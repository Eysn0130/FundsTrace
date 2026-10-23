'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createHash } = require('node:crypto');
const { EventEmitter } = require('node:events');

const filename = path.join(__dirname, 'installed-update-transport-diagnostic.cjs');
const source = fs.readFileSync(filename, 'utf8');
const installer = Buffer.from('MZ SYNTHETIC INSTALLER TRANSPORT BYTES');
const config = { version: '0.1.12', currentVersion: '0.1.11', installerBytes: installer.length,
  installerSha256: createHash('sha256').update(installer).digest('hex'),
  installerSha512: createHash('sha512').update(installer).digest('base64') };
const installerUrl = 'https://download.analytix.top/releases/fundstrace/windows/0.1.12/FundsTrace-0.1.12-windows-x64-unsigned.exe';
const metadata = () => ({ product: 'com.eysn.fundstrace', version: config.version,
  files: [{ url: installerUrl, size: config.installerBytes, sha512: config.installerSha512 }] });
const json = value => Buffer.from(JSON.stringify(value));
const plain = value => JSON.parse(JSON.stringify(value));

function harness(plans, environment = {}, initialClock = 0) {
  const requests = [], scheduled = [], active = new Map();
  let clock = initialClock, timerId = 0;
  const module = { exports: {} };
  const context = vm.createContext({ module, Buffer, Date: { now: () => clock }, process: { env: environment },
    setTimeout(callback, milliseconds) {
      const id = ++timerId; scheduled.push(milliseconds); active.set(id, { callback, milliseconds }); return id;
    },
    clearTimeout(id) { active.delete(id); },
    require(name) {
      if (name !== 'node:https') return require(name);
      return { get(...args) {
        assert.equal(args.length, 3);
        const [url, options, callback] = args, plan = plans[requests.length];
        const isMetadata = requests.length === 0;
        assert.deepEqual(plain(options), { headers: { 'User-Agent': 'electron-builder', 'Cache-Control': 'no-cache',
          ...(isMetadata ? {} : { accept: '*/*' }) } }, 'only the fixed public updater headers, with no credentials, proxy or TLS options');
        if (isMetadata) assert.match(url, /^https:\/\/analytix\.top\/fundstrace\/latest\.yml\?noCache=[0-9a-v]+$/);
        else assert.equal(url, installerUrl);
        assert.ok(plan, 'unexpected network request, including redirects');
        const request = new EventEmitter();
        request.destroyed = false; request.destroy = () => { request.destroyed = true; };
        const entry = { url, options, request }; requests.push(entry);
        if (plan.syncError) throw plan.syncError;
        queueMicrotask(() => {
          clock += plan.elapsedMs ?? 11;
          if (plan.requestError) { request.emit('error', plan.requestError); return; }
          const timeout = () => {
            const timer = active.values().next().value;
            assert.ok(timer); clock += timer.milliseconds; timer.callback();
          };
          if (plan.timeout === 'connect') { timeout(); return; }
          const socket = new EventEmitter(); request.emit('socket', socket); socket.emit('secureConnect');
          if (plan.timeout === 'response') { timeout(); return; }
          const response = new EventEmitter();
          response.statusCode = plan.statusCode ?? 200;
          response.headers = plan.headers || {};
          response.destroyed = false; response.destroy = () => { response.destroyed = true; };
          entry.response = response; callback(response);
          for (const chunk of plan.chunks || [plan.body || Buffer.alloc(0)]) {
            if (response.destroyed) break;
            clock += 3; response.emit('data', chunk);
          }
          if (response.destroyed) return;
          if (plan.timeout === 'read') { timeout(); return; }
          if (plan.responseError) { response.emit('error', plan.responseError); return; }
          if (plan.aborted) { response.emit('aborted'); return; }
          if (plan.prematureClose) { response.emit('close'); return; }
          response.emit('end'); response.emit('close');
        });
        return request;
      } };
    },
  });
  vm.runInContext(source, context, { filename });
  return { requests, scheduled, active, run: (value, options) => module.exports.safeProductionTransportDiagnostic(value, options) };
}

test('post-failure transport reads both fixed endpoints fully and verifies both installer digests', async () => {
  const body = json(metadata());
  const run = harness([{ chunks: [body.subarray(0, 13), body.subarray(13)] },
    { chunks: [installer.subarray(0, 3), installer.subarray(3, 16), installer.subarray(16)] }]);
  let versionReads = 0;
  const report = plain(await run.run({ ...config, get version() { versionReads++; return config.version; },
    url: 'https://SYN_PRIVATE_URL', authorization: 'SYN_PRIVATE_HEADER' }));
  assert.equal(versionReads, 1);
  assert.equal(report.kind, 'completed'); assert.equal(report.configurationValid, true);
  assert.equal(report.metadata.kind, 'verified'); assert.equal(report.installer.kind, 'verified');
  assert.equal(report.metadata.bytes, body.length); assert.equal(report.installer.bytes, installer.length);
  assert.equal(report.metadata.completed, true); assert.equal(report.installer.completed, true);
  for (const key of ['expectedSizeMatch', 'sha256Match', 'sha512Match']) assert.equal(report.installer[key], true);
  assert.equal(report.metadata.versionMatch, true); assert.equal(report.metadata.installerUrlMatch, true);
  assert.deepEqual(run.requests.map(value => value.url), ['https://analytix.top/fundstrace/latest.yml?noCache=0', installerUrl]);
  assert.deepEqual(run.scheduled, [45000, 240000]); assert.equal(run.active.size, 0);
  assert.ok(!JSON.stringify(report).includes('SYN_PRIVATE'));
  assert.ok(!JSON.stringify(report).includes('https://'));
  assert.equal(report.transport, 'independentNodeHttps'); assert.equal(report.metadataNoCacheQuery, true);
});

test('metadata noCache uses only the internal base-32 timestamp and request headers remain public constants', async () => {
  const run = harness([{ body: json(metadata()) }, { body: installer }], {}, 12345);
  const report = await run.run({ ...config, noCache: 'SYN_PRIVATE_QUERY',
    headers: { Authorization: 'SYN_PRIVATE_TOKEN', 'User-Agent': 'SYN_PRIVATE_AGENT' } });
  assert.equal(run.requests[0].url, `https://analytix.top/fundstrace/latest.yml?noCache=${(12345).toString(32)}`);
  assert.equal(run.requests[1].url, installerUrl);
  assert.equal(new URL(run.requests[1].url).search, '');
  assert.ok(!JSON.stringify(report).includes('SYN_PRIVATE'));
  assert.ok(!JSON.stringify(report).includes('electron-builder'));
  assert.ok(!JSON.stringify(report).includes('Cache-Control'));
});

test('complete HTTP bodies with invalid metadata or incorrect digests stay distinct from transport failure', async () => {
  const wrong = Buffer.from(installer); wrong[3] ^= 1;
  const run = harness([{ body: Buffer.from('SYN_PRIVATE_INVALID_METADATA') }, { body: wrong }]);
  const report = await run.run(config);
  assert.equal(report.metadata.kind, 'invalidMetadata'); assert.equal(report.metadata.completed, true);
  assert.equal(report.metadata.metadataValid, false);
  assert.equal(report.installer.kind, 'mismatch'); assert.equal(report.installer.completed, true);
  assert.equal(report.installer.expectedSizeMatch, true);
  assert.equal(report.installer.sha256Match, false); assert.equal(report.installer.sha512Match, false);
  assert.ok(!JSON.stringify(report).includes('SYN_PRIVATE'));
});

test('redirect and HTTP error bodies are counted without following Location or exposing headers', async () => {
  const run = harness([{ statusCode: 302, headers: { location: 'https://SYN_PRIVATE_REDIRECT', authorization: 'SYN_PRIVATE' },
    body: Buffer.from('SYN_PRIVATE_REDIRECT_BODY') }, { statusCode: 403, body: installer }]);
  const report = await run.run(config);
  assert.equal(run.requests.length, 2);
  assert.equal(report.metadata.kind, 'httpError'); assert.equal(report.metadata.statusCode, 302);
  assert.equal(report.metadata.completed, true);
  assert.equal(report.installer.kind, 'httpError'); assert.equal(report.installer.statusCode, 403);
  assert.equal(report.installer.completed, true); assert.equal(report.installer.sha256Match, true);
  assert.ok(!JSON.stringify(report).includes('SYN_PRIVATE'));
});

test('metadata above 64 KiB and installers above the expected size abort before further chunks', async () => {
  const run = harness([{ chunks: [Buffer.alloc(65536), Buffer.alloc(1), Buffer.alloc(99)] },
    { chunks: [installer, Buffer.alloc(1), Buffer.alloc(99)] }]);
  const report = await run.run(config);
  assert.equal(report.metadata.kind, 'tooLarge'); assert.equal(report.metadata.bytes, 65537);
  assert.equal(report.installer.kind, 'tooLarge'); assert.equal(report.installer.bytes, installer.length + 1);
  for (const key of ['metadata', 'installer']) {
    assert.equal(report[key].completed, false); assert.equal(report[key].expectedSizeMatch, false);
  }
  for (const entry of run.requests) { assert.equal(entry.request.destroyed, true); assert.equal(entry.response.destroyed, true); }
});

test('a short but normally completed installer reports mismatch instead of success', async () => {
  const run = harness([{ body: json(metadata()) }, { body: installer.subarray(0, -1) }]);
  const report = await run.run(config);
  assert.equal(report.installer.kind, 'mismatch'); assert.equal(report.installer.completed, true);
  assert.equal(report.installer.expectedSizeMatch, false); assert.equal(report.installer.sha512Match, false);
});

for (const mode of ['aborted', 'prematureClose', 'responseError']) {
  test(`incomplete installer ${mode} remains a transport failure`, async () => {
    const plan = { body: installer.subarray(0, 5), [mode]: mode === 'responseError' ? { code: 'ECONNRESET', message: 'SYN_PRIVATE' } : true };
    const run = harness([{ body: json(metadata()) }, plan]);
    const report = await run.run(config);
    assert.equal(report.installer.kind, 'transportError'); assert.equal(report.installer.completed, false);
    assert.equal(report.installer.errorCode, mode === 'responseError' ? 'ECONNRESET' : 'ERR_STREAM_PREMATURE_CLOSE');
    assert.equal(report.installer.sha256Match, null); assert.equal(report.installer.sha512Match, null);
    assert.ok(!JSON.stringify(report).includes('SYN_PRIVATE'));
  });
}

for (const [mode, expectedKind] of [['connect', 'connectTimeout'], ['response', 'responseTimeout'], ['read', 'readTimeout']]) {
  test(`the original diagnostic total deadlines distinguish ${mode} timeout`, async () => {
    const run = harness([{ timeout: mode, body: Buffer.from('{') }, { body: installer }]);
    const report = await run.run(config);
    assert.equal(run.scheduled[0], 45000);
    assert.equal(run.scheduled[1], 285000 - report.metadata.elapsedMs);
    assert.equal(report.metadata.kind, expectedKind); assert.equal(report.metadata.errorCode, 'ETIMEDOUT');
    assert.equal(report.metadata.completed, false); assert.ok(report.metadata.elapsedMs >= 45000 && report.metadata.elapsedMs <= 300000);
    assert.equal(report.metadata.responseStarted, mode === 'read');
    assert.equal(report.metadata.tlsEstablished, mode !== 'connect');
    assert.equal(report.installer.kind, 'verified');
  });
}

for (const [mode, expectedKind] of [['connect', 'connectTimeout'], ['response', 'responseTimeout'], ['read', 'readTimeout']]) {
  test(`installer ${mode} timeout retains its 240-second maximum when the shared budget allows it`, async () => {
    const run = harness([{ body: json(metadata()) }, { timeout: mode, body: installer.subarray(0, 2) }]);
    const report = await run.run(config);
    assert.deepEqual(run.scheduled, [45000, 240000]);
    assert.equal(report.installer.kind, expectedKind); assert.equal(report.installer.errorCode, 'ETIMEDOUT');
    assert.equal(report.installer.completed, false); assert.equal(report.installer.budgetLimited, false);
    assert.ok(report.installer.elapsedMs >= 240000 && report.installer.elapsedMs <= 300000);
  });
}

test('an exhausted shared budget sends no requests and reports that neither endpoint ran', async () => {
  const run = harness([]), report = await run.run(config, { budgetMs: 0 });
  assert.equal(report.kind, 'budgetExhausted'); assert.equal(run.requests.length, 0);
  for (const key of ['metadata', 'installer']) {
    assert.equal(report[key].kind, 'budgetExhausted'); assert.equal(report[key].responseStarted, false);
    assert.equal(report[key].bytes, 0); assert.equal(report[key].completed, false);
  }
});

test('a metadata timeout limited by the shared budget does not start an installer request', async () => {
  const run = harness([{ timeout: 'connect' }]), report = await run.run(config, { budgetMs: 1000 });
  assert.deepEqual(run.scheduled, [1000]); assert.equal(run.requests.length, 1);
  assert.equal(report.kind, 'budgetExhausted'); assert.equal(report.metadata.kind, 'budgetExhausted');
  assert.equal(report.metadata.budgetLimited, true); assert.equal(report.metadata.errorCode, 'NONE');
  assert.equal(report.installer.kind, 'budgetExhausted'); assert.equal(report.installer.responseStarted, false);
});

test('metadata time consumes the same shared budget before the installer timer is created', async () => {
  const run = harness([{ body: json(metadata()), elapsedMs: 100 }, { timeout: 'read', body: installer.subarray(0, 2) }]);
  const report = await run.run(config, { budgetMs: 1000 });
  assert.deepEqual(run.scheduled, [1000, 897]);
  assert.equal(report.metadata.kind, 'verified'); assert.equal(report.installer.kind, 'budgetExhausted');
  assert.equal(report.installer.budgetLimited, true); assert.equal(report.installer.completed, false);
  assert.equal(report.installer.errorCode, 'NONE');
});

test('the default shared budget also caps two full endpoint timeout attempts', async () => {
  const run = harness([{ timeout: 'connect' }, { timeout: 'read' }]), report = await run.run(config);
  assert.deepEqual(run.scheduled, [45000, 239989]);
  assert.equal(report.metadata.kind, 'connectTimeout'); assert.equal(report.installer.kind, 'budgetExhausted');
  assert.equal(report.installer.budgetLimited, true);
});

test('invalid shared budgets are rejected before any network request', async () => {
  for (const budgetMs of [-1, 285001, 0.5, '1000', Infinity, NaN]) {
    const run = harness([]), report = await run.run(config, { budgetMs });
    assert.equal(report.kind, 'budgetRejected'); assert.equal(run.requests.length, 0);
  }
});

test('only allowlisted transport codes survive; raw request exceptions and unknown codes never enter output', async () => {
  const run = harness([{ requestError: { code: 'CERT_HAS_EXPIRED', message: 'SYN_PRIVATE_CERT_PATH' } },
    { requestError: { code: 'SYN_PRIVATE_CODE', message: 'https://SYN_PRIVATE_HOST', stack: 'SYN_PRIVATE' } }]);
  const report = await run.run(config);
  assert.equal(report.metadata.errorCode, 'CERT_HAS_EXPIRED'); assert.equal(report.installer.errorCode, 'UNKNOWN');
  assert.equal(report.metadata.kind, 'transportError'); assert.equal(report.installer.kind, 'transportError');
  assert.ok(!JSON.stringify(report).includes('SYN_PRIVATE'));
});

test('a synchronous request failure cannot mask the original application failure or skip the independent second endpoint', async () => {
  const run = harness([{ syncError: new Error('SYN_PRIVATE_EXCEPTION') }, { body: installer }]);
  const report = await run.run(config);
  assert.equal(report.kind, 'completed'); assert.equal(report.metadata.kind, 'transportError');
  assert.equal(report.metadata.errorCode, 'UNKNOWN'); assert.equal(report.installer.kind, 'verified');
  assert.ok(!JSON.stringify(report).includes('SYN_PRIVATE'));
});

test('only validated scalar stable versions and bounded expected sizes can construct request URLs', async () => {
  for (const changed of [{ version: '0.1.12-beta' }, { version: '../SYN_PRIVATE' }, { version: '01.1.12' },
    { currentVersion: 'v0.1.11' }, { version: ['0.1.12'] }, { installerBytes: 0 }, { installerBytes: 1.5 },
    { installerBytes: 1024 ** 3 + 1 }, { installerSha256: 'SYN_PRIVATE' }, { installerSha512: 'SYN_PRIVATE' }]) {
    const run = harness([]), report = await run.run({ ...config, ...changed });
    assert.equal(report.kind, 'configurationRejected'); assert.equal(report.configurationValid, false);
    assert.equal(run.requests.length, 0); assert.ok(!JSON.stringify(report).includes('SYN_PRIVATE'));
  }
  const run = harness([]);
  const report = await run.run({ get version() { throw new Error('SYN_PRIVATE_GETTER'); } });
  assert.equal(report.kind, 'diagnosticError'); assert.equal(run.requests.length, 0);
  assert.ok(!JSON.stringify(report).includes('SYN_PRIVATE'));
});

test('an inherited TLS-verification bypass is rejected without changing the environment or sending requests', async () => {
  const environment = { NODE_TLS_REJECT_UNAUTHORIZED: '0' }, run = harness([], environment);
  const report = await run.run(config);
  assert.equal(report.kind, 'environmentRejected'); assert.equal(report.tlsVerificationEnabled, false);
  assert.equal(run.requests.length, 0); assert.deepEqual(environment, { NODE_TLS_REJECT_UNAUTHORIZED: '0' });
});

for (const [name, change] of [
  ['wrong version', value => { value.version = '0.1.13'; }],
  ['wrong product', value => { value.product = 'SYN_PRIVATE_PRODUCT'; }],
  ['foreign installer URL', value => { value.files[0].url = 'https://SYN_PRIVATE_INSTALLER'; }],
  ['wrong declared size', value => { value.files[0].size++; }],
  ['wrong digest', value => { value.files[0].sha512 = 'A'.repeat(86) + '=='; }],
  ['multiple files', value => { value.files.push(value.files[0]); }],
  ['packages payload', value => { value.packages = { raw: 'SYN_PRIVATE' }; }],
]) {
  test(`completed metadata reports ${name} as mismatch while the fixed installer endpoint is still verified`, async () => {
    const value = metadata(); change(value);
    const run = harness([{ body: json(value) }, { body: installer }]);
    const report = await run.run(config);
    assert.equal(report.metadata.kind, 'mismatch'); assert.equal(report.metadata.completed, true);
    assert.equal(report.installer.kind, 'verified'); assert.equal(run.requests[1].url, installerUrl);
    assert.ok(!JSON.stringify(report).includes('SYN_PRIVATE'));
  });
}
