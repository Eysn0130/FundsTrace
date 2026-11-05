"""One short-lived, process-owned pure parser result; never a durable receipt.

Only a byte-identical plan before and after case-bound preview is eligible.
Taking ownership is destructive. Missing entries recompute; corrupt entries
fail closed. No plan is loaded or replayed after process restart.
"""
import atexit
import hashlib
import os
import stat
import threading

from . import import_staging

TTL_SECONDS = 60
_lock = threading.Lock()
_slot = None


def close(parsed):
    if parsed is not None:
        for name in ("table_plan", "feedback_plan"):
            plan = parsed[0].attrs.get(name)
            if plan is not None:
                for field in ("_details_pool", "_plan_store"):
                    owner = plan.pop(field, None)
                    if owner is not None:
                        owner.close()


def stamp(parsed):
    """Hash complete persisted payloads and all effective in-memory metadata."""
    frame, sheet, mode, mapping, rows, stats = parsed
    plan = frame.attrs.get("table_plan")
    if plan is None or "_plan_store" not in plan:
        return None
    store = plan["_plan_store"]
    sequences = ("records", "rows", "cancelled_rows", "cancellations")
    if rows is not plan["rows"] or any(
            not isinstance(plan.get(kind), import_staging.StagedSequence)
            or plan[kind].store is not store for kind in sequences):
        return None
    store.flush()
    store._con.commit()
    metadata = {key: value for key, value in plan.items()
                if key not in (*sequences, "_plan_store", "_details_pool")}
    metadata.update(_frame_columns=list(frame.columns), _sheet=sheet, _mode=mode, _mapping=mapping,
                    _stats=stats, _counts=store._counts, _ends=store._ends,
                    _member_details=store._member_details, _details_version=store._details_version,
                    _frame_attrs={key: value for key, value in frame.attrs.items() if key != "table_plan"})
    encoded = import_staging._encode_value(metadata)
    if len(encoded) > 16 * 1024 * 1024:
        return None
    digest = hashlib.sha256(encoded)
    path = store._con.execute("pragma database_list").fetchone()[2]
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > import_staging.MAX_SPOOL_BYTES:
            raise ValueError("invalid preview scratch file")
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        after = os.fstat(stream.fileno())
        current = os.stat(path, follow_symlinks=False)
        if ((before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)):
            raise ValueError("preview scratch changed while reading")
    return digest.digest()


def key(session, profile, overrides):
    return tuple(session.get(field) for field in (
        "import_id", "case_id", "surface", "filename", "suffix", "sha256", "size_bytes",
        "mode", "mapping_json", "columns_json")) + (profile, overrides)


def discard(import_id=None):
    global _slot
    with _lock:
        if _slot is None or import_id is not None and _slot[0][0] != import_id:
            return
        _key, parsed, _stamp, timer = _slot
        _slot = None
        timer.cancel()
        close(parsed)


def retain(cache_key, parsed, original_stamp):
    global _slot
    if original_stamp is None or stamp(parsed) != original_stamp:
        return False
    with _lock:
        if _slot is not None:
            _key, previous, _stamp, timer = _slot
            timer.cancel()
            close(previous)
        # The identity token prevents a delayed timer from evicting a replacement.
        timer = threading.Timer(TTL_SECONDS, _expire, args=(parsed,))
        timer.daemon = True
        _slot = cache_key, parsed, original_stamp, timer
        timer.start()
    return True


def _expire(parsed):
    global _slot
    with _lock:
        if _slot is not None and _slot[1] is parsed:
            _slot = None
            close(parsed)


def take(cache_key):
    global _slot
    with _lock:
        if _slot is None or _slot[0] != cache_key:
            return None
        _key, parsed, original_stamp, timer = _slot
        _slot = None
        timer.cancel()
    try:
        if stamp(parsed) != original_stamp:
            raise ValueError("preview scratch failed integrity verification")
    except BaseException:
        close(parsed)
        raise
    return parsed


atexit.register(discard)
