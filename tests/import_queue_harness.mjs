import assert from 'node:assert/strict';
import test from 'node:test';
import { createImportWorkflow } from '../frontend/src/features/import-workflow.mjs';

const files = () => ['first.zip', 'second.csv', 'third.xlsx'].map(name => ({ name,
  text() { throw new Error('Queue must not read file contents'); } }));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const preview = (name, automatic = true) => ({ case_id: '1', import_id: name, filename: name, mode: 'B',
  columns: [], mapping: {}, preview: [], self_accounts_in_file: [], stats: { total: 1, ok: 1, skipped: 0 }, automatic });
const complete = name => ({ case_id: '1', import_id: name, stage: 'done', result: { ok: 1, skipped: 0, rows_dup: 0 }, total: 1, done: 1 });
const tick = () => new Promise(setImmediate);
function harness(overrides = {}) {
  const controller = new AbortController(), calls = [], views = [];
  const client = { preview: async file => { calls.push(file.name); return preview(file.name); },
    commit: async id => ({ case_id: '1', import_id: id }), job: async id => complete(id), ...overrides };
  const queue = createImportWorkflow({ target: { caseId: '1' }, client, signal: controller.signal });
  queue.subscribe(() => views.push(queue.snapshot()));
  return { queue, controller, calls, views };
}

test('one selection shares a material group and an appended selection gets a new group', async () => {
  const groups=[];
  const h=harness({commit:async(id,body)=>{groups.push(body.material_group_id);return {case_id:'1',import_id:id};}});
  await h.queue.upload(files);
  assert.equal(groups.length,3);assert.match(groups[0],/^[a-f0-9]{32}$/);assert.ok(groups.every(group=>group===groups[0]));
  const receipt = h.queue.snapshot().items[0].job;
  await h.queue.append(()=>files().slice(0,1));assert.notEqual(groups[3],groups[0]);
  assert.equal(h.queue.snapshot().items.length, 4);
  assert.equal(h.queue.snapshot().items[0].job, receipt);
});

test('appending during a receipt wait adds visible work without shifting selection or submitting early', async () => {
  const gate = deferred();
  const h = harness({ job: async id => { if (id === 'first.zip') await gate.promise; return complete(id); } });
  const first = h.queue.upload(() => files().slice(0, 1));
  await tick();
  await h.queue.append(() => files().slice(1));
  assert.deepEqual(h.calls, ['first.zip']);
  assert.deepEqual(h.queue.snapshot().items.map(item => item.phase), ['polling', 'queued', 'queued']);
  assert.equal(h.queue.snapshot().selectedId, '1');
  assert.equal(h.queue.remove('3'), true);
  gate.resolve(); await first;
  assert.deepEqual(h.calls, ['first.zip', 'second.csv']);
  assert.deepEqual(h.queue.snapshot().items.map(item => item.phase), ['done', 'done']);
});

test('a directory finishing enumeration after the queue goes idle starts its own operation', async () => {
  const job = deferred(), directory = deferred();
  const h = harness({ job: async id => { if (id === 'first.zip') await job.promise; return complete(id); },
    previewDirectory: async picked => { h.calls.push(...picked.map(file => file.name)); return preview('directory.zip'); } });
  const first = h.queue.upload(() => files().slice(0, 1));
  await tick(); const next = h.queue.append(() => directory.promise, true);
  job.resolve(); await first;
  assert.equal(h.queue.snapshot().reading, true);
  assert.equal(h.queue.snapshot().running, false);
  directory.resolve([{ name: 'data.csv', webkitRelativePath: 'folder/data.csv', size: 4 }]); await next;
  assert.deepEqual(h.calls, ['first.zip', 'data.csv']);
  assert.deepEqual(h.queue.snapshot().items.map(item => [item.filename, item.phase]), [['first.zip', 'done'], ['folder', 'done']]);
});

test('a known task with a lost receipt quarantines appended jobs until its original receipt is read', async () => {
  let available = false;
  const h = harness({ job: async id => { if (!available) throw new Error('Synthetic lost receipt'); return complete(id); } });
  await h.queue.upload(() => files().slice(0, 2));
  await h.queue.append(() => files().slice(2));
  assert.deepEqual(h.queue.snapshot().items.map(item => item.phase), ['unknown', 'skipped', 'queued']);
  assert.deepEqual(h.calls, ['first.zip']);
  available = true; await h.queue.query('1');
  assert.deepEqual(h.calls, ['first.zip', 'third.xlsx']);
  assert.deepEqual(h.queue.snapshot().items.map(item => item.phase), ['done', 'skipped', 'done']);
});

for (const stage of ['error', 'not-started']) test(`the first submission stops the original queue when a settlement read confirms ${stage}`, async () => {
  const oldRead = deferred(), confirmed = deferred();
  let reads = 0;
  const h = harness({ job: async id => {
    if (id !== 'first.zip') return complete(id);
    if (++reads === 1) return oldRead.promise;
    await confirmed.promise;
    if (stage === 'not-started') throw Object.assign(new Error('Synthetic not started'), { code: 'import_job_not_found' });
    return { ...complete(id), stage: 'error', result: null };
  } });
  const first = h.queue.upload(() => files().slice(0, 1));
  await tick();
  await h.queue.append(() => files().slice(1, 2));
  h.queue.settled('first.zip');
  oldRead.resolve(complete('first.zip'));
  await tick();
  confirmed.resolve();
  await first;
  await tick();
  assert.deepEqual(h.calls, ['first.zip']);
  assert.deepEqual(h.queue.snapshot().items.map(item => item.phase), [stage === 'error' ? 'failed' : 'ready', 'skipped']);
  assert.equal(h.queue.snapshot().stopped, true);
});

test('a lost settlement read stops existing work and holds only fresh appends for the original result', async () => {
  const oldRead = deferred(), lost = deferred(), recoveryRead = deferred();
  let reads = 0;
  const h = harness({ job: async id => {
    if (id === 'first.zip') {
      if (++reads === 1) return oldRead.promise;
      if (reads === 2) { await lost.promise; throw new Error('Synthetic lost settlement receipt'); }
      if (reads === 3) return recoveryRead.promise;
    }
    return complete(id);
  } });
  const first = h.queue.upload(() => files().slice(0, 1));
  await tick();
  await h.queue.append(() => files().slice(1, 2));
  h.queue.settled('first.zip');
  oldRead.resolve(complete('first.zip'));
  await tick();
  lost.resolve();
  await first;
  await tick();
  assert.deepEqual(h.queue.snapshot().items.map(item => item.phase), ['unknown', 'skipped']);
  await h.queue.append(() => files().slice(2));
  assert.deepEqual(h.calls, ['first.zip']);
  const recovering = h.queue.query('1');
  await tick();
  h.queue.settled('first.zip');
  await tick();
  assert.deepEqual(h.calls, ['first.zip', 'third.xlsx']);
  assert.deepEqual(h.queue.snapshot().items.map(item => item.phase), ['done', 'skipped', 'done']);
  recoveryRead.resolve({ ...complete('first.zip'), stage: 'error', result: null });
  await recovering;
  assert.equal(h.queue.snapshot().items[0].phase, 'done');
});

test('a matching hint settles an unknown submission during another file’s repreview', async () => {
  const mapping = deferred(), settlement = deferred(), commits = [], reads = [];
  const h = harness({
    preview: async file => { h.calls.push(file.name); return preview(file.name, file.name !== 'first.zip'); },
    commit: async id => {
      commits.push(id);
      if (id === 'second.csv') throw new Error('Synthetic lost submit acknowledgement');
      return { case_id: '1', import_id: id };
    },
    repreview: async id => { await mapping.promise; return preview(id, false); },
    job: async id => {
      reads.push(id);
      if (id === 'second.csv') await settlement.promise;
      return complete(id);
    },
  });
  await h.queue.upload(files);
  assert.deepEqual(h.queue.snapshot().items.map(item => item.phase), ['ready', 'unknown', 'skipped']);
  const repreview = h.queue.repreview('1');
  await h.queue.append(() => [{ name: 'fresh.csv', size: 1 }]);
  h.queue.settled('second.csv');
  h.queue.settled('second.csv');
  await tick();
  assert.deepEqual(reads, ['second.csv']);
  settlement.resolve();
  await tick();
  assert.deepEqual(h.queue.snapshot().items.map(item => item.phase), ['previewing', 'done', 'skipped', 'queued']);
  assert.deepEqual(h.calls, ['first.zip', 'second.csv']);
  mapping.resolve();
  await repreview;
  assert.deepEqual(h.calls, ['first.zip', 'second.csv', 'fresh.csv']);
  assert.deepEqual(commits, ['second.csv', 'fresh.csv']);
  assert.deepEqual(h.queue.snapshot().items.map(item => item.phase), ['ready', 'done', 'skipped', 'done']);
});

test('matching settlement hints coalesce and release the queue without waiting for a superseded job read', async () => {
  const oldRead = deferred(), commits = [];
  let reads = 0, oldSignal;
  const h = harness({
    commit: async id => { commits.push(id); return { case_id: '1', import_id: id }; },
    job: async (id, signal) => {
      if (id === 'first.zip' && ++reads === 1) { oldSignal = signal; return oldRead.promise; }
      return complete(id);
    },
  });
  const first = h.queue.upload(() => files().slice(0, 1));
  await tick();
  await h.queue.append(() => files().slice(1, 2));
  h.queue.settled('unrelated.zip');
  h.queue.settled('first.zip');
  h.queue.settled('first.zip');
  await tick();
  assert.deepEqual(h.queue.snapshot().items.map(item => item.phase), ['done', 'done']);
  const receipt = h.queue.snapshot().items[0].job;
  assert.equal(oldSignal.aborted, true);
  assert.equal(reads, 2);
  assert.deepEqual(commits, ['first.zip', 'second.csv']);
  oldRead.resolve({ ...complete('first.zip'), stage: 'error', result: null });
  await first;
  await tick();
  assert.equal(h.queue.snapshot().items[0].job, receipt);
  assert.equal(h.queue.snapshot().dataVersion, 2);
  assert.equal(h.queue.snapshot().stopped, false);
});

test('a delayed settlement hint leaves a ready preview and its repreview intact', async () => {
  const mapping = deferred(), h = harness({
    preview: async file => preview(file.name, false),
    job: async () => { throw new Error('A ready preview has no submitted job'); },
    repreview: async id => { await mapping.promise; return preview(id, false); },
  });
  await h.queue.upload(() => files().slice(0, 1));
  const revised = h.queue.repreview('1');
  h.queue.settled('first.zip');
  assert.equal(h.queue.snapshot().items[0].phase, 'previewing');
  mapping.resolve();
  await revised;
  h.queue.settled('first.zip');
  assert.equal(h.queue.snapshot().items[0].phase, 'ready');
  assert.equal(h.queue.snapshot().items[0].error, '');
});

test('a new append never restarts the automatic commit of files previously stopped during upload', async () => {
  const gate = deferred(), commits = [];
  const h = harness({ preview: async file => { if (file.name === 'first.zip') await gate.promise; return preview(file.name); },
    commit: async id => { commits.push(id); return { case_id: '1', import_id: id }; } });
  const first = h.queue.upload(() => files().slice(0, 2));
  await tick(); h.queue.stop(); await h.queue.append(() => files().slice(2));
  gate.resolve(); await first;
  assert.deepEqual(commits, ['third.xlsx']);
  assert.deepEqual(h.queue.snapshot().items.map(item => item.phase), ['ready', 'skipped', 'done']);
});

test('a complete receipt precedes the next upload; manual mapping does not block independent items', async () => {
  const gate = deferred(), h = harness({ preview: async file => { h.calls.push(file.name); return preview(file.name, file.name !== 'second.csv'); },
    job: async id => { if (id === 'first.zip') await gate.promise; return complete(id); } });
  const pending = h.queue.upload(files);
  await tick(); assert.deepEqual(h.calls, ['first.zip']);
  gate.resolve(); await pending;
  assert.deepEqual(h.calls, ['first.zip', 'second.csv', 'third.xlsx']);
  assert.deepEqual(h.queue.snapshot().items.map(item => item.phase), ['done', 'ready', 'done']);
  assert.ok(h.views.every(view => view.items.every(item => !('files' in item) && !('file' in item))));
  await h.queue.start(); assert.equal(h.calls.length, 3);
});

for (const outcome of ['failed', 'unknown']) test(`${outcome} after submission stops future scheduling`, async () => {
  const h = harness({ job: async id => {
    if (outcome === 'unknown') throw new Error('Synthetic disconnected transport');
    return { ...complete(id), stage: 'error', result: null, error: 'Synthetic rollback' };
  } });
  await h.queue.upload(files);
  assert.equal(h.calls.length, 1);
  assert.deepEqual(h.queue.snapshot().items.map(item => item.phase), [outcome, 'skipped', 'skipped']);
});

test('a classified file rejection allows the next complete operation', async () => {
  const h = harness({ preview: async file => { h.calls.push(file.name);
    if (file.name === 'first.zip') throw Object.assign(new Error('Synthetic invalid file'), { status: 400, failureKind: 'file' });
    return preview(file.name);
  } });
  await h.queue.upload(files); assert.equal(h.calls.length, 3);
  assert.deepEqual(h.queue.snapshot().items.map(item => item.phase), ['failed', 'done', 'done']);
});

test('stopping the queue lets a submitted operation settle without uploading later files', async () => {
  const gate = deferred(), h = harness({ job: async id => { await gate.promise; return complete(id); } });
  const pending = h.queue.upload(files); await tick();
  h.queue.stop(); gate.resolve(); await pending;
  assert.equal(h.calls.length, 1);
  assert.deepEqual(h.queue.snapshot().items.map(item => item.phase), ['done', 'skipped', 'skipped']);
});

test('case revocation clears displayed facts and ignores a late submitted receipt', async () => {
  const gate = deferred(); let writes = 0;
  const signals = [];
  const h = harness({ commit: async id => { writes++; return { case_id: '1', import_id: id }; },
    job: async (id, signal) => { signals.push(signal); await gate.promise; return complete(id); } });
  const pending = h.queue.upload(files); await tick();
  h.queue.settled('first.zip'); await tick();
  assert.equal(signals.length, 2);
  h.controller.abort(); gate.resolve(); await pending;
  h.queue.settled('first.zip');
  assert.ok(signals.every(signal => signal.aborted));
  assert.equal(writes, 1); assert.equal(h.calls.length, 1);
  assert.equal(h.queue.snapshot().dataVersion, 0);
  assert.equal(h.queue.snapshot().items.length, 0);
});
