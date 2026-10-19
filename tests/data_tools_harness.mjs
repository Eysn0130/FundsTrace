import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/data-tools.js', import.meta.url), 'utf8');
const flush = async () => { for (let pass = 0; pass < 20; pass++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const file = (name = '合成对照表.xlsx', size = 2048) => ({ name, size });
const receipt = { ok: true, imported: 3, invalid: 2, blank_account: 1 };
class FormDataPort {
  constructor() { this.values = new Map(); }
  append(key, value) { this.values.set(key, value); }
  get(key) { return this.values.get(key); }
}
function harness() {
  const binding = { caseId: 11, generation: 1, canvasId: 5, canvasGeneration: 1 };
  const requests = [], publications = [], states = [], notices = [], shows = [], dismisses = [], preflights = [], picks = [];
  let ready = true, chosen = file();
  let canWrite = async () => true, request = async path => path === '/api/sample-data' ? { ok: 8, rows_dup: 5, skipped: 1 } : receipt;
  let publish = async () => {}, pickFile = async () => chosen, download = async () => {};
  const root = { FormData: FormDataPort };
  vm.runInNewContext(source, { window: root, console });
  const tools = root.FundsDataTools.create({
    context: () => ({ ...binding }),
    isCurrent(context, canvas) {
      assert.equal(canvas, false, 'bulk identity is explicitly case-scoped');
      return context.caseId === binding.caseId && context.generation === binding.generation;
    },
    ready: () => ready,
    async canWrite(context, action) { preflights.push({ context: { ...context }, action }); return canWrite(context, action); },
    async pickFile(context) { picks.push({ ...context }); return pickFile(context); },
    async request(path, options = {}) { requests.push({ path, options }); return request(path, options); },
    async publish(action, context, result) { publications.push({ action, context: { ...context }, result }); return publish(action, context, result); },
    async downloadTemplate(context) { return download(context); },
    onState(state) { states.push(state); }, show(state, trigger) { shows.push({ state, trigger }); },
    dismiss(reason) { dismisses.push(reason); }, notify(message, type) { notices.push({ message, type }); },
  });
  return { tools, binding, requests, publications, states, notices, shows, dismisses, preflights, picks,
    setReady(value) { ready = value; }, setFile(value) { chosen = value; },
    onPick(value) { pickFile = value; }, onWrite(value) { canWrite = value; },
    onRequest(value) { request = value; }, onPublish(value) { publish = value; }, onDownload(value) { download = value; },
    async selected() { tools.open('import'); await tools.pick(); },
    switchCase(id = 12) { binding.caseId = id; binding.generation++; tools.revoke(); },
    writes: () => requests.filter(row => row.options.method === 'POST'),
  };
}

function viewHarness() {
  const nodes = new Map();
  function element(id) {
    if (!nodes.has(id)) nodes.set(id, { id, open: false, isConnected: true, disabled: false, hidden: false,
      textContent: '', children: [], style: {}, attributes: {}, addEventListener() {},
      setAttribute(key, value) { this.attributes[key] = value; }, getClientRects: () => [{}],
      querySelector: selector => id === 'dtProgress' && selector === 'img' ? element('progress-image') : null,
      focus() {}, showModal() { this.open = true; }, close() { this.open = false; },
      replaceChildren(...children) { this.children = children; }, appendChild(child) { this.children.push(child); } });
    return nodes.get(id);
  }
  const doc = { getElementById: element, createElement: tag => ({ tagName: tag, textContent: '' }) };
  const root = { addEventListener() {}, FundsToolbarMenu: { place() {} } };
  vm.runInNewContext(source, { window: root, console });
  return { element, view: root.FundsDataTools.createView(doc, { isCurrent: () => true, readCaseName: () => '合成案件' }) };
}

test('opening each tool is a confirmation view without requests or business writes', () => {
  const h = harness(), anchor = { id: 'toolbar-data' };
  assert.equal(h.tools.open('import', anchor), true);
  assert.equal(h.tools.status().phase, 'select'); assert.equal(h.tools.status().file, null);
  assert.equal(h.shows[0].trigger, anchor); assert.equal(h.tools.open('demo', anchor), true);
  assert.equal(h.tools.status().phase, 'confirm'); assert.equal(h.requests.length, 0);
});

test('file selection does not auto-submit and reveals only real name and size', async () => {
  const h = harness(); await h.selected();
  assert.equal(h.requests.length, 0); assert.equal(h.preflights.length, 0);
  assert.equal(h.tools.status().file.name, '合成对照表.xlsx'); assert.equal(h.tools.status().file.size, 2048);
  assert.equal(h.tools.status().result, null); assert.equal('preview' in h.tools.status(), false);
});

test('picker captures case generation before native selection and rejects A-B-A completion', async () => {
  const h = harness(), pick = deferred(); h.onPick(() => pick.promise);
  h.tools.open('import'); const first = h.tools.pick(); await flush();
  assert.equal(h.picks[0].caseId, 11); assert.equal(h.picks[0].generation, 1);
  h.switchCase(); h.switchCase(11); pick.resolve(file());
  assert.equal(await first, false); assert.equal(h.tools.status().file, null); assert.equal(h.requests.length, 0);
});

test('the same file can be selected again through an independent picker operation', async () => {
  const h = harness(); await h.selected(); const original = h.tools.status().file;
  assert.equal(await h.tools.pick(), true); assert.equal(h.picks.length, 2);
  assert.equal(h.tools.status().file.name, original.name); assert.equal(h.requests.length, 0);
});

test('cancelling a replacement picker preserves the previously selected file', async () => {
  const h = harness(); await h.selected(); h.setFile(null);
  assert.equal(await h.tools.pick(), false); assert.equal(h.tools.status().file.name, '合成对照表.xlsx');
});

test('closing before a picker completes discards its callback without writing', async () => {
  const h = harness(), pick = deferred(); h.onPick(() => pick.promise); h.tools.open('import');
  const selecting = h.tools.pick(); h.tools.close(); pick.resolve(file());
  assert.equal(await selecting, false); assert.equal(h.tools.status().visible, false); assert.equal(h.requests.length, 0);
});

test('an old picker cannot place its file into a new tool session', async () => {
  const h = harness(), pick = deferred(); h.onPick(() => pick.promise); h.tools.open('import');
  const selecting = h.tools.pick(); h.tools.close(); h.tools.open('import'); pick.resolve(file('旧文件.xlsx'));
  assert.equal(await selecting, false); assert.equal(h.tools.status().file, null);
});

test('switching only the canvas retains the case-scoped file and import authorization', async () => {
  const h = harness(); await h.selected(); h.binding.canvasId = 6; h.binding.canvasGeneration++;
  assert.equal(await h.tools.submit(), true); assert.equal(h.writes()[0].options.body.get('case_id'), '11');
  assert.equal(h.publications.length, 1); assert.equal(h.tools.status().phase, 'done');
});

test('explicit import sends exactly the selected file and captured case in FormData', async () => {
  const h = harness(), selected = file('前导零账号.csv'); h.setFile(selected); await h.selected();
  assert.equal(await h.tools.submit(), true);
  assert.equal(h.writes().length, 1); assert.equal(h.writes()[0].path, '/api/account-ids/import');
  assert.equal(h.writes()[0].options.body.get('file'), selected);
  assert.equal(h.writes()[0].options.body.get('case_id'), '11');
  assert.equal(h.writes()[0].options.headers, undefined, 'browser supplies multipart boundary');
});

test('import cannot submit before selection while demo requires its explicit confirmation', async () => {
  const h = harness(); h.tools.open('import'); assert.equal(await h.tools.submit(), false); assert.equal(h.preflights.length, 0);
  h.tools.open('demo'); assert.equal(h.requests.length, 0); assert.equal(await h.tools.submit(), true);
  assert.equal(h.writes()[0].path, '/api/sample-data');
  assert.deepEqual(JSON.parse(h.writes()[0].options.body), { case_id: 11 });
  assert.equal(h.publications[0].action, 'demo');
});

test('unavailable tools, missing cases and unknown commands cannot open or mutate', async () => {
  const h = harness(); assert.equal(h.tools.open('preview'), false); h.setReady(false);
  assert.equal(h.tools.open('import'), false); h.setReady(true); h.binding.caseId = null;
  assert.equal(h.tools.open('demo'), false); assert.equal(await h.tools.submit(), false); assert.equal(h.requests.length, 0);
});

test('a declined draft/conflict preflight leaves the file available and performs no IO', async () => {
  const h = harness(); await h.selected(); h.onWrite(async () => false);
  assert.equal(await h.tools.submit(), false); assert.equal(h.requests.length, 0);
  assert.equal(h.tools.status().phase, 'select'); assert.equal(h.tools.blocked(), false);
});

test('a rejected draft preflight produces a known repairable error, never unknown', async () => {
  const h = harness(); await h.selected(); h.onWrite(async () => { throw new Error('请先保存或放弃账户草稿'); });
  assert.equal(await h.tools.submit(), false); assert.equal(h.requests.length, 0);
  assert.equal(h.tools.status().error, '请先保存或放弃账户草稿'); assert.equal(h.tools.status().unknown, false);
  assert.equal(h.tools.blocked(), false);
});

test('the operation mutex exists before awaiting conflict preflight and rejects duplicate submissions', async () => {
  const h = harness(), gate = deferred(); await h.selected(); h.onWrite(() => gate.promise);
  const first = h.tools.submit(); assert.equal(h.tools.blocked(), true); assert.equal(h.tools.status().phase, 'preflight');
  assert.equal(await h.tools.submit(), false); assert.equal(await h.tools.pick(), false);
  gate.resolve(true); assert.equal(await first, true); assert.equal(h.writes().length, 1);
});

test('closing during preflight cancels the not-yet-sent write even when the guard later allows it', async () => {
  const h = harness(), gate = deferred(); await h.selected(); h.onWrite(() => gate.promise);
  const first = h.tools.submit(); assert.equal(h.tools.close(), true); gate.resolve(true);
  assert.equal(await first, false); assert.equal(h.writes().length, 0); assert.equal(h.tools.blocked(), false);
});

test('case A-B-A switching while preflight is pending invalidates authorization', async () => {
  const h = harness(), gate = deferred(); await h.selected(); h.onWrite(() => gate.promise);
  const first = h.tools.submit(); h.switchCase(); h.switchCase(11); gate.resolve(true);
  assert.equal(await first, false); assert.equal(h.writes().length, 0); assert.equal(h.publications.length, 0);
});

test('readiness is rechecked after awaited preflight immediately before send', async () => {
  const h = harness(), gate = deferred(); await h.selected(); h.onWrite(() => gate.promise);
  const first = h.tools.submit(); h.setReady(false); gate.resolve(true);
  assert.equal(await first, false); assert.equal(h.writes().length, 0); assert.equal(h.tools.status().phase, 'error');
});

test('submitted writes lock Close, Cancel and duplicate submission until the response', async () => {
  const h = harness(), response = deferred(); await h.selected(); h.onRequest(() => response.promise);
  const first = h.tools.submit(); await flush(); assert.equal(h.tools.status().sent, true);
  assert.equal(h.tools.close('escape'), false); assert.equal(h.tools.close('close'), false);
  assert.equal(await h.tools.submit(), false); assert.equal(h.tools.status().visible, true);
  response.resolve(receipt); assert.equal(await first, true); assert.equal(h.tools.close(), true);
  assert.equal(h.writes().length, 1);
});

test('forced context revoke dismisses UI without releasing the in-flight case lock', async () => {
  const h = harness(), response = deferred(); await h.selected(); h.onRequest(() => response.promise);
  const first = h.tools.submit(); await flush(); h.switchCase();
  assert.equal(h.tools.status().visible, false); assert.equal(h.tools.blocked({ caseId: 11, generation: 999 }), true);
  assert.equal(h.tools.blocked({ caseId: 12 }), false); assert.equal(h.tools.open('demo'), false);
  response.resolve(receipt); await first; assert.equal(h.publications.length, 0); assert.equal(h.notices.length, 0);
});

test('late known success never publishes or reopens UI after case A-B-A switching', async () => {
  const h = harness(), response = deferred(); await h.selected(); h.onRequest(() => response.promise);
  const first = h.tools.submit(); await flush(); h.switchCase(); h.switchCase(11); const before = h.shows.length;
  response.resolve(receipt); assert.equal(await first, true);
  assert.equal(h.shows.length, before); assert.equal(h.publications.length, 0); assert.equal(h.tools.status().visible, false);
  assert.equal(h.notices.length, 0); assert.equal(h.tools.blocked(), false);
});

test('late known rejection cannot display an error or toast in the newly selected case', async () => {
  const h = harness(), response = deferred(); await h.selected(); h.onRequest(() => response.promise);
  const first = h.tools.submit(); await flush(); h.switchCase(); response.reject(Object.assign(new Error('旧案件错误'), { status: 400 }));
  assert.equal(await first, false); assert.equal(h.tools.status().error, ''); assert.equal(h.notices.length, 0);
});

test('transport failures retain an unknown lock and prohibit automatic resubmission', async () => {
  const h = harness(); await h.selected(); h.onRequest(async () => { throw new TypeError('network disconnected'); });
  assert.equal(await h.tools.submit(), false); assert.equal(h.tools.status().unknown, true);
  assert.equal(h.tools.blocked(), true); assert.equal(await h.tools.submit(), false);
  assert.equal(h.tools.close(), true); h.tools.open('demo'); assert.equal(h.tools.status().action, 'import');
  assert.equal(h.writes().length, 1);
});

test('HTTP 4xx rejection is known and allows a deliberate corrected-file retry', async () => {
  const h = harness(); await h.selected(); h.onRequest(async () => { throw Object.assign(new Error('缺少账号列'), { status: 400 }); });
  assert.equal(await h.tools.submit(), false); assert.equal(h.tools.status().unknown, false); assert.equal(h.tools.blocked(), false);
  h.setFile(file('修正后的文件.csv')); await h.tools.pick(); h.onRequest(async () => receipt);
  assert.equal(await h.tools.submit(), true); assert.equal(h.writes().length, 2);
  assert.equal(h.writes()[1].options.body.get('file').name, '修正后的文件.csv');
});

test('server 5xx is outcome-unknown rather than a safe retryable import error', async () => {
  const h = harness(); await h.selected(); h.onRequest(async () => { throw Object.assign(new Error('server failure'), { status: 503 }); });
  await h.tools.submit(); assert.equal(h.tools.status().unknown, true); assert.equal(h.tools.acknowledge(), false);
  assert.equal(await h.tools.submit(), false); assert.equal(h.writes().length, 1);
});

test('malformed or missing success counts retain unknown state without fabricated statistics', async () => {
  for (const bad of [null, { ok: true, imported: 1 }, { imported: -1, invalid: 0, blank_account: 0 }, { imported: '3', invalid: 0, blank_account: 0 }]) {
    const h = harness(); await h.selected(); h.onRequest(async () => bad); await h.tools.submit();
    assert.equal(h.tools.status().unknown, true); assert.equal(h.tools.status().result, null); assert.equal(h.publications.length, 0);
  }
});

test('known committed import with refresh failure remains successful and cannot be resubmitted', async () => {
  const h = harness(); await h.selected(); h.onPublish(async () => { throw new Error('metadata refresh failed'); });
  assert.equal(await h.tools.submit(), true); assert.equal(h.tools.status().phase, 'done');
  assert.equal(h.tools.status().result.imported, 3); assert.match(h.tools.status().refreshError, /操作已完成/);
  assert.equal(h.tools.status().unknown, false); assert.equal(await h.tools.submit(), false); assert.equal(h.writes().length, 1);
});

test('a refresh failure arriving after case switch does not set new-case error UI', async () => {
  const h = harness(), refresh = deferred(); await h.selected(); h.onPublish(() => refresh.promise);
  const first = h.tools.submit(); await flush(); h.switchCase(); refresh.reject(new Error('old refresh'));
  assert.equal(await first, true); assert.equal(h.tools.status().visible, false); assert.equal(h.tools.status().refreshError, '');
});

async function unknownImport(h) {
  await h.selected(); h.onRequest(async () => { throw new TypeError('transport'); }); await h.tools.submit();
}

test('unknown outcome requires a successful read-only reconciliation before explicit acknowledgement', async () => {
  const h = harness(); await unknownImport(h); assert.equal(h.tools.acknowledge(), false);
  h.onRequest(async () => [{ account: '000001', id_no: 'SYN-ID' }]);
  assert.equal((await h.tools.reconcile()).length, 1);
  assert.equal(h.requests[1].path, '/api/account-ids/list?case_id=11'); assert.equal(h.requests[1].options.method, undefined);
  assert.equal(h.tools.blocked(), true); assert.equal(h.tools.status().reconciled, true);
  assert.equal(h.tools.acknowledge(), true); assert.equal(h.tools.blocked(), false); assert.equal(h.writes().length, 1);
});

test('failed or invalid reconciliation does not release the unknown mutation lock', async () => {
  for (const result of ['throw', {}]) {
    const h = harness(); await unknownImport(h);
    h.onRequest(async () => { if (result === 'throw') throw new Error('read failed'); return result; });
    assert.equal(await h.tools.reconcile(), null); assert.equal(h.tools.status().reconciled, false);
    assert.equal(h.tools.acknowledge(), false); assert.equal(h.tools.blocked(), true);
  }
});

test('closing a reconciliation read prevents its late rows from authorizing acknowledgement', async () => {
  const h = harness(), read = deferred(); await unknownImport(h); h.onRequest(() => read.promise);
  const first = h.tools.reconcile(); assert.equal(h.tools.close(), true); read.resolve([{ account: 'SYN-A', id_no: 'SYN-ID' }]);
  assert.equal(await first, null); h.tools.open('import'); assert.equal(h.tools.status().reconciled, false);
  assert.equal(h.tools.acknowledge(), false); assert.equal(h.tools.blocked(), true);
});

test('unknown original case can be deliberately reopened after A-B-A for read-only reconciliation', async () => {
  const h = harness(); await unknownImport(h); h.switchCase(); assert.equal(h.tools.open('import'), false);
  h.switchCase(11); assert.equal(h.tools.open('demo'), true); assert.equal(h.tools.status().action, 'import');
  assert.equal(h.tools.status().operationContext.generation, 1); assert.equal(h.tools.status().context.generation, 3);
  h.onRequest(async () => []); await h.tools.reconcile(); assert.equal(h.requests.at(-1).path, '/api/account-ids/list?case_id=11');
  assert.equal(h.tools.acknowledge(), true); assert.equal(h.writes().length, 1);
});

test('a concurrent second reconciliation cannot create duplicate reads', async () => {
  const h = harness(), read = deferred(); await unknownImport(h); h.onRequest(() => read.promise);
  const first = h.tools.reconcile(); assert.equal(await h.tools.reconcile(), null); assert.equal(h.requests.length, 2);
  read.resolve([]); await first; assert.equal(h.tools.status().reconciled, true);
});

test('demo failure reconciles only batches and never regenerates demo data automatically', async () => {
  const h = harness(); h.tools.open('demo'); h.onRequest(async () => { throw new Error('transport'); }); await h.tools.submit();
  h.onRequest(async () => [{ id: 9, filename: '演示批次', rows_imported: 8, rows_dup: 5 }]); await h.tools.reconcile();
  assert.equal(h.requests.at(-1).path, '/api/batches?case_id=11'); assert.equal(h.writes().length, 1);
  assert.equal(h.tools.status().result, null); assert.equal(h.tools.status().rows[0].id, 9);
});

test('template download has its own duplicate guard and does not acquire a case write lock', async () => {
  const h = harness(), download = deferred(); h.onDownload(() => download.promise);
  const first = h.tools.downloadTemplate(); assert.equal(h.tools.status().templatePending, true);
  assert.equal(h.tools.blocked(), false); assert.equal(await h.tools.downloadTemplate(), false);
  h.tools.open('demo'); assert.equal(await h.tools.submit(), true); download.resolve(); assert.equal(await first, true);
  assert.equal(h.notices[0].message, '模板已准备下载'); assert.equal(h.writes().length, 1);
});

test('template download does not notify a different or A-B-A case when it completes', async () => {
  const h = harness(), download = deferred(); h.onDownload(() => download.promise);
  const first = h.tools.downloadTemplate(); h.switchCase(); h.switchCase(11); download.resolve();
  assert.equal(await first, true); assert.equal(h.notices.length, 0); assert.equal(h.requests.length, 0);
});

test('template errors are repairable and do not become unknown mutation outcomes', async () => {
  const h = harness(); h.onDownload(async () => { throw new Error('模板下载未完成'); });
  assert.equal(await h.tools.downloadTemplate(), false); assert.equal(h.tools.status().templatePending, false);
  assert.equal(h.tools.blocked(), false); assert.equal(h.notices[0].type, 'err');
});

test('legacy xls files show conversion guidance and are never sent', async () => {
  const h = harness(); h.setFile(file('旧版.XLS')); h.tools.open('import');
  assert.equal(await h.tools.pick(), false); assert.match(h.tools.status().error, /另存为 \.xlsx/);
  assert.equal(h.tools.status().file, null); assert.equal(await h.tools.submit(), false); assert.equal(h.requests.length, 0);
});

test('zero counts are real completed results with their exact business units', async () => {
  const h = harness(); await h.selected(); h.onRequest(async () => ({ ok: true, imported: 0, invalid: 0, blank_account: 0 }));
  assert.equal(await h.tools.submit(), true); const result = h.tools.status().result;
  assert.deepEqual(Object.keys(result).sort(), ['blank_account', 'imported', 'invalid']); assert.equal(result.imported, 0);
  assert.equal('new' in result, false); assert.equal('overwritten' in result, false); assert.equal('duplicates' in result, false);
});

test('a picker already open cannot replace the immutable file captured by submitted work', async () => {
  const h = harness(), pick = deferred(), response = deferred(); await h.selected(); h.onPick(() => pick.promise);
  const selecting = h.tools.pick(); h.onRequest(() => response.promise); const sending = h.tools.submit(); await flush();
  pick.resolve(file('迟到文件.csv')); assert.equal(await selecting, false);
  assert.equal(h.writes()[0].options.body.get('file').name, '合成对照表.xlsx'); response.resolve(receipt); await sending;
});

test('known completed results cannot be submitted again until a fresh explicit tool session', async () => {
  const h = harness(); await h.selected(); await h.tools.submit(); assert.equal(await h.tools.submit(), false);
  assert.equal(h.writes().length, 1); h.tools.close(); await h.selected(); assert.equal(await h.tools.submit(), true);
  assert.equal(h.writes().length, 2);
});

test('late unknown response retains only the original case lock without surfacing in a new case', async () => {
  const h = harness(), response = deferred(); await h.selected(); h.onRequest(() => response.promise);
  const first = h.tools.submit(); await flush(); h.switchCase(); response.reject(new Error('transport')); await first;
  assert.equal(h.tools.status().visible, false); assert.equal(h.tools.blocked({ caseId: 11 }), true);
  assert.equal(h.tools.blocked({ caseId: 12 }), false); assert.equal(h.notices.length, 0); assert.equal(h.publications.length, 0);
});

test('read-only reconciliation view masks account and identity suffixes without mutating its rows', () => {
  const { element, view } = viewHarness();
  const rows = [{ account: 'SYN-ACCOUNT-0007', id_no: 'SYN-IDENTITY-0009' }];
  view.onState({ visible: true, context: { caseId: 11 }, action: 'import', pending: false, unknown: true,
    phase: 'unknown', reconciled: true, rows });
  const shown = element('dtReconcileList').children[0].textContent;
  assert.equal(shown, '•••• 0007 · •••• 0009'); assert.equal(shown.includes(rows[0].account), false);
  assert.equal(shown.includes(rows[0].id_no), false); assert.equal(rows[0].id_no, 'SYN-IDENTITY-0009');
});

test('shared overlay scope ledger describes demo append and ID overwrite separately', () => {
  const { element, view } = viewHarness(), base = { visible: true, context: { caseId: 11 }, pending: false,
    unknown: false, phase: 'select', reconciled: false, rows: [] };
  view.onState({ ...base, action: 'import' });
  assert.equal(element('dtRange').textContent, '本案件全部画布'); assert.equal(element('dtOverwrite').hidden, false);
  assert.equal(element('dtTemplate').hidden, false); assert.equal(element('dtDemoBody').hidden, true);
  view.onState({ ...base, action: 'demo', phase: 'confirm' });
  assert.equal(element('dtRange').textContent, '当前案件流水'); assert.equal(element('dtOverwrite').hidden, true);
  assert.equal(element('dtTemplate').hidden, true); assert.equal(element('dtImportBody').hidden, true);
  assert.equal(element('dtSubmit').textContent, '加入当前案件');
  view.onState({ ...base, action: 'import' });
  assert.equal(element('dtOverwrite').hidden, false); assert.equal(element('dtTemplate').hidden, false);
});

test('view presents working lock, real success counts and unknown recovery as distinct states', () => {
  const { element, view } = viewHarness(), base = { visible: true, context: { caseId: 11 }, action: 'import',
    file: file(), rows: [] };
  view.onState({ ...base, pending: true, sent: true, phase: 'working' });
  assert.equal(element('dtClose').disabled, true); assert.equal(element('dtCancel').disabled, true);
  assert.equal(element('dtSubmit').disabled, true); assert.equal(element('dtChoose').disabled, true);
  assert.equal(element('dtProgress').attributes['data-state'], 'working');
  assert.equal(element('progress-image').src, '/static/ui-icons/refresh.svg');
  view.onState({ ...base, pending: false, phase: 'done', result: { imported: 0, invalid: 2, blank_account: 1 } });
  assert.equal(element('dtClose').disabled, false); assert.equal(element('dtImported').textContent, '0');
  assert.equal(element('dtInvalid').textContent, '2'); assert.equal(element('dtBlank').textContent, '1');
  assert.equal(element('dtResult').hidden, false); assert.equal(element('dtSubmit').hidden, true);
  assert.equal(element('dtProgress').attributes['data-state'], 'done');
  assert.equal(element('progress-image').src, '/static/ui-icons/check.svg');
  view.onState({ ...base, pending: false, unknown: true, phase: 'unknown', reconciled: true });
  assert.equal(element('dtProgress').hidden, true); assert.equal(element('dtResult').hidden, true);
  assert.equal(element('dtReconcile').hidden, false); assert.equal(element('dtAcknowledge').hidden, false);
  assert.equal(element('dtSubmit').hidden, true); assert.equal(element('dtClose').disabled, false);
});
