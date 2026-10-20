'use strict';
// CI fixture only: the installed program and its fixed HTTPS URLs stay unchanged.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const https = require('node:https');
const { createHash } = require('node:crypto');
const { Transform } = require('node:stream');
const { spawn } = require('node:child_process');

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, ms = 30000) {
  const deadline = Date.now() + ms;
  do { const value = await check(); if (value) return value; await delay(100); } while (Date.now() < deadline);
  throw new Error('UPDATE_JOURNEY_TIMEOUT');
}

async function createUpdateFixture(filename) {
  assert.equal(process.platform, 'win32'); assert.equal(process.env.GITHUB_ACTIONS, 'true');
  const config = JSON.parse(await fsp.readFile(filename, 'utf8'));
  const current = require('../package.json').version;
  const parts = current.split('.').map(Number);
  assert.equal(config.version, `${parts[0]}.${parts[1]}.${parts[2] + 1}`);
  for (const key of ['installer', 'pfx']) assert.ok(path.isAbsolute(config[key]));
  assert.match(config.passphrase, /^[A-Za-z0-9+/=]{24,200}$/);
  const bytes = await fsp.readFile(config.installer);
  assert.equal(bytes.subarray(0, 2).toString(), 'MZ');
  const sha512 = createHash('sha512').update(bytes).digest('base64');
  const sha256 = createHash('sha256').update(bytes).digest('hex');
  const size = bytes.length;
  const installerPath = `/releases/fundstrace/windows/${config.version}/FundsTrace-${config.version}-windows-x64-unsigned.exe`;
  const url = `https://download.analytix.top${installerPath}`;
  const metadata = { schemaVersion: 1, product: 'com.eysn.fundstrace', version: config.version,
    files: [{ url, sha512, size }], path: url, sha512, releaseDate: new Date().toISOString() };
  let mode = 'truncate';
  const requests = { manifest: 0, truncate: 0, corrupt: 0, valid: 0, rejected: 0, tlsFailures: 0,
    transferredBytes: 0, corruptCompleted: 0, validCompleted: 0 };
  const server = https.createServer({ pfx: await fsp.readFile(config.pfx), passphrase: config.passphrase }, (req, res) => {
    req.on('error', () => {}); res.on('error', () => {});
    if (req.method !== 'GET') { res.writeHead(405).end(); return; }
    const pathname = new URL(req.url, 'https://fixture.invalid').pathname;
    if (req.headers.host === 'analytix.top' && pathname === '/fundstrace/latest.yml') {
      requests.manifest++;
      res.writeHead(200, { 'Content-Type': 'application/yaml', 'Cache-Control': 'no-store' });
      res.end(JSON.stringify(metadata)); return;
    }
    if (req.headers.host !== 'download.analytix.top' || pathname !== installerPath) { requests.rejected++; res.writeHead(404).end(); return; }
    const selected = mode; requests[selected === 'good' ? 'valid' : selected]++;
    res.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Length': size, 'Cache-Control': 'no-store' });
    let first = true, transferred = 0;
    const source = fs.createReadStream(config.installer, { highWaterMark: 262144 });
    const throttle = new Transform({ transform(chunk, _encoding, callback) {
      transferred += chunk.length;
      requests.transferredBytes += chunk.length;
      if (selected === 'corrupt' && first) { chunk = Buffer.from(chunk); chunk[0] ^= 1; }
      first = false;
      if (selected === 'truncate' && transferred > 1048576) {
        source.destroy(); res.destroy(); callback(); return;
      }
      // Slow only the successful download enough to observe real progress.
      // Windows timer granularity must not delay every corrupt-file chunk.
      if (selected === 'good') setTimeout(() => callback(null, chunk), 2);
      else callback(null, chunk);
    } });
    source.on('error', () => res.destroy()); throttle.on('error', () => res.destroy());
    res.on('close', () => { source.destroy(); throttle.destroy(); });
    res.on('finish', () => {
      if (selected === 'corrupt') requests.corruptCompleted++;
      if (selected === 'good') requests.validCompleted++;
    });
    source.pipe(throttle).pipe(res);
  });
  server.on('tlsClientError', () => { requests.tlsFailures++; });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(443, '127.0.0.1', resolve); });
  return { version: config.version, sha256, requests, setMode(value) { mode = value; },
    close: () => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }) };
}

async function nativeButton(pid, choice) {
  const helper = path.resolve(__dirname, '../../scripts/click-windows-update-dialog.ps1');
  const child = spawn('pwsh.exe', ['-NoLogo', '-NoProfile', '-NonInteractive', '-File', helper,
    '-ApplicationPid', String(pid), '-Choice', choice], { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
  child.stdout.resume(); child.stderr.resume();
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { child.kill(); reject(new Error('UPDATE_DIALOG_TIMEOUT')); }, 25000);
    child.once('error', () => { clearTimeout(timer); reject(new Error('UPDATE_DIALOG_FAILED')); });
    child.once('exit', code => { clearTimeout(timer); code === 0 ? resolve() : reject(new Error('UPDATE_DIALOG_FAILED')); });
  });
}

async function runInstalledUpdateJourney({ fixture, primary, evaluate, clickElement, cdp, page, check, screenshot }) {
  const button = `document.querySelector('funds-desktop-update .funds-update-capsule')`;
  const phase = () => evaluate(`${button}?.dataset.phase`);
  const waitPhase = (value, ms = 30000) => until(async () => {
    const current = await phase();
    fixture.lastPhase = ['idle','checking','available','downloading','error','ready','installing','current'].includes(current) ? current : 'unknown';
    return current === value;
  }, ms);
  const click = () => clickElement(button);
  async function screenshotPhase(name) {
    if (!screenshot) return;
    const value = await cdp.send('Page.captureScreenshot', { format: 'png' }, page.sessionId);
    await fsp.writeFile(screenshot.replace(/\.png$/i, `.update-${name}.png`), Buffer.from(value.data, 'base64'), { flag: 'wx' });
  }
  await check('installedUpdateAvailableFromTrustedPrivateHttps', async () => {
    await waitPhase('available');
    assert.ok((await evaluate(`${button}.title`)).includes(`→ ${fixture.version}`));
    assert.ok(fixture.requests.manifest > 0);
  });
  await check('installedInterruptedUpdateRejected', async () => {
    await click(); await waitPhase('error'); assert.ok(!primary.done && fixture.requests.truncate > 0);
    await click(); await waitPhase('available');
  });
  await check('installedCorruptUpdateRejected', async () => {
    fixture.setMode('corrupt'); await click(); await waitPhase('error', 120000);
    assert.ok(!primary.done && fixture.requests.corrupt > 0 && fixture.requests.corruptCompleted > 0);
    await click(); await waitPhase('available');
  });
  await check('installedUpdateProgressAndReady', async () => {
    fixture.setMode('good'); await click(); await waitPhase('downloading');
    await until(async () => {
      const label = await evaluate(`${button}.textContent`);
      return /^(?:[1-9]|[1-9][0-9])%$/.test(label.trim());
    });
    await screenshotPhase('downloading'); await waitPhase('ready', 120000);
    assert.ok(fixture.requests.valid > 0 && fixture.requests.validCompleted > 0); await screenshotPhase('ready');
  });
  await check('installedUpdateLaterRetainsApplication', async () => {
    await click(); await nativeButton(primary.child.pid, 'later'); await waitPhase('ready');
    assert.ok(!primary.done);
    assert.equal(await evaluate(`document.readyState === 'complete' && !!document.querySelector('#graphContainer')`), true);
  });
  await check('installedUpdateConfirmedRestart', async () => {
    await click(); await nativeButton(primary.child.pid, 'restart');
    await until(() => primary.done, 30000);
    assert.deepEqual(await primary.exited, { code: 0, signal: null });
  });
  return { currentVersion: require('../package.json').version, nextVersion: fixture.version,
    installerSha256: fixture.sha256, requests: fixture.requests,
    privateFixtureOnly: true, productionFeedModified: false, tlsVerificationDisabled: false };
}

module.exports = { createUpdateFixture, runInstalledUpdateJourney };
