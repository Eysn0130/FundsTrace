import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/app-navigation.js', import.meta.url), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function harness() {
  const callbacks = new Map(), elements = new Map(), posts = [], calls = { refreshCases: 0, open: [], capture: 0, refresh: 0, camera: 0, dialog: [], click: [], loads: [], withWrites: 0, writes: [], canvasLists: 0,
    feedback: [], feedbackTasks: [], feedbackSync: 0, feedbackDispose: 0, feedbackBindings: [], feedbackDetached: [], modal: [] };
  let feedbackOptions;
  const origin = 'http://synthetic.local', state = { caseId: 1, caseGeneration: 1, canvasId: 0, canvasGeneration: 1,
    graphLoading: false, graphView: { exportDepth: 0 },
    cases: [{ id: 1, name: 'Synthetic A' }, { id: 2, name: 'Synthetic B' }], graphFacts: 'initial',
    graphCamera: { resize() { calls.camera++; } } };
  let caseRead = async () => state.cases, caseOpen = async () => true, freshRead = async () => { state.graphFacts = 'fresh'; }, srcWrites = 0;
  function element(id) {
    if (elements.has(id)) return elements.get(id);
    const attributes = new Map(), classes = new Set(), listeners = new Map();
    const node = { id, inert: false, hidden: false, dataset: {},
      classList: { toggle(name, on) { if (on) classes.add(name); else classes.delete(name); }, contains: name => classes.has(name) },
      setAttribute(name, value) { attributes.set(name, String(value)); },
      getAttribute: name => attributes.get(name) ?? null, removeAttribute(name) { attributes.delete(name); },
      addEventListener(type, callback, options) { const list = listeners.get(type) || []; list.push({ callback, once: !!options?.once }); listeners.set(type, list); },
      async fire(type) {
        const list = [...(listeners.get(type) || [])];
        listeners.set(type, list.filter(item => !item.once));
        await Promise.all(list.map(item => item.callback({ type, target: node })));
      },
      click() { calls.click.push(id); this.onclick?.(); }, showModal() { calls.dialog.push(id); }, close() {},
    };
    elements.set(id, node); return node;
  }
  const frame = element('casesFrame'), analysis = element('analysisFrame'), graph = element('graphPage');
  Object.defineProperty(frame, 'src', { set(value) { srcWrites++; frame.setAttribute('src', value); }, get: () => frame.getAttribute('src') });
  Object.defineProperty(analysis, 'src', { set(value) { analysis.setAttribute('src', value); }, get: () => analysis.getAttribute('src') });
  for (const [target, node] of [['workspace', frame], ['analysis', analysis]]) node.contentWindow = {
    postMessage(message, targetOrigin) { posts.push({ message: plain(message), targetOrigin, target }); } };
  const nav = ['home', 'cases', 'accounts', 'analysis'].map(page => { const node = element('nav-' + page); node.dataset.navPage = page; return node; });
  const document = {
    querySelector(selector) {
      if (selector.startsWith('#')) return element(selector.slice(1));
      if (selector === '[data-nav-page="cases"]') return nav[1];
      if (selector === '[data-feedback-host="static"]') return null;
      throw new Error(`Unexpected selector ${selector}`);
    },
    querySelectorAll: selector => selector === '[data-nav-page]' ? nav : [],
  };
  const location = { origin, href: origin + '/' };
  const sandbox = { state, document, location, URL, requestAnimationFrame: callback => callback(),
    CustomEvent: class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } },
    history: { replaceState(_state, _unused, url) { location.href = String(url); } },
    window: { addEventListener(type, callback) { const list = callbacks.get(type) || []; list.push(callback); callbacks.set(type, list); },
      dispatchEvent(event) { for (const callback of callbacks.get(event.type) || []) callback(event); return true; },
      FundsWorkspaceSession: { capture() { calls.capture++; }, retainedCaseIds: () => ['1'] },
      FundsFeedback: { bind(scope) { calls.feedbackBindings.push(plain(scope)); }, task(value) { calls.feedbackTasks.push(value); }, setModalBlocked(open) { calls.modal.push(open); } },
      FundsFeedbackWorkspace: { create(options) { feedbackOptions = options; return {
        sync() { calls.feedbackSync++; }, dispose() { calls.feedbackDispose++; },
        receive(event, target) { calls.feedback.push({ event: plain(event), target }); },
        detachSource(target) { calls.feedbackDetached.push(target); },
      }; } } },
    caseContext: () => ({ caseId: state.caseId, generation: state.caseGeneration, canvasId: state.canvasId, canvasGeneration: state.canvasGeneration }),
    async refreshManagedCases() { calls.refreshCases++; return caseRead(); },
    async openManagedCase(id, context) {
      calls.open.push({ id, context: plain(context) }); state.caseId = id; state.caseGeneration++;
      // selectCase's real adapter publishes this synchronously as it changes
      // the feedback binding, before its asynchronous load tail completes.
      await emit('funds:feedback-binding'); return caseOpen(id, context);
    },
    async refreshAll() { calls.refresh++; await freshRead(); },
    caseWorkspace: { open(trigger) { calls.dialog.push(trigger.id); }, status: () => ({ pending: false }) },
    exportWorkspace: { status: () => ({ pending: false }) }, batchView: { deleting: false }, accountDataWrites: new Set(), nodeWrites: new Map(),
    pcState: { binding: null }, nodeConfirmation: false, nodeWorkspace: { confirming: false }, cancelAnnotationDrag: false, layoutJob: null,
    isCurrentCase: (binding, canvas = true) => String(binding.caseId) === String(state.caseId) && binding.generation === state.caseGeneration
      && (!canvas || String(binding.canvasId) === String(state.canvasId) && binding.canvasGeneration === state.canvasGeneration),
    async loadCanvas(id) { calls.loads.push(String(id)); state.canvasId = Number(id); state.canvasGeneration++; },
    async refreshCanvasList() { calls.canvasLists++; },
    async caseRequest(path, options) { calls.writes.push({ path, options }); return { id: '9' }; }, jsonOpts: value => ({ body: JSON.stringify(value) }),
    scheduleGraphViewport() {}, scheduleGraphLabels() {}, toast() {},
  };
  sandbox.window.FundsImportWorkflow = { canWrite: async () => true };
  sandbox.window.FundsCanvasActions = { status: () => ({ pending: false, unknown: false }), blocked: () => false,
    async withWrite(_binding, action) { calls.withWrites++; return action(); } };
  vm.runInNewContext(source, sandbox, { filename: 'app-navigation.js' });
  for (const ready of callbacks.get('DOMContentLoaded') || []) ready();
  const request = (overrides = {}) => ({ channel: 'fundstrace:workspace', version: 1, type: 'request',
    requestId: 'request-1', caseId: String(state.caseId), generation: state.caseGeneration,
    action: 'select-case', payload: { caseId: '2' }, ...overrides });
  async function receive(message, overrides = {}) {
    const event = { origin, source: frame.contentWindow, data: message, ...overrides };
    await Promise.all((callbacks.get('message') || []).map(callback => callback(event)));
  }
  async function emit(type, event = {}) { await Promise.all((callbacks.get(type) || []).map(callback => callback({ type, ...event }))); }
  return { calls, posts, state, frame, analysis, graph, nav, request, receive, emit, element, sandbox, feedbackOptions,
    navigation: sandbox.window.FundsAppNavigation, srcWrites: () => srcWrites,
    ready: (instanceId = 'workspace-document-1', target = frame) => receive({ channel: 'fundstrace:workspace', version: 1, type: 'ready', instanceId }, { source: target.contentWindow }),
    caseRead(fn) { caseRead = fn; }, openRead(fn) { caseOpen = fn; }, freshRead(fn) { freshRead = fn; },
  };
}

test('same-origin direct iframe request selects a verified case through the owning host', async () => {
  const h = harness(); await h.ready(); await h.receive(h.request());
  assert.equal(h.calls.refreshCases, 1);
  assert.deepEqual(h.calls.open, [{ id: 2, context: { caseId: 1, generation: 1, canvasId: 0, canvasGeneration: 1 } }]);
  assert.equal(h.state.caseId, 2);
  assert.ok(h.posts.some(item => item.message.type === 'context' && item.message.caseId === '2' && item.message.generation === 2));
  assert.ok(h.posts.some(item => item.message.type === 'result' && item.message.requestId === 'request-1' && item.message.ok === true));
  assert.ok(h.posts.every(item => item.targetOrigin === 'http://synthetic.local'));
});

test('bad origin/source/channel/version and unknown actions cannot execute host operations', async () => {
  const h = harness(); await h.ready();
  await h.receive(h.request({ requestId: 'bad-origin' }), { origin: 'https://untrusted.example' });
  await h.receive(h.request({ requestId: 'bad-source' }), { source: {} });
  for (const overrides of [
    { requestId: 'bad-channel', channel: 'different-channel' },
    { requestId: 'bad-version', version: 2 },
    { requestId: 'bad-action', action: 'run-command' },
    { requestId: 'bad-case-action', action: 'case-action', payload: { action: 'run-command' } },
    { requestId: 123 }, { requestId: 'x'.repeat(161) },
  ]) await h.receive(h.request(overrides));
  assert.equal(h.calls.refreshCases, 0); assert.deepEqual(h.calls.open, []);
  assert.deepEqual(h.calls.dialog, []); assert.deepEqual(h.calls.click, []); assert.equal(h.calls.refresh, 0);
  assert.equal(h.state.caseId, 1);
});

test('replayed request IDs never repeat an in-flight or completed case selection', async () => {
  const h = harness(), gate = deferred(); await h.ready(); h.caseRead(() => gate.promise);
  const message = h.request(), first = h.receive(message); await flush();
  await h.receive(message); assert.equal(h.calls.refreshCases, 1); assert.deepEqual(h.calls.open, []);
  gate.resolve(h.state.cases); await first;
  await h.receive(message);
  assert.equal(h.calls.refreshCases, 1); assert.equal(h.calls.open.length, 1);
  assert.equal(h.posts.filter(item => item.message.type === 'result' && item.message.requestId === message.requestId).length, 1);
});

test('binding changes during the awaited case list reject the old iframe selection', async () => {
  const h = harness(), gate = deferred(); await h.ready(); h.caseRead(() => gate.promise);
  const pending = h.receive(h.request()); await flush();
  h.state.caseGeneration++; gate.resolve(h.state.cases); await pending;
  assert.deepEqual(h.calls.open, []); assert.equal(h.state.caseId, 1);
  const reply = h.posts.find(item => item.message.type === 'result');
  assert.equal(reply.message.ok, false); assert.match(reply.message.error, /案件已变化/);
});

test('home/cases navigation preserves the iframe and graph while refreshing return-home facts', async () => {
  const h = harness(); const graph = h.graph, frame = h.frame;
  h.navigation.show('cases'); assert.equal(graph.inert, true); assert.equal(frame.hidden, false);
  assert.equal(h.srcWrites(), 1); assert.match(frame.src, /embedded=1&surface=cases&case_id=1/);
  h.navigation.show('home'); await flush();
  assert.equal(graph.inert, false); assert.equal(frame.hidden, true); assert.equal(h.calls.refresh, 1);
  assert.equal(h.state.graphFacts, 'fresh');
  h.navigation.show('cases'); h.navigation.show('cases');
  assert.equal(h.frame, frame); assert.equal(h.graph, graph); assert.equal(h.srcWrites(), 1);
  assert.equal(h.calls.capture, 2); assert.equal(h.calls.refresh, 1);
  assert.equal(h.nav[1].getAttribute('aria-current'), 'page');
});

test('only explicit supported case actions reach the existing action UI', async () => {
  const h = harness(); await h.ready();
  await h.receive(h.request({ requestId: 'export', action: 'case-action', payload: { action: 'export' } }));
  assert.deepEqual(h.calls.dialog, ['nav-cases']); assert.deepEqual(h.calls.click, ['btnExportCase']);
  await h.receive(h.request({ requestId: 'old-binding', generation: 0, action: 'case-action', payload: { action: 'delete' } }));
  assert.deepEqual(h.calls.click, ['btnExportCase']);
  assert.equal(h.calls.refreshCases, 0); assert.deepEqual(h.calls.open, []);
});

const replyFor = (h, id) => h.posts.findLast(item => item.message.type === 'result' && item.message.requestId === id)?.message;
const contexts = h => h.posts.filter(item => item.message.type === 'context').map(item => item.message);
const trackPayload = { name: 'SYN tracked canvas', flow_ids: ['71'], snapshot: {} };

test('a non-owner data refresh cannot unlock a pending selection, including an evicted reused request ID', async () => {
  for (const reuse of [false, true]) {
    const h = harness(), refresh = deferred(), selected = deferred(); await h.ready(); h.freshRead(() => refresh.promise);
    const id = reuse ? 'shared-id' : 'data-change', data = h.receive(h.request({ requestId: id, action: 'data-changed', payload: {} })); await flush();
    if (reuse) for (let index = 0; index < 2049; index++) await h.receive(h.request({ requestId: `evict-${index}`, action: 'case-action', payload: { action: 'export' } }));
    h.caseRead(() => selected.promise);
    const ownerId = reuse ? id : 'select-owner', owner = h.receive(h.request({ requestId: ownerId })); await flush();
    await h.receive(h.request({ requestId: 'before-data-end', action: 'open-graph', payload: {} }));
    assert.equal(replyFor(h, 'before-data-end').ok, false); const start = h.posts.length;
    refresh.resolve(); await data;
    await h.receive(h.request({ requestId: 'after-data-end', action: 'open-graph', payload: {} }));
    assert.equal(replyFor(h, 'after-data-end').ok, false, 'a request without this owner object cannot release the selection');
    assert.equal(h.calls.open.length, 0); assert.ok(h.posts.slice(start).filter(item => item.message.type === 'context').every(item => item.message.navigationPending === true));
    selected.resolve(h.state.cases); await owner;
    assert.equal(replyFor(h, ownerId).ok, true); assert.equal(contexts(h).at(-1).navigationPending, false);
  }
});

for (const action of ['select-case', 'open-canvas', 'track-cycle']) test(`${action} publishes owner pending and releases before its success or failure result`, async () => {
  for (const failed of [false, true]) {
    const h = harness(), gate = deferred(); await h.ready();
    const id = `${action}-${failed}`, payload = action === 'select-case' ? { caseId: '2' } : action === 'open-canvas' ? { canvasId: '9' } : trackPayload;
    if (action === 'select-case') h.caseRead(() => gate.promise);
    if (action === 'open-canvas') h.sandbox.window.FundsImportWorkflow.canWrite = () => gate.promise;
    if (action === 'track-cycle') h.sandbox.window.FundsCanvasActions.withWrite = async (_binding, write) => {
      h.calls.withWrites++; await gate.promise; if (failed) throw new Error('SYN owner write failed'); return write();
    };
    const operation = h.receive(h.request({ requestId: id, action, payload })); await flush();
    assert.equal(contexts(h).at(-1).navigationPending, true); const whileBusy = h.posts.length;
    await h.receive(h.request({ requestId: 'busy-navigation', action: 'open-graph', payload: {} }));
    assert.equal(replyFor(h, 'busy-navigation').ok, false);
    assert.ok(h.posts.slice(whileBusy).filter(item => item.message.type === 'context').every(item => item.message.navigationPending === true), 'busy rejection never publishes a false release');
    gate.resolve(action === 'select-case' ? failed ? [] : h.state.cases : !failed); await operation;
    const result = replyFor(h, id); assert.equal(result.ok, !failed);
    const resultIndex = h.posts.findLastIndex(item => item.message === result || item.message.type === 'result' && item.message.requestId === id);
    const lastContext = h.posts.findLastIndex((item, index) => index < resultIndex && item.message.type === 'context');
    assert.equal(h.posts[lastContext].message.navigationPending, false, 'release context precedes the corresponding result');
    assert.equal(h.posts.slice(resultIndex + 1).filter(item => item.message.type === 'context').length, 0, 'finally cannot clear an error with a later context');
    await h.receive(h.request({ requestId: 'after-owner', action: 'open-graph', payload: {} })); assert.equal(replyFor(h, 'after-owner').ok, true);
  }
});

test('track-cycle rechecks owner and the full canvas binding after canWrite before acquiring or writing', async () => {
  for (const interference of ['owner', 'canvas-generation']) {
    const h = harness(), permission = deferred(), selected = deferred(); await h.ready();
    h.sandbox.window.FundsImportWorkflow.canWrite = () => permission.promise;
    const track = h.receive(h.request({ requestId: 'waiting-track', action: 'track-cycle', payload: trackPayload })); await flush();
    let owner;
    if (interference === 'owner') { h.caseRead(() => selected.promise); owner = h.receive(h.request({ requestId: 'new-owner' })); await flush(); }
    else h.state.canvasGeneration++;
    permission.resolve(true); await track;
    assert.equal(h.calls.withWrites, 0, 'a stale or superseded permission cannot acquire the write lease'); assert.equal(h.calls.writes.length, 0);
    assert.equal(replyFor(h, 'waiting-track').ok, false);
    if (owner) {
      await h.receive(h.request({ requestId: 'still-owned', action: 'open-graph', payload: {} })); assert.equal(replyFor(h, 'still-owned').ok, false);
      assert.equal(contexts(h).at(-1).navigationPending, true); selected.resolve(h.state.cases); await owner;
    }
  }
});

test('the newly selected case context remains pending until the owning case-open tail finishes', async () => {
  const h = harness(), tail = deferred(); await h.ready(); h.openRead(() => tail.promise);
  const owner = h.receive(h.request({ requestId: 'select-tail' })); await flush();
  assert.equal(h.state.caseId, 2); h.navigation.sync();
  assert.equal(contexts(h).at(-1).caseId, '2'); assert.equal(contexts(h).at(-1).navigationPending, true);
  await h.receive(h.request({ requestId: 'new-case-entry', action: 'open-graph', payload: {} }));
  assert.equal(replyFor(h, 'new-case-entry').ok, false);
  tail.resolve(true); await owner; assert.equal(contexts(h).at(-1).navigationPending, false);
});

const feedbackMessage = (h, instanceId, overrides = {}) => ({ channel: 'fundstrace:workspace', version: 1,
  type: 'feedback', caseId: String(h.state.caseId), generation: h.state.caseGeneration, instanceId,
  event: { id: 'synthetic-task', kind: 'task', operation: 'import', status: 'running', title: '合成任务' }, ...overrides });
const modalMessage = (h, instanceId, open, overrides = {}) => ({ channel: 'fundstrace:workspace', version: 1,
  type: 'feedback-modal', caseId: String(h.state.caseId), generation: h.state.caseGeneration, instanceId, open, ...overrides });

test('feedback requires a valid ready document and routes each iframe with its own instance namespace', async () => {
  const h = harness(), workspaceId = 'workspace-document-feedback', analysisId = 'analysis-document-feedback';
  await h.receive(feedbackMessage(h, workspaceId));
  await h.ready('short'); await h.receive(feedbackMessage(h, 'short'));
  assert.equal(h.calls.feedback.length, 0);
  await h.ready(workspaceId);
  const event = feedbackMessage(h, workspaceId);
  await h.receive(event);
  await h.ready(analysisId, h.analysis);
  const analysis = feedbackMessage(h, analysisId, { event: { id: 'run-1', kind: 'task', operation: 'analysis', status: 'success', title: '分析完成' } });
  await h.receive(analysis, { source: h.analysis.contentWindow });
  assert.deepEqual(h.calls.feedback, [
    { event: event.event, target: 'workspace:' + workspaceId },
    { event: analysis.event, target: 'analysis:' + analysisId },
  ]);
  assert.equal(h.calls.refreshCases, 0); assert.deepEqual(h.calls.writes, []);
  assert.ok(h.posts.some(item => item.target === 'analysis' && item.message.type === 'context'));
});

test('feedback rejects foreign origin, source, protocol, case, generation and document identity on both frames', async () => {
  const h = harness();
  for (const [target, instanceId, operation] of [[h.frame, 'workspace-document-boundary', 'import'], [h.analysis, 'analysis-document-boundary', 'analysis']]) {
    await h.ready(instanceId, target);
    const message = feedbackMessage(h, instanceId, { event: { id: 'boundary-task', kind: 'task', operation, status: 'running', title: '合成边界任务' } });
    const before = h.calls.feedback.length;
    await h.receive(message, { source: target.contentWindow, origin: 'https://foreign.invalid' });
    await h.receive(message, { source: {} });
    for (const invalid of [{ channel: 'foreign' }, { version: 2 }, { caseId: '2' }, { generation: 0 }, { generation: '1' }, { instanceId: 'old-document-instance' }]) {
      await h.receive({ ...message, ...invalid }, { source: target.contentWindow });
    }
    await h.receive(message, { source: target === h.frame ? h.analysis.contentWindow : h.frame.contentWindow });
    assert.equal(h.calls.feedback.length, before);
    await h.receive(message, { source: target.contentWindow });
    assert.equal(h.calls.feedback.length, before + 1);
  }
  const before = h.calls.feedback.length;
  await h.receive(feedbackMessage(h, 'analysis-document-boundary'), { source: h.analysis.contentWindow });
  assert.equal(h.calls.feedback.length, before, 'analysis may publish only analysis or download operations');
  assert.equal(h.calls.refreshCases, 0); assert.deepEqual(h.calls.writes, []);
});

test('a frame load clears its ready instance and modal, sends hello, and requires the new document handshake', async () => {
  for (const [page, key] of [['cases', 'workspace'], ['analysis', 'analysis']]) {
    const h = harness(), target = key === 'workspace' ? h.frame : h.analysis;
    h.navigation.show(page);
    const oldId = key + '-document-before-load', newId = key + '-document-after-load';
    await h.ready(oldId, target);
    await h.receive(modalMessage(h, oldId, true), { source: target.contentWindow });
    assert.equal(h.calls.modal.at(-1), true);
    const posted = h.posts.length; await target.fire('load');
    assert.equal(h.calls.modal.at(-1), false);
    const hello = h.posts.slice(posted).find(item => item.message.type === 'feedback-hello');
    assert.deepEqual(hello, { target: key, targetOrigin: 'http://synthetic.local', message: { channel: 'fundstrace:workspace', version: 1, type: 'feedback-hello' } });
    const event = feedbackMessage(h, oldId, { event: { id: 'reload-task', kind: 'task', operation: key === 'analysis' ? 'analysis' : 'import', status: 'success', title: '合成完成' } });
    const before = h.calls.feedback.length;
    await h.receive(event, { source: target.contentWindow });
    await h.receive(modalMessage(h, oldId, true), { source: target.contentWindow });
    assert.equal(h.calls.feedback.length, before); assert.equal(h.calls.modal.at(-1), false);
    await h.ready(newId, target);
    await h.receive(event, { source: target.contentWindow });
    assert.equal(h.calls.feedback.length, before, 'the reloaded frame cannot reuse its old document identity');
    await h.receive({ ...event, instanceId: newId }, { source: target.contentWindow });
    assert.equal(h.calls.feedback.length, before + 1);
  }
});

test('only the visible iframe and its current modal identity can block global feedback', async () => {
  const h = harness(), workspaceId = 'workspace-modal-document', analysisId = 'analysis-modal-document';
  await h.ready(workspaceId); await h.ready(analysisId, h.analysis);
  h.navigation.show('cases');
  await h.receive(modalMessage(h, analysisId, true), { source: h.analysis.contentWindow });
  assert.equal(h.calls.modal.at(-1), false, 'a hidden analysis confirmation does not block the cases page');
  for (const invalid of [{ instanceId: 'stale-modal-document' }, { caseId: '2' }, { generation: 0 }, { open: 'true' }]) {
    await h.receive({ ...modalMessage(h, workspaceId, true), ...invalid });
    assert.equal(h.calls.modal.at(-1), false);
  }
  await h.receive(modalMessage(h, workspaceId, true), { origin: 'https://foreign.invalid' });
  await h.receive(modalMessage(h, workspaceId, true), { source: {} });
  assert.equal(h.calls.modal.at(-1), false);
  await h.receive(modalMessage(h, workspaceId, true)); assert.equal(h.calls.modal.at(-1), true);
  h.navigation.show('home'); assert.equal(h.calls.modal.at(-1), false);
  h.navigation.show('analysis'); assert.equal(h.calls.modal.at(-1), true, 'the retained analysis modal applies only after that page is shown');
  await h.receive(modalMessage(h, analysisId, false), { source: h.analysis.contentWindow });
  assert.equal(h.calls.modal.at(-1), false);
  h.navigation.show('accounts'); assert.equal(h.calls.modal.at(-1), true, 'cases and accounts share the retained workspace frame');
  await h.receive(modalMessage(h, workspaceId, false)); assert.equal(h.calls.modal.at(-1), false);
});

test('case binding changes clear both retained modal states and reject old-case feedback', async () => {
  const h = harness(), workspaceId = 'workspace-case-document', analysisId = 'analysis-case-document';
  await h.ready(workspaceId); await h.ready(analysisId, h.analysis); h.navigation.show('cases');
  await h.receive(modalMessage(h, workspaceId, true));
  await h.receive(modalMessage(h, analysisId, true), { source: h.analysis.contentWindow });
  const oldWorkspace = feedbackMessage(h, workspaceId), oldAnalysis = feedbackMessage(h, analysisId, {
    event: { id: 'old-analysis', kind: 'task', operation: 'analysis', status: 'success', title: '旧案分析' },
  });
  const syncs = h.calls.feedbackSync;
  await h.receive(h.request({ requestId: 'feedback-select-case' }));
  assert.equal(h.state.caseId, 2); assert.ok(h.calls.feedbackSync > syncs);
  assert.equal(h.calls.modal.at(-1), false);
  h.navigation.show('analysis'); assert.equal(h.calls.modal.at(-1), false);
  h.navigation.show('cases'); assert.equal(h.calls.modal.at(-1), false);
  await h.receive(oldWorkspace); await h.receive(oldAnalysis, { source: h.analysis.contentWindow });
  assert.equal(h.calls.feedback.length, 0);
  await h.receive(feedbackMessage(h, workspaceId)); assert.equal(h.calls.feedback.length, 1);
  await h.emit('pagehide'); assert.equal(h.calls.feedbackDispose, 1);
});

test('opening an import from tasks queues the first workspace visit until ready publishes its current context', async () => {
  const h = harness(), importId = '0123456789abcdef';
  h.feedbackOptions.openImport(importId, 'v1', h.sandbox.caseContext());
  assert.equal(h.frame.hidden, false); assert.equal(h.graph.inert, true);
  assert.equal(h.srcWrites(), 1); assert.equal(h.posts.some(item => item.message.type === 'feedback-open-import'), false);
  await h.frame.fire('load');
  assert.equal(h.posts.some(item => item.message.type === 'feedback-hello'), true);
  assert.equal(h.posts.some(item => item.message.type === 'feedback-open-import'), false);
  const start = h.posts.length; await h.ready('workspace-first-import-document');
  const messages = h.posts.slice(start).filter(item => item.target === 'workspace').map(item => item.message);
  const opening = messages.findIndex(message => message.type === 'feedback-open-import');
  const bound = messages.findIndex(message => message.type === 'context');
  assert.ok(bound >= 0 && opening > bound, 'the iframe receives a valid case context before opening its queued import');
  assert.equal(messages[bound].caseId, '1'); assert.equal(messages[bound].generation, 1);
  assert.deepEqual(messages[opening], { channel: 'fundstrace:workspace', version: 1, type: 'feedback-open-import', caseId: '1', generation: 1, importId });
  await h.ready('workspace-first-import-document'); h.navigation.sync();
  assert.equal(h.posts.filter(item => item.message.type === 'feedback-open-import').length, 1, 'ready replay never opens the same queued task twice');
  assert.deepEqual(h.calls.writes, []); assert.equal(h.calls.refreshCases, 0);
});

test('a pending import-open belongs to its original binding and cannot survive a case switch', async () => {
  const h = harness(), original = h.sandbox.caseContext();
  h.feedbackOptions.openImport('original-import-id', 'v1', original);
  await h.sandbox.openManagedCase(2, original);
  await h.frame.fire('load'); await h.ready('workspace-new-case-document');
  assert.equal(h.posts.some(item => item.message.type === 'feedback-open-import'), false);
  h.navigation.show('home');
  h.feedbackOptions.openImport('stale-import-id', 'v1', original);
  assert.equal(h.graph.inert, false, 'a stale task action cannot navigate the newer case to imports');
  h.feedbackOptions.openImport('current-import-id', 'v1', h.sandbox.caseContext());
  const openings = h.posts.filter(item => item.message.type === 'feedback-open-import');
  assert.equal(openings.length, 1); assert.equal(openings[0].message.importId, 'current-import-id');
  assert.equal(openings[0].message.caseId, '2'); assert.equal(openings[0].message.generation, 2);
});

test('frame reload detaches its feedback source and suppresses context until the next ready handshake', async () => {
  for (const [key, page] of [['workspace', 'cases'], ['analysis', 'analysis']]) {
    const h = harness(), target = key === 'workspace' ? h.frame : h.analysis;
    const oldId = key + '-old-feedback-document', newId = key + '-new-feedback-document';
    h.navigation.show(page); await h.ready(oldId, target);
    const task = feedbackMessage(h, oldId, { event: { id: 'reload-running', kind: 'task', operation: key === 'analysis' ? 'analysis' : 'import', status: 'running', title: '合成执行任务' } });
    await h.receive(task, { source: target.contentWindow }); assert.equal(h.calls.feedback.length, 1);
    assert.deepEqual(h.calls.feedbackDetached, []);
    await target.fire('load');
    assert.deepEqual(h.calls.feedbackDetached, [key + ':' + oldId]);
    const afterLoad = h.posts.length; h.navigation.sync();
    assert.equal(h.posts.slice(afterLoad).some(item => item.target === key && item.message.type === 'context'), false,
      'the parent cannot send a context into the frame before its replacement document is ready');
    await h.receive(task, { source: target.contentWindow }); assert.equal(h.calls.feedback.length, 1);
    await h.ready(newId, target);
    assert.ok(h.posts.slice(afterLoad).some(item => item.target === key && item.message.type === 'context'));
    assert.deepEqual(h.calls.feedbackDetached, [key + ':' + oldId]);
  }
});

test('replacing a ready instance detaches its predecessor once while duplicate ready preserves current tasks', async () => {
  for (const key of ['workspace', 'analysis']) {
    const h = harness(), target = key === 'workspace' ? h.frame : h.analysis;
    const first = key + '-ready-document-first', next = key + '-ready-document-next';
    await h.ready(first, target); await h.ready(first, target);
    assert.deepEqual(h.calls.feedbackDetached, []);
    await h.ready(next, target);
    assert.deepEqual(h.calls.feedbackDetached, [key + ':' + first]);
    await h.ready(next, target);
    assert.deepEqual(h.calls.feedbackDetached, [key + ':' + first], 'the hello response from the same document must not retire its live task display');
    const old = feedbackMessage(h, first, { event: { id: 'instance-task', kind: 'task', operation: key === 'analysis' ? 'analysis' : 'import', status: 'success', title: '合成完成' } });
    await h.receive(old, { source: target.contentWindow }); assert.equal(h.calls.feedback.length, 0);
    await h.receive({ ...old, instanceId: next }, { source: target.contentWindow });
    assert.equal(h.calls.feedback.length, 1); assert.equal(h.calls.feedback[0].target, key + ':' + next);
  }
});

test('an import-settled message follows successful lease release and carries only the exact owning case and task', async () => {
  const h = harness(), gate = deferred(), releases = [], context = h.sandbox.caseContext(), importId = '0123456789abcdef';
  await h.ready();
  h.sandbox.window.FundsImportWorkflow.releaseExternal = async (...args) => { releases.push(plain(args)); await gate.promise; };
  const settled = () => h.posts.filter(item => item.message.type === 'feedback-import-settled');
  const pending = h.feedbackOptions.release(context, importId); await flush();
  assert.deepEqual(releases, [[context, importId, importId]]); assert.equal(settled().length, 0);
  gate.resolve(); await pending;
  assert.deepEqual(settled(), [{ target: 'workspace', targetOrigin: 'http://synthetic.local', message: {
    channel: 'fundstrace:workspace', version: 1, type: 'feedback-import-settled', caseId: '1', generation: 1, importId,
  } }]);
  assert.deepEqual(h.calls.writes, []);
});

test('failed or unavailable lease reconciliation never tells the iframe that its import settled', async () => {
  for (const unavailable of [false, true]) {
    const h = harness(); await h.ready();
    if (!unavailable) h.sandbox.window.FundsImportWorkflow.releaseExternal = async () => { throw new Error('合成回执仍待核对'); };
    await assert.rejects(h.feedbackOptions.release(h.sandbox.caseContext(), '0123456789abcdef'), unavailable ? /尚未就绪/ : /仍待核对/);
    assert.equal(h.posts.some(item => item.message.type === 'feedback-import-settled'), false);
  }
});

test('a late lease-release success cannot settle another case or a new A-B-A generation', async () => {
  for (const returning of [false, true]) {
    const h = harness(), gate = deferred(), original = h.sandbox.caseContext(); await h.ready();
    h.sandbox.window.FundsImportWorkflow.releaseExternal = () => gate.promise;
    const pending = h.feedbackOptions.release(original, '0123456789abcdef');
    await h.sandbox.openManagedCase(2, original);
    if (returning) await h.sandbox.openManagedCase(1, h.sandbox.caseContext());
    gate.resolve(); await pending;
    assert.equal(h.posts.some(item => item.message.type === 'feedback-import-settled'), false);
  }
});

const graphMetric = (h, phase, values = {}) => h.emit('funds:graph-metric', { detail: { ...h.sandbox.caseContext(), phase, ...values } });

test('layout feedback starts only on real computation and completes once with its full case and canvas binding', async () => {
  const h = harness(); h.state.canvasId = 7; h.state.canvasGeneration = 4;
  for (const [phase, values] of [['layout-phase', { layoutPhase: 'idle' }], ['layout-phase', { layoutPhase: 'committing' }], ['request', {}], ['layout', { outcome: 'committed' }]]) {
    await graphMetric(h, phase, values);
  }
  assert.equal(h.calls.feedbackTasks.length, 0, 'idle and unrelated metrics cannot fabricate a layout task');
  await graphMetric(h, 'layout-phase', { layoutPhase: 'computing' });
  await graphMetric(h, 'layout-phase', { layoutPhase: 'committing' });
  await graphMetric(h, 'layout', { outcome: 'committed' });
  const tasks = h.calls.feedbackTasks;
  assert.deepEqual(tasks.map(task => task.status), ['running', 'running', 'success']);
  assert.equal(tasks.every(task => task.id === 'graph:layout' && task.announce === false), true);
  assert.deepEqual(plain(tasks[0].scope), { caseId: 1, generation: 1, canvasId: 7, canvasGeneration: 4 });
  assert.equal(tasks.every(task => JSON.stringify(task.scope) === JSON.stringify(tasks[0].scope)), true);
  assert.match(tasks[0].title, /计算/); assert.match(tasks[1].title, /应用/);
  await graphMetric(h, 'layout-phase', { layoutPhase: 'idle' });
  assert.equal(h.calls.feedbackTasks.length, 3, 'the post-completion idle signal cannot replace success with cancellation');
  assert.deepEqual(h.calls.writes, []);
});

test('retained geometry and a layout status message remain warnings instead of a successful completion', async () => {
  for (const outcome of ['retained', 'committed']) {
    const h = harness();
    await graphMetric(h, 'layout-phase', { layoutPhase: 'computing' });
    if (outcome === 'committed') h.element('graphLayoutStatus').textContent = '合成局部路径未能优化';
    await graphMetric(h, 'layout', { outcome });
    const task = h.calls.feedbackTasks.at(-1);
    assert.equal(task.status, 'warning'); assert.equal(task.announce, false);
    if (outcome === 'retained') assert.match(task.title, /保留原图/);
    else assert.equal(task.detail, '合成局部路径未能优化');
  }
});

test('layout stop and binding revocation cannot invent tasks or accept stale case and canvas metrics', async () => {
  const h = harness();
  for (const invalid of [{ caseId: 2 }, { generation: 0 }, { canvasId: 9 }, { canvasGeneration: 0 }]) {
    await graphMetric(h, 'layout-phase', { layoutPhase: 'computing', ...invalid });
  }
  assert.equal(h.calls.feedbackTasks.length, 0);
  await graphMetric(h, 'layout-phase', { layoutPhase: 'computing' });
  await graphMetric(h, 'layout-phase', { layoutPhase: 'idle' });
  assert.deepEqual(h.calls.feedbackTasks.map(task => task.status), ['running', 'cancelled']);
  await graphMetric(h, 'layout-phase', { layoutPhase: 'idle' });
  assert.equal(h.calls.feedbackTasks.length, 2);
  await graphMetric(h, 'layout-phase', { layoutPhase: 'computing' });
  const previous = h.sandbox.caseContext();
  h.state.canvasId = 8; h.state.canvasGeneration++; await h.emit('funds:feedback-binding');
  await graphMetric(h, 'layout', { outcome: 'committed', ...previous });
  await graphMetric(h, 'layout-phase', { layoutPhase: 'idle' });
  assert.equal(h.calls.feedbackTasks.length, 3, 'a changed canvas cannot finish or cancel its predecessor task');
  await graphMetric(h, 'layout-phase', { layoutPhase: 'computing' });
  await h.sandbox.openManagedCase(2, h.sandbox.caseContext());
  await graphMetric(h, 'layout-phase', { layoutPhase: 'idle' });
  assert.equal(h.calls.feedbackTasks.length, 4, 'case rebinding also clears the current layout task identity');
});

test('the layout task return action reveals the canvas while preserving its camera transform', async () => {
  const h = harness(), transform = { scale: 1.75, x: 83, y: -42 };
  const camera = { transform: { ...transform }, resize() { h.calls.camera++; }, fit() { this.transform = { scale: 1, x: 0, y: 0 }; } };
  h.state.graphCamera = camera;
  await graphMetric(h, 'layout-phase', { layoutPhase: 'computing' });
  const task = h.calls.feedbackTasks.at(-1);
  h.navigation.show('cases'); const resizes = h.calls.camera;
  await task.actions[0].run(); await flush();
  assert.equal(h.graph.inert, false); assert.equal(h.frame.hidden, true);
  assert.equal(h.state.graphCamera, camera); assert.deepEqual(camera.transform, transform);
  assert.equal(h.calls.camera, resizes + 1, 'returning lets the existing camera resize without fitting or resetting it');
  assert.deepEqual(h.calls.writes, []); assert.deepEqual(h.calls.loads, []);
});
