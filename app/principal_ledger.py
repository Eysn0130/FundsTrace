"""Read complete account intervals, independently of graph/display filters.

Coverage and no-credit conditions require retained source evidence, not client
assertions or matching balances. Only own observations carry the account balance.
Matched cancellation journals cannot reconstruct historical availability: fail
closed until that history is supplied, rather than reading effective_flows.
"""
from datetime import datetime
from decimal import Decimal, localcontext
from itertools import groupby
import json

from . import db

MAX_LEDGER_ROWS = 100_000
MAX_WITNESS_CELLS = 500_000
POLICY = "source-evidence-book-ledger-v1"
UNKNOWN_CURRENCIES = ("", "unknown", "未知", "未知币种", "n/a", "none", "null", "nan")


def units(value):
    with localcontext() as ctx:
        ctx.prec = 50
        amount = Decimal(value)
        scaled = amount * 100
        if not amount.is_finite() or amount < 0 or scaled != scaled.to_integral_value():
            raise ValueError("金额须为非负、精确至分的十进制字符串")
        return int(scaled)


def money(value):
    return f"{value // 100}.{value % 100:02d}"


def _metadata(case_id, ids):
    from . import event_resolution
    return event_resolution.ledger_metadata(case_id, ids)


def read(case_id, account, currency, start, end):
    """Require a resolved accounting identity whenever typed evidence exists.

    A legacy explicit self-account without typed metadata can identify observed
    entries but does not certify ledger coverage or historical availability.
    Known card carriers, ambiguous identifiers and conflicts cannot be promoted
    to accounting ledgers merely by asserting complete coverage. Until uncertain
    own entries can be excluded by evidence, they conservatively block the same
    currency/interval, including when the selected account itself is resolved.
    """
    if currency.strip().casefold() in UNKNOWN_CURRENCIES:
        return {"status": "unknown", "reasons": ["currency_evidence_unknown"], "rows": []}
    # Filter the narrow original ledger before identity joins. No graph or
    # effective/net cancellation predicate is allowed to remove competitors.
    rows = db.q_raw("""select cast(f.id as varchar) id,f.trade_time,f.amount,f.balance,
        f.self_account raw_self_account,
        coalesce(s.entity_id,f.self_account) self_account,
        coalesce(p.entity_id,f.payer_account) payer_account,
        coalesce(q.entity_id,f.payee_account) payee_account
        from flows f
        left join resolved_endpoints s on s.case_id=f.case_id and s.flow_id=f.id and s.role='self'
        left join resolved_endpoints p on p.case_id=f.case_id and p.flow_id=f.id and p.role='payer'
        left join resolved_endpoints q on q.case_id=f.case_id and q.flow_id=f.id and q.role='payee'
        where f.case_id=? and f.currency=? and (f.trade_time between ? and ? or f.trade_time is null)
          and (f.self_account=? or s.entity_id=?)
        order by f.trade_time,f.id limit ?""", [case_id,currency,start,end,account,account,MAX_LEDGER_ROWS + 1])
    if len(rows) > MAX_LEDGER_ROWS:
        return {"status": "limited", "reasons": ["account_interval_resource_limit"], "rows": []}
    reasons = []
    # An unclassified currency on this own ledger might be the requested one;
    # filtering it away cannot establish that all competing entries were read.
    if db.q_raw("""select 1 from flows f
        left join resolved_endpoints s on s.case_id=f.case_id and s.flow_id=f.id and s.role='self'
        where f.case_id=? and lower(trim(coalesce(f.currency,''))) in (select unnest(?::varchar[]))
          and (f.trade_time between ? and ? or f.trade_time is null)
          and (f.self_account=? or s.entity_id=?) limit 1""",
        [case_id,list(UNKNOWN_CURRENCIES),start,end,account,account]):
        reasons.append("currency_evidence_unknown")
    if not rows:
        reasons.append("no_own_ledger_entries")
    if any(row["trade_time"] is None for row in rows):
        reasons.append("undated_own_entry")
    # An unresolved own entry may belong to the selected ledger. Checking only
    # the selected rows would omit precisely those competing movements whose
    # card/account relation has not been established. Names do not decide identity.
    if db.q_raw("""select 1 from resolved_endpoints s
        join flows f on f.case_id=s.case_id and f.id=s.flow_id
        left join resolution_entities e on e.case_id=s.case_id and e.entity_id=s.entity_id
        where s.case_id=? and s.role='self'
          and (f.currency=? or lower(trim(coalesce(f.currency,''))) in (select unnest(?::varchar[])))
          and (f.trade_time between ? and ? or f.trade_time is null)
          and (s.state<>'resolved' or coalesce(e.kind,'') not in ('account','subaccount')
               or coalesce(json_extract_string(e.details_json,'$.status'),'') not in ('resolved','name_conflict'))
        limit 1""", [case_id,currency,list(UNKNOWN_CURRENCIES),start,end]):
        reasons.append("account_ledger_identity_unknown")
    # Cancelled originals may exist only in the journal, with no flow_id or
    # reliable interval/account binding. Do not silently remove their history.
    if db.q_raw("select 1 from import_records where case_id=? and disposition='cancelled' limit 1", [case_id]):
        reasons.append("cancellation_availability_not_established")
    # An unparsed financial record has no reliable account/interval binding.
    # A caller's completeness assertion cannot override a known source gap.
    # Historical feedback v1 calls the same unresolved financial state review.
    if db.q_raw("select 1 from import_records where case_id=? and disposition in ('review','unresolved') limit 1", [case_id]):
        reasons.append("unresolved_source_records")
    if db.q_raw("select 1 from event_members where case_id=? and status='conflict' limit 1", [case_id]):
        from . import event_resolution
        for offset in range(0,len(rows),4096):
            if any(item["status"] == "conflict" for item in event_resolution.members(case_id,[row["id"] for row in rows[offset:offset+4096]])):
                reasons.append("conflicting_event_evidence")
                break
    for row in rows:
        incoming, outgoing = row["payee_account"] == account, row["payer_account"] == account
        if incoming == outgoing:
            reasons.append("ambiguous_own_entry_direction")
            continue
        row["direction"] = "in" if incoming else "out"
        try:
            row["amount"] = units(row["amount"])
        except (TypeError, ValueError):
            reasons.append("amount_evidence_unknown")
    return {"status": "unknown" if reasons else "ready", "reasons": sorted(set(reasons)), "rows": rows}


def ordered(rows, opening, metadata, balance_kind):
    """Use proven source order or a unique no-overdraft/balance-feasible step.

    IDs stabilize database reads only. They never settle a timestamp tie.
    If several next entries remain possible we report unknown, not one ordering
    chosen from permutations. No combinatorial search is used in production.
    """
    result, balance = [], opening
    for _, group in groupby(rows, key=lambda row: row["trade_time"]):
        pending = list(group)
        if len(pending) > 1:
            notes = [metadata.get(row["id"], {}) for row in pending]
            source_sets = [set(note.get("order_scopes", [])) for note in notes]
            common = set.intersection(*source_sets) if all(source_sets) else set()
            seq = [note.get("posting_order") for note in notes]
            if common and all(seq) and len(set(seq)) == len(seq):
                pending.sort(key=lambda row: int(metadata[row["id"]]["posting_order"]))
                proven = True
            else:
                proven = False
        else:
            proven = True
        while pending:
            if proven:
                row = pending.pop(0)
            else:
                # Bounded tie inference. Large unresolved groups are unknown,
                # not a reason to spend quadratic work on a guessed ordering.
                if len(pending) > 64:
                    return None, "posting_order_unknown"
                feasible = []
                for candidate in pending:
                    delta = candidate["amount"] * (1 if candidate["direction"] == "in" else -1)
                    after = balance + delta
                    book = balance_kind == "post_book" or metadata.get(candidate["id"], {}).get("balance_kind") == "post_book"
                    if after >= 0 and (not book or candidate["balance"] is None or candidate["balance"] >= 0 and units(candidate["balance"]) == after):
                        feasible.append(candidate)
                if len(feasible) != 1:
                    return None, "posting_order_unknown" if feasible else "inconsistent_order_constraints"
                row = feasible[0]
                pending.remove(row)
            balance += row["amount"] * (1 if row["direction"] == "in" else -1)
            book = balance_kind == "post_book" or metadata.get(row["id"], {}).get("balance_kind") == "post_book"
            # Negative stored balances must reach the inconsistency path;
            # units() intentionally accepts only nonnegative input amounts.
            if book and row["balance"] is not None and row["balance"] < 0:
                return None, "negative_book_balance"
            result.append({"id": row["id"], "direction": row["direction"], "amount": row["amount"],
                           "post_balance": units(row["balance"]) if book and row["balance"] is not None else None})
    return result, None


def calculate(case_id, request):
    from . import principal, principal_evidence
    start, end = datetime.fromisoformat(request.start), datetime.fromisoformat(request.end)
    loaded = read(case_id, request.account, request.currency, start, end)
    rows = loaded.pop("rows")
    conditions, assessment = principal_evidence.assess(case_id, rows)
    output = {"policy": POLICY, "currency": request.currency, "account": request.account,
              "interval": {"start": request.start, "end": request.end}, "conditions": conditions,
              "condition_basis": "source_evidence", "assessment": assessment,
              "ledger_count": len(rows), "targets": [], **loaded}
    if loaded["status"] != "ready":
        return output
    reasons = []
    if conditions["coverage"] != "complete_declared":
        reasons.append("ledger_coverage_unknown")
    if not conditions["no_overdraft"]:
        reasons.append("credit_availability_unknown")
    if not conditions["funds_available"]:
        reasons.append("funds_availability_unknown")
    if conditions["opening_balance"] is None:
        reasons.append("opening_balance_unknown")
    if reasons:
        return {**output, "status": "unknown", "reasons": reasons}
    if len(rows) * (2 * len(request.target_flow_ids) + 1) > MAX_WITNESS_CELLS:
        return {**output, "status": "limited", "reasons": ["witness_resource_limit"]}
    ids = {row["id"] for row in rows}
    if request.source_flow_id not in ids or not set(request.target_flow_ids) <= ids:
        return {**output, "status": "unknown", "reasons": ["source_or_target_not_in_own_interval"]}
    metadata = _metadata(case_id, list(ids))
    entries, reason = ordered(rows, units(conditions["opening_balance"]), metadata, conditions["balance_kind"])
    if reason:
        return {**output, "status": "inconsistency" if reason in {"inconsistent_order_constraints", "negative_book_balance"} else "unknown", "reasons": [reason]}
    try:
        result = principal.allocate_ledger(units(conditions["opening_balance"]), entries,
                                          {request.source_flow_id}, request.target_flow_ids)
    except ValueError:
        return {**output, "status": "unknown", "reasons": ["source_must_be_credit_and_target_debit"]}
    # Only explicitly designated monetary fields are formatted; IDs stay strings.
    monetary = {"amount", "min", "max", "fifo", "opening_balance", "closing_balance", "closing_source_balance",
                "source_amount", "other_amount", "source_balance", "balance"}
    def wire(value):
        if isinstance(value, list):
            return [wire(item) for item in value]
        if isinstance(value, dict):
            return {key: money(item) if key in monetary and type(item) is int else wire(item) for key,item in value.items()}
        return value
    return {**output, **wire(result), "reasons": [result["reason"]] if result.get("reason") else [],
            "source_flow_id": request.source_flow_id,
            "amount_semantics": {"amount": "account_debit", "min_max": "conditional_feasible_source_share",
                                 "fifo": "rule_allocation_not_proof", "scores": "not_used"},
            "joint_warning": "Each target extremum has its own feasible witness; independent maxima must not be added."}
