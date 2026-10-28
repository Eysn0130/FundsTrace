"""Read-only, bounded projections of the effective transactions tracked on a canvas.

These are investigative leads, never an attribution of conserved principal. Time
values remain the local, timezone-free transaction timestamps stored by import.
"""
from collections import defaultdict, deque
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import db, evidence_resolution
from .money import money_string

MAX_FLOWS = 20_000
MAX_PROVENANCE = 80_000
MAX_SEARCH_STEPS = 50_000
MAX_SEARCH_STATES = 10_000


class VisibleEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=512)
    target: str = Field(min_length=1, max_length=512)


class TimeInvestigationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: int = Field(gt=0)
    canvas_id: int = Field(default=0, ge=0)
    scope: Literal["all", "visible"] = "all"
    visible_accounts: list[str] = Field(default_factory=list, max_length=20_000)
    visible_edges: list[VisibleEdge] | None = Field(default=None, max_length=20_000)
    start: datetime | None = None
    end: datetime | None = None
    include_undated: bool = True

    @field_validator("start", "end", mode="before")
    @classmethod
    def local_timestamp(cls, value):
        if value is None:
            return value
        if not isinstance(value, (str, datetime)):
            raise ValueError("时间须为本地交易时间字符串，不接受 Unix 时间数值")
        try:
            parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        except ValueError as error:
            raise ValueError("时间格式无效") from error
        if parsed.tzinfo is not None:
            raise ValueError("交易时间不含时区；请使用原始本地交易时间")
        return parsed

    @field_validator("visible_accounts")
    @classmethod
    def account_lengths(cls, values):
        if any(not item or len(item) > 512 for item in values):
            raise ValueError("可见账号不能为空且不得超过 512 字符")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def valid_window(self):
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("开始时间不能晚于结束时间")
        if self.scope == "visible" and self.visible_edges is None:
            raise ValueError("当前可见范围须明确提供账号及有向关系集合")
        return self


class PathInvestigationIn(TimeInvestigationIn):
    start_account: str = Field(min_length=1, max_length=512)
    end_account: str = Field(min_length=1, max_length=512)
    max_hops: int = Field(default=4, ge=1, le=8)
    max_paths: int = Field(default=20, ge=1, le=50)
    currency: str | None = Field(default=None, max_length=32)
    mode: Literal["topological", "chronological"] = "topological"

    @model_validator(mode="after")
    def different_endpoints(self):
        if self.start_account == self.end_account:
            raise ValueError("起点和终点须为不同账号；本工具查询不重复账号的有向路径")
        return self


def mirror_groups(rows):
    """Existing graph FIFO rule: each row has at most one <=120-second mirror.

    Caller supplies time/id ordered records. SQL's second truncation is retained.
    Values are original flow IDs, not invented transaction identities.
    """
    pending, groups = {}, {}
    tolerance = timedelta(seconds=120)
    for row in rows:
        if row.get("resolution_policy") == "typed-account-evidence-v1":
            continue
        stamp = row.get("mirror_time", row.get("trade_time"))
        if any(row.get(key) is None for key in ("payer_account", "payee_account", "amount")) or stamp is None:
            continue
        if not row["payer_account"] or not row["payee_account"]:
            continue
        stamp = stamp.replace(microsecond=0)
        ledger = row.get("self_account") or ""
        if (not ledger or ledger not in (row["payer_account"], row["payee_account"])
                or row["payer_account"] == row["payee_account"]):
            continue
        key = (row["payer_account"], row["payee_account"], row["amount"], row.get("currency") or "")
        queue = pending.setdefault(key, deque())
        while queue and stamp - queue[0][0] > tolerance:
            queue.popleft()
        if queue and not (ledger and ledger == queue[0][1]):
            _, _, canonical = queue.popleft()
            groups[canonical] = [row["id"]]
        else:
            queue.append((stamp, ledger, row["id"]))
    return groups


def _stamp(value):
    return value.isoformat(sep=" ") if value is not None else None


def _totals(rows):
    totals = defaultdict(lambda: Decimal("0"))
    with localcontext() as context:
        context.prec = 40
        for row in rows:
            totals[row["currency"]] += row["amount"]
    return [{"currency": currency, "amount": money_string(amount)} for currency, amount in sorted(totals.items(), key=lambda item: item[0] or "")]


def _load(body):
    # A bounded full canvas is necessary before mirror matching: filtering first
    # could turn a later ledger mirror into a second transaction.
    rows = db.q_raw("""
        select f.* from effective_flows f
        join tracked_flows t on t.flow_id=f.id and t.case_id=f.case_id
        where f.case_id=? and t.canvas_id=? order by f.trade_time, f.id limit ?
    """, [body.case_id, body.canvas_id, MAX_FLOWS + 1])
    if len(rows) > MAX_FLOWS:
        raise HTTPException(413, {"code": "canvas_investigation_limit", "limit": MAX_FLOWS,
                                  "message": "画布有效流水超过研判上限，请将所需流水另存为较小画布；未返回部分金额或路径"})
    from . import funds_projection, event_resolution
    mirrors = {}
    graphable = [row for row in rows if row["payer_account"] and row["payee_account"]]
    if body.scope == "visible":
        accounts = set(body.visible_accounts)
        allowed = {(edge.source, edge.target) for edge in body.visible_edges}
        if any(source not in accounts or target not in accounts for source, target in allowed):
            raise HTTPException(422, "可见关系的两端须包含在可见账号集合中")
        graphable = [row for row in graphable if (row["payer_account"], row["payee_account"]) in allowed]
    scope_rows, scope_funds = funds_projection.from_rows(body.case_id, graphable)
    canonical = graphable
    selected = []
    for row in canonical:
        stamp = row["trade_time"]
        if stamp is None:
            if body.include_undated:
                selected.append(row)
        elif (body.start is None or stamp >= body.start) and (body.end is None or stamp <= body.end):
            selected.append(row)
    selected_raw = selected
    selected, selected_funds = funds_projection.from_rows(body.case_id, selected_raw)
    memberships = {item["flow_id"]: item for item in event_resolution.members(body.case_id, [row["id"] for row in selected_raw])}
    by_event = defaultdict(list)
    for row in selected_raw:
        member = memberships.get(str(row["id"]), {})
        if member.get("status") == "confirmed":
            by_event[member["event_id"]].append(row["id"])
    for row in selected:
        if row.get("fact_event_id"):
            mirrors[row["id"]] = [item for item in by_event[row["fact_event_id"]] if item != row["id"]]
    dated = [row["trade_time"] for row in scope_rows if row["trade_time"] is not None]
    summary = {
        "tracked_effective_count": len(rows), "scope_raw_count": len(graphable),
        "scope_count": len(scope_rows), "selected_count": len(selected),
        "mirror_duplicate_count": len(graphable) - len(scope_rows),
        "missing_time_count": sum(row["trade_time"] is None for row in scope_rows),
        "excluded_undated_count": sum(row["trade_time"] is None for row in scope_rows) if not body.include_undated else 0,
        "excluded_non_graphable_count": len(rows) - sum(bool(row["payer_account"] and row["payee_account"]) for row in rows),
        "time_min": _stamp(min(dated)) if dated else None, "time_max": _stamp(max(dated)) if dated else None,
        "funds": selected_funds, "scope_funds": scope_funds,
        "amount_totals": funds_projection.public_fields(selected_funds)["amount_totals"],
    }
    return rows, selected, mirrors, summary


def _provenance(case_id, ids):
    if not ids:
        return {}
    rows = db.q_raw("""
        select f.flow_id,f.source_id,f.source_row,f.disposition,s.filename,s.sha256,
               s.sheet,s.row_kind,s.parser_version,r.member_path,r.source_row as member_row
        from flow_sources f join import_sources s on s.source_id=f.source_id and s.case_id=f.case_id
        left join import_records r on r.case_id=f.case_id and r.source_id=f.source_id and r.record_no=f.source_row
        where f.case_id=? and f.flow_id in (select unnest(?::bigint[]))
        order by f.flow_id,f.source_id,f.source_row limit ?
    """, [case_id, list(ids), MAX_PROVENANCE + 1])
    if len(rows) > MAX_PROVENANCE:
        raise HTTPException(413, {"code": "canvas_provenance_limit", "limit": MAX_PROVENANCE,
                                  "message": "来源引用超过研判上限，请缩小画布；未返回不完整来源"})
    grouped = defaultdict(list)
    for row in rows:
        flow_id = row.pop("flow_id")
        row["source_row"] = str(row["source_row"])
        if row["member_row"] is not None:
            row["member_row"] = str(row["member_row"])
        grouped[flow_id].append(row)
    return grouped


def _flow(row, mirrors, references, by_id):
    original_ids = [row["id"], *mirrors.get(row["id"], [])]
    payer_display = row.get("payer_display_account") or row["payer_account"]
    payee_display = row.get("payee_display_account") or row["payee_account"]
    return {
        "id": str(row["id"]), "trade_no": row["trade_no"], "trade_time": _stamp(row["trade_time"]),
        "source": row["payer_account"], "source_name": row["payer_name"],
        "target": row["payee_account"], "target_name": row["payee_name"],
        "source_display_account": payer_display, "target_display_account": payee_display,
        "payer_display_account": payer_display, "payee_display_account": payee_display,
        "self_account": row.get("self_account") or "",
        "self_display_account": row.get("self_display_account") or row.get("self_account") or "",
        "amount": money_string(row["amount"]) if row["amount"] is not None else None, "currency": row["currency"], "summary": row["summary"],
        "granularity": "confirmed_event" if row.get("fact_status") == "confirmed" else "unresolved_observation",
        "observation_count": row.get("observation_count", 1),
        "original_ids": [str(flow_id) for flow_id in original_ids],
        "mirror_ids": [str(flow_id) for flow_id in mirrors.get(row["id"], [])],
        "provenance": [{"flow_id": str(flow_id), "trade_time": _stamp(by_id[flow_id]["trade_time"]),
                        "sources": references.get(flow_id, [])} for flow_id in original_ids],
    }


def _account_metadata(case_id, accounts):
    """Fetch display aliases only for the already bounded investigation scope."""
    rows = [{"account": account} for account in sorted(accounts)]
    evidence_resolution.decorate_accounts(case_id, rows)
    return {row["account"]: row for row in rows}


def _display_account(account, metadata):
    return metadata.get(account, {}).get("display_account") or account


def _resolve_account(token, metadata):
    """An exact ambiguous identifier expands to every in-scope entity match.

    Names are not identifiers. The current scope and case, rather than the full
    registry, establish the set which can enter this path query.
    """
    token = token.strip()
    if token in metadata:
        return {token}
    return {account for account, item in metadata.items()
            if token in {item.get("display_account"), item.get("account_number"), *item.get("cards", [])}}


def _resolution_metadata(case_id):
    from . import event_resolution, analysis_snapshot, funds_projection
    resolution = evidence_resolution.summary(case_id)
    return {"analysis_revision": analysis_snapshot.revision(case_id),
            "case_event_summary": event_resolution.summary(case_id),
            "statistics_policy": funds_projection.POLICY,
            "resolution_revision": resolution["revision"] if resolution is not None else None}


def _graph(rows, mirrors):
    grouped, accounts = defaultdict(list), {}
    for row in rows:
        grouped[(row["payer_account"], row["payee_account"])].append(row)
        for account, name, direction in ((row["payer_account"], row["payer_name"], "out"),
                                         (row["payee_account"], row["payee_name"], "in")):
            node = accounts.setdefault(account, {"account": account, "name": name or "", "in": [], "out": []})
            node["name"] = max(node["name"], name or "")
            node[direction].append(row)
    from . import funds_projection
    nodes = [{"account": account, "name": node["name"],
              **funds_projection.directional_fields(funds_projection.summarize_rows(node["in"]),
                                                     funds_projection.summarize_rows(node["out"]))}
             for account, node in sorted(accounts.items())]
    edges = []
    for (source, target), values in sorted(grouped.items()):
        dated = [row["trade_time"] for row in values if row["trade_time"] is not None]
        funds = funds_projection.summarize_rows(values)
        edges.append({"source": source, "target": target, "source_name": max(row["payer_name"] or "" for row in values),
                      "target_name": max(row["payee_name"] or "" for row in values),
                      "funds": funds, **funds_projection.public_fields(funds),
                      "currencies": [value["currency"] for value in funds["totals"]],
                      "tmin": _stamp(min(dated)) if dated else None, "tmax": _stamp(max(dated)) if dated else None,
                      "ids": [str(flow_id) for row in values for flow_id in [row["id"], *mirrors.get(row["id"], [])]],
                      "summaries": sorted({row["summary"] for row in values if row["summary"]})})
    return nodes, edges


def time_projection(body):
    rows, selected, mirrors, summary = _load(body)
    original_ids = {flow_id for row in selected for flow_id in [row["id"], *mirrors.get(row["id"], [])]}
    references = _provenance(body.case_id, original_ids)
    by_id = {row["id"]: row for row in rows}
    nodes, edges = _graph(selected, mirrors)
    from . import event_resolution, funds_projection
    event_resolution.decorate_edges(body.case_id, edges)
    evidence_resolution.decorate_accounts(body.case_id, nodes)
    metadata = {node["account"]: node for node in nodes}
    for edge in edges:
        edge["source_display_account"] = _display_account(edge["source"], metadata)
        edge["target_display_account"] = _display_account(edge["target"], metadata)
    policy = _resolution_metadata(body.case_id)
    timeline = defaultdict(list)
    for row in selected:
        if row["trade_time"] is not None:
            timeline[row["trade_time"]].append(row)
    return {
        "case_id": str(body.case_id), "canvas_id": str(body.canvas_id), "scope": body.scope,
        "window": {"start": _stamp(body.start), "end": _stamp(body.end), "include_undated": body.include_undated},
        "summary": summary, "funds": summary["funds"], "time_min": summary["time_min"], "time_max": summary["time_max"],
        "nodes": nodes, "edges": edges, "flows": [_flow(row, mirrors, references, by_id) for row in selected],
        "timeline": [{"time": _stamp(stamp), "funds": funds_projection.summarize_rows(values),
                      **funds_projection.public_fields(funds_projection.summarize_rows(values))}
                     for stamp, values in sorted(timeline.items())],
        "complete": True, "limits": {"max_canvas_flows": MAX_FLOWS, "max_provenance": MAX_PROVENANCE},
        **policy,
        "semantics": "selected_confirmed_events_and_unresolved_observations; canonical_local_time; inclusive_window",
    }


def paths(body):
    from . import funds_projection
    from .table_import import _ISO_CURRENCIES
    rows, selected, mirrors, summary = _load(body)
    summary["time_window_count"] = len(selected)
    summary["excluded_by_currency_count"] = 0
    if body.currency is not None:
        summary["excluded_by_currency_count"] = sum(row["currency"] != body.currency for row in selected)
        selected = [row for row in selected if row["currency"] == body.currency]
    if body.mode == "chronological":
        summary["excluded_undated_for_chronology_count"] = sum(row["trade_time"] is None for row in selected)
        summary["excluded_unknown_currency_for_chronology_count"] = sum(row["currency"] not in _ISO_CURRENCIES for row in selected)
        summary["excluded_nonpositive_for_chronology_count"] = sum(row["amount"] is None or row["amount"] <= 0 for row in selected)
        selected = [row for row in selected if row["trade_time"] is not None and row["currency"] in _ISO_CURRENCIES and row["amount"] is not None and row["amount"] > 0]
    summary["selected_count"] = len(selected)
    summary["funds"] = funds_projection.summarize_rows(selected)
    summary["amount_totals"] = funds_projection.public_fields(summary["funds"])["amount_totals"]
    metadata = _account_metadata(body.case_id, {row[side] for row in selected
                                              for side in ("payer_account", "payee_account")})
    start_accounts = _resolve_account(body.start_account, metadata)
    end_accounts = _resolve_account(body.end_account, metadata)
    # A path always stays within one currency, including explicitly unknown "".
    # Topology traverses aggregated directed relations; chronology traverses
    # original canonical occurrences with strictly increasing local timestamps.
    grouped = defaultdict(list)
    for row in selected:
        grouped[(row["payer_account"], row["payee_account"], row["currency"])].append(row)
    adjacency = defaultdict(list)
    for (source, target, currency), values in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1], item[0][2] or "")):
        if body.mode == "chronological":
            for row in values:
                adjacency[source].append((target, currency, [row], row["trade_time"]))
        else:
            adjacency[source].append((target, currency, values, None))
    queue = deque((account, [account], [], None, None) for account in sorted(start_accounts))
    found, examined, reason = [], 0, None
    if len(queue) > MAX_SEARCH_STATES:
        reason = "search_state_limit"
    while queue and reason is None:
        account, visited, steps, currency, last_time = queue.popleft()
        if len(steps) >= body.max_hops:
            continue
        for target, edge_currency, values, stamp in adjacency.get(account, []):
            examined += 1
            if examined > MAX_SEARCH_STEPS:
                reason = "search_step_limit"
                break
            if target in visited or (steps and currency != edge_currency):
                continue
            if body.mode == "chronological" and last_time is not None and stamp <= last_time:
                continue
            next_steps = steps + [(account, target, edge_currency, values)]
            next_visited = visited + [target]
            if target in end_accounts:
                found.append((next_visited, next_steps, edge_currency))
                if len(found) > body.max_paths:
                    found.pop()
                    reason = "result_limit"
                    break
            elif len(next_steps) < body.max_hops:
                if len(queue) >= MAX_SEARCH_STATES:
                    reason = "search_state_limit"
                    break
                queue.append((target, next_visited, next_steps, edge_currency, stamp))
    original_ids = {flow_id for _, steps, _ in found for _, _, _, values in steps for row in values
                    for flow_id in [row["id"], *mirrors.get(row["id"], [])]}
    references = _provenance(body.case_id, original_ids)
    by_id = {row["id"]: row for row in rows}
    result = []
    for accounts, steps, currency in found:
        result.append({"accounts": accounts,
                       "display_accounts": [_display_account(account, metadata) for account in accounts],
                       "hops": len(steps), "currency": currency,
                       "steps": [{"source": source, "target": target, "currency": step_currency,
                                  "source_display_account": _display_account(source, metadata),
                                  "target_display_account": _display_account(target, metadata),
                                  "funds": funds_projection.summarize_rows(values),
                                  "count": funds_projection.public_fields(funds_projection.summarize_rows(values))["cnt"],
                                  "amount": funds_projection.public_fields(funds_projection.summarize_rows(values))["total"],
                                  "flow_ids": [str(row["id"]) for row in values],
                                  "first_time": _stamp(min(row["trade_time"] for row in values if row["trade_time"] is not None))
                                      if any(row["trade_time"] is not None for row in values) else None,
                                  "last_time": _stamp(max(row["trade_time"] for row in values if row["trade_time"] is not None))
                                      if any(row["trade_time"] is not None for row in values) else None}
                                 for source, target, step_currency, values in steps]})
    evidence_ids = {row["id"] for _, steps, _ in found for _, _, _, values in steps for row in values}
    return {"case_id": str(body.case_id), "canvas_id": str(body.canvas_id), "scope": body.scope,
            "start_accounts": sorted(start_accounts), "end_accounts": sorted(end_accounts),
            "start_display_accounts": [_display_account(account, metadata) for account in sorted(start_accounts)],
            "end_display_accounts": [_display_account(account, metadata) for account in sorted(end_accounts)],
            "mode": body.mode, "currency": body.currency, "paths": result, "complete": reason is None,
            "flows": [_flow(row, mirrors, references, by_id) for row in selected if row["id"] in evidence_ids],
            "truncation_reason": reason, "summary": summary,
            "completeness_scope": "simple_paths_within_requested_scope_window_currency_and_max_hops",
            "limits": {"max_hops": body.max_hops, "max_paths": body.max_paths, "search_steps": min(examined, MAX_SEARCH_STEPS),
                       "max_search_steps": MAX_SEARCH_STEPS, "max_search_states": MAX_SEARCH_STATES,
                       "max_canvas_flows": MAX_FLOWS},
            **_resolution_metadata(body.case_id),
            "semantics": "simple_directed_paths; same_currency; " +
                         ("strictly_increasing_local_times; " if body.mode == "chronological" else "topological_only; ") +
                         "no_conserved_principal_attribution"}
