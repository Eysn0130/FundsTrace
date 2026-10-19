import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const root = fileURLToPath(new URL('../', import.meta.url));
const sandbox = { window: {} };
vm.runInNewContext(readFileSync(`${root}static/graph-view.js`, 'utf8'), sandbox, { filename: 'graph-view.js' });
const view = sandbox.window.FundsGraphView;
const sorted = values => [...values].sort();
const account = i => `SYN-${String(i).padStart(5, '0')}`;
function star(size = 1000) {
  return { nodes: [{ account: 'ROOT', is_main: 1 }, ...Array.from({ length: size }, (_, i) => ({ account: account(i) }))],
    edges: Array.from({ length: size }, (_, i) => ({ source: 'ROOT', target: account(i),
      total: i % 2 ? '9999999999999999.99' : '0.01',
      amount_totals: [{ currency: i % 2 ? 'USD' : 'CNY', amount: i % 2 ? '9999999999999999.99' : '0.01' }] })) };
}

test('one-hop expansion reports every hidden neighbor and adds deterministic batches', () => {
  const graph = star(), index = view.createIndex(graph), visible = new Set(['ROOT']);
  const first = view.neighborhood(index, 'ROOT', { limit: 50, visible });
  assert.equal(first.rootFound, true); assert.equal(first.totalNeighbors, 1000);
  assert.equal(first.added, 50); assert.equal(first.remaining, 950); assert.equal(first.nodeIds.size, 51);
  assert.equal(visible.size, 1);
  assert.deepEqual(sorted(first.nodeIds), ['ROOT', ...Array.from({ length: 50 }, (_, i) => account(i))]);
  const second = view.neighborhood(index, 'ROOT', { limit: 50, visible: first.nodeIds });
  assert.equal(second.added, 50); assert.equal(second.remaining, 900); assert.equal(second.nodeIds.size, 101);
  assert.equal(first.nodeIds.size, 51);
  let current = second;
  for (let i = 0; i < 18; i++) current = view.neighborhood(index, 'ROOT', { visible: current.nodeIds });
  assert.equal(current.nodeIds.size, 1001); assert.equal(current.remaining, 0);
  assert.equal(view.neighborhood(index, 'ROOT', { visible: current.nodeIds }).added, 0);
});

test('expansion respects direction, excludes self-loops and never traverses a second hop', () => {
  const index = view.createIndex({ nodes: ['R', 'IN', 'OUT', 'TWO', 'ISOLATED'].map(account => ({ account })), edges: [
    { source: 'IN', target: 'R' }, { source: 'R', target: 'OUT' }, { source: 'OUT', target: 'TWO' },
    { source: 'R', target: 'R' }, { source: 'R', target: 'MISSING' },
  ] });
  assert.deepEqual(sorted(view.neighborhood(index, 'R', { direction: 'in' }).nodeIds), ['IN', 'R']);
  assert.deepEqual(sorted(view.neighborhood(index, 'R', { direction: 'out' }).nodeIds), ['OUT', 'R']);
  assert.deepEqual(sorted(view.neighborhood(index, 'R').nodeIds), ['IN', 'OUT', 'R']);
  const isolated = view.neighborhood(index, 'ISOLATED');
  assert.deepEqual(sorted(isolated.nodeIds), ['ISOLATED']); assert.equal(isolated.remaining, 0);
  assert.equal(index.edgeKeys.size, 4);
});

test('limits remain bounded and malformed limits use safe defaults', () => {
  const index = view.createIndex(star());
  assert.equal(view.neighborhood(index, 'ROOT', { limit: 1e9 }).added, 200);
  assert.equal(view.neighborhood(index, 'ROOT', { limit: -5 }).added, 1);
  assert.equal(view.neighborhood(index, 'ROOT', { limit: 3.9 }).added, 3);
  for (const limit of [NaN, Infinity, '200', undefined]) assert.equal(view.neighborhood(index, 'ROOT', { limit }).added, 50);
});

test('indexes remain case-local even for overlapping account IDs and stale visible sets', () => {
  const a = view.createIndex({ nodes: [{ account: 'SHARED' }, { account: 'CASE-A' }], edges: [{ source: 'SHARED', target: 'CASE-A' }] });
  const b = view.createIndex({ nodes: [{ account: 'SHARED' }, { account: 'CASE-B' }], edges: [{ source: 'SHARED', target: 'CASE-B' }] });
  const visibleA = view.neighborhood(a, 'SHARED').nodeIds;
  assert.deepEqual(sorted(view.neighborhood(b, 'SHARED', { visible: visibleA }).nodeIds), ['CASE-B', 'SHARED']);
  const missing = view.neighborhood(b, 'CASE-A', { visible: visibleA });
  assert.equal(missing.rootFound, false); assert.deepEqual(sorted(missing.nodeIds), ['SHARED']);
  assert.equal(missing.added, 0); assert.equal(missing.totalNeighbors, 0); assert.equal(missing.remaining, 0);
  assert.deepEqual(sorted(visibleA), ['CASE-A', 'SHARED']);
});

test('auto labels use explicit zoom bands, hard caps and selection priority', () => {
  const index = view.createIndex(star());
  const overview = view.labels(index, { zoom: 0.59 });
  assert.equal(overview.level, 'overview'); assert.equal(overview.nodeIds.size, 20);
  assert.equal(overview.edgeKeys.size, 0); assert.ok(overview.nodeIds.has('ROOT'));
  const selectedEdge = { source: 'ROOT', target: account(999) };
  const names = view.labels(index, { zoom: 0.6, selectedAccount: account(998), hoveredAccount: account(997), selectedEdge });
  assert.equal(names.level, 'names'); assert.equal(names.nodeIds.size, 80); assert.equal(names.edgeKeys.size, 40);
  for (const id of [account(998), account(997), account(999), 'ROOT']) assert.ok(names.nodeIds.has(id));
  assert.ok(names.edgeKeys.has(`ROOT=>${account(999)}`));
  const detail = view.labels(index, { zoom: 1 });
  assert.equal(detail.level, 'detail'); assert.equal(detail.nodeIds.size, 160); assert.equal(detail.edgeKeys.size, 80);
  const lowSelected = view.labels(index, { zoom: 0.1, selectedEdge });
  assert.deepEqual(sorted(lowSelected.edgeKeys), [`ROOT=>${account(999)}`]);
});

test('focus labels contain only active one-hop neighborhoods, all is an explicit override', () => {
  const graph = star(); graph.nodes.push({ account: 'UNRELATED' });
  const index = view.createIndex(graph);
  const empty = view.labels(index, { mode: 'focus', zoom: 3 });
  assert.equal(empty.nodeIds.size, 0); assert.equal(empty.edgeKeys.size, 0);
  const focus = view.labels(index, { mode: 'focus', zoom: 3, selectedAccount: account(999) });
  assert.deepEqual(sorted(focus.nodeIds), ['ROOT', account(999)]);
  assert.deepEqual(sorted(focus.edgeKeys), [`ROOT=>${account(999)}`]);
  const all = view.labels(index, { mode: 'all', zoom: 0.01 });
  assert.equal(all.nodeIds.size, 1002); assert.equal(all.edgeKeys.size, 1000); assert.equal(all.level, 'detail');
});

test('focus labels retain eighteen selected roots and report excess roots beyond the overview budget', () => {
  const graph = { nodes: Array.from({ length: 35 }, (_, i) => ({ account: account(i) })), edges: [] };
  const index = view.createIndex(graph), selectedIds = new Set(graph.nodes.slice(0, 30).map(node => node.account));
  const eighteen = new Set([...selectedIds].slice(0, 18));
  const complete = view.labels(index, { mode: 'focus', zoom: 0.1, selectedIds: eighteen, selectedAccount: null });
  assert.deepEqual(sorted(complete.nodeIds), sorted(eighteen));
  assert.equal(complete.budgetHiddenNodes, 0);
  const focus = view.labels(index, { mode: 'focus', zoom: 0.1, selectedIds, selectedAccount: null });
  assert.deepEqual(sorted(focus.nodeIds), sorted([...selectedIds].slice(0, 20)));
  assert.equal(focus.nodeCap, 20, 'explicit multi-selection does not remove the density budget');
  assert.equal(focus.budgetHiddenNodes, 10);
  assert.equal(selectedIds.size, 30);
});

test('five-thousand-node selection remains bounded with hover, edge and primary priority and explicit all override', () => {
  const graph = star(4999), index = view.createIndex(graph);
  const selectedIds = new Set(['FOREIGN', ...[...index.nodeIds].reverse()]);
  const options = { selectedIds, hoveredAccount: account(4998), selectedAccount: account(4997),
    selectedEdge: { source: 'ROOT', target: account(4996) } };
  const before = [...selectedIds];
  let neighborReads = 0;
  const originalNeighbors = index.neighbors.get.bind(index.neighbors);
  index.neighbors.get = key => { neighborReads += 1; return originalNeighbors(key); };
  for (const mode of ['auto', 'focus']) for (const [zoom, cap] of [[0.1, 20], [0.8, 80], [2, 160]]) {
    const result = view.labels(index, { ...options, mode, zoom });
    assert.equal(result.nodeIds.size, cap); assert.equal(result.nodeCap, cap);
    assert.equal(result.budgetHiddenNodes, 5000-cap, 'only valid eligible roots count as budget-hidden');
    for (const id of ['ROOT', account(4998), account(4997), account(4996)]) assert.ok(result.nodeIds.has(id), `${id} stays in the priority budget`);
    assert.ok(result.edgeKeys.has(`ROOT=>${account(4996)}`));
    const reordered = view.labels(index, { ...options, mode, zoom, selectedIds: new Set([...selectedIds].reverse()) });
    assert.deepEqual(sorted(reordered.nodeIds), sorted(result.nodeIds), 'selection insertion order does not change stable label membership');
  }
  assert.equal(neighborReads, 0, 'a full root budget never enumerates unused one-hop neighbors');
  const all = view.labels(index, { ...options, mode: 'all', zoom: 0.01 });
  assert.equal(all.nodeIds.size, 5000); assert.equal(all.edgeKeys.size, 4999); assert.equal(all.budgetHiddenNodes, 0);
  assert.deepEqual([...selectedIds], before, 'sorting does not mutate the caller-owned selection');

  const disconnected = view.createIndex({ nodes: graph.nodes, edges: [] });
  let incidentReads = 0;
  const originalIncident = disconnected.incidentEdges.get.bind(disconnected.incidentEdges);
  disconnected.incidentEdges.get = key => { incidentReads += 1; return originalIncident(key); };
  view.labels(disconnected, { mode: 'focus', zoom: 0.8, selectedIds });
  assert.ok(incidentReads <= 80, 'edge-label work only visits roots admitted by the node budget');
});

test('multi-selection combines legacy, hover and selected-edge roots without foreign IDs', () => {
  const index = view.createIndex({ nodes: ['A', 'B', 'C', 'D', 'E', 'N', 'OTHER'].map(account => ({ account })),
    edges: [{ source: 'A', target: 'N' }, { source: 'D', target: 'E' }] });
  const result = view.labels(index, { mode: 'focus', zoom: 2, selectedIds: ['A', 'B', 'MISSING', 'A'],
    selectedAccount: 'C', hoveredAccount: 'N', selectedEdge: 'D=>E' });
  assert.deepEqual(sorted(result.nodeIds), ['A', 'B', 'C', 'D', 'E', 'N']);
  assert.deepEqual(sorted(result.edgeKeys), ['A=>N', 'D=>E']);
  const restricted = view.labels(index, { mode: 'focus', selectedIds: new Set(['A', 'B']), eligibleNodeIds: new Set(['B']) });
  assert.deepEqual([...restricted.nodeIds], ['B']);
  assert.equal(restricted.budgetHiddenNodes, 0, 'viewport-ineligible roots are not reported as hidden by a budget');
});

test('small graphs preserve every label and deterministic order is independent of input order', () => {
  const graph = star(19), index = view.createIndex(graph);
  const labels = view.labels(index, { zoom: 0.1 });
  assert.equal(labels.nodeIds.size, 20); assert.equal(labels.edgeKeys.size, 19);
  const neighborhood = view.labels(view.createIndex(star(50)), { zoom: 0.5 });
  assert.equal(neighborhood.nodeIds.size, 20); assert.equal(neighborhood.edgeKeys.size, 0);
  const big = star(), reverse = { nodes: [...big.nodes].reverse(), edges: [...big.edges].reverse() };
  const a = view.createIndex(big), b = view.createIndex(reverse);
  assert.deepEqual(sorted(view.labels(a, { zoom: 0.8 }).nodeIds), sorted(view.labels(b, { zoom: 0.8 }).nodeIds));
  assert.deepEqual(sorted(view.labels(a, { zoom: 0.8 }).edgeKeys), sorted(view.labels(b, { zoom: 0.8 }).edgeKeys));
  assert.deepEqual(sorted(view.neighborhood(a, 'ROOT').nodeIds), sorted(view.neighborhood(b, 'ROOT').nodeIds));
});

test('display policy leaves huge amounts, cents, currency groups and raw graph objects unchanged', () => {
  const graph = star(), original = JSON.stringify(graph);
  for (const edge of graph.edges) { Object.freeze(edge.amount_totals[0]); Object.freeze(edge.amount_totals); Object.freeze(edge); }
  graph.nodes.forEach(Object.freeze); Object.freeze(graph.nodes); Object.freeze(graph.edges); Object.freeze(graph);
  const index = view.createIndex(graph);
  view.neighborhood(index, 'ROOT', { visible: new Set(['ROOT']), limit: 200 });
  for (const zoom of [0.1, 0.6, 1, 2]) view.labels(index, { zoom, selectedAccount: account(999) });
  assert.equal(JSON.stringify(graph), original);
  assert.equal(index.edgeByKey.get(`ROOT=>${account(999)}`).total, '9999999999999999.99');
  assert.equal(index.edgeByKey.get(`ROOT=>${account(0)}`).amount_totals[0].amount, '0.01');
  const first = view.labels(index, { mode: 'all' }); first.nodeIds.clear(); first.edgeKeys.clear();
  assert.equal(view.labels(index, { mode: 'all' }).nodeIds.size, 1001);
});
