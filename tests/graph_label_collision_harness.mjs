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
