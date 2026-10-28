import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const app = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
const money = readFileSync(new URL('../static/money.js', import.meta.url), 'utf8');
const section = (start, end) => {
  const offset = app.indexOf(start), finish = app.indexOf(end, offset);
  assert.ok(offset >= 0 && finish > offset, `missing public source section ${start}`);
  return app.slice(offset, finish);
};
const flush = async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); };
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const decode = value => value.replace(/&(amp|lt|gt|quot|#39);/g, (_match, key) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" }[key]));
const batch = (id, overrides = {}) => ({ id, filename: `合成批次-${id}.csv`, mode: 'A', rows_imported: 2,
  rows_skipped: 0, rows_dup: 1, imported_at: '2026-10-16T12:34:56Z', ...overrides });

// Real source functions run against synthetic DOM/network boundaries. HTML
// replacement disconnects old controls; selectors and events follow ancestry.
function harness() {
  let document;
  const matches = (node, selector) => {
    if (!node || node.tagName === '#TEXT') return false;
    if (selector === ':focus') return document.activeElement === node;
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
      value = String(value); this.attributes.set(name, value);
      if (name === 'class') this.className = value;
      else if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase())] = value;
      else if (['hidden', 'disabled', 'checked'].includes(name)) this[name] = true;
      else if (name === 'tabindex') this.tabIndex = Number(value);
      else if (name === 'value') this.value = value;
      else if (name === 'style') for (const declaration of value.split(';')) { const [key, val] = declaration.split(':'); if (val) this.style[key.trim()] = val.trim(); }
    }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    removeAttribute(name) { this.attributes.delete(name); if (['hidden', 'disabled'].includes(name)) this[name] = false; }
    appendChild(child) { child.remove(); child.parentElement = this; this.children.push(child); return child; }
    append(...children) { children.forEach(child => this.appendChild(child)); }
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
  const search = add(document.body, 'tab-search'), panel = add(document.body, 'tab-batches'); panel.style.display = 'flex';
  for (const name of ['search', 'batches']) { const tab = add(document.body, `tab-${name}-button`, 'button'); tab.className = 'tab'; tab.dataset.tab = name; }
  add(panel, 'batchCount'); const viewport = add(panel, 'batchListScroll'); const table = add(viewport, 'batchTable', 'table'); add(table, 'batchList', 'tbody');
  const horizontal = add(panel, 'batchHorizontalScroll');
  add(horizontal, 'batchHorizontalPosition', 'input'); add(horizontal, 'batchScrollLeft', 'button'); add(horizontal, 'batchScrollRight', 'button');
  const feedback = add(panel, 'batchFeedback'); feedback.hidden = true; add(feedback, 'batchMessage'); add(feedback, 'batchRetry', 'button'); add(panel, 'btnBatchImport', 'button');
  const menu = add(document.body, 'batchMenu'); menu.hidden = true; menu.setAttribute('role', 'menu'); const remove = add(menu, 'batchDelete', 'button'); remove.setAttribute('role', 'menuitem');
  for (const id of ['ctxModal', 'importModal', 'impCommit', 'flowTbody', 'canvasSel', 'flowCkAll', 'caseSelect', 'analysisLink', 'btnImport']) add(document.body, id, id === 'btnImport' ? 'button' : 'div');
  let importClicks = 0; document.getElementById('btnImport').onclick = () => { importClicks += 1; };
  const requests = [], notices = [], confirmations = [], refreshStages = [];
  let network = async () => [], confirmation = () => true, refreshStage = async () => {};
  const state = { caseId: 1, caseGeneration: 1, canvasId: 0, canvasGeneration: 1, search: { page: 1 }, cases: [{ id: 1, name: '合成甲案' }, { id: 2, name: '合成乙案' }] };
  const window = new Element('#window'); window.innerWidth = 1000; window.innerHeight = 800;
  window.appendChild(document.root);
  window.location = { href: 'http://synthetic.invalid/?case_id=1' }; window.history = { replaceState(_state, _title, url) { window.location.href = String(url); } };
  const context = vm.createContext({ state, document, window, exportWorkspace: null, caseWorkspace: null, URL, Set, Map,
    $: selector => document.querySelector(selector), $$: selector => document.querySelectorAll(selector),
    api: (path, options) => { requests.push({ path, options }); return network(path, options); },
    fetch: async (path, options) => {
      requests.push({ path, options }); const value = await network(path, options);
      return { ok: true, status: 200, json: async () => value, text: async () => JSON.stringify(value) };
    },
    confirm: message => { confirmations.push(message); return confirmation(message); },
    toast: (message, type) => notices.push({ message, type }),
    refreshStats: async () => { refreshStages.push('stats'); await refreshStage('stats'); },
    loadGraph: async () => { refreshStages.push('graph'); await refreshStage('graph'); },
    doSearch: async () => { refreshStages.push('search'); await refreshStage('search'); },
    preferenceStorage: { setItem() {} }, cancelGraphLayout() {}, resetGraphView() {}, newGraphPresentation: () => ({}),
    revokePhotoCrop() {}, resetNodeWorkspace() {}, syncEdgeTypeControl() {}, closeDrawer() {}, syncFlowCkCount() {}, updateHeader() {},
    // Shared horizontal geometry has its own harness. Keep this boundary small
    // while requiring the actual batch control elements to be passed through.
    bindTableHorizontalScroll(parts) { for (const node of Object.values(parts)) assert.ok(node); return { sync() {} }; },
    setTimeout: callback => { callback(); return 0; }, clearTimeout() {}, requestAnimationFrame: callback => { callback(); return 0; },
  });
  vm.runInContext(money, context);
  vm.runInContext(section('function esc(', 'function toast('), context);
  vm.runInContext(section('let pendingCanvasId = 0;', 'async function loadCases()'), context);
  vm.runInContext(section('function caseContext()', '/* ---------------- 初始化'), context);
  vm.runInContext(section('async function refreshAll()', '/* ---------------- 线标注'), context);
  vm.runInContext(section('async function loadBatches()', '/* ---------------- 事件绑定'), context);
  vm.runInContext(section('function switchTab(name)', 'function bindWorkspaceActions('), context);
  vm.runInContext('bindBatchUI()', context);
  return { state, document, window, requests, notices, confirmations, refreshStages,
    get importClicks() { return importClicks; }, element: id => document.getElementById(id),
    network(handler) { network = handler; }, confirm(handler) { confirmation = handler; }, refresh(handler) { refreshStage = handler; },
    load() { return vm.runInContext('loadBatches()', context); }, refreshAll() { return vm.runInContext('refreshAll()', context); },
    selectCase(id) { return vm.runInContext(`selectCase(${id})`, context); },
    switchTab(name) { context.selectedTab = name; return vm.runInContext('switchTab(selectedTab)', context); },
    rows() { return document.querySelectorAll('#batchList tr[data-batch-id]'); },
    click(id) { const node = document.getElementById(id); assert.ok(node, `missing #${id}`); return node.click(); },
    key(node, key) { return node.fire('keydown', { key }); },
  };
}

async function listed(h, rows = [batch(17)]) { h.network(async () => rows); await h.load(); }
async function menuFor(h, index = 0) { const trigger = h.element(`batch-more-${index}`); assert.ok(trigger, 'rendered row has its menu trigger'); trigger.focus(); await trigger.click(); return trigger; }
const writes = h => h.requests.filter(request => request.options?.method === 'DELETE');

test('same-case batch responses publish only the latest request', async () => {
  const h = harness(), older = deferred(), latest = deferred(); let reads = 0;
  h.network(() => ++reads === 1 ? older.promise : latest.promise);
  const first = h.load(), second = h.load();
  latest.resolve([batch(22)]); await second; older.resolve([batch(11)]); await first;
  assert.deepEqual(h.rows().map(row => row.dataset.batchId), ['22']);
  assert.equal(h.element('batchList').textContent.includes('合成批次-11.csv'), false);
});

test('A-B-A navigation revokes a pending list and retained row actions', async () => {
  const h = harness(); await listed(h); const oldTrigger = h.element('batch-more-0'), pending = deferred(); h.network(() => pending.promise);
  const read = h.load(); h.selectCase(2); h.selectCase(1);
  assert.equal(oldTrigger.isConnected, false);
  await oldTrigger.click();
  assert.equal(h.element('batchMenu').hidden, true);
  pending.resolve([batch(99)]); await read;
  assert.equal(h.rows().length, 0);
  assert.equal(writes(h).length, 0);
});

test('batch rows escape filenames and retain full counts including zero imported and duplicates', async () => {
  const h = harness(), filename = '<img src=x onerror="SYN"> & "合成".csv';
  await listed(h, [batch(17, { filename, rows_imported: '900719925474099300001', rows_skipped: '20000000000000001', rows_dup: '80000000000000009' }),
    batch(18, { rows_imported: 0, rows_dup: 7 })]);
  const html = h.element('batchList').innerHTML, rows = h.rows();
  assert.equal(html.includes('<img src=x'), false);
  assert.equal(rows[0].textContent.includes(filename), true);
  assert.match(rows[0].textContent, /900,719,925,474,099,300,001/);
  assert.match(rows[0].textContent, /20,000,000,000,000,001/);
  assert.match(rows[0].textContent, /80,000,000,000,000,009/);
  assert.match(rows[1].textContent, /0/); assert.match(rows[1].textContent, /7/);
});

test('read failure offers retry and an empty case retains the import action', async () => {
  const h = harness(); h.network(async () => { throw new Error('合成读取故障'); }); await h.load();
  assert.equal(h.element('batchFeedback').hidden, false); assert.match(h.element('batchMessage').textContent, /失败|故障/);
  assert.equal(h.element('batchRetry').hidden, false);
  h.network(async () => []); await h.click('batchRetry'); await flush();
  assert.equal(h.rows().length, 0); assert.match(h.element('tab-batches').textContent, /暂无|还没有|尚无/);
  await h.click('btnBatchImport'); assert.equal(h.importClicks, 1);
});

test('cached rows disable their menus after a read failure and reenable after a successful retry', async () => {
  const h = harness(); await listed(h); const trigger = h.element('batch-more-0');
  assert.equal(trigger.disabled, false);
  h.network(async () => { throw new Error('合成缓存刷新故障'); }); await h.load();
  assert.deepEqual(h.rows().map(row => row.dataset.batchId), ['17'], 'a failed read retains the cached batch');
  assert.equal(h.element('batch-more-0'), trigger);
  assert.equal(trigger.disabled, true, 'the retained row must not expose an inert enabled menu control');
  h.network(async () => [batch(17)]); await h.click('batchRetry'); await flush();
  const refreshed = h.element('batch-more-0');
  assert.equal(refreshed.disabled, false);
  await refreshed.click(); assert.equal(h.element('batchMenu').hidden, false);
});

test('batch menu Escape restores its trigger and navigation closes stale menus', async () => {
  const h = harness(); await listed(h); const trigger = await menuFor(h);
  assert.equal(h.element('batchMenu').hidden, false);
  h.key(h.element('batchDelete'), 'Escape');
  assert.equal(h.element('batchMenu').hidden, true); assert.equal(h.document.activeElement, trigger);
  await menuFor(h); h.switchTab('search'); assert.equal(h.element('batchMenu').hidden, true);
  h.switchTab('batches'); await flush(); await menuFor(h); h.selectCase(2);
  assert.equal(h.element('batchMenu').hidden, true); assert.equal(writes(h).length, 0);
});

test('Tab, outside pointer gesture, scroll, resize and deliberate focus departure dismiss the row menu', async () => {
  for (const dismissal of [h => h.key(h.element('batchDelete'), 'Tab'), h => h.element('btnImport').fire('pointerdown'),
    h => h.element('batchListScroll').fire('scroll'), h => h.window.fire('resize'), h => h.element('btnImport').focus()]) {
    const h = harness(); await listed(h); await menuFor(h); dismissal(h); await flush();
    assert.equal(h.element('batchMenu').hidden, true); assert.equal(writes(h).length, 0);
  }
});

test('a pending confirmation is deduplicated and cancel leaves the batch intact', async () => {
  const h = harness(); await listed(h); const answer = deferred(); h.confirm(() => answer.promise);
  await menuFor(h); const first = h.click('batchDelete'); await flush(); await h.click('batchDelete');
  assert.equal(h.confirmations.length, 1);
  answer.resolve(false); await first; await flush();
  assert.equal(writes(h).length, 0); assert.equal(h.rows().length, 1);
});

test('withdrawal confirmation explains remaining support even for a duplicate-only batch', async () => {
  const h = harness(); await listed(h, [batch(17, { rows_imported: 0, rows_dup: 7 })]);
  h.confirm(() => false); await menuFor(h); await h.click('batchDelete');
  assert.match(h.confirmations[0], /撤回这次导入的流水和资料/);
  assert.match(h.confirmations[0], /其他导入仍有支持的流水及其图谱引用会保留/);
  assert.doesNotMatch(h.confirmations[0], /移除该批次的 0 笔流水/);
  assert.equal(writes(h).length, 0);
});

test('an old asynchronous confirmation after A-B-A cannot submit or alter the new list', async () => {
  const h = harness(); await listed(h); const answer = deferred(); h.confirm(() => answer.promise);
  await menuFor(h); const remove = h.click('batchDelete'); await flush();
  assert.equal(h.confirmations.length, 1); h.selectCase(2); h.selectCase(1);
  await listed(h, [batch(31)]); answer.resolve(true); await remove; await flush();
  assert.equal(writes(h).length, 0); assert.deepEqual(h.rows().map(row => row.dataset.batchId), ['31']);
  assert.equal(h.notices.length, 0);
});

test('a confirmed deletion stays bound to its original case and batch and rejects duplicate writes', async () => {
  const h = harness(); await listed(h); const deletion = deferred(); h.network((path, options) => options?.method === 'DELETE' ? deletion.promise : Promise.resolve([]));
  await menuFor(h); const first = h.click('batchDelete'); await flush(); await h.click('batchDelete');
  assert.equal(writes(h).length, 1); assert.equal(writes(h)[0].path, '/api/batches/17?case_id=1');
  h.selectCase(2); h.selectCase(1); await listed(h, [batch(31)]);
  deletion.resolve({ ok: true }); await first; await flush();
  assert.deepEqual(h.rows().map(row => row.dataset.batchId), ['31']); assert.equal(h.notices.length, 0); assert.equal(h.refreshStages.length, 0);
});

test('delete service failure retains the row and a later explicit attempt may retry', async () => {
  const h = harness(); await listed(h); let attempts = 0;
  h.network(async (_path, options) => { if (options?.method === 'DELETE') { attempts += 1; throw new Error('合成删除故障'); } return [batch(17)]; });
  await menuFor(h); await h.click('batchDelete'); await flush();
  assert.equal(h.rows().length, 1); assert.match(h.element('batchMessage').textContent, /删除.*失败|合成删除故障/);
  await menuFor(h); await h.click('batchDelete'); await flush(); assert.equal(attempts, 2);
});

test('successful deletion is not relabeled as failed when the subsequent refresh fails', async () => {
  const h = harness(); await listed(h); h.network(async (_path, options) => options?.method === 'DELETE' ? { ok: true } : []);
  h.refresh(async () => { throw new Error('合成刷新故障'); });
  await menuFor(h); await h.click('batchDelete'); await flush();
  assert.equal(writes(h).length, 1); assert.equal(h.rows().length, 0);
  const messages = h.element('batchMessage').textContent + h.notices.map(item => item.message).join(' ');
  assert.match(messages, /批次已删除|删除成功/); assert.match(messages, /刷新.*(?:失败|故障|未完成)/); assert.doesNotMatch(messages, /删除失败/);
});

test('a list started during deletion cannot restore its deleted row after a refresh failure', async () => {
  const h = harness(); await listed(h); const deletion = deferred(), oldList = deferred();
  h.network((_path, options) => options?.method === 'DELETE' ? deletion.promise : oldList.promise);
  h.refresh(async () => { throw new Error('合成刷新故障'); });
  await menuFor(h); const remove = h.click('batchDelete'); await flush();
  assert.equal(writes(h).length, 1);
  h.switchTab('search'); h.switchTab('batches'); await flush();
  assert.equal(h.requests.filter(request => !request.options?.method).length, 2, 'returning to batches has started a read while deletion is pending');
  deletion.resolve({ ok: true }); await remove; await flush();
  assert.equal(h.rows().length, 0);
  const receipt = h.element('batchMessage').textContent;
  assert.match(receipt, /批次已删除/); assert.match(receipt, /刷新.*(?:失败|故障|未完成)/);
  oldList.resolve([batch(17)]); await flush();
  assert.equal(h.rows().length, 0, 'a read started before deletion completed must not resurrect its removed batch');
  assert.equal(h.element('batchMessage').textContent, receipt, 'the stale list must not erase the durable deletion and refresh notice');
});

test('refreshAll stops its old-case continuation immediately after a case switch', async () => {
  const h = harness(), stats = deferred(); h.refresh(stage => stage === 'stats' ? stats.promise : Promise.resolve());
  const refresh = h.refreshAll(); h.selectCase(2); h.selectCase(1); stats.resolve(); await refresh;
  assert.deepEqual(h.refreshStages, ['stats']); assert.equal(h.requests.length, 0);
});
