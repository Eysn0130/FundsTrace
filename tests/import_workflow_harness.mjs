import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { Blob, File } from 'node:buffer';
import { webcrypto } from 'node:crypto';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/import-workflow.js', import.meta.url), 'utf8');
const importId = '0123456789ab';
const context = { caseId: 1, generation: 1 };
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }
function harness() {
  const window = { addEventListener() {} };
  vm.runInNewContext(source, { window, fetch() { throw new Error('Unexpected global fetch'); } });
  let stage = 'ready', attempt = 0, jobStage = null, owner = '1', blocked = false, active = true, hostPending = false;
  let beforeFetch = async () => {}, legacyRead = async () => true, networkError = false;
  const requests = [], restore = () => {};
  const workflow = window.FundsImportLease.create({ legacy: { canWrite: (...args) => legacyRead(...args), restore },
    accountBlocked: () => blocked, isCurrent: () => active, hostPending: () => hostPending,
    async fetch(path) {
      requests.push(path); await beforeFetch();
      if (networkError) throw new Error('Synthetic disconnected transport');
      if (path.includes('?')) return { ok: true, json: async () => ({ case_id: owner,
        items: [{ case_id: owner, import_id: importId, surface: 'v1', stage, attempt }] }) };
      return { ok: stage !== 'ready', json: async () => ({ case_id: owner, import_id: importId, stage: jobStage || stage }) };
    } });
  return { workflow, requests, restore,
    state(next) { ({ stage = stage, attempt = attempt, owner = owner, blocked = blocked, active = active,
      networkError = networkError, jobStage = jobStage, hostPending = hostPending } = next); },
    before(fn) { beforeFetch = fn; }, legacy(fn) { legacyRead = fn; } };
}

test('the real account controller can complete its own reserved write but blocks imports while unknown', async () => {
  let idNo = '', disconnected = false, writes = 0;
  const listeners = new Map();
  const window = { Blob, File, FormData, crypto: webcrypto, addEventListener(name, callback) { listeners.set(name, callback); }, dispatchEvent() {},
    FundsImportWorkflow: { canWrite: async () => true } };
  const sandbox = vm.createContext({ window, URLSearchParams, Uint8Array, isCurrentCase: () => true, CustomEvent: class {},
    fetch() { throw new Error('No import job may be dispatched'); } });
  vm.runInContext(source, sandbox);
  vm.runInContext(readFileSync(new URL('../static/account-workspace.js', import.meta.url), 'utf8'), sandbox);
  const actions = window.FundsAccountActions = window.FundsAccountWorkspace.create({ isCurrent: () => true,
    leases: new Set(), canWrite: binding => window.FundsImportWorkflow.canWrite(binding),
    async request(path, options = {}) {
      if (!options.method) return { case_id: '1', account: 'SYN-A', id_no: idNo, photo: null };
      writes++; if (disconnected) throw new Error('Synthetic unknown write');
      idNo = JSON.parse(options.body).id_no; return { ok: true, account: 'SYN-A', id_no: idNo };
    } });
  await actions.mutate({ kind: 'identity-set', account: 'SYN-A', idNo: '110101199001010011' }, context);
  assert.equal(writes, 1); assert.equal(actions.blocked(context), false);
  disconnected = true;
  await assert.rejects(actions.mutate({ kind: 'identity-set', account: 'SYN-A', idNo: '110101199001010029' }, context));
  assert.equal(actions.blocked(context), true);
  assert.equal(await window.FundsImportWorkflow.canWrite(context), false);
  await assert.rejects(window.FundsImportWorkflow.acquireExternal(context, importId), /未确认/);
  assert.equal(writes, 2);
});

test('acquire reserves the case before reads and does not let a second writer pass', async () => {
  const h = harness(), pending = deferred(); h.before(() => pending.promise);
  const first = h.workflow.acquireExternal(context, importId); await flush();
  assert.equal(await h.workflow.canWrite(context), false);
  await assert.rejects(h.workflow.acquireExternal(context, importId), /未确认/);
  assert.equal(h.requests.length, 1); pending.resolve(); await first;
  assert.equal(h.workflow.externalStatus(context)[0].importId, importId);
  assert.equal(h.workflow.restore, h.restore);
});

test('failed acquisition removes only its reservation and rejects foreign or stale records', async () => {
  for (const state of [{ owner: '2' }, { stage: 'parsing' }, { active: false }, { networkError: true }]) {
    const h = harness(); h.state(state);
    await assert.rejects(h.workflow.acquireExternal(context, importId));
    assert.equal(h.workflow.externalStatus(context).length, 0);
  }
  const h = harness(); h.legacy(async () => false);
  await assert.rejects(h.workflow.acquireExternal(context, importId));
  assert.equal(h.requests.length, 0); assert.equal(h.workflow.externalStatus(context).length, 0);
});

test('account unknown blocks both legacy writes and external acquisitions', async () => {
  const h = harness(); h.state({ blocked: true });
  assert.equal(await h.workflow.canWrite(context), false);
  await assert.rejects(h.workflow.acquireExternal(context, importId));
  assert.equal(h.requests.length, 0);
});

test('an external reservation made during the legacy check wins before a legacy POST', async () => {
  const h = harness(), pending = deferred(); h.legacy(() => pending.promise);
  const legacy = h.workflow.canWrite(context);
  const embedded = h.workflow.acquireExternal(context, importId);
  pending.resolve(true); assert.equal(await legacy, false); await embedded;
});

test('unknown, absent and nonterminal receipts keep the lease until fresh terminal proof', async () => {
  const h = harness(); await h.workflow.acquireExternal(context, importId);
  for (const state of [{ networkError: true }, { networkError: false }, { stage: 'parsing', attempt: 1 }]) {
    h.state(state); await assert.rejects(h.workflow.releaseExternal(context, importId, importId));
    assert.equal(await h.workflow.canWrite(context), false);
    assert.equal(h.workflow.externalStatus(context)[0].phase, 'unknown');
  }
  h.state({ stage: 'done', attempt: 1 }); await h.workflow.releaseExternal(context, importId, importId);
  assert.equal(h.workflow.externalStatus(context).length, 0); assert.equal(await h.workflow.canWrite(context), true);
});

test('terminal proof must belong to this case, import and a newer durable attempt', async () => {
  for (const state of [{ stage: 'error', attempt: 0 }, { stage: 'done', attempt: 1, owner: '2' },
    { stage: 'parsing', attempt: 1, jobStage: 'done' }]) {
    const h = harness(); await h.workflow.acquireExternal(context, importId); h.state(state);
    await assert.rejects(h.workflow.releaseExternal(context, importId, importId));
    assert.equal(h.workflow.externalStatus(context).length, 1);
  }
  const h = harness(); await h.workflow.acquireExternal(context, importId);
  await assert.rejects(h.workflow.releaseExternal(context, importId, 'wrong-job'));
  assert.equal(h.requests.length, 1);
});

test('a confirmed 4xx rejection only releases the unchanged ready attempt', async () => {
  const h = harness(); await h.workflow.acquireExternal(context, importId);
  await h.workflow.releaseExternal(context, importId, undefined, true);
  assert.equal(await h.workflow.canWrite(context), true);
  for (const state of [{ stage: 'ready', attempt: 1 }, { stage: 'parsing', attempt: 1 }]) {
    const x = harness(); await x.workflow.acquireExternal(context, importId); x.state(state);
    await assert.rejects(x.workflow.releaseExternal(context, importId, undefined, true));
    assert.equal(await x.workflow.canWrite(context), false);
  }
});

test('leases remain case-bound while a different case can read its own write status', async () => {
  const h = harness(); await h.workflow.acquireExternal(context, importId);
  assert.equal(await h.workflow.canWrite({ caseId: 2 }), true);
  assert.equal(h.workflow.externalStatus({ caseId: 2 }).length, 0);
  h.state({ active: false, stage: 'error', attempt: 1 });
  await h.workflow.releaseExternal(context, importId, importId);
  assert.equal(h.workflow.externalStatus(context).length, 0);
});

test('host activity before acquisition denies submission without leaving a reservation', async () => {
  const h = harness(); h.state({ hostPending: true });
  await assert.rejects(h.workflow.acquireExternal(context, importId));
  assert.equal(h.requests.length, 0); assert.equal(h.workflow.externalStatus(context).length, 0);
  h.state({ hostPending: false }); await h.workflow.acquireExternal(context, importId);
  assert.equal(h.workflow.externalStatus(context).length, 1);
});

test('host activity starting during either awaited preflight revokes only the unsent reservation', async () => {
  for (const phase of ['legacy', 'session']) {
    const h = harness(), pending = deferred();
    if (phase === 'legacy') h.legacy(() => pending.promise);
    else h.before(() => pending.promise);
    let postAuthorized = false;
    const acquisition = h.workflow.acquireExternal(context, importId).then(() => { postAuthorized = true; });
    await flush(); assert.equal(h.workflow.externalStatus(context).length, 1);
    h.state({ hostPending: true }); pending.resolve(true);
    await assert.rejects(acquisition);
    assert.equal(postAuthorized, false, 'the iframe never receives permission to POST');
    assert.equal(h.workflow.externalStatus(context).length, 0);
    assert.equal(h.requests.length, phase === 'legacy' ? 0 : 1);
    h.state({ hostPending: false }); await h.workflow.acquireExternal(context, importId);
    assert.equal(h.workflow.externalStatus(context).length, 1, 'a rejected preflight cannot strand the case lock');
  }
});
