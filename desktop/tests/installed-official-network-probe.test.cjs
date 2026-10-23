'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { diagnosticInspectorArguments, publicUpdateSnapshot, projectEvents, registrationExpression, createOfficialNetworkProbe } = require('./installed-official-network-probe.cjs');
const packageJson = path.resolve('synthetic', 'resources', 'app.asar', 'package.json');
const plain = value => JSON.parse(JSON.stringify(value));

test('Main inspector is forbidden in normal mode and requires every explicit diagnostic binding', () => {
  assert.deepEqual(diagnosticInspectorArguments(undefined), []);
  assert.deepEqual(diagnosticInspectorArguments({ enabled: false, get productionUpdate() { assert.fail('normal mode guard touched'); } }), []);
  const valid = { enabled: true, productionUpdate: true, platform: 'win32', ci: 'true', githubActions: 'true', sentinelVerified: true, temporaryInstallVerified: true };
  assert.deepEqual(diagnosticInspectorArguments(valid), ['--inspect-brk=127.0.0.1:0']);
  for (const changes of [{ productionUpdate: false }, { platform: 'darwin' }, { ci: 'false' }, { githubActions: 'false' },
    { sentinelVerified: false }, { temporaryInstallVerified: false }]) assert.throws(() => diagnosticInspectorArguments({ ...valid, ...changes }), /OFFICIAL_DIAGNOSTIC_BINDING/);
});

test('CLI accepts the diagnostic flag only once and only alongside the production-update journey', () => {
  const smoke = fs.readFileSync(path.join(__dirname, 'installed-app-smoke.cjs'), 'utf8');
  const parser = smoke.slice(smoke.indexOf('function argumentsOf()'), smoke.indexOf('function within('));
  const root = path.resolve('synthetic-install'), report = path.resolve('synthetic-report.json'), config = path.resolve('synthetic-config.json');
  const parse = args => vm.runInNewContext(`${parser}\nargumentsOf()`, { assert, path, process: { argv: ['node', 'smoke', root, '--report', report, ...args] } });
  assert.equal(parse([]).officialNetworkDiagnostic, false);
  assert.equal(parse(['--production-update', config, '--official-network-diagnostic']).officialNetworkDiagnostic, true);
  for (const args of [['--official-network-diagnostic'], ['--update-fixture', config, '--official-network-diagnostic'],
    ['--production-update', config, '--official-network-diagnostic', '--official-network-diagnostic']]) assert.throws(() => parse(args));
});

test('an unassigned spawn PID retains an owned cleanup handle and absorbs the later private spawn error', async () => {
  const smoke = fs.readFileSync(path.join(__dirname, 'installed-app-smoke.cjs'), 'utf8');
  const bootSource = smoke.slice(smoke.indexOf('function boot('), smoke.indexOf('// Only method names,'));
  const child = new EventEmitter(), owned = [];
  child.pid = undefined;
  const context = vm.createContext({ assert, path, Date, owned, updateFixture: null, officialProbe: undefined,
    officialDiagnosticContext: { enabled: true, productionUpdate: true, platform: 'win32', ci: 'true', githubActions: 'true',
      sentinelVerified: true, temporaryInstallVerified: true, executable: path.resolve('synthetic', 'FundsTrace.exe') },
    productionTransportConfig: { version: '0.1.12' }, smokeStartedAt: Date.now(), diagnosticInspectorArguments, createOfficialNetworkProbe,
    createMainErrorMarkerProbe: () => ({ consume() {} }), spawn: () => child,
  });
  vm.runInContext(bootSource, context);
  let failure;
  assert.throws(() => {
    try { context.boot(path.resolve('synthetic', 'FundsTrace.exe'), path.resolve('synthetic'), {}, true); }
    catch (error) { failure = error; throw error; }
  }, /OFFICIAL_DIAGNOSTIC_BINDING/);
  assert.equal(failure.message, 'OFFICIAL_DIAGNOSTIC_BINDING');
  assert.equal(owned.length, 1);
  assert.equal(child.listenerCount('error'), 1, 'the child must be owned and error-handled before diagnostic validation can throw');
  const privateError = new Error('SYN_PRIVATE_PATH_AND_ARGUMENTS'); privateError.code = 'EACCES';
  queueMicrotask(() => child.emit('error', privateError));
  const exit = await owned[0].exited;
  assert.deepEqual(plain(exit), { code: null, signal: null });
  assert.equal(owned[0].done, true); assert.equal(owned[0].failed, true);
  assert.ok(!JSON.stringify({ message: failure.message, exit }).includes('SYN_PRIVATE'));
});

function collector({ ready = false } = {}) {
  let finishReady; const readyPromise = new Promise(resolve => { finishReady = resolve; });
  const registrations = [], removals = [], listeners = {};
  const webRequest = Object.fromEntries(['onResponseStarted', 'onCompleted', 'onErrorOccurred'].map(name => [name, (filter, listener) => {
    if (filter === null) { removals.push(name); delete listeners[name]; return; }
    registrations.push({ name, filter: plain(filter) }); listeners[name] = listener;
  }]));
  const context = vm.createContext({ URL, Date, process: { getBuiltinModule(name) {
    assert.equal(name, 'module'); return { createRequire(value) {
      assert.equal(value, packageJson); return name => {
        assert.equal(name, 'electron'); return { app: { isReady: () => ready, whenReady: () => readyPromise },
          session: { fromPartition(name, options) { assert.equal(name, 'electron-updater'); assert.deepEqual(plain(options), { cache: false }); return { webRequest }; } },
          get autoUpdater() { assert.fail('autoUpdater getter accessed'); } };
      };
    } };
  } } });
  const ack = vm.runInContext(registrationExpression(packageJson, '0.1.12'), context);
  return { ack: plain(ack), context, registrations, removals, listeners, finishReady };
}

test('collector acknowledges before readiness and installs one combined filter per passive event on the exact partition', async () => {
  const run = collector();
  assert.deepEqual(run.ack, { registered: true, appWasReady: false }); assert.equal(run.registrations.length, 0);
  run.finishReady(); await Promise.resolve();
  assert.deepEqual(run.registrations.map(value => value.name), ['onResponseStarted', 'onCompleted', 'onErrorOccurred']);
  const patterns = ['https://analytix.top/fundstrace/latest.yml', 'https://analytix.top/fundstrace/latest.yml?noCache=*',
    'https://download.analytix.top/releases/fundstrace/windows/0.1.12/FundsTrace-0.1.12-windows-x64-unsigned.exe'];
  for (const item of run.registrations) assert.deepEqual(item.filter.urls, patterns);
  assert.ok(!patterns.some(value => value.endsWith('latest.yml*')));
  assert.equal(collector({ ready: true }).ack.registered, false);
});

test('passive events read only fixed scalar status/error fields and retain at most 32 safe records', async () => {
  const run = collector(); run.finishReady(); await Promise.resolve();
  const details = new Proxy({ statusCode: 200, error: 'net::ERR_TIMED_OUT' }, { get(target, key) {
    if (!['statusCode', 'error'].includes(key)) assert.fail(`forbidden detail field ${String(key)}`); return target[key];
  } });
  run.listeners.onResponseStarted(details); run.listeners.onCompleted(details); run.listeners.onErrorOccurred(details);
  for (let index = 0; index < 40; index++) run.listeners.onErrorOccurred({ error: 'SYN_PRIVATE_ERROR', statusCode: 999 });
  const result = projectEvents(plain(run.context.__fundsTraceOfficialNetworkDiagnostic));
  assert.equal(result.events.length, 32); assert.equal(result.droppedCount, 11);
  assert.equal(result.events[0].netErrorCode, 'NONE'); assert.equal(result.events[2].netErrorCode, 'ERR_TIMED_OUT');
  assert.equal(result.events[3].netErrorCode, 'UNKNOWN'); assert.ok(!JSON.stringify(result).includes('SYN_PRIVATE'));
});

test('disposed-before-ready prevents delayed installation and installed hooks are all removed with null', async () => {
  const before = collector(); before.context.__fundsTraceOfficialNetworkDiagnostic.remove(); before.finishReady(); await Promise.resolve();
  assert.equal(before.registrations.length, 0);
  const after = collector(); after.finishReady(); await Promise.resolve(); after.context.__fundsTraceOfficialNetworkDiagnostic.remove();
  assert.deepEqual(after.removals, ['onResponseStarted', 'onCompleted', 'onErrorOccurred']);
});

test('public state snapshots never invent UNKNOWN outside a real error phase or change the original failed report', () => {
  const report = { status: 'failed', failedCheck: 'installedProductionUpdateAvailable' };
  report.initial = publicUpdateSnapshot({ phase: 'checking', errorCode: 'UNKNOWN', raw: 'SYN_PRIVATE' }, 0);
  report.late = publicUpdateSnapshot({ phase: 'available', errorCode: '' }, 1234);
  assert.equal(report.initial.errorCode, null); assert.equal(report.late.errorCode, null); assert.equal(report.status, 'failed');
  assert.equal(publicUpdateSnapshot({ phase: 'error', errorCode: 'UNKNOWN' }, 12).errorCode, 'UNKNOWN');
  assert.equal(publicUpdateSnapshot({ phase: 'error', errorCode: 'SYN_PRIVATE' }, 12).errorCode, null);
  assert.equal(publicUpdateSnapshot(undefined, 12).phase, null); assert.ok(!JSON.stringify(report).includes('SYN_PRIVATE'));
});

function connection({ failRegister = false, binding = () => true, budget = 2000, identity = true } = {}) {
  const calls = [], sockets = [], timers = [];
  class Socket {
    static OPEN = 1;
    constructor(uri) { assert.match(uri, /^ws:\/\/127\.0\.0\.1:12345\//); this.readyState = 1; this.handlers = {}; sockets.push(this); queueMicrotask(() => this.emit('open')); }
    addEventListener(name, action) { (this.handlers[name] ||= []).push(action); }
    emit(name, value) { for (const action of this.handlers[name] || []) action(value); }
    send(raw) {
      const value = JSON.parse(raw); calls.push(value);
      let result = {};
      if (value.method === 'Runtime.evaluate') result = { result: { value: value.params.expression.startsWith('process.pid===') ? identity : value.params.expression.includes('state.remove=')
        ? { registered: !failRegister, appWasReady: false } : value.params.expression.includes('s.remove()') ? true
          : { registered: true, installed: true, disposed: false, appWasReady: false, droppedCount: 0, events: [] } } };
      queueMicrotask(() => this.emit('message', { data: JSON.stringify({ id: value.id, result }) }));
    }
    close() { this.readyState = 3; this.emit('close'); }
  }
  const module = { exports: {} };
  const context = vm.createContext({ module, require, path, URL, WebSocket: Socket, Date,
    setTimeout(callback, milliseconds) { timers.push({ callback, milliseconds }); return timers.length; }, clearTimeout() {} });
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'installed-official-network-probe.cjs'), 'utf8'), context);
  const probe = module.exports.createOfficialNetworkProbe({ binding, packageJson, version: '0.1.12', expectedPid: 42,
    executable: path.resolve('synthetic', 'FundsTrace.exe'), budgetMs: () => budget, WebSocketImpl: Socket });
  probe.consume('SYN_PRIVATE_OTHER_STDERR\nDebugger listening on ws://127.0.0.1:12345/1234-abc\n');
  return { probe, calls, sockets, timers };
}

test('only the owned loopback stderr endpoint is attached, registration precedes resume, and teardown precedes detach', async () => {
  const run = connection(); await run.probe.attach();
  assert.equal(run.calls[0].method, 'Runtime.evaluate'); assert.ok(run.calls[0].params.expression.startsWith('process.pid==='));
  assert.equal(run.calls[1].method, 'Runtime.evaluate'); assert.equal(run.calls[2].method, 'Runtime.runIfWaitingForDebugger');
  await run.probe.read(); const result = await run.probe.dispose();
  assert.equal(result.teardownSucceeded, true); assert.equal(result.detached, true);
  assert.ok(run.timers.every(value => value.milliseconds <= 2000));
  assert.ok(!JSON.stringify(result).includes('ws://')); assert.ok(!JSON.stringify(result).includes('SYN_PRIVATE'));
});

test('failed registration still detaches, and rejected ownership or exhausted budgets never connect', async () => {
  const failure = connection({ failRegister: true }); await assert.rejects(failure.probe.attach(), /OFFICIAL_DIAGNOSTIC_UNAVAILABLE/);
  assert.equal((await failure.probe.dispose()).detached, true);
  const rejected = connection({ binding: () => false }); await assert.rejects(rejected.probe.attach(), /OFFICIAL_DIAGNOSTIC_BINDING/); assert.equal(rejected.sockets.length, 0);
  const noBudget = connection({ budget: 0 }); await assert.rejects(noBudget.probe.attach(), /OFFICIAL_DIAGNOSTIC_UNAVAILABLE/); assert.equal(noBudget.sockets.length, 0);
  const wrongMain = connection({ identity: false }); await assert.rejects(wrongMain.probe.attach(), /OFFICIAL_DIAGNOSTIC_BINDING/);
  assert.equal((await wrongMain.probe.read()).observation, null);
  assert.equal((await wrongMain.probe.dispose()).detached, true);
  assert.equal(wrongMain.calls.length, 1, 'a mismatched Main must not load electron, register hooks or receive resume commands');
});
