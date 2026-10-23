'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const { projectOwnerSnapshot } = require('./installed-update-owner-snapshot.cjs');

const filename = path.join(__dirname, 'installed-update-owner-snapshot.cjs');
const source = fs.readFileSync(filename, 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const observed = overrides => ({ schemaVersion: 1, processBound: true, querySucceeded: true,
  mainWindowUnique: true, mainWindowVisible: true, mainWindowEnabled: false,
  processResponding: true, ownedVisibleDialogCount: 1, ...overrides });
const unavailable = overrides => ({ schemaVersion: 1, processBound: false, querySucceeded: false,
  mainWindowUnique: null, mainWindowVisible: null, mainWindowEnabled: null,
  processResponding: null, ownedVisibleDialogCount: null, ...overrides });

function harness({ binding = { pid: 42, installRoot: 'C:\\Temp\\应用 安装路径' }, options,
  platform = 'win32', ci = 'true', githubActions = 'true', spawnThrows = false } = {}) {
  const child = new EventEmitter();
  child.stdout = new PassThrough(); child.stderr = new PassThrough();
  let killed = 0, scheduled, command;
  child.kill = () => { killed++; };
  const module = { exports: {} };
  const context = vm.createContext({ module, Buffer, Date, __dirname,
    process: { platform, env: { CI: ci, GITHUB_ACTIONS: githubActions } },
    setTimeout(callback, milliseconds) { scheduled = { callback, milliseconds }; return 1; }, clearTimeout() {},
    require(name) {
      if (name === 'node:child_process') return { spawn(...args) {
        command = args;
        if (spawnThrows) throw new Error('SYN_PRIVATE_SPAWN');
        return child;
      } };
      return require(name);
    },
  });
  vm.runInContext(source, context, { filename });
  const pending = module.exports.safeUpdateOwnerSnapshot(binding, options);
  return { child, pending, get scheduled() { return scheduled; }, get command() { return command; },
    get killed() { return killed; }, emitRecord(value) { child.stdout.write(JSON.stringify(value)); } };
}

test('passive snapshot preserves a disabled main window with a native modal, with only fixed safe fields', async () => {
  const run = harness();
  const raw = JSON.stringify(observed({ title: 'SYN_PRIVATE_TITLE', hwnd: 99, pid: 42,
    path: 'SYN_PRIVATE_PATH', exception: 'SYN_PRIVATE_EXCEPTION' }));
  run.child.stdout.write('\uFEFF' + raw.slice(0, 40)); run.child.stdout.write(raw.slice(40));
  run.child.stderr.write('SYN_PRIVATE_STDERR');
  run.child.emit('close', 0);
  const value = plain(await run.pending);
  assert.equal(value.querySucceeded, true); assert.equal(value.mainWindowEnabled, false);
  assert.equal(value.ownedVisibleDialogCount, 1); assert.equal(value.helperReport, 'valid');
  assert.equal(value.stderrObserved, true); assert.equal(value.timedOut, false);
  assert.ok(!JSON.stringify(value).includes('SYN_PRIVATE'));
  assert.equal(Object.hasOwn(value, 'pid'), false); assert.equal(Object.hasOwn(value, 'hwnd'), false);
  assert.equal(run.command[0], 'pwsh.exe');
  assert.equal(run.command[2].windowsHide, true);
  assert.deepEqual(plain(run.command[2].stdio), ['ignore', 'pipe', 'pipe']);
  assert.deepEqual(plain(run.command[1].slice(-4)), ['-InstallRoot', 'C:\\Temp\\应用 安装路径', '-ProcessId', '42']);
  assert.equal(run.scheduled.milliseconds, 8000);
});

test('a responsive enabled main window can explicitly report no native modal', async () => {
  const run = harness();
  run.emitRecord(observed({ mainWindowEnabled: true, ownedVisibleDialogCount: 0 }));
  run.child.emit('close', 0);
  const value = await run.pending;
  assert.equal(value.querySucceeded, true); assert.equal(value.ownedVisibleDialogCount, 0);
  assert.equal(value.mainWindowEnabled, true);
});

test('ambiguous or missing main windows stay unavailable instead of reporting zero dialogs', async () => {
  const run = harness();
  run.emitRecord(unavailable({ processBound: true, mainWindowUnique: false }));
  run.child.emit('close', 0);
  const value = await run.pending;
  assert.equal(value.helperReport, 'valid'); assert.equal(value.processBound, true);
  assert.equal(value.querySucceeded, false); assert.equal(value.mainWindowUnique, false);
  assert.equal(value.ownedVisibleDialogCount, null); assert.equal(value.mainWindowEnabled, null);
});

test('native binding and enumeration failures remain nullable unavailable records', async () => {
  for (const record of [unavailable(), unavailable({ processBound: true })]) {
    const run = harness(); run.emitRecord(record); run.child.emit('close', 0);
    const value = await run.pending;
    assert.equal(value.querySucceeded, false); assert.equal(value.ownedVisibleDialogCount, null);
    assert.equal(value.helperReport, 'valid');
  }
});

test('owner snapshot timeout respects a smaller caller budget and kills only the observer', async () => {
  const run = harness({ options: { budgetMs: 317 } });
  run.emitRecord(observed());
  assert.equal(run.scheduled.milliseconds, 317);
  run.scheduled.callback();
  const value = await run.pending;
  assert.equal(value.querySucceeded, false); assert.equal(value.processBound, false);
  assert.equal(value.timedOut, true); assert.equal(value.ownedVisibleDialogCount, null);
  assert.equal(run.killed, 1); assert.equal(value.exitCode, null);
  run.child.emit('close', 0);
  assert.equal(value.querySucceeded, false, 'late pipe closure cannot upgrade a timed-out observation');
});

test('synchronous and asynchronous observer launch failures resolve without raw error details', async () => {
  const synchronous = harness({ spawnThrows: true });
  const asynchronous = harness();
  asynchronous.child.emit('error', new Error('SYN_PRIVATE_ERROR'));
  for (const run of [synchronous, asynchronous]) {
    const value = await run.pending;
    assert.equal(value.querySucceeded, false); assert.equal(value.helperReport, 'missing');
    assert.equal(value.ownedVisibleDialogCount, null);
    assert.ok(!JSON.stringify(value).includes('SYN_PRIVATE'));
  }
});

test('nonzero observer exit cannot contribute an otherwise valid successful snapshot', async () => {
  const run = harness(); run.emitRecord(observed()); run.child.emit('close', 1);
  const value = await run.pending;
  assert.equal(value.helperReport, 'valid'); assert.equal(value.exitCode, 1);
  assert.equal(value.querySucceeded, false); assert.equal(value.ownedVisibleDialogCount, null);
});

test('malformed or noisy stdout is discarded without copying content to the report', async () => {
  for (const raw of ['SYN_PRIVATE_UNSTRUCTURED', '{invalid}', `${JSON.stringify(observed())}\nSYN_PRIVATE_TRAILER`]) {
    const run = harness(); run.child.stdout.write(raw); run.child.emit('close', 0);
    const value = await run.pending;
    assert.equal(value.helperReport, 'invalid'); assert.equal(value.querySucceeded, false);
    assert.equal(value.ownedVisibleDialogCount, null);
    assert.ok(!JSON.stringify(value).includes('SYN_PRIVATE'));
  }
});

test('stdout is capped at 64 KiB and discards any prior successful snapshot on overflow', async () => {
  const run = harness(); run.emitRecord(observed());
  run.child.stdout.write('SYN_PRIVATE_OVERFLOW'.repeat(4000));
  run.child.emit('close', 0);
  const value = await run.pending;
  assert.equal(value.stdoutTruncated, true); assert.equal(value.querySucceeded, false);
  assert.equal(value.ownedVisibleDialogCount, null);
  assert.ok(!JSON.stringify(value).includes('SYN_PRIVATE'));
});

test('the snapshot projection rejects malformed and inconsistent fields', () => {
  assert.deepEqual(projectOwnerSnapshot(observed({ processResponding: false })), observed({ processResponding: false }));
  assert.deepEqual(projectOwnerSnapshot({ ...observed(), title: 'SYN_PRIVATE' }), observed());
  for (const changes of [{ schemaVersion: 2 }, { processBound: 1 }, { querySucceeded: 'true' },
    { mainWindowUnique: 1 }, { mainWindowVisible: 'true' }, { mainWindowEnabled: null },
    { processResponding: undefined }, { ownedVisibleDialogCount: -1 }, { ownedVisibleDialogCount: 65 },
    { ownedVisibleDialogCount: 0.5 }, { ownedVisibleDialogCount: Infinity }, { processBound: false },
    { mainWindowUnique: false }, { mainWindowVisible: false }, { ownedVisibleDialogCount: null }]) {
    assert.equal(projectOwnerSnapshot(observed(changes)), null);
  }
  for (const changes of [{ mainWindowUnique: true }, { mainWindowUnique: false },
    { mainWindowVisible: true }, { mainWindowEnabled: false }, { processResponding: true },
    { ownedVisibleDialogCount: 0 }]) assert.equal(projectOwnerSnapshot(unavailable(changes)), null);
  assert.equal(projectOwnerSnapshot({ get schemaVersion() { throw new Error('SYN_PRIVATE'); } }), null);
  let reads = 0;
  const getter = observed();
  Object.defineProperty(getter, 'processResponding', { get() { return reads++ ? 'SYN_PRIVATE' : true; } });
  assert.deepEqual(projectOwnerSnapshot(getter), observed());
  assert.equal(reads, 1);
});

test('platform, CI, binding and deadline guards never launch an observer for rejected inputs', async () => {
  const rejected = [{ platform: 'darwin' }, { ci: 'false' }, { githubActions: 'false' },
    { binding: { pid: 0, installRoot: 'C:\\Temp\\test' } },
    { binding: { pid: 2147483648, installRoot: 'C:\\Temp\\test' } },
    { binding: { pid: 42, installRoot: '\\\\server\\share' } },
    { binding: { pid: 42, installRoot: 'relative' } },
    { binding: { pid: 42, installRoot: 'C:\\Temp\\bad\nname' } },
    { binding: { pid: 42, installRoot: 'C:\\Temp\\bad"name' } },
    { binding: { get pid() { throw new Error('SYN_PRIVATE'); } } },
    ...[0, -1, 8001, 1.5, Infinity].map(budgetMs => ({ options: { budgetMs } }))];
  for (const options of rejected) {
    const run = harness(options), value = await run.pending;
    assert.equal(run.command, undefined); assert.equal(run.scheduled, undefined);
    assert.equal(value.querySucceeded, false); assert.equal(value.ownedVisibleDialogCount, null);
    assert.ok(!JSON.stringify(value).includes('SYN_PRIVATE'));
  }
});

test('native observer is limited to passive window APIs and a synthetic process binding', () => {
  const script = fs.readFileSync(path.resolve(__dirname, '../../scripts/observe-windows-update-owner.ps1'), 'utf8');
  const imports = [...script.matchAll(/static extern (?:bool|uint|int|SafeFileHandle) (\w+)\(/g)].map(match => match[1]);
  assert.deepEqual(imports.sort(), ['CreateFileW', 'EnumWindows', 'GetClassNameW', 'GetFinalPathNameByHandleW',
    'GetWindowThreadProcessId', 'IsWindowEnabled', 'IsWindowVisible'].sort());
  assert.doesNotMatch(script, /SendMessage|SendInput|PostMessage|SetForegroundWindow|ShowWindow|GetWindowText|GetWindowRect|GetClientRect|GetWindowPlacement|Win32_Process|Click/);
  assert.match(script, /installer-lifecycle-sentinel\.txt/);
  assert.match(script, /\$null = \$Process\.Handle/);
  assert.match(script, /GetTempPath/);
  assert.match(script, /\$env:GITHUB_ACTIONS -cne 'true'/);
});
