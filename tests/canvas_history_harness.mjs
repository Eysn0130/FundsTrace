import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/canvas-history.js', import.meta.url), 'utf8');
const require = createRequire(import.meta.url);
const historyAPI = require('../static/canvas-history.js');
const clone = value => JSON.parse(JSON.stringify(value));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function fixture(options = {}) {
  let state = { nodes: [{ id: 'SYN-A', x: 0, y: 1 }], visible: ['SYN-A'] };
  const restores = [];
  const history = historyAPI.create({
    capture: () => state,
    restore: async (target, expected) => {
      assert.deepEqual(state, expected);
      restores.push(clone(target)); state = target;
    },
    ...options,
  });
  history.reset('SYN-CASE:A');
  return { history, restores, get state() { return state; }, set state(value) { state = value; } };
}

test('exports the same browser global and CommonJS create API', () => {
  const sandbox = { window: {} };
  vm.runInNewContext(source, sandbox, { filename: 'canvas-history.js' });
  assert.equal(typeof sandbox.window.FundsCanvasHistory.create, 'function');
  assert.equal(typeof historyAPI.create, 'function');
  assert.equal(Object.isFrozen(historyAPI), true);
});

test('multiple edits undo and redo in order; a new edit truncates redo', async () => {
  const f = fixture(), h = f.history;
  await h.run('move', () => { f.state.nodes[0].x = 10; });
  await h.run('hide', () => { f.state.visible = []; });
  assert.equal(h.status().count, 2); assert.equal(h.status().undoLabel, 'hide');
  assert.equal(await h.undo(), true); assert.deepEqual(f.state.visible, ['SYN-A']);
  assert.equal(await h.undo(), true); assert.equal(f.state.nodes[0].x, 0);
  assert.equal(await h.undo(), false);
  assert.equal(await h.redo(), true); assert.equal(f.state.nodes[0].x, 10);
  assert.equal(h.status().redoLabel, 'hide');
  await h.run('move again', () => { f.state.nodes[0].x = 20; });
  assert.equal(h.status().canRedo, false); assert.equal(await h.redo(), false);
  assert.equal(h.status().count, 2);
  await h.undo(); assert.equal(f.state.nodes[0].x, 10);
  await h.undo(); assert.equal(f.state.nodes[0].x, 0);
});

test('rejected or declined restore leaves both stack directions unchanged', async () => {
  let reject = true, decline = false, state = { x: 0 };
  const h = historyAPI.create({ capture: () => state, restore: async target => {
    if (reject) throw new Error('synthetic restore failure');
    if (decline) return false;
    state = target;
  } });
  await h.run('move', () => { state.x = 1; });
  await assert.rejects(h.undo(), /synthetic restore failure/);
  assert.equal(h.status().canUndo, true); assert.equal(h.status().canRedo, false);
  reject = false; decline = true;
  assert.equal(await h.undo(), false); assert.equal(h.status().undoLabel, 'move');
  decline = false; await h.undo();
  reject = true; await assert.rejects(h.redo(), /synthetic restore failure/);
  assert.equal(h.status().canUndo, false); assert.equal(h.status().canRedo, true);
  reject = false; await h.redo(); assert.equal(state.x, 1);
});

test('rapid undo/run operations are serialized and busy record is ignored', async () => {
  const gate = deferred(), started = deferred(), events = [];
  let state = { x: 0 };
  const h = historyAPI.create({ capture: () => state, restore: async (target, expected) => {
    events.push('restore start'); assert.deepEqual(state, expected); started.resolve();
    await gate.promise; state = target; events.push('restore end');
  } });
  await h.run('first', () => { state.x = 1; });
  const undo = h.undo();
  const action = h.run('second', () => { events.push('action'); state.x += 2; });
  await started.promise;
  assert.equal(h.status().busy, true); assert.equal(h.status().canUndo, false);
  assert.equal(h.record('gesture while busy', { x: 1 }, { x: 8 }), false);
  assert.deepEqual(events, ['restore start']);
  gate.resolve(); assert.equal(await undo, true); assert.equal(await action, true);
  assert.deepEqual(events, ['restore start', 'restore end', 'action']);
  assert.equal(state.x, 2); assert.equal(h.status().count, 1); assert.equal(h.status().busy, false);
});

test('reset invalidates old queued work and old in-flight run cannot record or clear new busy', async () => {
  const oldGate = deferred(), oldStart = deferred(), newGate = deferred(), newStart = deferred();
  let state = { x: 0 }, oldQueuedCalls = 0, captures = 0;
  const h = historyAPI.create({ capture: () => { captures += 1; return state; }, restore: async target => { state = target; } });
  h.reset('A');
  const oldRun = h.run('old inflight', async () => { oldStart.resolve(); await oldGate.promise; });
  const oldQueued = h.run('old queued', () => { oldQueuedCalls += 1; });
  await oldStart.promise;
  h.reset('B'); state = { x: 100 };
  const newRun = h.run('new', async () => { newStart.resolve(); await newGate.promise; state.x = 101; });
  await newStart.promise;
  oldGate.resolve(); assert.equal(await oldRun, false); assert.equal(await oldQueued, false);
  assert.equal(oldQueuedCalls, 0); assert.equal(captures, 2);
  assert.equal(h.status().busy, true); assert.equal(h.status().count, 0);
  newGate.resolve(); assert.equal(await newRun, true); assert.equal(h.status().count, 1);
  assert.equal(h.status().undoLabel, 'new'); await h.undo(); assert.equal(state.x, 100);
});

test('same-key resets invalidate queued work and an in-flight restore cannot move new history', async () => {
  const gate = deferred(), started = deferred();
  let state = { x: 0 }, restores = 0;
  const h = historyAPI.create({ capture: () => state, restore: async target => {
    restores += 1; started.resolve(); await gate.promise;
    // A real caller uses its case-bound API to guard these already-started effects.
    return true;
  } });
  h.reset('A'); await h.run('old', () => { state.x = 1; });
  const undo = h.undo(), redo = h.redo(); await started.promise;
  h.reset('A'); state = { x: 50 };
  await h.run('new', () => { state.x = 51; });
  gate.resolve(); assert.equal(await undo, false); assert.equal(await redo, false);
  assert.equal(restores, 1); assert.equal(h.status().count, 1); assert.equal(h.status().undoLabel, 'new');
  assert.equal(h.status().canUndo, true); assert.equal(h.status().canRedo, false);
});

test('record clones snapshots and restore arguments cannot mutate stored entries', async () => {
  const targets = [], expectedValues = [];
  const h = historyAPI.create({ capture: () => ({}), restore: async (target, expected) => {
    targets.push(clone(target)); expectedValues.push(clone(expected));
    target.nodes[0].x = -999; expected.nodes[0].x = -999;
  } });
  const before = { nodes: [{ id: 'SYN-A', x: 0 }] }, after = { nodes: [{ id: 'SYN-A', x: 1 }] };
  assert.equal(h.record('move', before, after), true);
  before.nodes[0].x = 80; after.nodes[0].x = 90;
  await h.undo(); await h.redo(); await h.undo();
  assert.deepEqual(targets.map(s => s.nodes[0].x), [0, 1, 0]);
  assert.deepEqual(expectedValues.map(s => s.nodes[0].x), [1, 0, 1]);
});

test('zero-change runs and records preserve redo; failed action adds no entry', async () => {
  const f = fixture(), h = f.history;
  await h.run('move', () => { f.state.nodes[0].x = 1; }); await h.undo();
  assert.equal(h.record('nothing', f.state, clone(f.state)), false);
  assert.equal(await h.run('nothing async', async () => {}), true);
  await assert.rejects(h.run('failed', async () => { throw new Error('synthetic action failure'); }), /synthetic action failure/);
  assert.equal(h.status().count, 1); assert.equal(h.status().canRedo, true); assert.equal(h.status().busy, false);
  await h.redo(); assert.equal(f.state.nodes[0].x, 1);
});

test('entry and byte budgets prune oldest undo while keeping a contiguous retained history', async () => {
  let state = { x: 0 };
  const make = limits => historyAPI.create({ capture: () => state, restore: async target => { state = target; }, ...limits });
  const h = make({ maxEntries: 2 });
  for (let i = 1; i <= 3; i++) await h.run(`step ${i}`, () => { state.x = i; });
  assert.equal(h.status().count, 2); assert.equal(h.status().limitReached, true); assert.ok(h.status().message);
  await h.undo(); await h.undo(); assert.equal(state.x, 1); assert.equal(await h.undo(), false);
  state = { x: 0 };
  // Each entry stores two 7-character JSON strings plus a one-character label = 30 bytes.
  const bounded = make({ maxBytes: 60 });
  for (let i = 1; i <= 3; i++) await bounded.run('x', () => { state.x = i; });
  assert.equal(bounded.status().count, 2); assert.equal(bounded.status().bytes, 60);
  assert.equal(bounded.status().limitReached, true);
  await bounded.undo(); await bounded.undo(); assert.equal(state.x, 1);
});

test('an over-budget single edit clears unsafe earlier history and reset clears the warning', async () => {
  let state = { x: '' };
  const h = historyAPI.create({ capture: () => state, restore: async target => { state = target; }, maxBytes: 100 });
  await h.run('small', () => { state.x = 'a'; });
  assert.equal(h.status().count, 1);
  await h.run('large', () => { state.x = 'b'.repeat(1000); });
  assert.equal(h.status().count, 0); assert.equal(h.status().bytes, 0);
  assert.equal(h.status().limitReached, true); assert.ok(h.status().message);
  assert.equal(await h.undo(), false);
  h.reset('B'); assert.equal(h.status().limitReached, false); assert.equal(h.status().message, '');
  const disabled = historyAPI.create({ capture: () => state, restore: async () => {}, maxEntries: 0 });
  assert.equal(disabled.record('disabled', { x: 0 }, { x: 1 }), false);
  assert.equal(disabled.status().count, 0); assert.equal(disabled.status().limitReached, true);
});

test('capture failures and observer exceptions do not wedge the queue', async () => {
  let fail = true, state = { x: 0 };
  const h = historyAPI.create({ capture: () => { if (fail) throw new Error('capture failed'); return state; },
    restore: async target => { state = target; }, onChange: () => { throw new Error('observer failed'); } });
  await assert.rejects(h.run('bad capture', () => {}), /capture failed/);
  assert.equal(h.status().busy, false); fail = false;
  await h.run('move', () => { state.x = 1; }); await h.undo(); assert.equal(state.x, 0);
});

test('invalidate clears idle undo and redo without changing the current display', async () => {
  const f = fixture(), h = f.history;
  assert.equal(typeof h.invalidate, 'function');
  await h.run('first', () => { f.state.nodes[0].x = 1; });
  await h.run('second', () => { f.state.nodes[0].x = 2; });
  await h.undo();
  assert.equal(h.status().canUndo, true); assert.equal(h.status().canRedo, true);
  const current = clone(f.state), restores = f.restores.length;
  h.invalidate();
  assert.deepEqual(f.state, current);
  assert.equal(h.status().busy, false); assert.equal(h.status().count, 0); assert.equal(h.status().bytes, 0);
  assert.equal(h.status().canUndo, false); assert.equal(h.status().canRedo, false);
  assert.equal(h.status().undoLabel, ''); assert.equal(h.status().redoLabel, '');
  assert.equal(await h.undo(), false); assert.equal(await h.redo(), false); assert.equal(f.restores.length, restores);
  await h.run('current revision', () => { f.state.nodes[0].x = 3; });
  assert.equal(h.status().count, 1); await h.undo(); assert.deepEqual(f.state, current);
});

test('invalidate keeps an in-flight lock, cancels old queued run/travel, and queues new work behind the action', async t => {
  const oldGate = deferred(), oldStart = deferred(), newGate = deferred(), newStart = deferred();
  t.after(() => { oldGate.resolve(); newGate.resolve(); });
  let state = { x: 0 }, captures = 0, staleActions = 0, restores = 0;
  const events = [], changes = [];
  const h = historyAPI.create({ capture: () => { captures += 1; return state; },
    restore: async target => { restores += 1; state = target; }, onChange: status => changes.push(status.busy) });
  assert.equal(typeof h.invalidate, 'function'); h.reset('A');
  const oldRun = h.run('old running', async () => {
    events.push('old start'); state.x = 1; oldStart.resolve(); await oldGate.promise; events.push('old end');
  });
  const oldQueued = h.run('old queued', () => { staleActions += 1; });
  const oldTravel = h.undo();
  await oldStart.promise; const invalidationIndex = changes.length;
  h.invalidate(); state = { x: 100 };
  assert.equal(h.status().busy, true); assert.equal(h.status().count, 0);
  assert.equal(h.record('gesture during action', { x: 100 }, { x: 900 }), false);
  const fresh = h.run('fresh queued', async () => {
    events.push('new start'); newStart.resolve(); await newGate.promise; state.x = 101; events.push('new end');
  });
  await Promise.resolve(); await Promise.resolve();
  assert.deepEqual(events, ['old start']); assert.equal(captures, 1);
  oldGate.resolve();
  assert.equal(await oldRun, false); assert.equal(await oldQueued, false); assert.equal(await oldTravel, false);
  await newStart.promise;
  assert.deepEqual(events, ['old start', 'old end', 'new start']);
  assert.equal(staleActions, 0); assert.equal(restores, 0); assert.equal(captures, 2);
  assert.equal(h.status().busy, true); assert.equal(h.status().count, 0);
  assert.ok(changes.slice(invalidationIndex).every(Boolean), 'busy never drops while the old or fresh action is pending');
  newGate.resolve(); assert.equal(await fresh, true);
  assert.equal(h.status().busy, false); assert.equal(h.status().count, 1); assert.equal(h.status().undoLabel, 'fresh queued');
  assert.equal(state.x, 101); await h.undo(); assert.equal(state.x, 100);
});

test('invalidated in-flight run does not capture or record its obsolete before snapshot', async t => {
  const gate = deferred(), started = deferred(); t.after(() => gate.resolve());
  let state = { x: 0 }, captures = 0;
  const h = historyAPI.create({ capture: () => { captures += 1; return state; }, restore: async target => { state = target; } });
  assert.equal(typeof h.invalidate, 'function');
  const oldRun = h.run('obsolete before', async () => { started.resolve(); await gate.promise; });
  await started.promise; h.invalidate(); state = { x: 50 };
  gate.resolve(); assert.equal(await oldRun, false);
  assert.equal(captures, 1, 'obsolete completion must not capture the replacement revision');
  assert.equal(h.status().busy, false); assert.equal(h.status().count, 0);
  await h.run('fresh before', () => { state.x = 51; });
  assert.equal(h.status().count, 1); await h.undo(); assert.equal(state.x, 50);
  assert.equal(await h.undo(), false, 'there is no route back to the obsolete x=0 snapshot');
});

test('an invalidated in-flight undo cannot move the replacement history cursor', async t => {
  const gate = deferred(), started = deferred(); t.after(() => gate.resolve());
  let state = { x: 0 }, restores = 0;
  const events = [];
  const h = historyAPI.create({ capture: () => state, restore: async (target, expected) => {
    restores += 1;
    if (restores === 1) {
      events.push('old restore start'); started.resolve(); await gate.promise; events.push('old restore end');
      // History cannot undo effects already sent to a case-bound caller. This
      // completion only acknowledges the old action; replacement facts stay put.
      return true;
    }
    assert.deepEqual(state, expected); state = target;
  } });
  assert.equal(typeof h.invalidate, 'function');
  await h.run('old step', () => { state.x = 1; });
  const oldUndo = h.undo(), oldRedo = h.redo(); await started.promise;
  h.invalidate(); state = { x: 100 };
  const fresh = h.run('new step', () => { events.push('new action'); state.x = 101; });
  await Promise.resolve(); await Promise.resolve();
  assert.deepEqual(events, ['old restore start']); assert.equal(h.status().busy, true);
  gate.resolve(); assert.equal(await oldUndo, false); assert.equal(await oldRedo, false); assert.equal(await fresh, true);
  assert.equal(restores, 1);
  assert.deepEqual(events, ['old restore start', 'old restore end', 'new action']);
  assert.equal(h.status().count, 1); assert.equal(h.status().undoLabel, 'new step');
  assert.equal(h.status().canUndo, true); assert.equal(h.status().canRedo, false);
  await h.undo(); assert.equal(state.x, 100); assert.equal(h.status().canRedo, true);
  await h.redo(); assert.equal(state.x, 101); assert.equal(h.status().busy, false);
});

test('reset after invalidate separates the new binding lock from old pending completions', async t => {
  const oldGate = deferred(), oldStart = deferred(), newGate = deferred(), newStart = deferred();
  t.after(() => { oldGate.resolve(); newGate.resolve(); });
  let state = { x: 0 }, staleActions = 0, captures = 0;
  const h = historyAPI.create({ capture: () => { captures += 1; return state; }, restore: async target => { state = target; } });
  assert.equal(typeof h.invalidate, 'function'); h.reset('A');
  const oldRun = h.run('old binding', async () => { oldStart.resolve(); await oldGate.promise; });
  const oldQueued = h.run('old queued binding', () => { staleActions += 1; });
  await oldStart.promise; h.invalidate(); assert.equal(h.status().busy, true);
  h.reset('B'); state = { x: 100 };
  const fresh = h.run('new binding', async () => { newStart.resolve(); await newGate.promise; state.x = 101; });
  await newStart.promise;
  oldGate.resolve(); assert.equal(await oldRun, false); assert.equal(await oldQueued, false);
  assert.equal(staleActions, 0); assert.equal(captures, 2);
  assert.equal(h.status().busy, true); assert.equal(h.status().count, 0);
  newGate.resolve(); assert.equal(await fresh, true); assert.equal(h.status().busy, false);
  assert.equal(h.status().count, 1); assert.equal(h.status().undoLabel, 'new binding');
  await h.undo(); assert.equal(state.x, 100);
});

for (const operation of ['run', 'undo'])
test('failure in an invalidated ' + operation + ' releases pending and permits fresh queued work', async t => {
  const gate = deferred(), started = deferred(); t.after(() => gate.resolve());
  let state = { x: 0 }, staleActions = 0, failRestore = operation === 'undo';
  const events = [];
  const h = historyAPI.create({ capture: () => state, restore: async (target, expected) => {
    if (failRestore) {
      failRestore = false; started.resolve(); await gate.promise; throw new Error('invalidated restore failed');
    }
    assert.deepEqual(state, expected); state = target;
  } });
  assert.equal(typeof h.invalidate, 'function');
  if (operation === 'undo') await h.run('old step', () => { state.x = 1; });
  const oldTask = operation === 'run' ? h.run('failing old action', async () => {
    started.resolve(); await gate.promise; throw new Error('invalidated action failed');
  }) : h.undo();
  const rejected = assert.rejects(oldTask, /invalidated (action|restore) failed/);
  const stale = h.run('stale queued', () => { staleActions += 1; });
  await started.promise; h.invalidate(); state = { x: 100 };
  const fresh = h.run('fresh after failure', () => { events.push('new action'); state.x = 101; });
  await Promise.resolve(); await Promise.resolve(); assert.deepEqual(events, []); assert.equal(h.status().busy, true);
  gate.resolve(); await rejected; assert.equal(await stale, false); assert.equal(await fresh, true);
  assert.equal(staleActions, 0); assert.equal(h.status().busy, false); assert.equal(h.status().count, 1);
  assert.equal(h.status().undoLabel, 'fresh after failure'); assert.equal(h.status().canRedo, false);
  await h.undo(); assert.equal(state.x, 100); assert.equal(h.status().busy, false);
});
