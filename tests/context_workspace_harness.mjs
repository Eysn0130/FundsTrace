import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const app = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
const workflow = readFileSync(new URL('../static/workflow-ui.js', import.meta.url), 'utf8');
const money = readFileSync(new URL('../static/money.js', import.meta.url), 'utf8');
const section = (start, end) => {
  const offset = app.indexOf(start), finish = app.indexOf(end, offset);
  assert.ok(offset >= 0 && finish > offset, `missing public source section ${start}`);
  return app.slice(offset, finish);
};
const flush = async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); };
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const decode = value => value.replace(/&(amp|lt|gt|quot|#39);/g, (_match, key) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" }[key]));

// The DOM preserves real ancestry, detached-control identity and event order.
// Rendering, selection, recommendations and workflow orchestration are real.
function harness() {
  let document;
  const matches = (node, selector) => {
    if (!node || node.tagName === '#TEXT') return false;
    if (selector === ':focus') return document.activeElement === node;
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
    get name() { return this.attributes.get('name') || ''; }
    set name(value) { this.attributes.set('name', String(value)); }
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
        stack.at(-1).appendChild(child);
        if (!voids.has(child.tagName) && !token[0].endsWith('/>')) stack.push(child);
      }
    }
    setAttribute(name, value) {
      value = String(value);
      if (name === 'class') { this.className = value; return; }
      this.attributes.set(name, value);
      if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase())] = value;
      else if (['hidden', 'disabled', 'checked'].includes(name)) this[name] = true;
      else if (name === 'tabindex') this.tabIndex = Number(value);
      else if (name === 'value') this.value = value;
      else if (name === 'style') for (const declaration of value.split(';')) { const [key, val] = declaration.split(':'); if (val) this.style[key.trim()] = val.trim(); }
    }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    removeAttribute(name) { this.attributes.delete(name); if (['hidden', 'disabled'].includes(name)) this[name] = false; }
    appendChild(child) { child.remove(); child.parentElement = this; this.children.push(child); return child; }
    append(...children) {
      for (const child of children) {
        if (typeof child !== 'string') { this.appendChild(child); continue; }
        const text = new Element('#text');
        text.textContent = child;
        this.appendChild(text);
      }
    }
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
      const event = { type, target: this, key: '', relatedTarget: null, bubbles: !['scroll', 'resize'].includes(type), defaultPrevented: false, stopped: false,
        preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, ...options };
      const path = []; for (let node = this; node; node = node.parentElement) path.push(node);
      const results = [], invoke = (node, capture) => {
        event.currentTarget = node;
        for (const item of node.listeners.get(type) || []) if (item.capture === capture) results.push(item.handler(event));
        if (!capture) results.push(node['on' + type]?.(event));
      };
      for (const node of [...path].reverse()) { invoke(node, true); if (event.stopped) break; }
      if (!event.stopped) for (const node of event.bubbles ? path : [this]) { invoke(node, false); if (event.stopped) break; }
      event.completion = Promise.all(results); return event;
    }
    click() { return this.disabled ? Promise.resolve() : this.fire('click').completion; }
    dispatchEvent(event) { return !this.fire(event.type, event).defaultPrevented; }
    focus() {
      if (!this.isConnected || this.disabled) return;
      const previous = document.activeElement; document.activeElement = this;
      if (previous !== this) previous?.fire('focusout', { relatedTarget: this });
      this.fire('focusin', { relatedTarget: previous });
    }
    getBoundingClientRect() { return { left: 20, right: 180, top: 20, bottom: 60, width: 160, height: 40 }; }
  }
  document = { root: new Element('#document'), body: new Element('body'), documentElement: new Element('html'), activeElement: null,
    createElement: tag => new Element(tag), getElementById: id => document.root.querySelector('#' + id),
    querySelector: selector => document.root.querySelector(selector), querySelectorAll: selector => document.root.querySelectorAll(selector),
    addEventListener: (...args) => document.root.addEventListener(...args), removeEventListener: (...args) => document.root.removeEventListener(...args) };
  document.root.appendChild(document.body); document.activeElement = document.body;
  const add = (parent, id, tag = 'div') => { const node = new Element(tag); node.id = id; parent.appendChild(node); return node; };
  const modal = add(document.body, 'ctxModal'); modal.style.display = 'none';
  for (const id of ['ctxCaseName', 'ctxAnchor', 'ctxPos', 'ctxTotal', 'ctxSelected', 'ctxTrackedCount', 'ctxTrackStatus', 'ctxSuggest', 'ctxBody', 'ctxDetail', 'ctxDetailContent']) add(modal, id);
  add(modal, 'ctxDetailBack', 'button'); add(modal, 'ctxTraceThis', 'button');
  const tabs = add(modal, 'ctxTabs');
  for (const mode of ['all', 'source', 'target']) { const tab = add(tabs, `ctxMode-${mode}`, 'button'); tab.className = 'stab'; tab.dataset.mode = mode; }
  add(modal, 'ctxX', 'input').value = '5'; add(modal, 'ctxStart', 'input'); add(modal, 'ctxEnd', 'input');
  const track = add(modal, 'ctxTrack', 'button'); add(track, 'ctxTrackLabel'); add(modal, 'ctxClose', 'button'); add(modal, 'ctxCancel', 'button');
  const viewport = add(modal, 'ctxTableScroll'); const table = add(viewport, 'ctxTable', 'table'); add(table, 'ctxTbody', 'tbody');
  for (const id of ['ctxScrollLeft', 'ctxScrollRight', 'ctxHorizontalPosition']) add(modal, id, id === 'ctxHorizontalPosition' ? 'input' : 'button');
  for (const id of ['btnCloseDrawer', 'drawer', 'drawerBody', 'btnImport', 'btnImport2', 'importModal', 'impPbar', 'impClose', 'impCancel', 'impQueue', 'impQueueStop',
    'impFile', 'impRe', 'impCommit', 'impChangeFile', 'impMapping', 'impPreviewScroll', 'impHorizontalPosition', 'impScrollLeft', 'impScrollRight', 'impPreviewTable',
    'flowTbody', 'canvasSel', 'flowCkAll', 'caseSelect', 'analysisLink', 'batchList']) add(document.body, id);
  const requests = [], trackCalls = [], reviewWrites = [], notices = [];
  let network = async () => { throw new Error('Unexpected request'); }, tracker = async () => true;
  const state = { caseId: 1, caseGeneration: 1, canvasId: 0, canvasGeneration: 1, ctx: null, tracked: new Set([12]),
    lastGraph: { nodes: [], edges: [], analysis_revision: 'a'.repeat(32), membership_digest: 'b'.repeat(64) }, graphView: { revision: 0 }, search: { page: 1 }, cases: [{ id: 1, name: '合成甲案' }, { id: 2, name: '合成乙案' }] };
  const window = new Element('#window'); window.appendChild(document.root);
  window.location = { href: 'http://synthetic.invalid/?case_id=1' }; window.history = { replaceState(_state, _title, url) { window.location.href = String(url); } };
  const fetch = async (path, options) => {
    requests.push({ path, options });
    const value = options?.method === 'DELETE' ? { ok: true } : await network(path, options);
    return { ok: true, status: 200, json: async () => value, text: async () => JSON.stringify(value) };
  };
  const context = vm.createContext({ state, document, window, exportWorkspace: null, caseWorkspace: null, URL, Set, Map, Event, AbortController, fetch,
    $: selector => document.querySelector(selector), $$: selector => document.querySelectorAll(selector),
    api: async (path, options) => (await fetch(path, options)).json(),
    jsonOpts: body => ({ headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) }),
    trackFlows: (ids, guard) => { trackCalls.push([...ids]); reviewWrites.push(guard); return tracker(ids); },
    toast: (message, type) => notices.push({ message, type }),
    preferenceStorage: { setItem() {} }, cancelGraphLayout() {}, resetGraphView() {}, newGraphPresentation: () => ({}),
    revokePhotoCrop() {}, resetNodeWorkspace() {}, syncEdgeTypeControl() {}, closeDrawer() {}, syncFlowCkCount() {}, updateHeader() {}, resetBatchView() {},
    showNodeDrawer: async () => {}, showEdgeDrawer: async () => {}, loadGraph: async () => {}, renderGraph() {},
    bindTableHorizontalScroll(parts) { for (const node of Object.values(parts)) assert.ok(node); return { sync() {} }; },
    setTimeout: callback => { callback(); return 0; }, clearTimeout() {}, requestAnimationFrame: callback => { callback(); return 0; },
  });
  vm.runInContext(money, context);
  vm.runInContext(readFileSync(new URL('../static/principal-analysis.js', import.meta.url), 'utf8'), context);
  window.FundsFacts = context.FundsFacts;
  vm.runInContext(section('function esc(', 'function toast('), context);
  vm.runInContext(section('let pendingCanvasId = 0;', 'async function loadCases()'), context);
  vm.runInContext(section('function caseContext()', '/* ---------------- 初始化'), context);
  vm.runInContext(section('let contextTableScroll = null;', 'async function loadBatches()'), context);
  vm.runInContext('bindContextUI()', context);
  vm.runInContext(workflow, context);
  window.fire('DOMContentLoaded');
  return { state, document, requests, trackCalls, reviewWrites, notices, element: id => document.getElementById(id),
    network(handler) { network = handler; }, track(handler) { tracker = handler; }, run(code) { return vm.runInContext(code, context); },
    open(id = 12) { return vm.runInContext(`openContext(${id})`, context); }, load() { return vm.runInContext('loadContext()', context); },
    close() { return document.getElementById('ctxClose').click(); }, selectCase(id) { return vm.runInContext(`selectCase(${id})`, context); },
    rows() { return document.querySelectorAll('#ctxTbody tr').filter(row => row.querySelectorAll('td').length === 14); },
    box(id) { return document.querySelector(`#ctxTbody .ctx-ck[data-id="${id}"]`); }, cards() { return document.querySelectorAll('#ctxSuggest .sg-item').filter(card => {
      for (let node = card; node; node = node.parentElement) if (node.hidden || node.style.display === 'none') return false;
      return true;
    }); },
    check(id, checked) { const box = this.box(id); assert.ok(box, `missing visible checkbox ${id}`); box.checked = checked; return box.fire('change').completion; },
    change(id, value) { const node = document.getElementById(id); node.value = value; return node.fire('change').completion; },
    mode(value) { return document.getElementById(`ctxMode-${value}`).click(); },
    submit() { return document.getElementById('ctxTrack').click(); },
  };
}

function result(account = 'SYN-CURRENT') {
  const flow = (id, rn, dir, book, extra = {}) => ({ id, rn, dir, book, trade_time: '2026-10-17 12:34:56',
    payer_name: '合成来款方', payer_account: 'SYN-PAYER', payee_name: '合成收款方', payee_account: 'SYN-PAYEE',
    self_account: book === 'self' ? (dir === 'in' ? 'SYN-PAYEE' : 'SYN-PAYER') : (dir === 'in' ? 'SYN-PAYER' : 'SYN-PAYEE'),
    amount: '9007199254740993.07', currency: 'CNY', balance: null, summary: '合成交易摘要',
    review_fields: { account: 'SYN-PAYEE', name: '合成收款方', direction: dir === 'in' ? '贷' : '借',
      book_direction: dir, book_direction_basis: 'source_marker' },
    review_roles: [], ...extra });
  const anchor = flow(12, 3, 'out', 'self', { context_relation: 'anchor', payee_name: account });
  return { account, anchor, total: 9, pos: 3, analysis_revision: 'b'.repeat(32), rows: [
    flow(11, 2, 'in', 'self', { trade_time: '2026-10-17 12:33:56', context_relation: 'before', review_roles: ['source'] }), anchor,
    flow(13, 4, 'out', 'counter', { balance: '-10000000000000.01', trade_time: '2026-10-17 12:35:56', context_relation: 'after', review_roles: ['target'] }),
    flow(14, 5, 'out', 'self', { trade_time: '2026-10-18 12:36:56', context_relation: 'after', review_roles: ['target'] })],
    review: { token: 'a'.repeat(32), pair: { status: 'unknown', candidate_ids: [] }, principal: { source: {
      status: 'unknown', assessment: [{ status: 'unknown', message: `合成来源证据-${account}` }],
      targets: [{ amount: '100.00', min: '0.00', max: '100.00', fifo: null }], currency: 'CNY' } } },
    suggestions: { sources: [{ ids: ['11'], account: 'SYN-SOURCE', name: '合成来源', score: 88, currency: 'CNY', evidence: {
      conditional_group_share: { min: '0.00', max: '0.40' }, basis: '12:33:56 的 1 条流水是锚点前的候选入账。' } }],
      targets: [{ ids: ['13'], account: 'SYN-TARGET', name: '合成去向', score: 62 }] } };
}
async function opened(h, data = result()) { h.network(async () => data); await h.open(); }

test('bounded unknown posting windows disclose omitted ties in the existing detail', async () => {
  const h = harness(), data = result();
  data.ordering = {status:'partial', reasons:['neighbor_boundary_order_unknown', 'posting_order_unknown']};
  await opened(h, data);
  assert.deepEqual(h.element('ctxSuggest').querySelectorAll('h3').map(heading => heading.textContent), ['来源', '去向']);
  assert.match(h.element('ctxPos').textContent, /边界同刻记录无法排序，未任意截取/);
  assert.match(h.element('ctxPos').textContent, /同刻记录先后未确定/);
});
const cells = row => row.querySelectorAll('td');
const picked = card => card.getAttribute('aria-pressed') === 'true' || card.classList.contains('picked');
const sourceCard = h => h.cards().find(card => (card.dataset.ids || '').split(',').includes('11'));
const cardJudgment = card => card.parentElement.querySelector('.sg-judgment').textContent;

test('context rows display proved directions and maintain original order, exact money and tracking', async () => {
  const h = harness(), data = result(); data.rows[0].summary = '<img src=x onerror="SYN"> & "合成"';
  Object.assign(data.rows[0].review_fields, { direction: null, book_direction_basis: 'source_own_endpoint' });
  data.rows[0].payer_name = '<b>合成来款方</b>'; await opened(h, data);
  const rows = h.rows(); assert.equal(rows.length, 4);
  for (const row of rows) assert.equal(cells(row).length, 14);
  assert.match(cells(rows[0])[4].textContent, /之前/); assert.match(cells(rows[1])[4].textContent, /本笔/);
  assert.match(cells(rows[2])[4].textContent, /之后/);
  assert.equal(cells(rows[0])[2].textContent, 'SYN-PAYEE');
  assert.equal(cells(rows[0])[5].textContent, '入账据收款方'); assert.equal(cells(rows[2])[5].textContent, '出账');
  assert.match(cells(rows[0])[6].textContent, /9,007,199,254,740,993\.07/);
  assert.equal(cells(rows[0])[7].textContent.trim(), '—'); assert.match(cells(rows[2])[7].textContent, /-10,000,000,000,000\.01/);
  assert.equal(cells(rows[0])[12].textContent, data.rows[0].summary); assert.equal(h.element('ctxTbody').innerHTML.includes('<img src=x'), false);
  assert.equal(h.element('ctxTbody').innerHTML.includes('<b>合成来款方</b>'), false);
  assert.equal(h.box(12), null); assert.match(rows[1].textContent, /已上图/); assert.equal(h.element('ctxTrackedCount').textContent, '已上图 1 条记录');
});

test('score click only highlights rows and shows evidence; checking manually controls the CTA', async () => {
  const h = harness(); await opened(h);
  assert.equal(h.element('ctxTrack').disabled, true); assert.equal(h.element('ctxSelected').textContent, '已选 0 条');
  const card = sourceCard(h); assert.ok(card); assert.equal(card.tagName, 'BUTTON');
  assert.match(card.querySelector('.sg-score').textContent, /88/);
  assert.equal(cardJudgment(card), '');
  assert.equal(card.querySelector('.sg-evidence').hidden, true);
  await card.click(); assert.equal(h.box(11).checked, false); assert.equal(h.box(13).checked, false);
  assert.equal(h.rows()[0].classList.contains('clue-row'), true);
  assert.equal(picked(card), true); assert.equal(h.element('ctxSelected').textContent, '已选 0 条'); assert.equal(h.element('ctxTrack').disabled, true);
  assert.equal(card.querySelector('.sg-evidence').hidden, false);
  assert.match(card.querySelector('.sg-evidence').textContent, /12:33:56/);
  await h.check(11, true); assert.equal(picked(card), true);
  await card.click(); assert.equal(h.box(11).checked, true); assert.equal(h.box(12), null);
  assert.equal(picked(card), false); assert.equal(h.element('ctxTrack').disabled, false);
  await h.check(11, false);
  assert.equal(h.element('ctxTrack').disabled, true);
  assert.equal(h.requests.length, 1);
});

test('a filter that leaves only part of an existing clue hides its complete score and judgment', async () => {
  const h = harness(), data = result();
  data.suggestions.targets[0].ids = ['13', '14'];
  data.suggestions.targets[0].currency = 'CNY';
  data.suggestions.targets[0].evidence = { conditional_group_share: { min: '0.20', max: '0.60' }, basis: '完整组合原行。' };
  await opened(h, data);
  assert.equal(h.cards().length, 2);
  const target = () => h.cards().find(card => card.dataset.key === 'target-0');
  assert.match(target().querySelector('.sg-score').textContent, /62/);
  assert.equal(cardJudgment(target()), '');
  h.network(async () => ({}));
  await h.change('ctxEnd', '2026-10-17');
  assert.match(target().querySelector('.sg-score').textContent, /—/);
  assert.equal(cardJudgment(target()), '筛选后只显示部分相关流水。');
  await target().click();
  assert.equal(h.rows().filter(row => row.classList.contains('clue-row')).length, 1);
  assert.equal(h.box(13).checked, false);
  assert.equal(h.trackCalls.length, 0);
});

test('screening out an anchor hides the aggregate judgment while source mode alone keeps it', async () => {
  const h = harness(), data = result();
  data.suggestions.sources[0].anchor_ids = ['12', '14'];
  await opened(h, data);
  await h.mode('source');
  assert.match(sourceCard(h).querySelector('.sg-score').textContent, /88/);
  h.network(async () => ({}));
  await h.change('ctxEnd', '2026-10-17');
  const clue = sourceCard(h);
  assert.match(clue.querySelector('.sg-score').textContent, /—/);
  assert.doesNotMatch(cardJudgment(clue), /覆盖/);
  await clue.click();
  assert.equal(h.rows().filter(row => row.classList.contains('clue-row')).length, 1);
  assert.equal(h.box(11).checked, false);
  assert.equal(h.trackCalls.length, 0);
});

test('proved same-transaction sources stay together with their own balances and individual tracking IDs', async () => {
  const h = harness(), data = result();
  const first = {...data.anchor, amount:'100.00', balance:'50.00', self_account:'SYN-PAYER',
    event_id:'SYN-PROVED', event_state:'confirmed', context_group:'event:SYN-PROVED', context_relation:'anchor'};
  const second = {...data.rows[2], trade_time:first.trade_time, amount:'100.00', balance:'200.00', self_account:'SYN-PAYEE',
    event_id:'SYN-PROVED', event_state:'confirmed', context_group:'event:SYN-PROVED', context_relation:'same_transaction'};
  data.anchor=first; data.rows=[first,second]; data.total=2;
  await opened(h,data);
  assert.match(h.element('ctxPos').textContent,/当前 2 条来源记录/);
  assert.doesNotMatch(h.element('ctxPos').textContent,/共 2 笔|第/);
  assert.deepEqual(h.rows().map(row=>cells(row)[4].querySelector('.context-ownership').textContent),['本笔','本笔']);
  assert.equal(cells(h.rows()[0])[7].textContent,'¥50.00');
  assert.equal(cells(h.rows()[1])[7].textContent,'¥200.00');
  assert.equal(h.box(12),null); assert.equal(h.box(13).dataset.id,'13');
  assert.equal(h.requests.length,1);
});

test('same-time unresolved sources never acquire a previous or next relation from their IDs', async () => {
  const h=harness(), data=result();
  data.rows.forEach(row=>{row.trade_time=data.anchor.trade_time; if (row !== data.anchor) row.context_relation = 'same_time';});
  await opened(h,data);
  assert.deepEqual(h.rows().map(row=>cells(row)[4].querySelector('.context-ownership').textContent),['同时间','本笔','同时间','同时间']);
  assert.equal(h.rows().length,4);
  assert.equal(h.requests.length,1);
});

test('direction filters rows locally and datetime only requests a bound monetary projection', async () => {
  const h = harness(); await opened(h); const reads = h.requests.length;
  await h.mode('source');
  assert.equal(h.requests.length, reads); assert.equal(h.rows().length, 1); assert.equal(h.box(13), null);
  await sourceCard(h).click(); assert.equal(h.box(11).checked, false);
  await h.check(11, true);
  await h.mode('target');
  assert.equal(h.rows().length, 2); assert.equal(h.box(11), null); assert.equal(sourceCard(h), undefined);
  assert.equal(h.element('ctxTrack').disabled, true);
  await h.change('ctxStart', '2026-10-18');
  await h.change('ctxEnd', '2026-10-18');
  assert.equal(h.rows().length, 1); assert.ok(h.box(14));
  await h.change('ctxEnd', '2026-10-17');
  assert.equal(h.rows().length, 0); assert.match(h.element('ctxPos').textContent, /起始时间不能晚于结束时间/);
  const filtered = h.requests.slice(reads);
  assert.equal(filtered.length, 2);
  for (const item of filtered) {
    assert.equal(item.path, '/api/context/summary');
    assert.equal(item.options.headers['X-FundsTrace-Review'], h.state.ctx.data.review.token);
    assert.deepEqual(JSON.parse(item.options.body).flow_ids, ['14']);
  }
  assert.equal(h.trackCalls.length, 0);
});

test('aggregate review starts with no anchor and disables direction modes until a row is chosen', async () => {
  const h = harness(), data = result();
  data.anchor = { ...data.anchor, id: null };
  data.review.kind = 'edge';
  h.network(async () => data);
  await h.run("openContext(null, {view: state.graphView, revision: 0, source: 'SYN-A', target: 'SYN-B', forward_ids: ['12'], reverse_ids: [], analysis_revision: state.lastGraph.analysis_revision, membership_digest: state.lastGraph.membership_digest})");
  assert.equal(h.requests[0].path, '/api/context/edge');
  assert.equal(h.state.ctx.flowId, null);
  assert.equal(h.element('ctxMode-source').disabled, true);
  assert.equal(h.element('ctxMode-target').disabled, true);
  assert.equal(h.element('ctxTotal').textContent, '共 4 条记录，');
  await sourceCard(h).click();
  assert.equal(h.box(11).checked, false);
  assert.equal(h.element('ctxTrack').disabled, true);
});

test('GET failure clears stale rows and offers a wide-table retry with tracking disabled', async () => {
  const h = harness(); await opened(h); h.network(async () => { throw new Error('合成读取故障'); }); await h.load();
  assert.equal(h.state.ctx.phase, 'error'); assert.equal(h.box(11), null); assert.equal(h.cards().length, 0);
  assert.equal(h.element('ctxTrack').disabled, true);
  const cell = h.element('ctxTbody').querySelector('td'); assert.ok(cell);
  assert.equal(cell.colSpan || Number(cell.getAttribute('colspan')), 14); assert.match(cell.textContent, /加载失败/);
  const retry = cell.querySelector('button'); assert.ok(retry);
  const empty = result('SYN-RETRY'); empty.rows = []; empty.suggestions = { sources: [], targets: [] };
  h.network(async () => empty); await retry.click(); await flush();
  assert.equal(h.state.ctx.phase, 'ready'); assert.equal(h.element('ctxTrack').disabled, true); assert.match(h.element('ctxAnchor').textContent, /SYN-RETRY/);
});

test('overlapping window requests publish only the latest response', async () => {
  const h = harness(); await opened(h); const older = deferred(), newer = deferred(); let reads = 0;
  h.network(() => ++reads === 1 ? older.promise : newer.promise);
  const first = h.change('ctxX', '7'), second = h.change('ctxX', '9');
  newer.resolve(result('SYN-NEW')); await second; older.resolve(result('SYN-OLD')); await first;
  assert.match(h.element('ctxAnchor').textContent, /SYN-NEW/); assert.equal(h.element('ctxAnchor').textContent.includes('SYN-OLD'), false);
  assert.equal(h.state.ctx.x, 9);
});

test('close and reopen revoke both old GET responses and retained recommendation controls', async () => {
  const h = harness(); await opened(h); const oldCard = sourceCard(h), older = deferred(); h.network(() => older.promise);
  const pending = h.load(); await h.close(); await opened(h, result('SYN-REOPENED'));
  const heading = h.element('ctxPos').textContent; assert.equal(oldCard.isConnected, false);
  await oldCard.click(); assert.equal(h.box(11).checked, false);
  older.resolve(result('SYN-CLOSED')); await pending;
  assert.equal(h.element('ctxPos').textContent, heading); assert.equal(h.element('ctxModal').style.display, 'flex');
});

test('A-B-A and canvas navigation invalidate pending context responses', async () => {
  for (const navigate of [h => { h.selectCase(2); h.selectCase(1); }, h => { h.state.canvasId = 9; h.state.canvasGeneration += 1; }]) {
    const h = harness(), older = deferred(); h.network(() => older.promise); const pending = h.open(); navigate(h);
    const fresh = result('SYN-CURRENT-BINDING'), late = result('SYN-STALE-BINDING');
    fresh.suggestions.sources[0].name = '当前合成来源';
    late.suggestions.sources[0].name = '过期合成来源';
    await opened(h, fresh); const heading = h.element('ctxPos').textContent;
    older.resolve(late); await pending;
    assert.equal(h.element('ctxPos').textContent, heading); assert.equal(h.state.ctx.data.account, 'SYN-CURRENT-BINDING');
    assert.match(h.element('ctxSuggest').textContent, /当前合成来源/);
    assert.doesNotMatch(h.element('ctxSuggest').textContent, /过期合成来源/);
  }
});

test('tracking locks conditions and selection, deduplicates submission and retains checks on a no-op', async () => {
  const h = harness(); await opened(h); await h.check(11, true); const tracking = deferred(); h.track(() => tracking.promise);
  const first = h.submit(), duplicate = h.run("$('#ctxTrack').onclick()"); await flush();
  assert.equal(h.trackCalls.length, 1); assert.deepEqual(h.trackCalls[0], ['11']); assert.equal(h.state.ctx.phase, 'tracking');
  assert.equal(h.reviewWrites[0].token, h.state.ctx.data.review.token);
  assert.equal(h.reviewWrites[0].current(), true);
  for (const id of ['ctxX', 'ctxStart', 'ctxEnd', 'ctxMode-all', 'ctxMode-source', 'ctxMode-target', 'ctxTrack']) assert.equal(h.element(id).disabled, true, id);
  for (const box of h.element('ctxTbody').querySelectorAll('.ctx-ck')) assert.equal(box.disabled, true);
  for (const card of h.cards()) assert.equal(card.disabled, true);
  tracking.resolve(false); await Promise.all([first, duplicate]);
  assert.equal(h.element('ctxModal').style.display, 'flex'); assert.equal(h.box(11).checked, true);
  assert.equal(h.state.ctx.phase, 'ready'); assert.equal(h.element('ctxTrack').disabled, false); assert.match(h.element('ctxTrackStatus').textContent, /保留勾选/);
});

test('tracking errors preserve selection and only explicit success closes the context', async () => {
  const h = harness(); await opened(h); await h.check(11, true); h.track(async () => { throw new Error('合成上图故障'); });
  await h.submit(); assert.equal(h.box(11).checked, true); assert.equal(h.element('ctxModal').style.display, 'flex');
  assert.match(h.element('ctxTrackStatus').textContent, /失败/); assert.equal(h.element('ctxTrack').disabled, false);
  h.track(async () => true); await h.submit(); assert.equal(h.element('ctxModal').style.display, 'none'); assert.equal(h.state.ctx.closed, true);
});

test('late tracking completion cannot close or unlock a reopened context session', async () => {
  const h = harness(); await opened(h); await h.check(11, true); const tracking = deferred(); h.track(() => tracking.promise);
  const pending = h.submit(); await h.close(); await opened(h, result('SYN-NEXT-SESSION'));
  assert.equal(h.reviewWrites[0].current(), false);
  const current = h.state.ctx; tracking.resolve(true); await pending;
  assert.equal(h.state.ctx, current); assert.equal(h.element('ctxModal').style.display, 'flex'); assert.equal(h.element('ctxTrack').disabled, true);
  assert.equal(h.box(11).checked, false); assert.equal(h.state.ctx.phase, 'ready');
});

test('automatic source assessment uses the existing context read and keeps original balances and scores', async () => {
  const h = harness(); await opened(h);
  assert.equal(h.requests.length, 1);
  assert.deepEqual(h.element('ctxSuggest').querySelectorAll('.sg-title').map(title => title.textContent), ['来源', '去向']);
  assert.equal(cardJudgment(sourceCard(h)), '');
  assert.match(h.element('ctxSuggest').textContent, /88/);
  assert.match(h.rows()[2].textContent, /-10,000,000,000,000.01/);
  await h.mode('source');
  assert.equal(h.requests.length, 1, 'local filtering adds no analysis request');
  assert.equal(cardJudgment(sourceCard(h)), '');
  h.network(async () => ({}));
  await h.change('ctxEnd', '2026-10-17T12:34:30');
  assert.equal(h.element('ctxSuggest').querySelector('.context-score-note'), null);
  assert.doesNotMatch(h.element('ctxSuggest').textContent, /FIFO|条件范围/);
});

test('relationship summary shows only count and money and keeps actual date precision', async () => {
  const h = harness(), data = result();
  data.review.directional_funds = { forward: { status: 'partial', observation_count: 9, count: { lower: 0, upper: 1 },
    totals: [{ amount: null, currency: 'CNY', lower: '0.00', upper: '0.40' }] },
  booked: { forward: { cnt: 1, total: '0.40', amount_totals: [{ currency: 'CNY', amount: '0.40' }] } } };
  data.rows[0].trade_time = '2026-10-17 00:00:00';
  Object.assign(data.rows[0].review_fields, { trade_time_precision: 'date', trade_time_source_date: '2026-10-17' });
  data.rows[1].trade_time = '2026-10-17 00:00:00';
  await opened(h, data);
  const line = h.element('ctxAnchor').querySelector('.context-line-caption').textContent;
  assert.match(line, /1 笔 ¥0\.40/);
  assert.doesNotMatch(line, /待核|记录|未核|–/);
  assert.doesNotMatch(cells(h.rows()[0])[4].textContent, /00:00:00/);
  assert.match(cells(h.rows()[1])[4].textContent, /00:00:00/);
});

test('source and target cards show only the scoped Qwen explanation, with one request per card', async () => {
  const h = harness(), data = result();
  h.run('window.FundsDesktopPreferences = {}');
  const checks = {ledger_roles:true, currency_amount:true, order:true, own_balance:true, adjacent_balances:false, interval_records:true};
  Object.assign(data.suggestions.sources[0].evidence, { checks, conditional_group_share: null });
  data.suggestions.sources[0].score = 83;
  data.suggestions.targets[0].evidence = { checks, conditional_group_share: { min: '0.00', max: '1.00' } };
  h.network(async (path, options) => {
    if (path.endsWith('/model-trial/status')) return { enabled: true, configured: true };
    if (!path.endsWith('/review-model-trial')) return data;
    const body = JSON.parse(options.body);
    assert.equal(body.explain, true);
    assert.deepEqual([body.kind, body.group_flow_id], body.kind === 'trace_source' ? ['trace_source', '11'] : ['trace_target', '13']);
    return { state: body.kind === 'trace_source' ? 'proposal' : 'rule', explanation_calls: 1, explanation_model: 'qwen35-4b',
      explanation: body.kind === 'trace_source' ? '还要核对本方余额，才能追查这笔钱。' : '这笔入账最多可覆盖这笔支出中的 ¥1.00。',
      packet: { facts: { case_id: '1', canvas_id: '0', kind: body.kind,
        anchor_id: '12', group_flow_id: body.group_flow_id, revision: data.analysis_revision } } };
  });
  await h.open();
  const target = h.cards().find(card => card.dataset.key === 'target-0');
  await target.click(); await flush();
  assert.equal(cardJudgment(target), '这笔入账最多可覆盖这笔支出中的 ¥1.00。');
  const card = sourceCard(h);
  await card.click(); await flush();
  assert.equal(cardJudgment(card), '还要核对本方余额，才能追查这笔钱。');
  assert.match(card.querySelector('.sg-evidence').textContent, /核对通过 5\/6 项，待核对侧余额/);
  assert.equal(card.parentElement.querySelectorAll('.sg-judgment').length, 1);
  await card.click(); await card.click(); await flush();
  assert.equal(h.requests.filter(request => request.path.endsWith('/review-model-trial')).length, 2);
  assert.equal(h.trackCalls.length, 0);
});

test('the ordinary review shows time exclusion and its source rows while the amount is pending', async () => {
  const h = harness(), data = result();
  h.run('window.FundsDesktopPreferences = {}');
  data.suggestions.sources.push({ ids: ['14'], account: 'SYN-SECOND-SOURCE', name: '合成来源二', score: 60 });
  const sourceId = '2'.repeat(32);
  h.network(async (path, options) => {
    if (path.endsWith('/model-trial/status')) return { enabled: true, configured: false };
    if (path.includes('/record/')) return { originals: [] };
    if (!path.endsWith('/review-model-trial')) return data;
    const body = JSON.parse(options.body);
    assert.deepEqual(body.group_flow_ids, ['11', '14']);
    return { state: 'pending', reason: 'trace_time_exclusion', receipt: { status: 'pending', proposition: 'conditional_range' },
      packet: { facts: { case_id: '1', canvas_id: '0', revision: data.analysis_revision,
        kind: body.kind, anchor_id: body.anchor_id, group_flow_ids: body.group_flow_ids,
        source_refs: ['F11', 'F14'], target_refs: ['F12'], checks: { interval_records: false },
        reasons: ['unresolved_source_records'] },
      records: ['F11', 'F12', 'F14'].map((id, index) => ({ id,
        raw: { trade_time_wall: id === 'F12' ? '2026-07-05 10:00:00' : '2026-07-05 10:01:00' },
        structured: {}, locator: { source_id: sourceId,
          source_row: index + 2, member_path: 'table.csv', file_url: '/synthetic-original' } })) } };
  });
  await h.open();
  await h.element('ctxTbody').querySelector('.ctx-record[data-id="12"]').click();
  await flush();
  const actions = h.element('ctxDetailContent').querySelector('.context-review-actions');
  assert.ok(actions && !actions.hidden, h.element('ctxDetailContent').textContent);
  const chooser = actions.querySelector('select');
  chooser.value = '1';
  await chooser.fire('change').completion;
  await actions.querySelector('button').click();
  assert.equal(actions.querySelector('[role="status"]').textContent,
    '原表交易时钟显示，所选出账早于所选入账。若这一时钟反映资金可用顺序，可排除这组入账为这些出账提供资金。');
  assert.match(actions.textContent, /资金份额待核，需核清区间内收支是否完整/);
  assert.match(actions.textContent, /案内有尚未界定账簿或时段的来源记录/);
  assert.equal(actions.querySelectorAll('a').length, 3);
  assert.match(actions.textContent, /原行 3 · 出账 · 2026-07-05 10:00:00/);
  assert.equal(h.trackCalls.length, 0);
});

for (const configured of [false, true]) test(`the joint review shows necessary upper limits (model configured: ${configured})`, async () => {
  const h = harness(), data = result();
  const account = data.anchor.self_account;
  for (const row of data.rows) {
    const incoming = row.id === 11 || row.id === 13;
    Object.assign(row, { self_account: account, payer_account: incoming ? 'SYN-COUNTER' : account,
      payee_account: incoming ? account : 'SYN-COUNTER' });
  }
  h.run('window.FundsDesktopPreferences = {}');
  const explainRequests = [], firstExplanation = deferred();
  const sourceId = '3'.repeat(32), file = `/api/v1/cases/1/sources/${sourceId}/file`;
  h.network(async (path, options) => {
    if (path.endsWith('/model-trial/status')) return { enabled: true, configured };
    if (path.includes('/record/14?')) return { originals: [{
      filename: '合成原件', member_path: 'table.csv', source_row: 5, download_url: file,
      cells: [{ header: '合成栏名', value: '可回查原格' }],
    }] };
    if (path.includes('/record/')) return { originals: [] };
    if (!path.endsWith('/review-model-trial')) return data;
    const body = JSON.parse(options.body);
    assert.equal(body.kind, 'trace_source');
    assert.deepEqual(body.source_flow_ids, ['11', '13']);
    assert.deepEqual(body.target_flow_ids, ['12', '14']);
    if (body.explain) explainRequests.push(options.signal);
    const sequence = explainRequests.length;
    if (body.explain && sequence === 1) await firstExplanation.promise;
    return { state: 'pending', reason: 'ledger_evidence_missing',
      ...(body.explain ? { explanation: '已核证的条件上界和限制。', explanation_calls: 1, explanation_model: 'qwen35-4b' } : {}),
      receipt: { status: 'pending', proposition: 'conditional_range' },
      packet: { packet_sha256: sequence > 2 ? 'changed-packet' : 'initial-packet', facts: { case_id: '1', canvas_id: '0', revision: data.analysis_revision,
        kind: body.kind, anchor_id: body.anchor_id, selected_source_flow_ids: body.source_flow_ids,
        selected_target_flow_ids: body.target_flow_ids, source_count: 2, target_count: 2,
        selected_order_upper_bound: { currency: 'CNY', joint_upper: '5.37', same_direction_ties: true, targets: [
          { ref: 'F12', source_row: 3, upper: '3.35', later_source_rows: [4] },
          { ref: 'F14', source_row: 5, upper: '2.02', later_source_rows: [] },
        ] }, target_refs: ['F14'],
        checks: { own_balance: false, interval_records: false }, reasons: [] },
        records: [{ id: 'F14', raw: {}, locator: { source_id: sourceId, member_path: 'table.csv',
          source_row: 5, file_url: file } }] } };
  });
  await h.open();
  await h.element('ctxTbody').querySelector('.ctx-record[data-id="12"]').click();
  await flush();
  const actions = h.element('ctxDetailContent').querySelector('.context-review-actions');
  const chooser = actions.querySelector('select');
  chooser.value = '1';
  await chooser.fire('change').completion;
  for (const input of actions.querySelectorAll('input[name]')) input.checked = true;
  await actions.querySelector('button').click();
  assert.equal(actions.querySelector('[role="status"]').textContent,
    '按下列条件，所选入账合计至多承接 CNY 5.37。实际份额待核。');
  assert.match(actions.textContent, /原交易时钟反映资金可用顺序。同笔入账分给各目标的金额合计不超过该笔金额/);
  assert.match(actions.textContent, /所选同刻同向的原行各自保留，先后不影响上界/);
  assert.match(actions.textContent, /原行 3 出账单独核算，至多承接 CNY 3\.35。可排除较晚的入账原行 4/);
  assert.match(actions.textContent, /原行 5 出账单独核算，至多承接 CNY 2\.02/);
  assert.match(actions.textContent, /实际份额需核清本方余额是否为笔后账面余额、区间内收支是否完整/);
  assert.equal(explainRequests.length, 0);
  const action = [...actions.querySelectorAll('button')].find(item => item.textContent === '解释这组上界');
  assert.equal(!!action, configured);
  if (configured) {
    const pendingExplanation = action.click();
    await flush();
    assert.equal(explainRequests.length, 1);
    assert.equal(action.disabled, true);
    const original = actions.querySelectorAll('button').find(item => item.textContent === '查看原行 5');
    await original.click();
    assert.equal(explainRequests[0].aborted, true);
    const back = actions.querySelectorAll('button').find(item => item.textContent === '返回核证结果');
    await back.click();
    assert.equal(action.disabled, false);
    assert.doesNotMatch(actions.textContent, /正在生成说明/);
    firstExplanation.resolve();
    await pendingExplanation;
    assert.doesNotMatch(actions.textContent, /已核证的条件上界和限制/);
    await action.click();
    assert.equal(explainRequests.length, 2);
    assert.equal(actions.querySelector('[aria-label="条件上界说明"]').textContent, '已核证的条件上界和限制。');
    await action.click();
    assert.equal(explainRequests.length, 3);
    assert.equal(actions.querySelector('[aria-label="条件上界说明"]'), null);
    assert.match(actions.textContent, /原件或所选流水已更新/);
  }
  assert.equal(h.trackCalls.length, 0);
});

async function citedUpperBound(h, readOriginal, { cited = true } = {}) {
  const data = result();
  data.suggestions.targets[0].anchor_ids = ['12'];
  const sourceId = '2'.repeat(32), member = 'synthetic.xlsx/worksheets/0001';
  const file = `/api/v1/cases/1/sources/${sourceId}/file`;
  const original = { filename: '合成目标', member_path: member, source_row: 96, download_url: file,
    cells: [{ header: '合成栏名', value: '合成目标原格' }] };
  h.run('window.FundsDesktopPreferences = {}');
  h.network(async (path, options) => {
    if (path.endsWith('/model-trial/status')) return { enabled: true, configured: false };
    if (path.includes('/record/13?')) {
      assert.equal(path, '/api/context/12/record/13?case_id=1&canvas_id=0');
      assert.equal(options.headers['X-FundsTrace-Review'], data.review.token);
      return readOriginal(original, options);
    }
    if (path.includes('/record/')) return { originals: [] };
    if (!path.endsWith('/review-model-trial')) return data;
    const body = JSON.parse(options.body);
    assert.equal(body.kind, 'trace_target');
    assert.equal(body.group_flow_id, '13');
    return { state: 'pending', reason: 'ledger_evidence_missing',
      packet: { facts: { case_id: '1', canvas_id: '0', revision: data.analysis_revision,
        kind: body.kind, anchor_id: body.anchor_id, group_flow_id: body.group_flow_id, target_refs: ['F13'],
        selected_order_upper_bound: { currency: 'CNY', joint_upper: '3.35',
          targets: [{ ref: 'F13', source_row: 96, upper: '3.35', later_source_rows: [97] }] } },
      records: [{ id: 'F13', raw: {}, locator: { source_id: sourceId, member_path: member,
        source_row: 96, file_url: cited ? file : null } }] } };
  });
  await h.open();
  await h.element('ctxTbody').querySelector('.ctx-record[data-id="12"]').click();
  await flush();
  const actions = h.element('ctxDetailContent').querySelector('.context-review-actions');
  assert.ok(actions && !actions.hidden);
  const picker = actions.querySelector('select');
  picker.value = '1';
  await picker.fire('change').completion;
  await actions.querySelector('button').click();
  const button = actions.querySelectorAll('button').find(item => item.textContent === '查看原行 96');
  return { actions, button };
}

test('the cited upper-bound original opens in scope and returns to the same result', async () => {
  const h = harness();
  const { actions, button } = await citedUpperBound(h, item => ({ originals: [
    { ...item, member_path: 'elsewhere.xlsx', cells: [{ header: '合成栏名', value: '合成错表原格' }] },
    item,
  ] }));
  assert.ok(button);
  assert.equal(button.tagName, 'BUTTON');
  assert.match(actions.textContent, /可排除较晚的入账原行 97/);
  await button.click();
  assert.match(actions.textContent, /合成目标原格/);
  assert.doesNotMatch(actions.textContent, /合成错表原格/);
  const back = actions.querySelectorAll('button').find(item => item.textContent === '返回核证结果');
  assert.ok(back);
  assert.equal(h.document.activeElement, back);
  await back.click();
  assert.equal(h.document.activeElement, button);
  assert.match(actions.textContent, /至多承接 CNY 3\.35/);
  assert.doesNotMatch(actions.textContent, /合成目标原格/);
  assert.equal(h.requests.filter(item => item.path.endsWith('/review-model-trial')).length, 1);
});

for (const mode of ['withdrawn', 'case changed', 'unavailable']) test(`the cited upper-bound original rejects ${mode} evidence`, async () => {
  const h = harness(), pending = deferred();
  let read;
  const { actions, button } = await citedUpperBound(h, (original, options) => {
    read = { original, options };
    if (mode === 'withdrawn') throw Object.assign(new Error('合成撤回'), { status: 409 });
    return pending.promise;
  }, { cited: mode !== 'unavailable' });
  if (mode === 'unavailable') {
    assert.equal(button, undefined);
    assert.match(actions.textContent, /原行 96 出账/);
    return;
  }
  assert.ok(button);
  const clicked = button.click();
  await flush();
  assert.ok(read);
  if (mode === 'case changed') {
    await h.selectCase(2);
    assert.equal(read.options.signal.aborted, true);
    pending.resolve({ originals: [read.original] });
  }
  await clicked;
  assert.doesNotMatch(h.element('ctxDetailContent').textContent, /合成目标原格/);
  assert.equal(h.element('ctxModal').style.display, 'none');
});

test('closing the review aborts an in-place model request and discards its late citation', async () => {
  const h = harness(), data = result(), late = deferred();
  h.run('window.FundsDesktopPreferences = {}');
  Object.assign(data.suggestions.sources[0].evidence, { checks: { ledger_roles: true, currency_amount: true }, conditional_group_share: null });
  h.network(async path => {
    if (path.endsWith('/model-trial/status')) return { enabled: true, configured: true };
    if (path.endsWith('/review-model-trial')) return late.promise;
    return data;
  });
  await h.open();
  await sourceCard(h).click(); await flush();
  const request = h.requests.find(item => item.path.endsWith('/review-model-trial'));
  assert.ok(request);
  await h.close();
  assert.equal(request.options.signal.aborted, true);
  await opened(h);
  late.resolve({ state: 'proposal', explanation_calls:1, explanation_model:'qwen35-4b', explanation:'过期的说明。',
    packet: { facts: { case_id: '1', canvas_id: '0', kind: 'trace_source',
      anchor_id: '12', group_flow_id: '11', revision: data.analysis_revision } } });
  await flush();
  assert.doesNotMatch(h.element('ctxSuggest').textContent, /过期的说明/);
});

test('source detail uses the bound token, leaves checks intact and discards late raw content', async () => {
  const h = harness(); await opened(h); await h.check(11, true);
  const detail = deferred();
  h.network(() => detail.promise);
  const trigger = h.element('ctxTbody').querySelector('.ctx-record[data-id="11"]');
  const loading = trigger.click();
  await flush();
  assert.equal(h.element('ctxDetail').hidden, false);
  assert.equal(h.element('ctxTrack').disabled, true);
  assert.match(h.requests.at(-1).path, /\/api\/context\/12\/record\/11/);
  assert.equal(h.requests.at(-1).options.headers['X-FundsTrace-Review'], h.state.ctx.data.review.token);
  await h.element('ctxDetailBack').click();
  detail.resolve({ originals: [{filename: 'SYN-LATE', cells: []}] });
  await loading;
  assert.equal(h.element('ctxDetail').hidden, true);
  assert.equal(h.element('ctxDetailContent').textContent, '');
  assert.equal(h.box(11).checked, true);
  assert.equal(h.element('ctxTrack').disabled, false);
});

const fieldScope = {
  source_id: '2'.repeat(32), sha256: 'c'.repeat(64), parser_version: 'funds-trace-table-v87',
  member_path: 'table.csv', source_row: 2, header_row: 1,
};
const fieldOriginal = {
  filename: 'synthetic.csv', member_path: 'table.csv', source_row: 2, download_url: '/synthetic-original',
  citation_scope: fieldScope, cells: [
    { column: 3, header: '对方账号', value: 'SYN-SAME' },
    { column: 8, header: '备注', value: 'SYN-SAME' },
  ],
};

function fieldReceipt(body, evidence_status) {
  return { ...body, flow_id: '11', classification: 'rule_control', business_status: 'pending', evidence_status };
}

async function selectField(h, column = '3') {
  await h.element('ctxTbody').querySelector('.ctx-record[data-id="11"]').click();
  assert.ok(h.element('ctxVerifyField').disabled);
  await h.change('ctxFieldCandidate', 'account');
  await h.change('ctxFieldColumn', column);
  assert.equal(h.element('ctxVerifyField').disabled, false);
}

test('original citation support and refusal stay in the review; only a manual checkbox tracks', async () => {
  const h = harness(); await opened(h);
  h.network(async (_path, options) => {
    if (!options.body) return { originals: [fieldOriginal] };
    const body = JSON.parse(options.body);
    assert.equal(options.headers['X-FundsTrace-Review'], h.state.ctx.data.review.token);
    assert.deepEqual(body, { case_id: 1, canvas_id: 0, analysis_revision: 'b'.repeat(32), candidate: 'account',
      citation: { ...fieldScope, column: body.citation.column } });
    return fieldReceipt(body, body.citation.column === 3 ? 'supported' : 'refuted');
  });
  await sourceCard(h).click();
  await selectField(h, '8'); await h.element('ctxVerifyField').click();
  assert.match(h.element('ctxFieldStatus').textContent, /拒绝所引原列.*业务判断待核/);
  await h.change('ctxFieldColumn', '3'); await h.element('ctxVerifyField').click();
  assert.match(h.element('ctxFieldStatus').textContent, /核证时的修订支持原第 2 行第 3 列.*业务判断待核/);
  assert.deepEqual(h.element('ctxDetailContent').querySelectorAll('dt').map(node => node.textContent), ['对方账号', '备注']);
  assert.equal(h.element('ctxDetailContent').querySelector('a').getAttribute('href'), '/synthetic-original');
  await h.element('ctxDetailBack').click();
  const card = sourceCard(h);
  assert.match(card.querySelector('.sg-score').textContent, /88/);
  assert.equal(cardJudgment(card), '');
  assert.match(card.querySelector('.sg-field-receipt').textContent, /核证时的修订.*业务判断待核/);
  assert.equal(h.element('ctxTrack').disabled, true);
  assert.equal(h.box(11).checked, false);
  assert.equal(h.trackCalls.length, 0);
  await h.check(11, true); await h.submit(); assert.deepEqual(h.trackCalls, [['11']]);
});

test('late original citation response cannot paint in an A-B-A case binding', async () => {
  const h = harness(), pending = deferred(); let body;
  await opened(h);
  h.network(async (_path, options) => {
    if (!options.body) return { originals: [fieldOriginal] };
    body = JSON.parse(options.body);
    return pending.promise;
  });
  await selectField(h);
  const admitted = h.element('ctxVerifyField').click();
  await flush();
  assert.match(h.element('ctxFieldStatus').textContent, /正在核证/);
  h.selectCase(2); h.selectCase(1);
  await opened(h, result('SYN-REBOUND'));
  pending.resolve(fieldReceipt(body, 'supported'));
  await admitted;
  assert.equal(h.element('ctxFieldStatus'), null);
  assert.doesNotMatch(h.element('ctxSuggest').textContent, /核证时的修订/);
  assert.equal(h.state.ctx.data.account, 'SYN-REBOUND');
});

test('local batch withdrawal revokes pending original citation display before awaiting the write', async () => {
  const h = harness(), proof = deferred(), removed = deferred(); let body;
  await opened(h);
  h.network(async (_path, options) => {
    if (!options.body) return { originals: [fieldOriginal] };
    body = JSON.parse(options.body);
    return proof.promise;
  });
  await selectField(h);
  const admission = h.element('ctxVerifyField').click();
  await flush();
  h.run(section('async function deleteBatch(', 'function bindBatchUI('));
  h.run(`const batchForWithdrawal = { id: 51, filename: 'synthetic.csv' };
    const batchView = { rows: [batchForWithdrawal] };
    const batchCurrent = () => true;
    const closeBatchMenu = () => {}, setBatchBusy = () => {}, renderBatchRows = () => {}, showBatchFeedback = () => {};
    const confirm = () => true, refreshAll = async () => {};
    let removalStarted = false;`);
  h.run('const batchRequest = async () => { removalStarted = true; await window.testBatchRemoval; };');
  h.run('window').testBatchRemoval = removed.promise;
  const removal = h.run('deleteBatch(batchForWithdrawal, caseContext())');
  await flush();
  assert.equal(h.run('removalStarted'), true);
  assert.equal(h.state.ctx.closed, true);
  assert.equal(h.element('ctxDetailContent').textContent, '');
  proof.resolve(fieldReceipt(body, 'supported'));
  await admission;
  assert.equal(h.element('ctxDetailContent').textContent, '');
  assert.equal(h.element('ctxSuggest').textContent, '');
  removed.resolve();
  await removal;
});
