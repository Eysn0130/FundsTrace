'use strict';
// This journey uses the installed application's unchanged public updater.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const https = require('node:https');
const { createHash } = require('node:crypto');
const { nativeButton } = require('./installed-update-journey.cjs');

const FEED = 'https://analytix.top/fundstrace/latest.yml';
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(inspect, ms = 30000) {
  const deadline = Date.now() + ms;
  do { const value = await inspect(); if (value) return value; await delay(100); } while (Date.now() < deadline);
  throw new Error('UPDATE_JOURNEY_TIMEOUT');
}

function publicFeed() {
  return new Promise((resolve, reject) => {
    const request = https.get(FEED, { timeout: 30000 }, response => {
      if (response.statusCode !== 200) {
        response.resume(); const error = new Error('PRODUCTION_FEED_RESPONSE');
        error.statusCode = response.statusCode; reject(error); return;
      }
      const chunks = []; let bytes = 0;
      response.on('data', chunk => {
        bytes += chunk.length;
        if (bytes > 65536) { response.destroy(new Error('PRODUCTION_FEED_SIZE')); return; }
        chunks.push(chunk);
      });
      response.once('error', reject);
      response.once('end', () => {
        try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
        catch { reject(new Error('PRODUCTION_FEED_METADATA')); }
      });
    });
    request.once('timeout', () => request.destroy(new Error('PRODUCTION_FEED_TIMEOUT')));
    request.once('error', reject);
  });
}

async function installerDigests(filename) {
  const sha256 = createHash('sha256'), sha512 = createHash('sha512');
  let bytes = 0, signature;
  for await (const chunk of fs.createReadStream(filename)) {
    if (!signature) signature = chunk.subarray(0, 2).toString();
    bytes += chunk.length; sha256.update(chunk); sha512.update(chunk);
  }
  assert.equal(signature, 'MZ');
  return { bytes, sha256: sha256.digest('hex'), sha512: sha512.digest('base64') };
}

async function runInstalledProductionUpdateJourney({ configFile, primary, evaluate, clickElement, cdp, page, check, screenshot }) {
  const config = await check('installedProductionUpdateConfiguration', async () => {
    assert.equal(process.platform, 'win32'); assert.equal(process.env.GITHUB_ACTIONS, 'true');
    assert.notEqual(process.env.NODE_TLS_REJECT_UNAUTHORIZED, '0');
    const baseline = require('../upgrade-baseline.json');
    const value = JSON.parse((await fsp.readFile(configFile, 'utf8')).replace(/^\uFEFF/, ''));
    assert.equal(value.currentVersion, baseline.prior.version);
    assert.equal(value.version, baseline.targetVersion);
    assert.equal(value.version, require('../package.json').version);
    assert.match(value.installerSha256, /^[a-f0-9]{64}$/);
    assert.match(value.installerSha512, /^[A-Za-z0-9+/]{86}==$/);
    assert.ok(Number.isSafeInteger(value.installerBytes) && value.installerBytes > 0);
    assert.match(value.cacheDirectoryName, /^[a-z0-9][a-z0-9-]{0,127}$/);
    assert.ok(process.env.LOCALAPPDATA && path.isAbsolute(process.env.LOCALAPPDATA));
    assert.ok(value.preferences && !Array.isArray(value.preferences) && typeof value.preferences === 'object');
    assert.ok(Object.keys(value.preferences).length > 0);
    for (const [key, expected] of Object.entries(value.preferences)) {
      assert.match(key, /^fund_[a-z_]+$/); assert.equal(typeof expected, 'string');
    }
    assert.ok(!primary.child.spawnargs.some(argument => /^--(?:host-resolver-rules|ignore-certificate-errors(?:-spki-list)?|allow-insecure-localhost)(?:=|$)/.test(argument)));
    return value;
  });
  const filename = `FundsTrace-${config.version}-windows-x64-unsigned.exe`;
  const url = `https://download.analytix.top/releases/fundstrace/windows/${config.version}/${filename}`;
  const pending = path.join(process.env.LOCALAPPDATA, config.cacheDirectoryName, 'pending');
  const button = `document.querySelector('funds-desktop-update .funds-update-capsule')`;
  const status = () => evaluate('window.FundsDesktopUpdates.state()');
  const phase = () => evaluate(`${button}?.dataset.phase`);
  async function preferencesMatch() {
    for (const [key, expected] of Object.entries(config.preferences))
      assert.equal(await evaluate(`window.FundsDesktopPreferences.getItem(${JSON.stringify(key)})`), expected);
  }
  async function waitPhase(expected, ms = 30000) {
    return until(async () => {
      assert.ok(!primary.done);
      const value = await status();
      assert.notEqual(value.phase, 'error');
      assert.equal(value.currentVersion, config.currentVersion);
      return value.phase === expected && await phase() === expected;
    }, ms);
  }
  async function click() {
    await cdp.send('Page.bringToFront', {}, page.sessionId);
    await clickElement(button);
  }
  async function screenshotPhase(name) {
    if (!screenshot) return;
    const value = await cdp.send('Page.captureScreenshot', { format: 'png' }, page.sessionId);
    await fsp.writeFile(screenshot.replace(/\.png$/i, `.update-${name}.png`), Buffer.from(value.data, 'base64'), { flag: 'wx' });
  }
  await check('installedProductionFeedMatchesPublishedCandidate', async () => {
    const feed = await publicFeed();
    assert.equal(feed.schemaVersion, 1); assert.equal(feed.product, 'com.eysn.fundstrace');
    assert.equal(feed.version, config.version); assert.equal(feed.files.length, 1);
    assert.deepEqual(feed.files[0], { url, sha512: config.installerSha512, size: config.installerBytes });
    assert.equal(feed.path, url); assert.equal(feed.sha512, config.installerSha512);
    assert.equal(feed.checksumSha256, config.installerSha256);
    assert.ok(!feed.packages);
  });
  await check('installedProductionUpgradePreferencesBefore', preferencesMatch);
  await check('installedProductionUpdateAvailable', async () => {
    if (await phase() === 'idle') await click();
    await waitPhase('available', 45000);
    const value = await status();
    assert.equal(value.version, config.version);
    assert.ok((await evaluate(`${button}.title`)).includes(`${config.currentVersion} → ${config.version}`));
    await screenshotPhase('available');
  });
  await check('installedProductionUpdateCacheInitiallyEmpty', async () => {
    for (const name of [filename, 'update-info.json']) {
      const exists = await fsp.stat(path.join(pending, name)).then(() => true, error => {
        if (error.code === 'ENOENT') return false;
        throw error;
      });
      assert.equal(exists, false);
    }
  });
  let downloadMs = 0, downloadProgressObserved = false, maxObservedPercent = 0;
  await check('installedProductionUpdateDownloadedAndReady', async () => {
    const started = Date.now();
    await click();
    await until(async () => {
      assert.ok(!primary.done);
      const value = await status();
      assert.equal(value.currentVersion, config.currentVersion); assert.equal(value.version, config.version);
      assert.notEqual(value.phase, 'error');
      if (value.phase === 'downloading') {
        assert.ok(Number.isFinite(value.percent) && value.percent >= 0 && value.percent <= 100);
        maxObservedPercent = Math.max(maxObservedPercent, value.percent);
        if (!downloadProgressObserved) { downloadProgressObserved = true; await screenshotPhase('downloading'); }
      }
      return value.phase === 'ready' && await phase() === 'ready';
    }, 240000);
    downloadMs = Date.now() - started;
    await screenshotPhase('ready');
  });
  await check('installedProductionDownloadedInstallerIntegrity', async () => {
    const cached = JSON.parse(await fsp.readFile(path.join(pending, 'update-info.json'), 'utf8'));
    assert.equal(cached.fileName, filename); assert.equal(cached.sha512, config.installerSha512);
    const installer = path.join(pending, cached.fileName);
    const info = await fsp.stat(installer); assert.ok(info.isFile()); assert.equal(info.size, config.installerBytes);
    const actual = await installerDigests(installer);
    assert.deepEqual(actual, { bytes: config.installerBytes, sha256: config.installerSha256, sha512: config.installerSha512 });
  });
  await check('installedProductionUpdateLaterRetainsApplication', async () => {
    await click(); await nativeButton(primary.child.pid, 'later'); await waitPhase('ready');
    assert.equal(await evaluate(`document.readyState === 'complete' && !!document.querySelector('#graphContainer')`), true);
    await preferencesMatch();
  });
  await check('installedProductionUpdateConfirmedRestart', async () => {
    await click(); await nativeButton(primary.child.pid, 'restart');
    await until(() => primary.done, 30000);
    assert.deepEqual(await primary.exited, { code: 0, signal: null });
  });
  return { currentVersion: config.currentVersion, nextVersion: config.version,
    installerSha256: config.installerSha256, installerSha512: config.installerSha512,
    installerBytes: config.installerBytes, downloadMs, downloadProgressObserved, maxObservedPercent,
    cacheInitiallyEmpty: true, downloadedInstallerIntegrityVerified: true,
    productionFeed: true, privateFixtureOnly: false, productionFeedModified: false, tlsVerificationDisabled: false };
}

module.exports = { runInstalledProductionUpdateJourney };
