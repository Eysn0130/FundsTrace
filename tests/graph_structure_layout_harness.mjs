import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const context = vm.createContext({ setTimeout });
vm.runInContext(readFileSync(new URL('../static/graph-layout.js', import.meta.url), 'utf8'), context);
const layout = context.FundsGraphLayout;
const account = i => `SYN-${String(i).padStart(5, '0')}`;
const input = (nodes, edges = []) => ({ kind: 'structural', width: 1000, height: 700, nodes, edges });
const finish = graph => { const model = layout.simulation(graph); while (model.step()) {} return model.result(); };
const canonical = result => Array.from(result.nodes, node => [node.id, node.x, node.y]).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0);
function hub(count = 5000) {
  return input(Array.from({ length: count }, (_, i) => ({ id: account(i), ...(i === 0 ? { isMain: true } : {}) })),
    Array.from({ length: count - 1 }, (_, i) => ({ source: account(0), target: account(i + 1), total: i % 2 ? '0.01' : '9999999999999999.99', currency: i % 2 ? 'CNY' : 'USD' })));
}

test('5000-node hub spreads across separated radial bands without changing facts', () => {
  const graph = hub(), original = JSON.stringify(graph), result = finish(graph);
  assert.equal(result.nodes.length, 5000); assert.equal(result.structure.componentCount, 1);
  assert.equal(result.structure.components[0].anchorReason, 'explicit');
  assert.equal(result.structure.components[0].method, 'bfs-rings');
  assert.equal(result.structure.components[0].anchorIds[0], account(0));
  assert.ok(result.nodes.every(node => Number.isFinite(node.x) && Number.isFinite(node.y)));
  assert.equal(JSON.stringify(graph), original);
  const center = result.nodes.find(node => node.id === account(0));
  const bands = new Set(result.nodes.slice(1).map(node => Math.round(Math.hypot(node.x - center.x, node.y - center.y))));
  assert.ok(bands.size > 20); assert.ok(result.structure.bounds.width > graph.width);
  // Spatial hashing verifies all neighboring occupied cells, not just a sample.
  const cellSize = result.structure.spacing - 0.000001, cells = new Map();
  for (const node of result.nodes) {
    const gx = Math.floor(node.x / cellSize), gy = Math.floor(node.y / cellSize);
    for (let x = gx - 1; x <= gx + 1; x++) for (let y = gy - 1; y <= gy + 1; y++) {
      for (const other of cells.get(`${x},${y}`) || []) assert.ok(Math.hypot(node.x - other.x, node.y - other.y) >= cellSize);
    }
    const key = `${gx},${gy}`; if (!cells.has(key)) cells.set(key, []); cells.get(key).push(node);
  }
  assert.ok(result.interactions < 5000 * 10);
});

test('explicit main markers override degree anchors and multiple mains remain geometry-only', () => {
  const graph = hub(12); graph.nodes[0].isMain = false; graph.nodes[7].isMain = true;
  let result = finish(graph);
  assert.deepEqual(Array.from(result.structure.components[0].anchorIds), [account(7)]);
  graph.nodes[4].isMain = 1; result = finish(graph);
  assert.deepEqual(Array.from(result.structure.components[0].anchorIds), [account(4), account(7)]);
  assert.equal(graph.nodes[0].isMain, false); assert.equal(graph.nodes[1].isMain, undefined);
  assert.equal(result.nodes.some(node => 'isMain' in node), false);
  graph.nodes.forEach(node => { delete node.isMain; }); result = finish(graph);
  assert.equal(result.structure.components[0].anchorReason, 'degree');
  assert.deepEqual(Array.from(result.structure.components[0].anchorIds), [account(0)]);
});

test('disconnected cycles, multiple mains and isolated components get disjoint bounds', () => {
  const graph = input(Array.from({ length: 30 }, (_, i) => ({ id: account(i), isMain: i === 2 || i === 4 })), [
    ...Array.from({ length: 10 }, (_, i) => ({ source: account(i), target: account((i + 1) % 10) })),
    ...Array.from({ length: 8 }, (_, i) => ({ source: account(10), target: account(i + 11) })),
    { source: account(25), target: account(25) }, { source: account(26), target: 'MISSING' },
  ]);
  const result = finish(graph), components = result.structure.components;
  assert.equal(result.structure.componentCount, 13); assert.equal(result.nodes.length, 30);
  for (let i = 0; i < components.length; i++) for (let j = i + 1; j < components.length; j++) {
    const a = components[i].bounds, b = components[j].bounds;
    assert.ok(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y);
  }
  assert.equal(new Set(result.nodes.map(node => `${node.x},${node.y}`)).size, 30);
});

test('long chains and rings use a bounded-area BFS grid instead of thousands of radii', () => {
  for (const closed of [false, true]) {
    const nodes = Array.from({ length: 5000 }, (_, i) => ({ id: account(i), isMain: i === 0 }));
    const edges = Array.from({ length: closed ? 5000 : 4999 }, (_, i) => ({ source: account(i), target: account((i + 1) % 5000) }));
    const result = finish(input(nodes, edges));
    assert.equal(result.structure.components[0].method, 'bfs-grid');
    assert.ok(result.structure.bounds.width < 7000); assert.ok(result.structure.bounds.height < 7000);
    assert.equal(new Set(result.nodes.map(node => `${node.x},${node.y}`)).size, 5000);
  }
});

test('layout is deterministic across input order, duplicate directions and self-loops', () => {
  const graph = hub(80), first = finish(graph);
  const reordered = { ...graph, nodes: [...graph.nodes].reverse(), edges: [...graph.edges].reverse() };
  assert.deepEqual(canonical(finish(reordered)), canonical(first));
  const repeated = { ...graph, edges: [...graph.edges, ...graph.edges.map(edge => ({ source: edge.target, target: edge.source })),
    ...graph.nodes.map(node => ({ source: node.id, target: node.id }))] };
  assert.deepEqual(canonical(finish(repeated)), canonical(first));
  assert.deepEqual(Array.from(finish(reordered).nodes, node => node.id), reordered.nodes.map(node => node.id));
});

test('new case inputs do not inherit coordinates, anchors or metadata from an earlier index', () => {
  const a = hub(30), b = hub(30); a.nodes[2].isMain = true; delete b.nodes[0].isMain;
  a.nodes.forEach(node => { node.x = 123; node.y = 123; });
  finish(a); const second = finish(b), independent = finish(JSON.parse(JSON.stringify(b)));
  assert.deepEqual(canonical(second), canonical(independent));
  assert.deepEqual(Array.from(second.structure.components[0].anchorIds), [account(0)]);
  assert.equal(second.structure.components[0].anchorReason, 'degree');
});

test('run cancellation yields during large connected and disconnected layouts', async () => {
  for (const graph of [hub(), input(Array.from({ length: 5000 }, (_, i) => ({ id: account(i) })))]) {
    const original = JSON.stringify(graph);
    for (const afterPause of [4, 100, 200]) {
      let pauses = 0, cancelled = false;
      const result = await layout.run(graph, { cancelled: () => cancelled,
        pause: async () => { if (++pauses === afterPause) cancelled = true; } });
      assert.equal(result, null); assert.equal(pauses, afterPause); assert.equal(JSON.stringify(graph), original);
    }
  }
  let pauses = 0;
  assert.equal(await layout.run(hub(), { cancelled: () => true, pause: async () => { pauses++; } }), null);
  assert.equal(pauses, 0);
  const completed = await layout.run(hub(80), { pause: async () => { pauses++; } });
  assert.ok(pauses > 0); assert.deepEqual(canonical(completed), canonical(finish(hub(80))));
});

test('empty graphs, isolated accounts and varied node sizes keep valid geometry', () => {
  const empty = finish(input([])); assert.equal(empty.nodes.length, 0); assert.equal(empty.structure.componentCount, 0);
  const single = finish(input([{ id: '0009007199254740993', isMain: true, size: 120 }]));
  assert.equal(single.nodes[0].id, '0009007199254740993'); assert.equal(single.structure.spacing, 136);
  assert.equal(single.structure.components[0].anchorReason, 'explicit');
  assert.ok(Number.isFinite(single.nodes[0].x) && Number.isFinite(single.nodes[0].y));
  const largeNode = hub(40); largeNode.nodes[21].size = 200;
  const spaced = finish(largeNode), center = spaced.nodes[0];
  assert.equal(spaced.structure.spacing, 216);
  assert.ok(spaced.nodes.slice(1).every(node => Math.hypot(node.x - center.x, node.y - center.y) >= 216 - 0.000001));
  assert.throws(() => finish(input([{ id: 9007199254740992 }])), /string node IDs/);
  assert.throws(() => finish(input([{ id: 'SYN' }, { id: 'SYN' }])), /unique string node IDs/);
});

test('worker protocol returns the same structural coordinates and request identity', () => {
  const sent = [], worker = vm.createContext({ postMessage: message => sent.push(message), importScripts() {} });
  vm.runInContext(readFileSync(new URL('../static/graph-layout.js', import.meta.url), 'utf8'), worker);
  const graph = hub(100);
  worker.onmessage({ data: { requestId: 'synthetic-42', input: graph } });
  assert.equal(sent.length, 1); assert.equal(sent[0].requestId, 'synthetic-42');
  assert.deepEqual(canonical(sent[0].result), canonical(finish(graph)));
  worker.onmessage({ data: { requestId: 'invalid-id', input: input([{ id: 42 }]) } });
  assert.match(sent[1].error, /string node IDs/); assert.equal(sent[1].requestId, 'invalid-id');
});
