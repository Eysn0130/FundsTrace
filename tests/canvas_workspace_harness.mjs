import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/canvas-workspace.js', import.meta.url), 'utf8');
const flush = async () => { for (let pass = 0; pass < 24; pass += 1) await Promise.resolve(); };
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const copy = value => JSON.parse(JSON.stringify(value));
const bodyOf = request => typeof request.options?.body === 'string' ? JSON.parse(request.options.body) : request.options?.body;
const same = (left, right) => !!left && !!right && ['caseId', 'canvasId', 'generation', 'canvasGeneration'].every(key => left[key] === right[key]);
const fullSnapshot = () => ({
  nodes: [{ id: 'SYN-A', x: 13, y: 29 }, { id: 'SYN-B', x: 151, y: 197 }, { id: 'SYN-HIDDEN', x: 307, y: 409 }],
  edges: [{ source: 'SYN-A', target: 'SYN-B', label: '9007199254740993.07' },
    { source: 'SYN-B', target: 'SYN-HIDDEN', label: '0.03' }],
  presentation: { nodeStyles: { 'SYN-HIDDEN': { fill: '#367cff' } } },
  display: { edgeType: 'polyline', showIds: true },
  view: { scope: 'all', mode: 'neighborhood', visible_nodes: 2, total_nodes: 3 },
});

// Only the documented host/IO boundaries are synthetic. The shipped controller
// owns capture, serialization, requests, outcome classification and publication.
function harness({ canvasId = 7 } = {}) {
  const binding = { caseId: 11, canvasId, generation: 1, canvasGeneration: 1 };
  const requests = [], prompts = [], departures = [], collections = [], publications = [], notices = [], states = [], order = [], failures = [], completions = [];
  let name = '合成画布', ready = true;
  let ask = async action => action === 'delete' ? true : '合成新名称';
  let canLeave = async () => true, collect = async () => fullSnapshot();
  let request = async path => path.startsWith('/api/canvases?') ? [{ id: 7, name: '合成画布' }]
    : { canvas_id: 19, name: '合成新名称', copied: 3 };
  let publish = async () => {};
  const root = {};
  vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });
  const workspace = root.FundsCanvasWorkspace.create({
    context: () => ({ ...binding }), isCurrent: captured => same(captured, binding), readName: () => name,
    ready: action => { order.push('ready:' + action); return ready; },
    async canLeave(captured) { departures.push(copy(captured)); order.push('leave'); return canLeave(captured); },
    async ask(action, captured, trigger) { prompts.push({ action, context: copy(captured), trigger }); order.push('ask:' + action); return ask(action, captured, trigger); },
    async collect() { collections.push(copy(binding)); order.push('collect'); return collect(); },
    async request(path, options = {}) { requests.push({ path, options }); order.push('request:' + (options.method || 'GET')); return request(path, options); },
    async publish(action, captured, result, snapshot) { publications.push({ action, context: copy(captured), result: copy(result), snapshot: snapshot == null ? snapshot : copy(snapshot) }); order.push('publish:' + action); return publish(action, captured, result, snapshot); },
    notify(message, type) { notices.push({ message, type }); }, onState(status) { states.push(copy(status)); },
    failure(message, unknown) { failures.push({ message, unknown }); }, complete() { completions.push(copy(binding)); },
  });
  return { workspace, binding, requests, prompts, departures, collections, publications, notices, states, order, failures, completions,
    setName(value) { name = value; }, setReady(value) { ready = value; },
    onAsk(handler) { ask = handler; }, onLeave(handler) { canLeave = handler; }, onCollect(handler) { collect = handler; },
    onRequest(handler) { request = handler; }, onPublish(handler) { publish = handler; },
    writes: () => requests.filter(item => ['POST', 'PUT', 'PATCH', 'DELETE'].includes(item.options?.method)),
    change(kind = 'case') {
      if (kind === 'case') { binding.caseId = 12; binding.generation += 1; binding.caseId = 11; binding.generation += 1; }
      else { binding.canvasId = 8; binding.canvasGeneration += 1; binding.canvasId = canvasId; binding.canvasGeneration += 1; }
      workspace.sync();
    },
  };
}

test('default save becomes save-as, while default rename and delete cannot submit', async () => {
  for (const action of ['rename', 'delete']) {
    const h = harness({ canvasId: 0 }); await h.workspace.run(action);
    assert.equal(h.writes().length, 0, action); assert.equal(h.prompts.length, 0, action);
  }
  const h = harness({ canvasId: 0 }); await h.workspace.run('save');
  assert.equal(h.writes().length, 1); assert.equal(h.writes()[0].path, '/api/canvases/clone');
  assert.equal(bodyOf(h.writes()[0]).from_canvas, 0); assert.equal(h.prompts[0].action, 'clone');
  assert.equal(h.publications[0].action, 'clone'); assert.equal(h.departures.length, 1);
});

test('a new blank canvas does not copy the current graph or tracked collection', async () => {
  const h = harness(); await h.workspace.run('new');
  assert.equal(h.writes().length, 1); assert.equal(h.writes()[0].path, '/api/canvases/save');
  const payload = bodyOf(h.writes()[0]);
  assert.equal(payload.case_id, 11); assert.equal(payload.canvas_id ?? null, null);
  assert.deepEqual(copy(payload.snapshot), { nodes: [], edges: [] });
  assert.equal(h.collections.length, 0); assert.equal(h.publications[0].action, 'new');
});

test('save and save-as send the complete collected snapshot including hidden graph members', async () => {
  for (const action of ['save', 'clone']) {
    const h = harness(), snapshot = fullSnapshot(), trigger = { id: 'synthetic-toolbar-command' };
    h.onCollect(async () => snapshot); await h.workspace.run(action, trigger);
    assert.equal(h.writes().length, 1, action); assert.equal(h.collections.length, 1, action);
    const sent = bodyOf(h.writes()[0]); assert.deepEqual(copy(sent.snapshot), snapshot, action);
    assert.equal(sent.case_id, 11); assert.equal(action === 'clone' ? sent.from_canvas : sent.canvas_id, 7);
    assert.equal(h.publications[0].action, action); assert.deepEqual(h.publications[0].snapshot, snapshot);
    if (h.prompts.length) assert.equal(h.prompts[0].trigger, trigger);
  }
});

test('rename submits only the captured name and canvas identity without saving shape or leaving drafts', async () => {
  const h = harness(); await h.workspace.run('rename');
  assert.equal(h.writes().length, 1); assert.equal(h.writes()[0].path, '/api/canvases/rename');
  const sent = bodyOf(h.writes()[0]); assert.equal(sent.case_id, 11); assert.equal(sent.canvas_id, 7); assert.equal(sent.name, '合成新名称');
  assert.equal('snapshot' in sent, false); assert.equal(h.collections.length, 0); assert.equal(h.departures.length, 0);
});

test('a successful rename publishes its submitted normalized name when the server returns only an acknowledgement', async () => {
  const h = harness(); h.onAsk(async () => '  合成确认名称  '); h.onRequest(async () => ({ ok: true }));
  await h.workspace.run('rename');
  assert.equal(bodyOf(h.writes()[0]).name, '合成确认名称');
  assert.equal(h.publications.length, 1); assert.equal(h.publications[0].result.name, '合成确认名称');
  assert.equal(h.publications[0].result.ok, true); assert.equal(h.workspace.blocked(), false);
});

test('delete scopes its request to one captured canvas and case and requires a real confirmation', async () => {
  const h = harness(); await h.workspace.run('delete');
  assert.equal(h.prompts.length, 1); assert.equal(h.prompts[0].action, 'delete'); assert.equal(h.departures.length, 1);
  assert.equal(h.writes().length, 1); assert.equal(h.writes()[0].options.method, 'DELETE');
  assert.equal(h.writes()[0].path, '/api/canvases/7?case_id=11'); assert.equal(h.collections.length, 0);
  assert.equal(h.publications[0].action, 'delete');
});

test('draft disposition precedes naming/confirmation for new, clone and delete; Cancel performs no IO', async () => {
  for (const action of ['new', 'clone', 'delete']) {
    const h = harness(); h.onLeave(async () => false); await h.workspace.run(action);
    assert.equal(h.departures.length, 1, action); assert.equal(h.prompts.length, 0, action);
    assert.equal(h.requests.length, 0, action); assert.equal(h.publications.length, 0, action);
  }
  for (const action of ['save', 'rename']) {
    const h = harness(); h.onLeave(async () => { throw new Error('saving must retain account drafts'); });
    await h.workspace.run(action); assert.equal(h.departures.length, 0, action); assert.equal(h.writes().length, 1, action);
  }
});

test('the mutation lock exists before awaiting draft disposition and naming', async () => {
  for (const stage of ['leave', 'ask']) {
    const h = harness(), pending = deferred();
    if (stage === 'leave') h.onLeave(() => pending.promise); else h.onAsk(() => pending.promise);
    const first = h.workspace.run('clone'); await flush();
    assert.equal(h.workspace.blocked(), true, stage);
    assert.equal(await h.workspace.run('new'), false, stage);
    assert.equal(await h.workspace.run('clone'), false, stage);
    assert.equal(h.prompts.length, stage === 'ask' ? 1 : 0, stage);
    pending.resolve(stage === 'leave' ? false : null); await first;
    assert.equal(h.requests.length, 0, stage); assert.equal(h.workspace.blocked(), false, stage);
  }
});

test('unavailable commands and cancelled names do not collect or write', async () => {
  const h = harness(); h.setReady(false); await h.workspace.run('save');
  assert.equal(h.requests.length, 0); assert.equal(h.prompts.length, 0); assert.equal(h.collections.length, 0);
  for (const action of ['new', 'clone', 'rename', 'delete']) {
    const cancelled = harness(); cancelled.onAsk(async () => null); await cancelled.workspace.run(action);
    assert.equal(cancelled.requests.length, 0, action); assert.equal(cancelled.collections.length, 0, action);
    assert.equal(cancelled.workspace.blocked(), false, action);
  }
});

test('case and canvas A-B-A changes revoke work at every awaited preflight boundary', async () => {
  for (const kind of ['case', 'canvas']) for (const stage of ['leave', 'ask', 'collect']) {
    const h = harness(), pending = deferred();
    if (stage === 'leave') h.onLeave(() => pending.promise);
    else if (stage === 'ask') h.onAsk(() => pending.promise);
    else h.onCollect(() => pending.promise);
    const first = h.workspace.run('clone'); await flush(); h.change(kind);
    pending.resolve(stage === 'leave' ? true : stage === 'ask' ? '合成失效名称' : fullSnapshot()); await first;
    assert.equal(h.requests.length, 0, `${kind}/${stage}`); assert.equal(h.publications.length, 0, `${kind}/${stage}`);
    assert.equal(h.notices.length, 0, `${kind}/${stage}`);
  }
});

test('a delayed mutation uses its captured payload and cannot publish into a newer A-B-A binding', async () => {
  for (const kind of ['case', 'canvas']) {
    const h = harness(), pending = deferred(); h.onRequest(() => pending.promise);
    const first = h.workspace.run('clone'); await flush(); assert.equal(h.writes().length, 1);
    const captured = copy(bodyOf(h.writes()[0])); h.change(kind); const messages = h.notices.length;
    assert.equal(await h.workspace.run('new'), false, kind);
    pending.resolve({ canvas_id: 99, name: '合成旧结果' }); await first;
    assert.equal(captured.case_id, 11); assert.equal(captured.from_canvas, 7);
    assert.equal(h.publications.length, 0, kind); assert.equal(h.notices.length, messages, kind);
    assert.equal(h.writes().length, 1, kind);
  }
});

test('closing the command surface cannot release a mutation that is already in flight', async () => {
  const h = harness(), pending = deferred(); h.onRequest(() => pending.promise);
  const first = h.workspace.run('save'); await flush(); h.workspace.revoke(); h.workspace.sync();
  assert.equal(h.workspace.blocked(), true); assert.equal(await h.workspace.run('rename'), false);
  assert.equal(h.writes().length, 1); pending.resolve({ canvas_id: 7, name: '合成画布' }); await first;
  assert.equal(h.workspace.blocked(), false); assert.equal(h.writes().length, 1);
});

test('transport, server and malformed-success failures never retry a possibly committed mutation', async () => {
  for (const failure of [new Error('Synthetic transport loss'), Object.assign(new Error('Synthetic server error'), { status: 503 }),
    Object.assign(new SyntaxError('Synthetic malformed receipt'), { status: 200 })]) {
    const h = harness(); h.onRequest(async () => { throw failure; }); await h.workspace.run('clone');
    assert.equal(h.writes().length, 1, failure.message); assert.equal(h.workspace.blocked(), true, failure.message);
    assert.equal(h.publications.length, 0); assert.equal(await h.workspace.run('clone'), false);
    assert.equal(h.writes().length, 1, failure.message); assert.equal(h.workspace.acknowledge(), false);
  }
});

test('a definitive 4xx rejection releases the lock for an explicit corrected submission', async () => {
  const h = harness(); let writes = 0;
  h.onRequest(async () => { if (++writes === 1) throw Object.assign(new Error('合成名称无效'), { status: 400 }); return { canvas_id: 19, name: '合成修正名称' }; });
  await h.workspace.run('clone'); assert.equal(h.workspace.blocked(), false); assert.equal(h.publications.length, 0);
  await h.workspace.run('clone'); assert.equal(h.writes().length, 2); assert.equal(h.publications.length, 1);
});

test('pre-write collection failures release the lock without declaring a committed or unknown mutation', async () => {
  const h = harness(); h.onCollect(async () => { throw new Error('合成形态采集失败'); }); await h.workspace.run('save');
  assert.equal(h.requests.length, 0); assert.equal(h.workspace.blocked(), false); assert.equal(h.publications.length, 0);
  h.onCollect(async () => fullSnapshot()); await h.workspace.run('save'); assert.equal(h.writes().length, 1);
});

test('read-only reconciliation does not infer success from duplicate names and requires explicit acknowledgement', async () => {
  const h = harness(); h.onRequest(async () => { throw new Error('Synthetic connection loss after commit'); });
  await h.workspace.run('clone'); assert.equal(h.workspace.blocked(), true); assert.equal(h.workspace.acknowledge(), false);
  const rows = [{ id: 7, name: '同名合成画布' }, { id: 19, name: '同名合成画布' }];
  h.onRequest(async path => { assert.equal(path, '/api/canvases?case_id=11'); return rows; });
  const listed = await h.workspace.reconcile(); assert.deepEqual(copy(listed), rows);
  assert.equal(h.writes().length, 1); assert.equal(h.publications.length, 0); assert.equal(h.workspace.blocked(), true);
  assert.equal(await h.workspace.run('new'), false); assert.equal(h.workspace.acknowledge(), true);
  assert.equal(h.workspace.blocked(), false); assert.equal(h.writes().length, 1);
});

test('failed read-only reconciliation cannot unlock an unknown write or trigger another mutation', async () => {
  const h = harness(); h.onRequest(async () => { throw new Error('Synthetic unavailable service'); });
  await h.workspace.run('new'); assert.equal(await h.workspace.reconcile(), null);
  assert.equal(h.workspace.blocked(), true); assert.equal(h.workspace.acknowledge(), false);
  assert.equal(h.writes().length, 1); assert.equal(h.publications.length, 0);
});

test('a confirmed mutation followed by publication failure is reported without resubmitting the write', async () => {
  const h = harness(); h.onPublish(async () => { throw new Error('合成列表刷新失败'); }); await h.workspace.run('clone');
  assert.equal(h.writes().length, 1); assert.equal(h.publications.length, 1);
  assert.ok(h.notices.some(item => /已|完成|保存|刷新/.test(item.message)), 'a committed write needs an honest refresh warning');
  await flush(); assert.equal(h.writes().length, 1, 'refresh recovery must not replay the mutation');
});

test('a delayed publication cannot close or notify a newer case or canvas binding', async () => {
  for (const kind of ['case', 'canvas']) for (const outcome of ['success', 'failure']) {
    const h = harness(), pending = deferred(); h.onPublish(() => pending.promise);
    const first = h.workspace.run('clone'); await flush(); assert.equal(h.publications.length, 1);
    h.change(kind); const completions = h.completions.length, notices = h.notices.length;
    if (outcome === 'success') pending.resolve(); else pending.reject(new Error('Synthetic stale publication failure'));
    await first;
    assert.equal(h.completions.length, completions, `${kind}/${outcome}`); assert.equal(h.notices.length, notices, `${kind}/${outcome}`);
    assert.equal(h.writes().length, 1, `${kind}/${outcome}`);
  }
});

test('an unknown outcome remains recoverable through an explicit read of its origin after navigation', async () => {
  for (const kind of ['case', 'canvas']) {
    const h = harness(), pending = deferred(); h.onRequest(() => pending.promise);
    const first = h.workspace.run('clone'); await flush(); h.change(kind);
    pending.reject(new Error('Synthetic late lost receipt')); await first;
    assert.equal(h.workspace.blocked(), true, kind); assert.equal(h.failures.length, 0, 'stale writes do not surface an old-context error');
    const rows = [{ id: 19, name: '合成待核对结果' }];
    h.onRequest(async path => { assert.equal(path, '/api/canvases?case_id=11'); return rows; });
    assert.deepEqual(copy(await h.workspace.reconcile()), rows, kind);
    assert.equal(h.workspace.blocked(), true); assert.equal(h.workspace.acknowledge(), true);
    assert.equal(h.workspace.blocked(), false); assert.equal(h.publications.length, 0); assert.equal(h.writes().length, 1);
  }
});

test('case-export pre-save and canvas commands share one write lock in both directions', async () => {
  const external = harness(), pendingExternal = deferred(); let secondCallback = 0;
  const firstExternal = external.workspace.withWrite({ ...external.binding }, () => pendingExternal.promise); await flush();
  assert.equal(external.workspace.blocked(), true); assert.equal(await external.workspace.run('save'), false);
  await assert.rejects(external.workspace.withWrite({ ...external.binding }, async () => { secondCallback += 1; }));
  assert.equal(secondCallback, 0); assert.equal(external.requests.length, 0);
  pendingExternal.resolve({ canvas_id: 19 }); assert.deepEqual(await firstExternal, { canvas_id: 19 });
  assert.equal(external.workspace.blocked(), false);

  const command = harness(), pendingCommand = deferred(); command.onRequest(() => pendingCommand.promise);
  const firstCommand = command.workspace.run('save'); await flush(); let callback = 0;
  await assert.rejects(command.workspace.withWrite({ ...command.binding }, async () => { callback += 1; }));
  assert.equal(callback, 0); assert.equal(command.writes().length, 1);
  pendingCommand.resolve({ canvas_id: 7, name: '合成画布' }); await firstCommand;
  assert.equal(command.workspace.blocked(), false);
});

test('case-export pre-save cannot invoke a write adapter with an expired binding', async () => {
  const h = harness(), origin = { ...h.binding }; h.change(); let callbacks = 0;
  await assert.rejects(h.workspace.withWrite(origin, async () => { callbacks += 1; }));
  assert.equal(callbacks, 0); assert.equal(h.workspace.blocked(), false); assert.equal(h.requests.length, 0);
});

test('case-export pre-save respects current command availability before invoking its write adapter', async () => {
  const h = harness(); h.setReady(false); let callbacks = 0;
  await assert.rejects(h.workspace.withWrite({ ...h.binding }, async () => { callbacks += 1; }));
  assert.equal(callbacks, 0); assert.equal(h.workspace.blocked(), false); assert.equal(h.workspace.status().unknown, false);
  assert.equal(h.requests.length, 0); assert.ok(h.order.includes('ready:save'));
  h.setReady(true); assert.equal(await h.workspace.withWrite({ ...h.binding }, async () => 'synthetic-confirmed'), 'synthetic-confirmed');
  assert.equal(h.workspace.blocked(), false);
});

test('external pre-save failures become unknown only when a write was actually sent', async () => {
  for (const [writeSent, status, unknown] of [[false, undefined, false], [true, 400, false], [true, undefined, true], [true, 503, true]]) {
    const h = harness(), failure = Object.assign(new Error('Synthetic export pre-save failure'), { writeSent, status });
    await assert.rejects(h.workspace.withWrite({ ...h.binding }, async () => { throw failure; }), error => error === failure);
    assert.equal(h.workspace.blocked(), unknown, `${writeSent}/${status}`); assert.equal(h.publications.length, 0);
  }
});

// Native dialog close is queued. The view must tolerate a previously queued
// event arriving after the same dialog has been opened for a new question.
function viewHarness() {
  const elements = new Map(), closeEvents = [], binding = { caseId: 11, canvasId: 7, generation: 1, canvasGeneration: 1 };
  const document = { activeElement: null, getElementById: id => elements.get(id), createElement: () => new Control() };
  class Control {
    constructor() { this.open = false; this.disabled = false; this.hidden = false; this.isConnected = true; this.value = ''; this.listeners = new Map(); this.attributes = new Map(); this.children = [];
      this.classList = { toggle() {} }; }
    addEventListener(type, handler) { const list = this.listeners.get(type) || []; list.push(handler); this.listeners.set(type, list); }
    fire(type, options = {}) { const event = { type, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...options };
      for (const handler of this.listeners.get(type) || []) handler(event); return event; }
    click() { if (!this.disabled) this.onclick?.(); }
    focus() { if (!this.disabled) document.activeElement = this; }
    select() {} getClientRects() { return this.isConnected ? [{}] : []; }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    showModal() { this.open = true; }
    close() { this.open = false; closeEvents.push(() => this.fire('close')); }
    replaceChildren(...children) { this.children = children; }
  }
  for (const id of ['canvasActionForm', 'canvasNameForm', 'btnCanvasActions', 'canvasActionTitle', 'canvasNameField', 'canvasDeleteText',
    'canvasActionError', 'canvasActionProgress', 'canvasActionReconcile', 'canvasActionAcknowledge', 'canvasActionSubmit',
    'canvasActionCancel', 'canvasReconcileList', 'canvasActionName']) elements.set(id, new Control());
  const root = { addEventListener() {}, FundsToolbarMenu: { close() {}, place() {} }, FundsRibbon: { closePopovers() {} } };
  vm.runInNewContext(source, { window: root, console });
  const view = root.FundsCanvasWorkspace.createView(document, { context: () => ({ ...binding }), isCurrent: captured => same(captured, binding), readName: () => '合成画布' });
  let request = async () => ({ canvas_id: 19, name: '合成结果' }); const requests = [];
  const controller = root.FundsCanvasWorkspace.create({ context: () => ({ ...binding }), isCurrent: captured => same(captured, binding),
    readName: () => '合成画布', ready: () => true, canLeave: async () => true, collect: async () => fullSnapshot(), ask: view.ask,
    request: async (path, options) => { requests.push({ path, options }); return request(path, options); },
    publish: async () => {}, notify() {}, onState: view.onState, failure: view.failure, complete: view.complete, dismiss: view.dismiss });
  view.attach(controller);
  return { view, controller, binding, document, requests, element: id => elements.get(id),
    network(handler) { request = handler; }, flushClose() { for (const event of closeEvents.splice(0)) event(); },
    submit() { elements.get('canvasActionName').value = '合成名称'; return elements.get('canvasNameForm').fire('submit'); } };
}

test('pending departure writes lock both Cancel and Escape until their outcome is known', async () => {
  for (const action of ['new', 'clone', 'delete']) {
    const h = viewHarness(), pending = deferred(); h.network(() => pending.promise);
    const operation = h.controller.run(action, h.element('btnCanvasActions')); await flush();
    assert.equal(h.element('canvasActionForm').open, true, action); h.submit(); await flush();
    assert.equal(h.requests.length, 1, action); assert.equal(h.element('canvasActionCancel').disabled, true, action);
    h.element('canvasActionCancel').click(); h.element('canvasActionCancel').onclick();
    const escape = h.element('canvasActionForm').fire('cancel');
    assert.equal(escape.defaultPrevented, true, action); assert.equal(h.element('canvasActionForm').open, true, action);
    assert.equal(h.controller.blocked(), true, action); assert.equal(await h.controller.run('save'), false, action);
    pending.reject(Object.assign(new Error('合成明确拒绝'), { status: 400 })); await operation;
    assert.equal(h.element('canvasActionCancel').disabled, false, action); h.element('canvasActionCancel').click();
    assert.equal(h.element('canvasActionForm').open, false, action); assert.equal(h.requests.length, 1, action);
  }
});

test('pending rename permits closing its form while the shared mutation lock remains held', async () => {
  const h = viewHarness(), pending = deferred(); h.network(() => pending.promise);
  const operation = h.controller.run('rename', h.element('btnCanvasActions')); await flush(); h.submit(); await flush();
  assert.equal(h.requests.length, 1); assert.equal(h.element('canvasActionCancel').disabled, false);
  const escape = h.element('canvasActionForm').fire('cancel');
  assert.equal(escape.defaultPrevented, true); assert.equal(h.element('canvasActionForm').open, false);
  h.flushClose(); assert.equal(h.controller.blocked(), true); assert.equal(await h.controller.run('rename'), false);
  pending.resolve({ ok: true }); await operation; assert.equal(h.controller.blocked(), false); assert.equal(h.requests.length, 1);
});

test('a queued native close event cannot cancel a newly reopened naming form', async () => {
  const h = viewHarness(), trigger = h.element('btnCanvasActions');
  const first = h.view.ask('rename', { ...h.binding }, trigger); h.view.dismiss(); assert.equal(await first, null);
  let settled = false; const second = h.view.ask('rename', { ...h.binding }, trigger); second.then(() => { settled = true; });
  h.flushClose(); await flush();
  assert.equal(h.element('canvasActionForm').open, true); assert.equal(settled, false);
  assert.equal(h.document.activeElement, h.element('canvasActionName'));
  h.submit(); assert.equal(await second, '合成名称'); assert.equal(h.requests.length, 0);
});
