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
  const callbacks = new Map(), elements = new Map(), posts = [], calls = { refreshCases: 0, open: [], capture: 0, refresh: 0, camera: 0, dialog: [], click: [] };
  const origin = 'http://synthetic.local', state = { caseId: 1, caseGeneration: 1,
    cases: [{ id: 1, name: 'Synthetic A' }, { id: 2, name: 'Synthetic B' }], graphFacts: 'initial',
    graphCamera: { resize() { calls.camera++; } } };
  let caseRead = async () => state.cases, freshRead = async () => { state.graphFacts = 'fresh'; }, srcWrites = 0;
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
    caseContext: () => ({ caseId: state.caseId, generation: state.caseGeneration, canvasId: 0, canvasGeneration: 1 }),
    async refreshManagedCases() { calls.refreshCases++; return caseRead(); },
    async openManagedCase(id, context) {
      calls.open.push({ id, context: plain(context) }); state.caseId = id; state.caseGeneration++; return true;
    },
    async refreshAll() { calls.refresh++; await freshRead(); },
    caseWorkspace: { open(trigger) { calls.dialog.push(trigger.id); } },
    scheduleGraphViewport() {}, scheduleGraphLabels() {}, toast() {},
  };
  vm.runInNewContext(source, sandbox, { filename: 'app-navigation.js' });
  for (const ready of callbacks.get('DOMContentLoaded') || []) ready();
  const request = (overrides = {}) => ({ channel: 'fundstrace:workspace', version: 1, type: 'request',
    requestId: 'request-1', caseId: String(state.caseId), generation: state.caseGeneration,
    action: 'select-case', payload: { caseId: '2' }, ...overrides });
  async function receive(message, overrides = {}) {
    const event = { origin, source: frame.contentWindow, data: message, ...overrides };
    await Promise.all((callbacks.get('message') || []).map(callback => callback(event)));
  }
  return { calls, posts, state, frame, graph, nav, request, receive, element,
    navigation: sandbox.window.FundsAppNavigation, srcWrites: () => srcWrites,
    ready: () => receive({ channel: 'fundstrace:workspace', version: 1, type: 'ready' }),
    caseRead(fn) { caseRead = fn; }, freshRead(fn) { freshRead = fn; },
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
