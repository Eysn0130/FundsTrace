import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/analysis.js', import.meta.url), 'utf8');
const page = readFileSync(new URL('../static/analysis.html', import.meta.url), 'utf8');
const money = readFileSync(new URL('../static/money.js', import.meta.url), 'utf8');
const tableScroll = readFileSync(new URL('../static/table-scroll.js', import.meta.url), 'utf8');
const cases = [{ id: 1, name: '合成甲案' }, { id: 2, name: '合成乙案' }];
const flush = async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); };
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const response = (value, status = 200) => ({ ok: status >= 200 && status < 300, status,
  json: async () => value, text: async () => JSON.stringify(value), blob: async () => ({ name: 'synthetic-existing.png' }) });
const decode = value => value.replace(/&(amp|lt|gt|quot|#39);/g, (_match, key) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" }[key]));

// The DOM preserves real ancestry, detached-control identity and event order.
// Analysis rendering, input events, dialog callbacks and shared scrolling run unchanged.
function harness({ url = 'http://synthetic.invalid/analysis?case_id=1', initialCases = cases, initialNetwork } = {}) {
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
    get href() { return this.attributes.get('href') || ''; }
    set href(value) { this.attributes.set('href', String(value)); }
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
  document.body.innerHTML = /<body\b[^>]*>([\s\S]*)<\/body>/i.exec(page)[1];
  const requests = [], downloads = [], objectUrls = [], revokedUrls = [], alerts = [], timers = [];
  const storage = new Map([['fund_case', '1']]);
  let network = initialNetwork || (async path => path === '/api/cases' ? initialCases : path.startsWith('/api/stats?') ? { flows: 8, accounts: 4 } : { rows: [], total: 0 });
  class SyntheticURL extends URL {
    static createObjectURL(blob) { const url = `blob:analysis-${objectUrls.length + 1}`; objectUrls.push({ url, blob }); return url; }
    static revokeObjectURL(url) { revokedUrls.push(url); }
  }
  const window = new Element('#window'); window.appendChild(document.root);
  const location = new URL(url);
  window.location = { get href() { return location.href; }, set href(value) { location.href = new URL(value, location).href; },
    get search() { return location.search; }, get origin() { return location.origin; }, get pathname() { return location.pathname; } };
  window.history = { replaceState(_state, _title, value) { window.location.href = new URL(value, window.location.href).href; } };
  const context = vm.createContext({ window, document, URL: SyntheticURL, URLSearchParams, Blob,
    fetch: async (path, options) => { requests.push({ path, options }); const value = await network(path, options); return value && typeof value.ok === 'boolean' ? value : response(value); },
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, String(value)) },
    alert: message => alerts.push(String(message)),
    setTimeout: (callback, delay = 0) => { timers.push({ callback, delay }); return timers.length; }, clearTimeout() {},
    requestAnimationFrame: callback => { callback(); return 0; }, cancelAnimationFrame() {},
  });
  vm.runInContext(money, context, { filename: 'money.js' });
  vm.runInContext(tableScroll, context, { filename: 'table-scroll.js' });
  vm.runInContext(source, context, { filename: 'analysis.js' });
  const element = id => document.getElementById(id);
  return { document, window, requests, downloads, objectUrls, revokedUrls, alerts, timers, storage, element,
    ready: flush, network(handler) { network = handler; }, run(code) { return vm.runInContext(code, context); },
    async algorithms(names) { for (const node of document.querySelectorAll('.alg')) { node.checked = names.includes(node.value); await node.fire('change').completion; } },
    edit(id, value) { const input = element(id); assert.ok(input, `missing input #${id}`); input.value = value; return input.fire('input').completion; },
    async select(id) { element('caseSel').value = String(id); await element('caseSel').fire('change').completion; await flush(); },
    async accept() { const dialog = element('analysisConfirm'); assert.equal(dialog?.open, true); await element('confirmAccept').click(); await flush(); },
    async cancel() { const dialog = element('analysisConfirm'); assert.equal(dialog?.open, true); await element('confirmCancel').click(); await flush(); },
  };
}

const result = (marker = '合成环路') => ({ case_id: 1, algorithms: ['cycles'], subjects: [], source_snapshot: { flows: 3, accounts: 2 },
  cycles: { count: 1, window_days: 7, cycles: [{ accounts_names: [marker, '合成乙'], edges: [{ id: '17' }, { id: '18' }], len: 2, total: '12.34', currency: 'CNY', span_days: 1 }] } });
const emptyResult = (algorithms = ['cycles']) => ({ case_id: 1, algorithms, subjects: [], source_snapshot: { flows: 3, accounts: 2 },
  ...(algorithms.includes('cycles') ? { cycles: { count: 0, window_days: 7, cycles: [] } } : {}),
  ...(algorithms.includes('fast_flows') ? { fast_flows: { count: 0, window_minutes: 30, tolerance: .25, pairs: [] } } : {}),
  ...(algorithms.includes('outliers') ? { outliers: { count: 0, sigma: 2.5, outliers: [] } } : {}),
  ...(algorithms.includes('paths') ? { paths: { count: 0, src: 'SYN-A', dst: 'SYN-B', max_hops: 4, paths: [] } } : {}),
});
const posts = (h, path = '/api/analysis/run') => h.requests.filter(request => request.path === path);
const paramsOf = request => JSON.parse(request.options.body).params;
const feedback = h => ['analysisError', 'runStatus', 'reportStatus', 'caseStats'].map(id => h.element(id)?.textContent || '').join(' ') + h.alerts.join(' ');
async function finishedRun(h, value = result()) { await h.algorithms(value.algorithms || ['cycles']); h.network(() => value); await h.run('run()'); }

const adapter = readFileSync(new URL('../static/analysis-link.js', import.meta.url), 'utf8');
async function linkedHarness() {
  const h = harness({ url: 'http://synthetic.invalid/analysis?embedded=1&case_id=1' });
  await h.ready();
  const messages = [], parent = { postMessage(message, origin) { messages.push({ message, origin }); } };
  h.window.parent = parent;
  h.run('globalThis.parent=window.parent;globalThis.location=window.location;');
  h.run(adapter); await h.ready();
  const receive = (message, extra = {}) => h.window.fire('message', { origin: h.window.location.origin, source: parent,
    data: { channel: 'fundstrace:workspace', version: 1, ...message }, ...extra }).completion;
  const bind = (caseId = '1', generation = 1, extra = {}) => receive({ type: 'context', caseId, generation, name: `合成案件${caseId}`, revision: 0, surface: 'analysis', active: true, retainedCaseIds: [], ...extra });
  const last = () => messages.at(-1).message;
  await bind(); await h.ready();
  return { ...h, messages, receive, bind, last };
}

test('analysis requests a parent-owned case and preserves its draft across home and case changes', async () => {
  const h = await linkedHarness(); await h.edit('subjects', 'SYN-CASE-A'); await h.edit('pSrc', '000-A');
  await h.bind('1', 1, { active: false }); await h.bind('1', 1, { active: true });
  assert.equal(h.element('subjects').value, 'SYN-CASE-A');
  const selecting = h.select(2); await flush();
  const message = h.last(); assert.equal(message.action, 'select-case'); assert.equal(message.payload.caseId, '2');
  assert.equal(h.element('caseSel').value, '1');
  await h.bind('2', 2); await h.receive({ type: 'result', requestId: message.requestId, ok: true }); await selecting;
  assert.equal(h.element('caseSel').value, '2'); assert.equal(h.element('subjects').value, '');
  await h.edit('subjects', 'SYN-CASE-B'); await h.bind('1', 3); await h.ready();
  assert.equal(h.element('subjects').value, 'SYN-CASE-A'); assert.equal(h.element('pSrc').value, '000-A');
  const returning = h.element('backToGraph').click(); await flush();
  const back = h.last(); assert.equal(back.action, 'open-graph'); assert.equal(back.generation, 3);
  await h.receive({ type: 'result', requestId: back.requestId, ok: true }); await returning;
  assert.equal(h.window.location.pathname, '/analysis', 'the embedded page never navigates away from its retained host');
});

test('a same-case list refresh preserves unsaved analysis inputs and completed results', async () => {
  const h = await linkedHarness(); await h.algorithms(['cycles']);
  h.network(path => path === '/api/cases' ? cases : path === '/api/analysis/run' ? result('RETAINED-RESULT') : { flows: 3, accounts: 2 });
  await h.run('run()'); await h.edit('subjects', 'UNSUBMITTED-SUBJECT'); await h.edit('pSrc', '0000123');
  await h.receive({ type: 'cases-changed' }); await h.ready();
  assert.equal(h.element('subjects').value, 'UNSUBMITTED-SUBJECT'); assert.equal(h.element('pSrc').value, '0000123');
  assert.match(h.element('results').textContent, /RETAINED-RESULT/);
});

test('a host case rebinding rejects an old in-flight result without hiding the new case', async () => {
  const h = await linkedHarness(); await h.algorithms(['cycles']); const pending = deferred();
  h.network(path => path === '/api/analysis/run' ? pending.promise : { flows: 0, accounts: 0 });
  const running = h.run('run()'); await flush();
  await h.bind('2', 2); pending.resolve(result('STALE-CASE-A')); await running;
  assert.equal(h.element('caseSel').value, '2'); assert.doesNotMatch(h.element('results').textContent, /STALE-CASE-A/);
  assert.equal(h.element('btnReport').disabled, true);
});

test('a failed host request from an earlier binding cannot write an error into the new case', async () => {
  const h = await linkedHarness();
  const selecting = h.select(2); await flush(); const old = h.last();
  await h.bind('2', 2); await h.bind('1', 3);
  await h.receive({ type: 'result', requestId: old.requestId, ok: false, error: 'STALE-HOST-FAILURE' }); await selecting;
  assert.equal(h.element('caseSel').value, '1'); assert.doesNotMatch(h.element('analysisError').textContent, /STALE-HOST-FAILURE/);
});

test('data revisions revoke completed-result actions without automatically rerunning analysis', async () => {
  const h = await linkedHarness(); await h.algorithms(['cycles']);
  h.network(path => path === '/api/analysis/run' ? result('HISTORICAL-RESULT') : { flows: 4, accounts: 3 });
  await h.run('run()'); const before = posts(h).length;
  await h.bind('1', 1, { revision: 1 }); await h.ready();
  assert.equal(h.element('btnReport').disabled, true);
  assert.match(h.element('resultScope').textContent, /数据已更新/);
  assert.equal(posts(h).length, before);
  assert.equal(h.element('results').querySelectorAll('button').every(button => button.disabled), true);
});

test('one-click cycle tracking and canvas opening both use the owning host without a child POST', async () => {
  const h = await linkedHarness(); await h.algorithms(['cycles']);
  h.network(path => path === '/api/analysis/run' ? result('HOST-TRACKING') : { flows: 3, accounts: 2 });
  await h.run('run()'); const button = h.element('results').querySelector('.btn-track');
  await button.click(); await flush();
  const track = h.last(); assert.equal(track.action, 'track-cycle');
  assert.deepEqual(JSON.parse(JSON.stringify(track.payload.flow_ids)), ['17', '18']);
  assert.equal(h.requests.some(request => request.path === '/api/canvases/from-flows'), false);
  await h.receive({ type: 'result', requestId: track.requestId, ok: true, result: { canvas_id: '7', added: 2 } }); await flush();
  const link = h.element('results').querySelector('.btn-graph'); assert.ok(link);
  const opening = link.click(); await flush();
  const open = h.last(); assert.equal(open.action, 'open-canvas'); assert.equal(open.payload.canvasId, '7');
  assert.equal(open.payload.flowId, '17');
  await h.receive({ type: 'result', requestId: open.requestId, ok: true }); await opening;
  assert.equal(h.window.location.pathname, '/analysis');
});

test('a host write-lock rejection cannot be bypassed by one-click cycle tracking', async () => {
  const h = await linkedHarness(); await h.algorithms(['cycles']);
  h.network(path => path === '/api/analysis/run' ? result() : { flows: 3, accounts: 2 }); await h.run('run()');
  await h.element('results').querySelector('.btn-track').click(); await flush(); const track = h.last();
  await h.receive({ type: 'result', requestId: track.requestId, ok: false, error: '合成宿主写入未确认' }); await flush();
  assert.match(h.element('analysisError').textContent, /合成宿主写入未确认/);
  assert.equal(h.requests.some(request => request.path === '/api/canvases/from-flows'), false);
  assert.equal(h.element('results').querySelector('.btn-graph'), null);
});

test('a host case change during list refresh reenables controls after the same-case response', async () => {
  const h = await linkedHarness(), pending = deferred();
  h.network(path => path === '/api/cases' ? pending.promise : { flows: 3, accounts: 2 });
  await h.receive({ type: 'cases-changed' }); await flush();
  assert.equal(h.element('caseSel').disabled, true);
  await h.bind('2', 2); await h.ready();
  assert.equal(h.element('caseSel').value, '2');
  assert.equal(h.element('btnRun').disabled, true, 'the case list is still loading when the new context arrives');
  pending.resolve(cases); await h.ready();
  assert.equal(h.element('caseSel').value, '2');
  assert.equal(h.element('caseSel').disabled, false);
  assert.equal(h.element('btnRun').disabled, false);
  assert.equal(h.element('subjects').disabled, false);
});

test('a case list failure and retry retain dirty inputs and algorithms without replaying results', async () => {
  const h = await linkedHarness(); await h.algorithms(['cycles']);
  h.network(path => path === '/api/analysis/run' ? result('BEFORE-READ-FAILURE') : { flows: 3, accounts: 2 });
  await h.run('run()');
  await h.algorithms(['cycles', 'paths']); await h.edit('subjects', 'SYN-UNSAVED');
  await h.edit('pSrc', '000123'); await h.edit('pDst', 'ACCT.0');
  const runs = posts(h).length;
  assert.equal(h.element('draftStatus').textContent, '参数已修改');
  h.network(path => path === '/api/cases' ? response({ detail: 'Synthetic list unavailable' }, 500) : { flows: 3, accounts: 2 });
  await h.receive({ type: 'cases-changed' }); await h.ready();
  assert.equal(h.element('subjects').value, 'SYN-UNSAVED');
  assert.equal(h.element('pSrc').value, '000123'); assert.equal(h.element('pDst').value, 'ACCT.0');
  assert.equal(h.element('btnRun').disabled, true);
  h.network(path => path === '/api/cases' ? cases : { flows: 3, accounts: 2 });
  await h.run('loadCases()'); await h.ready();
  assert.equal(h.element('caseSel').value, '1');
  assert.equal(h.element('subjects').value, 'SYN-UNSAVED');
  assert.equal(h.element('pSrc').value, '000123'); assert.equal(h.element('pDst').value, 'ACCT.0');
  assert.equal(h.document.querySelectorAll('.alg').filter(input => input.checked).map(input => input.value).join(','), 'cycles,paths');
  assert.equal(h.element('draftStatus').textContent, '参数已修改');
  assert.equal(h.element('btnRun').disabled, false); assert.equal(h.element('btnReport').disabled, true);
  assert.doesNotMatch(h.element('results').textContent, /BEFORE-READ-FAILURE/);
  assert.equal(posts(h).length, runs, 'restoring inputs never runs analysis or reconstructs its business results');
});

test('a host case change during list recovery cannot replace another case draft with cleared loader fields', async () => {
  const h = await linkedHarness(); await h.edit('subjects', 'SYN-CASE-A-DRAFT');
  h.network(path => path === '/api/cases' ? response({ detail: 'Synthetic list unavailable' }, 500) : { flows: 3, accounts: 2 });
  await h.receive({ type: 'cases-changed' }); await h.ready();
  await h.bind('2', 2);
  h.network(path => path === '/api/cases' ? cases : { flows: 3, accounts: 2 });
  await h.run('loadCases()'); await h.ready();
  assert.equal(h.element('caseSel').value, '2'); assert.equal(h.element('subjects').value, '');
  await h.bind('1', 3); await h.ready();
  assert.equal(h.element('subjects').value, 'SYN-CASE-A-DRAFT');
  assert.equal(h.element('draftStatus').textContent, '参数已修改');
  assert.equal(posts(h).length, 0);
});
