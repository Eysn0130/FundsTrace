import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { harness } from './legacy_navigation_harness.mjs';

const source = name => readFileSync(new URL(`../static/${name}`, import.meta.url), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const response = (value, status = 200) => ({ ok: status === 200, status,
  headers: { get: () => 'application/json' }, json: async () => value, text: async () => JSON.stringify(value) });
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }

function editor() {
  const h = harness('app.js'), env = h.run('globalThis'), state = h.run('state');
  const events = new Map(), elementEvents = new Map(), messages = [], searches = [], boxes = [1, 2].map(id => ({ dataset: { id: String(id) }, checked: true }));
  env.setTimeout = (callback, delay = 0) => setTimeout(callback, delay === 1500 ? 0 : delay); env.clearTimeout = clearTimeout;
  env.performance = { now: () => 0 };
  env.CustomEvent = class { constructor(type, options) { this.type = type; this.detail = options.detail; } };
  env.window.addEventListener = (name, fn) => { if (!events.has(name)) events.set(name, new Set()); events.get(name).add(fn); };
  env.window.removeEventListener = (name, fn) => events.get(name)?.delete(fn);
  env.window.dispatchEvent = event => { for (const fn of [...events.get(event.type) || []]) fn(event); };
  const labels = ['money', 'time', 'summary'].map(em => Object.assign(h.element(`#label-${em}`), { dataset: { em }, checked: em === 'money' }));
  env.document.querySelectorAll = selector => {
    if (selector === '#flowTbody .flow-ck') return boxes;
    if (selector === '#edgeLabelGroup .edge-ck') return labels;
    if (selector.includes('.modal') || selector.includes('.act-track')) return [];
    return selector.split(',').flatMap(part => part === '#edgeLabelGroup .edge-ck' ? labels : /^#[\w-]+$/.test(part) ? [h.element(part)] : []);
  };
  const container = h.element('#graphContainer');
  container.addEventListener = (type, fn) => { if (!elementEvents.has(type)) elementEvents.set(type, []); elementEvents.get(type).push(fn); };
  h.run('globalThis.savedLoadGraph = loadGraph; globalThis.savedDoSearch = doSearch;');
  h.bindApp(); env.loadGraph = env.savedLoadGraph; env.doSearch = env.savedDoSearch;
  env.toast = (message, type) => messages.push({ message, type });
  // The table-workspace renderer owns stable anchor selection. The editor must
  // not replace its result with a checkbox snapshot captured before a request.
  env.renderFlows = () => {};
  let matrix = [1, 0, 0, 0, 1, 0, 12, 15, 1], models = [];
  const group = { getMatrix: () => matrix, setMatrix: value => { matrix = [...value]; } };
  state.graph = { getNodes: () => models.map(model => ({ getModel: () => model })), getEdges: () => [],
    get: name => name === 'group' ? group : null, paint() {}, clear() { models = []; } };
  env.renderGraph = () => {
    models = state.lastGraph.nodes.filter(node => !state.graphView.visibleIds || state.graphView.visibleIds.has(node.account))
      .map(node => ({ id: node.account, x: state.positions[node.account]?.x || 0, y: state.positions[node.account]?.y || 0,
        data: { amount: '9999999999999999.99', photo: 'SYNTHETIC-PHOTO' } }));
    state.graphInited = true;
  };
  for (const name of ['scheduleGraphViewport', 'scheduleGraphLabels', 'updateGraphViewStatus', 'updateHeader']) env[name] = () => {};
  state.caseId = 1; state.graphLoading = false; state.graphInited = true;
  state.positions = { 'SYN-A': { x: 3, y: 4 }, 'SYN-B': { x: 5, y: 6 } };
  const tracked = new Map([['1:0', [1]], ['2:0', [90]], ['1:7', [70]]]);
  const graph = ids => ({ tracked_ids: [...ids], nodes: ids.length ? [{ account: 'SYN-A' }, { account: 'SYN-B' }] : [], edges: [], main_accounts: [], focus_nodes: [] });
  state.lastGraph = graph([1]); state.tracked = new Set([1]); env.renderGraph();
  let intercept = null, conflict = false;
  h.network(async (path, options) => {
    if (intercept) { const result = intercept(path, options); if (result) return result; }
    const body = options.body ? JSON.parse(options.body) : {};
    const query = new URL(path, 'http://synthetic.local').searchParams;
    const key = `${body.case_id ?? query.get('case_id')}:${body.canvas_id ?? query.get('canvas_id') ?? 0}`;
    if (path === '/api/track/state') {
      if (conflict || JSON.stringify([...(tracked.get(key) || [])].sort()) !== JSON.stringify([...body.expected_ids].sort())) return response({ detail: 'synthetic conflict' }, 409);
      tracked.set(key, [...body.desired_ids]); return response({ ok: true, tracked_ids: body.desired_ids });
    }
    if (path === '/api/track' || path === '/api/track/bulk') {
      if (body.expected_ids && JSON.stringify([...(tracked.get(key) || [])].sort()) !== JSON.stringify([...body.expected_ids].sort())) return response({ detail: 'synthetic bulk conflict' }, 409);
      const ids = new Set(tracked.get(key) || []);
      for (const id of body.flow_ids) options.method === 'DELETE' ? ids.delete(id) : ids.add(id);
      tracked.set(key, [...ids].sort()); return response({ ok: true, tracked: ids.size, tracked_ids: [...ids].sort() });
    }
    if (path.startsWith('/api/graph')) {
      if (options.method === 'DELETE') tracked.set(key, []);
      return response(graph(tracked.get(key) || []));
    }
    if (path.startsWith('/api/flows?')) { searches.push(path); return response({ page: Number(query.get('page')), size: 50, total: 2, rows: [] }); }
    return response({});
  });
  h.run(source('canvas-history.js'));
  let snapshot;
  const create = env.window.FundsCanvasHistory.create;
  env.window.FundsCanvasHistory = { create(options) { snapshot = options.capture; return create(options); } };
  h.run(source('canvas-editor.js'));
  for (const fn of [...events.get('DOMContentLoaded') || []]) fn();
  return { h, env, state, boxes, labels, searches, messages, tracked, snapshot: () => snapshot(), history: env.window.FundsCanvasEditor.history,
    membership: env.window.FundsCanvasEditor.membership,
    get models() { return models; }, get matrix() { return matrix; }, set matrix(value) { matrix = value; },
    conflict(value) { conflict = value; }, intercept(fn) { intercept = fn; },
    event(type, event = {}) { for (const fn of [...events.get(type) || []]) fn(event); },
    gesture(type, event = {}) { for (const fn of elementEvents.get(type) || []) fn({ preventDefault() {}, stopImmediatePropagation() {}, ...event }); } };
}

test('canvas editor snapshots include only IDs, geometry and display choices', () => {
  const f = editor();
  f.state.edgeSnapshotLabels = { 'SYN-A=>SYN-B': { lx: 1, ly: 2, bw: 3, bh: 4, label: 'PRIVATE-SYNTHETIC', amount: '12.34' } };
  f.models[0].x = 44;
  const snapshot = f.snapshot(), text = JSON.stringify(snapshot);
  assert.equal(snapshot.positions['SYN-A'].x, 44);
  assert.doesNotMatch(text, /PRIVATE|amount|photo|999999/);
  assert.deepEqual(Object.keys(snapshot).sort(), ['camera','edgeBendPos','edgeLabelPos','edgeLabels','edgeSnapshotLabels','edgeType','graphView','layout','positions','showIds','showPhotos','trackedIds'].sort());
});

test('tracking, undo and redo preserve checked rows, filters, page and camera', async () => {
  const f = editor(); f.state.search.page = 3; f.h.element('#fAccount').value = 'SYN-FILTER';
  f.boxes[1].checked = false;
  await f.h.run('trackFlows([2])');
  assert.deepEqual(f.tracked.get('1:0'), [1, 2]); assert.equal(f.history.status().count, 1);
  assert.equal(f.boxes[0].checked, true); assert.equal(f.boxes[1].checked, false);
  f.matrix = [2, 0, 0, 0, 2, 0, 80, 90, 1];
  await f.h.element('#btnUndo').onclick();
  assert.deepEqual(f.tracked.get('1:0'), [1]); assert.deepEqual(f.matrix, [1, 0, 0, 0, 1, 0, 12, 15, 1]);
  await f.h.element('#btnRedo').onclick(); assert.deepEqual(f.tracked.get('1:0'), [1, 2]);
  assert.equal(f.boxes[0].checked, true); assert.equal(f.boxes[1].checked, false);
  assert.equal(f.state.search.page, 3); assert.ok(f.searches.every(path => path.includes('account=SYN-FILTER') && path.includes('page=3')));
});

test('write refresh leaves the table renderer latest anchor selection intact', async () => {
  const f = editor(); f.boxes[0].checked = true; f.boxes[1].checked = false;
  f.env.renderFlows = () => { f.boxes[0].checked = false; f.boxes[1].checked = true; };
  await f.h.run('trackFlows([2])');
  assert.equal(f.boxes[0].checked, false); assert.equal(f.boxes[1].checked, true);
});

test('membership batch accepts string IDs, retains existing geometry and camera, and is one undo step', async () => {
  const f = editor(), render = f.env.renderGraph, before = f.snapshot();
  f.models[0].x = 44;
  f.env.renderGraph = () => {
    assert.equal(f.state.forceFit, false); assert.equal(f.state.justAdded, null);
    render(); f.matrix = [3, 0, 0, 0, 3, 0, 77, 88, 1];
  };
  const result = await f.membership.set(['2', 3, '2'], true);
  assert.equal(result.status, 'confirmed'); assert.equal(result.ok, true);
  assert.deepEqual(f.tracked.get('1:0'), [1, 2, 3]);
  assert.equal(f.models[0].x, 44); assert.deepEqual(f.matrix, [...before.camera]);
  assert.equal(f.history.status().count, 1); assert.equal(f.history.status().undoLabel, '更新流水上图状态');
  await f.h.element('#btnUndo').onclick(); assert.deepEqual(f.tracked.get('1:0'), [1]);
  await f.h.element('#btnRedo').onclick(); assert.deepEqual(f.tracked.get('1:0'), [1, 2, 3]);
});

test('membership latest unsent intent coalesces without a write or history entry', async () => {
  const f = editor(), first = f.membership.set(['2'], true), last = f.membership.set([2], false);
  assert.equal((await first).status, 'superseded'); assert.equal((await last).status, 'confirmed');
  assert.deepEqual(f.tracked.get('1:0'), [1]); assert.equal(f.history.status().count, 0);
  assert.equal(f.h.requests.filter(item => item.path === '/api/track/state').length, 0);
  assert.deepEqual([...f.membership.snapshot().pendingIds], []);
});

test('membership rapid in-flight reversals serialize latest intent and retain other pending rows', async () => {
  const f = editor(), started = deferred(), gate = deferred(), flights = [];
  f.intercept((path, options) => {
    if (path !== '/api/track/state') return null;
    const body = JSON.parse(options.body); flights.push(body);
    if (flights.length === 1) {
      started.resolve(); return gate.promise.then(() => {
        f.tracked.set('1:0', body.desired_ids); return response({ ok: true, tracked_ids: body.desired_ids });
      });
    }
    return null;
  });
  const first = f.membership.set(['2'], true); await started.promise;
  const reverse = f.membership.set([2], false), other = f.membership.set(['3'], true);
  const snapshot = f.membership.snapshot();
  assert.deepEqual([...snapshot.confirmedIds], ['1']); assert.deepEqual([...snapshot.desiredIds], ['1', '3']);
  assert.deepEqual([...snapshot.pendingIds], ['2', '3']); assert.equal(snapshot.busy, true);
  assert.equal((await first).status, 'superseded'); assert.equal(flights.length, 1);
  gate.resolve();
  assert.equal((await reverse).status, 'confirmed'); assert.equal((await other).status, 'confirmed');
  assert.deepEqual(f.tracked.get('1:0'), [1, 3]); assert.equal(flights.length, 2);
  assert.deepEqual(flights[1].expected_ids, [1, 2]); assert.deepEqual(flights[1].desired_ids, [1, 3]);
  assert.equal(f.history.status().count, 2); assert.equal(f.membership.snapshot().busy, false);
});

test('membership waits behind another history command rather than dropping a click while busy', async () => {
  const f = editor(), gate = deferred();
  const previous = f.history.run('合成显示操作', () => gate.promise);
  const pending = f.membership.set([2], true); await flush();
  assert.equal(f.history.status().busy, true);
  assert.equal(f.h.requests.filter(item => item.path === '/api/track/state').length, 0);
  gate.resolve(); await previous;
  assert.equal((await pending).status, 'confirmed'); assert.deepEqual(f.tracked.get('1:0'), [1, 2]);
});

test('membership explicit failure rolls back that row, reports failure, and continues unrelated intent', async () => {
  const f = editor(), started = deferred(), gate = deferred(); let first = true;
  f.intercept(path => {
    if (path !== '/api/track/state' || !first) return null;
    first = false; started.resolve(); return gate.promise;
  });
  const failed = f.membership.set([2], true); await started.promise;
  const success = f.membership.set([3], true);
  gate.resolve(response({ detail: 'synthetic row rejection' }, 500));
  const failedResult = await failed;
  assert.equal(failedResult.status, 'failed'); assert.equal(failedResult.ok, false);
  assert.match(failedResult.error, /synthetic row rejection/);
  assert.equal((await success).status, 'confirmed'); assert.deepEqual(f.tracked.get('1:0'), [1, 3]);
  const snapshot = f.membership.snapshot();
  assert.deepEqual([...snapshot.desiredIds], ['1', '3']); assert.match(snapshot.failures['2'], /synthetic row rejection/);
  assert.equal(snapshot.failures['3'], undefined); assert.equal(f.history.status().count, 1);
  assert.equal((await f.membership.set(['2'], true)).status, 'confirmed');
  assert.equal(f.membership.snapshot().failures['2'], undefined); assert.deepEqual(f.tracked.get('1:0'), [1, 2, 3]);
});

test('membership graph failure compensates only its batch and retains earlier success for undo', async () => {
  const f = editor(); assert.equal((await f.membership.set([2], true)).status, 'confirmed');
  f.intercept(path => path.startsWith('/api/graph?') ? response({ detail: 'synthetic graph rejection' }, 500) : null);
  const result = await f.membership.set([3], true);
  assert.equal(result.status, 'failed'); assert.deepEqual(f.tracked.get('1:0'), [1, 2]);
  assert.deepEqual([...f.membership.snapshot().confirmedIds], ['1', '2']);
  assert.equal(f.history.status().count, 1); assert.match(f.membership.snapshot().failures['3'], /synthetic graph rejection/);
  f.intercept(() => null); await f.h.element('#btnUndo').onclick(); assert.deepEqual(f.tracked.get('1:0'), [1]);
});

test('membership lost write receipt revokes its binding instead of confirming a checkbox', async () => {
  const f = editor(), initial = f.membership.snapshot();
  f.intercept(path => {
    if (path !== '/api/track/state') return null;
    f.tracked.set('1:0', [1, 2]); return Promise.reject(new TypeError('synthetic receipt lost'));
  });
  const result = await f.membership.set([2], true), snapshot = f.membership.snapshot();
  assert.equal(result.ok, false); assert.equal(result.status, 'stale');
  assert.notEqual(snapshot.token, initial.token); assert.equal(snapshot.available, false);
  assert.deepEqual([...snapshot.confirmedIds], []); assert.deepEqual([...snapshot.pendingIds], []);
  assert.equal(f.history.status().count, 0); assert.deepEqual(f.tracked.get('1:0'), [1, 2]);
});

test('membership subscribers can close without cancelling sent or unsent accepted work', async () => {
  const f = editor(), started = deferred(), gate = deferred(), snapshots = [];
  const unsubscribe = f.membership.subscribe(value => snapshots.push(value)); let first = true;
  f.intercept((path, options) => {
    if (path !== '/api/track/state' || !first) return null;
    first = false; const body = JSON.parse(options.body); started.resolve();
    return gate.promise.then(() => { f.tracked.set('1:0', body.desired_ids); return response({ ok: true, tracked_ids: body.desired_ids }); });
  });
  const sent = f.membership.set([2], true); await started.promise;
  const queued = f.membership.set([3], true); unsubscribe(); const count = snapshots.length;
  gate.resolve(); assert.equal((await sent).status, 'confirmed'); assert.equal((await queued).status, 'confirmed');
  assert.equal(snapshots.length, count); assert.deepEqual(f.tracked.get('1:0'), [1, 2, 3]);
  const json = JSON.stringify(snapshots);
  assert.doesNotMatch(json, /SYN-A|SYN-B|amount|photo|999999/);
  assert.ok(snapshots.some(value => value.pendingIds.includes('2')));
  assert.equal(Object.isFrozen(snapshots[0].confirmedIds), true);
});

for (const navigation of ['selectCase(2); selectCase(1);', 'activateCanvas(7); activateCanvas(0);']) {
  test(`membership ${navigation} revokes queued intent and awaits the old sent write before its new graph read`, async () => {
    const f = editor(), started = deferred(), gate = deferred(); let first = true;
    f.intercept((path, options) => {
      if (path !== '/api/track/state' || !first) return null;
      first = false; const body = JSON.parse(options.body); started.resolve();
      return gate.promise.then(() => { f.tracked.set('1:0', body.desired_ids); return response({ ok: true, tracked_ids: body.desired_ids }); });
    });
    const sent = f.membership.set([2], true); await started.promise;
    const queued = f.membership.set([3], true), previous = f.membership.snapshot().token;
    f.h.run(navigation); const reloaded = f.h.run('loadGraph(false, caseContext())'); await flush();
    assert.equal((await sent).status, 'stale'); assert.equal((await queued).status, 'stale');
    assert.notEqual(f.membership.snapshot().token, previous);
    assert.equal(f.h.requests.filter(item => item.path.startsWith('/api/graph?')).length, 0);
    gate.resolve(); await reloaded; await flush();
    assert.deepEqual([...f.membership.snapshot().confirmedIds], ['1', '2']);
    assert.deepEqual([...f.membership.snapshot().pendingIds], []); assert.equal(f.history.status().count, 0);
    assert.equal(f.h.requests.filter(item => item.path === '/api/track/state').length, 1);
    assert.equal(f.h.requests.filter(item => item.path.startsWith('/api/graph?')).length, 1);
  });
}

test('membership publishes external graph and history changes and rejects malformed IDs without requests', async () => {
  const f = editor(), snapshots = [], unsubscribe = f.membership.subscribe(value => snapshots.push(value));
  const count = f.h.requests.length;
  for (const ids of [['bad'], ['9007199254740993'], [0], [null], Array(1)]) {
    const result = await f.membership.set(ids, true); assert.equal(result.status, 'failed'); assert.equal(result.ok, false);
  }
  assert.equal(f.h.requests.length, count);
  f.tracked.set('1:0', [1, 2]); await f.h.run('loadGraph(false, caseContext())');
  assert.deepEqual([...snapshots.at(-1).confirmedIds], ['1', '2']);
  await f.membership.set([3], true); const beforeUndo = snapshots.length;
  await f.h.element('#btnUndo').onclick();
  assert.ok(snapshots.length > beforeUndo); assert.deepEqual([...snapshots.at(-1).confirmedIds], ['1', '2']);
  unsubscribe();
});

test('bulk tracking and clear are reversible; cancel clear creates no history', async () => {
  const f = editor(); f.h.element('#bulkDir').value = 'source'; f.h.element('#bulkX').value = '5';
  await f.h.element('#btnBulkTrack').onclick(); assert.deepEqual(f.tracked.get('1:0'), [1, 2]);
  await f.h.element('#btnClearGraph').onclick(); assert.deepEqual(f.tracked.get('1:0'), []);
  await f.h.element('#btnUndo').onclick(); assert.deepEqual(f.tracked.get('1:0'), [1, 2]);
  await f.h.element('#btnUndo').onclick(); assert.deepEqual(f.tracked.get('1:0'), [1]);
  const count = f.history.status().count; f.env.confirm = () => false;
  await f.h.element('#btnClearGraph').onclick(); assert.equal(f.history.status().count, count); assert.equal(f.history.status().canRedo, true);
});

test('bulk success feedback retains anchors, direction, skipped accounts and zero expansion details', async () => {
  for (const [direction, label, party] of [['source', '来源', '付款'], ['target', '去向', '收款']]) {
    const f = editor(); f.h.element('#bulkDir').value = direction;
    f.intercept(path => {
      if (path !== '/api/track/bulk') return null;
      f.tracked.set('1:0', [1, 2]);
      return response({ ok: true, anchors: 2, tracked: 2, tracked_ids: [1, 2], skipped_not_main: [{ id: 1 }],
        detail: [{ is_main: true, picked: 0 }, { is_main: false, picked: 0 }] });
    });
    assert.equal(await f.h.element('#btnBulkTrack').onclick(), true);
    assert.ok(f.messages.some(item => item.message === `批量追踪完成：2 笔交易及其${label}流水共上图 2 笔`
      + `；1 笔被跳过（${party}方不是主账号，无法追踪）`
      + `；1 笔锚点在本方账单中无${label}流水（仅上图了本笔）`));
  }
});

test('restore conflict keeps the stack and current display unchanged', async () => {
  const f = editor(); await f.h.run('trackFlows([2])');
  f.conflict(true); const before = JSON.stringify(f.snapshot());
  assert.equal(await f.h.element('#btnUndo').onclick(), false);
  assert.equal(f.history.status().canUndo, true); assert.equal(f.history.status().canRedo, false);
  assert.equal(JSON.stringify(f.snapshot()), before); assert.deepEqual(f.tracked.get('1:0'), [1, 2]);
});

test('failed graph fetch compensates the restored tracked set without moving history', async () => {
  const f = editor(); await f.h.run('trackFlows([2])');
  const before = JSON.stringify(f.snapshot());
  f.intercept(path => path.startsWith('/api/graph?') ? response({ detail: 'synthetic graph read failure' }, 500) : null);
  assert.equal(await f.h.element('#btnUndo').onclick(), false);
  assert.deepEqual(f.tracked.get('1:0'), [1, 2]); assert.equal(f.history.status().canUndo, true);
  assert.equal(JSON.stringify(f.snapshot()), before);
});

test('a lost restore write receipt invalidates history and revokes the unconfirmed graph', async () => {
  const f = editor(); await f.h.run('trackFlows([2])');
  f.intercept((path, options) => {
    if (path !== '/api/track/state') return null;
    f.tracked.set('1:0', JSON.parse(options.body).desired_ids);
    return Promise.reject(new TypeError('synthetic restore receipt lost'));
  });
  assert.equal(await f.h.element('#btnUndo').onclick(), false);
  assert.deepEqual(f.tracked.get('1:0'), [1]); assert.equal(f.history.status().count, 0);
  assert.equal(f.state.graphLoading, true); assert.equal(f.state.tracked.size, 0); assert.equal(f.models.length, 0);
  assert.ok(f.messages.some(item => item.message.includes('状态无法确认')));
});

test('a concurrent write between restore CAS and graph read invalidates history without compensating it away', async () => {
  const f = editor(); await f.h.run('trackFlows([2])');
  const writes = f.h.requests.filter(item => item.path === '/api/track/state').length;
  f.intercept(path => {
    if (path.startsWith('/api/graph?')) f.tracked.set('1:0', [1, 99]);
    return null;
  });
  assert.equal(await f.h.element('#btnUndo').onclick(), false);
  assert.deepEqual(f.tracked.get('1:0'), [1, 99]);
  assert.equal(f.h.requests.filter(item => item.path === '/api/track/state').length, writes + 1);
  assert.equal(f.history.status().count, 0); assert.equal(f.state.graphLoading, true);
  assert.equal(f.state.tracked.size, 0); assert.equal(f.models.length, 0);
  assert.ok(f.messages.some(item => item.message.includes('状态无法确认')));
});

test('restore render failure revokes partial facts and history without another tracked-set write', async () => {
  const f = editor(); await f.h.run('trackFlows([2])');
  const writes = f.h.requests.filter(item => item.path === '/api/track/state').length;
  f.env.renderGraph = () => { throw new Error('synthetic render failure'); };
  assert.equal(await f.h.element('#btnUndo').onclick(), false);
  assert.deepEqual(f.tracked.get('1:0'), [1]);
  assert.equal(f.h.requests.filter(item => item.path === '/api/track/state').length, writes + 1);
  assert.equal(f.history.status().count, 0); assert.equal(f.history.status().canUndo, false); assert.equal(f.history.status().canRedo, false);
  assert.equal(f.state.graphLoading, true); assert.equal(f.state.tracked.size, 0);
  assert.equal(f.state.lastGraph.nodes.length, 0); assert.equal(f.models.length, 0);
  assert.ok(f.messages.some(item => item.message.includes('状态无法确认') && item.message.includes('重新加载')));
  const requests = f.h.requests.length;
  assert.equal(await f.h.element('#btnUndo').onclick(), false); assert.equal(f.h.requests.length, requests);
});

for (const operation of ['track', 'untrack', 'bulk', 'clear']) {
  test(`${operation} compensates a committed write after both graph refresh attempts fail`, async () => {
    const f = editor(); f.h.element('#showPhotoCk').checked = false;
    await f.h.element('#showPhotoCk').onchange({ target: f.h.element('#showPhotoCk') });
    const before = JSON.stringify(f.snapshot()), historyCount = f.history.status().count;
    let reads = 0;
    f.intercept(path => {
      if (path.startsWith('/api/graph?')) { reads += 1; return response({ detail: 'synthetic graph failure' }, 500); }
      return null;
    });
    const result = operation === 'track' ? await f.h.run('trackFlows([2])')
      : operation === 'untrack' ? await f.h.run('untrackFlows([1])')
      : await f.h.element(operation === 'bulk' ? '#btnBulkTrack' : '#btnClearGraph').onclick();
    assert.equal(result, false); assert.equal(reads, 2);
    assert.deepEqual(f.tracked.get('1:0'), [1]); assert.equal(JSON.stringify(f.snapshot()), before);
    assert.equal(f.history.status().count, historyCount); assert.equal(f.history.status().busy, false);
    assert.equal(f.state.graphLoading, false);
    const writes = f.h.requests.filter(request => request.path === '/api/track/state').map(request => JSON.parse(request.options.body));
    const rollback = writes.at(-1);
    assert.deepEqual(rollback.desired_ids, [1]);
    assert.deepEqual(rollback.expected_ids, operation === 'untrack' || operation === 'clear' ? [] : [1, 2]);
  });
}

test('compensation conflict preserves concurrent tracking and revokes unconfirmed facts and history', async () => {
  const f = editor(); f.h.element('#showPhotoCk').checked = false;
  await f.h.element('#showPhotoCk').onchange({ target: f.h.element('#showPhotoCk') });
  f.intercept(path => {
    if (path.startsWith('/api/graph?')) {
      f.tracked.set('1:0', [1, 2, 99]);
      return response({ detail: 'synthetic graph failure' }, 500);
    }
    return null;
  });
  assert.equal(await f.h.run('trackFlows([2])'), false);
  assert.deepEqual(f.tracked.get('1:0'), [1, 2, 99]);
  assert.equal(f.history.status().count, 0); assert.equal(f.state.tracked.size, 0);
  assert.equal(f.state.lastGraph.nodes.length, 0); assert.equal(f.models.length, 0); assert.equal(f.state.graphLoading, true);
  assert.ok(f.messages.some(item => item.message.includes('状态无法确认') && item.message.includes('重新加载')));
});

test('bulk write-time conflict preserves history and never overwrites pre-existing concurrent tracking', async () => {
  const f = editor(); f.h.element('#showPhotoCk').checked = false;
  await f.h.element('#showPhotoCk').onchange({ target: f.h.element('#showPhotoCk') });
  const before = JSON.stringify(f.snapshot()), count = f.history.status().count;
  f.tracked.set('1:0', [1, 99]);
  assert.equal(await f.h.element('#btnBulkTrack').onclick(), false);
  const request = f.h.requests.findLast(item => item.path === '/api/track/bulk');
  assert.deepEqual(JSON.parse(request.options.body).expected_ids, [1]);
  assert.deepEqual(f.tracked.get('1:0'), [1, 99]); assert.equal(JSON.stringify(f.snapshot()), before);
  assert.equal(f.history.status().count, count); assert.equal(f.history.status().busy, false);
  assert.equal(f.h.requests.filter(item => item.path === '/api/track/state').length, 0);
});

test('a lost write receipt revokes the old graph instead of presenting its state as confirmed', async () => {
  const f = editor(); f.h.element('#showPhotoCk').checked = false;
  await f.h.element('#showPhotoCk').onchange({ target: f.h.element('#showPhotoCk') });
  f.intercept(path => {
    if (path !== '/api/track/state') return null;
    f.tracked.set('1:0', [1, 2]); return Promise.reject(new TypeError('synthetic fetch receipt lost'));
  });
  assert.equal(await f.h.run('trackFlows([2])'), false);
  assert.deepEqual(f.tracked.get('1:0'), [1, 2]); assert.equal(f.state.tracked.size, 0);
  assert.equal(f.state.graphLoading, true); assert.equal(f.history.status().count, 0);
  assert.ok(f.messages.some(item => item.message.includes('状态无法确认')));
});

test('late compensation completion cannot restore old facts or geometry into a new binding', async () => {
  const f = editor(), started = deferred(), gate = deferred();
  let writes = 0;
  f.intercept((path, options) => {
    if (path.startsWith('/api/graph?') && path.includes('case_id=1')) return response({ detail: 'synthetic graph failure' }, 500);
    if (path === '/api/track/state' && ++writes === 2) {
      assert.deepEqual(JSON.parse(options.body).expected_ids, [1, 2]); started.resolve(); return gate.promise;
    }
    return null;
  });
  const pending = f.h.run('trackFlows([2])'); await started.promise;
  f.h.run('selectCase(2); state.graphLoading = false;'); await f.h.run('loadGraph(false, caseContext())');
  const before = JSON.stringify(f.snapshot());
  f.tracked.set('1:0', [1]); gate.resolve(response({ ok: true, tracked_ids: [1] })); await pending;
  assert.equal(JSON.stringify(f.snapshot()), before); assert.equal(f.state.caseId, 2);
  assert.deepEqual([...f.state.tracked], [90]); assert.equal(f.history.status().count, 0);
});

test('a concurrent tracked-set change observed by graph refresh is never admitted as this command', async () => {
  const f = editor();
  f.intercept(path => {
    if (path.startsWith('/api/graph?')) f.tracked.set('1:0', [1, 2, 99]);
    return null;
  });
  assert.equal(await f.h.run('trackFlows([2])'), false);
  assert.deepEqual(f.tracked.get('1:0'), [1, 2, 99]);
  assert.equal(f.history.status().count, 0); assert.equal(f.state.graphLoading, true); assert.equal(f.state.tracked.size, 0);
});

test('a layout timeout after graph publication compensates tracking and redraws the original geometry', async () => {
  const f = editor(), render = f.env.renderGraph;
  let first = true;
  f.h.run('globalThis.syntheticNow = 0; Date.now = () => syntheticNow;');
  f.env.renderGraph = () => {
    render();
    if (first) { first = false; f.models[0].x = 777; f.h.run('layoutJob = { requestId: 9 }'); }
  };
  const before = JSON.stringify(f.snapshot());
  const pending = f.h.run('trackFlows([2])'); await flush();
  assert.equal(f.history.status().busy, true);
  f.h.run('syntheticNow = 30001; graphMetric("layout")');
  assert.equal(await pending, false);
  assert.deepEqual(f.tracked.get('1:0'), [1]); assert.equal(JSON.stringify(f.snapshot()), before);
  assert.equal(f.models[0].x, 3); assert.equal(f.history.status().count, 0); assert.equal(f.state.graphLoading, false);
});

test('late tracking responses cannot load or record into another case or same-case generation', async () => {
  const f = editor(), gate = deferred();
  f.intercept(path => path === '/api/track/state' ? gate.promise : null);
  const pending = f.h.run('trackFlows([2])'); await flush();
  f.h.run('selectCase(2); selectCase(1); state.graphLoading = false;');
  const requests = f.h.requests.length; gate.resolve(response({ ok: true })); await pending;
  assert.equal(f.h.requests.length, requests); assert.equal(f.history.status().count, 0); assert.equal(f.state.tracked.size, 0);
  f.h.run('activateCanvas(7)'); assert.equal(f.history.status().count, 0);
});

test('layout handlers remain busy until the existing worker job actually completes', async () => {
  const f = editor();
  f.env.relayout = () => { f.h.run('layoutJob = { requestId: 1 }'); return Promise.resolve(); };
  f.h.element('#layoutSel').value = 'dagre';
  const change = f.h.element('#layoutSel').onchange({ target: f.h.element('#layoutSel') }); await flush();
  assert.equal(f.history.status().busy, true); assert.equal(f.history.status().count, 0);
  f.models[0].x = 100; f.state.positions['SYN-A'].x = 100;
  f.h.run('layoutJob = null; graphMetric("layout")'); await change;
  assert.equal(f.history.status().busy, false); assert.equal(f.history.status().count, 1);
  await f.h.element('#btnUndo').onclick(); assert.equal(f.models[0].x, 3); assert.equal(f.state.layout, 'force');
  await f.h.element('#btnRedo').onclick(); assert.equal(f.models[0].x, 100); assert.equal(f.state.layout, 'dagre');
});

test('display handlers restore original choices and command failures do not add history', async () => {
  const f = editor(); f.h.element('#showPhotoCk').checked = false;
  await f.h.element('#showPhotoCk').onchange({ target: f.h.element('#showPhotoCk') });
  assert.equal(f.state.showPhotos, false); await f.h.element('#btnUndo').onclick(); assert.equal(f.state.showPhotos, true);
  f.h.element('#edgeTypeSel').value = 'line'; await f.h.element('#edgeTypeSel').onchange({ target: f.h.element('#edgeTypeSel') });
  assert.equal(f.state.edgeType, 'line'); assert.equal(f.history.status().canRedo, false);
  const count = f.history.status().count;
  f.intercept(path => path === '/api/track/state' ? response({ detail: 'synthetic failure' }, 500) : null);
  await f.h.run('trackFlows([2])'); assert.equal(f.history.status().count, count); assert.equal(f.history.status().busy, false);
});

test('mouse gestures record geometry, exclude camera-only movement and capture double-click bend removal', async () => {
  const f = editor(); f.gesture('mousedown', { button: 0 });
  f.matrix = [1, 0, 0, 0, 1, 0, 100, 100, 1]; f.event('mouseup'); await flush();
  assert.equal(f.history.status().count, 0);
  f.gesture('mousedown', { button: 0 }); f.models[0].x = 80; f.event('mouseup'); await flush();
  assert.equal(f.history.status().count, 1); await f.h.element('#btnUndo').onclick(); assert.equal(f.models[0].x, 3);
  f.state.edgeBendPos = { 'SYN-A=>SYN-B': [{ x: 7, y: 8 }] };
  f.gesture('dblclick'); delete f.state.edgeBendPos['SYN-A=>SYN-B']; await flush();
  assert.equal(f.history.status().undoLabel, '调整连线折点');
  await f.h.element('#btnUndo').onclick(); assert.equal(f.state.edgeBendPos['SYN-A=>SYN-B'][0].x, 7);
});

test('keyboard shortcuts skip native edit controls and active dialogs', async () => {
  const f = editor(); await f.h.run('trackFlows([2])');
  let prevented = 0;
  f.event('keydown', { ctrlKey: true, key: 'z', target: { closest: () => ({}) }, preventDefault() { prevented += 1; } });
  await flush(); assert.equal(prevented, 0); assert.equal(f.history.status().canUndo, true);
  const queryAll = f.env.document.querySelectorAll;
  f.env.document.querySelectorAll = selector => selector.includes('.modal')
    ? [{ open: false, hidden: false, style: { display: 'flex' }, getClientRects: () => [1] }] : queryAll(selector);
  f.event('keydown', { ctrlKey: true, key: 'z', target: { closest: () => null }, preventDefault() { prevented += 1; } });
  await flush(); assert.equal(prevented, 0); assert.equal(f.history.status().canUndo, true);
  f.env.document.querySelectorAll = queryAll;
  f.event('keydown', { metaKey: true, key: 'z', target: { closest: () => null }, preventDefault() { prevented += 1; } });
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(prevented, 1); assert.equal(f.history.status().canRedo, true);
  f.event('keydown', { ctrlKey: true, key: 'y', target: { closest: () => null }, preventDefault() { prevented += 1; } });
  await new Promise(resolve => setTimeout(resolve, 5)); assert.equal(f.history.status().canUndo, true);
});
