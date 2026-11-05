import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import { Blob, File } from 'node:buffer';
import { EventEmitter } from 'node:events';
import vm from 'node:vm';
import test from 'node:test';

const accountSource = readFileSync(new URL('../static/account-workspace.js', import.meta.url), 'utf8');
const navigationSource = readFileSync(new URL('../static/app-navigation.js', import.meta.url), 'utf8');
const appSource = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
const error = (status, message = 'synthetic transport failure') => Object.assign(new Error(message), { status });
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const identity = '110101199001010011';
const crop = { cx: 0, cy: 0, r: .05 };
const photo = (content = 'synthetic-image-a', name = 'synthetic.png') => new File([content], name, { type: 'image/png' });

function factoryHarness() {
  const events = new EventEmitter(), leases = new Set(), requests = [], blobs = [], published = [], contexts = { caseId: 1, generation: 1 };
  const facts = new Map([['1:SYN-A', { case_id: '1', account: 'SYN-A', id_no: '', photo: null, is_main: false, main_source: null }]]);
  const originals = new Map();
  let writeOverride, readOverride, blobOverride, workflow = async () => true, reason = () => '', main, stateChanges = 0;
  const root = { Blob, File, FormData, crypto: webcrypto,
    addEventListener: (name, listener) => events.on(name, listener), removeEventListener: (name, listener) => events.off(name, listener) };
  const context = vm.createContext({ window: root, URLSearchParams, Uint8Array, Set, Map });
  vm.runInContext(accountSource, context, { filename: 'account-workspace.js' });
  const current = value => value.caseId === contexts.caseId && value.generation === contexts.generation;
  const key = (caseId, account) => `${caseId}:${account}`;
  const read = async path => {
    if (readOverride) { const overridden = await readOverride(path); if (overridden !== undefined) return overridden; }
    if (path === '/api/cases') return [{ id: 1 }, { id: 2 }];
    const parsed = new URL(path, 'http://synthetic.local');
    if (parsed.pathname !== '/api/account-registry/profile') throw new Error(`Unexpected read ${path}`);
    const value = facts.get(key(parsed.searchParams.get('case_id'), parsed.searchParams.get('account')));
    if (!value) throw error(404, 'synthetic account not found');
    return plain(value);
  };
  const write = async (path, options) => {
    const body = options.body instanceof FormData ? options.body : JSON.parse(options.body);
    const caseId = body instanceof FormData ? body.get('case_id') : body.case_id;
    const account = body instanceof FormData ? body.get('account') : body.account;
    const fact = facts.get(key(caseId, account));
    if (!fact) throw new Error('write attempted without known account');
    if (path === '/api/account-ids/set') {
      fact.id_no = body.id_no;
      return body.id_no ? { ok: true, account, id_no: body.id_no } : { ok: true, account, cleared: true };
    }
    if (path === '/api/account-photos/clear') { fact.photo = null; originals.delete(key(caseId, account)); return { ok: true, cleared: true }; }
    if (path === '/api/account-photos/set') {
      fact.photo = Object.fromEntries(['cx', 'cy', 'r'].map(name => ['crop_' + name, Number(body.get('crop_' + name))]));
      originals.set(key(caseId, account), body.get('file')); return { ok: true, account };
    }
    throw new Error(`Unexpected write ${path}`);
  };
  const request = async (path, options = {}) => {
    requests.push({ path, options });
    if (!options.method || options.method === 'GET') return read(path);
    return writeOverride ? writeOverride(path, options, () => write(path, options)) : write(path, options);
  };
  const blob = async path => {
    blobs.push(path);
    if (blobOverride) return blobOverride(path);
    const parsed = new URL(path, 'http://synthetic.local');
    const value = originals.get(key(parsed.searchParams.get('case_id'), parsed.searchParams.get('account')));
    if (!value) throw error(404); return value;
  };
  const options = { isCurrent: current, request, blob, leases, canWrite: value => workflow(value), reason: value => reason(value),
    main: () => main, trigger: () => ({ id: 'navAccounts' }), onState: () => { stateChanges++; },
    publish: async (binding, kind) => published.push({ binding: plain(binding), kind }) };
  const actions = root.FundsAccountWorkspace.create(options);
  return { actions, options, root, events, leases, requests, blobs, published, facts, originals, current,
    context: () => ({ ...contexts }), writes: () => requests.filter(item => item.options.method === 'POST'),
    setWrite(fn) { writeOverride = fn; }, setRead(fn) { readOverride = fn; }, setBlob(fn) { blobOverride = fn; },
    canWrite(fn) { workflow = fn; }, reason(fn) { reason = fn; }, main(value) { main = value; },
    switchCase(id) { contexts.caseId = id; contexts.generation++; }, changes: () => stateChanges,
  };
}
const identityMutation = () => ({ kind: 'identity-set', account: 'SYN-A', idNo: identity });

test('identity and photo writes use the bound case/account, submit once and release their shared lease', async () => {
  const h = factoryHarness(), gate = deferred(); h.setWrite((_path, _options, commit) => gate.promise.then(commit));
  const operation = h.actions.mutate({ ...identityMutation(), case_id: '2' }, h.context()); await tick();
  assert.equal(h.writes().length, 1); assert.equal(h.leases.size, 1);
  await assert.rejects(h.actions.mutate(identityMutation(), h.context()), /尚未确认/);
  gate.resolve(); await operation;
  const body = JSON.parse(h.writes()[0].options.body);
  assert.deepEqual(body, { case_id: 1, account: 'SYN-A', id_no: identity });
  assert.equal(h.leases.size, 0); assert.equal(h.actions.blocked(h.context()), false); assert.equal(h.published.length, 1);
  h.setWrite(undefined);
  await h.actions.mutate({ kind: 'photo-set', account: 'SYN-A', file: photo(), crop }, h.context());
  const form = h.writes()[1].options.body;
  assert.equal(form.get('case_id'), '1'); assert.equal(form.get('account'), 'SYN-A');
  assert.equal(form.get('crop_cx'), '0'); assert.equal(form.get('crop_cy'), '0'); assert.equal(form.get('crop_r'), '0.05');
  assert.equal(form.get('file').name, 'synthetic.png'); assert.equal(h.leases.size, 0);
});

test('sent network/408/5xx/bad receipts retain an unknown lease; explicit 4xx reject without a lock', async () => {
  for (const outcome of ['network', 408, 500, 503, 'bad-receipt', 'wrong-account', 400, 404, 409, 422]) {
    const h = factoryHarness();
    h.setWrite(async (_path, _options, commit) => {
      if (typeof outcome === 'number') throw error(outcome);
      if (outcome === 'network') { await commit(); throw new Error('synthetic dropped reply'); }
      if (outcome === 'wrong-account') return { ok: true, account: 'SYN-OTHER', id_no: identity };
      return { ok: true, account: 'SYN-A', id_no: 'incorrect' };
    });
    const uncertain = typeof outcome !== 'number' || outcome === 408 || outcome >= 500;
    await assert.rejects(h.actions.mutate(identityMutation(), h.context()), caught => uncertain ? caught.code === 'write-unknown' : caught.status === outcome);
    assert.equal(h.writes().length, 1, String(outcome)); assert.equal(h.leases.size, uncertain ? 1 : 0, String(outcome));
    assert.equal(h.actions.operations(h.context()).length, uncertain ? 1 : 0, String(outcome));
    if (uncertain) await assert.rejects(h.actions.mutate(identityMutation(), h.context()), /尚未确认/);
    assert.equal(h.writes().length, 1);
  }
});

test('unknown identity reconciliation is read-only, case bound and reports mismatches without retrying', async () => {
  for (const matching of [true, false]) {
    const h = factoryHarness();
    h.setWrite(async (_path, _options, commit) => { if (matching) await commit(); throw new Error('lost'); });
    await assert.rejects(h.actions.mutate(identityMutation(), h.context()), { code: 'write-unknown' });
    const operation = h.actions.operations(h.context())[0], original = h.context();
    await assert.rejects(h.actions.reconcile(operation.operationId, { caseId: 2, generation: 1 }), /已变化/);
    assert.equal(h.leases.size, 1);
    if (matching) await h.actions.reconcile(operation.operationId, original);
    else await assert.rejects(h.actions.reconcile(operation.operationId, original), { code: 'write-mismatch' });
    assert.equal(h.writes().length, 1); assert.equal(h.leases.size, 0); assert.equal(h.actions.operations(original).length, 0);
    await assert.rejects(h.actions.reconcile(operation.operationId, original), /已变化/);
  }
});

test('known-account and binding reads complete before writes, including 404, corrupt profiles and A-B-A', async () => {
  for (const value of [null, { case_id: '2', account: 'SYN-A' }, { case_id: '1', account: 'SYN-OTHER' }]) {
    const h = factoryHarness(); h.setRead(() => { if (!value) throw error(404); return value; });
    await assert.rejects(h.actions.mutate(identityMutation(), h.context()));
    assert.equal(h.writes().length, 0); assert.equal(h.leases.size, 0);
  }
  const h = factoryHarness(), gate = deferred(), binding = h.context(); h.setRead(() => gate.promise);
  const pending = h.actions.mutate(identityMutation(), binding); await tick();
  h.switchCase(2); h.switchCase(1); gate.resolve({ case_id: '1', account: 'SYN-A' });
  await assert.rejects(pending, /案件已变化/); assert.equal(h.writes().length, 0); assert.equal(h.leases.size, 0);
});

test('a removed final identity record reconciles only while its case still exists', async () => {
  for (const caseExists of [true, false]) {
    const h = factoryHarness(); h.setWrite(async () => { h.facts.delete('1:SYN-A'); throw new Error('lost clear'); });
    await assert.rejects(h.actions.mutate({ ...identityMutation(), idNo: '' }, h.context()), { code: 'write-unknown' });
    h.setRead(path => path === '/api/cases' ? caseExists ? [{ id: 1 }] : [{ id: 2 }] : undefined);
    const id = h.actions.operations(h.context())[0].operationId;
    if (caseExists) { await h.actions.reconcile(id, h.context()); assert.equal(h.leases.size, 0); }
    else { await assert.rejects(h.actions.reconcile(id, h.context()), { status: 404 }); assert.equal(h.leases.size, 1); }
    assert.equal(h.writes().length, 1);
  }
});

test('unknown leases survive A-B-A and authoritative reconciliation rebinds without repeating a clear', async () => {
  const h = factoryHarness(); h.facts.get('1:SYN-A').photo = { crop_cx: .5, crop_cy: .5, crop_r: .4 };
  h.setWrite(async (_path, _options, commit) => { await commit(); throw new Error('lost clear reply'); });
  const old = h.context();
  await assert.rejects(h.actions.mutate({ kind: 'photo-clear', account: 'SYN-A' }, old), { code: 'write-unknown' });
  const operation = h.actions.operations(old)[0];
  h.switchCase(2); assert.equal(h.actions.operations(h.context()).length, 0); assert.equal(h.leases.size, 1);
  await assert.rejects(h.actions.reconcile(operation.operationId, h.context()), /已变化/);
  h.switchCase(1); assert.equal(h.actions.operations(h.context()).length, 1); assert.equal(h.actions.blocked(h.context()), true);
  await assert.rejects(h.actions.reconcile(operation.operationId, old), /已变化/);
  await h.actions.reconcile(operation.operationId, h.context());
  assert.equal(h.writes().length, 1); assert.equal(h.leases.size, 0);
});

test('photo uncertainty checks both exact original bytes and crop, retaining zero crop centers', async () => {
  for (const mismatch of ['none', 'bytes', 'crop']) {
    const h = factoryHarness(); h.setWrite(async (_path, _options, commit) => { await commit(); throw new Error('lost photo receipt'); });
    await assert.rejects(h.actions.mutate({ kind: 'photo-set', account: 'SYN-A', file: photo(), crop }, h.context()), { code: 'write-unknown' });
    if (mismatch === 'bytes') h.originals.set('1:SYN-A', photo('synthetic-image-b'));
    if (mismatch === 'crop') h.facts.get('1:SYN-A').photo.crop_cx = .1;
    const operation = h.actions.operations(h.context())[0];
    if (mismatch === 'none') await h.actions.reconcile(operation.operationId, h.context());
    else await assert.rejects(h.actions.reconcile(operation.operationId, h.context()), { code: 'write-mismatch' });
    assert.equal(h.writes().length, 1); assert.equal(h.leases.size, 0);
  }
  const h = factoryHarness(); h.facts.get('1:SYN-A').photo = { crop_cx: .5, crop_cy: .5, crop_r: .4 }; h.originals.set('1:SYN-A', photo());
  await h.actions.mutate({ kind: 'photo-crop', account: 'SYN-A', crop }, h.context());
  assert.equal(h.blobs.length, 1); assert.equal(h.writes().length, 1);
  assert.equal(h.writes()[0].options.body.get('crop_cx'), '0'); assert.equal(h.writes()[0].options.body.get('crop_cy'), '0');
  assert.equal(await h.writes()[0].options.body.get('file').text(), 'synthetic-image-a');
});

test('invalid photo sizes, types, crops and identity formats fail before reads or writes', async () => {
  const h = factoryHarness();
  for (const mutation of [
    { kind: 'photo-set', account: 'SYN-A', file: new File([new Uint8Array(8 * 1024 * 1024 + 1)], 'large.png'), crop },
    { kind: 'photo-set', account: 'SYN-A', file: photo('bytes', 'unapproved.svg'), crop },
    { kind: 'photo-set', account: 'SYN-A', file: photo(), crop: { ...crop, r: 0 } },
    { kind: 'photo-set', account: 'SYN-A', file: photo(), crop: { ...crop, cx: NaN } },
    { ...identityMutation(), idNo: 'not-an-identity' },
  ]) await assert.rejects(h.actions.mutate(mutation, h.context()));
  assert.equal(h.requests.length, 0); assert.equal(h.leases.size, 0);
});

function mainMock(h, { manualUnknown = false } = {}) {
  const state = { visible: false, phase: 'idle', pending: false, unknown: false, rows: [], candidates: [], error: '' };
  const calls = { prepare: [], submit: 0, writes: 0, close: 0, acknowledge: 0 };
  const emit = () => h.events.emit('funds:main-accounts-state');
  const fact = () => h.facts.get('1:SYN-A');
  const actions = { status: () => ({ ...state }), open() { state.visible = true; return true; },
    async refresh() { state.rows = fact().is_main ? [{ account: 'SYN-A', source: fact().main_source }] : []; return true; },
    showSearch: () => true, async search(account, page, exact) { assert.equal(account, 'SYN-A'); assert.equal(page, 1); assert.equal(exact, true); state.candidates = [{ account: 'SYN-A', source: null }]; return true; },
    prepare(action, row) {
      calls.prepare.push({ action, account: row.account });
      if (action === 'manual') {
        calls.writes++; fact().is_main = true; fact().main_source = 'manual'; state.phase = manualUnknown ? 'unknown' : 'done'; state.unknown = manualUnknown; emit(); return Promise.resolve(true);
      }
      state.phase = 'confirm'; emit(); return true;
    },
    submit() { calls.submit++; throw new Error('adapter must not submit after prepare manual'); },
    close() { calls.close++; state.visible = false; return true; },
    async reconcile() { return []; }, acknowledge() { calls.acknowledge++; state.unknown = false; state.phase = 'idle'; return true; },
    accept() { calls.writes++; state.pending = true; state.phase = 'working'; emit(); fact().is_main = false; fact().main_source = null; state.pending = false; state.phase = 'done'; emit(); },
    cancel() { state.phase = 'idle'; state.visible = false; emit(); },
  };
  h.main(actions); return { actions, calls, state };
}

test('main manual preparation submits only through the established controller, and unknown results stay blocked', async () => {
  for (const uncertain of [false, true]) {
    const h = factoryHarness(), main = mainMock(h, { manualUnknown: uncertain });
    const pending = h.actions.mutate({ kind: 'main-set', account: 'SYN-A', on: true }, h.context());
    if (uncertain) {
      await assert.rejects(pending, { code: 'write-unknown' }); assert.equal(h.actions.blocked(h.context()), true);
      const id = h.actions.operations(h.context())[0].operationId; await h.actions.reconcile(id, h.context()); assert.equal(main.calls.acknowledge, 1);
    } else await pending;
    assert.equal(main.calls.writes, 1); assert.equal(main.calls.submit, 0); assert.equal(h.writes().length, 0);
    assert.deepEqual(main.calls.prepare, [{ action: 'manual', account: 'SYN-A' }]); assert.equal(h.actions.blocked(h.context()), false);
  }
});

test('main removal waits for the existing confirmation and cancellation never writes', async () => {
  for (const accept of [true, false]) {
    const h = factoryHarness(), fact = h.facts.get('1:SYN-A'); fact.is_main = true; fact.main_source = 'manual'; const main = mainMock(h);
    let settled = false; const pending = h.actions.mutate({ kind: 'main-set', account: 'SYN-A', on: false }, h.context()).finally(() => { settled = true; });
    await tick(); assert.equal(main.state.phase, 'confirm'); assert.equal(settled, false); assert.equal(main.calls.writes, 0);
    if (accept) { main.actions.accept(); await pending; assert.equal(main.calls.writes, 1); }
    else { main.actions.cancel(); await assert.rejects(pending, { code: 'cancelled' }); assert.equal(main.calls.writes, 0); }
    assert.equal(main.calls.submit, 0); assert.equal(h.actions.blocked(h.context()), false);
  }
});

function navigationHarness() {
  const h = factoryHarness(), callbacks = new Map(), posts = [], elements = new Map(); let srcWrites = 0;
  const state = { caseId: 1, caseGeneration: 1, cases: [{ id: 1, name: 'Synthetic A' }], graphView: { exportDepth: 0 }, graphCamera: { resize() {} } };
  function element(id) {
    if (elements.has(id)) return elements.get(id);
    const attributes = new Map(), classes = new Set(), listeners = new Map();
    const value = { id, dataset: {}, value: '', hidden: false, inert: false,
      classList: { toggle(name, on) { if (on) classes.add(name); else classes.delete(name); } },
      setAttribute: (name, text) => attributes.set(name, String(text)), getAttribute: name => attributes.get(name) ?? null, removeAttribute: name => attributes.delete(name),
      addEventListener: (name, handler) => listeners.set(name, handler), showModal() {}, close() {}, click() { this.onclick?.(); } };
    elements.set(id, value); return value;
  }
  const frame = element('casesFrame'); frame.contentWindow = { postMessage(message, target) { posts.push({ message: plain(message), target }); } };
  Object.defineProperty(frame, 'src', { set(value) { srcWrites++; frame.setAttribute('src', value); }, get: () => frame.getAttribute('src') });
  const nav = ['home', 'cases', 'accounts'].map(page => { const node = element('nav-' + page); node.dataset.navPage = page; return node; });
  const root = h.root; root.addEventListener = (name, handler) => { const list = callbacks.get(name) || []; list.push(handler); callbacks.set(name, list); };
  root.dispatchEvent = event => { for (const callback of callbacks.get(event.type) || []) callback(event); return true; };
  root.FundsWorkspaceSession = { capture() {}, retainedCaseIds: () => ['1'] };
  const document = { querySelector: selector => selector.startsWith('#') ? element(selector.slice(1)) : nav.find(item => selector.includes(`"${item.dataset.navPage}"`)),
    querySelectorAll: selector => selector === '[data-nav-page]' ? nav : [] };
  const location = { origin: 'http://synthetic.local', href: 'http://synthetic.local/' };
  const context = vm.createContext({ window: root, document, state, location, URL, URLSearchParams, accountDataWrites: h.leases,
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options?.detail; } },
    caseContext: h.context, isCurrentCase: h.current, caseRequest: h.options.request, hasNodeDrafts: () => false, syncNodeWorkspaceControls() {},
    caseWorkspace: { status: () => ({}), open() {} }, exportWorkspace: null, batchView: {}, nodeWrites: new Map(), pcState: {}, nodeConfirmation: null, nodeWorkspace: null, layoutJob: null, cancelAnnotationDrag: null,
    loadGraph: async () => {}, history: { replaceState(_state, _unused, url) { location.href = String(url); } }, requestAnimationFrame: callback => callback(),
    scheduleGraphViewport() {}, scheduleGraphLabels() {}, refreshAll: async () => {}, toast() {}, doSearch: async () => {},
  });
  vm.runInContext(navigationSource, context, { filename: 'app-navigation.js' });
  for (const callback of callbacks.get('DOMContentLoaded') || []) callback();
  const request = (requestId, payload, overrides = {}) => ({ channel: 'fundstrace:workspace', version: 1, type: 'request', requestId,
    caseId: String(state.caseId), generation: state.caseGeneration, action: 'account-action', payload, ...overrides });
  const receive = async (message, overrides = {}) => { for (const callback of callbacks.get('message') || []) await callback({ origin: location.origin, source: frame.contentWindow, data: message, ...overrides }); };
  return { ...h, actions: root.FundsAccountActions, navigation: root.FundsAppNavigation, state, frame, posts, request, receive, srcWrites: () => srcWrites,
    ready: () => receive({ channel: 'fundstrace:workspace', version: 1, type: 'ready' }) };
}

test('accounts/cases share a stable surface; parent requests enforce origin, owner, generation and write-once IDs', async () => {
  const h = navigationHarness(); await h.ready(); h.navigation.show('accounts');
  assert.match(h.frame.src, /surface=accounts/); assert.equal(h.frame.title, '账户与身份');
  h.navigation.show('cases'); h.navigation.show('accounts'); assert.equal(h.srcWrites(), 1);
  const payload = { action: 'mutate', mutation: identityMutation() }, gate = deferred(); h.setWrite((_path, _options, commit) => gate.promise.then(commit));
  for (const [id, message, event] of [
    ['origin', {}, { origin: 'https://untrusted.invalid' }], ['owner', {}, { source: {} }], ['generation', { generation: 0 }, {}],
    ['case', { caseId: '2' }, {}], ['channel', { channel: 'other' }, {}], ['version', { version: 2 }, {}],
  ]) await h.receive(h.request(id, payload, message), event);
  assert.equal(h.writes().length, 0);
  const message = h.request('one-write', payload), pending = h.receive(message); await tick();
  assert.equal(h.writes().length, 1); await h.receive(message); assert.equal(h.writes().length, 1);
  h.navigation.show('home'); h.navigation.show('accounts');
  gate.resolve(); await pending; await h.receive(message); assert.equal(h.writes().length, 1);
  assert.ok(h.posts.every(item => item.target === 'http://synthetic.local'));
});

test('closing/reopening accounts preserves parent unknown operations and never replays the write', async () => {
  const h = navigationHarness(); await h.ready(); h.navigation.show('accounts');
  h.setWrite(async (_path, _options, commit) => { await commit(); throw new Error('lost reply'); });
  const message = h.request('uncertain-write', { action: 'mutate', mutation: identityMutation() }); await h.receive(message);
  const result = h.posts.find(item => item.message.requestId === 'uncertain-write')?.message;
  assert.equal(result.code, 'write-unknown'); assert.equal(h.leases.size, 1);
  h.navigation.show('home'); h.navigation.show('accounts');
  assert.equal(h.posts.at(-1).message.unresolvedOperations[0].operationId, result.operationId);
  await h.receive(message); await h.receive(h.request('new-retry', { action: 'mutate', mutation: identityMutation() }));
  assert.equal(h.writes().length, 1); assert.equal(h.leases.size, 1);
  await h.receive(h.request('read-current', { action: 'reconcile', operationId: result.operationId }));
  assert.equal(h.writes().length, 1); assert.equal(h.leases.size, 0);
});

function section(start, end) {
  const from = appSource.indexOf(start), to = appSource.indexOf(end, from);
  assert.ok(from >= 0 && to > from, `missing source section ${start}`); return appSource.slice(from, to);
}

test('legacy drawer writes, crop submit, main tools and case destructive actions honor the parent unknown lock', async () => {
  const h = factoryHarness(); h.setWrite(async () => { throw new Error('lost'); });
  await assert.rejects(h.actions.mutate(identityMutation(), h.context()), { code: 'write-unknown' });
  const nodes = new Map(), calls = { fetch: 0, select: 0 }; let mainOptions, toolOptions;
  const element = id => {
    if (!nodes.has(id)) nodes.set(id, { id, style: {}, dataset: {}, addEventListener() {}, setAttribute() {}, querySelectorAll: () => [], close() {},
      classList: { contains: () => true, remove() {}, add() {} } });
    return nodes.get(id);
  };
  const state = { caseId: 1, caseGeneration: 1, graphView: { revision: 1, exportDepth: 0 }, cases: [], graph: {} };
  const pcState = { binding: { context: h.context(), key: 'SYN-KEY', view: state.graphView, revision: 1 }, account: 'SYN-A', file: photo() };
  const root = { FundsAccountActions: h.actions, FundsMainAccountsActions: { blocked: () => false }, FundsDataToolsActions: { blocked: () => false },
    addEventListener() {},
    FundsCaseWorkspace: { create: options => options },
    FundsMainAccountsView: { create: () => ({ attach() {}, onState() {}, show() {}, dismiss() {} }) },
    FundsMainAccounts: { create: options => { mainOptions = options; return {}; } },
    FundsDataTools: { createView: () => ({ attach() {}, onState() {}, show() {}, dismiss() {} }), create: options => { toolOptions = options; return {}; } } };
  const document = { getElementById: element, addEventListener() {} };
  const context = vm.createContext({ window: root, document, state, pcState, nodeWrites: new Map(), accountDataWrites: h.leases, nodeWorkspace: null,
    nodeConfirmation: null, nodeDrafts: new Map(), caseWorkspace: { status: () => ({}) }, exportWorkspace: null, batchView: {}, layoutJob: null,
    $: selector => element(selector.slice(1)), caseContext: h.context, isCurrentCase: h.current, hasNodeDrafts: () => false,
    fetch: async () => { calls.fetch++; return { ok: true, json: async () => ({}) }; },
    selectCase: () => { calls.select++; }, toast() {}, refreshManagedCases() {}, caseRequest() {}, downloadBlob() {}, refreshMainAccountRoles() {},
    refreshAccountIdentity() {}, syncNodeWorkspaceControls() {}, nodeWorkspaceIsCurrent: () => true, FormData, jsonOpts: body => ({ body: JSON.stringify(body) }),
    closeNodeMenu() {}, dataToolsWorkspace: null, pcLayout() {}, revokePhotoCrop() {},
  });
  vm.runInContext(section('async function nodeWorkspaceRequest(', 'function confirmNodeAction('), context);
  await assert.rejects(vm.runInContext("nodeWorkspaceRequest('/api/account-ids/set',{method:'POST',body:JSON.stringify({case_id:1,account:'SYN-A',id_no:'new'})})", context), /尚未确认/);
  vm.runInContext(section('async function writeNodeWorkspace(', 'function nodeRelatedIds('), context);
  context.currentWorkspace = { context: h.context() }; await vm.runInContext("writeNodeWorkspace(currentWorkspace,'id')", context);
  vm.runInContext(section('function bindPhotoCrop()', 'function edgeWorkspaceIsCurrent('), context); vm.runInContext('bindPhotoCrop()', context);
  await element('pcConfirm').onclick(); assert.equal(calls.fetch, 0);
  vm.runInContext(section('async function openManagedCase(', 'function installCaseWorkspace('), context);
  assert.equal(await vm.runInContext('openManagedCase(2,caseContext())', context), false); assert.equal(calls.select, 0);
  vm.runInContext(section('function installCaseWorkspace()', 'async function refreshAccountIdentity('), context);
  context.caseOptions = vm.runInContext('installCaseWorkspace()', context);
  await assert.rejects(async () => context.caseOptions.deleteCase(1), /尚未确认/);
  await assert.rejects(context.caseOptions.exportCase(1), /尚未确认/);
  vm.runInContext(section('function externalImportPending(', 'function bindCanvases()'), context);
  vm.runInContext(section('function installMainAccounts()', 'async function manageNodeMainAccount('), context); vm.runInContext('installMainAccounts()', context);
  assert.equal(mainOptions.ready('open', h.context()), false);
  vm.runInContext(section('function installDataTools()', 'function bindWorkspaceCamera('), context); vm.runInContext('installDataTools()', context);
  assert.equal(toolOptions.ready('import', h.context()), false);
  assert.equal(h.leases.size, 1); assert.equal(h.writes().length, 1);
});

test('parent unknown lock also blocks graph exports, canvas commands and the direct canvas selector', async () => {
  const h = factoryHarness(); h.setWrite(async () => { throw new Error('lost'); });
  await assert.rejects(h.actions.mutate(identityMutation(), h.context()), { code: 'write-unknown' });
  const nodes = new Map(), element = id => {
    if (!nodes.has(id)) nodes.set(id, { id, dataset: {}, style: {}, value: '', checked: false, selectedOptions: [],
      setAttribute() {}, removeAttribute() {}, toggleAttribute() {}, addEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
      showModal() { this.open = true; }, close() { this.open = false; } });
    return nodes.get(id);
  };
  const state = { caseId: 1, canvasId: 1, graphView: { exportDepth: 0 }, graphSelection: { status: () => ({}) }, search: { page: 1 } };
  const binding = () => ({ ...h.context(), canvasId: state.canvasId, canvasGeneration: 1 });
  let exports = 0, canvasOptions;
  const root = { FundsAccountActions: h.actions, FundsMainAccountsActions: { blocked: () => false }, addEventListener() {},
    FundsCanvasWorkspace: { createView: () => ({ attach() {}, onState() {} }), create: options => {
      canvasOptions = options; return { sync() {}, status: () => ({ pending: false }), blocked: () => false };
    } } };
  const context = vm.createContext({ window: root, state, document: { addEventListener() {} }, $: selector => element(selector.slice(1)),
    fmtCount: String, caseContext: binding, isCurrentCase: () => true, layoutJob: null, hasNodeDrafts: () => false,
    caseRequest: async () => ({}), toast() {}, activateCanvas(id) { state.canvasId = id; }, loadGraph: async () => {}, doSearch: async () => {},
  });
  for (const helper of ['money.js', 'principal-analysis.js']) {
    vm.runInContext(readFileSync(new URL(`../static/${helper}`, import.meta.url), 'utf8'), context, { filename: helper });
  }
  root.FundsFacts = context.FundsFacts;
  vm.runInContext(section('function installExportWorkspace(options)', 'function downloadBlob('), context);
  context.exportOptions = { getContext: binding, isCurrent: () => true,
    getScope: () => ({ graphReady: true, graphAccounts: 1, graphRelations: 0, trackedFlows: 1 }), getLabels: () => ({}),
    execute: async () => { exports++; } };
  for (const format of ['png', 'html', 'pdf', 'xlsx']) await vm.runInContext(`installExportWorkspace(exportOptions).submit('${format}')`, context);
  assert.equal(exports, 0);
  vm.runInContext(section('  async function executeExportFormat(format, task)', '  exportWorkspace = installExportWorkspace('), context);
  context.exportTask = { context: binding(), isCurrent: () => true, setPhase() {} };
  await assert.rejects(vm.runInContext("executeExportFormat('xlsx',exportTask)", context), /账户操作尚未确认/);
  vm.runInContext(section('function externalImportPending(', 'let graphRequestSequence = 0;'), context); vm.runInContext('bindCanvases()', context);
  assert.equal(canvasOptions.ready(), false);
  await element('canvasSel').onchange({ target: { value: '2', selectedOptions: [{ textContent: 'Synthetic other canvas' }] } });
  assert.equal(state.canvasId, 1); assert.equal(h.leases.size, 1);
});

test('batch deletion checks the account unknown lock both at entry and after confirmation', async () => {
  for (const lockAtEntry of [true, false]) {
    const h = factoryHarness(), confirmation = deferred(), deletes = [];
    h.setWrite(async () => { throw new Error('lost identity reply'); });
    const makeUnknown = () => assert.rejects(h.actions.mutate(identityMutation(), h.context()), { code: 'write-unknown' });
    if (lockAtEntry) await makeUnknown();
    let confirmations = 0;
    const batch = { id: 'SYN-BATCH', filename: 'synthetic.csv', rows_imported: 1 };
    const batchView = { rows: [batch], deleting: null, request: 0, phase: 'ready' };
    const context = vm.createContext({ window: { FundsAccountActions: h.actions, FundsMainAccountsActions: { blocked: () => false } },
      batchView, batchCurrent: h.current, currentBatch: batch, binding: h.context(), fmtCount: String,
      closeBatchMenu() {}, setBatchBusy() {}, showBatchFeedback() {}, toast() {}, renderBatchRows() {},
      confirm: () => { confirmations++; return confirmation.promise; },
      batchRequest: async (path, options) => { if (options.method === 'DELETE') deletes.push(path); },
      document: { activeElement: { isConnected: true }, body: {} },
    });
    vm.runInContext(section('async function deleteBatch(', 'function bindBatchUI('), context);
    const pending = vm.runInContext('deleteBatch(currentBatch,binding)', context); await tick();
    if (!lockAtEntry) {
      assert.equal(confirmations, 1, 'the unlocked entry must reach its confirmation first');
      await makeUnknown(); confirmation.resolve(true);
    }
    await pending;
    assert.equal(confirmations, lockAtEntry ? 0 : 1);
    assert.deepEqual(deletes, []); assert.equal(batchView.rows.length, 1); assert.equal(batchView.deleting, null);
    assert.equal(h.leases.size, 1); assert.equal(h.actions.blocked(h.context()), true);
  }
});
