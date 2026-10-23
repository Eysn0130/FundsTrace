'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const { safeUpdateDialogFailure } = require('./installed-update-journey.cjs');

const filename = path.join(__dirname, 'installed-update-journey.cjs');
const source = fs.readFileSync(filename, 'utf8');
const record = overrides => ({ schemaVersion: 1, choice: 'later', stage: 'waitDialog', status: 'failed',
  attempts: 198, ownedVisibleDialogCount: 0, matchingDialogCount: 0, matchingButtonCount: 0,
  processAlive: true, dialogSeen: false, buttonSeen: false, sendAttempted: false,
  sendMessageCompleted: false, dialogClosedObserved: false, dialogVisible: null, dialogEnabled: null,
  buttonVisible: null, buttonEnabled: null, foregroundIsDialog: null, ...overrides });
const dto = overrides => ({ ...record(), helperReport: 'valid', exitCode: 1, elapsedMs: 20031,
  timedOut: false, stderrObserved: false, stdoutTruncated: false, ...overrides });
const plain = value => JSON.parse(JSON.stringify(value));

function harness() {
  const child = new EventEmitter();
  child.stdout = new PassThrough(); child.stderr = new PassThrough();
  let killed = 0, scheduled, command;
  child.kill = () => { killed++; };
  const module = { exports: {} };
  const context = vm.createContext({ module, Buffer, Date, __dirname,
    setTimeout(callback, milliseconds) { scheduled = { callback, milliseconds }; return 1; },
    clearTimeout() {},
    require(name) {
      if (name === 'node:child_process') return { spawn(...args) { command = args; return child; } };
      return require(name);
    },
  });
  vm.runInContext(source, context, { filename });
  const pending = module.exports.nativeButton(42, 'later').then(() => null, error => error);
  const emitRecord = value => child.stdout.write(`FT_UPDATE_DIALOG ${JSON.stringify(value)}\n`);
  return { child, pending, emitRecord, get scheduled() { return scheduled; },
    get command() { return command; }, get killed() { return killed; } };
}

test('native dialog failure retains only bounded diagnostic fields across pipe chunks', async () => {
  const run = harness();
  const text = `FT_UPDATE_DIALOG ${JSON.stringify(record({ stage: 'findButton', dialogSeen: true,
    matchingDialogCount: 1, dialogVisible: true, rawException: 'SYN_PRIVATE_EXCEPTION',
    path: 'SYN_PRIVATE_PATH', title: 'SYN_PRIVATE_TITLE', pid: 42, hwnd: 42 }))}\n`;
  run.child.stdout.write('SYN_PRIVATE_UNSTRUCTURED_STDOUT\n');
  run.child.stdout.write(text.slice(0, 83)); run.child.stdout.write(text.slice(83));
  run.child.stderr.write('SYN_PRIVATE_STDERR');
  run.child.emit('close', 1);
  const error = await run.pending;
  assert.equal(error.message, 'UPDATE_DIALOG_FAILED');
  const safe = safeUpdateDialogFailure(error);
  assert.equal(safe.stage, 'findButton');
  assert.equal(safe.dialogSeen, true); assert.equal(safe.buttonSeen, false);
  assert.equal(safe.stderrObserved, true); assert.equal(safe.exitCode, 1);
  assert.equal(safe.helperReport, 'valid');
  assert.ok(!JSON.stringify(safe).includes('SYN_PRIVATE'));
  assert.equal(Object.hasOwn(safe, 'pid'), false); assert.equal(Object.hasOwn(safe, 'hwnd'), false);
  assert.equal(run.command[0], 'pwsh.exe');
  assert.equal(run.command[2].windowsHide, true);
});

test('native helper spawn error keeps the original fixed failure and a missing-report projection', async () => {
  const run = harness();
  run.child.emit('error', new Error('SYN_PRIVATE_SPAWN_ERROR'));
  const error = await run.pending;
  assert.equal(error.message, 'UPDATE_DIALOG_FAILED');
  const safe = safeUpdateDialogFailure(error);
  assert.equal(safe.stage, 'notStarted'); assert.equal(safe.helperReport, 'missing');
  assert.equal(safe.exitCode, null); assert.equal(safe.processAlive, null);
  assert.ok(!JSON.stringify(safe).includes('SYN_PRIVATE'));
});

test('invalid native helper output cannot enter the failure report', async () => {
  const run = harness();
  run.emitRecord(record({ stage: 'SYN_PRIVATE_STAGE' }));
  run.emitRecord(record({ attempts: 'SYN_PRIVATE_COUNT' }));
  run.child.stdout.write('FT_UPDATE_DIALOG {invalid}\n');
  run.child.emit('close', 1);
  const error = await run.pending;
  assert.equal(error.message, 'UPDATE_DIALOG_FAILED');
  const safe = safeUpdateDialogFailure(error);
  assert.equal(safe.helperReport, 'invalid'); assert.equal(safe.stage, 'notStarted');
  assert.ok(!JSON.stringify(safe).includes('SYN_PRIVATE'));
});

test('native dialog timeout remains 25 seconds and preserves the last safe stage', async () => {
  const run = harness();
  run.emitRecord(record({ stage: 'waitClosed', matchingDialogCount: 1, matchingButtonCount: 1,
    dialogSeen: true, buttonSeen: true, sendAttempted: true, sendMessageCompleted: true }));
  assert.equal(run.scheduled.milliseconds, 25000);
  run.child.kill = () => { throw new Error('SYN_PRIVATE_KILL_ERROR'); };
  run.scheduled.callback();
  const error = await run.pending;
  assert.equal(error.message, 'UPDATE_DIALOG_TIMEOUT');
  const safe = safeUpdateDialogFailure(error);
  assert.equal(safe.stage, 'waitClosed'); assert.equal(safe.timedOut, true); assert.equal(safe.exitCode, null);
  assert.equal(safe.sendMessageCompleted, true); assert.equal(safe.dialogClosedObserved, false);
});

test('native dialog diagnostics stop retaining output at 64 KiB without masking failure', async () => {
  const run = harness();
  run.emitRecord(record());
  run.child.stdout.write('SYN_PRIVATE_OVERFLOW'.repeat(4000));
  run.child.emit('close', 1);
  const error = await run.pending;
  assert.equal(error.message, 'UPDATE_DIALOG_FAILED');
  const safe = safeUpdateDialogFailure(error);
  assert.equal(safe.stdoutTruncated, true); assert.equal(safe.helperReport, 'valid');
  assert.ok(!JSON.stringify(safe).includes('SYN_PRIVATE'));
});

test('native helper success still requires its zero exit and final records drain before close', async () => {
  const run = harness();
  run.child.emit('exit', 0);
  run.emitRecord(record({ stage: 'completed', status: 'passed', dialogClosedObserved: true }));
  run.child.emit('close', 0);
  assert.equal(await run.pending, null);
  assert.equal(run.killed, 0);
});

test('native helper final safe record after exit is retained on failure', async () => {
  const run = harness();
  run.child.emit('exit', 1);
  run.emitRecord(record({ stage: 'sendMessage', dialogSeen: true, buttonSeen: true, sendAttempted: true }));
  run.child.emit('close', 1);
  const error = await run.pending;
  assert.equal(safeUpdateDialogFailure(error).stage, 'sendMessage');
});

test('safe dialog projection is repeatable and rejects unbounded or ill-typed fields', () => {
  const expected = dto();
  assert.deepEqual(safeUpdateDialogFailure({ ...expected, rawException: 'SYN_PRIVATE' }), expected);
  assert.deepEqual(safeUpdateDialogFailure({ nativeUpdateDialog: expected }), expected);
  assert.deepEqual(safeUpdateDialogFailure(safeUpdateDialogFailure(expected)), expected);
  for (const changed of [{ choice: 'SYN_PRIVATE' }, { stage: 'unknown' }, { status: 'unknown' },
    { attempts: -1 }, { attempts: 1000001 }, { attempts: 0.5 }, { matchingButtonCount: Infinity },
    { sendAttempted: 'true' }, { dialogVisible: 1 }, { processAlive: undefined },
    { helperReport: 'unknown' }, { exitCode: '1' }, { exitCode: 2147483648 },
    { elapsedMs: 60001 }, { elapsedMs: -1 }, { timedOut: 1 }, { stderrObserved: null }]) {
    assert.equal(safeUpdateDialogFailure(dto(changed)), null);
  }
  assert.equal(safeUpdateDialogFailure({ get nativeUpdateDialog() { throw new Error('SYN_PRIVATE'); } }), null);
  assert.equal(safeUpdateDialogFailure(null), null);
  assert.equal(safeUpdateDialogFailure('SYN_PRIVATE'), null);
  assert.deepEqual(plain(safeUpdateDialogFailure(dto())), expected);
});

test('native helper retains the exact owned title, labels, BM_CLICK and original deadlines', () => {
  const helper = fs.readFileSync(path.resolve(__dirname, '../../scripts/click-windows-update-dialog.ps1'), 'utf8');
  assert.ok(helper.includes('owner==pid'));
  assert.ok(helper.includes('Text(h)=="重启更新 FundsTrace"'));
  assert.ok(helper.includes("{ '稍后' } else { '重启更新' }"));
  assert.ok(helper.includes('SendMessageTimeout(target,0x00F5,IntPtr.Zero,IntPtr.Zero,2,2000,out result)'));
  assert.ok(helper.includes('[DateTime]::UtcNow.AddSeconds(20)'));
  assert.ok(helper.includes('[FundsUpdateDialog]::Closed($Clicked)'));
  assert.ok(!helper.includes('SetForegroundWindow'));
  assert.ok(!helper.includes('[Console]::Error.WriteLine'));
});
