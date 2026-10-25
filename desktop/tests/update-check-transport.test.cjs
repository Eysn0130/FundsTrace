'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { NsisUpdater } = require('electron-updater');
const { ElectronHttpExecutor } = require('electron-updater/out/electronHttpExecutor');
const { CancellationError } = require('builder-util-runtime');
const { CDN } = require('../updates.cjs');

// Exercise the locked updater/provider/executor, replacing only Electron's
// app adapter and native request boundary. No network or business data.
async function transport(t, { waitForReady = false } = {}) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'funds-update-check-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const requests = [], instances = [];
  let ready, started;
  const requestStarted = new Promise(resolve => { started = resolve; });
  const app = {
    version: '0.1.7', isPackaged: true, userDataPath: directory,
    whenReady: waitForReady ? () => new Promise(resolve => { ready = resolve; }) : async () => {},
  };
  class Executor extends ElectronHttpExecutor {
    createRequest(options, callback) {
      const request = new EventEmitter();
      request.aborts = 0;
      request.abort = () => { if (!request.aborts) { request.aborts++; request.emit('abort'); } };
      request.end = () => {
        requests.push(request);
        // Electron redirects abort the old request and create another request.
        if (requests.length === 1) queueMicrotask(() => request.emit('redirect', 302, 'GET', 'https://analytix.top/fundstrace/redirected.yml'));
        if (requests.length === 2) started();
        if (requests.length >= 3) queueMicrotask(() => {
          const response = new PassThrough();
          response.statusCode = 200; response.headers = {};
          callback(response);
          response.end(JSON.stringify({ product: 'com.eysn.fundstrace', version: '0.1.8',
            files: [{ url: `${CDN}0.1.8/FundsTrace-0.1.8-windows-x64-unsigned.exe`,
              sha512: Buffer.alloc(64, 1).toString('base64'), size: 160000000 }] }));
        });
      };
      return request;
    }
  }
  class Updater extends NsisUpdater {
    constructor(options) {
      super(undefined, app);
      this.httpExecutor = new Executor();
      this.setFeedURL(options);
      instances.push(this);
    }
  }
  const filename = require.resolve('../updates.cjs'), localRequire = createRequire(filename);
  const module = { exports: {} };
  vm.runInNewContext(await fs.readFile(filename, 'utf8'), {
    module, setTimeout, clearTimeout,
    require: name => name === 'electron-updater' ? { NsisUpdater: Updater } : localRequire(name),
  }, { filename });
  const controller = module.exports.updateController({ createUpdater: module.exports.createUpdater, enabled: true, version: '0.1.7' });
  return { controller, requests, instances, requestStarted, ready: () => ready() };
}

test('a feed deadline aborts the redirected native request and a real updater retry makes a new request', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { controller, requests, instances, requestStarted } = await transport(t);
  const pending = controller.action('check');
  await requestStarted;
  assert.equal(requests.length, 2);
  assert.equal(requests[0].aborts, 1);
  assert.equal(requests[1].aborts, 0);
  t.mock.timers.tick(30_000);
  assert.equal((await pending).errorCode, 'ERR_UPDATER_CHECK_TIMEOUT');
  assert.equal(requests[1].aborts, 1);
  assert.equal((await controller.action('check')).phase, 'available');
  assert.equal(instances.length, 2);
  assert.equal(requests.length, 3);
  t.mock.timers.tick(30_000); await new Promise(setImmediate);
  assert.equal(requests[2].aborts, 0, 'successful checks must clear their deadline');
  assert.equal(controller.snapshot().phase, 'available');
});

test('a deadline before app readiness prevents the retired updater from starting a late request', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { controller, requests, instances, ready } = await transport(t, { waitForReady: true });
  const pending = controller.action('check');
  await Promise.resolve();
  const retiredCheck = assert.rejects(instances[0].checkForUpdates(), CancellationError);
  t.mock.timers.tick(30_000);
  assert.equal((await pending).errorCode, 'ERR_UPDATER_CHECK_TIMEOUT');
  ready();
  await retiredCheck;
  assert.equal(controller.snapshot().errorCode, 'ERR_UPDATER_CHECK_TIMEOUT');
  assert.equal(requests.length, 0);
});
