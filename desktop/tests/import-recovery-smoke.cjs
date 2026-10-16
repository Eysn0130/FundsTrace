'use strict';
// Windows Actions only. Exercise a distributed backend through its public HTTP
// surface, with disposable synthetic data and only child handles owned here.
// Usage: node import-recovery-smoke.cjs <absolute-installed-backend-root> --report <absolute-json>
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { performance } = require('node:perf_hooks');
const { launchConfig, HEADER } = require('../host-policy.cjs');
const { startBackend } = require('../backend.cjs');

const UNIQUE_ROWS = 20000, DUPLICATE_ROWS = 3, EXPECTED_TOTAL = '1240000.00';
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function syntheticCsv() {
  const rows = Array.from({ length: UNIQUE_ROWS }, (_, index) =>
    `SYN-${String(index).padStart(6, '0')},2026-01-01 10:00:00,000SYN-A,000SYN-B,${index % 2 === 0 ? '123.45' : '0.55'},CNY,synthetic recovery`);
  return Buffer.from(['流水号,交易时间,付款账号,收款账号,交易金额,币种,摘要', ...rows, rows[0], rows[1], rows[0], ''].join('\n'), 'utf8');
}

function assertInterrupted(job) {
  // A completed import does not establish interruption/recovery coverage.
  assert.equal(job.stage, 'error');
  assert.equal(job.error_code, 'import_interrupted');
  assert.equal(job.recoverable, true);
  assert.equal(job.source_available, true);
  assert.equal(job.result, null);
  assert.equal(job.done, 0);
}

function assertReceipt(job) {
  assert.equal(job.stage, 'done');
  assert.equal(job.error_code, null);
  assert.equal(job.result.total, UNIQUE_ROWS + DUPLICATE_ROWS);
  assert.equal(job.result.ok, UNIQUE_ROWS);
  assert.equal(job.result.rows_dup, DUPLICATE_ROWS);
  assert.equal(job.result.skipped, 0);
  assert.match(job.result.batch_id, /^[1-9]\d*$/);
}

async function bounded(promise, ms) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error('TIMEOUT')), ms);
  })]); } finally { clearTimeout(timer); }
}

function within(root, value) {
  const relative = path.relative(root, value);
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}

function argumentsOf(args) {
  assert.equal(args.length, 3);
  assert.equal(args[1], '--report');
  assert.ok(path.isAbsolute(args[0]) && path.isAbsolute(args[2]));
  const backendRoot = path.resolve(args[0]), reportPath = path.resolve(args[2]);
  assert.equal(path.basename(backendRoot).toLowerCase(), 'backend');
  assert.equal(path.extname(reportPath).toLowerCase(), '.json');
  assert.ok(!within(backendRoot, reportPath));
  return { backendRoot, reportPath };
}

async function main() {
  const report = { schemaVersion: 1, status: 'running', checks: {}, timings: {}, failedCheck: null,
    synthetic: { inputRows: UNIQUE_ROWS + DUPLICATE_ROWS, uniqueRows: UNIQUE_ROWS,
      duplicateRows: DUPLICATE_ROWS, expectedAmount: EXPECTED_TOTAL },
    limits: { windowsActionsNativeBackendOnly: true, installedBackendPublicHttp: true,
      recoveryUiInteraction: false, defaultUserDataTouched: false } };
  const owned = [];
  let stage = 'ciSyntheticRuntime', reportPath, reportOwned = false, runtime;
  async function check(name, action) {
    stage = name;
    const start = performance.now(), result = await action();
    report.checks[name] = true;
    report.timings[name] = Math.round(performance.now() - start);
    return result;
  }
  function boot(config) {
    const instance = { child: null, done: false, exited: null, backend: null };
    owned.push(instance);
    instance.backend = startBackend(config, { spawnProcess: (...args) => {
      instance.child = spawn(...args);
      instance.exited = new Promise(resolve => {
        instance.child.once('exit', (code, signal) => { instance.done = true; resolve({ code, signal }); });
        instance.child.once('error', () => { instance.done = true; resolve({ code: null, signal: null }); });
      });
      return instance.child;
    } });
    return instance;
  }
  async function stop(instance) {
    await instance.backend.stop();
    assert.deepEqual(await bounded(instance.exited, 15000), { code: 0, signal: null });
  }
  function request(ready, endpoint, options = {}, timeout = 15000) {
    return fetch(`${ready.origin}${endpoint}`, { ...options, redirect: 'manual',
      headers: { [HEADER]: ready.token, ...options.headers }, signal: AbortSignal.timeout(timeout) });
  }
  async function json(ready, endpoint, options, expectedStatus = 200, timeout) {
    const response = await request(ready, endpoint, options, timeout);
    assert.equal(response.status, expectedStatus);
    return response.json();
  }
  const post = body => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  try {
    const config = await check('ciSyntheticRuntime', async () => {
      assert.equal(process.platform, 'win32'); assert.equal(process.arch, 'x64');
      assert.equal(process.env.CI, 'true'); assert.equal(process.env.GITHUB_ACTIONS, 'true');
      assert.ok(Number(process.versions.node.split('.')[0]) >= 22);
      const args = argumentsOf(process.argv.slice(2)); reportPath = args.reportPath;
      // The Unicode/space path is owned exclusively by this run. The ordinary
      // APPDATA/FundsTrace business directory is never opened or removed.
      runtime = await fs.mkdtemp(path.join(os.tmpdir(), 'FundsTrace 中文 导入恢复 '));
      assert.ok(!within(runtime, reportPath));
      await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx' }); reportOwned = true;
      const env = Object.fromEntries(Object.entries(process.env).filter(([name]) =>
        !/^(FUND_|FUNDS_TRACE_|PYTHON|NODE_OPTIONS$)/i.test(name)));
      const value = launchConfig({ packaged: true, resourcesPath: path.dirname(args.backendRoot), dataDir: runtime, env });
      assert.equal(path.dirname(value.command), args.backendRoot);
      return value;
    });
    const first = boot(config);
    let ready = await check('installedBackendReady', () => first.backend.ready);
    const created = await check('syntheticCaseCreated', () =>
      json(ready, '/api/v1/cases', post({ name: 'SYN native interruption recovery' }), 201));
    assert.match(created.id, /^[1-9]\d*$/);
    const base = `/api/v1/cases/${created.id}`, csv = syntheticCsv();
    const preview = await check('syntheticUploadPreviewed', async () => {
      const body = new FormData(); body.append('file', new Blob([csv], { type: 'text/csv' }), '合成 恢复流水.csv');
      const value = await json(ready, `${base}/imports/preview`, { method: 'POST', body }, 200, 120000);
      assert.equal(value.case_id, created.id);
      assert.equal(value.stats.total, UNIQUE_ROWS + DUPLICATE_ROWS);
      assert.equal(value.stats.ok, UNIQUE_ROWS + DUPLICATE_ROWS);
      assert.equal(value.stats.skipped, 0);
      return value;
    });
    const importBase = `${base}/imports/${preview.import_id}`;
    const commitRequest = { mode: preview.mode, mapping: preview.mapping, self_accounts: [], mark_accounts: [] };
    await check('activeImportObservedAndOwnedBackendKilled', async () => {
      const value = await json(ready, `${importBase}/commit`, post(commitRequest));
      assert.equal(value.started, true);
      const job = await json(ready, importBase);
      // No retries with new data and no injected production delay: failure to
      // observe an active task is a coverage failure, including an early done.
      assert.ok(job.stage === 'parsing' || job.stage === 'importing');
      assert.equal(first.done, false);
      assert.equal(first.child.kill('SIGKILL'), true);
      await bounded(first.exited, 15000);
      assert.equal(first.done, true);
    });
    const reopened = boot(config);
    ready = await check('interruptedBackendReopened', () => reopened.backend.ready);
    await check('interruptedReceiptRecovered', async () => {
      assertInterrupted(await json(ready, importBase));
      const sessions = await json(ready, `${base}/imports`);
      assert.equal(sessions.items.length, 1);
      assert.equal(sessions.items[0].import_id, preview.import_id);
      assert.equal(sessions.items[0].stage, 'error');
      assert.equal(sessions.items[0].error_code, 'import_interrupted');
      assert.equal(sessions.items[0].attempt, 1);
    });
    await check('interruptionLeftNoPartialBatchFlowOrSource', async () => {
      const stats = await json(ready, `${base}/stats`), flows = await json(ready, `${base}/flows`);
      assert.equal(stats.flows, 0); assert.equal(stats.accounts, 0); assert.equal(stats.batches, 0);
      assert.equal(stats.amount_total, '0.00'); assert.deepEqual(stats.amount_totals, []);
      assert.equal(flows.total, 0); assert.deepEqual(flows.items, []);
      assert.deepEqual(await json(ready, `/api/batches?case_id=${created.id}`), []);
      // This public route resolves committed import_sources, independently of
      // the retained upload/session needed for an explicit recovery attempt.
      const source = await request(ready, `${base}/sources/${preview.import_id}/file`);
      assert.equal(source.status, 404); await source.arrayBuffer();
    });
    await check('explicitRepreviewAndCommit', async () => {
      const value = await json(ready, `${importBase}/preview`, post({ mode: preview.mode, mapping: preview.mapping }), 200, 120000);
      assert.equal(value.import_id, preview.import_id); assert.equal(value.stats.ok, UNIQUE_ROWS + DUPLICATE_ROWS);
      // Repreview alone must retain the no-write state; this next request is the
      // explicit equivalent of the human Confirm import action.
      assert.equal((await json(ready, `${base}/stats`)).flows, 0);
      assert.equal((await json(ready, `${importBase}/commit`, post(commitRequest))).started, true);
    });
    const receipt = await check('recoveredImportCompleted', async () => {
      const deadline = performance.now() + 180000;
      while (performance.now() < deadline) {
        const value = await json(ready, importBase);
        if (value.stage === 'done') { assertReceipt(value); return value.result; }
        assert.notEqual(value.stage, 'error');
        await delay(200);
      }
      throw new Error('TIMEOUT');
    });
    async function verifyCommitted() {
      const stats = await json(ready, `${base}/stats`);
      assert.equal(stats.flows, UNIQUE_ROWS); assert.equal(stats.accounts, 2); assert.equal(stats.batches, 1);
      assert.equal(stats.amount_total, EXPECTED_TOTAL);
      assert.deepEqual(stats.amount_totals, [{ currency: 'CNY', amount: EXPECTED_TOTAL }]);
      assert.equal((await json(ready, `${base}/flows`)).total, UNIQUE_ROWS);
      const batches = await json(ready, `/api/batches?case_id=${created.id}`);
      assert.equal(batches.length, 1); assert.equal(batches[0].rows_imported, UNIQUE_ROWS);
      assert.equal(batches[0].rows_dup, DUPLICATE_ROWS);
      const flows = await json(ready, `${base}/flows?kw=SYN-000000`);
      assert.equal(flows.total, 1); assert.equal(flows.items[0].amount, '123.45');
      const sources = await json(ready, `${base}/flows/${flows.items[0].id}/sources`);
      assert.equal(sources.total, 3);
      assert.equal(sources.items.filter(item => item.disposition === 'imported').length, 1);
      assert.equal(sources.items.filter(item => item.disposition === 'duplicate').length, 2);
      assert.ok(sources.items.every(item => item.source_id === preview.import_id && item.source_available));
      const source = await request(ready, `${base}/sources/${preview.import_id}/file`);
      assert.equal(source.status, 200); assert.ok(Buffer.from(await source.arrayBuffer()).equals(csv));
      const sessions = await json(ready, `${base}/imports`);
      assert.equal(sessions.items.length, 1); assert.equal(sessions.items[0].attempt, 2);
      assert.deepEqual(sessions.items[0].result, receipt);
    }
    await check('exactMoneyDedupAndProvenance', verifyCommitted);
    await check('completedBackendClosed', () => stop(reopened));
    const completed = boot(config);
    ready = await check('completedReceiptReopened', () => completed.backend.ready);
    await check('doneReceiptNeverReplayed', async () => {
      const job = await json(ready, importBase); assertReceipt(job); assert.deepEqual(job.result, receipt);
      assert.equal((await json(ready, `${importBase}/commit`, post(commitRequest))).started, false);
      await verifyCommitted();
      assert.deepEqual((await json(ready, importBase)).result, receipt);
    });
    await check('finalOwnedBackendClosed', () => stop(completed));
    report.status = 'passed';
  } catch {
    report.status = 'failed'; report.failedCheck = stage; process.exitCode = 1;
  } finally {
    for (const instance of owned) {
      try {
        await instance.backend?.stop();
        if (instance.exited) await bounded(instance.exited, 15000);
      } catch { report.status = 'failed'; report.failedCheck = 'ownedProcessCleanup'; process.exitCode = 1; }
    }
    if (runtime && owned.every(instance => instance.done)) {
      try { await fs.rm(runtime, { recursive: true, force: true }); }
      catch { report.status = 'failed'; report.failedCheck = 'ownedRuntimeCleanup'; process.exitCode = 1; }
    }
    if (reportOwned) {
      try { await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8'); }
      catch { report.status = 'failed'; report.failedCheck = 'safeReport'; process.exitCode = 1; }
    }
    if (report.status === 'passed') process.stdout.write(`${JSON.stringify(report)}\n`);
    else process.stderr.write(`${report.failedCheck} failed.\n`);
  }
}

if (require.main === module) void main();
module.exports = { syntheticCsv, assertInterrupted, assertReceipt, argumentsOf };
