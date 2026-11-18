"""Public reports retain original endpoint text and admitted snapshot priority."""
import csv
import io
import json
import zipfile

import pytest

from app import evidence_resolution, source_ledger, table_archive
from test_folder_evidence import accepted
from test_import_report import complete


HEADERS = ["流水号", "交易时间", "付款账号", "付款户名", "收款账号", "收款户名", "金额", "币种"]
ROWS = [
    ["SYN-A1", "2026-11-13 09:00:00", "000SYN-P1", "合成同名甲", "000SYN-Q1", "合成乙", "37.41", "CNY"],
    ["SYN-A2", "2026-11-13 09:01:00", "000SYN-P2", "合成同名甲", "000SYN-Q1", "合成乙", "0.01", "CNY"],
    ["SYN-A3", "2026-11-13 09:02:00", "000SYN-P3", "合成丙", "000SYN-Q2", "合成丁", "2.03", "USD"],
]


def encoded(rows, headers=HEADERS):
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows([headers, *rows])
    return stream.getvalue().encode("utf-8-sig")


def imported(client, database, make_case, rows=ROWS):
    cid = make_case("Synthetic raw endpoint report")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as target:
        target.writestr("endpoints.csv", encoded(rows))
    base, view = complete(client, cid, stream.getvalue(), "synthetic.zip")
    assert view["table_summary"]["profile"] == table_archive.PROFILE
    assert view["table_summary"]["profile"] not in table_archive.EVIDENCE_PROFILES
    notes = database.q_raw("select details_json from import_records where case_id=? and source_id=? order by record_no",
                           [cid, view["import_id"]])
    assert len(notes) == len(rows)
    assert all(not ({source_ledger.FIELD, evidence_resolution.DETAIL_FIELD} & json.loads(row["details_json"]).keys())
               for row in notes)
    return cid, base, view


def directory(client, cid, rows, headers=HEADERS):
    # The public directory endpoint opts into typed evidence; no stored rows or
    # projections are fabricated to make the reader fixture pass admission.
    view, _ = accepted(client, cid, [("endpoints.csv", encoded(rows, headers))], scope_bank=None)
    assert view["table_summary"]["profile"] in table_archive.EVIDENCE_PROFILES
    return f'/api/v1/cases/{cid}/imports/{view["import_id"]}', view


def report(client, base, currency="CNY"):
    response = client.get(base + "/report", params={"member_path": "endpoints.csv", "currency": currency})
    assert response.status_code == 200, response.text
    return response.json()


def test_plain_a_archive_counts_original_names_and_numbers_without_changing_money(client, database, make_case):
    _, base, _ = imported(client, database, make_case)
    data = report(client, base)
    assert data["perspective"] == "transaction_endpoints"
    assert {key: value for key, value in data["overview"].items() if key != "selected_account"} == {
        "name_count": 2, "account_count": 3, "number_count": 3, "unnamed_account_count": 0}
    accounts = {row["account_number"]: row for row in data["accounts"]["items"]}
    assert set(accounts) == {"000SYN-P1", "000SYN-P2", "000SYN-Q1"}
    assert accounts["000SYN-P1"]["names"] == accounts["000SYN-P2"]["names"] == ["合成同名甲"]
    assert accounts["000SYN-Q1"]["names"] == ["合成乙"]
    assert data["summary"]["transaction_count"] == 2
    assert data["summary"]["amount"] == data["summary"]["in_amount"] == data["summary"]["out_amount"] == "37.42"
    assert data["currency"] == "CNY" and data["currencies"] == ["CNY", "USD"]
    usd = report(client, base, "USD")
    assert usd["overview"]["name_count"] == usd["overview"]["number_count"] == 2
    assert usd["summary"]["amount"] == usd["summary"]["in_amount"] == usd["summary"]["out_amount"] == "2.03"
    response = client.get(base + "/report/accounts", params={"member_path": "endpoints.csv", "currency": "CNY",
        "expected_revision": data["analysis_revision"], "search": "合成同名甲"})
    assert response.status_code == 200, response.text
    assert {row["account_number"] for row in response.json()["items"]} == {"000SYN-P1", "000SYN-P2"}


def test_missing_source_snapshots_do_not_erase_original_record_preview(client, database, make_case):
    _, base, _ = imported(client, database, make_case)
    data = report(client, base)
    response = client.get(base + "/report/records", params={"member_path": "endpoints.csv", "currency": "CNY",
        "expected_revision": data["analysis_revision"]})
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 2
    for item, original in zip(items, ROWS[:2]):
        preview = item["preview"]
        assert (preview["payer_account"], preview["payer_name"], preview["payee_account"], preview["payee_name"]) == tuple(original[2:6])
        assert preview["amount"] == original[6] and preview["currency"] == original[7]
        assert preview["self_account"] == ""


def test_plain_csv_duplicate_does_not_borrow_another_sources_self_claim(client, database, make_case):
    cid = make_case("Synthetic ordinary self source boundary")
    headers = [*HEADERS, "本方户名"]
    first = "Synthetic first file owner"
    _, _ = complete(client, cid, encoded([[*ROWS[0], first]], headers), "first.csv")
    base, _ = complete(client, cid, encoded([[*ROWS[0], "Synthetic second file owner"]], headers), "second.csv")
    flows = database.q_raw("select self_account,self_name from flows where case_id=?", [cid])
    notes = database.q_raw("select disposition,details_json from import_records where case_id=?", [cid])
    assert flows == [{"self_account": "", "self_name": first}]
    assert sorted(row["disposition"] for row in notes) == ["duplicate", "imported"]
    assert all(not ({source_ledger.FIELD, evidence_resolution.DETAIL_FIELD} & json.loads(row["details_json"]).keys())
               for row in notes)
    response = client.get(base + "/report", params={"member_path": "table.csv"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["summary"]["transaction_count"] == 1 and data["summary"]["amount"] == "37.41"
    assert data["currency"] == "CNY" and data["perspective"] == "transaction_endpoints"
    assert data["overview"]["name_count"] == data["overview"]["number_count"] == 2
    response = client.get(base + "/report/records", params={"member_path": "table.csv",
        "expected_revision": data["analysis_revision"]})
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    preview = response.json()["items"][0]["preview"]
    assert (preview["payer_account"], preview["payer_name"], preview["payee_account"], preview["payee_name"]) == tuple(ROWS[0][2:6])
    assert preview["self_account"] == preview["self_name"] == ""


def test_raw_account_label_does_not_use_a_generated_entity_id(client, database, make_case):
    cid = make_case("Synthetic projected endpoint labels")
    base, _ = directory(client, cid, ROWS[:1])
    data = report(client, base)
    accounts = data["accounts"]["items"]
    assert len(accounts) == 2 and all(row["account"].startswith("fte_") for row in accounts)
    assert {row["account_number"] for row in accounts} == {"000SYN-P1", "000SYN-Q1"}
    assert all(row["account_numbers"] == [row["account_number"]] for row in accounts)
    assert {name for row in accounts for name in row["names"]} == {"合成同名甲", "合成乙"}
    assert data["overview"]["name_count"] == data["overview"]["number_count"] == 2
    assert data["summary"]["amount"] == "37.41"
    raw = database.q_raw("select payer_account,payee_account from flows where case_id=?", [cid])
    assert raw == [{"payer_account": "000SYN-P1", "payee_account": "000SYN-Q1"}]


@pytest.mark.parametrize("kind", ["typed-empty", "typed-present", "ledger-empty"])
def test_publicly_admitted_typed_or_ledger_values_keep_priority(client, database, make_case, kind):
    cid = make_case("Synthetic admitted endpoint snapshots")
    if kind == "ledger-empty":
        headers = ["流水号", "交易时间", "本方账号", "本方户名", "对方账号", "对方户名", "收付标志", "金额", "币种"]
        rows = [["SYN-L1", "2026-11-13 09:00:00", "000SYN-Q1", "合成乙", "", "", "入", "37.41", "CNY"]]
    else:
        headers = [*HEADERS[:3], "付款卡号", *HEADERS[3:5], "收款卡号", *HEADERS[5:]]
        names = ("", "") if kind == "typed-empty" else ("合成甲", "合成乙")
        rows = [["SYN-T1", "2026-11-13 09:00:00", "000SYN-P1", "000SYN-CARD-P", names[0],
                 "000SYN-Q1", "000SYN-CARD-Q", names[1], "37.41", "CNY"]]
    base, view = directory(client, cid, rows, headers)
    raw_details = database.q_raw("select details_json from import_records where case_id=? and source_id=?",
                                 [cid, view["import_id"]])
    details = json.loads(raw_details[0]["details_json"])
    typed = evidence_resolution.validate_evidence(details[evidence_resolution.DETAIL_FIELD])
    data = report(client, base)
    assert data["summary"]["amount"] == "37.41" and data["currency"] == "CNY"
    records = client.get(base + "/report/records", params={"member_path": "endpoints.csv",
        "expected_revision": data["analysis_revision"]})
    assert records.status_code == 200, records.text
    preview = records.json()["items"][0]["preview"]
    if kind == "typed-empty":
        assert typed["endpoints"]["payer"]["name"] == typed["endpoints"]["payee"]["name"] == ""
        assert data["overview"]["name_count"] == 0 and data["overview"]["number_count"] == 2
        assert all(row["names"] == [] for row in data["accounts"]["items"])
        assert preview["payer_name"] == preview["payee_name"] == ""
    elif kind == "typed-present":
        assert typed["endpoints"]["payer"]["account"] == "000SYN-P1" and typed["endpoints"]["payer"]["card"] == "000SYN-CARD-P"
        assert {row["account_number"] for row in data["accounts"]["items"]} == {"000SYN-P1", "000SYN-Q1"}
        assert preview["payer_account"] == "000SYN-P1" and preview["payer_name"] == "合成甲"
        assert preview["payee_account"] == "000SYN-Q1" and preview["payee_name"] == "合成乙"
    else:
        ledger = source_ledger.validate(details[source_ledger.FIELD])
        assert ledger["row"]["payer_account"] == ledger["row"]["payer_name"] == ""
        assert data["perspective"] == "own_ledger"
        assert data["top_in"] == []
        unresolved, = data["relations"]["items"]
        assert unresolved["counterparty"] == unresolved["counterparty_number"] == "" and unresolved["names"] == []
        assert unresolved["in_amount"] == "37.41"
        assert preview["payer_name"] == preview["payer_account"] == ""
        assert preview["payee_account"] == "000SYN-Q1" and preview["payee_name"] == "合成乙"
