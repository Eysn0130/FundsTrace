'use strict';
// CI-only inspection of the installed local caption. Nothing here is shipped.
const assert = require('node:assert/strict');
const { installedLocalTarget } = require('./installed-startup-journey.cjs');
const HEIGHT = 32;
const titlebarTarget = value => installedLocalTarget(value, 'titlebar');
const near = (actual, expected, tolerance = 1) => Number.isFinite(actual) && Math.abs(actual - expected) <= tolerance;
const KEY = '__fundsTraceCaptionObservation';

const READ_CAPTION = `(() => {
  const bar = document.querySelector('.titlebar'), content = document.querySelector('.titlebar-content');
  const name = document.querySelector('.titlebar-name'), icon = document.querySelector('.titlebar-icon');
  const menu = document.querySelector('#titlebar-menu'), overlay = navigator.windowControlsOverlay;
  if (!bar || !content || !name || !icon || !menu || !overlay) return null;
  const rect = element => { const r = element.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; };
  const area = overlay.getTitlebarAreaRect();
  const style = getComputedStyle(bar), nameStyle = getComputedStyle(name);
  return {time:performance.now(),timeOrigin:performance.timeOrigin,viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio},
    overlay:{visible:overlay.visible,area:{x:area.x,y:area.y,width:area.width,height:area.height}},
    bar:rect(bar),content:rect(content),name:rect(name),icon:rect(icon),menu:rect(menu),
    text:name.textContent.trim(),opacity:Number(style.opacity),visibility:style.visibility,
    fontSize:Number.parseFloat(nameStyle.fontSize),fontWeight:nameStyle.fontWeight,color:nameStyle.color,
    drag:style.getPropertyValue('-webkit-app-region'),menuDrag:getComputedStyle(menu).getPropertyValue('-webkit-app-region'),
    menuDisabled:menu.disabled,iconLoaded:icon.complete && icon.naturalWidth > 0,
    stylesheetLoaded:!!document.querySelector('link[rel=stylesheet]')?.sheet};
})()`;

function assertCaption(value, reference) {
  assert.ok(value && value.overlay.visible === true, 'TITLEBAR_NATIVE_OVERLAY_MISSING');
  assert.ok(value.iconLoaded && value.stylesheetLoaded, 'TITLEBAR_RESOURCES_MISSING');
  assert.ok(near(value.bar.x, 0) && near(value.bar.y, 0) && near(value.bar.height, HEIGHT)
    && near(value.bar.width, value.viewport.width), 'TITLEBAR_FIXED_CAPTION');
  assert.ok(value.opacity === 1 && value.visibility === 'visible', 'TITLEBAR_PARTICIPATED_IN_FADE');
  assert.equal(value.text, '循迹 FundsTrace', 'TITLEBAR_BRAND_TEXT');
  assert.ok(near(value.icon.width, 18) && near(value.icon.height, 18) && near(value.fontSize, 13), 'TITLEBAR_BRAND_SIZE');
  assert.equal(value.drag, 'drag', 'TITLEBAR_DRAG_REGION');
  assert.equal(value.menuDrag, 'no-drag', 'TITLEBAR_MENU_DRAG_REGION');
  const area = value.overlay.area;
  assert.ok(area.x >= 0 && area.y >= 0 && near(area.height, HEIGHT)
    && area.width > 100 && area.x + area.width <= value.viewport.width - 80,
  'TITLEBAR_NATIVE_CONTROLS_SAFE_AREA');
  for (const box of [value.content, value.icon, value.name, value.menu]) {
    assert.ok(box.x >= area.x - 1 && box.y >= area.y - 1
      && box.x + box.width <= area.x + area.width + 1
      && box.y + box.height <= HEIGHT + 1 && box.width > 0 && box.height > 0,
    'TITLEBAR_CONTENT_OVERLAPS_NATIVE_CONTROLS');
  }
  if (reference) {
    assert.equal(value.timeOrigin, reference.timeOrigin, 'TITLEBAR_DOCUMENT_REPLACED');
    assert.ok(near(value.viewport.dpr, reference.viewport.dpr, .001), 'TITLEBAR_ZOOM_CHANGED');
    for (const key of ['fontSize', 'fontWeight', 'color', 'opacity', 'visibility']) {
      assert.equal(value[key], reference[key], 'TITLEBAR_STYLE_CHANGED');
    }
    for (const name of ['icon', 'name', 'menu']) for (const key of ['x', 'y', 'width', 'height']) {
      assert.ok(near(value[name][key], reference[name][key]), 'TITLEBAR_BRAND_GEOMETRY_CHANGED');
    }
  }
  return value;
}

function assertCaptionBody(caption, body, native) {
  assert.ok(near(body.width, caption.viewport.width, 2)
    && near(body.height + HEIGHT, caption.viewport.height, 2), 'TITLEBAR_BODY_VIEWPORT');
  assert.ok(near(body.dpr, caption.viewport.dpr, .01), 'TITLEBAR_BODY_DPR');
  if (native) {
    assert.ok(near(caption.viewport.width, native.client.width / native.scaleFactor, 2)
      && near(caption.viewport.height, native.client.height / native.scaleFactor, 2), 'TITLEBAR_NATIVE_CLIENT');
  }
}

async function windowId(cdp, target) {
  const value = await cdp.send('Browser.getWindowForTarget', {targetId:target.targetId});
  assert.ok(Number.isInteger(value.windowId), 'TITLEBAR_NATIVE_WINDOW_BINDING');
  return value.windowId;
}

async function createInstalledTitlebarProbe({cdp, target, evaluate, until}) {
  await until(async () => {
    try { const value = await evaluate(READ_CAPTION, target); return value?.iconLoaded && value.stylesheetLoaded && value.overlay.visible; }
    catch { return false; }
  }, 15000);
  const initial = assertCaption(await evaluate(READ_CAPTION, target));
  const id = await windowId(cdp, target);
  await evaluate(`(() => {
    if (window.${KEY}) return;
    // Store numbers/styles only. The permanent local shell has no case data.
    const state = {samples:[],stopped:false};
    const sample = () => {
      const bar = document.querySelector('.titlebar'), name = document.querySelector('.titlebar-name');
      if (!bar || !name) {state.missing = true;return;}
      const r=bar.getBoundingClientRect(),s=getComputedStyle(bar),n=getComputedStyle(name);
      state.samples.push({time:performance.now(),height:r.height,y:r.y,opacity:Number(s.opacity),visibility:s.visibility,
        fontSize:Number.parseFloat(n.fontSize),timeOrigin:performance.timeOrigin});
      if (state.samples.length>2048) state.samples.shift();
    };
    sample();state.timer=setInterval(sample,50);state.stop=()=>{clearInterval(state.timer);state.stopped=true;sample();};
    window.${KEY}=state;
  })()`, target);
  let stopped = false;
  return {
    target, initial, windowId:id,
    async read({stable = true} = {}) {
      const value = assertCaption(await evaluate(READ_CAPTION, target), stable ? initial : undefined);
      return value;
    },
    async checkBody(bodyTarget, native) {
      assert.equal(await windowId(cdp, bodyTarget), id, 'TITLEBAR_BUSINESS_WINDOW_BINDING');
      const caption = await this.read();
      const body = await evaluate('({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})', bodyTarget);
      assertCaptionBody(caption, body, native);
      return {caption, body, sameNativeWindow:true, clientChromeInsetDip:HEIGHT};
    },
    async finishStartup() {
      if (!stopped) await evaluate(`window.${KEY}.stop()`, target);
      stopped = true;
      const value = await evaluate(`({samples:window.${KEY}.samples,missing:window.${KEY}.missing===true})`, target);
      assert.equal(value.missing, false, 'TITLEBAR_REMOVED_DURING_STARTUP');
      assert.ok(value.samples.length >= 4, 'TITLEBAR_STARTUP_SAMPLES_MISSING');
      for (const sample of value.samples) {
        assert.ok(near(sample.height, HEIGHT) && near(sample.y, 0) && sample.opacity === 1
          && sample.visibility === 'visible' && near(sample.fontSize, 13)
          && sample.timeOrigin === initial.timeOrigin, 'TITLEBAR_STARTUP_CHANGED');
      }
      return {persistentThroughWaitingHandoffAndReady:true,samples:value.samples,ready:await this.read()};
    },
  };
}

async function runInstalledTitlebarJourney({cdp, page, probe, evaluate, until, nativeAction}) {
  await cdp.send('Emulation.clearDeviceMetricsOverride', {}, page.sessionId);
  await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))', page);
  const body = await probe.checkBody(page);
  const baseline = await evaluate('({width:innerWidth,dpr:devicePixelRatio})', page);
  const zoomIn = await nativeAction({action:'ZoomIn'});
  const result = {nativeWindowBinding:body.sameNativeWindow,captionHeightDip:HEIGHT,
    limits:{windows10And11Interactive:false,windows11Snap:false,actualSystemDpiValidated:false}};
  if (zoomIn.status === 'unavailable') {
    result.businessZoom = {status:'unavailable',nativeInput:zoomIn};
  } else {
    try {
      await until(async () => {
        const value = await evaluate('({width:innerWidth,dpr:devicePixelRatio})', page);
        return value.dpr > baseline.dpr + .05 && value.width < baseline.width;
      }, 7000);
      result.businessZoom = {status:'passed',business:await evaluate('({width:innerWidth,dpr:devicePixelRatio})', page),
        caption:await probe.read(),nativeInput:zoomIn};
    } finally { await nativeAction({action:'ZoomReset'}); }
    await until(async () => Math.abs(await evaluate('devicePixelRatio', page)-baseline.dpr)<.01, 7000);
    await probe.checkBody(page);
  }
  await cdp.send('Page.reload', {}, page.sessionId);
  await until(async () => {
    try {return await evaluate("document.readyState==='complete' && typeof state==='object' && state.graphInited && !state.graphLoading", page);}
    catch {return false;}
  },20000);
  result.reload = {sameShellTarget:true,caption:await probe.read()};
  result.nativeControls = await nativeAction({action:'CaptionProbe',
    dragXDip:Math.round((body.caption.menu.x+body.caption.menu.width+body.caption.overlay.area.width)/2),
    menuXDip:Math.round(body.caption.menu.x+body.caption.menu.width/2)});
  if (result.nativeControls.status === 'passed') {
    const value=result.nativeControls.interaction;
    for (const key of ['PointerInputInjected','MinimizeButtonClicked','MinimizedObserved','MinimizeRestoredObserved',
      'MaximizeButtonClicked','MaximizedObserved','RestoreButtonClicked','RestoredObserved']) {
      assert.equal(value?.[key],true,'TITLEBAR_NATIVE_BUTTON_SEQUENCE');
    }
    assert.equal(value.CaptionHit,2,'TITLEBAR_NATIVE_DRAG_HIT');
    assert.equal(value.MenuHit,1,'TITLEBAR_NATIVE_MENU_HIT');
  }
  // Maximize/restore and drag may resize/reposition the root; fixed branding is
  // still checked against the original native shell document and dimensions.
  result.afterNativeControls = await probe.read();
  await probe.checkBody(page);
  return result;
}

module.exports = {HEIGHT,titlebarTarget,assertCaption,assertCaptionBody,createInstalledTitlebarProbe,
  runInstalledTitlebarJourney,windowId};
