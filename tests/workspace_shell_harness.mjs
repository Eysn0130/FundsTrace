import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/workspace-shell.js', import.meta.url), 'utf8');

function harness(width = 1440) {
  const nodes = new Map();
  const events = [];
  let doc;
  function node(id) {
    const listeners = new Map(), classes = new Set(), attributes = new Map(), properties = new Map();
    const item = {
      id, width: id === 'left' ? 560 : width, inert: false, tabIndex: 0, title: '',
      style: { setProperty(key, value) { properties.set(key, value); }, getPropertyValue(key) { return properties.get(key) || ''; } },
      classList: { contains: name => classes.has(name), add: name => classes.add(name), remove: name => classes.delete(name), toggle(name, value) { if (value) classes.add(name); else classes.delete(name); } },
      addEventListener(name, fn) { const list = listeners.get(name) || []; list.push(fn); listeners.set(name, list); },
      setAttribute: (key, value) => attributes.set(key, value),
      getAttribute: key => attributes.get(key),
      querySelector() { return this.image; },
      contains(other) { return other === this || other?.parent === this; },
      focus() { doc.activeElement = this; },
      getBoundingClientRect() { return { width: this.width, left: 300, bottom: 110 }; },
      setPointerCapture() {},
      fire(name, detail = {}) { const event = { target: this, preventDefault() { this.prevented = true; }, ...detail }; for (const fn of listeners.get(name) || []) fn(event); return event; },
      image: { src: '' },
    };
    nodes.set(id, item); return item;
  }
  for (const id of ['workspace','left','editorToolbar','btnSidebarToggle','btnToolbarToggle','workspaceSidebarResize','btnDisplayActions','toolbarDisplayExtra','btnMainActions','toolbarMainActions','graphToolbar','body']) node(id);
  const sidebar = nodes.get('left');
  sidebar.getBoundingClientRect = () => {
    const workspace = nodes.get('workspace');
    const preferred = Number.parseInt(workspace.style.getPropertyValue('--sidebar-width')) || (window.innerWidth <= 700 ? window.innerWidth - 44 : 560);
    const maximum = Number.parseInt(workspace.style.getPropertyValue('--sidebar-maximum-width')) || Infinity;
    return { width: workspace.classList.contains('sidebar-collapsed') ? 0 : Math.min(preferred, maximum) };
  };
  const documentEvents = node('document');
  doc = { readyState: 'complete', body: nodes.get('body'), activeElement: null, getElementById: id => nodes.get(id), addEventListener: documentEvents.addEventListener.bind(documentEvents) };
  const windowEvents = node('window');
  const window = { document: doc, innerWidth: width, CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } }, dispatchEvent(event) { events.push(event); }, addEventListener: windowEvents.addEventListener.bind(windowEvents) };
  vm.runInNewContext(source, { window });
  return { nodes, events, doc, shell: nodes.get('workspace').fundsWorkspaceShell, documentEvents, windowEvents, window };
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

test('compact popovers reuse controls and close on Escape or outside click', () => {
  const h = harness();
  const button = h.nodes.get('btnDisplayActions'), panel = h.nodes.get('toolbarDisplayExtra');
  h.shell.setToolbarCollapsed(true);
  assert.equal(panel.inert, true);
  button.fire('click');
  assert.equal(panel.inert, false);
  assert.equal(panel.classList.contains('is-open'), true);
  assert.equal(button.getAttribute('aria-expanded'), 'true');
  h.documentEvents.fire('keydown', { key: 'Escape' });
  assert.equal(panel.inert, true);
  assert.equal(h.doc.activeElement, button);
  button.fire('click');
  h.documentEvents.fire('pointerdown', { target: h.nodes.get('left') });
  assert.equal(button.getAttribute('aria-expanded'), 'false');
  h.shell.setToolbarCollapsed(false);
  assert.equal(panel.inert, false);
  assert.equal(panel.getAttribute('aria-hidden'), 'false');
});

test('automatically closing a compact popover restores internal focus to its valid trigger only', () => {
  const h = harness();
  const button = h.nodes.get('btnDisplayActions'), panel = h.nodes.get('toolbarDisplayExtra');
  h.shell.setToolbarCollapsed(true);
  button.fire('click');
  h.doc.activeElement = { id: 'showIdCk', parent: panel };
  h.windowEvents.fire('resize');
  assert.equal(panel.classList.contains('is-open'), false);
  assert.equal(panel.inert, true);
  assert.equal(h.doc.activeElement, button);
  button.fire('click');
  const outside = h.nodes.get('left');
  h.doc.activeElement = outside;
  h.nodes.get('graphToolbar').fire('scroll');
  assert.equal(h.doc.activeElement, outside);
  button.fire('click');
  h.doc.activeElement = { id: 'showIdCk', parent: panel };
  button.disabled = true;
  h.windowEvents.fire('resize');
  assert.notEqual(h.doc.activeElement, button);
});

test('mount is idempotent and transition receipts contain chrome state only', () => {
  const h = harness();
  assert.equal(h.window.FundsWorkspaceShell.mount(), h.shell);
  h.nodes.get('btnToolbarToggle').fire('click');
  assert.equal(h.events.length, 1);
  h.nodes.get('editorToolbar').fire('transitionend', { propertyName: 'height' });
  assert.equal(h.events.at(-1).detail.phase, 'end');
  assert.deepEqual(Object.keys(h.events.at(-1).detail).sort(), ['phase','reason','sidebarCollapsed','toolbarCollapsed']);
});
