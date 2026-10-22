'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { pathToFileURL, fileURLToPath } = require('node:url');
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
test('issuer packaging has its own identity and excludes backend/business resources', async () => {
  const builder = require('../issuer/electron-builder.cjs');
  assert.equal(builder.appId, 'com.eysn.fundstrace.issuer'); assert.equal(builder.extraMetadata.main, 'issuer/main.cjs');
  assert.deepEqual(builder.files, ['package.json', 'issuer/main.cjs', 'issuer/core.cjs', 'issuer/policy.cjs', 'issuer/preload.cjs', 'issuer/renderer.js', 'issuer/index.html', 'issuer/style.css', 'assets/icon.png', 'assets/icon.ico',
    { from: path.resolve(__dirname, '../..'), to: '.', filter: ['LICENSE', 'LICENSE.en'] }]);
  assert.equal(builder.files.some(file => /[?*]|\.pem$|\.key$/.test(typeof file === 'string' ? file : file.from)), false);
  assert.deepEqual(builder.extraResources, [
    { from: '../LICENSE', to: 'LICENSE' },
    { from: '../LICENSE.en', to: 'LICENSE.en' },
    { from: '../THIRD_PARTY_NOTICES.md', to: 'THIRD_PARTY_NOTICES.md' },
    { from: '../LICENSES/MIT.txt', to: 'LICENSES/MIT.txt' },
    { from: '../docs/licensing.md', to: 'docs/licensing.md' },
    { from: '../docs/licensing.en.md', to: 'docs/licensing.en.md' },
  ]);
  assert.ok((await fs.readFile(path.join(builder.files.at(-1).from, 'LICENSE'), 'utf8')).includes('循迹 FundsTrace'));
  for (const resource of builder.extraResources) {
    assert.equal((await fs.stat(path.resolve(__dirname, '..', resource.from))).isFile(), true);
  }
  for (const filename of ['docs/licensing.md', 'docs/licensing.en.md']) {
    const licensing = builder.extraResources.find(resource => resource.to === filename);
    const licensingSource = path.resolve(__dirname, '..', licensing.from);
    const licensingText = await fs.readFile(licensingSource, 'utf8');
    for (const link of ['../LICENSE', '../LICENSE.en', '../LICENSES/MIT.txt', '../THIRD_PARTY_NOTICES.md']) {
      assert.ok(licensingText.includes(`](${link})`), `${filename}: ${link}`);
      const target = path.posix.normalize(path.posix.join(path.posix.dirname(licensing.to), link));
      const resource = builder.extraResources.find(item => item.to === target);
      assert.ok(resource, `Issuer licensing link must resolve to a distributed resource: ${link}`);
      assert.equal(path.resolve(__dirname, '..', resource.from), path.resolve(path.dirname(licensingSource), link));
    }
  }
  assert.equal(builder.publish, null); assert.equal(builder.win.signExecutable, false);
  assert.notEqual(builder.win.signAndEditExecutable, false);
});

test('Issuer uses the shared packaged icon for its page, window and macOS dock without loading keys', async () => {
  const source = await fs.readFile(path.join(__dirname, '../issuer/main.cjs'), 'utf8');
  const html = await fs.readFile(path.join(__dirname, '../issuer/index.html'), 'utf8');
  const builder = require('../issuer/electron-builder.cjs');
  const image = /<img\b[^>]*\bsrc="([^"]+)"/.exec(html)?.[1];
  const favicon = /<link\b[^>]*\brel="icon"[^>]*\bhref="([^"]+)"/.exec(html)?.[1];
  assert.equal(image, '../assets/icon.png');
  assert.equal(favicon, image);
  const csp = /http-equiv="Content-Security-Policy" content="([^"]+)"/.exec(html)?.[1];
  assert.match(csp, /(?:^|;\s*)img-src 'self'(?:;|$)/);
  assert.match(csp, /(?:^|;\s*)connect-src 'none'(?:;|$)/);
  const pageResources = [...html.matchAll(/<(?:link|script|img)\b[^>]*\b(?:src|href)="([^"]+)"/g)].map(match => match[1]);
  for (const platform of ['win32', 'darwin']) {
    const app = new EventEmitter(), state = { errors: [] };
    app.setName = app.setAppUserModelId = app.setPath = app.quit = () => {};
    app.getPath = () => '/synthetic/app-data';
    app.requestSingleInstanceLock = () => true;
    app.whenReady = () => Promise.resolve();
    if (platform === 'darwin') app.dock = { setIcon: icon => { state.dockIcon = icon; } };
    const local = new EventEmitter();
    local.setPermissionRequestHandler = local.setPermissionCheckHandler = () => {};
    local.webRequest = { onBeforeRequest: handler => { state.request = handler; } };
    class Window extends EventEmitter {
      constructor(options) {
        super(); state.options = options;
        this.webContents = new EventEmitter();
        this.webContents.setWindowOpenHandler = () => {};
      }
      async loadURL(url) { state.url = url; }
    }
    const electron = { app, BrowserWindow: Window,
      ipcMain: { handle() {} }, session: { fromPartition: () => local },
      screen: { getPrimaryDisplay: () => ({ workArea: { width: 1920, height: 1040 } }) },
      Menu: { buildFromTemplate: value => value, setApplicationMenu() {} },
      dialog: { showErrorBox: (...args) => state.errors.push(args) } };
    vm.runInNewContext(source, { require: name => {
      if (name === 'electron') return electron;
      if (name === './core.cjs') return { createIssuer: () => ({ clear() {} }) };
      if (name === './policy.cjs') return require('../issuer/policy.cjs');
      return require(name);
    }, __dirname: path.resolve(__dirname, '../issuer'), process: { platform } });
    await new Promise(resolve => setImmediate(resolve));
    const relative = `assets/icon.${platform === 'win32' ? 'ico' : 'png'}`;
    const icon = path.resolve(__dirname, '..', relative);
    assert.ok(builder.files.includes(relative));
    assert.equal(state.options.icon, icon);
    assert.equal(state.dockIcon, platform === 'darwin' ? icon : undefined);
    assert.equal(state.url, pathToFileURL(path.resolve(__dirname, '../issuer/index.html')).href);
    function cancelled(url) {
      let value;
      state.request({ url }, result => { value = result.cancel; });
      return value;
    }
    for (const name of pageResources) {
      const url = new URL(name, state.url).href;
      assert.equal(cancelled(url), false, `Required issuer resource is blocked: ${name}`);
      assert.ok(builder.files.includes(path.relative(path.resolve(__dirname, '..'), fileURLToPath(url)).split(path.sep).join('/')));
    }
    const sharedImage = new URL(image, state.url).href;
    for (const url of [sharedImage + '?injected', new URL('../assets/private.pem', state.url).href, 'https://example.com/icon.png', 'data:image/png;base64,c3ludGhldGlj']) {
      assert.equal(cancelled(url), true, 'only exact allowlisted local resources are available to the page');
    }
    assert.deepEqual(state.errors, []);
  }
});
