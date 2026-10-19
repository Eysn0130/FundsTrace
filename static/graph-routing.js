/* Bounded display-only routing. Nodes, account directions and manual geometry are immutable. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.FundsGraphRouting = api;
})(typeof window === 'undefined' ? globalThis : window, function () {
  'use strict';
  const EPS = 1e-7, SAMPLES = 24, METRICS = ['nodeHits', 'nodeLabelHits', 'edgeLabelHits', 'overlaps', 'crossings'];
  const finite = point => point && Number.isFinite(point.x) && Number.isFinite(point.y);
  const compareId = (a, b) => a < b ? -1 : a > b ? 1 : 0;
  const metrics = () => ({nodeHits:0, nodeLabelHits:0, edgeLabelHits:0, overlaps:0, crossings:0});
  const bounds = (a, b, gap = 0) => ({minX:Math.min(a.x,b.x)-gap, minY:Math.min(a.y,b.y)-gap,
    maxX:Math.max(a.x,b.x)+gap, maxY:Math.max(a.y,b.y)+gap});
  function rectangle(value) {
    if (!value) return null;
    const minX = value.minX ?? value.x, minY = value.minY ?? value.y;
    const maxX = value.maxX ?? minX + value.width, maxY = value.maxY ?? minY + value.height;
    return [minX,minY,maxX,maxY].every(Number.isFinite) && maxX >= minX && maxY >= minY
      ? {minX,minY,maxX,maxY} : null;
  }
  const overlaps = (a,b) => a.minX < b.maxX-EPS && a.maxX > b.minX+EPS && a.minY < b.maxY-EPS && a.maxY > b.minY+EPS;
  function segmentBox(a,b,box) {
    // Liang-Barsky also catches a segment contained completely inside a rectangle.
    let lo=0,hi=1;const dx=b.x-a.x,dy=b.y-a.y;
    for(const [p,q] of [[-dx,a.x-box.minX],[dx,box.maxX-a.x],[-dy,a.y-box.minY],[dy,box.maxY-a.y]]) {
      if(Math.abs(p)<EPS){if(q<0)return false;continue;}
      const r=q/p;if(p<0){if(r>hi)return false;lo=Math.max(lo,r);}else{if(r<lo)return false;hi=Math.min(hi,r);}
    }
    return hi-lo>EPS;
  }
  function segmentRelation(a,b,c,d) {
    const ax=b.x-a.x,ay=b.y-a.y,bx=d.x-c.x,by=d.y-c.y,cx=c.x-a.x,cy=c.y-a.y;
    const cross=ax*by-ay*bx,scale=Math.max(1,Math.hypot(ax,ay)*Math.hypot(bx,by));
    if(Math.abs(cross)<=EPS*scale) {
      if(Math.abs(cx*ay-cy*ax)>EPS*Math.max(1,Math.hypot(ax,ay)))return null;
      const horizontal=Math.abs(ax)>=Math.abs(ay),u=horizontal?'x':'y';
      return Math.min(Math.max(a[u],b[u]),Math.max(c[u],d[u]))-Math.max(Math.min(a[u],b[u]),Math.min(c[u],d[u]))>2?'overlaps':null;
    }
    const t=(cx*by-cy*bx)/cross,u=(cx*ay-cy*ax)/cross;
    return t>=-EPS&&t<=1+EPS&&u>=-EPS&&u<=1+EPS?'crossings':null;
  }
  function index(cellSize=128) {
    const cells=new Map(),wide=new Map(),entries=new Map();let serial=0,querySerial=0;
    function keys(box) {
      const x0=Math.floor(box.minX/cellSize),x1=Math.floor(box.maxX/cellSize),y0=Math.floor(box.minY/cellSize),y1=Math.floor(box.maxY/cellSize);
      if(![x0,x1,y0,y1].every(Number.isSafeInteger)||(x1-x0+1)*(y1-y0+1)>256)return null;
      const list=[];for(let x=x0;x<=x1;x++)for(let y=y0;y<=y1;y++)list.push(`${x},${y}`);return list;
    }
    return {
      add(box,data){const id=++serial,list=keys(box),entry={id,box,data,keys:list};entries.set(id,entry);
        if(!list)wide.set(id,entry);else for(const key of list){if(!cells.has(key))cells.set(key,new Map());cells.get(key).set(id,entry);}return id;},
      remove(id){const entry=entries.get(id);if(!entry)return;entries.delete(id);if(!entry.keys)wide.delete(id);else for(const key of entry.keys){cells.get(key)?.delete(id);if(!cells.get(key)?.size)cells.delete(key);}},
      query(box,visit){const stamp=++querySerial,x0=Math.floor(box.minX/cellSize),x1=Math.floor(box.maxX/cellSize),
        y0=Math.floor(box.minY/cellSize),y1=Math.floor(box.maxY/cellSize);
        const examine=entry=>{if(entry.queryStamp===stamp)return true;entry.queryStamp=stamp;
          return !overlaps(box,entry.box)||visit(entry)!==false;};
        // Keep the former Map's insertion order: overflow first, then each
        // occupied cell in x/y order, with an entry visited only on first sight.
        for(const entry of wide.values())if(!examine(entry))return false;
        if(![x0,x1,y0,y1].every(Number.isSafeInteger)||(x1-x0+1)*(y1-y0+1)>256){
          for(const entry of entries.values())if(!examine(entry))return false;
        }else for(let x=x0;x<=x1;x++)for(let y=y0;y<=y1;y++)for(const entry of cells.get(`${x},${y}`)?.values()||[])
          if(!examine(entry))return false;
        return true;}
    };
  }
  function body(node) {
    return rectangle(node?.body)||{minX:node.x-18,minY:node.y-18,maxX:node.x+18,maxY:node.y+18};
  }
  function attachment(node,toward) {
    const box=body(node),rx=Math.max(1,(box.maxX-box.minX)/2),ry=Math.max(1,(box.maxY-box.minY)/2);
    const dx=toward.x-node.x,dy=toward.y-node.y,scale=Math.sqrt(dx*dx/(rx*rx)+dy*dy/(ry*ry));
    return scale>EPS?{x:node.x+dx/scale,y:node.y+dy/scale}:{x:node.x,y:node.y};
  }
  function atRatio(points,ratio=.5) {
    const lengths=[0];let length=0;
    for(let i=1;i<points.length;i++){length+=Math.hypot(points[i].x-points[i-1].x,points[i].y-points[i-1].y);lengths.push(length);}
    const goal=length*ratio;for(let i=1;i<points.length;i++)if(lengths[i]>=goal){const t=(goal-lengths[i-1])/(lengths[i]-lengths[i-1]||1);
      return {point:{x:points[i-1].x+(points[i].x-points[i-1].x)*t,y:points[i-1].y+(points[i].y-points[i-1].y)*t},length};}
    return {point:points[0],length};
  }
  function sampleRoute(edge,nodes) {
    const lookup=nodes instanceof Map?nodes:new Map((nodes||[]).map(node=>[String(node.id),node]));
    const source=lookup.get(String(edge.source)),target=lookup.get(String(edge.target));
    if(!finite(source)||!finite(target))return null;
    const controls=(edge.controlPoints||[]).filter(finite),type=edge.type||'quadratic';
    const sampled=Array.isArray(edge.sampledPoints)&&edge.sampledPoints.length>=2;let points;
    if(sampled) {
      points=edge.sampledPoints.map(point=>({x:point.x,y:point.y}));
    } else if(source===target)return null;
    else if(type==='quadratic'&&controls.length) {
      const control=controls[0],start=attachment(source,control),end=attachment(target,control);
      points=Array.from({length:SAMPLES+1},(_,i)=>{const t=i/SAMPLES,u=1-t;return {x:u*u*start.x+2*u*t*control.x+t*t*end.x,y:u*u*start.y+2*u*t*control.y+t*t*end.y};});
    } else {
      const waypoints=type==='polyline'?controls:[],first=waypoints[0]||target,last=waypoints[waypoints.length-1]||source;
      points=[attachment(source,first),...waypoints.map(p=>({x:p.x,y:p.y})),attachment(target,last)];
    }
    if(!points.every(finite))return null;
    const middle=atRatio(points),width=Math.max(0,Number(edge.labelWidth)||0),height=Math.max(0,Number(edge.labelHeight)||0);
    // A saved manual ratio stays on its owning path and is an obstacle for automatic routes.
    const labelPoint=finite(edge.labelAnchor)?{x:edge.labelAnchor.x,y:edge.labelAnchor.y}
      :Number.isFinite(edge.labelRatio)?atRatio(points,Math.max(0,Math.min(1,edge.labelRatio))).point
      :!sampled&&type==='quadratic'&&controls.length?points[SAMPLES/2]:middle.point;
    const label=width&&height?{minX:labelPoint.x-width/2-4,minY:labelPoint.y-height/2-4,maxX:labelPoint.x+width/2+4,maxY:labelPoint.y+height/2+4}:null;
    return {points,label,midpoint:labelPoint,length:middle.length,control:type==='quadratic'?controls[0]:null};
  }
  function model(input) {
    const nodes=new Map();for(const node of input.nodes||[])if(node&&node.id!=null&&finite(node)&&!nodes.has(String(node.id)))nodes.set(String(node.id),node);
    const edges=(input.edges||[]).map(edge=>({...edge,id:String(edge.id??`${edge.source}=>${edge.target}`),source:String(edge.source),target:String(edge.target)}))
      .filter(edge=>nodes.has(edge.source)&&nodes.has(edge.target)).sort((a,b)=>compareId(a.id,b.id)||compareId(a.source,b.source)||compareId(a.target,b.target));
    const pairs=new Set(edges.map(edge=>JSON.stringify([edge.source,edge.target])));
    for(const edge of edges)edge.hasReverse=edge.source!==edge.target&&pairs.has(JSON.stringify([edge.target,edge.source]));
    const nodeIndex=index(),routeIndex=index(),labelIndex=index(),routes=new Map(),handles=new Map();
    for(const [id,node]of nodes){nodeIndex.add(body(node),{kind:'body',id});const label=rectangle(node.label);if(label)nodeIndex.add(label,{kind:'label',id});}
    function install(edge,route) {
      for(const id of handles.get(edge.id)?.segments||[])routeIndex.remove(id);const old=handles.get(edge.id)?.label;if(old)labelIndex.remove(old);
      const segments=[];if(route)for(let i=1;i<route.points.length;i++)segments.push(routeIndex.add(bounds(route.points[i-1],route.points[i],.01),{edge,a:route.points[i-1],b:route.points[i]}));
      const label=route?.label?labelIndex.add(route.label,{edge}):null;handles.set(edge.id,{segments,label});routes.set(edge.id,route);
    }
    for(const edge of edges)install(edge,sampleRoute(edge,nodes));
    return {nodes,edges,nodeIndex,routeIndex,labelIndex,routes,install};
  }
  function work(limit) {return {limit:Math.max(100,Math.min(2000000,Math.floor(Number(limit)||240000))),checks:0,limited:false};}
  function use(budget) {if(budget.checks>=budget.limit){budget.limited=true;return false;}budget.checks++;return true;}
  function score(edge,route,scene,budget) {
    const hit={nodeHits:new Set(),nodeLabelHits:new Set(),edgeLabelHits:new Set(),overlaps:new Set(),crossings:new Set()};
    const contacts={...hit,pathNodeLabels:new Set(),amountNodeLabels:new Set()};
    if(!route)return {...metrics(),contacts,length:0,complete:true};
    const own=new Set([edge.source,edge.target]);
    for(let i=1;i<route.points.length;i++) {
      const a=route.points[i-1],b=route.points[i],box=bounds(a,b,.01);
      if(!scene.nodeIndex.query(box,entry=>{
        if(!use(budget))return false;
        if(entry.data.kind==='body'&&own.has(entry.data.id))return true;
        if(segmentBox(a,b,entry.box)){
          hit[entry.data.kind==='body'?'nodeHits':'nodeLabelHits'].add(entry.data.id);
          if(entry.data.kind==='label')contacts.pathNodeLabels.add(entry.data.id);
        }
        return true;
      }))return {complete:false};
      if(!scene.labelIndex.query(box,entry=>{
        if(!use(budget))return false;
        if(entry.data.edge.id!==edge.id&&segmentBox(a,b,entry.box))hit.edgeLabelHits.add(entry.data.edge.id);
        return true;
      }))return {complete:false};
      if(!scene.routeIndex.query(box,entry=>{
        if(!use(budget))return false;
        const other=entry.data.edge;if(other.id===edge.id)return true;
        const relation=segmentRelation(a,b,entry.data.a,entry.data.b);if(relation)hit[relation].add(other.id);
        return true;
      }))return {complete:false};
    }
    if(route.label) {
      if(!scene.nodeIndex.query(route.label,entry=>{
        if(!use(budget))return false;
        hit[entry.data.kind==='body'?'nodeHits':'nodeLabelHits'].add(entry.data.id);
        if(entry.data.kind==='label')contacts.amountNodeLabels.add(entry.data.id);
        return true;
      }))return {complete:false};
      if(!scene.labelIndex.query(route.label,entry=>{
        if(!use(budget))return false;if(entry.data.edge.id!==edge.id)hit.edgeLabelHits.add(entry.data.edge.id);
        return true;
      }))return {complete:false};
      if(!scene.routeIndex.query(route.label,entry=>{
        if(!use(budget))return false;if(entry.data.edge.id!==edge.id&&segmentBox(entry.data.a,entry.data.b,route.label))hit.edgeLabelHits.add(entry.data.edge.id);
        return true;
      }))return {complete:false};
    }
    let lane=0;
    if(edge.hasReverse){const source=scene.nodes.get(edge.source),target=scene.nodes.get(edge.target),control=route.control;
      lane=!control||(target.x-source.x)*(control.y-source.y)-(target.y-source.y)*(control.x-source.x)<=EPS?1:0;}
    return {...Object.fromEntries(METRICS.map(key=>[key,hit[key].size])),contacts,lane,length:route.length,complete:true};
  }
  function better(a,b,conservative=false,original=b) {
    if(conservative){
      if(METRICS.some(key=>a[key]>b[key]))return false;
      // Node contacts cannot move to another account or switch from amount
      // overlap to a path through its text. Edge-to-edge conflicts retain the
      // numeric guard above; the caller validates their identities atomically
      // on the final rendered batch so cooperating replacements remain possible.
      const protectedKinds=['nodeHits','nodeLabelHits','pathNodeLabels','amountNodeLabels'];
      for(const key of protectedKinds)for(const id of a.contacts[key])if(!original.contacts[key].has(id))return false;
      if(METRICS.some(key=>a[key]<b[key])||protectedKinds.some(key=>a.contacts[key].size<b.contacts[key].size))return true;
    }
    for(const key of METRICS){if(a[key]!==b[key])return a[key]<b[key];}
    if(a.lane!==b.lane)return a.lane<b.lane;
    // Geometry with identical collision quality must improve visibly. A pixel
    // of sampled-length noise is not a reason to move a clear automatic curve.
    return b.length-a.length>=Math.max(8,b.length*.03);
  }
  function summary(scene,limit) {
    const budget=work(limit),result=metrics();let assessed=0,unmodeled=0;
    for(const edge of scene.edges){const route=scene.routes.get(edge.id);if(!route){unmodeled++;continue;}
      const value=score(edge,route,scene,budget);if(!value.complete)break;
      assessed++;for(const key of METRICS)result[key]+=value[key];}
    // Edge pairs are counted in both directions; node hits are counted per route.
    for(const key of ['edgeLabelHits','overlaps','crossings'])result[key]=Math.ceil(result[key]/2);
    return {...result,assessed,unmodeled,edges:scene.edges.length,checks:budget.checks,limited:budget.limited,
      complete:!budget.limited&&!unmodeled&&assessed===scene.edges.length};
  }
  function obstacleBudget(input) {
    const edges=input.edges||[],maxEdges=Math.max(1,Math.min(10000,Math.floor(Number(input.maxObstacleEdges)||1500)));
    const maxSegments=Math.max(1,Math.min(240000,Math.floor(Number(input.maxObstacleSegments)||36000)));
    let segments=0;
    if(edges.length>maxEdges)return {edges:edges.length,segments,maxObstacleEdges:maxEdges,maxObstacleSegments:maxSegments};
    for(const edge of edges){segments+=Array.isArray(edge.sampledPoints)?Math.max(0,edge.sampledPoints.length-1)
      :edge.source===edge.target?0:(edge.type||'quadratic')==='quadratic'&&edge.controlPoints?.length?SAMPLES
      :edge.type==='polyline'?(edge.controlPoints?.length||0)+1:1;
      if(segments>maxSegments)return {edges:edges.length,segments,maxObstacleEdges:maxEdges,maxObstacleSegments:maxSegments};}
    return null;
  }
  function analyze(input={}) {
    const cap=obstacleBudget(input);return cap?{...metrics(),...cap,assessed:0,checks:0,limited:true,complete:false,skipped:'obstacle-budget'}
      :summary(model(input),input.maxChecks);
  }
  function rebase(controlPoints,fromSource,fromTarget,toSource,toTarget) {
    const points=(controlPoints||[]).filter(finite);
    if(![fromSource,fromTarget,toSource,toTarget].every(finite))return points.map(point=>({x:point.x,y:point.y}));
    if(fromSource.x===toSource.x&&fromSource.y===toSource.y&&fromTarget.x===toTarget.x&&fromTarget.y===toTarget.y)
      return points.map(point=>({x:point.x,y:point.y}));
    const fx=fromTarget.x-fromSource.x,fy=fromTarget.y-fromSource.y,tx=toTarget.x-toSource.x,ty=toTarget.y-toSource.y;
    const length2=fx*fx+fy*fy,delta={x:((toSource.x-fromSource.x)+(toTarget.x-fromTarget.x))/2,
      y:((toSource.y-fromSource.y)+(toTarget.y-fromTarget.y))/2};
    return points.map(point=>{
      let moved;
      if(Number.isFinite(length2)&&length2>EPS*EPS&&Math.hypot(tx,ty)>EPS){
        const px=point.x-fromSource.x,py=point.y-fromSource.y,along=(px*fx+py*fy)/length2,normal=(py*fx-px*fy)/length2;
        moved={x:toSource.x+along*tx-normal*ty,y:toSource.y+along*ty+normal*tx};
      }else moved={x:point.x+delta.x,y:point.y+delta.y};
      return finite(moved)?moved:{x:point.x,y:point.y};
    });
  }
  function candidates(edge,scene,endpointApproach=false) {
    const source=scene.nodes.get(edge.source),target=scene.nodes.get(edge.target),dx=target.x-source.x,dy=target.y-source.y;
    const span=Math.hypot(dx,dy);if(!Number.isFinite(span)||span<1)return [];
    const tx=dx/span,ty=dy/span,nx=-ty,ny=tx,mx=source.x+dx/2,my=source.y+dy/2;
    const amplitude=Math.min(240,Math.max(42,span*.24,(Number(edge.labelHeight)||18)*2));
    const result=[],add=(normal,tangent=0)=>result.push({x:mx+nx*normal+tx*tangent,y:my+ny*normal+ty*tangent});
    add(0);const compact=Math.min(28,span*.08);
    for(const side of [1,-1])add(side*compact);
    for(const side of [1,-1]){add(side*amplitude);add(side*amplitude*1.8);add(side*amplitude,span*.22);add(side*amplitude,-span*.22);}
    // Endpoint text needs both a wider normal offset and a sideward approach.
    // Keep .28/.72 chord projections: pushing to .1/.9 clears the path but can
    // move its own centered amount label onto the endpoint text. These points
    // remain between endpoints and only label-obstructed routes pay for them.
    if(endpointApproach)for(const side of [1,-1]){add(side*amplitude*1.8,span*.22);add(side*amplitude*1.8,-span*.22);}
    if(endpointApproach)for(const [endpoint,along]of [[source,.28],[target,.72]]) {
      const label=rectangle(endpoint.label);if(!label)continue;
      const projected=[[label.minX,label.minY],[label.minX,label.maxY],[label.maxX,label.minY],[label.maxX,label.maxY]]
        .map(([x,y])=>({along:((x-source.x)*tx+(y-source.y)*ty)/span,normal:(x-source.x)*nx+(y-source.y)*ny}));
      const low=Math.max(.02,Math.min(...projected.map(point=>point.along))),
        high=Math.min(.98,Math.max(...projected.map(point=>point.along)));
      if(high<low)continue;
      // For a control at chord fraction c, longitudinal position is
      // u=2c*t+(1-2c)*t*t. Invert it at the text's near/far planes, then
      // derive the normal offset from the smallest Bezier weight there.
      const weight=u=>{const t=u/(along+Math.sqrt(along*along+(1-2*along)*u));return 2*t*(1-t);};
      const minimumWeight=Math.max(.04,Math.min(weight(low),weight(high)));
      const width=Math.max(0,Number(edge.labelWidth)||0),height=Math.max(0,Number(edge.labelHeight)||0);
      const amountRadius=width&&height?(width/2+4)*Math.abs(nx)+(height/2+4)*Math.abs(ny):0;
      for(const side of [1,-1]) {
        const extent=Math.max(0,...projected.map(point=>side*point.normal));
        // Also clear this relation's centered amount label, which is wider
        // than the path. Actual text geometry determines the offset; no fixed
        // large bow or extra sampling pass is used. All four additions are
        // capped at 240 and still pass the ordinary budget/containment gates.
        const required=Math.max((extent+6)/minimumWeight,2*(extent+amountRadius+6));
        const normal=Math.min(240,required);
        if(normal>amplitude*1.8+EPS)add(side*normal,(along-.5)*span);
      }
    }
    return result;
  }
  function excessiveDetour(edge,route,scene) {
    if(!route)return false;
    const direct=sampleRoute({...edge,type:'line',controlPoints:[],sampledPoints:null,labelAnchor:null},scene.nodes);
    // Short clear arcs are intentional. Only examine alternatives when the
    // actual sampled route detours by at least 12 world pixels and 8 percent.
    return direct&&route.length-direct.length>=Math.max(12,direct.length*.08);
  }
  function plan(input={}) {
    // A simple topology does not prove that its curved paths or labels are
    // clear. The same bounded geometric checks apply to all automatic routes.
    const capInfo=obstacleBudget(input);
    if(capInfo)return {routes:[],stats:{...capInfo,eligible:0,candidates:0,checks:0,limited:true,skipped:'obstacle-budget'}};
    const scene=model(input),baseline=summary(scene,input.maxChecks),budget=work(input.maxChecks),changes=new Map();
    const eligible=scene.edges.filter(edge=>!edge.manual&&edge.source!==edge.target&&(edge.type||'quadratic')==='quadratic');
    // An incomplete baseline can never admit a proposal. Do not spend another
    // candidate budget and final scan proving the same guaranteed rejection.
    if(!baseline.complete)return {routes:[],stats:{edges:scene.edges.length,eligible:eligible.length,assessed:0,candidates:0,
      checks:0,budget:budget.limit,limited:true,outsideBounds:0,conservative:input.conservative===true,
      baseline,final:baseline,proposed:null,rejected:'quality-budget',skipped:'incomplete-baseline'}};
    const routingBounds=rectangle(input.routingBounds),conservative=input.conservative===true;
    const contained=route=>!routingBounds||route.points.every(point=>point.x>=routingBounds.minX-EPS&&point.x<=routingBounds.maxX+EPS
      &&point.y>=routingBounds.minY-EPS&&point.y<=routingBounds.maxY+EPS)&&(!route.label||route.label.minX>=routingBounds.minX-EPS
        &&route.label.maxX<=routingBounds.maxX+EPS&&route.label.minY>=routingBounds.minY-EPS&&route.label.maxY<=routingBounds.maxY+EPS);
    const cap=Math.max(1,Math.min(1000,Math.floor(Number(input.maxEdges)||320)));let tried=0,assessed=0,outsideBounds=0;
    for(const edge of eligible.slice(0,cap)) {
      if(budget.limited)break;
      let route=scene.routes.get(edge.id),value=score(edge,route,scene,budget);if(!value.complete)break;assessed++;
      // Avoid candidate work on clear, already-short geometry. Long clear arcs
      // can still double back; reverse directions retain their independent lane.
      if(METRICS.every(key=>!value[key])&&!excessiveDetour(edge,route,scene)&&(!edge.hasReverse||value.lane===0))continue;
      const original=value;let control=null;
      for(const point of candidates(edge,scene,value.nodeLabelHits>0)) {
        const candidate=sampleRoute({...edge,controlPoints:[point],sampledPoints:null,labelAnchor:null},scene.nodes);tried++;
        if(!candidate||!contained(candidate)){outsideBounds++;continue;}
        const candidateValue=score(edge,candidate,scene,budget);if(!candidateValue.complete)break;
        if(better(candidateValue,value,conservative,original)){route=candidate;value=candidateValue;control=point;}
      }
      if(control) {const replacement={id:edge.id,source:edge.source,target:edge.target,type:'quadratic',controlPoints:[control],curvePolicy:'obstacle-aware'};
        changes.set(edge.id,replacement);scene.install(edge,route);}
    }
    const final=summary(scene,input.maxChecks);
    const limited=budget.limited||eligible.length>cap||!baseline.complete||!final.complete;
    let rejected=null;
    if(limited)rejected='quality-budget';
    else if(conservative&&METRICS.some(key=>final[key]>baseline[key]))rejected='quality-regression';
    else for(const key of METRICS){if(final[key]!==baseline[key]){if(final[key]>baseline[key])rejected='quality-regression';break;}}
    if(rejected)changes.clear();
    return {routes:[...changes.values()],stats:{edges:scene.edges.length,eligible:eligible.length,assessed,candidates:tried,
      checks:budget.checks,budget:budget.limit,limited,outsideBounds,conservative,baseline,final:rejected?baseline:final,proposed:rejected?final:null,rejected}};
  }
  return Object.freeze({plan,analyze,sampleRoute,rebase});
});
