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
function harness({ url = 'http://synthetic.invalid/analysis?case_id=1', initialCases = cases, initialNetwork, entitySearch = false } = {}) {
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
      // Traverse once. Recursively querying '*' re-filtered each descendant at
      // every ancestor and dominated the workspace's synthetic runtime.
      const all = [], collect = parent => { for (const child of parent.children) { all.push(child); collect(child); } };
      collect(this);
      const branches = selector.split(',').map(part => part.trim().split(/\s+/));
      return all.filter(node => branches.some(tokens => {
        if (!matches(node, tokens.at(-1))) return false;
        let parent = node.parentElement;
        for (let index = tokens.length - 2; index >= 0; index--) {
          while (parent && !matches(parent, tokens[index])) parent = parent.parentElement;
          if (!parent) return false;
          parent = parent.parentElement;
        }
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
  const feedbackEvents = [], feedbackBindings = [], feedbackNotices = [];
  window.FundsFeedback = { bind: scope => feedbackBindings.push({ ...scope }),
    task: event => feedbackEvents.push(event), notify: event => feedbackNotices.push(event) };
  const pickerMounts = [];
  if (entitySearch) window.FundsEntitySearch = { mount(input, options) {
    const control = { input, options, invalidations: 0, invalidate() { this.invalidations += 1; } };
    pickerMounts.push(control); return control;
  } };
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
  return { document, window, requests, downloads, objectUrls, revokedUrls, alerts, timers, storage, element, pickerMounts,
    feedbackEvents, feedbackBindings, feedbackNotices,
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

test('synthetic selector traversal preserves document order, distinct branches and ancestor matching', async () => {
  const h = harness(); await h.ready();
  const fixture = h.document.createElement('section'); fixture.className = 'field';
  fixture.innerHTML = '<div class="group"><span>text</span><input id="first" class="field" value="A"><div><input id="second" class="field" disabled checked></div></div><input id="last" class="field">';
  assert.deepEqual(fixture.querySelectorAll('.field').map(node => node.id), ['first', 'second', 'last']);
  assert.deepEqual(fixture.querySelectorAll('.group input').map(node => node.id), ['first', 'second']);
  assert.deepEqual(fixture.querySelectorAll('.group input, #first, #last').map(node => node.id), ['first', 'second', 'last']);
  assert.deepEqual(fixture.querySelectorAll('input:not(:disabled)').map(node => node.id), ['first', 'last']);
  assert.deepEqual(fixture.querySelectorAll('input:checked').map(node => node.id), ['second']);
  const first = fixture.querySelector('[value="A"]'); first.remove();
  assert.deepEqual(fixture.querySelectorAll('.field').map(node => node.id), ['second', 'last']);
  assert.equal(first.matches('.field'), true); assert.equal(first.isConnected, false);
  assert.equal(fixture.querySelectorAll('*').some(node => node.tagName === '#TEXT'), false);
});

test('analysis account pickers are case-bound fill controls and never submit or locate', async () => {
  const h = harness({ entitySearch: true }); await h.ready();
  assert.deepEqual(h.pickerMounts.map(control => control.input.id), ['pSrc', 'pDst', 'subjects']);
  for (const { options } of h.pickerMounts) {
    assert.equal(options.caseId(), '1'); assert.equal(options.source, 'flows'); assert.equal(options.kind, 'account');
    assert.equal(options.onSubmit, undefined); assert.equal(options.onSelect, undefined);
  }
  const [src, dst, subjects] = h.pickerMounts;
  assert.equal(src.options.scope(), null); assert.equal(dst.options.scope(), null);
  assert.notEqual(subjects.options.scope(), null); assert.equal(subjects.options.ghost, false);
  await h.algorithms(['paths']);
  assert.notEqual(src.options.scope(), null); assert.equal(subjects.options.scope(), null);
  await h.edit('pSrc', '00000000000000000001'); await h.edit('pDst', '90071992547409931234');
  await h.element('pSrc').fire('keydown', { key: 'Enter' }).completion;
  assert.equal(posts(h).length, 0);
  assert.equal(h.requests.some(request => request.path.startsWith('/api/graph') || request.path.startsWith('/api/track')), false);
  const pending = deferred(), prior = src.invalidations;
  h.network(() => pending.promise);
  const running = h.run('run()'); await h.ready();
  assert.equal(src.options.scope(), null); assert.ok(src.invalidations > prior);
  const params = paramsOf(posts(h)[0]);
  assert.equal(params.path_src, '00000000000000000001'); assert.equal(params.path_dst, '90071992547409931234');
  pending.resolve(emptyResult(['paths'])); await running;
  assert.notEqual(src.options.scope(), null);
});

test('subjects query only the current token without replacing surrounding account strings', async () => {
  const h = harness({ entitySearch: true }); await h.ready();
  const input = h.element('subjects'), { options } = h.pickerMounts.find(control => control.input === input);
  input.value = '000001，张三丰；000003\n000004';
  input.selectionStart = input.selectionEnd = input.value.indexOf('三') + 1;
  assert.deepEqual(JSON.parse(JSON.stringify(options.token(input))), { query: '张三丰', start: 7, end: 10 });
  input.selectionStart = input.selectionEnd = input.value.length;
  assert.deepEqual(JSON.parse(JSON.stringify(options.token(input))), { query: '000004', start: 18, end: 24 });
  input.selectionStart = 0; input.selectionEnd = 9;
  assert.equal(options.token(input).query, '', 'a selection across separators must not replace multiple objects');
  await h.edit('subjects', '000001，00000000000000000002；90071992547409931234');
  assert.equal(posts(h).length, 0);
  await h.algorithms(['cycles']); h.network(() => emptyResult()); await h.run('run()');
  assert.deepEqual(paramsOf(posts(h)[0]).subjects, ['000001', '00000000000000000002', '90071992547409931234']);
  await h.edit('subjects', ''); h.network(() => emptyResult()); await h.run('run()');
  assert.deepEqual(paramsOf(posts(h).at(-1)).subjects, [], 'empty subjects retain whole-case analysis');
});

test('case switching including A-B-A revokes every analysis picker scope and clears objects', async () => {
  const h = harness({ entitySearch: true }); await h.ready(); await h.algorithms(['paths']);
  const src = h.pickerMounts[0], firstScope = src.options.scope(), prior = src.invalidations;
  h.element('pSrc').value = '000001'; h.element('pDst').value = '000002'; h.element('subjects').value = '000003';
  h.run('selectAnalysisCase(2);selectAnalysisCase(1)');
  assert.equal(src.options.caseId(), '1'); assert.notEqual(src.options.scope(), firstScope);
  assert.ok(src.invalidations >= prior + 2);
  for (const id of ['pSrc', 'pDst', 'subjects']) assert.equal(h.element(id).value, '');
  assert.equal(posts(h).length, 0);
});

test('same-case embedded data invalidation also clears outstanding account candidates', async () => {
  const h = harness({ entitySearch: true }); await h.ready();
  const subjects = h.pickerMounts.find(control => control.input.id === 'subjects'), prior = subjects.invalidations;
  h.run("setAnalysisPhase('ready')");
  assert.ok(subjects.invalidations > prior);
  assert.equal(subjects.options.caseId(), '1'); assert.equal(posts(h).length, 0);
});

test('analysis case identifiers remain strings through selection scope and execution', async () => {
  const id = '90071992547409931234';
  const h = harness({ url: `http://synthetic.invalid/analysis?case_id=${id}`, initialCases: [{ id, name: 'Synthetic exact case string' }], entitySearch: true });
  await h.ready(); assert.equal(h.run('caseId'), id); assert.equal(h.pickerMounts[0].options.caseId(), id);
  await h.algorithms(['cycles']); h.network(() => ({ ...emptyResult(), case_id: id })); await h.run('run()');
  assert.equal(JSON.parse(posts(h)[0].options.body).case_id, id);
});

test('the paths shortcut reveals its required parameters before any input event', async () => {
  const h = harness({ url: 'http://synthetic.invalid/analysis?case_id=1&algorithm=paths' }); await h.ready();
  assert.equal(h.document.querySelectorAll('.alg').filter(node => node.checked).map(node => node.value).join(','), 'paths');
  assert.equal(h.element('pathParams').hidden, false); assert.notEqual(h.element('pathParams').style.display, 'none');
  assert.equal(h.requests.some(request => request.path === '/api/analysis/run'), false);
});

test('two rendered paths do not acquire array-separator commas between their rows', async () => {
  const h = harness(); await h.ready();
  h.run(`render({paths:{count:2,src:'SYN-A',dst:'SYN-B',max_hops:4,paths:[
    {total:'12.34',currency:'CNY',hops:1,path:[{from:'SYN-A',to:'SYN-B',amount:'12.34',currency:'CNY'}]},
    {total:'56.78',currency:'USD',hops:1,path:[{from:'SYN-A',to:'SYN-B',amount:'56.78',currency:'USD'}]}
  ]}},['paths'])`);
  const table = h.element('results').querySelector('table'); assert.ok(table);
  assert.equal(table.textContent.includes(','), false, 'independent small totals and synthetic names contain no commas');
});

test('parameters for unselected algorithms neither block nor enter a cycles-only request', async () => {
  const h = harness(); await h.ready(); await h.algorithms(['cycles']);
  await h.edit('ffWin', 'not-a-window'); await h.edit('ffTol', 'not-a-tolerance'); await h.edit('pHops', '999');
  h.network(() => result()); await h.run('run()');
  const request = h.requests.find(request => request.path === '/api/analysis/run'); assert.ok(request, h.alerts.join('; '));
  const params = JSON.parse(request.options.body).params;
  assert.equal(params.cycle_window_days, 7);
  for (const name of ['ff_window_minutes', 'ff_tolerance', 'path_src', 'path_dst', 'path_max_hops']) assert.equal(Object.hasOwn(params, name), false, name);
});

test('a rerun revokes the old rendered result while its next response is pending', async () => {
  const h = harness(); await h.ready(); await h.algorithms(['cycles']);
  // Keep the unrelated legacy ratio field valid so this regression reaches
  // rerendering even while the new HTML and old JS are being integrated.
  await h.edit('ffTol', '0.25');
  h.network(() => result('合成旧结果')); await h.run('run()'); assert.match(h.element('results').textContent, /合成旧结果/);
  const pending = deferred(); h.network(() => pending.promise); const run = h.run('run()');
  const visible = h.element('results').textContent.includes('合成旧结果'); pending.resolve(result('合成新结果')); await run;
  assert.equal(visible, false, 'old findings must not remain actionable as a new analysis starts');
  assert.match(h.element('results').textContent, /合成新结果/);
});

test('an old detail button cannot request flows after a rerun has revoked its rendered binding', async () => {
  const h = harness(); await h.ready(); await h.algorithms(['cycles']);
  await h.edit('ffTol', '0.25');
  h.network(() => result()); await h.run('run()'); const old = h.element('results').querySelector('.btn-detail'); assert.ok(old);
  const next = deferred(); h.network(path => path === '/api/analysis/run' ? next.promise : { rows: [], total: 0 });
  const running = h.run('run()'); await old.click(); await flush();
  const reads = h.requests.filter(request => request.path.startsWith('/api/flows?')).length;
  next.resolve(result('合成新绑定')); await running;
  assert.equal(reads, 0, 'the prior result is no longer an interactive analysis binding');
});

test('URL case selection wins over saved selection and updates graph navigation', async () => {
  const h = harness({ url: 'http://synthetic.invalid/analysis?case_id=2' }); await h.ready();
  assert.equal(h.element('caseSel').value, '2'); assert.equal(h.storage.get('fund_case'), '2');
  assert.equal(h.element('backToGraph').href, '/?case_id=2');
  assert.ok(h.requests.some(request => request.path === '/api/stats?case_id=2'));
  assert.equal(h.element('btnRun').disabled, false);
});

test('an empty case list cannot submit an analysis or leave a phantom selected case', async () => {
  const h = harness({ initialCases: [] }); await h.ready();
  assert.equal(h.element('btnRun').disabled, true); assert.equal(h.element('btnReport').disabled, true);
  await h.run('run()'); assert.equal(posts(h).length, 0);
  assert.notEqual(h.element('caseStats').textContent.trim(), '');
});

test('case loading failure offers a real retry and does not submit without a case', async () => {
  const h = harness({ initialNetwork: () => response({ detail: '合成案件读取失败' }, 503) }); await h.ready();
  assert.equal(h.element('caseRetry').hidden, false); assert.equal(h.element('btnRun').disabled, true);
  await h.run('run()'); assert.equal(posts(h).length, 0);
  h.network(path => path === '/api/cases' ? cases : { flows: 8, accounts: 4 });
  await h.element('caseRetry').click(); await flush();
  assert.equal(h.element('caseRetry').hidden, true); assert.equal(h.element('caseSel').value, '1');
  assert.equal(h.element('btnRun').disabled, false);
});

test('a late older case-list response cannot replace the newer selected list', async () => {
  const first = deferred(); const h = harness({ initialNetwork: path => path === '/api/cases' ? first.promise : { flows: 1, accounts: 1 } });
  h.network(path => path === '/api/cases' ? [{ id: 2, name: '合成新列表' }] : { flows: 2, accounts: 2 });
  await h.run('loadCases()'); first.resolve([{ id: 1, name: '合成过期列表' }]); await flush();
  assert.equal(h.element('caseSel').value, '2'); assert.equal(h.element('caseSel').textContent.includes('合成过期列表'), false);
});

test('stats failure can be retried without switching the selected case', async () => {
  const h = harness(); await h.ready(); h.network(() => response({ detail: '合成统计失败' }, 503));
  await h.run('showStats()'); assert.equal(h.element('statsRetry').hidden, false);
  assert.equal(h.element('caseSel').value, '1');
  h.network(() => ({ flows: 19, accounts: 7 })); await h.element('statsRetry').click(); await flush();
  assert.equal(h.element('statsRetry').hidden, true); assert.match(h.element('caseStats').textContent, /19/);
});

test('stats reject late A responses after A to B to A rebinding', async () => {
  const h = harness(); await h.ready(); const stale = deferred();
  h.network(() => stale.promise); const loading = h.run('showStats()');
  h.network(() => ({ flows: 71, accounts: 17 })); await h.select(2); await h.select(1);
  stale.resolve({ flows: 999, accounts: 999 }); await loading;
  assert.match(h.element('caseStats').textContent, /71/); assert.doesNotMatch(h.element('caseStats').textContent, /999/);
});

test('fast-flow percent values are converted once and exact zero remains zero', async () => {
  const h = harness(); await h.ready(); await h.algorithms(['fast_flows']);
  assert.equal(h.element('ffTol').value, '25');
  h.network(() => emptyResult(['fast_flows'])); await h.run('run()');
  assert.equal(paramsOf(posts(h)[0]).ff_tolerance, .25); assert.equal(paramsOf(posts(h)[0]).ff_window_minutes, 30);
  await h.edit('ffTol', '0'); await h.run('run()'); assert.equal(paramsOf(posts(h)[1]).ff_tolerance, 0);
  assert.equal(Object.hasOwn(paramsOf(posts(h)[1]), 'cycle_window_days'), false);
});

test('selected parameter limits block requests and preserve the user draft', async () => {
  const h = harness(); await h.ready(); await h.algorithms(['fast_flows']);
  for (const [id, value] of [['ffWin', '4'], ['ffWin', '1441'], ['ffTol', '-1'], ['ffTol', '101']]) {
    await h.edit('ffWin', '30'); await h.edit('ffTol', '25'); await h.edit(id, value);
    await h.run('run()'); assert.equal(posts(h).length, 0); assert.equal(h.element(id).value, value); assert.notEqual(feedback(h).trim(), '');
  }
  await h.algorithms(['cycles']); await h.edit('cycDays', '366'); await h.run('run()'); assert.equal(posts(h).length, 0);
});

test('path requests preserve synthetic long account strings and do not send subject-only fields', async () => {
  const h = harness({ url: 'http://synthetic.invalid/analysis?algorithm=paths' }); await h.ready();
  await h.edit('pSrc', ' 00000000000000000001 '); await h.edit('pDst', '90071992547409931234'); await h.edit('pHops', '12');
  await h.edit('cycDays', 'bad'); await h.edit('ffTol', 'bad'); await h.edit('subjects', '合成忽略对象');
  h.network(() => emptyResult(['paths'])); await h.run('run()'); const params = paramsOf(posts(h)[0]);
  assert.equal(params.path_src, '00000000000000000001'); assert.equal(params.path_dst, '90071992547409931234'); assert.equal(params.path_max_hops, 12);
  for (const field of ['cycle_window_days', 'ff_window_minutes', 'ff_tolerance']) assert.equal(Object.hasOwn(params, field), false);
  assert.equal(h.element('subjectParams').hidden, true);
});

test('Enter editing and selecting result tabs never launch an implicit analysis', async () => {
  const h = harness(); await h.ready(); await h.edit('cycDays', '14'); await h.element('cycDays').fire('keydown', { key: 'Enter' }).completion;
  assert.equal(posts(h).length, 0);
  await finishedRun(h, emptyResult(['cycles', 'fast_flows', 'outliers'])); const before = posts(h).length;
  const selected = h.document.querySelectorAll('.alg').filter(node => node.checked).map(node => node.value).join(',');
  const tabs = h.element('resultTabs').querySelectorAll('[role="tab"]'); assert.equal(tabs.filter(tab => !tab.disabled).length, 3);
  assert.equal(tabs.find(tab => tab.dataset.algorithm === 'paths').disabled, true);
  await h.element('resultTab-fast_flows').click(); assert.equal(posts(h).length, before);
  assert.equal(h.document.querySelectorAll('.alg').filter(node => node.checked).map(node => node.value).join(','), selected);
});

test('run guards duplicate direct calls while its response is pending', async () => {
  const h = harness(); await h.ready(); await h.algorithms(['cycles']); const pending = deferred(); h.network(() => pending.promise);
  const first = h.run('run()'); const second = h.run('run()'); await flush();
  const count = posts(h).length; pending.resolve(result()); await Promise.all([first, second]);
  assert.equal(count, 1); assert.equal(h.element('btnRun').disabled, false);
});

test('a late run cannot repaint after switching A to B to A', async () => {
  const h = harness(); await h.ready(); await h.algorithms(['cycles']); const pending = deferred(); h.network(() => pending.promise);
  const old = h.run('run()'); h.network(() => ({ flows: 8, accounts: 4 })); await h.select(2); await h.select(1);
  h.network(() => result('合成当前结果')); await h.run('run()'); pending.resolve(result('合成迟到结果')); await old;
  assert.match(h.element('results').textContent, /合成当前结果/); assert.doesNotMatch(h.element('results').textContent, /合成迟到结果/);
});

test('HTTP run failure preserves drafts and supports an explicit retry', async () => {
  const h = harness(); await h.ready(); await h.algorithms(['cycles']); await h.edit('cycDays', '14');
  h.network(() => response({ detail: '合成运行拒绝' }, 422)); await h.run('run()');
  assert.match(feedback(h), /合成运行拒绝/); assert.equal(h.element('cycDays').value, '14'); assert.equal(h.element('btnRun').disabled, false); assert.equal(h.element('btnReport').disabled, true);
  h.network(() => result('合成重试成功')); await h.run('run()'); assert.match(h.element('results').textContent, /合成重试成功/);
});

test('missing selected algorithm output is an invalid response rather than an empty finding', async () => {
  const h = harness(); await h.ready(); await h.algorithms(['cycles']); h.network(() => ({ case_id: 1, algorithms: ['cycles'], source_snapshot: { flows: 3, accounts: 2 } }));
  await h.run('run()'); assert.equal(h.element('btnReport').disabled, true); assert.equal(h.element('btnRun').disabled, false);
  assert.equal(h.element('results').textContent.includes('未发现资金闭环'), false); assert.notEqual(h.element('analysisError').textContent.trim(), '');
});

test('result scope remains bound to this response snapshot after a fresher stats read', async () => {
  const h = harness(); await h.ready(); const value = result(); value.source_snapshot = { flows: 111, accounts: 222 }; await finishedRun(h, value);
  h.network(() => ({ flows: 777, accounts: 888 })); await h.run('showStats()');
  assert.match(h.element('resultScope').textContent, /111/); assert.match(h.element('resultScope').textContent, /222/);
  assert.doesNotMatch(h.element('resultScope').textContent, /777|888/); assert.match(h.element('caseStats').textContent, /777/);
});

test('editing draft parameters marks pending changes without mutating the completed run', async () => {
  const h = harness(); await h.ready(); await finishedRun(h);
  const frozen = h.run('JSON.stringify(lastRun.params)'); await h.edit('cycDays', '30'); await h.edit('subjects', 'SYN-A，SYN-B');
  assert.notEqual(h.element('draftStatus').textContent.trim(), ''); assert.equal(h.run('JSON.stringify(lastRun.params)'), frozen);
  assert.match(h.element('results').textContent, /合成环路/); assert.equal(h.element('btnReport').disabled, false);
});

test('all four algorithm result categories retain their own fields and count', async () => {
  const h = harness(); await h.ready(); await h.edit('pSrc', 'SYN-A'); await h.edit('pDst', 'SYN-B');
  await finishedRun(h, emptyResult(['cycles', 'fast_flows', 'outliers', 'paths']));
  const tabs = h.element('resultTabs').querySelectorAll('[role="tab"]'); assert.equal(tabs.length, 4);
  for (const [name, expected] of [['cycles', /环路/], ['fast_flows', /快进快出/], ['outliers', /离群/], ['paths', /可达路径/]]) {
    await h.element('resultTab-' + name).click(); assert.match(h.element('results').textContent, expected);
  }
  for (const name of ['cycles', 'fast_flows', 'outliers', 'paths']) assert.equal(h.document.querySelector(`[data-alg-count="${name}"]`).textContent.trim(), '0');
});

test('report recomputation requires a cancelable explicit confirmation', async () => {
  const h = harness(); await h.ready(); await finishedRun(h);
  const reporting = h.element('btnReport').click(); await flush(); assert.equal(h.element('analysisConfirm').open, true);
  assert.match(h.element('confirmMessage').textContent, /重新|重算/); assert.equal(posts(h, '/api/analysis/report').length, 0);
  await h.cancel(); await reporting; assert.equal(posts(h, '/api/analysis/report').length, 0); assert.equal(h.element('btnReport').disabled, false);
});

test('confirmed report uses frozen parameters and prevents duplicate confirmation and POSTs', async () => {
  const h = harness(); await h.ready(); await finishedRun(h); const frozen = paramsOf(posts(h)[0]); await h.edit('cycDays', '31');
  const pending = deferred(); h.network(() => pending.promise);
  const first = h.element('btnReport').click(); const second = h.element('btnReport').click(); await flush(); await h.accept();
  const third = h.element('btnReport').click(); await flush(); assert.equal(posts(h, '/api/analysis/report').length, 1);
  assert.deepEqual(paramsOf(posts(h, '/api/analysis/report')[0]), frozen);
  pending.resolve(response({ synthetic: true })); await Promise.all([first, second, third]);
  assert.equal(h.downloads.length, 1); assert.equal(h.element('cycDays').value, '31'); assert.equal(h.element('btnReport').disabled, false);
  for (const timer of [...h.timers]) timer.callback(); assert.deepEqual(h.revokedUrls, h.objectUrls.map(item => item.url));
});

test('a report confirmation from an old A binding cannot submit after A to B to A', async () => {
  const h = harness(); await h.ready(); await finishedRun(h); const reporting = h.element('btnReport').click(); await flush();
  h.run('selectAnalysisCase(2); selectAnalysisCase(1)');
  if (h.element('analysisConfirm').open) await h.accept(); await reporting;
  assert.equal(posts(h, '/api/analysis/report').length, 0); assert.equal(h.downloads.length, 0);
});

test('report HTTP failure exposes the error and leaves an explicit retry available', async () => {
  const h = harness(); await h.ready(); await finishedRun(h); h.network(() => response('合成报告失败', 503));
  const reporting = h.element('btnReport').click(); await flush(); await h.accept(); await reporting;
  assert.match(feedback(h), /合成报告失败/); assert.equal(h.element('btnReport').disabled, false); assert.equal(h.downloads.length, 0);
});

test('a delayed report body cannot download into a newer case binding', async () => {
  const h = harness(); await h.ready(); await finishedRun(h); const body = deferred();
  h.network(() => ({ ...response({}), blob: () => body.promise })); const reporting = h.element('btnReport').click(); await flush(); await h.accept();
  h.network(() => ({ flows: 8, accounts: 4 })); await h.select(2); body.resolve({ synthetic: true }); await reporting;
  assert.equal(h.downloads.length, 0); assert.equal(h.objectUrls.length, 0); assert.equal(h.element('caseSel').value, '2');
});

test('detail tables have six columns and retain exact amounts, currencies, null balances and escaped names', async () => {
  const h = harness(); await h.ready(); const target = h.document.createElement('div');
  target.innerHTML = h.run(`detailTable([
    {trade_time:'2026-01-01T12:00:00',payer_name:'<SYN-A>',payer_account:'00000000000000000001',payee_name:'SYN-B',payee_account:'90071992547409931234',amount:'9999999999999999.99',currency:'CNY',balance:null,summary:'<script>SYN</script>'},
    {trade_time:'2026-01-02T12:00:00',payer_account:'SYN-A',payee_account:'SYN-B',amount:'0.01',currency:'USD',balance:'10000000000000000.01',summary:'合成'}])`);
  assert.equal(target.querySelectorAll('th').length, 6); assert.equal(target.querySelectorAll('tr').slice(1).every(row => row.querySelectorAll('td').length === 6), true);
  assert.match(target.textContent, /9,999,999,999,999,999\.99/); assert.match(target.textContent, /10,000,000,000,000,000\.01/);
  assert.match(target.textContent, /CNY|人民币|¥/); assert.match(target.textContent, /USD|美元|\$/); assert.match(target.textContent, /—/);
  assert.match(target.textContent, /<SYN-A>/); assert.match(target.textContent, /00000000000000000001/); assert.equal(target.querySelector('script'), null);
});

test('detail expansion requests the bounded 500 rows once and its second click collapses', async () => {
  const h = harness(); await h.ready(); await finishedRun(h); const pending = deferred(); h.network(() => pending.promise);
  const btn = h.element('results').querySelector('.btn-detail'); const first = btn.click(); const second = btn.click(); await flush();
  const requests = h.requests.filter(request => request.path.startsWith('/api/flows?')); assert.equal(requests.length, 1);
  const url = new URL(requests[0].path, 'http://synthetic.invalid'); assert.equal(url.searchParams.get('size'), '500'); assert.equal(url.searchParams.get('case_id'), '1'); assert.equal(url.searchParams.get('ids'), '17,18');
  pending.resolve({ rows: [], total: 501 }); await Promise.all([first, second]); await flush();
  assert.match(h.element('results').querySelector('.detail-row').textContent, /0 \/ 501/);
  await btn.click(); assert.equal(!!h.element('results').querySelector('.detail-row'), false);
});

test('late detail rows cannot attach after a new run replaces the original binding', async () => {
  const h = harness(); await h.ready(); await finishedRun(h); const pending = deferred(); h.network(() => pending.promise);
  const button = h.element('results').querySelector('.btn-detail'); const detail = button.click(); await flush();
  h.network(() => result('合成新结果')); await h.run('run()'); pending.resolve({ rows: [{ summary: '合成迟到明细' }], total: 1 }); await detail;
  assert.equal(h.element('results').querySelector('.detail-row'), null); assert.doesNotMatch(h.element('results').textContent, /合成迟到明细/);
});

test('detail failure can retry without changing the completed result binding', async () => {
  const h = harness(); await h.ready(); await finishedRun(h); const button = h.element('results').querySelector('.btn-detail');
  h.network(() => response({ detail: '合成明细失败' }, 503)); await button.click(); await flush();
  assert.equal(button.disabled, false); assert.match(h.element('results').textContent, /合成明细失败/);
  h.network(() => ({ rows: [], total: 0 })); await button.click(); await flush(); assert.ok(h.element('results').querySelector('.detail-row'));
});

test('a zero-flow case remains runnable and its selected algorithms present zero findings', async () => {
  const h = harness({ initialNetwork: path => path === '/api/cases' ? cases : { flows: 0, accounts: 0 } }); await h.ready();
  assert.equal(h.element('btnRun').disabled, false); await finishedRun(h, emptyResult(['cycles']));
  assert.equal(posts(h).length, 1); assert.equal(h.document.querySelector('[data-alg-count="cycles"]').textContent, '0');
  assert.equal(h.element('btnReport').disabled, false);
});

test('no selected algorithm is a visible validation error without a POST', async () => {
  const h = harness(); await h.ready(); await h.algorithms([]); await h.run('run()');
  assert.equal(posts(h).length, 0); assert.equal(h.element('analysisError').hidden, false);
  assert.match(h.element('analysisError').textContent, /算法/);
});

test('wrong-case analysis output cannot become a downloadable completed run', async () => {
  const h = harness(); await h.ready(); const wrong = result(); wrong.case_id = 2; await finishedRun(h, wrong);
  assert.equal(h.element('btnReport').disabled, true); assert.equal(h.element('analysisError').hidden, false);
  assert.equal(h.run('lastRun'), null);
});

test('dirty case switching can be canceled and confirmed clearing account-specific inputs', async () => {
  const h = harness(); await h.ready(); await finishedRun(h); await h.edit('subjects', 'SYN-OLD'); await h.edit('pSrc', '00001'); await h.edit('pDst', '00002');
  const canceled = h.select(2); await flush(); assert.equal(h.element('caseSel').value, '1'); await h.cancel(); await canceled;
  assert.equal(h.element('caseSel').value, '1'); assert.equal(h.element('subjects').value, 'SYN-OLD'); assert.match(h.element('results').textContent, /合成环路/);
  h.network(() => ({ flows: 0, accounts: 0 })); const accepted = h.select(2); await flush(); await h.accept(); await accepted;
  assert.equal(h.element('caseSel').value, '2'); for (const id of ['subjects', 'pSrc', 'pDst']) assert.equal(h.element(id).value, '');
  assert.equal(h.element('draftStatus').textContent, ''); assert.equal(h.element('btnReport').disabled, true);
});

test('leaving a dirty analysis page requires confirmation and cancel keeps the draft', async () => {
  const h = harness(); await h.ready(); await h.edit('subjects', 'SYN-DRAFT');
  const canceled = h.element('backToGraph').click(); await flush(); await h.cancel(); await canceled;
  assert.equal(h.window.location.pathname, '/analysis'); assert.equal(h.element('subjects').value, 'SYN-DRAFT');
  const accepted = h.element('backToGraph').click(); await flush(); await h.accept(); await accepted;
  assert.equal(h.window.location.pathname, '/'); assert.equal(new URLSearchParams(h.window.location.search).get('case_id'), '1');
});

test('native dialog close cancels report generation and returns focus to its trigger', async () => {
  const h = harness(); await h.ready(); await finishedRun(h); const trigger = h.element('btnReport'); trigger.focus();
  const reporting = trigger.click(); await flush(); await h.element('analysisConfirm').close('cancel'); await reporting;
  assert.equal(posts(h, '/api/analysis/report').length, 0); assert.equal(h.document.activeElement === trigger, true);
});

test('nonempty algorithm fields preserve exact multi-currency values and explicit approximate outlier scores', async () => {
  const h = harness(); await h.ready(); await h.edit('pSrc', 'SYN-A'); await h.edit('pDst', 'SYN-B');
  const value = { ...result(), algorithms: ['cycles', 'fast_flows', 'outliers', 'paths'],
    fast_flows: { count: 1, window_minutes: 30, tolerance: .25, pairs: [{ account: 'SYN-PASS', in_flow: { id: '21', amount: '9999999999999999.99', currency: 'USD', from: 'SYN-IN', time: '2026-01-01T12:00:00' },
      out_flows: [{ id: '22', amount: '9999999999999999.98', currency: 'USD', to: 'SYN-OUT' }], out_total: '9999999999999999.98', diff: '0.01', currency: 'USD', max_delay_min: 2 }] },
    outliers: { count: 1, sigma: 2.5, outliers: [{ account: 'SYN-OUTLIER', name: '<合成>', explain: '合成总额', dim: 'total', value: '10000000000000000.01', currency: 'CNY', z: 3.125 }] },
    paths: { count: 2, src: 'SYN-A', dst: 'SYN-B', max_hops: 4, paths: ['CNY', 'USD'].map(currency => ({ total: '10000000000000000.01', currency, hops: 1, path: [{ from: 'SYN-A', to: 'SYN-B', amount: '10000000000000000.01', currency }] })) } };
  await finishedRun(h, value); await h.element('resultTab-paths').click();
  assert.match(h.element('results').textContent, /¥10,000,000,000,000,000\.01/); assert.match(h.element('results').textContent, /USD 10,000,000,000,000,000\.01/);
  assert.match(h.element('analysisScopeNote').textContent, /非去重本金/);
  await h.element('resultTab-fast_flows').click(); const pair = h.element('results').textContent;
  assert.match(pair, /9,999,999,999,999,999\.99/); assert.match(pair, /9,999,999,999,999,999\.98/); assert.match(pair, /USD 0\.01/); assert.match(pair, /25%/);
  await h.element('resultTab-outliers').click(); assert.match(h.element('results').textContent, /10,000,000,000,000,000\.01/); assert.match(h.element('results').textContent, /3\.125/); assert.match(h.element('results').textContent, /近似/);
  assert.match(h.element('results').textContent, /<合成>/); assert.equal(!!h.element('results').querySelector('script'), false);
  await h.element('resultTab-cycles').click(); assert.match(h.element('results').textContent, /合成环路/); assert.equal(h.element('analysisScopeNote').textContent, '');
});

test('real shared horizontal controls clamp scrolling and are disposed when result tables are replaced', async () => {
  const h = harness(); await h.ready(); await h.edit('pSrc', 'SYN-A'); await h.edit('pDst', 'SYN-B');
  const value = { ...result(), algorithms: ['cycles', 'paths'], paths: emptyResult(['paths']).paths }; await finishedRun(h, value);
  const shell = h.element('results').querySelector('.analysis-table-shell'), viewport = shell.querySelector('[data-table-viewport]'), range = shell.querySelector('[data-scroll-range]');
  viewport.scrollWidth = 1200; viewport.clientWidth = 320; await h.window.fire('resize').completion;
  assert.equal(range.max, '880'); await range.fire('keydown', { key: 'End' }).completion; assert.equal(viewport.scrollLeft, 880);
  assert.equal(shell.querySelector('[data-scroll-right]').disabled, true); range.value = '-40'; await range.fire('input').completion; assert.equal(viewport.scrollLeft, 0);
  const before = h.window.listeners.get('resize').length; assert.ok(before > 0);
  await h.element('resultTab-paths').click(); assert.equal(h.window.listeners.get('resize').length, 0);
  await range.fire('keydown', { key: 'End' }).completion; assert.equal(viewport.scrollLeft, 0, 'detached horizontal controls cannot move their retired viewport');
});

test('switching result tabs A to B to A rejects a late detail response from the first A table', async () => {
  const h = harness(); await h.ready(); await finishedRun(h, { ...result(), algorithms: ['cycles', 'fast_flows'], fast_flows: emptyResult(['fast_flows']).fast_flows });
  const pending = deferred(); h.network(() => pending.promise); await h.element('results').querySelector('.btn-detail').click(); await flush();
  await h.element('resultTab-fast_flows').click(); await h.element('resultTab-cycles').click();
  pending.resolve({ rows: [{ summary: '合成旧标签明细' }], total: 1 }); await flush();
  assert.equal(!!h.element('results').querySelector('.detail-row'), false); assert.doesNotMatch(h.element('results').textContent, /合成旧标签明细/);
});

test('currency remains explicit for count-based outlier rows from different currency populations', async () => {
  const h = harness(); await h.ready(); await finishedRun(h, { ...emptyResult(['outliers']), outliers: { count: 2, sigma: 2.5, outliers: [
    { account: 'SYN-A', dim: 'cnt', value: 7, currency: 'CNY', explain: '合成交易笔数异常', z: 3 },
    { account: 'SYN-A', dim: 'cnt', value: 7, currency: 'USD', explain: '合成交易笔数异常', z: 3 },
  ] } });
  const table = h.element('results').querySelector('table'); assert.ok(table);
  assert.ok(table.querySelectorAll('th').some(cell => cell.textContent.includes('币种')));
  const rows = table.querySelectorAll('tbody tr'); assert.equal(rows.length, 2);
  assert.match(rows[0].textContent, /CNY/); assert.match(rows[1].textContent, /USD/);
});

test('starting or failing a rerun clears the completed result snapshot scope', async () => {
  const h = harness(); await h.ready(); const value = result(); value.source_snapshot = { flows: 111, accounts: 222 }; await finishedRun(h, value);
  const pending = deferred(); h.network(() => pending.promise); const running = h.run('run()');
  const during = h.element('resultScope').textContent;
  pending.resolve(response({ detail: '合成重跑失败' }, 503)); await running;
  assert.doesNotMatch(during, /111|222|本次结果/); assert.doesNotMatch(h.element('resultScope').textContent, /111|222|本次结果/);
});

test('outlier night and round fields describe counts with units rather than invented ratios', async () => {
  const h = harness(); await h.ready(); await finishedRun(h, { ...emptyResult(['outliers']), outliers: { count: 3, sigma: 2.5, outliers: [
    { account: 'SYN-A', dim: 'night', value: 5, currency: 'CNY', explain: '深夜占比异常', z: 3 },
    { account: 'SYN-B', dim: 'round', value: 8, currency: 'CNY', explain: '整额占比异常', z: 3 },
    { account: 'SYN-C', dim: 'counter_size', value: 9, currency: 'USD', explain: '合成对手数异常', z: 3 },
  ] } });
  const text = h.element('results').textContent;
  assert.match(text, /深夜交易笔数异常多/); assert.match(text, /整额交易笔数异常多/);
  assert.match(text, /5\s*笔/); assert.match(text, /8\s*笔/); assert.match(text, /9\s*个/); assert.doesNotMatch(text, /占比/);
});

test('same-case stats reads reject an older response without needing a case switch', async () => {
  const h = harness(); await h.ready(); const older = deferred(); h.network(() => older.promise); const first = h.run('showStats()');
  h.network(() => ({ flows: 37, accounts: 11 })); await h.run('showStats()'); older.resolve({ flows: 999, accounts: 999 }); await first;
  assert.match(h.element('caseStats').textContent, /37/); assert.doesNotMatch(h.element('caseStats').textContent, /999/);
});

test('detail flow identifiers remain distinct long strings in the bounded query', async () => {
  const h = harness(); await h.ready(); const value = result(); value.cycles.cycles[0].edges = [{ id: '0009007199254740993' }, { id: '9007199254740993' }]; await finishedRun(h, value);
  h.network(() => ({ rows: [], total: 2 })); await h.element('results').querySelector('.btn-detail').click(); await flush();
  const request = h.requests.find(item => item.path.startsWith('/api/flows?'));
  const query = new URL(request.path, 'http://synthetic.invalid').searchParams;
  assert.equal(query.get('ids'), '0009007199254740993,9007199254740993'); assert.equal(query.get('size'), '500');
});

test('late report failure cannot overwrite the newer case completed state', async () => {
  const h = harness(); await h.ready(); await finishedRun(h); const pending = deferred(); h.network(() => pending.promise);
  const reporting = h.element('btnReport').click(); await flush(); await h.accept();
  h.network(() => ({ flows: 8, accounts: 4 })); await h.select(2);
  h.network(() => ({ ...result('合成乙案完成'), case_id: 2 })); await h.run('run()');
  pending.resolve(response({ detail: '合成甲案迟到失败' }, 503)); await reporting;
  assert.match(h.element('results').textContent, /合成乙案完成/); assert.doesNotMatch(h.element('reportStatus').textContent, /合成甲案迟到失败/);
  assert.equal(h.element('btnReport').disabled, false);
});

test('keyboard result navigation skips unrun algorithms and keeps selection and run parameters intact', async () => {
  const h = harness(); await h.ready(); await finishedRun(h, emptyResult(['cycles', 'outliers'])); const before = h.run('JSON.stringify(lastRun.params)');
  await h.element('resultTab-cycles').fire('keydown', { key: 'ArrowRight' }).completion;
  assert.equal(h.element('resultTab-outliers').getAttribute('aria-selected'), 'true'); assert.equal(h.document.activeElement === h.element('resultTab-outliers'), true);
  await h.element('resultTab-outliers').fire('keydown', { key: 'Home' }).completion;
  assert.equal(h.element('resultTab-cycles').getAttribute('aria-selected'), 'true');
  assert.equal(h.run('JSON.stringify(lastRun.params)'), before); assert.equal(posts(h).length, 1);
});

test('busy tabs distinguish selected pending results and do not retain pending labels after a case switch or failure', async () => {
  const h = harness(); await h.ready(); await h.algorithms(['cycles', 'fast_flows']); const pending = deferred(); h.network(() => pending.promise);
  const running = h.run('run()'); const busy = Object.fromEntries(h.element('resultTabs').querySelectorAll('[role="tab"]').map(tab => [tab.dataset.algorithm, tab.textContent]));
  h.network(() => ({ flows: 8, accounts: 4 })); await h.select(2); const ready = h.element('resultTabs').textContent;
  pending.resolve(emptyResult(['cycles', 'fast_flows'])); await running;
  h.network(() => response({ detail: '合成运行失败' }, 503)); await h.run('run()'); const failed = h.element('resultTabs').textContent;
  for (const name of ['cycles', 'fast_flows']) assert.match(busy[name], /待结果/);
  for (const name of ['outliers', 'paths']) { assert.match(busy[name], /未运行/); assert.doesNotMatch(busy[name], /待结果/); }
  assert.doesNotMatch(ready, /待结果/); assert.doesNotMatch(failed, /待结果/);
});

test('fractional fast-flow tolerance preserves its submitted percent in the completed category heading', async () => {
  const h = harness(); await h.ready(); await h.algorithms(['fast_flows']);
  for (const [entered, ratio] of [['12.5', .125], ['0.29', .0029]]) {
    await h.edit('ffTol', entered); const value = emptyResult(['fast_flows']); value.fast_flows.tolerance = ratio; h.network(() => value); await h.run('run()');
    assert.equal(paramsOf(posts(h).at(-1)).ff_tolerance, ratio);
    await h.element('resultTab-fast_flows').click(); assert.ok(h.element('results').querySelector('h2').textContent.includes('±' + entered + '%'));
  }
});

const cycleResult = () => ({
  case_id: 1, algorithms: ['cycles'], subjects: [], source_snapshot: { flows: 3, accounts: 3 },
  cycles: { count: 1, window_days: 7, cycles: [{
    accounts: ['SYN-A', 'SYN-B', 'SYN-C'],
    accounts_names: ['合成甲', '合成乙', '合成丙'],
    loop_names: ['合成甲', '合成乙', '合成丙', '合成甲'],
    len: 3, total: '300.00', currency: 'CNY',
    start_time: '2026-01-01 10:00:00', end_time: '2026-01-01 12:00:00', span_days: 0.1,
    first_outflow: '100.00', last_inflow: '98.76', return_ratio: 0.9876,
    edges: [
      { id: 17, from: 'SYN-A', to: 'SYN-B', amount: '100.00', time: '2026-01-01 10:00:00', currency: 'CNY' },
      { id: 18, from: 'SYN-B', to: 'SYN-C', amount: '101.24', time: '2026-01-01 11:00:00', currency: 'CNY' },
      { id: 19, from: 'SYN-C', to: 'SYN-A', amount: '98.76', time: '2026-01-01 12:00:00', currency: 'CNY' },
    ],
  }] },
});

test('a detected cycle opens on its own canvas instead of appending to the default one', async () => {
  const h = harness(); await h.ready(); await finishedRun(h, cycleResult());
  const button = h.element('results').querySelector('.btn-track');
  assert.ok(button, '每条闭环都应有一键上图入口');
  assert.match(button.dataset.canvas, /环流 /);
  h.network(path => {
    if (path === '/api/canvases/from-flows') return response({ ok: true, canvas_id: 41, name: '环流', added: 3 });
    return response({});
  });
  await button.click(); await flush();
  const save = h.requests.find(item => item.path === '/api/canvases/from-flows'); assert.ok(save, feedback(h));
  const saved = JSON.parse(save.options.body);
  assert.equal(saved.case_id, '1');
  assert.match(saved.name, /^环流 /);
  assert.deepEqual(saved.snapshot.nodes, []);
  assert.deepEqual(saved.flow_ids, ['17', '18', '19']);
  assert.equal(h.requests.some(item => item.path === '/api/track' || item.path === '/api/canvases/save'), false);
  const bodies = h.requests.filter(item => item.options?.body).map(item => JSON.parse(item.options.body));
  assert.equal(bodies.some(body => body.canvas_id === 0), false, '不得写默认画布');
  assert.equal(button.textContent, '已上图 3 笔');
  const link = h.element('results').querySelector('.btn-graph');
  assert.equal(link?.getAttribute('href'), '/?case_id=1&canvas_id=41&focus_edge=17');
  assert.equal(link?.getAttribute('target'), '_blank', '画布必须新窗口打开，保住分析结果');
  assert.equal(link?.getAttribute('rel'), 'noopener');
});

test('each cycle click asks the atomic service to recheck its dedicated canvas', async () => {
  const h = harness(); await h.ready(); await finishedRun(h, cycleResult());
  const button = h.element('results').querySelector('.btn-track');
  h.network(path => {
    if (path === '/api/canvases/from-flows') return response({ ok: true, canvas_id: 7, added: 0 });
    return response({});
  });
  await button.click(); await flush();
  await button.click(); await flush();
  assert.equal(h.requests.filter(item => item.path === '/api/canvases/from-flows').length, 2);
  assert.equal(h.requests.some(item => item.path.startsWith('/api/canvases?')), false, 'display names are not canvas identity');
  assert.equal(button.textContent, '已在本画布');
});

test('an atomic cycle request failure restores its button and retries without compensating deletion', async () => {
  const h = harness(); await h.ready(); await finishedRun(h, cycleResult());
  const button = h.element('results').querySelector('.btn-track');
  h.network(path => {
    if (path === '/api/canvases/from-flows') return response({ detail: '合成上图失败' }, 503);
    return response({ ok: true });
  });
  await button.click(); await flush();
  assert.equal(button.textContent, '一键上图');
  assert.equal(button.disabled, false);
  assert.match(h.element('analysisError').textContent, /合成上图失败/);
  assert.equal(h.element('results').querySelector('.btn-graph'), null);
  assert.equal(h.requests.some(item => item.options?.method === 'DELETE'), false);
  h.network(() => response({ ok: true, canvas_id: 42, added: 0 }));
  await button.click(); await flush();
  assert.equal(button.textContent, '已在本画布');
  assert.equal(h.element('results').querySelector('.btn-graph')?.getAttribute('href'), '/?case_id=1&canvas_id=42&focus_edge=17');
});

test('cycle writes stay on their original case and a late A-B-A reply cannot change current feedback', async () => {
  const h = harness(); await h.ready(); await finishedRun(h, cycleResult());
  const button = h.element('results').querySelector('.btn-track'), pending = deferred();
  h.network(path => path.startsWith('/api/stats?') ? response({flows: 3, accounts: 3}) : pending.promise);
  await button.click(); await flush();
  await button.click(); await flush();
  await h.select(2); await h.select(1);
  h.run("analysisError('当前案件的新提示')");
  pending.resolve(response({ ok: true, canvas_id: 41, added: 3 })); await flush();
  const writes = h.requests.filter(item => item.options?.method === 'POST' && !item.path.startsWith('/api/analysis/'));
  assert.equal(writes.length, 1, 'one in-flight click issues exactly one complete mutation');
  assert.equal(JSON.parse(writes[0].options.body).case_id, '1');
  assert.equal(h.element('analysisError').textContent, '当前案件的新提示');
  assert.equal(h.element('results').querySelector('.btn-graph'), null);
});

test('changing result categories revokes a late cycle write failure', async () => {
  const h = harness(); await h.ready();
  const value = {...cycleResult(), algorithms: ['cycles', 'outliers'], outliers: {count: 0, outliers: [], sigma: 2.5}};
  await finishedRun(h, value);
  const button = h.element('results').querySelector('.btn-track'), pending = deferred();
  h.network(() => pending.promise); await button.click(); await flush();
  await h.element('resultTab-outliers').click();
  pending.resolve(response({detail: '旧结果失败'}, 503)); await flush();
  assert.equal(h.element('analysisError').textContent, '');
  assert.equal(h.requests.some(item => item.options?.method === 'DELETE'), false);
});

test('path ordering is disabled during its analysis request and when paths are unselected', async () => {
  const h = harness(); await h.ready();
  assert.equal(h.element('pOrder').disabled, true);
  await h.algorithms(['paths']); await h.edit('pSrc', '合成甲'); await h.edit('pDst', '合成乙');
  assert.equal(h.element('pOrder').disabled, false);
  const pending = deferred(); h.network(() => pending.promise); const running = h.run('run()');
  assert.equal(h.element('pOrder').disabled, true);
  pending.resolve(emptyResult(['paths'])); await running;
  assert.equal(h.element('pOrder').disabled, false);
});

test('bounded cycle and path searches disclose limits even when they return no findings', async () => {
  const h = harness(); await h.ready();
  for (const algorithm of ['cycles', 'paths']) {
    const value = emptyResult([algorithm]); value[algorithm].truncated = true;
    h.run(`render(${JSON.stringify(value)},${JSON.stringify([algorithm])})`);
    assert.match(h.element('results').textContent, /搜索或显示已达上限/);
    assert.match(h.element('results').textContent, /有界搜索未返回/);
    assert.match(h.element('results').textContent, /未显示结果不代表不存在/);
  }
});

test('cycle details span their seven-column result table', async () => {
  const h = harness(); await h.ready(); await finishedRun(h, cycleResult());
  h.network(() => response({rows: [], total: 0}));
  await h.element('results').querySelector('.btn-detail').click(); await flush();
  assert.equal(h.element('results').querySelector('.detail-row td')?.getAttribute('colspan'), '7');
});

test('analysis tasks use unknown-duration progress and one request identity through success or failure', async () => {
  const h = harness(); await h.ready(); await h.algorithms(['cycles']);
  const pending = deferred(); h.network(() => pending.promise);
  const running = h.run('run()'), started = h.feedbackEvents.at(-1);
  assert.equal(started.status, 'running'); assert.equal(started.operation, 'analysis');
  assert.equal('progress' in started, false); assert.equal(started.scope.caseId, '1');
  pending.resolve(result()); await running;
  assert.deepEqual(h.feedbackEvents.filter(event => event.id === started.id).map(event => event.status), ['running', 'success']);
  h.network(() => response({ detail: '合成分析失败' }, 503)); await h.run('run()');
  const failed = h.feedbackEvents.at(-1);
  assert.equal(failed.status, 'error'); assert.notEqual(failed.id, started.id); assert.match(failed.detail, /合成分析失败/);
  assert.equal(h.feedbackNotices.length, 0, 'the visible result and task mirror do not add a second toast');
});

test('case rebinding retires running feedback without claiming cancellation or publishing a late terminal result', async () => {
  const h = harness(); await h.ready(); await h.algorithms(['cycles']);
  const pending = deferred(); h.network(path => path === '/api/analysis/run' ? pending.promise : { flows: 0, accounts: 0 });
  const running = h.run('run()'), id = h.feedbackEvents.at(-1).id;
  await h.select(2); await h.select(1);
  const retired = h.feedbackEvents.filter(event => event.id === id);
  assert.deepEqual(retired.map(event => event.status), ['running', 'unknown']);
  assert.match(retired.at(-1).detail, /服务端请求可能仍在执行/);
  pending.resolve(result('LATE-RESULT')); await running;
  assert.equal(h.feedbackEvents.filter(event => event.id === id).length, 2);
  assert.equal(h.feedbackBindings.at(-1).caseId, '1');
  assert.ok(h.feedbackBindings.at(-1).generation > retired[0].scope.generation);
});

test('report task completes only after the file download is initiated and retains failures', async () => {
  const h = harness(); await h.ready(); await finishedRun(h);
  const pending = deferred(); h.network(() => pending.promise);
  const reporting = h.run('generateAnalysisReport()'); await h.accept();
  const started = h.feedbackEvents.at(-1);
  assert.equal(started.operation, 'download'); assert.equal(started.status, 'running');
  assert.equal(h.downloads.length, 0); assert.equal('progress' in started, false);
  pending.resolve(response({})); await reporting;
  const completed = h.feedbackEvents.at(-1);
  assert.equal(completed.id, started.id); assert.equal(completed.status, 'success');
  assert.equal(h.downloads.length, 1); assert.match(completed.detail, /已发起/);
  h.network(() => response({ detail: '合成报告失败' }, 500));
  const retry = h.run('generateAnalysisReport()'); await h.accept(); await retry;
  assert.equal(h.feedbackEvents.at(-1).status, 'error');
  assert.notEqual(h.feedbackEvents.at(-1).id, started.id);
});
