import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import {join} from 'node:path';

const context=vm.createContext({});
vm.runInContext(readFileSync(new URL('../static/graph-routing.js',import.meta.url),'utf8'),context);
const routing=context.FundsGraphRouting;
const plain=value=>JSON.parse(JSON.stringify(value));
const node=(id,x,y,long=false)=>({id,x,y,body:{minX:x-18,minY:y-18,maxX:x+18,maxY:y+18},
  label:{minX:x-(long?100:48),minY:y+25,maxX:x+(long?100:48),maxY:y+(long?77:59)}});
const edge=(source,target,extra={})=>({id:`${source}=>${target}`,source,target,type:'quadratic',labelWidth:92,labelHeight:18,...extra});
function merged(input,result){const changes=new Map(result.routes.map(route=>[route.id,route]));
  return {...input,edges:input.edges.map(item=>({...item,...(changes.get(item.id)||{})}))};}
const canonical=result=>plain(result.routes).sort((a,b)=>a.id.localeCompare(b.id));

test('complex route avoids intervening node and its fixed lower label without moving any node or money',()=>{
  const graph={nodes:[node('A',0,0),node('B',360,0),node('C',180,0)],
    edges:[edge('A','B',{amount:'99999999999999.99',currency:'CNY'})]};
  const original=JSON.stringify(graph),result=routing.plan(graph);
  assert.equal(result.stats.baseline.nodeHits,1);
  assert.equal(result.stats.final.nodeHits,0);assert.equal(result.stats.final.nodeLabelHits,0);
  assert.equal(result.routes.length,1);assert.equal(result.routes[0].source,'A');assert.equal(result.routes[0].target,'B');
  assert.equal(JSON.stringify(graph),original);
  assert.ok(result.routes[0].controlPoints[0].y<0,'route clears the stationary label below obstacle C');
  assert.equal(merged(graph,result).edges[0].amount,'99999999999999.99');
});

test('nearby edge labels remain centered on their owning paths and gain separation',()=>{
  const graph={nodes:[node('A',0,0),node('B',360,0),node('C',0,22),node('D',360,22)],
    edges:[edge('A','B'),edge('C','D')]};
  const result=routing.plan(graph),after=merged(graph,result);
  assert.ok(result.stats.final.edgeLabelHits<result.stats.baseline.edgeLabelHits);
  for(const relation of after.edges){const sampled=routing.sampleRoute(relation,graph.nodes);
    assert.equal(sampled.midpoint.x,(sampled.label.minX+sampled.label.maxX)/2);
    assert.equal(sampled.midpoint.y,(sampled.label.minY+sampled.label.maxY)/2);}
});

test('reverse directions occupy opposite automatic lanes rather than one coincident path',()=>{
  const graph={nodes:[node('A',0,0),node('B',360,0)],edges:[edge('A','B'),edge('B','A')]};
  const result=routing.plan(graph),routes=new Map(merged(graph,result).edges.map(relation=>[relation.id,relation]));
  assert.equal(result.stats.final.overlaps,0);assert.equal(result.stats.final.edgeLabelHits,0);
  const a=routes.get('A=>B').controlPoints[0],b=routes.get('B=>A').controlPoints[0];
  assert.ok(a.y*b.y<0);assert.ok(a.y>0&&b.y<0);
});

test('manual geometry, manual ratio, loops, line and polyline are protected fixed obstacles',()=>{
  const graph={nodes:[node('A',0,0),node('B',360,0),node('C',180,0)],edges:[
    edge('A','B',{manual:true,labelRatio:.2,controlPoints:[{x:90,y:-160}]}),
    edge('B','A',{type:'line'}),edge('A','C',{type:'polyline',controlPoints:[{x:50,y:-80},{x:100,y:-80}]}),
    edge('C','C',{type:'loop'})]};
  const original=JSON.stringify(graph),result=routing.plan(graph);
  assert.equal(result.routes.length,0);assert.equal(JSON.stringify(graph),original);
  const route=routing.sampleRoute(graph.edges[0],graph.nodes);
  const middle=routing.sampleRoute({...graph.edges[0],labelRatio:undefined},graph.nodes);
  assert.notEqual(route.midpoint.x,middle.midpoint.x);
  const emptySamples=routing.sampleRoute({...graph.edges[0],labelRatio:undefined,sampledPoints:[]},graph.nodes);
  assert.deepEqual(plain(emptySamples.midpoint),plain(emptySamples.points[12]));
});

test('accepted simple geometry is not replanned and already-clear complex geometry is preserved',()=>{
  const graph={nodes:[node('A',0,0),node('B',360,0)],edges:[edge('A','B',{controlPoints:[{x:180,y:-20}]})]};
  assert.equal(routing.plan({...graph,simple:true}).routes.length,0);
  assert.equal(routing.plan(graph).routes.length,0);
});

test('simple topology still checks the actual curved path against an intervening node',()=>{
  // The endpoint line is clear. Its automatic Bezier, however, runs through C.
  const graph={simple:true,conservative:true,nodes:[node('A',0,0),node('B',360,0),node('C',180,-120)],
    edges:[edge('A','B',{controlPoints:[{x:180,y:-240}]})]};
  const before=JSON.stringify(graph),result=routing.plan(graph);
  assert.equal(result.stats.baseline.nodeHits,1);
  assert.equal(result.routes.length,1);
  assert.equal(result.stats.final.nodeHits,0);assert.equal(result.stats.final.nodeLabelHits,0);
  for(const key of ['nodeHits','nodeLabelHits','edgeLabelHits','overlaps','crossings'])
    assert.ok(result.stats.final[key]<=result.stats.baseline[key],key);
  assert.equal(JSON.stringify(graph),before);
});

test('an upward relation clears the target two-line label with a bounded endpoint approach',()=>{
  // A real G6 geometry capture: a lower source approaches the text immediately
  // beneath the upper target. Midpoint-only normal offsets cannot clear it.
  const a={id:'A',x:188.5,y:214,body:{minX:168.5,minY:194,maxX:208.5,maxY:234},
    label:{minX:154,minY:239,maxX:223,maxY:261}};
  const b={id:'B',x:183,y:51,body:{minX:167,minY:35,maxX:199,maxY:67},
    label:{minX:154,minY:72,maxX:213,maxY:94}};
  const dx=b.x-a.x,dy=b.y-a.y,span=Math.hypot(dx,dy),normal=Math.min(28,span*.08);
  const graph={simple:true,conservative:true,nodes:[a,b],edges:[edge('A','B',{
    labelWidth:0,labelHeight:0,controlPoints:[{x:(a.x+b.x)/2-dy/span*normal,y:(a.y+b.y)/2+dx/span*normal}]})]};
  const before=JSON.stringify(graph),result=routing.plan(graph);
  assert.equal(result.stats.baseline.nodeLabelHits,1);
  assert.equal(result.routes.length,1);
  assert.equal(result.stats.final.nodeLabelHits,0);
  assert.ok(result.stats.candidates>11&&result.stats.candidates<=19,'only the label collision enables bounded endpoint-approach candidates');
  for(const key of ['nodeHits','nodeLabelHits','edgeLabelHits','overlaps','crossings'])
    assert.ok(result.stats.final[key]<=result.stats.baseline[key],key);
  const control=result.routes[0].controlPoints[0];
  const projection=((control.x-a.x)*dx+(control.y-a.y)*dy)/(span*span);
  assert.ok(projection>=0&&projection<=1,'control stays between the endpoint chord planes');
  assert.equal(JSON.stringify(graph),before);
  // Mirroring the same obstruction must mirror its solution, rather than
  // reintroducing a screen-right special case.
  const mirrorBox=box=>({minX:-box.maxX,minY:box.minY,maxX:-box.minX,maxY:box.maxY});
  const mirrored={...graph,nodes:graph.nodes.map(n=>({...n,x:-n.x,body:mirrorBox(n.body),label:mirrorBox(n.label)})),
    edges:graph.edges.map(e=>({...e,controlPoints:e.controlPoints.map(p=>({x:-p.x,y:p.y}))}))};
  const reflected=routing.plan(mirrored);
  assert.equal(reflected.routes.length,1);assert.equal(reflected.stats.final.nodeLabelHits,0);
  for(const key of ['nodeHits','nodeLabelHits','edgeLabelHits','overlaps','crossings'])
    assert.ok(reflected.stats.final[key]<=reflected.stats.baseline[key],key);
});

test('the captured six-node G6 star clears endpoint text while keeping its amount label away from that text',()=>{
  // Fixed synthetic G6 capture, including its actual attachment points and
  // measured text boxes. The other four relations remain real obstacles.
  const nodes=[
    ['A',188.5,214,40,[155.99755859375,239,221.00244140625,262]],
    ['B',183,51,32,[160.86376953125,72,205.13623046875,95]],
    ['C',40,161.5,32,[15.830078125,182.5,64.169921875,205.5]],
    ['D',334,161.5,32,[306.5,182.5,361.5,205.5]],
    ['E',95,325,32,[67.30712890625,346,122.69287109375,369]],
    ['F',319.5,323.5,32,[290.97705078125,344.5,348.02294921875,367.5]]
  ].map(([id,x,y,size,[minX,minY,maxX,maxY]])=>({id,x,y,
    body:{minX:x-size/2,minY:y-size/2,maxX:x+size/2,maxY:y+size/2},label:{minX,minY,maxX,maxY}}));
  const edges=[
    ['D',208.9349397536918,210.12014984013726,265.45,199.39,319.559148371878,169.48196744258996],
    ['B',191.09170164062033,193.36209597352467,198.79,132.06,186.15480389352535,67.19559237550135],
    ['C',170.23110788870986,204.05577649959326,118.45,175.87,56.22996669354082,164.47290785705778],
    ['E',172.75470893800116,227.59138732333605,132.87,262.02,103.50271353875303,310.8594956780918],
    ['F',202.151018808115,229.69361926072156,245.24,279.23,305.32734263967626,315.0509892089748]
  ].map(([target,x0,y0,cx,cy,x1,y1])=>edge('A',target,{labelWidth:74.4775390625,labelHeight:10,
    controlPoints:[{x:cx,y:cy}],sampledPoints:Array.from({length:13},(_,i)=>{
      const t=i/12,u=1-t;return {x:u*u*x0+2*u*t*cx+t*t*x1,y:u*u*y0+2*u*t*cy+t*t*y1};})}));
  const graph={nodes,edges,simple:true,conservative:true,maxChecks:120000},before=JSON.stringify(graph);
  const result=routing.plan(graph);
  assert.equal(result.stats.baseline.nodeLabelHits,4);
  assert.ok(result.routes.some(route=>route.id==='A=>B'),'the upper relation must receive a usable route');
  for(const key of ['nodeHits','nodeLabelHits','edgeLabelHits','overlaps','crossings'])assert.equal(result.stats.final[key],0,key);
  for(const proposal of result.routes){const source=nodes.find(n=>n.id===proposal.source),target=nodes.find(n=>n.id===proposal.target);
    const dx=target.x-source.x,dy=target.y-source.y,control=proposal.controlPoints[0];
    const projection=((control.x-source.x)*dx+(control.y-source.y)*dy)/(dx*dx+dy*dy);
    assert.ok(projection>=0&&projection<=1,'automatic controls stay inside the endpoint chord planes');}
  assert.equal(JSON.stringify(graph),before);
});

function fullAppLongAccountScene(labelShift=0){
  // Measured 13px two-line account labels and the full application's 25-point
  // G6 samples, including the 195.60px amount label on relation 3.
  const id=i=>`000GRAPH-0000${i}`;
  const nodes=[
    [0,378.7862406798079,289.1928101307201,19.75],
    [1,471.1695128578242,409.26604204226066,15.75],
    [2,293.1378121366914,414.1591923624816,15.75],
    [3,233.46932857912898,246.35304988951907,15.75],
    [4,374.62387840452396,137.75000000000006,15.75],
    [5,521.530671420871,238.43576635933925,15.75]
  ].map(([i,x,y,r])=>({id:id(i),x,y,body:{minX:x-r,minY:y-r,maxX:x+r,maxY:y+r},
    label:{minX:x-54.199462890625,minY:y+r+10.62108521054+labelShift,maxX:x+54.199462890625,maxY:y+r+41.62108521054+labelShift}}));
  const edges=[
    [4,381.36957194427725,269.61249094082905,388.82048435262357,213.13841608333735,377.53857559776895,153.22795336185283,69.0908203125,384.13727906182334,212.2793191173391],
    [2,365.1872589297549,303.5151770915867,325.9647158297088,344.8241269631515,299.87748529566704,399.92404902951216,92.919921875,329.24854397120987,348.2718700118505],
    [3,360.96254378594415,280.6852647129353,309.55496544876456,256.1475770420653,249.0904279490012,248.36395875781162,195.599609375,307.2907256581186,260.33609438871935],
    [5,398.206524099314,285.5990527785804,454.21901955205,275.2338427043147,507.71095398333813,245.99075874495745,69.0908203125,453.588879296688,270.51437423304185],
    [1,388.20530463974933,306.552065129669,415.3720182158928,356.6200878607317,459.71376057326427,398.4573307594666,69.0908203125,419.6657754111998,354.5623929026497]
  ].map(([i,x0,y0,cx,cy,x1,y1,labelWidth,lx,ly])=>edge(id(0),id(i),{labelWidth,labelHeight:19,
    labelAnchor:{x:lx,y:ly},controlPoints:[{x:cx,y:cy}],sampledPoints:Array.from({length:25},(_,j)=>{
      const t=j/24,u=1-t;return {x:u*u*x0+2*u*t*cx+t*t*x1,y:u*u*y0+2*u*t*cy+t*t*y1};})}));
  return {nodes,edges,conservative:true,maxChecks:120000};
}

test('the full-app long-account capture derives a top-label detour without transferring an edge-label collision',()=>{
  const graph=fullAppLongAccountScene(),{nodes,edges}=graph,id=i=>`000GRAPH-0000${i}`;
  const before=JSON.stringify(graph),result=routing.plan(graph);
  assert.equal(result.stats.baseline.nodeLabelHits,5);
  assert.ok(result.routes.some(route=>route.id===`${id(0)}=>${id(4)}`),'the real upper relation must receive a usable route');
  assert.equal(result.stats.final.edgeLabelHits,0,'do not transfer the huge-label hit to another relation');
  for(const key of ['nodeHits','nodeLabelHits','edgeLabelHits','overlaps','crossings'])
    assert.ok(result.stats.final[key]<=result.stats.baseline[key],key);
  const changes=new Map(result.routes.map(route=>[route.id,route]));
  const after=edges.map(e=>changes.has(e.id)?{...e,...changes.get(e.id),sampledPoints:null,labelAnchor:null}:e);
  const top=after.find(e=>e.target===id(4));
  assert.equal(routing.analyze({nodes,edges:[top]}).nodeLabelHits,0,'top path and its amount label clear every account label');
  const source=nodes[0],target=nodes[4],dx=target.x-source.x,dy=target.y-source.y,span=Math.hypot(dx,dy),control=top.controlPoints[0];
  const along=((control.x-source.x)*dx+(control.y-source.y)*dy)/(span*span);
  const normal=Math.abs(((control.y-source.y)*dx-(control.x-source.x)*dy)/span);
  assert.ok(along>=0&&along<=1);assert.ok(normal<=240,'derived label candidates retain their finite normal cap');
  assert.ok(result.stats.checks<=graph.maxChecks);assert.equal(JSON.stringify(graph),before);
});

test('narrow and wide full-app scenes never trade an amount-label improvement for a new path-label collision',()=>{
  // Measured text offsets from the narrow and wide G6 camera captures. Their
  // graph positions differ only by translation, which does not affect routing.
  for(const labelShift of [5.0661916054777,-3.74608521054]) {
    const graph=fullAppLongAccountScene(labelShift),result=routing.plan(graph),changes=new Map(result.routes.map(route=>[route.id,route]));
    const pathHits=(edge,labelId)=>routing.analyze({nodes:graph.nodes.map(n=>({...n,label:n.id===labelId?n.label:null})),
      edges:[{...edge,labelWidth:0,labelHeight:0}]}).nodeLabelHits;
    for(const before of graph.edges){const proposal=changes.get(before.id);if(!proposal)continue;
      const after={...before,...proposal,sampledPoints:null,labelAnchor:null};
      for(const node of graph.nodes)assert.ok(pathHits(after,node.id)<=pathHits(before,node.id),
        `${labelShift}/${before.id} must not introduce path contact with ${node.id}`);}
    for(const key of ['nodeHits','nodeLabelHits','edgeLabelHits','overlaps','crossings'])
      assert.ok(result.stats.final[key]<=result.stats.baseline[key],key);
  }
});

test('a clear but excessive automatic detour is shortened without increasing any collision count',()=>{
  const graph={conservative:true,nodes:[node('A',0,0),node('B',360,0)],
    edges:[edge('A','B',{controlPoints:[{x:180,y:-280}]})]};
  const before=routing.sampleRoute(graph.edges[0],graph.nodes),result=routing.plan(graph);
  assert.equal(result.routes.length,1);
  const after=routing.sampleRoute(merged(graph,result).edges[0],graph.nodes);
  assert.equal(result.stats.candidates,11,'clear excessive arcs keep the ordinary bounded candidate set');
  assert.ok(before.length-after.length>=Math.max(8,before.length*.03));
  assert.ok(Math.abs(result.routes[0].controlPoints[0].y)<=28);
  for(const key of ['nodeHits','nodeLabelHits','edgeLabelHits','overlaps','crossings'])
    assert.equal(result.stats.final[key],result.stats.baseline[key],key);
});

test('a clear automatic curve that overshoots and doubles back is repaired even in a simple graph',()=>{
  const graph={simple:true,conservative:true,nodes:[node('A',0,0),node('B',360,0)],
    edges:[edge('A','B',{controlPoints:[{x:680,y:-100}]})]};
  const before=routing.sampleRoute(graph.edges[0],graph.nodes),result=routing.plan(graph);
  assert.ok(before.points.some(point=>point.x>360));
  assert.equal(result.routes.length,1);
  const after=routing.sampleRoute(merged(graph,result).edges[0],graph.nodes);
  assert.ok(after.points.every(point=>point.x>=0&&point.x<=360));
  assert.ok(before.length-after.length>=Math.max(8,before.length*.03));
  for(const key of ['nodeHits','nodeLabelHits','edgeLabelHits','overlaps','crossings'])
    assert.equal(result.stats.final[key],result.stats.baseline[key],key);
});

test('short clear curves skip candidate work and manual long paths and ratios are immutable',()=>{
  const graph={simple:true,nodes:[node('A',0,0),node('B',360,0)],
    edges:[edge('A','B',{controlPoints:[{x:180,y:-20}]})]};
  const short=routing.plan(graph);
  assert.equal(short.routes.length,0);assert.equal(short.stats.candidates,0);
  const manual={...graph,edges:[edge('A','B',{manual:true,labelRatio:.17,controlPoints:[{x:180,y:-280}]})]};
  const before=JSON.stringify(manual),result=routing.plan(manual);
  assert.equal(result.routes.length,0);assert.equal(result.stats.candidates,0);
  assert.equal(JSON.stringify(manual),before);
});

test('clear long reverse lanes shorten independently while retaining their direction separation',()=>{
  const graph={conservative:true,nodes:[node('A',0,0),node('B',360,0)],edges:[
    edge('A','B',{controlPoints:[{x:180,y:280}]}),edge('B','A',{controlPoints:[{x:180,y:-280}]})]};
  const result=routing.plan(graph),after=merged(graph,result);
  assert.equal(result.routes.length,2);
  const a=after.edges[0].controlPoints[0],b=after.edges[1].controlPoints[0];
  assert.ok(a.y>0&&b.y<0);assert.ok(a.y<=28&&b.y>=-28);
  for(const key of ['nodeHits','nodeLabelHits','edgeLabelHits','overlaps','crossings'])
    assert.ok(result.stats.final[key]<=result.stats.baseline[key],key);
});

function corpus(kind,count,width=1000,height=700){
  const columns=Math.ceil(Math.sqrt(count)),nodes=Array.from({length:count},(_,i)=>node(`N${String(i).padStart(3,'0')}`,
    (i%columns)*(width/Math.max(3,columns-1)),Math.floor(i/columns)*(height/Math.max(3,columns-1)),i%7===0));
  const relations=[],add=(a,b)=>{if(a!==b)relations.push(edge(nodes[a].id,nodes[b].id));};
  for(let i=1;i<count;i++){
    if(kind==='chain')add(i-1,i);
    if(kind==='star')add(0,i);
    if(kind==='cycle'||kind==='scc'){add(i-1,i);if(kind==='scc'&&i%3===0)add(i,i-3);}
    if(kind==='blocks'){add(Math.floor(i/6)*6,i);if(i%6)add(i-1,i);}
    if(kind==='bipartite')for(let j=0;j<Math.min(4,Math.floor(count/2));j++)if(i>=Math.floor(count/2))add(j,i);
  }
  if(kind==='cycle'||kind==='scc')add(count-1,0);
  return {nodes,edges:relations,maxChecks:30000};
}

test('different shapes, counts and canvas scales preserve deterministic finite geometry with bounded work',()=>{
  for(const count of [24,81,160])for(const kind of ['chain','star','cycle','scc','blocks','bipartite'])for(const [width,height]of [[480,320],[1600,900]]){
    const graph=corpus(kind,count,width,height),before=JSON.stringify(graph),result=routing.plan(graph);
    assert.equal(JSON.stringify(graph),before);
    assert.ok(result.stats.checks<=result.stats.budget);assert.ok(result.stats.baseline.checks<=graph.maxChecks);
    assert.ok(result.stats.final.checks<=graph.maxChecks);
    assert.ok(result.routes.every(route=>route.controlPoints.length===1&&route.controlPoints.every(point=>Number.isFinite(point.x)&&Number.isFinite(point.y))));
    assert.ok(result.stats.candidates<=result.stats.assessed*19);
    const reordered={...graph,nodes:[...graph.nodes].reverse(),edges:[...graph.edges].reverse()};
    assert.deepEqual(canonical(routing.plan(reordered)),canonical(result));
  }
});

test('exhausted budgets and coincident endpoints remain finite and explicitly report partial coverage',()=>{
  const graph=corpus('bipartite',160,600,400);graph.maxChecks=100;
  const result=routing.plan(graph);assert.equal(result.stats.limited,true);assert.ok(result.stats.checks<=100);
  assert.equal(result.routes.length,0);assert.equal(result.stats.rejected,'quality-budget');
  assert.equal(result.stats.baseline.complete,false);assert.equal(result.stats.skipped,'incomplete-baseline');
  assert.equal(result.stats.candidates,0);assert.equal(result.stats.assessed,0);assert.equal(result.stats.checks,0);
  assert.deepEqual(result.stats.final,result.stats.baseline);
  assert.equal(routing.plan({nodes:[node('A',0,0),node('B',0,0)],edges:[edge('A','B')]}).routes.length,0);
  const invalid=routing.plan({nodes:[node('A',0,0),{id:'B',x:NaN,y:0}],edges:[edge('A','B')]});
  assert.equal(invalid.routes.length,0);assert.equal(invalid.stats.edges,0);
});

test('real sampled loop geometry and exact label anchors remain fixed obstacles',()=>{
  const loop=edge('C','C',{type:'loop',manual:true,labelWidth:70,labelAnchor:{x:180,y:0},
    sampledPoints:[{x:160,y:80},{x:160,y:0},{x:200,y:0},{x:200,y:80},{x:160,y:80}]});
  const graph={nodes:[node('A',0,0),node('B',360,0),node('C',180,120)],edges:[
    edge('A','B',{sampledPoints:[{x:18,y:0},{x:342,y:0}],labelAnchor:{x:180,y:0}}),loop]};
  const before=JSON.stringify(graph),result=routing.plan(graph),sampled=routing.sampleRoute(loop,graph.nodes);
  assert.deepEqual(plain(sampled.midpoint),loop.labelAnchor);assert.equal(sampled.points.length,5);
  assert.equal(result.stats.baseline.complete,true);assert.equal(result.stats.final.complete,true);
  assert.equal(result.routes.length,1);assert.equal(result.routes[0].id,'A=>B');
  assert.ok(result.stats.final.overlaps<result.stats.baseline.overlaps);
  assert.equal(result.stats.final.edgeLabelHits,0);assert.equal(JSON.stringify(graph),before);
});

test('obstacle edge and sample caps stop before indexing and retain the unchanged baseline',()=>{
  const a=node('A',0,0),b=node('B',360,0);Object.defineProperty(a,'body',{get(){throw Error('must not allocate obstacle index');}});
  const graph={simple:true,nodes:[a,b],edges:Array.from({length:1501},(_,i)=>edge('A','B',{id:String(i)}))};
  let result=routing.plan(graph);assert.equal(result.routes.length,0);assert.equal(result.stats.skipped,'obstacle-budget');
  assert.equal(result.stats.limited,true);assert.equal(result.stats.checks,0);
  assert.equal(routing.analyze(graph).skipped,'obstacle-budget');
  result=routing.plan({...graph,edges:[edge('A','B',{sampledPoints:Array.from({length:36002},()=>({x:0,y:0}))})]});
  assert.equal(result.routes.length,0);assert.equal(result.stats.skipped,'obstacle-budget');assert.equal(result.stats.checks,0);
});

test('routing envelope contains sampled candidate paths and padded centered labels without forcing a wider fit',()=>{
  const graph={nodes:[node('A',0,0),node('B',360,0),node('C',180,0)],edges:[edge('A','B')],conservative:true};
  const tight={minX:-66,minY:-18,maxX:426,maxY:77},blocked=routing.plan({...graph,routingBounds:tight});
  assert.equal(blocked.routes.length,0);assert.ok(blocked.stats.outsideBounds>0);
  const room={minX:-66,minY:-180,maxX:426,maxY:180},result=routing.plan({...graph,routingBounds:room});
  assert.equal(result.routes.length,1);
  for(const relation of merged(graph,result).edges){const route=routing.sampleRoute(relation,graph.nodes);
    assert.ok(route.points.every(point=>point.x>=room.minX&&point.x<=room.maxX&&point.y>=room.minY&&point.y<=room.maxY));
    assert.ok(route.label.minX>=room.minX&&route.label.maxX<=room.maxX&&route.label.minY>=room.minY&&route.label.maxY<=room.maxY);}
});

test('conservative planning does not hide extra crossings behind fewer node or label collisions',()=>{
  const nodes=Array.from({length:8},(_,i)=>node(`N${i}`,(i%4)*140,Math.floor(i/4)*160));
  const edges=[[0,6],[0,7],[1,2],[1,3],[2,5],[2,6],[4,5],[5,7]].map(([a,b])=>edge(`N${a}`,`N${b}`));
  const graph={nodes,edges},lexicographic=routing.plan(graph),safe=routing.plan({...graph,conservative:true});
  assert.ok(lexicographic.stats.final.nodeHits<lexicographic.stats.baseline.nodeHits);
  assert.ok(lexicographic.stats.final.crossings>lexicographic.stats.baseline.crossings,'fixture exposes a real layout tradeoff');
  assert.ok(safe.routes.length>0,'conservative planning still improves eligible geometry');
  for(const key of ['nodeHits','nodeLabelHits','edgeLabelHits','overlaps','crossings'])assert.ok(safe.stats.final[key]<=safe.stats.baseline[key],key);
  assert.ok(safe.stats.final.edgeLabelHits<safe.stats.baseline.edgeLabelHits);
  for(const count of [24,81])for(const kind of ['chain','star','cycle','scc','blocks','bipartite']){
    const result=routing.plan({...corpus(kind,count),conservative:true});
    for(const key of ['nodeHits','nodeLabelHits','edgeLabelHits','overlaps','crossings'])assert.ok(result.stats.final[key]<=result.stats.baseline[key],`${kind}/${count}/${key}`);
  }
});

test('rebasing preserves exact unchanged controls and transforms translations, rotation and span',()=>{
  const points=[{x:50,y:20},{x:80,y:-10}],a={x:0,y:0},b={x:100,y:0};
  const unchanged=routing.rebase(points,a,b,a,b);assert.deepEqual(plain(unchanged),points);assert.notEqual(unchanged[0],points[0]);
  assert.deepEqual(plain(routing.rebase(points,a,b,{x:10,y:30},{x:110,y:30})),[{x:60,y:50},{x:90,y:20}]);
  assert.deepEqual(plain(routing.rebase([{x:50,y:20}],a,b,a,{x:0,y:200})),[{x:-40,y:100}]);
  assert.deepEqual(plain(routing.rebase([{x:50,y:20}],a,b,a,{x:200,y:0})),[{x:100,y:40}]);
  assert.deepEqual(plain(routing.rebase([{x:5,y:7}],a,a,{x:10,y:20},{x:20,y:30})),[{x:20,y:32}]);
  assert.ok(routing.rebase(points,a,b,a,a).every(point=>Number.isFinite(point.x)&&Number.isFinite(point.y)));
});

// An immutable external source snapshot is an opt-in oracle for equivalent
// optimizations; normal regressions do not depend on local artifact paths.
if(process.env.FUNDS_GRAPH_BASELINE_DIR)test('routing preserves baseline obstacle analysis and incomplete-budget rejection',()=>{
  const beforeContext=vm.createContext({});
  vm.runInContext(readFileSync(join(process.env.FUNDS_GRAPH_BASELINE_DIR,'graph-routing.js'),'utf8'),beforeContext);
  const before=beforeContext.FundsGraphRouting;
  const compare=graph=>{
    const actual=structuredClone(routing.plan(graph)),expected=structuredClone(before.plan(graph));
    if(!expected.stats.baseline.complete){
      assert.deepEqual(actual.routes,expected.routes);assert.deepEqual(actual.stats.baseline,expected.stats.baseline);
      assert.deepEqual(actual.stats.final,actual.stats.baseline);assert.equal(actual.stats.checks,0);
      assert.equal(actual.stats.candidates,0);assert.equal(actual.stats.rejected,'quality-budget');
    }
    // Complete plans intentionally use the revised short-route policy. The
    // immutable oracle still guards every obstacle metric and budget rejection;
    // new route choices are covered above by independent geometric fixtures.
    assert.deepEqual(structuredClone(routing.analyze(graph)),structuredClone(before.analyze(graph)));
  };
  for(const count of [8,24])for(const kind of ['chain','star','cycle','scc','blocks','bipartite'])
    for(const [width,height]of [[480,320],[1600,900]])for(const conservative of [false,true])for(const maxChecks of [100,2500,30000]){
      const graph={...corpus(kind,count,width,height),conservative,maxChecks};compare(graph);
      if(maxChecks===2500)compare({...graph,nodes:[...graph.nodes].reverse(),edges:[...graph.edges].reverse()});
    }
  const ordinary=[node('A',0,0),node('B',360,0),node('C',180,120)],wide=node('W',200000,200000);
  wide.body={minX:190000,minY:190000,maxX:210000,maxY:210000};
  wide.label={minX:-100000,minY:10000,maxX:100000,maxY:30000};
  const fixtures=[
    {nodes:[...ordinary,wide],edges:[edge('A','B'),edge('B','A')]},
    {nodes:ordinary,edges:[edge('A','B',{manual:true,labelRatio:.2,controlPoints:[{x:50,y:-160}]}),edge('B','A')]},
    {nodes:ordinary,edges:[edge('A','B'),edge('C','C',{type:'loop',manual:true,labelAnchor:{x:180,y:0},
      sampledPoints:[{x:160,y:80},{x:160,y:0},{x:200,y:0},{x:200,y:80},{x:160,y:80}]})]},
    {nodes:ordinary,edges:[edge('C','C',{type:'loop'}),edge('A','B')]},
    {nodes:[node('A',0,0),node('B',0,0),{id:'invalid',x:NaN,y:0}],edges:[edge('A','B'),edge('A','invalid')]},
    {nodes:[node('A',0,0),node('B',100000,0),node('C',50000,0)],edges:[edge('A','B',{labelWidth:100000,labelHeight:30000})]}
  ];
  for(const fixture of fixtures)for(const maxChecks of [100,500,30000])for(const conservative of [false,true])compare({...fixture,maxChecks,conservative});
});
