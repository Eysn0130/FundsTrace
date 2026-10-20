'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const vm = require('node:vm');
const { preferenceStore, preferenceRequest, validPreference, CHANNEL } = require('../preferences.cjs');

function temporary(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'fundstrace-preferences-test-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return directory;
}
const chosen = { fund_case: '3', fund_edge_type: 'polyline', fund_show_ids: '0', fund_show_photos: '1', fund_edge_labels: '["money","time","summary","currency"]' };

test('presentation preferences survive host restart and a new loopback origin', t => {
  const directory = temporary(t);
  let store = preferenceStore(directory);
  for (const [key, value] of Object.entries(chosen)) assert.equal(store.setItem(key, value), true);
  store = preferenceStore(directory);
  for (const port of [54321, 54322]) {
    const origin = `http://127.0.0.1:${port}`;
    const frame = { url: origin + '/?case_id=3' };
    const sender = { id: 1, mainFrame: frame, isDestroyed: () => false };
    for (const [key, value] of Object.entries(chosen)) assert.equal(preferenceRequest(store, { sender, senderFrame: frame }, origin, new Set([1]), 'get', key), value);
  }
  assert.equal(store.removeItem('fund_edge_label'), true, 'migration may remove an absent legacy key');
  assert.equal(store.removeItem('fund_show_photos'), true);
  assert.equal(preferenceStore(directory).getItem('fund_show_photos'), null);
  assert.deepEqual(fs.readdirSync(directory), ['preferences.json']);
});

test('storage rejects arbitrary payloads and recovers from invalid state without persisting them', t => {
  const directory = temporary(t), file = path.join(directory, 'preferences.json');
  fs.writeFileSync(file, '{invalid');
  assert.equal(preferenceStore(directory).getItem('fund_case'), null);
  fs.writeFileSync(file, JSON.stringify({ ...chosen, token: 'SYNTHETIC-DO-NOT-PERSIST', fund_edge_type: 'javascript', fund_show_ids: true }));
  const store = preferenceStore(directory);
  assert.equal(store.getItem('token'), null);
  assert.equal(store.getItem('fund_edge_type'), null);
  assert.equal(store.getItem('fund_show_ids'), null);
  for (const [key, value] of [['token', 'synthetic'], ['../file', 'x'], ['__proto__', '{}'], ['fund_case', '0'], ['fund_case', '-1'], ['fund_edge_type', 'loop'], ['fund_edge_labels', '["money","unknown"]'], ['fund_edge_labels', '["money","money"]'], ['fund_edge_labels', '[]'], ['fund_show_ids', 'true']]) {
    assert.equal(validPreference(key, value), false);
    assert.equal(store.setItem(key, value), false);
  }
  assert.equal(store.removeItem('token'), false);
  assert.equal(store.setItem('fund_case', '2'), true);
  const saved = JSON.parse(fs.readFileSync(file));
  assert.equal(saved.fund_case, '2');
  assert.equal(Object.hasOwn(saved, 'token'), false);
});

test('failed atomic write preserves the last successful values', t => {
  const directory = temporary(t), store = preferenceStore(directory);
  assert.equal(store.setItem('fund_case', '2'), true);
  fs.rmSync(path.join(directory, 'preferences.json'));
  fs.mkdirSync(path.join(directory, 'preferences.json'));
  assert.equal(store.setItem('fund_case', '3'), false);
  assert.equal(store.getItem('fund_case'), '2');
  assert.deepEqual(fs.readdirSync(directory), ['preferences.json']);
});

test('preference IPC denies foreign frames, stale origins and unowned renderers', t => {
  const store = preferenceStore(temporary(t)), origin = 'http://127.0.0.1:54321';
  const frame = { url: origin + '/' }, sender = { id: 7, mainFrame: frame, isDestroyed: () => false };
  const valid = { sender, senderFrame: frame }, owned = new Set([7]);
  for (const event of [{ ...valid, senderFrame: { url: frame.url } }, { ...valid, sender: { ...sender, id: 8 } }, { ...valid, sender: { ...sender, isDestroyed: () => true } }, { sender }]) {
    assert.equal(preferenceRequest(store, event, origin, owned, 'set', 'fund_case', '3'), null);
  }
  for (const url of ['http://127.0.0.1:54322/', 'https://example.com/', origin + '/static/app.js', 'file:///tmp/test']) {
    frame.url = url;
    assert.equal(preferenceRequest(store, valid, origin, owned, 'set', 'fund_case', '3'), null);
  }
  frame.url = origin + '/analysis';
  assert.equal(preferenceRequest(store, valid, origin, owned, 'set', 'fund_case', '3'), true);
  assert.equal(preferenceRequest(store, valid, origin, owned, 'get', 'fund_case'), '3');
  assert.equal(preferenceRequest(store, valid, origin, owned, 'anything', 'fund_case'), null);
});

test('sandbox preload exposes only the narrow preferences API', () => {
  let exposed, calls = [];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../preload.cjs'), 'utf8'), {
    require(name) {
      assert.equal(name, 'electron');
      return { contextBridge: { exposeInMainWorld(name, value) { assert.ok(['FundsDesktopPreferences', 'FundsDesktopUpdates'].includes(name)); if (name === 'FundsDesktopPreferences') exposed = value; } },
        ipcRenderer: { sendSync(...args) { calls.push(args); return args[1] === 'get' ? '2' : true; } } };
    },
  });
  assert.deepEqual(Object.keys(exposed), ['getItem', 'setItem', 'removeItem']);
  assert.equal(Object.isFrozen(exposed), true);
  assert.equal(exposed.getItem('fund_case'), '2'); exposed.setItem('fund_case', 3); exposed.removeItem('fund_edge_label');
  assert.deepEqual(calls, [[CHANNEL, 'get', 'fund_case'], [CHANNEL, 'set', 'fund_case', '3'], [CHANNEL, 'remove', 'fund_edge_label']]);
});
