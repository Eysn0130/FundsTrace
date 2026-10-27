"""Join complete own ledgers through confirmed events, then allocate globally.

Independent per-account upper bounds are never propagated as fixed inflows.
Unobserved boundary accounts are explicit terminal model boundaries, not a
claim about money's real-world fate or a missing-ledger reconstruction.
"""
from collections import defaultdict
from datetime import datetime
import heapq

from . import db, event_resolution, principal_ledger

MAX_EVENTS = 20_000


def calculate(case_id, request):
    """Evaluate retained source evidence; client declarations grant no facts."""
    from . import principal_evidence
    start, end = datetime.fromisoformat(request.start), datetime.fromisoformat(request.end)
    assessments, reasons = [], []
    status = "unknown"
    for item in request.accounts:
        loaded = principal_ledger.read(case_id, item.account, request.currency, start, end)
        conditions, evidence = principal_evidence.assess(case_id, loaded["rows"])
        assessments.append({"account": item.account, "conditions": conditions, "assessment": evidence})
        reasons.extend(loaded["reasons"])
        if loaded["status"] == "limited":
            status = "limited"
    # A selected set of accounts does not prove that outside accounts are
    # terminal, even when their currently observed transfers do not return.
    reasons.extend(("complete_own_ledgers_and_opening_balances_required", "external_boundary_returns_unknown"))
    return {"policy": "source-evidence-multiledger-v1", "condition_basis": "source_evidence",
            "status": status, "currency": request.currency, "targets": [], "accounts": assessments,
            "assessment": [{"condition": "boundary", "status": "unknown", "kind": "missing_evidence", "source_ids": [],
                            "message": "缺少分析账户之外资金无回流的证据，不能把边界自行设为终止"}],
            "reasons": sorted(set(reasons)), "interval": {"start": request.start, "end": request.end}}


def _calculate_conditioned(case_id, request):
    from . import principal_network
    accounts = {item.account: item.conditions for item in request.accounts}
    output = {"policy": "conditional-multiledger-v1", "currency": request.currency, "targets": [],
              "accounts": [item.model_dump() for item in request.accounts], "boundary": request.boundary,
              "condition_basis": "user_declared_not_independently_certified", "reasons": [],
              "interval": {"start": request.start, "end": request.end}}
    def unknown(reason, status="unknown"):
        return {**output, "status": status, "reasons": [reason]}
    if request.boundary != "terminal_no_return_declared":
        return unknown("external_boundary_returns_unknown")
    if any(c.coverage != "complete_declared" or not c.no_overdraft or not c.funds_available or c.opening_balance is None for c in accounts.values()):
        return unknown("complete_own_ledgers_and_opening_balances_required")
    start,end = datetime.fromisoformat(request.start),datetime.fromisoformat(request.end)
    by_account, rows, opening = {}, {}, {}
    for account, conditions in accounts.items():
        loaded = principal_ledger.read(case_id,account,request.currency,start,end)
        if loaded["status"] != "ready":
            return {**output,"status":loaded["status"],"reasons":loaded["reasons"],"account":account}
        by_account[account] = loaded["rows"]
        rows.update((row["id"],row) for row in loaded["rows"])
        opening[account] = principal_ledger.units(conditions.opening_balance)
        if len(rows) > MAX_EVENTS * 2:
            return unknown("network_interval_resource_limit","limited")
    if request.source_flow_id not in rows or not set(request.target_flow_ids) <= rows.keys():
        return unknown("source_or_target_not_in_own_ledgers")
    if rows[request.source_flow_id]["direction"] != "in":
        return unknown("source_must_be_own_credit")
    metadata = event_resolution.ledger_metadata(case_id,list(rows))
    memberships = {item["flow_id"]:item for item in event_resolution.members(case_id,list(rows))}
    keys,events,event_rows,ordered_accounts = {},{},defaultdict(list),{}
    for account, ledger in by_account.items():
        ordered,reason = principal_ledger.ordered(ledger,opening[account],metadata,accounts[account].balance_kind)
        if reason:
            return unknown(reason, "inconsistency" if reason in {"inconsistent_order_constraints","negative_book_balance"} else "unknown")
        ordered_accounts[account] = ordered
        for entry in ordered:
            row = rows[entry["id"]]
            member = memberships.get(entry["id"],{})
            if member.get("status") == "conflict":
                return unknown("conflicting_event_evidence")
            internal = row["payer_account"] in accounts and row["payee_account"] in accounts
            if internal and member.get("status") != "confirmed":
                return unknown("internal_transfer_requires_confirmed_opposite_ledgers")
            key = member.get("event_id") if member.get("status") == "confirmed" else "flow:" + entry["id"]
            keys[entry["id"]] = key
            if key not in events:
                events[key] = {"id":key,"payer":row["payer_account"] if row["payer_account"] in accounts else None,
                               "payee":row["payee_account"] if row["payee_account"] in accounts else None,
                               "amount":row["amount"],"time":row["trade_time"]}
            event_rows[key].append(entry["id"])
            field = "payee_post_balance" if entry["direction"] == "in" else "payer_post_balance"
            events[key][field] = entry["post_balance"]
    for key,event in events.items():
        if event["payer"] is not None and event["payee"] is not None and len(event_rows[key]) != 2:
            return unknown("internal_event_missing_own_entry")
    if len(events) > MAX_EVENTS:
        return unknown("network_event_resource_limit","limited")
    # Merge proved local orders. Tie-breaking is only between independent ready
    # events; shared-account ties have already been proved or rejected above.
    successors,indegree = defaultdict(set),dict.fromkeys(events,0)
    for entries in ordered_accounts.values():
        for left,right in zip(entries,entries[1:]):
            a,b=keys[left["id"]],keys[right["id"]]
            if a != b and b not in successors[a]:
                successors[a].add(b); indegree[b] += 1
    ready=[(events[key]["time"],key) for key,n in indegree.items() if n == 0]
    heapq.heapify(ready)
    sequence=[]
    while ready:
        _,key=heapq.heappop(ready)
        sequence.append({name:value for name,value in events[key].items() if name != "time"})
        for child in successors[key]:
            indegree[child] -= 1
            if not indegree[child]:
                heapq.heappush(ready,(events[child]["time"],child))
    if len(sequence) != len(events):
        return unknown("contradictory_cross_ledger_order","inconsistency")
    targets=list(dict.fromkeys(keys[item] for item in request.target_flow_ids))
    try:
        result=principal_network.allocate_network(opening,sequence,keys[request.source_flow_id],targets)
    except ValueError:
        return unknown("target_must_follow_source_and_have_internal_payer")
    money_fields={"amount","capacity","min","max","fifo","source_amount","external_total","retained_total"}
    money_maps={"event_shares","closing_shares"}
    def wire(value):
        if isinstance(value,list): return [wire(item) for item in value]
        if isinstance(value,dict):
            return {key:principal_ledger.money(item) if key in money_fields and type(item) is int else
                    {str(k):principal_ledger.money(v) for k,v in item.items()} if key in money_maps else wire(item)
                    for key,item in value.items()}
        return value
    result=wire(result)
    for target in result.get("targets",[]):
        event=events[target["id"]]
        target.update(flow_ids=event_rows[target["id"]],payer=event["payer"],payee=event["payee"])
    return {**output,**result,"ledger_count":len(rows),"event_count":len(events),
            "source_flow_id":request.source_flow_id,
            "boundary_note":"分析账户之外为声明的终止边界，不证明现实资金终止；未知回流需补齐账本后重算。",
            "joint_warning":"沿途交易份额可重复经过多条边；独立上界不可相加。本金仅按期末存量与终止流出联合核对。"}
