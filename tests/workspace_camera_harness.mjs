import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const sandbox = { window: {} };
vm.runInNewContext(readFileSync(new URL('../static/workspace-camera.js', import.meta.url), 'utf8'), sandbox);
const create = sandbox.window.FundsWorkspaceCamera.create;
const plain = value => JSON.parse(JSON.stringify(value));

function harness({ left = 560, top = 180, width = 880, height = 720, zoom = 1, panX = 0, panY = 0,
  bounds, visibilityBounds, obstacles, maxZoom = 4, minZoom = 0.02, cameraCreate = create } = {}) {
  const calls = [], notifications = [];
  const box = { left, top, width, height };
  const container = { get clientWidth() { return box.width; }, get clientHeight() { return box.height; },
    getBoundingClientRect: () => ({ ...box }) };
  const camera = { zoom, x: panX, y: panY };
  let current = true, suspended = false, compositionAllowed = true, boundsReads = 0, visibilityReads = 0;
  const graph = { destroyed: false, width, height, maxZoom, minZoom,
    group: { getMatrix: () => [camera.zoom, 0, 0, 0, camera.zoom, 0, camera.x, camera.y, 1],
      setMatrix(value) { camera.zoom = value[0]; camera.x = value[6]; camera.y = value[7]; } },
    get(name) { return this[name]; },
    changeSize(w, h) { calls.push(['size', w, h]); this.width = w; this.height = h; },
    translate(x, y) { calls.push(['translate', x, y]); camera.x += x; camera.y += y; },
  };
  const api = cameraCreate({ graph, container, isCurrent: () => current, isSuspended: () => suspended,
    canCompose: () => compositionAllowed, getContentBounds: () => { boundsReads++; return bounds; },
    getVisibilityBounds: () => { visibilityReads++; return visibilityBounds; },
    getViewportObstacles: obstacles,
    onResize: detail => notifications.push(plain(detail)) });
  const screen = (x, y) => ({ x: box.left + x * camera.zoom + camera.x, y: box.top + y * camera.zoom + camera.y });
  const move = (x, y = box.top) => { box.width += box.left - x; box.height += box.top - y; box.left = x; box.top = y; return api.resize(); };
  return { api, box, graph, camera, calls, notifications, screen, move,
    current(value) { current = value; }, suspend(value) { suspended = value; },
    canCompose(value) { compositionAllowed = value; }, get boundsReads() { return boundsReads; },
    get visibilityReads() { return visibilityReads; } };
}

const near = (actual, expected, message) => assert.ok(Math.abs(actual - expected) < 1e-8, message || `${actual} ≠ ${expected}`);

test('explicit collapse scales a complete graph with the available width and expands reversibly', () => {
  const h = harness({ bounds: { minX: 0, minY: 0, maxX: 400, maxY: 200 }, panX: 240, panY: 260 });
  const initial = { ...h.camera };
  assert.equal(h.api.beginSidebar({ collapsed: true }), true);
  for (const left of [520, 400, 200, 0]) h.move(left);
  h.api.endSidebar();
  near(h.camera.zoom, 18 / 11); // 1440px / 880px, with ample height and padding.
  near(h.screen(200, 100).x, 720); near(h.screen(200, 100).y, 540);
  assert.ok(h.screen(0, 0).x >= 24 && h.screen(400, 200).x <= 1416);
  assert.equal(h.boundsReads, 1, 'content geometry is measured once, never on every frame');
  h.api.beginSidebar({ collapsed: false });
  for (const left of [60, 240, 420, 560]) h.move(left);
  h.api.endSidebar();
  near(h.camera.zoom, initial.zoom); near(h.camera.x, initial.x); near(h.camera.y, initial.y);
  near(h.api.getTransform().scale, 1); near(h.api.getTransform().x, 0); near(h.api.getTransform().y, 0);
});

test('hidden canonical labels cannot misclassify an actually complete fitted graph as right overflow', () => {
  const h = harness({ left: 390, width: 326, height: 720, panX: 38, panY: 100,
    bounds: { minX: -70, minY: 0, maxX: 320, maxY: 100 },
    visibilityBounds: { minX: 0, minY: 0, maxX: 250, maxY: 100 } });
  h.api.beginSidebar({ collapsed: true }); h.move(0); h.api.endSidebar();
  near(h.camera.zoom, 334 / 195); // 668px after horizontal padding / 390px canonical content.
  assert.ok(h.camera.x - 70 * h.camera.zoom >= 24 && h.camera.x + 320 * h.camera.zoom <= 716 - 24,
    'growth still reserves the canonical envelope for newly revealed labels');
  assert.equal(h.boundsReads, 1); assert.equal(h.visibilityReads, 1);
});

test('326-to-716 expansion uses the full viewport width ratio when height is unconstrained', () => {
  const bounds = Object.freeze({ minX: 0, minY: 0, maxX: 261, maxY: 80 });
  const h = harness({ left: 390, width: 326, height: 720, panX: 32.5, panY: 100, bounds });
  h.graph.getNodes = () => { throw new Error('composition must not read or rewrite model geometry'); };
  h.graph.layout = () => { throw new Error('composition must not relayout nodes'); };
  h.api.beginSidebar({ collapsed: true });
  for (const left of [300, 190, 80, 0]) h.move(left);
  h.api.endSidebar();
  near(h.camera.zoom, 716 / 326);
  near(261 * h.camera.zoom, 261 * 716 / 326);
  near(h.camera.x + 130.5 * h.camera.zoom, 358);
  assert.ok(261 * h.camera.zoom > 570, 'the 261px drawing uses over 570px of the widened canvas');
  assert.deepEqual(bounds, { minX: 0, minY: 0, maxX: 261, maxY: 80 });
  h.api.beginSidebar({ collapsed: false }); h.move(390); h.api.endSidebar();
  near(h.camera.zoom, 1); near(h.camera.x, 32.5); near(h.camera.y, 100);
});

test('right-overflow reveal follows visible content rather than distant undrawn canonical text', () => {
  const h = harness({ bounds: { minX: -10000, minY: 0, maxX: 20000, maxY: 100 },
    visibilityBounds: { minX: 0, minY: 0, maxX: 1000, maxY: 100 }, panX: 50, panY: 100 });
  h.api.beginSidebar({ collapsed: true }); h.move(0); h.api.endSidebar();
  near(h.camera.zoom, 1); near(h.camera.x, 416); near(h.camera.x + 1000, 1416);
});

test('subpixel fit rounding within half a pixel does not disable appropriate growth', () => {
  const h = harness({ bounds: { minX: 0, minY: 0, maxX: 400, maxY: 200 }, panX: 23.8, panY: 100 });
  h.api.beginSidebar({ collapsed: true }); h.move(0);
  assert.ok(h.camera.zoom > 1);
});

test('complete graph growth respects vertical padding, max zoom and overlay clearance', () => {
  const tall = harness({ bounds: { minX: 0, minY: 0, maxX: 300, maxY: 600 }, panX: 290, panY: 60 });
  tall.api.beginSidebar({ collapsed: true }); tall.move(0);
  near(tall.camera.zoom, 1.12);
  near(tall.screen(0, 0).y, 180 + 24); near(tall.screen(300, 600).y, 900 - 24);
  const capped = harness({ bounds: { minX: 0, minY: 0, maxX: 300, maxY: 100 }, panX: 290, panY: 200, maxZoom: 1.05 });
  capped.api.beginSidebar({ collapsed: true }); capped.move(0); near(capped.camera.zoom, 1.05);
  const overlay = harness({ bounds: { minX: 0, minY: 0, maxX: 400, maxY: 200 }, panX: 240, panY: 100,
    obstacles: ({ width }) => [{ bounds: { minX: width / 2 - 100, maxX: width / 2 + 100, minY: 0, maxY: 60 } }] });
  overlay.api.beginSidebar({ collapsed: true }); overlay.move(0);
  near(overlay.camera.zoom, 1.16);
  near(overlay.screen(0, 0).y, 180 + 60 + 24);
});

test('right overflow is revealed by translation at the existing scale without sacrificing the visible left', () => {
  const h = harness({ bounds: { minX: 0, minY: 0, maxX: 1200, maxY: 100 }, panX: 50, panY: 100 });
  h.api.beginSidebar({ collapsed: true });
  for (const left of [500, 400, 200, 0]) {
    h.move(left); near(h.camera.zoom, 1);
    assert.ok(h.camera.x >= 50, 'the content left edge stays within the widened local viewport');
  }
  h.api.endSidebar();
  near(h.camera.x, 216); near(h.camera.x + 1200, 1416);
  assert.ok(h.camera.x >= 24);
});

test('an oversized graph never fits down or jumps to its rightmost node', () => {
  const h = harness({ bounds: { minX: -5000, minY: -1000, maxX: 9000, maxY: 6000 }, zoom: 1.2, panX: 100, panY: 50 });
  const originalVisibleLeft = -h.camera.x / h.camera.zoom;
  h.api.beginSidebar({ collapsed: true }); h.move(0); h.api.endSidebar();
  near(h.camera.zoom, 1.2); near(h.camera.x, 100); near(h.camera.y, 50);
  near(-h.camera.x / h.camera.zoom, originalVisibleLeft);
  assert.ok(h.camera.x + 9000 * h.camera.zoom > h.box.width, 'the final node is not forced into view at the expense of all previous content');
});

test('rapid reversal follows the same physical geometry continuously and returns to the original camera', () => {
  const h = harness({ bounds: { minX: 0, minY: 0, maxX: 300, maxY: 200 }, panX: 290, panY: 260 });
  h.api.beginSidebar({ collapsed: true }); h.move(300);
  const first = { ...h.camera };
  h.api.beginSidebar({ collapsed: false }); assert.deepEqual(h.camera, first);
  h.move(400); h.api.beginSidebar({ collapsed: true }); h.move(300);
  near(h.camera.zoom, first.zoom); near(h.camera.x, first.x); near(h.camera.y, first.y);
  h.api.beginSidebar({ collapsed: false }); h.move(560); h.api.endSidebar();
  near(h.camera.zoom, 1); near(h.camera.x, 290); near(h.camera.y, 260);
  assert.equal(h.boundsReads, 1);
});

test('manual camera intervention and unavailable composition cancel automatic framing and keep live intent', () => {
  const h = harness({ bounds: { minX: 0, minY: 0, maxX: 300, maxY: 200 }, panX: 290, panY: 260 });
  h.api.beginSidebar({ collapsed: true }); h.move(300);
  h.camera.zoom = 2.2; h.camera.x += 47; const intended = { ...h.camera };
  h.move(0); near(h.camera.zoom, intended.zoom); near(h.camera.x, intended.x + 300);
  h.api.endSidebar(); h.api.beginSidebar({ collapsed: false }); h.move(560); h.api.endSidebar();
  near(h.camera.zoom, intended.zoom); near(h.camera.x, intended.x - 260);
  const locked = harness({ bounds: { minX: 0, minY: 0, maxX: 200, maxY: 200 } });
  locked.canCompose(false); assert.equal(locked.api.beginSidebar({ collapsed: true }), false); locked.move(0);
  near(locked.camera.zoom, 1); near(locked.camera.x, 560); assert.equal(locked.boundsReads, 0);
});

test('sidebar intent during export waits for restored camera and dimensions before composing once', () => {
  const h = harness({ bounds: { minX: 0, minY: 0, maxX: 400, maxY: 200 }, panX: 240, panY: 260 });
  const initial = { ...h.camera };
  h.suspend(true); h.graph.width = 2640; h.graph.height = 2160;
  Object.assign(h.camera, { zoom: 0.2, x: 800, y: 900 });
  h.api.beginSidebar({ collapsed: true }); h.move(0); h.api.endSidebar();
  assert.deepEqual(h.calls, []); assert.equal(h.boundsReads, 0);
  h.graph.width = 880; h.graph.height = 720; Object.assign(h.camera, initial);
  h.suspend(false); h.api.resume();
  near(h.camera.zoom, 18 / 11); near(h.camera.x, 4320 / 11); near(h.camera.y, 2160 / 11);
  h.api.beginSidebar({ collapsed: false }); h.move(560); h.api.endSidebar();
  near(h.camera.zoom, initial.zoom); near(h.camera.x, initial.x); near(h.camera.y, initial.y);
});

test('export started midway through collapse preserves its journey and finishes after sampling restoration', () => {
  const h = harness({ bounds: { minX: 0, minY: 0, maxX: 400, maxY: 200 }, panX: 240, panY: 260 });
  h.api.beginSidebar({ collapsed: true }); h.move(350);
  const saved = { ...h.camera }, sampledWidth = h.graph.width;
  h.suspend(true); h.graph.width = sampledWidth * 3; h.graph.height = 2160;
  Object.assign(h.camera, { zoom: 0.2, x: 800, y: 900 });
  h.move(0); h.api.endSidebar();
  Object.assign(h.camera, saved); h.graph.width = sampledWidth; h.graph.height = 720;
  h.suspend(false); h.api.resume();
  near(h.camera.zoom, 18 / 11); near(h.camera.x, 4320 / 11); near(h.camera.y, 2160 / 11);
  h.api.beginSidebar({ collapsed: false }); h.move(560); h.api.endSidebar();
  near(h.camera.zoom, 1); near(h.camera.x, 240); near(h.camera.y, 260);
  assert.equal(h.boundsReads, 1, 'export does not replace the original camera intent or repeat graph measurement');
});

test('ordinary width resizing preserves screen positions at arbitrary zoom without automatic composition', () => {
  for (const zoom of [0.02, 0.35, 1, 2.6, 4]) {
    const h = harness({ zoom, panX: -143.25, panY: 82.75 });
    const points = [[0, 0], [50, -60], [-888, 1250.2]];
    const before = points.map(point => h.screen(...point));
    for (const left of [560, 511.25, 409.5, 230.75, 80.125, 0]) {
      h.move(left);
      points.forEach((point, i) => assert.deepEqual(h.screen(...point), before[i]));
      assert.equal(h.camera.zoom, zoom);
    }
    for (const left of [0, 29.125, 219.75, 440, 560]) {
      h.move(left);
      points.forEach((point, i) => assert.deepEqual(h.screen(...point), before[i]));
    }
    assert.deepEqual(plain(h.api.getOffset()), { x: 0, y: 0 });
    assert.equal(h.graph.width, 880);
  }
});

test('rapid reversal, fractional geometry and width dragging preserve positions without layout or fit', () => {
  const h = harness({ zoom: 1.3, panX: 34, panY: -48 });
  const before = h.screen(101, 99);
  for (const left of [550.375, 410.125, 430.625, 210, 390.25, 760, 360, 560]) {
    // clientWidth is integer on the real DOM while rect.left retains fractions.
    h.box.width = Math.round(1440 - left); h.box.left = left; h.api.resize();
    assert.deepEqual(h.screen(101, 99), before);
  }
  assert.equal(h.calls.some(call => call[0] === 'layout' || call[0] === 'fit'), false);
});

test('shell compensation adds to user pan and zoom performed during transition', () => {
  const h = harness({ zoom: 1.4, panX: -30, panY: 25 });
  h.move(400);
  h.camera.x += 47; h.camera.y -= 13; h.camera.zoom = 2.8;
  const afterUserGesture = h.screen(140, 50);
  h.move(120); h.move(0); h.move(80);
  assert.deepEqual(h.screen(140, 50), afterUserGesture);
  assert.equal(h.camera.zoom, 2.8);
  assert.equal(h.camera.x, -30 + 47 + 480);
  assert.equal(h.camera.y, 25 - 13);
});

test('toolbar collapse and position-only viewport movement also retain screen coordinates', () => {
  const h = harness({ zoom: 0.75, panX: 11, panY: 22 });
  const before = h.screen(300, 230);
  h.move(560, 95);
  assert.deepEqual(h.screen(300, 230), before);
  const sizeCalls = h.calls.filter(call => call[0] === 'size').length;
  h.box.left += 33.5; h.box.top += 20.25; h.api.resize();
  assert.deepEqual(h.screen(300, 230), before);
  assert.equal(h.calls.filter(call => call[0] === 'size').length, sizeCalls);
  h.box.width += 100; h.api.resize();
  assert.deepEqual(h.screen(300, 230), before);
});

test('export sampling owns dimensions and camera until restoration, then receives the accumulated shell delta once', () => {
  const h = harness({ zoom: 2, panX: -100, panY: 30 });
  const before = h.screen(50, 80), savedCamera = { ...h.camera };
  h.suspend(true);
  h.graph.width = 2640; h.graph.height = 2160;
  Object.assign(h.camera, { zoom: 0.75, x: 800, y: 900 });
  h.move(300); h.move(80); h.move(120, 90);
  assert.deepEqual(h.calls, []);
  assert.deepEqual(h.notifications, []);
  assert.equal(h.graph.width, 2640);
  assert.deepEqual(h.camera, { zoom: 0.75, x: 800, y: 900 });
  h.graph.width = 880; h.graph.height = 720;
  Object.assign(h.camera, savedCamera);
  h.suspend(false); h.api.resume();
  assert.deepEqual(h.screen(50, 80), before);
  assert.deepEqual(h.calls, [['size', 1320, 810], ['translate', 440, 90]]);
  h.api.resize(); h.api.resume();
  assert.equal(h.notifications.length, 1);
  assert.equal(h.calls.length, 2);
});

test('same-size overlay opening and repeated observer deliveries are pure no-ops', () => {
  const h = harness();
  for (let i = 0; i < 10; i++) h.api.resize();
  assert.deepEqual(h.calls, []);
  assert.deepEqual(h.notifications, []);
});

test('unavailable, hidden, stale and destroyed graphs never receive a late camera mutation', () => {
  const h = harness();
  h.box.width = 0; h.box.left = 400; h.api.resize();
  h.box.width = 1040; h.current(false); h.api.resize();
  h.current(true); h.graph.destroyed = true; h.api.resize();
  assert.deepEqual(h.calls, []);
  h.graph.destroyed = false; h.api.resize();
  assert.deepEqual(h.calls, [['size', 1040, 720], ['translate', 160, 0]]);
  h.api.destroy(); h.move(0); h.api.resume(); h.api.rebase();
  assert.equal(h.calls.length, 2);
  assert.equal(create({ container: h.box }).resize(), null);
});

test('rebase accepts a deliberately replaced camera without applying prior shell movement', () => {
  const h = harness();
  h.box.left = 0; h.box.width = 1440;
  Object.assign(h.camera, { zoom: 0.8, x: 100, y: 200 });
  assert.equal(h.api.rebase(), true); h.api.resize();
  assert.deepEqual(h.calls, [['size', 1440, 720]]);
  const target = h.screen(50, 50); h.move(200);
  assert.deepEqual(h.screen(50, 50), target);
});

test('recreating a graph retains the workspace compensation used by its existing history snapshots', () => {
  const h = harness();
  h.move(0, 90);
  const offset = h.api.getOffset(); h.api.destroy();
  const replacement = create({ graph: h.graph, container: {
    clientWidth: h.box.width, clientHeight: h.box.height, getBoundingClientRect: () => h.box,
  }, initialOffset: offset });
  assert.deepEqual(plain(replacement.getOffset()), { x: 560, y: 90 });
  replacement.rebase(); replacement.resize();
  assert.deepEqual(plain(replacement.getOffset()), { x: 560, y: 90 });
  assert.equal(h.calls.length, 2, 'new graph baseline does not repeat an already-applied compensation');
});

test('graph replacement retains the cumulative affine required by zoom-aware history restoration', () => {
  const h = harness({ bounds: { minX: 0, minY: 0, maxX: 400, maxY: 200 }, panX: 240, panY: 260 });
  h.api.beginSidebar({ collapsed: true }); h.move(0); h.api.endSidebar();
  const transform = plain(h.api.getTransform()); h.api.destroy();
  const replacement = create({ graph: h.graph, container: {
    clientWidth: h.box.width, clientHeight: h.box.height, getBoundingClientRect: () => h.box,
  }, initialTransform: transform });
  replacement.rebase(); replacement.resize();
  assert.deepEqual(plain(replacement.getTransform()), transform); near(transform.scale, 18 / 11);
});

test('one failed size operation retains the full pending shell displacement for recovery', () => {
  const h = harness();
  const resize = h.graph.changeSize;
  h.graph.changeSize = () => { throw new Error('synthetic disposed size target'); };
  h.move(300);
  assert.deepEqual(h.calls, []);
  h.graph.changeSize = resize;
  h.api.resize();
  assert.deepEqual(h.calls, [['size', 1140, 720], ['translate', 260, 0]]);
});

function focusHarness(settings = {}) {
  let time = 0, nextFrame = 0;
  const frames = new Map(), listeners = new Map(), doc = { hidden: false,
    addEventListener(name, callback) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(callback); },
    removeEventListener(name, callback) { listeners.get(name)?.delete(callback); } }, media = { matches: false };
  const root = { document: doc, performance: { now: () => time }, matchMedia: () => media,
    requestAnimationFrame(callback) { const id = ++nextFrame; frames.set(id, callback); return id; },
    cancelAnimationFrame(id) { frames.delete(id); } };
  const context = { window: root };
  vm.runInNewContext(readFileSync(new URL('../static/graph-layout.js', import.meta.url), 'utf8'), context);
  root.FundsGraphLayout = context.FundsGraphLayout;
  vm.runInNewContext(readFileSync(new URL('../static/workspace-camera.js', import.meta.url), 'utf8'), context);
  const h = harness({ ...settings, cameraCreate: root.FundsWorkspaceCamera.create });
  let paints = 0; h.graph.paint = () => { paints++; };
  return { ...h, doc, media,
    visibility(hidden) { doc.hidden = hidden; for (const callback of listeners.get('visibilitychange') || []) callback(); },
    tick(nextTime) {
      time = nextTime;
      const pending = [...frames.values()]; frames.clear();
      for (const callback of pending) callback(time);
    },
    get pendingFrames() { return frames.size; }, get paints() { return paints; } };
}

test('focus pans smoothly to the unoccluded local center with exact completion and no chrome compensation', async () => {
  const h = focusHarness({ left: 390, top: 180, width: 1000, height: 700, zoom: 1.3, panX: 57, panY: -20 });
  const updates = [], completed = [], initial = { ...h.camera }, transform = plain(h.api.getTransform());
  const point = { x: 1200, y: 800 }, intent = h.api.getIntent();
  const result = h.api.focus(point, {
    bounds: { minX: 1170, maxX: 1230, minY: 780, maxY: 820 },
    obstacles: [{ bounds: { minX: 700, maxX: 1000, minY: 0, maxY: 700 } }],
    onFrame: value => updates.push(plain(value)), onFinish: value => completed.push(plain(value)) });
  assert.ok(h.api.getIntent() > intent, 'intent changes before the first animation frame');
  assert.deepEqual(h.camera, initial); assert.equal(h.pendingFrames, 1);
  h.tick(0); h.tick(80); const early = { ...h.camera };
  h.tick(160); const middle = { ...h.camera };
  assert.ok(early.x < initial.x && middle.x < early.x, 'the same pan progresses without a jump');
  assert.equal(h.camera.zoom, initial.zoom, 'locating does not automatically enlarge or shrink a fitting target');
  h.tick(320);
  assert.deepEqual(plain(await result), { ok: true, reason: 'focused' });
  near(h.screen(point.x, point.y).x, 390 + 350); near(h.screen(point.x, point.y).y, 180 + 350);
  assert.equal(updates.at(-1).progress, 1); assert.equal(completed.length, 1); assert.equal(h.pendingFrames, 0);
  assert.deepEqual(plain(h.api.getTransform()), transform, 'focus translation is not sidebar compensation');
  h.api.resize(); near(h.screen(point.x, point.y).x, 740);
});

test('focus keeps the node center clear of a top menu and minimap while reserving asymmetric text', async () => {
  const h = focusHarness({ width: 1000, height: 700 });
  const point = { x: 300, y: 100 }, obstacles = [
    { bounds: { minX: 0, maxX: 1000, minY: 0, maxY: 150 } },
    { bounds: { minX: 720, maxX: 1000, minY: 500, maxY: 700 } },
  ];
  const done = h.api.focus(point, { duration: 0, obstacles,
    bounds: { minX: 280, maxX: 450, minY: 80, maxY: 155 } });
  assert.equal((await done).ok, true);
  const projected = h.screen(point.x, point.y), local = { x: projected.x - h.box.left, y: projected.y - h.box.top };
  const envelope = { minX: local.x - 150, maxX: local.x + 150, minY: local.y - 55, maxY: local.y + 55 };
  for (const { bounds: b } of obstacles) assert.ok(envelope.maxX <= b.minX - 24 || envelope.minX >= b.maxX + 24
    || envelope.maxY <= b.minY - 24 || envelope.minY >= b.maxY + 24, 'the complete centered target clears each overlay');
  near(local.x, 500); near(local.y, 325); near(h.camera.zoom, 1);
});

test('focus only reduces scale when the target cannot fit and finishes at a precise continuous matrix', async () => {
  const h = focusHarness({ width: 400, height: 300, zoom: 2, panX: -100, panY: 40 });
  const promise = h.api.focus({ x: 900, y: 700 }, { bounds: { minX: 600, maxX: 1200, minY: 600, maxY: 800 } });
  h.tick(80); const firstZoom = h.camera.zoom; h.tick(160); const secondZoom = h.camera.zoom;
  assert.ok(firstZoom < 2 && secondZoom < firstZoom);
  h.tick(320); assert.equal((await promise).ok, true);
  near(h.camera.zoom, 352 / 600); near(h.screen(900, 700).x - h.box.left, 200); near(h.screen(900, 700).y - h.box.top, 150);
});

test('focus rejects invalid targets and unavailable areas without interrupting accepted camera intent', async () => {
  const h = focusHarness({ width: 400, height: 300 });
  const promise = h.api.focus({ x: 900, y: 700 }); h.tick(80);
  const before = { ...h.camera }, intent = h.api.getIntent();
  const rejected = [
    await h.api.focus({ x: NaN, y: 0 }),
    await h.api.focus({ x: 0, y: 0 }, { bounds: { minX: 5, maxX: 1, minY: 0, maxY: 1 } }),
    await h.api.focus({ x: 0, y: 0 }, { obstacles: [{ bounds: { minX: 0, maxX: 400, minY: 0, maxY: 300 } }] }),
    await h.api.focus({ x: 0, y: 0 }, { bounds: { minX: -1e9, maxX: 1e9, minY: -1e9, maxY: 1e9 } }),
  ];
  assert.ok(rejected.every(value => !value.ok)); assert.equal(h.api.getIntent(), intent);
  assert.deepEqual(h.camera, before); assert.equal(h.pendingFrames, 1);
  h.tick(320); assert.equal((await promise).ok, true);
});

test('reduced motion completes synchronously and a mid-flight preference change settles once', async () => {
  const h = focusHarness(); h.media.matches = true;
  const first = h.api.focus({ x: 700, y: 900 });
  assert.equal(h.pendingFrames, 0); near(h.screen(700, 900).x - h.box.left, 440);
  assert.equal((await first).ok, true);
  h.media.matches = false;
  let finishes = 0;
  const second = h.api.focus({ x: -500, y: 200 }, { onFinish: () => { finishes++; } });
  h.tick(80); h.media.matches = true; h.tick(96);
  assert.equal((await second).ok, true); near(h.screen(-500, 200).x - h.box.left, 440);
  assert.equal(finishes, 1); assert.equal(h.pendingFrames, 0);
});

test('redirected focus starts at the live intermediate camera and releases the superseded promise', async () => {
  const h = focusHarness(), results = [];
  const first = h.api.focus({ x: 1000, y: 900 }, { onFinish: value => results.push(plain(value)) });
  h.tick(100); const before = { ...h.camera };
  const second = h.api.focus({ x: -800, y: 200 });
  assert.deepEqual(h.camera, before); assert.equal((await first).reason, 'superseded');
  assert.equal(h.pendingFrames, 1); h.tick(100); assert.deepEqual(h.camera, before);
  h.tick(260); h.tick(420); assert.equal((await second).ok, true);
  near(h.screen(-800, 200).x - h.box.left, 440); near(h.screen(-800, 200).y - h.box.top, 360);
  assert.deepEqual(results, [{ ok: false, reason: 'superseded' }]);
});

for (const [reason, interrupt] of [
  ['composition-cancelled', h => h.api.cancelComposition()],
  ['manual-input', h => h.api.cancelFocus('manual-input')],
  ['sidebar', h => h.api.beginSidebar({ collapsed: true })],
  ['rebased', h => h.api.rebase()],
  ['destroyed', h => h.api.destroy()],
  ['resized', h => { h.box.width += 30; h.api.resize(); }],
  ['resized', h => { h.box.top += 10; }],
  ['stale', h => h.current(false)],
  ['stale', h => { h.graph.destroyed = true; }],
  ['suspended', h => h.suspend(true)],
  ['hidden', h => { h.doc.hidden = true; }],
  ['camera-changed', h => { h.camera.x += 73; }],
]) test(`focus releases its promise and stops late frames after ${reason}`, async () => {
  const h = focusHarness(); let finishes = 0;
  const result = h.api.focus({ x: 1300, y: 800 }, { onFinish: () => { finishes++; } });
  h.tick(80); interrupt(h); const before = { ...h.camera };
  h.tick(160); assert.deepEqual(plain(await result), { ok: false, reason });
  assert.deepEqual(h.camera, before); h.tick(500); assert.deepEqual(h.camera, before);
  assert.equal(finishes, 1); assert.equal(h.pendingFrames, 0);
});

test('focus also checks the adapter binding every frame and resolves callback failures', async () => {
  const h = focusHarness(); let binding = true;
  const first = h.api.focus({ x: 800, y: 500 }, { isCurrent: () => binding });
  h.tick(80); binding = false; const before = { ...h.camera }; h.tick(160);
  assert.equal((await first).reason, 'stale'); assert.deepEqual(h.camera, before);
  const second = h.api.focus({ x: 100, y: 100 }, { onFrame: () => { throw new Error('synthetic adapter failure'); },
    onFinish: () => { throw new Error('synthetic finish failure'); } });
  h.tick(240); assert.equal((await second).reason, 'camera-error'); assert.equal(h.pendingFrames, 0);
  const third = h.api.focus({ x: 200, y: 200 }, { duration: 0 }); assert.equal((await third).ok, true);
});

test('hidden-page cancellation resolves without waiting for a background animation frame', async () => {
  const h = focusHarness(); const promise = h.api.focus({ x: 1300, y: 900 });
  h.tick(80); const before = { ...h.camera }; h.visibility(true);
  assert.deepEqual(plain(await promise), { ok: false, reason: 'hidden' });
  assert.equal(h.pendingFrames, 0); h.visibility(false); h.tick(500); assert.deepEqual(h.camera, before);
  h.api.destroy(); assert.equal((await h.api.focus({ x: 100, y: 200 })).reason, 'stale');
});

test('a newer focus started by a cancellation handler keeps its promise and camera ownership', async () => {
  const h = focusHarness(); let newest;
  const first = h.api.focus({ x: 1300, y: 900 }, { onFinish: result => {
    if (!result.ok) newest = h.api.focus({ x: -200, y: -500 });
  } });
  h.tick(80); const olderReplacement = h.api.focus({ x: 600, y: 400 });
  assert.equal((await first).reason, 'superseded'); assert.equal((await olderReplacement).reason, 'superseded');
  assert.equal(h.pendingFrames, 1); h.tick(400); assert.equal((await newest).ok, true);
  near(h.screen(-200, -500).x - h.box.left, 440); near(h.screen(-200, -500).y - h.box.top, 360);
});

test('default focus retains a tiny overview scale until enlargement is explicitly requested',async()=>{
  const h=focusHarness({zoom:0.12,maxZoom:4});
  const done=h.api.focus({x:1300,y:900},{duration:0,bounds:{minX:1250,maxX:1350,minY:870,maxY:930}});
  assert.equal((await done).ok,true);near(h.camera.zoom,0.12);
  near(h.screen(1300,900).x-h.box.left,440);near(h.screen(1300,900).y-h.box.top,360);
});

test('explicit preferred zoom enlarges smoothly to the precise readable target without moving world geometry',async()=>{
  const h=focusHarness({zoom:0.18,panX:37,panY:-19}),point={x:1300,y:900};
  const beforePoint={...point},compensation=plain(h.api.getTransform());
  const done=h.api.focus(point,{preferredZoom:0.85,bounds:{minX:1250,maxX:1350,minY:870,maxY:930}});
  h.tick(80);const early=h.camera.zoom;h.tick(160);const middle=h.camera.zoom;
  assert.ok(early>0.18&&middle>early&&middle<0.85,'enlargement follows the same continuous camera interpolation');
  h.tick(320);assert.equal((await done).ok,true);near(h.camera.zoom,0.85);
  near(h.screen(point.x,point.y).x-h.box.left,440);near(h.screen(point.x,point.y).y-h.box.top,360);
  assert.deepEqual(point,beforePoint);assert.deepEqual(plain(h.api.getTransform()),compensation);
});

test('preferred zoom respects command and graph caps and still shrinks an oversized target to fit',async()=>{
  for(const [settings,expected]of[[{preferredZoom:2,maxZoom:1.1},1.1],[{preferredZoom:2},1.2]]){
    const h=focusHarness({zoom:0.12,maxZoom:1.2});
    const done=h.api.focus({x:300,y:200},{...settings,duration:0,bounds:{minX:280,maxX:320,minY:180,maxY:220}});
    assert.equal((await done).ok,true);near(h.camera.zoom,expected);
  }
  const h=focusHarness({width:400,height:300,zoom:0.1});
  assert.equal((await h.api.focus({x:900,y:700},{preferredZoom:2,duration:0,
    bounds:{minX:600,maxX:1200,minY:600,maxY:800}})).ok,true);
  near(h.camera.zoom,352/600);near(h.screen(900,700).x-h.box.left,200);
});

test('invalid preferred zoom rejects without changing the active focus, camera or intent',async()=>{
  const h=focusHarness(),done=h.api.focus({x:1300,y:900});h.tick(80);
  const before={...h.camera},intent=h.api.getIntent();
  for(const preferredZoom of [0,-1,NaN,Infinity,'0.85']){
    assert.deepEqual(plain(await h.api.focus({x:1,y:2},{preferredZoom})),{ok:false,reason:'invalid-zoom'});
    assert.deepEqual(h.camera,before);assert.equal(h.api.getIntent(),intent);
  }
  assert.equal(h.pendingFrames,1);h.tick(320);assert.equal((await done).ok,true);
});
