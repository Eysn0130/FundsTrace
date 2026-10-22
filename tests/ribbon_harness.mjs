import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const markup = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const source = readFileSync(new URL('../static/ribbon.js', import.meta.url), 'utf8');

// Minimal DOM uses the shipped markup, with no browser or frontend npm dependency.
function harness() {
  let doc;
  const all = [], events = [], listeners = new Map();
  const matches = (node, selector) => {
    if (selector.startsWith('.')) return node.classList.contains(selector.slice(1));
    const attribute = selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
    if (attribute) return node.hasAttribute(attribute[1]) && (attribute[2] === undefined || node.getAttribute(attribute[1]) === attribute[2]);
    return node.tagName.toLowerCase() === selector;
  };
  class Element {
    constructor(tag, attrs = {}) {
      this.tagName = tag.toUpperCase(); this.attrs = new Map(Object.entries(attrs)); this.children = [];
      this.id = attrs.id || ''; this.dataset = {}; this.handlers = new Map(); this.style = {};
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
    querySelectorAll(selector) { const out = []; const visit = n => { for (const child of n.children) { if (matches(child, selector)) out.push(child); visit(child); } }; visit(this); return out; }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    closest(selector) { for (let node = this; node; node = node.parentElement) if (selector === '[hidden]' ? node.hidden : matches(node, selector)) return node; return null; }
    contains(node) { for (; node; node = node.parentElement) if (node === this) return true; return false; }
    focus() { doc.activeElement = this; }
    getClientRects() { return this.closest('[hidden]') ? [] : [{}]; }
    getBoundingClientRect() { return { left: 1100, bottom: 180, width: 320, height: 180 }; }
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
  doc = { activeElement: null, createElement: tag => new Element(tag), getElementById: id => all.find(n => n.id === id), querySelectorAll: selector => all.filter(n => matches(n, selector)), querySelector: selector => all.find(n => matches(n, selector)), addEventListener(name, fn) { const list = listeners.get(name) || []; list.push(fn); listeners.set(name, list); }, dispatchEvent(event) { events.push(event); for (const fn of listeners.get(event.type) || []) fn(event); }, fire(name, detail = {}) { const event = { preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }, ...detail }; for (const fn of listeners.get(name) || []) fn(event); return event; } };
  const windowHandlers = new Map();
  const window = { document: doc, innerWidth: 1200, innerHeight: 800, CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } }, addEventListener(name, fn) { const list = windowHandlers.get(name) || []; list.push(fn); windowHandlers.set(name, list); } };
  vm.runInNewContext(source, { window });
  return { doc, window, events, api: window.FundsRibbon, node: id => doc.getElementById(id), fireWindow(name, detail = {}) { for (const fn of windowHandlers.get(name) || []) fn(detail); } };
}

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
