'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { sourceCopies } = require('../source-copies.cjs');
const sourceId = '0123456789abcdef';
function owner() { const sender = { isDestroyed: () => false }, frame = { url: 'http://127.0.0.1:18001/' }; sender.mainFrame = frame; return { sender, senderFrame: frame }; }
async function setup(t, options = {}) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'fundstrace-source-test-')), opened = [], revealed = [], requests = [];
  const bytes = Buffer.from('合成流水,金额\nSYNTHETIC,9999999999999999.99\n');
  const response = () => new Response(bytes, { headers: { 'content-disposition': "attachment; filename*=utf-8''" + encodeURIComponent('合成原件.csv') } });
  const service = sourceCopies({ origin: 'http://127.0.0.1:18001', token: 'SYNTHETIC-TEST-TOKEN', temporaryDirectory: root,
    shell: { openPath: async value => { opened.push(value); return ''; }, showItemInFolder: value => revealed.push(value) }, app: { getFileIcon: async () => ({ toDataURL: () => 'data:image/png;base64,AA==' }) },
    fetchSource: async (url, init) => { requests.push({ url, init }); return response(); }, ...options });
  t.after(async () => { await service.dispose(); await fs.rm(root, { recursive: true, force: true }); });
  return { service, root, bytes, opened, revealed, requests };
}
test('opens and reveals the same verified read-only copy using only a sender-owned ticket', async t => {
  const { service, root, bytes, opened, revealed, requests } = await setup(t), event = owner();
  const prepared = await service.prepare(event, { caseId: '17', sourceId });
  assert.equal(prepared.icon, 'data:image/png;base64,AA=='); assert.equal('filename' in prepared, false);
  assert.equal(requests[0].url, `http://127.0.0.1:18001/api/v1/cases/17/sources/${sourceId}/file`);
  assert.equal(requests[0].init.redirect, 'error'); assert.equal(requests[0].init.headers['X-FundsTrace-Desktop'], 'SYNTHETIC-TEST-TOKEN');
  await service.perform(event, { ticket: prepared.ticket, action: 'open' });
  const again = await service.prepare(event, { caseId: '17', sourceId }); assert.equal(again.ticket, prepared.ticket); assert.equal(requests.length, 1);
  await service.perform(event, { ticket: again.ticket, action: 'reveal' }); assert.equal(opened[0], revealed[0]);
  assert.equal(path.dirname(path.dirname(opened[0])), root); assert.deepEqual(await fs.readFile(opened[0]), bytes);
  if (process.platform !== 'win32') assert.equal((await fs.stat(opened[0])).mode & 0o222, 0);
  await assert.rejects(service.perform(owner(), { ticket: prepared.ticket, action: 'open' }));
  event.senderFrame.url = 'http://127.0.0.1:18001/workspace/'; await assert.rejects(service.perform(event, { ticket: prepared.ticket, action: 'open' }));
  await service.dispose(); assert.deepEqual(await fs.readFile(opened[0]), bytes); // The OS may still have this exported copy open.
});
test('releases an unexposed preparation without opening it', async t => {
  const { service, root, opened } = await setup(t), event = owner();
  const prepared = await service.prepare(event, { caseId: '1', sourceId });
  await service.release(owner(), prepared.ticket); assert.equal((await fs.readdir(root)).length, 1);
  await service.release(event, prepared.ticket); assert.deepEqual(await fs.readdir(root), []); assert.deepEqual(opened, []);
  await assert.rejects(service.perform(event, { ticket: prepared.ticket, action: 'open' }));
});
test('rejects paths, executable suffixes, foreign IDs and a failed source response', async t => {
  for (const name of ['../outside.csv','C:\\outside.csv','payload.csv.exe','pack.zip','unsafe:stream.csv']) {
    const { service, root, opened } = await setup(t, { fetchSource: async () => new Response('synthetic', { headers: { 'content-disposition': "attachment; filename*=utf-8''" + encodeURIComponent(name) } }) });
    await assert.rejects(service.prepare(owner(), { caseId: '1', sourceId })); assert.deepEqual(await fs.readdir(root), []); assert.deepEqual(opened, []);
  }
  const { service, requests } = await setup(t);
  for (const ids of [{caseId:'../1',sourceId},{caseId:1,sourceId},{caseId:'1',sourceId:'../source'}]) await assert.rejects(service.prepare(owner(), ids));
  assert.equal(requests.length, 0);
  const failed = await setup(t, { fetchSource: async () => new Response('', { status: 404 }) });
  await assert.rejects(failed.service.prepare(owner(), { caseId: '2', sourceId })); assert.deepEqual(await fs.readdir(failed.root), []);
});
test('bounds the stream and cleans partial files when the backend read fails', async t => {
  for (const oversized of [true, false]) {
    const body = { async *[Symbol.asyncIterator]() { yield Buffer.from('SYNTHETIC'); if (oversized) yield { byteLength: 300 * 1024 * 1024 + 1 }; else throw new Error('synthetic failure'); } };
    const { service, root, opened } = await setup(t, { fetchSource: async () => ({ ok:true, headers:new Headers({'content-disposition':"attachment; filename*=utf-8''synthetic.csv"}), body }) });
    await assert.rejects(service.prepare(owner(), { caseId: '1', sourceId })); assert.deepEqual(await fs.readdir(root), []); assert.deepEqual(opened, []);
  }
});
test('shutdown cancels an in-flight download and waits for its owned partial directory cleanup', async t => {
  let entered; const ready = new Promise(resolve => { entered = resolve; });
  const { service, root, opened } = await setup(t, { fetchSource: async (_url, init) => { entered(); return new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(new Error('aborted')), { once:true })); } });
  const preparation = service.prepare(owner(), { caseId: '1', sourceId }); const rejected = assert.rejects(preparation);
  await ready; await service.dispose(); await rejected; assert.deepEqual(await fs.readdir(root), []); assert.deepEqual(opened, []);
});
