import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createImportWorkflow } from '../frontend/src/features/import-workflow.mjs';

const preview = (id, automatic = true) => ({ case_id: '1', import_id: id, filename: `${id}.csv`, mode: 'B', mapping: { amount: '金额' }, columns: ['金额'], stats: { total: 1, ok: 1, skipped: 0 }, self_accounts_in_file: [], preview: [{ amount: '9007199254740991.13' }], automatic });
const job = id => ({ case_id: '1', import_id: id, stage: 'done', done: 1, total: 1, result: { ok: 1, rows_dup: 0, skipped: 0 }, message: '完成' });
function harness(overrides = {}, options = {}) {
  const calls = [], controller = new AbortController();
  const client = { preview: async file => { calls.push(`preview:${file.name}`); return preview(file.name); }, previewDirectory: async files => { calls.push(`directory:${files.length}`); return preview('directory'); }, repreview: async id => preview(id), commit: async id => { calls.push(`commit:${id}`); return { case_id: '1', import_id: id, started: true }; }, job: async id => { calls.push(`job:${id}`); return job(id); }, ...overrides };
  const flow = createImportWorkflow({ target: { caseId: '1', name: '合成案件' }, client, signal: controller.signal, onImported: () => calls.push('imported'), ...options });
  const started = flow.start();
  return { flow, calls, controller, started };
}
test('one workflow processes independent files in order and retains each exact preview/result', async () => {
  const h = harness(); await h.flow.upload(() => [{ name: 'A' }, { name: 'B' }]);
  assert.deepEqual(h.calls, ['preview:A', 'commit:A', 'job:A', 'imported', 'preview:B', 'commit:B', 'job:B', 'imported']);
  assert.deepEqual(h.flow.snapshot().items.map(x => x.phase), ['done', 'done']);
  assert.equal(h.flow.snapshot().items[0].preview.preview[0].amount, '9007199254740991.13');
});
test('a folder remains one related operation rather than independent uploads', async () => {
  const h = harness(); await h.flow.upload(() => [{ name: 'a.csv' }, { name: 'b.csv' }], true);
  assert.equal(h.flow.snapshot().items.length, 1); assert.equal(h.calls[0], 'directory:2');
});
test('a machine-classified file failure continues; unknown/resource failure stops unstarted files', async () => {
  for (const kind of ['file', 'unknown']) {
    const h = harness({ preview: async file => { if (file.name === 'A') throw Object.assign(new Error('合成读取失败'), { code: 'invalid_table_file', status: 400, failureKind: kind }); return preview(file.name); } });
    await h.flow.upload(() => [{ name: 'A' }, { name: 'B' }]);
    assert.deepEqual(h.flow.snapshot().items.map(x => x.phase), kind === 'file' ? ['failed', 'done'] : ['unknown', 'skipped']);
  }
});
test('an ambiguous commit can only query its original task and never replays the write', async () => {
  let attempts = 0;
  const h = harness({ commit: async () => { attempts++; throw new Error('合成回执丢失'); } });
  await h.flow.upload(() => [{ name: 'A' }, { name: 'B' }]);
  assert.deepEqual(h.flow.snapshot().items.map(x => x.phase), ['unknown', 'skipped']);
  await h.flow.commit('1'); assert.equal(attempts, 1);
  await h.flow.query('1'); assert.equal(h.flow.snapshot().items[0].phase, 'done'); assert.equal(attempts, 1);
});
test('revocation and late upload/query callbacks cannot publish or submit', async () => {
  let finish;
  const h = harness({ preview: () => new Promise(resolve => { finish = resolve; }) });
  const work = h.flow.upload(() => [{ name: 'A' }]);
  await Promise.resolve(); h.controller.abort(); finish(preview('A')); await work;
  assert(!h.calls.some(x => x.startsWith('commit:'))); assert.equal(h.flow.snapshot().items.length, 0);
});
test('restored preview never automatically commits and mapping changes require repreview', async () => {
  const h = harness({ preview: async file => preview(file.name, false) });
  await h.flow.upload(() => [{ name: 'A' }]);
  h.flow.edit('1', { mapping: { amount: '' } }); await h.flow.commit('1');
  assert(!h.calls.includes('commit:A'));
  await h.flow.repreview('1'); assert.equal(h.flow.snapshot().items[0].phase, 'ready');
  assert(!h.calls.includes('commit:A'));
  await h.flow.commit('1'); assert.equal(h.flow.snapshot().items[0].phase, 'done');
});
test('processing time belongs to the current attempt and excludes earlier previews and user wait', async t => {
  let clock = 0; t.mock.method(performance, 'now', () => clock);
  const h = harness({ preview: async () => { clock += 100; return preview('A', false); }, repreview: async () => { clock += 200; return preview('A', false); } });
  await h.flow.upload(() => [{name:'A',size:17}]); assert.equal(h.flow.snapshot().items[0].processingMs, 100);
  clock += 5000; await h.flow.repreview('1'); assert.equal(h.flow.snapshot().items[0].processingMs, 200);
  clock += 9000; await h.flow.commit('1'); assert.equal(h.flow.snapshot().items[0].processingMs, 200);
  assert.equal(h.flow.snapshot().items[0].sizeBytes, 17); h.flow.dispose();
});
test('case mismatch is rejected before any write', async () => {
  const h = harness({ preview: async () => ({ ...preview('A'), case_id: '2' }) });
  await h.flow.upload(() => [{ name: 'A' }]);
  assert.equal(h.flow.snapshot().items[0].phase, 'unknown'); assert(!h.calls.some(x => x.startsWith('commit:')));
});

test('stopping an upload retains its ready identity without starting an automatic write', async () => {
  let finish; const h = harness({ preview: () => new Promise(resolve => { finish = resolve; }) });
  const pending = h.flow.upload(() => [{ name: 'A' }, { name: 'B' }]);
  h.flow.stop(); finish(preview('A')); await pending;
  assert.deepEqual(h.flow.snapshot().items.map(x => x.phase), ['ready', 'skipped']);
  assert.equal(h.flow.snapshot().items[0].importId, 'A'); assert(!h.calls.some(x => x.startsWith('commit:')));
});
test('failed repreview keeps the editable draft dirty; duplicate confirms send only one write', async () => {
  let finish, failed = true, count = 0;
  const h = harness({ preview: async file => preview(file.name, false),
    commit: () => { count++; return new Promise(resolve => { finish = resolve; }); },
    repreview: async () => { if (failed) throw new Error('Synthetic invalid draft'); return preview('A'); } });
  await h.flow.upload(() => [{ name: 'A' }]);
  h.flow.edit('1', { mapping: { amount: '' } }); await h.flow.repreview('1');
  assert.equal(h.flow.snapshot().items[0].dirty, true); assert.equal(h.flow.snapshot().items[0].phase, 'ready');
  await h.flow.commit('1'); assert.equal(count, 0);
  h.flow.edit('1', { mapping: { amount: '金额' } }); failed = false; await h.flow.repreview('1');
  const first = h.flow.commit('1'), second = h.flow.commit('1');
  assert.equal(count, 1); finish({ case_id: '1', import_id: 'A' }); await Promise.all([first, second]);
  assert.equal(h.flow.snapshot().items[0].phase, 'done');
});
test('mode round trips retain each draft and supplementary account input retains typed separators', async () => {
  const h = harness({ preview: async file => ({ ...preview(file.name, false), mode: 'A', mapping: { payer_account: '付款账号' } }) });
  await h.flow.upload(() => [{ name: 'A' }]);
  h.flow.edit('1', { mode: 'B', mapping: { self_account: '本方账号' } });
  h.flow.edit('1', { mapping: { self_account: '另一本方账号' }, selfAccountsText: '0001,' });
  assert.equal(h.flow.snapshot().items[0].selfAccountsText, '0001,');
  h.flow.edit('1', { selfAccountsText: '0001,0002' });
  assert.deepEqual(h.flow.snapshot().items[0].selfAccounts, ['0001', '0002']);
  h.flow.edit('1', { mode: 'A', mapping: {} });
  assert.deepEqual(h.flow.snapshot().items[0].mapping, { payer_account: '付款账号' });
  h.flow.edit('1', { mode: 'B', mapping: {} });
  assert.deepEqual(h.flow.snapshot().items[0].mapping, { self_account: '另一本方账号' });
});
test('a missing or mismatched terminal receipt stays unknown', async () => {
  for (const response of [{ ...job('A'), result: null }, { ...job('A'), case_id: '2' }, job('OTHER')]) {
    const h = harness({ job: async () => response }); await h.flow.upload(() => [{ name: 'A' }, { name: 'B' }]);
    assert.deepEqual(h.flow.snapshot().items.map(x => x.phase), ['unknown', 'skipped']); assert(!h.calls.includes('imported'));
  }
});
test('a late progress response cannot publish after its binding is disposed', async () => {
  let finish; const h = harness({ job: () => new Promise(resolve => { finish = resolve; }) });
  const pending = h.flow.upload(() => [{ name: 'A' }]);
  await new Promise(setImmediate); h.flow.dispose(); finish(job('A')); await pending;
  assert.equal(h.flow.snapshot().items.length, 0); assert(!h.calls.includes('imported'));
});
test('verified settlement retires a late POST callback without another write', async () => {
  let reject; const h = harness({ commit: () => new Promise((_resolve, fail) => { reject = fail; }) });
  const pending = h.flow.upload(() => [{ name: 'A' }]); await new Promise(setImmediate);
  h.flow.settled('A'); await new Promise(setImmediate);
  assert.equal(h.flow.snapshot().items[0].phase, 'done');
  reject(new Error('Synthetic old POST failure')); await pending;
  assert.equal(h.flow.snapshot().items[0].phase, 'done'); assert.equal(h.calls.filter(x => x === 'imported').length, 1);
});
test('restoring either API surface only reads and retains explicit empty main-account selection', async () => {
  for (const surface of ['v1', 'legacy']) {
    const source = { case_id: '1', import_id: 'A', filename: 'A.csv', surface, stage: 'ready', source_available: true, request: { mode: 'B', mapping: { amount: '金额' }, mark_accounts: [], self_accounts: ['0001'] } };
    const h = harness({ repreview: async () => ({ ...preview('A'), self_accounts_in_file: [{ account: '0002' }] }) }, { resume: source, surface });
    await h.started; assert.equal(h.flow.snapshot().items[0].phase, 'ready');
    assert.deepEqual(h.flow.snapshot().items[0].marked, []); assert.deepEqual(h.flow.snapshot().items[0].selfAccounts, ['0001']);
    assert(!h.calls.some(x => x.startsWith('commit:')));
  }
});
test('a terminal failure requires a successful explicit repreview before another POST', async () => {
  let failed = true, count = 0;
  const h = harness({ commit: async id => { count++; return { case_id: '1', import_id: id }; }, job: async id => failed ? { ...job(id), stage: 'error', result: null, recoverable: true, error: 'Synthetic rollback' } : job(id) });
  await h.flow.upload(() => [{ name: 'A' }]); await h.flow.commit('1'); assert.equal(count, 1);
  await h.flow.repreview('1'); failed = false;
  assert.equal(count, 1); await h.flow.commit('1'); assert.equal(count, 2); assert.equal(h.flow.snapshot().items[0].phase, 'done');
});

test('a reopened completion reads current member dispositions without replacing its frozen receipt or importing', async () => {
  const result = { ok: 7, total: 9 }, source = { case_id: '1', import_id: 'A', filename: 'A.zip', surface: 'v1', stage: 'done', result };
  const members = [{ member_path: 'first.csv', dispositions: { unresolved: 2 } }];
  const h = harness({ job: async id => ({ ...job(id), member_results: members, result: { ok: 999 } }) }, { resume: source });
  await h.started;
  assert.deepEqual(h.flow.snapshot().items[0].job.result, result);
  assert.deepEqual(h.flow.snapshot().items[0].job.member_results, members);
  assert(!h.calls.some(x => x.startsWith('commit:') || x === 'imported'));
});
test('history member reads have a transient loading state without pretending an import is running', async () => {
  let finish;
  const source={case_id:'1',import_id:'A',filename:'A.zip',surface:'v1',stage:'done',result:{ok:7}};
  const h=harness({job:()=>new Promise(resolve=>{finish=resolve;})},{resume:source});
  assert.equal(h.flow.snapshot().items[0].phase,'done'); assert.equal(h.flow.snapshot().items[0].loadingMembers,true);
  finish({...job('A'),member_results:[]}); await h.started;
  assert.equal(h.flow.snapshot().items[0].loadingMembers,false); assert.equal(h.flow.snapshot().items[0].job.result.ok,7);
  assert(!h.calls.some(x=>x.startsWith('commit:'))); h.flow.dispose();
});
test('a failed or late history-member read cannot erase completion or publish after revocation', async () => {
  const source = { case_id: '1', import_id: 'A', filename: 'A.zip', surface: 'v1', stage: 'done', result: { ok: 7 } };
  const failed = harness({ job: async () => { throw new Error('Synthetic unavailable'); } }, { resume: source });
  await failed.started; assert.equal(failed.flow.snapshot().items[0].phase, 'done'); assert.equal(failed.flow.snapshot().items[0].loadingMembers,false); assert.equal(failed.flow.snapshot().items[0].job.result.ok, 7);
  let finish; const late = harness({ job: () => new Promise(resolve => { finish = resolve; }) }, { resume: source });
  late.controller.abort(); finish({ ...job('A'), member_results: [] }); await late.started;
  assert.equal(late.flow.snapshot().items.length, 0);
});
test('native folder selection and directory drop retain the same root name', async () => {
  for (const file of [{ name: 'a.csv', webkitRelativePath: 'Synthetic folder/a.csv' }, { name: 'Synthetic folder/a.csv' }]) {
    const h = harness(); await h.flow.upload(() => [file], true);
    assert.equal(h.flow.snapshot().items[0].filename, 'Synthetic folder');
  }
});
test('stopping an asynchronous folder read cannot restart uploads when its file list arrives late', async () => {
  let finish; const h = harness();
  const pending = h.flow.upload(() => new Promise(resolve => { finish = resolve; }), true);
  h.flow.stop(); finish([{ name: 'Synthetic folder/a.csv' }]); await pending;
  assert.equal(h.calls.length, 0); assert.equal(h.flow.snapshot().items.length, 0);
  await h.flow.upload(() => [{ name: 'B' }]);
  assert.deepEqual(h.calls, ['preview:B','commit:B','job:B','imported']);
});

test('settlement reconciliation keeps uncertainty until its own trustworthy response and never re-submits', async () => {
  let finish,writes=0;
  const h=harness({commit:async()=>{writes++;throw new Error('Synthetic lost receipt');},job:()=>new Promise(resolve=>{finish=resolve;})});
  await h.flow.upload(()=>[{name:'A',size:17}]);assert.equal(h.flow.snapshot().items[0].phase,'unknown');assert.equal(h.flow.snapshot().items[0].processingMs,undefined);
  h.flow.settled('A');assert.equal(h.flow.snapshot().items[0].queryingOnly,true);assert.equal(writes,1);
  finish(job('A'));await new Promise(resolve=>setImmediate(resolve));
  assert.equal(h.flow.snapshot().items[0].phase,'done');assert.equal(h.flow.snapshot().items[0].queryingOnly,false);assert.equal(writes,1);
  assert.equal(h.flow.snapshot().items[0].processingMs,undefined);h.flow.dispose();
});
