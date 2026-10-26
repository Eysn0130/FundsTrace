"""Disk-backed working notes remain lossless, mutable and operation-owned."""
import copy
import gc
import io
import os
import stat
import weakref

import pytest

from app import import_staging as staging, table_archive
from app.services import imports
from test_folder_evidence import complete, preview
from test_table_cancellations import round_trip, source


def test_spilled_notes_keep_copy_on_write_order_and_release_with_last_owner(database, monkeypatch):
    monkeypatch.setattr(staging, "MEMORY_PAYLOAD_BYTES", 0)
    monkeypatch.setattr(staging, "CACHE_ENTRIES", 1)
    pool = staging.DetailsPool()
    original = {"reason": "合成依据", "source_row": "2"}
    left = pool.pack(original)
    right = copy.copy(left)
    left.update(reason="修订", paired_record_no="8")
    del left["source_row"]
    left["source_row"] = "3"
    assert dict(right) == original
    assert list(left.items()) == [("reason", "修订"), ("paired_record_no", "8"), ("source_row", "3")]
    assert copy.deepcopy(left) == dict(left)
    assert pool._memory_bytes == 0
    stream, reference = pool._stream, weakref.ref(pool)
    if os.name != "nt":
        assert stat.S_IMODE(os.fstat(stream.fileno()).st_mode) == 0o600
    del pool, left
    gc.collect()
    assert reference() is not None and not stream.closed
    assert dict(right) == original
    del right
    gc.collect()
    assert reference() is None and stream.closed


def test_spill_preserves_all_archive_facts_notes_and_late_binding(database, monkeypatch):
    payload = source(round_trip())[1]
    expected = table_archive.parse(io.BytesIO(payload), profile=table_archive.EVIDENCE_PROFILE)
    monkeypatch.setattr(staging, "MEMORY_PAYLOAD_BYTES", 0)
    monkeypatch.setattr(staging, "CACHE_ENTRIES", 1)
    actual = table_archive.parse(io.BytesIO(payload), profile=table_archive.EVIDENCE_PROFILE)
    assert all(type(record["details"]._payload) is int for record in actual["records"])
    assert actual["rows"] == expected["rows"] and actual["cancelled_rows"] == expected["cancelled_rows"]
    for key in ("records", "members", "documents", "mapping_evidence", "cancellations", "stats"):
        assert imports._json(actual[key]) == imports._json(expected[key])
    record = actual["records"][0]
    record["details"].update(operation_context="synthetic-late-binding")
    assert record["details"]["operation_context"] == "synthetic-late-binding"
    assert "operation_context" not in expected["records"][0]["details"]


@pytest.mark.parametrize("damage", ["truncated", "checksum", "reference", "length"])
def test_spilled_corruption_fails_closed(database, monkeypatch, damage):
    monkeypatch.setattr(staging, "MEMORY_PAYLOAD_BYTES", 0)
    pool = staging.DetailsPool()
    note = pool.pack({"reason": "synthetic complete proof", "source_row": "2"})
    pool._cache.clear()
    pool._cache_bytes = 0
    if damage == "truncated":
        pool._stream.truncate(1)
    elif damage == "checksum":
        pool._stream.seek(0)
        pool._stream.write(b"invalid")
    elif damage == "reference":
        note._payload = (pool._spool_bytes << 32) | 1
    else:
        note._size += 1
    with pytest.raises((ValueError, staging.zlib.error)):
        imports._json(note)
    pool.close()


def test_spool_limit_and_failed_update_preserve_previous_note(database, monkeypatch):
    monkeypatch.setattr(staging, "MEMORY_PAYLOAD_BYTES", 0)
    pool = staging.DetailsPool()
    note = pool.pack({"reason": "before"})
    monkeypatch.setattr(staging, "MAX_SPOOL_BYTES", pool._spool_bytes)
    with pytest.raises(ValueError, match="storage limit"):
        note.update(reason="after")
    assert dict(note) == {"reason": "before"}
    pool.close()
    pool.close()
    with pytest.raises(ValueError, match="closed"):
        dict(note)


def test_reading_old_note_does_not_move_the_append_boundary(database, monkeypatch):
    monkeypatch.setattr(staging, "MEMORY_PAYLOAD_BYTES", 0)
    monkeypatch.setattr(staging, "CACHE_ENTRIES", 1)
    pool = staging.DetailsPool()
    first = pool.pack({"value": "first"})
    second = pool.pack({"value": "second"})
    assert first["value"] == "first"
    second.update(value="revised second")
    assert first["value"] == "first" and second["value"] == "revised second"
    pool.close()


def test_unchanged_updates_do_not_append_versions_or_bypass_validation(database, monkeypatch):
    monkeypatch.setattr(staging, "MEMORY_PAYLOAD_BYTES", 0)
    pool = staging.DetailsPool()
    note = pool.pack({"reason": "unchanged", "source_row": "2"})
    size, pointer = pool._spool_bytes, note._payload
    note.update(reason="unchanged")
    note.update({"source_row": "2", "reason": "unchanged"})
    note["source_row"] = "2"
    assert pool._spool_bytes == size and note._payload == pointer
    assert list(note) == ["reason", "source_row"]
    class EqualButNotText:
        def __eq__(self, other):
            return other == "unchanged"
    with pytest.raises(ValueError, match="invalid"):
        note.update(reason=EqualButNotText())
    assert dict(note) == {"reason": "unchanged", "source_row": "2"}
    note.update(reason="changed")
    assert pool._spool_bytes > size and note["reason"] == "changed"
    pool.close()


def test_preview_and_commit_explicitly_close_spill_even_with_retained_pool(client, database, make_case, monkeypatch):
    monkeypatch.setattr(staging, "MEMORY_PAYLOAD_BYTES", 0)
    original, pools = staging.DetailsPool.__init__, []
    def initialize(self):
        original(self)
        pools.append(self)
    monkeypatch.setattr(staging.DetailsPool, "__init__", initialize)
    cid = make_case("Synthetic explicit staging lifetime")
    view = preview(client, cid, [("rows.csv", source(round_trip())[0])], scope_bank=None)
    assert pools and all(pool._stream.closed for pool in pools)
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={"mode": view["mode"], "mapping": view["mapping"]})
    assert response.status_code == 200, response.text
    assert len(pools) == 2 and all(pool._stream.closed for pool in pools)
    job = complete(client, cid, response.json())
    assert job["stage"] == "done"
    assert len(pools) == 3 and all(pool._stream.closed for pool in pools)


def test_parser_failure_closes_spill_while_traceback_is_retained(database, monkeypatch):
    monkeypatch.setattr(staging, "MEMORY_PAYLOAD_BYTES", 0)
    from app import evidence_resolution
    original, pools = staging.DetailsPool.__init__, []
    def initialize(self):
        original(self)
        pools.append(self)
    monkeypatch.setattr(staging.DetailsPool, "__init__", initialize)
    def fail(_plan):
        raise ValueError("synthetic failure after notes exist")
    monkeypatch.setattr(evidence_resolution, "bind_occurrences", fail)
    with pytest.raises(table_archive.table_import.TableImportError) as caught:
        table_archive.parse(io.BytesIO(source(round_trip())[1]), profile=table_archive.EVIDENCE_PROFILE)
    assert caught.value.__traceback__ is not None
    assert pools and all(pool._stream.closed for pool in pools)


@pytest.mark.parametrize("phase", ["preview", "commit"])
def test_spill_write_failure_aborts_public_operation_without_partial_state(client, database, make_case, monkeypatch, phase):
    cid = make_case("Synthetic staging disk failure")
    files = [("rows.csv", source(round_trip())[0])]
    if phase == "commit":
        view = preview(client, cid, files, scope_bank=None)
    monkeypatch.setattr(staging, "MEMORY_PAYLOAD_BYTES", 0)
    original, attempts = staging.tempfile.TemporaryFile, []

    class BrokenWrite:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, key):
            return getattr(self.stream, key)

        def write(self, payload):
            self.stream.write(payload[:1])
            return 1

    def broken(*args, **kwargs):
        stream = original(*args, **kwargs)
        if kwargs.get("prefix") != "funds-trace-notes-":
            return stream
        attempts.append(stream)
        return BrokenWrite(stream)

    monkeypatch.setattr(staging.tempfile, "TemporaryFile", broken)
    if phase == "preview":
        response = client.post(f"/api/v1/cases/{cid}/imports/directory/preview", files=[("files", (name, content, "text/csv")) for name, content in files])
        assert response.status_code == 400
    else:
        job = complete(client, cid, view)
        assert job["stage"] == "error" and job["done"] == 0
        assert imports._source_available(imports._session(cid, view["import_id"]))
    gc.collect()
    assert attempts and all(stream.closed for stream in attempts)
    for table in ("flows", "batches", "import_sources", "flow_sources", "import_records", "evidence_sources", "resolved_endpoints"):
        assert database.q_raw(f"select count(*) n from {table} where case_id=?", [cid])[0]["n"] == 0
