import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
const context = vm.createContext({ setTimeout });
vm.runInContext(readFileSync(new URL('../static/graph-layout.js', import.meta.url), 'utf8'), context);
const layout = context.FundsGraphLayout;
const node = (id, more = {}) => ({ id, size: 36, width: 94, height: 80, offsetY: 22, ...more });
const edge = (source, target, more = {}) => ({ source, target, labelWidth: 104, total: '9999999999999999.99', currency: 'CNY', ...more });
const input = (nodes, edges = [], kind = 'auto') => ({ nodes, edges, kind, width: 1000, height: 700 });
const finish = graph => { const model = layout.simulation(graph); while (model.step()) {} return model.result(); };
const canonical = result => Array.from(result.nodes, p => [p.id, p.x, p.y]).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0);
const points = result => new Map(Array.from(result.nodes, p => [p.id, p]));
const bounds = (p, n) => ({ minX: p.x + (n.offsetX || 0) - n.width / 2, maxX: p.x + (n.offsetX || 0) + n.width / 2,
  minY: p.y + (n.offsetY || 0) - n.height / 2, maxY: p.y + (n.offsetY || 0) + n.height / 2 });
const overlaps = (a, b, gap = 0) => a.minX < b.maxX + gap - 1e-6 && b.minX < a.maxX + gap - 1e-6 && a.minY < b.maxY + gap - 1e-6 && b.minY < a.maxY + gap - 1e-6;
function assertSeparated(graph, result, gap = 0) {
  const p = points(result), boxes = graph.nodes.map(n => [n.id, bounds(p.get(n.id), n)]);
  for (let a = 0; a < boxes.length; a++) for (let b = a + 1; b < boxes.length; b++) assert.equal(overlaps(boxes[a][1], boxes[b][1], gap), false, `${boxes[a][0]} overlaps ${boxes[b][0]}`);
}
function oldCore() {
  return input([node('A', { x: 0, y: 0, fixed: true }), node('B', { x: 240, y: 0, fixed: true }), node('C', { x: 0, y: 220, fixed: true }), node('D', { x: 240, y: 220, fixed: true }), node('X'), node('Y'), node('Z')],
    [edge('A', 'D'), edge('B', 'C'), edge('A', 'B'), edge('A', 'X'), edge('B', 'X'), edge('X', 'Y'), edge('Z', 'Z')], 'incremental');
}

test('stable append retains every fixed coordinate and leaves all graph facts untouched', () => {
  const graph = oldCore(), before = JSON.stringify(graph), result = layout.placeIncremental(graph), p = points(result);
  for (const n of graph.nodes.filter(n => n.fixed)) assert.deepEqual([p.get(n.id).x, p.get(n.id).y], [n.x, n.y]);
  assertSeparated(graph, result, 12); assert.equal(JSON.stringify(graph), before);
  assert.equal(result.layout.fixedCount, 4); assert.equal(result.layout.addedCount, 3);
  assert.equal(result.layout.outsideCount, 3);
  assert.equal(result.nodes.some(n => 'total' in n || 'currency' in n || 'fixed' in n), false);
});

test('incremental spatial indexing terminates for finite coordinates beyond safe grid integers', () => {
  for (const sign of [-1, 1]) for (const farAnchor of [false, true]) {
    const isolated = vm.createContext({ setTimeout });
    vm.runInContext(readFileSync(new URL('../static/graph-layout.js', import.meta.url), 'utf8'), isolated);
    const far = sign * 1e19;
    const graph = input([node('old', { x: farAnchor ? far : 0, y: 0, fixed: true }), node('new')],
      [edge('old', 'new')], 'incremental');
    if (!farAnchor) graph.obstacles = [{ bounds: { minX: far, maxX: far, minY: -40, maxY: 40 } }];
    const before = JSON.stringify(graph); isolated.input = graph;
    // VM timeout safely exposes a non-progressing grid loop without hanging
    // the test process. It is a termination guard, not a speed benchmark.
    const result = vm.runInContext('FundsGraphLayout.placeIncremental(input)', isolated, { timeout: 1000 });
    assert.equal(result.nodes.length, 2);
    assert.ok(result.nodes.every(p => Number.isFinite(p.x) && Number.isFinite(p.y)));
    assert.deepEqual([result.nodes[0].x, result.nodes[0].y], [farAnchor ? far : 0, 0]);
    assert.equal(JSON.stringify(graph), before);
  }
});

test('new boxes stay outside the old crossing core and explicit old label/curve obstacles', () => {
  const graph = oldCore(); graph.obstacles = [
    { bounds: { minX: 300, maxX: 540, minY: -150, maxY: 430 } },
    { points: [{ x: -150, y: -300 }, { x: 500, y: -300 }, { x: 650, y: 200 }] },
    { points: [{ x: NaN, y: 1 }, { x: 1, y: 1 }] },
  ];
  const result = finish(graph), p = points(result), core = result.layout.protectedBounds;
  for (const n of graph.nodes.filter(n => !n.fixed)) {
    const box = bounds(p.get(n.id), n); assert.equal(overlaps(box, core), false); assert.equal(overlaps(box, graph.obstacles[0].bounds), false);
    assert.ok(box.maxY < -312 || box.minY > -288 || box.maxX < -162 || box.minX > 512);
  }
});

test('incremental geometry is deterministic across order and batches are cancellable', async () => {
  const graph = oldCore(), reordered = { ...graph, nodes: [...graph.nodes].reverse(), edges: [...graph.edges].reverse() };
  assert.deepEqual(canonical(finish(reordered)), canonical(layout.placeIncremental(graph)));
  let pauses = 0;
  const result = await layout.run(graph, { pause: async () => { pauses++; }, cancelled: () => pauses >= 2 });
  assert.equal(result, null); assert.equal(pauses, 2);
});

test('one old anchor can append near it while disconnected additions use external lanes', () => {
  const graph = input([node('old', { x: -1000.25, y: 2700.5, fixed: true }), node('new'), node('island')], [edge('old', 'new')], 'incremental');
  const result = finish(graph), p = points(result); assert.deepEqual([p.get('old').x, p.get('old').y], [-1000.25, 2700.5]);
  assert.ok(Math.hypot(p.get('new').x - p.get('old').x, p.get('new').y - p.get('old').y) < 600);
  assertSeparated(graph, result); assert.ok(result.layout.disconnectedCount >= 1);
});

test('many new leaves with long offset labels get finite nonoverlapping external positions', () => {
  const graph = oldCore(); graph.nodes = graph.nodes.filter(n => n.fixed);
  for (let i = 0; i < 100; i++) { graph.nodes.push(node(`N${String(i).padStart(3, '0')}`, { width: 90 + i % 7 * 20, height: 70 + i % 5 * 14, offsetX: i % 3 * 18, offsetY: 15 + i % 4 * 10 })); graph.edges.push(edge('A', `N${String(i).padStart(3, '0')}`)); }
  const result = finish(graph); assert.ok(result.nodes.every(p => Number.isFinite(p.x) && Number.isFinite(p.y))); assertSeparated(graph, result, 8);
});

test('SCC condensation puts a cycle and every downstream layer in a valid flow order', () => {
  const graph = input(['A', 'B', 'C', 'D', 'E'].map(id => node(id)), [edge('A', 'B'), edge('B', 'C'), edge('C', 'A'), edge('C', 'D'), edge('D', 'E')], 'dagre');
  const before = JSON.stringify(graph), result = finish(graph), p = points(result);
  assert.ok(p.get('D').x > Math.max(...['A', 'B', 'C'].map(id => p.get(id).x)));
  assert.ok(p.get('E').x > p.get('D').x); assertSeparated(graph, result, 8);
  assert.equal(result.layout.regions[0].rankCount, 3); assert.equal(result.layout.regions[0].cyclicBlockCount, 1); assert.equal(JSON.stringify(graph), before);
});

test('simple directed SCC cycles follow topology rather than account ID order', () => {
  for (const count of [12, 24, 25]) {
    const ids = Array.from({ length: count }, (_, i) => `C${String(i).padStart(2, '0')}`);
    const step = count === 25 ? 7 : 5;
    const graph = input(ids.map(id => node(id)), ids.map((id, i) => edge(id, ids[(i + step) % count])), 'dagre');
    const before = JSON.stringify(graph), result = finish(graph), p = points(result);
    const cross = (a, b, c, d) => {
      const orient = (u, v, w) => (v.x - u.x) * (w.y - u.y) - (v.y - u.y) * (w.x - u.x);
      return orient(a, b, c) * orient(a, b, d) < -1e-7 && orient(c, d, a) * orient(c, d, b) < -1e-7;
    };
    let crossings = 0;
    for (let a = 0; a < graph.edges.length; a++) for (let b = a + 1; b < graph.edges.length; b++) {
      const first = graph.edges[a], second = graph.edges[b];
      if (new Set([first.source, first.target, second.source, second.target]).size === 4 &&
          cross(p.get(first.source), p.get(first.target), p.get(second.source), p.get(second.target))) crossings++;
    }
    if (count <= 24) assert.equal(crossings, 0, 'a simple ring must not become an ID-ordered star polygon');
    else {
      const lengths = graph.edges.map(e => Math.hypot(p.get(e.source).x - p.get(e.target).x, p.get(e.source).y - p.get(e.target).y));
      assert.ok(lengths.filter(length => length > Math.min(...lengths) + 1e-7).length <= 3,
        'a large cycle keeps consecutive topology neighbors together; only the empty exterior return lane may span several grid cells');
    }
    assert.equal(result.layout.regions[0].simpleCycleBlockCount, 1);
    assert.deepEqual(canonical(finish({ ...graph, nodes: [...graph.nodes].reverse(), edges: [...graph.edges].reverse() })), canonical(result));
    assert.equal(JSON.stringify(graph), before); assertSeparated(graph, result, 8);
    if (count === 12) assert.deepEqual(canonical(finish({ ...graph, edges: [...graph.edges, ...graph.edges,
      ...ids.map(id => edge(id, id))] })), canonical(result), 'parallel relationships and self-loops do not change the non-self cycle order');
  }
  const branched = input(['A', 'B', 'C', 'D'].map(id => node(id)),
    [edge('A', 'B'), edge('B', 'C'), edge('C', 'D'), edge('D', 'A'), edge('A', 'C')], 'dagre');
  assert.equal(finish(branched).layout.regions[0].simpleCycleBlockCount, 0, 'a chorded SCC is not a simple ring');
  for (const count of [1, 2]) {
    const ids = ['A', 'B'].slice(0, count), graph = input(ids.map(id => node(id)), ids.map((id, i) => edge(id, ids[(i + 1) % count])), 'dagre');
    const result = finish(graph);
    assert.equal(result.layout.regions[0].simpleCycleBlockCount, count === 2 ? 1 : 0);
    assert.ok(result.nodes.every(p => Number.isFinite(p.x) && Number.isFinite(p.y))); assertSeparated(graph, result);
  }
});

for (const count of [25, 81]) test(`${count}-node large simple SCC cycles close outside every third-node complete label footprint`, () => {
  // Independent segment/slab intersection checks the center polyline only;
  // final clipped right-arc paths still belong to the G6 route-quality gate.
  const hits = (a, b, box) => {
    let start = 0, end = 1;
    for (const [axis, min, max] of [['x', box.minX - 8, box.maxX + 8], ['y', box.minY - 8, box.maxY + 8]]) {
      const delta = b[axis] - a[axis];
      if (Math.abs(delta) < 1e-9) { if (a[axis] <= min + 1e-7 || a[axis] >= max - 1e-7) return false; }
      else {
        const first = (min + 1e-7 - a[axis]) / delta, second = (max - 1e-7 - a[axis]) / delta;
        start = Math.max(start, Math.min(first, second)); end = Math.min(end, Math.max(first, second));
        if (start >= end) return false;
      }
    }
    return start < end;
  };
    const nodes = Array.from({ length: count }, (_, i) => node(`P${String(i).padStart(2, '0')}`, {
      width: [94, 150, 118][i % 3], height: [80, 140, 108][i % 3], offsetY: ([80, 140, 108][i % 3] - 36) / 2,
    }));
    const step = count === 25 ? 7 : 17, edges = nodes.map((n, i) => edge(n.id, nodes[(i + step) % count].id));
    const graph = input(nodes, edges, 'dagre'), before = JSON.stringify(graph), result = finish(graph), p = points(result);
    const closing = edges.find(e => e.target === nodes[0].id);
    for (const n of nodes) if (n.id !== closing.source && n.id !== closing.target) assert.equal(
      hits(p.get(closing.source), p.get(closing.target), bounds(p.get(n.id), n)), false,
      `the topology closing edge cannot cross ${n.id}'s body or fixed bottom label`);
    for (const e of edges) for (const n of nodes) if (n.id !== e.source && n.id !== e.target) assert.equal(
      hits(p.get(e.source), p.get(e.target), bounds(p.get(n.id), n)), false,
      `${e.source}->${e.target} must use the empty grid/perimeter lanes instead of a third-node footprint`);
    assertSeparated(graph, result, 12);
    const region = result.layout.regions[0], extent = Math.max(...nodes.map(n => Math.hypot(n.width, n.height) + 32));
    assert.ok(region.bounds.maxX - region.bounds.minX < (Math.ceil(Math.sqrt(count)) + 2) * extent);
    assert.ok(region.bounds.maxY - region.bounds.minY < (Math.ceil(Math.sqrt(count)) + 2) * extent, 'closed rings retain bounded-area packing instead of a giant circle');
    assert.deepEqual(canonical(finish({ ...graph, nodes: [...nodes].reverse(), edges: [...edges].reverse() })), canonical(result));
    assert.equal(JSON.stringify(graph), before, 'closing geometry cannot rewrite the real cycle direction or exact amounts');
});

test('multiline internal SCC relationships reserve midpoint clearance without expanding other SCCs', () => {
  for (const labelHeight of [37, 52, 800]) {
    const ids = 'ABCDEFGH'.split(''), small = 'IJKL'.split('');
    const graph = input([...ids, ...small].map(id => node(id)), [
      ...ids.map((id, i) => edge(id, ids[(i + 1) % ids.length], { labelHeight: i === 0 ? labelHeight : 13, fontSize: 13 })),
      ...small.map((id, i) => edge(id, small[(i + 1) % small.length], { labelHeight: 13 })),
    ], 'dagre');
    const before = JSON.stringify(graph), result = finish(graph), p = points(result);
    const baseline = finish({ ...graph, edges: graph.edges.map(e => ({ ...e, labelHeight: 13 })) }), base = points(baseline);
    assert.deepEqual(canonical(finish({ ...graph, edges: graph.edges.map(e => ({ ...e, labelHeight: 19 })) })), canonical(baseline), '13px and padded 19px single lines retain the existing geometry');
    assert.deepEqual(canonical(finish({ ...graph, edges: graph.edges.map(e => ({ ...e, fontSize: 28, labelHeight: 34 })) })), canonical(baseline), 'larger-font single lines use their own padded height as baseline');
    const relation = graph.edges[0], a = p.get(relation.source), b = p.get(relation.target);
    const label = { minX: (a.x + b.x - relation.labelWidth) / 2, maxX: (a.x + b.x + relation.labelWidth) / 2,
      minY: (a.y + b.y - labelHeight) / 2, maxY: (a.y + b.y + labelHeight) / 2 };
    for (const id of [relation.source, relation.target]) assert.equal(overlaps(label, bounds(p.get(id), graph.nodes.find(n => n.id === id)), 8), false,
      'the expanded edge midpoint must clear both complete endpoint boxes');
    const distance = (map, x, y) => Math.hypot(map.get(x).x - map.get(y).x, map.get(x).y - map.get(y).y);
    assert.ok(distance(p, 'A', 'B') > distance(base, 'A', 'B'));
    assert.ok(Math.abs(distance(p, 'I', 'J') - distance(base, 'I', 'J')) < 1e-7, 'only the affected SCC may expand');
    assert.equal(JSON.stringify(graph), before);
    assert.deepEqual(canonical(finish({ ...graph, nodes: [...graph.nodes].reverse(), edges: [...graph.edges].reverse() })), canonical(result));
  }
  const chorded = input('ABCD'.split('').map(id => node(id)), [edge('A', 'B', { fontSize: 13, labelHeight: 37 }),
    edge('B', 'C'), edge('C', 'D'), edge('D', 'A'), edge('A', 'C')], 'dagre');
  const result = finish(chorded), p = points(result), a = p.get('A'), b = p.get('B');
  const label = { minX: (a.x + b.x - 104) / 2, maxX: (a.x + b.x + 104) / 2,
    minY: (a.y + b.y - 37) / 2, maxY: (a.y + b.y + 37) / 2 };
  assert.equal(result.layout.regions[0].simpleCycleBlockCount, 0);
  for (const id of ['A', 'B']) assert.equal(overlaps(label, bounds(p.get(id), chorded.nodes.find(n => n.id === id)), 8), false);
});

for (const [kind, count] of [['force', 2], ['force', 81], ['partition', 2]]) test(`${kind} ${count}-node springs reserve measured multiline height while preserving single-line output`, () => {
  const nodes = Array.from({ length: count }, (_, i) => node(`M${String(i).padStart(2, '0')}`, {
    width: 60, height: 64, offsetY: 14, x: (i % 9) * 160, y: Math.floor(i / 9) * 140,
  }));
  const edges = count === 2 ? [edge(nodes[0].id, nodes[1].id, { labelWidth: 40 })]
    : nodes.map((n, i) => edge(n.id, nodes[(i + 1) % count].id, { labelWidth: 40 }));
  const graph = input(nodes, edges, kind), baseline = finish(graph), original = JSON.stringify(graph);
  for (const [fontSize, labelHeight] of [[13, 13], [13, 19], [28, 34]]) {
    const single = { ...graph, edges: edges.map(e => ({ ...e, fontSize, labelHeight })) };
    assert.deepEqual(finish(single), baseline, 'single-line height and font metadata must retain every output field');
  }
  const averageLength = result => { const p = points(result); return edges.reduce((sum, e) => sum +
    Math.hypot(p.get(e.source).x - p.get(e.target).x, p.get(e.source).y - p.get(e.target).y), 0) / edges.length; };
  const multiline = { ...graph, edges: edges.map(e => ({ ...e, fontSize: 13, labelHeight: 320 })) };
  const before = JSON.stringify(multiline), expanded = finish(multiline);
  assert.ok(averageLength(expanded) > averageLength(baseline) + 20,
    'a tall relationship must receive effective spring space instead of being ignored');
  assert.ok(expanded.nodes.every(p => Number.isFinite(p.x) && Number.isFinite(p.y))); assertSeparated(multiline, expanded, 8);
  if (kind === 'force') assert.equal(expanded.iterations, count <= 80 ? 160 : 110, 'height reservation cannot change the iteration contract');
  else assert.equal(expanded.layout.regions[0].method, 'partition-local-force');
  assert.deepEqual(canonical(finish({ ...multiline, nodes: [...nodes].reverse(), edges: [...multiline.edges].reverse() })), canonical(expanded));
  const parallel = { ...multiline, edges: [...multiline.edges, edge(nodes[0].id, nodes[1].id, { labelWidth: 340, labelHeight: 37, fontSize: 13 }),
    edge(nodes[0].id, nodes[1].id, { labelWidth: 40, labelHeight: 520, fontSize: 13 }),
    edge(nodes[1].id, nodes[0].id, { labelWidth: 120, labelHeight: 240, fontSize: 13 })] };
  assert.deepEqual(canonical(finish(parallel)), canonical(finish({ ...parallel, nodes: [...nodes].reverse(), edges: [...parallel.edges].reverse() })),
    'parallel and reversed relationships keep deterministic geometry when width and height priorities differ');
  assert.equal(JSON.stringify(multiline), before, 'multiline geometry cannot rewrite amounts, currencies, IDs or funds direction');
  assert.equal(JSON.stringify(graph), original);
});

test('layer crossing sweeps retain the best actual ordering on a known crossed pair', () => {
  const graph = input(['A', 'B', 'C', 'D'].map(id => node(id)), [edge('A', 'D'), edge('B', 'C'), edge('A', 'C')], 'dagre');
  const result = finish(graph), region = result.layout.regions[0], p = points(result);
  assert.equal(region.crossingBefore, 1); assert.equal(region.crossingAfter, 0); assert.ok((p.get('A').y - p.get('B').y) * (p.get('D').y - p.get('C').y) > 0);
});

test('flow layers measure full labels and allow long chains to grow beyond the viewport', () => {
  const graph = input(Array.from({ length: 40 }, (_, i) => node(`N${String(i).padStart(3, '0')}`)), Array.from({ length: 39 }, (_, i) => edge(`N${String(i).padStart(3, '0')}`, `N${String(i + 1).padStart(3, '0')}`, { labelWidth: 220 })), 'dagre');
  const result = finish(graph), p = points(result);
  for (let i = 1; i < graph.nodes.length; i++) assert.ok(p.get(graph.nodes[i].id).x - p.get(graph.nodes[i - 1].id).x >= 346);
  assert.ok(result.layout.regions[0].bounds.maxX - result.layout.regions[0].bounds.minX > graph.width); assertSeparated(graph, result);
});

test('automatic simple tree uses a horizontal backbone with balanced upper and lower branches', () => {
  const graph = input('ABCDEFGHIJK'.split('').map(id => node(id)), [edge('A', 'B'), edge('B', 'C'), edge('C', 'F'), edge('D', 'A'), edge('E', 'B'), edge('G', 'F'), edge('A', 'H'), edge('I', 'B'), edge('J', 'C'), edge('F', 'K')]);
  const before = JSON.stringify(graph), result = finish(graph), p = points(result), region = result.layout.regions[0];
  assert.equal(region.method, 'backbone-branches'); assert.deepEqual(Array.from(region.backboneIds), ['A', 'B', 'C', 'F']);
  assert.equal(new Set(['A', 'B', 'C', 'F'].map(id => p.get(id).y)).size, 1);
  assert.ok(p.get('A').x < p.get('B').x && p.get('B').x < p.get('C').x && p.get('C').x < p.get('F').x);
  for (const [branch, anchor] of [['D', 'A'], ['E', 'B'], ['G', 'F']]) assert.ok(p.get(branch).y < p.get(anchor).y);
  for (const [branch, anchor] of [['H', 'A'], ['I', 'B'], ['J', 'C'], ['K', 'F']]) assert.ok(p.get(branch).y > p.get(anchor).y);
  assertSeparated(graph, result, 12); assert.equal(JSON.stringify(graph), before);
});

test('automatic branch lanes separate unequal full text boxes and node offsets', () => {
  const graph = input([node('center'), ...Array.from({ length: 15 }, (_, i) => node(`leaf${i}`, { width: 80 + i * 5, height: 70 + i % 4 * 75, offsetY: i % 4 * 12 }))], Array.from({ length: 15 }, (_, i) => edge(`leaf${i}`, 'center')));
  assertSeparated(graph, finish(graph), 8);
});

test('auto keeps singletons, two-point and reversed pure paths correct without changing direction facts', () => {
  for (const count of [0, 1, 2, 6]) {
    const graph = input(Array.from({ length: count }, (_, i) => node(`N${i}`)), Array.from({ length: Math.max(0, count - 1) }, (_, i) => edge(`N${i + 1}`, `N${i}`)));
    const before = JSON.stringify(graph), result = finish(graph), p = points(result);
    assert.equal(result.nodes.length, count); if (count >= 2) assert.ok(p.get('N1').x < p.get('N0').x); assert.equal(JSON.stringify(graph), before); assertSeparated(graph, result);
  }
});

test('dense bridge-connected blocks get separate local regions packed from actual final label bounds', () => {
  const nodes = Array.from({ length: 18 }, (_, i) => node(`N${String(i).padStart(2, '0')}`, { width: 100 + i % 4 * 40, height: 80 + i % 3 * 30, offsetY: 20 + i % 2 * 30 })), edges = [];
  for (const start of [0, 9]) for (let i = start; i < start + 9; i++) for (let j = i + 1; j < start + 9; j++) edges.push(edge(nodes[i].id, nodes[j].id));
  edges.push(edge(nodes[4].id, nodes[12].id)); const graph = input(nodes, edges, 'partition'), result = finish(graph);
  assert.equal(result.layout.componentCount, 1); assert.equal(result.layout.regions.length, 2); assertSeparated(graph, result, 8);
  const [a, b] = result.layout.regions.map(region => region.bounds); assert.equal(overlaps(a, b, 50), false);
  assert.deepEqual(canonical(finish({ ...graph, nodes: [...nodes].reverse(), edges: [...edges].reverse() })), canonical(result));
});

test('bounded partition batches retain high-fanout residual leaves at the 64-node boundary', () => {
  for (const count of [64, 65, 81, 160]) for (const crossLinked of [false, true]) {
    const nodes = Array.from({ length: count }, (_, i) => node(`H${String(i).padStart(3, '0')}`));
    const edges = nodes.slice(1).map(n => edge(nodes[0].id, n.id));
    if (crossLinked) edges.push(edge(nodes[1].id, nodes[2].id));
    const graph = input(nodes, edges, 'partition'), before = JSON.stringify(graph), result = finish(graph);
    assert.ok(result.layout.regions.length <= (count <= 64 ? 1 : Math.ceil(count / 48)), 'removed hubs must not strand each remaining leaf in a separate region');
    assert.ok(result.layout.regions.every(region => region.nodeIds.length > 1));
    assert.ok(result.layout.regions.every(region => region.nodeIds.length <= (count <= 64 ? 64 : 48)));
    assert.equal(result.nodes.length, count); assertSeparated(graph, result, 8); assert.equal(JSON.stringify(graph), before);
    assert.deepEqual(canonical(finish({ ...graph, nodes: [...nodes].reverse(), edges: [...edges].reverse() })), canonical(result));
  }
});

test('bounded partition repair preserves bridge-separated dense groups and their tree tendrils', () => {
  const left = Array.from({ length: 10 }, (_, i) => node(`L${i}`)), right = Array.from({ length: 10 }, (_, i) => node(`R${i}`));
  const leaves = Array.from({ length: 55 }, (_, i) => node(`T${String(i).padStart(2, '0')}`));
  const edges = [];
  for (const block of [left, right]) for (let a = 0; a < block.length; a++) for (let b = a + 1; b < block.length; b++) edges.push(edge(block[a].id, block[b].id));
  edges.push(edge('L0', 'R0'), ...leaves.map(n => edge('L1', n.id)));
  const graph = input([...left, ...right, ...leaves], edges, 'partition'), before = JSON.stringify(graph), result = finish(graph);
  assert.equal(result.layout.regions.length, 3);
  for (const region of result.layout.regions) {
    const rightCount = region.nodeIds.filter(id => id.startsWith('R')).length;
    assert.ok(rightCount === 0 || rightCount === region.nodeIds.length, 'repair cannot mix the two bridge-separated dense groups');
  }
  assertSeparated(graph, result, 8); assert.equal(JSON.stringify(graph), before);
});

test('disconnected components and dense auto partitions retain all membership and money strings', () => {
  const graph = input(Array.from({ length: 16 }, (_, i) => node(`N${i}`)), [edge('N0', 'N1'), edge('N2', 'N3'), ...Array.from({ length: 5 }, (_, i) => i).flatMap(i => Array.from({ length: 5 }, (_, j) => j).filter(j => i !== j).map(j => edge(`N${i + 5}`, `N${j + 5}`)))]);
  const before = JSON.stringify(graph), result = finish(graph); assert.equal(result.nodes.length, graph.nodes.length); assertSeparated(graph, result, 8); assert.equal(JSON.stringify(graph), before);
  assert.ok(result.layout.regions.some(region => region.method === 'partition-local-force'));
});

test('large SCC and deep flow do not recurse; all new algorithms yield cancellation', async () => {
  for (const kind of ['auto', 'dagre', 'partition', 'incremental']) {
    const nodes = Array.from({ length: 2000 }, (_, i) => node(`N${String(i).padStart(4, '0')}`, kind === 'incremental' && i < 3 ? { fixed: true, x: i * 180, y: 0 } : {}));
    const graph = input(nodes, nodes.slice(1).map((n, i) => edge(nodes[i].id, n.id)), kind);
    let pauses = 0; assert.equal(await layout.run(graph, { pause: async () => { pauses++; }, cancelled: () => pauses >= 3 }), null); assert.equal(pauses, 3);
  }
  const nodes = Array.from({ length: 2000 }, (_, i) => node(`N${String(i).padStart(4, '0')}`));
  const result = finish(input(nodes, nodes.map((n, i) => edge(n.id, nodes[(i + 1) % nodes.length].id)), 'dagre'));
  assert.equal(result.nodes.length, 2000); assert.ok(result.nodes.every(p => Number.isFinite(p.x) && Number.isFinite(p.y)));
});

test('worker protocol supports new algorithms and request identity', () => {
  for (const kind of ['auto', 'dagre', 'partition', 'incremental']) {
    const graph = oldCore(); graph.kind = kind; const messages = [];
    const worker = vm.createContext({ importScripts() {}, postMessage(message) { messages.push(message); } });
    vm.runInContext(readFileSync(new URL('../static/graph-layout.js', import.meta.url), 'utf8'), worker); worker.onmessage({ data: { requestId: 'new-layout-contract', input: graph } });
    assert.equal(messages[0].requestId, 'new-layout-contract'); assert.deepEqual(canonical(messages[0].result), canonical(finish(graph)));
  }
});

test('bulk external lanes freeze large old graphs and pack all new text boxes outside old paths', () => {
  const nodes = Array.from({ length: 600 }, (_, i) => node(`O${String(i).padStart(4, '0')}`, { fixed: true, x: i % 30 * 180, y: Math.floor(i / 30) * 150 }));
  for (let i = 0; i < 1000; i++) nodes.push(node(`N${String(i).padStart(4, '0')}`, { width: 94 + i % 9 * 24, height: 80 + i % 6 * 18, offsetX: i % 4 * 12, offsetY: i % 3 * 20 }));
  const graph = input(nodes, nodes.slice(600).map((n, i) => edge(nodes[i % 600].id, n.id)), 'incremental');
  graph.externalOnly = true; graph.obstacles = [{ points: [{ x: -500, y: -600 }, { x: 8000, y: 2000 }] }, { bounds: { minX: 8100, maxX: 8500, minY: 100, maxY: 300 } }];
  const before = JSON.stringify(graph), result = layout.placeIncremental(graph), p = points(result);
  for (const n of nodes.slice(0, 600)) assert.deepEqual([p.get(n.id).x, p.get(n.id).y], [n.x, n.y]);
  for (const n of nodes.slice(600)) assert.ok(bounds(p.get(n.id), n).minX >= 8548);
  assertSeparated({ nodes: nodes.slice(600) }, result, 20);
  assert.equal(result.layout.quality, 'external-lanes'); assert.equal(result.layout.addedCount, 1000); assert.equal(result.interactions, 1000); assert.equal(JSON.stringify(graph), before);
  assert.deepEqual(canonical(finish({ ...graph, nodes: [...nodes].reverse(), edges: [...graph.edges].reverse() })), canonical(result));
});

test('automatic high fanout and long paths choose compact local regions instead of an extremely thin backbone', () => {
  for (const star of [true, false]) {
    const nodes = Array.from({ length: 40 }, (_, i) => node(`N${String(i).padStart(3, '0')}`));
    const graph = input(nodes, nodes.slice(1).map((n, i) => edge(nodes[star ? 0 : i].id, n.id))), result = finish(graph);
    assert.ok(result.layout.regions.every(region => region.method === 'partition-local-force'));
    assertSeparated(graph, result, 8);
    const region = result.layout.regions[0].bounds, w = region.maxX - region.minX, h = region.maxY - region.minY;
    assert.ok(w / h < 6 && h / w < 6);
  }
});

test('simple eleven-node backbone uses independent horizontal text bands without additive excess spacing', () => {
  const graph = input('ABCDEFGHIJK'.split('').map(id => node(id, { width: 77, height: 79, offsetY: 21.5 })), [edge('A', 'B'), edge('B', 'C'), edge('C', 'F'), edge('D', 'A'), edge('E', 'B'), edge('G', 'F'), edge('A', 'H'), edge('I', 'B'), edge('J', 'C'), edge('F', 'K')].map(e => ({ ...e, labelWidth: 108.8525, labelHeight: 18 })));
  const result = finish(graph), region = result.layout.regions[0].bounds;
  assert.ok(region.maxX - region.minX >= 580 && region.maxX - region.minX <= 640);
  assert.ok(region.maxY - region.minY >= 340 && region.maxY - region.minY <= 390);
  assertSeparated(graph, result, 12);
});

test('long overhead amounts clear node bodies and multiline branch labels clear full offset endpoint boxes', () => {
  const horizontal = input([node('A'), node('B')], [edge('A', 'B', { labelWidth: 420, labelHeight: 36 })]), horizontalResult = finish(horizontal), hp = points(horizontalResult);
  assert.ok(hp.get('B').x - hp.get('A').x >= 460);
  const label = { minX: (hp.get('A').x + hp.get('B').x) / 2 - 210, maxX: (hp.get('A').x + hp.get('B').x) / 2 + 210,
    minY: hp.get('A').y - 44, maxY: hp.get('A').y - 8 };
  for (const n of horizontal.nodes) assert.equal(overlaps(label, bounds(hp.get(n.id), { width: n.size, height: n.size })), false);
  const graph = input(['A', 'B', 'C', 'D', 'E', 'F'].map((id, i) => node(id, { height: 80 + i % 3 * 36, offsetY: 22 + i % 3 * 18 })),
    [edge('A', 'B'), edge('B', 'C'), edge('D', 'A', { labelWidth: 320, labelHeight: 92 }), edge('A', 'E', { labelWidth: 280, labelHeight: 76 }), edge('F', 'C', { labelWidth: 360, labelHeight: 110 })]);
  const before = JSON.stringify(graph), result = finish(graph), p = points(result);
  assertSeparated(graph, result, 12);
  for (const e of graph.edges.filter(e => e.labelHeight > 13)) {
    const a = p.get(e.source), b = p.get(e.target), box = { minX: (a.x + b.x) / 2 - e.labelWidth / 2, maxX: (a.x + b.x) / 2 + e.labelWidth / 2,
      minY: (a.y + b.y) / 2 - e.labelHeight / 2, maxY: (a.y + b.y) / 2 + e.labelHeight / 2 };
    for (const id of [e.source, e.target]) assert.equal(overlaps(box, bounds(p.get(id), graph.nodes.find(n => n.id === id))), false, `${e.source}-${e.target} midpoint label hits ${id}`);
  }
  assert.equal(JSON.stringify(graph), before);
});

test('three nearly equal dense regions choose discrete two-column packing instead of a narrow three-row strip', () => {
  const nodes = Array.from({ length: 18 }, (_, i) => node(`SYN-P${String(i + 1).padStart(2, '0')}`, { width: 170, height: 79, offsetY: 21.5 })), edges = [];
  for (const start of [0, 6, 12]) for (let i = start; i < start + 6; i++) for (let j = i + 1; j < start + 6; j++) edges.push(edge(nodes[i].id, nodes[j].id, { labelWidth: 148.85, labelHeight: 18 }));
  edges.push(edge(nodes[4].id, nodes[6].id), edge(nodes[11].id, nodes[12].id));
  const graph = { ...input(nodes, edges, 'partition'), width: 709, height: 573 }, before = JSON.stringify(graph);
  for (const chromeObstacles of [[], [{ bounds: { minX: 509, maxX: 699, minY: 350, maxY: 563 } }, { bounds: { minX: 12, maxX: 400, minY: 531, maxY: 563 } }]]) {
    const result = finish({ ...graph, chromeObstacles }), packing = result.layout.packing;
    assert.equal(result.layout.regions.length, 3); assert.equal(packing.rows, 2);
    assert.ok(packing.candidateCount > 1 && packing.candidateCount < 64);
    assert.ok(packing.fitZoom > (chromeObstacles.length ? 0.35 : 0.45));
    assert.equal(new Set(result.layout.regions.map(region => region.bounds.minY)).size, 2);
    assertSeparated(graph, result, 12);
    for (const region of result.layout.regions) for (const id of region.nodeIds) {
      const n = nodes.find(n => n.id === id), box = bounds(points(result).get(id), n);
      assert.ok(box.minX >= region.bounds.minX - 1e-6 && box.maxX <= region.bounds.maxX + 1e-6 && box.minY >= region.bounds.minY - 1e-6 && box.maxY <= region.bounds.maxY + 1e-6);
    }
  }
  assert.equal(JSON.stringify(graph), before);
});

for(const count of [80,81])test(`force boundary: ${count} coincident hub nodes separate complete text footprints beyond viewport capacity`,()=>{
  const nodes=Array.from({length:count},(_,i)=>node(`SYN-BOUNDARY-${String(i).padStart(3,'0')}`,{x:0,y:0}));
  const graph={...input(nodes,nodes.slice(1).map(n=>edge(nodes[0].id,n.id)),'force'),width:900,height:600};
  const before=JSON.stringify(graph),result=finish(graph);
  assert.equal(result.nodes.length,count);assert.ok(result.nodes.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)));
  assertSeparated(graph,result);
  assert.equal(JSON.stringify(graph),before,'geometry algorithms cannot mutate graph directions or monetary facts');
  // Eighty 94 x 80 footprints already exceed this viewport's area. Geometry
  // must grow in world space; fitting them is the camera's separate task.
  const boxes=nodes.map(n=>bounds(points(result).get(n.id),n));
  assert.ok(Math.max(...boxes.map(b=>b.maxX))-Math.min(...boxes.map(b=>b.minX))>graph.width||
    Math.max(...boxes.map(b=>b.maxY))-Math.min(...boxes.map(b=>b.minY))>graph.height);
});

for(const size of [56,80])test(`24-account backbone reserves four pixels beside amount labels for ${size}px bodies`,()=>{
  const nodes=Array.from({length:24},(_,i)=>node(`SYN-CHAIN-${String(i).padStart(3,'0')}`,
    {size,width:100,height:size+42,offsetY:21}));
  const edges=nodes.slice(1).map((n,i)=>edge(nodes[i].id,n.id,{labelWidth:142,labelHeight:18}));
  const graph=input(nodes,edges),before=JSON.stringify(graph),result=finish(graph),p=points(result);
  assert.ok(result.layout.regions.every(region=>region.method==='backbone-branches'));
  for(const relation of edges){
    const source=p.get(relation.source),target=p.get(relation.target),radius=size/2;
    assert.equal(source.y,target.y,'the accepted backbone keeps its horizontal spine');
    const left=Math.min(source.x,target.x),right=Math.max(source.x,target.x);
    // G6 clips the straight horizontal path at the body boundary. Equal body
    // radii put getPoint(.5), and therefore the centered amount, at this midpoint.
    const anchor=(left+radius+right-radius)/2,minX=anchor-relation.labelWidth/2,maxX=anchor+relation.labelWidth/2;
    assert.ok(minX>=left+radius+4-1e-7,`${relation.id||relation.source} amount touches left ${size}px body`);
    assert.ok(maxX<=right-radius-4+1e-7,`${relation.id||relation.target} amount touches right ${size}px body`);
  }
  assert.equal(JSON.stringify(graph),before,'layout cannot rewrite account direction or exact monetary strings');
});
