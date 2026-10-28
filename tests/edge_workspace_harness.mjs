import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const app = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
const workflow = readFileSync(new URL('../static/workflow-ui.js', import.meta.url), 'utf8');
const dialogs = readFileSync(new URL('../static/ui-dialogs.js', import.meta.url), 'utf8');
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
// Rendering, drawer selection and workflow orchestration are real.
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
    getClientRects() { return this.isConnected && !this.hidden ? [this.getBoundingClientRect()] : []; }
  }
  document = { root: new Element('#document'), body: new Element('body'), documentElement: new Element('html'), activeElement: null,
    createElement: tag => new Element(tag), getElementById: id => document.root.querySelector('#' + id),
    querySelector: selector => document.root.querySelector(selector), querySelectorAll: selector => document.root.querySelectorAll(selector),
    addEventListener: (...args) => document.root.addEventListener(...args), removeEventListener: (...args) => document.root.removeEventListener(...args) };
  document.root.appendChild(document.body); document.activeElement = document.body;
  const add = (parent, id, tag = 'div') => { const node = new Element(tag); node.id = id; parent.appendChild(node); return node; };
  const drawer = add(document.body, 'drawer'); drawer.inert = true;
  add(drawer, 'drawerTitle'); add(drawer, 'drawerContext'); add(drawer, 'btnCloseDrawer', 'button'); add(drawer, 'drawerBody');
  const menu = add(drawer, 'edgeMenu'); menu.hidden = true; menu.setAttribute('role', 'menu');
  add(menu, 'edgeRemove', 'button').setAttribute('role', 'menuitem');
  // Node and relationship details share one drawer lifecycle. These controls
  // remain outside replacement content in the real document.
  const nodeMenu = add(drawer, 'nodeMenu'); nodeMenu.hidden = true;
  add(nodeMenu, 'ndRemove', 'button');
  const nodePhotoMenu = add(drawer, 'nodePhotoMenu'); nodePhotoMenu.hidden = true;
  add(nodePhotoMenu, 'ndPhotoRe', 'button'); add(nodePhotoMenu, 'ndPhotoClr', 'button');
  add(document.body, 'nodeConfirm', 'dialog');
  add(document.body, 'photoCropModal').style.display = 'none';
  for (const id of ['graphContainer', 'ctxModal', 'ctxTrack', 'ctxTrackStatus', 'ctxTbody', 'ctxSuggest', 'btnImport', 'btnImport2', 'importModal',
    'impPbar', 'impClose', 'impCancel', 'impFile', 'impRe', 'impCommit', 'impChangeFile', 'impMapping', 'impPreviewScroll', 'impHorizontalPosition',
    'impScrollLeft', 'impScrollRight', 'impPreviewTable', 'pcCircle', 'flowTbody', 'canvasSel', 'flowCkAll', 'caseSelect', 'analysisLink', 'batchList']) add(document.body, id);
  const requests = [], removals = [], contextOpens = [], notices = [];
  let network = async () => { throw new Error('Unexpected request'); }, remover = async () => false, scrollDisposals = 0;
  const state = { caseId: 1, caseName: '合成甲案', caseGeneration: 1, canvasId: 0, canvasGeneration: 1, ctx: null,
    graphView: { revision: 1, selectedEdge: null, selectedAccount: null, edgeItems: new Map() },
    lastGraph: { nodes: [], edges: [] }, focusNodes: new Map(), search: { page: 1 }, cases: [{ id: 1, name: '合成甲案' }, { id: 2, name: '合成乙案' }] };
  state.graphSelection = { ids: new Set(), clear() { this.ids.clear(); }, select(id) { this.ids = new Set([id]); } };
  const window = new Element('#window'); window.innerWidth=1000; window.innerHeight=800; window.appendChild(document.root);
  window.location = { href: 'http://synthetic.invalid/?case_id=1' }; window.history = { replaceState(_state, _title, url) { window.location.href = String(url); } };
  const fetch = async (path, options) => {
    requests.push({ path, options }); const value = await network(path, options);
    return { ok: true, status: 200, json: async () => value, text: async () => JSON.stringify(value) };
  };
  const context = vm.createContext({ state, document, window, exportWorkspace: null, caseWorkspace: null, URL, Set, Map, Event, fetch,
    MutationObserver: class { observe() {} disconnect() {} }, getComputedStyle: node => ({ display: node.style.display || 'block' }),
    $: selector => document.querySelector(selector), $$: selector => document.querySelectorAll(selector),
    api: async (path, options) => (await fetch(path, options)).json(),
    untrackFlows: ids => { removals.push({ ids: [...ids], caseId: state.caseId, canvasId: state.canvasId }); return remover(ids); },
    openContext: async id => { contextOpens.push(id); }, loadContext: async () => {},
    contextIsCurrent: () => false, closeContext() {}, syncContextSelection() {},
    toast: (message, type) => notices.push({ message, type }),
    preferenceStorage: { setItem() {} }, cancelGraphLayout() {}, resetGraphView() {}, newGraphPresentation: () => ({}),
    revokePhotoCrop() {}, syncEdgeTypeControl() {}, syncFlowCkCount() {}, updateHeader() {}, resetBatchView() {},
    scheduleGraphLabels() {}, syncGraphEdgeLabel() {}, nodePhotoUrl: () => '', openPhotoCrop() {},
    loadGraph: async () => {}, renderGraph() {},
    bindTableHorizontalScroll(parts) { for (const node of Object.values(parts)) assert.ok(node); return { sync() {}, destroy() { scrollDisposals += 1; } }; },
    setTimeout: callback => { callback(); return 0; }, clearTimeout() {}, requestAnimationFrame: callback => { callback(); return 0; },
  });
  vm.runInContext(money, context);
  vm.runInContext(readFileSync(new URL('../static/principal-analysis.js', import.meta.url), 'utf8'), context);
  window.FundsFacts = context.FundsFacts;
  vm.runInContext(section('function esc(', 'function toast('), context);
  vm.runInContext(section('let pendingCanvasId = 0;', 'async function loadCases()'), context);
  vm.runInContext(section('function caseContext()', '/* ---------------- 初始化'), context);
  vm.runInContext(section('function edgeKey(', 'function graphEdgeType('), context);
  vm.runInContext(section('function flowObservationIds(', '/* ---------------- 追踪上图'), context);
  // Include the account/crop lifecycle dependencies used by closeDrawer and
  // case revocation exactly once, rather than slicing at an obsolete comment.
  vm.runInContext(section('let _selItem = null;', '/* ---------------- 交易上下文'), context);
  vm.runInContext(section("  $('#btnCloseDrawer').onclick = () => {", '  // 上下文弹窗'), context);
  vm.runInContext(dialogs, context); vm.runInContext(workflow, context); window.fire('DOMContentLoaded');
  const item = model => ({ destroyed: false, getModel: () => model });
  return { state, document, requests, removals, contextOpens, notices, element: id => document.getElementById(id),
    get scrollDisposals() { return scrollDisposals; },
    network(handler) { network = handler; }, remove(handler) { remover = handler; }, run(code) { return vm.runInContext(code, context); },
    async open(model = edge()) { context.fixtureModel = model; context.fixtureItem = item(model); await vm.runInContext('showEdgeDrawer(fixtureModel, fixtureItem)', context); return context.fixtureItem; },
    node(account='SYN-NODE') { context.nodeAccount=account; context.nodeItem=item({ id: account }); return vm.runInContext('showNodeDrawer(nodeAccount,nodeItem)', context); },
    close() { return document.getElementById('btnCloseDrawer').click(); }, selectCase(id) { return vm.runInContext(`selectCase(${id})`, context); },
    rows() { return document.querySelectorAll('#edgeTbody tr[data-flow-id]'); },
    row(id) { return document.querySelector(`#edgeTbody tr[data-flow-id="${id}"]`); },
    more(id=17) { const button=this.row(id)?.querySelector('.edge-more'); assert.ok(button); return button.click(); },
    context(id=17) { const button=this.row(id)?.querySelector('.edge-context'); assert.ok(button); return button.click(); },
    removeClick() { return document.getElementById('edgeRemove').click(); },
  };
}

function edge(name = 'SYN-EDGE', ids = [17, 18]) {
  return { source: name + '-SOURCE', target: name + '-TARGET', ids,
    data: { source: name + '-SOURCE', target: name + '-TARGET', source_name: '合成付款方', target_name: '合成收款方', cnt: 7,
      amount_totals: [{ currency: 'CNY', amount: '9007199254740993.07' }, { currency: 'USD', amount: '12.34' }],
      total: null, currencies: ['CNY', 'USD'], tmin: '2026-10-16 00:00:00', tmax: '2026-10-17 12:34:56' } };
}
const flow = (id, extra = {}) => ({ id, trade_time: '2026-10-17 12:34:56', amount: '9007199254740993.07', currency: 'CNY', summary: '合成摘要', ...extra });
async function opened(h, model = edge(), rows = [flow(17), flow(18, { amount: '12.34', currency: 'USD' })], total = 7) {
  h.network(async () => ({ rows, total })); await h.open(model);
}

test('typed edge parties use their source display numbers while requests retain entity IDs', async () => {
  const h = harness(), model = edge('fte_SYN');
  h.state.lastGraph.nodes = [{ account: model.data.source, display_account: 'SYN-A' },
    { account: model.data.target, display_account: 'SYN-B' }];
  await opened(h, model);
  const parties = h.element('drawerBody').querySelector('.edge-parties').textContent;
  assert.match(parties, /SYN-A/); assert.match(parties, /SYN-B/);
  assert.doesNotMatch(parties, /fte_SYN/);
  assert.equal(new URL(h.requests[0].path, 'http://synthetic.invalid').searchParams.get('ids'), '17,18');
});

test('edge workspace renders four complete columns, exact per-currency totals and returned versus total coverage', async () => {
  const h = harness(), model = edge(), long = 'SYN-' + '1234567890'.repeat(12), hostile = '<img src=x onerror="SYN"> & "合成"';
  model.data.source = long; model.data.target = hostile; model.data.source_name = ''; model.data.target_name = '<b>合成对方</b>';
  await opened(h, model, [flow(17, { summary: hostile }), flow(18, { summary: null, trade_time: null, amount: '12.34', currency: 'USD' })]);
  assert.equal(h.rows().length, 2);
  for (const row of h.rows()) assert.equal(row.querySelectorAll('td').length, 4);
  const text = h.element('drawerBody').textContent, html = h.element('drawerBody').innerHTML;
  assert.equal(text.includes(long), true); assert.equal(text.includes(hostile), true); assert.match(text, /未提供户名/);
  assert.equal(html.includes('<img src=x'), false); assert.equal(html.includes('<b>合成对方</b>'), false);
  assert.match(text, /9,007,199,254,740,993\.07/); assert.match(text, /USD 12\.34/);
  assert.match(h.element('edgeDisplayed').textContent, /2\s*\/\s*7/);
  assert.match(h.row(18).textContent, /—/); assert.match(text, /仅当前图谱/);
  const request = new URL(h.requests[0].path, 'http://synthetic.invalid');
  assert.equal(request.searchParams.get('case_id'), '1'); assert.equal(request.searchParams.get('ids'), '17,18'); assert.equal(request.searchParams.get('size'), '500');
});

test('returned flows display by time then string ID without mutating the source order', async () => {
  const h = harness(), rows = [flow(21, { trade_time: '2026-10-17 13:00:00' }), flow(2, { trade_time: '2026-10-17 12:00:00' }),
    flow(10, { trade_time: '2026-10-17 12:00:00' }), flow(3, { trade_time: '2026-10-16 15:00:00' })];
  const before = JSON.stringify(rows); await opened(h, edge('SYN-TIMELINE', [21, 2, 10, 3]), rows, 4);
  assert.deepEqual(h.rows().map(row => row.dataset.flowId), ['3', '10', '2', '21']);
  assert.equal(JSON.stringify(rows), before, 'ordering the view must preserve the response objects and order');
});

test('each row opens its own context and the more menu supports Escape focus restoration', async () => {
  const h = harness(); await opened(h); await h.context(18); assert.deepEqual(h.contextOpens, [18]);
  const trigger = h.row(17).querySelector('.edge-more'); trigger.focus(); await trigger.click();
  assert.equal(h.element('edgeMenu').hidden, false); assert.equal(h.document.activeElement, h.element('edgeRemove'));
  h.element('edgeRemove').fire('keydown', { key: 'Escape' });
  assert.equal(h.element('edgeMenu').hidden, true); assert.equal(h.document.activeElement, trigger);
  assert.equal(h.element('drawer').classList.contains('open'), true, 'the menu Escape must not bubble into the real drawer dismiss handler');
  trigger.fire('keydown', { key: 'Escape' }); assert.equal(h.element('drawer').classList.contains('open'), false, 'a second Escape reaches the drawer layer');
  assert.equal(h.removals.length, 0);
});

test('scrolling the table or drawer body revokes its row menu and makes the old remove action inert', async () => {
  for (const id of ['edgeTableScroll', 'drawerBody']) {
    const h = harness(); await opened(h); const trigger = h.row(17).querySelector('.edge-more');
    await trigger.click(); assert.equal(h.element('edgeMenu').hidden, false);
    const oldRemove = h.element('edgeRemove'), reads = h.requests.length;
    h.element(id).fire('scroll', { bubbles: false });
    assert.equal(h.element('edgeMenu').hidden, true, id); assert.equal(trigger.getAttribute('aria-expanded'), 'false', id);
    assert.equal(h.run('edgeWorkspace.menu'), null, id);
    await oldRemove.click(); await flush();
    assert.equal(h.removals.length, 0, id); assert.equal(h.requests.length, reads, id);
  }
});

test('read failures expose a four-column retry and a successful empty result is explicit', async () => {
  const h = harness(); h.network(async () => { throw new Error('合成读取故障'); }); await h.open();
  const retry = h.element('edgeTbody').querySelector('.edge-retry'); assert.ok(retry); assert.equal(retry.disabled, false);
  assert.equal(Number(retry.closest('td').getAttribute('colspan')), 4); assert.match(h.element('edgeStatus').textContent, /合成读取故障/);
  h.network(async () => ({ rows: [], total: 0 })); await retry.click(); await flush();
  assert.equal(h.rows().length, 0); assert.match(h.element('edgeTbody').textContent, /暂无/); assert.match(h.element('edgeDisplayed').textContent, /0\s*\/\s*0/);
});

test('only the latest read of the same edge can publish its rows', async () => {
  const h = harness(), older = deferred(), newer = deferred(); let reads = 0;
  h.network(() => ++reads === 1 ? older.promise : newer.promise);
  const first = h.open(), second = h.run('loadEdgeWorkspace()');
  newer.resolve({ rows: [flow(18)], total: 1 }); await second; older.resolve({ rows: [flow(17)], total: 2 }); await first;
  assert.deepEqual(h.rows().map(row => row.dataset.flowId), ['18']); assert.match(h.element('edgeDisplayed').textContent, /1\s*\/\s*1/);
});

test('close, another edge or node, A-B-A, canvas and revision changes revoke late edge reads', async () => {
  for (const navigation of ['close', 'edge', 'node', 'case', 'canvas', 'revision']) {
    const h = harness(), older = deferred(); let flowReads = 0;
    h.network(path => path.startsWith('/api/flows?') ? ++flowReads === 1 ? older.promise : Promise.resolve({ rows: [flow(18)], total: 1 })
      : path.includes('/profile?') ? Promise.resolve({ name: '合成节点', top_counterparties: [] }) : Promise.resolve([]));
    const first = h.open(edge('SYN-OLD'));
    if (navigation === 'close') await h.close();
    else if (navigation === 'node') await h.node();
    else {
      if (navigation === 'case') { h.selectCase(2); h.selectCase(1); }
      if (navigation === 'canvas') { h.state.canvasId = 9; h.state.canvasGeneration += 1; }
      if (navigation === 'revision') h.state.graphView.revision += 1;
      await h.open(edge('SYN-NEW', [18]));
    }
    const body = h.element('drawerBody').innerHTML, title = h.element('drawerTitle').textContent;
    older.resolve({ rows: [flow(17)], total: 2 }); await first;
    assert.equal(h.element('drawerBody').innerHTML, body, navigation); assert.equal(h.element('drawerTitle').textContent, title, navigation);
    if (navigation === 'close') assert.equal(h.element('drawer').classList.contains('open'), false);
  }
});

test('closing the workspace preserves graph selection and the selected edge binding', async () => {
  const h = harness(); await opened(h); h.state.graphSelection.ids.add('SYN-PRESERVED');
  const item = h.run('_selItem'), key = h.state.graphView.selectedEdge, account = h.state.graphView.selectedAccount;
  await h.close();
  assert.equal(h.element('drawer').classList.contains('open'), false); assert.equal(h.element('drawer').inert, true);
  assert.deepEqual([...h.state.graphSelection.ids], ['SYN-PRESERVED']); assert.equal(h.run('_selItem'), item);
  assert.equal(h.state.graphView.selectedEdge, key); assert.equal(h.state.graphView.selectedAccount, account);
  assert.equal(h.document.activeElement, h.element('graphContainer')); assert.ok(h.scrollDisposals >= 1);
});

test('pending removal locks row actions, deduplicates commands and a false result preserves the facts', async () => {
  const h = harness(), rows = [flow(17), flow(18)]; await opened(h, edge(), rows); const before = JSON.stringify(rows), pending = deferred();
  h.remove(() => pending.promise); await h.more(17); await h.removeClick(); await flush(); await h.run('removeEdgeFlow()');
  assert.equal(h.removals.length, 1); assert.deepEqual(h.removals[0], { ids: [17], caseId: 1, canvasId: 0 });
  for (const button of h.element('edgeTbody').querySelectorAll('button')) assert.equal(button.disabled, true);
  assert.equal(h.element('edgeRemove').disabled, true); assert.equal(h.element('drawer').getAttribute('aria-busy'), 'true');
  pending.resolve(false); await flush();
  assert.deepEqual(h.rows().map(row => row.dataset.flowId), ['17', '18']); assert.equal(JSON.stringify(rows), before);
  assert.match(h.element('edgeStatus').textContent, /未移出|保留/); assert.equal(h.row(17).querySelector('.edge-more').disabled, false);
  assert.equal(h.notices.some(notice => /成功|已移除/.test(notice.message)), false);
});

test('a rejected removal keeps rows and reports failure without mutating transaction facts', async () => {
  const h = harness(), rows = [flow(17)]; await opened(h, edge(), rows); const before = JSON.stringify(rows);
  h.remove(async () => { throw new Error('合成移出故障'); }); await h.more(); await h.removeClick(); await flush();
  assert.equal(h.rows().length, 1); assert.equal(JSON.stringify(rows), before); assert.match(h.element('edgeStatus').textContent, /合成移出故障/);
  assert.equal(h.row(17).querySelector('.edge-context').disabled, false); assert.equal(h.notices.some(notice => /成功|已移除/.test(notice.message)), false);
});

test('successful controlled removal may close the drawer and never republishes its disposed workspace', async () => {
  const h = harness(); await opened(h); const reads = h.requests.length;
  h.remove(async () => { h.run('closeDrawer()'); return true; });
  await h.more(); await h.removeClick(); await flush();
  assert.equal(h.removals.length, 1); assert.equal(h.element('drawer').classList.contains('open'), false);
  assert.equal(h.element('drawerBody').innerHTML, ''); assert.equal(h.requests.length, reads);
});

test('a late removal receipt cannot alter a newly opened edge', async () => {
  const h = harness(); await opened(h); const pending = deferred(); h.remove(() => pending.promise);
  await h.more(); await h.removeClick(); await flush();
  await opened(h, edge('SYN-NEXT', [18]), [flow(18)], 1); const body = h.element('drawerBody').innerHTML;
  pending.resolve(true); await flush();
  assert.equal(h.element('drawerBody').innerHTML, body); assert.equal(h.element('drawer').classList.contains('open'), true);
  assert.deepEqual(h.rows().map(row => row.dataset.flowId), ['18']);
});

for (const exact of [true, false]) test(`edge facts display ${exact ? 'one confirmed event' : 'an unresolved range'} while retaining ledger rows`, async () => {
  const h = harness(), model = edge();
  model.data.funds = { status: exact ? 'exact' : 'bounded', count: { lower: exact ? 1 : 0, upper: exact ? 1 : 2 },
    totals: [{ currency: 'CNY', amount: exact ? '100.00' : null, lower: exact ? '100.00' : '0.00', upper: exact ? '100.00' : '200.00' }] };
  model.data.cnt = exact ? 1 : null; model.data.total = exact ? '100.00' : null;
  model.data.amount_totals = [{ currency: 'CNY', amount: '200.00' }];
  await opened(h, model, [flow(17, {amount:'100.00'}), flow(18, {amount:'100.00'})], 2);
  const facts = h.document.querySelector('.edge-facts').textContent;
  assert.match(facts, exact ? /1 笔.*100\.00/s : /已追踪—.*0\.00–200\.00（待核）/s);
  if (exact) assert.doesNotMatch(facts, /200\.00/);
  assert.equal(h.rows().length, 2); assert.equal(h.requests.length, 1);
});

test('removing a confirmed edge row removes every selected observation and context keeps its representative', async () => {
  const h = harness();
  await opened(h, edge(), [flow(17, {observation_ids:['17','18'],granularity:'confirmed_event'})], 1);
  await h.context(17); assert.deepEqual(h.contextOpens, [17]);
  await h.more(17); await h.removeClick(); await flush();
  assert.deepEqual(h.removals.map(call => call.ids.map(String)), [['17','18']]);
  assert.equal(h.requests.length, 1);
});

test('a filtered confirmed edge row acts only on the observation returned in its selection', async () => {
  const h = harness();
  await opened(h, edge('SYN-SELECTED', [18]), [flow(18, {observation_ids:['18'],granularity:'confirmed_event'})], 1);
  await h.more(18); await h.removeClick(); await flush();
  assert.deepEqual(h.removals.map(call => call.ids.map(String)), [['18']]);
});
