import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/workflow-ui.js', import.meta.url), 'utf8');
const tableScroll = readFileSync(new URL('../static/table-scroll.js', import.meta.url), 'utf8');
const mainAccountsSource = readFileSync(new URL('../static/main-accounts.js', import.meta.url), 'utf8');
const flush = async () => { for (let i = 0; i < 12; i += 1) await Promise.resolve(); };
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const response = (value, status = 200) => ({ ok: status === 200, status,
  json: async () => value, text: async () => JSON.stringify(value) });
const preview = id => ({ import_id: id, filename: `${id}.csv`, mode: 'A', mapping: { amount: '合成金额' },
  self_accounts_in_file: [{ account: 'SYN-A', name: '合成本方', count: 2 }] });
const done = () => response({ stage: 'done', result: { ok: 2, rows_dup: 1 } });
const settle = () => new Promise(setImmediate);
const queueStates = h => h.element('impQueueList').children.map(row => row.dataset.state);

function harness(search = '') {
  const nodes = new Map(), events = new Map(), timers = [], uploads = [], requests = [], notices = [], rendered = [], contextRows = [], photoOpens = [], mainStates = [], mainPublications = [];
  let fetcher = () => { throw new Error('Unexpected request'); }, tracker = async () => true, graphLoader = null, graphRenderer = null, refresher = null, refreshes = 0, graphReads = 0, paints = 0;
  let document, mappingNodes = [], mainNodes = [];
  function element(id) {
    id = id.replace(/^#/, '');
    if (nodes.has(id)) return nodes.get(id);
    const classes = new Set(), listeners = new Map(), attributes = {};
    const dataset = new Proxy({}, { set(object, key, value) {
      object[key] = String(value);
      attributes['data-' + String(key).replace(/[A-Z]/g, letter => '-' + letter.toLowerCase())] = String(value);
      return true;
    } });
    const node = { id, value: '', files: [], style: { display: 'none' }, disabled: false, hidden: false,
      textContent: '', innerHTML: '', children: [], attributes, dataset, checked: true, isConnected: true, tagName: 'DIV',
      classList: { add: (...values) => values.forEach(value => classes.add(value)), remove: (...values) => values.forEach(value => classes.delete(value)), contains: value => classes.has(value),
        toggle(value, force) { const on = force ?? !classes.has(value); if (on) classes.add(value); else classes.delete(value); return on; } },
      setAttribute(name, value) {
        attributes[name] = String(value);
        if (name.startsWith('data-')) dataset[name.slice(5).replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase())] = value;
      },
      getAttribute(name) { return attributes[name] ?? null; },
      removeAttribute(name) { delete attributes[name]; },
      append(...items) { this.children.push(...items); for (const child of items) { child.parent = this; if (child.id) nodes.set(child.id, child); } },
      replaceChildren(...items) { this.children = []; this.append(...items); },
      querySelectorAll(selector) { return [...nodes.values()].filter(child => child !== this && this.contains(child) && child.matches(selector)); },
      querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
      contains(other) { for (let value = other; value; value = value.parent) if (value === this) return true; return false; },
      focus() { if (!this.disabled && this.isConnected) document.activeElement = this; },
      after(child) { if (child.id) nodes.set(child.id, child); },
      matches(selector) { return selector.split(',').some(part => {
        part = part.trim();
        if (part.startsWith('#')) return this.id === part.slice(1);
        const match = /^([a-z]+)?(?:\[([\w-]+)(?:=["']?([^"'\]]*)["']?)?\])?$/.exec(part);
        if (!match) return false;
        if (match[1] && this.tagName !== match[1].toUpperCase()) return false;
        if (!match[2]) return true;
        const value = this.getAttribute(match[2]) ?? this[match[2]];
        return match[3] === undefined ? value !== undefined && value !== null : value === match[3];
      }); },
      closest(selector) { for (let value = this; value; value = value.parent) if (value.matches(selector)) return value; return null; },
      addEventListener(type, listener) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(listener); },
      dispatchEvent(event) {
        event.target ||= this;
        for (let current = this; current; current = event.bubbles ? current.parent : null) {
          event.currentTarget = current;
          current['on' + event.type]?.(event);
          for (const listener of current._listeners.get(event.type) || []) listener.call(current, event);
        }
        return true;
      },
      click() { if (!this.disabled) this.onclick?.({ type: 'click', target: this }); },
      _listeners: listeners,
      showModal() { this.open = true; Promise.resolve().then(() => this.close('confirm')); },
      close(value = '') { this.returnValue = value; this.open = false; this.onclose?.(); },
    };
    let markup = '';
    Object.defineProperty(node, 'innerHTML', { get: () => markup, set(value) {
      markup = value;
      if (id === 'impMapping' || id === 'impMainSel') {
        const removed = id === 'impMapping' ? mappingNodes : mainNodes;
        for (const child of removed) { child.isConnected = false; nodes.delete(child.id); }
        if (id === 'impMapping') mappingNodes = []; else mainNodes = [];
      }
      // A real innerHTML replacement removes focused controls, implicitly
      // blurring to BODY. New controls must be separate node identities.
      if (id === 'drawerBody') for (const [key, child] of nodes) {
        if (child !== node && node.contains(child)) {
          child.isConnected = false;
          if (document?.activeElement === child) document.activeElement = document.body;
          nodes.delete(key);
        }
      }
      if (id === 'drawerBody') {
        // The account workspace reads real replacement input values and action
        // attributes. Preserve separate control identities without inventing
        // children that are absent from its template.
        for (const [, tag, attributes, childId] of String(value).matchAll(/<([a-z]+)\b([^>]*\bid="([^"]+)"[^>]*)>/gi)) {
          const child = element(childId); child.tagName = tag.toUpperCase(); child.parent = node;
          for (const [, name, attrValue] of attributes.matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
            child.setAttribute(name, attrValue ?? '');
            if (name === 'value') child.value = attrValue;
            if (name === 'disabled') child.disabled = true;
          }
          if (tag.toLowerCase() === 'span') child.textContent = new RegExp('<span\\b[^>]*\\bid="' + childId + '"[^>]*>([^<]*)</span>').exec(value)?.[1] || '';
        }
      }
    } });
    let disabled = false;
    Object.defineProperty(node, 'disabled', { get: () => disabled, set(value) {
      disabled = value;
      if (value && document?.activeElement === node) document.activeElement = document.body;
    } });
    nodes.set(id, node);
    if (id === 'drawerBody' || id === 'btnCloseDrawer' || id === 'nodeMenu' || id === 'nodePhotoMenu') node.parent = element('drawer');
    else if (id === 'ndPhotoClr' || id === 'ndPhotoRe') node.parent = element('nodePhotoMenu');
    else if (id === 'ndRemove') node.parent = element('nodeMenu');
    else if (id.startsWith('ndConfirm')) node.parent = element('nodeConfirm');
    else if (id.startsWith('nd')) node.parent = element('drawerBody');
    else if (id === 'context-row') { node.tagName = 'TR'; node.parent = element('ctxTbody'); }
    else if (id.startsWith('context-check')) { node.tagName = 'INPUT'; node.parent = element('context-row'); }
    else if (id === 'mapping-select' || id.startsWith('mapping-')) {
      node.tagName = 'SELECT'; node.dataset.field = id === 'mapping-select' ? 'amount' : id.slice('mapping-'.length); node.parent = element('impMapping');
    }
    else if (id.startsWith('imp-mode-')) { node.tagName = 'INPUT'; node.name = 'impMode'; node.value = id.at(-1); node.parent = element('impStep2'); }
    else if (id.startsWith('imp') && id !== 'importModal') node.parent = element('importModal');
    return node;
  }
  const state = { caseId: 1, canvasId: 0, canvasGeneration: 1, caseGeneration: 1, cases: [{ id: 1, name: '合成案件' }], imp: null,
    graphView: { revision: 0, selectedEdge: null }, edgeTypes: {}, edgeType: 'quadratic', graphLoading: false,
    lastGraph: { nodes: [{ account: 'SYN-A', photo_at: 'SYN-PHOTO' }], edges: [] }, focusNodes: new Map(), tracked: new Set(), ctx: null };
  const fetch = (path, options) => { requests.push({ path, options }); return fetcher(path, options); };
  document = { createElement: () => element(`created-${nodes.size}`), getElementById: id => nodes.get(id) || null,
    body: element('body'), documentElement: element('html'), addEventListener() {}, removeEventListener() {} };
  document.activeElement = document.body;
  const modeControls = () => ['A', 'B'].map(mode => element(`imp-mode-${mode}`));
  const mappingControls = () => mappingNodes;
  const mainControls = () => mainNodes;
  const contextControls = () => {
    const rows = state.ctx?.data?.rows;
    const ids = Array.isArray(rows) ? rows.filter(row => !state.tracked.has(row.id)).map(row => row.id) : [17];
    return ids.map((id, index) => { const node = element(index ? `context-check-${index}` : 'context-check'); node.dataset.id = String(id); return node; });
  };
  function renderMapping(result) {
    // Rendering a different mode replaces selects with just that mode's
    // supported fields. Removed controls cannot silently retain draft values.
    for (const node of [...mappingNodes, ...mainNodes]) { node.isConnected = false; nodes.delete(node.id); }
    const shared = ['trade_no', 'trade_time', '_time2', 'self_account', 'amount', 'balance', 'currency', 'summary', 'channel'];
    const modeFields = result.mode === 'B'
      ? ['self_name', 'counter_name', 'counter_account', 'counter_bank', 'direction', 'income_amt', 'expense_amt']
      : ['payer_name', 'payer_account', 'payer_bank', 'payee_name', 'payee_account', 'payee_bank'];
    mappingNodes = [...shared, ...modeFields].map(field => {
      const node = element(field === 'amount' ? 'mapping-select' : `mapping-${field}`);
      node.value = result.mapping?.[field] || ''; return node;
    });
    // app.js rebuilds the account checkboxes as checked on every render. A
    // mode-only orchestration rerender must explicitly preserve user choices.
    mainNodes = (result.self_accounts_in_file || []).map((account, index) => {
      const node = element(index ? `main-account-${index}` : 'main-account');
      node.value = account.account; node.checked = true; node.parent = element('impMainSel'); return node;
    });
  }
  const location = { search, href: 'http://synthetic.local/' + search };
  const context = vm.createContext({
    state, window: { location, history: { replaceState(_state, _title, url) {
      location.href = String(url); location.search = new URL(url).search;
    } }, addEventListener: (name, handler) => events.set(name, handler) },
    URLSearchParams, document,
    URL: class extends URL { static createObjectURL() { return 'blob:synthetic-workflow-photo'; } static revokeObjectURL() {} },
    // Dynamic account controls must exist in the current template. The setup
    // helper remains lazy so tests can add synthetic controls deliberately.
    $: selector => /^#nd/.test(selector) ? nodes.get(selector.slice(1)) || null : element(selector), $$: selector => selector === '#impMainSel .mk-acct' ? mainControls()
      : selector === '#ctxTbody .ctx-ck' ? contextControls()
        : selector === '#ctxTabs .stab' || selector === '#ctxSuggest .sg-item' ? []
          : selector === 'input[name=impMode]' ? modeControls()
            : selector === '#impMapping select' ? mappingControls()
              : selector === '#impMapping select,input[name=impMode],#impMainSel .mk-acct,#impSelfAccts'
                ? [...mappingControls(), ...modeControls(), ...mainControls(), element('impSelfAccts')]
                : [element('mapping-select'), element('impSelfAccts')],
    caseContext: () => ({ caseId: state.caseId, generation: state.caseGeneration, canvasId: state.canvasId, canvasGeneration: state.canvasGeneration }),
    isCurrentCase: (binding, canvas = true) => binding.caseId === state.caseId && binding.generation === state.caseGeneration
      && (!canvas || binding.canvasId === state.canvasId && binding.canvasGeneration === state.canvasGeneration),
    jsonOpts: body => ({ headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    collectMapping: () => ({ mode: modeControls().find(node => node.checked)?.value || 'A',
      mapping: Object.fromEntries(mappingControls().filter(node => node.value).map(node => [node.dataset.field, node.value])) }),
    renderImport: result => { rendered.push(result); element('impStep1').style.display = 'none'; element('impStep2').style.display = 'block';
      for (const node of modeControls()) node.checked = node.value === (result.mode || 'A'); renderMapping(result); },
    hidePbar: () => { element('impPbar').style.display = 'none'; },
    setPbar: (_fraction, message) => { element('impPbar').style.display = 'block'; element('impPtext').textContent = message; },
    setPbarBusy: message => { element('impPbar').style.display = 'block'; element('impPtext').textContent = message; },
    fmtCount: value => String(value), toast: (message, type) => notices.push({ message, type }),
    fmtTime: String, esc: String, nodePhotoUrl: () => '', totalsHtml: () => '', moneyHtml: String,
    drawerSequence: 0, _selItem: null, edgeWorkspace: null, layoutJob: null,
    scheduleGraphLabels() {}, syncGraphEdgeLabel() {},
    selectItem() {}, renderContext: () => { contextRows.push(state.ctx.data); context.syncContextSelection(); },
    openPhotoCrop: (...args) => photoOpens.push(args),
    trackFlows: ids => tracker(ids),
    loadGraph: async () => { graphReads += 1; if (graphLoader) await graphLoader(); },
    renderGraph: () => { paints += 1; graphRenderer?.(); },
    api: async (path, options) => { const result = await fetch(path, options); if (!result.ok) throw new Error('合成读取失败'); return result.json(); },
    refreshAll: async () => { refreshes += 1; await refresher?.(); },
    bindTableHorizontalScroll: () => ({ sync() {}, destroy() {} }),
    setTimeout: (callback, delay) => { timers.push({ callback, delay }); return timers.length; },
    clearTimeout: id => { if (timers[id - 1]) timers[id - 1].cancelled = true; },
    fetch,
    FormData: class {
      constructor() { this.entries = []; }
      append(...entry) { this.entries.push(entry); }
    },
    XMLHttpRequest: class {
      constructor() { this.upload = {}; uploads.push(this); }
      open(method, path) { this.method = method; this.path = path; }
      send(form) { this.form = form; }
      getResponseHeader(name) { return this.headers?.[name] ?? null; }
      finish(result, status = 200, headers = {}) { this.headers = headers; this.status = status; this.responseText = JSON.stringify(result); this.onload(); }
    },
  });
  const app = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
  for (const helper of ['money.js', 'principal-analysis.js']) {
    vm.runInContext(readFileSync(new URL('../static/' + helper, import.meta.url), 'utf8'), context);
  }
  context.window.FundsFacts = context.FundsFacts;
  vm.runInContext(app.slice(app.indexOf('function esc('), app.indexOf('function toast(')), context);
  vm.runInContext(app.slice(app.indexOf('function flowObservationIds('), app.indexOf('/* ---------------- 追踪上图')), context);
  vm.runInContext(readFileSync(new URL('../static/feedback-core.mjs', import.meta.url), 'utf8').replace('export function createFeedbackStore', 'function createFeedbackStore'), context);
  const feedback = vm.runInContext('createFeedbackStore()', context);
  feedback.bind(vm.runInContext('caseContext()', context)); context.window.FundsFeedback = feedback;
  // Load the complete account/crop dependencies. The former close-only section
  // marker no longer exists, and slicing to -1 silently included context state.
  for (const [start, end] of [['function syncEdgeTypeControl(', 'function findEdgeShapes('],
    ['function edgeKey(', 'function graphEdgeType('],
    ['let _selItem = null;', '/* ---------------- 交易上下文'],
    ['async function manageNodeMainAccount(', 'function installDataTools('],
    ['let contextTableScroll = null;', 'async function openContext('],
    ['async function loadContext()', 'function renderContext()']]) {
    vm.runInContext(app.slice(app.indexOf(start), app.indexOf(end, app.indexOf(start))), context);
  }
  // Existing workflow cases script an accepted confirmation. The dedicated
  // account harness exercises real Cancel, stale confirmation and menu events.
  for (const id of ['ndPhotoRe', 'ndPhotoClr', 'ndRemove', 'ndConfirmTitle', 'ndConfirmMessage', 'ndConfirmAccept', 'ndConfirmCancel', 'ndConfirmClose']) element(id);
  context.recordPhotoOpen = (...args) => photoOpens.push(args);
  vm.runInContext('selectItem = () => {}; const originalPhotoOpen = openPhotoCrop; openPhotoCrop = (...args) => { recordPhotoOpen(...args); return originalPhotoOpen(...args); }; bindNodeWorkspaceUI();', context);
  vm.runInContext(source, context, { filename: 'workflow-ui.js' });
  vm.runInContext(mainAccountsSource, context, { filename: 'main-accounts.js' });
  events.get('DOMContentLoaded')();
  return { element, state, uploads, requests, notices, feedback, rendered, timers, contextRows, document, photoOpens, mainStates, mainPublications,
    get refreshes() { return refreshes; },
    get graphReads() { return graphReads; }, get paints() { return paints; },
    network(handler) { fetcher = handler; },
    track(handler) { tracker = handler; },
    onGraphLoad(handler) { graphLoader = handler; },
    onGraphRender(handler) { graphRenderer = handler; },
    onRefresh(handler) { refresher = handler; },
    dataToolsBlocked(handler) { context.window.FundsDataToolsActions = { blocked: handler }; },
    mainAccountsBlocked(handler) { context.window.FundsMainAccountsActions = { blocked: handler }; },
    importAdmission(handler) { context.window.FundsImportWorkflow = { ...context.window.FundsImportWorkflow, canWrite: handler }; },
    enableMainAccounts() {
      // Exercise the real shared controller and the app's drawer entry adapter.
      // This view records state and applies the same drawer-control refresh as
      // installMainAccounts, without manufacturing the native panel DOM.
      context.window.FundsMainAccountsActions = context.window.FundsMainAccounts.create({
        context: context.caseContext, isCurrent: context.isCurrentCase,
        request: async (path, options) => {
          const result = await fetch(path, options);
          if (!result.ok) { const error = new Error('合成请求失败'); error.status = result.status; throw error; }
          return result.json();
        },
        publish: async (binding, rows, result) => {
          mainPublications.push({ binding: { ...binding }, rows: JSON.parse(JSON.stringify(rows)), result: JSON.parse(JSON.stringify(result)) });
        },
        show() {}, dismiss() {},
        onState(value) { mainStates.push(value); context.syncNodeWorkspaceControls(); },
      });
      return context.window.FundsMainAccountsActions;
    },
    canWrite(caseId = state.caseId) { return context.window.FundsImportWorkflow.canWrite({ caseId, generation: state.caseGeneration }); },
    run(code) { return vm.runInContext(code, context); },
    restore() { return vm.runInContext('window.FundsImportWorkflow.restore()', context); },
    get search() { return location.search; },
    open() { element('btnImport').onclick(); }, close() { element('impClose').onclick(); },
    upload(name) { element('impFile').files = [{ name }]; return element('impFile').onchange(); },
    uploadMultiple(names) { element('impFile').files = names.map(name => ({ name })); return element('impFile').onchange(); },
    uploadDirectory(paths) {
      element('impDirectory').files = paths.map(path => ({ name: path.split('/').at(-1), webkitRelativePath: path }));
      return element('impDirectory').onchange();
    },
    commit() { return element('impCommit').onclick(); },
    change(id, value) { const node = element(id); node.value = value;
      if (node.name === 'impMode') for (const option of modeControls()) option.checked = option === node;
      node.dispatchEvent({ type: 'change', bubbles: true }); },
    switchCase(id) { state.caseId = id; state.caseGeneration += 1; state.imp = null; element('importModal').style.display = 'none'; events.get('funds:feedback-binding')?.(); },
  };
}

async function ready(h, id = 'SYN-IMPORT') {
  h.open(); const result = h.upload(`${id}.csv`); h.uploads.at(-1).finish(preview(id)); await result;
}

const ctxResponse = (account, rows = [], id = 17) => ({
  anchor: { id, payee_name: account }, account, rows, review: { token: 'a'.repeat(32) },
});

test('context window changes publish only the latest response', async () => {
  const h = harness(), old = deferred(), latest = deferred();
  h.state.ctx = { flowId: 17, mode: 'all', x: 5, phase: 'ready', closed: false };
  h.element('ctxModal').style.display = 'flex';
  h.network(path => path.includes('x=5&') ? old.promise : latest.promise);
  const first = h.run('loadContext()');
  h.state.ctx.x = 9;
  const second = h.run('loadContext()');
  latest.resolve(response(ctxResponse('SYN-LATEST'))); await second;
  old.resolve(response(ctxResponse('SYN-OLD'))); await first;
  assert.deepEqual(h.contextRows.map(item => item.account), ['SYN-LATEST']);
  assert.match(h.element('ctxAnchor').innerHTML, /SYN-LATEST/);
  assert.equal(h.state.ctx.x, 9);
});

test('late node metadata cannot replace a more recently opened account drawer', async () => {
  const h = harness(), oldMain = deferred(); let mainReads = 0;
  h.network(path => path.includes('/SYN-A/profile') ? response({ name: '合成甲', top_counterparties: [] })
    : path.includes('/SYN-B/profile') ? response({ name: '合成乙', top_counterparties: [] })
      : ++mainReads === 1 ? oldMain.promise : response([]));
  const first = h.run("showNodeDrawer('SYN-A', {})"); await flush();
  await h.run("showNodeDrawer('SYN-B', {})");
  oldMain.resolve(response([])); await first;
  assert.match(h.element('drawerBody').innerHTML, /合成乙/, h.element('drawerBody').textContent);
  assert.equal(h.element('drawerBody').innerHTML.includes('合成甲'), false);
});

test('a pending edge drawer cannot overwrite a node drawer or reopen a closed drawer', async () => {
  const h = harness(), edge = deferred();
  h.network(path => path.startsWith('/api/flows?') ? edge.promise
    : path.includes('/profile?') ? response({ name: '合成乙', top_counterparties: [] }) : response([]));
  const old = h.run("showEdgeDrawer({ids:[17],data:{source:'SYN-A',target:'SYN-B'}},{})");
  await h.run("showNodeDrawer('SYN-B', {})");
  h.run('closeDrawer()');
  edge.resolve(response({ rows: [] })); await old;
  assert.equal(h.element('drawerBody').innerHTML, '', 'closing revokes the old detail DOM and late responses cannot refill it');
  assert.equal(h.element('drawer').classList.contains('open'), false);
});

const drawerWrites = [
  ['ndFocusApply', '/api/focus-nodes/set'], ['ndIdSave', '/api/account-ids/set'],
  ['ndPhotoClr', '/api/account-photos/clear'],
];

function mainDrawerFixture(h, { marked = false, receipt = null, search = null } = {}) {
  let records = marked ? [{ account: 'SYN-A', name: '已核对合成户名', source: 'auto' }] : [];
  const actions = h.enableMainAccounts();
  h.state.lastGraph.nodes = [{ account: 'SYN-A', name: '图谱别名', is_main: marked ? 1 : 0 }, { account: 'SYN-B', is_main: 0 }];
  h.network((path, options) => {
    if (path.includes('/profile?')) return response({ name: path.includes('/SYN-B/') ? '合成乙' : '导入合成户名', top_counterparties: [] });
    if (path.includes('/candidates?')) return search?.(path) || response({
      items: [{ account: 'SYN-A', name: '已核对合成户名', is_main: false, source: null }], total: 1, page: 1, page_size: 30,
    });
    if (!options?.method) return response(records.map(row => ({ ...row })));
    const body = JSON.parse(options.body);
    records = records.filter(row => row.account !== body.account);
    if (options.method !== 'DELETE') records.push({ account: body.account, name: body.name, source: 'manual' });
    return receipt?.promise || response(options.method === 'DELETE' ? { ok: true }
      : { ok: true, account: body.account, name: body.name, source: 'manual' });
  });
  return actions;
}

test('node main-account add uses the shared search controller and its authoritative account identity', async () => {
  const h = harness(), actions = mainDrawerFixture(h);
  await h.run("showNodeDrawer('SYN-A', {})");
  await h.element('ndToggleMain').onclick();
  const writes = h.requests.filter(item => item.options?.method);
  assert.equal(writes.length, 1); assert.equal(writes[0].path, '/api/main-accounts');
  assert.equal(writes[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(writes[0].options.body), { case_id: 1, account: 'SYN-A', name: '已核对合成户名' });
  assert.equal(h.mainPublications.length, 1); assert.equal(h.mainPublications[0].binding.caseId, 1);
  assert.equal(h.mainPublications[0].rows[0].source, 'manual');
  assert.equal(actions.status().screen, 'list'); assert.equal(actions.status().phase, 'done');
  assert.equal(h.graphReads, 0); assert.equal(h.paints, 0, 'main roles use metadata publication rather than rebuilding the graph');
});

test('node main-account removal requires shared confirmation and Cancel never sends a delete', async () => {
  const h = harness(), actions = mainDrawerFixture(h, { marked: true });
  await h.run("showNodeDrawer('SYN-A', {})");
  await h.element('ndToggleMain').onclick();
  assert.equal(actions.status().screen, 'confirm');
  assert.equal(actions.status().confirmation.action, 'remove');
  assert.equal(actions.status().confirmation.account, 'SYN-A');
  assert.equal(h.requests.filter(item => item.options?.method).length, 0);
  assert.equal(actions.cancelConfirm(), true);
  assert.equal(h.requests.filter(item => item.options?.method).length, 0);
  await h.element('ndToggleMain').onclick();
  assert.equal(await actions.submit(), true);
  const writes = h.requests.filter(item => item.options?.method);
  assert.equal(writes.length, 1); assert.equal(writes[0].options.method, 'DELETE');
  assert.deepEqual(JSON.parse(writes[0].options.body), { case_id: 1, account: 'SYN-A', name: '已核对合成户名' });
  assert.equal(h.mainPublications.length, 1); assert.deepEqual(h.mainPublications[0].rows, []);
  assert.equal(h.element('ndToggleMain').disabled, false);
});

test('pending node main-account additions lock duplicate clicks and keep the original case across A-B-A', async () => {
  const h = harness(), receipt = deferred(), actions = mainDrawerFixture(h, { receipt });
  await h.run("showNodeDrawer('SYN-A', {})");
  const first = h.element('ndToggleMain').onclick(); await flush();
  assert.equal(actions.status().pending, true); assert.equal(h.element('ndToggleMain').disabled, true);
  await h.element('ndToggleMain').onclick();
  h.switchCase(2); h.switchCase(1);
  receipt.resolve(response({ ok: true, account: 'SYN-A', source: 'manual' })); await first;
  const writes = h.requests.filter(item => item.options?.method);
  assert.equal(writes.length, 1);
  assert.deepEqual(JSON.parse(writes[0].options.body), { case_id: 1, account: 'SYN-A', name: '已核对合成户名' });
  assert.equal(h.mainPublications.length, 0); assert.equal(actions.status().visible, false);
  assert.equal(h.graphReads, 0); assert.equal(h.paints, 0); assert.equal(h.notices.length, 0);
  assert.equal(h.state.lastGraph.nodes[0].is_main, 0, 'the stale receipt cannot update the returning case graph');
});

test('a late main-account write cannot replace the newly opened drawer or leave its controls locked', async () => {
  const h = harness(), receipt = deferred(), actions = mainDrawerFixture(h, { receipt });
  await h.run("showNodeDrawer('SYN-A', {})");
  const first = h.element('ndToggleMain').onclick(); await flush();
  assert.equal(actions.status().pending, true);
  await h.run("showNodeDrawer('SYN-B', {})");
  const button = h.element('ndToggleMain');
  assert.equal(button.disabled, true, 'the shared case write locks the replacement drawer too');
  receipt.resolve(response({ ok: true, account: 'SYN-A', source: 'manual' })); await first;
  assert.equal(h.requests.filter(item => item.options?.method).length, 1);
  assert.equal(h.mainPublications.length, 1, 'the unchanged case may receive its authoritative role update');
  assert.equal(h.element('ndToggleMain'), button); assert.equal(button.disabled, false);
  assert.match(h.element('drawerBody').innerHTML, /合成乙/);
  assert.equal(h.element('ndStatus').textContent.includes('已保存'), false, 'the old action cannot write success feedback into another account');
  assert.equal(h.graphReads, 0); assert.equal(h.paints, 0); assert.equal(h.notices.length, 0);
});

test('switching node drawers before the shared candidate search resolves prevents an automatic stale add', async () => {
  const h = harness(), search = deferred(), actions = mainDrawerFixture(h, { search: () => search.promise });
  await h.run("showNodeDrawer('SYN-A', {})");
  const first = h.element('ndToggleMain').onclick(); await flush();
  assert.equal(actions.status().searching, true);
  await h.run("showNodeDrawer('SYN-B', {})");
  const button = h.element('ndToggleMain'), disabled = button.disabled;
  search.resolve(response({ items: [{ account: 'SYN-A', name: '已核对合成户名', is_main: false, source: null }], total: 1, page: 1, page_size: 30 }));
  await first;
  assert.equal(h.requests.filter(item => item.options?.method).length, 0);
  assert.equal(h.mainPublications.length, 0);
  assert.match(h.element('drawerBody').innerHTML, /合成乙/);
  assert.equal(h.element('ndToggleMain'), button); assert.equal(button.disabled, disabled);
  assert.equal(h.graphReads, 0); assert.equal(h.paints, 0); assert.equal(h.notices.length, 0);
});

test('focus, identity and photo drawer writes keep their original case binding through delayed A-B-A navigation', async () => {
  for (const [id, endpoint] of drawerWrites) {
    const h = harness(), write = deferred();
    h.state.lastGraph.nodes = [{ account: 'SYN-A', id_no: 'SYN-UNCHANGED', is_main: 0, photo_at: 'SYN-PHOTO' }];
    h.network((path, options) => options?.method === 'POST' || options?.method === 'DELETE' ? write.promise
      : path.includes('/profile?') ? response({ name: '合成甲', top_counterparties: [] }) : response([]));
    await h.run("showNodeDrawer('SYN-A', {})");
    h.element('ndFocusScale').value = '2'; h.element('ndIdInput').value = 'SYN-ID';
    const first = h.element(id).onclick(); await h.element(id).onclick();
    assert.equal(h.element(id).disabled, true, id);
    h.switchCase(2); h.switchCase(1);
    write.resolve(response({ on: true, scale: 2 })); await first;
    const writes = h.requests.filter(item => item.options?.method === 'POST' || item.options?.method === 'DELETE');
    assert.equal(writes.length, 1, id); assert.equal(writes[0].path, endpoint, id);
    assert.equal(JSON.parse(writes[0].options.body).case_id, 1, id);
    assert.equal(JSON.parse(writes[0].options.body).account, 'SYN-A', id);
    assert.equal(h.graphReads, 0, id); assert.equal(h.paints, 0, id);
    assert.equal(h.state.lastGraph.nodes[0].id_no, 'SYN-UNCHANGED', id);
    assert.equal(h.notices.length, 0, id);
  }
});

test('late drawer writes cannot replace a newly opened account or change its control state', async () => {
  for (const [id] of drawerWrites) {
    const h = harness(), write = deferred();
    h.network((path, options) => options?.method === 'POST' || options?.method === 'DELETE' ? write.promise
      : path.includes('/SYN-A/profile?') ? response({ name: '合成甲', top_counterparties: [] })
        : path.includes('/SYN-B/profile?') ? response({ name: '合成乙', top_counterparties: [] }) : response([]));
    await h.run("showNodeDrawer('SYN-A', {})");
    h.element('ndFocusScale').value = '2'; h.element('ndIdInput').value = 'SYN-ID';
    const first = h.element(id).onclick();
    await flush(); // Photo removal first awaits the scripted user confirmation.
    await h.run("showNodeDrawer('SYN-B', {})");
    const freshButton = h.element(id), disabled = freshButton.disabled;
    write.resolve(response({ on: true, scale: 2 })); await first;
    assert.match(h.element('drawerBody').innerHTML, /合成乙/, id);
    assert.equal(freshButton.disabled, disabled, id);
    assert.equal(h.graphReads, id === 'ndIdSave' ? 0 : 1, id); assert.equal(h.paints, id === 'ndIdSave' ? 1 : 0, id);
    assert.equal(h.notices.length, 0, id);
  }
});

test('drawer adapters preserve focus, identity and photo success behavior', async () => {
  for (const [id, endpoint] of drawerWrites) {
    const h = harness();
    h.state.lastGraph.nodes = [{ account: 'SYN-A', name: '图谱别名', id_no: null, is_main: 1, photo_at: 'SYN-PHOTO' }];
    h.network((path, options) => options?.method === 'POST' || options?.method === 'DELETE' ? response({ on: true, scale: 2 })
      : path.includes('/profile?') ? response({ name: '导入合成户名', top_counterparties: [] }) : response([]));
    await h.run("showNodeDrawer('SYN-A', {})");
    h.element('ndFocusScale').value = '2'; h.element('ndIdInput').value = 'SYN-ID';
    await h.element(id).onclick();
    const write = h.requests.find(item => item.options?.method === 'POST' || item.options?.method === 'DELETE');
    const payload = JSON.parse(write.options.body);
    assert.equal(write.path, endpoint, id); assert.equal(payload.case_id, 1, id); assert.equal(payload.account, 'SYN-A', id);
    assert.equal(h.element(id).disabled, id === 'ndIdSave' || id === 'ndFocusApply', id);
    assert.match(h.element('drawerBody').innerHTML, /导入合成户名/, id);
    if (id === 'ndFocusApply') { assert.equal(payload.scale, 2); assert.equal(payload.on, true); assert.equal(h.graphReads, 1); }
    if (id === 'ndIdSave') { assert.equal(payload.id_no, 'SYN-ID'); assert.equal(h.state.lastGraph.nodes[0].id_no, 'SYN-ID'); assert.equal(h.paints, 1); }
    if (id === 'ndPhotoClr') { assert.equal(h.state.forceRebuild, true); assert.equal(h.graphReads, 1); }
    assert.match(h.element('ndStatus').textContent, /已保存/, id);
  }
});

test('drawer rebuilds restore implicit blur without stealing deliberate focus or crossing requests', async () => {
  const h = harness();
  const profile = () => response({ name: '合成甲', top_counterparties: [] });
  h.network((path, options) => options?.method === 'POST' ? response({}) : path.includes('/profile?') ? profile() : response([]));
  await h.run("showNodeDrawer('SYN-A', {})");
  h.element('ndIdInput').value = 'SYN-ID'; h.element('ndIdSave').focus();
  await h.element('ndIdSave').onclick();
  assert.equal(h.document.activeElement, h.element('ndIdInput'), 'a saved disabled action returns focus to its editable field');

  let pending = deferred();
  h.network(path => path.includes('/profile?') ? pending.promise : response([]));
  h.element('ndPhotoUp').focus();
  let reload = h.run("showNodeDrawer('SYN-A', {})");
  assert.equal(h.document.activeElement, h.document.body, 'template removal really blurs the old photo control');
  pending.resolve(profile()); await reload;
  assert.equal(h.document.activeElement, h.element('ndPhotoUp'), 'photo-modal return focus survives the following rebuild');

  pending = deferred(); h.element('ndIdInput').focus();
  reload = h.run("showNodeDrawer('SYN-A', {})"); h.element('btnImport').focus();
  pending.resolve(profile()); await reload;
  assert.equal(h.document.activeElement, h.element('btnImport'), 'an intentional move outside the drawer wins');

  h.network(path => path.includes('/profile?') ? profile() : response([]));
  h.element('ndGone').focus(); await h.run("showNodeDrawer('SYN-A', {})");
  assert.equal(h.document.activeElement, h.element('btnCloseDrawer'), 'missing replacement has a reachable fallback');

  pending = deferred();
  h.network(path => path.includes('/SYN-A/profile?') ? pending.promise : path.includes('/profile?') ? profile() : response([]));
  h.element('ndIdInput').focus(); reload = h.run("showNodeDrawer('SYN-A', {})");
  await h.run("showNodeDrawer('SYN-B', {})"); pending.resolve(profile()); await reload;
  assert.equal(h.document.activeElement, h.document.body, 'a later account request revokes the earlier focus restoration');

  pending = deferred(); h.network(path => path.includes('/profile?') ? pending.promise : response([]));
  h.element('ndIdInput').focus();
  reload = h.run("showNodeDrawer('SYN-B', {})"); h.switchCase(2); h.element('btnImport').focus();
  pending.resolve(profile()); await reload;
  assert.equal(h.document.activeElement, h.element('btnImport'), 'a later case binding revokes focus restoration');
});

test('graph-rebuild drawer refresh restores the saved action focus and binds photo actions to the replacement item', async () => {
  const h = harness();
  h.network((path, options) => options?.method === 'POST' ? response({})
    : path.includes('/profile?') ? response({ name: '合成甲', top_counterparties: [] }) : response([]));
  h.run("globalThis.currentNodeItem = { generation: 1 }; globalThis.previousNodeItem = currentNodeItem; state.graph = { findById: () => currentNodeItem };");
  await h.run("showNodeDrawer('SYN-A', previousNodeItem)");
  h.onGraphLoad(async () => {
    // renderGraph revokes the old drawer before rebuilding G6 items, then
    // reopens the selected account after the binding-scoped graph publication.
    h.run('closeDrawer(false); state.graphView.revision += 1; currentNodeItem = { generation: 2 };');
    await h.run("showNodeDrawer('SYN-A', previousNodeItem)");
  });
  const oldButton = h.element('ndPhotoClr'); h.element('ndPhotoMore').focus();
  await oldButton.onclick();
  assert.equal(h.document.activeElement, h.element('ndPhotoUp'), 'photo replacement remains reachable after the graph replaces its drawer DOM');
  assert.equal(h.element('ndPhotoClr'), oldButton, 'photo menu controls remain outside replacement drawer content');
  assert.equal(h.graphReads, 1);
  assert.equal(h.requests.filter(request => request.path.includes('/profile?')).length, 2, 'the revoked write adapter must not reopen a third drawer');
  h.element('ndPhotoFile').files = [{ name: 'synthetic.png' }];
  h.element('ndPhotoFile').onchange();
  assert.equal(h.photoOpens.at(-1)[2], h.run('currentNodeItem'), 'photo callbacks use the live G6 item');
  assert.notEqual(h.photoOpens.at(-1)[2], h.run('previousNodeItem'));
});

test('opening node details resolves the current graph item before installing photo callbacks', async () => {
  const h = harness();
  h.network(path => path.includes('/profile?') ? response({ name: '合成甲', top_counterparties: [] }) : response([]));
  h.run("globalThis.currentNodeItem = { generation: 2 }; globalThis.previousNodeItem = { generation: 1 }; state.graph = { findById: () => currentNodeItem };");
  await h.run("showNodeDrawer('SYN-A', previousNodeItem)");
  h.element('ndPhotoFile').files = [{ name: 'synthetic.png' }]; h.element('ndPhotoFile').onchange();
  assert.equal(h.photoOpens.at(-1)[2], h.run('currentNodeItem'));
});

test('identity save retains success feedback and focus when renderGraph refreshes the drawer', async () => {
  const h = harness(); let reopened;
  h.state.lastGraph.nodes = [{ account: 'SYN-A' }];
  h.network((path, options) => options?.method === 'POST' ? response({})
    : path.includes('/profile?') ? response({ name: '合成甲', top_counterparties: [] }) : response([]));
  await h.run("showNodeDrawer('SYN-A', {})");
  h.onGraphRender(() => {
    h.run('closeDrawer(false); state.graphView.revision += 1;');
    reopened = h.run("showNodeDrawer('SYN-A', {})");
  });
  h.element('ndIdInput').value = 'SYN-ID'; h.element('ndIdSave').focus();
  await h.element('ndIdSave').onclick(); await reopened;
  assert.equal(h.state.lastGraph.nodes[0].id_no, 'SYN-ID');
  assert.equal(h.document.activeElement, h.element('ndIdInput'));
  assert.match(h.element('ndStatus').textContent, /已保存/);
  assert.equal(h.requests.filter(request => request.path.includes('/profile?')).length, 2);
});

test('graph-rebuild focus restoration respects a deliberate move to the graph or another control', async () => {
  for (const target of ['graphContainer', 'btnImport']) {
    const h = harness(), profile = deferred(); let reloading = false;
    h.network((path, options) => options?.method === 'POST' ? response({})
      : path.includes('/profile?') ? reloading ? profile.promise : response({ name: '合成甲', top_counterparties: [] }) : response([]));
    await h.run("showNodeDrawer('SYN-A', {})");
    h.onGraphLoad(async () => {
      h.run('closeDrawer(false); state.graphView.revision += 1;');
      reloading = true;
      await h.run("showNodeDrawer('SYN-A', {})");
    });
    h.element('ndFocusScale').value = '2'; h.element('ndFocusApply').focus();
    const pending = h.element('ndFocusApply').onclick(); await flush();
    h.element(target).focus();
    profile.resolve(response({ name: '合成甲', top_counterparties: [] })); await pending;
    assert.equal(h.graphReads, 1, 'the focus assertion must observe a real graph refresh');
    assert.equal(h.document.activeElement, h.element(target), `${target} retains intentional focus during graph refresh`);
  }
});

test('a graph-refresh focus marker cannot survive explicit close, another account, or a new case binding', async () => {
  for (const navigation of ["closeDrawer()", "showNodeDrawer('SYN-B', {})", 'select another case']) {
    const h = harness();
    h.network(path => path.includes('/profile?') ? response({ name: '合成账户', top_counterparties: [] }) : response([]));
    await h.run("showNodeDrawer('SYN-A', {})");
    h.element('ndIdInput').focus(); h.run('closeDrawer(false)');
    if (navigation === 'select another case') { h.switchCase(2); h.switchCase(1); }
    else await h.run(navigation);
    await h.run("showNodeDrawer('SYN-A', {})");
    assert.equal(h.document.activeElement, h.document.body, navigation);
  }
});

test('context read failures expose an explicit retry in the existing table', async () => {
  const h = harness(); h.state.ctx = { flowId: 17, mode: 'all', x: 5, phase: 'ready', closed: false };
  h.element('ctxModal').style.display = 'flex'; h.network(() => response({}, 503));
  await h.run('loadContext()');
  const cell = h.element('ctxTbody').children[0].children[0];
  assert.match(cell.textContent, /加载失败/);
  assert.equal(cell.children[0].textContent, '重新加载明细');
  h.network(() => response(ctxResponse('SYN-RETRY')));
  await cell.children[0].onclick();
  assert.equal(h.contextRows.at(-1).account, 'SYN-RETRY');
  assert.equal(h.element('ctxTrack').disabled, true, 'a successful empty retry cannot enable tracking');
});

test('context selection stays open on a rejected/no-op tracking command and closes only after success', async () => {
  const h = harness(); h.state.ctx = { flowId: 17, mode: 'all', phase: 'ready', closed: false, data: ctxResponse('SYN-CURRENT', [{id: 17}]) }; h.element('ctxModal').style.display = 'flex';
  h.track(async () => false); await h.element('ctxTrack').onclick();
  assert.equal(h.element('ctxModal').style.display, 'flex');
  assert.equal(h.element('context-check').checked, true);
  assert.match(h.element('ctxTrackStatus').textContent, /已保留勾选/);
  h.track(async () => true); await h.element('ctxTrack').onclick();
  assert.equal(h.element('ctxModal').style.display, 'none');
});

test('late tracking completion does not close a newly selected context or a different canvas', async () => {
  const h = harness(), tracking = deferred(); h.state.ctx = { flowId: 17, mode: 'all', phase: 'ready', closed: false, data: ctxResponse('SYN-CURRENT', [{id: 17}]) };
  h.element('ctxModal').style.display = 'flex'; h.track(() => tracking.promise);
  const first = h.element('ctxTrack').onclick();
  h.state.canvasId = 9; h.state.canvasGeneration += 1;
  h.state.ctx = { flowId: 18, mode: 'all', x: 5, phase: 'ready', closed: false };
  h.network(() => response(ctxResponse('SYN-NEW', [{id: 18}], 18)));
  await h.run('loadContext()'); tracking.resolve(true); await first;
  assert.equal(h.element('ctxModal').style.display, 'flex');
  assert.equal(h.element('ctxTrack').disabled, false);
});

// These cases render empty result categories; the separate analysis workspace
// harness covers table ancestry and scrolling. Keep every analysis function real.
function analysisFixture(algorithms, network = async () => response([])) {
  const nodes = new Map(), requests = [];
  let document;
  const element = selector => {
    if (!nodes.has(selector)) {
      const attributes = new Map();
      nodes.set(selector, { style: {}, dataset: {}, classList: { toggle() {} }, value: '', innerHTML: '', disabled: false,
        isConnected: true, open: false, returnValue: '', textContent: '',
        setAttribute(name, value) { attributes.set(name, String(value)); },
        getAttribute(name) { return attributes.get(name) ?? null; }, removeAttribute(name) { attributes.delete(name); },
        querySelectorAll() { return []; }, contains(node) { return node === this; },
        focus() { document.activeElement = this; }, click() { return this.onclick?.(); },
        showModal() { this.open = true; }, close(value = '') { this.returnValue = value; this.open = false; this.onclose?.(); },
        getContext: () => ({ translate() {}, rotate() {}, fillText() {} }), toDataURL: () => 'data:image/png;base64,',
      });
    }
    return nodes.get(selector);
  };
  for (const [id, value] of [['cycDays','7'],['ffWin','30'],['ffTol','25'],['pHops','4']]) element('#'+id).value = value;
  document = { querySelector: element, querySelectorAll: selector => selector === '.alg' ? algorithms
    : selector === '#analysisParams input, #analysisParams textarea' ? [...algorithms, ...['subjects','cycDays','ffWin','ffTol','pSrc','pDst','pHops'].map(id=>element('#'+id))]
      : selector === '#analysisParams [aria-invalid]' ? [...nodes.values()].filter(node=>node.getAttribute('aria-invalid') !== null) : [],
    addEventListener() {}, createElement: tag => element(`created-${tag}`), body: element('body') };
  document.activeElement = document.body;
  const location = new URL('http://synthetic.invalid/analysis?case_id=1');
  const context = vm.createContext({ document, window: { location, addEventListener() {}, history: { replaceState(_state,_title,url) { location.href = String(url); } } },
    fetch: async (path, options) => { requests.push({path,options}); return network(path,options); },
    localStorage: { getItem: () => null, setItem() {} }, URLSearchParams, URL, Blob, setTimeout() {} });
  vm.runInContext(readFileSync(new URL('../static/money.js', import.meta.url), 'utf8'), context, { filename: 'money.js' });
  vm.runInContext(tableScroll, context, { filename: 'table-scroll.js' });
  vm.runInContext(readFileSync(new URL('../static/analysis.js', import.meta.url), 'utf8'), context, { filename: 'analysis.js' });
  return { element, requests, run: code => vm.runInContext(code, context) };
}

test('analysis subject labels preserve markup as plain text in every selected algorithm', async () => {
  const selected = ['cycles','fast_flows','outliers'];
  const h = analysisFixture(selected.map(value=>({value,checked:true}))); await flush();
  h.run(`render({subjects:['<b>SYN-A</b> & "B"'], cycles:{cycles:[],count:0,window_days:7},
    fast_flows:{pairs:[],count:0,tolerance:.25,window_minutes:30},outliers:{outliers:[],count:0,sigma:2.5}},
    ['cycles','fast_flows','outliers'])`);
  const categories = selected.map(name => { h.run(`renderResultCategory('${name}')`); return h.element('#results').innerHTML; });
  for (const [index, html] of categories.entries()) {
    assert.ok(html.includes(['环路检测','快进快出','离群画像'][index]));
    assert.equal(html.includes('<b>SYN-A</b>'), false);
    assert.equal((html.match(/&lt;b&gt;SYN-A&lt;\/b&gt; &amp; &quot;B&quot;/g) || []).length, 1);
  }
  assert.equal((categories.join('').match(/&lt;b&gt;SYN-A&lt;\/b&gt; &amp; &quot;B&quot;/g) || []).length, 3);
});

test('analysis run and exported report share the algorithms selected for that run', async () => {
  const algorithms = [{ value: 'cycles', checked: true }, { value: 'paths', checked: false }];
  const results = { case_id: 1, algorithms: ['cycles'], subjects: [], source_snapshot: { flows: 8, accounts: 4 },
    cycles: { cycles: [], count: 0, window_days: 7 }, paths: { paths: [], count: 0, src: 'SYN-A', dst: 'SYN-B', max_hops: 4 } };
  const h = analysisFixture(algorithms, async path => {
    if (path === '/api/cases') return response([{ id: 1, name: '合成案件' }]);
    if (path.startsWith('/api/stats?')) return response({ flows: 8, accounts: 4 });
    if (path === '/api/analysis/report') return { ok: true, blob: async () => new Blob(['synthetic']) };
    return response(results);
  });
  await flush(); await h.run('run()');
  algorithms[0].checked = false; algorithms[1].checked = true;
  const pending = h.element('#btnReport').onclick();
  assert.equal(h.element('#analysisConfirm').open, true);
  assert.equal(h.requests.filter(item=>item.path==='/api/analysis/report').length, 0);
  h.element('#confirmAccept').click(); await pending;
  assert.equal(h.element('#analysisConfirm').open, false);
  const sent = h.requests.filter(item => item.options?.body).map(item => JSON.parse(item.options.body));
  assert.equal(sent.length, 2);
  assert.deepEqual(sent.map(item => item.params.algorithms), [['cycles'], ['cycles']]);
  assert.equal(h.element('#results').innerHTML.includes('路径追踪'), false);
});

test('exposed analysis windows reject zero, fractions and out-of-range values without default substitution', async () => {
  const h = analysisFixture([{value:'cycles',checked:true},{value:'fast_flows',checked:true}]); await flush();
  for (const cycle of ['0', '-1', '1.5', '366', 'NaN']) {
    h.element('#cycDays').value = cycle;
    assert.throws(() => h.run('collectParams()'), /环流时限（天）须为 1 至 365 的整数/);
  }
  h.element('#cycDays').value = '7';
  for (const window of ['0', '4', '-1', '5.5', '1441', 'NaN']) {
    h.element('#ffWin').value = window;
    assert.throws(() => h.run('collectParams()'), /快进快出窗口（分钟）须为 5 至 1440 的整数/);
  }
  for (const [cycle, window] of [['1', '5'], ['365', '1440'], ['7', '30'], ['', '']]) {
    h.element('#cycDays').value = cycle; h.element('#ffWin').value = window;
    const result = h.run('collectParams().params');
    assert.equal(result.cycle_window_days, cycle === '' ? 7 : Number(cycle));
    assert.equal(result.ff_window_minutes, window === '' ? 30 : Number(window));
    assert.equal(result.ff_tolerance, 0.25);
  }
});
