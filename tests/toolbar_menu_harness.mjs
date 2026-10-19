import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/toolbar-menu.js', import.meta.url), 'utf8');

// Execute the production script unchanged, modeling capture/bubble propagation,
// visible geometry, body portals, focus and browser-compatible pointer events.
function harness({ width = 1000, height = 700, withForm = false } = {}) {
  let doc, now = 1000, frameId = 0;
  const frames = new Map();
  class Element {
    constructor(tag = 'div', id = '') {
      this.tagName = tag.toUpperCase(); this.id = id; this.children = []; this.parentElement = null;
      this.attributes = new Map(); this.listeners = new Map(); this.style = {}; this.hidden = false; this.inert = false;
      this.disabled = false; this.rect = { left: 20, top: 20, width: 240, height: 140 };
      this.classList = { add: value => this.attributes.set('class', [...new Set([...this.classes(), value])].join(' ')),
        remove: value => this.attributes.set('class', this.classes().filter(item => item !== value).join(' ')), contains: value => this.classes().includes(value) };
    }
    classes() { return (this.attributes.get('class') || '').split(/\s+/).filter(Boolean); }
    get isConnected() { return this === doc || !!this.parentElement?.isConnected; }
    get tabIndex() { return this.attributes.has('tabindex') ? Number(this.attributes.get('tabindex')) : ['BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'A'].includes(this.tagName) ? 0 : -1; }
    set tabIndex(value) { this.setAttribute('tabindex', value); }
    appendChild(node) {
      if (node.parentElement) node.parentElement.children.splice(node.parentElement.children.indexOf(node), 1);
      node.parentElement = this; this.children.push(node); return node;
    }
    remove() { if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1); this.parentElement = null; }
    setAttribute(key, value) { this.attributes.set(key, String(value)); }
    removeAttribute(key) { this.attributes.delete(key); }
    getAttribute(key) { return this.attributes.get(key) ?? null; }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    matches(selector) {
      return selector.split(',').some(part => {
        const excluded = [...part.matchAll(/:not\(([^)]+)\)/g)].map(match => match[1]);
        if (excluded.some(value => this.matches(value))) return false;
        const text = part.replace(/:not\([^)]+\)/g, '').trim(), tag = text.match(/^[\w-]+/)?.[0], id = text.match(/#([\w-]+)/)?.[1];
        if (text.includes(':disabled') && !this.disabled || text.includes(':enabled') && this.disabled) return false;
        if (tag && this.tagName !== tag.toUpperCase() || id && this.id !== id) return false;
        if ([...text.matchAll(/\.([\w-]+)/g)].some(match => !this.classList.contains(match[1]))) return false;
        for (const match of text.matchAll(/\[([\w-]+)(?:=["']?([^\]"']+)["']?)?\]/g)) {
          const present = match[1] === 'hidden' ? this.hidden : match[1] === 'inert' ? this.inert : this.attributes.has(match[1]);
          if (!present || match[2] !== undefined && this.getAttribute(match[1]) !== match[2]) return false;
        }
        return true;
      });
    }
    closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
    querySelectorAll(selector) { return this.children.flatMap(child => [child, ...child.querySelectorAll('*')]).filter(node => node.matches(selector)); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    getClientRects() {
      for (let node = this; node; node = node.parentElement) if (node.hidden || node.style.display === 'none') return [];
      return this.isConnected ? [this.getBoundingClientRect()] : [];
    }
    getBoundingClientRect() {
      const width = Math.min(this.rect.width, parseFloat(this.style.maxWidth ?? Infinity));
      const height = Math.min(this.rect.height, parseFloat(this.style.maxHeight ?? Infinity));
      return { ...this.rect, width, height, right: this.rect.left + width, bottom: this.rect.top + height };
    }
    focus() {
      if (!this.disabled && this.isConnected && !this.closest('[inert]') && this.getClientRects().length && doc.activeElement !== this) {
        doc.activeElement = this; this.fire('focusin');
      }
    }
    addEventListener(type, callback, options = false) {
      const listeners = this.listeners.get(type) || [];
      listeners.push({ callback, capture: options === true || !!options.capture }); this.listeners.set(type, listeners);
    }
    dispatchEvent(event) { return this.fire(event.type, event); }
    fire(type, options = {}) {
      const event = { type, target: this, button: 0, pointerId: 1, clientX: 80, clientY: 200,
        key: '', ctrlKey: false, metaKey: false, shiftKey: false, repeat: false, defaultPrevented: false, stopped: false, immediate: false,
        preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; },
        stopImmediatePropagation() { this.immediate = this.stopped = true; }, ...options };
      const path = []; for (let node = this; node; node = node.parentElement) path.push(node);
      const invoke = (node, capture) => {
        event.currentTarget = node;
        for (const listener of node.listeners.get(type) || []) {
          if (listener.capture === capture) listener.callback(event);
          if (event.immediate) return;
        }
        if (!capture && !event.immediate) node['on' + type]?.(event);
      };
      for (const node of [...path].reverse()) { invoke(node, true); if (event.stopped) return event; }
      for (const node of path) { invoke(node, false); if (event.stopped) return event; }
      return event;
    }
    click() { return this.fire('click', { pointerId: undefined }); }
  }
  const window = new Element('window');
  doc = new Element('#document'); doc.parentElement = window; window.children.push(doc);
  doc.body = doc.appendChild(new Element('body')); doc.activeElement = doc.body; doc.documentElement = doc.body;
  doc.getElementById = id => doc.querySelector('#' + id);
  const add = (parent, tag, id, attrs = {}) => {
    const node = parent.appendChild(new Element(tag, id));
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
  };
  const toolbar = add(doc.body, 'header', 'editorToolbar');
  const before = add(toolbar, 'button', 'before');
  const trigger = add(toolbar, 'button', 'canvasActions', { 'data-toolbar-menu': 'canvasMenu' });
  trigger.rect = { left: 30, top: 20, width: 100, height: 30 };
  const svg = add(trigger, 'svg', 'triggerSvg');
  const after = add(toolbar, 'button', 'after');
  const second = add(toolbar, 'button', 'secondActions', { 'data-toolbar-menu': 'secondMenu' });
  const panel = add(toolbar, 'div', 'canvasMenu');
  const rename = add(panel, 'button', 'rename', { role: 'menuitem' });
  const disabled = add(panel, 'button', 'delete', { role: 'menuitem', 'aria-disabled': 'true' });
  const separator = add(panel, 'div', 'separator', { role: 'separator' });
  const copy = add(panel, 'button', 'copy', { role: 'menuitem' });
  const secondPanel = add(toolbar, 'div', 'secondMenu');
  const other = add(secondPanel, 'button', 'other', { role: 'menuitem' });
  let formTrigger, formAfter, formPanel, formDisabled, formHidden, currency, minimum, maximum, apply;
  if (withForm) {
    formTrigger = add(toolbar, 'button', 'relationFilter', { 'data-toolbar-menu': 'relationForm' });
    formTrigger.rect = { left: 150, top: 20, width: 110, height: 30 };
    formAfter = add(toolbar, 'button', 'formAfter');
    formPanel = add(toolbar, 'section', 'relationForm', { role: 'dialog', 'data-toolbar-panel': 'form' });
    formDisabled = add(formPanel, 'input', 'disabledAmount'); formDisabled.disabled = true;
    formHidden = add(formPanel, 'input', 'hiddenAmount'); formHidden.hidden = true;
    currency = add(formPanel, 'select', 'relationCurrency');
    minimum = add(formPanel, 'input', 'relationMinimum', { type: 'text' });
    maximum = add(formPanel, 'input', 'relationMaximum', { type: 'text' });
    apply = add(formPanel, 'button', 'relationApply');
  }
  const graph = add(doc.body, 'section', 'graphContainer'); graph.tabIndex = 0;
  const node = add(graph, 'canvas', 'node'), minimap = add(doc.body, 'aside', 'graphMinimapPanel');
  let ribbonCloses = 0;
  Object.assign(window, { document: doc, innerWidth: width, innerHeight: height,
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    FundsRibbon: { closePopovers() { ribbonCloses++; } },
    requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame(id) { frames.delete(id); },
    getComputedStyle: element => ({ display: element.style.display || 'block', visibility: element.style.visibility || 'visible' }) });
  const context = vm.createContext({ window, Date: { now: () => now }, console });
  vm.runInContext(source, context, { filename: 'toolbar-menu.js' });
  const api = window.FundsToolbarMenu;
  const tick = () => { const pending = [...frames.values()]; frames.clear(); for (const callback of pending) callback(); };
  return { window, document: doc, api, toolbar, before, trigger, svg, after, second, panel, rename, disabled, separator, copy, secondPanel, other,
    formTrigger, formAfter, formPanel, formDisabled, formHidden, currency, minimum, maximum, apply,
    graph, node, minimap, add, tick, frames, advance: ms => { now += ms; }, ribbonCloses: () => ribbonCloses };
}

test('mount is idempotent, portals menus and opens from the SVG with current availability', () => {
  const f = harness();
  assert.equal(f.api.mount(f.document), f.api);
  assert.equal(f.panel.parentElement, f.document.body); assert.equal(f.panel.hidden, true);
  assert.equal(f.trigger.getAttribute('aria-haspopup'), 'menu'); assert.equal(f.trigger.getAttribute('aria-controls'), 'canvasMenu');
  assert.equal(f.rename.tabIndex, -1); assert.equal(f.disabled.disabled, false);
  f.svg.click();
  assert.equal(f.api.isOpen(), true); assert.equal(f.document.activeElement, f.rename);
  assert.equal(f.trigger.getAttribute('aria-expanded'), 'true'); assert.equal(f.ribbonCloses(), 1);
  f.trigger.click(); assert.equal(f.api.isOpen(), false);
  f.trigger.setAttribute('aria-disabled', 'true'); f.trigger.click(); assert.equal(f.api.isOpen(), false);
  f.trigger.setAttribute('aria-disabled', 'false'); f.trigger.click(); assert.equal(f.api.isOpen(), true);
});

test('dialog-owned row menus reuse styles without toolbar activation interception', () => {
  const f = harness(); let calls = 0;
  const dialog = f.add(f.document.body, 'dialog', 'mainAccountsDialog');
  const panel = f.add(dialog, 'div', 'maRowMenu', { role: 'menu', class: 'toolbar-menu' });
  const item = f.add(panel, 'button', 'maConvert', { role: 'menuitem' });
  item.onclick = () => calls++;
  item.click();
  assert.equal(calls, 1); assert.equal(f.api.isOpen(), false);
  f.api.open(f.trigger); f.api.close('command');
  item.click(); assert.equal(calls, 2);
  f.copy.onclick = () => calls++;
  f.copy.click(); assert.equal(calls, 2, 'an inactive toolbar-owned item stays blocked');
});

test('keyboard navigation includes unavailable rows, activates once before callback and contains graph shortcuts', () => {
  const f = harness(); let calls = 0, leaked = 0;
  f.document.addEventListener('keydown', () => leaked++);
  f.copy.onclick = () => { assert.equal(f.api.isOpen(), false); calls++; };
  f.trigger.fire('keydown', { key: 'ArrowDown' });
  f.rename.fire('keydown', { key: 'ArrowDown' }); assert.equal(f.document.activeElement, f.disabled);
  f.disabled.fire('keydown', { key: 'Enter' }); assert.equal(f.api.isOpen(), true); assert.equal(calls, 0);
  f.disabled.fire('keydown', { key: 'End' }); assert.equal(f.document.activeElement, f.copy);
  assert.equal(f.copy.fire('keydown', { key: 'z', metaKey: true }).defaultPrevented, true);
  assert.equal(f.copy.fire('keydown', { key: 'y', ctrlKey: true }).defaultPrevented, true);
  f.copy.fire('keydown', { key: 'Enter', repeat: true }); assert.equal(calls, 0);
  f.copy.fire('keydown', { key: 'Enter' }); f.copy.click(); assert.equal(calls, 1); assert.equal(leaked, 0);
  f.trigger.fire('keydown', { key: 'ArrowUp' }); assert.equal(f.document.activeElement, f.copy);
  f.copy.fire('keydown', { key: 'Escape' }); assert.equal(f.document.activeElement, f.trigger); assert.equal(f.api.isOpen(), false);
  assert.equal(f.trigger.fire('keydown', { key: 'Enter', isComposing: true }).defaultPrevented, false);
});

test('outside toolbar actions run in one click and switching never restores stale focus', () => {
  const f = harness(); let action = 0; f.after.onclick = () => action++;
  f.api.open(f.trigger);
  f.after.fire('pointerdown'); f.after.focus(); f.after.click();
  assert.equal(action, 1); assert.equal(f.api.isOpen(), false); assert.equal(f.document.activeElement, f.after);
  f.api.open(f.trigger); f.second.fire('pointerdown'); f.second.click();
  assert.equal(f.api.getState().menuId, 'secondMenu'); assert.equal(f.document.activeElement, f.other);
  assert.equal(f.trigger.getAttribute('aria-expanded'), 'false'); assert.equal(f.panel.hidden, true);
});

test('Tab leaves the menu at its logical toolbar position despite its body portal', () => {
  const f = harness(); f.api.open(f.trigger);
  f.rename.fire('keydown', { key: 'Tab' }); assert.equal(f.document.activeElement, f.after); assert.equal(f.api.isOpen(), false);
  f.api.open(f.trigger); f.rename.fire('keydown', { key: 'Tab', shiftKey: true });
  assert.equal(f.document.activeElement, f.trigger); assert.equal(f.api.isOpen(), false);
});

test('toolbar form keeps dialog semantics, shared portal placement and native field tab stops', () => {
  const f = harness({ withForm: true });
  assert.equal(f.formTrigger.getAttribute('aria-haspopup'), 'dialog');
  assert.equal(f.formTrigger.getAttribute('aria-controls'), 'relationForm');
  assert.equal(f.formPanel.getAttribute('role'), 'dialog');
  assert.equal(f.formPanel.classList.contains('toolbar-menu'), true);
  assert.equal(f.formPanel.parentElement, f.document.body);
  assert.equal(f.formPanel.hidden, true); assert.equal(f.formPanel.inert, true);
  for (const field of [f.currency, f.minimum, f.maximum, f.apply]) assert.equal(field.tabIndex, 0);
  f.formTrigger.click();
  assert.equal(f.document.activeElement, f.currency, 'skip disabled and hidden fields when opening');
  assert.equal(f.formPanel.hidden, false); assert.equal(f.formPanel.inert, false);
  assert.equal(f.formTrigger.getAttribute('aria-expanded'), 'true');
  assert.equal(f.formPanel.style.left, '150px'); assert.equal(f.formPanel.style.top, '56px');
  let calls = 0; f.apply.onclick = () => calls++;
  f.apply.click();
  assert.equal(calls, 1, 'form buttons retain their own business handlers');
  assert.equal(f.api.isOpen(), true, 'the shell does not treat every form button as a menu command');
  f.maximum.focus();
  const escape = f.maximum.fire('keydown', { key: 'Escape' });
  assert.equal(escape.defaultPrevented, true); assert.equal(f.api.isOpen(), false);
  assert.equal(f.document.activeElement, f.formTrigger);
  f.formTrigger.fire('keydown', { key: 'ArrowUp' });
  assert.equal(f.document.activeElement, f.currency, 'a form starts at its first available field even from ArrowUp');
});

test('form editing keys reach local handlers and preserve native defaults without leaking graph shortcuts', () => {
  const f = harness({ withForm: true }); let local = 0, leaked = 0;
  f.document.addEventListener('keydown', () => leaked++);
  f.minimum.addEventListener('keydown', () => local++);
  f.api.open(f.formTrigger); f.minimum.focus();
  const cases = [
    { key: 'ArrowDown' }, { key: 'ArrowUp' }, { key: 'ArrowLeft' }, { key: 'ArrowRight' },
    { key: 'Home' }, { key: 'End' }, { key: ' ' }, { key: 'Enter' },
    { key: 'Backspace' }, { key: 'Delete' }, { key: 'a', ctrlKey: true },
    { key: 'z', ctrlKey: true }, { key: 'Z', metaKey: true, shiftKey: true },
    { key: 'y', ctrlKey: true }, { key: 'z', metaKey: true },
    { key: 'Process', isComposing: true, keyCode: 229 }
  ];
  for (const options of cases) {
    const event = f.minimum.fire('keydown', options);
    assert.equal(event.defaultPrevented, false, JSON.stringify(options));
    assert.equal(f.document.activeElement, f.minimum, JSON.stringify(options));
    assert.equal(f.api.isOpen(), true);
  }
  assert.equal(local, cases.length, 'field-level Enter and editing handlers remain reachable');
  assert.equal(leaked, 0, 'document-level graph undo/delete/navigation receives no form keystrokes');
});

test('Tab advances within a form and closes only at its logical toolbar boundary', () => {
  const f = harness({ withForm: true }); let leaked = 0;
  f.document.addEventListener('keydown', () => leaked++);
  f.api.open(f.formTrigger);
  assert.equal(f.currency.fire('keydown', { key: 'Tab' }).defaultPrevented, false);
  assert.equal(f.api.isOpen(), true);
  f.minimum.focus(); // The browser's normal Tab default moves to the next field.
  assert.equal(f.minimum.fire('keydown', { key: 'Tab', shiftKey: true }).defaultPrevented, false);
  assert.equal(f.api.isOpen(), true); f.currency.focus();
  assert.equal(f.currency.fire('keydown', { key: 'Tab', shiftKey: true }).defaultPrevented, true);
  assert.equal(f.api.isOpen(), false); assert.equal(f.document.activeElement, f.formTrigger);
  f.api.open(f.formTrigger); f.apply.focus();
  assert.equal(f.apply.fire('keydown', { key: 'Tab' }).defaultPrevented, true);
  assert.equal(f.api.isOpen(), false); assert.equal(f.document.activeElement, f.formAfter);
  assert.equal(leaked, 0);
});

test('form dismissal consumes the complete first canvas gesture and can switch to a command menu', () => {
  const f = harness({ withForm: true }); let graphEvents = 0;
  const types = ['pointerdown', 'mousedown', 'pointermove', 'mousemove', 'pointerup', 'mouseup', 'click', 'dblclick'];
  for (const type of types) f.graph.addEventListener(type, () => graphEvents++);
  f.api.open(f.formTrigger);
  for (const type of types) assert.equal(f.node.fire(type).defaultPrevented, true, type);
  assert.equal(graphEvents, 0); assert.equal(f.api.isOpen(), false);
  f.api.open(f.formTrigger); f.trigger.fire('pointerdown'); f.trigger.click();
  assert.equal(f.formPanel.hidden, true); assert.equal(f.formTrigger.getAttribute('aria-expanded'), 'false');
  assert.equal(f.api.getState().menuId, 'canvasMenu'); assert.equal(f.document.activeElement, f.rename);
});

test('first outside graph gesture and its double-click continuation cannot select, drag or activate', () => {
  const f = harness(); let graphEvents = 0;
  const types = ['pointerdown', 'mousedown', 'pointermove', 'mousemove', 'pointerup', 'mouseup', 'click', 'dblclick'];
  for (const type of types) f.graph.addEventListener(type, () => graphEvents++);
  f.api.open(f.trigger);
  for (const type of types.slice(0, -1)) assert.equal(f.node.fire(type).defaultPrevented, true, type);
  assert.equal(f.api.isOpen(), false); assert.equal(graphEvents, 0);
  for (const type of types) assert.equal(f.node.fire(type).defaultPrevented, true, 'second ' + type);
  assert.equal(graphEvents, 0);
  f.advance(600); assert.equal(f.node.fire('pointerdown').defaultPrevented, false); assert.equal(graphEvents, 1);
});

test('long drags remain consumed, release outside the graph is safe, and another location is immediately usable', () => {
  const f = harness(); let actions = 0; f.after.onclick = () => actions++;
  f.api.open(f.trigger); f.node.fire('pointerdown'); f.advance(5000);
  assert.equal(f.node.fire('pointermove', { clientX: 300 }).defaultPrevented, true);
  assert.equal(f.after.fire('pointerup', { clientX: 300 }).defaultPrevented, true);
  assert.equal(f.after.fire('click', { clientX: 300 }).defaultPrevented, true); assert.equal(actions, 0);
  f.after.fire('pointerdown', { clientX: 300 }); f.after.click(); assert.equal(actions, 1);
  f.api.open(f.trigger); f.minimap.fire('pointerdown'); f.minimap.fire('pointerup'); f.minimap.fire('click');
  assert.equal(f.node.fire('pointerdown', { clientX: 400 }).defaultPrevented, false);
});

test('small viewports clamp both dimensions, flip above and have no oversized minimum height', () => {
  const f = harness({ width: 160, height: 100 });
  f.trigger.rect = { left: 135, top: 65, width: 20, height: 25 }; f.api.open(f.trigger);
  assert.equal(f.panel.style.maxWidth, '144px'); assert.equal(f.panel.style.maxHeight, '51px');
  assert.equal(f.panel.style.left, '8px'); assert.equal(f.panel.style.top, '8px');
  assert.equal(f.panel.getAttribute('data-placement'), 'top');
});

test('resize, scroll, focus changes and hidden or moving anchors close without stealing focus', () => {
  const f = harness();
  f.api.open(f.trigger); f.panel.fire('scroll'); assert.equal(f.api.isOpen(), true);
  f.toolbar.fire('scroll'); assert.equal(f.api.isOpen(), false);
  f.api.open(f.trigger); f.window.fire('resize'); assert.equal(f.api.isOpen(), false);
  f.api.open(f.trigger); f.window.fire('fundstrace:workspace-resize'); assert.equal(f.api.isOpen(), false);
  f.api.open(f.trigger); f.after.focus(); assert.equal(f.api.isOpen(), false); assert.equal(f.document.activeElement, f.after);
  f.api.open(f.trigger); f.trigger.hidden = true; f.tick(); assert.equal(f.api.isOpen(), false); assert.equal(f.frames.size, 0);
  f.trigger.hidden = false; f.api.open(f.trigger); f.trigger.rect.left++; f.tick(); assert.equal(f.api.getState().reason, 'anchor-moved');
  f.api.open(f.trigger); f.window.fire('blur'); assert.equal(f.api.isOpen(), false);
  f.api.open(f.trigger); f.document.hidden = true; f.document.fire('visibilitychange'); assert.equal(f.api.isOpen(), false);
});

test('external close keeps an active command form focus, and callbacks can safely open the next menu', () => {
  const f = harness(); const input = f.add(f.document.body, 'input', 'formInput');
  f.rename.onclick = () => input.focus(); f.api.open(f.trigger); f.rename.click();
  assert.equal(f.document.activeElement, input); assert.equal(f.api.isOpen(), false);
  f.api.open(f.trigger); f.document.fire('funds:toolbar-menu-close'); assert.equal(f.api.isOpen(), false);
  f.rename.onclick = () => f.api.open(f.second); f.api.open(f.trigger); f.rename.click();
  assert.equal(f.api.getState().menuId, 'secondMenu'); assert.equal(f.document.activeElement, f.other);
});

test('late-bound menus use current attributes and shared placement also works for non-menu forms', () => {
  const f = harness();
  const lateTrigger = f.add(f.toolbar, 'button', 'lateTrigger', { 'data-toolbar-menu': 'latePanel' });
  const latePanel = f.add(f.toolbar, 'div', 'latePanel');
  const command = f.add(latePanel, 'button', 'lateCommand', { role: 'menuitem', 'aria-busy': 'true' });
  let calls = 0; command.onclick = () => calls++;
  assert.equal(f.api.open('lateTrigger'), true); assert.equal(latePanel.parentElement, f.document.body);
  command.click(); assert.equal(calls, 0); assert.equal(f.api.isOpen(), true);
  command.setAttribute('aria-busy', 'false'); command.fire('keydown', { key: ' ' });
  assert.equal(calls, 1); assert.equal(f.api.isOpen(), false);
  const form = f.add(f.document.body, 'dialog', 'nameForm', { role: 'dialog' });
  f.api.place(form, lateTrigger);
  assert.equal(form.getAttribute('role'), 'dialog'); assert.equal(form.hidden, false);
  assert.equal(form.style.top, '166px'); assert.equal(f.api.isOpen(), false);
});
