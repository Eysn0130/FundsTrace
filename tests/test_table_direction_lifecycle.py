"""Public synthetic regressions for the bounded v9 direction-header upgrade."""
import json
import datetime as dt
from decimal import Decimal

import pytest

from app import case_packages, table_contracts, table_import
from app.services import imports
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_contract_lifecycle import facts, source_records
from test_table_mapping_coverage import coverage, envelope
from test_table_mapping_versions import profile_preview
from test_table_source_contracts import encoded_table


ALIASES = ("debit/credit indicator", "credit/debit indicator", "CR/DR",
           "借贷标识", "收支标识", "收付标识")
OWN = ("date", "account", "counterparty account")
ROW = ("2026-03-01 10:00:00", "SYN-OWN", "SYN-OTHER")


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("alias", ALIASES)
def test_direction_headers_complete_automatically_preserve_signs_and_round_trip(
        client, database, make_case, alias, suffix):
    cid = make_case("Synthetic complete direction declarations")
    payload = encoded_table((*OWN, "signed amount/分", alias, "currency"), [
        [*ROW, "-123", "DEBIT", "CNY"],
        ["2026-03-01 11:00:00", "SYN-OWN", "SYN-USD", "234", "CREDIT", "USD"],
    ], suffix)
    view = preview(client, cid, payload, "synthetic" + suffix)
    assert view["automatic"] and view["table_summary"]["profile"] == "funds-trace-table-v16"
    assert view["table_summary"]["accepted"] == 2
    job = complete(client, cid, view)
    assert job["stage"] == "done" and (job["result"]["ok"], job["result"]["rows_dup"]) == (2, 0)
    expected = facts(database, cid)
    assert [(row["payer_account"], row["payee_account"], row["amount"], row["currency"])
            for row in expected] == [
        ("SYN-OWN", "SYN-OTHER", Decimal("1.23"), "CNY"),
        ("SYN-USD", "SYN-OWN", Decimal("2.34"), "USD"),
    ]
    records = source_records(client, cid, view["import_id"])
    assert all(record["disposition"] == "imported" and record["flow_id"] for record in records)
    assert all(record["details"]["direction_basis"] == "explicit_direction" for record in records)
    assert all(table_contracts.validate_details(record["details"], record["disposition"])
               for record in records)
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == payload
    repeated = complete(client, cid, preview(client, cid, payload, "synthetic-repeat" + suffix))
    assert repeated["stage"] == "done" and (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 2)
    assert facts(database, cid) == expected
    assert coverage(client, cid)["financial_interpretation_complete"]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == expected
    assert coverage(client, restored) == coverage(client, cid)
    assert {item["parser_version"] for item in database.q_raw(
        "select parser_version from import_sources where case_id=?", [restored])} == {"funds-trace-table-v16"}


@pytest.mark.parametrize("alias,reason", [
    ("debit/credit indicator", "unknown_unit"),
    ("credit/debit indicator", "unknown_unit"),
    ("借贷标识", "missing_direction"),
    ("CR/DR", "missing_direction"),
])
def test_ready_v8_direction_diagnostics_remain_frozen_after_v11_default(
        client, database, make_case, monkeypatch, alias, reason):
    cid = make_case("Synthetic frozen v8 direction session")
    payload = encoded_table((*OWN, "amount", alias), [[*ROW, "1.23", "DEBIT"]])
    old = profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v8")
    assert old["table_summary"]["accepted"] == 0
    assert imports.TABLE_PROFILE == table_import.PROFILE == "funds-trace-table-v16"
    rechecked = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview", json={
        "mode": old["mode"], "mapping": old["mapping"]})
    assert rechecked.status_code == 200, rechecked.text
    frozen = rechecked.json()
    assert frozen["table_summary"]["profile"] == "funds-trace-table-v8"
    old_job = complete(client, cid, frozen)
    assert old_job["stage"] == "done" and old_job["result"]["ok"] == 0
    record = source_records(client, cid, old["import_id"])[0]
    assert record["disposition"] == "unresolved"
    assert reason in record["details"]["validation_issues"].split(",")
    assert not facts(database, cid)
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert not facts(database, restored)
    assert coverage(client, restored) == coverage(client, cid)


def test_v8_ready_same_entry_balance_contract_retains_recovery_and_package_support(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic retained v8 book balances")
    payload = encoded_table((*OWN, "amount", "交易前账面余额", "交易后账面余额"),
                            [[*ROW, "1.23", "-10.00", "-11.23"]])
    old = profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v8")
    assert old["table_summary"]["accepted"] == 1
    response = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview", json={
        "mode": old["mode"], "mapping": old["mapping"]})
    assert response.status_code == 200, response.text
    assert response.json()["table_summary"]["profile"] == "funds-trace-table-v8"
    job = complete(client, cid, response.json())
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    expected = facts(database, cid)
    assert [(row["payer_account"], row["payee_account"], row["amount"], row["balance"])
            for row in expected] == [("SYN-OWN", "SYN-OTHER", Decimal("1.23"), Decimal("-11.23"))]
    record = source_records(client, cid, old["import_id"])[0]
    assert record["details"]["direction_basis"] == "same_entry_book_balances"
    assert record["details"]["balance_basis"] == "same_entry_exact"
    assert coverage(client, cid)["financial_interpretation_complete"]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == expected


@pytest.mark.parametrize("newest_first", [False, True])
def test_v9_resolved_missing_direction_supersedes_frozen_v8_assessment(
        client, database, make_case, monkeypatch, newest_first):
    cid = make_case("Synthetic direction assessment precedence")
    payload = encoded_table((*OWN, "amount", "借贷标识"), [[*ROW, "1.23", "DEBIT"]])
    for profile in (("funds-trace-table-v9", "funds-trace-table-v8") if newest_first
                    else ("funds-trace-table-v8", "funds-trace-table-v9")):
        job = complete(client, cid, profile_preview(client, cid, payload, monkeypatch, profile))
        assert job["stage"] == "done"
    assert len(facts(database, cid)) == 1
    assert coverage(client, cid)["unresolved_records"] == 0
    assert coverage(client, cid)["financial_interpretation_complete"]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == facts(database, cid)
    assert coverage(client, restored) == coverage(client, cid)


@pytest.mark.parametrize("profile", ["funds-trace-table-v7", "funds-trace-table-v8"])
@pytest.mark.parametrize("alias", ALIASES[:2])
@pytest.mark.parametrize("signed", [False, True])
@pytest.mark.parametrize("newest_first", [False, True])
def test_corrected_full_direction_header_discharges_only_its_old_unit_diagnostic(
        client, database, make_case, monkeypatch, profile, alias, signed, newest_first):
    cid = make_case("Synthetic resolved historical direction-header defect")
    payload = encoded_table((*OWN, "signed amount" if signed else "amount", alias),
                            [[*ROW, "1.23", "CREDIT"]])
    views = {name: profile_preview(client, cid, payload, monkeypatch, name)
             for name in (profile, "funds-trace-table-v9")}
    assert views[profile]["table_summary"]["accepted"] == 0
    assert views["funds-trace-table-v9"]["table_summary"]["accepted"] == 1
    for name in (("funds-trace-table-v9", profile) if newest_first
                 else (profile, "funds-trace-table-v9")):
        job = complete(client, cid, views[name])
        assert job["stage"] == "done", job
        if name == profile:
            old_receipt = job["result"]
    old_record = source_records(client, cid, views[profile]["import_id"])[0]
    assert old_record["reason_code"] == "unknown_unit"
    assert set(old_record["details"]["validation_issues"].split(",")) == (
        {"unknown_unit"} if signed else {"unknown_unit", "missing_direction"})
    expected = facts(database, cid)
    assert [(row["payer_account"], row["payee_account"], row["amount"])
            for row in expected] == [("SYN-OTHER", "SYN-OWN", Decimal("1.23"))]
    assert coverage(client, cid)["unresolved_records"] == 0
    assert coverage(client, cid)["financial_interpretation_complete"]
    assert source_records(client, cid, views[profile]["import_id"])[0] == old_record
    assert client.get(f"/api/v1/cases/{cid}/imports/{views[profile]['import_id']}").json()["result"] == old_receipt
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == expected
    assert coverage(client, restored) == coverage(client, cid)


@pytest.mark.parametrize("difference", ["other_mapping", "other_direction", "other_issue", "other_alias"])
def test_new_direction_header_does_not_erase_unproven_historical_financial_gaps(
        client, database, make_case, monkeypatch, difference):
    cid = make_case("Synthetic unrelated historical financial gap")
    alias = "debit/credit indicator"
    payload = encoded_table((*OWN, "amount", alias, "synthetic extra"),
                            [[*ROW, "1.23", "CREDIT", "SYN-EXTRA"]])
    old = profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v8")
    assert complete(client, cid, old)["stage"] == "done"
    assert complete(client, cid, preview(client, cid, payload))["stage"] == "done"
    assert len(facts(database, cid)) == 1
    if difference == "other_issue":
        record = source_records(client, cid, old["import_id"])[0]
        details = {**record["details"], "validation_issues": "unknown_unit,conflicting_amount"}
        database.x("update import_records set details_json=? where case_id=? and source_id=?", [
            json.dumps(details), cid, old["import_id"]])
    else:
        mapping = dict(old["mapping"])
        if difference == "other_mapping":
            mapping["summary"] = "synthetic extra"
        elif difference == "other_direction":
            mapping["direction"] = "synthetic extra"
        else:
            mapping["expense_amt"] = "debit/unknown indicator"
        database.x("update import_sources set mapping_json=? where case_id=? and source_id=?", [
            json.dumps(mapping), cid, old["import_id"]])
    assert coverage(client, cid)["unresolved_records"] == 1
    assert not coverage(client, cid)["financial_interpretation_complete"]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == facts(database, cid)
    assert coverage(client, restored) == coverage(client, cid)


@pytest.mark.parametrize("flag,amount,disposition", [
    ("UNKNOWN", "1.23", "unresolved"),
    ("CREDIT", "0.00", "non_monetary"),
])
def test_direction_header_gap_requires_an_actually_admitted_current_evaluation(
        client, database, make_case, monkeypatch, flag, amount, disposition):
    cid = make_case("Synthetic historical gap without resolved admission")
    payload = encoded_table((*OWN, "amount", "credit/debit indicator"), [[*ROW, amount, flag]])
    old = profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v8")
    assert complete(client, cid, old)["stage"] == "done"
    current = preview(client, cid, payload)
    assert complete(client, cid, current)["stage"] == "done"
    record = source_records(client, cid, current["import_id"])[0]
    assert record["disposition"] == disposition
    assert not facts(database, cid)
    assert coverage(client, cid)["unresolved_records"] == 1
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert coverage(client, restored) == coverage(client, cid)


def test_large_header_upgrade_keeps_record_level_conflict_after_other_rows_resolve(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic bulk direction-header upgrade")
    rows = [[(dt.datetime(2026, 3, 1, 10) + dt.timedelta(seconds=index)).isoformat(" "),
             "SYN-OWN", f"SYN-OTHER-{index}", "1.23", "DEBIT" if index == 500 else "CREDIT"]
            for index in range(1000)]
    payload = encoded_table((*OWN, "signed amount", "credit/debit indicator"), rows)
    old = profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v8")
    assert old["table_summary"]["unresolved"] == 1000
    assert complete(client, cid, old)["stage"] == "done"
    current = preview(client, cid, payload)
    assert (current["table_summary"]["accepted"], current["table_summary"]["unresolved"]) == (999, 1)
    job = complete(client, cid, current)
    assert job["stage"] == "done" and job["result"]["ok"] == 999
    assert database.q_raw("select count(*) n,sum(amount) total from effective_flows where case_id=?", [cid]) == [
        {"n": 999, "total": Decimal("1228.77")}]
    assert database.q_raw("select count(*) n from import_records where case_id=? and source_id=? and disposition='unresolved'",
                          [cid, old["import_id"]]) == [{"n": 1000}]
    report = source_records(client, cid, current["import_id"])
    assert report[500]["disposition"] == "unresolved" and report[500]["flow_id"] is None
    assert "conflicting_direction" in report[500]["details"]["validation_issues"].split(",")
    assert coverage(client, cid)["unresolved_records"] == 1
    assert not coverage(client, cid)["financial_interpretation_complete"]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == facts(database, cid)
    assert coverage(client, restored) == coverage(client, cid)


@pytest.mark.parametrize("legacy", ["v8", "archive"])
@pytest.mark.parametrize("newest_first", [False, True])
@pytest.mark.parametrize("evidence", ["flag", "split"])
def test_new_direction_conflict_cannot_be_hidden_by_advanced_mapping_or_older_evaluation(
        client, database, make_case, monkeypatch, legacy, newest_first, evidence):
    cid = make_case("Synthetic complete direction conflict")
    column, value, flag = (("direction", "out", "CREDIT") if evidence == "flag"
                           else ("credit amount", "1.23", "DEBIT"))
    payload = encoded_table((*OWN, "amount", column, "借贷标识"),
                            [[*ROW, "1.23", value, flag]])
    archived = envelope(payload)
    legacy_kind = legacy
    for kind in (("current", legacy_kind) if newest_first else (legacy_kind, "current")):
        if kind == "archive":
            view = preview(client, cid, archived, "synthetic.zip")
        elif kind == "v8":
            view = profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v8")
        else:
            view = preview(client, cid, payload)
            response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={
                "mode": view["mode"], "mapping": {**view["mapping"], "direction": (
                    "direction" if evidence == "flag" else "借贷标识")}})
            assert response.status_code == 200, response.text
            view = response.json()
            assert view["table_summary"]["unresolved"] == 1
        job = complete(client, cid, view)
        assert job["stage"] == "done", job
        if kind == "current":
            assert job["result"]["ok"] == 0
            record = source_records(client, cid, view["import_id"])[0]
            assert record["disposition"] == "unresolved" and record["flow_id"] is None
            assert "conflicting_direction" in record["details"]["validation_issues"].split(",")
            assert record["details"]["validation_basis"] == table_import.FINANCIAL_CONFLICT_BASIS
        else:
            old_source, old_receipt = view["import_id"], job["result"]
            assert job["result"]["ok"] == 1
    expected = facts(database, cid)
    assert len(expected) == 1 and expected[0]["amount"] == Decimal("1.23")
    assert coverage(client, cid)["unresolved_records"] == 1
    repeated = (preview(client, cid, archived, "synthetic-repeat.zip") if legacy == "archive"
                else profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v8"))
    assert complete(client, cid, repeated)["stage"] == "done"
    assert facts(database, cid) == expected
    assert coverage(client, cid)["unresolved_records"] == 1
    assert client.get(f"/api/v1/cases/{cid}/imports/{old_source}").json()["result"] == old_receipt
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == expected
    assert coverage(client, restored) == coverage(client, cid)


@pytest.mark.parametrize("newest_first", [False, True])
def test_two_ready_v8_current_sessions_with_distinct_facts_cannot_double_count(
        client, database, make_case, monkeypatch, newest_first):
    cid = make_case("Synthetic direction upgrade historical binding")
    payload = encoded_table((*OWN, "amount", "direction", "收支标识", "memo", "channel"),
                            [[*ROW, "1.23", "out", "DEBIT", "SYN-MEMO", "SYN-CHANNEL"]])
    old = profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v8")
    current = preview(client, cid, payload)
    response = client.post(f"/api/v1/cases/{cid}/imports/{current['import_id']}/preview", json={
        "mode": current["mode"], "mapping": {**current["mapping"], "summary": "channel"}})
    assert response.status_code == 200, response.text
    current = response.json()
    assert old["table_summary"]["accepted"] == current["table_summary"]["accepted"] == 1
    first, second = (current, old) if newest_first else (old, current)
    first_job = complete(client, cid, first)
    assert first_job["stage"] == "done" and first_job["result"]["ok"] == 1
    expected = facts(database, cid)
    # Do not repreview: the final commit must bind facts written since preview.
    second_job = complete(client, cid, second)
    assert second_job["stage"] == "done" and second_job["result"]["ok"] == 0
    assert second_job["result"]["table_summary"]["unresolved"] == 1
    assert facts(database, cid) == expected
    record = source_records(client, cid, second["import_id"])[0]
    assert record["disposition"] == "unresolved" and record["flow_id"] is None
    assert record["details"]["counterparty_basis"] == table_import.HISTORY_CONFLICT_BASIS
    assert database.q_raw("select sum(amount) amount from effective_flows where case_id=?", [cid]) == [
        {"amount": Decimal("1.23")}]
    assert client.get(f"/api/v1/cases/{cid}/imports/{first['import_id']}").json()["result"] == first_job["result"]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == expected
    assert coverage(client, restored) == coverage(client, cid)
