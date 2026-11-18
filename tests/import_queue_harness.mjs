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
  await h.queue.upload(()=>files().slice(0,1));assert.notEqual(groups[3],groups[0]);
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
