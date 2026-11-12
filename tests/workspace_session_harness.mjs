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
function deferred() {
  let resolve; const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

// Run the shipped switch/query/session adapters. DOM, G6 and in-memory services
// are simulated; no copy of the capture, restore or switching implementation.
function harness({ legacyRevision = false, accountOperations = false } = {}) {
  const elements = new Map(), callbacks = new Map(), requests = [], timers = [], notices = [], posts = [];
  let boxes = [], tableRows = [], matrix = [1, 0, 0, 0, 1, 0, 40, 60, 1], intent = 0, fitCalls = 0;
  let renderedRows = [], beforeRequest;
  const authorities = new Map(), flowAuthorities = new Map(), canvasLists = new Map(), accountFacts = new Map();
  const attributes = () => new Map();
  let created = 0;
  function element(id) {
    if (elements.has(id)) return elements.get(id);
    const attrs = attributes(), classes = new Set(), listeners = new Map();
    const item = { id, value: '', checked: false, indeterminate: false, disabled: false, hidden: false, inert: false,
      style: {}, dataset: {}, textContent: '', scrollLeft: 0, scrollTop: 0, children: [],
      classList: { contains: key => classes.has(key), add: key => classes.add(key), remove: key => classes.delete(key),
        toggle(key, on) { if (on) classes.add(key); else classes.delete(key); } },
      setAttribute(key, value) { attrs.set(key, String(value)); }, getAttribute: key => attrs.get(key) ?? null,
      removeAttribute(key) { attrs.delete(key); },
      contains: () => false, closest: () => null, focus() {},
      addEventListener(type, callback) { const list = listeners.get(type) || []; list.push(callback); listeners.set(type, list); },
      append(...nodes) { this.children.push(...nodes); }, appendChild(node) { this.children.push(node); return node; },
      dispatchEvent(event) {
        event.target ||= this; event.currentTarget = this; this['on' + event.type]?.(event);
        for (const callback of listeners.get(event.type) || []) callback(event);
      }, click() { this.onclick?.({ currentTarget: this }); },
      querySelector: selector => selector === '.flow-amount-value' ? null : ({ src: '', textContent: '', title: '', classList: { toggle() {} } }), querySelectorAll: () => [],
      get selectedOptions() { return [{ textContent: this.value === '0' ? '默认画布' : `Canvas ${this.value}` }]; },
    };
    Object.defineProperty(item, 'innerHTML', { set(value) { this.html = value; if (id === 'flowTbody' && !value) { boxes = []; tableRows = []; } }, get() { return this.html || ''; } });
    if (id === 'flowTableScroll') {
      item.clientHeight = 180;
      item.getBoundingClientRect = () => ({ top: 100, bottom: 280, height: 180 });
      item.querySelector = selector => selector === 'thead'
        ? { getBoundingClientRect: () => ({ top: 100, height: 34 }) } : null;
    }
    elements.set(id, item); return item;
  }
  element('tab-batches').style.display = 'none'; element('bulkDir').value = 'target'; element('bulkX').value = '5';
  element('flowTableDensity').value = 'compact'; element('flowTableWrap').value = 'wrap';
  const body = element('body'), navItems = ['home', 'cases', 'accounts'].map(page => ({ ...element('nav-' + page), dataset: { navPage: page }, removeAttribute() {} }));
  const frame = element('casesFrame');
  frame.contentWindow = { postMessage(message, target) { posts.push({ message: plain(message), target }); } };
  const doc = { activeElement: body, body,
    querySelector: selector => selector.startsWith('#') ? element(selector.slice(1)) : element(selector),
    querySelectorAll: selector => selector === '[data-nav-page]' ? navItems : [],
    createElement: () => element('created-' + created++), addEventListener() {},
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
  const membership = { snapshot: () => ({ confirmedIds: [...state.tracked], desiredIds: [...state.tracked], pendingIds: [], failures: {}, available: true }),
    subscribe(callback) { callback(); return () => {}; } };
  const sandbox = { state, document: doc, $, $$, URL, URLSearchParams, Event: class { constructor(type) { this.type = type; } },
    CustomEvent: class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } },
    location: { href: 'http://synthetic.local/', origin: 'http://synthetic.local' }, history,
    window: { location: { href: 'http://synthetic.local/' }, history, FormData,
      dispatchEvent(event) { for (const callback of callbacks.get(event.type) || []) callback(event); return true; },
      addEventListener(type, callback) { const list = callbacks.get(type) || []; list.push(callback); callbacks.set(type, list); } },
    setTimeout(callback) { timers.push(callback); return timers.length; }, clearTimeout() {}, requestAnimationFrame: callback => callback(),
    preferenceStorage: { setItem() {} }, exportWorkspace: null, caseWorkspace: null, layoutSequence: 0, layoutJob: null,
    nodeDrafts: new Map(), retainedNodeInputs: new Map(), nodeWrites: new Map(), accountDataWrites: new Set(), pcState: {},
    nodeConfirmation: null, nodeWorkspace: null, cancelAnnotationDrag: null, batchView: {},
    captureNodeDraft() {}, nodeDraftDirty: draft => draft.idValue !== draft.idBase,
    resetNodeWorkspace() { sandbox.nodeDrafts.clear(); sandbox.retainedNodeInputs.clear(); },
    resetGraphView() { state.graphView = newView(); state.graphSelection = selection; selection.clear(); },
    cancelGraphLayout() {}, revokeZoomOptimization() {}, revokePhotoCrop() {}, closeDrawer() {}, resetBatchView() {},
    beginDisplayGesture() {}, finishDisplayGesture() {}, invalidateResolution() {},
    updateHeader() {}, updateGraphViewStatus() {}, syncEdgeTypeControl() {}, rebuildGraphViewport() {}, scheduleGraphViewport() {}, scheduleGraphLabels() {},
    syncFlowTableScroll() {}, fmtCount: String, hasNodeDrafts: () => false, leaveNodeDrafts: async () => true,
    command() {}, syncControls() {}, membership,
    trackedWrites: new Set(), publishMembership() {}, graphMetric() {}, savePositions() {}, syncNodeWorkspaceControls() {},
    point: value => value && Number.isFinite(value.x) && Number.isFinite(value.y) ? { x: value.x, y: value.y } : null,
    order: (a, b) => a < b ? -1 : a > b ? 1 : 0,
    cameraOffset: () => ({ x: 0, y: 0 }), cameraTransform: value => value,
    renderGraph() { state.graphView.revision++; state.graphView.edgeItems = new Map(state.lastGraph.edges.map(edge => [edge.source + '=>' + edge.target, edge])); },
    renderFlows(rows) {
      renderedRows = plain(rows);
      boxes = rows.map(row => ({ checked: false, disabled: false, dataset: { id: String(row.id) }, setAttribute() {} }));
      tableRows = boxes.map((box, index) => ({ dataset: box.dataset, children: [], setAttribute() {}, removeAttribute() {}, classList: { toggle() {} },
        getBoundingClientRect: () => ({ top: 134 + index * 40 - element('flowTableScroll').scrollTop, bottom: 174 + index * 40 - element('flowTableScroll').scrollTop, height: 40 }),
        querySelector: selector => selector === '.flow-ck' ? box : element('membership-' + box.dataset.id) }));
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
      const method = options.method || 'GET', request = { path, method };
      requests.push(request);
      const url = new URL(path, 'http://synthetic.local/');
      await beforeRequest?.(request);
      if (method !== 'GET') {
        assert.ok(accountOperations && method === 'POST' && url.pathname === '/api/account-ids/set', 'only the synthetic account command may write');
        const body = JSON.parse(options.body), fact = accountFacts.get(`${body.case_id}:${body.account}`);
        assert.ok(fact, 'an identity write requires a known synthetic account');
        fact.id_no = body.id_no;
        return body.id_no ? { ok: true, account: body.account, id_no: body.id_no } : { ok: true, account: body.account, cleared: true };
      }
      if (url.pathname === '/api/account-registry/profile') {
        const fact = accountFacts.get(`${url.searchParams.get('case_id')}:${url.searchParams.get('account')}`);
        assert.ok(accountOperations && fact, 'profile reads require a known synthetic account');
        return plain(fact);
      }
      if (url.pathname === '/api/stats') return { flows: (authorities.get(`${url.searchParams.get('case_id')}:${state.canvasId}`)?.tracked_ids || []).length };
      if (url.pathname === '/api/graph') return plain(authorities.get(`${url.searchParams.get('case_id')}:${url.searchParams.get('canvas_id') || 0}`) || { nodes: [], edges: [], tracked_ids: [] });
      if (url.pathname === '/api/canvases') return canvasLists.get(url.searchParams.get('case_id')) || [];
      if (url.pathname.startsWith('/api/canvases/')) return { snapshot: { nodes: [], edges: [], display: {} } };
      if (url.pathname === '/api/flows') {
        let rows = flowAuthorities.get(`${state.caseId}:${state.canvasId}`)
          || (authorities.get(`${state.caseId}:${state.canvasId}`)?.tracked_ids || []).map(id => ({ id }));
        if (accountOperations) {
          const account = url.searchParams.get('account'), entity = url.searchParams.get('entity');
          rows = rows.filter(row => {
            const parties = [row.payer_account, row.payee_account];
            return (!account || parties.includes(account)) && (!entity || parties.some(value => value?.includes(entity)
              || accountFacts.get(`${url.searchParams.get('case_id')}:${value}`)?.id_no === entity));
          });
        }
        const page = Number(url.searchParams.get('page') || 1), size = Number(url.searchParams.get('size') || 50);
        return { rows: rows.slice((page - 1) * size, page * size), page, size, total: rows.length };
      }
      throw new Error(`Unexpected service ${path}`);
    },
  };
  sandbox.caseRequest = sandbox.api;
  context = vm.createContext(sandbox);
  for (const name of ['money', 'principal-analysis', 'graph-relations', 'graph-presentation', 'workspace-drafts']) vm.runInContext(source(name), context);
  vm.runInContext(app.split('\n').find(line => line.startsWith('function fmtTime(')), context);
  sandbox.window.FundsFacts = context.FundsFacts;
  sandbox.newGraphPresentation = value => sandbox.window.FundsGraphPresentation.create(value); state.presentation = sandbox.newGraphPresentation();
  vm.runInContext(section(app, 'function caseContext()', '/* ---------------- 初始化'), context);
  vm.runInContext(section(app, 'let pendingCanvasId =', 'async function loadCases('), context);
  vm.runInContext(section(app, 'function activateCanvas(', 'function graphMetric('), context);
  if (accountOperations) {
    vm.runInContext(source('graph-view'), context); sandbox.FundsGraphView = sandbox.window.FundsGraphView;
    vm.runInContext(app.split('\n').find(line => line.startsWith('function edgeKey(')), context);
    vm.runInContext(section(app, 'function applyGraphOverview()', '/* ---------------- 图谱'), context);
    vm.runInContext(section(app, 'let graphRequestSequence =', 'function nodeStyle('), context);
    vm.runInContext(section(app, 'async function applyPendingFocusEdge()', 'async function refreshStats('), context);
    vm.runInContext(section(app, 'async function refreshStats()', 'function updateHeader('), context);
    sandbox.loadBatches = async () => {};
  }
  vm.runInContext(section(app, 'function searchParams(', 'async function doSearch('), context);
  vm.runInContext(section(app, 'function flowObservationIds(', '/* ---------------- 追踪上图'), context);
  sandbox.doSearch = async () => {};
  vm.runInContext(section(app, 'async function refreshCanvasList(', 'async function loadCanvas('), context);
  vm.runInContext(section(app, 'async function openManagedCase(', 'function installCaseWorkspace('), context);
  vm.runInContext(section(app, 'async function refreshAll(', '/* ---------------- 线标注'), context);
  vm.runInContext(section(editor, '  function capture()', '  function syncControls()'), context);
  vm.runInContext(section(editor, '  function traceAnchors(', '  window.FundsCanvasEditor ='), context);
  const exportLine = editor.split('\n').find(line => line.includes('window.FundsCanvasEditor ='));
  vm.runInContext(exportLine, context);
  if (accountOperations) vm.runInContext(section(editor, '  const readGraph = loadGraph;', '  function reset()'), context);
  vm.runInContext(source('table-workspace'), context);
  if (legacyRevision) {
    const create=sandbox.window.FundsWorkspaceDrafts.create;
    sandbox.window.FundsWorkspaceDrafts={create(){
      const store=create();return {...store,get(...args){const value=store.get(...args);if(value)delete value.resolutionRevision;return value;}};
    }};
  }
  vm.runInContext(source('workspace-session'), context);
  for (const callback of callbacks.get('DOMContentLoaded') || []) callback();
  sandbox.window.FundsCanvasWorkspace = {
    createView: () => ({ onState() {}, attach() {} }),
    create(options) { canvasOptions = options; return { status: () => ({}), sync() {}, revoke() {} }; },
  };
  return { state, context, element, requests, notices, authorities, flowAuthorities, canvasLists, accountFacts, posts,
    session: sandbox.window.FundsWorkspaceSession, table: sandbox.window.FundsTableDrafts,
    box: id => boxes.find(box => box.dataset.id === String(id)), matrix: () => matrix,
    rows: () => plain(renderedRows),
    beforeRequest(callback) { beforeRequest = callback; },
    input(id, value) {
      const field = element(id); field.value = value;
      field.dispatchEvent({ type: 'input', target: field });
      element('queryFields').dispatchEvent({ type: 'input', target: field });
    },
    setMatrix(value) { matrix = value.slice(); }, fitCalls: () => fitCalls,
    drainTimers() { const pending = timers.splice(0); for (const callback of pending) callback(); },
    async openCase(id) { return context.openManagedCase(id, context.caseContext()); },
    switchCanvas(id) { context.activateCanvas(id); },
    async load() { await context.loadGraph(); },
    installNavigation() {
      if (accountOperations) vm.runInContext(source('account-workspace'), context);
      vm.runInContext(source('app-navigation'), context); return sandbox.window.FundsAppNavigation;
    },
    accountRequest(requestId, payload) { return { channel: 'fundstrace:workspace', version: 1, type: 'request', requestId,
      caseId: String(state.caseId), generation: state.caseGeneration, action: 'account-action', payload }; },
    async receive(message) {
      for (const callback of callbacks.get('message') || []) await callback({ origin: sandbox.location.origin, source: frame.contentWindow, data: message });
    },
    installCanvases() { vm.runInContext(section(app, 'function externalImportPending(', '\nlet graphRequestSequence'), context); context.bindCanvases(); return canvasOptions; },
  };
}
const data = (ids = ['flow-a']) => ({ nodes: [{ account: 'node-a' }, { account: 'node-b' }],
  edges: [{ source: 'node-a', target: 'node-b', amount: '1.01' }], tracked_ids: ids });

test('actual A–B–A switch restores display/query/selection while rereading graph facts without writes', async () => {
  const h = harness(); h.authorities.set('1:0', data()); h.authorities.set('2:0', data(['flow-b']));
  h.flowAuthorities.set('1:0', Array.from({ length: 150 }, (_, index) => ({ id: `SYN-ROW-${index + 1}` })));
  await h.load();
  h.state.positions = { 'node-a': { x: 150, y: 220 } }; h.state.graphSelection.rebind(['node-a']);
  h.element('fAccount').value = 'typed filter'; await h.context.doSearch(3);
  h.element('flowTableScroll').scrollTop = 12.25; h.element('flowTableScroll').scrollLeft = 421;
  h.setMatrix([2, 0, 0, 0, 2, 0, 91, 72, 1]);
  assert.equal(await h.openCase(2), true);
  assert.equal(h.element('fAccount').value, '', 'a new case starts with its own empty query inputs');
  h.authorities.set('1:0', data(['fresh-flow-a']));
  assert.equal(await h.openCase(1), true);
  assert.deepEqual(plain(h.state.positions), { 'node-a': { x: 150, y: 220 } });
  assert.equal(h.element('fAccount').value, 'typed filter'); assert.equal(h.state.search.page, 3);
  assert.deepEqual(plain(h.table.captureViewport()), { rowId: 'SYN-ROW-101', page: 3, offset: -12.25, scrollLeft: 421 });
  assert.equal(h.element('flowTableScroll').scrollTop, 12.25);
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

const oldIdentity = '110101199001010011', newIdentity = '110101199001010029';
function accountReturnHarness() {
  const h = harness({ accountOperations: true });
  h.flowAuthorities.set('1:0', [
    { id: 'SYN-FLOW-A', payer_account: 'SYN-A', payee_account: 'SYN-C', amount: '1.01' },
    { id: 'SYN-FLOW-B', payer_account: 'SYN-B', payee_account: 'SYN-C', amount: '2.02' },
  ]);
  h.authorities.set('1:0', { nodes: ['SYN-A', 'SYN-B', 'SYN-C'].map(account => ({ account })),
    edges: h.flowAuthorities.get('1:0').map(row => ({ source: row.payer_account, target: row.payee_account, amount: row.amount, ids: [row.id] })),
    tracked_ids: ['SYN-FLOW-A', 'SYN-FLOW-B'] });
  for (const account of ['SYN-A', 'SYN-B', 'SYN-C']) h.accountFacts.set('1:' + account,
    { case_id: '1', account, id_no: account === 'SYN-A' ? oldIdentity : '', photo: null, is_main: false, main_source: null });
  return h;
}
const flowQueries = (h, from = 0) => h.requests.slice(from).filter(request => request.path.startsWith('/api/flows?'))
  .map(request => new URL(request.path, 'http://synthetic.local/'));
const rowIds = h => h.rows().map(row => row.id);

test('account view-flows owns its target query while the same-case home refresh settles', async t => {
  const h = accountReturnHarness(); await h.load();
  h.input('fAccount', 'SYN-A'); await h.context.doSearch(1);
  assert.deepEqual(rowIds(h), ['SYN-FLOW-A']);
  const navigation = h.installNavigation(); navigation.show('accounts');
  const returning = deferred(), target = deferred();
  t.after(() => { returning.resolve(); target.resolve(); });
  h.beforeRequest(request => {
    const url = new URL(request.path, 'http://synthetic.local/');
    if (url.pathname === '/api/stats') return returning.promise;
    if (url.pathname === '/api/flows' && url.searchParams.get('account') === 'SYN-B') return target.promise;
  });
  const start = h.requests.length;
  const pending = h.receive(h.accountRequest('view-synthetic-b', { action: 'view-flows', account: 'SYN-B' }));
  await flush();
  // On the baseline B is already pending here. Releasing home stats/graph
  // lets its refresh of applied A finish before B's response is allowed back.
  returning.resolve(); await flush();
  assert.equal(flowQueries(h, start).filter(url => url.searchParams.get('account') === 'SYN-B').length, 1);
  target.resolve(); await pending; await flush();
  assert.equal(h.posts.find(item => item.message.requestId === 'view-synthetic-b')?.message.ok, true);
  const queries = flowQueries(h, start).map(url => url.search);
  assert.deepEqual(rowIds(h), ['SYN-FLOW-B'], `the requested account owns the final rows; queries: ${JSON.stringify(queries)}`);
  assert.equal(h.state.search.total, 1);
  assert.equal(h.element('fAccount').value, 'SYN-B');
  assert.equal(queries.length, 1, 'returning home must not add a second table-query owner');
  assert.equal(flowQueries(h, start)[0].searchParams.get('account_exact'), '1');
  assert.ok(h.requests.every(request => request.method === 'GET'));
});

for (const returnBeforeCommit of [false, true])
test('identity save refreshes the applied query with a separate unsubmitted draft when returning '
  + (returnBeforeCommit ? 'before commit' : 'after completion'), async t => {
  const h = accountReturnHarness(); await h.load();
  h.input('fAccount', oldIdentity); await h.context.doSearch(1);
  assert.deepEqual(rowIds(h), ['SYN-FLOW-A']);
  h.input('fAccount', 'SYN-UNSUBMITTED-DRAFT');
  const navigation = h.installNavigation(); navigation.show('accounts');
  const profile = deferred(); t.after(() => profile.resolve());
  if (returnBeforeCommit) h.beforeRequest(request => {
    if (request.path.startsWith('/api/account-registry/profile?')) return profile.promise;
  });
  const pending = h.receive(h.accountRequest('save-synthetic-identity', { action: 'mutate',
    mutation: { kind: 'identity-set', account: 'SYN-A', idNo: newIdentity } }));
  await flush();
  if (returnBeforeCommit) {
    assert.equal(h.accountFacts.get('1:SYN-A').id_no, oldIdentity, 'the pause precedes the identity commit');
    assert.equal(h.requests.filter(request => request.method === 'POST').length, 0);
    navigation.show('home'); await flush();
    assert.deepEqual(rowIds(h), ['SYN-FLOW-A'], 'the home read still sees the old identity before the commit');
    assert.equal(flowQueries(h).at(-1).searchParams.get('entity'), oldIdentity);
    profile.resolve();
  }
  await pending;
  if (!returnBeforeCommit) { navigation.show('home'); await flush(); }
  assert.equal(h.posts.find(item => item.message.requestId === 'save-synthetic-identity')?.message.ok, true);
  assert.equal(h.accountFacts.get('1:SYN-A').id_no, newIdentity);
  assert.equal(h.requests.filter(request => request.method === 'POST').length, 1);
  assert.equal(h.element('fAccount').value, 'SYN-UNSUBMITTED-DRAFT', 'authoritative refresh keeps the editable draft');
  assert.deepEqual(rowIds(h), [], 'the previously applied old identity no longer matches after the acknowledged commit');
  assert.equal(h.state.search.total, 0);
  const writeIndex = h.requests.findIndex(request => request.method === 'POST');
  const afterCommit = flowQueries(h, writeIndex + 1);
  assert.ok(afterCommit.length > 0, 'completion must reread table authority after the write');
  assert.ok(afterCommit.every(url => url.searchParams.get('entity') === oldIdentity), 'refresh owns the applied condition, not the unsubmitted draft');
  await h.context.doSearch(1, { refresh: true });
  assert.equal(flowQueries(h).at(-1).searchParams.get('entity'), oldIdentity);
  assert.deepEqual(rowIds(h), []);
});

test('identity publication preserves a newer pending first-page query and its separate draft', async t => {
  const h = accountReturnHarness(), original = h.flowAuthorities.get('1:0');
  const aRows = Array.from({ length: 51 }, (_, index) => ({ ...original[0], id: 'SYN-A-' + (index + 1) }));
  h.flowAuthorities.set('1:0', [...aRows, original[1]]);
  const graph = h.authorities.get('1:0'); graph.tracked_ids = [...aRows.map(row => row.id), original[1].id];
  graph.edges[0].ids = aRows.map(row => row.id); graph.edges[0].amount = '51.51';
  await h.load(); h.input('fAccount', oldIdentity); await h.context.doSearch(2);
  assert.deepEqual(rowIds(h), ['SYN-A-51']); assert.equal(h.state.search.page, 2);
  const navigation = h.installNavigation(); navigation.show('accounts');
  const profile = deferred(), bQuery = deferred();
  t.after(() => { profile.resolve(); bQuery.resolve(); });
  h.beforeRequest(request => {
    const url = new URL(request.path, 'http://synthetic.local/');
    if (url.pathname === '/api/account-registry/profile') return profile.promise;
    if (url.pathname === '/api/flows' && url.searchParams.get('entity') === 'SYN-B') return bQuery.promise;
  });
  const saving = h.receive(h.accountRequest('save-with-new-query', { action: 'mutate',
    mutation: { kind: 'identity-set', account: 'SYN-A', idNo: newIdentity } }));
  await flush(); assert.equal(h.requests.filter(request => request.method === 'POST').length, 0);
  navigation.show('home'); await flush();
  assert.deepEqual(rowIds(h), ['SYN-A-51']); assert.equal(h.state.search.page, 2);
  const queryStart = h.requests.length;
  h.input('fAccount', 'SYN-B'); const searching = h.element('btnSearch').onclick(); await flush();
  assert.equal(flowQueries(h, queryStart).length, 1, 'the user submitted B before the identity commit');
  h.input('fAccount', 'SYN-C');
  profile.resolve(); await flush();
  assert.equal(h.accountFacts.get('1:SYN-A').id_no, newIdentity);
  bQuery.resolve(); await Promise.all([saving, searching]); await flush();
  assert.equal(h.posts.find(item => item.message.requestId === 'save-with-new-query')?.message.ok, true);
  assert.deepEqual(rowIds(h), ['SYN-FLOW-B']); assert.equal(h.state.search.total, 1); assert.equal(h.state.search.page, 1);
  assert.equal(h.element('fAccount').value, 'SYN-C', 'publishing facts retains the unsubmitted C draft');
  assert.ok(flowQueries(h, queryStart).every(url => url.searchParams.get('entity') === 'SYN-B'
    && url.searchParams.get('page') === '1'), 'the new query owns both its parameters and first page');
  await h.context.doSearch(h.state.search.page, { refresh: true });
  assert.equal(flowQueries(h).at(-1).searchParams.get('entity'), 'SYN-B');
  assert.equal(flowQueries(h).at(-1).searchParams.get('page'), '1');
  assert.deepEqual(rowIds(h), ['SYN-FLOW-B']); assert.equal(h.element('fAccount').value, 'SYN-C');
});

test('a returning-home refresh cannot continue through a completed canvas switch', async t => {
  const h = accountReturnHarness(); await h.load(); await h.context.doSearch(1);
  const original = h.authorities.get('1:0');
  h.authorities.set('1:2', { nodes: original.nodes, edges: [original.edges[1]], tracked_ids: ['SYN-FLOW-B'] });
  h.flowAuthorities.set('1:2', h.flowAuthorities.get('1:0'));
  h.canvasLists.set('1', [{ id: 2, name: 'Synthetic named canvas' }]);
  h.installCanvases(); const navigation = h.installNavigation(); navigation.show('accounts');
  const stats = deferred(); t.after(() => stats.resolve());
  h.beforeRequest(request => { if (request.path.startsWith('/api/stats?')) return stats.promise; });
  const start = h.requests.length; navigation.show('home'); await flush();
  assert.equal(h.requests.slice(start).filter(request => request.path.startsWith('/api/stats?')).length, 1);
  const select = h.element('canvasSel'); select.value = '2'; await select.onchange({ target: select });
  assert.equal(h.state.canvasId, 2); assert.deepEqual([...h.state.tracked], ['SYN-FLOW-B']);
  assert.deepEqual(rowIds(h), ['SYN-FLOW-A', 'SYN-FLOW-B']); assert.equal(h.state.search.page, 1);
  const graphReads = () => h.requests.slice(start).filter(request => request.path.startsWith('/api/graph?'));
  assert.equal(graphReads().length, 1); assert.equal(flowQueries(h, start).length, 1);
  assert.equal(new URL(graphReads()[0].path, 'http://synthetic.local/').searchParams.get('canvas_id'), '2');
  assert.deepEqual(h.notices, [], 'the public canvas selector completes without an error');
  stats.resolve(); await flush();
  assert.equal(graphReads().length, 1, 'the old home refresh cannot reread the new canvas binding');
  assert.equal(flowQueries(h, start).length, 1, 'the old home refresh cannot consume another table refresh');
  assert.deepEqual([...h.state.tracked], ['SYN-FLOW-B']); assert.deepEqual(rowIds(h), ['SYN-FLOW-A', 'SYN-FLOW-B']);
  assert.ok(h.requests.every(request => request.method === 'GET'));
});


for(const revision of [null,'a'.repeat(64)])
test('same explicit resolution revision preserves a restored neighborhood: '+revision,async()=>{
  const h=harness(),graph={...data(),resolution_revision:revision};h.authorities.set('1:0',graph);await h.load();
  h.state.graphView.visibleIds=new Set(['node-a','node-b']);h.state.graphView.root='node-a';h.state.graphView.direction='in';
  h.state.graphSelection.rebind(['node-a']);h.setMatrix([2,0,0,0,2,0,91,72,1]);h.session.capture();
  h.state.graphView.visibleIds=null;h.state.graphView.root=null;h.setMatrix([1,0,0,0,1,0,4,5,1]);
  assert.equal(h.session.restore(),true);
  assert.deepEqual([...h.state.graphView.visibleIds],['node-a','node-b']);assert.equal(h.state.graphView.root,'node-a');
  assert.equal(h.state.graphView.direction,'in');assert.deepEqual([...h.state.graphSelection.ids],['node-a']);
  assert.deepEqual(h.matrix(),[2,0,0,0,2,0,91,72,1]);assert.ok(h.requests.every(request=>request.method==='GET'));
});
for(const kind of ['changed','unknown'])
test('a stale or unknown session neighborhood restores the complete fresh graph without guessing identity: '+kind,async()=>{
  const h=harness({legacyRevision:kind==='unknown'});
  h.authorities.set('1:0',{...data(),resolution_revision:'a'.repeat(64)});await h.load();
  h.state.graphView.visibleIds=new Set(['node-a','node-b']);h.state.graphView.root='node-a';
  h.state.graphSelection.rebind(['node-a']);h.setMatrix([2,0,0,0,2,0,91,72,1]);h.session.capture();
  const fresh={nodes:[{account:'node-c'},{account:'node-b'},{account:'unrelated-current'}],
    edges:[{source:'node-c',target:'node-b',amount:'1.01'}],tracked_ids:['flow-a'],resolution_revision:kind==='changed'?'b'.repeat(64):'a'.repeat(64)};
  h.authorities.set('1:0',fresh);await h.load();h.setMatrix([1,0,0,0,1,0,4,5,1]);
  assert.equal(h.session.restore(),true);
  assert.equal(h.state.graphView.visibleIds,null);assert.equal(h.state.graphView.root,null);
  assert.deepEqual([...h.state.graphSelection.ids],[]);assert.deepEqual(plain(h.state.lastGraph),fresh);
  assert.deepEqual(h.matrix(),[1,0,0,0,1,0,4,5,1],'stale partial-view camera is not reapplied');
  assert.ok(h.requests.every(request=>request.method==='GET'));
});

for(const revision of [null,'a'.repeat(64)])
test('same explicit revision preserves identity-only session filters and camera: '+revision,async()=>{
  const h=harness(),graph={...data(),resolution_revision:revision};h.authorities.set('1:0',graph);await h.load();
  h.state.graphView.visibleIds=null;h.state.graphView.root=null;
  h.state.presentation.onlyAccounts=['node-a','node-b'];h.state.presentation.hiddenAccounts=['node-b'];
  h.state.graphSelection.rebind(['node-a']);h.setMatrix([2,0,0,0,2,0,91,72,1]);h.session.capture();
  h.state.presentation=h.context.newGraphPresentation();h.setMatrix([1,0,0,0,1,0,4,5,1]);
  assert.equal(h.session.restore(),true);assert.equal(h.state.graphView.visibleIds,null);
  assert.deepEqual(plain(h.state.presentation.onlyAccounts),['node-a','node-b']);assert.deepEqual(plain(h.state.presentation.hiddenAccounts),['node-b']);
  assert.deepEqual(h.matrix(),[2,0,0,0,2,0,91,72,1]);assert.deepEqual([...h.state.graphSelection.ids],['node-a']);
  assert.ok(h.requests.every(request=>request.method==='GET'));
});
for(const kind of ['changed','unknown']) for(const mode of ['only','hidden'])
test('stale identity-only session filters restore the complete graph without old fact storage: '+kind+' '+mode,async()=>{
  const h=harness({legacyRevision:kind==='unknown'});h.authorities.set('1:0',{...data(),resolution_revision:'a'.repeat(64)});await h.load();
  h.state.graphView.visibleIds=null;h.state.graphView.root=null;
  h.state.presentation.onlyAccounts=mode==='only'?['node-a','node-b']:null;
  h.state.presentation.hiddenAccounts=mode==='hidden'?['node-a']:[];
  h.state.graphSelection.rebind(['node-a']);h.setMatrix([2,0,0,0,2,0,91,72,1]);h.session.capture();
  const fresh={nodes:[{account:'node-c'},{account:'node-b'},{account:'unrelated-current'}],
    edges:[{source:'node-c',target:'node-b',amount:'1.01'}],tracked_ids:['flow-a'],resolution_revision:kind==='changed'?'b'.repeat(64):'a'.repeat(64)};
  h.authorities.set('1:0',fresh);await h.load();h.setMatrix([1,0,0,0,1,0,4,5,1]);
  assert.equal(h.session.restore(),true);assert.equal(h.state.graphView.visibleIds,null);assert.equal(h.state.graphView.root,null);
  assert.equal(h.state.presentation.onlyAccounts,null);assert.deepEqual(plain(h.state.presentation.hiddenAccounts),[]);
  assert.deepEqual([...h.state.graphSelection.ids],[]);assert.deepEqual(plain(h.state.lastGraph),fresh);
  assert.deepEqual(h.matrix(),[1,0,0,0,1,0,4,5,1]);assert.ok(h.requests.every(request=>request.method==='GET'));
});
