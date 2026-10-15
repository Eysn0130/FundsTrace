'use strict';
// A native packaged backend must stay unactivated across an owned-process crash and reopen.
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

async function checkUnactivated({ origin, token }) {
  const get = (endpoint, options = {}) => fetch(`${origin}${endpoint}`, { ...options, signal: AbortSignal.timeout(10000) });
  assert.equal((await get('/__desktop/health')).status, 403);
  const headers = { [HEADER]: token };
  const health = await get('/__desktop/health', { headers });
  assert.equal(health.status, 200);
  assert.deepEqual(await health.json(), { protocol: 1, service: 'FundsTrace', ready: true });
  const page = await get('/', { headers, redirect: 'manual' });
  assert.equal(page.status, 307);
  assert.equal(page.headers.get('location'), '/license');
  const license = await get('/license', { headers });
  assert.equal(license.status, 200);
  assert.match(await license.text(), /\/static\/license\.js/);
  const script = await get('/static/license.js', { headers });
  assert.equal(script.status, 200);
  assert.match(script.headers.get('content-type'), /javascript/);
  const info = await get('/api/license/info', { headers });
  assert.equal(info.status, 200);
  assert.equal((await info.json()).activated, false);
  assert.equal((await get('/api/cases', { headers })).status, 403);
}

(async () => {
  const args = process.argv.slice(2);
  assert.ok(args[0] && (args.length === 1 || (args.length === 3 && args[1] === '--report')));
  const source = path.resolve(args[0]);
  const reportPath = args[2] && path.resolve(args[2]);
  const report = {
    schemaVersion: 1, status: 'running', platform: process.platform, arch: process.arch,
    node: process.versions.node, testState: 'new-empty-database',
    checks: {}, limits: { gui: false, importRecovery: false, productActivation: false },
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
    function boot() {
      let child, exited;
      const backend = startBackend(config, { spawnProcess: (...input) => {
        child = spawn(...input);
        exited = new Promise(resolve => child.once('exit', (code, signal) => resolve({ code, signal })));
        return child;
      } });
      const instance = { backend, child, exited };
      owned.push(instance);
      return instance;
    }
    const first = boot();
    stage = 'first-boot-and-unactivated-http';
    await checkUnactivated(await first.backend.ready);
    stage = 'database-at-requested-path';
    assert.equal((await fs.stat(path.join(dataDir, 'fund.duckdb'))).isFile(), true);
    report.checks.unactivatedFirstBoot = true;
    stage = 'owned-child-forced-exit';
    assert.equal(first.child.kill('SIGKILL'), true);
    await waitForExit(first.exited);
    report.checks.ownedChildForcedExit = true;

    const reopened = boot(); // The same data directory, after the held child has exited.
    stage = 'reopen-and-unactivated-http';
    const ready = await reopened.backend.ready;
    await checkUnactivated(ready);
    report.checks.sameEmptyDatabaseReopened = true;
    report.checks.unactivatedAfterReopen = true;
    stage = 'eof-shutdown';
    await reopened.backend.stop();
    assert.deepEqual(await waitForExit(reopened.exited), { code: 0, signal: null });
    await assert.rejects(fetch(`${ready.origin}/__desktop/health`, { signal: AbortSignal.timeout(10000) }));
    report.checks.eofShutdown = true;
    report.checks.chineseAndSpacePaths = true;
  } finally {
    await Promise.all(owned.map(instance => instance.backend.stop()));
    await fs.unlink(path.join(resources, 'backend')).catch(error => { if (error.code !== 'ENOENT') throw error; });
    await fs.rm(temporary, { recursive: true, force: true });
  }
  report.checks.temporaryStateRemoved = true;
  report.status = 'passed';
  if (reportPath) await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  console.log('Native packaged backend: unactivated empty-state crash/reopen, Chinese/space paths and EOF shutdown passed.');
})().catch(error => {
  const code = typeof error.code === 'string' && /^[A-Z][A-Z0-9_]{0,39}$/.test(error.code) ? error.code : 'ERROR';
  console.error(`Native packaged backend smoke failed at ${stage} (${code}).`);
  process.exitCode = 1;
});
