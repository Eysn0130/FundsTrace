import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/workflow-ui.js', import.meta.url), 'utf8');
const flush = async () => { for (let i = 0; i < 12; i += 1) await Promise.resolve(); };
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const response = (value, status = 200) => ({ ok: status === 200, status,
  json: async () => value, text: async () => JSON.stringify(value) });
const preview = id => ({ import_id: id, filename: `${id}.csv` });
const done = () => response({ stage: 'done', result: { ok: 2, rows_dup: 1 } });

function harness() {
  const nodes = new Map(), events = new Map(), timers = [], uploads = [], requests = [], notices = [], rendered = [], contextRows = [];
  let fetcher = () => { throw new Error('Unexpected request'); }, tracker = async () => true, refreshes = 0, graphReads = 0, paints = 0;
  let document;
  function element(id) {
    id = id.replace(/^#/, '');
    if (nodes.has(id)) return nodes.get(id);
    const classes = new Set();
    const node = { id, value: '', files: [], style: { display: 'none' }, disabled: false, hidden: false,
      textContent: '', innerHTML: '', children: [], attributes: {}, checked: true, isConnected: true,
      classList: { add: value => classes.add(value), remove: value => classes.delete(value), contains: value => classes.has(value), toggle() {} },
      setAttribute(name, value) { this.attributes[name] = value; },
      append(...items) { this.children.push(...items); for (const child of items) if (child.id) nodes.set(child.id, child); },
      replaceChildren(...items) { this.children = []; this.append(...items); },
      querySelectorAll() { return []; },
      contains(other) { for (let value = other; value; value = value.parent) if (value === this) return true; return false; },
      focus() { if (!this.disabled && this.isConnected) document.activeElement = this; },
      after(child) { if (child.id) nodes.set(child.id, child); },
      addEventListener() {},
    };
    let markup = '';
    Object.defineProperty(node, 'innerHTML', { get: () => markup, set(value) {
      markup = value;
      // A real innerHTML replacement removes focused controls, implicitly
      // blurring to BODY. New controls must be separate node identities.
      if (id === 'drawerBody') for (const [key, child] of nodes) {
        if (child !== node && node.contains(child)) {
          child.isConnected = false;
          if (document?.activeElement === child) document.activeElement = document.body;
          nodes.delete(key);
        }
      }
    } });
    let disabled = false;
    Object.defineProperty(node, 'disabled', { get: () => disabled, set(value) {
      disabled = value;
      if (value && document?.activeElement === node) document.activeElement = document.body;
    } });
    nodes.set(id, node);
    if (id === 'drawerBody' || id === 'btnCloseDrawer') node.parent = element('drawer');
    else if (id.startsWith('nd')) node.parent = element('drawerBody');
    return node;
  }
  const state = { caseId: 1, canvasId: 0, canvasGeneration: 1, caseGeneration: 1, cases: [{ id: 1, name: '合成案件' }], imp: null,
    graphView: { revision: 0 }, lastGraph: { nodes: [] }, focusNodes: new Map(), ctx: null };
  const fetch = (path, options) => { requests.push({ path, options }); return fetcher(path, options); };
  document = { createElement: () => element(`created-${nodes.size}`), getElementById: id => nodes.get(id) || null,
    body: element('body'), documentElement: element('html') };
  document.activeElement = document.body;
  const context = vm.createContext({
    state, window: { addEventListener: (name, handler) => events.set(name, handler) },
    document,
    $: element, $$: selector => selector === '#impMainSel .mk-acct' ? [Object.assign(element('main-account'), { value: 'SYN-A' })]
      : selector === '#ctxTbody .ctx-ck' ? [Object.assign(element('context-check'), { dataset: { id: '17' } })]
        : selector === '#ctxTabs .stab' ? [] : [element('mapping-select'), element('impSelfAccts')],
    caseContext: () => ({ caseId: state.caseId, generation: state.caseGeneration, canvasId: state.canvasId, canvasGeneration: state.canvasGeneration }),
    isCurrentCase: (binding, canvas = true) => binding.caseId === state.caseId && binding.generation === state.caseGeneration
      && (!canvas || binding.canvasId === state.canvasId && binding.canvasGeneration === state.canvasGeneration),
    jsonOpts: body => ({ headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    collectMapping: () => ({ mode: 'A', mapping: { amount: '合成金额' } }),
    renderImport: result => { rendered.push(result); element('impStep1').style.display = 'none'; element('impStep2').style.display = 'block'; },
    hidePbar: () => { element('impPbar').style.display = 'none'; },
    setPbar: (_fraction, message) => { element('impPbar').style.display = 'block'; element('impPtext').textContent = message; },
    setPbarBusy: message => { element('impPbar').style.display = 'block'; element('impPtext').textContent = message; },
    fmtCount: value => String(value), toast: (message, type) => notices.push({ message, type }),
    fmtTime: String, esc: String, nodePhotoUrl: () => '', totalsHtml: () => '', moneyHtml: String,
    selectItem() {}, renderCtxRows: value => contextRows.push(value), renderSuggestions() {}, isSystemAccount: () => false,
    trackFlows: ids => tracker(ids),
    loadGraph: async () => { graphReads += 1; }, renderGraph: () => { paints += 1; },
    api: async (path, options) => { const result = await fetch(path, options); if (!result.ok) throw new Error('合成读取失败'); return result.json(); },
    refreshAll: async () => { refreshes += 1; },
    setTimeout: (callback, delay) => { timers.push({ callback, delay }); },
    fetch,
    FormData: class { append() {} },
    XMLHttpRequest: class {
      constructor() { this.upload = {}; uploads.push(this); }
      open(method, path) { this.method = method; this.path = path; }
      send() {}
      finish(result, status = 200) { this.status = status; this.responseText = JSON.stringify(result); this.onload(); }
    },
  });
  const app = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
  for (const [start, end] of [['function closeDrawer()', '/* ---------------- 照片预览裁剪'],
    ['async function showNodeDrawer(', '/* ---------------- 交易上下文'],
    ['async function loadContext()', '/* ---------------- 智能推荐']]) {
    vm.runInContext(app.slice(app.indexOf(start), app.indexOf(end, app.indexOf(start))), context);
  }
  vm.runInContext(source, context, { filename: 'workflow-ui.js' });
  events.get('DOMContentLoaded')();
  return { element, state, uploads, requests, notices, rendered, timers, contextRows, document,
    get refreshes() { return refreshes; },
    get graphReads() { return graphReads; }, get paints() { return paints; },
    network(handler) { fetcher = handler; },
    track(handler) { tracker = handler; },
    run(code) { return vm.runInContext(code, context); },
    open() { element('btnImport').onclick(); }, close() { element('impClose').onclick(); },
    upload(name) { element('impFile').files = [{ name }]; return element('impFile').onchange(); },
    commit() { return element('impCommit').onclick(); },
    switchCase(id) { state.caseId = id; state.caseGeneration += 1; state.imp = null; element('importModal').style.display = 'none'; },
  };
}

async function ready(h, id = 'SYN-IMPORT') {
  h.open(); const result = h.upload(`${id}.csv`); h.uploads.at(-1).finish(preview(id)); await result;
}

test('reopening clears supplementary accounts and a late file A cannot replace file B', async () => {
  const h = harness(); h.open(); h.element('impSelfAccts').value = 'SYN-OLD';
  const a = h.upload('A.csv'); h.close(); h.open();
  assert.equal(h.element('impSelfAccts').value, '');
  const b = h.upload('B.csv'); h.uploads[1].finish(preview('B')); await b;
  h.uploads[0].finish(preview('A')); await a;
  assert.equal(h.state.imp.import_id, 'B');
  assert.deepEqual(h.rendered.map(item => item.import_id), ['B']);
});

test('a second file selection in the same upload view follows the latest intent', async () => {
  const h = harness(); h.open(); const a = h.upload('A.csv'), b = h.upload('B.csv');
  h.uploads[1].finish(preview('B')); await b; h.uploads[0].finish(preview('A')); await a;
  assert.equal(h.state.imp.import_id, 'B');
  assert.equal(h.element('impCommit').disabled, false);
});

test('A-B-A case navigation invalidates an earlier upload response', async () => {
  const h = harness(); h.open(); const a = h.upload('A.csv');
  h.switchCase(2); h.switchCase(1); h.open();
  h.uploads[0].finish(preview('STALE')); await a;
  assert.equal(h.state.imp, null); assert.equal(h.rendered.length, 0);
  assert.equal(h.element('impStep2').style.display, 'none');
});

test('the commit button locks before its receipt and duplicate clicks send one write', async () => {
  const h = harness(); await ready(h); const receipt = deferred();
  h.network(path => path === '/api/import/commit' ? receipt.promise : done());
  const first = h.commit(); await h.commit();
  assert.equal(h.element('impCommit').disabled, true);
  assert.equal(h.requests.filter(item => item.options?.method === 'POST').length, 1);
  receipt.resolve(response({ import_id: 'SYN-IMPORT', started: true })); await first;
  assert.equal(h.refreshes, 1); assert.match(h.notices[0].message, /新增 2 笔/);
});

test('failed progress has a read-only recovery action without a second commit', async () => {
  const h = harness(); await ready(h); let reads = 0;
  h.network(path => path === '/api/import/commit' ? response({ import_id: 'SYN-IMPORT', started: true })
    : ++reads === 1 ? response({ detail: '合成暂时故障' }, 503) : done());
  await h.commit();
  assert.equal(h.element('impRetryProgress').hidden, false);
  assert.equal(h.element('impCommit').disabled, true);
  assert.match(h.element('impRecoveryText').textContent, /不会再次提交导入/);
  await h.element('impRetryProgress').onclick();
  assert.equal(h.requests.filter(item => item.options?.method === 'POST').length, 1);
  assert.equal(reads, 2); assert.equal(h.refreshes, 1);
});

test('a lost commit receipt recovers by reading the known import ID', async () => {
  const h = harness(); await ready(h);
  h.network(path => path === '/api/import/commit' ? Promise.reject(new TypeError('合成断线')) : done());
  await h.commit(); assert.equal(h.element('impRetryProgress').hidden, false);
  await h.element('impRetryProgress').onclick();
  assert.match(h.requests.at(-1).path, /import_id=SYN-IMPORT&case_id=1$/);
  assert.equal(h.requests.filter(item => item.options?.method === 'POST').length, 1);
  assert.equal(h.refreshes, 1);
});

test('closing preserves a submitted operation and its delayed close cannot dismiss a new import', async () => {
  const h = harness(); await ready(h); const receipt = deferred();
  h.network(path => path === '/api/import/commit' ? receipt.promise : done());
  const commit = h.commit(); h.close();
  receipt.resolve(response({ import_id: 'SYN-IMPORT', started: true })); await commit;
  assert.equal(h.element('importModal').style.display, 'none');
  assert.equal(h.refreshes, 1);
  h.open(); assert.equal(h.element('importModal').style.display, 'flex');
  h.timers.find(timer => timer.delay === 700).callback();
  assert.equal(h.element('importModal').style.display, 'flex');
});

test('reopening an active task restores progress and never permits a duplicate write', async () => {
  const h = harness(); await ready(h); const receipt = deferred();
  h.network(path => path === '/api/import/commit' ? receipt.promise : done());
  const commit = h.commit(); h.close(); h.open();
  assert.equal(h.element('importModal').style.display, 'flex');
  assert.equal(h.element('impCommit').disabled, true); await h.commit();
  receipt.resolve(response({ import_id: 'SYN-IMPORT', started: true })); await commit;
  assert.equal(h.requests.filter(item => item.options?.method === 'POST').length, 1);
});

test('a stale commit receipt after A-B-A never polls or publishes into the new case view', async () => {
  const h = harness(); await ready(h); const receipt = deferred(); h.network(() => receipt.promise);
  const commit = h.commit(); h.switchCase(2); h.switchCase(1); h.open();
  receipt.resolve(response({ import_id: 'SYN-IMPORT', started: true })); await commit;
  assert.equal(h.requests.length, 1); assert.equal(h.refreshes, 0); assert.equal(h.notices.length, 0);
  assert.equal(h.element('impCommit').disabled, true);
});

test('a late repreview cannot replace the next file session', async () => {
  const h = harness(); await ready(h); const result = deferred(); h.network(() => result.promise);
  const repreview = h.element('impRe').onclick(); h.close(); h.open();
  const next = h.upload('NEXT.csv'); h.uploads.at(-1).finish(preview('NEXT')); await next;
  result.resolve(response(preview('OLD'))); await repreview;
  assert.equal(h.state.imp.import_id, 'NEXT');
});

test('terminal import failure enables an explicit corrected retry without replaying writes', async () => {
  const h = harness(); await ready(h);
  h.network(path => path === '/api/import/commit' ? response({ import_id: 'SYN-IMPORT', started: true })
    : response({ stage: 'error', error: '合成文件无可导入记录' }));
  await h.commit();
  assert.equal(h.element('impCommit').disabled, false);
  assert.equal(h.element('impRetryProgress').hidden, true);
  assert.match(h.element('impRecoveryText').textContent, /手动确认重试/);
  assert.equal(h.requests.filter(item => item.options?.method === 'POST').length, 1);
});

test('context side/window changes publish only the latest response', async () => {
  const h = harness(), old = deferred(), latest = deferred();
  h.state.ctx = { flowId: 17, side: 'payee', x: 5, autoSwitch: false };
  h.element('ctxModal').style.display = 'flex';
  h.network(path => path.includes('side=payee') ? old.promise : latest.promise);
  const first = h.run('loadContext()');
  h.state.ctx.side = 'payer'; h.state.ctx.x = 9;
  const second = h.run('loadContext()');
  const result = account => ({ anchor: { id: 17 }, account, total: 10, pos: 4 });
  latest.resolve(response(result('SYN-LATEST'))); await second;
  old.resolve(response(result('SYN-OLD'))); await first;
  assert.deepEqual(h.contextRows.map(item => item.account), ['SYN-LATEST']);
  assert.match(h.element('ctxPos').textContent, /付款方：SYN-LATEST/);
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
  assert.match(h.element('drawerBody').innerHTML, /合成乙/);
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
  assert.match(h.element('drawerBody').innerHTML, /合成乙/);
  assert.equal(h.element('drawer').classList.contains('open'), false);
});

const drawerWrites = [
  ['ndFocusApply', '/api/focus-nodes/set'], ['ndIdSave', '/api/account-ids/set'],
  ['ndPhotoClr', '/api/account-photos/clear'], ['ndToggleMain', '/api/main-accounts'],
];

test('all four drawer writes keep their original case binding through delayed A-B-A navigation', async () => {
  for (const [id, endpoint] of drawerWrites) {
    const h = harness(), write = deferred();
    h.state.lastGraph.nodes = [{ account: 'SYN-A', id_no: 'SYN-UNCHANGED', is_main: 0 }];
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
    await h.run("showNodeDrawer('SYN-B', {})");
    const freshButton = h.element(id);
    assert.equal(freshButton.disabled, false, id);
    write.resolve(response({ on: true, scale: 2 })); await first;
    assert.match(h.element('drawerBody').innerHTML, /合成乙/, id);
    assert.equal(freshButton.disabled, false, id);
    assert.equal(h.graphReads, 0, id); assert.equal(h.paints, 0, id);
    assert.equal(h.notices.length, 0, id);
  }
});

test('drawer adapters preserve focus, identity, photo and main-account success behavior', async () => {
  for (const [id, endpoint] of drawerWrites) {
    const h = harness();
    h.state.lastGraph.nodes = [{ account: 'SYN-A', name: '图谱别名', id_no: null, is_main: 1 }];
    h.network((path, options) => options?.method === 'POST' || options?.method === 'DELETE' ? response({ on: true, scale: 2 })
      : path.includes('/profile?') ? response({ name: '导入合成户名', top_counterparties: [] }) : response([]));
    await h.run("showNodeDrawer('SYN-A', {})");
    h.element('ndFocusScale').value = '2'; h.element('ndIdInput').value = 'SYN-ID';
    await h.element(id).onclick();
    const write = h.requests.find(item => item.options?.method === 'POST' || item.options?.method === 'DELETE');
    const payload = JSON.parse(write.options.body);
    assert.equal(write.path, endpoint, id); assert.equal(payload.case_id, 1, id); assert.equal(payload.account, 'SYN-A', id);
    assert.equal(h.element(id).disabled, false, id);
    assert.match(h.element('drawerBody').innerHTML, /导入合成户名/, id);
    if (id === 'ndFocusApply') { assert.equal(payload.scale, 2); assert.equal(payload.on, true); assert.equal(h.graphReads, 1); }
    if (id === 'ndIdSave') { assert.equal(payload.id_no, 'SYN-ID'); assert.equal(h.state.lastGraph.nodes[0].id_no, 'SYN-ID'); assert.equal(h.paints, 1); }
    if (id === 'ndPhotoClr') { assert.equal(h.state.forceRebuild, true); assert.equal(h.graphReads, 1); }
    if (id === 'ndToggleMain') { assert.equal(write.options.method, 'DELETE'); assert.equal(payload.name, '导入合成户名'); assert.equal(h.graphReads, 1); }
    assert.equal(h.notices.length, 1, id);
  }
});

test('drawer rebuilds restore implicit blur without stealing deliberate focus or crossing requests', async () => {
  const h = harness();
  const profile = () => response({ name: '合成甲', top_counterparties: [] });
  h.network((path, options) => options?.method === 'POST' ? response({}) : path.includes('/profile?') ? profile() : response([]));
  await h.run("showNodeDrawer('SYN-A', {})");
  h.element('ndIdInput').value = 'SYN-ID'; h.element('ndIdSave').focus();
  await h.element('ndIdSave').onclick();
  assert.equal(h.document.activeElement, h.element('ndIdSave'), 'save restores the replacement button after disabled/removal blur');

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

test('context read failures expose an explicit retry in the existing table', async () => {
  const h = harness(); h.state.ctx = { flowId: 17, side: 'payee', x: 5, autoSwitch: false };
  h.element('ctxModal').style.display = 'flex'; h.network(() => response({}, 503));
  await h.run('loadContext()');
  const cell = h.element('ctxTbody').children[0].children[0];
  assert.match(cell.textContent, /加载失败/);
  assert.equal(cell.children[0].textContent, '重新加载明细');
  h.network(() => response({ anchor: { id: 17 }, account: 'SYN-RETRY', total: 1, pos: 1 }));
  await cell.children[0].onclick();
  assert.equal(h.contextRows.at(-1).account, 'SYN-RETRY');
});

test('context selection stays open on a rejected/no-op tracking command and closes only after success', async () => {
  const h = harness(); h.state.ctx = { flowId: 17 }; h.element('ctxModal').style.display = 'flex';
  h.track(async () => false); await h.element('ctxTrack').onclick();
  assert.equal(h.element('ctxModal').style.display, 'flex');
  assert.equal(h.element('context-check').checked, true);
  assert.match(h.element('ctxTrackStatus').textContent, /已保留勾选/);
  h.track(async () => true); await h.element('ctxTrack').onclick();
  assert.equal(h.element('ctxModal').style.display, 'none');
});

test('late tracking completion does not close a newly selected context or a different canvas', async () => {
  const h = harness(), tracking = deferred(); h.state.ctx = { flowId: 17 };
  h.element('ctxModal').style.display = 'flex'; h.track(() => tracking.promise);
  const first = h.element('ctxTrack').onclick();
  h.state.canvasId = 9; h.state.canvasGeneration += 1;
  h.state.ctx = { flowId: 18, side: 'payer', x: 5, autoSwitch: false };
  h.network(() => response({ anchor: { id: 18 }, account: 'SYN-NEW', total: 1, pos: 1 }));
  await h.run('loadContext()'); tracking.resolve(true); await first;
  assert.equal(h.element('ctxModal').style.display, 'flex');
  assert.equal(h.element('ctxTrack').disabled, false);
});

test('analysis subject labels preserve markup as plain text in every selected algorithm', async () => {
  const nodes = new Map();
  const element = selector => {
    if (!nodes.has(selector)) nodes.set(selector, { style: {}, classList: { toggle() {} }, value: '', innerHTML: '',
      getContext: () => ({ translate() {}, rotate() {}, fillText() {} }), toDataURL: () => 'data:image/png;base64,',
    });
    return nodes.get(selector);
  };
  const document = { querySelector: element, querySelectorAll: () => [], addEventListener() {},
    createElement: tag => element(`created-${tag}`), body: { appendChild() {} } };
  const context = vm.createContext({ document, window: { location: { search: '' } },
    fetch: async () => response([]), localStorage: { getItem: () => null }, URLSearchParams,
    FundsMoney: { currency: String, count: String }, setTimeout() {} });
  vm.runInContext(readFileSync(new URL('../static/analysis.js', import.meta.url), 'utf8'), context);
  await flush();
  vm.runInContext(`render({subjects:['<b>SYN-A</b> & "B"'], cycles:{cycles:[],count:0},
    fast_flows:{pairs:[],count:0,tolerance:.25},outliers:{outliers:[],count:0,sigma:2.5}},
    ['cycles','fast_flows','outliers'])`, context);
  const html = element('#results').innerHTML;
  assert.equal(html.includes('<b>SYN-A</b>'), false);
  assert.equal((html.match(/&lt;b&gt;SYN-A&lt;\/b&gt; &amp; &quot;B&quot;/g) || []).length, 3);
});

test('analysis run and exported report share the algorithms selected for that run', async () => {
  const nodes = new Map(), requests = [], algorithms = [{ value: 'cycles', checked: true }, { value: 'paths', checked: false }];
  const element = selector => {
    if (!nodes.has(selector)) nodes.set(selector, { style: {}, classList: { toggle() {} }, value: '', innerHTML: '', click() {},
      getContext: () => ({ translate() {}, rotate() {}, fillText() {} }), toDataURL: () => 'data:image/png;base64,',
    });
    return nodes.get(selector);
  };
  const results = { cycles: { cycles: [], count: 0 }, paths: { paths: [], count: 0, src: 'SYN-A', dst: 'SYN-B' } };
  const document = { querySelector: element, querySelectorAll: selector => selector === '.alg' ? algorithms : [], addEventListener() {},
    createElement: tag => element(`created-${tag}`), body: { appendChild() {} } };
  const context = vm.createContext({ document, window: { location: { search: '' } },
    fetch: async (path, options) => {
      requests.push({ path, options });
      if (path === '/api/cases') return response([]);
      if (path === '/api/analysis/report') return { ok: true, blob: async () => new Blob(['synthetic']) };
      return response(results);
    }, localStorage: { getItem: () => null }, URLSearchParams, URL, Blob,
    FundsMoney: { currency: String, count: String }, setTimeout() {} });
  vm.runInContext(readFileSync(new URL('../static/analysis.js', import.meta.url), 'utf8'), context); await flush();
  vm.runInContext('caseId = 1', context); await vm.runInContext('run()', context);
  algorithms[0].checked = false; algorithms[1].checked = true;
  await element('#btnReport').onclick();
  const sent = requests.filter(item => item.options?.body).map(item => JSON.parse(item.options.body));
  assert.equal(sent.length, 2);
  assert.deepEqual(sent.map(item => item.params.algorithms), [['cycles'], ['cycles']]);
  assert.equal(element('#results').innerHTML.includes('路径追踪'), false);
});

test('exposed analysis windows reject zero, fractions and out-of-range values without default substitution', () => {
  const values = new Map([['#subjects', ''], ['#ffTol', '0.25'], ['#cycDays', '7'], ['#ffWin', '30']]);
  const context = vm.createContext({ $: selector => ({ value: values.get(selector) }),
    $$: () => [{ value: 'cycles', checked: true }] });
  const analysis = readFileSync(new URL('../static/analysis.js', import.meta.url), 'utf8');
  vm.runInContext(analysis.slice(analysis.indexOf('function collectParams()'), analysis.indexOf('function setBar(')), context);
  for (const cycle of ['0', '-1', '1.5', '366', 'NaN']) {
    values.set('#cycDays', cycle);
    assert.throws(() => vm.runInContext('collectParams()', context), /环流时限须为 1 至 365 天的整数/);
  }
  values.set('#cycDays', '7');
  for (const window of ['0', '4', '-1', '5.5', '1441', 'NaN']) {
    values.set('#ffWin', window);
    assert.throws(() => vm.runInContext('collectParams()', context), /快进快出窗口须为 5 至 1440 分钟的整数/);
  }
  for (const [cycle, window] of [['1', '5'], ['365', '1440'], ['7', '30'], ['', '']]) {
    values.set('#cycDays', cycle); values.set('#ffWin', window);
    const result = vm.runInContext('collectParams().params', context);
    assert.equal(result.cycle_window_days, cycle === '' ? 7 : Number(cycle));
    assert.equal(result.ff_window_minutes, window === '' ? 30 : Number(window));
  }
});
