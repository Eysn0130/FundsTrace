'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const baseline = require('../upgrade-baseline.json');
const source = fs.readFileSync(path.join(__dirname, 'public-update-transport-preflight.cjs'), 'utf8');
const size = 12345, sha256 = 'a'.repeat(64), sha512 = 'A'.repeat(86) + '==';
const args = () => ['--report', path.resolve('synthetic-public-preflight.json'), '--version', baseline.targetVersion,
  '--sha256', sha256, '--sha512', sha512, '--bytes', String(size)];
function goodDiagnostic() {
  const endpoint = { kind: 'verified', statusCode: 200, bytes: size, elapsedMs: 100,
    completed: true, tlsEstablished: true, responseStarted: true, budgetLimited: false, errorCode: 'NONE',
    expectedSizeMatch: true, sha256Match: true, sha512Match: true, metadataValid: null,
    versionMatch: null, productMatch: null, installerUrlMatch: null };
  return { schemaVersion: 1, transport: 'independentNodeHttps', metadataNoCacheQuery: true,
    kind: 'completed', configurationValid: true, tlsVerificationEnabled: true,
    metadata: { ...endpoint, bytes: 1234, sha256Match: null, metadataValid: true,
      versionMatch: true, productMatch: true, installerUrlMatch: true }, installer: { ...endpoint } };
}
function harness(options = {}) {
  const { diagnostic = goodDiagnostic(), diagnosticError = false, exists = false, writeError = false,
    closeError = false, platform = 'win32', tls } = options;
  const ci = Object.hasOwn(options, 'ci') ? options.ci : 'true';
  const module = { exports: {} }, requests = [], opens = [], writes = [];
  let closes = 0;
  const context = vm.createContext({ module, process: { platform, env: { GITHUB_ACTIONS: ci, NODE_TLS_REJECT_UNAUTHORIZED: tls } },
    require(name) {
      if (name === 'node:fs/promises') return { open: async (filename, flags) => {
        opens.push({ filename, flags });
        if (exists) throw Object.assign(new Error('SYN_PRIVATE_EXISTING_PATH'), { code: 'EEXIST' });
        return { writeFile: async (value, encoding) => {
          if (writeError) throw new Error('SYN_PRIVATE_WRITE_ERROR');
          assert.equal(encoding, 'utf8'); writes.push(value);
        }, close: async () => { closes++; if (closeError) throw new Error('SYN_PRIVATE_CLOSE_ERROR'); } };
      } };
      if (name === './installed-update-transport-diagnostic.cjs') return { safeProductionTransportDiagnostic: async (config, options) => {
        requests.push(JSON.parse(JSON.stringify({ config, options })));
        if (diagnosticError) throw new Error('SYN_PRIVATE_RAW_EXCEPTION');
        return diagnostic;
      } };
      return require(name);
    },
  });
  vm.runInContext(source, context);
  return { run: argv => module.exports.runPreflight(argv), requests, opens, writes, get closes() { return closes; } };
}

test('a complete verified public metadata and full dual-digest installer preflight alone returns zero', async () => {
  const run = harness();
  assert.equal(await run.run(args()), 0);
  assert.deepEqual(run.requests, [{ config: { version: baseline.targetVersion, currentVersion: baseline.prior.version,
    installerSha256: sha256, installerSha512: sha512, installerBytes: size }, options: { budgetMs: 285000 } }]);
  assert.equal(run.opens.length, 1); assert.equal(run.opens[0].flags, 'wx');
  assert.equal(run.closes, 1);
  const report = JSON.parse(run.writes[0]);
  assert.equal(report.status, 'passed'); assert.equal(report.passed, true); assert.equal(report.failureKind, 'NONE');
  assert.ok(!run.writes[0].includes(run.opens[0].filename));
});

const failures = [
  ['unfinished diagnostic', value => { value.kind = 'budgetExhausted'; }],
  ['invalid configuration', value => { value.configurationValid = false; }],
  ['disabled TLS verification', value => { value.tlsVerificationEnabled = false; }],
  ['different request shape', value => { value.metadataNoCacheQuery = false; }],
  ['empty metadata body', value => { value.metadata.bytes = 0; }],
  ['oversized metadata body', value => { value.metadata.bytes = 65537; }],
  ['short installer body', value => { value.installer.bytes = size - 1; }],
  ['oversized installer body', value => { value.installer.bytes = size + 1; }],
];
for (const endpoint of ['metadata', 'installer']) {
  failures.push([`${endpoint} HTTP error`, value => { value[endpoint].statusCode = 503; }],
    [`${endpoint} partial-content HTTP status`, value => { value[endpoint].statusCode = 206; }],
    [`${endpoint} incomplete body`, value => { value[endpoint].completed = false; }],
    [`${endpoint} TLS not established`, value => { value[endpoint].tlsEstablished = false; }],
    [`${endpoint} no HTTP response`, value => { value[endpoint].responseStarted = false; }],
    [`${endpoint} timeout`, value => { value[endpoint].kind = 'readTimeout'; value[endpoint].errorCode = 'ETIMEDOUT'; }],
    [`${endpoint} reported error`, value => { value[endpoint].errorCode = 'ECONNRESET'; }]);
}
for (const key of ['metadataValid', 'versionMatch', 'productMatch', 'installerUrlMatch', 'expectedSizeMatch', 'sha512Match']) {
  for (const invalid of [false, null, undefined]) failures.push([`metadata ${key} ${String(invalid)}`, value => { value.metadata[key] = invalid; }]);
}
for (const key of ['expectedSizeMatch', 'sha256Match', 'sha512Match']) {
  for (const invalid of [false, null, undefined]) failures.push([`installer ${key} ${String(invalid)}`, value => { value.installer[key] = invalid; }]);
}
for (const [name, mutate] of failures) {
  test(`preflight rejects ${name} while preserving the failed report`, async () => {
    const diagnostic = goodDiagnostic(); mutate(diagnostic);
    const run = harness({ diagnostic });
    assert.equal(await run.run(args()), 1);
    const report = JSON.parse(run.writes[0]);
    assert.equal(report.status, 'failed'); assert.equal(report.passed, false); assert.equal(run.closes, 1);
  });
}

test('raw diagnostic additions and exceptions cannot leak into the preflight report', async () => {
  const diagnostic = goodDiagnostic(); diagnostic.raw = 'SYN_PRIVATE_EXCEPTION'; diagnostic.metadata.headers = 'SYN_PRIVATE_HEADERS';
  const run = harness({ diagnostic }); assert.equal(await run.run(args()), 0);
  assert.ok(!run.writes[0].includes('SYN_PRIVATE'));
  const failure = harness({ diagnosticError: true }); assert.equal(await failure.run(args()), 1);
  const report = JSON.parse(failure.writes[0]);
  assert.equal(report.failureKind, 'diagnosticUnavailable'); assert.equal(report.diagnostic, null);
  assert.ok(!failure.writes[0].includes('SYN_PRIVATE'));
});

test('malformed or incomplete diagnostic DTOs cannot be interpreted as success or exposed raw', async () => {
  for (const diagnostic of [null, {}, { ...goodDiagnostic(), transport: 'SYN_PRIVATE' },
    { ...goodDiagnostic(), metadata: { ...goodDiagnostic().metadata, errorCode: 'SYN_PRIVATE' } }]) {
    const run = harness({ diagnostic }); assert.equal(await run.run(args()), 1);
    assert.equal(JSON.parse(run.writes[0]).diagnostic, null); assert.ok(!run.writes[0].includes('SYN_PRIVATE'));
  }
});

test('invalid CLI inputs fail without network access or report creation', async () => {
  const cases = [[], args().slice(0, -1), [...args(), '--unknown', 'SYN_PRIVATE'],
    args().map((value, index) => index === 0 ? '--version' : value)];
  for (const [flag, value] of [['--report', 'relative.json'], ['--report', '\0'], ['--version', '0.1.12-beta'],
    ['--version', '01.1.12'], ['--version', '9.9.9'], ['--sha256', 'SYN_PRIVATE'], ['--sha512', 'SYN_PRIVATE'],
    ['--bytes', '0'], ['--bytes', '-1'], ['--bytes', '1.2'], ['--bytes', '01'], ['--bytes', String(1024 ** 3 + 1)]]) {
    const argv = args(); argv[argv.indexOf(flag) + 1] = value; cases.push(argv);
  }
  for (const argv of cases) {
    const run = harness(); assert.equal(await run.run(argv), 2);
    assert.equal(run.requests.length, 0); assert.equal(run.opens.length, 0);
  }
});

test('only disposable Windows CI may invoke the public transport preflight', async () => {
  for (const configuration of [{ platform: 'darwin' }, { platform: 'linux' }, { ci: 'false' }, { ci: undefined }, { tls: '0' }]) {
    const run = harness(configuration);
    assert.equal(await run.run(args()), 2); assert.equal(run.requests.length, 0); assert.equal(run.opens.length, 0);
  }
});

test('an existing report is never overwritten and prevents unnecessary preflight requests', async () => {
  const run = harness({ exists: true }); assert.equal(await run.run(args()), 2);
  assert.equal(run.requests.length, 0); assert.equal(run.writes.length, 0); assert.equal(run.opens[0].flags, 'wx');
});

test('report write or close failure cannot return a successful exit', async () => {
  for (const configuration of [{ writeError: true }, { closeError: true }]) {
    const run = harness(configuration); assert.equal(await run.run(args()), 2); assert.equal(run.closes, 1);
  }
});
