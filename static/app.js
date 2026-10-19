/* 资金追踪分析系统 — 前端逻辑 */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const preferenceStorage = window.FundsDesktopPreferences || localStorage;
let cancelGraphImageExport = null;
let exportWorkspace = null;
let caseWorkspace = null;
let dataToolsWorkspace = null;
let mainAccountsWorkspace = null;
let neighborhoodWorkspace = null;
let nodeFinderWorkspace = null;
let activeGraphFocus = null;
let graphLocateFeedback = null;

const state = {
  caseId: null,
  caseGeneration: 0,
  cases: [],
  stats: {},
  tracked: new Set(),
  positions: {},
  graph: null,
  graphResizeObserver: null,
  graphResizeHandler: null,
  graphCamera: null,
  graphDisposers: [],
  graphSelection: null,
  minimap: null,
  graphInited: false,
  graphLoading: false,
  lastGraph: { nodes: [], edges: [], tracked_ids: [] },
  layout: 'auto',
  edgeLabels: new Set(['money']),
  edgeLabelPos: {},   // 边标签沿线位置 { 'source=>target': t(0~1) }
  edgeType: preferenceStorage.getItem('fund_edge_type') || 'quadratic',   // line/polyline/quadratic
  edgeSnapshotLabels: {},
  edgeTypes: {},   // 每条边已保存的线型；全局线型操作会清除此覆盖
  edgeBendPos: {},    // 折线折点 { 'source=>target': [{x,y}] }
  mainAccounts: new Set(),
  focusNodes: new Map(),   // 重点展示 { 账号: 放大倍数 1.1~4 }
  showIds: preferenceStorage.getItem('fund_show_ids') !== '0',   // 画布显示身份证号（默认开）
  showPhotos: preferenceStorage.getItem('fund_show_photos') !== '0',   // 画布显示节点照片（默认开）
  forceRebuild: false,   // 照片上传/清除/开关后整图重建（G6 对已有节点改 type 渲染不可靠）
  forceFit: true,   // 案件切换/清空后需要重新适应画布；平时上图不改变用户视图
  canvasId: 0,   // 当前画布 id（0 = 默认画布；每张画布有独立的追踪集合）
  canvasGeneration: 0,
  canvasName: '',
  graphView: newGraphView(),
  presentation: newGraphPresentation(),
  search: { page: 1, total: 0, size: 50 },
  ctx: null,   // 当前上下文查询 {flowId, side, x, data}
  imp: null,   // 导入预览状态
};

/* ---------------- 基础工具 ---------------- */
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
const fmtMoney = v => FundsMoney.format(v, '');
const fmtMoney2 = fmtMoney;
const fmtShort = fmtMoney; // Monetary graph labels retain every cent, including large values.
const fmtCount = v => FundsMoney.count(v);
const moneyLabel = (value, currency) => FundsMoney.currency(value, currency);
const totalLabel = (totals, fallback, currency) => FundsMoney.totals(totals, fallback, currency);
function moneyHtml(value, currency) { return esc(moneyLabel(value, currency)); }
function totalsHtml(totals, fallback, currency) { return esc(totalLabel(totals, fallback, currency)).replace(/\n/g, '<br>'); }
function fmtTime(s) { return s ? String(s).slice(0, 19) : ''; }   // 含年月日时分秒

function toast(msg, type = 'ok') {
  const d = document.createElement('div');
  d.className = 'toast ' + type;
  d.textContent = msg;
  $('#toasts').appendChild(d);
  setTimeout(() => d.classList.add('fade'), 2800);
  setTimeout(() => d.remove(), 3400);
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = await res.text();
    try { msg = JSON.parse(msg).detail || msg; } catch (e) { /* 纯文本 */ }
    toast(String(msg).slice(0, 200), 'err');
    throw new Error(msg);
  }
  const ct = res.headers.get('content-type') || '';
  return ct.includes('json') ? res.json() : res;
}
const jsonOpts = body => ({ headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

function caseContext() {
  return { caseId: state.caseId, canvasId: state.canvasId, generation: state.caseGeneration, canvasGeneration: state.canvasGeneration };
}
function isCurrentCase(context, canvas = true) {
  return context.caseId === state.caseId && context.generation === state.caseGeneration
    && (!canvas || (context.canvasId === state.canvasId && context.canvasGeneration === state.canvasGeneration));
}
function selectCase(caseId) {
  window.FundsMainAccountsActions?.revoke();
  window.FundsDataToolsActions?.revoke();
  window.FundsCanvasActions?.revoke();
  exportWorkspace?.revoke();
  caseWorkspace?.revoke();
  resetNodeWorkspace();
  cancelGraphLayout();
  state.caseId = Number(caseId);
  state.caseGeneration += 1;
  state.canvasId = 0;
  state.canvasName = '';
  state.layout = 'auto';
  state.canvasGeneration += 1;
  resetGraphView();
  state.presentation = newGraphPresentation();
  revokePhotoCrop(true);
  state.positions = {};
  state.edgeLabelPos = {};
  state.edgeBendPos = {}; state.edgeTypes = {}; syncEdgeTypeControl();
  state.edgeSnapshotLabels = {};
  state.ctx = null;
  state.imp = null;
  state.tracked = new Set();
  state.lastGraph = { nodes: [], edges: [], tracked_ids: [] };
  state.graphLoading = true;
  state.mainAccounts = new Set();
  state.focusNodes = new Map();
  state.stats = {};
  state.search.page = 1;
  state.forceFit = true;
  closeDrawer();
  $('#ctxModal').style.display = 'none';
  $('#importModal').style.display = 'none';
  $('#impCommit').disabled = false;
  $('#flowTbody').innerHTML = '';
  resetBatchView();
  $('#canvasSel').innerHTML = '<option value="0">默认画布</option>';
  $('#canvasSel').value = '0';
  $('#flowCkAll').checked = false;
  $('#flowCkAll').indeterminate = false;
  syncFlowCkCount();
  $('#caseSelect').value = String(state.caseId);
  if (state.graph) { state.graph.clear(); state.graphInited = false; }
  preferenceStorage.setItem('fund_case', state.caseId);
  const url = new URL(window.location.href);
  url.searchParams.set('case_id', String(state.caseId));
  window.history.replaceState(null, '', url);
  $('#analysisLink').href = `/analysis?case_id=${encodeURIComponent(state.caseId)}`;
  updateHeader();
}

/* ---------------- 初始化 ---------------- */
/* ---------------- 全站水印（FundsTrace） ---------------- */
function initWatermark() {
  if (document.querySelector('#watermark')) return;
  const txt = 'FundsTrace';
  const wm = document.createElement('div');
  wm.id = 'watermark';
  // 用 SVG 平铺：旋转 -28°，透明度低不影响阅读
  const cell = document.createElement('canvas');
  cell.width = 340; cell.height = 220;
  const g = cell.getContext('2d');
  g.translate(170, 110);
  g.rotate(-28 * Math.PI / 180);
  g.font = '15px "Microsoft YaHei", sans-serif';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.fillStyle = 'rgba(255, 255, 255, 0.055)';
  g.fillText(txt, 0, 0);
  g.font = '10px "Microsoft YaHei", sans-serif';
  g.fillText(new Date().toLocaleDateString('zh-CN'), 0, 18);
  wm.style.cssText = `position:fixed;inset:0;z-index:9999;pointer-events:none;` +
    `background-image:url(${cell.toDataURL()});background-repeat:repeat;`;
  document.body.appendChild(wm);
  // 防误删：被移除或隐藏时自动重建
  const guard = new MutationObserver(() => {
    const el = document.querySelector('#watermark');
    if (!el || el.style.display === 'none' || el.style.visibility === 'hidden') {
      if (el) el.remove();
      document.body.appendChild(wm);
    }
  });
  guard.observe(document.body, { childList: true, subtree: false });
  const guardStyle = new MutationObserver(() => {
    if (wm.style.display === 'none' || wm.style.visibility === 'hidden' || wm.style.opacity === '0') {
      wm.style.cssText = `position:fixed;inset:0;z-index:9999;pointer-events:none;` +
        `background-image:url(${cell.toDataURL()});background-repeat:repeat;`;
    }
  });
  guardStyle.observe(wm, { attributes: true });
}

window.addEventListener('DOMContentLoaded', async () => {
  initWatermark();
  bindUI();
  installNeighborhood();
  installNodeFinder();
  initGraph();
  await loadCases();
  void window.FundsImportWorkflow?.restore();
  await refreshStats();
  await loadGraph();
  await applyPendingFocusEdge();
  await refreshCanvasList(null).catch(() => {});
  doSearch(1);
});

async function refreshAll() {
  const context = caseContext();
  await refreshStats();
  if (!isCurrentCase(context, false)) return;
  await loadGraph();
  if (!isCurrentCase(context, false)) return;
  await doSearch(state.search.page || 1);
  if (!isCurrentCase(context, false)) return;
  await loadBatches();
}

/* ---------------- 线标注（多选） ---------------- */
function initEdgeLabels() {
  let arr = null;
  try {
    const raw = preferenceStorage.getItem('fund_edge_labels');
    if (raw) arr = JSON.parse(raw);
    else {
      const legacy = preferenceStorage.getItem('fund_edge_label');  // 旧版单选值迁移
      if (legacy) arr = legacy === 'all' ? ['money', 'time', 'summary'] : [legacy];
    }
  } catch (e) { arr = null; }
  if (!Array.isArray(arr) || !arr.length) arr = ['money'];
  state.edgeLabels = new Set(arr.filter(x => ['money', 'time', 'summary', 'currency'].includes(x)));
  if (!state.edgeLabels.size) state.edgeLabels.add('money');
}

function saveEdgeLabels() {
  preferenceStorage.setItem('fund_edge_labels', JSON.stringify([...state.edgeLabels]));
  preferenceStorage.removeItem('fund_edge_label');  // 清掉旧版键
}

/* ---------------- 案件 ---------------- */
/* 一次性画布目标：分析页等入口用 ?canvas_id=... 打开指定画布，避免落回默认画布。 */
let pendingCanvasId = 0;
/* 一次性聚焦目标：?focus_edge=<流水 id> 在出图后立刻选中该笔资金流向。 */
let pendingFocusEdge = 0;
/* URL 请求的目标画布；聚焦必须等它真的激活，否则会选中默认画布里的同名流水。 */
let requestedCanvasId = 0;
async function loadCases() {
  state.cases = await api('/api/cases');
  const sel = $('#caseSelect');
  sel.innerHTML = state.cases.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
  const params = new URLSearchParams(window.location.search);
  const requested = params.get('case_id');
  pendingCanvasId = Number(params.get('canvas_id') || 0) || 0;
  requestedCanvasId = pendingCanvasId;
  pendingFocusEdge = Number(params.get('focus_edge') || 0) || 0;
  const saved = requested || preferenceStorage.getItem('fund_case');
  selectCase((saved && state.cases.some(c => c.id == saved)) ? Number(saved) : state.cases[0].id);
}

/* 出图后把第一笔资金流向选中并拉进视野：不改追踪事实，只动选择与相机。 */
async function applyPendingFocusEdge() {
  const flowId = pendingFocusEdge;
  if (!flowId || !state.graph || !state.caseId) return false;
  // 还停在默认画布（目标画布尚未激活）时先不消费聚焦请求。
  if (requestedCanvasId && state.canvasId !== requestedCanvasId) return false;
  // 用接口返回的图谱数据定位这条流水所属的关系边，再取它的 G6 图元；
  // 边模型不一定带 ids，直接读模型会漏。
  const edge = (state.lastGraph.edges || []).find(item => (item.ids || []).includes(flowId));
  if (!edge) return false;
  const item = state.graphView.edgeItems.get(edgeKey(edge)) || state.graph.findById(edgeKey(edge));
  if (!item) return false;
  pendingFocusEdge = 0;
  closeDrawer(false); selectItem(item); refreshSelectedEdgeAppearance();
  await focusGraphItems([item], { allowHistoryBusy: true });
  return true;
}
// 聚焦在「画布切换完成」之后调用：applyCanvasSnapshot 会重置视图，先选中会被清掉。

async function refreshStats() {
  const context = caseContext();
  const stats = await api(`/api/stats?case_id=${context.caseId}`);
  if (!isCurrentCase(context, false)) return;
  state.stats = stats;
  updateHeader();
}
function updateHeader() {
  const s = state.stats || {};
  $('#statLine').innerHTML =
    `流水 <b>${fmtCount(s.flows || 0)}</b> 笔 · 账户 <b>${fmtCount(s.accounts || 0)}</b> 个 · 已上图 <b>${state.tracked.size}</b> 笔`;
  const empty = (s.flows || 0) === 0;
  $('#emptyHint').style.display = empty ? 'flex' : 'none';
  $('#canvasWrap').classList.toggle('case-empty', empty);
  const minimapButton = $('#btnMinimap');
  if (minimapButton) {
    minimapButton.disabled = empty;
    minimapButton.setAttribute('aria-pressed', String(!empty && !$('#graphMinimapPanel').hidden));
  }
  exportWorkspace?.refresh();
  caseWorkspace?.refresh();
}

/* ---------------- 流水查询（当前案件） ---------------- */
function searchParams(page) {
  const p = {
    case_id: state.caseId,
    account: $('#fAccount').value.trim(), name: $('#fName').value.trim(),
    counter: $('#fCounter').value.trim(), kw: $('#fKw').value.trim(),
    min_amount: $('#fMin').value, max_amount: $('#fMax').value,
    tfrom: $('#fFrom').value, tto: $('#fTo').value,
    page, size: state.search.size,
  };
  if ($('#fTracked') && $('#fTracked').checked) {
    p.tracked = 1;
    p.canvas_id = state.canvasId || 0;
  }
  Object.keys(p).forEach(k => { if (p[k] === '' || p[k] == null) delete p[k]; });
  return new URLSearchParams(p);
}

async function doSearch(page = 1) {
  const context = caseContext();
  const r = await api('/api/flows?' + searchParams(page));
  if (!isCurrentCase(context)) return;
  state.search.page = r.page;
  state.search.total = r.total;
  renderFlows(r.rows);
  const pages = Math.max(1, Math.ceil(r.total / r.size));
  $('#resultInfo').textContent = `共 ${fmtCount(r.total)} 笔`;
  $('#pageInfo').textContent = `第 ${page} / ${pages} 页`;
}

function partyCell(name, acct) {
  return `<span class="party-name" title="${esc(name || '')}">${esc(name || '')}</span><span class="mono" title="${esc(acct)}">${esc(acct)}</span>`;
}

let syncFlowTableScroll = () => {};
let unbindFlowTableScroll = () => {};

function bindFlowTableView() {
  const viewport = $('#flowTableScroll'), density = $('#flowTableDensity'), wrap = $('#flowTableWrap');
  if (!viewport || !density || !wrap) return;
  const apply = () => {
    viewport.dataset.flowDensity = density.value === 'comfortable' ? 'comfortable' : 'compact';
    viewport.dataset.flowWrap = wrap.value === 'single' ? 'single' : 'wrap';
    syncFlowTableScroll();
  };
  density.onchange = apply;
  wrap.onchange = apply;
  apply();
}

function bindTableHorizontalScroll({ viewport, range, left, right, table }) {
  return window.FundsTableScroll.bind({ viewport, range, left, right, table });
}

function bindFlowTableScroll() {
  unbindFlowTableScroll();
  const control = bindTableHorizontalScroll({ viewport: $('#flowTableScroll'), range: $('#flowHorizontalPosition'),
    left: $('#flowScrollLeft'), right: $('#flowScrollRight'), table: $('#flowTable') });
  syncFlowTableScroll = control?.sync || (() => {});
  unbindFlowTableScroll = control?.destroy || (() => {});
}

function flowTimeCell(value) {
  const text = fmtTime(value), split = /^(\d{4}-\d{2}-\d{2})[ T](.*)$/.exec(text);
  return split ? `<span class="flow-date">${esc(split[1])}</span><span class="flow-clock">${esc(split[2])}</span>` : esc(text);
}

function renderFlows(rows) {
  $('#flowTbody').innerHTML = rows.length ? rows.map(r => `
    <tr data-id="${r.id}" class="clickable" tabindex="0" aria-label="流水 ${esc(fmtTime(r.trade_time))}，Enter 查看详情，空格勾选">
      <td><input type="checkbox" class="flow-ck" data-id="${r.id}"></td>
      <td title="${esc(fmtTime(r.trade_time))}">${flowTimeCell(r.trade_time)}</td>
      <td>${partyCell(r.payer_name, r.payer_account)}</td>
      <td>${partyCell(r.payee_name, r.payee_account)}</td>
      <td class="amt">${moneyHtml(r.amount, r.currency)}</td>
      <td class="sum" title="${esc(r.summary)}"><span class="flow-summary">${esc(r.summary || '')}</span></td>
      <td class="acts"><span class="flow-row-actions">
        ${state.tracked.has(r.id)
          ? '<span class="tag on">已上图</span>'
          : '<button class="btn tiny primary act-track">上图</button>'}
        <button class="btn tiny act-ctx" title="查看前后 X 笔流水" aria-label="查看前后 X 笔流水">前后</button>
      </span></td>
    </tr>`).join('')
    : '<tr><td colspan="7" class="muted" style="text-align:center;padding:20px">无数据</td></tr>';
  $('#flowCkAll').checked = false;
  $('#flowCkAll').indeterminate = false;
  syncFlowCkCount();
  syncFlowTableScroll();
}

/* ---------------- 追踪上图 ---------------- */
function syncFlowCkCount() {
  const n = $$('#flowTbody .flow-ck').filter(c => c.checked).length;
  $('#flowCkCount').textContent = `已勾选 ${n} 笔`;
}

async function trackFlows(ids) {
  if (!ids || !ids.length) return toast('未选择流水', 'err');
  const context=caseContext();
  await api('/api/track', { method: 'POST', ...jsonOpts({ case_id: context.caseId, flow_ids: ids, canvas_id: context.canvasId || 0 }) });
  if(!isCurrentCase(context))return;
  state.justAdded = new Set(ids);
  state.forceFit = false;  // 追加保持相机；首批无坐标图谱由初布局适配。
  toast(`已上图 ${ids.length} 笔流水${state.canvasName ? `（画布「${state.canvasName}」）` : ''}`);
  await loadGraph();
}

async function untrackFlows(ids) {
  if (!ids || !ids.length) return;
  await api('/api/track', { method: 'DELETE', ...jsonOpts({ case_id: state.caseId, flow_ids: ids, canvas_id: state.canvasId || 0 }) });
  toast(`已移除 ${ids.length} 笔`);
  closeDrawer();
  await loadGraph();
}

/* Display scope never changes the complete tracked graph or transaction facts. */
function newGraphPresentation(value) {
  return typeof FundsGraphPresentation !== 'undefined' ? FundsGraphPresentation.create(value) : {
    version: 1, relationFilter: { currency: null, min: '', max: '', active: false },
    amountWidth: { enabled: false, currency: null }, nodeLabels: {}, nodeStyles: {}, edgeLabels: {}, edgeStyles: {},
    hiddenAccounts: [], onlyAccounts: null,
  };
}
function graphViewIsFiltered() {
  const p = state.presentation;
  return !!(p.relationFilter.active || p.hiddenAccounts.length || p.onlyAccounts !== null);
}
function graphLabelStyle(kind, id) {
  return (kind === 'node' ? state.presentation.nodeLabels[id] : state.presentation.edgeLabels[id]) || {};
}
function graphRelationStyle(edge, muted = false) {
  const key = edgeKey(edge), custom = state.presentation.edgeStyles[key] || {};
  const width = state.graphView.amountWidths?.widths.get(key);
  const stroke = custom.stroke || (muted ? '#3f4854' : '#7f8b99');
  return { ...denseEdgeStyle(muted), lineDash: [], ...custom, stroke,
    lineWidth: width ?? custom.lineWidth ?? (muted ? 0.75 : 1.4), lineCap:'round', lineJoin:'round', lineAppendWidth:8,
    endArrow: muted ? false : { ...graphArrow(), fill: stroke } };
}
function graphAmountWidthMetadata() {
  const encoding=state.graphView.amountWidths;if(!encoding)return null;
  const legend=encoding.legend;
  return {currency:legend.currency,min_amount:legend.minAmount,max_amount:legend.maxAmount,
    missing_edges:encoding.missing.size,invalid_edges:encoding.invalid.size,scale:'full-graph',transform:'sqrt',min_width:1.5,max_width:7};
}
function graphImageExportLines() {
  const view=state.graphView,filter=state.presentation.relationFilter,encoding=graphAmountWidthMetadata();
  const lines=[`FundsTrace · ${graphViewIsFiltered()?'当前筛选视图':view.visibleIds?'当前邻域视图':'全部图谱'} ${view.index.nodeIds.size}/${state.lastGraph.nodes.length} 个账户 · ${view.index.edgeKeys.size}/${state.lastGraph.edges.length} 条关系 · 导出全部标签（屏幕：${{auto:'自动分级',focus:'关注邻域',all:'全部标签'}[view.labelMode]}）`];
  if(filter.active)lines.push(`关系筛选：${filter.currency||'未标注币种'} · ${filter.min||'不限下界'} — ${filter.max||'不限上界'}；仅筛选显示，追踪数据未删除`);
  if(encoding)lines.push(`金额→线宽：${encoding.currency||'未标注币种'} ${encoding.min_amount??'—'} — ${encoding.max_amount??'—'} · sqrt · 全追踪图统一比例 1.5–7 · 无该币种 ${encoding.missing_edges} 条 · 无效 ${encoding.invalid_edges} 条`);
  lines.push('角色：源头 / 中转 / 沉淀 · 金环：主账号 · 紫环：同户名 · 选中与悬停装饰不导出');
  return lines;
}
function newGraphView(labelMode) {
  const requested = labelMode || new URLSearchParams(window.location.search).get('label_mode');
  return { labelMode: ['auto', 'focus', 'all'].includes(requested) ? requested : 'auto',
    fullIndex: null, index: null, visibleIds: null, root: null, direction: 'both', remaining: 0,
    selectedAccount: null, hoveredAccount: null, selectedEdge: null, previewAccounts: new Set(), policy: null,
    revision: 0, labelTimer: null, zoomSettling: false, fitZoom: 1, viewportFrame: null,
    clipIndex: null, clipped: new Set(), nodeItems: new Map(), edgeItems: new Map(), bodyWorld: null,
    labeledNodes: new Set(), labeledEdges: new Set(), mutedEdges: new Set(), edgeDensityMode: false,
    drawnNodes: 0, drawnEdges: 0, exportDepth: 0, amountWidths: null, recentNodeIds: new Set(), autoRoutes: {} };
}
function resetGraphView() {
  clearGraphLocateFeedback();
  state.graphCamera?.cancelComposition?.();
  cancelGraphImageExport?.();
  releaseIdleGraphLayoutWorker();
  state.graphSelection?.destroy(); state.graphSelection = null;
  revokeZoomOptimization();
  clearTimeout(state.graphView.labelTimer);
  if (state.graphView.viewportFrame != null) cancelAnimationFrame(state.graphView.viewportFrame);
  state.minimap?.clear();
  state.graphView = newGraphView(state.graphView.labelMode);
  if ($('#neighborAccount')) $('#neighborAccount').value = '';
  graphMetric('reset');
}
function activateCanvas(id) {
  window.FundsCanvasActions?.revoke();
  caseWorkspace?.revoke();
  exportWorkspace?.revoke();
  resetNodeWorkspace();
  cancelGraphLayout();
  revokeZoomOptimization();
  // Selection cleanup redraws edge labels. Release it while the old graph's
  // paths and view are still live, before clear retires their geometry.
  closeDrawer();
  // Revoke the prior graph before any request can yield. Its data must never
  // acquire the new canvas ID while the replacement graph is still loading.
  state.graphLoading = true;
  state.lastGraph = { nodes: [], edges: [], tracked_ids: [] };
  state.tracked = new Set(); state.mainAccounts = new Set(); state.focusNodes = new Map();
  state.positions = {}; state.edgeLabelPos = {}; state.edgeBendPos = {}; state.edgeTypes = {}; state.edgeSnapshotLabels = {};
  state.presentation = newGraphPresentation();
  state.layout = 'auto';
  syncEdgeTypeControl();
  state.justAdded = null; state.forceFit = true;
  if (state.graph) state.graph.clear();
  state.graphInited = false;
  state.canvasId = id; state.canvasGeneration += 1; resetGraphView(); revokePhotoCrop(true);
  updateHeader(); updateGraphViewStatus();
}
function graphMetric(phase, extra = {}) {
  if (typeof CustomEvent !== 'function' || typeof window.dispatchEvent !== 'function') return;
  const view = state.graphView;
  window.dispatchEvent(new CustomEvent('funds:graph-metric', { detail: {
    phase, ...caseContext(), at: performance.now(), labelMode: view.labelMode,
    nodes: state.lastGraph.nodes.length, edges: state.lastGraph.edges.length,
    renderedNodes: view.index?.nodeIds.size || 0, renderedEdges: view.index?.edgeKeys.size || 0,
    labels: (view.policy?.nodeIds.size || 0) + (view.policy?.edgeKeys.size || 0),
    drawnNodes: view.drawnNodes, drawnEdges: view.drawnEdges,
    createdLabels: view.labeledNodes.size + view.labeledEdges.size, ...extra,
  } }));
}
function updateGraphViewStatus() {
  const view = state.graphView, full = state.lastGraph;
  if (!neighborhoodWorkspace) {
    if ($('#btnExpandNeighbors')) $('#btnExpandNeighbors').disabled = !view.visibleIds || !view.remaining;
    if ($('#btnNeighborhood')) $('#btnNeighborhood').disabled = !full.nodes.length;
  }
  if ($('#btnGraphOverview')) $('#btnGraphOverview').disabled = !view.visibleIds && !graphViewIsFiltered();
  if ($('#btnCanvasSave')) $('#btnCanvasSave').disabled = state.graphLoading;
  if ($('#btnCanvasClone')) $('#btnCanvasClone').disabled = state.graphLoading;
  if ($('#graphLabelMode')) $('#graphLabelMode').value = view.labelMode;
  window.FundsRibbonCommands?.sync();
  exportWorkspace?.refresh();
  neighborhoodWorkspace?.update();
  nodeFinderWorkspace?.update();
}
function setLabelShapes(item, visible) {
  const children = item.getContainer?.()?.get?.('children') || [];
  let changed=false;
  for (const shape of children) {
    const name = shape.get('name'), kind = shape.get('className');
    if (name === 'text-shape' || name === 'text-bg-shape' || kind === 'node-label' || kind === 'edge-label') {
      if(shape.get('visible')!==visible){if (visible) shape.show?.(); else shape.hide?.();changed=true;}
      if(shape.get('ori-visibility')!==visible)shape.set?.('ori-visibility', visible);
    }
  }
  return changed;
}
function revokeZoomOptimization(graph = state.graph) {
  // Version-pinned G6 4.8 adapter: its 100ms closure captures item arrays.
  // Revoke before clear/changeData/destroy, while all groups are still valid.
  const controller=graph?.get?.('modeController');
  const behaviors=controller?.currentBehaves || [];
  for(const behavior of behaviors) {
    if((behavior.type || behavior.get?.('type'))!=='zoom-canvas') continue;
    clearTimeout(behavior.get?.('timeout'));
    behavior.set?.('timeout',undefined); behavior.set?.('optimized',false);
  }
  if(!graph) return;
  for(const item of [...(graph.getNodes?.()||[]),...(graph.getEdges?.()||[])]) {
    for(const shape of item.getContainer?.()?.get?.('children')||[]) {
      const visibility=shape.get('ori-visibility');
      if(visibility===undefined) continue;
      if(visibility) shape.show?.(); else shape.hide?.();
      shape.set?.('ori-visibility',undefined);
    }
  }
}
const graphArrow = () => ({path:'M 0,0 L 10,5 L 10,-5 Z',fill:'#7f8b99'});
function denseEdgeStyle(muted) {
  // An opaque low-contrast stroke avoids alpha accumulation at dense crossings.
  return {stroke:muted?'#3f4854':'#7f8b99',lineWidth:muted?0.75:1.4,strokeOpacity:1,endArrow:muted?false:graphArrow()};
}
function applyGraphEdgeDetail() {
  const view=state.graphView, graph=state.graph;
  if (!graph || !view.index) return;
  const dense=view.index.edgeKeys.size>1000 && view.labelMode!=='all' && !view.exportDepth;
  const focused=new Set();
  if(view.selectedEdge) focused.add(view.selectedEdge);
  const roots=[...new Set([view.selectedAccount,view.hoveredAccount])].filter(id=>view.index.incidentEdges.has(id));
  const adjacent=roots.map(id=>view.index.incidentEdges.get(id).values());
  // Share the budget between selection and hover, so a hub cannot starve the
  // hovered leaf's direction indicator. The selected edge is always reserved.
  while(focused.size<80 && adjacent.length) {
    for(let i=0;i<adjacent.length && focused.size<80;) {
      const next=adjacent[i].next();
      if(next.done) adjacent.splice(i,1); else {focused.add(next.value);i++;}
    }
  }
  const changedMode=dense!==view.edgeDensityMode;
  const keys=changedMode?view.index.edgeKeys:new Set([...(view.focusedEdges||[]),...focused]);
  const changed=[];
  for(const key of keys) {
    const muted=dense&&!focused.has(key), item=view.edgeItems.get(key);
    if(item && muted!==view.mutedEdges.has(key)) {
      graph.updateItem(item,{style:graphRelationStyle(item.getModel(),muted)});
      // G6 4.8 keeps cached arrow shapes when its public arrow attr turns false.
      // Clear only disabled caches; the pinned renderer remains unchanged.
      window.FundsGraphLabelGeometry?.syncArrows(item.getKeyShape?.());
      changed.push(item);
      if(muted)view.mutedEdges.add(key);else view.mutedEdges.delete(key);
    }
  }
  view.edgeDensityMode=dense;view.focusedEdges=focused;
  return changed;
}
let graphLabelMeasure = null;
function graphTextSize(text, style) {
  if(!graphLabelMeasure)graphLabelMeasure=document.createElement('canvas').getContext?.('2d');
  if(typeof FundsGraphLabelGeometry!=='undefined'&&typeof FundsGraphLabelGeometry.measureText==='function')return FundsGraphLabelGeometry.measureText(graphLabelMeasure,text,style);
  const lines=String(text??'').split('\n');
  if(graphLabelMeasure)graphLabelMeasure.font=`${style.fontStyle} ${style.fontWeight} ${style.fontSize}px ${style.fontFamily}`;
  let width=0;
  for(const line of lines){const metrics=graphLabelMeasure?.measureText?.(line);
    width=Math.max(width,metrics?Math.max(metrics.width,(metrics.actualBoundingBoxLeft||0)+(metrics.actualBoundingBoxRight||0)):Array.from(line).length*style.fontSize);}
  return {width:width+12,height:style.fontSize+Math.max(0,lines.length-1)*style.lineHeight+6};
}
function graphLayoutNode(model, fixed = false) {
  const size=Array.isArray(model.size)?Math.max(...model.size):model.size||32;
  const label=graphTextSize(model.fundsLabel??model.label,effectiveGraphLabelStyle('node',model.id));
  return {id:model.id,x:model.x,y:model.y,fixed,isMain:model.data?.is_main===1,size,
    width:Math.max(size,label.width),height:size+6+label.height,offsetX:0,offsetY:(6+label.height)/2};
}
function effectiveGraphLabelStyle(kind, id) {
  const custom=graphLabelStyle(kind,id);
  return FundsGraphPresentation.effectiveLabel(kind,custom);
}
function labelShapes(item) {
  return (item.getContainer?.()?.get?.('children')||[]).filter(shape =>
    ['text-shape','text-bg-shape'].includes(shape.get('name')));
}
function resetAutoLabelOffset(item) {
  (item.getContainer?.()?.get?.('children')||[]).find(shape=>shape.get('name')==='funds-label-leader')?.remove?.();
  // The label adapter owns only transient geometry, never transaction facts.
  if(item._fundsHasLabelLeader){delete item.getModel().fundsLabelLeader;item._fundsHasLabelLeader=false;}
  for(const shape of labelShapes(item)) {
    const offset=shape._fundsLabelOffset;
    if(!offset)continue;
    // G6 can recreate or update a shape between passes. Restore only the
    // coordinates written by this adapter, never subtract from a fresh shape.
    if(shape.attr('x')===offset.x && shape.attr('y')===offset.y) shape.attr({x:offset.baseX,y:offset.baseY});
    delete shape._fundsLabelOffset;
  }
}
function graphLabelBounds(item, kind, measured = true) {
  const children=labelShapes(item), graph=state.graph, m=item.getModel();
  if(measured) {
    const bounds=children.find(s=>s.get('name')==='text-bg-shape')?.getCanvasBBox?.()
      ||children.find(s=>s.get('name')==='text-shape')?.getCanvasBBox?.();
    if(bounds && [bounds.minX,bounds.minY,bounds.maxX,bounds.maxY].every(Number.isFinite)) return bounds;
  }
  const zoom=Math.abs(graph.getZoom?.()||1), style=effectiveGraphLabelStyle(kind,kind==='node'?m.id:edgeKey(m));
  const measuredText=graphTextSize(m.fundsLabel??m.label,style);
  const width=measuredText.width*zoom,height=measuredText.height*zoom;
  let anchor;
  if(kind==='node') {
    const size=Array.isArray(m.size)?Math.max(...m.size):m.size||32;
    anchor={x:m.x,y:m.y+size/2+6/zoom+height/zoom/2};
  } else {
    const saved=state.edgeSnapshotLabels[edgeKey(m)], ratio=state.edgeLabelPos[edgeKey(m)];
    const path=ratio!=null?edgePts(item):null;
    anchor=path?ptAtRatio(path,ratio):saved?.manual!==false&&saved?.lx!=null?{x:saved.lx,y:saved.ly}:item.getKeyShape?.()?.getPoint?.(0.5);
    if(!anchor) {
      const a=state.graphView.nodeItems.get(m.source)?.getModel(), b=state.graphView.nodeItems.get(m.target)?.getModel();
      if(a&&b)anchor={x:(a.x+b.x)/2,y:(a.y+b.y)/2};
    }
  }
  if(!anchor || !Number.isFinite(anchor.x)||!Number.isFinite(anchor.y)) return null;
  const point=graph.getCanvasByPoint?.(anchor.x,anchor.y)||anchor;
  return {minX:point.x-width/2,minY:point.y-height/2,maxX:point.x+width/2,maxY:point.y+height/2};
}
function graphNodeBodyBounds(item) {
  const shape=item?.getKeyShape?.()?.getCanvasBBox?.();
  if(shape&&[shape.minX,shape.minY,shape.maxX,shape.maxY].every(Number.isFinite))return shape;
  if(!item)return null;
  const m=item.getModel(), size=Array.isArray(m.size)?Math.max(...m.size):m.size||32;
  const point=state.graph.getCanvasByPoint?.(m.x,m.y)||{x:m.x,y:m.y}, radius=size/2*Math.abs(state.graph.getZoom?.()||1);
  return [point.x,point.y,radius].every(Number.isFinite)?{minX:point.x-radius,minY:point.y-radius,maxX:point.x+radius,maxY:point.y+radius}:null;
}
function graphBodyWorld(item, unproject) {
  const shape=item.getKeyShape?.()?.getCanvasBBox?.();
  if(shape&&unproject&&[shape.minX,shape.minY,shape.maxX,shape.maxY].every(Number.isFinite)) {
    const points=[unproject(shape.minX,shape.minY),unproject(shape.minX,shape.maxY),unproject(shape.maxX,shape.minY),unproject(shape.maxX,shape.maxY)];
    return {minX:Math.min(...points.map(p=>p.x)),minY:Math.min(...points.map(p=>p.y)),maxX:Math.max(...points.map(p=>p.x)),maxY:Math.max(...points.map(p=>p.y))};
  }
  const m=item.getModel(),size=Array.isArray(m.size)?Math.max(...m.size):m.size||32;
  return {minX:m.x-size/2,minY:m.y-size/2,maxX:m.x+size/2,maxY:m.y+size/2};
}
function graphScreenBodies() {
  const view=state.graphView;
  if(!view.bodyWorld) {
    const unproject=graphUnprojector();
    view.bodyWorld=new Map([...view.nodeItems].map(([id,item])=>[id,graphBodyWorld(item,unproject)]));
  }
  const bodies=new Map(),eligible=view.exportDepth?view.nodeItems.keys():(view.clipResult?.nodeIds||view.nodeItems.keys());
  for(const id of eligible) {
    const b=view.bodyWorld.get(id);if(!b)continue;
    const a=state.graph.getCanvasByPoint?.(b.minX,b.minY)||{x:b.minX,y:b.minY};
    const c=state.graph.getCanvasByPoint?.(b.maxX,b.maxY)||{x:b.maxX,y:b.maxY};
    bodies.set(id,{minX:Math.min(a.x,c.x),minY:Math.min(a.y,c.y),maxX:Math.max(a.x,c.x),maxY:Math.max(a.y,c.y)});
  }
  return bodies;
}
function graphChromeObstacles() {
  if(state.graphView.exportDepth)return [];
  const container=$('#graphContainer').getBoundingClientRect?.();if(!container)return [];
  return ['legend','graphMinimapPanel'].flatMap(id=>{
    const element=$('#'+id);if(!element || element.hidden || !element.getClientRects?.().length)return [];
    const box=element.getBoundingClientRect();
    return [{key:'chrome:'+id,bounds:{minX:box.left-container.left,minY:box.top-container.top,maxX:box.right-container.left,maxY:box.bottom-container.top}}];
  });
}
// Camera targets use the space that the user can actually see. Keep these
// floating forms out of layout/label routing: opening a menu never moves nodes.
function graphFocusObstacles() {
  const container = $('#graphContainer')?.getBoundingClientRect?.();
  if (!container) return [];
  const width = container.width ?? container.right - container.left, height = container.height ?? container.bottom - container.top;
  const elements = new Set([...$$('.toolbar-menu,.ribbon-popover,.modal,dialog'), $('#drawer'),
    $('#legend'), $('#graphMinimapPanel')]);
  return [...elements].flatMap(element => {
    if (!element || element.hidden || element.inert || element.style?.display === 'none' || !element.getClientRects?.().length) return [];
    if (element.id === 'drawer' && !element.classList.contains('open')) return [];
    if (element.tagName === 'DIALOG' && !element.open) return [];
    const box = (element.classList?.contains('modal') ? element.querySelector('.modal-box') || element : element).getBoundingClientRect();
    const bounds = { minX: Math.max(0, box.left - container.left), minY: Math.max(0, box.top - container.top),
      maxX: Math.min(width, box.right - container.left), maxY: Math.min(height, box.bottom - container.top) };
    return bounds.maxX > bounds.minX && bounds.maxY > bounds.minY ? [{ bounds }] : [];
  });
}
function captureGraphLocation() {
  // Capture is called only for explicit account searches. Claim intent before
  // the request yields so an old animation/default Fit cannot outrank it.
  state.graphCamera?.cancelFocus?.('search');
  clearGraphLocateFeedback();
  return { context: caseContext(), graph: state.graph, view: state.graphView, revision: state.graphView.revision,
    intent: state.graphCamera?.getIntent?.(), matrix: JSON.stringify(state.graph?.get?.('group')?.getMatrix?.() || null) };
}
function clearGraphLocateFeedback() {
  if (!graphLocateFeedback) return;
  clearTimeout(graphLocateFeedback.timer);
  graphLocateFeedback.layer.remove(); graphLocateFeedback = null;
}
function showGraphLocateFeedback(item) {
  clearGraphLocateFeedback();
  const graph = state.graph, container = $('#graphContainer'), model = item?.getModel?.();
  if (!model || !container?.appendChild || graph?.findById?.(model.id) !== item) return;
  const point = graph.getCanvasByPoint(model.x, model.y), zoom = graph.getZoom?.() || 1;
  const raw = Array.isArray(model.size) ? model.size : [model.size || 32, model.size || 32];
  const layer = document.createElement('div'); layer.className = 'graph-selection-layer graph-locate-layer';
  layer.setAttribute('aria-hidden', 'true');
  const halo = document.createElement('div'); halo.className = 'graph-selection-halo';
  Object.assign(halo.style, { left: `${point.x}px`, top: `${point.y}px`,
    width: `${Math.max(24, (raw[0] || 32) * zoom + 16)}px`, height: `${Math.max(24, (raw[1] || raw[0] || 32) * zoom + 16)}px` });
  const ring = document.createElement('div'); ring.className = 'graph-selection-ring graph-locate-arrival';
  halo.appendChild(ring); layer.appendChild(halo); container.appendChild(layer);
  const feedback = { layer, timer: null }; graphLocateFeedback = feedback;
  feedback.timer = setTimeout(() => { if (graphLocateFeedback === feedback) clearGraphLocateFeedback(); }, 1250);
}
function currentGraphLocation(binding, camera = true) {
  return !!binding && isCurrentCase(binding.context) && binding.graph === state.graph && binding.view === state.graphView
    && binding.revision === state.graphView.revision && (!camera || binding.intent === state.graphCamera?.getIntent?.()
      && binding.matrix === JSON.stringify(state.graph?.get?.('group')?.getMatrix?.() || null));
}
function graphLocationBlocker(allowHistoryBusy = false) {
  if (!state.caseId || !state.graph || state.graphLoading) return '当前画布尚未就绪';
  if (layoutJob && layoutJob.phase !== 'computing' || state.graphView.exportDepth || state.graphSelection?.status?.().dragging
    || !allowHistoryBusy && window.FundsCanvasEditor?.history.status().busy && layoutJob?.phase !== 'computing') return '画布正在处理，请稍后定位';
  return '';
}
function graphAccountAvailability(account) {
  const id = String(account ?? '');
  const tracked = state.lastGraph.nodes.some(node => String(node.account ?? node.id) === id);
  // A node clipped outside the viewport is still a visible member of the view.
  const visible = tracked && !!state.graph?.findById?.(id) && (!state.graphView.index || state.graphView.index.nodeIds.has(id));
  return { visible, tracked, reason: visible ? '' : tracked ? '该账户不在当前显示范围；可调整邻域、关系筛选或账户显示条件' : '该账户尚未上图，请先追踪相关流水' };
}
async function focusGraphItems(items, { binding, maxZoom, preferredZoom, readingEnvelope = false, allowHistoryBusy = false } = {}) {
  const token = binding || captureGraphLocation(), reason = graphLocationBlocker(allowHistoryBusy);
  if (reason) return { ok: false, reason };
  if (!currentGraphLocation(token)) return { ok: false, reason: '定位已取消，画布或视角已变化' };
  const graph = state.graph, targets = [...new Set(items)].filter(item => {
    const m = item?.getModel?.();
    return m && Number.isFinite(m.x) && Number.isFinite(m.y) && graph.findById?.(m.id) === item;
  });
  if (!targets.length || !state.graphCamera?.focus) return { ok: false, reason: '当前画布暂不可定位' };
  // Keep a precise search readable without selecting it or opening its editor.
  // Large result groups retain normal density limits rather than forcing labels.
  const view = state.graphView, previews = new Set(targets.length <= 10 ? targets.map(item => item.getModel().id) : []);
  view.previewAccounts = previews;
  applyGraphLabels(false);
  let bounds = null;
  const inverse = graphUnprojector();
  for (const item of targets) {
    let box;
    if (readingEnvelope) {
      // An overview's temporary label displacement and six-screen-pixel gap
      // must not become a giant world-space envelope during enlargement.
      // Measure the full text at its intended reading scale, without moving
      // live geometry. Normal label placement runs again at the final zoom.
      const model = item.getModel(), body = graphBodyWorld(item, inverse);
      const text = graphTextSize(model.fundsLabel ?? model.label, effectiveGraphLabelStyle('node', model.id));
      const zoom = Math.min(4, Math.max(.02, preferredZoom || graph.getZoom?.() || 1));
      box = { minX: Math.min(body.minX, model.x - text.width / 2), minY: body.minY,
        maxX: Math.max(body.maxX, model.x + text.width / 2), maxY: body.maxY + 6 / zoom + text.height };
    } else box = graphIndexedBounds(item, 'node', inverse) || graphBodyWorld(item, inverse);
    if (!box || !Object.values(box).every(Number.isFinite)) continue;
    if (!bounds) bounds = { ...box };
    else { bounds.minX = Math.min(bounds.minX, box.minX); bounds.minY = Math.min(bounds.minY, box.minY);
      bounds.maxX = Math.max(bounds.maxX, box.maxX); bounds.maxY = Math.max(bounds.maxY, box.maxY); }
  }
  if (!bounds) { if (view.previewAccounts === previews) view.previewAccounts.clear(); return { ok: false, reason: '节点位置尚未准备完成' }; }
  const point = targets.length === 1 ? targets[0].getModel()
    : { x: (bounds.minX + bounds.maxX) / 2, y: (bounds.minY + bounds.maxY) / 2 };
  const focus = { graph }; activeGraphFocus = focus;
  let result;
  try { result = await state.graphCamera.focus({ x: point.x, y: point.y }, {
    bounds, obstacles: graphFocusObstacles(), maxZoom, preferredZoom, duration: 320,
    isCurrent: () => currentGraphLocation(token, false) && !graphLocationBlocker(allowHistoryBusy),
    onFrame: () => { scheduleGraphViewport(); state.graphSelection?.refresh(); },
    onFinish: () => { if (currentGraphLocation(token, false)) { scheduleGraphViewport(); scheduleGraphLabels(); state.graphSelection?.refresh(); } }
  }); } finally { if (activeGraphFocus === focus) activeGraphFocus = null; }
  if (!result.ok && view.previewAccounts === previews) { view.previewAccounts.clear(); scheduleGraphLabels(); }
  if (result.ok && targets.length === 1 && currentGraphLocation(token, false) && view.previewAccounts === previews)
    showGraphLocateFeedback(targets[0]);
  const messages = { 'unavailable-space': '当前浮层遮挡了可用区域，请关闭部分浮层后定位', hidden: '页面已切换，定位已取消',
    superseded: '已改为定位最新搜索结果', resized: '画布尺寸已变化，定位已取消', 'camera-changed': '视角已由手动操作接管' };
  return result.ok ? result : { ok: false, reason: messages[result.reason] || '定位已取消，可再次搜索' };
}
async function locateGraphAccount(account, { binding } = {}) {
  if (binding && !currentGraphLocation(binding)) return { ok: false, reason: '定位已取消，画布或视角已变化' };
  const available = graphAccountAvailability(account);
  if (!available.visible) return { ok: false, reason: available.reason };
  return focusGraphItems([state.graph.findById(String(account))], { binding, preferredZoom: Math.max(.85, state.graph.getZoom?.() || 1) });
}
async function locateGraphFields(fields, { binding } = {}) {
  if (binding && !currentGraphLocation(binding)) return { ok: false, reason: '定位已取消，画布或视角已变化' };
  const tokens = Object.entries(fields || {}).filter(([, value]) => String(value ?? '').trim())
    .map(([kind, value]) => [kind, String(value).trim().toLocaleLowerCase()]);
  if (!tokens.length) return { ok: false, reason: '本次查询没有账户定位条件' };
  const nodes = state.lastGraph.nodes, matches = new Set();
  for (const [kind, query] of tokens) {
    const exact = kind !== 'name' ? nodes.filter(node => String(node.account ?? node.id).toLocaleLowerCase() === query) : [];
    const candidates = exact.length ? exact : nodes.filter(node => kind === 'name' ? String(node.name || '').toLocaleLowerCase().includes(query)
      : kind === 'account' ? String(node.account ?? node.id).toLocaleLowerCase().includes(query)
      : String(node.account ?? node.id).toLocaleLowerCase().includes(query) || String(node.name || '').toLocaleLowerCase().includes(query));
    for (const node of candidates) matches.add(String(node.account ?? node.id));
  }
  const visible = [...matches].filter(id => graphAccountAvailability(id).visible);
  let result;
  if (!visible.length) result = { ok: false, reason: matches.size ? '匹配账户不在当前显示范围；查询结果已保留，可调整图内显示条件后定位' : '当前追踪图中没有匹配账户；查询结果已保留' };
  else if (visible.length > 100) result = { ok: false, reason: `当前画布匹配 ${fmtCount(visible.length)} 个账户，请补全账号或户名后定位` };
  else result = await focusGraphItems(visible.map(id => state.graph.findById(id)), { binding, preferredZoom: Math.max(.85, state.graph.getZoom?.() || 1) });
  if (!result.ok && (!binding || currentGraphLocation(binding, false))) toast(result.reason);
  return result;
}
window.FundsGraphLocator = Object.freeze({ capture: captureGraphLocation, availability: graphAccountAvailability,
  previewAccount: locateGraphAccount, previewFields: locateGraphFields });

function installNodeFinder() {
  if (!window.FundsNodeFinder || nodeFinderWorkspace) return;
  let cached = null, accounts = [], finderPreview = null;
  const read = () => {
    const graph = state.graph, view = state.graphView, binding = JSON.stringify(caseContext());
    const ready = !!state.caseId && !!graph && !state.graphLoading;
    if (!cached || cached.graph !== graph || cached.revision !== view.revision || cached.ready !== ready || cached.binding !== binding) {
      cached = { graph, revision: view.revision, ready, binding };
      accounts = ready ? graph.getNodes().filter(item => !view.index || view.index.nodeIds.has(item.getModel().id))
        .map(item => { const model = item.getModel(); return { account: String(model.id), name: String(model.data?.name || '') }; })
        .sort((a, b) => a.account.localeCompare(b.account)) : [];
    }
    return { binding, revision: view.revision, source: graph, ready, accounts, recent: [...view.recentNodeIds || []] };
  };
  const current = snapshot => {
    const next = read();
    return snapshot.binding === next.binding && snapshot.revision === next.revision && snapshot.source === next.source && next.ready;
  };
  nodeFinderWorkspace = window.FundsNodeFinder.create({ read, formatCount: fmtCount, blocker: graphLocationBlocker,
    locate: (ids, snapshot) => {
      if (!current(snapshot)) return { ok: false, reason: '画布已更新，请重新查找' };
      const graph = state.graph, visible = new Set(read().accounts.map(item => item.account));
      if (!ids.length || ids.some(id => !visible.has(id))) return { ok: false, reason: '匹配账户已不在当前显示范围' };
      const result = focusGraphItems(ids.map(id => graph.findById(id)), { binding: captureGraphLocation(),
        readingEnvelope: ids.length === 1, preferredZoom: Math.max(ids.length === 1 ? 1.25 : .85, graph.getZoom?.() || 1) });
      finderPreview = state.graphView.previewAccounts;
      return result;
    },
    select: (ids, snapshot) => {
      if (!current(snapshot)) return { ok: false, reason: '画布已更新，请重新查找' };
      const reason = neighborhoodBlocker();
      if (reason) return { ok: false, reason: reason.replace('查看邻域', '选中结果').replace('应用', '选择') };
      const visible = new Set(read().accounts.map(item => item.account));
      if (!ids.length || ids.some(id => !visible.has(id))) return { ok: false, reason: '匹配账户已不在当前显示范围' };
      state.graphSelection?.rebind(ids);
      return { ok: true };
    },
    cancel: () => {
      state.graphCamera?.cancelFocus?.('search'); clearGraphLocateFeedback();
      if (state.graphView.previewAccounts?.size) { state.graphView.previewAccounts.clear(); scheduleGraphLabels(); }
    },
    clearPreview: () => {
      if (finderPreview && state.graphView.previewAccounts === finderPreview) {
        clearGraphLocateFeedback(); finderPreview.clear(); finderPreview = null; scheduleGraphLabels();
      }
    }
  });
  window.FundsNodeFinderActions = Object.freeze({ update: () => nodeFinderWorkspace.update(), getState: () => nodeFinderWorkspace.getState(),
    search: query => { nodeFinderWorkspace.open(); return nodeFinderWorkspace.search(query); }, locateNew: () => nodeFinderWorkspace.locateNew() });
  for (const event of ['funds:graph-metric', 'funds:main-accounts-state']) window.addEventListener(event, () => nodeFinderWorkspace.update());
}
function avoidGraphLabelCollisions(policy) {
  const view=state.graphView;
  if(typeof FundsGraphLabels==='undefined')return policy;
  const candidates=[], bodies=graphScreenBodies();
  const selectedIds=state.graphSelection?.ids||new Set();
  for(const id of policy.nodeIds) {
    const item=view.nodeItems.get(id);if(!item)continue;
    const required=selectedIds.has(id)||id===view.selectedAccount||id===view.hoveredAccount||!view.exportDepth&&view.previewAccounts?.has(id);
    const bounds=graphLabelBounds(item,'node');
    candidates.push({key:id,kind:'node',bounds,placements:bounds?[{dx:0,dy:0,bounds}]:[],required,
      priority:required?100:view.index.mainNodeIds.has(id)?70:20});
  }
  // Relationship text stays on its own path. Collisions may obscure it, but
  // never move or suppress a label already admitted by the user's LOD mode.
  const edgeObstacles=[...policy.edgeKeys].flatMap(key=>{
    const item=view.edgeItems.get(key),bounds=item&&graphLabelBounds(item,'edge');
    return bounds?[{key:'edge-label:'+key,bounds}]:[];
  });
  const c=$('#graphContainer');
  const width=view.exportDepth?(state.graph.get?.('width')||c.clientWidth):c.clientWidth;
  const height=view.exportDepth?(state.graph.get?.('height')||c.clientHeight):c.clientHeight;
  const selected=FundsGraphLabels.select(candidates,{obstacles:[...[...bodies].map(([key,bounds])=>({key,bounds})),...graphChromeObstacles(),...edgeObstacles],
    retainAll:view.labelMode==='all'||view.exportDepth>0,
    viewport:width&&height?{minX:4,minY:4,maxX:width-4,maxY:height-(view.exportFooterHeight||0)-4}:null});
  return {...policy,nodeIds:selected.nodeIds,edgeKeys:new Set(policy.edgeKeys),nodePlacements:selected.nodePlacements,
    edgePlacements:new Map(),collisions:selected.rejected,conflicts:selected.conflicts,unknownGeometry:selected.unknownGeometry};
}
function applyGraphLabels(paint = true) {
  const graph = state.graph, view = state.graphView;
  if (!graph || !view.index) return;
  const autoPaint=graph.get?.('autoPaint')!==false;
  if(paint)graph.setAutoPaint(false);
  try {
  // Fit/manual updates already settle the labels now; do not repeat this work
  // in the 140ms zoom timer while the next drag gesture is in progress.
  clearTimeout(view.labelTimer);view.labelTimer=null;view.zoomSettling=false;
  // Older host adapters can reach labels without a prior geometry rebuild.
  if (!view.nodeItems.size) view.nodeItems = new Map(graph.getNodes().map(n => [n.getModel().id, n]));
  if (!view.edgeItems.size) view.edgeItems = new Map(graph.getEdges().map(e => [edgeKey(e.getModel()), e]));
  let index = view.index;
  if (view.clipResult && !view.exportDepth && view.clipResult.nodeIds.size < index.nodeIds.size) {
    const ids = new Set(view.clipResult.nodeIds);
    for (const key of view.clipResult.edgeKeys) {
      const edge=index.edgeByKey.get(key);
      if (edge) {ids.add(edge.source);ids.add(edge.target);}
    }
    index = FundsGraphView.createIndex({ nodes: [...ids].map(id => view.index.nodeById.get(id)).filter(Boolean),
      edges: [...view.clipResult.edgeKeys].map(key => view.index.edgeByKey.get(key)).filter(e => e && ids.has(e.source) && ids.has(e.target)) });
  }
  for(const item of [...view.nodeItems.values(),...view.edgeItems.values()]) {
    resetAutoLabelOffset(item); FundsGraphLabelGeometry.plain(item);
  }
  for(const item of view.nodeItems.values()) FundsGraphLabelGeometry.placeNode(item,{zoom:graph.getZoom?.()||1,gap:6*(view.exportLabelScale||1)});
  syncGraphEdgeLabels();
  const requestedPolicy = FundsGraphView.labels(index, { zoom: (graph.getZoom?.() || 1) / view.fitZoom * 0.5,
    mode: view.exportDepth?'all':view.labelMode, selectedIds: new Set([...(state.graphSelection?.ids || []), ...(!view.exportDepth ? view.previewAccounts || [] : [])]), selectedAccount: view.selectedAccount, hoveredAccount: view.hoveredAccount,
    selectedEdge: view.selectedEdge, eligibleNodeIds: !view.exportDepth ? view.clipResult?.nodeIds : null });
  view.policy=avoidGraphLabelCollisions(requestedPolicy);
  const update = (items, previous, desired, changedOnly=false) => {
    let geometryChanged=false;
    const keys = new Set([...previous, ...desired]);
    for (const key of keys) {
      const item = items.get(key); if (!item) continue;
      const model = item.getModel(), wanted = desired.has(key);
      const membershipChanged=wanted!==previous.has(key);
      if(changedOnly&&!membershipChanged)continue;
      const canonical = model.fundsLabel ?? model.label ?? '';
      if (model.fundsLabel === undefined) model.fundsLabel = canonical;
      let recreated=false,visibilityChanged=false;
      if ((wanted && model.label !== canonical) || (!wanted && model.label !== undefined)) {
        if (!wanted && items === view.edgeItems) cacheEdgeLabel(item);
        // Undefined removes G6's label and background, not merely their paint.
        model.label = wanted ? canonical : undefined;
        graph.updateItem(item, { label: model.label });
        recreated=true;
        if (wanted && items === view.edgeItems) restoreEdgeLabelPositions([item]);
      }
      if (wanted) {
        visibilityChanged=setLabelShapes(item, true);
        if(recreated||membershipChanged||visibilityChanged) {
          FundsGraphLabelGeometry.plain(item);
          if(items===view.nodeItems) FundsGraphLabelGeometry.placeNode(item,{zoom:graph.getZoom?.()||1,gap:6*(view.exportLabelScale||1)});
        }
      }
      if(items===view.edgeItems&&(recreated||membershipChanged||visibilityChanged)) syncGraphEdgeLabel(item);
      if(recreated||membershipChanged||visibilityChanged)geometryChanged=true;
    }
    return geometryChanged;
  };
  const nodesChanged=update(view.nodeItems, view.labeledNodes, view.policy.nodeIds);
  const edgesChanged=update(view.edgeItems, view.labeledEdges, view.policy.edgeKeys);
  // New labels now have real G6 text/background bounds. Resolve any estimate
  // mismatch before painting; never repeatedly create rejected labels here.
  const settled=nodesChanged||edgesChanged?avoidGraphLabelCollisions(requestedPolicy):view.policy;
  update(view.nodeItems, view.policy.nodeIds, settled.nodeIds,true);
  update(view.edgeItems, view.policy.edgeKeys, settled.edgeKeys,true);
  view.policy=settled;
  const unproject=graphUnprojector();
  for(const [kind,items,keys] of [['node',view.nodeItems,view.policy.nodeIds],['edge',view.edgeItems,view.policy.edgeKeys]]) {
    for(const key of keys){const item=items.get(key);if(item)view.clipIndex?.include(kind,key,graphItemBounds(item,unproject));}
  }
  view.labeledNodes = new Set(view.policy.nodeIds); view.labeledEdges = new Set(view.policy.edgeKeys);
  for(const item of applyGraphEdgeDetail()||[])syncGraphEdgeLabel(item);
  refreshSelectedEdgeAppearance();
  updateGraphViewStatus();
  } finally {if(paint){graph.setAutoPaint(autoPaint);if(autoPaint)graph.paint();}}
}
function graphViewportRect(graph = state.graph) {
  if (!graph?.getPointByCanvas) return null;
  const c = $('#graphContainer');
  const a = graph.getPointByCanvas(0, 0), b = graph.getPointByCanvas(c.clientWidth, c.clientHeight);
  return { minX: Math.min(a.x,b.x), minY: Math.min(a.y,b.y), maxX: Math.max(a.x,b.x), maxY: Math.max(a.y,b.y) };
}
function restoreGraphClipping(view = state.graphView) {
  for (const item of view.clipped) { try { item.getContainer()?.show?.(); } catch (_) { /* disposed graph */ } }
  view.clipped.clear();
}
function graphUnprojector() {
  const graph=state.graph;
  if (!graph?.getPointByCanvas) return null;
  const o=graph.getPointByCanvas(0,0), x=graph.getPointByCanvas(1,0), y=graph.getPointByCanvas(0,1);
  if (![o.x,o.y,x.x,x.y,y.x,y.y].every(Number.isFinite)) return null;
  return (a,b)=>({x:o.x+a*(x.x-o.x)+b*(y.x-o.x),y:o.y+a*(x.y-o.y)+b*(y.y-o.y)});
}
function graphItemBounds(item, unproject = graphUnprojector()) {
  const b=item.getContainer?.()?.getCanvasBBox?.(), graph=state.graph;
  if (!b || !unproject || ![b.minX,b.minY,b.maxX,b.maxY].every(Number.isFinite)) return null;
  const points=[unproject(b.minX,b.minY),unproject(b.minX,b.maxY),unproject(b.maxX,b.minY),unproject(b.maxX,b.maxY)];
  return {minX:Math.min(...points.map(p=>p.x)),minY:Math.min(...points.map(p=>p.y)),maxX:Math.max(...points.map(p=>p.x)),maxY:Math.max(...points.map(p=>p.y))};
}
function graphIndexedBounds(item, kind, unproject) {
  const actual=graphItemBounds(item,unproject), label=graphLabelBounds(item,kind,false);
  if(!label || !unproject)return actual;
  const a=unproject(label.minX,label.minY),b=unproject(label.maxX,label.maxY);
  const estimated={minX:Math.min(a.x,b.x),minY:Math.min(a.y,b.y),maxX:Math.max(a.x,b.x),maxY:Math.max(a.y,b.y)};
  if(!actual)return estimated;
  return {minX:Math.min(actual.minX,estimated.minX),minY:Math.min(actual.minY,estimated.minY),
    maxX:Math.max(actual.maxX,estimated.maxX),maxY:Math.max(actual.maxY,estimated.maxY)};
}
function graphWorkspaceBounds(includeHiddenLabels=true) {
  const graph=state.graph, inverse=graphUnprojector();
  if(!graph || !inverse)return null;
  let bounds=null;
  for(const [items,kind] of [[graph.getNodes(),'node'],[graph.getEdges(),'edge']])for(const item of items) {
    const boxes=includeHiddenLabels?[graphIndexedBounds(item,kind,inverse)]:[
      item.getKeyShape?.()?.getCanvasBBox?.(),
      ...(item.getModel().label!==undefined?labelShapes(item).filter(shape=>shape.get('visible')!==false).map(shape=>shape.getCanvasBBox?.()):[])
    ].filter(Boolean).map(box=>{
      const a=inverse(box.minX,box.minY),b=inverse(box.maxX,box.maxY);
      return {minX:Math.min(a.x,b.x),minY:Math.min(a.y,b.y),maxX:Math.max(a.x,b.x),maxY:Math.max(a.y,b.y)};
    });
    for(const box of boxes) {
      if(!box || ![box.minX,box.minY,box.maxX,box.maxY].every(Number.isFinite))continue;
      if(!bounds)bounds={...box};
      else {bounds.minX=Math.min(bounds.minX,box.minX);bounds.minY=Math.min(bounds.minY,box.minY);
        bounds.maxX=Math.max(bounds.maxX,box.maxX);bounds.maxY=Math.max(bounds.maxY,box.maxY);}
    }
  }
  return bounds;
}
function graphWorkspaceObstacles({width,height}) {
  const container=$('#graphContainer'), dx=width-container.clientWidth,dy=height-container.clientHeight;
  return graphChromeObstacles().map(obstacle=>{
    const x=obstacle.key==='chrome:legend'?0:dx, y=dy;
    const box=obstacle.bounds;
    return {...obstacle,bounds:{minX:box.minX+x,maxX:box.maxX+x,minY:box.minY+y,maxY:box.maxY+y}};
  });
}
function paintWorkspaceResize(graph) {
  // The pinned G6 4.8.19 Canvas clears its bitmap in changeSize, but paint()
  // only queues the next rAF. ResizeObserver runs after that frame's rAF.
  // Flush this renderer's full draw before paint so chrome cannot expose an
  // empty frame. Keep the private seam version/feature guarded; vendor stays pinned.
  if(graph!==state.graph || graph?.destroyed || state.graphView.exportDepth
    || typeof G6==='undefined' || G6.version!=='4.8.19')return false;
  const canvas=graph.get?.('canvas');
  if(canvas?.get?.('el')?.tagName!=='CANVAS' || typeof canvas._clearFrame!=='function' || typeof canvas._drawAll!=='function')return false;
  try {canvas._clearFrame();canvas._drawAll();return true;}
  catch (_) {graph.paint?.();return false;}
}
function rebuildGraphViewport() {
  const graph = state.graph, view = state.graphView;
  if (!graph) return;
  view.nodeItems = new Map(graph.getNodes().map(n => [n.getModel().id,n]));
  view.edgeItems = new Map(graph.getEdges().map(e => [edgeKey(e.getModel()),e]));
  const unproject=graphUnprojector();
  view.bodyWorld=new Map([...view.nodeItems].map(([id,item])=>[id,graphBodyWorld(item,unproject)]));
  const nodes = [...view.nodeItems.values()].map(n => { const m=n.getModel(); return {id:m.id,x:m.x,y:m.y,size:m.size,isMain:m.data?.is_main===1,bounds:graphIndexedBounds(n,'node',unproject)}; });
  const edges = [...view.edgeItems.values()].map(e => {const m=e.getModel();return {source:m.source,target:m.target,
    controlPoints:m.controlPoints,bounds:graphIndexedBounds(e,'edge',unproject),...state.edgeSnapshotLabels[edgeKey(m)]};});
  view.clipIndex = typeof FundsGraphViewport === 'undefined' ? null : FundsGraphViewport.build({nodes,edges});
  view.clipResult = null;
  state.minimap?.setData({nodes});
  applyGraphViewport(false);
  state.graphSelection?.refresh();
}
function applyGraphViewport(labels = false) {
  const graph = state.graph, view = state.graphView;
  if (!graph || !view.clipIndex || view.exportDepth) return;
  const rect = graphViewportRect(graph), padding=120/(graph.getZoom()||1);
  const full=view.clipIndex.containsAll(rect,padding);
  if(full && view.clipResult?.full) { state.minimap?.setViewport(rect); return; }
  const result=full ? {nodeIds:view.index.nodeIds,edgeKeys:view.index.edgeKeys,full:true} : view.clipIndex.query(rect,padding);
  const autoPaint=graph.get?.('autoPaint')!==false;
  graph.setAutoPaint(false);
  let changed=false;
  const show = (item, visible) => {
    const group=item.getContainer?.();
    if (visible) { if (view.clipped.has(item) || group?.get?.('visible') === false) {group?.show?.();changed=true;} view.clipped.delete(item); }
    else { if (!view.clipped.has(item)) {group?.hide?.();changed=true;} view.clipped.add(item); }
  };
  for (const [id,item] of view.nodeItems) show(item,result.nodeIds.has(id));
  for (const [key,item] of view.edgeItems) show(item,result.edgeKeys.has(key));
  view.clipResult=result; view.drawnNodes=result.nodeIds.size; view.drawnEdges=result.edgeKeys.size;
  state.minimap?.setViewport(rect);
  graph.setAutoPaint(autoPaint);
  if (labels) applyGraphLabels(autoPaint); else if (changed&&autoPaint) graph.paint();
}
function scheduleGraphViewport() {
  const graph=state.graph,view=state.graphView,context=caseContext(),revision=view.revision;
  if (view.exportDepth || view.viewportFrame != null || typeof requestAnimationFrame !== 'function') return;
  view.viewportFrame=requestAnimationFrame(() => {
    view.viewportFrame=null;
    if (graph!==state.graph || view!==state.graphView || revision!==view.revision || !isCurrentCase(context)) return;
    applyGraphViewport();
  });
}
function scheduleGraphLabels(zoom = false) {
  const graph = state.graph, view = state.graphView, context = caseContext(), revision = view.revision;
  if (zoom) view.zoomSettling = true;
  clearTimeout(view.labelTimer);
  // Coalesce LOD work until wheel input settles; culling remains RAF-driven.
  view.labelTimer = setTimeout(() => {
    if (view !== state.graphView || graph !== state.graph || revision !== view.revision || !isCurrentCase(context)) return;
    view.zoomSettling = false; applyGraphViewport(); applyGraphLabels();
  }, view.zoomSettling ? 140 : 0);
}
function neighborhoodBlocker(ignoreHistory = false) {
  const context = caseContext(), camera = graphLocationBlocker(ignoreHistory);
  if (camera) return camera.replace('定位', '应用');
  if (layoutJob) return '画布布局正在处理，请稍后查看邻域';
  if (window.FundsDataToolsActions?.blocked(context) || window.FundsMainAccountsActions?.blocked(context)) return '案件资料操作尚未结束，请先完成或核对';
  const cases = caseWorkspace?.status(), canvas = window.FundsCanvasActions?.status(), exported = exportWorkspace?.status();
  if (cases?.pending || cases?.phase === 'unknown' || canvas?.pending || canvas?.unknown || exported?.pending || batchView.deleting) return '案件或画布正在处理，请稍后应用';
  if ([...accountDataWrites].some(write => String(write.caseId) === String(context.caseId)) || nodeWrites.size || pcState.binding
    || nodeConfirmation || nodeWorkspace?.confirming || ['loading','removing'].includes(nodeWorkspace?.phase)) return '账户资料正在处理，请稍后应用';
  if (hasNodeDrafts()) return '账户资料有未保存修改，请先保存或放弃后再查看邻域';
  return '';
}
function installNeighborhood() {
  if (!window.FundsNeighborhood || neighborhoodWorkspace) return;
  let source = null, accounts = [];
  neighborhoodWorkspace = window.FundsNeighborhood.create({
    read: () => {
      const view = state.graphView;
      if (source !== state.lastGraph) {
        source = state.lastGraph; accounts = source.nodes.map(node => ({ account: String(node.account ?? node.id), name: node.name || '' }));
      }
      const index = view.fullIndex || FundsGraphView.createIndex(source);
      const adjacent = view.direction === 'in' ? index.incoming : view.direction === 'out' ? index.outgoing : index.neighbors;
      const neighbors = adjacent.get(view.root) || new Set();
      return { binding: JSON.stringify(caseContext()), ready: !!state.caseId && !!state.graph && !state.graphLoading,
        revision: view.revision, source, graph: source, presentation: state.presentation, accounts,
        applied: { root: view.visibleIds ? view.root || '' : '', name: accounts.find(node => node.account === view.root)?.name || '',
          direction: view.direction, nodes: view.index?.nodeIds.size || 0, edges: view.index?.edgeKeys.size || 0,
          expanded: view.visibleIds ? [...neighbors].filter(id => view.visibleIds.has(id)).length : 0,
          remaining: view.visibleIds ? [...neighbors].filter(id => !view.visibleIds.has(id)).length : 0, totalNeighbors: neighbors.size } };
    },
    blocker: () => neighborhoodBlocker(), formatCount: fmtCount,
    preview: ({ account, direction }, snapshot) => {
      const result = FundsGraphView.neighborhood(FundsGraphView.createIndex(snapshot.graph), account, { direction, limit: 50 });
      const display = FundsGraphPresentation.displayData(snapshot.graph, snapshot.presentation, result.nodeIds);
      return { nodes: display.stats.visibleNodes, edges: display.stats.visibleEdges, rootFound: result.rootFound, remaining: result.remaining };
    },
    locate: (account, binding) => binding === JSON.stringify(caseContext()) ? locateGraphAccount(account) : { ok: false, reason: '画布已切换，请重新搜索' },
    commit: async ({ account, direction }, binding) => {
      if (binding !== JSON.stringify(caseContext()) || neighborhoodBlocker()) return false;
      return await exploreNeighborhood(account, true, { direction }) === true;
    },
    expand: async binding => {
      if (binding !== JSON.stringify(caseContext()) || neighborhoodBlocker()) return false;
      return await exploreNeighborhood(state.graphView.root, false, { direction: state.graphView.direction }) === true;
    }
  });
  window.FundsNeighborhoodActions = Object.freeze({ update: () => neighborhoodWorkspace.update(), getState: () => neighborhoodWorkspace.getState() });
  for (const event of ['funds:graph-metric','funds:main-accounts-state']) window.addEventListener(event, () => neighborhoodWorkspace.update());
  document.addEventListener('funds:toolbar-menu-change', () => {
    if (activeGraphFocus?.graph === state.graph) state.graphCamera?.cancelFocus?.('overlay-changed');
  });
}
async function exploreNeighborhood(account, reset = false, options = {}) {
  const reason = neighborhoodBlocker(true);
  if (reason) { toast(reason, 'err'); return false; }
  account = String(account ?? '').trim();
  const view = state.graphView;
  if (!view.fullIndex) view.fullIndex = FundsGraphView.createIndex(state.lastGraph);
  const direction = ['both','in','out'].includes(options.direction) ? options.direction
    : !reset && view.visibleIds && view.root === account ? view.direction : 'both';
  const continuing=!reset&&view.visibleIds&&view.root===account&&view.direction===direction;
  const previousVisible=continuing?new Set(view.visibleIds):null;
  const result = FundsGraphView.neighborhood(view.fullIndex, account, { direction, limit: 50,
    visible: continuing ? view.visibleIds : null });
  if (!result.rootFound) { toast('当前追踪图谱中未找到该账号，请输入完整账号', 'err'); return false; }
  savePositions(); closeDrawer();
  view.visibleIds = result.nodeIds; view.root = account; view.direction = direction; view.remaining = result.remaining;
  view.recentNodeIds=previousVisible?new Set([...result.nodeIds].filter(id=>!previousVisible.has(id))):new Set();
  view.selectedAccount = account; view.hoveredAccount = null;
  if (!neighborhoodWorkspace) $('#neighborAccount').value = account;
  state.forceFit = false;
  graphMetric('request', { reason: 'neighborhood' });
  renderGraph();
  // Continued expansion preserves the user's mental map; changing the center
  // uses the same camera path as explicit search, with no node relayout.
  if (!continuing && graphAccountAvailability(account).visible) {
    await focusGraphItems([state.graph.findById(account)], { allowHistoryBusy: true, preferredZoom: Math.max(.85, state.graph.getZoom?.() || 1) });
  }
  return true;
}
function showGraphOverview() {
  savePositions(); closeDrawer();
  state.presentation = newGraphPresentation({ ...state.presentation, relationFilter: {}, hiddenAccounts: [], onlyAccounts: null });
  const mode = state.graphView.labelMode,autoRoutes=state.graphView.autoRoutes;
  resetGraphView(); state.graphView.labelMode = mode;state.graphView.autoRoutes=autoRoutes;
  state.graphView.fullIndex = FundsGraphView.createIndex(state.lastGraph);
  state.forceFit = true;
  graphMetric('request', { reason: 'overview' });
  renderGraph();
}

/* ---------------- 图谱 ---------------- */
function initGraph() {
  state.graphCamera?.resize();
  const chromeOffset = state.graphCamera?.getOffset();
  const chromeTransform = state.graphCamera?.getTransform?.();
  state.graphCamera?.destroy();
  cancelGraphLayout();
  releaseIdleGraphLayoutWorker();
  revokeZoomOptimization();
  if (typeof G6 === 'undefined') {
    toast('图谱引擎 G6 未加载（vendor/g6.min.js 缺失），图谱功能不可用', 'err');
    return;
  }
  // 可重入：销毁旧实例（整图重建时彻底清画布，避免 G6 clear() 在 canvas 上留残影）
  state.graphSelection?.destroy(); state.graphSelection = null;
  for (const dispose of state.graphDisposers.splice(0)) dispose();
  if (state.graph) {
    state.minimap?.destroy(); state.minimap = null;
    try { state.graph.destroy(); } catch (e) { /* 忽略 */ }
    state.graph = null;
    state.graphInited = false;
  }
  state.graphResizeObserver?.disconnect();
  if (state.graphResizeHandler) window.removeEventListener('resize', state.graphResizeHandler);
  const c = $('#graphContainer');
  const graph = new G6.Graph({
    container: c,
    width: c.clientWidth || 900,
    height: c.clientHeight || 600,
    // Revoke G6's deferred optimization before replacing any graph items.
    modes: { default: [
      { type: 'drag-canvas', enableOptimize: true, shouldBegin: () => graphCameraInteractionAllowed() },
      { type: 'zoom-canvas', enableOptimize: true, optimizeZoom: 0.001, shouldBegin: () => graphCameraInteractionAllowed() },
      ...(typeof FundsGraphSelection === 'undefined' ? [{type:'drag-node',shouldBegin:()=>!layoutJob&&!state.graphLoading&&!window.FundsCanvasEditor?.history.status().busy}] : []),
    ] },
    plugins: [],
    fitView: false,
    animate: false,
    minZoom: 0.02,
    maxZoom: 4,
    defaultNode: {
      type: 'circle', size: 32,
      style: { stroke: '#0e1013', lineWidth: 2 },
      labelCfg: {
        position: 'bottom', offset: 6,
        style: FundsGraphPresentation.effectiveLabel('node'),
      },
    },
    defaultEdge: {
      type: 'quadratic',
      style: {
        stroke: '#7f8b99', lineWidth: 1.4,
        endArrow: { path: 'M 0,0 L 10,5 L 10,-5 Z', fill: '#7f8b99' },
      },
      labelCfg: {
        autoRotate: false,
        style: FundsGraphPresentation.effectiveLabel('edge'),
      },
    },
    nodeStateStyles: { selected: { stroke: '#66d9ef', lineWidth: 3 }, recent: { lineDash: [4, 3] } },
    edgeStateStyles: { selected: { stroke: '#f5a623' } },
  });
  state.graph = graph;
  const mini = $('#graphMinimap');
  if (mini?.getContext && typeof FundsGraphMinimap !== 'undefined') {
    state.minimap = FundsGraphMinimap.create({ canvas: mini, onNavigate: point => {
      if (state.graph !== graph || !graphCameraInteractionAllowed()) return;
      state.graphCamera?.resize();
      state.graphCamera?.cancelComposition?.();
      const c=$('#graphContainer'), p=graph.getCanvasByPoint(point.x,point.y);
      graph.translate(c.clientWidth/2-p.x,c.clientHeight/2-p.y);
      scheduleGraphViewport(); scheduleGraphLabels();
    } });
  }
  graph.on('viewportchange', event => {
    if (state.graph !== graph) return;
    clearGraphLocateFeedback();
    scheduleGraphViewport();
    if (event.action === 'zoom') scheduleGraphLabels(true);
  });
  graph.on('wheelzoom', () => graphMetric('viewport', { action: 'zoom' }));
  graph.on('node:mouseenter', event => { state.graphView.hoveredAccount = event.item.getModel().id; scheduleGraphLabels(); });
  graph.on('node:mouseleave', () => { state.graphView.hoveredAccount = null; scheduleGraphLabels(); });
  graph.on('node:dblclick', event => {
    const source = event.originalEvent || event;
    if (!source.ctrlKey && !source.metaKey) exploreNeighborhood(event.item.getModel().id);
  });
  initGraphSelection(graph, c);
  if (typeof FundsGraphSelection === 'undefined') graph.on('node:click', ev => {
    if (!layoutJob && !state.graphLoading && !window.FundsCanvasEditor?.history.status().busy) {
      closeDrawer(false); selectItem(ev.item);
    }
  });
  graph.on('edge:click', ev => {
    if(layoutJob||state.graphLoading||window.FundsCanvasEditor?.history.status().busy)return;
    const source = ev.originalEvent || ev;
    if (source.button != null && source.button !== 0) return;
    c.focus?.({ preventScroll: true });
    closeDrawer(false); selectItem(ev.item); refreshSelectedEdgeAppearance();
  });
  // 拖拽节点后立即保存位置，后续刷新/上图不会把节点弹回原位
  graph.on('node:dragstart', () => {captureEdgeLabelRatios();cancelGraphLayout();});
  graph.on('node:mousedown', () => {if(!layoutJob&&!window.FundsCanvasEditor?.history.status().busy)cancelGraphLayout();});
  graph.on('node:drag', event => { refreshGraphRoutesAndLabels(event.item?.getEdges?.()); graphMetric('node-drag'); });
  graph.on('dragnodeend', () => { refreshGraphRoutesAndLabels(); restoreEdgeLabelPositions();savePositions(); rebuildGraphViewport(); scheduleGraphLabels(); graphMetric('node-drag'); });
  graph.on('canvas:drag', () => graphMetric('canvas-drag'));
  graph.on('canvas:dragend', () => { scheduleGraphLabels(); graphMetric('canvas-drag'); });
  graph.on('node:dragend', () => savePositions());
  if (!state.graphSelection) graph.on('canvas:click', () => { if (_selItem) closeDrawer(); });
  initEdgeLabelDrag(graph);
  initEdgeBendDrag(graph);
  state.graphCamera = window.FundsWorkspaceCamera.create({
    container: c, graph, initialOffset: chromeOffset, initialTransform: chromeTransform,
    isCurrent: () => state.graph === graph,
    isSuspended: () => !!state.graphView.exportDepth,
    canCompose: () => !state.graphLoading&&!layoutJob&&!window.FundsCanvasEditor?.history?.status?.().busy,
    getContentBounds: graphWorkspaceBounds,
    getVisibilityBounds: () => graphWorkspaceBounds(false),
    getViewportObstacles: graphWorkspaceObstacles,
    onResize: ({ width, height }) => {
      if (layoutJob && (layoutJob.input?.width !== width || layoutJob.input?.height !== height)) layoutJob.invalidate?.();
      scheduleGraphViewport(); scheduleGraphLabels(); state.graphSelection?.refresh();
      paintWorkspaceResize(graph);
    }
  });
  const interruptCamera = () => {
    if(state.graph!==graph)return;
    state.graphCamera?.resize();
    state.graphCamera?.cancelComposition?.();
    clearGraphLocateFeedback();
    if (state.graphView.previewAccounts?.size) { state.graphView.previewAccounts.clear(); scheduleGraphLabels(); }
  };
  for(const type of ['pointerdown','wheel'])c.addEventListener(type,interruptCamera,{capture:true,passive:true});
  state.graphDisposers.push(()=>{for(const type of ['pointerdown','wheel'])c.removeEventListener(type,interruptCamera,true);});
  const resize = () => state.graphCamera?.resize();
  if (typeof ResizeObserver !== 'undefined') { state.graphResizeObserver = new ResizeObserver(resize); state.graphResizeObserver.observe(c); }
  else { state.graphResizeHandler = resize; window.addEventListener('resize', resize); }
}

function initGraphSelection(graph = state.graph, c = $('#graphContainer')) {
  if (typeof FundsGraphSelection !== 'undefined') {
    const context = caseContext();
    state.graphSelection = FundsGraphSelection.create({ graph, container: c, context,
      isCurrent: binding => graph === state.graph && isCurrentCase(binding) && !state.graphLoading,
      canInteract: () => !layoutJob&&!window.FundsCanvasEditor?.history.status().busy,
      onChange: ({ ids }) => {
        closeDrawer(false);
        state.graphView.previewAccounts?.clear();
        state.graphView.selectedAccount = ids.size <= 10 ? [...ids].at(-1) || null : null;
        scheduleGraphLabels(); updateGraphViewStatus();
      },
      onMoveStart: () => {
        cancelGraphLayout(); captureEdgeLabelRatios();
        window.dispatchEvent(new CustomEvent('funds:selection-drag-start'));
      },
      onBeforeMove: ({ ids, dx, dy }) => translateSelectedBends(ids, dx, dy),
      onAfterMove: ({ ids }) => {
        const edges=new Set();
        for(const id of ids)for(const edge of graph.findById(id)?.getEdges?.()||[])edges.add(edge);
        refreshGraphRoutesAndLabels([...edges]);
      },
      onMoveEnd: result => {
        try {
          if (result.moved || result.cancelled) {
            refreshGraphRoutesAndLabels(); restoreEdgeLabelPositions();
            try {
              if(result.committed&&!result.cancelled) {
                const eligibleKeys=new Set();
                for(const id of result.ids)for(const edge of graph.findById(id)?.getEdges?.()||[])eligibleKeys.add(edgeKey(edge.getModel()));
                if(eligibleKeys.size)improveGraphRoutes({fit:false,eligibleKeys});
              }
            } catch(_) {
              state.graphView.routeQuality={accepted:false,reason:'route-error'};
              toast('节点已移动，连线路径优化未完成，可重新布局后重试。','err');
            } finally {
              savePositions(); rebuildGraphViewport(); scheduleGraphLabels(); graphMetric('node-drag');
            }
          }
        } finally {
          window.dispatchEvent(new CustomEvent('funds:selection-drag-end', { detail: result }));
        }
      },
    });
  }
}

// A manual bend belongs to the whole relationship. Move it only when both
// endpoints move together; boundary relationships retain their anchored bends.
function translateSelectedBends(ids, dx, dy) {
  const selected = ids instanceof Set ? ids : new Set(ids);
  const affected = new Set();
  for (const id of selected) {
    const node = state.graphView.nodeItems.get(id);
    for (const edge of node?.getEdges?.() || []) affected.add(edge);
  }
  for (const item of affected) {
    const key = edgeKey(item.getModel());
    const model = item.getModel(), points = state.edgeBendPos[key];
    if (!points || !selected.has(model.source) || !selected.has(model.target)) continue;
    const moved = points.map(p => ({ x: p.x + dx, y: p.y + dy }));
    state.edgeBendPos[key] = moved; model.controlPoints = moved;
  }
}

function isCurrentGraphGesture(gesture, graph) {
  return graph === state.graph && isCurrentCase(gesture.context)
    && gesture.view === state.graphView && gesture.revision === state.graphView.revision;
}

function startGraphEdgeGesture(event, graph) {
  const source=event.originalEvent||event.event||event;
  if ((source.button!=null&&source.button!==0)||graph!==state.graph||state.graphLoading
    ||layoutJob||window.FundsCanvasEditor?.history.status().busy) return false;
  cancelGraphLayout();
  return window.dispatchEvent(new CustomEvent('funds:edge-drag-start',{cancelable:true}))!==false;
}
function bindGraphEdgeGesture(move, finish) {
  const moving=event=>{
    if(typeof event.buttons==='number'&&!(event.buttons&1))finish(false);
    else move(event);
  };
  const done=()=>finish(false),exit=event=>{if(event.relatedTarget===null)done();};
  const hidden=()=>{if(document.hidden)done();},escape=event=>{if(event.key==='Escape')finish(true);};
  window.addEventListener('mousemove',moving);window.addEventListener('mouseup',done);
  window.addEventListener('blur',done);window.addEventListener('mouseout',exit);
  window.addEventListener('keydown',escape);document.addEventListener?.('visibilitychange',hidden);
  return ()=>{
    window.removeEventListener('mousemove',moving);window.removeEventListener('mouseup',done);
    window.removeEventListener('blur',done);window.removeEventListener('mouseout',exit);
    window.removeEventListener('keydown',escape);document.removeEventListener?.('visibilitychange',hidden);
  };
}

/* ---------------- 折线折点：拖动添加/移动折点，双击折点删除 ----------------
   - 按住边空白处拖动 → 在该位置插入一个新折点（不覆盖已有折点）
   - 按住已有折点附近（≤10px）拖动 → 移动该折点
   - 双击已有折点附近（≤20px）→ 删除该折点；全部删完自动还原线型 */
function initEdgeBendDrag(graph) {
  let bend = null;   // { edge, key, pts, idx }

  graph.on('edge:mousedown', e => {
    if (!e.item || !['polyline', 'line'].includes(e.item.getModel().type || graphEdgeType(e.item.getModel()))) return;
    if (isLabelShape(e.target)) return;            // 标签拖拽交给标签逻辑
    if (e.item && e.item.getModel().type === 'loop') return;
    const binding = { context: caseContext(), view: state.graphView, revision: state.graphView.revision };
    const m = e.item.getModel();
    const key = edgeKey(m);
    const cps = (state.edgeBendPos[key] || []).map(p => ({ x: p.x, y: p.y }));
    const anchor = graph.getPointByClient(e.clientX, e.clientY);
    if(!anchor||![anchor.x,anchor.y].every(Number.isFinite))return;
    const original={points:state.edgeBendPos[key]?.map(p=>({...p})),type:m.type,
      controlPoints:m.controlPoints?.map(p=>({...p}))||[],curvePolicy:m.curvePolicy};
    // 命中已有折点 → 移动模式
    let hit = -1;
    for (let i = 0; i < cps.length; i++) {
      if (((cps[i].x - anchor.x) ** 2 + (cps[i].y - anchor.y) ** 2) * (graph.getZoom?.() || 1) ** 2 <= 10 * 10) { hit = i; break; }
    }
    if (hit >= 0) {
      if(!startGraphEdgeGesture(e,graph))return;
      bend = { ...binding, edge: e.item, key, pts: cps, idx: hit, original };
      e.preventDefault && e.preventDefault();
      return;
    }
    // 插入模式：投影到 源→折点…→目标 各段，取最近段插入
    const src = graph.findById(m.source), tgt = graph.findById(m.target);
    if (!src || !tgt) return;
    const sm = src.getModel(), tm = tgt.getModel();
    if (sm.x == null || tm.x == null) return;
    const chain = [{ x: sm.x, y: sm.y }, ...cps, { x: tm.x, y: tm.y }];
    let seg = 0, bd = Infinity;
    for (let i = 1; i < chain.length; i++) {
      const ax = chain[i - 1].x, ay = chain[i - 1].y;
      const dx = chain[i].x - ax, dy = chain[i].y - ay;
      const L2 = dx * dx + dy * dy || 1e-6;
      const u = Math.max(0, Math.min(1, ((anchor.x - ax) * dx + (anchor.y - ay) * dy) / L2));
      const d = (ax + dx * u - anchor.x) ** 2 + (ay + dy * u - anchor.y) ** 2;
      if (d < bd) { bd = d; seg = i; }
    }
    const idx = seg - 1;
    const pts = cps.slice();
    pts.splice(idx, 0, { x: anchor.x, y: anchor.y });
    if(!startGraphEdgeGesture(e,graph))return;
    bend = { ...binding, edge: e.item, key, pts, idx, original };
    e.preventDefault && e.preventDefault();
  });

  const move = e => {
    if (!bend) return;
    if (!isCurrentGraphGesture(bend, graph)) { bend = null; return; }
    try {
      const p = graph.getPointByClient(e.clientX, e.clientY);
      bend.pts[bend.idx] = { x: p.x, y: p.y };
      state.edgeBendPos[bend.key] = bend.pts.map(q => ({ x: q.x, y: q.y }));
      // 直线模式下拖出的边按需转为折线（只影响该条边），其余边保持直线
      const upd = { controlPoints: state.edgeBendPos[bend.key], curvePolicy:null };
      upd.type = graphEdgeType(bend.edge.getModel());
      try { graph.updateItem(bend.edge, upd); } catch (err) { /* 忽略 */ }
      syncGraphEdgeLabel(bend.edge); refreshSelectedEdgeAppearance();
    } catch (err) { /* 忽略 */ }
  };

  const finish = cancelled => {
    const active=bend;bend=null;
    if(!active||!isCurrentGraphGesture(active,graph))return;
    if(cancelled) {
      if(active.original.points)state.edgeBendPos[active.key]=active.original.points;
      else delete state.edgeBendPos[active.key];
      graph.updateItem(active.edge,{type:active.original.type,controlPoints:active.original.controlPoints,curvePolicy:active.original.curvePolicy});
      syncGraphEdgeLabel(active.edge);refreshSelectedEdgeAppearance();
    }
    savePositions();rebuildGraphViewport();scheduleGraphLabels();
    window.dispatchEvent(new CustomEvent('funds:edge-drag-end',{detail:{cancelled}}));
  };
  const dispose=bindGraphEdgeGesture(move,finish);
  state.graphDisposers.push(() => { bend = null;dispose(); });

  graph.on('edge:dblclick', e => {
    if(layoutJob||state.graphLoading||window.FundsCanvasEditor?.history.status().busy)return;
    if (!e.item || !['polyline', 'line'].includes(e.item.getModel().type || graphEdgeType(e.item.getModel()))) return;
    if (isLabelShape(e.target)) return;
    const key = edgeKey(e.item.getModel());
    const cps = state.edgeBendPos[key];
    if (!cps || !cps.length) return;
    const anchor = graph.getPointByClient(e.clientX, e.clientY);
    let bi = -1, bd = Infinity;
    cps.forEach((p, i) => {
      const d = (p.x - anchor.x) ** 2 + (p.y - anchor.y) ** 2;
      if (d < bd) { bd = d; bi = i; }
    });
    if (bi >= 0 && bd * (graph.getZoom?.() || 1) ** 2 <= 20 * 20) {
      cps.splice(bi, 1);
      if (cps.length) {
        state.edgeBendPos[key] = cps;
        try { graph.updateItem(e.item, { controlPoints: cps }); } catch (err) { /* 忽略 */ }
      } else {
        delete state.edgeBendPos[key];
        try { graph.updateItem(e.item, { controlPoints: [], type: graphEdgeType(e.item.getModel()) }); } catch (err) { /* 忽略 */ }
      }
    }
  });
}

/* ---------------- 边标签拖拽（沿线上平移） ---------------- */
function edgeKey(m) { return m.source + '=>' + m.target; }
function graphEdgeType(edge) {
  if (edge.source === edge.target) return 'loop';
  const type = state.edgeTypes[edgeKey(edge)] || state.edgeType;
  return type === 'line' && state.edgeBendPos[edgeKey(edge)]?.length ? 'polyline' : type;
}
function graphHasSimpleGeometry(positions) {
  if(typeof FundsGraphLabelGeometry==='undefined')return false;
  const graph=state.graph,full=state.lastGraph;
  if(full.nodes.length>24||full.edges.length>30)return false;
  // Test the complete tracked graph, so filtering cannot reroute a complex case.
  return FundsGraphLabelGeometry.isSimpleGraph({nodes:full.nodes.map(n=>{
    const p=positions?.get(n.account)||graph?.findById?.(n.account)?.getModel?.()||state.positions[n.account];
    return {id:n.account,x:p?.x,y:p?.y};
  }),edges:full.edges});
}
function graphEdgeRoute(edge,positions) {
  const key=edgeKey(edge),type=graphEdgeType(edge),points=state.edgeBendPos[key];
  const manual=Object.prototype.hasOwnProperty.call(state.edgeTypes,key)||points?.length;
  const source=positions?.get(edge.source),target=positions?.get(edge.target);
  const cached=state.graphView.autoRoutes?.[key];
  const followed=()=>({type,controlPoints:FundsGraphRouting.rebase(cached.controlPoints,cached.source,cached.target,source,target),
    curvePolicy:cached.curvePolicy||'obstacle-aware'});
  if(!manual&&type==='quadratic'&&cached&&!['screen-right','compact-arc'].includes(cached.curvePolicy)&&source&&target&&typeof FundsGraphRouting!=='undefined')return followed();
  if(!manual&&type==='quadratic'&&source&&target) {
    const control=FundsGraphLabelGeometry.compactControlPoint(source,target);
    if(control)return {type:'quadratic',controlPoints:[control],curvePolicy:'compact-arc'};
  }
  return {type,controlPoints:points||[],curvePolicy:null};
}

// Routing reads complete tracked geometry. Display filters never become a new
// topology and camera/label refreshes never invoke this planner.
function graphRouteWorldBounds(box,unproject) {
  if(!box||!unproject)return null;
  const corners=[unproject(box.minX,box.minY),unproject(box.minX,box.maxY),unproject(box.maxX,box.minY),unproject(box.maxX,box.maxY)];
  return {minX:Math.min(...corners.map(p=>p.x)),minY:Math.min(...corners.map(p=>p.y)),maxX:Math.max(...corners.map(p=>p.x)),maxY:Math.max(...corners.map(p=>p.y))};
}
function graphRouteQualityEdge(item,unproject=graphUnprojector()) {
  const model=item.getModel(),path=item.getKeyShape?.()?.attr?.('path');
  return Array.isArray(path)?{id:edgeKey(model),source:model.source,target:model.target,path,
    label:graphRouteWorldBounds(graphLabelBounds(item,'edge'),unproject)}:null;
}
function graphRouteScene(eligibleKeys,{planner=true,quality:measureQuality=true}={}) {
  const graph=state.graph,view=state.graphView,zoom=Math.abs(graph.getZoom?.()||1),unproject=graphUnprojector();
  const world=box=>graphRouteWorldBounds(box,unproject);
  const nodes=[];
  for(const data of state.lastGraph.nodes) {
    const item=graph.findById?.(data.account),m=item?.getModel?.(),p=m||state.positions[data.account];
    if(!Number.isFinite(p?.x)||!Number.isFinite(p?.y))continue;
    const size=m?.size||nodeSize(data),diameter=Array.isArray(size)?Math.max(...size):size;
    let body=item?world(graphNodeBodyBounds(item)):null;
    const shape=item?.getKeyShape?.(),local=shape?.getBBox?.(),matrix=shape?.getTotalMatrix?.();
    if(local&&[local.minX,local.minY,local.maxX,local.maxY].every(Number.isFinite)) {
      const corners=[[local.minX,local.minY],[local.minX,local.maxY],[local.maxX,local.minY],[local.maxX,local.maxY]];
      // Route obstacles are the solid key shape (including its stroke), not
      // its screen-space blur. Keep the complete shadow envelope for label
      // anchoring and fit. Total matrices also preserve local transforms.
      const points=matrix&&matrix.length===9&&Array.from(matrix).every(Number.isFinite)&&unproject
        ?corners.map(([x,y])=>unproject(matrix[0]*x+matrix[3]*y+matrix[6],matrix[1]*x+matrix[4]*y+matrix[7]))
        :!shape?.getMatrix?.()?corners.map(([x,y])=>({x:p.x+x,y:p.y+y})):null;
      if(points?.every(v=>Number.isFinite(v.x)&&Number.isFinite(v.y)))body={minX:Math.min(...points.map(v=>v.x)),
        minY:Math.min(...points.map(v=>v.y)),maxX:Math.max(...points.map(v=>v.x)),maxY:Math.max(...points.map(v=>v.y))};
    }
    const fallbackBody={minX:p.x-diameter/2,maxX:p.x+diameter/2,minY:p.y-diameter/2,maxY:p.y+diameter/2};
    const actualBody=body||fallbackBody;
    const actualLabel=item?world(graphLabelBounds(item,'node')):null;
    const fullText=actualLabel?null:graphTextSize(m?.fundsLabel??nodeLabel(data),effectiveGraphLabelStyle('node',data.account));
    const label=actualLabel||{minX:p.x-fullText.width/2,maxX:p.x+fullText.width/2,
      minY:actualBody.maxY+6/zoom,maxY:actualBody.maxY+6/zoom+fullText.height};
    nodes.push({id:data.account,x:p.x,y:p.y,body:actualBody,label});
  }
  const byId=new Map(nodes.map(n=>[n.id,n])),liveEdges=new Map(graph.getEdges().map(item=>[edgeKey(item.getModel()),item]));
  const positions=new Map(nodes.map(n=>[n.id,n])),simple=graphHasSimpleGeometry(positions),edges=[],qualityEdges=[];
  for(const data of state.lastGraph.edges) {
    if(!byId.has(data.source)||!byId.has(data.target))continue;
    const id=edgeKey(data),item=liveEdges.get(id),quality=measureQuality&&item&&graphRouteQualityEdge(item,unproject);
    if(quality)qualityEdges.push(quality);
    if(!planner)continue;
    const m=item?.getModel?.()||{...data,...graphEdgeRoute(data,positions,simple)};
    const shape=item?.getKeyShape?.(),path=shape?.attr?.('path');
    const sampled=Array.isArray(path)&&typeof FundsGraphRouteQuality!=='undefined'?FundsGraphRouteQuality.sample(path,{steps:24}):null;
    const size=graphTextSize(m.fundsLabel??edgeLabel(data),effectiveGraphLabelStyle('edge',id));
    const ratio=state.edgeLabelPos[id],manual=Object.prototype.hasOwnProperty.call(state.edgeTypes,id)||!!state.edgeBendPos[id]?.length;
    const labelPath=item&&Number.isFinite(ratio)?edgePts(item):null;
    const anchor=labelPath?ptAtRatio(labelPath,ratio):shape?.getPoint?.(.5);
    const edge={id,source:data.source,target:data.target,type:m.type||graphEdgeType(data),controlPoints:m.controlPoints||[],
      manual:manual||!item||eligibleKeys&&!eligibleKeys.has(id),labelWidth:size.width,labelHeight:size.height,labelRatio:ratio,
      sampledPoints:sampled?.valid&&!sampled.truncated?sampled.points:null,labelAnchor:anchor};
    edges.push(edge);
  }
  return {nodes,edges,simple,qualityEdges,renderedEdges:qualityEdges.length,totalEdges:state.lastGraph.edges.length};
}
function graphActualRouteQuality(scene=graphRouteScene(undefined,{planner:false})) {
  const result=FundsGraphRouteQuality.evaluate({nodes:scene.nodes,edges:scene.qualityEdges,maxChecks:120000});
  return {...result,renderedEdges:scene.renderedEdges,totalEdges:scene.totalEdges};
}
function graphRouteQualityImproves(before,after,changedKeys) {
  if(!before?.valid||!after?.valid||!before.coverage.complete||!after.coverage.complete)return false;
  let improved=false;
  for(const key of ['edgeNode','labelNode','edgeNodeLabel','edgeLabel','labelOverlap','collinearOverlap','crossings']) {
    if(after.counts[key]>before.counts[key])return false;
    const prior=before.collisionKeys?.[key],next=after.collisionKeys?.[key];
    if(!Array.isArray(prior)||!Array.isArray(next)||prior.length!==before.counts[key]||next.length!==after.counts[key]
      ||!prior.concat(next).every(identity=>typeof identity==='string'))return false;
    const allowed=new Set(prior);
    if(allowed.size!==prior.length||new Set(next).size!==next.length||next.some(identity=>!allowed.has(identity)))return false;
    if(after.counts[key]<before.counts[key])improved=true;
  }
  if(improved)return true;
  let beforeLength=before.totalLength,afterLength=after.totalLength;
  if(changedKeys!==undefined) {
    beforeLength=0;afterLength=0;
    for(const key of changedKeys) {
      const prior=before.edgeLengths?.[key],next=after.edgeLengths?.[key];
      if(!Number.isFinite(prior)||!Number.isFinite(next))return false;
      beforeLength+=prior;afterLength+=next;
    }
  }
  return Number.isFinite(beforeLength)&&Number.isFinite(afterLength)
    &&beforeLength-afterLength>=Math.max(8,beforeLength*.03);
}
function improveGraphRoutes({fit=false,eligibleKeys,proposal:preparedProposal}={}) {
  if(typeof FundsGraphRouting==='undefined'||typeof FundsGraphRouteQuality==='undefined')return;
  const graph=state.graph,view=state.graphView;
  const work=preparedProposal?null:{planningMs:0};
  if(work)(view.routeWork||(view.routeWork=Object.create(null)))[state.layout]=work;
  if(state.lastGraph.edges.length>1500||state.lastGraph.nodes.length>3000) {
    view.routeQuality={accepted:false,reason:'obstacle-budget',evaluated:false,coverage:{complete:false},totalEdges:state.lastGraph.edges.length};return;
  }
  const scene=graphRouteScene(eligibleKeys,{planner:!preparedProposal}),before=graphActualRouteQuality(scene);
  if(!before.valid||!before.coverage.complete){
    (view.routeWork||(view.routeWork=Object.create(null)))[state.layout]={planningMs:0};
    view.routeQuality={before,after:before,accepted:false,reason:'quality-budget'};return;}
  const boxes=[...graph.getNodes(),...graph.getEdges()].map(item=>graphItemBounds(item)).filter(Boolean);
  const routingBounds=boxes.length?{minX:Math.min(...boxes.map(b=>b.minX)),minY:Math.min(...boxes.map(b=>b.minY)),
    maxX:Math.max(...boxes.map(b=>b.maxX)),maxY:Math.max(...boxes.map(b=>b.maxY))}:null;
  let proposal=preparedProposal;
  if(!proposal){const started=performance.now();try{proposal=FundsGraphRouting.plan({...scene,maxChecks:120000,maxEdges:320,routingBounds,conservative:true});}
    finally{work.planningMs=performance.now()-started;}}
  if(!proposal.routes.length){view.routeQuality={before,after:before,planner:proposal.stats,accepted:false};return;}
  const clone=value=>JSON.parse(JSON.stringify(value)),cache=clone(view.autoRoutes||{}),camera=graph.get?.('group')?.getMatrix?.();
  const savedCamera=camera?Array.from(camera):null,oldZoom=graph.getZoom?.()||1,fitZoom=view.fitZoom;
  const oldRoutes=new Map(),positions=new Map(scene.nodes.map(n=>[n.id,n]));
  // Measure shortening only for installed geometry that is still changed.
  // Unrelated long relationships must not dilute a local route improvement;
  // the full-scene collision and coverage checks remain authoritative.
  const changedRouteKeys=()=>[...oldRoutes].filter(([item,prior])=>{
    const model=item.getModel();return model.type!==prior.type
      ||JSON.stringify(model.controlPoints||[])!==JSON.stringify(prior.controlPoints);
  }).map(([item])=>edgeKey(item.getModel()));
  const restore=()=>{
    view.autoRoutes=clone(cache);view.fitZoom=fitZoom;
    const group=graph.get?.('group');if(typeof group?.getMatrix==='function')group.setMatrix?.(savedCamera||[1,0,0,0,1,0,0,0,1]);
    for(const [item,route]of oldRoutes){graph.updateItem(item,route);syncGraphEdgeLabel(item);}
    rebuildGraphViewport();applyGraphLabels(false);
  };
  const install=route=>{
    const item=view.edgeItems.get(route.id)||graph.getEdges().find(e=>edgeKey(e.getModel())===route.id);if(!item)return null;
    const m=item.getModel(),source=positions.get(m.source),target=positions.get(m.target);
    if(Object.prototype.hasOwnProperty.call(state.edgeTypes,route.id)||state.edgeBendPos[route.id]?.length||!source||!target)return null;
    if(!oldRoutes.has(item))oldRoutes.set(item,{type:m.type,controlPoints:clone(m.controlPoints||[]),curvePolicy:m.curvePolicy||null});
    view.autoRoutes[route.id]={source:{x:source.x,y:source.y},target:{x:target.x,y:target.y},controlPoints:clone(route.controlPoints)};
    graph.updateItem(item,{type:'quadratic',controlPoints:route.controlPoints,curvePolicy:'obstacle-aware'});syncGraphEdgeLabel(item);return item;
  };
  try {
    for(const route of proposal.routes) {
      const prior=view.autoRoutes[route.id],item=install(route);
      if(preparedProposal&&item) {
        const actual=graphRouteQualityEdge(item),sample=actual&&FundsGraphRouteQuality.sample(actual.path,{steps:24});
        // Control points are not curve extrema: some hosts' container boxes
        // include the whole control hull. Compare the actual sampled path and
        // glyph envelope at the same precision as the routing quality gate.
        const within=sample?.valid&&!sample.truncated&&routingBounds&&sample.points.every(p=>p.x>=routingBounds.minX-1e-6&&p.x<=routingBounds.maxX+1e-6
          &&p.y>=routingBounds.minY-1e-6&&p.y<=routingBounds.maxY+1e-6)&&(!actual.label||actual.label.minX>=routingBounds.minX-1e-6
            &&actual.label.maxX<=routingBounds.maxX+1e-6&&actual.label.minY>=routingBounds.minY-1e-6&&actual.label.maxY<=routingBounds.maxY+1e-6);
        if(!within) {
          if(prior)view.autoRoutes[route.id]=prior;else delete view.autoRoutes[route.id];
          graph.updateItem(item,oldRoutes.get(item));syncGraphEdgeLabel(item);
        }
      }
    }
    rebuildGraphViewport();if(fit)fitGraph();else applyGraphLabels(false);
    const after=graphActualRouteQuality();
    const nodeIds=new Set(scene.nodes.map(n=>n.id));
    const minimumFont=Math.min(...scene.nodes.map(n=>effectiveGraphLabelStyle('node',n.id).fontSize),
      ...state.lastGraph.edges.filter(e=>nodeIds.has(e.source)&&nodeIds.has(e.target)).map(e=>effectiveGraphLabelStyle('edge',edgeKey(e)).fontSize));
    const readingPreserved=Math.min(12,minimumFont*(graph.getZoom?.()||1))+1e-6>=Math.min(12,minimumFont*oldZoom);
    let accepted=readingPreserved&&graphRouteQualityImproves(before,after,changedRouteKeys()),acceptedQuality=accepted?after:before,actualTrials=0;
    if(!accepted) {
      restore();
      const trialScene=graphRouteScene(undefined,{planner:false});
      trialScene.qualityEdges=trialScene.qualityEdges.map(edge=>({...edge,path:edge.path.map(command=>command.slice())}));
      const trialIndices=new Map(trialScene.qualityEdges.map((edge,index)=>[edge.id,index]));
      const trialUnproject=graphUnprojector();
      // G6 attachment points and glyph boxes can differ from the pure planner's
      // approximation. Salvage only independently verified improvements, with
      // a small fixed number of real-render evaluations and no camera changes.
      const severity=new Map();
      for(const [rank,kind]of ['edgeNode','labelNode','edgeNodeLabel','edgeLabel','labelOverlap','crossings'].entries()) {
        for(const witness of before.collisions[kind]||[])for(const value of Object.values(witness)) {
          if(typeof value!=='string')continue;
          const id=value.startsWith('edgeLabel:')?value.slice(10):value;
          if(id.includes('=>')&&!severity.has(id))severity.set(id,rank);
        }
      }
      const trials=[...proposal.routes].sort((a,b)=>(severity.get(a.id)??9)-(severity.get(b.id)??9)||(a.id<b.id?-1:a.id>b.id?1:0));
      for(const route of trials.slice(0,12)) {
        const priorCache=view.autoRoutes[route.id],item=install(route);if(!item)continue;actualTrials++;
        const measured=graphItemBounds(item),inside=measured&&routingBounds&&measured.minX>=routingBounds.minX-1e-6
          &&measured.maxX<=routingBounds.maxX+1e-6&&measured.minY>=routingBounds.minY-1e-6&&measured.maxY<=routingBounds.maxY+1e-6;
        const index=trialIndices.get(route.id),previous=index!==undefined?trialScene.qualityEdges[index]:null;
        const changed=inside&&previous?graphRouteQualityEdge(item,trialUnproject):null;
        if(changed)trialScene.qualityEdges[index]={...changed,path:changed.path.map(command=>command.slice())};
        const trial=changed?graphActualRouteQuality(trialScene):null;
        if(inside&&graphRouteQualityImproves(acceptedQuality,trial,[route.id])){accepted=true;acceptedQuality=trial;continue;}
        if(previous)trialScene.qualityEdges[index]=previous;
        if(priorCache)view.autoRoutes[route.id]=priorCache;else delete view.autoRoutes[route.id];
        graph.updateItem(item,oldRoutes.get(item));syncGraphEdgeLabel(item);
      }
      rebuildGraphViewport();applyGraphLabels(false);
      const final=graphActualRouteQuality();
      if(!accepted||!graphRouteQualityImproves(before,final,changedRouteKeys())){restore();accepted=false;acceptedQuality=before;}else acceptedQuality=final;
    }
    view.routeQuality={before,after:acceptedQuality,proposed:accepted?null:after,planner:proposal.stats,accepted,actualTrials,
      reason:accepted?'actual-curve-improvement':readingPreserved?'actual-quality-retained':'reading-size-retained'};
  } catch(_) {restore();view.routeQuality={before,after:before,accepted:false,reason:'route-recovery'};}
}
function refreshGraphRoutesAndLabels(items=state.graph?.getEdges?.()||[],{labels=true}={}) {
  const graph=state.graph;if(!graph)return;
  const positions=new Map((graph.getNodes?.()||[]).map(item=>[item.getModel().id,item.getModel()]));
  const simple=graphHasSimpleGeometry(positions);
  if(simple!==state.graphView.routeSimple)items=graph.getEdges?.()||items;
  state.graphView.routeSimple=simple;
  for(const item of items) {
    const model=item.getModel(),route=graphEdgeRoute(model,positions,simple);
    if(model.type!==route.type||model.curvePolicy!==route.curvePolicy
      ||JSON.stringify(model.controlPoints||[])!==JSON.stringify(route.controlPoints))graph.updateItem(item,route);
    rememberGraphAutomaticRoute(model,positions,simple);
    if(labels)syncGraphEdgeLabel(item);
  }
  refreshSelectedEdgeAppearance();
}
function rememberGraphAutomaticRoute(model,positions,simple) {
  if(model.curvePolicy!=='compact-arc'||model.type!=='quadratic'||model.controlPoints?.length!==1)return;
  const source=positions.get(model.source),target=positions.get(model.target);if(!source||!target)return;
  const key=edgeKey(model);if(Object.prototype.hasOwnProperty.call(state.edgeTypes,key)||state.edgeBendPos[key]?.length)return;
  state.graphView.autoRoutes[key]={source:{x:source.x,y:source.y},target:{x:target.x,y:target.y},
    controlPoints:model.controlPoints.map(p=>({x:p.x,y:p.y})),curvePolicy:'compact-arc'};
}
function syncGraphEdgeLabel(item,outline,anchorOverride) {
  if(typeof FundsGraphLabelGeometry==='undefined'||!item?.getKeyShape)return;
  const model=item.getModel(),key=edgeKey(model),view=state.graphView;
  const saved=state.edgeSnapshotLabels[key];
  if(state.edgeLabelPos[key]==null&&saved?.manual!==false&&Number.isFinite(saved?.lx)&&Number.isFinite(saved?.ly)) {
    const path=edgePts(item);
    if(path)state.edgeLabelPos[key]=nearestRatio(path,saved.lx,saved.ly);
  }
  const ratio=state.edgeLabelPos[key],manual=Number.isFinite(ratio);
  const path=manual?edgePts(item):null;
  const shape=item.getKeyShape(),anchor=anchorOverride||(path?ptAtRatio(path,ratio):shape?.getPoint?.(.5));
  const a=shape?.getPoint?.(.48),b=shape?.getPoint?.(.52);
  const horizontal=a&&b&&Math.abs(b.y-a.y)<=Math.abs(b.x-a.x)*.12;
  const selected=key===view.selectedEdge&&!view.exportDepth;
  const style=graphRelationStyle(model,view.mutedEdges.has(key));
  if(selected){style.stroke='#66d9ef';if(style.endArrow)style.endArrow={...style.endArrow,fill:'#66d9ef'};}
  outline=outline||(item.getContainer?.()?.get?.('children')||[]).find(s=>s.get('name')==='funds-edge-selection-outline');
  const result=FundsGraphLabelGeometry.placeEdge(item,{anchor,above:!manual&&graphHasSimpleGeometry()&&horizontal,
    gap:4,style,outline,visible:model.label!==undefined});
  outline?.toBack?.();
  model.labelPlacement=result.mode==='above'?'above':'inline';
}
function syncGraphEdgeLabels() {
  for(const item of state.graph?.getEdges?.()||[])syncGraphEdgeLabel(item);
}
function syncEdgeTypeControl() {
  const control=$('#edgeTypeSel');if(!control)return;
  const edge=state.graphView.selectedEdge, model=_selItem?.getModel?.();
  control.disabled=!edge || model?.source===model?.target || state.graphLoading || !!window.FundsCanvasEditor?.history.status().busy || !!layoutJob;
  control.title=edge?'仅修改所选关系线型':'请先选择一条关系';
  control.value=edge?(state.edgeTypes[edge]||state.edgeType):state.edgeType;
}

function findEdgeShapes(edge) {
  const kids = edge.getContainer().get('children') || [];
  return {
    text: kids.find(c => c.get('name') === 'text-shape' || c.get('className') === 'edge-label'),
    bg: kids.find(c => c.get('name') === 'text-bg-shape'),
  };
}

function shapeTx(sh) {
  const m = sh.attr('matrix');
  return m ? { x: m[6], y: m[7] } : { x: sh.attr('x') || 0, y: sh.attr('y') || 0 };
}

function setShapeTx(sh, x, y) {
  sh.set('matrix', [1, 0, 0, 0, 1, 0, x, y, 1]);
}

// 把任意线型（曲线/直线/折线）的边路径统一折算为密集折点 + 累计弧长，
// 标签拖拽按弧长比例定位，三种线型通用
function edgePts(edge) {
  const path = edge.getKeyShape().attr('path');
  if (!path || !path.length) return null;
  const pts = [];
  let cur = null;
  for (const s of path) {
    if (s[0] === 'M' || s[0] === 'L') {
      cur = { x: s[1], y: s[2] };
      pts.push(cur);
    } else if (s[0] === 'Q') {
      if (!cur) return null;
      for (let i = 1; i <= 24; i++) {
        const u = i / 24, v = 1 - u;
        pts.push({ x: v * v * cur.x + 2 * v * u * s[1] + u * u * s[3],
                   y: v * v * cur.y + 2 * v * u * s[2] + u * u * s[4] });
      }
      cur = { x: s[3], y: s[4] };
    } else if (s[0] === 'C') {
      if (!cur) return null;
      for (let i = 1; i <= 24; i++) {
        const u = i / 24, v = 1 - u;
        pts.push({ x: v * v * v * cur.x + 3 * v * v * u * s[1] + 3 * v * u * u * s[3] + u * u * u * s[5],
                   y: v * v * v * cur.y + 3 * v * v * u * s[2] + 3 * v * u * u * s[4] + u * u * u * s[6] });
      }
      cur = { x: s[5], y: s[6] };
    }
    // A（弧）/Z（闭合）：直线与折线用不到，跳过
  }
  if (pts.length < 2) return null;
  const acc = [0];
  let total = 0;
  for (let i = 1; i < pts.length; i++) {
    total += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
    acc.push(total);
  }
  if (total <= 0) return null;
  return { pts, acc, total };
}

// 弧长比例 r∈[0,1] → 路径上的点
function ptAtRatio(P, r) {
  const target = Math.max(0, Math.min(1, r)) * P.total;
  for (let i = 1; i < P.acc.length; i++) {
    if (P.acc[i] >= target) {
      const segLen = (P.acc[i] - P.acc[i - 1]) || 1;
      const u = (target - P.acc[i - 1]) / segLen;
      return { x: P.pts[i - 1].x + (P.pts[i].x - P.pts[i - 1].x) * u,
               y: P.pts[i - 1].y + (P.pts[i].y - P.pts[i - 1].y) * u };
    }
  }
  return P.pts[P.pts.length - 1];
}

// 点 (px,py) 在路径上的最近弧长比例
function nearestRatio(P, px, py) {
  let best = 0.5, bd = Infinity;
  for (let i = 1; i < P.pts.length; i++) {
    const ax = P.pts[i - 1].x, ay = P.pts[i - 1].y;
    const dx = P.pts[i].x - ax, dy = P.pts[i].y - ay;
    const L2 = dx * dx + dy * dy || 1e-6;
    const u = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / L2));
    const qx = ax + dx * u, qy = ay + dy * u;
    const d = (qx - px) * (qx - px) + (qy - py) * (qy - py);
    if (d < bd) {
      bd = d;
      best = (P.acc[i - 1] + Math.sqrt(L2) * u) / P.total;
    }
  }
  return best;
}

function isLabelShape(target) {
  if (!target || !target.get) return false;
  return target.get('name') === 'text-shape' || target.get('name') === 'text-bg-shape'
      || target.get('className') === 'edge-label';
}

// 手动标签沿完整逻辑路径定位；可见线及选中外圈同步留缝。
function placeEdgeLabel(edge, pt) {
  resetAutoLabelOffset(edge);
  syncGraphEdgeLabel(edge,undefined,pt);
}

// 渲染后恢复用户拖过的标签位置（按弧长比例，任意线型通用）
function restoreEdgeLabelPositions(edges = state.graph.getEdges()) {
  for (const edge of edges) {
    const t = state.edgeLabelPos[edgeKey(edge.getModel())];
    if (t == null) {
      const saved = state.edgeSnapshotLabels[edgeKey(edge.getModel())];
      if (saved?.manual!==false && saved?.lx != null && saved?.ly != null) placeEdgeLabel(edge, {x:saved.lx,y:saved.ly});
      continue;
    }
    const P = edgePts(edge);
    if (!P) continue;
    const point=ptAtRatio(P,t),key=edgeKey(edge.getModel());
    placeEdgeLabel(edge,point);
    // A lazy label may have no text shape. Keep its saved coordinates current
    // as well, so a full save never serializes the old layout's absolute point.
    state.edgeSnapshotLabels[key]={...state.edgeSnapshotLabels[key],lx:point.x,ly:point.y,manual:true};
  }
}
function captureEdgeLabelRatios() {
  const items=state.graphView.edgeItems;
  for(const [key,saved] of Object.entries(state.edgeSnapshotLabels)) {
    if(state.edgeLabelPos[key]!=null || saved.manual===false || saved.lx==null || saved.ly==null) continue;
    const item=items.get(key);if(!item)continue;
    const path=edgePts(item);if(path) state.edgeLabelPos[key]=nearestRatio(path,saved.lx,saved.ly);
  }
}

function initEdgeLabelDrag(graph) {
  let drag = null;  // { edge, P }

  graph.on('edge:mousedown', e => {
    if (!isLabelShape(e.target)) return;
    const P = edgePts(e.item);
    if (!P) return;
    if(!startGraphEdgeGesture(e,graph))return;
    const key=edgeKey(e.item.getModel());
    drag = { edge: e.item, P, key, ratio:state.edgeLabelPos[key],snapshot:state.edgeSnapshotLabels[key],
      context: caseContext(), view: state.graphView, revision: state.graphView.revision };
    e.preventDefault && e.preventDefault();
  });

  // 拖动中用原生事件（鼠标可能离开边线，仍需跟随投影）
  const move = e => {
    if (!drag) return;
    if (!isCurrentGraphGesture(drag, graph)) { drag = null; return; }
    try {
      const p = graph.getPointByClient(e.clientX, e.clientY);
      const r = nearestRatio(drag.P, p.x, p.y);
      state.edgeLabelPos[edgeKey(drag.edge.getModel())] = r;
      placeEdgeLabel(drag.edge, ptAtRatio(drag.P, r));
    } catch (_) { /* binding may have been revoked */ }
  };

  const finish = cancelled => {
    const active=drag;drag=null;
    if(!active||!isCurrentGraphGesture(active,graph))return;
    if(cancelled) {
      if(active.ratio===undefined)delete state.edgeLabelPos[active.key];else state.edgeLabelPos[active.key]=active.ratio;
      if(active.snapshot===undefined)delete state.edgeSnapshotLabels[active.key];else state.edgeSnapshotLabels[active.key]=active.snapshot;
      syncGraphEdgeLabel(active.edge);
    }
    savePositions();rebuildGraphViewport();scheduleGraphLabels();
    window.dispatchEvent(new CustomEvent('funds:edge-drag-end',{detail:{cancelled}}));
  };
  const dispose=bindGraphEdgeGesture(move,finish);
  state.graphDisposers.push(() => { drag = null;dispose(); });

  // 悬停标签时显示可拖动光标
  const canvasEl = graph.get('canvas') && graph.get('canvas').get('canvas');
  graph.on('edge:mousemove', e => {
    if (canvasEl) canvasEl.style.cursor = isLabelShape(e.target) ? 'move' : 'default';
  });
  graph.on('canvas:mousemove', e => {
    if (canvasEl && canvasEl.style.cursor === 'move') canvasEl.style.cursor = 'default';
  });
}

/* ---------------- 画布（多桌布）管理 ---------------- */
function promptText(title, defval) {
  // 页面内输入弹窗（不依赖浏览器原生 prompt——内网安全策略可能拦截原生弹窗）
  return new Promise(resolve => {
    const modal = $('#textInputModal');
    const input = $('#tiInput');
    $('#tiTitle').textContent = title || '输入名称';
    input.value = defval || '';
    modal.style.display = 'flex';
    setTimeout(() => { input.focus(); input.select(); }, 50);
    const done = v => {
      modal.style.display = 'none';
      $('#tiOk').onclick = $('#tiCancel').onclick = input.onkeydown = null;
      resolve(v);
    };
    $('#tiOk').onclick = () => done(input.value.trim());
    $('#tiCancel').onclick = () => done(null);
    input.onkeydown = e => {
      if (e.key === 'Enter') done(input.value.trim());
      else if (e.key === 'Escape') done(null);
    };
  });
}

async function refreshCanvasList(keepId) {
  const context = caseContext();
  const lst = await api(`/api/canvases?case_id=${context.caseId}`);
  if (!isCurrentCase(context)) return;
  const sel = $('#canvasSel');
  const cur = keepId != null ? keepId : (pendingCanvasId || state.canvasId || 0);
  const wanted = pendingCanvasId;
  pendingCanvasId = 0;
  sel.innerHTML = '<option value="0">默认画布</option>' +
    lst.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
  const has = Number(cur) === 0 || lst.some(c => c.id === Number(cur));
  sel.value = has ? String(cur) : '0';
  const resolvedId = Number(sel.value) || 0;
  if (wanted && resolvedId === wanted && resolvedId !== state.canvasId) {
    // ?canvas_id=... 走与用户在下拉里切换完全一致的路径：激活画布、应用快照并重载图谱。
    // 直接 activateCanvas 只会清空图面，不会重新取数。
    setTimeout(() => {
      const node = $('#canvasSel');
      if (!node || String(state.caseId) === '') return;
      node.value = String(wanted);
      node.dispatchEvent(new Event('change'));
    }, 0);
    return;
  }
  if (resolvedId !== state.canvasId) activateCanvas(resolvedId);
  state.canvasName = state.canvasId
    ? (lst.find(c => c.id === state.canvasId) || {}).name || '' : '';
}

function applyCanvasSnapshot(snap) {
  resetGraphView();
  state.presentation = newGraphPresentation(snap.presentation);
  const display = snap.display || {};
  state.layout = ['auto','force','structural','dagre','partition'].includes(display.layout) ? display.layout : 'auto';
  if (['quadratic', 'line', 'polyline'].includes(display.edgeType)) state.edgeType = display.edgeType;
  if (Array.isArray(display.edgeLabels)) state.edgeLabels = new Set(display.edgeLabels.filter(key => ['money','time','summary','currency'].includes(key)));
  if (typeof display.showIds === 'boolean') state.showIds = display.showIds;
  if (typeof display.showPhotos === 'boolean') state.showPhotos = display.showPhotos;
  if (['auto', 'focus', 'all'].includes(display.labelMode)) state.graphView.labelMode = display.labelMode;
  const context = caseContext();
  // 快照与导出 HTML 同构：nodes[{id,x,y,label,size,style,img?}] / edges[{source,target,type,controlPoints,label,lx,ly,bw,bh}]
  state.positions = {};
  for (const n of (snap.nodes || [])) state.positions[n.id] = { x: n.x, y: n.y };
  state.edgeLabelPos = {};
  state.edgeBendPos = {}; state.edgeTypes = {};
  state.edgeSnapshotLabels = {};
  for (const e of (snap.edges || [])) {
    const k = e.source + '=>' + e.target;
    if (Number.isFinite(e.lx) && Number.isFinite(e.ly)) state.edgeSnapshotLabels[k] = { lx:e.lx, ly:e.ly, bw:e.bw, bh:e.bh, manual:e.labelManual!==false };
    if(Number.isFinite(e.labelRatio)&&e.labelRatio>=0&&e.labelRatio<=1)state.edgeLabelPos[k]=e.labelRatio;
    const automatic=e.routeManual===false||e.routeManual!==true&&['screen-right','compact-arc'].includes(e.curvePolicy);
    if(automatic&&['screen-right','compact-arc','obstacle-aware'].includes(e.curvePolicy)&&e.type==='quadratic'&&Array.isArray(e.controlPoints)
      &&e.controlPoints.length===1&&e.controlPoints.every(p=>Number.isFinite(p?.x)&&Number.isFinite(p?.y))) {
      const source=state.positions[e.source],target=state.positions[e.target];
      if(source&&target)state.graphView.autoRoutes[k]={source:{...source},target:{...target},controlPoints:e.controlPoints.map(p=>({x:p.x,y:p.y})),curvePolicy:e.curvePolicy};
    }
    if(!automatic) {
      if (e.controlPoints && e.controlPoints.length) state.edgeBendPos[k] = e.controlPoints.map(p => ({x:p.x,y:p.y}));
      if (['line', 'polyline', 'quadratic', 'loop'].includes(e.type)) state.edgeTypes[k] = e.type;
    }
  }
  state.forceRebuild = true;
  window.FundsCanvasEditor?.syncControls?.();
  renderGraph();
  const graph = state.graph, view = state.graphView, revision = view.revision;
  const layoutRevision=layoutSequence,openZoom=graph?.getZoom?.(),openIntent=state.graphCamera?.getIntent?.();
  const readCamera=()=>{const matrix=graph?.get?.('group')?.getMatrix?.();return matrix?Array.from(matrix):null;};
  const openCamera=JSON.stringify(readCamera());
  // 标注绝对位置恢复（renderGraph 之后放置）
  setTimeout(() => {
    if (!isCurrentCase(context) || graph !== state.graph || view !== state.graphView || revision !== view.revision) return;
    // A delayed initial fit owns only the unchanged opening view. User camera
    // actions, later layouts and the image sampler outrank this old callback.
    if(view.exportDepth||layoutJob||layoutRevision!==layoutSequence||openIntent!==state.graphCamera?.getIntent?.()
      ||graph?.getZoom?.()!==openZoom||JSON.stringify(readCamera())!==openCamera)return;
    const g = state.graph;
    if (!g) return;
    restoreEdgeLabelPositions();
    // Photos are current case records. Saving a canvas never embeds redundant
    // bytes or revives a photo cleared after an older snapshot was written.
    try { fitGraph(1.6); } catch (err) { /* 忽略 */ }
  }, 250);
}

async function loadCanvas(id) {
  if (hasNodeDrafts() && !await leaveNodeDrafts()) return;
  const context = caseContext();
  const r = await api(`/api/canvases/${id}?case_id=${context.caseId}`);
  if (!isCurrentCase(context)) return;
  activateCanvas(r.id);
  const activated = caseContext();
  await loadGraph();
  if (!isCurrentCase(activated)) return;
  applyCanvasSnapshot(r.snapshot);
  state.canvasName = r.name;
  toast(`已调阅画布「${r.name}」（如需继续编辑，记得点「保存」）`);
}

function bindCanvases() {
  let missingCanvasId = null;
  const named = () => !!state.canvasId;
  const ready = () => !!state.caseId && state.canvasId !== missingCanvasId && !layoutJob && !state.graphLoading
    && !window.FundsMainAccountsActions?.blocked(caseContext())
    && !window.FundsCanvasEditor?.history?.status?.().busy
    && !state.graphSelection?.status?.().dragging;
  const view = window.FundsCanvasWorkspace.createView(document, { context: caseContext, readName: () => state.canvasName, isCurrent: isCurrentCase });
  const sync = status => {
    view.onState(status);
    const unavailable = !ready() || status.pending || status.unknown;
    for (const id of ['btnCanvasNew', 'btnCanvasClone', 'btnCanvasSave']) $('#' + id).disabled = unavailable;
    for (const id of ['btnCanvasRename', 'btnCanvasDel', 'btnCanvasMenuClone']) {
      const row = $('#' + id);
      const disabled = unavailable || (id !== 'btnCanvasMenuClone' && !named());
      row.setAttribute('aria-disabled', String(disabled));
      row.title = disabled ? (state.canvasId === missingCanvasId ? '画布已不存在，请在顶栏选择其他画布' : !named() && id !== 'btnCanvasMenuClone' ? '默认画布请使用另存为' : '画布正在处理或结果待核对') : '';
    }
    // Keep the menu trigger usable so an uncertain result can be reconciled.
    $('#btnCanvasActions').setAttribute('aria-busy', 'false');
    $('#canvasSel').disabled = status.pending;
  };
  const actions = window.FundsCanvasWorkspace.create({
    context: caseContext, isCurrent: isCurrentCase, readName: () => state.canvasName,
    ready, canLeave: async context => isCurrentCase(context) && (!hasNodeDrafts() || await leaveNodeDrafts()) && isCurrentCase(context),
    ask: view.ask, collect: () => window.__collectSnapshot({ scope: 'all', embedPhotos: false }),
    request: caseRequest, notify: toast, onState: sync,
    dismiss: view.dismiss, complete: view.complete, failure: view.failure, showStatus: view.showStatus,
    reconciled: async (rows, context) => {
      if (state.caseId !== context.caseId) return;
      const select = $('#canvasSel');
      select.innerHTML = '<option value="0">默认画布</option>' + rows.map(row => `<option value="${row.id}">${esc(row.name)}</option>`).join('');
      if (state.canvasId && !rows.some(row => String(row.id) === String(state.canvasId))) {
        missingCanvasId = state.canvasId;
        const missing = document.createElement('option'); missing.value = String(state.canvasId);
        missing.textContent = '画布已删除，请选择其他画布'; missing.disabled = true;
        select.appendChild(missing);
      }
      select.value = String(state.canvasId);
    },
    publish: async (action, context, result, snapshot) => {
      if (!isCurrentCase(context)) return;
      if (action === 'rename' || action === 'save') {
        state.canvasName = result.name;
        const selected = $('#canvasSel').selectedOptions[0];
        if (selected) selected.textContent = result.name;
        try { await refreshCanvasList(context.canvasId); }
        catch (_) { if (isCurrentCase(context)) toast('画布已保存，列表暂未刷新', 'err'); return; }
        if (isCurrentCase(context)) toast(action === 'rename' ? '画布已重命名' : '画布形态已保存');
        return;
      }
      const id = action === 'delete' ? 0 : result.canvas_id;
      activateCanvas(id);
      const activated = caseContext();
      state.canvasName = action === 'delete' ? '' : result.name;
      try {
        await refreshCanvasList(id);
        if (!isCurrentCase(activated)) return;
        await loadGraph(true, activated);
        if (!isCurrentCase(activated)) return;
        if (action === 'clone') applyCanvasSnapshot(snapshot);
        toast(action === 'delete' ? '画布已删除，原始流水已保留' : action === 'new' ? '空白画布已创建' : '已另存为新画布，源画布已保留');
      } catch (_) {
        if (isCurrentCase(activated)) toast('画布操作已完成，部分数据未刷新，请重新调阅画布', 'err');
      }
    }
  });
  view.attach(actions);
  window.FundsCanvasActions = actions;
  for (const [id, action] of [['btnCanvasNew','new'], ['btnCanvasClone','clone'], ['btnCanvasSave','save'],
    ['btnCanvasRename','rename'], ['btnCanvasDel','delete'], ['btnCanvasMenuClone','clone']]) {
    $('#' + id).onclick = event => actions.run(action, id === 'btnCanvasMenuClone' || id === 'btnCanvasRename' || id === 'btnCanvasDel'
      ? $('#btnCanvasActions') : event?.currentTarget || $('#' + id));
  }
  $('#canvasSel').onchange = async event => {
    const context = caseContext(), v = Number(event.target.value) || 0;
    event.target.value = String(state.canvasId);
    if (v === state.canvasId || actions.status().pending) return;
    if (hasNodeDrafts() && !await leaveNodeDrafts()) return;
    if (!isCurrentCase(context)) return;
    missingCanvasId = null;
    event.target.value = String(v);
    activateCanvas(v);
    state.canvasName = v ? (event.target.selectedOptions[0] || {}).textContent || '' : '';
    const activated = caseContext();
    let snapshot = null;
    try {
      if (v) {
        const result = await caseRequest(`/api/canvases/${v}?case_id=${activated.caseId}`);
        if (!isCurrentCase(activated)) return;
        if (Array.isArray(result.snapshot?.nodes) && Array.isArray(result.snapshot?.edges)) snapshot = result.snapshot;
      }
      if (!isCurrentCase(activated)) return;
      await loadGraph(true, activated);
      if (!isCurrentCase(activated)) return;
      if (snapshot) applyCanvasSnapshot(snapshot);
      await applyPendingFocusEdge();
    } catch (_) { if (isCurrentCase(activated)) toast('画布未能载入，请重新调阅', 'err'); }
    finally { actions.sync(); }
  };
  document.addEventListener('funds:toolbar-menu-change', event => {
    if (event.detail?.open && event.detail.menuId === 'canvasActions') {
      actions.sync();
      if (actions.blocked()) { window.FundsToolbarMenu.close('command'); view.showStatus(actions.status(), $('#btnCanvasActions')); }
    }
  });
  window.addEventListener('funds:graph-metric', () => actions.sync());
  actions.sync();
}

let graphRequestSequence = 0;
let latestGraphRead = null, publishedGraphSequence = 0;
function revokeGraphReads() {
  latestGraphRead = { sequence: ++graphRequestSequence, promise: Promise.resolve(null) };
}
async function readGraphData(retry = true, context = caseContext()) {
  if (!isCurrentCase(context)) return null;
  const read = { sequence: ++graphRequestSequence, promise: null };
  const current = () => isCurrentCase(context) && read.sequence === graphRequestSequence;
  read.promise = (async () => {
    graphMetric('request', { reason: 'load' });
    for (let attempt = 0; ; attempt += 1) {
      try {
        const graph = await api(`/api/graph?case_id=${context.caseId}&canvas_id=${context.canvasId || 0}`);
        return current() ? graph : null;
      } catch (e) {
        if (!current()) return null;
        if (!retry || attempt > 0) throw e;
        // A retry belongs to its original read, never a newer generation.
        await new Promise(r => setTimeout(r, 1500));
        if (!current()) return null;
      }
    }
  })();
  latestGraphRead = read;
  let latest = read, graph = await read.promise;
  // A superseded mutation refresh must wait for the replacement read rather
  // than compensating a committed write against still-stale client state.
  while (isCurrentCase(context) && latest.sequence !== graphRequestSequence) {
    latest = latestGraphRead;
    graph = await latest.promise;
  }
  return isCurrentCase(context) && graph ? { graph, sequence: latest.sequence } : null;
}

async function loadGraph(retry = true, context = caseContext()) {
  let read;
  do { read = await readGraphData(retry, context); }
  while (read && isCurrentCase(context) && read.sequence !== graphRequestSequence);
  if (!read || !isCurrentCase(context) || read.sequence <= publishedGraphSequence) return;
  const g = read.graph;
  const view = state.graphView;
  view.fullIndex = FundsGraphView.createIndex(g);
  if (view.visibleIds) {
    // A neighborhood filters display, not subsequent tracing. Reveal exactly
    // the added/explicitly requested flows, without expanding unrelated facts.
    const added = new Set((g.tracked_ids || []).filter(id => !state.tracked.has(id)));
    for (const id of state.justAdded || []) added.add(id);
    view.visibleIds = new Set([...view.visibleIds].filter(id => view.fullIndex.nodeIds.has(id)));
    for (const edge of g.edges) {
      if (!(edge.ids || []).some(id => added.has(id))) continue;
      for (const id of [edge.source, edge.target]) if (view.fullIndex.nodeIds.has(id)) view.visibleIds.add(id);
    }
    const adjacent = view.direction === 'in' ? view.fullIndex.incoming : view.direction === 'out' ? view.fullIndex.outgoing : view.fullIndex.neighbors;
    view.remaining = [...(adjacent.get(view.root) || [])].filter(id => !view.visibleIds.has(id)).length;
  }
  state.lastGraph = g;
  state.graphLoading = false;
  state.tracked = new Set(g.tracked_ids);
  state.mainAccounts = new Set(g.main_accounts || []);
  state.focusNodes = new Map((g.focus_nodes || []).map(f => [f.account, f.scale || 1.6]));
  renderGraph();
  updateHeader();
  publishedGraphSequence = read.sequence;
}

function nodeStyle(n) {
  const role=n.out_cnt>0&&n.in_cnt>0?'mid':n.out_cnt>0?'src':'sink';
  const fill={src:'#d77d79',mid:'#63b69d',sink:'#7ca6d5'}[role];
  const stroke=n.is_main===1?'#e8c46a':n.is_same_name===1?'#a79be0':{src:'#e7a19a',mid:'#9bd6bf',sink:'#b0c8e5'}[role];
  return {fill,stroke,lineWidth:n.is_main===1?2.8:1.5,shadowColor:'rgba(0,0,0,.22)',shadowBlur:5,
    ...(state.presentation.nodeStyles?.[n.account]||{})};
}
function syncNodeIdentityRings() {
  for(const item of state.graph.getNodes()) {
    const m=item.getModel(),n=m.data||{},group=item.getContainer?.();
    const kind=n.is_main===1?'main':n.is_same_name===1?'same':null;
    let ring=(group?.get?.('children')||[]).find(shape=>shape.get('name')==='funds-identity-ring');
    const custom=state.presentation.nodeStyles?.[m.id];
    let border=(group?.get?.('children')||[]).find(shape=>shape.get('name')==='funds-photo-border');
    if(m.type==='image'&&custom&&(custom.stroke||custom.lineWidth)) {
      const size=Array.isArray(m.size)?Math.max(...m.size):m.size||40;
      const attrs={x:0,y:0,r:size/2,stroke:custom.stroke||nodeRing(n),lineWidth:custom.lineWidth||1.5,fill:null};
      if(border)border.attr(attrs);else group?.addShape?.('circle',{name:'funds-photo-border',capture:false,attrs});
    } else border?.remove?.();
    if(!kind){ring?.remove?.();continue;}
    const size=Array.isArray(m.size)?Math.max(...m.size):m.size||32;
    const attrs={x:0,y:0,r:size/2+2.5,stroke:kind==='main'?'#e8c46a':'#a79be0',lineWidth:1.2,fill:null,shadowBlur:0};
    if(ring)ring.attr(attrs);else ring=group?.addShape?.('circle',{name:'funds-identity-ring',capture:false,attrs});
  }
}

// 节点描边/头像描边色：主账号金 > 同户名蓝 > 角色色
function nodeRing(n) {
  if (n.is_main === 1) return '#e8c46a';
  if (n.is_same_name === 1) return '#a79be0';
  return (n.out_cnt > 0 && n.in_cnt > 0) ? '#63b69d' : (n.out_cnt > 0 ? '#d77d79' : '#7ca6d5');
}

// 有照片且开关打开时，节点用圆形头像替代圆点
function nodePhotoUrl(n) {
  if (!state.showPhotos || !n.photo_at) return null;
  return `/api/account-photos/img?case_id=${state.caseId}&account=${encodeURIComponent(n.account)}`
       + `&ring=${encodeURIComponent(nodeRing(n))}&v=${encodeURIComponent(n.photo_at)}`;
}

function nodeSize(n) {
  const scale=state.focusNodes.get(n.account)||1;
  const base=state.presentation.nodeStyles?.[n.account]?.size ?? (28+Math.min(24,(n.in_cnt+n.out_cnt)*2)+(n.is_main===1?6:n.is_same_name===1?3:0));
  return Math.max(Math.round(base*scale),nodePhotoUrl(n)?Math.round(40*scale):0);
}

function nodeLabel(n) {
  const star = n.is_main === 1 ? '★ ' : (n.is_same_name === 1 ? '◆ ' : '');
  const name = Array.from(n.name || '(未知)').reduce((text,char,i)=>text+(i&&i%14===0?'\n':'')+char,'');
  let t = `${star}${name}\n${n.account || ''}`;   // 户名 / 卡号 分两行，主账号★、同户名◆
  if (state.showIds && n.id_no) t += `\n证件 ${n.id_no}`;   // 身份证号（可在工具栏开关）
  return t;
}

/* ---------- 边标签（多选分行） ---------- */
function fmtEdgeSpan(a, b) {
  if (!a) return '';
  a = String(a); b = String(b || '');
  return a === b ? a : `${a} ~ ${b}`;    // 完整年月日时分秒
}

function edgeLabel(e) {
  const lines = [];
  const s = state.edgeLabels;
  if (s.has('money')) lines.push(`${fmtCount(e.cnt)}笔·${totalLabel(e.amount_totals, e.total, e.currencies?.[0])}`);
  else if (s.has('currency') && e.currencies?.length) lines.push(e.currencies.join(' / '));
  if (s.has('summary')) {
    const summaries=e.summaries || [], full=summaries.slice(0,2).join(' / '), chars=Array.from(full);
    const x=chars.slice(0,42).join('')+(chars.length>42||summaries.length>2?'…':'');
    if (x) lines.push(x);
  }
  if (s.has('time')) {
    const t = fmtEdgeSpan(e.tmin, e.tmax);
    if (t) lines.push(t);
  }
  return lines.join('\n') || `${e.cnt}笔`;
}

function renderGraph() {
  clearGraphLocateFeedback();
  state.graphCamera?.resize();
  state.graphCamera?.cancelComposition?.();
  state.graphView.previewAccounts?.clear();
  syncEdgeTypeControl();
  if (!state.graph) return;
  cancelGraphLayout();
  const selectedIds = new Set(state.graphSelection?.ids || []);
  const selectedEdgeKey = state.graphView.selectedEdge;
  const detail = _selItem && $('#drawer').classList.contains('open') ? {
    node: !_selItem.getModel().source, id: _selItem.getModel().id, key: edgeKey(_selItem.getModel()),
  } : null;
  const camera = state.graph.get?.('group')?.getMatrix?.();
  const savedCamera = camera ? Array.from(camera) : null;
  if (_selItem) closeDrawer(false);
  revokeZoomOptimization();
  restoreGraphClipping();
  const renderContext = caseContext();
  const view = state.graphView;
  const oldNodes=state.graph.getNodes(),oldEdges=state.graph.getEdges();
  const canPreserveLive=state.graphInited&&!!view.index&&!state.forceRebuild;
  clearTimeout(view.labelTimer); view.labelTimer=null;
  if (view.viewportFrame != null) cancelAnimationFrame(view.viewportFrame);
  view.viewportFrame=null;
  // changeData disposes removed items before emitting geometry notifications.
  // Revoke drawing caches first; rebuild them from the replacement items below.
  view.clipIndex=null; view.clipResult=null; view.nodeItems=new Map(); view.edgeItems=new Map(); view.bodyWorld=null;
  view.revision += 1;
  const full = state.lastGraph;
  const scoped = view.visibleIds ? {
    nodes: full.nodes.filter(node => view.visibleIds.has(node.account)),
    edges: full.edges.filter(edge => view.visibleIds.has(edge.source) && view.visibleIds.has(edge.target)),
  } : full;
  const g = typeof FundsGraphPresentation !== 'undefined'
    ? FundsGraphPresentation.displayData(scoped, state.presentation) : scoped;
  view.index = view.visibleIds || graphViewIsFiltered() ? FundsGraphView.createIndex(g)
    : (view.fullIndex || FundsGraphView.createIndex(g));
  view.amountWidths = typeof FundsGraphRelations !== 'undefined' && state.presentation.amountWidth.enabled
    ? FundsGraphRelations.encodeWidths(full.edges, state.presentation.amountWidth.currency) : null;
  const pos = state.positions;
  const hasPosition=id=>Number.isFinite(pos[id]?.x)&&Number.isFinite(pos[id]?.y);
  const oldIds=new Set(oldNodes.map(node=>node.getModel().id));
  const trulyNew=g.nodes.some(n=>!hasPosition(n.account)&&!oldIds.has(n.account));
  // Only an append takes the live drag geometry as its authority. Ordinary
  // renders must apply explicit align/distribute/history/snapshot positions.
  if(trulyNew&&canPreserveLive)for(const node of oldNodes){const m=node.getModel();
    if(Number.isFinite(m.x)&&Number.isFinite(m.y))pos[m.id]={x:m.x,y:m.y};}
  const hasSavedPositions=g.nodes.some(n=>hasPosition(n.account));
  const W = $('#graphContainer').clientWidth || 900, H = $('#graphContainer').clientHeight || 600;
  const nodes = g.nodes.map(n => {
    const p = pos[n.account];
    const focusScale = state.focusNodes.get(n.account) || 1;
    const isFocus = focusScale > 1;
    const nd = {
      id: n.account, fundsLabel: nodeLabel(n), data: n,
      x: p ? p.x : null, y: p ? p.y : null,
      size: nodeSize(n),
      style: nodeStyle(n),
      labelCfg: {style: effectiveGraphLabelStyle('node',n.account)},
    };
    const purl = nodePhotoUrl(n);
    if (purl) {          // 有照片：圆形头像替代圆点（描边色含在头像图里，
                         // 不能带圆点的 fill/stroke/shadow 样式，否则 G6 会在图下画出色块）
      nd.type = 'image';
      nd.img = purl;
      nd.size = Math.max(nd.size, isFocus ? Math.round(40 * focusScale) : 40);
      nd.style = {};
    }
    return nd;
  });
  const byId = new Map(nodes.map(n => [n.id, n]));
  const additions=nodes.filter(n=>!Number.isFinite(n.x)||!Number.isFinite(n.y));
  if(additions.length&&additions.length<nodes.length&&FundsGraphLayout.placeIncremental) {
    const inverse=graphUnprojector(),obstacles=[];
    const externalOnly=additions.length>32||nodes.length>500||g.edges.length>800||oldEdges.length>800;
    // Hidden accounts keep their saved slots so restoring a filter cannot
    // reveal a new account on top of an old one.
    for(const n of full.nodes)if(!byId.has(n.account)&&hasPosition(n.account)){
      const p=pos[n.account],d=graphLayoutNode({id:n.account,data:n,fundsLabel:nodeLabel(n),size:nodePhotoUrl(n)?Math.max(40,nodeSize(n)):nodeSize(n)});
      obstacles.push({bounds:{minX:p.x-d.width/2,maxX:p.x+d.width/2,minY:p.y+d.offsetY-d.height/2,maxY:p.y+d.offsetY+d.height/2}});
    }
    for(const item of externalOnly?oldNodes:[...oldNodes,...oldEdges]) {
      // Actual displaced text and the complete curved/manual route are
      // obstacles too; a source-target chord misses a right-bowing branch.
      const label=graphLabelBounds(item,item.getModel().source?'edge':'node');
      if(label&&inverse){const a=inverse(label.minX,label.minY),b=inverse(label.maxX,label.maxY);
        obstacles.push({bounds:{minX:Math.min(a.x,b.x),minY:Math.min(a.y,b.y),maxX:Math.max(a.x,b.x),maxY:Math.max(a.y,b.y)}});}
    }
    for(const item of oldEdges){try{
      if(externalOnly){const bounds=graphItemBounds(item,inverse);if(bounds)obstacles.push({bounds});}
      else {const points=edgePts(item)?.pts;if(points)obstacles.push({points});}
    }catch(_){/* Incomplete host adapter. */}}
    const placed=FundsGraphLayout.placeIncremental({width:W,height:H,anchorId:view.selectedAccount||view.root,
      externalOnly,obstacles,
      nodes:nodes.map(n=>graphLayoutNode(n,Number.isFinite(n.x)&&Number.isFinite(n.y))),
      edges:g.edges.map(e=>{const label=graphTextSize(edgeLabel(e),effectiveGraphLabelStyle('edge',edgeKey(e)));
        return {source:e.source,target:e.target,labelWidth:label.width,labelHeight:label.height};})});
    const fresh=new Set(additions.map(n=>n.id));
    for(const p of placed.nodes){const n=byId.get(p.id);if(n&&fresh.has(p.id)){
      n.x=p.x;n.y=p.y;pos[p.id]={x:p.x,y:p.y};}}
    view.recentNodeIds=fresh;
  }
  for (const nd of nodes) {
    if (Number.isFinite(nd.x)&&Number.isFinite(nd.y)) continue;
    Object.assign(nd,FundsGraphLayout.seed(nd.id,W,H));
  }
  view.simpleGraph=FundsGraphLabelGeometry.isSimpleGraph(full);
  const simpleRoutes=graphHasSimpleGeometry(byId);
  view.routeSimple=simpleRoutes;
  const dense=g.edges.length>1000 && view.labelMode!=='all';
  const edges = g.edges.map(e => {
    const bk = edgeKey({ source: e.source, target: e.target });
    const route=graphEdgeRoute(e,byId,simpleRoutes);
    return {
      source: e.source, target: e.target, data: e,
      // 直线模式下被拖出过折点的边按折线渲染；其余随全局线型
      ...route,
      fundsLabel: edgeLabel(e),
      style: graphRelationStyle(e,dense),
      labelCfg: {style:effectiveGraphLabelStyle('edge',bk)},

    };
  });
  for(const edge of edges)rememberGraphAutomaticRoute(edge,byId,simpleRoutes);
  const initialLabels = FundsGraphView.labels(view.index, {zoom:0.5,mode:view.labelMode,
    selectedIds,selectedAccount:view.selectedAccount,hoveredAccount:view.hoveredAccount,selectedEdge:view.selectedEdge});
  for (const n of nodes) n.label=initialLabels.nodeIds.has(n.id)?n.fundsLabel:undefined;
  for (const e of edges) e.label=initialLabels.edgeKeys.has(edgeKey(e))?e.fundsLabel:undefined;
  view.labeledNodes=new Set(initialLabels.nodeIds);view.labeledEdges=new Set(initialLabels.edgeKeys);
  view.mutedEdges=dense?new Set(edges.map(edgeKey)):new Set();view.edgeDensityMode=dense;view.focusedEdges=new Set();
  let graph = state.graph;
  const needFit = state.forceFit;
  const needsFirstLayout = nodes.length > 1 && !hasSavedPositions && (graph.getNodes().length === 0||state.forceRebuild);
  if (!state.graphInited) {
    graph.data({ nodes, edges });
    graph.render();
    state.graphInited = true;
    state.forceFit = false;
  } else if (state.forceRebuild) {
    // 照片/重点展示/画布切换等变更：销毁旧 Graph 重建——G6 的 clear() 会在 canvas
    // 上留残影（节点重影掉帧），彻底销毁实例是唯一可靠清理方式
    state.forceRebuild = false;
    initGraph();
    graph = state.graph;
    state.graph.data({ nodes, edges });
    state.graph.render();
    state.graphInited = true;
    restoreEdgeLabelPositions();
    if (needFit) fitGraph(1.6);
  } else {
    // 关键：changeData 会异步重启布局模拟把节点拉回布局位，
    // 必须先停掉布局，再按 positions 恢复每个节点的位置
    graph.setAutoPaint(false);
    graph.changeData({ nodes, edges });
    try { graph.stopLayout(); } catch (err) { /* 忽略 */ }
    graph.getNodes().forEach(n => {
      const id = n.getModel().id;
      const upd = {};
      // 1) 恢复拖拽保存的位置
      const p = state.positions[id];
      if (p) {
        const m = n.getModel();
        upd.labelCfg = {style:effectiveGraphLabelStyle('node',id)};
        if (Math.abs(m.x - p.x) > 0.5 || Math.abs(m.y - p.y) > 0.5) upd.x = p.x, upd.y = p.y;
      }
      // 2) 强制同步主账号的标签与样式——G6 的 changeData 不会更新已有节点的这两项
      const nm = byId.get(id)?.data;
      if (nm) {
        const wantImg = nodePhotoUrl(nm);
        const wantLabel = nodeLabel(nm);
        const wantStyle = wantImg ? {} : nodeStyle(nm);   // 照片节点不能带圆点样式（防色块）
        const fs = nodeSize(nm);
        const m = n.getModel();
        if (m.size !== fs) upd.size = fs;   // 重点展示尺寸同步
        if (m.fundsLabel !== wantLabel || JSON.stringify(m.style) !== JSON.stringify(wantStyle)) {
          upd.fundsLabel = wantLabel;
          if (m.label !== undefined) upd.label = wantLabel;
          upd.style = wantStyle;
        }
        // 3) 照片节点与圆点互切（上传/清除/开关后 type 与 img 需同步）
        const wantType = wantImg ? 'image' : 'circle';
        if (m.type !== wantType) {
          upd.type = wantType;
          if (wantImg) upd.img = wantImg;
        } else if (wantImg && m.img !== wantImg) {
          upd.img = wantImg;
        }
      }
      if (Object.keys(upd).length) {
        try { graph.updateItem(n, upd); } catch (err) { /* 忽略 */ }
      }
    });
    restoreEdgeLabelPositions();
    // 恢复折线折点
    for (const edge of graph.getEdges()) {
      const pts = state.edgeBendPos[edgeKey(edge.getModel())];
      if (pts) {
        try { graph.updateItem(edge, { controlPoints: pts }); } catch (err) { /* 忽略 */ }
      }
    }
  }
  if (savedCamera && !needFit) graph.get?.('group')?.setMatrix?.(savedCamera);
  syncNodeIdentityRings();
  if (!state.graphSelection) initGraphSelection(graph);
  state.graphSelection?.rebind(selectedIds);
  rebuildGraphViewport();
  if (selectedEdgeKey && !selectedIds.size) {
    const item = view.edgeItems.get(selectedEdgeKey);
    if (item && !item.destroyed) { selectItem(item); refreshSelectedEdgeAppearance(); }
  }
  applyGraphLabels(false);
  graph.setAutoPaint(true);
  graph.paint();
  graphMetric('paint');
  if (needsFirstLayout) void relayout(true);
  // Recent additions are feedback, never user selection or animation authority.
  if (state.justAdded && state.justAdded.size) {
    const added = state.justAdded;
    state.justAdded = null;
    const involved = new Set();
    for (const e of g.edges) if ((e.ids || []).some(x => added.has(x))) { involved.add(e.source); involved.add(e.target); }
    const recentRevision=view.revision;
    setTimeout(() => {
      if (!isCurrentCase(renderContext) || graph !== state.graph || view!==state.graphView || recentRevision!==view.revision) return;
      view.pendingRecent=involved;
      applyPendingGraphRecent(view,graph);
    }, 80);
  }
  if (detail) {
    const item = detail.node ? graph.findById(detail.id) : state.graphView.edgeItems.get(detail.key);
    if (item && (!detail.node || (state.graphSelection?.ids.size === 1 && state.graphSelection.ids.has(detail.id)))) {
      if (detail.node) void showNodeDrawer(detail.id, item);
      else void showEdgeDrawer(item.getModel(), item);
    }
  }
  state.forceFit = false;
  const fitRevision = view.revision, fitIntent = state.graphCamera?.getIntent?.();
  setTimeout(() => {
    if (!isCurrentCase(renderContext) || graph !== state.graph || view !== state.graphView || fitRevision !== view.revision
      || fitIntent !== state.graphCamera?.getIntent?.() || layoutJob) return;
    savePositions(); if (needFit) fitGraph();
  }, 0);
}

function applyPendingGraphRecent(view,graph) {
  if(view.exportDepth || !view.pendingRecent)return;
  const involved=view.pendingRecent;view.pendingRecent=null;
  graph.setAutoPaint(false);
  for(const node of graph.getNodes())graph.setItemState(node,'recent',involved.has(node.getModel().id));
  graph.setAutoPaint(true);graph.paint();
}

function fitGraph(cap = 1.6,{rebuild=false}={}) {
  state.graphCamera?.resize();
  state.graphCamera?.cancelComposition?.();
  const graph=state.graph;
  const autoPaint=graph?.get?.('autoPaint')!==false;
  try {
    restoreGraphClipping();
    const nodes=graph.getNodes(), container=$('#graphContainer'),view=state.graphView;
    graph.setAutoPaint(false);
    // At the final Fit baseline labels() always receives normalized zoom .5.
    // Establish that same set before measuring, independent of entry LOD or
    // the old viewport's clipped membership. Otherwise a second Fit can shrink
    // the envelope again after long peripheral labels were removed by LOD.
    view.fitZoom=graph.getZoom();view.clipResult=null;
    applyGraphLabels(false);
    const width=container.clientWidth||900,height=container.clientHeight||600;
    const items=[...nodes,...graph.getEdges()],maxZoom=Math.min(4,cap),obstacles=graphChromeObstacles();
    for(const item of items)resetAutoLabelOffset(item);
    let measuredZoom=null,measuredBounds=null;
    const measure=()=>{
      const zoom=graph.getZoom?.()||1;
      if(zoom===measuredZoom)return measuredBounds;
      for(const item of nodes) FundsGraphLabelGeometry.placeNode(item,{zoom:graph.getZoom?.()||1});
      const inverse=graphUnprojector();let bounds=null;
      for(const item of items) {
        const box=graphItemBounds(item,inverse);if(!box)continue;
        if(!bounds)bounds={...box};
        else {bounds.minX=Math.min(bounds.minX,box.minX);bounds.minY=Math.min(bounds.minY,box.minY);
          bounds.maxX=Math.max(bounds.maxX,box.maxX);bounds.maxY=Math.max(bounds.maxY,box.maxY);}
      }
      measuredZoom=zoom;measuredBounds=bounds;return bounds;
    };
    const cameraFit=bounds=>bounds&&FundsGraphLayout.fitViewport({bounds,width,height,obstacles,maxZoom,minZoom:.02,padding:24});
    let bounds,fit;
    if(graph.getCanvasByPoint&&graph.translate&&graph.zoomTo) {
      // A fixed screen gap changes its world footprint at each zoom. Start
      // from the same scale and settle the measured envelope in this command,
      // so repeated Fit cannot creep according to the previous camera.
      graph.zoomTo(maxZoom);
      for(let pass=0;pass<8;pass++) {
        bounds=measure();fit=cameraFit(bounds);if(!fit)break;
        if(Math.abs(graph.getZoom()-fit.zoom)<1e-7)break;
        graph.zoomTo(fit.zoom);
      }
      bounds=measure();fit=cameraFit(bounds);
    }
    if(fit) {
      const p=graph.getCanvasByPoint((bounds.minX+bounds.maxX)/2,(bounds.minY+bounds.maxY)/2);
      graph.translate(fit.center.x-p.x,fit.center.y-p.y);
    } else {
      graph.fitView([24,24,24,24]);
      if(graph.getZoom()>cap)graph.zoomTo(cap,{x:width/2,y:height/2});
    }
    state.graphCamera?.rebase();
    state.graphView.fitZoom = graph.getZoom();
    state.graphView.cameraFit=fit?{fits:fit.fits,zoom:graph.getZoom(),width,height}:null;
    if(rebuild)rebuildGraphViewport();
    applyGraphViewport();
    applyGraphLabels(false);
  } catch (err) { /* A disposed or incomplete host adapter cannot change data. */ }
  finally { if(graph&&!graph.destroyed){graph.setAutoPaint?.(autoPaint);if(autoPaint)graph.paint?.();} }
}

async function locateGraphAdditions() {
  const graph = state.graph, items = [...state.graphView.recentNodeIds || []]
    .filter(id => !state.graphView.index || state.graphView.index.nodeIds.has(id)).map(id => graph?.findById(id)).filter(Boolean);
  if (!items.length) return false;
  const result = await focusGraphItems(items, { preferredZoom: Math.max(.85, graph.getZoom?.() || 1) });
  return result.ok;
}

/* Layout preparation never mutates the live G6 graph. Only the synchronous
 * commit owns geometry/history; a computing job does not own the camera. */
let layoutSequence = 0, layoutJob = null, layoutMeasurementRevision = 0;
document.fonts?.addEventListener?.('loadingdone',invalidateGraphLayoutMeasurements);
document.fonts?.addEventListener?.('loadingerror',invalidateGraphLayoutMeasurements);
function graphCameraInteractionAllowed() {
  return !!state.graph&&!state.graphLoading&&!state.graphView.exportDepth
    && (!layoutJob||layoutJob.phase==='computing')
    && (!window.FundsCanvasEditor?.history.status().busy||layoutJob?.phase==='computing');
}
function invalidateGraphLayoutMeasurements() {
  layoutMeasurementRevision++;
  FundsGraphLabelGeometry.clearTextMeasurements?.();
  layoutJob?.invalidate?.();
}
function releaseIdleGraphLayoutWorker(view=state.graphView) {
  const worker=view?.idleLayoutWorker;if(!worker)return;
  view.idleLayoutWorker=null;worker.onmessage=null;worker.onerror=null;worker.onmessageerror=null;worker.terminate();
}
function graphBackgroundRoutingWorthwhile(input,candidateKind=input.kind) {
  // Learn from this view's actual synchronous planner, not node count alone.
  // Incomplete real baselines never reached planning: do not pre-plan them
  // on every repeat. A changed scene still crosses the fresh real gate.
  // Force moves its baseline on each run: pre-planning that next curve scene
  // repeatedly wastes work when the fresh real quality gate is incomplete.
  return candidateKind!=='force'&&(input.nodes.length>48||input.edges.length>128)
    && (state.graphView.routeWork?.[input.kind]?.planningMs||0)>=50;
}
// This is a proposal scene, not a substitute for real G6 paths/glyphs. Keep the
// old complete graph untouched while the Worker plans at candidate coordinates.
function graphLayoutRouteInput(input,candidate,fit) {
  if(state.lastGraph.edges.length>1500||state.lastGraph.nodes.length>3000)return null;
  const points=new Map(candidate.result.nodes.map(n=>[n.id,n])),dimensions=new Map(input.nodes.map(n=>[n.id,n]));
  const zoom=fit?Math.max(.02,Math.min(1.6,candidate.quality.fit.zoom)):Math.abs(state.graph.getZoom?.()||1);
  const oldZoom=Math.abs(state.graph.getZoom?.()||1);
  const measured=new Map(graphRouteScene(undefined,{planner:false,quality:false}).nodes.map(n=>[n.id,n]));
  const nodes=[];
  for(const data of state.lastGraph.nodes) {
    const p=points.get(data.account)||state.positions[data.account];if(!Number.isFinite(p?.x)||!Number.isFinite(p?.y))continue;
    const d=dimensions.get(data.account)||graphLayoutNode({id:data.account,size:nodeSize(data),fundsLabel:nodeLabel(data)});
    const height=Math.max(0,d.height-d.size-(d.gapWorld??6));
    const old=measured.get(data.account),dx=old?p.x-old.x:0,dy=old?p.y-old.y:0;
    const shift=(box,extra=0)=>({minX:box.minX+dx,maxX:box.maxX+dx,minY:box.minY+dy+extra,maxY:box.maxY+dy+extra});
    nodes.push({id:data.account,x:p.x,y:p.y,
      body:old?.body?shift(old.body):{minX:p.x-d.size/2,maxX:p.x+d.size/2,minY:p.y-d.size/2,maxY:p.y+d.size/2},
      label:old?.label?shift(old.label,6/zoom-6/oldZoom):{minX:p.x-d.width/2,maxX:p.x+d.width/2,minY:p.y+d.size/2+6/zoom,maxY:p.y+d.size/2+6/zoom+height}});
  }
  const byId=new Map(nodes.map(n=>[n.id,n])),simple=graphHasSimpleGeometry(byId);
  const edges=[];
  for(const data of state.lastGraph.edges) {
    if(!byId.has(data.source)||!byId.has(data.target))continue;
    const id=edgeKey(data),live=state.graphView.edgeItems.get(id),manual=Object.prototype.hasOwnProperty.call(state.edgeTypes,id)||!!state.edgeBendPos[id]?.length;
    const style=effectiveGraphLabelStyle('edge',id),size=graphTextSize(edgeLabel(data),style);
    // An automatic old route is not the baseline of a fresh layout. Manual
    // bends remain world-space user geometry; do not rebase or simplify them.
    const edge={id,source:data.source,target:data.target,type:graphEdgeType(data),
      controlPoints:manual?(state.edgeBendPos[id]||[]):[],manual:manual||!live,
      labelWidth:size.width,labelHeight:size.height,labelRatio:state.edgeLabelPos[id]};
    if(!manual&&edge.type==='quadratic') {
      const control=FundsGraphLabelGeometry.compactControlPoint(byId.get(data.source),byId.get(data.target));
      if(control)edge.controlPoints=[control];
    }
    const shape=live?.getKeyShape?.(),path=shape?.attr?.('path');
    const unchanged=[data.source,data.target].every(id=>{const a=byId.get(id),b=measured.get(id);return b&&a.x===b.x&&a.y===b.y;});
    // Unchanged manual geometry remains exact, including loops. Automatic
    // routes use the fresh compact recipe even if an old cache is absent.
    if(live&&unchanged&&manual) {
      const sampled=Array.isArray(path)?FundsGraphRouteQuality.sample(path,{steps:24}):null;
      if(sampled?.valid&&!sampled.truncated)edge.sampledPoints=sampled.points;
      const labelPath=Number.isFinite(edge.labelRatio)?edgePts(live):null;
      edge.labelAnchor=labelPath?ptAtRatio(labelPath,edge.labelRatio):shape?.getPoint?.(.5);
    }
    edges.push(edge);
  }
  const boxes=nodes.flatMap(n=>[n.body,n.label]);
  for(const edge of edges) {
    const route=FundsGraphRouting.sampleRoute(edge,byId);
    if(route?.label)boxes.push(route.label);
    if(route?.points)for(const p of route.points)boxes.push({minX:p.x,maxX:p.x,minY:p.y,maxY:p.y});
  }
  const routingBounds=boxes.length?{minX:Math.min(...boxes.map(b=>b.minX)),minY:Math.min(...boxes.map(b=>b.minY)),
    maxX:Math.max(...boxes.map(b=>b.maxX)),maxY:Math.max(...boxes.map(b=>b.maxY))}:null;
  return {nodes,edges,simple,maxChecks:120000,maxEdges:320,routingBounds,conservative:true};
}
function validGraphRouteProposal(proposal,input) {
  if(!proposal||!Array.isArray(proposal.routes)||proposal.routes.length>320||!proposal.stats||typeof proposal.stats!=='object')return false;
  const edges=new Map(input.edges.map(e=>[e.id,e])),seen=new Set();
  return proposal.routes.every(route=>{
    const edge=edges.get(route?.id);
    if(!edge||edge.manual||seen.has(route.id)||route.source!==edge.source||route.target!==edge.target||route.type!=='quadratic'
      ||!Array.isArray(route.controlPoints)||route.controlPoints.length!==1
      ||!route.controlPoints.every(p=>Number.isFinite(p?.x)&&Number.isFinite(p?.y)&&Math.abs(p.x)<=1e12&&Math.abs(p.y)<=1e12))return false;
    seen.add(route.id);return true;
  });
}
function layoutStatus(message = '') {
  const node = $('#graphLayoutStatus');
  if (node) { node.textContent = message; node.hidden = !message; }
  exportWorkspace?.refresh();
}
function cancelGraphLayout({notify=true}={}) {
  cancelGraphImageExport?.();
  layoutSequence += 1;
  if (layoutJob) {
    const job=layoutJob;job.cancelled=true;layoutJob=null;
    clearTimeout(job.timer);clearTimeout(job.deadlineTimer);clearTimeout(job.restartTimer);job.worker?.terminate();job.pendingResolve?.(null);
  }
  layoutStatus();
  if(notify)graphMetric('layout-phase',{layoutPhase:'idle'});
}
async function relayout(fit = true, reserveLabels = false, previousLayout = state.layout, inheritedCamera = null) {
  if (!state.graph) return;
  state.graphCamera?.resize();
  state.graphCamera?.cancelComposition?.();
  cancelGraphLayout({notify:false});
  const graph = state.graph, view = state.graphView, revision = view.revision, context = caseContext(), requestId = layoutSequence;
  const idleWorker=view.idleLayoutWorker;view.idleLayoutWorker=null;
  const job = { requestId, phase:'computing', cancelled: false, worker: idleWorker||null, timer: null, deadlineTimer: null, attempts: [], best: null }; layoutJob = job;
  const owns=()=>!job.cancelled&&layoutJob===job&&isCurrentCase(context)&&state.graph===graph&&state.graphView===view&&view.revision===revision;
  const container = $('#graphContainer');
  const measurementRevision=layoutMeasurementRevision;
  job.invalidate=()=>{
    if(!owns()||job.phase!=='computing')return;
    job.invalidated=true;clearTimeout(job.timer);job.worker?.terminate();job.worker=null;job.pendingResolve?.(null);
    clearTimeout(job.restartTimer);
    // Keep this job/history busy until the latest size/font state is stable.
    // A retired request may release resources, never roll back newer geometry.
    job.restartTimer=setTimeout(()=>{if(owns())void relayout(fit,reserveLabels,previousLayout,cameraBaseline);},60);
  };
  const current = () => {
    if(!owns()){if(layoutJob===job)cancelGraphLayout();return false;}
    if(job.invalidated)return false;
    if(job.input&&(measurementRevision!==layoutMeasurementRevision||job.fontStatus!==document.fonts?.status||job.input.width!==(container.clientWidth||900)
      ||job.input.height!==(container.clientHeight||600)||job.chromeKey!==JSON.stringify(graphChromeObstacles()))) {
      job.invalidate();return false;
    }
    return true;
  };
  const camera = () => { const matrix=graph.get?.('group')?.getMatrix?.(); return matrix?Array.from(matrix):null; };
  const cameraBaseline=inheritedCamera||{matrix:JSON.stringify(camera()),zoom:graph.getZoom?.(),intent:state.graphCamera?.getIntent?.()};
  const openCamera=cameraBaseline.matrix,openZoom=cameraBaseline.zoom;
  const zoom=Math.abs(graph.getZoom?.()||1);
  let input;
  try { input = { width: container.clientWidth || 900, height: container.clientHeight || 600, kind: state.layout, chromeObstacles:graphChromeObstacles(),
    nodes: graph.getNodes().map(node => {
      const m=node.getModel(),bounds=graphLabelBounds(node,'node',false),shape=node.getKeyShape?.(),body=shape?.getBBox?.();
      const modelSize=Array.isArray(m.size)?Math.max(...m.size):m.size||32;
      // G6 canvas bounds include a fixed screen-pixel shadow. Dividing those
      // bounds by the current zoom makes repeated low-zoom layouts inflate.
      // Local bounds retain real photo/body dimensions; effects are reserved
      // at the canonical 1x scale, then measured again on the final canvas.
      const shadow=Math.max(0,Number(shape?.attr?.('shadowBlur'))||0)*2;
      const bodyWidth=body&&Number.isFinite(body.maxX-body.minX)?body.maxX-body.minX+shadow:modelSize;
      const bodyHeight=body&&Number.isFinite(body.maxY-body.minY)?body.maxY-body.minY+shadow:modelSize;
      // Engine geometry uses a canonical world-space footprint, independent
      // of the user's current camera. The evaluator separately solves the
      // fixed 6 CSS-pixel gap at the anticipated fit scale.
      const size=Math.max(modelSize,bodyWidth,bodyHeight),gapWorld=6;
      return {id:m.id,x:m.x,y:m.y,isMain:m.data?.is_main===1,
        size:reserveLabels?Math.max(size,graphLabelLayoutSize(node)):size,
        width:bounds?Math.max(size,(bounds.maxX-bounds.minX)/zoom):size,
        height:bounds?size+gapWorld+(bounds.maxY-bounds.minY)/zoom:size,
        offsetX:0,offsetY:bounds?(gapWorld+(bounds.maxY-bounds.minY)/zoom)/2:0,
        fontSize:effectiveGraphLabelStyle('node',m.id).fontSize,labelGap:bounds?6:0,gapWorld:bounds?gapWorld:0};
    }),
    edges: graph.getEdges().map(edge => {
      const m=edge.getModel(),bounds=graphLabelBounds(edge,'edge',false);
      return {source:m.source,target:m.target,labelWidth:bounds?(bounds.maxX-bounds.minX)/zoom:0,labelHeight:bounds?(bounds.maxY-bounds.minY)/zoom:0,
        fontSize:effectiveGraphLabelStyle('edge',edgeKey(m)).fontSize};
    }) }; }
  catch(_){finish('画布测量未完成，已保留原图。可重新布局后重试。',true);return;}

  job.input=input;job.chromeKey=JSON.stringify(input.chromeObstacles);job.fontStatus=document.fonts?.status;

  if (input.nodes.length < 2) { if (fit) fitGraph();finish();return; }
  layoutStatus(`正在后台布局 ${fmtCount(input.nodes.length)} 个账户，可继续平移、缩放…`);
  graphMetric('layout-phase',{layoutPhase:'computing'});
  // Each engine crosses the same admission boundary. Fit and incremental
  // placement never enter this pipeline, and candidates cannot write facts.
  let kinds;
  try{kinds=FundsGraphLayoutQuality.candidates(input);}
  catch(_){finish('布局准备未完成，已保留原图。可重新布局后重试。',true);return;}
  function finish(message='',failed=false) {
    clearTimeout(job.timer);clearTimeout(job.deadlineTimer);clearTimeout(job.restartTimer);
    const worker=job.worker;
    if(worker&&!failed&&layoutJob===job&&!job.pendingResolve){
      releaseIdleGraphLayoutWorker(view);worker.onmessage=null;
      const retire=()=>{if(view.idleLayoutWorker===worker)releaseIdleGraphLayoutWorker(view);};
      worker.onerror=retire;worker.onmessageerror=retire;view.idleLayoutWorker=worker;
    }else worker?.terminate();
    job.worker=null;job.pendingResolve?.(null);
    if(layoutJob!==job)return;
    layoutJob=null;
    if(failed){state.layout=previousLayout;window.FundsCanvasEditor?.syncControls?.();}
    layoutStatus(message);
    graphMetric('layout',{outcome:failed?'retained':'committed',attempts:job.attempts.length});
  }
  function fail() {
    if(!current())return;
    if(job.best&&!job.applying)return apply(job.best);
    finish('本次布局未完成，已保留原图。可继续操作或重新布局。',true);
  }
  function writePositions(positions) {
    if(graph.getNodes().every(item=>{const m=item.getModel(),p=positions.get(m.id);return m.x===p.x&&m.y===p.y;}))return;
    if(typeof graph.refreshPositions==='function') {
      for(const item of graph.getNodes()){const m=item.getModel(),p=positions.get(m.id);m.x=p.x;m.y=p.y;}
      graph.refreshPositions();
    } else for(const item of graph.getNodes()){const p=positions.get(item.getModel().id);graph.updateItem(item,{x:p.x,y:p.y});}
  }
  function apply(candidate) {
    if(!current())return;
    job.applying=true;job.phase='committing';graphMetric('layout-phase',{layoutPhase:'committing'});
    const valid=FundsGraphLayoutQuality.validate(input,candidate.result);
    if(!valid.valid)return finish('布局结果无效，已保留原图。可重新布局后重试。',true);
    const positions=new Map(candidate.result.nodes.map(node=>[node.id,node]));
    const clone=value=>JSON.parse(JSON.stringify(value));
    const before={nodes:new Map(graph.getNodes().map(item=>{const m=item.getModel();return[m.id,{x:m.x,y:m.y}];})),
      positions:clone(state.positions),ratios:clone(state.edgeLabelPos),labels:clone(state.edgeSnapshotLabels),
      camera:camera(),lastLayout:view.lastLayout,quality:view.layoutQuality,fitZoom:view.fitZoom,
      autoRoutes:clone(view.autoRoutes||{}),routeQuality:view.routeQuality,cameraFit:view.cameraFit};
    const autoPaint=graph.get?.('autoPaint')!==false;
    const ownsCamera=fit&&graph.getZoom?.()===openZoom&&JSON.stringify(camera())===openCamera&&cameraBaseline.intent===state.graphCamera?.getIntent?.();
    try {
      graph.setAutoPaint(false);captureEdgeLabelRatios();view.autoRoutes={...view.autoRoutes};
      for(const item of graph.getEdges())delete view.autoRoutes[edgeKey(item.getModel())];
      writePositions(positions);
      view.bodyWorld=null;
      refreshGraphRoutesAndLabels(undefined,{labels:false});restoreEdgeLabelPositions();
      // A newer user pan/zoom owns the camera even if its initial layout is
      // still computing. An explicit later Fit remains available.
      if(ownsCamera)fitGraph(1.6,{rebuild:true});
      else {rebuildGraphViewport();applyGraphLabels();}
      improveGraphRoutes({fit:ownsCamera,proposal:candidate.proposal});
      savePositions();
      view.lastLayout=candidate.result.layout||candidate.kind;
      let minimumFontPixels=Infinity,visibleLabels=0;
      for(const item of [...graph.getNodes(),...graph.getEdges()])for(const shape of labelShapes(item)) {
        if(shape.get('name')!=='text-shape'||shape.isVisible?.()===false)continue;
        const fontSize=shape.attr('fontSize');
        if(Number.isFinite(fontSize)){visibleLabels++;minimumFontPixels=Math.min(minimumFontPixels,fontSize*Math.abs(graph.getZoom?.()||1));}
      }
      const rendered={zoom:graph.getZoom?.()||1,visibleLabels,minimumFontPixels:Number.isFinite(minimumFontPixels)?minimumFontPixels:null,
        fits:ownsCamera?view.cameraFit?.fits??null:null};
      view.layoutQuality={requested:input.kind,effective:candidate.kind,assessment:candidate.quality,rendered,
        attempts:job.attempts.map(attempt=>({...attempt})),routing:job.routing?{...job.routing}:null,geometry:'measured-footprint-estimate'};
      const warning=rendered.fits===false?'图谱跨度超出当前缩放范围，可平移或聚焦账户阅读。'
        :candidate.quality.overlaps.count?'局部标签空间不足，可放大查看或调整布局。'
        :rendered.minimumFontPixels!=null&&rendered.minimumFontPixels<10?'图谱布局已完成；文字较小时可放大或聚焦账户阅读。':'';
      finish(warning);
    } catch(error) {
      // Rendering is a transaction too. Never save half of a candidate.
      state.positions=before.positions;state.edgeLabelPos=before.ratios;state.edgeSnapshotLabels=before.labels;
      view.lastLayout=before.lastLayout;view.layoutQuality=before.quality;view.fitZoom=before.fitZoom;
      view.autoRoutes=before.autoRoutes;view.routeQuality=before.routeQuality;view.cameraFit=before.cameraFit;
      const restoreCamera=()=>{const group=graph.get?.('group');if(typeof group?.getMatrix==='function')group.setMatrix?.(before.camera||[1,0,0,0,1,0,0,0,1]);};
      try{restoreCamera();}catch(_){/* A disposed host has no camera to restore. */}
      try {writePositions(before.nodes);refreshGraphRoutesAndLabels();restoreEdgeLabelPositions();rebuildGraphViewport();applyGraphLabels();}
      catch(_){/* Model coordinates are restored even if a disposed host cannot redraw. */}
      finally {
        for(const item of graph.getNodes()){const m=item.getModel(),p=before.nodes.get(m.id);if(p){m.x=p.x;m.y=p.y;}}
        state.positions=before.positions;state.edgeLabelPos=before.ratios;state.edgeSnapshotLabels=before.labels;
        try{restoreCamera();}catch(_){/* Keep model recovery independent of host availability. */}
      }
      finish('布局显示未完成，已恢复原图。可重新布局后重试。',true);
    } finally {try{graph.setAutoPaint(autoPaint);if(autoPaint)graph.paint();}catch(_){/* Disposed rendering surfaces do not retain a busy job. */}}
  }
  const validQuality=quality=>quality?.valid===true&&Number.isSafeInteger(quality.overlaps?.count)&&quality.overlaps.count>=0
    && ['exact','sampled'].includes(quality.overlaps.coverage)&&Number.isFinite(quality.fit?.zoom)&&quality.fit.zoom>0
    && Number.isFinite(quality.fit.minimumFontPixels)&&Number.isFinite(quality.fit.targetFontPixels)&&quality.fit.targetFontPixels>0
    && typeof quality.fit.readable==='boolean'&&Number.isFinite(quality.compactness)&&quality.profile?.nodeCount===input.nodes.length;
  let taskSequence=0;
  async function runTask(task,taskInput,valid,attempt) {
    if(!current())return null;
    const taskId=++taskSequence;
    if(typeof Worker!=='undefined') {
      try {
        // One task at a time. A completed view keeps one warm pure-kernel
        // Worker; cancellation/invalid input always retires its active owner.
        const worker=job.worker||new Worker('/static/graph-layout-worker.js?v=compact-arcs-1');job.worker=worker;
        const response=await new Promise(resolve=>{
          let settled=false;
          const stop=value=>{
            if(settled)return;settled=true;clearTimeout(job.timer);
            const retain=!!(value?.result||value?.proposal)&&!value.retry;
            if(!retain){worker.terminate();if(job.worker===worker)job.worker=null;}
            if(job.pendingResolve===stop)job.pendingResolve=null;resolve(value);
          };
          job.pendingResolve=stop;
          const retry=reason=>{if(settled||!current())return;attempt.outcome=reason;stop({retry:true});};
          job.timer=setTimeout(()=>retry('timeout'),8000);
          worker.onmessage=event=>{
            if(settled||!current())return;
            const data=event.data;
            if(data?.protocol!==1||data.requestId!==requestId||data.taskId!==taskId||data.task!==task)return;
            if(data.error)return retry('worker-error');
            if(!valid(data))return retry('invalid-result');
            stop(data);
          };
          worker.onerror=()=>retry('worker-error');worker.onmessageerror=()=>retry('message-error');
          worker.postMessage({protocol:1,requestId,taskId,task,input:taskInput,prepareRoutes:task==='layout'&&graphBackgroundRoutingWorthwhile(input,taskInput.kind)});
        });
        if(!current()||response===null)return null;
        if(!response.retry){attempt.execution='worker';attempt.outcome='valid';return response;}
      } catch(_){clearTimeout(job.timer);job.worker?.terminate();job.worker=null;job.pendingResolve=null;attempt.outcome='worker-unavailable';}
    }
    if(!current())return null;
    attempt.execution='yielding';
    // Worker failure has one same-input recovery. No partly rendered candidate
    // exists during this path, and cancellation is checked before publication.
    const result=task==='layout'?await FundsGraphLayout.run(taskInput,{cancelled:()=>!current()}):null;
    if(!current())return null;
    const response=task==='layout'?{result,quality:FundsGraphLayoutQuality.evaluate(taskInput,result,{fitViewport:FundsGraphLayout.fitViewport})}
      :{proposal:FundsGraphRouting.plan(taskInput)};
    if(!valid(response))throw new Error('Invalid layout task result');
    attempt.outcome='completed';return response;
  }
  async function accept(result,kind,quality) {
    if(!current())return;
    const candidate={result,kind,quality};
    if(!job.best||FundsGraphLayoutQuality.prefer(job.best.quality,quality)===quality)job.best=candidate;
    if(kind===kinds[0]&&kinds.length>1&&(quality.overlaps.count>0||!quality.fit.readable)) {
      void calculate(kinds[1]);return;
    }
    const best=job.best;
    const ownsCamera=fit&&graph.getZoom?.()===openZoom&&JSON.stringify(camera())===openCamera&&cameraBaseline.intent===state.graphCamera?.getIntent?.();
    const routeInput=graphBackgroundRoutingWorthwhile(input,best.kind)?graphLayoutRouteInput(input,best,ownsCamera):null;
    if(routeInput) {
      job.routing={execution:'pending',outcome:'pending'};
      const planned=await runTask('routes',routeInput,data=>validGraphRouteProposal(data.proposal,routeInput),job.routing);
      if(!current()||!planned)return;
      best.proposal=planned.proposal;
    }
    if(current())apply(best);
  }
  async function calculate(kind) {
    if(!current())return;
    const attempt={kind,execution:'pending',outcome:'pending'};job.attempts.push(attempt);
    const candidateInput={...input,kind};
    // Cache only pure computations with every input field, including force's
    // initial coordinates, plus the font-measurement generation. Two entries,
    // at most 1 MiB each; never cache accepted G6 geometry or transaction facts.
    const key=JSON.stringify([measurementRevision,candidateInput]);
    const cache=view.layoutComputations||(view.layoutComputations=new Map());
    try {
      let response;
      if(cache.has(key)) {response=JSON.parse(cache.get(key));attempt.execution='cache';attempt.outcome='valid';}
      else {
        response=await runTask('layout',candidateInput,data=>FundsGraphLayoutQuality.validate(input,data.result).valid&&validQuality(data.quality),attempt);
        if(!current()||!response)return;
        const encoded=JSON.stringify({result:response.result,quality:response.quality});
        if(key.length+encoded.length<=524288){cache.set(key,encoded);while(cache.size>2)cache.delete(cache.keys().next().value);}
      }
      if(!current())return;
      await accept(response.result,kind,response.quality);
    } catch(_){attempt.outcome='error';fail();}
  }
  job.deadlineTimer=setTimeout(()=>{if(current()){for(const attempt of job.attempts)if(attempt.outcome==='pending')attempt.outcome='timeout';fail();}},24000);
  void calculate(kinds[0]);
}

function graphLabelLayoutSize(item) {
  const bounds=graphLabelBounds(item,'node',false),zoom=Math.abs(state.graph.getZoom?.()||1);
  if(!bounds)return item.getModel().size||32;
  return Math.max((bounds.maxX-bounds.minX)/zoom,(bounds.maxY-bounds.minY)/zoom*2+(item.getModel().size||32))+16;
}

function savePositions() {
  if (!state.graph) return;
  state.graph.getNodes().forEach(n => {
    const m = n.getModel();
    if (m.x != null && m.id) state.positions[m.id] = { x: m.x, y: m.y };
  });
  for (const item of state.graph.getEdges?.() || []) cacheEdgeLabel(item);
}
function cacheEdgeLabel(item) {
    const children = item.getContainer?.()?.get?.('children') || [];
    const text = children.find(shape => shape.get('name') === 'text-shape');
    const background = children.find(shape => shape.get('name') === 'text-bg-shape');
    const key=edgeKey(item.getModel()),previous=state.edgeSnapshotLabels[key];
    if (text) state.edgeSnapshotLabels[key] = {
      lx:text.attr('x'), ly:text.attr('y'), bw:background?.attr('width'), bh:background?.attr('height'),
      manual:state.edgeLabelPos[key]!=null || !!(previous?.manual!==false&&previous?.lx!=null),
    };
}

/* ---------------- 右侧抽屉 ---------------- */
let _selItem = null;
let drawerSequence = 0;
let edgeWorkspace = null;
function clearEdgeSelectionAppearance(item) {
  if(!item)return;
  (item.getContainer?.()?.get?.('children')||[]).find(shape=>shape.get('name')==='funds-edge-selection-outline')?.remove?.();
  try{state.graph?.setItemState(item,'selected',false);}catch(_){/* disposed */}
  syncGraphEdgeLabel(item);
}
function refreshSelectedEdgeAppearance() {
  const key=state.graphView.selectedEdge,item=key&&state.graphView.edgeItems.get(key);if(!item)return;
  if(state.graphView.exportDepth){
    (item.getContainer?.()?.get?.('children')||[]).find(shape=>shape.get('name')==='funds-edge-selection-outline')?.hide?.();
    state.graph.setItemState(item,'selected',false);syncGraphEdgeLabel(item);return;
  }
  const model=item.getModel(),base={...graphRelationStyle(model,false),stroke:'#66d9ef'},styles=item.get?.('styles')||{};
  item.set?.('styles',{...styles,selected:{...styles.selected,...base,shadowBlur:0,shadowColor:'transparent',
    'text-shape':{...styles.selected?.['text-shape'],fontWeight:effectiveGraphLabelStyle('edge',key).fontWeight}}});
  const group=item.getContainer?.(),path=item.getKeyShape?.()?.attr?.('path');
  let outline=(group?.get?.('children')||[]).find(shape=>shape.get('name')==='funds-edge-selection-outline');
  if(path) {
    const attrs={path,stroke:'#66d9ef',lineWidth:base.lineWidth+5,opacity:.32,lineCap:'round',lineJoin:'round',fill:null};
    if(outline){outline.attr(attrs);outline.show?.();}else outline=group?.addShape?.('path',{name:'funds-edge-selection-outline',capture:false,attrs});
    outline?.toBack?.();
  }
  try{state.graph.setItemState(item,'selected',true);}catch(_){/* disposed */}
  syncGraphEdgeLabel(item,outline);
}
function selectItem(item) {
  if (_selItem?.getModel?.().source || !state.graphSelection) {
    if (_selItem) clearEdgeSelectionAppearance(_selItem);
  }
  if (state.graphSelection && item) {
    const model = item.getModel();
    if (model.source) state.graphSelection.clear();
    else if (state.graphSelection.ids.size !== 1 || !state.graphSelection.ids.has(model.id)) state.graphSelection.select(model.id);
  }
  _selItem = item || null;
  state.graphView.selectedAccount = !_selItem?.getModel?.().source ? _selItem?.getModel?.().id || null : null;
  state.graphView.selectedEdge = _selItem?.getModel?.().source ? edgeKey(_selItem.getModel()) : null;
  if (_selItem && !state.graphSelection && !_selItem.getModel().source) {
    if (_selItem.getModel().source) refreshSelectedEdgeAppearance();
    try { state.graph.setItemState(_selItem, 'selected', true); } catch (_) { /* disposed */ }
  }
  scheduleGraphLabels(); syncEdgeTypeControl();
  window.FundsRibbonCommands?.sync();
}

function closeDrawer(clearSelection = true, { keepSelection = false } = {}) {
  drawerSequence += 1;
  disposeNodeWorkspace();
  if (clearSelection || keepSelection) for (const draft of nodeDrafts.values()) draft.focusIntent = null;
  disposeEdgeWorkspace();
  $('#drawer').classList.remove('open');
  $('#drawer').inert = true;
  $('#drawer').setAttribute('aria-hidden', 'true');
  $('#drawer').setAttribute('aria-busy', 'false');
  $('#drawerBody').innerHTML = '';
  if (keepSelection) { window.FundsRibbonCommands?.sync(); return; }
  if (clearSelection) selectItem(null);
  else {
    if (_selItem?.getModel?.().source) {
      clearEdgeSelectionAppearance(_selItem);
    }
    _selItem = null; state.graphView.selectedEdge = null;
    syncEdgeTypeControl();
  }
  if (clearSelection) state.graphSelection?.clear();
  window.FundsRibbonCommands?.sync();
}

/* ---------------- 账户资料与照片裁剪 ---------------- */
let nodeWorkspace = null, nodeConfirmation = null;
const nodeDrafts = new Map(), nodeWrites = new Map();
// Unlike drawer-local writes, these leases survive canvas/case revocation.
const accountDataWrites = new Set();
const pcState = { file: null, account: null, item: null, binding: null, objectUrl: null, cx: .5, cy: .5, r: .4 };

function nodeDraftKey(context, account) {
  return JSON.stringify([context.caseId, context.canvasId, context.generation, context.canvasGeneration, account]);
}
function nodeWorkspaceIsCurrent(workspace) {
  return !!workspace && workspace === nodeWorkspace && !workspace.closed
    && workspace.sequence === drawerSequence && isCurrentCase(workspace.context)
    && workspace.view === state.graphView && workspace.revision === state.graphView.revision
    && $('#drawer').classList.contains('open');
}
function captureNodeDraft(workspace = nodeWorkspace) {
  if (!workspace || workspace.closed || !isCurrentCase(workspace.context) || !workspace.draft || workspace !== nodeWorkspace) return;
  const id = $('#ndIdInput'), scale = $('#ndFocusScale');
  if (id) workspace.draft.idValue = id.value;
  if (scale) workspace.draft.focusValue = Number(scale.value) || 1;
}
function rememberNodeFocus(workspace, kind) {
  if (!workspace?.draft) return;
  const target = { id: 'ndIdInput', focus: 'ndFocusScale', main: 'ndToggleMain', 'photo-clear': 'ndPhotoUp', photo: 'ndPhotoUp' }[kind];
  const active = document.activeElement;
  if (target && ($('#drawer').contains(active) || active === document.body || active === document.documentElement)) {
    workspace.focusIntent = workspace.draft.focusIntent = { id: target, node: active };
  }
}
function restoreNodeFocus(workspace) {
  if (!nodeWorkspaceIsCurrent(workspace) || workspace.phase !== 'ready' || (nodeWrites.size > 0) || !workspace.draft?.focusIntent) return;
  const focus = workspace.draft.focusIntent, active = document.activeElement;
  if (!active || active === document.body || active === document.documentElement || active.isConnected === false || active === focus.node) {
    const target = $('#' + focus.id);
    (target && !target.disabled ? target : $('#btnCloseDrawer'))?.focus();
  }
  workspace.draft.focusIntent = null; workspace.focusIntent = null;
}
function nodeDraftDirty(draft) {
  return !!draft && (draft.idValue.trim() !== draft.idBase || draft.focusValue !== draft.focusBase);
}
function closeNodeMenu(restore = false) {
  const menu = nodeWorkspace?.menu;
  if (menu) {
    menu.element.hidden = true; menu.trigger.setAttribute('aria-expanded', 'false');
    if (restore && menu.trigger.isConnected && !menu.trigger.disabled) menu.trigger.focus();
  }
  if (nodeWorkspace) nodeWorkspace.menu = null;
  for (const id of ['#nodeMenu', '#nodePhotoMenu']) { const element = $(id); if (element) element.hidden = true; }
}
function disposeNodeWorkspace() {
  const workspace = nodeWorkspace;
  if (!workspace) return;
  captureNodeDraft(workspace);
  if (workspace.draft && $('#drawer').contains(document.activeElement)) workspace.draft.focusIntent = { id: document.activeElement.id, node: document.activeElement };
  else if (workspace.draft && workspace.focusIntent) workspace.draft.focusIntent = workspace.focusIntent;
  closeNodeMenu();
  if (nodeConfirmation?.workspace === workspace) $('#nodeConfirm').close('cancel');
  workspace.closed = true; workspace.scroll?.destroy(); nodeWorkspace = null;
}
function resetNodeWorkspace() {
  disposeNodeWorkspace(); nodeDrafts.clear(); nodeWrites.clear();
  if ($('#nodeConfirm')?.open) $('#nodeConfirm').close('cancel');
}
async function nodeWorkspaceRequest(path, options = {}) {
  const writing = options.method && options.method.toUpperCase() !== 'GET';
  let lease = null;
  if (writing) {
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : options.body;
    const caseId = body?.case_id ?? body?.get?.('case_id') ?? state.caseId;
    if (window.FundsDataToolsActions?.blocked({ caseId })) throw new Error('本案件资料正在批量更新，请稍后保存');
    if (window.FundsMainAccountsActions?.blocked({ caseId })) throw new Error('主账号标记正在更新，请稍后保存');
    lease = { caseId }; accountDataWrites.add(lease);
  }
  try {
    const response = await fetch(path, options);
    if (!response.ok) {
      let message = await response.text();
      try { message = JSON.parse(message).detail || message; } catch (_) { /* plain text */ }
      const error = new Error(String(message).slice(0, 200)); error.status = response.status; throw error;
    }
    return await response.json();
  } finally { if (lease) accountDataWrites.delete(lease); }
}

function confirmNodeAction({ title, message, accept = '确定', cancel = '取消', danger = false, workspace = null }) {
  if (nodeConfirmation) return Promise.resolve(false);
  const dialog = $('#nodeConfirm'), trigger = document.activeElement;
  $('#ndConfirmTitle').textContent = title; $('#ndConfirmMessage').textContent = message;
  $('#ndConfirmAccept').textContent = accept; $('#ndConfirmCancel').textContent = cancel;
  $('#ndConfirmAccept').classList.toggle('danger', danger);
  dialog.returnValue = '';
  return new Promise(resolve => {
    const confirmation = nodeConfirmation = { workspace };
    dialog.onclose = () => {
      if (nodeConfirmation !== confirmation) return;
      nodeConfirmation = null; dialog.onclose = null;
      if (trigger?.isConnected && !trigger.disabled && !trigger.closest('[inert]')) trigger.focus();
      resolve(dialog.returnValue === 'confirm');
    };
    $('#ndConfirmAccept').onclick = () => dialog.close('confirm');
    $('#ndConfirmCancel').onclick = $('#ndConfirmClose').onclick = () => dialog.close('cancel');
    dialog.showModal();
  });
}
function hasNodeDrafts() {
  captureNodeDraft();
  return [...nodeDrafts.values()].some(nodeDraftDirty);
}
async function leaveNodeDrafts() {
  if (!hasNodeDrafts()) return true;
  const context = caseContext();
  return await confirmNodeAction({ title: '离开账户资料？', message: '尚有未保存的账户资料。离开当前案件或画布后将放弃这些修改。', accept: '放弃修改', cancel: '继续编辑' }) && isCurrentCase(context);
}
function syncNodeWorkspaceControls(workspace = nodeWorkspace) {
  if (!nodeWorkspaceIsCurrent(workspace) || !workspace.draft) return;
  const pending = (nodeWrites.size > 0), crop = pcState.binding && isCurrentCase(pcState.binding.context);
  const bulk = !!window.FundsDataToolsActions?.blocked(workspace.context) || !!window.FundsMainAccountsActions?.blocked(workspace.context);
  const busy = workspace.phase !== 'ready' || pending || !!crop || workspace.confirming || bulk;
  for (const button of $('#drawerBody').querySelectorAll('[data-node-action]')) button.disabled = !!busy;
  for (const id of ['#ndIdInput', '#ndFocusScale', '#ndPhotoFile']) if ($(id)) $(id).disabled = !!busy;
  $('#ndIdSave').disabled = !!busy || workspace.draft.idValue.trim() === workspace.draft.idBase;
  $('#ndFocusApply').disabled = !!busy || workspace.draft.focusValue === workspace.draft.focusBase;
  $('#ndPhotoRe').disabled = !!busy || !workspace.node.photo_at;
  $('#ndPhotoClr').disabled = !!busy || !workspace.node.photo_at;
  $('#ndPhotoMore').disabled = !!busy || !workspace.node.photo_at;
  $('#ndRemove').disabled = !!busy || !nodeRelatedIds(workspace.account).length;
  $('#ndDirty').textContent = nodeDraftDirty(workspace.draft) ? '有未保存修改' : '';
  $('#drawer').setAttribute('aria-busy', String(pending));
  if (pending) $('#ndStatus').textContent = '正在保存…';
  else if ($('#ndStatus').textContent === '正在保存…') $('#ndStatus').textContent = workspace.draft.status || '';
}
function renderNodeStats(workspace = nodeWorkspace) {
  if (!nodeWorkspaceIsCurrent(workspace) || !workspace.profile) return;
  const graph = workspace.scope === 'graph', values = graph ? workspace.node : workspace.profile;
  $('#ndScopeCase').setAttribute('aria-pressed', String(!graph)); $('#ndScopeGraph').setAttribute('aria-pressed', String(graph));
  $('#ndStats').innerHTML = `<div><span>转入 ${fmtCount(values.in_cnt || 0)} 笔</span><strong>${totalsHtml(values.in_totals, values.in_amt)}</strong></div>
    <div><span>转出 ${fmtCount(values.out_cnt || 0)} 笔</span><strong>${totalsHtml(values.out_totals, values.out_amt)}</strong></div>
    <dl><div><dt>首笔时间</dt><dd>${esc(fmtTime(workspace.profile.tmin) || '—')}</dd></div><div><dt>末笔时间</dt><dd>${esc(fmtTime(workspace.profile.tmax) || '—')}</dd></div></dl>`;
  $('#ndStatsScope').hidden = !graph;
  $('#ndStatsScope').textContent = graph ? '已追踪流水 · 同一笔双方流水合并统计；时间范围为本案件' : '本案件全部流水';
}
function renderNodeWorkspace(workspace) {
  if (!nodeWorkspaceIsCurrent(workspace)) return;
  const { profile, node, account, mainInfo } = workspace, scale = state.focusNodes.get(account) || 1;
  let draft = nodeDrafts.get(workspace.key);
  if (!draft) {
    draft = { idValue: node.id_no || '', idBase: node.id_no || '', focusValue: scale, focusBase: scale, status: '' };
    nodeDrafts.set(workspace.key, draft);
  } else {
    if (draft.idValue.trim() === draft.idBase) draft.idValue = draft.idBase = node.id_no || '';
    if (draft.focusValue === draft.focusBase) draft.focusValue = draft.focusBase = scale;
  }
  workspace.draft = draft;
  const name = profile.name || node.name || '(未知)', photo = nodePhotoUrl(node);
  const badge = node.is_main === 1 ? `主账号${mainInfo ? mainInfo.source === 'auto' ? ' · 自动识别' : ' · 手动标记' : ''}` : node.is_same_name === 1 ? '同户名账号 · 本人流水未导入' : '';
  const avatar = photo ? `<img class="node-avatar" src="${esc(photo)}" alt="${esc(name)}的节点照片">` : '<span class="node-avatar node-avatar-empty"><img class="ui-icon" src="/static/ui-icons/user-search.svg" alt=""></span>';
  $('#drawerBody').innerHTML = `
    <section class="node-identity"><div class="node-person">${avatar}<div><h2>${esc(name)}</h2><div class="mono">${esc(account)}</div>${badge ? `<span class="node-badge">${esc(badge)}</span>` : ''}</div></div><div class="node-quick"><button class="btn small" id="ndBrowse" data-node-action type="button">查看流水</button><button class="btn small" id="ndNeighborhood" data-node-action type="button">展开邻域</button></div></section>
    <section class="node-section"><h3>身份资料</h3><div class="node-form-grid"><div><label for="ndIdInput">身份证号</label><div class="node-field"><input id="ndIdInput" placeholder="未填写" autocomplete="off" value="${esc(draft.idValue)}"><button class="btn small primary" id="ndIdSave" data-node-action type="button">保存</button></div></div><div class="node-photo"><span class="node-field-label">节点照片</span><div class="node-field">${photo ? `<img id="ndPhotoImg" class="node-photo-thumb" src="${esc(photo)}" alt="当前节点照片">` : ''}<span class="muted">${photo ? '已设置照片' : '未设置照片'}</span><input type="file" id="ndPhotoFile" accept=".jpg,.jpeg,.png,.webp,.bmp" hidden><button class="btn small" id="ndPhotoUp" data-node-action type="button">选择照片</button><button class="btn small icon-button" id="ndPhotoMore" data-node-action type="button" aria-label="照片操作" aria-haspopup="menu" aria-controls="nodePhotoMenu" aria-expanded="false"><img class="ui-icon" src="/static/ui-icons/dots.svg" alt=""></button></div></div></div></section>
    <section class="node-section"><h3>图谱显示</h3><div class="node-form-grid"><div><span class="node-field-label">主账号</span><div class="node-field"><span class="muted">${node.is_main === 1 ? '已标记' : '未标记'}</span><button class="btn small" id="ndToggleMain" data-node-action type="button">${node.is_main === 1 ? '取消主账号标记' : '设为主账号'}</button></div></div><div><label for="ndFocusScale">重点展示</label><div class="node-field"><select id="ndFocusScale"><option value="1">正常大小</option>${[1.5,2,2.5,3,3.5,4].map(value => `<option value="${value}" ${draft.focusValue === value ? 'selected' : ''}>放大 ${value} 倍</option>`).join('')}</select><button class="btn small primary" id="ndFocusApply" data-node-action type="button">应用</button></div></div></div></section>
    <section class="node-section node-statistics"><div class="node-section-head"><h3>交易统计</h3><div class="node-scope" role="group" aria-label="统计范围"><button id="ndScopeCase" class="btn small" type="button" aria-pressed="true">本案件</button><button id="ndScopeGraph" class="btn small" type="button" aria-pressed="false">当前图谱</button></div></div><div id="ndStats" class="node-stats"></div><p id="ndStatsScope" class="muted"></p></section>
    <section class="node-ledger" aria-labelledby="ndCounterTitle"><h3 id="ndCounterTitle">交易对手 TOP20 <span>本案件</span></h3><div id="ndCounterScroll" class="node-table-scroll" tabindex="0" role="region" aria-label="账户交易对手，可水平滚动"><table id="ndCounterTable" class="tbl"><thead><tr><th>对手</th><th>转入</th><th>转出</th><th>操作</th></tr></thead><tbody id="ndCounterTbody">${(profile.top_counterparties || []).map(counter => `<tr><td><b>${esc(counter.name || '(未知)')}</b><span class="mono">${esc(counter.acct)}</span></td><td>${fmtCount(counter.in_cnt || 0)} 笔<br><span class="amt">${totalsHtml(counter.in_totals, counter.in_amt)}</span></td><td>${fmtCount(counter.out_cnt || 0)} 笔<br><span class="amt">${totalsHtml(counter.out_totals, counter.out_amt)}</span></td><td class="node-counter-action"><button class="btn small" type="button" data-node-action data-node-counter="${esc(counter.acct)}" aria-label="查看与${esc(counter.name || counter.acct)}的交易流水">查看流水</button></td></tr>`).join('') || '<tr><td colspan="4" class="edge-empty">暂无交易对手</td></tr>'}</tbody></table></div><div class="flow-horizontal-scroll node-horizontal-scroll" role="group" aria-label="账户交易对手表格水平滚动"><button id="ndScrollLeft" class="btn small icon-button" type="button" aria-label="账户交易对手向左滚动"><img class="ui-icon" src="/static/ui-icons/chevron-left.svg" alt=""></button><input id="ndHorizontalPosition" type="range" min="0" max="0" value="0" aria-label="账户交易对手水平位置"><button id="ndScrollRight" class="btn small icon-button" type="button" aria-label="账户交易对手向右滚动"><img class="ui-icon" src="/static/ui-icons/chevron-right.svg" alt=""></button></div></section>
    <div class="node-status-row"><span id="ndDirty" role="status"></span><span id="ndStatus" role="status" aria-live="polite">${esc(draft.status || '')}</span></div><footer class="node-footer"><span>仅当前案件</span><button id="ndMore" class="btn small" data-node-action type="button" aria-label="账户更多操作" aria-haspopup="menu" aria-controls="nodeMenu" aria-expanded="false"><img class="ui-icon" src="/static/ui-icons/dots.svg" alt="">更多操作</button></footer>`;
  $('#ndFocusScale').value = String(draft.focusValue);
  const capture = () => { captureNodeDraft(workspace); draft.status = ''; $('#ndStatus').textContent = ''; syncNodeWorkspaceControls(workspace); };
  $('#ndIdInput').oninput = capture; $('#ndFocusScale').onchange = capture;
  $('#ndIdSave').onclick = () => writeNodeWorkspace(workspace, 'id');
  $('#ndFocusApply').onclick = () => writeNodeWorkspace(workspace, 'focus');
  $('#ndToggleMain').onclick = () => writeNodeWorkspace(workspace, 'main');
  $('#ndPhotoUp').onclick = () => { if (nodeWorkspaceIsCurrent(workspace) && (nodeWrites.size === 0)) $('#ndPhotoFile').click(); };
  $('#ndPhotoFile').onchange = () => {
    if (!nodeWorkspaceIsCurrent(workspace) || (nodeWrites.size > 0)) return;
    const file = $('#ndPhotoFile').files[0]; if (file) openPhotoCrop(file, account, state.graph?.findById?.(account));
    $('#ndPhotoFile').value = '';
  };
  $('#ndPhotoMore').onclick = () => openNodeMenu(workspace, $('#nodePhotoMenu'), $('#ndPhotoMore'));
  $('#ndMore').onclick = () => openNodeMenu(workspace, $('#nodeMenu'), $('#ndMore'));
  $('#ndBrowse').onclick = () => browseNodeFlows(workspace);
  $('#ndNeighborhood').onclick = () => { if (nodeWorkspaceIsCurrent(workspace) && (nodeWrites.size === 0)) exploreNeighborhood(account); };
  $('#ndCounterTbody').onclick = event => { const button = event.target.closest('[data-node-counter]'); if (button && !button.disabled) browseNodeFlows(workspace, button.dataset.nodeCounter); };
  $('#ndScopeCase').onclick = () => { workspace.scope = 'case'; renderNodeStats(workspace); };
  $('#ndScopeGraph').onclick = () => { workspace.scope = 'graph'; renderNodeStats(workspace); };
  workspace.scroll = bindTableHorizontalScroll({ viewport: $('#ndCounterScroll'), range: $('#ndHorizontalPosition'), left: $('#ndScrollLeft'), right: $('#ndScrollRight'), table: $('#ndCounterTable') });
  renderNodeStats(workspace); syncNodeWorkspaceControls(workspace); restoreNodeFocus(workspace);
}
async function loadNodeWorkspace(workspace = nodeWorkspace) {
  if (!nodeWorkspaceIsCurrent(workspace)) return;
  const read = ++workspace.readSequence; workspace.phase = 'loading';
  $('#drawer').setAttribute('aria-busy', 'true'); $('#drawerBody').innerHTML = '<div class="node-loading" role="status">正在读取账户资料…</div>';
  try {
    const [profile, mainAccounts] = await Promise.all([
      nodeWorkspaceRequest(`/api/accounts/${encodeURIComponent(workspace.account)}/profile?case_id=${workspace.context.caseId}`),
      nodeWorkspaceRequest(`/api/main-accounts?case_id=${workspace.context.caseId}`),
    ]);
    if (!nodeWorkspaceIsCurrent(workspace) || workspace.readSequence !== read) return;
    workspace.profile = profile; workspace.node = { ...(state.lastGraph.nodes.find(node => node.account === workspace.account) || {}) };
    workspace.mainInfo = mainAccounts.find(info => info.account === workspace.account); workspace.phase = 'ready';
    renderNodeWorkspace(workspace);
  } catch (error) {
    if (!nodeWorkspaceIsCurrent(workspace) || workspace.readSequence !== read) return;
    workspace.phase = 'error'; $('#drawer').setAttribute('aria-busy', 'false');
    $('#drawerBody').innerHTML = `<div class="node-loading"><p role="status">账户资料读取失败：${esc(error.message || error)}</p><button id="ndRetry" class="btn small" type="button">重试</button></div>`;
    $('#ndRetry').onclick = () => loadNodeWorkspace(workspace);
  }
}
async function showNodeDrawer(account, item) {
  disposeNodeWorkspace(); disposeEdgeWorkspace();
  const context = caseContext(), view = state.graphView;
  const key = nodeDraftKey(context, account);
  for (const [other, draft] of nodeDrafts) if (other !== key) draft.focusIntent = null;
  item = state.graph?.findById?.(account) || item; selectItem(item);
  const workspace = nodeWorkspace = { context, view, revision: view.revision, account, key: nodeDraftKey(context, account),
    sequence: ++drawerSequence, readSequence: 0, phase: 'loading', scope: 'case', closed: false, menu: null };
  $('#drawer').classList.remove('edge-detail'); $('#drawer').classList.add('account-detail', 'open');
  $('#drawer').inert = false; $('#drawer').setAttribute('aria-hidden', 'false');
  $('#drawerTitle').textContent = '账户资料'; $('#drawerContext').textContent = (state.cases.find(value => value.id == context.caseId)?.name || ''); $('#drawerContext').hidden = false;
  await loadNodeWorkspace(workspace);
}
async function writeNodeWorkspace(workspace, kind) {
  if (window.FundsDataToolsActions?.blocked(workspace?.context)) return;
  if (window.FundsMainAccountsActions?.blocked(workspace?.context)) return;
  if (!nodeWorkspaceIsCurrent(workspace) || workspace.phase !== 'ready' || (nodeWrites.size > 0) || pcState.binding || workspace.confirming) return;
  captureNodeDraft(workspace); closeNodeMenu();
  if (kind === 'main') return manageNodeMainAccount(workspace);
  const { context, account, key, draft } = workspace, name = workspace.profile.name || workspace.node.name || '';
  let path, body, method = 'POST';
  if (kind === 'id') { body = { id_no: draft.idValue.trim() }; path = '/api/account-ids/set'; if (body.id_no === draft.idBase) return; }
  else if (kind === 'focus') { body = { on: draft.focusValue > 1, scale: draft.focusValue }; path = '/api/focus-nodes/set'; if (draft.focusValue === draft.focusBase) return; }
  else if (kind === 'photo-clear') { body = {}; path = '/api/account-photos/clear'; if (!workspace.node.photo_at) return; }
  else return;
  rememberNodeFocus(workspace, kind);
  const operation = { kind }; nodeWrites.set(key, operation); $('#ndStatus').textContent = '正在保存…'; syncNodeWorkspaceControls(workspace);
  try {
    const result = await nodeWorkspaceRequest(path, { method, ...jsonOpts({ case_id: context.caseId, account, ...body }) });
    if (!isCurrentCase(context) || nodeWrites.get(key) !== operation) return;
    if (kind === 'id') {
      const saved = typeof result.id_no === 'string' ? result.id_no : body.id_no;
      if (draft.idValue.trim() === body.id_no) {
        draft.idValue = saved;
        if (nodeWorkspace?.key === key && nodeWorkspaceIsCurrent(nodeWorkspace) && $('#ndIdInput')?.value.trim() === body.id_no) $('#ndIdInput').value = saved;
      }
      draft.idBase = saved;
      const node = state.lastGraph.nodes.find(value => value.account === account); if (node) node.id_no = saved || null;
    }
    if (kind === 'focus') { draft.focusBase = body.scale; if (body.on) state.focusNodes.set(account, body.scale); else state.focusNodes.delete(account); }
    draft.status = '已保存';
    const active = nodeWorkspace;
    if (active?.key === key && nodeWorkspaceIsCurrent(active)) $('#ndStatus').textContent = draft.status;
    if (kind === 'id') renderGraph();
    else { state.forceRebuild = true; await loadGraph(true, context); }
  } catch (error) {
    const active = nodeWorkspace;
    if (isCurrentCase(context)) draft.status = '保存未完成：' + String(error.message || error).slice(0, 200);
    if (isCurrentCase(context) && active?.key === key && nodeWorkspaceIsCurrent(active)) $('#ndStatus').textContent = draft.status;
  } finally {
    if (nodeWrites.get(key) === operation) nodeWrites.delete(key);
    if (nodeWorkspace) {
      syncNodeWorkspaceControls(nodeWorkspace);
      if (nodeWorkspace.key === key) restoreNodeFocus(nodeWorkspace);
    }
  }
}
function nodeRelatedIds(account) {
  const ids = new Set();
  for (const edge of state.lastGraph.edges) if (edge.source === account || edge.target === account) for (const id of edge.ids || []) ids.add(id);
  return [...ids];
}
function browseNodeFlows(workspace, counter = '') {
  if (!nodeWorkspaceIsCurrent(workspace) || (nodeWrites.size > 0)) return;
  const account = workspace.account; closeDrawer(false, { keepSelection: true }); switchTab('search');
  $('#workspace')?.fundsWorkspaceShell?.setSidebarCollapsed(false);
  for (const id of ['#fAccount','#fName','#fCounter','#fKw','#fMin','#fMax','#fFrom','#fTo']) $(id).value = '';
  $('#fTracked').checked = false; $('#fAccount').value = account; $('#fCounter').value = counter; doSearch(1);
}
function openNodeMenu(workspace, element, trigger) {
  if (!nodeWorkspaceIsCurrent(workspace) || (nodeWrites.size > 0) || trigger.disabled) return;
  const toggled = workspace.menu?.element === element; closeNodeMenu(); if (toggled) return;
  workspace.menu = { element, trigger }; element.hidden = false; trigger.setAttribute('aria-expanded', 'true');
  const rect = trigger.getBoundingClientRect(), drawer = $('#drawer').getBoundingClientRect();
  element.style.left = `${Math.max(8, Math.min(drawer.width - element.offsetWidth - 8, rect.right - drawer.left - element.offsetWidth))}px`;
  element.style.top = `${Math.max(8, Math.min(drawer.height - element.offsetHeight - 8, rect.bottom - drawer.top + 5))}px`;
  element.querySelector('button:not(:disabled)')?.focus();
}
async function removeNodeFromGraph(workspace = nodeWorkspace) {
  if (!nodeWorkspaceIsCurrent(workspace) || (nodeWrites.size > 0)) return;
  const ids = nodeRelatedIds(workspace.account); if (!ids.length) return;
  closeNodeMenu(); workspace.confirming = true; syncNodeWorkspaceControls(workspace);
  const confirmed = await confirmNodeAction({ title: '从图谱移出账户？', message: `将移出「${workspace.profile.name || workspace.account}」关联的 ${ids.length} 笔已追踪流水。原始流水保留，可通过撤销恢复。`, accept: '移出图谱', danger: true, workspace });
  workspace.confirming = false;
  if (!nodeWorkspaceIsCurrent(workspace)) return;
  syncNodeWorkspaceControls(workspace);
  if (!confirmed) return;
  const fingerprint = values => values.map(value => `${typeof value}:${value}`).sort().join('|');
  if (fingerprint(ids) !== fingerprint(nodeRelatedIds(workspace.account))) { $('#ndStatus').textContent = '图谱已变化，请重新确认移出范围。'; return; }
  workspace.phase = 'removing'; syncNodeWorkspaceControls(workspace); $('#ndStatus').textContent = '正在移出图谱…';
  try {
    const result = await untrackFlows(ids);
    if (!nodeWorkspaceIsCurrent(workspace)) return;
    if (result === true) closeDrawer(false, { keepSelection: true });
    else $('#ndStatus').textContent = '移出未完成，当前图谱未确认更新。';
  } catch (error) { if (nodeWorkspaceIsCurrent(workspace)) $('#ndStatus').textContent = '移出未完成：' + String(error.message || error).slice(0, 200); }
  finally { if (nodeWorkspaceIsCurrent(workspace)) { workspace.phase = 'ready'; syncNodeWorkspaceControls(workspace); } }
}
function bindNodeWorkspaceUI() {
  $('#ndPhotoRe').onclick = () => { const workspace = nodeWorkspace; if (!nodeWorkspaceIsCurrent(workspace) || (nodeWrites.size > 0) || !workspace.node.photo_at) return; closeNodeMenu(); openPhotoCrop(null, workspace.account, state.graph?.findById?.(workspace.account)); };
  $('#ndPhotoClr').onclick = async () => {
    const workspace = nodeWorkspace;
    if (!nodeWorkspaceIsCurrent(workspace) || (nodeWrites.size > 0) || !workspace.node.photo_at) return;
    rememberNodeFocus(workspace, 'photo-clear');
    closeNodeMenu(); workspace.confirming = true; syncNodeWorkspaceControls(workspace);
    const confirmed = await confirmNodeAction({ title: '移除节点照片？', message: '照片将从该账户移除，节点恢复为圆点。需要恢复时请重新选择照片。', accept: '移除照片', danger: true, workspace });
    workspace.confirming = false;
    if (!nodeWorkspaceIsCurrent(workspace)) return;
    syncNodeWorkspaceControls(workspace);
    if (confirmed) return writeNodeWorkspace(workspace, 'photo-clear');
  };
  $('#ndRemove').onclick = () => removeNodeFromGraph();
  $('#drawer').addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !nodeWorkspace?.menu) return;
    event.preventDefault(); event.stopImmediatePropagation(); closeNodeMenu(true);
  }, true);
  $('#drawerBody').addEventListener('scroll', () => closeNodeMenu(), { capture: true, passive: true });
  document.addEventListener('pointerdown', event => { const menu = nodeWorkspace?.menu; if (menu && !menu.element.contains(event.target) && !menu.trigger.contains(event.target)) closeNodeMenu(); }, true);
  document.addEventListener('focusin', event => { const menu = nodeWorkspace?.menu; if (menu && !menu.element.contains(event.target) && !menu.trigger.contains(event.target)) closeNodeMenu(); });
  window.addEventListener('resize', () => closeNodeMenu());
}

function pcLayout() {
  const img = $('#pcImg'), W = img.clientWidth, H = img.clientHeight;
  if (!W || !H) return;
  const d = 2 * pcState.r * Math.min(W, H), circle = $('#pcCircle');
  circle.style.width = d + 'px'; circle.style.height = d + 'px';
  circle.style.left = (pcState.cx * W - d / 2) + 'px'; circle.style.top = (pcState.cy * H - d / 2) + 'px';
}
function revokePhotoCrop(force = false) {
  if (pcState.binding?.busy && !force) return;
  if (pcState.objectUrl) URL.revokeObjectURL(pcState.objectUrl);
  pcState.objectUrl = null; pcState.binding = null; pcState.file = null; pcState.account = null;
  $('#photoCropModal').style.display = 'none'; $('#photoCropModal').setAttribute('aria-busy', 'false');
  syncNodeWorkspaceControls();
}
function openPhotoCrop(file, account, item) {
  if (window.FundsDataToolsActions?.blocked(caseContext())) return;
  if (window.FundsMainAccountsActions?.blocked(caseContext())) return;
  const context = caseContext(), key = nodeDraftKey(context, account);
  if ((nodeWrites.size > 0) || pcState.binding?.busy) return;
  if (nodeWorkspace?.key === key) rememberNodeFocus(nodeWorkspace, 'photo');
  revokePhotoCrop(true); closeNodeMenu();
  pcState.binding = { context, key, view: state.graphView, revision: state.graphView.revision, busy: false };
  pcState.file = file || null; pcState.account = account; pcState.item = item || null;
  pcState.cx = .5; pcState.cy = .5; pcState.r = .4;
  if (file) pcState.objectUrl = URL.createObjectURL(file);
  $('#pcImg').src = file ? pcState.objectUrl : `/api/account-photos/orig?case_id=${context.caseId}&account=${encodeURIComponent(account)}`;
  $('#pcZoom').value = 40; $('#pcClose').disabled = false; $('#pcConfirm').disabled = false;
  $('#pcConfirm').textContent = '确认并上传'; $('#pcZoom').disabled = false; $('#pcStatus').textContent = '';
  $('#photoCropModal').style.display = 'flex'; syncNodeWorkspaceControls();
}
function bindPhotoCrop() {
  const img = $('#pcImg'), circle = $('#pcCircle'); let dragging = false;
  img.onload = pcLayout;
  circle.addEventListener('pointerdown', event => { if (pcState.binding?.busy) return; dragging = true; try { circle.setPointerCapture(event.pointerId); } catch (_) { /* optional */ } event.preventDefault(); });
  circle.addEventListener('pointermove', event => { if (!dragging || pcState.binding?.busy) return; const rect = img.getBoundingClientRect(); pcState.cx = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)); pcState.cy = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)); pcLayout(); });
  const stop = () => { dragging = false; }; circle.addEventListener('pointerup', stop); circle.addEventListener('pointercancel', stop); circle.addEventListener('lostpointercapture', stop);
  $('#pcZoom').oninput = event => { if (pcState.binding?.busy) return; pcState.r = Number(event.target.value) / 100; pcLayout(); };
  $('#pcClose').onclick = () => revokePhotoCrop();
  $('#pcConfirm').onclick = async () => {
    const binding = pcState.binding, account = pcState.account, crop = { cx: pcState.cx, cy: pcState.cy, r: pcState.r };
    const current = () => binding && pcState.binding === binding && isCurrentCase(binding.context) && binding.view === state.graphView && binding.revision === state.graphView.revision;
    if (!current()) {
      if (binding && pcState.binding === binding) { $('#pcStatus').textContent = '图谱已更新，请重新打开照片裁剪。'; $('#pcConfirm').disabled = true; }
      return;
    }
    if (binding.busy || (nodeWrites.size > 0) || window.FundsMainAccountsActions?.blocked(binding.context)) return;
    const operation = { kind: 'photo' }; binding.busy = true; nodeWrites.set(binding.key, operation);
    $('#pcClose').disabled = true; $('#pcConfirm').disabled = true; $('#pcConfirm').textContent = '正在上传…'; $('#pcZoom').disabled = true;
    $('#photoCropModal').setAttribute('aria-busy', 'true'); $('#pcStatus').textContent = '正在上传照片…'; syncNodeWorkspaceControls();
    try {
      let file = pcState.file;
      if (!file) { const response = await fetch(`/api/account-photos/orig?case_id=${binding.context.caseId}&account=${encodeURIComponent(account)}`); if (!current()) return; if (!response.ok) throw new Error('无现有照片原图'); file = await response.blob(); if (!current()) return; }
      const data = new FormData(); data.append('file', file, file.name || 'photo.png'); data.append('case_id', binding.context.caseId); data.append('account', account);
      data.append('crop_cx', crop.cx.toFixed(4)); data.append('crop_cy', crop.cy.toFixed(4)); data.append('crop_r', crop.r.toFixed(4));
      const response = await fetch('/api/account-photos/set', { method: 'POST', body: data });
      if (!current()) return;
      const result = await response.json(); if (!current()) return; if (!response.ok) throw new Error(result.detail || '上传失败');
      const draft = nodeDrafts.get(binding.key); if (draft) draft.status = '照片已更新';
      revokePhotoCrop(true); state.forceRebuild = true; await loadGraph(true, binding.context);
    } catch (error) { if (current()) $('#pcStatus').textContent = '上传未完成：' + String(error.message || error).slice(0, 200); }
    finally {
      if (nodeWrites.get(binding.key) === operation) nodeWrites.delete(binding.key);
      if (pcState.binding === binding) {
        binding.busy = false; $('#pcClose').disabled = false; $('#pcConfirm').textContent = '确认并上传';
        $('#pcConfirm').disabled = !current(); $('#pcZoom').disabled = !current();
        $('#photoCropModal').setAttribute('aria-busy', 'false');
        if (!current()) $('#pcStatus').textContent = '图谱已更新，请重新打开照片裁剪。';
      }
      syncNodeWorkspaceControls();
    }
  };
}

function edgeWorkspaceIsCurrent(workspace) {
  return !!workspace && workspace === edgeWorkspace && !workspace.closed
    && workspace.sequence === drawerSequence && isCurrentCase(workspace.context)
    && workspace.view === state.graphView && workspace.revision === state.graphView.revision
    && $('#drawer').classList.contains('open');
}

function closeEdgeMenu(restoreFocus = false) {
  const menu = edgeWorkspace?.menu;
  const element = $('#edgeMenu');
  if (element) element.hidden = true;
  if (edgeWorkspace) edgeWorkspace.menu = null;
  if (menu?.trigger?.isConnected) {
    menu.trigger.setAttribute('aria-expanded', 'false');
    if (restoreFocus && !menu.trigger.disabled) menu.trigger.focus();
  }
}

function disposeEdgeWorkspace() {
  closeEdgeMenu(false);
  if (edgeWorkspace) { edgeWorkspace.closed = true; edgeWorkspace.scroll?.destroy(); }
  edgeWorkspace = null;
}

function syncEdgeWorkspaceControls(workspace) {
  if (!edgeWorkspaceIsCurrent(workspace)) return;
  const busy = workspace.phase === 'loading' || workspace.phase === 'removing';
  $('#drawer').setAttribute('aria-busy', String(busy));
  $$('#edgeTbody button').forEach(button => { button.disabled = workspace.phase !== 'ready'; });
  if ($('#edgeRemove')) $('#edgeRemove').disabled = workspace.phase !== 'ready';
  workspace.scroll?.sync();
}

function renderEdgeRows(workspace) {
  if (!edgeWorkspaceIsCurrent(workspace)) return;
  $('#edgeTbody').innerHTML = workspace.rows.length ? workspace.rows.map(flow => {
    const time = fmtTime(flow.trade_time).replace('T', ' ').split(' ');
    return `<tr data-flow-id="${esc(flow.id)}"><td class="edge-time"><time>${esc(time[0] || '—')}<span>${esc(time[1] || '')}</span></time></td>
      <td class="amt">${moneyHtml(flow.amount, flow.currency)}</td><td class="edge-summary">${esc(flow.summary || '—')}</td>
      <td class="edge-row-actions"><button type="button" class="btn edge-context" data-id="${esc(flow.id)}" aria-label="查看流水 ${esc(flow.id)} 的前后流水">前后流水</button><button type="button" class="btn edge-more" data-id="${esc(flow.id)}" aria-haspopup="menu" aria-controls="edgeMenu" aria-expanded="false" aria-label="流水 ${esc(flow.id)} 操作"><img class="ui-icon" src="/static/ui-icons/dots.svg" alt=""></button></td></tr>`;
  }).join('') : '<tr><td colspan="4" class="edge-empty">该流向暂无已追踪流水</td></tr>';
  $('#edgeDisplayed').textContent = `已显示 ${fmtCount(workspace.rows.length)} / ${fmtCount(workspace.total)} 笔`;
  syncEdgeWorkspaceControls(workspace);
}

async function loadEdgeWorkspace(workspace = edgeWorkspace) {
  if (!edgeWorkspaceIsCurrent(workspace) || workspace.phase === 'removing') return;
  const request = ++workspace.readSequence;
  closeEdgeMenu(false); workspace.phase = 'loading'; workspace.rows = [];
  $('#edgeStatus').textContent = '';
  $('#edgeDisplayed').textContent = '正在读取流水…';
  $('#edgeTbody').innerHTML = '<tr><td colspan="4" class="edge-empty">正在读取流水…</td></tr>';
  syncEdgeWorkspaceControls(workspace);
  try {
    const result = await api(`/api/flows?case_id=${encodeURIComponent(workspace.context.caseId)}&ids=${encodeURIComponent(workspace.ids.join(','))}&size=500`);
    if (!edgeWorkspaceIsCurrent(workspace) || request !== workspace.readSequence) return;
    if (!Array.isArray(result.rows)) throw new Error('流水响应不完整');
    workspace.rows = [...result.rows].sort((a, b) => fmtTime(a.trade_time).localeCompare(fmtTime(b.trade_time))
      || String(a.id).localeCompare(String(b.id)));
    workspace.total = result.total ?? workspace.data.cnt ?? result.rows.length;
    workspace.phase = 'ready'; renderEdgeRows(workspace);
  } catch (error) {
    if (!edgeWorkspaceIsCurrent(workspace) || request !== workspace.readSequence) return;
    workspace.phase = 'error';
    $('#edgeTbody').innerHTML = '<tr><td colspan="4" class="edge-empty">流水暂时无法读取<button type="button" class="btn edge-retry">重新读取</button></td></tr>';
    $('#edgeStatus').textContent = String(error.message || '请求未完成').slice(0, 220);
    $('#edgeDisplayed').textContent = '流水未载入'; syncEdgeWorkspaceControls(workspace);
    $('#edgeTbody .edge-retry').disabled = false;
  }
}

async function showEdgeDrawer(model, item) {
  disposeNodeWorkspace();
  for (const draft of nodeDrafts.values()) draft.focusIntent = null;
  disposeEdgeWorkspace(); selectItem(item);
  const view = state.graphView, data = model.data || model;
  const start = fmtTime(data.tmin), end = fmtTime(data.tmax);
  const span = start && end ? `${start} — ${start.slice(0, 10) === end.slice(0, 10) ? end.slice(11) : end}` : start || end || '—';
  const totals = Array.isArray(data.amount_totals) && data.amount_totals.length
    ? data.amount_totals.map(total => `<span>${moneyHtml(total.amount, total.currency)}</span>`).join('')
    : `<span>${totalsHtml(null, data.total, data.currencies?.[0])}</span>`;
  const workspace = edgeWorkspace = { context: caseContext(), sequence: ++drawerSequence, view,
    revision: view.revision, key: edgeKey(model), data, ids: [...(model.ids || data.ids || [])],
    rows: [], total: data.cnt || 0, readSequence: 0, phase: 'loading', menu: null, closed: false };
  $('#drawerTitle').textContent = '资金流向';
  $('#drawerContext').textContent = state.caseName || $('#caseSelect')?.selectedOptions?.[0]?.textContent || '';
  $('#drawerContext').hidden = false;
  $('#drawer').classList.remove('account-detail');
  $('#drawer').classList.remove('account-detail');
  $('#drawer').classList.add('edge-detail', 'open'); $('#drawer').inert = false;
  $('#drawer').setAttribute('aria-hidden', 'false');
  $('#drawerBody').innerHTML = `<div class="edge-parties"><div><span>付款方</span><b>${esc(data.source_name || '未提供户名')}</b><span class="mono">${esc(data.source || model.source)}</span></div><img class="ui-icon" src="/static/ui-icons/chevron-right.svg" alt="转至"><div><span>收款方</span><b>${esc(data.target_name || '未提供户名')}</b><span class="mono">${esc(data.target || model.target)}</span></div></div>
    <dl class="edge-facts"><div class="edge-coverage"><dt>已追踪</dt><dd>${fmtCount(data.cnt || 0)} 笔</dd></div><div class="edge-totals"><dt class="sr-only">分币种合计</dt><dd>${totals}</dd></div><div class="edge-span"><dt>时间跨度</dt><dd>${esc(span)}</dd></div></dl>
    <section class="edge-ledger" aria-labelledby="edgeLedgerTitle"><h3 id="edgeLedgerTitle">已追踪流水<span>${fmtCount(data.cnt || 0)} 笔</span></h3>
      <div class="edge-table-scroll" id="edgeTableScroll" tabindex="0" role="region" aria-label="资金流向已追踪流水，可水平滚动"><table class="tbl" id="edgeTable"><thead><tr><th scope="col">时间</th><th scope="col" class="amt">金额</th><th scope="col">摘要</th><th scope="col">操作</th></tr></thead><tbody id="edgeTbody"></tbody></table></div>
      <div class="flow-horizontal-scroll edge-horizontal-scroll" role="group" aria-label="资金流向表格水平滚动"><button class="btn small" id="edgeScrollLeft" type="button" aria-label="资金流向向左滚动"><img class="ui-icon" src="/static/ui-icons/chevron-left.svg" alt=""></button><input id="edgeHorizontalPosition" type="range" min="0" max="0" value="0" step="1" disabled aria-label="资金流向水平位置" aria-controls="edgeTableScroll"><button class="btn small" id="edgeScrollRight" type="button" aria-label="资金流向向右滚动"><img class="ui-icon" src="/static/ui-icons/chevron-right.svg" alt=""></button></div>
    </section><p class="workflow-status" id="edgeStatus" role="status" aria-live="polite"></p><footer class="edge-footer"><span id="edgeDisplayed"></span><span>仅当前图谱</span></footer>`;
  workspace.scroll = bindTableHorizontalScroll({ viewport: $('#edgeTableScroll'), range: $('#edgeHorizontalPosition'),
    left: $('#edgeScrollLeft'), right: $('#edgeScrollRight'), table: $('#edgeTable') });
  await loadEdgeWorkspace(workspace);
}

function openEdgeMenu(trigger) {
  const workspace = edgeWorkspace;
  if (!edgeWorkspaceIsCurrent(workspace) || workspace.phase !== 'ready') return;
  const row = workspace.rows.find(flow => String(flow.id) === trigger.dataset.id);
  if (!row) return;
  const toggling = workspace.menu?.trigger === trigger; closeEdgeMenu(false);
  if (toggling) { trigger.focus(); return; }
  workspace.menu = { trigger, row };
  const menu = $('#edgeMenu'); menu.hidden = false; trigger.setAttribute('aria-expanded', 'true');
  const bounds = trigger.getBoundingClientRect(), box = menu.getBoundingClientRect(), pane = $('#drawer').getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(pane.width - box.width - 8, bounds.right - pane.left - box.width))}px`;
  const below = bounds.bottom - pane.top + 6;
  menu.style.top = `${Math.max(8, Math.min(pane.height - box.height - 8, below + box.height > pane.height ? bounds.top - pane.top - box.height - 6 : below))}px`;
  $('#edgeRemove').focus();
}

async function removeEdgeFlow() {
  const workspace = edgeWorkspace, flow = workspace?.menu?.row;
  if (!flow || !edgeWorkspaceIsCurrent(workspace) || workspace.phase !== 'ready') return;
  closeEdgeMenu(true); workspace.phase = 'removing';
  $('#edgeStatus').textContent = '正在移出当前图谱…'; syncEdgeWorkspaceControls(workspace);
  try {
    const result = await untrackFlows([flow.id]);
    if (!edgeWorkspaceIsCurrent(workspace)) return;
    if (result === true) {
      const item = state.graphView.edgeItems.get(workspace.key);
      if (item && !item.destroyed) await showEdgeDrawer(item.getModel(), item);
      else closeDrawer(false);
    } else $('#edgeStatus').textContent = '本次未移出图谱，流水仍保留。';
  } catch (error) {
    if (edgeWorkspaceIsCurrent(workspace)) $('#edgeStatus').textContent = '移出未完成：' + String(error.message || error).slice(0, 220);
  } finally {
    if (edgeWorkspaceIsCurrent(workspace)) { workspace.phase = 'ready'; syncEdgeWorkspaceControls(workspace); }
  }
}

function bindEdgeWorkspaceUI() {
  $('#edgeRemove').onclick = () => { void removeEdgeFlow(); };
  $('#drawerBody').addEventListener('click', event => {
    const button = event.target.closest?.('button');
    const workspace = edgeWorkspace;
    if (!button || button.disabled || !edgeWorkspaceIsCurrent(workspace)) return;
    if (button.classList.contains('edge-retry')) { void loadEdgeWorkspace(workspace); return; }
    if (button.id === 'edgeRemove') { void removeEdgeFlow(); return; }
    if (workspace.phase !== 'ready') return;
    if (button.classList.contains('edge-more')) openEdgeMenu(button);
    else if (button.classList.contains('edge-context')) {
      const flow = workspace.rows.find(row => String(row.id) === button.dataset.id);
      if (flow) { closeEdgeMenu(false); void openContext(flow.id); }
    }
  });
  $('#drawer').addEventListener('keydown', event => {
    if (!edgeWorkspace?.menu) return;
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeEdgeMenu(true); }
    else if (event.key === 'Tab') closeEdgeMenu(true);
    else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) { event.preventDefault(); $('#edgeRemove').focus(); }
  }, true);
  document.addEventListener('pointerdown', event => {
    const menu = edgeWorkspace?.menu;
    if (menu && !$('#edgeMenu').contains(event.target) && !menu.trigger.contains(event.target)) closeEdgeMenu(false);
  });
  document.addEventListener('focusin', event => {
    const menu = edgeWorkspace?.menu;
    if (menu && !$('#edgeMenu').contains(event.target) && !menu.trigger.contains(event.target)) closeEdgeMenu(false);
  });
  $('#drawerBody').addEventListener('scroll', () => closeEdgeMenu(false), { capture: true, passive: true });
  window.addEventListener('resize', () => closeEdgeMenu(false));
}

/* ---------------- 交易上下文（对手前后 X 笔） ---------------- */
/* 系统结算类户名：不是真实交易对手，其"视角"无追踪意义 */
const SYSTEM_ACCOUNT_NAMES = ['正常', '利息', '手续费', '小额费', '待报解', '税', '佣金', '年费'];

function isSystemAccount(name) {
  const nm = (name || '').trim();
  if (!nm) return false;
  return SYSTEM_ACCOUNT_NAMES.some(sys => nm === sys || nm.includes(sys));
}

let contextTableScroll = null;

function contextIsCurrent(request, context) {
  return !!request && state.ctx === request && !request.closed && isCurrentCase(context)
    && $('#ctxModal').style.display !== 'none';
}

function closeContext() {
  if (state.ctx) state.ctx = { ...state.ctx, closed: true, phase: 'closed', data: null, submission: null };
  $('#ctxModal').style.display = 'none';
  $('#ctxTbody').innerHTML = ''; $('#ctxSuggest').innerHTML = '';
  $('#ctxTrackStatus').textContent = '';
  syncContextSelection();
}

function syncContextSelection() {
  const selection = state.ctx, ready = selection?.phase === 'ready' && !selection.closed
    && (!selection.context || isCurrentCase(selection.context));
  const tracking = selection?.phase === 'tracking';
  const boxes = $$('#ctxTbody .ctx-ck'), picked = boxes.filter(box => box.checked);
  $('#ctxSelected').textContent = `已选 ${fmtCount(picked.length)} 笔`;
  const rows = selection?.data?.rows || [];
  const shown = rows.filter(row => selection.book === 'all' || row.book === selection.book);
  $('#ctxTrackedCount').textContent = `已上图 ${fmtCount(shown.filter(row => state.tracked.has(row.id)).length)} 笔`;
  $('#ctxTrack').disabled = !ready || !picked.length;
  $('#ctxTrackLabel').textContent = tracking ? '正在上图…' : picked.length ? `加入图谱（${fmtCount(picked.length)}）` : '加入图谱';
  $('#ctxModal').setAttribute('aria-busy', String(selection?.phase === 'loading' || tracking));
  $$('#ctxTabs .stab').forEach(button => { button.disabled = tracking; });
  $('#ctxX').disabled = tracking; $('#ctxBook').disabled = !ready;
  boxes.forEach(box => {
    box.disabled = !ready;
    box.closest('tr').classList.toggle('picked-row', box.checked);
  });
  $$('#ctxSuggest .sg-item').forEach(button => {
    const ids = new Set(button.dataset.ids.split(','));
    const eligible = boxes.filter(box => ids.has(box.dataset.id));
    const checked = eligible.filter(box => box.checked).length;
    const all = !!eligible.length && checked === eligible.length;
    button.disabled = !ready || !eligible.length;
    button.classList.toggle('picked', all);
    button.setAttribute('aria-pressed', checked && !all ? 'mixed' : String(all));
    const mark = button.querySelector('.ctx-match-check'); if (mark) mark.hidden = !all;
    const onGraph = button.querySelector('.ctx-match-on'); if (onGraph) onGraph.hidden = !!eligible.length;
  });
  contextTableScroll?.sync();
}

async function openContext(flowId) {
  const side = state.ctx?.side || 'payee';
  state.ctx = { flowId, side, x: Math.max(1, Math.min(50, Math.floor(Number($('#ctxX').value) || 5))),
    autoSwitch: true, book: 'all', phase: 'loading', closed: false };
  $('#ctxX').value = state.ctx.x; $('#ctxBook').value = 'all';
  $('#ctxCaseName').textContent = state.cases.find(item => item.id === state.caseId)?.name || `案件 ${state.caseId}`;
  $('#ctxAnchor').textContent = '本笔交易';
  $('#ctxTableScroll').scrollLeft = 0; $('#ctxTableScroll').scrollTop = 0;
  $('#ctxModal').style.display = 'flex';
  $$('#ctxTabs .stab').forEach(button => button.classList.toggle('active', button.dataset.side === side));
  await loadContext();
}

async function loadContext() {
  if (!state.ctx || state.ctx.closed || state.ctx.phase === 'tracking') return;
  const context = caseContext(), request = state.ctx;
  const { flowId, side, x } = request;
  request.context = context; request.phase = 'loading'; request.data = null;
  $('#ctxTrackStatus').textContent = '';
  $('#ctxPos').textContent = '正在读取流水…';
  $('#ctxTbody').innerHTML = '<tr><td colspan="8" class="context-empty" role="status">正在读取流水…</td></tr>';
  $('#ctxSuggest').innerHTML = '<p class="context-empty">正在读取匹配线索…</p>';
  syncContextSelection();
  // Report failures inside this bound dialog, never through a stale global toast.
  const response = await fetch(`/api/context/${encodeURIComponent(flowId)}?x=${x}&side=${side}&case_id=${encodeURIComponent(context.caseId)}`);
  if (!response.ok) {
    let message = await response.text();
    try { message = JSON.parse(message).detail || message; } catch (_) { /* plain text */ }
    throw new Error(String(message).slice(0, 200));
  }
  const r = await response.json();
  if (!contextIsCurrent(request, context)) return;
  if (!r?.anchor || !Array.isArray(r.rows)) throw new Error('流水明细格式无效');
  if (request.autoSwitch) {
    request.autoSwitch = false;
    const a = r.anchor;
    const counterName = side === 'payee' ? a.payee_name : a.payer_name;
    const selfName = side === 'payee' ? a.payer_name : a.payee_name;
    const counterAcct = side === 'payee' ? a.payee_account : a.payer_account;
    if (isSystemAccount(counterName) || isSystemAccount(counterAcct)) {
      const other = side === 'payee' ? 'payer' : 'payee';
      toast(`对手方“${counterName}”为系统结算账户，已切换到${other === 'payer' ? '付款' : '收款'}方视角（${selfName || '本人账户'}）`);
      request.side = other;
      $$('#ctxTabs .stab').forEach(button => button.classList.toggle('active', button.dataset.side === other));
      return loadContext();
    }
  }
  request.data = r; request.phase = 'ready';
  const a = r.anchor;
  $('#ctxAnchor').innerHTML = `<div class="context-anchor-id"><h3>本笔交易 <span>#${esc(a.id)}</span></h3><time>${esc(fmtTime(a.trade_time))}</time></div>
    <div class="context-party"><span>付款方</span><b>${esc(a.payer_name || '未提供户名')}</b><span class="mono">${esc(a.payer_account || '—')}</span></div>
    <img class="ui-icon context-arrow" src="/static/ui-icons/chevron-right.svg" alt="转至">
    <div class="context-party"><span>收款方</span><b>${esc(a.payee_name || '未提供户名')}</b><span class="mono">${esc(a.payee_account || '—')}</span></div>
    <div class="context-amount"><span>交易金额</span><b>${moneyHtml(a.amount, a.currency)}</b></div>`;
  const name = side === 'payee' ? a.payee_name : a.payer_name;
  $('#ctxPos').textContent = `${name || (side === 'payee' ? '收款方' : '付款方')} · ${r.account} · 共 ${fmtCount(r.total)} 笔 · 本笔第 ${fmtCount(r.pos)} 笔`;
  renderCtxRows(r); renderSuggestions(r);
}

/* ---------------- 智能推荐（疑似来源 / 去向） ---------------- */
function renderSuggestions(r) {
  const wrap = $('#ctxSuggest'), book = state.ctx?.book || 'all';
  const selection = state.ctx, context = caseContext();
  const visibleIds = new Set(r.rows.filter(row => book === 'all' || row.book === book).map(row => String(row.id)));
  const suggestions = r.suggestions || {};
  const item = suggestion => {
    const ids = (suggestion.ids || []).map(String).filter(id => visibleIds.has(id));
    return `<div class="sg-entry"><button class="sg-item" type="button" data-ids="${esc(ids.join(','))}" aria-pressed="false" aria-label="勾选推荐流水：${esc(suggestion.name || '未知户名')} ${esc(suggestion.account)}">
      <span class="sg-main"><b>${esc(suggestion.name || '未知户名')}</b><span class="mono">${esc(suggestion.account)}</span></span>
      <span class="sg-score">评分 <b>${esc(suggestion.score)}</b></span>
      <img class="ui-icon ctx-match-check" src="/static/ui-icons/check.svg" alt="已选" hidden><span class="ctx-match-on" hidden>已上图</span>
      <span class="sg-reason">${(suggestion.reasons || []).map(esc).join(' · ')}</span></button>
      ${(suggestion.tags || []).length ? `<details class="sg-details"><summary>补充依据</summary><div class="sg-tags">${suggestion.tags.map(tag => `<p class="sg-tag">${esc(tag)}</p>`).join('')}</div></details>` : ''}</div>`;
  };
  const group = (title, note, list) => {
    const visible = (list || []).filter(suggestion => (suggestion.ids || []).some(id => visibleIds.has(String(id))));
    return `<section class="sgroup"><h4 class="sg-title">${title}<span>${note}</span></h4>${visible.length
      ? `<div class="sg-items">${visible.map(item).join('')}</div>` : '<p class="sg-empty">当前范围暂无匹配线索</p>'}</section>`;
  };
  wrap.innerHTML = group('疑似来源', '锚点前转入', suggestions.sources) + group('疑似去向', '锚点后转出', suggestions.targets);
  wrap.querySelectorAll('.sg-item').forEach(button => {
    button.onclick = () => {
      if (!contextIsCurrent(selection, context) || selection.phase !== 'ready' || !button.isConnected || button.disabled) return;
      const ids = new Set(button.dataset.ids.split(','));
      const boxes = $$('#ctxTbody .ctx-ck').filter(box => ids.has(box.dataset.id) && !box.disabled);
      const checked = !boxes.every(box => box.checked);
      boxes.forEach(box => { box.checked = checked; });
      syncContextSelection();
    };
  });
  syncContextSelection();
}

function renderCtxRows(r) {
  const book = state.ctx?.book || 'all';
  const rows = r.rows.filter(row => book === 'all' || row.book === book);
  $('#ctxTbody').innerHTML = rows.length ? rows.map(row => {
    const anchor = row.id === r.anchor.id;
    const relative = anchor ? '本笔' : row.rn < r.pos ? `前${fmtCount(r.pos - row.rn)}` : `后${fmtCount(row.rn - r.pos)}`;
    const tracked = state.tracked.has(row.id), direction = row.dir === 'in' ? '转入' : '转出';
    const ownership = row.book === 'self' ? '本方' : row.book === 'counter' ? '对手' : '';
    const counter = row.dir === 'in' ? 'payer' : 'payee';
    const time = fmtTime(row.trade_time).replace('T', ' ').split(' ');
    const checkbox = tracked ? '<span class="ctx-on">已上图</span>'
      : `<input type="checkbox" class="ctx-ck" data-id="${esc(row.id)}" aria-label="选择${esc(relative)}流水：${esc(fmtTime(row.trade_time))} ${esc(row[counter + '_name'] || row[counter + '_account'] || '')}">`;
    return `<tr class="${anchor ? 'anchor' : ''}" data-flow-id="${esc(row.id)}"><td>${checkbox}</td><td class="context-relative">${relative}</td>
      <td class="context-direction">${direction}${ownership ? `<span class="context-ownership">${ownership}</span>` : ''}</td>
      <td class="context-time"><time>${esc(time[0] || '—')}<span>${esc(time[1] || '')}</span></time></td>
      <td class="amt">${moneyHtml(row.amount, row.currency)}</td><td class="amt">${row.balance == null || row.balance === '' ? '—' : moneyHtml(row.balance, row.currency)}</td>
      <td class="context-counter"><b>${esc(row[counter + '_name'] || '未提供户名')}</b><span class="mono">${esc(row[counter + '_account'] || '—')}</span></td><td class="context-summary">${esc(row.summary || '')}</td></tr>`;
  }).join('') : '<tr><td colspan="8" class="context-empty">该流水归属下暂无前后流水</td></tr>';
  syncContextSelection();
}

function bindContextUI() {
  $('#ctxClose').onclick = closeContext; $('#ctxCancel').onclick = closeContext;
  const editable = () => state.ctx && !state.ctx.closed && state.ctx.phase !== 'tracking';
  $$('#ctxTabs .stab').forEach(button => { button.onclick = async () => {
    if (!editable()) return;
    $$('#ctxTabs .stab').forEach(item => item.classList.toggle('active', item === button));
    state.ctx.side = button.dataset.side; await loadContext();
  }; });
  $('#ctxX').onchange = async () => {
    if (!editable()) return;
    const value = Math.max(1, Math.min(50, Math.floor(Number($('#ctxX').value) || 5)));
    $('#ctxX').value = value; state.ctx.x = value; await loadContext();
  };
  $('#ctxBook').onchange = () => {
    if (!editable() || state.ctx.phase !== 'ready') return;
    state.ctx.book = $('#ctxBook').value;
    renderCtxRows(state.ctx.data); renderSuggestions(state.ctx.data);
  };
  $('#ctxTbody').addEventListener('change', event => {
    if (event.target.matches('.ctx-ck')) syncContextSelection();
  });
  contextTableScroll = bindTableHorizontalScroll({ viewport: $('#ctxTableScroll'), range: $('#ctxHorizontalPosition'),
    left: $('#ctxScrollLeft'), right: $('#ctxScrollRight'), table: $('#ctxTable') });
}

/* ---------------- 导入 ---------------- */
const FIELD_LABELS = {
  trade_no: '交易流水号', trade_time: '交易时间', _time2: '时间（时分秒列）',
  payer_name: '付款户名', payer_account: '付款账号', payer_bank: '付款开户行',
  payee_name: '收款户名', payee_account: '收款账号', payee_bank: '收款开户行',
  self_name: '本方户名', self_account: '本方账号', counter_name: '对方户名',
  counter_account: '对方账号', counter_bank: '对方开户行', direction: '收支标志',
  income_amt: '收入金额列', expense_amt: '支出金额列', amount: '交易金额',
  balance: '余额', currency: '币种', summary: '摘要', channel: '交易类型/渠道',
};

function importFieldRequirement(field, mode, mapping) {
  const amounts = mapping.income_amt && mapping.expense_amt;
  if (field === 'trade_time' || (field === 'self_account' && mode === 'B')) return { required: true, note: '必填' };
  const pair = { payer_name: 'payer_account', payer_account: 'payer_name',
    payee_name: 'payee_account', payee_account: 'payee_name', counter_name: 'counter_account', counter_account: 'counter_name' }[field];
  if (pair) return { required: !mapping[pair], note: `与${FIELD_LABELS[pair]}二选一` };
  if (field === 'amount') return { required: !amounts, note: mode === 'A' ? '必填' : '可用收入与支出列替代' };
  if (field === 'direction') return { required: !amounts, note: '可用收入与支出列替代' };
  if (field === 'income_amt' || field === 'expense_amt') return { required: !mapping.amount, note: '收入与支出列共同替代交易金额' };
  return { required: false, note: '选填' };
}

function refreshImportMappingState() {
  const { mode, mapping } = collectMapping();
  $$('#impMapping select').forEach(select => {
    const field = select.dataset.field, requirement = importFieldRequirement(field, mode, mapping);
    const label = document.getElementById('map-label-' + field), note = document.getElementById('map-note-' + field);
    const status = document.getElementById('map-state-' + field);
    if (label) { label.classList.toggle('is-required', requirement.required); label.innerHTML = esc(FIELD_LABELS[field]) + (requirement.required ? '<span class="import-required" aria-hidden="true">*</span>' : ''); }
    if (note) note.textContent = requirement.note;
    if (status) { status.textContent = select.value ? '已映射' : requirement.required ? '待映射' : '未映射'; status.dataset.state = select.value ? 'mapped' : requirement.required ? 'missing' : 'optional'; }
  });
}

function renderImport(r) {
  const expanded = $('#impAdvanced')?.open || false;
  $('#impStep1').style.display = 'none'; $('#impStep2').style.display = 'block';
  $('#impMeta').innerHTML = `<b>${esc(r.filename)}</b><span class="muted"> · ${fmtCount(r.stats?.total || 0)} 行</span>`;
  $$('input[name=impMode]').forEach(i => { i.checked = i.value === r.mode; });
  const primary = r.mode === 'A'
    ? ['trade_time', 'payer_name', 'payer_account', 'payee_name', 'payee_account', 'amount', 'currency']
    : ['trade_time', 'self_account', 'counter_name', 'counter_account', 'direction', 'amount', 'income_amt', 'expense_amt'];
  const fields = r.mode === 'A'
    ? ['trade_no', 'trade_time', '_time2', 'self_account', 'payer_name', 'payer_account', 'payer_bank', 'payee_name', 'payee_account', 'payee_bank', 'amount', 'balance', 'currency', 'summary', 'channel']
    : ['trade_no', 'trade_time', '_time2', 'self_name', 'self_account', 'counter_name', 'counter_account', 'counter_bank', 'direction', 'income_amt', 'expense_amt', 'amount', 'balance', 'currency', 'summary', 'channel'];
  const sample = r.preview[0] || {};
  const receives = sample.self_account && sample.payee_account === sample.self_account && sample.payer_account !== sample.self_account;
  const sends = sample.self_account && sample.payer_account === sample.self_account && sample.payee_account !== sample.self_account;
  const counterparty = receives ? 'payer' : sends ? 'payee' : null;
  const examples = { ...sample };
  if (r.mode === 'B' && counterparty) {
    for (const key of ['name', 'account', 'bank']) examples['counter_' + key] ??= sample[counterparty + '_' + key];
    examples.direction ??= receives ? '收入' : '支出';
  }
  const row = field => {
    const requirement = importFieldRequirement(field, r.mode, r.mapping), mapped = r.mapping[field];
    const value = field === 'amount' ? moneyHtml(sample.amount, sample.currency)
      : field === 'balance' && sample.balance != null ? moneyHtml(sample.balance, sample.currency)
        : esc(examples[field] ?? '—');
    return `<tr data-field="${field}"><td><label id="map-label-${field}" class="import-field-label${requirement.required ? ' is-required' : ''}" for="map-${field}">${esc(FIELD_LABELS[field])}${requirement.required ? '<span class="import-required" aria-hidden="true">*</span>' : ''}</label><span class="import-requirement" id="map-note-${field}">${esc(requirement.note)}</span></td>
      <td><select id="map-${field}" data-field="${field}" aria-describedby="map-note-${field}"><option value="">不使用</option>${r.columns.map(column => `<option value="${esc(column)}"${mapped === column ? ' selected' : ''}>${esc(column)}</option>`).join('')}</select></td>
      <td class="import-map-example">${mapped ? value : '—'}</td><td><span id="map-state-${field}" class="import-map-state" data-state="${mapped ? 'mapped' : requirement.required ? 'missing' : 'optional'}">${mapped ? '已映射' : requirement.required ? '待映射' : '未映射'}</span></td></tr>`;
  };
  const table = list => `<div class="import-matrix-scroll"><table class="import-matrix"><thead><tr><th>字段</th><th>文件列</th><th>示例</th><th>状态</th></tr></thead><tbody>${list.map(row).join('')}</tbody></table></div>`;
  const advanced = fields.filter(field => !primary.includes(field));
  $('#impMapping').innerHTML = table(primary) + `<details class="import-advanced" id="impAdvanced"${expanded ? ' open' : ''}><summary>其他字段（${advanced.length}）</summary>${table(advanced)}</details>`;
  const accounts = r.self_accounts_in_file || [];
  $('#impMainSelWrap').style.display = accounts.length ? 'block' : 'none';
  $('#impMainTitle').textContent = `本方主账号 · ${accounts.length} 个`;
  $('#impMainSel').innerHTML = accounts.map(x => `<label class="mainsel"><input type="checkbox" class="mk-acct" value="${esc(x.account)}" checked>${esc(x.name || '未知户名')} <span class="mono">${esc(x.account)}</span>（${fmtCount(x.count)} 笔）</label>`).join('');
  // Keep every normalized preview field available; horizontal movement uses the
  // same controller as the workspace table, without querying or changing data.
  const columns = [['trade_time', '交易时间'], ['payer', '付款方'], ['payee', '收款方'], ['amount', '金额'], ['summary', '摘要'],
    ['currency', '币种'], ['balance', '余额'], ['trade_no', '交易流水号'], ['payer_bank', '付款开户行'],
    ['payee_bank', '收款开户行'], ['channel', '交易类型/渠道'], ['self_account', '本方账号'], ['self_name', '本方户名']];
  $('#impPreviewHead').innerHTML = `<tr>${columns.map(([key, label]) => `<th${key === 'amount' || key === 'balance' ? ' class="amt"' : ''}>${label}</th>`).join('')}</tr>`;
  $('#impTbody').innerHTML = r.preview.map(value => `<tr>${columns.map(([key]) => {
    let content;
    if (key === 'payer' || key === 'payee') content = `${esc(value[key + '_name'] || '—')}<br><span class="mono">${esc(value[key + '_account'] || '—')}</span>`;
    else if (key === 'trade_time') content = esc(fmtTime(value.trade_time));
    else if (key === 'amount') content = moneyHtml(value.amount, value.currency);
    else if (key === 'balance') content = value.balance == null ? '—' : moneyHtml(value.balance, value.currency);
    else content = esc(value[key] || '—');
    return `<td${key === 'amount' || key === 'balance' ? ' class="amt"' : ''}>${content}</td>`;
  }).join('')}</tr>`).join('');
  const stats = r.stats;
  $('#impStats').textContent = `可导入 ${fmtCount(stats.ok)} 行 · 跳过 ${fmtCount(stats.skipped)} 行`;
  $('#impPreviewNote').textContent = `前 ${r.preview.length} 行 · 规范化后 · ${columns.length} 列`;
  $('#impBad').innerHTML = (stats.bad_examples || []).map(b => `<div class="warn">${esc(b.row)} — ${esc(b.reason)}</div>`).join('');
}

function collectMapping() {
  const mode = ($$('input[name=impMode]').find(i => i.checked) || {}).value || (state.imp && state.imp.mode);
  const mapping = {};
  $$('#impMapping select').forEach(s => { if (s.value) mapping[s.dataset.field] = s.value; });
  return { mode, mapping };
}

function setPbar(pct, text) {
  $('#impPbar').classList.remove('indeterminate');
  $('#impPbar').style.display = 'block';
  $('#impPfill').style.width = Math.max(0, Math.min(100, pct * 100)) + '%';
  $('#impPtext').textContent = text;
}

function setPbarBusy(text) {
  // 上传完成后的不确定进度（服务器解析中）
  $('#impPbar').classList.add('indeterminate');
  $('#impPbar').style.display = 'block';
  $('#impPfill').style.width = '38%';
  $('#impPtext').textContent = text;
}

function hidePbar() {
  $('#impPbar').style.display = 'none';
  $('#impPbar').classList.remove('indeterminate');
}

async function loadBatches() {
  const context = caseContext(), sequence = ++batchView.request;
  closeBatchMenu(true);
  batchView.context = context; batchView.phase = 'loading';
  showBatchFeedback('正在读取导入批次…'); setBatchBusy();
  const current = () => sequence === batchView.request && isCurrentCase(context, false);
  try {
    const rows = await batchRequest(`/api/batches?case_id=${encodeURIComponent(context.caseId)}`);
    if (!current()) return;
    if (!Array.isArray(rows)) throw new Error('批次列表格式无效');
    batchView.rows = rows; batchView.phase = rows.length ? 'ready' : 'empty';
    renderBatchRows(context);
    showBatchFeedback(rows.length ? '' : '暂无导入批次');
    return true;
  } catch (error) {
    if (!current()) return;
    batchView.phase = 'error';
    showBatchFeedback('导入批次读取失败：' + batchError(error), () => loadBatches());
    return false;
  } finally { if (current()) setBatchBusy(); }
}

let batchView = { request: 0, context: null, rows: [], phase: 'empty', menu: null, deleting: null, scroll: null, bound: false };
const batchError = error => String(error?.message || error || '请求失败').slice(0, 200);
const batchCurrent = context => context && isCurrentCase(context, false)
  && batchView.context?.caseId === context.caseId && batchView.context?.generation === context.generation;

async function batchRequest(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = await response.text();
    try { message = JSON.parse(message).detail || message; } catch (_) { /* plain text */ }
    const error = new Error(String(message).slice(0, 200)); error.status = response.status; throw error;
  }
  return response.json();
}

function showBatchFeedback(message, retry, label = '重新读取') {
  $('#batchFeedback').hidden = !message;
  $('#batchMessage').textContent = message;
  $('#batchRetry').hidden = !retry; $('#batchRetry').textContent = label;
  const context = batchView.context;
  $('#batchRetry').onclick = retry ? () => { if (batchCurrent(context)) return retry(); } : null;
}

function setBatchBusy() {
  const busy = batchView.phase === 'loading' || !!batchView.deleting;
  $('#tab-batches').setAttribute('aria-busy', String(busy));
  $('#batchRetry').disabled = busy; $('#btnBatchImport').disabled = !!batchView.deleting;
  $('#batchList').querySelectorAll('.batch-more').forEach(button => { button.disabled = busy || batchView.phase !== 'ready'; });
  batchView.scroll?.sync();
}

function resetBatchView() {
  closeBatchMenu(false);
  batchView.request += 1; batchView.context = null; batchView.rows = [];
  batchView.phase = 'empty'; batchView.deleting = null;
  $('#batchList').innerHTML = ''; $('#batchCount').textContent = '';
  $('#batchListScroll').hidden = true; $('#batchHorizontalScroll').hidden = true;
  $('#batchListScroll').scrollLeft = 0; $('#batchListScroll').scrollTop = 0;
  showBatchFeedback(''); setBatchBusy();
}

function renderBatchRows(context) {
  const nonzero = value => /^\d+$/.test(String(value)) && !/^0+$/.test(String(value));
  $('#batchCount').textContent = `${fmtCount(batchView.rows.length)} 个批次`;
  $('#batchListScroll').hidden = !batchView.rows.length; $('#batchHorizontalScroll').hidden = !batchView.rows.length;
  $('#batchList').innerHTML = batchView.rows.map((batch, index) => `<tr data-batch-id="${esc(batch.id)}">
    <td><div class="batch-file"><span class="batch-filename">${esc(batch.filename)}</span><span class="batch-mode">${batch.mode === 'A' ? '双边' : batch.mode === 'B' ? '单账户' : '演示'}</span></div><time class="batch-time" datetime="${esc(batch.imported_at || '')}">${esc(fmtTime(batch.imported_at))}</time></td>
    <td class="batch-number${nonzero(batch.rows_imported) ? '' : ' is-zero'}">${esc(fmtCount(batch.rows_imported ?? 0))}</td>
    <td class="batch-number${nonzero(batch.rows_skipped) ? ' has-skipped' : ' is-zero'}">${esc(fmtCount(batch.rows_skipped ?? 0))}</td>
    <td class="batch-number${nonzero(batch.rows_dup) ? '' : ' is-zero'}">${esc(fmtCount(batch.rows_dup ?? 0))}</td>
    <td class="batch-actions"><button class="btn small icon-button batch-more" id="batch-more-${index}" type="button" data-index="${index}" aria-label="${esc('批次操作：' + batch.filename)}" aria-haspopup="menu" aria-controls="batchMenu" aria-expanded="false"><img class="ui-icon" src="/static/ui-icons/dots.svg" alt=""></button></td></tr>`).join('');
  $('#batchList').querySelectorAll('.batch-more').forEach(button => {
    const batch = batchView.rows[Number(button.dataset.index)];
    button.onclick = () => openBatchMenu(button, batch, context);
    button.onkeydown = event => {
      if (['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); openBatchMenu(button, batch, context); }
    };
  });
  setBatchBusy();
}

function closeBatchMenu(restoreFocus = false) {
  const previous = batchView.menu; batchView.menu = null; $('#batchMenu').hidden = true;
  previous?.trigger.setAttribute('aria-expanded', 'false');
  if (restoreFocus && previous?.trigger.isConnected && !previous.trigger.disabled) previous.trigger.focus();
}

function openBatchMenu(trigger, batch, context) {
  if (!batchCurrent(context) || batchView.deleting || batchView.phase !== 'ready' || !trigger.isConnected
    || !$('#batchList').contains(trigger)) return;
  const toggling = batchView.menu?.trigger === trigger;
  closeBatchMenu(false); if (toggling) { trigger.focus(); return; }
  batchView.menu = { trigger, batch, context };
  trigger.setAttribute('aria-expanded', 'true'); $('#batchMenu').hidden = false;
  const anchor = trigger.getBoundingClientRect(), menu = $('#batchMenu').getBoundingClientRect();
  $('#batchMenu').style.left = Math.max(8, Math.min(window.innerWidth - menu.width - 8, anchor.right - menu.width)) + 'px';
  $('#batchMenu').style.top = Math.max(8, Math.min(window.innerHeight - menu.height - 8,
    anchor.bottom + menu.height + 8 <= window.innerHeight ? anchor.bottom + 4 : anchor.top - menu.height - 4)) + 'px';
  $('#batchDelete').focus();
}

async function deleteBatch(batch, context) {
  if (window.FundsMainAccountsActions?.blocked(context)) return;
  if (!batchCurrent(context) || batchView.deleting || !batchView.rows.some(row => String(row.id) === String(batch.id))) return;
  const trigger = batchView.menu?.trigger;
  closeBatchMenu(true);
  const task = batchView.deleting = { context, id: batch.id };
  const current = () => batchView.deleting === task && batchCurrent(context);
  setBatchBusy();
  let deleted = false;
  try {
    const accepted = await confirm(`删除批次「${batch.filename}」？\n\n将移除该批次的 ${fmtCount(batch.rows_imported ?? 0)} 笔流水及对应的图谱引用。此操作无法撤销。`);
    if (!current() || !accepted || window.FundsMainAccountsActions?.blocked(context)) return;
    showBatchFeedback('正在删除批次…');
    await batchRequest(`/api/batches/${encodeURIComponent(batch.id)}?case_id=${encodeURIComponent(context.caseId)}`, { method: 'DELETE' });
    if (!current()) return;
    deleted = true;
    batchView.request += 1; // Reads started before deletion cannot restore the removed batch.
    batchView.rows = batchView.rows.filter(row => String(row.id) !== String(batch.id));
    batchView.phase = batchView.rows.length ? 'ready' : 'empty'; renderBatchRows(context);
    toast('批次已删除');
    try { await refreshAll(); }
    catch (_) {
      if (current()) showBatchFeedback('批次已删除，工作区刷新未完成。', async () => {
        try { await refreshAll(); }
        catch (error) { if (batchCurrent(context)) showBatchFeedback('工作区刷新失败：' + batchError(error), () => loadBatches()); }
      }, '刷新工作区');
    }
  } catch (error) {
    if (current()) showBatchFeedback(error.status && error.status < 500
      ? '批次未能删除：' + batchError(error)
      : '删除结果暂未确认，请重新读取批次：' + batchError(error), () => loadBatches());
  } finally {
    if (current()) {
      batchView.deleting = null; setBatchBusy();
      if (!document.activeElement || document.activeElement === document.body || document.activeElement.isConnected === false) {
        const target = !deleted && trigger?.isConnected ? trigger : $('#btnBatchImport'); target.focus();
      }
    }
  }
}

function bindBatchUI() {
  if (batchView.bound) return; batchView.bound = true;
  $('#btnBatchImport').onclick = () => $('#btnImport').click();
  $('#batchDelete').onclick = () => {
    const menu = batchView.menu; if (menu) return deleteBatch(menu.batch, menu.context);
  };
  document.addEventListener('pointerdown', event => {
    const menu = batchView.menu;
    if (menu && !$('#batchMenu').contains(event.target) && !menu.trigger.contains(event.target)) closeBatchMenu(false);
  });
  document.addEventListener('focusin', event => {
    const menu = batchView.menu;
    if (menu && !$('#batchMenu').contains(event.target) && !menu.trigger.contains(event.target)) closeBatchMenu(false);
  });
  document.addEventListener('keydown', event => {
    if (!batchView.menu) return;
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeBatchMenu(true); }
    else if (event.key === 'Tab') closeBatchMenu(true);
    else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) { event.preventDefault(); $('#batchDelete').focus(); }
  });
  $('#batchListScroll').addEventListener('scroll', () => closeBatchMenu(false), { passive: true });
  window.addEventListener('resize', () => closeBatchMenu(false));
  batchView.scroll = bindTableHorizontalScroll({ viewport: $('#batchListScroll'), range: $('#batchHorizontalPosition'),
    left: $('#batchScrollLeft'), right: $('#batchScrollRight'), table: $('#batchTable') });
}

/* ---------------- 事件绑定 ---------------- */
function switchTab(name) {
  closeBatchMenu(false);
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  $('#tab-search').style.display = name === 'search' ? 'flex' : 'none';
  $('#tab-batches').style.display = name === 'batches' ? 'flex' : 'none';
  if (name === 'batches') loadBatches().catch(() => {});
}

/* One export task owns its captured case/canvas, even while the dialog is hidden. */
function installExportWorkspace(options) {
  const dialog = $('#exportActions');
  if (!dialog) return null;
  const inputs = [...dialog.querySelectorAll('input[name="exportFormat"]')];
  const formats = new Set(['png', 'html', 'pdf', 'xlsx']);
  const names = { png: 'PNG', html: 'HTML', pdf: 'PDF', xlsx: 'Excel' };
  const closeButton = $('#btnExportClose'), cancelButton = $('#btnExportCancel'), submitButton = $('#btnExportSubmit');
  const errorLabel = $('#exportError'), feedback = $('#exportFeedback'), busyLabel = $('#exportBusy');
  let context = null, format = 'png', phase = 'idle', pending = null, trigger = null, message = '';
  const same = (a, b) => a && b && ['caseId', 'canvasId', 'generation', 'canvasGeneration'].every(key => a[key] === b[key]);
  const count = value => fmtCount(Math.max(0, Number(value) || 0));
  function blocker(scope) {
    if (!context?.caseId) return '请先选择案件';
    if (window.FundsMainAccountsActions?.blocked(context)) return '主账号标记更新尚未结束，请先完成或核对';
    if (format === 'xlsx') return scope.trackedFlows > 0 ? '' : '当前画布暂无追踪流水';
    if (scope.pendingLayout) return '布局计算中，完成后可导出图谱';
    if (!scope.graphReady) return scope.graphAccounts > 0 ? '图谱加载中，完成后可导出' : '当前范围暂无图谱';
    return scope.graphAccounts > 0 ? '' : '当前范围暂无图谱';
  }
  function refresh() {
    if (!context || !options.isCurrent(context)) return;
    const scope = options.getScope(context), labels = options.getLabels(context);
    $('#exportContext').textContent = `当前案件：${labels.caseName} · 画布：${labels.canvasName}`;
    $('#exportScope').textContent = format === 'xlsx' ? '当前画布的全部追踪流水' : '当前图谱范围';
    $('#exportContents').textContent = format === 'xlsx'
      ? `${count(scope.trackedFlows)} 笔追踪流水 · 待调取账号`
      : `${count(scope.graphAccounts)} 个账户 · ${count(scope.graphRelations)} 条关系`;
    $('#exportPreserves').textContent = format === 'xlsx' ? '追踪流水、待调取账号两张工作表'
      : format === 'html' ? '当前布局、样式及当前可见标签' : '当前布局、样式及全部标签';
    for (const input of inputs) { input.checked = input.value === format; input.disabled = !!pending; }
    const locked = !!pending && phase === 'capturing';
    dialog.dataset.phase = phase;
    dialog.toggleAttribute('data-capture-locked', locked);
    dialog.setAttribute('aria-busy', pending ? 'true' : 'false');
    closeButton.disabled = cancelButton.disabled = locked;
    cancelButton.textContent = pending ? '收起' : '取消';
    submitButton.disabled = !!pending || !!blocker(scope);
    $('#exportSubmitLabel').textContent = pending ? (phase === 'capturing' ? '正在采样…' : '正在生成…') : `导出 ${names[format] || ''}`;
    errorLabel.hidden = phase !== 'error'; errorLabel.textContent = phase === 'error' ? message : '';
    feedback.textContent = phase === 'done' ? '已开始下载' : pending || phase === 'error' ? '' : blocker(scope);
    busyLabel.hidden = !pending;
    const busyText = busyLabel.querySelector('span');
    if (busyText) busyText.textContent = phase === 'capturing' ? '正在采样图谱…' : '正在生成文件，可收起此窗口';
  }
  function close() {
    if (pending && phase === 'capturing') return;
    if (dialog.open) dialog.close();
  }
  function revoke() {
    pending = null; phase = 'idle'; message = ''; context = null; format = 'png'; trigger = null;
    if (dialog.open) dialog.close();
  }
  function open(opener) {
    const next = options.getContext();
    if (!same(context, next)) { revoke(); context = { ...next }; }
    trigger = opener || trigger;
    if (!pending) { phase = 'idle'; message = ''; }
    refresh();
    if (!dialog.open) dialog.showModal();
  }
  async function submit(requestedFormat) {
    if (pending) return;
    const next = options.getContext();
    if (!same(context, next)) { revoke(); context = { ...next }; }
    const selected = requestedFormat || inputs.find(input => input.checked)?.value;
    if (!formats.has(selected)) { phase = 'error'; message = '请选择导出格式'; refresh(); return; }
    format = selected;
    if (format !== 'xlsx') options.setLegacyFormat?.(format);
    const reason = blocker(options.getScope(context));
    if (reason) { phase = 'error'; message = reason; refresh(); return; }
    message = ''; phase = format === 'xlsx' ? 'generating' : 'capturing';
    const job = { context: { ...context } };
    pending = job;
    job.isCurrent = () => pending === job && options.isCurrent(job.context);
    job.setPhase = value => {
      if (job.isCurrent() && ['capturing', 'generating'].includes(value)) { phase = value; refresh(); }
    };
    refresh();
    try {
      await options.execute(format, job);
      if (!job.isCurrent()) return;
      pending = null; phase = 'done'; refresh();
    } catch (error) {
      if (!job.isCurrent()) return;
      pending = null; phase = 'error';
      const detail = String(error?.message || '文件生成失败，请重试');
      message = /Failed to fetch|NetworkError|Load failed/i.test(detail) ? '连接失败，请重试' : detail.slice(0, 300);
      refresh();
    }
  }
  for (const input of inputs) input.addEventListener('change', () => {
    if (pending) return refresh();
    const selected = inputs.find(item => item.checked)?.value;
    if (!formats.has(selected)) { phase = 'error'; message = '请选择导出格式'; return refresh(); }
    format = selected; phase = 'idle'; message = '';
    if (format !== 'xlsx') options.setLegacyFormat?.(format);
    refresh();
  });
  closeButton.onclick = cancelButton.onclick = close;
  submitButton.onclick = () => submit();
  dialog.addEventListener('cancel', event => { if (pending && phase === 'capturing') event.preventDefault(); });
  dialog.addEventListener('close', () => {
    if (context && options.isCurrent(context) && trigger?.isConnected) trigger.focus();
  });
  return { open, submit, refresh, revoke, status: () => ({ phase, format, pending: !!pending,
    context: context ? { ...context } : null, dialogOpen: dialog.open }) };
}

function downloadBlob(blob, name) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

async function caseRequest(path, options = {}, binary = false) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = '';
    try { detail = await response.text(); try { detail = JSON.parse(detail).detail || detail; } catch (_) { /* text response */ } } catch (_) { /* status is still known */ }
    const failure = new Error(String(detail || `操作失败（HTTP ${response.status}）`).slice(0, 300));
    failure.status = response.status;
    throw failure;
  }
  return binary ? response.blob() : response.json();
}

let managedCaseListRequest = 0;
async function refreshManagedCases() {
  const request = ++managedCaseListRequest;
  const cases = await caseRequest('/api/cases');
  if (request !== managedCaseListRequest) return cases;
  state.cases = cases;
  const sel = $('#caseSelect');
  sel.innerHTML = cases.map(item => `<option value="${item.id}">${esc(item.name)}</option>`).join('');
  // Refresh the list without selecting a case or resetting any in-progress edits.
  sel.value = String(state.caseId);
  return cases;
}

async function openManagedCase(id, context, options = {}) {
  if (!isCurrentCase(context)) return false;
  if (window.FundsDataToolsActions?.blocked(context)) { toast('本案件资料更新尚未确认，请先完成数据工具中的操作', 'err'); return false; }
  if (window.FundsMainAccountsActions?.blocked(context)) { toast('主账号标记尚未确认，请先在主账号管理中核对', 'err'); return false; }
  if (!options.discardApproved && hasNodeDrafts() && !await leaveNodeDrafts()) return false;
  if (!isCurrentCase(context)) return false;
  selectCase(id);
  const opened = caseContext();
  try {
    await refreshStats();
    if (!isCurrentCase(opened)) return true;
    await refreshCanvasList(null);
    if (!isCurrentCase(opened)) return true;
    await loadGraph();
    if (!isCurrentCase(opened)) return true;
    await doSearch(1);
    if (isCurrentCase(opened) && $('#tab-batches').style.display === 'flex') await loadBatches();
  } catch (_) { if (isCurrentCase(opened)) toast('案件已打开，部分数据加载失败，请刷新后重试', 'err'); }
  return true;
}

function installCaseWorkspace() {
  return window.FundsCaseWorkspace.create({
    document, context: caseContext, isCurrent: isCurrentCase,
    readSummary: () => ({ name: state.cases.find(item => item.id === state.caseId)?.name || String(state.caseId || '未选择'),
      canvasName: state.canvasName || '默认画布', flows: state.stats.flows || 0,
      accounts: state.stats.accounts || 0, defaultCase: state.caseId === 1 }),
    listCases: refreshManagedCases,
    listCanvases: context => caseRequest(`/api/canvases?case_id=${context.caseId}`),
    createCase: name => caseRequest('/api/cases', { method: 'POST', ...jsonOpts({ name }) }),
    importCase: file => { const data = new FormData(); data.append('file', file);
      return caseRequest('/api/cases/import', { method: 'POST', body: data }); },
    deleteCase: id => {
      if (window.FundsMainAccountsActions?.blocked({ caseId: id })) throw new Error('主账号标记正在更新，请稍后删除案件');
      return caseRequest(`/api/cases/${id}`, { method: 'DELETE' });
    },
    exportCase: async id => {
      if (window.FundsDataToolsActions?.blocked({ caseId: id })) throw new Error('本案件资料正在更新，请稍后导出');
      if (window.FundsMainAccountsActions?.blocked({ caseId: id })) throw new Error('主账号标记正在更新，请稍后导出');
      return { blob: await caseRequest(`/api/cases/${id}/export`, {}, true), filename: `案件包_${id}.fundcase` };
    },
    saveCanvas: async (context, name) => window.FundsCanvasActions.withWrite(context, async () => {
      if (!isCurrentCase(context)) throw new Error('案件或画布已变化，请重新保存');
      if (layoutJob || state.graphLoading || window.FundsCanvasEditor?.history?.status?.().busy) throw new Error('画布正在处理，请稍后保存');
      const snapshot = await window.__collectSnapshot({ scope: 'all', embedPhotos: false });
      if (!isCurrentCase(context) || !snapshot) throw new Error('图谱已变化，请重新保存');
      let result;
      try {
        result = await caseRequest(context.canvasId ? '/api/canvases/save' : '/api/canvases/clone', {
          method: 'POST', ...jsonOpts({ case_id: context.caseId, name, snapshot,
            ...(context.canvasId ? { canvas_id: context.canvasId } : { from_canvas: 0 }) }) });
      } catch (error) { error.writeSent = true; throw error; }
      if (isCurrentCase(context)) {
        if (context.canvasId) state.canvasName = result.name;
        await refreshCanvasList(context.canvasId).catch(() => {
          if (isCurrentCase(context)) toast('画布已保存，画布列表暂未刷新', 'err');
        });
      }
      return result;
    }),
    openCase: openManagedCase,
    canLeave: async context => {
      if (window.FundsDataToolsActions?.blocked(context)) throw new Error('本案件资料更新尚未确认，请先完成数据工具中的操作');
      if (window.FundsMainAccountsActions?.blocked(context)) throw new Error('主账号标记尚未确认，请先在主账号管理中核对');
      return isCurrentCase(context) && (!hasNodeDrafts() || await leaveNodeDrafts()) && isCurrentCase(context);
    },
    download: downloadBlob, notify: toast,
  });
}

async function refreshAccountIdentity(context, authoritativeRows = null) {
  const binding = caseContext(), graph = state.graph, view = state.graphView, revision = view.revision;
  const rows = authoritativeRows || await caseRequest(`/api/account-ids/list?case_id=${encodeURIComponent(context.caseId)}`);
  if (!isCurrentCase(context, false) || !isCurrentCase(binding) || state.graph !== graph || state.graphView !== view || view.revision !== revision) return;
  const identities = new Map(rows.map(row => [String(row.account), row.id_no]));
  for (const node of state.lastGraph.nodes) node.id_no = identities.get(String(node.account)) || null;
  const nodes = new Map(state.lastGraph.nodes.map(node => [String(node.account), node]));
  const autoPaint = graph?.get?.('autoPaint') !== false;
  graph?.setAutoPaint?.(false);
  try { for (const item of graph?.getNodes?.() || []) {
    const model = item.getModel(), source = nodes.get(String(model.id));
    if (!source) continue;
    const text = nodeLabel(source), update = { data: source, fundsLabel: text };
    // Preserve LOD's absent label instead of revealing every hidden label.
    if (model.label !== undefined) update.label = text;
    graph.updateItem(item, update);
  } } finally { graph?.setAutoPaint?.(autoPaint); if (autoPaint) graph?.paint?.(); }
  for (const [key, draft] of nodeDrafts) {
    if (nodeDraftDirty(draft)) continue;
    let account; try { account = JSON.parse(key).at(-1); } catch (_) { continue; }
    draft.idBase = draft.idValue = identities.get(String(account)) || '';
  }
  if (nodeWorkspaceIsCurrent(nodeWorkspace) && nodeWorkspace.phase === 'ready' && !nodeDraftDirty(nodeWorkspace.draft)) {
    const saved = identities.get(String(nodeWorkspace.account)) || '';
    nodeWorkspace.node.id_no = saved || null;
    nodeWorkspace.draft.idBase = nodeWorkspace.draft.idValue = saved;
    if ($('#ndIdInput')) $('#ndIdInput').value = saved;
  }
  rebuildGraphViewport(); applyGraphLabels(); state.graphSelection?.refresh();
  syncNodeWorkspaceControls();
}

async function refreshMainAccountRoles(context, rows) {
  const binding = caseContext(), graph = state.graph, view = state.graphView, revision = view.revision;
  const fresh = await caseRequest(`/api/graph?case_id=${encodeURIComponent(context.caseId)}&canvas_id=${encodeURIComponent(binding.canvasId || 0)}`);
  if (!isCurrentCase(context, false) || !isCurrentCase(binding) || state.graph !== graph || state.graphView !== view || view.revision !== revision) return;
  if (!Array.isArray(fresh.nodes)) throw new Error('图谱账号资料未能读取');
  const roles = new Map(fresh.nodes.map(node => [String(node.account), node]));
  if (roles.size !== state.lastGraph.nodes.length || state.lastGraph.nodes.some(node => !roles.has(String(node.account)))) throw new Error('图谱已变化，请重新读取主账号资料');
  const selected = [...(state.graphSelection?.ids || [])];
  const nodeDetail = nodeWorkspaceIsCurrent(nodeWorkspace) ? nodeWorkspace : null;
  const edgeDetail = edgeWorkspaceIsCurrent(edgeWorkspace) ? edgeWorkspace : null;
  if (nodeDetail?.phase === 'ready') captureNodeDraft(nodeDetail);
  revokeGraphReads(); revokeZoomOptimization(); restoreGraphClipping();
  clearTimeout(view.labelTimer); view.labelTimer = null;
  if (view.viewportFrame != null) cancelAnimationFrame(view.viewportFrame);
  view.viewportFrame = null; view.revision += 1;
  state.mainAccounts = new Set(rows.map(row => String(row.account)));
  state.lastGraph.main_accounts = [...state.mainAccounts];
  for (const node of state.lastGraph.nodes) {
    const role = roles.get(String(node.account)); node.is_main = role.is_main === 1 ? 1 : 0; node.is_same_name = role.is_same_name === 1 ? 1 : 0;
  }
  const nodes = new Map(state.lastGraph.nodes.map(node => [String(node.account), node]));
  const autoPaint = graph?.get?.('autoPaint') !== false;
  let applied = false;
  const patch = () => {
    applied = true;
    for (const item of graph?.getNodes?.() || []) {
      const model = item.getModel(), source = nodes.get(String(model.id)); if (!source) continue;
      const text = nodeLabel(source), photo = nodePhotoUrl(source);
      const update = { data: source, fundsLabel: text, size: nodeSize(source), style: photo ? {} : nodeStyle(source) };
      if (model.label !== undefined) update.label = text;
      if (photo) update.img = photo;
      graph.updateItem(item, update);
    }
  };
  graph?.setAutoPaint?.(false);
  try {
    state.graphSelection?.rebind(selected, patch);
    if (!applied) patch();
    for (const edge of graph?.getEdges?.() || []) edge.refresh?.();
    syncNodeIdentityRings(); syncGraphEdgeLabels(); restoreEdgeLabelPositions();
    view.fullIndex = FundsGraphView.createIndex(state.lastGraph);
    const visible = new Set((graph?.getNodes?.() || []).map(item => String(item.getModel().id)));
    view.index = FundsGraphView.createIndex({ nodes: state.lastGraph.nodes.filter(node => visible.has(String(node.account))),
      edges: state.lastGraph.edges.filter(edge => visible.has(String(edge.source)) && visible.has(String(edge.target))) });
    rebuildGraphViewport(); applyGraphLabels(); state.graphSelection?.refresh();
  } finally { graph?.setAutoPaint?.(autoPaint); if (autoPaint) graph?.paint?.(); }
  if (nodeDetail) {
    nodeDetail.revision = view.revision; nodeDetail.readSequence += 1;
    nodeDetail.node = { ...nodeDetail.node, ...(nodes.get(String(nodeDetail.account)) || {}) };
    nodeDetail.mainInfo = rows.find(row => row.account === nodeDetail.account);
    if (nodeDetail.phase === 'ready') { nodeDetail.scroll?.destroy(); renderNodeWorkspace(nodeDetail); }
    else if (nodeDetail.phase === 'loading') await loadNodeWorkspace(nodeDetail);
  }
  if (edgeDetail) edgeDetail.revision = view.revision;
  updateGraphViewStatus(); syncNodeWorkspaceControls();
}

function installMainAccounts() {
  function blocker(context) {
    if (!context.caseId || !isCurrentCase(context, false)) return '请先选择案件';
    if (window.FundsDataToolsActions?.blocked(context)) return '数据工具操作尚未结束，请先完成或核对';
    const cases = caseWorkspace?.status(), canvas = window.FundsCanvasActions?.status(), exported = exportWorkspace?.status();
    if (cases?.pending || cases?.phase === 'unknown' || canvas?.pending || canvas?.unknown || exported?.pending) return '案件或画布正在处理，请稍后操作';
    if ([...accountDataWrites].some(write => String(write.caseId) === String(context.caseId)) || nodeWrites.size || pcState.binding || nodeConfirmation || nodeWorkspace?.confirming) return '账户资料正在处理，请稍后操作';
    if (['loading','removing'].includes(nodeWorkspace?.phase) || batchView.deleting) return '案件资料正在处理，请稍后操作';
    if (layoutJob || state.graphLoading || state.graphView.exportDepth || state.graphSelection?.status?.().dragging || window.FundsCanvasEditor?.history?.status?.().busy) return '画布正在处理，请稍后操作';
    return '';
  }
  const view = window.FundsMainAccountsView.create(document, { isCurrent: isCurrentCase,
    readCaseName: context => state.cases.find(row => String(row.id) === String(context.caseId))?.name || `案件 #${context.caseId}` });
  const actions = window.FundsMainAccounts.create({
    context: caseContext, isCurrent: isCurrentCase, ready: (_action, context) => !blocker(context),
    canWrite: async context => {
      if (hasNodeDrafts()) throw new Error('账户资料有未保存修改，请先保存或放弃后再更改主账号');
      const reason = blocker(context); if (reason) throw new Error(reason);
      if (window.FundsImportWorkflow && !await window.FundsImportWorkflow.canWrite(context)) throw new Error('流水导入尚未结束，请先在导入窗口核对结果');
      if (!isCurrentCase(context, false)) return false;
      const after = blocker(context); if (after) throw new Error(after);
      return true;
    },
    request: caseRequest, show: view.show, dismiss: view.dismiss, publish: refreshMainAccountRoles,
    onState: next => {
      view.onState(next); syncNodeWorkspaceControls(); dataToolsWorkspace?.sync();
      // Refresh the shared history/membership controls from the same lock.
      window.dispatchEvent(new CustomEvent('funds:main-accounts-state', { detail: { blocked: next.pending || next.unknown } }));
    }
  });
  view.attach(actions);
  $('#btnMainActions').onclick = () => {
    if (!actions.open($('#btnMainActions'))) toast(blocker(caseContext()) || '其他案件的主账号操作尚未结束，请先核对', 'err');
  };
  return actions;
}

async function manageNodeMainAccount(workspace) {
  const actions = window.FundsMainAccountsActions;
  if (!actions || !nodeWorkspaceIsCurrent(workspace)) return;
  if (!actions.open($('#btnMainActions'))) { toast('案件或账户资料正在处理，请稍后操作', 'err'); return; }
  if (actions.status().pending || actions.status().unknown || !await actions.refresh() || !nodeWorkspaceIsCurrent(workspace)) return;
  const row = actions.status().rows.find(item => item.account === workspace.account);
  if (row) return actions.prepare('remove', row);
  actions.showSearch();
  if (await actions.search(workspace.account, 1, true) && nodeWorkspaceIsCurrent(workspace)) {
    const candidate = actions.status().candidates.find(item => item.account === workspace.account);
    if (candidate) return actions.prepare('manual', candidate);
  }
}

function installDataTools() {
  const sameCase = context => String(context?.caseId) === String(state.caseId);
  const caseBusy = context => {
    const value = caseWorkspace?.status();
    return String(value?.context?.caseId) === String(context.caseId) && (value.pending || value.phase === 'unknown');
  };
  const ready = (_action, context) => isCurrentCase(context, false) && !caseBusy(context)
    && !window.FundsMainAccountsActions?.blocked(context)
    && ![...accountDataWrites].some(write => String(write.caseId) === String(context.caseId))
    && !nodeWrites.size && !pcState.binding && !nodeConfirmation && !nodeWorkspace?.confirming
    && nodeWorkspace?.phase !== 'removing' && !layoutJob && !state.graphLoading
    && !state.graphView.exportDepth && !window.FundsCanvasEditor?.history?.status?.().busy;
  const view = window.FundsDataTools.createView(document, {
    context: caseContext, isCurrent: isCurrentCase,
    readCaseName: context => state.cases.find(row => String(row.id) === String(context.caseId))?.name || `案件 #${context.caseId}`
  });
  const actions = window.FundsDataTools.create({
    context: caseContext, isCurrent: isCurrentCase, ready,
    canWrite: async (context, action) => {
      if (hasNodeDrafts()) throw new Error(`账户资料有未保存修改，请先保存或放弃后再${action === 'demo' ? '加入演示数据' : '导入'}`);
      if (!ready('import', context)) throw new Error('案件或账户资料正在处理，请稍后操作');
      if (window.FundsImportWorkflow && !await window.FundsImportWorkflow.canWrite(context)) throw new Error('流水导入尚未结束，请先在导入窗口核对结果');
      return isCurrentCase(context, false) && ready('import', context);
    },
    request: caseRequest, show: view.show, dismiss: view.dismiss, pickFile: view.pickFile,
    notify: toast,
    onState: value => {
      view.onState(value); syncNodeWorkspaceControls();
      const otherCase = value.blocked && !sameCase(value.operationContext);
      for (const id of ['btnImportIds', 'btnSample']) {
        const row = $('#' + id);
        // Own pending/unknown work must remain reachable to reopen its status.
        const disabled = otherCase || (!value.blocked && !ready('import', caseContext()));
        row.setAttribute('aria-disabled', String(disabled));
        row.title = disabled ? otherCase ? '其他案件的数据工具操作尚未结束' : '案件资料正在处理，请稍后操作' : '';
      }
      $('#btnIdTpl').setAttribute('aria-disabled', String(value.templatePending));
      $('#btnSample2').disabled = value.blocked || !ready('demo', caseContext());
    },
    publish: async (action, context) => {
      if (!isCurrentCase(context, false)) return;
      if (action === 'import') return refreshAccountIdentity(context);
      await refreshStats();
      if (!isCurrentCase(context, false)) return;
      await doSearch(state.search.page || 1);
      if (isCurrentCase(context, false)) await loadBatches();
    },
    reconcile: async (action, context) => {
      const reader = caseContext();
      const rows = await caseRequest(`${action === 'import' ? '/api/account-ids/list' : '/api/batches'}?case_id=${encodeURIComponent(context.caseId)}`);
      if (!isCurrentCase(reader) || String(reader.caseId) !== String(context.caseId)) return rows;
      // A read confirms current facts, not whether the unknown write committed.
      if (action === 'import') await refreshAccountIdentity(reader, rows);
      else {
        await refreshStats();
        if (isCurrentCase(reader)) await doSearch(state.search.page || 1);
        if (isCurrentCase(reader)) await loadBatches();
      }
      return rows;
    },
    downloadTemplate: async context => {
      const blob = await caseRequest('/api/account-ids/template', {}, true);
      if (isCurrentCase(context, false)) downloadBlob(blob, '账号身份证对照表模板.xlsx');
    }
  });
  view.attach(actions);
  $('#btnImportIds').onclick = () => actions.open('import', $('#btnDataActions'));
  $('#btnIdTpl').onclick = () => actions.downloadTemplate();
  $('#btnSample').onclick = () => actions.open('demo', $('#btnDataActions'));
  document.addEventListener('funds:toolbar-menu-change', event => {
    if (event.detail?.open && event.detail.menuId === 'dataActions') actions.sync();
  });
  window.addEventListener('funds:graph-metric', () => actions.sync());
  return actions;
}

function bindWorkspaceCamera() {
  window.addEventListener('fundstrace:workspace-resize', event => {
    const detail=event.detail||{};
    if(detail.phase==='prepare' || detail.phase==='start') {
      // A group-drag preview caches canvas-local coordinates. Finish it before
      // chrome starts moving, retaining the selection and committed geometry.
      if(state.graphSelection?.status?.().dragging)state.graphSelection.rebind([...state.graphSelection.ids]);
    }
    if(detail.reason==='sidebar') {
      if(detail.phase==='prepare')state.graphCamera?.beginSidebar?.({collapsed:detail.sidebarCollapsed});
      if(detail.phase==='end')state.graphCamera?.endSidebar?.();
    } else if(detail.phase==='start')state.graphCamera?.cancelComposition?.();
  });
}

function bindWorkspaceActions() {
  const triggers = new Map();
  $$('.action-dialog').forEach(dialog => {
    if (dialog.id === 'exportActions' || dialog.id === 'caseActions') return;
    dialog.querySelectorAll('[data-dialog-close]').forEach(button => { button.onclick = () => dialog.close(); });
    // Close before the existing action runs, so text inputs/file pickers are not behind the native dialog.
    dialog.addEventListener('click', event => {
      if (event.target.closest?.('[data-dialog-action]')) dialog.close();
    }, true);
    dialog.addEventListener('close', () => {
      const trigger = triggers.get(dialog); triggers.delete(dialog); trigger?.focus();
    });
  });
  $$('[data-action-dialog]').forEach(button => {
    button.onclick = () => {
      window.FundsToolbarMenu?.close('dialog');
      if (button.dataset.actionDialog === 'exportActions') return exportWorkspace?.open(button);
      if (button.dataset.actionDialog === 'caseActions') return caseWorkspace?.open(button);
      const dialog = document.querySelector('#' + button.dataset.actionDialog);
      if (!dialog || dialog.open) return;
      const current = state.cases.find(item => item.id === state.caseId);
      const caseName = current?.name || String(state.caseId || '未选择');
      dialog.querySelectorAll('[data-current-case]').forEach(label => {
        label.textContent = `当前案件：${caseName} · 画布：${state.canvasName || '默认画布'}`;
      });
      triggers.set(dialog, button); dialog.showModal();
    };
  });
}

function bindUI() {
  caseWorkspace = installCaseWorkspace();
  bindWorkspaceActions();
  bindFlowTableView();
  bindFlowTableScroll();
  bindBatchUI();
  // 左侧 tab
  $$('.tab').forEach(t => t.onclick = () => switchTab(t.dataset.tab));

  // 查询
  $('#btnSearch').onclick = () => doSearch(1);
  $('#btnReset').onclick = () => {
    ['#fAccount', '#fName', '#fCounter', '#fKw', '#fMin', '#fMax', '#fFrom', '#fTo'].forEach(s => $(s).value = '');
    doSearch(1);
  };
  $('#btnPrev').onclick = () => { if (state.search.page > 1) doSearch(state.search.page - 1); };
  $('#btnNext').onclick = () => doSearch(state.search.page + 1);
  $('#flowTbody').addEventListener('click', e => {
    const tr = e.target.closest('tr[data-id]');
    if (!tr) return;
    const id = Number(tr.dataset.id);
    if (e.target.closest('.act-track')) { trackFlows([id]); return; }
    openContext(id);
  });
  $('#flowTbody').addEventListener('keydown', event => {
    const row = event.target.closest('tr[data-id]');
    if (!row || event.target !== row) return;
    if (event.key === 'Enter') { event.preventDefault(); openContext(Number(row.dataset.id)); }
    if (event.key === ' ') {
      event.preventDefault(); const checkbox = row.querySelector('.flow-ck');
      if (checkbox) { checkbox.checked = !checkbox.checked; syncFlowCkCount(); }
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      const next = event.key === 'ArrowDown' ? row.nextElementSibling : row.previousElementSibling;
      if (next?.matches('tr[data-id]')) { event.preventDefault(); next.focus(); }
    }
  });
  // 勾选/全选（checkbox 的 click 会冒泡，但 stopPropagation 防止触发行点击）
  $('#flowTbody').addEventListener('change', e => {
    if (e.target.classList.contains('flow-ck')) syncFlowCkCount();
  });
  $('#flowTbody').addEventListener('click', e => {
    if (e.target.classList.contains('flow-ck')) e.stopPropagation();
  }, true);
  $('#flowCkAll').onchange = e => {
    $$('#flowTbody .flow-ck').forEach(c => { c.checked = e.target.checked; });
    syncFlowCkCount();
  };
  // 批量追踪：勾选的每笔流水自动取来源/去向的最近 X 笔（仅本方账单）上图
  $('#btnBulkTrack').onclick = async () => {
    const ids = $$('#flowTbody .flow-ck').filter(c => c.checked).map(c => Number(c.dataset.id));
    if (!ids.length) return toast('请先勾选要追踪的流水', 'err');
    const dir = $('#bulkDir').value;
    const x = Math.max(1, Math.min(50, Number($('#bulkX').value) || 5));
    const btn = $('#btnBulkTrack');
    btn.disabled = true;
    try {
      const r = await api('/api/track/bulk', { method: 'POST',
        ...jsonOpts({ case_id: state.caseId, flow_ids: ids, direction: dir, x, canvas_id: state.canvasId || 0 }) });
      const dirLabel = dir === 'source' ? '来源' : '去向';
      const notMain = (r.skipped_not_main || []).length;
      const zero = (r.detail || []).filter(d => d.picked === 0 && d.is_main).length;
      let msg = `批量追踪完成：${r.anchors} 笔交易及其${dirLabel}流水共上图 ${r.tracked} 笔`;
      if (notMain) msg += `；${notMain} 笔被跳过（${dirLabel === '来源' ? '付款' : '收款'}方不是主账号，无法追踪）`;
      if (zero) msg += `；${zero} 笔锚点在本方账单中无${dirLabel}流水（仅上图了本笔）`;
      toast(msg);
      state.forceFit = false;
      await loadGraph();
      await doSearch(state.search.page || 1);
    } catch (err2) {
      toast('批量追踪失败：' + err2.message, 'err');
    } finally {
      btn.disabled = false;
    }
  };

  // Changing cases is explicit and retains the captured generation across draft confirmation.
  $('#caseSelect').onchange = async e => {
    const value = e.target.value, context = caseContext();
    e.target.value = String(state.caseId);
    await openManagedCase(value, context);
  };

  $('#graphLabelMode').onchange = event => {
    state.graphView.labelMode = ['auto','focus','all'].includes(event.target.value) ? event.target.value : 'auto';
    scheduleGraphLabels();
  };
  if (!window.FundsNeighborhood) {
    $('#btnNeighborhood').onclick = () => exploreNeighborhood($('#neighborAccount').value.trim(), true, { direction: $('#neighborDirection').value });
    $('#neighborAccount').onkeydown = event => { if (event.key === 'Enter' && !event.isComposing && !event.repeat && event.keyCode !== 229) exploreNeighborhood(event.target.value.trim(), true, { direction: $('#neighborDirection').value }); };
    $('#btnExpandNeighbors').onclick = () => exploreNeighborhood(state.graphView.root);
  }
  $('#btnGraphOverview').onclick = showGraphOverview;
  if ($('#btnMinimap')) $('#btnMinimap').onclick = () => {
    const panel=$('#graphMinimapPanel'); panel.hidden=!panel.hidden;
    $('#btnMinimap').setAttribute('aria-pressed',String(!panel.hidden));
  };
  $$('#graphMinimapPanel [data-pan]').forEach(button => {
    button.onclick=() => {
      if (!graphCameraInteractionAllowed()) return;
      state.graphCamera?.resize();
      state.graphCamera?.cancelComposition?.();
      const pan={left:[100,0],right:[-100,0],up:[0,100],down:[0,-100]}[button.dataset.pan];
      state.graph.translate(...pan); scheduleGraphViewport(); scheduleGraphLabels();
    };
  });
  // 图谱工具
  $('#layoutSel').onchange = e => { const previous=state.layout;state.layout = e.target.value; return relayout(true,false,previous); };
  initEdgeLabels();
  $$('#edgeLabelGroup .edge-ck').forEach(ck => {
    ck.checked = state.edgeLabels.has(ck.dataset.em);
    ck.onchange = () => {
      if (ck.checked) state.edgeLabels.add(ck.dataset.em);
      else state.edgeLabels.delete(ck.dataset.em);
      if (!state.edgeLabels.size) {  // 至少保留一项
        state.edgeLabels.add('money');
        $$('#edgeLabelGroup .edge-ck').forEach(c => { if (c.dataset.em === 'money') c.checked = true; });
      }
      saveEdgeLabels();
      renderGraph();
    };
  });
  // 身份证号显示开关（节点标签第三行）
  const idCk = $('#showIdCk');
  if (idCk) {
    idCk.checked = state.showIds;
    idCk.onchange = () => {
      state.showIds = idCk.checked;
      preferenceStorage.setItem('fund_show_ids', state.showIds ? '1' : '0');
      renderGraph();
    };
  }
  // 节点照片显示开关
  const phCk = $('#showPhotoCk');
  if (phCk) {
    phCk.checked = state.showPhotos;
    phCk.onchange = () => {
      state.showPhotos = phCk.checked;
      preferenceStorage.setItem('fund_show_photos', state.showPhotos ? '1' : '0');
      state.forceRebuild = true;   // 照片节点与圆点互切需整图重建
      renderGraph();
    };
  }
  bindPhotoCrop();
  bindCanvases();
  dataToolsWorkspace = installDataTools();
  bindWorkspaceCamera();
  window.FundsDataToolsActions = dataToolsWorkspace;
  dataToolsWorkspace.sync();
  mainAccountsWorkspace = installMainAccounts();
  window.FundsMainAccountsActions = mainAccountsWorkspace;
  mainAccountsWorkspace.sync();
  syncEdgeTypeControl();
  $('#edgeTypeSel').onchange = e => {
    const value=e.target.value,key=state.graphView.selectedEdge;
    if(!key || !['line','polyline','quadratic'].includes(value) || _selItem?.getModel?.().source===_selItem?.getModel?.().target)return;
    const work=()=>{
      captureEdgeLabelRatios();savePositions();state.edgeTypes[key]=value;state.forceFit=false;renderGraph();
    };
    if(window.FundsCanvasEditor?.command)return window.FundsCanvasEditor.command('修改所选关系线型',work);
    return work();
  };
  $('#btnRelayout').onclick = () => relayout(true);
  $('#btnArrangeLabels').onclick = () => {const previous=state.layout;state.layout='structural';window.FundsCanvasEditor?.syncControls?.();return relayout(true,true,previous);};
  $('#btnFit').onclick = () => {if(graphCameraInteractionAllowed())fitGraph(1.6);};
  $('#btnClearGraph').onclick = async () => {
    const where = state.canvasName ? `画布「${state.canvasName}」` : '默认画布';
    if (!confirm(`清空${where}的全部追踪（已上图流水）？其他画布不受影响。`)) return;
    await api(`/api/graph?case_id=${state.caseId}&canvas_id=${state.canvasId || 0}`, { method: 'DELETE' });
    state.positions = {};
    state.edgeLabelPos = {}; state.edgeBendPos = {}; state.edgeSnapshotLabels = {}; state.edgeTypes = {};
    toast(`${where}已清空`);
    await loadGraph();
  };

  // PNG and PDF share one mutable G6 canvas. Queue only the sampling step;
  // each job owns its dimensions/camera and relinquishes them before the next.
  const imageExportQueue = [];
  let imageExportActive = false;
  function drainImageExports() {
    if (imageExportActive || !imageExportQueue.length) return;
    const { scale, current, resolve } = imageExportQueue.shift();
    if (!current()) { resolve(null); return drainImageExports(); }
    imageExportActive = true;
    state.graphCamera?.resize();
    const c = $('#graphContainer'), W = c.clientWidth, H = c.clientHeight;
    const graph = state.graph, view = state.graphView;
    const originalMaxZoom = graph.get?.('maxZoom');
    const samplingZoom = typeof graph.set === 'function' && Number.isFinite(originalMaxZoom) && originalMaxZoom > 0;
    const selection=state.graphSelection,visualsVisible=selection?.status?.().visualsVisible!==false;
    const selectedEdgeKey=view.selectedEdge,selectedEdge=selectedEdgeKey&&view.edgeItems.get(selectedEdgeKey);
    const selectedOutline=(selectedEdge?.getContainer?.()?.get?.('children')||[]).find(shape=>shape.get('name')==='funds-edge-selection-outline');
    const edgeOutlineVisible=selectedOutline?.get?.('visible')!==false;
    const recent=(graph.getNodes?.()||[]).filter(item=>item.hasState?.('recent')||(item.getStates?.()||[]).includes('recent'));
    const footerLines=graphImageExportLines(),wrapped=[];
    const context=document.createElement('canvas').getContext?.('2d');
    if(context)context.font='12px Arial';
    for(const line of footerLines) {
      let row='';for(const char of line) {
        const next=row+char,width=context?.measureText?.(next)?.width??Array.from(next).length*8;
        if(row&&width>W-32){wrapped.push(row);row=char;}else row=next;
      }
      wrapped.push(row);
    }
    const footerHeight=(wrapped.length*18+24)*scale;
    let footer=null,exportGeometry=null,camera = null, finished = false, timer;
    const done = url => {
      if (finished) return;
      finished = true; clearTimeout(timer);
      if (cancelGraphImageExport === cancel) cancelGraphImageExport = null;
      const owned = current();
      try{footer?.remove?.();exportGeometry?.restore();}catch(_){/* disposed canvas */}
      if (samplingZoom) { try { graph.set('maxZoom', originalMaxZoom); } catch (_) { /* disposed sampling graph */ } }
      view.exportDepth = Math.max(0, view.exportDepth - 1);view.exportFooterHeight=0;delete view.exportLabelScale;
      if (owned) {
        try { graph.changeSize(W, H); } catch (_) { /* Release ownership even if G6 restoration fails. */ }
        if (camera) {
          try { graph.get('group').setMatrix(camera); graph.paint(); } catch (_) { /* The graph may be disposing. */ }
          try { applyGraphViewport(); applyGraphLabels(); } catch (_) { /* Keep the queue usable. */ }
        } else { try { fitGraph(1.6); } catch (_) { /* Older host adapters have no camera API. */ } }
        for(const item of recent){try{graph.setItemState(item,'recent',true);}catch(_){/* removed */}}
        try{applyPendingGraphRecent(view,graph);}catch(_){/* Preserve queue ownership on redraw failure. */}
        try{selection?.setVisualsVisible?.(visualsVisible);}catch(_){/* Restore independent adornments even if an adapter fails. */}
        try{
          refreshSelectedEdgeAppearance();
          if(!edgeOutlineVisible&&view.selectedEdge===selectedEdgeKey) {
            (selectedEdge?.getContainer?.()?.get?.('children')||[]).find(shape=>shape.get('name')==='funds-edge-selection-outline')?.hide?.();
          }
          graph.paint();
        }catch(_){/* A rendering failure must not lock the export queue. */}
      }
      state.graphCamera?.resume();
      imageExportActive = false;
      resolve(owned ? url : null);
      Promise.resolve().then(drainImageExports);
    };
    const cancel = () => done(null);
    cancelGraphImageExport = cancel;
    view.exportDepth += 1;view.exportFooterHeight=footerHeight;view.exportLabelScale=scale;
    try {
      selection?.setVisualsVisible?.(false);
      for(const item of recent)graph.setItemState(item,'recent',false);
      const group=graph.get?.('group'),matrix=group?.getMatrix?.();
      // G6 represents an untouched identity transform as null. It is still a
      // real camera to restore, unlike an older host with no matrix API.
      camera = matrix ? Array.from(matrix) : typeof group?.setMatrix==='function'&&typeof group?.getMatrix==='function'
        ? [1,0,0,0,1,0,0,0,1] : null;
      restoreGraphClipping(view);
      graph.changeSize(W * scale, H * scale);
      if (samplingZoom) graph.set('maxZoom', originalMaxZoom * scale);
      graph.fitView([40*scale,40*scale,footerHeight+40*scale,40*scale]);
      applyGraphLabels();
      exportGeometry=window.FundsGraphExportGeometry?.prepare({
        graph,width:W*scale,height:H*scale,footerHeight,scale,
        applyLabels:()=>applyGraphLabels(false),
        labelBounds:(item,kind)=>graphLabelBounds(item,kind,true),
        bodyBounds:graphNodeBodyBounds,itemBounds:graphItemBounds,unprojector:graphUnprojector,
        fitViewport:window.FundsGraphLayout?.fitViewport,
        restoreEdge:item=>{
          window.FundsGraphLabelGeometry?.restoreEdge(item,{style:graphRelationStyle(item.getModel())});
          return ()=>{if(current())syncGraphEdgeLabel(item);};
        },
      });
      footer=graph.get?.('canvas')?.addGroup?.({name:'funds-export-legend',capture:false});
      if(footer) {
        footer.addShape('rect',{capture:false,attrs:{x:0,y:H*scale-footerHeight,width:W*scale,height:footerHeight,fill:'#111c26'}});
        footer.addShape('text',{capture:false,attrs:{x:16*scale,y:H*scale-footerHeight+12*scale,text:wrapped.join('\n'),fontSize:12*scale,lineHeight:18*scale,fontFamily:'Arial',fill:'#c3d2df',textBaseline:'top'}});
        graph.paint();
      }
      timer = setTimeout(() => {
        if (finished) return;
        if (!current()) return done(null);
        try { done(graph.toDataURL('image/png','#111a23')); } catch (_) { done(null); }
      }, 400);
    } catch (_) { done(null); }
  }
  function exportHiResPng(scale = 2, current = () => true) {
    return new Promise(resolve => { imageExportQueue.push({ scale, current, resolve }); drainImageExports(); });
  }



  // 采集当前画布快照：节点坐标/样式、边线型/折点、标注内容及其拖动位置（所见即所得导出用）
  async function collectGraphSnapshot(options = {}) {
    const g = state.graph, context = caseContext(), revision = layoutSequence, view = state.graphView, viewRevision = view.revision;
    const current = () => isCurrentCase(context) && state.graph === g && layoutSequence === revision
      && view === state.graphView && viewRevision === view.revision;
    if (!g || !state.graphInited || state.graphLoading || layoutJob) return null;
    const sel = $('#caseSelect');
    const caseName = sel?.selectedOptions?.[0]?.textContent
      || state.cases.find(item => item.id === context.caseId)?.name || '';
    const container = $('#graphContainer'), width = container.clientWidth, height = container.clientHeight;
    const metadata = (source, keys) => {
      const result = {};
      for (const key of keys) if (source[key] !== undefined) result[key] = source[key];
      // Copy canonical strings and arrays before any photo fetch can yield.
      return JSON.parse(JSON.stringify(result));
    };
    const scope = options.scope === 'all' ? 'all' : 'visible', embedPhotos=options.embedPhotos!==false;
    const liveNodes = g.getNodes(), liveEdges = g.getEdges();
    const nodeItems=new Map(liveNodes.map(item=>[item.getModel().id,item]));
    const nodeModels = new Map(liveNodes.map(item => [item.getModel().id, item.getModel()]));
    const edgeItems = new Map(liveEdges.map(item => [edgeKey(item.getModel()), item]));
    const nodeSources = scope === 'all' ? state.lastGraph.nodes.map(source => {
      const live = nodeModels.get(source.account);
      if (live) return live;
      const position = state.positions[source.account] || FundsGraphLayout.seed(source.account, width, height);
      const size=nodeSize(source);
      return {id: source.account, ...position, label: nodeLabel(source), size,
        style: nodePhotoUrl(source) ? {} : nodeStyle(source), data: source};
    }) : liveNodes.map(item => item.getModel());
    const prepared = nodeSources.map(m => {
      const source = m.data || {},item=nodeItems.get(m.id);
      const offset=labelShapes(item||{}).find(shape=>shape.get('name')==='text-shape')?._fundsLabelOffset;
      const custom=state.presentation.nodeStyles?.[m.id];
      return { photoUrl: embedPhotos?nodePhotoUrl(source):null, node: {
        id: m.id, x: Math.round((m.x || 0) * 100) / 100, y: Math.round((m.y || 0) * 100) / 100,
        label: m.fundsLabel ?? m.label ?? nodeLabel(source), size: m.size || 32, style: (m.style && { ...m.style }) || {},
        labelStyle: effectiveGraphLabelStyle('node',m.id),
        labelOffset:{x:offset?offset.x-offset.baseX:0,y:offset?offset.y-offset.baseY:0},
        ...(nodePhotoUrl(source)&&custom?{photoBorder:{stroke:custom.stroke||nodeRing(source),lineWidth:custom.lineWidth||1.5}}:{}),
        labelVisible: scope === 'all' || !state.graphView.policy || state.graphView.policy.nodeIds.has(m.id),
        data: metadata(source, ['out_cnt', 'in_cnt', 'out_amt', 'in_amt', 'out_totals', 'in_totals', 'is_main', 'is_same_name']),
      } };
    });
    const edgeSources = scope === 'all' ? state.lastGraph.edges : liveEdges.map(item => item.getModel().data || item.getModel());
    const edges = edgeSources.map(source => {
      const item = edgeItems.get(edgeKey(source));
      const m = item?.getModel() || { ...source, label: edgeLabel(source), data: source,
        ...graphEdgeRoute(source,new Map(Object.entries(state.positions)),graphHasSimpleGeometry()) };
      const children = item?.getContainer?.()?.get('children') || [];
      const text = children.find(child => child.get('name') === 'text-shape');
      const background = children.find(child => child.get('name') === 'text-bg-shape');
      const key=edgeKey(m),savedLabel = state.edgeSnapshotLabels[key] || {};
      const ratio=state.edgeLabelPos[key], hasRatio=Number.isFinite(ratio)&&ratio>=0&&ratio<=1;
      const manual=hasRatio || savedLabel.manual!==false&&savedLabel.lx!=null;
      const offset=text?._fundsLabelOffset;
      return {
        source: m.source, target: m.target, type: m.type || state.edgeType,
        curvePolicy:m.curvePolicy||null,labelPlacement:m.labelPlacement||'inline',
        routeManual:Object.prototype.hasOwnProperty.call(state.edgeTypes,edgeKey(m))||!!state.edgeBendPos[edgeKey(m)]?.length,
        routeAutoEligible:scope==='all'||(!view.visibleIds&&!graphViewIsFiltered())||graphHasSimpleGeometry(),
        style: graphRelationStyle(source, false), labelStyle: effectiveGraphLabelStyle('edge',edgeKey(source)),
        labelRatio:hasRatio?ratio:null,labelManual:manual,
        labelOffset:{x:offset?offset.x-offset.baseX:0,y:offset?offset.y-offset.baseY:0},
        controlPoints: (m.controlPoints || []).map(point => ({ x: +(+point.x).toFixed(2), y: +(+point.y).toFixed(2) })),
        label: m.fundsLabel ?? m.label ?? edgeLabel(m.data || source),
        labelVisible: scope === 'all' || !state.graphView.policy || state.graphView.policy.edgeKeys.has(edgeKey(m)),
        lx: !item&&hasRatio?null:text ? +(text.attr('x') || 0).toFixed(2) : savedLabel.lx ?? null,
        ly: !item&&hasRatio?null:text ? +(text.attr('y') || 0).toFixed(2) : savedLabel.ly ?? null,
        bw: background != null ? background.attr('width') : savedLabel.bw ?? null,
        bh: background != null ? background.attr('height') : savedLabel.bh ?? null,
        data: metadata(m.data || {}, ['cnt', 'ids', 'amount_totals', 'total', 'currencies']),
      };
    });
    for (const entry of prepared) {
      if (!current()) return null;
      if (!entry.photoUrl) continue;
      try {
        const response = await fetch(entry.photoUrl);
        if (!current()) return null;
        const blob = await response.blob();
        if (!current()) return null;
        entry.node.img = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(blob);
        });
        if (!current()) return null;
      } catch (_) { if (!current()) return null; /* Photo failure keeps a circle. */ }
    }
    const relationFilter = state.presentation.relationFilter;
    return current() ? { case_name: caseName, w: width, h: height, nodes: prepared.map(entry => entry.node), edges,
      presentation: newGraphPresentation(state.presentation),
      display: { layout:state.layout,edgeType:state.edgeType,edgeLabels:[...state.edgeLabels],showIds:state.showIds,showPhotos:state.showPhotos,
        labelMode:view.labelMode,labelRenderPolicy:'clean-midpoint-v1' },
      view: {scope, mode: graphViewIsFiltered() ? 'filtered' : state.graphView.visibleIds ? 'neighborhood' : 'overview',
        ...(view.amountWidths?{amount_width:graphAmountWidthMetadata()}:{}),
        ...(relationFilter.active ? {relation_filter:{currency:relationFilter.currency,min:relationFilter.min||null,max:relationFilter.max||null}} : {}),
        total_nodes: state.lastGraph.nodes.length, total_edges: state.lastGraph.edges.length,
        visible_nodes: liveNodes.length, visible_edges: liveEdges.length, label_mode: state.graphView.labelMode} } : null;
  }

  window.__collectSnapshot = collectGraphSnapshot;   // 供调试/自动化取快照

  async function exportResponse(response, current) {
    if (!current()) return null;
    if (!response.ok) {
      let detail = '';
      try { detail = await response.text(); try { detail = JSON.parse(detail).detail || detail; } catch (_) { /* plain text */ } } catch (_) { /* HTTP status remains useful */ }
      if (!current()) return null;
      throw new Error(String(detail || `文件生成失败（HTTP ${response.status}）`).slice(0, 300));
    }
    const blob = await response.blob();
    return current() ? blob : null;
  }
  async function executeExportFormat(format, task) {
    const context = task.context;
    if (window.FundsMainAccountsActions?.blocked(context)) throw new Error('主账号标记正在更新，请完成或核对后再导出');
    if (format === 'xlsx') {
      task.setPhase('generating');
      const response = await fetch(`/api/export/tracked.xlsx?case_id=${context.caseId}&canvas_id=${context.canvasId || 0}`);
      const blob = await exportResponse(response, task.isCurrent);
      if (blob && task.isCurrent()) downloadBlob(blob, `资金追踪流水_案件${context.caseId}_画布${context.canvasId || 0}.xlsx`);
      return;
    }
    const graph = state.graph, view = state.graphView, revision = view.revision, layoutRevision = layoutSequence;
    const current = () => task.isCurrent() && graph === state.graph && view === state.graphView
      && revision === view.revision && layoutRevision === layoutSequence;
    const assertGraph = () => { if (task.isCurrent() && !current()) throw new Error('图谱已变化，请重新导出'); return current(); };
    const suffix = view.visibleIds ? `_邻域${graph.getNodes().length}-${state.lastGraph.nodes.length}` : '';
    const filename = `资金图谱_案件${context.caseId}${suffix}`;
    if (format === 'html') {
      task.setPhase('generating');
      const snapshot = await window.__collectSnapshot();
      if (!assertGraph()) return;
      if (!snapshot?.nodes?.length) throw new Error('当前范围暂无图谱');
      const response = await fetch('/api/export/graph.html', { method: 'POST', ...jsonOpts(snapshot) });
      const blob = await exportResponse(response, current);
      if (!assertGraph()) return;
      if (blob) downloadBlob(blob, `${snapshot.case_name || '资金图谱'}_图谱.html`);
      return;
    }
    const url = await exportHiResPng(format === 'pdf' ? 3 : 2, current);
    if (!assertGraph()) return;
    if (!url?.startsWith('data:image/png')) throw new Error('未获得图像数据，请重试');
    task.setPhase('generating');
    if (format === 'pdf') {
      const response = await fetch('/api/export/graph.pdf', { method: 'POST', ...jsonOpts({ image: url }) });
      const blob = await exportResponse(response, current);
      if (!assertGraph()) return;
      if (blob) downloadBlob(blob, `${filename}.pdf`);
    } else {
      const binary = atob(url.slice(url.indexOf(',') + 1));
      const bytes = Uint8Array.from(binary, char => char.charCodeAt(0));
      if (current()) downloadBlob(new Blob([bytes], { type: 'image/png' }), `${filename}.png`);
    }
  }
  exportWorkspace = installExportWorkspace({
    getContext: caseContext, isCurrent: isCurrentCase,
    getLabels: () => ({ caseName: state.cases.find(item => item.id === state.caseId)?.name || String(state.caseId || '未选择'),
      canvasName: state.canvasName || '默认画布' }),
    getScope: () => ({ graphAccounts: state.graph?.getNodes?.().length || 0,
      graphRelations: state.graph?.getEdges?.().length || 0, trackedFlows: state.tracked.size,
      pendingLayout: !!layoutJob,
      graphReady: !!state.graph && state.graphInited && !state.graphLoading
        && !window.FundsCanvasEditor?.history?.status?.().busy }),
    setLegacyFormat: format => { $('#exportFmt').value = format; }, execute: executeExportFormat,
  });
  $('#btnExportPng').onclick = () => exportWorkspace?.submit($('#exportFmt').value);
  $('#btnExportXlsx').onclick = () => exportWorkspace?.submit('xlsx');

  // Both demo entrances share the case-bound confirmation and mutation lock.
  $('#btnSample2').onclick = event => dataToolsWorkspace.open('demo', event.currentTarget);

  // 抽屉
  $('#btnCloseDrawer').onclick = () => {
    closeDrawer(false, { keepSelection: true });
    $('#graphContainer').focus({ preventScroll: true });
  };
  bindEdgeWorkspaceUI();
  bindNodeWorkspaceUI();

  // 上下文弹窗：共享表格、焦点层和工作态。
  bindContextUI();

  // 导入弹窗（数据导入到当前案件）
  const openImport = () => {
    const cur = state.cases.find(c => c.id === state.caseId);
    $('#impCaseTag').textContent = `导入到案件：${cur ? cur.name : state.caseId}`;
    $('#impCaseName').textContent = cur ? `「${cur.name}」` : '当前案件';
    $('#importModal').style.display = 'flex';
    $('#impStep1').style.display = 'block';
    $('#impStep2').style.display = 'none';
    hidePbar();
    $('#impFile').value = '';
  };
  $('#btnImport').onclick = openImport;
  $('#btnImport2').onclick = openImport;
  $('#impClose').onclick = () => { $('#importModal').style.display = 'none'; };
  $('#importModal').addEventListener('click', e => { if (e.target === $('#importModal')) $('#importModal').style.display = 'none'; });
  $('#impFile').onchange = async () => {
    const context = caseContext();
    const f = $('#impFile').files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append('file', f);
    // XHR 上传以获取真实进度；上传完成后转入「解析中」的不确定进度
    const p = await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/import/preview?case_id=' + encodeURIComponent(context.caseId));
      xhr.upload.onprogress = e => {
        if (!isCurrentCase(context, false)) return;
        if (!e.lengthComputable) return;
        if (e.loaded >= e.total) setPbarBusy('上传完成，正在解析文件…');
        else setPbar(e.loaded / e.total, `上传文件 ${Math.round(e.loaded / e.total * 100)}%`);
      };
      xhr.onload = () => {
        let r = null;
        try { r = JSON.parse(xhr.responseText); } catch (err) { /* 非 JSON */ }
        if (xhr.status === 200 && r) resolve(r);
        else reject(new Error((r && r.detail) || `解析失败（HTTP ${xhr.status}）`));
      };
      xhr.onerror = () => reject(new Error('网络错误'));
      xhr.send(fd);
    }).then(
      r => { if (!isCurrentCase(context, false)) return null; hidePbar(); return r; },
      err => { if (!isCurrentCase(context, false)) return null; hidePbar(); toast(String(err.message || err).slice(0, 200), 'err'); return null; }
    );
    if (!p || !isCurrentCase(context, false)) return;
    state.imp = p;
    renderImport(p);
  };
  $('#impRe').onclick = async () => {
    const context = caseContext();
    const { mode, mapping } = collectMapping();
    try {
      const r = await api('/api/import/repreview', { method: 'POST', ...jsonOpts({ import_id: state.imp.import_id, mode, mapping, case_id: state.caseId }) });
      if (!isCurrentCase(context, false)) return;
      state.imp = r;
      renderImport(r);
    } catch (e) { /* 已提示 */ }
  };
  $('#impCommit').onclick = async () => {
    const context = caseContext();
    const { mode, mapping } = collectMapping();
    const selfAccts = $('#impSelfAccts').value.trim();
    const markAccts = $$('#impMainSel .mk-acct').filter(c => c.checked).map(c => c.value).join(',');
    const r0 = await api('/api/import/commit', { method: 'POST', ...jsonOpts({ import_id: state.imp.import_id, mode, mapping, case_id: state.caseId, self_accounts: selfAccts, mark_accounts: markAccts }) });
    if (!isCurrentCase(context, false)) return;
    $('#impCommit').disabled = true;
    setPbar(0.02, r0.started === false ? '导入进行中…' : '解析文件…');
    for (;;) {
      if (!isCurrentCase(context, false)) return;
      let p;
      try { p = await api('/api/import/progress?import_id=' + r0.import_id + '&case_id=' + encodeURIComponent(context.caseId)); }
      catch (e) { $('#impCommit').disabled = false; return; }
      if (!isCurrentCase(context, false)) return;
      if (p.stage === 'parsing') {
        setPbar(0.05, p.message || '解析文件…');
      } else if (p.stage === 'importing') {
        setPbar(p.total ? p.done / p.total : 0.1, `写入数据库 ${fmtCount(p.done)} / ${fmtCount(p.total)} 笔`);
      } else if (p.stage === 'done') {
        const r = p.result;
        setPbar(1, `导入完成：新增 ${fmtCount(r.ok)} 笔`);
        let msg = `导入完成：新增 ${fmtCount(r.ok)} 笔，跳过 ${fmtCount(r.skipped || 0)} 行，重复 ${fmtCount(r.rows_dup || 0)} 笔`;
        if (r.main_detected && r.main_detected.length) {
          const names = r.main_detected.map(d => d.name || d.account.slice(-6)).join('、');
          msg = `已将本方账号标为主账号：${names}（金色★标记）。新增 ${fmtCount(r.ok)} 笔，重复 ${fmtCount(r.rows_dup || 0)} 笔`;
        } else if (r.self_accounts_count > 1) {
          msg = `本方账号列含 ${r.self_accounts_count} 个不同账号（混合文件），未自动标记主账号。` +
                `新增 ${fmtCount(r.ok)} 笔，重复 ${fmtCount(r.rows_dup || 0)} 笔`;
        } else if (r.main_matched && r.main_matched.length) {
          msg += `；含 ${r.main_matched.length} 个已标记主账号（标记已保持）`;
        }
        toast(msg);
        setTimeout(() => { $('#impPbar').style.display = 'none'; $('#importModal').style.display = 'none'; }, 700);
        $('#impCommit').disabled = false;
        await refreshAll();
        return;
      } else if (p.stage === 'error') {
        $('#impPbar').style.display = 'none';
        toast(p.error || '导入失败', 'err');
        $('#impCommit').disabled = false;
        return;
      }
      await new Promise(r2 => setTimeout(r2, 300));
    }
  };
}
