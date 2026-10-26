"""Lossless, operation-local packing of completed members' flat audit notes.

Only decoded working notes are cached. Original bytes, canonical financial rows
and persisted journal JSON are unchanged; this is not a new source format.
"""
from collections import OrderedDict
from collections.abc import MutableMapping
import json
import os
import tempfile
import weakref
import zlib

CACHE_ENTRIES = 256
CACHE_BYTES = 2 * 1024 * 1024
MEMORY_PAYLOAD_BYTES = 64 * 1024 * 1024
MAX_SPOOL_BYTES = 8 * 1024 * 1024 * 1024


def _validate(notes):
    if not isinstance(notes, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                          for key, value in notes.items()):
        raise ValueError("invalid staged import notes")
    return notes


class DetailsPool:
    """One parser operation owns a finite decoded cache, without global state."""
    def __init__(self):
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._memory_bytes = 0
        self._stream = None
        self._spool_bytes = 0
        self._closed = False

    def _store(self, payload):
        if self._closed:
            raise ValueError("staged import notes storage is closed")
        # Count cumulative allocations, including superseded versions. This
        # bounds retained in-memory payloads without tracking every note's life.
        if self._memory_bytes + len(payload) <= MEMORY_PAYLOAD_BYTES:
            self._memory_bytes += len(payload)
            return payload
        if len(payload) >= 1 << 32 or self._spool_bytes + len(payload) > MAX_SPOOL_BYTES:
            raise ValueError("staged import notes exceed temporary storage limit")
        if self._stream is None:
            from . import db
            self._stream = tempfile.TemporaryFile(mode="w+b", buffering=0, prefix="funds-trace-notes-", dir=db.DATA_DIR)
            try:
                if hasattr(os, "fchmod"):
                    os.fchmod(self._stream.fileno(), 0o600)
            except BaseException:
                self._stream.close()
                raise
            weakref.finalize(self, self._stream.close)
        offset = self._spool_bytes
        self._stream.seek(offset)
        if self._stream.write(payload) != len(payload):
            raise OSError("incomplete staged import notes write")
        self._spool_bytes += len(payload)
        # One integer owns offset and compressed length; no per-record index,
        # named source file, or mutable alias is retained. Old versions remain
        # readable for copy.copy until the operation's last note is released.
        return (offset << 32) | len(payload)

    def _load(self, key):
        if isinstance(key, bytes):
            return key
        if type(key) is not int or key < 0 or self._stream is None:
            raise ValueError("invalid staged import notes reference")
        offset, length = key >> 32, key & 0xffffffff
        if not length or offset + length > self._spool_bytes:
            raise ValueError("invalid staged import notes reference")
        self._stream.seek(offset)
        payload = self._stream.read(length)
        if len(payload) != length:
            raise ValueError("incomplete staged import notes")
        return payload

    def close(self):
        self._closed = True
        if self._stream is not None:
            self._stream.close()
        self._cache.clear()
        self._cache_bytes = 0

    def _remember(self, payload, size, notes):
        if payload in self._cache:
            self._cache.move_to_end(payload)
            return
        if size > CACHE_BYTES:
            return
        while self._cache and (len(self._cache) >= CACHE_ENTRIES or self._cache_bytes + size > CACHE_BYTES):
            _key, (old_size, _notes) = self._cache.popitem(last=False)
            self._cache_bytes -= old_size
        self._cache[payload] = (size, notes)
        self._cache_bytes += size

    def _encode(self, notes):
        raw = json.dumps(_validate(notes), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        key = self._store(zlib.compress(raw, level=1))
        self._remember(key, len(raw), notes)
        return key, len(raw)

    def _decode(self, payload, size):
        if self._closed:
            raise ValueError("staged import notes storage is closed")
        if not isinstance(size, int) or size < 0:
            raise ValueError("invalid staged import notes length")
        cached = self._cache.get(payload)
        if cached is not None:
            if cached[0] != size:
                raise ValueError("incomplete staged import notes")
            self._cache.move_to_end(payload)
            return cached[1]
        decoder = zlib.decompressobj()
        raw = decoder.decompress(self._load(payload), size + 1)
        if len(raw) != size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("incomplete staged import notes")
        notes = _validate(json.loads(raw))
        self._remember(payload, size, notes)
        return notes

    def pack(self, notes):
        return PackedDetails(self, dict(notes))


class PackedDetails(MutableMapping):
    """Copy-on-write notes: shared cache entries never become mutable aliases."""
    __slots__ = ("_pool", "_payload", "_size")

    def __init__(self, pool, notes):
        self._pool = pool
        self._payload, self._size = pool._encode(notes)

    def _read(self):
        return self._pool._decode(self._payload, self._size)

    def __getitem__(self, key):
        return self._read()[key]

    def __iter__(self):
        return iter(self._read())

    def __len__(self):
        return len(self._read())

    def __setitem__(self, key, value):
        self.update({key: value})

    def __delitem__(self, key):
        notes = self.copy()
        del notes[key]
        self._payload, self._size = self._pool._encode(notes)

    def update(self, *args, **kwargs):
        previous = self._read()
        notes = dict(previous)
        notes.update(*args, **kwargs)
        if notes == previous:
            # Rebinding may restate the same declaration. Keep validation even
            # for unusual equal-valued inputs, but allocate no new disk version.
            _validate(notes)
            return
        self._payload, self._size = self._pool._encode(notes)

    def copy(self):
        return dict(self._read())

    def __deepcopy__(self, memo):
        notes = self.copy()
        memo[id(self)] = notes
        return notes


def json_default(value):
    if isinstance(value, PackedDetails):
        return value.copy()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
