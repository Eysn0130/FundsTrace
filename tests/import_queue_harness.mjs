import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const context = vm.createContext({ window: {} });
vm.runInContext(readFileSync(new URL('../static/import-queue.js', import.meta.url), 'utf8'), context);
const create = context.window.FundsImportQueue.create;
const files = () => ['first.zip', 'second.csv', 'third.xlsx'].map(name => ({ name,
  text() { throw new Error('Queue must not read file contents'); } }));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

test('a complete result precedes the next file and mapping does not block independent automatic items', async () => {
  const gate = deferred(), calls = [], views = [];
  const queue = create(files(), { context: { caseId: 1 }, isCurrent: () => true, changed: value => views.push(value),
    async run(item, file) {
      calls.push(file.name);
      if (item.id === 1) await gate.promise;
      return { state: item.id === 2 ? 'waiting' : 'done', importId: String(item.id) };
    } });
  const pending = queue.start();
  assert.deepEqual(calls, ['first.zip']);
  gate.resolve(); await pending;
  assert.deepEqual(calls, ['first.zip', 'second.csv', 'third.xlsx']);
  assert.deepEqual(Array.from(queue.snapshot().items, item => item.state), ['done', 'waiting', 'done']);
  assert.ok(views.every(view => view.items.every(item => !('file' in item))));
  await queue.start(); assert.equal(calls.length, 3);
});

for (const state of ['failed', 'unknown']) test(`${state} with unsafe outcome stops future scheduling`, async () => {
  let calls = 0;
  const queue = create(files(), { context: { caseId: 1 }, isCurrent: () => true,
    run: async () => { calls++; return { state, stop: true, message: 'Synthetic unsafe result' }; } });
  await queue.start();
  assert.equal(calls, 1);
  assert.deepEqual(Array.from(queue.snapshot().items, item => item.state), [state, 'skipped', 'skipped']);
});

test('a classified per-file failure allows the next complete operation', async () => {
  let calls = 0;
  const queue = create(files(), { context: { caseId: 1 }, isCurrent: () => true,
    run: async () => ({ state: ++calls === 1 ? 'failed' : 'done' }) });
  await queue.start(); assert.equal(calls, 3);
});

for (const action of ['stop', 'case-change']) test(`${action} never cancels or mislabels a submitted operation`, async () => {
  const gate = deferred(); let current = true, calls = 0;
  const queue = create(files(), { context: { caseId: 1 }, isCurrent: () => current,
    run: async () => { calls++; await gate.promise; return { state: 'done', importId: 'synthetic-durable' }; } });
  const pending = queue.start();
  if (action === 'stop') queue.stop(); else current = false;
  gate.resolve(); await pending;
  assert.equal(calls, 1);
  assert.deepEqual(Array.from(queue.snapshot().items, item => item.state), ['done', 'skipped', 'skipped']);
});
