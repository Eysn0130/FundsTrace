'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { releaseInfo, allowed, updateController, CDN } = require('../updates.cjs');
const info = (version = '0.1.8') => ({ product: 'com.eysn.fundstrace', version, files: [{ url: `${CDN}${version}/FundsTrace-${version}-windows-x64-unsigned.exe`, sha512: Buffer.alloc(64, 1).toString('base64'), size: 160000000 }] });
test('the shared JSON document is valid update YAML and retains the product binding', () => {
  const { parseUpdateInfo } = require('electron-updater/out/providers/Provider');
  const parsed = parseUpdateInfo(JSON.stringify(info()), 'latest.yml', new URL('https://analytix.top/fundstrace/latest.yml'));
  assert.equal(releaseInfo(parsed).version, '0.1.8');
  assert.equal(releaseInfo(parsed).file.url, info().files[0].url);
});
function fixture(options = {}) {
  const updater = new EventEmitter();
  const calls = [];
  updater.checkForUpdates = async () => { calls.push('check'); return { updateInfo: info() }; };
  updater.downloadUpdate = async () => { calls.push('download'); updater.emit('download-progress', { percent: 41.25 }); return ['main-owned-cache-path']; };
  updater.quitAndInstall = (...args) => calls.push(['install', ...args]);
  const states = [];
  const controller = updateController({ createUpdater: () => ({ updater, cancelCheck() {} }), version: '0.1.7', enabled: true, notify: value => states.push(value), restart: async () => { calls.push('shutdown'); return true; }, ...options });
  return { updater, calls, states, controller };
}
test('check, explicit download, progress and explicit restart preserve the update lifecycle', async () => {
  const { updater, calls, states, controller } = fixture();
  assert.equal(updater.autoInstallOnAppQuit, false); assert.equal(updater.autoDownload, false);
  assert.equal(updater.allowDowngrade, false); assert.equal(updater.disableWebInstaller, true);
  await controller.action('download'); assert.deepEqual(calls, []);
  assert.equal((await controller.action('check')).phase, 'available');
  assert.deepEqual(calls, ['check']);
  assert.equal((await controller.action('download')).phase, 'ready');
  assert.ok(states.some(value => value.phase === 'downloading' && value.percent === 41.25));
  assert.equal((await controller.action('install')).phase, 'installing');
  assert.deepEqual(calls, ['check', 'download', 'check', 'shutdown']);
  controller.install(); assert.deepEqual(calls.at(-1), ['install', true, true]);
});
test('malformed feeds, foreign products and external executables never become downloadable', async () => {
  for (const mutate of [v => { v.product = 'Analytix'; }, v => { v.files[0].url = 'https://evil.test/app.exe'; },
    v => { v.files[0].size = -1; }, v => { v.files[0].sha512 = 'invalid'; }, v => { v.packages = {}; },
    v => { v.version = '0.1.9-beta'; }, v => { v.files.push(v.files[0]); }]) {
    const value = info(); mutate(value); assert.throws(() => releaseInfo(value));
    const { updater, calls, controller } = fixture(); updater.checkForUpdates = async () => ({ updateInfo: value });
    assert.equal((await controller.action('check')).phase, 'error');
    await controller.action('download'); await controller.action('install'); assert.deepEqual(calls, []);
  }
});
test('equal/older versions and source previews never download or install', async () => {
  for (const version of ['0.1.7', '0.1.6', '0.0.99']) {
    const { updater, controller, calls } = fixture(); updater.checkForUpdates = async () => ({ updateInfo: info(version) });
    assert.equal((await controller.action('check')).phase, 'current');
    await controller.action('download'); assert.deepEqual(calls, []);
  }
  const { controller, calls } = fixture({ enabled: false });
  for (const action of ['check', 'download', 'install']) assert.equal((await controller.action(action)).phase, 'unavailable');
  assert.deepEqual(calls, []);
});
test('failed or interrupted downloads cannot be installed and can be retried', async () => {
  const { updater, controller, calls } = fixture();
  await controller.action('check'); updater.downloadUpdate = async () => { throw new Error('checksum mismatch'); };
  assert.equal((await controller.action('download')).phase, 'error');
  await controller.action('install'); assert.deepEqual(calls, ['check']);
  assert.equal((await controller.action('check')).phase, 'available');
});
test('update failure diagnostics expose fixed categories and clear on retry without leaking exception details', async () => {
  for (const code of ['EACCES', 'EPERM', 'ENOSPC', 'private-exception-value']) {
    const { updater, controller } = fixture();
    await controller.action('check');
    updater.downloadUpdate = async () => { throw Object.assign(new Error('private path and request details'), { code }); };
    const state = await controller.action('download');
    assert.equal(state.errorCode, code === 'private-exception-value' ? 'UNKNOWN' : code);
    assert.ok(!JSON.stringify(state).includes('private'));
    assert.equal((await controller.action('check')).errorCode, '');
  }
});
test('multiple windows cannot duplicate a download; cancelling restart retains the ready update', async () => {
  let finish; const { updater, controller, calls } = fixture({ restart: async () => false });
  await controller.action('check'); updater.downloadUpdate = () => new Promise(resolve => { finish = resolve; calls.push('download'); });
  const first = controller.action('download'), second = controller.action('download');
  await Promise.resolve(); finish(['cached']); await Promise.all([first, second]);
  assert.deepEqual(calls, ['check', 'download']);
  assert.equal((await controller.action('install')).phase, 'ready');
});
test('a stalled check expires after 30 seconds and retry uses a new updater through restart', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const first = fixture(), next = fixture();
  let finishOld, cancelled = 0, created = 0;
  first.updater.checkForUpdates = () => { first.calls.push('check'); return new Promise(resolve => { finishOld = resolve; }); };
  const { controller } = fixture({ createUpdater: () => {
    created++;
    return { updater: created === 1 ? first.updater : next.updater, cancelCheck() { cancelled++; } };
  } });
  const check = controller.action('check'), duplicate = controller.action('check');
  await Promise.resolve();
  assert.deepEqual(first.calls, ['check']);
  t.mock.timers.tick(29_999); await Promise.resolve();
  assert.equal(controller.snapshot().phase, 'checking');
  t.mock.timers.tick(1);
  await new Promise(setImmediate);
  assert.equal(controller.snapshot().phase, 'error');
  assert.equal((await check).errorCode, 'ERR_UPDATER_CHECK_TIMEOUT');
  await duplicate;
  assert.equal(cancelled, 1);
  assert.equal((await controller.action('check')).phase, 'available');
  assert.equal(created, 2);
  assert.deepEqual(next.calls, ['check']);
  t.mock.timers.tick(30_000); await new Promise(setImmediate);
  assert.equal(cancelled, 1, 'successful retry must clear its deadline');
  finishOld({ updateInfo: info('0.1.99') });
  first.updater.emit('error', Object.assign(new Error('late private details'), { code: 'ERR_FAILED' }));
  first.updater.emit('download-progress', { percent: 99 });
  await Promise.resolve();
  assert.deepEqual(controller.snapshot(), { phase: 'available', currentVersion: '0.1.7', version: '0.1.8', percent: 0, errorCode: '' });
  assert.equal((await controller.action('download')).phase, 'ready');
  assert.equal((await controller.action('install')).phase, 'installing');
  controller.install();
  assert.deepEqual(next.calls, ['check', 'download', 'check', ['install', true, true]]);
});
test('installation recheck has the same deadline and cannot restart a retired updater', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { updater, controller, calls } = fixture();
  await controller.action('check'); await controller.action('download');
  let finish;
  updater.checkForUpdates = () => new Promise(resolve => { finish = resolve; });
  const install = controller.action('install');
  t.mock.timers.tick(30_000);
  await new Promise(setImmediate);
  assert.equal(controller.snapshot().phase, 'error');
  assert.equal((await install).errorCode, 'ERR_UPDATER_CHECK_TIMEOUT');
  finish({ updateInfo: info() }); await Promise.resolve();
  assert.equal(controller.snapshot().phase, 'error');
  assert.deepEqual(calls, ['check', 'download']);
});
test('a cancellation failure still releases the timed-out check for a fresh retry', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const first = fixture(), next = fixture();
  first.updater.checkForUpdates = () => new Promise(() => {});
  let created = 0;
  const { controller } = fixture({ createUpdater: () => ({
    updater: ++created === 1 ? first.updater : next.updater,
    cancelCheck() { throw new Error('private cancellation details'); },
  }) });
  const pending = controller.action('check'); await Promise.resolve();
  t.mock.timers.tick(30_000);
  assert.equal((await pending).errorCode, 'ERR_UPDATER_CHECK_TIMEOUT');
  assert.equal((await controller.action('check')).phase, 'available');
  assert.equal(created, 2);
});
test('IPC requires an owned, exact-origin top frame', () => {
  const frame = { url: 'http://127.0.0.1:55123/' }, sender = { id: 7, mainFrame: frame, isDestroyed: () => false };
  assert.equal(allowed({ sender, senderFrame: frame }, 'http://127.0.0.1:55123', new Set([7])), true);
  for (const event of [{ sender, senderFrame: { ...frame } }, { sender, senderFrame: { url: 'https://analytix.top' } }, { sender: { ...sender, id: 8 }, senderFrame: frame }])
    assert.equal(allowed(event, 'http://127.0.0.1:55123', new Set([7])), false);
});
test('a superseded or changed cached release is not installed after download', async () => {
  for (const change of ['newer', 'checksum', 'offline']) {
    const { updater, controller, calls } = fixture();
    await controller.action('check'); await controller.action('download');
    updater.checkForUpdates = async () => {
      if (change === 'offline') throw new Error('offline');
      const value = info(change === 'newer' ? '0.1.9' : '0.1.8');
      if (change === 'checksum') value.files[0].sha512 = Buffer.alloc(64, 2).toString('base64');
      return { updateInfo: value };
    };
    assert.equal((await controller.action('install')).phase, 'error');
    assert.deepEqual(calls, ['check', 'download']);
  }
});

test('confirmed restart uses the real NSIS adapter in silent mode and forces relaunch', () => {
  const { NsisUpdater } = require('electron-updater');
  const launched = [];
  const native = { installerPath: 'C:\\synthetic\\FundsTrace-next.exe',
    installDirectory: 'C:\\synthetic\\FundsTrace', downloadedUpdateHelper: {},
    spawnLog: async (file, args) => { launched.push({ file, args }); },
    dispatchError: () => assert.fail('NSIS adapter rejected the update') };
  const { controller, updater } = fixture();
  updater.quitAndInstall = (isSilent, isForceRunAfter) =>
    NsisUpdater.prototype.doInstall.call(native, { isSilent, isForceRunAfter });
  controller.install();
  assert.equal(launched.length, 1);
  assert.ok(launched[0].args.includes('/S'), 'Restart must not leave an installer finish page requiring another click');
  assert.ok(launched[0].args.includes('--updated'));
  assert.ok(launched[0].args.includes('--force-run'), 'The updated application must reopen');
});
