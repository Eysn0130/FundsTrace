'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { startupTarget, assertLayout, assertWaitingObservation, assertHandoffObservation,
  assertNativeClientGeometry, frameSummary, VIEWPORTS, DEVICE_SCALES } = require('./installed-startup-journey.cjs');

function layout(width = 1440, height = 880, dpr = 1) {
  const unscaledHeight = 388, scale = Math.min(1, (width - 48) / 360, (height - 48) / unscaledHeight);
  const group = { x:(width - 360 * scale) / 2, y:(height - unscaledHeight * scale) / 2,
    width:360 * scale, height:unscaledHeight * scale };
  const box = (w, h, y) => ({ x:width / 2 - w * scale / 2, y:group.y + y * scale,
    width:w * scale, height:h * scale });
  return { phase:'waiting', motion:'full', viewport:{ width, height, dpr }, overflow:{ width, height },
    surface:{ x:0, y:0, width, height }, brand:group, intrinsic:{ width:360, height:unscaledHeight }, scale,
    fonts:{ cn:46, en:21 }, loadingSlot:28,
    resources:{ iconLoaded:true, stylesheetLoaded:true, rendererInitialized:true },
    boxes:{ mark:box(176, 176, 0), caption:box(240, 128, 196), 'cn-name':box(106, 54, 196),
      wordmark:box(120, 28, 259), subtitle:box(240, 18, 305), wait:box(100, 28, 360),
      'wait-track':box(33, 2, 360), 'wait-label':box(100, 15, 373) } };
}

function sample(time, phase, exitOpacity, backdropOpacity = .6, overrides = {}) {
  const { brand, boxes } = layout();
  return { time, phase, exitOpacity, backdropOpacity, motion:'full', brandOpacity:1,
    captionOpacity:1, waitOpacity:.75, brandPresent:true, brandVisible:true,
    width:1440, height:880, brand, mark:boxes.mark, caption:boxes.caption, wait:boxes.wait, ...overrides };
}
function observation(samples, installedAt = 500) {
  return { installedAt, samples, resourceErrors:0, scriptErrors:0, cspViolations:0 };
}
function waiting() {
  return observation([sample(500, 'waiting', 1, 1), sample(1400, 'waiting', 1, 1),
    sample(3950, 'waiting', 1, .8), sample(6500, 'waiting', 1, .6), sample(7100, 'waiting', 1, .6)]);
}
function handoff({ reduced = false } = {}) {
  const scale = reduced ? 140 / 1100 : 1;
  // Independent boundary/midpoint examples: a continuous fade traverses both
  // bright and dim states before removal, on the real normal/reduced duration.
  return observation([[0, 1], [110, .9975], [275, .917], [550, .5], [825, .083], [990, .0025], [1100, 0]]
    .map(([time, opacity], index) => sample(8000 + time * scale, index === 6 ? 'complete' : 'handoff', opacity,
      reduced ? 1 : .6, { motion:reduced ? 'reduce' : 'full' })));
}

test('installed startup classification accepts only exact local Windows ASAR paths', () => {
  assert.equal(startupTarget('file:///C:/Users/CI/AppData/Local/Programs/FundsTrace/resources/app.asar/startup/index.html'), true);
  assert.equal(startupTarget('file:///D:/Program%20Files/FundsTrace/resources/app.asar/startup/index.html'), true);
  for (const value of [null, '', 'file:///Users/ci/resources/app.asar/startup/index.html',
    'file://server/C:/resources/app.asar/startup/index.html', 'https://example.test/resources/app.asar/startup/index.html',
    'file:///C:/resources/app.asar/startup/index.html?q=1', 'file:///C:/resources/app.asar/startup/index.html#x',
    'file:///C:/resources/app.asar/startup/index.html/', 'file:///C:/resources/app.asar.unpacked/startup/index.html',
    'file:///C:/resources/other/../app.asar/startup/index.html', 'file:///C:/resources/other/%2e%2e/app.asar/startup/index.html',
    'file:///C:/resources/app.asar/startup/%69ndex.html?x', 'file:///C:\\resources/app.asar/startup/index.html',
    'file:///C:/bad%00/resources/app.asar/startup/index.html', 'file:///C:/bad%zz/resources/app.asar/startup/index.html']) {
    assert.equal(startupTarget(value), false, String(value));
  }
});

test('all twenty logical viewport/DPR combinations retain proportional centered geometry', () => {
  const reference = assertLayout(layout(), { width:1440, height:880, dpr:1 });
  let count = 0;
  for (const [width, height] of VIEWPORTS) for (const dpr of DEVICE_SCALES) {
    assertLayout(layout(width, height, dpr), { width, height, dpr, reference });
    count++;
  }
  assert.equal(count, 20);
  assert.equal(layout(960, 580).scale, 1, 'normal minimum retains original Logo size');
  assert.ok(layout(683, 284).scale < 1, 'a short content area scales the entire group');
});

test('layout evidence rejects drift, clipped children, replaced Logo sizing and variable loading slots', () => {
  const cases = [
    [value => { value.brand.x += 10; }, /STARTUP_GROUP_CENTER/],
    [value => { value.boxes.mark.width = 144; }, /STARTUP_ORIGINAL_LOGO_SIZE/],
    [value => { value.boxes.wordmark.x += 8; }, /STARTUP_CHILD_ALIGNMENT/],
    [value => { value.loadingSlot = 0; }, /STARTUP_FIXED_LOADING_SLOT/],
    [value => { value.resources.iconLoaded = false; }, /STARTUP_RESOURCE_LOAD/],
    [value => { value.overflow.width += 5; }, /STARTUP_DOCUMENT_OVERFLOW/],
    [value => { value.fonts.cn = 38; }, /STARTUP_CN_FONT/],
  ];
  for (const [mutate, expected] of cases) {
    const value = layout(); mutate(value);
    assert.throws(() => assertLayout(value, { width:1440, height:880, dpr:1 }), expected);
  }
  const short = layout(683, 284); short.scale = 1;
  assert.throws(() => assertLayout(short, { width:683, height:284, dpr:1 }), /STARTUP_GROUP_SCALE/);
});

test('relative geometry check catches title/loading reflow even while all boxes remain centered', () => {
  const reference = assertLayout(layout(), { width:1440, height:880, dpr:1 });
  const value = layout(960, 440);
  value.boxes.wordmark.y += 4 * value.scale;
  assert.throws(() => assertLayout(value, { width:960, height:440, dpr:1, reference }), /STARTUP_RELATIVE_GEOMETRY_CHANGED/);
});

test('waiting records pure beginning, gradual forty-percent reveal, and no automatic completion', () => {
  const result = assertWaitingObservation(waiting());
  assert.equal(result.initialOpaqueObserved, true);
  assert.equal(result.finalBackdropOpacity, .6);
  assert.equal(result.frameIntervals.strictFrameRateGate, false);
  const late = waiting(); late.installedAt = 2000; late.samples.splice(0, 2);
  const partial = assertWaitingObservation(late);
  assert.equal(partial.initialOpaqueCoverage, 'late-attach');
  assert.equal(partial.initialOpaqueObserved, false, 'late observation cannot prove initial opaque phase');
});

test('waiting rejects premature readiness, reversals, over-transparent endpoint and rendering errors', () => {
  for (const [mutate, expected] of [
    [value => { value.samples[3].phase = 'handoff'; }, /STARTUP_LEFT_WAITING_BEFORE_RELEASE/],
    [value => { value.samples[3].backdropOpacity = .9; }, /STARTUP_WAITING_BACKDROP_REVERSED/],
    [value => { value.samples.at(-1).backdropOpacity = .4; }, /STARTUP_WAITING_BACKDROP_ENDPOINT/],
    [value => { value.samples[0].backdropOpacity = .9; value.samples[1].backdropOpacity = .9; }, /STARTUP_INITIAL_OPAQUE_MISSING/],
    [value => { value.scriptErrors = 1; }, /STARTUP_SCRIPT_ERROR/],
    [value => { value.cspViolations = 1; }, /STARTUP_CSP_VIOLATION/],
  ]) {
    const value = waiting(); mutate(value);
    assert.throws(() => assertWaitingObservation(value), expected);
  }
});

test('real handoff observations validate normal 1100ms and reduced 140ms parent fades', () => {
  for (const reduced of [false, true]) {
    const result = assertHandoffObservation(handoff({ reduced }), { targetGone:true });
    assert.equal(result.expectedDurationMs, reduced ? 140 : 1100);
    assert.equal(result.targetDisposed, true);
    assert.equal(result.lastObservedOpacity, 0);
  }
  const incompleteLastSample = handoff(); incompleteLastSample.samples.pop();
  assert.equal(assertHandoffObservation(incompleteLastSample, { targetGone:true }).completePhaseObserved, false,
    'the last read before target disposal need not observe an exact zero');
});

test('handoff rejects abrupt removal, independently hidden titles, resetting opacity and wrong timing', () => {
  for (const [mutate, expected] of [
    [value => { value.samples[2].brandPresent = false; }, /STARTUP_HANDOFF_GROUP_REMOVED_EARLY/],
    [value => { value.samples[2].captionOpacity = 0; }, /STARTUP_HANDOFF_CHILD_FADE_DIVERGED/],
    [value => { value.samples[2].brand.x += 12; }, /STARTUP_HANDOFF_GROUP_JUMP/],
    [value => { value.samples[4].exitOpacity = .9; }, /STARTUP_HANDOFF_OPACITY_REVERSED/],
    [value => { value.samples[3].backdropOpacity = .4; }, /STARTUP_HANDOFF_BACKDROP_CHANGED/],
    [value => { value.samples.forEach((item, index) => { item.time = 8000 + index * 20; }); }, /STARTUP_HANDOFF_DURATION/],
    [value => { value.samples.forEach(item => { item.exitOpacity = item.phase === 'complete' ? 0 : 1; }); }, /STARTUP_HANDOFF_SKIPPED_FADE/],
    [value => { value.samples.pop(); value.samples.at(-1).exitOpacity = .5; }, /STARTUP_HANDOFF_OPACITY_REVERSED|STARTUP_HANDOFF_DISAPPEARED_OPAQUE/],
  ]) {
    const value = handoff(); mutate(value);
    assert.throws(() => assertHandoffObservation(value, { targetGone:true }), expected);
  }
  assert.throws(() => assertHandoffObservation(handoff(), { targetGone:false }), /STARTUP_TARGET_NOT_DISPOSED/);
});

test('frame summaries report shared-runner gaps without declaring a strict 60fps gate', () => {
  const result = frameSummary([0, 16.7, 33.4, 50.1, 170.1].map(time => ({ time })));
  assert.equal(result.intervalCount, 4);
  assert.ok(Math.abs(result.maxGapMs - 120) < .001);
  assert.equal(result.p95Ms, result.maxGapMs);
  assert.equal(result.strictFrameRateGate, false);
});

test('Win32 client includes the Views menu while the WebContents viewport excludes it', () => {
  const native = {scaleFactor:1,client:{width:944,height:625}};
  assert.equal(assertNativeClientGeometry(native,{width:944,height:605,dpr:1}),20);
  assert.equal(assertNativeClientGeometry({scaleFactor:1.5,client:{width:1416,height:937.5}},
    {width:944,height:605,dpr:1.5},20),20);
  assert.throws(() => assertNativeClientGeometry(native,{width:960,height:605,dpr:1}),/STARTUP_NATIVE_CLIENT_WIDTH/);
  assert.throws(() => assertNativeClientGeometry(native,{width:944,height:626.5,dpr:1}),/STARTUP_NATIVE_CLIENT_CHROME/);
  assert.throws(() => assertNativeClientGeometry(native,{width:944,height:500,dpr:1}),/STARTUP_NATIVE_CLIENT_CHROME/);
  assert.throws(() => assertNativeClientGeometry(native,{width:944,height:590,dpr:1},20),/STARTUP_NATIVE_CLIENT_CHROME_CHANGED/);
});
