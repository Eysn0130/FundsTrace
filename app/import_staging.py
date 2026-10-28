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
import datetime as dt
from decimal import Decimal
from collections.abc import Mapping, Sequence
import sqlite3

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
    if isinstance(value, (PackedDetails, _TrackedChild)):
        return value.copy()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


# This database is an operation-owned scratch file, never a product database.
# SQLite's small fixed page cache and batched writes avoid a second analytical
# engine/cache beside the application's DuckDB connection.
PLAN_CHUNK = 128
PLAN_CHUNK_BYTES = 2 * 1024 * 1024


def _wire(value):
    if value is None or type(value) in (str, int, bool, float):
        return ["v", value]
    # Snapshot a packed note once, rather than invoking its decoder for every
    # key through MutableMapping.items(). Encoding never mutates tracked data.
    if isinstance(value, (PackedDetails, _TrackedChild)):
        value = value.copy()
    if isinstance(value, Mapping):
        items = dict.items(value) if isinstance(value, dict) else value.items()
        return ["d", [[key, _wire(item)] for key, item in items]]
    if isinstance(value, tuple):
        return ["t", [_wire(item) for item in value]]
    if isinstance(value, list):
        return ["l", [_wire(item) for item in value]]
    if isinstance(value, set):
        return ["s", sorted((_wire(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))]
    if isinstance(value, Decimal):
        return ["m", str(value)]
    if isinstance(value, dt.datetime):
        return ["z", value.isoformat()]
    if isinstance(value, bytes):
        return ["b", value.hex()]
    raise ValueError("unsupported staged value")


def _unwire(value):
    tag, item = value
    if tag == "v":
        return item
    if tag == "d":
        return {key: _unwire(child) for key, child in item}
    if tag in ("t", "l", "s"):
        return {"t": tuple, "l": list, "s": set}[tag](_unwire(child) for child in item)
    if tag == "m":
        return Decimal(item)
    if tag == "z":
        return dt.datetime.fromisoformat(item)
    if tag == "b":
        return bytes.fromhex(item)
    raise ValueError("invalid staged value")


def _encode_value(value):
    return json.dumps(_wire(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _decode_value(value):
    return _unwire(json.loads(value))


def _pack_value(value):
    raw = _encode_value(value)
    return len(raw).to_bytes(8, "big") + zlib.compress(raw, 1)


def _unpack_value(payload):
    if len(payload) < 9:
        raise ValueError("incomplete staged plan value")
    size = int.from_bytes(payload[:8], "big")
    if size > MAX_SPOOL_BYTES:
        raise ValueError("invalid staged plan value length")
    decoder = zlib.decompressobj()
    raw = decoder.decompress(payload[8:], size + 1)
    if len(raw) != size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError("incomplete staged plan value")
    return raw


class _TrackedDict(dict):
    """Ordinary mutable parser facts with bounded, write-back dirty tracking."""
    def __init__(self, value, changed, ensure):
        self._changed = changed
        self._ensure = ensure
        super().__init__(value)

    def __getitem__(self, key):
        value = super().__getitem__(key)
        return _TrackedChild(self, value) if isinstance(value, dict) else value

    def get(self, key, default=None):
        return self[key] if key in self else default

    def items(self):
        return ((key, self[key]) for key in self)

    def values(self):
        return (self[key] for key in self)

    def copy(self):
        return {key: self[key] for key in self}

    def __setitem__(self, key, value):
        self._ensure()
        super().__setitem__(key, value)
        self._changed()

    def __delitem__(self, key):
        self._ensure()
        super().__delitem__(key)
        self._changed()

    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def pop(self, key, *default):
        self._ensure()
        if key not in self:
            if default:
                return default[0]
            raise KeyError(key)
        value = super().pop(key)
        self._changed()
        return value

    def clear(self):
        self._ensure()
        super().clear()
        self._changed()

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def __deepcopy__(self, memo):
        value = _decode_value(_encode_value(self))
        memo[id(self)] = value
        return value


class _TrackedChild(MutableMapping):
    """A retained notes alias keeps its root alive without a root/child cycle."""
    def __init__(self, owner, value):
        self._owner, self._value = owner, value
    def __getitem__(self, key):
        value = self._value[key]
        return _TrackedChild(self._owner, value) if isinstance(value, dict) else value
    def __iter__(self):
        return iter(self._value)
    def __len__(self):
        return len(self._value)
    def __setitem__(self, key, value):
        self._owner._ensure()
        self._value[key] = value
        self._owner._changed()
    def __delitem__(self, key):
        self._owner._ensure()
        del self._value[key]
        self._owner._changed()
    def copy(self):
        return dict(self._value)
    def __deepcopy__(self, memo):
        value = _decode_value(_encode_value(self))
        memo[id(self)] = value
        return value


def _dispose_plan(connection, directory):
    try:
        connection.close()
    finally:
        directory.cleanup()


class PlanStore:
    """Complete replayable plan; Python residency is limited to working chunks.

    Record order and source record numbers are separate: removing a financial
    row never renumbers the journal. Mutable dictionaries remain write-through
    even if a caller holds one beyond the read cache's lifetime.
    """
    def __init__(self):
        from . import db
        directory = tempfile.TemporaryDirectory(prefix="funds-trace-plan-", dir=db.DATA_DIR)
        connection = None
        try:
            path = os.path.join(directory.name, "plan.sqlite")
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(descriptor)
            self._con = connection = sqlite3.connect(path, check_same_thread=False)
            self._con.executescript("""pragma journal_mode=off; pragma synchronous=off;
                pragma cache_size=-4096; pragma temp_store=file; pragma mmap_size=0;
                create table items(kind text,position integer,number integer,member text,disposition text,
                                   details_version integer,payload blob,binding text,
                                   primary key(kind,position)) without rowid;
                create index item_numbers on items(kind,number);
                create index item_members on items(kind,member,position);
                create index item_auxiliary on items(kind,position) where disposition='auxiliary';
                create table lookup(namespace text,key blob,value blob,
                                    primary key(namespace,key)) without rowid;""")
            self._con.execute(f"pragma max_page_count={MAX_SPOOL_BYTES // 4096}")
        except BaseException:
            if connection is not None:
                connection.close()
            directory.cleanup()
            raise
        self._finalizer = weakref.finalize(self, _dispose_plan, self._con, directory)
        self._closed = False
        self._dirty = OrderedDict()
        self._bindings = OrderedDict()
        self._binding_bytes = 0
        self._cache = OrderedDict()
        self._live = weakref.WeakValueDictionary()
        self._counts = {}
        self._read_bytes = 0
        self._sizes = {}
        self._dirty_bytes = 0
        self._ends = {}
        self._namespace = 0
        self._member_details = {}
        self._member_details_sizes = {}
        self._details_version = 0
        self.encoded_bytes = 0
        self.update_encoded_bytes = 0
        self.decoded_bytes = 0
        self.decode_calls = 0
        self.index_encoded_bytes = 0
        self.member_note_encoded_bytes = 0
        self.peak_dirty_records = 0
        self.peak_read_records = 0
        self.peak_read_bytes = 0
        self.peak_dirty_input_bytes = 0
        self.read_queries = 0
        self.write_batches = 0

    def _check(self):
        if self._closed:
            raise ValueError("staged import plan is closed")

    def sequence(self, kind):
        return StagedSequence(self, kind)

    def mapping(self):
        self._namespace += 1
        return StagedMap(self, str(self._namespace))

    def set_member_details(self, values):
        """Present member-constant notes without rewriting every source record.

        The version belongs only to this disposable store. A later ordinary
        mutation writes its complete effective notes and current version, so
        explicit edits/deletions survive replay until the next family binding.
        """
        self._check()
        values = {member: dict(_validate(notes)) for member, notes in values.items()}
        self.flush()
        self.read_queries += 1
        for (member,) in self._con.execute("select distinct member from items where kind='records'"):
            if member not in values:
                raise KeyError(member)
        self._member_details = values
        self._member_details_sizes = {member: len(_encode_value(notes)) for member, notes in values.items()}
        self._details_version += 1
        for (kind, _position), item in self._live.items():
            if kind == "records":
                self._apply_member_details(item)
        # Existing external aliases remain live; do not retain their newly
        # materialized notes under the old, smaller cache-byte accounting.
        self._cache.clear()
        self._sizes.clear()
        self._read_bytes = 0

    def _apply_member_details(self, item):
        # Bypass tracked mutation only for this explicit derived binding. Held
        # nested aliases share this dictionary and immediately see the change.
        dict.__getitem__(item, "details").update(self._member_details[dict.__getitem__(item, "member_path")])
        item._staged_details_version = self._details_version

    def _track(self, kind, position, payload, details_version=0, binding=None):
        identity = kind, position
        cached = self._live.get(identity)
        if cached is not None:
            return cached
        raw = _unpack_value(payload)
        size = len(raw)
        self.decoded_bytes += size
        self.decode_calls += 1
        value = _decode_value(raw)
        owner = weakref.ref(self)
        def ensure():
            store = owner()
            if store is None:
                raise ValueError("staged import plan is closed")
            store._check()
        def changed():
            store = owner()
            if store is None:
                raise ValueError("staged import plan is closed")
            store._check()
            current = result_ref()
            if current is None:
                raise ValueError("staged import value is unavailable")
            if identity not in store._dirty:
                store._dirty_bytes += size
            store._dirty[identity] = current
            store.peak_dirty_records = max(store.peak_dirty_records, len(store._dirty))
            store.peak_dirty_input_bytes = max(store.peak_dirty_input_bytes, store._dirty_bytes)
            if len(store._dirty) >= PLAN_CHUNK or store._dirty_bytes >= PLAN_CHUNK_BYTES:
                store.flush()
        result = _TrackedDict(value, changed, ensure)
        result._staged_identity = identity
        result._staged_store = owner
        result._staged_binding = _validate(json.loads(binding)) if binding else {}
        expected_fields = {"occurrence_scope"} if kind == "records" else {"_occurrence_key", "_occurrence_scope"}
        required_field = "occurrence_scope" if kind == "records" else "_occurrence_scope"
        if binding and (required_field not in result._staged_binding or not set(result._staged_binding) <= expected_fields):
            raise ValueError("invalid staged occurrence binding")
        result._staged_binding_size = len(binding.encode("utf-8")) if binding else 0
        result._staged_details_version = details_version
        if kind == "records" and details_version < self._details_version:
            self._apply_member_details(result)
            size += self._member_details_sizes[value["member_path"]]
        if result._staged_binding:
            target = dict.__getitem__(result, "details") if kind == "records" else result
            dict.update(target, result._staged_binding)
            size += len(binding.encode("utf-8"))
        result_ref = weakref.ref(result)
        self._live[identity] = result
        self._cache[identity] = result
        self._sizes[identity] = size
        self._read_bytes += size
        while len(self._cache) > 1 and (len(self._cache) > PLAN_CHUNK or self._read_bytes > PLAN_CHUNK_BYTES):
            key, _value = self._cache.popitem(last=False)
            self._read_bytes -= self._sizes.pop(key)
        self.peak_read_records = max(self.peak_read_records, len(self._cache))
        self.peak_read_bytes = max(self.peak_read_bytes, self._read_bytes)
        return result

    def bind_occurrence_fields(self, item, fields):
        """Store small derived bindings separately from immutable source payloads.

        Ordinary mutations still flush the complete effective object, consuming
        its overlay. Thus retained aliases, explicit deletion and rebinding have
        the same behavior as an ordinary list plan.
        """
        self._check()
        identity = item._staged_identity
        if identity in self._dirty:
            self.flush()
        target = dict.__getitem__(item, "details") if identity[0] == "records" else item
        dict.update(target, fields)
        item._staged_binding.update(fields)
        encoded = json.dumps(item._staged_binding, ensure_ascii=False, separators=(",", ":"))
        encoded_size = len(encoded.encode("utf-8"))
        if identity in self._cache:
            delta = encoded_size - item._staged_binding_size
            self._sizes[identity] += delta
            self._read_bytes += delta
            while len(self._cache) > 1 and self._read_bytes > PLAN_CHUNK_BYTES:
                key, _value = self._cache.popitem(last=False)
                self._read_bytes -= self._sizes.pop(key)
            self.peak_read_bytes = max(self.peak_read_bytes, self._read_bytes)
        item._staged_binding_size = encoded_size
        previous = self._bindings.get(identity, "")
        self._binding_bytes += encoded_size - len(previous.encode("utf-8"))
        self._bindings[identity] = encoded
        if len(self._bindings) >= PLAN_CHUNK or self._binding_bytes >= PLAN_CHUNK_BYTES:
            self.flush()

    def flush(self):
        self._check()
        if self._bindings:
            self._con.executemany("update items set binding=? where kind=? and position=?",
                ((value, kind, position) for (kind, position), value in self._bindings.items()))
            self.update_encoded_bytes += self._binding_bytes
            self.write_batches += 1
            self._bindings.clear()
            self._binding_bytes = 0
        if not self._dirty:
            return
        values = []
        for (kind, position), item in self._dirty.items():
            payload = _pack_value(item)
            self.update_encoded_bytes += len(payload)
            values.append((payload, item.get("member_path", ""), item.get("disposition", ""),
                           item.get("record_no", item.get("_source_row", position)),
                           item._staged_details_version, kind, position))
        self._con.executemany("update items set payload=?,member=?,disposition=?,number=?,details_version=?,binding=null where kind=? and position=?", values)
        self.write_batches += 1
        for item in self._dirty.values():
            item._staged_binding.clear()
        self._dirty.clear()
        self._dirty_bytes = 0

    def close(self):
        if self._closed:
            return
        # A closed owner is never readable, including through a cached object.
        self._closed = True
        self._bindings.clear()
        self._binding_bytes = 0
        self._dirty.clear()
        self._cache.clear()
        self._sizes.clear()
        self._live.clear()
        self._member_details.clear()
        self._member_details_sizes.clear()
        self._finalizer()

    def metrics(self):
        self._check()
        return {"records": dict(self._counts), "encoded_bytes": self.encoded_bytes,
                "update_encoded_bytes": self.update_encoded_bytes,
                "decoded_bytes": self.decoded_bytes, "decode_calls": self.decode_calls,
                "index_encoded_bytes": self.index_encoded_bytes,
                "member_note_encoded_bytes": self.member_note_encoded_bytes,
                "file_bytes": self._con.execute("pragma page_count").fetchone()[0] * 4096,
                "peak_dirty_records": self.peak_dirty_records,
                "peak_read_records": self.peak_read_records,
                "peak_read_encoded_bytes": self.peak_read_bytes,
                "peak_dirty_input_encoded_bytes": self.peak_dirty_input_bytes,
                "read_queries": self.read_queries, "write_batches": self.write_batches}


class StagedSequence(Sequence):
    def __init__(self, store, kind):
        self.store, self.kind = store, kind

    def __len__(self):
        self.store._check()
        return self.store._counts.get(self.kind, 0)

    def __iter__(self):
        store = self.store
        store.flush()
        store.read_queries += 1
        cursor = store._con.execute("select position,payload,details_version,binding from items where kind=? order by position", (self.kind,))
        try:
            for position, payload, version, binding in cursor:
                store._check()
                yield store._track(self.kind, position, payload, version, binding)
        finally:
            cursor.close()

    def __getitem__(self, index):
        if isinstance(index, slice):
            from itertools import islice
            start, stop, step = index.indices(len(self))
            return list(islice(self, start, stop, step))
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        store = self.store
        if self.kind == "records":
            identity = self.kind, index + 1
            if identity in store._dirty:
                return store._dirty[identity]
            if identity in store._cache:
                return store._cache[identity]
            store.flush()
            store.read_queries += 1
            start = index // PLAN_CHUNK * PLAN_CHUNK + 1
            result = None
            for position, payload, version, binding in store._con.execute(
                    "select position,payload,details_version,binding from items where kind=? and position>=? and position<? order by position",
                    (self.kind, start, start + PLAN_CHUNK)):
                item = store._track(self.kind, position, payload, version, binding)
                if position == index + 1:
                    result = item
                if store._read_bytes >= PLAN_CHUNK_BYTES:
                    break
            if result is not None:
                return result
            position, payload, version, binding = store._con.execute("select position,payload,details_version,binding from items where kind=? and position=?", identity).fetchone()
            return store._track(self.kind, position, payload, version, binding)
        store.flush()
        store.read_queries += 1
        position, payload, version, binding = store._con.execute(
            "select position,payload,details_version,binding from items where kind=? order by position limit 1 offset ?",
            (self.kind, index)).fetchone()
        return store._track(self.kind, position, payload, version, binding)

    def select(self, *, member=None, lower=None, upper=None, disposition=None):
        store = self.store
        store.flush()
        clause, args = "kind=?", [self.kind]
        if member is not None:
            clause += " and member=?"
            args.append(member)
        if lower is not None:
            clause += " and number>=? and number<=?"
            args.extend((lower, upper))
        if disposition is not None:
            clause += " and disposition=?"
            args.append(disposition)
        cursor = store._con.execute(f"select position,payload,details_version,binding from items where {clause} order by position", args)
        store.read_queries += 1
        try:
            for position, payload, version, binding in cursor:
                yield store._track(self.kind, position, payload, version, binding)
        finally:
            cursor.close()

    def with_records(self, records, *, missing_scope_only=False):
        """Pair financial rows with their exact journal positions in one scan."""
        store = self.store
        if records.store is not store or records.kind != "records":
            raise ValueError("source records belong to another staged plan")
        store.flush()
        store.read_queries += 1
        # This overlay is written only by the closed occurrence-binding helper.
        # Ordinary edits consume it during flush, so late changes take the full
        # decoding path. Keep broken row/journal references on that path too.
        scope_filter = """and (coalesce(json_extract(r.binding,'$._occurrence_scope'),'')=''
            or j.position is null or j.number is null or j.number!=r.number or r.number<=0)""" if missing_scope_only else ""
        cursor = store._con.execute(f"""select r.position,r.payload,r.details_version,r.binding,
                j.position,j.payload,j.details_version,j.binding from items r left join items j
                on j.kind='records' and j.position=r.number
                where r.kind=? {scope_filter} order by r.position""", (self.kind,))
        try:
            for position, payload, version, binding, record_position, record_payload, record_version, record_binding in cursor:
                row = store._track(self.kind, position, payload, version, binding)
                number = row["_source_row"]
                if type(number) is not int or number <= 0 or number > len(records) or record_position is None:
                    raise KeyError(number)
                record = store._track("records", record_position, record_payload, record_version, record_binding)
                if record["record_no"] != number:
                    raise ValueError("non-consecutive staged source records")
                yield row, record
        finally:
            cursor.close()

    def extend(self, values):
        store = self.store
        store.flush()
        position = store._ends.get(self.kind, 0)
        pending, size = [], 0
        def write():
            store._con.executemany("insert into items(kind,position,number,member,disposition,details_version,payload) values (?,?,?,?,?,?,?)", pending)
            store.write_batches += 1
            store._counts[self.kind] = store._counts.get(self.kind, 0) + len(pending)
            pending.clear()
        for item in values:
            payload = _pack_value(item)
            if pending and (len(pending) >= PLAN_CHUNK or size + len(payload) > PLAN_CHUNK_BYTES):
                write()
                size = 0
            position += 1
            # Binding is a snapshot of the records already in the plan, as for
            # a plain list. Later appends keep their exact notes until rebinding,
            # including explicit edits/deletions copied from a tracked record.
            pending.append((self.kind, position, item.get("record_no", item.get("_source_row", position)),
                            item.get("member_path", ""), item.get("disposition", ""),
                            store._details_version, payload))
            size += len(payload)
            store.encoded_bytes += len(payload)
        if pending:
            write()
        store._ends[self.kind] = position

    def append(self, value):
        self.extend((value,))

    def clear(self):
        store = self.store
        store.flush()
        store._con.execute("delete from items where kind=?", (self.kind,))
        store._counts[self.kind] = 0
        store._cache.clear()
        store._sizes.clear()
        store._read_bytes = 0

    def remove_numbers(self, numbers):
        store = self.store
        store.flush()
        store._con.executemany("delete from items where kind=? and number=?", ((self.kind, number) for number in numbers))
        store._counts[self.kind] = store._con.execute("select count(*) from items where kind=?", (self.kind,)).fetchone()[0]
        store._cache.clear()
        store._sizes.clear()
        store._read_bytes = 0

    def remove_positions(self, positions):
        store = self.store
        store.flush()
        store._con.executemany("delete from items where kind=? and position=?", ((self.kind, position) for position in positions))
        store._counts[self.kind] = store._con.execute("select count(*) from items where kind=?", (self.kind,)).fetchone()[0]
        store._cache.clear()
        store._sizes.clear()
        store._read_bytes = 0


class StagedMap(MutableMapping):
    """Disk index with a bounded write buffer, for facts and dedup identities."""
    def __init__(self, store, namespace):
        self.store, self.namespace = store, namespace
        self._pending = OrderedDict()

    def flush(self):
        self.store._check()
        if self._pending:
            self.store._con.executemany("insert or replace into lookup values (?,?,?)",
                ((self.namespace, key, value) for key, value in self._pending.items()))
            self.store.write_batches += 1
            self._pending.clear()

    def __setitem__(self, key, value):
        self.store._check()
        encoded_key, encoded_value = _encode_value(key), _encode_value(value)
        self.store.index_encoded_bytes += len(encoded_key) + len(encoded_value)
        self._pending[encoded_key] = encoded_value
        if len(self._pending) >= PLAN_CHUNK:
            self.flush()

    def __getitem__(self, key):
        self.store._check()
        encoded = _encode_value(key)
        value = self._pending.get(encoded)
        if value is None:
            self.store.read_queries += 1
            row = self.store._con.execute("select value from lookup where namespace=? and key=?", (self.namespace, encoded)).fetchone()
            if row is None:
                raise KeyError(key)
            value = row[0]
        return _decode_value(value)

    def __delitem__(self, key):
        self.flush()
        result = self.store._con.execute("delete from lookup where namespace=? and key=?", (self.namespace, _encode_value(key)))
        if not result.rowcount:
            raise KeyError(key)

    def __iter__(self):
        self.flush()
        cursor = self.store._con.execute("select key from lookup where namespace=?", (self.namespace,))
        try:
            for (key,) in cursor:
                yield _decode_value(key)
        finally:
            cursor.close()

    def __len__(self):
        self.flush()
        return self.store._con.execute("select count(*) from lookup where namespace=?", (self.namespace,)).fetchone()[0]

    def add(self, key):
        self[key] = True


class _RecordMap(Mapping):
    def __init__(self, records):
        self.records = records
    def __getitem__(self, number):
        if type(number) is not int or number <= 0:
            raise KeyError(number)
        try:
            value = self.records[number - 1]
        except IndexError:
            raise KeyError(number) from None
        if value["record_no"] != number:
            raise ValueError("non-consecutive staged source records")
        return value
    def __iter__(self):
        return iter(range(1, len(self.records) + 1))
    def __len__(self):
        return len(self.records)


def record_map(plan):
    records = plan["records"]
    return _RecordMap(records) if isinstance(records, StagedSequence) else {item["record_no"]: item for item in records}


def auxiliary_records(plan):
    records = plan["records"]
    return (records.select(disposition="auxiliary") if isinstance(records, StagedSequence)
            else (record for record in records if record["disposition"] == "auxiliary"))


def bind_occurrence_fields(row, record, key, scope):
    fields = {"_occurrence_key": key} if key is not None else {}
    fields["_occurrence_scope"] = scope
    for item, values in ((row, fields), (record, {"occurrence_scope": scope})):
        if isinstance(item, _TrackedDict):
            item._ensure()
            item._staged_store().bind_occurrence_fields(item, values)
        elif item is record:
            item["details"].update(values)
        else:
            item.update(values)


def row_records(plan, *, kinds=("rows", "cancelled_rows"), missing_scope_only=False):
    records, indexed = plan["records"], None
    for kind in kinds:
        rows = plan.get(kind, ())
        if isinstance(rows, StagedSequence) and isinstance(records, StagedSequence):
            yield from rows.with_records(records, missing_scope_only=missing_scope_only)
        else:
            if indexed is None:
                indexed = record_map(plan)
            for row in rows:
                yield row, indexed[row["_source_row"]]


def stage_plan(plan):
    store = PlanStore()
    try:
        for kind in ("records", "rows", "cancelled_rows", "cancellations"):
            if kind in plan:
                values = plan[kind]
                staged = store.sequence(kind)
                staged.extend(values)
                plan[kind] = staged
        plan["_plan_store"] = store
        return plan
    except BaseException:
        store.close()
        raise
