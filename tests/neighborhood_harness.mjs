import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/neighborhood.js', import.meta.url), 'utf8');
const flush = async () => { for (let count = 0; count < 25; count++) await Promise.resolve(); };
const clone = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
class Element {
  constructor(id) { this.id = id; this.value = ''; this.hidden = false; this.disabled = false; this.attributes = {}; this.listeners = new Map(); this.children = []; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, listener) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(listener); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  focus() { this.focused = true; }
  emit(name, properties = {}) {
    const event = { target: this, defaultPrevented: false, propagationStopped: false,
      preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.propagationStopped = true; }, ...properties };
    for (const listener of this.listeners.get(name) || []) listener(event);
    return event;
  }
}
function harness() {
  const elements = new Map(), commits = [], expands = [], locates = [], previews = [], closes = [], places = [];
  const el = id => { if (!elements.has(id)) elements.set(id, new Element(id)); return elements.get(id); };
  const doc = new Element('document'); doc.getElementById = el; doc.createElement = tag => new Element(tag);
  doc.createTextNode = value => { const node = new Element('#text'); node.textContent = value; return node; };
  const root = { document: doc, console }; root.window = root;
  vm.runInContext(source, vm.createContext(root));
  let menuOpen = false, blocked = '', onPreview, onLocate, onCommit, onExpand, controller;
  const state = { binding: 'case:2/1/canvas:3/1', ready: true, revision: 1, source: {}, accounts: [
    { account: '00123', name: '同名企业' }, { account: '00456', name: '同名企业' }, { account: '00999', name: '唯一户名' },
    { account: '000GRAPH-00001', name: '合成节点 1' }, { account: '000GRAPH-00003', name: '合成节点 3' },
  ], applied: { root: '000GRAPH-00001', name: '合成节点 1', direction: 'both', nodes: 3, edges: 2, expanded: 2, remaining: 0, totalNeighbors: 2 } };
  const read = () => ({ ...state, applied: { ...state.applied } });
  root.FundsToolbarMenu = {
    open() { menuOpen = true; doc.emit('funds:toolbar-menu-change', { detail: { open: true, menuId: 'neighborPopover' } }); return true; },
    close(reason) { if (!menuOpen) return false; menuOpen = false; closes.push(reason); doc.emit('funds:toolbar-menu-change', { detail: { open: false, reason, menuId: 'neighborPopover' } }); return true; },
    place(panel, trigger) { places.push([panel.id, trigger.id]); },
  };
  const realCommit = (draft, binding) => {
    if (binding !== state.binding) return false;
    const item = state.accounts.find(item => item.account === draft.account); if (!item) return false;
    state.applied = { root: item.account, name: item.name, direction: draft.direction, nodes: 2, edges: 1, expanded: 1, remaining: 0, totalNeighbors: 1 };
    state.revision++; controller.update(); return true;
  };
  const realExpand = binding => {
    if (binding !== state.binding || !Number(state.applied.remaining)) return false;
    const addition = Math.min(50, Number(state.applied.remaining));
    state.applied = { ...state.applied, expanded: Number(state.applied.expanded) + addition, remaining: Number(state.applied.remaining) - addition,
      nodes: Number(state.applied.nodes) + addition, edges: Number(state.applied.edges) + addition };
    state.revision++; controller.update(); return true;
  };
  controller = root.FundsNeighborhood.create({ document: doc, read, blocker: () => blocked,
    preview(draft, snapshot) { previews.push({ draft: clone(draft), binding: snapshot.binding }); return onPreview ? onPreview(draft, snapshot) : { nodes: 2, edges: 1, rootFound: true, remaining: 0 }; },
    locate(account, binding) { locates.push({ account, binding }); return onLocate ? onLocate(account, binding) : { ok: true }; },
    commit(draft, binding) { commits.push({ draft: clone(draft), binding }); return onCommit ? onCommit(draft, binding, realCommit) : realCommit(draft, binding); },
    expand(binding) { expands.push({ binding, root: state.applied.root, direction: state.applied.direction }); return onExpand ? onExpand(binding, realExpand) : realExpand(binding); },
    formatCount: value => String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ',') });
  const input = value => { el('neighborAccount').value = value; el('neighborAccount').emit('input'); };
  const direction = value => el(`neighborDirection${value[0].toUpperCase()}${value.slice(1)}`).emit('click');
  return { controller, state, root, doc, el, input, direction, commits, expands, locates, previews, closes, places,
    async open() { controller.open(); await flush(); },
    setBlocked(value) { blocked = value; controller.update(); },
    onPreview(value) { onPreview = value; }, onLocate(value) { onLocate = value; }, onCommit(value) { onCommit = value; }, onExpand(value) { onExpand = value; },
    switchBinding(binding) { state.binding = binding; state.revision++; controller.update(); },
  };
}

test('open shows authoritative current scope without changing camera or graph', async () => {
  const h = harness(); await h.open();
  assert.equal(h.locates.length, 0); assert.equal(h.commits.length, 0); assert.equal(h.expands.length, 0);
  assert.equal(h.el('neighborAppliedAccount').textContent, '000GRAPH-00001');
  assert.equal(h.el('neighborAppliedNodes').textContent, '3'); assert.equal(h.el('neighborAppliedEdges').textContent, '2');
  assert.equal(h.el('neighborDraftState').textContent, '已应用'); assert.equal(h.el('btnNeighborhood').disabled, true);
  assert.equal(h.el('btnExpandNeighbors').disabled, true); assert.match(h.el('neighborExpandHint').textContent, /已无/);
  assert.ok(h.places.length);
});

test('typing an exact account previews a draft without locating, applying, or changing current scope', async () => {
  const h = harness(); await h.open(); h.input('000GRAPH-00003'); h.direction('out'); await flush();
  assert.equal(h.locates.length, 0); assert.equal(h.commits.length, 0);
  assert.equal(h.controller.getState().draft.direction, 'out'); assert.equal(h.el('neighborDraftState').textContent, '待应用');
  assert.equal(h.el('neighborAppliedDirection').textContent, '双向邻居'); assert.equal(h.el('neighborAppliedAccount').textContent, '000GRAPH-00001');
  assert.equal(h.el('neighborResolvedName').textContent, '合成节点 3'); assert.equal(h.el('neighborPreviewNodes').textContent, '2');
  assert.equal(h.el('btnNeighborhood').disabled, false); assert.equal(h.el('neighborDirectionOut').attributes['aria-pressed'], 'true');
});

test('explicit search gives exact account priority, preserves leading zeros, and only locates', async () => {
  const h = harness(); await h.open(); h.input('00123');
  assert.equal(await h.controller.search(), true); await flush();
  assert.deepEqual(h.locates, [{ account: '00123', binding: h.state.binding }]); assert.equal(h.el('neighborAccount').value, '00123');
  assert.equal(h.el('neighborResolvedName').textContent, '同名企业'); assert.equal(h.commits.length, 0);
  assert.equal(h.state.applied.root, '000GRAPH-00001');
});

test('unique fuzzy card/account or name resolves canonical account after explicit search', async () => {
  const h = harness(); await h.open(); h.input('唯一户名');
  assert.equal(h.el('btnNeighborhood').disabled, true); assert.equal(await h.controller.search(), true); await flush();
  assert.equal(h.el('neighborAccount').value, '00999'); assert.equal(h.locates[0].account, '00999');
  h.input('456'); await h.controller.search(); assert.equal(h.locates[1].account, '00456');
});

test('ambiguous names never select or move to an arbitrary first account', async () => {
  const h = harness(); await h.open(); h.input('同名企业');
  assert.equal(await h.controller.search(), false); await flush();
  assert.equal(h.locates.length, 0); assert.equal(h.controller.getState().selected, null); assert.equal(h.el('btnNeighborhood').disabled, true);
  assert.equal(h.el('neighborSearchResults').children.length, 2); assert.match(h.el('neighborSearchCount').textContent, /2 个账户/);
  h.el('neighborSearchResults').children[1].emit('click'); await flush();
  assert.equal(h.locates[0].account, '00456'); assert.equal(h.el('neighborAccount').value, '00456');
});

test('matching list is capped at 20 while total is disclosed and account strings stay intact', async () => {
  const h = harness(); h.state.accounts = Array.from({ length: 55 }, (_, index) => ({ account: `000${index}`, name: '合成共同户名' }));
  await h.open(); h.input('共同户名'); await h.controller.search(); await flush();
  assert.equal(h.controller.getState().resultTotal, 55); assert.equal(h.el('neighborSearchResults').children.length, 20);
  assert.match(h.el('neighborSearchCount').textContent, /55.*前 20/); assert.equal(h.locates.length, 0);
});

test('no results and empty search give an actionable message without applying or clearing filters', async () => {
  const h = harness(); await h.open(); h.input('不存在'); await h.controller.search(); await flush();
  assert.match(h.el('neighborStatus').textContent, /已追踪账户中没有/); assert.equal(h.el('btnNeighborhood').disabled, true);
  h.input(''); await h.controller.search(); assert.match(h.el('neighborStatus').textContent, /请输入/);
  assert.equal(h.commits.length, 0); assert.equal(h.state.applied.root, '000GRAPH-00001');
});

test('an invisible matched account reports locate reason but retains explicit apply without clearing scope', async () => {
  const h = harness(); h.onLocate(() => ({ ok: false, reason: '该账户被当前账户显示条件隐藏' })); await h.open();
  h.input('00999'); assert.equal(await h.controller.search(), false); await flush();
  assert.match(h.el('neighborStatus').textContent, /账户显示条件隐藏/); assert.equal(h.el('btnNeighborhood').disabled, false);
  assert.equal(h.state.applied.root, '000GRAPH-00001'); assert.equal(h.commits.length, 0);
});

test('applying a canonical draft commits once and then reflects authoritative applied data', async () => {
  const h = harness(); await h.open(); h.input('000GRAPH-00003'); h.direction('out'); await flush();
  assert.equal(await h.controller.apply(), true); await flush();
  assert.deepEqual(h.commits, [{ binding: h.state.binding, draft: { account: '000GRAPH-00003', direction: 'out' } }]);
  assert.equal(h.el('neighborAppliedAccount').textContent, '000GRAPH-00003'); assert.equal(h.el('neighborAppliedDirection').textContent, '转出去向');
  assert.equal(h.el('neighborDraftState').textContent, '已应用'); assert.equal(await h.controller.apply(), false);
});

test('continue expansion uses applied center and direction while retaining distinct dirty draft', async () => {
  const h = harness(); h.state.applied.remaining = 70; h.state.applied.totalNeighbors = 72; await h.open();
  h.input('000GRAPH-00003'); h.direction('out'); await flush();
  assert.equal(await h.controller.expand(), true); await flush();
  assert.deepEqual(h.expands, [{ binding: h.state.binding, root: '000GRAPH-00001', direction: 'both' }]);
  assert.equal(h.controller.getState().draft.account, '000GRAPH-00003'); assert.equal(h.controller.getState().draft.direction, 'out');
  assert.equal(h.state.applied.expanded, 52); assert.equal(h.state.applied.remaining, 20); assert.equal(h.el('neighborAppliedNodes').textContent, '53');
});

test('closing discards draft, and reopening never reuses unresolved search candidates', async () => {
  const h = harness(); await h.open(); h.input('同名企业'); await h.controller.search(); h.root.FundsToolbarMenu.close('escape');
  assert.equal(h.controller.getState().open, false); await h.open();
  assert.equal(h.el('neighborAccount').value, '000GRAPH-00001'); assert.equal(h.controller.getState().results.length, 0); assert.equal(h.controller.getState().dirty, false);
});

test('Enter in account searches only, IME and repeated Enter do not dispatch, button Enter stays native', async () => {
  const h = harness(); await h.open(); h.input('00999');
  h.el('neighborPopover').emit('keydown', { target: h.el('neighborAccount'), key: 'Enter', isComposing: true });
  h.el('neighborPopover').emit('keydown', { target: h.el('neighborAccount'), key: 'Enter', keyCode: 229 });
  h.el('neighborPopover').emit('keydown', { target: h.el('neighborAccount'), key: 'Enter', repeat: true }); await flush();
  assert.equal(h.locates.length, 0); const event = h.el('neighborPopover').emit('keydown', { target: h.el('neighborAccount'), key: 'Enter' }); await flush();
  assert.equal(event.defaultPrevented, true); assert.equal(h.locates.length, 1); assert.equal(h.commits.length, 0);
  const native = h.el('neighborPopover').emit('keydown', { target: h.el('neighborDirectionOut'), key: 'Enter' }); assert.equal(native.defaultPrevented, false);
});

test('busy and not-ready contexts block both camera preview and mutations', async () => {
  const h = harness(); await h.open(); h.input('00999'); await flush(); h.setBlocked('账户资料尚未保存');
  assert.equal(await h.controller.search(), false); assert.equal(await h.controller.apply(), false); assert.equal(await h.controller.expand(), false);
  assert.match(h.el('neighborStatus').textContent, /尚未保存/); assert.equal(h.locates.length, 0); assert.equal(h.commits.length, 0);
  h.setBlocked(''); h.state.ready = false; h.controller.update(); assert.equal(h.el('neighborAccount').disabled, true);
});

test('deferred application cannot reenter even when the surface closes and reopens', async () => {
  const h = harness(), job = deferred(); h.onCommit((draft, binding, commit) => job.promise.then(() => commit(draft, binding)));
  await h.open(); h.input('00999'); await flush(); const first = h.controller.apply();
  assert.equal(await h.controller.apply(), false); h.controller.close(); await h.open();
  assert.equal(h.controller.getState().pending, true); assert.equal(await h.controller.apply(), false); assert.equal(h.commits.length, 1);
  job.resolve(); assert.equal(await first, true); await flush(); assert.equal(h.controller.getState().pending, false);
  assert.equal(h.el('neighborAppliedAccount').textContent, '00999');
});

test('binding change revokes late commit UI and a new context remains available', async () => {
  const h = harness(), job = deferred(); h.onCommit((draft, binding, commit) => job.promise.then(() => commit(draft, binding)));
  await h.open(); h.input('00999'); await flush(); const first = h.controller.apply();
  h.switchBinding('case:4/2/canvas:9/1'); h.state.applied.root = '00456'; h.controller.update(); await h.open();
  job.resolve(); assert.equal(await first, false); await flush();
  assert.equal(h.el('neighborAppliedAccount').textContent, '00456'); assert.equal(h.controller.getState().pending, false);
});

test('late search locate cannot leak failure into a different case or reopened session', async () => {
  const h = harness(), job = deferred(); h.onLocate(() => job.promise); await h.open(); h.input('00999');
  const search = h.controller.search(); h.controller.close(); await h.open(); job.resolve({ ok: false, reason: '旧画布错误' });
  assert.equal(await search, false); await flush(); assert.equal(h.controller.getState().locateMessage, ''); assert.equal(h.controller.getState().searchPending, false);
});

test('revision change invalidates a slow preview and preserves current dirty draft', async () => {
  const h = harness(), job = deferred(); await h.open(); h.onPreview(() => job.promise); h.input('00999');
  h.onPreview(() => ({ nodes: 7, edges: 6, rootFound: true })); h.state.revision++; h.controller.update(); await flush();
  job.resolve({ nodes: 9999, edges: 9998, rootFound: true }); await flush();
  assert.equal(h.el('neighborPreviewNodes').textContent, '7'); assert.equal(h.el('neighborAccount').value, '00999'); assert.equal(h.controller.getState().dirty, true);
});

test('a changed source revokes stale locate feedback even within the same binding', async () => {
  const h = harness(), job = deferred(); h.onLocate(() => job.promise); await h.open(); h.input('00999'); const search = h.controller.search();
  h.state.source = {}; h.state.revision++; h.controller.update(); await flush(); job.resolve({ ok: false, reason: '过时提示' }); await search; await flush();
  assert.equal(h.controller.getState().locateMessage, ''); assert.equal(h.controller.getState().searchPending, false);
});

test('removed account or preview root absence prevents apply and never silently clears display conditions', async () => {
  const h = harness(); await h.open(); h.input('00999'); await flush(); h.state.accounts = h.state.accounts.filter(item => item.account !== '00999');
  h.state.revision++; h.controller.update(); await flush(); assert.equal(h.controller.getState().selected, null); assert.equal(await h.controller.apply(), false);
  h.input('00123'); h.onPreview(() => ({ nodes: 0, edges: 0, rootFound: false })); h.direction('out'); await flush();
  assert.match(h.el('neighborStatus').textContent, /已不在/); assert.equal(h.el('btnNeighborhood').disabled, true); assert.equal(h.commits.length, 0);
});

test('full large counts and long accounts remain unrounded and text-only', async () => {
  const h = harness(), account = '000' + '9'.repeat(70) + '<svg onload=bad>';
  h.state.accounts.push({ account, name: '<script>合成户名</script>' });
  h.state.applied = { ...h.state.applied, root: account, name: '<script>合成户名</script>', nodes: '123456789012345678901234567890', edges: '999999999999999999999', remaining: '100000000000000000000' };
  h.onPreview(() => ({ nodes: '123456789012345678901234567890', edges: '999999999999999999999', rootFound: true })); await h.open();
  assert.equal(h.el('neighborAppliedAccount').textContent, account); assert.equal(h.el('neighborAppliedName').textContent, '<script>合成户名</script>');
  assert.equal(h.el('neighborAppliedNodes').textContent, '123,456,789,012,345,678,901,234,567,890');
  assert.equal(h.el('neighborPreviewNodes').attributes['data-size'], 'extended'); assert.equal(h.el('btnExpandNeighbors').disabled, false);
  assert.equal(h.el('neighborAppliedName').children.length, 0);
});

test('no applied neighborhood keeps expansion disabled and does not fabricate applied direction', async () => {
  const h = harness(); h.state.applied = { root: '', nodes: 5, edges: 4, remaining: 99 }; await h.open();
  assert.equal(h.el('neighborAppliedAccount').textContent, '尚未查看邻域'); assert.equal(h.el('neighborAppliedDirection').textContent, '—');
  assert.equal(h.el('btnExpandNeighbors').disabled, true); assert.equal(await h.controller.expand(), false);
});
