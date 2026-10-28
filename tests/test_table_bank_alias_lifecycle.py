"""Public bank-name mapping, source evidence and frozen-history regressions."""
import io
import json
import zipfile
from decimal import Decimal

from fastapi import HTTPException
from openpyxl import Workbook
import pytest

from app import case_packages, table_import
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_feedback_package import _archive, _members, _mutate
from test_table_contract_lifecycle import facts, source_records
from test_table_mapping_coverage import coverage
from test_table_mapping_versions import profile_preview
from test_table_source_role_lifecycle import csv_bytes, transaction
from test_table_source_roles import TRANSACTION_EXPLICIT


def workbook(headers, rows):
    book = Workbook()
    sheet = book.active
    sheet.title = "Synthetic bank evidence"
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_bank_alias_automatic_commit_retains_full_source_mapping_and_facts(
        client, database, make_case, suffix):
    cid = make_case("Synthetic bank source evidence")
    headers = [*TRANSACTION_EXPLICIT, "payee_bank", "payer_bank"]
    rows = [transaction(**{"payee_bank": "Synthetic counter bank", "交易对方账号开户行": "Synthetic counter bank"}),
            transaction(**{"交易流水号": "SYN-T2", "借贷标志": "贷", "交易金额": "2.03",
                           "交易时间": "2026-02-01 11:00:00", "币种": "USD",
                           "payer_bank": "Synthetic USD bank", "交易对方账号开户行": "Synthetic USD bank"}),
            transaction(**{"交易流水号": "SYN-T3", "交易时间": "2026-02-01 12:00:00",
                           "payee_bank": "Synthetic conflicting bank", "交易对方账号开户行": "Synthetic source bank"})]
    # Extra party columns deliberately leave the closed reference template.
    # Give the two generic reference candidates equal values in this probe.
    for row in rows:
        row["凭证号"] = row["交易流水号"]
    data = csv_bytes(headers, rows) if suffix == ".csv" else workbook(headers, rows)
    view = preview(client, cid, data, "synthetic-bank" + suffix)
    assert view["automatic"] and view["table_summary"]["profile"] == "funds-trace-table-v17"
    assert view["mapping"]["counter_bank"] == "交易对方账号开户行"
    summary = view["table_summary"]
    assert (summary["accepted"], summary["unresolved"], summary["record_total"]) == (2, 1, 3)
    evidence = [item for item in summary["mapping_evidence"] if item["field"] == "counter_bank"]
    assert len(evidence) == 1 and evidence[0]["column"] == "交易对方账号开户行"
    assert evidence[0]["column_index"] == str(headers.index("交易对方账号开户行") + 1)
    assert evidence[0]["header_row"] == "1"
    assert summary["documents"][0]["mapping"]["counter_bank"] == "交易对方账号开户行"
    job = complete(client, cid, view)
    assert job["stage"] == "done" and (job["done"], job["total"], job["result"]["ok"]) == (3, 3, 2)
    before = facts(database, cid)
    assert [(row["payer_bank"], row["payee_bank"], row["amount"], row["currency"]) for row in before] == [
        ("", "Synthetic counter bank", Decimal("12.34"), "CNY"),
        ("Synthetic USD bank", "", Decimal("2.03"), "USD"),
    ]
    records = source_records(client, cid, view["import_id"])
    assert [row["disposition"] for row in records] == ["imported", "imported", "unresolved"]
    assert [row["source_row"] for row in records] == [2, 3, 4]
    assert records[-1]["reason_code"] == "conflicting_field" and records[-1]["flow_id"] is None
    assert records[-1]["details"]["financial_effect"] == "undetermined"
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == data
    other = make_case("Synthetic bank evidence isolated case")
    assert client.get(f"/api/v1/cases/{other}/sources/{view['import_id']}/file").status_code == 404
    repeated = complete(client, cid, preview(client, cid, data, "repeat" + suffix))
    assert repeated["stage"] == "done" and (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 2)
    assert facts(database, cid) == before and coverage(client, cid)["unresolved_records"] == 1
    assert client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json()["result"] == job["result"]
    package = case_packages.export_package(cid)[0]
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert json.loads(archive.read("meta.json"))["format"] == 6
    restored = case_packages.import_package(package)["case_id"]
    assert facts(database, restored) == before and coverage(client, restored) == coverage(client, cid)
    restored_source = database.q_raw(
        "select source_id,mapping_json from import_sources where case_id=? and filename=?",
        [restored, "synthetic-bank" + suffix])[0]
    assert json.loads(restored_source["mapping_json"])["counter_bank"] == "交易对方账号开户行"
    restored_records = source_records(client, restored, restored_source["source_id"])
    assert [{key: value for key, value in row.items() if key != "flow_id"} for row in restored_records] == [
        {key: value for key, value in row.items() if key != "flow_id"} for row in records]


@pytest.mark.parametrize("newest_first", [False, True])
def test_v12_ready_and_history_are_frozen_across_bank_alias_upgrade(
        client, database, make_case, monkeypatch, newest_first):
    cid = make_case("Synthetic bank profile history")
    data = csv_bytes(TRANSACTION_EXPLICIT, [transaction(**{"凭证号": "SYN-T1"})])
    old = profile_preview(client, cid, data, monkeypatch, "funds-trace-table-v12")
    current = profile_preview(client, cid, data, monkeypatch, "funds-trace-table-v13")
    assert old["table_summary"]["profile"] == "funds-trace-table-v12"
    assert "counter_bank" not in old["mapping"]
    assert current["mapping"]["counter_bank"] == "交易对方账号开户行"
    old_repreview = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview", json={
        "mode": old["mode"], "mapping": old["mapping"]})
    assert old_repreview.status_code == 200
    old = old_repreview.json()
    assert old["table_summary"]["profile"] == "funds-trace-table-v12"
    first, second = (current, old) if newest_first else (old, current)
    first_job = complete(client, cid, first)
    assert first_job["stage"] == "done" and first_job["result"]["ok"] == 1
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    original_source_tables = {
        table: database.q_raw(f"select * from {table} where case_id=? and source_id=? order by all",
                              [cid, first["import_id"]])
        for table in ("import_sources", "import_members", "import_records", "flow_sources")}
    before_report = source_records(client, cid, first["import_id"])
    second_job = complete(client, cid, second)
    assert second_job["stage"] == "done" and second_job["result"]["ok"] == 0
    assert second_job["result"]["table_summary"]["unresolved"] == 1
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before
    for table, rows in original_source_tables.items():
        assert database.q_raw(f"select * from {table} where case_id=? and source_id=? order by all",
                             [cid, first["import_id"]]) == rows
    assert source_records(client, cid, first["import_id"]) == before_report
    assert client.get(f"/api/v1/cases/{cid}/imports/{first['import_id']}").json()["result"] == first_job["result"]
    assert client.get(f"/api/v1/cases/{cid}/sources/{first['import_id']}/file").content == data
    assert source_records(client, cid, second["import_id"])[0]["details"]["counterparty_basis"] == table_import.HISTORY_CONFLICT_BASIS
    assert coverage(client, cid)["unresolved_records"] == 1
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == facts(database, cid)
    assert coverage(client, restored) == coverage(client, cid)


def test_v13_bank_conflict_cannot_be_hidden_by_later_v12_assessment(client, make_case, monkeypatch):
    cid = make_case("Synthetic bank conflict coverage")
    data = csv_bytes([*TRANSACTION_EXPLICIT, "payee_bank"], [transaction(**{
        "交易对方账号开户行": "Synthetic source bank", "payee_bank": "Synthetic conflicting bank",
        "凭证号": "SYN-T1"})])
    current = profile_preview(client, cid, data, monkeypatch, "funds-trace-table-v13")
    assert complete(client, cid, current)["result"]["ok"] == 0
    old = profile_preview(client, cid, data, monkeypatch, "funds-trace-table-v12")
    assert complete(client, cid, old)["result"]["ok"] == 1
    assert coverage(client, cid)["unresolved_records"] == 1


def test_v13_known_reference_metadata_and_unknown_future_package_are_distinguished(
        client, database, make_case, tmp_path, monkeypatch):
    cid = make_case("Synthetic bank package version")
    data = workbook(TRANSACTION_EXPLICIT, [transaction()])
    from app.services import imports
    with monkeypatch.context() as context:
        context.setattr(imports, "TABLE_PROFILE", "funds-trace-table-v13")
        view = preview(client, cid, data, "synthetic.xlsx")
    assert complete(client, cid, view)["result"]["ok"] == 1
    package = case_packages.export_package(cid)[0]
    restored = case_packages.import_package(package)["case_id"]
    assert facts(database, restored) == facts(database, cid)

    members = _members(package)
    _mutate(members, "import_sources", "update entries set parser_version='funds-trace-table-v18'", tmp_path)
    # An unknown future rule must remain a package rejection, not downgrade.
    with pytest.raises(HTTPException):
        case_packages.import_package(_archive(members))


@pytest.mark.parametrize("kind", ["direction", "unit"])
@pytest.mark.parametrize("fault", [None, "other_bank_label", "extra_issue"])
def test_bank_column_addition_preserves_only_the_existing_closed_header_resolution(
        client, database, make_case, monkeypatch, kind, fault):
    cid = make_case("Synthetic bank plus historical header correction")
    if kind == "direction":
        profile = "funds-trace-table-v8"
        headers = ["date", "account", "counterparty account", "amount", "debit/credit indicator",
                   "交易对方账号开户行"]
        values = ["2026-01-01", "SYN-OWN", "SYN-OTHER", "1.23", "CREDIT", "Synthetic bank"]
    else:
        profile = "funds-trace-table-v10"
        headers = ["transaction timestamp", "account", "counterparty account", "transaction amount(unit: cents)", "direction",
                   "交易对方账号开户行"]
        values = ["2026-01-01", "SYN-OWN", "SYN-OTHER", "123", "out", "Synthetic bank"]
    data = csv_bytes(headers, [dict(zip(headers, values))])
    old = profile_preview(client, cid, data, monkeypatch, profile)
    old_job = complete(client, cid, old)
    assert old_job["stage"] == "done" and old_job["result"]["ok"] == 0
    old_record = source_records(client, cid, old["import_id"])[0]
    assert old_record["reason_code"] == "unknown_unit"
    current = preview(client, cid, data)
    assert complete(client, cid, current)["result"]["ok"] == 1
    assert facts(database, cid)[0]["amount"] == Decimal("1.23")
    if fault == "other_bank_label":
        changed = {**current["mapping"], "counter_bank": "Synthetic unverified bank column"}
        database.x("update import_sources set mapping_json=? where case_id=? and source_id=?",
                   [json.dumps(changed), cid, current["import_id"]])
    elif fault == "extra_issue":
        details = {**old_record["details"], "validation_issues": "unknown_unit,conflicting_amount"}
        database.x("update import_records set details_json=? where case_id=? and source_id=?",
                   [json.dumps(details), cid, old["import_id"]])
    assert coverage(client, cid)["unresolved_records"] == (0 if fault is None else 1)
    assert client.get(f"/api/v1/cases/{cid}/imports/{old['import_id']}").json()["result"] == old_job["result"]
    if fault is None:
        assert source_records(client, cid, old["import_id"])[0] == old_record
        restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
        assert facts(database, restored) == facts(database, cid)
        assert coverage(client, restored) == coverage(client, cid)
