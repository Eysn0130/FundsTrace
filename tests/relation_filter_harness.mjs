import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const sources = ['money.js', 'graph-relations.js', 'graph-presentation.js', 'relation-filter.js']
  .map(name => readFileSync(new URL(`../static/${name}`, import.meta.url), 'utf8'));
const flush = async () => { for (let pass = 0; pass < 20; pass++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const clone = value => JSON.parse(JSON.stringify(value));

class Element {
  constructor(id) { this.id = id; this.value = ''; this.textContent = ''; this.hidden = false; this.disabled = false; this.attributes = {}; this.listeners = new Map(); this.children = []; }
  addEventListener(type, listener) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(listener); }
  emit(type, properties = {}) {
    const event = { type, target: this, defaultPrevented: false, propagationStopped: false,
      preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.propagationStopped = true; }, ...properties };
    for (const listener of this.listeners.get(type) || []) listener(event);
    return event;
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  get textContent() { return (this._text || '') + (this.children || []).map(child => child.textContent).join(''); }
  set textContent(value) { this._text = String(value); this.children = []; }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  focus() { this.focused = true; }
}

function harness() {
  const elements = new Map(), timers = new Map(), commits = [], previews = [], places = [], closes = [];
  const element = id => { if (!elements.has(id)) elements.set(id, new Element(id)); return elements.get(id); };
  const doc = new Element('document'); doc.getElementById = element; doc.createElement = tag => new Element(tag);
  doc.createTextNode = text => { const node = new Element('#text'); node.textContent = text; return node; };
  let timerId = 0, menuOpen = false, blocked = '', onPreview = null, onCommit = null, controller;
  const root = { document: doc, console, setTimeout(fn) { timers.set(++timerId, fn); return timerId; }, clearTimeout(id) { timers.delete(id); } };
  root.window = root;
  const context = vm.createContext(root);
  for (const source of sources) vm.runInContext(source, context);
  const graph = { nodes: ['A', 'B', 'C', 'D', 'E', 'F'].map(account => ({ account })), edges: [
    { source: 'A', target: 'B', amount_totals: [{ currency: 'CNY', amount: '0.01' }] },
    { source: 'B', target: 'C', amount_totals: [{ currency: 'CNY', amount: '0.09' }, { currency: 'USD', amount: '1.00' }] },
    { source: 'D', target: 'E', amount_totals: [{ currency: 'CNY', amount: '0.10' }] },
    { source: 'E', target: 'F', amount_totals: [{ currency: 'CNY', amount: '0.11' }] },
    { source: 'A', target: 'F', amount_totals: [{ currency: 'USD', amount: '0.02' }] },
  ] };
  const state = { binding: 'case:2/1/canvas:3/1', graph, presentation: root.FundsGraphPresentation.create({}),
    view: { index: null, revision: 0 }, visibleIds: null, ready: true };
  const reindex = () => {
    const data = root.FundsGraphPresentation.displayData(state.graph, state.presentation, state.visibleIds);
    state.view.index = { nodeIds: data.nodeIds, edgeKeys: data.edgeKeys }; state.view.revision++;
  };
  reindex();
  const read = () => ({ ...state, filter: state.presentation.relationFilter,
    currencies: root.FundsGraphRelations.currencyChoices(state.graph.edges) });
  root.FundsToolbarMenu = {
    open() { menuOpen = true; doc.emit('funds:toolbar-menu-change', { detail: { open: true, menuId: 'relationFilterPopover' } }); return true; },
    close(reason) { if (!menuOpen) return false; menuOpen = false; closes.push(reason); doc.emit('funds:toolbar-menu-change', { detail: { open: false, reason, menuId: 'relationFilterPopover' } }); return true; },
    place(panel, trigger) { places.push([panel.id, trigger.id]); },
  };
  const realPreview = (filter, snapshot) => {
    const result = root.FundsGraphPresentation.displayData(snapshot.graph, { ...snapshot.presentation, relationFilter: filter }, snapshot.visibleIds);
    return { nodes: result.nodes.length, edges: result.edges.length };
  };
  const realCommit = (filter, binding) => {
    if (binding !== state.binding) return false;
    state.presentation = root.FundsGraphPresentation.create({ ...state.presentation, relationFilter: filter });
    reindex(); controller.update(); return true;
  };
  controller = root.FundsRelationFilter.create({ document: doc, read, blocker: () => blocked,
    preview(filter, snapshot) { previews.push({ filter: clone(filter), binding: snapshot.binding }); return onPreview ? onPreview(filter, snapshot) : realPreview(filter, snapshot); },
    commit(filter, binding) { commits.push({ filter: clone(filter), binding }); return onCommit ? onCommit(filter, binding, realCommit) : realCommit(filter, binding); },
    formatCount: root.FundsMoney.count });
  const input = (id, value) => { element(id).value = value; element(id).emit(id === 'relationCurrency' ? 'change' : 'input'); };
  const drain = async () => { const work = [...timers.values()]; timers.clear(); for (const fn of work) fn(); await flush(); };
  return { controller, state, root, element, input, drain, commits, previews, places, closes, timers, reindex, read,
    onPreview(value) { onPreview = value; }, onCommit(value) { onCommit = value; }, setBlocked(value) { blocked = value; },
    async open() { controller.open(); await flush(); },
    range(currency = 'CNY', min = '0.00', max = '0.10') { input('relationCurrency', `currency:${currency}`); input('relationMin', min); input('relationMax', max); },
    switchBinding(binding) { state.binding = binding; controller.update(); },
  };
}

test('opening is read only and typing is a debounced, explicit draft with independent applied counts', async () => {
  const h = harness(); await h.open();
  assert.equal(h.commits.length, 0); assert.equal(h.controller.getState().dirty, false);
  assert.deepEqual(clone(h.controller.getState().preview), { nodes: 6, edges: 5 });
  assert.match(h.element('relationCurrentCounts').textContent, /当前显示 5 条关系 · 6 个账户/);
  assert.equal(h.element('relationMin').disabled, true);
  h.range();
  assert.equal(h.element('relationDraftState').textContent, '待应用');
  assert.equal(h.element('relationAppliedSummary').textContent, '已应用：全部关系');
  assert.equal(h.element('relationPreviewEdges').textContent, '—');
  assert.equal(h.timers.size, 1); await h.drain();
  assert.deepEqual(clone(h.controller.getState().preview), { nodes: 5, edges: 3 });
  assert.equal(h.state.presentation.relationFilter.active, false); assert.equal(h.commits.length, 0);
  assert.equal(h.element('btnRelationClear').disabled, true, 'unapplied conditions cannot masquerade as an applied filter');
  assert.equal(h.element('btnRelationApply').disabled, false);
  assert.ok(h.places.length > 0, 'growing content is positioned through the shared menu');
});

test('explicit application commits exact inclusive aggregate conditions once and unchanged reapplication is a no-op', async () => {
  const h = harness(); await h.open(); h.range(); await h.drain();
  assert.equal(await h.controller.apply(), true);
  assert.deepEqual(h.commits[0], { binding: h.state.binding, filter: { currency: 'CNY', min: '0.00', max: '0.10', active: true } });
  assert.equal(h.element('relationDraftState').textContent, '已应用');
  assert.match(h.element('relationAppliedSummary').textContent, /CNY · 0.00 — 0.10/);
  assert.match(h.element('relationCurrentCounts').textContent, /当前显示 3 条关系 · 5 个账户/);
  assert.equal(await h.controller.apply(), false); assert.equal(h.commits.length, 1);
  assert.equal(h.state.graph.edges.length, 5); assert.equal(h.state.graph.edges[1].amount_totals[1].currency, 'USD');
});

test('ordinary graph and presentation sync preserve a dirty draft and recompute the full composed scope', async () => {
  const h = harness(); await h.open(); h.range(); await h.drain();
  h.state.visibleIds = new Set(['A', 'B', 'C', 'D', 'E']);
  h.state.presentation.hiddenAccounts = ['B']; h.reindex(); h.controller.update(); await h.drain();
  assert.equal(h.element('relationMax').value, '0.10'); assert.equal(h.controller.getState().dirty, true);
  assert.deepEqual(clone(h.controller.getState().preview), { nodes: 4, edges: 1 });
  assert.equal(h.state.presentation.relationFilter.active, false); assert.equal(h.commits.length, 0);
});

test('closing or Escape through the shared menu discards the draft and reopening reads authoritative state', async () => {
  const h = harness(); await h.open(); h.range();
  h.root.FundsToolbarMenu.close('escape');
  assert.equal(h.controller.getState().open, false); assert.equal(h.timers.size, 0);
  await h.open();
  assert.equal(h.element('relationCurrency').value, 'all'); assert.equal(h.element('relationMin').value, '');
  assert.equal(h.controller.getState().dirty, false); assert.equal(h.commits.length, 0);
});

test('inline errors block submission, correction clears stale errors, and zero is a valid lower bound', async () => {
  const h = harness(); await h.open(); h.range('CNY', '0.10', '0.01'); await h.drain();
  assert.equal(h.element('relationMaxError').hidden, false); assert.match(h.element('relationMaxError').textContent, /最低金额/);
  assert.equal(h.element('relationMax').attributes['aria-invalid'], 'true');
  assert.equal(await h.controller.apply(), false); assert.equal(h.commits.length, 0);
  h.input('relationMax', '0.10'); assert.equal(h.element('relationMaxError').hidden, true);
  h.input('relationMin', '-1'); assert.equal(h.element('relationMinError').hidden, false);
  h.input('relationMin', '0'); await h.drain();
  assert.equal(h.element('relationMinError').hidden, true); assert.equal(await h.controller.apply(), true);
  assert.equal(h.commits[0].filter.min, '0.00');
});

test('selecting all currencies clears and disables amounts without applying or mixing currencies', async () => {
  const h = harness(); await h.open(); h.range(); h.input('relationCurrency', 'all'); await h.drain();
  assert.equal(h.element('relationMin').value, ''); assert.equal(h.element('relationMax').value, '');
  assert.equal(h.element('relationMin').disabled, true); assert.equal(h.element('relationMax').disabled, true);
  assert.equal(h.controller.getState().dirty, false); assert.equal(h.commits.length, 0);
  assert.deepEqual(clone(h.controller.getState().preview), { nodes: 6, edges: 5 });
});

test('unlabelled currency remains a genuine filter and absent applied or draft currencies remain explicit', async () => {
  const h = harness(); h.state.graph.edges[0].amount_totals.push({ currency: '', amount: '0.05' }); h.reindex();
  await h.open(); h.range('', '0', '0.10'); await h.drain();
  assert.deepEqual(clone(h.controller.getState().preview), { nodes: 2, edges: 1 });
  assert.equal(await h.controller.apply(), true); assert.equal(h.commits[0].filter.currency, '');
  h.state.graph = { nodes: h.state.graph.nodes, edges: [] }; h.reindex(); h.controller.update(); await h.drain();
  assert.equal(h.element('relationCurrency').value, 'currency:');
  assert.match(h.element('relationCurrency').children.find(option => option.value === 'currency:').textContent, /未标注币种.*当前图无此币种/);
  assert.deepEqual(clone(h.controller.getState().preview), { nodes: 0, edges: 0 });
  assert.match(h.element('relationFilterStatus').textContent, /当前画布暂无追踪关系/);
});

test('a draft currency removed during graph refresh is retained and never silently replaced with CNY', async () => {
  const h = harness(); await h.open(); h.range('USD', '', '');
  h.state.graph = { nodes: h.state.graph.nodes, edges: h.state.graph.edges.map(edge => ({ ...edge, amount_totals: edge.amount_totals.filter(row => row.currency !== 'USD') })) };
  h.reindex(); h.controller.update(); await h.drain();
  assert.equal(h.element('relationCurrency').value, 'currency:USD');
  assert.match(h.element('relationCurrency').children.find(option => option.value === 'currency:USD').textContent, /当前图无此币种/);
  assert.deepEqual(clone(h.controller.getState().preview), { nodes: 0, edges: 0 });
  assert.equal(h.element('btnRelationClear').disabled, true); assert.match(h.element('relationFilterStatus').textContent, /修改待应用条件/);
});

test('an empty tracked graph is distinguished from a nonempty graph with no qualifying relationships', async () => {
  const h = harness(); await h.open(); h.range('CNY', '1', '2'); await h.drain();
  assert.match(h.element('relationFilterStatus').textContent, /当前显示范围内没有符合条件的关系/);
  await h.controller.apply();
  assert.match(h.element('relationFilterStatus').textContent, /清除已应用筛选/);
  h.state.graph = { nodes: [], edges: [] }; h.reindex(); h.controller.update(); await h.drain();
  assert.equal(h.element('relationFilterStatus').textContent, '当前画布暂无追踪关系');
  assert.equal(h.element('btnRelationClear').disabled, false, 'an obsolete applied filter can still be cleared');
});

test('clear removes only the applied relation filter, preserves other presentation scope, and cannot create no-op history', async () => {
  const h = harness(); await h.open(); h.range(); await h.controller.apply();
  h.state.visibleIds = new Set(['A', 'B', 'C', 'D', 'E']); h.state.presentation.hiddenAccounts = ['B'];
  h.state.presentation.onlyAccounts = ['A', 'B', 'C', 'D', 'E'];
  h.state.presentation.amountWidth = { enabled: true, currency: 'USD' }; h.reindex(); h.controller.update();
  h.range('USD', '1.00', '9.00');
  assert.equal(await h.controller.clear(), true);
  assert.equal(h.state.presentation.relationFilter.active, false);
  assert.deepEqual(clone(h.state.presentation.hiddenAccounts), ['B']);
  assert.deepEqual(clone(h.state.presentation.onlyAccounts), ['A', 'B', 'C', 'D', 'E']);
  assert.equal(h.state.presentation.amountWidth.currency, 'USD');
  assert.deepEqual([...h.state.visibleIds], ['A', 'B', 'C', 'D', 'E']);
  assert.equal(h.element('relationCurrency').value, 'all');
  assert.equal(await h.controller.clear(), false); assert.equal(h.commits.length, 2);
});

test('duplicate submission and both buttons are locked until commit settles; failures preserve editable draft', async () => {
  const h = harness(), work = deferred(); await h.open(); h.range();
  h.onCommit(() => work.promise); const applying = h.controller.apply();
  assert.equal(h.controller.getState().pending, true); assert.equal(h.element('btnRelationApply').disabled, true);
  assert.equal(h.element('relationMin').disabled, true); assert.equal(await h.controller.apply(), false);
  assert.equal(await h.controller.clear(), false); assert.equal(h.commits.length, 1);
  work.reject(new Error('合成提交失败')); assert.equal(await applying, false);
  assert.equal(h.controller.getState().pending, false); assert.equal(h.element('relationMin').disabled, false);
  assert.match(h.element('relationFilterStatus').textContent, /合成提交失败/);
  assert.equal(h.element('relationMax').value, '0.10'); h.input('relationMax', '0.09');
  assert.doesNotMatch(h.element('relationFilterStatus').textContent, /合成提交失败/);
});

test('busy reasons are visible and prevent writes without discarding valid draft conditions', async () => {
  const h = harness(); await h.open(); h.range(); h.setBlocked('图谱布局正在计算，请稍后操作'); h.controller.update();
  assert.equal(h.element('btnRelationApply').disabled, true); assert.equal(h.element('relationFilterBusy').hidden, false);
  assert.match(h.element('relationFilterBusy').textContent, /布局/); assert.equal(await h.controller.apply(), false);
  assert.equal(h.commits.length, 0); assert.equal(h.element('relationMax').value, '0.10');
  h.setBlocked(''); h.controller.update(); assert.equal(h.element('btnRelationApply').disabled, false);
  assert.doesNotMatch(h.element('relationFilterStatus').textContent, /布局/);
});

test('case and canvas generation switches revoke timer work and reject A-B-A late preview results', async () => {
  const h = harness(), old = deferred(); await h.open(); h.onPreview(() => old.promise); h.range(); await h.drain();
  h.switchBinding('case:3/2/canvas:4/2'); assert.equal(h.controller.getState().open, false);
  h.switchBinding('case:2/3/canvas:3/3'); h.onPreview(null); await h.open();
  old.resolve({ nodes: 999999, edges: 888888 }); await flush();
  assert.deepEqual(clone(h.controller.getState().preview), { nodes: 6, edges: 5 });
  assert.equal(h.element('relationPreviewEdges').textContent, '5'); assert.ok(h.closes.includes('context'));
  h.range(); assert.equal(h.timers.size, 1); h.switchBinding('case:2/3/canvas:4/4'); assert.equal(h.timers.size, 0);
});

test('older asynchronous preview cannot replace a newer draft result within the same open session', async () => {
  const h = harness(), old = deferred(); await h.open(); h.onPreview(() => old.promise); h.range(); await h.drain();
  h.onPreview(null); h.input('relationMax', '0.01'); await h.drain();
  assert.deepEqual(clone(h.controller.getState().preview), { nodes: 2, edges: 1 });
  old.resolve({ nodes: 500, edges: 600 }); await flush();
  assert.deepEqual(clone(h.controller.getState().preview), { nodes: 2, edges: 1 });
});

test('late commits cannot write after binding changes and late failure cannot pollute a reopened session', async () => {
  const h = harness(), work = deferred(); await h.open(); h.range();
  h.onCommit(async (filter, binding, commit) => { await work.promise; return commit(filter, binding); });
  const applying = h.controller.apply(); h.switchBinding('case:2/1/canvas:3/2');
  await h.open(); work.resolve(); assert.equal(await applying, false);
  assert.equal(h.state.presentation.relationFilter.active, false); assert.equal(h.controller.getState().error, '');
  const failure = deferred(); h.range(); h.onCommit(() => failure.promise); const failed = h.controller.apply();
  h.controller.close(); await h.open(); assert.equal(h.controller.getState().pending, true);
  failure.reject(new Error('旧会话的错误')); assert.equal(await failed, false);
  assert.equal(h.controller.getState().error, ''); assert.equal(h.controller.getState().pending, false);
});

test('large counts retain every digit with grouping, complete title and aria text', async () => {
  const h = harness(); h.onPreview(() => ({ nodes: '123456789012345678901', edges: '987654321098765432109' })); await h.open();
  assert.equal(h.element('relationPreviewEdges').textContent, '987,654,321,098,765,432,109');
  assert.equal(h.element('relationPreviewNodes').textContent, '123,456,789,012,345,678,901');
  assert.match(h.element('relationPreviewEdges').title, /^987,654,321,098,765,432,109 条关系$/);
  assert.match(h.element('relationPreviewNodes').attributes['aria-label'], /123,456,789,012,345,678,901 个账户/);
  assert.equal(h.element('relationPreviewEdges').attributes['data-size'], 'extended');
});

test('Enter on an amount input submits once while IME, repeats and native button activation remain independent', async () => {
  const h = harness(); await h.open(); h.range();
  const panel = h.element('relationFilterPopover'), min = h.element('relationMin'), button = h.element('btnRelationApply');
  const composing = panel.emit('keydown', { target: min, key: 'Enter', isComposing: true });
  assert.equal(composing.defaultPrevented, false); assert.equal(h.commits.length, 0);
  panel.emit('keydown', { target: min, key: 'Enter', keyCode: 229 }); assert.equal(h.commits.length, 0);
  panel.emit('keydown', { target: min, key: 'Enter', repeat: true }); assert.equal(h.commits.length, 0);
  const native = panel.emit('keydown', { target: button, key: 'Enter' });
  assert.equal(native.defaultPrevented, false); assert.equal(h.commits.length, 0);
  const entered = panel.emit('keydown', { target: min, key: 'Enter' });
  assert.equal(entered.defaultPrevented, true); await flush(); assert.equal(h.commits.length, 1);
  button.emit('click'); await flush(); assert.equal(h.commits.length, 1);
});
