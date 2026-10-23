"""Synthetic public lifecycle and closed-version coverage for v10 time headers."""
import datetime as dt
import hashlib
import json
from decimal import Decimal

import pytest

from app import case_packages, table_import
from app.services import imports
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_contract_lifecycle import facts, source_records
from test_table_mapping_coverage import coverage
from test_table_mapping_versions import profile_preview
from test_table_source_contracts import encoded_table


def v10_preview(client, cid, payload, monkeypatch, filename="synthetic.csv"):
    with monkeypatch.context() as context:
        context.setattr(imports, "TABLE_PROFILE", "funds-trace-table-v10")
        return preview(client, cid, payload, filename)


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("time_header,times", [
    ("交易时间（YYYYMMDDHHMMSS）", ("20260301102030", "20260301112131")),
    ("transaction date (YYYY-MM-DD HH:mm:ss)",
     ("2026-03-01 10:20:30", "2026-03-01 11:21:31")),
])
def test_annotated_time_completes_automatically_with_duplicate_and_source_package_round_trip(
        client, database, make_case, monkeypatch, suffix, time_header, times):
    cid = make_case("Synthetic declared time lifecycle")
    payload = encoded_table((time_header, "payer account", "payee account", "amount/分", "currency"), [
        [times[0], "SYN-A", "SYN-B", "123", "CNY"],
        [times[1], "SYN-C", "SYN-D", "234", "USD"],
    ], suffix)
    view = v10_preview(client, cid, payload, monkeypatch, "synthetic" + suffix)
    assert view["automatic"] and view["table_summary"]["profile"] == "funds-trace-table-v10"
    assert view["mapping"]["trade_time"] == time_header
    assert view["table_summary"]["accepted"] == 2
    assert view["table_summary"]["currency_totals"] == {"CNY": "1.23", "USD": "2.34"}
    job = complete(client, cid, view)
    assert job["stage"] == "done" and (job["result"]["ok"], job["result"]["rows_dup"]) == (2, 0)
    expected = facts(database, cid)
    assert [(row["trade_time"], row["payer_account"], row["payee_account"], row["amount"], row["currency"])
            for row in expected] == [
        (dt.datetime(2026, 3, 1, 10, 20, 30), "SYN-A", "SYN-B", Decimal("1.23"), "CNY"),
        (dt.datetime(2026, 3, 1, 11, 21, 31), "SYN-C", "SYN-D", Decimal("2.34"), "USD"),
    ]
    report = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()
    assert report["profile"] == "funds-trace-table-v10"
    assert report["sha256"] == hashlib.sha256(payload).hexdigest()
    assert report["source_available"] is True
    records = report["items"]
    assert [record["source_row"] for record in records] == [2, 3]
    assert all(record["disposition"] == "imported" and record["flow_id"] for record in records)
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == payload
    repeated = v10_preview(client, cid, payload, monkeypatch, "synthetic-repeat" + suffix)
    repeated_job = complete(client, cid, repeated)
    assert repeated_job["stage"] == "done"
    assert (repeated_job["result"]["ok"], repeated_job["result"]["rows_dup"]) == (0, 2)
    assert facts(database, cid) == expected
    assert coverage(client, cid)["financial_interpretation_complete"]
    assert [record["disposition"] for record in source_records(client, cid, repeated["import_id"])] == [
        "duplicate", "duplicate"]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == expected
    assert coverage(client, restored) == coverage(client, cid)
    restored_source = database.q_raw(
        "select source_id,parser_version,mapping_json from import_sources where case_id=? and filename=?",
        [restored, "synthetic" + suffix])[0]
    assert restored_source["parser_version"] == "funds-trace-table-v10"
    assert json.loads(restored_source["mapping_json"])["trade_time"] == time_header
    restored_records = source_records(client, restored, restored_source["source_id"])
    assert [{key: value for key, value in record.items() if key != "flow_id"} for record in restored_records] == [
        {key: value for key, value in record.items() if key != "flow_id"} for record in records]
    assert all(record["flow_id"] for record in restored_records)


def test_ready_v9_direction_session_keeps_its_version_after_v11_default(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic retained v9 ready session")
    payload = encoded_table(("date", "account", "counterparty account", "signed amount/分", "借贷标识"),
                            [["2026-03-01 10:20:30", "SYN-OWN", "SYN-OTHER", "-123", "DEBIT"]])
    old = profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v9")
    assert old["table_summary"]["accepted"] == 1
    assert imports.TABLE_PROFILE == table_import.PROFILE == "funds-trace-table-v16"
    response = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview", json={
        "mode": old["mode"], "mapping": old["mapping"]})
    assert response.status_code == 200, response.text
    assert response.json()["table_summary"]["profile"] == "funds-trace-table-v9"
    job = complete(client, cid, response.json())
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    expected = facts(database, cid)
    assert [(row["payer_account"], row["payee_account"], row["amount"]) for row in expected] == [
        ("SYN-OWN", "SYN-OTHER", Decimal("1.23"))]
    record = source_records(client, cid, old["import_id"])[0]
    repeated = complete(client, cid, preview(client, cid, payload))
    assert repeated["stage"] == "done" and (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 1)
    assert facts(database, cid) == expected
    assert source_records(client, cid, old["import_id"])[0] == record
    assert client.get(f"/api/v1/cases/{cid}/imports/{old['import_id']}").json()["result"] == job["result"]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == expected
    assert coverage(client, restored) == coverage(client, cid)
    assert {row["parser_version"] for row in database.q_raw(
        "select parser_version from import_sources where case_id=?", [restored])} == {
            "funds-trace-table-v9", "funds-trace-table-v16"}


@pytest.mark.parametrize("newest_first", [False, True])
def test_v10_time_conflict_remains_visible_after_v9_assessment_and_package(
        client, database, make_case, monkeypatch, newest_first):
    cid = make_case("Synthetic declared time conflict coverage")
    payload = encoded_table(("date", "交易时间（YYYYMMDDHHMMSS）", "payer account", "payee account", "amount"),
                            [["2026-03-01 10:20:30", "20260301102031", "SYN-A", "SYN-B", "1.23"]])
    for profile in (("funds-trace-table-v10", "funds-trace-table-v9") if newest_first
                    else ("funds-trace-table-v9", "funds-trace-table-v10")):
        view = profile_preview(client, cid, payload, monkeypatch, profile)
        job = complete(client, cid, view)
        assert job["stage"] == "done"
        if profile == "funds-trace-table-v10":
            assert job["result"]["ok"] == 0 and job["result"]["table_summary"]["unresolved"] == 1
            record = source_records(client, cid, view["import_id"])[0]
            assert "conflicting_time" in record["details"]["validation_issues"].split(",")
            assert record["details"]["validation_basis"] == table_import.FINANCIAL_CONFLICT_BASIS
        else:
            assert job["result"]["ok"] == 1
    assert len(facts(database, cid)) == 1
    assert coverage(client, cid)["unresolved_records"] == 1
    assert not coverage(client, cid)["financial_interpretation_complete"]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == facts(database, cid)
    assert coverage(client, restored) == coverage(client, cid)


@pytest.mark.parametrize("legacy", ["funds-trace-table-v7", "funds-trace-table-v8"])
@pytest.mark.parametrize("newest_first", [False, True])
def test_v10_direction_resolution_preserves_frozen_unit_record_and_receipt(
        client, database, make_case, monkeypatch, legacy, newest_first):
    cid = make_case("Synthetic bounded v10 direction resolution")
    payload = encoded_table(("date", "account", "counterparty account", "amount", "debit/credit indicator"),
                            [["2026-03-01 10:20:30", "SYN-OWN", "SYN-OTHER", "1.23", "CREDIT"]])
    old = profile_preview(client, cid, payload, monkeypatch, legacy)
    current = v10_preview(client, cid, payload, monkeypatch)
    for view in ((current, old) if newest_first else (old, current)):
        job = complete(client, cid, view)
        assert job["stage"] == "done"
        if view is old:
            receipt = job["result"]
    record = source_records(client, cid, old["import_id"])[0]
    assert record["reason_code"] == "unknown_unit"
    assert set(record["details"]["validation_issues"].split(",")) == {"unknown_unit", "missing_direction"}
    assert coverage(client, cid)["financial_interpretation_complete"]
    assert source_records(client, cid, old["import_id"])[0] == record
    assert client.get(f"/api/v1/cases/{cid}/imports/{old['import_id']}").json()["result"] == receipt
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == facts(database, cid)
    assert coverage(client, restored) == coverage(client, cid)


@pytest.mark.parametrize("fault", ["extra_issue", "future_profile"])
def test_v10_direction_resolution_does_not_extend_beyond_its_closed_proof(
        client, database, make_case, monkeypatch, fault):
    cid = make_case("Synthetic bounded direction proof rejection")
    payload = encoded_table(("date", "account", "counterparty account", "amount", "debit/credit indicator"),
                            [["2026-03-01 10:20:30", "SYN-OWN", "SYN-OTHER", "1.23", "CREDIT"]])
    old = profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v8")
    assert complete(client, cid, old)["stage"] == "done"
    current = v10_preview(client, cid, payload, monkeypatch)
    assert complete(client, cid, current)["stage"] == "done"
    assert coverage(client, cid)["unresolved_records"] == 0
    if fault == "extra_issue":
        record = source_records(client, cid, old["import_id"])[0]
        details = {**record["details"], "validation_issues": "unknown_unit,conflicting_amount"}
        database.x("update import_records set details_json=? where case_id=? and source_id=?",
                   [json.dumps(details), cid, old["import_id"]])
    else:
        database.x("update import_sources set parser_version='funds-trace-table-v17' where case_id=? and source_id=?",
                   [cid, current["import_id"]])
    assert coverage(client, cid)["unresolved_records"] == 1
    assert not coverage(client, cid)["financial_interpretation_complete"]
