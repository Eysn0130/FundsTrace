"""Synthetic public imports retain source roles, exact facts and old receipts."""
import csv
import datetime as dt
from decimal import Decimal
import io
import json
import zipfile

from openpyxl import Workbook
import pytest

from app import case_packages, table_import
from app.services import imports
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_contract_lifecycle import facts, source_records
from test_table_mapping_coverage import coverage
from test_table_mapping_versions import profile_preview
from test_table_source_roles import ACCOUNT, CLIENT, SUBACCOUNT, TRANSACTION_EXPLICIT, TRANSACTION_QUERY_ONLY


@pytest.fixture(autouse=True)
def frozen_v12_source_role_profile(monkeypatch):
    """These historical v12 lifecycle contracts stay pinned after v13."""
    monkeypatch.setattr(imports, "TABLE_PROFILE", table_import.SOURCE_ROLE_PROFILE)


def transaction(**changes):
    row = {
        "本方卡号": "SYN-CARD", "本方账号": "SYN-OWN", "姓名(查询条件)": "Synthetic query name",
        "证件号码(查询条件)": "SYN-QUERY-ID", "查询帐号": "SYN-QUERY-ACCOUNT",
        "查询卡号": "SYN-QUERY-CARD", "交易时间": "2026-02-01 10:00:00", "交易金额": "12.34",
        "借贷标志": "借", "交易余额": "100.00", "交易对方名称": "Synthetic counterparty",
        "交易对方账号": "SYN-OTHER", "交易对方账号开户行": "Synthetic bank",
        "交易摘要": "Synthetic transfer", "交易流水号": "SYN-T1", "凭证号": "SYN-V1",
        "交易是否成功": "成功", "币种": "CNY",
    }
    row.update(changes)
    return row


def csv_bytes(headers, rows):
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    writer.writerow(headers)
    writer.writerows([[row.get(header, "") for header in headers] for row in rows])
    return text.getvalue().encode()


def xlsx_bytes(sheets):
    workbook = Workbook()
    for index, (name, headers, rows) in enumerate(sheets):
        sheet = workbook.active if index == 0 else workbook.create_sheet()
        sheet.title = name
        sheet.append(headers)
        for row in rows:
            sheet.append([row.get(header, "") for header in headers])
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


@pytest.mark.parametrize("suffix", ["csv", "xlsx"])
def test_explicit_source_roles_automatically_import_exact_money_and_validate_last_row(
        client, database, make_case, suffix):
    cid = make_case("Synthetic explicit source role lifecycle")
    headers = [*TRANSACTION_EXPLICIT, "", "", ""]
    rows = [transaction(), transaction(**{
        "本方账号": "", "本方卡号": "SYN-FALLBACK", "交易时间": "2026-02-01 11:00:00",
        "交易金额": "2.03", "借贷标志": "贷", "交易余额": "50.00", "币种": "USD",
        "交易对方账号": "SYN-OTHER2", "交易流水号": "", "凭证号": "SYN-V2",
    }), transaction(**{
        "交易时间": "2026-02-01 12:00:00", "交易金额": "invalid-money",
        "交易流水号": "SYN-T3", "凭证号": "SYN-V3",
    })]
    data = (csv_bytes(headers, rows) if suffix == "csv"
            else xlsx_bytes([("Synthetic transactions", headers, rows)]))
    view = preview(client, cid, data, f"synthetic.{suffix}")
    assert view["automatic"] is True
    assert view["table_summary"]["profile"] == "funds-trace-table-v12"
    summary = view["table_summary"]
    assert (summary["record_total"], summary["accepted"], summary["unresolved"]) == (3, 2, 1)
    assert summary["currency_totals"] == {"CNY": "12.34", "USD": "2.03"}
    job = complete(client, cid, view)
    assert job["stage"] == "done" and (job["result"]["ok"], job["done"], job["total"]) == (2, 3, 3)
    before = facts(database, cid)
    assert [(row["trade_no"], row["payer_account"], row["payee_account"], row["amount"],
             row["balance"], row["currency"]) for row in before] == [
        ("SYN-T1", "SYN-OWN", "SYN-OTHER", Decimal("12.34"), Decimal("100.00"), "CNY"),
        ("SYN-V2", "SYN-OTHER2", "SYN-FALLBACK", Decimal("2.03"), Decimal("50.00"), "USD"),
    ]
    assert before[0]["payer_name"] == before[1]["payee_name"] == ""
    records = source_records(client, cid, view["import_id"])
    assert [record["source_row"] for record in records] == [2, 3, 4]
    assert [record["disposition"] for record in records] == ["imported", "imported", "unresolved"]
    assert records[0]["details"]["trade_no_basis"] == "source_transaction_id"
    assert records[1]["details"]["trade_no_basis"] == "legacy_voucher_fallback"
    assert records[1]["details"]["source_account_column"] == "本方卡号"
    assert "invalid_amount" in records[-1]["details"]["validation_issues"].split(",")
    assert records[-1]["flow_id"] is None
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == data
    repeated = complete(client, cid, preview(client, cid, data, f"renamed.{suffix}"))
    assert repeated["stage"] == "done"
    assert (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 2)
    assert facts(database, cid) == before
    assert coverage(client, cid)["unresolved_records"] == 1
    package = case_packages.export_package(cid)[0]
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert json.loads(archive.read("meta.json"))["format"] == 4
    restored = case_packages.import_package(package)["case_id"]
    assert facts(database, restored) == before
    assert coverage(client, restored) == coverage(client, cid)
    assert client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json()["result"] == job["result"]


def test_ready_v11_source_role_session_stays_frozen_after_v12_default(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic frozen v11 source roles")
    data = csv_bytes(TRANSACTION_EXPLICIT, [transaction(**{
        "本方卡号": "SYN-OWN", "凭证号": "SYN-T1",
    })])
    old = profile_preview(client, cid, data, monkeypatch, "funds-trace-table-v11")
    assert old["table_summary"]["accepted"] == 1
    response = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview", json={
        "mode": old["mode"], "mapping": old["mapping"],
    })
    assert response.status_code == 200, response.text
    frozen = response.json()
    assert frozen["table_summary"]["profile"] == "funds-trace-table-v11"
    first = complete(client, cid, frozen)
    assert first["stage"] == "done" and first["result"]["ok"] == 1
    before = facts(database, cid)
    current = complete(client, cid, preview(client, cid, data))
    assert current["stage"] == "done"
    assert (current["result"]["ok"], current["result"]["rows_dup"]) == (0, 1)
    assert facts(database, cid) == before
    assert client.get(f"/api/v1/cases/{cid}/imports/{old['import_id']}").json()["result"] == first["result"]
    assert coverage(client, cid)["unresolved_records"] == 0
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == before


@pytest.mark.parametrize("newest_first", [False, True])
def test_both_ready_source_role_versions_cannot_duplicate_a_physical_payment(
        client, database, make_case, monkeypatch, newest_first):
    cid = make_case("Synthetic source role history binding")
    data = csv_bytes(TRANSACTION_EXPLICIT, [transaction()])
    old = profile_preview(client, cid, data, monkeypatch, "funds-trace-table-v11")
    # Reproduce an already-supported historical advanced mapping. The new
    # automatic path below does not ask a user to make this choice.
    response = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview", json={
        "mode": "B", "mapping": {**old["mapping"], "self_account": "本方卡号", "trade_no": "交易流水号"},
    })
    assert response.status_code == 200, response.text
    old = response.json()
    current = preview(client, cid, data)
    assert old["table_summary"]["accepted"] == current["table_summary"]["accepted"] == 1
    first, second = (current, old) if newest_first else (old, current)
    first_job = complete(client, cid, first)
    assert first_job["stage"] == "done" and first_job["result"]["ok"] == 1
    before = facts(database, cid)
    assert before[0]["payer_account"] == ("SYN-OWN" if newest_first else "SYN-CARD")
    second_job = complete(client, cid, second)
    assert second_job["stage"] == "done" and second_job["result"]["ok"] == 0
    assert second_job["result"]["table_summary"]["unresolved"] == 1
    assert facts(database, cid) == before
    assert client.get(f"/api/v1/cases/{cid}/imports/{first['import_id']}").json()["result"] == first_job["result"]
    record = source_records(client, cid, second["import_id"])[0]
    assert record["disposition"] == "unresolved" and record["flow_id"] is None
    assert record["details"]["counterparty_basis"] == table_import.HISTORY_CONFLICT_BASIS
    assert database.q_raw("select count(*) n,sum(amount) amount from effective_flows where case_id=?", [cid]) == [
        {"n": 1, "amount": Decimal("12.34")},
    ]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == before
    assert coverage(client, restored) == coverage(client, cid)


def test_mixed_workbook_classifies_auxiliary_sources_without_inventing_a_query_owner(
        client, database, make_case):
    cid = make_case("Synthetic source role workbook")
    data = xlsx_bytes([
        ("Synthetic account", ACCOUNT, [{"账号": "SYN-AUX", "卡号": "SYN-AUX-CARD", "账户余额": "900.00"}]),
        ("Synthetic client", CLIENT, [{"客户名称": "Synthetic client", "证件号码": "SYN-CLIENT-ID"}]),
        ("Synthetic subaccount", SUBACCOUNT, [{"账卡号": "SYN-AUX", "子账户账号": "SYN-SUB", "账户余额": "800.00"}]),
        ("Synthetic query", TRANSACTION_QUERY_ONLY, [transaction(**{"交易对方账卡号": "SYN-OTHER"})]),
        ("Synthetic returned transaction", TRANSACTION_EXPLICIT, [transaction()]),
    ])
    view = preview(client, cid, data, "synthetic.xlsx")
    summary = view["table_summary"]
    assert view["automatic"] is True
    assert (summary["record_total"], summary["accepted"], summary["unresolved"], summary["auxiliary"]) == (5, 1, 1, 3)
    assert summary["currency_totals"] == {"CNY": "12.34"}
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    records = source_records(client, cid, view["import_id"])
    assert [record["disposition"] for record in records] == ["auxiliary", "auxiliary", "auxiliary", "unresolved", "imported"]
    assert [record["details"]["source_role"] for record in records[:3]] == ["account", "client", "subaccount"]
    assert all(record["reason_code"] == "auxiliary_table" and record["flow_id"] is None for record in records[:3])
    query = records[3]
    assert query["reason_code"] == "missing_identity" and query["flow_id"] is None
    assert "missing_identity" in query["details"]["validation_issues"].split(",")
    before = facts(database, cid)
    assert len(before) == 1 and before[0]["payer_account"] == "SYN-OWN" and before[0]["payer_name"] == ""
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == data
    repeated = complete(client, cid, preview(client, cid, data, "repeat.xlsx"))
    assert repeated["stage"] == "done"
    assert (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 1)
    assert facts(database, cid) == before
    assert coverage(client, cid)["unresolved_records"] == 1
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == before
    assert coverage(client, restored) == coverage(client, cid)


def test_default_v12_retains_unit_time_sign_and_same_entry_balance_contracts(
        client, database, make_case):
    cid = make_case("Synthetic inherited exact financial contracts")
    signed_headers = ["交易时间(YYYYMMDDHHMMSS)", "account", "counterparty account",
                      "signed amount(unit: cents)", "CR/DR", "currency"]
    balance_headers = ["date", "account", "counterparty account", "amount(unit: cent)",
                       "交易前账面余额[unit: cent]", "交易后账面余额(unit: 元)", "currency"]
    data = xlsx_bytes([
        ("Synthetic signed entries", signed_headers, [
            dict(zip(signed_headers, ["20260201100000", "SYN-OWN", "SYN-OTHER", "-123", "DEBIT", "CNY"])),
            dict(zip(signed_headers, ["20260201110000", "SYN-OWN", "SYN-USD", "234", "CREDIT", "USD"])),
        ]),
        ("Synthetic same entry balances", balance_headers, [
            dict(zip(balance_headers, ["2026-02-02", "SYN-OWN", "SYN-OTHER", "1234", "10000", "87.66", "CNY"])),
            dict(zip(balance_headers, ["2026-02-03", "SYN-OWN", "SYN-OTHER", "1234", "10000", "88.66", "CNY"])),
        ]),
    ])
    view = preview(client, cid, data, "synthetic.xlsx")
    assert view["automatic"] and view["table_summary"]["profile"] == "funds-trace-table-v12"
    assert (view["table_summary"]["accepted"], view["table_summary"]["unresolved"]) == (3, 1)
    assert view["table_summary"]["currency_totals"] == {"CNY": "13.57", "USD": "2.34"}
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 3
    actual = facts(database, cid)
    assert [(row["trade_time"], row["payer_account"], row["payee_account"], row["amount"], row["currency"])
            for row in actual] == [
        (dt.datetime(2026, 2, 1, 10), "SYN-OWN", "SYN-OTHER", Decimal("1.23"), "CNY"),
        (dt.datetime(2026, 2, 1, 11), "SYN-USD", "SYN-OWN", Decimal("2.34"), "USD"),
        (dt.datetime(2026, 2, 2), "SYN-OWN", "SYN-OTHER", Decimal("12.34"), "CNY"),
    ]
    records = source_records(client, cid, view["import_id"])
    assert records[2]["details"]["direction_basis"] == "same_entry_book_balances"
    assert records[2]["details"]["balance_basis"] == "same_entry_exact"
    assert records[-1]["reason_code"] == "balance_amount_conflict" and records[-1]["flow_id"] is None
