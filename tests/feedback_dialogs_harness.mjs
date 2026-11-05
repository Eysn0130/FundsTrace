import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/feedback-dialogs.js', import.meta.url), 'utf8');
const initialScope = () => ({ caseId: 'A', generation: 1, canvasId: 'canvas-A', canvasGeneration: 1 });

// Execute the shipped script unchanged. This DOM models connected ancestry,
// inherited inert, native cancel/default keyboard actions, close and focus.
function harness() {
  let document, current = initialScope();
  const focuses = [], listeners = new Map();
  function event(type, target, detail = {}) {
    return { type, target, defaultPrevented: false, propagationStopped: false, ...detail,
      preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.propagationStopped = true; } };
  }
  class Element {
    constructor(tag) {
      this.tagName = tag.toUpperCase(); this.children = []; this.parentElement = null;
      this.attributes = new Map(); this.listeners = new Map(); this.textContent = ''; this.className = '';
      this.disabled = false; this.hidden = false; this.inert = false; this.open = false; this.closeCount = 0;
    }
    get isConnected() { return this === document.body || !!this.parentElement?.isConnected; }
    append(...nodes) { for (const node of nodes) this.appendChild(node); }
    appendChild(node) { node.remove(); this.children.push(node); node.parentElement = this; return node; }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    remove() {
      if (this.contains(document.activeElement)) document.activeElement = document.body;
      if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(node => node !== this);
      this.parentElement = null;
    }
    closest(selector) {
      assert.equal(selector, '[inert]');
      return this.inert || this.attributes.has('inert') ? this : this.parentElement?.closest(selector) || null;
    }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    addEventListener(name, callback) {
      if (!this.listeners.has(name)) this.listeners.set(name, new Set());
      this.listeners.get(name).add(callback);
    }
    fire(name, detail = {}) {
      const value = event(name, this, detail);
      for (const callback of [...(this.listeners.get(name) || [])]) callback(value);
      return value;
    }
    click() { if (!this.disabled) this.onclick?.(event('click', this)); }
    focus(options) {
      if (!this.isConnected || this.disabled || this.closest('[inert]')) return;
      for (let node = this; node; node = node.parentElement) if (node.hidden) return;
      document.activeElement = this; focuses.push({ node: this, options });
    }
    showModal() { assert.equal(this.isConnected, true); this.open = true; }
    close() { if (!this.open) return; this.open = false; this.closeCount++; this.fire('close'); }
  }
  document = { createElement: tag => new Element(tag) };
  document.body = new Element('body'); document.activeElement = document.body;
  const holder = new Element('section'), trigger = new Element('button');
  document.body.append(holder); holder.append(trigger); trigger.focus();
  const window = {
    addEventListener(name, callback) {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name).add(callback);
    },
    removeEventListener(name, callback) { listeners.get(name)?.delete(callback); },
  };
  vm.runInNewContext(source, { window, document,
    isCurrentCase: scope => ['caseId', 'generation', 'canvasId', 'canvasGeneration'].every(key => scope[key] === current[key]),
  });
  const api = window.FundsFeedbackDialogs;
  function opened() {
    const dialog = document.body.children.find(node => node.tagName === 'DIALOG');
    if (!dialog) return null;
    const footer = dialog.children.find(node => node.tagName === 'FOOTER');
    return { dialog, cancel: footer.children[0], accept: footer.children[1] };
  }
  return { api, document, holder, trigger, focuses, opened,
    confirm: (extra = {}) => api.confirm({ title: '删除合成批次', message: '仅测试确认决定。', accept: '删除批次', danger: true, scope: { ...current }, ...extra }),
    bind(scope) { current = { ...scope }; for (const callback of [...(listeners.get('funds:feedback-binding') || [])]) callback(event('funds:feedback-binding', window)); },
    scope(scope) { current = { ...scope }; },
    listenerCount: () => listeners.get('funds:feedback-binding')?.size || 0,
    press(dialog, key, isComposing = false) {
      const keydown = dialog.fire('keydown', { key, isComposing });
      if (!keydown.defaultPrevented) {
        if (key === 'Escape') { const cancel = dialog.fire('cancel'); if (!cancel.defaultPrevented) dialog.close(); }
        else if (key === 'Enter') document.activeElement?.click();
      }
      return keydown;
    },
  };
}

test('confirmation starts on cancel; Escape resolves false without authorizing a write', async () => {
  const h = harness(); let writes = 0;
  const result = h.confirm().then(accepted => { if (accepted) writes++; return accepted; });
  const { dialog, cancel } = h.opened();
  assert.equal(h.document.activeElement, cancel); assert.equal(cancel.textContent, '取消');
  assert.equal(dialog.attributes.get('aria-labelledby'), dialog.children[0].id);
  assert.equal(h.listenerCount(), 1);
  h.press(dialog, 'Escape');
  assert.equal(await result, false); assert.equal(writes, 0); assert.equal(h.opened(), null);
  assert.equal(h.listenerCount(), 0); assert.equal(h.document.activeElement, h.trigger);
});

test('accept resolves true once and restores the original connected trigger', async () => {
  const h = harness(); let writes = 0;
  const result = h.confirm().then(accepted => { if (accepted) writes++; return accepted; });
  const { dialog, accept } = h.opened();
  accept.click(); accept.click(); dialog.fire('close'); h.api.revoke();
  assert.equal(await result, true); assert.equal(writes, 1); assert.equal(dialog.closeCount, 1);
  assert.equal(h.document.activeElement, h.trigger);
  assert.equal(h.focuses.at(-1).options.preventScroll, true); assert.equal(h.listenerCount(), 0);
});

test('A-B-A binding revocation resolves false and old buttons cannot authorize a new confirmation', async () => {
  const h = harness(), a = initialScope();
  const first = h.confirm(), old = h.opened();
  h.bind({ caseId: 'B', generation: 2, canvasId: 'canvas-B', canvasGeneration: 2 });
  assert.equal(await first, false); assert.equal(h.opened(), null);
  h.bind({ ...a, generation: 3, canvasGeneration: 3 });
  const second = h.confirm(), next = h.opened();
  old.accept.click(); old.dialog.fire('close');
  assert.equal(h.opened().dialog, next.dialog); assert.equal(next.dialog.open, true);
  next.cancel.click(); assert.equal(await second, false);
  assert.equal(await h.confirm({ scope: a }), false); assert.equal(h.opened(), null);
});

test('accept rechecks the binding even if a change notification has not yet arrived', async () => {
  const h = harness(), result = h.confirm(), { accept } = h.opened();
  h.scope({ ...initialScope(), generation: 2 });
  accept.click(); assert.equal(await result, false); assert.equal(h.opened(), null);
});

test('IME Escape and Enter suppress native cancellation and button activation', async () => {
  const h = harness(); let settled = false;
  const result = h.confirm().then(value => { settled = true; return value; });
  const { dialog, accept } = h.opened(); accept.focus();
  for (const key of ['Escape', 'Enter']) {
    const keydown = h.press(dialog, key, true);
    assert.equal(keydown.defaultPrevented, true); assert.equal(keydown.propagationStopped, true);
    await Promise.resolve(); assert.equal(settled, false); assert.equal(dialog.open, true);
  }
  h.press(dialog, 'Enter'); assert.equal(await result, true);
});

test('replacing a confirmation resolves the prior promise false and ignores its late close or click', async () => {
  const h = harness(), first = h.confirm(), old = h.opened();
  const second = h.confirm({ title: '另一项合成确认' }), next = h.opened();
  assert.equal(await first, false); assert.notEqual(next.dialog, old.dialog); assert.equal(h.listenerCount(), 1);
  old.accept.click(); old.cancel.click(); old.dialog.fire('close');
  assert.equal(h.opened().dialog, next.dialog); assert.equal(next.dialog.open, true);
  next.accept.click(); assert.equal(await second, true); assert.equal(h.listenerCount(), 0);
});

test('native close and repeated revoke settle once and never refocus an invalid trigger', async () => {
  for (const invalid of ['disconnected', 'disabled', 'inert', 'inert ancestor']) {
    const h = harness(), result = h.confirm(), { dialog, accept } = h.opened();
    const before = h.focuses.filter(entry => entry.node === h.trigger).length;
    if (invalid === 'disconnected') h.trigger.remove();
    else if (invalid === 'disabled') h.trigger.disabled = true;
    else if (invalid === 'inert') h.trigger.inert = true;
    else h.holder.inert = true;
    dialog.close(); h.api.revoke(); h.api.revoke(); dialog.fire('close'); accept.click();
    assert.equal(await result, false, invalid); assert.equal(h.opened(), null, invalid);
    assert.equal(h.focuses.filter(entry => entry.node === h.trigger).length, before, invalid);
    assert.equal(h.document.activeElement, h.document.body, invalid); assert.equal(h.listenerCount(), 0, invalid);
  }
});

test('explicit revoke is idempotent and restores a still-valid trigger once', async () => {
  const h = harness(), result = h.confirm(), { dialog, accept } = h.opened();
  const before = h.focuses.filter(entry => entry.node === h.trigger).length;
  h.api.revoke(); h.api.revoke(); dialog.fire('close'); accept.click();
  assert.equal(await result, false); assert.equal(dialog.closeCount, 1);
  assert.equal(h.document.activeElement, h.trigger);
  assert.equal(h.focuses.filter(entry => entry.node === h.trigger).length, before + 1);
  assert.equal(h.listenerCount(), 0);
});
