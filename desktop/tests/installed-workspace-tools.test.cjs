'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const vm=require('node:vm');

function workspace({collapsed=false,slotLeft=56,width=1024,scale=.75625}={}) {
  const nodes=new Map(),box=(left,top,width,height)=>({left,top,right:left+width,bottom:top+height,width,height});
  const node=(id,bounds,parent=null)=>{
    const value={id,bounds,parentElement:parent,hidden:false,clientWidth:bounds.width,scrollWidth:bounds.width,
      style:{visibility:'visible',overflowX:'visible',overflowY:'visible'},selectors:new Map(),children:[],
      getBoundingClientRect(){return this.bounds;},getClientRects(){return this.hidden?[]:[this.bounds];},
      querySelector(selector){return this.selectors.get(selector)||null;},
      querySelectorAll(selector){return selector==='button,input,select,a'?this.children:[];},
      contains(other){return other===this||this.children.includes(other);}};
    nodes.set(id,value);return value;
  };
  const slot=node('graphPage',box(slotLeft,0,width-slotLeft,688));
  const toolbar=node('editorToolbar',box(slotLeft,0,1280*scale,(collapsed?66:148)*scale),slot);
  toolbar.clientWidth=1280;toolbar.style.height=String(collapsed?66:148);
  toolbar.style.getPropertyValue=key=>key==='--toolbar-fit-scale'?String(scale):'';
  toolbar.classList={contains:name=>name==='is-collapsed'&&collapsed};
  const header=node('ribbon-tabbar',box(slotLeft,0,1280*scale,35*scale),toolbar);
  header.clientWidth=1280;header.scrollWidth=1280;header.style.overflowX='auto';
  const panel=node('ribbonPanel-common',box(slotLeft,35*scale,1280*scale,112*scale),toolbar);
  panel.clientWidth=1280;panel.scrollWidth=1280;panel.hidden=collapsed;panel.style.overflowX='hidden';
  const compact=node('ribbonCompact',box(slotLeft,35*scale,1280*scale,31*scale),toolbar);
  compact.clientWidth=1280;compact.scrollWidth=1280;compact.hidden=!collapsed;compact.style.overflowX='hidden';
  const required=['caseSelect','canvasSel','btnCanvasSave','btnUndo','btnRedo','btnWorkspaceView',
    'ribbonTab-common','ribbonTab-trace','ribbonTab-style','ribbonTab-view'];
  for(const [index,id] of required.entries()) header.children.push(node(id,box(slotLeft+(index*60+8)*scale,3*scale,50*scale,28*scale),header));
  const inspect=node('inspect',box(slotLeft+1000*scale,3*scale,60*scale,28*scale),header);
  const fold=node('fold',box(slotLeft+1100*scale,3*scale,90*scale,28*scale),header);
  header.children.push(inspect,fold);header.selectors.set('[data-ribbon-command="inspectSelection"]',inspect);
  header.selectors.set('[data-ribbon-collapse]',fold);
  const action=node('action',box(slotLeft+8*scale,42*scale,60*scale,64*scale),panel);
  action.style.flexDirection='column';action.style.fontSize='12px';panel.children.push(action);
  const icon=node('icon',box(slotLeft+8*scale,42*scale,27*scale,27*scale),action);icon.style.width='27px';
  action.selectors.set('.ui-icon',icon);panel.selectors.set('.ribbon-action',action);
  const compactAction=node('compactAction',box(slotLeft+8*scale,36*scale,60*scale,28*scale),compact);
  compact.children.push(compactAction);
  const table=node('flowTableScroll',box(slotLeft,250,560,400));table.style.overflowX='auto';table.tabIndex=0;
  const document={querySelector:selector=>({'#editorToolbar':toolbar,'#editorToolbar .ribbon-tabbar':header,
    '#ribbonCompact':compact,'#flowTableScroll':table}[selector]||null),
    querySelectorAll:selector=>({'#editorToolbar .ribbon-panel:not([hidden])':collapsed?[]:[panel],
      '#flowTable thead th':Array(7).fill({}),'.flow-horizontal-scroll':[]}[selector]||[]),
    getElementById:id=>nodes.get(id)||null};
  return {nodes,document,innerWidth:width,innerHeight:688,devicePixelRatio:1,getComputedStyle:value=>value.style};
}
async function toolsFixture(options) {
  const source=await fs.readFile(path.join(__dirname,'installed-app-smoke.cjs'),'utf8');
  const start=source.indexOf('function readWorkspaceTools() {'),end=source.indexOf('function argumentsOf() {',start);
  assert.ok(start>=0&&end>start);
  const fixture=workspace(options),context=vm.createContext({...fixture,assert});
  vm.runInContext(source.slice(start,end),context);
  return {...fixture,read:()=>context.readWorkspaceTools(),readImport:()=>context.readImportEntry(),check:value=>context.assertWorkspaceTools(value,
    {tab:'common',collapsed:options?.collapsed===true})};
}

test('the actual installed projection sees the current persistent top row and scaled slot with no legacy topbar',async()=>{
  for(const options of [{},{scale:1,width:1336},{collapsed:true}]) {
    const fixture=await toolsFixture(options),value=fixture.read();
    assert.equal(fixture.nodes.has('topbar'),false);
    assert.equal(value.bars[0].id,'ribbon-tabbar');
    assert.equal(value.bars.length,2);
    assert.equal(value.clippedControls,0);
    fixture.check(value);
  }
});

test('the actual installed assertion rejects toolbar overflow into its rail despite fitting the viewport',async()=>{
  const fixture=await toolsFixture(),toolbar=fixture.nodes.get('editorToolbar');
  toolbar.bounds={...toolbar.bounds,left:0,width:1024,right:1024};
  assert.throws(()=>fixture.check(fixture.read()),/WORKSPACE_TOOLBAR_SLOT_CLIPPED/);
});

test('visible controls must fit their own row and every clipped parent, not merely the viewport',async()=>{
  for(const target of ['caseSelect','action','compactAction']) {
    const fixture=await toolsFixture({collapsed:target==='compactAction'}),control=fixture.nodes.get(target);
    control.bounds={...control.bounds,left:20,right:50,width:30};
    const value=fixture.read();
    assert.ok(value.clippedControls>0,'a control behind the rail is still within the viewport');
    assert.throws(()=>fixture.check(value),/WORKSPACE_TOOLBAR_CLIPPED/);
  }
  const fixture=await toolsFixture(),control=fixture.nodes.get('caseSelect');
  const wrapper={bounds:{left:56,top:0,right:80,bottom:35,width:24,height:35},
    parentElement:control.parentElement,style:{overflowX:'hidden',overflowY:'visible'},
    getBoundingClientRect(){return this.bounds;}};
  control.parentElement=wrapper;
  assert.throws(()=>fixture.check(fixture.read()),/WORKSPACE_TOOLBAR_CLIPPED/);
});

test('row overflow, missing persistent commands and the seven-column table remain required',async()=>{
  const overflow=await toolsFixture();overflow.nodes.get('ribbon-tabbar').scrollWidth=1290;
  assert.throws(()=>overflow.check(overflow.read()),/WORKSPACE_TOOLBAR_OVERFLOW/);
  const missing=await toolsFixture();missing.nodes.get('btnUndo').hidden=true;
  assert.throws(()=>missing.check(missing.read()),/WORKSPACE_TOOLBAR_CONTROLS_MISSING/);
  const table=await toolsFixture();table.nodes.get('flowTableScroll').tabIndex=-1;
  assert.throws(()=>table.check(table.read()),{code:'ERR_ASSERTION'});
});

test('native file input remains enabled and exactly associated with its visible label despite its clipped size',async()=>{
  const fixture=await toolsFixture(),input={id:'synthetic-file',type:'file',accept:'.csv,.tsv,.txt,.xlsx',disabled:false};
  const label={htmlFor:input.id,control:input,querySelector:()=>input};
  fixture.nodes.set(input.id,input);
  const query=fixture.document.querySelector;
  fixture.document.querySelector=selector=>selector==='dialog[open] label.file-drop'?label:query(selector);
  assert.deepEqual(JSON.parse(JSON.stringify(fixture.readImport())),
    {associated:true,type:'file',accept:'.csv,.tsv,.txt,.xlsx',enabled:true});
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

test('the actual inspection waits for settled height and slot scaling without waiting away clipping failures',async()=>{
  const source=await fs.readFile(path.join(__dirname,'installed-app-smoke.cjs'),'utf8');
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
        if(++reads===1){if(pending==='height')value.cssHeight=100;else value.scale=1;}
        return value;
      },
      until:async check=>{for(let attempt=0;attempt<3;attempt++)if(await check())return;throw new Error('TIMEOUT');}});
    vm.runInContext(`${source.slice(start,end)};globalThis.inspect=inspect;`,context);
    if(clipping)await assert.rejects(context.inspect('synthetic',{tab:'common',collapsed:false}),/WORKSPACE_TOOLBAR_CLIPPED/);
    else await context.inspect('synthetic',{tab:'common',collapsed:false});
    assert.equal(reads,2);assert.equal(samples.length,1);assert.equal(samples[0].cssHeight,148);
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
      check:async(_name,action)=>action(),readyCases:async()=>{},
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
