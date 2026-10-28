# -*- coding: utf-8 -*-
"""分析报告生成：matplotlib 统计图 + 自包含 HTML（文字+图片，可直接打印/另存 PDF）"""
import base64
import html
import io
import json
import os
import sys
from datetime import datetime

from .money import money_display, wire_money


def _ensure_mpl_dir():
    """matplotlib 首次导入会在用户主目录写配置；内网锁定账户常无写权限导致崩溃。
    打包运行时把配置目录指到 exe 旁的 mpl-cache（必定可写）。"""
    if os.environ.get("MPLCONFIGDIR"):
        return
    from .paths import DATA_DIR
    d = str(DATA_DIR / "cache" / "matplotlib")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        import tempfile
        d = tempfile.gettempdir()
    os.environ.setdefault("MPLCONFIGDIR", d)


_ensure_mpl_dir()

import matplotlib  # noqa: E402

matplotlib.use("Agg")
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
matplotlib.rcParams["axes.unicode_minus"] = False
import matplotlib.pyplot as plt  # noqa: E402


def _fig64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _hist(values, title, xlabel, bins=24, color="#3d7bff"):
    if not values:
        return None
    fig, ax = plt.subplots(figsize=(9, 3.2))
    # Plot coordinates may be approximate; never reuse them for amounts/totals.
    ax.hist([float(value) for value in values], bins=bins, color=color, edgecolor="white")
    ax.set_title(title, fontsize=12)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("笔数")
    ax.spines[["top", "right"]].set_visible(False)
    return _fig64(fig)


def _balance_chart(flows, account, name):
    """Approximate display coordinates for a single-currency balance curve."""
    currencies = {f.get("currency") or "" for f in flows
                  if f.get("self_account") == account and f.get("balance") is not None}
    if len(currencies) > 1:
        raise ValueError("Balance charts require one currency; no implicit conversion")
    pts = [(f["trade_time"], f["balance"]) for f in flows
           if f.get("self_account") == account and f.get("balance") is not None]
    if len(pts) < 3:
        return None
    pts.sort(key=lambda x: x[0])
    xs = [t if isinstance(t, datetime) else datetime.fromisoformat(t) for t, _ in pts]
    ys = [float(b) for _, b in pts]
    fig, ax = plt.subplots(figsize=(9, 3.4))
    ax.plot(xs, ys, color="#e25656", linewidth=1.2, marker="o", markersize=2.5)
    ax.set_title(f"账户余额曲线：{name or account}（{len(pts)} 笔含余额）", fontsize=12)
    ax.set_ylabel("余额（元）")
    ax.grid(axis="y", alpha=0.3)
    ax.spines[["top", "right"]].set_visible(False)
    fig.autofmt_xdate()
    return _fig64(fig)


def _escape_text(value):
    """Escape data at the HTML boundary while preserving numeric formatting."""
    if isinstance(value, str):
        return html.escape(value, quote=True)
    if isinstance(value, dict):
        return {key: _escape_text(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_escape_text(item) for item in value]
    return value


def _search_note(result):
    search = result.get("search", {})
    limits = []
    if search.get("complete") is False:
        limits.append("搜索受预算、邻居数或起点数限制，可能还有未发现的结果")
    if search.get("result_limited"):
        limits.append("仅显示候选中的前若干项")
    return "结果按币种分别排序，顺序限于本次有界搜索候选。" + "；".join(limits) + ("。" if limits else "")


def _funds_money(funds, fallback=None, currency=None):
    if not funds:
        return f"{currency or '未标明币种'} {money_display(fallback)}"
    values = []
    for row in funds.get("totals", []):
        unit = row.get("currency") or "币种待定"
        if row["amount"] is not None:
            amount = money_display(row["amount"])
        elif row["upper"] is None:
            amount = f"≥{money_display(row['lower'])}（上界未知）"
        else:
            amount = f"{money_display(row['lower'])}–{money_display(row['upper'])}（待核）"
        values.append(f"{unit} {amount}")
    return " / ".join(values) or ("0.00" if funds["status"] == "exact" else "金额待定")


def _funds_count(funds, fallback=None):
    if not funds:
        return f"{fallback:,} 笔" if fallback is not None else "事件数待定"
    lower, upper = funds["count"]["lower"], funds["count"]["upper"]
    return f"{lower:,} 个事件" if lower == upper else f"{lower:,}–{upper:,} 个事件（待核）"


def _outlier_value(row):
    if row.get("metric_basis") == "observation_clue":
        if row["dim"] == "total":
            return _funds_money(row["funds"])
        if row["dim"] == "cnt":
            return _funds_count(row["funds"])
        value = money_display(row["observation_value"]) if row["dim"] == "avg_amount" else format(row["observation_value"], ",")
        return value + "（观察线索，待核）"
    return _funds_money(None, row["value"], row.get("currency")) if row["dim"] in ("total", "avg_amount") else format(row["value"], ",")


def build_report(case_id, results, case_info):
    """组装自包含 HTML 报告，返回 bytes"""
    # application/json is still a raw-text HTML element: escape script-closing
    # characters without changing the JSON values obtained during readback.
    snapshot = json.dumps(wire_money({"schema_version": "funds-report.v1", "case_id": str(case_id),
                                      "case": case_info, "results": results}), ensure_ascii=False)
    snapshot = (snapshot.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
                .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    cases = _escape_text(results)
    case_info = _escape_text(case_info)
    names = {"cycles": "资金回流环路检测", "fast_flows": "快进快出容差配对",
             "outliers": "交易行为离群账户", "paths": "多跳资金路径追踪"}
    algorithms = cases.get("algorithms", [name for name in names if name in cases])
    scope = "、".join(names[name] for name in algorithms)
    sec = []
    add = sec.append

    # ---------- 概览 ----------
    stats = case_info.get("stats", {})
    coverage = stats.get("import_coverage")
    add(f"""
    <div class="meta">
      <div><b>案件名称：</b>{case_info.get('case_name', case_id)}</div>
      <div><b>流水总量：</b>{_funds_count(stats.get('funds'), stats.get('flows'))}　{_funds_money(stats['funds']) if stats.get('funds') else ''}　<b>账户数：</b>{stats.get('accounts', 0):,} 个</div>
      <div><b>本次分析范围：</b>{scope}</div>
      <div><b>生成时间：</b>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}　<b>生成工具：</b>FundsTrace</div>
    </div>
    <p class="note">本报告由 FundsTrace 自动生成，基于当前案件的有效流水数据。
    仅执行上列分析项目；未选项目未分析，不作“未发现”的结论。
    各项结果均为算法辅助判断，供侦查参考，最终定案以人工核实为准。</p>
    <p class="note">金额按币种分别分析，不自动换汇。已确认事件的金额精确到分，未决观察显示证据允许的金额范围；路径及环路合计是各交易段之和，
    不代表实际本金。排序、容差配对与评分基于本次候选，含未决观察时仅作复核线索；代表记录时间不证明经济事件先后。单笔均额按十进制计算并以四舍六入五成双保留两位；Z 分数、时间跨度和图表坐标为近似统计或展示值。
    本报告内嵌可回读的事件、观察粒度和资金范围快照。</p>""")
    add('<p class="note">已匹配的冲正／撤销双方退出有效统计，普通退款仍保留；资金效果未确定记录不计入金额。本报告不代表结算净额或完整清算结果。</p>')
    if stats.get("funds"):
        add('<p class="note">有据关联的双方流水按同一事件统计；未决观察保留为线索，原账簿记录与余额不变。</p>')
    elif stats.get("statistics_policy") == "typed-account-evidence-v1":
        add(f'<p class="note">统计口径：已接纳账务记录；相同有据原件副本共享记录，双方反馈尚未证明同一经济事件时各自保留。解析修订：{stats.get("resolution_revision", "")}</p>')
    if coverage is not None:
        add(f"""<p class="note">已导入反馈来源中，资金效果未确定记录 {coverage['unresolved_records']:,} 条，
        未计入流水统计；已匹配冲正／撤销事件 {coverage.get('cancelled_records', 0):,} 条，
        保留原始流水中退出分析 {coverage.get('excluded_flows', 0):,} 笔；非资金事件 {coverage['non_monetary_records']:,} 条单独保留，不计入金额与交易笔数。
        此完整性范围仅限已导入反馈来源的资金语义，不表示银行提供的数据齐全。</p>""")
    else:
        add('<p class="note">未提供已导入反馈来源的资金语义覆盖信息，不能据此认定资金解释完整。</p>')

    for name in algorithms:
        missing = cases.get(name, {}).get("unassessed_observations", 0)
        if missing:
            add(f'<p class="note">{names[name]}：{missing:,} 条观察缺少本规则所需的有效金额或时间，未参与候选计算；仍保留在来源资金范围中。</p>')

    # ---------- 路径追踪 ----------
    paths = cases.get("paths")
    if "paths" in algorithms and paths is not None:
        time_order = paths.get("order") == "time"
        rows = ""
        for index, item in enumerate(paths["paths"]):
            currency = item.get("currency") or "未标明币种"
            steps = "<br>".join(
                f"{step['from_name'] or step.get('from_display_account', step['from'])} → {step['to_name'] or step.get('to_display_account', step['to'])}："
                f"{_funds_money(step.get('funds'), step['amount'], step.get('currency'))}（{step['time']}）" for step in item["path"])
            timing = ""
            if time_order:
                span = f"{item.get('start_time') or '—'} → {item.get('end_time') or '—'}"
                timing_label = "时序未确定" if item.get("continuous") is None else "时序连续" if item["continuous"] else "时序不连续"
                timing = f"<td>{span}（{timing_label}）</td>"
            rows += (f"<tr><td>{index + 1}</td><td>{steps}</td><td>{item['hops']} 跳</td>"
                     f"<td>{_funds_money(item.get('funds'), item['total'], item.get('currency'))}</td>{timing}</tr>")

        def endpoint(label, matched):
            """Expand a name entry point to the accounts it resolved to."""
            if len(matched) > 1:
                return f"{label}（户名匹配 {len(matched)} 个账号：{'、'.join(matched)}）"
            return label

        order_note = "按首跳时间排序，时序连续的路径排在前面。" if time_order else ""
        add(f"""<h3>多跳资金路径追踪</h3>
        <p>起点 <span class="mono">{endpoint(paths.get('src_display_account', paths['src']), paths.get('src_display_accounts', paths.get('src_accounts')) or [])}</span> → 终点 <span class="mono">{endpoint(paths.get('dst_display_account', paths['dst']), paths.get('dst_display_accounts', paths.get('dst_accounts')) or [])}</span>；
        最多 {paths['max_hops']} 跳，本次返回 {paths['count']} 条同币种路径。{order_note}各边金额合计不等于守恒本金。
        {_search_note(paths)}</p>""")
        if rows:
            timing_head = "<th>首跳 → 末跳</th>" if time_order else ""
            add(f"<table><tr><th>#</th><th>交易路径及金额</th><th>跳数</th><th>各边金额合计</th>{timing_head}</tr>{rows}</table>")
        else:
            add("<p>当前搜索范围内未找到同币种可达路径。</p>")

    # ---------- 环路 ----------
    cyc = cases.get("cycles", {})
    if "cycles" in algorithms and cyc.get("count"):
        rows = "".join(
            f"<tr><td>{k + 1}</td><td>{' → '.join(c.get('loop_names') or c['accounts_names'] + c['accounts_names'][:1])}</td>"
            f"<td>{c['len']} 跳</td><td>{_funds_money(c.get('funds'), c['total'], c.get('currency'))}</td>"
            f"<td>{'—' if c.get('return_ratio') is None else format(c['return_ratio'] * 100, '.1f') + '%'}</td>"
            f"<td>{c.get('start_time', '—')} → {c.get('end_time', '—')}（{c.get('span_days', '—')} 天）"
            f"{'（时序未确定）' if c.get('continuous') is None else ''}</td></tr>"
            for k, c in enumerate(cyc["cycles"]))
        sub_txt = "、".join(cases.get("subjects") or []) or "全案件"
        add(f"""<h3>资金回流环路检测（{cyc.get('window_days', 7)} 天内的时序相容线索）—— 分析对象：{sub_txt}</h3>
        <p>原理：交易方向构成<b>回到起点</b>的闭环，记录时间跨度在时限内，且未发现已确证的逆序。
        分析对象筛选包含该账户的闭环；有明确先后时展示从实际时序起点开始。同一账户环保留本次候选中回转比例最接近 100% 的交易组合。
        同刻交易缺少共同账簿的明确顺序时标为时序未确定，不认定已按该顺序流转。
        <b>回转比例（近似值）</b> = 最后一笔回流 ÷ 第一笔转出，结果按该比例接近 100% 的程度排序。
        时序和比例仅作为复核线索，不证明同一笔本金流转或交易异常性质。
        本次返回 <b>{cyc['count']} 个环路</b>。{_search_note(cyc)}</p>
        <table><tr><th>#</th><th>资金闭环路径</th><th>跳数</th><th>环路金额合计</th><th>回转比例</th><th>时序（起 → 止）</th></tr>{rows}</table>""")
    elif "cycles" in algorithms:
        add(f"<h3>资金回流环路检测</h3><p>本次搜索未发现符合环路参数的资金闭环。{_search_note(cyc)}</p>")

    # ---------- 快进快出配对 ----------
    ff = cases.get("fast_flows", {})
    if "fast_flows" in algorithms and ff.get("count"):
        rows = ""
        for p in ff["pairs"][:40]:
            currency = p.get('currency') or '未标明币种'
            ins = f"{_funds_money(p['in_flow'].get('funds'), p['in_flow']['amount'], p['in_flow'].get('currency'))}（{p['in_flow']['time'][5:16]} 来自 {p['in_flow']['from'] or p['in_flow'].get('from_acct_display_account', p['in_flow']['from_acct']) or '—'}）"
            outs = "<br>".join(f"{_funds_money(o.get('funds'), o['amount'], o.get('currency'))} → {o['to'] or o.get('to_acct_display_account', o['to_acct'])}" for o in p["out_flows"][:5])
            rows += (f"<tr><td>{p.get('display_account', p['account'])[-6:]}<br><span class='mono'>{p.get('display_account', p['account'])}</span></td>"
                     f"<td>{ins}</td><td>{outs}</td><td>{_funds_money(p.get('out_funds'), p['out_total'], p.get('currency'))}"
                     f"<br><span class='small'>{'差额待核' if p['diff'] is None else '差额 ' + money_display(p['diff'])}</span></td>"
                     f"<td>{p['max_delay_min']} 分钟</td></tr>")
        sub_txt2 = "、".join(ff.get("subjects") or []) if isinstance(ff.get("subjects"), list) else ""
        add(f"""<h3>快进快出容差配对—— 分析对象：{sub_txt2 or '全案件'}</h3>
        <p>原理：按设定的最低转入金额，匹配账户转入后 {ff['window_minutes']} 分钟内的转出，
        转出合计与转入金额的相对差额在 ±{ff['tolerance']:.0%} 内。
        本算法未使用余额或来源容量约束；差额与时间仅作为复核线索，不证明同一笔本金归属或交易性质。
        共配对成功 <b>{ff['count']} 组</b>。</p>
        <table><tr><th>配对账户</th><th>转入</th><th>短窗内转出</th><th>转出合计/差额</th><th>最大延迟</th></tr>{rows}</table>""")
    elif "fast_flows" in algorithms:
        add("<h3>快进快出容差配对</h3><p>未发现符合本次快进快出参数的配对。</p>")

    # ---------- 离群画像 ----------
    ol = cases.get("outliers", {})
    if "outliers" in algorithms and ol.get("count"):
        rows = "".join(
            f"<tr><td>{o['name'] or ''}<br><span class='mono'>{o.get('display_account', o['account'])}</span></td>"
            f"<td>{o['explain']}</td><td>"
            f"{_outlier_value(o)}"
            f"</td><td><b>{o['z']}</b></td></tr>"
            for o in ol["outliers"])
        add(f"""<h3>交易行为离群账户</h3>
        <p>原理：对每个账户提取行为特征（交易笔数、总额、深夜交易笔数、整数金额交易笔数、对手数量、单笔均额），
        与本次分析范围内的同币种账户基准分布对比，高于均值至少 {ol['sigma']} 个标准差的账户标记为离群。
        结果仅表示相对本次账户基准的统计差异，不能据此判断经营是否正常或交易性质。</p>
        <table><tr><th>账户</th><th>离群维度</th><th>数值</th><th>Z 分数</th></tr>{rows}</table>""")
    elif "outliers" in algorithms:
        add("<h3>交易行为离群账户</h3><p>未发现超过本次离群阈值的账户。</p>")

    # ---------- 总结 ----------
    summary = []
    if "paths" in algorithms and paths and paths.get("count"):
        summary.append(f"找到 {paths['count']} 条同币种资金路径")
    if "cycles" in algorithms and cyc.get("count"):
        summary.append(f"发现 {cyc['count']} 个资金回流环路")
    if "fast_flows" in algorithms and ff.get("count"):
        summary.append(f"{ff['count']} 组符合金额容差的快进快出配对")
    if "outliers" in algorithms and ol.get("count"):
        summary.append(f"{ol['count']} 个行为离群账户")
    concl = "；".join(summary) + "。" if summary else "未发现符合本次所选算法及参数的结果。"
    add(f"<h2>分析结论</h2><p class=\"concl\">在本次分析范围（{scope}）内，{concl}</p>")

    css = """
    body{font-family:"Microsoft YaHei",sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#222;line-height:1.6}
    h1{font-size:22px;border-bottom:3px solid #2f6fe0;padding-bottom:8px}
    h2{font-size:17px;margin-top:28px;background:#eef4ff;padding:6px 10px;border-left:4px solid #2f6fe0}
    h3{font-size:14px;margin-top:18px}
    .meta{display:flex;flex-wrap:wrap;gap:6px 24px;background:#f5f7fa;padding:10px 12px;border-radius:6px;font-size:13px}
    .note{font-size:12px;color:#888;border-left:3px solid #ddd;padding-left:8px}
    .concl{background:#fffbe6;border:1px solid #f0e0a0;padding:10px 14px;border-radius:6px}
    table{width:100%;border-collapse:collapse;font-size:12px;margin:8px 0}
    th{background:#f0f3f7;padding:5px 8px;border:1px solid #dde;text-align:left}
    td{padding:5px 8px;border:1px solid #e4e8ee;vertical-align:top}
    .chart{width:100%;margin:10px 0;border:1px solid #eee}
    .mono{font-family:Consolas,monospace;font-size:11px;color:#555}
    .small{font-size:11px;color:#666}
    .wm{position:fixed;right:12px;bottom:10px;color:#bbb;font-size:11px}
    @media print{body{margin:0}}"""
    html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>资金追踪分析报告 - 案件{case_id}</title><style>{css}</style></head><body>
<h1>资金追踪分析报告</h1>
{''.join(sec)}
<script type="application/json" id="funds-trace-report-data">{snapshot}</script>
<div class="wm">FundsTrace · 资金流水追踪与关联分析</div>
</body></html>"""
    return html.encode("utf-8")
