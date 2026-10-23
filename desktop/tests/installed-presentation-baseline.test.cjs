'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createHash } = require('node:crypto');
const { preferenceStore } = require('../preferences.cjs');
const { baselineConfig, runInstalledPresentationBaseline } = require('./installed-presentation-baseline.cjs');

const preferences = { fund_case:'23', fund_edge_type:'polyline', fund_show_ids:'1',
  fund_show_photos:'0', fund_edge_labels:'["money","time","summary","currency"]' };
const hash = bytes => createHash('sha256').update(bytes).digest('hex');

function smokeArguments(extra) {
  const source=fs.readFileSync(path.join(__dirname,'installed-app-smoke.cjs'),'utf8');
  const start=source.indexOf('function argumentsOf() {'), end=source.indexOf('\nfunction within(',start);
  assert.ok(start>=0 && end>start);
  const root=path.resolve('synthetic-installed'), report=path.resolve('synthetic-report.json');
  return vm.runInNewContext(`${source.slice(start,end)}\nargumentsOf()`,{
    assert,path,process:{argv:['node','installed-app-smoke.cjs',root,'--report',report,...extra]},
  });
}

test('the baseline CLI is isolated while preserving all existing smoke modes',()=>{
  assert.equal(smokeArguments([]).presentationBaseline,undefined);
  assert.equal(smokeArguments(['--startup-close']).startupClose,true);
  for(const [flag,key] of [['--update-fixture','updateFixture'],['--production-update','productionUpdate'],
    ['--presentation-baseline','presentationBaseline']]) {
    const file=path.resolve('synthetic-config.json');
    assert.equal(smokeArguments([flag,file])[key],file);
  }
  const file=path.resolve('synthetic-config.json'), screenshot=path.resolve('synthetic.png');
  assert.equal(smokeArguments(['--presentation-baseline',file,'--screenshot',screenshot]).screenshot,screenshot);
});

for(const other of ['--update-fixture','--production-update','--startup-close'])
  test(`the baseline CLI rejects combination with ${other}`,()=>{
    const file=path.resolve('synthetic-config.json');
    assert.throws(()=>smokeArguments(['--presentation-baseline',file,...(other==='--startup-close'?[other]:[other,file])]));
  });

function nativeBounds({ x=240, y=115, width=1440, height=810, scale=1 } = {}) {
  const rect = { x:Math.round(x*scale), y:Math.round(y*scale),
    width:Math.round(width*scale), height:Math.round(height*scale) };
  return { schemaVersion:1, status:'passed', processBound:true, normalizationAvailable:true,
    scaleFactor:scale, electronWindow:rect,
    window:{x:rect.x-8,y:rect.y,width:rect.width+16,height:rect.height+8},
    client:{x:0,y:0,width:rect.width,height:rect.height},
    frameInsets:{left:8,top:0,right:8,bottom:8},
    minTrack:{width:1280*scale+16,height:720*scale+8},
    electronMinTrack:{width:1280*scale,height:720*scale},
    workArea:{x:0,y:0,width:1920*scale,height:1040*scale} };
}

function fixture(t, options={}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'fundstrace-presentation-baseline-'));
  t.after(() => fs.rmSync(directory, {recursive:true,force:true}));
  const file = path.join(directory, 'preferences.json');
  const write = value => fs.writeFileSync(file, `${JSON.stringify(value)}\n`);
  write({...preferences});
  const original = fs.readFileSync(file), store = preferenceStore(directory);
  const calls=[], completed=[], primary={done:false,exited:Promise.resolve(options.exit || {code:0,signal:null})};
  let started=false, resized;
  const context={file,write,store,read:()=>JSON.parse(fs.readFileSync(file,'utf8'))};
  const input = {
    config:{version:'0.1.10',preferences:{...preferences}}, primary,
    ready:async () => { calls.push('startup-completed'); started=true; },
    evaluate:async expression => {
      if (expression === 'window.FundsDesktopUpdates.state()') return {currentVersion:options.version || '0.1.10'};
      const match = /^window\.FundsDesktopPreferences\.getItem\(("[a-z_]+")\)$/.exec(expression);
      assert.ok(match);
      const key = JSON.parse(match[1]);
      if (key === 'fund_window_state') return options.rendererWindowState ?? null;
      return store.getItem(key);
    },
    nativeWindow:async action => {
      assert.equal(started,true,'native input must follow the completed old startup');
      if (!action) {
        calls.push('inspect');
        const value=nativeBounds({scale:options.scale || 1}); options.inspect?.(value); return value;
      }
      calls.push(action.action);
      if (action.action === 'UserResize') {
        resized=nativeBounds({x:0,y:0,width:action.widthDip,height:action.heightDip,scale:options.scale || 1});
        const bounds=Object.fromEntries(Object.entries(resized.electronWindow).map(([key,value])=>[key,Math.round(value/resized.scaleFactor)]));
        // The observation helper has no write callback. This stand-in for the
        // installed Main uses the actual preference store's atomic persistence.
        assert.equal(store.setItem('fund_window_state',JSON.stringify({bounds,maximized:false})),true);
        resized.interaction={source:'SendInput-native-resize-edge',userResizeObserved:true};
        options.resize?.(resized,context); return resized;
      }
      assert.equal(action.action,'CaptionClose');
      options.close?.(context);
      primary.done=true;
      return {status:'passed',processBound:true,interaction:{CloseButtonClicked:true,WindowClosedObserved:true},
        ...options.nativeClose};
    },
    readPreferences:async () => { calls.push('read'); return fs.readFileSync(file); },
    until:async (inspect,timeout) => {
      assert.ok([5000,15000].includes(timeout));
      for(let i=0;i<3;i++) { const value=await inspect(); if(value)return value; }
      throw new Error('BOUNDED_WAIT_FAILED');
    },
    bounded:async (promise,timeout) => { assert.equal(timeout,30000);assert.equal(primary.done,true);return promise; },
    backendReleased:async () => {calls.push('backend-port');return options.backendReleased ?? true;},
    check:async (name,action) => {const value=await action();completed.push(name);return value;},
  };
  options.before?.(context);
  return {input,calls,completed,file,original,context,run:()=>runInstalledPresentationBaseline(input)};
}

for(const scale of [1,1.25,1.5]) test(`prior UI establishes the full byte baseline through native input at scale ${scale}`,async t=>{
  const h=fixture(t,{scale});
  const result=await h.run(), bytes=fs.readFileSync(h.file), values=JSON.parse(bytes);
  // This is the old failing oracle: a genuine Main window-state save changes
  // the five-key seed's whole-file digest. The new baseline includes that key.
  assert.notEqual(hash(bytes),hash(h.original));
  assert.equal(result.originalSha256,hash(h.original));
  assert.equal(result.baselineSha256,hash(bytes));
  assert.deepEqual(Object.keys(values).sort(),[...Object.keys(preferences),'fund_window_state'].sort());
  for(const [key,value] of Object.entries(preferences))assert.equal(values[key],value);
  assert.deepEqual(h.calls.filter(value=>value!=='read'),['startup-completed','inspect','UserResize','CaptionClose','backend-port']);
  assert.equal(result.windowStateRendererReadable,false);
  for(const [key,value] of Object.entries(result)) if(typeof value==='boolean'&&key!=='windowStateRendererReadable')assert.equal(value,true);
  assert.equal(h.completed.at(-1),'priorPresentationBaselineBytes');
});

test('the prior client Main-only window key is rejected as a renderer expectation',()=>{
  assert.throws(()=>baselineConfig({version:'0.1.10',preferences:{...preferences,fund_window_state:'{}'}}));
});

for(const key of Object.keys(preferences)) test(`baseline rejects changed original ${key}`,async t=>{
  const h=fixture(t,{resize:(_native,context)=>context.write({...context.read(),[key]:'changed'})});
  await assert.rejects(h.run());
  assert.equal(h.calls.includes('CaptionClose'),false);
});

for(const [name,options] of [
  ['wrong prior version',{version:'0.1.11'}],
  ['Main window state exposed through renderer IPC',{rendererWindowState:'private-window-state'}],
  ['unbound initial HWND',{inspect:value=>{value.processBound=false;}}],
  ['unavailable native normalization',{inspect:value=>{value.normalizationAvailable=false;}}],
  ['no native resize space',{inspect:value=>{value.workArea.width=1280;value.workArea.height=720;
    value.electronWindow.width=1280;value.electronWindow.height=720;}}],
  ['unbound resized HWND',{resize:value=>{value.processBound=false;}}],
  ['native input unavailable',{resize:value=>{value.status='unavailable';}}],
  ['resize flag absent',{resize:value=>{value.interaction.userResizeObserved=false;}}],
  ['actual client geometry unchanged',{resize:value=>{value.client.width=1440;value.client.height=810;}}],
  ['DPI changed during native input',{resize:value=>{value.scaleFactor=2;}}],
  ['unknown preference key',{resize:(_value,context)=>context.write({...context.read(),fund_unreviewed:'1'})}],
  ['missing original preference',{resize:(_value,context)=>{const next=context.read();delete next.fund_show_photos;context.write(next);}}],
  ['invalid saved window state',{resize:(_value,context)=>context.write({...context.read(),fund_window_state:'{}'})}],
  ['saved window mismatches HWND',{resize:(_value,context)=>{const next=context.read(),state=JSON.parse(next.fund_window_state);
    state.bounds.width+=10;context.write({...next,fund_window_state:JSON.stringify(state)});}}],
  ['saved position mismatches HWND',{resize:(_value,context)=>{const next=context.read(),state=JSON.parse(next.fund_window_state);
    state.bounds.x+=10;context.write({...next,fund_window_state:JSON.stringify(state)});}}],
  ['a maximized state after restored user resize',{resize:(_value,context)=>{const next=context.read(),state=JSON.parse(next.fund_window_state);
    state.maximized=true;context.write({...next,fund_window_state:JSON.stringify(state)});}}],
]) test(`prior baseline rejects ${name}`,async t=>{
  const h=fixture(t,options);await assert.rejects(h.run());
  assert.equal(h.calls.includes('CaptionClose'),false);
  assert.equal(h.completed.includes('priorPresentationBaselineBytes'),false);
});

for(const [name,options] of [
  ['unavailable system close',{nativeClose:{status:'unavailable'}}],
  ['unbound system close',{nativeClose:{processBound:false}}],
  ['system X not clicked',{nativeClose:{interaction:{CloseButtonClicked:false,WindowClosedObserved:true}}}],
  ['window did not close',{nativeClose:{interaction:{CloseButtonClicked:true,WindowClosedObserved:false}}}],
  ['nonzero process exit',{exit:{code:1,signal:null}}],
  ['a forced termination',{exit:{code:null,signal:'SIGTERM'}}],
  ['backend port remained open',{backendReleased:false}],
  ['preference key order changed on close',{close:context=>context.write(Object.fromEntries(Object.entries(context.read()).reverse()))}],
  ['original preference changed on close',{close:context=>context.write({...context.read(),fund_show_ids:'0'})}],
  ['window state removed on close',{close:context=>{const next=context.read();delete next.fund_window_state;context.write(next);}}],
]) test(`no complete baseline is returned when ${name}`,async t=>{
  const h=fixture(t,options);await assert.rejects(h.run());
  assert.equal(h.completed.includes('priorPresentationBaselineBytes'),false);
});

test('unexpected initial Main state cannot be adopted as the old UI baseline',async t=>{
  const h=fixture(t,{before:context=>context.write({...context.read(),fund_window_state:JSON.stringify({
    bounds:{x:0,y:0,width:1280,height:720},maximized:false})})});
  await assert.rejects(h.run());
  assert.equal(h.calls.includes('UserResize'),false);
});
