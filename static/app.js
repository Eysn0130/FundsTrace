/* 资金追踪分析系统 — 前端逻辑 */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const preferenceStorage = window.FundsDesktopPreferences || localStorage;
let cancelGraphImageExport = null;

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
  graphDisposers: [],
  graphSelection: null,
  minimap: null,
  graphInited: false,
  graphLoading: false,
  lastGraph: { nodes: [], edges: [], tracked_ids: [] },
  layout: 'force',
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
  cancelGraphLayout();
  state.caseId = Number(caseId);
  state.caseGeneration += 1;
  state.canvasId = 0;
  state.canvasName = '';
  state.canvasGeneration += 1;
  resetGraphView();
  revokePhotoCrop();
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
  $('#batchList').innerHTML = '';
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
  initGraph();
  await loadCases();
  void window.FundsImportWorkflow?.restore();
  await refreshStats();
  await loadGraph();
  await refreshCanvasList(null).catch(() => {});
  doSearch(1);
});

async function refreshAll() {
  await refreshStats();
  await loadGraph();
  await doSearch(state.search.page || 1);
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
async function loadCases() {
  state.cases = await api('/api/cases');
  const sel = $('#caseSelect');
  sel.innerHTML = state.cases.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
  const requested = new URLSearchParams(window.location.search).get('case_id');
  const saved = requested || preferenceStorage.getItem('fund_case');
  selectCase((saved && state.cases.some(c => c.id == saved)) ? Number(saved) : state.cases[0].id);
}

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

function bindFlowTableScroll() {
  unbindFlowTableScroll();
  const viewport = $('#flowTableScroll'), range = $('#flowHorizontalPosition');
  const left = $('#flowScrollLeft'), right = $('#flowScrollRight'), table = $('#flowTable');
  if (!viewport || !range || !left || !right) return;
  let idleTimer = null;
  const limit = () => Math.max(0, (viewport.scrollWidth || 0) - (viewport.clientWidth || 0));
  const sync = () => {
    const max = limit(), value = Math.max(0, Math.min(max, viewport.scrollLeft || 0));
    range.max = String(max); range.value = String(value); range.disabled = max === 0;
    left.disabled = value <= 0; right.disabled = value >= max;
    range.setAttribute('aria-valuetext', max ? `水平位置 ${Math.round(value)} / ${Math.round(max)}` : '全部列已显示');
    range.style.setProperty?.('--flow-scroll-progress', `${max ? value / max * 100 : 0}%`);
  };
  const move = value => { viewport.scrollLeft = Math.max(0, Math.min(limit(), Number(value) || 0)); sync(); };
  const page = () => Math.max(32, Math.round((viewport.clientWidth || 0) * .7));
  const scroll = () => {
    sync(); viewport.classList.add('is-scrolling'); clearTimeout(idleTimer);
    idleTimer = setTimeout(() => viewport.classList.remove('is-scrolling'), 800);
  };
  const key = event => {
    const max = limit(), value = viewport.scrollLeft || 0;
    const next = {ArrowLeft:value-24,ArrowRight:value+24,PageUp:value-page(),PageDown:value+page(),Home:0,End:max}[event.key];
    if (next === undefined) return;
    event.preventDefault(); move(next);
  };
  range.oninput = () => move(range.value);
  range.addEventListener('keydown', key);
  left.onclick = () => move((viewport.scrollLeft || 0) - page());
  right.onclick = () => move((viewport.scrollLeft || 0) + page());
  viewport.addEventListener('scroll', scroll, {passive:true});
  window.addEventListener('resize', sync);
  const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(sync) : null;
  observer?.observe(viewport); if (table) observer?.observe(table);
  syncFlowTableScroll = sync;
  unbindFlowTableScroll = () => {
    clearTimeout(idleTimer); observer?.disconnect();
    viewport.removeEventListener('scroll', scroll); window.removeEventListener('resize', sync);
    range.removeEventListener('keydown', key);
  };
  sync();
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
  await api('/api/track', { method: 'POST', ...jsonOpts({ case_id: state.caseId, flow_ids: ids, canvas_id: state.canvasId || 0 }) });
  state.justAdded = new Set(ids);
  state.forceFit = true;   // 新上图后重新适应画布，保证新增内容可见
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
function newGraphView(labelMode) {
  const requested = labelMode || new URLSearchParams(window.location.search).get('label_mode');
  return { labelMode: ['auto', 'focus', 'all'].includes(requested) ? requested : 'auto',
    fullIndex: null, index: null, visibleIds: null, root: null, direction: 'both', remaining: 0,
    selectedAccount: null, hoveredAccount: null, selectedEdge: null, policy: null,
    revision: 0, labelTimer: null, zoomSettling: false, fitZoom: 1, viewportFrame: null,
    clipIndex: null, clipped: new Set(), nodeItems: new Map(), edgeItems: new Map(),
    labeledNodes: new Set(), labeledEdges: new Set(), mutedEdges: new Set(), edgeDensityMode: false,
    drawnNodes: 0, drawnEdges: 0, exportDepth: 0 };
}
function resetGraphView() {
  cancelGraphImageExport?.();
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
  cancelGraphLayout();
  revokeZoomOptimization();
  // Revoke the prior graph before any request can yield. Its data must never
  // acquire the new canvas ID while the replacement graph is still loading.
  state.graphLoading = true;
  state.lastGraph = { nodes: [], edges: [], tracked_ids: [] };
  state.tracked = new Set(); state.mainAccounts = new Set(); state.focusNodes = new Map();
  state.positions = {}; state.edgeLabelPos = {}; state.edgeBendPos = {}; state.edgeTypes = {}; state.edgeSnapshotLabels = {};
  syncEdgeTypeControl();
  state.justAdded = null; state.forceFit = true;
  if (state.graph) state.graph.clear();
  state.graphInited = false;
  state.canvasId = id; state.canvasGeneration += 1; resetGraphView(); revokePhotoCrop(); closeDrawer();
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
  const count = view.index?.nodeIds.size || 0, edges = view.index?.edgeKeys.size || 0;
  const labels = (view.policy?.nodeIds.size || 0) + (view.policy?.edgeKeys.size || 0);
  const status = $('#graphViewStatus');
  if (status) status.textContent = `${view.visibleIds ? '邻域' : '全图'} ${count}/${full.nodes.length} 个账户 · ${edges}/${full.edges.length} 条关系 · ${labels} 个标签`
    + (state.graphSelection?.ids.size ? ` · 已选 ${state.graphSelection.ids.size} 个账户${state.graphSelection.ids.size > 10 ? '（静态）' : ''}` : '')
    + (view.clipIndex ? ` · 视窗绘制 ${view.drawnNodes} 个账户` : '')
    + (view.edgeDensityMode ? ' · 密集概览，悬停查看方向' : '')
    + (view.visibleIds ? ` · 当前中心尚有 ${view.remaining} 个邻居未展开；图谱导出仅含当前视图` : '');
  if ($('#btnExpandNeighbors')) $('#btnExpandNeighbors').disabled = !view.visibleIds || !view.remaining;
  if ($('#btnNeighborhood')) $('#btnNeighborhood').disabled = !full.nodes.length;
  if ($('#btnGraphOverview')) $('#btnGraphOverview').disabled = !view.visibleIds;
  if ($('#btnCanvasSave')) $('#btnCanvasSave').disabled = state.graphLoading;
  if ($('#btnCanvasClone')) $('#btnCanvasClone').disabled = state.graphLoading;
  if ($('#graphLabelMode')) $('#graphLabelMode').value = view.labelMode;
}
function setLabelShapes(item, visible) {
  const children = item.getContainer?.()?.get?.('children') || [];
  for (const shape of children) {
    const name = shape.get('name'), kind = shape.get('className');
    if (name === 'text-shape' || name === 'text-bg-shape' || kind === 'node-label' || kind === 'edge-label') {
      if (visible) shape.show?.(); else shape.hide?.();
      shape.set?.('ori-visibility', visible);
    }
  }
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
  for(const key of keys) {
    const muted=dense&&!focused.has(key), item=view.edgeItems.get(key);
    if(item && muted!==view.mutedEdges.has(key)) {
      graph.updateItem(item,{style:denseEdgeStyle(muted)});
      if(muted)view.mutedEdges.add(key);else view.mutedEdges.delete(key);
    }
  }
  view.edgeDensityMode=dense;view.focusedEdges=focused;
}
let graphLabelMeasure = null;
function graphLabelBounds(item, kind) {
  const children=item.getContainer?.()?.get?.('children')||[];
  const text=children.find(s=>s.get('name')===(kind==='node'?'text-bg-shape':'text-shape'))
    ||children.find(s=>s.get('name')==='text-shape');
  const bounds=text?.getCanvasBBox?.();
  if(bounds && [bounds.minX,bounds.minY,bounds.maxX,bounds.maxY].every(Number.isFinite)) return bounds;
  const graph=state.graph, m=item.getModel(), zoom=graph.getZoom?.()||1;
  const label=String(m.fundsLabel??m.label??''), fontSize=kind==='node'?11:10;
  const lineHeight=kind==='node'?16:14, lines=label.split('\n');
  if(!graphLabelMeasure) graphLabelMeasure=document.createElement('canvas').getContext?.('2d');
  if(graphLabelMeasure) graphLabelMeasure.font=`${fontSize}px Arial`;
  const width=Math.max(...lines.map(line=>graphLabelMeasure?.measureText?.(line)?.width??line.length*fontSize))*zoom;
  const height=lines.length*lineHeight*zoom;
  let anchor;
  if(kind==='node') {
    const size=Array.isArray(m.size)?Math.max(...m.size):m.size||32;
    anchor={x:m.x,y:m.y+size/2+6+lines.length*lineHeight/2};
  } else {
    const saved=state.edgeSnapshotLabels[edgeKey(m)];
    const ratio=state.edgeLabelPos[edgeKey(m)], path=ratio!=null?edgePts(item):null;
    anchor=path?ptAtRatio(path,ratio):saved?.lx!=null?{x:saved.lx,y:saved.ly}:item.getKeyShape?.()?.getPoint?.(0.5);
    if(!anchor) {
      const a=state.graphView.nodeItems.get(m.source)?.getModel(), b=state.graphView.nodeItems.get(m.target)?.getModel();
      if(a&&b) anchor={x:(a.x+b.x)/2,y:(a.y+b.y)/2};
    }
  }
  if(!anchor || !Number.isFinite(anchor.x)||!Number.isFinite(anchor.y)) return null;
  const p=graph.getCanvasByPoint?.(anchor.x,anchor.y)||anchor;
  return {minX:p.x-width/2,minY:p.y-height/2,maxX:p.x+width/2,maxY:p.y+height/2};
}
function avoidGraphLabelCollisions(policy) {
  const view=state.graphView;
  if(typeof FundsGraphLabels==='undefined') return policy;
  const candidates=[], disabled=view.labelMode==='all'||view.exportDepth>0;
  const endpointBounds=new Map();
  const nodeBounds=id=>{
    if(endpointBounds.has(id))return endpointBounds.get(id);
    const node=view.nodeItems.get(id), shape=node?.getKeyShape?.()?.getCanvasBBox?.();
    let bounds=shape&&[shape.minX,shape.minY,shape.maxX,shape.maxY].every(Number.isFinite)?shape:null;
    if(!bounds&&node) {
      const m=node.getModel(), size=Array.isArray(m.size)?Math.max(...m.size):m.size||32;
      const p=state.graph.getCanvasByPoint?.(m.x,m.y)||{x:m.x,y:m.y};
      const radius=size/2*(state.graph.getZoom?.()||1);
      if([p.x,p.y,radius].every(Number.isFinite))bounds={minX:p.x-radius,minY:p.y-radius,maxX:p.x+radius,maxY:p.y+radius};
    }
    endpointBounds.set(id,bounds);return bounds;
  };
  for(const id of policy.nodeIds) {
    const item=view.nodeItems.get(id);if(!item)continue;
    const required=id===view.selectedAccount||id===view.hoveredAccount;
    candidates.push({key:id,kind:'node',bounds:graphLabelBounds(item,'node'),required,
      priority:required?100:view.index.mainNodeIds.has(id)?70:20});
  }
  for(const key of policy.edgeKeys) {
    const item=view.edgeItems.get(key);if(!item)continue;
    const required=key===view.selectedEdge;
    let bounds=graphLabelBounds(item,'edge');
    // Ordinary amount labels must also fit between their endpoint bodies. This
    // visits only the two endpoints of each budgeted label, not the whole graph.
    if(bounds&&!required&&!disabled) {
      const m=item.getModel();
      if([m.source,m.target].some(id=>{const b=nodeBounds(id);return b&&bounds.minX<=b.maxX+2&&bounds.maxX>=b.minX-2
        &&bounds.minY<=b.maxY+2&&bounds.maxY>=b.minY-2;}))bounds=null;
    }
    candidates.push({key,kind:'edge',bounds,required,
      priority:required?100:state.edgeSnapshotLabels[key]?.lx!=null?60:10});
  }
  const selected=FundsGraphLabels.select(candidates,{disabled});
  return {...policy,nodeIds:selected.nodeIds,edgeKeys:selected.edgeKeys,collisions:selected.rejected};
}
function applyGraphLabels(paint = true) {
  const graph = state.graph, view = state.graphView;
  if (!graph || !view.index) return;
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
  view.policy = FundsGraphView.labels(index, { zoom: (graph.getZoom?.() || 1) / view.fitZoom * 0.5,
    mode: view.labelMode, selectedAccount: view.selectedAccount, hoveredAccount: view.hoveredAccount,
    selectedEdge: view.selectedEdge, eligibleNodeIds: !view.exportDepth ? view.clipResult?.nodeIds : null });
  view.policy=avoidGraphLabelCollisions(view.policy);
  if (paint) graph.setAutoPaint(false);
  const update = (items, previous, desired) => {
    const keys = new Set([...previous, ...desired]);
    for (const key of keys) {
      const item = items.get(key); if (!item) continue;
      const model = item.getModel(), wanted = desired.has(key);
      const canonical = model.fundsLabel ?? model.label ?? '';
      if (model.fundsLabel === undefined) model.fundsLabel = canonical;
      if ((wanted && model.label !== canonical) || (!wanted && model.label !== undefined)) {
        if (!wanted && items === view.edgeItems) cacheEdgeLabel(item);
        // Undefined removes G6's label and background, not merely their paint.
        model.label = wanted ? canonical : undefined;
        graph.updateItem(item, { label: model.label });
        if (wanted && items === view.edgeItems) restoreEdgeLabelPositions([item]);
      }
      if (wanted) setLabelShapes(item, true);
      if (wanted) view.clipIndex?.include(items === view.nodeItems?'node':'edge',key,graphItemBounds(item));
    }
  };
  update(view.nodeItems, view.labeledNodes, view.policy.nodeIds);
  update(view.edgeItems, view.labeledEdges, view.policy.edgeKeys);
  // New labels now have real G6 text/background bounds. Resolve any estimate
  // mismatch before painting; never repeatedly create rejected labels here.
  const settled=avoidGraphLabelCollisions(view.policy);
  update(view.nodeItems, view.policy.nodeIds, settled.nodeIds);
  update(view.edgeItems, view.policy.edgeKeys, settled.edgeKeys);
  view.policy={...settled,collisions:(view.policy.collisions||0)+(settled.collisions||0)};
  view.labeledNodes = new Set(view.policy.nodeIds); view.labeledEdges = new Set(view.policy.edgeKeys);
  applyGraphEdgeDetail();
  if (paint) { graph.setAutoPaint(true); graph.paint(); }
  updateGraphViewStatus();
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
function rebuildGraphViewport() {
  const graph = state.graph, view = state.graphView;
  if (!graph) return;
  view.nodeItems = new Map(graph.getNodes().map(n => [n.getModel().id,n]));
  view.edgeItems = new Map(graph.getEdges().map(e => [edgeKey(e.getModel()),e]));
  const unproject=graphUnprojector();
  const nodes = [...view.nodeItems.values()].map(n => { const m=n.getModel(); return {id:m.id,x:m.x,y:m.y,size:m.size,isMain:m.data?.is_main===1,bounds:graphItemBounds(n,unproject)}; });
  const edges = [...view.edgeItems.values()].map(e => {const m=e.getModel();return {source:m.source,target:m.target,
    controlPoints:m.controlPoints,bounds:graphItemBounds(e,unproject),...state.edgeSnapshotLabels[edgeKey(m)]};});
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
  graph.setAutoPaint(true);
  if (labels) applyGraphLabels(); else if (changed) graph.paint();
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
function exploreNeighborhood(account, reset = false) {
  const view = state.graphView;
  if (!view.fullIndex) view.fullIndex = FundsGraphView.createIndex(state.lastGraph);
  const direction = $('#neighborDirection')?.value || 'both';
  const result = FundsGraphView.neighborhood(view.fullIndex, account, { direction, limit: 50,
    visible: reset ? null : view.visibleIds });
  if (!result.rootFound) return toast('当前追踪图谱中未找到该账号，请输入完整账号', 'err');
  savePositions(); closeDrawer();
  view.visibleIds = result.nodeIds; view.root = account; view.direction = direction; view.remaining = result.remaining;
  view.selectedAccount = account; view.hoveredAccount = null;
  $('#neighborAccount').value = account;
  state.forceFit = true;
  graphMetric('request', { reason: 'neighborhood' });
  renderGraph();
}
function showGraphOverview() {
  savePositions(); closeDrawer();
  const mode = state.graphView.labelMode;
  resetGraphView(); state.graphView.labelMode = mode;
  state.graphView.fullIndex = FundsGraphView.createIndex(state.lastGraph);
  state.forceFit = true;
  graphMetric('request', { reason: 'overview' });
  renderGraph();
}

/* ---------------- 图谱 ---------------- */
function initGraph() {
  cancelGraphLayout();
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
      { type: 'drag-canvas', enableOptimize: true },
      { type: 'zoom-canvas', enableOptimize: true, optimizeZoom: 0.001 },
      ...(typeof FundsGraphSelection === 'undefined' ? ['drag-node'] : []),
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
        style: { fill: '#dfe3ea', fontSize: 11, lineHeight: 16, background: { fill: 'rgba(16,18,22,.8)', padding: [2, 5, 2, 5] } },
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
        style: { fill: '#dfe3ea', fontSize: 10, lineHeight: 14, background: { fill: 'transparent', padding: [2, 5, 2, 5] } },
      },
    },
    nodeStateStyles: { selected: { stroke: '#66d9ef', lineWidth: 3 }, recent: { lineDash: [4, 3] } },
    edgeStateStyles: { selected: { stroke: '#f5a623', lineWidth: 2.6 } },
  });
  state.graph = graph;
  const mini = $('#graphMinimap');
  if (mini?.getContext && typeof FundsGraphMinimap !== 'undefined') {
    state.minimap = FundsGraphMinimap.create({ canvas: mini, onNavigate: point => {
      if (state.graph !== graph || state.graphLoading) return;
      const c=$('#graphContainer'), p=graph.getCanvasByPoint(point.x,point.y);
      graph.translate(c.clientWidth/2-p.x,c.clientHeight/2-p.y);
      scheduleGraphViewport(); scheduleGraphLabels();
    } });
  }
  graph.on('viewportchange', event => {
    if (state.graph !== graph) return;
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
  if (typeof FundsGraphSelection === 'undefined') graph.on('node:click', ev => { void showNodeDrawer(ev.item.getModel().id, ev.item); });
  graph.on('edge:click', ev => {
    c.focus?.({ preventScroll: true });
    void showEdgeDrawer(ev.item.getModel(), ev.item).catch(() => {});
  });
  // 拖拽节点后立即保存位置，后续刷新/上图不会把节点弹回原位
  graph.on('node:dragstart', () => {captureEdgeLabelRatios();cancelGraphLayout();});
  graph.on('node:mousedown', () => cancelGraphLayout());
  graph.on('node:drag', () => graphMetric('node-drag'));
  graph.on('dragnodeend', () => { restoreEdgeLabelPositions();savePositions(); rebuildGraphViewport(); scheduleGraphLabels(); graphMetric('node-drag'); });
  graph.on('canvas:drag', () => graphMetric('canvas-drag'));
  graph.on('canvas:dragend', () => { scheduleGraphLabels(); graphMetric('canvas-drag'); });
  graph.on('node:dragend', () => savePositions());
  if (!state.graphSelection) graph.on('canvas:click', () => { if (_selItem) closeDrawer(); });
  initEdgeLabelDrag(graph);
  initEdgeBendDrag(graph);
  const resize = () => {
    if (state.graph !== graph || !c.clientWidth || !c.clientHeight) return;
    cancelGraphImageExport?.();
    try { graph.changeSize(c.clientWidth, c.clientHeight); scheduleGraphViewport(); state.graphSelection?.refresh(); } catch (_) { /* graph may be disposing */ }
  };
  if (typeof ResizeObserver !== 'undefined') { state.graphResizeObserver = new ResizeObserver(resize); state.graphResizeObserver.observe(c); }
  else { state.graphResizeHandler = resize; window.addEventListener('resize', resize); }
}

function initGraphSelection(graph = state.graph, c = $('#graphContainer')) {
  if (typeof FundsGraphSelection !== 'undefined') {
    const context = caseContext();
    state.graphSelection = FundsGraphSelection.create({ graph, container: c, context,
      isCurrent: binding => graph === state.graph && isCurrentCase(binding) && !state.graphLoading,
      canInteract: () => !window.FundsCanvasEditor?.history.status().busy,
      onPrimary: item => { void showNodeDrawer(item.getModel().id, item).catch(() => {}); },
      onChange: ({ ids }) => {
        closeDrawer(false);
        state.graphView.selectedAccount = ids.size <= 10 ? [...ids].at(-1) || null : null;
        scheduleGraphLabels(); updateGraphViewStatus();
      },
      onMoveStart: () => {
        cancelGraphLayout(); captureEdgeLabelRatios();
        window.dispatchEvent(new CustomEvent('funds:selection-drag-start'));
      },
      onBeforeMove: ({ ids, dx, dy }) => translateSelectedBends(ids, dx, dy),
      onMoveEnd: result => {
        if (result.moved || result.cancelled) {
          restoreEdgeLabelPositions(); savePositions(); rebuildGraphViewport(); scheduleGraphLabels();
          graphMetric('node-drag');
        }
        window.dispatchEvent(new CustomEvent('funds:selection-drag-end', { detail: result }));
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
    window.dispatchEvent(new CustomEvent('funds:edge-drag-start'));
    const binding = { context: caseContext(), view: state.graphView, revision: state.graphView.revision };
    const m = e.item.getModel();
    const key = edgeKey(m);
    const cps = (state.edgeBendPos[key] || []).map(p => ({ x: p.x, y: p.y }));
    const anchor = graph.getPointByClient(e.clientX, e.clientY);
    // 命中已有折点 → 移动模式
    let hit = -1;
    for (let i = 0; i < cps.length; i++) {
      if (((cps[i].x - anchor.x) ** 2 + (cps[i].y - anchor.y) ** 2) * (graph.getZoom?.() || 1) ** 2 <= 10 * 10) { hit = i; break; }
    }
    if (hit >= 0) {
      bend = { ...binding, edge: e.item, key, pts: cps, idx: hit };
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
    bend = { ...binding, edge: e.item, key, pts, idx };
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
      const upd = { controlPoints: state.edgeBendPos[bend.key] };
      upd.type = graphEdgeType(bend.edge.getModel());
      try { graph.updateItem(bend.edge, upd); } catch (err) { /* 忽略 */ }
    } catch (err) { /* 忽略 */ }
  };

  const up = () => { if (bend && isCurrentGraphGesture(bend, graph)) {savePositions();rebuildGraphViewport();} bend = null; };
  window.addEventListener('mousemove', move); window.addEventListener('mouseup', up);
  state.graphDisposers.push(() => { bend = null; window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); });

  graph.on('edge:dblclick', e => {
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
function syncEdgeTypeControl() {
  const control = $('#edgeTypeSel');
  if (control) control.value = Object.keys(state.edgeTypes).length ? 'saved' : state.edgeType;
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

// 把标签（文字+背景）绝对定位到图坐标 pt（text.attr x/y 即图坐标，bg 中心与其对齐）
function placeEdgeLabel(edge, pt) {
  const { text, bg } = findEdgeShapes(edge);
  if (text) {
    text.attr('x', pt.x);
    text.attr('y', pt.y);
  }
  if (bg) {
    const w = bg.attr('width'), h = bg.attr('height');
    if (w != null && h != null) {
      bg.attr('x', pt.x - w / 2);
      bg.attr('y', pt.y - h / 2);
    }
  }
}

// 渲染后恢复用户拖过的标签位置（按弧长比例，任意线型通用）
function restoreEdgeLabelPositions(edges = state.graph.getEdges()) {
  for (const edge of edges) {
    const t = state.edgeLabelPos[edgeKey(edge.getModel())];
    if (t == null) {
      const saved = state.edgeSnapshotLabels[edgeKey(edge.getModel())];
      if (saved?.lx != null && saved?.ly != null) placeEdgeLabel(edge, {x:saved.lx,y:saved.ly});
      continue;
    }
    const P = edgePts(edge);
    if (!P) continue;
    const point=ptAtRatio(P,t),key=edgeKey(edge.getModel());
    placeEdgeLabel(edge,point);
    // A lazy label may have no text shape. Keep its saved coordinates current
    // as well, so a full save never serializes the old layout's absolute point.
    state.edgeSnapshotLabels[key]={...state.edgeSnapshotLabels[key],lx:point.x,ly:point.y};
  }
}
function captureEdgeLabelRatios() {
  const items=state.graphView.edgeItems;
  for(const [key,saved] of Object.entries(state.edgeSnapshotLabels)) {
    if(state.edgeLabelPos[key]!=null || saved.lx==null || saved.ly==null) continue;
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
    window.dispatchEvent(new CustomEvent('funds:edge-drag-start'));
    drag = { edge: e.item, P, context: caseContext(), view: state.graphView, revision: state.graphView.revision };
    e.preventDefault && e.preventDefault();
  });

  // 拖动中用原生事件（鼠标可能离开边线，仍需跟随投影）
  const move = e => {
    if (!drag) return;
    if (!isCurrentGraphGesture(drag, graph)) { drag = null; return; }
    try {
      const p = graph.getPointByClient(e.clientX, e.clientY);
      const r = nearestRatio(drag.P, p.x, p.y);
      placeEdgeLabel(drag.edge, ptAtRatio(drag.P, r));
      state.edgeLabelPos[edgeKey(drag.edge.getModel())] = r;
    } catch (_) { /* binding may have been revoked */ }
  };

  const up = () => { if (drag && isCurrentGraphGesture(drag, graph)) {savePositions();rebuildGraphViewport();} drag = null; };
  window.addEventListener('mousemove', move); window.addEventListener('mouseup', up);
  state.graphDisposers.push(() => { drag = null; window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); });

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
  const lst = await api(`/api/canvases?case_id=${context.caseId}`).catch(() => []);
  if (!isCurrentCase(context)) return;
  const sel = $('#canvasSel');
  const cur = keepId != null ? keepId : (state.canvasId || 0);
  sel.innerHTML = '<option value="0">默认画布</option>' +
    lst.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
  const has = Number(cur) === 0 || lst.some(c => c.id === Number(cur));
  sel.value = has ? String(cur) : '0';
  const resolvedId = Number(sel.value) || 0;
  if (resolvedId !== state.canvasId) activateCanvas(resolvedId);
  state.canvasName = state.canvasId
    ? (lst.find(c => c.id === state.canvasId) || {}).name || '' : '';
}

function applyCanvasSnapshot(snap) {
  resetGraphView();
  const context = caseContext();
  // 快照与导出 HTML 同构：nodes[{id,x,y,label,size,style,img?}] / edges[{source,target,type,controlPoints,label,lx,ly,bw,bh}]
  state.positions = {};
  for (const n of (snap.nodes || [])) state.positions[n.id] = { x: n.x, y: n.y };
  state.edgeLabelPos = {};
  state.edgeBendPos = {}; state.edgeTypes = {};
  state.edgeSnapshotLabels = {};
  for (const e of (snap.edges || [])) {
    const k = e.source + '=>' + e.target;
    if (e.lx != null && e.ly != null) state.edgeSnapshotLabels[k] = { lx:e.lx, ly:e.ly, bw:e.bw, bh:e.bh };
    if (e.controlPoints && e.controlPoints.length) state.edgeBendPos[k] = e.controlPoints.map(p => ({x:p.x,y:p.y}));
    if (['line', 'polyline', 'quadratic', 'loop'].includes(e.type)) state.edgeTypes[k] = e.type;
  }
  state.forceRebuild = true;
  renderGraph();
  const graph = state.graph, view = state.graphView, revision = view.revision;
  // 标注绝对位置恢复（renderGraph 之后放置）
  setTimeout(() => {
    if (!isCurrentCase(context) || graph !== state.graph || view !== state.graphView || revision !== view.revision) return;
    const g = state.graph;
    if (!g) return;
    for (const e of (snap.edges || [])) {
      if (e.lx == null) continue;
      const edge = g.getEdges().find(x => {
        const m = x.getModel();
        return m.source === e.source && m.target === e.target;
      });
      if (!edge) continue;
      const kids = edge.getContainer().get('children') || [];
      for (const k of kids) {
        const nm = k.get('name');
        if (nm === 'text-shape') k.attr({ x: e.lx, y: e.ly });
        else if (nm === 'text-bg-shape' && e.bw) k.attr({ x: e.lx - e.bw / 2, y: e.ly - e.bh / 2 });
      }
    }
    // 照片节点（快照内嵌 dataURL 直接展示，无需再取）
    try { fitGraph(1.1); } catch (err) { /* 忽略 */ }
  }, 250);
}

async function loadCanvas(id) {
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
  // 切换画布 = 切换追踪集合（各画布的图互相独立）+ 恢复该画布保存的形态
  $('#canvasSel').onchange = async e => {
    cancelGraphLayout();
    const v = Number(e.target.value) || 0;
    activateCanvas(v);
    state.canvasName = v ? (e.target.selectedOptions[0] || {}).textContent || '' : '';
    state.positions = {};
    state.edgeLabelPos = {};
    state.edgeBendPos = {}; state.edgeTypes = {};
    state.edgeSnapshotLabels = {};
    state.forceFit = true;
    state.forceRebuild = true;
    const context = caseContext();
    let snapshot = null;
    if (v) {
      // Resolve optional shape before publishing an interactive graph. A late
      // snapshot must not reset a neighborhood the user has already opened.
      try {
        const r = await api(`/api/canvases/${v}?case_id=${context.caseId}`);
        if (!isCurrentCase(context)) return;
        if (r.snapshot && r.snapshot.nodes && r.snapshot.nodes.length) snapshot = r.snapshot;
      } catch (err) { /* 无快照或已删 */ }
    }
    if (!isCurrentCase(context)) return;
    await loadGraph(true, context);
    if (!isCurrentCase(context)) return;
    if (snapshot) applyCanvasSnapshot(snapshot);
  };
  // 默认画布是虚拟画布：改名/删除不适用（置灰）；存形自动转为另存为
  const syncCanvasBtns = () => {
    const named = !!state.canvasId;
    $('#btnCanvasRename').disabled = !named;
    $('#btnCanvasDel').disabled = !named;
  };
  $('#canvasSel').addEventListener('change', () => setTimeout(syncCanvasBtns, 0));

  // 另存为：当前画布（含默认画布）的上图流水 + 形态 → 新画布，源画布不变
  $('#btnCanvasClone').onclick = async () => {
    if (state.graphLoading) return toast('图谱正在加载，请完成后再另存为', 'err');
    const context = caseContext();
    const srcLabel = state.canvasName ? `画布「${state.canvasName}」` : '默认画布';
    const name = await promptText(`把${srcLabel}另存为新画布，请输入名称`, '');
    if (!isCurrentCase(context)) return;
    if (name === null || !name) return;
    try {
      const snap = await window.__collectSnapshot({ scope: 'all' });
      if (!isCurrentCase(context)) return;
      if (!snap) return toast('图谱已变化，请重新另存为', 'err');
      const r = await api('/api/canvases/clone', { method: 'POST',
        ...jsonOpts({ case_id: state.caseId, from_canvas: state.canvasId || 0, name, snapshot: snap }) });
      if (!isCurrentCase(context)) return;
      activateCanvas(r.canvas_id);
      const activated = caseContext();
      state.canvasName = r.name;
      state.forceRebuild = true;
      await refreshCanvasList(r.canvas_id);
      if (!isCurrentCase(activated)) return;
      await loadGraph();
      if (!isCurrentCase(activated)) return;
      if (snap.nodes.length) applyCanvasSnapshot(snap);
      syncCanvasBtns();
      toast(`已另存为新画布「${r.name}」（复制 ${r.copied} 笔上图流水），源${srcLabel}保持不变`);
    } catch (e2) { toast('另存失败：' + e2.message, 'err'); }
  };
  $('#btnCanvasSave').onclick = async () => {
    if (state.graphLoading) return toast('图谱正在加载，请完成后再保存', 'err');
    const context = caseContext();
    if (!state.canvasId) return $('#btnCanvasClone').click();   // 默认画布无形态存储 → 转另存为
    const name = await promptText('保存画布形态（节点位置/线型/折点/标注/重点展示）', state.canvasName);
    if (!isCurrentCase(context)) return;
    if (name === null) return;
    try {
      const snap = await window.__collectSnapshot({ scope: 'all' });
      if (!isCurrentCase(context)) return;
      if (!snap) return toast('图谱已变化，请重新保存', 'err');
      const r = await api('/api/canvases/save', { method: 'POST',
        ...jsonOpts({ case_id: state.caseId, canvas_id: state.canvasId, name: name || state.canvasName, snapshot: snap }) });
      if (!isCurrentCase(context)) return;
      state.canvasName = r.name;
      await refreshCanvasList(r.canvas_id);
      toast(`画布「${r.name}」形态已保存`);
    } catch (e2) { toast('保存失败：' + e2.message, 'err'); }
  };
  $('#btnCanvasNew').onclick = async () => {
    const context = caseContext();
    // 新建空白画布：独立追踪集合，图从空白开始（当前画布的追踪不受影响）
    const name = await promptText('新建空白画布，请输入名称', '');
    if (!isCurrentCase(context)) return;
    if (name === null || !name) return;
    try {
      const r = await api('/api/canvases/save', { method: 'POST',
        ...jsonOpts({ case_id: state.caseId, canvas_id: null, name, snapshot: { nodes: [], edges: [] } }) });
      if (!isCurrentCase(context)) return;
      activateCanvas(r.canvas_id);
      const activated = caseContext();
      state.canvasName = r.name;
      state.positions = {};
      state.edgeLabelPos = {};
      state.edgeBendPos = {}; state.edgeTypes = {};
      state.edgeSnapshotLabels = {};
      state.forceFit = true;
      state.forceRebuild = true;
      await refreshCanvasList(r.canvas_id);
      if (!isCurrentCase(activated)) return;
      await loadGraph();
      if (!isCurrentCase(activated)) return;
      toast(`空白画布「${r.name}」已创建，现在上图只属于这张画布`);
    } catch (e2) { toast('新建失败：' + e2.message, 'err'); }
  };
  $('#btnCanvasRename').onclick = async () => {
    const context = caseContext();
    if (!state.canvasId) return toast('默认画布不可改名（它是不占名字的草稿区）；如需命名请点「另存为」', 'err');
    const name = await promptText('重命名画布', state.canvasName);
    if (!isCurrentCase(context)) return;
    if (name === null || !name) return;
    try {
      await api('/api/canvases/rename', { method: 'POST',
        ...jsonOpts({ case_id: state.caseId, canvas_id: state.canvasId, name }) });
      if (!isCurrentCase(context)) return;
      await refreshCanvasList(state.canvasId);
      toast('画布已重命名');
    } catch (e2) { toast('重命名失败：' + e2.message, 'err'); }
  };
  $('#btnCanvasDel').onclick = async () => {
    const context = caseContext();
    if (!state.canvasId) return toast('默认画布不可删除；清空它的上图请用「清空图谱」按钮', 'err');
    if (!confirm(`确定删除画布「${state.canvasName}」及其全部上图流水？删除后不可恢复。`)) return;
    try {
      await api(`/api/canvases/${state.canvasId}?case_id=${state.caseId}`, { method: 'DELETE' });
      if (!isCurrentCase(context)) return;
      activateCanvas(0);
      const activated = caseContext();
      state.canvasName = '';
      state.forceFit = true;
      state.forceRebuild = true;
      await refreshCanvasList(0);
      if (!isCurrentCase(activated)) return;
      await loadGraph();
      if (!isCurrentCase(activated)) return;
      syncCanvasBtns();
      toast('画布已删除，已切回默认画布');
    } catch (e2) { toast('删除失败：' + e2.message, 'err'); }
  };
  setTimeout(syncCanvasBtns, 0);   // 初始按当前画布刷新按钮可用性
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
  const role = (n.out_cnt > 0 && n.in_cnt > 0) ? 'mid' : (n.out_cnt > 0 ? 'src' : 'sink');
  const color = { src: '#e25656', mid: '#4caf7d', sink: '#4a86e8' }[role];
  // is_main = 导入了其本人流水的账号（金色）；is_same_name = 同户名的关联账号（蓝色）
  if (n.is_main === 1) {
    return { fill: color, stroke: '#ffd700', lineWidth: 3.5,
             shadowColor: 'rgba(255, 215, 0, .55)', shadowBlur: 16 };
  }
  if (n.is_same_name === 1) {
    return { fill: color, stroke: '#4a9fe8', lineWidth: 3,
             shadowColor: 'rgba(74, 159, 232, .5)', shadowBlur: 12 };
  }
  return { fill: color, stroke: '#0e1013', lineWidth: 2 };
}

// 节点描边/头像描边色：主账号金 > 同户名蓝 > 角色色
function nodeRing(n) {
  if (n.is_main === 1) return '#ffd700';
  if (n.is_same_name === 1) return '#4a9fe8';
  return (n.out_cnt > 0 && n.in_cnt > 0) ? '#4caf7d' : (n.out_cnt > 0 ? '#e25656' : '#4a86e8');
}

// 有照片且开关打开时，节点用圆形头像替代圆点
function nodePhotoUrl(n) {
  if (!state.showPhotos || !n.photo_at) return null;
  return `/api/account-photos/img?case_id=${state.caseId}&account=${encodeURIComponent(n.account)}`
       + `&ring=${encodeURIComponent(nodeRing(n))}&v=${encodeURIComponent(n.photo_at)}`;
}

function nodeLabel(n) {
  const star = n.is_main === 1 ? '★ ' : (n.is_same_name === 1 ? '◆ ' : '');
  const name = (n.name || '(未知)').slice(0, 12);
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
    const x = (e.summaries || []).slice(0, 2).join('/').slice(0, 24);
    if (x) lines.push(x);
  }
  if (s.has('time')) {
    const t = fmtEdgeSpan(e.tmin, e.tmax);
    if (t) lines.push(t);
  }
  return lines.join('\n') || `${e.cnt}笔`;
}

function renderGraph() {
  syncEdgeTypeControl();
  if (!state.graph) return;
  cancelGraphLayout();
  const selectedIds = new Set(state.graphSelection?.ids || []);
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
  view.revision += 1;
  const full = state.lastGraph;
  const g = view.visibleIds ? {
    nodes: full.nodes.filter(node => view.visibleIds.has(node.account)),
    edges: full.edges.filter(edge => view.visibleIds.has(edge.source) && view.visibleIds.has(edge.target)),
  } : full;
  view.index = view.visibleIds ? FundsGraphView.createIndex(g) : (view.fullIndex || FundsGraphView.createIndex(g));
  const pos = state.positions;
  const W = $('#graphContainer').clientWidth || 900, H = $('#graphContainer').clientHeight || 600;
  const nodes = g.nodes.map(n => {
    const p = pos[n.account];
    const focusScale = state.focusNodes.get(n.account) || 1;
    const isFocus = focusScale > 1;
    const nd = {
      id: n.account, fundsLabel: nodeLabel(n), data: n,
      x: p ? p.x : null, y: p ? p.y : null,
      size: (28 + Math.min(24, (n.in_cnt + n.out_cnt) * 2)) + (n.is_main === 1 ? 6 : (n.is_same_name === 1 ? 3 : 0)),
      style: nodeStyle(n),
    };
    if (isFocus) nd.size = Math.round(nd.size * focusScale);   // 重点展示：按倍数放大（1~4）
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
  const neighbor = new Map();
  for (const e of g.edges) { if (!neighbor.has(e.source)) neighbor.set(e.source, e.target); if (!neighbor.has(e.target)) neighbor.set(e.target, e.source); }
  for (const nd of nodes) {
    if (nd.x != null) continue;
    const nb = byId.get(neighbor.get(nd.id)), seeded = FundsGraphLayout.seed(nd.id, W, H);
    nd.x = nb?.x != null ? nb.x + (seeded.x - W / 2) * 0.4 : seeded.x;
    nd.y = nb?.y != null ? nb.y + (seeded.y - H / 2) * 0.4 : seeded.y;
  }
  const dense=g.edges.length>1000 && view.labelMode!=='all';
  const edges = g.edges.map(e => {
    const bk = edgeKey({ source: e.source, target: e.target });
    const bent = !!state.edgeBendPos[bk];
    return {
      source: e.source, target: e.target, data: e,
      // 直线模式下被拖出过折点的边按折线渲染；其余随全局线型
      type: graphEdgeType(e),
      fundsLabel: edgeLabel(e),
      style: denseEdgeStyle(dense),
      controlPoints: bent ? state.edgeBendPos[bk] : undefined,
    };
  });
  const initialLabels = FundsGraphView.labels(view.index, {zoom:0.5,mode:view.labelMode,
    selectedAccount:view.selectedAccount,hoveredAccount:view.hoveredAccount,selectedEdge:view.selectedEdge});
  for (const n of nodes) n.label=initialLabels.nodeIds.has(n.id)?n.fundsLabel:undefined;
  for (const e of edges) e.label=initialLabels.edgeKeys.has(edgeKey(e))?e.fundsLabel:undefined;
  view.labeledNodes=new Set(initialLabels.nodeIds);view.labeledEdges=new Set(initialLabels.edgeKeys);
  view.mutedEdges=dense?new Set(edges.map(edgeKey)):new Set();view.edgeDensityMode=dense;view.focusedEdges=new Set();
  let graph = state.graph;
  const needFit = state.forceFit || !!state.justAdded;
  const needsFirstLayout = nodes.length > 1 && !state.forceRebuild
    && graph.getNodes().length === 0 && !nodes.some(node => pos[node.id]);
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
    if (needFit) fitGraph(1.1);
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
        if (Math.abs(m.x - p.x) > 0.5 || Math.abs(m.y - p.y) > 0.5) upd.x = p.x, upd.y = p.y;
      }
      // 2) 强制同步主账号的标签与样式——G6 的 changeData 不会更新已有节点的这两项
      const nm = byId.get(id)?.data;
      if (nm) {
        const wantImg = nodePhotoUrl(nm);
        const wantLabel = nodeLabel(nm);
        const wantStyle = wantImg ? {} : nodeStyle(nm);   // 照片节点不能带圆点样式（防色块）
        const wantSize = (28 + Math.min(24, (nm.in_cnt + nm.out_cnt) * 2))
          + (nm.is_main === 1 ? 6 : (nm.is_same_name === 1 ? 3 : 0));
        const fscale = state.focusNodes.get(id) || 1;
        const fs = wantSize * (fscale > 1 ? fscale : 1);
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
  if (!state.graphSelection) initGraphSelection(graph);
  state.graphSelection?.rebind(selectedIds);
  rebuildGraphViewport();
  applyGraphLabels(false);
  graph.setAutoPaint(true);
  graph.paint();
  graphMetric('paint');
  if (needsFirstLayout) void relayout(needFit);
  // Recent additions are feedback, never user selection or animation authority.
  if (state.justAdded && state.justAdded.size) {
    const added = state.justAdded;
    state.justAdded = null;
    const involved = new Set();
    for (const e of g.edges) if ((e.ids || []).some(x => added.has(x))) { involved.add(e.source); involved.add(e.target); }
    setTimeout(() => {
      if (!isCurrentCase(renderContext) || graph !== state.graph) return;
      graph.setAutoPaint(false);
      for (const node of graph.getNodes()) {
        graph.setItemState(node, 'recent', involved.has(node.getModel().id));
      }
      graph.setAutoPaint(true); graph.paint();
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
  setTimeout(() => {
    if (!isCurrentCase(renderContext) || graph !== state.graph || layoutJob) return;
    savePositions(); if (needFit) fitGraph();
  }, 0);
}

function fitGraph(cap = 1.1) {
  try {
    restoreGraphClipping();
    const graph=state.graph, nodes=graph.getNodes(), container=$('#graphContainer');
    let fitted=false;
    if(nodes.length && nodes.length<=20 && graph.getCanvasByPoint && graph.translate && graph.zoomTo) {
      const inverse=graphUnprojector();
      const bounds=[...nodes,...graph.getEdges()].map(item=>graphItemBounds(item,inverse)).filter(Boolean);
      if(bounds.length) {
        const minX=Math.min(...bounds.map(b=>b.minX)),minY=Math.min(...bounds.map(b=>b.minY));
        const maxX=Math.max(...bounds.map(b=>b.maxX)),maxY=Math.max(...bounds.map(b=>b.maxY));
        const width=container.clientWidth||900,height=container.clientHeight||600;
        const zoom=Math.max(0.02,Math.min(4,cap,Math.max(1,width-80)/Math.max(1,maxX-minX),Math.max(1,height-80)/Math.max(1,maxY-minY)));
        graph.zoomTo(zoom);
        const p=graph.getCanvasByPoint((minX+maxX)/2,(minY+maxY)/2);
        graph.translate(width/2-p.x,height/2-p.y);fitted=true;
      }
    }
    if(!fitted) {
      graph.fitView(40);
      const z = graph.getZoom();
      if (z > cap) graph.zoomTo(cap,{x:(container.clientWidth||900)/2,y:(container.clientHeight||600)/2});
    }
    state.graphView.fitZoom = state.graph.getZoom();
    applyGraphViewport();
    applyGraphLabels();
  } catch (err) { /* 忽略 */ }
}

/* Layout runs outside rendering; case/canvas changes and dragging revoke its result. */
let layoutSequence = 0, layoutJob = null;
function layoutStatus(message = '') {
  const node = $('#graphLayoutStatus');
  if (node) { node.textContent = message; node.hidden = !message; }
}
function cancelGraphLayout() {
  cancelGraphImageExport?.();
  layoutSequence += 1;
  if (layoutJob) { layoutJob.cancelled = true; layoutJob.worker?.terminate(); layoutJob = null; }
  layoutStatus();
}
async function relayout(fit = true) {
  if (!state.graph) return;
  cancelGraphLayout();
  const graph = state.graph, context = caseContext(), requestId = layoutSequence;
  const job = { requestId, cancelled: false, worker: null }; layoutJob = job;
  const current = () => !job.cancelled && layoutJob === job && isCurrentCase(context) && state.graph === graph;
  const container = $('#graphContainer');
  const input = { width: container.clientWidth || 900, height: container.clientHeight || 600, kind: state.layout,
    nodes: graph.getNodes().map(node => { const m = node.getModel(); return { id: m.id, x: m.x, y: m.y,
      isMain:m.data?.is_main===1,size:m.size }; }),
    edges: graph.getEdges().map(edge => { const m = edge.getModel(); return { source: m.source, target: m.target }; }) };
  if (input.nodes.length < 2) { layoutJob = null; if (fit) fitGraph(); return; }
  layoutStatus(`正在后台布局 ${fmtCount(input.nodes.length)} 个账户，可继续操作…`);
  function apply(result) {
    if (!current() || !result) return;
    graph.setAutoPaint(false);
    const positions = new Map(result.nodes.map(node => [node.id, node]));
    captureEdgeLabelRatios();
    try {
      if (typeof graph.refreshPositions === 'function') {
        // G6's batch path updates every node, then each affected edge once.
        // Per-node updateItem otherwise repeatedly refreshes a hub's edges.
        for (const node of graph.getNodes()) { const m=node.getModel(),p=positions.get(m.id); if(p){m.x=p.x;m.y=p.y;} }
        graph.refreshPositions();
      } else {
        for (const node of graph.getNodes()) { const p = positions.get(node.getModel().id); if (p) graph.updateItem(node, { x: p.x, y: p.y }); }
      }
    } finally { graph.setAutoPaint(true); graph.paint(); }
    restoreEdgeLabelPositions();savePositions();
    rebuildGraphViewport();
    if (fit) fitGraph(); else applyGraphLabels();
    job.worker?.terminate(); layoutJob = null; layoutStatus();
    graphMetric('layout');
  }
  async function fallback() {
    if (!current()) return;
    // No Worker support: large graphs use linear layered placement; smaller
    // force simulations yield after every iteration to keep input responsive.
    if (input.nodes.length > 400 && input.kind === 'force') input.kind = 'dagre';
    const result = await FundsGraphLayout.run(input, { cancelled: () => !current() });
    apply(result);
  }
  if (typeof Worker !== 'undefined') {
    try {
      const worker = new Worker('/static/graph-layout.js?v=1'); job.worker = worker;
      worker.onmessage = event => { if (!current() || event.data.requestId !== requestId) return; if (event.data.error) { worker.terminate(); job.worker = null; void fallback(); } else apply(event.data.result); };
      worker.onerror = () => { if (!current()) return; worker.terminate(); job.worker = null; void fallback(); };
      worker.postMessage({ requestId, input });
      return;
    } catch (_) { /* fallback remains cancellable */ }
  }
  await fallback();
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
    if (text) state.edgeSnapshotLabels[edgeKey(item.getModel())] = {
      lx:text.attr('x'), ly:text.attr('y'), bw:background?.attr('width'), bh:background?.attr('height'),
    };
}

/* ---------------- 右侧抽屉 ---------------- */
let _selItem = null;
let drawerSequence = 0;
function selectItem(item) {
  if (_selItem?.getModel?.().source || !state.graphSelection) {
    if (_selItem) { try { state.graph.setItemState(_selItem, 'selected', false); } catch (_) { /* disposed */ } }
  }
  if (state.graphSelection && item) {
    const model = item.getModel();
    if (model.source) state.graphSelection.clear();
    else if (state.graphSelection.ids.size !== 1 || !state.graphSelection.ids.has(model.id)) state.graphSelection.select(model.id);
  }
  _selItem = item || null;
  state.graphView.selectedAccount = !_selItem?.getModel?.().source ? _selItem?.getModel?.().id || null : null;
  state.graphView.selectedEdge = _selItem?.getModel?.().source ? edgeKey(_selItem.getModel()) : null;
  if (_selItem && (!state.graphSelection || _selItem.getModel().source)) {
    try { state.graph.setItemState(_selItem, 'selected', true); } catch (_) { /* disposed */ }
  }
  scheduleGraphLabels();
}

function closeDrawer(clearSelection = true) {
  drawerSequence += 1;
  $('#drawer').classList.remove('open');
  $('#drawerBody').innerHTML = '';
  if (clearSelection) selectItem(null);
  else {
    if (_selItem?.getModel?.().source) {
      try { state.graph?.setItemState(_selItem, 'selected', false); } catch (_) { /* disposed */ }
    }
    _selItem = null; state.graphView.selectedEdge = null;
  }
  if (clearSelection) state.graphSelection?.clear();
}

/* ---------------- 照片预览裁剪弹窗 ---------------- */
const pcState = { file: null, account: null, item: null, cx: 0.5, cy: 0.5, r: 0.4 };

function pcLayout() {
  const img = $('#pcImg');
  const W = img.clientWidth, H = img.clientHeight;
  if (!W || !H) return;
  const d = 2 * pcState.r * Math.min(W, H);
  const c = $('#pcCircle');
  c.style.width = d + 'px';
  c.style.height = d + 'px';
  c.style.left = (pcState.cx * W - d / 2) + 'px';
  c.style.top = (pcState.cy * H - d / 2) + 'px';
}

function revokePhotoCrop() {
  pcState.binding = null; pcState.file = null; pcState.account = null;
  $('#photoCropModal').style.display = 'none';
}
function openPhotoCrop(file, account, item) {
  pcState.binding = {context: caseContext(), view: state.graphView, revision: state.graphView.revision};
  pcState.file = file || null;
  pcState.account = account;
  pcState.item = item || null;
  pcState.cx = 0.5; pcState.cy = 0.5; pcState.r = 0.4;
  $('#pcImg').src = file
    ? URL.createObjectURL(file)
    : `/api/account-photos/orig?case_id=${state.caseId}&account=${encodeURIComponent(account)}`;
  $('#pcZoom').value = 40;
  $('#photoCropModal').style.display = 'flex';
}

function bindPhotoCrop() {
  const img = $('#pcImg');
  const circle = $('#pcCircle');
  let dragging = false;
  img.onload = pcLayout;
  circle.addEventListener('pointerdown', e => {
    dragging = true;
    try { circle.setPointerCapture(e.pointerId); } catch (err) { /* 忽略 */ }
    e.preventDefault();
  });
  circle.addEventListener('pointermove', e => {
    if (!dragging) return;
    const rect = img.getBoundingClientRect();
    pcState.cx = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    pcState.cy = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));
    pcLayout();
  });
  circle.addEventListener('pointerup', () => { dragging = false; });
  $('#pcZoom').oninput = e => { pcState.r = Number(e.target.value) / 100; pcLayout(); };
  $('#pcClose').onclick = revokePhotoCrop;
  $('#pcConfirm').onclick = async () => {
    const binding = pcState.binding, account = pcState.account, crop = {cx:pcState.cx,cy:pcState.cy,r:pcState.r};
    const current = () => binding && pcState.binding === binding && isCurrentCase(binding.context)
      && binding.view === state.graphView && binding.revision === state.graphView.revision;
    if (!current()) return;
    try {
      let file = pcState.file;
      if (!file) {   // 重新裁剪现有照片：取已存原图
        const res0 = await fetch(`/api/account-photos/orig?case_id=${binding.context.caseId}&account=${encodeURIComponent(account)}`);
        if (!current()) return;
        if (!res0.ok) throw new Error('无现有照片原图');
        file = await res0.blob();
        if (!current()) return;
      }
      const fd = new FormData();
      fd.append('file', file, file.name || 'photo.png');
      fd.append('case_id', binding.context.caseId);
      fd.append('account', account);
      fd.append('crop_cx', crop.cx.toFixed(4));
      fd.append('crop_cy', crop.cy.toFixed(4));
      fd.append('crop_r', crop.r.toFixed(4));
      const res = await fetch('/api/account-photos/set', { method: 'POST', body: fd });
      if (!current()) return;
      const r = await res.json();
      if (!current()) return;
      if (!res.ok) throw new Error(r.detail || '上传失败');
      $('#photoCropModal').style.display = 'none';
      toast('照片已按所选范围更新，画布节点已同步');
      state.forceRebuild = true;   // 照片节点与圆点互切需整图重建
      await loadGraph();
      if (!isCurrentCase(binding.context)) return;
      // renderGraph rebinds the selected account and refreshes its drawer.
    } catch (e) { if (!current()) return; toast('上传失败：' + e.message, 'err'); }
  };
}

async function showNodeDrawer(account, item) {
  const context = caseContext();
  const view = state.graphView, revision = view.revision;
  item = state.graph?.findById?.(account) || item;
  selectItem(item);
  const request = ++drawerSequence;
  const current = () => request === drawerSequence && isCurrentCase(context) && view === state.graphView && revision === view.revision;
  $('#drawerTitle').textContent = '账户详情';
  $('#drawer').classList.add('open');
  $('#drawerBody').innerHTML = '<div class="muted">加载中…</div>';
  const prof = await api(`/api/accounts/${encodeURIComponent(account)}/profile?case_id=${context.caseId}`);
  if (!current()) return;
  const gn = state.lastGraph.nodes.find(n => n.account === account) || {};
  const mainInfo = (await api(`/api/main-accounts?case_id=${context.caseId}`)).find(m => m.account === account);
  if (!current()) return;
  const isMain = gn.is_main === 1;        // 导入了其本人流水的账号
  const isSameName = gn.is_same_name === 1;   // 同户名的关联账号
  const focusScale = state.focusNodes.get(account) || 1;   // 重点展示倍数（>1 即开启）
  const isFocus = state.focusNodes.has(account);   // 重点展示
  let srcLabel = '';
  if (isMain) {
    srcLabel = mainInfo ? (mainInfo.source === 'auto' ? '★ 主账号（自动识别）' : '★ 主账号（手动标记）') : '★ 主账号';
  } else if (isSameName) {
    srcLabel = '◆ 同户名账号（其流水尚未导入）';
  }
  $('#drawerBody').innerHTML = `
    <div class="kv"><label>户名</label><div>${esc(prof.name || gn.name || '(未知)')}${srcLabel ? ` <span class="tag ${isMain ? 'main-tag' : 'same-tag'}">${srcLabel}</span>` : ''}</div></div>
    <div class="kv"><label>账号</label><div class="mono wrap">${esc(account)}</div></div>
    <div class="btn-row"><button class="btn primary" id="ndNeighborhood">从此账户展开邻域</button></div>
    <div class="kv"><label>身份证号</label><div class="btn-row" style="margin:0;width:100%">
      <input id="ndIdInput" class="ipt" style="flex:1;min-width:0" placeholder="输入或粘贴身份证号" value="${esc(gn.id_no || '')}">
      <button class="btn small primary" id="ndIdSave">保存</button>
    </div></div>
    <div class="kv"><label>节点照片</label><div class="btn-row" style="margin:0;width:100%;align-items:center">
      <img id="ndPhotoImg" alt="" src="${nodePhotoUrl(gn) || ''}" style="width:44px;height:44px;border-radius:50%;object-fit:cover;background:#23272f;${nodePhotoUrl(gn) ? '' : 'display:none'}">
      <input type="file" id="ndPhotoFile" accept=".jpg,.jpeg,.png,.webp,.bmp" style="flex:1;min-width:0;font-size:11px" title="选择后自动弹出裁剪预览框">
    </div></div>
    <div class="btn-row" style="margin-top:0">
      <button class="btn small primary" id="ndPhotoUp" title="选择文件后弹出预览框，拖动圆圈选择头像范围">裁剪并上传所选照片</button>
      <button class="btn small" id="ndPhotoRe" ${gn.photo_at ? '' : 'disabled'} title="不换照片，只调整现有照片的显示范围">重新裁剪现有照片</button>
      <button class="btn small danger" id="ndPhotoClr">清除照片</button>
    </div>
    <div class="btn-row" style="margin-top:0">
      <button class="btn ${isMain ? '' : 'primary'}" id="ndToggleMain">${isMain ? '取消主账号标记' : '★ 标记为主账号'}</button>
    </div>
    <div class="kv"><label>重点展示</label><div class="btn-row" style="margin:0;width:100%">
      <select id="ndFocusScale" style="flex:1;min-width:0;background:#23272f;color:#dfe3ea;border:1px solid #333945;border-radius:6px;padding:5px 6px;font-size:12px">
        <option value="1" ${focusScale <= 1 ? 'selected' : ''}>不重点展示（正常大小）</option>
        ${[1.5, 2, 2.5, 3, 3.5, 4].map(s => `<option value="${s}" ${Math.abs(focusScale - s) < 0.01 ? 'selected' : ''}>放大 ${s} 倍${s >= 3 ? '（醒目）' : ''}</option>`).join('')}
      </select>
      <button class="btn small primary" id="ndFocusApply">应用</button>
    </div></div>
    <div class="sec">图谱内（已追踪 · 同一笔双方流水已合并统计）</div>
    <table class="tbl mini">
      <tr><td>转出 ${gn.out_cnt || 0} 笔</td><td class="amt">${totalsHtml(gn.out_totals, gn.out_amt)}</td></tr>
      <tr><td>转入 ${gn.in_cnt || 0} 笔</td><td class="amt">${totalsHtml(gn.in_totals, gn.in_amt)}</td></tr>
    </table>
    <div class="sec">本案件统计</div>
    <table class="tbl mini">
      <tr><td>转出 ${prof.out_cnt} 笔</td><td class="amt">${totalsHtml(prof.out_totals, prof.out_amt)}</td></tr>
      <tr><td>转入 ${prof.in_cnt} 笔</td><td class="amt">${totalsHtml(prof.in_totals, prof.in_amt)}</td></tr>
      <tr><td>首笔</td><td>${fmtTime(prof.tmin)}</td></tr>
      <tr><td>末笔</td><td>${fmtTime(prof.tmax)}</td></tr>
    </table>
    <div class="btn-row">
      <button class="btn" id="ndBrowse">浏览该账户全部流水</button>
      <button class="btn danger" id="ndRemove">从图谱移除</button>
    </div>
    <div class="sec">交易对手 TOP20（本案件）</div>
    <div class="tbl-wrap"><table class="tbl mini">
      <thead><tr><th>对手</th><th>转出</th><th>转入</th></tr></thead>
      <tbody>${(prof.top_counterparties || []).map(c => `
        <tr class="clickable" data-acct="${esc(c.acct)}" title="点击查询与其交易流水">
          <td>${esc(c.name || '')}<br><span class="mono">${esc(c.acct)}</span></td>
          <td>${c.out_cnt || 0}笔<br>${totalsHtml(c.out_totals, c.out_amt)}</td>
          <td>${c.in_cnt || 0}笔<br>${totalsHtml(c.in_totals, c.in_amt)}</td>
        </tr>`).join('')}</tbody>
    </table></div>`;
  $('#ndFocusApply').onclick = async () => {
    const scale = Number($('#ndFocusScale').value) || 1;
    try {
      const r = await api('/api/focus-nodes/set', { method: 'POST', ...jsonOpts({ case_id: context.caseId, account, on: scale > 1, scale }) });
      if (!current()) return;
      toast(r.on ? `该节点已重点展示（放大 ${r.scale} 倍）` : '已取消重点展示，节点恢复正常大小');
      state.forceRebuild = true;   // 尺寸变化走整图重建，避免渲染残留
      await loadGraph(true, context);
    } catch (e) { toast('设置失败：' + e.message, 'err'); }
  };
  $('#ndIdSave').onclick = async () => {
    const v = $('#ndIdInput').value.trim();
    try {
      await api('/api/account-ids/set', { method: 'POST', ...jsonOpts({ case_id: context.caseId, account, id_no: v }) });
      if (!current()) return;
      const gn2 = state.lastGraph.nodes.find(n => n.account === account);
      if (gn2) gn2.id_no = v || null;
      renderGraph();
      toast(v ? '身份证号已保存并更新到图谱' : '已清除该账号身份证号');
    } catch (e) { toast('保存失败：' + e.message, 'err'); }
  };
  $('#ndPhotoUp').onclick = async () => {
    const f = $('#ndPhotoFile').files[0];
    if (!f) return toast('请先选择图片文件，将弹出预览框供您选择头像范围', 'err');
    openPhotoCrop(f, account, item);
  };
  $('#ndPhotoFile').onchange = () => {
    const f = $('#ndPhotoFile').files[0];
    if (f) openPhotoCrop(f, account, item);   // 选完文件自动弹出裁剪预览
  };
  $('#ndPhotoRe').onclick = () => {
    if (!gn.photo_at) return toast('该节点还没有照片', 'err');
    openPhotoCrop(null, account, item);   // 无新文件时用已存原图重新裁剪
  };
  $('#ndPhotoClr').onclick = async () => {
    try {
      await api('/api/account-photos/clear', { method: 'POST', ...jsonOpts({ case_id: context.caseId, account }) });
      if (!current()) return;
      toast('已清除照片，节点恢复圆点');
      state.forceRebuild = true;   // 照片节点与圆点互切需整图重建
      await loadGraph(true, context);
    } catch (e) { toast('清除失败：' + e.message, 'err'); }
  };
  $('#ndToggleMain').onclick = async () => {
    const name = prof.name || gn.name || '';
    if (isMain) {
      await api('/api/main-accounts', { method: 'DELETE', ...jsonOpts({ case_id: context.caseId, account, name }) });
      if (!current()) return;
      toast('已取消主账号标记');
    } else {
      await api('/api/main-accounts', { method: 'POST', ...jsonOpts({ case_id: context.caseId, account, name }) });
      if (!current()) return;
      toast('已标记为主账号，同户名的其他账号也会自动带标');
    }
    await loadGraph(true, context);
  };
  $('#ndBrowse').onclick = () => {
    closeDrawer(); switchTab('search');
    // 清空全部筛选条件，只保留目标账号——否则残留的金额/时间/关键词会过滤掉大部分流水
    ['#fAccount', '#fName', '#fCounter', '#fKw', '#fMin', '#fMax', '#fFrom', '#fTo']
      .forEach(id => $(id).value = '');
    $('#fTracked').checked = false;
    $('#fAccount').value = account;
    doSearch(1);
  };
  $('#ndNeighborhood').onclick = () => { if (current()) exploreNeighborhood(account); };
  $('#ndRemove').onclick = () => {
    const ids = [];
    for (const e of state.lastGraph.edges) {
      if (e.source === account || e.target === account) ids.push(...(e.ids || []));
    }
    if (!ids.length) return;
    if (confirm(`移除该账户相关的 ${ids.length} 笔已追踪流水？`)) untrackFlows(ids);
  };
  $('#drawerBody').querySelectorAll('tr[data-acct]').forEach(tr => tr.onclick = () => {
    closeDrawer(); switchTab('search');
    $('#fTracked').checked = false;
    $('#fAccount').value = account; $('#fCounter').value = tr.dataset.acct;
    doSearch(1);
  });
  for (const selector of ['#ndFocusApply', '#ndIdSave', '#ndPhotoUp', '#ndPhotoFile', '#ndPhotoRe', '#ndPhotoClr', '#ndToggleMain', '#ndBrowse', '#ndNeighborhood', '#ndRemove']) {
    const element = $(selector);
    for (const name of ['onclick', 'onchange']) {
      const action = element?.[name];
      if (typeof action === 'function') element[name] = (...args) => current() ? action.apply(element, args) : undefined;
    }
  }

}

async function showEdgeDrawer(m, item) {
  const context = caseContext();
  selectItem(item);
  const request = ++drawerSequence, view = state.graphView, revision = view.revision;
  const current = () => request === drawerSequence && isCurrentCase(context) && view === state.graphView && revision === view.revision;
  $('#drawerTitle').textContent = '资金流向';
  $('#drawer').classList.add('open');
  $('#drawerBody').innerHTML = '<div class="muted">加载中…</div>';
  const r = await api(`/api/flows?case_id=${context.caseId}&ids=` + (m.ids || m.data.ids).join(',') + '&size=500');
  if (!current()) return;
  const d = m.data;
  $('#drawerBody').innerHTML = `
    <div class="kv"><label>付款方</label><div>${esc(d.source_name || '')}<br><span class="mono wrap">${esc(d.source)}</span></div></div>
    <div class="kv"><label>收款方</label><div>${esc(d.target_name || '')}<br><span class="mono wrap">${esc(d.target)}</span></div></div>
    <table class="tbl mini">
      <tr><td>笔数</td><td>${d.cnt} 笔</td></tr>
      <tr><td>合计</td><td class="amt">${totalsHtml(d.amount_totals, d.total, d.currencies?.[0])}</td></tr>
      <tr><td>时间跨度</td><td>${fmtTime(d.tmin)} ~ ${fmtTime(d.tmax)}</td></tr>
    </table>
    <div class="sec">该流向的已追踪流水</div>
    <div class="tbl-wrap"><table class="tbl mini">
      <thead><tr><th>时间</th><th>金额</th><th>摘要</th><th></th></tr></thead>
      <tbody>${r.rows.map(f => `
        <tr>
          <td class="nowrap">${fmtTime(f.trade_time)}</td>
          <td class="amt">${moneyHtml(f.amount, f.currency)}</td>
          <td>${esc(f.summary || '')}</td>
          <td class="nowrap">
            <button class="btn tiny ctx-btn" data-id="${f.id}">前后X笔</button>
            <button class="btn tiny danger del-btn" data-id="${f.id}">移除</button>
          </td>
        </tr>`).join('')}</tbody>
    </table></div>`;
  $('#drawerBody').querySelectorAll('.ctx-btn').forEach(b => b.onclick = () => { if (current()) openContext(Number(b.dataset.id)); });
  $('#drawerBody').querySelectorAll('.del-btn').forEach(b => b.onclick = () => { if (current()) untrackFlows([Number(b.dataset.id)]); });
}

/* ---------------- 交易上下文（对手前后 X 笔） ---------------- */
/* 系统结算类户名：不是真实交易对手，其"视角"无追踪意义 */
const SYSTEM_ACCOUNT_NAMES = ['正常', '利息', '手续费', '小额费', '待报解', '税', '佣金', '年费'];

function isSystemAccount(name) {
  const nm = (name || '').trim();
  if (!nm) return false;
  return SYSTEM_ACCOUNT_NAMES.some(sys => nm === sys || nm.includes(sys));
}

async function openContext(flowId) {
  // 默认视角：对手方是系统账户时切到本人账户（付款方视角看收款系统户 → 反之）
  let side = (state.ctx && state.ctx.side) || 'payee';
  const g = state.lastGraph;
  if (g && g.nodes) {
    // 从流水行取对手户名（打开前无法知道，这里先查图上节点不可靠，改由首次加载后自动纠正）
  }
  state.ctx = { flowId, side, x: Number($('#ctxX').value) || 5, autoSwitch: true, book: 'all' };
  $('#ctxBook').value = 'all';
  $('#ctxModal').style.display = 'flex';
  $$('#ctxTabs .stab').forEach(t => t.classList.toggle('active', t.dataset.side === state.ctx.side));
  $('#ctxTbody').innerHTML = '<tr><td colspan="7" class="muted" style="padding:16px">加载中…</td></tr>';
  await loadContext();
}

async function loadContext() {
  const context = caseContext();
  const request = state.ctx;
  const { flowId, side, x } = state.ctx;
  const r = await api(`/api/context/${flowId}?x=${x}&side=${side}&case_id=${context.caseId}`);
  if (!isCurrentCase(context) || state.ctx !== request) return;
  // 对手方是系统账户（正常/利息/手续费等）→ 该视角无追踪意义，自动切到另一侧真实账户
  if (state.ctx.autoSwitch) {
    state.ctx.autoSwitch = false;
    const a = r.anchor || {};
    const counterName = side === 'payee' ? a.payee_name : a.payer_name;
    const selfName = side === 'payee' ? a.payer_name : a.payee_name;
    const counterAcct = side === 'payee' ? a.payee_account : a.payer_account;
    if (isSystemAccount(counterName) || isSystemAccount(counterAcct)) {
      const other = side === 'payee' ? 'payer' : 'payee';
      toast(`对手方"${counterName}"为系统结算账户，已自动切换到${other === 'payer' ? '付款' : '收款'}方视角（${selfName || '本人账户'}）`);
      state.ctx.side = other;
      $$('#ctxTabs .stab').forEach(t => t.classList.toggle('active', t.dataset.side === other));
      return loadContext();
    }
  }
  state.ctx.data = r;
  const a = r.anchor;
  $('#ctxAnchor').innerHTML =
    `锚点交易 #${a.id}：${fmtTime(a.trade_time)} · ${moneyHtml(a.amount, a.currency)} · ${esc(a.payer_name || '')} → ${esc(a.payee_name || '')}`;
  $('#ctxPos').textContent =
    `${side === 'payee' ? '收款方' : '付款方'}：${r.account} 共 ${fmtCount(r.total)} 笔，锚点为第 ${fmtCount(r.pos)} 笔`;
  renderCtxRows(r);
  renderSuggestions(r);
}

/* ---------------- 智能推荐（疑似来源 / 去向） ---------------- */
function renderSuggestions(r) {
  const wrap = $('#ctxSuggest');
  const sug = r.suggestions || { sources: [], targets: [] };
  const item = (s, kind) => {
    const high = s.score >= 75;
    return `
      <div class="sg-item" data-ids="${s.ids.join(',')}" title="点击勾选其在下方表格中的流水">
        <div class="sg-main"><b>${esc(s.name || '(未知)')}</b><span class="mono">${esc(s.account)}</span></div>
        <div class="sg-score${high ? ' high' : ''}">${s.score}</div>
        <div class="sg-reason">${s.reasons.map(esc).join(' · ')}</div>
        ${(s.tags || []).length ? `<div class="sg-tags">${s.tags.map(t => `<span class="sg-tag ${t.includes('拆分') ? 'split' : 'fast'}">${esc(t)}</span>`).join('')}</div>` : ''}
      </div>`;
  };
  const group = (title, kind, list, emptyText) => `
    <div class="sgroup">
      <div class="sg-title"><span class="sg-kind ${kind}">${title}</span><span class="muted">按资金特征评分，点击卡片勾选对应流水</span></div>
      ${list.length ? `<div class="sg-items">${list.map(s => item(s, kind)).join('')}</div>`
                    : `<div class="sg-empty">${emptyText}</div>`}
    </div>`;
  wrap.innerHTML =
    group('疑似来源（锚点前转入）', 'src', sug.sources || [], '前后范围内暂无锚点前转入本账户的对手') +
    group('疑似去向（锚点后转出）', 'tgt', sug.targets || [], '前后范围内暂无锚点后本账户转出的对手');
  wrap.querySelectorAll('.sg-item').forEach(el => {
    el.onclick = () => {
      const ids = el.dataset.ids.split(',').map(Number);
      const boxes = $$('#ctxTbody .ctx-ck').filter(c => ids.includes(Number(c.dataset.id)) && !c.disabled);
      if (!boxes.length) return toast('其流水已上图或不在当前范围内', 'err');
      const willCheck = !boxes.every(c => c.checked);
      boxes.forEach(c => {
        if (c.checked !== willCheck) {
          c.checked = willCheck;
          c.closest('tr').classList.toggle('picked-row', willCheck);
        }
      });
      el.classList.toggle('picked', willCheck);
    };
  });
}

function renderCtxRows(r) {
  const book = (state.ctx && state.ctx.book) || 'all';
  const rows = r.rows.filter(f => book === 'all' || f.book === book);
  if (!rows.length) {
    $('#ctxTbody').innerHTML = '<tr><td colspan="8" class="muted" style="padding:16px">该流水归属视角下，前后范围内暂无流水</td></tr>';
    return;
  }
  $('#ctxTbody').innerHTML = rows.map(f => {
    const isAnchor = f.id === r.anchor.id;
    const rel = isAnchor ? '⚓ 本笔' : (f.rn < r.pos ? `前${r.pos - f.rn}` : `后${f.rn - r.pos}`);
    const tracked = state.tracked.has(f.id);
    const dirTag = f.dir === 'in' ? '<span class="tag in">转入</span>' : '<span class="tag out">转出</span>';
    // 流水归属：该行记录来自谁的账单（锚点账户=本方 / 对手方；未声明归属则不标）
    const bookTag = f.book === 'self'
      ? '<span class="tag" style="background:#16324f;color:#7ab8ff;border:1px solid #2d5a8a" title="该行来自锚点账户（本方）的流水账单">本方</span>'
      : f.book === 'counter'
        ? '<span class="tag" style="background:#3a2e1e;color:#e8b96a;border:1px solid #6b5a1e" title="该行来自对手方账户的流水账单">对手</span>'
        : '';
    const counter = f.dir === 'in'
      ? `${esc(f.payer_name || '')}<br><span class="mono">${esc(f.payer_account)}</span>`
      : `${esc(f.payee_name || '')}<br><span class="mono">${esc(f.payee_account)}</span>`;
    const selCell = tracked
      ? '<span class="tag on">已上图</span>'
      : `<input type="checkbox" class="ctx-ck" data-id="${f.id}">`;
    const bal = (f.balance == null || f.balance === '') ? '—' : moneyHtml(f.balance, f.currency);
    return `<tr class="${isAnchor ? 'anchor' : ''}">
      <td>${selCell}</td>
      <td class="nowrap">${rel}</td>
      <td>${dirTag} ${bookTag}</td>
      <td class="nowrap">${fmtTime(f.trade_time)}</td>
      <td class="amt">${moneyHtml(f.amount, f.currency)}</td>
      <td class="amt">${bal}</td>
      <td>${counter}</td>
      <td>${esc(f.summary || '')}</td>
    </tr>`;
  }).join('');
}

/* ---------------- 导入 ---------------- */
const FIELD_LABELS = {
  trade_no: '交易流水号', trade_time: '交易时间*', _time2: '时间(时分秒列)',
  payer_name: '付款户名*', payer_account: '付款账号', payer_bank: '付款开户行',
  payee_name: '收款户名*', payee_account: '收款账号', payee_bank: '收款开户行',
  self_name: '本方户名', self_account: '本方账号*',
  counter_name: '对方户名*', counter_account: '对方账号', counter_bank: '对方开户行',
  direction: '收支标志*', income_amt: '收入金额列', expense_amt: '支出金额列',
  amount: '交易金额*', balance: '余额', currency: '币种（选填）',
  summary: '摘要', channel: '交易类型/渠道',
};

function renderImport(r) {
  $('#impStep1').style.display = 'none';
  $('#impStep2').style.display = 'block';
  $('#impMeta').innerHTML = `<b>${esc(r.filename)}</b> · 识别为 <b>${r.mode === 'A' ? '双边流水' : '单账户流水'}</b>`;
  $$('input[name=impMode]').forEach(i => { i.checked = i.value === r.mode; });
  const fields = r.mode === 'A'
    ? ['trade_no', 'trade_time', '_time2', 'self_account', 'payer_name', 'payer_account', 'payer_bank', 'payee_name', 'payee_account', 'payee_bank', 'amount', 'balance', 'currency', 'summary', 'channel']
    : ['trade_no', 'trade_time', '_time2', 'self_name', 'self_account', 'counter_name', 'counter_account', 'counter_bank', 'direction', 'income_amt', 'expense_amt', 'amount', 'balance', 'currency', 'summary', 'channel'];
  $('#impMapping').innerHTML = fields.map(f => {
    const lbl = (f === 'self_account' && r.mode === 'A')
      ? '本方账号（标主，选填）'
      : (FIELD_LABELS[f] || f);
    return `
    <div class="map-pair${r.mapping[f] ? ' hot' : ''}">
      <label class="ml">${lbl}</label>
      <select data-field="${f}">
        <option value="">（不使用）</option>
        ${r.columns.map(c => `<option value="${esc(c)}" ${r.mapping[f] === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}
      </select>
    </div>`;
  }).join('');
  // 本方账号勾选区：显式选择哪些账号标为主账号
  const saf = r.self_accounts_in_file || [];
  $('#impMainSelWrap').style.display = saf.length ? 'block' : 'none';
  $('#impMainTitle').textContent = `本文件的本方账号（默认全部标为主账号，可取消勾选非主账号，共 ${saf.length} 个）`;
  $('#impMainSel').innerHTML = saf.map((x, i) => `
    <label class="mainsel"><input type="checkbox" class="mk-acct" value="${esc(x.account)}" checked>
      ${esc(x.name || '(未知)')}　<span class="mono">${esc(x.account)}</span>（${x.count} 笔）</label>`).join('');
  $('#impTbody').innerHTML = r.preview.map(row => `
    <tr>
      <td class="nowrap">${fmtTime(row.trade_time)}</td>
      <td>${esc(row.payer_name)}<br><span class="mono">${esc(row.payer_account)}</span></td>
      <td>${esc(row.payee_name)}<br><span class="mono">${esc(row.payee_account)}</span></td>
      <td class="amt">${moneyHtml(row.amount, row.currency)}</td>
      <td>${esc(row.summary || '')}</td>
    </tr>`).join('');
  const st = r.stats;
  $('#impStats').textContent = `共 ${fmtCount(st.total)} 行 · 可导入 ${fmtCount(st.ok)} 行 · 跳过 ${fmtCount(st.skipped)} 行（时间/金额无法解析）`;
  $('#impBad').innerHTML = (st.bad_examples || []).map(b => `<div class="warn">示例：${esc(b.row)} — ${esc(b.reason)}</div>`).join('');
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
  const context = caseContext();
  const list = await api(`/api/batches?case_id=${context.caseId}`);
  if (!isCurrentCase(context, false)) return;
  $('#batchList').innerHTML = list.length ? list.map(b => `
    <div class="batch-item">
      <div><b>${esc(b.filename)}</b>
        <span class="tag">${b.mode === 'A' ? '双边' : (b.mode === 'B' ? '单账户' : '演示')}</span></div>
      <div class="muted">导入 ${fmtCount(b.rows_imported)} 笔 · 跳过 ${fmtCount(b.rows_skipped || 0)} · 重复 ${fmtCount(b.rows_dup || 0)}<br>${fmtTime(b.imported_at)}</div>
      <button class="btn tiny danger" data-id="${b.id}">删除该批次</button>
    </div>`).join('') : '<div class="muted">本案件暂无导入批次</div>';
  $('#batchList').querySelectorAll('button').forEach(b => b.onclick = async () => {
    if (!confirm('删除该批次将同时移除其全部流水及对应的图谱引用，确定？')) return;
    await api(`/api/batches/${b.dataset.id}?case_id=${state.caseId}`, { method: 'DELETE' });
    toast('批次已删除');
    await refreshAll();
  });
}

/* ---------------- 事件绑定 ---------------- */
function switchTab(name) {
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  $('#tab-search').style.display = name === 'search' ? 'flex' : 'none';
  $('#tab-batches').style.display = name === 'batches' ? 'flex' : 'none';
  if (name === 'batches') loadBatches().catch(() => {});
}

function bindWorkspaceActions() {
  const triggers = new Map();
  $$('.action-dialog').forEach(dialog => {
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
  bindWorkspaceActions();
  bindFlowTableView();
  bindFlowTableScroll();
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
      state.forceFit = true;
      await loadGraph();
      await doSearch(state.search.page || 1);
    } catch (err2) {
      toast('批量追踪失败：' + err2.message, 'err');
    } finally {
      btn.disabled = false;
    }
  };

  // 案件（一案一数据：切换案件后查询/统计/图谱/批次全部随之隔离）
  $('#caseSelect').onchange = async e => {
    selectCase(e.target.value);
    await refreshStats();
    await loadGraph();
    await refreshCanvasList(null).catch(() => {});
    doSearch(1);
  };
  // 案件包：导出当前案件 / 导入别人发来的案件包
  $('#btnExportCase').onclick = () => {
    const cur = state.cases.find(c => c.id === state.caseId);
    if (!confirm(`把当前案件「${cur ? cur.name : state.caseId}」打包导出？\n\n生成的 .fundcase 文件发给同事，对方在自己的软件里「📥导入案件包」即可打开（不影响他已有案件）。`)) return;
    const a = document.createElement('a');
    a.href = `/api/cases/${state.caseId}/export`;
    a.click();
    toast('案件包已开始下载（含流水/画布/主账号/身份证/照片）');
  };
  $('#btnImportCase').onclick = () => $('#casePkgFile').click();
  $('#casePkgFile').onchange = async () => {
    const f = $('#casePkgFile').files[0];
    if (!f) return;
    toast('正在导入案件包…');
    const fd = new FormData();
    fd.append('file', f);
    try {
      const res = await fetch('/api/cases/import', { method: 'POST', body: fd });
      const r = await res.json();
      if (!res.ok) throw new Error(r.detail || '导入失败');
      toast(`案件包导入完成：「${r.name}」（${fmtCount(r.flows)} 笔流水），已自动切换`);
      await loadCases();
      const sel = $('#caseSelect');
      sel.value = String(r.case_id);
      sel.dispatchEvent(new Event('change'));
    } catch (e) { toast('案件包导入失败：' + e.message, 'err'); }
    $('#casePkgFile').value = '';
  };
  $('#btnDelCase').onclick = async () => {
    if (state.caseId === 1) return toast('默认案件不可删除', 'err');
    const cur = state.cases.find(c => c.id === state.caseId);
    const cname = cur ? cur.name : state.caseId;
    const cnt = (state.stats && state.stats.flows) || 0;
    if (!confirm(`确定删除案件「${cname}」？\n\n将删除其全部数据：流水 ${fmtCount(cnt)} 笔、导入批次、主账号标记、图谱追踪。\n删除后不可恢复！`)) return;
    try {
      const r = await api(`/api/cases/${state.caseId}`, { method: 'DELETE' });
      toast(`案件已删除（含 ${fmtCount(r.deleted_flows)} 笔流水）`);
      await loadCases();               // 自动回落到剩余案件的第一个
      state.positions = {};
      state.edgeLabelPos = {};
      state.forceFit = true;
      closeDrawer();
      await refreshStats();
      await loadGraph();
      await refreshCanvasList(null).catch(() => {});
      doSearch(1);
    } catch (e) { /* api 已提示 */ }
  };
  $('#btnNewCase').onclick = async () => {
    const name = await promptText('新案件名称：', '');
    if (!name) return;
    const c = await api('/api/cases', { method: 'POST', ...jsonOpts({ name }) });
    toast(`已创建案件「${c.name}」，当前数据不会被带入`);
    await loadCases();
    selectCase(c.id);
    await refreshStats();
    await loadGraph();
    doSearch(1);
  };

  $('#graphLabelMode').onchange = event => {
    state.graphView.labelMode = ['auto','focus','all'].includes(event.target.value) ? event.target.value : 'auto';
    scheduleGraphLabels();
  };
  $('#btnNeighborhood').onclick = () => exploreNeighborhood($('#neighborAccount').value.trim(), true);
  $('#neighborAccount').onkeydown = event => { if (event.key === 'Enter') exploreNeighborhood(event.target.value.trim(), true); };
  $('#btnExpandNeighbors').onclick = () => exploreNeighborhood(state.graphView.root);
  $('#btnGraphOverview').onclick = showGraphOverview;
  if ($('#btnMinimap')) $('#btnMinimap').onclick = () => {
    const panel=$('#graphMinimapPanel'); panel.hidden=!panel.hidden;
    $('#btnMinimap').setAttribute('aria-pressed',String(!panel.hidden));
  };
  $$('#graphMinimapPanel [data-pan]').forEach(button => {
    button.onclick=() => {
      if (state.graphLoading || !state.graph) return;
      const pan={left:[100,0],right:[-100,0],up:[0,100],down:[0,-100]}[button.dataset.pan];
      state.graph.translate(...pan); scheduleGraphViewport(); scheduleGraphLabels();
    };
  });
  // 图谱工具
  $('#layoutSel').onchange = e => { state.layout = e.target.value; relayout(true); };
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
  // 导入账号-身份证对照表
  const idFile = $('#idFile');
  if (idFile) {
    $('#btnImportIds').onclick = () => idFile.click();
    $('#btnIdTpl').onclick = () => {
      const a = document.createElement('a');
      a.href = '/api/account-ids/template';
      a.download = '账号身份证对照表模板.xlsx';
      a.click();
      toast('模板已下载：填好「卡号」和「身份证号」两列后，点「导入身份证」选择该文件');
    };
    idFile.onchange = async () => {
      const f = idFile.files[0];
      if (!f) return;
      const fd = new FormData();
      fd.append('file', f);
      fd.append('case_id', state.caseId);
      try {
        const res = await fetch('/api/account-ids/import', { method: 'POST', body: fd });
        const r = await res.json();
        if (!res.ok) throw new Error(r.detail || '导入失败');
        let msg = `身份证导入完成：成功 ${r.imported} 个`;
        if (r.invalid) msg += `，格式无效 ${r.invalid} 个`;
        if (r.blank_account) msg += `，账号为空 ${r.blank_account} 行`;
        toast(msg);
        await loadGraph();
      } catch (e) { toast('身份证导入失败：' + e.message, 'err'); }
      idFile.value = '';
    };
  }
  syncEdgeTypeControl();
  $('#edgeTypeSel').onchange = e => {
    if (!['line', 'polyline', 'quadratic'].includes(e.target.value)) return;
    state.edgeType = e.target.value;
    state.edgeTypes = {};
    preferenceStorage.setItem('fund_edge_type', state.edgeType);
    renderGraph();
  };
  $('#btnRelayout').onclick = () => relayout(true);
  $('#btnFit').onclick = () => fitGraph(1.1);
  $('#btnAutoMain').onclick = async () => {
    const r = await api('/api/main-accounts/auto', { method: 'POST', ...jsonOpts({ case_id: state.caseId }) });
    if (r.detected.length) {
      toast(`自动识别主账号 ${r.detected.length} 个：` + r.detected.map(d => d.name || d.account.slice(-6)).join('、'));
    } else {
      toast('未在单账户流水中识别到主账号（双边流水批次无法自动识别，可点节点手动标记）', 'err');
    }
    await loadGraph();
  };

  $('#btnClearAutoMain').onclick = async () => {
    if (!confirm('清除本案件全部"自动识别"的主账号标记？\n（手动标记的会保留）')) return;
    const r = await api('/api/main-accounts/clear-auto', { method: 'POST', ...jsonOpts({ case_id: state.caseId }) });
    toast(`已清除 ${r.cleared} 个自动识别的主账号标记`);
    await loadGraph();
  };

  $('#btnClearGraph').onclick = async () => {
    const where = state.canvasName ? `画布「${state.canvasName}」` : '默认画布';
    if (!confirm(`清空${where}的全部追踪（已上图流水）？其他画布不受影响。`)) return;
    await api(`/api/graph?case_id=${state.caseId}&canvas_id=${state.canvasId || 0}`, { method: 'DELETE' });
    state.positions = {};
    state.edgeLabelPos = {}; state.edgeBendPos = {}; state.edgeSnapshotLabels = {}; state.edgeTypes = {};
    toast(`${where}已清空`);
    await loadGraph();
  };

  // 导出
  $('#btnExportXlsx').onclick = async () => {
    const context = caseContext();
    try {
      const response = await fetch(`/api/export/tracked.xlsx?case_id=${context.caseId}&canvas_id=${context.canvasId || 0}`);
      if (!isCurrentCase(context)) return;
      if (!response.ok) throw new Error(`下载失败（HTTP ${response.status}）`);
      const blob = await response.blob();
      if (!isCurrentCase(context)) return;
      downloadBlob(blob, `资金追踪流水_案件${context.caseId}_画布${context.canvasId || 0}.xlsx`);
      toast('Excel 已开始下载（含「追踪流水」与「待调取账号」两张表）');
    } catch (error) {
      if (isCurrentCase(context)) toast('Excel 导出失败：' + error.message, 'err');
    }
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
    const c = $('#graphContainer'), W = c.clientWidth, H = c.clientHeight;
    const graph = state.graph, view = state.graphView;
    let camera = null, finished = false, timer;
    const done = url => {
      if (finished) return;
      finished = true; clearTimeout(timer);
      if (cancelGraphImageExport === cancel) cancelGraphImageExport = null;
      const owned = current();
      view.exportDepth = Math.max(0, view.exportDepth - 1);
      if (owned) {
        try { graph.changeSize(W, H); } catch (_) { /* Release ownership even if G6 restoration fails. */ }
        if (camera) {
          try { graph.get('group').setMatrix(camera); graph.paint(); } catch (_) { /* The graph may be disposing. */ }
          try { applyGraphViewport(); applyGraphLabels(); } catch (_) { /* Keep the queue usable. */ }
        } else { try { fitGraph(1.1); } catch (_) { /* Older host adapters have no camera API. */ } }
      }
      imageExportActive = false;
      resolve(owned ? url : null);
      Promise.resolve().then(drainImageExports);
    };
    const cancel = () => done(null);
    cancelGraphImageExport = cancel;
    view.exportDepth += 1;
    try {
      const matrix = graph.get?.('group')?.getMatrix?.();
      camera = matrix ? Array.from(matrix) : null;
      restoreGraphClipping(view);
      graph.changeSize(W * scale, H * scale);
      graph.fitView(60);
      applyGraphLabels();
      timer = setTimeout(() => {
        if (finished) return;
        if (!current()) return done(null);
        try { done(graph.toDataURL()); } catch (_) { done(null); }
      }, 400);
    } catch (_) { done(null); }
  }
  function exportHiResPng(scale = 2, current = () => true) {
    return new Promise(resolve => { imageExportQueue.push({ scale, current, resolve }); drainImageExports(); });
  }

  function downloadBlob(blob, name) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }

  // 采集当前画布快照：节点坐标/样式、边线型/折点、标注内容及其拖动位置（所见即所得导出用）
  async function collectGraphSnapshot(options = {}) {
    const g = state.graph, context = caseContext(), revision = layoutSequence, view = state.graphView, viewRevision = view.revision;
    const current = () => isCurrentCase(context) && state.graph === g && layoutSequence === revision
      && view === state.graphView && viewRevision === view.revision;
    if (!g || !state.graphInited || state.graphLoading) return null;
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
    const scope = options.scope === 'all' ? 'all' : 'visible';
    const liveNodes = g.getNodes(), liveEdges = g.getEdges();
    const nodeModels = new Map(liveNodes.map(item => [item.getModel().id, item.getModel()]));
    const edgeItems = new Map(liveEdges.map(item => [edgeKey(item.getModel()), item]));
    const nodeSources = scope === 'all' ? state.lastGraph.nodes.map(source => {
      const live = nodeModels.get(source.account);
      if (live) return live;
      const position = state.positions[source.account] || FundsGraphLayout.seed(source.account, width, height);
      const scale = state.focusNodes.get(source.account) || 1;
      const size = ((28 + Math.min(24, (source.in_cnt + source.out_cnt) * 2))
        + (source.is_main === 1 ? 6 : source.is_same_name === 1 ? 3 : 0)) * scale;
      return {id: source.account, ...position, label: nodeLabel(source), size,
        style: nodePhotoUrl(source) ? {} : nodeStyle(source), data: source};
    }) : liveNodes.map(item => item.getModel());
    const prepared = nodeSources.map(m => {
      const source = m.data || {};
      return { photoUrl: nodePhotoUrl(source), node: {
        id: m.id, x: Math.round((m.x || 0) * 100) / 100, y: Math.round((m.y || 0) * 100) / 100,
        label: m.fundsLabel ?? m.label ?? nodeLabel(source), size: m.size || 32, style: (m.style && { ...m.style }) || {},
        labelVisible: scope === 'all' || !state.graphView.policy || state.graphView.policy.nodeIds.has(m.id),
        data: metadata(source, ['out_cnt', 'in_cnt', 'out_amt', 'in_amt', 'out_totals', 'in_totals']),
      } };
    });
    const edgeSources = scope === 'all' ? state.lastGraph.edges : liveEdges.map(item => item.getModel().data || item.getModel());
    const edges = edgeSources.map(source => {
      const item = edgeItems.get(edgeKey(source));
      const m = item?.getModel() || { ...source, label: edgeLabel(source), data: source,
        type: graphEdgeType(source),
        controlPoints: state.edgeBendPos[edgeKey(source)] };
      const children = item?.getContainer?.()?.get('children') || [];
      const text = children.find(child => child.get('name') === 'text-shape');
      const background = children.find(child => child.get('name') === 'text-bg-shape');
      const savedLabel = state.edgeSnapshotLabels[edgeKey(m)] || {};
      return {
        source: m.source, target: m.target, type: m.type || state.edgeType,
        controlPoints: (m.controlPoints || []).map(point => ({ x: +(+point.x).toFixed(2), y: +(+point.y).toFixed(2) })),
        label: m.fundsLabel ?? m.label ?? edgeLabel(m.data || source),
        labelVisible: scope === 'all' || !state.graphView.policy || state.graphView.policy.edgeKeys.has(edgeKey(m)),
        lx: text ? +(text.attr('x') || 0).toFixed(2) : savedLabel.lx ?? null,
        ly: text ? +(text.attr('y') || 0).toFixed(2) : savedLabel.ly ?? null,
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
    return current() ? { case_name: caseName, w: width, h: height, nodes: prepared.map(entry => entry.node), edges,
      view: {scope, mode: state.graphView.visibleIds ? 'neighborhood' : 'overview',
        total_nodes: state.lastGraph.nodes.length, total_edges: state.lastGraph.edges.length,
        visible_nodes: liveNodes.length, visible_edges: liveEdges.length, label_mode: state.graphView.labelMode} } : null;
  }

  async function exportGraphHtml() {
    const context = caseContext();
    const snap = await window.__collectSnapshot();
    if (!isCurrentCase(context)) return;
    if (!snap || !snap.nodes.length) return toast('图谱为空', 'err');
    try {
      const res = await fetch('/api/export/graph.html', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(snap) });
      if (!isCurrentCase(context)) return;
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      if (!isCurrentCase(context)) return;
      downloadBlob(blob, `${snap.case_name || '资金图谱'}_图谱.html`);
      toast('HTML 图谱已导出（保留当前布局与标注，双击打开可拖拽）');
    } catch (e) { toast('HTML 导出失败：' + e.message, 'err'); }
  }
  window.__collectSnapshot = collectGraphSnapshot;   // 供调试/自动化取快照

  $('#btnExportPng').onclick = async () => {
    if (!state.lastGraph.nodes.length) return toast('图谱为空', 'err');
    const context = caseContext(), graph = state.graph, view = state.graphView, revision = view.revision, layoutRevision = layoutSequence;
    const current = () => isCurrentCase(context) && graph === state.graph && view === state.graphView
      && revision === view.revision && layoutRevision === layoutSequence;
    const suffix = view.visibleIds ? `_邻域${graph.getNodes().length}-${state.lastGraph.nodes.length}` : '';
    const filename = `资金图谱_案件${context.caseId}${suffix}`;
    const fmt = $('#exportFmt').value;
    if (fmt === 'html') {
      await exportGraphHtml();
      return;
    }
    // PDF 用 3 倍采样（配合服务端无损嵌入，保证清晰度）；PNG 用 2 倍
    const url = await exportHiResPng(fmt === 'pdf' ? 3 : 2, current);
    if (!current()) return;
    if (!url || !url.startsWith('data:image')) return toast('导出失败：未获得图像数据', 'err');
    if (fmt === 'pdf') {
      try {
        const res = await fetch('/api/export/graph.pdf', { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image: url }) });
        if (!current()) return;
        if (!res.ok) throw new Error(await res.text());
        const blob = await res.blob();
        if (!current()) return;
        downloadBlob(blob, `${filename}.pdf`);
        toast('PDF 已导出');
      } catch (e) { if (current()) toast('PDF 导出失败：' + e.message, 'err'); }
    } else {
      // Use the same Blob download path as HTML/PDF. Large data: URLs can be
      // generated successfully while the browser declines their navigation.
      const binary = atob(url.slice(url.indexOf(',') + 1));
      const bytes = Uint8Array.from(binary, ch => ch.charCodeAt(0));
      downloadBlob(new Blob([bytes], { type: 'image/png' }), `${filename}.png`);
      toast('PNG 已导出');
    }
  };

  // 演示数据（导入到当前案件）
  const genSample = async () => {
    const btns = ['#btnSample', '#btnSample2'].map(s => $(s));
    btns.forEach(b => b && (b.disabled = true));
    try {
      const r = await api('/api/sample-data', { method: 'POST', ...jsonOpts({ case_id: state.caseId }) });
      toast(`已生成演示数据：${fmtCount(r.ok)} 笔（当前案件）`);
      await refreshAll();
    } finally {
      btns.forEach(b => b && (b.disabled = false));
    }
  };
  $('#btnSample').onclick = genSample;
  $('#btnSample2').onclick = genSample;

  // 抽屉
  $('#btnCloseDrawer').onclick = closeDrawer;

  // 上下文弹窗
  $('#ctxClose').onclick = () => { $('#ctxModal').style.display = 'none'; };
  $('#ctxModal').addEventListener('click', e => { if (e.target === $('#ctxModal')) $('#ctxModal').style.display = 'none'; });
  $$('#ctxTabs .stab').forEach(t => t.onclick = async () => {
    $$('#ctxTabs .stab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    state.ctx.side = t.dataset.side;
    await loadContext();
  });
  $('#ctxX').onchange = async () => {
    let v = Math.max(1, Math.min(50, Number($('#ctxX').value) || 5));
    $('#ctxX').value = v;
    state.ctx.x = v;
    await loadContext();
  };
  $('#ctxBook').onchange = () => {
    state.ctx.book = $('#ctxBook').value;
    if (state.ctx.data) renderCtxRows(state.ctx.data);   // 客户端过滤，无需重新请求
  };
  $('#ctxTrack').onclick = async () => {
    const ids = $$('#ctxTbody .ctx-ck').filter(c => c.checked).map(c => Number(c.dataset.id));
    if (!ids.length) return toast('请先勾选要上图的流水', 'err');
    await trackFlows(ids);
    $('#ctxModal').style.display = 'none';
  };

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
