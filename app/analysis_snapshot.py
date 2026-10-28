"""Case-write generations, shared by facts, identity, events and allocations.

Public writers invalidate within their existing transaction, including no-flow
source changes. A failed write rolls the generation back with the facts. Analysis
readers hold db._lock through loading, calculation and serialization.
"""
from uuid import uuid4
import hashlib

from fastapi import HTTPException

from . import db

# Bump when interpretation changes without a source write. Keep the persisted
# case generation and existing 32-hex wire format; old exports must be refreshed.
POLICY = "source-evidence-analysis-v2"


def invalidate(case_id):
    token = uuid4().hex
    db.x("insert into analysis_state(case_id,revision) values (?,?) "
         "on conflict(case_id) do update set revision=excluded.revision", [case_id, token])
    return token


def revision(case_id, expected=None):
    if not db.q_raw("select 1 from cases where id=?", [case_id]):
        raise HTTPException(404, "案件不存在")
    rows = db.q_raw("select revision from analysis_state where case_id=?", [case_id])
    generation = rows[0]["revision"] if rows else invalidate(case_id)
    current = hashlib.sha256(f"{POLICY}:{generation}".encode()).hexdigest()[:32]
    if expected is not None and expected != current:
        raise HTTPException(409, "来源或关联已更新，请重新分析后导出")
    return current
