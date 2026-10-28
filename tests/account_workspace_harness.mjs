import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const app = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
const workflow = readFileSync(new URL('../static/workflow-ui.js', import.meta.url), 'utf8');
const dialogs = readFileSync(new URL('../static/ui-dialogs.js', import.meta.url), 'utf8');
const money = readFileSync(new URL('../static/money.js', import.meta.url), 'utf8');
const canvases = readFileSync(new URL('../static/canvas-workspace.js', import.meta.url), 'utf8');
const dataTools = readFileSync(new URL('../static/data-tools.js', import.meta.url), 'utf8');
const mainAccounts = readFileSync(new URL('../static/main-accounts.js', import.meta.url), 'utf8');
const workspaceCamera = readFileSync(new URL('../static/workspace-camera.js', import.meta.url), 'utf8');
const canvasHistory = readFileSync(new URL('../static/canvas-history.js', import.meta.url), 'utf8');
const section = (start, end) => {
  const offset = app.indexOf(start), finish = app.indexOf(end, offset);
  assert.ok(offset >= 0 && finish > offset, `missing public source section ${start}`);
  return app.slice(offset, finish);
};
const flush = async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); };
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const response = (value, status = 200) => ({ ok: status >= 200 && status < 300, status,
  json: async () => value, text: async () => JSON.stringify(value), blob: async () => ({ name: 'synthetic-existing.png' }) });
const decode = value => value.replace(/&(amp|lt|gt|quot|#39);/g, (_match, key) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" }[key]));

// The DOM preserves real ancestry, detached-control identity and event order.
// Account rendering, drawer selection, confirmations and workflow orchestration run unchanged.
function harness() {
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
    get selectedOptions() { return this.children.filter(child => child.tagName === 'OPTION' && child.value === String(this.value)); }
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
    click() { return this.disabled ? Promise.resolve() : this.fire('click').completion; }
    dispatchEvent(event) { return !this.fire(event.type, event).defaultPrevented; }
    focus() {
      if (!this.isConnected || this.disabled || this.closest('[inert]')) return;
      const previous = document.activeElement; document.activeElement = this;
      if (previous !== this) previous?.fire('focusout', { relatedTarget: this });
      this.fire('focusin', { relatedTarget: previous });
    }
    showModal() { this.open = true; this.setAttribute('open', ''); }
    close(value = '') { this.returnValue = value; this.open = false; this.removeAttribute('open'); return this.fire('close', { bubbles: false }).completion; }
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
  const drawer = add(document.body, 'drawer'); drawer.inert = true;
  add(drawer, 'drawerTitle'); add(drawer, 'drawerContext'); add(drawer, 'btnCloseDrawer', 'button'); add(drawer, 'drawerBody');
  const menu = add(drawer, 'edgeMenu'); menu.hidden = true; menu.setAttribute('role', 'menu');
  add(menu, 'edgeRemove', 'button').setAttribute('role', 'menuitem');
  for (const id of ['graphContainer', 'ctxModal', 'ctxTrack', 'ctxTrackStatus', 'ctxTbody', 'ctxSuggest', 'btnImport', 'btnImport2', 'importModal',
    'impPbar', 'impClose', 'impCancel', 'impFile', 'impRe', 'impCommit', 'impChangeFile', 'impMapping', 'impPreviewScroll', 'impHorizontalPosition',
    'impScrollLeft', 'impScrollRight', 'impPreviewTable', 'pcCircle', 'flowTbody', 'canvasSel', 'flowCkAll', 'caseSelect', 'analysisLink', 'batchList', 'tab-batches',
    'btnCanvasClone', 'btnCanvasSave', 'btnCanvasNew', 'btnCanvasRename', 'btnCanvasDel', 'btnCanvasActions', 'btnCanvasMenuClone',
    'btnDataActions', 'btnImportIds', 'btnIdTpl', 'btnSample', 'btnSample2', 'btnMainActions']) add(document.body, id);
  const nodeMenu = add(drawer, 'nodeMenu'); nodeMenu.hidden = true;
  add(nodeMenu, 'ndRemove', 'button');
  const photoMenu = add(drawer, 'nodePhotoMenu'); photoMenu.hidden = true;
  add(photoMenu, 'ndPhotoRe', 'button'); add(photoMenu, 'ndPhotoClr', 'button');
  // Canvas readiness shares the actual annotation lifecycle; retain its dialog
  // identity and close events alongside the account confirmation surface.
  add(document.body, 'annotationDialog', 'dialog');
  const confirmation = add(document.body, 'nodeConfirm', 'dialog'); confirmation.className = 'action-dialog';
  for (const id of ['ndConfirmTitle', 'ndConfirmMessage']) add(confirmation, id);
  for (const id of ['ndConfirmAccept', 'ndConfirmCancel', 'ndConfirmClose']) add(confirmation, id, 'button');
  const crop = add(document.body, 'photoCropModal'); crop.style.display = 'none'; crop.className = 'modal';
  for (const id of ['pcImg', 'pcZoom', 'pcConfirm', 'pcClose', 'pcStatus']) add(crop, id, id === 'pcImg' ? 'img' : id === 'pcZoom' ? 'input' : 'button');
  for (const id of ['fAccount', 'fName', 'fCounter', 'fKw', 'fMin', 'fMax', 'fFrom', 'fTo', 'fTracked']) add(document.body, id, 'input');
  const requests = [], removals = [], contextOpens = [], notices = [], searches = [], objectUrls = [], revokedUrls = [], prompts = [], canvasSnapshots = [];
  let graphLoader = null, graphRenderer = null, graphReads = 0, paints = 0;
  let network = async () => { throw new Error('Unexpected request'); }, remover = async () => false, scrollDisposals = 0;
  let prompt = async () => 'SYN-CANVAS';
  const state = { caseId: 1, caseName: '合成甲案', caseGeneration: 1, canvasId: 0, canvasGeneration: 1, ctx: null,
    graphView: { revision: 1, selectedEdge: null, selectedAccount: null, edgeItems: new Map() },
    lastGraph: { nodes: [], edges: [] }, focusNodes: new Map(), search: { page: 1 }, cases: [{ id: 1, name: '合成甲案' }, { id: 2, name: '合成乙案' }] };
  state.graphSelection = { ids: new Set(), clear() { this.ids.clear(); }, select(id) { this.ids = new Set([id]); }, refresh() {} };
  const window = new Element('#window'); window.innerWidth=1000; window.innerHeight=800; window.appendChild(document.root);
  window.__collectSnapshot = async () => ({ nodes: [], edges: [] });
  window.location = { href: 'http://synthetic.invalid/?case_id=1' }; window.history = { replaceState(_state, _title, url) { window.location.href = String(url); } };
  class SyntheticURL extends URL {
    static createObjectURL(file) { const url = `blob:synthetic-${objectUrls.length + 1}`; objectUrls.push({ url, file }); return url; }
    static revokeObjectURL(url) { revokedUrls.push(url); }
  }
  class SyntheticFormData {
    constructor() { this.entries = []; }
    append(...entry) { this.entries.push(entry); }
    get(name) { return this.entries.find(entry => entry[0] === name)?.[1] ?? null; }
  }
  window.FormData = SyntheticFormData;
  const fetch = async (path, options) => {
    requests.push({ path, options }); const value = await network(path, options);
    return value && typeof value.ok === 'boolean' ? value : response(value);
  };
  const context = vm.createContext({ state, document, window, exportWorkspace: null, caseWorkspace: null, mainAccountsWorkspace: null, dataToolsWorkspace: null, batchView: { deleting: null },
    URL: SyntheticURL, URLSearchParams, Set, Map, Event, CustomEvent: class { constructor(type) { this.type = type; } }, fetch, FormData: SyntheticFormData,
    MutationObserver: class { observe() {} disconnect() {} }, getComputedStyle: node => ({ display: node.style.display || 'block' }),
    $: selector => document.querySelector(selector), $$: selector => document.querySelectorAll(selector),
    api: async (path, options) => (await fetch(path, options)).json(),
    untrackFlows: ids => { removals.push({ ids: [...ids], caseId: state.caseId, canvasId: state.canvasId }); return remover(ids); },
    openContext: async id => { contextOpens.push(id); }, loadContext: async () => {},
    contextIsCurrent: () => false, closeContext() {}, syncContextSelection() {},
    toast: (message, type) => notices.push({ message, type }),
    preferenceStorage: { setItem() {} }, cancelGraphLayout() {}, resetGraphView() {}, newGraphPresentation: () => ({}),
    revokeZoomOptimization() {}, updateGraphViewStatus() {}, layoutJob: null,
    syncEdgeTypeControl() {}, syncFlowCkCount() {}, updateHeader() {}, resetBatchView() {},
    scheduleGraphLabels() {}, syncGraphEdgeLabel() {}, rebuildGraphViewport() {}, applyGraphLabels() {}, nodePhotoUrl: node => node.photo_at ? '/synthetic-photo.png' : '',
    jsonOpts: value => ({ headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) }),
    switchTab: tab => { state.tab = tab; }, doSearch: page => { searches.push({ page, fields: Object.fromEntries(['fAccount', 'fName', 'fCounter', 'fKw', 'fMin', 'fMax', 'fFrom', 'fTo'].map(id => [id, document.getElementById(id).value])), tracked: document.getElementById('fTracked').checked }); },
    exploreNeighborhood: account => contextOpens.push(account),
    loadGraph: async () => { graphReads += 1; await graphLoader?.(); }, renderGraph: () => { paints += 1; return graphRenderer?.(); },
    refreshStats: async () => {}, refreshCanvasList: async () => {}, loadBatches() {},
    promptText: async title => { prompts.push(title); return prompt(title); }, applyCanvasSnapshot: value => canvasSnapshots.push(value),
    bindTableHorizontalScroll(parts) { for (const node of Object.values(parts)) assert.ok(node); return { sync() {}, destroy() { scrollDisposals += 1; } }; },
    setTimeout: callback => { callback(); return 0; }, clearTimeout() {}, requestAnimationFrame: callback => { callback(); return 0; },
  });
  vm.runInContext(money, context);
  vm.runInContext(readFileSync(new URL('../static/principal-analysis.js', import.meta.url), 'utf8'), context);
  window.FundsFacts = context.FundsFacts;
  vm.runInContext(section('function esc(', 'function toast('), context);
  vm.runInContext(section('let pendingCanvasId = 0;', 'async function loadCases()'), context);
  vm.runInContext(section('async function applyPendingFocusEdge()', 'async function refreshStats()'), context);
  vm.runInContext(section('function caseContext()', '/* ---------------- 初始化'), context);
  // Load the production controller and its lexical lifecycle state together.
  // A duplicated flag or empty cancellation shim would conceal canvas blockers.
  vm.runInContext(section('let annotationShapes = [];', 'function applyPendingGraphRecent('), context);
  vm.runInContext(section('function activateCanvas(id)', 'function graphMetric('), context);
  vm.runInContext(section('function edgeKey(', 'function graphEdgeType('), context);
  vm.runInContext(section('let _selItem = null;', '/* ---------------- 交易上下文'), context);
  vm.runInContext(section('async function loadCanvas(id)', 'let graphRequestSequence = 0;'), context);
  vm.runInContext(section("  $('#btnCloseDrawer').onclick = () => {", '  // 上下文弹窗'), context);
  vm.runInContext(section('async function openManagedCase(', 'function installCaseWorkspace('), context);
  vm.runInContext(section('async function caseRequest(', 'let managedCaseListRequest = 0;'), context);
  vm.runInContext(section('function nodeLabel(', '/* ---------- 边标签'), context);
  vm.runInContext(section('async function refreshAccountIdentity(', 'function bindWorkspaceActions('), context);
  vm.runInContext(section("  $('#caseSelect').onchange = async e => {", "  $('#graphLabelMode').onchange = event => {"), context);
  vm.runInContext(canvases, context);
  vm.runInContext(dataTools, context); vm.runInContext(mainAccounts, context); vm.runInContext(workspaceCamera, context); vm.runInContext(canvasHistory, context);
  // The canvas command controller and app adapter remain real. These account
  // tests replace only the separate naming surface; draft disposition still
  // uses the actual account confirmation and can cancel before asking a name.
  window.FundsCanvasWorkspace = { create: window.FundsCanvasWorkspace.create,
    createView: () => ({ ask: async action => action === 'delete' ? true : context.promptText(action),
      onState() {}, dismiss() {}, complete() {}, attach() {}, showStatus() {},
      failure: message => context.toast(message, 'err') }) };
  window.FundsDataTools = { create: window.FundsDataTools.create,
    createView: () => ({ show() {}, onState() {}, dismiss() {}, attach() {},
      pickFile: async () => ({ name: 'synthetic-identities.csv', size: 123 }) }) };
  window.FundsMainAccountsView = { create: () => ({ show() {}, onState() {}, dismiss() {}, attach() {} }) };
  vm.runInContext(dialogs, context); vm.runInContext(workflow, context); window.fire('DOMContentLoaded');
  vm.runInContext('bindNodeWorkspaceUI(); bindPhotoCrop(); bindCanvases()', context);
  const item = model => ({ destroyed: false, getModel: () => model });
  return { state, document, requests, removals, contextOpens, notices, searches, objectUrls, revokedUrls, prompts, canvasSnapshots,
    element: id => document.getElementById(id),
    get scrollDisposals() { return scrollDisposals; }, get graphReads() { return graphReads; }, get paints() { return paints; },
    network(handler) { network = handler; }, remove(handler) { remover = handler; },
    onPrompt(handler) { prompt = handler; },
    onGraphLoad(handler) { graphLoader = handler; }, onGraphRender(handler) { graphRenderer = handler; },
    run(code) { return vm.runInContext(code, context); },
    open(account = 'SYN-A') { context.nodeAccount = account; context.nodeItem = item({ id: account }); return vm.runInContext('showNodeDrawer(nodeAccount,nodeItem)', context); },
    close() { return document.getElementById('btnCloseDrawer').click(); },
    selectCase(id) { return vm.runInContext(`selectCase(${id})`, context); },
    changeCase(id) { const select = document.getElementById('caseSelect'); select.value = String(id); return select.fire('change'); },
    edit(id, value) { const input = document.getElementById(id); assert.ok(input, id); input.value = value; return input.fire(id === 'ndFocusScale' ? 'change' : 'input').completion; },
    async accept() { assert.equal(confirmation.open, true, 'the action must first ask for confirmation'); await document.getElementById('ndConfirmAccept').click(); await flush(); },
    async cancel() { assert.equal(confirmation.open, true); await document.getElementById('ndConfirmCancel').click(); await flush(); },
    crop(file = { name: 'synthetic.png' }, account = 'SYN-A') { context.cropFile = file; context.cropAccount = account; return vm.runInContext('openPhotoCrop(cropFile,cropAccount,nodeItem)', context); },
  };
}

const graphNode = (account = 'SYN-A', extra = {}) => ({ account, name: '合成图内户名', id_no: 'SYN-ORIGINAL', is_main: 1,
  photo_at: 'SYN-PHOTO', out_cnt: 2, in_cnt: 3,
  out_totals: [{ currency: 'CNY', amount: '1.01' }, { currency: 'USD', amount: '2.02' }],
  in_totals: [{ currency: 'CNY', amount: '3.03' }], ...extra });
const profile = (extra = {}) => ({ name: '合成案件户名', out_cnt: 12, in_cnt: 13,
  out_totals: [{ currency: 'CNY', amount: '9007199254740993.07' }, { currency: 'USD', amount: '12.34' }],
  in_totals: [{ currency: 'CNY', amount: '56.78' }], tmin: '2026-10-16 00:00:00', tmax: '2026-10-17 12:34:56',
  top_counterparties: [{ acct: 'SYN-COUNTER', name: '合成对手', out_cnt: 2, in_cnt: 1,
    out_totals: [{ currency: 'USD', amount: '12.34' }], in_totals: [{ currency: 'CNY', amount: '56.78' }] }], ...extra });
const isWrite = request => ['POST', 'DELETE'].includes(request.options?.method);
const writes = h => h.requests.filter(isWrite);
function networkReady(h, readProfile = () => profile()) {
  h.network((path, options) => options?.method === 'POST' || options?.method === 'DELETE' ? { on: true, scale: 2 }
    : path.includes('/profile?') ? readProfile(path) : path.startsWith('/api/main-accounts?')
      ? [{ account: 'SYN-A', name: '合成案件户名', source: 'auto' }] : { rows: [], total: 0 });
}
async function ready(h, extra = {}) {
  h.state.lastGraph.nodes = [graphNode('SYN-A', extra), graphNode('SYN-B', { name: '合成乙图内户名' })];
  h.state.lastGraph.edges = [{ source: 'SYN-A', target: 'SYN-B', ids: [17, 18] },
    { source: 'SYN-C', target: 'SYN-A', ids: [18, 19] }, { source: 'SYN-B', target: 'SYN-C', ids: [20] }];
  networkReady(h); await h.open();
}
async function showMore(h, photo = false) {
  await h.element(photo ? 'ndPhotoMore' : 'ndMore').click(); await flush();
  assert.equal(h.element(photo ? 'nodePhotoMenu' : 'nodeMenu').hidden, false);
}

// Assertions observe controls, rendered facts and IO calls rather than source text.
test('a failed account read offers a real retry and successful retry restores the workspace', async () => {
  const h = harness(); let reads = 0;
  h.network(path => path.includes('/profile?') && ++reads === 1 ? Promise.reject(new Error('合成读取故障'))
    : path.includes('/profile?') ? profile() : []);
  await h.open();
  assert.match(h.element('drawerBody').textContent, /合成读取故障/);
  const retry = h.element('ndRetry'); assert.ok(retry); assert.equal(retry.disabled, false);
  await retry.click(); await flush();
  assert.equal(reads, 2); assert.ok(h.element('ndIdInput')); assert.match(h.element('drawerBody').textContent, /合成案件户名/);
  assert.equal(h.element('drawer').getAttribute('aria-busy'), 'false');
});

test('the latest same-account read owns its result', async () => {
  const h = harness(), old = deferred(), fresh = deferred(); let reads = 0;
  h.network(path => path.includes('/profile?') ? ++reads === 1 ? old.promise : fresh.promise : []);
  const first = h.open(), second = h.open();
  fresh.resolve(profile({ name: '合成新结果' })); await second;
  old.resolve(profile({ name: '合成旧结果' })); await first;
  assert.match(h.element('drawerBody').textContent, /合成新结果/);
  assert.equal(h.element('drawerBody').textContent.includes('合成旧结果'), false);
});

test('close, account, A-B-A, canvas and graph revisions revoke delayed account reads', async () => {
  for (const navigation of ['close', 'account', 'case', 'canvas', 'revision']) {
    const h = harness(), old = deferred(); let reads = 0;
    h.network(path => path.includes('/profile?') ? ++reads === 1 ? old.promise : profile({ name: '合成当前结果' }) : []);
    const first = h.open();
    if (navigation === 'close') await h.close();
    else {
      if (navigation === 'case') { h.selectCase(2); h.selectCase(1); }
      if (navigation === 'canvas') { h.state.canvasId = 9; h.state.canvasGeneration += 1; }
      if (navigation === 'revision') h.state.graphView.revision += 1;
      await h.open('SYN-A');
    }
    const body = h.element('drawerBody').innerHTML, title = h.element('drawerTitle').textContent;
    old.resolve(profile({ name: '合成失效结果' })); await first;
    assert.equal(h.element('drawerBody').innerHTML, body, navigation); assert.equal(h.element('drawerTitle').textContent, title, navigation);
    if (navigation === 'close') assert.equal(h.element('drawer').classList.contains('open'), false);
  }
});

test('closing and reopening restores both drafts while a different binding starts from persisted facts', async () => {
  const h = harness(); await ready(h);
  await h.edit('ndIdInput', 'SYN-DRAFT'); await h.edit('ndFocusScale', '2.5');
  await h.close(); await h.open();
  assert.equal(h.element('ndIdInput').value, 'SYN-DRAFT'); assert.equal(h.element('ndFocusScale').value, '2.5');
  assert.match(h.element('ndDirty').textContent, /未保存/); assert.equal(writes(h).length, 0);
  await h.open('SYN-B'); assert.equal(h.element('ndIdInput').value, 'SYN-ORIGINAL');
  await h.open(); assert.equal(h.element('ndIdInput').value, 'SYN-DRAFT');
  h.state.canvasGeneration += 1; await h.open();
  assert.equal(h.element('ndIdInput').value, 'SYN-ORIGINAL'); assert.equal(h.element('ndFocusScale').value, '1');
});

test('same-account graph refresh preserves draft values and graph facts are not edited before save', async () => {
  const h = harness(); await ready(h); const before = JSON.stringify(h.state.lastGraph);
  await h.edit('ndIdInput', 'SYN-UNSAVED'); await h.edit('ndFocusScale', '3');
  h.run('closeDrawer(false)'); h.state.graphView.revision += 1; await h.open();
  assert.equal(h.element('ndIdInput').value, 'SYN-UNSAVED'); assert.equal(h.element('ndFocusScale').value, '3');
  assert.equal(JSON.stringify(h.state.lastGraph), before); assert.equal(writes(h).length, 0);
});

test('identity saves deduplicate direct submissions, hold photo actions and preserve failed drafts', async () => {
  const h = harness(); await ready(h); const pending = deferred();
  h.network((path, options) => options?.method === 'POST' ? pending.promise : path.includes('/profile?') ? profile() : []);
  await h.edit('ndIdInput', 'SYN-EDITED-ID'); const button = h.element('ndIdSave'), submit = button.onclick;
  const first = submit(); await submit(); await flush();
  assert.equal(writes(h).length, 1); assert.equal(button.disabled, true); assert.equal(h.element('ndPhotoUp').disabled, true);
  const payload = JSON.parse(writes(h)[0].options.body);
  assert.equal(writes(h)[0].path, '/api/account-ids/set'); assert.equal(payload.case_id, 1);
  assert.equal(payload.account, 'SYN-A'); assert.equal(payload.id_no, 'SYN-EDITED-ID');
  h.element('ndPhotoFile').files = [{ name: 'synthetic.png' }]; h.element('ndPhotoFile').fire('change');
  assert.equal(h.element('photoCropModal').style.display, 'none');
  pending.resolve(response({ detail: '合成保存故障' }, 500)); await first;
  assert.equal(h.element('ndIdInput').value, 'SYN-EDITED-ID'); assert.equal(h.state.lastGraph.nodes[0].id_no, 'SYN-ORIGINAL');
  assert.equal(h.element('ndIdSave').disabled, false); assert.match(h.element('ndStatus').textContent, /合成保存故障/);
  assert.match(h.element('ndDirty').textContent, /未保存/); assert.equal(h.graphReads, 0); assert.equal(h.paints, 0);
  await h.close(); await h.open();
  assert.equal(h.element('ndIdInput').value, 'SYN-EDITED-ID'); assert.match(h.element('ndStatus').textContent, /合成保存故障/);
});

test('identity and scale saves preserve other unsaved fields through redraw', async () => {
  const h = harness(); await ready(h);
  let reopened;
  h.onGraphRender(() => { h.run('closeDrawer(false)'); h.state.graphView.revision += 1; reopened = h.open(); });
  h.onGraphLoad(async () => { h.run('closeDrawer(false)'); h.state.graphView.revision += 1; await h.open(); });
  await h.edit('ndIdInput', 'SYN-SAVED-ID'); await h.edit('ndFocusScale', '2.5');
  await h.element('ndIdSave').click(); await reopened; await flush();
  assert.equal(h.state.lastGraph.nodes[0].id_no, 'SYN-SAVED-ID');
  assert.equal(h.element('ndIdInput').value, 'SYN-SAVED-ID'); assert.equal(h.element('ndFocusScale').value, '2.5');
  assert.match(h.element('ndDirty').textContent, /未保存/);
  await h.element('ndFocusApply').click(); await flush();
  const scale = writes(h).find(request => request.path === '/api/focus-nodes/set'); assert.ok(scale);
  assert.equal(JSON.parse(scale.options.body).scale, 2.5); assert.equal(JSON.parse(scale.options.body).on, true);
  assert.equal(h.element('ndIdInput').value, 'SYN-SAVED-ID');
});

test('late writes after A-B-A and canvas changes do not publish feedback, graph data or control state', async () => {
  for (const navigation of ['case', 'canvas']) {
    const h = harness(); await ready(h); const pending = deferred();
    h.network((path, options) => options?.method === 'POST' ? pending.promise : path.includes('/profile?') ? profile({ name: '合成当前结果' }) : []);
    await h.edit('ndIdInput', 'SYN-LATE-ID'); const first = h.element('ndIdSave').onclick();
    if (navigation === 'case') { h.selectCase(2); h.selectCase(1); }
    if (navigation === 'canvas') h.state.canvasGeneration += 1;
    await h.open('SYN-A');
    const fresh = h.element('ndIdSave'), disabled = fresh.disabled, body = h.element('drawerBody').innerHTML, messages = h.notices.length;
    pending.resolve({}); await first;
    assert.equal(h.element('drawerBody').innerHTML, body, navigation); assert.equal(fresh.disabled, disabled, navigation);
    assert.equal(h.notices.length, messages, navigation); assert.equal(h.paints, 0, navigation); assert.equal(h.graphReads, 0, navigation);
    assert.equal(h.state.lastGraph.nodes.some(node => node.id_no === 'SYN-LATE-ID'), false, navigation);
  }
});

test('case and graph scopes display separate exact totals without mixing currencies or changing facts', async () => {
  const h = harness(); await ready(h); const before = JSON.stringify(h.state.lastGraph);
  await h.element('ndScopeCase').click();
  const caseText = h.element('ndStats').textContent;
  assert.match(caseText, /9,007,199,254,740,993\.07/); assert.match(caseText, /USD 12\.34/); assert.match(caseText, /12/);
  await h.element('ndScopeGraph').click();
  const graphText = h.element('ndStats').textContent;
  assert.match(graphText, /1\.01/); assert.match(graphText, /USD 2\.02/);
  assert.equal(graphText.includes('9,007,199,254,740,993.07'), false); assert.equal(graphText.includes('12.34'), false);
  assert.equal(JSON.stringify(h.state.lastGraph), before); assert.equal(writes(h).length, 0);
});

test('counterparty navigation clears every previous filter before searching its account pair', async () => {
  const h = harness(); await ready(h);
  const fields = ['fAccount', 'fName', 'fCounter', 'fKw', 'fMin', 'fMax', 'fFrom', 'fTo'];
  for (const id of fields) h.element(id).value = 'SYN-STALE'; h.element('fTracked').checked = true;
  const counter = h.element('drawerBody').querySelector('[data-node-counter]'); assert.ok(counter);
  await counter.click(); await flush();
  assert.equal(h.searches.length, 1); const search = h.searches[0]; assert.equal(search.page, 1); assert.equal(search.tracked, false);
  assert.equal(search.fields.fAccount, 'SYN-A'); assert.equal(search.fields.fCounter, 'SYN-COUNTER');
  for (const id of fields.filter(id => !['fAccount', 'fCounter'].includes(id))) assert.equal(search.fields[id], '', id);
  assert.equal(h.state.tab, 'search'); assert.equal(h.element('drawer').classList.contains('open'), false);
});

test('controlled removal requires confirmation and deduplicates only incident tracked flows', async () => {
  const h = harness(); await ready(h); const before = JSON.stringify(h.state.lastGraph), pending = deferred(); h.remove(() => pending.promise);
  await showMore(h); const first = h.element('ndRemove').click(); await flush();
  assert.equal(h.removals.length, 0); assert.match(h.element('ndConfirmMessage').textContent, /3/);
  await h.cancel(); await first; assert.equal(h.removals.length, 0);
  await showMore(h); const second = h.element('ndRemove').click(); await flush(); await h.accept();
  assert.equal(h.removals.length, 1); assert.deepEqual(h.removals[0], { ids: [17, 18, 19], caseId: 1, canvasId: 0 });
  pending.resolve(false); await second; await flush();
  assert.equal(JSON.stringify(h.state.lastGraph), before); assert.equal(h.element('drawer').classList.contains('open'), true);
  assert.match(h.element('ndStatus').textContent, /未移出|保留|移出未完成/);
});

test('pending confirmation cannot cross another account or a newer graph revision', async () => {
  for (const navigation of ['account', 'revision', 'case']) {
    const h = harness(); await ready(h); h.remove(async () => true); await showMore(h);
    const first = h.element('ndRemove').click(); await flush(); assert.equal(h.element('nodeConfirm').open, true);
    if (navigation === 'account') await h.open('SYN-B');
    if (navigation === 'revision') h.state.graphView.revision += 1;
    if (navigation === 'case') { h.selectCase(2); h.selectCase(1); }
    if (h.element('nodeConfirm').open) await h.accept();
    await first; await flush(); assert.equal(h.removals.length, 0, navigation);
  }
});

test('photo clearing is confirmed, mutually exclusive and retains the photo on error', async () => {
  const h = harness(); await ready(h); const pending = deferred();
  h.network((path, options) => options?.method === 'POST' ? pending.promise : path.includes('/profile?') ? profile() : []);
  await showMore(h, true); const first = h.element('ndPhotoClr').click(); await flush();
  assert.equal(writes(h).length, 0); await h.accept(); assert.equal(writes(h).length, 1);
  assert.equal(writes(h)[0].path, '/api/account-photos/clear');
  assert.equal(h.element('ndPhotoRe').disabled, true); assert.equal(h.element('ndPhotoUp').disabled, true);
  const file = h.element('ndPhotoFile'); file.files = [{ name: 'synthetic.png' }]; file.fire('change');
  assert.equal(h.element('photoCropModal').style.display, 'none');
  pending.resolve(response({ detail: '合成清除故障' }, 500)); await first; await flush();
  assert.equal(h.state.lastGraph.nodes[0].photo_at, 'SYN-PHOTO'); assert.match(h.element('ndStatus').textContent, /合成清除故障/);
  assert.equal(h.element('ndPhotoUp').disabled, false); assert.equal(h.graphReads, 0);
});

test('crop confirmation deduplicates direct submits, records its binding and revokes its object URL', async () => {
  const h = harness(); await ready(h); const pending = deferred();
  h.network(path => path === '/api/account-photos/set' ? pending.promise : path.includes('/profile?') ? profile() : []);
  h.crop(); const url = h.objectUrls.at(-1).url, submit = h.element('pcConfirm').onclick;
  const first = submit(); await submit(); await flush();
  const requests = writes(h); assert.equal(requests.length, 1); assert.equal(requests[0].path, '/api/account-photos/set');
  assert.equal(requests[0].options.body.get('case_id'), 1); assert.equal(requests[0].options.body.get('account'), 'SYN-A');
  assert.equal(h.element('pcConfirm').disabled, true); assert.equal(h.element('ndPhotoUp').disabled, true);
  assert.equal(h.element('pcClose').disabled, true); await h.element('pcClose').click();
  assert.equal(h.element('photoCropModal').style.display, 'flex', 'a submitted upload remains active until it settles or its scope is revoked');
  pending.resolve({}); await first; await flush();
  assert.equal(h.element('photoCropModal').style.display, 'none'); assert.equal(h.graphReads, 1);
  assert.equal(h.revokedUrls.includes(url), true);
});

test('scope revocation of a pending crop rejects late success without unlocking a newer crop', async () => {
  const h = harness(); await ready(h); const firstWrite = deferred(), nextWrite = deferred(); let count = 0;
  h.network(path => path === '/api/account-photos/set' ? ++count === 1 ? firstWrite.promise : nextWrite.promise
    : path.includes('/profile?') ? profile() : []);
  h.crop(); const old = h.element('pcConfirm').onclick(); h.selectCase(2); h.selectCase(1);
  await h.open(); const messages = h.notices.length; h.crop({ name: 'synthetic-next.png' }); const next = h.element('pcConfirm').onclick();
  firstWrite.resolve({}); await old; await flush();
  assert.equal(h.element('photoCropModal').style.display, 'flex'); assert.equal(h.element('pcConfirm').disabled, true);
  assert.equal(h.notices.length, messages); assert.equal(h.graphReads, 0);
  nextWrite.resolve(response({ detail: '合成第二张失败' }, 500)); await next;
  assert.equal(h.element('pcConfirm').disabled, false); assert.equal(h.element('photoCropModal').style.display, 'flex');
});

test('scope revocation during original-photo retrieval cannot submit the fetched original', async () => {
  const h = harness(); await ready(h); const original = deferred();
  h.network(path => path.startsWith('/api/account-photos/orig?') ? original.promise : path.includes('/profile?') ? profile() : []);
  h.crop(null); const pending = h.element('pcConfirm').onclick(); h.selectCase(2); h.selectCase(1);
  original.resolve(response({})); await pending; await flush();
  assert.equal(writes(h).length, 0); assert.equal(h.graphReads, 0); assert.equal(h.notices.length, 0);
  assert.equal(h.element('photoCropModal').style.display, 'none');
});

test('a same-context A save refreshes A facts while the current B account keeps its draft and focus', async () => {
  const h = harness(); await ready(h); const pending = deferred(); let reopened;
  h.network((path, options) => options?.method === 'POST' ? pending.promise : path.includes('/profile?') ? profile() : []);
  await h.open('SYN-B'); await h.edit('ndIdInput', 'SYN-B-DRAFT'); await h.open();
  await h.edit('ndIdInput', 'SYN-A-SAVED'); const first = h.element('ndIdSave').onclick(); await h.open('SYN-B');
  h.element('btnCloseDrawer').focus();
  h.onGraphRender(() => { h.run('closeDrawer(false)'); h.state.graphView.revision += 1; reopened = h.open('SYN-B'); });
  pending.resolve({}); await first; await reopened; await flush();
  assert.equal(h.state.lastGraph.nodes.find(node => node.account === 'SYN-A').id_no, 'SYN-A-SAVED');
  assert.equal(h.paints, 1); assert.equal(h.state.graphView.selectedAccount, 'SYN-B');
  assert.equal(h.element('ndIdInput').value, 'SYN-B-DRAFT'); assert.equal(h.document.activeElement, h.element('btnCloseDrawer'));
  assert.match(h.element('ndDirty').textContent, /未保存/);
});

test('a pending save remains exclusive after closing and reopening the same account', async () => {
  const h = harness(); await ready(h); const pending = deferred();
  h.network((path, options) => options?.method === 'POST' ? pending.promise : path.includes('/profile?') ? profile() : []);
  await h.edit('ndIdInput', 'SYN-PENDING'); const first = h.element('ndIdSave').onclick();
  await h.close(); await h.open();
  assert.equal(h.element('ndIdSave').disabled, true); assert.equal(h.element('ndPhotoUp').disabled, true);
  await h.element('ndIdSave').onclick(); assert.equal(writes(h).length, 1);
  pending.resolve({}); await first; await flush();
  assert.equal(h.element('ndPhotoUp').disabled, false); assert.equal(h.state.lastGraph.nodes[0].id_no, 'SYN-PENDING');
});

test('changed incident flow membership invalidates an already opened removal confirmation', async () => {
  const h = harness(); await ready(h); h.remove(async () => true); await showMore(h);
  const first = h.element('ndRemove').click(); await flush();
  h.state.lastGraph.edges.push({ source: 'SYN-A', target: 'SYN-D', ids: [999] });
  await h.accept(); await first;
  assert.equal(h.removals.length, 0); assert.match(h.element('ndStatus').textContent, /图谱已变化|重新确认/);
  assert.equal(h.element('drawer').classList.contains('open'), true);
});

test('removal errors keep the workspace and controlled success may close it without inventing facts', async () => {
  for (const outcome of ['error', 'success']) {
    const h = harness(); await ready(h); const before = JSON.stringify(h.state.lastGraph);
    h.remove(async () => { if (outcome === 'error') throw new Error('合成移出故障'); return true; });
    await showMore(h); const first = h.element('ndRemove').click(); await flush(); await h.accept(); await first;
    assert.equal(JSON.stringify(h.state.lastGraph), before);
    assert.equal(h.element('drawer').classList.contains('open'), outcome === 'error');
    if (outcome === 'error') assert.match(h.element('ndStatus').textContent, /合成移出故障/);
  }
});

test('account and photo menus handle Escape locally and restore their triggers', async () => {
  for (const photo of [false, true]) {
    const h = harness(); await ready(h); const trigger = h.element(photo ? 'ndPhotoMore' : 'ndMore'); trigger.focus(); await showMore(h, photo);
    const action = h.element(photo ? 'ndPhotoRe' : 'ndRemove'); action.fire('keydown', { key: 'Escape' }); await flush();
    assert.equal(h.element(photo ? 'nodePhotoMenu' : 'nodeMenu').hidden, true);
    assert.equal(h.document.activeElement, trigger); assert.equal(h.element('drawer').classList.contains('open'), true);
    trigger.fire('keydown', { key: 'Escape' }); await flush(); assert.equal(h.element('drawer').classList.contains('open'), false);
  }
});

test('case navigation asks before discarding drafts, keeps them on Cancel and clears them after acceptance', async () => {
  const h = harness(); await ready(h); await h.edit('ndIdInput', 'SYN-UNSAVED-SCOPE'); await h.close();
  let change = h.changeCase(2); await flush();
  assert.equal(h.state.caseId, 1); assert.equal(h.element('caseSelect').value, '1'); await h.cancel(); await change.completion;
  await h.open(); assert.equal(h.element('ndIdInput').value, 'SYN-UNSAVED-SCOPE');
  change = h.changeCase(2); await flush(); await h.accept(); await change.completion;
  assert.equal(h.state.caseId, 2); assert.equal(h.element('caseSelect').value, '2');
  h.selectCase(1); h.state.lastGraph.nodes = [graphNode()]; await h.open();
  assert.equal(h.element('ndIdInput').value, 'SYN-ORIGINAL'); assert.equal(writes(h).length, 0);
});

test('clean case navigation revokes its old binding after import preflight without a needless confirmation turn', async () => {
  const h = harness(); await ready(h); const generation = h.state.caseGeneration;
  const change = h.changeCase(2);
  assert.equal(h.element('nodeConfirm').open, false);
  await change.completion;
  assert.equal(h.state.caseId, 2); assert.equal(h.state.caseGeneration, generation + 1);
  assert.equal(h.element('nodeConfirm').open, false);
});

test('detached account controls cannot mutate, navigate or reopen photos after their binding changes', async () => {
  const h = harness(); await ready(h); await h.edit('ndIdInput', 'SYN-DETACHED');
  const save = h.element('ndIdSave').onclick, browse = h.element('ndBrowse').onclick, fileChange = h.element('ndPhotoFile').onchange;
  await h.open('SYN-B'); const reads = h.requests.length, body = h.element('drawerBody').innerHTML;
  await save(); browse(); fileChange(); await flush();
  assert.equal(writes(h).length, 0); assert.equal(h.requests.length, reads); assert.equal(h.searches.length, 0);
  assert.equal(h.element('photoCropModal').style.display, 'none'); assert.equal(h.element('drawerBody').innerHTML, body);
});

test('successful per-field writes restore a reachable field or photo control after graph replacement', async () => {
  for (const [action, target] of [['ndIdSave', 'ndIdInput'], ['ndFocusApply', 'ndFocusScale'], ['ndPhotoClr', 'ndPhotoUp']]) {
    const h = harness(); await ready(h); let reopened;
    const redraw = async () => { h.run('closeDrawer(false)'); h.state.graphView.revision += 1; reopened = h.open(); await reopened; };
    h.onGraphRender(redraw); h.onGraphLoad(redraw);
    await h.edit('ndIdInput', 'SYN-FOCUS-ID'); await h.edit('ndFocusScale', '2');
    if (action === 'ndPhotoClr') { await showMore(h, true); h.element('ndPhotoClr').focus(); }
    else h.element(action).focus();
    const write = h.element(action).click();
    if (action === 'ndPhotoClr') { await flush(); await h.accept(); }
    await write; await reopened; await flush();
    assert.equal(h.document.activeElement === h.element(target), true, `${action}: expected current ${target}, focused ${h.document.activeElement?.id}`);
    assert.equal(h.element(target).disabled, false, action);
    assert.match(h.element('ndStatus').textContent, /已保存/, action);
  }
});

test('an account main-role button opens the shared manager at the exact-account cancellation confirmation', async () => {
  const h = harness(); await ready(h);
  const actions = h.run('window.FundsMainAccountsActions = installMainAccounts()');
  const graphBefore = JSON.stringify(h.state.lastGraph);
  await h.element('ndToggleMain').click(); await flush();
  const status = actions.status();
  assert.equal(status.visible, true); assert.equal(status.screen, 'confirm');
  assert.equal(status.confirmation.action, 'remove'); assert.equal(status.confirmation.account, 'SYN-A');
  assert.equal(status.confirmation.source, 'auto'); assert.equal(writes(h).length, 0);
  assert.equal(actions.cancelConfirm(), true); assert.equal(actions.status().screen, 'list');
  assert.equal(JSON.stringify(h.state.lastGraph), graphBefore);
  assert.equal(h.element('ndStatus').textContent, '', 'opening the manager cannot claim a saved account setting');
});

test('main-account preflight preserves an unsaved account draft and refuses the business write', async () => {
  const h = harness(); await ready(h); await h.edit('ndIdInput', 'SYN-UNSAVED-MAIN-DRAFT');
  const actions = h.run('window.FundsMainAccountsActions = installMainAccounts()');
  assert.equal(actions.open(h.element('btnMainActions')), true); await flush();
  assert.equal(actions.prepare('auto'), true); assert.equal(await actions.submit(), false);
  assert.match(actions.status().error, /未保存/); assert.equal(actions.status().pending, false);
  assert.equal(h.element('ndIdInput').value, 'SYN-UNSAVED-MAIN-DRAFT');
  assert.equal(h.run('nodeWorkspace.draft.idBase'), 'SYN-ORIGINAL'); assert.equal(writes(h).length, 0);
});

test('a main-account write blocks stale account saves, canvas commands and identity import until its response settles', async () => {
  const h = harness(); await ready(h);
  const manager = h.run('window.FundsMainAccountsActions = installMainAccounts()');
  const bulk = h.run('window.FundsDataToolsActions = installDataTools()'), pending = deferred();
  h.network((path, options) => path === '/api/main-accounts/auto' ? pending.promise
    : path.startsWith('/api/main-accounts?') ? [{ account: 'SYN-A', source: 'auto', name: '合成案件户名' }] : []);
  assert.equal(manager.open(h.element('btnMainActions')), true); await flush();
  assert.equal(manager.prepare('auto'), true); const write = manager.submit(); await flush();
  assert.equal(manager.status().sent, true); assert.equal(writes(h).length, 1);
  assert.equal(h.element('ndIdSave').disabled, true); assert.equal(h.element('ndPhotoUp').disabled, true);
  h.element('ndIdInput').value = 'SYN-QUEUED-AFTER-MAIN';
  await h.run('writeNodeWorkspace(nodeWorkspace, "id")');
  assert.equal(bulk.open('import', h.element('btnDataActions')), false);
  assert.equal(await h.run('window.FundsCanvasActions.run("clone")'), false);
  assert.equal(writes(h).length, 1, 'none of the competing commands may reach the network');
  pending.resolve(response({ detail: '合成主账号写入被拒绝' }, 409)); await write;
  assert.equal(manager.blocked({ caseId: 1 }), false); assert.equal(h.element('ndIdInput').disabled, false);
  assert.equal(h.element('ndPhotoUp').disabled, false);
  assert.equal(h.run('nodeWorkspace.draft.idValue'), 'SYN-ORIGINAL', 'a stale callback cannot capture an edit made while its field is disabled');
  assert.match(manager.status().error, /合成主账号写入被拒绝/); assert.equal(writes(h).length, 1);
});

test('a pending write respects a deliberate move outside the drawer during its graph refresh', async () => {
  for (const target of ['graphContainer', 'btnImport']) {
    const h = harness(); await ready(h); const write = deferred();
    h.network((path, options) => options?.method === 'POST' ? write.promise : path.includes('/profile?') ? profile() : []);
    h.onGraphLoad(async () => { h.run('closeDrawer(false)'); h.state.graphView.revision += 1; await h.open(); });
    await h.edit('ndFocusScale', '2'); h.element('ndFocusApply').focus(); const first = h.element('ndFocusApply').click();
    h.element(target).focus(); write.resolve({}); await first; await flush();
    assert.equal(h.document.activeElement, h.element(target), target);
  }
});

test('a redraw focus marker expires after explicit close, another account and case A-B-A', async () => {
  for (const navigation of ['close', 'account', 'case']) {
    const h = harness(); await ready(h); h.element('ndIdInput').focus(); h.run('closeDrawer(false)');
    if (navigation === 'close') h.run('closeDrawer()');
    if (navigation === 'account') await h.open('SYN-B');
    if (navigation === 'case') { h.selectCase(2); h.selectCase(1); }
    await h.open(); assert.equal(h.document.activeElement === h.document.body, true, `${navigation}: focused ${h.document.activeElement?.id}`);
  }
});

test('server-normalized identity becomes the saved baseline without overwriting a newer draft', async () => {
  const submitted = ' 000 000 000 000 000 00x ', canonical = '00000000000000000X';
  for (const changedWhilePending of [false, true]) {
    const h = harness(); await ready(h); const pending = deferred(); let reopened;
    h.network((path, options) => options?.method === 'POST' ? pending.promise : path.includes('/profile?') ? profile() : []);
    h.onGraphRender(() => { h.run('closeDrawer(false)'); h.state.graphView.revision += 1; reopened = h.open(); });
    await h.edit('ndIdInput', submitted); const first = h.element('ndIdSave').click(); await flush();
    assert.equal(JSON.parse(writes(h)[0].options.body).id_no, submitted.trim());
    // A synthetic input event models a newer draft delivered before the save
    // receipt; ordinary physical input remains disabled while the write runs.
    if (changedWhilePending) await h.edit('ndIdInput', 'SYN-NEWER-DRAFT');
    pending.resolve({ id_no: canonical }); await first; await reopened; await flush();
    assert.equal(h.state.lastGraph.nodes[0].id_no, canonical);
    assert.equal(h.element('ndIdInput').value, changedWhilePending ? 'SYN-NEWER-DRAFT' : canonical);
    assert.equal(h.element('ndIdSave').disabled, !changedWhilePending);
    assert.equal(/未保存/.test(h.element('ndDirty').textContent), changedWhilePending);
    await h.edit('ndIdInput', canonical);
    assert.equal(h.element('ndIdSave').disabled, true, 'the server value is the confirmed comparison baseline');
    assert.equal(writes(h).length, 1);
  }
});

test('an account setting write serializes photo and setting entries across accounts in the same scope', async () => {
  const h = harness(); await ready(h); const pending = deferred();
  h.network((path, options) => options?.method === 'POST' || options?.method === 'DELETE' ? pending.promise : path.includes('/profile?') ? profile() : []);
  await h.edit('ndIdInput', 'SYN-A-SERIAL'); const first = h.element('ndIdSave').click(); await h.open('SYN-B');
  const disabled = h.element('ndPhotoUp').disabled;
  h.crop({ name: 'synthetic-blocked.png' }, 'SYN-B'); const cropOpened = h.element('photoCropModal').style.display === 'flex';
  if (cropOpened) h.run('revokePhotoCrop(true)');
  const second = h.element('ndToggleMain').onclick(); await flush(); const count = writes(h).length;
  pending.resolve({}); await first; await second; await flush();
  assert.equal(disabled, true, 'B photo entry must stay disabled while A changes the same graph scope');
  assert.equal(cropOpened, false, 'a direct photo handler must respect the scope-wide setting lock');
  assert.equal(count, 1, 'a direct B setting handler must not overlap the submitted A mutation');
  assert.equal(h.element('ndPhotoUp').disabled, false, 'the current B controls recover after A settles');
});

test('a crop whose view revision expires releases busy state and requires an explicit reopen', async () => {
  const h = harness(); await ready(h); const pending = deferred();
  h.network(path => path === '/api/account-photos/set' ? pending.promise : path.includes('/profile?') ? profile() : []);
  h.crop(); const first = h.element('pcConfirm').click(); h.state.graphView.revision += 1;
  pending.resolve({}); await first; await flush();
  assert.equal(h.element('pcClose').disabled, false, 'a stale receipt cannot trap the crop dialog in its busy state');
  assert.equal(h.element('pcConfirm').disabled, true, 'an expired crop cannot be resubmitted without reopening');
  assert.match(h.element('pcStatus').textContent, /重新打开|已变化|已失效/);
  assert.equal(h.graphReads, 0); await h.element('pcClose').click(); assert.equal(h.element('photoCropModal').style.display, 'none');
});

const canvasRequests = h => h.requests.filter(request => request.path.startsWith('/api/canvases'));
function canvasNetwork(h) {
  h.network((path, options) => path === '/api/canvases/clone' || path === '/api/canvases/save'
    ? { canvas_id: 7, name: '合成画布', copied: 3 }
    : path.startsWith('/api/canvases/') ? { id: 7, name: '合成画布', snapshot: { nodes: [], edges: [] } }
      : path.includes('/profile?') ? profile() : []);
}
test('new, clone, default-save and load commands ask before leaving account drafts and Cancel retains their scope', async () => {
  for (const action of ['btnCanvasNew', 'btnCanvasClone', 'btnCanvasSave', 'loadCanvas']) {
    const h = harness(); await ready(h); canvasNetwork(h); await h.edit('ndIdInput', 'SYN-CANVAS-DRAFT');
    const command = action === 'loadCanvas' ? h.run('loadCanvas(7)') : h.element(action).click(); await flush();
    const confirmed = h.element('nodeConfirm').open;
    if (confirmed) await h.cancel(); await command;
    assert.equal(confirmed, true, action);
    assert.equal(canvasRequests(h).length, 0, `${action}: Cancel must prevent canvas IO`);
    assert.equal(h.prompts.length, 0, `${action}: draft disposition precedes naming`);
    assert.equal(h.state.canvasId, 0, action); assert.equal(h.element('ndIdInput').value, 'SYN-CANVAS-DRAFT', action);
  }
});

test('accepting a canvas departure clears the old draft and performs exactly one clone', async () => {
  const h = harness(); await ready(h); canvasNetwork(h); await h.edit('ndIdInput', 'SYN-LEAVING-CANVAS');
  const command = h.element('btnCanvasClone').click(); await flush(); await h.accept(); await command;
  assert.equal(canvasRequests(h).length, 1); assert.equal(canvasRequests(h)[0].path, '/api/canvases/clone');
  assert.equal(h.state.canvasId, 7); assert.equal(h.prompts.length, 1);
  h.state.graphLoading = false; h.state.lastGraph.nodes = [graphNode()]; await h.open();
  assert.equal(h.element('ndIdInput').value, 'SYN-ORIGINAL');
});

test('saving an existing named canvas preserves account drafts without a departure confirmation', async () => {
  const h = harness(); await ready(h); h.state.canvasId = 7; await h.open(); canvasNetwork(h);
  await h.edit('ndIdInput', 'SYN-NAMED-CANVAS-DRAFT'); await h.element('btnCanvasSave').click();
  assert.equal(h.element('nodeConfirm').open, false); assert.equal(h.state.canvasId, 7);
  assert.equal(h.element('ndIdInput').value, 'SYN-NAMED-CANVAS-DRAFT');
  assert.equal(canvasRequests(h).length, 1); assert.equal(canvasRequests(h)[0].path, '/api/canvases/save');
});

test('cancelling naming after approving departure keeps the account draft until a canvas actually changes', async () => {
  const h = harness(); await ready(h); canvasNetwork(h); h.onPrompt(async () => null);
  await h.edit('ndIdInput', 'SYN-DRAFT-RETAINED');
  const command = h.element('btnCanvasClone').click(); await flush(); await h.accept(); await command;
  assert.equal(h.prompts.length, 1); assert.equal(canvasRequests(h).length, 0); assert.equal(h.state.canvasId, 0);
  await h.close(); await h.open(); assert.equal(h.element('ndIdInput').value, 'SYN-DRAFT-RETAINED');
  assert.match(h.element('ndDirty').textContent, /未保存/);
});

test('rename acknowledgement survives a failed canvas-list refresh and a later save uses the new name', async () => {
  const h = harness(); await ready(h); h.state.canvasId = 7; h.state.canvasName = '合成旧名称'; await h.open();
  const select = h.element('canvasSel'); select.innerHTML = '<option value="0">默认画布</option><option value="7">合成旧名称</option>'; select.value = '7';
  h.run(section('async function refreshCanvasList(', 'function applyCanvasSnapshot('));
  await h.edit('ndIdInput', 'SYN-RENAME-DRAFT'); const binding = JSON.stringify(h.run('caseContext()'));
  h.onPrompt(async () => '  合成确认名称  ');
  h.network((path, options) => path === '/api/canvases/rename' ? response({ ok: true })
    : path === '/api/canvases/save' ? { canvas_id: 7, name: JSON.parse(options.body).name }
      : response({ detail: '合成列表读取失败' }, 503));
  await h.element('btnCanvasRename').click();
  assert.equal(h.state.canvasName, '合成确认名称'); assert.equal(select.selectedOptions[0].textContent, '合成确认名称');
  assert.equal(select.value, '7'); assert.equal(JSON.stringify(h.run('caseContext()')), binding);
  assert.equal(h.element('ndIdInput').value, 'SYN-RENAME-DRAFT'); assert.match(h.element('ndDirty').textContent, /未保存/);
  h.onPrompt(async () => h.state.canvasName); await h.element('btnCanvasSave').click();
  const canvasWrites = canvasRequests(h).filter(isWrite); assert.equal(canvasWrites.length, 2);
  assert.equal(canvasWrites[0].path, '/api/canvases/rename'); assert.equal(canvasWrites[1].path, '/api/canvases/save');
  assert.equal(JSON.parse(canvasWrites[1].options.body).name, '合成确认名称');
  assert.equal(h.state.canvasName, '合成确认名称'); assert.equal(select.selectedOptions[0].textContent, '合成确认名称');
});

test('recovered deletion keeps a missing-canvas placeholder and prevents writes until explicitly selecting a surviving canvas', async () => {
  const h = harness(); h.state.canvasId = 7; h.state.canvasName = '合成删除目标';
  const select = h.element('canvasSel'); select.innerHTML = '<option value="0">默认画布</option><option value="7">合成删除目标</option><option value="9">合成保留画布</option>'; select.value = '7';
  const actions = h.run('window.FundsCanvasActions');
  h.network((path, options) => {
    if (options?.method === 'DELETE') throw new Error('合成删除回执丢失');
    if (path === '/api/canvases?case_id=1') return [{ id: 9, name: '合成保留画布' }];
    if (path === '/api/canvases/9?case_id=1') return { id: 9, name: '合成保留画布', snapshot: { nodes: [], edges: [] } };
    if (path === '/api/canvases/save') return { canvas_id: 9, name: '合成保留画布' };
    throw new Error('Unexpected synthetic request ' + path);
  });
  await h.element('btnCanvasDel').click(); assert.equal(actions.status().unknown, true);
  assert.equal((await actions.reconcile()).length, 1); assert.equal(actions.acknowledge(), true);
  assert.equal(h.state.canvasId, 7); assert.equal(select.value, '7'); assert.equal(select.selectedOptions.length, 1);
  assert.equal(select.selectedOptions[0].disabled, true); assert.match(select.selectedOptions[0].textContent, /已删除.*选择其他/);
  const before = canvasRequests(h).length;
  for (const action of ['new', 'clone', 'save', 'rename', 'delete']) assert.equal(await actions.run(action), false, action);
  let preSaveWrites = 0;
  await assert.rejects(actions.withWrite(h.run('caseContext()'), async () => { preSaveWrites += 1; }));
  assert.equal(preSaveWrites, 0, 'case-package pre-save must respect the actual missing-canvas adapter guard');
  assert.equal(actions.status().unknown, false, 'a rejected pre-save did not send a write');
  assert.equal(canvasRequests(h).length, before); assert.equal(h.element('btnCanvasSave').disabled, true);
  h.onGraphLoad(async () => { h.state.graphLoading = false; }); select.value = '9'; await select.fire('change').completion;
  assert.equal(h.state.canvasId, 9); assert.equal(select.value, '9'); assert.equal(h.element('btnCanvasSave').disabled, false);
  h.onPrompt(async () => '合成保留画布'); await h.element('btnCanvasSave').click();
  const saved = canvasRequests(h).find(request => request.path === '/api/canvases/save'); assert.ok(saved);
  assert.equal(JSON.parse(saved.options.body).canvas_id, 9);
  assert.equal(canvasRequests(h).filter(isWrite).length, 2, 'only the original DELETE and an explicit save of the surviving canvas may write');
});

test('bulk identity refresh patches live labels and the clean drawer without changing geometry, camera, selection or history', async () => {
  const h = harness(); await ready(h); h.state.showIds = true;
  h.state.positions = { 'SYN-A': { x: 17, y: 23 }, 'SYN-B': { x: 111, y: 157 } };
  h.state.graphView.visibleIds = new Set(['SYN-A', 'SYN-B']); h.state.graphView.root = 'SYN-A';
  h.state.presentation = { relationFilter: { active: true, currency: 'CNY', min: '1.00', max: '999.99' } };
  h.state.graphSelection.ids = new Set(['SYN-A']);
  const input = h.element('ndIdInput'); input.focus();
  const models = h.state.lastGraph.nodes.map((node, index) => ({ id: node.account, x: index ? 111 : 17, y: index ? 157 : 23,
    data: node, fundsLabel: 'old canonical label', ...(index ? {} : { label: 'old visible label' }) }));
  h.state.lastGraph.nodes[0].display_account = 'SYN-NEW-CARD';
  models[0].data = { ...models[0].data, display_account: 'SYN-OLD-CARD', out_cnt: 1 };
  const camera = [1.75, 0, 0, 0, 1.75, 0, 149, -87, 1];
  const history = h.run('window.FundsCanvasHistory.create({capture:()=>({positions:state.positions}),restore:async()=>true})');
  history.record('合成样式修改', { fill: '#111111' }, { fill: '#222222' });
  let autoPaint = true, paintCount = 0;
  h.state.graph = { getNodes: () => models.map(model => ({ getModel: () => model })),
    get: key => key === 'autoPaint' ? autoPaint : key === 'group' ? { getMatrix: () => camera } : null,
    setAutoPaint: value => { autoPaint = value; }, updateItem: (item, update) => { Object.assign(item.getModel(), update); },
    paint: () => { paintCount += 1; }, changeData() { assert.fail('identity refresh cannot replace the graph'); },
    changeSize() { assert.fail('identity refresh cannot resize the graph'); }, fitView() { assert.fail('identity refresh cannot fit the camera'); } };
  h.run('window.FundsCanvasEditor = {}').history = history;
  const before = { positions: JSON.stringify(h.state.positions), camera: JSON.stringify(camera), history: JSON.stringify(history.status()),
    edges: JSON.stringify(h.state.lastGraph.edges), revision: h.state.graphView.revision, view: h.state.graphView,
    filter: JSON.stringify(h.state.presentation), focus: h.run('nodeWorkspace.draft.focusValue') };
  h.network(path => { assert.equal(path, '/api/account-ids/list?case_id=1'); return [
    { account: 'SYN-A', id_no: '111111111111111' }, { account: 'SYN-B', id_no: '222222222222222' } ]; });

  await h.run('refreshAccountIdentity(caseContext())');

  assert.equal(h.state.lastGraph.nodes[0].id_no, '111111111111111'); assert.match(models[0].label, /111111111111111/);
  assert.equal(models[0].fundsLabel, models[0].label); assert.match(models[1].fundsLabel, /222222222222222/);
  assert.match(models[0].fundsLabel, /SYN-OLD-CARD/); assert.equal(models[0].data.out_cnt, 1);
  assert.equal(Object.hasOwn(models[1], 'label'), false, 'an absent LOD label remains absent');
  assert.deepEqual(models.map(model => [model.x, model.y]), [[17, 23], [111, 157]]);
  assert.equal(JSON.stringify(h.state.positions), before.positions); assert.equal(JSON.stringify(camera), before.camera);
  assert.equal(JSON.stringify(history.status()), before.history); assert.equal(history.status().canUndo, true);
  assert.equal(JSON.stringify(h.state.lastGraph.edges), before.edges);
  assert.equal(h.state.graphView, before.view); assert.equal(h.state.graphView.revision, before.revision);
  assert.deepEqual([...h.state.graphView.visibleIds], ['SYN-A', 'SYN-B']); assert.equal(h.state.graphView.root, 'SYN-A');
  assert.deepEqual([...h.state.graphSelection.ids], ['SYN-A']); assert.equal(JSON.stringify(h.state.presentation), before.filter);
  assert.equal(h.element('ndIdInput'), input); assert.equal(input.value, '111111111111111'); assert.equal(h.document.activeElement, input);
  assert.equal(h.run('nodeWorkspace.draft.idBase'), '111111111111111'); assert.equal(h.run('nodeWorkspace.draft.focusValue'), before.focus);
  assert.equal(h.element('ndDirty').textContent, ''); assert.equal(h.graphReads, 0); assert.equal(h.paints, 0);
  assert.equal(autoPaint, true); assert.ok(paintCount > 0);
});

test('a same-case bulk identity write disables account controls and guards stale account-save callbacks', async () => {
  const h = harness(); await ready(h);
  const actions = h.run('window.FundsDataToolsActions = installDataTools()'), pending = deferred();
  h.network((path, options) => path === '/api/account-ids/import' ? pending.promise
    : path === '/api/account-ids/list?case_id=1' ? [{ account: 'SYN-A', id_no: '111111111111111' }] : []);
  assert.equal(actions.open('import', h.element('btnDataActions')), true); assert.equal(await actions.pick(), true);
  const operation = actions.submit(); await flush();
  assert.equal(actions.blocked({ caseId: 1 }), true); assert.equal(actions.blocked({ caseId: 2 }), false);
  assert.equal(h.element('ndIdInput').disabled, true); assert.equal(h.element('ndIdSave').disabled, true);
  // A callback queued before the field became disabled still cannot issue a write.
  h.element('ndIdInput').value = 'SYN-QUEUED-ACCOUNT-EDIT';
  await h.run('writeNodeWorkspace(nodeWorkspace, "id")');
  assert.equal(writes(h).length, 1); assert.equal(writes(h)[0].path, '/api/account-ids/import');
  pending.resolve(response({ ok: true, imported: 1, invalid: 0, blank_account: 0 })); await operation;
  assert.equal(actions.blocked({ caseId: 1 }), false); assert.equal(h.element('ndIdInput').disabled, false);
  assert.equal(writes(h).length, 1); assert.equal(h.graphReads, 0);
});

test('unknown identity import reconciles fresh same-case metadata after A-B-A without retrying or changing the working graph', async () => {
  const h = harness(); await ready(h);
  const actions = h.run('window.FundsDataToolsActions = installDataTools()');
  const origin = JSON.parse(h.run('JSON.stringify(caseContext())'));
  let storedRows = [];
  const network = (path, options) => {
    if (path === '/api/account-ids/import') {
      storedRows = [{ account: 'SYN-A', id_no: '111111111111111' }, { account: 'SYN-B', id_no: '222222222222222' }];
      throw new Error('Synthetic response lost after storing new identities');
    }
    if (path === '/api/account-ids/list?case_id=1') return storedRows;
    throw new Error('Unexpected recovery request ' + path);
  };
  h.network(network); assert.equal(actions.open('import', h.element('btnDataActions')), true);
  assert.equal(await actions.pick(), true); assert.equal(await actions.submit(), false);
  assert.equal(actions.status().unknown, true); assert.equal(writes(h).length, 1);
  assert.equal(h.state.lastGraph.nodes[0].id_no, 'SYN-ORIGINAL');
  h.selectCase(2); h.selectCase(1); await ready(h); h.state.graphLoading = false; h.state.showIds = true;
  assert.notEqual(h.state.caseGeneration, origin.generation);
  h.state.positions = { 'SYN-A': { x: 31, y: 47 }, 'SYN-B': { x: 107, y: 163 } };
  h.state.graphSelection.ids = new Set(['SYN-A']); h.state.graphView.visibleIds = new Set(['SYN-A', 'SYN-B']);
  const models = h.state.lastGraph.nodes.map((node, index) => ({ id: node.account,
    x: index ? 107 : 31, y: index ? 163 : 47, data: node, fundsLabel: 'old label', ...(index ? {} : { label: 'old label' }) }));
  const camera = [1.5, 0, 0, 0, 1.5, 0, 137, -59, 1]; let autoPaint = true;
  h.state.graph = { getNodes: () => models.map(model => ({ getModel: () => model })),
    get: key => key === 'autoPaint' ? autoPaint : key === 'group' ? { getMatrix: () => camera } : null,
    setAutoPaint: value => { autoPaint = value; }, updateItem: (item, update) => { Object.assign(item.getModel(), update); }, paint() {},
    changeData() { assert.fail('reconciliation cannot replace graph data'); }, fitView() { assert.fail('reconciliation cannot fit the camera'); } };
  const history = h.run('window.FundsCanvasHistory.create({capture:()=>({positions:state.positions}),restore:async()=>true})');
  history.record('合成保留历史', { size: 28 }, { size: 32 }); h.run('window.FundsCanvasEditor = {}').history = history;
  const before = { positions: JSON.stringify(h.state.positions), camera: JSON.stringify(camera), history: JSON.stringify(history.status()),
    edges: JSON.stringify(h.state.lastGraph.edges), view: h.state.graphView, revision: h.state.graphView.revision, input: h.element('ndIdInput') };
  h.network(network); assert.equal(actions.open('import', h.element('btnDataActions')), true);
  assert.equal(actions.status().context.generation, h.state.caseGeneration);

  assert.deepEqual(await actions.reconcile(), storedRows);

  assert.equal(actions.status().unknown, true, 'an authoritative read does not infer whether the lost write committed');
  assert.equal(actions.status().reconciled, true); assert.equal(actions.blocked({ caseId: 1 }), true);
  assert.equal(h.state.lastGraph.nodes[0].id_no, storedRows[0].id_no); assert.match(models[0].label, /111111111111111/);
  assert.match(models[1].fundsLabel, /222222222222222/); assert.equal(Object.hasOwn(models[1], 'label'), false);
  assert.equal(h.element('ndIdInput'), before.input); assert.equal(before.input.value, storedRows[0].id_no);
  assert.equal(h.run('nodeWorkspace.draft.idBase'), storedRows[0].id_no); assert.equal(h.element('ndDirty').textContent, '');
  assert.equal(actions.acknowledge(), true); assert.equal(actions.blocked({ caseId: 1 }), false);
  assert.equal(JSON.stringify(h.state.positions), before.positions); assert.equal(JSON.stringify(camera), before.camera);
  assert.deepEqual(models.map(model => [model.x, model.y]), [[31, 47], [107, 163]]);
  assert.equal(JSON.stringify(history.status()), before.history); assert.equal(JSON.stringify(h.state.lastGraph.edges), before.edges);
  assert.equal(h.state.graphView, before.view); assert.equal(h.state.graphView.revision, before.revision);
  assert.deepEqual([...h.state.graphSelection.ids], ['SYN-A']); assert.deepEqual([...h.state.graphView.visibleIds], ['SYN-A', 'SYN-B']);
  assert.equal(h.graphReads, 0); assert.equal(h.paints, 0); assert.equal(autoPaint, true);
  assert.equal(writes(h).length, 1); assert.equal(writes(h)[0].options.body.get('case_id'), '1');
  assert.equal(h.requests.filter(request => request.path === '/api/account-ids/list?case_id=1').length, 1);
});

test('an account write keeps its case lease across A-B-A reset and blocks bulk import until its response settles', async () => {
  const h = harness(); await ready(h);
  const actions = h.run('window.FundsDataToolsActions = installDataTools()'), pending = deferred();
  h.network((path, options) => path === '/api/account-ids/set' ? pending.promise : []);
  await h.edit('ndIdInput', 'SYN-DELAYED-ACCOUNT-SAVE');
  const operation = h.element('ndIdSave').click(); await flush();
  assert.equal(h.run('accountDataWrites.size'), 1); assert.equal(writes(h).length, 1);
  h.selectCase(2); h.selectCase(1); h.state.graphLoading = false;
  assert.equal(h.run('nodeWrites.size'), 0); assert.equal(h.run('accountDataWrites.size'), 1);
  assert.equal(actions.open('import', h.element('btnDataActions')), false);
  assert.equal(writes(h).length, 1, 'resetting a drawer must not release the old case write lease');
  pending.resolve({ ok: true, id_no: 'SYN-DELAYED-ACCOUNT-SAVE' }); await operation;
  assert.equal(h.run('accountDataWrites.size'), 0); assert.equal(actions.open('import', h.element('btnDataActions')), true);
  assert.equal(writes(h).length, 1); assert.equal(h.graphReads, 0);
});

test('an unsubmitted crop reports an expired revision without starting a request', async () => {
  const h = harness(); await ready(h); h.crop(); h.state.graphView.revision += 1;
  await h.element('pcConfirm').click(); await flush();
  assert.equal(writes(h).length, 0); assert.equal(h.element('pcClose').disabled, false); assert.equal(h.element('pcConfirm').disabled, true);
  assert.match(h.element('pcStatus').textContent, /重新打开|已变化|已失效/);
});

test('account and counterparty primary summaries use evidence ranges without losing original facts', async () => {
  const h = harness();
  const exact = {status:'exact',count:{lower:1,upper:1},totals:[{currency:'CNY',amount:'100.00',lower:'100.00',upper:'100.00'}]};
  const bounded = {status:'bounded',count:{lower:0,upper:2},totals:[{currency:'CNY',amount:null,lower:'0.00',upper:'200.00'}]};
  const data = profile({in_funds:exact,out_funds:bounded, top_counterparties:[{acct:'SYN-COUNTER',name:'合成对手',in_funds:exact,out_funds:bounded,
    in_cnt:2,out_cnt:2,in_amt:'200.00',out_amt:'200.00'}]});
  networkReady(h, () => data); await h.open();
  assert.match(h.element('ndStats').textContent, /转入 1 笔.*¥100\.00.*转出 —.*0\.00–200\.00（待核）/s);
  assert.match(h.element('ndCounterTbody').textContent, /1 笔.*¥100\.00.*—.*0\.00–200\.00（待核）/s);
  assert.equal(data.in_cnt,13); assert.equal(data.out_cnt,12);
});

test('a single associated card remains in the existing details while the sourced account is the heading', async () => {
  const h=harness();
  networkReady(h,()=>profile({account_number:'000SYN-ACCOUNT',display_account:'000SYN-CARD',cards:['000SYN-CARD']}));
  await h.open();
  const identity=h.element('drawerBody').querySelector('.node-identity');
  assert.match(identity.textContent,/000SYN-ACCOUNT/);
  const details=identity.querySelector('details');
  assert.ok(details); assert.match(details.textContent,/关联卡号/);
  assert.match(details.textContent,/000SYN-CARD/);
});

test('node detail and graph statistics use the selected scoped node without rewriting case facts', async () => {
  const h = harness(); await ready(h, { display_account: 'SYN-NEW-CARD' });
  const scoped = graphNode('SYN-A', { display_account: 'SYN-OLD-CARD', cards: ['SYN-OLD-CARD', 'SYN-NEW-CARD'], display_basis: 'transaction_card', out_cnt: 1,
    out_totals: [{ currency: 'CNY', amount: '10.25' }] });
  h.state.graph = { findById: () => ({ getModel: () => ({ data: scoped }) }) };
  await h.open();
  assert.match(h.element('drawerBody').textContent, /SYN-OLD-CARD/);
  assert.equal(h.run('nodeWorkspace.node.display_account'), 'SYN-OLD-CARD');
  await h.element('ndScopeGraph').click();
  assert.match(h.element('ndStats').textContent, /10.25/);
  assert.doesNotMatch(h.element('ndStats').textContent, /9,007,199,254,740,993\.07/);
  await h.element('ndScopeCase').click();
  assert.match(h.element('ndStats').textContent, /9,007,199,254,740,993\.07/);
  assert.equal(h.state.lastGraph.nodes[0].display_account, 'SYN-NEW-CARD');
});
