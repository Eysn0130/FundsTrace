'use strict';
// CI-only prior-client baseline. This helper observes preferences; only the
// installed client's real native resize/close may write the window state.
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const { TextDecoder } = require('node:util');
const { readWindowState } = require('../preferences.cjs');
const { assertNativeWindowBounds } = require('./installed-startup-journey.cjs');

const KEYS = Object.freeze(['fund_case', 'fund_edge_type', 'fund_show_ids', 'fund_show_photos', 'fund_edge_labels']);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');

function baselineConfig(value) {
  assert.ok(value && !Array.isArray(value) && typeof value === 'object');
  assert.deepEqual(Object.keys(value).sort(), ['preferences', 'version']);
  assert.match(value.version, /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/);
  assert.ok(value.preferences && !Array.isArray(value.preferences) && typeof value.preferences === 'object');
  assert.deepEqual(Object.keys(value.preferences).sort(), [...KEYS].sort());
  for (const expected of Object.values(value.preferences)) assert.equal(typeof expected, 'string');
  return value;
}

function preferencesFrom(bytes, expected, windowRequired) {
  assert.ok(Buffer.isBuffer(bytes) && bytes.length > 0 && bytes.length <= 16384, 'PRESENTATION_BASELINE_SIZE');
  const text = new TextDecoder('utf-8', { fatal:true }).decode(bytes);
  const values = JSON.parse(text);
  assert.ok(values && !Array.isArray(values) && typeof values === 'object', 'PRESENTATION_BASELINE_OBJECT');
  const keys = [...KEYS, ...(windowRequired ? ['fund_window_state'] : [])].sort();
  assert.deepEqual(Object.keys(values).sort(), keys, 'PRESENTATION_BASELINE_KEYS');
  for (const key of KEYS) assert.equal(values[key], expected[key], 'PRESENTATION_BASELINE_ORIGINAL_VALUE');
  assert.equal(text, `${JSON.stringify(values)}\n`, 'PRESENTATION_BASELINE_CANONICAL_BYTES');
  if (windowRequired) assert.ok(readWindowState(values.fund_window_state), 'PRESENTATION_BASELINE_WINDOW_STATE');
  return values;
}

function windowMatchesNative(values, native) {
  const saved = readWindowState(values.fund_window_state);
  assert.ok(saved && saved.maximized === false, 'PRESENTATION_BASELINE_RESTORED_WINDOW');
  assert.ok(native.status === 'passed' && native.processBound === true && native.scaleFactor > 0,
    'PRESENTATION_BASELINE_NATIVE_BINDING');
  assertNativeWindowBounds(native, { ...saved.bounds,
    minWidth:native.electronMinTrack.width / native.scaleFactor,
    minHeight:native.electronMinTrack.height / native.scaleFactor });
  // Match the existing HWND/Electron DIP conversion contract. The window's raw
  // rectangle includes measured native frame insets; it is not getBounds().
  for (const key of ['x', 'y', 'width', 'height']) assert.ok(
    Math.abs(native.electronWindow[key] / native.scaleFactor - saved.bounds[key]) <= 2,
    'PRESENTATION_BASELINE_WINDOW_GEOMETRY');
}

function resizeTarget(native) {
  assert.ok(native.status === 'passed' && native.processBound === true && native.normalizationAvailable === true,
    'PRESENTATION_BASELINE_NATIVE_BINDING');
  const scale = native.scaleFactor;
  assert.ok(Number.isFinite(scale) && scale > 0);
  const target = {};
  for (const [key, extra] of [['width', 80], ['height', 5]]) {
    const minimum = Math.ceil(native.electronMinTrack[key] / scale);
    const maximum = Math.floor(native.workArea[key] / scale);
    assert.ok(minimum > 0 && maximum >= minimum, 'PRESENTATION_BASELINE_RESIZE_RANGE');
    let value = Math.min(minimum + extra, maximum);
    if (Math.abs(value - native.electronWindow[key] / scale) < 1) value = value === minimum ? maximum : minimum;
    target[key] = value;
  }
  assert.ok(['width','height'].some(key => Math.abs(target[key] - native.electronWindow[key] / scale) >= 1),
    'PRESENTATION_BASELINE_RESIZE_SPACE');
  return target;
}

async function runInstalledPresentationBaseline({ config, primary, evaluate, ready, nativeWindow,
  readPreferences, until, bounded, backendReleased, check }) {
  const expected = baselineConfig(config);
  await check('priorPresentationStartupCompleted', ready);
  const original = await check('priorPresentationPreferencesBefore', async () => {
    const state = await evaluate('window.FundsDesktopUpdates.state()');
    assert.equal(state.currentVersion, expected.version, 'PRESENTATION_BASELINE_PRIOR_VERSION');
    for (const key of KEYS) assert.equal(
      await evaluate(`window.FundsDesktopPreferences.getItem(${JSON.stringify(key)})`), expected.preferences[key]);
    assert.equal(await evaluate('window.FundsDesktopPreferences.getItem("fund_window_state")'), null,
      'PRESENTATION_BASELINE_MAIN_ONLY');
    const bytes = await readPreferences();
    preferencesFrom(bytes, expected.preferences, false);
    return bytes;
  });
  const resized = await check('priorPresentationNativeUserResize', async () => {
    const before = await nativeWindow();
    const target = resizeTarget(before);
    const value = await nativeWindow({ action:'UserResize', widthDip:target.width, heightDip:target.height });
    assert.ok(value.status === 'passed' && value.processBound === true, 'PRESENTATION_BASELINE_NATIVE_BINDING');
    assert.deepEqual(value.interaction, { source:'SendInput-native-resize-edge', userResizeObserved:true });
    assertNativeWindowBounds(value, { ...target,
      minWidth:before.electronMinTrack.width / before.scaleFactor,
      minHeight:before.electronMinTrack.height / before.scaleFactor });
    assert.equal(value.scaleFactor, before.scaleFactor, 'PRESENTATION_BASELINE_DPI_CHANGED');
    assert.ok(value.client.width !== before.client.width || value.client.height !== before.client.height,
      'PRESENTATION_BASELINE_RESIZE_NOT_OBSERVED');
    return value;
  });
  const persisted = await check('priorPresentationWindowPersisted', async () => {
    const bytes = await until(async () => {
      const current = await readPreferences();
      const parsed = JSON.parse(new TextDecoder('utf-8', { fatal:true }).decode(current));
      if (!Object.hasOwn(parsed, 'fund_window_state')) {
        preferencesFrom(current, expected.preferences, false);
        return false; // Main's existing 250 ms save timer has not run yet.
      }
      const values = preferencesFrom(current, expected.preferences, true);
      windowMatchesNative(values, resized);
      return current;
    }, 5000);
    assert.equal(await evaluate('window.FundsDesktopPreferences.getItem("fund_window_state")'), null,
      'PRESENTATION_BASELINE_MAIN_ONLY');
    return bytes;
  });
  await check('priorPresentationNativeCaptionClose', async () => {
    const value = await nativeWindow({ action:'CaptionClose' });
    assert.ok(value.status === 'passed' && value.processBound === true, 'PRESENTATION_BASELINE_NATIVE_CLOSE');
    assert.equal(value.interaction.CloseButtonClicked, true);
    assert.equal(value.interaction.WindowClosedObserved, true);
  });
  await check('priorPresentationCleanExit', async () => {
    assert.deepEqual(await bounded(primary.exited, 30000), { code:0, signal:null });
  });
  await check('priorPresentationBackendReleased', () => until(backendReleased, 15000));
  const final = await check('priorPresentationBaselineBytes', async () => {
    const bytes = await readPreferences();
    const values = preferencesFrom(bytes, expected.preferences, true);
    windowMatchesNative(values, resized);
    assert.deepEqual(bytes, persisted, 'PRESENTATION_BASELINE_CLOSE_CHANGED_BYTES');
    return bytes;
  });
  return { priorVersion:expected.version, originalSha256:hash(original), baselineSha256:hash(final),
    originalPreferencesUnchanged:true, onlyExpectedKeys:true, windowStateMatchesNative:true,
    windowStateRendererReadable:false, nativeUserResizeObserved:true, nativeCaptionCloseObserved:true,
    processExitedZero:true, backendReleased:true, closePreservedPreferenceBytes:true };
}

module.exports = { baselineConfig, runInstalledPresentationBaseline };
