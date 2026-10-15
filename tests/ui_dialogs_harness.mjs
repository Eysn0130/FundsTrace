import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/ui-dialogs.js', import.meta.url), 'utf8');

// The script executes unchanged. These DOM boundaries model the behavior it
// relies on: observed style/child changes, connected visibility, inherited inert,
// focused elements and capture/bubble propagation through real parent links.
function harness() {
  let document, context;
  const observers = [], pending = new Set(), closes = new Map(), clicks = new Map();
  let layouts = 0;
  const changed = (node, type, attributeName) => {
    for (const observer of observers) for (const target of observer.targets) {
      if (target.node !== node || !target.options[type]) continue;
      if (type === 'attributes' && target.options.attributeFilter && !target.options.attributeFilter.includes(attributeName)) continue;
      pending.add(observer);
    }
  };
  class Element {
    constructor(tag = 'div', id = '', className = '') {
      this.tagName = tag.toUpperCase(); this.id = id; this.className = className;
      this.children = []; this.parentElement = null; this.dataset = {}; this.attributes = new Map(); this.listeners = new Map();
      this.disabled = false; this._inert = false; this._hidden = false; this.open = false; this.textContent = '';
      this.style = new Proxy({}, { set: (object, key, value) => { object[key] = value; changed(this, 'attributes', 'style'); return true; } });
    }
    get isConnected() { return this === document || !!this.parentElement?.isConnected; }
    get hidden() { return this._hidden; }
    set hidden(value) { this._hidden = Boolean(value); changed(this, 'attributes', 'hidden'); }
    get inert() { return this._inert; }
    set inert(value) {
      this._inert = Boolean(value);
      if (value && this.contains(document?.activeElement)) document.activeElement = document.body;
    }
    get tabIndex() {
      if (this.attributes.has('tabindex')) return Number(this.attributes.get('tabindex'));
      return ['BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'A'].includes(this.tagName) ? 0 : -1;
    }
    set tabIndex(value) { this.attributes.set('tabindex', String(value)); }
    appendChild(node) {
      if (node.parentElement) node.parentElement.children.splice(node.parentElement.children.indexOf(node), 1);
      this.children.push(node); node.parentElement = this; changed(this, 'childList'); return node;
    }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    matches(selector) {
      return selector.split(',').some(part => {
        const value = part.trim(), tag = value.match(/^[\w-]+/)?.[0], id = value.match(/#([\w-]+)/)?.[1];
        if (tag && this.tagName !== tag.toUpperCase()) return false;
        if (id && this.id !== id) return false;
        for (const name of [...value.matchAll(/\.([\w-]+)/g)].map(match => match[1])) {
          if (!this.className.split(/\s+/).includes(name)) return false;
        }
        for (const match of value.matchAll(/\[([\w-]+)\]/g)) {
          const key = match[1];
          const present = key === 'inert' ? this.inert : key === 'open' ? this.open : key.startsWith('data-')
            ? key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()) in this.dataset : this.attributes.has(key);
          if (!present) return false;
        }
        return true;
      });
    }
    closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
    querySelectorAll(selector) {
      return this.children.flatMap(child => [child, ...child.querySelectorAll('*')]).filter(node => node.matches(selector));
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    setAttribute(key, value) { this.attributes.set(key, String(value)); }
    getAttribute(key) { return this.attributes.get(key) ?? null; }
    getClientRects() {
      for (let node = this; node; node = node.parentElement) {
        if (node.hidden || node.style.display === 'none' || (node.tagName === 'DIALOG' && !node.open)) return [];
      }
      return this.isConnected ? [{ width: 100, height: 40 }] : [];
    }
    getBoundingClientRect() { return { left: 100, right: 500, top: 100, bottom: 400, width: 400, height: 300 }; }
    focus() {
      if (!this.isConnected || this.disabled || this.closest('[inert]') || !this.getClientRects().length || document.activeElement === this) return;
      document.activeElement = this; this.fire('focusin');
    }
    addEventListener(type, callback, options = false) {
      const values = this.listeners.get(type) || [];
      values.push({ callback, capture: options === true || !!options.capture }); this.listeners.set(type, values);
    }
    fire(type, options = {}) {
      const event = { type, target: this, key: '', keyCode: 0, isComposing: false, shiftKey: false, button: 0,
        pointerId: 1, clientX: 10, clientY: 10, defaultPrevented: false, stopped: false, immediate: false,
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
    click() { clicks.set(this.id || this, (clicks.get(this.id || this) || 0) + 1); this.fire('click'); }
    close() { this.open = false; }
  }
  document = new Element('#document'); document.body = document.appendChild(new Element('body'));
  document.activeElement = document.body;
  document.getElementById = id => document.querySelector('#' + id);
  const add = (parent, tag, id, className = '') => parent.appendChild(new Element(tag, id, className));
  const main = add(document.body, 'main', 'workspace'), priorInert = add(document.body, 'aside', 'priorInert'); priorInert.inert = true;
  const trigger = add(main, 'button', 'btnFlowOverview');
  add(document.body, 'div', 'toasts'); add(document.body, 'div', 'watermark');
  const modal = (id, closeId) => {
    const overlay = add(document.body, 'div', id, 'modal'); overlay.style.display = 'none';
    const box = add(overlay, 'section', id + 'Box'), title = add(box, 'h2', id + 'Heading'); title.textContent = id;
    const close = add(box, 'button', closeId);
    close.onclick = () => { closes.set(id, (closes.get(id) || 0) + 1); overlay.style.display = 'none'; };
    // Existing click-only dismissers coexist with the capture boundary. They
    // must never run for the overlay click that the new helper consumes.
    overlay.addEventListener('click', event => { if (event.target === overlay) close.click(); });
    return { overlay, box, close };
  };
  const overview = modal('flowOverview', 'btnCloseFlowOverview'), ctx = modal('ctxModal', 'ctxClose');
  const overviewAction = add(overview.box, 'button', 'overviewContext');
  add(ctx.box, 'input', 'ctxInput'); add(ctx.box, 'button', 'ctxTrack'); const suggestions = add(ctx.box, 'div', 'ctxSuggest');
  const input = modal('textInputModal', 'tiCancel');
  const name = add(input.box, 'input', 'tiInput'), ok = add(input.box, 'button', 'tiOk');
  const crop = modal('photoCropModal', 'pcClose'), circle = add(crop.box, 'div', 'pcCircle'); circle.tabIndex = 0;
  const imports = modal('importModal', 'impClose'); const mapping = add(imports.box, 'div', 'impMapping');
  const drawer = add(main, 'aside', 'drawer'); add(drawer, 'div', 'drawerTitle'); add(drawer, 'button', 'btnCloseDrawer');
  const native = add(document.body, 'dialog', 'caseActions', 'action-dialog'); add(native, 'button', 'nativeClose');
  const window = new Element('window');
  class MutationObserver {
    constructor(callback) { this.callback = callback; this.targets = []; observers.push(this); }
    observe(node, options) { this.targets.push({ node, options }); }
  }
  context = vm.createContext({ window, document, MutationObserver, console,
    getComputedStyle: node => ({ display: node.style.display || (node.tagName === 'DIALOG' && !node.open ? 'none' : 'block') }),
    pcState: { cx: .5, cy: .5 }, pcLayout() { layouts++; } });
  vm.runInContext(source, context, { filename: 'ui-dialogs.js' });
  window.fire('DOMContentLoaded');
  const flush = () => {
    for (let count = 0; pending.size && count < 20; count++) {
      const current = [...pending]; pending.clear(); for (const observer of current) observer.callback([]);
    }
    assert.equal(pending.size, 0, 'Mutation observers should converge');
  };
  const open = (item, from = trigger, preFocus = null) => {
    from.focus(); item.overlay.style.display = 'flex'; preFocus?.focus(); flush();
  };
  return { document, window, context, main, priorInert, trigger, overview, ctx, overviewAction, input, name, ok, crop, circle, imports, mapping,
    suggestions, native, drawer, closes, clicks, flush, open, add, layouts: () => layouts };
}

test('modal opening blocks background and closing restores original inert values and overview trigger focus', () => {
  const f = harness();
  // The table workspace focuses Close before the style observer runs. Focus
  // restoration still belongs to the original overview button after inert clears.
  f.open(f.overview, f.trigger, f.overview.close);
  assert.equal(f.main.inert, true); assert.equal(f.overview.overlay.inert, false); assert.equal(f.ctx.overlay.inert, true);
  assert.equal(f.document.activeElement, f.overview.close);
  assert.equal(f.overview.overlay.getAttribute('role'), 'dialog');
  assert.equal(f.overview.overlay.getAttribute('aria-modal'), 'true');
  assert.equal(f.overview.overlay.getAttribute('aria-labelledby'), 'flowOverviewHeading');
  f.overview.close.click(); f.flush();
  assert.equal(f.main.inert, false); assert.equal(f.priorInert.inert, true);
  assert.equal(f.document.activeElement, f.trigger);
});

test('nested Escape closes only the top modal and restores focus within its parent before returning to the page', () => {
  const f = harness(); f.open(f.overview); f.open(f.ctx, f.overviewAction);
  let leakedEscape = 0; f.document.addEventListener('keydown', () => leakedEscape++);
  assert.equal(f.overview.overlay.inert, true); assert.equal(f.ctx.overlay.inert, false);
  const first = f.ctx.close.fire('keydown', { key: 'Escape' }); f.flush();
  assert.equal(first.defaultPrevented, true); assert.equal(leakedEscape, 0);
  assert.equal(f.closes.get('ctxModal'), 1); assert.equal(f.closes.get('flowOverview') || 0, 0);
  assert.equal(f.overview.overlay.style.display, 'flex'); assert.equal(f.overview.overlay.inert, false);
  assert.equal(f.document.activeElement, f.overviewAction);
  f.overviewAction.fire('keydown', { key: 'Escape' }); f.flush();
  assert.equal(f.closes.get('flowOverview'), 1); assert.equal(f.document.activeElement, f.trigger);
  assert.equal(f.main.inert, false);
});

test('Tab wraps within the top modal, skips hidden and disabled controls and contains focus attempts', () => {
  const f = harness();
  const hidden = f.add(f.ctx.box, 'button', 'hiddenControl'); hidden.hidden = true;
  const disabled = f.add(f.ctx.box, 'button', 'disabledControl'); disabled.disabled = true;
  f.open(f.ctx);
  const last = f.document.getElementById('ctxTrack'); last.focus();
  const forward = last.fire('keydown', { key: 'Tab' });
  assert.equal(forward.defaultPrevented, true); assert.equal(f.document.activeElement, f.ctx.close);
  const backward = f.ctx.close.fire('keydown', { key: 'Tab', shiftKey: true });
  assert.equal(backward.defaultPrevented, true); assert.equal(f.document.activeElement, last);
  const middle = f.document.getElementById('ctxInput'); middle.focus();
  assert.equal(middle.fire('keydown', { key: 'Tab' }).defaultPrevented, false);
  const outside = f.add(f.document.body, 'button', 'newOutside'); outside.focus();
  assert.equal(f.document.activeElement, f.ctx.close);
  f.ctx.close.disabled = true; middle.disabled = true; last.disabled = true;
  const empty = f.ctx.overlay.fire('keydown', { key: 'Tab' });
  assert.equal(empty.defaultPrevented, true); assert.equal(f.document.activeElement, f.ctx.overlay);
});

test('only a matching primary pointer down/up on the actual outside mask dismisses a modal', () => {
  const cases = [
    { label: 'matching outside click', down: 'mask', up: 'mask', closes: true },
    { label: 'click without pointer gesture', closes: false },
    { label: 'inner drag ending outside', down: 'inside', up: 'mask', closes: false },
    { label: 'outside drag ending inside', down: 'mask', up: 'inside', closes: false },
    { label: 'mask drag over movement threshold', down: 'mask', up: 'mask', upX: 40, closes: false },
    { label: 'different pointer', down: 'mask', up: 'mask', pointerId: 2, closes: false },
    { label: 'secondary button', down: 'mask', up: 'mask', button: 2, closes: false },
    { label: 'cancelled pointer', down: 'mask', up: 'mask', cancel: true, closes: false },
  ];
  for (const item of cases) {
    const f = harness(); f.open(f.ctx);
    const target = kind => kind === 'mask' ? f.ctx.overlay : f.ctx.box;
    if (item.down) target(item.down).fire('pointerdown', { button: item.button ?? 0, clientX: 10, clientY: 10, pointerId: 1 });
    if (item.cancel) f.ctx.overlay.fire('pointercancel');
    if (item.up) target(item.up).fire('pointerup', { clientX: item.upX ?? 12, clientY: 12, pointerId: item.pointerId ?? 1 });
    const click = f.ctx.overlay.fire('click'); f.flush();
    assert.equal(click.defaultPrevented, true, item.label);
    assert.equal(f.closes.get('ctxModal') || 0, item.closes ? 1 : 0, item.label);
  }
});

test('window resizing leaves modal state, background protection and focused control intact', () => {
  const f = harness(); f.open(f.ctx); const input = f.document.getElementById('ctxInput'); input.focus();
  f.window.fire('resize'); f.flush();
  assert.equal(f.ctx.overlay.style.display, 'flex'); assert.equal(f.main.inert, true);
  assert.equal(f.document.activeElement, input); assert.equal(f.closes.size, 0);
});

test('IME Enter/Escape never reach the legacy naming handler and retain their browser default', () => {
  for (const key of ['Enter', 'Escape']) for (const composition of [{ isComposing: true }, { keyCode: 229 }]) {
    const f = harness(); let submitted = 0, cancelled = 0;
    f.name.onkeydown = event => { if (event.key === 'Enter') submitted++; if (event.key === 'Escape') cancelled++; };
    f.open(f.input);
    assert.equal(f.document.activeElement, f.name);
    const event = f.name.fire('keydown', { key, ...composition }); f.flush();
    assert.equal(event.defaultPrevented, false); assert.equal(event.immediate, true);
    assert.equal(submitted, 0); assert.equal(cancelled, 0); assert.equal(f.closes.size, 0);
    assert.equal(f.input.overlay.style.display, 'flex'); assert.equal(f.document.activeElement, f.name);
  }
});

test('ordinary naming Enter reaches the target handler while ordinary Escape cancels once at the modal boundary', () => {
  const f = harness(); let submitted = 0, targetEscape = 0;
  f.name.onkeydown = event => {
    if (event.key === 'Enter') submitted++;
    if (event.key === 'Escape') targetEscape++;
  };
  f.open(f.input);
  const enter = f.name.fire('keydown', { key: 'Enter' });
  assert.equal(enter.defaultPrevented, false); assert.equal(submitted, 1);
  assert.equal(f.input.overlay.style.display, 'flex');
  const escape = f.name.fire('keydown', { key: 'Escape' }); f.flush();
  assert.equal(escape.defaultPrevented, true); assert.equal(targetEscape, 0);
  assert.equal(f.closes.get('textInputModal'), 1); assert.equal(f.document.activeElement, f.trigger);
});

test('native action dialog owns Escape when open instead of closing the underlying custom modal', () => {
  const f = harness(); f.open(f.overview); f.native.open = true;
  const event = f.overview.close.fire('keydown', { key: 'Escape' });
  assert.equal(event.defaultPrevented, false); assert.equal(f.closes.size, 0);
});

test('new recommendation items expose keyboard button semantics and Enter/Space invoke the existing click action', () => {
  const f = harness(); const item = f.add(f.suggestions, 'div', 'syntheticSuggestion', 'sg-item');
  const label = f.add(item, 'span', 'syntheticSuggestionText', 'sg-main'); label.textContent = 'Synthetic source';
  let selected = false; item.onclick = () => { selected = !selected; }; f.flush(); f.open(f.ctx);
  assert.equal(item.tabIndex, 0); assert.equal(item.getAttribute('role'), 'button');
  assert.equal(item.getAttribute('aria-label'), '勾选推荐流水：Synthetic source');
  item.focus(); assert.equal(f.document.activeElement, item);
  assert.equal(item.fire('keydown', { key: 'Enter' }).defaultPrevented, true); assert.equal(selected, true);
  assert.equal(item.fire('keydown', { key: ' ' }).defaultPrevented, true); assert.equal(selected, false);
  assert.equal(f.clicks.get('syntheticSuggestion'), 2);
});

test('crop arrow keys move the selected center, Shift accelerates and bounds remain within the image', () => {
  const f = harness(); f.open(f.crop); f.circle.focus();
  assert.equal(f.circle.fire('keydown', { key: 'ArrowRight' }).defaultPrevented, true);
  assert.equal(f.context.pcState.cx, .51);
  f.circle.fire('keydown', { key: 'ArrowUp', shiftKey: true }); assert.equal(f.context.pcState.cy, .45);
  f.context.pcState.cx = .001; f.circle.fire('keydown', { key: 'ArrowLeft' }); assert.equal(f.context.pcState.cx, 0);
  f.context.pcState.cy = .999; f.circle.fire('keydown', { key: 'ArrowDown', shiftKey: true }); assert.equal(f.context.pcState.cy, 1);
  assert.equal(f.layouts(), 4);
  assert.equal(f.circle.fire('keydown', { key: 'Enter' }).defaultPrevented, false); assert.equal(f.layouts(), 4);
});

test('new import mapping fields bind their visible labels to the actual select controls', () => {
  const f = harness(), field = f.add(f.mapping, 'div', 'mappingField'), label = f.add(field, 'label', 'mappingLabel');
  const select = f.add(field, 'select', ''); select.dataset.field = 'payer_account'; f.flush();
  assert.equal(select.id, 'map-payer_account'); assert.equal(label.htmlFor, 'map-payer_account');
});
