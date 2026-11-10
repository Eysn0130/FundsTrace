"""Case-write generations, shared by facts, identity, events and allocations.

Public writers invalidate within their existing transaction, including no-flow
source changes. A failed write rolls the generation back with the facts. Analysis
readers hold db._lock, or use a complete committed case read snapshot.
Missing generations retain locked initialization and reading.
"""
from uuid import uuid4
import hashlib
from contextlib import contextmanager

from fastapi import HTTPException

from . import db

# Bump when interpretation changes without a source write. Keep the persisted
# case generation and existing 32-hex wire format; old exports must be refreshed.
POLICY = "source-evidence-analysis-v5-source-card-ownership"


def invalidate(case_id):
    from . import source_ledger
    source_ledger.refresh_index(db.con(), case_id)
    token = uuid4().hex
    db.x("insert into analysis_state(case_id,revision) values (?,?) "
         "on conflict(case_id) do update set revision=excluded.revision", [case_id, token])
    return token


def revision(case_id, expected=None):
    if not db.q_raw("select 1 from cases where id=?", [case_id]):
        raise HTTPException(404, "案件不存在")
    rows = db.q_raw("select revision from analysis_state where case_id=?", [case_id])
    if not rows and db.in_read_snapshot():
        raise RuntimeError("Analysis revision must be initialized before a read snapshot")
    generation = rows[0]["revision"] if rows else invalidate(case_id)
    current = hashlib.sha256(f"{POLICY}:{generation}".encode()).hexdigest()[:32]
    if expected is not None and expected != current:
        raise HTTPException(409, "来源或关联已更新，请重新分析后导出")
    return current


@contextmanager
def case_read_snapshot(case_id):
    """Keep the entire case analysis read in one committed view.

    A new/old case without a generation must initialize under the writer lock,
    before reading its facts; that first request retains the complete old lock
    path. Unsupported engine syntax also retains that path, never a writable
    private connection. Normal populated cases never acquire the writer lock.
    """
    db._require_snapshot_entry()
    # Reentrant only for this entry calling the lower-level snapshot context.
    # The gate covers connection open/close and the complete locked fallback.
    with db._read_admission:
        with db.read_snapshot() as active:
            if not active:
                with db.transaction():
                    revision(case_id)
                yield
                return
            if db.q_raw("select 1 from analysis_state where case_id=?", [case_id]):
                yield
                return
        with db._lock:
            with db.transaction():
                revision(case_id)
            yield
