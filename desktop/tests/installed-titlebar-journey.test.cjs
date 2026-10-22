'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {titlebarTarget,assertCaption,assertCaptionBody,READ_CAPTION,readinessFailure,
  createInstalledTitlebarProbe,safeCaptionProjection,safeTitlebarFailure,safeProtocolMethod,protocolError,
  safeInstalledFailure,createMainErrorMarkerProbe} = require('./installed-titlebar-journey.cjs');

function caption() {
  return {time:1000,timeOrigin:9000,viewport:{width:1280,height:720,dpr:1},
    document:{readyState:'complete',visibilityState:'visible',secureContext:true,standardsMode:true,fontStatus:'loaded'},
    dom:{bar:true,content:true,name:true,icon:true,menu:true},
    bridge:{type:'object',openMenu:'function',onMenuEnabled:'function'},
    overlay:{supported:true,visible:true,rectMethod:'function',areaReadFailed:false,area:{x:0,y:0,width:1142,height:32}},
    bar:{x:0,y:0,width:1280,height:32},content:{x:0,y:0,width:1142,height:32},
    icon:{x:8,y:7,width:18,height:18},name:{x:33,y:7,width:108,height:18},
    menu:{x:150,y:3,width:28,height:26},text:'循迹 FundsTrace',opacity:1,visibility:'visible',
    fontSize:13,fontWeight:'400',color:'rgb(213, 222, 232)',drag:'drag',menuDrag:'no-drag',
    menuDisabled:true,iconLoaded:true,stylesheetLoaded:true,
    image:{complete:true,naturalWidth:1024,naturalHeight:1024,decode:'decoded'}};
}

test('permanent caption classification is separate from startup, business and untrusted file paths',()=>{
  assert.equal(titlebarTarget('file:///C:/SYN/FundsTrace/resources/app.asar/titlebar/index.html'),true);
  assert.equal(titlebarTarget('file:///D:/SYN%20%E8%B7%AF%E5%BE%84/FundsTrace/resources/app.asar/titlebar/index.html'),true);
  for(const value of ['about:blank','http://127.0.0.1:1234/','file:///C:/SYN/resources/app.asar/startup/index.html',
    'file://server/C:/resources/app.asar/titlebar/index.html','file:///C:/SYN/resources/../resources/app.asar/titlebar/index.html',
    'file:///C:/SYN/resources/app.asar/titlebar/index.html?x','file:///C:/SYN/resources/app.asar/titlebar/index.html#x']) {
    assert.equal(titlebarTarget(value),false);
  }
});

test('the fixed caption fits its real native overlay safe area and remains separate from the 32 DIP body inset',()=>{
  const value=caption();assertCaption(value);
  assertCaptionBody(value,{width:1280,height:688,dpr:1},{client:{width:1280,height:720},scaleFactor:1});
  const scaled=caption();scaled.viewport.dpr=1.5;
  assertCaptionBody(scaled,{width:1280,height:688,dpr:1.5},{client:{width:1920,height:1080},scaleFactor:1.5});
});

test('caption checks reject hidden overlay, wrong height, faded branding and native-button overlap',()=>{
  for(const [mutate,expected] of [
    [v=>{v.overlay.visible=false;},/TITLEBAR_NATIVE_OVERLAY_MISSING/],
    [v=>{v.bar.height=26;},/TITLEBAR_FIXED_CAPTION/],
    [v=>{v.opacity=.5;},/TITLEBAR_PARTICIPATED_IN_FADE/],
    [v=>{v.menu.x=1140;},/TITLEBAR_CONTENT_OVERLAPS_NATIVE_CONTROLS/],
    [v=>{v.overlay.area.width=1280;},/TITLEBAR_NATIVE_CONTROLS_SAFE_AREA/],
    [v=>{v.menuDrag='drag';},/TITLEBAR_MENU_DRAG_REGION/],
    [v=>{v.iconLoaded=false;},/TITLEBAR_RESOURCES_MISSING/],
  ]){const value=caption();mutate(value);assert.throws(()=>assertCaption(value),expected);}
});

test('persistent caption permits readiness menu enabling but rejects zoom, replacement and layout changes',()=>{
  const before=caption(),ready=caption();ready.menuDisabled=false;assertCaption(ready,before);
  for(const [mutate,expected] of [
    [v=>{v.timeOrigin++;},/TITLEBAR_DOCUMENT_REPLACED/],
    [v=>{v.viewport.dpr=1.2;},/TITLEBAR_ZOOM_CHANGED/],
    [v=>{v.fontWeight='500';},/TITLEBAR_STYLE_CHANGED/],
    [v=>{v.name.x+=5;},/TITLEBAR_BRAND_GEOMETRY_CHANGED/],
  ]){const value=caption();mutate(value);assert.throws(()=>assertCaption(value,before),expected);}
});

test('body geometry rejects a second menu inset, overlap, emulated DPR and a mismatched native client',()=>{
  const value=caption();
  assert.throws(()=>assertCaptionBody(value,{width:1280,height:662,dpr:1}),/TITLEBAR_BODY_VIEWPORT/);
  assert.throws(()=>assertCaptionBody(value,{width:1280,height:720,dpr:1}),/TITLEBAR_BODY_VIEWPORT/);
  assert.throws(()=>assertCaptionBody(value,{width:1280,height:688,dpr:2}),/TITLEBAR_BODY_DPR/);
  assert.throws(()=>assertCaptionBody(value,{width:1280,height:688,dpr:1},
    {client:{width:1280,height:746},scaleFactor:1}),/TITLEBAR_NATIVE_CLIENT/);
});

test('caption/body layout uses actual cleared viewports without an unsupported browser window ID command',async()=>{
  const calls=[];
  const unsupported=['Browser','getWindowForTarget'].join('.');
  const cdp={send:async(method,params,sessionId)=>{assert.notEqual(method,unsupported);calls.push({method,params,sessionId});return {};}};
  const probe=await createInstalledTitlebarProbe({cdp,target:{targetId:'caption',sessionId:'caption-session'},
    evaluate:async expression=>expression===READ_CAPTION ? caption() : expression==='({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})'
      ? {width:1280,height:688,dpr:1} : true,
    until:async check=>{assert.equal(await check(),true);}});
  const result=await probe.checkBody({targetId:'body',sessionId:'body-session'},
    {client:{width:1280,height:720},scaleFactor:1});
  assert.equal(result.sameCaptionDocument,true);
  assert.equal(result.bodyInsetDip,32);
  assert.equal(result.layoutMatches,true);
  assert.equal(result.nativeClientMatched,true);
  assert.equal(Object.hasOwn(result,'sameNativeWindow'),false);
  assert.equal(Object.hasOwn(result,'nativeWindowBinding'),false);
  assert.deepEqual(calls,[
    {method:'Emulation.clearDeviceMetricsOverride',params:{},sessionId:'caption-session'},
    {method:'Emulation.clearDeviceMetricsOverride',params:{},sessionId:'body-session'},
  ]);
  for(const filename of ['installed-titlebar-journey.cjs','installed-app-smoke.cjs']) {
    assert.ok(!(await fs.readFile(path.join(__dirname,filename),'utf8')).includes(unsupported));
  }
  await assert.rejects(probe.checkBody(probe.target),/TITLEBAR_BODY_MUST_BE_SEPARATE_TARGET/);
});

function captionDocument({overlay=true,stylesheet=true,complete=true,naturalWidth=1024}={}) {
  const sample=caption(),selectors=new Map();
  for(const [selector,key] of [['.titlebar','bar'],['.titlebar-content','content'],['.titlebar-name','name'],
    ['.titlebar-icon','icon'],['#titlebar-menu','menu']]) {
    selectors.set(selector,{getBoundingClientRect:()=>sample[key],textContent:'循迹 FundsTrace',disabled:true,
      styleKey:key,complete,naturalWidth,naturalHeight:naturalWidth,decode:()=>Promise.resolve()});
  }
  // Any attempt to inspect a URL or resource location fails the real DOM probe.
  for(const element of selectors.values()) for(const key of ['src','currentSrc','href']) {
    Object.defineProperty(element,key,{get(){throw new Error('SYN_RESOURCE_URL_MUST_NOT_BE_READ');}});
  }
  selectors.set('link[rel=stylesheet]',{sheet:stylesheet?{}:null});
  const document={readyState:'complete',visibilityState:'visible',compatMode:'CSS1Compat',fonts:{status:'loaded'},
    querySelector:selector=>selectors.get(selector)||null};
  Object.defineProperty(document,'URL',{get(){throw new Error('SYN_DOCUMENT_URL_MUST_NOT_BE_READ');}});
  const context=vm.createContext({document,window:{FundsTraceTitlebar:{openMenu(){},onMenuEnabled(){}}},
    navigator:overlay?{windowControlsOverlay:{visible:true,getTitlebarAreaRect:()=>sample.overlay.area}}:{},
    innerWidth:1280,innerHeight:720,devicePixelRatio:1,isSecureContext:true,
    performance:{now:()=>1000,timeOrigin:9000},getComputedStyle:element=>({opacity:'1',visibility:'visible',fontSize:'13px',
      fontWeight:'400',color:'rgb(213, 222, 232)',getPropertyValue:()=>element.styleKey==='menu'?'no-drag':'drag'})});
  return ()=>vm.runInContext(READ_CAPTION,context);
}

test('the real DOM projection still reports fixed geometry when the overlay API is missing',()=>{
  const value=captionDocument({overlay:false})();
  assert.ok(value);
  assert.equal(value.bar.height,32);
  assert.equal(value.overlay.supported,false);
  assert.equal(value.document.secureContext,true);
  assert.equal(value.bridge.openMenu,'function');
  assert.equal(readinessFailure(value),'TITLEBAR_OVERLAY_API_UNAVAILABLE');
  assert.throws(()=>assertCaption(value),/TITLEBAR_NATIVE_OVERLAY_MISSING/);
});

test('stylesheet, pending image, failed image and hidden overlay failures have different fixed stages',()=>{
  assert.equal(readinessFailure(captionDocument({stylesheet:false})()),'TITLEBAR_STYLESHEET_NOT_LOADED');
  assert.equal(readinessFailure(captionDocument({complete:false,naturalWidth:0})()),'TITLEBAR_ICON_NOT_LOADED');
  assert.equal(readinessFailure(captionDocument({complete:true,naturalWidth:0})()),'TITLEBAR_ICON_DECODE_FAILED');
  const hidden=caption();hidden.overlay.visible=false;
  assert.equal(readinessFailure(hidden),'TITLEBAR_OVERLAY_NOT_VISIBLE');
  hidden.overlay.visible=true;hidden.overlay.areaReadFailed=true;hidden.overlay.area=null;
  assert.equal(readinessFailure(hidden),'TITLEBAR_OVERLAY_RECT_UNAVAILABLE');
});

test('image decoding is observed asynchronously without delaying or fabricating readiness',async()=>{
  const read=captionDocument();
  assert.equal(read().image.decode,'pending');
  await new Promise(resolve=>setImmediate(resolve));
  const value=read();
  assert.equal(value.image.decode,'decoded');
  assert.equal(value.iconLoaded,true);
});

test('a timed-out readiness probe preserves the 15 second gate and captures only the local caption strip',async()=>{
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'fundstrace-caption-probe-test-'));
  const screenshotPath=path.join(directory,'native-installed-app.startup-caption-failure.png'),calls=[];
  const value=captionDocument({overlay:false})();
  const png=Buffer.from([137,80,78,71,13,10,26,10,0,0,0,0]);
  let observed;
  try {
    await assert.rejects(createInstalledTitlebarProbe({target:{targetId:'caption',sessionId:'caption-session'},
      evaluate:async()=>value,
      until:async(check,ms)=>{assert.equal(ms,15000);assert.equal(await check(),false);assert.equal(await check(),false);throw new Error('SYN_TIMEOUT_PRIVATE_URL');},
      cdp:{send:async(method,params,sessionId,timeout)=>{calls.push({method,params,sessionId,timeout});return {data:png.toString('base64')};}},
      failureScreenshotPath:screenshotPath}),error=>{
      observed=error;
      assert.equal(error.message,'TITLEBAR_OVERLAY_API_UNAVAILABLE');
      assert.equal(error.titlebarFailure.attempts,2);
      assert.equal(error.titlebarFailure.readFailures,0);
      assert.equal(error.titlebarFailure.observation.bar.height,32);
      assert.equal(error.titlebarFailure.screenshot.captured,true);
      return true;
    });
    assert.deepEqual(calls,[{method:'Page.captureScreenshot',params:{format:'png',fromSurface:true,captureBeyondViewport:false,
      clip:{x:0,y:0,width:1280,height:32,scale:1}},sessionId:'caption-session',timeout:3000}]);
    assert.deepEqual(await fs.readFile(screenshotPath),png);
    assert.ok(!JSON.stringify(observed.titlebarFailure).includes('SYN_TIMEOUT_PRIVATE_URL'));
  } finally {await fs.rm(directory,{recursive:true,force:true});}
});

test('failure projections discard unexpected strings, extra keys and protocol exception text',async()=>{
  const value=caption();
  value.overlay.supported=false;value.overlay.visible=false;
  value.headers={'X-FundsTrace-Desktop':'SYN_PRIVATE_TOKEN'};value.url='https://SYN_PRIVATE_URL';
  value.text='SYN_PRIVATE_TOKEN';value.color='url(https://SYN_PRIVATE_URL)';value.fontWeight='SYN_PRIVATE_TOKEN';
  value.bridge.type='SYN_PRIVATE_TOKEN';
  const safe=safeTitlebarFailure({stage:'TITLEBAR_OVERLAY_API_UNAVAILABLE',attempts:2,readFailures:0,observation:value});
  assert.ok(!JSON.stringify(safe).includes('SYN_PRIVATE'));
  assert.ok(!JSON.stringify(safeCaptionProjection(value)).includes('headers'));
  assert.deepEqual(safeTitlebarFailure(safe),safe,'the installed smoke can sanitize the projection again');
  await assert.rejects(createInstalledTitlebarProbe({cdp:{},target:{targetId:'caption',sessionId:'caption-session'},
    evaluate:async()=>{throw new Error('https://SYN_PRIVATE_URL/X-FundsTrace-Desktop/SYN_PRIVATE_TOKEN');},
    until:async check=>{await check();throw new Error('SYN_PRIVATE_TOKEN');}}),error=>{
    assert.equal(error.message,'TITLEBAR_DOM_READ_UNAVAILABLE');
    assert.equal(error.titlebarFailure.readFailures,1);
    assert.equal(error.titlebarFailure.observation,null);
    assert.ok(!JSON.stringify(error.titlebarFailure).includes('SYN_PRIVATE'));
    return true;
  });
});

test('every creation substage keeps a fixed failure code, last safe DOM and bounded protocol category',async()=>{
  for(const scenario of [
    {fail:'initial-projection',code:'TITLEBAR_INITIAL_PROJECTION_FAILED',method:'Runtime.evaluate',category:'protocol-command'},
    {fail:'initial-validation',code:'TITLEBAR_FIXED_CAPTION',method:'none',category:'assertion'},
    {fail:'observation-installation',code:'TITLEBAR_OBSERVATION_INSTALL_FAILED',method:'Runtime.evaluate',category:'protocol-timeout'},
  ]) {
    let calls=0;
    await assert.rejects(createInstalledTitlebarProbe({target:{targetId:'caption',sessionId:'caption-session'},
      evaluate:async()=>{
        calls++;
        if(scenario.fail==='initial-projection' && calls===2) throw protocolError('CDP_COMMAND','Runtime.evaluate');
        if(scenario.fail==='observation-installation' && calls===3) throw protocolError('CDP_TIMEOUT','Runtime.evaluate');
        const value=caption();
        if(scenario.fail==='initial-validation' && calls===2) value.bar.height=26;
        return value;
      },
      until:async(check,ms)=>{assert.equal(ms,15000);assert.equal(await check(),true);},
      cdp:{send:async()=>({})},
    }),error=>{
      assert.equal(error.message,scenario.code);
      assert.equal(error.titlebarFailure.operation,scenario.fail);
      assert.equal(error.titlebarFailure.method,scenario.method);
      assert.equal(error.titlebarFailure.errorCategory,scenario.category);
      assert.equal(safeInstalledFailure(error).protocolMethod,scenario.method==='none' ? null : scenario.method);
      assert.equal(error.titlebarFailure.observation.dom.bar,true);
      assert.equal(error.titlebarFailure.observation.document.readyState,'complete');
      assert.equal(error.titlebarFailure.screenshot.captured,false);
      return true;
    });
  }
});

test('generic failure diagnostics preserve only fixed kinds and allowed protocol method names',()=>{
  for(const kind of ['CDP_CLOSED','CDP_COMMAND','CDP_TIMEOUT','CDP_SEND','CDP_CONNECT','TIMEOUT','ERR_ASSERTION']) {
    const error=protocolError(kind,'Target.getTargets');
    error.params={token:'SYN_PRIVATE_TOKEN'};error.result={url:'https://SYN_PRIVATE_URL'};
    assert.deepEqual(safeInstalledFailure(error),{kind,protocolMethod:'Target.getTargets'});
  }
  assert.deepEqual(safeInstalledFailure({code:'ERR_ASSERTION',message:'https://SYN_PRIVATE_URL',
    protocolMethod:'Runtime.evaluate'}),{kind:'ERR_ASSERTION',protocolMethod:'Runtime.evaluate'});
  assert.equal(safeProtocolMethod('Network.getAllCookies'),null);
  const unsafe=safeInstalledFailure({message:'SYN_PRIVATE_TOKEN',protocolMethod:'https://SYN_PRIVATE_URL',
    headers:{'X-FundsTrace-Desktop':'SYN_PRIVATE_TOKEN'}});
  assert.deepEqual(unsafe,{kind:'UNCLASSIFIED',protocolMethod:null});
  assert.ok(!JSON.stringify(unsafe).includes('SYN_PRIVATE'));
});

async function connectHarness() {
  const source=await fs.readFile(path.join(__dirname,'installed-app-smoke.cjs'),'utf8');
  const start=source.indexOf('async function connect(url) {'),end=source.indexOf('async function evaluate(',start);
  assert.ok(start>=0 && end>start);
  let socket,nextTimer=1;
  const timers=new Map();
  class Socket {
    static OPEN=1;
    constructor() {socket=this;this.listeners=new Map();this.readyState=0;queueMicrotask(()=>{this.readyState=1;this.emit('open',{});});}
    addEventListener(name,fn) {const list=this.listeners.get(name)||[];list.push(fn);this.listeners.set(name,list);}
    emit(name,event) {for(const fn of this.listeners.get(name)||[]) fn(event);}
    send() {if(this.throwSend) throw new Error('SYN_PRIVATE_SEND_PAYLOAD');}
    close() {this.readyState=3;this.emit('close',{});}
  }
  const context=vm.createContext({WebSocket:Socket,protocolError,safeProtocolMethod,bounded:async promise=>promise,
    setTimeout:fn=>{const id=nextTimer++;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id)});
  vm.runInContext(`${source.slice(start,end)};globalThis.testConnect=connect;`,context);
  const client=await context.testConnect('ws://127.0.0.1/SYN_PRIVATE_DEBUG_PATH');
  return {client,socket,timers};
}

test('the actual CDP transport tags command, timeout, send and socket-close rejections without payloads',async()=>{
  for(const kind of ['CDP_COMMAND','CDP_TIMEOUT','CDP_SEND','CDP_CLOSED','CDP_INVALID_JSON']) {
    const h=await connectHarness();
    if(kind==='CDP_SEND') h.socket.throwSend=true;
    const request=h.client.send('Target.getTargets',{headers:{token:'SYN_PRIVATE_TOKEN'}},'SYN_PRIVATE_SESSION');
    if(kind==='CDP_COMMAND') h.socket.emit('message',{data:JSON.stringify({id:1,error:{message:'https://SYN_PRIVATE_URL/SYN_PRIVATE_TOKEN'}})});
    if(kind==='CDP_TIMEOUT') [...h.timers.values()][0]();
    if(kind==='CDP_CLOSED') h.socket.close();
    if(kind==='CDP_INVALID_JSON') h.socket.emit('message',{data:'SYN_PRIVATE_INVALID_PACKET'});
    await assert.rejects(request,error=>{
      assert.deepEqual(safeInstalledFailure(error),{kind,protocolMethod:'Target.getTargets'});
      assert.ok(!JSON.stringify(safeInstalledFailure(error)).includes('SYN_PRIVATE'));
      return true;
    });
    h.client.close();
    await assert.rejects(h.client.send('Runtime.evaluate'),error=>error.message==='CDP_CLOSED'
      && error.protocolMethod==='Runtime.evaluate');
  }
});

test('Main stderr probes latch fixed markers across chunks and retain only boolean snapshots',()=>{
  const probe=createMainErrorMarkerProbe();
  probe.consume('https://SYN_PRIVATE_URL token=SYN_PRIVATE_TOKEN\nObject has been dest');
  assert.deepEqual(probe.snapshot(),{objectDestroyed:false,javascriptErrorInMainProcess:false,uncaughtException:false});
  probe.consume('royed\nA JavaScript error occurred in the main pro');
  probe.consume('cess\nUNCAUGHT EXCEPTION\nSYN_PRIVATE_TOKEN');
  assert.deepEqual(probe.snapshot(),{objectDestroyed:true,javascriptErrorInMainProcess:true,uncaughtException:true});
  assert.ok(!JSON.stringify(probe.snapshot()).includes('SYN_PRIVATE'));
  assert.deepEqual(Object.keys(probe).sort(),['consume','snapshot']);
  const shorter=createMainErrorMarkerProbe();shorter.consume('JavaScript error occurred in main process');
  assert.equal(shorter.snapshot().javascriptErrorInMainProcess,true);
});
