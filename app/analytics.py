# -*- coding: utf-8 -*-
"""智能分析算法集：资金追踪第一梯队（图结构）+ 第二梯队（统计模式）

所有算法基于当前案件的已确认事件和未决观察，输出 JSON 可序列化的结构化结果。
设计原则：可解释优先——每个结果都附带可用于办案的量化依据。
"""
import re
from bisect import insort
from collections import defaultdict, deque
from datetime import datetime as _dt
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from itertools import groupby

from .money import money_decimal
from . import funds_projection

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
ALGORITHMS = ("cycles", "fast_flows", "outliers", "paths")
MONEY_CONTRACT = {
    "version": "funds-money.v1",
    "amounts": "exact decimal strings with two fractional digits",
    "currency": "independent groups; no implicit conversion or cross-currency pairing",
    "totals": "evidence-bounded sum of distinct transaction events; unresolved observations are ranges, not conserved principal",
    "temporal_basis": "recorded timestamps; equal times require shared own-ledger source order; rule candidates, not real-world attribution",
    "avg_amount": "decimal mean rounded to cents using ROUND_HALF_EVEN",
    "approximate_metrics": ["z", "sigma", "span_days", "max_delay_min", "tolerance", "return_ratio"],
}


def _pt(s):
    return s if isinstance(s, _dt) else _dt.fromisoformat(s)


def _load_posting_orders(case_id, flows):
    """Read source order for observations that connect or compete at equal times.

    A confirmed event can have an own entry on either ledger. Its representative
    ID is only a display choice, so look up the shared account's original entry.
    """
    occurrences = defaultdict(int)
    for row in flows:
        if row["trade_time"]:
            for account in (row["payer_account"], row["payee_account"]):
                if account:
                    occurrences[account, row["currency"], row["trade_time"]] += 1
    tied = [row for row in flows if row["trade_time"] and (
        occurrences[row["payee_account"], row["currency"], row["trade_time"]] > 1
        or occurrences[row["payer_account"], row["currency"], row["trade_time"]] > 1)]
    if not tied:
        return
    ids = [row["id"] for row in tied]
    events = list({row["fact_event_id"] for row in tied if row.get("fact_event_id")})
    entries = db_q("""select cast(f.id as varchar) id,f.self_account,m.event_id
        from effective_flows f left join event_members m
          on m.case_id=f.case_id and m.flow_id=f.id and m.status='confirmed'
        where f.case_id=? and (f.id in (select unnest(?::bigint[]))
            or m.event_id in (select unnest(?::varchar[])))""", [case_id, ids, events])
    from . import event_resolution
    metadata = event_resolution.ledger_metadata(case_id, [row["id"] for row in entries])
    by_fact = defaultdict(lambda: defaultdict(list))
    for row in entries:
        if row["self_account"]:
            key = ("event", row["event_id"]) if row["event_id"] else ("flow", row["id"])
            by_fact[key][row["self_account"]].append({"id": row["id"], **metadata.get(row["id"], {})})
    for row in tied:
        key = ("event", row["fact_event_id"]) if row.get("fact_event_id") else ("flow", str(row["id"]))
        row["_ledger_orders"] = {account: entries[0] for account, entries in by_fact[key].items()
                                  if len(entries) == 1}


def _time_relation(left, right, account):
    """-1 means proved earlier, 1 later; equal-time uncertainty stays None."""
    if not left.get("trade_time") or not right.get("trade_time"):
        return None
    a = left.get("_parsed_time") or _pt(left["trade_time"])
    b = right.get("_parsed_time") or _pt(right["trade_time"])
    if a != b:
        return -1 if a < b else 1
    first, second = (row.get("_ledger_orders", {}).get(account) for row in (left, right))
    if not first or not second or first["id"] == second["id"]:
        return None
    from . import principal_ledger
    ordered = principal_ledger.source_order([first, second], {item["id"]: item for item in (first, second)})
    return (-1 if ordered[0]["id"] == first["id"] else 1) if ordered else None


def _sequence_timing(flows):
    relations = [_time_relation(left, right, left["payee_account"])
                 for left, right in zip(flows, flows[1:])]
    status = ("reversed" if 1 in relations else "unknown" if None in relations
              or any(not row.get("trade_time") for row in flows) else "continuous")
    return {"continuous": None if status == "unknown" else status == "continuous", "temporal_status": status}


def load_flows(case_id):
    rows = db_q(funds_projection.cte("select * from effective_flows where case_id = ?") +
                "select f.*, "
                "(select json_extract_string(e.details_json,'$.cards[*]') from resolution_entities e where e.case_id=f.case_id and e.entity_id=f.payer_account) payer_aliases, "
                "(select json_extract_string(e.details_json,'$.cards[*]') from resolution_entities e where e.case_id=f.case_id and e.entity_id=f.payee_account) payee_aliases, "
                "(select e.account_number from resolution_entities e where e.case_id=f.case_id and e.entity_id=f.payer_account) payer_account_number, "
                "(select e.account_number from resolution_entities e where e.case_id=f.case_id and e.entity_id=f.payee_account) payee_account_number "
                "from selected_facts f order by f.trade_time, f.id", [case_id])
    for row in rows:
        row["amount"] = money_decimal(row["amount"]) if row["amount"] is not None else None
        row["balance"] = money_decimal(row["balance"]) if row["balance"] is not None else None
        row["_parsed_time"] = _pt(row["trade_time"]) if row["trade_time"] is not None else None
        if isinstance(row["trade_time"], _dt):
            row["trade_time"] = row["trade_time"].isoformat(sep=" ")
        elif row["trade_time"] is None:
            row["trade_time"] = ""
    _load_posting_orders(case_id, rows)
    return rows


def _project_results(rows, result_key, group):
    """Expose evidence ranges without turning observed rule candidates into facts."""
    if rows and any("fact_status" not in row for row in rows):
        return
    by_id = {str(row["id"]): row for row in rows}

    def project(selected):
        return funds_projection.summarize_rows(selected)

    def scalar(funds):
        value = funds_projection.public_fields(funds)["total"]
        return money_decimal(value) if value is not None else None

    def leg(value):
        selected = by_id.get(str(value.get("id")))
        if selected is None:
            return
        value["funds"] = project([selected])
        value["granularity"] = "confirmed_event" if selected["fact_status"] == "confirmed" else "unresolved_observation"
        value["observation_count"] = selected["observation_count"]
        value["observation_amount"] = selected.get("observation_amount", selected["amount"])
        value["amount"] = scalar(value["funds"])

    group["funds"] = project(rows)
    group["statistics_policy"] = funds_projection.POLICY
    for item in group[result_key]:
        if result_key in {"paths", "cycles"}:
            legs = item["path" if result_key == "paths" else "edges"]
            selected = [by_id[key] for key in dict.fromkeys(str(value["id"]) for value in legs)]
            item["funds"] = project(selected)
            item["total"] = scalar(item["funds"])
            item["metric_basis"] = "confirmed_events" if item["funds"]["status"] == "exact" else "observation_clue"
            for value in legs:
                leg(value)
            if result_key == "cycles":
                item["first_outflow"] = legs[0]["amount"]
                item["last_inflow"] = legs[-1]["amount"]
        elif result_key == "pairs":
            incoming, outgoing = item["in_flow"], item["out_flows"]
            item["in_funds"] = project([by_id[str(incoming["id"])]])
            item["out_funds"] = project([by_id[str(value["id"])] for value in outgoing])
            item["funds"] = project([by_id[str(value["id"])] for value in [incoming, *outgoing]])
            item["out_total"] = scalar(item["out_funds"])
            item["metric_basis"] = "confirmed_events" if item["funds"]["status"] == "exact" else "observation_clue"
            leg(incoming)
            for value in outgoing:
                leg(value)
            if item["metric_basis"] != "confirmed_events":
                item["diff"] = None
        elif result_key == "outliers":
            selected = [row for row in rows if item["account"] in (row["payer_account"], row["payee_account"])]
            item["funds"] = project(selected)
            item["metric_basis"] = "confirmed_events" if item["funds"]["status"] == "exact" else "observation_clue"
            if item["metric_basis"] == "observation_clue":
                item["observation_value"] = item["value"]
                item["value"] = None


def _grouped(flows, algorithm, result_key, **options):
    """Maintain legacy arrays, with currency-tagged rows and independent groups.

    Limits and statistical baselines apply within each currency. There is no
    ranking of amounts from different currencies and no conversion to CNY.
    """
    groups = defaultdict(list)
    for flow in flows:
        groups[flow.get("currency") or ""].append(flow)
    by_currency = []
    combined = []
    # DECIMAL(18,2) rows can produce larger totals; do not inherit a small
    # process-wide Decimal precision when summing or calculating means.
    with localcontext() as context:
        context.prec = 50
        for currency, rows in sorted(groups.items()):
            candidates = [row for row in rows if row["amount"] is not None and row["amount"] >= 0
                          and (result_key not in {"cycles", "pairs"} or row.get("trade_time"))]
            group = algorithm(candidates, **options)
            candidate_ids = {row["id"] for row in candidates}
            group["unassessed_observations"] = sum(row.get("observation_count", 1) for row in rows
                                                   if row["id"] not in candidate_ids)
            _project_results(rows, result_key, group)
            for result in group[result_key]:
                result["currency"] = currency
            group["currency"] = currency
            by_currency.append(group)
            combined.extend(group[result_key])
    base = dict(next((group for group in by_currency if group[result_key]),
                     by_currency[0] if by_currency else algorithm([], **options)))
    base.pop("currency", None)
    if by_currency and "search" in base:
        searches = [group["search"] for group in by_currency]
        base["search"] = {key: all(search[key] for search in searches) if key == "complete"
                          else any(search[key] for search in searches) if isinstance(searches[0][key], bool)
                          else sum(search[key] for search in searches)
                          for key in searches[0]}
        base["truncated"] = any(group["truncated"] for group in by_currency)
    result = {**base, result_key: combined, "count": len(combined),
              "by_currency": by_currency, "money_contract": MONEY_CONTRACT,
              "unassessed_observations": sum(group.get("unassessed_observations", 0) for group in by_currency)}
    if not flows or all("fact_status" in row for row in flows):
        result["funds"] = funds_projection.summarize_rows(flows)
    return result


# db 由 analysis_routes 注入，避免循环导入
db_q = None


def set_db(q):
    global db_q
    db_q = q


def build_adjacency(flows):
    """正向邻接：account -> [(dst, amount, flow)]；同时返回户名表与反向邻接"""
    adj = defaultdict(list)
    radj = defaultdict(list)
    names = {}
    for f in flows:
        p, q2 = f["payer_account"], f["payee_account"]
        if not p or not q2:
            continue
        amt = f["amount"] or ZERO
        adj[p].append((q2, amt, f))
        radj[q2].append((p, amt, f))
        if not names.get(p):
            names[p] = f["payer_name"] or ""
        if not names.get(q2):
            names[q2] = f["payee_name"] or ""
    return adj, radj, names


# ================= 1. 多跳资金路径追踪 =================

PATH_ORDERS = ("amount", "time")
PATH_SEARCH_BUDGET = 100_000


def resolve_endpoints(token, flows):
    """起点／终点输入解析：账号精确匹配优先，其次按户名精确匹配全部账号。

    户名可能对应多个账号（同名账户）；调用方按集合处理，不丢成员。
    """
    token = str(token or "").strip()
    if not token:
        return set()
    accounts = {flow[key] for flow in flows for key in ("payer_account", "payee_account") if flow[key]}
    if token in accounts:
        return {token}
    aliases = {flow[side + "_account"] for flow in flows for side in ("payer", "payee")
               if flow.get(side + "_account") and token in {flow.get(side + "_display_account"), flow.get("raw_" + side + "_account"), flow.get(side + "_account_number"), *(flow.get(side + "_aliases") or [])}}
    if aliases:
        return aliases
    return {flow[account] for flow in flows
            for account, name in (("payer_account", "payer_name"), ("payee_account", "payee_name"))
            if flow[account] and (flow[name] or "").strip() == token}


def _path_options(flows, src, dst, order):
    """一次解析两个端点，供 trace_paths 与 run_all 共用（各币种共用同一集合）。"""
    order = order if order in PATH_ORDERS else "amount"
    _adj, _radj, names = build_adjacency(flows)
    src_token, dst_token = str(src or "").strip(), str(dst or "").strip()
    return {"order": order,
            "src_accounts": sorted(resolve_endpoints(src_token, flows)),
            "dst_accounts": sorted(resolve_endpoints(dst_token, flows)),
            "src_name": names.get(src_token, ""), "dst_name": names.get(dst_token, "")}


def trace_paths(case_id, src, dst, max_hops=4, max_paths=50, neighbor_limit=20, order="amount"):
    """枚举同币种 src 到 dst 的资金路径；两端均可写账号或户名。

    order="amount" 保持既有金额降序；order="time" 按首跳时间升序，时序连续的
    路径排在前面。两端解析在整个案件上完成一次，避免各币种分组解析出不同集合。
    """
    flows = load_flows(case_id)
    options = _path_options(flows, src, dst, order)
    return _grouped(flows, _trace_paths, "paths", src=src, dst=dst, max_hops=max_hops,
                    max_paths=max_paths, neighbor_limit=neighbor_limit, **options)


def _trace_paths(flows, src, dst, max_hops, max_paths, neighbor_limit, order="amount",
                 src_accounts=None, dst_accounts=None, src_name="", dst_name=""):
    adj, radj, names = build_adjacency(flows)
    by_id = {row["id"]: row for row in flows}
    srcs = set(src_accounts) if src_accounts is not None else resolve_endpoints(src, flows)
    dsts = set(dst_accounts) if dst_accounts is not None else resolve_endpoints(dst, flows)
    search = {"budget": PATH_SEARCH_BUDGET, "steps": 0, "budget_exhausted": False,
              "neighbor_pruned": False, "start_pruned": False, "complete": True,
              "matches_considered": 0, "result_limited": False}
    result = {"src": src, "dst": dst, "src_name": src_name, "dst_name": dst_name,
              "src_accounts": sorted(srcs), "dst_accounts": sorted(dsts), "order": order,
              "max_hops": max_hops, "paths": [], "count": 0, "search": search, "truncated": False}
    if not srcs:
        return {**result, "note": "起点账号或户名在当前案件中没有匹配到账户"}
    if not dsts:
        return {**result, "note": "终点账号或户名在当前案件中没有匹配到账户"}
    # 多终点反向可达集剪枝：只扩展能到达任一终点的节点
    reach = set(dsts)
    q = deque(dsts)
    while q:
        u = q.popleft()
        for v, _a, _f in radj.get(u, []):
            if v not in reach:
                reach.add(v)
                q.append(v)
    reached = [s for s in sorted(srcs) if s in reach]
    if not reached:
        return {**result, "note": "起点无法到达终点"}

    # Keep only the best display rows, while continuing the bounded search.
    results = []
    outgoing = {}

    def outs(node):
        if node not in outgoing:
            choices = [item for item in adj.get(node, []) if item[0] in reach]
            direct = [item for item in choices if item[0] in dsts]
            others = [item for item in choices if item[0] not in dsts]
            key = (lambda item: (item[2]["trade_time"], -item[1])) if order == "time" else (lambda item: -item[1])
            others.sort(key=key)
            if len(others) > neighbor_limit:
                search["neighbor_pruned"] = True
            outgoing[node] = direct + others[:neighbor_limit]
            if order == "time":
                outgoing[node].sort(key=key)
        return outgoing[node]

    def dfs(node, path, total, visited):
        if path and node in dsts:
            steps = list(path)
            times = [step["time"] or "" for step in steps]
            item = {
                "path": steps, "total": round(total, 2), "hops": len(steps),
                "start_time": times[0], "end_time": times[-1],
                **_sequence_timing([by_id[step["id"]] for step in steps])}
            search["matches_considered"] += 1
            rank = ((0 if item["continuous"] else 1, item["start_time"] or "~", -total)
                    if order == "time" else (-total,))
            insort(results, (rank, search["matches_considered"], item))
            if len(results) > max_paths:
                results.pop()
                search["result_limited"] = True
            return
        if len(path) >= max_hops:
            return
        for nxt, amt, f in outs(node):
            if search["steps"] >= search["budget"]:
                search["budget_exhausted"] = True
                return
            search["steps"] += 1
            if nxt in visited:
                continue
            visited.add(nxt)
            path.append({"id": f["id"], "from": node, "from_name": names.get(node, ""), "to": nxt,
                         "to_name": names.get(nxt, ""), "amount": amt,
                         "time": f["trade_time"], "no": f["trade_no"] or "",
                         "currency": f["currency"]})
            dfs(nxt, path, total + amt, visited)
            path.pop()
            visited.discard(nxt)
            if search["budget_exhausted"]:
                return

    if order == "time":
        reached.sort(key=lambda start: min((item[2]["trade_time"] for item in adj.get(start, [])
                                           if item[0] in reach), default="~"))
    for start in reached:
        dfs(start, [], ZERO, {start})
        if search["budget_exhausted"]:
            break
    search["complete"] = not (search["budget_exhausted"] or search["neighbor_pruned"])
    return {**result, "count": len(results), "paths": [item for _rank, _serial, item in results],
            "truncated": not search["complete"] or search["result_limited"]}


# ================= 2. 环路检测（资金回流） =================

def find_cycles(case_id, max_len=4, max_cycles=30, min_total=0, window_days=7,
                budget=1_500_000, start_top=300, subjects=None, *, _flows=None):
    """检测资金闭环（对敲/回流特征）。只考虑进出均有的账户，环规范化去重。
    时间约束：环上记录须落在 window_days 天内，排除已证实的逆序；同刻缺序的
    候选明确标为未知，不据此认定真实资金链。刻意对敲通常
    短窗完成，跨月才凑成的闭环更可能是正常经营往来。结果按"回转比例"（最后一笔
    回流 / 第一笔转出）接近 1 的程度排序，其次按金额合计降序。
    subjects：自定义分析对象（账号或户名列表）。给定后保留包含这些账户的环路，
    展示起点仍为真实时序起点。无 subjects 时只从金额最大的前 start_top 个账户
    发起。预算限制检查的交易边数，search 标明未完整搜索及显示限额。"""
    min_total = money_decimal(min_total)
    flows = load_flows(case_id) if _flows is None else _flows
    subject_accounts = (set().union(*(resolve_endpoints(subject, flows) for subject in subjects))
                        if subjects else None)
    return _grouped(flows, _find_cycles, "cycles", max_len=max_len,
                    max_cycles=max_cycles, min_total=min_total, window_days=window_days,
                    budget=budget, start_top=start_top, subjects=subjects, subject_accounts=subject_accounts)


def _find_cycles(flows, max_len, max_cycles, min_total, window_days, budget, start_top, subjects,
                 subject_accounts=None):
    adj, radj, names = build_adjacency(flows)
    by_id = {row["id"]: row for row in flows}
    indeg = defaultdict(int)
    for s, edges in adj.items():
        for d, _a, _f in edges:
            indeg[d] += 1
    node_out = {x: sum((a for (_, a, _f) in adj[x]), ZERO) for x in adj}
    candidates = [x for x in adj if indeg.get(x, 0) >= 1 and node_out[x] > 0]
    candidates.sort(key=lambda x: -node_out[x])
    eligible = set(candidates)
    search = {"budget": budget, "steps": 0, "budget_exhausted": False,
              "neighbor_pruned": False, "start_pruned": False, "complete": True,
              "matches_considered": 0, "result_limited": False}
    selected = None
    to_subject = {}
    if subjects:
        selected = (set(subject_accounts) if subject_accounts is not None
                    else set().union(*(resolve_endpoints(subject, flows) for subject in subjects))) & eligible

        def distances(graph):
            found = dict.fromkeys(selected, 0)
            queue = deque(selected)
            while queue:
                node = queue.popleft()
                if found[node] >= max_len - 1:
                    continue
                for neighbor, _amount, _flow in graph.get(node, []):
                    if neighbor in eligible and neighbor not in found:
                        found[neighbor] = found[node] + 1
                        queue.append(neighbor)
            return found

        # A chronological origin can precede the selected account. Search only
        # nodes structurally able to share a short cycle with a selected node.
        to_subject = distances(radj)
        eligible &= distances(adj).keys() & to_subject.keys()
        candidates = [account for account in candidates if account in eligible]
        candidates.sort(key=lambda account: min((flow["trade_time"] for target, _amount, flow in adj[account]
                                                 if target in eligible), default="~"))
    else:
        search["start_pruned"] = len(candidates) > start_top
        candidates = candidates[:start_top]
    outgoing = {account: [edge for edge in adj[account] if edge[0] in eligible] for account in eligible}

    # Bound retained memory by the display limit. A later transaction sequence
    # can replace an earlier representative of the same account cycle.
    best = {}
    ranks = {}
    parsed_times = {}

    def parsed_time(value):
        if value not in parsed_times:
            parsed_times[value] = _pt(value)
        return parsed_times[value]

    def canon(nodes_seq):
        """环规范化：以最小账户名为起点的旋转"""
        k = nodes_seq.index(min(nodes_seq))
        return tuple(nodes_seq[k:] + nodes_seq[:k])

    def closeness(cycle):
        first, last = cycle["first_outflow"], cycle["last_inflow"]
        with localcontext() as context:
            context.prec = 50
            distance = abs(first - last) / first if first else ZERO
        return (0 if first else 1, distance, -cycle["total"],
                tuple(edge["id"] for edge in cycle["edges"]))

    def dfs(start, node, nodes_seq, edges_seq, depth, total):
        if selected is not None and not selected.intersection(nodes_seq) and to_subject[node] > max_len - depth:
            return
        for nxt, amt, f in outgoing.get(node, []):
            if search["steps"] >= budget:
                search["budget_exhausted"] = True
                return
            search["steps"] += 1
            # 时间顺序在遍历时就剪枝：早于上一跳的流水不可能出现在同一条
            # 按时间走完的资金链上，直接跳过（比事后拒绝整个环快得多）。
            if edges_seq and _time_relation(by_id[edges_seq[-1]["id"]], f, node) == 1:
                continue
            if edges_seq and (parsed_time(f["trade_time"]) - parsed_time(edges_seq[0]["time"])).total_seconds() > window_days * 86400:
                continue
            if nxt == start and depth >= 2:
                if selected is not None and not selected.intersection(nodes_seq):
                    continue
                if _time_relation(by_id[edges_seq[0]["id"]], f, start) == 1:
                    continue
                total_c = total + amt
                if total_c < min_total:
                    continue
                # 时间跨度仍须落在 window_days 内；单调性已由遍历顺序保证。
                span_days = (parsed_time(f["trade_time"]) - parsed_time(edges_seq[0]["time"])).total_seconds() / 86400
                key = canon(nodes_seq)
                # accounts follows the chronological traversal (the accepted start),
                # so the displayed path matches start_time → end_time. canon() is
                # only the rotation-invariant dedupe key.
                first_out = edges_seq[0]["amount"]
                cycle = {"accounts": list(nodes_seq), "edges": edges_seq + [
                    {"id": f["id"], "from": node, "to": nxt, "amount": amt, "time": f["trade_time"], "currency": f["currency"]}],
                    "total": total_c, "len": len(edges_seq) + 1,
                    "start_time": edges_seq[0]["time"], "end_time": f["trade_time"],
                    "span_days": round(span_days, 1),
                    # 回转比例 = 最后一笔回流 / 第一笔转出（近似值，见 money_contract）。
                    # 1.0 表示原样回流，最像对敲；首笔为 0 时无从比较，返回 None。
                    "first_outflow": first_out, "last_inflow": amt,
                    "return_ratio": round(float(amt / first_out), 4) if first_out else None,
                    **_sequence_timing([by_id[edge["id"]] for edge in edges_seq] + [f])}
                search["matches_considered"] += 1
                rank = closeness(cycle)
                if key in best:
                    if rank < ranks[key]:
                        best[key], ranks[key] = cycle, rank
                elif max_cycles > 0:
                    if len(best) >= max_cycles:
                        search["result_limited"] = True
                        worst = max(ranks, key=ranks.get)
                        if rank >= ranks[worst]:
                            continue
                        del best[worst], ranks[worst]
                    best[key], ranks[key] = cycle, rank
                else:
                    search["result_limited"] = True
                continue
            if nxt in nodes_seq or nxt not in eligible or depth + 1 > max_len:
                continue
            dfs(start, nxt, nodes_seq + [nxt], edges_seq + [
                {"id": f["id"], "from": node, "to": nxt, "amount": amt, "time": f["trade_time"], "currency": f["currency"]}],
                depth + 1, total + amt)
            if search["budget_exhausted"]:
                return

    for s in candidates:
        dfs(s, s, [s], [], 1, ZERO)
        if search["budget_exhausted"]:
            break
    cycles = sorted(best.values(), key=closeness)
    for c in cycles:
        c["accounts_names"] = [names.get(a, a) for a in c["accounts"]]
        # 闭环展示：末尾重复起点，读起来才是"出去又回来"。
        c["loop_names"] = c["accounts_names"] + c["accounts_names"][:1]
    search["complete"] = not (search["budget_exhausted"] or search["start_pruned"])
    return {"count": len(cycles), "max_len": max_len, "window_days": window_days, "cycles": cycles,
            "search": search, "truncated": not search["complete"] or search["result_limited"]}


# ================= 5. 快进快出配对（金额守恒匹配） =================

def match_fast_flows(case_id, window_minutes=30, tolerance=0.25, min_amount=1000,
                     max_pairs=100, subjects=None, *, _flows=None):
    """账户收到大额资金后短时间内按金额守恒转出 → 贪心配对建立证据链。
    转出按时间排序 + 二分定位窗口，避免大账户的全表扫描。
    subjects：自定义分析对象（账号或户名列表）。给定后只分析这些账户的过账配对。"""
    min_amount = money_decimal(min_amount)
    tolerance = Decimal(str(tolerance))
    if not tolerance.is_finite() or not ZERO <= tolerance <= 1:
        raise ValueError("ff_tolerance must be finite and between 0 and 1")
    if min_amount < ZERO:
        raise ValueError("ff_min_amount must be nonnegative")
    flows = load_flows(case_id) if _flows is None else _flows
    selected = set().union(*(resolve_endpoints(subject, flows) for subject in subjects)) if subjects else None
    return _grouped(flows, _match_fast_flows, "pairs",
                    window_minutes=window_minutes, tolerance=tolerance,
                    min_amount=min_amount, max_pairs=max_pairs, subjects=selected)


def _match_fast_flows(flows, window_minutes, tolerance, min_amount, max_pairs, subjects):
    import bisect

    def source_order(group, account):
        if len(group) <= 1:
            return group
        notes = [row.get("_ledger_orders", {}).get(account) for row in group]
        if not all(notes) or len({note["id"] for note in notes}) != len(group):
            return None
        from . import principal_ledger
        ordered = principal_ledger.source_order(notes, {note["id"]: note for note in notes})
        by_id = {note["id"]: row for note, row in zip(notes, group)}
        return [by_id[note["id"]] for note in ordered] if ordered is not None else None

    by_acct = defaultdict(lambda: {"in": [], "out": []})
    for f in flows:
        a = f["amount"] or ZERO
        if not f["payer_account"] or not f["payee_account"] or a <= 0:
            continue
        by_acct[f["payee_account"]]["in"].append(f)
        by_acct[f["payer_account"]]["out"].append(f)
    if subjects is not None:
        by_acct = {account: values for account, values in by_acct.items() if account in subjects}
    pairs = []
    used = set()
    for acct, d in sorted(by_acct.items(), key=lambda kv: -sum((f["amount"] or ZERO for f in kv[1]["in"]), ZERO)):
        d["in"].sort(key=lambda x: _pt(x["trade_time"]))
        d["out"].sort(key=lambda x: _pt(x["trade_time"]))
        incoming = []
        for _, group in groupby(d["in"], key=lambda row: _pt(row["trade_time"])):
            ordered = source_order(list(group), acct)
            # Otherwise reusing an outflow would pick one same-time source
            # by import ID. Keep the unresolved group out of greedy pairing.
            if ordered is not None:
                incoming.extend(ordered)
        out_groups = []
        for stamp, group in groupby(d["out"], key=lambda row: _pt(row["trade_time"])):
            group = list(group)
            ordered = source_order(group, acct)
            out_groups.append((stamp, ordered if ordered is not None else group, ordered is not None))
        out_times = [stamp for stamp, _, _ in out_groups]
        for fin in incoming:
            if len(pairs) >= max_pairs:
                break
            fid = fin["id"]
            amt = fin["amount"] or ZERO
            if amt < min_amount or fid in used:
                continue
            t0 = _pt(fin["trade_time"])
            t_end = t0 + timedelta(minutes=window_minutes)
            lo = bisect.bisect_left(out_times, t0)
            hi = bisect.bisect_right(out_times, t_end)
            acc, chosen = ZERO, []
            for k in range(lo, hi):   # 只扫窗口内的转出
                if acc >= amt * (1 + tolerance):
                    break
                _, group, proved = out_groups[k]
                eligible = [fo for fo in group if fo["id"] != fid and fo["id"] not in used
                            and _time_relation(fin, fo, acct) == -1]
                for fo in eligible:
                    # Unknown same-time outgoing order permits the whole
                    # eligible group, never an arbitrary matching prefix.
                    chosen.append(fo)
                    acc += fo["amount"] or ZERO
                    if proved and acc >= amt * (1 - tolerance):
                        break
                if acc >= amt * (1 - tolerance):
                    break
            if chosen and abs(acc - amt) <= amt * tolerance:
                pairs.append({
                    "account": acct, "in_flow": {"id": fid, "time": fin["trade_time"],
                                                 "amount": amt, "from": fin["payer_name"],
                                                 "from_acct": fin["payer_account"], "currency": fin["currency"]},
                    "out_flows": [{"id": fo["id"], "time": fo["trade_time"], "amount": fo["amount"],
                                   "to": fo["payee_name"], "to_acct": fo["payee_account"], "currency": fo["currency"]} for fo in chosen],
                    "out_total": acc, "diff": acc - amt,
                    "max_delay_min": round((_pt(chosen[-1]["trade_time"]) - t0).total_seconds() / 60, 1),
                })
                used.add(fid)
                for fo in chosen:
                    used.add(fo["id"])
    pairs.sort(key=lambda p: -p["in_flow"]["amount"])
    return {"count": len(pairs), "window_minutes": window_minutes, "tolerance": float(tolerance),
            "min_amount": min_amount, "pairs": pairs[:max_pairs]}


# ================= 7. 交易行为画像与离群检测 =================

def account_outliers(case_id, sigma=2.5, top=30, subjects=None, *, _flows=None):
    """账户行为特征与基准对比，Z-score 离群 → 疑似异常账户。
    subjects：自定义分析对象（账号或户名列表）。给定后只对这些账户做画像，
    基准分布也取自这些账户之间的对比。"""
    if not 0 < float(sigma) < float("inf"):
        raise ValueError("out_sigma must be positive and finite")
    flows = load_flows(case_id) if _flows is None else _flows
    selected = None
    if subjects:
        selected = set().union(*(resolve_endpoints(subject, flows) for subject in subjects))
    return _grouped(flows, _account_outliers, "outliers", sigma=float(sigma),
                    top=top, subjects=selected)


def _account_outliers(flows, sigma, top, subjects):
    # None means all accounts; an empty resolved set means no matching subject.
    sub = subjects
    feats = defaultdict(lambda: {"cnt": 0, "total": ZERO, "night": 0, "round": 0,
                                 "counter": set(), "amts": []})
    for f in flows:
        a = f["amount"] or ZERO
        parties = tuple(dict.fromkeys((f["payer_account"], f["payee_account"])))
        if sub is not None:
            parties = tuple(x for x in parties if x in sub)
        for acct in parties:
            if not acct:
                continue
            x = feats[acct]
            x["cnt"] += 1
            x["total"] += a
            x["amts"].append(a)
            hour = str(f.get("trade_time") or "")[11:13]
            hh = int(hour) if hour.isdigit() else None
            if hh is not None and (hh >= 22 or hh < 6):
                x["night"] += 1
            if a > 0 and (a % 10000) == 0:
                x["round"] += 1
        if f["payer_account"] and f["payee_account"]:
            if f["payer_account"] in feats:
                feats[f["payer_account"]]["counter"].add(f["payee_account"])
            if f["payee_account"] in feats:
                feats[f["payee_account"]]["counter"].add(f["payer_account"])
    if len(feats) < 5:
        return {"count": 0, "sigma": sigma, "outliers": []}

    import statistics
    dims = ["cnt", "total", "night", "round", "counter_size", "avg_amount"]
    data = []
    for acct, x in feats.items():
        data.append({"account": acct, "name": "", "cnt": x["cnt"], "total": round(x["total"], 2),
                     "night": x["night"], "round": x["round"],
                     "counter_size": len(x["counter"]),
                     "avg_amount": (x["total"] / x["cnt"]).quantize(CENT, rounding=ROUND_HALF_EVEN) if x["cnt"] else ZERO})
    # 补户名
    nm = {}
    for f in flows:
        nm.setdefault(f["payer_account"], f["payer_name"] or "")
        nm.setdefault(f["payee_account"], f["payee_name"] or "")
    for r in data:
        r["name"] = nm.get(r["account"], "")

    outliers = []
    for dim in dims:
        # Keep cent differences even at large magnitudes when establishing the
        # statistical baseline. Only the dimensionless final Z score is float.
        vals = [Decimal(r[dim]) for r in data]
        mean = statistics.mean(vals)
        std = statistics.pstdev(vals)
        if std <= 0:
            continue
        for r in data:
            z = float((Decimal(r[dim]) - mean) / std)
            r.setdefault("_z", {})[dim] = round(z, 2)
            if z >= sigma:
                outliers.append((z, r, dim))
    # 每账户取最高 z 维度
    best = {}
    for z, r, dim in outliers:
        if r["account"] not in best or z > best[r["account"]]["z"]:
            best[r["account"]] = {"account": r["account"], "name": r["name"], "z": round(z, 2),
                                  "dim": dim, "value": r[dim],
                                  "explain": {"cnt": "交易笔数异常多", "total": "交易总额异常大",
                                              "night": "深夜交易占比异常", "round": "整数金额交易异常多",
                                              "counter_size": "交易对手数量异常", "avg_amount": "单笔均额异常"}[dim]}
    result = sorted(best.values(), key=lambda x: -x["z"])[:top]
    return {"count": len(result), "sigma": sigma, "dims": dims, "outliers": result}


# ================= 汇总 =================

def run_all(case_id, params=None):
    p = params or {}
    algorithms = p.get("algorithms")
    if algorithms is None:
        algorithms = list(ALGORITHMS[:3])
        if p.get("path_src") and p.get("path_dst"):
            algorithms.append("paths")
    subjects = p.get("subjects") or []
    if isinstance(subjects, str):
        subjects = [x.strip() for x in re.split(r"[,，;；\n\r\s]+", subjects) if x.strip()]
    # One database read establishes the source snapshot for all algorithms and
    # report overview counts. The pure helpers only read these row dictionaries.
    from . import db
    from .import_coverage import import_coverage
    with db._lock:
        flows = load_flows(case_id)
        coverage = import_coverage(case_id)
    accounts = {flow[key] for flow in flows for key in ("payer_account", "payee_account") if flow[key]}
    funds = funds_projection.summarize_rows(flows)
    fields = funds_projection.public_fields(funds)
    out = {"case_id": case_id, "funds": funds, "statistics_policy": funds_projection.POLICY, "subjects": subjects, "algorithms": list(algorithms), "money_contract": MONEY_CONTRACT,
           "source_snapshot": {"flows": fields["cnt"], "accounts": len(accounts), "import_coverage": coverage,
               "funds": funds, "amount_total": fields["total"], "amount_totals": fields["amount_totals"],
               "statistics_policy": funds_projection.POLICY}}
    if "cycles" in algorithms:
        out["cycles"] = find_cycles(case_id, max_len=p.get("cycle_max_len", 4), max_cycles=p.get("cycle_max", 30),
                                    window_days=p.get("cycle_window_days", 7), min_total=p.get("cycle_min_total", 0),
                                    subjects=subjects, _flows=flows)
    if "fast_flows" in algorithms:
        out["fast_flows"] = match_fast_flows(
            case_id, window_minutes=p.get("ff_window_minutes", 30),
            tolerance=p.get("ff_tolerance", 0.25), min_amount=p.get("ff_min_amount", 1000),
            max_pairs=p.get("ff_max_pairs", 60), subjects=subjects, _flows=flows)
    if "outliers" in algorithms:
        out["outliers"] = account_outliers(case_id, sigma=p.get("out_sigma", 2.5), subjects=subjects, _flows=flows)
    if "paths" in algorithms:
        out["paths"] = _grouped(flows, _trace_paths, "paths", src=p["path_src"], dst=p["path_dst"],
                                max_hops=p.get("path_max_hops", 4), max_paths=p.get("path_max_paths", 50),
                                neighbor_limit=20,
                                **_path_options(flows, p["path_src"], p["path_dst"], p.get("path_order", "amount")))
    return out
