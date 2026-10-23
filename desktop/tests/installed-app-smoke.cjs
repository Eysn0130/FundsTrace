'use strict';
const smokeStartedAt = Date.now();
// CI-only installed EXE smoke. Never import Main, inspect request headers, or log CDP payloads.
// Usage: node installed-app-smoke.cjs <absolute-install-root> --report <absolute-json> [--screenshot <absolute-png>] [--startup-close | --presentation-baseline <absolute-config>]
const assert = require('node:assert/strict');
const { spawn, execFile } = require('node:child_process');
const { promisify } = require('node:util');
const fs = require('node:fs/promises');
const net = require('node:net');
const path = require('node:path');
const os = require('node:os');
const { performance } = require('node:perf_hooks');
const { pathToFileURL } = require('node:url');
const { startupTarget, createInstalledStartupProbe, assertNativeClientGeometry, assertNativeWindowBounds } = require('./installed-startup-journey.cjs');
const { titlebarTarget, createInstalledTitlebarProbe, runInstalledTitlebarJourney, safeTitlebarFailure,
  safeProtocolMethod, protocolError, safeInstalledFailure, createMainErrorMarkerProbe } = require('./installed-titlebar-journey.cjs');
const { runInstalledGraphJourney, safeJourneyFailure } = require('./installed-graph-journey.cjs');
const { createUpdateFixture, runInstalledUpdateJourney, safeUpdateDialogFailure } = require('./installed-update-journey.cjs');
const { runInstalledProductionUpdateJourney } = require('./installed-production-update-journey.cjs');
const { safeProductionTransportDiagnostic } = require('./installed-update-transport-diagnostic.cjs');
const { safeInstalledElectronMetadataProbe } = require('./installed-electron-metadata-probe.cjs');
const { safeUpdateOwnerSnapshot } = require('./installed-update-owner-snapshot.cjs');
const { runInstalledPresentationBaseline } = require('./installed-presentation-baseline.cjs');
const { FAILURE_CODES } = require('../updates.cjs');

const SENTINEL = 'FundsTrace synthetic installer lifecycle v1\n';
const SEED_CASE = 'SYN prior installer lifecycle case';
const CREATED_CASE = 'SYN current installer lifecycle case';
const COMPATIBILITY_CASES = ['SYN prior compatibility populated case', 'SYN prior compatibility isolated case',
  'SYN prior compatibility populated case(导入1)', 'SYN prior compatibility populated case(导入2)'];
const VIEWPORTS = [[960, 500], [960, 640], [1280, 640]], DEVICE_SCALES = [1, 1.5, 2];
const report = { schemaVersion: 1, status: 'running', checks: {}, timings: {}, failedCheck: null,
  limits: { windowsServerAutomationOnly: true, windows10And11Interactive: false,
    dpi: false, importRecovery: false, screenshotIsRendererSurface: true,
    nativeGraphExportSaveDialog: false, taskbarAppearance: false,
    simulatedCssViewportOnly: true, actualSystemDpiValidated: false,
    offlineEnforcement: 'not-measured-by-this-helper; see standard-user compatibility report',
    simulatedCssViewports: VIEWPORTS, simulatedDeviceScaleFactors: DEVICE_SCALES } };
const timings = report.timings, owned = [];
let stage = 'ciSyntheticUser', reportPath, reportOwned = false, cdp, page, analysisPage, titlebarPage, analysisTitlebarPage,
  titlebarProbe, updateFixture, productionUpdate = false, productionTransportConfig, productionOwner, presentationPreferences;
let releaseStartupInspection = async () => {};

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function bounded(promise, ms) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error('TIMEOUT')), ms);
  })]); } finally { clearTimeout(timer); }
}
async function until(check, ms) {
  const deadline = performance.now() + ms;
  do { const result = await check(); if (result) return result; await delay(100); }
  while (performance.now() < deadline);
  throw new Error('TIMEOUT');
}
async function check(name, action) {
  stage = name;
  const start = performance.now();
  const result = await action();
  timings[name] = Math.round(performance.now() - start);
  report.checks[name] = true;
  return result;
}
async function windowIcons(installRoot, processId, minimumWindows, taskbarScreenshot) {
  let stdout;
  try {
    ({ stdout } = await promisify(execFile)('pwsh.exe', ['-NoLogo', '-NoProfile', '-NonInteractive', '-File',
      path.resolve(__dirname, '../../scripts/test-windows-brand-icon.ps1'),
      '-InstallRoot', installRoot, '-ExpectedIco', path.resolve(__dirname, '../assets/icon.ico'),
      '-ProcessId', String(processId), ...(taskbarScreenshot ? ['-TaskbarScreenshot', taskbarScreenshot] : [])],
    { timeout: 60000, maxBuffer: 256 * 1024 }));
  } catch (error) {
    // Retain only fixed native stages and icon fingerprints. Compiler/COM
    // messages and process arguments may contain local paths or headers.
    try {
      const raw = String(error.stdout || '');
      if (raw.length > 32768) throw new Error('DIAGNOSTIC_TOO_LARGE');
      const failed = JSON.parse(raw.replace(/^\uFEFF/, ''));
      if (failed.schemaVersion === 1 && failed.status === 'failed') {
        const sources = ['WM_GETICON-small2', 'WM_GETICON-big', 'WM_GETICON-small', 'class-small', 'class-big'];
        report.nativeWindowIconFailure = {
          failedCheck: ['ciPlatform', 'inputBinding', 'nativeIconReader', 'installedProcessBinding', 'visibleWindowIcons'].includes(failed.failedCheck) ? failed.failedCheck : null,
          visibleWindowCount: Number.isInteger(failed.visibleWindowCount) && failed.visibleWindowCount >= 0 && failed.visibleWindowCount <= 64 ? failed.visibleWindowCount : null,
          iconComparisons: Array.isArray(failed.iconComparisons) ? failed.iconComparisons.slice(0, 8).filter(icon =>
            sources.includes(icon?.Source) && typeof icon.Matched === 'boolean'
            && Number.isInteger(icon.Width) && icon.Width > 0 && icon.Width <= 1024
            && Number.isInteger(icon.Height) && icon.Height > 0 && icon.Height <= 1024
            && typeof icon.ActualSha256 === 'string' && typeof icon.ExpectedSha256 === 'string'
            && /^[a-f0-9]{64}$/.test(icon.ActualSha256) && /^[a-f0-9]{64}$/.test(icon.ExpectedSha256)
          ).map(({ Source, Width, Height, Matched, ActualSha256, ExpectedSha256 }) =>
            ({ Source, Width, Height, Matched, ActualSha256, ExpectedSha256 })) : [],
        };
      }
    } catch {}
    throw new Error('NATIVE_WINDOW_ICON_CHECK_FAILED');
  }
  const result = JSON.parse(stdout.replace(/^\uFEFF/, ''));
  assert.equal(result.status, 'passed');
  assert.ok(result.visibleWindowCount >= minimumWindows);
  return result;
}
async function nativeStartupWindow(installRoot, processId, { action = 'Inspect', widthDip, heightDip, screenshotPath, dragXDip, menuXDip } = {}) {
  const args = ['-NoLogo', '-NoProfile', '-NonInteractive', '-File',
    path.resolve(__dirname, '../../scripts/test-windows-startup-window.ps1'),
    '-InstallRoot', installRoot, '-ProcessId', String(processId), '-Action', action];
  if (widthDip !== undefined) args.push('-WidthDip', String(widthDip), '-HeightDip', String(heightDip));
  if (screenshotPath) args.push('-ScreenshotPath', screenshotPath);
  if (dragXDip !== undefined) args.push('-DragXDip', String(dragXDip), '-MenuXDip', String(menuXDip));
  let stdout;
  try { ({ stdout } = await promisify(execFile)('pwsh.exe', args, { timeout:20000, maxBuffer:128 * 1024, windowsHide:true })); }
  catch (error) {
    if (![1, 2].includes(error.code) || typeof error.stdout !== 'string') throw new Error('STARTUP_NATIVE_WINDOW_FAILED');
    stdout = error.stdout;
  }
  let result;
  try { result = JSON.parse(String(stdout).replace(/^\uFEFF/, '')); }
  catch { throw new Error('STARTUP_NATIVE_REPORT_INVALID'); }
  assert.equal(result.schemaVersion, 1);
  if (result.status === 'failed') {
    const stages = ['ciPlatform','syntheticUserBinding','inputBinding','nativeWindowReader','installedProcessBinding',
      'physicalDpiContext','uniqueVisibleWindow','nativeWindowInspect','nativeWindowResize','screenshotOutputBinding','nativeWindowCapture','nativeCaptionInteraction'];
    const actions=['Inspect','Resize','UserResize','Capture','CaptionProbe','CaptionClose','CaptionCloseForeground','ZoomIn','ZoomReset'];
    const codes=['OWNED_WINDOW_COUNT','FOREGROUND_BINDING','HTCLOSE','STATE_TIMEOUT','PROCESS_BINDING','NATIVE_GEOMETRY','CAPTION_POINT',
      'CAPTION_HIT','CAPTION_STATE','CAPTION_GEOMETRY','NATIVE_CLOSE_FAILED','NATIVE_CAPTION_FAILED'];
    report.startupNativeFailure = { action:actions.includes(action) ? action : 'unknown',
      failedCheck:stages.includes(result.failedCheck) ? result.failedCheck : 'unknown', processBound:result.processBound === true,
      failureCode:codes.includes(result.failureCode) ? result.failureCode : action==='CaptionCloseForeground' ? 'NATIVE_CLOSE_FAILED' : 'NATIVE_WINDOW_FAILED' };
  }
  assert.equal(result.processBound, true);
  assert.ok(result.status === 'passed' || ['Capture','CaptionProbe','CaptionClose','CaptionCloseForeground','ZoomIn','ZoomReset'].includes(action) && result.status === 'unavailable');
  return result;
}
async function nativeStartupBounds(config, primary, target) {
  const observe = options => nativeStartupWindow(config.installRoot, primary.child.pid, options);
  const result = report.startup.nativeWindows = { step:'initialInspect', actualSystemDpiValidated:false,
    scope:'actual-installed-HWND-on-Windows-Server; no-system-display-setting-changes' };
  const initial = await observe({});
  result.initial = initial;
  const scale = initial.scaleFactor, area = initial.workArea;
  const defaultWidth = Math.round(Math.min(1440, area.width / scale - 64, (area.height / scale - 64) * 16 / 9));
  const expected = { width:Math.min(Math.max(1280, defaultWidth), area.width / scale),
    height:Math.min(Math.max(720, Math.round(defaultWidth * 9 / 16)), area.height / scale) };
  const min = { width:Math.min(1280, area.width / scale), height:Math.min(720, area.height / scale) };
  result.expectedInitialDip={...expected,minWidth:min.width,minHeight:min.height};
  assertNativeWindowBounds(initial,result.expectedInitialDip);
  const client = async native => {
    await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))', target);
    const value = await evaluate(`({width:innerWidth,height:innerHeight,dpr:devicePixelRatio,
      outerWidth,outerHeight,screenX,screenY,
      scale:Number(getComputedStyle(document.querySelector('#startup-surface')).getPropertyValue('--brand-scale'))})`, target);
    (result.clientMeasurements ||= []).push(value);
    result.clientChromeInsetDip = assertNativeClientGeometry(native, value, result.clientChromeInsetDip);
    await titlebarProbe.checkBody(target, native);
    return value;
  };
  result.step = 'defaultCapture';
  if (config.screenshot) result.initialCapture = await observe({ action:'Capture', screenshotPath:`${config.screenshot.slice(0, -4)}.startup-native-default.png` });
  result.step = 'initialClient';
  result.initialClient = await client(initial);
  result.step = 'minimumResize';
  const minimum = await observe({ action:'Resize', widthDip:Math.round(min.width), heightDip:Math.round(min.height) });
  result.minimum = minimum;
  assertNativeWindowBounds(minimum,{...min,minWidth:min.width,minHeight:min.height});
  result.minimumClient = await client(minimum);
  result.step = 'minimumCapture';
  if (config.screenshot) result.minimumCapture = await observe({ action:'Capture', screenshotPath:`${config.screenshot.slice(0, -4)}.startup-native-minimum.png` });
  result.step = 'restoreDefault';
  result.restored = await observe({ action:'Resize', widthDip:Math.round(initial.electronWindow.width / scale), heightDip:Math.round(initial.electronWindow.height / scale) });
  assertNativeWindowBounds(result.restored,result.expectedInitialDip);
  await client(result.restored);
  result.step = 'completed';
  return result;
}
function readWorkspaceTools() {
  const rect=node=>{if(!node)return null;const r=node.getBoundingClientRect();return {
    left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};
  const visible=node=>!!node&&node.getClientRects().length>0&&getComputedStyle(node).visibility!=='hidden';
  const toolbar=document.querySelector('#editorToolbar'),slot=toolbar?.parentElement;
  const header=document.querySelector('#editorToolbar .ribbon-tabbar');
  const panels=[...document.querySelectorAll('#editorToolbar .ribbon-panel:not([hidden])')].filter(visible);
  const compact=document.querySelector('#ribbonCompact'),compactVisible=visible(compact);
  const bars=[header,...panels,...(compactVisible?[compact]:[])].filter(visible);
  const slotRect=rect(slot),toolbarRect=rect(toolbar),viewport={left:0,top:0,right:innerWidth,bottom:innerHeight};
  const clipped=(node,bar)=>{
    const r=rect(node),bounds=[viewport,slotRect,toolbarRect,rect(bar)].filter(Boolean);
    for(let parent=node.parentElement;parent&&parent!==slot;parent=parent.parentElement) {
      const style=getComputedStyle(parent),p=rect(parent);
      bounds.push({left:/^(hidden|clip|auto|scroll)$/.test(style.overflowX)?p.left:-Infinity,
        right:/^(hidden|clip|auto|scroll)$/.test(style.overflowX)?p.right:Infinity,
        top:/^(hidden|clip|auto|scroll)$/.test(style.overflowY)?p.top:-Infinity,
        bottom:/^(hidden|clip|auto|scroll)$/.test(style.overflowY)?p.bottom:Infinity});
    }
    return bounds.some(b=>r.left<b.left-1||r.right>b.right+1||r.top<b.top-1||r.bottom>b.bottom+1);
  };
  const controls=bar=>[...bar.querySelectorAll('button,input,select,a')].filter(visible);
  const action=panels[0]?.querySelector('.ribbon-action'),table=document.querySelector('#flowTableScroll');
  const required=['caseSelect','canvasSel','btnCanvasSave','btnUndo','btnRedo','btnWorkspaceView',
    'ribbonTab-common','ribbonTab-trace','ribbonTab-style','ribbonTab-view'];
  const style=toolbar?getComputedStyle(toolbar):null;
  return {width:innerWidth,height:innerHeight,dpr:devicePixelRatio,slot:slotRect,slotClientWidth:slot?.clientWidth||0,toolbar:toolbarRect,
    scale:style?Number(style.getPropertyValue('--toolbar-fit-scale')):null,
    logicalWidth:toolbar?.clientWidth||0,cssHeight:style?parseFloat(style.height):null,
    collapsed:toolbar?.classList.contains('is-collapsed')===true,compactVisible,panels:panels.map(node=>node.id),
    requiredControlsVisible:required.every(id=>{const node=document.getElementById(id);return visible(node)&&header?.contains(node);})
      &&visible(header?.querySelector('[data-ribbon-command="inspectSelection"]'))&&visible(header?.querySelector('[data-ribbon-collapse]')),
    bars:bars.map(bar=>({id:bar===header?'ribbon-tabbar':bar.id,client:bar.clientWidth,scroll:bar.scrollWidth,rect:rect(bar)})),
    clippedControls:bars.reduce((count,bar)=>count+controls(bar).filter(node=>clipped(node,bar)).length,0),
    action:action?{direction:getComputedStyle(action).flexDirection,font:parseFloat(getComputedStyle(action).fontSize),
      icon:parseFloat(getComputedStyle(action.querySelector('.ui-icon')).width)}:null,
    table:table?{columns:document.querySelectorAll('#flowTable thead th').length,overflow:getComputedStyle(table).overflowX,focusable:table.tabIndex===0}:null,
    duplicateScrollControls:[...document.querySelectorAll('.flow-horizontal-scroll')].filter(visible).length};
}
function assertWorkspaceTools(value,{tab,collapsed}) {
  assert.ok(value.slot&&value.toolbar&&value.slot.width>0&&value.toolbar.width>0,'WORKSPACE_TOOLBAR_SLOT_MISSING');
  const r=value.toolbar,s=value.slot;
  assert.ok(r.left>=s.left-1&&r.right<=s.right+1&&r.top>=s.top-1&&r.bottom<=s.bottom+1
    &&Math.abs(r.left-s.left)<=1&&Math.abs(r.right-s.right)<=1,'WORKSPACE_TOOLBAR_SLOT_CLIPPED');
  assert.ok(value.scale>0&&value.scale<=1&&Math.abs(r.width-value.logicalWidth*value.scale)<=1,'WORKSPACE_TOOLBAR_SCALE');
  assert.equal(value.collapsed,collapsed);assert.equal(value.compactVisible,collapsed);
  assert.deepEqual(value.panels,collapsed?[]:[`ribbonPanel-${tab}`]);
  assert.deepEqual(value.bars.map(bar=>bar.id),['ribbon-tabbar',collapsed?'ribbonCompact':`ribbonPanel-${tab}`]);
  assert.ok(value.bars.every(bar=>bar.scroll<=bar.client+1),'WORKSPACE_TOOLBAR_OVERFLOW');
  assert.equal(value.requiredControlsVisible,true,'WORKSPACE_TOOLBAR_CONTROLS_MISSING');
  assert.equal(value.clippedControls,0,'WORKSPACE_TOOLBAR_CLIPPED');
  if(value.action){assert.equal(value.action.direction,'column');assert.equal(value.action.font,12);assert.ok(Math.abs(value.action.icon-27)<.05);}
  assert.deepEqual(value.table,{columns:7,overflow:'auto',focusable:true});
  assert.equal(value.duplicateScrollControls,0);
}
function readImportEntry() {
  const label=document.querySelector('dialog[open] label.file-drop'),input=label?.querySelector('input[type="file"]');
  return {associated:!!input&&!!input.id&&label.htmlFor===input.id&&label.control===input
    &&document.getElementById(input.id)===input,type:input?.type||null,accept:input?.accept||null,enabled:input?.disabled===false};
}
function argumentsOf() {
  const input = process.argv.slice(2), startupClose = input.includes('--startup-close');
  assert.ok(input.filter(value => value === '--startup-close').length <= 1);
  const args = input.filter(value => value !== '--startup-close');
  assert.ok(args.length === 3 || args.length === 5 || args.length === 7);
  const installRoot = args.shift(), values = {};
  while (args.length) {
    const key = args.shift(), value = args.shift();
    assert.ok(['--report', '--screenshot', '--update-fixture', '--production-update', '--presentation-baseline'].includes(key) && !values[key] && value && path.isAbsolute(value));
    values[key] = path.resolve(value);
  }
  assert.ok(installRoot && path.isAbsolute(installRoot) && values['--report']);
  assert.equal(path.extname(values['--report']).toLowerCase(), '.json');
  if (values['--screenshot']) assert.equal(path.extname(values['--screenshot']).toLowerCase(), '.png');
  assert.ok([startupClose, values['--update-fixture'], values['--production-update'], values['--presentation-baseline']].filter(Boolean).length <= 1);
  return { installRoot: path.resolve(installRoot), reportPath: values['--report'], screenshot: values['--screenshot'],
    updateFixture: values['--update-fixture'], productionUpdate: values['--production-update'],
    presentationBaseline:values['--presentation-baseline'], startupClose };
}
function within(root, value) {
  const relative = path.relative(root, value);
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}
function businessOrigin(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' && url.hostname === '127.0.0.1' && url.port
      && !url.username && !url.password && url.pathname === '/' ? url.origin : null;
  } catch { return null; }
}
function boot(executable, installRoot, env, debug) {
  const args = debug ? ['--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1'] : [];
  if (updateFixture) args.push('--no-proxy-server', '--host-resolver-rules=MAP analytix.top 127.0.0.1, MAP download.analytix.top 127.0.0.1');
  const child = spawn(executable, args,
    { cwd: installRoot, env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: false });
  const instance = { child, done: false, failed: false, debugUrl: null, mainErrors:createMainErrorMarkerProbe() };
  owned.push(instance);
  instance.exited = new Promise(resolve => {
    child.once('exit', (code, signal) => { instance.done = true; resolve({ code, signal }); });
    child.once('error', () => { instance.failed = true; instance.done = true; resolve({ code: null, signal: null }); });
  });
  child.stdout.resume();
  let partial = '';
  child.stderr.setEncoding('utf8');
  child.stderr.on('data', chunk => {
    instance.mainErrors.consume(chunk);
    if (!debug || instance.debugUrl) return;
    partial = (partial + chunk).slice(-8192);
    const lines = partial.split('\n'); partial = lines.pop();
    for (const line of lines) {
      const match = /^DevTools listening on (ws:\/\/127\.0\.0\.1:[1-9]\d*\/devtools\/browser\/[A-Za-z0-9-]+)\r?$/.exec(line);
      if (match) { instance.debugUrl = match[1]; partial = ''; break; }
    }
  });
  return instance;
}

// Only method names, result objects and session IDs stay in memory. Protocol errors
// deliberately discard their messages, which can contain URLs or page contents.
async function connect(url) {
  const socket = new WebSocket(url), pending = new Map();
  let sequence = 0, closed = false, listener = () => {};
  function rejectPending(kind = 'CDP_CLOSED') {
    closed = true;
    for (const { reject, timer, protocolMethod } of pending.values()) { clearTimeout(timer); reject(protocolError(kind,protocolMethod)); }
    pending.clear();
  }
  socket.addEventListener('close', () => rejectPending());
  socket.addEventListener('error', () => rejectPending());
  socket.addEventListener('message', event => {
    let message;
    try { message = JSON.parse(String(event.data)); } catch { rejectPending('CDP_INVALID_JSON'); return; }
    if (message.id) {
      const request = pending.get(message.id); if (!request) return;
      pending.delete(message.id); clearTimeout(request.timer);
      if (message.error) request.reject(protocolError('CDP_COMMAND',request.protocolMethod)); else request.resolve(message.result || {});
    } else {
      try { listener(message); } catch { rejectPending('CDP_LISTENER_FAILED'); }
    }
  });
  try {
    await bounded(new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once: true });
      socket.addEventListener('error', () => reject(protocolError('CDP_CONNECT')), { once: true });
    }), 10000);
  } catch (error) { socket.close(); throw error; }
  return {
    onEvent(handler) { listener = handler; },
    send(method, params = {}, sessionId, timeout = 10000) {
      const protocolMethod=safeProtocolMethod(method);
      if (closed || socket.readyState !== WebSocket.OPEN) return Promise.reject(protocolError('CDP_CLOSED',protocolMethod));
      return new Promise((resolve, reject) => {
        const id = ++sequence;
        const timer = setTimeout(() => { pending.delete(id); reject(protocolError('CDP_TIMEOUT',protocolMethod)); }, timeout);
        pending.set(id, { resolve, reject, timer, protocolMethod });
        try { socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) })); }
        catch { pending.delete(id); clearTimeout(timer); reject(protocolError('CDP_SEND',protocolMethod)); }
      });
    },
    close() { listener = () => {}; rejectPending(); socket.close(); },
  };
}
async function evaluate(expression, target = page) {
  const result = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, target.sessionId);
  try {assert.ok(!result.exceptionDetails);}
  catch(error) {error.protocolMethod='Runtime.evaluate';throw error;}
  return result.result?.value;
}
// Runtime inspects/layouts the DOM and reads the published update status. Product actions use actual CDP
// pointer input; navigation uses existing links or Page.navigate where no link exists.
function casesFrameExpression(expression, mode='value') {
  assert.ok(['value','point','visible'].includes(mode));
  return `(() => {
    const frame=document.querySelector('#casesFrame');
    if(!frame||frame.tagName!=='IFRAME')throw new Error('CASES_FRAME_MISSING');
    const frameStyle=getComputedStyle(frame),bounds=frame.getBoundingClientRect();
    if(frame.hidden||!frame.getClientRects().length||frameStyle.visibility!=='visible'||frameStyle.display==='none'
      ||Number(frameStyle.opacity)===0||bounds.width<=0||bounds.height<=0)throw new Error('CASES_FRAME_HIDDEN');
    let child;
    try {
      child=frame.contentWindow;
      if(!child||child.location.origin!==location.origin||child.document!==frame.contentDocument)
        throw new Error('CASES_FRAME_CROSS_ORIGIN');
    } catch {throw new Error('CASES_FRAME_CROSS_ORIGIN');}
    const result=((window,document,innerWidth,innerHeight,devicePixelRatio,location,getComputedStyle,requestAnimationFrame)=>(${expression}))
      (child,child.document,child.innerWidth,child.innerHeight,child.devicePixelRatio,child.location,
        child.getComputedStyle.bind(child),child.requestAnimationFrame.bind(child));
    if(${JSON.stringify(mode)}==='value')return result;
    if(!result)return ${mode==='visible'?'false':'null'};
    const sx=bounds.width/frame.offsetWidth,sy=bounds.height/frame.offsetHeight;
    const project=point=>({x:bounds.left+(frame.clientLeft+point.x)*sx,y:bounds.top+(frame.clientTop+point.y)*sy});
    const reachable=point=>point.x>=0&&point.y>=0&&point.x<innerWidth&&point.y<innerHeight
      &&document.elementFromPoint(point.x,point.y)===frame;
    if(${JSON.stringify(mode)}==='visible')return result.every(point=>reachable(project(point)));
    const point=project(result);return reachable(point)?point:null;
  })()`;
}
async function evaluateCases(expression,target=page) {return evaluate(casesFrameExpression(expression),target);}
async function readyCases(expression,target=page) {
  return readyDocument(casesFrameExpression(`document.readyState==='complete'&&(${expression})`),target);
}
async function clickElement(expression, target = page, {modifiers=0,frame=false} = {}) {
  assert.ok(Number.isInteger(modifiers)&&modifiers>=0&&modifiers<=15,'POINTER_MODIFIERS_INVALID');
  assert.equal(typeof frame,'boolean','POINTER_FRAME_INVALID');
  const probe=`(() => {
    const element = ${expression}; if (!element || element.disabled) return null;
    element.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    const rect = element.getBoundingClientRect(), x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
    if (rect.width <= 0 || rect.height <= 0 || x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return null;
    const hit = document.elementFromPoint(x, y); return hit && (hit === element || element.contains(hit)) ? { x, y } : null;
  })()`;
  const point = await evaluate(frame?casesFrameExpression(probe,'point'):probe, target);
  assert.ok(point);
  const modified=modifiers?{modifiers}:{};
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', ...point, button: 'left', clickCount: 1, ...modified }, target.sessionId);
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...point, button: 'left', clickCount: 1, ...modified }, target.sessionId);
}
function visibleControl(expression,{frame=false}={}) {
  const probe=`(() => {
    const element = ${expression}; if (!element || element.disabled) return false;
    const rect = element.getBoundingClientRect(), style = getComputedStyle(element);
    if (style.visibility !== 'visible' || style.display === 'none' || Number(style.opacity) === 0
      || rect.width < 16 || rect.height < 16 || rect.left < -1 || rect.top < -1
      || rect.right > innerWidth + 1 || rect.bottom > innerHeight + 1) return false;
    const points=[[.5,.5],[.2,.2],[.8,.2],[.2,.8],[.8,.8]].map(([rx,ry])=>({x:rect.x+rect.width*rx,y:rect.y+rect.height*ry}));
    const accessible=points.every(({x,y}) => {
      const hit = document.elementFromPoint(x,y);
      return hit && (hit === element || element.contains(hit));
    });
    return accessible?${frame?'points':'true'}:false;
  })()`;
  return frame?casesFrameExpression(probe,'visible'):probe;
}
async function readyDocument(expression, target = page) {
  await until(async () => {
    try { return await evaluate(`document.readyState === 'complete' && (${expression})`, target); }
    catch { return false; } // An actual navigation may replace the execution context.
  }, 20000);
}
async function viewportMatrix(name, target, inspect, controls, reachableControls = [],{frame=false}={}) {
  // Chromium emulation checks the shipped renderer at CSS sizes and emulated
  // DPRs. It does not change Windows display scaling or establish real DPI evidence.
  for (const [width, height] of VIEWPORTS) for (const deviceScaleFactor of DEVICE_SCALES) {
    await check(`${name}Css${width}x${height}Dpr${String(deviceScaleFactor).replace('.', '_')}`, async () => {
      await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor,
        mobile: false, screenWidth: width, screenHeight: height }, target.sessionId);
      await evaluate(`new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))`, target);
      assert.equal(await evaluate(`innerWidth === ${width} && innerHeight === ${height}
        && devicePixelRatio === ${deviceScaleFactor}&&document.documentElement.scrollWidth<=innerWidth+1`, target), true);
      if(frame)assert.equal(await evaluate(`(() => {
        const frame=document.querySelector('#casesFrame'),r=frame.getBoundingClientRect(),slot=frame.parentElement.getBoundingClientRect();
        return r.left>=slot.left-1&&r.right<=slot.right+1&&r.top>=slot.top-1&&r.bottom<=slot.bottom+1
          &&r.left>=0&&r.top>=0&&r.right<=innerWidth+1&&r.bottom<=innerHeight+1
          &&Math.abs(frame.contentWindow.innerWidth-frame.clientWidth)<=1&&Math.abs(frame.contentWindow.innerHeight-frame.clientHeight)<=1;
      })()`,target),true,'CASES_FRAME_SLOT_GEOMETRY');
      const local=`document.documentElement.scrollWidth<=innerWidth+1&&(${inspect})`;
      assert.equal(await evaluate(frame?casesFrameExpression(local):local,target),true);
      for (const control of controls) assert.equal(await evaluate(visibleControl(control,{frame}), target), true);
      for (const control of reachableControls) {
        // React is an ordinary vertically scrolling document. Check the empty
        // case entry after bringing it into view, including overlay hit tests.
        const scroll=`(${control}).scrollIntoView({block:'center',inline:'nearest'})`;
        await evaluate(frame?casesFrameExpression(scroll):scroll, target);
        assert.equal(await evaluate(visibleControl(control,{frame}), target), true);
      }
      const reset=`(() => {window.scrollTo(0,0);document.querySelector('.standalone-main')?.scrollTo(0,0);})()`;
      await evaluate(frame?casesFrameExpression(reset):reset, target);
    });
  }
}
async function portClosed(port) {
  return new Promise(resolve => {
    const socket = net.connect({ host: '127.0.0.1', port });
    const finish = value => { socket.destroy(); resolve(value); };
    socket.once('connect', () => finish(false));
    socket.once('error', error => finish(error.code === 'ECONNREFUSED'));
    socket.setTimeout(1000, () => finish(false));
  });
}
async function readPresentationPreferences(userRoot) {
  // The lifecycle sentinel binds this path to the disposable CI user above.
  // Retain only the two presentation keys this journey changes, never case data.
  const file = path.join(userRoot, 'preferences.json');
  const values = await fs.readFile(file, 'utf8').catch(error => {
    if (error.code === 'ENOENT') return '{}';
    throw error;
  });
  assert.ok(Buffer.byteLength(values) <= 16384, 'PRESENTATION_PREFERENCES_TOO_LARGE');
  const current = JSON.parse(values), saved = {};
  assert.ok(current && typeof current === 'object' && !Array.isArray(current));
  for (const key of ['fund_window_state', 'fund_sidebar_width']) {
    if (Object.hasOwn(current, key)) saved[key] = current[key];
  }
  return { file, saved };
}
async function restorePresentationPreferences(snapshot) {
  const values = await fs.readFile(snapshot.file, 'utf8').catch(error => {
    if (error.code === 'ENOENT') return '{}';
    throw error;
  });
  assert.ok(Buffer.byteLength(values) <= 16384, 'PRESENTATION_PREFERENCES_TOO_LARGE');
  const current = JSON.parse(values);
  assert.ok(current && typeof current === 'object' && !Array.isArray(current));
  for (const key of ['fund_window_state', 'fund_sidebar_width']) {
    if (Object.hasOwn(snapshot.saved, key)) current[key] = snapshot.saved[key];
    else delete current[key];
  }
  const temporary = `${snapshot.file}.ci-presentation-${process.pid}.tmp`;
  try {
    await fs.writeFile(temporary, `${JSON.stringify(current)}\n`, { flag:'wx' });
    await fs.rename(temporary, snapshot.file);
  } finally { await fs.rm(temporary, { force:true }); }
}

(async () => {
  const config = await check('ciSyntheticUser', async () => {
    assert.equal(process.platform, 'win32'); assert.equal(process.arch, 'x64');
    assert.equal(process.env.CI, 'true'); assert.equal(process.env.GITHUB_ACTIONS, 'true');
    assert.ok(Number(process.versions.node.split('.')[0]) >= 22 && typeof WebSocket === 'function');
    const value = argumentsOf();
    assert.ok(process.env.APPDATA && path.isAbsolute(process.env.APPDATA));
    const userRoot = path.join(process.env.APPDATA, 'FundsTrace');
    assert.equal(await fs.readFile(path.join(userRoot, 'installer-lifecycle-sentinel.txt'), 'utf8'), SENTINEL);
    assert.ok((await fs.stat(path.join(userRoot, 'data', 'fund.duckdb'))).isFile());
    if (!value.updateFixture && !value.productionUpdate && !value.presentationBaseline)
      presentationPreferences = await readPresentationPreferences(userRoot);
    for (const output of [value.reportPath, value.screenshot].filter(Boolean)) {
      assert.ok(!within(userRoot, output) && !within(value.installRoot, output));
    }
    assert.ok((await fs.stat(path.join(value.installRoot, 'FundsTrace.exe'))).isFile());
    return value;
  });
  reportPath = config.reportPath;
  productionUpdate = !!config.productionUpdate;
  if (productionUpdate) {
    try {
      const input = JSON.parse((await fs.readFile(config.productionUpdate, 'utf8')).replace(/^\uFEFF/, ''));
      productionTransportConfig = Object.fromEntries(['currentVersion', 'version', 'installerSha256',
        'installerSha512', 'installerBytes'].map(key => [key, input[key]]));
    } catch { /* The existing configuration check retains authority to fail. */ }
  }
  const priorClientOnly = productionUpdate || !!config.presentationBaseline;
  if (config.startupClose) report.limits = { windowsServerAutomationOnly:true, windows10And11Interactive:false,
    actualSystemDpiValidated:false, importRecovery:false, startupBusinessRendererIntentionallyPaused:true,
    normalWorkbenchJourney:false, nativeCaptionCloseRequired:true, fallbackCloseAllowed:false };
  if (config.presentationBaseline) report.limits = { windowsServerAutomationOnly:true, windows10And11Interactive:false,
    actualSystemDpiValidated:false, importRecovery:false, priorClientPresentationOnly:true,
    nativeUserResizeRequired:true, nativeCaptionCloseRequired:true, fallbackCloseAllowed:false,
    preferencesWrittenOnlyByInstalledClient:true, preferencesRestoredByHelper:false };
  await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx' }); reportOwned = true;
  if (config.updateFixture) updateFixture = await check('isolatedUpdateHttpsFixture', () => createUpdateFixture(config.updateFixture));
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) =>
    !/^(FUND_|FUNDS_TRACE_|ELECTRON_|NODE_OPTIONS$)/i.test(name)));
  const executable = path.join(config.installRoot, 'FundsTrace.exe');
  const primary = await check('installedExecutableStarted', async () => {
    const instance = boot(executable, config.installRoot, env, true);
    await bounded(new Promise((resolve, reject) => {
      instance.child.once('spawn', resolve);
      instance.child.once('error', () => reject(new Error('SPAWN_FAILED')));
    }), 10000);
    return instance;
  });
  if (productionUpdate) productionOwner = { pid: primary.child.pid, installRoot: config.installRoot };
  let errorCount = 0, instrumentationFailed = false;
  const sessions = new Map();
  let startupInspectionHeld = !priorClientOnly;
  const heldBusinessSessions = new Set(), debuggerSessions = new Set();
  releaseStartupInspection = async () => {
    startupInspectionHeld = false;
    for (const sessionId of debuggerSessions) {
      await cdp.send('Debugger.resume', {}, sessionId, 3000).catch(() => {});
      await cdp.send('Debugger.disable', {}, sessionId, 3000).catch(() => {});
    }
    heldBusinessSessions.clear();
    debuggerSessions.clear();
  };
  await check(config.startupClose ? 'installedStartupCloseInstrumentation' : 'installedWindowReady', async () => {
    const debugUrl = await until(() => {
      assert.ok(!primary.done); return primary.debugUrl;
    }, 30000);
    cdp = await connect(debugUrl);
    cdp.onEvent(message => {
      if (message.method === 'Runtime.exceptionThrown' || message.method === 'Inspector.targetCrashed'
        || message.method === 'Target.targetCrashed'
        || message.method === 'Runtime.consoleAPICalled' && message.params?.type === 'error') errorCount += 1;
      if (message.method === 'Debugger.paused') {
        const item = [...sessions.values()].find(value => value.sessionId === message.sessionId);
        void (async () => {
          if (!item) return;
          const { targetInfo } = await cdp.send('Target.getTargetInfo', { targetId:item.targetId });
          if (startupInspectionHeld && businessOrigin(targetInfo.url)) {
            item.paused = true;
            item.beforeScriptExecution = message.params?.reason === 'instrumentation';
            heldBusinessSessions.add(item.sessionId);
          } else {
            // An initially blank target may turn out to be the local overlay.
            // Never keep that renderer paused based on stale attachment metadata.
            await cdp.send('Debugger.resume', {}, item.sessionId).catch(() => {});
            if (!startupInspectionHeld || startupTarget(targetInfo.url) || titlebarTarget(targetInfo.url)) {
              await cdp.send('Debugger.disable', {}, item.sessionId).catch(() => {});
              debuggerSessions.delete(item.sessionId);
            }
          }
        })().catch(() => { if (startupInspectionHeld) instrumentationFailed = true; });
        return;
      }
      if (message.method !== 'Target.attachedToTarget') return;
      const { sessionId, targetInfo } = message.params;
      const item = { sessionId, targetId:targetInfo.targetId, ready: false, paused:false };
      if (targetInfo.type === 'page') sessions.set(targetInfo.targetId, item);
      void (async () => {
        // A newly created renderer is paused before its execution context
        // exists. Resume it before awaiting Runtime.enable, which otherwise
        // waits for the same paused renderer to initialize.
        await cdp.send('Runtime.runIfWaitingForDebugger', {}, sessionId);
        if (targetInfo.type === 'page') {
          await cdp.send('Runtime.enable', {}, sessionId);
          await cdp.send('Page.enable', {}, sessionId);
          if (startupInspectionHeld) {
            // CI inspection only: hold execution of our own business page while
            // measuring the unmodified shipped startup layer. No fake readiness.
            debuggerSessions.add(sessionId);
            await cdp.send('Debugger.enable', {}, sessionId);
            if (startupInspectionHeld) await cdp.send('Debugger.setInstrumentationBreakpoint', { instrumentation:'beforeScriptExecution' }, sessionId);
            if (!startupInspectionHeld) await cdp.send('Debugger.disable', {}, sessionId).catch(() => {});
          }
        }
        item.ready = true;
      })().catch(() => { instrumentationFailed = true; });
    });
    await cdp.send('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: true, flatten: true });
    if (config.startupClose) {
      page = await check('installedStartupCloseBusinessPaused', () => until(async () => {
        assert.ok(!primary.done && !instrumentationFailed && startupInspectionHeld);
        const held = [...sessions.values()].filter(item => heldBusinessSessions.has(item.sessionId));
        assert.ok(held.length <= 1);
        const item = held[0];
        if (!item?.ready || !item.paused || !item.beforeScriptExecution
          || [...sessions.values()].some(session => !session.ready)) return false;
        const { targetInfo } = await cdp.send('Target.getTargetInfo', { targetId:item.targetId });
        const origin = businessOrigin(targetInfo.url);
        assert.equal(targetInfo.targetId, item.targetId);
        assert.equal(targetInfo.type, 'page');
        if (!origin) return false;
        // Main starts this navigation only after authenticating its owned
        // backend. Keep that real business page paused before any resume.
        return { ...item, origin };
      }, 30000));
      report.startupClose = { businessRendererPausedBeforeScript:true, backendOriginObserved:true,
        keptPausedForNativeClose:false };
      await check('installedStartupCloseNativeCaption', async () => {
        assert.ok(startupInspectionHeld && heldBusinessSessions.has(page.sessionId)
          && sessions.get(page.targetId)?.paused && sessions.get(page.targetId)?.beforeScriptExecution);
        report.startupClose.keptPausedForNativeClose = true;
        report.startupClose.nativeCaption = await nativeStartupWindow(config.installRoot,primary.child.pid,{action:'CaptionClose'});
        assert.equal(report.startupClose.nativeCaption.status,'passed');
        assert.equal(report.startupClose.nativeCaption.interaction.CloseButtonClicked,true);
        assert.equal(report.startupClose.nativeCaption.interaction.WindowClosedObserved,true);
      });
      await check('installedStartupCloseCleanExit', async () => {
        assert.deepEqual(await bounded(primary.exited,30000),{code:0,signal:null});
      });
      await check('installedStartupCloseBackendReleased', () => until(() => portClosed(Number(new URL(page.origin).port)),15000));
      await check('installedStartupCloseNoPageExceptions', async () => {
        assert.ok(!instrumentationFailed); assert.equal(errorCount,0);
      });
      return;
    }
    if (!priorClientOnly) await check('installedStartupAnimationAndBounds', () => bounded((async () => {
      const expectedUrl = pathToFileURL(path.join(config.installRoot, 'resources/app.asar/startup/index.html')).href;
      const startup = await until(async () => {
        assert.ok(!primary.done && !instrumentationFailed);
        const { targetInfos } = await cdp.send('Target.getTargets');
        const pages = targetInfos.filter(target => target.type === 'page');
        assert.ok(pages.length <= 3);
        const overlays = pages.filter(target => startupTarget(target.url));
        const captions = pages.filter(target => titlebarTarget(target.url));
        const business = pages.filter(target => businessOrigin(target.url));
        const pending = pages.filter(target => !target.url || target.url === 'about:blank');
        assert.ok(overlays.length <= 1 && captions.length <= 1 && business.length <= 1
          && overlays.length + captions.length + business.length + pending.length === pages.length);
        const caption = captions[0], captionSession = caption && sessions.get(caption.targetId);
        const target = overlays[0], session = target && sessions.get(target.targetId);
        if (!target || !session?.ready || !captionSession?.ready) return false;
        assert.equal(target.url, expectedUrl);
        assert.equal(caption.url, pathToFileURL(path.join(config.installRoot, 'resources/app.asar/titlebar/index.html')).href);
        titlebarPage = { ...captionSession, targetId:caption.targetId };
        return { ...session, targetId:target.targetId };
      }, 20000);
      await until(() => [...sessions.values()].some(item => heldBusinessSessions.has(item.sessionId) && item.paused), 15000);
      report.startup = { deliberatelyHeldBusinessRenderer:true, measuredUserColdStart:false };
      titlebarProbe = await createInstalledTitlebarProbe({cdp,target:titlebarPage,evaluate,until,
        failureScreenshotPath:config.screenshot ? `${config.screenshot.slice(0,-4)}.startup-caption-failure.png` : undefined});
      report.titlebar = {heightDip:32,limits:{windows11Snap:false,windows10And11Interactive:false,actualSystemDpiValidated:false}};
      const environmentReducedMotion = await evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches", startup);
      await cdp.send('Emulation.setEmulatedMedia', {
        features:[{ name:'prefers-reduced-motion', value:'no-preference' }],
      }, startup.sessionId);
      await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))', startup);
      assert.equal(await evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches", startup), false);
      report.startup.motionPreference = { environmentReducedMotion, tested:'no-preference', emulatedForInspection:true };
      const probe = await createInstalledStartupProbe({ cdp, target:startup, evaluate, until,
        screenshotPrefix:config.screenshot ? `${config.screenshot.slice(0, -4)}.startup` : undefined });
      report.startup.nativeWindows = await nativeStartupBounds(config, primary, startup);
      report.startup.waiting = await probe.observeWaitingAndLayout();
      await releaseStartupInspection();
      report.startup.completion = await probe.observeCompletion({ timeoutMs:15000 });
      assert.ok(!instrumentationFailed);
    })(), 45000).finally(releaseStartupInspection));
    stage = 'installedWindowReady';
    page = await until(async () => {
      assert.ok(!primary.done && !instrumentationFailed);
      const { targetInfos } = await cdp.send('Target.getTargets');
      const pages = targetInfos.filter(target => target.type === 'page' && !startupTarget(target.url) && !titlebarTarget(target.url));
      assert.ok(pages.length <= 1);
      const target = pages[0];
      const origin = target && businessOrigin(target.url);
      if (!origin) return false;
      const session = sessions.get(target.targetId);
      return session?.ready ? { ...session, targetId: target.targetId, origin } : false;
    }, 90000);
    await until(async () => {
      assert.ok(!primary.done && !instrumentationFailed);
      const session = sessions.get(page.targetId);
      if (!session?.ready) return false;
      page.sessionId = session.sessionId;
      // Navigation can replace the execution context while a first DOM probe
      // is in flight. Retry only this bounded navigation probe.
      try {
        return await evaluate(`document.readyState === 'complete' && !!document.querySelector('#graphContainer')
          && !!document.querySelector('#caseSelect') && typeof state === 'object' && !state.graphLoading && state.graphInited`);
      } catch { return false; }
    }, 15000);
  });
  if (config.startupClose) { report.status = 'passed'; return; }
  if (config.presentationBaseline) {
    const baselineConfig = JSON.parse((await fs.readFile(config.presentationBaseline, 'utf8')).replace(/^\uFEFF/, ''));
    report.presentationBaseline = await runInstalledPresentationBaseline({ config:baselineConfig, primary, evaluate,
      ready:() => until(async () => {
        assert.ok(!primary.done && !instrumentationFailed);
        const { targetInfos } = await cdp.send('Target.getTargets');
        // The old client's real workbench readiness and completed startup fade
        // must precede the native resize. No new caption/graph assertions apply.
        return !targetInfos.some(target => target.type === 'page' && startupTarget(target.url))
          && targetInfos.some(target => target.targetId === page.targetId && businessOrigin(target.url) === page.origin);
      }, 15000),
      nativeWindow:options => nativeStartupWindow(config.installRoot, primary.child.pid, options),
      readPreferences:() => fs.readFile(path.join(process.env.APPDATA, 'FundsTrace', 'preferences.json')),
      until, bounded, backendReleased:() => portClosed(Number(new URL(page.origin).port)), check });
    await check('priorPresentationNoPageExceptions', async () => {
      assert.ok(!instrumentationFailed); assert.equal(errorCount, 0);
    });
    report.status = 'passed'; return;
  }
  if (!priorClientOnly) await check('installedPersistentCaptionStartup', async () => {
    report.titlebar.startup = await titlebarProbe.finishStartup();
    report.titlebar.readyBody = await titlebarProbe.checkBody(page);
    const waiting = report.startup.nativeWindows.clientMeasurements.at(-1), ready = report.titlebar.readyBody.body;
    assert.ok(Math.abs(waiting.width-ready.width)<=2 && Math.abs(waiting.height-ready.height)<=2,
      'TITLEBAR_STARTUP_BUSINESS_VIEWPORT_DIFFERENT');
  });
  if (config.productionUpdate) {
    // The real prior release predates current graph/icon checks. Exercise only
    // its shipped updater here; the lifecycle verifies the new install below.
    report.updateCycle = await runInstalledProductionUpdateJourney({ configFile: config.productionUpdate,
      primary, evaluate, clickElement, cdp, page, check, screenshot: config.screenshot,
      observations: report.productionUpdateObservations = {} });
    await check('productionUpdateNoPageExceptions', async () => {
      assert.ok(!instrumentationFailed); assert.equal(errorCount, 0);
    });
    await check('productionUpdateOldBackendReleased', () => until(() => portClosed(Number(new URL(page.origin).port)), 15000));
    report.status = 'passed'; return;
  }
  report.workspaceWindowIcons = await check('installedWorkspaceWindowIcon', () => windowIcons(config.installRoot, primary.child.pid, 1,
    config.screenshot ? `${config.screenshot.slice(0, -4)}.taskbar.png` : undefined));
  await check('nodeSelectionModuleLoaded', async () => {
    assert.equal(await evaluate(`typeof window.FundsGraphSelection?.create === 'function'
      && !!state.graphSelection && !state.graphSelection.status().destroyed`), true);
  });
  await check('authenticatedWorkspaceLoaded', async () => {
    // The renderer's own normal fetch exercises Main's exact-origin request policy.
    // Return booleans only; never ask CDP for network headers or Main state.
    assert.equal(await evaluate(`(async () => {
      const response = await fetch('/api/v1/cases'); if (!response.ok) return false;
      const value = await response.json(), allowed = new Set(${JSON.stringify([SEED_CASE, CREATED_CASE, ...COMPATIBILITY_CASES])});
      return Array.isArray(value.items) && value.items.some(item => item.name === ${JSON.stringify(SEED_CASE)})
        && value.items.some(item => item.name === ${JSON.stringify(CREATED_CASE)})
        && value.items.every(item => item.id === '1' || allowed.has(item.name));
    })()`), true);
    const denied = await fetch(`${page.origin}/api/v1/cases`, { redirect: 'manual', signal: AbortSignal.timeout(10000) });
    assert.equal(denied.status, 403); await denied.arrayBuffer();
  });
  await check('activationFree', async () => {
    assert.equal(await evaluate(`(async () => {
      const response = await fetch('/api/license/info'); if (!response.ok) return false;
      const value = await response.json(); return value.activation_required === false && value.activated === false;
    })()`), true);
  });
  await check('installedPersistentCaptionZoomReloadAndNativeControls', async () => {
    report.titlebar.journey = await runInstalledTitlebarJourney({cdp,page,probe:titlebarProbe,evaluate,until,
      nativeAction:options=>nativeStartupWindow(config.installRoot,primary.child.pid,options)});
  });
  await check('installedMinimumWorkspaceToolsAndSingleTableScroll', async () => {
    const samples = report.minimumWorkspace = [];
    const inspect = async (label,expected) => {
      let value;
      try {
        await until(async()=>{
          value=await evaluate(`(${readWorkspaceTools.toString()})()`);
          return value.collapsed===expected.collapsed&&value.compactVisible===expected.collapsed
            &&value.panels.join(',')===(expected.collapsed?'':`ribbonPanel-${expected.tab}`)
            &&Math.abs(value.cssHeight-(expected.collapsed?66:148))<.05
            &&value.slotClientWidth>0&&value.logicalWidth===Math.max(1280,value.slotClientWidth)
            &&Math.abs(value.scale-Math.min(1,value.slotClientWidth/1280))<1e-6;
        },5000);
      } finally { if(value)samples.push({label,...value}); }
      assertWorkspaceTools(value,expected);
    };
    try {
      for(const size of [null,[1280,688,1],[1440,808,1],[1024,648,1.5]]) {
        if(size) await cdp.send('Emulation.setDeviceMetricsOverride',{width:size[0],height:size[1],deviceScaleFactor:size[2],mobile:false},page.sessionId);
        else await cdp.send('Emulation.clearDeviceMetricsOverride',{},page.sessionId);
        for(const tab of ['common','trace','style','view']) {
          await clickElement(`document.querySelector('#ribbonTab-${tab}')`);
          await inspect(`${size?size.join('x'):'native'}:${tab}`,{tab,collapsed:false});
        }
        await clickElement(`document.querySelector('[data-ribbon-collapse="true"]')`);
        await inspect(`${size?size.join('x'):'native'}:compact`,{tab:'view',collapsed:true});
        await clickElement(`document.querySelector('#editorToolbar .ribbon-tabbar [data-ribbon-collapse="false"]')`);
      }
    } finally {
      await cdp.send('Emulation.clearDeviceMetricsOverride',{},page.sessionId);
      await clickElement(`document.querySelector('#ribbonTab-common')`);
    }
  });
  await check('syntheticWindowContent', async () => {
    assert.equal(await evaluate(`(() => {
      const select = document.querySelector('#caseSelect');
      const option = [...select.options].find(item => item.textContent === ${JSON.stringify(CREATED_CASE)});
      if (!option) return false; select.value = option.value; select.dispatchEvent(new Event('change', { bubbles: true })); return true;
    })()`), true);
    await until(() => evaluate(`!state.graphLoading && state.graphInited && state.graphSelection
      && state.cases.find(item => item.id === state.caseId)?.name === ${JSON.stringify(CREATED_CASE)}
      && state.lastGraph.nodes.length === 0 && state.lastGraph.edges.length === 0`), 15000);
  });
  await viewportMatrix('graphLayout', page, `(() => {
    const graph = document.querySelector('#graphContainer')?.getBoundingClientRect();
    return graph && graph.width > 100 && graph.height > 80 && graph.left >= 0 && graph.top >= 0
      && graph.right <= innerWidth + 1 && graph.bottom <= innerHeight + 1
      && !!document.querySelector('#graphContainer canvas, #graphContainer svg');
  })()`, [`document.querySelector('[data-ribbon-proxy="btnImport"]')`, `document.querySelector('#btnImport2')`]);
  await check('minimalCssViewportScreenshot', async () => {
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 960, height: 500, deviceScaleFactor: 1,
      mobile: false, screenWidth: 960, screenHeight: 500 }, page.sessionId);
    await evaluate(`new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))`);
    if (config.screenshot) {
      const result = await cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false }, page.sessionId);
      assert.ok(typeof result.data === 'string' && result.data.length < 24 * 1024 * 1024);
      const bytes = Buffer.from(result.data, 'base64');
      assert.ok(bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])));
      await fs.writeFile(config.screenshot, bytes, { flag: 'wx' });
    }
  });
  await runInstalledGraphJourney({ cdp, page, evaluate, clickElement, until, check,
    createdCase: CREATED_CASE, seedCase: SEED_CASE,
    captureGraph: config.screenshot ? () => check('installedGraphScreenshot', async () => {
      const result = await cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true,
        captureBeyondViewport: false }, page.sessionId);
      assert.ok(typeof result.data === 'string' && result.data.length < 24 * 1024 * 1024);
      const bytes = Buffer.from(result.data, 'base64');
      assert.ok(bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])));
      await fs.writeFile(`${config.screenshot.slice(0, -4)}.graph.png`, bytes, { flag: 'wx' });
    }) : null,
  });
  await check('installedReactWorkspaceOpened', async () => {
    const enteringCase=await evaluate(`document.querySelector('#caseSelect').value`);
    assert.ok(typeof enteringCase==='string'&&/^\d+$/.test(enteringCase));
    // Public direct entry must redirect into the real host. Never render a
    // standalone React root or bypass the case-bound embedded bridge.
    await cdp.send('Page.navigate', { url:`${page.origin}/workspace/?case_id=${encodeURIComponent(enteringCase)}` }, page.sessionId);
    await readyDocument(`location.pathname==='/'&&new URLSearchParams(location.search).get('page')==='cases'
      &&new URLSearchParams(location.search).get('case_id')===${JSON.stringify(enteringCase)}
      &&document.querySelector('#caseSelect')?.value===${JSON.stringify(enteringCase)}&&(() => {
        const frame=document.querySelector('#casesFrame');if(!frame||frame.hidden)return false;
        const url=new URL(frame.src);return url.origin===location.origin&&url.pathname==='/workspace/'
          &&url.searchParams.get('embedded')==='1'&&url.searchParams.get('surface')==='cases'
          &&url.searchParams.get('case_id')===${JSON.stringify(enteringCase)};
      })()`);
    await readyCases(`!!document.querySelector('.funds-trace .case-list')
      &&[...document.querySelectorAll('.case-item')].some(item=>item.getAttribute('aria-label')===${JSON.stringify(SEED_CASE)})`);
    await clickElement(`[...document.querySelectorAll('.case-item')].find(item=>item.getAttribute('aria-label')===${JSON.stringify(SEED_CASE)})`,page,{frame:true});
    await readyCases(`document.querySelector('.workspace-heading h1')?.textContent === ${JSON.stringify(SEED_CASE)}
      && !!document.querySelector('.empty-state h3') && !document.querySelector('[role="alert"]')`);
    await readyDocument(`(() => {
      const doc=document.querySelector('#casesFrame').contentDocument,select=document.querySelector('#caseSelect');
      return select.selectedOptions[0]?.textContent===${JSON.stringify(SEED_CASE)}
        &&doc.querySelector('.workspace-heading .description .mono')?.textContent===select.value
        &&doc.querySelector('.case-item[aria-pressed="true"]')?.getAttribute('aria-label')===${JSON.stringify(SEED_CASE)};
    })()`);
    report.workspaceEntry={publicRedirectPreservedCase:true,embeddedCasesSurface:true,caseSelectionLinked:true};
  });
  await viewportMatrix('reactWorkspaceLayout', page, `!!document.querySelector('.main-workspace[aria-label="资金流水面板"]')
    && document.querySelector('.workspace-heading h1')?.textContent === ${JSON.stringify(SEED_CASE)}
    && !document.querySelector('[role="alert"]')`, [
    `document.querySelector('.heading-actions .button.primary')`,
  ], [
    `document.querySelector('.empty-state .button.primary')`,
  ],{frame:true});
  await check('reactEmptyCaseImportEntry', async () => {
    await clickElement(`document.querySelector('.empty-state .button.primary')`,page,{frame:true});
    await readyCases(`!!document.querySelector('dialog[open] input[type="file"]')`);
    assert.equal(await evaluate(visibleControl(`document.querySelector('dialog[open] label.file-drop')`,{frame:true})), true);
    assert.deepEqual(await evaluateCases(`(${readImportEntry.toString()})()`),
      {associated:true,type:'file',accept:'.csv,.tsv,.txt,.xlsx',enabled:true});
    await clickElement(`[...document.querySelectorAll('dialog[open] button')].find(item => item.textContent.trim() === '取消')`,page,{frame:true});
    await readyCases(`!document.querySelector('dialog[open]')`);
  });
  await check('workspaceGraphLinkOpened', async () => {
    await clickElement(`[...document.querySelectorAll('.heading-actions button')].find(node=>node.textContent.trim()==='进入画图')`,page,{frame:true});
    await readyDocument(`location.pathname === '/' && typeof state === 'object' && !state.graphLoading
      && state.graphInited && state.cases.find(item => item.id === state.caseId)?.name === ${JSON.stringify(SEED_CASE)}
      &&state.stats.flows===0&&state.lastGraph.nodes.length===0&&document.querySelector('#casesFrame').hidden
      &&document.querySelector('[data-nav-page="home"]').getAttribute('aria-current')==='page'`);
  });
  await check('installedPersistentCaptionNavigation', async () => {
    report.titlebar.navigation = {sameShellTarget:true,caption:await titlebarProbe.read()};
  });
  await check('installedEmbeddedAnalysisCaseLinked', async () => {
    await clickElement(`document.querySelector('#navAnalysis')`);
    await readyDocument(`(() => {
      const frame=document.querySelector('#analysisFrame'),select=frame?.contentDocument?.querySelector('#caseSel');
      return frame&&!frame.hidden&&frame.contentDocument.readyState==='complete'
        &&document.querySelector('#navAnalysis').getAttribute('aria-current')==='page'
        &&select&&!select.disabled&&select.value===document.querySelector('#caseSelect').value
        &&frame.contentDocument.querySelector('#caseStats')?.textContent.includes('流水');
    })()`);
    await clickElement(`document.querySelector('[data-nav-page="home"]')`);
    await readyDocument(`document.querySelector('#analysisFrame').hidden&&!document.querySelector('#graphPage').inert
      &&!state.graphLoading&&state.graphInited&&state.cases.find(item=>item.id===state.caseId)?.name===${JSON.stringify(SEED_CASE)}`);
    report.titlebar.embeddedAnalysis={sameCase:true,returnedToGraph:true,...await titlebarProbe.checkBody(page)};
  });
  await check('analysisLinkClicked', async () => {
    await clickElement(`document.querySelector('#ribbonTab-trace')`);
    await until(()=>evaluate(visibleControl(`document.querySelector('[data-analysis-algorithm="paths"]')`)),5000);
    // Ctrl is a real CDP pointer modifier (bit 2), preserving the product's
    // explicit external-window path instead of synthesizing window.open().
    await clickElement(`document.querySelector('[data-analysis-algorithm="paths"]')`,page,{modifiers:2});
  });
  await check('installedAnalysisWindowAttached', async () => {
    analysisPage = await until(async () => {
      assert.ok(!primary.done && !instrumentationFailed);
      const { targetInfos } = await cdp.send('Target.getTargets');
      const pageCount = targetInfos.filter(target => target.type === 'page' && !titlebarTarget(target.url)).length;
      assert.ok(pageCount <= 2);
      if (pageCount !== 2) return false;
      const target = targetInfos.find(target => {
        if (target.type !== 'page' || target.targetId === page.targetId) return false;
        try { const url = new URL(target.url); return url.origin === page.origin && url.pathname === '/analysis'; }
        catch { return false; }
      });
      const session = target && sessions.get(target.targetId);
      return session?.ready ? { ...session, targetId: target.targetId, origin: page.origin } : false;
    }, 20000);
  });
  await check('installedAnalysisCaptionBoundToWindow', async () => {
    assert.notEqual(analysisPage.targetId,page.targetId,'TITLEBAR_ANALYSIS_BUSINESS_TARGET');
    analysisTitlebarPage = await until(async () => {
      const {targetInfos} = await cdp.send('Target.getTargets');
      assert.ok(targetInfos.some(value=>value.targetId===titlebarPage.targetId)
        && targetInfos.some(value=>value.targetId===page.targetId),'TITLEBAR_PRIMARY_TARGETS_LOST_DURING_ANALYSIS_OPEN');
      const captions=targetInfos.filter(value=>value.type==='page' && titlebarTarget(value.url) && value.targetId!==titlebarPage.targetId);
      assert.ok(captions.length<=1,'TITLEBAR_ANALYSIS_CAPTION_AMBIGUOUS');
      const target=captions[0],session=target && sessions.get(target.targetId);
      if(target) assert.ok(target.url===pathToFileURL(path.join(config.installRoot,'resources/app.asar/titlebar/index.html')).href,
        'TITLEBAR_ANALYSIS_CAPTION_FILE');
      return session?.ready ? {...session,targetId:target.targetId} : false;
    },10000);
    const probe = await createInstalledTitlebarProbe({cdp,target:analysisTitlebarPage,evaluate,until});
    report.titlebar.analysis = {...await probe.checkBody(analysisPage),targetSeparation:{distinctBodies:true,distinctCaptions:true,
      newAnalysisCaptionAfterOpen:true,primaryCaptionRetained:true},
      evidenceScope:'distinct-installed-renderer-targets-and-layout; window lifetime checked on close'};
  });
  await check('installedAnalysisLinkOpened', async () => {
    await readyDocument(`location.pathname === '/analysis' && !!document.querySelector('#panel')
      && !!document.querySelector('#results .analysis-empty') && !!document.querySelector('#caseSel')?.options.length
      && document.querySelector('#caseStats')?.textContent.includes('流水')`, analysisPage);
  });
  report.analysisWindowIcons = await check('installedAnalysisWindowIcon', () => windowIcons(config.installRoot, primary.child.pid, 2));
  await viewportMatrix('analysisLayout', analysisPage, `!!document.querySelector('#panel')
    && !!document.querySelector('#results .analysis-empty') && document.querySelector('#caseStats')?.textContent.includes('流水')`, [
    `document.querySelector('#caseSel')`, `document.querySelector('#backToGraph')`,
  ]);
  await check('analysisControlsReachable', async () => {
    await evaluate(`document.querySelector('#btnRun').scrollIntoView({ block: 'center' })`, analysisPage);
    assert.equal(await evaluate(visibleControl(`document.querySelector('#btnRun')`), analysisPage), true);
    report.titlebar.analysis.nativeClose=await nativeStartupWindow(config.installRoot,primary.child.pid,{action:'CaptionCloseForeground'});
    if(report.titlebar.analysis.nativeClose.status==='unavailable') {
      report.titlebar.analysis.nativeClose.fallback='window.close-on-owning-local-shell; not a native button click';
      // A successful close can destroy this execution context before its reply.
      await evaluate('window.close()',analysisTitlebarPage).catch(()=>{});
    } else {
      assert.equal(report.titlebar.analysis.nativeClose.interaction.CloseButtonClicked,true,'TITLEBAR_ANALYSIS_NATIVE_CLOSE_CLICK');
      assert.equal(report.titlebar.analysis.nativeClose.interaction.WindowClosedObserved,true,'TITLEBAR_ANALYSIS_NATIVE_CLOSE_OBSERVED');
    }
    await until(async () => {
      const { targetInfos } = await cdp.send('Target.getTargets');
      const alive=id=>targetInfos.some(target=>target.targetId===id);
      const lifetime={analysisBodyGone:!alive(analysisPage.targetId),analysisCaptionGone:!alive(analysisTitlebarPage.targetId),
        primaryBodyAlive:alive(page.targetId),primaryCaptionAlive:alive(titlebarPage.targetId)};
      report.titlebar.analysis.lifetimeCoupling=lifetime;
      assert.ok(lifetime.primaryBodyAlive && lifetime.primaryCaptionAlive,'TITLEBAR_PRIMARY_TARGET_LOST_ON_ANALYSIS_CLOSE');
      return lifetime.analysisBodyGone && lifetime.analysisCaptionGone;
    }, 10000);
  });
  await check('singleInstance', async () => {
    const second = boot(executable, config.installRoot, env, false);
    assert.deepEqual(await bounded(second.exited, 15000), { code: 0, signal: null });
    assert.ok(!primary.done);
    const { targetInfos } = await cdp.send('Target.getTargets');
    const pages = targetInfos.filter(target => target.type === 'page' && !titlebarTarget(target.url));
    assert.equal(pages.length, 1); assert.equal(pages[0].targetId, page.targetId);
    assert.equal(targetInfos.filter(target=>target.type==='page' && titlebarTarget(target.url)).length,1);
    await titlebarProbe.read();
    assert.equal(businessOrigin(pages[0].url), page.origin);
    assert.equal(await evaluate(`document.readyState === 'complete' && !!document.querySelector('#graphContainer')`), true);
  });
  let rememberedPresentation;
  if (!updateFixture) await check('installedPresentationUserResize', async () => {
    await cdp.send('Emulation.clearDeviceMetricsOverride', {}, page.sessionId);
    const initial = await nativeStartupWindow(config.installRoot, primary.child.pid);
    const scale = initial.scaleFactor, area = initial.workArea;
    const minimum = { width:Math.min(1280, area.width / scale), height:Math.min(720, area.height / scale) };
    // A deliberately free ratio verifies that a saved user size is not replaced
    // by the first-open 16:9 default. Small CI desktops still have a few spare DIP.
    const size = { width:Math.min(minimum.width + 80, area.width / scale),
      height:Math.min(minimum.height + 5, area.height / scale) };
    const resized = await nativeStartupWindow(config.installRoot, primary.child.pid,
      { action:'UserResize', widthDip:Math.round(size.width), heightDip:Math.round(size.height) });
    assert.deepEqual(resized.interaction, {source:'SendInput-native-resize-edge',userResizeObserved:true});
    assertNativeWindowBounds(resized, {...size,minWidth:minimum.width,minHeight:minimum.height});
    await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
    const handle = await evaluate(`(() => {
      const node = document.querySelector('#workspaceSidebarResize'), rect = node.getBoundingClientRect();
      const x = rect.x + rect.width / 2, y = rect.y + rect.height / 4;
      return {x,y,hit:document.elementFromPoint(x,y)===node,
        current:Number(node.getAttribute('aria-valuenow')),maximum:Number(node.getAttribute('aria-valuemax'))};
    })()`);
    assert.equal(handle.hit, true, 'PRESENTATION_SEPARATOR_REACHABLE');
    const right = handle.current + 20 <= handle.maximum;
    const delta = right ? 20 : -20, sidebarWidth = handle.current + delta;
    report.presentationMemory = { scope:'installed-EXE-native-window-edge-and-sidebar-pointer-drag; same-isolated-Windows-user',
      before:initial.electronWindow, changed:resized.electronWindow, scaleFactor:scale,
      changedSize:initial.electronWindow.width !== resized.electronWindow.width || initial.electronWindow.height !== resized.electronWindow.height,
      nativeUserResize:resized.interaction, expectedSidebarWidth:sidebarWidth };
    await cdp.send('Input.dispatchMouseEvent',
      {type:'mousePressed',x:handle.x,y:handle.y,button:'left',buttons:1,clickCount:1}, page.sessionId);
    await cdp.send('Input.dispatchMouseEvent',
      {type:'mouseMoved',x:handle.x+delta,y:handle.y,button:'left',buttons:1}, page.sessionId);
    await cdp.send('Input.dispatchMouseEvent',
      {type:'mouseReleased',x:handle.x+delta,y:handle.y,button:'left',buttons:0,clickCount:1}, page.sessionId);
    report.presentationMemory.sidebarReadback = await evaluate(`({width:document.querySelector('#left').getBoundingClientRect().width,
      saved:window.FundsDesktopPreferences.getItem('fund_sidebar_width')})`);
    await until(() => evaluate(`window.FundsDesktopPreferences.getItem('fund_sidebar_width') === '${sidebarWidth}'
      && Math.abs(document.querySelector('#left').getBoundingClientRect().width - ${sidebarWidth}) <= 1`), 5000);
    // Geometry is Main-owned; the renderer's public preference port cannot read it.
    assert.equal(await evaluate(`window.FundsDesktopPreferences.getItem('fund_window_state')`), null);
    rememberedPresentation = { bounds:Object.fromEntries(Object.entries(resized.electronWindow)
      .map(([key,value]) => [key, value / scale])), minimum, sidebarWidth };
    report.presentationMemory.windowStateRendererReadable = false;
  });
  await check('noPageExceptions', async () => {
    assert.ok(!instrumentationFailed); assert.equal(errorCount, 0);
  });
  if (updateFixture) report.updateCycle = await runInstalledUpdateJourney({
    fixture: updateFixture, primary, evaluate, clickElement, cdp, page, check, screenshot: config.screenshot,
  });
  await check('normalClose', async () => {
    // Close the actual native caption button when the CI desktop supports real
    // pointer input. A fallback local window.close targets the owning root.
    if (!primary.done) {
      report.titlebar.nativeClose = await nativeStartupWindow(config.installRoot,primary.child.pid,{action:'CaptionClose'});
      if (report.titlebar.nativeClose.status==='unavailable') {
        report.titlebar.nativeClose.fallback='window.close-on-owning-local-shell; not a native button click';
        await evaluate('window.close()',titlebarPage).catch(() => {});
      } else {
        assert.equal(report.titlebar.nativeClose.interaction.CloseButtonClicked,true);
        assert.equal(report.titlebar.nativeClose.interaction.WindowClosedObserved,true);
      }
    }
    assert.deepEqual(await bounded(primary.exited, 30000), { code: 0, signal: null });
    assert.equal(errorCount, 0);
  });
  await check('backendReleasedAfterClose', () => until(() => portClosed(Number(new URL(page.origin).port)), 15000));
  if (rememberedPresentation) {
    await check('installedPresentationRestoredAfterRelaunch', async () => {
      cdp.close();
      const restarted = boot(executable, config.installRoot, env, true);
      const debugUrl = await until(() => { assert.ok(!restarted.done); return restarted.debugUrl; }, 30000);
      cdp = await connect(debugUrl);
      cdp.onEvent(message => {
        if (message.method === 'Runtime.exceptionThrown' || message.method === 'Inspector.targetCrashed'
          || message.method === 'Target.targetCrashed'
          || message.method === 'Runtime.consoleAPICalled' && message.params?.type === 'error') errorCount += 1;
      });
      const targets = await until(async () => {
        assert.ok(!restarted.done);
        const {targetInfos} = await cdp.send('Target.getTargets');
        const body = targetInfos.find(value => value.type === 'page' && businessOrigin(value.url));
        const caption = targetInfos.find(value => value.type === 'page' && titlebarTarget(value.url));
        return body && caption ? {body,caption} : false;
      }, 90000);
      const attach = async target => {
        const {sessionId} = await cdp.send('Target.attachToTarget', {targetId:target.targetId,flatten:true});
        await cdp.send('Runtime.enable', {}, sessionId);
        await cdp.send('Page.enable', {}, sessionId);
        return {targetId:target.targetId,sessionId};
      };
      page = {...await attach(targets.body),origin:businessOrigin(targets.body.url)};
      titlebarPage = await attach(targets.caption);
      analysisPage = analysisTitlebarPage = null;
      await readyDocument(`!!document.querySelector('#graphContainer') && typeof state === 'object'
        && !state.graphLoading && state.graphInited`);
      const restored = await nativeStartupWindow(config.installRoot, restarted.child.pid);
      const expected = rememberedPresentation;
      assertNativeWindowBounds(restored, {...expected.bounds,minWidth:expected.minimum.width,minHeight:expected.minimum.height});
      for (const key of ['x','y']) assert.ok(Math.abs(restored.electronWindow[key] / restored.scaleFactor - expected.bounds[key]) <= 2,
        'PRESENTATION_NATIVE_POSITION_RESTORED');
      await until(() => evaluate(`window.FundsDesktopPreferences.getItem('fund_sidebar_width') === '${expected.sidebarWidth}'
        && Math.abs(document.querySelector('#left').getBoundingClientRect().width - ${expected.sidebarWidth}) <= 1`), 5000);
      const probe = await createInstalledTitlebarProbe({cdp,target:titlebarPage,evaluate,until});
      report.presentationMemory.restored = { native:restored.electronWindow,scaleFactor:restored.scaleFactor,
        sidebarWidth:await evaluate(`document.querySelector('#left').getBoundingClientRect().width`),
        caption:await probe.checkBody(page),sameUserRestart:true };
      await evaluate('window.close()', titlebarPage).catch(() => {});
      assert.deepEqual(await bounded(restarted.exited, 30000), {code:0,signal:null});
      assert.equal(errorCount, 0);
      await until(() => portClosed(Number(new URL(page.origin).port)), 15000);
    });
  }
  report.status = 'passed';
})().catch(async error => {
  report.status = 'failed'; report.failedCheck = stage; process.exitCode = 1;
  report.failure=safeInstalledFailure(error);
  if (/^(STARTUP|TITLEBAR)_[A-Z0-9_]{1,80}$/.test(error?.message || '')) {
    report.startupFailure = error.message;
    // These two projections contain only this helper's bounded numeric DOM
    // geometry / frame samples, never protocol errors, URLs or request data.
    if (error.startupLayout) report.startupLayoutFailure = error.startupLayout;
    if (error.startupObservation) report.startupObservationFailure = error.startupObservation;
    if (error.titlebarFailure) report.titlebarFailure = safeTitlebarFailure(error.titlebarFailure);
  }
  if (updateFixture) {
    let errorCode = 'UNAVAILABLE';
    // Read the published status only; do not inspect Main or request headers.
    try {
      const status = await evaluate('window.FundsDesktopUpdates.state()');
      if (FAILURE_CODES.includes(status?.errorCode) || status?.errorCode === 'UNKNOWN') errorCode = status.errorCode;
    } catch {}
    const local = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
    const cache = path.join(local, 'fundstrace-updater');
    report.updateFixtureDiagnostics = { ...updateFixture.requests, lastPhase: updateFixture.lastPhase || 'not-started', errorCode,
      cacheUnderOwnProfile: within(os.homedir(), cache),
      cacheDirectoryPresent: await fs.stat(cache).then(value => value.isDirectory(), () => false),
      cachePendingPresent: await fs.stat(path.join(cache, 'pending')).then(value => value.isDirectory(), () => false),
    };
  }
  const detail = safeJourneyFailure(error) || (['UPDATE_DIALOG_TIMEOUT','UPDATE_DIALOG_FAILED','UPDATE_JOURNEY_TIMEOUT'].includes(error?.message)
    ? { step: 'update', kind: error.message } : null);
  if (detail) report.failureDetail = detail;
  if (productionUpdate) {
    const codes = ['ERR_ASSERTION', 'ECONNRESET', 'ETIMEDOUT', 'ENOTFOUND', 'EAI_AGAIN', 'ENOENT',
      'ECONNREFUSED', 'CERT_HAS_EXPIRED', 'UNABLE_TO_VERIFY_LEAF_SIGNATURE'];
    const kinds = ['UPDATE_JOURNEY_TIMEOUT', 'UPDATE_DIALOG_TIMEOUT', 'UPDATE_DIALOG_FAILED'];
    report.productionFailure = { code: codes.includes(error?.code) ? error.code : 'UNKNOWN',
      kind: kinds.includes(error?.message) ? error.message : 'UNCLASSIFIED' };
    const nativeDialog = safeUpdateDialogFailure(error);
    if (nativeDialog) report.productionFailure.nativeDialog = nativeDialog;
    try {
      const value = await evaluate('window.FundsDesktopUpdates.state()');
      report.productionFailure.updatePhase = ['idle','checking','available','downloading','error','ready','installing','current'].includes(value?.phase) ? value.phase : 'unknown';
      report.productionFailure.updateErrorCode = FAILURE_CODES.includes(value?.errorCode) ? value.errorCode : 'UNKNOWN';
    } catch { /* The old renderer may already have exited normally. */ }
    const diagnosticBudget = maximum => Math.max(0, Math.min(maximum, 600000 - (Date.now() - smokeStartedAt)));
    try { report.productionFailure.ownerSnapshot = await safeUpdateOwnerSnapshot(productionOwner,
      { budgetMs: diagnosticBudget(8000) }); }
    catch { /* Passive observation must not replace the original failure. */ }
    try { report.productionFailure.electronMetadata = await safeInstalledElectronMetadataProbe(
      { installRoot: productionOwner?.installRoot, version: productionTransportConfig?.version,
        installerSha512: productionTransportConfig?.installerSha512, installerBytes: productionTransportConfig?.installerBytes },
      { budgetMs: diagnosticBudget(60000) }); }
    catch { /* The isolated probe is evidence only, never updater acceptance. */ }
    // A separate fixed-origin HTTPS observation runs only after the original
    // client check has failed. It cannot turn that failure into acceptance.
    // Reserve cleanup time inside the unchanged 720-second native child limit.
    try {
      report.productionFailure.transport = await safeProductionTransportDiagnostic(productionTransportConfig,
        { budgetMs: diagnosticBudget(285000) });
    }
    catch { /* Diagnostic failure must not replace the original failure. */ }
  }
}).finally(async () => {
  // Best effort graceful close, then only handles spawned above. No process-name
  // discovery, user-data removal, or termination of unrelated installations.
  if (cdp && owned.some(instance => !instance.done)) {
    await releaseStartupInspection();
    if (analysisTitlebarPage || analysisPage) await cdp.send('Page.close', {}, (analysisTitlebarPage||analysisPage).sessionId, 3000).catch(() => {});
    if (titlebarPage || page) await cdp.send('Page.close', {}, (titlebarPage||page).sessionId, 3000).catch(() => {});
    await Promise.all(owned.filter(instance => !instance.done).map(instance => bounded(instance.exited, 5000).catch(() => {})));
  }
  cdp?.close();
  if (updateFixture) await updateFixture.close();
  for (const instance of owned) {
    if (!instance.done) {
      instance.child.kill();
      await bounded(instance.exited, 10000).catch(() => { report.status = 'failed'; report.failedCheck = 'ownedProcessCleanup'; process.exitCode = 1; });
    }
  }
  if (presentationPreferences) {
    try {
      await restorePresentationPreferences(presentationPreferences);
      report.checks.isolatedPresentationPreferencesRestored = true;
    } catch {
      if (report.status !== 'failed') report.failedCheck = 'isolatedPresentationPreferencesCleanup';
      report.checks.isolatedPresentationPreferencesRestored = false;
      report.status = 'failed'; process.exitCode = 1;
    }
  }
  if (reportOwned) {
    report.mainProcessErrorMarkers={objectDestroyed:false,javascriptErrorInMainProcess:false,uncaughtException:false};
    for(const instance of owned) for(const [key,value] of Object.entries(instance.mainErrors.snapshot())) {
      report.mainProcessErrorMarkers[key] ||= value;
    }
    if(report.status==='passed' && Object.values(report.mainProcessErrorMarkers).some(Boolean)) {
      report.status='failed';report.failedCheck='noMainProcessErrors';process.exitCode=1;
    }
    try { await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8'); }
    catch { report.status = 'failed'; report.failedCheck = 'safeReport'; process.exitCode = 1; }
  }
  if (report.status === 'passed') process.stdout.write(`${JSON.stringify(report)}\n`);
  else process.stderr.write(`${report.failedCheck} failed.\n`);
}).catch(() => {
  process.stderr.write('ownedProcessCleanup failed.\n'); process.exitCode = 1;
});
