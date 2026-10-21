import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// Exercise the actual self-contained export, including the embedded style policy.
const html = readFileSync(0, 'utf8');
const presentation = html.match(/<script id="graph-presentation">([\s\S]*?)<\/script>/)?.[1];
assert.ok(presentation, 'the output embeds its own effective style policy');
const labelGeometry = html.match(/<script id="graph-label-geometry">([\s\S]*?)<\/script>/)?.[1];
assert.ok(labelGeometry, 'offline HTML embeds the same plain-text path-gap implementation');
const graphRouting = html.match(/<script id="graph-routing">([\s\S]*?)<\/script>/)?.[1];
assert.ok(graphRouting, 'offline HTML retains the shared route planner without fetching a resource');
const routeQuality = html.match(/<script id="graph-route-quality">([\s\S]*?)<\/script>/)?.[1];
assert.ok(routeQuality, 'offline HTML embeds the actual rendered-path quality gate');
const runtime = html.split('<script>\nvar DATA')[1]?.split('</script>')[0];
assert.ok(runtime);
const plain = value => JSON.parse(JSON.stringify(value));
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} differs from ${expected}`);
let canvasBoundsAvailable=true, loseBoundsAfterUpdate=false;
class Shape {
  constructor(kind, name, attrs, owner) { this.kind = kind; this.name = name; this.attrs = { ...attrs }; this.owner = owner; this.visible = true; }
  get(key) { return this[key]; }
  attr(key, value) {
    if (arguments.length === 0) return this.attrs;
    if (typeof key === 'object') { Object.assign(this.attrs, key); return; }
    if (arguments.length > 1) { this.attrs[key] = value; return; }
    return this.attrs[key];
  }
  hide() { this.visible = false; }
  show() { this.visible = true; }
  remove() { this.owner.children = this.owner.children.filter(shape => shape !== this); }
  toBack() {}
  set(key, value) { this[key] = value; }
  setClip(clip) { this.clip = clip; }
  setMatrix(matrix) { this.matrix = matrix; }
  getPoint(ratio) { return this.owner.item.pathPoint(ratio); }
  getCanvasBBox() {
    if(!canvasBoundsAvailable)return null;
    const box=this.getBBox(),model=this.owner.item.model,zoom=this.owner.item.graph.getZoom();
    const x=model.source?0:model.x,y=model.source?0:model.y;
    return {minX:(box.minX+x)*zoom,minY:(box.minY+y)*zoom,
      maxX:(box.maxX+x)*zoom,maxY:(box.maxY+y)*zoom};
  }
  getBBox() {
    if (this.name === 'key-shape') {
      if (!this.owner.item.model.source) {
        const a = this.attrs, radius = a.r || 0;
        const minX = this.kind === 'image' ? a.x : a.x - radius;
        const minY = this.kind === 'image' ? a.y : a.y - radius;
        const width = this.kind === 'image' ? a.width : radius * 2;
        const height = this.kind === 'image' ? a.height : radius * 2;
        return { minX, minY, maxX: minX + width, maxY: minY + height, width, height };
      }
      const points = Array.from({length:65}, (_,index) => this.getPoint(index/64));
      const minX=Math.min(...points.map(p=>p.x)),minY=Math.min(...points.map(p=>p.y));
      const maxX=Math.max(...points.map(p=>p.x)),maxY=Math.max(...points.map(p=>p.y));
      return {minX,minY,maxX,maxY,width:maxX-minX,height:maxY-minY};
    }
    const x=this.attrs.x||0,y=this.attrs.y||0;
    if (this.kind === 'text' && !this.owner.item.model.source) {
      const fontSize = this.attrs.fontSize || 13, lineHeight = this.attrs.lineHeight || fontSize + 5;
      const lines = String(this.attrs.text || '').split('\n');
      const width = Math.max(...lines.map(line => line.length)) * fontSize * 0.6;
      const minX = x - width / 2 + 2, minY = y - fontSize * 0.7;
      const height = fontSize * 1.2 + (lines.length - 1) * lineHeight;
      return { minX, minY, maxX: minX + width, maxY: minY + height, width, height };
    }
    return {minX:x-45,minY:y-17,maxX:x+45,maxY:y+17,width:90,height:34};
  }
}
class Group {
  constructor(item) { this.children = [];this.item=item; }
  get(key) { return key === 'children' ? this.children : undefined; }
  addShape(kind, options) { const shape = new Shape(kind, options.name, options.attrs, this);shape.capture=options.capture; this.children.push(shape); return shape; }
}
class Item {
  constructor(model, graph) { this.model = model; this.graph = graph; this.group = new Group(this); this.reset(); }
  getModel() { return this.model; }
  getContainer() { return this.group; }
  getKeyShape() { return this.group.children.find(shape=>shape.name==='key-shape'); }
  routeControl() {
    if(this.model.type!=='quadratic')return null;
    if(this.model.controlPoints?.[0])return this.model.controlPoints[0];
    const a=this.graph.nodes.find(item=>item.model.id===this.model.source)?.model;
    const b=this.graph.nodes.find(item=>item.model.id===this.model.target)?.model;
    if(!a||!b)return null;
    const dx=b.x-a.x,dy=b.y-a.y,span=Math.hypot(dx,dy)||1;
    return {x:(a.x+b.x)/2-dy/span*30,y:(a.y+b.y)/2+dx/span*30};
  }
  pathPoint(ratio) {
    if (!this.model.source) return {x:0,y:0};
      const from = this.graph.nodes.find(item => item.model.id === this.model.source).model;
      const to = this.graph.nodes.find(item => item.model.id === this.model.target).model;
      const control=this.routeControl();
      if(control){
        // Independent circle/ellipse clipping: a real rendered quadratic ends
        // at each node boundary, not at its center as a model-only mock would.
        const attach=node=>{const size=node.size||this.graph.options.defaultNode.size;
          const rx=(Array.isArray(size)?size[0]:size)/2,ry=(Array.isArray(size)?size[1]:size)/2;
          const dx=control.x-node.x,dy=control.y-node.y,length=Math.sqrt(dx*dx/(rx*rx)+dy*dy/(ry*ry));
          return length?{x:node.x+dx/length,y:node.y+dy/length}:{x:node.x,y:node.y};};
        const start=attach(from),end=attach(to);
        return {x:(1-ratio)**2*start.x+2*(1-ratio)*ratio*control.x+ratio**2*end.x,
          y:(1-ratio)**2*start.y+2*(1-ratio)*ratio*control.y+ratio**2*end.y};
      }
      return { x: from.x + (to.x - from.x) * ratio, y: from.y + (to.y - from.y) * ratio };
  }
  reset() {
    const edge = !!this.model.source;
    const center = edge ? this.pathPoint(.5) : { x: 0, y: 30 };
    this.group.children = [];
    const start=edge?this.pathPoint(0):{x:0,y:0},end=edge?this.pathPoint(1):{x:0,y:0};
    const control=edge?this.routeControl():null;
    const path=[['M',start.x,start.y],control?['Q',control.x,control.y,end.x,end.y]:['L',end.x,end.y]];
    const size = this.model.size || this.graph.options.defaultNode.size;
    const width = Array.isArray(size) ? size[0] : size, height = Array.isArray(size) ? size[1] : size;
    const kind = edge ? 'path' : this.model.type === 'image' ? 'image' : 'circle';
    const body = kind === 'image' ? { x: -width / 2, y: -height / 2, width, height } : { x: 0, y: 0, r: width / 2 };
    this.group.addShape(kind, { name: 'key-shape', attrs: { ...body,...this.model.style,path } });
    this.group.addShape('text', { name: 'text-shape', attrs: { ...this.model.labelCfg?.style, x: center.x, y: center.y, text: this.model.label } });
    // Deliberately asymmetric baseline/padding: a bg-centering shortcut must fail.
    this.group.addShape('rect', { name: 'text-bg-shape', attrs: { x: center.x - 47, y: center.y - 21, width: 90, height: 34 } });
  }
}
let graph;
class Graph {
  constructor(options) { graph = this; this.options = options; this.events = new Map(); this.fitRequests = []; this.zoom = 1; }
  data(models) { this.models = models; this.nodes = models.nodes.map(model => new Item(model, this)); this.edges = models.edges.map(model => new Item(model, this)); }
  render() {} getNodes() { return this.nodes; } getEdges() { return this.edges; }
  setAutoPaint() {} paint() {} fitView(padding) {
    this.fitRequests.push(plain(padding));
    if (this.nextFitZoom) { const next = this.nextFitZoom; delete this.nextFitZoom; this.zoomTo(next); }
  } changeSize() {}
  getZoom() { return this.zoom; }
  getPointByCanvas(x,y) { return {x:x/this.zoom,y:y/this.zoom}; }
  zoomTo(zoom) { this.zoom = zoom; this.emit('viewportchange', { action: 'zoom' }); }
  on(name, action) { this.events.set(name, [...(this.events.get(name) || []), action]); }
  updateItem(item, model) { Object.assign(item.model,model);item.reset();if(loseBoundsAfterUpdate)canvasBoundsAvailable=false; }
  emit(name, event) { for (const action of this.events.get(name) || []) action(event); }
  move(id, dx, dy) {
    const item = this.nodes.find(item => item.model.id === id); item.model.x += dx; item.model.y += dy;
    item.reset(); for (const edge of this.edges) edge.reset();
    this.emit('node:drag', { item });return item;
  }
  drag(id, dx, dy) { const item=this.move(id,dx,dy);this.emit('node:dragend',{item}); }

}
const windowEvents = new Map(), timers = [], legend = { getBoundingClientRect() { return { height: 72 }; } };
const context = { G6: { Graph }, window: { innerWidth: 1000, innerHeight: 700, addEventListener(name, action) { windowEvents.set(name, action); } },
  document: { body: { appendChild() {} }, getElementById(id) { return id === 'legend' ? legend : { style: {}, getBoundingClientRect() { return { height: 90 }; } }; },
    createElement() { return { style: {}, toDataURL() { return 'data:synthetic'; },
      getContext() { return { translate() {}, rotate() {}, fillText() {} }; } }; } }, setTimeout(action, delay) { timers.push({ action, delay }); } };
context.window.window = context.window;
vm.runInNewContext(presentation + '\n' + labelGeometry + '\n' + graphRouting + '\n' + routeQuality + '\nvar DATA' + runtime, context);
const text = item => item.group.children.find(shape => shape.name === 'text-shape');
const bg = item => item.group.children.find(shape => shape.name === 'text-bg-shape');
const shape = (item, name) => item.group.children.find(shape => shape.name === name);
function assertNodeBottom(item, message = 'node text remains centered below the actual body') {
  const body = item.getKeyShape().getBBox(), label = text(item), box = label.getBBox();
  near((box.minX + box.maxX) / 2, (body.minX + body.maxX) / 2);
  near((box.minY - body.maxY) * graph.getZoom(), 6);
  assert.equal(bg(item), undefined, message);
  assert.equal(shape(item, 'funds-label-leader'), undefined, message);
  assert.equal(label.attr('stroke'), null); assert.equal(label.attr('lineWidth'), 0);
}
const [a, b] = graph.nodes, [manual, automatic] = graph.edges;
assert.deepEqual(graph.fitRequests, [[40, 40, 100, 40]], 'initial fit leaves room for the rendered legend');
timers.find(timer => timer.delay === 150).action();
assert.deepEqual(graph.fitRequests.at(-1), [40, 40, 100, 40]);
legend.getBoundingClientRect = () => ({ height: 104 });
windowEvents.get('resize')();
assert.deepEqual(graph.fitRequests.at(-1), [40, 40, 132, 40], 'resize remeasures the existing legend');
delete legend.getBoundingClientRect; legend.offsetHeight = 56;
graph.emit('node:dblclick', {});
assert.deepEqual(graph.fitRequests.at(-1), [40, 40, 84, 40], 'offsetHeight supports older adapters');
delete legend.offsetHeight;
graph.emit('node:dblclick', {});
assert.deepEqual(graph.fitRequests.at(-1), [40, 40, 40, 40], 'missing measurement retains the old 40px padding');
const note=shape(a,'funds-node-annotation');
assert.ok(note,'the downloaded standalone HTML renders the custom annotation');
assert.equal(note.attr('text'),'实际控制人（合成）\n</script><script>window.injected=true</script>');
assert.equal(note.attr('fontSize'),20);assert.equal(note.attr('lineHeight'),28);
assert.equal(note.attr('fontFamily'),'KaiTi');assert.equal(note.attr('fill'),'#c084fc');
assert.equal(note.attr('x'),23);assert.equal(note.attr('textBaseline'),'bottom');
assert.equal(a.model.annotation.id,a.model.id,'a supplied annotation cannot target a different account');
assert.equal(a.model.annotation.html,undefined);assert.equal(context.window.injected,undefined);
function assertNoteAnchor(){
  const current=shape(a,'funds-node-annotation'),body=a.getKeyShape().getCanvasBBox();
  near((a.model.y+current.attr('y'))*graph.getZoom(),body.minY-10+a.model.annotation.dy*graph.getZoom());
}
assertNoteAnchor();
graph.zoomTo(2);graph.emit('viewportchange',{action:'zoom'});assertNoteAnchor();
graph.move(a.model.id,17,23);assertNoteAnchor();graph.move(a.model.id,-17,-23);
graph.zoomTo(1);graph.emit('viewportchange',{action:'zoom'});
assert.equal(a.model.labelCfg.style.fontFamily, 'Arial');
assert.equal(a.model.labelCfg.style.lineHeight, 28, 'legacy short lineHeight is clamped to the effective font');
assert.equal(manual.model.labelCfg.style.fontSize, 13);
assert.equal(manual.model.labelCfg.style.lineHeight, 18);
assert.equal(a.model.labelCfg.style.background, undefined);
assert.equal(manual.model.labelCfg.style.background, undefined);
assert.equal(manual.model.style.lineCap, 'round');
assert.equal(manual.model.style.lineJoin, 'round');
assert.equal(manual.model.style.lineAppendWidth, 8);
assert.equal(manual.model.style.lineWidth, 7);
assert.deepEqual(plain(manual.model.style.lineDash), [2, 3]);
assert.equal(a.model.style.fill, '#63b69d');
assert.equal(shape(a, 'funds-identity-ring').attr('stroke'), '#e8c46a');
assert.equal(shape(b, 'funds-identity-ring').attr('stroke'), '#a79be0');
assert.equal(shape(b, 'funds-photo-border').attr('stroke'), '#123456');
assert.equal(shape(b, 'funds-photo-border').attr('lineWidth'), 4);
assert.equal(shape(b, 'funds-label-leader'), undefined, 'hidden text cannot leave a leader visible');
assertNodeBottom(a, 'old snapshot side/top offsets never displace a node label');
assertNodeBottom(b, 'hidden photograph labels use the same bottom anchor');
assert.equal(bg(a), undefined);
assert.equal(shape(a, 'funds-label-leader'), undefined, 'old snapshot leader geometry is ignored');
assert.equal(text(manual).attr('x'), 325); assert.equal(text(manual).attr('y'), 200);
assert.equal(bg(manual), undefined);
assert.equal(text(automatic).attr('x'), 250); assert.equal(text(automatic).attr('y'), 200);
assert.equal(shape(automatic, 'funds-label-leader'), undefined);
assert.equal([...a.group.children, ...b.group.children].some(shape => /selection/.test(shape.name)), false);
function assertInline(item) {
  const key=item.getKeyShape(),visible=shape(item,'funds-edge-visible-path'),label=text(item),box=label.getBBox();
  assert.ok(visible,'inline label has a separate visible path');
  assert.equal(key.capture,true,'full logical path remains the hit target inside the gap');
  assert.equal(key.attr('opacity'),0);assert.equal(key.clip,null);
  assert.equal(key.attr('endArrow'),false,'only the visible copy draws the real target arrow');
  assert.equal(visible.capture,false);assert.equal(visible.attr('endArrow').fill,item.model.style.stroke);
  assert.deepEqual(plain(visible.attr('path')),plain(key.attr('path')));
  const clip=visible.clip.attrs.path;
  assert.deepEqual(plain(clip.slice(5)),[['M',box.minX-4,box.minY-4],['L',box.minX-4,box.maxY+4],
    ['L',box.maxX+4,box.maxY+4],['L',box.maxX+4,box.minY-4],['Z']], 'the transparent gap pads text by 4px');
  assert.equal(label.attr('stroke'),null);assert.equal(label.attr('lineWidth'),0);
  assert.equal(bg(item),undefined);assert.equal(shape(item,'funds-label-leader'),undefined);
}
assertInline(manual);assertInline(automatic);

graph.drag('SYN-B', 200, 80);
near(text(manual).attr('x'), 475); near(text(manual).attr('y'), 260);
assert.equal(text(automatic).attr('x'), 350); assert.equal(text(automatic).attr('y'), 240);
assertInline(manual);assertInline(automatic);
assertNodeBottom(a, 'repeated drag callbacks do not accumulate offsets');
graph.drag('SYN-A', 20, -10);
assertNodeBottom(a); assertNodeBottom(b);
assert.equal(shape(a,'funds-label-leader'),undefined);
assert.equal(shape(b, 'funds-label-leader'), undefined);
assert.equal(text(b).visible, false);
assert.equal(manual.model.data.total, '10000000000000000.01');

// Earlier automatic collision offsets are migrated back onto their own midpoint.
const oldData = plain(context.DATA);
delete oldData.view;
for (const node of oldData.nodes) {
  delete node.labelOffset; delete node.labelLeader; delete node.photoBorder;
}
for (const edge of oldData.edges) {
  delete edge.labelRatio; delete edge.labelManual; delete edge.labelLeader;
}
const source = presentation + '\n' + labelGeometry + '\n' + graphRouting + '\n' + routeQuality + '\nvar DATA' + runtime;
const withData = data => source.replace(/var DATA = [\s\S]*?;\nfunction buildWM/, () => `var DATA = ${JSON.stringify(data)};\nfunction buildWM`);
vm.runInNewContext(withData(oldData), context);
assert.equal(text(graph.edges[0]).attr('x'), 250);
assert.equal(bg(graph.edges[0]), undefined);
graph.drag('SYN-B', 200, 80);
assert.equal(text(graph.edges[0]).attr('x'), 350, 'old collision offsets never drift away from their relationship');
assert.equal(text(graph.edges[0]).attr('y'), 240);

// Malformed optional geometry cannot reach drawing attributes or create a leader.
const malformed = plain(oldData);
malformed.nodes[0].labelOffset = { x: '50', y: 10 };
malformed.nodes[0].labelLeader = { x1: '100', y1: 200, x2: 0, y2: 0 };
malformed.nodes[1].photoBorder = { stroke: 'url(https://invalid.example)', lineWidth: 100 };
malformed.edges[0].labelRatio = 2;
malformed.edges[0].labelStyle = { fontFamily: 'url(evil)', fontSize: 32, lineHeight: 8, opacity: 0 };
vm.runInNewContext(withData(malformed), context);
assertNodeBottom(graph.nodes[0]);
assert.equal(shape(graph.nodes[0], 'funds-label-leader'), undefined);
assert.equal(shape(graph.nodes[1], 'funds-photo-border'), undefined);
assert.equal(text(graph.edges[0]).attr('x'), 250);
assert.equal(graph.edges[0].model.labelCfg.style.fontFamily, 'Arial');
assert.equal(graph.edges[0].model.labelCfg.style.lineHeight, 36);
assert.equal(graph.edges[0].model.labelCfg.style.opacity, undefined);

// Sparse horizontal fallback puts text above a complete line, with the same gap.
const sparse=plain(oldData);sparse.edges=sparse.edges.slice(0,1);
vm.runInNewContext(withData(sparse),context);
let edge=graph.edges[0],key=edge.getKeyShape();
near(text(edge).getBBox().maxY,200-edge.model.style.lineWidth/2-4);
assert.equal(shape(edge,'funds-edge-visible-path'),undefined);assert.equal(key.attr('opacity'),1);
graph.drag('SYN-B',0,160);
assertInline(edge);
near(text(edge).attr('x'),250);near(text(edge).attr('y'),280);
edge.model.labelVisible=false;graph.emit('node:drag',{});
assert.equal(text(edge).visible,false);assert.equal(shape(edge,'funds-edge-visible-path'),undefined);
assert.equal(edge.getKeyShape().attr('opacity'),1,'hidden labels restore the complete visible path');
edge.model.labelVisible=true;graph.emit('node:dragend',{});assertInline(edge);

// Automatic arcs use a short bend perpendicular to their directed chord.
// Sampling the exported drawing detects an endpoint overshoot independently
// of the control-point implementation, including the former screen-right hook.
function assertCompact(item, message='automatic quadratic follows a short, forward-only arc') {
  const from=graph.nodes.find(node=>node.model.id===item.model.source).model;
  const to=graph.nodes.find(node=>node.model.id===item.model.target).model;
  const dx=to.x-from.x,dy=to.y-from.y,span=Math.hypot(dx,dy);
  const controls=item.model.controlPoints;
  assert.equal(controls?.length,1,message);
  const control=controls[0],along=((control.x-from.x)*dx+(control.y-from.y)*dy)/(span*span);
  near(along,.5);
  const bend=((control.x-from.x)*-dy+(control.y-from.y)*dx)/span;
  assert.ok(bend>=0&&bend<=Math.min(28,span*.08)+1e-9,message);
  let previous=-1,length=0,last;
  for(let index=0;index<=64;index++){
    const current=item.getKeyShape().getPoint(index/64);
    const projection=((current.x-from.x)*dx+(current.y-from.y)*dy)/(span*span);
    assert.ok(projection>=previous-1e-9&&projection>=-1e-9&&projection<=1+1e-9,message);
    if(last)length+=Math.hypot(current.x-last.x,current.y-last.y);
    previous=projection;last=current;
  }
  assert.ok(length<=span*1.02,message);
  assert.equal(item.model.curvePolicy,'compact-arc');
}
// Legacy automatic routing is repaired on initial open, before any drag. An
// explicit inline placement still overrides the small-graph label fallback.
const curved=plain(oldData);curved.edges=curved.edges.slice(1);
for(const node of curved.nodes)node.labelVisible=false;
curved.edges[0].labelPlacement='inline';
curved.edges[0].type='quadratic';curved.edges[0].curvePolicy='screen-right';
curved.edges[0].controlPoints=[{x:480,y:100}];
vm.runInNewContext(withData(curved),context);
assertInline(graph.edges[0]);
edge=graph.edges[0];assertCompact(edge,'opening a legacy automatic route removes its saved screen-right hook');
near(text(edge).attr('x'),edge.getKeyShape().getPoint(.5).x);
near(text(edge).attr('y'),edge.getKeyShape().getPoint(.5).y);
graph.drag('SYN-B',20,180);
assertCompact(edge,'an upward payment follows its directed chord without overshooting either endpoint');
assert.equal(edge.model.source,'SYN-B');assert.equal(edge.model.target,'SYN-A');
near(text(edge).attr('x'),edge.getKeyShape().getPoint(.5).x);
near(text(edge).attr('y'),edge.getKeyShape().getPoint(.5).y);assertInline(edge);

// Reverse transfer directions use separate lanes, with no discontinuous
// screen-side switch when a drag crosses the vertical axis.
const bidirectional=plain(curved);
bidirectional.edges.push({...plain(curved.edges[0]),source:'SYN-A',target:'SYN-B',curvePolicy:'compact-arc',controlPoints:[]});
for(const item of bidirectional.edges)item.labelVisible=false;
vm.runInNewContext(withData(bidirectional),context);
for(const item of graph.edges)assertCompact(item);
assert.ok(Math.hypot(graph.edges[0].getKeyShape().getPoint(.5).x-graph.edges[1].getKeyShape().getPoint(.5).x,
  graph.edges[0].getKeyShape().getPoint(.5).y-graph.edges[1].getKeyShape().getPoint(.5).y)>1,
  'opposite transfers cannot paint the same automatic arc');
graph.drag('SYN-B',-299.999,200);
const beforeVertical=plain(graph.edges[0].model.controlPoints[0]);
graph.drag('SYN-B',-.002,0);
const afterVertical=graph.edges[0].model.controlPoints[0];
assert.ok(Math.hypot(beforeVertical.x-afterVertical.x,beforeVertical.y-afterVertical.y)<.01,
  'crossing a vertical chord cannot flip the arc to the other side of the screen');
for(const item of graph.edges)assertCompact(item);
const compactBefore=graph.edges.map(item=>plain(item.model.controlPoints));
const compactPositions=graph.nodes.map(item=>({id:item.model.id,x:item.model.x,y:item.model.y}));
for(let index=0;index<4;index++)graph.emit('node:dragend',{item:graph.nodes[1]});
for(const zoom of [.5,2,1])graph.zoomTo(zoom);
assert.deepEqual(graph.edges.map(item=>plain(item.model.controlPoints)),compactBefore,
  'repeated terminal and camera events cannot accumulate automatic arc drift');
assert.deepEqual(graph.nodes.map(item=>({id:item.model.id,x:item.model.x,y:item.model.y})),compactPositions,
  'arc migration and label refresh never change node positions');

// Missing automatic bend metadata is not evidence of an intentional hand bend.
const defaults=plain(curved);
Object.assign(defaults.edges[0],{curvePolicy:null,routeManual:false,controlPoints:[]});
vm.runInNewContext(withData(defaults),context);assertCompact(graph.edges[0]);
delete defaults.edges[0].routeManual;
vm.runInNewContext(withData(defaults),context);assertCompact(graph.edges[0]);
const legacyManual=plain(defaults);
Object.assign(legacyManual.edges[0],{controlPoints:[{x:480,y:100}]});
vm.runInNewContext(withData(legacyManual),context);
graph.drag('SYN-B',20,180);
assert.deepEqual(plain(graph.edges[0].model.controlPoints),[{x:480,y:100}],
  'unmarked legacy hand bends are not reinterpreted as default arcs');
const explicitManual=plain(curved);
Object.assign(explicitManual.edges[0],{routeManual:true,labelManual:true,labelRatio:.3});
vm.runInNewContext(withData(explicitManual),context);
graph.drag('SYN-B',20,180);
assert.deepEqual(plain(graph.edges[0].model.controlPoints),[{x:480,y:100}],
  'even an old routing marker cannot override an explicit hand bend');
assert.equal(graph.edges[0].model.labelRatio,.3);
for(const type of ['line','polyline']){
  const straight=plain(defaults);Object.assign(straight.edges[0],{type,routeManual:false});
  vm.runInNewContext(withData(straight),context);graph.drag('SYN-B',20,180);
  assert.equal(graph.edges[0].model.type,type,'automatic arc repair preserves the chosen line type');
  assert.equal(graph.edges[0].model.controlPoints?.length||0,0);
}

// A sparse tree can become geometrically complex without changing membership.
// Default arcs remain compact around their current endpoints; unrelated routes
// and explicit user bends retain their complete saved geometry.
const evolving=plain(oldData);
evolving.nodes.push({id:'SYN-C',x:100,y:400,label:'Synthetic C'},
  {id:'SYN-D',x:400,y:400,label:'Synthetic D'});
evolving.edges=[{...plain(oldData.edges[0]),type:'quadratic',labelPlacement:'above',curvePolicy:'screen-right',controlPoints:[{x:250,y:200}]},
  {source:'SYN-A',target:'SYN-C',type:'quadratic',label:'branch',labelPlacement:'inline',curvePolicy:'screen-right',controlPoints:[{x:240,y:240}]},
  {source:'SYN-C',target:'SYN-D',type:'quadratic',label:'manual bend',labelPlacement:'inline',routeManual:true,controlPoints:[{x:250,y:450}]}];
vm.runInNewContext(withData(evolving),context);
const [trunk,branch,manualBend]=graph.edges;
assert.equal(shape(trunk,'funds-edge-visible-path'),undefined);
assertCompact(branch);
assertInline(manualBend,'an explicit inline placement never becomes above in a simple graph');
const manualControl=plain(manualBend.model.controlPoints);
const trunkControl=plain(trunk.model.controlPoints);
graph.move('SYN-C',150,-300);
assertInline(trunk);assertInline(branch);assertInline(manualBend);
assert.deepEqual(plain(trunk.model.controlPoints),trunkControl,'moving another node cannot clear an unrelated automatic route');
assertCompact(branch,'the incident automatic arc remains compact across a geometric crossing');
assert.deepEqual(plain(manualBend.model.controlPoints),manualControl,'manual bends survive the complex transition');
graph.move('SYN-C',-150,300);
assert.equal(shape(trunk,'funds-edge-visible-path'),undefined,'horizontal above labels return after the crossing is removed');
assertCompact(branch,'returning to the original endpoints recovers a compact arc');
assert.deepEqual(plain(manualBend.model.controlPoints),manualControl);
assertInline(manualBend);

// Actual complex automatic routes are exported as accepted geometry. Initial
// display, repeated fit and zoom preserve every control point exactly; dragging
// only rebases the incident routes, while unrelated and manual bends stay put.
const routed=plain(oldData);
routed.nodes.push({id:'SYN-C',x:100,y:400,label:'Synthetic C'},
  {id:'SYN-D',x:400,y:400,label:'Synthetic D'});
const relation=(source,target,controlPoints,extra={})=>({source,target,type:'quadratic',
  label:'CNY 10000000000000000.01',labelPlacement:'inline',curvePolicy:'obstacle-aware',
  routeManual:false,controlPoints,...extra});
routed.edges=[relation('SYN-A','SYN-B',[{x:250,y:140}]),
  relation('SYN-B','SYN-C',[{x:520,y:380}]),
  relation('SYN-C','SYN-D',[{x:250,y:480}]),
  relation('SYN-D','SYN-A',[{x:40,y:40}],{routeManual:true,labelManual:true,labelRatio:0.3}),
  relation('SYN-A','SYN-C',[{x:30,y:260}],{type:'polyline',routeManual:true})];
for(const item of routed.edges)item.routeAutoEligible=false;
vm.runInNewContext(withData(routed),context);
const originalRoutes=graph.edges.map(item=>plain(item.model.controlPoints));
const routePositions=graph.nodes.map(item=>({id:item.model.id,x:item.model.x,y:item.model.y}));
assert.equal(graph.edges[0].model.curvePolicy,'obstacle-aware','the accepted automatic routing marker survives export');
assert.deepEqual(graph.edges.map(item=>plain(item.model.controlPoints)),originalRoutes);
for(const zoom of [0.5,1.7,1]){
  graph.zoomTo(zoom);graph.emit('node:dblclick',{});
  assert.deepEqual(graph.edges.map(item=>plain(item.model.controlPoints)),originalRoutes,'camera actions never plan a different route');
}
assert.deepEqual(graph.nodes.map(item=>({id:item.model.id,x:item.model.x,y:item.model.y})),routePositions);
graph.drag('SYN-B',20,80);
assert.deepEqual(plain(graph.edges[0].model.controlPoints),[{x:276,y:176}]);
assert.deepEqual(plain(graph.edges[1].model.controlPoints),[{x:492,y:472}]);
assert.deepEqual(graph.edges.slice(2).map(item=>plain(item.model.controlPoints)),originalRoutes.slice(2),
  'unrelated automatic routes, manual quadratic bends and manual polylines remain unchanged');
for(const item of graph.edges.slice(0,3)){
  assertInline(item);
  near(text(item).attr('x'),item.getKeyShape().getPoint(.5).x);
  near(text(item).attr('y'),item.getKeyShape().getPoint(.5).y);
}
graph.emit('node:dragend',{item:graph.nodes[1]});
assert.deepEqual(plain(graph.edges[0].model.controlPoints),[{x:276,y:176}],'repeated terminal events do not accumulate route drift');
graph.drag('SYN-B',-20,-80);
assert.deepEqual(graph.edges.map(item=>plain(item.model.controlPoints)),originalRoutes,'returning to the saved endpoints recovers exact geometry');

// A manual drag can remove the geometric crossing of an otherwise sparse
// tree. That change must not reinterpret its accepted obstacle-aware bend or
// replace unrelated routes merely because the topology now qualifies simple.
const simplifyingTree=plain(evolving);
Object.assign(simplifyingTree.nodes[2],{x:250,y:100});
Object.assign(simplifyingTree.edges[0],{routeManual:false,curvePolicy:'obstacle-aware',controlPoints:[{x:250,y:90}]});
Object.assign(simplifyingTree.edges[1],{routeManual:false,curvePolicy:'obstacle-aware',controlPoints:[{x:135,y:90}]});
for(const item of simplifyingTree.edges)item.routeAutoEligible=false;
vm.runInNewContext(withData(simplifyingTree),context);
const fixedTreeRoutes=[plain(graph.edges[0].model.controlPoints),plain(graph.edges[2].model.controlPoints)];
graph.drag('SYN-C',-150,300);
assert.deepEqual(plain(graph.edges[1].model.controlPoints),[{x:180,y:300}],
  'an accepted automatic bend follows its endpoint frame when the tree becomes geometrically simple');
assert.equal(graph.edges[1].model.curvePolicy,'obstacle-aware');
assert.deepEqual(plain(graph.edges[0].model.controlPoints),fixedTreeRoutes[0],'a newly simple drawing cannot replan an unrelated automatic path');
assert.deepEqual(plain(graph.edges[2].model.controlPoints),fixedTreeRoutes[1],'manual bends remain fixed across the geometry transition');
graph.emit('node:dragend',{item:graph.nodes[2]});
assert.deepEqual(plain(graph.edges[1].model.controlPoints),[{x:180,y:300}]);

// A graph first saved while complex still has automatic routing eligibility
// after reopening: null curvePolicy is not evidence of a manual route.
const reopened=plain(evolving);reopened.nodes[2].x=250;reopened.nodes[2].y=100;
for(const item of reopened.edges.slice(0,2)){
  item.curvePolicy=null;item.routeManual=false;item.controlPoints=[];item.labelPlacement='inline';
}
vm.runInNewContext(withData(reopened),context);
assert.equal(graph.edges[1].model.routeManual,false);
assert.equal(graph.edges[2].model.routeManual,true);
graph.move('SYN-C',-150,300);
graph.emit('node:dragend',{item:graph.nodes[2]});
assert.ok(graph._fundsRouteQuality.before?.valid,'reopened default curves remain eligible for an actual route assessment');
assert.equal(graph.edges[1].model.routeManual,false);
assert.ok(graph.edges[1].getKeyShape().getBBox().height>0,'a conservative rejection retains the usable default curve');
assert.deepEqual(plain(graph.edges[2].model.controlPoints),manualControl);
assertInline(graph.edges[0]);assertInline(graph.edges[1]);

// Filtering a complex source graph to a small forest must not confer the
// simple-graph routing policy on that partial export, even after dragging.
const filtered=plain(sparse);
Object.assign(filtered.edges[0],{type:'quadratic',routeManual:false,routeAutoEligible:false,
  curvePolicy:null,controlPoints:[],labelPlacement:'above'});
vm.runInNewContext(withData(filtered),context);
edge=graph.edges[0];assert.equal(edge.model.routeAutoEligible,false);assertInline(edge);
graph.drag('SYN-B',20,180);
assert.equal(edge.model.controlPoints?.length||0,0,'partial source scope prevents a new automatic right arc');
assertInline(edge);
graph.drag('SYN-B',-20,-180);
assertInline(edge,'a partial export does not gain above labels when returned to a horizontal forest');
assert.equal(edge.model.controlPoints?.length||0,0);
assert.equal(edge.model.data.total,'10000000000000000.01');

// A partial export can retain an accepted complex route without having enough
// topology to plan a new one. Existing bends still follow their own endpoints.
const partialRoute=plain(filtered);
Object.assign(partialRoute.edges[0],{curvePolicy:'obstacle-aware',controlPoints:[{x:250,y:140}]});
vm.runInNewContext(withData(partialRoute),context);
edge=graph.edges[0];
assert.deepEqual(plain(edge.model.controlPoints),[{x:250,y:140}]);
graph.drag('SYN-B',20,180);
assert.deepEqual(plain(edge.model.controlPoints),[{x:296,y:226}]);
assert.equal(edge.model.curvePolicy,'obstacle-aware','a partial forest cannot acquire the simple right-arc policy');
assertInline(edge);

// A partial export must also keep legacy automatic geometry: the source
// topology needed to decide whether that route was obstacle-driven is absent.
const partialLegacy=plain(partialRoute);
Object.assign(partialLegacy.edges[0],{curvePolicy:'screen-right',controlPoints:[{x:480,y:100}]});
vm.runInNewContext(withData(partialLegacy),context);
assert.deepEqual(plain(graph.edges[0].model.controlPoints),[{x:480,y:100}]);
graph.drag('SYN-B',20,180);
near(graph.edges[0].model.controlPoints[0].x,1696/3);
near(graph.edges[0].model.controlPoints[0].y,964/3);
assert.equal(graph.edges[0].model.curvePolicy,'screen-right','a partial source cannot migrate its legacy routing policy');

// Same-account loops and temporarily coincident endpoints have no chord from
// which to derive an arc. Keep their geometry until a real chord exists.
const coincident=plain(curved);
coincident.nodes[1].x=coincident.nodes[0].x;coincident.nodes[1].y=coincident.nodes[0].y;
vm.runInNewContext(withData(coincident),context);
assert.deepEqual(plain(graph.edges[0].model.controlPoints),[{x:480,y:100}],
  'coincident endpoints cannot collapse an existing automatic bend');
graph.drag('SYN-B',80,60);
assert.ok(graph.edges[0].model.controlPoints.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)));
assert.ok(graph.edges[0].getKeyShape().getBBox().width>0,'separating coincident endpoints restores a visible route');
const looped=plain(curved);
Object.assign(looped.edges[0],{source:'SYN-A',target:'SYN-A'});
vm.runInNewContext(withData(looped),context);
graph.drag('SYN-A',20,30);
assert.deepEqual(plain(graph.edges[0].model.controlPoints),[{x:500,y:130}],
  'a self-relation follows its node without collapsing into a zero-length quadratic');

// Complete, clear accepted geometry has no quality improvement to make.
// It still follows its endpoint frame exactly at drag-end, not only while moving.
const clearAccepted=plain(curved);
for(const node of clearAccepted.nodes)node.labelVisible=false;
Object.assign(clearAccepted.edges[0],{source:'SYN-A',target:'SYN-B',curvePolicy:'obstacle-aware',
  routeAutoEligible:true,controlPoints:[{x:250,y:230}],labelVisible:false});
vm.runInNewContext(withData(clearAccepted),context);
assert.deepEqual(plain(graph.edges[0].model.controlPoints),[{x:250,y:230}]);
graph.drag('SYN-B',20,80);
assert.deepEqual(plain(graph.edges[0].model.controlPoints),[{x:252,y:272}],
  'a clear complete-topology route retains the exact endpoint rebase after the gate');
assert.equal(graph._fundsRouteQuality.changed,0,'clear geometry cannot trigger gratuitous replanning');
graph.drag('SYN-B',-20,-80);
assert.deepEqual(plain(graph.edges[0].model.controlPoints),[{x:250,y:230}]);

// Saved side/top offsets, font changes, photograph bounds and later zoom/fit
// share the same measured six-screen-pixel anchor. No geometry/facts move.
const anchored = plain(oldData);
anchored.nodes[0].label = '合成丙公司\nSYN-C003\n完整标签';
anchored.nodes[0].size = 72;
anchored.nodes[0].labelOffset = { x: 250, y: -180 };
anchored.nodes[0].labelStyle = { fontSize: 24, lineHeight: 31 };
anchored.nodes[1].size = [80, 52];
anchored.nodes[1].labelOffset = { x: -300, y: -220 };
anchored.nodes[1].labelVisible = true;
anchored.nodes[1].data.in_amt = '10000000000000000.01';
vm.runInNewContext(withData(anchored), context);
const positionsBefore = graph.nodes.map(item => ({ id: item.model.id, x: item.model.x, y: item.model.y }));
for (const zoom of [0.4, 2, 1]) {
  graph.zoomTo(zoom);
  for (const item of graph.nodes) assertNodeBottom(item);
  graph.emit('node:dragend', {});
  for (const item of graph.nodes) assertNodeBottom(item, 'a subsequent drag callback retains the anchor');
}
graph.nextFitZoom = 0.73; graph.emit('node:dblclick', {});
for (const item of graph.nodes) assertNodeBottom(item, 'fit-induced zoom retains six screen pixels');
assert.deepEqual(graph.nodes.map(item => ({ id: item.model.id, x: item.model.x, y: item.model.y })), positionsBefore);
assert.equal(graph.nodes[1].model.data.in_amt, '10000000000000000.01');

// A legacy arc can be shorter yet cross the target's account label. Opening
// the export must never trade the old detour for a new text/arrow obstruction.
const labelObstacle={nodes:[{id:'R-A',x:100,y:100,label:'A\nSYN-A',size:32},
  {id:'R-B',x:100,y:400,label:'B\nSYN-B',size:32}],
  edges:[{source:'R-B',target:'R-A',type:'quadratic',routeManual:false,
    curvePolicy:'screen-right',controlPoints:[{x:400,y:250}],label:'amount',labelPlacement:'inline'}]};
vm.runInNewContext(withData(labelObstacle),context);
const qualityApi=context.FundsGraphRouteQuality;
function publicScene(){
  const world=shape=>{const b=shape.getCanvasBBox();return {minX:b.minX/graph.getZoom(),minY:b.minY/graph.getZoom(),
    maxX:b.maxX/graph.getZoom(),maxY:b.maxY/graph.getZoom()};};
  return {nodes:graph.nodes.map(item=>({id:item.model.id,body:world(item.getKeyShape()),label:item.model.labelVisible===false?null:world(text(item))})),
    edges:graph.edges.map((item,index)=>({id:String(index),source:item.model.source,target:item.model.target,
      path:item.getKeyShape().attr('path'),label:item.model.labelVisible===false?null:world(text(item))}))};
}
let measured=qualityApi.evaluate(publicScene());
assert.equal(measured.coverage.complete,true);
assert.equal(measured.counts.edgeNodeLabel,0,'legacy migration cannot introduce a new target-label collision');
assert.equal(graph._fundsRouteQuality?.accepted,true,'a real-path quality gate accepts the verified repair');

assert.equal(graph._fundsRouteQuality.planner.baseline.nodeLabelHits,1,
  'the tentative short arc genuinely hits the endpoint label in the planner');
assert.equal(graph._fundsRouteQuality.planner.final.nodeLabelHits,0);
assert.equal(graph.edges[0].model.curvePolicy,'obstacle-aware');

// Accepted routes stay untouched on open. During a drag the same accepted
// frame follows continuously; only the terminal event may run a new planner.
const dragObstacle=plain(labelObstacle);
dragObstacle.nodes[1].x=300;
dragObstacle.edges[0].curvePolicy='obstacle-aware';
vm.runInNewContext(withData(dragObstacle),context);
assert.deepEqual(plain(graph.edges[0].model.controlPoints),[{x:400,y:250}]);
assert.equal(graph._fundsRouteQuality,undefined,'accepted obstacle routes are not replanned on initial display');
const moving=graph.move('R-B',-200,0),beforeTerminal=qualityApi.evaluate(publicScene());
assert.equal(beforeTerminal.counts.edgeNodeLabel,1,'moving the endpoint can introduce a new real label obstruction');
assert.equal(graph._fundsRouteQuality,undefined,'drag frames do not invoke the expensive route planner');
const dragFits=plain(graph.fitRequests),dragCoordinates=graph.nodes.map(item=>({id:item.model.id,x:item.model.x,y:item.model.y}));
graph.emit('node:dragend',{item:moving});
measured=qualityApi.evaluate(publicScene());
assert.equal(measured.counts.edgeNodeLabel,0,'drag-end accepts a new side approach that clears the target label');
assert.equal(graph._fundsRouteQuality.accepted,true);
assert.equal(graph._fundsRouteQuality.changed,1);
for(const kind of Object.keys(measured.counts))assert.ok(measured.counts[kind]<=beforeTerminal.counts[kind]);
assert.deepEqual(graph.fitRequests,dragFits,'a terminal route correction never fits or zooms the graph');
assert.deepEqual(graph.nodes.map(item=>({id:item.model.id,x:item.model.x,y:item.model.y})),dragCoordinates);
const fixed=plain(graph.edges[0].model.controlPoints),lastGate=graph._fundsRouteQuality;
graph.emit('node:dragend',{item:moving});
assert.deepEqual(plain(graph.edges[0].model.controlPoints),fixed);
assert.equal(graph._fundsRouteQuality,lastGate,'duplicate drag-end callbacks cannot start another planning pass');

// Read-only/older hosts without real canvas bounds cannot certify a new route.
canvasBoundsAvailable=false;
vm.runInNewContext(withData(labelObstacle),context);
assert.deepEqual(plain(graph.edges[0].model.controlPoints),[{x:400,y:250}]);
assert.equal(graph.edges[0].model.curvePolicy,'screen-right');
assert.equal(graph._fundsRouteQuality.reason,'missing-geometry');
canvasBoundsAvailable=true;

// An explicit partial view remains protected even if an old payload claims
// routeAutoEligible:true on its individual edge.
const declaredPartial=plain(labelObstacle);
declaredPartial.view={scope:'visible',mode:'filtered',total_nodes:10,total_edges:20,visible_nodes:2,visible_edges:1};
declaredPartial.edges[0].routeAutoEligible=true;
vm.runInNewContext(withData(declaredPartial),context);
assert.deepEqual(plain(graph.edges[0].model.controlPoints),[{x:400,y:250}]);
graph.drag('R-B',20,20);
assert.equal(graph._fundsRouteQuality.reason,'partial-topology');
assert.equal(graph.edges[0].model.curvePolicy,'screen-right');

// A hard obstacle cap or incomplete real-path budget keeps the old geometry.
const budgeted=plain(labelObstacle);
budgeted.edges=Array.from({length:1501},()=>plain(labelObstacle.edges[0]));
vm.runInNewContext(withData(budgeted),context);
assert.equal(graph._fundsRouteQuality.reason,'obstacle-budget');
assert.ok(graph.edges.every(item=>item.model.curvePolicy==='screen-right'));
assert.ok(graph.edges.every(item=>item.model.controlPoints[0].x===400));
budgeted.edges=budgeted.edges.slice(0,100);
vm.runInNewContext(withData(budgeted),context);
assert.equal(graph._fundsRouteQuality.reason,'quality-budget');
assert.equal(graph._fundsRouteQuality.before.coverage.complete,false);
assert.ok(graph.edges.every(item=>item.model.curvePolicy==='screen-right'));
assert.ok(graph.edges.every(item=>item.model.controlPoints[0].x===400));

// A host can lose its real bounds while a tentative new key shape is installed.
// That incomplete check must atomically restore both geometry and its old frame.
loseBoundsAfterUpdate=true;
vm.runInNewContext(withData(labelObstacle),context);
assert.equal(graph._fundsRouteQuality.reason,'missing-geometry');
assert.equal(graph._fundsRouteQuality.accepted,false);
assert.deepEqual(plain(graph.edges[0].model.controlPoints),[{x:400,y:250}]);
assert.equal(graph.edges[0].model.curvePolicy,'screen-right');
loseBoundsAfterUpdate=false;canvasBoundsAvailable=true;
graph.move('R-B',20,20);
assert.deepEqual(plain(graph.edges[0].model.controlPoints),[{x:430,y:240}],
  'rollback restores the saved endpoint frame instead of leaking the tentative compact frame');
assert.equal(graph.edges[0].model.curvePolicy,'screen-right',
  'a previously rejected legacy migration cannot bypass the gate during the next drag');

// The approximate planner may have no safe proposal. Its model-level totals
// cannot certify a migration that exchanges one real node-label collision for
// another. Keep the actual evaluator and independently drawn paths unchanged.
const swappedIdentity=plain(labelObstacle);
swappedIdentity.nodes.push({id:'R-C',x:280,y:200,label:'Collision C account\nSYN-C',size:32},
  {id:'R-D',x:0,y:500,label:'D\nSYN-D',size:32});
const noProposal=script=>script.replace('\nvar DATA =',
  '\nwindow.FundsGraphRouting=Object.assign({},window.FundsGraphRouting,{plan:function(){return {routes:[],stats:{limited:false}};}});\nvar DATA =');
for(const extraCollision of [false,true]){
  const data=plain(swappedIdentity);
  if(extraCollision)data.nodes.push({id:'R-E',x:280,y:310,label:'Collision E account\nSYN-E',size:32});
  vm.runInNewContext(noProposal(withData(data)),context);
  const assessment=graph._fundsRouteQuality;
  assert.equal(assessment.accepted,false,'new collision identities must roll back even when every total is nonincreasing');
  assert.equal(assessment.reason,'actual-quality-regression');
  assert.equal(assessment.before.counts.edgeNodeLabel,extraCollision?2:1);
  assert.equal(assessment.proposed.counts.edgeNodeLabel,1);
  for(const kind of Object.keys(assessment.before.counts))
    assert.ok(assessment.proposed.counts[kind]<=assessment.before.counts[kind]);
  assert.ok(assessment.proposed.collisionKeys.edgeNodeLabel.some(key=>!assessment.before.collisionKeys.edgeNodeLabel.includes(key)),
    'the old foreign label is replaced by a genuinely new target-label obstruction');
  assert.deepEqual(plain(graph.edges[0].model.controlPoints),[{x:400,y:250}]);
  assert.equal(graph.edges[0].model.curvePolicy,'screen-right');
  graph.move('R-B',20,20);
  assert.deepEqual(plain(graph.edges[0].model.controlPoints),[{x:430,y:240}],
    'identity rejection restores the old endpoint frame as well as its curve');
}

// Self-contained exports always embed the matching evaluator. Missing keys
// on either assessment, including both sides, cannot certify a safe migration.
function qualityVersion(script,missingCalls){
  const shim='\nFundsGraphRouteQuality=(function(api){var calls=0;return Object.assign({},api,{evaluate:function(input){'
    +'var result=api.evaluate(input);calls++;if('+JSON.stringify(missingCalls)+'.indexOf(calls)>=0)delete result.collisionKeys;return result;}});'
    +'})(FundsGraphRouteQuality);\nvar DATA =';
  return script.replace('\nvar DATA =',shim);
}
for(const missingCalls of [[1],[2],[1,2]]){
  vm.runInNewContext(qualityVersion(noProposal(withData(swappedIdentity)),missingCalls),context);
  assert.equal(graph._fundsRouteQuality.accepted,false,
    'missing collision identities always reject, including two unidentified assessments');
  assert.equal(graph.edges[0].model.curvePolicy,'screen-right');
  assert.deepEqual(plain(graph.edges[0].model.controlPoints),[{x:400,y:250}]);
}
