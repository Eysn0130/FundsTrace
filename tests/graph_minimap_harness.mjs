import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const root = fileURLToPath(new URL('../', import.meta.url));
const sandbox = { window: {} };
vm.runInNewContext(readFileSync(`${root}static/graph-minimap.js`, 'utf8'), sandbox, { filename: 'graph-minimap.js' });
const minimap = sandbox.window.FundsGraphMinimap;
const plain = value => JSON.parse(JSON.stringify(value));

function fixture(options = {}) {
  const operations = () => {
    const calls = [], styles = [];
    const context = { calls, styles, inks: [] };
    for (const name of ['fillStyle', 'strokeStyle']) {
      let value;
      Object.defineProperty(context, name, { get: () => value, set: next => { value = next; styles.push([name, next]); } });
    }
    for (const name of ['clearRect', 'drawImage', 'fillRect', 'strokeRect', 'beginPath', 'rect', 'arc', 'moveTo', 'fill']) {
      context[name] = (...args) => {
        calls.push([name, ...args]);
        if (name === 'fill' || name === 'fillRect') context.inks.push([name, context.fillStyle]);
        if (name === 'strokeRect') context.inks.push([name, context.strokeStyle]);
      };
    }
    return context;
  };
  const context = operations(), base = operations(), events = new Map(), captured = new Set();
  const callbacks = new Map(), allCallbacks = [], navigations = [], navigationMeta = [], gestures = [], cancelled = [], statuses = [], focuses = [];
  let nextId = 0;
  const canvas = { width: 200, height: 120, getContext: () => context,
    getBoundingClientRect: () => ({ left: 20, top: 30, width: 100, height: 60 }),
    addEventListener: (name, fn) => events.set(name, fn),
    removeEventListener: (name, fn) => { if (events.get(name) === fn) events.delete(name); },
    focus: settings => focuses.push(plain(settings)),
    setPointerCapture: id => captured.add(id), releasePointerCapture: id => captured.delete(id) };
  const backing = { width: 0, height: 0, getContext: () => base };
  const api = minimap.create({ ...options, canvas, createCanvas: () => backing,
    onNavigate: (point, meta) => { navigations.push(plain(point)); navigationMeta.push(meta && plain(meta)); options.onNavigate?.(point, meta); },
    onGestureStart: () => { gestures.push(true); options.onGestureStart?.(); },
    onStatusChange: status => { statuses.push(plain(status)); options.onStatusChange?.(status); },
    raf: fn => { const id = ++nextId; callbacks.set(id, fn); allCallbacks.push(fn); return id; },
    caf: id => { callbacks.delete(id); cancelled.push(id); } });
  const event = (name, values = {}) => events.get(name)?.({ pointerId: 1, button: 0, clientX: 70, clientY: 60,
    preventDefault() {}, ...values });
  const flush = () => { const work = [...callbacks.values()]; callbacks.clear(); work.forEach(fn => fn()); };
  return { api, canvas, backing, context, base, events, captured, callbacks, allCallbacks, navigations, navigationMeta, gestures,
    cancelled, statuses, focuses, event, flush };
}

test('a primary click confirms once on pointer-up with bounded graph and logical canvas coordinates', () => {
  const f = fixture();
  f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50 }] }); f.flush();
  f.event('pointerdown', { clientX: 118 }); f.flush();
  assert.deepEqual(f.navigations, [], 'pressing the map does not locate before the gesture is classified');
  assert.equal(f.captured.has(1), true); assert.equal(f.gestures.length, 1);
  f.event('pointerup', { clientX: 118 });
  assert.equal(f.captured.size, 0); assert.deepEqual(f.navigations, [], 'confirmation remains frame-coalesced');
  f.flush();
  assert.deepEqual(f.navigations, [{ x: 100, y: 0 }]);
  assert.deepEqual(f.navigationMeta, [{ kind: 'click', position: { x: 196, y: 60 } }]);
  f.event('pointerup', { clientX: 118 }); f.event('pointermove'); f.flush();
  assert.equal(f.navigations.length, 1, 'release or move events after completion cannot repeat the click');
});

test('an inside click with sub-four-pixel CSS jitter locates the released point once', () => {
  const f = fixture();
  f.canvas.width = 216; f.canvas.height = 116;
  f.canvas.getBoundingClientRect = () => ({ left: 20, top: 30, width: 108, height: 58 });
  f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50 }] });
  f.api.setViewport({ minX: -40, minY: -20, maxX: 40, maxY: 20 }); f.flush();
  f.event('pointerdown', { clientX: 84, clientY: 54 });
  f.event('pointermove', { clientX: 86, clientY: 55 }); f.flush();
  assert.deepEqual(f.navigations, [], 'minor pointer jitter is not a drag');
  f.event('pointerup', { clientX: 85, clientY: 54 }); f.flush();
  assert.deepEqual(f.navigations, [{ x: 22, y: -10 }]);
  assert.deepEqual(f.navigationMeta, [{ kind: 'click', position: { x: 130, y: 48 } }]);
});

test('outside dragging keeps the viewport down offset and reports only drag targets through host feedback', () => {
  const f = fixture();
  f.canvas.width = 216; f.canvas.height = 116;
  f.canvas.getBoundingClientRect = () => ({ left: 20, top: 30, width: 108, height: 58 });
  f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50 }] });
  f.api.setViewport({ minX: -40, minY: -20, maxX: 40, maxY: 20 }); f.flush();
  f.event('pointerdown', { clientX: 34, clientY: 44 }); f.flush();
  assert.deepEqual(f.navigations, [], 'pressing outside the viewport cannot jump before a relative drag');
  f.event('pointermove', { clientX: 44, clientY: 49 }); f.flush();
  assert.deepEqual(f.navigations, [{ x: 20, y: 10 }]);
  f.api.setViewport({ minX: -20, minY: -10, maxX: 60, maxY: 30 });
  f.event('pointermove', { clientX: 54, clientY: 54 }); f.flush();
  assert.deepEqual(f.navigations.at(-1), { x: 40, y: 20 });
  f.event('pointerup', { clientX: 54, clientY: 54 }); f.flush();
  assert.equal(f.captured.size, 0); assert.deepEqual(f.navigations.at(-1), { x: 40, y: 20 });
  assert.deepEqual(f.navigationMeta.map(meta => meta.kind), ['drag', 'drag', 'drag']);
  assert.deepEqual(f.navigationMeta.at(-1).position, { x: 68, y: 48 });
});

test('drag takeover starts from the live viewport supplied synchronously by gesture start', () => {
  const f = fixture({ onGestureStart() {
    f.api.setViewport({ minX: -20, minY: -10, maxX: 60, maxY: 30 });
  } });
  f.canvas.width = 216; f.canvas.height = 116;
  f.canvas.getBoundingClientRect = () => ({ left: 20, top: 30, width: 108, height: 58 });
  f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50 }] });
  f.api.setViewport({ minX: -40, minY: -20, maxX: 40, maxY: 20 }); f.flush();
  f.event('pointerdown', { clientX: 84, clientY: 54 });
  f.event('pointermove', { clientX: 94, clientY: 59 }); f.flush();
  assert.deepEqual(f.navigations, [{ x: 40, y: 20 }],
    'the live center (20,10) plus CSS movement (10,5) at 2x scale must not pull back to the cached center');
  f.event('pointerup', { clientX: 94, clientY: 59 }); f.flush();
  assert.deepEqual(f.navigations.at(-1), { x: 40, y: 20 });
  assert.deepEqual(f.navigationMeta.map(meta => meta.kind), ['drag', 'drag']);
});

test('the four CSS pixel threshold includes a release without move events and stays a drag after returning', () => {
  const f = fixture();
  f.canvas.width = 216; f.canvas.height = 116;
  f.canvas.getBoundingClientRect = () => ({ left: 20, top: 30, width: 108, height: 58 });
  f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50 }] });
  f.api.setViewport({ minX: -40, minY: -20, maxX: 40, maxY: 20 }); f.flush();
  f.event('pointerdown', { clientX: 84, clientY: 54 });
  f.event('pointermove', { clientX: 87.9, clientY: 54 }); f.flush();
  assert.deepEqual(f.navigations, []);
  f.event('pointerup', { clientX: 88, clientY: 54 }); f.flush();
  assert.deepEqual(f.navigations, [{ x: 8, y: 0 }]); assert.equal(f.navigationMeta[0].kind, 'drag');
  f.event('pointerdown', { clientX: 84, clientY: 54 });
  f.event('pointerup', { clientX: 88, clientY: 54 }); f.flush();
  assert.deepEqual(f.navigations.at(-1), { x: 8, y: 0 }); assert.equal(f.navigationMeta.at(-1).kind, 'drag');
  f.event('pointerdown', { clientX: 84, clientY: 54 });
  f.event('pointermove', { clientX: 88, clientY: 54 });
  f.event('pointerup', { clientX: 84, clientY: 54 }); f.flush();
  assert.deepEqual(f.navigations.at(-1), { x: 0, y: 0 }); assert.equal(f.navigationMeta.at(-1).kind, 'drag');
});

test('gesture start runs synchronously before capture and focus and revocation inside it fails closed', () => {
  for (const revoke of [api => api.clear(), api => api.setData({ nodes: [{ x: 5, y: 5 }] }), api => api.destroy(),
    api => { api.setEnabled(false); api.setEnabled(true); }, api => api.cancelNavigation(), () => { throw Error('cancel'); }]) {
    let f;
    f = fixture({ onGestureStart() {
      assert.equal(f.captured.size, 0); assert.deepEqual(f.focuses, []); revoke(f.api);
    } });
    f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50 }] }); f.flush();
    f.event('pointerdown');
    assert.equal(f.gestures.length, 1); assert.equal(f.captured.size, 0); assert.deepEqual(f.focuses, []);
    f.event('pointerup'); f.flush(); assert.deepEqual(f.navigations, []);
  }
  let busy = false;
  const f = fixture({ canNavigate: () => !busy, onGestureStart: () => { busy = true; } });
  f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50 }] }); f.flush();
  f.event('pointerdown'); f.event('pointerup'); f.flush();
  assert.equal(f.gestures.length, 1); assert.equal(f.captured.size, 0); assert.deepEqual(f.navigations, []);
});

test('revocation cancels queued click and final drag targets after capture has already been released', () => {
  for (const kind of ['click', 'drag']) {
    for (const revoke of [api => api.cancelNavigation(), api => { api.setEnabled(false); api.setEnabled(true); },
      api => api.clear(), api => api.setData({ nodes: [{ x: 1000, y: 1000 }] }), api => api.destroy()]) {
      const f = fixture();
      f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50 }] }); f.flush();
      f.event('pointerdown');
      f.event('pointerup', kind === 'drag' ? { clientX: 80 } : {});
      const stale = f.allCallbacks.at(-1);
      assert.equal(f.captured.size, 0); assert.equal(f.callbacks.size, 1);
      revoke(f.api); stale(); f.flush();
      assert.deepEqual(f.navigations, []); assert.equal(f.captured.size, 0);
    }
  }
});

test('bounds reject invalid inputs and expand one-point/one-axis graphs without a zero divisor', () => {
  assert.equal(minimap.bounds([]), null);
  assert.equal(minimap.bounds({}), null);
  assert.equal(minimap.bounds([null, { x: NaN, y: 3 }, { x: 1, y: Infinity }]), null);
  assert.deepEqual(plain(minimap.bounds([{ x: 5, y: 9 }])), { minX: 4, minY: 8, maxX: 6, maxY: 10 });
  assert.deepEqual(plain(minimap.bounds([{ x: 0, y: 0 }, { x: 10, y: 0 }])), { minX: 0, minY: -1, maxX: 10, maxY: 1 });
  assert.equal(minimap.bounds([{ x: -Number.MAX_VALUE, y: 0 }, { x: Number.MAX_VALUE, y: 1 }]), null);
  assert.equal(minimap.transform({ minX: 0, minY: 0, maxX: 0, maxY: 1 }, 200, 100), null);
  assert.equal(minimap.transform({ minX: 0, minY: 0, maxX: 1, maxY: 1 }, 0, 100), null);
});

test('aspect-preserving geometry matches independently calculated coordinates and clips the viewport', () => {
  const mapping = minimap.transform({ minX: -100, minY: -50, maxX: 100, maxY: 50 }, 220, 120, 10);
  assert.equal(mapping.scale, 1);
  assert.deepEqual(plain(minimap.project({ x: 0, y: 0 }, mapping)), { x: 110, y: 60 });
  assert.deepEqual(plain(minimap.project({ x: -100, y: -50 }, mapping)), { x: 10, y: 10 });
  assert.deepEqual(plain(minimap.unproject({ x: 160, y: 35 }, mapping)), { x: 50, y: -25 });
  assert.deepEqual(plain(minimap.viewportRect({ minX: -50, minY: -25, maxX: 50, maxY: 25 }, mapping)),
    { x: 60, y: 35, width: 100, height: 50 });
  assert.deepEqual(plain(minimap.viewportRect({ minX: -1000, minY: -1000, maxX: 1000, maxY: 1000 }, mapping)),
    { x: 0, y: 0, width: 220, height: 120 });
  assert.equal(minimap.viewportRect({ minX: 1000, minY: 1000, maxX: 1100, maxY: 1100 }, mapping), null);
  assert.equal(minimap.viewportRect({ minX: 10, minY: 0, maxX: 0, maxY: 1 }, mapping), null);
  assert.equal(minimap.project({ x: Infinity, y: 0 }, mapping), null);
});

test('5000-node base is cached and 100 coalesced viewport updates draw no node paths', () => {
  const f = fixture();
  const nodes = Array.from({ length: 5000 }, (_, i) => Object.freeze({ id: `SYN-${i}`, x: i % 100, y: Math.floor(i / 100),
    isMain: i === 0, amount: '9999999999999999.99' }));
  const data = Object.freeze({ nodes: Object.freeze(nodes), edges: Object.freeze([]) });
  const original = JSON.stringify(data);
  f.api.setData(data); f.flush();
  assert.equal(f.base.calls.filter(c => c[0] === 'rect').length, 0);
  assert.equal(f.base.calls.filter(c => c[0] === 'arc' && c[3] === 1.4).length, 4999);
  assert.equal(f.base.calls.filter(c => c[0] === 'arc' && c[3] === 2.5).length, 1);
  assert.equal(f.base.calls.filter(c => c[0] === 'beginPath').length, 2);
  assert.equal(f.base.calls.filter(c => c[0] === 'fill').length, 2);
  for (const [index, call] of f.base.calls.entries()) {
    if (call[0] !== 'arc') continue;
    assert.deepEqual(f.base.calls[index - 1], ['moveTo', call[1] + call[3], call[2]],
      'each circle starts a separate subpath so the batched dots cannot connect');
    assert.deepEqual(call.slice(4), [0, Math.PI * 2]);
  }
  const baseCalls = f.base.calls.length;
  f.context.calls.length = 0;
  for (let i = 0; i < 100; i++) f.api.setViewport({ minX: i / 100, minY: 0, maxX: 50, maxY: 20 });
  assert.equal(f.callbacks.size, 1);
  f.flush();
  assert.equal(f.base.calls.length, baseCalls, 'viewport gestures never rebuild or traverse the node base');
  assert.deepEqual(f.context.calls.map(c => c[0]), ['clearRect', 'drawImage', 'fillRect', 'strokeRect']);
  assert.equal(JSON.stringify(data), original);
});

test('initial theme and theme changes paint the requested ink while retaining cached geometry and viewport', () => {
  for (const initial of [undefined, 'light']) {
    const f = fixture(initial ? { theme: initial } : {});
    const data = { nodes: [{ x: -100, y: -50, isMain: true }, { x: 100, y: 50 }] };
    f.api.setData(data); f.api.setViewport({ minX: -50, minY: -25, maxX: 50, maxY: 25 }); f.flush();
    const baseCalls = f.base.calls.length;
    const baseGeometry = f.base.calls.filter(call => ['rect', 'moveTo', 'arc'].includes(call[0]));
    const viewportGeometry = f.context.calls.filter(call => call[0] === 'fillRect' || call[0] === 'strokeRect');
    const assertInk = theme => {
      assert.deepEqual(f.base.inks.slice(-2), [['fill', theme === 'light' ? '#2367ad' : '#679bd6'], ['fill', '#ffd16c']]);
      assert.deepEqual(f.context.inks.slice(-2), theme === 'light'
        ? [['fillRect', 'rgba(35,103,173,0.12)'], ['strokeRect', '#2367ad']]
        : [['fillRect', 'rgba(122,184,255,0.12)'], ['strokeRect', '#b7d9ff']]);
    };
    assertInk(initial || 'dark');
    // setData copied the geometry; a theme change cannot reread caller data.
    data.nodes[1].x = 9000;
    const next = initial === 'light' ? 'dark' : 'light';
    f.api.setTheme(next); f.flush(); assertInk(next);
    assert.deepEqual(f.base.calls.slice(baseCalls).filter(call => ['rect', 'moveTo', 'arc'].includes(call[0])), baseGeometry);
    assert.deepEqual(f.context.calls.filter(call => call[0] === 'fillRect' || call[0] === 'strokeRect').slice(-2), viewportGeometry);
    const calls = [f.base.calls.length, f.context.calls.length, f.allCallbacks.length];
    f.api.setTheme(next); f.flush();
    assert.deepEqual([f.base.calls.length, f.context.calls.length, f.allCallbacks.length], calls, 'the same resolved theme is a no-op');
  }
});

test('theme changes preserve pointer capture, pending navigation and a scheduled frame', () => {
  const f = fixture();
  f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50 }] });
  f.api.setViewport({ minX: -50, minY: -25, maxX: 50, maxY: 25 }); f.flush();
  f.event('pointerdown'); f.event('pointermove', { clientX: 116 });
  const frames = f.allCallbacks.length;
  f.api.setTheme('light');
  assert.equal(f.captured.has(1), true); assert.equal(f.callbacks.size, 1); assert.equal(f.allCallbacks.length, frames);
  f.flush(); assert.deepEqual(f.navigations, [{ x: 100, y: 0 }]);
  assert.deepEqual(f.context.inks.slice(-2), [['fillRect', 'rgba(35,103,173,0.12)'], ['strokeRect', '#2367ad']]);
  f.event('pointermove', { clientX: 70 }); f.api.setTheme('dark'); f.flush();
  assert.equal(f.navigations.length, 2); assert.equal(f.navigations[1].x, 0); assert.equal(f.captured.has(1), true);
  f.event('pointerup'); f.flush(); assert.equal(f.captured.size, 0);
});

test('custom colors override initial theme defaults until a changed legacy theme restores its palette', () => {
  const f = fixture({ theme: 'light', colors: { node: '#102030', main: '#405060' } });
  f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50, isMain: true }] });
  f.api.setViewport({ minX: -50, minY: -25, maxX: 50, maxY: 25 }); f.flush();
  assert.deepEqual(f.base.inks.slice(-2), [['fill', '#102030'], ['fill', '#405060']]);
  assert.deepEqual(f.context.inks.slice(-2), [['fillRect', 'rgba(35,103,173,0.12)'], ['strokeRect', '#2367ad']]);
  f.api.setColors({ viewportStroke: '#708090' }); f.flush();
  const status = f.api.getStatus(), calls = [f.base.calls.length, f.context.calls.length, f.allCallbacks.length];
  f.api.setTheme('light'); f.flush();
  assert.deepEqual([f.base.calls.length, f.context.calls.length, f.allCallbacks.length], calls,
    'the unchanged legacy theme preserves explicit color overrides without rebuilding');
  assert.equal(f.context.strokeStyle, '#708090');
  f.api.setTheme('dark'); f.flush();
  assert.deepEqual(f.base.inks.slice(-2), [['fill', '#679bd6'], ['fill', '#ffd16c']]);
  assert.deepEqual(f.context.inks.slice(-2), [['fillRect', 'rgba(122,184,255,0.12)'], ['strokeRect', '#b7d9ff']]);
  assert.equal(f.api.getStatus(), status, 'the compatibility entry preserves the current viewport status');
  f.api.setColors({ main: '#aabbcc' }); f.flush();
  assert.deepEqual(f.base.inks.slice(-2), [['fill', '#679bd6'], ['fill', '#aabbcc']]);
  f.api.setTheme('light'); f.flush();
  assert.deepEqual(f.base.inks.slice(-2), [['fill', '#2367ad'], ['fill', '#ffd16c']]);
});

test('legacy theme changes after clear or destroy never restore old geometry or navigation', () => {
  const f = fixture({ theme: 'light' });
  f.api.setData({ nodes: [{ x: 1000, y: 1000 }, { x: 2000, y: 2000 }] }); f.flush();
  f.event('pointerdown'); f.event('pointerup'); const stale = f.allCallbacks.at(-1);
  f.api.clear(); f.base.calls.length = 0; f.context.calls.length = 0;
  const status = f.api.getStatus();
  f.api.setTheme('dark'); stale(); f.flush();
  assert.deepEqual(f.base.calls.map(call => call[0]), ['clearRect']);
  assert.deepEqual(f.context.calls.map(call => call[0]), ['clearRect']);
  assert.equal(f.api.getStatus(), status); assert.equal(status.state, 'empty');
  assert.equal(f.captured.size, 0); assert.deepEqual(f.navigations, []);
  f.api.destroy();
  const calls = [f.base.calls.length, f.context.calls.length, f.allCallbacks.length];
  f.api.setTheme('light'); f.api.setColors({ node: '#ffffff' }); f.flush();
  assert.deepEqual([f.base.calls.length, f.context.calls.length, f.allCallbacks.length], calls);
  assert.equal(f.backing.width, 0); assert.equal(f.backing.height, 0);
});

test('click and captured drag navigate in graph coordinates, coalescing input and respecting CSS scale', () => {
  const f = fixture();
  f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50 }] }); f.flush();
  f.event('pointerdown');
  assert.equal(f.captured.has(1), true); f.flush();
  assert.deepEqual(f.navigations, []);
  f.event('pointerup'); f.flush();
  assert.deepEqual(f.navigations, [{ x: 0, y: 0 }]);
  f.event('pointerdown');
  f.event('pointermove', { clientX: 95 });
  f.event('pointermove', { clientX: 116 });
  f.flush();
  assert.equal(f.navigations.length, 2);
  assert.equal(f.navigations[1].x, 100);
  assert.equal(f.navigations[1].y, 0);
  f.event('pointerup', { clientX: -500, clientY: 1000 }); f.flush();
  assert.deepEqual(f.navigations.at(-1), { x: -100, y: 50 });
  assert.equal(f.captured.size, 0);
  f.event('pointermove'); f.flush();
  assert.equal(f.navigations.length, 3);
  assert.deepEqual(f.navigationMeta.map(meta => meta.kind), ['click', 'drag', 'drag']);
});

test('foreign pointers, secondary buttons, cancellation and empty geometry cannot navigate', () => {
  const f = fixture();
  f.api.setData({ nodes: {} }); f.flush();
  f.event('pointerdown'); f.flush(); assert.equal(f.navigations.length, 0);
  f.api.setData({ nodes: [{ x: 0, y: 0 }] }); f.flush();
  f.event('pointerdown', { button: 2 }); f.event('pointerdown', { isPrimary: false }); f.flush();
  assert.equal(f.navigations.length, 0);
  assert.equal(f.gestures.length, 0, 'empty geometry, secondary buttons and non-primary pointers cannot start a camera gesture');
  f.event('pointerdown'); f.event('pointermove', { pointerId: 2 });
  f.event('pointercancel'); f.flush();
  assert.equal(f.navigations.length, 0); assert.equal(f.captured.size, 0);
  f.event('pointerdown'); f.event('lostpointercapture'); f.flush();
  assert.equal(f.navigations.length, 0);
});

test('grabbing the viewport preserves the grabbed offset through host pan feedback', () => {
  const f = fixture();
  // The 216×116 canvas has exactly 8px inset and 1:1 world scale for this graph.
  // Its CSS box is half-sized, so moving 10 CSS pixels pans by 20 world units.
  f.canvas.width = 216; f.canvas.height = 116;
  f.canvas.getBoundingClientRect = () => ({ left: 20, top: 30, width: 108, height: 58 });
  f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50 }] });
  f.api.setViewport({ minX: -40, minY: -20, maxX: 40, maxY: 20 }); f.flush();
  // World (20,-10) is 20 right and 10 above the viewport center.
  f.event('pointerdown', { clientX: 84, clientY: 54 }); f.flush();
  assert.deepEqual(f.navigations, [], 'grabbing an off-center point must not jump the camera');
  f.event('pointerup', { clientX: 84, clientY: 54 }); f.flush();
  assert.deepEqual(f.navigations, [{ x: 20, y: -10 }], 'a confirmed inside click locates its world point');
  assert.equal(f.navigationMeta[0].kind, 'click');
  f.event('pointerdown', { clientX: 84, clientY: 54 });
  f.event('pointermove', { clientX: 94, clientY: 59 }); f.flush();
  assert.deepEqual(f.navigations, [{ x: 20, y: -10 }, { x: 20, y: 10 }]);
  f.api.setViewport({ minX: -20, minY: -10, maxX: 60, maxY: 30 });
  f.event('pointermove', { clientX: 104, clientY: 64 }); f.flush();
  assert.deepEqual(f.navigations.at(-1), { x: 40, y: 20 });
  f.event('pointerup', { clientX: 124, clientY: 64 }); f.flush();
  assert.deepEqual(f.navigations.at(-1), { x: 80, y: 20 });
  assert.equal(f.captured.size, 0);
  // Clicking outside the viewport still locates the clicked world point.
  f.event('pointerdown', { clientX: 34, clientY: 44 }); f.flush();
  assert.deepEqual(f.navigations.at(-1), { x: 80, y: 20 }, 'pressing outside still waits for release');
  f.event('pointerup', { clientX: 34, clientY: 44 }); f.flush();
  assert.deepEqual(f.navigations.at(-1), { x: -80, y: -30 });
  assert.equal(f.navigationMeta.at(-1).kind, 'click');
});

test('disabled, busy and explicitly cancelled navigation revoke capture and queued input', () => {
  let busy = false;
  const f = fixture({ canNavigate: () => !busy });
  f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50 }] }); f.flush();
  f.api.setEnabled(false); f.event('pointerdown'); f.flush();
  assert.equal(f.captured.size, 0); assert.deepEqual(f.navigations, []);
  f.api.setEnabled(true); f.event('pointerdown');
  f.event('pointermove', { clientX: 80 });
  assert.equal(f.captured.has(1), true);
  busy = true; f.flush();
  assert.equal(f.captured.size, 0); assert.deepEqual(f.navigations, [], 'busy state is rechecked at RAF commit');
  f.event('pointerdown');
  assert.equal(f.captured.size, 0, 'busy state also blocks starting a gesture');
  busy = false; f.event('pointermove'); f.flush();
  assert.deepEqual(f.navigations, [], 'an invalidated gesture cannot resume');
  f.event('pointerdown'); f.event('pointermove', { clientX: 80 }); const late = f.allCallbacks.at(-1);
  f.api.setEnabled(false); f.api.setEnabled(true); late(); f.flush();
  assert.equal(f.captured.size, 0); assert.deepEqual(f.navigations, [], 'reopening cannot submit old input');
  f.event('pointerdown'); f.api.cancelNavigation(); f.event('pointerup'); f.flush();
  assert.deepEqual(f.navigations, []); assert.equal(f.captured.size, 0);
  f.event('pointerdown'); f.event('pointerup'); f.flush();
  assert.deepEqual(f.navigations, [{ x: 0, y: 0 }]);
  f.event('pointerdown');
  busy = true; f.event('pointermove');
  assert.equal(f.captured.size, 0, 'a newly busy gesture releases capture synchronously on input');
});

test('an allowed map click focuses the canvas without scrolling while disabled or busy clicks do not', () => {
  let busy = false;
  const f = fixture({ canNavigate: () => !busy });
  f.event('pointerdown');
  assert.deepEqual(f.focuses, [], 'empty geometry cannot take focus');
  f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50 }] }); f.flush();
  f.api.setEnabled(false); f.event('pointerdown');
  assert.deepEqual(f.focuses, [], 'a disabled map must not move keyboard focus');
  f.api.setEnabled(true); busy = true; f.event('pointerdown');
  assert.deepEqual(f.focuses, [], 'a busy map must not move keyboard focus');
  busy = false; f.event('pointerdown', { button: 2 });
  assert.deepEqual(f.focuses, [], 'only an allowed primary click may take focus');
  f.event('pointerdown');
  assert.deepEqual(f.focuses, [{ preventScroll: true }]);
  assert.equal(f.captured.has(1), true);
  assert.equal(f.gestures.length, 1, 'disabled and busy presses never notify gesture start');
});

test('status is immutable geometry only and reports a viewport completely outside the graph', () => {
  const f = fixture();
  assert.deepEqual(plain(f.api.getStatus()), { state: 'empty', viewport: null });
  f.api.setData({ nodes: [{ x: -100, y: -50, account: 'SYNTHETIC' }, { x: 100, y: 50 }] });
  assert.deepEqual(plain(f.api.getStatus()), { state: 'ready', viewport: null });
  f.api.setViewport({ minX: -50, minY: -25, maxX: 50, maxY: 25 });
  const inside = f.api.getStatus();
  assert.deepEqual(plain(inside), { state: 'viewport', viewport: { x: 54, y: 37, width: 92, height: 46 } });
  assert.equal(Object.isFrozen(inside), true); assert.equal(Object.isFrozen(inside.viewport), true);
  assert.throws(() => { inside.viewport.x = 99; }, TypeError);
  const count = f.statuses.length;
  f.api.setViewport({ minX: -50, minY: -25, maxX: 50, maxY: 25 });
  assert.equal(f.statuses.length, count, 'unchanged geometry does not notify UI again');
  f.api.setViewport({ minX: 1000, minY: 1000, maxX: 1100, maxY: 1100 });
  assert.deepEqual(plain(f.api.getStatus()), { state: 'outside', viewport: null });
  f.api.setViewport({ minX: 0, minY: 0, maxX: NaN, maxY: 5 });
  assert.deepEqual(plain(f.api.getStatus()), { state: 'ready', viewport: null });
  const stale = f.allCallbacks.at(-1);
  f.api.clear(); const afterClear = f.statuses.length;
  stale();
  assert.deepEqual(plain(f.api.getStatus()), { state: 'empty', viewport: null });
  assert.equal(f.statuses.length, afterClear, 'an old callback cannot restore cleared geometry or status');
  assert.deepEqual(plain(inside), { state: 'viewport', viewport: { x: 54, y: 37, width: 92, height: 46 } },
    'previous status snapshots do not mutate');
});

test('optional colors style the same cached dots and viewport without additional drawing primitives', () => {
  const f = fixture({ colors: { node: '#a6b1c0', main: '#e8b34f', viewportStroke: '#c56b4d',
    viewportFill: 'rgba(197,107,77,0.07)' } });
  f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50, isMain: true }] });
  f.api.setViewport({ minX: -50, minY: -25, maxX: 50, maxY: 25 }); f.flush();
  assert.equal(f.base.fillStyle, '#e8b34f');
  assert.equal(f.context.fillStyle, 'rgba(197,107,77,0.07)');
  assert.equal(f.context.strokeStyle, '#c56b4d');
  assert.deepEqual(f.context.calls.map(c => c[0]), ['clearRect', 'clearRect', 'drawImage', 'fillRect', 'strokeRect']);
});

test('changed colors rebuild the geometry base once and repaint the preserved viewport', () => {
  const f = fixture();
  const data = Object.freeze({ nodes: Object.freeze([
    Object.freeze({ x: -100, y: -50, amount: '9999999999999999.99' }),
    Object.freeze({ x: 100, y: 50, isMain: true, account: 'SYNTHETIC' }),
  ]) });
  f.api.setData(data);
  f.api.setViewport({ minX: -50, minY: -25, maxX: 50, maxY: 25 }); f.flush();
  const status = f.api.getStatus(), statusCount = f.statuses.length;
  const geometry = f.base.calls.filter(c => ['rect', 'moveTo', 'arc'].includes(c[0]));
  f.base.calls.length = 0; f.base.styles.length = 0; f.context.calls.length = 0;
  f.api.setColors({ node: '#a6b1c0', main: '#e8b34f', viewportStroke: '#c56b4d',
    viewportFill: 'rgba(197,107,77,0.07)', ignored: '#ffffff' });
  assert.equal(f.base.calls.filter(c => c[0] === 'clearRect').length, 1);
  assert.deepEqual(f.base.calls.filter(c => c[0] === 'arc').map(c => c[3]), [1.4, 2.5]);
  assert.equal(f.base.calls.filter(c => c[0] === 'beginPath').length, 2);
  assert.equal(f.base.calls.filter(c => c[0] === 'fill').length, 2);
  assert.deepEqual(f.base.calls.filter(c => ['rect', 'moveTo', 'arc'].includes(c[0])), geometry,
    'recoloring retains the copied point geometry and main-node selection');
  assert.deepEqual(f.base.styles, [['fillStyle', '#a6b1c0'], ['fillStyle', '#e8b34f']]);
  assert.equal(f.api.getStatus(), status, 'appearance changes preserve the viewport status snapshot');
  assert.equal(f.statuses.length, statusCount);
  assert.equal(f.callbacks.size, 1);
  f.flush();
  assert.equal(f.context.fillStyle, 'rgba(197,107,77,0.07)');
  assert.equal(f.context.strokeStyle, '#c56b4d');
  assert.deepEqual(f.context.calls.map(c => c[0]), ['clearRect', 'drawImage', 'fillRect', 'strokeRect']);
  const rebuilt = f.base.calls.length;
  f.api.setViewport({ minX: -40, minY: -20, maxX: 60, maxY: 30 }); f.flush();
  assert.equal(f.base.calls.length, rebuilt, 'viewport-only updates still reuse the recolored base');
});

test('equal and invalid color patches do not rebuild or schedule a repaint', () => {
  const colors = { node: '#a6b1c0', main: '#e8b34f', viewportStroke: '#c56b4d',
    viewportFill: 'rgba(197,107,77,0.07)' };
  const f = fixture({ colors });
  f.api.setData({ nodes: [{ x: 0, y: 0 }] }); f.flush();
  const baseCount = f.base.calls.length, paintCount = f.context.calls.length;
  for (const patch of [undefined, null, false, 7, 'blue', {}, [], colors,
    { node: '', main: 15, viewportStroke: '   ', viewportFill: null, other: '#ffffff' }]) f.api.setColors(patch);
  assert.equal(f.base.calls.length, baseCount);
  assert.equal(f.context.calls.length, paintCount);
  assert.equal(f.callbacks.size, 0);
  f.api.setColors({ node: '#718294', main: '', viewportStroke: undefined });
  assert.deepEqual(f.base.styles.slice(-2), [['fillStyle', '#718294'], ['fillStyle', '#e8b34f']],
    'valid partial patches preserve the other colors');
  f.flush();
});

test('a color change cancels captured and queued navigation and preserves the enabled state', () => {
  const f = fixture();
  f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50 }] });
  f.api.setViewport({ minX: -50, minY: -25, maxX: 50, maxY: 25 }); f.flush();
  f.event('pointerdown', { clientX: 116 });
  f.event('pointermove', { clientX: 100 });
  const late = f.allCallbacks.at(-1);
  assert.equal(f.captured.has(1), true);
  f.api.setColors({ node: '#718294' });
  assert.equal(f.captured.size, 0, 'theme changes release pointer capture synchronously');
  late(); f.event('pointerup'); f.flush();
  assert.deepEqual(f.navigations, [], 'neither dispatched RAF nor late pointer-up may submit the old pan');
  f.api.setEnabled(false);
  f.api.setColors({ main: '#e8b34f' }); f.event('pointerdown', { clientX: 116 }); f.flush();
  assert.equal(f.captured.size, 0); assert.deepEqual(f.navigations, [], 'recoloring does not enable a disabled map');
  f.api.setEnabled(true); f.event('pointerdown', { clientX: 116 }); f.event('pointerup', { clientX: 116 }); f.flush();
  assert.deepEqual(f.navigations, [{ x: 100, y: 0 }], 'fresh navigation still uses the preserved geometry');
});

test('colors changed after clear keep the map empty and carry forward without reviving old points', () => {
  const f = fixture();
  f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50 }] });
  f.api.setViewport({ minX: -50, minY: -25, maxX: 50, maxY: 25 }); f.flush();
  f.event('pointerdown', { clientX: 116 }); f.event('pointerup', { clientX: 116 }); const stale = f.allCallbacks.at(-1);
  f.api.clear();
  f.base.calls.length = 0; f.context.calls.length = 0;
  f.api.setColors({ main: '#e8b34f', viewportStroke: '#c56b4d', viewportFill: 'rgba(197,107,77,0.07)' });
  stale(); f.flush(); f.event('pointerdown'); f.flush();
  assert.deepEqual(plain(f.api.getStatus()), { state: 'empty', viewport: null });
  assert.deepEqual(f.base.calls.map(c => c[0]), ['clearRect']);
  assert.deepEqual(f.context.calls.map(c => c[0]), ['clearRect']);
  assert.equal(f.captured.size, 0); assert.deepEqual(f.navigations, []);
  f.api.setData({ nodes: [{ x: 10, y: 20, isMain: true }] }); f.flush();
  assert.deepEqual(plain(f.api.getStatus()), { state: 'ready', viewport: null });
  assert.equal(f.base.calls.filter(c => c[0] === 'arc').length, 1);
  assert.equal(f.base.calls.filter(c => c[0] === 'rect').length, 0);
  assert.equal(f.base.fillStyle, '#e8b34f');
  f.api.setViewport({ minX: 9, minY: 19, maxX: 11, maxY: 21 }); f.flush();
  assert.equal(f.context.strokeStyle, '#c56b4d');
  assert.equal(f.context.fillStyle, 'rgba(197,107,77,0.07)');
});

test('clear synchronously erases a case, releases capture and rejects already dispatched old RAF callbacks', () => {
  const f = fixture();
  f.api.setData({ nodes: [{ x: 1000, y: 1000 }, { x: 2000, y: 2000 }] }); f.flush();
  f.event('pointerdown'); f.event('pointermove', { clientX: 80 }); const stale = f.allCallbacks.at(-1);
  f.api.clear();
  assert.equal(f.callbacks.size, 0); assert.equal(f.captured.size, 0);
  assert.equal(f.context.calls.at(-1)[0], 'clearRect');
  const afterClear = f.context.calls.length;
  stale(); assert.equal(f.context.calls.length, afterClear); assert.equal(f.navigations.length, 0);
  f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50 }] }); f.flush();
  f.event('pointerdown'); f.event('pointerup'); f.flush();
  assert.deepEqual(f.navigations, [{ x: 0, y: 0 }]);
  stale(); assert.equal(f.navigations.length, 1);
});

test('data replacement cancels old gestures and resize explicitly rebuilds for the new canvas dimensions', () => {
  const f = fixture(), input = [{ x: 0, y: 0 }, { x: 100, y: 100 }];
  f.api.setData({ nodes: input }); f.flush();
  input[1].x = 9000;
  f.event('pointerdown');
  f.api.setData({ nodes: [{ x: 0, y: 0 }, { x: 10, y: 10 }] });
  f.flush(); assert.equal(f.navigations.length, 0);
  f.canvas.width = 400; f.canvas.height = 240;
  f.api.resize(); f.api.setViewport({ minX: 0, minY: 0, maxX: 10, maxY: 10 }); f.flush();
  assert.equal(f.backing.width, 400); assert.equal(f.backing.height, 240);
  assert.equal(f.context.calls.at(-1)[0], 'strokeRect');
  f.event('pointerdown'); f.flush();
  assert.deepEqual(f.navigations, [], 'grabbing the visible viewport does not recenter it');
  f.event('pointermove', { clientX: 75 }); f.flush();
  assert.equal(f.navigations.length, 1);
  assert.ok(Math.abs(f.navigations[0].x - (5 + 20 / 22.4)) < 1e-10);
  assert.equal(f.navigations[0].y, 5);
});

test('destroy is idempotent, removes listeners and late callbacks or API calls cannot restore data', () => {
  const f = fixture();
  f.api.setData({ nodes: [{ x: 0, y: 0 }] });
  const stale = f.allCallbacks.at(-1);
  f.api.destroy(); f.api.destroy();
  assert.equal(f.events.size, 0); assert.equal(f.callbacks.size, 0);
  assert.equal(f.backing.width, 0); assert.equal(f.backing.height, 0);
  const count = f.context.calls.length, baseCount = f.base.calls.length;
  stale(); f.api.setData({ nodes: [{ x: 100, y: 100 }] }); f.api.setViewport({ minX: 0, minY: 0, maxX: 10, maxY: 10 });
  f.api.resize(); f.api.clear(); f.api.setTheme('light'); f.api.setColors({ node: '#ffffff', main: '#e8b34f' });
  assert.equal(f.context.calls.length, count); assert.equal(f.navigations.length, 0);
  assert.equal(f.base.calls.length, baseCount); assert.equal(f.callbacks.size, 0);
  assert.equal(f.backing.width, 0); assert.equal(f.backing.height, 0);
});
