import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/table-workspace.js', import.meta.url), 'utf8');
const app = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
const section = (start, end) => app.slice(app.indexOf(start), app.indexOf(end, app.indexOf(start)));
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const flow = id => ({ id, trade_time: '2026-01-01 12:00:00', payer_name: 'Synthetic payer',
  payer_account: 'SYN-00000000000000000001', payee_name: 'Synthetic payee', payee_account: 'SYN-00000000000000000002',
  amount: '9999999999999999.99', currency: 'CNY', summary: 'Synthetic transaction' });

// A small DOM with real ancestry, moved-node identity and capture/bubble order.
// The frozen app's actual renderer, search parameters and table handlers run in
// this harness; only HTML parsing and network/graph boundaries are synthetic.
function harness() {
  const byId = new Map(), ready = [], requests = [], graphCalls = [], membershipCalls = [];
  const membershipListeners = new Set();
  let doc, context, membershipToken = 0;
  let status = { confirmedIds: [], desiredIds: [], pendingIds: [], failures: {}, available: true, busy: false };
  let fetchFlows = async () => ({ page: 1, size: 50, total: 2, rows: [flow(1), flow(2)] });
  let graphRead = async () => {};
  const matches = (node, selector) => {
    if (selector === '[inert]') return node.inert;
    if (selector.startsWith('#')) return node.id === selector.slice(1);
    if (selector.startsWith('.')) return node.classList.contains(selector.slice(1));
    if (selector === 'tr[data-id]') return node.tagName === 'TR' && node.dataset.id !== undefined;
    return node.tagName === selector.toUpperCase();
  };
  class Element {
    constructor(tag = 'div', id = '') {
      this.tagName = tag.toUpperCase(); this.id = id; this.children = []; this.parentElement = null;
      this.dataset = {}; this.attributes = new Map(); this.listeners = new Map(); this.style = {};
      this.value = ''; this.checked = false; this.disabled = false; this.inert = false; this.indeterminate = false;
      this.scrollLeft = this.scrollTop = 0; this.textContent = ''; this.title = ''; this.tabIndex = 0;
      const classes = new Set();
      this.classList = { contains: name => classes.has(name), add: name => classes.add(name), remove: name => classes.delete(name),
        toggle: (name, on = !classes.has(name)) => on ? classes.add(name) : classes.delete(name) };
      if (id) byId.set(id, this);
    }
    get className() { return this.attributes.get('class') || ''; }
    set className(value) { this.attributes.set('class', value); for (const name of value.split(/\s+/)) this.classList.add(name); }
    get isConnected() { return this === doc.body || !!this.parentElement?.isConnected; }
    get nextElementSibling() { return this.parentElement?.children[this.parentElement.children.indexOf(this) + 1]; }
    get previousElementSibling() { return this.parentElement?.children[this.parentElement.children.indexOf(this) - 1]; }
    appendChild(child) {
      child.remove(); this.children.push(child); child.parentElement = this; return child;
    }
    remove() {
      if (doc?.activeElement && this.contains(doc.activeElement)) doc.activeElement = doc.body;
      if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1);
      this.parentElement = null;
    }
    before(node) {
      const parent = this.parentElement; node.remove();
      parent.children.splice(parent.children.indexOf(this), 0, node); node.parentElement = parent;
    }
    after(node) {
      const parent = this.parentElement; node.remove();
      parent.children.splice(parent.children.indexOf(this) + 1, 0, node); node.parentElement = parent;
    }
    replaceWith(node) { this.before(node); this.remove(); }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    matches(selector) { return matches(this, selector); }
    closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
    querySelectorAll(selector) {
      if (!selector) throw new SyntaxError('Empty CSS selector');
      const all = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
      if (selector === '*') return all;
      const tokens = selector.split(/\s+/), leaf = tokens.pop();
      return all.filter(node => {
        if (!matches(node, leaf)) return false;
        let parent = node.parentElement;
        for (const token of [...tokens].reverse()) {
          while (parent && !matches(parent, token)) parent = parent.parentElement;
          if (!parent) return false;
          parent = parent.parentElement;
        }
        return true;
      });
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    setAttribute(key, value) { this.attributes.set(key, String(value)); }
    getAttribute(key) { return this.attributes.get(key) ?? null; }
    focus(options) { doc.activeElement = this; this.lastFocusOptions = options; }
    addEventListener(type, callback, options = false) {
      const list = this.listeners.get(type) || []; list.push({ callback, capture: options === true || !!options.capture }); this.listeners.set(type, list);
    }
    fire(type, options = {}) {
      const event = { type, target: this, key: '', isComposing: false, defaultPrevented: false, stopped: false, immediate: false,
        preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; },
        stopImmediatePropagation() { this.stopped = this.immediate = true; }, ...options };
      const path = []; for (let node = this; node; node = node.parentElement) path.push(node);
      const invoke = (node, capture) => {
        event.currentTarget = node;
        for (const item of node.listeners.get(type) || []) {
          if (item.capture === capture) item.callback(event);
          if (event.immediate) return;
        }
        if (!capture && !event.immediate) node['on' + type]?.(event);
      };
      for (const node of [...path].reverse()) { invoke(node, true); if (event.stopped) return event; }
      for (const node of path) { invoke(node, false); if (event.stopped) return event; }
      return event;
    }
    click() { this.fire('click'); }
    set innerHTML(value) {
      for (const child of [...this.children]) child.remove();
      if (this.id !== 'flowTbody') return;
      for (const match of value.matchAll(/<tr data-id="([^"]+)"/g)) {
        const tr = this.appendChild(new Element('tr')); tr.dataset.id = match[1];
        const td = tr.appendChild(new Element('td')), box = td.appendChild(new Element('input'));
        box.className = 'flow-ck'; box.dataset.id = match[1];
        const actions = tr.appendChild(new Element('td'));
        if (!context?.state.tracked.has(Number(match[1]))) {
          const track = actions.appendChild(new Element('button')); track.className = 'act-track';
        }
        const details = actions.appendChild(new Element('button')); details.className = 'act-ctx';
      }
    }
  }
  const element = id => byId.get(id);
  doc = { activeElement: null, body: new Element('body'),
    querySelector: selector => doc.body.querySelector(selector), querySelectorAll: selector => doc.body.querySelectorAll(selector),
    createElement: kind => new Element(kind), createComment: () => new Element('#comment') };
  const add = (parent, id, tag = 'div', className = '') => {
    const result = parent.appendChild(new Element(tag, id)); result.className = className; return result;
  };
  const sidebar = add(doc.body, 'left'), tab = add(sidebar, 'tab-search'), query = add(tab, 'queryFields');
  const toggle = add(tab, 'btnQueryToggle', 'button'); toggle.setAttribute('aria-expanded', 'true'); add(toggle, '', 'img');
  add(tab, 'querySummary');
  for (const id of ['fAccount', 'fName', 'fCounter', 'fKw', 'fMin', 'fMax', 'fFrom', 'fTo', 'fTracked']) add(query, id, 'input');
  add(query, 'btnReset', 'button'); add(query, 'btnSearch', 'button');
  add(tab, 'flowCkAll', 'input'); add(tab, 'flowCkCount');
  const tools = add(tab, 'flowTools', 'div', 'flow-table-tools');
  add(tools, 'resultInfo'); add(tools, 'btnFlowOverview', 'button');
  add(tab, 'flowTableHint'); const viewport = add(tab, 'flowTableScroll');
  const table = add(viewport, 'flowTable', 'table'); add(table, 'flowSelectionHeader', 'th'); add(table, 'flowTbody', 'tbody');
  add(tab, 'flowHorizontal', 'div', 'flow-horizontal-scroll');
  const pager = add(tab, 'flowPager', 'div', 'pager');
  add(pager, 'btnPrev', 'button'); add(pager, 'pageInfo'); add(pager, 'btnNext', 'button');
  const overview = add(doc.body, 'flowOverview'); overview.style.display = 'none';
  for (const id of ['flowOverviewContext', 'btnCloseFlowOverview', 'overviewTrackAll', 'overviewMembershipStatus', 'btnOverviewUndo', 'btnOverviewRedo', 'flowOverviewContent']) add(overview, id);
  add(doc.body, 'btnUndo', 'button'); add(doc.body, 'btnRedo', 'button');
  const publish = () => { for (const callback of membershipListeners) callback(status); };
  const state = { caseId: 1, caseGeneration: 1, canvasId: 0, canvasGeneration: 1, canvasName: '', graphLoading: false,
    cases: [{ id: 1, name: 'Synthetic A' }, { id: 2, name: 'Synthetic B' }], tracked: new Set(), search: { page: 1, size: 50, total: 0 } };
  const resetMembership = () => {
    membershipToken++; status = { confirmedIds: [], desiredIds: [], pendingIds: [], failures: {}, available: false, busy: false }; publish();
  };
  const membership = {
    snapshot: () => status,
    subscribe(fn) { membershipListeners.add(fn); fn(status); return () => membershipListeners.delete(fn); },
    set(ids, included) {
      const gate = deferred(), token = membershipToken, desired = new Set(status.desiredIds);
      for (const id of ids.map(String)) included ? desired.add(id) : desired.delete(id);
      const call = { ids: Array.from(ids, String), included, token, gate }; membershipCalls.push(call);
      status = { ...status, desiredIds: [...desired], pendingIds: call.ids, busy: true }; publish();
      return gate.promise.then(result => {
        if (token !== membershipToken) return { ok: false, status: 'stale' };
        if (result?.failed) status = { ...status, desiredIds: [...status.confirmedIds], failures: { [call.ids[0]]: 'Synthetic failure' }, pendingIds: [], busy: false };
        else status = { ...status, confirmedIds: [...desired], desiredIds: [...desired], pendingIds: [], failures: {}, busy: false };
        publish(); return { ok: !result?.failed, status: result?.failed ? 'failed' : 'confirmed' };
      });
    },
  };
  const sandbox = {
    window: { addEventListener(name, fn) { if (name === 'DOMContentLoaded') ready.push(fn); },
      FundsCanvasEditor: { membership, history: { status: () => ({ canUndo: false, canRedo: false, busy: false }) } } },
    document: doc, state, URLSearchParams, console,
    $: selector => doc.querySelector(selector), $$: selector => doc.querySelectorAll(selector),
    fmtCount: String, fmtTime: String, flowTimeCell: String, moneyHtml: String,
    esc: value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;'),
    syncFlowTableScroll() {}, openContext() {}, trackFlows() {},
    selectCase(value) {
      state.caseId = Number(value); state.caseGeneration++; state.canvasId = 0; state.canvasGeneration++;
      state.graphLoading = true; state.tracked = new Set(); resetMembership();
      element('flowTbody').innerHTML = ''; context.syncFlowCkCount();
    },
    activateCanvas(value) { state.canvasId = Number(value); state.canvasGeneration++; state.graphLoading = true; resetMembership(); },
    async loadGraph(_retry = true, binding = sandbox.caseContext()) {
      graphCalls.push(binding); await graphRead(binding);
      if (sandbox.isCurrentCase(binding)) { state.graphLoading = false; status = { ...status, available: true }; publish(); }
    },
    async api(path) { requests.push(path); return fetchFlows(new URL(path, 'http://synthetic.local').searchParams); },
  };
  context = vm.createContext(sandbox);
  vm.runInContext(section('function caseContext()', 'function selectCase('), context);
  vm.runInContext(section('function searchParams(', 'async function doSearch('), context);
  vm.runInContext(section('function partyCell(', 'let syncFlowTableScroll'), context);
  vm.runInContext(section('function renderFlows(', '/* ---------------- 追踪上图'), context);
  // Existing listeners must coexist with the new capture handlers, particularly
  // row Space and checkbox click. This is the frozen source, not a restatement.
  vm.runInContext(section("  $('#flowTbody').addEventListener('click'", '  // 批量追踪'), context);
  vm.runInContext(source, context, { filename: 'table-workspace.js' });
  for (const callback of ready) callback();
  return { context, state, doc, element, requests, graphCalls, membershipCalls,
    rows: () => element('flowTbody').querySelectorAll('tr[data-id]'),
    box: id => element('flowTbody').querySelectorAll('.flow-ck').find(box => box.dataset.id === String(id)),
    search: (...args) => context.doSearch(...args),
    render: values => context.renderFlows(values),
    network(fn) { fetchFlows = fn; }, graph(fn) { graphRead = fn; },
    locator(value) { sandbox.window.FundsGraphLocator = value; },
    setStatus(value) { status = { ...status, ...value }; publish(); },
    open() { element('btnFlowOverview').click(); }, close() { element('btnCloseFlowOverview').click(); },
    toggleAnchor(id, checked) { const box = this.box(id); box.checked = checked; box.fire('change'); },
  };
}

test('query disclosure keeps table tools, results and selection usable and restores internal focus', async () => {
  const f = harness(); await f.search(); f.toggleAnchor(1, true);
  f.element('fAccount').focus(); f.element('btnQueryToggle').click();
  assert.equal(f.element('queryFields').inert, true);
  assert.equal(f.element('btnQueryToggle').getAttribute('aria-expanded'), 'false');
  assert.equal(f.doc.activeElement, f.element('btnQueryToggle'));
  assert.equal(f.element('flowTools').closest('[inert]'), null);
  assert.equal(f.box(1).checked, true); assert.match(f.element('querySummary').textContent, /2 笔/);
  f.open(); f.close(); f.element('btnQueryToggle').click();
  assert.equal(f.element('queryFields').inert, false);
  assert.equal(f.element('btnQueryToggle').getAttribute('aria-expanded'), 'true');
  assert.equal(f.box(1).checked, true);
});

test('renderer refresh and page returns retain independent sidebar anchors with clear initial labels', async () => {
  const f = harness(); await f.search();
  assert.equal(f.box(1).getAttribute('aria-label'), '选为追踪锚点，流水 1');
  f.toggleAnchor(1, true); await f.search();
  assert.equal(f.box(1).checked, true); assert.equal(f.box(2).checked, false);
  assert.equal(f.element('flowCkCount').textContent, '已选锚点 1 笔');
  f.network(async params => ({ page: Number(params.get('page')), size: 2, total: 4,
    rows: params.get('page') === '2' ? [flow(3), flow(4)] : [flow(1), flow(2)] }));
  await f.search(2); f.toggleAnchor(3, true); await f.search(1);
  assert.equal(f.box(1).checked, true); assert.equal(f.element('flowCkAll').indeterminate, true);
  await f.search(2); assert.equal(f.box(3).checked, true); assert.equal(f.box(4).checked, false);
  assert.equal(f.membershipCalls.length, 0);
});

test('overview moves the same table nodes and retains separate scroll positions and selection meanings', async () => {
  const f = harness(); await f.search(); f.toggleAnchor(1, true);
  f.setStatus({ confirmedIds: ['2'], desiredIds: ['2'] });
  const viewport = f.element('flowTableScroll'), table = f.element('flowTable'), row = f.rows()[0];
  viewport.scrollLeft = 83; viewport.scrollTop = 41;
  f.open();
  assert.equal(viewport.parentElement, f.element('flowOverviewContent'));
  assert.equal(f.element('flowTable'), table); assert.equal(f.rows()[0], row);
  assert.equal(viewport.scrollLeft, 0); assert.equal(viewport.scrollTop, 0);
  assert.equal(f.box(1).checked, false); assert.equal(f.box(2).checked, true);
  assert.equal(f.box(1).getAttribute('aria-label'), '加入当前画布，流水 1');
  assert.equal(f.element('flowSelectionHeader').textContent, '上图');
  viewport.scrollLeft = 17; viewport.scrollTop = 200; f.close();
  assert.equal(viewport.parentElement, f.element('tab-search')); assert.equal(f.element('flowTable'), table);
  assert.equal(f.box(1).checked, true); assert.equal(f.box(2).checked, false);
  assert.equal(viewport.scrollLeft, 83); assert.equal(viewport.scrollTop, 41);
  assert.equal(f.doc.activeElement, f.element('btnFlowOverview'));
  f.open(); assert.equal(viewport.scrollLeft, 17); assert.equal(viewport.scrollTop, 200);
});

test('overview checkbox and row Space request membership once while closing leaves the sent operation alive', async () => {
  const f = harness(); await f.search(); f.toggleAnchor(1, true); f.open();
  const row = f.rows()[0]; const event = row.fire('keydown', { key: ' ' });
  assert.equal(event.defaultPrevented, true); assert.equal(f.membershipCalls.length, 1);
  assert.deepEqual(f.membershipCalls[0].ids, ['1']); assert.equal(f.membershipCalls[0].included, true);
  assert.equal(f.box(1).checked, true); assert.match(row.querySelector('.membership-status').textContent, /添加中/);
  assert.equal(f.box(1).disabled, false);
  f.close(); assert.equal(f.membershipCalls.length, 1); assert.equal(f.box(1).checked, true);
  f.membershipCalls[0].gate.resolve(); await flush(); f.open();
  assert.equal(f.box(1).checked, true); assert.equal(f.rows()[0].querySelector('.membership-status').textContent, '已上图');
  const box = f.box(1); box.checked = false; box.fire('click'); box.fire('change');
  assert.equal(f.membershipCalls.length, 2); assert.equal(f.membershipCalls[1].included, false);
  f.membershipCalls[1].gate.resolve(); await flush(); f.close();
  assert.equal(f.box(1).checked, true); assert.equal(f.element('flowCkCount').textContent, '已选锚点 1 笔');
});

test('membership failures roll back the overview row without clearing anchors and page-all uses visible IDs only', async () => {
  const f = harness(); await f.search(); f.toggleAnchor(2, true); f.open();
  f.box(1).checked = true; f.box(1).fire('change');
  f.membershipCalls[0].gate.resolve({ failed: true }); await flush();
  assert.equal(f.box(1).checked, false); assert.equal(f.box(1).disabled, false);
  assert.match(f.rows()[0].querySelector('.membership-status').textContent, /失败/);
  const all = f.element('overviewTrackAll'); all.checked = true; all.fire('change');
  assert.deepEqual(f.membershipCalls[1].ids, ['1', '2']);
  f.membershipCalls[1].gate.resolve(); await flush();
  assert.equal(all.checked, true); assert.equal(all.indeterminate, false);
  assert.match(f.element('overviewMembershipStatus').textContent, /2\/2/);
  f.close(); assert.equal(f.box(1).checked, false); assert.equal(f.box(2).checked, true);
});

test('result refresh restores the focused row control or row without scrolling or stealing external focus', async () => {
  for (const overview of [false, true]) {
    for (const selector of ['.flow-ck', '.act-ctx', '.act-track', null]) {
      const f = harness(); await f.search(); if (overview) f.open();
      const oldRow = f.rows()[0], target = selector ? oldRow.querySelector(selector) : oldRow;
      target.focus(); const viewport = f.element('flowTableScroll'); viewport.scrollLeft = 108; viewport.scrollTop = 91;
      await f.search();
      const newRow = f.rows()[0], expected = selector ? newRow.querySelector(selector) : newRow;
      assert.notEqual(newRow, oldRow); assert.equal(f.doc.activeElement, expected);
      assert.equal(expected.lastFocusOptions.preventScroll, true);
      assert.equal(viewport.scrollLeft, 108); assert.equal(viewport.scrollTop, 91);
      f.element('fAccount').focus(); await f.search();
      assert.equal(f.doc.activeElement, f.element('fAccount'));
    }
  }
});

test('a focused disappearing action falls back to its row and a disappearing row to the table region', async () => {
  for (const overview of [false, true]) {
    const f = harness(); await f.search(); if (overview) f.open();
    f.rows()[0].querySelector('.act-track').focus(); f.state.tracked.add(1); await f.search();
    assert.equal(f.doc.activeElement, f.rows()[0]);
    f.box(1).focus(); f.network(async () => ({ page: 1, size: 50, total: 1, rows: [flow(2)] }));
    await f.search();
    assert.equal(f.doc.activeElement, f.element('flowTableScroll'));
    assert.equal(f.doc.activeElement.lastFocusOptions.preventScroll, true);
    assert.equal(f.element('flowOverview').style.display, overview ? 'flex' : 'none');
  }
});

test('latest query wins, an explicit failure retains last results, and click-entry failures are handled', async () => {
  const f = harness(), first = deferred(), second = deferred(); let index = 0;
  f.network(() => (++index === 1 ? first : second).promise);
  const old = f.search(1), current = f.search(2);
  second.resolve({ page: 2, size: 1, total: 2, rows: [flow(22)] });
  assert.equal(await current, true);
  first.resolve({ page: 1, size: 1, total: 2, rows: [flow(11)] });
  assert.equal(await old, false); assert.deepEqual(f.rows().map(row => row.dataset.id), ['22']);
  assert.equal(f.state.search.page, 2); assert.equal(f.element('flowTableScroll').getAttribute('aria-busy'), 'false');
  f.network(async () => { throw new Error('Synthetic query failure'); });
  assert.equal(await f.search(), false); assert.deepEqual(f.rows().map(row => row.dataset.id), ['22']);
  assert.equal(f.element('resultInfo').textContent, '查询失败 · 保留上次结果');
  f.element('fAccount').fire('keydown', { key: 'Enter' }); await flush();
  assert.equal(f.element('flowTableScroll').getAttribute('aria-busy'), 'false');
});

test('draft summary shows current unsubmitted input and remains a draft if input changes during a request', async () => {
  const f = harness(); f.element('fAccount').value = 'SYN-APPLIED'; await f.search();
  f.element('fAccount').value = 'SYN-DRAFT'; f.element('fAccount').fire('input');
  assert.match(f.element('querySummary').textContent, /待查询 · 账号 SYN-DRAFT/);
  assert.doesNotMatch(f.element('querySummary').textContent, /SYN-APPLIED/);
  const pending = deferred(); f.network(() => pending.promise); const searching = f.search();
  f.element('fAccount').value = 'SYN-LATER'; f.element('fAccount').fire('input');
  pending.resolve({ page: 1, size: 50, total: 1, rows: [flow(1)] }); await searching;
  assert.match(f.element('querySummary').textContent, /待查询 · 账号 SYN-LATER/);
});

test('canvas invalidation clears real rows immediately and graph readiness refreshes one consistent page', async () => {
  const f = harness(); await f.search(); f.toggleAnchor(1, true); f.open();
  const old = deferred(); f.network(() => old.promise); const stale = f.search();
  f.context.activateCanvas(7);
  assert.equal(f.element('flowOverview').style.display, 'none'); assert.equal(f.rows().length, 0);
  assert.equal(f.state.search.total, 0); assert.equal(f.element('flowCkCount').textContent, '已选锚点 0 笔');
  f.open(); assert.equal(f.element('overviewTrackAll').disabled, true); assert.match(f.element('overviewMembershipStatus').textContent, /0\/0/);
  f.close(); f.network(async params => ({ page: 1, size: 50, total: 1, rows: [flow(7)] }));
  const before = f.requests.length; await f.context.loadGraph();
  assert.equal(f.requests.length, before + 1); assert.deepEqual(f.rows().map(row => row.dataset.id), ['7']);
  await f.context.loadGraph(); assert.equal(f.requests.length, before + 1);
  old.resolve({ page: 1, size: 50, total: 1, rows: [flow(99)] }); assert.equal(await stale, false);
  f.open(); assert.equal(f.element('overviewTrackAll').disabled, false); assert.match(f.element('overviewMembershipStatus').textContent, /0\/1/);
});

test('switching a canvas from the sidebar clears anchors even when the same flows appear again', async () => {
  const f = harness(); await f.search(); f.toggleAnchor(1, true);
  f.context.activateCanvas(7); assert.equal(f.rows().length, 0);
  await f.context.loadGraph();
  assert.equal(f.box(1).checked, false); assert.equal(f.box(2).checked, false);
  assert.equal(f.element('flowCkCount').textContent, '已选锚点 0 笔');
});

test('case A-B-A rejects old reads and graph completions without republishing another binding', async () => {
  const f = harness(), oldRead = deferred(), oldGraph = deferred(); await f.search();
  f.network(() => oldRead.promise); const oldQuery = f.search();
  f.context.selectCase(2); f.graph(() => oldGraph.promise); const graphB = f.context.loadGraph();
  f.context.selectCase(1); f.graph(async () => {});
  f.network(async () => ({ page: 1, size: 50, total: 1, rows: [flow(101)] }));
  await f.context.loadGraph(); const count = f.requests.length;
  oldGraph.resolve(); await graphB;
  oldRead.resolve({ page: 1, size: 50, total: 1, rows: [flow(99)] }); assert.equal(await oldQuery, false);
  assert.equal(f.requests.length, count); assert.deepEqual(f.rows().map(row => row.dataset.id), ['101']);
  assert.equal(f.state.caseId, 1); assert.equal(f.element('flowTableScroll').getAttribute('aria-busy'), 'false');
});

test('only explicit query button and non-IME Enter preview frozen account fields, preserving leading zeroes', async () => {
  const f = harness(), previews = [], captures = [];
  f.locator({ capture() { const token = { sequence: captures.length + 1 }; captures.push(token); return token; },
    async previewFields(fields, options) { previews.push(JSON.parse(JSON.stringify({ fields, options }))); } });
  f.element('fAccount').value = ' 00000123 '; f.element('fName').value = '合成户名'; f.element('fCounter').value = '00000456';
  await f.element('btnSearch').onclick();
  assert.deepEqual(previews[0], { fields: { account: '00000123', name: '合成户名', counterparty: '00000456' }, options: { binding: { sequence: 1 } } });
  const requests = f.requests.length;
  f.element('fAccount').fire('keydown', { key: 'Enter', isComposing: true });
  f.element('fAccount').fire('keydown', { key: 'Enter', keyCode: 229 });
  await flush(); assert.equal(f.requests.length, requests); assert.equal(previews.length, 1);
  f.element('fAccount').fire('keydown', { key: 'Enter' }); await flush();
  assert.equal(previews.length, 2); assert.equal(captures.length, 2);
});

test('implicit searches, reset and binding readiness refresh never capture or preview the camera', async () => {
  const f = harness(), calls = [];
  f.locator({ capture() { calls.push('capture'); return {}; }, previewFields() { calls.push('preview'); } });
  f.element('fAccount').value = '00001'; await f.search(1); await f.search(2);
  await f.element('btnReset').onclick();
  f.context.activateCanvas(7); await f.context.loadGraph();
  assert.deepEqual(calls, []); assert.ok(f.requests.length >= 4);
});

test('explicit date, amount and summary-only queries do not capture or interrupt the camera', async () => {
  const f = harness(), calls = [];
  f.locator({ capture() { calls.push('capture'); return {}; }, previewFields() { calls.push('preview'); } });
  f.element('fFrom').value = '2026-10-01'; f.element('fTo').value = '2026-10-18';
  f.element('fMin').value = '0.01'; f.element('fMax').value = '9999999999999999.99'; f.element('fKw').value = '合成摘要';
  await f.element('btnSearch').onclick();
  f.element('fMin').fire('keydown', { key: 'Enter' }); await flush();
  assert.deepEqual(calls, []); assert.equal(f.requests.length, 2);
  assert.equal(f.element('resultInfo').textContent, '共 2 笔');
});

test('input changes during a query revoke its preview even when the original text is restored', async () => {
  const f = harness(), result = deferred(), previews = [];
  f.locator({ capture: () => ({}), previewFields: fields => previews.push(fields) });
  f.element('fAccount').value = '00001'; f.network(() => result.promise);
  const search = f.search(1, { locate: true });
  f.element('fAccount').value = '00002'; f.element('fAccount').fire('input');
  f.element('fAccount').value = '00001'; f.element('fAccount').fire('input');
  result.resolve({ page: 1, size: 50, total: 1, rows: [flow(1)] });
  assert.equal(await search, true); assert.deepEqual(previews, []);
  assert.equal(f.element('resultInfo').textContent, '共 1 笔');
});

test('programmatic draft changes also prevent preview and never substitute the newer input into the old request', async () => {
  const f = harness(), result = deferred(), previews = [];
  f.locator({ capture: () => ({}), previewFields: fields => previews.push(fields) });
  f.element('fAccount').value = '00001'; f.network(() => result.promise);
  const search = f.search(1, { locate: true }); f.element('fAccount').value = '00002';
  result.resolve({ page: 1, size: 50, total: 1, rows: [flow(1)] }); await search;
  assert.deepEqual(previews, []); assert.match(f.requests[0], /account=00001/);
  assert.match(f.element('querySummary').textContent, /待查询 · 账号 00002/);
});

test('only the newest successful explicit query may preview; failures keep existing results', async () => {
  const f = harness(), first = deferred(), second = deferred(), previews = []; let count = 0;
  f.locator({ capture: () => ({}), previewFields: fields => previews.push(JSON.parse(JSON.stringify(fields))) });
  f.network(() => (++count === 1 ? first : second).promise);
  f.element('fAccount').value = '00001'; const old = f.search(1, { locate: true });
  f.element('fAccount').value = '00002'; const latest = f.search(1, { locate: true });
  second.resolve({ page: 1, size: 50, total: 1, rows: [flow(2)] }); assert.equal(await latest, true);
  first.resolve({ page: 1, size: 50, total: 1, rows: [flow(1)] }); assert.equal(await old, false);
  assert.deepEqual(previews.map(fields => fields.account), ['00002']);
  f.network(async () => { throw new Error('Synthetic query failure'); });
  assert.equal(await f.search(1, { locate: true }), false); assert.equal(previews.length, 1);
});

test('switching canvas or case A-B-A rejects a delayed explicit query preview', async () => {
  for (const change of [f => f.context.activateCanvas(7), f => { f.context.selectCase(2); f.context.selectCase(1); }]) {
    const f = harness(), result = deferred(), previews = [];
    f.locator({ capture: () => ({}), previewFields: fields => previews.push(fields) });
    f.element('fAccount').value = '00001'; f.network(() => result.promise);
    const search = f.search(1, { locate: true }); change(f);
    result.resolve({ page: 1, size: 50, total: 1, rows: [flow(1)] });
    assert.equal(await search, false); assert.deepEqual(previews, []);
  }
});

test('missing, throwing and rejected camera adapters cannot turn a successful query into failure', async () => {
  for (const locator of [undefined, { capture() { throw new Error('Synthetic capture failure'); } },
    { capture: () => ({}), async previewFields() { throw new Error('Synthetic preview failure'); } }]) {
    const f = harness(); f.locator(locator); f.element('fAccount').value = '00001';
    assert.equal(await f.search(1, { locate: true }), true);
    assert.equal(f.element('resultInfo').textContent, '共 2 笔'); assert.equal(f.rows().length, 2);
    assert.equal(f.element('flowTableScroll').getAttribute('aria-busy'), 'false');
  }
});
