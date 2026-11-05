"""Public staged import keeps complete checks without redundant journal scans."""
from collections import Counter
from decimal import Decimal

import pytest

from app import import_staging, import_validation
from app.services import imports
from test_folder_evidence import complete, preview
from test_import_chunk_transactions import snapshot
from test_table_cancellations import source, transaction


def members(count):
    return [("synthetic.csv", source([transaction(**{
        "交易流水号": f"SYN-{number:04d}",
        "交易时间": "2026-01-01 10:00:00.123456",
        "交易金额": "90071992547409.93" if number == 0 else "12.34",
        "交易余额": "" if number % 2 == 0 else "0.00",
    }) for number in range(count)])[0])]


def test_public_staged_validation_reads_each_journal_once_per_phase(
        client, database, make_case, monkeypatch):
    count = 260  # Cross the bounded decoded-cache window.
    cid, other = make_case("Synthetic scan cost"), make_case("Synthetic isolated case")
    before_other = snapshot(database, other)
    scans, active = Counter(), []
    iterate = import_staging.StagedSequence.__iter__

    def observed(sequence):
        for item in iterate(sequence):
            if active and sequence.kind == "records":
                scans[active[-1]] += 1
            yield item

    def phase(name, function):
        def call(*args, **kwargs):
            active.append(name)
            try:
                return function(*args, **kwargs)
            finally:
                active.pop()
        return call

    monkeypatch.setattr(import_staging.StagedSequence, "__iter__", observed)
    monkeypatch.setattr(import_validation, "table_manifest", phase("manifest", import_validation.table_manifest))
    monkeypatch.setattr(imports, "_write_feedback_journal", phase("journal", imports._write_feedback_journal))
    for attempt in range(2):
        scans.clear()
        job = complete(client, cid, preview(client, cid, members(count), scope_bank=None))
        assert job["stage"] == "done", job
        assert job["result"]["ok"] == (count if attempt == 0 else 0)
        assert job["result"]["rows_dup"] == (0 if attempt == 0 else count)
        assert scans == {"manifest": count, "journal": count}
        assert database.con().execute("select count(*),sum(amount),count(*) filter(where balance is null) from flows where case_id=?", [cid]).fetchone() == (
            count, Decimal("90071992547409.93") + Decimal("12.34") * (count - 1), count // 2)
        assert database.con().execute("select count(*) from flow_sources where case_id=?", [cid]).fetchone()[0] == (attempt + 1) * count
        assert database.con().execute("select count(*) from import_records where case_id=?", [cid]).fetchone()[0] == (attempt + 1) * count
    assert snapshot(database, other) == before_other
    page = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()
    assert page["total"] == count
    assert all(row["trade_time"].endswith(".123456") and isinstance(row["amount"], str) for row in page["items"])


@pytest.mark.parametrize("damage", ["number", "missing_link", "extra_link", "disposition"])
def test_late_journal_validation_failure_rolls_back_all_chunks(
        client, database, make_case, monkeypatch, damage):
    cid = make_case("Synthetic late validation rollback")
    other = make_case("Synthetic unaffected rollback case")
    before, other_before = snapshot(database, cid), snapshot(database, other)
    view = preview(client, cid, members(5), scope_bank=None)
    journal, write = imports._write_feedback_journal, database.insert_dataframe
    writes = []

    def damaged(case_id, source_id, plan):
        if damage == "number":
            plan["records"][-1]["record_no"] = 99
        elif damage == "disposition":
            plan["records"][-1]["disposition"] = "unresolved"
        elif damage == "missing_link":
            database.con().execute("delete from flow_sources where case_id=? and source_id=? and source_row=5", [case_id, source_id])
        else:
            database.con().execute("insert into flow_sources select case_id,source_id,99,flow_id,disposition from flow_sources where case_id=? and source_id=? and source_row=1", [case_id, source_id])
        return journal(case_id, source_id, plan)

    def observed(name, frame, columns):
        if name == "import_records":
            writes.append(len(frame))
        return write(name, frame, columns)

    with monkeypatch.context() as patch:
        patch.setattr(imports, "JOURNAL_CHUNK_RECORDS", 2)
        patch.setattr(imports, "_write_feedback_journal", damaged)
        patch.setattr(database, "insert_dataframe", observed)
        job = complete(client, cid, view)
    assert job["stage"] == "error" and job["result"] is None
    assert writes  # A failure after a write still rolls back the whole operation.
    assert snapshot(database, cid) == before
    assert snapshot(database, other) == other_before
    assert complete(client, cid, view)["stage"] == "done"


@pytest.mark.parametrize("damage", ["nonaccepted_tail", "noncontiguous_member"])
def test_manifest_fusion_still_checks_entire_source_journal(
        client, database, make_case, monkeypatch, damage):
    cid = make_case("Synthetic complete manifest scan")
    # A full-width invalid-time tail reaches the staged journal under both
    # tolerant historical CSV and the current strict structural contract.
    payload = source([*[transaction(**{"交易流水号": f"SYN-{i}"}) for i in range(3)],
                      transaction(**{"交易流水号": "SYN-TAIL", "交易时间": "invalid"})])[0]
    view = preview(client, cid, [("synthetic.csv", payload)], scope_bank=None)
    before = snapshot(database, cid)
    manifest = import_validation.table_manifest

    def damaged(plan, rows):
        assert plan["_plan_store"] is not None
        if damage == "nonaccepted_tail":
            assert plan["records"][-1]["disposition"] != "accepted"
            plan["records"][-1]["record_no"] = 99
        else:
            plan["records"][1]["member_path"] = "synthetic-other.csv"
        return manifest(plan, rows)

    with monkeypatch.context() as patch:
        patch.setattr(import_validation, "table_manifest", damaged)
        job = complete(client, cid, view)
    assert job["stage"] == "error" and job["error_code"] == "import_integrity_failed"
    assert snapshot(database, cid) == before
    assert complete(client, cid, view)["stage"] == "done"
    assert database.con().execute("select count(*) from import_records where case_id=?", [cid]).fetchone()[0] == 4
