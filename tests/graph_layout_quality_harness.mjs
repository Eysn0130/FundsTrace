import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const context = vm.createContext({});
vm.runInContext(readFileSync(new URL('../static/graph-layout-quality.js', import.meta.url), 'utf8'), context);
vm.runInContext(readFileSync(new URL('../static/graph-layout.js', import.meta.url), 'utf8'), context);
const quality = context.FundsGraphLayoutQuality, layout = context.FundsGraphLayout;
const clone = value => JSON.parse(JSON.stringify(value));
const id = n => `SYN-Q${String(n).padStart(4, '0')}`;
function shape(kind, count = 24) {
  const nodes = Array.from({ length: count }, (_, n) => ({ id: id(n), size: 36, width: 100, height: 80, offsetY: 22, fontSize: 13 }));
  let edges = [];
  if (kind === 'chain' || kind === 'cycle') edges = Array.from({ length: count - (kind === 'cycle' ? 0 : 1) }, (_, n) => ({ source: id(n), target: id((n + 1) % count) }));
  if (kind === 'star') edges = Array.from({ length: count - 1 }, (_, n) => ({ source: id(0), target: id(n + 1) }));
  if (kind === 'disconnected') edges = Array.from({ length: Math.floor(count / 3) }, (_, n) => ({ source: id(n * 3), target: id(n * 3 + 1) }));
  if (kind === 'mesh') for (let a = 0; a < count; a++) for (let b = a + 1; b < count; b++) if ((a + b) % 3 === 0) edges.push({ source: id(a), target: id(b) });
  return { kind: 'auto', width: 1000, height: 700, nodes, edges };
}
function finish(input) { const model = layout.simulation(input); while (model.step()) {} return model.result(); }
const regular = input => ({ nodes: input.nodes.map((node, n) => ({ id: node.id, x: n % 20 * 150, y: Math.floor(n / 20) * 120 })) });

test('result validation rejects missing, repeated, foreign, nonnumeric and exploded coordinates atomically', () => {
  const input = shape('chain', 3), result = regular(input), original = JSON.stringify(input);
  assert.equal(quality.validate(input, result).valid, true);
  const variants = [null, {}, { nodes: result.nodes.slice(1) }, { nodes: [...result.nodes, result.nodes[0]] },
    { nodes: [result.nodes[0], result.nodes[0], result.nodes[2]] },
    { nodes: result.nodes.map((n, i) => i ? n : { ...n, id: 'FOREIGN' }) },
    ...[NaN, Infinity, -Infinity, '123', undefined, 1e50, -1e50].map(x => ({ nodes: result.nodes.map((n, i) => i ? n : { ...n, x }) }))];
  for (const candidate of variants) assert.equal(quality.validate(input, candidate).valid, false);
  assert.equal(quality.validate({ nodes: [{ id: 'A' }, { id: 'A' }] }, { nodes: [{ id: 'A', x: 0, y: 0 }, { id: 'A', x: 1, y: 1 }] }).valid, false);
  assert.equal(quality.validate({ nodes: [] }, { nodes: [] }).valid, true);
  assert.equal(quality.validate(input, { nodes: result.nodes.map(n => ({ ...n, x: n.x + 100000 })) }).valid, true);
  assert.equal(JSON.stringify(input), original);
});

test('same-size topologies and directed cycles are profiled without order or duplicate-edge bias', () => {
  const expected = { chain: [1, 2, false], star: [1, 23, false], cycle: [1, 2, true], disconnected: [16, 1, false], mesh: [2, 8, false] };
  for (const name of Object.keys(expected)) {
    const input = shape(name), score = quality.profile(input), [components, degree, cyclic] = expected[name];
    assert.equal(score.nodeCount, 24); assert.equal(score.componentCount, components); assert.equal(score.maxDegree, degree); assert.equal(score.cyclic, cyclic);
    const reordered = { ...input, nodes: [...input.nodes].reverse(), edges: [...input.edges].reverse() };
    assert.deepEqual(clone(quality.profile(reordered)), clone(score));
    assert.deepEqual(clone(quality.profile({ ...input, edges: [...input.edges, ...input.edges, { source: 'MISSING', target: id(0) }] })), clone(score));
    assert.deepEqual(clone(quality.candidates(input)), clone(quality.candidates(reordered)));
  }
  const loop = shape('chain', 1); loop.edges.push({ source: id(0), target: id(0) });
  assert.equal(quality.profile(loop).cyclic, true);
});

test('auto proposes at most one existing alternative; explicit layouts preserve user choice', () => {
  assert.deepEqual(clone(quality.candidates(shape('star'))), ['auto', 'structural']);
  assert.deepEqual(clone(quality.candidates(shape('chain'))), ['auto', 'dagre']);
  assert.deepEqual(clone(quality.candidates(shape('cycle'))), ['auto', 'partition']);
  assert.deepEqual(clone(quality.candidates(shape('mesh'))), ['auto', 'partition']);
  for (const kind of ['force', 'dagre', 'partition', 'structural']) assert.deepEqual(clone(quality.candidates({ ...shape('mesh'), kind })), [kind]);
});

test('complete body and long-label rectangles participate in overlap checks', () => {
  const input = { width: 1000, height: 700, nodes: [
    { id: 'A', size: 36, width: 300, height: 100, offsetY: 32 },
    { id: 'B', size: 36, width: 300, height: 100, offsetY: 32 }], edges: [] };
  const score = quality.evaluate(input, { nodes: [{ id: 'A', x: 0, y: 0 }, { id: 'B', x: 200, y: 0 }] });
  assert.equal(score.overlaps.count, 1); assert.equal(score.overlaps.coverage, 'exact');
  assert.deepEqual(clone(score.bounds), { minX: -150, minY: -18, maxX: 350, maxY: 82 });
  const apart = quality.evaluate(input, { nodes: [{ id: 'A', x: 0, y: 0 }, { id: 'B', x: 300, y: 0 }] });
  assert.equal(apart.overlaps.count, 0, 'touching envelopes are not overlapping');
  assert.equal(quality.prefer(score, apart), apart);
});

test('spatial overlap scan agrees with an independent complete rectangle enumeration', () => {
  let randomState = 42;
  const random = () => { randomState = (Math.imul(randomState, 1664525) + 1013904223) >>> 0; return randomState / 4294967296; };
  const input = { width: 1000, height: 700, nodes: Array.from({ length: 120 }, (_, n) => ({ id: id(n), size: 12,
    width: n === 5 ? 12000 : 40 + random() * 180, height: 20 + random() * 90, offsetX: random() * 100 - 50, offsetY: random() * 100 - 50 })), edges: [] };
  const result = { nodes: input.nodes.map(node => ({ id: node.id, x: random() * 1600 - 800, y: random() * 1200 - 600 })) };
  let expected = 0;
  for (let i = 0; i < input.nodes.length; i++) for (let j = i + 1; j < input.nodes.length; j++) {
    const a = input.nodes[i], b = input.nodes[j], pa = result.nodes[i], pb = result.nodes[j];
    if (Math.abs(pa.x + a.offsetX - pb.x - b.offsetX) < (a.width + b.width) / 2 &&
        Math.abs(pa.y + a.offsetY - pb.y - b.offsetY) < (a.height + b.height) / 2) expected++;
  }
  const score = quality.evaluate(input, result);
  assert.equal(score.overlaps.coverage, 'exact'); assert.equal(score.overlaps.count, expected);
});

test('assessment is immutable, reorder invariant, and works across the existing engines and topologies', () => {
  for (const name of ['chain', 'star', 'cycle', 'disconnected', 'mesh']) for (const kind of ['auto', 'dagre', 'partition', 'structural', 'force']) {
    const input = { ...shape(name), kind }, result = finish(input), before = JSON.stringify([input, result]);
    const score = quality.evaluate(input, result, { fitViewport: layout.fitViewport });
    assert.equal(score.valid, true); assert.equal(score.overlaps.coverage, 'exact');
    assert.ok(Number.isFinite(score.fit.zoom)); assert.ok(score.fit.zoom > 0);
    assert.equal(score.edges.geometry, 'straight-center-line-estimate');
    const reordered = { ...input, nodes: [...input.nodes].reverse(), edges: [...input.edges].reverse() };
    assert.deepEqual(clone(quality.evaluate(reordered, { ...result, nodes: [...result.nodes].reverse() }, { fitViewport: layout.fitViewport })), clone(score));
    assert.equal(JSON.stringify([input, result]), before);
  }
});

test('readability estimate uses viewport and fonts without guaranteeing legibility or changing geometry', () => {
  const input = shape('chain', 3), result = regular(input);
  const wide = quality.evaluate(input, result), narrow = quality.evaluate({ ...input, width: 160, height: 150 }, result);
  assert.ok(wide.fit.minimumFontPixels > narrow.fit.minimumFontPixels);
  assert.equal(narrow.fit.readable, false); assert.equal(quality.prefer(narrow, wide), wide);
  const obstacle = { ...input, chromeObstacles: [{ bounds: { minX: 500, minY: 0, maxX: 1000, maxY: 700 } }] };
  const fitted = quality.evaluate(obstacle, result, { fitViewport: layout.fitViewport });
  assert.equal(fitted.fit.method, 'host-viewport-estimate'); assert.ok(fitted.fit.zoom < wide.fit.zoom);
  const largerText = quality.evaluate({ ...input, nodes: input.nodes.map(node => ({ ...node, fontSize: 18 })) }, result);
  assert.ok(Math.abs(largerText.fit.minimumFontPixels / largerText.fit.zoom - 18) < 1e-7);
  assert.equal(quality.prefer({ valid: false }, narrow), narrow);
  assert.equal(quality.prefer({ valid: false }, { valid: false }), null);
});

test('small relationship text participates in minimum readable font size', () => {
  const input = { width: 1000, height: 700, maxZoom: 1,
    nodes: [{ id: 'A', size: 20, fontSize: 20 }, { id: 'B', size: 20, fontSize: 20 }],
    edges: [{ source: 'A', target: 'B', fontSize: 8 }] };
  const result = { nodes: [{ id: 'A', x: 0, y: 0 }, { id: 'B', x: 100, y: 0 }] };
  const score = quality.evaluate(input, result);
  assert.equal(score.fit.zoom, 1); assert.equal(score.fit.minimumFontPixels, 8);
  assert.equal(score.fit.readable, false);
});

test('center-line diagnostics explicitly remain estimates and identify independent crossing examples', () => {
  const input = { width: 1000, height: 700, nodes: ['A', 'B', 'C', 'D', 'E'].map(id => ({ id, size: 10 })), edges: [
    { source: 'A', target: 'B' }, { source: 'C', target: 'D' }] };
  const result = { nodes: [{ id: 'A', x: -100, y: -100 }, { id: 'B', x: 100, y: 100 },
    { id: 'C', x: 100, y: -100 }, { id: 'D', x: -100, y: 100 }, { id: 'E', x: 0, y: 0 }] };
  const score = quality.evaluate(input, result);
  assert.equal(score.edges.crossings.count, 1); assert.equal(score.edges.crossings.coverage, 'exact');
  assert.equal(score.edges.nodeIntersections.count, 2);
});

test('fit estimate reserves the fixed CSS-pixel label gap at the predicted zoom', () => {
  const input = shape('chain', 2); input.width = 250; input.height = 160;
  input.nodes.forEach(node => { node.labelGap = 6; node.gapWorld = 6; });
  const result = { nodes: [{ id: id(0), x: 0, y: 0 }, { id: id(1), x: 500, y: 150 }] };
  const before = JSON.stringify([input, result]), score = quality.evaluate(input, result, { fitViewport: layout.fitViewport });
  assert.ok(score.fit.zoom < 1);
  const extra = 6 / score.fit.zoom - 6;
  assert.ok(Math.abs(score.bounds.maxY - (150 + 22 + 40 + extra)) < 1e-7);
  assert.ok(Math.abs(score.bounds.minY + 18) < 1e-7, 'the node-body top remains fixed');
  assert.ok(score.fit.iterations <= 8);
  assert.equal(JSON.stringify([input, result]), before);
});

test('automatic preference requires collision and reading-size dominance instead of a score tradeoff', () => {
  const assessment = (count, pixels, compactness = 2) => ({ valid: true,
    overlaps: { count, coverage: 'exact' }, fit: { minimumFontPixels: pixels, targetFontPixels: 12 }, compactness });
  const baseline = assessment(1, 12), giantSpread = assessment(0, 2);
  assert.equal(quality.prefer(baseline, giantSpread), baseline, 'zero collisions cannot win by making text unreadable');
  assert.equal(quality.prefer(giantSpread, baseline), giantSpread, 'a reading gain alone cannot introduce more collisions');
  const noCollisions = assessment(0, 12), betterReading = assessment(1, 14), readableLarge = assessment(0, 18);
  assert.equal(quality.prefer(baseline, noCollisions), noCollisions);
  assert.equal(quality.prefer(assessment(1, 8), baseline), baseline);
  assert.equal(quality.prefer(baseline, betterReading), baseline, 'both candidates already reach the capped reading target');
  assert.equal(quality.prefer(noCollisions, readableLarge), noCollisions);
  const compact = assessment(0, 14, 1.2);
  assert.equal(quality.prefer(noCollisions, compact), compact, 'equal capped reading and collisions permit compactness tie-break');
  const tinyReadingDifference = assessment(0, 12 - 1e-8);
  assert.equal(quality.prefer(baseline, tinyReadingDifference), tinyReadingDifference);
});

test('unknown collision coverage never automatically replaces the current valid candidate', () => {
  const assessment = (count, pixels, coverage) => ({ valid: true,
    overlaps: { count, coverage }, fit: { minimumFontPixels: pixels, targetFontPixels: 12 }, compactness: 2 });
  const current = assessment(5, 12, 'sampled');
  for (const candidate of [assessment(0, 2, 'exact'), assessment(10, 12, 'exact'), assessment(0, 12, 'exact'),
    assessment(0, 12, 'sampled'), assessment(10, 18, 'sampled')]) {
    assert.equal(quality.prefer(current, candidate), current);
  }
  const complete = assessment(5, 12, 'exact');
  assert.equal(quality.prefer(complete, assessment(0, 18, 'sampled')), complete);
  assert.equal(quality.prefer({ valid: false }, current), current, 'validity remains the first hard gate');
});

test('1001+ nodes and dense overlap stay within explicit work and sampling bounds', () => {
  for (const count of [1001, 5000]) {
    const input = shape('star', count), spread = quality.evaluate(input, regular(input));
    assert.equal(spread.overlaps.coverage, 'exact'); assert.equal(spread.overlaps.count, 0);
    const coincident = quality.evaluate(input, { nodes: input.nodes.map(node => ({ id: node.id, x: 0, y: 0 })) });
    assert.equal(coincident.overlaps.coverage, 'sampled'); assert.ok(coincident.overlaps.count > 0);
    assert.ok(coincident.overlaps.checks <= 65536); assert.ok(coincident.overlaps.work <= 262145);
    assert.ok(spread.edges.crossings.checks <= 8192); assert.ok(spread.edges.nodeIntersections.checks <= 16384);
    assert.equal(spread.edges.nodeIntersections.coverage, 'sampled');
    assert.equal(quality.prefer(spread, coincident), spread);
    assert.equal(quality.prefer(coincident, spread), coincident, 'unknown baseline collisions cannot establish automatic dominance');
  }
});
