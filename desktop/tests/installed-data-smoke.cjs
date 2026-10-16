'use strict';
// Used only by the Windows Actions installer lifecycle, against its fresh user's own data.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const { launchConfig, HEADER } = require('../host-policy.cjs');
const { startBackend } = require('../backend.cjs');

const SEED_CASE = 'SYN prior installer lifecycle case';
const CREATED_CASE = 'SYN current installer lifecycle case';
let stage = 'ci-guard';

async function waitForExit(exited) {
  let timer;
  try {
    return await Promise.race([exited, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Owned backend exit timed out')), 15000);
    })]);
  } finally { clearTimeout(timer); }
}

function request(ready, endpoint, options = {}) {
  return fetch(`${ready.origin}${endpoint}`, { ...options, redirect: 'manual',
    headers: { [HEADER]: ready.token, ...options.headers }, signal: AbortSignal.timeout(10000) });
}

async function entries(ready) {
  for (const endpoint of ['/', '/analysis', '/api/v1/cases']) {
    for (const headers of [{}, { [HEADER]: '0'.repeat(64) }]) {
      const denied = await fetch(`${ready.origin}${endpoint}`, { headers, redirect: 'manual',
        signal: AbortSignal.timeout(10000) });
      assert.equal(denied.status, 403);
      await denied.arrayBuffer();
    }
    const allowed = await request(ready, endpoint);
    assert.equal(allowed.status, 200);
    await allowed.arrayBuffer();
  }
  const infoResponse = await request(ready, '/api/license/info');
  assert.equal(infoResponse.status, 200);
  const info = await infoResponse.json();
  assert.equal(info.activation_required, false);
  assert.equal(info.activated, false);
  const response = await request(ready, '/api/v1/cases');
  assert.equal(response.status, 200);
  const { items } = await response.json();
  assert.ok(Array.isArray(items));
  const seeded = items.find(item => item.name === SEED_CASE);
  assert.ok(seeded);
  assert.match(seeded.id, /^[1-9]\d*$/);
  const stats = await request(ready, `/api/v1/cases/${seeded.id}/stats`);
  assert.equal(stats.status, 200);
  await stats.arrayBuffer();
  return items;
}

(async () => {
  assert.equal(process.platform, 'win32');
  assert.equal(process.arch, 'x64');
  assert.equal(process.env.GITHUB_ACTIONS, 'true');
  assert.equal(process.argv.length, 3);
  assert.ok(path.isAbsolute(process.argv[2]));
  assert.ok(process.env.APPDATA && path.isAbsolute(process.env.APPDATA));
  const installRoot = path.resolve(process.argv[2]);
  const userRoot = path.join(process.env.APPDATA, 'FundsTrace');
  const dataDir = path.join(userRoot, 'data');
  stage = 'owned-default-data';
  assert.equal(await fs.readFile(path.join(userRoot, 'installer-lifecycle-sentinel.txt'), 'utf8'),
    'FundsTrace synthetic installer lifecycle v1\n');
  assert.equal((await fs.stat(path.join(dataDir, 'fund.duckdb'))).isFile(), true);
  await assert.rejects(fs.stat(path.join(dataDir, 'license.dat')), { code: 'ENOENT' });
  const env = Object.fromEntries(Object.entries(process.env)
    .filter(([name]) => !name.toUpperCase().startsWith('FUNDS_TRACE_LICENSE')));
  const config = launchConfig({ packaged: true, resourcesPath: path.join(installRoot, 'resources'), dataDir, env });
  const owned = [];
  function boot() {
    let child, exited;
    const backend = startBackend(config, { spawnProcess: (...args) => {
      child = spawn(...args);
      exited = new Promise(resolve => {
        child.once('exit', (code, signal) => resolve({ code, signal }));
        child.once('error', () => resolve({ code: null, signal: null }));
      });
      return child;
    } });
    const instance = { backend, exited };
    owned.push(instance);
    return instance;
  }
  async function eof(instance) {
    await instance.backend.stop();
    assert.deepEqual(await waitForExit(instance.exited), { code: 0, signal: null });
  }
  try {
    stage = 'installed-first-boot';
    const first = boot();
    const firstReady = await first.backend.ready;
    await entries(firstReady);
    stage = 'installed-create-case';
    const response = await request(firstReady, '/api/v1/cases', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: CREATED_CASE }) });
    assert.equal(response.status, 201);
    const created = await response.json();
    assert.equal(created.name, CREATED_CASE);
    assert.match(created.id, /^[1-9]\d*$/);
    stage = 'installed-first-eof';
    await eof(first);
    stage = 'installed-reopen';
    const reopened = boot();
    const ready = await reopened.backend.ready;
    const cases = await entries(ready);
    assert.ok(cases.some(item => item.id === created.id && item.name === CREATED_CASE));
    await assert.rejects(fs.stat(path.join(dataDir, 'license.dat')), { code: 'ENOENT' });
    stage = 'installed-final-eof';
    await eof(reopened);
    await assert.rejects(fetch(`${ready.origin}/__desktop/health`, { signal: AbortSignal.timeout(10000) }));
  } finally {
    // stopBackend owns these child handles; no process discovery or name-based termination.
    for (const instance of owned) {
      await instance.backend.stop();
      await waitForExit(instance.exited);
    }
  }
  // Only fixed check names leave this helper: never tokens, paths, IDs, or diagnostics.
  process.stdout.write(JSON.stringify({
    defaultUserData: true, seededCaseRead: true, activationFree: true, desktopAuthentication: true,
    caseCreatedAndReopened: true, eofShutdown: true, noLicenseFileGenerated: true,
  }) + '\n');
})().catch(() => {
  process.stderr.write(`Installed data smoke failed at ${stage}.\n`);
  process.exitCode = 1;
});
