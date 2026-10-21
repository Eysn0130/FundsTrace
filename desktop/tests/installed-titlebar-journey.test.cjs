'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {titlebarTarget,assertCaption,assertCaptionBody,windowId} = require('./installed-titlebar-journey.cjs');

function caption() {
  return {time:1000,timeOrigin:9000,viewport:{width:1280,height:720,dpr:1},
    overlay:{visible:true,area:{x:0,y:0,width:1142,height:32}},
    bar:{x:0,y:0,width:1280,height:32},content:{x:0,y:0,width:1142,height:32},
    icon:{x:8,y:7,width:18,height:18},name:{x:33,y:7,width:108,height:18},
    menu:{x:150,y:3,width:28,height:26},text:'循迹 FundsTrace',opacity:1,visibility:'visible',
    fontSize:13,fontWeight:'400',color:'rgb(213, 222, 232)',drag:'drag',menuDrag:'no-drag',
    menuDisabled:true,iconLoaded:true,stylesheetLoaded:true};
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

test('native parent binding requires an actual Browser window ID for each target',async()=>{
  const calls=[];
  assert.equal(await windowId({send:async(method,params)=>{calls.push({method,params});return {windowId:12};}},
    {targetId:'body'}),12);
  assert.deepEqual(calls,[{method:'Browser.getWindowForTarget',params:{targetId:'body'}}]);
  await assert.rejects(windowId({send:async()=>({})},{targetId:'body'}),/TITLEBAR_NATIVE_WINDOW_BINDING/);
});
