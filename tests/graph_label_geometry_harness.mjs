import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const require = createRequire(import.meta.url);
const geometry = require('../static/graph-label-geometry.js');
const data = (count, edges = []) => ({ nodes: Array.from({ length: count }, (_, i) => ({ id: String(i) })), edges });
const relation = (source, target) => ({ source: String(source), target: String(target) });
const plain = value => JSON.parse(JSON.stringify(value));

class Shape {
  constructor(kind, options, group) {
    this.kind = kind; this.name = options.name; this.capture = options.capture !== false;
    this.attrs = { ...options.attrs }; this.group = group; this.visible = true; this.clip = null;
  }
  get(name) { return this[name]; }
  set(name, value) { this[name] = value; }
  attr(name, value) {
    if (arguments.length === 0) return this.attrs;
    if (typeof name === 'object') { Object.assign(this.attrs, name); return; }
    if (arguments.length > 1) { this.attrs[name] = value; return; }
    return this.attrs[name];
  }
  setMatrix(matrix) { this.attrs.matrix = matrix; }
  setClip(clip) { this.clip = plain(clip); }
  getClip() { return this.clip; }
  getBBox() {
    if (this.kind === 'text') {
      const { x = 0, y = 0, fontSize = 12, lineHeight = fontSize * 1.4, text = '' } = this.attrs;
      const lines = String(text).split('\n');
      const width = Math.max(...lines.map(line => line.length)) * fontSize * 0.6;
      // A deliberately asymmetric baseline must be measured, not guessed.
      return { minX: x - width / 2 + 1, maxX: x + width / 2 + 1,
        minY: y - fontSize * 0.7, maxY: y + fontSize * 0.5 + (lines.length - 1) * lineHeight };
    }
    const path = this.attrs.path || [];
    const xs = [], ys = [];
    for (const command of path) for (let i = 1; i + 1 < command.length; i += 2) {
      xs.push(command[i]); ys.push(command[i + 1]);
    }
    return { minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys) };
  }
  isVisible() { return this.visible; }
  show() { this.visible = true; }
  hide() { this.visible = false; }
  remove() { this.group.children = this.group.children.filter(shape => shape !== this); }
  toBack() {}
}
class Group {
  constructor() { this.children = []; }
  get(name) { return name === 'children' ? this.children : undefined; }
  addShape(kind, options) { const shape = new Shape(kind, options, this); this.children.push(shape); return shape; }
}
function fixture() {
  const group = new Group();
  const path = [['M', 10, 20], ['Q', 280, 15, 200, 240]];
  const style = {
    stroke: '#123456', lineWidth: 7, opacity: 0.85, strokeOpacity: 0.9, fillOpacity: 1,
    lineDash: [6, 4], lineDashOffset: 3, lineCap: 'round', lineJoin: 'round',
    endArrow: { path: [['M', 0, 0], ['L', -10, -5], ['L', -10, 5], ['Z']], d: 0, fill: '#123456' }
  };
  const key = group.addShape('path', { name: 'key-shape', capture: true, attrs: { ...plain(style), path: plain(path) } });
  key.getPoint = ratio => { assert.equal(ratio, 0.5); return { x: 172.5, y: 72.5 }; };
  const text = group.addShape('text', { name: 'text-shape', capture: true, attrs: {
    x: 100, y: 50, text: 'CNY 1,280,000.01', fontSize: 14, stroke: '#000', lineWidth: 2,
    matrix: [0, 1, 0, -1, 0, 0, 13, 18, 1]
  } });
  group.addShape('rect', { name: 'text-bg-shape', attrs: { fill: '#000', stroke: '#fff' } });
  group.addShape('path', { name: 'funds-label-leader', attrs: { path: [['M', 0, 0], ['L', 20, 20]] } });
  const outline = group.addShape('path', { name: 'funds-edge-selection-outline', capture: false,
    attrs: { path: plain(path), stroke: '#63daef', lineWidth: 12, opacity: 0.25 } });
  const model = { source: 'SYN-A', target: 'SYN-B', style: plain(style),
    amount_totals: [{ currency: 'CNY', amount: '10000000000000000.01' }], label: 'CNY 1,280,000.01' };
  const item = { getContainer: () => group, getKeyShape: () => key, getModel: () => model };
  return { item, group, key, text, outline, model, path, style };
}
const visible = f => f.group.children.find(shape => shape.name === 'funds-edge-visible-path');
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} differs from ${expected}`);

function nodeFixture(kind, body, label, fontSize = 13) {
  const group = new Group();
  const key = group.addShape(kind, { name: 'key-shape', attrs: { fill: '#63b69d', x: body.minX, y: body.minY } });
  // Neither model.size nor a guessed radius can substitute for the rendered body.
  key.getBBox = () => ({ ...body, width: body.maxX - body.minX, height: body.maxY - body.minY });
  const text = group.addShape('text', { name: 'text-shape', attrs: {
    x: 95, y: -70, text: label, fontSize, lineHeight: fontSize + 5,
    stroke: '#000000', lineWidth: 3
  } });
  group.addShape('rect', { name: 'text-bg-shape', attrs: { fill: '#101820', stroke: '#fff' } });
  group.addShape('path', { name: 'funds-label-leader', attrs: { path: [['M', 0, 0], ['L', 95, -70]] } });
  const model = { id: 'SYN-C003', x: 437.8, y: 260, size: 4, label,
    data: { balance: '10000000000000000.01' } };
  const item = { getContainer: () => group, getKeyShape: () => key, getModel: () => model };
  return { item, group, key, text, model };
}

for (const [kind, body, label, fontSize] of [
  ['circle', { minX: -18, minY: -18, maxX: 18, maxY: 18 }, '合成丙公司\nSYN-C003', 13],
  ['circle', { minX: -36, minY: -36, maxX: 36, maxY: 36 }, '更长的账户显示名称\nSYN-C003\n补充标签', 24],
  ['image', { minX: -42, minY: -21, maxX: 38, maxY: 31 }, 'Synthetic photograph\nSYN-C003', 18]
]) for (const zoom of [0.4, 1, 2]) {
  test(`${kind} ${fontSize}px node label stays below actual body at zoom ${zoom}`, () => {
    const f = nodeFixture(kind, body, label, fontSize);
    const before = JSON.stringify(f.model), keyBefore = JSON.stringify(f.key.attr());
    const result = geometry.placeNode(f.item, { zoom });
    assert.equal(result.mode, 'bottom');
    const box = f.text.getBBox();
    near((box.minX + box.maxX) / 2, (body.minX + body.maxX) / 2);
    near((box.minY - body.maxY) * zoom, 6);
    assert.deepEqual(result.bbox, { ...box, width: box.maxX - box.minX, height: box.maxY - box.minY });
    assert.deepEqual(geometry.placeNode(f.item, { zoom }), result, 'repeated paint passes cannot accumulate an offset');
    assert.equal(JSON.stringify(f.model), before, 'anchoring never changes node positions or exact facts');
    assert.equal(JSON.stringify(f.key.attr()), keyBefore, 'anchoring never changes the node body');
    assert.equal(f.text.attr('text'), label);
    assert.equal(f.text.attr('stroke'), null); assert.equal(f.text.attr('lineWidth'), 0);
    assert.equal(f.group.children.some(shape => ['text-bg-shape', 'funds-label-leader'].includes(shape.name)), false);
  });
}

test('node labels follow body resizing and zoom without retaining the old collision displacement', () => {
  const body = { minX: -18, minY: -18, maxX: 18, maxY: 18 };
  const f = nodeFixture('circle', body, '合成丙公司\nSYN-C003');
  for (const [radius, zoom] of [[18, 1], [42, 0.4], [12, 2], [18, 1]]) {
    Object.assign(body, { minX: -radius, minY: -radius, maxX: radius, maxY: radius });
    // Simulate a stale bottom/top/side collision result before the next pass.
    f.text.attr({ x: 150, y: -110 });
    geometry.placeNode(f.item, { zoom });
    const box = f.text.getBBox(); near((box.minX + box.maxX) / 2, 0);
    near((box.minY - body.maxY) * zoom, 6);
  }
});

test('node canvas shadows cannot consume the fixed six-screen-pixel text gap', () => {
  const body = { minX: -18, minY: -18, maxX: 18, maxY: 18 };
  const f = nodeFixture('circle', body, '合成丙公司\nSYN-C003');
  f.key.attr({ shadowBlur: 5 });
  const modelBefore = JSON.stringify(f.model), bodyBefore = JSON.stringify(f.key.attr());
  for (const zoom of [0.4, 1, 2]) {
    const project = box => ({ minX: 170 + box.minX * zoom, minY: 230 + box.minY * zoom,
      maxX: 170 + box.maxX * zoom, maxY: 230 + box.maxY * zoom });
    f.key.getCanvasBBox = () => {
      const box = project(f.key.getBBox());
      return { minX: box.minX - 5, minY: box.minY - 5, maxX: box.maxX + 5, maxY: box.maxY + 5 };
    };
    f.text.getCanvasBBox = () => project(f.text.getBBox());
    const result = geometry.placeNode(f.item, { zoom });
    const screenBody = f.key.getCanvasBBox(), screenText = f.text.getCanvasBBox();
    near(screenText.minY - screenBody.maxY, 6);
    near((screenText.minX + screenText.maxX) / 2, (screenBody.minX + screenBody.maxX) / 2);
    assert.deepEqual(geometry.placeNode(f.item, { zoom }), result);
  }
  assert.equal(JSON.stringify(f.model), modelBefore); assert.equal(JSON.stringify(f.key.attr()), bodyBefore);
});

test('UMD loads without window and exposes the same API as CommonJS', () => {
  const sandbox = {};
  vm.runInNewContext(readFileSync(new URL('../static/graph-label-geometry.js', import.meta.url), 'utf8'), sandbox);
  assert.deepEqual(Object.keys(sandbox.FundsGraphLabelGeometry).sort(), Object.keys(geometry).sort());
  assert.equal(sandbox.FundsGraphLabelGeometry.isSimpleGraph(data(2, [relation(0, 1)])), true);
  const browser = { window: {} };
  vm.runInNewContext(readFileSync(new URL('../static/graph-label-geometry.js', import.meta.url), 'utf8'), browser);
  assert.equal(typeof browser.window.FundsGraphLabelGeometry.placeEdge, 'function');
});

const textStyle = (changes = {}) => ({ fontStyle: 'normal', fontWeight: 400, fontSize: 13,
  fontFamily: 'Arial, sans-serif', lineHeight: 18, ...changes });
function measureContext() {
  let reads = 0, writes = 0, font = '';
  return {
    get font() { return font; }, set font(value) { font = value; writes++; },
    get reads() { return reads; }, get writes() { return writes; },
    direction: 'inherit', fontKerning: 'auto', letterSpacing: '0px', wordSpacing: '0px',
    measureText(line) {
      reads++;
      const width = Array.from(line).length * (parseFloat(/([\d.]+)px/.exec(font)?.[1]) || 13);
      return { width, actualBoundingBoxLeft: width * .4 + 1, actualBoundingBoxRight: width * .6 + 2 };
    }
  };
}
// The former caller's formula stays independent of the new cache implementation.
function uncachedTextSize(context, value, style) {
  const lines = String(value ?? '').split('\n');
  if (context) context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize}px ${style.fontFamily}`;
  let width = 0;
  for (const line of lines) {
    const metrics = context?.measureText?.(line);
    width = Math.max(width, metrics ? Math.max(metrics.width,
      (metrics.actualBoundingBoxLeft || 0) + (metrics.actualBoundingBoxRight || 0)) : Array.from(line).length * style.fontSize);
  }
  return { width: width + 12, height: style.fontSize + Math.max(0, lines.length - 1) * style.lineHeight + 6 };
}

test('shared text metrics exactly preserve former dimensions, font writes and immutable receipts', () => {
  const context = measureContext(), style = textStyle(), before = JSON.stringify(style);
  for (const value of ['', null, undefined, '中文账户 SYN-C003', '-9,999.99', '💰👨‍👩‍👧‍👦', '\n', '首行\n\nCNY 10000000000000000.01']) {
    const expected = uncachedTextSize(measureContext(), value, style);
    const result = geometry.measureText(context, value, style), priorReads = context.reads, priorWrites = context.writes;
    assert.deepEqual(result, expected);
    assert.deepEqual(geometry.measureText(context, value, style), expected);
    assert.equal(context.reads, priorReads, 'an identical text/style pair does not call Canvas measureText again');
    assert.equal(context.writes, priorWrites + 1, 'a hit still applies the requested Canvas font');
    result.width = -999;
    assert.deepEqual(geometry.measureText(context, value, style), expected, 'callers cannot mutate retained dimensions');
    assert.deepEqual(geometry.measureText(null, value, style), uncachedTextSize(null, value, style));
  }
  assert.equal(JSON.stringify(style), before);
});

test('text metrics key follows the actual Canvas font, shaping properties, context and measuring method', () => {
  const context = measureContext(), style = textStyle(), value = '合成账户\nCNY -90.01';
  const initial = geometry.measureText(context, value, style);
  for (const [name, next] of Object.entries({ fontKerning: 'none', fontStretch: 'expanded', fontVariantCaps: 'small-caps',
    letterSpacing: '2px', wordSpacing: '3px', textRendering: 'optimizeLegibility', direction: 'rtl', lang: 'zh-CN',
    textAlign: 'end', textBaseline: 'top' })) {
    context[name] = next; const reads = context.reads;
    assert.deepEqual(geometry.measureText(context, value, style), initial);
    assert.equal(context.reads, reads + 2, `${name} is a shaping dependency`);
    geometry.measureText(context, value, style); assert.equal(context.reads, reads + 2);
  }
  const priorReads = context.reads;
  context.measureText = () => ({ width: 800 });
  assert.equal(geometry.measureText(context, value, style).width, 812, 'replacement method cannot reuse prior metrics');
  assert.equal(context.reads, priorReads);
  const other = measureContext(); geometry.measureText(other, value, style); assert.equal(other.reads, 2);
  for (const changed of [textStyle({ fontWeight: 700 }), textStyle({ fontStyle: 'italic' }),
    textStyle({ fontFamily: 'monospace' }), textStyle({ fontSize: 24 }), textStyle({ lineHeight: 30 })]) {
    assert.deepEqual(geometry.measureText(other, value, changed), uncachedTextSize(measureContext(), value, changed));
  }
  let actualFont = 'normal 400 11px Arial';
  const retained = { get font() { return actualFont; }, set font(_) {},
    measureText: () => ({ width: actualFont.includes('11px') ? 11 : 27 }) };
  assert.equal(geometry.measureText(retained, value, style).width, 23);
  actualFont = 'normal 400 27px Arial';
  assert.equal(geometry.measureText(retained, value, style).width, 39,
    'a rejected Canvas font assignment must be keyed by the retained actual font');
});

test('1000 unique node and relationship labels reuse measurements without a per-pass cache thrash', () => {
  const context = measureContext(), style = textStyle();
  const texts = Array.from({ length: 2000 }, (_, i) => `${i < 1000 ? '合成账户' : 'CNY'} ${i}\nSYN-${i}`);
  const first = texts.map(text => geometry.measureText(context, text, style));
  const reads = context.reads;
  assert.equal(reads, 4000);
  assert.deepEqual(texts.map(text => geometry.measureText(context, text, style)), first);
  assert.equal(context.reads, reads, 'a repeated 2000-label pass performs no additional Canvas text measurements');
  geometry.clearTextMeasurements(); geometry.measureText(context, texts[0], style);
  assert.equal(context.reads, reads + 2, 'a host can explicitly revoke measurements after a silent loaded FontFace edit');
});

test('text cache has finite entry and character limits and never truncates oversized labels', () => {
  const context = measureContext(), style = textStyle();
  for (let i = 0; i < 4097; i++) geometry.measureText(context, `SYN-${i}`, style);
  let reads = context.reads; geometry.measureText(context, 'SYN-0', style);
  assert.equal(context.reads, reads + 1, 'oldest entry is evicted at the entry bound');
  for (let i = 0; i < 40; i++) geometry.measureText(context, `${i}:` + '合'.repeat(7900), style);
  reads = context.reads; geometry.measureText(context, '0:' + '合'.repeat(7900), style);
  assert.equal(context.reads, reads + 1, 'the accumulated character bound also evicts old entries');
  const oversized = '💰'.repeat(5000);
  const expected = uncachedTextSize(measureContext(), oversized, style);
  reads = context.reads;
  assert.deepEqual(geometry.measureText(context, oversized, style), expected);
  assert.deepEqual(geometry.measureText(context, oversized, style), expected);
  assert.equal(context.reads, reads + 2, 'oversized text is measured in full each time rather than cached or truncated');
});

test('text metrics are revoked by font events and loading-state changes across UMD realms', () => {
  const events = new Map(), fontSet = { status: 'loaded', addEventListener: (name, callback) => events.set(name, callback) };
  const sandbox = { window: {}, document: { fonts: fontSet } };
  vm.runInNewContext(readFileSync(new URL('../static/graph-label-geometry.js', import.meta.url), 'utf8'), sandbox);
  const api = sandbox.window.FundsGraphLabelGeometry, context = measureContext(), style = textStyle(), value = '中文\nCNY -1.01';
  let expected = uncachedTextSize(measureContext(), value, style);
  for (const event of ['loadingdone', 'loadingerror']) {
    assert.deepEqual(plain(api.measureText(context, value, style)), expected);
    let reads = context.reads; api.measureText(context, value, style); assert.equal(context.reads, reads);
    events.get(event)(); api.measureText(context, value, style); assert.equal(context.reads, reads + 2);
  }
  for (const status of ['loading', 'loaded']) {
    const reads = context.reads; fontSet.status = status;
    assert.deepEqual(plain(api.measureText(context, value, style)), expected);
    assert.equal(context.reads, reads + 2, 'status changes invalidate before the next font completion event');
  }
});

test('simple means a bounded forest, not a business-role inference', () => {
  const graph = data(5, [relation(0, 1), relation(1, 2), relation(3, 4)]);
  graph.nodes[0].amount_totals = [{ currency: 'USD', amount: '999999999999999999.99' }];
  const before = JSON.stringify(graph);
  assert.equal(geometry.isSimpleGraph(graph), true);
  assert.equal(JSON.stringify(graph), before);
  assert.equal(geometry.isSimpleGraph(data(24)), true);
  assert.equal(geometry.isSimpleGraph(data(25)), false);
  assert.equal(geometry.isSimpleGraph(data(0)), true);
  assert.equal(geometry.isSimpleGraph({ nodes: [{ account: 'SYN-A' }], edges: [] }), true);
});

test('cycles, self loops, parallel and opposite directions are complex', () => {
  for (const edges of [
    [relation(0, 0)], [relation(0, 1), relation(1, 0)], [relation(0, 1), relation(0, 1)],
    [relation(0, 1), relation(1, 2), relation(2, 0)]
  ]) assert.equal(geometry.isSimpleGraph(data(3, edges)), false);
  assert.equal(geometry.isSimpleGraph(data(3, [relation(0, 7)])), false);
  assert.equal(geometry.isSimpleGraph({ nodes: [{ id: 'A' }, { id: 'A' }], edges: [] }), false);
  assert.equal(geometry.isSimpleGraph(null), false);
  assert.equal(geometry.isSimpleGraph({ nodes: [{ id: 'undefined' }, { id: 'A' }], edges: [{ target: 'A' }] }), false);
});

test('maximum undirected degree is five, independently of transfer direction', () => {
  assert.equal(geometry.isSimpleGraph(data(7, Array.from({ length: 5 }, (_, i) => relation(i + 1, 0)))), true);
  assert.equal(geometry.isSimpleGraph(data(7, Array.from({ length: 6 }, (_, i) => relation(0, i + 1)))), false);
});

test('the same eleven-node forest is simple when sparse and complex when branches cross', () => {
  const edges = [['A', 'B'], ['B', 'C'], ['C', 'F'], ['D', 'A'], ['E', 'B'], ['G', 'F'],
    ['A', 'H'], ['I', 'B'], ['J', 'C'], ['F', 'K']].map(([source, target]) => relation(source, target));
  const sparse = { A: [230, 255], B: [505, 255], C: [780, 255], F: [1055, 255],
    D: [135, 65], E: [465, 65], G: [1030, 65], H: [115, 445], I: [430, 465], J: [735, 465], K: [1085, 445] };
  const dense = { A: [430, 205], B: [680, 265], C: [500, 405], F: [880, 195],
    D: [860, 450], E: [400, 465], G: [320, 75], H: [940, 70], I: [720, 50], J: [1020, 370], K: [700, 475] };
  const graphAt = positions => ({ nodes: Object.entries(positions).map(([id, [x, y]]) => ({ id, x, y })), edges });
  assert.equal(geometry.isSimpleGraph(graphAt(sparse)), true);
  assert.equal(geometry.isSimpleGraph(graphAt(dense)), false);
  const withoutGeometry = { nodes: Object.keys(dense).map(id => ({ id })), edges };
  assert.equal(geometry.isSimpleGraph(withoutGeometry), true, 'full graph caches retain structural-only classification');
  const incomplete = graphAt(dense); delete incomplete.nodes[0].x;
  assert.equal(geometry.isSimpleGraph(incomplete), true, 'geometry is used only when all coordinates are finite');
});

test('crossing detection ignores shared endpoint IDs but rejects independent overlaps and zero lengths', () => {
  const graphAt = (coordinates, edges) => ({ nodes: coordinates.map(([x, y], id) => ({ id: String(id), x, y })),
    edges: edges.map(([source, target]) => relation(source, target)) });
  assert.equal(geometry.isSimpleGraph(graphAt([[0, 0], [100, 100], [0, 100], [100, 0]], [[0, 1], [2, 3]])), false);
  assert.equal(geometry.isSimpleGraph(graphAt([[0, 0], [100, 100], [0, 100]], [[0, 1], [1, 2]])), true);
  assert.equal(geometry.isSimpleGraph(graphAt([[0, 0], [100, 0], [25, 0], [125, 0]], [[0, 1], [2, 3]])), false);
  assert.equal(geometry.isSimpleGraph(graphAt([[0, 0], [0, 100], [0, 25], [0, 125]], [[0, 1], [2, 3]])), false);
  assert.equal(geometry.isSimpleGraph(graphAt([[0, 0], [0, 0]], [[0, 1]])), false);
  assert.equal(geometry.isSimpleGraph(graphAt([[0, 0], [50, 0], [100, 0], [150, 0]], [[0, 1], [2, 3]])), true);
  assert.equal(geometry.isSimpleGraph(graphAt([[0, 0], [50, 0], [50, 0], [100, 0]], [[0, 1], [2, 3]])), true,
    'a geometric point touch is not a strict crossing or a positive-length overlap');
});

const compact = geometry.compactControlPoint;
const quadratic = (source, control, target, ratio) => ({
  x: (1 - ratio) ** 2 * source.x + 2 * (1 - ratio) * ratio * control.x + ratio ** 2 * target.x,
  y: (1 - ratio) ** 2 * source.y + 2 * (1 - ratio) * ratio * control.y + ratio ** 2 * target.y
});

test('compact curves follow every direction without overshooting along their chord', () => {
  const source = { x: 377, y: 430 };
  for (const target of [{ x: 667, y: 323 }, { x: 367, y: 103 }, { x: 80, y: 324 },
    { x: 190, y: 651 }, { x: 639, y: 648 }, { x: 697, y: 430 },
    { x: 377, y: 110 }, { x: 697, y: 430.000001 }, { x: 377.000001, y: 110 }]) {
    const control = compact(source, target), dx = target.x - source.x, dy = target.y - source.y;
    const span = Math.hypot(dx, dy), middle = { x: (source.x + target.x) / 2, y: (source.y + target.y) / 2 };
    near(((control.x - source.x) * dx + (control.y - source.y) * dy) / (span * span), .5);
    assert.ok(Math.hypot(control.x - middle.x, control.y - middle.y) <= 28 + 1e-9);
    let previous = -1;
    for (let step = 0; step <= 100; step++) {
      const value = quadratic(source, control, target, step / 100);
      const projection = ((value.x - source.x) * dx + (value.y - source.y) * dy) / (span * span);
      near(projection, step / 100);
      assert.ok(projection >= previous && projection <= 1 + 1e-9, 'curve never returns from beyond its target');
      previous = projection;
    }
    const reverse = compact(target, source);
    near((control.x + reverse.x) / 2, middle.x); near((control.y + reverse.y) / 2, middle.y);
    assert.ok(Math.hypot(control.x - reverse.x, control.y - reverse.y) > 0, 'reciprocal directions occupy opposite sides');
  }
});

test('compact curves stay continuous across the former 50px vertical threshold', () => {
  const source = { x: 30, y: 20 };
  const before = compact(source, { x: 70, y: 69.999 });
  const after = compact(source, { x: 70, y: 70.001 });
  assert.ok(Math.hypot(after.x - before.x, after.y - before.y) < .002);
});

test('compact curves handle tiny, coincident and invalid geometry without NaN', () => {
  const source = { x: 30, y: 20 };
  assert.deepEqual(compact(source, source), source);
  for (const target of [{ x: 30.0000000001, y: 20.0000000001 }, { x: 30.01, y: 20.02 }]) {
    const control = compact(source, target);
    assert.ok(Number.isFinite(control.x) && Number.isFinite(control.y));
    assert.ok(Math.hypot(control.x - source.x, control.y - source.y) <= Math.hypot(target.x - source.x, target.y - source.y));
  }
  for (const value of [null, {}, { x: NaN, y: 0 }, { x: 0, y: Infinity }, { x: '0', y: 0 }]) {
    assert.equal(compact(value, source), null); assert.equal(compact(source, value), null);
  }
});

test('inline suppression clears a cached key arrow without affecting the visible copy or hit path', () => {
  const f = fixture(); let destroyed = 0;
  f.key.set('endArrowShape', { destroy: () => { destroyed++; } });
  geometry.placeEdge(f.item, { style: f.style, outline: f.outline });
  assert.equal(f.key.get('endArrowShape'), null, 'G6 can retain the previous arrow even after endArrow:false');
  assert.equal(destroyed, 1);
  assert.equal(f.key.attr('endArrow'), false);
  assert.deepEqual(visible(f).attr('endArrow'), f.style.endArrow);
  assert.deepEqual(f.key.attr('path'), f.path); assert.equal(f.key.capture, true); assert.equal(f.key.getClip(), null);
});

test('paintEdge recolours an inline visible path and cached arrows without changing geometry or hit suppression', () => {
  const f = fixture();
  const startArrow = { ...plain(f.style.endArrow), d: 3 };
  const style = { ...f.style, startArrow };
  geometry.placeEdge(f.item, { style, outline: f.outline });
  const drawn = visible(f), clip = drawn.getClip(), outlineClip = f.outline.getClip();
  const textBefore = plain(f.text.attr()), modelBefore = JSON.stringify(f.model), pathBefore = plain(f.key.attr('path'));
  const caches = [];
  for (const name of ['startArrow', 'endArrow']) {
    const cache = new Shape('path', { attrs: { ...plain(drawn.attr(name)), stroke: '#123456', matrix: [1, 0, 0, 0, 1, 0, 17, 23, 1] } }, f.group);
    drawn.set(name + 'Shape', cache); caches.push(cache);
  }
  const arrowGeometry = caches.map(shape => ({ path: plain(shape.attr('path')), matrix: plain(shape.attr('matrix')), d: shape.attr('d') }));
  assert.equal(geometry.paintEdge(f.item, { stroke: '#2367ad', lineWidth: 99, opacity: 1, strokeOpacity: 1,
    path: [['M', 0, 0]], startArrow: { fill: '#abcdef', stroke: '#fedcba', path: 'IGNORED', d: 99 },
    endArrow: { fill: '#2367ad', path: 'IGNORED', d: 99 } }), true);
  assert.equal(drawn.attr('stroke'), '#2367ad'); assert.equal(f.key.attr('stroke'), '#2367ad');
  assert.equal(f.key.attr('opacity'), 0); assert.equal(f.key.attr('strokeOpacity'), 0); assert.equal(f.key.attr('fillOpacity'), 0);
  assert.equal(f.key.attr('startArrow'), false); assert.equal(f.key.attr('endArrow'), false);
  assert.equal(f.key.capture, true); assert.equal(drawn.capture, false);
  assert.equal(drawn.attr('lineWidth'), 7); assert.equal(drawn.attr('opacity'), .85); assert.equal(drawn.attr('strokeOpacity'), .9);
  assert.deepEqual(drawn.attr('lineDash'), [6, 4]); assert.deepEqual(f.key.attr('path'), pathBefore);
  assert.equal(drawn.getClip(), clip); assert.equal(f.outline.getClip(), outlineClip);
  assert.deepEqual(f.text.attr(), textBefore); assert.equal(JSON.stringify(f.model), modelBefore);
  assert.deepEqual(caches.map(shape => ({ path: plain(shape.attr('path')), matrix: plain(shape.attr('matrix')), d: shape.attr('d') })), arrowGeometry);
  assert.equal(caches[0].attr('fill'), '#abcdef'); assert.equal(caches[0].attr('stroke'), '#fedcba');
  assert.equal(caches[1].attr('fill'), '#2367ad'); assert.equal(caches[1].attr('stroke'), '#2367ad');
  assert.deepEqual(drawn.attr('startArrow'), { ...startArrow, fill: '#abcdef', stroke: '#fedcba' });
  assert.deepEqual(drawn.attr('endArrow'), { ...f.style.endArrow, fill: '#2367ad' });
  geometry.restoreEdge(f.item);
  assert.equal(f.key.attr('stroke'), '#2367ad', 'restoration uses the updated base paint');
  assert.equal(f.key.attr('opacity'), .85); assert.equal(f.key.attr('lineWidth'), 7);
  assert.deepEqual(f.key.attr('endArrow'), { ...f.style.endArrow, fill: '#2367ad' });
});

test('paintEdge preserves unclipped anchors, hidden paths and disabled arrows when given a full rendering style', () => {
  const f = fixture(), muted = { ...f.style, opacity: 0, endArrow: false };
  geometry.placeEdge(f.item, { style: muted, above: true, outline: f.outline });
  const textBefore = plain(f.text.attr()), keyBefore = plain(f.key.attr()), childrenBefore = f.group.children.slice();
  f.key.hide();
  geometry.paintEdge(f.item, { ...f.style, stroke: '#7f8b99', opacity: 1, endArrow: { ...f.style.endArrow, fill: '#7f8b99' } });
  assert.deepEqual(f.key.attr(), { ...keyBefore, stroke: '#7f8b99' });
  assert.equal(f.key.isVisible(), false); assert.equal(f.key.attr('endArrow'), false);
  assert.deepEqual(f.text.attr(), textBefore); assert.deepEqual(f.group.children, childrenBefore);
  assert.equal(visible(f), undefined); assert.equal(f.key.getClip(), null);
  geometry.restoreEdge(f.item);
  assert.equal(f.key.attr('opacity'), 0); assert.equal(f.key.attr('endArrow'), false);
  assert.equal(geometry.paintEdge(null, {}), false); assert.equal(geometry.paintEdge(f.item, null), false);
});

test('arrow cache synchronization only removes explicitly false arrows and is idempotent', () => {
  const f = fixture(), removed = [], start = { destroy: () => removed.push('start') }, end = { destroy: () => removed.push('end') };
  let invalidations = 0; f.key.clearCacheBBox = () => { invalidations++; };
  f.key.set('startArrowShape', start); f.key.set('endArrowShape', end);
  assert.equal(geometry.syncArrows(f.key), false, 'missing startArrow is not an explicit suppression');
  assert.equal(f.key.get('startArrowShape'), start); assert.equal(f.key.get('endArrowShape'), end);
  f.key.attr('startArrow', false);
  assert.equal(geometry.syncArrows(f.key), true); assert.equal(f.key.get('startArrowShape'), null);
  assert.equal(f.key.get('endArrowShape'), end, 'the enabled endpoint arrow retains its cache');
  assert.deepEqual(removed, ['start']); assert.equal(invalidations, 1);
  assert.equal(geometry.syncArrows(f.key), false); assert.equal(invalidations, 1);
  f.key.attr('endArrow', false);
  assert.equal(geometry.syncArrows(f.key), true); assert.equal(f.key.get('endArrowShape'), null);
  assert.deepEqual(removed, ['start', 'end']); assert.equal(invalidations, 2);
  for (const value of [null, {}, { attr: () => false }, { attr: () => false, get: () => end }]) {
    assert.equal(geometry.syncArrows(value), false);
  }
});

test('arrow synchronization clears G6-normalized disabled arrows while retaining enabled paths', () => {
  for (const setting of [false, { path: '' }, { path: [] }]) {
    const f = fixture(); let destroyed = 0;
    const cached = { attrs: { path: 'M0,0 L-10,-5 L-10,5 Z' }, destroy: () => { destroyed++; } };
    f.key.attr({ startArrow: setting, endArrow: setting });
    f.key.set('startArrowShape', cached);
    f.key.set('endArrowShape', { ...cached });
    assert.equal(geometry.syncArrows(f.key), true, 'disabled styles must revoke the previous nonempty arrow path');
    assert.equal(f.key.get('startArrowShape'), null); assert.equal(f.key.get('endArrowShape'), null);
    assert.equal(destroyed, 2);
    assert.equal(geometry.syncArrows(f.key), false); assert.equal(destroyed, 2);
  }
  for (const setting of [true, { path: 'M0,0 L-10,-5 L-10,5 Z' }, { path: [['M', 0, 0], ['L', -10, -5]] }, {}]) {
    const f = fixture(), cached = { destroy: () => assert.fail('enabled arrows stay owned by G6') };
    f.key.attr({ startArrow: setting, endArrow: setting });
    f.key.set('startArrowShape', cached); f.key.set('endArrowShape', cached);
    assert.equal(geometry.syncArrows(f.key), false);
    assert.equal(f.key.get('startArrowShape'), cached); assert.equal(f.key.get('endArrowShape'), cached);
  }
});

test('cached visible-path arrows cannot survive muted styles or hidden-label restoration', () => {
  const f = fixture(); geometry.placeEdge(f.item, { style: f.style, outline: f.outline });
  let removed = 0;
  visible(f).set('endArrowShape', { destroy: () => { removed++; } });
  const muted = { ...f.style, endArrow: false };
  geometry.placeEdge(f.item, { style: muted, outline: f.outline });
  assert.equal(visible(f).attr('endArrow'), false); assert.equal(visible(f).get('endArrowShape'), null);
  assert.equal(removed, 1); assert.equal(f.key.attr('endArrow'), false);
  f.key.set('endArrowShape', { destroy: () => { removed++; } });
  geometry.placeEdge(f.item, { style: muted, visible: false, outline: f.outline });
  assert.equal(f.key.get('endArrowShape'), null); assert.equal(removed, 2);
  assert.equal(visible(f), undefined); assert.equal(f.key.attr('opacity'), f.style.opacity);
  geometry.restoreEdge(f.item, { style: f.style });
  assert.deepEqual(f.key.attr('endArrow'), f.style.endArrow, 'restoring detail permits G6 to recreate the enabled arrow');
});

test('unchanged cached placements still clear arrows recreated by a G6 path update', () => {
  const f = fixture(), options = { style: { ...f.style, endArrow: false } };
  const receipt = geometry.placeEdge(f.item, options); let removed = 0;
  f.key.set('endArrowShape', { destroy: () => { removed++; } });
  visible(f).set('endArrowShape', { destroy: () => { removed++; } });
  assert.deepEqual(geometry.placeEdge(f.item, options), receipt);
  assert.equal(f.key.get('endArrowShape'), null); assert.equal(visible(f).get('endArrowShape'), null);
  assert.equal(removed, 2);
  geometry.placeEdge(f.item, options); assert.equal(removed, 2);
});

test('plain removes only label backgrounds and leaders, preserves facts and appearance', () => {
  const f = fixture(), before = JSON.stringify(f.model);
  assert.equal(geometry.plain(f.item), f.text);
  assert.deepEqual(f.group.children.map(shape => shape.name), ['key-shape', 'text-shape', 'funds-edge-selection-outline']);
  assert.equal(f.text.attr('stroke'), null);
  assert.equal(f.text.attr('lineWidth'), 0);
  assert.equal(f.text.attr('shadowBlur'), 0);
  assert.equal(JSON.stringify(f.model), before);
  assert.equal(f.key.attr('stroke'), '#123456');
});

test('inline is a real clip on a non-capturing visible path; the complete key remains hittable', () => {
  const f = fixture(), before = JSON.stringify(f.model), styleBefore = JSON.stringify(f.style);
  const result = geometry.placeEdge(f.item, { style: f.style, outline: f.outline });
  assert.equal(result.mode, 'inline');
  assert.deepEqual(result.anchor, { x: 172.5, y: 72.5 }, 'uses actual path midpoint');
  near((result.bbox.minX + result.bbox.maxX) / 2, 172.5);
  near((result.bbox.minY + result.bbox.maxY) / 2, 72.5);
  assert.deepEqual(f.text.attr('matrix'), [1, 0, 0, 0, 1, 0, 0, 0, 1]);
  assert.deepEqual(f.key.attr('path'), f.path);
  assert.equal(f.key.attr('opacity'), 0);
  assert.equal(f.key.get('capture'), true);
  assert.equal(f.key.getClip(), null);
  assert.equal(f.key.attr('endArrow'), false);
  assert.equal(visible(f).capture, false);
  assert.deepEqual(visible(f).attr('path'), f.path);
  assert.deepEqual(visible(f).getClip(), f.outline.getClip());
  assert.equal(visible(f).getClip().type, 'path');
  assert.equal(f.group.children.some(shape => shape.kind === 'rect'), false);
  assert.equal(JSON.stringify(f.model), before);
  assert.equal(JSON.stringify(f.style), styleBefore);
});

test('the inner clip has opposite winding and exactly measured text clearance', () => {
  const f = fixture(), result = geometry.placeEdge(f.item, { style: f.style, gap: 6 });
  const clipPath = visible(f).getClip().attrs.path;
  assert.equal(clipPath.length, 10);
  assert.deepEqual(clipPath[5], ['M', result.bbox.minX - 6, result.bbox.minY - 6]);
  assert.deepEqual(clipPath[6], ['L', result.bbox.minX - 6, result.bbox.maxY + 6]);
  assert.deepEqual(clipPath[7], ['L', result.bbox.maxX + 6, result.bbox.maxY + 6]);
  const area = points => points.slice(0, 4).reduce((sum, p, i) => {
    const q = points[(i + 1) % 4]; return sum + p[1] * q[2] - q[1] * p[2];
  }, 0);
  assert.ok(area(clipPath.slice(0, 4)) * area(clipPath.slice(5, 9)) < 0);
  assert.ok(clipPath[0][1] < 10 && clipPath[0][2] < 15);
  assert.ok(clipPath[2][1] > 280 && clipPath[2][2] > 240);
});

test('dash, phase, line width, opacity and endpoint arrows survive the visible copy', () => {
  const f = fixture(); geometry.placeEdge(f.item, { style: f.style });
  for (const name of ['lineWidth', 'lineDashOffset', 'opacity', 'strokeOpacity', 'lineCap', 'lineJoin']) {
    assert.equal(visible(f).attr(name), f.style[name]);
  }
  assert.deepEqual(visible(f).attr('lineDash'), [6, 4]);
  assert.deepEqual(visible(f).attr('endArrow'), f.style.endArrow);
  visible(f).attr('endArrow').fill = '#ffffff';
  assert.equal(f.style.endArrow.fill, '#123456', 'drawing attributes never alias caller or model styles');
});

test('above mode uses actual text bounds plus half line width and gap; no extra visible layer', () => {
  const f = fixture(); geometry.placeEdge(f.item, { style: f.style, outline: f.outline });
  const result = geometry.placeEdge(f.item, { style: f.style, outline: f.outline, above: true, gap: 4 });
  assert.equal(result.mode, 'above');
  near(result.bbox.maxY, result.anchor.y - f.style.lineWidth / 2 - 4);
  assert.equal(visible(f), undefined);
  assert.equal(f.key.attr('opacity'), 0.85);
  assert.deepEqual(f.key.attr('endArrow'), f.style.endArrow);
  assert.equal(f.outline.getClip(), null);
});

test('hidden, empty and missing labels restore a continuous relationship', () => {
  for (const hide of ['flag', 'shape', 'empty', 'missing']) {
    const f = fixture(); geometry.placeEdge(f.item, { style: f.style, outline: f.outline });
    if (hide === 'shape') f.text.hide();
    if (hide === 'empty') f.text.attr({ text: '' });
    if (hide === 'missing') f.text.remove();
    const result = geometry.placeEdge(f.item, { style: f.style, outline: f.outline, visible: hide !== 'flag' });
    assert.equal(result.mode, 'none');
    assert.equal(visible(f), undefined);
    assert.equal(f.key.attr('opacity'), 0.85);
    assert.deepEqual(f.key.attr('path'), f.path);
    assert.equal(f.outline.getClip(), null);
  }
});

test('repeated calls are stable and style refresh after a G6 update retains the new path', () => {
  const f = fixture(); geometry.placeEdge(f.item, { style: f.style, outline: f.outline });
  const first = geometry.placeEdge(f.item, { style: f.style, outline: f.outline });
  const second = geometry.placeEdge(f.item, { style: f.style, outline: f.outline });
  assert.deepEqual(second, first);
  assert.equal(f.group.children.filter(shape => shape.name === 'funds-edge-visible-path').length, 1);
  const newPath = [['M', 40, 60], ['Q', 310, 25, 240, 300]];
  f.key.attr({ path: newPath, opacity: 1, strokeOpacity: 1, lineWidth: 2 });
  const updatedStyle = { ...f.style, lineWidth: 3, stroke: '#abcdef', lineDash: [] };
  geometry.placeEdge(f.item, { style: updatedStyle });
  assert.deepEqual(f.key.attr('path'), newPath);
  assert.deepEqual(visible(f).attr('path'), newPath);
  assert.equal(visible(f).attr('lineWidth'), 3);
  assert.equal(visible(f).attr('stroke'), '#abcdef');
  assert.deepEqual(visible(f).attr('lineDash'), []);
  assert.equal(f.outline.getClip(), null, 'a previous selection outline does not keep a stale gap');
});

test('explicit anchors support manual path ratios without automatic movement', () => {
  const f = fixture(), anchor = { x: 123.25, y: 81.5 };
  const result = geometry.placeEdge(f.item, { anchor, style: f.style });
  assert.deepEqual(result.anchor, anchor);
  near((result.bbox.minX + result.bbox.maxX) / 2, anchor.x);
  near((result.bbox.minY + result.bbox.maxY) / 2, anchor.y);
  geometry.restoreEdge(f.item);
  assert.equal(visible(f), undefined);
  assert.deepEqual(f.key.attr('endArrow'), f.style.endArrow);
});

test('one relationship gap does not clip or repaint another relationship', () => {
  const a = fixture(), b = fixture(), original = JSON.stringify(b.key.attr());
  geometry.placeEdge(a.item, { style: a.style });
  assert.equal(JSON.stringify(b.key.attr()), original);
  assert.equal(b.key.getClip(), null);
  assert.equal(visible(b), undefined);
});

function mutations(f) {
  const counts={writes:0,bboxes:0},wrapped=new Set();
  const watch=shape=>{
    if(wrapped.has(shape))return shape;wrapped.add(shape);
    for(const name of ['set','setMatrix','setClip','show','hide','remove','toBack'])if(typeof shape[name]==='function'){
      const original=shape[name];shape[name]=function(...args){counts.writes++;return original.apply(this,args);};
    }
    const attr=shape.attr;shape.attr=function(...args){if(args.length>1||args[0]&&typeof args[0]==='object')counts.writes++;return attr.apply(this,args);};
    const bbox=shape.getBBox;shape.getBBox=function(...args){counts.bboxes++;return bbox.apply(this,args);};
    return shape;
  };
  for(const shape of f.group.children)watch(shape);
  const add=f.group.addShape;f.group.addShape=function(...args){counts.writes++;return watch(add.apply(this,args));};
  return{counts,reset(){counts.writes=counts.bboxes=0;}};
}

test('plain is mutation-free after normalization and repairs later external decorations',()=>{
  const f=fixture(),calls=mutations(f);
  geometry.plain(f.item);calls.reset();geometry.plain(f.item);
  assert.equal(calls.counts.writes,0);assert.equal(calls.counts.bboxes,0);
  f.text.attr({stroke:'#fff',shadowBlur:4});f.group.addShape('rect',{name:'text-bg-shape',attrs:{fill:'#000'}});
  calls.reset();geometry.plain(f.item);assert.ok(calls.counts.writes>0);
  assert.equal(f.text.attr('stroke'),null);assert.equal(f.text.attr('shadowBlur'),0);
  assert.equal(f.group.children.some(shape=>shape.name==='text-bg-shape'),false);
  calls.reset();geometry.plain(f.item);assert.equal(calls.counts.writes,0);
});

test('already anchored nodes avoid every repeated mutation and remeasure only current body and glyph bounds',()=>{
  const body={minX:-18,minY:-18,maxX:18,maxY:18},f=nodeFixture('circle',body,'账户\nSYN-C003'),calls=mutations(f);
  const first=geometry.placeNode(f.item,{zoom:.4});calls.reset();
  assert.deepEqual(geometry.placeNode(f.item,{zoom:.4}),first);
  assert.equal(calls.counts.writes,0);assert.equal(calls.counts.bboxes,2,'one current body and one current text envelope prove anchoring');
  for(const change of [()=>{body.maxY=39},()=>f.text.attr({text:'更长名称\nSYN-C003\n新增标签',fontSize:24}),()=>f.text.attr({x:300,y:-100}),
    ()=>f.text.setMatrix([0,1,0,-1,0,0,0,0,1])]){
    change();calls.reset();const result=geometry.placeNode(f.item,{zoom:.4});assert.ok(calls.counts.writes>0);
    near(result.bbox.minY-body.maxY,6/.4);near((result.bbox.minX+result.bbox.maxX)/2,0);
    assert.deepEqual(f.text.attr('matrix'),[1,0,0,0,1,0,0,0,1]);
    calls.reset();geometry.placeNode(f.item,{zoom:.4});assert.equal(calls.counts.writes,0);
  }
});

for(const mode of ['inline','above','hidden'])test(`unchanged ${mode} edges skip all shape mutations and local bbox recomputation`,()=>{
  const f=fixture(),calls=mutations(f),options={style:f.style,outline:f.outline,above:mode==='above',visible:mode!=='hidden'};
  const first=geometry.placeEdge(f.item,options);calls.reset();
  const second=geometry.placeEdge(f.item,{...options,style:plain(f.style)});
  assert.deepEqual(second,first);assert.equal(calls.counts.writes,0);assert.equal(calls.counts.bboxes,0);
  if(second.bbox)second.bbox.minX=-99999;
  assert.deepEqual(geometry.placeEdge(f.item,options),first,'cached geometry never aliases a returned receipt');
  assert.equal(calls.counts.writes,0);assert.equal(calls.counts.bboxes,0);
});

const edgeMutations={
  text:f=>f.text.attr({text:'USD 999999999999.99\n追加标签'}),
  font:f=>f.text.attr({fontSize:22,lineHeight:30,fontFamily:'monospace',fontWeight:700}),
  anchor:(f,o)=>{o.anchor={x:67,y:89}},
  gap:(f,o)=>{o.gap=11},
  above:(f,o)=>{o.above=true},
  style:(f,o)=>{o.style={...f.style,lineWidth:3,stroke:'#abcdef',lineDash:[]}},
  path:f=>{f.key.attr('path')[1][1]+=31},
  position:f=>f.text.attr({x:300,y:-100}),
  matrix:f=>f.text.setMatrix([0,1,0,-1,0,0,5,6,1]),
  visibleStyle:f=>visible(f).attr({stroke:'#ffffff',lineWidth:99}),
  visibleClip:f=>{visible(f).getClip().attrs.path[5][1]-=99},
  outlineClip:f=>{f.outline.getClip().attrs.path[5][2]-=99},
  replacedClip:f=>visible(f).setClip({type:'path',attrs:{path:[['M',0,0],['L',1,1]]}}),
  hiddenCopy:f=>visible(f).hide(),
  hitCapture:f=>f.key.set('capture',false),
  removedCopy:f=>visible(f).remove(),
  textReplacement:f=>{const attrs=plain(f.text.attr());f.text.remove();f.text=f.group.addShape('text',{name:'text-shape',attrs})},
  keyReplacement:f=>{const attrs=plain(f.key.attr());f.key.remove();f.key=f.group.addShape('path',{name:'key-shape',attrs});f.key.getPoint=()=>({x:172.5,y:72.5});f.item.getKeyShape=()=>f.key},
  outlineReplacement:(f,o)=>{o.outline=f.group.addShape('path',{name:'funds-edge-selection-outline',attrs:{path:plain(f.path)}})},
  bboxInvalidation:f=>f.text.set('bbox',null),
};
for(const [name,change]of Object.entries(edgeMutations))test(`edge idempotence invalidates on ${name} and repairs the current drawing`,()=>{
  const f=fixture(),calls=mutations(f),options={style:f.style,outline:f.outline};
  geometry.placeEdge(f.item,options);change(f,options);calls.reset();
  const result=geometry.placeEdge(f.item,options);assert.ok(calls.counts.writes>0,`${name} cannot reuse an outdated output`);
  assert.equal(result.mode,options.above?'above':'inline');
  const anchor=options.anchor||{x:172.5,y:72.5};near((result.bbox.minX+result.bbox.maxX)/2,anchor.x);
  if(options.above){near(result.bbox.maxY,anchor.y-options.style.lineWidth/2-(options.gap??4));assert.equal(visible(f),undefined);}
  else{
    near((result.bbox.minY+result.bbox.maxY)/2,anchor.y);
    assert.deepEqual(visible(f).attr('path'),f.key.attr('path'));assert.deepEqual(visible(f).getClip(),options.outline.getClip());
    assert.equal(visible(f).isVisible(),true);assert.equal(visible(f).capture,false);assert.equal(f.key.capture,true);
    assert.equal(visible(f).attr('stroke'),options.style.stroke);assert.equal(visible(f).attr('lineWidth'),options.style.lineWidth);
    assert.deepEqual(visible(f).getClip().attrs.path[5],['M',result.bbox.minX-(options.gap??4),result.bbox.minY-(options.gap??4)]);
  }
  calls.reset();geometry.placeEdge(f.item,options);assert.equal(calls.counts.writes,0);assert.equal(calls.counts.bboxes,0);
});

test('edge cache observes hidden-label transitions, in-place G6 writes and font-load invalidation across a UMD host boundary',()=>{
  const events=new Map(),sandbox={window:{},document:{fonts:{status:'loaded',addEventListener:(name,callback)=>events.set(name,callback)}}};
  vm.runInNewContext(readFileSync(new URL('../static/graph-label-geometry.js',import.meta.url),'utf8'),sandbox);
  const api=sandbox.window.FundsGraphLabelGeometry,f=fixture(),calls=mutations(f),options={style:f.style,outline:f.outline};
  api.placeEdge(f.item,options);calls.reset();api.placeEdge(f.item,options);assert.equal(calls.counts.writes,0);assert.equal(calls.counts.bboxes,0);
  f.text.attr('fontSize',27);calls.reset();const changed=api.placeEdge(f.item,options);assert.ok(calls.counts.writes>0);
  assert.ok(changed.bbox.width>150,'the foreign-realm attribute object is copied rather than retained as mutable state');
  calls.reset();events.get('loadingdone')();api.placeEdge(f.item,options);assert.ok(calls.counts.bboxes>0,'font metrics can change without a font attribute edit');
  f.text.hide();calls.reset();assert.equal(api.placeEdge(f.item,options).mode,'none');assert.equal(visible(f),undefined);
  f.text.show();calls.reset();assert.equal(api.placeEdge(f.item,options).mode,'inline');assert.ok(visible(f).getClip());
  calls.reset();api.placeEdge(f.item,options);assert.equal(calls.counts.writes,0);assert.equal(calls.counts.bboxes,0);
});
