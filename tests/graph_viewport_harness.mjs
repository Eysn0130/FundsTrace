import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const context = vm.createContext({ window: {} });
vm.runInContext(readFileSync(new URL('../static/graph-viewport.js', import.meta.url), 'utf8'), context);
const viewport = context.window.FundsGraphViewport;
const box = (minX = 0, minY = 0, maxX = 100, maxY = 100) => ({ minX, minY, maxX, maxY });
const ids = values => [...values].sort();

test('node bounds conservatively include radius, halo and exact padding boundaries', () => {
  const index = viewport.build({ nodes: [
    { id: 'CENTER', x: 50, y: 50, size: 48 }, { id: 'OUTSIDE', x: 500, y: 500, size: 48 },
    { id: 'PADDING', x: 142, y: 50, size: 48 },
  ] });
  assert.deepEqual(ids(index.query(box()).nodeIds), ['CENTER']);
  assert.deepEqual(ids(index.query(box(), 10).nodeIds), ['CENTER', 'PADDING']);
  assert.deepEqual(ids(index.query(box(), 9.999).nodeIds), ['CENTER']);
  assert.deepEqual(ids(index.query(box(), -100).nodeIds), ['CENTER']);
  assert.deepEqual(ids(index.query(box(), NaN).nodeIds), ['CENTER']);
  const origin = viewport.build({ nodes: [{ id: 'SYN', x: 0, y: 0, size: 48 }] });
  assert.deepEqual(ids(origin.query(box(32, -1, 40, 1)).nodeIds), ['SYN']);
  assert.equal(origin.query(box(32.0001, -1, 40, 1)).nodeIds.size, 0);
  const large = viewport.build({ nodes: [{ id: 'LARGE', x: 0, y: 0, size: 100 }] });
  assert.deepEqual(ids(large.query(box(58, -1, 60, 1)).nodeIds), ['LARGE']);
  assert.equal(large.query(box(58.0001, -1, 60, 1)).nodeIds.size, 0);
});

test('an edge crossing the viewport survives even when both endpoint nodes are outside', () => {
  const index = viewport.build({ nodes: [{ id: 'LEFT', x: -1000, y: 50 }, { id: 'RIGHT', x: 1000, y: 50 }],
    edges: [{ source: 'LEFT', target: 'RIGHT' }] });
  const result = index.query(box());
  assert.equal(result.nodeIds.size, 0); assert.deepEqual(ids(result.edgeKeys), ['LEFT=>RIGHT']);
});

test('control points, self-loop envelopes and dragged label bounds keep potentially visible edges', () => {
  const nodes = [
    { id: 'CURVE-A', x: -600, y: -600 }, { id: 'CURVE-B', x: -400, y: -600 },
    { id: 'LOOP', x: -80, y: 50 }, { id: 'LABEL-A', x: -500, y: -500 }, { id: 'LABEL-B', x: -400, y: -500 },
  ];
  const edges = [
    { source: 'CURVE-A', target: 'CURVE-B', controlPoints: [{ x: 50, y: 50 }] },
    { source: 'LOOP', target: 'LOOP', type: 'loop' },
    { source: 'LABEL-A', target: 'LABEL-B', lx: 50, ly: 50, bw: 40, bh: 20 },
  ];
  const result = viewport.build({ nodes, edges }).query(box());
  assert.equal(result.nodeIds.size, 0);
  assert.deepEqual(ids(result.edgeKeys), ['CURVE-A=>CURVE-B', 'LABEL-A=>LABEL-B', 'LOOP=>LOOP']);
  const unlabeled = viewport.build({ nodes, edges: [{ source: 'LABEL-A', target: 'LABEL-B' }] });
  assert.equal(unlabeled.query(box()).edgeKeys.size, 0);
  const wideLabel = viewport.build({ nodes, edges: [{ source: 'LABEL-A', target: 'LABEL-B', lx: 250, ly: 50, bw: 400, bh: 20 }] });
  assert.deepEqual(ids(wideLabel.query(box()).edgeKeys), ['LABEL-A=>LABEL-B']);
});

test('invalid or empty viewports safely return the entire indexed graph', () => {
  const index = viewport.build({ nodes: [{ id: 'A', x: -1000, y: -1000 }, { id: 'B', x: 1000, y: 1000 }],
    edges: [{ source: 'A', target: 'B' }] });
  for (const invalid of [null, undefined, {}, { minX: 0, minY: 0, maxX: NaN, maxY: 100 },
    { minX: 0, minY: 0, maxX: Infinity, maxY: 100 }, { minX: '0', minY: 0, maxX: 100, maxY: 100 },
    box(100, 0, 0, 100), box(0, 100, 100, 0), box(50, 0, 50, 100), box(0, 50, 100, 50)]) {
    const result = index.query(invalid);
    assert.deepEqual(ids(result.nodeIds), ['A', 'B'], JSON.stringify(invalid));
    assert.deepEqual(ids(result.edgeKeys), ['A=>B'], JSON.stringify(invalid));
  }
  const empty = viewport.build({});
  assert.equal(empty.query(box()).nodeIds.size, 0); assert.equal(empty.query(null).edgeKeys.size, 0);
});

test('a high-zoom world viewport reduces 5000 sparse nodes to the expected local point', () => {
  const graph = { nodes: Array.from({ length: 5000 }, (_, i) => ({ id: `SYN-${String(i).padStart(5, '0')}`,
    x: i % 100 * 200, y: Math.floor(i / 100) * 200, size: 48 })) };
  const index = viewport.build(graph);
  assert.equal(index.query(box(-100, -100, 20100, 10100)).nodeIds.size, 5000);
  const close = index.query(box(4900, 4900, 5100, 5100));
  assert.deepEqual(ids(close.nodeIds), ['SYN-02525']); assert.equal(close.edgeKeys.size, 0);
  assert.deepEqual(ids(index.query(box(5100, 4900, 5300, 5100)).nodeIds), ['SYN-02526']);
});

test('exact monetary metadata, string accounts and frozen raw geometry remain unchanged', () => {
  const graph = { nodes: [
    { id: '0009007199254740993', x: 10, y: 10, data: { total: '9999999999999999.99', currency: 'USD' } },
    { id: 'SYN-B', x: 50, y: 50, data: { total: '0.01', currency: 'CNY' } },
  ], edges: [{ source: '0009007199254740993', target: 'SYN-B', controlPoints: [{ x: 30, y: 20 }],
    data: { amount_totals: [{ currency: 'USD', amount: '9999999999999999.99' }, { currency: 'CNY', amount: '0.01' }] } }] };
  const original = JSON.stringify(graph);
  function freeze(value) { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } }
  freeze(graph);
  const index = viewport.build(graph), result = index.query(box());
  assert.deepEqual(ids(result.nodeIds), ['0009007199254740993', 'SYN-B']);
  assert.deepEqual(ids(result.edgeKeys), ['0009007199254740993=>SYN-B']);
  result.nodeIds.clear(); result.edgeKeys.clear();
  assert.equal(index.query(box()).nodeIds.size, 2); assert.equal(index.query(box()).edgeKeys.size, 1);
  assert.equal(JSON.stringify(graph), original);
  assert.equal(graph.edges[0].data.amount_totals[0].amount, '9999999999999999.99');
  assert.equal(graph.edges[0].data.amount_totals[1].amount, '0.01');
});

test('two cases may reuse identical IDs without sharing viewport bounds or results', () => {
  const a = viewport.build({ nodes: [{ id: 'SHARED', x: 10, y: 10 }, { id: 'OTHER', x: 50, y: 50 }],
    edges: [{ source: 'SHARED', target: 'OTHER' }] });
  const b = viewport.build({ nodes: [{ id: 'SHARED', x: 10000, y: 10000 }, { id: 'OTHER', x: 10100, y: 10100 }],
    edges: [{ source: 'SHARED', target: 'OTHER' }] });
  assert.equal(a.query(box()).nodeIds.size, 2); assert.equal(a.query(box()).edgeKeys.size, 1);
  assert.equal(b.query(box()).nodeIds.size, 0); assert.equal(b.query(box()).edgeKeys.size, 0);
  assert.equal(b.query(box(9900, 9900, 10200, 10200)).nodeIds.size, 2);
  assert.equal(a.query(box()).nodeIds.size, 2);
});

test('measured group bounds retain long node labels and large focused self-loops beyond nominal envelopes', () => {
  const graph = { nodes: [
    { id: 'LONG-LABEL', x: 1000, y: 50, size: 48,
      bounds: { minX: 50, minY: 20, maxX: 1020, maxY: 80 } },
    { id: 'LARGE-LOOP', x: -300, y: 50, size: 100 },
  ], edges: [{ source: 'LARGE-LOOP', target: 'LARGE-LOOP', type: 'loop',
    bounds: { minX: -380, minY: -180, maxX: 30, maxY: 140 } }] };
  const before = JSON.stringify(graph), index = viewport.build(graph), visible = index.query(box());
  assert.deepEqual(ids(visible.nodeIds), ['LONG-LABEL']);
  assert.deepEqual(ids(visible.edgeKeys), ['LARGE-LOOP=>LARGE-LOOP']);
  // Measured bounds augment the fallback envelope; they cannot shrink it.
  assert.deepEqual(ids(index.query(box(1030, 45, 1040, 55)).nodeIds), ['LONG-LABEL']);
  const nominalOnly = viewport.build({ nodes: graph.nodes.map(({ bounds, ...node }) => node),
    edges: graph.edges.map(({ bounds, ...edge }) => edge) });
  assert.equal(nominalOnly.query(box()).nodeIds.size, 0); assert.equal(nominalOnly.query(box()).edgeKeys.size, 0);
  assert.equal(JSON.stringify(graph), before);
});

test('lazy label measurements enlarge the private index without changing frozen source geometry or money', () => {
  const graph = { nodes: [{ id: 'SYN-A', x: 1000, y: 1000 }, { id: 'SYN-B', x: 1100, y: 1000 }],
    edges: [{ source: 'SYN-A', target: 'SYN-B', data: { amount: '9999999999999999.99', currency: 'USD' } }] };
  Object.freeze(graph.edges[0].data); graph.nodes.forEach(Object.freeze); graph.edges.forEach(Object.freeze);
  Object.freeze(graph.nodes); Object.freeze(graph.edges); Object.freeze(graph);
  const before = JSON.stringify(graph), index = viewport.build(graph);
  assert.equal(index.query(box()).nodeIds.size, 0); assert.equal(index.query(box()).edgeKeys.size, 0);
  const measured = Object.freeze({ minX: 10, minY: 10, maxX: 20, maxY: 20 });
  index.include('node', 'SYN-A', measured); index.include('edge', 'SYN-A=>SYN-B', measured);
  assert.deepEqual(ids(index.query(box()).nodeIds), ['SYN-A']);
  assert.deepEqual(ids(index.query(box()).edgeKeys), ['SYN-A=>SYN-B']);
  assert.equal(index.query(box(990, 990, 1110, 1010)).nodeIds.size, 2);
  assert.equal(index.query(box(990, 990, 1110, 1010)).edgeKeys.size, 1);
  // A later narrower measurement retains earlier conservative coverage.
  index.include('node', 'SYN-A', { minX: 2000, minY: 2000, maxX: 2010, maxY: 2010 });
  index.include('edge', 'SYN-A=>SYN-B', { minX: 2000, minY: 2000, maxX: 2010, maxY: 2010 });
  assert.equal(index.query(box()).nodeIds.size, 1); assert.equal(index.query(box()).edgeKeys.size, 1);
  index.include('node', 'UNKNOWN', measured); index.include('edge', 'UNKNOWN=>EDGE', measured);
  assert.equal(index.query(null).nodeIds.size, 2); assert.equal(index.query(null).edgeKeys.size, 1);
  assert.equal(JSON.stringify(graph), before); assert.equal(graph.edges[0].data.amount, '9999999999999999.99');
  const rebuilt = viewport.build(graph);
  assert.equal(rebuilt.query(box()).nodeIds.size, 0); assert.equal(rebuilt.query(box()).edgeKeys.size, 0);
});

test('overview coverage uses current node and measured-label extents with padding', () => {
  const index=viewport.build({nodes:[{id:'A',x:50,y:50,size:40},{id:'B',x:80,y:50,size:40}],edges:[{source:'A',target:'B'}]});
  assert.equal(index.containsAll(box(0,0,110,100)),true);
  assert.equal(index.containsAll(box(0,0,100,100)),false);
  assert.equal(index.containsAll(box(0,0,100,100),8),true);
  index.include('node','A',box(-200,20,10,30));
  assert.equal(index.containsAll(box(0,0,110,100)),false);
  assert.equal(index.containsAll(box(-250,0,110,100)),true);
  assert.equal(index.containsAll(null),false);
  assert.equal(index.containsAll(box(100,0,0,100)),false);
  assert.equal(viewport.build({nodes:[],edges:[]}).containsAll(box()),true);
});
