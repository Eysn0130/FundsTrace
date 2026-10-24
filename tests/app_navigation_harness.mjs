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
  const callbacks = new Map(), elements = new Map(), posts = [], calls = { refreshCases: 0, open: [], capture: 0, refresh: 0, camera: 0, dialog: [], click: [], loads: [], withWrites: 0, writes: [], canvasLists: 0 };
  const origin = 'http://synthetic.local', state = { caseId: 1, caseGeneration: 1, canvasId: 0, canvasGeneration: 1,
    graphLoading: false, graphView: { exportDepth: 0 },
    cases: [{ id: 1, name: 'Synthetic A' }, { id: 2, name: 'Synthetic B' }], graphFacts: 'initial',
    graphCamera: { resize() { calls.camera++; } } };
  let caseRead = async () => state.cases, caseOpen = async () => true, freshRead = async () => { state.graphFacts = 'fresh'; }, srcWrites = 0;
  function element(id) {
    if (elements.has(id)) return elements.get(id);
    const attributes = new Map(), classes = new Set();
    const node = { id, inert: false, hidden: false, dataset: {},
      classList: { toggle(name, on) { if (on) classes.add(name); else classes.delete(name); }, contains: name => classes.has(name) },
      setAttribute(name, value) { attributes.set(name, String(value)); },
      getAttribute: name => attributes.get(name) ?? null, removeAttribute(name) { attributes.delete(name); },
      addEventListener() {},
      click() { calls.click.push(id); this.onclick?.(); }, showModal() { calls.dialog.push(id); }, close() {},
    };
    elements.set(id, node); return node;
  }
  const frame = element('casesFrame'), graph = element('graphPage');
  Object.defineProperty(frame, 'src', { set(value) { srcWrites++; frame.setAttribute('src', value); }, get: () => frame.getAttribute('src') });
  frame.contentWindow = { postMessage(message, targetOrigin) { posts.push({ message: plain(message), targetOrigin }); } };
  const nav = ['home', 'cases'].map(page => { const node = element('nav-' + page); node.dataset.navPage = page; return node; });
  const document = {
    querySelector(selector) {
      if (selector.startsWith('#')) return element(selector.slice(1));
      if (selector === '[data-nav-page="cases"]') return nav[1];
      throw new Error(`Unexpected selector ${selector}`);
    },
    querySelectorAll: selector => selector === '[data-nav-page]' ? nav : [],
  };
  const location = { origin, href: origin + '/' };
  const sandbox = { state, document, location, URL, requestAnimationFrame: callback => callback(),
    history: { replaceState(_state, _unused, url) { location.href = String(url); } },
    window: { addEventListener(type, callback) { const list = callbacks.get(type) || []; list.push(callback); callbacks.set(type, list); },
      FundsWorkspaceSession: { capture() { calls.capture++; }, retainedCaseIds: () => ['1'] } },
    caseContext: () => ({ caseId: state.caseId, generation: state.caseGeneration, canvasId: state.canvasId, canvasGeneration: state.canvasGeneration }),
    async refreshManagedCases() { calls.refreshCases++; return caseRead(); },
    async openManagedCase(id, context) {
      calls.open.push({ id, context: plain(context) }); state.caseId = id; state.caseGeneration++; return caseOpen(id, context);
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
  return { calls, posts, state, frame, graph, nav, request, receive, element, sandbox,
    navigation: sandbox.window.FundsAppNavigation, srcWrites: () => srcWrites,
    ready: () => receive({ channel: 'fundstrace:workspace', version: 1, type: 'ready' }),
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
