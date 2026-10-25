'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { readWindowState } = require('../preferences.cjs');
const { assertNativeMaximizedWindowBounds, assertNativeWindowBounds, assertNativeCaptionState,
  assertSavedPresentationWindow, expectedStartupBounds, assertNativeClientGeometry } = require('./installed-startup-journey.cjs');

function maximized(scale = 1, area = { x: 0, y: 0, width: 1024, height: 728 }) {
  const frame = 8 * scale;
  return { status:'passed', processBound:true, maximized:true, minimized:false, normalizationAvailable:false,
    scaleFactor:scale, workArea:{...area}, client:{...area},
    window:{x:area.x-frame,y:area.y-frame,width:area.width+2*frame,height:area.height+2*frame},
    minTrack:{width:1296*scale,height:728*scale}, electronWindow:null,electronMinTrack:null,frameInsets:null };
}
function restored(height = 725) {
  return { status:'passed',processBound:true,maximized:false,minimized:false,normalizationAvailable:true,
    window:{x:-8,y:0,width:1040,height:height+8},client:{x:0,y:0,width:1024,height},
    electronWindow:{x:0,y:0,width:1024,height},frameInsets:{left:8,top:0,right:8,bottom:8},
    minTrack:{width:1040,height:728},electronMinTrack:{width:1024,height:720},scaleFactor:1,
    workArea:{x:0,y:0,width:1024,height:728} };
}
const saved = (bounds, max = true) => JSON.stringify({bounds,maximized:max});
const normal = Object.freeze({x:0,y:0,width:1024,height:725});

test('maximized bounds use independent work area at several DPI levels and negative monitor coordinates', () => {
  for (const [scale,area] of [[1,{x:0,y:0,width:1024,height:728}],
    [1.5,{x:-2880,y:0,width:2880,height:1560}], [2,{x:-2560,y:-1440,width:2560,height:1400}]]) {
    const value=maximized(scale,area);
    assertNativeMaximizedWindowBounds(value);
    assertNativeClientGeometry(value,{width:area.width/scale,height:area.height/scale-32,dpr:scale});
    assert.equal(value.electronWindow,null);
    assert.equal(value.electronMinTrack,null);
  }
});

test('a false max flag, minimized state, wrong work area and fabricated normal geometry cannot pass', () => {
  for (const [change,code] of [
    [v=>v.maximized=false,/STARTUP_NATIVE_MAXIMIZED_STATE/],
    [v=>v.minimized=true,/STARTUP_NATIVE_MAXIMIZED_STATE/],
    [v=>delete v.maximized,/STARTUP_NATIVE_MAXIMIZED_STATE/],
    [v=>v.client.height-=8,/STARTUP_NATIVE_MAXIMIZED_WORK_AREA/],
    [v=>v.client.x+=8,/STARTUP_NATIVE_MAXIMIZED_WORK_AREA/],
    [v=>v.electronWindow={...v.client},/STARTUP_NATIVE_MAXIMIZED_NORMALIZATION/],
    [v=>v.electronMinTrack={width:1280,height:720},/STARTUP_NATIVE_MAXIMIZED_NORMALIZATION/],
  ]) { const value=maximized();change(value);assert.throws(()=>assertNativeMaximizedWindowBounds(value),code); }
});

test('maximized startup freezes normal bounds in DIP and clamps their position to a negative-coordinate work area', () => {
  const native={scaleFactor:1.5,workArea:{x:-2880,y:-150,width:2880,height:1560},
    get electronWindow(){assert.fail('actual maximized dimensions used as expectation');}};
  assert.deepEqual(expectedStartupBounds(native,saved({x:-4000,y:-500,width:1500,height:900})),
    {source:'saved',width:1500,height:900,minWidth:1280,minHeight:720,maximized:true,x:-1920,y:-100});
  assert.deepEqual(expectedStartupBounds(native,saved({x:500,y:500,width:1500,height:900})),
    {source:'saved',width:1500,height:900,minWidth:1280,minHeight:720,maximized:true,x:-1500,y:40});
});

test('persisted maximize state must retain every frozen normal coordinate and dimension', () => {
  assertSavedPresentationWindow(saved(normal),normal,true);
  assertSavedPresentationWindow(saved(normal,false),normal,false);
  assert.throws(()=>assertSavedPresentationWindow(saved(normal,false),normal,true),/PRESENTATION_SAVED_MAXIMIZED/);
  for (const key of ['x','y','width','height']) {
    const changed={...normal,[key]:normal[key]+5};
    assert.throws(()=>assertSavedPresentationWindow(saved(changed),normal,true),/PRESENTATION_SAVED_NORMAL_BOUNDS/);
  }
  for (const value of [null,'bad','{}',saved(normal,'true')])
    assert.throws(()=>assertSavedPresentationWindow(value,normal,true),/PRESENTATION_SAVED_WINDOW_INVALID/);
});

test('single caption state changes require actual successful input and the requested before/after states', () => {
  const make = maximize => ({...(maximize?maximized():restored()),interaction:{
    InputSource:'SendInput-on-bound-visible-caption',PointerInputInjected:true,
    MaximizeButtonClicked:maximize,MaximizedObserved:maximize,RestoreButtonClicked:!maximize,RestoredObserved:!maximize,
    Before:{Maximized:!maximize,Minimized:false},After:{Maximized:maximize,Minimized:false} }});
  for (const max of [true,false]) {
    assertNativeCaptionState(make(max),max);
    for (const mutate of [v=>v.status='unavailable',v=>v.interaction.PointerInputInjected=false,
      v=>v.interaction.Before.Maximized=max,v=>v.interaction.After.Maximized=!max,
      v=>v.interaction.After.Minimized=true,v=>v.maximized=!max,
      v=>v.interaction[max?'MaximizeButtonClicked':'RestoreButtonClicked']=false]) {
      const value=make(max);mutate(value);
      assert.throws(()=>assertNativeCaptionState(value,max),/STARTUP_NATIVE_CAPTION_STATE/);
    }
  }
});

const appSource = fs.readFileSync(path.join(__dirname,'installed-app-smoke.cjs'),'utf8');
test('the actual native wrapper never accepts unavailable maximize or restore input', async () => {
  const start=appSource.indexOf('async function nativeStartupWindow('),end=appSource.indexOf('\nasync function nativeStartupBounds(',start);
  assert.ok(start>=0&&end>start);
  const native=vm.runInNewContext(`(${appSource.slice(start,end)})`,{
    assert,path,__dirname,report:{},execFile(){},
    promisify:()=>async()=>({stdout:JSON.stringify({schemaVersion:1,processBound:true,status:'unavailable',
      failedCheck:'nativeCaptionInteraction',reason:'INPUT_DESKTOP_UNAVAILABLE'})}),
  });
  for (const action of ['CaptionMaximize','CaptionRestore'])
    await assert.rejects(native('C:/synthetic-install',123,{action}));
});

function startupFlow(overrides={}) {
  const start=appSource.indexOf('async function nativeStartupBounds('),end=appSource.indexOf('\nfunction readWorkspaceTools()',start);
  assert.ok(start>=0&&end>start);
  const actions=[],report={startup:{}},captionChecks=[];
  let current=maximized();
  const context={assert,report,presentationPreferences:{saved:{fund_window_state:saved(normal)}},
    assertNativeMaximizedWindowBounds,assertNativeWindowBounds,assertNativeCaptionState,expectedStartupBounds,assertNativeClientGeometry,
    titlebarProbe:{checkBody:async(target,native)=>captionChecks.push(native.maximized)},
    nativeStartupWindow:async(root,pid,options={})=>{
      assert.equal(root,'C:/synthetic-install');assert.equal(pid,123);
      actions.push(options.action||'Inspect');
      if(options.action==='CaptionRestore') current={...restored(),interaction:{
        InputSource:'SendInput-on-bound-visible-caption',PointerInputInjected:true,RestoreButtonClicked:true,RestoredObserved:true,
        Before:{Maximized:true,Minimized:false},After:{Maximized:false,Minimized:false}}};
      if(options.action==='Resize') current=restored(options.heightDip);
      if(overrides.native) return overrides.native(structuredClone(current),options);
      return structuredClone(current);
    },evaluate:async expression=>expression.startsWith('new Promise')?undefined:
      {width:current.client.width,height:current.client.height-32,dpr:1}};
  const run=vm.runInNewContext(`(${appSource.slice(start,end)})`,context);
  return {run:()=>run({installRoot:'C:/synthetic-install'},{child:{pid:123}},{}),actions,report,captionChecks};
}

test('the actual startup consumer restores maximized state before its existing normal resize checks', async () => {
  const flow=startupFlow();await flow.run();
  assert.deepEqual(flow.actions,['Inspect','CaptionRestore','Resize','Resize']);
  assert.deepEqual(flow.captionChecks,[true,false,false,false]);
  assert.equal(flow.report.startup.nativeWindows.step,'completed');
});

test('the actual startup consumer rejects unavailable restore, wrong saved size and wrong restored position', async () => {
  for (const change of [v=>v.status='unavailable',v=>{v.client.height=720;v.electronWindow.height=720;v.window.height=728;},
    v=>{v.client.x=5;v.electronWindow.x=5;v.window.x=-3;}]) {
    const flow=startupFlow({native:(value,options)=>{if(options.action==='CaptionRestore')change(value);return value;}});
    await assert.rejects(flow.run());
    assert.deepEqual(flow.actions,['Inspect','CaptionRestore']);
  }
});

test('the maximized lifecycle cannot count an earlier clean process exit as a native close', async () => {
  const prefix = "await check('normalClose', ";
  const start = appSource.lastIndexOf(prefix), end = appSource.indexOf("\n  await check('backendReleasedAfterClose'", start);
  assert.ok(start >= 0 && end > start);
  const action = appSource.slice(start + prefix.length, end).trim().replace(/\);$/, '');
  const run = rememberedPresentation => vm.runInNewContext(`
    const primary = {done:true,exited:Promise.resolve({code:0,signal:null})};
    (${action})()
  `, { assert, rememberedPresentation, errorCount:0, bounded:async value => value,
    nativeStartupWindow:() => assert.fail('already closed process cannot receive input') });
  await assert.rejects(run({savedNormalBounds:normal}), /PRESENTATION_MAXIMIZED_NATIVE_CLOSE_REQUIRED/);
  await run(null); // The private updater may legitimately have exited already.
});

test('the persisted normal-bounds read uses the already bound preference snapshot path', async () => {
  const prefix = 'const savedNormal = await until(';
  const start = appSource.indexOf(prefix), end = appSource.indexOf('},5000);', start);
  assert.ok(start >= 0 && end > start);
  const callback = appSource.slice(start + prefix.length, end + 1);
  const read = vm.runInNewContext(`(${callback})`, { path, readWindowState,
    presentationPreferences:{file:path.join('/synthetic-owned-user','preferences.json')},
    rememberedPresentation:{bounds:normal},
    readPresentationPreferences:async directory => {
      assert.equal(directory,'/synthetic-owned-user');
      return {saved:{fund_window_state:saved(normal,false)}};
    },
  });
  assert.deepEqual(await read(), {bounds:normal,maximized:false});
});
