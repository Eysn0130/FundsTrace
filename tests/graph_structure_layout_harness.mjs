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

test('structural layout consumes measured text extents without changing large-graph work', () => {
  const graph = hub(5000); graph.nodes[12].width = 180; graph.nodes[13].height = 92;
  const result = finish(graph);
  assert.equal(result.structure.spacing, 196);
  assert.ok(result.interactions < graph.nodes.length * 10);
  const points = result.nodes, center = points[0];
  assert.ok(points.slice(1).every(point => Math.hypot(point.x - center.x, point.y - center.y) >= 196 - 0.000001));
  const offsetGraph = hub(20); offsetGraph.nodes[4].height = 120; offsetGraph.nodes[4].offsetY = 42;
  assert.equal(finish(offsetGraph).structure.spacing, 220);
});

function smallForce(count = 24) {
  return { kind: 'force', width: 720, height: 460,
    nodes: Array.from({ length: count }, (_, i) => ({ id: account(i), x: 360, y: 230, size: 36,
      width: i % 3 ? 128 : 230, height: i % 2 ? 74 : 108 })),
    edges: Array.from({ length: count - 1 }, (_, i) => ({ source: account(0), target: account(i + 1), labelWidth: i % 2 ? 160 : 92,
      total: '9999999999999999.99', currency: 'CNY' })) };
}

test('small force separates complete node-label boxes and can extend beyond the viewport', () => {
  const graph = smallForce(80), original = JSON.stringify(graph), result = finish(graph);
  const dimensions = new Map(graph.nodes.map(node => [node.id, node]));
  assert.equal(result.nodes.length, graph.nodes.length);
  for (let i = 0; i < result.nodes.length; i++) for (let j = i + 1; j < result.nodes.length; j++) {
    const a = result.nodes[i], b = result.nodes[j], da = dimensions.get(a.id), db = dimensions.get(b.id);
    assert.ok(Math.abs(a.x - b.x) >= (da.width + db.width) / 2 + 18 - 0.000001 ||
      Math.abs(a.y - b.y) >= (da.height + db.height) / 2 + 18 - 0.000001, `${a.id}/${b.id} overlap`);
  }
  assert.ok(result.nodes.some(node => node.x < 0 || node.x > graph.width || node.y < 0 || node.y > graph.height));
  assert.equal(JSON.stringify(graph), original);
  assert.ok(result.nodes.every(node => Object.keys(node).sort().join(',') === 'id,x,y'));
});

test('small force is deterministic across input order, retains original output order and uses edge label width', () => {
  const graph = smallForce(), first = finish(graph);
  const reversed = { ...graph, nodes: [...graph.nodes].reverse(), edges: [...graph.edges].reverse() };
  assert.deepEqual(canonical(finish(reversed)), canonical(first));
  assert.deepEqual(Array.from(finish(reversed).nodes, node => node.id), reversed.nodes.map(node => node.id));
  const pair = { kind: 'force', width: 400, height: 300,
    nodes: [{ id: 'SYN-A', x: 150, y: 150, size: 36, width: 180, height: 64 }, { id: 'SYN-B', x: 250, y: 150, size: 36, width: 140, height: 64 }],
    edges: [{ source: 'SYN-A', target: 'SYN-B', labelWidth: 260 }] };
  const long = finish(pair).nodes, short = finish({ ...pair, edges: [{ ...pair.edges[0], labelWidth: 0 }] }).nodes;
  const length = nodes => Math.hypot(nodes[0].x - nodes[1].x, nodes[0].y - nodes[1].y);
  assert.ok(length(long) > 260 + 90 + 70 + 24);
  assert.ok(length(long) > length(short) + 150);
});

test('small force separates actual below-node label boxes with different heights and offsets', () => {
  const graph = smallForce(48);
  graph.nodes.forEach((node, i) => {
    node.width = i % 2 ? 140 : 210;
    node.height = [70, 120, 190][i % 3];
    node.offsetY = (node.height - node.size) / 2;
    node.offsetX = i % 4 ? 0 : 12;
  });
  const original = JSON.stringify(graph), first = finish(graph);
  const dimensions = new Map(graph.nodes.map(node => [node.id, node]));
  for (let i = 0; i < first.nodes.length; i++) for (let j = i + 1; j < first.nodes.length; j++) {
    const a = first.nodes[i], b = first.nodes[j], da = dimensions.get(a.id), db = dimensions.get(b.id);
    assert.ok(Math.abs(a.x + da.offsetX - b.x - db.offsetX) >= (da.width + db.width) / 2 + 18 - 0.000001 ||
      Math.abs(a.y + da.offsetY - b.y - db.offsetY) >= (da.height + db.height) / 2 + 18 - 0.000001, `${a.id}/${b.id} actual boxes overlap`);
  }
  const reversed = { ...graph, nodes: [...graph.nodes].reverse(), edges: [...graph.edges].reverse() };
  assert.deepEqual(canonical(finish(reversed)), canonical(first));
  assert.deepEqual(Array.from(finish(reversed).nodes, node => node.id), reversed.nodes.map(node => node.id));
  assert.equal(JSON.stringify(graph), original);
  assert.ok(first.nodes.every(node => Object.keys(node).sort().join(',') === 'id,x,y'));
});

test('eleven-account first-screen tree keeps measured label spacing without expanding ordinary edges to hundreds of pixels', () => {
  // Synthetic browser regression: a compact prior layered layout, 679x573
  // viewport, complete node text boxes and measured relationship text widths.
  const initial = { B: [413.6666666666667, 255.57142857142858], K: [585.5, 441.14285714285717],
    H: [757.3333333333334, 255.57142857142858], G: [585.5, 131.85714285714286],
    I: [757.3333333333334, 131.85714285714286], F: [585.5, 379.2857142857143],
    D: [241.83333333333334, 131.85714285714286], J: [929.1666666666666, 131.85714285714286],
    C: [585.5, 317.42857142857144], A: [585.5, 193.71428571428572], E: [413.6666666666667, 131.85714285714286] };
  const graph = { kind: 'force', width: 679, height: 573,
    nodes: Object.entries(initial).map(([id, [x, y]]) => ({ id: `SYN-${id}`, x, y, size: 36,
      width: id === 'A' ? 93.61181640625 : 77, height: 79, offsetX: 0, offsetY: 21.5 })),
    edges: ['IB', 'JC', 'AB', 'DA', 'CF', 'BC', 'FK', 'AH', 'EB', 'GF'].map(pair => ({ source: `SYN-${pair[0]}`,
      target: `SYN-${pair[1]}`, labelWidth: pair === 'AB' ? 119.6943359375 : ['FK', 'EB', 'GF'].includes(pair) ? 101.62255859375 : 108.8525390625 })) };
  const original = JSON.stringify(graph), result = finish(graph), dimensions = new Map(graph.nodes.map(node => [node.id, node]));
  const byId = new Map(result.nodes.map(node => [node.id, node]));
  const box = node => { const d = dimensions.get(node.id); return { minX: node.x + d.offsetX - d.width / 2,
    maxX: node.x + d.offsetX + d.width / 2, minY: node.y + d.offsetY - d.height / 2, maxY: node.y + d.offsetY + d.height / 2 }; };
  const boxes = result.nodes.map(box);
  assert.ok(Math.max(...boxes.map(b => b.maxX)) - Math.min(...boxes.map(b => b.minX)) <= 900);
  assert.ok(Math.max(...boxes.map(b => b.maxY)) - Math.min(...boxes.map(b => b.minY)) <= 700);
  for (const edge of graph.edges) {
    const a = byId.get(edge.source), b = byId.get(edge.target), length = Math.hypot(a.x - b.x, a.y - b.y);
    assert.ok(length >= 150 && length <= 280, `${edge.source}/${edge.target} length ${length}`);
  }
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const a = boxes[i], b = boxes[j];
    assert.ok(a.maxX + 18 <= b.minX + 1e-7 || b.maxX + 18 <= a.minX + 1e-7 ||
      a.maxY + 18 <= b.minY + 1e-7 || b.maxY + 18 <= a.minY + 1e-7);
  }
  assert.equal(JSON.stringify(graph), original);
});

test('small force yields cancellation before final geometry and leaves source facts unchanged', async () => {
  const graph = smallForce(80), original = JSON.stringify(graph);
  let pauses = 0, cancelled = false;
  const result = await layout.run(graph, { cancelled: () => cancelled,
    pause: async () => { if (++pauses === 4) cancelled = true; } });
  assert.equal(result, null); assert.equal(pauses, 4); assert.equal(JSON.stringify(graph), original);
});

function largeForce(count, shape = 'hub') {
  const graph = smallForce(count);
  graph.nodes.forEach((node, i) => {
    node.size = [36, 44, 96][i % 3]; node.width = [72, 180, 310][i % 3];
    node.height = [74, 110, 180][i % 3]; node.offsetX = [-18, 0, 12][i % 3];
    node.offsetY = (Math.max(node.size, node.height) - node.size) / 2;
    if (shape === 'disconnected') { node.x = (i % 7) * 20; node.y = Math.floor(i / 7) % 5 * 20; }
  });
  if (shape === 'disconnected') graph.edges = graph.edges.filter((_, i) => i % 4 === 0);
  return graph;
}

// Independent spatial checks cover every intersecting cell and complete boxes,
// including offset labels. No timing assumption or sampled pair assertion.
function assertCompleteForceBoxes(graph, result) {
  const dimensions = new Map(graph.nodes.map(node => [node.id, node])), cells = new Map(), boxes = [], cellSize = 160;
  for (const point of result.nodes) {
    const d = dimensions.get(point.id), width = Math.max(d.size, d.width), height = Math.max(d.size, d.height);
    const box = { minX: point.x + d.offsetX - width / 2 - 9, maxX: point.x + d.offsetX + width / 2 + 9,
      minY: point.y + d.offsetY - height / 2 - 9, maxY: point.y + d.offsetY + height / 2 + 9 };
    const ids = new Set(), keys = [];
    for (let x = Math.floor(box.minX / cellSize); x <= Math.floor(box.maxX / cellSize); x++)
      for (let y = Math.floor(box.minY / cellSize); y <= Math.floor(box.maxY / cellSize); y++) {
        const key = `${x},${y}`; keys.push(key); for (const i of cells.get(key) || []) ids.add(i);
      }
    for (const i of ids) {
      const b = boxes[i];
      assert.ok(box.maxX <= b.minX + 1e-6 || b.maxX <= box.minX + 1e-6 || box.maxY <= b.minY + 1e-6 || b.maxY <= box.minY + 1e-6,
        `${point.id}/${result.nodes[i].id} complete text/body boxes overlap`);
    }
    const id = boxes.push(box) - 1;
    for (const key of keys) { if (!cells.has(key)) cells.set(key, []); cells.get(key).push(id); }
  }
  return boxes;
}

for (const [count, shape] of [[81, 'hub'], [400, 'hub'], [1001, 'disconnected']]) {
  test(`large force: ${count} ${shape} complete measured footprints remain separate without viewport clamping`, () => {
    const graph = largeForce(count, shape), original = JSON.stringify(graph), result = finish(graph);
    assert.equal(result.nodes.length, count); assert.ok(result.nodes.every(node => Number.isFinite(node.x) && Number.isFinite(node.y)));
    const boxes = assertCompleteForceBoxes(graph, result);
    assert.ok(Math.max(...boxes.map(box => box.maxX)) - Math.min(...boxes.map(box => box.minX)) > graph.width ||
      Math.max(...boxes.map(box => box.maxY)) - Math.min(...boxes.map(box => box.minY)) > graph.height);
    assert.equal(result.iterations, count > 1000 ? 70 : 110);
    // 13x13 coarse cells + at most 24 exact neighbors in each nearby cell.
    // The final phase performs at most 24 spatial queries per account.
    assert.ok(result.interactions <= count * (result.iterations * 400 + 24));
    assert.equal(JSON.stringify(graph), original);
    assert.deepEqual(Array.from(result.nodes, node => node.id), graph.nodes.map(node => node.id));
    assert.ok(result.nodes.every(node => Object.keys(node).sort().join(',') === 'id,x,y'));
  });
}

test('large force geometry is deterministic across node/edge order with directional money facts unchanged', () => {
  const graph = largeForce(400), original = JSON.stringify(graph), first = finish(graph);
  const reversed = { ...graph, nodes: [...graph.nodes].reverse(), edges: [...graph.edges].reverse() };
  assert.deepEqual(canonical(finish(reversed)), canonical(first));
  assert.equal(JSON.stringify(graph), original);
});

test('large force can cancel during both simulation and bounded final separation without writing source positions', async () => {
  const graph = largeForce(400), original = JSON.stringify(graph);
  for (const afterPause of [3, 112]) {
    let pauses = 0, cancelled = false;
    const result = await layout.run(graph, { cancelled: () => cancelled,
      pause: async () => { if (++pauses === afterPause) cancelled = true; } });
    assert.equal(result, null); assert.equal(pauses, afterPause); assert.equal(JSON.stringify(graph), original);
  }
});

function assertFitVisible(result, bounds, obstacles = []) {
  assert.ok(result.fits);
  const world = { x: (bounds.minX + bounds.maxX) / 2, y: (bounds.minY + bounds.maxY) / 2 };
  const screen = { minX: result.center.x + (bounds.minX - world.x) * result.zoom,
    maxX: result.center.x + (bounds.maxX - world.x) * result.zoom,
    minY: result.center.y + (bounds.minY - world.y) * result.zoom,
    maxY: result.center.y + (bounds.maxY - world.y) * result.zoom };
  const viewport = result.viewport;
  assert.ok(screen.minX >= viewport.minX - 1e-7 && screen.maxX <= viewport.maxX + 1e-7);
  assert.ok(screen.minY >= viewport.minY - 1e-7 && screen.maxY <= viewport.maxY + 1e-7);
  for (const obstacle of obstacles) {
    const b = obstacle.bounds;
    assert.ok(screen.maxX <= b.minX || screen.minX >= b.maxX || screen.maxY <= b.minY || screen.minY >= b.maxY);
  }
}

test('fit camera uses corner space instead of subtracting minimap height across the canvas', () => {
  const bounds = { minX: -200, minY: -200, maxX: 300, maxY: 400 };
  const obstacles = [{ key: 'minimap', bounds: { minX: 970, minY: 520, maxX: 1180, maxY: 780 } },
    { key: 'legend', bounds: { minX: 20, minY: 740, maxX: 420, maxY: 780 } }];
  const graph = { bounds, width: 1200, height: 800, obstacles, padding: 24 };
  const result = layout.fitViewport(graph), oldFullStripZoom = (800 - 260 - 48) / 600;
  assert.ok(result.zoom > oldFullStripZoom);
  assertFitVisible(result, bounds, obstacles);
  assert.equal(result.center.x, (result.viewport.minX + result.viewport.maxX) / 2);
  assert.equal(result.worldCenter.x, 50); assert.equal(result.worldCenter.y, 100);
  assert.equal(JSON.stringify(graph), JSON.stringify({ bounds, width: 1200, height: 800, obstacles, padding: 24 }));
});

test('fit camera avoids a wide top status strip and correctly projects negative coordinates', () => {
  const bounds = { minX: -830, minY: -470, maxX: -230, maxY: -70 };
  const obstacles = [{ key: 'status', bounds: { minX: 0, minY: 0, maxX: 1000, maxY: 74 } }];
  const result = layout.fitViewport({ bounds, width: 1000, height: 700, obstacles, maxZoom: 1.6, padding: 24 });
  assertFitVisible(result, bounds, obstacles);
  assert.ok(result.viewport.minY >= 98); assert.equal(result.worldCenter.x, -530);
});

test('fit camera respects zoom caps, reports unavoidable minimum zoom and rejects invalid geometry', () => {
  const tiny = { bounds: { minX: 4, minY: 8, maxX: 14, maxY: 18 }, width: 1000, height: 700 };
  assert.equal(layout.fitViewport(tiny).zoom, 1.6);
  assert.equal(layout.fitViewport({ ...tiny, maxZoom: 1.2 }).zoom, 1.2);
  assert.equal(layout.fitViewport({ ...tiny, maxZoom: 0.01, minZoom: 0.02 }).zoom, 0.02);
  const huge = layout.fitViewport({ ...tiny, bounds: { minX: -1e6, minY: -1e6, maxX: 1e6, maxY: 1e6 } });
  assert.equal(huge.zoom, 0.02); assert.equal(huge.fits, false);
  for (const bounds of [null, {}, { minX: 5, minY: 0, maxX: 2, maxY: 10 }, { minX: NaN, minY: 0, maxX: 3, maxY: 4 }]) {
    assert.equal(layout.fitViewport({ ...tiny, bounds }), null);
  }
  assert.equal(layout.fitViewport({ ...tiny, width: 0 }), null);
  assert.equal(layout.fitViewport({ ...tiny, width: 30 }), null);
  const point = layout.fitViewport({ ...tiny, bounds: { minX: 3, minY: 4, maxX: 3, maxY: 4 } });
  assertFitVisible(point, { minX: 3, minY: 4, maxX: 3, maxY: 4 });
});

test('fit camera keeps a centered full rectangle when no overlay reduces the chosen zoom', () => {
  const bounds = { minX: 0, minY: 0, maxX: 30, maxY: 20 };
  const result = layout.fitViewport({ bounds, width: 1000, height: 700 });
  assert.equal(result.center.x, 500); assert.equal(result.center.y, 350);
  assert.equal(result.viewport.minX, 24); assert.equal(result.viewport.maxX, 976);
});
