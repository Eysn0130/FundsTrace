'use strict';
// A native packaged backend must work without activation across an owned-process crash and reopen.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { launchConfig, HEADER } = require('../host-policy.cjs');
const { startBackend } = require('../backend.cjs');
let stage = 'arguments'; // Only fixed stage names and safe error codes enter CI logs.

async function waitForExit(exited) {
  let timer;
  try {
    return await Promise.race([exited, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Owned backend exit timed out')), 15000);
    })]);
  } finally { clearTimeout(timer); }
}

function request({ origin, token }, endpoint, options = {}) {
  return fetch(`${origin}${endpoint}`, { ...options, headers: { [HEADER]: token, ...options.headers },
    redirect: 'manual', signal: AbortSignal.timeout(10000) });
}

async function checkNormalStartup(ready) {
  const endpoints = ['/__desktop/health', '/', '/analysis', '/workspace/', '/api/cases', '/api/v1/cases'];
  for (const endpoint of endpoints) {
    const denied = await fetch(`${ready.origin}${endpoint}`, { redirect: 'manual', signal: AbortSignal.timeout(10000) });
    assert.equal(denied.status, 403, endpoint);
    await denied.arrayBuffer();
  }
  const health = await request(ready, '/__desktop/health');
  assert.equal(health.status, 200);
  assert.deepEqual(await health.json(), { protocol: 1, service: 'FundsTrace', ready: true });
  for (const endpoint of ['/', '/analysis', '/workspace/']) {
    const page = await request(ready, endpoint);
    assert.equal(page.status, 200, endpoint);
    assert.match(page.headers.get('content-type'), /text\/html/, endpoint);
    await page.arrayBuffer();
  }
  const legacy = await request(ready, '/api/cases');
  assert.equal(legacy.status, 200);
  const legacyCases = await legacy.json();
  assert.ok(Array.isArray(legacyCases));
  const versioned = await request(ready, '/api/v1/cases');
  assert.equal(versioned.status, 200);
  const { items: versionedCases } = await versioned.json();
  assert.ok(Array.isArray(versionedCases));
  const info = await request(ready, '/api/license/info');
  assert.equal(info.status, 200);
  const licenseInfo = await info.json();
  assert.equal(licenseInfo.activation_required, false);
  assert.equal(licenseInfo.activated, false);
  return { legacyCases, versionedCases };
}

(async () => {
  const args = process.argv.slice(2);
  assert.ok(args[0] && (args.length === 1 || (args.length === 3 && args[1] === '--report')));
  const source = path.resolve(args[0]);
  const reportPath = args[2] && path.resolve(args[2]);
  const report = {
    schemaVersion: 1, status: 'running', platform: process.platform, arch: process.arch,
    node: process.versions.node, testState: 'temporary-database-with-one-synthetic-case',
    checks: {}, limits: { gui: false, importRecovery: false, productionActivation: false },
  };
  if (reportPath) await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'FundsTrace 封装烟测 '));
  const resources = path.join(temporary, '安装资源 含空格');
  const dataDir = path.join(temporary, '运行数据 含空格');
  const owned = [];
  try {
    stage = 'resource-link';
    await fs.mkdir(resources);
    await fs.symlink(source, path.join(resources, 'backend'), process.platform === 'win32' ? 'junction' : 'dir');
    // Do not read or inherit a maintainer's license configuration into this empty test instance.
    const env = {};
    for (const key of Object.keys(process.env)) {
      if (!key.toUpperCase().startsWith('FUNDS_TRACE_LICENSE')) env[key] = process.env[key];
    }
    const config = launchConfig({ packaged: true, resourcesPath: resources, dataDir, env });
    function boot(configuration = config) {
      let child, exited;
      const backend = startBackend(configuration, { spawnProcess: (...input) => {
        child = spawn(...input);
        exited = new Promise(resolve => child.once('exit', (code, signal) => resolve({ code, signal })));
        return child;
      } });
      const instance = { backend, child, exited };
      owned.push(instance);
      return instance;
    }
    const first = boot();
    stage = 'first-boot-without-activation';
    const firstReady = await first.backend.ready;
    await checkNormalStartup(firstReady);
    stage = 'database-at-requested-path';
    assert.equal((await fs.stat(path.join(dataDir, 'fund.duckdb'))).isFile(), true);
    await assert.rejects(fs.stat(path.join(dataDir, 'license.dat')), { code: 'ENOENT' });
    report.checks.normalFirstBootWithoutActivation = true;
    stage = 'create-synthetic-case';
    const created = await request(firstReady, '/api/v1/cases', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'Synthetic packaged smoke case' }) });
    assert.equal(created.status, 201);
    const createdCase = await created.json();
    assert.match(createdCase.id, /^[1-9]\d*$/);
    assert.equal(createdCase.name, 'Synthetic packaged smoke case');
    report.checks.syntheticCaseCreatedWithoutActivation = true;
    stage = 'owned-child-forced-exit';
    assert.equal(first.child.kill('SIGKILL'), true);
    await waitForExit(first.exited);
    report.checks.ownedChildForcedExit = true;

    const reopened = boot(); // The same data directory, after the held child has exited.
    stage = 'reopen-without-activation';
    const ready = await reopened.backend.ready;
    const { legacyCases, versionedCases } = await checkNormalStartup(ready);
    assert.ok(legacyCases.some(item => String(item.id) === createdCase.id && item.name === createdCase.name));
    assert.ok(versionedCases.some(item => item.id === createdCase.id && item.name === createdCase.name));
    await assert.rejects(fs.stat(path.join(dataDir, 'license.dat')), { code: 'ENOENT' });
    report.checks.syntheticCasePersistedAfterCrash = true;
    report.checks.normalReopenWithoutActivation = true;
    report.checks.desktopTokenRequired = true;
    report.checks.legacyAndVersionedEntryPoints = true;
    stage = 'eof-shutdown';
    await reopened.backend.stop();
    assert.deepEqual(await waitForExit(reopened.exited), { code: 0, signal: null });
    await assert.rejects(fetch(`${ready.origin}/__desktop/health`, { signal: AbortSignal.timeout(10000) }));
    report.checks.eofShutdown = true;
    report.checks.chineseAndSpacePaths = true;
    await assert.rejects(fs.stat(path.join(dataDir, 'license.dat')), { code: 'ENOENT' });
    report.checks.noLicenseFileGenerated = true;
  } finally {
    await Promise.all(owned.map(instance => instance.backend.stop()));
    await fs.unlink(path.join(resources, 'backend')).catch(error => { if (error.code !== 'ENOENT') throw error; });
    await fs.rm(temporary, { recursive: true, force: true });
  }
  report.checks.temporaryStateRemoved = true;
  report.status = 'passed';
  if (reportPath) await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  console.log('Native packaged backend: normal startup without activation, token protection, synthetic case crash/reopen, Chinese paths and EOF passed. GUI and import recovery are not covered.');
})().catch(error => {
  const code = typeof error.code === 'string' && /^[A-Z][A-Z0-9_]{0,39}$/.test(error.code) ? error.code : 'ERROR';
  console.error(`Native packaged backend smoke failed at ${stage} (${code}).`);
  process.exitCode = 1;
});
