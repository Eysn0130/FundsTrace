import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/main-accounts.js', import.meta.url), 'utf8');
const flush = async () => { for (let pass = 0; pass < 20; pass++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const plain = value => JSON.parse(JSON.stringify(value));
const initialRows = () => [
  { account: '00001', name: '同户名', source: 'manual' },
  { account: '00002', name: '同户名', source: 'auto' },
  { account: '00003', name: '第三方', source: 'auto' },
];
const newAccount = { account: '00004', name: '画布外账号', is_main: false, source: null };
function harness() {
  const binding = { caseId: 11, generation: 1, canvasId: 5, canvasGeneration: 1 };
  const requests = [], states = [], publications = [], shows = [], dismisses = [], preflights = [];
  let records = initialRows(), candidates = [newAccount], ready = true, canWrite = async () => true, publish = async () => {};
  let route = null;
  const root = {};
  vm.runInNewContext(source, { window: root });
  const controller = root.FundsMainAccounts.create({
    context: () => ({ ...binding }),
    isCurrent(context, canvas) {
      assert.equal(canvas, false);
      return context.caseId === binding.caseId && context.generation === binding.generation;
    },
    ready: () => ready,
    async canWrite(context, action) { preflights.push({ context: { ...context }, action }); return canWrite(context, action); },
    async request(path, options = {}) {
      requests.push({ path, options });
      if (route) { const result = route(path, options); if (result !== undefined) return result; }
      if (path.includes('/candidates?')) {
        const query = new URL(path, 'http://synthetic.invalid').searchParams;
        return { items: candidates.map(row => ({ ...row })), total: candidates.length, page: Number(query.get('page')), page_size: 30 };
      }
      if (!options.method) return records.map(row => ({ ...row }));
      const body = JSON.parse(options.body);
      if (path.endsWith('/clear-auto')) {
        const cleared = records.filter(row => row.source === 'auto').length;
        records = records.filter(row => row.source === 'manual'); return { cleared };
      }
      if (path.endsWith('/auto')) return { detected: [{ account: '00002', name: '同户名', source: 'auto', added: false }],
        added_count: 0, manual_preserved: 0, existing_auto_count: 1, batches_scanned: 1 };
      if (options.method === 'DELETE') { records = records.filter(row => row.account !== body.account); return { ok: true }; }
      records = [...records.filter(row => row.account !== body.account), { account: body.account, name: body.name, source: 'manual' }];
      return { ok: true, account: body.account, name: body.name, source: 'manual' };
    },
    async publish(context, rows, result) { publications.push({ context: { ...context }, rows: plain(rows), result: plain(result) }); return publish(context, rows, result); },
    show(state, trigger) { shows.push({ state, trigger }); }, dismiss(reason) { dismisses.push(reason); },
    onState(value) { states.push(value); },
  });
  return { controller, binding, requests, states, publications, shows, dismisses, preflights,
    writes: () => requests.filter(row => row.options.method),
    setReady(value) { ready = value; }, onWrite(value) { canWrite = value; }, onPublish(value) { publish = value; },
    onRequest(value) { route = value; }, setRows(value) { records = value; }, setCandidates(value) { candidates = value; },
    switchCase(id = 12) { binding.caseId = id; binding.generation++; controller.revoke(); },
    async opened() { assert.equal(controller.open('main-trigger'), true); await flush(); },
    async searching() { await this.opened(); assert.equal(controller.showSearch(), true); await flush(); },
  };
}

test('opening reads all marked accounts, preserves sources, and never writes', async () => {
  const h = harness(); await h.opened(); const state = h.controller.status();
  assert.equal(h.shows[0].trigger, 'main-trigger'); assert.equal(state.visible, true);
  assert.deepEqual(plain(state.counts), { all: 3, auto: 2, manual: 1 });
  assert.equal(state.rows[0].account, '00001'); assert.equal(h.writes().length, 0); assert.equal(state.loading, false);
});

test('source filters classify full case rows without additional IO or writes', async () => {
  const h = harness(); await h.opened();
  assert.equal(h.controller.setFilter('auto'), true); assert.equal(h.controller.status().filter, 'auto');
  assert.equal(h.controller.status().rows.length, 3); assert.equal(h.controller.setFilter('bogus'), false);
  assert.equal(h.requests.length, 1); assert.equal(h.writes().length, 0);
});

test('search is a distinct view and uses account-level encoded query and pagination', async () => {
  const h = harness(); await h.searching(); assert.equal(h.controller.status().screen, 'search');
  await h.controller.search('  张 %_&  ', 2);
  const url = new URL(h.requests.at(-1).path, 'http://synthetic.invalid');
  assert.equal(url.pathname, '/api/main-accounts/candidates'); assert.equal(url.searchParams.get('case_id'), '11');
  assert.equal(url.searchParams.get('q'), '张 %_&'); assert.equal(url.searchParams.get('page'), '2');
  assert.equal(url.searchParams.get('page_size'), '30'); assert.equal(url.searchParams.has('exact'), false); assert.equal(h.writes().length, 0);
});

test('exact account lookup is explicit, case preserving and remains read-only', async () => {
  const h = harness(); await h.searching(); assert.equal(await h.controller.search('  000AbC&12  ', 1, true), true);
  const url = new URL(h.requests.at(-1).path, 'http://synthetic.invalid');
  assert.equal(url.searchParams.get('exact'), 'true'); assert.equal(url.searchParams.get('q'), '000AbC&12');
  assert.equal(url.searchParams.get('page'), '1'); assert.equal(h.writes().length, 0);
  const before = h.requests.length;
  assert.equal(await h.controller.search('000AbC&12', 1, 'true'), false);
  assert.equal(await h.controller.search('000AbC&12', 1, null), false); assert.equal(h.requests.length, before);
});

test('a late contains response cannot replace a newer exact lookup or add automatically', async () => {
  const h = harness(); await h.searching(); const contains = deferred(), exact = deferred();
  h.onRequest(path => path.includes('/candidates?') ? path.includes('&exact=true') ? exact.promise : contains.promise : undefined);
  const old = h.controller.search('00004'), latest = h.controller.search('00004', 1, true);
  exact.resolve({ items: [newAccount], total: 1, page: 1, page_size: 30 }); await latest;
  contains.resolve({ items: [{ ...newAccount, account: '000040' }], total: 1, page: 1, page_size: 30 }); await old;
  assert.equal(h.controller.status().candidates[0].account, '00004'); assert.equal(h.writes().length, 0);
  assert.equal(h.controller.prepare('manual', '000040'), false); assert.equal(h.writes().length, 0);
});

test('explicit add sends exact leading-zero account and refreshes authoritative rows', async () => {
  const h = harness(); await h.searching(); assert.equal(await h.controller.prepare('manual', newAccount), true);
  assert.equal(h.writes().length, 1); assert.equal(h.writes()[0].path, '/api/main-accounts');
  assert.deepEqual(JSON.parse(h.writes()[0].options.body), { case_id: 11, account: '00004', name: '画布外账号' });
  assert.equal(h.publications.length, 1); assert.equal(h.publications[0].rows.length, 4);
  assert.equal(h.controller.status().screen, 'list'); assert.equal(h.controller.status().confirmation, null);
  assert.equal(await h.controller.submit(), false); assert.equal(h.writes().length, 1);
});

test('auto rows can be converted to manual while existing manual is a no-op', async () => {
  const h = harness(); await h.opened();
  assert.equal(h.controller.prepare('manual', '00001'), false);
  assert.equal(await h.controller.prepare('manual', '00002'), true);
  assert.equal(h.controller.status().rows.find(row => row.account === '00002').source, 'manual');
  assert.equal(h.controller.status().counts.manual, 2); assert.equal(h.writes().length, 1);
});

test('unlisted arbitrary accounts and unknown action names cannot be submitted', async () => {
  const h = harness(); await h.opened();
  assert.equal(h.controller.prepare('manual', { account: '99999', name: '伪造' }), false);
  assert.equal(h.controller.prepare('remove', '99999'), false); assert.equal(h.controller.prepare('delete-all'), false);
  assert.equal(await h.controller.submit(), false); assert.equal(h.preflights.length, 0); assert.equal(h.writes().length, 0);
});

test('remove requires explicit confirmation and only sends its selected account', async () => {
  const h = harness(); await h.opened(); assert.equal(h.controller.prepare('remove', '00002'), true);
  assert.equal(h.controller.status().screen, 'confirm'); assert.equal(h.writes().length, 0);
  assert.equal(await h.controller.submit(), true);
  assert.equal(h.writes()[0].options.method, 'DELETE');
  assert.deepEqual(JSON.parse(h.writes()[0].options.body), { case_id: 11, account: '00002', name: '同户名' });
  assert.equal(h.controller.status().rows.find(row => row.account === '00001').source, 'manual');
});

test('cancelling a destructive confirmation performs no preflight or write', async () => {
  const h = harness(); await h.opened(); h.controller.prepare('remove', '00002');
  assert.equal(h.controller.cancelConfirm(), true); assert.equal(h.controller.status().screen, 'list');
  assert.equal(await h.controller.submit(), false); assert.equal(h.preflights.length, 0); assert.equal(h.writes().length, 0);
});

test('clear-auto preserves manual entries and derives result from the server receipt', async () => {
  const h = harness(); await h.opened(); h.controller.prepare('clear-auto');
  assert.equal(h.writes().length, 0); assert.equal(await h.controller.submit(), true);
  assert.equal(h.writes()[0].path, '/api/main-accounts/clear-auto');
  assert.equal(h.controller.status().rows.length, 1); assert.equal(h.controller.status().rows[0].account, '00001');
  assert.equal(h.controller.prepare('clear-auto'), false); assert.match(h.controller.status().message, /2/);
});

test('recognition displays newly added and already present counts separately', async () => {
  const h = harness(); await h.opened(); h.controller.prepare('auto'); assert.equal(h.writes().length, 0);
  assert.equal(await h.controller.submit(), true); assert.equal(h.writes()[0].path, '/api/main-accounts/auto');
  assert.match(h.controller.status().message, /新增 0/); assert.match(h.controller.status().message, /已有自动 1/);
});

test('search order is determined by the newest query rather than response timing', async () => {
  const h = harness(); await h.searching(); const first = deferred(), second = deferred();
  h.onRequest(path => path.includes('q=first') ? first.promise : path.includes('q=second') ? second.promise : undefined);
  const old = h.controller.search('first'), latest = h.controller.search('second');
  second.resolve({ items: [{ ...newAccount, account: 'new' }], total: 1, page: 1, page_size: 30 }); await latest;
  first.resolve({ items: [{ ...newAccount, account: 'old' }], total: 1, page: 1, page_size: 30 }); await old;
  assert.equal(h.controller.status().query, 'second'); assert.equal(h.controller.status().candidates[0].account, 'new');
});

test('case A-B-A invalidates delayed searches and leaves no old error or candidates', async () => {
  const h = harness(); await h.searching(); const response = deferred();
  h.onRequest(path => path.includes('/candidates?') ? response.promise : undefined);
  const old = h.controller.search('old'); h.switchCase(); h.switchCase(11); h.controller.open(); await flush();
  response.reject(new Error('旧案件检索错误')); await old;
  assert.equal(h.controller.status().candidates.length, 0); assert.equal(h.controller.status().error, '');
});

test('closing the search view invalidates pending results', async () => {
  const h = harness(); await h.searching(); const response = deferred();
  h.onRequest(path => path.includes('/candidates?') ? response.promise : undefined);
  const old = h.controller.search('pending'); assert.equal(h.controller.back(), true);
  response.resolve({ items: [newAccount], total: 1, page: 1, page_size: 30 }); await old;
  assert.equal(h.controller.status().screen, 'list'); assert.equal(h.controller.status().searching, false);
});

test('candidates are cleared during a new query so a prior result cannot be added', async () => {
  const h = harness(); await h.searching(); const response = deferred();
  h.onRequest(path => path.includes('/candidates?') ? response.promise : undefined);
  const searching = h.controller.search('new'); assert.equal(h.controller.prepare('manual', newAccount), false);
  response.resolve({ items: [], total: 0, page: 1, page_size: 30 }); await searching;
  assert.equal(h.controller.prepare('manual', newAccount), false); assert.equal(h.writes().length, 0);
});

test('the mutex is held before awaiting preflight and rejects double-clicked manual adds', async () => {
  const h = harness(); await h.searching(); const gate = deferred(); h.onWrite(() => gate.promise);
  const adding = h.controller.prepare('manual', newAccount); assert.equal(h.controller.status().phase, 'preflight');
  assert.equal(h.controller.blocked({ caseId: 11 }), true); assert.equal(h.controller.prepare('manual', newAccount), false);
  assert.equal(await h.controller.submit(), false); gate.resolve(true); await adding; assert.equal(h.writes().length, 1);
});

test('preflight refusal preserves confirmation without sending a write', async () => {
  const h = harness(); await h.opened(); h.onWrite(async () => false); h.controller.prepare('remove', '00002');
  assert.equal(await h.controller.submit(), false); assert.equal(h.writes().length, 0);
  assert.equal(h.controller.status().screen, 'confirm'); assert.equal(h.controller.blocked(), false);
});

test('preflight errors are known failures and may be repaired', async () => {
  const h = harness(); await h.opened(); h.onWrite(async () => { throw new Error('请先处理账户草稿'); });
  h.controller.prepare('auto'); assert.equal(await h.controller.submit(), false);
  assert.equal(h.controller.status().unknown, false); assert.equal(h.controller.status().error, '请先处理账户草稿');
  assert.equal(h.writes().length, 0);
});

test('closing during preflight cancels its unsent authorization', async () => {
  const h = harness(); await h.opened(); const gate = deferred(); h.onWrite(() => gate.promise);
  h.controller.prepare('auto'); const request = h.controller.submit(); assert.equal(h.controller.close(), true);
  gate.resolve(true); assert.equal(await request, false); assert.equal(h.writes().length, 0);
});

test('a new session cannot inherit a previous session confirmation or preflight', async () => {
  const h = harness(); await h.opened(); const gate = deferred(); h.onWrite(() => gate.promise);
  h.controller.prepare('auto'); const request = h.controller.submit(); h.controller.close(); h.controller.open();
  gate.resolve(true); assert.equal(await request, false); assert.equal(h.writes().length, 0);
  assert.equal(h.controller.status().phase, 'idle'); assert.equal(h.controller.status().pending, false);
});

test('readiness is checked again after asynchronous preflight', async () => {
  const h = harness(); await h.opened(); const gate = deferred(); h.onWrite(() => gate.promise);
  h.controller.prepare('auto'); const request = h.controller.submit(); h.setReady(false); gate.resolve(true);
  assert.equal(await request, false); assert.equal(h.writes().length, 0); assert.equal(h.controller.status().phase, 'error');
});

test('sent mutation locks closing and all competing account commands until its receipt', async () => {
  const h = harness(); await h.opened(); const response = deferred();
  h.onRequest((path, options) => options.method ? response.promise : undefined);
  h.controller.prepare('remove', '00002'); const request = h.controller.submit(); await flush();
  assert.equal(h.controller.close('escape'), false); assert.equal(h.controller.back(), false);
  assert.equal(h.controller.prepare('auto'), false); assert.equal(h.controller.showSearch(), false);
  response.resolve({ ok: true }); assert.equal(await request, true); assert.equal(h.controller.close(), true);
});

test('case revocation preserves the sent case lease and withholds stale receipt publication', async () => {
  const h = harness(); await h.opened(); const response = deferred();
  h.onRequest((path, options) => options.method ? response.promise : undefined);
  h.controller.prepare('remove', '00002'); const request = h.controller.submit(); await flush(); h.switchCase();
  assert.equal(h.controller.status().visible, false); assert.equal(h.controller.blocked({ caseId: 11 }), true);
  assert.equal(h.controller.blocked({ caseId: 12 }), false); assert.equal(h.controller.open(), false);
  response.resolve({ ok: true }); await request; assert.equal(h.publications.length, 0); assert.equal(h.controller.blocked(), false);
});

test('A-B-A after sending never publishes the earlier binding receipt', async () => {
  const h = harness(); await h.opened(); const response = deferred();
  h.onRequest((path, options) => options.method ? response.promise : undefined);
  h.controller.prepare('remove', '00002'); const request = h.controller.submit(); await flush(); h.switchCase(); h.switchCase(11);
  response.resolve({ ok: true }); await request;
  assert.equal(h.controller.status().visible, false); assert.equal(h.publications.length, 0); assert.equal(h.shows.length, 1);
});

test('reopening a pending case in a new binding requires fresh read recovery', async () => {
  const h = harness(); await h.opened(); const response = deferred();
  h.onRequest((path, options) => options.method ? response.promise : undefined);
  h.controller.prepare('remove', '00002'); const request = h.controller.submit(); await flush(); h.switchCase(); h.switchCase(11); h.controller.open();
  response.resolve({ ok: true }); await request;
  assert.equal(h.publications.length, 0); assert.equal(h.controller.status().unknown, true);
  assert.equal(h.controller.status().committed, true); assert.equal(await h.controller.submit(), false);
  await h.controller.reconcile(); assert.equal(h.publications[0].context.generation, 3);
  assert.equal(h.controller.acknowledge(), true); assert.equal(h.controller.blocked(), false);
});

test('a known server rejection permits repair without creating an unknown lease', async () => {
  const h = harness(); await h.opened();
  h.onRequest((path, options) => options.method ? Promise.reject(Object.assign(new Error('账号已不属于当前案件'), { status: 404 })) : undefined);
  h.controller.prepare('manual', '00002'); await flush();
  assert.equal(h.controller.status().unknown, false); assert.equal(h.controller.blocked(), false);
  assert.equal(h.controller.status().error, '账号已不属于当前案件');
});

test('transport ambiguity survives dismissal and reopens as read-only recovery without write replay', async () => {
  const h = harness(); await h.opened();
  h.onRequest((path, options) => options.method ? Promise.reject(new Error('network')) : undefined);
  h.controller.prepare('auto'); assert.equal(await h.controller.submit(), false);
  assert.equal(h.controller.status().unknown, true); assert.equal(h.controller.acknowledge(), false);
  assert.equal(h.controller.prepare('auto'), false); assert.equal(h.controller.close(), true); assert.equal(h.controller.blocked(), true);
  h.controller.open(); assert.equal(h.writes().length, 1); assert.equal(await h.controller.submit(), false);
  assert.ok(await h.controller.reconcile()); assert.equal(h.writes().length, 1);
  assert.equal(h.controller.acknowledge(), true); assert.equal(h.controller.status().unknown, false);
});

test('HTTP 408 and server 5xx remain ambiguous because a mutation may have committed', async () => {
  for (const status of [408, 500]) {
    const h = harness(); await h.opened();
    h.onRequest((path, options) => options.method ? Promise.reject(Object.assign(new Error('timeout'), { status })) : undefined);
    h.controller.prepare('auto'); await h.controller.submit(); assert.equal(h.controller.status().unknown, true);
  }
});

test('a successful write with failed list refresh cannot be retried as a write', async () => {
  const h = harness(); await h.opened(); let wrote = false;
  h.onRequest((path, options) => { if (options.method) { wrote = true; return { ok: true }; } if (wrote) throw new Error('refresh failed'); });
  h.controller.prepare('remove', '00002'); assert.equal(await h.controller.submit(), true);
  assert.equal(h.controller.status().unknown, true); assert.equal(h.controller.status().committed, true);
  assert.match(h.controller.status().refreshError, /操作已完成/); assert.equal(await h.controller.submit(), false); assert.equal(h.writes().length, 1);
  assert.equal(await h.controller.reconcile(), null); assert.equal(h.controller.acknowledge(), false);
});

test('publication failure also keeps a read-only recovery lease rather than resending', async () => {
  const h = harness(); await h.opened(); h.onPublish(async () => { throw new Error('graph refresh'); });
  h.controller.prepare('clear-auto'); assert.equal(await h.controller.submit(), true);
  assert.equal(h.controller.status().unknown, true); assert.equal(h.controller.status().committed, true);
  h.onPublish(async () => {}); assert.ok(await h.controller.reconcile()); assert.equal(h.writes().length, 1);
  assert.equal(h.controller.acknowledge(), true); assert.equal(h.controller.status().rows.length, 1);
});

test('reconcile responses from a superseded case cannot acknowledge or publish', async () => {
  const h = harness(); await h.opened();
  h.onRequest((path, options) => options.method ? Promise.reject(new Error('network')) : undefined);
  h.controller.prepare('auto'); await h.controller.submit(); const response = deferred();
  h.onRequest((path, options) => !options.method ? response.promise : undefined);
  const reading = h.controller.reconcile(); h.switchCase(); h.switchCase(11); h.controller.open();
  response.resolve(initialRows()); assert.equal(await reading, null);
  assert.equal(h.controller.acknowledge(), false); assert.equal(h.publications.length, 0); assert.equal(h.controller.status().unknown, true);
});

test('malformed success receipts are ambiguous and never publish invented success', async () => {
  for (const result of [{}, { ok: false }, { ok: 'true' }]) {
    const h = harness(); await h.opened(); h.onRequest((path, options) => options.method ? result : undefined);
    h.controller.prepare('remove', '00002'); assert.equal(await h.controller.submit(), false);
    assert.equal(h.controller.status().unknown, true); assert.equal(h.publications.length, 0);
  }
});

test('recognition receipts require distinct accounts and counters that match actual sources', async () => {
  const variants = [
    { detected: [{ account: '00001', source: 'manual', added: false }], added_count: 1, manual_preserved: 0, existing_auto_count: 0, batches_scanned: 1 },
    { detected: [{ account: '00001', source: 'manual', added: true }], added_count: 1, manual_preserved: 0, existing_auto_count: 0, batches_scanned: 1 },
    { detected: [{ account: '00002', source: 'auto', added: false }, { account: '00002', source: 'auto', added: false }], added_count: 0, manual_preserved: 0, existing_auto_count: 2, batches_scanned: 1 },
  ];
  for (const result of variants) {
    const h = harness(); await h.opened(); h.onRequest((path, options) => options.method ? result : undefined);
    h.controller.prepare('auto'); assert.equal(await h.controller.submit(), false);
    assert.equal(h.controller.status().unknown, true); assert.equal(h.publications.length, 0);
  }
});

test('malformed account searches and duplicate marked accounts are not rendered as authoritative', async () => {
  const h = harness(); h.setRows([...initialRows(), initialRows()[0]]); await h.opened();
  assert.equal(h.controller.status().rows.length, 0); assert.ok(h.controller.status().error);
  h.setRows(initialRows()); await h.controller.refresh(); h.setCandidates([{ ...newAccount, source: 'auto' }]);
  h.controller.showSearch(); await flush(); assert.equal(h.controller.status().candidates.length, 0); assert.ok(h.controller.status().error);
});

test('an unsuccessful initial list read fails closed until an authoritative refresh succeeds', async () => {
  const h = harness(); h.onRequest(() => { throw new Error('list unavailable'); }); await h.opened();
  assert.equal(h.controller.status().listLoaded, false); assert.equal(h.controller.status().loading, false);
  assert.equal(h.controller.prepare('auto'), false); assert.equal(h.controller.showSearch(), false);
  assert.equal(h.writes().length, 0); h.onRequest(() => undefined); await h.controller.refresh();
  assert.equal(h.controller.status().listLoaded, true); assert.equal(h.controller.prepare('auto'), true);
});

test('a failed later refresh invalidates previously read rows for further mutations', async () => {
  const h = harness(); await h.opened(); h.onRequest(() => { throw new Error('list unavailable'); });
  await h.controller.refresh(); assert.equal(h.controller.status().rows.length, 3);
  assert.equal(h.controller.status().listLoaded, false); assert.equal(h.controller.prepare('remove', '00002'), false);
  assert.equal(h.controller.prepare('auto'), false); assert.equal(h.writes().length, 0);
});

test('a delayed graph publication cannot display completion after case A-B-A', async () => {
  const h = harness(); await h.opened(); const publication = deferred(); let graphUpdates = 0;
  h.onPublish(async context => { await publication.promise; if (context.generation === h.binding.generation) graphUpdates++; });
  h.controller.prepare('remove', '00002'); const request = h.controller.submit(); await flush();
  assert.equal(h.publications.length, 1); h.switchCase(); h.switchCase(11); publication.resolve();
  assert.equal(await request, true); assert.equal(graphUpdates, 0);
  assert.equal(h.controller.status().visible, false); assert.equal(h.controller.status().message, '');
  assert.equal(h.controller.blocked(), false); assert.equal(h.shows.length, 1);
});

test('two overlapping list reads only accept the newest read', async () => {
  const h = harness(); await h.opened(); const first = deferred(), second = deferred(); let count = 0;
  h.onRequest((path, options) => !options.method ? (++count === 1 ? first.promise : second.promise) : undefined);
  const old = h.controller.refresh(), latest = h.controller.refresh();
  second.resolve([initialRows()[0]]); await latest; first.resolve(initialRows()); await old;
  assert.equal(h.controller.status().rows.length, 1); assert.equal(h.controller.status().loading, false);
});

test('unavailable and missing case contexts cannot open or write', async () => {
  const h = harness(); h.setReady(false); assert.equal(h.controller.open(), false);
  h.setReady(true); h.binding.caseId = null; assert.equal(h.controller.open(), false);
  assert.equal(await h.controller.submit(), false); assert.equal(h.requests.length, 0);
});

test('canvas switches retain case scope without changing the captured account', async () => {
  const h = harness(); await h.opened(); h.controller.prepare('remove', '00002');
  h.binding.canvasId = 9; h.binding.canvasGeneration++;
  assert.equal(await h.controller.submit(), true); assert.equal(JSON.parse(h.writes()[0].options.body).case_id, 11);
  assert.equal(h.publications[0].context.canvasId, 5, 'host chooses current graph metadata refresh safely');
});
