"""Bounded chronological exploration using source evidence, never import IDs."""
from itertools import groupby

from . import db, event_resolution, principal_ledger

MAX_NEIGHBORS = 4096


def own_anchor(case_id, account, anchor):
    """Only confirmed event membership can bridge an opposite observation."""
    rows = db.q("""select f.* from effective_flows f where f.case_id=? and f.self_account=?
        and (f.id=? or f.id in (
          select m.flow_id from event_members m join event_members a
            on a.case_id=m.case_id and a.event_id=m.event_id
          where a.case_id=? and a.flow_id=? and a.status='confirmed' and m.status='confirmed'))
        order by f.id limit 2""", [case_id, account, anchor["id"], case_id, anchor["id"]])
    return rows[0] if len(rows) == 1 else None


def neighbors(case_id, account, anchor, direction, count):
    """Return nearest proved earlier credits/later debits on the own ledger.

    Entire timestamp groups are read before choosing a boundary. A stable ID
    may order presentation within a fully included tie, but never selects a
    subset of that tie or places it before/after the anchor.
    """
    currency = anchor.get("currency") or ""
    if currency.strip().casefold() in principal_ledger.UNKNOWN_CURRENCIES:
        return {"rows": [], "status": "unknown", "reasons": ["currency_evidence_unknown"]}
    if not anchor.get("trade_time"):
        return {"rows": [], "status": "unknown", "reasons": ["anchor_time_unknown"]}
    before = direction == "source"
    endpoint, comparison, order = ("payee_account", "<=", "desc") if before else ("payer_account", ">=", "asc")
    rows = db.q(f"""with candidates as (
        select f.*, cast(trade_time as varchar) _precise_time, dense_rank() over(order by trade_time {order}) time_rank
        from effective_flows f where case_id=? and self_account=? and {endpoint}=?
          and currency=? and trade_time {comparison} ?
          and coalesce(payer_account,'')<>'' and coalesce(payee_account,'')<>''
          and payer_account<>payee_account
        ) select * from candidates where time_rank<=?
          order by trade_time {order},id limit ?""",
        [case_id, account, account, currency, anchor["trade_time"], count + 1, MAX_NEIGHBORS + 1])
    for row in rows:
        row["trade_time"] = row.pop("_precise_time")
    if len(rows) > MAX_NEIGHBORS:
        return {"rows": [], "status": "limited", "reasons": ["neighbor_interval_resource_limit"]}
    own = own_anchor(case_id, account, anchor)
    metadata = event_resolution.ledger_metadata(case_id, [row["id"] for row in rows] + ([own["id"]] if own else []))
    membership = {row["flow_id"]: row for row in event_resolution.members(case_id, [anchor["id"], *(r["id"] for r in rows)])}
    anchor_event = membership.get(str(anchor["id"]), {})
    selected, reasons = [], set()
    for time, group in groupby(rows, key=lambda row: row["trade_time"]):
        group = [row for row in group if row["id"] != anchor["id"] and not (
            anchor_event.get("status") == "confirmed"
            and membership.get(str(row["id"]), {}).get("status") == "confirmed"
            and membership[str(row["id"])]["event_id"] == anchor_event["event_id"])]
        if time == anchor["trade_time"]:
            proved = []
            for row in group:
                pair = principal_ledger.source_order(
                    [{"id": str(own["id"])}, {"id": str(row["id"])}], metadata) if own else None
                if pair is None:
                    reasons.add("posting_order_unknown")
                elif (pair[0]["id"] == str(row["id"])) == before:
                    proved.append(row)
            group = proved
        if not group:
            continue
        ordered = principal_ledger.source_order([{**row, "id": str(row["id"])} for row in group], metadata)
        if ordered is not None:
            by_id = {str(row["id"]): row for row in group}
            group = [by_id[row["id"]] for row in (reversed(ordered) if before else ordered)]
        remaining = count - len(selected)
        if len(group) > remaining and ordered is None:
            reasons.add("neighbor_boundary_order_unknown")
            break
        selected.extend(group[:remaining])
        if len(selected) == count:
            break
    return {"rows": selected, "status": "partial" if reasons else "ok", "reasons": sorted(reasons)}
