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
    const calls = [];
    const context = { calls, inks: [] };
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
  const callbacks = new Map(), allCallbacks = [], navigations = [], cancelled = [];
  let nextId = 0;
  const canvas = { width: 200, height: 120, getContext: () => context,
    getBoundingClientRect: () => ({ left: 20, top: 30, width: 100, height: 60 }),
    addEventListener: (name, fn) => events.set(name, fn),
    removeEventListener: (name, fn) => { if (events.get(name) === fn) events.delete(name); },
    setPointerCapture: id => captured.add(id), releasePointerCapture: id => captured.delete(id) };
  const backing = { width: 0, height: 0, getContext: () => base };
  const api = minimap.create({ canvas, createCanvas: () => backing, onNavigate: point => navigations.push(plain(point)), ...options,
    raf: fn => { const id = ++nextId; callbacks.set(id, fn); allCallbacks.push(fn); return id; },
    caf: id => { callbacks.delete(id); cancelled.push(id); } });
  const event = (name, values = {}) => events.get(name)?.({ pointerId: 1, button: 0, clientX: 70, clientY: 60,
    preventDefault() {}, ...values });
  const flush = () => { const work = [...callbacks.values()]; callbacks.clear(); work.forEach(fn => fn()); };
  return { api, canvas, backing, context, base, events, captured, callbacks, allCallbacks, navigations, cancelled, event, flush };
}

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
  assert.equal(f.base.calls.filter(c => c[0] === 'rect').length, 4999);
  assert.equal(f.base.calls.filter(c => c[0] === 'arc').length, 1);
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
  f.event('pointerdown');
  const frames = f.allCallbacks.length;
  f.api.setTheme('light');
  assert.equal(f.captured.has(1), true); assert.equal(f.callbacks.size, 1); assert.equal(f.allCallbacks.length, frames);
  f.flush(); assert.deepEqual(f.navigations, [{ x: 0, y: 0 }]);
  assert.deepEqual(f.context.inks.slice(-2), [['fillRect', 'rgba(35,103,173,0.12)'], ['strokeRect', '#2367ad']]);
  f.event('pointermove', { clientX: 116 }); f.api.setTheme('dark'); f.flush();
  assert.equal(f.navigations.length, 2); assert.equal(f.navigations[1].x, 100); assert.equal(f.captured.has(1), true);
  f.event('pointerup'); f.flush(); assert.equal(f.captured.size, 0);
});

test('click and captured drag navigate in graph coordinates, coalescing input and respecting CSS scale', () => {
  const f = fixture();
  f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50 }] }); f.flush();
  f.event('pointerdown');
  assert.equal(f.captured.has(1), true); f.flush();
  assert.deepEqual(f.navigations, [{ x: 0, y: 0 }]);
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
});

test('foreign pointers, secondary buttons, cancellation and empty geometry cannot navigate', () => {
  const f = fixture();
  f.api.setData({ nodes: {} }); f.flush();
  f.event('pointerdown'); f.flush(); assert.equal(f.navigations.length, 0);
  f.api.setData({ nodes: [{ x: 0, y: 0 }] }); f.flush();
  f.event('pointerdown', { button: 2 }); f.event('pointerdown', { isPrimary: false }); f.flush();
  assert.equal(f.navigations.length, 0);
  f.event('pointerdown'); f.event('pointermove', { pointerId: 2 });
  f.event('pointercancel'); f.flush();
  assert.equal(f.navigations.length, 0); assert.equal(f.captured.size, 0);
  f.event('pointerdown'); f.event('lostpointercapture'); f.flush();
  assert.equal(f.navigations.length, 0);
});

test('clear synchronously erases a case, releases capture and rejects already dispatched old RAF callbacks', () => {
  const f = fixture();
  f.api.setData({ nodes: [{ x: 1000, y: 1000 }, { x: 2000, y: 2000 }] }); f.flush();
  f.event('pointerdown'); const stale = f.allCallbacks.at(-1);
  f.api.clear();
  assert.equal(f.callbacks.size, 0); assert.equal(f.captured.size, 0);
  assert.equal(f.context.calls.at(-1)[0], 'clearRect');
  const afterClear = f.context.calls.length;
  stale(); assert.equal(f.context.calls.length, afterClear); assert.equal(f.navigations.length, 0);
  f.api.setData({ nodes: [{ x: -100, y: -50 }, { x: 100, y: 50 }] }); f.flush();
  f.event('pointerdown'); f.flush();
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
  assert.deepEqual(f.navigations, [{ x: 5, y: 5 }]);
});

test('destroy is idempotent, removes listeners and late callbacks or API calls cannot restore data', () => {
  const f = fixture();
  f.api.setData({ nodes: [{ x: 0, y: 0 }] });
  const stale = f.allCallbacks.at(-1);
  f.api.destroy(); f.api.destroy();
  assert.equal(f.events.size, 0); assert.equal(f.callbacks.size, 0);
  assert.equal(f.backing.width, 0); assert.equal(f.backing.height, 0);
  const count = f.context.calls.length;
  stale(); f.api.setData({ nodes: [{ x: 100, y: 100 }] }); f.api.setViewport({ minX: 0, minY: 0, maxX: 10, maxY: 10 });
  f.api.resize(); f.api.clear(); f.api.setTheme('light');
  assert.equal(f.context.calls.length, count); assert.equal(f.navigations.length, 0);
});
