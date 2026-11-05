'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const vm=require('node:vm');

function workspace({collapsed=false,slotLeft=56,width=1024,scale=1,scrolling=false,tabScroll=false}={}) {
  const nodes=new Map(),box=(left,top,width,height)=>({left,top,right:left+width,bottom:top+height,width,height});
  const node=(id,bounds,parent=null,control=false)=>{
    const value={id,bounds,parentElement:parent,hidden:false,control,clientWidth:bounds.width,clientHeight:bounds.height,clientTop:0,scrollWidth:bounds.width,_scrollLeft:0,scrollWrites:[],
      style:{visibility:'visible',overflowX:'visible',overflowY:'visible'},selectors:new Map(),children:[],
      getBoundingClientRect(){let left=0;for(let p=this.parentElement;p;p=p.parentElement)left+=p.scrollLeft||0;
        return {...this.bounds,left:this.bounds.left-left,right:this.bounds.right-left};},
      getClientRects(){for(let p=this;p;p=p.parentElement)if(p.hidden)return [];return [this.getBoundingClientRect()];},
      querySelector(selector){return this.selectors.get(selector)||null;},
      querySelectorAll(selector){return selector==='button,input,select,a'?[...nodes.values()].filter(item=>item.control&&this.contains(item)):[];},
      contains(other){for(let p=other;p;p=p.parentElement)if(p===this)return true;return false;},
      getAttribute(name){return this.attributes?.[name]??null;}};
    Object.defineProperty(value,'scrollLeft',{get(){return this._scrollLeft;},set(left){this.scrollWrites.push(left);
      if(!this.scrollLocked&&!(this.restoreLocked&&this._scrollLeft>0&&left===0))this._scrollLeft=Math.max(0,Math.min(left,this.scrollWidth-this.clientWidth));}});
    nodes.set(id,value);if(parent?.children)parent.children.push(value);return value;
  };
  const slotWidth=width-slotLeft,narrow=slotWidth<1200,stacked=slotWidth<720,headerHeight=stacked?56:34,
    height=collapsed?(stacked?99:77):(stacked?129:narrow?107:127);
  const slot=node('graphPage',box(slotLeft,0,slotWidth,688));
  const toolbar=node('editorToolbar',box(slotLeft,0,slotWidth*scale,height*scale),slot);
  toolbar.clientWidth=slotWidth;toolbar.style.height=String(height);toolbar.attributes={'data-ribbon-layout':stacked?'stacked':narrow?'narrow':'wide'};
  toolbar.style.getPropertyValue=key=>key==='--toolbar-fit-scale'?String(scale):'';
  toolbar.classList={contains:name=>name==='is-collapsed'&&collapsed};
  const header=node('ribbon-tabbar',box(slotLeft,0,slotWidth,headerHeight),toolbar);
  const tabs=node('ribbonTabs',box(slotLeft+8,0,tabScroll?120:240,28),header);
  tabs.style.overflowX='auto';tabs.scrollWidth=240;header.selectors.set('#ribbonTabs',tabs);
  for(const [index,id] of ['ribbonTab-common','ribbonTab-trace','ribbonTab-style','ribbonTab-view'].entries())
    node(id,box(slotLeft+8+index*60,0,56,28),tabs,true);
  const required=['caseSelect','canvasSel','btnCanvasSave','btnUndo','btnRedo','btnWorkspaceView'];
  for(const [index,id] of required.entries())node(id,box(slotLeft+(stacked?8:250)+index*(narrow&&!stacked?50:90),stacked?28:3,narrow&&!stacked?44:80,28),header,true);
  const inspect=node('inspect',box(slotLeft+slotWidth-104,0,28,28),header,true);
  const fold=node('fold',box(slotLeft+slotWidth-36,0,28,28),header,true);
  header.selectors.set('[data-ribbon-command="inspectSelection"]',inspect);header.selectors.set('[data-ribbon-collapse]',fold);
  const panel=node('ribbonPanel-common',box(slotLeft,headerHeight,slotWidth,narrow?72:92),toolbar);
  panel.hidden=collapsed;panel.style.overflowX='auto';panel.style.overflowY='hidden';
  const compact=node('ribbonCompact',box(slotLeft,headerHeight,slotWidth,42),toolbar);
  compact.hidden=!collapsed;compact.style.overflowX='auto';compact.style.overflowY='hidden';
  const action=node('action',box(slotLeft+8,headerHeight+4,60,52),panel,true);
  action.style.flexDirection='column';action.style.fontSize='12px';
  const icon=node('icon',box(slotLeft+8,headerHeight+4,narrow?22:24,narrow?22:24),action);icon.style.width=narrow?'22px':'24px';
  action.selectors.set('.ui-icon',icon);panel.selectors.set('.ribbon-action',action);
  node('compactAction',box(slotLeft+8,headerHeight+2,60,28),compact,true);
  for(const [index,id] of ['btnCanvasClone','btnCanvasRemove'].entries()){
    const direct=node(id,box(slotLeft+80+index*70,headerHeight+4,60,52),panel,true);direct.hidden=narrow;
  }
  const menu=node('canvasActions',box(0,0,240,100));menu.hidden=true;
  node('btnCanvasMenuClone',box(0,0,200,32),menu,true);node('btnCanvasDel',box(0,32,200,32),menu,true);
  if(scrolling){const bar=collapsed?compact:panel;bar.scrollWidth=slotWidth+200;bar.clientHeight-=8;
    node('farAction',box(slotLeft+slotWidth+120,headerHeight+4,60,28),bar,true);}
  const table=node('flowTableScroll',box(slotLeft,250,560,400));table.style.overflowX='auto';table.tabIndex=0;
  const document={tableColumns:7,duplicateScrollControls:[],querySelector:selector=>({'#editorToolbar':toolbar,'#editorToolbar .ribbon-tabbar':header,
    '#ribbonCompact':compact,'#flowTableScroll':table,'#canvasActions':menu}[selector]||null),
    querySelectorAll(selector){return {'#editorToolbar .ribbon-panel:not([hidden])':collapsed?[]:[panel],
      '#flowTable thead th':Array(this.tableColumns).fill({}),'.flow-horizontal-scroll':this.duplicateScrollControls}[selector]||[];},
    getElementById:id=>nodes.get(id)||null};
  return {nodes,document,innerWidth:width,innerHeight:688,devicePixelRatio:1,getComputedStyle:value=>value.style,requestAnimationFrame:fn=>fn()};
}
async function toolsFixture(options) {
  const source=await fs.readFile(path.join(__dirname,'installed-app-smoke.cjs'),'utf8');
  const start=source.indexOf('function readWorkspaceTools(target) {'),end=source.indexOf('function argumentsOf() {',start);
  assert.ok(start>=0&&end>start);
  const fixture=workspace(options),context=vm.createContext({...fixture,assert});
  vm.runInContext(source.slice(start,end),context);
  return {...fixture,read:()=>context.readWorkspaceTools(),verify:()=>context.verifyWorkspaceToolReachability(context.readWorkspaceTools),
    readImport:()=>context.readImportEntry(),check:value=>context.assertWorkspaceTools(value,{tab:'common',collapsed:options?.collapsed===true})};
}

test('installed projection keeps full-size controls at both three-layout boundaries without legacy topbar scaling',async()=>{
  const cases=[{slot:719,layout:'stacked',expanded:129,collapsed:99},
    {slot:720,layout:'narrow',expanded:107,collapsed:77},{slot:817,layout:'narrow',expanded:107,collapsed:77},
    {slot:1199,layout:'narrow',expanded:107,collapsed:77},{slot:1200,layout:'wide',expanded:127,collapsed:77}];
  for(const expected of cases)for(const collapsed of [false,true]) {
    const fixture=await toolsFixture({width:expected.slot+56,collapsed}),value=fixture.read();
    assert.equal(value.layout,expected.layout);assert.equal(value.cssHeight,collapsed?expected.collapsed:expected.expanded);
    assert.equal(fixture.nodes.has('topbar'),false);assert.equal(value.bars[0].id,'ribbon-tabbar');
    assert.equal(value.bars.length,2);assert.equal(value.clippedControls,0);assert.equal(value.scale,1);
    assert.equal(value.logicalWidth,value.slotClientWidth);fixture.check(value);
  }
  const scaled=await toolsFixture({scale:.8});assert.throws(()=>scaled.check(scaled.read()),/WORKSPACE_TOOLBAR_SLOT_CLIPPED|WORKSPACE_TOOLBAR_SCALE/);
});

test('installed assertion rejects toolbar overflow into its rail despite fitting the viewport',async()=>{
  const fixture=await toolsFixture(),toolbar=fixture.nodes.get('editorToolbar');
  toolbar.bounds={...toolbar.bounds,left:0,width:1024,right:1024};
  assert.throws(()=>fixture.check(fixture.read()),/WORKSPACE_TOOLBAR_SLOT_CLIPPED/);
});

test('permanent horizontal or vertical clipping remains a failure outside legal native scroll areas',async()=>{
  for(const target of ['caseSelect','action','compactAction']) {
    const fixture=await toolsFixture({collapsed:target==='compactAction'}),control=fixture.nodes.get(target);
    control.bounds={...control.bounds,left:20,right:50,width:30};
    const value=fixture.read();assert.ok(value.clippedControls>0);
    assert.throws(()=>fixture.check(value),/WORKSPACE_TOOLBAR_CLIPPED/);
  }
  const fixture=await toolsFixture(),control=fixture.nodes.get('caseSelect');
  control.parentElement={bounds:{left:56,top:0,right:80,bottom:56,width:24,height:56},parentElement:control.parentElement,
    style:{overflowX:'hidden',overflowY:'visible'},getBoundingClientRect(){return this.bounds;}};
  assert.throws(()=>fixture.check(fixture.read()),/WORKSPACE_TOOLBAR_CLIPPED/);
  const vertical=await toolsFixture({scrolling:true});vertical.nodes.get('farAction').bounds.bottom=200;
  assert.throws(()=>vertical.check(vertical.read()),/WORKSPACE_TOOLBAR_CLIPPED/);
});

test('native horizontal command scrolling and narrow tab scrolling require observed reachability and restore position',async()=>{
  for(const options of [{scrolling:true},{scrolling:true,collapsed:true},{tabScroll:true},{width:775,tabScroll:true},{width:1336,scrolling:true}]) {
    const fixture=await toolsFixture(options),value=fixture.read();
    assert.ok(value.clippedControls>0);assert.equal(value.permanentClippedControls,0);
    assert.throws(()=>fixture.check(value),/WORKSPACE_TOOLBAR_UNREACHABLE/);
    value.reachability=await fixture.verify();fixture.check(value);
    assert.equal(value.reachability.reached,value.clippedControls);assert.equal(value.reachability.restored,true);
    for(const node of fixture.nodes.values())assert.equal(node.scrollLeft,0);
    assert.ok([...fixture.nodes.values()].some(node=>node.scrollWrites.some(left=>left>0)));
  }
});

test('scrolling does not excuse frozen scrolling, a hidden clipping parent, oversized commands or failed restoration',async()=>{
  for(const fault of ['frozen','hidden-parent','oversized','restore']) {
    const fixture=await toolsFixture({scrolling:true}),bar=fixture.nodes.get('ribbonPanel-common'),control=fixture.nodes.get('farAction');
    if(fault==='frozen')bar.scrollLocked=true;
    if(fault==='restore')bar.restoreLocked=true;
    if(fault==='oversized')control.bounds={...control.bounds,width:1200,right:control.bounds.left+1200};
    if(fault==='hidden-parent')control.parentElement={parentElement:bar,style:{overflowX:'hidden',overflowY:'visible'},
      getBoundingClientRect:()=>({left:56,top:56,right:100,bottom:120})};
    const value=fixture.read();value.reachability=await fixture.verify();
    assert.throws(()=>fixture.check(value),/WORKSPACE_TOOLBAR_UNREACHABLE/);
    if(fault!=='restore')assert.equal(value.reachability.restored,true);
  }
});

test('horizontal reachability cannot excuse a native scrollbar clipping the command vertically',async()=>{
  const fixture=await toolsFixture({scrolling:true,collapsed:true}),compact=fixture.nodes.get('ribbonCompact');
  const fitting=fixture.read();fitting.reachability=await fixture.verify();fixture.check(fitting);
  // The outer 42px box still fits, but its native scrollport now exposes only 26px of content.
  compact.clientHeight=26;
  const clipped=fixture.read();
  assert.ok(clipped.permanentClippedControls>0);
  assert.throws(()=>fixture.check(clipped),/WORKSPACE_TOOLBAR_CLIPPED/);
});

test('row overflow, persistent commands, management fallback and the seven-column single-scroll table remain required',async()=>{
  const overflow=await toolsFixture();overflow.nodes.get('ribbon-tabbar').scrollWidth=1290;
  assert.throws(()=>overflow.check(overflow.read()),/WORKSPACE_TOOLBAR_OVERFLOW/);
  const hidden=await toolsFixture({scrolling:true});hidden.nodes.get('ribbonPanel-common').style.overflowX='hidden';
  assert.throws(()=>hidden.check(hidden.read()),/WORKSPACE_TOOLBAR_OVERFLOW/);
  const missing=await toolsFixture();missing.nodes.get('btnUndo').hidden=true;
  assert.throws(()=>missing.check(missing.read()),/WORKSPACE_TOOLBAR_CONTROLS_MISSING/);
  const direct=await toolsFixture();direct.nodes.get('btnCanvasClone').hidden=false;
  assert.throws(()=>direct.check(direct.read()),/WORKSPACE_CANVAS_DIRECT_CONTROLS/);
  const menu=await toolsFixture();menu.nodes.delete('btnCanvasMenuClone');
  assert.throws(()=>menu.check(menu.read()),/WORKSPACE_CANVAS_MENU_COMMANDS/);
  for(const fault of ['focus','columns','duplicate']) {
    const fixture=await toolsFixture();
    if(fault==='focus')fixture.nodes.get('flowTableScroll').tabIndex=-1;
    if(fault==='columns')fixture.document.tableColumns=6;
    if(fault==='duplicate')fixture.document.duplicateScrollControls.push(fixture.nodes.get('flowTableScroll'));
    assert.throws(()=>fixture.check(fixture.read()),{code:'ERR_ASSERTION'});
  }
});

test('slot metadata, exact action sizes and three-layout heights remain enforced',async()=>{
  for(const fault of ['layout','logicalWidth','height','font','icon','direction']) {
    const fixture=await toolsFixture(),value=fixture.read();
    if(fault==='layout')value.layout='wide';
    if(fault==='logicalWidth')value.logicalWidth=1280;
    if(fault==='height')value.cssHeight=133;
    if(fault==='font')value.action.font=11;
    if(fault==='icon')value.action.icon=27;
    if(fault==='direction')value.action.direction='row';
    assert.throws(()=>fixture.check(value),{code:'ERR_ASSERTION'});
  }
});

test('reachability restores a previously scrolled command row, not just zero',async()=>{
  const fixture=await toolsFixture({scrolling:true}),bar=fixture.nodes.get('ribbonPanel-common');
  bar.scrollLeft=83;
  const value=fixture.read();value.reachability=await fixture.verify();fixture.check(value);
  assert.equal(bar.scrollLeft,83);assert.equal(value.reachability.restored,true);
});

test('native file input remains enabled and exactly associated with its visible label despite its clipped size',async()=>{
  const fixture=await toolsFixture(),input={id:'synthetic-file',type:'file',accept:'.csv,.tsv,.txt,.xlsx,.xls,.zip',disabled:false};
  const label={htmlFor:input.id,control:input,querySelector:()=>input};
  fixture.nodes.set(input.id,input);
  const query=fixture.document.querySelector;
  fixture.document.querySelector=selector=>selector==='dialog[open] label.file-drop'?label:query(selector);
  assert.deepEqual(JSON.parse(JSON.stringify(fixture.readImport())),
    {associated:true,type:'file',accept:'.csv,.tsv,.txt,.xlsx,.xls,.zip',enabled:true});
  label.htmlFor='another-input';assert.equal(fixture.readImport().associated,false);
  label.htmlFor=input.id;label.control=null;assert.equal(fixture.readImport().associated,false);
  label.control=input;input.disabled=true;assert.equal(fixture.readImport().enabled,false);
});

test('the actual pointer helper preserves ordinary clicks and dispatches Ctrl on both real mouse events',async()=>{
  const source=await fs.readFile(path.join(__dirname,'installed-app-smoke.cjs'),'utf8');
  const start=source.indexOf('async function clickElement('),end=source.indexOf('function visibleControl(',start);
  assert.ok(start>=0&&end>start);
  const calls=[],page={sessionId:'body'};
  const context=vm.createContext({assert,page,evaluate:async()=>({x:120,y:80}),cdp:{send:async(...args)=>{calls.push(args);}}});
  vm.runInContext(source.slice(start,end),context);
  await context.clickElement('SYN_ELEMENT');
  assert.equal(calls.length,2);
  for(const call of calls) assert.equal(Object.hasOwn(call[1],'modifiers'),false,'existing click payloads remain unchanged');
  calls.length=0;
  await context.clickElement('SYN_ELEMENT',page,{modifiers:2});
  assert.deepEqual(calls.map(call=>[call[0],call[1].type,call[1].button,call[1].modifiers,call[2]]),[
    ['Input.dispatchMouseEvent','mousePressed','left',2,'body'],['Input.dispatchMouseEvent','mouseReleased','left',2,'body']]);
  await assert.rejects(context.clickElement('SYN_ELEMENT',page,{modifiers:32}),/POINTER_MODIFIERS_INVALID/);
});

test('inspection waits for settled height and unscaled slot width without waiting away clipping failures',async()=>{
  const source=(await fs.readFile(path.join(__dirname,'installed-app-smoke.cjs'),'utf8')).replace(/\r\n/g,'\n');
  const start=source.indexOf('const inspect = async (label,expected) => {');
  const end=source.indexOf('    try {\n      for(const size',start);
  assert.ok(start>=0&&end>start);
  for(const clipping of [false,true]) for(const pending of ['height','scale']) {
    const fixture=await toolsFixture(),samples=[];
    if(clipping)fixture.nodes.get('caseSelect').bounds.left=20;
    let reads=0;
    const context=vm.createContext({samples,assertWorkspaceTools:value=>fixture.check(value),
      readWorkspaceTools(){},evaluate:async()=>{
        const value=fixture.read();
        if(++reads===1){if(pending==='height')value.cssHeight=100;else value.scale=.8;}
        return value;
      },
      until:async check=>{for(let attempt=0;attempt<3;attempt++)if(await check())return;throw new Error('TIMEOUT');}});
    vm.runInContext(`${source.slice(start,end)};globalThis.inspect=inspect;`,context);
    if(clipping)await assert.rejects(context.inspect('synthetic',{tab:'common',collapsed:false}),/WORKSPACE_TOOLBAR_CLIPPED/);
    else await context.inspect('synthetic',{tab:'common',collapsed:false});
    assert.equal(reads,2);assert.equal(samples.length,1);assert.equal(samples[0].cssHeight,107);
  }
});

test('the actual inspection records bounded reachability evidence instead of accepting scroll metadata alone',async()=>{
  const source=(await fs.readFile(path.join(__dirname,'installed-app-smoke.cjs'),'utf8')).replace(/\r\n/g,'\n');
  const start=source.indexOf('const inspect = async (label,expected) => {'),end=source.indexOf('    try {\n      for(const size',start);
  assert.ok(start>=0&&end>start);
  for(const frozen of [false,true]) {
    const fixture=await toolsFixture({scrolling:true}),samples=[];let probes=0;
    fixture.nodes.get('ribbonPanel-common').scrollLocked=frozen;
    const context=vm.createContext({samples,readWorkspaceTools(){},verifyWorkspaceToolReachability(){},
      assertWorkspaceTools:value=>fixture.check(value),until:async check=>{assert.equal(await check(),true);},
      evaluate:async expression=>{if(expression.includes('verifyWorkspaceToolReachability')){probes++;return fixture.verify();}return fixture.read();}});
    vm.runInContext(`${source.slice(start,end)};globalThis.inspect=inspect;`,context);
    if(frozen)await assert.rejects(context.inspect('synthetic',{tab:'common',collapsed:false}),/WORKSPACE_TOOLBAR_UNREACHABLE/);
    else await context.inspect('synthetic',{tab:'common',collapsed:false});
    assert.equal(probes,1);assert.equal(samples.length,1);assert.equal(samples[0].reachability.restored,true);
  }
});

async function casesFixture({missing=false,crossOrigin=false,originThrows=false,hidden=false,outerBlocked=false,innerBlocked=false}={}) {
  const source=await fs.readFile(path.join(__dirname,'installed-app-smoke.cjs'),'utf8');
  const start=source.indexOf('function casesFrameExpression('),end=source.indexOf('async function readyDocument(',start);
  assert.ok(start>=0&&end>start);
  const box=(x,y,width,height)=>({x,y,left:x,top:y,right:x+width,bottom:y+height,width,height});
  const style={visibility:'visible',display:'block',opacity:'1'},calls=[],page={sessionId:'body',origin:'http://127.0.0.1:12345'};
  const button={disabled:false,style,scrollIntoView(){},getBoundingClientRect:()=>box(20,30,80,40),contains:()=>false};
  const childDocument={readyState:'complete',documentElement:{scrollWidth:796},
    querySelector:selector=>selector==='#action'?button:null,elementFromPoint:()=>innerBlocked?{}:button};
  const child={document:childDocument,location:{origin:crossOrigin?'https://invalid.example':page.origin},
    innerWidth:796,innerHeight:496,devicePixelRatio:1,getComputedStyle:node=>node.style,
    requestAnimationFrame:fn=>fn(),scrollTo(){}};
  if(originThrows)Object.defineProperty(child,'location',{get(){throw new Error('SYN_PRIVATE_CROSS_ORIGIN_DETAIL');}});
  const frame={tagName:'IFRAME',hidden,style,contentWindow:child,contentDocument:childDocument,
    offsetWidth:800,offsetHeight:500,clientWidth:796,clientHeight:496,clientLeft:2,clientTop:2,
    bounds:box(56,40,1000,625),getBoundingClientRect(){return this.bounds;},getClientRects(){return this.hidden?[]:[this.bounds];}};
  const document={documentElement:{scrollWidth:1280},querySelector:selector=>selector==='#casesFrame'&&!missing?frame:null,
    elementFromPoint:()=>outerBlocked?{}:frame};
  const context=vm.createContext({assert,URL,page,document,location:{origin:page.origin},innerWidth:1280,innerHeight:800,
    devicePixelRatio:1,getComputedStyle:node=>node.style,requestAnimationFrame:fn=>fn(),window:{scrollTo(){}},
    cdp:{send:async(...args)=>{calls.push(args);}}});
  context.evaluate=async expression=>vm.runInContext(expression,context);
  vm.runInContext(source.slice(start,end),context);
  return {source,context,calls,page,frame,child,document,childDocument};
}

test('the actual cases-frame pointer uses both hit tests and border-aware transformed frame offsets',async()=>{
  const fixture=await casesFixture();
  await fixture.context.clickElement('document.querySelector("#action")',fixture.page,{frame:true,modifiers:2});
  assert.equal(fixture.calls.length,2);
  for(const [,params,session] of fixture.calls) {
    assert.equal(params.x,133.5);assert.equal(params.y,105);assert.equal(params.modifiers,2);assert.equal(session,'body');
  }
  assert.equal(vm.runInContext(fixture.context.visibleControl('document.querySelector("#action")',{frame:true}),fixture.context),true);
});

test('missing, hidden, cross-origin and obscured cases frames cannot dispatch a product click',async()=>{
  for(const options of [{missing:true},{hidden:true},{crossOrigin:true},{originThrows:true},{outerBlocked:true},{innerBlocked:true}]) {
    const fixture=await casesFixture(options);
    await assert.rejects(fixture.context.clickElement('document.querySelector("#action")',fixture.page,{frame:true}));
    assert.equal(fixture.calls.length,0);
    const expression=fixture.context.visibleControl('document.querySelector("#action")',{frame:true});
    if(options.missing||options.hidden||options.crossOrigin||options.originThrows)assert.throws(()=>vm.runInContext(expression,fixture.context),error=>
      /^CASES_FRAME_(MISSING|HIDDEN|CROSS_ORIGIN)$/.test(error.message)&&!error.message.includes('SYN_PRIVATE'));
    else assert.equal(vm.runInContext(expression,fixture.context),false);
  }
});

test('the actual embedded matrix measures the iframe slot rather than equating child and top viewports',async()=>{
  for(const mismatch of [false,true]) {
    const fixture=await casesFixture(),{context,frame,child,document,childDocument}=fixture;
    const start=fixture.source.indexOf('async function viewportMatrix('),end=fixture.source.indexOf('async function portClosed(',start);
    assert.ok(start>=0&&end>start);
    context.VIEWPORTS=[[960,500]];context.DEVICE_SCALES=[1.25];context.check=async(_name,action)=>action();
    context.cdp.send=async(_method,metrics)=>{
      Object.assign(context,{innerWidth:metrics.width,innerHeight:metrics.height,devicePixelRatio:metrics.deviceScaleFactor});
      document.documentElement.scrollWidth=metrics.width;
      frame.bounds={x:56,y:0,left:56,top:0,right:metrics.width,bottom:metrics.height,width:metrics.width-56,height:metrics.height};
      frame.parentElement={getBoundingClientRect:()=>frame.bounds};
      frame.offsetWidth=frame.clientWidth=metrics.width-56;frame.offsetHeight=frame.clientHeight=metrics.height;
      frame.clientLeft=frame.clientTop=0;
      child.innerWidth=frame.clientWidth+(mismatch?2:0);child.innerHeight=frame.clientHeight;child.devicePixelRatio=metrics.deviceScaleFactor;
      childDocument.documentElement.scrollWidth=child.innerWidth;
    };
    vm.runInContext(fixture.source.slice(start,end),context);
    const run=context.viewportMatrix('embedded',fixture.page,'true',[],[],{frame:true});
    if(mismatch)await assert.rejects(run,/CASES_FRAME_SLOT_GEOMETRY/);else await run;
    assert.equal(frame.clientWidth,904);
  }
});

test('the actual public React entry requires the host redirect, preserves its case and selects through the iframe',async()=>{
  const source=await fs.readFile(path.join(__dirname,'installed-app-smoke.cjs'),'utf8');
  const start=source.indexOf("  await check('installedReactWorkspaceOpened'"),end=source.indexOf("  await viewportMatrix('reactWorkspaceLayout'",start);
  assert.ok(start>=0&&end>start);
  for(const failure of [null,'standalone','lost-case','unembedded']) {
    const origin='http://127.0.0.1:12345',selected={value:'2',selectedOptions:[{textContent:'SYN current'}]},report={},navigations=[];
    const childDoc={querySelector:selector=>selector==='.workspace-heading .description .mono'?{textContent:selected.value}
      :selector==='.case-item[aria-pressed="true"]'?{getAttribute:()=> 'SYN seed'}:null};
    const frame={hidden:false,src:`${origin}/workspace/?embedded=${failure==='unembedded'?'0':'1'}&surface=cases&case_id=2`,contentDocument:childDoc};
    const context=vm.createContext({assert,URL,URLSearchParams,SEED_CASE:'SYN seed',report,page:{origin,sessionId:'body'},
      location:{pathname:'/',search:'?case_id=2'},document:{querySelector:selector=>selector==='#casesFrame'?frame:selected},
      check:async(_name,action)=>action(),readyCases:async()=>{},startWorkspaceGraphEventObservation:async()=>{},
      clickElement:async(_expression,_target,options)=>{assert.equal(options.frame,true);selected.value='3';selected.selectedOptions=[{textContent:'SYN seed'}];}});
    context.evaluate=async()=>selected.value;
    context.readyDocument=async expression=>assert.equal(vm.runInContext(expression,context),true);
    context.cdp={send:async(_method,params)=>{
      navigations.push(params.url);
      context.location=failure==='standalone'?{pathname:'/workspace/',search:'?case_id=2'}
        :{pathname:'/',search:failure==='lost-case'?'?page=cases':'?page=cases&case_id=2',origin};
    }};
    const run=vm.runInContext(`(async()=>{${source.slice(start,end)}})()`,context);
    if(failure)await assert.rejects(run,{code:'ERR_ASSERTION'});else {
      await run;assert.equal(selected.value,'3');assert.equal(report.workspaceEntry.caseSelectionLinked,true);
      assert.ok(frame.src.endsWith('case_id=2'),'bridge case switches do not require rewriting the iframe URL');
    }
    assert.deepEqual(navigations,[`${origin}/workspace/?case_id=2`]);
  }
});

async function importEntryContractFixture(sourceText) {
  const fixture=await casesFixture(),{context,page,childDocument}=fixture;
  const source=(sourceText??fixture.source).replace(/\r\n/g,'\n');
  const start=source.indexOf("  await check('reactEmptyCaseImportEntry'"),end=source.indexOf("  await check('workspaceGraphLinkOpened'",start);
  const readerStart=source.indexOf('function readImportEntry() {'),readerEnd=source.indexOf('function argumentsOf() {',readerStart);
  assert.ok(start>=0&&end>start&&readerStart>=0&&readerEnd>readerStart);
  const input={id:'synthetic-file',type:'file',accept:'.csv,.tsv,.txt,.xlsx,.xls,.zip',disabled:false};
  const label=childDocument.querySelector('#action'),events=[];
  label.style={...label.style};label.htmlFor=input.id;label.control=input;
  label.querySelector=selector=>{assert.equal(selector,'input[type="file"]');return input;};
  let open=false;
  const originalQuery=childDocument.querySelector;
  childDocument.querySelector=selector=>selector==='dialog[open] label.file-drop'?(open?label:null)
    :selector==='dialog[open] input[type="file"]'?(open?input:null)
    :selector==='dialog[open]'?(open?{}:null):originalQuery(selector);
  childDocument.getElementById=id=>id===input.id?input:null;
  context.check=async(name,action)=>{assert.equal(name,'reactEmptyCaseImportEntry');events.push('check');await action();};
  context.clickElement=async(expression,target,options)=>{
    assert.equal(target,page);assert.equal(options.frame,true);
    if(!open){assert.ok(expression.includes('.empty-state .button.primary'));open=true;events.push('open');}
    else{assert.ok(expression.includes("item.textContent.trim() === '取消'"));open=false;events.push('cancel');}
  };
  context.readyCases=async(expression,target=page)=>{
    assert.equal(target,page);events.push(open?'wait-input':'wait-closed');
    assert.equal(vm.runInContext(context.casesFrameExpression(`document.readyState==='complete'&&(${expression})`),context),true);
  };
  context.evaluate=async(expression,target=page)=>{
    assert.equal(target,page);events.push(expression.includes('function readImportEntry')?'read-entry':'visible-label');
    return vm.runInContext(expression,context);
  };
  vm.runInContext(source.slice(readerStart,readerEnd),context);
  return {...fixture,input,label,events,isOpen:()=>open,
    run:()=>vm.runInContext(`(async()=>{${source.slice(start,end)}})()`,context)};
}

for(const newline of ['LF','CRLF'])test(`the exact native empty-case import block accepts all six suffixes with ${newline} source`,async()=>{
  const source=(await fs.readFile(path.join(__dirname,'installed-app-smoke.cjs'),'utf8')).replace(/\r\n/g,'\n');
  const fixture=await importEntryContractFixture(newline==='CRLF'?source.replace(/\n/g,'\r\n'):source);
  await fixture.run();
  assert.deepEqual(fixture.events,['check','open','wait-input','visible-label','read-entry','cancel','wait-closed']);
  assert.equal(fixture.isOpen(),false);
});

for(const [name,change] of [
  ['missing XLS',value=>{value.input.accept='.csv,.tsv,.txt,.xlsx,.zip';}],
  ['missing ZIP',value=>{value.input.accept='.csv,.tsv,.txt,.xlsx,.xls';}],
  ['extra EXE',value=>{value.input.accept+=',.exe';}],
  ['wrong label association',value=>{value.label.htmlFor='another-input';}],
  ['disabled input',value=>{value.input.disabled=true;}],
  ['non-file input',value=>{value.input.type='text';}],
  ['invisible label',value=>{value.label.style.visibility='hidden';}],
])test(`the exact native empty-case import block rejects ${name}`,async()=>{
  const fixture=await importEntryContractFixture();change(fixture);
  await assert.rejects(fixture.run(),{code:'ERR_ASSERTION'});
  assert.ok(!fixture.events.includes('cancel'),'a failed contract must not reach the successful close');
});

const WORKSPACE_GRAPH_KEYS=['pathHome','documentComplete','statePresent','graphPresent','graphLoadingFalse','graphInited',
  'caseNameStrictMatch','caseNameCoercedMatch','caseSelectorMatches','flowsZeroStrict','flowsZeroCoerced','nodesEmpty','edgesEmpty',
  'casesFramePresent','casesFrameHidden','graphPageActive','homeCurrent','layoutIdle','historyIdle'];
const plain=value=>JSON.parse(JSON.stringify(value));
async function graphReturnFixture({newline='LF',failure=null,diagnosticValue,diagnosticThrows=false,diagnosticTimeout=false,otherStage=false,observation=null}={}) {
  let source=(await fs.readFile(path.join(__dirname,'installed-app-smoke.cjs'),'utf8')).replace(/\r\n/g,'\n');
  if(newline==='CRLF')source=source.replace(/\n/g,'\r\n');
  const blockStart=source.indexOf("  await check('workspaceGraphLinkOpened'"),blockEnd=source.indexOf("  await check('installedPersistentCaptionNavigation'",blockStart);
  const checkStart=source.indexOf('async function check(name, action) {'),checkEnd=source.indexOf('async function windowIcons(',checkStart);
  const readyStart=source.indexOf('async function readyDocument('),readyEnd=source.indexOf('async function viewportMatrix(',readyStart);
  const readerStart=source.indexOf('function readWorkspaceGraphDiagnostic('),readerEnd=source.indexOf('function readImportEntry() {',readerStart);
  const tailStart=source.indexOf('})().catch(async error => {');
  assert.ok(blockStart>=0&&blockEnd>blockStart&&checkStart>=0&&checkEnd>checkStart&&readyStart>=0&&readyEnd>readyStart&&tailStart>=0);
  const report={status:'running',checks:{},timings:{}},events=[],seenErrors=[],writes=[],seed='SYN fixture seed';
  const originalError=Object.assign(new Error(failure==='click'?'CDP_TIMEOUT':'TIMEOUT'),failure==='click'?{protocolMethod:'Input.dispatchMouseEvent'}:{});
  const frame={hidden:true},graphPage={inert:false},home={getAttribute:()=> 'page'},select={value:'7'};
  for(const name of ['contentWindow','contentDocument'])Object.defineProperty(frame,name,{get(){throw new Error('SYN_PRIVATE_CROSS_ORIGIN');}});
  const nodes=new Map([['#casesFrame',frame],['#graphPage',graphPage],['[data-nav-page="home"]',home],['#caseSelect',select]]);
  const state={caseId:7,cases:[{id:7,name:seed}],graph:{},graphLoading:false,graphInited:true,stats:{flows:0},lastGraph:{nodes:[],edges:[]}};
  const instance={done:false,exited:Promise.resolve(),child:{kill(){events.push('kill');}},mainErrors:{snapshot:()=>({})}};
  const {safeInstalledFailure}=require('./installed-titlebar-journey.cjs');
  const context=vm.createContext({assert,SEED_CASE:seed,report,timings:report.timings,stage:'syntheticStart',performance:{now:()=>0},
    page:{sessionId:'body'},state,layoutJob:null,window:{FundsCanvasEditor:{history:{status:()=>({busy:false})}}},
    location:{pathname:'/'},document:{readyState:'complete',querySelector:selector=>nodes.get(selector)||null},
    originalError,owned:[instance],reportOwned:true,reportPath:'SYN_REPORT',updateFixture:null,productionUpdate:false,presentationPreferences:null,
    finishWorkspaceGraphEventObservation:async()=>{},readyCases:async()=>{},
    analysisTitlebarPage:null,analysisPage:null,titlebarPage:null,
    safeInstalledFailure:error=>{seenErrors.push(error);return safeInstalledFailure(error);},safeJourneyFailure:()=>null,
    process:{exitCode:0,stdout:{write(){events.push('stdout');}},stderr:{write(){events.push('stderr');}}},
    fs:{writeFile:async(_file,value)=>{writes.push(JSON.parse(value));events.push('write-report');}},
    releaseStartupInspection:async()=>{events.push('release-startup');},
    cdp:{send:async method=>{assert.equal(method,'Page.close');events.push('close-page');},close(){events.push('close-cdp');}},
    bounded:async(promise,ms)=>{events.push(`bounded-${ms}`);const value=await promise;if(ms===3000&&diagnosticTimeout)throw new Error('TIMEOUT');return value;},
    until:async(action,ms)=>{assert.equal(ms,20000);events.push('wait-ready');const value=await action();if(!value||failure==='ready')throw originalError;},
    clickElement:async(expression,target,options)=>{
      assert.equal(expression,"[...document.querySelectorAll('.heading-actions button')].find(node=>node.textContent.trim()==='进入画图')");
      assert.equal(target,context.page);assert.equal(options.frame,true);events.push(`click-${report.workspaceGraphStep}`);
      if(failure==='click')throw originalError;
    }});
  const readyExpression=`location.pathname === '/' && typeof state === 'object' && !state.graphLoading
      && state.graphInited && state.cases.find(item => item.id === state.caseId)?.name === ${JSON.stringify(seed)}
      &&state.stats.flows===0&&state.lastGraph.nodes.length===0&&document.querySelector('#casesFrame').hidden
      &&document.querySelector('[data-nav-page="home"]').getAttribute('aria-current')==='page'`;
  context.evaluate=async expression=>{
    if(expression.includes('function readWorkspaceGraphDiagnostic')) {
      events.push('diagnostic');if(diagnosticThrows)throw new Error('SYN_PRIVATE_PROTOCOL_URL_AMOUNT');
      return diagnosticValue===undefined?vm.runInContext(expression,context):diagnosticValue;
    }
    assert.equal(expression,`document.readyState === 'complete' && (${readyExpression})`);
    assert.equal(report.workspaceGraphStep,'wait-ready');return vm.runInContext(expression,context);
  };
  vm.runInContext(source.slice(checkStart,checkEnd)+source.slice(readyStart,readyEnd),context);
  if(readerStart>=0&&readerEnd>readerStart)vm.runInContext(source.slice(readerStart,readerEnd),context);
  if(observation) {
    let uuid=0;context.require=name=>{assert.equal(name,'node:crypto');return {randomUUID:()=>`SYN-UUID-${++uuid}`};};
    context.workspaceGraphObservationKey=null;
    vm.runInContext(workspaceObservationSource(source),context);
    const evaluate=context.evaluate;
    context.evaluate=async expression=>{
      if(expression.includes('function installWorkspaceGraphEventObserver')) {observation.calls.push('install');return observation.run(expression);}
      if(expression.includes('function readAndDisposeWorkspaceGraphEventObserver')) {
        observation.calls.push('finish');const value=observation.run(expression);
        if(observation.finishThrows)throw new Error('SYN_PRIVATE_PROTOCOL');
        return observation.override===undefined?value:observation.override;
      }
      return evaluate(expression);
    };
  }
  const block=source.slice(blockStart,blockEnd);
  const action=block.slice(block.indexOf('async () => {')+'async () => {'.length);
  const originalTry=/\btry\s*\{([\s\S]*?)\}\s*finally\s*\{/.exec(action)?.[1]||action;
  assert.equal((originalTry.match(/\bawait clickElement\(/g)||[]).length,1,'the original return check retains its real click');
  assert.equal((originalTry.match(/\bawait readyDocument\(/g)||[]).length,1,'the original return check retains its strict ready wait');
  return {context,report,events,writes,seenErrors,originalError,nodes,state,
    read:()=>{assert.equal(typeof context.readWorkspaceGraphDiagnostic,'function','W08 must expose its read-only fixed projection');return context.readWorkspaceGraphDiagnostic(seed);},
    run:()=>vm.runInContext(`(async()=>{${otherStage?"await check('syntheticOther',async()=>{throw originalError;});":block}report.status='passed';})()${source.slice(tailStart+4)}`,context)};
}

for(const newline of ['LF','CRLF'])test(`the actual W08 projection is fixed, read-only and distinguishes strict mismatches with ${newline} source`,async()=>{
  const f=await graphReturnFixture({newline});
  assert.deepEqual(plain(f.read()),Object.fromEntries(WORKSPACE_GRAPH_KEYS.map(key=>[key,true])));
  f.state.caseId='7';f.state.stats.flows='0';
  let value=plain(f.read());assert.equal(value.caseNameStrictMatch,false);assert.equal(value.caseNameCoercedMatch,true);
  assert.equal(value.flowsZeroStrict,false);assert.equal(value.flowsZeroCoerced,true);assert.equal(value.caseSelectorMatches,true);
  f.state.caseId={private:'SYN_PRIVATE_ID'};f.state.stats.flows=[];f.state.lastGraph={nodes:{},edges:null};
  value=plain(f.read());for(const key of ['caseNameCoercedMatch','caseSelectorMatches','flowsZeroCoerced','nodesEmpty','edgesEmpty'])assert.equal(value[key],null,key);
  delete f.context.state;value=plain(f.read());assert.equal(value.statePresent,false);assert.equal(value.casesFramePresent,true);
  for(const key of WORKSPACE_GRAPH_KEYS.slice(3,13))assert.equal(value[key],null,key);
  delete f.context.document;delete f.context.location;delete f.context.layoutJob;f.context.window={};
  value=plain(f.read());assert.deepEqual(Object.keys(value),WORKSPACE_GRAPH_KEYS);assert.equal(value.statePresent,false);
  for(const key of WORKSPACE_GRAPH_KEYS.filter(key=>key!=='statePresent'))assert.equal(value[key],null,key);
  assert.deepEqual(f.events,[],'a diagnostic read cannot invoke CDP, navigation or cleanup');
  assert.doesNotMatch(JSON.stringify(value),/SYN_PRIVATE/);
});

for(const newline of ['LF','CRLF'])test(`the actual W08 check and catch/finally preserve success, failure and cleanup with ${newline} source`,async()=>{
  const passed=await graphReturnFixture({newline});await passed.run();
  assert.equal(passed.report.status,'passed');assert.equal(passed.report.workspaceGraphStep,'complete');
  assert.equal(passed.report.checks.workspaceGraphLinkOpened,true);assert.equal(passed.report.workspaceGraphDiagnostic,undefined);
  assert.equal(passed.events.includes('diagnostic'),false);
  for(const failure of ['click','ready']) {
    const f=await graphReturnFixture({newline,failure});await f.run();
    assert.equal(f.report.status,'failed');assert.equal(f.report.failedCheck,'workspaceGraphLinkOpened');
    assert.equal(f.report.workspaceGraphStep,failure==='click'?'click':'wait-ready');assert.equal(f.context.process.exitCode,1);
    assert.equal(f.report.failure.kind,failure==='click'?'CDP_TIMEOUT':'TIMEOUT');assert.equal(f.seenErrors[0],f.originalError);
    assert.deepEqual(plain(f.report.workspaceGraphDiagnostic),{available:true,...Object.fromEntries(WORKSPACE_GRAPH_KEYS.map(key=>[key,true]))});
    assert.equal(f.events.filter(item=>item==='diagnostic').length,1);assert.ok(f.events.includes('bounded-3000'));
    assert.ok(f.events.indexOf('diagnostic')<f.events.indexOf('release-startup'));
    assert.deepEqual(f.events.slice(-8),['release-startup','close-page','bounded-5000','close-cdp','kill','bounded-10000','write-report','stderr']);
    assert.equal(f.writes.length,1);assert.equal(f.writes[0].failure.kind,f.report.failure.kind);
  }
  const unrelated=await graphReturnFixture({newline,otherStage:true});await unrelated.run();
  assert.equal(unrelated.report.failedCheck,'syntheticOther');assert.equal(unrelated.report.workspaceGraphDiagnostic,undefined);
  assert.equal(unrelated.events.includes('diagnostic'),false);assert.equal(unrelated.context.process.exitCode,1);
});

for(const newline of ['LF','CRLF'])test(`the actual W08 failure projection rejects unavailable and malicious diagnostics with ${newline} source`,async()=>{
  const valid=()=>Object.fromEntries(WORKSPACE_GRAPH_KEYS.map(key=>[key,true]));
  const unknown={...valid(),private:'SYN_PRIVATE_NAME_ID_AMOUNT_URL'},invalid={...valid(),graphPresent:'SYN_PRIVATE_NAME_ID_AMOUNT_URL'},missing=valid();delete missing.homeCurrent;
  const getter=valid();Object.defineProperty(getter,'homeCurrent',{enumerable:true,get(){throw new Error('SYN_PRIVATE_GETTER');}});
  for(const options of [{diagnosticThrows:true},{diagnosticTimeout:true},{diagnosticValue:null},{diagnosticValue:unknown},{diagnosticValue:invalid},{diagnosticValue:missing},{diagnosticValue:getter}]) {
    const f=await graphReturnFixture({newline,failure:'ready',...options});await f.run();
    assert.deepEqual(plain(f.report.workspaceGraphDiagnostic),{available:false});assert.equal(f.report.failure.kind,'TIMEOUT');
    assert.equal(f.seenErrors[0],f.originalError);assert.equal(f.report.failedCheck,'workspaceGraphLinkOpened');assert.equal(f.context.process.exitCode,1);
    assert.ok(f.events.includes('bounded-3000'));assert.equal(f.writes.length,1);assert.ok(f.events.includes('kill'));
    assert.doesNotMatch(JSON.stringify(f.writes),/SYN_PRIVATE/);
  }
  const f=await graphReturnFixture({newline,failure:'ready'});
  Object.defineProperty(f.state,'graph',{get(){throw new Error('SYN_PRIVATE_PAGE_GETTER');}});
  await f.run();assert.deepEqual(plain(f.report.workspaceGraphDiagnostic),{available:false});
  assert.equal(f.report.failure.kind,'TIMEOUT');assert.doesNotMatch(JSON.stringify(f.writes),/SYN_PRIVATE/);
  const singleRead=valid();let reads=0;Object.defineProperty(singleRead,'homeCurrent',{enumerable:true,get(){reads++;return true;}});
  const once=await graphReturnFixture({newline,failure:'ready',diagnosticValue:singleRead});await once.run();
  assert.equal(once.report.workspaceGraphDiagnostic.available,true);assert.equal(reads,1,'the host copies each approved value only once');
});

const WORKSPACE_EVENT_EMPTY={available:true,pointerDown:false,pointerUp:false,click:false,trustedClick:null,
  requestSeen:false,requestBindingCurrent:null,resultSeen:false,resultOk:null,resultBindingRejected:null,
  resultBusyRejected:null,resultOtherRejected:null,targetConnected:true,targetEnabled:true,alertPresent:false};
function workspaceObservationSource(source) {
  const start=source.indexOf('function installWorkspaceGraphEventObserver('),end=source.indexOf('function readWorkspaceGraphDiagnostic(',start);
  assert.ok(start>=0&&end>start,'W10 must provide its actual passive observation helpers');
  return source.slice(start,end);
}
async function workspaceEventFixture(newline='LF',{failAdd=0,failRemove=false}={}) {
  let source=(await fs.readFile(path.join(__dirname,'installed-app-smoke.cjs'),'utf8')).replace(/\r\n/g,'\n');
  if(newline==='CRLF')source=source.replace(/\n/g,'\r\n');
  const listeners=[],calls=[],sideEffects=[];let additions=0,removalFailed=false,alert=false;
  const forbidden=()=>{sideEffects.push('forbidden');throw new Error('SYN_PRIVATE_SIDE_EFFECT');};
  const target=()=>({
    addEventListener(type,callback,options){
      assert.equal(options.capture,true);assert.equal(options.passive,true);
      if(++additions===failAdd)throw new Error('SYN_PRIVATE_INSTALL');
      listeners.push({target:this,type,callback,options});
    },
    removeEventListener(type,callback,options){
      assert.equal(typeof options==='boolean'?options:options.capture,true);
      const index=listeners.findIndex(value=>value.target===this&&value.type===type&&value.callback===callback);
      if(index>=0)listeners.splice(index,1);
      if(failRemove&&!removalFailed){removalFailed=true;throw new Error('SYN_PRIVATE_DISPOSE');}
    },
    emit(type,properties={}){const event={type,target:this,isTrusted:false,preventDefault:forbidden,stopPropagation:forbidden,...properties};
      for(const value of [...listeners])if(value.target===this&&value.type===type)value.callback(event);},
    postMessage:forbidden,focus:forbidden,scrollTo:forbidden,
  });
  const host=target(),child=target(),button=Object.assign(target(),{textContent:'进入画图',isConnected:true,disabled:false,
    setAttribute:forbidden,scrollIntoView:forbidden,contains(node){return node===this||node===descendant;}}),descendant={};
  const childDocument={querySelector:selector=>selector==='[role="alert"]'?(alert?{}:null):selector==='.heading-actions button'?button:null,
    querySelectorAll:selector=>selector==='.heading-actions button'?[button]:[],defaultView:child};
  const frame={contentWindow:child,contentDocument:childDocument,isConnected:true};
  const document={querySelector:selector=>selector==='#casesFrame'?frame:null,defaultView:host};
  host.document=document;child.document=childDocument;child.parent=host;host.parent=host;
  const origin='http://127.0.0.1:12345';host.location={origin};child.location={origin};
  const state={caseId:7,caseGeneration:11};
  const context=vm.createContext({window:host,document,location:host.location,state});
  vm.runInContext(workspaceObservationSource(source),context);
  const message=(type,overrides={})=>({channel:'fundstrace:workspace',version:1,type,
    ...(type==='request'?{action:'open-graph',caseId:'7',generation:11,payload:{}}:{ok:true}),requestId:'SYN_PRIVATE_REQUEST',...overrides});
  return {context,host,child,button,descendant,frame,document,childDocument,state,origin,listeners,calls,sideEffects,
    install:key=>context.installWorkspaceGraphEventObserver(key),finish:key=>plain(context.readAndDisposeWorkspaceGraphEventObserver(key)),
    run:expression=>vm.runInContext(expression,context),alert(value){alert=value;},
    request(overrides={},event={}){host.emit('message',{origin,source:child,data:message('request',overrides),...event});},
    result(overrides={},event={}){child.emit('message',{origin,source:host,data:message('result',overrides),...event});},
  };
}

for(const newline of ['LF','CRLF'])test(`the actual passive observer follows only its button and first authenticated request/result with ${newline} source`,async()=>{
  const f=await workspaceEventFixture(newline),key='SYN observer';assert.equal(f.install(key),true);
  assert.equal(Object.isFrozen(f.host[key]),true);assert.equal(f.listeners.length,5);
  assert.deepEqual(plain(f.host[key].read()),WORKSPACE_EVENT_EMPTY);
  f.button.emit('click',{target:{},isTrusted:true});f.result();
  for(const event of [{origin:'https://invalid.example'},{source:{}},{data:{channel:'invalid'}}])f.request({},event);
  for(const overrides of [{version:2},{type:'ready'},{action:'select-case'},{requestId:1},{requestId:'x'.repeat(161)}])f.request(overrides);
  assert.deepEqual(plain(f.host[key].read()),WORKSPACE_EVENT_EMPTY);
  f.button.emit('pointerdown',{target:f.descendant});f.button.emit('pointerup',{target:f.descendant});
  f.button.emit('click',{target:f.descendant,isTrusted:false});f.button.emit('click',{target:f.descendant,isTrusted:true});f.button.emit('click',{isTrusted:false});
  f.request({requestId:''});f.state.caseGeneration=12;f.request({requestId:'second',caseId:'OLD',generation:0});
  for(const event of [{origin:'https://invalid.example'},{source:{}}])f.result({requestId:''},event);
  for(const overrides of [{version:2},{type:'request'},{ok:'SYN_PRIVATE'}])f.result({requestId:'',...overrides});
  assert.equal(f.host[key].read().resultSeen,false);
  f.result({requestId:'second',ok:false,error:'SYN_PRIVATE'});f.result({requestId:'',ok:true});
  f.result({requestId:'',ok:false,error:'案件已变化，请重新操作'});
  const expected={...WORKSPACE_EVENT_EMPTY,pointerDown:true,pointerUp:true,click:true,trustedClick:true,requestSeen:true,requestBindingCurrent:true,
    resultSeen:true,resultOk:true,resultBindingRejected:false,resultBusyRejected:false,resultOtherRejected:false};
  assert.deepEqual(f.finish(key),expected);assert.equal(f.listeners.length,0);assert.equal(Object.hasOwn(f.host,key),false);
  assert.deepEqual(f.sideEffects,[]);assert.doesNotMatch(JSON.stringify(expected),/SYN_PRIVATE|requestId|payload|origin/);
  for(const [error,field] of [['案件已变化，请重新操作','resultBindingRejected'],['正在切换案件，请稍后操作','resultBusyRejected'],['SYN_PRIVATE_REASON','resultOtherRejected']]) {
    const g=await workspaceEventFixture(newline);g.install(key);g.request({caseId:7});g.result({ok:false,error});g.alert(true);g.button.disabled=true;
    const value=g.finish(key);assert.equal(value.requestBindingCurrent,false);assert.equal(value[field],true);assert.equal(value.resultOk,false);
    assert.equal(value.targetEnabled,false);assert.equal(value.alertPresent,true);assert.doesNotMatch(JSON.stringify(value),/SYN_PRIVATE/);
  }
});

for(const newline of ['LF','CRLF'])test(`the actual observer cleans partial installs, navigation, foreign keys and read/dispose failures with ${newline} source`,async()=>{
  const key='SYN observer',foreign={tag:'foreign',read(){throw new Error('SYN_PRIVATE_FOREIGN');}};
  const occupied=await workspaceEventFixture(newline);occupied.host[key]=foreign;
  assert.equal(occupied.install(key),false);assert.equal(occupied.host[key],foreign);assert.equal(occupied.listeners.length,0);
  assert.deepEqual(occupied.finish(key),{available:false});assert.equal(occupied.host[key],foreign);
  const inherited=await workspaceEventFixture(newline);Object.setPrototypeOf(inherited.host,{[key]:foreign});
  assert.equal(inherited.install(key),false);assert.equal(inherited.listeners.length,0);
  const partial=await workspaceEventFixture(newline,{failAdd:3});assert.equal(partial.install(key),false);
  assert.equal(partial.listeners.length,0);assert.equal(Object.hasOwn(partial.host,key),false);
  for(const change of [f=>{f.document.querySelector=()=>null;},f=>{f.frame.contentDocument=null;},
    f=>{f.childDocument.querySelectorAll=()=>[];},f=>{Object.defineProperty(f.frame,'contentWindow',{get(){throw new Error('SYN_PRIVATE_CROSS_ORIGIN');}});}]) {
    const f=await workspaceEventFixture(newline);change(f);assert.equal(f.install(key),false);assert.equal(f.listeners.length,0);
  }
  for(const change of [f=>{f.context.document={};f.host.document=f.context.document;},f=>{f.frame.contentDocument={};},
    f=>{f.frame.contentWindow={};},f=>{f.document.querySelector=()=>({});}]) {
    const f=await workspaceEventFixture(newline);f.install(key);change(f);assert.deepEqual(f.finish(key),{available:false});
    assert.equal(f.listeners.length,0);assert.equal(Object.hasOwn(f.host,key),false);
  }
  const replaced=await workspaceEventFixture(newline);replaced.install(key);replaced.button.isConnected=false;
  assert.equal(replaced.finish(key).targetConnected,false);assert.equal(replaced.listeners.length,0);
  const swapped=await workspaceEventFixture(newline);swapped.install(key);const own=swapped.host[key];swapped.host[key]=foreign;own.dispose();
  assert.equal(swapped.host[key],foreign);assert.equal(swapped.listeners.length,0);
  const getter=await workspaceEventFixture(newline);getter.install(key);Object.defineProperty(getter.button,'disabled',{get(){throw new Error('SYN_PRIVATE_GETTER');}});
  assert.deepEqual(getter.finish(key),{available:false});assert.equal(getter.listeners.length,0);
  const removal=await workspaceEventFixture(newline,{failRemove:true});removal.install(key);
  assert.deepEqual(removal.finish(key),{available:false});assert.equal(removal.listeners.length,0);assert.equal(Object.hasOwn(removal.host,key),false);
});

for(const newline of ['LF','CRLF'])test(`the actual Node observation helpers preserve W08 errors, successful silence and global cleanup with ${newline} source`,async()=>{
  const source=(await fs.readFile(path.join(__dirname,'installed-app-smoke.cjs'),'utf8')).replace(/\r\n/g,'\n');
  const entry=source.slice(source.indexOf("  await check('installedReactWorkspaceOpened'"),source.indexOf("  await viewportMatrix('reactWorkspaceLayout'"));
  assert.ok(entry.lastIndexOf('await startWorkspaceGraphEventObservation()')>entry.lastIndexOf('await readyDocument('),'installation stays at the established entry end');
  for(const options of [{},{failure:'click'},{failure:'ready'},{otherStage:true},{failure:'ready',finishThrows:true}]) {
    const observation=await workspaceEventFixture(newline);observation.finishThrows=options.finishThrows;
    const f=await graphReturnFixture({newline,...options,observation});await f.context.startWorkspaceGraphEventObservation();
    assert.equal(observation.listeners.length,5);await f.run();
    assert.equal(observation.listeners.length,0);assert.deepEqual(observation.calls,['install','finish']);
    assert.ok(f.events.filter(value=>value==='bounded-3000').length>=2);
    if(options.failure) {
      assert.equal(f.report.status,'failed');assert.equal(f.report.failedCheck,'workspaceGraphLinkOpened');assert.equal(f.context.process.exitCode,1);
      assert.equal(f.seenErrors[0],f.originalError);assert.equal(f.report.failure.kind,options.failure==='click'?'CDP_TIMEOUT':'TIMEOUT');
      assert.deepEqual(plain(f.report.workspaceGraphEventDiagnostic),options.finishThrows?{available:false}:WORKSPACE_EVENT_EMPTY);
    } else assert.equal(f.report.workspaceGraphEventDiagnostic,undefined);
    assert.ok(f.events.includes('kill'));assert.equal(f.writes.length,1);assert.doesNotMatch(JSON.stringify(f.writes),/SYN_PRIVATE/);
  }
  const valid=()=>({...WORKSPACE_EVENT_EMPTY}),unknown={...valid(),private:'SYN_PRIVATE'},invalid={...valid(),click:'SYN_PRIVATE'},missing=valid();delete missing.click;
  const getter=valid();Object.defineProperty(getter,'click',{enumerable:true,get(){throw new Error('SYN_PRIVATE_GETTER');}});
  for(const override of [null,unknown,invalid,missing,getter]) {
    const observation=await workspaceEventFixture(newline);observation.override=override;
    const f=await graphReturnFixture({newline,failure:'ready',observation});await f.context.startWorkspaceGraphEventObservation();await f.run();
    assert.deepEqual(plain(f.report.workspaceGraphEventDiagnostic),{available:false});assert.equal(f.report.failure.kind,'TIMEOUT');
    assert.equal(f.seenErrors[0],f.originalError);assert.equal(observation.listeners.length,0);assert.doesNotMatch(JSON.stringify(f.writes),/SYN_PRIVATE/);
  }
  const observation=await workspaceEventFixture(newline),f=await graphReturnFixture({newline,observation});
  await f.context.finishWorkspaceGraphEventObservation(true);assert.deepEqual(plain(f.report.workspaceGraphEventDiagnostic),{available:false});
  delete f.report.workspaceGraphEventDiagnostic;
  await f.context.startWorkspaceGraphEventObservation();const first=Object.keys(observation.host).find(key=>key.startsWith('__fundsTraceWorkspaceGraph_'));
  await f.context.finishWorkspaceGraphEventObservation(false);await f.context.startWorkspaceGraphEventObservation();
  const second=Object.keys(observation.host).find(key=>key.startsWith('__fundsTraceWorkspaceGraph_'));
  assert.ok(first&&second&&first!==second);await f.context.finishWorkspaceGraphEventObservation(false);
  assert.equal(f.report.workspaceGraphEventDiagnostic,undefined);assert.equal(observation.listeners.length,0);
  await f.context.startWorkspaceGraphEventObservation();
  f.context.bounded=async(promise,ms)=>{assert.equal(ms,3000);await promise;throw new Error('TIMEOUT');};
  await f.context.finishWorkspaceGraphEventObservation(true);assert.deepEqual(plain(f.report.workspaceGraphEventDiagnostic),{available:false});
  assert.equal(observation.listeners.length,0);
});

for(const newline of ['LF','CRLF'])test(`the actual native graph entry waits for its enabled iframe button and preserves failure cleanup with ${newline} source`,async()=>{
  for(const mode of ['enabled','disabled','wait-error','post-ready']) {
    const observation=await workspaceEventFixture(newline),f=await graphReturnFixture({newline,failure:mode==='post-ready'?'ready':null,observation});
    const native=await casesFixture(),button=native.childDocument.querySelector('#action'),samples=[];
    button.textContent='进入画图';button.disabled=true;native.document.readyState='complete';
    native.childDocument.querySelectorAll=selector=>{assert.equal(selector,'.heading-actions button');return [button];};
    const start=native.source.indexOf('async function readyDocument('),end=native.source.indexOf('async function viewportMatrix(',start);
    assert.ok(start>=0&&end>start);vm.runInContext(native.source.slice(start,end),native.context);
    const failure=mode==='wait-error'?new Error('CDP_TIMEOUT'):mode==='post-ready'?f.originalError:new Error('TIMEOUT');
    native.context.until=async(predicate,ms)=>{
      assert.equal(ms,20000);
      for(let poll=0;poll<3;poll++) {
        samples.push(button.disabled);if(mode==='wait-error')throw failure;
        if(await predicate())return true;
        if(mode!=='disabled'){button.disabled=false;observation.button.disabled=false;}
      }
      throw failure;
    };
    f.context.readyCases=async(...args)=>{f.events.push('wait-enabled');assert.equal(f.report.workspaceGraphStep,'wait-enabled');return native.context.readyCases(...args);};
    const click=f.context.clickElement;
    f.context.clickElement=async(expression,target,options)=>{
      assert.equal(button.disabled,false,'the actual entry must wait before attempting a disabled native button');
      await click(expression,target,options);await native.context.clickElement(expression,native.page,options);
    };
    const send=native.context.cdp.send;
    native.context.cdp.send=async(...args)=>{
      const result=await send(...args);
      if(args[0]==='Input.dispatchMouseEvent') {
        observation.button.emit(args[1].type==='mousePressed'?'pointerdown':'pointerup',{isTrusted:true});
        if(args[1].type==='mouseReleased')observation.button.emit('click',{isTrusted:true});
      }
      return result;
    };
    await f.context.startWorkspaceGraphEventObservation();observation.button.disabled=true;
    await f.run();assert.equal(observation.listeners.length,0);assert.ok(f.events.includes('kill'));assert.equal(f.writes.length,1);
    const blocked=mode==='disabled'||mode==='wait-error';
    assert.deepEqual(f.events.filter(value=>['wait-enabled','click-click','wait-ready'].includes(value)),blocked?['wait-enabled']:['wait-enabled','click-click','wait-ready']);
    assert.equal(native.calls.length,blocked?0:2,'only an enabled control reaches the real CDP pointer helper');
    if(mode==='enabled') {
      assert.equal(f.report.status,'passed');assert.equal(f.report.workspaceGraphStep,'complete');assert.equal(f.report.workspaceGraphEventDiagnostic,undefined);
      assert.deepEqual(samples,[true,false]);
    } else {
      assert.equal(f.report.status,'failed');assert.equal(f.report.failedCheck,'workspaceGraphLinkOpened');assert.equal(f.context.process.exitCode,1);
      assert.equal(f.report.workspaceGraphStep,blocked?'wait-enabled':'wait-ready');assert.equal(f.seenErrors[0],failure);
      assert.equal(f.report.failure.kind,mode==='wait-error'?'CDP_TIMEOUT':'TIMEOUT');assert.equal(f.report.workspaceGraphEventDiagnostic.available,true);
      assert.equal(f.report.workspaceGraphEventDiagnostic.targetEnabled,!blocked);
    }
  }
});
