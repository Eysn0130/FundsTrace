"""Public v17 currency and regional ambiguity lifecycles use synthetic sources."""
from datetime import datetime, timedelta
from decimal import Decimal
import io
import json
import zipfile

import pytest

from app import case_packages, table_import
from app.services import imports
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_contract_lifecycle import facts, source_records
from test_table_currency_region_v17 import FIRST, HEADERS, NEW_PROFILE, OLD_PROFILE, OVERLAP, SECOND
from test_table_mapping_coverage import coverage
from test_table_region_headers import payload


def repreview_unchanged(client, cid, view):
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={
        "mode": view["mode"], "mapping": view["mapping"],
    })
    assert response.status_code == 200, response.text
    refreshed = response.json()
    for field in ("mode", "mapping", "stats", "preview", "table_summary"):
        assert refreshed[field] == view[field], field
    return refreshed


def source_snapshot(database, cid, source_id):
    return {table: database.q_raw(
        f"select * from {table} where case_id=? and source_id=? order by all", [cid, source_id])
        for table in ("import_sources", "import_members", "import_records", "flow_sources")}


def receipt(client, cid, source_id):
    response = client.get(f"/api/v1/cases/{cid}/imports/{source_id}")
    assert response.status_code == 200, response.text
    assert response.json()["stage"] == "done"
    return response.json()["result"]


def money_facts(rows):
    return [(row["trade_time"], row["payer_account"], row["payee_account"], row["amount"], row["currency"])
            for row in rows]


def assert_source_coordinate(client, cid, source_id, record):
    response = client.get(f"/api/v1/cases/{cid}/flows/{record['flow_id']}/sources")
    assert response.status_code == 200, response.text
    source = next(item for item in response.json()["items"] if item["source_id"] == source_id)
    assert (source["source_row"], source["member_row"], source["member_path"], source["row_kind"],
            source["parser_version"]) == (
        record["record_no"], record["source_row"], record["member_path"], "table_record", NEW_PROFILE)


def restore_and_replay(client, database, cid, data, view, records, expected_duplicates):
    before, before_coverage = facts(database, cid), coverage(client, cid)
    package = case_packages.export_package(cid)[0]
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert json.loads(archive.read("meta.json"))["format"] == 4
    restored = case_packages.import_package(package)["case_id"]
    assert facts(database, restored) == before
    assert coverage(client, restored) == before_coverage
    source = database.q_raw(
        "select source_id,parser_version,mapping_json from import_sources where case_id=? and filename=?",
        [restored, view["filename"]])[0]
    assert source["parser_version"] == NEW_PROFILE
    assert json.loads(source["mapping_json"]) == view["mapping"]
    restored_records = source_records(client, restored, source["source_id"])
    assert [{key: value for key, value in record.items() if key != "flow_id"}
            for record in restored_records] == [
        {key: value for key, value in record.items() if key != "flow_id"} for record in records]
    repeated = complete(client, restored, preview(client, restored, data, view["filename"]))
    assert repeated["stage"] == "done"
    assert (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, expected_duplicates)
    assert facts(database, restored) == before
    assert coverage(client, restored) == before_coverage


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_currency_code_full_validation_repreview_exact_facts_sources_and_format4(
        client, database, make_case, suffix):
    cid = make_case("Synthetic v17 currency lifecycle")
    headers = ["交易时间", "本方账号", "对方账号", "收支标志", "交易金额(分)", "币种代码", "currency"]
    rows = [
        ["2026-01-01 10:00:00.123456", "SYN-OWN", "SYN-USD", "支出", "123", "USD", "USD"],
        ["2026-01-01 11:00:00.654321", "SYN-OWN", "SYN-CNY", "收入", "203", "CNY", "人民币"],
    ]
    expected = [
        (datetime(2026, 1, 1, 10, 0, 0, 123456), "SYN-OWN", "SYN-USD", Decimal("1.23"), "USD"),
        (datetime(2026, 1, 1, 11, 0, 0, 654321), "SYN-CNY", "SYN-OWN", Decimal("2.03"), "CNY"),
    ]
    for index in range(126):
        timestamp = datetime(2026, 1, 2, 10) + timedelta(seconds=index)
        counterparty = f"SYN-CENT-{index:03d}"
        rows.append([timestamp.isoformat(" "), "SYN-OWN", counterparty, "支出", "1", "人民币", "CNY"])
        expected.append((timestamp, "SYN-OWN", counterparty, Decimal("0.01"), "CNY"))
    # A conflicting second currency candidate beyond the preview/sample limit
    # must invalidate this last record, even though the first candidate is valid.
    rows.append(["2026-01-03 10:00:00", "SYN-OWN", "SYN-REJECTED", "支出", "999", "USD", "CNY"])
    data = payload([headers, *rows], suffix)
    view = preview(client, cid, data, "synthetic-currency" + suffix)
    assert imports.TABLE_PROFILE == table_import.PROFILE == NEW_PROFILE
    assert view["automatic"] and view["mode"] == "B"
    summary = view["table_summary"]
    assert summary["profile"] == NEW_PROFILE
    assert (summary["record_total"], summary["accepted"], summary["unresolved"]) == (129, 128, 1)
    assert summary["currency_totals"] == {"CNY": "3.29", "USD": "1.23"}
    assert summary["reason_counts"] == {"transaction_valid": 128, "invalid_currency": 1}
    assert [(row["payer_account"], row["payee_account"], row["amount"], row["currency"], row["source_row"])
            for row in view["preview"][:2]] == [
        ("SYN-OWN", "SYN-USD", "1.23", "USD", 2), ("SYN-CNY", "SYN-OWN", "2.03", "CNY", 3)]
    assert {(entry["column"], entry["column_index"], entry["candidate_count"], entry["header_row"])
            for entry in summary["mapping_evidence"] if entry["field"] == "currency"} == {
        ("币种代码", "6", "2", "1"), ("currency", "7", "2", "1")}
    assert [(entry["column_index"], entry["unit_factor"]) for entry in summary["mapping_evidence"]
            if entry["field"] == "amount"] == [("5", "0.01")]
    view = repreview_unchanged(client, cid, view)
    job = complete(client, cid, view)
    assert job["stage"] == "done"
    assert (job["done"], job["total"], job["result"]["ok"], job["result"]["rows_dup"]) == (129, 129, 128, 0)
    assert money_facts(facts(database, cid)) == expected
    records = source_records(client, cid, view["import_id"])
    assert [record["record_no"] for record in records] == list(range(1, 130))
    assert [record["source_row"] for record in records] == list(range(2, 131))
    assert [record["disposition"] for record in records] == ["imported"] * 128 + ["unresolved"]
    assert records[-1]["reason_code"] == "invalid_currency" and records[-1]["flow_id"] is None
    assert "invalid_currency" in records[-1]["details"]["validation_issues"].split(",")
    assert all("currency_basis" not in record["details"] for record in records[:-1])
    for record in (records[0], records[1], records[-2]):
        assert_source_coordinate(client, cid, view["import_id"], record)
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == data
    before, frozen = facts(database, cid), source_snapshot(database, cid, view["import_id"])
    before_coverage = coverage(client, cid)
    repeated = complete(client, cid, preview(client, cid, data, "synthetic-currency-repeat" + suffix))
    assert repeated["stage"] == "done"
    assert (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 128)
    assert facts(database, cid) == before
    assert source_snapshot(database, cid, view["import_id"]) == frozen
    assert receipt(client, cid, view["import_id"]) == job["result"]
    assert coverage(client, cid) == before_coverage
    restore_and_replay(client, database, cid, data, view, records, 128)


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("old_first", [True, False], ids=["v16-then-v17", "v17-then-v16"])
def test_currency_ready_versions_preserve_first_facts_sources_and_receipt_without_double_counting(
        client, database, make_case, monkeypatch, suffix, old_first):
    cid = make_case("Synthetic currency versioned ready sessions")
    data = payload([
        ["交易时间", "本方账号", "对方账号", "收支标志", "交易金额", "币种代码"],
        ["2026-02-01 10:00:00", "SYN-OWN", "SYN-USD", "支出", "1.23", "USD"],
        ["2026-02-01 11:00:00", "SYN-OWN", "SYN-CNY", "收入", "2.03", "CNY"],
    ], suffix)
    with monkeypatch.context() as historical:
        historical.setattr(imports, "TABLE_PROFILE", OLD_PROFILE)
        old = preview(client, cid, data, "synthetic-v16" + suffix)
    current = preview(client, cid, data, "synthetic-v17" + suffix)
    assert old["table_summary"]["profile"] == OLD_PROFILE
    assert current["table_summary"]["profile"] == NEW_PROFILE
    assert old["table_summary"]["currency_totals"] == {"CNY": "3.26"}
    assert current["table_summary"]["currency_totals"] == {"CNY": "2.03", "USD": "1.23"}
    assert {item["import_id"]: item["stage"] for item in client.get(
        f"/api/v1/cases/{cid}/imports").json()["items"]} == {
            old["import_id"]: "ready", current["import_id"]: "ready"}
    # Both requests are already ready before either commit, and each repreview
    # must retain its pinned interpretation after v17 becomes the upload default.
    old, current = repreview_unchanged(client, cid, old), repreview_unchanged(client, cid, current)
    first, second = (old, current) if old_first else (current, old)
    first_job = complete(client, cid, first)
    assert first_job["stage"] == "done" and (first_job["result"]["ok"], first_job["result"]["rows_dup"]) == (2, 0)
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    assert money_facts(facts(database, cid)) == [
        (datetime(2026, 2, 1, 10), "SYN-OWN", "SYN-USD", Decimal("1.23"), "CNY" if old_first else "USD"),
        (datetime(2026, 2, 1, 11), "SYN-CNY", "SYN-OWN", Decimal("2.03"), "CNY"),
    ]
    frozen = source_snapshot(database, cid, first["import_id"])
    first_report = source_records(client, cid, first["import_id"])
    second_job = complete(client, cid, second)
    assert second_job["stage"] == "done"
    assert (second_job["result"]["ok"], second_job["result"]["rows_dup"]) == (0, 1)
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before
    assert source_snapshot(database, cid, first["import_id"]) == frozen
    assert source_records(client, cid, first["import_id"]) == first_report
    assert receipt(client, cid, first["import_id"]) == first_job["result"]
    second_report = source_records(client, cid, second["import_id"])
    assert [record["source_row"] for record in second_report] == [2, 3]
    assert [record["disposition"] for record in second_report] == ["unresolved", "duplicate"]
    assert second_report[0]["reason_code"] == "conflicting_field" and second_report[0]["flow_id"] is None
    assert second_report[0]["details"]["counterparty_basis"] == table_import.HISTORY_CONFLICT_BASIS
    assert second_report[1]["flow_id"] == first_report[1]["flow_id"]
    assert coverage(client, cid)["unresolved_records"] == 1
    repeated = complete(client, cid, preview(client, cid, data, "synthetic-version-repeat" + suffix))
    assert repeated["stage"] == "done"
    assert (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 1 if old_first else 2)
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before
    assert source_snapshot(database, cid, first["import_id"]) == frozen
    assert receipt(client, cid, first["import_id"]) == first_job["result"]


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_ambiguous_region_journal_blocks_fabrication_then_explicit_header_recovers(
        client, database, make_case, suffix):
    cid = make_case("Synthetic bounded region ambiguity")
    clear_header = ["date", "payee_account", "payer_account", "extra", "amount", "币种代码"]
    recovered_row = ["2026-01-05", "SYN-RECIPIENT", "SYN-SENDER", "uninterpreted", "3.45", "USD"]
    data = payload([HEADERS, FIRST, OVERLAP, SECOND, clear_header, recovered_row], suffix)
    view = preview(client, cid, data, "synthetic-ambiguous-region" + suffix)
    summary = view["table_summary"]
    assert view["automatic"] and summary["profile"] == NEW_PROFILE
    assert (summary["record_total"], summary["accepted"], summary["unresolved"], summary["non_transaction"]) == (5, 2, 2, 1)
    assert summary["currency_totals"] == {"CNY": "1.23", "USD": "3.45"}
    assert summary["reason_counts"] == {"transaction_valid": 2, "ambiguous_region_header": 2, "repeated_header": 1}
    assert summary["documents"][0]["header_rows"] == [1, 5]
    assert {entry["header_row"] for entry in summary["mapping_evidence"]} == {"1", "5"}
    assert {(entry["field"], entry["column_index"]) for entry in summary["mapping_evidence"]
            if entry["header_row"] == "5"} == {
        ("trade_time", "1"), ("payee_account", "2"), ("payer_account", "3"), ("amount", "5"), ("currency", "6")}
    view = repreview_unchanged(client, cid, view)
    job = complete(client, cid, view)
    assert job["stage"] == "done"
    assert (job["done"], job["total"], job["result"]["ok"]) == (5, 5, 2)
    expected = [
        (datetime(2026, 1, 1), "SYN-A", "SYN-B", Decimal("1.23"), "CNY"),
        (datetime(2026, 1, 5), "SYN-SENDER", "SYN-RECIPIENT", Decimal("3.45"), "USD"),
    ]
    assert money_facts(facts(database, cid)) == expected
    records = source_records(client, cid, view["import_id"])
    assert [record["record_no"] for record in records] == [1, 2, 3, 4, 5]
    assert [record["source_row"] for record in records] == [2, 3, 4, 5, 6]
    assert [record["disposition"] for record in records] == ["imported", "unresolved", "unresolved", "non_transaction", "imported"]
    assert [record["reason_code"] for record in records] == [
        "transaction_valid", "ambiguous_region_header", "ambiguous_region_header", "repeated_header", "transaction_valid"]
    assert all(record["flow_id"] is None and record["details"]["financial_effect"] == "undetermined"
               and record["details"]["validation_issues"] == "ambiguous_region_header" for record in records[1:3])
    for record in (records[0], records[-1]):
        assert_source_coordinate(client, cid, view["import_id"], record)
    # The two unambiguous segments preserve their independently imported facts;
    # the overlapping middle has no asserted standalone interpretation here.
    separate = []
    for index, segment in enumerate(([HEADERS, FIRST], [clear_header, recovered_row])):
        segment_case = make_case(f"Synthetic explicit segment {index}")
        segment_job = complete(client, segment_case, preview(
            client, segment_case, payload(segment, suffix), f"synthetic-clear-{index}" + suffix))
        assert segment_job["stage"] == "done" and segment_job["result"]["ok"] == 1
        separate.extend(money_facts(facts(database, segment_case)))
    assert separate == expected
    before, frozen = facts(database, cid), source_snapshot(database, cid, view["import_id"])
    repeated = complete(client, cid, preview(client, cid, data, "synthetic-region-repeat" + suffix))
    assert repeated["stage"] == "done" and (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 2)
    assert facts(database, cid) == before
    assert source_snapshot(database, cid, view["import_id"]) == frozen
    assert receipt(client, cid, view["import_id"]) == job["result"]
    assert coverage(client, cid)["unresolved_records"] == 2
    restore_and_replay(client, database, cid, data, view, records, 2)


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_header_words_only_in_extra_columns_keep_ordinary_transactions(
        client, database, make_case, suffix):
    cid = make_case("Synthetic legitimate extra header words")
    data = payload([
        ["date", "payer_account", "payee_account", "amount", "extra1", "extra2", "extra3", "extra4"],
        ["2026-03-01", "SYN-A", "SYN-B", "1.23", "date", "payer_account", "payee_account", "amount"],
        ["2026-03-02", "SYN-C", "SYN-D", "2.34", "", "", "", ""],
    ], suffix)
    view = preview(client, cid, data, "synthetic-extra-words" + suffix)
    summary = view["table_summary"]
    assert (summary["accepted"], summary["unresolved"]) == (2, 0)
    assert summary["documents"][0]["header_rows"] == [1]
    assert summary["reason_counts"] == {"transaction_valid": 2}
    job = complete(client, cid, repreview_unchanged(client, cid, view))
    assert job["stage"] == "done" and (job["result"]["ok"], job["result"]["rows_dup"]) == (2, 0)
    assert money_facts(facts(database, cid)) == [
        (datetime(2026, 3, 1), "SYN-A", "SYN-B", Decimal("1.23"), "CNY"),
        (datetime(2026, 3, 2), "SYN-C", "SYN-D", Decimal("2.34"), "CNY"),
    ]
    records = source_records(client, cid, view["import_id"])
    assert [record["source_row"] for record in records] == [2, 3]
    assert [record["disposition"] for record in records] == ["imported", "imported"]
