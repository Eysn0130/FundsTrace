import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { harness } from './legacy_navigation_harness.mjs';

const source = name => readFileSync(new URL(`../static/${name}`, import.meta.url), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const response = (value, status = 200) => ({ ok: status === 200, status,
  headers: { get: () => 'application/json' }, json: async () => value, text: async () => JSON.stringify(value) });
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }

function editor({ relationships = [], navigation = false } = {}) {
  const h = harness('app.js'), env = h.run('globalThis'), state = h.run('state');
  const events = new Map(), elementEvents = new Map(), messages = [], searches = [], boxes = [1, 2].map(id => ({ dataset: { id: String(id) }, checked: true }));
  env.setTimeout = (callback, delay = 0) => setTimeout(callback, delay === 1500 ? 0 : delay); env.clearTimeout = clearTimeout;
  env.performance = { now: () => 0 };
  env.CustomEvent = class { constructor(type, options) { this.type = type; this.detail = options.detail; } };
  env.window.addEventListener = (name, fn) => { if (!events.has(name)) events.set(name, new Set()); events.get(name).add(fn); };
  env.window.removeEventListener = (name, fn) => events.get(name)?.delete(fn);
  env.window.dispatchEvent = event => { for (const fn of [...events.get(event.type) || []]) fn(event); };
  const labels = ['money', 'time', 'summary', 'currency'].map(em => Object.assign(h.element(`#label-${em}`), { dataset: { em }, checked: em === 'money' }));
  let displayPortaled = false;
  env.document.querySelectorAll = selector => {
    if (selector === '#flowTbody .flow-ck') return boxes;
    if (selector.includes('.modal') || selector.includes('.act-track')) return [];
    return [...new Set(selector.split(',').flatMap(value => {
      const part = value.trim();
      if (part === '#edgeLabelGroup .edge-ck') return displayPortaled ? labels.slice(0, 2) : labels;
      if (part === '#toolbarDisplayExtra .edge-ck') return labels.slice(2);
      return /^#[\w-]+$/.test(part) ? [h.element(part)] : [];
    }))];
  };
  const container = h.element('#graphContainer');
  container.addEventListener = (type, fn) => { if (!elementEvents.has(type)) elementEvents.set(type, []); elementEvents.get(type).push(fn); };
  h.run('globalThis.savedLoadGraph = loadGraph; globalThis.savedDoSearch = doSearch;');
  h.bindApp(); env.loadGraph = env.savedLoadGraph; env.doSearch = env.savedDoSearch;
  env.toast = (message, type) => messages.push({ message, type });
  // The table-workspace renderer owns stable anchor selection. The editor must
  // not replace its result with a checkbox snapshot captured before a request.
  env.renderFlows = () => {};
  let matrix = [1, 0, 0, 0, 1, 0, 12, 15, 1], models = [], edgeItems = [];
  const group = { getMatrix: () => matrix, setMatrix: value => { matrix = [...value]; } };
  state.graph = { getNodes: () => models.map(model => ({ getModel: () => model })), getEdges: () => edgeItems,
    get: name => name === 'group' ? group : null, paint() {}, setItemState() {}, clear() { models = []; edgeItems = []; } };
  env.renderGraph = () => {
    models = state.lastGraph.nodes.filter(node => !state.graphView.visibleIds || state.graphView.visibleIds.has(node.account))
      .map(node => ({ id: node.account, x: state.positions[node.account]?.x || 0, y: state.positions[node.account]?.y || 0,
        data: { amount: '9999999999999999.99', photo: 'SYNTHETIC-PHOTO' } }));
    edgeItems = state.lastGraph.edges.map(edge => {
      const model = { ...edge, type: env.graphEdgeType(edge) }, shapes = { get: () => [] };
      return { getModel: () => model, getContainer: () => shapes, getKeyShape: () => ({ attr: () => null }) };
    });
    state.graphView.edgeItems = new Map(edgeItems.map(item => [env.edgeKey(item.getModel()), item]));
    state.graphInited = true;
  };
  for (const name of ['scheduleGraphViewport', 'scheduleGraphLabels', 'updateGraphViewStatus', 'updateHeader']) env[name] = () => {};
  state.caseId = 1; state.graphLoading = false; state.graphInited = true;
  state.positions = { 'SYN-A': { x: 3, y: 4 }, 'SYN-B': { x: 5, y: 6 } };
  const tracked = new Map([['1:0', [1]], ['2:0', [90]], ['1:7', [70]]]);
  const graph = ids => ({ tracked_ids: [...ids], nodes: ids.length ? [{ account: 'SYN-A' }, { account: 'SYN-B' }] : [],
    edges: ids.length ? relationships.map(edge => ({ ...edge })) : [], main_accounts: [], focus_nodes: [] });
  state.lastGraph = graph([1]); state.tracked = new Set([1]); env.renderGraph();
  let intercept = null, conflict = false;
  h.network(async (path, options) => {
    if (intercept) { const result = intercept(path, options); if (result) return result; }
    const body = options.body ? JSON.parse(options.body) : {};
    const query = new URL(path, 'http://synthetic.local').searchParams;
    const key = `${body.case_id ?? query.get('case_id')}:${body.canvas_id ?? query.get('canvas_id') ?? 0}`;
    if (path === '/api/track/state') {
      if (conflict || JSON.stringify([...(tracked.get(key) || [])].sort()) !== JSON.stringify([...body.expected_ids].sort())) return response({ detail: 'synthetic conflict' }, 409);
      tracked.set(key, [...body.desired_ids]); return response({ ok: true, tracked_ids: body.desired_ids });
    }
    if (path === '/api/track' || path === '/api/track/bulk') {
      if (body.expected_ids && JSON.stringify([...(tracked.get(key) || [])].sort()) !== JSON.stringify([...body.expected_ids].sort())) return response({ detail: 'synthetic bulk conflict' }, 409);
      const ids = new Set(tracked.get(key) || []);
      for (const id of body.flow_ids) options.method === 'DELETE' ? ids.delete(id) : ids.add(id);
      tracked.set(key, [...ids].sort()); return response({ ok: true, tracked: ids.size, tracked_ids: [...ids].sort() });
    }
    if (path.startsWith('/api/graph')) {
      if (options.method === 'DELETE') tracked.set(key, []);
      return response(graph(tracked.get(key) || []));
    }
    if (path.startsWith('/api/flows?')) { searches.push(path); return response({ page: Number(query.get('page')), size: 50, total: 2, rows: [] }); }
    return response({});
  });
  h.run(source('canvas-history.js'));
  let snapshot;
  const create = env.window.FundsCanvasHistory.create;
  env.window.FundsCanvasHistory = { create(options) { snapshot = options.capture; return create(options); } };
  h.run(source('canvas-editor.js'));
  for (const fn of [...events.get('DOMContentLoaded') || []]) fn();
  const nav = new Map();
  if (navigation) {
    for (const page of ['home', 'cases', 'accounts', 'analysis']) {
      const node = h.element(`#nav-${page}`); node.dataset.navPage = page; nav.set(page, node);
    }
    const queryAll = env.document.querySelectorAll, classes = new Set(), page = h.element('#graphPage');
    page.classList = { toggle(name, on) { on ? classes.add(name) : classes.delete(name); }, contains: name => classes.has(name) };
    env.document.querySelectorAll = selector => selector === '[data-nav-page]' ? [...nav.values()] : queryAll(selector);
    env.location = env.window.location; env.history = env.window.history;
    env.refreshAll = async () => {};
    env.window.FundsWorkspaceSession = { capture() {}, retainedCaseIds: () => ['1'] };
    const ready = new Set(events.get('DOMContentLoaded'));
    h.run(source('app-navigation.js'));
    for (const fn of events.get('DOMContentLoaded')) if (!ready.has(fn)) fn();
  }
  return { h, env, state, boxes, labels, searches, messages, tracked, snapshot: () => snapshot(), history: env.window.FundsCanvasEditor.history,
    nav,
    portalDisplay(value = true) { displayPortaled = value; },
    membership: env.window.FundsCanvasEditor.membership,
    get models() { return models; }, get matrix() { return matrix; }, set matrix(value) { matrix = value; },
    conflict(value) { conflict = value; }, intercept(fn) { intercept = fn; },
    event(type, event = {}) { for (const fn of [...events.get(type) || []]) fn(event); },
    gesture(type, event = {}) { for (const fn of elementEvents.get(type) || []) fn({ preventDefault() {}, stopImmediatePropagation() {}, ...event }); } };
}

test('portaled display checkboxes retain change history, busy locking and restored selection state', async () => {
  const f = editor(), summary = f.labels.find(node => node.dataset.em === 'summary');
  f.portalDisplay();
  summary.checked = true;
  await summary.onchange({ target: summary });
  assert.equal(f.state.edgeLabels.has('summary'), true);
  assert.equal(f.history.status().count, 1);
  await f.h.element('#btnUndo').onclick();
  assert.equal(f.state.edgeLabels.has('summary'), false);
  assert.equal(summary.checked, false, 'undo synchronizes controls outside their original label group');

  const wait = deferred();
  const pending = f.env.window.FundsCanvasEditor.command('synthetic pending display command', () => wait.promise);
  await flush();
  assert.equal(f.history.status().busy, true);
  assert.ok(f.labels.every(node => node.disabled), 'both portaled and inline display controls honor the same command lock');
  wait.resolve(); await pending;
  assert.ok(f.labels.every(node => !node.disabled));
});

function cameraChrome(f, { left = 560, top = 180, bounds } = {}) {
  f.h.run(source('workspace-camera.js'));
  const box = { left, top, width: 1440 - left, height: 900 - top };
  const group = f.state.graph.get('group');
  f.state.graph.get = name => name === 'group' ? group : box[name];
  f.state.graph.changeSize = () => {};
  f.state.graph.translate = (x, y) => {
    const next = [...f.matrix]; next[6] += x; next[7] += y; f.matrix = next;
  };
  const container = { get clientWidth() { return box.width; }, get clientHeight() { return box.height; }, getBoundingClientRect: () => box };
  f.state.graphCamera = f.env.window.FundsWorkspaceCamera.create({ graph: f.state.graph, container,
    getContentBounds: () => bounds, canCompose: () => !f.history.status().busy });
  return {
    move(nextLeft, nextTop = box.top, sample = true) {
      box.width += box.left - nextLeft; box.height += box.top - nextTop;
      box.left = nextLeft; box.top = nextTop;
      if (sample) f.state.graphCamera.resize();
    },
    screen(model = f.models[0]) { return { x: box.left + model.x * f.matrix[0] + f.matrix[6],
      y: box.top + model.y * f.matrix[4] + f.matrix[7] }; },
  };
}

test('canvas editor snapshots include only IDs, geometry and display choices', () => {
  const f = editor();
  f.state.edgeSnapshotLabels = { 'SYN-A=>SYN-B': { lx: 1, ly: 2, bw: 3, bh: 4, label: 'PRIVATE-SYNTHETIC', amount: '12.34' } };
  f.models[0].x = 44;
  const snapshot = f.snapshot(), text = JSON.stringify(snapshot);
  assert.equal(snapshot.positions['SYN-A'].x, 44);
  assert.doesNotMatch(text, /PRIVATE|"(?:amount|photo|total|amount_totals)"|999999/);
  assert.deepEqual(Object.keys(snapshot).sort(), ['autoRoutes','edgeTypes','camera','cameraOffset','cameraTransform','edgeBendPos','edgeLabelPos','edgeLabels','edgeSnapshotLabels','edgeType','graphView','layout','positions','showIds','showPhotos','trackedIds','presentation'].sort());
});

test('canvas editor snapshots sanitize automatic route geometry and reject nonfinite routes', () => {
  const f = editor();
  f.state.graphView.autoRoutes = {
    'SYN-A=>SYN-B': {
      source: { x: 3, y: 4, extra: 'PRIVATE-SYNTHETIC' }, target: { x: 5, y: 6, photo: 'PRIVATE-PHOTO' },
      controlPoints: [{ x: 17, y: 18, extra: 'PRIVATE-CONTROL' }], curvePolicy: 'obstacle-aware',
      disallowedbusiness: { amount: 'PRIVATE-AMOUNT', photo: 'PRIVATE-PHOTO' },
    },
    'invalid-source': { source: { x: NaN, y: 4 }, target: { x: 5, y: 6 }, controlPoints: [{ x: 7, y: 8 }] },
    'invalid-target': { source: { x: 3, y: 4 }, target: { x: 5, y: Infinity }, controlPoints: [{ x: 7, y: 8 }] },
    'invalid-control': { source: { x: 3, y: 4 }, target: { x: 5, y: 6 }, controlPoints: [{ x: Infinity, y: 8 }] },
  };
  const original = f.state.graphView.autoRoutes['SYN-A=>SYN-B'];
  const snapshot = f.snapshot(), routes = JSON.parse(JSON.stringify(snapshot.autoRoutes));
  assert.deepEqual(routes, { 'SYN-A=>SYN-B': {
    source: { x: 3, y: 4 }, target: { x: 5, y: 6 }, controlPoints: [{ x: 17, y: 18 }], curvePolicy: 'obstacle-aware',
  } });
  assert.doesNotMatch(JSON.stringify(snapshot), /PRIVATE|disallowedbusiness|"(?:extra|amount|photo|total|amount_totals)"|999999/);
  assert.equal(original.source.extra, 'PRIVATE-SYNTHETIC', 'capture must not mutate the live route cache');
  original.controlPoints[0].x = 99;
  assert.equal(snapshot.autoRoutes['SYN-A=>SYN-B'].controlPoints[0].x, 17, 'history must own its geometry copy');
});

test('tracking, undo and redo preserve checked rows, filters, page and camera', async () => {
  const f = editor(); f.state.search.page = 3; f.h.element('#fAccount').value = 'SYN-FILTER';
  f.boxes[1].checked = false;
  await f.h.run('trackFlows([2])');
  assert.deepEqual(f.tracked.get('1:0'), [1, 2]); assert.equal(f.history.status().count, 1);
  assert.equal(f.boxes[0].checked, true); assert.equal(f.boxes[1].checked, false);
  f.matrix = [2, 0, 0, 0, 2, 0, 80, 90, 1];
  await f.h.element('#btnUndo').onclick();
  assert.deepEqual(f.tracked.get('1:0'), [1]); assert.deepEqual(f.matrix, [1, 0, 0, 0, 1, 0, 12, 15, 1]);
  await f.h.element('#btnRedo').onclick(); assert.deepEqual(f.tracked.get('1:0'), [1, 2]);
  assert.equal(f.boxes[0].checked, true); assert.equal(f.boxes[1].checked, false);
  assert.equal(f.state.search.page, 3); assert.ok(f.searches.every(path => path.includes('account=SYN-FILTER') && path.includes('page=3')));
});

test('undo and redo preserve zoom, selection and screen camera across sidebar and toolbar transitions', async () => {
  const f = editor(), chrome = cameraChrome(f);
  f.matrix = [2.5, 0, 0, 0, 2.5, 0, 20, -40, 1];
  const selected = new Set(['SYN-A']);
  const selection = { ids: selected, rebind(ids) { selected.clear(); for (const id of ids) selected.add(id); },
    refresh() {}, clear() { selected.clear(); }, destroy() { selected.clear(); } };
  f.state.graphSelection = selection;
  const render = f.env.renderGraph;
  f.env.renderGraph = () => { render(); f.state.graphSelection = selection; };
  const before = chrome.screen();
  f.event('funds:selection-drag-start'); f.models[0].x = 83; f.models[0].y = 44;
  f.event('funds:selection-drag-end');
  const after = chrome.screen();
  assert.equal(f.history.status().count, 1);
  for (const left of [420.5, 180.25, 0]) chrome.move(left, 90);
  assert.deepEqual(chrome.screen(), after);
  assert.equal(f.history.status().count, 1, 'shell transitions never add geometry history');
  assert.equal(await f.h.element('#btnUndo').onclick(), true);
  assert.deepEqual(chrome.screen(), before); assert.deepEqual([...selected], ['SYN-A']);
  assert.equal(f.models[0].x, 3); assert.equal(f.models[0].y, 4); assert.equal(f.matrix[0], 2.5);
  assert.equal(await f.h.element('#btnRedo').onclick(), true);
  assert.deepEqual(chrome.screen(), after); assert.deepEqual([...selected], ['SYN-A']);
  chrome.move(560, 180);
  assert.deepEqual(chrome.screen(), after);
  assert.equal(await f.h.element('#btnUndo').onclick(), true);
  assert.deepEqual(chrome.screen(), before);
  assert.equal(f.state.positions['SYN-A'].x, 3); assert.equal(f.state.positions['SYN-A'].y, 4);
});

test('a membership response cannot undo shell expansion performed while its graph read was pending', async () => {
  const f = editor(), chrome = cameraChrome(f), read = deferred(), started = deferred();
  f.matrix = [1.75, 0, 0, 0, 1.75, 0, -60, 70, 1];
  const before = chrome.screen();
  f.intercept(path => { if (path.startsWith('/api/graph')) { started.resolve(); return read.promise; } return null; });
  const operation = f.membership.set([2], true); await started.promise;
  chrome.move(0); assert.deepEqual(chrome.screen(), before);
  read.resolve(response({ tracked_ids: [1, 2], nodes: [{ account: 'SYN-A' }, { account: 'SYN-B' }], edges: [] }));
  assert.equal((await operation).status, 'confirmed');
  assert.deepEqual(chrome.screen(), before); assert.equal(f.matrix[0], 1.75);
  assert.deepEqual(f.tracked.get('1:0'), [1, 2]);
});

test('rollback after a failed graph publication restores geometry without undoing pending shell compensation', async () => {
  const f = editor(), chrome = cameraChrome(f), read = deferred(), started = deferred(), render = f.env.renderGraph;
  f.matrix = [0.65, 0, 0, 0, 0.65, 0, 160, 90, 1];
  const before = chrome.screen();
  f.intercept(path => { if (path.startsWith('/api/graph')) { started.resolve(); return read.promise; } return null; });
  f.env.renderGraph = () => { if (f.state.tracked.has(2)) throw new Error('synthetic publication failure'); render(); };
  const operation = f.membership.set([2], true); await started.promise;
  chrome.move(0, 90);
  read.resolve(response({ tracked_ids: [1, 2], nodes: [{ account: 'SYN-A' }, { account: 'SYN-B' }], edges: [] }));
  assert.equal((await operation).status, 'failed');
  assert.deepEqual(chrome.screen(), before); assert.equal(f.matrix[0], 0.65);
  assert.deepEqual(f.tracked.get('1:0'), [1]); assert.equal(f.history.status().count, 0);
});

test('history snapshots without camera offset remain restorable after shell movement', async () => {
  const f = editor(), chrome = cameraChrome(f), before = f.snapshot();
  f.models[0].x = 80;
  const after = f.snapshot(); delete before.cameraOffset; delete after.cameraOffset; delete before.cameraTransform; delete after.cameraTransform;
  assert.equal(f.history.record('合成旧格式几何', before, after), true);
  const target = chrome.screen({ x: 3, y: 4 });
  chrome.move(0);
  assert.equal(await f.h.element('#btnUndo').onclick(), true);
  assert.deepEqual(chrome.screen(), target);
});

test('history capture consumes shell movement that preceded its ResizeObserver delivery', () => {
  const f = editor(), chrome = cameraChrome(f), before = chrome.screen();
  chrome.move(200, 100, false);
  const snapshot = f.snapshot();
  assert.deepEqual(chrome.screen(), before);
  assert.deepEqual({ ...snapshot.cameraOffset }, { x: 360, y: 80 });
  assert.equal(snapshot.camera[6], 372); assert.equal(snapshot.camera[7], 95);
  assert.equal(f.history.status().count, 0);
});

test('geometry undo and redo retain automatic sidebar zoom without recording a shell history entry', async () => {
  const f = editor(), chrome = cameraChrome(f, { bounds: { minX: 0, minY: 0, maxX: 200, maxY: 100 } });
  f.matrix = [2, 0, 0, 0, 2, 0, 200, 100, 1];
  const original = [...f.matrix];
  f.gesture('mousedown', { button: 0 }); f.models[0].x = 83; f.event('mouseup'); await flush();
  f.state.graphCamera.beginSidebar({ collapsed: true }); chrome.move(0); f.state.graphCamera.endSidebar();
  const composed = [...f.matrix];
  assert.ok(composed[0] > original[0]); assert.equal(f.history.status().count, 1);
  assert.equal(await f.h.element('#btnUndo').onclick(), true);
  assert.equal(f.models[0].x, 3);
  for (const i of [0, 4, 6, 7]) assert.ok(Math.abs(f.matrix[i] - composed[i]) < 1e-8);
  assert.equal(await f.h.element('#btnRedo').onclick(), true); assert.equal(f.models[0].x, 83);
  for (const i of [0, 4, 6, 7]) assert.ok(Math.abs(f.matrix[i] - composed[i]) < 1e-8);
});

test('a geometry edit captured while composed restores through its inverse affine after an uninterrupted expansion', async () => {
  const f = editor(), chrome = cameraChrome(f, { bounds: { minX: 0, minY: 0, maxX: 200, maxY: 100 } });
  f.matrix = [2, 0, 0, 0, 2, 0, 200, 100, 1];
  const original = [...f.matrix];
  f.state.graphCamera.beginSidebar({ collapsed: true }); chrome.move(0); f.state.graphCamera.endSidebar();
  f.gesture('mousedown', { button: 0 }); f.models[0].x = 83; f.event('mouseup'); await flush();
  f.state.graphCamera.beginSidebar({ collapsed: false }); chrome.move(560); f.state.graphCamera.endSidebar();
  assert.equal(f.history.status().count, 1);
  assert.equal(await f.h.element('#btnUndo').onclick(), true); assert.equal(f.models[0].x, 3);
  for (const i of [0, 4, 6, 7]) assert.ok(Math.abs(f.matrix[i] - original[i]) < 1e-8);
});

test('write refresh leaves the table renderer latest anchor selection intact', async () => {
  const f = editor(); f.boxes[0].checked = true; f.boxes[1].checked = false;
  f.env.renderFlows = () => { f.boxes[0].checked = false; f.boxes[1].checked = true; };
  await f.h.run('trackFlows([2])');
  assert.equal(f.boxes[0].checked, false); assert.equal(f.boxes[1].checked, true);
});

test('membership batch accepts string IDs, retains existing geometry and camera, and is one undo step', async () => {
  const f = editor(), render = f.env.renderGraph, before = f.snapshot();
  f.models[0].x = 44;
  f.env.renderGraph = () => {
    assert.equal(f.state.forceFit, false); assert.equal(f.state.justAdded, null);
    render(); f.matrix = [3, 0, 0, 0, 3, 0, 77, 88, 1];
  };
  const result = await f.membership.set(['2', 3, '2'], true);
  assert.equal(result.status, 'confirmed'); assert.equal(result.ok, true);
  assert.deepEqual(f.tracked.get('1:0'), [1, 2, 3]);
  assert.equal(f.models[0].x, 44); assert.deepEqual(f.matrix, [...before.camera]);
  assert.equal(f.history.status().count, 1); assert.equal(f.history.status().undoLabel, '更新流水上图状态');
  await f.h.element('#btnUndo').onclick(); assert.deepEqual(f.tracked.get('1:0'), [1]);
  await f.h.element('#btnRedo').onclick(); assert.deepEqual(f.tracked.get('1:0'), [1, 2, 3]);
});

test('membership latest unsent intent coalesces without a write or history entry', async () => {
  const f = editor(), first = f.membership.set(['2'], true), last = f.membership.set([2], false);
  assert.equal((await first).status, 'superseded'); assert.equal((await last).status, 'confirmed');
  assert.deepEqual(f.tracked.get('1:0'), [1]); assert.equal(f.history.status().count, 0);
  assert.equal(f.h.requests.filter(item => item.path === '/api/track/state').length, 0);
  assert.deepEqual([...f.membership.snapshot().pendingIds], []);
});

for (const offscreen of [false, true]) {
  test(`membership addition keeps the camera even when endpoints are ${offscreen ? 'outside' : 'inside'} the current view`, async () => {
    const f = editor(); let fits = 0;
    f.matrix = [1,0,0,0,1,0,offscreen ? -2000 : 100,100,1];
    const before = [...f.matrix];
    f.state.graph.findById = id => f.state.graph.getNodes().find(node => node.getModel().id === id);
    f.state.graph.getCanvasByPoint = (x,y) => ({x:x*f.matrix[0]+f.matrix[6],y:y*f.matrix[4]+f.matrix[7]});
    f.state.graph.getZoom = () => f.matrix[0];
    f.env.fitGraph = () => { fits++; f.matrix = [1,0,0,0,1,0,450,300,1]; };
    f.intercept(path => path.startsWith('/api/graph') ? response({
      tracked_ids:[...f.tracked.get('1:0')], nodes:[{account:'SYN-A'},{account:'SYN-B'}],
      edges:f.tracked.get('1:0').includes(2) ? [{source:'SYN-A',target:'SYN-B',ids:[2]}] : [],
    }) : null);
    assert.equal((await f.membership.set([2], true)).status, 'confirmed');
    assert.equal(fits, 0, 'adding tracked flows never requests a global fit or shrinks the camera');
    const after = [...f.matrix];
    assert.deepEqual(after, before, 'incremental publication retains both the existing zoom and camera center');
    await f.h.element('#btnUndo').onclick(); assert.deepEqual(f.matrix, before);
    await f.h.element('#btnRedo').onclick(); assert.deepEqual(f.matrix, after);
    assert.equal((await f.membership.set([2], false)).status, 'confirmed');
    assert.equal(fits, 0, 'removing membership never fits the camera');
  });
}

test('membership additions reveal endpoints in a neighborhood and undo restores its display scope', async () => {
  const f = editor();
  f.state.graphView.visibleIds = new Set(['SYN-A']); f.state.graphView.root = 'SYN-A';
  f.env.renderGraph();
  const camera = [...f.matrix];
  f.intercept(path => path.startsWith('/api/graph') ? response({
    tracked_ids: [...f.tracked.get('1:0')],
    nodes: [{account:'SYN-A'}, {account:'SYN-B'}, ...(f.tracked.get('1:0').includes(2) ? [{account:'SYN-C'}] : [])],
    edges: f.tracked.get('1:0').includes(2) ? [{source:'SYN-A',target:'SYN-C',ids:[2]}] : [],
  }) : null);
  assert.equal((await f.membership.set(['2'], true)).status, 'confirmed');
  assert.deepEqual(f.models.map(node => node.id), ['SYN-A', 'SYN-C']);
  assert.deepEqual(f.matrix, camera);
  await f.h.element('#btnUndo').onclick();
  assert.deepEqual([...f.state.graphView.visibleIds], ['SYN-A']);
  await f.h.element('#btnRedo').onclick();
  assert.deepEqual([...f.state.graphView.visibleIds], ['SYN-A', 'SYN-C']);
});

test('failed graph publication rolls back the neighborhood along with tracked membership', async () => {
  const f = editor();
  f.state.graphView.visibleIds = new Set(['SYN-A']); f.state.graphView.root = 'SYN-A';
  f.env.renderGraph();
  f.intercept(path => path.startsWith('/api/graph') ? response({tracked_ids:[1,2],
    nodes:[{account:'SYN-A'},{account:'SYN-B'},{account:'SYN-C'}],
    edges:[{source:'SYN-A',target:'SYN-C',ids:[2]}]}) : null);
  const render = f.env.renderGraph; let fail = true;
  f.env.renderGraph = () => {
    if (fail) { fail = false; assert.ok(f.state.graphView.visibleIds.has('SYN-C')); throw new Error('synthetic drawing failure'); }
    render();
  };
  assert.equal((await f.membership.set([2], true)).status, 'failed');
  assert.deepEqual(f.tracked.get('1:0'), [1]);
  assert.deepEqual([...f.state.graphView.visibleIds], ['SYN-A']);
  assert.deepEqual(f.models.map(node => node.id), ['SYN-A']);
  assert.equal(f.history.status().count, 0);
});

test('tracking waits for a superseding same-canvas read before confirming or compensating', async () => {
  const f = editor(), first = deferred(), latest = deferred(), started = deferred(); let reads = 0;
  f.intercept(path => {
    if (!path.startsWith('/api/graph')) return null;
    if (++reads === 1) { started.resolve(); return first.promise; }
    return latest.promise;
  });
  const track = f.h.run('trackFlows([2])'); await started.promise;
  const refresh = f.h.run('loadGraph()');
  first.resolve(response({tracked_ids:[1,2],nodes:[{account:'SYN-A'},{account:'SYN-B'}],edges:[]}));
  await flush(); await flush();
  assert.equal(f.h.requests.filter(item => item.path === '/api/track/state').length, 1, 'an obsolete read is not a failed mutation');
  latest.resolve(response({tracked_ids:[1,2],nodes:[{account:'SYN-A'},{account:'SYN-B'}],edges:[]}));
  await Promise.all([track, refresh]);
  assert.deepEqual(f.tracked.get('1:0'), [1,2]); assert.deepEqual([...f.state.tracked], [1,2]);
  assert.equal(f.history.status().count, 1);
});

test('undo revokes older graph reads so they cannot restore the removed membership', async () => {
  const f = editor(); await f.h.run('trackFlows([2])');
  const old = deferred(), started = deferred(); let reads = 0;
  f.intercept(path => {
    if (!path.startsWith('/api/graph') || ++reads > 1) return null;
    started.resolve(); return old.promise;
  });
  const refresh = f.h.run('loadGraph()'); await started.promise;
  await f.h.element('#btnUndo').onclick();
  assert.deepEqual(f.tracked.get('1:0'), [1]);
  old.resolve(response({tracked_ids:[1,2],nodes:[{account:'SYN-A'},{account:'SYN-B'}],edges:[]}));
  await refresh;
  assert.deepEqual([...f.state.tracked], [1]);
  assert.equal(f.history.status().canUndo, false); assert.equal(f.history.status().canRedo, true);
});

test('a superseding read with a drawing failure cannot confirm an unrendered tracking command', async () => {
  const f = editor(), first = deferred(), latest = deferred(), started = deferred(); let reads = 0;
  f.intercept(path => {
    if (!path.startsWith('/api/graph')) return null;
    if (++reads === 1) { started.resolve(); return first.promise; }
    return latest.promise;
  });
  const render = f.env.renderGraph;
  f.env.renderGraph = () => {
    if (f.state.tracked.has(2)) throw new Error('synthetic drawing failure');
    render();
  };
  const track = f.h.run('trackFlows([2])'); await started.promise;
  const refresh = f.h.run('loadGraph()').then(() => null, error => error.message);
  first.resolve(response({tracked_ids:[1,2],nodes:[{account:'SYN-A'},{account:'SYN-B'}],edges:[]}));
  latest.resolve(response({tracked_ids:[1,2],nodes:[{account:'SYN-A'},{account:'SYN-B'}],edges:[]}));
  await Promise.all([track, refresh]);
  assert.deepEqual(f.tracked.get('1:0'), [1]); assert.deepEqual([...f.state.tracked], [1]);
  assert.equal(f.history.status().count, 0);
  assert.ok(f.messages.some(value => /synthetic drawing failure/.test(value.message)));
});

test('membership rapid in-flight reversals serialize latest intent and retain other pending rows', async () => {
  const f = editor(), started = deferred(), gate = deferred(), flights = [];
  f.intercept((path, options) => {
    if (path !== '/api/track/state') return null;
    const body = JSON.parse(options.body); flights.push(body);
    if (flights.length === 1) {
      started.resolve(); return gate.promise.then(() => {
        f.tracked.set('1:0', body.desired_ids); return response({ ok: true, tracked_ids: body.desired_ids });
      });
    }
    return null;
  });
  const first = f.membership.set(['2'], true); await started.promise;
  const reverse = f.membership.set([2], false), other = f.membership.set(['3'], true);
  const snapshot = f.membership.snapshot();
  assert.deepEqual([...snapshot.confirmedIds], ['1']); assert.deepEqual([...snapshot.desiredIds], ['1', '3']);
  assert.deepEqual([...snapshot.pendingIds], ['2', '3']); assert.equal(snapshot.busy, true);
  assert.equal((await first).status, 'superseded'); assert.equal(flights.length, 1);
  gate.resolve();
  assert.equal((await reverse).status, 'confirmed'); assert.equal((await other).status, 'confirmed');
  assert.deepEqual(f.tracked.get('1:0'), [1, 3]); assert.equal(flights.length, 2);
  assert.deepEqual(flights[1].expected_ids, [1, 2]); assert.deepEqual(flights[1].desired_ids, [1, 3]);
  assert.equal(f.history.status().count, 2); assert.equal(f.membership.snapshot().busy, false);
});

test('membership waits behind another history command rather than dropping a click while busy', async () => {
  const f = editor(), gate = deferred();
  const previous = f.history.run('合成显示操作', () => gate.promise);
  const pending = f.membership.set([2], true); await flush();
  assert.equal(f.history.status().busy, true);
  assert.equal(f.h.requests.filter(item => item.path === '/api/track/state').length, 0);
  gate.resolve(); await previous;
  assert.equal((await pending).status, 'confirmed'); assert.deepEqual(f.tracked.get('1:0'), [1, 2]);
});

test('membership explicit failure rolls back that row, reports failure, and continues unrelated intent', async () => {
  const f = editor(), started = deferred(), gate = deferred(); let first = true;
  f.intercept(path => {
    if (path !== '/api/track/state' || !first) return null;
    first = false; started.resolve(); return gate.promise;
  });
  const failed = f.membership.set([2], true); await started.promise;
  const success = f.membership.set([3], true);
  gate.resolve(response({ detail: 'synthetic row rejection' }, 500));
  const failedResult = await failed;
  assert.equal(failedResult.status, 'failed'); assert.equal(failedResult.ok, false);
  assert.match(failedResult.error, /synthetic row rejection/);
  assert.equal((await success).status, 'confirmed'); assert.deepEqual(f.tracked.get('1:0'), [1, 3]);
  const snapshot = f.membership.snapshot();
  assert.deepEqual([...snapshot.desiredIds], ['1', '3']); assert.match(snapshot.failures['2'], /synthetic row rejection/);
  assert.equal(snapshot.failures['3'], undefined); assert.equal(f.history.status().count, 1);
  assert.equal((await f.membership.set(['2'], true)).status, 'confirmed');
  assert.equal(f.membership.snapshot().failures['2'], undefined); assert.deepEqual(f.tracked.get('1:0'), [1, 2, 3]);
});

test('membership graph failure compensates only its batch and retains earlier success for undo', async () => {
  const f = editor(); assert.equal((await f.membership.set([2], true)).status, 'confirmed');
  f.intercept(path => path.startsWith('/api/graph?') ? response({ detail: 'synthetic graph rejection' }, 500) : null);
  const result = await f.membership.set([3], true);
  assert.equal(result.status, 'failed'); assert.deepEqual(f.tracked.get('1:0'), [1, 2]);
  assert.deepEqual([...f.membership.snapshot().confirmedIds], ['1', '2']);
  assert.equal(f.history.status().count, 1); assert.match(f.membership.snapshot().failures['3'], /synthetic graph rejection/);
  f.intercept(() => null); await f.h.element('#btnUndo').onclick(); assert.deepEqual(f.tracked.get('1:0'), [1]);
});

test('membership lost write receipt revokes its binding instead of confirming a checkbox', async () => {
  const f = editor(), initial = f.membership.snapshot();
  f.intercept(path => {
    if (path !== '/api/track/state') return null;
    f.tracked.set('1:0', [1, 2]); return Promise.reject(new TypeError('synthetic receipt lost'));
  });
  const result = await f.membership.set([2], true), snapshot = f.membership.snapshot();
  assert.equal(result.ok, false); assert.equal(result.status, 'stale');
  assert.notEqual(snapshot.token, initial.token); assert.equal(snapshot.available, false);
  assert.deepEqual([...snapshot.confirmedIds], []); assert.deepEqual([...snapshot.pendingIds], []);
  assert.equal(f.history.status().count, 0); assert.deepEqual(f.tracked.get('1:0'), [1, 2]);
});

test('membership subscribers can close without cancelling sent or unsent accepted work', async () => {
  const f = editor(), started = deferred(), gate = deferred(), snapshots = [];
  const unsubscribe = f.membership.subscribe(value => snapshots.push(value)); let first = true;
  f.intercept((path, options) => {
    if (path !== '/api/track/state' || !first) return null;
    first = false; const body = JSON.parse(options.body); started.resolve();
    return gate.promise.then(() => { f.tracked.set('1:0', body.desired_ids); return response({ ok: true, tracked_ids: body.desired_ids }); });
  });
  const sent = f.membership.set([2], true); await started.promise;
  const queued = f.membership.set([3], true); unsubscribe(); const count = snapshots.length;
  gate.resolve(); assert.equal((await sent).status, 'confirmed'); assert.equal((await queued).status, 'confirmed');
  assert.equal(snapshots.length, count); assert.deepEqual(f.tracked.get('1:0'), [1, 2, 3]);
  const json = JSON.stringify(snapshots);
  assert.doesNotMatch(json, /SYN-A|SYN-B|amount|photo|999999/);
  assert.ok(snapshots.some(value => value.pendingIds.includes('2')));
  assert.equal(Object.isFrozen(snapshots[0].confirmedIds), true);
});

for (const navigation of ['selectCase(2); selectCase(1);', 'activateCanvas(7); activateCanvas(0);']) {
  test(`membership ${navigation} revokes queued intent and awaits the old sent write before its new graph read`, async () => {
    const f = editor(), started = deferred(), gate = deferred(); let first = true;
    f.intercept((path, options) => {
      if (path !== '/api/track/state' || !first) return null;
      first = false; const body = JSON.parse(options.body); started.resolve();
      return gate.promise.then(() => { f.tracked.set('1:0', body.desired_ids); return response({ ok: true, tracked_ids: body.desired_ids }); });
    });
    const sent = f.membership.set([2], true); await started.promise;
    const queued = f.membership.set([3], true), previous = f.membership.snapshot().token;
    f.h.run(navigation); const reloaded = f.h.run('loadGraph(false, caseContext())'); await flush();
    assert.equal((await sent).status, 'stale'); assert.equal((await queued).status, 'stale');
    assert.notEqual(f.membership.snapshot().token, previous);
    assert.equal(f.h.requests.filter(item => item.path.startsWith('/api/graph?')).length, 0);
    gate.resolve(); await reloaded; await flush();
    assert.deepEqual([...f.membership.snapshot().confirmedIds], ['1', '2']);
    assert.deepEqual([...f.membership.snapshot().pendingIds], []); assert.equal(f.history.status().count, 0);
    assert.equal(f.h.requests.filter(item => item.path === '/api/track/state').length, 1);
    assert.equal(f.h.requests.filter(item => item.path.startsWith('/api/graph?')).length, 1);
  });
}

test('membership publishes external graph and history changes and rejects malformed IDs without requests', async () => {
  const f = editor(), snapshots = [], unsubscribe = f.membership.subscribe(value => snapshots.push(value));
  const count = f.h.requests.length;
  for (const ids of [['bad'], ['9007199254740993'], [0], [null], Array(1)]) {
    const result = await f.membership.set(ids, true); assert.equal(result.status, 'failed'); assert.equal(result.ok, false);
  }
  assert.equal(f.h.requests.length, count);
  f.tracked.set('1:0', [1, 2]); await f.h.run('loadGraph(false, caseContext())');
  assert.deepEqual([...snapshots.at(-1).confirmedIds], ['1', '2']);
  await f.membership.set([3], true); const beforeUndo = snapshots.length;
  await f.h.element('#btnUndo').onclick();
  assert.ok(snapshots.length > beforeUndo); assert.deepEqual([...snapshots.at(-1).confirmedIds], ['1', '2']);
  unsubscribe();
});

test('bulk tracking and clear are reversible; cancel clear creates no history', async () => {
  const f = editor(); f.h.element('#bulkDir').value = 'source'; f.h.element('#bulkX').value = '5';
  await f.h.element('#btnBulkTrack').onclick(); assert.deepEqual(f.tracked.get('1:0'), [1, 2]);
  await f.h.element('#btnClearGraph').onclick(); assert.deepEqual(f.tracked.get('1:0'), []);
  await f.h.element('#btnUndo').onclick(); assert.deepEqual(f.tracked.get('1:0'), [1, 2]);
  await f.h.element('#btnUndo').onclick(); assert.deepEqual(f.tracked.get('1:0'), [1]);
  const count = f.history.status().count; f.env.confirm = () => false;
  await f.h.element('#btnClearGraph').onclick(); assert.equal(f.history.status().count, count); assert.equal(f.history.status().canRedo, true);
});

test('bulk success feedback retains anchors, direction, skipped accounts and zero expansion details', async () => {
  for (const [direction, label, party] of [['source', '来源', '付款'], ['target', '去向', '收款']]) {
    const f = editor(); f.h.element('#bulkDir').value = direction;
    f.intercept(path => {
      if (path !== '/api/track/bulk') return null;
      f.tracked.set('1:0', [1, 2]);
      return response({ ok: true, anchors: 2, tracked: 2, tracked_ids: [1, 2], skipped_not_main: [{ id: 1 }],
        detail: [{ is_main: true, picked: 0 }, { is_main: false, picked: 0 }] });
    });
    assert.equal(await f.h.element('#btnBulkTrack').onclick(), true);
    assert.ok(f.messages.some(item => item.message === `批量追踪完成：2 笔交易及其${label}流水共上图 2 笔`
      + `；1 笔被跳过（${party}方不是主账号，无法追踪）`
      + `；1 笔锚点在本方账单中无${label}流水（仅上图了本笔）`));
  }
});

test('restore conflict keeps the stack and current display unchanged', async () => {
  const f = editor(); await f.h.run('trackFlows([2])');
  f.conflict(true); const before = JSON.stringify(f.snapshot());
  assert.equal(await f.h.element('#btnUndo').onclick(), false);
  assert.equal(f.history.status().canUndo, true); assert.equal(f.history.status().canRedo, false);
  assert.equal(JSON.stringify(f.snapshot()), before); assert.deepEqual(f.tracked.get('1:0'), [1, 2]);
});

test('failed graph fetch compensates the restored tracked set without moving history', async () => {
  const f = editor(); await f.h.run('trackFlows([2])');
  const before = JSON.stringify(f.snapshot());
  f.intercept(path => path.startsWith('/api/graph?') ? response({ detail: 'synthetic graph read failure' }, 500) : null);
  assert.equal(await f.h.element('#btnUndo').onclick(), false);
  assert.deepEqual(f.tracked.get('1:0'), [1, 2]); assert.equal(f.history.status().canUndo, true);
  assert.equal(JSON.stringify(f.snapshot()), before);
});

test('a lost restore write receipt invalidates history and revokes the unconfirmed graph', async () => {
  const f = editor(); await f.h.run('trackFlows([2])');
  f.intercept((path, options) => {
    if (path !== '/api/track/state') return null;
    f.tracked.set('1:0', JSON.parse(options.body).desired_ids);
    return Promise.reject(new TypeError('synthetic restore receipt lost'));
  });
  assert.equal(await f.h.element('#btnUndo').onclick(), false);
  assert.deepEqual(f.tracked.get('1:0'), [1]); assert.equal(f.history.status().count, 0);
  assert.equal(f.state.graphLoading, true); assert.equal(f.state.tracked.size, 0); assert.equal(f.models.length, 0);
  assert.ok(f.messages.some(item => item.message.includes('状态无法确认')));
});

test('a concurrent write between restore CAS and graph read invalidates history without compensating it away', async () => {
  const f = editor(); await f.h.run('trackFlows([2])');
  const writes = f.h.requests.filter(item => item.path === '/api/track/state').length;
  f.intercept(path => {
    if (path.startsWith('/api/graph?')) f.tracked.set('1:0', [1, 99]);
    return null;
  });
  assert.equal(await f.h.element('#btnUndo').onclick(), false);
  assert.deepEqual(f.tracked.get('1:0'), [1, 99]);
  assert.equal(f.h.requests.filter(item => item.path === '/api/track/state').length, writes + 1);
  assert.equal(f.history.status().count, 0); assert.equal(f.state.graphLoading, true);
  assert.equal(f.state.tracked.size, 0); assert.equal(f.models.length, 0);
  assert.ok(f.messages.some(item => item.message.includes('状态无法确认')));
});

test('restore render failure revokes partial facts and history without another tracked-set write', async () => {
  const f = editor(); await f.h.run('trackFlows([2])');
  const writes = f.h.requests.filter(item => item.path === '/api/track/state').length;
  f.env.renderGraph = () => { throw new Error('synthetic render failure'); };
  assert.equal(await f.h.element('#btnUndo').onclick(), false);
  assert.deepEqual(f.tracked.get('1:0'), [1]);
  assert.equal(f.h.requests.filter(item => item.path === '/api/track/state').length, writes + 1);
  assert.equal(f.history.status().count, 0); assert.equal(f.history.status().canUndo, false); assert.equal(f.history.status().canRedo, false);
  assert.equal(f.state.graphLoading, true); assert.equal(f.state.tracked.size, 0);
  assert.equal(f.state.lastGraph.nodes.length, 0); assert.equal(f.models.length, 0);
  assert.ok(f.messages.some(item => item.message.includes('状态无法确认') && item.message.includes('重新加载')));
  const requests = f.h.requests.length;
  assert.equal(await f.h.element('#btnUndo').onclick(), false); assert.equal(f.h.requests.length, requests);
});

for (const operation of ['track', 'untrack', 'bulk', 'clear']) {
  test(`${operation} compensates a committed write after both graph refresh attempts fail`, async () => {
    const f = editor(); f.h.element('#showPhotoCk').checked = false;
    await f.h.element('#showPhotoCk').onchange({ target: f.h.element('#showPhotoCk') });
    f.state.edgeTypes = { 'SYN-A=>SYN-B': 'polyline' };
    f.state.edgeBendPos = { 'SYN-A=>SYN-B': [{ x: 7, y: 8 }] };
    const before = JSON.stringify(f.snapshot()), historyCount = f.history.status().count;
    let reads = 0;
    f.intercept(path => {
      if (path.startsWith('/api/graph?')) { reads += 1; return response({ detail: 'synthetic graph failure' }, 500); }
      return null;
    });
    const result = operation === 'track' ? await f.h.run('trackFlows([2])')
      : operation === 'untrack' ? await f.h.run('untrackFlows([1])')
      : await f.h.element(operation === 'bulk' ? '#btnBulkTrack' : '#btnClearGraph').onclick();
    assert.equal(result, false); assert.equal(reads, 2);
    assert.deepEqual(f.tracked.get('1:0'), [1]); assert.equal(JSON.stringify(f.snapshot()), before);
    assert.equal(f.state.edgeTypes['SYN-A=>SYN-B'], 'polyline');
    assert.equal(f.history.status().count, historyCount); assert.equal(f.history.status().busy, false);
    assert.equal(f.state.graphLoading, false);
    const writes = f.h.requests.filter(request => request.path === '/api/track/state').map(request => JSON.parse(request.options.body));
    const rollback = writes.at(-1);
    assert.deepEqual(rollback.desired_ids, [1]);
    assert.deepEqual(rollback.expected_ids, operation === 'untrack' || operation === 'clear' ? [] : [1, 2]);
  });
}

test('compensation conflict preserves concurrent tracking and revokes unconfirmed facts and history', async () => {
  const f = editor(); f.h.element('#showPhotoCk').checked = false;
  await f.h.element('#showPhotoCk').onchange({ target: f.h.element('#showPhotoCk') });
  f.intercept(path => {
    if (path.startsWith('/api/graph?')) {
      f.tracked.set('1:0', [1, 2, 99]);
      return response({ detail: 'synthetic graph failure' }, 500);
    }
    return null;
  });
  assert.equal(await f.h.run('trackFlows([2])'), false);
  assert.deepEqual(f.tracked.get('1:0'), [1, 2, 99]);
  assert.equal(f.history.status().count, 0); assert.equal(f.state.tracked.size, 0);
  assert.equal(f.state.lastGraph.nodes.length, 0); assert.equal(f.models.length, 0); assert.equal(f.state.graphLoading, true);
  assert.ok(f.messages.some(item => item.message.includes('状态无法确认') && item.message.includes('重新加载')));
});

test('bulk write-time conflict preserves history and never overwrites pre-existing concurrent tracking', async () => {
  const f = editor(); f.h.element('#showPhotoCk').checked = false;
  await f.h.element('#showPhotoCk').onchange({ target: f.h.element('#showPhotoCk') });
  const before = JSON.stringify(f.snapshot()), count = f.history.status().count;
  f.tracked.set('1:0', [1, 99]);
  assert.equal(await f.h.element('#btnBulkTrack').onclick(), false);
  const request = f.h.requests.findLast(item => item.path === '/api/track/bulk');
  assert.deepEqual(JSON.parse(request.options.body).expected_ids, [1]);
  assert.deepEqual(f.tracked.get('1:0'), [1, 99]); assert.equal(JSON.stringify(f.snapshot()), before);
  assert.equal(f.history.status().count, count); assert.equal(f.history.status().busy, false);
  assert.equal(f.h.requests.filter(item => item.path === '/api/track/state').length, 0);
});

test('a lost write receipt revokes the old graph instead of presenting its state as confirmed', async () => {
  const f = editor(); f.h.element('#showPhotoCk').checked = false;
  await f.h.element('#showPhotoCk').onchange({ target: f.h.element('#showPhotoCk') });
  f.intercept(path => {
    if (path !== '/api/track/state') return null;
    f.tracked.set('1:0', [1, 2]); return Promise.reject(new TypeError('synthetic fetch receipt lost'));
  });
  assert.equal(await f.h.run('trackFlows([2])'), false);
  assert.deepEqual(f.tracked.get('1:0'), [1, 2]); assert.equal(f.state.tracked.size, 0);
  assert.equal(f.state.graphLoading, true); assert.equal(f.history.status().count, 0);
  assert.ok(f.messages.some(item => item.message.includes('状态无法确认')));
});

test('late compensation completion cannot restore old facts or geometry into a new binding', async () => {
  const f = editor(), started = deferred(), gate = deferred();
  let writes = 0;
  f.intercept((path, options) => {
    if (path.startsWith('/api/graph?') && path.includes('case_id=1')) return response({ detail: 'synthetic graph failure' }, 500);
    if (path === '/api/track/state' && ++writes === 2) {
      assert.deepEqual(JSON.parse(options.body).expected_ids, [1, 2]); started.resolve(); return gate.promise;
    }
    return null;
  });
  const pending = f.h.run('trackFlows([2])'); await started.promise;
  f.h.run('selectCase(2); state.graphLoading = false;'); await f.h.run('loadGraph(false, caseContext())');
  const before = JSON.stringify(f.snapshot());
  f.tracked.set('1:0', [1]); gate.resolve(response({ ok: true, tracked_ids: [1] })); await pending;
  assert.equal(JSON.stringify(f.snapshot()), before); assert.equal(f.state.caseId, 2);
  assert.deepEqual([...f.state.tracked], [90]); assert.equal(f.history.status().count, 0);
});

test('a concurrent tracked-set change observed by graph refresh is never admitted as this command', async () => {
  const f = editor();
  f.intercept(path => {
    if (path.startsWith('/api/graph?')) f.tracked.set('1:0', [1, 2, 99]);
    return null;
  });
  assert.equal(await f.h.run('trackFlows([2])'), false);
  assert.deepEqual(f.tracked.get('1:0'), [1, 2, 99]);
  assert.equal(f.history.status().count, 0); assert.equal(f.state.graphLoading, true); assert.equal(f.state.tracked.size, 0);
});

test('a layout timeout after graph publication compensates tracking and redraws the original geometry', async () => {
  const f = editor(), render = f.env.renderGraph;
  let first = true;
  f.h.run('globalThis.syntheticNow = 0; Date.now = () => syntheticNow;');
  f.env.renderGraph = () => {
    render();
    if (first) { first = false; f.models[0].x = 777; f.h.run('layoutJob = { requestId: 9 }'); }
  };
  const before = JSON.stringify(f.snapshot());
  const pending = f.h.run('trackFlows([2])'); await flush();
  assert.equal(f.history.status().busy, true);
  f.h.run('syntheticNow = 30001; graphMetric("layout")');
  assert.equal(await pending, false);
  assert.deepEqual(f.tracked.get('1:0'), [1]); assert.equal(JSON.stringify(f.snapshot()), before);
  assert.equal(f.models[0].x, 3); assert.equal(f.history.status().count, 0); assert.equal(f.state.graphLoading, false);
});

for (const phase of ['before', 'during']) {
  test(`a graph read started ${phase} compensation cannot republish reverted membership`, async () => {
    const f = editor(), render = f.env.renderGraph, late = deferred(), readStarted = deferred();
    const rollback = deferred(), rollbackStarted = deferred(); let writes = 0, reads = 0, first = true;
    f.h.run('globalThis.syntheticNow = 0; Date.now = () => syntheticNow;');
    f.env.renderGraph = () => {
      render(); if (first) { first = false; f.h.run('layoutJob = {requestId:9}'); }
    };
    f.intercept((path, options) => {
      if (path === '/api/track/state' && ++writes === 2 && phase === 'during') {
        rollbackStarted.resolve(); return rollback.promise.then(() => {
          f.tracked.set('1:0', [1]); return response({tracked_ids:[1]});
        });
      }
      if (path.startsWith('/api/graph') && ++reads > 1) { readStarted.resolve(); return late.promise; }
      return null;
    });
    const pending = f.h.run('trackFlows([2])'); await flush();
    let refresh;
    if (phase === 'before') { refresh = f.h.run('loadGraph()'); await readStarted.promise; }
    f.h.run('syntheticNow = 30001; graphMetric("layout")');
    if (phase === 'during') {
      await rollbackStarted.promise; refresh = f.h.run('loadGraph()'); await readStarted.promise; rollback.resolve();
    }
    assert.equal(await pending, false);
    late.resolve(response({tracked_ids:[1,2],nodes:[{account:'SYN-A'},{account:'SYN-B'}],edges:[]}));
    await refresh;
    assert.deepEqual(f.tracked.get('1:0'), [1]); assert.deepEqual([...f.state.tracked], [1]);
    assert.equal(f.history.status().count, 0);
  });
}

test('late tracking responses cannot load or record into another case or same-case generation', async () => {
  const f = editor(), gate = deferred();
  f.intercept(path => path === '/api/track/state' ? gate.promise : null);
  const pending = f.h.run('trackFlows([2])'); await flush();
  f.h.run('selectCase(2); selectCase(1); state.graphLoading = false;');
  const requests = f.h.requests.length; gate.resolve(response({ ok: true })); await pending;
  assert.equal(f.h.requests.length, requests); assert.equal(f.history.status().count, 0); assert.equal(f.state.tracked.size, 0);
  f.h.run('activateCanvas(7)'); assert.equal(f.history.status().count, 0);
});

for (const layout of ['dagre', 'auto', 'partition']) test(`layout ${layout} remains busy until the existing worker job completes and survives undo and redo`, async () => {
  const f = editor();
  const initialLayout = f.state.layout;
  f.env.relayout = () => { f.h.run('layoutJob = { requestId: 1 }'); return Promise.resolve(); };
  f.h.element('#layoutSel').value = layout;
  const change = f.h.element('#layoutSel').onchange({ target: f.h.element('#layoutSel') }); await flush();
  assert.equal(f.history.status().busy, true); assert.equal(f.history.status().count, 0);
  f.models[0].x = 100; f.state.positions['SYN-A'].x = 100;
  f.h.run('layoutJob = null; graphMetric("layout")'); await change;
  assert.equal(f.history.status().busy, false); assert.equal(f.history.status().count, 1);
  await f.h.element('#btnUndo').onclick(); assert.equal(f.models[0].x, 3); assert.equal(f.state.layout, initialLayout);
  await f.h.element('#btnRedo').onclick(); assert.equal(f.models[0].x, 100); assert.equal(f.state.layout, layout);
  assert.equal(f.h.element('#layoutSel').value, layout, 'redo synchronizes the existing layout control');
});

test('display handlers restore original choices and command failures do not add history', async () => {
  const f = editor({ relationships: [{ source: 'SYN-A', target: 'SYN-B' }] }); f.h.element('#showPhotoCk').checked = false;
  const draw = f.env.renderGraph;
  // The production renderer synchronizes the type control during the busy
  // command. Its completion must synchronize it again before another edit.
  f.env.renderGraph = () => { draw(); f.h.run('syncEdgeTypeControl()'); };
  await f.h.element('#showPhotoCk').onchange({ target: f.h.element('#showPhotoCk') });
  assert.equal(f.state.showPhotos, false); await f.h.element('#btnUndo').onclick(); assert.equal(f.state.showPhotos, true);
  const defaultType = f.state.edgeType;
  f.h.run('selectItem(state.graph.getEdges()[0])');
  f.h.element('#edgeTypeSel').value = 'line'; await f.h.element('#edgeTypeSel').onchange({ target: f.h.element('#edgeTypeSel') });
  assert.equal(f.state.edgeTypes['SYN-A=>SYN-B'], 'line'); assert.equal(f.state.edgeType, defaultType);
  assert.equal(f.history.status().count, 1); assert.equal(f.history.status().undoLabel, '修改所选关系线型');
  assert.equal(f.history.status().canRedo, false);
  assert.equal(f.h.element('#edgeTypeSel').disabled, false);
  f.h.element('#edgeTypeSel').value = 'polyline'; await f.h.element('#edgeTypeSel').onchange({ target: f.h.element('#edgeTypeSel') });
  assert.equal(f.state.edgeTypes['SYN-A=>SYN-B'], 'polyline'); assert.equal(f.state.edgeType, defaultType);
  assert.equal(f.history.status().count, 2); assert.equal(f.h.element('#edgeTypeSel').disabled, false);
  const count = f.history.status().count;
  f.intercept(path => path === '/api/track/state' ? response({ detail: 'synthetic failure' }, 500) : null);
  await f.h.run('trackFlows([2])'); assert.equal(f.history.status().count, count); assert.equal(f.history.status().busy, false);
});

test('mouse gestures record geometry, exclude camera-only movement and capture double-click bend removal', async () => {
  const f = editor(); f.gesture('mousedown', { button: 0 });
  f.matrix = [1, 0, 0, 0, 1, 0, 100, 100, 1]; f.event('mouseup'); await flush();
  assert.equal(f.history.status().count, 0);
  f.gesture('mousedown', { button: 0 }); f.models[0].x = 80; f.event('mouseup'); await flush();
  assert.equal(f.history.status().count, 1); await f.h.element('#btnUndo').onclick(); assert.equal(f.models[0].x, 3);
  f.state.edgeBendPos = { 'SYN-A=>SYN-B': [{ x: 7, y: 8 }] };
  f.gesture('dblclick'); delete f.state.edgeBendPos['SYN-A=>SYN-B']; await flush();
  assert.equal(f.history.status().undoLabel, '调整连线折点');
  await f.h.element('#btnUndo').onclick(); assert.equal(f.state.edgeBendPos['SYN-A=>SYN-B'][0].x, 7);
});

test('group preview commits after mouseup and records exactly one geometry undo', async () => {
  const f = editor(); let dragging = true;
  f.state.graphSelection = { status: () => ({ dragging }) };
  f.event('funds:selection-drag-start');
  f.event('mouseup'); await flush();
  assert.equal(f.history.status().count, 0, 'preview has not committed its geometry yet');
  f.models[0].x += 40; f.models[1].x += 40;
  dragging = false; f.event('funds:selection-drag-end');
  assert.equal(f.history.status().count, 1);
  assert.equal(f.history.status().undoLabel, '移动选中节点');
  f.event('mouseup'); await flush();
  assert.equal(f.history.status().count, 1, 'the later native event cannot add another history entry');
});

test('selection capture blocks history and commit gestures while computing preserves native camera starts', async () => {
  const f = editor(); f.state.graphSelection = { status: () => ({ dragging: false }) };
  const gate = deferred(), busy = f.history.run('合成等待', () => gate.promise);
  let prevented = 0, stopped = 0;
  const attempt = () => f.gesture('mousedown', { type: 'mousedown', button: 0,
    preventDefault() { prevented += 1; }, stopImmediatePropagation() { stopped += 1; } });
  attempt();
  assert.equal(prevented, 1); assert.equal(stopped, 1);
  gate.resolve(); await busy;
  f.h.run('layoutJob = { requestId: 1, phase: "committing" }'); attempt();
  assert.equal(prevented, 2); assert.equal(stopped, 2);
  f.h.run('layoutJob = { requestId: 1, phase: "computing" }'); attempt();
  assert.equal(prevented, 2, 'the capture listener must let native canvas pan reach G6 while a worker computes');
  assert.equal(stopped, 2);
  for (const type of ['funds:selection-drag-start', 'funds:edge-drag-start']) {
    let cancelled = false;
    f.event(type, { type, preventDefault() { cancelled = true; }, stopImmediatePropagation() {} });
    assert.equal(cancelled, true, 'geometry ownership remains locked during computation');
  }
  f.h.run('layoutJob = null'); attempt();
  assert.equal(prevented, 2, 'an idle selection click bypasses the geometry snapshot');
});

test('a pending layout command separates computing camera permission from atomic commit and edit history', async () => {
  const f = editor(), before = f.snapshot();
  f.state.graphSelection = { ids: new Set(), status: () => ({ dragging: false }), rebind() {}, refresh() {}, clear() {}, destroy() {} };
  f.env.relayout = () => { f.h.run('layoutJob = { requestId: 7, phase: "computing" }; graphMetric("layout-phase")'); return Promise.resolve(); };
  const pending = f.h.element('#btnRelayout').onclick(); await flush();
  assert.equal(f.history.status().busy, true); assert.equal(f.history.status().count, 0);
  assert.equal(f.h.element('#graphContainer').style.pointerEvents, '', 'the old complete canvas receives camera events during calculation');
  assert.equal(f.h.run('graphCameraInteractionAllowed()'), true);
  let prevented = 0;
  f.gesture('mousedown', { type: 'mousedown', button: 0, preventDefault() { prevented++; }, stopImmediatePropagation() {} });
  assert.equal(prevented, 0, 'native camera gestures bypass geometry history capture during calculation');
  assert.equal(await f.h.element('#btnUndo').onclick(), false, 'history cannot travel through an unfinished layout transaction');
  assert.equal(f.h.element('#btnBulkTrack').disabled, true, 'membership controls cannot change layout input while its transaction owns the graph');
  assert.deepEqual(f.tracked.get('1:0'), [1]); assert.equal(f.history.status().count, 0);
  f.h.run('layoutJob.phase = "committing"; graphMetric("layout-phase")');
  assert.equal(f.h.run('graphCameraInteractionAllowed()'), false);
  assert.equal(f.h.element('#graphContainer').style.pointerEvents, 'none');
  f.gesture('mousedown', { type: 'mousedown', button: 0, preventDefault() { prevented++; }, stopImmediatePropagation() {} });
  assert.equal(prevented, 1, 'the short atomic commit cannot receive native geometry or camera gestures');
  f.models[0].x = 120; f.state.positions['SYN-A'].x = 120;
  f.h.run('layoutJob = null; graphMetric("layout")'); await pending;
  assert.equal(f.history.status().busy, false); assert.equal(f.history.status().count, 1);
  assert.equal(f.h.element('#graphContainer').style.pointerEvents, '');
  const undone=await f.h.element('#btnUndo').onclick();assert.notEqual(undone,false,JSON.stringify(f.messages));
  assert.deepEqual(f.snapshot().positions, before.positions);
  await f.h.element('#btnRedo').onclick(); assert.equal(f.models[0].x, 120);
});

test('custom graph gesture cancellation receives the original event during a busy command', async () => {
  const f = editor(), gate = deferred(), busy = f.history.run('合成等待', () => gate.promise);
  for (const type of ['funds:selection-drag-start', 'funds:edge-drag-start']) {
    let cancelled = false;
    assert.doesNotThrow(() => f.event(type, { type, preventDefault() { cancelled = true; }, stopImmediatePropagation() {} }));
    assert.equal(cancelled, true);
  }
  gate.resolve(); await busy;
});

test('keyboard shortcuts skip native edit controls and active dialogs', async () => {
  const f = editor(); await f.h.run('trackFlows([2])');
  let prevented = 0;
  f.event('keydown', { ctrlKey: true, key: 'z', target: { closest: () => ({}) }, preventDefault() { prevented += 1; } });
  await flush(); assert.equal(prevented, 0); assert.equal(f.history.status().canUndo, true);
  const queryAll = f.env.document.querySelectorAll;
  f.env.document.querySelectorAll = selector => selector.includes('.modal')
    ? [{ open: false, hidden: false, style: { display: 'flex' }, getClientRects: () => [1] }] : queryAll(selector);
  f.event('keydown', { ctrlKey: true, key: 'z', target: { closest: () => null }, preventDefault() { prevented += 1; } });
  await flush(); assert.equal(prevented, 0); assert.equal(f.history.status().canUndo, true);
  f.env.document.querySelectorAll = queryAll;
  f.event('keydown', { metaKey: true, key: 'z', target: { closest: () => null }, preventDefault() { prevented += 1; } });
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(prevented, 1); assert.equal(f.history.status().canRedo, true);
  f.event('keydown', { ctrlKey: true, key: 'y', target: { closest: () => null }, preventDefault() { prevented += 1; } });
  await new Promise(resolve => setTimeout(resolve, 5)); assert.equal(f.history.status().canUndo, true);
});

test('keyboard undo does not require the graph container to hold focus', async () => {
  const f = editor(); await f.h.run('trackFlows([2])');
  // 选择控制器存在，但焦点在图谱之外（刚点过功能区／侧栏后的情形）。
  f.state.graphSelection = { ids: new Set(), status: () => ({ dragging: false }), rebind() {}, refresh() {}, clear() {}, destroy() {} };
  f.env.document.activeElement = { tagName: 'BUTTON' };
  let prevented = 0;
  f.event('keydown', { ctrlKey: true, key: 'z', target: { closest: () => null }, preventDefault() { prevented += 1; } });
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(prevented, 1, 'Ctrl+Z must still undo when focus left the graph');
  assert.equal(f.history.status().canRedo, true);
});

for (const page of ['cases', 'accounts', 'analysis']) test(`history shortcuts and buttons ignore the hidden home canvas on ${page} and resume after returning`, async () => {
  const f = editor({ navigation: true }), checkbox = f.h.element('#showIdCk');
  checkbox.checked = false; await checkbox.onchange();
  assert.equal(f.state.showIds, false); assert.equal(f.history.status().canUndo, true);
  let prevented = 0;
  const shortcut = async keys => {
    f.event('keydown', { target: f.env.document.activeElement, preventDefault() { prevented++; }, ...keys });
    await flush();
  };
  f.nav.get(page).onclick(); f.env.document.activeElement = f.nav.get(page);
  assert.equal(f.h.element('#graphPage').inert, true);
  for (const keys of [{ ctrlKey: true, key: 'z' }, { metaKey: true, key: 'z' }]) await shortcut(keys);
  assert.equal(f.state.showIds, false, 'a parent-page rail shortcut must not undo the hidden home display');
  assert.equal(prevented, 0, 'other pages retain their native shortcut behavior');
  assert.equal(await f.h.element('#btnUndo').onclick(), false, 'a direct hidden undo button callback cannot bypass the page boundary');
  assert.equal(f.history.status().canUndo, true);

  f.nav.get('home').onclick(); f.env.document.activeElement = f.h.element('#btnFit');
  await shortcut({ ctrlKey: true, key: 'z' });
  assert.equal(f.state.showIds, true, 'returning home permits undo with Ribbon focus');
  assert.equal(f.history.status().canRedo, true);
  f.nav.get(page).onclick(); f.env.document.activeElement = f.nav.get(page);
  for (const keys of [{ ctrlKey: true, key: 'y' }, { metaKey: true, shiftKey: true, key: 'z' }]) await shortcut(keys);
  assert.equal(f.state.showIds, true, 'a parent-page rail shortcut must not redo the hidden home display');
  assert.equal(prevented, 1);
  assert.equal(await f.h.element('#btnRedo').onclick(), false, 'a direct hidden redo button callback cannot bypass the page boundary');
  assert.equal(f.history.status().canRedo, true);

  f.nav.get('home').onclick(); f.env.document.activeElement = f.h.element('#btnSearch');
  await shortcut({ metaKey: true, shiftKey: true, key: 'z' });
  assert.equal(f.state.showIds, false, 'returning home permits redo with query-sidebar focus');
  assert.equal(f.history.status().canUndo, true); assert.equal(prevented, 2);
});

test('selected-edge type undo and redo retain other overrides, the default and source facts', async () => {
  const f=editor({relationships:[{source:'SYN-A',target:'SYN-B'},{source:'SYN-B',target:'SYN-A'}]});
  const draw=f.env.renderGraph;f.env.renderGraph=()=>{draw();f.h.run('syncEdgeTypeControl()');};
  f.state.edgeTypes={'SYN-A=>SYN-B':'line','SYN-B=>SYN-A':'polyline'};
  f.env.renderGraph();
  const defaultType=f.state.edgeType,sourceBefore=JSON.stringify(f.state.lastGraph),camera=[...f.matrix];
  f.h.run('selectItem(state.graph.getEdges()[0])');
  assert.equal(f.h.element('#edgeTypeSel').value,'line');
  f.h.element('#edgeTypeSel').value='quadratic';await f.h.element('#edgeTypeSel').onchange({target:f.h.element('#edgeTypeSel')});
  assert.equal(f.state.edgeTypes['SYN-A=>SYN-B'],'quadratic');assert.equal(f.state.edgeTypes['SYN-B=>SYN-A'],'polyline');
  assert.equal(f.state.edgeType,defaultType);assert.equal(f.history.status().count,1);
  assert.equal(f.h.element('#edgeTypeSel').value,'quadratic');
  assert.equal(f.h.element('#edgeTypeSel').disabled,false);
  await f.h.element('#btnUndo').onclick();
  assert.equal(f.state.edgeTypes['SYN-A=>SYN-B'],'line');assert.equal(f.state.edgeTypes['SYN-B=>SYN-A'],'polyline');
  assert.equal(f.state.edgeType,defaultType);assert.equal(f.h.element('#edgeTypeSel').disabled,true);
  f.h.run('selectItem(state.graph.getEdges()[0])');
  assert.equal(f.h.element('#edgeTypeSel').value,'line');
  await f.h.element('#btnRedo').onclick();
  assert.equal(f.state.edgeTypes['SYN-A=>SYN-B'],'quadratic');assert.equal(f.state.edgeTypes['SYN-B=>SYN-A'],'polyline');
  assert.equal(f.state.edgeType,defaultType);assert.equal(f.h.element('#edgeTypeSel').disabled,true);
  f.h.run('selectItem(state.graph.getEdges()[0])');assert.equal(f.h.element('#edgeTypeSel').value,'quadratic');
  assert.equal(f.h.element('#edgeTypeSel').disabled,false);
  f.h.element('#edgeTypeSel').value='line';await f.h.element('#edgeTypeSel').onchange({target:f.h.element('#edgeTypeSel')});
  assert.equal(f.state.edgeTypes['SYN-A=>SYN-B'],'line');assert.equal(f.state.edgeTypes['SYN-B=>SYN-A'],'polyline');
  assert.equal(f.state.edgeType,defaultType);assert.equal(f.history.status().count,2);
  assert.equal(f.h.element('#edgeTypeSel').disabled,false);assert.equal(f.h.element('#edgeTypeSel').value,'line');
  f.h.run('selectItem(state.graph.getEdges()[1])');assert.equal(f.h.element('#edgeTypeSel').value,'polyline');
  assert.deepEqual(f.matrix,camera);assert.equal(JSON.stringify(f.state.lastGraph),sourceBefore);
  assert.doesNotMatch(JSON.stringify(f.snapshot()),/"(?:amount|photo|total|amount_totals)"|999999/);
});

test('clear canvas removes restored edge types and undo restores their geometry', async () => {
  const f=editor();f.state.edgeTypes={'SYN-A=>SYN-B':'polyline'};f.state.edgeBendPos={'SYN-A=>SYN-B':[{x:7,y:8}]};
  const automaticRoute={source:{x:3,y:4},target:{x:5,y:6},controlPoints:[{x:17,y:18}],curvePolicy:'obstacle-aware'};
  f.state.graphView.autoRoutes={'SYN-B=>SYN-A':automaticRoute};
  await f.h.element('#btnClearGraph').onclick();assert.deepEqual(Object.keys(f.state.edgeTypes),[]);
  assert.deepEqual(Object.keys(f.state.graphView.autoRoutes),[]);
  await f.h.element('#btnUndo').onclick();assert.equal(f.state.edgeTypes['SYN-A=>SYN-B'],'polyline');
  assert.deepEqual(JSON.parse(JSON.stringify(f.state.edgeBendPos['SYN-A=>SYN-B'])),[{x:7,y:8}]);
  assert.deepEqual(JSON.parse(JSON.stringify(f.state.graphView.autoRoutes)),{'SYN-B=>SYN-A':automaticRoute});
  await f.h.element('#btnRedo').onclick();assert.deepEqual(Object.keys(f.state.edgeTypes),[]);
  assert.deepEqual(Object.keys(f.state.graphView.autoRoutes),[]);
});

function annotations() {
  const f = editor(), shapes = [], container = f.h.element('#graphContainer');
  let rebuilt = 0;
  f.env.renderNodeAnnotations = () => {};
  f.env.rebuildGraphViewport = () => { rebuilt += 1; };
  f.env.scheduleGraphLabels = () => {};
  f.state.graph.findById = id => f.models.find(model => model.id === id) ? {
    getModel: () => ({ ...f.models.find(model => model.id === id), size: 32, fundsLabel: '合成账户' }),
    getContainer: () => ({ get: () => shapes }),
  } : null;
  f.state.graph.getPointByCanvas = (x, y) => ({ x, y });
  f.state.graph.getZoom = () => 1;
  f.state.graph.getCanvasByPoint = (x, y) => ({ x, y });
  f.state.graphSelection = { ids: new Set(['SYN-A']), status: () => ({ dragging: false }), refresh() {}, clear() {}, destroy() {}, rebind() {} };
  const dialog = f.h.element('#annotationDialog');
  dialog.showModal = () => { dialog.open = true; };
  dialog.querySelectorAll = () => [{ value: 'below', checked: true }];
  container.setPointerCapture = () => {};
  container.releasePointerCapture = () => {};
  container.removeEventListener = () => {};
  f.env.installAnnotationDrag(container);
  return { ...f, dialog, get rebuilt() { return rebuilt; },
    save(text) { f.env.openAnnotationEditor(); f.h.element('#annotationText').value = text; return f.env.saveAnnotationEditor(); },
    pointer(type, x = 3, y = 64, pointerId = 7) { f.gesture(type, { type, button: 0, pointerId, clientX: x, clientY: y,
      preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() {} }); } };
}

test('annotations: add edit delete and undo share the existing display history without losing earlier annotations', async () => {
  const f = annotations();
  await f.save('第一行\n第二行');
  assert.equal(f.history.status().count, 1);
  assert.ok(f.rebuilt > 0, 'saving must refresh the annotation bounds');
  assert.equal(f.state.presentation.annotations[0].text, '第一行\n第二行');
  await f.env.window.FundsCanvasEditor.command('合成移动节点', () => { f.state.positions['SYN-A'].x = 30; f.models[0].x = 30; });
  await f.save('修改后的标注');
  await f.history.undo();
  assert.equal(f.state.presentation.annotations[0].text, '第一行\n第二行');
  await f.history.undo();
  assert.equal(f.state.presentation.annotations[0].text, '第一行\n第二行');
  f.state.graphSelection = { ids: new Set(['SYN-A']), status: () => ({}), clear() {}, destroy() {} };
  f.env.openAnnotationEditor(); await f.env.deleteAnnotation();
  assert.equal(f.state.presentation.annotations.length, 0);
  await f.history.undo(); assert.equal(f.state.presentation.annotations[0].text, '第一行\n第二行');
});

test('annotations: a dialog from an old same-case canvas binding cannot write into the new presentation', async () => {
  const f = annotations(); f.env.openAnnotationEditor();
  f.h.element('#annotationText').value = '旧画布的内容';
  f.state.canvasGeneration += 1;
  const result = await f.env.saveAnnotationEditor();
  assert.equal(result, false); assert.equal(f.state.presentation.annotations.length, 0);
  assert.equal(f.history.status().count, 0);
});

test('annotations: pointer drag records one undo step, ignores other pointers and rolls back cancellation', async () => {
  const f = annotations(); await f.save('合成多行\n第二行');
  const y = f.env.annotationAnchorY(f.state.graph.findById('SYN-A'), f.state.presentation.annotations[0]) + 4;
  f.pointer('pointerdown', 3, y); f.pointer('pointermove', 23, y + 30, 999);
  assert.equal(f.state.presentation.annotations[0].dx, 0, 'a second pointer cannot move the note');
  f.pointer('pointermove', 23, y + 30); f.pointer('pointerup', 23, y + 30);
  assert.equal(f.history.status().count, 2); assert.equal(f.state.presentation.annotations[0].dx, 20);
  await f.history.undo(); assert.equal(f.state.presentation.annotations[0].dx, 0);
  f.pointer('pointerdown', 3, y); f.pointer('pointermove', 43, y + 40); f.pointer('pointercancel', 43, y + 40);
  assert.equal(f.state.presentation.annotations[0].dx, 0); assert.equal(f.history.status().count, 2);
  f.pointer('pointerdown', 3, y); f.pointer('pointermove', 43, y + 40); f.pointer('lostpointercapture');
  assert.equal(f.state.presentation.annotations[0].dx, 0);
});

test('annotations: view revision revocation rolls back a drag without recording unrelated new state', async () => {
  const f=annotations();await f.save('绑定合成标注');
  const note=f.state.presentation.annotations[0],y=f.env.annotationAnchorY(f.state.graph.findById('SYN-A'),note)+4;
  f.pointer('pointerdown',3,y);f.pointer('pointermove',40,y+20);
  f.state.graphView.revision+=1;f.state.presentation.nodeStyles={'SYN-A':{size:48}};
  f.pointer('pointerup',40,y+20);
  assert.equal(note.dx,0);assert.equal(f.history.status().count,1,'a revoked gesture cannot record unrelated later presentation');
});
