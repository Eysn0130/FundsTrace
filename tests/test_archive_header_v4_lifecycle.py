"""Public archive-v4 lifecycles preserve history while fixing two header rules."""
from decimal import Decimal
import json

import pytest

from app import case_packages, table_import
from app.services import imports
from test_archive_profile_upgrade import frozen_preview
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_container_identity import archive_bytes, case_flows
from test_table_contract_lifecycle import facts, source_records
from test_table_currency_region_lifecycle import receipt, source_snapshot
from test_table_currency_region_v17 import FIRST, HEADERS, OVERLAP, SECOND
from test_table_multisheet_append import encoded
from test_table_mapping_coverage import coverage
from test_table_write_integrity import persisted


OLD_PROFILE = "funds-trace-table-archive-v3"
NEW_PROFILE = "funds-trace-table-archive-v4"


def csv_data(rows):
    return encoded([("Synthetic archive headers", rows)], ".csv")


def currency_data(code="USD"):
    return csv_data([
        ["date", "payer_account", "payee_account", "amount", "币种代码"],
        ["2026-01-01", "SYN-A", "SYN-B", "123.45", "CNY"],
        ["2026-01-02", "SYN-C", "SYN-D", "2.03", "人民币"],
        ["2026-01-03", "SYN-E", "SYN-F", "4.56", code],
    ])


def region_data():
    clear = ["date", "payee_account", "payer_account", "amount", "extra1", "extra2"]
    recovered = ["2026-01-05", "SYN-E", "SYN-F", "3.45", "", ""]
    return csv_data([HEADERS, FIRST, OVERLAP, SECOND, clear, recovered])


def commit_view(client, cid, view):
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    return job


def old_ready(client, cid, payload, content, monkeypatch, kind="archive"):
    if kind == "archive":
        return frozen_preview(client, cid, content, monkeypatch, OLD_PROFILE)
    with monkeypatch.context() as historical:
        historical.setattr(imports, "TABLE_PROFILE", "funds-trace-table-v16")
        return preview(client, cid, payload, "standalone.csv")


def mapped_v5_ready(client, cid, payload, monkeypatch, *, currency_column=None):
    with monkeypatch.context() as historical:
        historical.setattr(imports, "TABLE_PROFILE", "funds-trace-table-v5")
        view = preview(client, cid, payload, "standalone.csv")
    if currency_column is not None:
        response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={
            "mode": view["mode"], "mapping": {**view["mapping"], "currency": currency_column}})
        assert response.status_code == 200, response.text
        view = response.json()
    assert view["table_summary"]["profile"] == "funds-trace-table-v5"
    return view


def refresh(client, cid, view):
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={
        "mode": view["mode"], "mapping": view["mapping"]})
    assert response.status_code == 200, response.text
    current = response.json()
    assert current["table_summary"]["profile"] == view["table_summary"]["profile"]
    return current


def assert_history_conflicts(records, indexes):
    for index in indexes:
        record = records[index]
        assert (record["disposition"], record["reason_code"], record["flow_id"]) == (
            "unresolved", "conflicting_field", None)
        assert record["details"]["financial_effect"] == "undetermined"
        assert record["details"]["counterparty_basis"] == table_import.ARCHIVE_HISTORY_CONFLICT_BASIS
        assert "conflicting_field" in record["details"]["validation_issues"].split(",")


def assert_incomplete(client, cid):
    value = coverage(client, cid)
    assert value["unresolved_records"] > 0
    assert value["financial_interpretation_complete"] is False
    return value


def currency_history(client, database, make_case, monkeypatch, *, new_first, kind="archive"):
    cid = make_case("Synthetic archive header historical conflict")
    payload = currency_data()
    content = archive_bytes([("bank/currency.csv", payload)], nested=True)
    legacy = old_ready(client, cid, payload, content, monkeypatch, kind)
    assert legacy["table_summary"]["accepted"] == 3
    assert legacy["table_summary"]["currency_totals"] == {"CNY": "130.04"}
    request = json.loads(database.q_raw("select request_json from import_sessions where import_id=?",
                                      [legacy["import_id"]])[0]["request_json"])
    assert request["_table_profile"] == (OLD_PROFILE if kind == "archive" else "funds-trace-table-v16")
    first = preview(client, cid, content, "current.zip") if new_first else legacy
    first_job = commit_view(client, cid, first)
    assert (first_job["result"]["ok"], first_job["result"]["rows_dup"]) == (3, 0)
    original = case_flows(database, cid)
    frozen = source_snapshot(database, cid, first["import_id"])
    second = refresh(client, cid, legacy) if new_first else preview(client, cid, content, "current.zip")
    assert (second["table_summary"]["accepted"], second["table_summary"]["unresolved"]) == (2, 1)
    second_job = commit_view(client, cid, second)
    assert (second_job["result"]["ok"], second_job["result"]["rows_dup"]) == (0, 2)
    assert_history_conflicts(source_records(client, cid, second["import_id"]), [2])
    assert case_flows(database, cid) == original
    assert source_snapshot(database, cid, first["import_id"]) == frozen
    assert receipt(client, cid, first["import_id"]) == first_job["result"]
    expected_totals = {"CNY": Decimal("125.48"), "USD": Decimal("4.56")} if new_first else {"CNY": Decimal("130.04")}
    assert {row["currency"]: row["amount"] for row in database.q_raw(
        "select currency,sum(amount) amount from flows where case_id=? group by currency", [cid])} == expected_totals
    assert_incomplete(client, cid)
    return cid, payload, content, first, second


@pytest.mark.parametrize("archive_first", [False, True], ids=["csv-first", "archive-first"])
def test_same_currency_code_csv_bytes_in_both_container_orders_keep_exact_totals_and_flow_ids(
        client, database, make_case, archive_first):
    cid = make_case("Synthetic v4 currency container parity")
    payload = currency_data()
    content = archive_bytes([("bank/currency-records.csv", payload)], nested=True)
    materials = [(payload, "standalone.csv"), (content, "synthetic.zip")]
    if archive_first:
        materials.reverse()
    first = preview(client, cid, *materials[0])
    assert first["table_summary"]["currency_totals"] == {"CNY": "125.48", "USD": "4.56"}
    assert first["table_summary"]["profile"] == (NEW_PROFILE if archive_first else table_import.PROFILE)
    initial = commit_view(client, cid, first)
    assert (initial["result"]["ok"], initial["result"]["rows_dup"]) == (3, 0)
    before, frozen = case_flows(database, cid), source_snapshot(database, cid, first["import_id"])
    second = preview(client, cid, *materials[1])
    assert second["table_summary"]["currency_totals"] == {"CNY": "125.48", "USD": "4.56"}
    assert second["table_summary"]["profile"] == (table_import.PROFILE if archive_first else NEW_PROFILE)
    appended = commit_view(client, cid, second)
    assert (appended["result"]["ok"], appended["result"]["rows_dup"]) == (0, 3)
    assert case_flows(database, cid) == before
    assert {row["currency"]: row["amount"] for row in database.q_raw(
        "select currency,sum(amount) amount from flows where case_id=? group by currency", [cid])} == {
        "CNY": Decimal("125.48"), "USD": Decimal("4.56")}
    assert source_snapshot(database, cid, first["import_id"]) == frozen
    assert receipt(client, cid, first["import_id"]) == initial["result"]
    assert [record["disposition"] for record in source_records(client, cid, second["import_id"])] == ["duplicate"] * 3


def test_archive_v4_ambiguous_region_preserves_two_records_recovers_and_isolates_other_member(
        client, database, make_case):
    cid = make_case("Synthetic v4 bounded region recovery")
    other = csv_data([["date", "payer_account", "payee_account", "amount", "currency"],
                      ["2026-01-06", "SYN-P", "SYN-Q", "2.03", "USD"]])
    content = archive_bytes([("bank/regions.csv", region_data()), ("bank/other.csv", other)], nested=True)
    view = preview(client, cid, content, "synthetic.zip")
    summary = view["table_summary"]
    assert summary["currency_totals"] == {"CNY": "4.68", "USD": "2.03"}
    assert summary["profile"] == NEW_PROFILE
    assert (summary["accepted"], summary["unresolved"]) == (3, 2)
    job = commit_view(client, cid, view)
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (3, 0)
    records = source_records(client, cid, view["import_id"])
    assert [record["source_row"] for record in records] == [2, 3, 4, 5, 6, 2]
    assert [record["reason_code"] for record in records] == [
        "transaction_valid", "ambiguous_region_header", "ambiguous_region_header",
        "repeated_header", "transaction_valid", "transaction_valid"]
    assert all(record["flow_id"] is None and record["details"]["financial_effect"] == "undetermined"
               for record in records[1:3])
    assert [(row["payer_account"], row["payee_account"], row["amount"], row["currency"])
            for row in case_flows(database, cid)] == [
        ("SYN-A", "SYN-B", Decimal("1.23"), "CNY"),
        ("SYN-F", "SYN-E", Decimal("3.45"), "CNY"),
        ("SYN-P", "SYN-Q", Decimal("2.03"), "USD")]


@pytest.mark.parametrize("new_first", [False, True], ids=["old-v3-then-v4", "v4-then-old-ready"])
def test_old_and_current_archive_currency_conflicts_preserve_first_facts_and_frozen_ready_version(
        client, database, make_case, monkeypatch, new_first):
    currency_history(client, database, make_case, monkeypatch, new_first=new_first)


@pytest.mark.parametrize("new_first", [False, True], ids=["old-csv-then-v4", "v4-then-old-csv-ready"])
def test_cross_container_v4_currency_conflict_adds_no_money_or_false_duplicate(
        client, database, make_case, monkeypatch, new_first):
    currency_history(client, database, make_case, monkeypatch, new_first=new_first, kind="ordinary")


def test_old_archive_region_facts_remain_unchanged_when_v4_marks_the_two_rows_uncertain(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic old region facts remain historical")
    payload = region_data()
    content = archive_bytes([("bank/regions.csv", payload)], nested=True)
    old = old_ready(client, cid, payload, content, monkeypatch)
    assert (old["table_summary"]["accepted"], old["table_summary"]["unresolved"]) == (4, 0)
    first = commit_view(client, cid, old)
    assert first["result"]["ok"] == 4
    before, frozen = case_flows(database, cid), source_snapshot(database, cid, old["import_id"])
    current = preview(client, cid, content, "current.zip")
    assert (current["table_summary"]["accepted"], current["table_summary"]["unresolved"]) == (2, 2)
    second = commit_view(client, cid, current)
    assert (second["result"]["ok"], second["result"]["rows_dup"]) == (0, 2)
    records = source_records(client, cid, current["import_id"])
    assert [record["reason_code"] for record in records] == [
        "transaction_valid", "ambiguous_region_header", "ambiguous_region_header", "repeated_header", "transaction_valid"]
    assert case_flows(database, cid) == before
    assert sum(row["amount"] for row in before) == Decimal("203.68")
    assert source_snapshot(database, cid, old["import_id"]) == frozen
    assert receipt(client, cid, old["import_id"]) == first["result"]
    assert assert_incomplete(client, cid)["unresolved_records"] == 2


@pytest.mark.parametrize("kind", ["archive", "ordinary"], ids=["old-archive-ready", "old-csv-ready"])
@pytest.mark.parametrize("reason", ["invalid_currency", "ambiguous_region_header"])
def test_v4_uncertain_header_assessment_blocks_legacy_ready_from_reaccepting_physical_records(
        client, database, make_case, monkeypatch, kind, reason):
    cid = make_case("Synthetic v4 unresolved proof blocks old ready")
    if reason == "invalid_currency":
        payload = csv_data([
            ["date", "payer_account", "payee_account", "amount", "币种代码", "currency"],
            ["2026-02-01", "SYN-NO", "SYN-NO2", "9.99", "USD", "CNY"],
            ["2026-02-02", "SYN-A", "SYN-B", "1.23", "CNY", "RMB"],
        ])
        conflicts, accepted = [0], 1
    else:
        payload = region_data()
        conflicts, accepted = [1, 2], 2
    content = archive_bytes([("bank/header-proof.csv", payload)], nested=True)
    old = old_ready(client, cid, payload, content, monkeypatch, kind)
    assert old["table_summary"]["accepted"] == accepted + len(conflicts)
    current = preview(client, cid, content, "current.zip")
    assert (current["table_summary"]["accepted"], current["table_summary"]["unresolved"]) == (accepted, len(conflicts))
    initial = commit_view(client, cid, current)
    assert initial["result"]["ok"] == accepted
    current_records = source_records(client, cid, current["import_id"])
    assert all(current_records[index]["reason_code"] == reason for index in conflicts)
    before, frozen = case_flows(database, cid), source_snapshot(database, cid, current["import_id"])
    old = refresh(client, cid, old)
    assert (old["table_summary"]["accepted"], old["table_summary"]["unresolved"]) == (accepted, len(conflicts))
    blocked = commit_view(client, cid, old)
    assert (blocked["result"]["ok"], blocked["result"]["rows_dup"]) == (0, accepted)
    assert_history_conflicts(source_records(client, cid, old["import_id"]), conflicts)
    assert case_flows(database, cid) == before
    assert source_snapshot(database, cid, current["import_id"]) == frozen
    assert receipt(client, cid, current["import_id"]) == initial["result"]
    assert_incomplete(client, cid)


@pytest.mark.parametrize("field,value", [("self_account", "SYN-UNPROVEN-OWNER"), ("self_name", "Synthetic other owner")])
def test_v4_cross_container_history_checks_self_identity_in_addition_to_money_facts(
        client, database, make_case, field, value):
    cid = make_case("Synthetic v4 full self identity conflict")
    payload = csv_data([
        ["date", "account", "counterparty account", "direction", "amount", "currency", "account name"],
        ["2026-03-01", "SYN-OWN", "SYN-OTHER", "out", "1.23", "USD", "Synthetic owner"],
    ])
    standalone = preview(client, cid, payload, "standalone.csv")
    assert commit_view(client, cid, standalone)["result"]["ok"] == 1
    database.x(f"update flows set {field}=? where case_id=?", [value, cid])
    before, frozen = case_flows(database, cid), source_snapshot(database, cid, standalone["import_id"])
    content = archive_bytes([("bank/self.csv", payload)], nested=True)
    current = preview(client, cid, content, "current.zip")
    assert (current["table_summary"]["accepted"], current["table_summary"]["unresolved"]) == (0, 1)
    job = commit_view(client, cid, current)
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (0, 0)
    assert_history_conflicts(source_records(client, cid, current["import_id"]), [0])
    assert case_flows(database, cid) == before
    assert source_snapshot(database, cid, standalone["import_id"]) == frozen
    assert_incomplete(client, cid)


def test_v4_history_without_own_flow_blocks_third_interpretation_but_allows_identical_old_facts(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic v4 history-only direct evidence")
    payload = csv_data([
        ["date", "payer_account", "payee_account", "amount", "币种代码", "unused_eur"],
        ["2026-04-01", "SYN-A", "SYN-B", "1.23", "USD", "EUR"],
    ])
    content = archive_bytes([("bank/history.csv", payload)], nested=True)
    old = old_ready(client, cid, payload, content, monkeypatch)
    same_ready = old_ready(client, cid, payload, content, monkeypatch)
    third_ready = mapped_v5_ready(client, cid, payload, monkeypatch, currency_column="unused_eur")
    assert third_ready["table_summary"]["currency_totals"] == {"EUR": "1.23"}
    first = commit_view(client, cid, old)
    assert first["result"]["ok"] == 1
    before = case_flows(database, cid)
    assert before[0]["currency"] == "CNY"
    current = preview(client, cid, content, "current.zip")
    assert commit_view(client, cid, current)["result"]["ok"] == 0
    assert_history_conflicts(source_records(client, cid, current["import_id"]), [0])
    assert database.q_raw("select * from flow_sources where case_id=? and source_id=?",
                          [cid, current["import_id"]]) == []
    frozen = source_snapshot(database, cid, current["import_id"])
    third = refresh(client, cid, third_ready)
    assert (third["table_summary"]["accepted"], third["table_summary"]["unresolved"]) == (0, 1)
    blocked = commit_view(client, cid, third)
    assert (blocked["result"]["ok"], blocked["result"]["rows_dup"]) == (0, 0)
    assert_history_conflicts(source_records(client, cid, third["import_id"]), [0])
    same = refresh(client, cid, same_ready)
    assert (same["table_summary"]["accepted"], same["table_summary"]["unresolved"]) == (1, 0)
    duplicate = commit_view(client, cid, same)
    assert (duplicate["result"]["ok"], duplicate["result"]["rows_dup"]) == (0, 1)
    assert case_flows(database, cid) == before
    assert source_snapshot(database, cid, current["import_id"]) == frozen
    assert receipt(client, cid, old["import_id"]) == first["result"]
    assert_incomplete(client, cid)


def test_v4_direct_history_compares_all_ordinary_old_interpretations_not_only_one_match(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic multiple ordinary historical interpretations")
    payload = csv_data([
        ["date", "payer_account", "payee_account", "amount", "币种代码", "unused_eur"],
        ["2026-04-02", "SYN-A", "SYN-B", "1.23", "USD", "EUR"],
    ])
    old_cny = mapped_v5_ready(client, cid, payload, monkeypatch)
    old_eur = mapped_v5_ready(client, cid, payload, monkeypatch, currency_column="unused_eur")
    later_cny = mapped_v5_ready(client, cid, payload, monkeypatch)
    assert commit_view(client, cid, old_cny)["result"]["ok"] == 1
    assert commit_view(client, cid, old_eur)["result"]["ok"] == 1
    before = case_flows(database, cid)
    assert [row["currency"] for row in before] == ["CNY", "EUR"]
    old_sources = {view["import_id"]: source_snapshot(database, cid, view["import_id"])
                   for view in (old_cny, old_eur)}
    content = archive_bytes([("bank/multiple-history.csv", payload)], nested=True)
    current = preview(client, cid, content, "current.zip")
    current_job = commit_view(client, cid, current)
    assert (current_job["result"]["ok"], current_job["result"]["rows_dup"]) == (0, 0)
    assert_history_conflicts(source_records(client, cid, current["import_id"]), [0])
    # CNY matches one stored fact but disagrees with the other direct ordinary
    # interpretation. It cannot be accepted as a duplicate of just one row.
    later = refresh(client, cid, later_cny)
    assert (later["table_summary"]["accepted"], later["table_summary"]["unresolved"]) == (0, 1)
    job = commit_view(client, cid, later)
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (0, 0)
    assert_history_conflicts(source_records(client, cid, later["import_id"]), [0])
    assert case_flows(database, cid) == before
    for source_id, snapshot in old_sources.items():
        assert source_snapshot(database, cid, source_id) == snapshot
    assert_incomplete(client, cid)


def test_v4_history_does_not_follow_an_ordinary_bridge_to_another_archive_path(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic direct archive matching without transitive closure")
    payload = csv_data([
        ["date", "payer_account", "payee_account", "amount", "币种代码"],
        ["2026-04-03", "SYN-A", "SYN-B", "1.23", "USD"],
    ])
    ordinary = preview(client, cid, payload, "standalone.csv")
    assert commit_view(client, cid, ordinary)["result"]["ok"] == 1
    ordinary_id = case_flows(database, cid)[0]["id"]
    content_a = archive_bytes([("bank/A.csv", payload)], nested=True)
    old_a = old_ready(client, cid, payload, content_a, monkeypatch)
    old_job = commit_view(client, cid, old_a)
    assert (old_job["result"]["ok"], old_job["result"]["rows_dup"]) == (1, 0)
    before = case_flows(database, cid)
    assert [row["currency"] for row in before] == ["USD", "CNY"]
    current_a = preview(client, cid, content_a, "current-a.zip")
    assert commit_view(client, cid, current_a)["result"]["ok"] == 0
    assert_history_conflicts(source_records(client, cid, current_a["import_id"]), [0])
    frozen = source_snapshot(database, cid, current_a["import_id"])
    content_b = archive_bytes([("bank/B.csv", payload)], nested=True)
    current_b = preview(client, cid, content_b, "current-b.zip")
    assert (current_b["table_summary"]["accepted"], current_b["table_summary"]["unresolved"]) == (1, 0)
    job = commit_view(client, cid, current_b)
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (0, 1)
    record = source_records(client, cid, current_b["import_id"])[0]
    assert (record["disposition"], int(record["flow_id"])) == ("duplicate", ordinary_id)
    assert case_flows(database, cid) == before
    assert source_snapshot(database, cid, current_a["import_id"]) == frozen
    assert_incomplete(client, cid)


@pytest.mark.parametrize("scope", ["other-case", "other-archive-path"])
def test_v4_history_binding_does_not_merge_different_cases_or_archive_member_paths(
        client, database, make_case, monkeypatch, scope):
    original_case = make_case("Synthetic archive physical scope")
    payload = currency_data()
    old_content = archive_bytes([("bank/original.csv", payload)], nested=True)
    old = old_ready(client, original_case, payload, old_content, monkeypatch)
    assert commit_view(client, original_case, old)["result"]["ok"] == 3
    before = case_flows(database, original_case)
    cid = make_case("Synthetic other case") if scope == "other-case" else original_case
    path = "bank/original.csv" if scope == "other-case" else "bank/independent.csv"
    current = preview(client, cid, archive_bytes([(path, payload)], nested=True), "current.zip")
    assert (current["table_summary"]["accepted"], current["table_summary"]["unresolved"]) == (3, 0)
    job = commit_view(client, cid, current)
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (3, 0)
    if scope == "other-case":
        assert case_flows(database, original_case) == before
        assert client.get(f"/api/v1/cases/{cid}/sources/{old['import_id']}/report").status_code == 404
    else:
        assert case_flows(database, cid)[:3] == before and len(case_flows(database, cid)) == 6


def test_v4_history_conflict_new_money_and_receipt_roll_back_together_after_late_failure(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic v4 historical conflict transaction")
    payload = currency_data()
    old_content = archive_bytes([("bank/currency.csv", payload)], nested=True)
    old = old_ready(client, cid, payload, old_content, monkeypatch)
    first = commit_view(client, cid, old)
    assert first["result"]["ok"] == 3
    before = persisted(database)
    frozen = source_snapshot(database, cid, old["import_id"])
    extra = csv_data([["date", "payer_account", "payee_account", "amount", "currency"],
                      ["2026-01-04", "SYN-NEW", "SYN-NEW2", "6.78", "CNY"]])
    current_content = archive_bytes([("bank/currency.csv", payload), ("bank/new.csv", extra)], nested=True)
    current = preview(client, cid, current_content, "current.zip")
    assert (current["table_summary"]["accepted"], current["table_summary"]["unresolved"]) == (3, 1)
    write = database.insert_dataframe

    def fail_after_records(table, frame, columns):
        result = write(table, frame, columns)
        if table == "import_records":
            raise RuntimeError("Synthetic A01 late journal failure")
        return result

    with monkeypatch.context() as failure:
        failure.setattr(database, "insert_dataframe", fail_after_records)
        failed = complete(client, cid, current)
    assert failed["stage"] == "error" and failed["result"] is None
    assert persisted(database) == before
    assert source_snapshot(database, cid, old["import_id"]) == frozen
    assert receipt(client, cid, old["import_id"]) == first["result"]
    retried = commit_view(client, cid, current)
    assert (retried["result"]["ok"], retried["result"]["rows_dup"]) == (1, 2)
    assert len(case_flows(database, cid)) == 4
    assert_incomplete(client, cid)


@pytest.mark.parametrize("new_first", [False, True], ids=["historical-cny-retained", "correct-currencies-retained"])
def test_v4_conflicted_history_format4_restores_to_clean_database_without_clearing_uncertainty(
        client, database, make_case, monkeypatch, tmp_path, new_first):
    cid, _payload, content, first, second = currency_history(
        client, database, make_case, monkeypatch, new_first=new_first)
    expected, expected_coverage = facts(database, cid), assert_incomplete(client, cid)
    reports = {view["table_summary"]["profile"]: source_records(client, cid, view["import_id"])
               for view in (first, second)}
    response = client.get(f"/api/cases/{cid}/export")
    assert response.status_code == 200
    package = response.content
    with database._lock:
        database._con.close()
        database._con = None
    with monkeypatch.context() as clean:
        directory = tmp_path / "clean-archive-v4"
        directory.mkdir()
        clean.setattr(database, "DATA_DIR", directory)
        clean.setattr(database, "DB_PATH", directory / "fund.duckdb")
        try:
            assert database.q_raw("select * from flows") == []
            response = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package)})
            assert response.status_code == 200, response.text
            restored = response.json()["case_id"]
            assert facts(database, restored) == expected
            assert assert_incomplete(client, restored) == expected_coverage
            assert database.q_raw("select * from import_sessions") == []
            for source in database.q_raw("select source_id,parser_version from import_sources where case_id=?", [restored]):
                records = source_records(client, restored, source["source_id"])
                without_flow_ids = lambda items: [{key: value for key, value in item.items() if key != "flow_id"} for item in items]
                assert without_flow_ids(records) == without_flow_ids(reports[source["parser_version"]])
            repeated = preview(client, restored, content, "repeated.zip")
            result = commit_view(client, restored, repeated)
            assert result["result"]["ok"] == 0
            assert result["result"]["rows_dup"] == (3 if new_first else 2)
            assert facts(database, restored) == expected
            assert_incomplete(client, restored)
        finally:
            with database._lock:
                if database._con is not None:
                    database._con.close()
                    database._con = None


def witness_summary_rows():
    from test_table_cancellations import round_trip

    rows = round_trip()
    for row in rows:
        row["凭证号"] = row["交易流水号"]
        row["摘要说明"] = "Synthetic pair summary" if row["交易金额"] != "-10.00" else "冲正"
        row["商户名称"] = row["摘要说明"]
    rows[0]["商户名称"] = "Synthetic historical witness summary"
    return rows


def summary_history(client, cid, payload):
    view = preview(client, cid, payload, "historical.csv")
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={
        "mode": view["mode"], "mapping": {**view["mapping"], "summary": "商户名称"}})
    assert response.status_code == 200, response.text
    view = response.json()
    return view, commit_view(client, cid, view)


def assert_clean_cancellation_package_restore(client, database, cid, monkeypatch, tmp_path):
    expected, expected_coverage = facts(database, cid), coverage(client, cid)

    def reports(case_id):
        result = []
        for source in database.q_raw("select source_id,filename,sha256,parser_version from import_sources where case_id=?", [case_id]):
            records = source_records(client, case_id, source["source_id"])
            result.append({"filename": source["filename"], "sha256": source["sha256"],
                           "profile": source["parser_version"], "records": [
                               {key: value for key, value in record.items() if key != "flow_id"} for record in records]})
        return sorted(result, key=lambda item: json.dumps(item, sort_keys=True))

    expected_reports = reports(cid)
    response = client.get(f"/api/cases/{cid}/export")
    assert response.status_code == 200, response.text
    package = response.content
    with database._lock:
        database._con.close()
        database._con = None
    with monkeypatch.context() as clean:
        directory = tmp_path / "clean-witness-package"
        directory.mkdir()
        clean.setattr(database, "DATA_DIR", directory)
        clean.setattr(database, "DB_PATH", directory / "fund.duckdb")
        try:
            assert database.q_raw("select * from flows") == []
            response = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package)})
            assert response.status_code == 200, response.text
            restored = response.json()["case_id"]
            assert facts(database, restored) == expected
            assert coverage(client, restored) == expected_coverage
            assert reports(restored) == expected_reports
            assert database.q_raw("select * from import_sessions") == []
        finally:
            with database._lock:
                if database._con is not None:
                    database._con.close()
                    database._con = None


def test_v4_history_conflicting_witness_revokes_its_pair_without_changing_old_facts(
        client, database, make_case, monkeypatch, tmp_path):
    from test_table_cancellations import source

    cid = make_case("Synthetic v4 history-conflicting cancellation witness")
    payload, content = source(witness_summary_rows(), path="table.csv")
    old, first = summary_history(client, cid, payload)
    assert first["result"]["ok"] == 2
    before, frozen = case_flows(database, cid), source_snapshot(database, cid, old["import_id"])
    current = preview(client, cid, content, "current.zip")
    assert current["table_summary"]["profile"] == NEW_PROFILE
    # A conflicted witness cannot remain the trusted root of a cancelled pair.
    # The public commit must finish with retained uncertainty rather than an
    # invalid evidence graph failing the strict manifest boundary.
    job = commit_view(client, cid, current)
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (0, 0)
    for summary in (current["table_summary"], job["result"]["table_summary"]):
        assert (summary["accepted"], summary["cancelled"], summary["unresolved"]) == (0, 0, 3)
    records = source_records(client, cid, current["import_id"])
    assert_history_conflicts(records, [0])
    assert [record["reason_code"] for record in records] == [
        "conflicting_field", "cancellation_historical_conflict", "cancellation_historical_conflict"]
    assert all(record["disposition"] == "unresolved" and record["flow_id"] is None for record in records)
    assert case_flows(database, cid) == before
    assert source_snapshot(database, cid, old["import_id"]) == frozen
    assert receipt(client, cid, old["import_id"]) == first["result"]
    assert database.q_raw("select count(*) n,sum(amount) amount from effective_flows where case_id=?", [cid]) == [
        {"n": 2, "amount": Decimal("11.00")}]
    assert assert_incomplete(client, cid)["unresolved_records"] == 3
    assert_clean_cancellation_package_restore(client, database, cid, monkeypatch, tmp_path)


def test_v4_history_conflicting_root_revokes_only_its_chain_and_keeps_independent_pair(
        client, database, make_case, monkeypatch, tmp_path):
    from test_table_cancellations import round_trip, source, transaction

    rows = round_trip()
    rows += [
        transaction(**{"交易时间": "2026-01-01 10:03:00", "交易金额": "20.00", "交易余额": "80.00", "交易流水号": "SYN-NEXT"}),
        transaction(**{"交易时间": "2026-01-01 10:04:00", "交易金额": "-20.00", "摘要说明": "撤销", "交易流水号": "SYN-NEXT"}),
    ]
    rows += [row | {"交易账号": "SYN-INDEPENDENT"} for row in round_trip()]
    for row in rows:
        row["凭证号"] = row["交易流水号"]
        if not row["交易金额"].startswith("-"):
            row["摘要说明"] = "Synthetic pair summary"
        row["商户名称"] = row["摘要说明"]
    rows[0]["商户名称"] = "Synthetic historical witness summary"
    payload, content = source(rows, path="table.csv")
    cid = make_case("Synthetic v4 selective cancellation chain revocation")
    old, first = summary_history(client, cid, payload)
    assert first["result"]["ok"] == 5
    before, frozen = case_flows(database, cid), source_snapshot(database, cid, old["import_id"])
    current = preview(client, cid, content, "current.zip")
    assert current["table_summary"]["profile"] == NEW_PROFILE
    job = commit_view(client, cid, current)
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (0, 1)
    for summary in (current["table_summary"], job["result"]["table_summary"]):
        assert (summary["accepted"], summary["cancelled"], summary["unresolved"]) == (1, 2, 5)
    records = source_records(client, cid, current["import_id"])
    assert_history_conflicts(records, [0])
    assert [record["reason_code"] for record in records] == [
        "conflicting_field", *(["cancellation_historical_conflict"] * 4),
        "transaction_valid", "matched_cancellation", "matched_cancellation"]
    assert all(record["disposition"] == "unresolved" and record["flow_id"] is None for record in records[:5])
    assert records[5]["disposition"] == "duplicate"
    assert [record["disposition"] for record in records[6:]] == ["cancelled", "cancelled"]
    assert all(record["details"]["cancellation_witness_record_no"] == "6" for record in records[6:])
    assert case_flows(database, cid) == before
    assert source_snapshot(database, cid, old["import_id"]) == frozen
    assert receipt(client, cid, old["import_id"]) == first["result"]
    assert database.q_raw("select count(*) n,sum(amount) amount from effective_flows where case_id=?", [cid]) == [
        {"n": 4, "amount": Decimal("32.00")}]
    assert assert_incomplete(client, cid)["unresolved_records"] == 5
    assert_clean_cancellation_package_restore(client, database, cid, monkeypatch, tmp_path)


def test_v4_nonwitness_observer_history_conflict_keeps_valid_pair_and_old_observer_fact(
        client, database, make_case, monkeypatch, tmp_path):
    from test_table_cancellations import round_trip, source, transaction

    rows = round_trip() + [transaction(**{
        "交易时间": "2026-01-01 10:03:00", "交易金额": "2.00", "交易余额": "98.00",
        "交易流水号": "SYN-OBSERVER", "凭证号": "SYN-OBSERVER"})]
    for row in rows:
        row["凭证号"] = row["交易流水号"]
        row["商户名称"] = row["交易流水号"]
    rows[3]["商户名称"] = "SYN-HISTORICAL-OBSERVER"
    payload, content = source(rows, path="table.csv")
    cid = make_case("Synthetic v4 nonwitness observer preservation")
    old = preview(client, cid, payload, "historical.csv")
    response = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview", json={
        "mode": old["mode"], "mapping": {**old["mapping"], "trade_no": "商户名称"}})
    assert response.status_code == 200, response.text
    old = response.json()
    first = commit_view(client, cid, old)
    assert first["result"]["ok"] == 3
    before, frozen = case_flows(database, cid), source_snapshot(database, cid, old["import_id"])
    current = preview(client, cid, content, "current.zip")
    assert current["table_summary"]["profile"] == NEW_PROFILE
    job = commit_view(client, cid, current)
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (0, 1)
    for summary in (current["table_summary"], job["result"]["table_summary"]):
        assert (summary["accepted"], summary["cancelled"], summary["unresolved"]) == (1, 2, 1)
    records = source_records(client, cid, current["import_id"])
    assert [record["disposition"] for record in records] == ["duplicate", "cancelled", "cancelled", "unresolved"]
    assert_history_conflicts(records, [3])
    assert [record["reason_code"] for record in records[1:3]] == ["matched_cancellation", "matched_cancellation"]
    assert all(record["details"]["cancellation_witness_record_no"] == "1" for record in records[1:3])
    assert case_flows(database, cid) == before
    assert source_snapshot(database, cid, old["import_id"]) == frozen
    assert receipt(client, cid, old["import_id"]) == first["result"]
    assert database.q_raw("select count(*) n,sum(amount) amount from effective_flows where case_id=?", [cid]) == [
        {"n": 2, "amount": Decimal("3.00")}]
    assert assert_incomplete(client, cid)["unresolved_records"] == 1
    assert_clean_cancellation_package_restore(client, database, cid, monkeypatch, tmp_path)


def test_old_ready_cancellation_profile_closes_pair_when_v4_history_blocks_its_witness(
        client, database, make_case, monkeypatch, tmp_path):
    from test_table_cancellations import source

    cid = make_case("Synthetic old ready witness dependency closure")
    payload, content = source(witness_summary_rows(), path="table.csv")
    legacy = frozen_preview(client, cid, content, monkeypatch, OLD_PROFILE)
    assert (legacy["table_summary"]["accepted"], legacy["table_summary"]["cancelled"]) == (1, 2)
    old, first = summary_history(client, cid, payload)
    assert first["result"]["ok"] == 2
    before, frozen_old = case_flows(database, cid), source_snapshot(database, cid, old["import_id"])
    current = preview(client, cid, content, "current.zip")
    current_job = commit_view(client, cid, current)
    assert (current_job["result"]["ok"], current_job["result"]["rows_dup"]) == (0, 0)
    assert_history_conflicts(source_records(client, cid, current["import_id"]), [0])
    frozen_current = source_snapshot(database, cid, current["import_id"])
    legacy = refresh(client, cid, legacy)
    assert legacy["table_summary"]["profile"] == OLD_PROFILE
    assert (legacy["table_summary"]["accepted"], legacy["table_summary"]["cancelled"],
            legacy["table_summary"]["unresolved"]) == (0, 0, 3)
    job = commit_view(client, cid, legacy)
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (0, 0)
    records = source_records(client, cid, legacy["import_id"])
    assert_history_conflicts(records, [0])
    assert [record["reason_code"] for record in records[1:]] == ["cancellation_historical_conflict"] * 2
    assert all(record["disposition"] == "unresolved" and record["flow_id"] is None for record in records)
    assert case_flows(database, cid) == before
    assert source_snapshot(database, cid, old["import_id"]) == frozen_old
    assert source_snapshot(database, cid, current["import_id"]) == frozen_current
    assert receipt(client, cid, old["import_id"]) == first["result"]
    assert assert_incomplete(client, cid)["unresolved_records"] == 3
    assert_clean_cancellation_package_restore(client, database, cid, monkeypatch, tmp_path)


@pytest.mark.parametrize("disposition", ["imported", "duplicate", "unresolved"], ids=[
    "imported-marker", "duplicate-marker", "unresolved-without-conflicting-field"])
def test_package_rejects_archive_history_marker_without_its_required_conflict_disposition_and_reason(
        client, database, make_case, tmp_path, disposition):
    from test_feedback_package import _archive, _members, _mutate

    cid = make_case("Synthetic invalid archive history package marker")
    payload = csv_data([
        ["date", "payer_account", "payee_account", "amount", "币种代码", "currency"],
        ["2026-02-01", "SYN-A", "SYN-B", "1.23", "CNY", "RMB"],
        ["2026-02-02", "SYN-C", "SYN-D", "4.56", "USD", "CNY"],
    ])
    content = archive_bytes([("bank/marker.csv", payload)], nested=True)
    view = preview(client, cid, content, "synthetic.zip")
    first = commit_view(client, cid, view)
    assert (first["result"]["ok"], first["result"]["rows_dup"]) == (1, 0)
    assert first["result"]["table_summary"]["unresolved"] == 1
    if disposition == "duplicate":
        view = preview(client, cid, content, "repeat.zip")
        repeated = commit_view(client, cid, view)
        assert (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 1)
    targets = database.q_raw(
        "select record_no,reason_code,details_json from import_records where case_id=? and source_id=? and disposition=?",
        [cid, view["import_id"], disposition])
    assert len(targets) == 1
    target = targets[0]
    details = json.loads(target["details_json"])
    assert "conflicting_field" not in details.get("validation_issues", "").split(",")
    assert target["reason_code"] == ("invalid_currency" if disposition == "unresolved" else "transaction_valid")
    assert details.get("counterparty_basis") != table_import.ARCHIVE_HISTORY_CONFLICT_BASIS

    response = client.get(f"/api/cases/{cid}/export")
    assert response.status_code == 200
    members = _members(response.content)
    assert json.loads(members["meta.json"])["format"] == 6
    # The unchanged journal is valid: only the added marker makes the copy invalid.
    restored = client.post("/api/cases/import", files={"file": ("valid-control.fundcase", response.content)})
    assert restored.status_code == 200, restored.text
    restored_cid = restored.json()["case_id"]
    assert facts(database, restored_cid) == facts(database, cid)
    assert coverage(client, restored_cid) == coverage(client, cid)

    details["counterparty_basis"] = table_import.ARCHIVE_HISTORY_CONFLICT_BASIS
    serialized = json.dumps(details, ensure_ascii=False).replace("'", "''")
    _mutate(members, "import_records",
            f"update entries set details_json='{serialized}' where source_id='{view['import_id']}' and record_no={target['record_no']}",
            tmp_path)
    before, cases_before = persisted(database), database.q_raw("select * from cases order by id")
    rejected = client.post("/api/cases/import", files={"file": ("invalid-marker.fundcase", _archive(members))})
    assert rejected.status_code == 400, rejected.text
    assert persisted(database) == before
    assert database.q_raw("select * from cases order by id") == cases_before


@pytest.mark.parametrize("current_profile", ["funds-trace-table-v6", "funds-trace-table-v7"])
def test_unrelated_zero_amount_v4_source_does_not_make_superseded_v5_history_sticky(
        client, database, make_case, monkeypatch, current_profile):
    cid = make_case("Synthetic unrelated archive marker isolation")
    zero = csv_data([
        ["date", "payer_account", "payee_account", "amount", "currency"],
        ["2026-12-01", "SYN-ZERO-A", "SYN-ZERO-B", "0.00", "CNY"],
    ])
    archive = preview(client, cid, archive_bytes([("bank/zero.csv", zero)], nested=True), "zero.zip")
    assert archive["table_summary"]["profile"] == NEW_PROFILE
    zero_job = commit_view(client, cid, archive)
    assert (zero_job["result"]["ok"], zero_job["result"]["table_summary"]["non_monetary"]) == (0, 1)
    frozen_zero = source_snapshot(database, cid, archive["import_id"])
    payload = ("date,payer_account,payee_account,付款方账户名称,amount\n"
               "2026-01-01,SYN-A,SYN-B,Synthetic payer,1.01\n").encode()
    assert zero != payload
    old = mapped_v5_ready(client, cid, payload, monkeypatch)
    with monkeypatch.context() as current:
        current.setattr(imports, "TABLE_PROFILE", current_profile)
        newer = preview(client, cid, payload, "current.csv")
    assert newer["table_summary"]["profile"] == current_profile
    current_job = commit_view(client, cid, newer)
    assert current_job["result"]["ok"] == 1
    before = case_flows(database, cid)
    old = refresh(client, cid, old)
    assert old["table_summary"]["unresolved"] == 1
    old_job = commit_view(client, cid, old)
    assert (old_job["result"]["ok"], old_job["result"]["rows_dup"]) == (0, 0)
    record = source_records(client, cid, old["import_id"])[0]
    assert (record["disposition"], record["reason_code"], record["flow_id"]) == (
        "unresolved", "conflicting_field", None)
    assert record["details"]["counterparty_basis"] == table_import.HISTORY_CONFLICT_BASIS
    assert case_flows(database, cid) == before
    assert len(before) == 1 and before[0]["amount"] == Decimal("1.01")
    assert source_snapshot(database, cid, archive["import_id"]) == frozen_zero
    assert receipt(client, cid, archive["import_id"]) == zero_job["result"]
    assert receipt(client, cid, newer["import_id"]) == current_job["result"]
    value = coverage(client, cid)
    assert value["unresolved_records"] == 0
    assert value["financial_interpretation_complete"] is True
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == facts(database, cid)
    assert coverage(client, restored) == value
