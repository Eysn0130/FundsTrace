'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const { launchConfig, sameOrigin, pageAllowed, popupKind, requestAllowed, downloadAllowed, HEADER, windowBounds } = require('../host-policy.cjs');
const { startBackend, verifyHealth } = require('../backend.cjs');

const origin = 'http://127.0.0.1:54321';

test('initial window and minimum sizes fit a small logical work area without double DPI scaling', () => {
  for (const area of [{x:0,y:0,width:1920,height:1040}, {x:0,y:0,width:960,height:500},
    {x:-1280,y:24,width:1280,height:696}]) {
    const bounds = windowBounds(area);
    assert.ok(bounds.width <= area.width && bounds.height <= area.height);
    assert.ok(bounds.minWidth <= bounds.width && bounds.minHeight <= bounds.height);
    assert.ok(bounds.x >= area.x && bounds.x + bounds.width <= area.x + area.width);
    assert.ok(bounds.y >= area.y && bounds.y + bounds.height <= area.y + area.height);
  }
  assert.deepEqual(windowBounds({x:0,y:0,width:960,height:500}),
    {x:0,y:0,width:960,height:500,minWidth:960,minHeight:500});
});

test('host denies remote/credential origins, documents and arbitrary popups', () => {
  assert.equal(sameOrigin(`${origin}/api`, origin), true);
  for (const url of ['http://127.0.0.1:54322/', 'http://localhost:54321/', 'file:///etc/passwd', 'https://example.com', 'http://name:pass@127.0.0.1:54321/', 'javascript:alert(1)']) {
    assert.equal(sameOrigin(url, origin), false, url);
    assert.equal(pageAllowed(url, origin), false, url);
    assert.equal(popupKind(url, origin), 'deny', url);
  }
  assert.equal(pageAllowed(`${origin}/workspace/?case_id=2`, origin), true);
  assert.equal(pageAllowed(`${origin}/static/app.js`, origin), false);
  assert.equal(popupKind(`${origin}/analysis?case_id=2`, origin), 'page');
  assert.equal(popupKind(`${origin}/api/export/tracked.xlsx?case_id=2`, origin), 'download');
  assert.equal(popupKind(`${origin}/api/account-ids/template`, origin), 'download');
  assert.equal(popupKind(`${origin}/api/cases/2/export`, origin), 'download');
  assert.equal(popupKind(`${origin}/api/cases/2/export/extra`, origin), 'deny');
  assert.equal(popupKind(`${origin}/api/cases/0/export`, origin), 'deny');
  assert.equal(popupKind(`${origin}/api/cases`, origin), 'deny');
});

test('requests require an owned renderer and exact backend origin; blob downloads stay local', () => {
  const owned = new Set([7]);
  assert.equal(requestAllowed(`${origin}/static/graph-layout.js`, origin, 7, owned), true);
  for (const id of [-1, 0, 8, undefined]) assert.equal(requestAllowed(`${origin}/`, origin, id, owned), false);
  assert.equal(requestAllowed('https://example.com/', origin, 7, owned), false);
  assert.equal(downloadAllowed(`blob:${origin}/a-b`, origin), true);
  assert.equal(downloadAllowed('blob:https://example.com/a', origin), false);
});

test('source launch isolates runtime and Python settings, preserving only normal license configuration', t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'funds-trace-launch-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const python = path.join(directory, 'python'); fs.writeFileSync(python, 'not executed');
  const env = { PATH: 'preserved', FUND_DATA_DIR: '/unrelated/data', PYTHONPATH: '/injected', FUNDS_TRACE_PYTHON: '/wrong/python', FUNDS_TRACE_LICENSE_SECRET: 'test-only-value', FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE: path.join(directory, 'public.pem') };
  const normal = launchConfig({ packaged: false, repoRoot: directory, python, dataDir: path.join(directory, 'data'), env });
  assert.equal(normal.command, python);
  assert.deepEqual(normal.args, [path.join(directory, 'desktop_backend.py')]);
  assert.equal(normal.env.FUND_DATA_DIR, undefined);
  assert.equal(normal.env.PYTHONPATH, undefined);
  assert.equal(normal.env.FUNDS_TRACE_PYTHON, undefined);
  assert.equal(normal.env.FUNDS_TRACE_LICENSE_SECRET, 'test-only-value');
  assert.equal(normal.env.FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE, env.FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE);
  assert.equal(normal.env.PYTHONUTF8, '1');
  assert.equal(launchConfig({ packaged: false, repoRoot: directory, python, dataDir: directory, synthetic: true, env }).env.FUNDS_TRACE_LICENSE_SECRET, undefined);
  assert.equal(launchConfig({ packaged: false, repoRoot: directory, python, dataDir: directory, synthetic: true, env }).env.FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE, undefined);
  assert.throws(() => launchConfig({ packaged: false, repoRoot: directory, python, dataDir: directory, env: { FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE: 'relative.pem' } }), /absolute/);
  assert.throws(() => launchConfig({ packaged: false, repoRoot: directory, python: 'python', dataDir: directory }), /absolute/);
});

test('installed launch refuses missing/mismatched native bundles and synthetic mode without fallback', t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'funds-trace-bundle-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const backend = path.join(directory, 'backend'); fs.mkdirSync(backend);
  const input = { packaged: true, resourcesPath: directory, dataDir: path.join(directory, 'data'), platform: 'win32', arch: 'x64' };
  assert.throws(() => launchConfig(input));
  fs.writeFileSync(path.join(backend, 'build-manifest.json'), JSON.stringify({ protocol: 1, platform: 'darwin', arch: 'x64' }));
  assert.throws(() => launchConfig(input), /mismatch/);
  fs.writeFileSync(path.join(backend, 'build-manifest.json'), JSON.stringify({ protocol: 1, platform: 'win32', arch: 'x64' }));
  assert.throws(() => launchConfig(input));
  fs.writeFileSync(path.join(backend, 'funds-trace-backend.exe'), 'not executed');
  assert.equal(launchConfig(input).command, path.join(backend, 'funds-trace-backend.exe'));
  assert.throws(() => launchConfig({ ...input, synthetic: true }), /Synthetic/);
});

function fakeChild() {
  const child = new EventEmitter(); child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
  child.kills = [];
  child.kill = signal => { child.kills.push(signal || 'SIGTERM'); queueMicrotask(() => child.emit('exit', null, signal)); };
  return child;
}
const config = { command: '/explicit/python', args: ['/explicit/desktop_backend.py'], cwd: '/explicit', env: {}, dataDir: '/explicit/data', synthetic: true };

test('backend bootstrap sends token only over stdin, requires health, EOF stop is idempotent', async () => {
  const child = fakeChild(); let options, command, args, input = '', healthCalls = 0;
  child.stdin.on('data', value => { input += value; });
  child.stdin.on('finish', () => queueMicrotask(() => child.emit('exit', 0)));
  const instance = startBackend(config, { spawnProcess: (cmd, argv, opts) => { command = cmd; args = argv; options = opts; return child; }, health: async (port, token) => { healthCalls++; assert.equal(port, 54321); assert.match(token, /^[a-f0-9]{64}$/); }, readyTimeout: 1000 });
  const bootstrap = JSON.parse(input); assert.equal(bootstrap.protocol, 1); assert.equal(bootstrap.synthetic, true); assert.equal(bootstrap.dataDir, config.dataDir);
  assert.equal(command, config.command); assert.deepEqual(args, config.args);
  assert.equal(options.shell, false); assert.equal(options.windowsHide, true);
  assert.equal(JSON.stringify(options).includes(bootstrap.token), false);
  child.stdout.write('{"protocol":1,'); child.stdout.write('"port":54321}\n');
  const result = await instance.ready; assert.equal(result.origin, origin); assert.equal(healthCalls, 1);
  assert.equal(result.token, bootstrap.token);
  await Promise.all([instance.stop(), instance.stop()]); assert.deepEqual(child.kills, []);
});

test('malformed, oversized and wrong-service readiness fail without exposing child output', async () => {
  for (const output of ['not readiness secret\n', '{"protocol":1,"port":0}\n', '{"protocol":1,"port":54321,"token":"secret"}\n', 'x'.repeat(5000)]) {
    const child = fakeChild(); child.stdin.on('finish', () => child.emit('exit', 0));
    const instance = startBackend(config, { spawnProcess: () => child, readyTimeout: 1000 });
    child.stdout.write(output);
    await assert.rejects(instance.ready, error => !error.message.includes('secret') && error.message.includes('readiness'));
    await instance.stop();
  }
  const child = fakeChild(); child.stdin.on('finish', () => child.emit('exit', 0));
  const instance = startBackend(config, { spawnProcess: () => child, health: async () => { throw new Error('sensitive diagnostic'); }, readyTimeout: 1000 });
  child.stdout.write('{"protocol":1,"port":54321}\n');
  await assert.rejects(instance.ready, /identity check failed/); await instance.stop();
});

test('backend timeout and kill fallback affect only the held child', async () => {
  const child = fakeChild();
  const instance = startBackend(config, { spawnProcess: () => child, readyTimeout: 10, stopTimeout: 10 });
  await assert.rejects(instance.ready, /timed out/); await instance.stop(); assert.deepEqual(child.kills, ['SIGTERM']);
});

test('health checks status, protocol and exact service using main-only authorization', async t => {
  let response = { protocol: 1, service: 'FundsTrace', ready: true }, observed;
  const server = http.createServer((req, res) => { observed = req.headers[HEADER.toLowerCase()]; res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(response)); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  await verifyHealth(server.address().port, 'test-main-only-token'); assert.equal(observed, 'test-main-only-token');
  response = { protocol: 1, service: 'Other', ready: true };
  await assert.rejects(verifyHealth(server.address().port, 'test-main-only-token'), /identity check failed/);
});

test('distribution manifest prevents cross-platform packaging and includes only host source', async t => {
  const builder = require('../electron-builder.cjs');
  assert.equal(builder.appId, 'com.eysn.fundstrace');
  assert.deepEqual(builder.files, ['package.json', 'main.cjs', 'backend.cjs', 'host-policy.cjs']);
  assert.equal(builder.publish, null);
  assert.equal(builder.nsis.deleteAppDataOnUninstall, false);
  assert.equal(builder.win.signAndEditExecutable, false);
  assert.deepEqual(builder.extraResources, [
    { from: path.resolve(__dirname, '../../build/desktop/backend/funds-trace-backend'), to: 'backend', filter: ['**/*'] },
    { from: path.resolve(__dirname, '../licenses/Apache-2.0.txt'), to: 'licenses/Analytix-Apache-2.0.txt' }
  ]);
  // Missing native backend must fail rather than create an Electron-only package.
  if (!fs.existsSync(path.join(builder.extraResources[0].from, 'build-manifest.json'))) {
    await assert.rejects(builder.beforePack({ electronPlatformName: 'win32', arch: 1 }));
  }
});
