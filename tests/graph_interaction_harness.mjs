import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { harness } from './legacy_navigation_harness.mjs';

const response = value => ({ ok: true, status: 200, headers: { get: () => 'application/json' },
  json: async () => value, text: async () => JSON.stringify(value) });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

function interactions() {
  const h = harness('app.js'), env = h.run('globalThis'), state = h.run('state');
  const classes = new Set(), selected = new Set();
  h.element('#drawer').classList = {
    add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name),
  };
  env.scheduleGraphLabels = () => {};
  state.caseId = 1; state.graphLoading = false;
  state.lastGraph = { nodes: [{ account: 'SYN-A' }, { account: 'SYN-B' }], edges: [] };
  state.graph = { setItemState(item, name, on) {
    if (name === 'selected') on ? selected.add(item.getModel().id) : selected.delete(item.getModel().id);
  } };
  const item = model => ({ getModel: () => model });
  env.nodeA = item({ id: 'SYN-A' }); env.nodeB = item({ id: 'SYN-B' });
  const edge = (id, source, target) => ({ id, source, target, ids: [id], data: {
    source, target, source_name: source, target_name: target, cnt: 1, total: '12.34', currencies: ['CNY'],
  } });
  env.edgeA = item(edge(1, 'SYN-A', 'SYN-B')); env.edgeB = item(edge(2, 'SYN-B', 'SYN-A'));
  const pending = new Map();
  h.network(path => {
    if (path.startsWith('/api/main-accounts?')) return response([]);
    const request = deferred(); pending.set(path, request); return request.promise;
  });
  return { h, env, state, classes, selected,
    body: () => h.element('#drawerBody').innerHTML,
    openNode: name => h.run(`showNodeDrawer('SYN-${name}', node${name})`),
    openEdge: name => h.run(`showEdgeDrawer(edge${name}.getModel(), edge${name})`),
    resolveNode(name) { pending.get(`/api/accounts/SYN-${name}/profile?case_id=1`).resolve(response({ name: `PROFILE-${name}`, top_counterparties: [] })); },
    resolveEdge(name) { pending.get(`/api/flows?case_id=1&ids=${name === 'A' ? 1 : 2}&size=500`).resolve(response({ rows: [{ id: 1, amount: '12.34', currency: 'CNY', summary: `FLOW-${name}` }] })); },
  };
}

test('graph interaction: the latest same-graph account owns the drawer after out-of-order profile responses', async () => {
  const f = interactions(), a = f.openNode('A'), b = f.openNode('B');
  f.resolveNode('B'); await b;
  assert.ok(f.body().includes('PROFILE-B'), 'the current profile should be displayed');
  f.resolveNode('A'); await a;
  assert.ok(f.body().includes('PROFILE-B'), 'a late profile must preserve the current account');
  assert.ok(!f.body().includes('PROFILE-A'), 'a late profile must not enter the drawer');
  assert.deepEqual([...f.selected], ['SYN-B']);
});

test('graph interaction: the latest same-graph edge owns the drawer after out-of-order flow responses', async () => {
  const f = interactions(), a = f.openEdge('A'), b = f.openEdge('B');
  f.resolveEdge('B'); await b;
  assert.ok(f.body().includes('FLOW-B'), 'the current edge flows should be displayed');
  f.resolveEdge('A'); await a;
  assert.ok(f.body().includes('FLOW-B'), 'late flows must preserve the current edge');
  assert.ok(!f.body().includes('FLOW-A'), 'late flows must not enter the drawer');
});

for (const kind of ['Node', 'Edge']) {
  test(`graph interaction: closing the ${kind.toLowerCase()} drawer revokes its delayed response`, async () => {
    const f = interactions(), request = f[`open${kind}`]('A');
    f.h.run('closeDrawer()'); const body = f.body();
    f[`resolve${kind}`]('A'); await request;
    assert.equal(f.classes.has('open'), false);
    assert.ok(f.body() === body, 'a closed drawer must not receive delayed detail content');
    assert.equal(f.selected.size, 0);
  });
}

for (const [first, second] of [['Node', 'Edge'], ['Edge', 'Node']]) {
  test(`graph interaction: delayed ${first.toLowerCase()} details cannot overwrite a newer ${second.toLowerCase()} drawer`, async () => {
    const f = interactions(), a = f[`open${first}`]('A'), b = f[`open${second}`]('B');
    f[`resolve${second}`]('B'); await b; const body = f.body();
    f[`resolve${first}`]('A'); await a;
    assert.ok(f.body() === body, 'the latest drawer must retain its detail content');
    assert.equal(f.h.element('#drawerTitle').textContent, second === 'Node' ? '账户详情' : '资金流向');
  });
}

function graphInteractions({ selection = false } = {}) {
  const h = harness('app.js'), env = h.run('globalThis'), state = h.run('state');
  env.performance = { now: () => 0 };
  const listeners = new Map(), graphs = [], destroyedWithListeners = [];
  env.window.addEventListener = (name, callback) => {
    if (!listeners.has(name)) listeners.set(name, new Set());
    listeners.get(name).add(callback);
  };
  env.window.removeEventListener = (name, callback) => listeners.get(name)?.delete(callback);
  env.window.dispatchEvent = event => { for (const callback of [...listeners.get(event.type) || []]) callback(event); };
  env.CustomEvent = class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } };
  if (selection) {
    const enhance = element => {
      const attributes = new Map(), classes = new Set(), children = [];
      Object.assign(element, { children,
        getAttribute: name => attributes.get(name) ?? null,
        setAttribute: (name, value) => attributes.set(name, String(value)), removeAttribute: name => attributes.delete(name),
        appendChild(child) { children.push(child); Object.defineProperty(this, 'firstElementChild', { value: children[0], configurable: true }); },
        classList: { add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name) },
      });
      return element;
    };
    const create = env.document.createElement;
    env.document.createElement = kind => enhance(create(kind));
    const container = enhance(h.element('#graphContainer')); container.clientWidth = 900; container.clientHeight = 600;
    env.updateGraphViewStatus = () => {};
    h.run(readFileSync(new URL('../static/graph-selection.js', import.meta.url), 'utf8'));
    env.FundsGraphSelection = env.window.FundsGraphSelection;
  }
  const count = name => listeners.get(name)?.size || 0;
  const makeItem = model => ({ model, states: new Set(), getModel() { return this.model; },
    getContainer: () => ({ get: () => [] }),
    getKeyShape: () => ({ attr: () => [['M', 0, 0], ['L', 200, 200]] }),
  });
  class Graph {
    constructor() { this.events = new Map(); this.nodes = []; this.edges = []; this.updates = []; this.zoom = 1; graphs.push(this); }
    on(name, callback) { if (!this.events.has(name)) this.events.set(name, []); this.events.get(name).push(callback); }
    emit(name, event = {}) { for (const callback of this.events.get(name) || []) callback(event); }
    get(name) { return name === 'canvas' ? { get: () => ({ style: {} }) } : null; }
    getNodes() { return this.nodes; }
    getEdges() { return this.edges; }
    findById(id) { return [...this.nodes, ...this.edges].find(item => item.getModel().id === id); }
    getPointByClient(x, y) { return { x: x / this.zoom, y: y / this.zoom }; }
    getCanvasByPoint(x, y) { return { x: x * this.zoom, y: y * this.zoom }; }
    getZoom() { return this.zoom; }
    data(data) { this.changeData(data); }
    changeData(data) {
      const update = (models, old) => models.map(model => {
        const previous = old.find(item => model.id ? item.model.id === model.id
          : item.model.source === model.source && item.model.target === model.target);
        if (previous) { previous.model = { ...model }; return previous; }
        return makeItem({ ...model });
      });
      this.nodes = update(data.nodes, this.nodes); this.edges = update(data.edges, this.edges);
    }
    updateItem(item, changes) { this.updates.push({ item, changes }); Object.assign(item.model, changes); }
    setItemState(item, name, on) { on ? item.states.add(name) : item.states.delete(name); }
    clearItemStates(item, names) {
      for (const target of item ? [item] : [...this.nodes, ...this.edges]) {
        if (names) for (const name of Array.isArray(names) ? names : [names]) target.states.delete(name);
        else target.states.clear();
      }
    }
    findAllByState(kind, name) { return (kind === 'node' ? this.nodes : this.edges).filter(item => item.states.has(name)); }
    destroy() { destroyedWithListeners.push({ move: count('mousemove'), up: count('mouseup') }); this.destroyed = true; }
    render() {} paint() {} setAutoPaint() {} stopLayout() {} changeSize() {}
  }
  env.G6 = { Graph };
  for (const name of ['scheduleGraphLabels', 'scheduleGraphViewport', 'rebuildGraphViewport',
    'restoreGraphClipping', 'applyGraphLabels', 'fitGraph']) env[name] = () => {};
  state.caseId = 1; state.graphLoading = false; state.forceFit = false; state.edgeType = 'polyline';
  h.run('initGraph()');
  const seed = () => {
    const graph = state.graph;
    graph.data({ nodes: [{ id: 'SYN-A', x: 0, y: 0 }, { id: 'SYN-B', x: 200, y: 200 }],
      edges: [{ id: 'SYN-E', source: 'SYN-A', target: 'SYN-B', type: 'polyline' }] });
    return graph;
  };
  return { h, env, state, graphs, destroyedWithListeners, count, seed,
    event(name, event = {}) { for (const callback of [...listeners.get(name) || []]) callback(event); },
    edgeEvent(graph, name, x, y) { graph.emit(name, { item: graph.getEdges()[0], clientX: x, clientY: y, preventDefault() {} }); },
  };
}

test('graph interaction: repeated graph rebuilds release old global drag listeners before destruction', () => {
  const f = graphInteractions();
  assert.equal(f.count('mousemove'), 2); assert.equal(f.count('mouseup'), 2);
  for (let i = 0; i < 4; i++) {
    f.h.run('initGraph()');
    assert.equal(f.count('mousemove'), 2, `mousemove listeners after rebuild ${i + 1}`);
    assert.equal(f.count('mouseup'), 2, `mouseup listeners after rebuild ${i + 1}`);
  }
  assert.equal(f.destroyedWithListeners.length, 4);
  assert.ok(f.destroyedWithListeners.every(item => item.move === 0 && item.up === 0));
});

test('graph interaction: rebuilding during a bend drag prevents the old graph from changing new geometry', () => {
  const f = graphInteractions(), old = f.seed();
  f.state.edgeBendPos = { 'SYN-A=>SYN-B': [{ x: 100, y: 100 }] };
  f.edgeEvent(old, 'edge:mousedown', 100, 100);
  f.h.run('initGraph()'); f.seed();
  f.state.edgeBendPos = { 'SYN-A=>SYN-B': [{ x: 40, y: 50 }] };
  f.event('mousemove', { clientX: 150, clientY: 160 }); f.event('mouseup');
  assert.deepEqual(JSON.parse(JSON.stringify(f.state.edgeBendPos)), { 'SYN-A=>SYN-B': [{ x: 40, y: 50 }] });
});

for (const [binding, invalidate] of [['case generation', 'state.caseGeneration += 1'],
  ['canvas generation', 'state.canvasGeneration += 1'], ['view revision', 'state.graphView.revision += 1']]) {
  for (const kind of ['bend', 'label']) {
    test(`graph interaction: a ${binding} change revokes an active ${kind} drag on the reused graph`, () => {
      const f = graphInteractions(), graph = f.seed();
      f.state.edgeBendPos = { 'SYN-A=>SYN-B': [{ x: 100, y: 100 }] };
      const event = { item: graph.getEdges()[0], clientX: 100, clientY: 100, preventDefault() {} };
      if (kind === 'label') event.target = { get: name => name === 'name' ? 'text-shape' : undefined };
      graph.emit('edge:mousedown', event);
      f.h.run(`${invalidate}; state.edgeBendPos = {}; state.edgeLabelPos = {};`);
      f.event('mousemove', { clientX: 150, clientY: 160 }); f.event('mouseup');
      assert.deepEqual(Object.keys(f.state.edgeBendPos), [], 'old bend geometry cannot enter the new binding');
      assert.deepEqual(Object.keys(f.state.edgeLabelPos), [], 'old label geometry cannot enter the new binding');
      assert.equal(graph.updates.length, 0, 'the revoked gesture must not update old G6 items');
    });
  }
}

test('graph interaction: moving selected nodes translates internal manual bends and retains boundary bends', () => {
  const f = graphInteractions({ selection: true }), graph = f.state.graph;
  graph.data({ nodes: [{ id: 'SYN-A', x: 0, y: 0 }, { id: 'SYN-B', x: 200, y: 200 }, { id: 'SYN-C', x: 300, y: 200 }],
    edges: [{ id: 'SYN-AB', source: 'SYN-A', target: 'SYN-B' }, { id: 'SYN-BC', source: 'SYN-B', target: 'SYN-C' }] });
  for (const node of graph.getNodes()) node.getEdges = () => graph.getEdges().filter(edge =>
    edge.model.source === node.model.id || edge.model.target === node.model.id);
  f.state.graphView.nodeItems = new Map(graph.getNodes().map(node => [node.model.id, node]));
  f.state.graphSelection.rebind(['SYN-A', 'SYN-B']);
  f.state.edgeBendPos = { 'SYN-A=>SYN-B': [{ x: 40, y: 60 }, { x: 80, y: 90 }], 'SYN-B=>SYN-C': [{ x: 250, y: 180 }] };
  graph.emit('node:mousedown', { item: graph.findById('SYN-A'), clientX: 0, clientY: 0, button: 0, preventDefault() {} });
  f.event('mousemove', { clientX: 20, clientY: 30, buttons: 1, preventDefault() {} });
  f.event('mouseup', { clientX: 20, clientY: 30, preventDefault() {} });
  assert.deepEqual(JSON.parse(JSON.stringify(f.state.edgeBendPos)), {
    'SYN-A=>SYN-B': [{ x: 60, y: 90 }, { x: 100, y: 120 }], 'SYN-B=>SYN-C': [{ x: 250, y: 180 }],
  });
  assert.equal(graph.findById('SYN-A').model.x, 20);
  assert.equal(graph.findById('SYN-B').model.x, 220);
  assert.equal(graph.findById('SYN-C').model.x, 300);
});

for (const zoom of [0.25, 1, 4]) {
  test(`graph interaction: bend drag uses a 10 screen-pixel hit radius at zoom ${zoom}`, () => {
    const f = graphInteractions(), graph = f.seed(); graph.zoom = zoom;
    for (const [screenOffset, expectedPoints] of [[8, 1], [12, 2]]) {
      f.state.edgeBendPos = { 'SYN-A=>SYN-B': [{ x: 100, y: 100 }] };
      f.edgeEvent(graph, 'edge:mousedown', 100 * zoom + screenOffset, 100 * zoom);
      f.event('mousemove', { clientX: 100 * zoom + screenOffset + 1, clientY: 100 * zoom }); f.event('mouseup');
      assert.equal(f.state.edgeBendPos['SYN-A=>SYN-B'].length, expectedPoints,
        `${screenOffset}px away should ${expectedPoints === 1 ? 'move the existing point' : 'insert one point'}`);
    }
  });

  test(`graph interaction: bend removal uses a 20 screen-pixel hit radius at zoom ${zoom}`, () => {
    const f = graphInteractions(), graph = f.seed(); graph.zoom = zoom;
    for (const [screenOffset, removed] of [[18, true], [22, false]]) {
      f.state.edgeBendPos = { 'SYN-A=>SYN-B': [{ x: 100, y: 100 }] };
      f.edgeEvent(graph, 'edge:dblclick', 100 * zoom + screenOffset, 100 * zoom);
      assert.equal(!f.state.edgeBendPos['SYN-A=>SYN-B'], removed, `${screenOffset}px from the bend`);
    }
  });
}

test('graph interaction: new-tracking feedback uses recent state without altering selected items', () => {
  const f = graphInteractions({ selection: true }), graph = f.state.graph;
  f.state.lastGraph = { nodes: ['SYN-A', 'SYN-B', 'SYN-C'].map(account => ({ account, in_cnt: 1, out_cnt: 1 })),
    edges: [{ source: 'SYN-A', target: 'SYN-B', ids: [1], cnt: 1, total: '12.34', currencies: ['CNY'] }] };
  f.state.positions = { 'SYN-A': { x: 0, y: 0 }, 'SYN-B': { x: 200, y: 200 }, 'SYN-C': { x: 300, y: 200 } };
  f.h.run('renderGraph()');
  f.env.currentNode = graph.findById('SYN-C'); f.h.run('selectItem(currentNode)');
  const selectedBefore = graph.findAllByState('node', 'selected').map(item => item.model.id);
  f.state.justAdded = new Set([1]); f.h.run('renderGraph()');
  const runFeedback = () => {
    for (const timer of f.h.timers.filter(timer => !timer.cancelled && !timer.ran && timer.delay <= 100)) {
      timer.ran = true; timer.callback();
    }
  };
  runFeedback();
  assert.deepEqual(graph.findAllByState('node', 'selected').map(item => item.model.id), selectedBefore);
  assert.deepEqual(graph.findAllByState('node', 'recent').map(item => item.model.id).sort(), ['SYN-A', 'SYN-B']);
  assert.deepEqual([...f.state.graphSelection.ids], ['SYN-C']);
  assert.equal(f.state.graphView.selectedAccount, 'SYN-C', 'rendering feedback should retain the selected label focus');
  f.state.lastGraph.edges.push({ source: 'SYN-B', target: 'SYN-C', ids: [2], cnt: 1, total: '56.78', currencies: ['CNY'] });
  f.state.justAdded = new Set([2]); f.h.run('renderGraph()'); runFeedback();
  assert.deepEqual(graph.findAllByState('node', 'selected').map(item => item.model.id), selectedBefore);
  assert.deepEqual(graph.findAllByState('node', 'recent').map(item => item.model.id).sort(), ['SYN-B', 'SYN-C']);
});
