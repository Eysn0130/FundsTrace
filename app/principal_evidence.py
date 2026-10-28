"""Prepare source-backed attribution constraints for existing user actions.

Matching balances and a requester's booleans do not establish ledger coverage,
credit terms or historical availability. Missing evidence stays explicit. This
module performs no allocation policy selection and never writes source facts.
"""
from types import SimpleNamespace
from datetime import datetime

from . import db, event_resolution, principal_ledger


def assess(case_id, rows, metadata=None):
    if metadata is None:
        metadata = event_resolution.ledger_metadata(case_id, [row["id"] for row in rows])
    refs = sorted({source for item in metadata.values() for source in item.get("source_ids", [])})
    conditions = {"coverage": "unknown", "no_overdraft": False, "funds_available": False,
                  "opening_balance": None, "balance_kind": "unknown"}
    assessment = [
        {"condition": "coverage", "status": "unknown", "kind": "missing_evidence", "source_ids": [],
         "message": "缺少可证明该账户区间收支完整的请求范围与完整反馈依据"},
        {"condition": "no_overdraft", "status": "unknown", "kind": "missing_evidence", "source_ids": [],
         "message": "缺少该区间的透支或授信依据"},
        {"condition": "funds_available", "status": "unknown", "kind": "missing_evidence", "source_ids": [],
         "message": "缺少历史可用资金、冻结及在途资金依据"},
    ]
    book = bool(rows) and all(metadata.get(row["id"], {}).get("balance_kind") == "post_book" for row in rows)
    if book:
        conditions["balance_kind"] = "post_book"
    assessment.append({"condition": "balance_kind", "status": "established" if book else "unknown",
        "kind": "source_declaration" if book else "missing_evidence", "source_ids": refs if book else [],
        "message": "来源明确声明本笔交易后账面余额" if book else "余额字段尚未明确为本笔交易后账面余额"})
    # The source-ordered first posting anchors the recorded model, not the
    # requested interval's start or any unobserved history before this posting.
    opening = None
    first, opening_error = None, None
    if rows and rows[0].get("trade_time") is not None:
        first_group = []
        for row in rows:
            if row["trade_time"] != rows[0]["trade_time"]:
                break
            first_group.append(row)
        ordered = principal_ledger.source_order(first_group, metadata)
        first = ordered[0] if ordered else None
    if first:
        if metadata.get(first["id"], {}).get("balance_kind") == "post_book" and first.get("balance") is not None and first.get("direction"):
            try:
                value = principal_ledger.units(first["balance"]) - first["amount"] * (1 if first["direction"] == "in" else -1)
                if value >= 0:
                    opening = principal_ledger.money(value)
                else:
                    opening_error = "negative_opening_book_balance"
            except (ValueError, TypeError):
                pass
    negative = [row for row in rows if metadata.get(row["id"], {}).get("balance_kind") == "post_book"
                and row.get("balance") is not None and row["balance"] < 0]
    if negative:
        assessment.append({"condition": "book_balance", "status": "inconsistency", "kind": "source_declaration",
            "reason": "negative_book_balance", "source_ids": sorted({source for row in negative
                for source in metadata.get(row["id"], {}).get("source_ids", [])}),
            "message": "来源声明的负账面余额不适用于非负账面容量模型"})
    conditions["opening_balance"] = opening
    assessment.append({"condition": "opening_balance", "status": "inconsistency" if opening_error else "established" if opening is not None else "unknown",
        "kind": "derived_from_source" if opening is not None or opening_error else "missing_evidence",
        "source_ids": metadata.get(first["id"], {}).get("source_ids", []) if first else [],
        **({"reason": opening_error} if opening_error else {}),
        **({"flow_id": first["id"], "trade_time": first["trade_time"].isoformat()} if first else {}),
        "message": "由首笔明确的交易后账面余额及方向反推紧邻前余额" if opening is not None else
            "来源反推首记录前余额为负，不适用于非负账面容量模型" if opening_error else "首条记录紧邻前的账面余额未知"})
    return conditions, assessment


def book_scope(assessment):
    opening = next(item for item in assessment if item["condition"] == "opening_balance")
    return {"kind": "recorded_book_ledger", "entries": "all_recorded_own_entries_in_requested_interval",
            "boundary": "first_exit",
            "opening_boundary": "immediately_before_first_recorded_entry",
            "opening_record": {key: opening[key] for key in ("flow_id", "trade_time") if key in opening},
            "capacity": "nonnegative_book_balance", "order": "source_time_and_scoped_posting_order",
            "real_world_attribution": "unknown",
            "note": "仅对已记录账面模型计算条件范围与FIFO，来源标签首次出账后不因再入账恢复；未证明现实区间完整、历史可用资金或实际来源。"}


def context_result(case_id, account, anchor, visible_rows, *, own_entry=...):
    """Use the existing anchor/window only to choose results, never coverage."""
    currency = anchor.get("currency") or ""
    source_id = str(anchor["id"])
    if anchor.get("payee_account") != account:
        return {"status": "unknown", "currency": currency, "targets": [], "condition_basis": "source_evidence",
            "assessment": [{"condition": "source", "status": "unknown", "kind": "missing_evidence", "source_ids": [],
                            "message": "当前锚点是出账；匹配评分不能指定其中某笔入账为已确认来源"}]}
    # An opposite observation can identify the own entry only through a
    # confirmed event. Similar amount/time or a copied balance is insufficient.
    if own_entry is ...:
        from . import ledger_navigation
        own_entry = ledger_navigation.own_anchor(case_id, account, anchor)
    if own_entry:
        source_id = str(own_entry["id"])
    targets = list(dict.fromkeys(str(row["id"]) for row in visible_rows
        if row.get("self_account") == account and row.get("payer_account") == account
        and row.get("payee_account") != account and row.get("currency") == currency))
    if len(targets) > 20:
        return {"status": "limited", "currency": currency, "targets": [], "condition_basis": "source_evidence",
            "assessment": [{"condition": "target_limit", "status": "limited", "kind": "resource_limit", "source_ids": [],
                            "message": "当前明细超过一次份额计算的目标上限，未将截断结果作为完整结论"}]}
    span = db.q_raw("""select min(trade_time) lo,max(trade_time) hi from effective_flows
        where case_id=? and self_account=? and currency=?""", [case_id, account, currency])[0]
    if span["lo"] is None or span["hi"] is None:
        return {"status": "unknown", "currency": currency, "targets": [], "condition_basis": "source_evidence",
            "assessment": [{"condition": "own_ledger", "status": "unknown", "kind": "missing_evidence", "source_ids": [],
                            "message": "未取得该账务账户的本方账簿；对手观察和余额不能替代"}]}
    request = SimpleNamespace(account=account, currency=currency, source_flow_id=source_id, target_flow_ids=targets,
        start=span["lo"].isoformat(), end=span["hi"].isoformat())
    return principal_ledger.calculate(case_id, request)


def tracking_result(case_id, account, anchor, candidates, direction):
    """Allocate only the anchor/candidates the existing action identifies.

    Reverse tracking labels each credit independently. It does not pick one
    credit as the actual source or sum independently attainable upper bounds.
    The complete own ledger (including untracked competitors) is read once.
    """
    from . import analysis_snapshot, ledger_navigation
    revision = analysis_snapshot.revision(case_id)
    if direction == "target":
        value = context_result(case_id, account, anchor, candidates)
        return {**value, "revision": revision}
    own = ledger_navigation.own_anchor(case_id, account, anchor)
    currency = anchor.get("currency") or ""
    sources = {str(row["id"]): row for row in candidates
               if row.get("self_account") == account and row.get("payee_account") == account
               and row.get("payer_account") != account and row.get("currency") == currency}
    result = {"currency": currency, "revision": revision, "status": "unknown", "targets": [],
              "source_candidates": [], "candidate_semantics": "independent_conditional_source_shares",
              "reasons": []}
    if not own or own.get("payer_account") != account:
        return {**result, "reasons": ["target_not_in_own_ledger"]}
    if not sources:
        return {**result, "reasons": ["no_proven_earlier_credit_candidates"]}
    if len(sources) > 20:
        return {**result, "status": "limited", "reasons": ["source_candidate_resource_limit"]}
    span = db.q_raw("""select min(trade_time) lo,max(trade_time) hi from effective_flows
        where case_id=? and self_account=? and currency=?""", [case_id, account, currency])[0]
    if span["lo"] is None or span["hi"] is None:
        return {**result, "reasons": ["no_own_ledger_entries"]}
    request = SimpleNamespace(account=account, currency=currency, source_flow_id=next(iter(sources)),
        target_flow_ids=[str(own["id"])], start=span["lo"].isoformat(), end=span["hi"].isoformat())
    prepared = principal_ledger.prepare(case_id, request)
    if len(prepared[1]) * 3 * len(sources) > principal_ledger.MAX_WITNESS_CELLS:
        return {**result, "status": "limited", "reasons": ["witness_resource_limit"]}
    values = []
    for source_id in sources:
        request.source_flow_id = source_id
        value = principal_ledger.calculate(case_id, request, prepared=prepared)
        # The original detail exposes amounts and limits, not a repeated full
        # witness for every candidate. The revision-bound analysis API retains it.
        value.pop("fifo_witness", None)
        values.append({**value, "source_flow_id": source_id})
    statuses = {item["status"] for item in values}
    return {**result, "status": "ok" if statuses <= {"ok", "determined"} else "unknown",
            "source_candidates": values}


def path_result(case_id, body, path, by_id, *, budget=None):
    """Evaluate a path's explicit first postings in one complete book network.

    The path's intermediate accounts define this model's first-exit boundary;
    its first and last relationships select source candidates and targets only.
    Every recorded own entry in the interval participates, including untracked
    branches and internal returns. Different candidates are never combined.
    Callers hold the case snapshot lock and share ``remaining`` across paths.
    """
    from . import analysis_snapshot, ledger_navigation
    from .principal_routes import NetworkIn, calculate_network

    revision = analysis_snapshot.revision(case_id)
    accounts = list(dict.fromkeys(path.get("accounts", [])[1:-1]))
    currency = path.get("currency") or ""
    output = {"status": "unknown", "revision": revision, "currency": currency,
        "source_candidates": [], "reasons": [], "condition_basis": "source_evidence",
        "candidate_semantics": "independent_conditional_source_shares",
        "model_scope": {"kind": "recorded_book_network", "boundary": "first_exit",
            "accounts": accounts, "account_selection": "path_intermediate_accounts",
            "entries": "all_recorded_own_entries_in_requested_interval",
            "real_world_attribution": "unknown",
            "note": "以该路径内部账户集合为首次退出边界；完整账面竞争与内部回转参与，边界外再入不恢复来源标签。"},
        "joint_warning": "各首笔候选、不同路径及各目标的上界不可相加；沿途交易额不是本金，FIFO不是实际来源证明。"}

    def unavailable(reason, *, limited=False):
        return {**output, "status": "limited" if limited else "unknown", "reasons": [reason]}

    if not accounts:
        return unavailable("path_has_no_internal_ledger")
    if len(accounts) > 20:
        return unavailable("path_account_resource_limit", limited=True)
    if currency.strip().casefold() in principal_ledger.UNKNOWN_CURRENCIES:
        return unavailable("currency_evidence_unknown")
    steps = path.get("steps", [])
    if len(steps) < 2:
        return unavailable("path_has_no_internal_ledger")

    def own_postings(step, account, direction):
        observed, mapped = {}, {}
        for flow_id in step.get("flow_ids", []):
            original = by_id.get(flow_id)
            if original is None:
                original = by_id.get(str(flow_id))
            if original is None and str(flow_id).isascii() and str(flow_id).isdigit():
                original = by_id.get(int(flow_id))
            if original is None:
                return None
            own = ledger_navigation.own_anchor(case_id, account, original)
            if (not own or own.get("currency") != currency
                    or own.get("payee_account" if direction == "in" else "payer_account") != account
                    or own.get("payer_account") == own.get("payee_account")):
                return None
            stamp = own.get("trade_time")
            stamp = datetime.fromisoformat(stamp) if isinstance(stamp, str) else stamp
            if (stamp is None or body.start is not None and stamp < body.start
                    or body.end is not None and stamp > body.end):
                return None
            identifier = str(own["id"])
            mapped[identifier] = own
            observed.setdefault(identifier, []).append(str(flow_id))
        return (mapped, observed) if mapped else None

    sources = own_postings(steps[0], accounts[0], "in")
    if sources is None:
        return unavailable("source_own_entry_not_established")
    targets = own_postings(steps[-1], accounts[-1], "out")
    if targets is None:
        return unavailable("target_own_entry_not_established")
    source_rows, source_observations = sources
    target_rows, target_observations = targets
    if len(source_rows) > 20 or len(target_rows) > 20:
        return unavailable("path_candidate_resource_limit", limited=True)
    budget = {"remaining": 20} if budget is None else budget
    remaining = min(20, budget.get("remaining", 0))
    if len(source_rows) > remaining:
        return unavailable("path_principal_budget_limit", limited=True)

    span = db.q_raw("""select min(f.trade_time) lo,max(f.trade_time) hi from flows f
        left join resolved_endpoints s on s.case_id=f.case_id and s.flow_id=f.id and s.role='self'
        where f.case_id=? and f.currency=?
          and coalesce(s.entity_id,f.self_account) in (select unnest(?::varchar[]))""",
        [case_id, currency, accounts])[0]
    start, end = body.start or span["lo"], body.end or span["hi"]
    if start is None or end is None or start > end:
        return unavailable("no_own_ledger_entries_in_window")
    values = []
    for source_id in sorted(source_rows, key=int):
        request = NetworkIn(case_id=str(case_id), currency=currency,
            start=start.isoformat(), end=end.isoformat(), source_flow_id=source_id,
            target_flow_ids=sorted(target_rows, key=int),
            accounts=[{"account": account} for account in accounts], expected_revision=revision)
        budget["remaining"] = remaining = remaining - 1
        value = calculate_network(request)
        values.append({**value, "source_flow_id": source_id,
            "source_observation_ids": source_observations[source_id],
            "target_observation_ids": target_observations})
    statuses = {value["status"] for value in values}
    status = "ok" if statuses <= {"ok", "determined"} else "limited" if "limited" in statuses else "unknown"
    return {**output, "status": status, "source_candidates": values,
            "reasons": sorted({reason for value in values for reason in value.get("reasons", [])})}
