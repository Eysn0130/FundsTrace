'use strict';
// CI-only inspection of the installed local caption. Nothing here is shipped.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { installedLocalTarget } = require('./installed-startup-journey.cjs');
const HEIGHT = 32;
const titlebarTarget = value => installedLocalTarget(value, 'titlebar');
const near = (actual, expected, tolerance = 1) => Number.isFinite(actual) && Math.abs(actual - expected) <= tolerance;
const KEY = '__fundsTraceCaptionObservation';
const PROTOCOL_METHODS = new Set([
  'Target.getTargets','Target.getTargetInfo','Target.setAutoAttach',
  'Runtime.enable','Runtime.evaluate','Runtime.runIfWaitingForDebugger',
  'Page.enable','Page.navigate','Page.reload','Page.close','Page.bringToFront','Page.captureScreenshot',
  'Debugger.enable','Debugger.disable','Debugger.resume','Debugger.setInstrumentationBreakpoint',
  'Emulation.clearDeviceMetricsOverride','Emulation.setDeviceMetricsOverride','Emulation.setEmulatedMedia',
  'Input.dispatchMouseEvent','Input.dispatchKeyEvent','Input.insertText',
  'DOM.getDocument','DOM.querySelector','DOM.setFileInputFiles',
]);
const FAILURE_KINDS = new Set(['CDP_CLOSED','CDP_COMMAND','CDP_TIMEOUT','CDP_SEND','CDP_CONNECT','CDP_INVALID_JSON',
  'CDP_LISTENER_FAILED','TIMEOUT','SPAWN_FAILED','ERR_ASSERTION','ENOENT','EACCES','ETIMEDOUT','ECONNRESET','ECONNREFUSED']);
const safeProtocolMethod = method => PROTOCOL_METHODS.has(method) ? method : null;
function protocolError(kind,method) {
  const error=new Error(FAILURE_KINDS.has(kind) ? kind : 'CDP_COMMAND');
  error.protocolMethod=safeProtocolMethod(method);
  return error;
}
function safeInstalledFailure(error) {
  const kind=FAILURE_KINDS.has(error?.code) ? error.code : FAILURE_KINDS.has(error?.message) ? error.message : 'UNCLASSIFIED';
  return {kind,protocolMethod:safeProtocolMethod(error?.protocolMethod)};
}
function createMainErrorMarkerProbe() {
  const flags={objectDestroyed:false,javascriptErrorInMainProcess:false,uncaughtException:false};
  const patterns=[['objectDestroyed','object has been destroyed'],
    ['javascriptErrorInMainProcess','javascript error occurred in main process'],
    ['javascriptErrorInMainProcess','javascript error occurred in the main process'],['uncaughtException','uncaught exception']]
    .map(([key,text])=>({key,text,matched:0}));
  return {
    consume(chunk) {
      // Retain prefix lengths and booleans only, never an stderr line or tail.
      for(const character of String(chunk)) for(const item of patterns) {
        if(flags[item.key]) continue;
        const value=character.toLowerCase();
        item.matched=value===item.text[item.matched] ? item.matched+1 : value===item.text[0] ? 1 : 0;
        if(item.matched===item.text.length) {flags[item.key]=true;item.matched=0;}
      }
    },
    snapshot:()=>({...flags}),
  };
}

const READ_CAPTION = `(() => {
  const bar = document.querySelector('.titlebar'), content = document.querySelector('.titlebar-content');
  const name = document.querySelector('.titlebar-name'), icon = document.querySelector('.titlebar-icon');
  const menu = document.querySelector('#titlebar-menu'), overlay = navigator.windowControlsOverlay;
  const rect = element => { if (!element) return null; const r = element.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; };
  let area = null, areaReadFailed = false;
  try { const r = overlay?.getTitlebarAreaRect?.(); if (r) area = {x:r.x,y:r.y,width:r.width,height:r.height}; }
  catch { areaReadFailed = true; }
  const style = bar ? getComputedStyle(bar) : null, nameStyle = name ? getComputedStyle(name) : null;
  let imageDecode = 'missing';
  if (icon) {
    imageDecode = !icon.complete ? 'pending' : icon.naturalWidth > 0 ? 'natural-size-ready' : 'failed';
    if (icon.complete && icon.naturalWidth > 0 && typeof icon.decode === 'function') {
      let state = window.__fundsTraceCaptionDecode;
      if (!state || state.icon !== icon) {
        state = {icon,status:'pending'}; window.__fundsTraceCaptionDecode = state;
        try { Promise.resolve(icon.decode()).then(()=>{state.status='decoded';},()=>{state.status='failed';}); }
        catch { state.status='failed'; }
      }
      imageDecode = state.status;
    }
  }
  const bridge = window.FundsTraceTitlebar;
  return {time:performance.now(),timeOrigin:performance.timeOrigin,viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio},
    document:{readyState:document.readyState,visibilityState:document.visibilityState,secureContext:globalThis.isSecureContext===true,
      standardsMode:document.compatMode==='CSS1Compat',fontStatus:document.fonts?.status || 'unavailable'},
    dom:{bar:!!bar,content:!!content,name:!!name,icon:!!icon,menu:!!menu},
    bridge:{type:typeof bridge,openMenu:typeof bridge?.openMenu,onMenuEnabled:typeof bridge?.onMenuEnabled},
    overlay:{supported:!!overlay,visible:overlay?.visible===true,rectMethod:typeof overlay?.getTitlebarAreaRect,areaReadFailed,area},
    bar:rect(bar),content:rect(content),name:rect(name),icon:rect(icon),menu:rect(menu),
    text:name?.textContent.trim()==='循迹 FundsTrace'?'循迹 FundsTrace':'unexpected',opacity:style ? Number(style.opacity) : null,visibility:style?.visibility || 'unavailable',
    fontSize:nameStyle ? Number.parseFloat(nameStyle.fontSize) : null,fontWeight:nameStyle?.fontWeight || 'unavailable',color:nameStyle?.color || 'unavailable',
    drag:style?.getPropertyValue('-webkit-app-region') || 'unavailable',menuDrag:menu ? getComputedStyle(menu).getPropertyValue('-webkit-app-region') : 'unavailable',
    menuDisabled:menu ? menu.disabled : null,iconLoaded:!!icon && icon.complete && icon.naturalWidth > 0,
    image:{complete:icon?.complete===true,naturalWidth:icon?.naturalWidth || 0,naturalHeight:icon?.naturalHeight || 0,decode:imageDecode},
    stylesheetLoaded:!!document.querySelector('link[rel=stylesheet]')?.sheet};
})()`;

const number = value => Number.isFinite(value) && Math.abs(value) <= Number.MAX_SAFE_INTEGER ? value : null;
const oneOf = (value, allowed) => allowed.includes(value) ? value : 'other';
function safeCaptionProjection(value) {
  if (!value || typeof value !== 'object') return null;
  const box = value => value && typeof value === 'object'
    ? Object.fromEntries(['x','y','width','height'].map(key=>[key,Number.isFinite(value[key]) && Math.abs(value[key])<=32768 ? value[key] : null])) : null;
  const type = value => oneOf(value,['undefined','object','function','boolean','string','number','symbol','bigint']);
  return {time:number(value.time),timeOrigin:number(value.timeOrigin),
    document:{readyState:oneOf(value.document?.readyState,['loading','interactive','complete']),
      visibilityState:oneOf(value.document?.visibilityState,['visible','hidden']),secureContext:value.document?.secureContext===true,
      standardsMode:value.document?.standardsMode===true,fontStatus:oneOf(value.document?.fontStatus,['loading','loaded','unavailable'])},
    dom:Object.fromEntries(['bar','content','name','icon','menu'].map(key=>[key,value.dom?.[key]===true])),
    bridge:{type:type(value.bridge?.type),openMenu:type(value.bridge?.openMenu),onMenuEnabled:type(value.bridge?.onMenuEnabled)},
    viewport:{width:number(value.viewport?.width),height:number(value.viewport?.height),dpr:number(value.viewport?.dpr)},
    overlay:{supported:value.overlay?.supported===true,visible:value.overlay?.visible===true,rectMethod:type(value.overlay?.rectMethod),
      areaReadFailed:value.overlay?.areaReadFailed===true,area:box(value.overlay?.area)},
    bar:box(value.bar),content:box(value.content),name:box(value.name),icon:box(value.icon),menu:box(value.menu),
    brandTextMatches:value.text==='循迹 FundsTrace' || value.brandTextMatches===true,opacity:number(value.opacity),visibility:oneOf(value.visibility,['visible','hidden','collapse','unavailable']),
    fontSize:number(value.fontSize),fontWeight:/^(?:[1-9]00|normal|bold)$/.test(value.fontWeight) ? value.fontWeight : 'other',
    color:/^rgba?\([\d,. %]+\)$/.test(value.color) && value.color.length<=64 ? value.color : 'other',
    drag:oneOf(value.drag,['drag','no-drag','none','unavailable']),menuDrag:oneOf(value.menuDrag,['drag','no-drag','none','unavailable']),
    menuDisabled:typeof value.menuDisabled==='boolean' ? value.menuDisabled : null,
    iconLoaded:value.iconLoaded===true,stylesheetLoaded:value.stylesheetLoaded===true,
    image:{complete:value.image?.complete===true,naturalWidth:number(value.image?.naturalWidth),naturalHeight:number(value.image?.naturalHeight),
      decode:oneOf(value.image?.decode,['missing','pending','natural-size-ready','decoded','failed'])}};
}

function readinessFailure(value) {
  if (!value) return 'TITLEBAR_DOM_READ_UNAVAILABLE';
  if (!['bar','content','name','icon','menu'].every(key=>value.dom?.[key])) return 'TITLEBAR_DOM_INCOMPLETE';
  if (!value.stylesheetLoaded) return 'TITLEBAR_STYLESHEET_NOT_LOADED';
  if (!value.iconLoaded) return value.image?.complete || value.image?.decode==='failed'
    ? 'TITLEBAR_ICON_DECODE_FAILED' : 'TITLEBAR_ICON_NOT_LOADED';
  if (!value.overlay?.supported) return 'TITLEBAR_OVERLAY_API_UNAVAILABLE';
  if (!value.overlay.visible) return 'TITLEBAR_OVERLAY_NOT_VISIBLE';
  if (!value.overlay.area || value.overlay.areaReadFailed) return 'TITLEBAR_OVERLAY_RECT_UNAVAILABLE';
  return 'TITLEBAR_READINESS_TIMEOUT';
}

function safeTitlebarFailure(value) {
  const stage = /^TITLEBAR_[A-Z0-9_]{1,80}$/.test(value?.stage || '') ? value.stage : 'TITLEBAR_READINESS_TIMEOUT';
  const screenshot = value?.screenshot;
  return {stage,
    operation:oneOf(value?.operation,['readiness','initial-projection','initial-validation','observation-installation']),
    method:oneOf(value?.method,['Runtime.evaluate','none']),
    errorCategory:oneOf(value?.errorCategory,['bound-timeout','protocol-closed','protocol-command','protocol-timeout','protocol-send',
      'protocol-connect','assertion','javascript-TypeError','javascript-ReferenceError','javascript-SyntaxError','javascript-RangeError','unclassified']),
    attempts:Number.isInteger(value?.attempts) && value.attempts>=0 && value.attempts<=10000 ? value.attempts : null,
    readFailures:Number.isInteger(value?.readFailures) && value.readFailures>=0 && value.readFailures<=10000 ? value.readFailures : null,
    observation:safeCaptionProjection(value?.observation),
    screenshot:{captured:screenshot?.captured===true,source:'shell-renderer-caption-only',nativeControlsIncluded:false,
      ...(screenshot?.captured===true ? {file:'native-installed-app.startup-caption-failure.png',width:number(screenshot.width),height:number(screenshot.height)} : {})}};
}

function errorCategory(error) {
  const codes={TIMEOUT:'bound-timeout',CDP_CLOSED:'protocol-closed',CDP_COMMAND:'protocol-command',CDP_TIMEOUT:'protocol-timeout',
    CDP_SEND:'protocol-send',CDP_CONNECT:'protocol-connect'};
  if(Object.hasOwn(codes,error?.message)) return codes[error.message];
  if(error?.code==='ERR_ASSERTION') return 'assertion';
  return ['TypeError','ReferenceError','SyntaxError','RangeError'].includes(error?.name) ? `javascript-${error.name}` : 'unclassified';
}

async function captureCaptionFailure(cdp,target,value,screenshotPath) {
  const width=Math.min(4096,Math.floor(value?.viewport?.width || 0));
  const height=Math.min(HEIGHT,Math.floor(value?.viewport?.height || 0));
  if (!screenshotPath || !path.isAbsolute(screenshotPath) || !value?.dom?.bar || width<1 || height<1) return {captured:false};
  try {
    const result=await cdp.send('Page.captureScreenshot',{format:'png',fromSurface:true,captureBeyondViewport:false,
      clip:{x:0,y:0,width,height,scale:1}},target.sessionId,3000);
    if(typeof result.data!=='string' || result.data.length>16*1024*1024) return {captured:false};
    const bytes=Buffer.from(result.data,'base64');
    if(!bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) return {captured:false};
    await fs.mkdir(path.dirname(screenshotPath),{recursive:true});
    await fs.writeFile(screenshotPath,bytes,{flag:'wx'});
    return {captured:true,width,height};
  } catch {return {captured:false};}
}

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

async function createInstalledTitlebarProbe({cdp, target, evaluate, until, failureScreenshotPath}) {
  let latest=null,attempts=0,readFailures=0,lastReadError;
  const fail=async(stage,operation,method,cause)=>{
    const screenshot=await captureCaptionFailure(cdp,target,latest,failureScreenshotPath);
    const error=new Error(stage);
    const causeDetail=safeInstalledFailure(cause);
    if(causeDetail.kind!=='UNCLASSIFIED') error.code=causeDetail.kind;
    error.protocolMethod=causeDetail.protocolMethod;
    error.titlebarFailure=safeTitlebarFailure({stage,operation,method,errorCategory:errorCategory(cause),
      attempts,readFailures,observation:latest,screenshot});
    throw error;
  };
  try {
    await until(async () => {
      attempts++;
      try {
        latest=await evaluate(READ_CAPTION,target);
        return latest?.iconLoaded && latest.stylesheetLoaded && latest.overlay?.visible && !!latest.overlay.area;
      } catch(error) {readFailures++;lastReadError=error;return false;}
    },15000);
  } catch(error) {
    await fail(readinessFailure(latest),'readiness','Runtime.evaluate',latest ? error : lastReadError || error);
  }
  let initial;
  try {latest=await evaluate(READ_CAPTION,target);}
  catch(error) {await fail('TITLEBAR_INITIAL_PROJECTION_FAILED','initial-projection','Runtime.evaluate',error);}
  try {initial=assertCaption(latest);}
  catch(error) {await fail(/^TITLEBAR_[A-Z0-9_]{1,80}$/.test(error?.message || '') ? error.message : 'TITLEBAR_INITIAL_VALIDATION_FAILED',
    'initial-validation','none',error);}
  try {await evaluate(`(() => {
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
  })()`, target);}
  catch(error) {await fail('TITLEBAR_OBSERVATION_INSTALL_FAILED','observation-installation','Runtime.evaluate',error);}
  let stopped = false;
  return {
    target, initial,
    async read({stable = true} = {}) {
      const value = assertCaption(await evaluate(READ_CAPTION, target), stable ? initial : undefined);
      return value;
    },
    async checkBody(bodyTarget, native) {
      assert.notEqual(bodyTarget.targetId,target.targetId,'TITLEBAR_BODY_MUST_BE_SEPARATE_TARGET');
      // Explicitly clear prior CSS/DPR emulation before comparing actual Views.
      // The optional Win32 client measurement adds native geometry evidence;
      // no unsupported browser-to-HWND identity claim is inferred from it.
      for(const item of [target,bodyTarget]) await cdp.send('Emulation.clearDeviceMetricsOverride',{},item.sessionId);
      await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))',bodyTarget);
      const caption = await this.read();
      const body = await evaluate('({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})', bodyTarget);
      assertCaptionBody(caption, body, native);
      return {caption,body,sameCaptionDocument:caption.timeOrigin===initial.timeOrigin,
        bodyInsetDip:HEIGHT,layoutMatches:true,nativeClientMatched:!!native,viewportEmulationCleared:true,distinctRendererTargets:true};
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
  const result = {sameCaptionDocument:body.sameCaptionDocument,bodyInsetDip:body.bodyInsetDip,layoutMatches:body.layoutMatches,captionHeightDip:HEIGHT,
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
  runInstalledTitlebarJourney,READ_CAPTION,safeCaptionProjection,safeTitlebarFailure,readinessFailure,
  safeProtocolMethod,protocolError,safeInstalledFailure,createMainErrorMarkerProbe};
