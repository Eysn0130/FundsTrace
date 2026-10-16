'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const { syntheticCsv, assertInterrupted, assertReceipt } = require('./import-recovery-smoke.cjs');

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
