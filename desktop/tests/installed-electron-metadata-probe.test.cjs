'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { PassThrough, Writable } = require('node:stream');
const { emptyProbe, allowedUrl } = require('./electron-metadata-probe-main.cjs');
const { safeProbeResult } = require('./installed-electron-metadata-probe.cjs');
const plain = value => JSON.parse(JSON.stringify(value));
const expected = { version: '0.1.12', installerSha512: 'A'.repeat(86) + '==', installerBytes: 123 };
const metadata = () => ({ product: 'com.eysn.fundstrace', version: expected.version,
  files: [{ url: 'https://download.analytix.top/releases/fundstrace/windows/0.1.12/FundsTrace-0.1.12-windows-x64-unsigned.exe',
    sha512: expected.installerSha512, size: expected.installerBytes }] });

function loadMain() {
  const module = { exports: {} }, timers = [];
  const context = vm.createContext({ module, require, URL, Buffer, Date,
    setTimeout(callback, milliseconds) { timers.push({ callback, milliseconds }); return timers.length; }, clearTimeout() {} });
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'electron-metadata-probe-main.cjs'), 'utf8'), context);
  return { ...module.exports, timers };
}
async function mainRun(action, target = expected) {
  const loaded = loadMain(), requests = [], emitted = [];
  let constructor, headers, executor;
  class Executor {
    createRequest(options, callback) {
      const request = new EventEmitter(); request.aborted = false; request.abort = () => { request.aborted = true; };
      requests.push(request); request.receive = callback; return request;
    }
    addRedirectHandlers(request, options, reject, count, handler) { request.on('redirect', (_status, _method, url) => handler({ protocol: 'https:', hostname: new URL(url).hostname, path: new URL(url).pathname + new URL(url).search })); }
  }
  class Provider {
    constructor(config, updater, runtime) { constructor = { config, updater, runtime }; executor = runtime.executor; }
    setRequestHeaders(value) { headers = value; }
    getLatestVersion() { return action({ executor, requests, timers: loaded.timers }); }
  }
  const promise = loaded.runMetadataProbe({ app: { whenReady: async () => {} }, GenericProvider: Provider,
    ElectronHttpExecutor: Executor, expected: target, profileIsolated: true, emit: value => emitted.push(plain(value)) });
  await promise;
  return { emitted, requests, timers: loaded.timers, constructor, headers };
}
const options = () => ({ protocol: 'https:', hostname: 'analytix.top', path: '/fundstrace/latest.yml?noCache=abc123' });

test('copied Electron Main uses only original Provider APIs and fixed public metadata shape', async () => {
  const run = await mainRun(async ({ executor }) => {
    const request = executor.createRequest(options(), () => {});
    request.emit('finish'); request.emit('socket');
    const response = new EventEmitter(); response.statusCode = 200; request.receive(response);
    response.emit('data', Buffer.from('SYN_PRIVATE_BODY')); request.emit('close');
    return metadata();
  });
  assert.equal(run.timers[0].milliseconds, 45000);
  assert.equal(run.constructor.config.url, 'https://analytix.top/fundstrace/');
  assert.equal(run.constructor.updater.isAddNoCacheQuery, true);
  assert.deepEqual(plain(run.headers), { 'User-Agent': 'electron-builder', 'Cache-Control': 'no-cache' });
  const result = run.emitted[0];
  assert.equal(result.status, 'completed'); assert.equal(result.providerResolved, true);
  assert.equal(result.metadataValid, true); assert.equal(result.targetMatch, true);
  assert.equal(result.requestCount, 1); assert.equal(result.finishCount, 1); assert.equal(result.responseCount, 1);
  assert.equal(result.closeCount, 1); assert.equal(result.socketObserved, true);
  assert.equal(result.transport, 'independentCopiedElectronRuntime'); assert.equal(result.businessStartup, false);
  assert.ok(!JSON.stringify(result).includes('SYN_PRIVATE'));
});

test('HTTP/Provider completion, valid metadata and optional target matching remain separate', async () => {
  const noTarget = await mainRun(async () => metadata(), undefined);
  // The direct Main API also supports callers which deliberately omit a target.
  const loaded = loadMain(), values = [];
  await loaded.runMetadataProbe({ app: { whenReady: async () => {} }, GenericProvider: class {
    setRequestHeaders() {} async getLatestVersion() { return metadata(); }
  }, ElectronHttpExecutor: class {}, emit: value => values.push(value) });
  assert.equal(values[0].targetMatch, null);
  const wrongTarget = await mainRun(async () => metadata(), { ...expected, version: '0.1.13' });
  assert.equal(wrongTarget.emitted[0].metadataValid, true); assert.equal(wrongTarget.emitted[0].targetMatch, false);
  const invalid = await mainRun(async () => ({ raw: 'SYN_PRIVATE' }));
  assert.equal(invalid.emitted[0].providerResolved, true); assert.equal(invalid.emitted[0].metadataValid, false);
  assert.equal(invalid.emitted[0].targetMatch, false);
  assert.equal(noTarget.emitted.length, 1);
});

test('login observation never reads authInfo or invokes the credentials callback', async () => {
  let credentialCallback = false;
  const run = await mainRun(async ({ executor, timers }) => {
    const request = executor.createRequest(options(), () => {});
    request.emit('login', new Proxy({}, { get() { assert.fail('authInfo read'); } }), () => { credentialCallback = true; });
    timers[0].callback(); return metadata();
  });
  assert.equal(credentialCallback, false); assert.equal(run.emitted[0].loginObserved, true);
  assert.equal(run.emitted[0].status, 'timeout'); assert.equal(run.requests[0].aborted, true);
});

test('unknown redirects are rejected before another network request and never emitted raw', async () => {
  const run = await mainRun(async ({ executor }) => {
    const request = executor.createRequest(options(), () => {});
    executor.addRedirectHandlers(request, options(), () => {}, 0, () => assert.fail('foreign request forwarded'));
    request.emit('redirect', 302, 'GET', 'https://SYN_PRIVATE_HOST/fundstrace/latest.yml?noCache=abc123');
    return metadata();
  });
  assert.equal(run.emitted[0].unknownRedirectRejected, true); assert.equal(run.emitted[0].redirectCount, 1);
  assert.equal(run.emitted[0].lastRedirectStatus, 302); assert.equal(run.requests.length, 1);
  assert.ok(!JSON.stringify(run.emitted).includes('SYN_PRIVATE'));
});

test('the metadata Main aborts all owned requests after at most sixteen creations', async () => {
  const run = await mainRun(async ({ executor }) => {
    for (let index = 0; index < 17; index++) executor.createRequest(options(), () => {});
    return metadata();
  });
  assert.equal(run.requests.length, 16); assert.equal(run.emitted[0].requestLimitExceeded, true);
  assert.ok(run.requests.every(request => request.aborted));
});

test('body observations are bounded and unsafe exception details never enter a probe result', async () => {
  const run = await mainRun(async ({ executor }) => {
    const request = executor.createRequest(options(), () => {}), response = new EventEmitter(); response.statusCode = 200;
    request.receive(response); response.emit('data', Buffer.alloc(65537));
    throw { code: 'SYN_PRIVATE_ERROR', message: 'SYN_PRIVATE_URL' };
  });
  assert.equal(run.emitted[0].bodyLimitExceeded, true); assert.equal(run.emitted[0].responseBytes, 65537);
  assert.ok(run.requests[0].aborted); assert.ok(!JSON.stringify(run.emitted).includes('SYN_PRIVATE'));
});

test('Chromium network errors use only exact allowlisted codes or net::code messages', async () => {
  for (const code of ['ERR_TIMED_OUT', 'ERR_CONNECTION_TIMED_OUT', 'ERR_PROXY_CONNECTION_FAILED', 'ERR_TUNNEL_CONNECTION_FAILED',
    'ERR_NO_SUPPORTED_PROXIES', 'ERR_PROXY_AUTH_UNSUPPORTED', 'ERR_NETWORK_CHANGED', 'ERR_ADDRESS_UNREACHABLE', 'ERR_SSL_PROTOCOL_ERROR']) {
    const run = await mainRun(async () => { throw { message: `net::${code}` }; });
    assert.equal(run.emitted[0].errorCode, code); assert.ok(safeProbeResult(run.emitted[0]));
  }
  for (const message of ['net::ERR_TIMED_OUT SYN_PRIVATE', 'SYN_PRIVATE net::ERR_PROXY_CONNECTION_FAILED', 'net::SYN_PRIVATE']) {
    const run = await mainRun(async () => { throw { message }; });
    assert.equal(run.emitted[0].errorCode, 'UNKNOWN'); assert.ok(!JSON.stringify(run.emitted).includes('SYN_PRIVATE'));
  }
});

test('allowed metadata URLs require the single internally bound noCache value', () => {
  assert.equal(allowedUrl('https://analytix.top/fundstrace/latest.yml?noCache=abc123', 'abc123'), true);
  for (const value of ['http://analytix.top/fundstrace/latest.yml?noCache=abc123', 'https://other.invalid/fundstrace/latest.yml?noCache=abc123',
    'https://analytix.top/other?noCache=abc123', 'https://analytix.top/fundstrace/latest.yml',
    'https://analytix.top/fundstrace/latest.yml?noCache=abc123&auth=SYN_PRIVATE',
    'https://analytix.top/fundstrace/latest.yml?noCache=different']) assert.equal(allowedUrl(value, 'abc123'), false);
});

test('parent projection strips unknown fields and rejects every unsafe enum, count or type', () => {
  const result = { ...emptyProbe(), raw: 'SYN_PRIVATE', pid: 123, url: 'SYN_PRIVATE' };
  assert.ok(!JSON.stringify(safeProbeResult(result)).includes('SYN_PRIVATE'));
  for (const changed of [{ status: 'passed' }, { stage: 'SYN_PRIVATE' }, { requestCount: 17 }, { responseBytes: 65538 },
    { targetMatch: 'true' }, { errorCode: 'SYN_PRIVATE' }, { elapsedMs: 60001 }, { businessStartup: true }]) assert.equal(safeProbeResult({ ...result, ...changed }), null);
});

function loadWrapper({ failCleanup = false, pending = false, childUnavailable = false, taskkillCode = 0 } = {}) {
  const module = { exports: {} }, spawned = [], removed = [], copied = [], writes = [], timers = new Map();
  let nextTimer = 0;
  const mockFs = {
    mkdtemp: async () => '/synthetic/temp/fundstrace-metadata-probe-owned',
    rm: async value => { removed.push(value); if (failCleanup) throw new Error('SYN_PRIVATE_CLEANUP'); },
    mkdir: async () => {}, copyFile: async (...args) => copied.push(args), writeFile: async (...args) => writes.push(args),
    readdir: async () => ['en-US.pak', 'zh-CN.pak', 'SYN_PRIVATE.txt'],
    lstat: async value => {
      if (/FundsTrace\.exe$|icudtl\.dat$|resources\.pak$|en-US\.pak$|zh-CN\.pak$/.test(value)) return { isFile: () => true, isSymbolicLink: () => false };
      throw Object.assign(new Error('SYN_PRIVATE_MISSING'), { code: 'ENOENT' });
    },
  };
  const context = vm.createContext({ module, Buffer, Date, __dirname, __filename: path.join(__dirname, 'installed-electron-metadata-probe.cjs'),
    process: { platform: 'win32', env: { GITHUB_ACTIONS: 'true', TEMP: '/synthetic/temp',
      get ELECTRON_NO_ASAR() { assert.fail('excluded env value read'); },
      get electron_no_asar() { assert.fail('excluded env value read'); },
      get NODE_OPTIONS() { assert.fail('excluded env value read'); },
      get FUNDS_METADATA_PROBE_CONFIG() { assert.fail('excluded env value read'); },
      get FUND_SYN_PRIVATE() { assert.fail('excluded env value read'); },
      get FUNDS_TRACE_SYN_PRIVATE() { assert.fail('excluded env value read'); } }, execPath: '/synthetic/node.exe', argv: [] },
    setTimeout(callback, milliseconds) { const id = ++nextTimer; timers.set(id, { callback, milliseconds }); return id; },
    clearTimeout(id) { timers.delete(id); },
    require(name) {
      if (name === 'node:fs/promises') return mockFs;
      if (name !== 'node:child_process') return require(name);
      return { spawn(command, args, options) {
        const child = new EventEmitter(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
        child.exitCode = null; child.signalCode = null; child.pid = 9876; child.stopMessages = 0;
        child.send = value => { assert.deepEqual(plain(value), { stop: true }); child.stopMessages++; }; child.kill = () => {};
        if (command === 'taskkill.exe') {
          queueMicrotask(() => {
            const original = spawned[0].child; original.exitCode = 1; original.emit('exit', 1); original.emit('close', 1);
            child.exitCode = taskkillCode; child.emit('close', taskkillCode);
          });
          spawned.push({ command, args, options, child }); return child;
        }
        child.stdin = new Writable({ write(chunk, _encoding, callback) {
          const config = JSON.parse(chunk.toString());
          assert.equal(Object.hasOwn(config, 'executable'), false);
          if (!pending) queueMicrotask(() => {
            child.stdout.write('SYN_PRIVATE_UNSTRUCTURED\n');
            child.stderr.write('SYN_PRIVATE_STDERR');
            child.stdout.write(`FT_ELECTRON_METADATA ${JSON.stringify({ ...emptyProbe(), profileIsolated: true,
              status: childUnavailable ? 'unavailable' : 'completed', raw: 'SYN_PRIVATE' })}\n`);
            child.exitCode = 0; child.emit('exit', 0); child.emit('close', 0);
          }); callback();
        } });
        spawned.push({ command, args, options, child }); return child;
      } };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'installed-electron-metadata-probe.cjs'), 'utf8'), context);
  return { ...module.exports, spawned, removed, copied, writes, timers };
}

test('parent launches only its fixed worker, projects safe JSON, and removes only its owned temporary copy', async () => {
  const loaded = loadWrapper(), result = await loaded.safeInstalledElectronMetadataProbe({ installRoot: '/synthetic/install', ...expected });
  assert.equal(loaded.spawned.length, 1); assert.equal(loaded.spawned[0].command, '/synthetic/node.exe');
  assert.equal(loaded.spawned[0].args[1], '--metadata-probe-worker');
  assert.equal(result.cleanupSucceeded, true); assert.equal(result.workerExited, true);
  assert.equal(result.treeTerminationSucceeded, null);
  assert.equal(result.stderrObserved, true); assert.equal(result.profileIsolated, true);
  assert.deepEqual(loaded.removed, ['/synthetic/temp/fundstrace-metadata-probe-owned']);
  assert.ok(!JSON.stringify(result).includes('SYN_PRIVATE')); assert.ok(!JSON.stringify(result).includes('/synthetic'));
});

test('cleanup failure makes the diagnostic unavailable despite a successful child report', async () => {
  const loaded = loadWrapper({ failCleanup: true }), result = await loaded.safeInstalledElectronMetadataProbe({ installRoot: '/synthetic/install' });
  assert.equal(result.status, 'unavailable'); assert.equal(result.reason, 'cleanup'); assert.equal(result.cleanupSucceeded, false);
});

test('a copied runtime with unavailable dependency APIs remains unavailable rather than a completed probe', async () => {
  const loaded = loadWrapper({ childUnavailable: true }), result = await loaded.safeInstalledElectronMetadataProbe({ installRoot: '/synthetic/install' });
  assert.equal(result.status, 'unavailable'); assert.equal(result.reason, 'unavailable');
  assert.equal(result.probe.status, 'unavailable'); assert.equal(result.cleanupSucceeded, true);
});

test('the bounded parent preserves the live descendant tree until taskkill and never kills Electron first', async () => {
  const loaded = loadWrapper({ pending: true });
  const pending = loaded.safeInstalledElectronMetadataProbe({ installRoot: '/synthetic/install' });
  await new Promise(resolve => setImmediate(resolve));
  const timer = [...loaded.timers.values()].find(value => value.milliseconds <= 55000 && value.milliseconds >= 50000);
  assert.ok(timer); timer.callback();
  const result = await pending;
  assert.equal(loaded.spawned.length, 2); assert.equal(loaded.spawned[1].command, 'taskkill.exe');
  assert.deepEqual(plain(loaded.spawned[1].args), ['/PID', '9876', '/T', '/F']);
  assert.equal(loaded.spawned[0].child.stopMessages, 0, 'no stop message may kill Electron before tree termination');
  assert.equal(result.treeTerminationSucceeded, true);
  assert.equal(result.status, 'unavailable'); assert.equal(result.workerExited, true); assert.equal(result.cleanupSucceeded, true);
  const source = fs.readFileSync(path.join(__dirname, 'installed-electron-metadata-probe.cjs'), 'utf8');
  assert.ok(!source.includes("process.on('message'")); assert.ok(!source.includes('child.send('));
});

test('a failed tree termination stays unavailable even when the worker exited and temporary cleanup succeeded', async () => {
  const loaded = loadWrapper({ pending: true, taskkillCode: 1 });
  const pending = loaded.safeInstalledElectronMetadataProbe({ installRoot: '/synthetic/install' });
  await new Promise(resolve => setImmediate(resolve));
  [...loaded.timers.values()].find(value => value.milliseconds >= 50000).callback();
  const result = await pending;
  assert.equal(result.workerExited, true); assert.equal(result.cleanupSucceeded, true);
  assert.equal(result.treeTerminationSucceeded, false); assert.equal(result.status, 'unavailable'); assert.equal(result.reason, 'cleanup');
});

test('a budget below the required sixty seconds starts no worker and does not touch the original installation', async () => {
  const loaded = loadWrapper(), result = await loaded.safeInstalledElectronMetadataProbe({ installRoot: '/synthetic/install' }, { budgetMs: 59999 });
  assert.equal(result.status, 'notRun'); assert.equal(result.reason, 'budgetInsufficient');
  assert.equal(loaded.spawned.length, 0); assert.equal(loaded.removed.length, 0); assert.equal(loaded.copied.length, 0);
});

test('both diagnostic child environments exclude product and Electron overrides before values are read', () => {
  const loaded = loadWrapper(), env = loaded.probeEnvironment('/synthetic/owned/configuration.json');
  assert.deepEqual(plain(env), { GITHUB_ACTIONS: 'true', TEMP: '/synthetic/temp', FUNDS_METADATA_PROBE_CONFIG: '/synthetic/owned/configuration.json' });
  const source = fs.readFileSync(path.join(__dirname, 'installed-electron-metadata-probe.cjs'), 'utf8');
  assert.ok(source.includes('`--user-data-dir=${userData}`'));
  assert.ok(!source.includes('--no-proxy-server')); assert.ok(!source.includes('--no-sandbox'));
});

test('runtime copying is restricted to fixed root files, locale PAKs and the diagnostic Main', async () => {
  const loaded = loadWrapper(); await loaded.copyRuntime('/synthetic/install', '/synthetic/owned', Date.now() + 10000);
  assert.ok(loaded.copied.some(([original]) => original === '/synthetic/install/FundsTrace.exe'));
  assert.ok(loaded.copied.some(([original]) => original.endsWith('/electron-metadata-probe-main.cjs')));
  assert.ok(!loaded.copied.some(([original]) => original.includes('app.asar') || original.includes('backend') || original.includes('SYN_PRIVATE')));
  assert.ok(loaded.writes.some(([target]) => target.endsWith('/resources/app/package.json')));
});
