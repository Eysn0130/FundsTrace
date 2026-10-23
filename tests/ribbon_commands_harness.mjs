import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const root = fileURLToPath(new URL('../', import.meta.url));
const source = name => readFileSync(`${root}static/${name}.js`, 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
const delay = () => new Promise(resolve => setTimeout(resolve, 2));
const relation = (source, target, amount, currency = 'CNY') => ({ source, target, cnt: 1,
  ids: [`SYN-${source}-${target}`], currencies: [currency], amount_totals: [{ currency, amount }] });
const fixture = () => ({ nodes: ['A', 'B', 'C', 'D'].map(account => ({ account, name: `Synthetic ${account}`,
  in_cnt: 1, out_cnt: 1, is_main: account === 'A' ? 1 : 0 })), edges: [
  relation('A', 'B', '9007199254740993.01'), relation('B', 'A', '9007199254740993.02'),
  relation('B', 'C', '0.10'), relation('A', 'C', '0.01'), relation('C', 'D', '1.00', 'USD'),
], tracked_ids: ['9007199254740993', '9007199254740994'] });
const positions = () => ({ A: { x: 10, y: 40 }, B: { x: 30, y: 10 }, C: { x: 100, y: 100 }, D: { x: 180, y: 160 } });

function harness({ relationController = false, workspaceShell = false } = {}) {
  const elements = new Map(), events = new Map(), documentEvents = new Map(), requests = [], messages = [], enabled = new Map();
  const ribbonValues = {}, ribbonStatus = {}, drawerCalls = [], filterAdapter = { options: null, updates: 0 };
  let visibleModals = [];
  let network = null;
  const register = (map, type, callback) => { if (!map.has(type)) map.set(type, new Set()); map.get(type).add(callback); };
  const dispatch = (map, event) => { for (const callback of [...map.get(event.type) || []]) callback(event); };
  function element(id, kind = 'div') {
    if (elements.has(id)) return elements.get(id);
    const classes = new Set(), listeners = new Map(), attributes = new Map();
    const node = { id, kind, value: '', checked: false, disabled: false, hidden: false,
      style: { setProperty(name, value) { this[name] = String(value); } }, dataset: {}, children: [],
      innerHTML: '', textContent: '', clientWidth: 900, clientHeight: 600,
      showModal() { this.open = true; }, close() { this.open = false; },
      classList: { add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name),
        toggle(name, force) { const on = force === undefined ? !classes.has(name) : force; on ? classes.add(name) : classes.delete(name); } },
      get options() { return this.children.filter(child => child.kind === 'option'); },
      appendChild(child) { this.children.push(child); if (child.kind === 'option' && this.options.length === 1) this.value = child.value; return child; },
      replaceChildren(...children) { this.children = [...children]; this.value = ''; },
      addEventListener: (type, callback) => register(listeners, type, callback),
      removeEventListener: (type, callback) => listeners.get(type)?.delete(callback),
      dispatchEvent: event => dispatch(listeners, event),
      setAttribute: (name, value) => attributes.set(name, String(value)),
      getAttribute: name => attributes.get(name) ?? null, hasAttribute: name => attributes.has(name),
      removeAttribute: name => attributes.delete(name), remove() {}, focus() { document.activeElement = this; }, scrollIntoView() {},
      contains(candidate) { return candidate === this || this.children.some(child => child.contains?.(candidate)); },
      closest: () => null,
      getBoundingClientRect() { return { left: 0, top: 0, right: this.clientWidth, bottom: this.clientHeight,
        width: this.clientWidth, height: this.clientHeight }; },
      getClientRects: () => [], querySelector: () => null, querySelectorAll: () => [], click() { this.dispatchEvent({ type: 'click' }); },
    };
    elements.set(id, node); return node;
  }
  const document = { readyState: 'loading', querySelector: selector => element(selector),
    querySelectorAll: selector => selector === '.modal,dialog,[role="dialog"]' ? visibleModals : [], getElementById: id => element('#' + id), createElement: kind => element(Symbol(kind), kind),
    addEventListener: (type, callback) => register(documentEvents, type, callback),
    dispatchEvent: event => dispatch(documentEvents, event), body: element('body') };
  const location = new URL('http://synthetic.local/?case_id=1');
  const window = { location, history: { replaceState(_state, _title, href) { location.href = new URL(href, location).href; } },
    addEventListener: (type, callback) => register(events, type, callback),
    removeEventListener: (type, callback) => events.get(type)?.delete(callback),
    dispatchEvent: event => dispatch(events, event) };
  const storage = new Map();
  const context = vm.createContext({ window, document, URL, URLSearchParams, console, setTimeout, clearTimeout,
    performance: { now: () => 0 }, CustomEvent: class { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, String(value)),
      removeItem: key => storage.delete(key) },
    requestAnimationFrame: () => 1, cancelAnimationFrame() {},
    fetch: async (path, options) => { requests.push({ path, options });
      if (network) return network(path, options || {});
      throw new Error('Unexpected network request'); } });
  const run = code => vm.runInContext(code, context);
  for (const name of ['money', 'graph-layout', 'graph-view', 'graph-relations', 'graph-presentation', 'graph-label-geometry', 'workspace-camera']) {
    run(source(name));
    for (const key of Object.keys(window)) if (key.startsWith('FundsGraph')) context[key] = window[key];
  }
  run(source('app'));
  const state = run('state');
  window.FundsGraphLayout = context.FundsGraphLayout;
  window.matchMedia = () => ({ matches: true });
  let matrix = [1.5, 0, 0, 0, 1.5, 0, 71, 93, 1], nodeItems = [], edgeItems = [], pending = null;
  const group = { getMatrix: () => matrix, setMatrix: next => { matrix = [...next]; } };
  const item = model => ({ getModel: () => model, getContainer: () => ({ get: () => [] }),
    getKeyShape: () => ({ attr: () => null }) });
  const graph = { getNodes: () => nodeItems, getEdges: () => edgeItems,
    get: key => key === 'group' ? group : ({ width: 900, height: 600, minZoom: 0.02, maxZoom: 4 })[key] ?? null,
    data: data => { pending = data; }, render: () => graph.changeData(pending),
    changeData(data) { nodeItems = data.nodes.map(item); edgeItems = data.edges.map(item); },
    findById: id => nodeItems.find(node => node.getModel().id === id),
    updateItem(target, patch) { Object.assign(target.getModel(), patch); },
    clear() { nodeItems = []; edgeItems = []; }, setAutoPaint() {}, stopLayout() {}, paint() {}, setItemState() {}, changeSize() {},
    getZoom: () => matrix[0], zoomTo: zoom => { matrix[0] = matrix[4] = zoom; },
    translate: (x, y) => { matrix[6] += x; matrix[7] += y; },
    getPointByCanvas: (x, y) => ({ x: (x - matrix[6]) / matrix[0], y: (y - matrix[7]) / matrix[4] }),
    getCanvasByPoint: (x, y) => ({ x: x * matrix[0] + matrix[6], y: y * matrix[4] + matrix[7] }) };
  context.graphStub = graph;
  context.drawerCalls = drawerCalls;
  run(`
    toast = (message,type) => globalThis.messages.push({message,type});
    scheduleGraphViewport = () => {}; scheduleGraphLabels = () => {};
    applyGraphLabels = () => {};
    rebuildGraphViewport = () => {
      state.graphView.nodeItems = new Map(state.graph.getNodes().map(item => [item.getModel().id,item]));
      state.graphView.edgeItems = new Map(state.graph.getEdges().map(item => [edgeKey(item.getModel()),item]));
    };
    initGraphSelection = () => {
      const selection = { ids: new Set(),
        rebind(ids) { this.ids = new Set([...ids].filter(id=>state.graph.findById(id))); },
        select(id) { this.rebind([id]); }, clear() { this.ids.clear(); }, refresh() {}, destroy() { this.ids.clear(); } };
      state.graphSelection = selection;
    };
    showNodeDrawer = async (id,item) => {
      globalThis.drawerCalls.push({kind:'node',id,item});selectItem(item);$('#drawer').classList.add('open');
      if(globalThis.drawerWait)await globalThis.drawerWait;
    };
    showEdgeDrawer = async (_model,item) => {
      globalThis.drawerCalls.push({kind:'edge',id:edgeKey(_model),item});selectItem(item);$('#drawer').classList.add('open');
      if(globalThis.drawerWait)await globalThis.drawerWait;
    };
    initGraph = () => { state.graph = globalThis.graphStub; initGraphSelection(); };
  `);
  context.messages = messages;
  state.caseId = 1; state.graph = graph; state.graphLoading = false; state.forceFit = false;
  window.FundsRibbon = { setStatus(values) { Object.assign(ribbonStatus, values); }, closePopovers() {},
    setCommandEnabled: (command, value) => enabled.set(command, value),
    setValues(values) { for (const [id, value] of Object.entries(values)) {
      ribbonValues[id] = clone(value);
      const node = element('#' + id); if (typeof value === 'boolean') node.checked = value; else node.value = String(value);
    } } };
  function install(data = fixture(), geometry = positions()) {
    state.lastGraph = data; state.tracked = new Set(data.tracked_ids); state.positions = clone(geometry);
    state.graphLoading = false; state.graphInited = false; state.forceFit = false; state.forceRebuild = false;
    run('initGraphSelection(); renderGraph();');
  }
  install();
  state.graphCamera = window.FundsWorkspaceCamera.create({ graph, container: element('#graphContainer'),
    isCurrent: () => state.graph === graph });
  run(source('canvas-history')); run(source('canvas-editor'));
  const editor = window.FundsCanvasEditor;
  if (relationController) window.FundsRelationFilter = { create(options) {
    filterAdapter.options = options;
    return { update() { filterAdapter.updates++; }, apply: () => false };
  } };
  run(source('ribbon-commands'));
  // Mount the ribbon only: app startup/network and unrelated DOM bindings are outside this harness.
  [...events.get('DOMContentLoaded')].at(-1)();
  const commands = window.FundsRibbonCommands;
  const shellCalls = [];
  let shell = null;
  if (workspaceShell) {
    // Exercise the production shell instead of reproducing its view-state rules here.
    Object.assign(window, { document, localStorage: context.localStorage, CustomEvent: context.CustomEvent,
      setTimeout, clearTimeout });
    run(source('workspace-shell'));
    shell = window.FundsWorkspaceShell.mount(document);
    for (const method of ['toggleFocus', 'setSidebarCollapsed', 'setToolbarCollapsed']) {
      const original = shell[method];
      shell[method] = (...args) => {
        shellCalls.push({ method, view: shell.getState().view, args });
        return original(...args);
      };
    }
  }
  async function settle() { for (let i = 0; i < 50 && editor.history.status().busy; i++) await delay(); await delay(); }
  return { run, state, element, requests, messages, commands, editor, enabled, install, ribbonValues, ribbonStatus, drawerCalls,
    shell, shellCalls,
    relationOptions: filterAdapter.options,
    network(callback) { network = callback; },
    drawerWait(promise) { context.drawerWait = promise; },
    modals(value) { visibleModals = value; },
    focus(node = element('#graphContainer')) { document.activeElement = node; },
    fireWindow(type, values = {}) { window.dispatchEvent({ type, ...values }); },
    fireDocument(type, values = {}) { document.dispatchEvent({ type, ...values }); },
    key(values = {}) {
      const event = { type: 'keydown', key: 'Enter', target: element('#graphContainer'), defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; }, ...values };
      element('#graphContainer').dispatchEvent(event); return event;
    },
    get camera() { return [...matrix]; }, get nodes() { return nodeItems; }, get edges() { return edgeItems; },
    select(ids) { state.graphSelection.rebind(ids); commands.sync(); },
    selectEdge(key) { const edge = state.graphView.edgeItems.get(key); assert.ok(edge, key); run('globalThis.chosenEdge = null');
      context.chosenEdge = edge; run("selectItem(globalThis.chosenEdge); $('#drawer').classList.add('open');"); },
    async event(command, value) { document.dispatchEvent({ type: 'funds:ribbon-command', detail: { command, value } }); await settle(); },
    settle };
}

for (const [command, expectedZoom] of [['zoomIn', 1.8], ['zoomOut', 1.25], ['zoomReset', 1]]) {
  test(`${command} cancels sidebar composition before changing the camera and preserves graph facts`, async () => {
    const f = harness(), order = [], before = JSON.stringify(f.state.lastGraph), geometry = JSON.stringify(f.state.positions);
    const graph = f.state.graph, zoom = graph.zoomTo;
    f.state.graphCamera = { cancelComposition() { order.push('cancel'); } };
    graph.zoomTo = (...args) => { order.push('zoom'); return zoom(...args); };
    const history = JSON.stringify(f.editor.history.status());

    assert.equal(await f.commands.execute(command), true);

    assert.deepEqual(order, ['cancel', 'zoom']); assert.ok(Math.abs(f.camera[0] - expectedZoom) < 1e-10);
    assert.equal(JSON.stringify(f.state.lastGraph), before); assert.equal(JSON.stringify(f.state.positions), geometry);
    assert.equal(JSON.stringify(f.editor.history.status()), history); assert.equal(f.requests.length, 0);
  });
}

for (const [command, selection, query, targets] of [
  ['fitSelection', ['B', 'C'], undefined, ['B', 'C']],
  ['focusSelection', ['A', 'D'], undefined, ['A', 'D']],
]) {
  test(`${command} uses the shared camera focus without changing source facts or history`, async () => {
    const f = harness(), order = []; f.select(selection);
    const graph = f.state.graph, camera = f.state.graphCamera, focus = camera.focus;
    camera.focus = (...args) => { order.push('focus'); return focus(...args); };
    graph.zoomTo = () => { throw new Error('Focus must not bypass the camera with direct zoom'); };
    graph.translate = () => { throw new Error('Focus must not bypass the camera with direct pan'); };
    const before = JSON.stringify(f.state.lastGraph), geometry = JSON.stringify(f.state.positions), history = JSON.stringify(f.editor.history.status());

    assert.equal(await f.commands.execute(command, query), true);

    assert.deepEqual(order, ['focus']);
    assert.equal(f.camera[0],2,'the complete target envelope fits at the explicitly requested reading scale');
    assert.equal(f.camera[4],2,'focus preserves the same scale on both axes');
    for (const id of targets) {
      const model = graph.findById(id).getModel(), screen = graph.getCanvasByPoint(model.x, model.y);
      assert.ok(screen.x >= 50 && screen.x <= 850, `${id} must be within the focused viewport horizontally`);
      assert.ok(screen.y >= 60 && screen.y <= 540, `${id} must be within the focused viewport vertically`);
    }
    assert.deepEqual([...f.state.graphSelection.ids], selection);
    assert.equal(JSON.stringify(f.state.lastGraph), before); assert.equal(JSON.stringify(f.state.positions), geometry);
    assert.equal(JSON.stringify(f.editor.history.status()), history); assert.equal(f.requests.length, 0);
  });
}

for (const previousView of ['standard', 'split']) {
  test(`focusCanvas delegates to the shell and restores the previous ${previousView} view without editing the graph`, async () => {
    const f = harness({ workspaceShell: true });
    f.shell.setView(previousView);
    f.select(['B', 'D']);
    f.state.edgeBendPos = { 'A=>B': [{ x: 20, y: 25 }] };
    f.state.edgeLabelPos['A=>B'] = 0.35;
    f.state.edgeSnapshotLabels = { 'A=>B': { lx: 15, ly: 20 } };
    const snapshot = () => JSON.stringify({ facts: f.state.lastGraph, tracked: [...f.state.tracked],
      positions: f.state.positions, bends: f.state.edgeBendPos, labelRatios: f.state.edgeLabelPos,
      labelPositions: f.state.edgeSnapshotLabels, presentation: f.state.presentation,
      selection: [...f.state.graphSelection.ids], nodes: f.nodes.map(item => item.getModel()),
      edges: f.edges.map(item => item.getModel()), camera: f.camera, history: f.editor.history.status() });
    const before = snapshot();
    f.shellCalls.length = 0;

    assert.equal(await f.commands.execute('focusCanvas'), true);
    assert.equal(f.shell.getState().view, 'focus');
    assert.deepEqual(f.shellCalls, [{ method: 'toggleFocus', view: previousView, args: [] }],
      'the ribbon uses the shell mode API rather than managing two collapse booleans');
    assert.equal(snapshot(), before);

    await f.event('focusCanvas');
    assert.equal(f.shell.getState().view, previousView);
    assert.deepEqual(f.shellCalls, [
      { method: 'toggleFocus', view: previousView, args: [] },
      { method: 'toggleFocus', view: 'focus', args: [] },
    ]);
    assert.equal(snapshot(), before);
    assert.equal(f.requests.length, 0);
  });
}

test('focusCanvas remains safe when no workspace shell is mounted', async () => {
  const f = harness(); f.select(['B']);
  const snapshot = () => JSON.stringify({ facts: f.state.lastGraph, positions: f.state.positions,
    selection: [...f.state.graphSelection.ids], camera: f.camera, history: f.editor.history.status() });
  const before = snapshot();
  assert.equal(f.element('#workspace').fundsWorkspaceShell, undefined);

  await assert.doesNotReject(() => f.commands.execute('focusCanvas'));
  await f.event('focusCanvas');

  assert.equal(snapshot(), before); assert.equal(f.requests.length, 0);
  assert.deepEqual(f.messages, []);
});

test('rejected focus and zoom commands do not interrupt the sidebar camera', async () => {
  const f = harness(), order = [], camera = f.camera;
  f.state.graphCamera = { cancelComposition() { order.push('cancel'); } };
  assert.equal(await f.commands.execute('fitSelection'), false);
  f.state.graphLoading = true;
  assert.equal(await f.commands.execute('zoomIn'), false);
  assert.deepEqual(order, []); assert.deepEqual(f.camera, camera);
});

test('graph find forwards the complete query to its controller without mutating graph state at the ribbon boundary',async()=>{
  const f=harness();f.select(['D']);
  f.run(`globalThis.findQueries=[];window.FundsNodeFinderActions={search:async query=>{findQueries.push(query);return query!== '不存在';}};`);
  const before=JSON.stringify({facts:f.state.lastGraph,geometry:f.state.positions,selection:[...f.state.graphSelection.ids],
    camera:f.camera,history:f.editor.history.status()});
  assert.equal(await f.commands.execute('findNode','共同户名'),true);
  assert.equal(await f.commands.execute('findNode','0000000000000000123'),true);
  assert.equal(await f.commands.execute('findNode','不存在'),false,'the routing boundary returns the controller outcome');
  assert.deepEqual(clone(f.run('findQueries')),['共同户名','0000000000000000123','不存在']);
  assert.equal(JSON.stringify({facts:f.state.lastGraph,geometry:f.state.positions,selection:[...f.state.graphSelection.ids],
    camera:f.camera,history:f.editor.history.status()}),before);
  assert.equal(f.requests.length,0);assert.equal(f.drawerCalls.length,0);
});

test('graph find delegates blocked and empty-input presentation to its controller rather than the selection mutation guard',async()=>{
  const f=harness();f.select(['A']);f.state.graphLoading=true;
  f.run(`hasNodeDrafts=()=>true;globalThis.findQueries=[];window.FundsNodeFinderActions={search:query=>{findQueries.push(query);return false;}};`);
  const camera=f.camera,history=JSON.stringify(f.editor.history.status());
  assert.equal(await f.commands.execute('findNode'),false);
  assert.equal(f.run('findQueries.length'),1);assert.equal(f.run('findQueries[0]'),undefined);
  assert.deepEqual([...f.state.graphSelection.ids],['A']);assert.deepEqual(f.camera,camera);
  assert.equal(JSON.stringify(f.editor.history.status()),history);assert.equal(f.requests.length,0);
  f.run('window.FundsNodeFinderActions=undefined');
  assert.equal(await f.commands.execute('findNode','Synthetic A'),false,'an unavailable controller does not run the former selection-mutating fallback');
  assert.deepEqual([...f.state.graphSelection.ids],['A']);assert.deepEqual(f.camera,camera);
});

test('amount filter uses exact directed edges; invalid draft preserves view and history', async () => {
  const f = harness(), membership = [...f.state.tracked], sourceBefore = JSON.stringify(f.state.lastGraph);
  f.element('#relationCurrency').value = 'currency:CNY';
  f.element('#relationMin').value = '9007199254740993.02'; f.element('#relationMax').value = '9007199254740993.02';
  assert.equal(await f.commands.applyFilter(), true);
  assert.deepEqual(Array.from(f.edges, item => `${item.getModel().source}=>${item.getModel().target}`), ['B=>A']);
  assert.equal(f.editor.history.status().count, 1);
  const stateBefore = JSON.stringify(f.state.presentation), camera = f.camera;
  f.element('#relationMin').value = '9007199254740993.03';
  assert.equal(await f.commands.applyFilter(), false);
  assert.equal(JSON.stringify(f.state.presentation), stateBefore); assert.equal(f.editor.history.status().count, 1);
  assert.deepEqual(Array.from(f.edges, item => `${item.getModel().source}=>${item.getModel().target}`), ['B=>A']);
  assert.match(f.element('#relationFilterStatus').textContent, /最低金额/);
  assert.deepEqual([...f.state.tracked], membership); assert.equal(JSON.stringify(f.state.lastGraph), sourceBefore);
  assert.deepEqual(f.camera, camera); assert.equal(f.requests.length, 0);
});

test('relation controller adapter preview composes neighborhood, hidden and only scopes exactly as commit renders', async () => {
  const f = harness({ relationController: true }), data = fixture();
  data.nodes.push(...['E', 'F'].map(account => ({ account, name: `Synthetic ${account}`, in_cnt: 1, out_cnt: 1 })));
  data.edges.push(relation('D', 'E', '0.01'), relation('E', 'F', '0.02'));
  f.install(data, { ...positions(), E: { x: 240, y: 170 }, F: { x: 310, y: 190 } });
  f.state.graphView.visibleIds = new Set(['A', 'B', 'C', 'D', 'E']);
  f.state.presentation.hiddenAccounts = ['A'];
  f.state.presentation.onlyAccounts = ['A', 'C', 'D', 'E', 'F'];
  f.run('renderGraph()'); f.commands.sync();
  const ports = f.relationOptions, filter = { currency: 'CNY', min: '0.00', max: '1.00', active: true };
  const snapshot = ports.read(), camera = f.camera, raw = JSON.stringify(f.state.lastGraph), tracked = [...f.state.tracked];

  assert.deepEqual(clone(ports.preview(filter, snapshot)), { nodes: 3, edges: 1 });
  assert.equal(await ports.commit(filter, snapshot.binding), true);

  assert.deepEqual(Array.from(f.nodes, item => item.getModel().id), ['C', 'D', 'E']);
  assert.deepEqual(Array.from(f.edges, item => `${item.getModel().source}=>${item.getModel().target}`), ['D=>E']);
  assert.deepEqual(clone(ports.preview(filter, ports.read())), { nodes: f.nodes.length, edges: f.edges.length });
  assert.deepEqual([...f.state.graphView.visibleIds], ['A', 'B', 'C', 'D', 'E']);
  assert.deepEqual(clone(f.state.presentation.hiddenAccounts), ['A']);
  assert.deepEqual(clone(f.state.presentation.onlyAccounts), ['A', 'C', 'D', 'E', 'F']);
  assert.equal(f.editor.history.status().count, 1); assert.deepEqual(f.camera, camera);
  assert.equal(JSON.stringify(f.state.lastGraph), raw); assert.deepEqual([...f.state.tracked], tracked); assert.equal(f.requests.length, 0);
});

test('relation controller commit retains exact money, camera, source and unrelated display settings in one history entry', async () => {
  const f = harness({ relationController: true });
  f.state.presentation.nodeLabels = { A: { fontSize: 18, lineHeight: 23 } };
  f.state.presentation.edgeStyles = { 'B=>A': { stroke: '#aabbcc', lineWidth: 2 } };
  f.state.presentation.amountWidth = { enabled: true, currency: 'CNY' }; f.run('renderGraph()'); f.commands.sync();
  const ports = f.relationOptions, snapshot = ports.read(), camera = f.camera;
  const raw = JSON.stringify(f.state.lastGraph), tracked = [...f.state.tracked], geometry = clone(f.state.positions);
  const expected = clone(f.state.presentation);
  expected.relationFilter = { currency: 'CNY', min: '9007199254740993.02', max: '9007199254740993.02', active: true };

  assert.deepEqual(clone(ports.preview(expected.relationFilter, snapshot)), { nodes: 2, edges: 1 });
  assert.equal(await ports.commit(expected.relationFilter, snapshot.binding), true);

  assert.deepEqual(Array.from(f.edges, item => `${item.getModel().source}=>${item.getModel().target}`), ['B=>A']);
  assert.equal(f.state.presentation.relationFilter.min, '9007199254740993.02');
  assert.deepEqual(clone(f.state.presentation), expected); assert.equal(f.editor.history.status().count, 1);
  assert.deepEqual(f.camera, camera); assert.deepEqual(clone(f.state.positions), geometry);
  assert.equal(JSON.stringify(f.state.lastGraph), raw); assert.deepEqual([...f.state.tracked], tracked); assert.equal(f.requests.length, 0);
});

test('relation controller adapter exposes busy and unsaved-account blockers before history or graph mutation', async () => {
  const f = harness({ relationController: true }), ports = f.relationOptions;
  const filter = { currency: 'CNY', min: '0.00', max: '0.10', active: true }, binding = ports.read().binding;
  const before = JSON.stringify(f.state.presentation), camera = f.camera, raw = JSON.stringify(f.state.lastGraph);
  f.state.graphLoading = true;
  assert.equal(ports.read().ready, false); assert.match(ports.blocker(), /画布正在处理/);
  await assert.rejects(ports.commit(filter, binding), /画布正在处理/);
  f.state.graphLoading = false;
  f.run('hasNodeDrafts = () => true');
  assert.match(ports.blocker(), /账户资料有未保存修改/);
  await assert.rejects(ports.commit(filter, binding), /账户资料有未保存修改/);
  f.run('hasNodeDrafts = () => false');
  let release;
  const busyHistory = f.editor.history.run('Synthetic pending operation', () => new Promise(resolve => { release = resolve; }));
  await delay(); assert.match(ports.blocker(), /画布正在处理/);
  await assert.rejects(ports.commit(filter, binding), /画布正在处理/);
  release(); await busyHistory;
  assert.equal(JSON.stringify(f.state.presentation), before); assert.equal(f.editor.history.status().count, 0);
  assert.deepEqual(f.camera, camera); assert.equal(JSON.stringify(f.state.lastGraph), raw); assert.equal(f.requests.length, 0);

  assert.equal(ports.blocker(), ''); assert.equal(await ports.commit(filter, binding), true);
  assert.equal(f.editor.history.status().count, 1); assert.equal(f.edges.length, 2); assert.equal(f.requests.length, 0);
});

test('relation controller adapter rejects stale case and canvas generation bindings including A-B-A', async () => {
  const f = harness({ relationController: true }), ports = f.relationOptions;
  const original = ports.read().binding, filter = { currency: 'CNY', min: '0.00', max: '0.10', active: true };
  f.run('selectCase(2)'); f.install(); f.commands.sync();
  assert.equal(await ports.commit(filter, original), false); assert.equal(f.editor.history.status().count, 0);
  f.run('selectCase(1)'); f.install(); f.commands.sync();
  assert.notEqual(ports.read().binding, original); assert.equal(await ports.commit(filter, original), false);
  assert.equal(f.state.presentation.relationFilter.active, false); assert.equal(f.editor.history.status().count, 0);
  const beforeCanvas = ports.read().binding;
  f.state.canvasId = 7; f.state.canvasGeneration++;
  f.state.canvasId = 0; f.state.canvasGeneration++; f.commands.sync();
  assert.equal(await ports.commit(filter, beforeCanvas), false); assert.equal(f.editor.history.status().count, 0);

  assert.equal(await ports.commit(filter, ports.read().binding), true);
  assert.equal(f.state.presentation.relationFilter.active, true); assert.equal(f.editor.history.status().count, 1); assert.equal(f.requests.length, 0);
});

test('styles and hide/only/restore commands affect display without changing membership', async () => {
  const f = harness(), before = JSON.stringify(f.state.lastGraph), membership = [...f.state.tracked], camera = f.camera;
  f.select(['A', 'B']); await f.event('labelBold', true); await f.event('labelColor', '#aabbcc');
  assert.equal(f.state.presentation.nodeLabels.A.fontWeight, 'bold');
  assert.equal(f.nodes.find(item => item.getModel().id === 'A').getModel().labelCfg.style.fill, '#aabbcc');
  await f.event('hideSelection');
  assert.deepEqual(f.nodes.map(item => item.getModel().id), ['C', 'D']);
  await f.event('restoreHidden'); assert.equal(f.nodes.length, 4);
  f.select(['B', 'C']); await f.event('showOnlySelection');
  assert.deepEqual(f.nodes.map(item => item.getModel().id), ['B', 'C']); assert.equal(f.edges.length, 1);
  await f.event('restoreHidden'); assert.equal(f.nodes.length, 4); assert.equal(f.edges.length, 5);
  assert.deepEqual([...f.state.tracked], membership); assert.equal(JSON.stringify(f.state.lastGraph), before);
  assert.deepEqual(f.camera, camera); assert.equal(f.requests.length, 0);
});

test('align and distribution move coordinates and bend geometry while keeping camera', async () => {
  const f = harness(), camera = f.camera;
  f.state.edgeBendPos = { 'A=>B': [{ x: 20, y: 25 }], 'C=>D': [{ x: 140, y: 130 }] };
  f.state.edgeSnapshotLabels = { 'A=>B': { lx: 15, ly: 20 } };
  f.state.edgeLabelPos['A=>B'] = 0.5;
  f.select(['A', 'B', 'C']); await f.event('alignLeft');
  assert.deepEqual(['A', 'B', 'C'].map(id => f.state.positions[id].x), [10, 10, 10]);
  assert.equal(f.state.edgeBendPos['A=>B'][0].x, 10);
  assert.equal(f.state.edgeBendPos['C=>D'][0].x, 95);
  assert.equal(Object.hasOwn(f.state.edgeSnapshotLabels, 'A=>B'), false);
  assert.deepEqual(f.camera, camera);
  f.install(); f.state.edgeBendPos = { 'A=>B': [{ x: 20, y: 25 }] };
  f.select(['A', 'B', 'C']); await f.event('distributeHorizontal');
  assert.deepEqual(['A', 'B', 'C'].map(id => f.state.positions[id].x), [10, 55, 100]);
  assert.equal(f.state.edgeBendPos['A=>B'][0].x, 32.5);
  f.install(); f.state.edgeBendPos = { 'A=>B': [{ x: 20, y: 25 }], 'C=>D': [{ x: 140, y: 130 }] };
  f.select(['A', 'B', 'C']); await f.event('alignTop');
  assert.deepEqual(['A', 'B', 'C'].map(id => f.state.positions[id].y), [10, 10, 10]);
  assert.equal(f.state.edgeBendPos['A=>B'][0].y, 10); assert.equal(f.state.edgeBendPos['C=>D'][0].y, 85);
  f.install(); f.state.edgeBendPos = { 'A=>B': [{ x: 20, y: 25 }] };
  f.select(['A', 'B', 'C']); await f.event('distributeVertical');
  assert.deepEqual(['A', 'B', 'C'].map(id => f.state.positions[id].y), [55, 10, 100]);
  assert.equal(f.state.edgeBendPos['A=>B'][0].y, 32.5);
  assert.deepEqual(f.camera, camera); assert.equal(f.requests.length, 0);
});

test('selected-edge command events persist across renderer replacement; amount encoding blocks fixed-width override', async () => {
  const f = harness(), camera = f.camera;
  f.selectEdge('A=>B'); const original = f.state.graphView.edgeItems.get('A=>B');
  await f.event('edgeColor', '#ff5500');
  assert.notEqual(f.state.graphView.edgeItems.get('A=>B'), original);
  assert.equal(f.state.presentation.edgeStyles['A=>B'].stroke, '#ff5500');
  assert.equal(f.state.graphView.selectedEdge, 'A=>B');
  await f.event('labelItalic', true); await f.event('edgeDash', 'dotted');
  assert.equal(f.state.presentation.edgeLabels['A=>B'].fontStyle, 'italic');
  assert.deepEqual(clone(f.state.presentation.edgeStyles['A=>B'].lineDash), [2, 3]);
  f.element('#widthCurrency').value = 'currency:CNY'; f.element('#amountWidthCk').checked = true;
  await f.commands.applyWidth(); f.commands.sync();
  const count = f.editor.history.status().count, encodedWidth = f.state.graphView.edgeItems.get('A=>B').getModel().style.lineWidth;
  assert.equal(f.enabled.get('edgeWidth'), false);
  assert.equal(await f.commands.execute('edgeWidth', 12), false);
  assert.equal(f.editor.history.status().count, count);
  assert.equal(f.state.graphView.edgeItems.get('A=>B').getModel().style.lineWidth, encodedWidth);
  assert.equal(f.state.presentation.edgeStyles['A=>B'].lineWidth, undefined);
  await f.event('styleReset');
  assert.deepEqual(clone(f.edges.find(item=>item.getModel().source==='A'&&item.getModel().target==='B').getModel().style.lineDash), []);
  assert.deepEqual(clone(f.state.presentation.edgeStyles), {});
  assert.equal(f.state.graphView.edgeItems.get('A=>B').getModel().style.lineWidth, encodedWidth);
  assert.deepEqual(f.camera, camera); assert.equal(f.requests.length, 0);
});

test('case/canvas rebinding resets presentation and history, including returning to an earlier case', async () => {
  const f = harness(); f.select(['A']); await f.event('labelBold', true); await f.event('hideSelection');
  assert.equal(f.editor.history.status().count, 2);
  f.run('activateCanvas(7)');
  assert.equal(f.state.canvasId, 7); assert.equal(f.state.presentation.hiddenAccounts.length, 0);
  assert.equal(Object.keys(f.state.presentation.nodeLabels).length, 0); assert.equal(f.editor.history.status().count, 0);
  assert.equal(await f.commands.execute('styleReset'), false);
  f.install(); f.select(['A']); await f.event('labelColor', '#112233');
  f.run('selectCase(2)');
  assert.equal(f.state.caseId, 2); assert.equal(f.state.canvasId, 0);
  assert.equal(Object.keys(f.state.presentation.nodeLabels).length, 0); assert.equal(f.editor.history.status().count, 0);
  f.run('selectCase(1)'); f.install();
  assert.equal(Object.keys(f.state.presentation.nodeLabels).length, 0); assert.equal(f.editor.history.status().count, 0);
  assert.equal(f.state.presentation.relationFilter.active, false); assert.equal(f.requests.length, 0);
});

test('selection, graph refresh and busy status sync leave the user filter draft intact', async () => {
  const f = harness();
  f.element('#relationCurrency').value = 'currency:USD'; f.element('#relationMin').value = '0.27'; f.element('#relationMax').value = '9.33';
  f.select(['B']); f.commands.sync();
  f.state.lastGraph = { ...f.state.lastGraph }; f.commands.sync();
  f.state.graphLoading = true; f.commands.sync(); f.state.graphLoading = false; f.commands.sync();
  assert.equal(f.element('#relationCurrency').value, 'currency:USD');
  assert.equal(f.element('#relationMin').value, '0.27'); assert.equal(f.element('#relationMax').value, '9.33');
  assert.equal(f.state.presentation.relationFilter.active, false); assert.equal(f.editor.history.status().count, 0);
});

test('multi-node styles expose each shared or mixed property and batch formatting is one history action', async () => {
  const f = harness();
  f.state.presentation.nodeLabels = {
    A: { fontSize: 16, lineHeight: 21, fontWeight: 'bold', fontStyle: 'normal', fill: '#aabbcc' },
    B: { fontSize: 16, lineHeight: 21, fontWeight: 'normal', fontStyle: 'italic', fill: '#112233' },
  };
  f.select(['A', 'B']);
  assert.equal(f.ribbonValues.labelFontFamily, 'Arial'); assert.equal(f.ribbonValues.labelFontSize, 16);
  for (const property of ['labelBold', 'labelItalic', 'labelColor']) assert.deepEqual(f.ribbonValues[property], { mixed: true });
  f.state.presentation.nodeLabels.B.fontFamily = 'SimSun'; f.state.presentation.nodeLabels.B.fontSize = 20;
  f.commands.sync();
  assert.deepEqual(f.ribbonValues.labelFontFamily, { mixed: true }); assert.deepEqual(f.ribbonValues.labelFontSize, { mixed: true });
  const before = f.editor.history.status().count, camera = f.camera;
  await f.event('labelBold', true);
  assert.equal(f.editor.history.status().count, before + 1);
  assert.equal(f.ribbonValues.labelBold, true);
  for (const id of ['A', 'B']) assert.equal(f.state.presentation.nodeLabels[id].fontWeight, 'bold');
  assert.deepEqual(f.ribbonValues.labelItalic, { mixed: true });
  assert.deepEqual(f.ribbonValues.labelColor, { mixed: true });
  assert.equal(f.state.presentation.nodeLabels.A.fill, '#aabbcc');
  assert.equal(f.state.presentation.nodeLabels.B.fill, '#112233');
  await f.event('labelItalic', true); await f.event('labelFontSize', '24'); await f.event('labelFontFamily', 'Arial');
  for (const id of ['A', 'B']) {
    const style = f.state.presentation.nodeLabels[id];
    assert.equal(style.fontStyle, 'italic'); assert.equal(style.fontSize, 24); assert.equal(style.lineHeight, 29);
    assert.equal(style.fontFamily, 'Arial');
  }
  assert.equal(f.ribbonValues.labelFontSize, 24); assert.equal(f.ribbonValues.labelItalic, true);
  assert.deepEqual(f.camera, camera); assert.equal(f.requests.length, 0);
});

test('shared explicit and default style values do not become mixed; selected edge status clears accurately', () => {
  const f = harness();
  f.state.presentation.nodeLabels.A = { fontFamily: 'Arial', fontSize: 13, fontWeight: 'normal', fill: '#DFE3EA' };
  f.select(['A', 'B']);
  assert.equal(f.ribbonValues.labelFontSize, 13); assert.equal(f.ribbonValues.labelBold, false);
  assert.equal(f.ribbonValues.labelColor, '#dfe3ea');
  f.state.presentation.nodeLabels.A.fill = '#abc'; f.state.presentation.nodeLabels.B = { fill: '#AABBCC' };
  f.commands.sync(); assert.equal(f.ribbonValues.labelColor, '#aabbcc');
  f.selectEdge('A=>B'); f.commands.sync();
  assert.equal(f.ribbonStatus.edgeSelectionCount, 1); assert.equal(f.ribbonStatus.selectionCount, 0);
  assert.equal(f.ribbonValues.labelFontSize, 13);
  f.run('closeDrawer()'); f.commands.sync();
  assert.equal(f.ribbonStatus.edgeSelectionCount, 0); assert.equal(f.ribbonStatus.selectionCount, 0);
  assert.equal(f.enabled.get('labelBold'), false);
});

test('node appearance commands affect selected nodes as one action and round-trip through undo/redo', async () => {
  const f = harness(), sourceBefore = JSON.stringify(f.state.lastGraph), camera = f.camera;
  const sizes = Object.fromEntries(f.nodes.map(item => [item.getModel().id, item.getModel().size]));
  const styles = Object.fromEntries(f.nodes.map(item => [item.getModel().id, clone(item.getModel().style)]));
  const graphData = clone(f.state.lastGraph), tracked = [...f.state.tracked];
  const response = value => ({ ok: true, headers: { get: () => 'application/json' }, json: async () => clone(value) });
  // Exercise the editor's real CAS/read-back history path using an in-memory
  // synthetic server, rather than invoking its private restore implementation.
  f.network((path, options) => {
    if (path === '/api/track/state') {
      const body = JSON.parse(options.body);
      assert.deepEqual(body.expected_ids, tracked); assert.deepEqual(body.desired_ids, tracked);
      return response({ ok: true, tracked_ids: tracked });
    }
    if (path.startsWith('/api/graph?')) return response(graphData);
    if (path.startsWith('/api/flows?')) return response({ page: 1, size: 50, total: 0, rows: [] });
    throw new Error(`Unexpected synthetic route: ${path}`);
  });
  f.run('renderFlows = () => {};');
  f.select(['A', 'B']);
  for (const [command, value, property, expected] of [
    ['nodeBorderColor', '#ab7654', 'stroke', '#ab7654'],
    ['nodeBorderWidth', '4', 'lineWidth', 4],
    ['nodeSize', '64', 'size', 64],
  ]) {
    const count = f.editor.history.status().count;
    await f.event(command, value);
    assert.equal(f.editor.history.status().count, count + 1);
    for (const id of ['A', 'B']) assert.equal(f.state.presentation.nodeStyles[id][property], expected);
    assert.equal(f.state.presentation.nodeStyles.C, undefined); assert.equal(f.state.presentation.nodeStyles.D, undefined);
  }
  for (const item of f.nodes) {
    const model = item.getModel();
    if (['A', 'B'].includes(model.id)) {
      assert.equal(model.style.stroke, '#ab7654'); assert.equal(model.style.lineWidth, 4); assert.equal(model.size, 64);
    } else {
      assert.equal(model.size, sizes[model.id]); assert.deepEqual(clone(model.style), styles[model.id]);
    }
  }
  assert.equal(await f.editor.history.undo(), true);
  for (const id of ['A', 'B']) {
    assert.equal(f.state.presentation.nodeStyles[id].size, undefined);
    assert.equal(f.state.presentation.nodeStyles[id].stroke, '#ab7654');
    assert.equal(f.nodes.find(item => item.getModel().id === id).getModel().size, sizes[id]);
  }
  assert.equal(await f.editor.history.redo(), true);
  assert.deepEqual(clone(f.state.presentation.nodeStyles), {
    A: { stroke: '#ab7654', lineWidth: 4, size: 64 }, B: { stroke: '#ab7654', lineWidth: 4, size: 64 },
  });
  f.select(['A']); const count = f.editor.history.status().count;
  await f.event('nodeSize', 'auto');
  assert.equal(f.editor.history.status().count, count + 1);
  assert.equal(f.state.presentation.nodeStyles.A.size, undefined); assert.equal(f.state.presentation.nodeStyles.B.size, 64);
  assert.equal(f.nodes.find(item => item.getModel().id === 'A').getModel().size, sizes.A);
  assert.equal(f.ribbonValues.nodeSize, 'auto');
  assert.deepEqual(f.camera, camera); assert.equal(JSON.stringify(f.state.lastGraph), sourceBefore);
  assert.deepEqual([...f.state.tracked], tracked);
  assert.equal(f.requests.filter(request => request.path === '/api/track/state').length, 2);
});

test('node appearance rejects non-whitelisted values without history and cannot format a selected edge', async () => {
  const f = harness(); f.select(['A', 'B']);
  for (const [command, value] of [
    ['nodeBorderColor', 'url(synthetic-invalid)'], ['nodeBorderWidth', 0], ['nodeBorderWidth', 6],
    ['nodeSize', 23], ['nodeSize', 97], ['nodeSize', 'not-a-size'], ['nodeFill', '#ffffff'],
  ]) assert.equal(await f.commands.execute(command, value), false);
  assert.equal(f.editor.history.status().count, 0); assert.deepEqual(clone(f.state.presentation.nodeStyles), {});
  await f.event('nodeBorderColor', '#ABC'); await f.event('nodeBorderWidth', '0.5'); await f.event('nodeSize', '24');
  for (const id of ['A', 'B']) assert.deepEqual(Object.keys(f.state.presentation.nodeStyles[id]).sort(), ['lineWidth', 'size', 'stroke']);
  const before = JSON.stringify(f.state.presentation.nodeStyles), count = f.editor.history.status().count;
  f.selectEdge('A=>B'); f.commands.sync();
  for (const command of ['nodeBorderColor', 'nodeBorderWidth', 'nodeSize']) {
    assert.equal(f.enabled.get(command), false);
    assert.equal(await f.commands.execute(command, command === 'nodeBorderColor' ? '#123456' : 32), false);
  }
  assert.equal(JSON.stringify(f.state.presentation.nodeStyles), before); assert.equal(f.editor.history.status().count, count);
  assert.equal(f.requests.length, 0);
});

test('style reset clears node appearance while retaining filters, encoding and graph geometry', async () => {
  const f = harness(), sourceBefore = JSON.stringify(f.state.lastGraph), camera = f.camera;
  f.element('#relationCurrency').value = 'currency:CNY'; f.element('#relationMin').value = '0.01';
  await f.commands.applyFilter();
  f.element('#widthCurrency').value = 'currency:CNY'; f.element('#amountWidthCk').checked = true;
  await f.commands.applyWidth();
  f.select(['A', 'B']); await f.event('nodeBorderColor', '#abcdef'); await f.event('nodeBorderWidth', '3');
  await f.event('nodeSize', '48'); await f.event('labelBold', true);
  f.state.edgeBendPos = { 'A=>B': [{ x: 25, y: 30 }] }; f.state.edgeLabelPos = { 'A=>B': 0.37 };
  const positionsBefore = clone(f.state.positions), bendBefore = clone(f.state.edgeBendPos), ratioBefore = clone(f.state.edgeLabelPos);
  const filterBefore = clone(f.state.presentation.relationFilter), encodingBefore = clone(f.state.presentation.amountWidth);
  const count = f.editor.history.status().count;
  await f.event('styleReset');
  assert.equal(f.editor.history.status().count, count + 1);
  assert.deepEqual(clone(f.state.presentation.nodeStyles), {}); assert.deepEqual(clone(f.state.presentation.nodeLabels), {});
  assert.deepEqual(clone(f.state.presentation.relationFilter), filterBefore); assert.deepEqual(clone(f.state.presentation.amountWidth), encodingBefore);
  assert.deepEqual(clone(f.state.positions), positionsBefore); assert.deepEqual(clone(f.state.edgeBendPos), bendBefore);
  assert.deepEqual(clone(f.state.edgeLabelPos), ratioBefore); assert.deepEqual(f.camera, camera);
  assert.equal(JSON.stringify(f.state.lastGraph), sourceBefore); assert.equal(f.requests.length, 0);
});

test('explicit details accepts exactly one node or one relationship without changing graph facts', async () => {
  const f = harness(), sourceBefore = JSON.stringify(f.state.lastGraph), camera = f.camera;
  for (const ids of [[], ['A', 'B']]) {
    f.select(ids); assert.equal(f.enabled.get('inspectSelection'), false);
    assert.equal(await f.commands.execute('inspectSelection'), false);
  }
  f.select(['A']);
  assert.equal(f.enabled.get('inspectSelection'), true);
  assert.equal(await f.commands.execute('inspectSelection'), true);
  assert.equal(f.drawerCalls.at(-1).kind, 'node'); assert.equal(f.drawerCalls.at(-1).id, 'A');
  f.selectEdge('A=>B'); f.commands.sync();
  assert.equal(f.enabled.get('inspectSelection'), true);
  assert.equal(await f.commands.execute('inspectSelection'), true);
  assert.equal(f.drawerCalls.at(-1).kind, 'edge'); assert.equal(f.drawerCalls.at(-1).id, 'A=>B');
  f.state.graphSelection.ids.add('A'); f.commands.sync();
  assert.equal(f.enabled.get('inspectSelection'), false);
  assert.equal(await f.commands.execute('inspectSelection'), false);
  assert.equal(f.drawerCalls.length, 2);
  f.run('closeDrawer()'); f.state.graphSelection.ids.add('missing'); f.commands.sync();
  assert.equal(f.enabled.get('inspectSelection'), false);
  assert.equal(await f.commands.execute('inspectSelection'), false);
  assert.equal(f.editor.history.status().count, 0); assert.equal(f.requests.length, 0);
  assert.equal(JSON.stringify(f.state.lastGraph), sourceBefore); assert.deepEqual(f.camera, camera);
});

test('explicit details blocks loading, layout, history, node dragging and edge/pointer gestures', async () => {
  const f = harness(); f.select(['A']);
  async function blocked() {
    f.commands.sync(); assert.equal(f.enabled.get('inspectSelection'), false);
    assert.equal(await f.commands.execute('inspectSelection'), false);
  }
  f.state.graphLoading = true; await blocked(); f.state.graphLoading = false;
  f.run("layoutJob={phase:'computing'}"); await blocked(); f.run('layoutJob=null');
  let release;
  const pending = f.editor.history.run('Synthetic pending command', () => new Promise(resolve => { release = resolve; }));
  await delay(); await blocked(); release(); await pending;
  f.state.graphSelection.status = () => ({ dragging: true }); await blocked();
  f.state.graphSelection.status = () => ({ dragging: false });
  f.fireWindow('funds:edge-drag-start'); await blocked();
  f.fireWindow('funds:edge-drag-end'); f.commands.sync(); assert.equal(f.enabled.get('inspectSelection'), true);
  f.element('#graphContainer').dispatchEvent({ type: 'pointerdown', button: 0 }); await blocked();
  f.fireWindow('pointercancel'); f.commands.sync(); assert.equal(f.enabled.get('inspectSelection'), true);
  f.fireWindow('funds:edge-drag-start', { defaultPrevented: true });
  assert.equal(await f.commands.execute('inspectSelection'), true);
  assert.equal(f.drawerCalls.length, 1);
});

test('Enter uses the details command only when the graph container itself has focus', async () => {
  const f = harness(); f.select(['B']); f.focus();
  const event = f.key(); await f.settle();
  assert.equal(event.defaultPrevented, true); assert.equal(f.drawerCalls.length, 1);
  assert.equal(f.drawerCalls[0].id, 'B'); assert.equal(f.editor.history.status().count, 0);
  const ignored = [
    { repeat: true }, { isComposing: true }, { defaultPrevented: true },
    { ctrlKey: true }, { metaKey: true }, { altKey: true }, { shiftKey: true },
    { key: ' ' }, { target: f.element('#graphCanvas') }, { target: f.element('#graphFindInput') },
  ];
  for (const input of ignored) f.key(input);
  f.focus(f.element('#graphFindInput')); f.key();
  f.focus(); f.element('#graphContainer').closest = () => f.element('#editable'); f.key();
  f.element('#graphContainer').closest = () => null;
  await f.settle(); assert.equal(f.drawerCalls.length, 1);
  for (const modal of [
    { open: true }, { hidden: false, style: {}, getClientRects: () => [{}] },
  ]) { f.modals([modal]); assert.equal(f.key().defaultPrevented, false); }
  f.modals([{ hidden: false, style: { display: 'none' }, getClientRects: () => [] }]);
  f.key(); await f.settle(); assert.equal(f.drawerCalls.length, 2);
  f.modals([]); f.select([]); assert.equal(f.key().defaultPrevented, false);
  f.select(['A', 'B']); assert.equal(f.key().defaultPrevented, false);
  f.select(['A']); f.state.graphLoading = true; assert.equal(f.key().defaultPrevented, false);
  f.state.graphLoading = false; f.fireWindow('funds:edge-drag-start'); assert.equal(f.key().defaultPrevented, false);
  f.fireWindow('blur'); f.key(); await f.settle(); assert.equal(f.drawerCalls.length, 3);
  f.selectEdge('A=>B'); f.key(); await f.settle();
  assert.equal(f.drawerCalls.at(-1).kind, 'edge'); assert.equal(f.drawerCalls.length, 4);
});

test('details completion is rejected after a binding change, including returning to the same canvas', async () => {
  const f = harness(); f.select(['A']);
  let release;
  f.drawerWait(new Promise(resolve => { release = resolve; }));
  const pending = f.commands.execute('inspectSelection');
  f.state.canvasGeneration += 1;
  release(); assert.equal(await pending, false);
  assert.equal(f.drawerCalls.length, 1); assert.equal(f.editor.history.status().count, 0);
  f.drawerWait(null); f.commands.sync();
  assert.equal(await f.commands.execute('inspectSelection'), true);
});

test('releasing a graph pointer restores the explicit button command before its next click', async () => {
  const f = harness(); f.select(['A']);
  f.element('#graphContainer').dispatchEvent({ type: 'pointerdown', button: 0 });
  assert.equal(f.enabled.get('inspectSelection'), false);
  f.fireWindow('pointerup');
  assert.equal(f.enabled.get('inspectSelection'), true);
  await f.event('inspectSelection');
  assert.equal(f.drawerCalls.length, 1); assert.equal(f.drawerCalls[0].id, 'A');
  assert.equal(f.editor.history.status().count, 0);
});

test('style reset clears annotations through its normal redraw and undo restores the complete prior display', async () => {
  const f=harness(),graphData=clone(f.state.lastGraph),tracked=[...f.state.tracked],camera=f.camera;
  const response=value=>({ok:true,headers:{get:()=> 'application/json'},json:async()=>clone(value)});
  f.network((path,options)=>{
    if(path==='/api/track/state') {
      const body=JSON.parse(options.body);assert.deepEqual(body.expected_ids,tracked);assert.deepEqual(body.desired_ids,tracked);
      return response({ok:true,tracked_ids:tracked});
    }
    if(path.startsWith('/api/graph?'))return response(graphData);
    if(path.startsWith('/api/flows?'))return response({page:1,size:50,total:0,rows:[]});
    throw new Error(`Unexpected synthetic route: ${path}`);
  });
  f.state.presentation.nodeStyles={A:{size:48,stroke:'#aabbcc'}};
  f.state.presentation.annotations=[{id:'A',text:'合成主体\n待核对',anchor:'above',dx:23,dy:-14,size:20,fontFamily:'KaiTi',color:'#c084fc'}];
  f.run(`renderFlows=()=>{};renderGraph();globalThis.resetPaints=0;const renderBeforeReset=renderGraph;
    renderGraph=(...args)=>{resetPaints++;return renderBeforeReset(...args)};`);
  const before=clone(f.state.presentation),facts=JSON.stringify(f.state.lastGraph);
  const result=await f.commands.execute('styleReset');await f.settle();
  assert.equal(result,true,JSON.stringify(f.messages));
  assert.equal(f.run('resetPaints'),1,'the presentation command owns one unified redraw');
  assert.deepEqual(clone(f.state.presentation.annotations),[]);assert.deepEqual(clone(f.state.presentation.nodeStyles),{});
  assert.equal(f.editor.history.status().count,1);
  assert.equal(await f.editor.history.undo(),true);assert.deepEqual(clone(f.state.presentation),before);
  assert.equal(await f.editor.history.redo(),true);assert.deepEqual(clone(f.state.presentation.annotations),[]);
  assert.deepEqual(f.camera,camera);assert.equal(JSON.stringify(f.state.lastGraph),facts);assert.deepEqual([...f.state.tracked],tracked);
});
