"""Packed temporary notes retain the complete journal and mutation semantics."""
import copy
import gc
import io
import weakref
import zipfile
import zlib

import pytest

from app import evidence_resolution, import_staging as staging, table_archive, table_import
from app.services import imports
from test_table_cancellations import round_trip, source
from test_folder_evidence import preview, complete


def test_notes_mutations_preserve_order_and_do_not_change_shared_snapshots():
    pool = staging.DetailsPool()
    original = {"source_row": "2", "reason": "合成依据", "flow_sha256": "a" * 64}
    left, right = pool.pack(original), pool.pack(original)
    keys, values = left.keys(), left.values()
    original["reason"] = "external mutation"
    assert left["reason"] == right["reason"] == "合成依据"
    left.update(reason="revised", paired_record_no="8")
    assert "paired_record_no" in keys and "revised" in values
    assert right["reason"] == "合成依据" and "paired_record_no" not in right
    assert left.keys() & {"flow_sha256", "paired_record_no"} == {"flow_sha256", "paired_record_no"}
    assert {"reason", "source_row"} <= left.keys()
    assert left.pop("flow_sha256") == "a" * 64
    left["flow_sha256"] = "b" * 64
    assert list(left) == ["source_row", "reason", "paired_record_no", "flow_sha256"]
    assert left.get("missing", "fallback") == "fallback" and left.pop("missing", None) is None
    expected = {"source_row": "2", "reason": "revised", "paired_record_no": "8", "flow_sha256": "b" * 64}
    assert imports._json({"details": left}) == imports._json({"details": expected})
    duplicate = copy.deepcopy(left)
    duplicate["reason"] = "copy mutation"
    assert left["reason"] == "revised"
    shallow = copy.copy(left)
    shallow["reason"] = "shallow mutation"
    assert left["reason"] == "revised"
    with pytest.raises(ValueError):
        left.update(reason=123)
    assert dict(left) == expected


def test_completed_notes_do_not_retain_all_decoded_strings():
    class Text(str):
        pass
    pool, packed, references = staging.DetailsPool(), [], []
    enabled = gc.isenabled()
    gc.disable()
    try:
        for index in range(600):
            value = Text("Synthetic note " * 1000 + str(index))
            references.append(weakref.ref(value))
            packed.append(pool.pack({"reason": value, "source_row": str(index)}))
        del value
        assert references[0]() is None
        assert sum(reference() is not None for reference in references) <= staging.CACHE_ENTRIES
        assert pool._cache_bytes <= staging.CACHE_BYTES
        # Eviction affects storage only; the earliest complete note still reads.
        assert packed[0]["reason"] == "Synthetic note " * 1000 + "0"
        assert packed[-1]["source_row"] == "599"
    finally:
        if enabled:
            gc.enable()


def test_archive_releases_decoded_notes_while_the_member_is_still_being_parsed(monkeypatch):
    """Packing after a large member returns overlaps all of its decoded notes."""
    class Text(str):
        pass

    evidence, references = evidence_resolution.transaction_evidence, []
    def observe(*args, **kwargs):
        if len(references) > staging.CACHE_ENTRIES:
            assert references[0]() is None
        value = Text(evidence(*args, **kwargs))
        references.append(weakref.ref(value))
        return value

    monkeypatch.setattr(evidence_resolution, "transaction_evidence", observe)
    rows = [round_trip()[0] | {"交易流水号": f"SYN-REFERENCE-{index}"}
            for index in range(staging.CACHE_ENTRIES + 10)]
    plan = table_archive.parse(io.BytesIO(source(rows)[1]), profile=table_archive.EVIDENCE_PROFILE)
    assert len(references) == len(rows) == plan["stats"]["accepted"]
    assert all(evidence_resolution.validate_evidence(record["details"][evidence_resolution.DETAIL_FIELD])
               for record in plan["records"])


def test_archive_members_share_one_bounded_decoded_cache():
    payload = source(round_trip())[0]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("first.csv", payload)
        archive.writestr("second.csv", payload)
    plan = table_archive.parse(stream, profile=table_archive.EVIDENCE_PROFILE)
    pools = {id(record["details"]._pool) for record in plan["records"]}
    assert len(pools) == 1
    assert plan["stats"]["total"] == 6 and plan["stats"]["cancelled"] == 4
    from app import table_cancellations
    assert table_cancellations.validate_evidence(plan["records"]) == plan["cancellations"]


def test_member_staging_cannot_change_a_historical_table_profile():
    with pytest.raises(table_import.TableImportError, match="临时审计存储"):
        table_import.parse(source(round_trip())[0], suffix=".csv", profile=table_import.PROFILE,
                           _details_pool=staging.DetailsPool())


@pytest.mark.parametrize("damage", ["truncated", "trailing", "checksum", "length", "oversized", "invalid_json", "not_flat"])
def test_damaged_staging_never_becomes_missing_or_empty_notes(damage):
    pool = staging.DetailsPool()
    notes = pool.pack({"reason": "Synthetic proof", "source_row": "2"})
    if damage == "truncated":
        notes._payload = notes._payload[:-1]
    elif damage == "trailing":
        notes._payload += b"extra"
    elif damage == "checksum":
        notes._payload = notes._payload[:-1] + bytes([notes._payload[-1] ^ 1])
    elif damage == "length":
        notes._size += 1
    else:
        raw = (b'{"reason":"' + b"x" * 10000 + b'"}' if damage == "oversized" else
               b"not JSON" if damage == "invalid_json" else b'{"reason":1}')
        notes._payload = zlib.compress(raw)
        if damage != "oversized":
            notes._size = len(raw)
    for access in (lambda: notes.get("unknown"), lambda: "unknown" in notes,
                   lambda: imports._json(notes)):
        with pytest.raises((ValueError, zlib.error)):
            access()


def test_archive_packing_preserves_every_source_note_and_financial_fact(monkeypatch):
    payload = source(round_trip())[1]
    packed = table_archive.parse(io.BytesIO(payload), profile=table_archive.EVIDENCE_PROFILE)
    assert all(isinstance(record["details"], staging.PackedDetails) for record in packed["records"])
    with monkeypatch.context() as patch:
        patch.setattr(staging.DetailsPool, "pack", lambda _self, notes: notes)
        plain = table_archive.parse(io.BytesIO(payload), profile=table_archive.EVIDENCE_PROFILE)
    assert packed["rows"] == plain["rows"] and packed["cancelled_rows"] == plain["cancelled_rows"]
    for key in ("records", "members", "documents", "mapping_evidence", "cancellations", "stats"):
        assert imports._json(packed[key]) == imports._json(plain[key])
    old = table_archive.parse(io.BytesIO(payload), profile=table_archive.PROFILE)
    assert all(type(record["details"]) is dict for record in old["records"])


def test_json_fallback_does_not_silently_encode_unrelated_objects():
    with pytest.raises(TypeError):
        imports._json({"unexpected": object()})


def test_corrupted_notes_abort_public_commit_and_close_partial_journal(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic staging corruption")
    view = preview(client, cid, [("rows.csv", source(round_trip())[0])], scope_bank=None)
    initialize, attempts = imports._JournalRecords.__init__, []
    def corrupt(self, records):
        assert isinstance(records[1]["details"], staging.PackedDetails)
        records[1]["details"]._payload += b"corrupt"
        try:
            return initialize(self, records)
        finally:
            attempts.append(self._stream.closed)
    monkeypatch.setattr(imports._JournalRecords, "__init__", corrupt)
    job = complete(client, cid, view)
    assert job["stage"] == "error" and job["done"] == 0 and attempts == [True]
    for name in ("flows", "batches", "import_sources", "flow_sources", "import_members",
                 "import_records", "evidence_sources", "resolution_entities", "resolved_endpoints"):
        assert database.q_raw(f"select count(*) n from {name} where case_id=?", [cid])[0]["n"] == 0
    assert imports._source_available(imports._session(cid, view["import_id"]))
