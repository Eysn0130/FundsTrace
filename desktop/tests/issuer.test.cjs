'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { createIssuer, machineCode, expiryCode, payloadBytes, saveNewFiles, DOMAIN } = require('../issuer/core.cjs');
const { trustedSender, localResource } = require('../issuer/policy.cjs');
// RFC 8032 test vector 1: this published seed is not a deployment signing key.
const seed = '9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60';
const key = crypto.createPrivateKey({ key: Buffer.from(`302e020100300506032b657004220420${seed}`, 'hex'), format: 'der', type: 'pkcs8' });
const publicKey = crypto.createPublicKey({ key: Buffer.from('302a300506032b6570032100d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a', 'hex'), type: 'spki', format: 'der' });
const today = new Date(2026, 9, 15, 23, 59);
async function temporary(t) { const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'fundstrace-issuer-test-')); t.after(() => fs.rm(dir, { recursive: true, force: true })); return dir; }
test('FT2 machine/date contract normalizes input and rejects invalid or past calendar dates', () => {
  assert.equal(machineCode(' abcd-1234-5678-90ef '), 'ABCD-1234-5678-90EF');
  for (const machine of ['', 'ABCD1234567890EF', 'ABCD-1234-5678-90EG', 12]) assert.throws(() => machineCode(machine));
  assert.equal(expiryCode('0', today), '0'); assert.equal(expiryCode('2026-10-15', today), '20261015');
  assert.equal(expiryCode('2028-02-29', today), '20280229');
  for (const expiry of ['2026-10-14', '2027-02-29', '2026-13-01', '2026-10-32', '2026-1-15', 0, null]) assert.throws(() => expiryCode(expiry, today));
});
test('published RFC key produces domain-bound canonical FT2 payload and a verifiable 64-byte signature', async t => {
  const dir = await temporary(t), file = path.join(dir, 'synthetic-rfc-key.pem');
  await fs.writeFile(file, key.export({ type: 'pkcs8', format: 'pem' }));
  const issuer = createIssuer({ today: () => today }); await issuer.load(file);
  const result = issuer.issue('abcd-1234-5678-90ef', '2026-10-15');
  const [, payload, signature] = result.code.split('.'), bytes = Buffer.from(payload, 'base64url');
  assert.equal(bytes.toString(), '{"v":2,"p":"FundsTrace","m":"B153F5DBD18EEA1D","e":"20261015"}');
  assert.deepEqual(bytes, payloadBytes('ABCD-1234-5678-90EF', '2026-10-15', today));
  assert.equal(signature.includes('='), false); assert.equal(Buffer.from(signature, 'base64url').length, 64);
  assert.equal(crypto.verify(null, Buffer.concat([DOMAIN, bytes]), publicKey, Buffer.from(signature, 'base64url')), true);
  assert.equal(crypto.verify(null, bytes, publicKey, Buffer.from(signature, 'base64url')), false);
  assert.equal(JSON.parse(Buffer.from(issuer.issue('ABCD-1234-5678-90EF', '0').code.split('.')[1], 'base64url')).e, '0');
  assert.equal(issuer.copyValue('code').startsWith('FT2.'), true); assert.equal(issuer.copyValue('public'), result.publicKey);
  assert.equal(result.fingerprint, crypto.createHash('sha256').update(publicKey.export({ type: 'spki', format: 'der' })).digest('hex').slice(0, 16).toUpperCase());
  issuer.clear(); assert.equal(issuer.state().loaded, false); assert.throws(() => issuer.issue('ABCD-1234-5678-90EF', '0')); assert.throws(() => issuer.copyValue('code'));
});
test('encrypted PKCS8 loads; wrong password, malformed, oversized and other key types fail safely', async t => {
  const dir = await temporary(t), issuer = createIssuer(), file = path.join(dir, 'synthetic-only.pem');
  await fs.writeFile(file, key.export({ type: 'pkcs8', format: 'pem', cipher: 'aes-256-cbc', passphrase: 'synthetic-test-passphrase' }));
  const state = await issuer.load(file, 'synthetic-test-passphrase'); assert.equal(state.loaded, true);
  const otherType = crypto.createPrivateKey({ key: Buffer.from(`302e020100300506032b656e04220420${seed}`, 'hex'), format: 'der', type: 'pkcs8' });
  for (const action of [() => issuer.load(file, 'wrong-synthetic-password'), async () => { await fs.writeFile(file, 'x'.repeat(16385)); return issuer.load(file); }, async () => { await fs.writeFile(file, 'invalid synthetic private data'); return issuer.load(file); }, async () => { await fs.writeFile(file, publicKey.export({ type: 'spki', format: 'pem' })); return issuer.load(file); }, async () => { await fs.writeFile(file, otherType.export({ type: 'pkcs8', format: 'pem' })); return issuer.load(file); }]) {
    await assert.rejects(action, error => !error.message.includes(dir) && !error.message.includes('synthetic') && error.code === 'key');
  }
  assert.deepEqual(issuer.state(), state, 'a failed replacement retains the previously loaded key');
  await assert.rejects(() => issuer.create('short', path.join(dir, 'new.pem'), path.join(dir, 'public.pem')), error => error.code === 'password');
});
test('new-file writes never overwrite; partial failures remove only files created by this operation', async t => {
  const dir = await temporary(t), first = path.join(dir, 'new.pem'), existing = path.join(dir, 'existing.pem');
  await fs.writeFile(existing, 'preserved synthetic file');
  await assert.rejects(() => saveNewFiles([[first, 'synthetic'], [existing, 'replacement']]), error => error.code === 'save');
  await assert.rejects(() => fs.stat(first), { code: 'ENOENT' }); assert.equal(await fs.readFile(existing, 'utf8'), 'preserved synthetic file');
  await assert.rejects(() => saveNewFiles([[first, 'a'], [first, 'b']])); await assert.rejects(() => fs.stat(first), { code: 'ENOENT' });
  await saveNewFiles([[first, 'synthetic']]); assert.equal(await fs.readFile(first, 'utf8'), 'synthetic');
});
test('IPC requires exact owned main frame and local resources have an explicit allowlist', () => {
  const page = 'file:///synthetic/issuer/index.html', frame = { url: page }, contents = { isDestroyed: () => false, mainFrame: frame };
  assert.equal(trustedSender({ sender: contents, senderFrame: frame }, contents, page), true);
  assert.equal(trustedSender({ sender: {}, senderFrame: frame }, contents, page), false);
  assert.equal(trustedSender({ sender: contents, senderFrame: { url: page } }, contents, page), false);
  frame.url = `${page}?injected`; assert.equal(trustedSender({ sender: contents, senderFrame: frame }, contents, page), false);
  for (const url of ['https://example.com/', 'http://127.0.0.1/', 'file:///arbitrary/private.pem']) assert.equal(localResource(url, new Set([page])), false);
});
test('issuer packaging has its own identity and excludes backend/business resources', () => {
  const builder = require('../issuer/electron-builder.cjs');
  assert.equal(builder.appId, 'com.eysn.fundstrace.issuer'); assert.equal(builder.extraMetadata.main, 'issuer/main.cjs');
  assert.deepEqual(builder.files, ['package.json', 'issuer/main.cjs', 'issuer/core.cjs', 'issuer/policy.cjs', 'issuer/preload.cjs', 'issuer/renderer.js', 'issuer/index.html', 'issuer/style.css']);
  assert.equal(builder.files.some(file => /[?*]|\.pem$|\.key$/.test(file)), false);
  assert.deepEqual(builder.extraResources.map(item => item.from), ['../LICENSE', '../THIRD_PARTY_NOTICES.md']);
  assert.equal(builder.publish, null); assert.equal(builder.win.signAndEditExecutable, false);
});
