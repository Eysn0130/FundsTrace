# -*- coding: utf-8 -*-
"""智能分析算法集：资金追踪第一梯队（图结构）+ 第二梯队（统计模式）

所有算法基于当前案件的双边流水（DuckDB），输出 JSON 可序列化的结构化结果。
设计原则：可解释优先——每个结果都附带可用于办案的量化依据。
"""
import re
from collections import defaultdict, deque
from datetime import datetime as _dt
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from .money import money_decimal

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
ALGORITHMS = ("cycles", "fast_flows", "outliers", "paths")
MONEY_CONTRACT = {
    "version": "funds-money.v1",
    "amounts": "exact decimal strings with two fractional digits",
    "currency": "independent groups; no implicit conversion or cross-currency pairing",
    "totals": "exact sum of the displayed transaction legs, not conserved principal",
    "avg_amount": "decimal mean rounded to cents using ROUND_HALF_EVEN",
    "approximate_metrics": ["z", "sigma", "span_days", "max_delay_min", "tolerance"],
}


def _pt(s):
    return s if isinstance(s, _dt) else _dt.strptime(s, "%Y-%m-%d %H:%M:%S")


def load_flows(case_id):
    rows = db_q("select id, trade_no, trade_time, amount, balance, currency, self_account, summary, "
                "payer_account, payer_name, payee_account, payee_name "
                "from flows where case_id = ? order by trade_time, id", [case_id])
    for row in rows:
        row["amount"] = money_decimal(row["amount"]) if row["amount"] is not None else ZERO
        row["balance"] = money_decimal(row["balance"]) if row["balance"] is not None else None
        row["currency"] = row.get("currency") or ""
        if isinstance(row["trade_time"], _dt):
            row["trade_time"] = row["trade_time"].strftime("%Y-%m-%d %H:%M:%S")
    return rows


def _grouped(flows, algorithm, result_key, **options):
    """Maintain legacy arrays, with currency-tagged rows and independent groups.

    Limits and statistical baselines apply within each currency. There is no
    ranking of amounts from different currencies and no conversion to CNY.
    """
    groups = defaultdict(list)
    for flow in flows:
        groups[flow["currency"]].append(flow)
    by_currency = []
    combined = []
    # DECIMAL(18,2) rows can produce larger totals; do not inherit a small
    # process-wide Decimal precision when summing or calculating means.
    with localcontext() as context:
        context.prec = 50
        for currency, rows in sorted(groups.items()):
            group = algorithm(rows, **options)
            for result in group[result_key]:
                result["currency"] = currency
            group["currency"] = currency
            by_currency.append(group)
            combined.extend(group[result_key])
    base = dict(next((group for group in by_currency if group[result_key]),
                     by_currency[0] if by_currency else algorithm([], **options)))
    base.pop("currency", None)
    return {**base, result_key: combined, "count": len(combined),
            "by_currency": by_currency, "money_contract": MONEY_CONTRACT}


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
        names.setdefault(p, f["payer_name"] or "")
        names.setdefault(q2, f["payee_name"] or "")
    return adj, radj, names


# ================= 1. 多跳资金路径追踪 =================

def trace_paths(case_id, src, dst, max_hops=4, max_paths=50, neighbor_limit=20):
    """枚举同币种 src 到 dst 的资金路径。total 为各条边的金额合计。"""
    return _grouped(load_flows(case_id), _trace_paths, "paths", src=src, dst=dst,
                    max_hops=max_hops, max_paths=max_paths, neighbor_limit=neighbor_limit)


def _trace_paths(flows, src, dst, max_hops, max_paths, neighbor_limit):
    adj, radj, names = build_adjacency(flows)
    # 反向可达集剪枝：只扩展能到达 dst 的节点
    reach = {dst}
    q = deque([dst])
    while q:
        u = q.popleft()
        for v, _a, _f in radj.get(u, []):
            if v not in reach:
                reach.add(v)
                q.append(v)
    if src not in reach:
        return {"src": src, "dst": dst, "max_hops": max_hops, "paths": [], "count": 0,
                "note": "起点无法到达终点"}

    results = []

    def dfs(node, path, total, visited):
        if len(results) >= max_paths:
            return
        if node == dst and path:
            results.append({"path": list(path), "total": round(total, 2), "hops": len(path)})
            return
        if len(path) >= max_hops:
            return
        all_outs = adj.get(node, [])
        # 剪枝：终点直连边强制保留，其余按金额取前 N
        dst_outs = [x for x in all_outs if x[0] == dst]
        others = sorted((x for x in all_outs if x[0] != dst), key=lambda x: -(x[1] or 0))[:neighbor_limit]
        for nxt, amt, f in dst_outs + others:
            if nxt in visited:
                continue
            if nxt not in reach:
                continue
            visited.add(nxt)
            path.append({"from": node, "from_name": names.get(node, ""), "to": nxt,
                         "to_name": names.get(nxt, ""), "amount": amt,
                         "time": f["trade_time"], "no": f["trade_no"] or "",
                         "currency": f["currency"]})
            dfs(nxt, path, total + amt, visited)
            path.pop()
            visited.discard(nxt)

    dfs(src, [], ZERO, {src})
    results.sort(key=lambda x: -x["total"])
    for r in results:
        for step in r["path"]:
            step.setdefault("from_name", names.get(step["from"], ""))
            step.setdefault("to_name", names.get(step["to"], ""))
    return {"src": src, "src_name": names.get(src, ""), "dst": dst, "dst_name": names.get(dst, ""),
            "max_hops": max_hops, "count": len(results), "paths": results[:max_paths]}


# ================= 2. 环路检测（资金回流） =================

def find_cycles(case_id, max_len=4, max_cycles=30, min_total=0, window_days=7,
                budget=1_500_000, start_top=300, subjects=None, *, _flows=None):
    """检测资金闭环（对敲/回流特征）。只考虑进出均有的账户，环规范化去重。
    时间约束：环上全部交易须在 window_days 天内完成——刻意对敲通常短窗完成，
    跨月才凑成的闭环更可能是正常经营往来。
    subjects：自定义分析对象（账号或户名列表）。给定后只从这些账户发起环路搜索。
    性能保护：无 subjects 时只从金额最大的前 start_top 个账户发起，DFS 探索受 budget 限制。"""
    min_total = money_decimal(min_total)
    return _grouped(load_flows(case_id) if _flows is None else _flows, _find_cycles, "cycles", max_len=max_len,
                    max_cycles=max_cycles, min_total=min_total, window_days=window_days,
                    budget=budget, start_top=start_top, subjects=subjects)


def _find_cycles(flows, max_len, max_cycles, min_total, window_days, budget, start_top, subjects):
    adj, radj, names = build_adjacency(flows)
    indeg = defaultdict(int)
    for s, edges in adj.items():
        for d, _a, _f in edges:
            indeg[d] += 1
    node_out = {x: sum((a for (_, a, _f) in adj[x]), ZERO) for x in adj}
    candidates = [x for x in adj if indeg.get(x, 0) >= 1 and node_out[x] > 0]
    candidates.sort(key=lambda x: -node_out[x])
    # Subject and start-count controls choose search origins only. A closed
    # path can still pass through every eligible account in this currency.
    eligible = set(candidates)
    if subjects:
        sub = {x.strip() for x in subjects if x.strip()}
        candidates = [x for x in candidates if x in sub or names.get(x, "") in sub]
    else:
        candidates = candidates[:start_top]

    cycles = []
    seen = set()
    budget_left = [budget]

    def canon(nodes_seq):
        """环规范化：以最小账户名为起点的旋转"""
        k = nodes_seq.index(min(nodes_seq))
        return tuple(nodes_seq[k:] + nodes_seq[:k])

    def dfs(start, node, nodes_seq, edges_seq, depth, total):
        budget_left[0] -= 1
        if budget_left[0] <= 0 or len(cycles) >= max_cycles:
            return
        for nxt, amt, f in adj.get(node, []):
            if nxt == start and depth >= 2:
                total_c = total + amt
                if total_c < min_total:
                    continue
                # 时间约束：环上全部交易的时间跨度 ≤ window_days
                times = [_pt(e["time"]) for e in edges_seq] + [_pt(f["trade_time"])]
                span_days = (max(times) - min(times)).total_seconds() / 86400
                if span_days > window_days:
                    continue
                key = canon(nodes_seq)
                if key in seen:
                    continue
                seen.add(key)
                cycles.append({"accounts": list(key), "edges": edges_seq + [
                    {"id": f["id"], "from": node, "to": nxt, "amount": amt, "time": f["trade_time"], "currency": f["currency"]}],
                    "total": total_c, "len": len(edges_seq) + 1,
                    "span_days": round(span_days, 1)})
                if len(cycles) >= max_cycles:
                    return
                continue
            if nxt in nodes_seq or nxt not in eligible or depth + 1 > max_len:
                continue
            dfs(start, nxt, nodes_seq + [nxt], edges_seq + [
                {"id": f["id"], "from": node, "to": nxt, "amount": amt, "time": f["trade_time"], "currency": f["currency"]}], depth + 1, total + amt)
            if len(cycles) >= max_cycles or budget_left[0] <= 0:
                return

    for s in candidates:
        if len(cycles) >= max_cycles or budget_left[0] <= 0:
            break
        dfs(s, s, [s], [], 1, ZERO)

    cycles.sort(key=lambda c: -c["total"])
    for c in cycles:
        c["accounts_names"] = [names.get(a, a) for a in c["accounts"]]
    return {"count": len(cycles), "max_len": max_len, "window_days": window_days, "cycles": cycles[:max_cycles]}


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
    return _grouped(load_flows(case_id) if _flows is None else _flows, _match_fast_flows, "pairs",
                    window_minutes=window_minutes, tolerance=tolerance,
                    min_amount=min_amount, max_pairs=max_pairs, subjects=subjects)


def _match_fast_flows(flows, window_minutes, tolerance, min_amount, max_pairs, subjects):
    import bisect
    by_acct = defaultdict(lambda: {"in": [], "out": []})
    for f in flows:
        a = f["amount"] or ZERO
        if not f["payer_account"] or not f["payee_account"] or a <= 0:
            continue
        by_acct[f["payee_account"]]["in"].append(f)
        by_acct[f["payer_account"]]["out"].append(f)
    if subjects:
        sub = {x.strip() for x in subjects if x.strip()}
        by_acct = {a: d for a, d in by_acct.items()
                   if a in sub or (d["in"] and any((f.get("payee_name") or "") in sub for f in d["in"]))}
    pairs = []
    used = set()
    for acct, d in sorted(by_acct.items(), key=lambda kv: -sum((f["amount"] or ZERO for f in kv[1]["in"]), ZERO)):
        d["in"].sort(key=lambda x: x["trade_time"])
        d["out"].sort(key=lambda x: x["trade_time"])
        out_times = [_pt(fo["trade_time"]) for fo in d["out"]]
        for fin in d["in"]:
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
                fo = d["out"][k]
                # A self-transfer occurs in both lists, but one source record
                # cannot be evidence for both legs of a pass-through pair.
                if fo["id"] == fid or fo["id"] in used:
                    continue
                if acc >= amt * (1 + tolerance):
                    break
                chosen.append(fo)
                acc += fo["amount"] or ZERO
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
        sub = {x.strip() for x in subjects if x.strip()}
        # Resolve identity from the complete case snapshot before grouping by
        # currency. Historical aliases and blank names must not discard any
        # transactions of an account selected by a matching name elsewhere.
        selected = {f[account] for f in flows
                    for account, name in (("payer_account", "payer_name"), ("payee_account", "payee_name"))
                    if f[account] and (f[account] in sub or (f.get(name) or "") in sub)}
    return _grouped(flows, _account_outliers, "outliers", sigma=float(sigma),
                    top=top, subjects=selected)


def _account_outliers(flows, sigma, top, subjects):
    # None means all accounts; an empty resolved set means no matching subject.
    sub = subjects
    feats = defaultdict(lambda: {"cnt": 0, "total": ZERO, "night": 0, "round": 0,
                                 "counter": set(), "amts": []})
    for f in flows:
        a = f["amount"] or ZERO
        parties = (f["payer_account"], f["payee_account"])
        if sub is not None:
            parties = tuple(x for x in parties if x in sub)
        for acct in parties:
            if not acct:
                continue
            x = feats[acct]
            x["cnt"] += 1
            x["total"] += a
            x["amts"].append(a)
            hh = int(f["trade_time"][11:13])
            if hh >= 22 or hh < 6:
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
    flows = load_flows(case_id)
    accounts = {flow[key] for flow in flows for key in ("payer_account", "payee_account") if flow[key]}
    out = {"case_id": case_id, "subjects": subjects, "algorithms": list(algorithms), "money_contract": MONEY_CONTRACT,
           "source_snapshot": {"flows": len(flows), "accounts": len(accounts)}}
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
                                neighbor_limit=20)
    return out
