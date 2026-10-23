import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';
import test from 'node:test';

const require=createRequire(import.meta.url),p=require('../static/graph-presentation.js');
const source=name=>readFileSync(new URL(`../static/${name}.js`,import.meta.url),'utf8');
const plain=value=>JSON.parse(JSON.stringify(value));

test('display choices round trip independently of transaction facts and arbitrary external icons',()=>{
  const input={nodeStyles:{A:{shape:'ellipse',icon:'organization',stroke:'#123456',url:'https://invalid.local/icon'}},
    nodeLabels:{A:{underline:true}},edgeLabels:{'A=>B':{underline:false}},pinnedAccounts:['A','A',3],
    groups:[{id:'group-1',name:' 待核查 ',members:['A','B','A'],color:'#abcdef',notes:' 人工观察 '},{id:'group-1',name:'duplicate'}],
    timeWindow:{start:'2026-10-01T12:30',end:'2026-10-23',includeUndated:false},transactions:[{amount:'999999999.99'}]};
  const safe=p.create(input);
  assert.deepEqual(safe.nodeStyles.A,{stroke:'#123456',shape:'ellipse',icon:'organization'});
  assert.deepEqual(safe.pinnedAccounts,['A']);
  assert.deepEqual(safe.groups,[{id:'group-1',name:'待核查',members:['A','B'],color:'#abcdef',notes:'人工观察'}]);
  assert.deepEqual(safe.timeWindow,{start:'2026-10-01 12:30',end:'2026-10-23',includeUndated:false});
  assert.deepEqual(p.create(plain(safe)),safe);
  assert.equal(safe.nodeLabels.A.underline,true);assert.equal(safe.edgeLabels['A=>B'].underline,false);
  assert.deepEqual(p.sanitizeNode({shape:'script',icon:'https://invalid.local',size:1000}),{});
  assert.equal(JSON.stringify(safe).includes('transactions'),false);
  safe.groups[0].members.push('C');assert.deepEqual(input.groups[0].members,['A','B','A']);
  assert.deepEqual(p.create({timeWindow:{start:'2026-01-01T10:00:00.1',end:'2026-01-01 10:00:00.123456'}}).timeWindow,
    {start:'2026-01-01 10:00:00.1',end:'2026-01-01 10:00:00.123456',includeUndated:true},'timeline microseconds survive saved playback bounds');
});

function fakeItem() {
  const children=[],model={id:'A',label:'Synthetic name\nA',size:40};
  const group={get:name=>name==='children'?children:null,addShape(type,{name,attrs}){
    let visible=true;const data={...attrs},shape={get:key=>key==='name'?name:key==='type'?type:key==='visible'?visible:null,
      attr(key,value){if(typeof key==='object')Object.assign(data,key);else if(arguments.length===2)data[key]=value;else return data[key];},
      getBBox:()=>({minX:-30,maxX:30,minY:25,maxY:57}),show(){visible=true;},hide(){visible=false;},
      remove(){const index=children.indexOf(shape);if(index>=0)children.splice(index,1);}};
    children.push(shape);return shape;
  }};
  const text=group.addShape('text',{name:'text-shape',attrs:{text:model.label,fill:'#123456'}});
  return {model,children,text,getModel:()=>model,getContainer:()=>group,find:name=>children.find(shape=>shape.get('name')===name)};
}

test('shared live/offline decorations follow real label bounds and are removed with hidden labels',()=>{
  const item=fakeItem(),options={pinned:true,groups:[{id:'g',name:'人工分组',color:'#336699'}]};
  p.decorateNode(item,{icon:'person'},options);p.decorateLabel(item,{underline:true});
  assert.ok(item.find('funds-node-icon').attr('path').length>0);
  assert.equal(item.find('funds-node-group').attr('text'),'分组：人工分组');
  assert.equal(item.find('funds-node-group').attr('y'),62);
  assert.deepEqual(item.find('funds-label-underline').attr('path'),[['M',-30,40],['L',30,40],['M',-30,56],['L',30,56]]);
  p.decorateNode(item,{icon:'person'},options);p.decorateLabel(item,{underline:true});
  assert.equal(item.children.filter(shape=>shape.get('name')==='funds-node-icon').length,1);
  item.text.getBBox=()=>({minX:70,maxX:130,minY:125,maxY:157});
  p.decorateLabel(item,{underline:true});
  assert.deepEqual(item.find('funds-label-underline').attr('path'),[['M',70,140],['L',130,140],['M',70,156],['L',130,156]],'underline follows relocated label geometry');
  item.model.label=undefined;p.decorateNode(item,{icon:'none'},{groups:options.groups});p.decorateLabel(item,{underline:true});
  for(const name of ['funds-node-icon','funds-node-pin','funds-node-group','funds-label-underline'])assert.equal(item.find(name),undefined);
});

function layoutApi() {
  const env={};vm.createContext(env);vm.runInContext(source('graph-layout'),env);vm.runInContext(source('graph-layout-quality'),env);return env;
}

test('all layout engines retain exact pinned positions and keep movable nodes outside pinned footprints',()=>{
  const {FundsGraphLayout:layout,FundsGraphLayoutQuality:quality}=layoutApi();
  for(const kind of ['auto','force','structural','dagre','partition','incremental'])for(const count of [5,85]) {
    const nodes=Array.from({length:count},(_,i)=>({id:`N${String(i).padStart(3,'0')}`,size:32,width:90,height:60,offsetY:12,
      ...(i===0?{x:-720.125,y:430.875,fixed:true}:i===1?{x:650.25,y:-130.5,fixed:true}:{})}));
    const input={kind,width:900,height:600,nodes,edges:nodes.slice(1).map(node=>({source:nodes[0].id,target:node.id}))};
    const before=JSON.stringify(input),model=layout.simulation(input);let turns=0;
    while(model.step()){assert.ok(++turns<20000);}
    const result=model.result(),byId=new Map(result.nodes.map(node=>[node.id,node]));
    assert.equal(quality.validate(input,result).valid,true,kind);
    for(const fixed of nodes.slice(0,2)){
      assert.deepEqual([byId.get(fixed.id).x,byId.get(fixed.id).y],[fixed.x,fixed.y],`${kind} ${count}`);
      for(const node of nodes.slice(2)){
        const next=byId.get(node.id);
        assert.ok(Math.abs(next.x-fixed.x)>=90||Math.abs(next.y-fixed.y)>=60,`${kind}: ${node.id} overlaps pin`);
      }
    }
    assert.equal(JSON.stringify(input),before);
    const moved={...result,nodes:result.nodes.map(node=>node.id===nodes[0].id?{...node,x:node.x+1}:node)};
    assert.equal(quality.validate(input,moved).reason,'fixed-node-moved');
  }
});

function commandFixture() {
  let generation=1;
  const state={caseId:1,graph:{},lastGraph:{nodes:[],edges:[]},graphLoading:false,graphView:{revision:1,exportDepth:0,labelMode:'auto',nodeItems:new Map([['A',{}],['B',{}]]),selectedEdge:null},
    graphSelection:{ids:new Set(['A'])},presentation:p.create(),positions:{A:{x:12.5,y:33.25},B:{x:-100,y:40}},edgeLabels:new Set(['money']),showIds:true};
  const capture=()=>({presentation:p.create(state.presentation),positions:plain(state.positions),labelMode:state.graphView.labelMode,edgeLabels:[...state.edgeLabels],showIds:state.showIds});
  const history=require('../static/canvas-history.js').create({capture,restore:async value=>{
    state.presentation=p.create(value.presentation);state.positions=plain(value.positions);state.graphView.labelMode=value.labelMode;
    state.edgeLabels=new Set(value.edgeLabels);state.showIds=value.showIds;state.graphView.revision++;return true;
  }});
  const window={FundsCanvasEditor:{history,command:(label,action)=>history.run(label,action),syncControls(){}}};
  const env={window,state,FundsGraphPresentation:p,layoutJob:null,caseContext:()=>({caseId:state.caseId,generation}),
    isCurrentCase:token=>token.caseId===state.caseId&&token.generation===generation,savePositions(){},
    newGraphPresentation:p.create,renderGraph(){state.graphView.revision++;}};
  vm.createContext(env);
  const app=source('app'),start=app.indexOf('window.FundsCanvasDisplay=Object.freeze((()=>{'),end=app.indexOf('})());',start)+6;
  assert.ok(start>0&&end>start);vm.runInContext(app.slice(start,end),env);
  return {api:window.FundsCanvasDisplay,state,history,rebind(){generation++;history.reset('same-case-new-generation');}};
}

test('public display commands preserve scope/facts/geometry and undo/redo complete choices',async()=>{
  const {api,state,history}=commandFixture();
  state.presentation.groups=[{id:'g',name:'待核查',color:'#abcdef',members:['A'],notes:''}];
  const facts=state.lastGraph,positions=JSON.stringify(state.positions);
  assert.equal(await api.setNodeShape('ellipse'),true);assert.equal(await api.setNodeIcon('account'),true);
  assert.equal(await api.setUnderline(true),true);assert.equal(await api.setPinned(true),true);
  assert.deepEqual(state.presentation.nodeStyles.A,{shape:'ellipse',icon:'account'});
  assert.deepEqual(state.presentation.pinnedAccounts,['A']);
  await history.undo();assert.deepEqual(state.presentation.pinnedAccounts,[]);
  await history.redo();assert.deepEqual(state.presentation.pinnedAccounts,['A']);
  assert.equal(await api.preset('report'),true);assert.equal(state.graphView.labelMode,'all');assert.equal(state.showIds,false);
  assert.deepEqual([...state.edgeLabels],['money','time']);assert.equal(state.presentation.groups[0].name,'待核查');
  assert.equal(state.presentation.nodeLabels.A.underline,true);assert.equal(state.lastGraph,facts);
  assert.equal(JSON.stringify(state.positions),positions);
  await history.undo();assert.equal(state.graphView.labelMode,'auto');assert.equal(state.showIds,true);
  state.graphSelection.ids.clear();state.graphView.selectedEdge='A=>B';
  assert.equal(await api.setUnderline(true),true);assert.equal(state.presentation.edgeLabels['A=>B'].underline,true);
});

test('stale selection dialogs and busy/missing selection commands cannot write into another binding',async()=>{
  const {api,state,history,rebind}=commandFixture(),old=api.snapshot();
  rebind();assert.equal(await api.setNodeShape('rect',old),false);assert.equal(history.status().count,0);
  const token=api.snapshot();state.graphView.revision++;assert.equal(await api.setPinned(true,token),false);
  state.graphLoading=true;assert.equal(await api.setNodeIcon('flag'),false);state.graphLoading=false;
  assert.equal(await api.setNodeIcon('remote-image'),false);
  state.graphSelection.ids.clear();assert.equal(await api.setPinned(true),false);
  assert.deepEqual(state.presentation,p.create());
});
