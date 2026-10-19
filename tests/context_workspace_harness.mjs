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
  for (const id of ['ctxCaseName', 'ctxAnchor', 'ctxPos', 'ctxSelected', 'ctxTrackedCount', 'ctxTrackStatus', 'ctxSuggest']) add(modal, id);
  const tabs = add(modal, 'ctxTabs');
  for (const side of ['payee', 'payer']) { const tab = add(tabs, `ctxSide-${side}`, 'button'); tab.className = 'stab'; tab.dataset.side = side; }
  add(modal, 'ctxX', 'input').value = '5'; add(modal, 'ctxBook', 'select').value = 'all';
  const track = add(modal, 'ctxTrack', 'button'); add(track, 'ctxTrackLabel'); add(modal, 'ctxClose', 'button'); add(modal, 'ctxCancel', 'button');
  const viewport = add(modal, 'ctxTableScroll'); const table = add(viewport, 'ctxTable', 'table'); add(table, 'ctxTbody', 'tbody');
  for (const id of ['ctxScrollLeft', 'ctxScrollRight', 'ctxHorizontalPosition']) add(modal, id, id === 'ctxHorizontalPosition' ? 'input' : 'button');
  for (const id of ['btnCloseDrawer', 'drawer', 'drawerBody', 'btnImport', 'btnImport2', 'importModal', 'impPbar', 'impClose', 'impCancel',
    'impFile', 'impRe', 'impCommit', 'impChangeFile', 'impMapping', 'impPreviewScroll', 'impHorizontalPosition', 'impScrollLeft', 'impScrollRight', 'impPreviewTable',
    'flowTbody', 'canvasSel', 'flowCkAll', 'caseSelect', 'analysisLink', 'batchList']) add(document.body, id);
  const requests = [], trackCalls = [], notices = [];
  let network = async () => { throw new Error('Unexpected request'); }, tracker = async () => true;
  const state = { caseId: 1, caseGeneration: 1, canvasId: 0, canvasGeneration: 1, ctx: null, tracked: new Set([12]),
    lastGraph: { nodes: [], edges: [] }, graphView: { revision: 0 }, search: { page: 1 }, cases: [{ id: 1, name: '合成甲案' }, { id: 2, name: '合成乙案' }] };
  const window = new Element('#window'); window.appendChild(document.root);
  window.location = { href: 'http://synthetic.invalid/?case_id=1' }; window.history = { replaceState(_state, _title, url) { window.location.href = String(url); } };
  const fetch = async (path, options) => {
    requests.push({ path, options }); const value = await network(path, options);
    return { ok: true, status: 200, json: async () => value, text: async () => JSON.stringify(value) };
  };
  const context = vm.createContext({ state, document, window, exportWorkspace: null, caseWorkspace: null, URL, Set, Map, Event, fetch,
    $: selector => document.querySelector(selector), $$: selector => document.querySelectorAll(selector),
    api: async (path, options) => (await fetch(path, options)).json(),
    trackFlows: ids => { trackCalls.push([...ids]); return tracker(ids); },
    toast: (message, type) => notices.push({ message, type }),
    preferenceStorage: { setItem() {} }, cancelGraphLayout() {}, resetGraphView() {}, newGraphPresentation: () => ({}),
    revokePhotoCrop() {}, resetNodeWorkspace() {}, syncEdgeTypeControl() {}, closeDrawer() {}, syncFlowCkCount() {}, updateHeader() {}, resetBatchView() {},
    showNodeDrawer: async () => {}, showEdgeDrawer: async () => {}, loadGraph: async () => {}, renderGraph() {},
    bindTableHorizontalScroll(parts) { for (const node of Object.values(parts)) assert.ok(node); return { sync() {} }; },
    setTimeout: callback => { callback(); return 0; }, clearTimeout() {}, requestAnimationFrame: callback => { callback(); return 0; },
  });
  vm.runInContext(money, context);
  vm.runInContext(section('function esc(', 'function toast('), context);
  vm.runInContext(section('let pendingCanvasId = 0;', 'async function loadCases()'), context);
  vm.runInContext(section('function caseContext()', '/* ---------------- 初始化'), context);
  vm.runInContext(section('const SYSTEM_ACCOUNT_NAMES =', '/* ---------------- 导入 ---------------- */'), context);
  vm.runInContext('bindContextUI()', context);
  vm.runInContext(workflow, context);
  window.fire('DOMContentLoaded');
  return { state, document, requests, trackCalls, notices, element: id => document.getElementById(id),
    network(handler) { network = handler; }, track(handler) { tracker = handler; }, run(code) { return vm.runInContext(code, context); },
    open(id = 12) { return vm.runInContext(`openContext(${id})`, context); }, load() { return vm.runInContext('loadContext()', context); },
    close() { return document.getElementById('ctxClose').click(); }, selectCase(id) { return vm.runInContext(`selectCase(${id})`, context); },
    rows() { return document.querySelectorAll('#ctxTbody tr').filter(row => row.querySelectorAll('td').length === 8); },
    box(id) { return document.querySelector(`#ctxTbody .ctx-ck[data-id="${id}"]`); }, cards() { return document.querySelectorAll('#ctxSuggest .sg-item').filter(card => {
      for (let node = card; node; node = node.parentElement) if (node.hidden || node.style.display === 'none') return false;
      return true;
    }); },
    check(id, checked) { const box = this.box(id); assert.ok(box, `missing visible checkbox ${id}`); box.checked = checked; return box.fire('change').completion; },
    change(id, value) { const node = document.getElementById(id); node.value = value; return node.fire('change').completion; },
    side(value) { return document.getElementById(`ctxSide-${value}`).click(); },
    submit() { return document.getElementById('ctxTrack').click(); },
  };
}

function result(account = 'SYN-CURRENT') {
  const flow = (id, rn, dir, book, extra = {}) => ({ id, rn, dir, book, trade_time: '2026-10-17 12:34:56',
    payer_name: '合成来款方', payer_account: 'SYN-PAYER', payee_name: '合成收款方', payee_account: 'SYN-PAYEE',
    amount: '9007199254740993.07', currency: 'CNY', balance: null, summary: '合成交易摘要', ...extra });
  const anchor = flow(12, 3, 'out', 'self');
  return { account, anchor, total: 9, pos: 3, rows: [flow(11, 2, 'in', 'self'), anchor,
    flow(13, 4, 'out', 'counter', { balance: '-10000000000000.01' }), flow(14, 5, 'out', 'self')],
    suggestions: { sources: [{ ids: [11, 12, 99], account: 'SYN-SOURCE', name: '合成来源', score: 88, reasons: ['合成原因'], tags: ['拆分'] }],
      targets: [{ ids: [13], account: 'SYN-TARGET', name: '合成去向', score: 62, reasons: ['合成去向原因'], tags: [] }] } };
}
async function opened(h, data = result()) { h.network(async () => data); await h.open(); }
const cells = row => row.querySelectorAll('td');
const picked = card => card.getAttribute('aria-pressed') === 'true' || card.classList.contains('picked');
const sourceCard = h => h.cards().find(card => (card.dataset.ids || '').split(',').includes('11'));

test('context rows retain eight full columns, exact money, relative direction, attribution and tracked membership', async () => {
  const h = harness(), data = result(); data.rows[0].summary = '<img src=x onerror="SYN"> & "合成"';
  data.rows[0].payer_name = '<b>合成来款方</b>'; await opened(h, data);
  const rows = h.rows(); assert.equal(rows.length, 4);
  for (const row of rows) assert.equal(cells(row).length, 8);
  assert.match(cells(rows[0])[1].textContent, /前\s*1/); assert.match(cells(rows[1])[1].textContent, /本笔|锚点/);
  assert.match(cells(rows[2])[1].textContent, /后\s*1/);
  assert.match(cells(rows[0])[2].textContent, /转入|收入/); assert.match(cells(rows[0])[2].textContent, /本方/);
  assert.match(cells(rows[2])[2].textContent, /转出|支出/); assert.match(cells(rows[2])[2].textContent, /对手|对方/);
  assert.match(cells(rows[0])[4].textContent, /9,007,199,254,740,993\.07/);
  assert.equal(cells(rows[0])[5].textContent.trim(), '—'); assert.match(cells(rows[2])[5].textContent, /-10,000,000,000,000\.01/);
  assert.equal(cells(rows[0])[7].textContent, data.rows[0].summary); assert.equal(h.element('ctxTbody').innerHTML.includes('<img src=x'), false);
  assert.equal(h.element('ctxTbody').innerHTML.includes('<b>合成来款方</b>'), false);
  assert.equal(h.box(12), null); assert.match(rows[1].textContent, /已上图/); assert.match(h.element('ctxTrackedCount').textContent, /1/);
});

test('manual selection updates its count and CTA while recommendations stay synchronized', async () => {
  const h = harness(); await opened(h);
  assert.equal(h.element('ctxTrack').disabled, true); assert.match(h.element('ctxSelected').textContent, /0/);
  const card = sourceCard(h); assert.ok(card); assert.equal(card.tagName, 'BUTTON');
  await card.click(); assert.equal(h.box(11).checked, true); assert.equal(h.box(13).checked, false);
  assert.equal(picked(card), true); assert.match(h.element('ctxSelected').textContent, /1/); assert.equal(h.element('ctxTrack').disabled, false);
  await h.check(11, false); assert.equal(picked(card), false); assert.equal(h.element('ctxTrack').disabled, true);
  await h.check(11, true); assert.equal(picked(card), true);
  await card.click(); assert.equal(h.box(11).checked, false); assert.equal(h.box(12), null);
  assert.equal(h.element('ctxTrack').disabled, true);
});

test('attribution filtering is local and removes recommendations with no current untracked candidates', async () => {
  const h = harness(); await opened(h); const reads = h.requests.length;
  await h.change('ctxBook', 'self');
  assert.equal(h.requests.length, reads); assert.equal(h.rows().length, 3); assert.equal(h.box(13), null);
  assert.equal(h.cards().some(card => (card.dataset.ids || '').split(',').includes('13')), false);
  await sourceCard(h).click(); assert.equal(h.box(11).checked, true); assert.equal(h.box(14).checked, false);
  await h.change('ctxBook', 'counter');
  assert.equal(h.rows().length, 1); assert.equal(h.box(11), null); assert.equal(sourceCard(h), undefined);
  assert.equal(h.element('ctxTrack').disabled, true);
});

test('GET failure clears stale rows and offers an eight-column retry whose empty result keeps tracking disabled', async () => {
  const h = harness(); await opened(h); h.network(async () => { throw new Error('合成读取故障'); }); await h.load();
  assert.equal(h.state.ctx.phase, 'error'); assert.equal(h.box(11), null); assert.equal(h.cards().length, 0);
  assert.equal(h.element('ctxTrack').disabled, true);
  const cell = h.element('ctxTbody').querySelector('td'); assert.ok(cell);
  assert.equal(cell.colSpan || Number(cell.getAttribute('colspan')), 8); assert.match(cell.textContent, /加载失败/);
  const retry = cell.querySelector('button'); assert.ok(retry);
  const empty = result('SYN-RETRY'); empty.rows = []; empty.suggestions = { sources: [], targets: [] };
  h.network(async () => empty); await retry.click(); await flush();
  assert.equal(h.state.ctx.phase, 'ready'); assert.equal(h.element('ctxTrack').disabled, true); assert.match(h.element('ctxPos').textContent, /SYN-RETRY/);
});

test('side and window requests publish only the latest response', async () => {
  const h = harness(); await opened(h); const older = deferred(), newer = deferred(); let reads = 0;
  h.network(() => ++reads === 1 ? older.promise : newer.promise);
  const first = h.side('payer'), second = h.change('ctxX', '9');
  newer.resolve(result('SYN-NEW')); await second; older.resolve(result('SYN-OLD')); await first;
  assert.match(h.element('ctxPos').textContent, /SYN-NEW/); assert.equal(h.element('ctxPos').textContent.includes('SYN-OLD'), false);
  assert.equal(h.state.ctx.x, 9); assert.equal(h.state.ctx.side, 'payer');
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
  }
});

test('tracking locks conditions and selection, deduplicates submission and retains checks on a no-op', async () => {
  const h = harness(); await opened(h); await h.check(11, true); const tracking = deferred(); h.track(() => tracking.promise);
  const first = h.submit(), duplicate = h.run("$('#ctxTrack').onclick()"); await flush();
  assert.equal(h.trackCalls.length, 1); assert.deepEqual(h.trackCalls[0], [11]); assert.equal(h.state.ctx.phase, 'tracking');
  for (const id of ['ctxX', 'ctxBook', 'ctxSide-payee', 'ctxSide-payer', 'ctxTrack']) assert.equal(h.element(id).disabled, true, id);
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
  const current = h.state.ctx; tracking.resolve(true); await pending;
  assert.equal(h.state.ctx, current); assert.equal(h.element('ctxModal').style.display, 'flex'); assert.equal(h.element('ctxTrack').disabled, true);
  assert.equal(h.box(11).checked, false); assert.equal(h.state.ctx.phase, 'ready');
});
