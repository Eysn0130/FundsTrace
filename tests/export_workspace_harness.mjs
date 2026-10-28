import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
const page = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const money = readFileSync(new URL('../static/money.js', import.meta.url), 'utf8');
const dialogSource = readFileSync(new URL('../static/ui-dialogs.js', import.meta.url), 'utf8');
const flush = async () => { for (let pass = 0; pass < 24; pass += 1) await Promise.resolve(); };
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }

// Parse the complete actual controller with V8; no business implementation is substituted.
function declaredFunction(name) {
  const start = source.indexOf('function ' + name + '(');
  assert.ok(start >= 0, 'missing public controller ' + name);
  for (let end = source.indexOf('}', start); end >= 0; end = source.indexOf('}', end + 1)) {
    const candidate = source.slice(start, end + 1);
    try { new vm.Script('(' + candidate + ')'); return candidate; } catch (_) { /* nested brace */ }
  }
  throw new Error('unterminated public controller ' + name);
}

const decode = value => value.replace(/&(amp|lt|gt|quot|#39);/g, (_match, key) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" }[key]));

// Real export markup, control identity, native close/cancel and focus order.
// Only the injected host and IO boundaries are synthetic.
function harness({ execute, withDialogs = false } = {}) {
  let document;
  const matches = (node, selector) => {
    if (!node || node.tagName === '#TEXT') return false;
    if (selector === ':focus') return document.activeElement === node;
    if (selector === '[open]') return node.open;
    if (selector === '[inert]') return node.inert;
    if (selector.includes(':checked')) { if (!node.checked) return false; selector = selector.replace(':checked', ''); }
    if (selector.includes(':not(:disabled)')) { if (node.disabled) return false; selector = selector.replace(':not(:disabled)', ''); }
    const attrs = [...selector.matchAll(/\[([\w-]+)(?:=["']?([^"'\]]*)["']?)?\]/g)];
    selector = selector.replace(/\[[^\]]*\]/g, '');
    for (const [, name, value] of attrs) {
      const actual = node.getAttribute(name);
      if (actual === null || value !== undefined && actual !== value) return false;
    }
    const id = selector.match(/#([\w-]+)/)?.[1];
    if (id && node.id !== id) return false;
    for (const [, name] of selector.matchAll(/\.([\w-]+)/g)) if (!node.classList.contains(name)) return false;
    const tag = selector.match(/^[\w-]+/)?.[0];
    return !tag || node.tagName === tag.toUpperCase();
  };
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag.toUpperCase(); this.children = []; this.parentElement = null; this.attributes = new Map();
      this.listeners = new Map(); this.style = {}; this.value = ''; this.disabled = false; this.hidden = false;
      this.files = []; this.open = false; this.returnValue = ''; this.inert = false;
      this.checked = false; this.tabIndex = 0; this.scrollLeft = this.scrollTop = 0; this.clientWidth = 320;
      this.offsetWidth = 160; this.offsetHeight = 40; this._text = ''; this._html = '';
      this.dataset = new Proxy({}, { set: (object, key, value) => {
        object[key] = String(value); this.attributes.set('data-' + key.replace(/[A-Z]/g, letter => '-' + letter.toLowerCase()), String(value)); return true;
      } });
      const classes = new Set();
      this.classList = { contains: name => classes.has(name),
        add: (...names) => names.forEach(name => classes.add(name)), remove: (...names) => names.forEach(name => classes.delete(name)),
        toggle(name, force) { const on = force ?? !classes.has(name); if (on) classes.add(name); else classes.delete(name); return on; } };
    }
    get id() { return this.attributes.get('id') || ''; }
    set id(value) { this.attributes.set('id', String(value)); }
    get nextElementSibling() { const siblings = (this.parentElement?.children || []).filter(node => node.tagName !== '#TEXT'); return siblings[siblings.indexOf(this) + 1] || null; }
    get disabled() { return this._disabled || false; }
    set disabled(value) { this._disabled = Boolean(value); if (value && document?.activeElement === this) document.activeElement = document.body; }
    get className() { return this.attributes.get('class') || ''; }
    set className(value) { for (const name of this.className.split(/\s+/)) this.classList.remove(name); this.attributes.set('class', value); this.classList.add(...value.split(/\s+/).filter(Boolean)); }
    get isConnected() { return this === document?.root || !!this.parentElement?.isConnected; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    set textContent(value) { this.replaceChildren(); this._text = String(value ?? ''); this._html = ''; }
    get innerHTML() { return this._html; }
    set innerHTML(value) {
      this.replaceChildren(); this._text = ''; this._html = String(value);
      const stack = [this], voids = new Set(['INPUT', 'IMG', 'BR', 'HR', 'META', 'LINK']);
      for (const token of String(value).matchAll(/<\/?([a-z][\w:-]*)\b([^>]*?)>|([^<]+)/gi)) {
        if (token[3]) { const text = new Element('#text'); text._text = decode(token[3]); stack.at(-1).appendChild(text); continue; }
        if (token[0].startsWith('</')) { if (stack.length > 1) stack.pop(); continue; }
        const child = new Element(token[1]);
        for (const [, name, quoted, single, bare] of token[2].matchAll(/([\w-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) child.setAttribute(name, decode(quoted ?? single ?? bare ?? ''));
        const parent = stack.at(-1); parent.appendChild(child);
        if (child.tagName === 'OPTION' && (child.getAttribute('selected') !== null || !parent.value)) parent.value = child.value;
        if (!voids.has(child.tagName) && !token[0].endsWith('/>')) stack.push(child);
      }
    }
    setAttribute(name, value) {
      value = String(value);
      if (name === 'class') { this.className = value; return; }
      this.attributes.set(name, value);
      if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase())] = value;
      else if (['hidden', 'disabled', 'checked', 'open', 'inert'].includes(name)) this[name] = true;
      else if (name === 'tabindex') this.tabIndex = Number(value);
      else if (name === 'value') this.value = value;
      else if (name === 'style') for (const declaration of value.split(';')) { const [key, val] = declaration.split(':'); if (val) this.style[key.trim()] = val.trim(); }
    }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    removeAttribute(name) { this.attributes.delete(name); if (['hidden', 'disabled', 'open', 'inert'].includes(name)) this[name] = false; }
    appendChild(child) { child.remove(); child.parentElement = this; this.children.push(child); return child; }
    append(...children) { children.forEach(child => this.appendChild(child)); }
    after(child) { const parent = this.parentElement; child.remove(); parent.children.splice(parent.children.indexOf(this) + 1, 0, child); child.parentElement = parent; }
    remove() {
      if (document?.activeElement && this.contains(document.activeElement)) document.activeElement = document.body;
      if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1);
      this.parentElement = null;
    }
    replaceChildren(...children) { for (const child of [...this.children]) child.remove(); this._text = ''; this.append(...children); }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    matches(selector) { return selector.split(',').some(part => matches(this, part.trim())); }
    closest(selector) { for (let node = this; node; node = node.parentElement) if (node.matches(selector)) return node; return null; }
    querySelectorAll(selector) {
      const all = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
      return all.filter(node => selector.split(',').some(part => {
        const tokens = part.trim().split(/\s+/), leaf = tokens.pop();
        if (!matches(node, leaf)) return false;
        let parent = node.parentElement;
        for (const token of tokens.reverse()) { while (parent && !matches(parent, token)) parent = parent.parentElement; if (!parent) return false; parent = parent.parentElement; }
        return true;
      }));
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    addEventListener(type, handler, options = false) {
      const list = this.listeners.get(type) || []; list.push({ handler, capture: options === true || !!options.capture }); this.listeners.set(type, list);
    }
    removeEventListener(type, handler) { this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item.handler !== handler)); }
    fire(type, options = {}) {
      const event = { type, target: this, key: '', relatedTarget: null, bubbles: !['scroll', 'resize', 'close', 'cancel'].includes(type), defaultPrevented: false, stopped: false, immediate: false,
        preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; },
        stopImmediatePropagation() { this.immediate = this.stopped = true; }, ...options };
      const path = []; for (let node = this; node; node = node.parentElement) path.push(node);
      const results = [], invoke = (node, capture) => {
        event.currentTarget = node;
        for (const item of node.listeners.get(type) || []) {
          if (item.capture === capture) results.push(item.handler.call(node, event));
          if (event.immediate) return;
        }
        if (!capture) results.push(node['on' + type]?.(event));
      };
      for (const node of [...path].reverse()) { invoke(node, true); if (event.stopped) break; }
      if (!event.stopped) for (const node of event.bubbles ? path : [this]) { invoke(node, false); if (event.stopped) break; }
      event.completion = Promise.all(results); return event;
    }
    click() { if (this.disabled) return Promise.resolve(); if (this.tagName === 'A' && this.download) downloads.push({ href: this.href, filename: this.download }); return this.fire('click').completion; }
    dispatchEvent(event) { return !this.fire(event.type, event).defaultPrevented; }
    focus() {
      if (!this.isConnected || this.disabled || this.closest('[inert]')) return;
      const previous = document.activeElement; document.activeElement = this;
      if (previous !== this) previous?.fire('focusout', { relatedTarget: this });
      this.fire('focusin', { relatedTarget: previous });
    }
    showModal() { this.open = true; this.setAttribute('open', ''); }
    close(value = '') { this.returnValue = value; this.open = false; this.removeAttribute('open'); return this.fire('close', { bubbles: false }).completion; }
    hasAttribute(name) { return this.attributes.has(name); }
    toggleAttribute(name, force) { const on = force ?? !this.hasAttribute(name); if (on) this.setAttribute(name, ''); else this.removeAttribute(name); return on; }
    select() {}
    getContext() { return { translate() {}, rotate() {}, fillText() {}, measureText: text => ({ width: String(text).length * 6 }) }; }
    toDataURL() { return 'data:image/png;base64,c3ludGhldGlj'; }
    setPointerCapture() {}
    getBoundingClientRect() { return { left: 20, right: 180, top: 20, bottom: 60, width: 160, height: 40 }; }
    getClientRects() {
      for (let node = this; node; node = node.parentElement) if (node.hidden || node.style.display === 'none') return [];
      return this.isConnected ? [this.getBoundingClientRect()] : [];
    }
  }
  document = { root: new Element('#document'), body: new Element('body'), documentElement: new Element('html'), activeElement: null,
    createElement: tag => new Element(tag), getElementById: id => document.root.querySelector('#' + id),
    querySelector: selector => document.root.querySelector(selector), querySelectorAll: selector => document.root.querySelectorAll(selector),
    addEventListener: (...args) => document.root.addEventListener(...args), removeEventListener: (...args) => document.root.removeEventListener(...args) };
  document.root.appendChild(document.body); document.activeElement = document.body;
  const add = (parent, id, tag = 'div') => { const node = new Element(tag); node.id = id; parent.appendChild(node); return node; };
  document.body.innerHTML = withDialogs ? page.match(/<body\b[^>]*>([\s\S]*)<\/body>/)[1]
    : page.match(/<button\b[^>]*id="btnExportActions"[\s\S]*?<\/button>/)[0] + page.match(/<dialog\b[^>]*id="exportActions"[\s\S]*?<\/dialog>/)[0];
  const downloads = [], calls = [], legacyFormats = [], timers = [];
  const current = { caseId: 1, canvasId: 7, generation: 1, canvasGeneration: 1 };
  const scope = { graphAccounts: 2, graphRelations: 1, trackedFlows: 9, pendingLayout: false, graphReady: true };
  const labels = { caseName: '合成案件 <甲>', canvasName: '合成画布 & 七' };
  let executeIO = execute || (async (_format, task) => { task.setPhase('generating'); return { downloadStarted: true }; });
  const window = new Element('#window'); window.appendChild(document.root);
  const context = vm.createContext({ window, document, console,
    getComputedStyle: node => ({ display: node.hidden ? 'none' : node.style.display || 'block' }),
    MutationObserver: class { constructor(callback) { this.callback = callback; } observe(target) { assert.ok(target); } disconnect() {} },
    setTimeout: (callback, delay = 0) => { timers.push({ callback, delay }); return timers.length; }, clearTimeout() {},
  });
  vm.runInContext(money, context, { filename: 'money.js' });
  vm.runInContext(readFileSync(new URL('../static/principal-analysis.js', import.meta.url), 'utf8'), context);
  window.FundsFacts = context.FundsFacts;
  vm.runInContext("const $ = selector => document.querySelector(selector); const $$ = selector => [...document.querySelectorAll(selector)]; const fmtCount = value => FundsMoney.count(value);", context);
  vm.runInContext(declaredFunction('installExportWorkspace'), context, { filename: 'app.js:installExportWorkspace' });
  context.exportHost = {
    getContext: () => ({ ...current }),
    isCurrent: binding => !!binding && Object.keys(current).every(key => binding[key] === current[key]),
    getScope: () => ({ ...scope }), getLabels: () => ({ ...labels }), getTrackedCount: () => scope.trackedFlows,
    canGraphExport: () => scope.graphReady && scope.graphAccounts > 0 && !scope.pendingLayout,
    execute: async (format, task) => { calls.push({ format, task }); return executeIO(format, task); },
    setLegacyFormat: format => { legacyFormats.push(format); document.getElementById('exportFmt').value = format; },
  };
  const controller = vm.runInContext('installExportWorkspace(exportHost)', context);
  if (withDialogs) { vm.runInContext(dialogSource, context, { filename: 'ui-dialogs.js' }); window.fire('DOMContentLoaded'); }
  const element = id => document.getElementById(id), trigger = element('btnExportActions');
  return { document, window, controller, element, trigger, current, scope, labels, calls, legacyFormats, timers, downloads,
    run: code => vm.runInContext(code, context), io(handler) { executeIO = handler; },
    open() { return controller.open(trigger); },
    choose(format) {
      const inputs = document.querySelectorAll('input[name="exportFormat"]');
      for (const input of inputs) input.checked = input.value === format;
      const selected = inputs.find(input => input.checked); return selected?.fire('change').completion || flush();
    },
    async submit() { await element('btnExportSubmit').click(); await flush(); },
    async close() { await element('btnExportClose').click(); await flush(); },
    async escape() { const event = element('exportActions').fire('cancel', { bubbles: false }); await event.completion; if (!event.defaultPrevented) await element('exportActions').close('cancel'); await flush(); return event; },
    switchBinding(caseId = 2, canvasId = 0) { current.caseId = caseId; current.canvasId = canvasId; current.generation += 1; current.canvasGeneration += 1; controller.revoke(); },
  };
}

test('the export workspace offers exactly four executable single-choice formats with PNG initially selected', async () => {
  const h = harness(); h.open(); const inputs = h.document.querySelectorAll('input[name="exportFormat"]');
  assert.deepEqual(inputs.filter(input => !input.disabled).map(input => input.value), ['png', 'html', 'pdf', 'xlsx']);
  assert.deepEqual(inputs.filter(input => input.checked).map(input => input.value), ['png']);
  for (const format of ['html', 'pdf', 'xlsx', 'png']) { await h.choose(format); assert.deepEqual(inputs.filter(input => input.checked).map(input => input.value), [format]); }
  assert.equal(h.calls.length, 0); assert.equal(h.controller.status().dialogOpen, true);
  assert.equal(!!h.document.querySelector('input[name="exportFormat"][value="svg"]:not(:disabled)'), false);
});

test('all four selected formats execute with the captured case and canvas through the real controller', async () => {
  for (const format of ['png', 'html', 'pdf', 'xlsx']) {
    const h = harness(); h.open(); await h.choose(format); await h.submit();
    assert.equal(h.calls.length, 1); assert.equal(h.calls[0].format, format);
    assert.deepEqual(JSON.parse(JSON.stringify(h.calls[0].task.context)), { caseId: 1, canvasId: 7, generation: 1, canvasGeneration: 1 });
    assert.equal(h.controller.status().pending, false); assert.equal(h.element('exportError').hidden, true);
  }
});

test('graph scope and tracked Excel scope remain visibly distinct', async () => {
  const h = harness(); h.open();
  const graph = h.element('exportScope').textContent + h.element('exportContents').textContent;
  assert.match(graph, /2/); assert.match(graph, /1/); assert.match(graph, /账户|关系|图谱/);
  await h.choose('xlsx'); const excel = h.element('exportScope').textContent + h.element('exportContents').textContent;
  assert.match(excel, /9/); assert.match(excel, /追踪/); assert.match(excel, /待调取/); assert.notEqual(excel, graph);
  await h.choose('html'); assert.match(h.element('exportPreserves').textContent, /当前.*标签|标签.*当前/);
  assert.doesNotMatch(h.element('exportPreserves').textContent, /全部标签/);
});

test('Excel description distinguishes one confirmed event from two retained observations', async () => {
  const h = harness(); h.scope.trackedFlows = 2;
  h.scope.funds = { count: { lower: 1, upper: 1 } };
  h.open(); await h.choose('xlsx');
  assert.match(h.element('exportContents').textContent, /1 笔追踪流水/);
  assert.match(h.element('exportContents').textContent, /2 条来源记录/);
  assert.doesNotMatch(h.element('exportContents').textContent, /2 笔追踪流水/);
  assert.match(h.element('exportPreserves').textContent, /资金统计/);
  assert.match(h.element('exportPreserves').textContent, /原始账簿观察/);
  assert.doesNotMatch(h.element('exportPreserves').textContent, /两张/);
});

test('Excel description preserves unresolved bounds and does not invent an exact count without funds', async () => {
  const h = harness(); h.scope.trackedFlows = 2;
  h.scope.funds = { observation_count: 2, count: { lower: 0, upper: 2 } };
  h.open(); await h.choose('xlsx');
  assert.match(h.element('exportContents').textContent, /—追踪流水 · 2 条来源记录/);
  delete h.scope.funds; h.controller.refresh();
  assert.match(h.element('exportContents').textContent, /—追踪流水 · 2 条来源记录/);
  assert.doesNotMatch(h.element('exportContents').textContent, /2 个事件|2 笔/);
});

test('working failure and completion feedback stay outside the scrolling export body above its footer', async () => {
  const h = harness({ execute: async () => { throw new Error('合成可见失败'); } }); h.open(); await h.submit();
  assert.equal(h.element('exportError').hidden, false); const dialog = h.element('exportActions'), status = h.element('exportError').parentElement;
  for (const id of ['exportError', 'exportBusy', 'exportFeedback']) {
    assert.equal(h.element(id).parentElement === status, true); assert.equal(!!h.element(id).closest('.export-dialog-body'), false);
  }
  assert.equal(status.parentElement === dialog, true); assert.equal(status.nextElementSibling.classList.contains('export-dialog-footer'), true);
  const pending = deferred(); h.io(async (_format, task) => { task.setPhase('generating'); await pending.promise; return { downloadStarted: true }; });
  const submitting = h.submit(); await flush(); assert.equal(h.element('exportBusy').hidden, false);
  pending.resolve(); await submitting; assert.match(h.element('exportFeedback').textContent, /已开始下载/);
});

test('context labels preserve synthetic markup literally and are bound to the opened case and canvas', () => {
  const h = harness(); h.open(); const text = h.element('exportContext').textContent;
  assert.match(text, /合成案件 <甲>/); assert.match(text, /合成画布 & 七/); assert.equal(!!h.element('exportContext').querySelector('script'), false);
});

test('an unchecked format group cannot start an export', async () => {
  const h = harness(); h.open(); await h.choose('not-a-format'); await h.submit();
  assert.equal(h.calls.length, 0); assert.equal(h.controller.status().pending, false);
  assert.equal(h.element('exportError').hidden, false); assert.notEqual(h.element('exportError').textContent.trim(), '');
});

test('the same binding remembers its chosen format across a normal close and reopen', async () => {
  const h = harness(); h.open(); await h.choose('pdf'); await h.close();
  assert.equal(h.controller.status().dialogOpen, false); h.open();
  assert.equal(h.controller.status().format, 'pdf'); assert.equal(h.document.querySelector('input[name="exportFormat"]:checked').value, 'pdf');
  assert.equal(h.calls.length, 0);
});

test('reopening a completed export refreshes its changed scope without retaining the old download result', async () => {
  const h = harness(); h.open(); await h.choose('html'); await h.submit(); assert.match(h.element('exportFeedback').textContent, /已开始下载/);
  await h.close(); h.scope.graphAccounts = 7; h.scope.graphRelations = 6; h.open();
  assert.doesNotMatch(h.element('exportFeedback').textContent, /已开始下载/); assert.match(h.element('exportContents').textContent, /7.*6/);
  assert.equal(h.controller.status().format, 'html'); assert.equal(h.controller.status().pending, false); assert.equal(h.calls.length, 1);
});

test('Excel selection does not overwrite the retained hidden graph format', async () => {
  const h = harness(); h.open(); await h.choose('pdf'); assert.equal(h.element('exportFmt').value, 'pdf');
  await h.choose('xlsx'); assert.equal(h.controller.status().format, 'xlsx'); assert.equal(h.element('exportFmt').value, 'pdf');
});

test('normal Close, Cancel and native Escape return focus to the opening trigger', async () => {
  for (const close of ['close', 'cancel', 'escape']) {
    const h = harness(); h.trigger.focus(); h.open();
    if (close === 'cancel') { await h.element('btnExportCancel').click(); await flush(); } else await h[close]();
    assert.equal(h.controller.status().dialogOpen, false); assert.equal(h.document.activeElement === h.trigger, true, close);
  }
});

test('capturing prevents Close and Escape without releasing the pending task', async () => {
  const pending = deferred(); const h = harness({ execute: async (_format, task) => { task.setPhase('capturing'); await pending.promise; return { downloadStarted: true }; } });
  h.open(); const submitting = h.submit(); await flush(); const task = h.calls[0].task;
  await h.close(); const escaped = await h.escape();
  assert.equal(escaped.defaultPrevented, true); assert.equal(h.controller.status().dialogOpen, true);
  assert.equal(h.controller.status().pending, true); assert.equal(task.isCurrent(), true); assert.equal(h.calls.length, 1);
  pending.resolve(); await submitting; assert.equal(h.controller.status().pending, false);
});

test('the real dialog backdrop cannot close an image capture but can hide file generation', async () => {
  const pending = deferred(); let task;
  const h = harness({ withDialogs: true, execute: async (_format, currentTask) => { task = currentTask; task.setPhase('capturing'); await pending.promise; return { downloadStarted: true }; } });
  h.open(); const submitting = h.submit(); await flush(); const dialog = h.element('exportActions');
  await dialog.fire('pointerdown', { clientX: 0, clientY: 0, button: 0 }).completion;
  await dialog.fire('click', { clientX: 0, clientY: 0, button: 0 }).completion;
  const captureOpen = dialog.open;
  task.setPhase('generating'); await dialog.fire('pointerdown', { clientX: 0, clientY: 0, button: 0 }).completion;
  await dialog.fire('click', { clientX: 0, clientY: 0, button: 0 }).completion; const generationOpen = dialog.open;
  pending.resolve(); await submitting; assert.equal(captureOpen, true); assert.equal(generationOpen, false);
});

test('generating can be hidden and reopened as the same pending task without a duplicate export', async () => {
  const pending = deferred(); const h = harness({ execute: async (_format, task) => { task.setPhase('generating'); await pending.promise; return { downloadStarted: true }; } });
  h.open(); await h.choose('pdf'); const submitting = h.submit(); await flush(); const task = h.calls[0].task;
  await h.close(); assert.equal(h.controller.status().dialogOpen, false); assert.equal(h.controller.status().pending, true); assert.equal(task.isCurrent(), true);
  h.open(); await h.element('btnExportSubmit').click(); await flush();
  assert.equal(h.controller.status().phase, 'generating'); assert.equal(h.controller.status().format, 'pdf'); assert.equal(h.calls.length, 1);
  pending.resolve(); await submitting; assert.equal(h.controller.status().pending, false);
});

test('finishing a hidden generating task does not force its dialog open', async () => {
  const pending = deferred(); const h = harness({ execute: async (_format, task) => { task.setPhase('generating'); await pending.promise; return { downloadStarted: true }; } });
  h.open(); const submitting = h.submit(); await flush(); await h.close(); pending.resolve(); await submitting;
  assert.equal(h.controller.status().dialogOpen, false); assert.equal(h.controller.status().pending, false); assert.equal(h.calls.length, 1);
});

test('one pending task freezes its format and rejects repeated submit events', async () => {
  const pending = deferred(); const h = harness({ execute: async (_format, task) => { task.setPhase('generating'); await pending.promise; return { downloadStarted: true }; } });
  h.open(); await h.choose('html'); const first = h.submit(); await flush();
  for (const input of h.document.querySelectorAll('input[name="exportFormat"]')) assert.equal(input.disabled, true);
  await h.element('btnExportSubmit').click(); await h.element('btnExportSubmit').fire('click').completion; await flush();
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].format, 'html'); pending.resolve(); await first;
});

test('a clear execution error preserves selection and enables an explicit retry', async () => {
  const h = harness({ execute: async () => { throw new Error('合成文件生成失败'); } }); h.open(); await h.choose('xlsx'); await h.submit();
  assert.equal(h.element('exportError').hidden, false); assert.match(h.element('exportError').textContent, /合成文件生成失败/);
  assert.equal(h.controller.status().pending, false); assert.equal(h.element('btnExportSubmit').disabled, false); assert.equal(h.controller.status().format, 'xlsx');
  h.io(async () => ({ downloadStarted: true })); await h.submit(); assert.equal(h.calls.length, 2); assert.equal(h.element('exportError').hidden, true);
});

test('native network failures use actionable Chinese feedback without hiding business errors', async () => {
  for (const message of ['Failed to fetch', 'NetworkError', 'Load failed', '合成业务错误 HTTP 503']) {
    const h = harness({ execute: async () => { throw new Error(message); } }); h.open(); await h.submit();
    assert.equal(h.element('exportError').textContent, message.startsWith('合成') ? message : '连接失败，请重试');
    assert.equal(h.element('btnExportSubmit').disabled, false);
  }
});

test('empty graph blocks visual formats while nonempty tracked Excel remains available', async () => {
  const h = harness(); h.scope.graphAccounts = h.scope.graphRelations = 0; h.scope.graphReady = false; h.open();
  for (const format of ['png', 'html', 'pdf']) { await h.choose(format); await h.submit(); }
  assert.equal(h.calls.length, 0); await h.choose('xlsx'); await h.submit(); assert.equal(h.calls.length, 1); assert.equal(h.calls[0].format, 'xlsx');
});

test('empty tracked membership blocks Excel independently of a nonempty visible graph', async () => {
  const h = harness(); h.scope.trackedFlows = 0; h.open(); await h.choose('xlsx'); await h.submit(); assert.equal(h.calls.length, 0);
  await h.choose('png'); await h.submit(); assert.equal(h.calls.length, 1); assert.equal(h.calls[0].format, 'png');
});

test('a pending layout blocks graph export while tracked Excel retains its own scope', async () => {
  const h = harness(); h.scope.pendingLayout = true; h.open(); await h.submit(); assert.equal(h.calls.length, 0);
  await h.choose('xlsx'); await h.submit(); assert.equal(h.calls.length, 1); assert.equal(h.calls[0].format, 'xlsx');
});

test('case A to B to A revocation makes old task callbacks inert for the new dialog binding', async () => {
  const pending = deferred(); const h = harness({ execute: async (_format, task) => { task.setPhase('generating'); await pending.promise; return { downloadStarted: true }; } });
  h.open(); const submitting = h.submit(); await flush(); const old = h.calls[0].task;
  h.switchBinding(2, 0); h.switchBinding(1, 7); h.open(); const before = JSON.stringify(h.controller.status());
  assert.equal(old.isCurrent(), false); old.setPhase('capturing'); pending.resolve(); await submitting;
  assert.equal(JSON.stringify(h.controller.status()), before); assert.equal(h.element('exportError').hidden, true);
});

test('a late execution error cannot replace the newer case status or feedback', async () => {
  const pending = deferred(); const h = harness({ execute: async (_format, task) => { task.setPhase('generating'); return pending.promise; } });
  h.open(); const submitting = h.submit(); await flush(); h.switchBinding(2, 3); h.labels.caseName = '合成乙案'; h.labels.canvasName = '合成画布三'; h.open();
  const before = h.element('exportFeedback').textContent; pending.reject(new Error('合成甲案迟到失败')); await submitting;
  assert.equal(h.element('exportFeedback').textContent, before); assert.equal(h.element('exportError').hidden, true); assert.match(h.element('exportContext').textContent, /合成乙案/);
});

test('canvas revocation invalidates a pending task even when the numeric canvas returns to its original value', async () => {
  const pending = deferred(); const h = harness({ execute: async (_format, task) => { task.setPhase('generating'); return pending.promise; } });
  h.open(); const submitting = h.submit(); await flush(); const task = h.calls[0].task;
  h.switchBinding(1, 8); h.switchBinding(1, 7); assert.equal(task.isCurrent(), false); pending.resolve({ downloadStarted: true }); await submitting;
  assert.equal(h.controller.status().pending, false);
});
