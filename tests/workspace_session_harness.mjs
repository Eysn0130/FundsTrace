import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = name => readFileSync(new URL(`../static/${name}.js`, import.meta.url), 'utf8');
const app = source('app'), editor = source('canvas-editor');
function section(text, start, end) {
  const from = text.indexOf(start), to = text.indexOf(end, from);
  assert.ok(from >= 0 && to > from, `source section ${start}`);
  return text.slice(from, to);
}
const plain = value => JSON.parse(JSON.stringify(value));
const flush = () => new Promise(resolve => setImmediate(resolve));

// Run the shipped switch/query/session adapters. Only DOM, G6 and GET services
// are simulated; no copy of the capture, restore or switching implementation.
function harness() {
  const elements = new Map(), callbacks = new Map(), requests = [], timers = [], notices = [];
  let boxes = [], tableRows = [], matrix = [1, 0, 0, 0, 1, 0, 40, 60, 1], intent = 0, fitCalls = 0;
  const authorities = new Map(), canvasLists = new Map();
  const attributes = () => new Map();
  function element(id) {
    if (elements.has(id)) return elements.get(id);
    const attrs = attributes(), classes = new Set();
    const item = { id, value: '', checked: false, indeterminate: false, disabled: false, hidden: false, inert: false,
      style: {}, dataset: {}, textContent: '', scrollLeft: 0, scrollTop: 0,
      classList: { contains: key => classes.has(key), add: key => classes.add(key), remove: key => classes.delete(key),
        toggle(key, on) { if (on) classes.add(key); else classes.delete(key); } },
      setAttribute(key, value) { attrs.set(key, String(value)); }, getAttribute: key => attrs.get(key) ?? null,
      contains: () => false, closest: () => null, focus() {}, addEventListener() {}, appendChild() {},
      dispatchEvent(event) { this['on' + event.type]?.(event); }, click() { this.onclick?.({ currentTarget: this }); },
      querySelector: () => ({ src: '', textContent: '', title: '', classList: { toggle() {} } }), querySelectorAll: () => [],
      get selectedOptions() { return [{ textContent: this.value === '0' ? '默认画布' : `Canvas ${this.value}` }]; },
    };
    Object.defineProperty(item, 'innerHTML', { set(value) { this.html = value; if (id === 'flowTbody' && !value) { boxes = []; tableRows = []; } }, get() { return this.html || ''; } });
    elements.set(id, item); return item;
  }
  element('tab-batches').style.display = 'none'; element('bulkDir').value = 'target'; element('bulkX').value = '5';
  element('flowTableDensity').value = 'compact'; element('flowTableWrap').value = 'wrap';
  const body = element('body'), navItems = ['home', 'cases'].map(page => ({ ...element('nav-' + page), dataset: { navPage: page }, removeAttribute() {} }));
  element('casesFrame').contentWindow = { postMessage() {} };
  const doc = { activeElement: body, body,
    querySelector: selector => selector.startsWith('#') ? element(selector.slice(1)) : element(selector),
    querySelectorAll: selector => selector === '[data-nav-page]' ? navItems : [],
    createElement: () => element('created'), addEventListener() {},
  };
  const $ = selector => doc.querySelector(selector);
  const $$ = selector => selector === '#flowTbody .flow-ck' ? boxes : selector === '#flowTbody tr[data-id]' ? tableRows : [];
  const state = { caseId: 1, caseGeneration: 1, canvasId: 0, canvasGeneration: 1, canvasName: '', graphLoading: false,
    cases: [{ id: 1, name: 'Synthetic A' }, { id: 2, name: 'Synthetic B' }], stats: {}, search: { page: 1, total: 0, size: 50 },
    positions: {}, edgeLabelPos: {}, edgeBendPos: {}, edgeSnapshotLabels: {}, edgeTypes: {},
    tracked: new Set(), mainAccounts: new Set(), focusNodes: new Map(), layout: 'auto', edgeType: 'quadratic',
    edgeLabels: new Set(['money']), showIds: true, showPhotos: false, lastGraph: { nodes: [], edges: [], tracked_ids: [] } };
  const selection = { ids: new Set(), rebind(ids) { this.ids = new Set(ids); }, clear() { this.ids.clear(); }, destroy() {} };
  const newView = () => ({ visibleIds: null, root: null, direction: 'both', labelMode: 'auto', selectedAccount: null,
    selectedEdge: null, edgeItems: new Map(), autoRoutes: {}, revision: 0, exportDepth: 0 });
  const graph = { clear() {}, get: key => key === 'group' ? { getMatrix: () => matrix, setMatrix(value) { matrix = Array.from(value); } } : null,
    getNodes: () => state.lastGraph.nodes.map(node => ({ getModel: () => ({ id: node.account, ...state.positions[node.account] }) })),
    paint() {}, getZoom: () => matrix[0] };
  state.graph = graph; state.graphSelection = selection; state.graphView = newView();
  state.graphCamera = { resize() {}, cancelFocus() { intent++; }, getIntent: () => intent,
    getOffset: () => ({ x: 0, y: 0 }), getTransform: () => ({ scale: 1, x: 0, y: 0 }) };
  let context, canvasOptions;
  const history = { replaceState() {}, status: () => ({ busy: false, canUndo: false, canRedo: false }) };
  const membership = { snapshot: () => ({ desiredIds: [], pendingIds: [], failures: {}, available: true }),
    subscribe(callback) { callback(); return () => {}; } };
  const sandbox = { state, document: doc, $, $$, URL, URLSearchParams, Event: class { constructor(type) { this.type = type; } },
    location: { href: 'http://synthetic.local/', origin: 'http://synthetic.local' }, history,
    window: { location: { href: 'http://synthetic.local/' }, history,
      addEventListener(type, callback) { const list = callbacks.get(type) || []; list.push(callback); callbacks.set(type, list); } },
    setTimeout(callback) { timers.push(callback); return timers.length; }, clearTimeout() {}, requestAnimationFrame: callback => callback(),
    preferenceStorage: { setItem() {} }, exportWorkspace: null, caseWorkspace: null, layoutSequence: 0, layoutJob: null,
    nodeDrafts: new Map(), retainedNodeInputs: new Map(), nodeWrites: new Map(), accountDataWrites: new Set(), pcState: {},
    captureNodeDraft() {}, nodeDraftDirty: draft => draft.idValue !== draft.idBase,
    resetNodeWorkspace() { sandbox.nodeDrafts.clear(); sandbox.retainedNodeInputs.clear(); },
    resetGraphView() { state.graphView = newView(); state.graphSelection = selection; selection.clear(); },
    cancelGraphLayout() {}, revokeZoomOptimization() {}, revokePhotoCrop() {}, closeDrawer() {}, resetBatchView() {},
    beginDisplayGesture() {}, finishDisplayGesture() {},
    updateHeader() {}, updateGraphViewStatus() {}, syncEdgeTypeControl() {}, rebuildGraphViewport() {}, scheduleGraphViewport() {}, scheduleGraphLabels() {},
    syncFlowTableScroll() {}, fmtCount: String, hasNodeDrafts: () => false, leaveNodeDrafts: async () => true,
    command() {}, syncControls() {}, membership,
    point: value => value && Number.isFinite(value.x) && Number.isFinite(value.y) ? { x: value.x, y: value.y } : null,
    order: (a, b) => a < b ? -1 : a > b ? 1 : 0,
    cameraOffset: () => ({ x: 0, y: 0 }), cameraTransform: value => value,
    renderGraph() { state.graphView.revision++; state.graphView.edgeItems = new Map(state.lastGraph.edges.map(edge => [edge.source + '=>' + edge.target, edge])); },
    renderFlows(rows) {
      boxes = rows.map(row => ({ checked: false, disabled: false, dataset: { id: String(row.id) }, setAttribute() {} }));
      tableRows = boxes.map(box => ({ dataset: box.dataset, classList: { toggle() {} }, querySelector: selector => selector === '.flow-ck' ? box : element('membership-' + box.dataset.id) }));
      context.syncFlowCkCount();
    }, syncFlowCkCount() {},
    toast(message) { notices.push(message); }, restoreEdgeLabelPositions() {}, fitGraph() { fitCalls++; },
    async refreshStats() { state.stats = { flows: (authorities.get(`${state.caseId}:${state.canvasId}`)?.tracked_ids || []).length }; },
    async loadGraph(_retry = true, binding = context.caseContext()) {
      requests.push({ path: `/api/graph?case_id=${binding.caseId}&canvas_id=${binding.canvasId}`, method: 'GET' });
      if (!context.isCurrentCase(binding)) return;
      state.lastGraph = plain(authorities.get(`${state.caseId}:${state.canvasId}`) || { nodes: [], edges: [], tracked_ids: [] });
      state.tracked = new Set(state.lastGraph.tracked_ids); state.graphLoading = false; sandbox.renderGraph();
    },
    async api(path, options = {}) {
      const method = options.method || 'GET'; requests.push({ path, method });
      assert.equal(method, 'GET', 'work restoration must never write the database');
      const url = new URL(path, 'http://synthetic.local/');
      if (url.pathname === '/api/canvases') return canvasLists.get(url.searchParams.get('case_id')) || [];
      if (url.pathname.startsWith('/api/canvases/')) return { snapshot: { nodes: [], edges: [], display: {} } };
      if (url.pathname === '/api/flows') {
        const rows = (authorities.get(`${state.caseId}:${state.canvasId}`)?.tracked_ids || []).map(id => ({ id }));
        return { rows, page: Number(url.searchParams.get('page') || 1), size: 50, total: rows.length };
      }
      throw new Error(`Unexpected service ${path}`);
    },
  };
  sandbox.caseRequest = sandbox.api;
  context = vm.createContext(sandbox);
  for (const name of ['money', 'graph-relations', 'graph-presentation', 'workspace-drafts']) vm.runInContext(source(name), context);
  sandbox.newGraphPresentation = value => sandbox.window.FundsGraphPresentation.create(value); state.presentation = sandbox.newGraphPresentation();
  vm.runInContext(section(app, 'function caseContext()', '/* ---------------- 初始化'), context);
  vm.runInContext(section(app, 'let pendingCanvasId =', 'async function loadCases('), context);
  vm.runInContext(section(app, 'function activateCanvas(', 'function graphMetric('), context);
  vm.runInContext(section(app, 'function searchParams(', 'async function doSearch('), context);
  sandbox.doSearch = async () => {};
  vm.runInContext(section(app, 'async function refreshCanvasList(', 'async function loadCanvas('), context);
  vm.runInContext(section(app, 'async function openManagedCase(', 'function installCaseWorkspace('), context);
  vm.runInContext(section(app, 'async function refreshAll(', '/* ---------------- 线标注'), context);
  vm.runInContext(section(editor, '  function capture()', '  function syncControls()'), context);
  const exportLine = editor.split('\n').find(line => line.includes('window.FundsCanvasEditor ='));
  vm.runInContext(exportLine, context);
  vm.runInContext(source('table-workspace'), context);
  vm.runInContext(source('workspace-session'), context);
  for (const callback of callbacks.get('DOMContentLoaded') || []) callback();
  sandbox.window.FundsCanvasWorkspace = {
    createView: () => ({ onState() {}, attach() {} }),
    create(options) { canvasOptions = options; return { status: () => ({}), sync() {}, revoke() {} }; },
  };
  return { state, context, element, requests, notices, authorities, canvasLists,
    session: sandbox.window.FundsWorkspaceSession, table: sandbox.window.FundsTableDrafts,
    box: id => boxes.find(box => box.dataset.id === String(id)), matrix: () => matrix,
    setMatrix(value) { matrix = value.slice(); }, fitCalls: () => fitCalls,
    drainTimers() { const pending = timers.splice(0); for (const callback of pending) callback(); },
    async openCase(id) { return context.openManagedCase(id, context.caseContext()); },
    switchCanvas(id) { context.activateCanvas(id); },
    async load() { await context.loadGraph(); },
    installNavigation() { vm.runInContext(source('app-navigation'), context); return sandbox.window.FundsAppNavigation; },
    installCanvases() { vm.runInContext(section(app, 'function bindCanvases()', '\nlet graphRequestSequence'), context); context.bindCanvases(); return canvasOptions; },
  };
}
const data = (ids = ['flow-a']) => ({ nodes: [{ account: 'node-a' }, { account: 'node-b' }],
  edges: [{ source: 'node-a', target: 'node-b', amount: '1.01' }], tracked_ids: ids });

test('actual A–B–A switch restores display/query/selection while rereading graph facts without writes', async () => {
  const h = harness(); h.authorities.set('1:0', data()); h.authorities.set('2:0', data(['flow-b'])); await h.load();
  h.state.positions = { 'node-a': { x: 150, y: 220 } }; h.state.graphSelection.rebind(['node-a']);
  h.element('fAccount').value = 'typed filter'; h.state.search.page = 3; h.setMatrix([2, 0, 0, 0, 2, 0, 91, 72, 1]);
  assert.equal(await h.openCase(2), true);
  assert.equal(h.element('fAccount').value, '', 'a new case starts with its own empty query inputs');
  h.authorities.set('1:0', data(['fresh-flow-a']));
  assert.equal(await h.openCase(1), true);
  assert.deepEqual(plain(h.state.positions), { 'node-a': { x: 150, y: 220 } });
  assert.equal(h.element('fAccount').value, 'typed filter'); assert.equal(h.state.search.page, 3);
  assert.deepEqual([...h.state.graphSelection.ids], ['node-a']);
  assert.deepEqual([...h.state.tracked], ['fresh-flow-a']); assert.deepEqual(h.matrix(), [2, 0, 0, 0, 2, 0, 91, 72, 1]);
  assert.ok(h.requests.every(request => request.method === 'GET'));
});

test('restored table anchors survive the follow-up authoritative query', async () => {
  const h = harness(); h.authorities.set('1:0', data()); await h.load();
  h.table.restore(['flow-a']);
  await h.context.doSearch(1);
  assert.equal(h.box('flow-a').checked, true);
  assert.deepEqual(plain(h.table.capture()), ['flow-a']);
});

test('inactive graph cannot overwrite its captured work during case-page switches', async () => {
  const h = harness(); h.authorities.set('1:0', data()); h.authorities.set('2:0', data(['flow-b'])); await h.load();
  h.state.positions = { 'node-a': { x: 81, y: 95 } }; h.session.capture();
  h.element('graphPage').inert = true;
  h.state.positions = { 'node-a': { x: -1, y: -1 } };
  await h.openCase(2); await h.openCase(1);
  assert.deepEqual(plain(h.state.positions), { 'node-a': { x: 81, y: 95 } });
});

test('session restore invalidates the formal snapshot delayed fit', async () => {
  const h = harness(); h.authorities.set('1:0', data()); await h.load();
  h.state.positions = { 'node-a': { x: 120, y: 30 } }; h.setMatrix([1.5, 0, 0, 0, 1.5, 0, 95, 105, 1]); h.session.capture();
  h.context.applyCanvasSnapshot({ nodes: [], edges: [], display: {} });
  assert.equal(h.session.restore(), true); h.drainTimers();
  assert.equal(h.fitCalls(), 0); assert.deepEqual(h.matrix(), [1.5, 0, 0, 0, 1.5, 0, 95, 105, 1]);
});

test('deleting a named canvas restores the retained default-canvas display draft', async () => {
  const h = harness(); h.authorities.set('1:0', data()); h.authorities.set('1:2', data());
  h.canvasLists.set('1', [{ id: 2, name: 'Canvas 2' }]); await h.load();
  h.state.positions = { 'node-a': { x: 331, y: 225 } };
  h.switchCanvas(2); await h.load();
  h.state.positions = { 'node-a': { x: -20, y: -20 } };
  const options = h.installCanvases(); h.canvasLists.set('1', []);
  await options.publish('delete', h.context.caseContext(), {}, null);
  assert.equal(h.state.canvasId, 0);
  assert.deepEqual(plain(h.state.positions), { 'node-a': { x: 331, y: 225 } });
  assert.equal(h.session.lastCanvas(1), '0');
});

test('returning home rereads current facts after a same-case import or deletion', async () => {
  const h = harness(); h.authorities.set('1:0', data()); await h.load();
  const navigation = h.installNavigation(); navigation.show('cases');
  h.authorities.set('1:0', data(['new-source-flow']));
  navigation.show('home'); await flush();
  assert.deepEqual([...h.state.tracked], ['new-source-flow']);
  assert.deepEqual(plain(h.table.capture()), []);
  assert.ok(h.requests.every(request => request.method === 'GET'));
});
