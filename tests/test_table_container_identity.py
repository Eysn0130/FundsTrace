"""Exact CSV bytes may change containers without changing their stored facts."""
import base64
import csv
from datetime import datetime
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from app import case_packages, feedback_import, importer, table_archive, table_import
from test_archive_profile_upgrade import COLUMNS, transaction
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_feedback_package import _archive, _members, _mutate
from test_table_contract_lifecycle import source_records
from test_table_currency_region_lifecycle import receipt, source_snapshot
from test_table_write_integrity import persisted


def csv_bytes(rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(COLUMNS)
    writer.writerows([[row.get(column, "") for column in COLUMNS] for row in rows])
    return output.getvalue().encode("gb18030")


def archive_bytes(members, *, nested=False):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in members:
            archive.writestr(path, data)
    if not nested:
        return result.getvalue()
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("synthetic-envelope.zip", result.getvalue())
    return outer.getvalue()


def ingest(client, cid, data, filename):
    view = preview(client, cid, data, filename)
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    return view, job


def case_flows(database, cid):
    return database.q_raw("select * from flows where case_id=? order by id", [cid])


@pytest.mark.parametrize("nested", [False, True], ids=["flat", "nested"])
def test_exact_csv_bytes_in_renamed_zip_member_reuse_standalone_flow_ids(
        client, database, make_case, nested):
    cid = make_case("Synthetic exact-byte container identity")
    payload = csv_bytes([
        transaction("SYN-ONE", "SYN-ONE", "123.45"),
        transaction("SYN-TWO", "SYN-TWO", "2.03", **{"交易时间": "2026-01-02 10:00:00"}),
        transaction("SYN-THREE", "SYN-THREE", "4.56", **{"交易时间": "2026-01-03 10:00:00", "交易币种": "USD"}),
    ])
    first, first_job = ingest(client, cid, payload, "standalone.csv")
    assert (first_job["result"]["ok"], first_job["result"]["rows_dup"]) == (3, 0)
    before = case_flows(database, cid)
    frozen = source_snapshot(database, cid, first["import_id"])
    content = archive_bytes([("bank/transactions-001.csv", payload)], nested=nested)
    second, second_job = ingest(client, cid, content, "feedback.zip")
    assert (second_job["result"]["ok"], second_job["result"]["rows_dup"]) == (0, 3)
    assert case_flows(database, cid) == before
    assert database.q_raw("select currency,sum(amount) amount from flows where case_id=? group by currency order by currency", [cid]) == [
        {"currency": "CNY", "amount": Decimal("125.48")}, {"currency": "USD", "amount": Decimal("4.56")}]
    assert source_snapshot(database, cid, first["import_id"]) == frozen
    assert receipt(client, cid, first["import_id"]) == first_job["result"]
    records = source_records(client, cid, second["import_id"])
    path = ("synthetic-envelope.zip/" if nested else "") + "bank/transactions-001.csv"
    digest = hashlib.sha256(payload).hexdigest()
    assert [record["member_path"] for record in records] == [path] * 3
    assert [record["source_row"] for record in records] == [2, 3, 4]
    assert [record["disposition"] for record in records] == ["duplicate"] * 3
    assert [record["record_key"] for record in records] == [table_import.record_key(path, digest, row) for row in (2, 3, 4)]
    assert {int(record["flow_id"]) for record in records} == {row["id"] for row in before}


@pytest.mark.parametrize("nested", [False, True], ids=["flat", "nested"])
@pytest.mark.parametrize("archive_first", [False, True], ids=["csv-first", "archive-first"])
def test_exact_bytes_container_orders_and_replays_do_not_readd_money(
        client, database, make_case, nested, archive_first):
    cid = make_case("Synthetic container order and repeat")
    payload = csv_bytes([transaction("SYN-SAME", "SYN-SAME", "1.23")])
    content = archive_bytes([("bank/reply.csv", payload)], nested=nested)
    materials = [(payload, "synthetic.csv"), (content, "synthetic.zip")]
    if archive_first:
        materials.reverse()
    first, first_job = ingest(client, cid, *materials[0])
    assert (first_job["result"]["ok"], first_job["result"]["rows_dup"]) == (1, 0)
    before, frozen = case_flows(database, cid), source_snapshot(database, cid, first["import_id"])
    second, second_job = ingest(client, cid, *materials[1])
    assert (second_job["result"]["ok"], second_job["result"]["rows_dup"]) == (0, 1)
    for data, filename in materials:
        _, repeated = ingest(client, cid, data, "repeat-" + filename)
        assert (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 1)
    assert case_flows(database, cid) == before
    assert source_snapshot(database, cid, first["import_id"]) == frozen
    assert receipt(client, cid, first["import_id"]) == first_job["result"]
    assert int(source_records(client, cid, second["import_id"])[0]["flow_id"]) == before[0]["id"]


def test_folded_csv_physical_rows_promote_to_two_archive_events_and_stay_replayable(
        client, database, make_case):
    cid = make_case("Synthetic folded standalone physical rows")
    row = transaction("SYN-TWICE", "SYN-TWICE", "1.23")
    payload = csv_bytes([row, dict(row)])
    first, initial = ingest(client, cid, payload, "standalone.csv")
    assert (initial["result"]["ok"], initial["result"]["rows_dup"]) == (1, 1)
    before = case_flows(database, cid)
    frozen = source_snapshot(database, cid, first["import_id"])
    content = archive_bytes([("bank/two-physical-rows.csv", payload)], nested=True)
    second, promoted = ingest(client, cid, content, "synthetic.zip")
    assert (promoted["result"]["ok"], promoted["result"]["rows_dup"]) == (1, 1)
    rows = case_flows(database, cid)
    assert len(rows) == 2 and rows[0] == before[0]
    assert sum(row["amount"] for row in rows) == Decimal("2.46")
    records = source_records(client, cid, second["import_id"])
    assert [record["source_row"] for record in records] == [2, 3]
    assert [record["disposition"] for record in records] == ["duplicate", "imported"]
    assert int(records[0]["flow_id"]) == before[0]["id"]
    assert len({record["flow_id"] for record in records}) == 2
    for data, filename in [(payload, "repeat.csv"), (content, "repeat.zip")]:
        _, repeated = ingest(client, cid, data, filename)
        assert (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 2)
    assert case_flows(database, cid) == rows
    assert source_snapshot(database, cid, first["import_id"]) == frozen
    assert receipt(client, cid, first["import_id"]) == initial["result"]


def test_two_archive_paths_with_identical_bytes_reuse_each_old_flow_at_most_once(
        client, database, make_case):
    cid = make_case("Synthetic same bytes in two archive paths")
    payload = csv_bytes([transaction("SYN-PATHS", "SYN-PATHS", "1.23")])
    first, _ = ingest(client, cid, payload, "standalone.csv")
    old_flow = case_flows(database, cid)[0]
    frozen = source_snapshot(database, cid, first["import_id"])
    content = archive_bytes([("bank/first.csv", payload), ("bank/second.csv", payload)])
    view, result = ingest(client, cid, content, "two-paths.zip")
    assert (result["result"]["ok"], result["result"]["rows_dup"]) == (1, 1)
    records = source_records(client, cid, view["import_id"])
    assert [record["member_path"] for record in records] == ["bank/first.csv", "bank/second.csv"]
    assert [record["disposition"] for record in records] == ["duplicate", "imported"]
    assert len({record["record_key"] for record in records}) == 2
    assert len({record["flow_id"] for record in records}) == 2
    assert int(records[0]["flow_id"]) == old_flow["id"]
    before = case_flows(database, cid)
    _, repeated = ingest(client, cid, content, "repeat.zip")
    assert (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 2)
    assert case_flows(database, cid) == before and before[0] == old_flow
    assert source_snapshot(database, cid, first["import_id"]) == frozen


def test_equal_facts_from_different_csv_bytes_do_not_gain_standalone_fallback(
        client, database, make_case):
    cid = make_case("Synthetic different bytes equal fields")
    payload = csv_bytes([transaction("SYN-EQUAL", "SYN-EQUAL", "1.23")])
    changed = payload.replace(b"\r\n", b"\n")
    assert changed != payload
    first, _ = ingest(client, cid, payload, "standalone.csv")
    old_flow = case_flows(database, cid)[0]
    content = archive_bytes([("bank/same-fields.csv", changed)], nested=True)
    view, result = ingest(client, cid, content, "different-bytes.zip")
    assert (result["result"]["ok"], result["result"]["rows_dup"]) == (1, 0)
    records = source_records(client, cid, view["import_id"])
    assert records[0]["disposition"] == "imported" and int(records[0]["flow_id"]) != old_flow["id"]
    assert len(case_flows(database, cid)) == 2
    assert case_flows(database, cid)[0] == old_flow
    assert source_records(client, cid, first["import_id"])[0]["flow_id"] == str(old_flow["id"])


def test_archive_to_archive_renamed_path_does_not_use_standalone_fallback(client, database, make_case):
    cid = make_case("Synthetic archive path occurrences")
    payload = csv_bytes([transaction("SYN-ARCHIVE", "SYN-ARCHIVE", "1.23")])
    first_data = archive_bytes([("bank/first.csv", payload)])
    first, _ = ingest(client, cid, first_data, "first.zip")
    before = case_flows(database, cid)
    second_data = archive_bytes([("bank/second.csv", payload)])
    second, result = ingest(client, cid, second_data, "second.zip")
    assert (result["result"]["ok"], result["result"]["rows_dup"]) == (1, 0)
    assert len(case_flows(database, cid)) == 2 and case_flows(database, cid)[0] == before[0]
    assert source_records(client, cid, first["import_id"])[0]["flow_id"] != source_records(
        client, cid, second["import_id"])[0]["flow_id"]
    for data in (first_data, second_data):
        assert ingest(client, cid, data, "repeat.zip")[1]["result"]["ok"] == 0


@pytest.mark.parametrize("filename", ["standalone.txt", "standalone.tsv", "standalone", "standalone.CSV", "standalone.TsV"])
def test_existing_csv_reader_extensions_supply_same_bytes_fallback(client, database, make_case, filename):
    cid = make_case("Synthetic existing CSV reader extensions")
    payload = csv_bytes([transaction("SYN-EXT", "SYN-EXT", "1.23")])
    _, first = ingest(client, cid, payload, filename)
    assert first["result"]["ok"] == 1
    before = case_flows(database, cid)
    content = archive_bytes([("bank/reply.csv", payload)])
    _, second = ingest(client, cid, content, "synthetic.zip")
    assert (second["result"]["ok"], second["result"]["rows_dup"]) == (0, 1)
    assert case_flows(database, cid) == before


def test_legacy_excel_labelled_format4_source_stays_readable_but_cannot_supply_csv_fallback(
        client, database, make_case, tmp_path):
    cid = make_case("Synthetic compatible old Excel source metadata")
    payload = csv_bytes([transaction("SYN-OLD-EXCEL", "SYN-OLD-EXCEL", "1.23")])
    ingest(client, cid, payload, "standalone.csv")
    package = _members(case_packages.export_package(cid)[0])
    # Historical format 4 permits this combination. Keeping it readable does
    # not authorize treating its source as an ordinary standalone CSV.
    _mutate(package, "import_sources", "update entries set filename='legacy.xlsx',sheet='Workbook'", tmp_path)
    response = client.post("/api/cases/import", files={"file": ("legacy.fundcase", _archive(package))})
    assert response.status_code == 200, response.text
    restored = response.json()["case_id"]
    before = case_flows(database, restored)
    assert len(before) == 1
    old_source = database.q_raw("select source_id,filename,sheet from import_sources where case_id=?", [restored])[0]
    assert (old_source["filename"], old_source["sheet"]) == ("legacy.xlsx", "Workbook")
    frozen = source_snapshot(database, restored, old_source["source_id"])
    content = archive_bytes([("bank/renamed.csv", payload)])
    _, job = ingest(client, restored, content, "synthetic.zip")
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (1, 0)
    assert len(case_flows(database, restored)) == 2
    assert case_flows(database, restored)[0] == before[0]
    assert source_snapshot(database, restored, old_source["source_id"]) == frozen


@pytest.mark.parametrize("archive_profile", ["funds-trace-table-archive-v3", "funds-trace-table-archive-v4"],
                         ids=["frozen-v3", "default-v4"])
def test_equal_sha_and_position_with_advanced_mapping_cannot_reuse_changed_full_facts(
        client, database, make_case, monkeypatch, archive_profile):
    from test_archive_profile_upgrade import frozen_preview
    from test_table_mapping_coverage import coverage

    cid = make_case("Synthetic advanced mapping standalone fallback")
    payload = csv_bytes([transaction("SYN-MAPPING", "SYN-MAPPING", "1.23", **{
        "摘要说明": "Synthetic source summary", "IP地址": "Synthetic alternate mapped summary"})])
    view = preview(client, cid, payload, "standalone.csv")
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={
        "mode": view["mode"], "mapping": {**view["mapping"], "summary": "IP地址"}})
    assert response.status_code == 200, response.text
    first = complete(client, cid, response.json())
    assert first["stage"] == "done" and first["result"]["ok"] == 1
    before = case_flows(database, cid)
    frozen = source_snapshot(database, cid, view["import_id"])
    assert before[0]["summary"] == "Synthetic alternate mapped summary"
    content = archive_bytes([("bank/original.csv", payload)], nested=True)
    current = (frozen_preview(client, cid, content, monkeypatch, archive_profile)
               if archive_profile == "funds-trace-table-archive-v3"
               else preview(client, cid, content, "synthetic.zip"))
    assert current["table_summary"]["profile"] == archive_profile
    second = complete(client, cid, current)
    assert second["stage"] == "done", second
    after = case_flows(database, cid)
    records = source_records(client, cid, current["import_id"])
    assert len(records) == 1 and records[0]["source_row"] == 2
    if archive_profile == "funds-trace-table-archive-v3":
        assert (second["result"]["ok"], second["result"]["rows_dup"]) == (1, 0)
        assert len(after) == 2 and after[0] == before[0]
        assert after[1]["summary"] == "Synthetic source summary"
        assert int(records[0]["flow_id"]) != before[0]["id"]
    else:
        assert (second["result"]["ok"], second["result"]["rows_dup"]) == (0, 0)
        assert after == before
        assert (records[0]["disposition"], records[0]["reason_code"], records[0]["flow_id"]) == (
            "unresolved", "conflicting_field", None)
        assert records[0]["details"]["counterparty_basis"] == table_import.ARCHIVE_HISTORY_CONFLICT_BASIS
        assert records[0]["details"]["financial_effect"] == "undetermined"
        assert "conflicting_field" in records[0]["details"]["validation_issues"].split(",")
        gap = coverage(client, cid)
        assert gap["unresolved_records"] > 0 and gap["financial_interpretation_complete"] is False
    assert source_snapshot(database, cid, view["import_id"]) == frozen
    assert receipt(client, cid, view["import_id"]) == first["result"]


@pytest.mark.parametrize("archive_profile", ["funds-trace-table-archive-v3", "funds-trace-table-archive-v4"],
                         ids=["frozen-v3", "default-v4"])
@pytest.mark.parametrize("field,value", [
    ("trade_no", "SYN-CHANGED"), ("trade_time", datetime(2026, 1, 1, 10, 0, 1)),
    ("payer_name", "Synthetic changed payer"), ("payer_account", "SYN-CHANGED-PAYER"),
    ("payer_bank", "Synthetic changed payer bank"), ("payee_name", "Synthetic changed payee"),
    ("payee_account", "SYN-CHANGED-PAYEE"), ("payee_bank", "Synthetic changed payee bank"),
    ("amount", "9.99"), ("balance", "99.99"), ("currency", "USD"),
    ("summary", "Synthetic changed summary"), ("channel", "SYN-CHANGED-CHANNEL"),
    ("self_account", "SYN-THIRD-PARTY"), ("self_name", "Synthetic changed owner"),
])
def test_standalone_fallback_compares_every_stored_fact_and_both_self_identity_fields(
        client, database, make_case, monkeypatch, field, value, archive_profile):
    from test_archive_profile_upgrade import frozen_preview
    from test_table_mapping_coverage import coverage

    cid = make_case("Synthetic controlled historical fact boundary")
    payload = csv_bytes([transaction("SYN-FULL-FACTS", "SYN-FULL-FACTS", "1.23")])
    first, initial = ingest(client, cid, payload, "standalone.csv")
    old_id = case_flows(database, cid)[0]["id"]
    # Controlled historical storage isolates the fallback comparison. The
    # journal identity/SHA stays untouched; this is not a claimed bank record.
    database.x(f"update flows set {field}=? where case_id=? and id=?", [value, cid, old_id])
    before = case_flows(database, cid)
    frozen = source_snapshot(database, cid, first["import_id"])
    content = archive_bytes([("bank/full-facts.csv", payload)])
    view = (frozen_preview(client, cid, content, monkeypatch, archive_profile)
            if archive_profile == "funds-trace-table-archive-v3"
            else preview(client, cid, content, "synthetic.zip"))
    assert view["table_summary"]["profile"] == archive_profile
    second = complete(client, cid, view)
    assert second["stage"] == "done", second
    after = case_flows(database, cid)
    records = source_records(client, cid, view["import_id"])
    assert len(records) == 1 and records[0]["source_row"] == 2
    if archive_profile == "funds-trace-table-archive-v3":
        assert (second["result"]["ok"], second["result"]["rows_dup"]) == (1, 0)
        assert len(after) == 2 and after[0] == before[0]
        assert int(records[0]["flow_id"]) != old_id
    else:
        assert (second["result"]["ok"], second["result"]["rows_dup"]) == (0, 0)
        assert after == before
        assert (records[0]["disposition"], records[0]["reason_code"], records[0]["flow_id"]) == (
            "unresolved", "conflicting_field", None)
        assert records[0]["details"]["counterparty_basis"] == table_import.ARCHIVE_HISTORY_CONFLICT_BASIS
        assert records[0]["details"]["financial_effect"] == "undetermined"
        assert "conflicting_field" in records[0]["details"]["validation_issues"].split(",")
        gap = coverage(client, cid)
        assert gap["unresolved_records"] > 0 and gap["financial_interpretation_complete"] is False
    assert source_snapshot(database, cid, first["import_id"]) == frozen
    assert receipt(client, cid, first["import_id"]) == initial["result"]


def test_exact_archive_identity_reserves_its_old_flow_before_earlier_fallback_candidate(
        client, database, make_case):
    cid = make_case("Synthetic exact identity precedes fallback")
    payload = csv_bytes([transaction("SYN-PRIORITY", "SYN-PRIORITY", "1.23")])
    _, _ = ingest(client, cid, payload, "standalone.csv")
    original = case_flows(database, cid)[0]
    exact_content = archive_bytes([("bank/exact.csv", payload)])
    exact, old_job = ingest(client, cid, exact_content, "exact.zip")
    assert (old_job["result"]["ok"], old_job["result"]["rows_dup"]) == (0, 1)
    frozen = source_snapshot(database, cid, exact["import_id"])
    # The first member has only the new standalone identity. The later member
    # already owns the exact physical identity and must keep its old flow ID.
    combined = archive_bytes([("bank/new-first.csv", payload), ("bank/exact.csv", payload)])
    view, current = ingest(client, cid, combined, "combined.zip")
    assert (current["result"]["ok"], current["result"]["rows_dup"]) == (1, 1)
    records = source_records(client, cid, view["import_id"])
    assert [record["member_path"] for record in records] == ["bank/new-first.csv", "bank/exact.csv"]
    assert [record["disposition"] for record in records] == ["imported", "duplicate"]
    assert int(records[1]["flow_id"]) == original["id"]
    assert int(records[0]["flow_id"]) != original["id"]
    assert source_snapshot(database, cid, exact["import_id"]) == frozen


def test_standalone_fallback_allocates_shared_old_flow_in_current_member_order(
        client, database, make_case):
    cid = make_case("Synthetic fallback current source order")
    payload_a = csv_bytes([transaction("SYN-ORDER", "SYN-ORDER", "1.23")])
    payload_b = payload_a.replace(b"\r\n", b"\n")
    assert payload_a != payload_b
    source_a, first = ingest(client, cid, payload_a, "a.csv")
    source_b, second = ingest(client, cid, payload_b, "b.csv")
    assert (first["result"]["ok"], second["result"]["ok"], second["result"]["rows_dup"]) == (1, 0, 1)
    old = case_flows(database, cid)[0]
    frozen_a = source_snapshot(database, cid, source_a["import_id"])
    frozen_b = source_snapshot(database, cid, source_b["import_id"])
    # Historical sources encountered A before B. Current physical occurrences
    # encounter B before A and must get the single available flow in that order.
    content = archive_bytes([("bank/B.csv", payload_b), ("bank/A.csv", payload_a)])
    view, job = ingest(client, cid, content, "ordered.zip")
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (1, 1)
    records = source_records(client, cid, view["import_id"])
    assert [record["member_path"] for record in records] == ["bank/B.csv", "bank/A.csv"]
    assert [record["disposition"] for record in records] == ["duplicate", "imported"]
    assert int(records[0]["flow_id"]) == old["id"]
    assert int(records[1]["flow_id"]) != old["id"]
    assert case_flows(database, cid)[0] == old
    assert source_snapshot(database, cid, source_a["import_id"]) == frozen_a
    assert source_snapshot(database, cid, source_b["import_id"]) == frozen_b


def test_standalone_identity_is_case_bound_and_never_links_to_another_case(client, database, make_case):
    first_case, other = make_case("Synthetic identity origin"), make_case("Synthetic isolated envelope")
    payload = csv_bytes([transaction("SYN-CASE", "SYN-CASE", "1.23")])
    first, _ = ingest(client, first_case, payload, "standalone.csv")
    before = case_flows(database, first_case)
    content = archive_bytes([("bank/reply.csv", payload)])
    second, result = ingest(client, other, content, "synthetic.zip")
    assert (result["result"]["ok"], result["result"]["rows_dup"]) == (1, 0)
    assert case_flows(database, first_case) == before
    record = source_records(client, other, second["import_id"])[0]
    assert int(record["flow_id"]) != before[0]["id"]
    assert client.get(f"/api/v1/cases/{other}/sources/{first['import_id']}/report").status_code == 404


def test_container_fallback_and_new_occurrence_roll_back_together_after_late_journal_failure(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic container fallback transaction")
    payload = csv_bytes([transaction("SYN-ROLLBACK", "SYN-ROLLBACK", "1.23")])
    first, first_job = ingest(client, cid, payload, "standalone.csv")
    before = persisted(database)
    content = archive_bytes([("bank/first.csv", payload), ("bank/second.csv", payload)])
    view = preview(client, cid, content, "synthetic.zip")
    write = database.insert_dataframe

    def fail_after_journal(table, frame, columns):
        result = write(table, frame, columns)
        if table == "import_records":
            raise RuntimeError("Synthetic D01 late journal rollback")
        return result

    with monkeypatch.context() as failure:
        failure.setattr(database, "insert_dataframe", fail_after_journal)
        failed = complete(client, cid, view)
    assert failed["stage"] == "error" and failed["result"] is None
    assert persisted(database) == before
    assert receipt(client, cid, first["import_id"]) == first_job["result"]
    retried = complete(client, cid, view)
    assert retried["stage"] == "done" and (retried["result"]["ok"], retried["result"]["rows_dup"]) == (1, 1)
    assert len(case_flows(database, cid)) == 2


def low_level_material(kind):
    """Use real parsers to isolate the optional commit argument's boundary."""
    payload = csv_bytes([
        transaction("SYN-LOW-A", "SYN-LOW-A", "1.23"),
        transaction("SYN-LOW-B", "SYN-LOW-B", "2.03", **{"交易时间": "2026-01-02 10:00:00"}),
    ])
    if kind == "feedback":
        from test_feedback_import_api import archive, transaction as feedback_transaction

        raw = archive([feedback_transaction()])
        plan = feedback_import.parse(io.BytesIO(raw))
        mode, mapping, filename, row_kind = "B", feedback_import.MAPPING, "legacy-feedback.zip", "archive_record"
    elif kind == "ordinary":
        raw, filename = payload, "ordinary.csv"
        plan = table_import.parse(raw, suffix=".csv")
        mode, mapping, row_kind = plan["mode"], plan["mapping"], table_import.ROW_KIND
    else:
        raw = archive_bytes([("bank/low-level.csv", payload)])
        filename = "synthetic.zip"
        plan = table_archive.parse(io.BytesIO(raw))
        mode, mapping, row_kind = plan["mode"], plan["mapping"], table_import.ROW_KIND
    provenance = {
        "source_id": "a1" * 16, "filename": filename, "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw), "mode": mode, "mapping": mapping, "sheet": "",
        "parser_version": plan["profile"], "row_kind": row_kind,
    }
    keys = {index: table_import.record_key("table.csv", hashlib.sha256(payload).hexdigest(), index + 1)
            for index in (1, 2)}
    return filename, mode, plan["rows"], provenance, keys


@pytest.mark.parametrize("kind", ["ordinary", "feedback", "without-provenance"])
@pytest.mark.parametrize("empty", [False, True], ids=["populated-map", "empty-map"])
def test_standalone_identity_argument_is_not_available_to_other_commit_entrypoints(
        database, make_case, flow_row, kind, empty):
    cid = make_case("Synthetic internal argument source boundary")
    if kind == "without-provenance":
        filename, mode, rows, source = "synthetic.csv", "A", [flow_row()], None
    else:
        filename, mode, rows, source, _ = low_level_material(kind)
    keys = {} if empty else {row.get("_source_row", index): "b" * 64 for index, row in enumerate(rows, 1)}
    before = persisted(database)
    with pytest.raises(importer.ImportError):
        importer.commit_rows(filename, mode, rows, case_id=cid, provenance=source, standalone_csv_keys=keys)
    assert persisted(database) == before


@pytest.mark.parametrize("fault", [
    "missing", "extra", "boolean-position", "string-position", "float-position", "zero-position",
    "negative-position", "short-hash", "long-hash", "nonhex-hash", "uppercase-hash", "nonstring-hash", "not-a-map",
])
def test_archive_standalone_identity_argument_rejects_invalid_positions_or_keys_before_writes(
        database, make_case, fault):
    cid = make_case("Synthetic internal mapping shape boundary")
    filename, mode, rows, source, keys = low_level_material("archive")
    assert [row["_source_row"] for row in rows] == [1, 2]
    if fault == "missing":
        keys.pop(1)
    elif fault == "extra":
        keys[3] = "b" * 64
    elif fault.endswith("position"):
        position = {"boolean-position": True, "string-position": "1", "float-position": 1.0,
                    "zero-position": 0, "negative-position": -1}[fault]
        first = keys.pop(1)
        keys[position] = first
    elif fault == "not-a-map":
        keys = list(keys.items())
    else:
        keys[1] = {"short-hash": "a" * 63, "long-hash": "a" * 65, "nonhex-hash": "g" * 64,
                   "uppercase-hash": "A" * 64, "nonstring-hash": None}[fault]
    before = persisted(database)
    with pytest.raises(importer.ImportError):
        importer.commit_rows(filename, mode, rows, case_id=cid, provenance=source, standalone_csv_keys=keys)
    assert persisted(database) == before


RESTORE_CHILD = r'''
import base64,json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from app import db,table_import
db._start_ckpt=lambda:None
from app.main import app
from fastapi.testclient import TestClient
from test_table_container_identity import ingest
from test_folder_evidence import assert_unresolved_funds
data=json.load(sys.stdin)
fields=(*table_import.FLOW_FIELDS,'self_account','self_name')
def facts(client,cid):
    response=client.get(f'/api/v1/cases/{cid}/flows')
    assert response.status_code==200,response.text
    return sorted([{field:row[field] for field in fields} for row in response.json()['items']],
                  key=lambda row:json.dumps(row,sort_keys=True))
result=[]
with TestClient(app) as client:
    assert db.q_raw('select * from flows')==[]
    for key,initial_new in [('before',1),('after',0)]:
        package=base64.b64decode(data[key])
        response=client.post('/api/cases/import',files={'file':('synthetic.fundcase',package)})
        assert response.status_code==200,response.text
        cid=response.json()['case_id']
        assert db.q_raw('select * from import_sessions where case_id=?',[cid])==[]
        _,first=ingest(client,cid,base64.b64decode(data['archive']),'restored.zip')
        assert (first['result']['ok'],first['result']['rows_dup'])==(initial_new,2-initial_new)
        for content,filename in [(data['csv'],'repeat.csv'),(data['archive'],'repeat.zip')]:
            _,job=ingest(client,cid,base64.b64decode(content),filename)
            assert (job['result']['ok'],job['result']['rows_dup'])==(0,2)
        assert facts(client,cid)==data['expected']
        stats=client.get(f'/api/v1/cases/{cid}/stats').json()
        assert stats['flows'] is stats['amount_total'] is None
        assert stats['amount_totals']==[{'currency':'CNY','amount':None}]
        assert_unresolved_funds(stats['funds'],2,[('CNY','2.46')])
        assert [row['amount'] for row in facts(client,cid)]==['1.23','1.23']
        result.append({'flows':stats['flows'],'amount_totals':stats['amount_totals'],'funds':stats['funds']})
print('D01_RESULT='+json.dumps({'database':str(db.DB_PATH),'cases':result}))
db._con.close()
db._con=None
'''


def test_container_identity_survives_format4_restore_in_new_process_and_clean_database(
        client, database, make_case, tmp_path):
    cid = make_case("Synthetic container identity clean process restore")
    row = transaction("SYN-PORTABLE", "SYN-PORTABLE", "1.23")
    payload = csv_bytes([row, dict(row)])
    _, first = ingest(client, cid, payload, "standalone.csv")
    assert (first["result"]["ok"], first["result"]["rows_dup"]) == (1, 1)
    before_package = case_packages.export_package(cid)[0]
    content = archive_bytes([("bank/portable.csv", payload)], nested=True)
    _, promoted = ingest(client, cid, content, "synthetic.zip")
    assert (promoted["result"]["ok"], promoted["result"]["rows_dup"]) == (1, 1)
    after_package = case_packages.export_package(cid)[0]
    fields = (*table_import.FLOW_FIELDS, "self_account", "self_name")
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    expected = sorted([{field: row[field] for field in fields} for row in flows],
                      key=lambda row: json.dumps(row, sort_keys=True))
    before = persisted(database)
    runtime = tmp_path / "clean-child"
    paths = {key: runtime / name for key, name in (
        ("FUND_DATA_DIR", "data"), ("MPLCONFIGDIR", "mpl"), ("XDG_CACHE_HOME", "cache"),
        ("TMPDIR", "tmp"), ("TMP", "tmp"), ("TEMP", "tmp"))}
    for path in set(paths.values()):
        path.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **{key: str(path) for key, path in paths.items()},
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1",
           "FUNDS_TRACE_LICENSE_SECRET": "synthetic-regression-key-not-for-use"}
    env.pop("FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE", None)
    encoded = lambda data: base64.b64encode(data).decode("ascii")
    control = {"before": encoded(before_package), "after": encoded(after_package),
               "csv": encoded(payload), "archive": encoded(content), "expected": expected}
    child = subprocess.run([sys.executable, "-c", RESTORE_CHILD], input=json.dumps(control),
                           cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True,
                           text=True, encoding="utf-8", timeout=30)
    assert child.returncode == 0, child.stdout + child.stderr
    result = json.loads(next(line.removeprefix("D01_RESULT=") for line in child.stdout.splitlines()
                             if line.startswith("D01_RESULT=")))
    assert Path(result["database"]) == paths["FUND_DATA_DIR"] / "fund.duckdb"
    assert result["cases"][0] == result["cases"][1]
    for stats in result["cases"]:
        assert stats["flows"] is None
        assert stats["amount_totals"] == [{"currency": "CNY", "amount": None}]
        assert stats["funds"]["count"] == {"lower": 0, "upper": 2}
        assert stats["funds"]["totals"] == [{"currency": "CNY", "amount": None, "lower": "0.00", "upper": "2.46"}]
    assert persisted(database) == before
