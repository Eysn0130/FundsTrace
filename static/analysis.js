/* 智能分析页面逻辑 */
const preferenceStorage = window.FundsDesktopPreferences || localStorage;
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = (value, currency) => esc(FundsMoney.currency(value, currency));
const count = value => FundsMoney.count(value);
const dimensionValue = row => ['total', 'avg_amount'].includes(row.dim || row.dimension) ? money(row.value, row.currency) : esc(String(row.value));
let caseId = null, lastResults = null, lastRun = null;
let caseGeneration = 0, analysisRequest = 0;

function analysisContext() { return { caseId, generation: caseGeneration }; }
function isCurrentAnalysis(context) { return context.caseId === caseId && context.generation === caseGeneration; }
function selectAnalysisCase(id) {
  caseId = Number(id);
  caseGeneration += 1;
  analysisRequest += 1;
  lastResults = null;
  lastRun = null;
  $('#caseSel').value = String(caseId);
  $('#caseStats').textContent = '';
  $('#results').innerHTML = '<div class="card"><div class="hint">当前案件已切换，请核对参数后开始分析。</div></div>';
  $('#btnReport').disabled = true;
  $('#btnRun').disabled = false;
  $('#pbar').style.display = 'none';
  preferenceStorage.setItem('fund_case', caseId);
  const url = new URL(window.location.href);
  url.searchParams.set('case_id', String(caseId));
  window.history.replaceState(null, '', url);
  $('#backToGraph').href = `/?case_id=${encodeURIComponent(caseId)}`;
}

/* 水印 */
(function wm() {
  const c = document.createElement('canvas');
  c.width = 340; c.height = 220;
  const g = c.getContext('2d');
  g.translate(170, 110); g.rotate(-28 * Math.PI / 180);
  g.font = '15px "Microsoft YaHei"'; g.textAlign = 'center';
  g.fillStyle = 'rgba(255,255,255,0.055)';
  g.fillText('FundsTrace', 0, 0);
  g.font = '10px "Microsoft YaHei"'; g.fillText(new Date().toLocaleDateString('zh-CN'), 0, 18);
  const d = document.createElement('div');
  d.id = 'watermark';
  d.style.cssText = `position:fixed;inset:0;z-index:999;pointer-events:none;background-image:url(${c.toDataURL()});background-repeat:repeat;`;
  document.body.appendChild(d);
})();

async function api(path, body) {
  const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!res.ok) {
    let m = await res.text();
    try { m = JSON.parse(m).detail || m; } catch (e) { /* ignore */ }
    throw new Error(String(m).slice(0, 300));
  }
  return res.json();
}

async function loadCases() {
  const cases = await (await fetch('/api/cases')).json();
  $('#caseSel').innerHTML = cases.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
  const saved = new URLSearchParams(window.location.search).get('case_id') || preferenceStorage.getItem('fund_case');
  const savedCase = cases.find(c => String(c.id) === saved);
  const pick = savedCase || cases[0];
  if (pick) { selectAnalysisCase(pick.id); await showStats(); }
}

async function showStats() {
  const context = analysisContext();
  const s = await (await fetch(`/api/stats?case_id=${context.caseId}`)).json();
  if (!isCurrentAnalysis(context)) return;
  $('#caseStats').textContent = `流水 ${count(s.flows)} 笔 · 账户 ${count(s.accounts)} 个`;
}

function collectParams() {
  const algs = $$('.alg').filter(c => c.checked).map(c => c.value);
  const subjects = $('#subjects').value.split(/[,，;；\n\r\s]+/).map(x => x.trim()).filter(Boolean);
  const toleranceText = $('#ffTol').value.trim();
  const tolerance = toleranceText === '' ? 0.25 : Number(toleranceText);
  if (!Number.isFinite(tolerance) || tolerance < 0 || tolerance > 1) throw new Error('配对金额容差须在 0 至 1 之间');
  const cycleText = $('#cycDays').value.trim(), cycleDays = cycleText === '' ? 7 : Number(cycleText);
  if (!Number.isInteger(cycleDays) || cycleDays < 1 || cycleDays > 365) throw new Error('环流时限须为 1 至 365 天的整数');
  const windowText = $('#ffWin').value.trim(), windowMinutes = windowText === '' ? 30 : Number(windowText);
  if (!Number.isInteger(windowMinutes) || windowMinutes < 5 || windowMinutes > 1440) throw new Error('快进快出窗口须为 5 至 1440 分钟的整数');
  const params = {
    algorithms: algs,
    subjects,
    ff_window_minutes: windowMinutes,
    ff_tolerance: tolerance,
    cycle_window_days: cycleDays,
  };
  if (algs.includes('paths')) {
    const src = $('#pSrc').value.trim(), dst = $('#pDst').value.trim();
    const hops = Number($('#pHops').value);
    if (!src || !dst) throw new Error('路径追踪需要填写起点账号与终点账号');
    if (!Number.isInteger(hops) || hops < 1 || hops > 12) throw new Error('路径跳数须为 1 至 12 的整数');
    params.path_src = src;
    params.path_dst = dst;
    params.path_max_hops = hops;
  }
  return { algs, params };
}

function setBar(pct) {
  const b = $('#pbar');
  b.style.display = 'block';
  b.classList.toggle('indeterminate', pct == null);
  $('#pfill').style.width = pct == null ? '38%' : Math.round(pct * 100) + '%';
}

async function run() {
  let collected;
  try { collected = collectParams(); } catch (error) { alert(error.message); return; }
  const { algs, params } = collected;
  if (!algs.length) return alert('请至少勾选一个算法');
  const context = analysisContext();
  const request = ++analysisRequest;
  const current = () => isCurrentAnalysis(context) && request === analysisRequest;
  lastResults = null;
  lastRun = null;
  $('#btnRun').disabled = true;
  $('#btnReport').disabled = true;
  setBar(null);
  try {
    const results = await api('/api/analysis/run', { case_id: context.caseId, params });
    if (!current()) return;
    lastResults = results;
    lastRun = { ...context, params };
    render(results, algs);
    $('#btnReport').disabled = false;
  } catch (e) {
    if (current()) alert('分析失败：' + e.message);
  } finally {
    if (current()) {
      $('#pbar').style.display = 'none';
      $('#btnRun').disabled = false;
    }
  }
}

function card(title, principle, inner, count) {
  return `<div class="card"><h3>${title}${count != null ? ` — 共 <span class="count">${count}</span> 项` : ''}</h3>
    <div class="principle">${principle}</div>${inner}</div>`;
}

function tbl(head, rows) {
  if (!rows) return '<div class="empty">无数据</div>';
  return `<div class="table-scroll" tabindex="0" role="region" aria-label="分析结果表格"><table><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr>${rows}</table></div>`;
}

function render(res, algs) {
  const out = [];
  if (algs.includes('cycles') && res.cycles) {
    const c = res.cycles;
    out.push(card(`环路检测（资金回流 · ${c.window_days ?? 7} 天内完成）${res.subjects && res.subjects.length ? ' · 对象：' + esc(res.subjects.join('、')) : ''}`,
      '资金从账户出发经若干账户回到起点，且环上全部交易在时限内完成——短窗闭环是跑分团伙制造资金流假象、对敲刷流水的典型特征（跨月的闭环更可能是正常经营往来），可作为主观"明知"的佐证。',
      c.cycles.length ? tbl(['#', '环路路径', '跳数', '环路金额', '时间跨度', '明细'],
        c.cycles.map((x, k) => {
          const ids = x.edges.map(e => e.id).join(',');
          return `<tr class="cyc-row" data-ids="${ids}"><td>${k + 1}</td><td>${esc(x.accounts_names.join(' → '))}</td>
          <td>${x.len} 跳</td><td class="amt">${money(x.total, x.currency)}</td><td>${x.span_days != null ? x.span_days + ' 天' : '—'}</td>
          <td><button class="btn btn-detail">查看明细</button></td></tr>`;
        }).join('')) : '<div class="empty">在时限内未发现资金闭环（可放宽环流时限再试）</div>',
      c.count));
  }
  if (algs.includes('split') && res.split) {
    const s = res.split;
    out.push(card(`3. 大额拆分规避检测（${s.window_days} 天窗口 / 单笔 ${s.amt_range[0]}~${s.amt_range[1]} 元 / 合计≥${s.total_min} 元）`,
      '同一账户短窗口内多笔临界金额转出且合计超 5 万申报线——刻意规避监管的行为特征，可作为主观故意佐证。',
      s.groups.length ? tbl(['账户', '时间范围', '笔数', '合计', '对手概览'],
        s.groups.map(g => `<tr><td>${esc(g.name || '')}<br><span class="mono">${esc(g.account)}</span></td>
          <td>${g.start.slice(5, 16)} ~ ${g.end.slice(5, 16)}</td><td>${g.count} 笔</td><td class="amt">${money(g.total, g.currency)}</td>
          <td>${esc(g.flows.slice(0, 3).map(x => `${money(x.amount, x.currency)}→${x.to || x.to_acct}`).join('；'))}${g.flows.length > 3 ? '…' : ''}</td></tr>`)) : '<div class="empty">未发现拆分规避行为</div>',
      s.count));
  }
  if (algs.includes('fast_flows') && res.fast_flows) {
    const f = res.fast_flows;
    out.push(card(`快进快出金额守恒配对（${f.window_minutes} 分钟内 ±${(f.tolerance * 100).toFixed(0)}%）${res.subjects && res.subjects.length ? ' · 对象：' + esc(res.subjects.join('、')) : ''}`,
      '账户收到大额资金后短窗内转出且金额守恒——代收代付/过账行为的量化证据。',
      f.pairs.length ? tbl(['过账账户', '转入', '短窗内转出', '转出合计/差额', '最大延迟', '明细'],
        f.pairs.map(p => {
          const ids = [p.in_flow.id, ...p.out_flows.map(o => o.id)].join(',');
          return `<tr class="ff-row" data-ids="${ids}"><td class="mono">${esc(p.account)}</td>
          <td class="amt">${money(p.in_flow.amount, p.in_flow.currency)}<br><span class="hint">${esc(p.in_flow.from || '')} ${p.in_flow.time.slice(5, 16)}</span></td>
          <td>${p.out_flows.map(o => `${money(o.amount, o.currency)} → ${esc(o.to || o.to_acct)}`).join('<br>')}</td>
          <td class="amt">${money(p.out_total, p.currency)}<br><span class="hint">差额 ${money(p.diff, p.currency)}</span></td>
          <td>${p.max_delay_min} 分钟</td>
          <td><button class="btn btn-detail">查看明细</button></td></tr>`;
        }).join('')) : '<div class="empty">未发现金额守恒的快进快出配对</div>',
      f.count));
  }
  if (algs.includes('outliers') && res.outliers) {
    const o = res.outliers;
    out.push(card(`离群账户画像（偏离 > ${o.sigma}σ）${res.subjects && res.subjects.length ? ' · 对象：' + esc(res.subjects.join('、')) : ''}`,
      '对每个账户提取行为特征（笔数/总额/深夜占比/整额占比/对手数/单笔均额）与案件基准对比，金额按币种分别计算；均额按分舍入，Z 分数及占比为近似统计值，离群仅供人工复核。',
      o.outliers.length ? tbl(['账户', '异常维度', '数值', 'Z 分数'],
        o.outliers.map(x => `<tr><td>${esc(x.name || '')}<br><span class="mono">${esc(x.account)}</span></td>
          <td>${esc(x.explain)}</td><td>${dimensionValue(x)}</td><td><b>${x.z}</b></td></tr>`)) : '<div class="empty">未发现离群账户</div>',
      o.count));
  }
  if (algs.includes('paths') && res.paths) {
    const p = res.paths;
    out.push(card(`路径追踪：${esc(p.src_name || p.src)} → ${esc(p.dst_name || p.dst)}`,
      `最大 ${p.max_hops} 跳，按币种分别排列交易边金额合计；多跳合计不代表去重本金。`,
      p.paths.length ? tbl(['#', '累计金额', '跳数', '路径'],
        p.paths.map((x, k) => `<tr><td>${k + 1}</td><td class="amt">${money(x.total, x.currency)}</td><td>${x.hops} 跳</td>
          <td>${x.path.map(s => `${esc(s.from_name || s.from)}<span class="hint"> —${money(s.amount, s.currency)}→ </span>${esc(s.to_name || s.to)}`).join('<br>')}</td></tr>`)) : '<div class="empty">未找到可达路径（尝试增大跳数）</div>',
      p.count));
  }
  $('#results').innerHTML = out.join('') || '<div class="card"><div class="empty">无结果</div></div>';
}

/* 路径追踪参数显隐 + 起终点快捷选择 */
document.addEventListener('change', e => {
  if (e.target.classList && e.target.classList.contains('alg')) {
    $('#pathParams').style.display = $$('.alg').some(c => c.checked && c.value === 'paths') ? 'block' : 'none';
  }
});

$('#btnRun').onclick = run;

/* ---------- 环路/配对的流水明细展开 ---------- */
function detailTable(rows) {
  return `<table class="mini"><tr><th>时间</th><th>付款方</th><th>收款方</th><th>金额</th><th>余额</th><th>摘要</th></tr>
    ${rows.map(r => `<tr><td class="nowrap">${esc(String(r.trade_time).slice(0, 19))}</td>
      <td>${esc(r.payer_name || '')}<br><span class="mono">${esc(r.payer_account)}</span></td>
      <td>${esc(r.payee_name || '')}<br><span class="mono">${esc(r.payee_account)}</span></td>
      <td class="amt">${money(r.amount, r.currency)}</td><td class="amt">${r.balance != null ? money(r.balance, r.currency) : '—'}</td>
      <td>${esc(r.summary || '')}</td></tr>`).join('')}</table>`;
}

document.addEventListener('click', async e => {
  const btn = e.target.closest('.btn-detail');
  if (!btn) return;
  const row = btn.closest('tr');
  const next = row.nextElementSibling;
  if (next && next.classList.contains('detail-row')) {   // 已展开则收起
    next.remove();
    btn.textContent = '查看明细';
    return;
  }
  btn.disabled = true;
  const context = analysisContext();
  const run = lastRun;
  try {
    const ids = row.dataset.ids;
    const r = await (await fetch(`/api/flows?ids=${ids}&case_id=${context.caseId}&size=500`)).json();
    if (!isCurrentAnalysis(context) || lastRun !== run) return;
    const det = document.createElement('tr');
    det.className = 'detail-row';
    det.innerHTML = `<td colspan="6" style="background:#15181e;padding:8px 10px">
      ${r.rows.length ? detailTable(r.rows) : '<div class="empty">流水不存在（可能已被删除）</div>'}</td>`;
    row.after(det);
    btn.textContent = '收起明细';
  } catch (err) {
    if (isCurrentAnalysis(context) && lastRun === run) alert('明细加载失败：' + err.message);
  } finally {
    btn.disabled = false;
  }
});

$('#btnReport').onclick = async () => {
  const run = lastRun;
  if (!lastResults || !run || !isCurrentAnalysis(run)) return;
  const current = () => isCurrentAnalysis(run) && lastRun === run;
  $('#btnReport').disabled = true;
  try {
    const res = await fetch('/api/analysis/report', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ case_id: run.caseId, params: run.params }) });
    if (!current()) return;
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    if (!current()) return;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `资金分析报告_案件${run.caseId}.html`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  } catch (e) {
    if (current()) alert('报告生成失败：' + e.message);
  } finally {
    if (current()) $('#btnReport').disabled = false;
  }
};

$('#caseSel').onchange = e => { selectAnalysisCase(e.target.value); void showStats(); };

loadCases();
