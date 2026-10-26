const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const source = readFileSync(path.join(root, 'static/case-workspace.js'), 'utf8');
const dialogSource = readFileSync(path.join(root, 'static/ui-dialogs.js'), 'utf8');
const page = readFileSync(path.join(root, 'static/index.html'), 'utf8');
const flush = async () => { for (let pass = 0; pass < 24; pass += 1) await Promise.resolve(); };
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const failure = (message, status) => Object.assign(new Error(message), status == null ? {} : { status });
const file = name => Object.assign(new Blob(['synthetic case package'], { type: 'application/octet-stream' }), { name });
const clone = value => JSON.parse(JSON.stringify(value));

const decode = value => value.replace(/&(amp|lt|gt|quot|#39);/g, (_match, key) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" }[key]));

// Real case-management markup and events; only host IO is synthetic.
function harness({ withDialogs = false, autoOpenCreated = false } = {}) {
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
  const dialog = page.match(/<dialog\b[^>]*id="caseActions"[\s\S]*?<\/dialog>/);
  assert.ok(dialog, 'the real page must expose a valid caseActions dialog');
  document.body.innerHTML = withDialogs ? page.match(/<body\b[^>]*>([\s\S]*)<\/body>/)[1]
    : page.match(/<button\b[^>]*id="btnCaseActions"[\s\S]*?<\/button>/)[0] + dialog[0] + page.match(/<input\b[^>]*id="casePkgFile"[^>]*>/)[0];
  const downloads = [], timers = [], calls = Object.fromEntries(['summary', 'list', 'canvasList', 'create', 'import', 'delete', 'export', 'save', 'open', 'leave', 'download', 'notify'].map(name => [name, []]));
  const states = [];
  const current = { caseId: 2, canvasId: 7, generation: 1, canvasGeneration: 1 };
  const summary = { name: '合成案件 <乙>', canvasName: '合成画布 & 七', flows: 123, accounts: 45, defaultCase: false };
  const cases = [{ id: 1, name: '默认合成案件' }, { id: 2, name: summary.name }];
  const isCurrent = context => !!context && Object.keys(current).every(key => context[key] === current[key]);
  const handlers = {
    summary: () => ({ ...summary }), list: async () => cases.map(item => ({ ...item })), canvasList: async () => [{ id: 7, name: '合成画布七' }],
    create: async name => ({ id: 3, name }), import: async () => ({ case_id: 4, name: '合成导入案件', flows: 17 }),
    delete: async () => ({ deleted_flows: 123 }), export: async () => ({ blob: new Blob(['synthetic package']), filename: '合成案件.fundcase' }),
    save: async () => {}, open: async (id, expected) => { if (!isCurrent(expected)) return false; current.caseId = id; current.canvasId = 0; current.generation += 1; current.canvasGeneration += 1; return true; },
    leave: async () => true, download: async () => {}, notify: () => {},
  };
  const window = new Element('#window'); window.appendChild(document.root);
  const context = vm.createContext({ document, window, console, Blob,
    getComputedStyle: node => ({ display: node.hidden ? 'none' : node.style.display || 'block' }),
    MutationObserver: class { observe(target) { assert.ok(target); } disconnect() {} },
    setTimeout: (callback, delay = 0) => { timers.push({ callback, delay }); return timers.length; }, clearTimeout() {},
    requestAnimationFrame: callback => { callback(); return 0; }, cancelAnimationFrame() {},
  });
  vm.runInContext(source, context, { filename: 'case-workspace.js' });
  const invoke = (kind, args, record = args) => { calls[kind].push(record); return handlers[kind](...args); };
  let controller;
  const host = {
    document, context: () => ({ ...current }), isCurrent,
    autoOpenCreated, onState: () => states.push(clone(controller.status())),
    readSummary: () => invoke('summary', [], clone(current)), listCases: () => invoke('list', []),
    listCanvases: binding => invoke('canvasList', [binding], clone(binding)),
    createCase: name => invoke('create', [name]), importCase: selected => invoke('import', [selected]),
    deleteCase: id => invoke('delete', [id]), exportCase: id => invoke('export', [id]),
    saveCanvas: (binding, name) => invoke('save', [binding, name], [clone(binding), name]),
    openCase: (id, binding, flags) => invoke('open', [id, binding, flags], [id, clone(binding), ...(flags ? [clone(flags)] : [])]),
    canLeave: binding => invoke('leave', [binding], clone(binding)),
    download: (blob, filename) => invoke('download', [blob, filename]),
    notify: (message, type) => invoke('notify', [message, type]),
  };
  controller = (context.FundsCaseWorkspace || context.window.FundsCaseWorkspace).create(host);
  const element = id => document.getElementById(id), trigger = element('btnCaseActions');
  if (withDialogs) { vm.runInContext(dialogSource, context, { filename: 'ui-dialogs.js' }); window.fire('DOMContentLoaded'); }
  return { document, window, controller, element, trigger, current, summary, cases, calls, downloads, timers, states,
    io(kind, handler) { assert.ok(Object.hasOwn(handlers, kind)); handlers[kind] = handler; },
    async open() { controller.open(trigger); await flush(); },
    async view(name) { await element({ new: 'btnNewCase', import: 'btnImportCase', export: 'btnExportCase', delete: 'btnDelCase' }[name]).click(); await flush(); },
    async submit() { await controller.submit(); await flush(); },
    async edit(id, value) { const input = element(id); assert.ok(input); input.value = value; await input.fire('input').completion; await input.fire('change').completion; await flush(); },
    async check(id, checked) { element(id).checked = checked; await element(id).fire('change').completion; await flush(); },
    async choose(selected) { element('casePkgFile').files = selected ? [selected] : []; await element('casePkgFile').fire('change').completion; await flush(); },
    async close() { await element('caseClose').click(); await flush(); },
    async back() { await element('caseBack').click(); await flush(); },
    switchBinding(caseId = 5, canvasId = 0) { current.caseId = caseId; current.canvasId = canvasId; current.generation += 1; current.canvasGeneration += 1; controller.revoke(); },
  };
}

const writes = h => h.calls.create.length + h.calls.import.length + h.calls.delete.length;
async function readyCreate(h, name = '合成新案') { await h.open(); await h.view('new'); await h.edit('caseName', name); }

test('the home summary shows the captured synthetic case and canvas as plain text', async () => {
  const h = harness(); await h.open();
  assert.equal(h.controller.status().view, 'home'); assert.equal(h.controller.status().dialogOpen, true);
  assert.match(h.element('caseContext').textContent, /合成案件 <乙>/); assert.match(h.element('caseContext').textContent, /合成画布 & 七/);
  assert.match(h.element('caseCounts').textContent, /123/); assert.match(h.element('caseCounts').textContent, /45/);
  assert.equal(!!h.element('caseContext').querySelector('script'), false); assert.equal(writes(h), 0);
});

test('blank case names fail validation without a POST', async () => {
  const h = harness(); await readyCreate(h, '   '); await h.submit();
  assert.equal(h.calls.create.length, 0); assert.equal(h.element('caseError').hidden, false);
});

test('creating a case trims its name and waits for explicit opening before leaving the current case', async () => {
  const h = harness(); await readyCreate(h, '  合成新案  '); const before = clone(h.current); await h.submit();
  assert.deepEqual(h.calls.create, [['合成新案']]); assert.equal(h.controller.status().phase, 'done'); assert.deepEqual(h.current, before);
  assert.equal(h.calls.leave.length, 0); assert.equal(h.calls.open.length, 0); assert.ok(h.element('caseOpenResult').getClientRects().length);
  await h.element('caseOpenResult').click(); await flush();
  assert.deepEqual(h.calls.open[0], [3, before]); assert.equal(h.current.caseId, 3); assert.equal(h.controller.status().dialogOpen, false);
});

test('declining the guarded explicit open preserves the completed new-case result', async () => {
  const h = harness(); await readyCreate(h); await h.submit(); h.io('open', async () => false);
  await h.element('caseOpenResult').click(); await flush();
  assert.equal(h.current.caseId, 2); assert.equal(h.controller.status().dialogOpen, true); assert.equal(h.controller.status().result.id, 3);
  assert.equal(h.calls.create.length, 1);
});

test('import validates a selected package and its final extension before calling IO', async () => {
  const h = harness(); await h.open(); await h.view('import'); await h.submit(); assert.equal(h.calls.import.length, 0);
  await h.choose(file('synthetic.fundcase.exe')); await h.submit(); assert.equal(h.calls.import.length, 0);
  assert.equal(h.element('caseError').hidden, false);
});

test('fundcase and zip imports create a distinct result without switching or discarding current drafts', async () => {
  for (const extension of ['fundcase', 'ZIP']) {
    const h = harness(); await h.open(); await h.view('import'); const selected = file('synthetic.' + extension); await h.choose(selected); await h.submit();
    assert.equal(h.calls.import.length, 1); assert.equal(h.calls.import[0][0] === selected, true); assert.equal(h.current.caseId, 2);
    assert.equal(h.calls.leave.length, 0); assert.equal(h.calls.open.length, 0); assert.equal(h.controller.status().result.id, 4);
    assert.match(h.element('caseResultText').textContent, /合成导入案件/);
  }
});

test('closing and reopening a pending creation retains one global task and its submitted name', async () => {
  const pending = deferred(); const h = harness(); await readyCreate(h, '合成冻结名称'); h.io('create', () => pending.promise);
  const submitting = h.submit(); await flush(); await h.close(); assert.equal(h.controller.status().dialogOpen, false);
  await h.open(); const duplicate = h.controller.submit(); await flush(); assert.equal(h.controller.status().pending, true); assert.equal(h.calls.create.length, 1);
  pending.resolve({ id: 3, name: '合成冻结名称' }); await Promise.all([submitting, duplicate]);
  assert.deepEqual(h.calls.create, [['合成冻结名称']]); assert.equal(h.calls.open.length, 0);
});

test('one pending mutation prevents a second write after a context rebind', async () => {
  const pending = deferred(); const h = harness(); await readyCreate(h); h.io('create', () => pending.promise);
  const submitting = h.submit(); await flush(); h.switchBinding(5, 0); await h.open(); await h.view('delete'); const duplicate = h.controller.submit(); await flush();
  assert.equal(writes(h), 1); pending.resolve({ id: 3, name: '合成旧上下文结果' }); await Promise.all([submitting, duplicate]);
  assert.equal(h.calls.open.length, 0); assert.equal(h.current.caseId, 5);
});

test('A to B to A retains a committed global creation result without automatic navigation or rebinding its target', async () => {
  const pending = deferred(); const h = harness(); await readyCreate(h); const original = clone(h.current); h.io('create', () => pending.promise);
  const submitting = h.submit(); await flush(); h.switchBinding(5, 0); h.switchBinding(2, 7); await h.open();
  pending.resolve({ id: 3, name: '合成迟到新案' }); await submitting;
  assert.equal(h.calls.open.length, 0); assert.equal(h.current.caseId, 2); assert.deepEqual(clone(h.controller.status().context), original);
  assert.equal(h.controller.status().result.id, 3); assert.match(h.element('caseResultText').textContent, /合成迟到新案/);
  const explicit = clone(h.current); await h.element('caseOpenResult').click(); await flush(); assert.deepEqual(h.calls.open, [[3, explicit]]);
});

test('known 4xx create failures preserve the draft and allow an explicit retry', async () => {
  for (const status of [409, 422]) {
    const h = harness(); await readyCreate(h); h.io('create', async () => { throw failure('合成已知拒绝', status); }); await h.submit();
    assert.equal(h.controller.status().phase, 'error'); assert.equal(h.element('caseName').value, '合成新案');
    h.io('create', async name => ({ id: 3, name })); await h.submit(); assert.equal(h.calls.create.length, 2); assert.equal(h.controller.status().phase, 'done');
  }
});

test('transport and 5xx create outcomes become unknown and reconciliation only lists cases', async () => {
  for (const status of [undefined, 500, 503]) {
    const h = harness(); await readyCreate(h); h.io('create', async () => { throw failure('合成不确定结果', status); }); await h.submit();
    assert.equal(h.controller.status().phase, 'unknown'); const before = writes(h), reads = h.calls.list.length; await h.submit();
    assert.equal(writes(h), before); assert.equal(h.calls.list.length, reads + 1); assert.equal(h.controller.status().result, null);
    assert.equal(h.calls.open.length, 0); assert.equal(h.element('caseOpenResult').getClientRects().length, 0);
  }
});

test('a same-name item in reconciliation is not proof that the uncertain operation created it', async () => {
  const h = harness(); await readyCreate(h); h.io('create', async () => { throw failure('合成连接中断'); }); await h.submit();
  h.io('list', async () => [{ id: 97, name: '合成新案' }]); await h.submit();
  assert.equal(h.calls.create.length, 1); assert.equal(h.controller.status().result, null); assert.equal(h.calls.open.length, 0);
});

test('after an unknown result a new mutation requires returning home and explicitly starting again', async () => {
  const h = harness(); await readyCreate(h); h.io('create', async () => { throw failure('合成连接中断'); }); await h.submit();
  await h.submit(); assert.equal(h.calls.create.length, 1); await h.back(); assert.equal(h.controller.status().view, 'home');
  await h.view('new'); await h.edit('caseName', '合成明确另发'); h.io('create', async name => ({ id: 4, name })); await h.submit();
  assert.equal(h.calls.create.length, 2); assert.equal(h.calls.create[1][0], '合成明确另发');
});

test('import 5xx outcomes cannot automatically resend the retained package', async () => {
  const h = harness(); await h.open(); await h.view('import'); await h.choose(file('synthetic.fundcase'));
  h.io('import', async () => { throw failure('合成导入结果不确定', 503); }); await h.submit(); const reads = h.calls.list.length; await h.submit();
  assert.equal(h.controller.status().phase, 'unknown'); assert.equal(h.calls.import.length, 1); assert.equal(h.calls.list.length, reads + 1); assert.equal(h.calls.open.length, 0);
});

test('the default case cannot reach a delete mutation', async () => {
  const h = harness(); h.current.caseId = 1; h.summary.defaultCase = true; await h.open();
  assert.equal(h.element('btnDelCase').disabled, true); await h.view('delete'); await h.controller.submit();
  assert.equal(h.calls.delete.length, 0); assert.equal(h.calls.leave.length, 0);
});

test('deletion freezes its target and opens the default case only after a matched successful response', async () => {
  const h = harness(); await h.open(); await h.view('delete'); const before = clone(h.current); await h.submit();
  assert.deepEqual(h.calls.delete, [[2]]); assert.deepEqual(h.calls.leave, [before]); assert.deepEqual(h.calls.open, [[1, before, { discardApproved: true }]]); assert.equal(h.current.caseId, 1);
});

test('reopening after the host closes a completed deletion shows the fresh default case and disables deletion', async () => {
  const h = harness(); await h.open(); await h.view('delete');
  h.io('open', async (id, binding, flags) => {
    assert.equal(id, 1); assert.equal(binding.caseId, 2); assert.equal(flags.discardApproved, true);
    h.current.caseId = 1; h.current.canvasId = 0; h.current.generation += 1; h.current.canvasGeneration += 1;
    Object.assign(h.summary, { name: '合成默认案件', canvasName: '默认画布', flows: 0, accounts: 0, defaultCase: true });
    h.controller.revoke(); return true;
  });
  await h.submit(); assert.equal(h.controller.status().dialogOpen, false); await h.open();
  assert.equal(h.controller.status().view, 'home'); assert.equal(h.controller.status().phase, 'idle');
  assert.deepEqual(clone(h.controller.status().context), clone(h.current)); assert.equal(h.controller.status().result, null);
  assert.match(h.element('caseContext').textContent, /合成默认案件.*默认画布/);
  assert.equal(h.element('caseFeedback').textContent, ''); assert.equal(h.element('btnDelCase').disabled, true);
  await h.view('delete'); await h.submit(); assert.deepEqual(h.calls.delete, [[2]]); assert.equal(h.controller.status().view, 'home');
});

test('canceling the draft-leave guard prevents deletion', async () => {
  const h = harness(); await h.open(); await h.view('delete'); h.io('leave', async () => false); await h.submit();
  assert.equal(h.calls.delete.length, 0); assert.equal(h.calls.open.length, 0); assert.equal(h.current.caseId, 2);
});

test('a pending deletion locks both close buttons Escape and the real backdrop, then unlocks after cancellation or settlement', async () => {
  for (const outcome of ['guard-cancel', 'success', 'known-error']) {
    const h = harness({ withDialogs: true }), pending = deferred(); await h.open(); await h.view('delete');
    if (outcome === 'guard-cancel') h.io('leave', () => pending.promise); else h.io('delete', () => pending.promise);
    const submitting = h.submit(); await flush(); const dialog = h.element('caseActions');
    assert.equal(h.element('caseClose').disabled, true); assert.equal(h.element('caseCloseFooter').disabled, true);
    assert.equal(dialog.hasAttribute('data-capture-locked'), true);
    await h.element('caseClose').click(); await h.element('caseCloseFooter').click();
    const escape = dialog.fire('cancel'); await escape.completion; if (!escape.defaultPrevented) await dialog.close();
    await dialog.fire('pointerdown', { clientX: 0, clientY: 0, button: 0 }).completion;
    await dialog.fire('click', { clientX: 0, clientY: 0, button: 0 }).completion;
    assert.equal(escape.defaultPrevented, true); assert.equal(dialog.open, true);
    const duplicate = h.controller.submit(); assert.equal(h.calls.delete.length, outcome === 'guard-cancel' ? 0 : 1);
    if (outcome === 'known-error') pending.reject(failure('合成明确拒绝', 409));
    else pending.resolve(outcome === 'guard-cancel' ? false : { deleted_flows: 123 });
    await Promise.all([submitting, duplicate]);
    assert.equal(h.controller.status().phase, ({ 'guard-cancel': 'idle', success: 'done', 'known-error': 'error' })[outcome]);
    assert.equal(h.element('caseClose').disabled, false); assert.equal(h.element('caseCloseFooter').disabled, false);
    assert.equal(dialog.hasAttribute('data-capture-locked'), false);
    await h.element('caseCloseFooter').click(); assert.equal(dialog.open, false);
  }
});

test('a context that changes while draft confirmation waits cannot begin deletion', async () => {
  const leave = deferred(); const h = harness(); await h.open(); await h.view('delete'); h.io('leave', () => leave.promise);
  const submitting = h.submit(); await flush(); h.switchBinding(5, 0); leave.resolve(true); await submitting;
  assert.equal(h.calls.delete.length, 0); assert.equal(h.calls.open.length, 0);
});

test('a late successful delete cannot send a newer case back to the default case', async () => {
  const pending = deferred(); const h = harness(); await h.open(); await h.view('delete'); h.io('delete', () => pending.promise);
  const submitting = h.submit(); await flush(); assert.deepEqual(h.calls.delete, [[2]]); h.switchBinding(5, 0);
  pending.resolve({ deleted_flows: 123 }); await submitting; assert.equal(h.calls.open.length, 0); assert.equal(h.current.caseId, 5);
});

test('unknown deletion never retries DELETE or infers a fallback from a read-only list', async () => {
  const h = harness(); await h.open(); await h.view('delete'); h.io('delete', async () => { throw failure('合成删除结果不确定', 500); }); await h.submit();
  const reads = h.calls.list.length; h.io('list', async () => [{ id: 1, name: '默认合成案件' }]); await h.submit();
  assert.deepEqual(h.calls.delete, [[2]]); assert.equal(h.calls.list.length, reads + 1); assert.equal(h.calls.open.length, 0); assert.equal(h.current.caseId, 2);
});

test('export downloads the returned blob before declaring success and does not discard node drafts', async () => {
  const h = harness(); await h.open(); await h.view('export'); const downloading = deferred(); h.io('download', () => downloading.promise);
  const submitting = h.submit(); await flush(); assert.deepEqual(h.calls.export, [[2]]); assert.equal(h.calls.download.length, 1);
  assert.equal(h.calls.download[0][0] instanceof Blob, true); assert.equal(h.calls.download[0][1], '合成案件.fundcase');
  assert.notEqual(h.controller.status().phase, 'done'); assert.equal(h.calls.leave.length, 0); assert.equal(h.calls.save.length, 0);
  downloading.resolve(); await submitting; assert.equal(h.controller.status().phase, 'done');
});

test('closing and reopening a completed package export returns home with a fresh summary and no previous success message', async () => {
  const h = harness(); await h.open(); await h.view('export'); await h.submit();
  assert.equal(h.controller.status().phase, 'done'); assert.equal(h.calls.download.length, 1); await h.close();
  Object.assign(h.summary, { canvasName: '合成更新画布', flows: 130, accounts: 46 }); await h.open();
  assert.equal(h.controller.status().view, 'home'); assert.equal(h.controller.status().phase, 'idle');
  assert.equal(h.controller.status().result, null); assert.equal(h.controller.status().dialogOpen, true);
  assert.match(h.element('caseContext').textContent, /合成更新画布/); assert.match(h.element('caseCounts').textContent, /130.*46/);
  assert.equal(h.element('caseFeedback').textContent, ''); assert.equal(h.element('caseError').hidden, true);
  assert.equal(h.calls.export.length, 1); assert.equal(h.calls.download.length, 1); assert.equal(h.calls.leave.length, 0);
});

test('an expired case export response cannot download into a new binding', async () => {
  const pending = deferred(); const h = harness(); await h.open(); await h.view('export'); h.io('export', () => pending.promise);
  const submitting = h.submit(); await flush(); h.switchBinding(5, 0); h.switchBinding(2, 7);
  pending.resolve({ blob: new Blob(['stale synthetic package']), filename: 'stale.fundcase' }); await submitting;
  assert.equal(h.calls.download.length, 0); assert.equal(h.calls.open.length, 0);
});

test('read-only export failures allow retry rather than being classified as an uncertain mutation', async () => {
  const h = harness(); await h.open(); await h.view('export'); h.io('export', async () => { throw failure('合成导出连接失败'); }); await h.submit();
  assert.equal(h.controller.status().phase, 'error'); h.io('export', async () => ({ blob: new Blob(['synthetic']), filename: 'retry.fundcase' })); await h.submit();
  assert.equal(h.calls.export.length, 2); assert.equal(h.calls.download.length, 1);
});

test('saving the default canvas requires a name and completes before package export', async () => {
  const h = harness(); h.current.canvasId = 0; h.summary.canvasName = '默认画布'; await h.open(); await h.view('export'); await h.check('caseSaveCanvas', true);
  await h.edit('caseCanvasName', ' '); await h.submit(); assert.equal(h.calls.save.length, 0); assert.equal(h.calls.export.length, 0);
  const before = clone(h.current), saved = deferred(); await h.edit('caseCanvasName', '合成全量画布'); h.io('save', () => saved.promise);
  const submitting = h.submit(); await flush(); assert.deepEqual(h.calls.save, [[before, '合成全量画布']]); assert.equal(h.calls.export.length, 0);
  saved.resolve(); await submitting; assert.deepEqual(h.calls.export, [[2]]); assert.equal(h.calls.download.length, 1);
});

test('a failed canvas save never falls through to exporting a package', async () => {
  const h = harness(); await h.open(); await h.view('export'); await h.check('caseSaveCanvas', true);
  h.io('save', async () => { throw failure('合成画布保存失败', 503); }); await h.submit();
  assert.equal(h.calls.save.length, 1); assert.equal(h.calls.export.length, 0); assert.equal(h.calls.download.length, 0); assert.notEqual(h.controller.status().phase, 'done');
});

test('retrying a package read after a successful default-canvas save does not clone the canvas again', async () => {
  const h = harness(); h.current.canvasId = 0; await h.open(); await h.view('export'); await h.check('caseSaveCanvas', true); await h.edit('caseCanvasName', '合成只保存一次');
  h.io('export', async () => { throw failure('合成包读取失败', 503); }); await h.submit();
  assert.equal(h.controller.status().phase, 'error'); assert.equal(h.calls.save.length, 1); assert.equal(h.calls.export.length, 1);
  assert.equal(h.element('caseSaveCanvas').checked, false); assert.match(h.element('caseFeedback').textContent, /已保存/);
  h.io('export', async () => ({ blob: new Blob(['synthetic retry package']), filename: 'retry-after-save.fundcase' })); await h.submit();
  assert.equal(h.calls.save.length, 1); assert.equal(h.calls.export.length, 2); assert.equal(h.calls.download.length, 1);
  assert.equal(h.controller.status().phase, 'done'); assert.equal(h.calls.canvasList.length, 0);
});

test('an uncertain sent canvas save only reconciles canvases and cannot repeat the save or export', async () => {
  const h = harness(); h.current.canvasId = 0; await h.open(); await h.view('export'); await h.check('caseSaveCanvas', true); await h.edit('caseCanvasName', '合成待核对画布');
  h.io('save', async () => { throw Object.assign(failure('合成保存连接中断', 503), { writeSent: true }); }); await h.submit();
  assert.equal(h.controller.status().phase, 'unknown'); assert.match(h.element('caseSubmitLabel').textContent, /画布列表/); const reads = h.calls.list.length;
  await h.submit(); assert.equal(h.calls.save.length, 1); assert.equal(h.calls.export.length, 0); assert.equal(h.calls.list.length, reads);
  assert.deepEqual(h.calls.canvasList, [clone(h.current)]); assert.equal(writes(h), 0);
});

test('a save preparation failure before sending a write remains retryable without an uncertain mutation shortcut', async () => {
  const h = harness(); await h.open(); await h.view('export'); await h.check('caseSaveCanvas', true);
  h.io('save', async () => { throw failure('合成快照读取失败'); }); await h.submit(); assert.equal(h.controller.status().phase, 'error');
  h.io('save', async () => {}); await h.submit(); assert.equal(h.calls.save.length, 2); assert.equal(h.calls.export.length, 1); assert.equal(h.calls.canvasList.length, 0); assert.equal(writes(h), 0);
});

test('a canvas save completed after rebinding cannot start a package export for the new case', async () => {
  const pending = deferred(); const h = harness(); await h.open(); await h.view('export'); await h.check('caseSaveCanvas', true); h.io('save', () => pending.promise);
  const submitting = h.submit(); await flush(); h.switchBinding(5, 9); pending.resolve(); await submitting;
  assert.equal(h.calls.save.length, 1); assert.equal(h.calls.export.length, 0); assert.equal(h.calls.download.length, 0);
});

test('closing from either footer or header restores focus without starting an action', async () => {
  for (const id of ['caseClose', 'caseCloseFooter']) {
    const h = harness(); h.trigger.focus(); await h.open(); await h.element(id).click(); await flush();
    assert.equal(h.controller.status().dialogOpen, false); assert.equal(h.document.activeElement === h.trigger, true); assert.equal(writes(h), 0);
  }
});

async function readyOperation(h, action) {
  if (action === 'new') await readyCreate(h, '合成订阅任务');
  else { await h.open(); await h.view(action); }
  if (action === 'import') await h.choose(file('synthetic-subscribed.fundcase'));
}

for (const [action, io, receipt] of [
  ['new', 'create', { id: 3, name: '合成订阅任务' }],
  ['import', 'import', { case_id: 4, name: '合成订阅导入', flows: 17 }],
  ['delete', 'delete', { deleted_flows: 123 }],
]) {
  test(`onState observes a sent pending ${action} before its slow request settles and closing never cancels it`, async () => {
    const h = harness(), pending = deferred(); await readyOperation(h, action);
    const binding = clone(h.current), baseline = h.states.length;
    h.io(io, () => {
      const observed = h.states.at(-1);
      assert.equal(observed.view, action); assert.equal(observed.phase, 'pending');
      assert.equal(observed.pending, true); assert.equal(observed.sent, true);
      assert.deepEqual(observed.context, binding);
      return pending.promise;
    });
    const submitting = h.submit(); await flush();
    const pendingStates = h.states.slice(baseline).filter(state => state.phase === 'pending');
    assert.ok(pendingStates.some(state => state.pending && !state.sent), 'subscribers see preparation before a write is sent');
    assert.ok(pendingStates.some(state => state.pending && state.sent), 'subscribers see the sent write while its response is still pending');
    assert.equal(h.calls[io].length, 1);
    await h.close();
    assert.equal(h.controller.status().dialogOpen, action === 'delete', 'deletion remains locked; other progress dialogs may close');
    assert.equal(h.controller.status().pending, true); assert.equal(h.controller.status().sent, true);
    await h.controller.submit(); assert.equal(h.calls[io].length, 1, 'closing or another submit cannot send a duplicate write');
    pending.resolve(receipt); await submitting;
    const settled = h.states.at(-1);
    assert.equal(settled.phase, 'done'); assert.equal(settled.pending, false); assert.equal(settled.sent, false);
    assert.equal(h.calls[io].length, 1);
    if (action === 'new' || action === 'import') assert.equal(settled.result.id, receipt.id ?? receipt.case_id);
  });
}

test('onState identifies a readonly pending export while generation continues after its progress dialog closes', async () => {
  const h = harness(), pending = deferred(); await readyOperation(h, 'export');
  const binding = clone(h.current);
  h.io('export', () => {
    const observed = h.states.at(-1);
    assert.equal(observed.view, 'export'); assert.equal(observed.phase, 'pending');
    assert.equal(observed.pending, true); assert.equal(observed.sent, false);
    assert.deepEqual(observed.context, binding); return pending.promise;
  });
  const submitting = h.submit(); await flush();
  assert.equal(h.calls.export.length, 1); assert.equal(h.calls.download.length, 0);
  await h.close(); assert.equal(h.controller.status().dialogOpen, false);
  assert.equal(h.controller.status().pending, true); assert.equal(h.controller.status().view, 'export');
  await h.controller.submit(); assert.equal(h.calls.export.length, 1);
  pending.resolve({ blob: new Blob(['synthetic observed package']), filename: 'synthetic-observed.fundcase' }); await submitting;
  assert.equal(h.calls.download.length, 1); assert.equal(h.calls.download[0][1], 'synthetic-observed.fundcase');
  assert.equal(h.states.at(-1).phase, 'done'); assert.equal(h.states.at(-1).pending, false);
  assert.equal(h.states.at(-1).sent, false); assert.equal(writes(h), 0); assert.equal(h.calls.save.length, 0);
});

for (const [action, io] of [['new', 'create'], ['import', 'import'], ['delete', 'delete']]) {
  test(`a committed ${action} with a failed case-list refresh stays complete and cannot repeat its write`, async () => {
    const h = harness(); await readyOperation(h, action);
    h.io('list', async () => { throw failure('合成案件列表暂不可用', 503); });
    await h.submit();
    const completed = h.states.at(-1);
    assert.equal(completed.phase, 'done'); assert.equal(completed.pending, false);
    assert.equal(completed.refreshError, '案件列表刷新失败');
    assert.equal(h.controller.status().phase, 'done'); assert.equal(h.calls[io].length, 1);
    assert.match(h.element('caseFeedback').textContent, /刷新失败/);
    if (action !== 'delete') assert.ok(completed.result, 'a saved case receipt survives a failed list refresh');
    await h.submit(); assert.equal(h.calls[io].length, 1);
    assert.equal(h.controller.status().phase, 'done'); assert.equal(h.controller.status().refreshError, '案件列表刷新失败');
  });
}

for (const [action, io] of [['new', 'create'], ['delete', 'delete']]) {
  test(`a committed ${action} with a failed automatic open reports refresh trouble without becoming unknown or resending`, async () => {
    const h = harness({ autoOpenCreated: true }); await readyOperation(h, action);
    h.io('open', async () => { throw failure('合成工作区暂不可用', 503); });
    await h.submit();
    const completed = h.states.at(-1);
    assert.equal(completed.phase, 'done'); assert.equal(completed.pending, false);
    assert.equal(completed.refreshError, '界面刷新失败'); assert.match(h.element('caseError').textContent, /操作已完成/);
    assert.equal(h.calls[io].length, 1); assert.equal(h.calls.open.length, 1);
    await h.submit(); assert.equal(h.calls[io].length, 1); assert.equal(h.calls.open.length, 1);
    assert.equal(h.controller.status().phase, 'done');
  });
}
