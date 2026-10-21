'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function workbench() {
  const listeners = new Map(), signals = [];
  const storage = { getItem: () => null, setItem() {} };
  const context = vm.createContext({
    window: { location: { href: 'http://127.0.0.1:54321/', search: '' }, history: { replaceState() {} },
      addEventListener: (name, listener) => listeners.set(name, listener),
      dispatchEvent: event => signals.push({ type: event.type, detail: event.detail }) },
    document: { querySelector: () => ({}), querySelectorAll: () => [] },
    localStorage: storage, FundsMoney: { count: String }, URL, URLSearchParams, clearTimeout, setTimeout,
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../static/app.js'), 'utf8'), context);
  for (const name of ['initWatermark', 'bindUI', 'installNeighborhood', 'installNodeFinder', 'initGraph', 'installAnnotationDialog',
    'loadCases', 'refreshStats', 'loadGraph', 'applyPendingFocusEdge', 'refreshCanvasList', 'doSearch']) {
    vm.runInContext(`${name} = async () => {}`, context);
  }
  return { context, signals, start: listeners.get('DOMContentLoaded') };
}

test('workbench readiness waits for the first query instead of DOM or backend health alone', async () => {
  const harness = workbench();
  let resolve;
  harness.context.initialSearch = new Promise(yes => { resolve = yes; });
  vm.runInContext('doSearch = () => initialSearch', harness.context);
  const starting = harness.start();
  await new Promise(yes => setImmediate(yes));
  assert.deepEqual(harness.signals, []);
  resolve(); await starting;
  assert.deepEqual(harness.signals, [{ type: 'funds-trace:workbench', detail: 'ready' }]);
});

test('initialization failure signals failure and never publishes a ready workbench', async () => {
  const harness = workbench();
  vm.runInContext('loadCases = async () => { throw new Error("synthetic unavailable cases"); }', harness.context);
  await assert.rejects(harness.start(), /synthetic unavailable/);
  assert.deepEqual(harness.signals, [{ type: 'funds-trace:workbench', detail: 'failed' }]);
});

test('business preload forwards only fixed readiness states and no event data', () => {
  const listeners = new Map(), calls = [];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../preload.cjs'), 'utf8'), {
    window: { addEventListener: (name, callback) => listeners.set(name, callback) },
    require: name => {
      assert.equal(name, 'electron');
      return { contextBridge: { exposeInMainWorld() {} }, ipcRenderer: { send: (...args) => calls.push(args) } };
    },
  });
  const notify = listeners.get('funds-trace:workbench');
  for (const detail of [undefined, 'arbitrary', { ready: true }, ['ready']]) notify({ detail });
  assert.deepEqual(calls, []);
  notify({ detail: 'ready' }); notify({ detail: 'failed' });
  assert.deepEqual(calls, [['funds-trace:workbench', 'ready'], ['funds-trace:workbench', 'failed']]);
});
