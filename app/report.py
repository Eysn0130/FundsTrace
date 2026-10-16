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
    xs = [datetime.strptime(t, "%Y-%m-%d %H:%M:%S") for t, _ in pts]
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
    names = {"cycles": "资金回流环路检测", "fast_flows": "快进快出金额守恒配对",
             "outliers": "交易行为离群账户", "paths": "多跳资金路径追踪"}
    algorithms = cases.get("algorithms", [name for name in names if name in cases])
    scope = "、".join(names[name] for name in algorithms)
    sec = []
    add = sec.append

    # ---------- 概览 ----------
    stats = case_info.get("stats", {})
    add(f"""
    <div class="meta">
      <div><b>案件名称：</b>{case_info.get('case_name', case_id)}</div>
      <div><b>流水总量：</b>{stats.get('flows', 0):,} 笔　<b>账户数：</b>{stats.get('accounts', 0):,} 个</div>
      <div><b>本次分析范围：</b>{scope}</div>
      <div><b>生成时间：</b>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}　<b>生成工具：</b>FundsTrace</div>
    </div>
    <p class="note">本报告由 FundsTrace 自动生成，基于当前案件已导入的全部流水数据。
    仅执行上列分析项目；未选项目未分析，不作“未发现”的结论。
    各项结果均为算法辅助判断，供侦查参考，最终定案以人工核实为准。</p>
    <p class="note">金额按币种分别分析，不自动换汇。金额和交易边合计精确到分；路径及环路合计是各条交易边之和，
    不代表去重后的实际本金。单笔均额按十进制计算并以四舍六入五成双保留两位；Z 分数、时间跨度和图表坐标为近似统计或展示值。
    本报告内嵌可回读的原始精确金额快照。</p>""")

    # ---------- 路径追踪 ----------
    paths = cases.get("paths")
    if "paths" in algorithms and paths is not None:
        rows = ""
        for index, item in enumerate(paths["paths"]):
            currency = item.get("currency") or "未标明币种"
            steps = "<br>".join(
                f"{step['from_name'] or step['from']} → {step['to_name'] or step['to']}："
                f"{currency} {money_display(step['amount'])}（{step['time']}）" for step in item["path"])
            rows += (f"<tr><td>{index + 1}</td><td>{steps}</td><td>{item['hops']} 跳</td>"
                     f"<td>{currency} {money_display(item['total'])}</td></tr>")
        add(f"""<h3>多跳资金路径追踪</h3>
        <p>起点 <span class="mono">{paths['src']}</span> → 终点 <span class="mono">{paths['dst']}</span>；
        最多 {paths['max_hops']} 跳，共发现 {paths['count']} 条同币种路径。各边金额合计不等于守恒本金。</p>""")
        if rows:
            add(f"<table><tr><th>#</th><th>交易路径及金额</th><th>跳数</th><th>各边金额合计</th></tr>{rows}</table>")
        else:
            add("<p>当前搜索范围内未找到同币种可达路径。</p>")

    # ---------- 环路 ----------
    cyc = cases.get("cycles", {})
    if "cycles" in algorithms and cyc.get("count"):
        rows = "".join(
            f"<tr><td>{k + 1}</td><td>{' → '.join(c['accounts_names'])}</td>"
            f"<td>{c['len']} 跳</td><td>{c.get('currency') or '未标明币种'} {money_display(c['total'])}</td>"
            f"<td>{c.get('span_days', '—')} 天</td></tr>" for k, c in enumerate(cyc["cycles"]))
        sub_txt = "、".join(cases.get("subjects") or []) or "全案件"
        add(f"""<h3>资金回流环路检测（闭环对敲，{cyc.get('window_days', 7)} 天内完成）—— 分析对象：{sub_txt}</h3>
        <p>原理：资金从账户出发经若干账户<b>回到起点</b>，且环上全部交易在时限内完成——"跑分"团伙制造资金流假象、
        对敲刷流水的典型特征，可作为主观"明知"的佐证。共发现 <b>{cyc['count']} 个环路</b>。</p>
        <table><tr><th>#</th><th>资金闭环路径</th><th>跳数</th><th>环路金额合计</th><th>时间跨度</th></tr>{rows}</table>""")
    elif "cycles" in algorithms:
        add("<h3>资金回流环路检测</h3><p>未发现符合本次环路参数的资金闭环。</p>")

    # ---------- 快进快出配对 ----------
    ff = cases.get("fast_flows", {})
    if "fast_flows" in algorithms and ff.get("count"):
        rows = ""
        for p in ff["pairs"][:40]:
            currency = p.get('currency') or '未标明币种'
            ins = f"{currency} {money_display(p['in_flow']['amount'])}（{p['in_flow']['time'][5:16]} 来自 {p['in_flow']['from'] or '—'}）"
            outs = "<br>".join(f"{currency} {money_display(o['amount'])} → {o['to'] or o['to_acct']}" for o in p["out_flows"][:5])
            rows += (f"<tr><td>{p['account'][-6:]}<br><span class='mono'>{p['account']}</span></td>"
                     f"<td>{ins}</td><td>{outs}</td><td>{currency} {money_display(p['out_total'])}"
                     f"<br><span class='small'>差额 {money_display(p['diff'])}</span></td>"
                     f"<td>{p['max_delay_min']} 分钟</td></tr>")
        sub_txt2 = "、".join(ff.get("subjects") or []) if isinstance(ff.get("subjects"), list) else ""
        add(f"""<h3>快进快出金额守恒配对—— 分析对象：{sub_txt2 or '全案件'}</h3>
        <p>原理：账户收到大额资金后 {ff['window_minutes']} 分钟内转出，且转出合计与转入金额
        误差在 ±{ff['tolerance']:.0%} 内（<b>金额守恒</b>）——"代收代付/过账"行为的量化证据。
        共配对成功 <b>{ff['count']} 组</b>。</p>
        <table><tr><th>过账账户</th><th>转入</th><th>短窗内转出</th><th>转出合计/差额</th><th>最大延迟</th></tr>{rows}</table>""")
    elif "fast_flows" in algorithms:
        add("<h3>快进快出金额守恒配对</h3><p>未发现符合本次快进快出参数的配对。</p>")

    # ---------- 离群画像 ----------
    ol = cases.get("outliers", {})
    if "outliers" in algorithms and ol.get("count"):
        rows = "".join(
            f"<tr><td>{o['name'] or ''}<br><span class='mono'>{o['account']}</span></td>"
            f"<td>{o['explain']}</td><td>"
            f"{(o.get('currency') or '未标明币种') + ' ' + money_display(o['value']) if o['dim'] in ('total', 'avg_amount') else format(o['value'], ',')}"
            f"</td><td><b>{o['z']}</b></td></tr>"
            for o in ol["outliers"])
        add(f"""<h3>交易行为离群账户</h3>
        <p>原理：对每个账户提取行为特征（交易笔数、总额、深夜交易占比、整数金额占比、对手数量、单笔均额），
        与案件全体账户的基准分布对比，偏离超过 {ol['sigma']} 个标准差的账户标记为离群——
        其行为模式与正常经营明显不符。</p>
        <table><tr><th>账户</th><th>异常维度</th><th>数值</th><th>Z 分数</th></tr>{rows}</table>""")
    elif "outliers" in algorithms:
        add("<h3>交易行为离群账户</h3><p>未发现超过本次离群阈值的账户。</p>")

    # ---------- 总结 ----------
    summary = []
    if "paths" in algorithms and paths and paths.get("count"):
        summary.append(f"找到 {paths['count']} 条同币种资金路径")
    if "cycles" in algorithms and cyc.get("count"):
        summary.append(f"发现 {cyc['count']} 个资金回流环路")
    if "fast_flows" in algorithms and ff.get("count"):
        summary.append(f"{ff['count']} 组金额守恒的快进快出配对")
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
