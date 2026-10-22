import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const markup = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const source = readFileSync(new URL('../static/ribbon.js', import.meta.url), 'utf8');

// Minimal DOM uses the shipped markup, with no browser or frontend npm dependency.
function harness({ slotWidth = 1144, hidden = false, resizeObserver = true } = {}) {
  let doc;
  const all = [], events = [], windowEvents = [], listeners = new Map(), observers = [];
  const matches = (node, selector) => {
    if (selector.startsWith('.')) return node.classList.contains(selector.slice(1));
    const attribute = selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
    if (attribute) return node.hasAttribute(attribute[1]) && (attribute[2] === undefined || node.getAttribute(attribute[1]) === attribute[2]);
    return node.tagName.toLowerCase() === selector;
  };
  class Element {
    constructor(tag, attrs = {}) {
      this.tagName = tag.toUpperCase(); this.attrs = new Map(Object.entries(attrs)); this.children = [];
      this.id = attrs.id || ''; this.dataset = {}; this.handlers = new Map();
      this.style = { setProperty(key, value) { this[key] = String(value); }, getPropertyValue(key) { return this[key] || ''; } };
      this.hidden = 'hidden' in attrs; this.inert = false; this.disabled = 'disabled' in attrs;
      this.checked = 'checked' in attrs; this.type = attrs.type || ''; this.value = attrs.value || ''; this.tabIndex = +(attrs.tabindex || 0);
      this.isConnected = true; this.textContent = ''; this.clicks = 0;
      const classes = new Set((attrs.class || '').split(/\s+/));
      this.classList = { contains: n => classes.has(n), add: n => classes.add(n), remove: n => classes.delete(n) };
      for (const [key, value] of Object.entries(attrs)) if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, a) => a.toUpperCase())] = value;
      all.push(this);
    }
    hasAttribute(key) { return this.attrs.has(key); }
    getAttribute(key) { return this.attrs.get(key) ?? null; }
    setAttribute(key, value) { this.attrs.set(key, String(value)); }
    appendChild(node) { if (node.parentElement) node.parentElement.children = node.parentElement.children.filter(c => c !== node); this.children.push(node); node.parentElement = this; }
    insertBefore(node, next) { if (!next) return this.appendChild(node); if (node.parentElement) node.parentElement.children = node.parentElement.children.filter(c => c !== node); this.children.splice(this.children.indexOf(next), 0, node); node.parentElement = this; }
    get nextSibling() { return this.parentElement?.children[this.parentElement.children.indexOf(this) + 1] || null; }
    querySelectorAll(selector) { const out = []; const visit = n => { for (const child of n.children) { if (matches(child, selector)) out.push(child); visit(child); } }; visit(this); return out; }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    closest(selector) { for (let node = this; node; node = node.parentElement) if (selector === '[hidden]' ? node.hidden : matches(node, selector)) return node; return null; }
    contains(node) { for (; node; node = node.parentElement) if (node === this) return true; return false; }
    focus() { doc.activeElement = this; }
    getClientRects() { return this.closest('[hidden]') ? [] : [{}]; }
    getBoundingClientRect() {
      if (this.id === 'graphPage') return { left: 56, right: 56 + this.clientWidth, width: this.clientWidth, top: 32, bottom: 800, height: 768 };
      if (this.id === 'editorToolbar') {
        // Model the shipped CSS width/zoom relationship, independently of the
        // width source selected by ribbon.js. The 56px navigation rail remains
        // outside this slot and must never contribute to the toolbar's width.
        const width = Number.parseFloat(this.style.getPropertyValue('--toolbar-fit-width')) * Number(this.style.getPropertyValue('--toolbar-fit-scale'));
        return { left: 56, right: 56 + width, width, top: 32, bottom: 180, height: 148 };
      }
      return { left: 1100, bottom: 180, width: 320, height: 180 };
    }
    addEventListener(name, fn) { const list = this.handlers.get(name) || []; list.push(fn); this.handlers.set(name, list); }
    fire(name, detail = {}) { const event = { target: this, preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }, ...detail }; for (const fn of this.handlers.get(name) || []) fn(event); return event; }
    click() { if (!this.disabled) { this.clicks++; this.fire('click'); } }
  }
  const documentRoot = new Element('document');
  const stack = [documentRoot];
  const voidTags = new Set(['input','img','meta','link','br','hr','source']);
  for (const match of markup.matchAll(/<(\/?)([a-z][\w-]*)([^>]*?)>/gi)) {
    const [, closing, tag, raw] = match;
    if (closing) { while (stack.length > 1) if (stack.pop().tagName === tag.toUpperCase()) break; continue; }
    const attrs = {};
    for (const attribute of raw.matchAll(/([^\s=]+)(?:="([^"]*)"|='([^']*)'|=([^\s]+))?/g)) attrs[attribute[1]] = attribute[2] ?? attribute[3] ?? attribute[4] ?? '';
    const node = new Element(tag, attrs); stack.at(-1).appendChild(node);
    if (!voidTags.has(tag.toLowerCase()) && !raw.endsWith('/')) stack.push(node);
  }
  doc = { activeElement: null, body: all.find(n => n.tagName === 'BODY'), documentElement: all.find(n => n.tagName === 'HTML'), createElement: tag => new Element(tag), getElementById: id => all.find(n => n.id === id), querySelectorAll: selector => all.filter(n => matches(n, selector)), querySelector: selector => all.find(n => matches(n, selector)), addEventListener(name, fn) { const list = listeners.get(name) || []; list.push(fn); listeners.set(name, list); }, dispatchEvent(event) { events.push(event); for (const fn of listeners.get(event.type) || []) fn(event); }, fire(name, detail = {}) { const event = { preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }, ...detail }; for (const fn of listeners.get(name) || []) fn(event); return event; } };
  const windowHandlers = new Map();
  const window = { document: doc, innerWidth: 1200, innerHeight: 800, CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } }, addEventListener(name, fn) { const list = windowHandlers.get(name) || []; list.push(fn); windowHandlers.set(name, list); }, dispatchEvent(event) { windowEvents.push(event); for (const fn of windowHandlers.get(event.type) || []) fn(event); } };
  Object.defineProperty(doc.documentElement, 'clientWidth', { get: () => window.innerWidth });
  const host = doc.getElementById('graphPage');
  host.hidden = hidden;
  Object.defineProperty(host, 'clientWidth', { get: () => host.closest('[hidden]') ? 0 : slotWidth });
  if (resizeObserver) window.ResizeObserver = class {
    constructor(callback) { this.callback = callback; this.targets = new Set(); observers.push(this); }
    observe(target) { this.targets.add(target); }
    disconnect() { this.targets.clear(); }
  };
  const notifySlotResize = () => {
    for (const observer of observers) if (observer.targets.has(host)) observer.callback([{ target: host, contentRect: host.getBoundingClientRect() }]);
  };
  vm.runInNewContext(source, { window });
  return { doc, window, events, windowEvents, observers, notifySlotResize,
    resizeSlot(width, notify = true) { slotWidth = width; if (notify) notifySlotResize(); },
    api: window.FundsRibbon, node: id => doc.getElementById(id), fireWindow(name, detail = {}) { for (const fn of windowHandlers.get(name) || []) fn(detail); } };
}

test('the retained ribbon preserves a 1280 logical width without scaling the rail, caption or content', () => {
  const h = harness(), layout = h.node('layoutSel');
  assert.equal(h.node('topbar'), undefined, 'the old stacked app header is replaced by the navigation rail');
  assert.ok(h.node('appRail'));
  assert.ok(h.node('browserCaption'));
  layout.value = 'dagre';
  for (const [viewportWidth, availableWidth, logicalWidth, expectedScale] of [
    [1512, 1456, 1456, 1], [1336, 1280, 1280, 1], [1920, 1864, 1864, 1],
    [1024, 968, 1280, 0.75625], [960, 904, 1280, 0.70625], [1336, 1280, 1280, 1]
  ]) {
    h.window.innerWidth = viewportWidth;
    h.resizeSlot(availableWidth, false);
    h.fireWindow('resize');
    for (const id of ['editorToolbar']) {
      const style = h.node(id).style;
      const logical = Number.parseFloat(style.getPropertyValue('--toolbar-fit-width'));
      const scale = Number(style.getPropertyValue('--toolbar-fit-scale'));
      assert.equal(logical, logicalWidth);
      assert.equal(scale, expectedScale);
      assert.equal(h.node(id).getBoundingClientRect().right, viewportWidth, 'the complete toolbar fits beside the 56px rail');
      assert.equal(h.node(id).getBoundingClientRect().width, availableWidth);
    }
    assert.equal(h.doc.body.style.getPropertyValue('--toolbar-fit-scale'), '');
    assert.equal(h.node('appRail').style.getPropertyValue('--toolbar-fit-scale'), '');
    assert.equal(h.node('browserCaption').style.getPropertyValue('--toolbar-fit-scale'), '');
    assert.equal(h.node('flowTableScroll').style.getPropertyValue('--toolbar-fit-scale'), '');
    assert.equal(h.node('layoutSel'), layout);
    assert.equal(layout.value, 'dagre');
  }
  assert.equal(h.events.length, 0, 'adapting chrome must not invoke a graph command');
  assert.ok(h.windowEvents.some(event => event.detail.reason === 'toolbar-fit' && event.detail.phase === 'prepare'));
  assert.ok(h.windowEvents.some(event => event.detail.reason === 'toolbar-fit' && event.detail.phase === 'start'));
});

test('slot resizing fits the ribbon without a window resize and coordinates the canvas before scaling', () => {
  const h = harness({ slotWidth: 1456 }), toolbar = h.node('editorToolbar'), layout = h.node('layoutSel');
  h.window.innerWidth = 1512;
  layout.value = 'dagre';
  const samples = [];
  h.window.addEventListener('fundstrace:workspace-resize', event => {
    if (event.detail.reason === 'toolbar-fit') samples.push([event.detail.phase, toolbar.getBoundingClientRect().width]);
  });
  h.resizeSlot(968);
  assert.equal(h.window.innerWidth, 1512);
  assert.equal(toolbar.getBoundingClientRect().width, 968);
  assert.deepEqual(samples, [['prepare', 1456], ['start', 968]], 'camera coordination surrounds the CSS geometry change');
  h.notifySlotResize();
  assert.equal(samples.length, 2, 'unchanged observer notifications do not restart camera coordination');
  h.resizeSlot(1456);
  assert.equal(toolbar.getBoundingClientRect().right, 1512);
  assert.equal(h.node('layoutSel'), layout);
  assert.equal(layout.value, 'dagre');
  assert.equal(h.events.length, 0);
});

test('a temporarily hidden slot retains its fit and observes the restored slot width', () => {
  const h = harness({ slotWidth: 968 }), toolbar = h.node('editorToolbar'), host = h.node('graphPage');
  const style = [toolbar.style.getPropertyValue('--toolbar-fit-width'), toolbar.style.getPropertyValue('--toolbar-fit-scale')];
  host.hidden = true;
  h.resizeSlot(1456);
  h.window.innerWidth = 1512; h.fireWindow('resize');
  assert.deepEqual([toolbar.style.getPropertyValue('--toolbar-fit-width'), toolbar.style.getPropertyValue('--toolbar-fit-scale')], style, 'a zero-width hidden slot must not be replaced by the whole viewport');
  host.hidden = false; h.notifySlotResize();
  assert.equal(toolbar.getBoundingClientRect().width, 1456);
  assert.equal(toolbar.style.getPropertyValue('--toolbar-fit-scale'), '1');
  assert.equal(h.events.length, 0);
});

test('a ribbon mounted in a hidden slot waits for geometry and retains the window-resize fallback', () => {
  const h = harness({ slotWidth: 968, hidden: true, resizeObserver: false }), toolbar = h.node('editorToolbar');
  assert.equal(toolbar.style.getPropertyValue('--toolbar-fit-scale'), '');
  h.node('graphPage').hidden = false; h.fireWindow('resize');
  assert.equal(toolbar.getBoundingClientRect().width, 968);
  h.resizeSlot(1456, false); h.window.innerWidth = 1512; h.fireWindow('resize');
  assert.equal(toolbar.getBoundingClientRect().right, 1512);
});

test('slot resizing closes a floating popover and returns focus without replacing its controls', () => {
  const h = harness({ slotWidth: 1456 }); h.api.setCollapsed(true);
  const trigger = h.node('btnDisplayActions'), panel = h.node('toolbarDisplayExtra'), checkbox = h.node('showIdCk');
  const originalParent = panel.parentElement;
  checkbox.checked = false;
  trigger.click(); checkbox.focus();
  h.resizeSlot(968);
  assert.equal(panel.hidden, true);
  assert.equal(panel.parentElement, originalParent);
  assert.equal(h.doc.activeElement, trigger);
  assert.equal(h.node('showIdCk'), checkbox);
  assert.equal(checkbox.checked, false);
  assert.equal(h.events.length, 0);
});

test('floating controls leave scaled chrome temporarily and return home with identity and focus intact', () => {
  const h = harness(); h.api.setCollapsed(true);
  const trigger = h.node('btnDisplayActions'), panel = h.node('toolbarDisplayExtra');
  const originalParent = panel.parentElement, originalNext = panel.nextSibling, checkbox = h.node('showIdCk');
  checkbox.checked = false;
  h.window.innerWidth = 1024; h.fireWindow('resize');
  trigger.click();
  assert.equal(panel.parentElement, h.doc.body);
  assert.equal(panel.hidden, false);
  assert.equal(panel.style.left, '696px', 'anchored popover uses unscaled viewport coordinates');
  checkbox.focus();
  h.window.innerWidth = 960; h.fireWindow('resize');
  assert.equal(panel.hidden, true);
  assert.equal(panel.parentElement, originalParent);
  assert.equal(panel.nextSibling, originalNext);
  assert.equal(h.doc.activeElement, trigger);
  assert.equal(h.node('showIdCk'), checkbox);
  assert.equal(checkbox.checked, false);
  h.api.setCollapsed(false);
  assert.equal(panel.hidden, false);
  assert.equal(panel.getAttribute('role'), 'group');
  assert.equal(panel.parentElement, originalParent);
  assert.equal(h.events.length, 0);
});

test('four real tabs show one panel and support wrapping keyboard navigation', () => {
  const h = harness();
  assert.equal(h.doc.querySelectorAll('.ribbon-panel').filter(p => !p.hidden).length, 1);
  h.node('ribbonTab-common').fire('keydown', { key: 'ArrowLeft' });
  assert.equal(h.api.getState().tab, 'view');
  assert.equal(h.doc.activeElement, h.node('ribbonTab-view'));
  h.node('ribbonTab-view').fire('keydown', { key: 'Home' });
  assert.equal(h.api.getState().tab, 'common');
  h.node('ribbonTab-common').fire('keydown', { key: 'End' });
  assert.equal(h.api.getState().tab, 'view');
  assert.equal(h.events.length, 0, 'switching tabs must not invoke graph commands');
});

test('compact mode moves original controls without cloning or emitting graph commands', () => {
  const h = harness();
  const layout = h.node('layoutSel'), currency = h.node('relationCurrency'), checkbox = h.node('showPhotoCk');
  layout.value = 'dagre'; currency.value = 'USD'; checkbox.checked = false;
  h.api.setCollapsed(true);
  assert.equal(h.node('ribbonCompact').contains(layout), true);
  assert.equal(h.node('ribbonTabs').parentElement.hidden, true);
  assert.equal(h.node('ribbonPanels').hidden, true, 'the fixed-height panel container must release its space');
  assert.equal(h.node('ribbonPanels').inert, true);
  assert.equal(h.doc.querySelectorAll('.ribbon-panel').filter(p => !p.hidden).length, 0);
  h.api.activateTab('trace'); h.api.setCollapsed(false);
  assert.equal(h.node('ribbonPanels').hidden, false);
  assert.equal(h.node('ribbonPanels').inert, false);
  assert.equal(h.node('ribbonPanel-trace').contains(currency), true);
  assert.equal(h.node('layoutSel'), layout); assert.equal(layout.value, 'dagre');
  assert.equal(currency.value, 'USD'); assert.equal(checkbox.checked, false);
  assert.equal(h.events.length, 0);
});

test('compact popovers close on Escape, resize and outside click with valid focus restoration', () => {
  const h = harness(); h.api.setCollapsed(true);
  const button = h.node('btnDisplayActions'), panel = h.node('toolbarDisplayExtra');
  button.click(); assert.equal(panel.hidden, false); assert.equal(button.getAttribute('aria-expanded'), 'true');
  h.node('showIdCk').focus();
  const escape = h.doc.fire('keydown', { key: 'Escape' });
  assert.equal(panel.hidden, true); assert.equal(h.doc.activeElement, button);
  assert.equal(escape.prevented, true); assert.equal(escape.stopped, true);
  button.click(); h.node('showIdCk').focus(); h.fireWindow('resize');
  assert.equal(h.doc.activeElement, button); assert.equal(panel.hidden, true);
  button.click(); h.doc.fire('pointerdown', { target: h.node('left') }); assert.equal(panel.hidden, true);
  h.api.setCollapsed(false); assert.equal(panel.hidden, false, 'expanded labels are inline');
});

test('adaptive inline controls are groups and compact popovers alone use dialog roles', () => {
  const h = harness();
  const ids = ['textStylePopover', 'edgeStylePopover', 'toolbarDisplayExtra'];
  for (const id of ids) {
    assert.equal(h.node(id).getAttribute('role'), 'group', `${id} is inline in expanded mode`);
    assert.equal(h.node(id).hidden, false);
  }
  h.api.activateTab('style');
  for (const id of ids) assert.equal(h.node(id).getAttribute('role'), 'group');
  h.api.setCollapsed(true);
  for (const id of ids) {
    assert.equal(h.node(id).getAttribute('role'), 'dialog');
    assert.equal(h.node(id).hidden, true);
  }
  h.node('btnTextStyle').click();
  assert.equal(h.node('textStylePopover').getAttribute('role'), 'dialog');
  assert.equal(h.node('textStylePopover').hidden, false);
  h.api.setCollapsed(false);
  for (const id of ids) assert.equal(h.node(id).getAttribute('role'), 'group');
});

test('commands carry values, text entry is not intercepted and planned controls remain disabled', () => {
  const h = harness(); h.api.activateTab('style');
  h.node('labelBold').click();
  assert.equal(h.events.at(-1).detail.command, 'labelBold'); assert.equal(h.events.at(-1).detail.value, true);
  h.node('labelFontSize').value = '20'; h.node('labelFontSize').fire('change');
  assert.equal(h.events.at(-1).detail.value, '20');
  const input = h.node('graphFindInput'); input.value = '  synthetic account  ';
  input.fire('keydown', { key: 'ArrowRight' }); assert.equal(h.events.length, 2);
  input.fire('keydown', { key: 'Enter' });
  assert.equal(h.events.length, 2, 'the finder form owns Enter; ribbon must not dispatch a duplicate search');
  assert.equal(input.value, '  synthetic account  ', 'the ribbon leaves local text editing to the finder');
  h.api.setStatus({ selectionCount: 3, busy: false });
  for (const node of h.doc.querySelectorAll('[data-planned]')) assert.equal(node.disabled, true);
  assert.equal(h.node('labelUnderline').disabled, true);
  assert.equal(h.doc.querySelectorAll('[data-ribbon-command="clearSelection"]')[0].disabled, false);
  h.api.setStatus({ busy: true });
  assert.equal(h.doc.querySelectorAll('[data-ribbon-command="clearSelection"]')[0].disabled, true);
});

test('proxy buttons invoke the existing single business handler and updates emit no commands', () => {
  const h = harness();
  const proxy = h.doc.querySelectorAll('[data-ribbon-proxy="btnImport"]')[0];
  proxy.click(); assert.equal(h.node('btnImport').clicks, 1);
  h.api.setValues({ widthCurrency: 'EUR', amountWidthCk: true, labelBold: true });
  assert.equal(h.node('widthCurrency').value, 'EUR'); assert.equal(h.node('amountWidthCk').checked, true);
  assert.equal(h.node('labelBold').getAttribute('aria-pressed'), 'true');
  assert.equal(h.events.length, 0);
});

test('mixed styles show placeholders and toggles enable consistently without extra commands', () => {
  const h = harness();
  h.api.setStatus({ selectionCount: 2 });
  h.api.setValues({ labelFontFamily: { mixed: true }, labelFontSize: { mixed: true },
    labelBold: { mixed: true }, labelItalic: { mixed: true }, labelColor: { mixed: true } });
  for (const id of ['labelFontFamily', 'labelFontSize']) {
    const node = h.node(id), placeholder = node.children.at(-1);
    assert.equal(node.value, ''); assert.equal(placeholder.textContent, '混合值');
    assert.equal(placeholder.disabled, true); assert.equal(placeholder.hidden, true);
    node.fire('change');
  }
  assert.equal(h.node('labelBold').getAttribute('aria-pressed'), 'mixed');
  assert.equal(h.node('labelItalic').getAttribute('aria-pressed'), 'mixed');
  assert.equal(h.node('labelColor').getAttribute('data-mixed'), 'true');
  assert.match(h.node('labelColor').getAttribute('aria-description'), /混合值/);
  assert.equal(h.events.length, 0);
  h.node('labelBold').click(); h.node('labelItalic').click();
  assert.deepEqual(h.events.map(event => [event.detail.command, event.detail.value]), [['labelBold', true], ['labelItalic', true]]);
  h.api.setValues({ labelFontFamily: 'Arial', labelFontSize: 16, labelBold: true, labelItalic: false, labelColor: '#123456' });
  assert.equal(h.node('labelFontSize').value, '16');
  assert.equal(h.node('labelBold').getAttribute('aria-pressed'), 'true');
  assert.equal(h.node('labelItalic').getAttribute('aria-pressed'), 'false');
  assert.equal(h.node('labelColor').getAttribute('data-mixed'), 'false');
  assert.equal(h.node('labelColor').getAttribute('aria-description'), '');
  h.api.setCollapsed(true); h.api.setCollapsed(false);
  assert.equal(h.node('labelFontFamily').value, 'Arial');
  assert.equal(h.events.length, 2);
});

test('selected relationships have an accurate status and only compatible fallback commands', () => {
  const h = harness(); h.api.setStatus({ selectionCount: 0, edgeSelectionCount: 1 });
  assert.equal(h.node('ribbonSelectionStatus').textContent, '已选 1 条关系');
  const command = name => h.doc.querySelectorAll(`[data-ribbon-command="${name}"]`)[0];
  assert.equal(command('clearSelection').disabled, false);
  assert.equal(command('fitSelection').disabled, false);
  assert.equal(command('accountDetails').disabled, true);
  h.api.setStatus({ edgeSelectionCount: 0 });
  assert.equal(h.node('ribbonSelectionStatus').textContent, '未选择节点');
});

test('expanded and compact details entries share exact selection availability and the command route', () => {
  const h = harness(), buttons = h.doc.querySelectorAll('[data-ribbon-command="inspectSelection"]');
  assert.equal(buttons.length, 2);
  for (const [selectionCount, edgeSelectionCount, allowed, label] of [
    [0, 0, false, '查看详情'], [2, 0, false, '查看详情'], [1, 1, false, '查看详情'],
    [1, 0, true, '查看账户详情'], [0, 1, true, '查看资金流向'],
  ]) {
    h.api.setStatus({ selectionCount, edgeSelectionCount, busy: false });
    for (const button of buttons) {
      assert.equal(button.disabled, !allowed); assert.equal(button.getAttribute('aria-label'), label);
      if (allowed) assert.match(button.title, /Enter/);
    }
  }
  h.api.setStatus({ busy: true });
  for (const button of buttons) assert.equal(button.disabled, true);
  h.api.setStatus({ busy: false }); h.api.setCommandEnabled('inspectSelection', false);
  for (const button of buttons) assert.equal(button.disabled, true);
  h.api.setCommandEnabled('inspectSelection', true);
  const compact = buttons.find(button => h.node('ribbonCompact').contains(button));
  const expanded = buttons.find(button => button !== compact);
  assert.ok(compact); assert.ok(expanded);
  expanded.click(); h.api.setCollapsed(true); compact.click();
  assert.deepEqual(h.events.map(event => event.detail.command), ['inspectSelection', 'inspectSelection']);
  h.api.setStatus({ selectionCount: 2, edgeSelectionCount: 0 });
  for (const button of buttons) assert.equal(button.disabled, true, 'a stale command override cannot enable multiple objects');
});
