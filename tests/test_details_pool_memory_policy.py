"""Memory notes keep the same lossless checked envelope as compressed spills."""
import copy
import json
import zlib

import pytest

from app import import_staging as staging


def test_memory_payload_is_uncompressed_but_checksummed_and_fully_accounted():
    expected = {"reason": "合成说明" * 200, "empty": "", "unused": "审计字段"}
    pool = staging.DetailsPool()
    note = pool.pack(expected)
    raw = json.dumps(expected, ensure_ascii=False, separators=(",", ":")).encode()
    assert zlib.decompress(note._payload) == raw
    assert len(note._payload) >= len(raw)
    assert pool._memory_bytes == len(note._payload)
    assert note._size == len(raw)
    # A bit flip inside otherwise valid text must still fail the checksum.
    payload = bytearray(note._payload)
    payload[payload.index(b"reason")] ^= 1
    note._payload = bytes(payload)
    with pytest.raises((ValueError, zlib.error)):
        note.get("unrequested")
    pool.close()


def test_memory_to_spill_transition_counts_actual_bytes_and_preserves_old_copies(database, monkeypatch):
    expected = {"reason": "synthetic " * 100, "empty": ""}
    raw = json.dumps(expected, separators=(",", ":")).encode()
    limit = len(zlib.compress(raw, 0))
    monkeypatch.setattr(staging, "MEMORY_PAYLOAD_BYTES", limit)
    pool = staging.DetailsPool()
    first = pool.pack(expected)
    assert pool._memory_bytes == limit and isinstance(first._payload, bytes)
    old = copy.copy(first)
    first.update(reason="changed")
    assert type(first._payload) is int and pool._memory_bytes == limit
    assert dict(old) == expected and copy.deepcopy(first) == {"reason": "changed", "empty": ""}
    del first["empty"]
    assert dict(first) == {"reason": "changed"}
    pool.close()
    for note in (first, old):
        with pytest.raises(ValueError, match="closed"):
            note.copy()


def test_wrapper_overhead_cannot_push_memory_over_the_remaining_budget(monkeypatch):
    expected = {"reason": "synthetic " * 100}
    raw = json.dumps(expected, separators=(",", ":")).encode()
    monkeypatch.setattr(staging, "MEMORY_PAYLOAD_BYTES", len(raw))
    pool = staging.DetailsPool()
    note = pool.pack(expected)
    assert dict(note) == expected and pool._memory_bytes <= len(raw)
    assert pool._memory_bytes == len(note._payload)
    pool.close()


def test_equal_memory_payloads_have_operation_local_cache_and_copy_on_write(monkeypatch):
    monkeypatch.setattr(staging, "CACHE_ENTRIES", 1)
    first, second = staging.DetailsPool(), staging.DetailsPool()
    left, right = first.pack({"value": "same"}), second.pack({"value": "same"})
    original = copy.copy(left)
    left.update(value="new")
    assert dict(original) == dict(right) == {"value": "same"}
    first.close()
    assert dict(right) == {"value": "same"}
    second.close()


@pytest.mark.parametrize("size", [-1, 0, True, None, "1"])
def test_memory_length_corruption_is_rejected_even_for_unused_fields(size):
    pool = staging.DetailsPool()
    note = pool.pack({"reason": "proof"})
    note._size = size
    with pytest.raises((TypeError, ValueError, zlib.error)):
        note.get("unused")
    pool.close()


@pytest.mark.parametrize("payload", [None, True, 1.5, bytearray(b"invalid")])
def test_memory_payload_type_corruption_is_not_treated_as_empty(payload):
    pool = staging.DetailsPool()
    note = pool.pack({"reason": "proof"})
    note._payload = payload
    with pytest.raises((TypeError, ValueError, zlib.error)):
        note.get("unused")
    pool.close()


@pytest.mark.parametrize("spilled", [False, True])
def test_compressed_envelope_rejects_trailing_data(database, monkeypatch, spilled):
    monkeypatch.setattr(staging, "MEMORY_PAYLOAD_BYTES", 0 if spilled else 64 * 1024**2)
    pool = staging.DetailsPool()
    note = pool.pack({"reason": "proof"})
    pool._cache.clear()
    pool._cache_bytes = 0
    if spilled:
        pool._stream.seek(pool._spool_bytes)
        pool._stream.write(b"extra")
        pool._spool_bytes += 5
        note._payload += 5
    else:
        note._payload += b"extra"
    with pytest.raises((ValueError, zlib.error)):
        note.copy()
    pool.close()
