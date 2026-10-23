"""Public synthetic lifecycle and bounded coverage for v11 unit declarations."""
import datetime as dt
import hashlib
import io
import json
from decimal import Decimal
import zipfile

import pytest

from app import case_packages, table_import
from app.services import imports
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_contract_lifecycle import facts, source_records
from test_table_mapping_coverage import coverage, envelope
from test_table_mapping_versions import profile_preview
from test_table_source_contracts import encoded_table


@pytest.fixture(autouse=True)
def retained_v11_default(monkeypatch):
    # These tests certify the frozen v11 contract independently of new defaults.
    monkeypatch.setattr(imports, "TABLE_PROFILE", table_import.UNIT_DECLARATION_PROFILE)


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("amount_header,values,currencies", [
    ("transaction amount (unit: cents)", ("123", "234"), ("CNY", "USD")),
    ("amount/unit: cents", ("123", "234"), ("CNY", "USD")),
    ("amount[unit: CNY]", ("1.23", "2.34"), ("CNY", "CNY")),
])
def test_declared_units_and_time_import_automatically_and_preserve_source_and_format4(
        client, database, make_case, suffix, amount_header, values, currencies):
    cid = make_case("Synthetic v11 time and unit lifecycle")
    time_header = "交易时间（YYYYMMDDHHMMSS）"
    payload = encoded_table((time_header, "payer account", "payee account", amount_header, "currency"), [
        ["20260301102030", "SYN-A", "SYN-B", values[0], currencies[0]],
        ["20260301112131", "SYN-C", "SYN-D", values[1], currencies[1]],
    ], suffix)
    view = preview(client, cid, payload, "synthetic" + suffix)
    assert view["automatic"] and view["table_summary"]["profile"] == "funds-trace-table-v11"
    assert view["mapping"]["trade_time"] == time_header
    assert view["mapping"]["amount"] == amount_header
    assert (view["table_summary"]["accepted"], view["table_summary"]["unresolved"]) == (2, 0)
    assert view["table_summary"]["currency_totals"] == (
        {"CNY": "3.57"} if currencies[1] == "CNY" else {"CNY": "1.23", "USD": "2.34"})
    # Commit the automatic preview as returned; no mapping edit or repreview.
    job = complete(client, cid, view)
    assert job["stage"] == "done" and (job["result"]["ok"], job["result"]["rows_dup"]) == (2, 0)
    expected = facts(database, cid)
    assert [(row["trade_time"], row["payer_account"], row["payee_account"], row["amount"], row["currency"])
            for row in expected] == [
        (dt.datetime(2026, 3, 1, 10, 20, 30), "SYN-A", "SYN-B", Decimal("1.23"), currencies[0]),
        (dt.datetime(2026, 3, 1, 11, 21, 31), "SYN-C", "SYN-D", Decimal("2.34"), currencies[1]),
    ]
    report = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()
    assert report["profile"] == "funds-trace-table-v11"
    assert report["sha256"] == hashlib.sha256(payload).hexdigest()
    assert report["source_available"] is True
    records = report["items"]
    assert [record["source_row"] for record in records] == [2, 3]
    assert all(record["disposition"] == "imported" and record["flow_id"] for record in records)
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == payload
    repeated = preview(client, cid, payload, "synthetic-repeat" + suffix)
    repeated_job = complete(client, cid, repeated)
    assert repeated_job["stage"] == "done"
    assert (repeated_job["result"]["ok"], repeated_job["result"]["rows_dup"]) == (0, 2)
    assert [record["disposition"] for record in source_records(client, cid, repeated["import_id"])] == [
        "duplicate", "duplicate"]
    assert facts(database, cid) == expected
    assert coverage(client, cid)["financial_interpretation_complete"]
    package = case_packages.export_package(cid)[0]
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert json.loads(archive.read("meta.json"))["format"] == 4
    restored = case_packages.import_package(package)["case_id"]
    assert facts(database, restored) == expected
    assert coverage(client, restored) == coverage(client, cid)
    restored_source = database.q_raw(
        "select source_id,parser_version,mapping_json from import_sources where case_id=? and filename=?",
        [restored, "synthetic" + suffix])[0]
    assert restored_source["parser_version"] == "funds-trace-table-v11"
    assert json.loads(restored_source["mapping_json"]) == view["mapping"]
    restored_records = source_records(client, restored, restored_source["source_id"])
    assert [{key: value for key, value in record.items() if key != "flow_id"} for record in restored_records] == [
        {key: value for key, value in record.items() if key != "flow_id"} for record in records]
    assert all(record["flow_id"] for record in restored_records)


def test_ready_v10_session_remains_v10_when_v11_rejects_unpaired_unit_brackets(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic retained v10 unit grammar")
    payload = encoded_table(("date", "payer account", "payee account", "amount(分]"),
                            [["2026-03-01 10:20:30", "SYN-A", "SYN-B", "123"]])
    old = profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v10")
    assert old["table_summary"]["accepted"] == 1
    assert imports.TABLE_PROFILE == table_import.UNIT_DECLARATION_PROFILE == "funds-trace-table-v11"
    response = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview", json={
        "mode": old["mode"], "mapping": old["mapping"]})
    assert response.status_code == 200, response.text
    frozen = response.json()
    assert frozen["table_summary"]["profile"] == "funds-trace-table-v10"
    assert frozen["table_summary"]["currency_totals"] == {"CNY": "1.23"}
    job = complete(client, cid, frozen)
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    old_record = source_records(client, cid, old["import_id"])[0]
    current = preview(client, cid, payload)
    assert current["table_summary"]["reason_counts"] == {"unknown_unit": 1}
    assert complete(client, cid, current)["stage"] == "done"
    assert [(row["amount"], row["payer_account"], row["payee_account"]) for row in facts(database, cid)] == [
        (Decimal("1.23"), "SYN-A", "SYN-B")]
    assert source_records(client, cid, old["import_id"])[0] == old_record
    assert client.get(f"/api/v1/cases/{cid}/imports/{old['import_id']}").json()["result"] == job["result"]
    assert coverage(client, cid)["unresolved_records"] == 1
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == facts(database, cid)
    assert coverage(client, restored) == coverage(client, cid)


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("conflict,issue", [
    ("last_row", "amount_precision"),
    ("unit_column", "conflicting_unit"),
    ("currency", "currency_unit_conflict"),
])
def test_v11_full_file_unit_failures_keep_source_reason_and_coverage_after_format4(
        client, database, make_case, suffix, conflict, issue):
    cid = make_case("Synthetic v11 complete unit validation")
    amount_header = "amount[unit: CNY]" if conflict == "currency" else "amount(unit: cents)"
    extra = "currency" if conflict == "currency" else "amount unit"
    valid_value, valid_extra = ("1.23", "CNY") if conflict == "currency" else ("123", "cents")
    rows = [[(dt.datetime(2026, 3, 1, 10) + dt.timedelta(seconds=index)).isoformat(" "),
             "SYN-A", f"SYN-B-{index}", valid_value, valid_extra] for index in range(105)]
    invalid_value = "1.1" if conflict == "last_row" else valid_value
    invalid_extra = {"last_row": "cents", "unit_column": "yuan", "currency": "USD"}[conflict]
    rows.append(["2026-03-01 11:00:00", "SYN-A", "SYN-LAST", invalid_value, invalid_extra])
    payload = encoded_table(("date", "payer account", "payee account", amount_header, extra), rows, suffix)
    view = preview(client, cid, payload, "synthetic" + suffix)
    assert view["automatic"] and view["table_summary"]["profile"] == "funds-trace-table-v11"
    assert (view["table_summary"]["accepted"], view["table_summary"]["unresolved"]) == (105, 1)
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 105
    record = source_records(client, cid, view["import_id"])[-1]
    assert record["source_row"] == 107 and record["disposition"] == "unresolved" and record["flow_id"] is None
    assert issue in record["details"]["validation_issues"].split(",")
    assert record["details"]["validation_basis"] == table_import.FINANCIAL_CONFLICT_BASIS
    assert record["details"]["reason"]
    assert coverage(client, cid)["unresolved_records"] == 1
    assert not coverage(client, cid)["financial_interpretation_complete"]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == facts(database, cid)
    assert coverage(client, restored) == coverage(client, cid)
    restored_source = database.q_raw("select source_id from import_sources where case_id=?", [restored])[0]["source_id"]
    assert source_records(client, restored, restored_source)[-1] == record


@pytest.mark.parametrize("legacy", [f"funds-trace-table-v{version}" for version in range(7, 11)])
@pytest.mark.parametrize("newest_first", [False, True])
def test_v11_unit_header_correction_discharges_only_its_proven_historical_gap(
        client, database, make_case, monkeypatch, legacy, newest_first):
    cid = make_case("Synthetic bounded unit-header correction")
    payload = encoded_table(("transaction timestamp", "payer account", "payee account", "transaction amount(unit: cents)"),
                            [["2026-03-01 10:20:30", "SYN-A", "SYN-B", "123"]])
    old = profile_preview(client, cid, payload, monkeypatch, legacy)
    current = preview(client, cid, payload)
    assert old["table_summary"]["accepted"] == 0 and current["table_summary"]["accepted"] == 1
    for view in ((current, old) if newest_first else (old, current)):
        job = complete(client, cid, view)
        assert job["stage"] == "done"
        if view is old:
            receipt = job["result"]
    old_record = source_records(client, cid, old["import_id"])[0]
    assert old_record["reason_code"] == "unknown_unit"
    assert old_record["details"]["validation_basis"] == table_import.FINANCIAL_CONFLICT_BASIS
    assert old_record["details"]["validation_issues"] == "unknown_unit,missing_amount"
    assert [(row["amount"], row["payer_account"], row["payee_account"]) for row in facts(database, cid)] == [
        (Decimal("1.23"), "SYN-A", "SYN-B")]
    assert coverage(client, cid)["unresolved_records"] == 0
    assert coverage(client, cid)["financial_interpretation_complete"]
    repeated = complete(client, cid, preview(client, cid, payload))
    assert repeated["stage"] == "done" and (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 1)
    assert coverage(client, cid)["financial_interpretation_complete"]
    assert source_records(client, cid, old["import_id"])[0] == old_record
    assert client.get(f"/api/v1/cases/{cid}/imports/{old['import_id']}").json()["result"] == receipt
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == facts(database, cid)
    assert coverage(client, restored) == coverage(client, cid)


@pytest.mark.parametrize("legacy", ["funds-trace-table-v7", "funds-trace-table-v8"])
@pytest.mark.parametrize("newest_first", [False, True])
def test_v11_retains_only_the_exact_proven_direction_header_resolution(
        client, database, make_case, monkeypatch, legacy, newest_first):
    cid = make_case("Synthetic v11 whole direction-header proof")
    payload = encoded_table(("date", "account", "counterparty account", "amount", "debit/credit indicator"),
                            [["2026-03-01 10:20:30", "SYN-OWN", "SYN-OTHER", "1.23", "CREDIT"]])
    old = profile_preview(client, cid, payload, monkeypatch, legacy)
    current = preview(client, cid, payload)
    assert current["mapping"]["direction"] == "debit/credit indicator"
    for view in ((current, old) if newest_first else (old, current)):
        job = complete(client, cid, view)
        assert job["stage"] == "done"
        if view is old:
            receipt = job["result"]
    old_record = source_records(client, cid, old["import_id"])[0]
    assert old_record["reason_code"] == "unknown_unit"
    assert set(old_record["details"]["validation_issues"].split(",")) == {"unknown_unit", "missing_direction"}
    assert coverage(client, cid)["financial_interpretation_complete"]
    assert source_records(client, cid, old["import_id"])[0] == old_record
    assert client.get(f"/api/v1/cases/{cid}/imports/{old['import_id']}").json()["result"] == receipt
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == facts(database, cid)
    assert coverage(client, restored) == coverage(client, cid)


@pytest.mark.parametrize("legacy", ["v10", "archive"])
@pytest.mark.parametrize("newest_first", [False, True])
def test_v11_financial_conflict_is_sticky_across_older_assessment_orders(
        client, database, make_case, monkeypatch, legacy, newest_first):
    cid = make_case("Synthetic v11 strict brackets coverage")
    payload = encoded_table(("date", "payer account", "payee account", "amount(分]"),
                            [["2026-03-01 10:20:30", "SYN-A", "SYN-B", "123"]])
    for profile in (("v11", legacy) if newest_first else (legacy, "v11")):
        view = (preview(client, cid, envelope(payload), "synthetic.zip") if profile == "archive"
                else profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-" + profile))
        job = complete(client, cid, view)
        assert job["stage"] == "done"
        if profile == "v11":
            assert job["result"]["ok"] == 0
            record = source_records(client, cid, view["import_id"])[0]
            assert record["reason_code"] == "unknown_unit"
            assert record["details"]["validation_basis"] == table_import.FINANCIAL_CONFLICT_BASIS
        else:
            assert job["result"]["ok"] == 1
    assert len(facts(database, cid)) == 1
    assert coverage(client, cid)["unresolved_records"] == 1
    assert not coverage(client, cid)["financial_interpretation_complete"]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == facts(database, cid)
    assert coverage(client, restored) == coverage(client, cid)


@pytest.mark.parametrize("difference", [
    "mapping", "unmapped_candidate", "extra_issue", "direction_issue", "future_profile",
])
def test_v11_unit_header_correction_does_not_discharge_an_unproven_gap(
        client, database, make_case, monkeypatch, difference):
    cid = make_case("Synthetic bounded English unit proof rejection")
    extra_header = "amount" if difference == "unmapped_candidate" else "synthetic note"
    extra_value = "1.23" if difference == "unmapped_candidate" else "SYN-NOTE"
    payload = encoded_table(("transaction timestamp", "payer account", "payee account", "amount(unit: cents)", extra_header),
                            [["2026-03-01 10:20:30", "SYN-A", "SYN-B", "123", extra_value]])
    old = profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v10")
    if difference == "mapping":
        response = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview", json={
            "mode": old["mode"], "mapping": {**old["mapping"], "summary": "synthetic note"}})
        assert response.status_code == 200, response.text
        old = response.json()
    assert complete(client, cid, old)["stage"] == "done"
    current = preview(client, cid, payload)
    assert current["table_summary"]["accepted"] == 1
    assert complete(client, cid, current)["stage"] == "done"
    record = source_records(client, cid, old["import_id"])[0]
    if difference in {"extra_issue", "direction_issue"}:
        issues = ("unknown_unit,missing_direction" if difference == "direction_issue"
                  else "unknown_unit,missing_amount,conflicting_time")
        details = {**record["details"], "validation_issues": issues}
        database.x("update import_records set details_json=? where case_id=? and source_id=?",
                   [json.dumps(details), cid, old["import_id"]])
    elif difference == "future_profile":
        database.x("update import_sources set parser_version='funds-trace-table-v17' where case_id=? and source_id=?",
                   [cid, current["import_id"]])
    elif difference == "unmapped_candidate":
        assert "amount" not in old["mapping"] and "amount" not in current["mapping"]
        assert record["details"]["validation_issues"] == "unknown_unit"
    assert len(facts(database, cid)) == 1
    assert coverage(client, cid)["unresolved_records"] == 1
    assert not coverage(client, cid)["financial_interpretation_complete"]
    if difference != "future_profile":
        restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
        assert facts(database, restored) == facts(database, cid)
        assert coverage(client, restored) == coverage(client, cid)


def test_v11_unit_header_proof_resolves_only_admitted_rows_in_the_complete_file(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic row-bound unit-header correction")
    rows = [[(dt.datetime(2026, 3, 1, 10) + dt.timedelta(seconds=index)).isoformat(" "),
             "SYN-A", f"SYN-B-{index}", "123"] for index in range(105)]
    rows.append(["2026-03-01 11:00:00", "SYN-A", "SYN-LAST", "1.1"])
    payload = encoded_table(("transaction timestamp", "payer account", "payee account", "amount(unit: cents)"), rows)
    old = profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v10")
    assert old["table_summary"]["unresolved"] == 106
    old_job = complete(client, cid, old)
    assert old_job["stage"] == "done" and old_job["result"]["ok"] == 0
    old_records = source_records(client, cid, old["import_id"])
    current = preview(client, cid, payload)
    assert (current["table_summary"]["accepted"], current["table_summary"]["unresolved"]) == (105, 1)
    job = complete(client, cid, current)
    assert job["stage"] == "done" and job["result"]["ok"] == 105
    records = source_records(client, cid, current["import_id"])
    assert records[-1]["source_row"] == 107 and records[-1]["flow_id"] is None
    assert records[-1]["reason_code"] == "amount_precision"
    assert source_records(client, cid, old["import_id"]) == old_records
    assert client.get(f"/api/v1/cases/{cid}/imports/{old['import_id']}").json()["result"] == old_job["result"]
    assert coverage(client, cid)["unresolved_records"] == 1
    assert not coverage(client, cid)["financial_interpretation_complete"]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == facts(database, cid)
    assert coverage(client, restored) == coverage(client, cid)
