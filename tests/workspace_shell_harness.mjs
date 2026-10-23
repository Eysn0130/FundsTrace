import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/workspace-shell.js', import.meta.url), 'utf8');

function harness(width = 1440, preferences = {}, height = 800) {
  const nodes = new Map();
  const events = [];
  let doc;
  function node(id) {
    const listeners = new Map(), classes = new Set(), attributes = new Map(), properties = new Map(), captured = new Set();
    const item = {
      id, width: id === 'left' ? 560 : width, height, inert: false, tabIndex: 0, title: '', textContent: '',
      style: { setProperty(key, value) { properties.set(key, value); }, getPropertyValue(key) { return properties.get(key) || ''; } },
      classList: { contains: name => classes.has(name), add: name => classes.add(name), remove: name => classes.delete(name), toggle(name, value) { if (value) classes.add(name); else classes.delete(name); } },
      addEventListener(name, fn) { const list = listeners.get(name) || []; list.push(fn); listeners.set(name, list); },
      setAttribute: (key, value) => attributes.set(key, value),
      getAttribute: key => attributes.get(key),
      querySelector() { return this.image; },
      contains(other) { return other === this || other?.parent === this; },
      focus() { doc.activeElement = this; },
      getBoundingClientRect() { return { width: this.width, height: this.height, left: 300, bottom: 110 }; },
      setPointerCapture(id) { captured.add(id); },
      hasPointerCapture(id) { return captured.has(id); },
      releasePointerCapture(id) { captured.delete(id); },
      fire(name, detail = {}) { const event = { target: this, preventDefault() { this.prevented = true; }, ...detail }; for (const fn of listeners.get(name) || []) fn(event); return event; },
      image: { src: '' },
    };
    nodes.set(id, item); return item;
  }
  for (const id of ['workspace','left','editorToolbar','btnSidebarToggle','btnToolbarToggle','workspaceSidebarResize','btnDisplayActions','toolbarDisplayExtra','btnMainActions','toolbarMainActions','graphToolbar','body','btnWorkspaceView','workspaceViewCurrent','workspaceViewStandard','workspaceViewFocus','workspaceViewSplit','detailPanel','transactionTable']) node(id);
  nodes.get('transactionTable').parent = nodes.get('left');
  const sidebar = nodes.get('left');
  sidebar.getBoundingClientRect = () => {
    const workspace = nodes.get('workspace');
    const preferred = Number.parseInt(workspace.style.getPropertyValue('--sidebar-width')) || (window.innerWidth <= 700 ? window.innerWidth - 44 : 560);
    const maximum = Number.parseInt(workspace.style.getPropertyValue('--sidebar-maximum-width')) || Infinity;
    const collapsed = workspace.classList.contains('sidebar-collapsed');
    const split = workspace.getAttribute('data-workspace-view') === 'split';
    return {
      width: collapsed ? 0 : split ? workspace.width : Math.min(preferred, maximum),
      height: collapsed ? 0 : split ? Number.parseInt(workspace.style.getPropertyValue('--workspace-bottom-height')) : workspace.height,
    };
  };
  const documentEvents = node('document');
  doc = { readyState: 'complete', body: nodes.get('body'), activeElement: null, getElementById: id => nodes.get(id), addEventListener: documentEvents.addEventListener.bind(documentEvents) };
  const windowEvents = node('window');
  const window = { document: doc, innerWidth: width, innerHeight: height, CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } }, dispatchEvent(event) { events.push(event); }, addEventListener: windowEvents.addEventListener.bind(windowEvents) };
  Object.defineProperties(window, Object.getOwnPropertyDescriptors(preferences));
  vm.runInNewContext(source, { window });
  return { nodes, events, doc, shell: nodes.get('workspace').fundsWorkspaceShell, documentEvents, windowEvents, window };
}

function storage(initial = {}) {
  const values = new Map(Object.entries(initial)), writes = [];
  return {
    writes,
    getItem: key => values.get(key) ?? null,
    setItem(key, value) { values.set(key, value); writes.push([key, value]); },
  };
}

test('sidebar and toolbar collapse only on click and remain independent', () => {
  const h = harness();
  h.nodes.get('left').fire('mouseenter');
  h.nodes.get('editorToolbar').fire('mouseenter');
  assert.equal(h.shell.getState().sidebarCollapsed, false);
  assert.equal(h.shell.getState().toolbarCollapsed, false);
  h.nodes.get('btnSidebarToggle').fire('click');
  assert.equal(h.shell.getState().sidebarCollapsed, true);
  assert.equal(h.shell.getState().toolbarCollapsed, false);
  assert.equal(h.nodes.get('left').inert, true);
  assert.equal(h.nodes.get('btnSidebarToggle').getAttribute('aria-expanded'), 'false');
  assert.equal(h.nodes.get('workspaceSidebarResize').tabIndex, -1);
  h.nodes.get('btnToolbarToggle').fire('click');
  assert.equal(h.shell.getState().toolbarCollapsed, true);
  assert.equal(h.nodes.get('btnToolbarToggle').getAttribute('aria-expanded'), 'false');
  h.nodes.get('btnSidebarToggle').fire('click');
  assert.equal(h.shell.getState().sidebarCollapsed, false);
  assert.equal(h.shell.getState().toolbarCollapsed, true);
  assert.equal(h.nodes.get('left').inert, false);
});

test('collapsing a focused sidebar preserves focus on its external toggle', () => {
  const h = harness();
  h.doc.activeElement = { parent: h.nodes.get('left') };
  h.shell.setSidebarCollapsed(true);
  assert.equal(h.doc.activeElement, h.nodes.get('btnSidebarToggle'));
  assert.match(h.nodes.get('btnSidebarToggle').image.src, /chevron-right\.svg$/);
});

test('sidebar preparation precedes geometry changes and reduced motion completes in the same action', () => {
  const h = harness(), phases = [], dispatch = h.window.dispatchEvent;
  h.window.matchMedia = () => ({ matches: true });
  h.window.dispatchEvent = event => {
    phases.push({ phase: event.detail.phase, target: event.detail.sidebarCollapsed,
      collapsed: h.nodes.get('workspace').classList.contains('sidebar-collapsed') });
    dispatch(event);
  };
  h.shell.setSidebarCollapsed(true);
  assert.deepEqual(phases, [
    { phase: 'prepare', target: true, collapsed: false },
    { phase: 'start', target: true, collapsed: true },
    { phase: 'end', target: true, collapsed: true },
  ]);
});

test('pointer and keyboard resizing clamp width and preserve it across collapse', () => {
  const h = harness();
  const handle = h.nodes.get('workspaceSidebarResize');
  handle.fire('pointerdown', { button: 0, pointerId: 1, clientX: 560 });
  handle.fire('pointermove', { pointerId: 1, clientX: 1500 });
  assert.equal(h.nodes.get('workspace').style.getPropertyValue('--sidebar-width'), '760px');
  handle.fire('pointerup', { pointerId: 1 });
  assert.equal(h.nodes.get('workspace').classList.contains('is-resizing'), false);
  const key = handle.fire('keydown', { key: 'ArrowLeft', shiftKey: true });
  assert.equal(key.prevented, true);
  assert.equal(handle.getAttribute('aria-valuenow'), '710');
  h.shell.setSidebarCollapsed(true); h.shell.setSidebarCollapsed(false);
  assert.equal(h.nodes.get('workspace').style.getPropertyValue('--sidebar-width'), '710px');
  assert.ok(h.events.every(event => event.type === 'fundstrace:workspace-resize'));
  assert.equal(h.events.at(-1).detail.reason, 'sidebar');
});

test('collapsing during a resize ends capture so the collapse can animate and late moves cannot change width', () => {
  const h = harness();
  const workspace = h.nodes.get('workspace'), handle = h.nodes.get('workspaceSidebarResize');
  const captured = new Set();
  handle.setPointerCapture = id => captured.add(id);
  handle.hasPointerCapture = id => captured.has(id);
  handle.releasePointerCapture = id => captured.delete(id);
  handle.fire('pointerdown', { button: 0, pointerId: 1, clientX: 560 });
  handle.fire('pointermove', { pointerId: 1, clientX: 600 });
  assert.equal(workspace.style.getPropertyValue('--sidebar-width'), '600px');
  h.shell.setSidebarCollapsed(true);
  assert.equal(captured.size, 0);
  assert.equal(workspace.classList.contains('is-resizing'), false);
  assert.equal(h.doc.body.classList.contains('workspace-resizing'), false);
  handle.fire('pointermove', { pointerId: 1, clientX: 720 });
  handle.fire('pointerup', { pointerId: 1 });
  h.shell.setSidebarCollapsed(false);
  assert.equal(workspace.style.getPropertyValue('--sidebar-width'), '600px');
  assert.equal(h.events.filter(event => event.detail.reason === 'sidebar-width' && event.detail.phase === 'end').length, 0);
});

test('a second pointer cannot take over an active sidebar resize', () => {
  const h = harness();
  const handle = h.nodes.get('workspaceSidebarResize');
  handle.fire('pointerdown', { button: 0, pointerId: 1, clientX: 560 });
  handle.fire('pointerdown', { button: 0, pointerId: 2, clientX: 1000, isPrimary: false });
  handle.fire('pointermove', { pointerId: 2, clientX: 1100 });
  assert.equal(h.nodes.get('workspace').style.getPropertyValue('--sidebar-width'), '');
  handle.fire('pointermove', { pointerId: 1, clientX: 600 });
  assert.equal(h.nodes.get('workspace').style.getPropertyValue('--sidebar-width'), '600px');
});

test('narrow windows retain a readable sidebar width and a canvas edge', () => {
  const h = harness(390);
  const handle = h.nodes.get('workspaceSidebarResize');
  handle.fire('keydown', { key: 'ArrowRight' });
  assert.equal(h.nodes.get('workspace').style.getPropertyValue('--sidebar-width'), '346px');
  assert.equal(handle.getAttribute('aria-valuemin'), '346');
  assert.equal(handle.getAttribute('aria-valuemax'), '346');
});

test('expanding after a collapsed window resize clamps the actual width and restores the preferred width later', () => {
  const h = harness();
  const handle = h.nodes.get('workspaceSidebarResize');
  const workspace = h.nodes.get('workspace');
  handle.fire('pointerdown', { button: 0, pointerId: 1, clientX: 560 });
  handle.fire('pointermove', { pointerId: 1, clientX: 663 });
  handle.fire('pointerup', { pointerId: 1 });
  assert.equal(h.nodes.get('left').getBoundingClientRect().width, 663);
  h.shell.setSidebarCollapsed(true);
  workspace.width = h.window.innerWidth = 599;
  h.windowEvents.fire('resize');
  h.shell.setSidebarCollapsed(false);
  assert.equal(h.nodes.get('left').getBoundingClientRect().width, 399);
  assert.equal(workspace.style.getPropertyValue('--sidebar-width'), '663px');
  assert.equal(handle.getAttribute('aria-valuenow'), '399');
  assert.equal(handle.getAttribute('aria-valuemax'), '399');
  assert.equal(workspace.width - h.nodes.get('left').getBoundingClientRect().width, 200);
  workspace.width = h.window.innerWidth = 1440;
  h.windowEvents.fire('resize');
  assert.equal(h.nodes.get('left').getBoundingClientRect().width, 663);
  assert.equal(handle.getAttribute('aria-valuenow'), '663');
});

test('unmodified sidebar width keeps CSS responsive defaults while respecting available space', () => {
  const h = harness();
  const workspace = h.nodes.get('workspace');
  workspace.width = h.window.innerWidth = 599;
  h.windowEvents.fire('resize');
  assert.equal(h.nodes.get('left').getBoundingClientRect().width, 399);
  assert.equal(workspace.style.getPropertyValue('--sidebar-width'), '');
  workspace.width = h.window.innerWidth = 1440;
  h.windowEvents.fire('resize');
  assert.equal(h.nodes.get('left').getBoundingClientRect().width, 560);
});

test('desktop sidebar preference survives a new mount and writes only after user resizing', () => {
  const desktop = storage(), browser = storage({ fund_sidebar_width: '740' });
  const h = harness(1440, { FundsDesktopPreferences: desktop, localStorage: browser });
  const handle = h.nodes.get('workspaceSidebarResize');
  assert.equal(h.nodes.get('workspace').style.getPropertyValue('--sidebar-width'), '');
  handle.fire('pointerdown', { button: 0, pointerId: 1, clientX: 560 });
  handle.fire('pointermove', { pointerId: 1, clientX: 650 });
  handle.fire('pointermove', { pointerId: 1, clientX: 663 });
  assert.equal(desktop.writes.length, 0);
  handle.fire('pointerup', { pointerId: 1 });
  handle.fire('lostpointercapture', { pointerId: 1 });
  assert.deepEqual(desktop.writes, [['fund_sidebar_width', '663']]);
  assert.equal(browser.writes.length, 0);
  const reopened = harness(1440, { FundsDesktopPreferences: desktop });
  assert.equal(reopened.nodes.get('left').getBoundingClientRect().width, 663);
  assert.equal(reopened.nodes.get('workspaceSidebarResize').getAttribute('aria-valuenow'), '663');
  assert.equal(desktop.writes.length, 1);
  reopened.nodes.get('workspaceSidebarResize').fire('keydown', { key: 'ArrowLeft' });
  assert.deepEqual(desktop.writes.at(-1), ['fund_sidebar_width', '643']);
});

test('restoring on a narrow viewport preserves the preferred width when it grows again', () => {
  const preferences = storage({ fund_sidebar_width: '663' });
  const h = harness(599, { localStorage: preferences });
  const workspace = h.nodes.get('workspace'), handle = h.nodes.get('workspaceSidebarResize');
  assert.equal(h.nodes.get('left').getBoundingClientRect().width, 399);
  assert.equal(handle.getAttribute('aria-valuenow'), '399');
  workspace.width = h.window.innerWidth = 1440;
  h.windowEvents.fire('resize');
  assert.equal(h.nodes.get('left').getBoundingClientRect().width, 663);
  assert.equal(preferences.getItem('fund_sidebar_width'), '663');
  assert.equal(preferences.writes.length, 0);
});

test('a width chosen in a tiny viewport stays readable when restored on a larger screen', () => {
  const preferences = storage({ fund_sidebar_width: '346' });
  const h = harness(1440, { localStorage: preferences });
  assert.equal(h.nodes.get('left').getBoundingClientRect().width, 360);
  h.nodes.get('workspace').width = h.window.innerWidth = 390;
  h.windowEvents.fire('resize');
  assert.equal(h.nodes.get('left').getBoundingClientRect().width, 346);
  assert.equal(preferences.getItem('fund_sidebar_width'), '346');
  assert.equal(preferences.writes.length, 0);
});

test('invalid saved widths and unavailable storage leave resizing usable', () => {
  for (const width of ['-1', '761', 'NaN', '600px', '4e2', '']) {
    const h = harness(1440, { localStorage: storage({ fund_sidebar_width: width }) });
    assert.equal(h.nodes.get('workspace').style.getPropertyValue('--sidebar-width'), '');
  }
  const blocked = harness(1440, { get localStorage() { throw new Error('denied'); } });
  assert.doesNotThrow(() => blocked.nodes.get('workspaceSidebarResize').fire('keydown', { key: 'ArrowRight' }));
  assert.equal(blocked.nodes.get('left').getBoundingClientRect().width, 580);
  const failing = harness(1440, { FundsDesktopPreferences: {
    getItem() { throw new Error('unavailable'); },
    setItem() { throw new Error('unavailable'); },
  } });
  assert.doesNotThrow(() => failing.nodes.get('workspaceSidebarResize').fire('keydown', { key: 'ArrowRight' }));
  assert.equal(failing.nodes.get('left').getBoundingClientRect().width, 580);
});

test('mount is idempotent and transition receipts contain chrome state only', () => {
  const h = harness();
  assert.equal(h.window.FundsWorkspaceShell.mount(), h.shell);
  h.nodes.get('btnToolbarToggle').fire('click');
  assert.equal(h.events.length, 1);
  h.nodes.get('editorToolbar').fire('transitionend', { propertyName: 'height' });
  assert.equal(h.events.at(-1).detail.phase, 'end');
  assert.deepEqual(Object.keys(h.events.at(-1).detail).sort(), ['phase','reason','sidebarCollapsed','toolbarCollapsed','view']);
});

test('workspace modes keep independent session collapse choices and focus returns to the last non-focus mode', () => {
  const h = harness(), workspace = h.nodes.get('workspace');
  assert.deepEqual({ ...h.shell.getState() }, { view: 'standard', sidebarCollapsed: false, toolbarCollapsed: false });
  assert.equal(workspace.getAttribute('data-workspace-view'), 'standard');
  h.shell.setSidebarCollapsed(true);
  h.shell.setView('focus');
  assert.deepEqual({ ...h.shell.getState() }, { view: 'focus', sidebarCollapsed: true, toolbarCollapsed: true });
  h.shell.setSidebarCollapsed(false);
  h.shell.setToolbarCollapsed(false);
  h.shell.setView('split');
  assert.deepEqual({ ...h.shell.getState() }, { view: 'split', sidebarCollapsed: false, toolbarCollapsed: false });
  h.shell.setToolbarCollapsed(true);
  h.shell.toggleFocus();
  assert.deepEqual({ ...h.shell.getState() }, { view: 'focus', sidebarCollapsed: false, toolbarCollapsed: false });
  h.shell.toggleFocus();
  assert.deepEqual({ ...h.shell.getState() }, { view: 'split', sidebarCollapsed: false, toolbarCollapsed: true });
  h.shell.setView('standard');
  assert.deepEqual({ ...h.shell.getState() }, { view: 'standard', sidebarCollapsed: true, toolbarCollapsed: false });
  h.shell.toggleFocus(); h.shell.toggleFocus();
  assert.equal(h.shell.getState().view, 'standard');
});

test('view changes publish only their own three geometry phases and repeated or invalid choices do nothing', () => {
  const h = harness(), workspace = h.nodes.get('workspace'), phases = [];
  const dispatch = h.window.dispatchEvent;
  h.window.dispatchEvent = event => {
    phases.push({ reason: event.detail.reason, phase: event.detail.phase, view: event.detail.view,
      domView: workspace.getAttribute('data-workspace-view'), collapsed: workspace.classList.contains('sidebar-collapsed') });
    dispatch(event);
  };
  h.shell.setView('focus');
  assert.deepEqual(phases, [
    { reason: 'workspace-view', phase: 'prepare', view: 'focus', domView: 'standard', collapsed: false },
    { reason: 'workspace-view', phase: 'start', view: 'focus', domView: 'focus', collapsed: true },
    { reason: 'workspace-view', phase: 'end', view: 'focus', domView: 'focus', collapsed: true },
  ]);
  h.shell.setView('focus'); h.shell.setView('unknown'); h.shell.setView('__proto__');
  h.nodes.get('left').fire('transitionend', { propertyName: 'width' });
  h.nodes.get('editorToolbar').fire('transitionend', { propertyName: 'height' });
  assert.equal(phases.length, 3);
  h.shell.setView('split'); h.shell.setView('standard');
  assert.equal(h.events.length, 9);
  assert.ok(h.events.every(event => event.detail.reason === 'workspace-view'));
});

test('a mode switch supersedes pending explicit collapse receipts without emitting a sidebar end later', () => {
  const h = harness();
  h.shell.setSidebarCollapsed(true); h.shell.setToolbarCollapsed(true);
  h.events.length = 0;
  h.shell.setView('split');
  h.nodes.get('left').fire('transitionend', { propertyName: 'width' });
  h.nodes.get('left').fire('transitionend', { propertyName: 'height' });
  h.nodes.get('editorToolbar').fire('transitionend', { propertyName: 'height' });
  assert.deepEqual(h.events.map(event => [event.detail.reason, event.detail.phase]), [
    ['workspace-view', 'prepare'], ['workspace-view', 'start'], ['workspace-view', 'end'],
  ]);
  // The public independent control still requests sidebar construction.
  h.shell.setSidebarCollapsed(true);
  assert.equal(h.events.at(-1).detail.reason, 'sidebar');
});

test('view menu radio state follows the public API and menu selection restores its stable trigger focus', () => {
  const h = harness();
  const options = ['Standard', 'Focus', 'Split'].map(name => h.nodes.get(`workspaceView${name}`));
  assert.deepEqual(options.map(option => option.getAttribute('aria-checked')), ['true', 'false', 'false']);
  assert.equal(h.nodes.get('workspaceViewCurrent').textContent, '标准');
  h.shell.setView('focus');
  assert.deepEqual(options.map(option => option.getAttribute('aria-checked')), ['false', 'true', 'false']);
  assert.equal(h.nodes.get('workspaceViewCurrent').textContent, '专注');
  h.doc.activeElement = options[2];
  options[2].fire('click');
  assert.equal(h.shell.getState().view, 'split');
  assert.equal(h.doc.activeElement, h.nodes.get('btnWorkspaceView'));
  assert.deepEqual(options.map(option => option.getAttribute('aria-checked')), ['false', 'false', 'true']);
  assert.equal(h.nodes.get('workspaceViewCurrent').textContent, '上下分栏');
});

test('split mode resizes the query height by pointer and vertical keys without changing the saved width', () => {
  const preferences = storage({ fund_sidebar_width: '663' });
  const h = harness(1440, { localStorage: preferences }), workspace = h.nodes.get('workspace');
  const handle = h.nodes.get('workspaceSidebarResize');
  h.shell.setView('split');
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '352px');
  assert.equal(handle.getAttribute('aria-orientation'), 'horizontal');
  assert.equal(handle.getAttribute('aria-label'), '调整查询区高度');
  handle.fire('pointerdown', { button: 0, pointerId: 1, clientY: 448, clientX: 700 });
  assert.equal(h.doc.body.classList.contains('workspace-resizing-vertical'), true);
  handle.fire('pointermove', { pointerId: 1, clientY: 400, clientX: 1000 });
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '400px');
  handle.fire('pointerup', { pointerId: 1 });
  assert.equal(h.doc.body.classList.contains('workspace-resizing-vertical'), false);
  assert.equal(handle.hasPointerCapture(1), false);
  assert.equal(handle.fire('keydown', { key: 'ArrowUp', shiftKey: true }).prevented, true);
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '450px');
  handle.fire('keydown', { key: 'ArrowDown' });
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '430px');
  assert.equal(handle.fire('keydown', { key: 'ArrowRight' }).prevented, undefined);
  assert.equal(workspace.style.getPropertyValue('--sidebar-width'), '663px');
  assert.deepEqual(preferences.writes, []);
  h.shell.setView('standard');
  assert.equal(h.nodes.get('left').getBoundingClientRect().width, 663);
  assert.equal(handle.getAttribute('aria-orientation'), 'vertical');
  assert.equal(handle.getAttribute('aria-label'), '调整查询区宽度');
  h.shell.setView('split');
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '430px');
  assert.deepEqual(preferences.writes, []);
  const reopened = harness(1440, { localStorage: preferences });
  reopened.shell.setView('split');
  assert.equal(reopened.nodes.get('workspace').style.getPropertyValue('--workspace-bottom-height'), '352px');
});

test('split height window clamps preserve the chosen height and leave room for the graph', () => {
  const h = harness(), workspace = h.nodes.get('workspace'), handle = h.nodes.get('workspaceSidebarResize');
  h.shell.setView('split');
  handle.fire('pointerdown', { button: 0, pointerId: 1, clientY: 448 });
  handle.fire('pointermove', { pointerId: 1, clientY: 300 });
  handle.fire('pointerup', { pointerId: 1 });
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '500px');
  workspace.height = h.window.innerHeight = 400;
  h.windowEvents.fire('resize');
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '260px');
  assert.equal(handle.getAttribute('aria-valuemin'), '240');
  assert.equal(handle.getAttribute('aria-valuemax'), '260');
  workspace.height = h.window.innerHeight = 300;
  h.windowEvents.fire('resize');
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '160px');
  assert.equal(handle.getAttribute('aria-valuemin'), '160');
  workspace.height = h.window.innerHeight = 100;
  h.windowEvents.fire('resize');
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '0px');
  assert.equal(handle.getAttribute('aria-valuemin'), '0');
  assert.equal(handle.getAttribute('aria-valuemax'), '0');
  workspace.height = h.window.innerHeight = 800;
  h.windowEvents.fire('resize');
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '500px');
  for (const height of [0, 100, 300]) {
    const small = harness(1440, {}, height); small.shell.setView('split');
    const actual = Number.parseInt(small.nodes.get('workspace').style.getPropertyValue('--workspace-bottom-height'));
    assert.ok(actual >= 0 && actual <= height);
    assert.ok(height - actual >= Math.min(140, height));
  }
});

test('split height follows actual workspace resizing after toolbar expansion and restores its preferred height', () => {
  const observed = [], preferences = storage({ fund_sidebar_width: '663' });
  let deliver = () => {};
  class ResizeObserver {
    constructor(callback) { deliver = callback; }
    observe(target) { observed.push(target); }
  }
  const h = harness(1440, { ResizeObserver, localStorage: preferences });
  const workspace = h.nodes.get('workspace'), handle = h.nodes.get('workspaceSidebarResize');
  h.shell.setView('split');
  h.nodes.get('btnToolbarToggle').fire('click');
  workspace.height = 882; // The compact toolbar frees 82px without resizing the browser window.
  deliver();
  handle.fire('pointerdown', { button: 0, pointerId: 1, clientY: 530 });
  handle.fire('pointermove', { pointerId: 1, clientY: -1000 });
  handle.fire('pointerup', { pointerId: 1 });
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '742px');
  h.nodes.get('btnToolbarToggle').fire('click');
  workspace.height = 800;
  deliver();
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '660px');
  assert.equal(handle.getAttribute('aria-valuemax'), '660');
  assert.equal(handle.getAttribute('aria-valuenow'), '660');
  assert.equal(workspace.height - h.nodes.get('left').getBoundingClientRect().height, 140);
  h.nodes.get('btnToolbarToggle').fire('click');
  workspace.height = 882;
  deliver();
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '742px');
  assert.equal(handle.getAttribute('aria-valuemax'), '742');
  assert.deepEqual(observed, [workspace]);
  assert.deepEqual(preferences.writes, []);
  assert.ok(h.events.every(event => event.detail.reason !== 'sidebar'));
  const count = h.events.length;
  deliver();
  assert.equal(h.events.length, count);
  h.shell.setView('standard');
  workspace.height = 800;
  deliver();
  h.shell.setView('focus');
  workspace.height = 882;
  deliver();
  assert.ok(h.events.every(event => event.detail.reason !== 'sidebar'));
});

test('without ResizeObserver toolbar transition completion still reclamps the split and restores its preference', () => {
  const h = harness(), workspace = h.nodes.get('workspace'), handle = h.nodes.get('workspaceSidebarResize');
  h.shell.setView('split');
  h.shell.setToolbarCollapsed(true);
  workspace.height = 882;
  h.nodes.get('editorToolbar').fire('transitionend', { propertyName: 'height' });
  handle.fire('pointerdown', { button: 0, pointerId: 1, clientY: 530 });
  handle.fire('pointermove', { pointerId: 1, clientY: -1000 });
  handle.fire('pointerup', { pointerId: 1 });
  h.shell.setToolbarCollapsed(false);
  workspace.height = 800;
  h.nodes.get('editorToolbar').fire('transitionend', { propertyName: 'height' });
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '660px');
  assert.equal(handle.getAttribute('aria-valuemax'), '660');
  h.shell.setToolbarCollapsed(true);
  workspace.height = 882;
  h.nodes.get('editorToolbar').fire('transitionend', { propertyName: 'height' });
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '742px');
  const toolbar = h.nodes.get('editorToolbar'), toggleClass = toolbar.classList.toggle;
  toolbar.classList.toggle = (name, collapsed) => {
    toggleClass(name, collapsed);
    if (name === 'is-collapsed') workspace.height = collapsed ? 882 : 800;
  };
  // With motion disabled the geometry is final immediately, with no transitionend.
  h.shell.setToolbarCollapsed(false);
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '660px');
  assert.equal(handle.getAttribute('aria-valuemax'), '660');
  h.shell.setToolbarCollapsed(true);
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '742px');
  assert.ok(h.events.every(event => event.detail.reason !== 'sidebar'));
});

test('split dragging clamps both limits and reselecting its current mode leaves the drag active', () => {
  const h = harness(), workspace = h.nodes.get('workspace'), handle = h.nodes.get('workspaceSidebarResize');
  h.shell.setView('split');
  handle.fire('pointerdown', { button: 0, pointerId: 1, clientY: 448 });
  h.events.length = 0;
  h.shell.setView('split');
  assert.equal(handle.hasPointerCapture(1), true);
  assert.equal(h.events.length, 0);
  handle.fire('pointermove', { pointerId: 1, clientY: -1000 });
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '660px');
  assert.equal(handle.getAttribute('aria-valuenow'), '660');
  handle.fire('pointermove', { pointerId: 1, clientY: 2000 });
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '240px');
  assert.equal(handle.getAttribute('aria-valuenow'), '240');
  handle.fire('pointercancel', { pointerId: 1 });
  assert.equal(handle.hasPointerCapture(1), false);
  assert.equal(h.events.at(-1).detail.reason, 'sidebar-height');
  assert.equal(h.events.at(-1).detail.phase, 'end');
});

test('switching modes ends an active height resize and stale pointer events cannot alter either dimension', () => {
  const preferences = storage({ fund_sidebar_width: '663' });
  const h = harness(1440, { localStorage: preferences }), workspace = h.nodes.get('workspace');
  const handle = h.nodes.get('workspaceSidebarResize');
  h.shell.setView('split');
  handle.fire('pointerdown', { button: 0, pointerId: 1, clientY: 448 });
  handle.fire('pointermove', { pointerId: 1, clientY: 400 });
  h.events.length = 0;
  h.shell.setView('standard');
  assert.equal(handle.hasPointerCapture(1), false);
  assert.equal(h.doc.body.classList.contains('workspace-resizing'), false);
  assert.equal(h.doc.body.classList.contains('workspace-resizing-vertical'), false);
  assert.equal(workspace.classList.contains('is-resizing'), false);
  handle.fire('pointermove', { pointerId: 1, clientY: 250, clientX: 900 });
  handle.fire('pointerup', { pointerId: 1 });
  assert.equal(workspace.style.getPropertyValue('--sidebar-width'), '663px');
  assert.equal(workspace.style.getPropertyValue('--workspace-bottom-height'), '400px');
  assert.deepEqual(preferences.writes, []);
  assert.ok(h.events.every(event => event.detail.reason === 'workspace-view'));
});

test('mode changes retain the original query table and leave the independent detail surface untouched', () => {
  const h = harness(), table = h.nodes.get('transactionTable'), detail = h.nodes.get('detailPanel');
  table.textContent = 'Synthetic query results';
  table.selection = { row: 3, scrollLeft: 480 };
  detail.setAttribute('data-open', 'true');
  detail.textContent = 'Synthetic selected transaction';
  h.shell.setView('split'); h.shell.toggleFocus(); h.shell.toggleFocus(); h.shell.setView('standard');
  assert.equal(h.nodes.get('transactionTable'), table);
  assert.equal(table.parent, h.nodes.get('left'));
  assert.equal(table.textContent, 'Synthetic query results');
  assert.deepEqual(table.selection, { row: 3, scrollLeft: 480 });
  assert.equal(detail.getAttribute('data-open'), 'true');
  assert.equal(detail.textContent, 'Synthetic selected transaction');
  assert.equal(detail.inert, false);
});

test('rapid mode changes keep the temporary transition suppression until the newest frame', () => {
  const frames = [], h = harness(1440, { requestAnimationFrame: callback => frames.push(callback) });
  const workspace = h.nodes.get('workspace');
  h.shell.setView('focus'); h.shell.setView('split');
  assert.equal(workspace.classList.contains('workspace-view-changing'), true);
  frames[0]();
  assert.equal(workspace.classList.contains('workspace-view-changing'), true);
  frames[1]();
  assert.equal(workspace.classList.contains('workspace-view-changing'), false);
});
