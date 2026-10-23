import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { create } = require('../static/entity-picker.js');
const flush = async () => { for (let step = 0; step < 20; step++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

class Element {
  constructor(tag, doc) {
    this.tagName = tag.toUpperCase(); this.ownerDocument = doc; this.parentElement = null; this.children = [];
    this.attributes = new Map(); this.listeners = new Map(); this.style = {}; this.dataset = {}; this.className = ''; this.hidden = false;
    this.value = ''; this.disabled = false; this.readOnly = false; this.scrollTop = 0; this.scrollLeft = 0; this.selectionStart = this.selectionEnd = 0; this._text = '';
  }
  get classList() { return { contains: name => this.className.split(/\s+/).includes(name) }; }
  get isConnected() { return this === this.ownerDocument.body || !!this.parentElement?.isConnected; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  removeAttribute(name) { this.attributes.delete(name); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(node => node.textContent).join(''); }
  appendChild(node) { this.children.push(node); node.parentElement = this; return node; }
  append(...nodes) { nodes.forEach(node => this.appendChild(node)); }
  prepend(node) { this.children.unshift(node); node.parentElement = this; }
  replaceChildren(...nodes) { this.children.forEach(node => { node.parentElement = null; }); this.children = []; this._text = ''; this.append(...nodes); }
  remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(node => node !== this); this.parentElement = null; }
  contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  matches(selector) {
    if (selector === '[hidden], [inert]') return this.hidden || this.attributes.has('inert');
    if (selector.startsWith('.')) return this.classList.contains(selector.slice(1));
    return this.tagName.toLowerCase() === selector;
  }
  closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
  all(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.all(selector)]); }
  addEventListener(type, listener) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(listener); }
  removeEventListener(type, listener) { this.listeners.set(type, (this.listeners.get(type) || []).filter(value => value !== listener)); }
  dispatchEvent(event) {
    if (!event.target) event.target = this;
    for (const listener of this.listeners.get(event.type) || []) { listener(event); if (event.immediateStopped) break; }
    return !event.defaultPrevented;
  }
  emit(type, properties = {}) {
    const event = { type, target: this, defaultPrevented: false, propagationStopped: false, immediateStopped: false,
      preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.propagationStopped = true; },
      stopImmediatePropagation() { this.immediateStopped = true; this.propagationStopped = true; }, ...properties };
    this.dispatchEvent(event); return event;
  }
  focus() { this.ownerDocument.activeElement = this; }
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  setRangeText(value, start, end) { this.value = this.value.slice(0, start) + value + this.value.slice(end); this.selectionStart = this.selectionEnd = start + value.length; }
  showPopover() { this.nativeOpen = true; }
  hidePopover() { this.nativeOpen = false; }
  get clientWidth() {
    if (this.tagName === 'INPUT' || this.tagName === 'TEXTAREA') return this.ownerDocument.anchor.width;
    if (this.className === 'fep-popup') return parseFloat(this.style.width) || 420;
    return this.parentElement?.clientWidth || 420;
  }
  get scrollWidth() { return this.style.flexWrap === 'nowrap' ? this.textContent.length * 7.2 + 30 : this.clientWidth; }
  get clientHeight() { return this.className === 'fep-scroll' ? Math.min(this.scrollHeight, parseFloat(this.style.maxHeight) || 280) : this.getBoundingClientRect().height; }
  get scrollHeight() { return this.children.reduce((sum, child) => sum + child.getBoundingClientRect().height, 0); }
  getBoundingClientRect() {
    if (this.tagName === 'INPUT' || this.tagName === 'TEXTAREA') return { ...this.ownerDocument.anchor, right: this.ownerDocument.anchor.left + this.ownerDocument.anchor.width, bottom: this.ownerDocument.anchor.top + this.ownerDocument.anchor.height };
    let height = 0;
    if (this.hidden) height = 0;
    else if (this.classList.contains('fep-offscreen')) height = 1;
    else if (this.classList.contains('fep-option')) height = this.dataset.layout === 'inline' ? 34 : 54;
    else if (this.className === 'fep-spacer') height = parseFloat(this.style.height) || 0;
    else if (this.className === 'fep-feedback') height = 34;
    else if (this.className === 'fep-popup') height = Math.min(this.children.reduce((sum, child) => sum + (child.className === 'fep-scroll' ? child.clientHeight : child.getBoundingClientRect().height), 2), parseFloat(this.style.maxHeight) || 360);
    return { left: parseFloat(this.style.left) || 0, top: parseFloat(this.style.top) || 0, width: this.clientWidth, height, right: this.clientWidth, bottom: height };
  }
}

function harness({ search, ...extra } = {}) {
  const timers = new Map(); let timerId = 0;
  const doc = { anchor: { left: 100, top: 100, width: 410, height: 32 } };
  const windowEvents = new Element('window', doc), documentEvents = new Element('document', doc);
  const win = Object.assign(windowEvents, {
    innerWidth: 1200, innerHeight: 800, AbortController,
    Event: class { constructor(type, init) { this.type = type; Object.assign(this, init); } },
    setTimeout(fn) { timers.set(++timerId, fn); return timerId; }, clearTimeout(id) { timers.delete(id); },
    requestAnimationFrame(fn) { timers.set(++timerId, fn); return timerId; }, cancelAnimationFrame(id) { timers.delete(id); },
    getComputedStyle() { return { font: '13px system-ui', lineHeight: '20px', padding: '5px 8px', borderWidth: '1px', letterSpacing: 'normal', textAlign: 'start' }; },
  });
  doc.defaultView = win; doc.body = new Element('body', doc);
  doc.createElement = tag => new Element(tag, doc); doc.createTextNode = value => { const node = new Element('#text', doc); node.textContent = value; return node; };
  doc.addEventListener = documentEvents.addEventListener.bind(documentEvents); doc.removeEventListener = documentEvents.removeEventListener.bind(documentEvents);
  doc.emit = documentEvents.emit.bind(documentEvents);
  const form = doc.body.appendChild(new Element('form', doc)), label = form.appendChild(new Element('label', doc));
  const input = label.appendChild(new Element('input', doc)); input.setAttribute('aria-label', '查询账户'); doc.activeElement = input;
  const calls = [], picks = [], submits = [];
  let binding = 'case:A/generation:1';
  const picker = create({ input, scope: () => binding, search: request => { calls.push(request); return search ? search(request) : Promise.resolve({ items: [], hasMore: false }); },
    kind: 'account', onSelect: item => picks.push(item), onSubmit: (...args) => submits.push(args), ...extra });
  const popup = form.all('.fep-popup')[0], list = form.all('.fep-scroll')[0], ghost = form.all('.fep-ghost')[0];
  const type = value => { input.value = value; input.setSelectionRange(value.length, value.length); input.emit('input'); };
  return { picker, input, popup, list, ghost, calls, picks, submits, doc, win, form, label, type,
    bind(value) { binding = value; }, options() { return list.all('.fep-option'); },
    timers() { const pending = [...timers.values()]; timers.clear(); pending.forEach(fn => fn()); },
  };
}
const item = (index, override = {}) => ({ key: 'k' + index, label: '张三丰', value: '000000000000' + String(index).padStart(4, '0'), account: '000000000000' + String(index).padStart(4, '0'), idNo: 'DEMO-ID-' + index, matchedField: 'name', matchedText: '张三丰', ...override });

test('typing revokes old options immediately; debounce coalesces and old responses cannot repopulate', async () => {
  const pending = [];
  const h = harness({ search: () => { const wait = deferred(); pending.push(wait); return wait.promise; } });
  h.type('张'); assert.equal(h.calls.length, 0); h.timers(); assert.equal(h.calls.length, 1);
  pending[0].resolve({ items: [item(1)], hasMore: false }); await flush(); assert.equal(h.options().length, 1);
  h.type('李'); assert.equal(h.options().length, 0); assert.equal(h.input.getAttribute('aria-expanded'), 'false');
  h.timers(); h.type('王'); h.timers(); assert.equal(h.calls[1].signal.aborted, true);
  pending[2].resolve({ items: [item(3, { label: '王' })], hasMore: false }); await flush();
  pending[1].resolve({ items: [item(2, { label: '李' })], hasMore: false }); await flush();
  assert.match(h.options()[0].textContent, /王/); assert.doesNotMatch(h.options()[0].textContent, /李/);
});

test('null scope and A-B-A generation changes fail closed, including a request ignoring abort', async () => {
  const wait = deferred(); const h = harness({ debounce: 0, search: () => wait.promise });
  h.bind(null); h.type('张'); assert.equal(h.calls.length, 0);
  h.bind('A:1'); h.type('张'); h.bind('B:2'); h.picker.invalidate(); h.bind('A:3');
  wait.resolve({ items: [item(1)], hasMore: false }); await flush(); assert.equal(h.options().length, 0); assert.equal(h.input.getAttribute('aria-expanded'), 'false');
});

test('pagination uses the provider cursor, preserves scroll and active identity, and never declares loaded pages unique', async () => {
  const h = harness({ debounce: 0, search: ({ offset }) => Promise.resolve(offset === 0
    ? { items: Array.from({ length: 30 }, (_, index) => item(index)), hasMore: true, nextOffset: 90 }
    : { items: [item(29), item(90)], hasMore: false, nextOffset: 92 }) });
  h.type('张'); await flush(); h.list.scrollTop = 220; const oldTop = h.list.scrollTop;
  h.input.emit('keydown', { key: 'End' }); await flush();
  assert.equal(h.calls[1].offset, 90); assert.ok(h.list.scrollTop >= oldTop);
  assert.equal(h.input.getAttribute('aria-activedescendant').endsWith('-29'), true);
  assert.equal(h.options().filter(row => row.getAttribute('aria-posinset') === '30').length, 1);
  assert.equal(h.options()[0].getAttribute('aria-setsize'), '31');
});

test('thousands of loaded candidates keep bounded DOM, wheel does not cancel native scrolling, keyboard target remains represented', async () => {
  const h = harness({ debounce: 0, search: ({ offset, limit }) => Promise.resolve({ items: Array.from({ length: limit }, (_, index) => item(offset + index)), hasMore: offset + limit < 6000, nextOffset: offset + limit }) });
  h.type('张'); await flush();
  for (let page = 0; page < 100; page++) { h.input.emit('keydown', { key: 'End' }); await flush(); assert.ok(h.options().length <= 71); }
  assert.equal(h.calls.length, 101); assert.ok(h.options().length <= 71);
  const activeId = h.input.getAttribute('aria-activedescendant');
  h.list.scrollTop = 0; h.list.emit('scroll'); assert.equal(h.list.scrollTop, 0); assert.ok(h.options().some(row => row.id === activeId));
  const event = h.popup.emit('wheel'); assert.equal(event.propagationStopped, true); assert.equal(event.defaultPrevented, false);
  h.input.emit('keydown', { key: 'ArrowUp' }); assert.ok(h.list.scrollTop > 0); assert.ok(h.options().length <= 71);
});

test('page failure preserves loaded rows; retry requests the same offset rather than restarting or skipping', async () => {
  let failed = false;
  const h = harness({ debounce: 0, search: ({ offset }) => {
    if (offset === 0) return Promise.resolve({ items: [item(1)], hasMore: true, nextOffset: 30 });
    if (!failed) { failed = true; return Promise.reject(new Error('synthetic timeout')); }
    return Promise.resolve({ items: [item(2)], hasMore: false, nextOffset: 31 });
  } });
  h.type('张'); await flush(); h.input.emit('keydown', { key: 'End' }); await flush(); assert.equal(h.options().length, 1);
  const retry = h.popup.all('button')[0]; assert.equal(retry.textContent, '重试'); retry.emit('click'); await flush();
  assert.deepEqual(h.calls.map(call => call.offset), [0, 30, 30]); assert.equal(h.options().length, 2);
});

test('selection preserves complete strings and replaces only the current textarea token; plain Enter stays native without submit', async () => {
  const h = harness({ debounce: 0, ghost: false, onSubmit: undefined, token: input => ({ query: input.value.slice(3), start: 3, end: input.value.length }), search: () => Promise.resolve({ items: [item(1)], hasMore: false }) });
  h.input.tagName = 'TEXTAREA'; h.type('甲\n 张'); await flush();
  const enter = h.input.emit('keydown', { key: 'Enter' }); assert.equal(enter.defaultPrevented, false);
  h.input.emit('keydown', { key: 'ArrowDown' }); h.input.emit('keydown', { key: 'Enter' });
  assert.equal(h.input.value, '甲\n 0000000000000001'); assert.equal(h.picks[0].account, '0000000000000001'); assert.equal(h.submits.length, 0);
  assert.equal(h.picker.getSelected().key, 'k1'); h.picker.open(); await flush(); assert.equal(h.picker.getSelected().key, 'k1');
  h.bind('case:B'); assert.equal(h.picker.getSelected(), null);
});

test('moving the textarea caret between tokens revokes old candidates without changing text; separator selections remain native', async () => {
  const waits = [];
  const wordAtCaret = input => {
    const value = input.value, separator = /[,，;；\s]/u;
    let start = input.selectionStart, end = input.selectionEnd;
    if (separator.test(value.slice(start, end))) return { query: '', start, end };
    while (start > 0 && !separator.test(value[start - 1])) start--;
    while (end < value.length && !separator.test(value[end])) end++;
    return { query: value.slice(start, end), start, end };
  };
  const h = harness({ debounce: 0, ghost: false, token: wordAtCaret, onSubmit: undefined,
    search: () => { const wait = deferred(); waits.push(wait); return wait.promise; } });
  h.input.tagName = 'TEXTAREA'; h.type('张,李');
  assert.equal(h.calls[0].query, '李');
  waits[0].resolve({ items: [item(1, { label: '李' })], hasMore: false }); await flush(); assert.equal(h.options().length, 1);
  h.input.setSelectionRange(1, 1); h.input.emit('click');
  assert.equal(h.input.value, '张,李'); assert.equal(h.calls[1].query, '张'); assert.equal(h.options().length, 0);
  h.input.setSelectionRange(3, 3); h.input.emit('keyup', { key: 'ArrowRight' });
  assert.equal(h.calls[1].signal.aborted, true); assert.equal(h.calls[2].query, '李');
  waits[1].resolve({ items: [item(2, { label: '张' })], hasMore: false }); await flush(); assert.equal(h.options().length, 0);
  waits[2].resolve({ items: [item(3, { label: '李' })], hasMore: false }); await flush(); assert.match(h.options()[0].textContent, /李/);
  h.input.setSelectionRange(0, 3); h.input.emit('select'); assert.equal(h.options().length, 0); assert.equal(h.calls.length, 3);
  assert.equal(h.input.getAttribute('aria-expanded'), 'false');
  h.input.setSelectionRange(1, 1); h.doc.emit('selectionchange'); assert.equal(h.calls[3].query, '张');
  assert.equal(h.input.emit('keydown', { key: 'Home' }).defaultPrevented, false);
  assert.equal(h.input.emit('keydown', { key: 'End' }).defaultPrevented, false);
});

test('same-text tokens in different positions reject earlier responses and choose only the current token', async () => {
  const waits = [];
  const h = harness({ debounce: 0, ghost: false, token: input => input.selectionStart < 2 ? { query: '张', start: 0, end: 1 } : { query: '张', start: 2, end: 3 },
    search: () => { const wait = deferred(); waits.push(wait); return wait.promise; } });
  h.input.tagName = 'TEXTAREA'; h.type('张,张'); h.input.setSelectionRange(1, 1); h.input.emit('click');
  assert.equal(h.calls.length, 2); assert.equal(h.calls[0].signal.aborted, true);
  waits[0].resolve({ items: [item(1)], hasMore: false }); await flush(); assert.equal(h.options().length, 0);
  waits[1].resolve({ items: [item(2)], hasMore: false }); await flush();
  h.input.emit('keydown', { key: 'ArrowDown' }); h.input.emit('keydown', { key: 'Enter' });
  assert.equal(h.input.value, '0000000000000002,张');
  const calls = h.calls.length; h.input.emit('select'); h.doc.emit('selectionchange'); assert.equal(h.calls.length, calls);
});

test('short pages continue filling a near-boundary viewport without a new wheel event; duplicate-only pages do not spin', async () => {
  const h = harness({ debounce: 0, search: ({ offset }) => Promise.resolve({ items: [item(offset)], hasMore: offset < 8, nextOffset: offset + 1 }) });
  h.type('张'); await flush();
  assert.ok(h.calls.length > 1); assert.equal(h.list.scrollTop, 0); assert.ok(h.calls.length <= 9);
  const duplicate = harness({ debounce: 0, search: ({ offset }) => Promise.resolve({ items: [item(1)], hasMore: true, nextOffset: offset + 1 }) });
  duplicate.type('张'); await flush(); assert.equal(duplicate.calls.length, 2); assert.equal(duplicate.options().length, 1);
});

test('ghost is prefix-only and follows the keyboard candidate; Shift Enter only writes, never selects or submits', async () => {
  const h = harness({ debounce: 0, search: () => Promise.resolve({ items: [item(1, { matchedText: '张三丰' }), item(2, { matchedText: '张明' })], hasMore: false }) });
  h.type('张'); await flush(); assert.equal(h.ghost.textContent, '张三丰');
  h.input.emit('keydown', { key: 'ArrowDown' }); h.input.emit('keydown', { key: 'ArrowDown' }); assert.equal(h.ghost.textContent, '张明');
  const event = h.input.emit('keydown', { key: 'Enter', shiftKey: true }); assert.equal(event.defaultPrevented, true); assert.equal(h.input.value, '张明');
  assert.equal(h.picks.length, 0); assert.equal(h.submits.length, 0);
  h.type('三'); await flush(); assert.equal(h.ghost.hidden, true);
});

test('moving a single-line caret clears the ghost and Shift Enter cannot splice a stale suffix into an account', async () => {
  const prefix = '000SYN-SEARCH-0001234', candidate = prefix + '0';
  const h = harness({ debounce: 0, search: () => Promise.resolve({ items: [item(1, { value: candidate, account: candidate, matchedField: 'account', matchedText: candidate })], hasMore: false }) });
  h.type(prefix); await flush(); assert.equal(h.ghost.textContent, candidate);
  // Revalidate the caret during the shortcut, even before selectionchange.
  h.input.setSelectionRange(prefix.length - 1, prefix.length - 1);
  h.input.emit('keydown', { key: 'Enter', shiftKey: true }); assert.equal(h.input.value, prefix);
  h.type(prefix); await flush();
  h.input.setSelectionRange(prefix.length - 1, prefix.length - 1); h.input.emit('keyup', { key: 'ArrowLeft' });
  assert.equal(h.ghost.hidden, true);
  const middle = h.input.emit('keydown', { key: 'Enter', shiftKey: true });
  assert.equal(middle.defaultPrevented, true); assert.equal(middle.immediateStopped, true); assert.equal(h.input.value, prefix);
  h.input.setSelectionRange(prefix.length, prefix.length); h.doc.emit('selectionchange');
  assert.equal(h.ghost.textContent, candidate); assert.equal(h.ghost.hidden, false);
  // A programmatic caret change need not have delivered selectionchange yet.
  h.input.setSelectionRange(prefix.length - 1, prefix.length - 1);
  h.input.emit('keydown', { key: 'Enter', shiftKey: true }); assert.equal(h.input.value, prefix);
  assert.equal(h.picks.length, 0); assert.equal(h.submits.length, 0);
});

test('single-line Shift Enter consumes missing suffixes and rechecks selection, scroll, focus and binding before writing', async () => {
  const prefix = '0001234', candidate = prefix + '0';
  const h = harness({ debounce: 0, search: () => Promise.resolve({ items: [item(1, { value: candidate, account: candidate, matchedText: candidate })], hasMore: false }) });
  const noSubmit = () => {
    const event = h.input.emit('keydown', { key: 'Enter', shiftKey: true });
    assert.equal(event.defaultPrevented, true); assert.equal(event.immediateStopped, true);
    assert.equal(h.picks.length, 0); assert.equal(h.submits.length, 0);
  };
  noSubmit(); // No query or loaded candidates: prevent native form submission too.
  h.type(candidate); await flush(); assert.equal(h.ghost.hidden, true); noSubmit(); assert.equal(h.input.value, candidate);
  for (const change of [
    () => h.input.setSelectionRange(0, prefix.length),
    () => { h.input.scrollLeft = 1; },
    () => { h.doc.activeElement = h.form; },
    () => h.bind('another-case'),
  ]) {
    h.bind('case:A/generation:1'); h.doc.activeElement = h.input; h.input.scrollLeft = 0;
    h.type(prefix); await flush(); assert.equal(h.ghost.hidden, false);
    change(); noSubmit(); assert.equal(h.input.value, prefix);
  }
  h.bind('case:A/generation:1'); h.doc.activeElement = h.input; h.type(prefix); await flush();
  h.input.scrollLeft = 3; h.input.emit('scroll'); assert.equal(h.ghost.hidden, true);
  h.input.scrollLeft = 0; h.input.emit('scroll'); assert.equal(h.ghost.hidden, false);
  h.input.setSelectionRange(0, prefix.length); h.input.emit('select'); assert.equal(h.ghost.hidden, true);
});

test('textarea Shift Enter stays native even when the current token has matching candidates', async () => {
  const h = harness({ debounce: 0, ghost: false, onSubmit: undefined, token: input => ({ query: input.value, start: 0, end: input.value.length }),
    search: () => Promise.resolve({ items: [item(1)], hasMore: false }) });
  h.input.tagName = 'TEXTAREA'; h.type('张'); await flush();
  const event = h.input.emit('keydown', { key: 'Enter', shiftKey: true });
  assert.equal(event.defaultPrevented, false); assert.equal(event.propagationStopped, false);
  assert.equal(h.input.value, '张'); assert.equal(h.picks.length, 0); assert.equal(h.submits.length, 0);
});

test('numeric ID matches retain name layout; actual account match is number first; missing fields leave no empty placeholders', async () => {
  const h = harness({ debounce: 0, search: () => Promise.resolve({ items: [item(1, { matchedField: 'id', idNo: '123456789012345678', matchedText: '123456789012345678' }), item(2, { matchedField: 'account', idNo: undefined }), item(3, { account: undefined, idNo: undefined })], hasMore: false }) });
  h.type('123'); await flush(); const rows = h.options();
  assert.notEqual(rows[0].dataset.layout, 'number'); assert.equal(rows[1].dataset.layout, 'number'); assert.equal(rows[2].children.length, 1);
  assert.match(rows[0].textContent, /123456789012345678/); assert.equal(rows[0].all('mark')[0].textContent, '123');
});

test('IME owns Enter/Escape, Escape closes only candidates, Tab remains normal; no action runs while disabled', async () => {
  const h = harness({ debounce: 0, search: () => Promise.resolve({ items: [item(1)], hasMore: false }) });
  h.type('张'); await flush();
  for (const key of ['Enter', 'Escape']) { const e = h.input.emit('keydown', { key, isComposing: true }); assert.equal(e.defaultPrevented, false); assert.equal(e.immediateStopped, true); }
  assert.equal(h.submits.length, 0); assert.equal(h.picks.length, 0);
  const escape = h.input.emit('keydown', { key: 'Escape' }); assert.equal(escape.defaultPrevented, true); assert.equal(escape.immediateStopped, true);
  h.picker.open(); const tab = h.input.emit('keydown', { key: 'Tab' }); assert.equal(tab.defaultPrevented, false); assert.equal(h.input.getAttribute('aria-expanded'), 'false');
  h.picker.open(); h.input.disabled = true; h.options()[0]?.emit('click'); assert.equal(h.picks.length, 0);
});

test('layers remain outside labels inside their form; resize preserves active identity, destroy restores input and removes listeners', async () => {
  const h = harness({ debounce: 0, search: () => Promise.resolve({ items: [item(1), item(2)], hasMore: false }) });
  assert.equal(h.popup.parentElement, h.form); assert.equal(h.label.contains(h.popup), false);
  h.type('张'); await flush(); h.input.emit('keydown', { key: 'ArrowDown' }); const activeId = h.input.getAttribute('aria-activedescendant');
  h.doc.anchor.width = 260; h.win.emit('resize'); h.timers(); assert.equal(h.input.getAttribute('aria-activedescendant'), activeId); assert.equal(h.options()[0].dataset.layout, 'stack');
  assert.equal(h.list.getAttribute('aria-label'), '查询账户候选');
  h.picker.destroy(); assert.equal(h.form.all('.fep-popup').length, 0); assert.equal(h.form.all('.fep-ghost').length, 0); assert.equal(h.input.getAttribute('role'), null);
  const count = h.calls.length; h.type('王'); h.timers(); assert.equal(h.calls.length, count);
});

test('click reopens a focused field after data invalidation or Escape without changing its draft', async () => {
  const h = harness({ search: async () => ({ items: [item(1)], hasMore: false }) });
  h.type('张'); h.timers(); await flush(); assert.equal(h.input.getAttribute('aria-expanded'), 'true');
  h.picker.invalidate(); assert.equal(h.input.getAttribute('aria-expanded'), 'false');
  h.input.emit('click'); await flush(); assert.equal(h.input.value, '张'); assert.equal(h.input.getAttribute('aria-expanded'), 'true');
  h.input.emit('keydown', { key: 'Escape' }); assert.equal(h.input.getAttribute('aria-expanded'), 'false');
  h.input.emit('click'); await flush(); assert.equal(h.input.getAttribute('aria-expanded'), 'true');
  assert.equal(h.picks.length, 0); assert.equal(h.submits.length, 0);
});
