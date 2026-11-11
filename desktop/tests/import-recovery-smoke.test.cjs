'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const smoke = require('./import-recovery-smoke.cjs');
const { syntheticCsv, assertInterrupted, assertReceipt } = smoke;

test('native recovery fixture has independent exact-money and duplicate expectations', () => {
  const lines = syntheticCsv().toString('utf8').trimEnd().split('\n');
  assert.equal(lines.shift(), '流水号,交易时间,付款账号,收款账号,交易金额,币种,摘要');
  assert.equal(lines.length, 20003);
  const unique = [...new Set(lines)];
  assert.equal(unique.length, 20000);
  const cents = unique.reduce((sum, line) => sum + BigInt(line.split(',')[4].replace('.', '')), 0n);
  assert.equal(cents, 124000000n);
  assert.equal(lines.filter(line => line === lines[0]).length, 3);
});

test('a done or other failed task cannot pass interruption coverage', () => {
  const interrupted = { stage: 'error', error_code: 'import_interrupted', recoverable: true,
    source_available: true, result: null, done: 0 };
  assert.doesNotThrow(() => assertInterrupted(interrupted));
  assert.throws(() => assertInterrupted({ ...interrupted, stage: 'done' }));
  assert.throws(() => assertInterrupted({ ...interrupted, error_code: 'import_failed' }));
  assert.throws(() => assertInterrupted({ ...interrupted, result: { ok: 1 } }));
});

test('native completion checks the independent row, duplicate and batch receipt', () => {
  const job = { stage: 'done', error_code: null,
    result: { total: 20003, ok: 20000, rows_dup: 3, skipped: 0, batch_id: '2' } };
  assert.doesNotThrow(() => assertReceipt(job));
  assert.throws(() => assertReceipt({ ...job, result: { ...job.result, rows_dup: 0 } }));
});

test('the shipped recovered-import polling survives one HTTP timeout and still validates the receipt', async () => {
  const source = fs.readFileSync(require.resolve('./import-recovery-smoke.cjs'), 'utf8');
  const start = source.indexOf("    const receipt = await check('recoveredImportCompleted'");
  const end = source.indexOf('    async function verifyCommitted()', start);
  assert.ok(start >= 0 && end > start);
  const job = { stage: 'done', error_code: null,
    result: { total: 20003, ok: 20000, rows_dup: 3, skipped: 0, batch_id: '2' } };
  let elapsed = 0, reads = 0;
  const timeouts = [], wait = async ms => { elapsed += ms; };
  const json = async (_ready, _path, _options, _expected, timeout = 15000) => {
    timeouts.push(timeout); reads++;
    if (reads === 1) { elapsed += timeout; throw new DOMException('', 'TimeoutError'); }
    elapsed += 5000; return job;
  };
  const report = { pollingDiagnostics: { recoveredImportReadTimeouts: 0 } };
  const result = await vm.runInNewContext(`(async () => { ${source.slice(start, end)} return receipt; })()`, {
    check: async (_name, action) => action(), performance: { now: () => elapsed },
    ready: {}, importBase: '/synthetic-import', json, delay: wait, assert, assertReceipt, report,
    waitForRecoveredImport: (read, options) => smoke.waitForRecoveredImport(read, { ...options, now: () => elapsed, wait }),
  });
  assert.deepEqual(result, job.result); assert.equal(reads, 2);
  assert.equal(report.pollingDiagnostics.recoveredImportReadTimeouts, 1);
  assert.deepEqual(timeouts, [15000, 15000]); assert.ok(elapsed < 180000);
});

test('recovery keeps the 15s request maximum and original 180s total across repeated timeouts', async () => {
  assert.equal(smoke.REQUEST_TIMEOUT_MS, 15000); assert.equal(smoke.RECOVERY_TIMEOUT_MS, 180000);
  let elapsed = 0;
  const timeouts = [], waits = [];
  await assert.rejects(smoke.waitForRecoveredImport(async timeout => {
    timeouts.push(timeout); elapsed += timeout; throw new DOMException('', 'TimeoutError');
  }, { now: () => elapsed, wait: async ms => { waits.push(ms); elapsed += ms; } }), { name: 'TimeoutError' });
  assert.equal(elapsed, 180000); assert.equal(timeouts.length, 12);
  assert.deepEqual(timeouts, [...Array(11).fill(15000), 12800]);
  assert.deepEqual(waits, Array(11).fill(200));
});

test('the last polling pause is clipped to the unspent total budget', async () => {
  let elapsed = 0, reads = 0;
  const waits = [];
  await assert.rejects(smoke.waitForRecoveredImport(async timeout => {
    reads++;
    if (reads < 12) { elapsed += timeout; throw new DOMException('', 'TimeoutError'); }
    assert.equal(timeout, 12800); elapsed += 12700; return { stage: 'importing' };
  }, { now: () => elapsed, wait: async ms => { waits.push(ms); elapsed += ms; } }), { name: 'TimeoutError' });
  assert.equal(elapsed, 180000); assert.equal(reads, 12); assert.equal(waits.at(-1), 100);
});

for (const [kind, read, name] of [
  ['task error', async () => ({ stage: 'error' }), 'AssertionError'],
  ['damaged done receipt', async () => ({ stage: 'done', error_code: null,
    result: { total: 20003, ok: 20000, rows_dup: 0, skipped: 0, batch_id: '2' } }), 'AssertionError'],
  ['HTTP error', async () => { assert.equal(503, 200); }, 'AssertionError'],
  ['hard network error', async () => { throw new TypeError(); }, 'TypeError'],
  ['invalid JSON', async () => { throw new SyntaxError(); }, 'SyntaxError'],
  ['other abort', async () => { throw new DOMException('', 'AbortError'); }, 'AbortError'],
]) test(`${kind} fails the recovery poll immediately instead of being retried`, async () => {
  let reads = 0, waits = 0;
  await assert.rejects(smoke.waitForRecoveredImport(async timeout => {
    reads++; assert.equal(timeout, 15000); return read();
  }, { now: () => 0, wait: async () => { waits++; } }), { name });
  assert.equal(reads, 1); assert.equal(waits, 0);
});

test('a receipt returned after the total deadline cannot pass qualification', async () => {
  let elapsed = 0;
  await assert.rejects(smoke.waitForRecoveredImport(async () => {
    elapsed = 180001;
    return { stage: 'done', error_code: null,
      result: { total: 20003, ok: 20000, rows_dup: 3, skipped: 0, batch_id: '2' } };
  }, { now: () => elapsed, wait: async () => { assert.fail('a late receipt must not schedule another poll'); } }), { name: 'TimeoutError' });
});

test('the shipped failure path records only a safe error category and failed-check duration', async () => {
  const source = fs.readFileSync(require.resolve('./import-recovery-smoke.cjs'), 'utf8');
  const start = source.indexOf('  async function check('), end = source.indexOf('  function boot(', start);
  assert.ok(start >= 0 && end > start);
  const privateValue = 'SYN-MUST-NOT-ENTER-REPORT';
  const report = { status: 'running', checks: {}, timings: {}, failedCheck: null }, process = {};
  let elapsed = 0;
  await vm.runInNewContext(`(async () => { let stage; ${source.slice(start, end)}
    try { await check('syntheticFailure', async () => { advance(); throw failure; }); }
    catch (error) { fail(stage, error, report.timings[stage]); }
  })()`, { report, process, performance: { now: () => elapsed },
    advance: () => { elapsed = 123.6; }, failureDetails: smoke.failureDetails,
    failure: { name: 'TimeoutError', message: privateValue, stack: privateValue,
      response: { body: privateValue }, token: privateValue } });
  assert.equal(report.status, 'failed'); assert.equal(report.failedCheck, 'syntheticFailure');
  assert.equal(report.checks.syntheticFailure, undefined); assert.equal(report.timings.syntheticFailure, 124);
  assert.deepEqual(JSON.parse(JSON.stringify(report.failures)), [{ check: 'syntheticFailure', errorName: 'TimeoutError', elapsedMs: 124 }]);
  assert.equal(process.exitCode, 1); assert.equal(JSON.stringify(report).includes(privateValue), false);
  assert.deepEqual(smoke.failureDetails({ name: privateValue, message: privateValue }, 42), { errorName: 'UNCLASSIFIED', elapsedMs: 42 });
});
