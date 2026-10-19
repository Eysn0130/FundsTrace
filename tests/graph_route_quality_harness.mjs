import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const quality = require('../static/graph-route-quality.js');

const box = (x, y, width = 10, height = 10) => ({ minX: x, minY: y, maxX: x + width, maxY: y + height });
const edge = (id, path, extra = {}) => ({ id, source: id + ':source', target: id + ':target', path, ...extra });
const line = (id, x0, y0, x1, y1, extra) => edge(id, [['M', x0, y0], ['L', x1, y1]], extra);
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-6, `${actual} != ${expected}`);

test('sample measures arc length and tangent across unequal line segments', () => {
  const sampled = quality.sample([['M', 0, 0], ['L', 10, 0], ['L', 10, 90]]);
  assert.equal(sampled.valid, true); close(sampled.length, 100);
  assert.deepEqual(sampled.midpoint, { x: 10, y: 40 });
  assert.deepEqual(sampled.tangent, { x: 0, y: 1 });
  assert.deepEqual(sampled.bounds, box(0, 0, 10, 90));
});

test('quadratic and cubic paths detect obstacles that endpoint centerlines miss', () => {
  for (const [path, body] of [
    [[['M', 0, 0], ['Q', 100, 100, 200, 0]], box(95, 45)],
    [[['M', 0, 0], ['C', 0, 120, 200, 120, 200, 0]], box(95, 85)]
  ]) {
    const result = quality.evaluate({ nodes: [{ id: 'obstacle', body }], edges: [edge('curve', path)] });
    assert.equal(result.counts.edgeNode, 1);
    assert.equal(result.coverage.complete, true);
    const endpointLine = quality.evaluate({ nodes: [{ id: 'obstacle', body }], edges: [line('curve', 0, 0, 200, 0)] });
    assert.equal(endpointLine.counts.edgeNode, 0);
  }
});

test('M subpaths keep actual label gaps and do not invent connecting lines', () => {
  const path = [['M', 0, 0], ['L', 40, 0], ['M', 60, 0], ['L', 100, 0]];
  const sampled = quality.sample(path);
  close(sampled.length, 80); assert.equal(sampled.segments.length, 2);
  const result = quality.evaluate({ nodes: [{ id: 'gap', body: box(45, -3, 10, 6) }], edges: [edge('gapped', path)] });
  assert.equal(result.counts.edgeNode, 0);
});

test('collision identities remain complete when diagnostic witnesses are disabled', () => {
  const nodes=[{id:'first',label:box(40,-5,20,10)},{id:'second',label:box(40,95,20,10)}];
  const before=quality.evaluate({nodes,edges:[line('curve',0,0,100,0)],witnessLimit:0});
  const after=quality.evaluate({nodes,edges:[line('curve',0,100,100,100)],witnessLimit:0});
  assert.equal(before.coverage.complete,true);assert.equal(after.coverage.complete,true);
  assert.equal(before.counts.edgeNodeLabel,1);assert.equal(after.counts.edgeNodeLabel,1);
  assert.deepEqual(before.collisions.edgeNodeLabel,[]);assert.deepEqual(after.collisions.edgeNodeLabel,[]);
  assert.equal(before.collisionKeys.edgeNodeLabel.length,1);
  assert.equal(after.collisionKeys.edgeNodeLabel.length,1);
  assert.notDeepEqual(before.collisionKeys.edgeNodeLabel,after.collisionKeys.edgeNodeLabel,
    'an unchanged total must not hide moving an obstruction to a different account label');
});

test('quality reports total sampled path length separately from collision counts without closing label gaps', () => {
  const path = [['M', 0, 0], ['L', 40, 0], ['M', 60, 0], ['L', 100, 0]];
  const curve = [['M', 0, 100], ['Q', 50, 180, 100, 100]];
  const result = quality.evaluate({ edges: [edge('gapped', path), edge('curve', curve)] });
  close(result.totalLength, 80 + quality.sample(curve).length);
  close(result.edgeLengths.gapped, 80);
  close(result.edgeLengths.curve, quality.sample(curve).length);
  assert.deepEqual(Object.keys(result.counts), ['edgeNode', 'edgeNodeLabel', 'edgeLabel', 'labelOverlap', 'labelNode', 'crossings', 'collinearOverlap']);
  assert.equal(result.coverage.complete, true);
  assert.equal(quality.evaluate().totalLength, 0);
  const invalid = quality.evaluate({edges:[edge('bad', [['M',0,0], ['Q',1,NaN,2,3]])]});
  assert.equal(invalid.totalLength, 0);
  assert.equal(Object.hasOwn(invalid.edgeLengths, 'bad'), false);
});

test('sampling supports relative paths, close segments and zero-length paths', () => {
  const sampled = quality.sample([['M', 10, 20], ['l', 20, 0], ['v', 20], ['h', -20], ['z']]);
  assert.equal(sampled.valid, true); close(sampled.length, 80);
  assert.deepEqual(sampled.bounds, box(10, 20, 20, 20));
  const zero = quality.sample([['M', 0, 0], ['L', 0, 0]]);
  assert.equal(zero.valid, true); assert.equal(zero.length, 0); assert.deepEqual(zero.midpoint, { x: 0, y: 0 });
});

test('endpoint bodies are excluded but their fixed text labels remain obstacles', () => {
  const result = quality.evaluate({ nodes: [
    { id: 'a', body: box(-5, -5), label: box(90, -3, 20, 6) },
    { id: 'b', body: box(195, -5) }, { id: 'foreign', body: box(45, -5) }
  ], edges: [line('ab', 0, 0, 200, 0, { source: 'a', target: 'b', label: box(80, -3, 40, 6) })] });
  assert.equal(result.counts.edgeNode, 1); assert.equal(result.counts.edgeNodeLabel, 1);
  assert.equal(result.counts.edgeLabel, 0, 'own middle gap label is not an unrelated edge-label obstruction');
});

test('unrelated labels and label/body conflicts retain complete box geometry', () => {
  const result = quality.evaluate({ nodes: [
    { id: 'body', body: box(70, 10, 10, 10) },
    { id: 'label', label: box(20, 10, 300, 12) }
  ], edges: [line('a', 0, 15, 400, 15), line('b', 0, 50, 400, 50, { label: box(40, 10, 70, 12) })] });
  assert.equal(result.counts.edgeNodeLabel, 1);
  assert.equal(result.counts.edgeLabel, 1);
  assert.equal(result.counts.labelOverlap, 1);
  assert.equal(result.counts.labelNode, 2);
});

test('crossing witnesses include acute angles and deduplicate sampled vertices', () => {
  const result = quality.evaluate({ edges: [
    line('horizontal', 0, 50, 100, 50), edge('vertical', [['M', 50, 0], ['L', 50, 50], ['L', 50, 100]]),
    line('shallow', 0, 10, 100, 90)
  ] });
  assert.equal(result.counts.crossings, 3);
  const crossing = result.collisions.crossings.find(hit => hit.a === 'horizontal' && hit.b === 'vertical');
  close(crossing.angle, 90); assert.deepEqual(crossing.point, { x: 50, y: 50 });
  assert.ok(result.collisions.crossings.some(hit => hit.angle < 45));
});

test('crossing identities follow relation pairs and retain multiple intersections', () => {
  const horizontal=line('horizontal',0,50,100,50);
  const before=quality.evaluate({edges:[horizontal,line('vertical',50,0,50,100)]});
  const shifted=quality.evaluate({edges:[horizontal,line('vertical',70,0,70,100)]});
  assert.equal(before.counts.crossings,1);assert.equal(shifted.counts.crossings,1);
  assert.deepEqual(before.collisionKeys.crossings,shifted.collisionKeys.crossings,
    'moving an existing intersection of the same two relations is not a new obstructing relation');
  const repeated=quality.evaluate({edges:[horizontal,edge('vertical',[['M',20,0],['L',20,100],['L',80,0],['L',80,100]])]});
  assert.equal(repeated.counts.crossings,3);assert.equal(repeated.collisionKeys.crossings.length,3);
  assert.equal(new Set(repeated.collisionKeys.crossings).size,3,'additional intersections cannot disappear from identity evidence');
});

test('shared endpoints do not count as crossings but distant recrossings do', () => {
  const common = { source: 'shared' };
  const result = quality.evaluate({ edges: [
    line('a', 0, 0, 100, 100, common), edge('b', [['M', 0, 0], ['L', 0, 100], ['L', 100, 0]], common)
  ] });
  assert.equal(result.counts.crossings, 1);
  assert.deepEqual(result.collisions.crossings[0].point, { x: 50, y: 50 });
  const fan = quality.evaluate({ edges: [line('a', 0, 0, 100, 100, common), line('b', 0, 0, 100, -100, common)] });
  assert.equal(fan.counts.crossings, 0);
});

test('collinear and reverse paths are overlap conflicts, not infinite crossings', () => {
  const result = quality.evaluate({ edges: [
    line('a', 0, 0, 100, 0, { source: 'one', target: 'two' }),
    line('b', 100, 0, 0, 0, { source: 'two', target: 'one' }), line('c', 110, 0, 150, 0)
  ] });
  assert.equal(result.counts.collinearOverlap, 1); assert.equal(result.counts.crossings, 0);
});

test('self-loops use real curved extents and remain associated with their owner', () => {
  const loop = edge('loop', [['M', 0, 0], ['C', -100, -100, 100, -100, 0, 0]], { source: 'same', target: 'same' });
  const result = quality.evaluate({ nodes: [
    { id: 'same', body: box(-6, -6, 12, 12) }, { id: 'above', body: box(-6, -80, 12, 12) }
  ], edges: [loop] });
  assert.equal(result.valid, true); assert.equal(result.counts.edgeNode, 1);
  assert.equal(result.collisions.edgeNode[0].node, 'above'); assert.equal(result.counts.crossings, 0);
});

test('empty graphs and one node are valid; malformed coordinates are explicit', () => {
  const empty = quality.evaluate(); assert.equal(empty.validity, 'valid'); assert.equal(empty.coverage.complete, true);
  assert.equal(quality.evaluate({ nodes: [{ id: 'only', body: box(0, 0) }] }).valid, true);
  const invalid = quality.evaluate({ nodes: [{ id: 'bad', body: box(Infinity, 0) }], edges: [edge('bad-path', [['M', 0, 0], ['Q', 1, NaN, 2, 3]])] });
  assert.equal(invalid.validity, 'invalid'); assert.deepEqual(invalid.invalidPaths, ['bad-path']);
  assert.deepEqual(invalid.invalidGeometry, [{ id: 'bad', kind: 'body' }]);
  assert.equal(quality.sample([['M', 0, 0], ['A', 5, 5, 0, 0, 1, 10, 10]]).valid, false, 'unsupported arcs must not be silently converted to a line');
});

test('input permutation gives identical deterministic metrics and witnesses without mutation', () => {
  const input = { nodes: [{ id: 'z', body: box(45, 45) }, { id: 'a', label: box(15, 45, 60, 12) }],
    edges: [line('z', 0, 50, 100, 50), line('a', 50, 0, 50, 100)] };
  const serialized = JSON.stringify(input), result = quality.evaluate(input);
  assert.equal(JSON.stringify(input), serialized);
  assert.deepEqual(quality.evaluate({ nodes: [...input.nodes].reverse(), edges: [...input.edges].reverse() }), result);
});

test('large graphs stop at a shared work budget and never report unknown coverage as complete', () => {
  const nodes = Array.from({ length: 1001 }, (_, i) => ({ id: String(i).padStart(4, '0'), body: box(i * 30, i % 7 * 40) }));
  const edges = nodes.slice(1).map((node, i) => line('edge' + node.id, i * 30, i % 7 * 40, (i + 1) * 30, (i + 1) % 7 * 40));
  const result = quality.evaluate({ nodes, edges, maxChecks: 5000 });
  assert.ok(result.coverage.checks <= 5000); assert.equal(result.coverage.complete, false);
  assert.equal(result.validity, 'unknown'); assert.equal(result.coverage.reason, 'budget');
  assert.ok(result.coverage.processedEdges < edges.length);
  const dense = quality.evaluate({ nodes: nodes.map(node => ({ ...node, body: box(0, 0) })), edges, maxChecks: 3500, witnessLimit: 3 });
  assert.ok(dense.coverage.checks <= 3500); assert.ok(dense.collisions.edgeNode.length <= 3);
});

test('huge paths, huge boxes and repeated zero-length commands are bounded too', () => {
  const huge = quality.evaluate({ nodes: [{ id: 'all', body: box(-1e10, -1e10, 2e10, 2e10) }],
    edges: [line('far', -1e10, 0, 1e10, 0)], maxChecks: 200 });
  assert.ok(huge.coverage.checks <= 200);
  const path = Array.from({ length: 50000 }, () => ['M', 0, 0]);
  const degenerate = quality.evaluate({ edges: [edge('zero', path)], maxChecks: 100 });
  assert.equal(degenerate.coverage.complete, false); assert.ok(degenerate.coverage.checks <= 100);
  assert.equal(degenerate.validity, 'unknown');
  const zeroBudget = quality.evaluate({ nodes: [{ id: 'one', body: box(0, 0) }], maxChecks: 0 });
  assert.equal(zeroBudget.coverage.complete, false); assert.equal(zeroBudget.coverage.checks, 0);
});

if(process.env.FUNDS_GRAPH_BASELINE_DIR)test('quality optimization exactly preserves baseline witnesses and partial-budget accounting',()=>{
  const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path'),beforeContext=vm.createContext({});
  vm.runInContext(fs.readFileSync(path.join(process.env.FUNDS_GRAPH_BASELINE_DIR,'graph-route-quality.js'),'utf8'),beforeContext);
  const before=beforeContext.FundsGraphRouteQuality;
  const legacyResult = input => {
    const { totalLength, edgeLengths, collisionKeys, ...existing } = structuredClone(quality.evaluate(input));
    assert.ok(Number.isFinite(totalLength) && totalLength >= 0);
    return existing;
  };
  const scenes=[
    {},
    {nodes:[{id:'body',body:box(45,45)},{id:'text',label:box(20,40,90,20)}],edges:[
      line('A=>B',0,50,120,50,{source:'A',target:'B',label:box(40,45,30,15)}),
      edge('B=>A',[['M',50,0],['Q',80,50,50,100]],{source:'B',target:'A',label:box(45,35,40,20)}),
      line('cross',0,0,120,100)]},
    {nodes:[{id:'same',body:box(-8,-8,16,16)},{id:'upper',body:box(-8,-80,16,16)}],edges:[
      edge('loop',[['M',0,0],['C',-80,-120,80,-120,0,0]],{source:'same',target:'same'}),
      edge('gap',[['M',-100,-50],['L',-10,-50],['M',10,-50],['L',100,-50]],{label:box(-15,-60,30,20)})]},
    {edges:[line('H',0,50,100,50),edge('V',[['M',50,0],['L',50,50],['L',50,100]]),line('shallow',0,10,100,90)]},
    {nodes:[{id:'all',body:box(-1e10,-1e10,2e10,2e10)},{id:'wide-text',label:box(-1e8,-10,2e8,20)}],edges:[
      line('far',-1e10,0,1e10,0),line('vertical',0,-1e10,0,1e10,{label:box(-1e7,-1e7,2e7,2e7)})]},
    {nodes:[{id:'bad',body:box(Infinity,0)}],edges:[edge('bad',[['M',0,0],['A',5,5,0,0,1,10,10]])]},
    {edges:[edge('zero',[['M',0,0],['L',0,0],['M',20,20],['L',20,20]]),
      edge('relative',[['M',10,20],['l',20,0],['v',20],['h',-20],['z']])]}
  ];
  for(const input of scenes)for(const maxChecks of [0,1,20,100,1000,120000])for(const witnessLimit of [0,1,4,40])for(const steps of [2,12,24]){
    const graph={...input,maxChecks,witnessLimit,steps};
    assert.deepEqual(legacyResult(graph),structuredClone(before.evaluate(graph)));
    if(maxChecks===100)assert.deepEqual(legacyResult({...graph,nodes:[...(graph.nodes||[])].reverse(),edges:[...(graph.edges||[])].reverse()}),
      structuredClone(before.evaluate({...graph,nodes:[...(graph.nodes||[])].reverse(),edges:[...(graph.edges||[])].reverse()})));
  }
  for(const path of scenes.flatMap(scene=>(scene.edges||[]).map(item=>item.path)))for(const steps of [2,12,24])for(const maxSegments of [1,5,64])
    assert.deepEqual(structuredClone(quality.sample(path,{steps,maxSegments,maxWork:100})),structuredClone(before.sample(path,{steps,maxSegments,maxWork:100})));
});
