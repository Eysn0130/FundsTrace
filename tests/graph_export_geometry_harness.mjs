import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const window = {};
const context = vm.createContext({ window });
for (const name of ['graph-layout', 'graph-labels', 'graph-export-geometry']) {
  vm.runInContext(readFileSync(new URL(`../static/${name}.js`, import.meta.url), 'utf8'), context);
}
const api = window.FundsGraphExportGeometry;
const fitViewport = context.FundsGraphLayout.fitViewport;
const copy = value => JSON.parse(JSON.stringify(value));
const intersects = (a, b) => a.minX < b.maxX && a.maxX > b.minX && a.minY < b.maxY && a.maxY > b.minY;
const union = boxes => boxes.reduce((result, box) => !result ? { ...box } : ({
  minX: Math.min(result.minX, box.minX), minY: Math.min(result.minY, box.minY),
  maxX: Math.max(result.maxX, box.maxX), maxY: Math.max(result.maxY, box.maxY),
}), null);
const transform = (matrix, x, y) => ({ x: matrix[0] * x + matrix[3] * y + matrix[6], y: matrix[1] * x + matrix[4] * y + matrix[7] });
const envelope = (box, project) => {
  const points = [[box.minX, box.minY], [box.maxX, box.minY], [box.minX, box.maxY], [box.maxX, box.maxY]].map(([x, y]) => project(x, y));
  return { minX: Math.min(...points.map(p => p.x)), minY: Math.min(...points.map(p => p.y)),
    maxX: Math.max(...points.map(p => p.x)), maxY: Math.max(...points.map(p => p.y)) };
};

function fixture(scale, edgeScale = 1, scene = {}) {
  const width = (scene.width || 906) * scale, height = (scene.height || 820) * scale, footerHeight = (scene.footerHeight ?? 60) * scale;
  const nodesData = scene.nodes || [
    ['A', '华盛建材\nDEMO-0012', -240, -230, 34],
    ['B', '星港物流\nDEMO-0035', 170, -205, 30],
    ['C', '华安商贸\nDEMO-0021', -50, 70, 48],
    ['D', '远景设备\nDEMO-0088', -390, 150, 28],
    ['E', '宁远实业\nDEMO-0048', 270, 200, 32],
    ['F', '恒信供应链\nDEMO-0066', -75, 430, 30],
  ];
  const edgesData = scene.edges || [
    ['A', 'C', '4笔·¥254,000.00\nUSD 3,500.00', 55, -155],
    ['B', 'C', '2笔·¥6,500.00', 220, -145],
    ['D', 'C', '1笔·¥128,000.00', -235, 110],
    ['E', 'C', '3笔·¥128,000.00', 325, 109],
    ['C', 'F', '2笔·¥241,500.00', 80, 190],
  ];
  let camera = [1.2, 0, 0, 0, 1.2, 0, 17, 29, 1], autoPaint = true;
  const originalCamera = [...camera], cleanupCalls = [], groups = [];
  const cameraGroup = { getMatrix: () => [...camera], setMatrix: value => { camera = [...value]; } };
  const nodeById = new Map();
  function makeItem(kind, id, label, x, y, radius = 0) {
    const model = kind === 'node' ? { id, label, x, y, size: radius * 2 }
      : { source: id.split('=>')[0], target: id.split('=>')[1], label, x: 0, y: 0 };
    const localScale = kind === 'edge' ? edgeScale : 1;
    const attrs = { x: kind === 'node' ? 0 : x / localScale, y: kind === 'node' ? radius : y / localScale,
      text: label, textAlign: 'center', textBaseline: kind === 'node' ? 'top' : 'middle', fontSize: scene.fontSize || 18, lineHeight: scene.lineHeight || 24 };
    const text = {
      attr(value) { if (value === undefined) return { ...attrs }; if (typeof value === 'string') return attrs[value]; Object.assign(attrs, value); },
      get: name => name === 'name' ? 'text-shape' : name === 'visible' ? true : false,
      getMatrix: () => [1, 0, 0, 0, 1, 0, 0, 0, 1],
      getTotalMatrix: () => [camera[0] * localScale, 0, 0, 0, camera[4] * localScale, 0,
        camera[6] + camera[0] * model.x, camera[7] + camera[4] * model.y, 1],
      getCanvasBBox() {
        const lines = attrs.text.split('\n'), textWidth = Math.max(...lines.map(line => [...line].reduce((sum, c) => sum + (c.charCodeAt(0) > 255 ? attrs.fontSize : scene.asciiWidth || 10), 0)));
        const textHeight = attrs.fontSize + (lines.length - 1) * attrs.lineHeight;
        const box = { minX: attrs.x - textWidth / 2, maxX: attrs.x + textWidth / 2,
          minY: attrs.textBaseline === 'top' ? attrs.y : attrs.y - textHeight / 2,
          maxY: attrs.textBaseline === 'top' ? attrs.y + textHeight : attrs.y + textHeight / 2 };
        return envelope(box, (a, b) => transform(this.getTotalMatrix(), a, b));
      },
    };
    const keyShape = { attr: name => name === 'stroke' ? '#7f8b99' : null, getCanvasBBox() {
      if (kind === 'node') return envelope({ minX: -radius, minY: -radius, maxX: radius, maxY: radius },
        (a, b) => transform([camera[0], 0, 0, 0, camera[4], 0, camera[6] + camera[0] * model.x, camera[7] + camera[4] * model.y, 1], a, b));
      const a = nodeById.get(model.source).getModel(), b = nodeById.get(model.target).getModel();
      return envelope({ minX: Math.min(a.x, b.x), minY: Math.min(a.y, b.y), maxX: Math.max(a.x, b.x), maxY: Math.max(a.y, b.y) },
        (a, b) => transform(camera, a, b));
    } };
    const item = { kind, text, clip: { original: id }, getModel: () => model, getKeyShape: () => keyShape,
      getContainer: () => ({ get: name => name === 'children' ? [text] : undefined,
        getCanvasBBox: () => union([keyShape.getCanvasBBox(), text.getCanvasBBox()]) }) };
    return item;
  }
  const nodes = nodesData.map(([id, label, x, y, radius]) => { const item = makeItem('node', id, label, x, y, radius); nodeById.set(id, item); return item; });
  const edges = edgesData.map(([a, b, label, x, y]) => makeItem('edge', `${a}=>${b}`, label, x, y));
  const graph = {
    getNodes: () => nodes, getEdges: () => edges, getZoom: () => camera[0],
    get: name => name === 'group' ? cameraGroup : name === 'autoPaint' ? autoPaint : name === 'canvas' ? {
      addGroup() { const group = { removed: false, children: [], toBack() {}, addShape(type, options) { this.children.push({ type, ...options }); }, remove() { this.removed = true; } }; groups.push(group); return group; },
    } : undefined,
    setAutoPaint: value => { autoPaint = value; },
    zoomTo(value) { camera[0] = camera[4] = value; },
    translate(x, y) { camera[6] += x; camera[7] += y; },
    getCanvasByPoint: (x, y) => transform(camera, x, y),
    getPointByCanvas: (x, y) => ({ x: (x - camera[6]) / camera[0], y: (y - camera[7]) / camera[4] }),
  };
  const applyLabels = () => { for (const item of nodes) item.text.attr({ x: 0, y: item.getModel().size / 2 + 6 * scale / graph.getZoom() }); };
  const unprojector = () => (x, y) => graph.getPointByCanvas(x, y);
  const bodyBounds = item => item.getKeyShape().getCanvasBBox();
  const labelBounds = item => item.text.getCanvasBBox();
  const itemBounds = (item, inverse) => envelope(item.getContainer().getCanvasBBox(), inverse);
  // The pre-fix path fits the existing body envelope, then anchors all text.
  const bodyWorld = union(nodes.map(item => envelope(bodyBounds(item), unprojector())));
  const fit = fitViewport({ bounds: bodyWorld, width, height: height - footerHeight, padding: 40 * scale, maxZoom: 10 });
  graph.zoomTo(fit.zoom); const center = graph.getCanvasByPoint(fit.worldCenter.x, fit.worldCenter.y);
  graph.translate(fit.center.x - center.x, fit.center.y - center.y); applyLabels();
  const baselineShapes = nodes.concat(edges).map(item => [item, copy(item.text.attr()), item.clip]);
  const facts = JSON.stringify(nodes.concat(edges).map(item => item.getModel()));
  const options = { graph, width, height, footerHeight, scale, applyLabels, labelBounds, bodyBounds, itemBounds, unprojector,
    fitViewport, restoreEdge(item) {
      const clip = item.clip; item.clip = null;
      return () => { item.clip = clip; cleanupCalls.push(item); };
    } };
  return { options, nodes, edges, groups, originalCamera, baselineShapes, facts, cleanupCalls,
    camera: () => [...camera], restoreCamera: () => cameraGroup.setMatrix(originalCamera), applyLabels,
    labels: () => nodes.concat(edges).map(item => ({ item, box: labelBounds(item) })),
  };
}

for (const scale of [2, 3]) test(`${scale}x three-account chain keeps unequal amount and time labels attached to their own paths`, () => {
  // Same geometry and unequal text as the reported PNG/PDF. The short second
  // edge's time text clears its endpoint by only a few pixels. Padding alone
  // used to trigger a large vertical relocation and a detached guide line.
  const f = fixture(scale, 1, { width: 794, height: 720, footerHeight: 0, fontSize: 13, lineHeight: 18, asciiWidth: 6.35,
    nodes: [
      ['A', '合成节点 2\n000GRAPH-00002', 397, 338.5, 15],
      ['B', '合成节点 3\n000GRAPH-00003', 644.1, 338.5, 16],
      ['C', '合成节点 4\n000GRAPH-00004', 800.5, 338.5, 15],
    ], edges: [
      ['A', 'B', '1笔·¥9,999,999,999,999,999.99\n2026-01-03 10:02:00', 520.3, 329.51],
      ['B', 'C', '1笔·¥0.01\n2026-01-04 10:03:00', 722.55, 325.88],
    ] });
  const anchors = f.edges.map(item => copy(item.text.attr()));
  const result = api.prepare(f.options);
  assert.equal(result.movedLabels, 0, 'sampling must not detach an edge label to satisfy export-only collision padding');
  f.edges.forEach((item, i) => assert.deepEqual(item.text.attr(), anchors[i], 'the canonical path label position and full amount/time remain intact'));
  assert.equal(f.groups.length, 0, 'sampling must not add floating guide lines');
  assert.equal(JSON.stringify(f.nodes.concat(f.edges).map(item => item.getModel())), f.facts);
  result.restore(); f.restoreCamera(); f.applyLabels(); assert.deepEqual(f.camera(), f.originalCamera);
});

function assertInside(f) {
  const { width, height, footerHeight } = f.options, labels = f.labels();
  for (const { item, box } of labels) {
    assert.ok(box.minX >= -1e-6 && box.maxX <= width + 1e-6 && box.minY >= -1e-6
      && box.maxY <= height - footerHeight + 1e-6, `${item.getModel().label}: final text is inside all four content edges`);
  }
}

for (const scale of [2, 3]) test(`${scale}x sampling fits complete labels without moving amounts away from their paths`, () => {
  const f = fixture(scale), bottom = f.nodes.at(-1).text.getCanvasBBox();
  assert.ok(bottom.maxY > f.options.height - f.options.footerHeight, 'fixture reproduces post-fit footer clipping');
  const before = f.labels();
  assert.ok(before.some((a, i) => before.slice(i + 1).some(b => intersects(a.box, b.box))), 'fixture reproduces text collisions');
  const result = api.prepare(f.options);
  assertInside(f); assert.ok(result.unresolvedCount > 0); assert.equal(result.movedLabels, 0);
  assert.equal(JSON.stringify(f.nodes.concat(f.edges).map(item => item.getModel())), f.facts);
  for (const item of f.nodes) {
    const text = item.text.getCanvasBBox(), body = f.options.bodyBounds(item);
    assert.ok(Math.abs(text.minY - body.maxY - 6 * scale) < 1e-6, 'account text keeps its bottom anchor');
  }
  const sampledCamera = f.camera(); result.restore(); result.restore();
  assert.deepEqual(f.camera(), sampledCamera, 'geometry cleanup leaves camera restoration to the controller');
  for (const [item, attrs, clip] of f.baselineShapes.filter(([item]) => item.kind === 'edge')) {
    assert.deepEqual(item.text.attr(), attrs); assert.equal(item.clip, clip);
  }
  assert.equal(f.cleanupCalls.length, 0, 'sampling never replaces the live path or its label clip');
  assert.equal(f.groups.length, 0, 'sampling adds no detached label guides');
  f.restoreCamera(); f.applyLabels(); assert.deepEqual(f.camera(), f.originalCamera);
  assert.equal(JSON.stringify(f.nodes.concat(f.edges).map(item => item.getModel())), f.facts);
});

test('item-local scale does not change a manual label position or saved ratio during sampling', () => {
  const f = fixture(2, 1.25), before = f.edges.map(item => copy(item.text.attr()));
  for (const [i, item] of f.edges.entries()) Object.assign(item.getModel(), { labelManual: true, labelRatio: .25 + i / 10 });
  const models = JSON.stringify(f.edges.map(item => item.getModel()));
  const result = api.prepare(f.options); assertInside(f); assert.equal(result.movedLabels, 0);
  f.edges.forEach((item, index) => assert.deepEqual(item.text.attr(), before[index], 'manual positions stay anchored during capture, not only after restoration'));
  assert.equal(JSON.stringify(f.edges.map(item => item.getModel())), models);
  result.restore(); f.edges.forEach((item, index) => assert.deepEqual(item.text.attr(), before[index]));
});

test('older hosts without actual bounds remain a conservative no-op', () => {
  const graph = { getNodes: () => [{}], getEdges: () => [], get: () => null };
  const result = api.prepare({ graph, width: 100, height: 100, footerHeight: 20, scale: 2 });
  assert.equal(result.movedLabels, 0); result.restore();
});

test('unresolvable account-label collisions remain visible and are reported', () => {
  const f = fixture(2), first = f.nodes[0].getModel(), second = f.nodes[1].getModel();
  second.x = first.x; second.y = first.y; second.size = first.size;
  const text = f.nodes.concat(f.edges).map(item => item.text.attr('text'));
  const result = api.prepare(f.options);
  assert.ok(result.unresolvedCount > 0);
  assert.deepEqual(f.nodes.concat(f.edges).map(item => item.text.attr('text')), text);
  assert.equal(f.nodes.length, 6); assert.equal(f.edges.length, 5); result.restore();
});

test('an adapter failure releases paint ownership without replacing label or path geometry', () => {
  const f = fixture(2), applyLabels = f.options.applyLabels;
  let count = 0;
  f.options.applyLabels = () => {
    if (++count === 2) throw new Error('synthetic label adapter failure');
    return applyLabels();
  };
  assert.throws(() => api.prepare(f.options), /synthetic label adapter failure/);
  for (const [item, attrs, clip] of f.baselineShapes.filter(([item]) => item.kind === 'edge')) {
    assert.deepEqual(item.text.attr(), attrs); assert.equal(item.clip, clip);
  }
  assert.equal(f.options.graph.get('autoPaint'), true); assert.equal(f.cleanupCalls.length, 0);
  assert.equal(f.groups.length, 0);
  f.restoreCamera(); f.applyLabels(); assert.deepEqual(f.camera(), f.originalCamera);
});

test('image sampling reports an annotation collision while preserving both anchored texts', () => {
  const f=fixture(2),owner=f.nodes[0],base=owner.getContainer(),model=f.edges[0].getModel();
  const note={get:name=>name==='name'?'funds-node-annotation':undefined,getCanvasBBox:()=>envelope(
    {minX:25,minY:-175,maxX:185,maxY:-120},(x,y)=>f.options.graph.getCanvasByPoint(x,y))};
  owner.getContainer=()=>({get:name=>name==='children'?[...base.get('children'),note]:base.get(name),
    getCanvasBBox:()=>union([base.getCanvasBBox(),note.getCanvasBBox()])});
  const before = f.edges.map(item => copy(item.text.attr()));
  const result=api.prepare(f.options);
  assert.ok(f.edges.some(edge => intersects(edge.text.getCanvasBBox(),note.getCanvasBBox())), 'fixture retains the actual annotation conflict');
  assert.ok(result.unresolvedCount > 0);
  f.edges.forEach((item, i) => assert.deepEqual(item.text.attr(), before[i]));
  assert.equal(result.movedLabels, 0); assert.equal(f.groups.length, 0);
  assert.equal(f.edges[0].getModel(),model);result.restore();
});
