'use strict';
// CI-only observation of the installed, unchanged startup document. The caller
// may pause its own business renderer while measuring; this is not cold-start
// timing, an OS display-setting change, or a substitute for Windows 10/11 review.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { performance } = require('node:perf_hooks');

const VIEWPORTS = Object.freeze([[1440, 880], [1302, 604], [960, 580], [960, 440], [683, 284]].map(Object.freeze));
const DEVICE_SCALES = Object.freeze([1, 1.25, 1.5, 2]);
const OBSERVATION_KEY = '__fundsTraceStartupObservation';
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const near = (actual, expected, tolerance = 1) => Number.isFinite(actual) && Math.abs(actual - expected) <= tolerance;

function installedLocalTarget(value, kind) {
  try {
    if (!['startup', 'titlebar'].includes(kind)) return false;
    if (typeof value !== 'string' || !/^file:\/\/\//i.test(value) || /%2f|%5c/i.test(value)) return false;
    const url = new URL(value);
    if (url.protocol !== 'file:' || url.hostname || url.username || url.password || url.port || url.search || url.hash) return false;
    const pathname = decodeURIComponent(value.slice(value.indexOf('://') + 3));
    // Do not normalize traversal into a permitted target or accept an unpacked
    // checkout. The Windows installed ASAR path is an absolute drive path.
    if (!/^\/[A-Za-z]:\//.test(pathname) || pathname.includes('\\') || /[\u0000-\u001f]/.test(pathname)) return false;
    if (pathname.split('/').some(part => part === '.' || part === '..')) return false;
    if (pathname !== decodeURIComponent(url.pathname)) return false;
    return pathname.endsWith(`/resources/app.asar/${kind}/index.html`);
  } catch { return false; }
}
const startupTarget = value => installedLocalTarget(value, 'startup');

const INSTALL_OBSERVATION = `(() => {
  if (window.${OBSERVATION_KEY}) return true;
  const surface = document.querySelector('#startup-surface');
  const brand = document.querySelector('.startup-brand');
  const backdrop = document.querySelector('.startup-backdrop');
  const caption = document.querySelector('.startup-caption');
  const wait = document.querySelector('.startup-wait');
  if (!surface || !brand || !backdrop || !caption || !wait) return false;
  const state = { installedAt: performance.now(), samples: [], resourceErrors: 0,
    scriptErrors: 0, cspViolations: 0, stopped: false, sampleLimit: 4096 };
  const number = value => Math.round(value * 10000) / 10000;
  const rect = element => {
    const box = element.getBoundingClientRect();
    return { x:number(box.x), y:number(box.y), width:number(box.width), height:number(box.height) };
  };
  let geometry, geometryAt = -Infinity, width = -1, height = -1, raf = 0;
  function sample(now, source, rafTime) {
    if (now - geometryAt >= 100 || width !== innerWidth || height !== innerHeight) {
      width = innerWidth; height = innerHeight; geometryAt = now;
      geometry = { width, height, brand:rect(brand), mark:rect(document.querySelector('.startup-mark')),
        caption:rect(caption), wait:rect(wait) };
    }
    const style = getComputedStyle(surface);
    state.samples.push({ time:number(now), source, ...(source === 'raf' ? { rafTime:number(rafTime) } : {}), phase:surface.dataset.phase,
      motion:document.documentElement.dataset.motion,
      exitOpacity:number(Number(style.opacity)), backdropOpacity:number(Number(getComputedStyle(backdrop).opacity)),
      brandOpacity:number(Number(getComputedStyle(brand).opacity)),
      captionOpacity:number(Number(getComputedStyle(caption).opacity)),
      waitOpacity:number(Number(getComputedStyle(wait).opacity)),
      brandPresent:surface.contains(brand) && brand.contains(caption) && brand.contains(wait),
      brandVisible:getComputedStyle(brand).visibility === 'visible', ...geometry });
    if (state.samples.length > state.sampleLimit) state.samples.shift();
  }
  function tick(rafTime) { if (!state.stopped) { sample(performance.now(), 'raf', rafTime); raf = requestAnimationFrame(tick); } }
  const observer = new MutationObserver(() => sample(performance.now(), 'mutation'));
  observer.observe(surface, { attributes:true, attributeFilter:['data-phase'], childList:true, subtree:true });
  const resourceError = event => {
    if (['IMG','LINK','SCRIPT'].includes(event.target?.tagName)) state.resourceErrors++;
    else state.scriptErrors++;
  };
  const cspViolation = () => state.cspViolations++;
  addEventListener('error', resourceError, true);
  addEventListener('securitypolicyviolation', cspViolation);
  state.stop = () => { state.stopped = true; cancelAnimationFrame(raf); observer.disconnect();
    removeEventListener('error', resourceError, true); removeEventListener('securitypolicyviolation', cspViolation); };
  window.${OBSERVATION_KEY} = state;
  sample(performance.now(), 'initial');
  raf = requestAnimationFrame(tick);
  return true;
})()`;

const READ_OBSERVATION = `(() => {
  const value = window.${OBSERVATION_KEY};
  return value ? { installedAt:value.installedAt, samples:value.samples,
    resourceErrors:value.resourceErrors, scriptErrors:value.scriptErrors, cspViolations:value.cspViolations } : null;
})()`;

const READ_LAYOUT = `(() => {
  const surface = document.querySelector('#startup-surface'), brand = document.querySelector('.startup-brand');
  const rect = element => { const r = element.getBoundingClientRect();
    return { x:r.x, y:r.y, width:r.width, height:r.height }; };
  const elements = ['mark','caption','cn-name','wordmark','subtitle','wait','wait-track','wait-label'];
  const boxes = Object.fromEntries(elements.map(name => [name, rect(document.querySelector('.startup-'+name))]));
  const icon = document.querySelector('.startup-icon');
  return { phase:surface.dataset.phase, motion:document.documentElement.dataset.motion,
    viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio},
    overflow:{width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight},
    surface:rect(surface), brand:rect(brand), boxes,
    intrinsic:{width:brand.offsetWidth,height:brand.offsetHeight},
    scale:Number(getComputedStyle(surface).getPropertyValue('--brand-scale')),
    fonts:{cn:Number.parseFloat(getComputedStyle(document.querySelector('.startup-cn-name')).fontSize),
      en:Number.parseFloat(getComputedStyle(document.querySelector('.startup-wordmark')).fontSize)},
    loadingSlot:Number.parseFloat(getComputedStyle(document.querySelector('.startup-wait')).height),
    resources:{iconLoaded:icon.complete && icon.naturalWidth > 0,
      stylesheetLoaded:!!document.querySelector('link[rel=stylesheet]')?.sheet,
      rendererInitialized:surface.style.getPropertyValue('--brand-scale') !== ''} };
})()`;

function assertLayout(value, { width, height, dpr, reference } = {}) {
  assert.ok(value && value.phase === 'waiting', 'STARTUP_LAYOUT_PHASE');
  assert.equal(value.viewport.width, width, 'STARTUP_VIEWPORT_WIDTH');
  assert.equal(value.viewport.height, height, 'STARTUP_VIEWPORT_HEIGHT');
  assert.ok(near(value.viewport.dpr, dpr, .001), 'STARTUP_VIEWPORT_DPR');
  assert.ok(value.overflow.width <= width + 1 && value.overflow.height <= height + 1, 'STARTUP_DOCUMENT_OVERFLOW');
  assert.ok(value.resources.iconLoaded && value.resources.stylesheetLoaded && value.resources.rendererInitialized, 'STARTUP_RESOURCE_LOAD');
  assert.ok(near(value.surface.x, 0) && near(value.surface.y, 0)
    && near(value.surface.width, width) && near(value.surface.height, height), 'STARTUP_SURFACE_COVERAGE');
  assert.equal(value.intrinsic.width, 360, 'STARTUP_FIXED_GROUP_WIDTH');
  assert.ok(value.intrinsic.height > 300 && value.intrinsic.height < 500, 'STARTUP_INTRINSIC_HEIGHT');
  const scale = Math.min(1, (width - 48) / value.intrinsic.width, (height - 48) / value.intrinsic.height);
  assert.ok(near(value.scale, scale, .00002), 'STARTUP_GROUP_SCALE');
  const center = box => ({ x:box.x + box.width / 2, y:box.y + box.height / 2 });
  const midpoint = center(value.brand);
  assert.ok(near(midpoint.x, width / 2, .6) && near(midpoint.y, height / 2, .6), 'STARTUP_GROUP_CENTER');
  assert.ok(value.brand.x >= 23.3 && value.brand.y >= 23.3
    && value.brand.x + value.brand.width <= width - 23.3
    && value.brand.y + value.brand.height <= height - 23.3, 'STARTUP_GROUP_MARGIN');
  assert.ok(near(value.boxes.mark.width, 176 * scale, .25)
    && near(value.boxes.mark.height, 176 * scale, .25), 'STARTUP_ORIGINAL_LOGO_SIZE');
  assert.equal(value.fonts.cn, 46, 'STARTUP_CN_FONT');
  assert.equal(value.fonts.en, 21, 'STARTUP_EN_FONT');
  assert.equal(value.loadingSlot, 28, 'STARTUP_FIXED_LOADING_SLOT');
  assert.ok(near(value.boxes.wait.height, 28 * scale, .3), 'STARTUP_LOADING_SLOT_SCALE');
  for (const box of Object.values(value.boxes)) {
    assert.ok(near(center(box).x, midpoint.x, .8), 'STARTUP_CHILD_ALIGNMENT');
    assert.ok(box.x >= -.5 && box.y >= -.5 && box.x + box.width <= width + .5
      && box.y + box.height <= height + .5, 'STARTUP_CHILD_CLIP');
  }
  const order = ['mark', 'caption', 'wait'];
  for (let i = 1; i < order.length; i++) {
    const before = value.boxes[order[i - 1]], after = value.boxes[order[i]];
    assert.ok(after.y >= before.y + before.height - .5, 'STARTUP_VERTICAL_ORDER');
  }
  const relative = Object.fromEntries(Object.entries(value.boxes).map(([name, box]) => [name, {
    x:(box.x - value.brand.x) / scale, y:(box.y - value.brand.y) / scale,
    width:box.width / scale, height:box.height / scale,
  }]));
  if (reference) for (const name of Object.keys(relative)) for (const key of ['x', 'y', 'width', 'height']) {
    assert.ok(near(relative[name][key], reference[name][key], 1), 'STARTUP_RELATIVE_GEOMETRY_CHANGED');
  }
  return relative;
}

function assertNativeWindowBounds(native, expected) {
  const {window:raw,electronWindow:logical,frameInsets:insets,minTrack,electronMinTrack:minimum,scaleFactor:scale,workArea:area}=native;
  assert.ok(raw && logical && insets && minTrack && minimum && scale>0,'STARTUP_NATIVE_LOGICAL_BOUNDS_MISSING');
  for(const key of ['left','top','right','bottom']) assert.ok(Number.isInteger(insets[key]) && insets[key]>=0 && insets[key]<=256,
    'STARTUP_NATIVE_FRAME_INSETS');
  assert.ok(near(logical.x,raw.x+insets.left,1) && near(logical.y,raw.y+insets.top,1)
    && near(logical.width,raw.width-insets.left-insets.right,1)
    && near(logical.height,raw.height-insets.top-insets.bottom,1),'STARTUP_NATIVE_LOGICAL_CONVERSION');
  assert.ok(near(minimum.width,minTrack.width-insets.left-insets.right,1)
    && near(minimum.height,minTrack.height-insets.top-insets.bottom,1),'STARTUP_NATIVE_MIN_TRACK_CONVERSION');
  assert.ok(logical.x>=area.x-1 && logical.y>=area.y-1
    && logical.x+logical.width<=area.x+area.width+1 && logical.y+logical.height<=area.y+area.height+1,
  'STARTUP_NATIVE_WORK_AREA');
  assert.ok(near(logical.width/scale,expected.width,2) && near(logical.height/scale,expected.height,2),
    'STARTUP_NATIVE_DEFAULT_OR_RESIZED_BOUNDS');
  assert.ok(near(minimum.width/scale,expected.minWidth,2) && near(minimum.height/scale,expected.minHeight,2),
    'STARTUP_NATIVE_MIN_TRACK_BOUNDS');
}

function assertNativeClientGeometry(native, viewport, priorInset) {
  const scale = native.scaleFactor;
  assert.ok(near(viewport.dpr, scale, .01), 'STARTUP_NATIVE_CLIENT_DPR');
  assert.ok(near(viewport.width, native.client.width / scale, 2), 'STARTUP_NATIVE_CLIENT_WIDTH');
  // The persistent local caption owns exactly 32 DIP inside the Win32 client.
  // OS resize borders stay outside this measurement; no native menu strip is
  // allowed to introduce a second inset above either WebContentsView.
  const inset = native.client.height / scale - viewport.height;
  assert.ok(viewport.height > 0 && near(inset, 32, 2), 'STARTUP_NATIVE_CLIENT_CHROME');
  if (priorInset !== undefined) assert.ok(near(inset, priorInset, 2), 'STARTUP_NATIVE_CLIENT_CHROME_CHANGED');
  return inset;
}

function assertObservationHealth(value) {
  assert.ok(value && Array.isArray(value.samples) && value.samples.length, 'STARTUP_OBSERVATION_MISSING');
  assert.equal(value.resourceErrors, 0, 'STARTUP_RESOURCE_ERROR');
  assert.equal(value.scriptErrors, 0, 'STARTUP_SCRIPT_ERROR');
  assert.equal(value.cspViolations, 0, 'STARTUP_CSP_VIOLATION');
  for (let index = 0; index < value.samples.length; index++) {
    const sample = value.samples[index];
    assert.ok(Number.isFinite(sample.time) && ['waiting', 'handoff', 'complete'].includes(sample.phase), 'STARTUP_SAMPLE_VALUE');
    assert.ok(sample.exitOpacity >= 0 && sample.exitOpacity <= 1
      && sample.backdropOpacity >= 0 && sample.backdropOpacity <= 1, 'STARTUP_SAMPLE_OPACITY');
    if (index) assert.ok(sample.time >= value.samples[index - 1].time, 'STARTUP_CLOCK_REVERSED');
  }
}

function frameSummary(samples) {
  const frames = samples.some(sample => sample.source) ? samples.filter(sample => sample.source === 'raf') : samples;
  const times = frames.map(sample => Number.isFinite(sample.rafTime) ? sample.rafTime : sample.time);
  const gaps = times.slice(1).map((time, i) => time - times[i]).filter(gap => gap > .5);
  const sorted = [...gaps].sort((a, b) => a - b);
  return { intervalCount:gaps.length, p95Ms:sorted.length ? sorted[Math.ceil(sorted.length * .95) - 1] : null,
    maxGapMs:sorted.length ? sorted.at(-1) : null, strictFrameRateGate:false,
    scope:'shared-CI-CDP-observation; deliberately-held-startup; not-user-cold-start' };
}

function assertWaitingObservation(value) {
  assertObservationHealth(value);
  const samples = value.samples;
  assert.ok(samples.every(sample => sample.phase === 'waiting'), 'STARTUP_LEFT_WAITING_BEFORE_RELEASE');
  assert.ok(samples.every(sample => near(sample.exitOpacity, 1, .0001) && sample.brandPresent && sample.brandVisible), 'STARTUP_WAITING_GROUP_MISSING');
  const reduced = samples.at(-1).motion === 'reduce';
  for (let i = 1; i < samples.length; i++) {
    assert.ok(samples[i].backdropOpacity <= samples[i - 1].backdropOpacity + .001, 'STARTUP_WAITING_BACKDROP_REVERSED');
  }
  const opaque = samples.some(sample => near(sample.backdropOpacity, 1, .001));
  if (value.installedAt <= 1200) assert.ok(opaque, 'STARTUP_INITIAL_OPAQUE_MISSING');
  assert.ok(samples.at(-1).time >= 7000, 'STARTUP_WAITING_OBSERVATION_TOO_SHORT');
  assert.ok(near(samples.at(-1).backdropOpacity, reduced ? 1 : .6, .001), 'STARTUP_WAITING_BACKDROP_ENDPOINT');
  return { initialOpaqueObserved:opaque, initialOpaqueCoverage:value.installedAt <= 1200 ? 'early-attach' : 'late-attach',
    finalBackdropOpacity:samples.at(-1).backdropOpacity, reducedMotion:reduced,
    frameIntervals:frameSummary(samples) };
}

function assertHandoffObservation(value, { targetGone } = {}) {
  assertObservationHealth(value);
  const samples = value.samples.filter(sample => sample.phase === 'handoff' || sample.phase === 'complete');
  assert.ok(targetGone, 'STARTUP_TARGET_NOT_DISPOSED');
  assert.ok(samples.length >= 4, 'STARTUP_HANDOFF_INSUFFICIENT_SAMPLES');
  const first = samples[0], last = samples.at(-1), reduced = first.motion === 'reduce';
  const expectedDurationMs = reduced ? 140 : 1100;
  assert.ok(first.phase === 'handoff' && first.exitOpacity >= .9, 'STARTUP_HANDOFF_INITIAL_JUMP');
  let reachedMidpoint = false;
  for (let i = 0; i < samples.length; i++) {
    const sample = samples[i];
    assert.ok(sample.motion === first.motion, 'STARTUP_HANDOFF_MOTION_CHANGED');
    assert.ok(near(sample.backdropOpacity, first.backdropOpacity, .001), 'STARTUP_HANDOFF_BACKDROP_CHANGED');
    if (sample.phase === 'handoff') {
      assert.ok(sample.brandPresent && sample.brandVisible, 'STARTUP_HANDOFF_GROUP_REMOVED_EARLY');
      assert.ok(near(sample.brandOpacity, 1, .001) && near(sample.captionOpacity, 1, .001)
        && near(sample.waitOpacity, first.waitOpacity, .001), 'STARTUP_HANDOFF_CHILD_FADE_DIVERGED');
      assert.ok(near(sample.brand.x + sample.brand.width / 2, sample.width / 2, .8)
        && near(sample.brand.y + sample.brand.height / 2, sample.height / 2, .8), 'STARTUP_HANDOFF_GROUP_JUMP');
      for (const box of [sample.mark, sample.caption, sample.wait]) {
        assert.ok(near(box.x + box.width / 2, sample.width / 2, .8), 'STARTUP_HANDOFF_ALIGNMENT_JUMP');
      }
      assert.ok(near(sample.mark.width / sample.brand.width, 176 / 360, .002)
        && near((sample.wait.y - sample.brand.y) / sample.brand.width,
          (first.wait.y - first.brand.y) / first.brand.width, .004), 'STARTUP_HANDOFF_GEOMETRY_CHANGED');
    }
    if (i) assert.ok(sample.exitOpacity <= samples[i - 1].exitOpacity + .001, 'STARTUP_HANDOFF_OPACITY_REVERSED');
    if (sample.exitOpacity >= .2 && sample.exitOpacity <= .8) reachedMidpoint = true;
  }
  assert.ok(reachedMidpoint, 'STARTUP_HANDOFF_SKIPPED_FADE');
  // The shipped renderer computes opacity from rAF's frame timestamp. A busy
  // runner can execute that callback later; its performance.now() is not the
  // animation clock. The mutation sample still anchors the real phase change.
  const animationTime = sample => sample.source === 'raf' && Number.isFinite(sample.rafTime) ? sample.rafTime : sample.time;
  const observedSpanMs = animationTime(last) - first.time;
  assert.ok(observedSpanMs >= expectedDurationMs * .7 && observedSpanMs <= expectedDurationMs + 250,
    'STARTUP_HANDOFF_DURATION');
  assert.ok(last.exitOpacity < .2, 'STARTUP_HANDOFF_DISAPPEARED_OPAQUE');
  const quintic = value => { const x = Math.max(0, Math.min(1, value)); return x*x*x*(x*(x*6-15)+10); };
  // Phase mutation records the real beginning without replacing the bridge.
  // Small scheduling offsets are allowed; a 140ms fade cannot pass as 1100ms.
  for (const sample of samples) {
    const progress = ((animationTime(sample) - first.time) / expectedDurationMs - .04) / .92;
    assert.ok(near(sample.exitOpacity, 1 - quintic(progress), .1), 'STARTUP_HANDOFF_CURVE');
  }
  return { reducedMotion:reduced, expectedDurationMs, observedSpanMs, targetDisposed:true,
    lastObservedOpacity:last.exitOpacity, completePhaseObserved:last.phase === 'complete',
    frameIntervals:frameSummary(samples) };
}

async function createInstalledStartupProbe({ cdp, target, evaluate, until, screenshotPrefix } = {}) {
  assert.ok(cdp && target?.targetId && target?.sessionId && typeof evaluate === 'function' && typeof until === 'function');
  if (screenshotPrefix) assert.ok(path.isAbsolute(screenshotPrefix), 'STARTUP_SCREENSHOT_ABSOLUTE_PATH');
  await until(async () => {
    try { return await evaluate(INSTALL_OBSERVATION, target); } catch { return false; }
  }, 10000);
  await evaluate('document.fonts.ready.then(() => true)', target);
  await until(async () => {
    const value = await evaluate(READ_LAYOUT, target);
    return value.resources.iconLoaded && value.resources.stylesheetLoaded && value.resources.rendererInitialized;
  }, 10000);
  let waitingDone = false, latest;
  const read = async () => { latest = await evaluate(READ_OBSERVATION, target); return latest; };
  async function targetGone() {
    const { targetInfos } = await cdp.send('Target.getTargets');
    return !targetInfos.some(info => info.targetId === target.targetId);
  }
  return {
    async observeWaitingAndLayout() {
      const matrix = [], screenshots = [];
      let reference;
      try {
        for (const [width, height] of VIEWPORTS) for (const deviceScaleFactor of DEVICE_SCALES) {
          await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor,
            mobile:false, screenWidth:width, screenHeight:height }, target.sessionId);
          await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))', target);
          const snapshot = await evaluate(READ_LAYOUT, target);
          try {
            reference ||= assertLayout(snapshot, { width, height, dpr:deviceScaleFactor });
            assertLayout(snapshot, { width, height, dpr:deviceScaleFactor, reference });
          } catch (error) { error.startupLayout = snapshot; throw error; }
          matrix.push({ width, height, deviceScaleFactor, ...snapshot });
          const label = width === 1440 ? 'normal' : width === 960 && height === 580 ? 'minimum' : width === 683 ? 'small' : null;
          if (screenshotPrefix && deviceScaleFactor === 1 && label) {
            const screenshotPath = `${screenshotPrefix}-${label}.png`;
            await fs.mkdir(path.dirname(screenshotPath), { recursive:true });
            const { data } = await cdp.send('Page.captureScreenshot', { format:'png' }, target.sessionId);
            await fs.writeFile(screenshotPath, Buffer.from(data, 'base64'), { flag:'wx' });
            screenshots.push({ label, path:path.basename(screenshotPath), rendererSurfaceOnly:true, width, height, deviceScaleFactor });
          }
        }
      } finally {
        await cdp.send('Emulation.clearDeviceMetricsOverride', {}, target.sessionId);
      }
      await until(async () => {
        const value = await read();
        const last = value?.samples.at(-1);
        return last?.time >= 7000 && near(last.backdropOpacity, last.motion === 'reduce' ? 1 : .6, .001);
      }, 9000);
      let waiting;
      try { waiting = assertWaitingObservation(latest); }
      catch (error) { error.startupObservation = latest; throw error; }
      waitingDone = true;
      return { simulatedCssViewportOnly:true, actualSystemDpiValidated:false, matrix, screenshots, waiting };
    },
    async observeCompletion({ timeoutMs = 15000 } = {}) {
      assert.ok(waitingDone, 'STARTUP_LAYOUT_NOT_FINISHED');
      const deadline = performance.now() + timeoutMs;
      let gone = false;
      while (performance.now() < deadline) {
        try { await read(); } catch {
          gone = await targetGone();
          if (gone) break;
          throw new Error('STARTUP_OBSERVATION_READ_FAILED');
        }
        if (await targetGone()) { gone = true; break; }
        await delay(35);
      }
      let handoff;
      try { handoff = assertHandoffObservation(latest, { targetGone:gone }); }
      catch (error) { error.startupObservation = latest; throw error; }
      return { handoff, observation:latest, limits:{ deliberatelyHeldBusinessRenderer:true,
        measuredUserColdStart:false, actualSystemDpiValidated:false,
        rendererSurfaceScreenshotIsNotNativeComposition:true } };
    },
  };
}

module.exports = { installedLocalTarget, startupTarget, createInstalledStartupProbe, assertLayout, assertWaitingObservation,
  assertHandoffObservation, assertNativeWindowBounds, assertNativeClientGeometry, frameSummary, VIEWPORTS, DEVICE_SCALES };
