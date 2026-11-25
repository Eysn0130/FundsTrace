"""Small, case-bound acceptance records for the isolated desktop trial."""
from datetime import datetime, timezone
import json

from .. import analysis_snapshot, db
from . import assistance, cases, imports, trace_review


def _exists():
    return bool(db.q_raw("""select 1 from information_schema.tables
        where table_schema='main' and table_name='desktop_trial_review_receipts'"""))


def _write(cid, revision, scope, receipt, locators, tickets):
    if not tickets or not {location["source_id"] for location in locators} <= tickets.keys():
        raise cases.ServiceError(409, "assistance_stale", "原件已更新，请重新打开核对")
    for source, (digest, _, _) in tickets.items():
        original = imports._registered_original(cid, source)
        if not original["active"] or original["active_batch_id"] is None or original["sha256"] != digest:
            raise cases.ServiceError(409, "assistance_stale", "原件已更新，请重新打开核对")
    originals = [{"source_id": loc["source_id"], "sha256": tickets[loc["source_id"]][0],
                  "member_path": loc["member_path"], "source_row": loc["source_row"]} for loc in locators]
    sources = [{"source_id": source, "sha256": evidence[0]} for source, evidence in sorted(tickets.items())]
    db.x("""create table if not exists desktop_trial_review_receipts(
        case_id bigint, receipt_id varchar, accepted_at varchar, analysis_revision varchar,
        scope_sha256 varchar, receipt_json varchar, originals_json varchar,
        primary key(case_id,receipt_id))""")
    db.x("""insert into desktop_trial_review_receipts values(?,?,?,?,?,?,?) on conflict do nothing""",
         [cid, receipt["receipt_id"], datetime.now(timezone.utc).isoformat(), revision, scope,
          json.dumps(receipt, ensure_ascii=False),
          json.dumps({"originals": originals, "source_proofs": sources}, ensure_ascii=False)])
    if any(assistance._original_stamp(path) != stamp for _, path, stamp in tickets.values()):
        raise cases.ServiceError(409, "assistance_stale", "原件已更新，请重新打开核对")


def record_adopted_pair(case_id, result):
    """Write the historical receipt inside the transaction publishing the event."""
    cid = int(case_id)
    receipt = result["receipt"]
    locators = [row["locator"] for row in result["records"] if row["id"] in receipt["refs"]]
    expected = {location["source_id"]: location.get("sha256") for location in locators}
    tickets = assistance.verified_product_evidence(cid, expected)
    revision = analysis_snapshot.revision(cid, result["analysis_revision"])
    _write(cid, revision, receipt["receipt_id"], receipt, locators, tickets)


def record(case_id, body, result, range_tickets=None):
    receipt = result.get("receipt")
    if not receipt or receipt["status"] != "accepted":
        return
    packet = result["packet"]
    cid, canvas, anchor = int(case_id), int(body.canvas_id or 0), int(body.anchor_id or 0)
    refs = set(receipt["refs"])
    locators = [row["locator"] for row in packet["records"] if row["id"] in refs]
    if receipt["proposition"] == "conditional_range":
        locators.extend(packet["facts"].get("ledger_registry_sources", []))
    if receipt["proposition"] == "same_transaction":
        expected = {}
        for location in locators:
            source = location["source_id"]
            expected[source] = location.get("sha256") or expected.get(source)
        tickets = assistance.verified_product_evidence(cid, expected)
    else:
        tickets = range_tickets or {}
    with db.transaction():
        review = trace_review._require(body.review_token, cid, canvas, anchor_id=anchor)
        _write(cid, review.revision, packet["facts"].get("range_scope") or receipt["receipt_id"], receipt, locators, tickets)


def read(case_id, receipt_id):
    with db._lock:
        rows = db.q_raw("""select accepted_at,analysis_revision,scope_sha256,receipt_json,originals_json
            from desktop_trial_review_receipts where case_id=? and receipt_id=?""",
            [int(case_id), receipt_id]) if _exists() else []
        if not rows:
            raise cases.ServiceError(404, "trial_receipt_not_found", "当前案件没有该隔离试用受理实录")
        [row] = rows
        return {"case_id": case_id, "authority": "historical", "accepted_at": row["accepted_at"],
                "analysis_revision": row["analysis_revision"], "scope_sha256": row["scope_sha256"],
                "receipt": json.loads(row["receipt_json"]), **json.loads(row["originals_json"])}


def delete_case(case_id):
    if _exists():
        db.x("delete from desktop_trial_review_receipts where case_id=?", [case_id])
