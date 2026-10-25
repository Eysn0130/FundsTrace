import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/feedback-workspace.js', import.meta.url), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const id = 'a'.repeat(32);
function harness() {
  const window = {}, calls = [], tasks = [], notifications = [], timers = [];
  vm.runInNewContext(source, { window, setTimeout, clearTimeout, AbortController });
  let context = { caseId: 1, generation: 1 }, leases = [], rows = [], job = { case_id: '1', import_id: id, stage: 'importing', done: 1, total: 3 };
  let release = async () => { leases = []; }, fetcher, changed = async () => {};
  const options = { context: () => context, isCurrent: value => String(value.caseId) === String(context.caseId) && value.generation === context.generation,
    feedback: () => ({ task: value => tasks.push(value), notify: value => notifications.push(value) }),
    fetch: async (path, options) => { calls.push({ path, options }); return fetcher ? fetcher(path, options) : { ok: true, json: async () => path.includes('?limit=') ? { case_id: String(context.caseId), items: rows } : job }; },
    leases: () => leases, release: async (...args) => { calls.push({ release: args }); return release(...args); },
    changed: async () => { calls.push({ changed: true }); await changed(); }, openImport: (...args) => calls.push({ open: args }),
    schedule: callback => { timers.push(callback); return timers.length; }, cancel() {}, now: () => 5000 };
  const observer = window.FundsFeedbackWorkspace.create(options);
  return { observer, calls, tasks, notifications, valid: window.FundsFeedbackWorkspace.validEvent,
    context(value) { context = value; }, rows(value) { rows = value; }, leases(value) { leases = value; }, job(value) { job = value; }, release(value) { release = value; }, fetch(value) { fetcher = value; }, changed(value) { changed = value; } };
}
const row = (stage, attempt = 1, extra = {}) => ({ case_id: '1', import_id: id, surface: 'v1', stage, attempt, ...extra });

test('feedback event schema rejects arbitrary actions, invalid progress and oversized strings', () => {
  const h = harness(), event = { id: 'task:1', kind: 'task', operation: 'analysis', status: 'running', title: '正在分析' };
  assert.equal(h.valid(event), true);
  for (const extra of [{ actions: [] }, { progress: { done: 4, total: 3 } }, { progress: { done: 0, total: 0 } }, { title: 'x'.repeat(161) }, { status: 'done' }, { importId: id }]) assert.equal(h.valid({ ...event, ...extra }), false);
});

test('closed UI tasks are observed with GETs and terminal receipt still uses owning lease validation', async () => {
  const h = harness(); h.rows([row('importing')]); h.leases([{ importId: id, phase: 'pending' }]);
  h.observer.sync(); await flush();
  assert.equal(h.tasks.at(-1).status, 'running'); assert.equal(h.tasks.at(-1).progress.total, 3);
  h.rows([row('done', 2)]); await h.observer.refresh();
  assert.equal(h.tasks.at(-1).status, 'success'); assert.ok(h.calls.some(value => value.release));
  assert.ok(h.calls.filter(value => value.path).every(value => !value.options.method));
  assert.equal(h.calls.filter(value => value.changed).length, 1);
});

test('ready row cannot turn an unresolved write into cancelled or failed', async () => {
  const h = harness(); h.rows([row('ready')]); h.leases([{ importId: id, phase: 'unknown' }]);
  h.observer.sync(); await flush();
  const task = h.tasks.at(-1); assert.equal(task.status, 'unknown');
  assert.deepEqual(Array.from(task.actions, value => value.label), ['查询状态', '查看导入记录']);
  await task.actions[0].run();
  assert.equal(h.tasks.at(-1).status, 'unknown'); assert.equal(h.calls.some(value => value.release), false);
});

test('a child completion cannot unlock a write when durable attempt validation fails', async () => {
  const h = harness(); h.rows([row('done')]); h.leases([{ importId: id, phase: 'pending' }]); h.release(async () => { throw new Error('old attempt'); });
  h.observer.sync(); await flush();
  h.observer.receive({ id: 'import:' + id, kind: 'task', operation: 'import', status: 'success', title: '已完成', importId: id }); await flush();
  assert.equal(h.tasks.at(-1).status, 'unknown'); assert.equal(h.calls.some(value => value.changed), false);
});

test('A-B-A late reads cannot publish or offer actions in the new binding', async () => {
  const h = harness(); let resolve;
  h.fetch(() => new Promise(done => { resolve = done; })); h.observer.sync();
  h.context({ caseId: 2, generation: 2 }); h.fetch(async () => ({ ok: true, json: async () => ({ case_id: '2', items: [] }) })); h.observer.sync(); await flush();
  h.context({ caseId: 1, generation: 3 }); h.observer.sync(); await flush();
  resolve({ ok: true, json: async () => ({ case_id: '1', items: [row('done')] }) }); await flush();
  assert.equal(h.tasks.length, 0);
});

test('past completed receipts stay in history without startup toasts; interrupted jobs remain discoverable', async () => {
  const h = harness(); h.rows([row('done')]); h.observer.sync(); await flush();
  assert.equal(h.tasks.length, 0); assert.equal(h.notifications.length, 0);
  h.rows([row('error', 1, { error_code: 'import_interrupted' })]); await h.observer.refresh();
  assert.equal(h.tasks.at(-1).status, 'error'); assert.match(h.tasks.at(-1).detail, /中断/); assert.equal(h.tasks.at(-1).announce, false);
});

test('a previously unknown lease can later settle through fresh owning receipt validation', async () => {
  const h = harness(); h.rows([row('done', 2)]); h.leases([{ importId: id, phase: 'unknown' }]);
  h.observer.sync(); await flush();
  assert.equal(h.calls.filter(value => value.release).length, 1); assert.equal(h.tasks.at(-1).status, 'success');
});

test('completed write with failed read refresh retains its warning and read action on later polls', async () => {
  const h = harness(); h.rows([row('importing')]); h.observer.sync(); await flush();
  h.changed(async () => { throw new Error('read unavailable'); }); h.rows([row('done', 2)]);
  await h.observer.refresh(); assert.equal(h.tasks.at(-1).status, 'warning');
  await h.observer.refresh(); assert.equal(h.tasks.at(-1).status, 'warning');
  assert.equal(h.tasks.at(-1).actions[0].label, '刷新列表');
  h.changed(async () => {}); await h.tasks.at(-1).actions[0].run();
  assert.equal(h.tasks.at(-1).status, 'success');
  assert.ok(h.calls.filter(value => value.path).every(value => !value.options.method));
});

test('reloading a source retires its running display to unknown without cancelling or completing it', async () => {
  const h = harness(); h.observer.sync(); await flush();
  h.observer.receive({ id: 'run', kind: 'task', operation: 'analysis', status: 'running', title: '正在分析' }, 'analysis:old');
  h.observer.receive({ id: 'report', kind: 'task', operation: 'download', status: 'success', title: '报告已生成' }, 'analysis:old');
  h.observer.receive({ id: 'run', kind: 'task', operation: 'analysis', status: 'running', title: '新页面分析' }, 'analysis:new');
  h.observer.detachSource('analysis:old');
  assert.equal(h.tasks.at(-1).id, 'analysis:old:run');
  assert.equal(h.tasks.at(-1).status, 'unknown');
  assert.match(h.tasks.at(-1).detail, /重新载入/);
  assert.equal(h.tasks.filter(value => value.id === 'analysis:old:report').length, 1, 'terminal results remain intact');
  assert.equal(h.tasks.filter(value => value.id === 'analysis:new:run').length, 1, 'the new instance is not changed');
  assert.equal(h.calls.some(value => value.release), false);
});

test('a delayed old-case refresh failure cannot poison a returned-to case warning state', async () => {
  const h = harness(); let rejectOld;
  h.rows([row('importing')]); h.observer.sync(); await flush();
  h.changed(() => new Promise((_resolve, reject) => { rejectOld = reject; }));
  h.rows([row('done', 2)]); const old = h.observer.refresh(); await flush();
  h.context({ caseId: 2, generation: 2 }); h.rows([]); h.observer.sync(); await flush();
  h.context({ caseId: 1, generation: 3 }); h.rows([row('importing', 2)]); h.changed(async () => {}); h.observer.sync(); await flush();
  h.rows([row('done', 3)]); await h.observer.refresh();
  assert.equal(h.tasks.at(-1).status, 'success');
  rejectOld(new Error('old refresh failed')); await old;
  await h.observer.refresh();
  assert.equal(h.tasks.at(-1).status, 'success'); assert.equal(h.tasks.at(-1).scope.generation, 3);
});
