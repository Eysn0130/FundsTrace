import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../static/minimap-navigation.js', import.meta.url), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));

// Run the real controller with bubbling events, native disabled clicks and focus.
function harness({ saved, appearance = {}, accentColor = '', computedStyleSupport = true, storageThrows = false, fitSucceeds = true,
  animationSupport = false, reducedMotion = false, slot, reentrant = false, ribbonState } = {}) {
  const nodes = new Map(), actions = [], changes = [], writes = [], storageReads = [], storageWrites = [], themeChanges = [];
  const animations = [], computedReads = [], layoutChanges = [], resizeObservers = [], mutationObservers = [];
  const space = slot ? { width: 800, height: 600, right: 20, bottom: 20, ...slot } : null;
  const state = { loading: false, busy: false, hasNodes: true, zoom: 1, status: 'viewport' };
  const stored = new Map([['fund_minimap_expanded', saved]]);
  let doc, presentation = { opacity: '1', transform: 'translateY(0) scale(1)' };
  class Element {
    constructor(id) {
      this.id = id; this.hidden = false; this.inert = false; this.disabled = false; this.tabIndex = 0;
      this.attributes = new Map(); this.listeners = new Map(); this.children = []; this.classes = new Set();
      this.textWrites = []; this.text = '';
      this.classList = { toggle: (name, enabled) => enabled ? this.classes.add(name) : this.classes.delete(name),
        contains: name => this.classes.has(name) };
    }
    get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
    set textContent(value) { this.text = String(value); this.children = []; this.textWrites.push(this.text); }
    appendChild(child) { child.parentElement = this; this.children.push(child); return child; }
    contains(child) { return child === this || this.children.some(node => node.contains(child)); }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    removeAttribute(name) { this.attributes.delete(name); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    addEventListener(type, listener) {
      const entries = this.listeners.get(type) || []; entries.push(listener); this.listeners.set(type, entries);
    }
    removeEventListener(type, listener) {
      this.listeners.set(type, (this.listeners.get(type) || []).filter(entry => entry !== listener));
    }
    focus() {
      let visible = !this.disabled;
      for (let node = this; node; node = node.parentElement) visible &&= !node.hidden && !node.inert;
      if (visible) doc.activeElement = this;
    }
    fire(type, properties = {}) {
      const event = { type, target: this, key: '', defaultPrevented: false, stopped: false,
        preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, ...properties };
      for (let node = this; node; node = node.parentElement) {
        event.currentTarget = node;
        for (const listener of [...node.listeners.get(type) || []]) listener(event);
        node['on' + type]?.(event);
        if (event.stopped) break;
      }
      return event;
    }
    click() { return this.disabled ? null : this.fire('click'); }
  }
  doc = new Element('document'); doc.documentElement = doc.appendChild(new Element('html'));
  doc.documentElement.dataset = { ftTheme: 'dark', ftAccent: 'blue', ...appearance };
  doc.body = doc.documentElement.appendChild(new Element('body')); doc.activeElement = doc.body;
  doc.getElementById = id => nodes.get(id) || null;
  const add = (id, parent = doc.body) => {
    const node = new Element(id); nodes.set(id, node); parent.appendChild(node); return node;
  };
  const ribbon = add('btnMinimap'), background = add('background');
  if (ribbonState) { ribbon.disabled = ribbonState.disabled; ribbon.setAttribute('aria-disabled', ribbonState.ariaDisabled); }
  const sidebarToggle = add('btnSidebarToggle');
  const host = space ? add('canvasWrap') : null;
  if (host) host.getBoundingClientRect = () => ({ left: 0, top: 0, width: space.width, height: space.height });
  const panel = add('graphMinimapPanel', host || doc.body), popover = add('graphMinimapPopover', panel);
  const canvas = add('graphMinimap', popover);
  const status = add('minimapStatus', popover), empty = add('minimapEmpty', popover), recovery = add('minimapRecovery', popover);
  const emptyIcon = add('minimapEmptyIcon', empty);
  const toggle = add('btnMinimapToggle', panel), zoomOut = add('btnMinimapZoomOut', panel);
  const value = add('minimapZoomValue', panel), zoomIn = add('btnMinimapZoomIn', panel), fit = add('btnMinimapFit', panel);
  const resetIcon = add('minimapResetIcon', value);
  const motion = new Element('motionPreference'); motion.matches = reducedMotion;
  motion.change = matches => { motion.matches = matches; return motion.fire('change', { matches }); };
  const view = new Element('window');
  view.ResizeObserver = class {
    constructor(callback) { this.callback = callback; this.targets = new Set(); this.disconnected = false; resizeObservers.push(this); }
    observe(target) { this.targets.add(target); }
    disconnect() { this.disconnected = true; this.targets.clear(); }
    deliver() { this.callback([...this.targets].map(target => ({ target }))); }
  };
  view.MutationObserver = class {
    constructor(callback) { this.callback = callback; this.disconnected = false; mutationObservers.push(this); }
    observe(target) { this.target = target; }
    disconnect() { this.disconnected = true; }
    deliver(records = [{ target: host, type: 'attributes', attributeName: 'style' }]) { this.callback(records); }
  };
  view.matchMedia = query => { assert.equal(query, '(prefers-reduced-motion: reduce)'); return motion; };
  if (computedStyleSupport) {
    view.getComputedStyle = node => {
      if (node === doc.documentElement) return { getPropertyValue: name => name === '--accent' ? accentColor : '' };
      if (node === panel && space) return { getPropertyValue: name => ({ '--mm-width': '272px', '--mm-map-height': '170px',
        '--mm-controls-height': '46px', '--mm-gap': '10px', right: `${space.right}px`, bottom: `${space.bottom}px` })[name] || '',
        right: `${space.right}px`, bottom: `${space.bottom}px` };
      assert.equal(node, popover); computedReads.push({ ...presentation }); return { ...presentation };
    };
  }
  doc.defaultView = view;
  if (animationSupport) popover.animate = (frames, timing) => {
    const animation = { frames: plain(frames), timing: plain(timing), cancelCount: 0, onfinish: null,
      cancel() { this.cancelCount++; },
      finish() { this.onfinish?.(); },
      present(frame) { presentation = { opacity: String(frame.opacity), transform: frame.transform }; },
    };
    presentation = { opacity: String(frames[0].opacity), transform: frames[0].transform };
    animations.push(animation); return animation;
  };
  const storage = {
    getItem(key) { storageReads.push(key); assert.equal(stored.has(key), true); if (storageThrows) throw Error('blocked'); return stored.get(key); },
    setItem(key, next) {
      assert.equal(stored.has(key), true); storageWrites.push([key, next]);
      writes.push(next);
      if (storageThrows) throw Error('blocked'); stored.set(key, next);
    },
  };
  const root = { document: doc };
  vm.runInContext(source, vm.createContext({ window: root }), { filename: 'minimap-navigation.js' });
  let previousRibbonCalls = 0;
  const previousRibbonClick = () => previousRibbonCalls++;
  ribbon.onclick = previousRibbonClick;
  let controller;
  controller = root.FundsMinimapNavigation.create({ document: doc, storage, getState: () => ({ ...state }),
    onZoom(direction) {
      actions.push(['zoom', direction]);
      state.zoom = direction === 'reset' ? 1 : Math.max(0.02, Math.min(4, state.zoom * (direction === 'in' ? 1.2 : 1 / 1.2)));
      return true;
    },
    onFit() {
      actions.push(['fit']);
      if (!fitSucceeds) return false;
      state.zoom = 0.8; state.status = 'viewport'; return true;
    },
    onPan(direction) { actions.push(['pan', direction]); return true; },
    onExpandedChange(next) { changes.push(next); if (reentrant) controller?.sync(); },
    onLayoutChange(next) { layoutChanges.push(plain(next)); if (reentrant) controller?.sync(); },
    onThemeChange(name, colors) { themeChanges.push({ name, colors }); },
  });
  return { controller, doc, panel, popover, canvas, status, empty, recovery, toggle, zoomOut, value, zoomIn, fit,
    ribbon, background, sidebarToggle, host, view, emptyIcon, resetIcon, motion, animations, computedReads, state, actions, changes,
    layoutChanges, resizeObservers, mutationObservers,
    writes, storageReads, storageWrites, themeChanges, previousRibbonClick, previousRibbonCalls: () => previousRibbonCalls,
    stored: () => stored.get('fund_minimap_expanded'),
    resizeSlot(next, signal = 'observer') {
      Object.assign(space, next);
      if (signal === 'passive') return;
      if (signal === 'mutation') mutationObservers.forEach(observer => observer.deliver());
      else if (signal === 'observer') resizeObservers.forEach(observer => observer.deliver());
      else view.fire(signal);
    },
    appear(next, detail = {}) {
      const { color, ...attributes } = next;
      Object.assign(doc.documentElement.dataset, attributes);
      if (color !== undefined) accentColor = color;
      return view.fire('funds-appearance-change', { detail });
    },
    setState(next) { Object.assign(state, next); return controller.sync(); } };
}

function assertZoom(h, percent) {
  const label = percent === null ? '缩放不可用' : `当前缩放 ${percent}，点击恢复 100%`;
  assert.equal(h.value.getAttribute('title'), label);
  assert.equal(h.value.getAttribute('aria-label'), label);
  assert.deepEqual(h.value.children, [h.resetIcon], 'sync preserves the reset icon');
  assert.deepEqual(h.value.textWrites, [], 'zoom percentages never replace the icon with text');
}

test('slot width retracts the whole panel without changing preference and restores it at the exact safe boundary', () => {
  const h = harness({ saved: '1', slot: { width: 298 }, reentrant: true });
  h.canvas.focus();
  assert.equal(h.panel.hidden, false); assert.equal(h.popover.hidden, false);
  h.resizeSlot({ width: 297 }, 'passive');
  assert.equal(h.controller.isNavigationAvailable(), false, 'live geometry revokes navigation before ResizeObserver delivery');
  assert.equal(h.panel.hidden, false, 'the navigation guard never mutates DOM');
  h.canvas.fire('keydown', { key: '+' }); h.canvas.fire('keydown', { key: 'ArrowRight' });
  assert.deepEqual(h.actions, [], 'late map keyboard input is also rejected before the observer delivers');
  h.resizeObservers.forEach(observer => observer.deliver());
  assert.equal(h.panel.hidden, true); assert.equal(h.panel.inert, true);
  assert.equal(h.panel.getAttribute('aria-hidden'), 'true'); assert.equal(h.popover.hidden, true);
  assert.equal(h.doc.activeElement, h.sidebarToggle);
  assert.match(h.ribbon.getAttribute('title'), /增大窗口或收起查询区/);
  h.ribbon.fire('click'); h.controller.setExpanded(false); h.zoomIn.fire('click');
  assert.deepEqual(h.writes, []); assert.deepEqual(h.actions, []); assert.equal(h.stored(), '1');
  h.resizeSlot({ width: 298 });
  assert.equal(h.panel.hidden, false); assert.equal(h.panel.inert, false); assert.equal(h.popover.hidden, false);
  assert.equal(h.controller.isNavigationAvailable(), true);
  assert.deepEqual(h.changes, [true, false, true]); assert.deepEqual(h.writes, []);
});

test('short slots retain the capsule and focused toggle, restore the preferred map and respect an explicit later collapse', () => {
  const h = harness({ saved: '1', slot: { height: 252 } });
  h.canvas.focus(); h.resizeSlot({ height: 251 });
  assert.equal(h.panel.hidden, false); assert.equal(h.popover.hidden, true); assert.equal(h.popover.inert, true);
  assert.equal(h.doc.activeElement, h.toggle); assert.equal(h.toggle.disabled, false);
  assert.equal(h.toggle.getAttribute('aria-disabled'), 'true'); assert.equal(h.ribbon.disabled, true);
  h.toggle.click(); h.controller.setExpanded(false); h.canvas.fire('keydown', { key: 'ArrowRight' });
  assert.deepEqual(h.writes, []); assert.deepEqual(h.actions, []);
  h.zoomIn.click(); assert.deepEqual(h.actions, [['zoom', 'in']], 'the visible capsule still controls the camera');
  h.resizeSlot({ height: 252 });
  assert.equal(h.popover.hidden, false); assert.equal(h.toggle.getAttribute('aria-disabled'), 'false');
  assert.equal(h.ribbon.disabled, false); assert.equal(h.toggle.getAttribute('title'), '收起缩略图');
  h.toggle.click(); assert.deepEqual(h.writes, ['0']);
  h.resizeSlot({ height: 200 }); h.resizeSlot({ height: 600 });
  assert.equal(h.popover.hidden, true); assert.deepEqual(h.writes, ['0']);
});

test('slots shorter than the controls hide the panel and recover without stealing focus or writing storage', () => {
  const h = harness({ saved: '1', slot: {} });
  h.canvas.focus(); h.resizeSlot({ height: 71 }, 'resize');
  assert.equal(h.panel.hidden, true); assert.equal(h.doc.activeElement, h.sidebarToggle);
  h.resizeSlot({ height: 72 }, 'fundstrace:workspace-resize');
  assert.equal(h.panel.hidden, false); assert.equal(h.popover.hidden, true);
  h.resizeSlot({ height: 252 });
  assert.equal(h.popover.hidden, false); assert.equal(h.doc.activeElement, h.sidebarToggle);
  assert.deepEqual(h.writes, []);
});

test('space follows computed right and transport bottom offsets and notifies layout changes without observer loops', () => {
  const h = harness({ saved: '1', slot: { width: 284, height: 304, right: 6, bottom: 72 }, reentrant: true });
  assert.equal(h.popover.hidden, false);
  h.resizeSlot({ bottom: 114 }, 'mutation');
  assert.equal(h.popover.hidden, true); assert.equal(h.panel.hidden, false);
  h.resizeSlot({ height: 346 }); assert.equal(h.popover.hidden, false);
  h.resizeSlot({ right: 7 }); assert.equal(h.panel.hidden, true);
  h.resizeSlot({ right: 6 }); assert.equal(h.panel.hidden, false);
  const notices = h.layoutChanges.length;
  h.controller.sync(); h.resizeObservers.forEach(observer => observer.deliver());
  assert.equal(h.layoutChanges.length, notices, 'unchanged layout and reentrant host sync do not notify again');
  assert.deepEqual(h.writes, []);
});

test('a missing layout API degrades to sufficient space and a saved collapsed map stays collapsed after recovery', () => {
  const fallback = harness({ saved: '1', slot: { width: 0, height: 0 }, computedStyleSupport: false });
  assert.equal(fallback.panel.hidden, false); assert.equal(fallback.popover.hidden, false);
  const h = harness({ saved: '0', slot: { width: 200, height: 30 } });
  assert.equal(h.panel.hidden, true);
  h.resizeSlot({ width: 800, height: 600 });
  assert.equal(h.panel.hidden, false); assert.equal(h.popover.hidden, true);
  assert.deepEqual(h.changes, [false]); assert.deepEqual(h.writes, []);
});

test('automatic retraction cancels in-flight animation and destroy releases observers and rejects late resize work', () => {
  const h = harness({ slot: {}, animationSupport: true });
  h.toggle.click(); const running = h.animations[0], stale = running.onfinish;
  h.resizeSlot({ height: 200 });
  assert.equal(h.popover.hidden, true); assert.equal(running.cancelCount, 1);
  stale(); assert.equal(h.popover.hidden, true); assert.equal(h.controller.isNavigationAvailable(), false);
  h.resizeSlot({ height: 600 }); assert.equal(h.popover.hidden, false);
  assert.equal(h.animations.length, 1, 'space recovery is immediate and does not replay disclosure animation');
  h.controller.destroy();
  assert.equal(h.resizeObservers.every(observer => observer.disconnected), true);
  assert.equal(h.mutationObservers.every(observer => observer.disconnected), true);
  h.resizeSlot({ height: 200 }); h.resizeSlot({ height: 600 }, 'resize'); stale();
  assert.equal(h.popover.hidden, true); assert.equal(h.controller.isNavigationAvailable(), false);
  assert.deepEqual(h.writes, ['1']); assert.equal([...h.view.listeners.values()].flat().length, 0);
  const exiting = harness({ saved: '1', slot: {}, animationSupport: true });
  exiting.toggle.click(); const exit = exiting.animations[0];
  exiting.resizeSlot({ height: 200 });
  assert.equal(exiting.popover.hidden, true, 'a manual exit is hard-settled when its map no longer fits');
  assert.equal(exit.cancelCount, 1); exit.finish(); assert.equal(exiting.popover.hidden, true);
});

test('map and ribbon disclose the same non-modal popover and preserve the capsule', () => {
  const h = harness();
  assert.equal(h.popover.hidden, true); assert.equal(h.panel.hidden, false);
  assert.equal(h.ribbon.getAttribute('aria-pressed'), 'false'); assert.deepEqual(h.changes, [false]);
  h.background.focus(); h.toggle.click();
  assert.equal(h.popover.hidden, false); assert.equal(h.doc.activeElement, h.background, 'opening never steals focus');
  assert.equal(h.panel.classList.contains('is-expanded'), true);
  assert.equal(h.toggle.getAttribute('aria-expanded'), 'true'); assert.equal(h.ribbon.getAttribute('aria-pressed'), 'true');
  assert.equal(h.toggle.getAttribute('title'), '收起缩略图');
  assert.equal(h.ribbon.getAttribute('aria-controls'), 'graphMinimapPopover');
  assert.equal(h.canvas.getAttribute('role'), 'region'); assert.equal(h.canvas.tabIndex, 0);
  assert.equal(h.canvas.getAttribute('aria-describedby'), 'minimapStatus minimapHelp');
  h.ribbon.click();
  assert.equal(h.popover.hidden, true); assert.equal(h.doc.activeElement, h.toggle);
  assert.equal(h.toggle.getAttribute('title'), '展开缩略图');
  h.ribbon.click(); assert.equal(h.popover.hidden, false);
  h.canvas.focus(); h.toggle.click();
  assert.equal(h.popover.hidden, true); assert.equal(h.doc.activeElement, h.toggle);
  assert.deepEqual(h.changes, [false, true, false, true, false]);
  assert.deepEqual(h.writes, ['1', '0', '1', '0']); assert.equal(h.previousRibbonCalls(), 0);
});

test('only the explicit stored preference opens on startup and storage failure keeps the local choice', () => {
  for (const saved of [undefined, '0', 'true', '', 'unexpected']) assert.equal(harness({ saved }).popover.hidden, true);
  const restored = harness({ saved: '1' });
  assert.equal(restored.popover.hidden, false); assert.deepEqual(restored.changes, [true]); assert.deepEqual(restored.writes, []);
  restored.controller.setExpanded(false); assert.equal(restored.stored(), '0');
  const failed = harness({ storageThrows: true });
  failed.controller.setExpanded(true); failed.controller.sync();
  assert.equal(failed.popover.hidden, false); assert.equal(failed.ribbon.getAttribute('aria-pressed'), 'true');
  failed.controller.setExpanded(false); assert.equal(failed.popover.hidden, true);
  assert.deepEqual(failed.changes, [false, true, false]);
});

test('capsule camera controls use live zoom and remain usable while the map is collapsed', () => {
  const h = harness();
  assertZoom(h, '100%');
  h.zoomIn.click(); assertZoom(h, '120%');
  h.setState({ zoom: 1.379 }); assertZoom(h, '138%');
  h.value.click(); assertZoom(h, '100%');
  h.zoomOut.click(); assertZoom(h, '83%');
  h.fit.click(); assertZoom(h, '80%');
  assert.deepEqual(h.actions, [['zoom', 'in'], ['zoom', 'reset'], ['zoom', 'out'], ['fit']]);
  assert.equal(h.popover.hidden, true);
  h.setState({ zoom: null });
  assertZoom(h, null); assert.equal(h.value.disabled, true);
  assert.equal(h.zoomIn.disabled, true); assert.equal(h.zoomOut.disabled, true); assert.equal(h.fit.disabled, false);
});

test('zoom limits block both click and keyboard zoom without blocking reset or fit', () => {
  const h = harness({ saved: '1' });
  h.setState({ zoom: 0.02 }); assertZoom(h, '2%');
  assert.equal(h.zoomOut.disabled, true); assert.equal(h.zoomIn.disabled, false);
  h.zoomOut.click(); h.canvas.fire('keydown', { key: '-' }); assert.deepEqual(h.actions, []);
  h.setState({ zoom: 4 }); assertZoom(h, '400%');
  assert.equal(h.zoomIn.disabled, true); assert.equal(h.zoomOut.disabled, false);
  h.zoomIn.click(); h.canvas.fire('keydown', { key: '+' }); assert.deepEqual(h.actions, []);
  h.canvas.fire('keydown', { key: '0' }); assertZoom(h, '100%');
  assert.deepEqual(h.actions, [['zoom', 'reset']]);
});

test('busy, loading and empty graph revoke all camera actions while disclosure stays available', () => {
  const h = harness({ saved: '1' });
  for (const blocked of [{ busy: true }, { busy: false, loading: true }, { loading: false, hasNodes: false }]) {
    h.setState(blocked);
    for (const button of [h.zoomOut, h.zoomIn, h.value, h.fit, h.recovery]) assert.equal(button.disabled, true);
    assert.equal(h.canvas.getAttribute('aria-disabled'), 'true'); assert.equal(h.canvas.tabIndex, -1);
    assert.equal(h.empty.hidden, false); assert.equal(h.recovery.hidden, true);
    assertZoom(h, h.state.busy && !h.state.loading && h.state.hasNodes ? '100%' : null);
    h.zoomIn.click(); h.value.click(); h.fit.click();
    h.canvas.fire('keydown', { key: 'ArrowRight' }); h.canvas.fire('keydown', { key: '0' });
    assert.deepEqual(h.actions, []);
    h.toggle.click(); assert.equal(h.popover.hidden, true);
    h.toggle.click(); assert.equal(h.popover.hidden, false);
    assert.equal(h.empty.getAttribute('aria-label'), blocked.hasNodes === false ? '当前范围暂无节点' : '正在更新画布…');
    assert.equal(h.empty.getAttribute('data-loading'), String(blocked.hasNodes !== false));
    assert.deepEqual(h.empty.children, [h.emptyIcon], 'empty-state updates preserve its icon');
    assert.deepEqual(h.empty.textWrites, []);
  }
  h.setState({ hasNodes: true }); assert.equal(h.empty.hidden, true);
  assert.equal(h.canvas.getAttribute('aria-disabled'), 'false'); assert.equal(h.zoomIn.disabled, false);
  h.zoomIn.click(); assert.deepEqual(h.actions, [['zoom', 'in']]);
});

test('every action rechecks current host state before a stale enabled control can navigate', () => {
  const h = harness({ saved: '1' });
  h.state.busy = true;
  h.zoomIn.click(); h.canvas.fire('keydown', { key: 'ArrowUp' });
  assert.deepEqual(h.actions, []); assert.equal(h.zoomIn.disabled, true);
  h.state.busy = false; h.controller.sync(); h.state.zoom = 4;
  h.zoomIn.click(); assert.deepEqual(h.actions, []);
});

test('outside viewport recovery follows actual map status and fits once', () => {
  const h = harness({ saved: '1' });
  assert.equal(h.recovery.hidden, true); assert.equal(h.status.textContent, '拖动视窗定位 · 方向键平移');
  h.setState({ status: 'outside' });
  assert.equal(h.recovery.hidden, false); assert.equal(h.status.textContent, '视窗已移出图形');
  h.recovery.focus();
  h.recovery.click();
  assert.deepEqual(h.actions, [['fit']]); assert.equal(h.recovery.hidden, true); assertZoom(h, '80%');
  assert.equal(h.doc.activeElement, h.canvas, 'successful recovery returns keyboard focus to the navigable map');
  h.setState({ status: 'outside', busy: true }); assert.equal(h.recovery.hidden, true);
  h.setState({ busy: false, hasNodes: false }); assert.equal(h.recovery.hidden, true);
});

test('failed outside recovery keeps focus on the visible recovery button', () => {
  const h = harness({ saved: '1', fitSucceeds: false });
  h.setState({ status: 'outside' }); h.recovery.focus(); h.recovery.click();
  assert.deepEqual(h.actions, [['fit']]); assert.equal(h.recovery.hidden, false);
  assert.equal(h.doc.activeElement, h.recovery);
});

test('canvas keyboard performs host camera actions and contains its shortcuts without trapping Tab', () => {
  const h = harness({ saved: '1' }); let leaked = 0;
  h.doc.addEventListener('keydown', () => leaked++);
  h.canvas.focus();
  for (const key of ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', '+', '-', '0']) {
    const event = h.canvas.fire('keydown', { key });
    assert.equal(event.defaultPrevented, true); assert.equal(event.stopped, true);
  }
  assert.deepEqual(h.actions, [['pan', 'up'], ['pan', 'down'], ['pan', 'left'], ['pan', 'right'],
    ['zoom', 'in'], ['zoom', 'out'], ['zoom', 'reset']]);
  assert.equal(leaked, 0);
  assert.equal(h.canvas.fire('keydown', { key: 'Tab' }).defaultPrevented, false);
  assert.equal(h.canvas.fire('keydown', { key: '+', ctrlKey: true }).defaultPrevented, false);
  assert.equal(h.canvas.fire('keydown', { key: '+', isComposing: true }).defaultPrevented, false);
  assert.equal(leaked, 3);
  const escape = h.canvas.fire('keydown', { key: 'Escape' });
  assert.equal(escape.defaultPrevented, true); assert.equal(leaked, 3);
  assert.equal(h.popover.hidden, true); assert.equal(h.doc.activeElement, h.toggle);
  const before = h.actions.length;
  h.canvas.fire('keydown', { key: 'ArrowUp' }); assert.equal(h.actions.length, before, 'a stale canvas event cannot pan after collapse');
  assert.equal(h.background.fire('keydown', { key: 'Escape' }).defaultPrevented, false);
});

test('Escape is local to the popover and a hidden focused descendant returns to the toggle', () => {
  const h = harness({ saved: '1' });
  h.background.focus(); h.background.fire('keydown', { key: 'Escape' }); assert.equal(h.popover.hidden, false);
  h.recovery.focus();
  h.recovery.fire('keydown', { key: 'Escape' });
  assert.equal(h.popover.hidden, true); assert.equal(h.doc.activeElement, h.toggle);
  h.controller.setExpanded(true, { focus: true }); assert.equal(h.doc.activeElement, h.canvas);
  h.controller.setExpanded(false); assert.equal(h.doc.activeElement, h.toggle);
  h.setState({ hasNodes: false });
  h.controller.setExpanded(true, { focus: true }); assert.equal(h.doc.activeElement, h.toggle);
});

test('keyboard expansion focuses the canvas when available and the bottom toggle for an empty or busy map', () => {
  for (const blocked of [{ hasNodes: false }, { busy: true }, { loading: true }]) {
    const h = harness();
    assert.equal(h.doc.getElementById('btnMinimapCollapse'), null, 'there is no top collapse control');
    h.setState(blocked); h.background.focus();
    h.controller.setExpanded(true, { focus: true });
    assert.equal(h.doc.activeElement?.id, h.toggle.id, 'unavailable geometry leaves keyboard focus on the usable bottom toggle');
    assert.equal(h.toggle.disabled, false); assert.equal(h.canvas.tabIndex, -1);
    assert.equal(h.popover.hidden, false); assert.equal(h.popover.inert, false);
    h.toggle.click();
    assert.equal(h.popover.hidden, true); assert.equal(h.doc.activeElement, h.toggle);
    h.setState({ hasNodes: true, busy: false, loading: false });
    h.controller.setExpanded(true, { focus: true });
    assert.equal(h.doc.activeElement, h.canvas); assert.equal(h.canvas.tabIndex, 0);
    h.canvas.fire('keydown', { key: 'Escape' });
    assert.equal(h.popover.hidden, true); assert.equal(h.doc.activeElement, h.toggle);
  }
});

test('destroy releases camera ownership and removes listeners without overwriting the saved preference', () => {
  const h = harness({ saved: '1' });
  h.controller.destroy(); h.controller.destroy();
  assert.equal(h.popover.hidden, true); assert.equal(h.canvas.getAttribute('aria-disabled'), 'true');
  assert.deepEqual(h.changes, [true, false]); assert.equal(h.stored(), '1');
  assert.equal(h.ribbon.onclick, h.previousRibbonClick);
  assert.equal(h.ribbon.disabled, false); assert.equal(h.ribbon.getAttribute('aria-disabled'), null);
  const previouslyDisabled = harness({ ribbonState: { disabled: true, ariaDisabled: 'true' } });
  previouslyDisabled.controller.destroy();
  assert.equal(previouslyDisabled.ribbon.disabled, true);
  assert.equal(previouslyDisabled.ribbon.getAttribute('aria-disabled'), 'true');
  h.toggle.click(); h.zoomIn.click(); h.canvas.fire('keydown', { key: 'ArrowRight' });
  assert.deepEqual(h.actions, []); assert.equal(h.controller.sync(), null);
  assert.equal(h.controller.setExpanded(true), false); assert.equal(h.popover.hidden, true);
  for (const node of [h.toggle, h.canvas, h.popover, h.zoomIn, h.zoomOut, h.value, h.fit, h.recovery, h.view, h.motion]) {
    assert.equal([...node.listeners.values()].flat().length, 0);
  }
});

function paletteSnapshots() {
  const reference = harness({ saved: '1', appearance: { ftTheme: 'light' } });
  reference.appear({ ftTheme: 'dark' }); reference.appear({ ftTheme: 'light', ftAccent: 'teal' });
  assert.deepEqual(reference.themeChanges.map(change => change.name), ['warm', 'graphite', 'mist']);
  const palettes = Object.fromEntries(reference.themeChanges.map(({ name, colors }) => [name, plain(colors)]));
  assert.equal(new Set(Object.values(palettes).map(colors => JSON.stringify(colors))).size, 3,
    'the three choices expose distinct palettes');
  for (const colors of Object.values(palettes)) {
    assert.deepEqual(Object.keys(colors).sort(), ['main', 'node', 'viewportFill', 'viewportStroke']);
    for (const color of Object.values(colors)) assert.equal(typeof color === 'string' && color.trim().length > 0, true);
  }
  reference.controller.destroy();
  return palettes;
}

test('startup follows the resolved appearance attributes without reading or writing a separate theme preference', () => {
  const palettes = paletteSnapshots();
  for (const [appearance, expected] of [
    [{}, 'graphite'], [{ ftTheme: 'dark', ftAccent: 'teal' }, 'graphite'],
    [{ ftTheme: 'light', ftAccent: 'blue' }, 'warm'], [{ ftTheme: 'light', ftAccent: 'teal' }, 'mist'],
    [{ ftTheme: 'light', ftAccent: 'violet' }, 'warm'], [{ ftTheme: 'unexpected' }, 'graphite'],
  ]) {
    const h = harness({ saved: '1', appearance, animationSupport: true });
    assert.equal(h.panel.getAttribute('data-theme'), expected);
    assert.equal(h.doc.getElementById('settingsTheme'), null);
    assert.deepEqual(h.themeChanges.map(change => change.name), [expected]);
    assert.deepEqual(plain(h.themeChanges[0].colors), palettes[expected]);
    h.controller.sync(); h.controller.setExpanded(true); h.setState({ zoom: 1.25 });
    assert.deepEqual(h.storageReads, ['fund_minimap_expanded']);
    assert.deepEqual(h.storageWrites, [], 'initialization, sync and an unchanged disclosure never write preferences');
    assert.equal(h.themeChanges.length, 1); assert.equal(h.animations.length, 0);
  }
});

test('appearance preview, cancel and resolved system changes follow the current DOM instead of saved settings', () => {
  const palettes = paletteSnapshots();
  const h = harness({ saved: '1' });
  h.appear({ ftTheme: 'light' }, { preview: true, preference: { theme: 'light' } });
  assert.equal(h.panel.getAttribute('data-theme'), 'warm');
  assert.deepEqual(plain(h.themeChanges.at(-1).colors), palettes.warm);
  h.appear({ ftTheme: 'dark' }, { preview: false, preference: { theme: 'dark' } });
  assert.equal(h.panel.getAttribute('data-theme'), 'graphite', 'cancel restores the saved appearance');
  h.appear({ ftTheme: 'light', ftAccent: 'teal' }, { preview: false, preference: { theme: 'system' } });
  assert.equal(h.panel.getAttribute('data-theme'), 'mist', 'system uses the already resolved light attribute');
  h.appear({ ftTheme: 'dark' }, { preview: false, preference: { theme: 'system' } });
  assert.equal(h.panel.getAttribute('data-theme'), 'graphite', 'a later system switch resolves to the dark palette');
  assert.deepEqual(h.themeChanges.map(change => change.name), ['graphite', 'warm', 'graphite', 'mist', 'graphite']);
  assert.deepEqual(h.storageWrites, []);
});

test('appearance changes update a collapsed or busy overview without opening it or persisting settings', () => {
  const h = harness({ animationSupport: true });
  h.appear({ ftTheme: 'light', ftAccent: 'teal' }, { preview: true });
  assert.equal(h.panel.getAttribute('data-theme'), 'mist');
  assert.equal(h.popover.hidden, true); assert.equal(h.popover.inert, true);
  assert.deepEqual(h.themeChanges.map(change => change.name), ['graphite', 'mist']);
  assert.deepEqual(h.storageWrites, []);
  assert.deepEqual(h.changes, [false]); assert.deepEqual(h.actions, []); assert.deepEqual(h.animations, []);
  h.setState({ busy: true, hasNodes: false }); h.appear({ ftTheme: 'light', ftAccent: 'blue' });
  assert.equal(h.panel.getAttribute('data-theme'), 'warm'); assert.equal(h.popover.hidden, true);
  assert.deepEqual(h.writes, []); assert.deepEqual(h.animations, []);
  h.toggle.click();
  assert.deepEqual(h.storageWrites, [['fund_minimap_expanded', '1']], 'only disclosure writes its own preference');
  h.controller.destroy(); const count = h.themeChanges.length;
  h.appear({ ftTheme: 'dark' });
  assert.equal(h.panel.getAttribute('data-theme'), 'warm', 'an appearance event after destroy cannot update the old overview');
  assert.equal(h.themeChanges.length, count);
  assert.deepEqual(h.storageWrites, [['fund_minimap_expanded', '1']]);
  assert.equal([...h.view.listeners.values()].flat().length, 0);
});

test('accent changes update the viewport stroke within one palette and callback edits stay isolated', () => {
  const palettes = paletteSnapshots();
  const h = harness({ appearance: { ftTheme: 'light' }, accentColor: '#367cff' });
  assert.equal(h.themeChanges[0].name, 'warm');
  assert.deepEqual(plain(h.themeChanges[0].colors), { ...palettes.warm, viewportStroke: '#367cff' });
  const first = h.themeChanges[0].colors;
  first.node = '#callback-only';
  h.appear({ ftAccent: 'violet', color: ' #8b5cf6 ' }, { preview: true });
  assert.equal(h.panel.getAttribute('data-theme'), 'warm');
  assert.deepEqual(plain(h.themeChanges.at(-1).colors), { ...palettes.warm, viewportStroke: '#8b5cf6' });
  assert.notEqual(h.themeChanges.at(-1).colors, first);
  assert.equal(first.viewportStroke, '#367cff', 'the previous callback palette remains a snapshot');
  h.appear({ ftAccent: 'violet', color: '#8b5cf6' }); h.controller.sync();
  assert.equal(h.themeChanges.length, 2, 'an unchanged effective palette does not repeat its callback');
  h.appear({ ftAccent: 'blue', color: '#367cff' }, { preview: false });
  assert.deepEqual(plain(h.themeChanges.at(-1).colors), { ...palettes.warm, viewportStroke: '#367cff' });
  assert.deepEqual(h.storageWrites, []);
});

test('missing computed styles use each palette default without blocking appearance updates', () => {
  const palettes = paletteSnapshots();
  const h = harness({ appearance: { ftTheme: 'light' }, computedStyleSupport: false });
  assert.deepEqual(plain(h.themeChanges[0].colors), palettes.warm);
  h.appear({ ftAccent: 'teal' });
  assert.deepEqual(plain(h.themeChanges.at(-1).colors), palettes.mist);
  const unavailable = harness({ appearance: { ftTheme: 'light' }, storageThrows: true });
  unavailable.appear({ ftTheme: 'dark' });
  assert.equal(unavailable.panel.getAttribute('data-theme'), 'graphite');
  assert.deepEqual(unavailable.storageWrites, []);
});

test('stored disclosure is settled on startup without an entrance or exit animation', () => {
  for (const saved of [undefined, '0', '1']) {
    const h = harness({ saved, animationSupport: true });
    const expanded = saved === '1';
    assert.equal(h.popover.hidden, !expanded); assert.equal(h.popover.inert, !expanded);
    assert.equal(h.popover.getAttribute('aria-hidden'), String(!expanded));
    assert.equal(h.canvas.tabIndex, expanded ? 0 : -1);
    assert.deepEqual(h.animations, []); assert.deepEqual(h.storageWrites, []);
    h.controller.sync(); h.controller.setExpanded(expanded);
    assert.deepEqual(h.animations, [], 'a routine state refresh never starts disclosure animation');
  }
});

test('exit becomes inert immediately, waits 160ms visually and leaves capsule camera controls available', () => {
  const h = harness({ saved: '1', animationSupport: true });
  h.setState({ status: 'outside' }); h.recovery.focus(); h.toggle.click();
  assert.equal(h.animations.length, 1);
  const exit = h.animations[0];
  assert.deepEqual(exit.frames, [{ opacity: 1, transform: 'translateY(0) scale(1)' },
    { opacity: 0, transform: 'translateY(8px) scale(.985)' }]);
  assert.deepEqual(exit.timing, { duration: 160, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'both' });
  assert.equal(h.popover.hidden, false, 'the exiting surface remains drawable until animation completion');
  assert.equal(h.popover.inert, true); assert.equal(h.popover.getAttribute('aria-hidden'), 'true');
  assert.equal(h.canvas.tabIndex, -1); assert.equal(h.canvas.getAttribute('aria-disabled'), 'true');
  assert.equal(h.panel.getAttribute('data-expanded'), 'false'); assert.equal(h.doc.activeElement, h.toggle);
  h.recovery.fire('click'); h.canvas.fire('keydown', { key: 'ArrowRight' });
  assert.deepEqual(h.actions, [], 'stale map events cannot recover or pan during exit');
  assert.equal(h.themeChanges.length, 1);
  h.zoomIn.click(); h.fit.click(); assertZoom(h, '80%');
  assert.deepEqual(h.actions, [['zoom', 'in'], ['fit']]);
  assert.equal(h.panel.hidden, false); assert.equal(h.animations.length, 1, 'camera sync does not restart exit');
  exit.finish();
  assert.equal(exit.cancelCount, 1); assert.equal(h.popover.hidden, true); assert.equal(h.popover.inert, true);
  h.toggle.click();
  const entrance = h.animations[1];
  assert.equal(entrance.timing.duration, 220);
  assert.deepEqual(entrance.frames, [exit.frames[1], exit.frames[0]]);
  assert.equal(h.popover.hidden, false); assert.equal(h.popover.inert, false); assert.equal(h.canvas.tabIndex, 0);
  entrance.finish(); assert.equal(h.popover.hidden, false); assert.equal(entrance.cancelCount, 1);
});

test('rapid reversals continue from the computed in-flight frame and reject obsolete completion callbacks', () => {
  const h = harness({ animationSupport: true });
  h.toggle.click();
  const entrance = h.animations[0], oldEntranceFinish = entrance.onfinish;
  const openingFrame = { opacity: '0.65', transform: 'matrix(0.9895, 0, 0, 0.9895, 0, 3.5)' };
  entrance.present(openingFrame); h.toggle.click();
  const exit = h.animations[1], oldExitFinish = exit.onfinish;
  assert.equal(entrance.cancelCount, 1); assert.deepEqual(exit.frames[0], openingFrame);
  assert.equal(exit.timing.duration, 104);
  assert.deepEqual(h.computedReads, [openingFrame]);
  oldEntranceFinish();
  assert.equal(exit.cancelCount, 0); assert.equal(h.popover.hidden, false, 'late entrance completion cannot truncate the current exit');
  assert.equal(h.popover.inert, true);
  const closingFrame = { opacity: '0.4', transform: 'matrix(0.982, 0, 0, 0.982, 0, 6)' };
  exit.present(closingFrame); h.toggle.click();
  const reopened = h.animations[2];
  assert.equal(exit.cancelCount, 1); assert.deepEqual(reopened.frames[0], closingFrame);
  assert.equal(reopened.timing.duration, 132);
  assert.deepEqual(h.computedReads, [openingFrame, closingFrame]);
  oldExitFinish(); oldEntranceFinish();
  assert.equal(reopened.cancelCount, 0, 'old completions cannot cancel the replacement animation');
  assert.equal(h.popover.hidden, false); assert.equal(h.popover.inert, false);
  assert.equal(h.popover.getAttribute('aria-hidden'), 'false'); assert.equal(h.canvas.tabIndex, 0);
  h.controller.sync(); h.setState({ zoom: 1.6, status: 'outside' });
  assert.equal(h.animations.length, 3); assert.equal(reopened.cancelCount, 0, 'sync does not restart the current animation');
  reopened.finish(); oldExitFinish();
  assert.equal(h.popover.hidden, false); assert.equal(h.panel.getAttribute('data-expanded'), 'true');
});

for (const opacity of [0, 0.01, 0.02, 0.98, 0.99, 1]) {
  test(`near-complete reversal at opacity ${opacity} settles without another animation`, () => {
    const expanded = opacity >= 0.98;
    const h = harness({ saved: expanded ? '1' : undefined, animationSupport: true });
    h.controller.setExpanded(!expanded, { focus: true });
    const running = h.animations[0], stale = running.onfinish;
    running.present({ opacity, transform: expanded ? 'translateY(0) scale(1)' : 'translateY(8px) scale(.985)' });
    h.controller.setExpanded(expanded, { focus: true });
    assert.equal(h.animations.length, 1, 'an opacity distance at most 0.02 creates no replacement animation');
    assert.equal(running.cancelCount, 1);
    assert.equal(h.popover.hidden, !expanded); assert.equal(h.popover.inert, !expanded);
    assert.equal(h.popover.getAttribute('aria-hidden'), String(!expanded));
    assert.equal(h.panel.getAttribute('data-expanded'), String(expanded));
    assert.equal(h.toggle.getAttribute('aria-expanded'), String(expanded));
    assert.equal(h.canvas.tabIndex, expanded ? 0 : -1);
    assert.equal(h.doc.activeElement?.id, expanded ? h.canvas.id : h.toggle.id);
    stale(); h.controller.sync();
    assert.equal(h.popover.hidden, !expanded); assert.equal(h.animations.length, 1);
    assert.equal(running.cancelCount, 1, 'an obsolete finish cannot settle the already completed reversal again');
    h.controller.setExpanded(!expanded);
    const replacement = h.animations[1];
    assert.equal(replacement.timing.duration, expanded ? 160 : 220);
    stale();
    assert.equal(replacement.cancelCount, 0, 'the old callback cannot cancel a later full transition');
    assert.equal(h.popover.hidden, false); assert.equal(h.popover.inert, expanded);
    assert.equal(h.popover.getAttribute('aria-hidden'), String(expanded));
    replacement.finish(); stale();
    assert.equal(h.popover.hidden, expanded); assert.equal(h.popover.inert, expanded);
  });
}

test('reduced motion settles initially and changing it mid-animation settles to the current semantic state', () => {
  const h = harness({ saved: '1', animationSupport: true, reducedMotion: true });
  h.controller.setExpanded(false); assert.equal(h.popover.hidden, true);
  h.controller.setExpanded(true); assert.equal(h.popover.hidden, false);
  assert.equal(h.animations.length, 0, 'an initial reduced-motion preference skips every disclosure transition');
  h.motion.change(false); assert.equal(h.animations.length, 0);
  h.controller.setExpanded(false);
  const exit = h.animations[0], staleExit = exit.onfinish;
  assert.equal(h.popover.hidden, false);
  h.motion.change(true);
  assert.equal(exit.cancelCount, 1); assert.equal(h.popover.hidden, true); assert.equal(h.popover.inert, true);
  h.motion.change(false); h.controller.setExpanded(true);
  const entrance = h.animations[1], staleEntrance = entrance.onfinish;
  staleExit(); assert.equal(entrance.cancelCount, 0); assert.equal(h.popover.hidden, false);
  entrance.present({ opacity: '0.2', transform: 'translateY(8px) scale(.976)' });
  h.motion.change(true);
  assert.equal(entrance.cancelCount, 1); assert.equal(h.popover.hidden, false); assert.equal(h.popover.inert, false);
  staleEntrance(); staleExit();
  assert.equal(h.popover.hidden, false); assert.equal(h.animations.length, 2);
  h.controller.setExpanded(false); assert.equal(h.popover.hidden, true); assert.equal(h.animations.length, 2);
});

test('destroy hard-closes an in-flight entrance and late animation or media events cannot reopen it', () => {
  const h = harness({ animationSupport: true });
  h.toggle.click(); const running = h.animations[0], stale = running.onfinish;
  h.controller.destroy(); h.controller.destroy();
  assert.equal(running.cancelCount, 1); assert.equal(h.popover.hidden, true); assert.equal(h.popover.inert, true);
  assert.equal(h.popover.getAttribute('aria-hidden'), 'true'); assert.equal(h.canvas.tabIndex, -1);
  assert.deepEqual(h.changes, [false, true, false]);
  assert.deepEqual(h.writes, ['1']); assert.equal(h.stored(), '1', 'destroy does not overwrite the saved expanded preference');
  stale(); h.motion.change(true); h.motion.change(false); h.toggle.click(); h.appear({ ftTheme: 'light', ftAccent: 'teal' });
  h.canvas.fire('keydown', { key: 'ArrowUp' });
  assert.equal(h.popover.hidden, true); assert.equal(h.animations.length, 1); assert.equal(running.cancelCount, 1);
  assert.deepEqual(h.actions, []); assert.equal(h.themeChanges.length, 1);
  assert.equal(h.ribbon.onclick, h.previousRibbonClick);
  assert.equal([...h.motion.listeners.values()].flat().length, 0);
  assert.equal([...h.view.listeners.values()].flat().length, 0);
});
