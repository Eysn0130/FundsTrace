'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const filename = path.join(__dirname, 'installed-production-update-journey.cjs');
const source = fs.readFileSync(filename, 'utf8');
const baseline = require('../upgrade-baseline.json');
const button = "document.querySelector('funds-desktop-update .funds-update-capsule')";

async function throughAvailability(stages) {
  const config = { currentVersion: baseline.prior.version, version: baseline.targetVersion,
    installerSha256: 'a'.repeat(64), installerSha512: 'A'.repeat(86) + '==', installerBytes: 1,
    cacheDirectoryName: 'fundstrace-updater', preferences: { fund_appearance: 'dark' } };
  const normalized = stages.map(stage => ({ phase: stage.phase, currentVersion: config.currentVersion,
    version: stage.phase === 'available' ? config.version : '', ...stage }));
  let next = 0, current = normalized[0], clicks = 0;
  const observed = [], completed = [], stop = new Error('STOP_AFTER_AVAILABLE');
  const module = { exports: {} };
  const context = vm.createContext({ module, Buffer, Date, setTimeout,
    process: { platform: 'win32', env: { GITHUB_ACTIONS: 'true', LOCALAPPDATA: path.resolve('synthetic-local') } },
    require(name) {
      if (name === 'node:fs/promises') return { readFile: async value => {
        assert.equal(value, 'synthetic-config'); return JSON.stringify(config);
      } };
      if (name === '../upgrade-baseline.json') return baseline;
      if (name === '../package.json') return { version: baseline.targetVersion };
      if (name === '../updates.cjs') return require('../updates.cjs');
      if (name === './installed-update-journey.cjs') return { nativeButton: () => assert.fail('unexpected native dialog') };
      return require(name);
    },
  });
  vm.runInContext(source, context, { filename });
  const evaluate = async expression => {
    if (expression === 'window.FundsDesktopUpdates.state()') {
      current = normalized[Math.min(next++, normalized.length - 1)];
      observed.push(current); return { ...current };
    }
    if (expression === `${button}?.dataset.phase`) return Object.hasOwn(current, 'uiPhase') ? current.uiPhase : current.phase;
    if (expression === `${button}.title`) return `FundsTrace ${current.currentVersion} → ${current.version}`;
    assert.equal(expression, 'window.FundsDesktopPreferences.getItem("fund_appearance")');
    return 'dark';
  };
  const error = await module.exports.runInstalledProductionUpdateJourney({
    configFile: 'synthetic-config', primary: { done: false, child: { spawnargs: [] } }, evaluate,
    clickElement: async () => { clicks++; assert.fail('automatic startup check must not click a hidden idle capsule'); },
    cdp: { send: async method => assert.equal(method, 'Page.bringToFront') }, page: { sessionId: 'synthetic' },
    check: async (name, action) => {
      if (name === 'installedProductionUpdateCacheInitiallyEmpty') throw stop;
      const value = await action(); completed.push(name); return value;
    }, observations: {},
  }).then(() => null, failure => failure);
  return { error, stop, clicks, observed, completed };
}

for (const [name, stages] of [
  ['idle then checking then available', [{ phase: 'idle' }, { phase: 'checking' }, { phase: 'available' }]],
  ['already available', [{ phase: 'available' }]],
  ['unmounted then idle then available', [{ phase: 'idle', uiPhase: undefined }, { phase: 'idle' }, { phase: 'available' }]],
]) {
  test(`production update journey waits for Main's automatic check without a pointer: ${name}`, async () => {
    const result = await throughAvailability(stages);
    assert.equal(result.error, result.stop);
    assert.equal(result.clicks, 0);
    assert.ok(result.completed.includes('installedProductionUpdateAvailable'));
    assert.deepEqual(result.observed.slice(0, stages.length).map(value => value.phase), stages.map(value => value.phase));
  });
}

for (const [name, stage] of [
  ['real update error', { phase: 'error', errorCode: 'ERR_CONNECTION_RESET' }],
  ['wrong installed version', { phase: 'available', currentVersion: '0.0.0' }],
  ['wrong offered version', { phase: 'available', version: '9.9.9' }],
]) {
  test(`production update availability still rejects ${name}`, async () => {
    const result = await throughAvailability([stage]);
    assert.equal(result.error?.code, 'ERR_ASSERTION');
    assert.equal(result.clicks, 0);
    assert.equal(result.completed.includes('installedProductionUpdateAvailable'), false);
  });
}
