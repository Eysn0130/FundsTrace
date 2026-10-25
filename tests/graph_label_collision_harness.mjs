import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const root = fileURLToPath(new URL('../', import.meta.url));
const sandbox = { window: {} };
vm.runInNewContext(readFileSync(`${root}static/graph-labels.js`, 'utf8'), sandbox, { filename: 'graph-labels.js' });
const labels = sandbox.window.FundsGraphLabels;
const box = (minX, minY, maxX, maxY) => ({ minX, minY, maxX, maxY });
const candidate = (key, bounds, options = {}) => ({ key, kind: 'node', bounds, priority: 0, required: false, ...options });
const ids = result => ({ nodes: [...result.nodeIds], edges: [...result.edgeKeys], rejected: result.rejected });

test('crossing rectangles and touching boundaries collide across node and edge labels', () => {
  const result = labels.select([
    candidate('node-a', box(0, 0, 20, 20), { priority: 3 }),
    candidate('crossing-edge', box(10, -5, 30, 5), { kind: 'edge', priority: 2 }),
    candidate('separate-edge', box(30, 30, 40, 40), { kind: 'edge', priority: 1 }),
    candidate('touching-node', box(20, 5, 25, 10)),
  ], { padding: 0 });
  assert.deepEqual(ids(result), { nodes: ['node-a'], edges: ['separate-edge'], rejected: 2 });
});

test('default padding reserves three pixels on each side and exact contact remains conservative', () => {
  const pair = start => [candidate('a', box(0, 0, 10, 10)), candidate('b', box(start, 0, start + 10, 10))];
  assert.deepEqual([...labels.select(pair(15)).nodeIds], ['a']);
  assert.deepEqual([...labels.select(pair(16)).nodeIds], ['a']);
  assert.deepEqual([...labels.select(pair(16.01)).nodeIds], ['a', 'b']);
  assert.deepEqual([...labels.select(pair(15), { padding: 0 }).nodeIds], ['a', 'b']);
  assert.deepEqual([...labels.select(pair(15), { padding: -1 }).nodeIds], ['a', 'b']);
});

test('required labels may overlap each other and block ordinary labels regardless of ordinary priority', () => {
  const result = labels.select([
    candidate('ordinary-high', box(0, 0, 20, 20), { priority: 10000 }),
    candidate('required-low', box(0, 0, 20, 20), { priority: -100, required: true }),
    candidate('required-edge', box(10, 10, 25, 25), { priority: 1, kind: 'edge', required: true }),
    candidate('ordinary-far', box(100, 100, 120, 120)),
  ]);
  assert.deepEqual(ids(result), { nodes: ['required-low', 'ordinary-far'], edges: ['required-edge'], rejected: 1 });
});

test('priority then key ordering is deterministic under input permutations and crosses label kinds', () => {
  const examples = [
    candidate('z', box(0, 0, 10, 10), { priority: 10 }),
    candidate('a', box(0, 0, 10, 10), { priority: 10, kind: 'edge' }),
    candidate('highest', box(100, 100, 110, 110), { priority: 20 }),
    candidate('far', box(200, 200, 210, 210), { priority: -1 }),
  ];
  const expected = { nodes: ['highest', 'far'], edges: ['a'], rejected: 1 };
  assert.deepEqual(ids(labels.select(examples)), expected);
  assert.deepEqual(ids(labels.select([...examples].reverse())), expected);
  assert.deepEqual(ids(labels.select([examples[2], examples[0], examples[3], examples[1]])), expected);
  const equalKey = [candidate('same', box(0, 0, 10, 10)), candidate('same', box(0, 0, 10, 10), { kind: 'edge' })];
  assert.deepEqual(ids(labels.select(equalKey)), { nodes: [], edges: ['same'], rejected: 1 });
  assert.deepEqual(ids(labels.select([...equalKey].reverse())), { nodes: [], edges: ['same'], rejected: 1 });
});

test('the caller supplies screen coordinates so changing a projected gap changes collision results', () => {
  const projected = screenGap => [candidate('anchor', box(0, 0, 20, 20)),
    candidate('neighbor', box(20 + screenGap, 0, 40 + screenGap, 20))];
  assert.equal(labels.select(projected(5)).nodeIds.size, 1);
  assert.equal(labels.select(projected(30)).nodeIds.size, 2);
  assert.equal(labels.select(projected(5), { padding: 0 }).nodeIds.size, 2);
});

test('very large rectangles use bounded indexing and remain conservative blockers', () => {
  const result = labels.select([
    candidate('huge-required', box(-Number.MAX_VALUE, -Number.MAX_VALUE, Number.MAX_VALUE, Number.MAX_VALUE), { required: true }),
    candidate('tiny', box(0, 0, 10, 10), { priority: 100 }),
  ]);
  assert.deepEqual(ids(result), { nodes: ['huge-required'], edges: [], rejected: 1 });
  const separated = labels.select([
    candidate('left', box(-1e12, -1e12, -1e10, -1e10)),
    candidate('right', box(1e10, 1e10, 1e12, 1e12)),
    candidate('center', box(0, 0, 1, 1)),
  ]);
  assert.deepEqual([...separated.nodeIds], ['center', 'left', 'right']);
  assert.equal(separated.rejected, 0);
});

test('invalid geometry fails closed for ordinary labels and preserves required labels without invented bounds', () => {
  const result = labels.select([
    candidate('required-invalid', box(20, 0, 10, 10), { required: true }),
    candidate('ordinary-invalid', box(NaN, 0, 10, 10)),
    candidate('ordinary-infinite', box(0, 0, Infinity, 10)),
    candidate('ordinary-missing', null),
    candidate('valid', box(0, 0, 10, 10)),
  ]);
  assert.deepEqual(ids(result), { nodes: ['required-invalid', 'valid'], edges: [], rejected: 3 });
  assert.deepEqual(ids(labels.select([])), { nodes: [], edges: [], rejected: 0 });
  assert.deepEqual(ids(labels.select(null)), { nodes: [], edges: [], rejected: 0 });
  assert.deepEqual(ids(labels.select([null, { key: 42, kind: 'node' }, { key: 'bad-kind', kind: 'account' }])),
    { nodes: [], edges: [], rejected: 3 });
  assert.deepEqual(ids(labels.select([candidate('point', box(0, 0, 0, 0)), candidate('touch', box(0, 0, 10, 10))], { padding: 0 })),
    { nodes: ['point'], edges: [], rejected: 1 });
});

test('disabled explicitly retains all labels even with overlap or missing geometry', () => {
  const input = [candidate('a', box(0, 0, 20, 20)), candidate('b', box(0, 0, 20, 20), { kind: 'edge' }),
    candidate('c', null)];
  assert.deepEqual(ids(labels.select(input, { disabled: true })), { nodes: ['a', 'c'], edges: ['b'], rejected: 0 });
});

test('duplicate identities retain the highest priority candidate and never duplicate output membership', () => {
  const result = labels.select([
    candidate('same', box(0, 0, 20, 20), { priority: 1 }),
    candidate('same', box(100, 100, 120, 120), { priority: 2 }),
    candidate('near-low', box(0, 0, 20, 20)),
  ]);
  assert.deepEqual(ids(result), { nodes: ['same', 'near-low'], edges: [], rejected: 1 });
});

test('5000 separated rectangles remain complete and frozen input and geometry remain unchanged', () => {
  const input = Array.from({ length: 5000 }, (_, index) => Object.freeze(candidate(`SYN-${String(index).padStart(5, '0')}`,
    Object.freeze(box(index % 100 * 100, Math.floor(index / 100) * 100,
      index % 100 * 100 + 40, Math.floor(index / 100) * 100 + 16)), { priority: index % 3 })));
  Object.freeze(input);
  const before = JSON.stringify(input);
  const result = labels.select(input);
  assert.equal(result.nodeIds.size, 5000); assert.equal(result.edgeKeys.size, 0); assert.equal(result.rejected, 0);
  assert.equal(JSON.stringify(input), before);
  result.nodeIds.clear();
  assert.equal(labels.select(input).nodeIds.size, 5000, 'returned sets do not alter a retained module cache');
});

test('labels avoid every node body and return the first usable screen-space placement', () => {
  const result = labels.select([
    candidate('edge', box(0, 0, 20, 10), { kind: 'edge', placements: [
      { bounds: box(0, 0, 20, 10), dx: 0, dy: 0 },
      { bounds: box(0, 30, 20, 40), dx: 0, dy: 30 },
    ] }),
  ], { obstacles: [{ key: 'THIRD-NODE', bounds: box(5, -5, 15, 15) }], padding: 0 });
  assert.ok(result.edgeKeys.has('edge'));
  assert.equal(result.edgePlacements.get('edge').dy, 30);
  assert.equal(result.edgePlacements.get('edge').index, 1);
  assert.equal(result.conflicts, 0);
});

test('required labels try alternatives before retaining a conflicting position with explicit statistics', () => {
  const result = labels.select([
    candidate('a', box(0, 0, 20, 20), { required: true }),
    candidate('b', box(0, 0, 20, 20), { required: true, placements: [
      { bounds: box(0, 0, 20, 20), dx: 0, dy: 0 },
      { bounds: box(40, 0, 60, 20), dx: 40, dy: 0 },
    ] }),
    candidate('c', box(0, 0, 20, 20), { required: true }),
  ], { padding: 0 });
  assert.equal(result.nodeIds.size, 3);
  assert.equal(result.nodePlacements.get('b').dx, 40);
  assert.equal(result.nodePlacements.get('b').conflicted, false);
  assert.equal(result.nodePlacements.get('c').conflicted, true);
  assert.equal(result.conflicts, 1);
  assert.equal(result.unplaced, 0);
});

test('retainAll keeps all and export semantics while still attempting collision avoidance', () => {
  const result = labels.select([
    candidate('a', box(0, 0, 10, 10)),
    candidate('b', box(0, 0, 10, 10), { placements: [{ bounds: box(30, 0, 40, 10), dx: 30, dy: 0 }] }),
    candidate('c', box(0, 0, 10, 10)),
    candidate('d', null),
  ], { retainAll: true, padding: 0 });
  assert.equal(result.nodeIds.size, 4);
  assert.equal(result.nodePlacements.get('b').dx, 30);
  assert.equal(result.conflicts, 1);
  assert.equal(result.unknownGeometry, 1);
  assert.equal(result.rejected, 0);
});

test('viewport intersections keep the first position and explicit own-body exclusions without mutating inputs', () => {
  const input = [candidate('a', box(0, 0, 10, 10), { ignoreObstacleKeys: ['a'], placements: [
    { bounds: box(-5, 0, 5, 10), dx: -5, dy: 0 },
    { bounds: box(0, 0, 10, 10), dx: 0, dy: 0 },
  ] }), candidate('b', box(200, 200, 210, 210))];
  const before = JSON.stringify(input);
  const result = labels.select(input, { padding: 0, viewport: box(0, 0, 100, 100),
    obstacles: [{ key: 'a', bounds: box(0, 0, 10, 10) }] });
  assert.deepEqual([...result.nodeIds], ['a']);
  assert.equal(result.nodePlacements.get('a').index, 0);
  assert.equal(result.nodePlacements.get('a').dx, -5);
  assert.equal(result.unplaced, 1);
  assert.equal(JSON.stringify(input), before);
});

for (const [side, bounds] of [
  ['left', box(-18, 30, 2, 40)], ['right', box(98, 30, 118, 40)],
  ['top', box(30, -8, 50, 2)], ['bottom', box(30, 98, 50, 108)],
]) test(`a label with only a visible strip at the ${side} viewport boundary keeps its fixed anchor`, () => {
  const input = Object.freeze([Object.freeze(candidate('partial', Object.freeze(bounds)))]);
  const result = labels.select(input, { viewport: box(0, 0, 100, 100) });
  assert.deepEqual([...result.nodeIds], ['partial']);
  const placement = result.nodePlacements.get('partial');
  assert.deepEqual({ ...placement.bounds }, bounds);
  assert.equal(placement.dx, 0); assert.equal(placement.dy, 0);
  assert.equal(placement.conflicted, false);
  assert.equal(result.rejected, 0);
});

test('viewport intersection preserves collision checks and fully offscreen required and all modes', () => {
  const viewport = box(0, 0, 100, 100), partial = box(-10, 20, 10, 30), outside = box(-30, 60, -10, 70);
  const result = labels.select([
    candidate('partial', partial, { priority: 1 }), candidate('collision', partial), candidate('outside', outside),
  ], { viewport });
  assert.deepEqual(ids(result), { nodes: ['partial'], edges: [], rejected: 2 });
  assert.equal(result.unplaced, 2);
  const required = labels.select([candidate('outside', outside, { required: true })], { viewport });
  assert.deepEqual([...required.nodeIds], ['outside']);
  assert.equal(required.nodePlacements.get('outside').conflicted, true);
  for (const options of [{ retainAll: true }, { disabled: true }]) {
    const retained = labels.select([candidate('outside', outside)], { viewport, ...options });
    assert.deepEqual([...retained.nodeIds], ['outside']);
    assert.equal(retained.nodePlacements.get('outside').dx, 0);
    assert.equal(retained.nodePlacements.get('outside').dy, 0);
  }
});

test('a manually pinned required label stays at its only position and reports body conflicts', () => {
  const result = labels.select([
    candidate('manual', box(0, 0, 20, 10), { kind: 'edge', required: true }),
    candidate('ordinary', box(0, 0, 20, 10)),
    candidate('unknown-required', null, { required: true }),
  ], { obstacles: [{ key: 'THIRD', bounds: box(5, 0, 15, 10) }], padding: 0, obstaclePadding: 0 });
  assert.deepEqual([...result.edgeKeys], ['manual']);
  assert.equal(result.edgePlacements.get('manual').dx, 0);
  assert.equal(result.edgePlacements.get('manual').dy, 0);
  assert.equal(result.edgePlacements.get('manual').conflicted, true);
  assert.equal(result.conflicts, 1);
  assert.equal(result.unknownGeometry, 1);
  assert.equal(result.unplaced, 1);
  assert.equal(result.rejected, 1);
});
