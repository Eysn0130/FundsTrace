/* Whole-case analysis: explicit runs, immutable result parameters and case-bound reads. */
const preferenceStorage = window.FundsDesktopPreferences || localStorage;
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = (value, currency) => esc(FundsMoney.currency(value, currency));
const count = value => FundsMoney.count(value);
const percent = value => new Intl.NumberFormat('zh-CN', { style: 'percent', maximumSignificantDigits: 15, useGrouping: false }).format(value);
const dimensionValue = row => {
  const dimension = row.dim || row.dimension;
  if (['total', 'avg_amount'].includes(dimension)) return money(row.value, row.currency);
  return esc(String(row.value)) + (['cnt', 'night', 'round'].includes(dimension) ? ' 笔' : dimension === 'counter_size' ? ' 个' : '');
};
const dimensionLabel = row => ({ cnt: '交易笔数异常多', night: '深夜交易笔数异常多', round: '整额交易笔数异常多',
  counter_size: '交易对手数异常多', total: '交易总额异常大', avg_amount: '单笔均额异常' }[row.dim || row.dimension] || row.explain || '统计维度');
const analysisMethods = {
  cycles: { label: '环路检测', list: 'cycles' }, fast_flows: { label: '快进快出', list: 'pairs' },
  outliers: { label: '离群画像', list: 'outliers' }, paths: { label: '路径追踪', list: 'paths' },
};
const resultOrder = ['paths', 'cycles', 'fast_flows', 'outliers'];
let caseId = null, lastResults = null, lastRun = null;
let caseGeneration = 0, analysisRequest = 0, caseLoadRequest = 0, statsRequest = 0, resultRevision = 0;
let analysisPhase = 'loading', casesLoading = false, reportPending = false, confirmation = null;
let analysisCases = [], currentStats = null, draftBaseline = '', activeResult = null, renderedResults = null, renderedAlgorithms = [];
const resultScrolls = new Map();
let tableSerial = 0;

function analysisContext() { return { caseId, generation: caseGeneration }; }
function isCurrentAnalysis(context) { return !!context && context.caseId === caseId && context.generation === caseGeneration; }
function draftFingerprint() {
  return JSON.stringify({ algorithms: $$('.alg').filter(input => input.checked).map(input => input.value),
    fields: ['subjects', 'cycDays', 'ffWin', 'ffTol', 'pSrc', 'pDst', 'pHops'].map(id => $('#' + id).value) });
}
function hasAnalysisDraft() { return !!draftBaseline && draftFingerprint() !== draftBaseline; }
function analysisBusy() { return analysisPhase === 'running' || reportPending; }
function analysisError(message = '') { $('#analysisError').textContent = message; $('#analysisError').hidden = !message; }
function disposeResultTables(root = null) {
  for (const [element, control] of resultScrolls) if (!root || root.contains(element)) { control?.destroy(); resultScrolls.delete(element); }
}
function clearResults(message, detail = '') {
  resultRevision += 1; disposeResultTables();
  $('#resultScope').textContent = '本地分析 · 全部导入流水';
  $('#results').innerHTML = `<div class="analysis-empty"><span>${esc(message)}</span>${detail ? `<p>${esc(detail)}</p>` : ''}</div>`;
}
function syncAlgorithmFields() {
  const selected = new Set($$('.alg').filter(input => input.checked).map(input => input.value));
  for (const [id, enabled] of [['cycleParams', selected.has('cycles')], ['ffParams', selected.has('fast_flows')], ['pathParams', selected.has('paths')],
    ['subjectParams', ['cycles', 'fast_flows', 'outliers'].some(name => selected.has(name))]]) {
    $('#' + id).hidden = !enabled; $('#' + id).style.display = enabled ? '' : 'none';
  }
  $('#outlierParams').hidden = !selected.has('outliers') || selected.has('cycles') || selected.has('fast_flows') || selected.has('paths');
  $('#pSrc').required = $('#pDst').required = selected.has('paths');
  $('#pSrc').setAttribute('aria-required', String(selected.has('paths'))); $('#pDst').setAttribute('aria-required', String(selected.has('paths')));
  syncAnalysisControls();
}
function syncAnalysisControls() {
  const busy = analysisBusy(), blocked = busy || !caseId || casesLoading || !!confirmation;
  for (const input of $$('#analysisParams input, #analysisParams textarea')) input.disabled = blocked;
  for (const [id, algorithms] of [['cycDays', ['cycles']], ['ffWin', ['fast_flows']], ['ffTol', ['fast_flows']], ['subjects', ['cycles', 'fast_flows', 'outliers']],
    ['pSrc', ['paths']], ['pDst', ['paths']], ['pHops', ['paths']]]) {
    $('#' + id).disabled = blocked || !$$('.alg').some(input => input.checked && algorithms.includes(input.value));
  }
  $('#caseSel').disabled = casesLoading || !analysisCases.length;
  $('#btnRun').disabled = blocked;
  $('#btnRun').textContent = analysisPhase === 'running' ? '分析中…' : lastRun ? '重新分析' : '开始分析';
  $('#btnReport').disabled = busy || !!confirmation || !lastResults || !lastRun || !isCurrentAnalysis(lastRun) || analysisPhase !== 'done';
  $('#btnReport').setAttribute('aria-busy', String(reportPending));
  $('#results').setAttribute('aria-busy', String(analysisPhase === 'running'));
  $('#draftStatus').textContent = hasAnalysisDraft() ? '参数已修改' : '';
  for (const tab of $$('.analysis-result-tab')) tab.disabled = blocked || !renderedAlgorithms.includes(tab.dataset.algorithm) || analysisPhase !== 'done';
}
function setAnalysisPhase(phase) {
  analysisPhase = phase;
  const labels = { loading: '读取中', ready: '待运行', running: '分析中', done: '已完成', error: '未完成', empty: '无案件' };
  $('#runStatus').dataset.state = phase;
  $('#runStatus').innerHTML = `${['running', 'done'].includes(phase) ? `<img class="ui-icon" src="/static/ui-icons/${phase === 'done' ? 'check' : 'refresh'}.svg" alt="">` : ''}<span>${labels[phase] || ''}</span>`;
  syncAnalysisControls();
}
function dismissAnalysisConfirmation() { confirmation?.finish(false); }
function confirmAnalysisAction({ title, message, accept = '确定', cancel = '取消' }) {
  if (confirmation) return Promise.resolve(false);
  const context = analysisContext(), dialog = $('#analysisConfirm'), trigger = document.activeElement;
  $('#confirmTitle').textContent = title; $('#confirmMessage').textContent = message;
  $('#confirmAccept').textContent = accept; $('#confirmCancel').textContent = cancel; dialog.returnValue = '';
  return new Promise(resolve => {
    const pending = confirmation = { context, finish(accepted) {
      if (confirmation !== pending) return;
      confirmation = null; dialog.onclose = null;
      if (dialog.open) dialog.close(accepted ? 'confirm' : 'cancel');
      syncAnalysisControls();
      if (isCurrentAnalysis(context) && trigger?.isConnected && !trigger.disabled) trigger.focus();
      resolve(accepted && isCurrentAnalysis(context));
    } };
    dialog.onclose = () => pending.finish(dialog.returnValue === 'confirm');
    $('#confirmAccept').onclick = () => pending.finish(true);
    $('#confirmCancel').onclick = $('#confirmClose').onclick = () => pending.finish(false);
    dialog.showModal(); syncAnalysisControls();
  });
}
function selectAnalysisCase(id) {
  dismissAnalysisConfirmation();
  caseId = id == null ? null : Number(id); caseGeneration += 1; analysisRequest += 1; statsRequest += 1;
  lastResults = lastRun = renderedResults = null; renderedAlgorithms = []; activeResult = null; currentStats = null; reportPending = false;
  $('#caseSel').value = caseId == null ? '' : String(caseId);
  for (const id of ['subjects', 'pSrc', 'pDst']) $('#' + id).value = '';
  for (const input of $$('#analysisParams [aria-invalid]')) input.removeAttribute('aria-invalid');
  $('#caseStats').textContent = caseId ? '正在读取统计…' : '暂无案件'; $('#statsRetry').hidden = true;
  $('#reportStatus').textContent = ''; analysisError(); $('#analysisScopeNote').textContent = '';
  $('#pbar').hidden = true; $('#pbar').style.display = 'none';
  clearResults(caseId ? '尚未运行分析' : '暂无案件', caseId ? '全部导入流水 · 本地计算' : '返回图谱新建案件');
  draftBaseline = draftFingerprint(); setAnalysisPhase(caseId ? 'ready' : 'empty'); renderResultTabs(); syncAlgorithmFields();
  $('#resultScope').textContent = '本地分析 · 全部导入流水';
  if (caseId) {
    preferenceStorage.setItem('fund_case', String(caseId));
    const url = new URL(window.location.href); url.searchParams.set('case_id', String(caseId)); window.history.replaceState(null, '', url);
  }
  $('#backToGraph').href = caseId ? `/?case_id=${encodeURIComponent(caseId)}` : '/';
}

/* Reuse the product watermark and supplied brand assets. */
(function wm() {
  const canvas = document.createElement('canvas'); canvas.width = 340; canvas.height = 220;
  const context = canvas.getContext('2d'); context.translate(170, 110); context.rotate(-28 * Math.PI / 180);
  context.font = '15px "Microsoft YaHei"'; context.textAlign = 'center'; context.fillStyle = 'rgba(255,255,255,0.055)'; context.fillText('FundsTrace', 0, 0);
  context.font = '10px "Microsoft YaHei"'; context.fillText(new Date().toLocaleDateString('zh-CN'), 0, 18);
  $('#watermark').style.backgroundImage = `url(${canvas.toDataURL()})`;
})();
async function responseError(response) {
  let message = await response.text();
  try { message = JSON.parse(message).detail || message; } catch (_) { /* plain text */ }
  return String(message).slice(0, 300);
}
async function getJson(path) { const response = await fetch(path); if (!response.ok) throw new Error(await responseError(response)); return response.json(); }
async function api(path, body) {
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!response.ok) throw new Error(await responseError(response));
  return response.json();
}
async function loadCases() {
  const request = ++caseLoadRequest; casesLoading = true; $('#caseRetry').hidden = true; syncAnalysisControls();
  try {
    const cases = await getJson('/api/cases');
    if (request !== caseLoadRequest) return;
    if (!Array.isArray(cases)) throw new Error('案件列表格式不正确');
    analysisCases = cases; casesLoading = false;
    $('#caseSel').innerHTML = cases.length ? cases.map(item => `<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('') : '<option value="">暂无案件</option>';
    const saved = new URLSearchParams(window.location.search).get('case_id') || preferenceStorage.getItem('fund_case');
    const pick = cases.find(item => String(item.id) === String(saved)) || cases[0];
    selectAnalysisCase(pick?.id ?? null);
    if (pick) await showStats();
  } catch (error) {
    if (request !== caseLoadRequest) return;
    casesLoading = false; analysisCases = []; selectAnalysisCase(null); setAnalysisPhase('error');
    $('#caseStats').textContent = '案件读取未完成'; $('#caseRetry').hidden = false;
    analysisError('案件读取未完成：' + error.message); clearResults('案件读取未完成', '可重试读取案件');
  }
}
async function showStats() {
  if (!caseId) return;
  const context = analysisContext(), request = ++statsRequest;
  $('#statsRetry').hidden = true;
  try {
    const stats = await getJson(`/api/stats?case_id=${context.caseId}`);
    if (!isCurrentAnalysis(context) || request !== statsRequest) return;
    currentStats = stats;
    $('#caseStats').textContent = `全部导入流水，${count(stats.flows)} 笔 · ${count(stats.accounts)} 个账户`;
    if (!lastRun && analysisPhase !== 'running' && String(stats.flows) === '0') clearResults('当前案件暂无已导入流水');
  } catch (error) {
    if (!isCurrentAnalysis(context) || request !== statsRequest) return;
    currentStats = null; $('#caseStats').textContent = '统计读取未完成'; $('#statsRetry').hidden = false;
  }
}
function parameterError(message, id) { const error = new Error(message); error.field = id; throw error; }
function integerParameter(id, fallback, minimum, maximum, label) {
  const text = $('#' + id).value.trim(), value = text === '' ? fallback : Number(text);
  if (!Number.isInteger(value) || value < minimum || value > maximum) parameterError(`${label}须为 ${minimum} 至 ${maximum} 的整数`, id);
  return value;
}
function collectParams() {
  const algs = $$('.alg').filter(input => input.checked).map(input => input.value).filter(name => analysisMethods[name]);
  if (!algs.length) parameterError('请至少选择一类分析算法', null);
  const params = { algorithms: algs };
  if (algs.some(name => name !== 'paths')) params.subjects = $('#subjects').value.split(/[,，;；\n\r\s]+/).map(value => value.trim()).filter(Boolean);
  if (algs.includes('cycles')) params.cycle_window_days = integerParameter('cycDays', 7, 1, 365, '环流时限（天）');
  if (algs.includes('fast_flows')) {
    params.ff_window_minutes = integerParameter('ffWin', 30, 5, 1440, '快进快出窗口（分钟）');
    const text = $('#ffTol').value.trim(), percent = text === '' ? 25 : Number(text);
    if (!Number.isFinite(percent) || percent < 0 || percent > 100) parameterError('配对金额容差须在 0% 至 100% 之间', 'ffTol');
    params.ff_tolerance = percent / 100;
  }
  if (algs.includes('paths')) {
    const src = $('#pSrc').value.trim(), dst = $('#pDst').value.trim();
    if (!src || !dst) parameterError('路径追踪需要填写起点账号与终点账号', !src ? 'pSrc' : 'pDst');
    params.path_src = src; params.path_dst = dst; params.path_max_hops = integerParameter('pHops', 4, 1, 12, '路径跳数');
  }
  return { algs, params };
}
function setBar() { $('#pbar').hidden = false; $('#pbar').style.display = ''; }
function validateAnalysisResult(result, algorithms, context) {
  if (!result || typeof result !== 'object' || result.case_id != null && String(result.case_id) !== String(context.caseId)) throw new Error('分析结果与当前案件不匹配');
  for (const name of algorithms) if (!result[name] || !Array.isArray(result[name][analysisMethods[name].list])) throw new Error(`${analysisMethods[name].label}结果未完整返回，请重试`);
}
async function run() {
  if (!caseId || casesLoading || analysisBusy() || confirmation) return;
  let collected;
  try { collected = collectParams(); } catch (error) {
    analysisError(error.message);
    if (error.field) { const input = $('#' + error.field); input.setAttribute('aria-invalid', 'true'); input.focus(); }
    else $$('.alg')[0]?.focus();
    return;
  }
  const { algs, params } = collected, context = analysisContext(), request = ++analysisRequest;
  const current = () => isCurrentAnalysis(context) && request === analysisRequest;
  const frozenParams = Object.freeze({ ...params, algorithms: Object.freeze([...algs]), ...(params.subjects ? { subjects: Object.freeze([...params.subjects]) } : {}) });
  lastResults = lastRun = renderedResults = null; renderedAlgorithms = []; draftBaseline = draftFingerprint();
  analysisError(); $('#reportStatus').textContent = ''; $('#analysisScopeNote').textContent = '';
  for (const input of $$('#analysisParams [aria-invalid]')) input.removeAttribute('aria-invalid');
  clearResults('正在分析', '全部导入流水 · 本地计算'); setAnalysisPhase('running'); renderResultTabs(); setBar();
  try {
    const result = await api('/api/analysis/run', { case_id: context.caseId, params: frozenParams });
    if (!current()) return;
    validateAnalysisResult(result, algs, context);
    lastResults = result; lastRun = { ...context, params: frozenParams, sourceStats: result.source_snapshot ? { ...result.source_snapshot } : null };
    render(result, algs); setAnalysisPhase('done');
    $('#resultScope').textContent = lastRun.sourceStats ? `本次结果 · 全部导入流水，${count(lastRun.sourceStats.flows)} 笔 · ${count(lastRun.sourceStats.accounts)} 个账户` : '本次结果 · 全部导入流水';
  } catch (error) {
    if (!current()) return;
    lastResults = lastRun = renderedResults = null; renderedAlgorithms = [];
    clearResults('本次分析未完成', '参数已保留，可重新运行'); setAnalysisPhase('error'); renderResultTabs(); analysisError('分析未完成：' + error.message);
  } finally {
    if (current()) { $('#pbar').hidden = true; $('#pbar').style.display = 'none'; syncAnalysisControls(); }
  }
}
function card(title, principle, inner, countValue) {
  return `<div class="analysis-result-heading"><h2>${title}</h2>${countValue != null ? `<p>共 ${count(countValue)} 项</p>` : ''}</div>${principle ? `<p class="analysis-result-principle">${principle}</p>` : ''}${inner}`;
}
function tableShell(table, label = '分析结果') {
  const id = `analysisTable${++tableSerial}`;
  return `<div class="analysis-table-shell"><div id="${id}" class="analysis-table-scroll table-scroll" data-table-viewport tabindex="0" role="region" aria-label="${esc(label)}表格，可水平滚动">${table}</div><div class="flow-horizontal-scroll analysis-horizontal-scroll" role="group" aria-label="${esc(label)}表格水平滚动"><button class="btn small" data-scroll-left type="button" aria-label="${esc(label)}向左滚动"><img class="ui-icon" src="/static/ui-icons/chevron-left.svg" alt=""></button><input type="range" data-scroll-range min="0" max="0" value="0" step="1" aria-controls="${id}" aria-label="${esc(label)}水平位置"><button class="btn small" data-scroll-right type="button" aria-label="${esc(label)}向右滚动"><img class="ui-icon" src="/static/ui-icons/chevron-right.svg" alt=""></button></div></div>`;
}
function tbl(head, rows, label = '分析结果') {
  if (Array.isArray(rows)) rows = rows.join('');
  if (!rows) return '<div class="analysis-empty">当前参数下未发现相关结果</div>';
  return tableShell(`<table class="analysis-table${label === '路径追踪' ? ' analysis-path-table' : ''}"><thead><tr>${head.map((heading, index) => `<th scope="col"${heading === '明细' ? ' class="analysis-table-action"' : index === 1 && label === '路径追踪' ? ' class="amt"' : ''}>${esc(heading)}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table>`, label);
}
function bindResultTables(root = $('#results')) {
  for (const shell of root.querySelectorAll('.analysis-table-shell')) {
    if (resultScrolls.has(shell)) continue;
    resultScrolls.set(shell, window.FundsTableScroll.bind({ viewport: shell.querySelector('[data-table-viewport]'), range: shell.querySelector('[data-scroll-range]'),
      left: shell.querySelector('[data-scroll-left]'), right: shell.querySelector('[data-scroll-right]'), table: shell.querySelector('table') }));
  }
}
function renderResultTabs() {
  $('#resultTabs').innerHTML = resultOrder.map(name => {
    const included = renderedAlgorithms.includes(name), waiting = analysisPhase === 'running' && $$('.alg').some(input => input.checked && input.value === name);
    const value = included && renderedResults?.[name] ? count(renderedResults[name].count) : waiting ? '待结果' : '未运行';
    return `<button type="button" id="resultTab-${name}" class="analysis-result-tab" role="tab" data-algorithm="${name}" aria-controls="results" aria-selected="${name === activeResult}" tabindex="${name === activeResult ? '0' : '-1'}"${!included ? ' disabled' : ''}>${analysisMethods[name].label}<span class="analysis-tab-count">${value}</span></button>`;
  }).join('');
  for (const element of $$('[data-alg-count]')) element.textContent = renderedAlgorithms.includes(element.dataset.algCount) && renderedResults?.[element.dataset.algCount] ? count(renderedResults[element.dataset.algCount].count) : '—';
  syncAnalysisControls();
}
function renderResultCategory(name) {
  if (!renderedAlgorithms.includes(name) || !renderedResults?.[name]) return;
  activeResult = name; resultRevision += 1; disposeResultTables();
  const res = renderedResults, value = res[name];
  const subjects = res.subjects?.length && name !== 'paths' ? ` · 对象：${esc(res.subjects.join('、'))}` : '';
  $('#analysisScopeNote').textContent = name === 'paths' ? '按交易边累加，非去重本金。' : '';
  let content = '';
  if (name === 'cycles') content = card(`环路检测 · ${esc(value.window_days ?? 7)} 天内${subjects}`, '有界搜索中的资金闭环线索，需结合原始流水核实。',
    value.cycles.length ? tbl(['序号', '环路路径', '跳数', '交易边金额合计', '时间跨度', '明细'], value.cycles.map((item, index) => {
      const ids = item.edges.map(edge => String(edge.id)).join(',');
      return `<tr data-ids="${esc(ids)}"><td class="analysis-sequence">${index + 1}</td><td>${esc(item.accounts_names.join(' → '))}</td><td class="analysis-hops">${esc(item.len)} 跳</td><td class="amt">${money(item.total, item.currency)}</td><td>${item.span_days != null ? esc(item.span_days) + ' 天' : '—'}</td><td class="analysis-table-action"><button class="btn small btn-detail" type="button" aria-expanded="false">查看明细</button></td></tr>`;
    }).join(''), '环路检测') : '<div class="analysis-empty">当前参数下未发现资金闭环</div>', value.count);
  if (name === 'fast_flows') content = card(`快进快出 · ${esc(value.window_minutes)} 分钟内 ±${esc(percent(value.tolerance))}${subjects}`, '短窗口内按币种配对转入与转出，差额与时间仅作为复核线索。',
    value.pairs.length ? tbl(['过账账户', '转入', '短窗内转出', '转出合计 / 差额', '最大延迟', '明细'], value.pairs.map(item => {
      const ids = [item.in_flow.id, ...item.out_flows.map(flow => flow.id)].map(String).join(',');
      return `<tr data-ids="${esc(ids)}"><td class="mono">${esc(item.account)}</td><td class="amt">${money(item.in_flow.amount, item.in_flow.currency)}<br><span class="hint">${esc(item.in_flow.from || '')} ${esc(String(item.in_flow.time || '').slice(0, 19))}</span></td><td>${item.out_flows.map(flow => `${money(flow.amount, flow.currency)} → ${esc(flow.to || flow.to_acct)}`).join('<br>')}</td><td class="amt">${money(item.out_total, item.currency)}<br><span class="hint">差额 ${money(item.diff, item.currency)}</span></td><td>${esc(item.max_delay_min)} 分钟</td><td class="analysis-table-action"><button class="btn small btn-detail" type="button" aria-expanded="false">查看明细</button></td></tr>`;
    }).join(''), '快进快出') : '<div class="analysis-empty">当前参数下未发现快进快出配对</div>', value.count);
  if (name === 'outliers') content = card(`离群画像 · 偏离 > ${esc(value.sigma)}σ${subjects}`, '按币种比较账户与案件基准；均值与 Z 分数为近似统计值。',
    value.outliers.length ? tbl(['账户', '币种', '异常维度', '数值', 'Z 分数（近似）'], value.outliers.map(item => `<tr><td>${esc(item.name || '')}<br><span class="mono">${esc(item.account)}</span></td><td>${esc(item.currency || '未知币种')}</td><td>${esc(dimensionLabel(item))}</td><td>${dimensionValue(item)}</td><td>${esc(item.z)}</td></tr>`).join(''), '离群画像') : '<div class="analysis-empty">当前参数下未发现离群账户</div>', value.count);
  if (name === 'paths') content = card(`${esc(value.src_name || value.src)} → ${esc(value.dst_name || value.dst)} · 最大 ${esc(value.max_hops)} 跳`, '',
    value.paths.length ? tbl(['序号', '交易边金额合计', '跳数', '路径'], value.paths.map((item, index) => `<tr><td class="analysis-sequence">${String(index + 1).padStart(2, '0')}</td><td class="amt">${money(item.total, item.currency)}</td><td class="analysis-hops">${esc(item.hops)} 跳</td><td>${item.path.map(leg => `<span class="analysis-path-leg">${esc(leg.from_name || leg.from)}<span class="hint"> —${money(leg.amount, leg.currency)}→ </span>${esc(leg.to_name || leg.to)}</span>`).join('')}</td></tr>`).join(''), '路径追踪') : '<div class="analysis-empty">当前跳数与参数下未找到可达路径</div>');
  $('#results').innerHTML = content; $('#results').setAttribute('aria-labelledby', 'resultTab-' + name);
  renderResultTabs(); bindResultTables();
}
function render(result, algorithms) {
  renderedResults = result; renderedAlgorithms = [...algorithms];
  const preferred = activeResult && algorithms.includes(activeResult) ? activeResult : resultOrder.find(name => algorithms.includes(name) && result[name]?.count > 0) || algorithms[0];
  renderResultCategory(preferred);
}
function detailTable(rows) {
  return `<table class="analysis-table mini"><thead><tr>${['时间', '付款方', '收款方', '金额', '余额', '摘要'].map(label => `<th scope="col">${label}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr><td class="nowrap">${esc(String(row.trade_time).slice(0, 19).replace('T', ' '))}</td><td>${esc(row.payer_name || '')}<br><span class="mono">${esc(row.payer_account)}</span></td><td>${esc(row.payee_name || '')}<br><span class="mono">${esc(row.payee_account)}</span></td><td class="amt">${money(row.amount, row.currency)}</td><td class="amt">${row.balance != null ? money(row.balance, row.currency) : '—'}</td><td>${esc(row.summary || '')}</td></tr>`).join('')}</tbody></table>`;
}
async function toggleAnalysisDetail(button) {
  if (button.disabled || analysisBusy() || confirmation || !lastRun || !isCurrentAnalysis(lastRun) || analysisPhase !== 'done') return;
  const row = button.closest('tr');
  if (!row || !$('#results').contains(row)) return;
  const next = row.nextElementSibling;
  if (next?.classList.contains('detail-row')) {
    const failed = next.dataset.state === 'error'; disposeResultTables(next); next.remove();
    button.textContent = '查看明细'; button.setAttribute('aria-expanded', 'false');
    if (!failed) return;
  }
  const run = lastRun, revision = resultRevision, context = analysisContext();
  const current = () => isCurrentAnalysis(context) && lastRun === run && resultRevision === revision && row.isConnected && analysisPhase === 'done';
  const ids = [...new Set((row.dataset.ids || '').split(',').filter(Boolean))];
  const detail = document.createElement('tr'); detail.className = 'detail-row';
  detail.innerHTML = '<td colspan="6" class="analysis-detail-cell">正在读取流水…</td>'; row.after(detail);
  button.disabled = true; button.setAttribute('aria-expanded', 'true');
  try {
    const query = new URLSearchParams({ ids: ids.join(','), case_id: String(context.caseId), size: '500' });
    const result = await getJson('/api/flows?' + query);
    if (!current()) return;
    if (!Array.isArray(result.rows)) throw new Error('流水明细格式不正确');
    detail.innerHTML = `<td colspan="6" class="analysis-detail-cell"><div class="analysis-detail-heading"><span>本次分析相关流水</span><span>已显示 ${count(result.rows.length)} / ${count(result.total ?? ids.length)} 笔 · 最多读取 500 笔</span></div>${result.rows.length ? tableShell(detailTable(result.rows), '相关流水明细') : '<span class="muted">流水已不存在</span>'}</td>`;
    button.textContent = '收起明细'; bindResultTables(detail);
  } catch (error) {
    if (!current()) return;
    detail.dataset.state = 'error'; detail.innerHTML = `<td colspan="6" class="analysis-detail-cell analysis-detail-error" role="alert">明细读取未完成：${esc(error.message)}</td>`; button.textContent = '重试明细';
  } finally { if (current()) button.disabled = false; }
}
async function generateAnalysisReport() {
  const run = lastRun;
  if (!lastResults || !run || !isCurrentAnalysis(run) || analysisPhase !== 'done' || analysisBusy() || confirmation) return;
  const caseName = analysisCases.find(item => String(item.id) === String(run.caseId))?.name || `案件 ${run.caseId}`;
  const algorithms = run.params.algorithms.map(name => analysisMethods[name].label).join('、');
  const accepted = await confirmAnalysisAction({ title: '生成分析报告',
    message: `${caseName} · ${algorithms}\n${hasAnalysisDraft() ? '当前修改尚未运行，将使用已完成结果的参数。\n' : ''}基于当前案件全部导入流水重新计算并生成 HTML，数据变化可能使报告与屏幕结果不同。`, accept: '重新计算并生成' });
  if (!accepted || !isCurrentAnalysis(run) || lastRun !== run || analysisBusy()) return;
  const current = () => isCurrentAnalysis(run) && lastRun === run && reportPending;
  reportPending = true; $('#reportStatus').textContent = '正在重新计算并生成报告…'; syncAnalysisControls();
  try {
    const response = await fetch('/api/analysis/report', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ case_id: run.caseId, params: run.params }) });
    if (!current()) return;
    if (!response.ok) throw new Error(await responseError(response));
    const blob = await response.blob(); if (!current()) return;
    const link = document.createElement('a'), url = URL.createObjectURL(blob);
    link.href = url; link.download = `资金分析报告_案件${run.caseId}.html`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    $('#reportStatus').textContent = '报告已生成 · 已按本次参数重新计算';
  } catch (error) { if (current()) $('#reportStatus').textContent = '报告生成未完成：' + error.message; }
  finally { if (current()) { reportPending = false; syncAnalysisControls(); } }
}
$('#btnRun').onclick = run; $('#btnReport').onclick = generateAnalysisReport;
$('#caseRetry').onclick = loadCases; $('#statsRetry').onclick = showStats;
$('#caseSel').onchange = async event => {
  const next = event.target.value;
  if (String(caseId) === String(next)) return;
  $('#caseSel').value = caseId == null ? '' : String(caseId);
  if (confirmation) return;
  if (hasAnalysisDraft() && !await confirmAnalysisAction({ title: '切换分析案件？', message: '尚有未运行的参数修改。切换案件将清理当前分析结果、对象和路径账号。', accept: '切换案件', cancel: '继续编辑' })) return;
  selectAnalysisCase(next); void showStats();
};
$('#backToGraph').onclick = async event => {
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button > 0) return;
  event.preventDefault();
  const context = analysisContext();
  if (hasAnalysisDraft() && !await confirmAnalysisAction({ title: '离开分析页面？', message: '尚有未运行的参数修改。离开后这些修改将不会保留。', accept: '返回图谱', cancel: '继续编辑' })) return;
  if (!isCurrentAnalysis(context)) return;
  try {
    const opener = window.opener;
    if (opener && !opener.closed && opener.location.origin === window.location.origin && opener.location.pathname === '/' && new URLSearchParams(opener.location.search).get('case_id') === String(caseId)) { opener.focus(); window.close(); return; }
  } catch (_) { /* an unrelated opener cannot receive this page's state */ }
  window.location.href = $('#backToGraph').href;
};
$('#resultTabs').onclick = event => {
  const tab = event.target.closest('[data-algorithm]');
  if (!tab || tab.disabled || analysisBusy() || confirmation || !lastRun || !isCurrentAnalysis(lastRun)) return;
  renderResultCategory(tab.dataset.algorithm); $('#resultTab-' + activeResult).focus();
};
$('#resultTabs').onkeydown = event => {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || analysisBusy() || confirmation || !lastRun || !isCurrentAnalysis(lastRun)) return;
  const tabs = $$('.analysis-result-tab').filter(tab => !tab.disabled), index = tabs.indexOf(event.target);
  if (index < 0) return;
  const next = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs.at(-1) : tabs[(index + (event.key === 'ArrowLeft' ? -1 : 1) + tabs.length) % tabs.length];
  event.preventDefault(); renderResultCategory(next.dataset.algorithm); $('#resultTab-' + activeResult).focus();
};
document.addEventListener('click', event => { const button = event.target.closest('.btn-detail'); if (button) void toggleAnalysisDetail(button); });
function analysisDraftChanged(event) {
  if (analysisBusy() || confirmation || !$('#analysisParams').contains(event.target)) return;
  event.target.removeAttribute('aria-invalid'); syncAlgorithmFields();
}
document.addEventListener('input', analysisDraftChanged); document.addEventListener('change', analysisDraftChanged);
const requestedAlgorithm = new URLSearchParams(window.location.search).get('algorithm');
if (analysisMethods[requestedAlgorithm]) $$('.alg').forEach(input => { input.checked = input.value === requestedAlgorithm; });
syncAlgorithmFields(); void loadCases();
