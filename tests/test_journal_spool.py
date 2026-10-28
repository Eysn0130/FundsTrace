"""Finalized journals spill locally without changing public import semantics."""
import copy
import errno
import datetime as dt
import itertools
import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid

import pytest

from app import case_packages, import_staging, table_import
from app.services import imports
from test_folder_evidence import account, complete, preview, table, transaction
from test_import_chunk_transactions import snapshot
from test_table_cancellations import round_trip, source
from test_table_mapping_versions import profile_preview


def observe_plan_stores(monkeypatch):
    initialize, owners = import_staging.PlanStore.__init__, []
    def observed(self):
        initialize(self)
        path = Path(self._con.execute("pragma database_list").fetchone()[2])
        owners.append((self, self._con, path))
        if os.name != "nt":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    monkeypatch.setattr(import_staging.PlanStore, "__init__", observed)
    return owners


def assert_plan_stores_closed(owners):
    for owner, connection, path in owners:
        assert owner._closed and not path.parent.exists()
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("select 1")


def assert_receipt(database, import_id, job):
    saved = database.q_raw("select stage,done,result_json from import_sessions where import_id=?", [import_id])[0]
    assert saved["stage"] == job["stage"] and saved["done"] == job["done"]
    # The v1 schema adds its two nullable family fields to stored receipts.
    expected = ({"feedback": None, "table_summary": None, **json.loads(saved["result_json"])}
                if saved["result_json"] is not None else None)
    assert expected == job["result"]


def request(client, cid, family, monkeypatch):
    if family == "table":
        return profile_preview(client, cid, source(round_trip())[0], monkeypatch, table_import.EVIDENCE_PROFILE)
    if family == "archive":
        return preview(client, cid, [("rows.csv", source(round_trip())[0])], scope_bank=None)
    members = [("tx.csv", table("transaction", [transaction()])), ("acc.csv", table("account", [account()]))]
    if family == "legacy":
        return preview(client, cid, members, zipped=True, scope_bank=None)
    return preview(client, cid, members)


@pytest.mark.parametrize("family", ["feedback", "archive", "table", "legacy"])
def test_spool_is_private_replayable_and_released_with_full_provenance(client, database, make_case, monkeypatch, family):
    cid = make_case("Synthetic private finalized journal")
    original, streams, expected = imports._JournalRecords, [], []
    owners = observe_plan_stores(monkeypatch)
    class Observe(original):
        def __init__(self, records):
            frozen = imports._json(records)
            expected.append(frozen)
            super().__init__(records)
            streams.append(self._stream)
            assert records == []
            if os.name != "nt":
                assert stat.S_IMODE(os.fstat(self._stream.fileno()).st_mode) == 0o600
            assert imports._json(list(self)) == frozen
            assert imports._json(list(self)) == frozen
    monkeypatch.setattr(imports, "_JournalRecords", Observe)
    write, staged_journals = imports._write_feedback_journal, []
    def replay(case_id, source_id, plan):
        records = plan["records"]
        if isinstance(records, import_staging.StagedSequence):
            frozen = imports._json(list(records))
            assert imports._json(list(records)) == frozen
            assert imports._json(list(records)) == frozen
            staged_journals.append(frozen)
        write(case_id, source_id, plan)
        if isinstance(records, import_staging.StagedSequence):
            # Replaying the complete journal may not mutate its finalized facts.
            assert imports._json(list(records)) == frozen
    monkeypatch.setattr(imports, "_write_feedback_journal", replay)
    view = request(client, cid, family, monkeypatch)
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert len(streams) == (1 if family == "feedback" else 0)
    assert len(staged_journals) == (1 if family in {"archive", "table"} else 0)
    assert sum("records" in owner._counts for owner, _, _ in owners) == (2 if family in {"archive", "table"} else 0)
    assert all(stream.closed for stream in streams)
    assert_plan_stores_closed(owners)
    assert_receipt(database, view["import_id"], job)
    records = database.q_raw("select * from import_records where case_id=? order by record_no", [cid])
    assert len(records) == (view.get("feedback") or view["table_summary"])["record_total"]
    if family == "archive":
        assert job["result"]["table_summary"]["cancelled"] == 2
        assert [item["disposition"] for item in records] == ["imported", "cancelled", "cancelled"]
    portable = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert database.q_raw("select count(*) n from effective_flows where case_id=?", [portable]) == database.q_raw(
        "select count(*) n from effective_flows where case_id=?", [cid])
    first_rows = database.q_raw("select * from flows where case_id=? order by id", [cid])
    repeated = complete(client, cid, request(client, cid, family, monkeypatch))
    assert repeated["stage"] == "done" and repeated["result"]["ok"] == 0
    assert repeated["result"].get("resolution") == job["result"].get("resolution")
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == first_rows
    assert all(stream.closed for stream in streams)
    assert_plan_stores_closed(owners)
    assert not list(database.DATA_DIR.glob("funds-trace-journal-*"))
    assert not list(database.DATA_DIR.glob("funds-trace-plan-*"))


@pytest.mark.parametrize("backend,failure", [
    *[("journal", name) for name in ("write", "short_write", "flush", "read", "verify")],
    *[("plan", name) for name in ("write", "flush", "read", "verify")],
])
def test_spool_io_failure_rolls_back_and_closes_before_explicit_retry(client, database, make_case, monkeypatch, backend, failure):
    cid = make_case("Synthetic failed journal spill")
    previous = complete(client, cid, preview(client, cid, [("old.csv", table("transaction", [
        transaction(**{"交易流水号": "SYN-OLD"})])), ("acc.csv", table("account", [account()]))]))
    assert previous["stage"] == "done"
    before = snapshot(database, cid)
    if backend == "plan":
        view = request(client, cid, "archive", monkeypatch)
    else:
        view = preview(client, cid, [("new.csv", table("transaction", [
            transaction(**{"交易流水号": "SYN-NEW"})])), ("acc.csv", table("account", [account()]))])
    session = database.q_raw("select * from import_sessions where import_id=?", [view["import_id"]])[0]
    path = imports._path(session["import_id"], session["suffix"])
    source_bytes = path.read_bytes()
    temporary, streams, failures = imports.tempfile.TemporaryFile, [], []
    class Fault:
        def __init__(self, stream):
            self.stream, self.writes = stream, 0
        def __getattr__(self, name):
            return getattr(self.stream, name)
        def __iter__(self):
            return iter(self.stream)
        def write(self, data):
            self.writes += 1
            if self.writes == 2 and failure == "write":
                failures.append(failure)
                raise OSError(errno.ENOSPC, "Synthetic temporary disk full")
            size = self.stream.write(data)
            if self.writes == 2 and failure == "short_write":
                failures.append(failure)
                return size - 1
            return size
        def flush(self):
            if failure == "flush":
                failures.append(failure)
                raise OSError(errno.ENOSPC, "Synthetic temporary flush failure")
            self.stream.flush()
            if failure == "read":
                failures.append(failure)
                self.stream.truncate(self.stream.tell() - 1)
    def create(*args, **kwargs):
        stream = temporary(*args, **kwargs)
        streams.append(stream)
        return Fault(stream)
    with monkeypatch.context() as fail:
        owners = observe_plan_stores(fail)
        if backend == "journal":
            fail.setattr(imports.tempfile, "TemporaryFile", create)
        elif failure in {"write", "flush"}:
            connect = import_staging.sqlite3.connect
            class Connection:
                def __init__(self, connection):
                    self.connection = connection
                def __getattr__(self, name):
                    return getattr(self.connection, name)
                def executemany(self, query, values):
                    target = "insert into items" if failure == "write" else "update items"
                    if query.startswith(target):
                        # SQLite exposes I/O failure as an exception, not a
                        # short byte count. Leave a partial private batch first.
                        values = iter(values)
                        self.connection.execute(query, next(values))
                        failures.append(failure)
                        raise sqlite3.OperationalError("Synthetic temporary disk full")
                    return self.connection.executemany(query, values)
            fail.setattr(import_staging.sqlite3, "connect", lambda *args, **kwargs: Connection(connect(*args, **kwargs)))
        elif failure == "read":
            write = imports._write_feedback_journal
            def damage(case_id, source_id, plan):
                store = plan["_plan_store"]
                store.flush()
                store._con.execute("update items set payload=? where kind='records' and position=1", [b"truncated"])
                store._cache.clear()
                store._live.clear()
                failures.append(failure)
                return write(case_id, source_id, plan)
            fail.setattr(imports, "_write_feedback_journal", damage)
        if failure == "verify":
            def reject(*args, **kwargs):
                failures.append(failure)
                raise imports.import_validation.IntegrityError("Synthetic post-journal fidelity failure")
            if backend == "plan":
                fail.setattr(imports.import_validation, "verify_table_write", reject)
            else:
                write = imports._write_feedback_journal
                def reject_after_write(*args, **kwargs):
                    write(*args, **kwargs)
                    reject()
                fail.setattr(imports, "_write_feedback_journal", reject_after_write)
        job = complete(client, cid, view)
    assert job["stage"] == "error" and job["result"] is None
    assert failures == [failure], "The selected storage fault must actually run"
    assert_receipt(database, view["import_id"], job)
    assert snapshot(database, cid) == before
    if backend == "journal":
        assert streams and all(stream.closed for stream in streams)
    else:
        assert owners
    assert_plan_stores_closed(owners)
    assert not list(database.DATA_DIR.glob("funds-trace-journal-*"))
    assert not list(database.DATA_DIR.glob("funds-trace-plan-*"))
    assert path.read_bytes() == source_bytes
    # The failure leaves only the registered original, never an automatic replay.
    retried = complete(client, cid, view)
    assert retried["stage"] == "done" and retried["result"]["ok"] == 1
    assert_receipt(database, view["import_id"], retried)


def test_memory_records_and_disk_replay_preserve_all_journals_receipts_and_revisions(
        client, database, make_case, monkeypatch, tmp_path):
    # Snapshot finalized values before the staging owner closes. This oracle
    # uses no JSON serialization and does not recalculate any decisions.
    class MemoryRecords:
        def __init__(self, records):
            memory_suppliers.append(isinstance(records, import_staging.StagedSequence))
            self.records = copy.deepcopy(list(records))
        def __iter__(self):
            return iter(self.records)
        def __len__(self):
            return len(self.records)
        def close(self):
            self.records.clear()
    spool, write = imports._JournalRecords, imports._write_feedback_journal
    tables = ("flows", "batches", "import_sources", "flow_sources", "import_members", "import_records",
              "main_accounts", "account_ids", "evidence_sources", "resolution_entities", "resolved_endpoints",
              "resolution_subject_aliases", "resolution_state", "resolution_revisions")
    results, analysis_revisions = [], []
    for number, storage in enumerate((MemoryRecords, spool)):
        memory_suppliers = []
        with database._lock:
            database._con.close()
            database._con = None
            directory = tmp_path / f"synthetic-replay-{number}"
            directory.mkdir()
            monkeypatch.setattr(database, "DATA_DIR", directory)
            monkeypatch.setattr(database, "DB_PATH", directory / "fund.duckdb")
            database.con()
        ids = itertools.count(1)
        monkeypatch.setattr(imports.uuid, "uuid4", lambda: uuid.UUID(int=next(ids)))
        monkeypatch.setattr(imports, "_now", lambda: dt.datetime(2026, 10, 25, 12))
        monkeypatch.setattr(imports, "_JournalRecords", storage)
        def write_from_memory(case_id, source_id, plan):
            records = plan["records"]
            if isinstance(records, import_staging.StagedSequence):
                plan["records"] = MemoryRecords(records)
                try:
                    return write(case_id, source_id, plan)
                finally:
                    plan["records"].close()
                    plan["records"] = records
            return write(case_id, source_id, plan)
        monkeypatch.setattr(imports, "_write_feedback_journal", write_from_memory if storage is MemoryRecords else write)
        cid = make_case("Synthetic memory/disk journal equivalence")
        receipts = []
        first = [("card.csv", table("transaction", [transaction(**{"交易账号": ""})]))]
        for index, members in enumerate((first, first, [("account.csv", table("account", [account()]))])):
            job = complete(client, cid, preview(client, cid, members))
            assert job["stage"] == "done", job
            receipts.append(job["result"])
            if index == 0:
                key = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]["payer_account"]
                assert client.post("/api/main-accounts", json={"case_id": cid, "account": key, "name": "Synthetic main declaration"}).status_code == 200
                assert client.post("/api/account-ids/set", json={"case_id": cid, "account": key, "id_no": "123456789012345"}).status_code == 200
        job = complete(client, cid, request(client, cid, "archive", monkeypatch))
        assert job["stage"] == "done" and job["result"]["table_summary"]["cancelled"] == 2
        receipts.append(job["result"])
        conflict = complete(client, cid, preview(client, cid, [("conflict.csv", table("account", [account(number="SYN-OTHER")]))]))
        assert conflict["stage"] == "done"
        receipts.append(conflict["result"])
        if storage is MemoryRecords:
            assert True in memory_suppliers and False in memory_suppliers
        assert client.delete(f"/api/batches/{conflict['result']['batch_id']}", params={"case_id": cid}).status_code == 200
        payload = {name: database.q_raw(f"select * from {name} where case_id=? order by all", [cid]) for name in tables}
        for name in payload:
            for row in payload[name]:
                for key in ("created_at", "imported_at", "marked_at", "updated_at"):
                    if key in row:
                        row[key] = "operational timestamp"
        # Package import deliberately generates fresh SQL UUID source IDs and
        # flow IDs. Compare its public funds/coverage, while the original full
        # revision, decisions and journals above remain an exact comparison.
        stats = client.get(f"/api/v1/cases/{cid}/stats").json()
        analysis_revision = stats.pop("analysis_revision")
        assert isinstance(analysis_revision, str) and len(analysis_revision) == 32
        analysis_revisions.append(analysis_revision)
        restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
        portable_stats = client.get(f"/api/v1/cases/{restored}/stats").json()
        assert isinstance(portable_stats["analysis_revision"], str) and len(portable_stats["analysis_revision"]) == 32
        assert portable_stats["analysis_revision"] != analysis_revision
        assert portable_stats["import_coverage"] == stats["import_coverage"]
        assert portable_stats["amount_totals"] == stats["amount_totals"]
        results.append({"receipts": receipts, "tables": payload,
                        "stats": stats})
    assert analysis_revisions[1] != analysis_revisions[0]
    assert results[1] == results[0]


@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("failure", ["read", "flush"])
def test_late_preview_staging_failure_removes_only_new_operation(
        client, database, make_case, monkeypatch, directory, failure):
    """The original and ready session must not outlive a failed preview."""
    cid = make_case("Synthetic late preview staging failure")
    content = source(round_trip())[0]
    previous = preview(client, cid, [("previous.csv", content)], scope_bank=None)
    sessions_before = database.q_raw("select * from import_sessions where case_id=? order by import_id", [cid])
    facts_before = snapshot(database, cid)
    files_before = {path.name: path.read_bytes() for path in imports._directory().iterdir()}
    owners = observe_plan_stores(monkeypatch)
    preview_result, attempts, entered = imports._preview_result, [], []

    class Fault:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, query, *args):
            if failure == "read" and query.startswith("select position,payload") and " from items" in query:
                attempts.append(failure)
                raise sqlite3.OperationalError("Synthetic late staged read failure")
            return self.connection.execute(query, *args)

        def executemany(self, query, values):
            if failure == "flush" and query.startswith("update items"):
                attempts.append(failure)
                raise sqlite3.OperationalError("Synthetic late staged flush failure")
            return self.connection.executemany(query, values)

    def fail_late(session, parsed, legacy=False):
        # Arm only after parsing, source retention and session insertion. A
        # transaction may still own this new row; that is the required fix.
        assert database.q_raw("select stage from import_sessions where import_id=?", [session["import_id"]]) == [{"stage": "ready"}]
        entered.append(session["import_id"])
        plan = parsed[0].attrs["table_plan"]
        store = plan["_plan_store"]
        if failure == "flush":
            # Restate an existing value without changing any parser fact. This
            # queues a real write-back for the next normal preview read.
            record = plan["records"][0]
            record["reason_code"] = record["reason_code"]
            assert store._dirty
        store._con = Fault(store._con)
        return preview_result(session, parsed, legacy=legacy)

    monkeypatch.setattr(imports, "_preview_result", fail_late)
    monkeypatch.setattr(imports, "TABLE_PROFILE", table_import.EVIDENCE_PROFILE)
    base = f"/api/v1/cases/{cid}/imports"
    response = (client.post(base + "/directory/preview", files=[("files", ("new.csv", content, "text/csv"))])
                if directory else client.post(base + "/preview", files={"file": ("new.csv", content, "text/csv")}))
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"
    assert attempts == [failure] and len(entered) == 1
    assert entered[0] != previous["import_id"]
    assert owners
    assert_plan_stores_closed(owners)
    assert not list(database.DATA_DIR.glob("funds-trace-plan-*"))
    assert not list(database.DATA_DIR.glob("funds-trace-notes-*"))
    assert {path.name: path.read_bytes() for path in imports._directory().iterdir()} == files_before
    assert snapshot(database, cid) == facts_before
    assert database.q_raw("select * from import_sessions where case_id=? order by import_id", [cid]) == sessions_before
    assert imports._source_available(imports._session(cid, previous["import_id"]))
