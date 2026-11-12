import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/table-workspace.js', import.meta.url), 'utf8');
const app = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
const entitySearch = readFileSync(new URL('../static/entity-search.js', import.meta.url), 'utf8');
const editor = readFileSync(new URL('../static/canvas-editor.js', import.meta.url), 'utf8');
const money = readFileSync(new URL('../static/money.js', import.meta.url), 'utf8');
const facts = readFileSync(new URL('../static/principal-analysis.js', import.meta.url), 'utf8');
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

const eventRow = (id=17, observation_ids=['17','18']) => ({...flow(id),observation_ids,granularity:'confirmed_event'});

function assertQuietResult(f, total) {
  assert.equal(f.state.search.total, total, 'pagination retains the authoritative total');
  assert.equal(f.element('resultInfo').hidden, true, 'a successful result has no count or empty status slot');
  assert.equal(f.element('resultInfo').textContent, '');
  assert.equal(f.element('resultInfo').title, '');
}

// A small DOM with real ancestry, moved-node identity and capture/bubble order.
// The frozen app's actual renderer, search parameters and table handlers run in
// this harness; only HTML parsing and network/graph boundaries are synthetic.
function harness({ entityInputs = false, geometry = false, columnStorage = null, resizeObserver = false, fontMeasure = false } = {}) {
  const byId = new Map(), ready = [], requests = [], graphCalls = [], membershipCalls = [], contextCalls = [], writes = [], windowListeners = new Map();
  const heights = new Map(), widths = new Map(), frames = [];
  const clipboardWrites = [];
  let tableViewportWidth = 360;
  const resizeCallbacks = [];
  const baselineWidths = [40, 86, 140, 140, 96, 126, 92];
  function measuredWidths() {
    const viewport = byId.get('flowTableScroll'), table = byId.get('flowTable');
    const mode = viewport?.dataset.flowWrap === 'single' ? 'single' : 'wrap';
    const natural = baselineWidths.map((width, index) => Math.max(width, ...(table?.querySelectorAll('tr[data-id]') || []).map(row => widths.get(row.dataset.id)?.[mode]?.[index] || 0)));
    const cols = table?.querySelector('colgroup')?.children || [];
    return natural.map((width, index) => Math.max(width, parseFloat(cols[index]?.style.width) || 0));
  }
  const membershipListeners = new Set();
  let doc, context, membershipToken = 0;
  let status = { confirmedIds: [], desiredIds: [], pendingIds: [], failures: {}, available: true, busy: false };
  let fetchFlows = async () => ({ page: 1, size: 50, total: 2, rows: [flow(1), flow(2)] });
  let graphRead = async () => {};
  const matches = (node, selector) => {
    if (selector.includes(',')) return selector.split(',').some(part => matches(node, part.trim()));
    if (selector === '[inert]') return node.inert;
    if (selector.startsWith('#')) return node.id === selector.slice(1);
    if (selector.startsWith('.')) return node.classList.contains(selector.slice(1));
    if (selector === 'tr[data-id]') return node.tagName === 'TR' && node.dataset.id !== undefined;
    return node.tagName === selector.toUpperCase();
  };
  class Element {
    constructor(tag = 'div', id = '') {
      this.tagName = tag.toUpperCase(); this.id = id; this.children = []; this.parentElement = null; this.ownerDocument = doc;
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
    get firstChild() { return this.children[0] || null; }
    get scrollLeft() { return this._scrollLeft || 0; }
    set scrollLeft(value) {
      this._scrollLeft = geometry && this.id === 'flowTableScroll' ? Math.max(0, Math.min(Number(value) || 0, this.scrollWidth - this.clientWidth)) : value;
    }
    get clientWidth() { return geometry && this.id === 'flowTableScroll' ? tableViewportWidth : 0; }
    get scrollWidth() { return geometry && this.id === 'flowTableScroll' ? measuredWidths().reduce((sum, width) => sum + width, 0) : 0; }
    get clientHeight() { return geometry && this.id === 'flowTableScroll' ? 120 : 0; }
    get scrollHeight() { return geometry && this.id === 'flowTableScroll' ? this.querySelectorAll('tr[data-id]').reduce((sum, row) => sum + (heights.get(row.dataset.id) || 40), 0) : 0; }
    getBoundingClientRect() {
      if (!geometry) return undefined;
      if (this.id === 'flowTableScroll') return { top: 0, bottom: 120, height: 120 };
      if (this.tagName === 'TH') {
        const index = this.parentElement.children.indexOf(this);
        return { top: 0, bottom: 0, height: 0, width: measuredWidths()[index] };
      }
      if (this.tagName === 'TR' && this.dataset.id) {
        const siblings = this.parentElement.children, viewport = byId.get('flowTableScroll');
        const top = siblings.slice(0, siblings.indexOf(this)).reduce((sum, row) => sum + (heights.get(row.dataset.id) || 40), 0) - viewport.scrollTop;
        const height = heights.get(this.dataset.id) || 40;
        return { top, bottom: top + height, height };
      }
      return { top: 0, bottom: 0, height: 0 };
    }
    appendChild(child) {
      child.remove(); this.children.push(child); child.parentElement = this; return child;
    }
    append(...nodes) { nodes.forEach(node => this.appendChild(node)); }
    insertBefore(child, reference) {
      if (!reference) return this.appendChild(child);
      child.remove(); this.children.splice(this.children.indexOf(reference), 0, child); child.parentElement = this; return child;
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
    removeAttribute(key) { this.attributes.delete(key); }
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
      for (const match of value.matchAll(/<tr data-id="([^"]+)"([^>]*)>([\s\S]*?)<\/tr>/g)) {
        const tr = this.appendChild(new Element('tr')); tr.dataset.id = match[1];
        const members = match[2].match(/data-observation-ids="([^"]*)"/)?.[1];
        if (members !== undefined) tr.dataset.observationIds = members;
        const td = tr.appendChild(new Element('td')), box = td.appendChild(new Element('input'));
        box.className = 'flow-ck'; box.dataset.id = match[1];
        const checkboxMembers = match[3].match(/class="flow-ck"[^>]*data-observation-ids="([^"]*)"/)?.[1];
        if (checkboxMembers !== undefined) box.dataset.observationIds = checkboxMembers;
        const actions = tr.appendChild(new Element('td'));
        if (match[3].includes('act-track')) {
          const track = actions.appendChild(new Element('button')); track.className = 'act-track';
        }
        const details = actions.appendChild(new Element('button')); details.className = 'act-ctx';
      }
      if (geometry) { const viewport = byId.get('flowTableScroll'); viewport.scrollLeft = viewport.scrollLeft; }
    }
  }
  const element = id => byId.get(id);
  doc = { activeElement: null, body: new Element('body'),
    querySelector: selector => doc.body.querySelector(selector), querySelectorAll: selector => doc.body.querySelectorAll(selector),
    createElement: kind => {
      const node = new Element(kind);
      if (fontMeasure && kind === 'canvas') node.getContext = () => ({font:'',measureText(value){return {width:value.length*(this.font.includes('BodyFont')?9:4)};}});
      return node;
    }, createComment: () => new Element('#comment') };
  const add = (parent, id, tag = 'div', className = '') => {
    const result = parent.appendChild(new Element(tag, id)); result.className = className; return result;
  };
  const sidebar = add(doc.body, 'left'), tab = add(sidebar, 'tab-search'), query = add(tab, 'queryFields');
  const toggle = add(tab, 'btnQueryToggle', 'button'); toggle.setAttribute('aria-expanded', 'true'); add(toggle, '', 'img');
  for (const id of ['fAccount', 'fName', 'fCounter', 'fKw', 'fMin', 'fMax', 'fFrom', 'fTo', 'fTracked']) add(query, id, 'input');
  add(query, 'btnReset', 'button'); add(query, 'btnSearch', 'button');
  add(tab, 'flowCkAll', 'input'); add(tab, 'flowCkCount');
  const tools = add(tab, 'flowTools', 'div', 'flow-table-tools');
  add(tools, 'resultInfo'); add(tools, 'btnFlowOverview', 'button'); add(tools,'btnFlowColumnsReset','button');
  const density = add(tools, 'flowTableDensity', 'select'), wrap = add(tools, 'flowTableWrap', 'select');
  const viewport = add(tab, 'flowTableScroll');
  viewport.dataset.flowDensity = density.value = 'compact'; viewport.dataset.flowWrap = wrap.value = 'wrap';
  density.onchange = () => { viewport.dataset.flowDensity = density.value; viewport.scrollLeft = viewport.scrollLeft; };
  wrap.onchange = () => { viewport.dataset.flowWrap = wrap.value; viewport.scrollLeft = viewport.scrollLeft; };
  const table = add(viewport, 'flowTable', 'table'), head = add(table, 'flowTableHead', 'thead'), heading = add(head, 'flowTableHeading', 'tr');
  const horizontal = add(tab, 'flowHorizontalScroll', 'div', 'flow-horizontal-scroll');
  add(horizontal, 'flowScrollLeft', 'button'); add(horizontal, 'flowHorizontalPosition', 'input'); add(horizontal, 'flowScrollRight', 'button');
  add(heading, 'flowSelectionHeader', 'th'); for (let column = 1; column < 7; column++) add(heading, 'flowHeader' + column, 'th');
  add(table, 'flowTbody', 'tbody');
  const overview = add(doc.body, 'flowOverview'); overview.style.display = 'none';
  for (const id of ['flowOverviewContext', 'btnCloseFlowOverview', 'overviewTrackAll', 'overviewMembershipStatus', 'btnOverviewUndo', 'btnOverviewRedo', 'flowOverviewContent']) add(overview, id);
  add(doc.body, 'btnUndo', 'button'); add(doc.body, 'btnRedo', 'button');
  add(doc.body, 'btnBulkTrack', 'button'); add(doc.body, 'bulkDir', 'input').value = 'source';
  add(doc.body, 'bulkX', 'input').value = '5';
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
    ...(resizeObserver ? { ResizeObserver: class { constructor(callback) { resizeCallbacks.push(callback); } observe() {} } } : {}),
    window: { addEventListener(name, fn) { if (name === 'DOMContentLoaded') ready.push(fn); else { if(!windowListeners.has(name))windowListeners.set(name,[]);windowListeners.get(name).push(fn); } },
      ...(fontMeasure ? {getComputedStyle:()=>({font:'',fontStyle:'normal',fontWeight:'400',fontSize:'12px',fontFamily:'BodyFont',paddingLeft:'10px',paddingRight:'10px',borderLeftWidth:'0px',borderRightWidth:'0px'})} : {}),
      localStorage: columnStorage,
      FundsCanvasEditor: { membership, history: { status: () => ({ canUndo: false, canRedo: false, busy: false }) } } },
    document: doc, state, URLSearchParams, console, setTimeout, clearTimeout, innerWidth:1000, innerHeight:700,
    navigator: { clipboard: { writeText: async value => { clipboardWrites.push(value); } } },
    requestAnimationFrame(callback) { frames.push(callback); return frames.length; },
    $: selector => doc.querySelector(selector), $$: selector => doc.querySelectorAll(selector),
    fmtCount: String, fmtTime: String, flowTimeCell: String, moneyHtml: String,
    esc: value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;'),
    syncFlowTableScroll() {}, openContext(value) { contextCalls.push(value); }, trackFlows() {},
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
  vm.runInContext(money, context); vm.runInContext(facts, context);
  sandbox.window.FundsFacts = context.FundsFacts;
  const pickerOptions = new Map(), pickerSelections = new Map(), invalidated = [];
  if (entityInputs) {
    sandbox.window.FundsEntityPicker = { create(options) {
      pickerOptions.set(options.input.id, options);
      return { invalidate() { invalidated.push(options.input.id); pickerSelections.delete(options.input.id); }, close() {}, open() {}, getSelected() { return pickerSelections.get(options.input.id) || null; } };
    } };
    vm.runInContext(entitySearch, context, { filename: 'entity-search.js' });
  }
  vm.runInContext(section('function caseContext()', 'function selectCase('), context);
  vm.runInContext(section('function searchParams(', 'async function doSearch('), context);
  vm.runInContext(section('function partyCell(', 'let syncFlowTableScroll'), context);
  vm.runInContext(section('function renderFlows(', '/* ---------------- 追踪上图'), context);
  // Existing listeners must coexist with the new capture handlers, particularly
  // row Space and checkbox click. This is the frozen source, not a restatement.
  vm.runInContext(section("  $('#flowTbody').addEventListener('click'", '  // 批量追踪'), context);
  vm.runInContext(source, context, { filename: 'table-workspace.js' });
  for (const callback of ready) callback();
  return { context, state, doc, element, requests, graphCalls, membershipCalls, contextCalls, writes, pickerOptions, invalidated, heights, widths, clipboardWrites,
    installEditorCommands() {
      const originalApi = sandbox.api;
      vm.runInContext(section('function newGraphView(', 'function resetGraphView('), context);
      state.graphView = sandbox.newGraphView('auto');
      Object.assign(sandbox, {
        toast() {}, closeDrawer() {}, jsonOpts: value => ({body:JSON.stringify(value)}),
        command: async (_label, action) => { await action(sandbox.caseContext()); return true; },
        mutateTracked: async (_binding, write, prepare) => { const value = await write([...state.tracked]); prepare?.(value); return value; },
        trackedWrite: (_binding, write) => write(),
        replaceTracked: async (binding, expected, desired) => {
          writes.push({path:'/api/track/state', caseId:binding.caseId, canvasId:binding.canvasId, expected:[...expected], desired:[...desired]});
          return {tracked_ids:desired};
        },
        order: (a,b) => a < b ? -1 : a > b ? 1 : 0,
        api: async (path, options) => {
          if (path !== '/api/track/bulk') return originalApi(path, options);
          const body = JSON.parse(options.body); writes.push({path,...body});
          return {tracked_ids:body.flow_ids, anchors:body.flow_ids.length, tracked:body.flow_ids.length};
        },
      });
      const traceStart = editor.indexOf('  function traceAnchors(');
      const traceEnd = editor.indexOf('  window.FundsCanvasEditor =', traceStart);
      assert.ok(traceStart >= 0 && traceEnd > traceStart);
      vm.runInContext(editor.slice(traceStart, traceEnd), context);
      vm.runInContext(editor.slice(editor.indexOf('  function track(ids, remove)'), editor.indexOf('  const neighborhood =')), context);
      const bulkStart = editor.indexOf("    $('#btnBulkTrack').onclick = () => {");
      const bulkEnd = editor.indexOf("    $('#btnClearGraph').onclick =", bulkStart);
      assert.ok(bulkStart >= 0 && bulkEnd > bulkStart);
      vm.runInContext(editor.slice(bulkStart, bulkEnd), context);
    },
    drafts: sandbox.window.FundsTableDrafts,
    drainFrames() { const pending = frames.splice(0); for (const callback of pending) callback(); },
    chooseCandidate(id, item) {
      const field = element(id); field.value = item.value; field.fire('input');
      pickerSelections.set(id, item); pickerOptions.get(id).onSelect(item);
    },
    view(view, relayout = () => {}) {
      for (const phase of ['prepare', 'start', 'end']) {
        if (phase === 'end') relayout();
        for (const fn of windowListeners.get('fundstrace:workspace-resize') || []) fn({detail:{reason:'workspace-view',phase,view}});
      }
    },
    resizeTable(width) { tableViewportWidth = width; for (const callback of resizeCallbacks) callback(); },
    windowKey(event) { for(const fn of windowListeners.get('keydown')||[])fn({preventDefault(){},stopImmediatePropagation(){},...event}); },
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

test('single-click selects and locates without tracking; double-click and Enter open exact string IDs', async () => {
  const f=harness(), located=[],options=[], flowId='9007199254740993';
  f.network(async()=>({page:1,size:50,total:1,rows:[flow(flowId)]}));
  f.locator({capture:()=>({rowBinding:1}),availability:()=>({visible:true}),previewFields:(value,detail)=>{located.push(value);options.push(detail)}});
  await f.search();const row=f.rows()[0];
  row.click();
  assert.equal(row.getAttribute('aria-current'),'true');assert.deepEqual(f.contextCalls,[]);
  assert.equal(located.length,1);assert.equal(located[0].exactAccount,flow(flowId).payer_account);
  assert.deepEqual(JSON.parse(JSON.stringify(options[0])),{binding:{rowBinding:1},flowId,quiet:true});
  assert.equal(f.box(flowId).checked,false);assert.equal(f.membershipCalls.length,0);assert.equal(f.writes.length,0);
  row.fire('keydown',{key:' ',ctrlKey:true});row.fire('keydown',{key:'Enter',metaKey:true});
  assert.equal(f.box(flowId).checked,false);assert.deepEqual(f.contextCalls,[],'native modifier combinations are not row commands');
  row.fire('click',{detail:2});row.fire('dblclick');
  assert.deepEqual(f.contextCalls,[flowId]);assert.equal(located.length,1);
  row.fire('keydown',{key:'Enter'});assert.deepEqual(f.contextCalls,[flowId,flowId]);
  f.open();row.click();assert.equal(located.length,1,'overview does not pan the canvas hidden by its modal');
  f.context.selectCase(2);row.click();assert.equal(located.length,1,'a removed old-case row cannot locate into a new case');
});

test('column resizing has minima, cancellation, keyboard control and separate saved sidebar and overview widths', async () => {
  const saved=new Map(),storage={getItem:key=>saved.get(key)||null,setItem:(key,value)=>saved.set(key,value)};
  const f=harness({columnStorage:storage});await f.search();
  f.element('flowTableWrap').value='two-line';f.element('flowTableWrap').fire('change');f.drainFrames();
  const table=f.element('flowTable'),headers=table.querySelectorAll('thead th');
  headers.forEach((header,index)=>{header.getBoundingClientRect=()=>({width:parseFloat(table.querySelector('colgroup').children[index].style.width)||140})});
  const handle=headers[2].querySelector('.flow-column-resize');
  handle.fire('keydown',{key:'Home'});assert.equal(table.querySelector('colgroup').children[2].style.width,'112px');
  handle.fire('keydown',{key:'ArrowRight'});assert.equal(table.querySelector('colgroup').children[2].style.width,'120px');
  handle.fire('pointerdown',{button:0,clientX:300,pointerId:1});handle.fire('pointermove',{clientX:380});
  assert.equal(table.querySelector('colgroup').children[2].style.width,'200px');
  handle.fire('pointercancel');assert.equal(table.querySelector('colgroup').children[2].style.width,'120px');
  handle.fire('pointerdown',{button:0,clientX:300,pointerId:1});handle.fire('pointermove',{clientX:380});handle.fire('pointerup');
  const preferences=JSON.parse(saved.get('funds_flow_columns_v1'));
  assert.equal(preferences['sidebar:compact:two-line'][2],200);
  assert.ok(Object.values(preferences).every(widths=>widths.length===7&&widths.every(Number.isFinite)));
  f.open();assert.notEqual(table.querySelector('colgroup').children[2].style.width,'200px');
  f.close();assert.equal(table.querySelector('colgroup').children[2].style.width,'200px');
  const g=harness({columnStorage:storage});await g.search();
  g.element('flowTableWrap').value='two-line';g.element('flowTableWrap').fire('change');g.drainFrames();
  assert.equal(g.element('flowTable').querySelector('colgroup').children[2].style.width,'200px');
  f.element('btnFlowColumnsReset').click();
  assert.equal(table.querySelector('colgroup').children[2].style.width,'140px');
  assert.equal(JSON.parse(saved.get('funds_flow_columns_v1'))['sidebar:compact:two-line'],undefined);
  assert.equal(f.membershipCalls.length,0);assert.equal(f.writes.length,0,'resizing never writes graph membership');
});

test('a pending text-mode geometry callback cannot restore sidebar offsets into overview', async () => {
  const f=harness();await f.search();const viewport=f.element('flowTableScroll');viewport.scrollLeft=190;
  f.element('flowTableWrap').value='single';f.element('flowTableWrap').fire('change');
  f.open();assert.equal(viewport.scrollLeft,0);f.drainFrames();
  assert.equal(viewport.scrollLeft,0,'the old sidebar callback does not write the new presentation slot');
  f.close();assert.equal(viewport.scrollLeft,190);
});

test('overflow full-value popover copies original text and clears it on a case change', async () => {
  const f=harness({geometry:true});await f.search();
  const row=f.rows()[0],cell=f.doc.createElement('td'),span=f.doc.createElement('span');
  const full='合成长名称 <literal>\n000000000000000000000000SYN-0001';
  cell.setAttribute('title',full);span.className='party-name';span.textContent=full;cell.appendChild(span);row.insertBefore(cell,row.children[1]);
  Object.defineProperty(span,'scrollWidth',{value:400});Object.defineProperty(span,'clientWidth',{value:60});
  cell.getBoundingClientRect=()=>({left:100,top:200,bottom:240,width:60,height:40});
  const overview=f.element('flowOverview');overview.className='modal';overview.getClientRects=()=>[1];
  f.open();const panel=f.doc.querySelector('#flowValuePopover');
  panel.getBoundingClientRect=()=>({width:280,height:120});span.fire('pointerover');
  await new Promise(resolve=>setTimeout(resolve,330));
  assert.equal(panel.hidden,false);assert.equal(panel.querySelector('pre').textContent,full);
  span.fire('pointerout',{relatedTarget:null});span.fire('pointerover');
  await new Promise(resolve=>setTimeout(resolve,180));assert.equal(panel.hidden,false,'returning to the value cancels a pending hide');
  panel.querySelector('button').click();await flush();assert.deepEqual(f.clipboardWrites,[full]);
  panel.querySelector('button').focus();f.windowKey({key:'Escape'});
  assert.equal(panel.hidden,true);assert.equal(overview.style.display,'flex','Escape dismisses the value before its owning overview');
  assert.equal(f.doc.activeElement===row,true);
  span.fire('pointerover');await new Promise(resolve=>setTimeout(resolve,330));assert.equal(panel.hidden,false);
  f.context.selectCase(2);assert.equal(panel.hidden,true);assert.equal(panel.querySelector('pre').textContent,'');
});

function lifecycleHarness({ canvas = 0 } = {}) {
  const f = harness(), calls = [];
  const batches = f.doc.createElement('div'); batches.id = 'tab-batches'; batches.style.display = 'none'; f.doc.body.appendChild(batches);
  Object.assign(f.context, {
    initWatermark() {}, bindUI() {}, installNeighborhood() {}, installNodeFinder() {}, initGraph() {}, installAnnotationDialog() {},
    async loadCases() { f.context.selectCase(1); }, async refreshStats() {}, async applyPendingFocusEdge() {},
    async refreshCanvasList() { if (canvas && f.state.canvasId !== canvas) f.context.activateCanvas(canvas); },
    async caseRequest() { return { snapshot: { nodes: [], edges: [] } }; },
    applyCanvasSnapshot() { calls.push('snapshot'); }, async loadBatches() {},
    accountDataWrites: new Set(), nodeWrites: new Map(), pcState: { binding: null }, hasNodeDrafts: () => false,
    toast: (...args) => calls.push(args), CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
  });
  f.context.window.dispatchEvent = event => calls.push(event.detail);
  f.context.window.FundsWorkspaceSession = {
    lastCanvas: () => canvas,
    restore() {
      calls.push('restore'); f.element('fKw').value = 'SYN-RESTORED';
      f.drafts.restoreViewport({ rowId: '301', page: 3, offset: 0, scrollLeft: 0 });
    },
  };
  vm.runInContext(section('async function openManagedCase(', 'function installCaseWorkspace('), f.context);
  return { ...f, lifecycleCalls: calls, async start() {
    let start;
    const listen = f.context.window.addEventListener;
    f.context.window.addEventListener = (_name, callback) => { start = callback; };
    vm.runInContext(section("window.addEventListener('DOMContentLoaded', async () => {", 'async function refreshAll('), f.context);
    f.context.window.addEventListener = listen;
    await start();
  } };
}

function canvasLifecycleHarness() {
  const f = lifecycleHarness();
  for (const id of ['canvasSel', 'btnCanvasNew', 'btnCanvasClone', 'btnCanvasSave', 'btnCanvasRemove',
    'btnCanvasRename', 'btnCanvasDel', 'btnCanvasMenuClone', 'btnCanvasActions']) {
    const node = f.doc.createElement(id === 'canvasSel' ? 'select' : 'button'); node.id = id; f.doc.body.appendChild(node);
  }
  const select = f.doc.querySelector('#canvasSel');
  let callbacks;
  const actions = { status: () => ({ pending: false }), blocked: () => false,
    sync: () => callbacks.onState({ pending: false }) };
  Object.assign(f.context, { layoutJob: null, cancelAnnotationDrag: null, operationFeedbackChanged() {},
    async refreshCanvasList() {} });
  f.doc.addEventListener = () => {};
  f.context.window.FundsCanvasWorkspace = { createView: () => ({ onState() {}, attach() {} }),
    create(value) { callbacks = value; return actions; } };
  f.context.window.FundsWorkspaceSession.removeCanvas = () => {};
  vm.runInContext(section('function externalImportPending(', 'let graphRequestSequence'), f.context);
  f.context.bindCanvases();
  return { ...f, changeCanvas(id) {
    select.value = String(id); select.selectedOptions = [{ textContent: 'Synthetic saved canvas' }];
    return select.onchange({ target: select });
  }, publishCanvas(action) {
    return callbacks.publish(action, f.context.caseContext(), { canvas_id: 8, name: 'Synthetic new canvas' }, { nodes: [], edges: [] });
  } };
}

for (const canvas of [0, 7]) test(`canvas selection ${canvas} owns one query after restoring filters and page`, async () => {
  const f = canvasLifecycleHarness();
  f.element('fTracked').checked = true;
  if (!canvas) { f.state.canvasId = 7; f.state.canvasGeneration++; }
  f.network(async params => ({ page: Number(params.get('page')), size: 50, total: 150, rows: [flow(301)] }));
  await f.changeCanvas(canvas);
  assert.equal(f.requests.length, 1);
  const params = new URL(f.requests[0], 'http://synthetic.local').searchParams;
  assert.equal(params.get('kw'), 'SYN-RESTORED'); assert.equal(params.get('page'), '3');
  assert.equal(params.get('canvas_id') || '0', String(canvas));
  assert.deepEqual(f.rows().map(row => row.dataset.id), ['301']);
});

for (const phase of ['snapshot', 'session']) test(`canvas restore ${phase} failure keeps current raw rows visible`, async () => {
  const f = canvasLifecycleHarness();
  if (phase === 'snapshot') f.context.applyCanvasSnapshot = () => { throw new Error('Synthetic invalid snapshot'); };
  else f.context.window.FundsWorkspaceSession.restore = () => { throw new Error('Synthetic invalid saved query'); };
  f.network(async () => ({ page: 1, size: 50, total: 1, rows: [flow(201)] }));
  await f.changeCanvas(7);
  assert.equal(f.requests.length, 1);
  assert.deepEqual(f.rows().map(row => row.dataset.id), ['201']);
  assert.match(f.lifecycleCalls.at(-1)[0], /画布未能载入/);
});

for (const phase of ['metadata', 'graph']) test(`stale canvas ${phase} read cannot query an A-B-A binding`, async () => {
  const f = canvasLifecycleHarness(), pending = deferred();
  if (phase === 'metadata') f.context.caseRequest = () => pending.promise;
  else f.graph(() => pending.promise);
  const opening = f.changeCanvas(7); await flush();
  f.context.activateCanvas(8); f.context.activateCanvas(7);
  pending.resolve({ snapshot: { nodes: [], edges: [] } }); await opening;
  assert.equal(f.requests.length, 0);
  assert.deepEqual(f.lifecycleCalls, []);
});

for (const action of ['delete', 'new', 'clone']) test(`canvas publish ${action} retains its query owner`, async () => {
  const f = canvasLifecycleHarness(); f.state.canvasId = 7; f.state.canvasGeneration++;
  f.element('fTracked').checked = true;
  f.network(async params => ({ page: Number(params.get('page')), size: 50, total: 150, rows: [flow(301)] }));
  await f.publishCanvas(action);
  assert.equal(f.requests.length, 1);
  const params = new URL(f.requests[0], 'http://synthetic.local').searchParams;
  assert.equal(params.get('kw') || '', action === 'delete' ? 'SYN-RESTORED' : '');
  assert.equal(params.get('page'), action === 'delete' ? '3' : '1');
  assert.equal(params.get('canvas_id') || '0', action === 'delete' ? '0' : '8');
  assert.equal(f.lifecycleCalls.includes('restore'), action === 'delete');
});

test('canvas delete restore failure still queries the current default canvas', async () => {
  const f = canvasLifecycleHarness(); f.state.canvasId = 7; f.state.canvasGeneration++;
  f.context.window.FundsWorkspaceSession.restore = () => { throw new Error('Synthetic invalid saved query'); };
  f.network(async () => ({ page: 1, size: 50, total: 1, rows: [flow(201)] }));
  await f.publishCanvas('delete');
  assert.equal(f.requests.length, 1); assert.deepEqual(f.rows().map(row => row.dataset.id), ['201']);
  assert.match(f.lifecycleCalls.at(-1)[0], /部分数据未刷新/);
});

for (const action of ['select', 'delete']) test(`stale final ${action} query cannot focus or notify a later A-B-A binding`, async () => {
  const f = canvasLifecycleHarness(), pending = deferred();
  f.context.applyPendingFocusEdge = () => { f.lifecycleCalls.push('focus'); };
  f.network(() => pending.promise);
  const opening = action === 'select' ? f.changeCanvas(7) : f.publishCanvas('delete');
  await flush();
  const current = f.state.canvasId;
  f.context.activateCanvas(8); f.context.activateCanvas(current);
  pending.resolve({ page: 1, size: 50, total: 1, rows: [flow(201)] }); await opening;
  assert.equal(f.requests.length, 1);
  assert.equal(f.lifecycleCalls.includes('focus'), false);
  assert.equal(f.lifecycleCalls.some(call => Array.isArray(call)), false);
  assert.deepEqual(f.rows(), []);
});

test('actual startup owns one final page query after graph and canvas initialization', async () => {
  const f = lifecycleHarness();
  await f.start();
  assert.equal(f.requests.length, 1);
  assert.equal(new URL(f.requests[0], 'http://synthetic.local').searchParams.get('page'), '1');
  assert.deepEqual(f.lifecycleCalls, ['ready']);
  assert.deepEqual(f.rows().map(row => row.dataset.id), ['1', '2']);
});

for (const canvas of [0, 7]) test(`managed case with canvas ${canvas} queries only after restored filters and page`, async () => {
  const f = lifecycleHarness({ canvas });
  f.network(async params => ({ page: Number(params.get('page')), size: 50, total: 150,
    rows: [flow(params.get('page') === '3' ? 301 : 1)] }));
  assert.equal(await f.context.openManagedCase(2, f.context.caseContext()), true);
  assert.equal(f.requests.length, 1);
  const params = new URL(f.requests[0], 'http://synthetic.local').searchParams;
  assert.equal(params.get('kw'), 'SYN-RESTORED'); assert.equal(params.get('page'), '3');
  assert.deepEqual(f.rows().map(row => row.dataset.id), ['301']);
  assert.deepEqual(f.lifecycleCalls, canvas ? ['snapshot', 'restore'] : ['restore']);
});

test('a real malformed saved snapshot still leaves current-case rows visible and reports the restore warning', async () => {
  const f = lifecycleHarness({ canvas: 7 }), snapshot = { nodes: {} };
  f.state.lastGraph = {}; f.context.resetGraphView = () => {};
  f.context.newGraphPresentation = () => ({});
  vm.runInContext(section('function applyCanvasSnapshot(', 'function scheduleCanvasOpeningFit('), f.context);
  assert.throws(() => f.context.applyCanvasSnapshot(snapshot), /not iterable/);
  f.context.caseRequest = async () => ({ snapshot });
  f.network(async params => {
    assert.equal(params.get('case_id'), '2');
    return { page: 1, size: 50, total: 1, rows: [flow(201)] };
  });
  assert.equal(await f.context.openManagedCase(2, f.context.caseContext()), true);
  assert.equal(f.requests.length, 1);
  assert.deepEqual(f.rows().map(row => row.dataset.id), ['201']);
  assert.equal(f.lifecycleCalls.length, 1);
  assert.match(f.lifecycleCalls[0][0], /部分数据加载失败/); assert.equal(f.lifecycleCalls[0][1], 'warn');
  assert.equal(f.lifecycleCalls[0][2].canvasId, 7);
  assert.deepEqual(snapshot, { nodes: {} });
  await f.context.loadGraph(); assert.equal(f.requests.length, 1);
});

test('startup focus failure retains a correct first page and rethrows the original error', async () => {
  const f = lifecycleHarness(), failure = new Error('Synthetic focus restore failure');
  f.context.applyPendingFocusEdge = async () => { throw failure; };
  await assert.rejects(f.start(), error => error === failure);
  assert.equal(f.requests.length, 1);
  assert.deepEqual(f.rows().map(row => row.dataset.id), ['1', '2']);
  assert.deepEqual(f.lifecycleCalls, ['failed']);
});

test('stale startup restore failure cannot query or consume a later A-B-A binding', async () => {
  const f = lifecycleHarness(), pending = deferred(), failure = new Error('Synthetic stale focus failure');
  f.context.applyPendingFocusEdge = () => pending.promise;
  const opening = f.start(); await flush();
  f.context.selectCase(2); f.context.selectCase(1);
  pending.reject(failure); await assert.rejects(opening, error => error === failure);
  assert.equal(f.requests.length, 0);
  await f.context.loadGraph(); assert.equal(f.requests.length, 1);
});

test('failed caller-owned graph read leaves the binding refresh for a successful ordinary caller', async () => {
  const f = harness(); f.context.activateCanvas(7);
  f.graph(async () => { throw new Error('Synthetic graph read failure'); });
  await assert.rejects(f.context.loadGraph(true, f.context.caseContext(), { tableQuery: 'caller' }), /Synthetic graph read failure/);
  assert.equal(f.requests.length, 0);
  f.graph(async () => {}); await f.context.loadGraph();
  assert.equal(f.requests.length, 1);
  await f.context.loadGraph(); assert.equal(f.requests.length, 1);
});

test('old caller-owned A-B-A graph completion cannot consume a fresh binding refresh', async () => {
  const f = harness(), old = deferred();
  f.context.selectCase(2); f.graph(() => old.promise);
  const pending = f.context.loadGraph(true, f.context.caseContext(), { tableQuery: 'caller' });
  f.context.selectCase(1); f.graph(async () => {});
  old.resolve(); await pending;
  assert.equal(f.requests.length, 0);
  await f.context.loadGraph(); assert.equal(f.requests.length, 1);
  await f.context.loadGraph(); assert.equal(f.requests.length, 1);
});

test('query status hides totals while keeping loading, failure and retry feedback visible', async () => {
  const f = harness(); f.network(params => pageResult(Number(params.get('page')), 75));
  await f.search(); assertQuietResult(f, 75);
  const pending = deferred(); f.network(() => pending.promise);
  await wheelEdge(f, 1);
  assert.equal(f.element('resultInfo').hidden, false);
  assert.equal(f.element('resultInfo').textContent, '正在读取…');
  pending.reject(new Error('Synthetic continuation failure')); await flush();
  assert.equal(f.element('resultInfo').hidden, false);
  assert.equal(f.element('resultInfo').textContent, '续载失败');
  assert.match(f.element('resultInfo').title, /Synthetic continuation failure/);
  assert.equal(f.rows().length, 50); assert.equal(f.state.search.total, 75);
  f.network(params => pageResult(Number(params.get('page')), 75));
  await wheelEdge(f, 1); assertQuietResult(f, 75); assert.equal(f.rows().length, 75);
  f.network(async () => { throw new Error('Synthetic query failure'); });
  assert.equal(await f.search(), false);
  assert.equal(f.element('resultInfo').hidden, false);
  assert.equal(f.element('resultInfo').textContent, '查询失败 · 保留上次结果');
  assert.equal(f.state.search.total, 75); assert.equal(f.rows().length, 75);
  f.network(() => pageResult(1, 0)); await f.search(); assertQuietResult(f, 0);
});

test('query disclosure keeps table tools, results and selection usable and restores internal focus', async () => {
  const f = harness(); await f.search(); f.toggleAnchor(1, true);
  f.element('fAccount').focus(); f.element('btnQueryToggle').click();
  assert.equal(f.element('queryFields').inert, true);
  assert.equal(f.element('btnQueryToggle').getAttribute('aria-expanded'), 'false');
  assert.equal(f.doc.activeElement, f.element('btnQueryToggle'));
  assert.equal(f.element('flowTools').closest('[inert]'), null);
  assert.equal(f.box(1).checked, true); assertQuietResult(f, 2);
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
  assert.equal(f.element('flowCkCount').textContent, '已选锚点 1 条记录');
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
  const horizontal = f.element('flowHorizontalScroll');
  assert.equal(horizontal.parentElement, f.element('tab-search'));
  assert.equal(viewport.fire('keydown', { key: 'ArrowRight' }).defaultPrevented, false, 'native horizontal keys remain available');
  viewport.scrollLeft = 83; viewport.scrollTop = 41;
  f.open();
  assert.equal(viewport.parentElement, f.element('flowOverviewContent'));
  assert.equal(horizontal.parentElement, viewport.parentElement, 'the same horizontal control follows its table into overview');
  assert.equal(f.element('flowTable'), table); assert.equal(f.rows()[0], row);
  assert.equal(viewport.scrollLeft, 0); assert.equal(viewport.scrollTop, 0);
  assert.equal(f.box(1).checked, false); assert.equal(f.box(2).checked, true);
  assert.equal(f.box(1).getAttribute('aria-label'), '加入当前画布，流水 1');
  assert.equal(f.element('flowSelectionHeader').textContent, '上图');
  viewport.scrollLeft = 17; viewport.scrollTop = 200; f.close();
  assert.equal(viewport.parentElement, f.element('tab-search')); assert.equal(f.element('flowTable'), table);
  assert.equal(horizontal.parentElement, viewport.parentElement);
  assert.equal(f.box(1).checked, true); assert.equal(f.box(2).checked, false);
  assert.equal(viewport.scrollLeft, 83); assert.equal(viewport.scrollTop, 41);
  assert.equal(f.doc.activeElement, f.element('btnFlowOverview'));
  f.open(); assert.equal(viewport.scrollLeft, 17); assert.equal(viewport.scrollTop, 200);
  assert.equal(f.doc.querySelectorAll('.flow-horizontal-scroll').length, 1, 'overview reuses the existing control without duplicate IDs');
  assert.equal(f.element('flowHorizontalScroll'), horizontal);
  assert.equal(viewport.fire('keydown', { key: 'ArrowLeft' }).defaultPrevented, false);
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
  assert.equal(f.box(1).checked, true); assert.equal(f.element('flowCkCount').textContent, '已选锚点 1 条记录');
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
      await f.search(1, { refresh: true });
      const newRow = f.rows()[0], expected = selector ? newRow.querySelector(selector) : newRow;
      assert.notEqual(newRow, oldRow); assert.equal(f.doc.activeElement, expected);
      assert.equal(expected.lastFocusOptions.preventScroll, true);
      assert.equal(viewport.scrollLeft, 108); assert.equal(viewport.scrollTop, 91);
      f.element('fAccount').focus(); await f.search();
      assert.equal(f.doc.activeElement, f.element('fAccount'));
    }
  }
});

test('a membership button retains focus after its label changes and a disappearing row falls back to the table region', async () => {
  for (const overview of [false, true]) {
    const f = harness(); await f.search(); if (overview) f.open();
    f.rows()[0].querySelector('.act-track').focus(); f.state.tracked.add(1);
    f.setStatus({confirmedIds:['1'],desiredIds:['1']}); await f.search();
    assert.equal(f.doc.activeElement === f.rows()[0].querySelector('.act-track'), true);
    assert.equal(f.doc.activeElement.textContent,'移出');
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

test('unsubmitted draft inputs survive a pending frozen query without a visible summary or implicit request', async () => {
  const f = harness(); f.element('fAccount').value = 'SYN-APPLIED'; await f.search();
  f.element('fAccount').value = 'SYN-DRAFT'; f.element('fAccount').fire('input');
  assert.equal(f.doc.querySelector('#querySummary'), null); assert.equal(f.requests.length, 1);
  assert.equal(f.element('fAccount').value, 'SYN-DRAFT');
  const pending = deferred(); f.network(() => pending.promise); const searching = f.search();
  f.element('fAccount').value = 'SYN-LATER'; f.element('fAccount').fire('input');
  pending.resolve({ page: 1, size: 50, total: 1, rows: [flow(1)] }); await searching;
  assert.equal(f.element('fAccount').value, 'SYN-LATER'); assert.equal(f.requests.length, 2);
  assert.equal(new URL(f.requests.at(-1), 'http://synthetic.local').searchParams.get('entity'), 'SYN-DRAFT');
});

test('canvas invalidation clears real rows immediately and graph readiness refreshes one consistent page', async () => {
  const f = harness(); await f.search(); f.toggleAnchor(1, true); f.open();
  const old = deferred(); f.network(() => old.promise); const stale = f.search();
  f.context.activateCanvas(7);
  assert.equal(f.element('flowOverview').style.display, 'none'); assert.equal(f.rows().length, 0);
  assert.equal(f.state.search.total, 0); assert.equal(f.element('flowCkCount').textContent, '已选锚点 0 条记录');
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
  assert.equal(f.element('flowCkCount').textContent, '已选锚点 0 条记录');
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

test('only explicit query button and plain non-IME Enter preview frozen account fields, preserving leading zeroes', async () => {
  const f = harness(), previews = [], captures = [];
  f.locator({ capture() { const token = { sequence: captures.length + 1 }; captures.push(token); return token; },
    async previewFields(fields, options) { previews.push(JSON.parse(JSON.stringify({ fields, options }))); } });
  f.element('fAccount').value = ' 00000123 '; f.element('fName').value = '合成户名'; f.element('fCounter').value = '00000456';
  await f.element('btnSearch').onclick();
  assert.deepEqual(previews[0], { fields: { account: '', entity: '00000123', name: '合成户名', counterparty: '00000456' }, options: { binding: { sequence: 1 } } });
  const requests = f.requests.length;
  f.element('fAccount').fire('keydown', { key: 'Enter', isComposing: true });
  f.element('fAccount').fire('keydown', { key: 'Enter', keyCode: 229 });
  f.element('fAccount').fire('keydown', { key: 'Enter', shiftKey: true });
  f.element('fMin').fire('keydown', { key: 'Enter', shiftKey: true });
  await flush(); assert.equal(f.requests.length, requests); assert.equal(previews.length, 1);
  f.element('fAccount').fire('keydown', { key: 'Enter' }); await flush();
  assert.equal(previews.length, 2); assert.equal(captures.length, 2);
});

test('free entity text is literal while an explicit picked account and counterparty use exact string filters', async () => {
  const f = harness(), full = '00090071992547409931234567890123456789';
  f.element('fAccount').value = ' 00%_\\ & 合成 ';
  f.element('fCounter').value = '00000123';
  await f.search();
  let params = new URL(f.requests.at(-1), 'http://synthetic.local').searchParams;
  assert.equal(params.get('entity'), '00%_\\ & 合成'); assert.equal(params.has('account'), false);
  assert.equal(params.has('account_exact'), false); assert.equal(params.has('counter_exact'), false);
  f.element('fAccount').value = full; f.element('fAccount').dataset.exactAccount = full;
  f.element('fCounter').dataset.exactAccount = '00000123'; await f.search();
  params = new URL(f.requests.at(-1), 'http://synthetic.local').searchParams;
  assert.equal(params.get('account'), full); assert.equal(params.get('account_exact'), '1'); assert.equal(params.has('entity'), false);
  assert.equal(params.get('counter'), '00000123'); assert.equal(params.get('counter_exact'), '1');
  f.element('fAccount').value = full + '0'; await f.search();
  params = new URL(f.requests.at(-1), 'http://synthetic.local').searchParams;
  assert.equal(params.get('entity'), full + '0'); assert.equal(params.has('account_exact'), false);
});

test('real helper mounts flow-scoped inputs; typing and candidate selection only bind, while SVG and Enter explicitly execute', async () => {
  const f = harness({ entityInputs: true }), full = '00090071992547409931234567890123456789';
  assert.deepEqual([...f.pickerOptions.keys()], ['fAccount', 'fCounter']);
  for (const options of f.pickerOptions.values()) { assert.equal(options.kind, 'account'); assert.ok(options.scope()); }
  const field = f.element('fAccount'); field.value = '合'; field.fire('input');
  assert.equal(f.requests.length, 0);
  f.chooseCandidate('fAccount', { key: '1:' + full, value: full, account: full, label: '合成同名主体' });
  assert.equal(f.requests.length, 0); assert.equal(field.dataset.exactAccount, full); assert.equal(field.value, full);
  const svg = field.parentElement.children.find(node => node.className === 'entity-search-action');
  assert.ok(svg); assert.equal(svg.children[0].src, '/static/ui-icons/search.svg'); assert.equal(svg.textContent, '');
  svg.click(); await flush();
  let params = new URL(f.requests.at(-1), 'http://synthetic.local').searchParams;
  assert.equal(params.get('account'), full); assert.equal(params.get('account_exact'), '1');
  f.chooseCandidate('fCounter', { key: '1:00012', value: '00012', account: '00012', label: '合成对手' });
  assert.equal(f.requests.length, 1);
  f.pickerOptions.get('fCounter').onSubmit('00012'); await flush();
  params = new URL(f.requests.at(-1), 'http://synthetic.local').searchParams;
  assert.equal(params.get('counter'), '00012'); assert.equal(params.get('counter_exact'), '1');
  field.fire('input'); assert.equal(field.dataset.exactAccount, undefined); assert.equal(f.requests.length, 2);
  await f.element('btnReset').onclick(); assert.equal(f.element('fCounter').dataset.exactAccount, undefined);
  assert.deepEqual(f.invalidated, ['fAccount', 'fCounter']);
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
  assertQuietResult(f, 2);
});

test('full primary-reference keyword stays exact through button and Enter submission without a duplicate summary', async () => {
  const f = harness(), number = '00090071992547409931234567890123456789';
  await f.search();
  f.element('fKw').value = number; f.element('fKw').fire('input');
  assert.equal(f.doc.querySelector('#querySummary'), null);
  assert.equal(f.requests.length, 1);
  assertQuietResult(f, 2);
  f.network(async params => {
    assert.equal(params.get('kw'), number);
    return { page: 1, size: 50, total: 1, rows: [{ ...flow(17), trade_no: number }] };
  });
  const icon = f.doc.createElement('img'); f.element('btnSearch').appendChild(icon);
  icon.click(); await flush();
  assert.equal(f.element('fKw').value, number);
  assertQuietResult(f, 1);
  assert.deepEqual(f.rows().map(row => row.dataset.id), ['17']);
  f.element('fKw').fire('keydown', { key: 'Enter' }); await flush();
  assert.equal(new URL(f.requests.at(-1), 'http://synthetic.local').searchParams.get('kw'), number);
  assertQuietResult(f, 1);
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
  assertQuietResult(f, 1);
});

test('programmatic draft changes also prevent preview and never substitute the newer input into the old request', async () => {
  const f = harness(), result = deferred(), previews = [];
  f.locator({ capture: () => ({}), previewFields: fields => previews.push(fields) });
  f.element('fAccount').value = '00001'; f.network(() => result.promise);
  const search = f.search(1, { locate: true }); f.element('fAccount').value = '00002';
  result.resolve({ page: 1, size: 50, total: 1, rows: [flow(1)] }); await search;
  assert.deepEqual(previews, []); assert.match(f.requests[0], /entity=00001/);
  assert.equal(f.element('fAccount').value, '00002'); assert.equal(f.doc.querySelector('#querySummary'), null);
});

test('only the newest successful explicit query may preview; failures keep existing results', async () => {
  const f = harness(), first = deferred(), second = deferred(), previews = []; let count = 0;
  f.locator({ capture: () => ({}), previewFields: fields => previews.push(JSON.parse(JSON.stringify(fields))) });
  f.network(() => (++count === 1 ? first : second).promise);
  f.element('fAccount').value = '00001'; const old = f.search(1, { locate: true });
  f.element('fAccount').value = '00002'; const latest = f.search(1, { locate: true });
  second.resolve({ page: 1, size: 50, total: 1, rows: [flow(2)] }); assert.equal(await latest, true);
  first.resolve({ page: 1, size: 50, total: 1, rows: [flow(1)] }); assert.equal(await old, false);
  assert.deepEqual(previews.map(fields => fields.entity), ['00002']);
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
    assertQuietResult(f, 2); assert.equal(f.rows().length, 2);
    assert.equal(f.element('flowTableScroll').getAttribute('aria-busy'), 'false');
  }
});


test('split automatic columns fill the resized bottom viewport and restore sidebar geometry without writes', async () => {
  const f = harness({ geometry: true, resizeObserver: true }); await f.search();
  f.element('flowTableWrap').value = 'two-line'; f.element('flowTableWrap').fire('change'); f.drainFrames();
  const table = f.element('flowTable'), viewport = f.element('flowTableScroll'), rows = f.rows(), reads = f.requests.length;
  const widths = () => table.querySelector('colgroup').children.map(col => parseFloat(col.style.width));
  const sidebarWidths = widths();
  f.toggleAnchor(1, true);
  f.view('split', () => f.resizeTable(1600));
  assert.equal(widths().reduce((sum, width) => sum + width, 0), 1600);
  assert.equal(widths()[0], 40, 'the fixed anchor column does not absorb spare space');
  f.resizeTable(1900);
  assert.equal(widths().reduce((sum, width) => sum + width, 0), 1900);
  for (const handle of table.querySelectorAll('.flow-column-resize')) {
    assert.ok(Number(handle.getAttribute('aria-valuenow')) <= Number(handle.getAttribute('aria-valuemax')));
  }
  f.view('standard', () => f.resizeTable(360));
  assert.deepEqual(widths(), sidebarWidths);
  assert.equal(viewport, f.element('flowTableScroll')); assert.deepEqual(f.rows(), rows);
  assert.equal(f.box(1).checked, true); assert.equal(f.requests.length, reads);
  assert.equal(f.membershipCalls.length, 0); assert.equal(f.writes.length, 0);
});

test('automatic content columns stay balanced and adapt to loaded party names', async () => {
  const f = harness({ geometry: true });
  let current = { ...flow(1), payer_name: 'A', payer_account: '', payee_name: 'B', payee_account: '', summary: 'Short note' };
  f.network(async () => ({ page: 1, size: 50, total: 1, rows: [current] })); await f.search();
  f.element('flowTableWrap').value = 'two-line'; f.element('flowTableWrap').fire('change'); f.drainFrames();
  f.view('split', () => f.resizeTable(1835));
  const widths = () => f.element('flowTable').querySelector('colgroup').children.map(col => parseFloat(col.style.width));
  const short = widths(), text = short.filter((_width, index) => [2, 3, 5].includes(index));
  assert.ok(Math.max(...text) / Math.min(...text) < 1.4, 'short summaries do not absorb all spare width');
  assert.ok(Math.abs(short[2] - short[3]) <= 1); assert.equal(short.reduce((sum, width) => sum + width, 0), 1835);
  current = { ...current, payer_name: 'Long synthetic company '.repeat(8) };
  await f.search(); const long = widths();
  assert.ok(long[2] > short[2], 'content measurement gives longer parties more useful room');
  assert.ok(long[5] < short[5]); assert.ok(Math.abs(long[2] - long[3]) <= 1);
  f.resizeTable(420);
  assert.ok(widths().reduce((sum, width) => sum + width, 0) > 420, 'small slots keep column minima and local scrolling');
  assert.equal(f.rows().length, 1); assert.equal(f.membershipCalls.length, 0);
});

test('content measurement uses actual font parts when tabular-number font shorthand is empty', async () => {
  const f = harness({ geometry: true, fontMeasure: true }); await f.search();
  f.element('flowTableWrap').value = 'two-line'; f.element('flowTableWrap').fire('change'); f.drainFrames();
  f.view('split', () => f.resizeTable(1835));
  const timeWidth = parseFloat(f.element('flowTable').querySelector('colgroup').children[1].style.width);
  assert.ok(timeWidth >= 112, 'the ten-character date needs 90px plus cell insets in the supplied font');
  f.view('standard', () => f.resizeTable(360));
  assert.ok(parseFloat(f.element('flowTable').querySelector('colgroup').children[1].style.width) >= 112);
});

test('content sizing follows displayed account aliases and keeps exact large amounts and IDs', async () => {
  const f = harness({ geometry: true }), flowId = '9007199254740993';
  let row = { ...flow(flowId), payer_name: 'A', payee_name: 'B', payer_account: 'INTERNAL-'.repeat(20),
    payer_display_account: '000CARD-A', payee_display_account: '000CARD-B', summary: 'Short' };
  f.network(async () => ({ page: 1, size: 50, total: 1, rows: [row] })); await f.search();
  f.element('flowTableWrap').value = 'two-line'; f.element('flowTableWrap').fire('change'); f.drainFrames();
  f.view('split', () => f.resizeTable(1835));
  const cols = () => f.element('flowTable').querySelector('colgroup').children.map(col => parseFloat(col.style.width));
  const initial = cols();
  row = { ...row, payer_display_account: '000CARD-' + '9'.repeat(80) };
  await f.search();
  assert.ok(cols()[2] > initial[2], 'the display alias, rather than the longer internal identity, sets the content demand');
  assert.ok(cols()[4] >= '¥9,999,999,999,999,999.99'.length * 7 + 10);
  assert.equal(f.rows()[0].dataset.id, flowId); assert.equal(row.amount, '9999999999999999.99');
  assert.equal(row.payer_account, 'INTERNAL-'.repeat(20)); assert.equal(f.membershipCalls.length, 0);
});

test('a refreshed split result can shrink its automatic columns back to the viewport', async () => {
  const f = harness({ geometry: true, resizeObserver: true }); await f.search();
  f.element('flowTableWrap').value = 'two-line'; f.element('flowTableWrap').fire('change'); f.drainFrames();
  const table = f.element('flowTable');
  f.view('split', () => f.resizeTable(1900)); await f.search(1, { refresh: true });
  f.resizeTable(1600);
  assert.equal(table.querySelector('colgroup').children.reduce((sum, col) => sum + parseFloat(col.style.width), 0), 1600);
});

test('split column preferences are independent of sidebar and overview preferences', async () => {
  const saved = new Map(), storage = { getItem: key => saved.get(key) || null, setItem: (key, value) => saved.set(key, value) };
  const f = harness({ geometry: true, columnStorage: storage }); await f.search();
  f.element('flowTableWrap').value = 'two-line'; f.element('flowTableWrap').fire('change'); f.drainFrames();
  const table = f.element('flowTable'), handle = table.querySelectorAll('thead th')[2].querySelector('.flow-column-resize');
  const payerWidth = () => parseFloat(table.querySelector('colgroup').children[2].style.width);
  handle.fire('keydown', { key: 'ArrowRight' }); const sidebarWidth = payerWidth();
  f.view('split', () => f.resizeTable(1600));
  assert.notEqual(payerWidth(), sidebarWidth);
  handle.fire('keydown', { key: 'ArrowRight' }); const splitWidth = payerWidth();
  const prefs = JSON.parse(saved.get('funds_flow_columns_v1'));
  assert.equal(prefs['sidebar:compact:two-line'][2], sidebarWidth);
  assert.equal(prefs['split:compact:two-line'][2], splitWidth);
  f.view('standard', () => f.resizeTable(360)); assert.equal(payerWidth(), sidebarWidth);
  f.view('split', () => f.resizeTable(1600)); assert.equal(payerWidth(), splitWidth);
  f.element('btnFlowColumnsReset').click();
  assert.equal(JSON.parse(saved.get('funds_flow_columns_v1'))['split:compact:two-line'], undefined);
  f.view('standard', () => f.resizeTable(360)); assert.equal(payerWidth(), sidebarWidth);
});

test('resizing one split column preserves the already expanded summary width', async () => {
  const f = harness({ geometry: true }); await f.search();
  f.element('flowTableWrap').value = 'two-line'; f.element('flowTableWrap').fire('change'); f.drainFrames();
  f.view('split', () => f.resizeTable(3600));
  const table = f.element('flowTable'), cols = table.querySelector('colgroup').children;
  const summaryWidth = cols[5].style.width;
  assert.ok(parseFloat(summaryWidth) > 800);
  table.querySelectorAll('thead th')[2].querySelector('.flow-column-resize').fire('keydown', { key: 'ArrowRight' });
  assert.equal(cols[5].style.width, summaryWidth, 'an untouched flex allocation must not shrink to the old sidebar cap');
  assert.equal(cols.reduce((sum, col) => sum + parseFloat(col.style.width), 0), 3608);
});

test('reselecting the current workspace preset keeps its current table position', async () => {
  const f = harness(); await f.search(); const viewport = f.element('flowTableScroll');
  viewport.scrollLeft = 190; viewport.scrollTop = 32;
  f.view('standard');
  assert.deepEqual([viewport.scrollLeft, viewport.scrollTop], [190, 32]);
});

test('a pending text mode callback cannot overwrite the split scroll position', async () => {
  const f = harness({ geometry: true }); await f.search(); const viewport = f.element('flowTableScroll');
  viewport.scrollLeft = 190;
  f.element('flowTableWrap').value = 'two-line'; f.element('flowTableWrap').fire('change');
  f.view('split', () => f.resizeTable(600)); assert.equal(viewport.scrollLeft, 0);
  f.drainFrames(); assert.equal(viewport.scrollLeft, 0);
});

test('workspace views retain one query/table and anchors, with independent filter disclosure and no writes', async () => {
  const f = harness(); await f.search(); f.toggleAnchor(1,true);
  const table=f.element('flowTableScroll'), rows=f.rows(), requests=f.requests.length;
  table.scrollLeft=190; table.scrollTop=32;
  f.element('fAccount').value='SYN-UNSUBMITTED'; f.element('fAccount').fire('input');
  f.view('split');
  assert.equal(f.element('queryFields').inert,true,'first split gives the transaction table room');
  assert.equal(f.element('btnQueryToggle').getAttribute('aria-expanded'),'false');
  assert.equal(f.box(1).getAttribute('aria-label'),'选为追踪锚点，流水 1');
  f.toggleAnchor(2,true);
  assert.equal(f.membershipCalls.length,0,'split checkboxes stay browse anchors, never graph membership');
  f.element('btnQueryToggle').click(); // remember this split view with its filters open
  f.view('focus'); f.view('standard');
  assert.equal(f.element('btnQueryToggle').getAttribute('aria-expanded'),'true');
  f.element('btnQueryToggle').click(); // standard remembers its own closed filter state
  f.view('split');
  assert.equal(f.element('btnQueryToggle').getAttribute('aria-expanded'),'true');
  f.view('standard');
  assert.equal(f.element('btnQueryToggle').getAttribute('aria-expanded'),'false');
  assert.equal(f.element('flowTableScroll'),table);assert.deepEqual(f.rows(),rows);
  assert.deepEqual([table.scrollLeft,table.scrollTop],[190,32]);
  assert.equal(f.element('fAccount').value,'SYN-UNSUBMITTED');
  assert.equal(f.box(1).checked,true);assert.equal(f.box(2).checked,true);
  assert.equal(f.requests.length,requests);assert.equal(f.graphCalls.length,0);assert.equal(f.membershipCalls.length,0);
});

test('overview returns the same table and original anchor meaning after a workspace view change', async () => {
  const f=harness();await f.search();f.toggleAnchor(1,true);
  const table=f.element('flowTableScroll');f.open();
  assert.equal(f.box(1).getAttribute('aria-label'),'加入当前画布，流水 1');
  f.view('split');f.close();
  assert.equal(f.element('flowTableScroll'),table);
  assert.equal(table.parentElement.id,'tab-search');
  assert.equal(f.box(1).checked,true);
  assert.equal(f.box(1).getAttribute('aria-label'),'选为追踪锚点，流水 1');
  f.toggleAnchor(2,true);assert.equal(f.membershipCalls.length,0);
});

test('workspace view scroll snapshots survive browser clamping and are revoked on a binding change', async () => {
  const f=harness(); await f.search(); const table=f.element('flowTableScroll');
  table.scrollLeft=190; table.scrollTop=32;
  const clamp=()=>{table.scrollLeft=0;table.scrollTop=0;};
  f.view('split',clamp); assert.deepEqual([table.scrollLeft,table.scrollTop],[0,0]);
  table.scrollTop=41;
  f.view('standard',clamp); assert.deepEqual([table.scrollLeft,table.scrollTop],[190,32]);
  f.open(); table.scrollTop=200;
  f.view('split'); assert.equal(table.scrollTop,200,'view changes do not move the modal table');
  f.close(); assert.deepEqual([table.scrollLeft,table.scrollTop],[0,41]);
  f.view('standard',clamp); assert.deepEqual([table.scrollLeft,table.scrollTop],[190,32]);
  f.context.selectCase('another-case');
  f.view('split',clamp); f.view('standard',clamp);
  assert.deepEqual([table.scrollLeft,table.scrollTop],[0,0],'old binding offsets cannot restore into a new case');
});

test('a successful empty query does not move the camera to an account excluded by the other conditions', async () => {
  const f = harness(), previews = [];
  f.element('fAccount').value = '0001'; f.element('fAccount').dataset.exactAccount = '0001';
  f.element('fMin').value = '100000';
  f.locator({ capture: () => ({ binding: 1 }), previewFields: value => previews.push(value) });
  f.network(async () => ({ page: 1, size: 50, total: 0, rows: [] }));
  assert.equal(await f.search(1, { locate: true }), true);
  assert.equal(f.rows().length, 0); assert.deepEqual(previews, []);
});

test('a confirmed row is already tracked when only its other selected observation is tracked', async () => {
  const f = harness(); f.state.tracked = new Set([18]);
  f.setStatus({confirmedIds:['18'],desiredIds:['18']});
  f.render([eventRow()]);
  assert.equal(f.rows()[0].querySelector('.act-track').textContent, '部分·移出');
  assert.equal(f.rows()[0].querySelector('.act-track').getAttribute('aria-pressed'), 'mixed');
  assert.equal(f.rows()[0].querySelector('.membership-status').textContent, '已上图');
  f.open(); assert.equal(f.box(17).checked, true);
  f.box(17).checked = false; f.box(17).fire('change');
  assert.deepEqual(f.membershipCalls[0].ids, ['17','18']);
  assert.equal(f.membershipCalls[0].included, false);
  f.membershipCalls[0].gate.resolve(); await flush();
  assert.equal(f.box(17).checked, false);
});

test('row tracking and bulk anchors send all selected observations while source context stays singular', async () => {
  const f = harness(); f.installEditorCommands();
  f.render([eventRow(), flow(20)]);
  f.rows()[0].querySelector('.act-track').click(); await flush();
  assert.deepEqual(f.writes[0].desired.map(String), ['17','18']);
  f.rows()[0].querySelector('.act-ctx').click();
  assert.deepEqual(f.contextCalls, ['17']);
  f.toggleAnchor(17,true); f.toggleAnchor(20,true);
  assert.equal(await f.element('btnBulkTrack').onclick(), true);
  const bulk = f.writes.find(write => write.path === '/api/track/bulk');
  assert.deepEqual(bulk.flow_ids.map(String), ['17','18','20']);
  assert.deepEqual(bulk.expected_ids, []);
});

test('filter expansion displays a partial source anchor without expanding readonly captures or bulk tracking', async () => {
  const f = harness(); f.installEditorCommands();
  f.render([eventRow(18, ['18'])]); f.toggleAnchor(18, true);
  f.render([eventRow(17, ['17', '18'])]);
  assert.equal(f.box(17).checked, false); assert.equal(f.box(17).indeterminate, true);
  assert.equal(f.element('flowCkAll').checked, false); assert.equal(f.element('flowCkAll').indeterminate, true);
  for (let pass = 0; pass < 2; pass++) {
    assert.deepEqual(Array.from(f.drafts.selectedIds()), ['18']);
    assert.deepEqual(Array.from(f.drafts.capture()), ['18']);
    f.open(); f.close(); f.render([eventRow(17, ['17', '18'])]);
  }
  await f.element('btnBulkTrack').onclick();
  assert.deepEqual(f.writes.find(write => write.path === '/api/track/bulk').flow_ids, ['18']);
  f.render([eventRow(18, ['18'])]);
  assert.equal(f.box(18).checked, true); assert.equal(f.box(18).indeterminate, false);
  f.render([eventRow(17, ['17', '18'])]); f.toggleAnchor(17, true);
  assert.deepEqual(Array.from(f.drafts.selectedIds()).sort(), ['17', '18']);
  assert.equal(f.box(17).indeterminate, false);
  f.toggleAnchor(17, false); assert.deepEqual(Array.from(f.drafts.capture()), []);
});

test('explicit row Space and native header selection resolve partial anchors while retaining offscreen selections', () => {
  const f = harness();
  f.render([eventRow(18, ['18']), flow(20)]); f.toggleAnchor(18, true); f.toggleAnchor(20, true);
  f.render([eventRow(17, ['17', '18'])]);
  f.rows()[0].fire('keydown', {key:' '});
  assert.deepEqual(Array.from(f.drafts.selectedIds()).sort(), ['17', '18', '20']);
  assert.equal(f.box(17).checked, true); assert.equal(f.box(17).indeterminate, false);
  f.rows()[0].fire('keydown', {key:' '});
  assert.deepEqual(Array.from(f.drafts.selectedIds()), ['20']);
  f.drafts.restore(['18', '20']);
  const all = f.element('flowCkAll'); all.checked = true; all.fire('change');
  assert.deepEqual(Array.from(f.drafts.selectedIds()).sort(), ['17', '18', '20']);
  all.checked = false; all.fire('change');
  assert.deepEqual(Array.from(f.drafts.capture()), ['20']);
});

test('removal compares selected string observation IDs with numeric tracked IDs without changing other membership', async () => {
  const f = harness(); f.installEditorCommands(); f.state.tracked = new Set([17,18,99]);
  await f.context.untrackFlows(['17','18']);
  assert.deepEqual(f.writes[0].expected, [17,18,99]);
  assert.deepEqual(f.writes[0].desired, [99]);
});

test('overview page-all and keyboard membership use selected observation sets without expanding a filtered event', async () => {
  const f = harness(); f.render([eventRow(18,['18']),flow(20)]); f.open();
  f.rows()[0].fire('keydown',{key:' '});
  assert.deepEqual(f.membershipCalls[0].ids,['18']);
  f.membershipCalls[0].gate.resolve(); await flush();
  const all=f.element('overviewTrackAll'); all.checked=true; all.fire('change');
  assert.deepEqual(f.membershipCalls[1].ids,['18','20']);
  f.membershipCalls[1].gate.resolve(); await flush();
  f.close(); f.render([eventRow(),flow(20)]); f.open();
  all.checked=false; all.fire('change');
  assert.deepEqual(f.membershipCalls[2].ids,['17','18','20']);
});

const exactFunds = {policy:'evidence-bounded-funds-v1',status:'exact',observation_count:2,confirmed_event_count:1,
  unresolved_observation_count:0,count:{lower:1,upper:1},totals:[{currency:'CNY',amount:'100.00',lower:'100.00',upper:'100.00'}]};
const boundedFunds = {...exactFunds,status:'bounded',confirmed_event_count:0,unresolved_observation_count:2,
  count:{lower:0,upper:2},totals:[{currency:'CNY',amount:null,lower:'0.00',upper:'200.00'}]};

test('a quiet query counts a confirmed pair once internally and has no further scroll page', async () => {
  const f = harness();
  f.network(async () => ({page:1,size:1,total:1,rows:[eventRow()],funds:exactFunds}));
  assert.equal(await f.search(),true);
  assert.equal(new URL(f.requests.at(-1),'http://synthetic.local').searchParams.get('granularity'),'events');
  assertQuietResult(f, 1);
  assert.equal(f.state.search.total,1);
  const reads = f.requests.length; await wheelEdge(f, 1);
  assert.equal(f.requests.length, reads);
});

test('source counts remain exact across draft edits and a failed query', async () => {
  const f = harness();
  f.network(async () => ({page:1,size:50,total:2,rows:[flow(17),flow(18)],funds:boundedFunds}));
  await f.search();
  assertQuietResult(f, 2);
  const reads=f.requests.length;
  f.element('fAccount').value='SYN-DRAFT'; f.element('fAccount').fire('input');
  assert.equal(f.requests.length,reads);
  assert.equal(f.element('fAccount').value,'SYN-DRAFT');
  assertQuietResult(f, 2);
  f.network(async () => {throw new Error('Synthetic failed query');});
  assert.equal(await f.search(),false);
  assert.match(f.element('resultInfo').textContent,/查询失败 · 保留上次结果/);
  assert.equal(f.state.search.total, 2);
  assert.equal(f.element('resultInfo').hidden, false);
  assert.doesNotMatch(f.element('resultInfo').textContent,/条记录|待核|事件/);
});

test('canvas and case A-B-A invalidation revoke query funds and reject late old summaries', async () => {
  for (const invalidate of [f => f.context.activateCanvas(7), f => {f.context.selectCase(2); f.context.selectCase(1);}]) {
    const f=harness();
    f.network(async () => ({page:1,size:50,total:1,rows:[eventRow()],funds:exactFunds}));
    await f.search();
    const late=deferred(); f.network(() => late.promise); const pending=f.search();
    invalidate(f);
    assert.doesNotMatch(f.element('resultInfo').textContent,/100\.00|1 条记录/);
    f.network(async () => ({page:1,size:50,total:2,rows:[flow(17),flow(18)],funds:boundedFunds}));
    await f.context.loadGraph();
    late.resolve({page:1,size:50,total:1,rows:[eventRow()],funds:exactFunds});
    assert.equal(await pending,false);
    assertQuietResult(f, 2);
  }
});

const pageResult = (page, total = 600) => ({ page, size: 50, total,
  rows: Array.from({ length: Math.min(50, Math.max(0, total - (page - 1) * 50)) }, (_, index) => flow((page - 1) * 50 + index + 1)) });
const paged = params => pageResult(Number(params.get('page')));
test('continued exact queries retain all selected observations after their representative row is evicted', async () => {
  const f = harness({ entityInputs: true, geometry: true });
  const first = '9007199254740993', second = '9007199254740994';
  f.installEditorCommands();
  f.chooseCandidate('fAccount', { account: 'fte-SYN-ACCOUNT', value: 'SYN-DISPLAY-ACCOUNT' });
  f.element('fKw').value = 'SYN-APPLIED';
  f.network(async params => {
    assert.equal(params.get('granularity'), 'events');
    assert.equal(params.get('account'), 'fte-SYN-ACCOUNT');
    assert.equal(params.get('account_exact'), '1');
    assert.equal(params.has('entity'), false);
    assert.equal(params.get('kw'), 'SYN-APPLIED');
    const result = paged(params);
    if (result.page === 1) result.rows[0] = eventRow(first, [first, second]);
    return result;
  });
  await f.search(); f.toggleAnchor(first, true);
  f.element('fAccount').value = 'UNSUBMITTED-ACCOUNT'; f.element('fAccount').fire('input');
  f.element('fKw').value = 'UNSUBMITTED-KEYWORD'; f.element('fKw').fire('input');
  for (let page = 2; page <= 6; page++) await wheelEdge(f, 1);
  assert.equal(f.requests.length, 6); assert.equal(f.box(first), undefined);
  assert.ok(f.rows().length <= 250);
  assert.deepEqual(Array.from(f.drafts.selectedIds()), [first, second]);
  assert.deepEqual(Array.from(f.drafts.capture()), [first, second]);
  await f.element('btnBulkTrack').onclick();
  const write = f.writes.find(item => item.path === '/api/track/bulk');
  assert.deepEqual(write.flow_ids, [first, second]);
  assert.equal(f.element('fAccount').value, 'UNSUBMITTED-ACCOUNT');
  assert.equal(f.element('fKw').value, 'UNSUBMITTED-KEYWORD');
});
async function wheelEdge(f, direction) {
  const viewport = f.element('flowTableScroll');
  viewport.scrollTop = direction < 0 ? 0 : Math.max(0, viewport.scrollHeight - viewport.clientHeight);
  const event = viewport.fire('wheel', { deltaY: direction * 80, deltaX: 0 });
  await flush(); return event;
}

test('continuous scrolling reads only adjacent pages and bounds its real rows while retaining offscreen anchors', async () => {
  const f = harness({ geometry: true }); f.network(paged); await f.search();
  assert.equal(f.requests.length, 1); f.toggleAnchor(1, true);
  for (let page = 2; page <= 8; page++) {
    await wheelEdge(f, 1);
    assert.equal(f.requests.length, page);
    assert.ok(f.rows().length <= 250); assert.equal(new Set(f.rows().map(row => row.dataset.id)).size, f.rows().length);
  }
  assert.equal(f.rows()[0].dataset.id, '151'); assert.equal(f.rows().at(-1).dataset.id, '400');
  assert.equal(f.box(1), undefined);
  assert.deepEqual(Array.from(f.drafts.selectedIds()), ['1']); assert.deepEqual(Array.from(f.drafts.capture()), ['1']);
  assert.equal(f.element('flowCkCount').textContent, '已选锚点 1 条记录');
  assert.deepEqual(f.requests.map(path => Number(new URL(path, 'http://synthetic.local').searchParams.get('page'))), [1, 2, 3, 4, 5, 6, 7, 8]);
  assert.equal(f.requests.every(path => new URL(path, 'http://synthetic.local').searchParams.get('size') === '50'), true);
  const all = f.element('flowCkAll'); all.checked = true; all.fire('change');
  const selected = Array.from(f.drafts.selectedIds());
  assert.equal(selected.length, 251); assert.equal(selected.includes('1'), true); assert.equal(selected.includes('401'), false);
  assert.equal(f.membershipCalls.length, 0, 'sidebar all only selects anchors');
  f.open(); const overviewAll = f.element('overviewTrackAll'); overviewAll.checked = true; overviewAll.fire('change');
  assert.equal(f.membershipCalls[0].ids.length, 250); assert.equal(f.membershipCalls[0].ids.includes('1'), false);
  assert.equal(f.membershipCalls[0].ids.includes('401'), false);
});

test('prepending and evicting variable-height rows preserve the visible row offset and horizontal position', async () => {
  const f = harness({ geometry: true }); f.network(paged);
  for (let row = 1; row <= 600; row++) f.heights.set(String(row), row % 3 === 0 ? 83 : 31);
  await f.search(4); const viewport = f.element('flowTableScroll'); viewport.scrollLeft = 177; viewport.scrollTop = 11;
  const before = f.drafts.captureViewport();
  await wheelEdge(f, -1);
  const previousFirst = f.rows().find(row => row.dataset.id === before.rowId);
  assert.equal(previousFirst.getBoundingClientRect().top, 0, 'the wheel reached the top before the prepend');
  assert.equal(viewport.scrollLeft, 177); assert.equal(f.rows()[0].dataset.id, '101');
  for (let next = 5; next <= 9; next++) {
    viewport.scrollTop = viewport.scrollHeight - viewport.clientHeight;
    const anchor = f.drafts.captureViewport();
    await wheelEdge(f, 1);
    const visible = f.rows().find(row => row.dataset.id === anchor.rowId);
    assert.ok(visible); assert.equal(visible.getBoundingClientRect().top, anchor.offset);
    assert.equal(viewport.scrollLeft, 177); assert.ok(f.rows().length <= 250);
  }
  viewport.scrollTop = 13;
  const anchor = f.drafts.captureViewport();
  // A native scroll event near the upper edge starts the previous page before
  // reaching zero, so the partial first row remains the actual visual anchor.
  viewport.fire('scroll'); await flush();
  const visible = f.rows().find(row => row.dataset.id === anchor.rowId);
  assert.equal(visible.getBoundingClientRect().top, anchor.offset);
});

test('scroll bursts have one flight and continue the frozen applied filters without camera previews', async () => {
  const f = harness({ geometry: true }), pending = deferred(), previews = [];
  f.locator({ capture: () => ({}), previewFields: value => previews.push(value) });
  f.element('fAccount').value = '000-APPLIED'; f.network(paged); await f.search(1, { locate: true });
  f.network(params => Number(params.get('page')) === 2 ? pending.promise : paged(params));
  f.element('fAccount').value = 'UNSUBMITTED'; f.element('fAccount').fire('input');
  const viewport = f.element('flowTableScroll'); viewport.scrollTop = viewport.scrollHeight - viewport.clientHeight;
  for (let index = 0; index < 12; index++) { viewport.fire('wheel', { deltaY: 80 }); viewport.fire('scroll'); }
  assert.equal(f.requests.length, 2); assert.equal(viewport.getAttribute('aria-busy'), 'true');
  pending.resolve(pageResult(2)); await flush(); await wheelEdge(f, 1);
  assert.equal(f.requests.length, 3);
  assert.equal(f.requests.every(path => new URL(path, 'http://synthetic.local').searchParams.get('entity') === '000-APPLIED'), true);
  assert.equal(f.element('fAccount').value, 'UNSUBMITTED'); assert.equal(previews.length, 1);
  await f.search(f.state.search.page, { refresh: true });
  assert.equal(new URL(f.requests.at(-1), 'http://synthetic.local').searchParams.get('entity'), '000-APPLIED');
  assert.equal(f.element('fAccount').value, 'UNSUBMITTED'); assert.equal(previews.length, 1);
});

test('both continuation directions retain failed reads until a new boundary wheel or keyboard action retries once', async () => {
  for (const direction of [-1, 1]) {
    const f = harness({ geometry: true }); f.network(paged); await f.search(2);
    const failedPage = direction < 0 ? 1 : 3; let failures = 1;
    f.network(params => {
      if (Number(params.get('page')) === failedPage && failures-- > 0) throw new Error('合成续载断网');
      return paged(params);
    });
    const original = f.rows().map(row => row.dataset.id); await wheelEdge(f, direction);
    assert.deepEqual(f.rows().map(row => row.dataset.id), original);
    assert.match(f.element('resultInfo').textContent, /续载失败/); assert.match(f.element('resultInfo').title, /合成续载断网/);
    const requests = f.requests.length;
    for (let event = 0; event < 5; event++) f.element('flowTableScroll').fire('scroll');
    await flush(); assert.equal(f.requests.length, requests, 'programmatic scroll never loops a failed read');
    if (direction < 0) await wheelEdge(f, direction);
    else { const row = f.rows().at(-1); row.focus(); row.fire('keydown', { key: 'ArrowDown' }); await flush(); }
    assert.equal(f.requests.length, requests + 1); assert.equal(f.rows().length, 100);
    assertQuietResult(f, 600); assert.equal(f.element('flowTableScroll').getAttribute('aria-busy'), 'false');
    assert.equal(Number(new URL(f.requests.at(-1), 'http://synthetic.local').searchParams.get('page')), failedPage);
  }
});

test('a failed submitted query retries its own frozen draft and keeps the previous result readable', async () => {
  const f = harness({ geometry: true }); f.network(paged); f.element('fAccount').value = 'OLD'; await f.search();
  f.element('fAccount').value = 'ATTEMPT'; f.network(() => { throw new Error('合成查询失败'); });
  assert.equal(await f.search(), false); assert.equal(f.rows().length, 50);
  const beforeRetry = f.requests.length;
  for (let event = 0; event < 5; event++) f.element('flowTableScroll').fire('scroll');
  await flush(); assert.equal(f.requests.length, beforeRetry, 'a failed submitted query waits for deliberate wheel or keyboard input');
  f.element('fAccount').value = 'LATER'; f.network(paged);
  await wheelEdge(f, 1);
  assert.equal(new URL(f.requests.at(-1), 'http://synthetic.local').searchParams.get('entity'), 'ATTEMPT');
  assert.equal(f.element('fAccount').value, 'LATER');
});

test('new queries, case A-B-A and canvas rebindings reject delayed continuation rows and busy cleanup', async () => {
  for (const change of ['query', 'refresh', 'case', 'canvas']) {
    const f = harness({ geometry: true }), pending = deferred(); f.network(paged); await f.search();
    f.network(params => Number(params.get('page')) === 2 ? pending.promise : paged(params));
    await wheelEdge(f, 1); assert.equal(f.requests.length, 2);
    if (change === 'case') { f.context.selectCase(2); f.context.selectCase(1); }
    if (change === 'canvas') f.context.activateCanvas(7);
    f.network(params => ({ ...paged(params), rows: pageResult(1).rows.map(row => ({ ...row, id: 'fresh-' + row.id })) }));
    if (change === 'refresh') await f.search(1, { refresh: true });
    else if (change === 'case' || change === 'canvas') await f.context.loadGraph();
    else await f.search();
    const before = f.rows().map(row => row.dataset.id); pending.resolve(pageResult(2)); await flush();
    assert.deepEqual(f.rows().map(row => row.dataset.id), before); assert.equal(f.rows().length, 50);
    assert.equal(f.element('flowTableScroll').getAttribute('aria-busy'), 'false');
  }
});

test('a saved viewport restores directly to one page using restored inputs and retains only identifiers and geometry', async () => {
  const f = harness({ geometry: true }); f.network(paged); f.element('fAccount').value = 'BEFORE'; await f.search();
  f.element('fAccount').value = 'RESTORED'; f.drafts.restore(['453']);
  assert.equal(f.drafts.restoreViewport({ rowId: '453', page: 10, offset: -9, scrollLeft: 111 }), true);
  const before = f.requests.length; await f.search(10, { refresh: true });
  assert.equal(f.requests.length, before + 1); assert.equal(f.rows().length, 50);
  const params = new URL(f.requests.at(-1), 'http://synthetic.local').searchParams;
  assert.equal(params.get('page'), '10'); assert.equal(params.get('entity'), 'RESTORED');
  assert.equal(f.box(453).checked, true);
  assert.deepEqual(JSON.parse(JSON.stringify(f.drafts.captureViewport())), { rowId: '453', page: 10, offset: -9, scrollLeft: 111 });
  const viewport = f.element('flowTableScroll'), count = f.requests.length;
  viewport.fire('wheel', { deltaY: 80, shiftKey: true }); viewport.fire('wheel', { deltaY: 1, deltaX: 80 });
  await flush(); assert.equal(f.requests.length, count, 'horizontal gestures never fetch vertical continuation');
});

test('keyboard arrows cross a page boundary once and preserve native Enter, Space and external focus', async () => {
  const f = harness({ geometry: true }); f.network(paged); await f.search(2);
  f.rows()[0].focus(); const up = f.rows()[0].fire('keydown', { key: 'ArrowUp' });
  assert.equal(up.immediate, true); assert.equal(up.defaultPrevented, true); await flush();
  assert.equal(f.doc.activeElement.dataset.id, '50'); assert.equal(f.requests.length, 2);
  f.rows().at(-1).focus(); const down = f.rows().at(-1).fire('keydown', { key: 'ArrowDown' }); await flush();
  assert.equal(down.immediate, true); assert.equal(f.doc.activeElement.dataset.id, '101'); assert.equal(f.requests.length, 3);
  const row = f.doc.activeElement; row.fire('keydown', { key: ' ' }); assert.equal(f.box(101).checked, true);
  const pending = deferred(); f.network(() => pending.promise); f.rows().at(-1).focus();
  f.rows().at(-1).fire('keydown', { key: 'ArrowDown' }); f.element('fAccount').focus();
  pending.resolve(pageResult(4)); await flush(); assert.equal(f.doc.activeElement, f.element('fAccount'));
});

test('row-height changes preserve a partial visible row and scope changes revoke delayed measurement restoration', async () => {
  const f = harness({ geometry: true }); f.network(paged); await f.search();
  const viewport = f.element('flowTableScroll'); viewport.scrollTop = 90; viewport.scrollLeft = 170;
  const anchor = f.drafts.captureViewport(); f.element('flowTableDensity').fire('change');
  f.heights.set('1', 80); f.heights.set('2', 80); f.drainFrames();
  assert.equal(f.rows().find(row => row.dataset.id === anchor.rowId).getBoundingClientRect().top, anchor.offset);
  assert.equal(viewport.scrollLeft, 170);
  f.element('flowTableWrap').fire('change'); f.context.activateCanvas(7); f.drainFrames();
  assert.equal(viewport.scrollTop, 0); assert.equal(viewport.scrollLeft, 0);
});

test('a changed result total refreshes the query epoch instead of combining pages from different data states', async () => {
  const f = harness({ geometry: true }); f.network(paged); await f.search();
  f.network(params => pageResult(Number(params.get('page')), 601));
  await wheelEdge(f, 1);
  assert.deepEqual(f.requests.map(path => Number(new URL(path, 'http://synthetic.local').searchParams.get('page'))), [1, 2, 1]);
  assert.equal(f.rows().length, 50); assert.equal(f.state.search.total, 601);
});

test('the last loaded result never starts reads beyond the known end', async () => {
  const f = harness({ geometry: true }); f.network(params => pageResult(Number(params.get('page')), 75)); await f.search();
  await wheelEdge(f, 1); assert.equal(f.rows().length, 75);
  const before = f.requests.length; await wheelEdge(f, 1); await wheelEdge(f, 1);
  assert.equal(f.requests.length, before); assertQuietResult(f, 75);
});

test('restored viewport page wins over legacy page state and a shrunken result clamps directly to its new last page', async () => {
  const f = harness({ geometry: true }); f.network(paged); await f.search();
  f.drafts.restore(['7']); f.drafts.restoreViewport({ rowId: '12003', page: 241, offset: -9, scrollLeft: 80 });
  f.state.search.page = 3; f.network(params => pageResult(Number(params.get('page')), 7));
  const before = f.requests.length; assert.equal(await f.search(3, { refresh: true }), true);
  assert.deepEqual(f.requests.slice(before).map(path => Number(new URL(path, 'http://synthetic.local').searchParams.get('page'))), [241, 1]);
  assert.equal(f.rows().length, 7); assert.equal(f.state.search.page, 1); assert.equal(f.state.search.total, 7);
  assert.equal(f.element('flowTableScroll').scrollLeft, 80); assert.equal(f.element('flowTableScroll').scrollTop, 0);
  assert.equal(f.box(7).checked, true); assertQuietResult(f, 7);
});

test('a clamp read is still revoked by a newer query or canvas binding', async () => {
  for (const changed of ['query', 'canvas']) {
    const f = harness({ geometry: true }), pending = deferred();
    f.network(params => Number(params.get('page')) === 241 ? pageResult(241, 7) : pending.promise);
    const old = f.search(241, { refresh: true }); await flush();
    assert.deepEqual(f.requests.map(path => Number(new URL(path, 'http://synthetic.local').searchParams.get('page'))), [241, 1]);
    if (changed === 'canvas') f.context.activateCanvas(7);
    f.network(() => ({ page: 1, size: 50, total: 1, rows: [flow('NEW')] })); await f.search();
    pending.resolve(pageResult(1, 7)); assert.equal(await old, false);
    assert.deepEqual(f.rows().map(row => row.dataset.id), ['NEW']);
  }
});

test('a slow continuation never evicts the page the reader returned to, but offscreen focus does not pin it later', async () => {
  const f = harness({ geometry: true }); f.network(paged); await f.search();
  for (let page = 2; page <= 5; page++) await wheelEdge(f, 1);
  const original = f.rows(), viewport = f.element('flowTableScroll'), pending = deferred();
  f.network(params => Number(params.get('page')) === 6 ? pending.promise : paged(params));
  await wheelEdge(f, 1); assert.equal(f.requests.length, 6);
  viewport.scrollTop = 0; original[0].focus(); viewport.fire('scroll');
  pending.resolve(pageResult(6)); await flush();
  assert.deepEqual(f.rows(), original, 'the late read leaves all five displayed pages and their row identity intact');
  assert.equal(f.doc.activeElement, original[0]); assert.equal(viewport.scrollTop, 0);
  assert.equal(f.rows()[0].dataset.id, '1'); assert.equal(f.rows().at(-1).dataset.id, '250');
  assert.equal(viewport.getAttribute('aria-busy'), 'false');
  assert.equal(f.element('resultInfo').title, f.element('resultInfo').textContent);
  f.network(paged); await wheelEdge(f, 1);
  assert.equal(f.requests.length, 7); assert.equal(f.rows()[0].dataset.id, '51'); assert.equal(f.rows().at(-1).dataset.id, '300');
  assert.equal(f.doc.activeElement, viewport, 'the now-offscreen old focus follows the existing disappearing-row fallback');
  assert.equal(f.element('resultInfo').title, f.element('resultInfo').textContent);
});

test('evicting the only wide row preserves seven measured column minima and horizontal position without retaining that row', async () => {
  const f = harness({ geometry: true });
  f.widths.set('1', { single: [40, 86, 1700, 140, 96, 4600, 92], wrap: [40, 86, 140, 140, 96, 126, 92] });
  f.element('flowTableWrap').value = 'single'; f.element('flowTableWrap').fire('change'); f.drainFrames();
  f.network(paged); await f.search();
  const viewport = f.element('flowTableScroll'), width = viewport.scrollWidth;
  assert.ok(width > 6000); viewport.scrollLeft = 1800;
  for (let page = 2; page <= 6; page++) await wheelEdge(f, 1);
  assert.equal(f.box(1), undefined); assert.equal(f.rows().length, 250);
  assert.equal(viewport.scrollWidth, width); assert.equal(viewport.scrollLeft, 1800);
  const columns = f.element('flowTable').querySelector('colgroup').children;
  assert.equal(columns.length, 7); assert.equal(columns.every(col => parseFloat(col.style.width) > 0), true);
  assert.deepEqual(Object.keys(f.drafts.captureViewport()).sort(), ['offset', 'page', 'rowId', 'scrollLeft']);
  await f.search(f.state.search.page, { refresh: true });
  assert.equal(viewport.scrollWidth, width); assert.equal(viewport.scrollLeft, 1800, 'same-query authoritative refresh keeps its geometric column envelope');
  await f.search(2);
  assert.ok(viewport.scrollWidth < 1000, 'a new submitted query releases the earlier query geometry');
  assert.ok(viewport.scrollLeft <= viewport.scrollWidth - viewport.clientWidth);
  f.context.activateCanvas(7);
  assert.equal(columns.every(col => col.style.width === '' && col.style.minWidth === ''), true);
});

test('display modes keep separate seven-column geometry and horizontal offsets while preserving the visible row anchor', async () => {
  const f = harness({ geometry: true });
  f.widths.set('1', { single: [40, 86, 1700, 140, 96, 4600, 92], wrap: [40, 86, 140, 140, 96, 126, 92] });
  const wrap = f.element('flowTableWrap'), density = f.element('flowTableDensity');
  wrap.value = 'single'; wrap.fire('change'); f.drainFrames(); f.network(paged); await f.search();
  const viewport = f.element('flowTableScroll'); viewport.scrollLeft = 1800;
  for (let page = 2; page <= 6; page++) await wheelEdge(f, 1);
  const width = viewport.scrollWidth; viewport.scrollTop = 90;
  const anchor = f.drafts.captureViewport();
  wrap.value = 'wrap'; wrap.fire('change'); f.drainFrames();
  assert.ok(viewport.scrollWidth < 1000, 'the wide single-line envelope cannot become wrap mode geometry');
  assert.equal(f.rows().find(row => row.dataset.id === anchor.rowId).getBoundingClientRect().top, anchor.offset);
  viewport.scrollLeft = 130;
  wrap.value = 'single'; wrap.fire('change'); f.drainFrames();
  assert.equal(viewport.scrollWidth, width); assert.equal(viewport.scrollLeft, 1800);
  assert.equal(f.rows().find(row => row.dataset.id === anchor.rowId).getBoundingClientRect().top, anchor.offset);
  density.value = 'comfortable'; density.fire('change'); f.drainFrames();
  assert.ok(viewport.scrollWidth < 1000, 'density is part of the bounded display-mode key');
  density.value = 'compact'; density.fire('change'); f.drainFrames();
  assert.equal(viewport.scrollWidth, width); assert.equal(viewport.scrollLeft, 1800);
  wrap.value = 'wrap'; wrap.fire('change'); f.drainFrames();
  assert.equal(viewport.scrollLeft, 130);
  assert.equal(f.rows().length, 250); assert.equal(f.box(1), undefined);
});

test('a failed submitted query never retries on middle-of-table wheel or viewport Arrow browsing', async () => {
  for (const action of ['wheel', 'keyboard']) {
    const f = harness({ geometry: true }); f.network(paged); await f.search(2);
    const viewport = f.element('flowTableScroll'); viewport.scrollTop = 500;
    f.element('fAccount').value = 'ATTEMPT'; f.network(() => { throw new Error('合成查询断网'); });
    assert.equal(await f.search(), false); const requests = f.requests.length;
    f.element('fAccount').value = 'LATER'; f.network(paged);
    if (action === 'wheel') {
      viewport.fire('wheel', { deltaY: 80 }); viewport.scrollTop = 580; viewport.fire('scroll');
    } else {
      viewport.focus(); const event = viewport.fire('keydown', { key: 'ArrowDown' }); assert.equal(event.defaultPrevented, false);
    }
    await flush(); assert.equal(f.requests.length, requests); assert.equal(f.rows()[0].dataset.id, '51');
    if (action === 'wheel') await wheelEdge(f, 1);
    else { viewport.scrollTop = 0; viewport.fire('keydown', { key: 'ArrowUp' }); await flush(); }
    assert.equal(f.requests.length, requests + 1);
    assert.equal(new URL(f.requests.at(-1), 'http://synthetic.local').searchParams.get('entity'), 'ATTEMPT');
    assert.equal(f.element('fAccount').value, 'LATER');
  }
});

test('horizontal wheel and Shift scrolling plus native scroll events never fetch vertical pages', async () => {
  const f = harness({ geometry: true }); f.network(paged); await f.search(2);
  const viewport = f.element('flowTableScroll');
  viewport.fire('wheel', { deltaX: 80, deltaY: 0 }); viewport.scrollLeft = 80; viewport.fire('scroll'); await flush();
  assert.equal(f.requests.length, 1); assert.equal(viewport.scrollTop, 0); assert.equal(f.rows()[0].dataset.id, '51');
  viewport.fire('wheel', { deltaY: 80, shiftKey: true }); viewport.scrollLeft = 160; viewport.fire('scroll'); await flush();
  assert.equal(f.requests.length, 1); assert.equal(viewport.scrollTop, 0);
  viewport.scrollTop = 13; viewport.fire('scroll'); await flush();
  assert.deepEqual(f.requests.map(path => Number(new URL(path, 'http://synthetic.local').searchParams.get('page'))), [2, 1]);
  const compensated = viewport.scrollTop; assert.ok(compensated > 13);
  viewport.scrollLeft = 200; viewport.fire('scroll'); viewport.fire('scroll'); await flush();
  assert.equal(f.requests.length, 2, 'the compensated baseline also ignores later horizontal and programmatic scroll events');
  assert.equal(viewport.scrollTop, compensated); assert.equal(viewport.scrollLeft, 200);
});
