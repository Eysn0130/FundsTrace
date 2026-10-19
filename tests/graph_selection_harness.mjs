import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/graph-selection.js', import.meta.url), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const sorted = values => [...values].sort();

class Surface {
  constructor(tag = 'div', document = null) {
    this.tagName = tag.toUpperCase(); this.ownerDocument = document; this.children = []; this.parentNode = null;
    this.events = new Map(); this.attributes = new Map(); this.style = {}; this.className = ''; this.hidden = false;
    this.clientWidth = 800; this.clientHeight = 600;
    this.classList = {
      contains: name => this.className.split(/\s+/).includes(name),
      add: name => { if (!this.classList.contains(name)) this.className = `${this.className} ${name}`.trim(); },
      remove: name => { this.className = this.className.split(/\s+/).filter(value => value !== name).join(' '); },
    };
  }
  get firstElementChild() { return this.children[0]; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  removeAttribute(name) { this.attributes.delete(name); }
  appendChild(child) { child.remove(); child.parentNode = this; this.children.push(child); return child; }
  remove() { if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); this.parentNode = null; }
  contains(target) { return target === this || this.children.some(child => child.contains(target)); }
  closest(selector) {
    const editable = ['INPUT', 'TEXTAREA', 'SELECT'].includes(this.tagName) || (this.getAttribute('contenteditable') !== null && this.getAttribute('contenteditable') !== 'false') || this.getAttribute('role') === 'textbox';
    if (selector.includes('input') && editable) return this;
    if ((selector.includes('.modal') && this.classList.contains('modal')) || (selector.includes('[role="dialog"]') && this.getAttribute('role') === 'dialog')) return this;
    return this.parentNode?.closest(selector) || null;
  }
  addEventListener(name, fn) { if (!this.events.has(name)) this.events.set(name, new Set()); this.events.get(name).add(fn); }
  removeEventListener(name, fn) { this.events.get(name)?.delete(fn); if (!this.events.get(name)?.size) this.events.delete(name); }
  emit(name, event = {}) { for (const fn of [...(this.events.get(name) || [])]) fn(event); }
  focus() { this.ownerDocument.activeElement = this; }
  getBoundingClientRect() { return { left: 20, top: 30, width: this.clientWidth, height: this.clientHeight }; }
}

function fixture(count = 12, options = {}) {
  const document = new Surface('document'); document.ownerDocument = document; document.hidden = false; document.modals = [];
  document.createElement = tag => new Surface(tag, document);
  document.querySelectorAll = () => document.modals;
  const container = document.createElement('section'); document.appendChild(container);
  const outside = document.createElement('div'); document.appendChild(outside);
  const window = new Surface('window', document), media = new Surface('media'); media.matches = false;
  window.document = document; window.matchMedia = () => media;
  window.getComputedStyle = element => ({ display: element.style.display || 'block', visibility: element.style.visibility || 'visible' });
  let frameRequests = 0, timerRequests = 0;
  window.requestAnimationFrame = () => { frameRequests++; throw Error('selection must not schedule animation frames'); };
  const sandbox = { window, setTimeout: () => { timerRequests++; throw Error('selection must not schedule perpetual work'); } };
  vm.runInNewContext(source, sandbox, { filename: 'graph-selection.js' });
  const allModels = Array.from({ length: count }, (_, i) => ({ id: `SYN-${i}`, x: 60 + (i % 10) * 65, y: 60 + Math.floor(i / 10) * 65,
    size: 32, style: { fill: '#4caf7d', stroke: '#ffd700', shadowColor: '#ffd700', shadowBlur: 16 },
    data: { amount: '9999999999999999.99', account: `000${i}` } }));
  const graph = new Surface('graph'), calls = [], selected = new Set();
  let nodeScans = 0, geometryRefreshes = 0, nodeUpdates = 0, edgeRefreshes = 0, paints = 0, zoom = 1, panX = 0, panY = 0, autoPaint = true;
  function makeItem(model) {
    const values = { styles: { selected: { fill: '#fff', stroke: '#123', lineWidth: 2 } } }, attrs = { ...model.style, img: model.img };
    const shapes = [];
    const group = { shapes, addShape(type, config) {
      const shape = { type, ...config, visible: true, destroyed: false,
        attr(patch) { if (typeof patch === 'string') return this.attrs[patch]; Object.assign(this.attrs, patch); },
        show() { this.visible = true; }, hide() { this.visible = false; },
        remove() { this.destroyed = true; const i = shapes.indexOf(this); if (i >= 0) shapes.splice(i, 1); } };
      shapes.push(shape); return shape;
    } };
    const edges = [];
    return { getModel: () => model, get: name => values[name], set: (name, value) => { values[name] = value; }, edges,
      updatePosition: point => { nodeUpdates++; Object.assign(model, point); }, getEdges: () => edges,
      getKeyShape: () => ({ attr: () => attrs }), getContainer: () => group, attrs, getType: () => 'node' };
  }
  graph.nodes = allModels.map(makeItem);
  graph.on = graph.addEventListener.bind(graph); graph.off = graph.removeEventListener.bind(graph);
  graph.getNodes = () => { nodeScans++; return graph.nodes; };
  graph.get = key => key === 'autoPaint' ? autoPaint : undefined;
  graph.setAutoPaint = value => { calls.push(['autoPaint', value]); autoPaint = value; };
  graph.getZoom = () => zoom;
  graph.getCanvasByPoint = (x, y) => ({ x: x * zoom + panX, y: y * zoom + panY });
  graph.getPointByClient = (x, y) => ({ x: (x - 20 - panX) / zoom, y: (y - 30 - panY) / zoom });
  graph.setItemState = (item, name, value) => {
    calls.push(['state', item.getModel().id, name, value]);
    if (value) { selected.add(item.getModel().id); Object.assign(item.attrs, item.get('styles')[name]); }
    else { selected.delete(item.getModel().id); Object.assign(item.attrs, item.getModel().style); }
  };
  graph.paint = () => { paints++; };
  graph.refreshPositions = () => { geometryRefreshes++; calls.push(['refreshPositions']); graph.emit('aftergraphrefreshposition', {}); };
  graph.updateItem = () => { throw Error('group movement must not refresh high-degree edges per node'); };
  const context = { case: 'SYN-A', canvas: 'SYN-C', revision: 1 }, primary = [], changes = [], starts = [], ends = [], before = [];
  let valid = true;
  const api = window.FundsGraphSelection.create({ graph, container, context, isCurrent: captured => valid && captured === context,
    onPrimary: item => primary.push(item.getModel().id), onChange: change => changes.push({ ids: sorted(change.ids), count: change.count }),
    onMoveStart: change => starts.push({ ...plain(change), ids: sorted(change.ids) }),
    onMoveEnd: change => ends.push({ ...plain(change), ids: sorted(change.ids) }),
    onBeforeMove: change => before.push({ ...plain(change), ids: sorted(change.ids) }), ...options });
  const key = values => { const event = { key: 'a', target: container, preventDefault() { this.prevented = true; }, ...values }; container.emit('keydown', event); return event; };
  const click = (index, values = {}) => graph.emit('node:click', { item: graph.nodes[index], originalEvent: { target: container, ...values } });
  const down = (index, values = {}) => graph.emit('node:mousedown', { item: graph.nodes[index], clientX: 80, clientY: 90,
    originalEvent: { target: container, button: 0, clientX: 80, clientY: 90, ...values } });
  const pointer = (name, values = {}) => window.emit(name, { clientX: 90, clientY: 100, buttons: 1, preventDefault() {}, ...values });
  return { api, graph, container, document, window, outside, media, calls, selected, allModels, primary, changes, starts, ends, before,
    click, down, pointer, key, makeItem, makeEdge: () => ({ refresh: () => { edgeRefreshes++; } }),
    stale: () => { valid = false; }, camera: (scale, x = 0, y = 0) => { zoom = scale; panX = x; panY = y; },
    metrics: () => ({ nodeScans, geometryRefreshes, nodeUpdates, edgeRefreshes, paints, autoPaint, frameRequests, timerRequests }) };
}

test('ordinary click replaces selection and opens details; Ctrl and Command toggle without opening', () => {
  const f = fixture();
  f.click(0); f.click(1, { ctrlKey: true }); f.click(2, { metaKey: true }); f.click(1, { ctrlKey: true });
  assert.deepEqual(sorted(f.api.ids), ['SYN-0', 'SYN-2']); assert.deepEqual(f.primary, ['SYN-0']);
  f.click(3); assert.deepEqual(sorted(f.api.ids), ['SYN-3']); assert.deepEqual(f.primary, ['SYN-0', 'SYN-3']);
  assert.deepEqual(f.changes.map(change => change.count), [1, 2, 3, 2, 1]);
  assert.equal(f.document.activeElement, f.container); assert.equal(f.container.getAttribute('tabindex'), '0');
  f.graph.emit('canvas:click', {}); assert.equal(f.api.ids.size, 0); assert.equal(f.api.status().haloCount, 0);
});

test('canvas and Escape clear empty node selection for edge details, without repaint, scan or notification recursion', () => {
  let api, notifications = 0;
  const f = fixture(2, { onChange: change => {
    assert.equal(change.count, 0); assert.equal(change.ids.size, 0); notifications++;
    api.clear(); // A host closing its drawer can synchronously issue clear again.
  } });
  api = f.api;
  const before = f.metrics();
  f.graph.emit('canvas:click', {}); assert.equal(notifications, 1);
  f.key({ key: 'Escape' }); assert.equal(notifications, 2);
  api.clear(); assert.equal(notifications, 3);
  assert.deepEqual(f.metrics(), before);
  const ordinary = fixture(1); ordinary.api.select('SYN-0'); ordinary.api.select('SYN-0');
  assert.equal(ordinary.changes.length, 1, 'selecting an unchanged node does not notify');
});

test('selection preserves role fill and shadow, image and source facts', () => {
  const f = fixture(1), model = f.allModels[0]; model.img = '/synthetic-avatar.png';
  model.style.fill = '#e25656'; f.graph.nodes = [f.makeItem(model)]; f.api.rebind();
  const before = JSON.stringify(model); f.click(0);
  assert.equal(f.graph.nodes[0].attrs.fill, '#e25656'); assert.equal(f.graph.nodes[0].attrs.shadowBlur, 16);
  assert.equal(f.graph.nodes[0].attrs.img, '/synthetic-avatar.png'); assert.equal(f.graph.nodes[0].attrs.stroke, '#ffd700');
  const [outline] = f.graph.nodes[0].getContainer().shapes;
  assert.equal(outline.attrs.stroke, '#66d9ef'); assert.equal(outline.capture, false);
  assert.equal(JSON.stringify(model), before); f.api.clear();
  assert.equal(f.graph.nodes[0].attrs.stroke, '#ffd700'); assert.equal(f.graph.nodes[0].get('styles').selected.fill, '#fff');
});

test('keyboard select-all includes offscreen graph nodes and excludes edges, foreign focus, editors and modals', () => {
  const f = fixture(); f.allModels[11].x = 20000;
  const foreign = f.key({ ctrlKey: true, target: f.outside }); assert.equal(foreign.prevented, undefined); assert.equal(f.api.ids.size, 0);
  const input = f.document.createElement('input'); f.container.appendChild(input);
  f.key({ ctrlKey: true, target: input }); assert.equal(f.api.ids.size, 0);
  const editable = f.document.createElement('div'); editable.setAttribute('contenteditable', 'true'); f.container.appendChild(editable);
  f.key({ metaKey: true, target: editable }); assert.equal(f.api.ids.size, 0);
  const modal = f.document.createElement('div'); modal.className = 'modal'; f.document.modals = [modal];
  f.key({ ctrlKey: true }); assert.equal(f.api.ids.size, 0); modal.style.display = 'none';
  const all = f.key({ metaKey: true }); assert.equal(all.prevented, true); assert.equal(f.api.ids.size, 12);
  assert.ok(f.api.ids.has('SYN-11')); assert.equal(f.primary.length, 0);
  assert.equal(f.key({ key: 'Escape' }).prevented, true); assert.equal(f.api.ids.size, 0);
});

test('ten selections breathe and eleven selections retain independent static Canvas rings', () => {
  const f = fixture();
  for (let i = 0; i < 10; i++) f.api.select(`SYN-${i}`, { toggle: true });
  assert.equal(f.api.status().haloCount, 10); assert.equal(f.api.status().animatedCount, 10);
  f.api.select('SYN-10', { toggle: true });
  assert.equal(f.api.status().haloCount, 0); assert.equal(f.api.status().animatedCount, 0);
  assert.equal(f.container.children[0].children.length, 0); assert.equal(f.selected.size, 11);
  assert.equal(f.api.status().outlineCount, 11);
  assert.ok(f.graph.nodes.slice(0, 11).every(item => item.getContainer().shapes.length === 1 && item.attrs.stroke === '#ffd700'));
  f.api.select('SYN-10', { toggle: true }); assert.equal(f.api.status().animatedCount, 10);
  f.media.matches = true; f.media.emit('change'); assert.equal(f.api.status().animatedCount, 0); assert.equal(f.api.status().haloCount, 10);
  f.media.matches = false; f.media.emit('change'); assert.equal(f.api.status().animatedCount, 10);
  f.document.hidden = true; f.document.emit('visibilitychange'); assert.equal(f.api.status().animatedCount, 0);
});

test('halo follows node geometry and camera, clips offscreen, and never schedules per-frame scans', () => {
  const f = fixture(1); f.allModels[0].size = [80, 60]; f.click(0);
  let halo = f.container.children[0].children[0]; assert.equal(halo.style.width, '88px'); assert.equal(halo.style.height, '68px'); assert.equal(halo.firstElementChild.style.borderWidth, '1.5px');
  const scans = f.metrics().nodeScans;
  f.camera(2, 100, 10); f.graph.emit('viewportchange');
  halo = f.container.children[0].children[0]; assert.equal(halo.style.left, '220px'); assert.equal(halo.style.top, '130px'); assert.equal(halo.style.width, '176px'); assert.equal(halo.firstElementChild.style.borderWidth, '2px');
  f.allModels[0].x = 10000; f.graph.emit('afterlayout'); assert.equal(f.api.status().haloCount, 0);
  f.allModels[0].x = 100; f.api.refresh(); assert.equal(f.api.status().haloCount, 1);
  assert.equal(f.metrics().nodeScans, scans); assert.equal(f.metrics().frameRequests, 0); assert.equal(f.metrics().timerRequests, 0);
});

test('pending IDs survive an empty graph and explicit rebind reapplies styles to replacement items', () => {
  const f = fixture(0, { ids: ['SYN-0'] }); assert.deepEqual(sorted(f.api.ids), ['SYN-0']);
  const model = { id: 'SYN-0', x: 60, y: 60, size: 64, style: { fill: '#abc' } };
  f.graph.nodes = [f.makeItem(model)]; f.api.rebind(new Set(['SYN-0', 'missing']));
  assert.deepEqual(sorted(f.api.ids), ['SYN-0']); assert.equal(f.graph.nodes[0].attrs.fill, '#abc'); assert.equal(f.api.status().haloCount, 1);
  const replacement = f.makeItem({ ...model, x: 200, style: { ...model.style, stroke: '#e3b45a', lineWidth: 3.5 } }); f.graph.nodes = [replacement]; f.api.rebind();
  assert.equal(replacement.attrs.stroke, '#e3b45a'); assert.equal(replacement.attrs.lineWidth, 3.5);
  assert.equal(replacement.getContainer().shapes.length, 1); assert.equal(f.container.children[0].children[0].style.left, '200px');
});

test('business-role refresh patches deselected models before restoring the same selection without notifying a selection change', () => {
  const f = fixture(2), item = f.graph.nodes[0], model = f.allModels[0];
  f.api.select('SYN-0');
  const notifications = f.changes.length, geometry = f.allModels.map(node => [node.x, node.y]);
  const originalSelectedStyle = { ...item.get('styles').selected };
  let patched = 0;
  f.api.rebind(f.api.ids, () => {
    patched++;
    assert.equal(f.selected.has('SYN-0'), false, 'business updates run outside the active selected-state cache');
    assert.equal(item.getContainer().shapes.length, 0);
    model.style = { fill: '#ee6677', stroke: '#a79be0', lineWidth: 1.5, shadowBlur: 5 };
    Object.assign(item.attrs, model.style);
  });
  assert.equal(patched, 1); assert.deepEqual(sorted(f.api.ids), ['SYN-0']);
  assert.equal(f.changes.length, notifications, 'role changes do not report a different selection');
  assert.equal(item.attrs.stroke, '#a79be0'); assert.equal(item.attrs.fill, '#ee6677');
  assert.equal(item.get('styles').selected.stroke, '#a79be0');
  assert.equal(item.getContainer().shapes.length, 1); assert.deepEqual(f.allModels.map(node => [node.x, node.y]), geometry);
  f.api.clear();
  assert.equal(item.attrs.stroke, '#a79be0', 'clearing selection cannot resurrect the previous main-account border');
  assert.deepEqual(item.get('styles').selected, { fill: '#fff', stroke: '#123', lineWidth: 2 });
  assert.notEqual(originalSelectedStyle.stroke, item.attrs.stroke);
});

test('an expired binding cannot invoke a business-role rebind callback', () => {
  const f = fixture(2); f.api.select('SYN-0'); f.stale();
  let patched = false;
  f.api.rebind(['SYN-0'], () => { patched = true; });
  assert.equal(patched, false);
});

test('four screen pixels start a live group drag, updating only its items and incremental bend deltas', () => {
  const f = fixture(3); f.click(0); f.click(1, { ctrlKey: true }); f.camera(2);
  const before = f.allModels.map(model => ({ x: model.x, y: model.y })), refreshes = f.metrics().geometryRefreshes;
  f.down(0); f.pointer('mousemove', { clientX: 83, clientY: 90 }); assert.equal(f.starts.length, 0);
  f.pointer('mousemove', { clientX: 84, clientY: 90 }); assert.equal(f.starts.length, 1);
  assert.equal(f.allModels[0].x, before[0].x + 2); assert.equal(f.allModels[1].x, before[1].x + 2); assert.equal(f.allModels[2].x, before[2].x);
  f.pointer('mousemove', { clientX: 100, clientY: 100 });
  assert.equal(f.allModels[0].x, before[0].x + 10); assert.equal(f.allModels[0].y, before[0].y + 5);
  assert.deepEqual(f.before.map(change => [change.dx, change.dy]), [[2, 0], [8, 5]]);
  assert.equal(f.metrics().geometryRefreshes - refreshes, 0); assert.equal(f.metrics().nodeUpdates, 4); assert.equal(f.metrics().autoPaint, true);
  f.pointer('mouseup', { clientX: 100, clientY: 100 });
  assert.equal(f.ends.length, 1); assert.equal(f.ends[0].committed, true); assert.equal(f.ends[0].preview, false);
  assert.equal(f.window.events.has('mousemove'), false); f.graph.emit('canvas:click', {}); assert.equal(f.api.ids.size, 2);
});

for (const zoom of [0.4, 1, 2]) for (const count of [1, 2, 50]) {
  test(`a released-button move finishes ${count} dragged nodes at zoom ${zoom} without rolling them back`, () => {
    const f = fixture(Math.max(3, count)); f.camera(zoom, 25, -15);
    if (count === 1) f.click(0);
    else if (count === 50) f.key({ ctrlKey: true });
    else { f.click(0); f.click(1, { ctrlKey: true }); }
    const original = f.allModels.map(model => [model.x, model.y]);
    f.down(0);
    f.pointer('mousemove', { clientX: 84, clientY: 90 });
    // A mouseup outside the browser can be missed. The next move only reports
    // that the button is no longer held; its location is not a new drag point.
    f.pointer('mousemove', { clientX: 140, clientY: 150, buttons: 0 });
    const expected = original.map((point, i) => [point[0] + (i < count ? 4 / zoom : 0), point[1]]);
    assert.deepEqual(f.allModels.map(model => [model.x, model.y]), expected);
    assert.equal(f.ends.length, 1); assert.equal(f.ends[0].committed, true);
    assert.equal(f.ends[0].cancelled, false); assert.equal(f.api.status().dragging, false);
    const completed = JSON.stringify(f.allModels), commits = f.api.status().commitCount;
    f.pointer('mouseup', { clientX: 140, clientY: 150, buttons: 0 });
    f.pointer('mousemove', { clientX: 150, clientY: 160, buttons: 0 });
    assert.equal(JSON.stringify(f.allModels), completed);
    assert.equal(f.api.status().commitCount, commits); assert.equal(f.ends.length, 1);
    assert.equal(f.window.events.has('mousemove'), false);
  });
}

test('releasing a pending sub-threshold gesture creates no movement or history', () => {
  const f = fixture(3); f.click(0); f.down(0);
  const original = JSON.stringify(f.allModels);
  f.pointer('mousemove', { clientX: 83, clientY: 90 });
  f.pointer('mousemove', { clientX: 140, clientY: 150, buttons: 0 });
  f.pointer('mouseup', { clientX: 140, clientY: 150, buttons: 0 });
  assert.equal(JSON.stringify(f.allModels), original);
  assert.equal(f.starts.length, 0); assert.equal(f.ends.length, 0);
  assert.equal(f.api.status().commitCount, 0);
});

const leaveSurface = (f, terminal) => {
  if (terminal === 'hidden') { f.document.hidden = true; f.document.emit('visibilitychange'); }
  else f.window.emit(terminal === 'exit' ? 'mouseout' : 'blur', terminal === 'exit' ? { relatedTarget: null } : {});
};

for (const terminal of ['blur', 'exit', 'hidden']) for (const zoom of [0.4, 1, 2]) for (const count of [1, 2, 50]) {
  test(`${terminal} completes ${count} dragged nodes at zoom ${zoom} exactly once`, () => {
    const f = fixture(Math.max(3, count)); f.camera(zoom, 25, -15);
    if (count === 1) f.click(0);
    else if (count === 50) f.key({ ctrlKey: true });
    else { f.click(0); f.click(1, { ctrlKey: true }); }
    const original = f.allModels.map(model => [model.x, model.y]);
    f.down(0);
    const lateMove = [...f.window.events.get('mousemove')][0], lateUp = [...f.window.events.get('mouseup')][0];
    f.pointer('mousemove', { clientX: 84, clientY: 90 });
    leaveSurface(f, terminal);
    const expected = original.map((point, i) => [point[0] + (i < count ? 4 / zoom : 0), point[1]]);
    assert.deepEqual(f.allModels.map(model => [model.x, model.y]), expected);
    assert.equal(f.ends.length, 1); assert.equal(f.ends[0].committed, true);
    assert.equal(f.ends[0].cancelled, false); assert.equal(f.api.status().dragging, false);
    const completed = JSON.stringify(f.allModels), commits = f.api.status().commitCount;
    lateMove({ clientX: 140, clientY: 150, buttons: 1 });
    lateUp({ clientX: 140, clientY: 150, buttons: 0 });
    leaveSurface(f, terminal);
    assert.equal(JSON.stringify(f.allModels), completed);
    assert.equal(f.api.status().commitCount, commits); assert.equal(f.ends.length, 1);
    assert.equal(f.window.events.has('mousemove'), false); assert.equal(f.window.events.has('mouseup'), false);
  });
}

for (const terminal of ['blur', 'exit', 'hidden']) {
  test(`${terminal} before the drag threshold does not move or create history`, () => {
    const f = fixture(3); f.click(0); const original = JSON.stringify(f.allModels);
    f.down(0); f.pointer('mousemove', { clientX: 83, clientY: 90 });
    leaveSurface(f, terminal);
    assert.equal(JSON.stringify(f.allModels), original);
    assert.equal(f.starts.length, 0); assert.equal(f.ends.length, 0); assert.equal(f.api.status().commitCount, 0);
    assert.equal(f.window.events.has('mousemove'), false);
  });

  test(`${terminal} never commits a revoked binding or accepts its late pointer events`, () => {
    const f = fixture(50); f.key({ ctrlKey: true }); const original = JSON.stringify(f.allModels);
    f.down(0); f.pointer('mousemove');
    const lateMove = [...f.window.events.get('mousemove')][0], lateUp = [...f.window.events.get('mouseup')][0];
    f.stale(); leaveSurface(f, terminal);
    lateMove({ clientX: 140, clientY: 150, buttons: 1 }); lateUp({ clientX: 140, clientY: 150 });
    assert.equal(JSON.stringify(f.allModels), original);
    assert.equal(f.ends.length, 0); assert.equal(f.api.status().commitCount, 0);
    assert.equal(f.container.children[0].children.length, 0);
  });
}

test('the click trailing the same drag gesture does not select, open details or add movement history', () => {
  const f = fixture(3); f.click(0); f.down(0); f.pointer('mousemove'); f.pointer('mouseup');
  const notifications = f.changes.length;
  f.click(1); f.graph.emit('canvas:click', {});
  assert.deepEqual(sorted(f.api.ids), ['SYN-0']); assert.deepEqual(f.primary, ['SYN-0']);
  assert.equal(f.changes.length, notifications); assert.equal(f.starts.length, 1); assert.equal(f.ends.length, 1);
  assert.equal(f.api.status().commitCount, 1);
});

for (const clickTarget of ['node', 'canvas']) for (const zoom of [0.4, 1, 2]) for (const count of [1, 2, 50]) {
  test(`G6 ${clickTarget}:click before window mouseup cannot cancel ${count} dragged nodes at zoom ${zoom}`, () => {
    const f = fixture(Math.max(3, count)); f.camera(zoom, 25, -15);
    if (count === 1) f.click(0);
    else if (count === 50) f.key({ ctrlKey: true });
    else { f.click(0); f.click(1, { ctrlKey: true }); }
    const original = f.allModels.map(model => [model.x, model.y]);
    const selected = sorted(f.api.ids), primary = [...f.primary], notifications = f.changes.length;
    f.down(0);
    f.pointer('mousemove', { clientX: 84, clientY: 90 });
    f.pointer('mousemove', { clientX: 86, clientY: 92 });
    // G6's click distance threshold exceeds the controller's four-screen-pixel
    // threshold. Its canvas mouseup handler emits this before window mouseup.
    if (clickTarget === 'node') f.click(0);
    else f.graph.emit('canvas:click', {});
    assert.equal(f.api.status().dragging, true); assert.equal(f.ends.length, 0);
    assert.deepEqual(sorted(f.api.ids), selected); assert.deepEqual(f.primary, primary);
    assert.equal(f.changes.length, notifications);
    f.pointer('mouseup', { clientX: 86, clientY: 92, buttons: 0 });
    const expected = original.map((point, i) => [point[0] + (i < count ? 6 / zoom : 0), point[1] + (i < count ? 2 / zoom : 0)]);
    assert.deepEqual(f.allModels.map(model => [model.x, model.y]), expected);
    assert.equal(f.ends.length, 1); assert.equal(f.ends[0].committed, true); assert.equal(f.ends[0].cancelled, false);
    assert.deepEqual(sorted(f.api.ids), selected); assert.deepEqual(f.primary, primary);
    const completed = JSON.stringify(f.allModels), commits = f.api.status().commitCount;
    f.pointer('mouseup', { clientX: 100, clientY: 110, buttons: 0 });
    f.click(0); f.graph.emit('canvas:click', {});
    assert.equal(JSON.stringify(f.allModels), completed);
    assert.equal(f.api.status().commitCount, commits); assert.equal(f.ends.length, 1);
    assert.deepEqual(sorted(f.api.ids), selected); assert.deepEqual(f.primary, primary);
  });
}

for (const action of [
  { name: 'Ctrl node toggle', modifiers: { ctrlKey: true }, selected: ['SYN-0', 'SYN-1'], opened: ['SYN-0'] },
  { name: 'Command node toggle', modifiers: { metaKey: true }, selected: ['SYN-0', 'SYN-1'], opened: ['SYN-0'] },
  { name: 'ordinary node click', modifiers: {}, selected: ['SYN-1'], opened: ['SYN-0', 'SYN-1'] },
  { name: 'blank canvas click', modifiers: {}, selected: [], opened: ['SYN-0'], canvas: true },
]) test(`a fresh native mousedown immediately after dragging admits ${action.name} without extra history`, () => {
  const f = fixture(3); f.click(0); f.down(0); f.pointer('mousemove'); f.pointer('mouseup');
  f.click(0); // The old gesture's generated click remains suppressed.
  f.container.emit('mousedown', { target: f.container, button: 0, clientX: 80, clientY: 90, ...action.modifiers });
  if (!action.canvas) f.down(1, action.modifiers);
  f.pointer('mouseup', { clientX: 80, clientY: 90 });
  if (action.canvas) f.graph.emit('canvas:click', { originalEvent: { target: f.container } });
  else f.click(1, action.modifiers);
  assert.deepEqual(sorted(f.api.ids), action.selected);
  assert.deepEqual(f.primary, action.opened);
  assert.equal(f.starts.length, 1); assert.equal(f.ends.length, 1); assert.equal(f.api.status().commitCount, 1);
});

test('dragging an unselected node replaces selection at threshold and keeps window drag listeners active', () => {
  const f = fixture(3); f.click(0); f.click(1, { ctrlKey: true }); f.down(2);
  assert.deepEqual(sorted(f.api.ids), ['SYN-0', 'SYN-1']);
  f.pointer('mousemove'); assert.deepEqual(sorted(f.api.ids), ['SYN-2']); assert.equal(f.window.events.has('mousemove'), true);
  f.pointer('mousemove', { clientX: 100, clientY: 110 }); f.pointer('mouseup', { clientX: 100, clientY: 110 });
  assert.equal(f.allModels[2].x, 210); assert.equal(f.allModels[2].y, 80); assert.equal(f.ends.length, 1);
  assert.equal(f.primary.length, 1);
});

test('fifty selected nodes use a single preview and submit geometry exactly once on mouseup', () => {
  const f = fixture(50); f.key({ ctrlKey: true }); const initial = f.allModels.map(model => [model.x, model.y]);
  f.graph.setAutoPaint(false); const refreshes = f.metrics().geometryRefreshes, paints = f.metrics().paints;
  f.down(0); f.pointer('mousemove', { clientX: 100, clientY: 110 }); f.pointer('mousemove', { clientX: 120, clientY: 130 });
  assert.equal(f.api.status().preview, true); assert.equal(f.api.status().haloCount, 0);
  assert.equal(f.container.children[0].children.length, 1); assert.equal(f.container.children[0].children[0].className, 'graph-selection-preview');
  assert.deepEqual(f.allModels.map(model => [model.x, model.y]), initial); assert.equal(f.metrics().geometryRefreshes, refreshes); assert.equal(f.before.length, 0);
  f.pointer('mouseup', { clientX: 120, clientY: 130 });
  assert.equal(f.metrics().geometryRefreshes - refreshes, 1); assert.equal(f.metrics().paints - paints, 1);
  assert.equal(f.metrics().autoPaint, false); assert.equal(f.before.length, 1); assert.deepEqual([f.before[0].dx, f.before[0].dy], [40, 40]);
  assert.equal(f.allModels[49].x, initial[49][0] + 40); assert.equal(f.container.children[0].children.length, 0);
  assert.equal(f.ends[0].committed, true); assert.equal(f.ends[0].preview, true);
});

test('forty-nine selected nodes keep live movement while Ctrl/Command mousedown never starts movement', () => {
  const f = fixture(49); f.key({ ctrlKey: true }); f.down(0, { ctrlKey: true }); f.pointer('mousemove'); assert.equal(f.starts.length, 0);
  f.down(0, { metaKey: true }); f.pointer('mousemove'); assert.equal(f.starts.length, 0);
  f.down(0); f.pointer('mousemove'); assert.equal(f.api.status().preview, false); assert.equal(f.metrics().geometryRefreshes, 0); assert.equal(f.metrics().nodeUpdates, 49);
});

test('one selected leaf in 5000 nodes updates one item and shared group edges refresh once per move', () => {
  const f = fixture(5000), shared = f.makeEdge(), leaf = f.makeEdge(), untouched = f.makeEdge();
  f.graph.nodes[0].edges.push(shared, leaf); f.graph.nodes[1].edges.push(shared); f.graph.nodes[4999].edges.push(untouched);
  f.click(0); f.click(1, { ctrlKey: true });
  const scans = f.metrics().nodeScans; f.down(0); f.pointer('mousemove');
  assert.equal(f.metrics().nodeScans, scans); assert.equal(f.metrics().nodeUpdates, 2); assert.equal(f.metrics().edgeRefreshes, 2);
  assert.equal(f.metrics().geometryRefreshes, 0); assert.equal(f.api.status().commitCount, 1); assert.ok(f.api.status().lastCommitMs >= 0);
  f.window.emit('blur'); f.api.select('SYN-4999'); f.down(4999); f.pointer('mousemove');
  assert.equal(f.metrics().nodeUpdates, 3); assert.equal(f.metrics().edgeRefreshes, 3); assert.equal(f.metrics().nodeScans, scans);
});

test('Escape explicitly cancels preview and restores an active small-group move', () => {
  const small = fixture(2); small.click(0); small.down(0); small.pointer('mousemove'); assert.equal(small.allModels[0].x, 70);
  small.key({ key: 'Escape' }); assert.equal(small.allModels[0].x, 60); assert.equal(small.ends[0].cancelled, true); assert.equal(small.ends[0].committed, false);
  assert.deepEqual(small.before.map(change => [change.dx, change.dy]), [[10, 10], [-10, -10]]);
  const large = fixture(50); large.key({ ctrlKey: true }); large.down(0); large.pointer('mousemove'); large.key({ key: 'Escape' });
  assert.equal(large.metrics().geometryRefreshes, 0); assert.equal(large.container.children[0].children.length, 0); assert.equal(large.window.events.has('mousemove'), false);
  assert.equal(large.ends[0].cancelled, true);
});

test('invalid context rejects old callbacks, drops preview, and never commits to a reused-ID case', () => {
  const f = fixture(50); f.key({ ctrlKey: true }); f.down(0); f.pointer('mousemove'); const moves = [...f.window.events.get('mousemove')];
  f.stale(); f.pointer('mouseup'); assert.equal(f.metrics().geometryRefreshes, 0); assert.equal(f.ends.length, 0);
  assert.equal(f.container.children[0].children.length, 0); assert.equal(f.window.events.has('mousemove'), false);
  moves[0]({ clientX: 300, clientY: 300, buttons: 1 }); f.click(0); f.key({ key: 'Escape' }); f.api.rebind();
  assert.equal(f.metrics().geometryRefreshes, 0); assert.equal(f.primary.length, 0); assert.equal(f.changes.length, 1);
});

test('destroy removes every graph, keyboard, media and window listener and rejects dispatched callbacks', () => {
  const f = fixture(50); f.key({ ctrlKey: true }); f.down(0); f.pointer('mousemove');
  const callbacks = [...f.graph.events.values()].flatMap(values => [...values]); f.api.destroy(); f.api.destroy();
  assert.equal(f.graph.events.size, 0); assert.equal(f.window.events.size, 0); assert.equal(f.container.events.size, 0);
  assert.equal(f.document.events.size, 0); assert.equal(f.media.events.size, 0); assert.equal(f.container.children.length, 0);
  assert.equal(f.container.getAttribute('tabindex'), null); assert.equal(f.api.status().destroyed, true);
  const refreshes = f.metrics().geometryRefreshes, primary = f.primary.length;
  for (const callback of callbacks) callback({ item: f.graph.nodes[0] });
  assert.equal(f.metrics().geometryRefreshes, refreshes); assert.equal(f.primary.length, primary);
});

test('ordinary role nodes do not inherit the engine selected blur', () => {
  const f = fixture(1), item = f.graph.nodes[0];
  for (const key of ['shadowBlur', 'shadowColor']) { delete item.attrs[key]; delete f.allModels[0].style[key]; }
  item.get('styles').selected.shadowBlur = 10;
  f.click(0);
  assert.equal(item.attrs.fill, '#4caf7d');
  assert.equal(item.attrs.shadowBlur, 0);
  assert.equal(item.attrs.shadowColor, 'transparent');
});

test('photos keep a static non-interactive selection outline above ten and release it', () => {
  const f = fixture(12), photo = f.graph.nodes[0];
  f.allModels[0].type = 'image'; f.allModels[0].img = 'SYNTHETIC-PHOTO';
  f.key({ metaKey: true });
  assert.equal(f.api.status().haloCount, 0);
  const [outline] = photo.getContainer().shapes;
  assert.equal(outline.type, 'circle'); assert.equal(outline.capture, false);
  assert.equal(outline.attrs.fill, null); assert.equal(outline.attrs.shadowBlur, 0);
  assert.equal(f.api.status().outlineCount, 12); assert.equal(photo.attrs.stroke, '#ffd700');
  assert.equal(f.allModels[0].img, 'SYNTHETIC-PHOTO');
  f.api.rebind(); assert.equal(photo.getContainer().shapes.length, 1);
  f.click(0, { metaKey: true }); assert.equal(photo.getContainer().shapes.length, 0);
  f.key({ metaKey: true }); f.api.destroy(); assert.equal(photo.getContainer().shapes.length, 0);
});

test('selection keeps custom role borders and refreshes the independent ring after size changes', () => {
  const f = fixture(1), model = f.allModels[0];
  Object.assign(model.style, { stroke: '#e3b45a', lineWidth: 3.5, lineDash: [6, 4], strokeOpacity: 0.8 });
  f.graph.nodes = [f.makeItem(model)]; f.api.rebind();
  const before = JSON.stringify(model); f.click(0);
  const item = f.graph.nodes[0], [outline] = item.getContainer().shapes;
  assert.equal(item.attrs.stroke, '#e3b45a'); assert.equal(item.attrs.lineWidth, 3.5);
  assert.deepEqual(plain(item.attrs.lineDash), [6, 4]); assert.equal(item.attrs.strokeOpacity, 0.8);
  assert.equal(outline.attrs.r, 19.75); assert.equal(outline.attrs.lineWidth, 2);
  assert.equal(JSON.stringify(model), before);
  model.size = 64; f.api.refresh();
  assert.equal(outline.attrs.r, 35.75); assert.equal(item.getContainer().shapes.length, 1);
  model.size = [80, 60]; f.graph.emit('afterlayout'); assert.equal(outline.attrs.r, 43.75);
  f.api.clear(); assert.equal(item.getContainer().shapes.length, 0);
  assert.equal(item.attrs.stroke, '#e3b45a'); assert.equal(item.attrs.shadowBlur, 16);
});

test('export visual suspension preserves selection and notifications, then restores current geometry', () => {
  const f = fixture(3); f.click(0); f.click(1, { ctrlKey: true });
  const ids = sorted(f.api.ids), notifications = f.changes.length, stateCalls = f.calls.filter(call => call[0] === 'state').length;
  const facts = JSON.stringify(f.allModels), layer = f.container.children[0];
  assert.equal(f.api.setVisualsVisible(false), true);
  assert.equal(f.api.setVisualsVisible(false), true);
  assert.equal(f.api.status().visualsVisible, false); assert.equal(f.api.status().haloCount, 0);
  assert.equal(layer.hidden, true); assert.equal(layer.style.visibility, 'hidden');
  assert.ok(f.graph.nodes.slice(0, 2).every(item => item.getContainer().shapes[0].visible === false));
  f.graph.emit('viewportchange'); f.media.emit('change');
  assert.deepEqual(sorted(f.api.ids), ids); assert.equal(f.changes.length, notifications);
  assert.equal(f.calls.filter(call => call[0] === 'state').length, stateCalls);
  assert.equal(JSON.stringify(f.allModels), facts); assert.equal(f.api.status().commitCount, 0);
  f.allModels[0].size = 80; f.api.refresh();
  assert.equal(f.api.setVisualsVisible(true), true);
  assert.equal(f.api.status().visualsVisible, true); assert.equal(f.api.status().haloCount, 2);
  assert.equal(f.api.status().animatedCount, 2); assert.equal(layer.hidden, false);
  const [outline] = f.graph.nodes[0].getContainer().shapes;
  assert.equal(outline.visible, true); assert.equal(outline.attrs.r, 43);
  assert.deepEqual(sorted(f.api.ids), ids); assert.equal(f.changes.length, notifications);
});

test('visual suspension survives item replacement but never restores revoked or destroyed bindings', () => {
  const f = fixture(1); f.click(0); const oldItem = f.graph.nodes[0];
  f.api.setVisualsVisible(false);
  const replacement = f.makeItem({ ...f.allModels[0], size: 48 }); f.graph.nodes = [replacement]; f.api.rebind();
  assert.equal(oldItem.getContainer().shapes.length, 0);
  assert.equal(replacement.getContainer().shapes[0].visible, false);
  assert.equal(f.api.setVisualsVisible(true), true); assert.equal(replacement.getContainer().shapes[0].visible, true);
  f.api.setVisualsVisible(false); f.stale();
  assert.equal(f.api.setVisualsVisible(true), false); f.api.refresh();
  assert.equal(replacement.getContainer().shapes.length, 0); assert.equal(f.api.status().haloCount, 0);
  assert.equal(f.api.status().visualsVisible, false);
  f.api.destroy(); assert.equal(f.api.setVisualsVisible(true), false); assert.equal(f.container.children.length, 0);
});

test('busy history blocks new input while still permitting geometry rebind and refresh', () => {
  let busy = true;
  const f = fixture(12, { canInteract: () => !busy });
  f.api.rebind(['SYN-0']);
  f.click(1); f.key({ metaKey: true }); f.key({ key: 'Escape' }); f.down(0); f.pointer('mousemove');
  assert.deepEqual(sorted(f.api.ids), ['SYN-0']);
  assert.equal(f.primary.length, 0); assert.equal(f.starts.length, 0);
  assert.equal(f.api.status().haloCount, 1);
  busy = false; f.key({ metaKey: true }); assert.equal(f.api.ids.size, 12);
});

test('geometry refresh between G6 item disposal and rebind never reads a destroyed model', () => {
  const f = fixture(); f.click(0);
  const removed = f.graph.nodes[0];
  removed.destroyed = true; removed.getModel = () => { throw new TypeError('disposed G6 model'); };
  f.graph.nodes = f.graph.nodes.slice(1);
  assert.doesNotThrow(() => f.graph.emit('aftergraphrefreshposition', {}));
  assert.doesNotThrow(() => f.api.rebind());
  assert.equal(f.api.ids.size, 0); assert.equal(f.api.status().haloCount, 0);
});

test('selection preserves the configured text weight instead of G6 selected weight', () => {
  const f = fixture();
  f.allModels[0].labelCfg = {style:{fontWeight:'bold'}};
  f.click(0);
  assert.equal(f.graph.nodes[0].get('styles').selected['text-shape'].fontWeight, 'bold');
  f.click(1);
  assert.equal(f.graph.nodes[1].get('styles').selected['text-shape'].fontWeight, 'normal');
});

for (const nextEvent of ['mousemove', 'mouseup']) test(`a pending node gesture cannot start on ${nextEvent} after history becomes busy`, () => {
  let busy = false;
  const f = fixture(3, { canInteract: () => !busy }); f.click(0); f.down(0);
  f.pointer('mousemove', { clientX: 83, clientY: 90 }); // Still below the four-pixel threshold.
  const original = f.allModels.map(model => [model.x, model.y]);
  busy = true; f.pointer(nextEvent); f.pointer('mouseup');
  assert.deepEqual(f.allModels.map(model => [model.x, model.y]), original);
  assert.equal(f.starts.length, 0); assert.equal(f.ends.length, 0); assert.equal(f.before.length, 0);
  assert.equal(f.api.status().commitCount, 0); assert.equal(f.window.events.has('mousemove'), false);
  f.api.rebind(['SYN-2']); f.api.refresh();
  assert.deepEqual(sorted(f.api.ids), ['SYN-2']); assert.equal(f.api.status().haloCount, 1);
});
