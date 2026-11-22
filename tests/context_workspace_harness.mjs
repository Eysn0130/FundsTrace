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
  const context = vm.createContext({ state, document, window, exportWorkspace: null, caseWorkspace: null, URL, Set, Map, Event, fetch,
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
    review_fields: { account: 'SYN-PAYEE', name: '合成收款方', direction: dir === 'in' ? '贷' : '借' },
    review_roles: [], ...extra });
  const anchor = flow(12, 3, 'out', 'self', { context_relation: 'anchor', payee_name: account });
  return { account, anchor, total: 9, pos: 3, rows: [
    flow(11, 2, 'in', 'self', { trade_time: '2026-10-17 12:33:56', context_relation: 'before', review_roles: ['source'] }), anchor,
    flow(13, 4, 'out', 'counter', { balance: '-10000000000000.01', trade_time: '2026-10-17 12:35:56', context_relation: 'after', review_roles: ['target'] }),
    flow(14, 5, 'out', 'self', { trade_time: '2026-10-18 12:36:56', context_relation: 'after', review_roles: ['target'] })],
    review: { token: 'a'.repeat(32), pair: { status: 'unknown', candidate_ids: [] }, principal: { source: {
      status: 'unknown', assessment: [{ status: 'unknown', message: `合成来源证据-${account}` }],
      targets: [{ amount: '100.00', min: '0.00', max: '100.00', fifo: null }], currency: 'CNY' } } },
    suggestions: { sources: [{ ids: ['11'], account: 'SYN-SOURCE', name: '合成来源', score: 88, evidence: {
      judgment: '已记录账面支持部分关联，实际本金归属待核。', basis: '12:33:56 的 1 条流水是锚点前的候选入账。' } }],
      targets: [{ ids: ['13'], account: 'SYN-TARGET', name: '合成去向', score: 62 }] } };
}
async function opened(h, data = result()) { h.network(async () => data); await h.open(); }

test('bounded unknown posting windows disclose omitted ties in the existing detail', async () => {
  const h = harness(), data = result();
  data.ordering = {status:'partial', reasons:['neighbor_boundary_order_unknown', 'posting_order_unknown']};
  await opened(h, data);
  assert.match(h.element('ctxPos').textContent, /边界同刻记录无法排序，未任意截取/);
  assert.match(h.element('ctxPos').textContent, /同刻记录先后未确定/);
});
const cells = row => row.querySelectorAll('td');
const picked = card => card.getAttribute('aria-pressed') === 'true' || card.classList.contains('picked');
const sourceCard = h => h.cards().find(card => (card.dataset.ids || '').split(',').includes('11'));

test('context rows retain original column order, exact money, direction and tracked membership', async () => {
  const h = harness(), data = result(); data.rows[0].summary = '<img src=x onerror="SYN"> & "合成"';
  data.rows[0].payer_name = '<b>合成来款方</b>'; await opened(h, data);
  const rows = h.rows(); assert.equal(rows.length, 4);
  for (const row of rows) assert.equal(cells(row).length, 14);
  assert.match(cells(rows[0])[4].textContent, /之前/); assert.match(cells(rows[1])[4].textContent, /本笔/);
  assert.match(cells(rows[2])[4].textContent, /之后/);
  assert.equal(cells(rows[0])[2].textContent, 'SYN-PAYEE');
  assert.equal(cells(rows[0])[5].textContent, '贷'); assert.equal(cells(rows[2])[5].textContent, '借');
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
  assert.equal(card.querySelector('.sg-judgment').textContent, '已记录账面支持部分关联，实际本金归属待核。');
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
  data.suggestions.targets[0].evidence = { judgment: '完整组合可核对。', basis: '完整组合原行。' };
  await opened(h, data);
  assert.equal(h.cards().length, 2);
  const target = () => h.cards().find(card => card.dataset.key === 'target-0');
  assert.match(target().querySelector('.sg-score').textContent, /62/);
  h.network(async () => ({}));
  await h.change('ctxEnd', '2026-10-17');
  assert.match(target().querySelector('.sg-score').textContent, /—/);
  assert.doesNotMatch(target().querySelector('.sg-judgment').textContent, /完整组合/);
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
  assert.doesNotMatch(clue.querySelector('.sg-judgment').textContent, /部分关联/);
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
    await opened(h, result('SYN-CURRENT-BINDING')); const heading = h.element('ctxPos').textContent;
    older.resolve(result('SYN-STALE-BINDING')); await pending;
    assert.equal(h.element('ctxPos').textContent, heading); assert.equal(h.state.ctx.data.account, 'SYN-CURRENT-BINDING');
    assert.match(h.element('ctxSuggest').textContent, /合成来源证据-SYN-CURRENT-BINDING/);
    assert.doesNotMatch(h.element('ctxSuggest').textContent, /合成来源证据-SYN-STALE-BINDING/);
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
  assert.match(h.element('ctxSuggest').textContent, /账面规则范围：—/);
  assert.match(h.element('ctxSuggest').textContent, /FIFO 证据不足/);
  assert.match(h.element('ctxSuggest').textContent, /88/);
  assert.match(h.rows()[2].textContent, /-10,000,000,000,000.01/);
  await h.mode('source');
  assert.equal(h.requests.length, 1, 'local filtering adds no analysis request');
  assert.match(h.element('ctxSuggest').textContent, /账面规则范围：—/);
  h.network(async () => ({}));
  await h.change('ctxEnd', '2026-10-17T12:34:30');
  assert.equal(h.element('ctxSuggest').querySelector('.context-score-note'), null);
  assert.doesNotMatch(h.element('ctxSuggest').textContent, /FIFO|条件范围/);
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
