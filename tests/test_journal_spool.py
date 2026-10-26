"""Finalized journals spill locally without changing public import semantics."""
import errno
import datetime as dt
import itertools
import os
import stat
import uuid

import pytest

from app import case_packages, table_import
from app.services import imports
from test_folder_evidence import account, complete, preview, table, transaction
from test_import_chunk_transactions import snapshot
from test_table_cancellations import round_trip, source
from test_table_mapping_versions import profile_preview


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
    view = request(client, cid, family, monkeypatch)
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert len(streams) == (0 if family == "legacy" else 1)
    assert all(stream.closed for stream in streams)
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
    assert not list(database.DATA_DIR.glob("funds-trace-journal-*"))


@pytest.mark.parametrize("failure", ["write", "short_write", "flush", "read", "verify"])
def test_spool_io_failure_rolls_back_and_closes_before_explicit_retry(client, database, make_case, monkeypatch, failure):
    cid = make_case("Synthetic failed journal spill")
    previous = complete(client, cid, preview(client, cid, [("old.csv", table("transaction", [
        transaction(**{"交易流水号": "SYN-OLD"})])), ("acc.csv", table("account", [account()]))]))
    assert previous["stage"] == "done"
    before = snapshot(database, cid)
    view = request(client, cid, "archive", monkeypatch)
    session = database.q_raw("select * from import_sessions where import_id=?", [view["import_id"]])[0]
    path = imports._path(session["import_id"], session["suffix"])
    source_bytes = path.read_bytes()
    temporary, streams = imports.tempfile.TemporaryFile, []
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
                raise OSError(errno.ENOSPC, "Synthetic temporary disk full")
            size = self.stream.write(data)
            return size - 1 if self.writes == 2 and failure == "short_write" else size
        def flush(self):
            if failure == "flush":
                raise OSError(errno.ENOSPC, "Synthetic temporary flush failure")
            self.stream.flush()
            if failure == "read":
                self.stream.truncate(self.stream.tell() - 1)
    def create(*args, **kwargs):
        stream = temporary(*args, **kwargs)
        streams.append(stream)
        return Fault(stream)
    with monkeypatch.context() as fail:
        fail.setattr(imports.tempfile, "TemporaryFile", create)
        if failure == "verify":
            def reject(*args, **kwargs):
                raise imports.import_validation.IntegrityError("Synthetic post-journal fidelity failure")
            fail.setattr(imports.import_validation, "verify_table_write", reject)
        job = complete(client, cid, view)
    assert job["stage"] == "error" and job["result"] is None
    assert snapshot(database, cid) == before
    assert streams and all(stream.closed for stream in streams)
    assert not list(database.DATA_DIR.glob("funds-trace-journal-*"))
    assert path.read_bytes() == source_bytes
    # The failure leaves only the registered original, never an automatic replay.
    retried = complete(client, cid, view)
    assert retried["stage"] == "done" and retried["result"]["ok"] == 1


def test_memory_records_and_disk_replay_preserve_all_journals_receipts_and_revisions(
        client, database, make_case, monkeypatch, tmp_path):
    # Retain the original finalized objects as the compatibility oracle, rather
    # than independently implementing serialization or recalculating decisions.
    class MemoryRecords:
        def __init__(self, records):
            self.records = records
        def __iter__(self):
            return iter(self.records)
        def __len__(self):
            return len(self.records)
        def close(self):
            self.records.clear()
    spool = imports._JournalRecords
    tables = ("flows", "batches", "import_sources", "flow_sources", "import_members", "import_records",
              "main_accounts", "account_ids", "evidence_sources", "resolution_entities", "resolved_endpoints",
              "resolution_subject_aliases", "resolution_state", "resolution_revisions")
    results = []
    for number, storage in enumerate((MemoryRecords, spool)):
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
        restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
        portable_stats = client.get(f"/api/v1/cases/{restored}/stats").json()
        assert portable_stats["import_coverage"] == stats["import_coverage"]
        assert portable_stats["amount_totals"] == stats["amount_totals"]
        results.append({"receipts": receipts, "tables": payload,
                        "stats": stats})
    assert results[1] == results[0]
