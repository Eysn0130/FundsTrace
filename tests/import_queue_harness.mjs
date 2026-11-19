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
  const h = harness({ commit: async id => { writes++; return { case_id: '1', import_id: id }; },
    job: async id => { await gate.promise; return complete(id); } });
  const pending = h.queue.upload(files); await tick();
  h.controller.abort(); gate.resolve(); await pending;
  assert.equal(writes, 1); assert.equal(h.calls.length, 1);
  assert.equal(h.queue.snapshot().items.length, 0);
});
