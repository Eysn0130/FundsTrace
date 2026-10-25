import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { harness } from './legacy_navigation_harness.mjs';

const response = value => ({ ok: true, status: 200, headers: { get: () => 'application/json' },
  json: async () => value, text: async () => JSON.stringify(value) });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

// Mirror the production Worker envelope without sending its input back to the
// host. Fault tests can replace result/quality/proposal independently.
function workerReply(env, message, overrides = {}) {
  assert.equal(message.protocol, 1);
  assert.ok(Number.isSafeInteger(message.requestId));
  assert.ok(Number.isSafeInteger(message.taskId));
  assert.ok(['layout', 'routes'].includes(message.task));
  const reply = { protocol: 1, requestId: message.requestId, taskId: message.taskId, task: message.task };
  if (message.task === 'routes') {
    reply.proposal = Object.hasOwn(overrides, 'proposal') ? overrides.proposal : env.FundsGraphRouting.plan(message.input);
  } else {
    if (Object.hasOwn(overrides, 'result')) reply.result = overrides.result;
    else {
      const simulation = env.FundsGraphLayout.simulation(message.input);
      while (simulation.step()) {}
      reply.result = simulation.result();
    }
    if (Object.hasOwn(overrides, 'quality')) reply.quality = overrides.quality;
    else reply.quality = env.FundsGraphLayoutQuality.evaluate(message.input, reply.result,
      { fitViewport: env.FundsGraphLayout.fitViewport });
  }
  assert.equal(Object.hasOwn(reply, 'input'), false);
  return reply;
}

function interactions() {
  const h = harness('app.js'), env = h.run('globalThis'), state = h.run('state');
  const classes = new Set(), selected = new Set();
  h.element('#drawer').classList = {
    add: (...names) => names.forEach(name => classes.add(name)), remove: name => classes.delete(name), contains: name => classes.has(name),
  };
  env.scheduleGraphLabels = () => {};
  state.caseId = 1; state.graphLoading = false;
  state.lastGraph = { nodes: [{ account: 'SYN-A' }, { account: 'SYN-B' }], edges: [] };
  state.graph = { setItemState(item, name, on) {
    if (name === 'selected') on ? selected.add(item.getModel().id) : selected.delete(item.getModel().id);
  } };
  const item = model => ({ getModel: () => model });
  env.nodeA = item({ id: 'SYN-A' }); env.nodeB = item({ id: 'SYN-B' });
  const edge = (id, source, target) => ({ id, source, target, ids: [id], data: {
    source, target, source_name: source, target_name: target, cnt: 1, total: '12.34', currencies: ['CNY'],
  } });
  env.edgeA = item(edge(1, 'SYN-A', 'SYN-B')); env.edgeB = item(edge(2, 'SYN-B', 'SYN-A'));
  const pending = new Map();
  h.network(path => {
    if (path.startsWith('/api/main-accounts?')) return response([]);
    const request = deferred(); pending.set(path, request); return request.promise;
  });
  return { h, env, state, classes, selected,
    body: () => {
      const body = h.element('#drawerBody').innerHTML;
      // A browser serializes child tbody updates through its parent's
      // innerHTML. The legacy fake stores these two markup strings separately.
      return body.replace(/(<tbody\b[^>]*\bid=["']edgeTbody["'][^>]*>)[\s\S]*?(<\/tbody>)/,
        (_match, open, close) => open + h.element('#edgeTbody').innerHTML + close);
    },
    openNode: name => h.run(`showNodeDrawer('SYN-${name}', node${name})`),
    openEdge: name => h.run(`showEdgeDrawer(edge${name}.getModel(), edge${name})`),
    resolveNode(name) { pending.get(`/api/accounts/SYN-${name}/profile?case_id=1`).resolve(response({ name: `PROFILE-${name}`, top_counterparties: [] })); },
    resolveEdge(name) { pending.get(`/api/flows?case_id=1&ids=${name === 'A' ? 1 : 2}&size=500`).resolve(response({ rows: [{ id: 1, amount: '12.34', currency: 'CNY', summary: `FLOW-${name}` }] })); },
  };
}

test('graph interaction: the latest same-graph account owns the drawer after out-of-order profile responses', async () => {
  const f = interactions(), a = f.openNode('A'), b = f.openNode('B');
  f.resolveNode('B'); await b;
  assert.ok(f.body().includes('PROFILE-B'), 'the current profile should be displayed');
  f.resolveNode('A'); await a;
  assert.ok(f.body().includes('PROFILE-B'), 'a late profile must preserve the current account');
  assert.ok(!f.body().includes('PROFILE-A'), 'a late profile must not enter the drawer');
  assert.deepEqual([...f.selected], ['SYN-B']);
});

test('graph interaction: the latest same-graph edge owns the drawer after out-of-order flow responses', async () => {
  const f = interactions(), a = f.openEdge('A'), b = f.openEdge('B');
  f.resolveEdge('B'); await b;
  assert.ok(f.body().includes('FLOW-B'), 'the current edge flows should be displayed');
  f.resolveEdge('A'); await a;
  assert.ok(f.body().includes('FLOW-B'), 'late flows must preserve the current edge');
  assert.ok(!f.body().includes('FLOW-A'), 'late flows must not enter the drawer');
});

for (const kind of ['Node', 'Edge']) {
  test(`graph interaction: closing the ${kind.toLowerCase()} drawer revokes its delayed response`, async () => {
    const f = interactions(), request = f[`open${kind}`]('A');
    f.h.run('closeDrawer()'); const body = f.body();
    f[`resolve${kind}`]('A'); await request;
    assert.equal(f.classes.has('open'), false);
    assert.ok(f.body() === body, 'a closed drawer must not receive delayed detail content');
    assert.equal(f.selected.size, 0);
  });
}

for (const [first, second] of [['Node', 'Edge'], ['Edge', 'Node']]) {
  test(`graph interaction: delayed ${first.toLowerCase()} details cannot overwrite a newer ${second.toLowerCase()} drawer`, async () => {
    const f = interactions(), a = f[`open${first}`]('A'), b = f[`open${second}`]('B');
    f[`resolve${second}`]('B'); await b; const body = f.body();
    f[`resolve${first}`]('A'); await a;
    assert.ok(f.body() === body, 'the latest drawer must retain its detail content');
    assert.equal(f.h.element('#drawerTitle').textContent, second === 'Node' ? '账户资料' : '资金流向');
  });
}

function graphInteractions({ selection = false, observeResize = false, observeFonts = false, geometry = false } = {}) {
  const observers = [],fontListeners=new Map();
  class GraphResizeObserver {
    constructor(callback) { this.callback = callback; this.targets = []; observers.push(this); }
    observe(target) { this.targets.push(target); }
    disconnect() { this.disconnected = true; }
  }
  const h = harness(observeFonts?'graph-labels.js':'app.js', undefined, undefined, observeResize ? { ResizeObserver: GraphResizeObserver } : {});
  const env = h.run('globalThis');
  if(observeFonts){env.document.fonts={status:'loaded',addEventListener(name,callback){
    if(!fontListeners.has(name))fontListeners.set(name,new Set());fontListeners.get(name).add(callback)}};
    // This font fixture loads app.js after its own VM bootstrap. Match the
    // normal app fixture's separate view boundary while retaining the real
    // case-bound main-account controller and its command blockers.
    env.window.FundsMainAccountsView={create:()=>({show(){},onState(){},dismiss(){},attach(){}})};
    h.run(readFileSync(new URL('../static/app.js',import.meta.url),'utf8'));}
  const state = h.run('state');
  const applyLabels = env.applyGraphLabels, fitGraph=env.fitGraph,applyViewport=env.applyGraphViewport,rebuildViewport=env.rebuildGraphViewport;
  if(geometry){h.element('#graphContainer').clientWidth=900;h.element('#graphContainer').clientHeight=600;}
  env.performance = { now: () => 0 };
  const listeners = new Map(), documentListeners = new Map(), graphs = [], destroyedWithListeners = [];
  env.window.addEventListener = (name, callback) => {
    if (!listeners.has(name)) listeners.set(name, new Set());
    listeners.get(name).add(callback);
  };
  env.window.removeEventListener = (name, callback) => listeners.get(name)?.delete(callback);
  env.window.dispatchEvent = event => { for (const callback of [...listeners.get(event.type) || []]) {
    callback(event); if(event.immediatePropagationStopped)break;
  } return !event.defaultPrevented; };
  env.document.addEventListener=(name,callback)=>{
    if(!documentListeners.has(name))documentListeners.set(name,new Set());documentListeners.get(name).add(callback);
  };
  env.document.removeEventListener=(name,callback)=>documentListeners.get(name)?.delete(callback);
  env.CustomEvent = class {
    constructor(type, options = {}) { this.type = type; this.detail = options.detail;this.cancelable=!!options.cancelable;this.defaultPrevented=false; }
    preventDefault(){if(this.cancelable)this.defaultPrevented=true}
    stopImmediatePropagation(){this.immediatePropagationStopped=true}
  };
  if (selection) {
    const enhance = element => {
      const attributes = new Map(), classes = new Set(), children = [];
      Object.assign(element, { children,
        getAttribute: name => attributes.get(name) ?? null,
        setAttribute: (name, value) => attributes.set(name, String(value)), removeAttribute: name => attributes.delete(name),
        appendChild(child) { children.push(child); Object.defineProperty(this, 'firstElementChild', { value: children[0], configurable: true }); },
        contains(target){return target===this||children.includes(target)},
        classList: { add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name) },
      });
      return element;
    };
    const create = env.document.createElement;
    env.document.createElement = kind => enhance(create(kind));
    const container = enhance(h.element('#graphContainer')); container.clientWidth = 900; container.clientHeight = 600;
    env.updateGraphViewStatus = () => {};
    h.run(readFileSync(new URL('../static/graph-selection.js', import.meta.url), 'utf8'));
    env.FundsGraphSelection = env.window.FundsGraphSelection;
  }
  const count = name => listeners.get(name)?.size || 0;
  const makeItem = model => ({ model, states: new Set(), getModel() { return this.model; },
    getContainer: () => ({ get: () => [] }),
    getKeyShape: () => ({ attr: () => [['M', 0, 0], ['L', 200, 200]] }),
  });
  class RenderShape {
    constructor(kind, config, owner) {
      this.kind=kind;this.name=config.name;this.capture=config.capture!==false;this.attrs={...config.attrs};this.owner=owner;this.visible=true;this.clip=null;
    }
    get(name){return this[name]}
    set(name,value){this[name]=value}
    attr(name,value){if(arguments.length===0)return this.attrs;if(typeof name==='object'){Object.assign(this.attrs,name);return}
      if(arguments.length>1){this.attrs[name]=value;return}return this.attrs[name]}
    show(){this.visible=true}hide(){this.visible=false}isVisible(){return this.visible}
    remove(){this.owner.children=this.owner.children.filter(shape=>shape!==this)}toBack(){}
    setClip(config){this.clip=config}getClip(){return this.clip}setMatrix(matrix){this.attrs.matrix=matrix}
    getBBox(){
      if(this.kind==='text'){
        const lines=String(this.attrs.text??'').split('\n'),size=this.attrs.fontSize||12;
        const width=Math.max(...lines.map(line=>line.length))*size*.6,height=size+(lines.length-1)*(this.attrs.lineHeight||size+4);
        return {minX:this.attrs.x-width/2,maxX:this.attrs.x+width/2,minY:this.attrs.y-height/2,maxY:this.attrs.y+height/2};
      }
      if(this.kind==='circle')return {minX:-18,minY:-18,maxX:18,maxY:18};
      const points=(this.attr('path')||[]).flatMap(part=>Array.from({length:Math.floor((part.length-1)/2)},(_,i)=>({x:part[i*2+1],y:part[i*2+2]})));
      return {minX:Math.min(...points.map(p=>p.x)),maxX:Math.max(...points.map(p=>p.x)),minY:Math.min(...points.map(p=>p.y)),maxY:Math.max(...points.map(p=>p.y))};
    }
    getCanvasBBox(){const bounds=this.getBBox(),model=this.owner.item.model,dx=model.source?0:model.x,dy=model.source?0:model.y;
      const a=this.owner.graph.getCanvasByPoint(bounds.minX+dx,bounds.minY+dy);
      const b=this.owner.graph.getCanvasByPoint(bounds.maxX+dx,bounds.maxY+dy);
      return {minX:Math.min(a.x,b.x),maxX:Math.max(a.x,b.x),minY:Math.min(a.y,b.y),maxY:Math.max(a.y,b.y)}}
  }
  const renderItem=(model,graph)=>{
    const item={model,states:new Set(),getModel(){return this.model},getKeyShape(){return key},getContainer(){return group}};
    const group={item,graph,children:[],visible:true,get(name){return this[name]},show(){this.visible=true},hide(){this.visible=false},
      addShape(kind,config){const shape=new RenderShape(kind,config,this);this.children.push(shape);return shape},
      getCanvasBBox(){const boxes=this.children.filter(shape=>shape.visible).map(shape=>shape.getCanvasBBox());return{
        minX:Math.min(...boxes.map(box=>box.minX)),maxX:Math.max(...boxes.map(box=>box.maxX)),
        minY:Math.min(...boxes.map(box=>box.minY)),maxY:Math.max(...boxes.map(box=>box.maxY))}}};
    const key=group.addShape(model.source?'path':'circle',{name:'key-shape',attrs:{...model.style,path:[]}});
    const path=()=>{
      const edge=item.model,a=graph.findById(edge.source)?.model,b=graph.findById(edge.target)?.model;
      if(!a||!b)return [];
      const controls=edge.controlPoints||[];
      return edge.type==='quadratic'&&controls.length===1
        ?[['M',a.x,a.y],['Q',controls[0].x,controls[0].y,b.x,b.y]]
        :[['M',a.x,a.y],...controls.map(p=>['L',p.x,p.y]),['L',b.x,b.y]];
    };
    const attr=key.attr.bind(key);key.attr=(...args)=>args.length===1&&args[0]==='path'?path():attr(...args);
    key.getPoint=r=>{const parts=path();if(parts.length<2)return null;const a=parts[0],b=parts.at(-1),v=1-r;
      return b[0]==='Q'?{x:v*v*a[1]+2*v*r*b[1]+r*r*b[3],y:v*v*a[2]+2*v*r*b[2]+r*r*b[4]}
        :{x:a[1]+(b[1]-a[1])*r,y:a[2]+(b[2]-a[2])*r}};
    item.syncLabel=()=>{
      group.children=group.children.filter(shape=>!['text-shape','text-bg-shape'].includes(shape.name));
      if(item.model.label===undefined)return;
      const center=item.model.source?key.getPoint(.5):{x:0,y:30};if(!center)return;
      group.addShape('text',{name:'text-shape',attrs:{...item.model.labelCfg?.style,...center,text:item.model.label}});
      group.addShape('rect',{name:'text-bg-shape',attrs:{fill:'#111'}});
    };
    return item;
  };
  class Graph {
    constructor(options) { this.options=options;this.events = new Map(); this.nodes = []; this.edges = []; this.updates = []; this.zoom = 1; graphs.push(this); }
    on(name, callback) { if (!this.events.has(name)) this.events.set(name, []); this.events.get(name).push(callback); }
    emit(name, event = {}) { for (const callback of this.events.get(name) || []) callback(event); }
    get(name) { return name === 'canvas' ? { get: () => ({ style: {} }) } : this.options?.[name]??null; }
    getNodes() { return this.nodes; }
    getEdges() { return this.edges; }
    findById(id) { return [...this.nodes, ...this.edges].find(item => item.getModel().id === id); }
    getPointByClient(x, y) { return { x: x / this.zoom, y: y / this.zoom }; }
    getPointByCanvas(x,y){return {x:x/this.zoom,y:y/this.zoom}}
    getCanvasByPoint(x, y) { return { x: x * this.zoom, y: y * this.zoom }; }
    getZoom() { return this.zoom; }
    data(data) { this.changeData(data); }
    changeData(data) {
      const update = (models, old) => models.map(model => {
        const previous = old.find(item => model.id ? item.model.id === model.id
          : item.model.source === model.source && item.model.target === model.target);
        if (previous) { previous.model = { ...model }; return previous; }
        return geometry?renderItem({...model},this):makeItem({ ...model });
      });
      this.nodes = update(data.nodes, this.nodes); this.edges = update(data.edges, this.edges);
      if(geometry)for(const item of [...this.nodes,...this.edges])item.syncLabel();
    }
    updateItem(item, changes) { this.updates.push({ item, changes }); Object.assign(item.model, changes);
      if(geometry&&Object.hasOwn(changes,'label'))item.syncLabel(); }
    setItemState(item, name, on) { on ? item.states.add(name) : item.states.delete(name); }
    clearItemStates(item, names) {
      for (const target of item ? [item] : [...this.nodes, ...this.edges]) {
        if (names) for (const name of Array.isArray(names) ? names : [names]) target.states.delete(name);
        else target.states.clear();
      }
    }
    findAllByState(kind, name) { return (kind === 'node' ? this.nodes : this.edges).filter(item => item.states.has(name)); }
    destroy() { destroyedWithListeners.push({ move: count('mousemove'), up: count('mouseup') }); this.destroyed = true; }
    clear() { this.nodes=[];this.edges=[]; }
    render() {} paint() {} setAutoPaint() {} stopLayout() {} changeSize() {}
  }
  env.G6 = { Graph };
  for (const name of ['scheduleGraphLabels', 'scheduleGraphViewport', 'rebuildGraphViewport',
    'restoreGraphClipping', ...(geometry?[]:['applyGraphLabels']), 'fitGraph']) env[name] = () => {};
  state.caseId = 1; state.graphLoading = false; state.forceFit = false; state.edgeType = 'polyline';
  h.run('initGraph()');
  const seed = () => {
    const graph = state.graph;
    graph.data({ nodes: [{ id: 'SYN-A', x: 0, y: 0 }, { id: 'SYN-B', x: 200, y: 200 }],
      edges: [{ id: 'SYN-E', source: 'SYN-A', target: 'SYN-B', type: 'polyline' }] });
    return graph;
  };
  return { h, env, state, graphs, destroyedWithListeners, observers, applyLabels, fitGraph, applyViewport, rebuildViewport, count, seed,
    fontEvent(name){for(const callback of [...fontListeners.get(name)||[]])callback()},
    event(name, event = {}) { for (const callback of [...listeners.get(name) || []]) callback(event); },
    documentEvent(name,event={}){for(const callback of [...documentListeners.get(name)||[]])callback(event)},
    edgeEvent(graph, name, x, y) { graph.emit(name, { item: graph.getEdges()[0], clientX: x, clientY: y, preventDefault() {} }); },
  };
}

test('graph interaction: repeated graph rebuilds release old global drag listeners before destruction', () => {
  const f = graphInteractions();
  assert.equal(f.count('mousemove'), 2); assert.equal(f.count('mouseup'), 2);
  for (let i = 0; i < 4; i++) {
    f.h.run('initGraph()');
    assert.equal(f.count('mousemove'), 2, `mousemove listeners after rebuild ${i + 1}`);
    assert.equal(f.count('mouseup'), 2, `mouseup listeners after rebuild ${i + 1}`);
  }
  assert.equal(f.destroyedWithListeners.length, 4);
  assert.ok(f.destroyedWithListeners.every(item => item.move === 0 && item.up === 0));
});

test('graph interaction: rebuilding during a bend drag prevents the old graph from changing new geometry', () => {
  const f = graphInteractions(), old = f.seed();
  f.state.edgeBendPos = { 'SYN-A=>SYN-B': [{ x: 100, y: 100 }] };
  f.edgeEvent(old, 'edge:mousedown', 100, 100);
  f.h.run('initGraph()'); f.seed();
  f.state.edgeBendPos = { 'SYN-A=>SYN-B': [{ x: 40, y: 50 }] };
  f.event('mousemove', { clientX: 150, clientY: 160 }); f.event('mouseup');
  assert.deepEqual(JSON.parse(JSON.stringify(f.state.edgeBendPos)), { 'SYN-A=>SYN-B': [{ x: 40, y: 50 }] });
});

for (const [binding, invalidate] of [['case generation', 'state.caseGeneration += 1'],
  ['canvas generation', 'state.canvasGeneration += 1'], ['view revision', 'state.graphView.revision += 1']]) {
  for (const kind of ['bend', 'label']) {
    test(`graph interaction: a ${binding} change revokes an active ${kind} drag on the reused graph`, () => {
      const f = graphInteractions(), graph = f.seed();
      f.state.edgeBendPos = { 'SYN-A=>SYN-B': [{ x: 100, y: 100 }] };
      const event = { item: graph.getEdges()[0], clientX: 100, clientY: 100, preventDefault() {} };
      if (kind === 'label') event.target = { get: name => name === 'name' ? 'text-shape' : undefined };
      graph.emit('edge:mousedown', event);
      f.h.run(`${invalidate}; state.edgeBendPos = {}; state.edgeLabelPos = {};`);
      f.event('mousemove', { clientX: 150, clientY: 160 }); f.event('mouseup');
      assert.deepEqual(Object.keys(f.state.edgeBendPos), [], 'old bend geometry cannot enter the new binding');
      assert.deepEqual(Object.keys(f.state.edgeLabelPos), [], 'old label geometry cannot enter the new binding');
      assert.equal(graph.updates.length, 0, 'the revoked gesture must not update old G6 items');
    });
  }
}

test('graph interaction: moving selected nodes translates internal manual bends and retains boundary bends', () => {
  const f = graphInteractions({ selection: true }), graph = f.state.graph;
  graph.data({ nodes: [{ id: 'SYN-A', x: 0, y: 0 }, { id: 'SYN-B', x: 200, y: 200 }, { id: 'SYN-C', x: 300, y: 200 }],
    edges: [{ id: 'SYN-AB', source: 'SYN-A', target: 'SYN-B' }, { id: 'SYN-BC', source: 'SYN-B', target: 'SYN-C' }] });
  for (const node of graph.getNodes()) node.getEdges = () => graph.getEdges().filter(edge =>
    edge.model.source === node.model.id || edge.model.target === node.model.id);
  f.state.graphView.nodeItems = new Map(graph.getNodes().map(node => [node.model.id, node]));
  f.state.graphSelection.rebind(['SYN-A', 'SYN-B']);
  f.state.edgeBendPos = { 'SYN-A=>SYN-B': [{ x: 40, y: 60 }, { x: 80, y: 90 }], 'SYN-B=>SYN-C': [{ x: 250, y: 180 }] };
  graph.emit('node:mousedown', { item: graph.findById('SYN-A'), clientX: 0, clientY: 0, button: 0, preventDefault() {} });
  f.event('mousemove', { clientX: 20, clientY: 30, buttons: 1, preventDefault() {} });
  f.event('mouseup', { clientX: 20, clientY: 30, preventDefault() {} });
  assert.deepEqual(JSON.parse(JSON.stringify(f.state.edgeBendPos)), {
    'SYN-A=>SYN-B': [{ x: 60, y: 90 }, { x: 100, y: 120 }], 'SYN-B=>SYN-C': [{ x: 250, y: 180 }],
  });
  assert.equal(graph.findById('SYN-A').model.x, 20);
  assert.equal(graph.findById('SYN-B').model.x, 220);
  assert.equal(graph.findById('SYN-C').model.x, 300);
});

for (const zoom of [0.25, 1, 4]) {
  test(`graph interaction: bend drag uses a 10 screen-pixel hit radius at zoom ${zoom}`, () => {
    const f = graphInteractions(), graph = f.seed(); graph.zoom = zoom;
    for (const [screenOffset, expectedPoints] of [[8, 1], [12, 2]]) {
      f.state.edgeBendPos = { 'SYN-A=>SYN-B': [{ x: 100, y: 100 }] };
      f.edgeEvent(graph, 'edge:mousedown', 100 * zoom + screenOffset, 100 * zoom);
      f.event('mousemove', { clientX: 100 * zoom + screenOffset + 1, clientY: 100 * zoom }); f.event('mouseup');
      assert.equal(f.state.edgeBendPos['SYN-A=>SYN-B'].length, expectedPoints,
        `${screenOffset}px away should ${expectedPoints === 1 ? 'move the existing point' : 'insert one point'}`);
    }
  });

  test(`graph interaction: bend removal uses a 20 screen-pixel hit radius at zoom ${zoom}`, () => {
    const f = graphInteractions(), graph = f.seed(); graph.zoom = zoom;
    for (const [screenOffset, removed] of [[18, true], [22, false]]) {
      f.state.edgeBendPos = { 'SYN-A=>SYN-B': [{ x: 100, y: 100 }] };
      f.edgeEvent(graph, 'edge:dblclick', 100 * zoom + screenOffset, 100 * zoom);
      assert.equal(!f.state.edgeBendPos['SYN-A=>SYN-B'], removed, `${screenOffset}px from the bend`);
    }
  });
}

test('graph interaction: new-tracking feedback uses recent state without altering selected items', () => {
  const f = graphInteractions({ selection: true }), graph = f.state.graph;
  f.state.lastGraph = { nodes: ['SYN-A', 'SYN-B', 'SYN-C'].map(account => ({ account, in_cnt: 1, out_cnt: 1 })),
    edges: [{ source: 'SYN-A', target: 'SYN-B', ids: [1], cnt: 1, total: '12.34', currencies: ['CNY'] }] };
  f.state.positions = { 'SYN-A': { x: 0, y: 0 }, 'SYN-B': { x: 200, y: 200 }, 'SYN-C': { x: 300, y: 200 } };
  f.h.run('renderGraph()');
  f.env.currentNode = graph.findById('SYN-C'); f.h.run('selectItem(currentNode)');
  const selectedBefore = graph.findAllByState('node', 'selected').map(item => item.model.id);
  f.state.justAdded = new Set([1]); f.h.run('renderGraph()');
  const runFeedback = () => {
    for (const timer of f.h.timers.filter(timer => !timer.cancelled && !timer.ran && timer.delay <= 100)) {
      timer.ran = true; timer.callback();
    }
  };
  runFeedback();
  assert.deepEqual(graph.findAllByState('node', 'selected').map(item => item.model.id), selectedBefore);
  assert.deepEqual(graph.findAllByState('node', 'recent').map(item => item.model.id).sort(), ['SYN-A', 'SYN-B']);
  assert.deepEqual([...f.state.graphSelection.ids], ['SYN-C']);
  assert.equal(f.state.graphView.selectedAccount, 'SYN-C', 'rendering feedback should retain the selected label focus');
  f.state.lastGraph.edges.push({ source: 'SYN-B', target: 'SYN-C', ids: [2], cnt: 1, total: '56.78', currencies: ['CNY'] });
  f.state.justAdded = new Set([2]); f.h.run('renderGraph()'); runFeedback();
  assert.deepEqual(graph.findAllByState('node', 'selected').map(item => item.model.id), selectedBefore);
  assert.deepEqual(graph.findAllByState('node', 'recent').map(item => item.model.id).sort(), ['SYN-B', 'SYN-C']);
});

function stableExpansionScene() {
  const f=graphInteractions({selection:true,geometry:true}),graph=f.state.graph;
  // Camera animation timing has its own fake-RAF suite. Integration scenes
  // settle deliberate focus immediately and keep the real geometry adapter.
  f.env.window.FundsGraphLayout=f.env.FundsGraphLayout;
  f.env.window.matchMedia=()=>({matches:true});
  let camera=[.85,0,0,0,.85,0,93,-47,1],fits=0;
  const get=graph.get.bind(graph),group={getMatrix:()=>camera,setMatrix:matrix=>{camera=[...matrix]}};
  graph.get=name=>name==='group'?group:get(name);
  graph.getZoom=()=>camera[0];
  graph.translate=(x,y)=>{camera[6]+=x;camera[7]+=y};
  graph.getCanvasByPoint=(x,y)=>({x:x*camera[0]+camera[6],y:y*camera[4]+camera[7]});
  graph.getPointByCanvas=(x,y)=>({x:(x-camera[6])/camera[0],y:(y-camera[7])/camera[4]});
  f.env.fitGraph=()=>{fits++;camera=[.2,0,0,0,.2,0,0,0,1]};
  f.env.updateHeader=()=>{};
  const node=account=>({account,name:`合成测试 ${account}`,in_cnt:1,out_cnt:1});
  const edge=(source,target,id,amount)=>({source,target,ids:[id],cnt:1,total:amount,currencies:['CNY'],
    amount_totals:[{currency:'CNY',amount}]});
  f.state.lastGraph={nodes:['SYN-A','SYN-B','SYN-C','SYN-D'].map(node),tracked_ids:[1,2],
    edges:[edge('SYN-A','SYN-B',1,'9999999999999999.99'),edge('SYN-C','SYN-D',2,'0.01')]};
  f.state.positions={'SYN-A':{x:100,y:100},'SYN-B':{x:500,y:500},'SYN-C':{x:100,y:500},'SYN-D':{x:500,y:100}};
  f.state.tracked=new Set([1,2]);f.state.graphView.labelMode='all';f.state.edgeType='quadratic';
  f.state.edgeBendPos={'SYN-A=>SYN-B':[{x:190,y:290}]};
  f.state.edgeLabelPos={'SYN-A=>SYN-B':.37};
  f.h.run('renderGraph();savePositions()');
  f.state.graphSelection.rebind(['SYN-A']);
  const settle=()=>{
    for(let pass=0;pass<5;pass++)for(const timer of f.h.timers.filter(timer=>!timer.ran&&!timer.cancelled&&timer.delay<=100)){
      timer.ran=true;timer.callback();
    }
  };
  settle();
  return {...f,node,edge,settle,get camera(){return [...camera]},get fits(){return fits}};
}

function graphLocatorScene() {
  const f=stableExpansionScene(),graph=f.state.graph;
  const ids=['0000000000000000123','0000000000000000124','SYN-SECOND-NAME'];
  f.h.element('#graphContainer').getBoundingClientRect=()=>({left:390,top:180,right:1290,bottom:780,width:900,height:600});
  f.state.lastGraph={nodes:ids.map((account,i)=>({...f.node(account),name:i===2?'另一户名':'同名测试'})),
    edges:[f.edge(ids[0],ids[1],'9007199254740993','9999999999999999.99'),f.edge(ids[1],ids[2],'9007199254740994','0.01')],
    tracked_ids:['9007199254740993','9007199254740994']};
  f.state.positions=Object.fromEntries(ids.map((id,i)=>[id,{x:1800+i*260,y:1200+i*80}]));
  f.state.tracked=new Set(f.state.lastGraph.tracked_ids);f.state.graphView.fullIndex=null;
  f.state.graphView.visibleIds=null;f.h.run('renderGraph()');f.settle();f.state.graphSelection.rebind([ids[2]]);
  let historyCommands=0;
  f.env.window.FundsCanvasEditor={history:{status:()=>({busy:false,count:7})},command(){historyCommands++;throw new Error('Preview must not create history');}};
  const snapshot=()=>JSON.stringify({facts:f.state.lastGraph,tracked:[...f.state.tracked],positions:f.state.positions,
    bends:f.state.edgeBendPos,ratios:f.state.edgeLabelPos,presentation:f.state.presentation,selected:[...f.state.graphSelection.ids]});
  return {...f,ids,graph,locator:f.env.window.FundsGraphLocator,snapshot,get historyCommands(){return historyCommands;},
    get fits(){return f.fits;},get camera(){const matrix=graph.get('group').getMatrix();return [...matrix]}};
}

function nodeFinderAdapterScene() {
  const f=graphLocatorScene();let ports;
  f.env.window.FundsNodeFinder={create(options){ports=options;return {update(){},getState(){return {phase:'idle'}}}}};
  f.h.run('installNodeFinder()');assert.ok(ports,'the real app installs the node finder adapter');
  return {...f,ports,get camera(){return f.camera},get historyCommands(){return f.historyCommands}};
}

test('node finder adapter: search scope includes offscreen members and preserves full account strings while excluding filtered accounts',()=>{
  const f=nodeFinderAdapterScene(),before=f.snapshot();
  f.state.graphView.clipResult={nodeIds:new Set([f.ids[2]]),edgeKeys:new Set()};
  f.graph.findById(f.ids[0]).getContainer().hide();
  const snapshot=f.ports.read();
  assert.equal(snapshot.ready,true);
  assert.deepEqual(JSON.parse(JSON.stringify(snapshot.accounts)),f.ids.map((account,i)=>({account,name:i===2?'另一户名':'同名测试'})));
  assert.equal(f.snapshot(),before,'reading the locator scope does not change business or selection state');
  f.state.presentation.hiddenAccounts=[f.ids[0]];f.h.run('renderGraph()');f.settle();
  const filtered=f.ports.read();
  assert.ok(filtered.revision>snapshot.revision);assert.deepEqual(Array.from(filtered.accounts,entry=>entry.account),f.ids.slice(1));
  assert.equal(f.state.lastGraph.nodes.length,3,'filtering retains the complete tracked facts');
});

test('node finder adapter: optional identity suggestions preserve complete strings and omit unavailable fields without changing the graph',()=>{
  const f=nodeFinderAdapterScene(),identity='00000020000101000X';
  f.state.lastGraph.nodes[0].id_no=identity;f.h.run('renderGraph()');f.settle();
  const before=f.snapshot(),camera=f.camera,accounts=f.ports.read().accounts;
  assert.deepEqual(JSON.parse(JSON.stringify(accounts)),f.ids.map((account,i)=>({account,name:i===2?'另一户名':'同名测试',...(i===0?{idNo:identity}:{})})));
  assert.equal(f.snapshot(),before,'reading identity candidates cannot mutate facts, selection or geometry');
  assert.deepEqual(f.camera,camera,'reading identity candidates cannot move the camera');
});

test('node finder adapter: locating keeps an unsaved editor intact and shows the complete node away from an open drawer',async()=>{
  const f=nodeFinderAdapterScene(),drawer=f.h.element('#drawer');
  f.h.run(`nodeDrafts.set('SYN-EDIT',{idBase:'原资料',idValue:'尚未保存的资料',focusBase:1,focusValue:1.5});`);
  drawer.hidden=false;drawer.inert=false;drawer.id='drawer';drawer.classList={contains:name=>name==='open'};
  drawer.getClientRects=()=>[drawer.getBoundingClientRect()];
  drawer.getBoundingClientRect=()=>({left:990,top:180,right:1290,bottom:780,width:300,height:600});
  const before=f.snapshot(),draft=f.h.run('JSON.stringify([...nodeDrafts])'),focus=f.state.graphCamera.focus,calls=[];
  f.state.graphCamera.focus=(point,options)=>{calls.push({point,options});return focus(point,options)};
  const result=await f.ports.locate([f.ids[0]],f.ports.read());
  assert.equal(result.ok,true);assert.equal(calls.length,1);assert.equal(calls[0].options.duration,320);
  const node=f.graph.findById(f.ids[0]),model=node.getModel(),point=f.graph.getCanvasByPoint(model.x,model.y);
  assert.ok(Math.abs(point.x-300)<1e-8&&Math.abs(point.y-300)<1e-8,`the located node reaches the unobstructed center: ${JSON.stringify(point)}`);
  assert.equal(f.camera[0],1.25,'a distant node becomes readable when the previous overview was zoomed out');
  const bounds=node.getContainer().getCanvasBBox();
  assert.ok(bounds.minX>=24&&bounds.maxX<=576&&bounds.minY>=24&&bounds.maxY<=576,'body and complete account label avoid the editor');
  assert.deepEqual([...f.state.graphView.previewAccounts],[f.ids[0]]);
  assert.equal(f.snapshot(),before);assert.equal(f.h.run('JSON.stringify([...nodeDrafts])'),draft);
  assert.equal(drawer.classList.contains('open'),true,'preview does not close the editor');
  assert.equal(f.historyCommands,0);assert.equal(f.h.requests.length,0);
});

test('node finder adapter: a tiny overview label envelope does not prevent a readable single-node arrival in a small window',async()=>{
  const f=nodeFinderAdapterScene(),container=f.h.element('#graphContainer'),node=f.graph.findById(f.ids[0]),model=node.getModel();
  container.clientWidth=640;container.clientHeight=420;
  container.getBoundingClientRect=()=>({left:390,top:180,right:1030,bottom:600,width:640,height:420});
  const menu=f.h.element('#findNodePopover');menu.hidden=false;menu.inert=false;
  menu.getBoundingClientRect=()=>({left:390,top:180,right:790,bottom:340,width:400,height:160});
  menu.getClientRects=()=>[menu.getBoundingClientRect()];
  const querySelectorAll=f.env.document.querySelectorAll;
  f.env.document.querySelectorAll=selector=>selector==='.toolbar-menu,.ribbon-popover,.modal,dialog'?[menu]:querySelectorAll(selector);
  const group=node.getContainer(),naturalBounds=group.getCanvasBBox.bind(group);
  // An overview may retain a collision offset or a fixed-screen label gap.
  // Model that transient rendered union, keeping the actual body/text intact.
  const displacedBounds=()=>{const bounds=naturalBounds(),far=f.graph.getCanvasByPoint(model.x+2400,model.y+1800);
    return {...bounds,maxX:Math.max(bounds.maxX,far.x),maxY:Math.max(bounds.maxY,far.y)}};
  group.getCanvasBBox=displacedBounds;
  f.graph.get('group').setMatrix([.12,0,0,0,.12,0,93,-47,1]);
  const before=f.snapshot(),pointBefore={x:model.x,y:model.y};
  const control=await f.locator.previewAccount(f.ids[0],{binding:f.locator.capture()});
  assert.equal(control.ok,true);assert.ok(f.camera[0]<.2,'the deliberately displaced rendered envelope reproduces the overview framing problem');
  f.graph.get('group').setMatrix([.12,0,0,0,.12,0,93,-47,1]);
  const result=await f.ports.locate([f.ids[0]],f.ports.read());
  assert.equal(result.ok,true);assert.ok(Math.abs(f.camera[0]-1.25)<1e-10,'the single-node finder sizes complete natural text at its intended reading scale');
  assert.equal(group.getCanvasBBox,displacedBounds,'framing never rewrites the live renderer to hide the displaced envelope');
  // Complete the ordinary label refresh scheduled by the camera's final frame.
  f.h.run('applyGraphLabels(false)');
  const bounds=naturalBounds();
  assert.ok(bounds.minX>=24&&bounds.maxX<=616&&bounds.minY>=24&&bounds.maxY<=396,'the full natural node and label remain in the small viewport');
  assert.ok(bounds.minX>=400||bounds.minY>=160,'the readable target does not fall behind the open top menu');
  const text=group.children.find(shape=>shape.name==='text-shape');
  assert.equal(text.attr('text'),model.fundsLabel);assert.ok(text.attr('text').includes(f.ids[0]),'leading zeroes and the complete account survive the enlargement');
  assert.deepEqual({x:model.x,y:model.y},pointBefore);assert.equal(f.snapshot(),before);
  assert.equal(f.historyCommands,0);assert.equal(f.h.requests.length,0);
});

test('node finder adapter: only explicit selection may replace selection and it refuses unsaved account edits',()=>{
  const f=nodeFinderAdapterScene(),snapshot=f.ports.read(),before=f.snapshot(),camera=f.camera;
  f.h.run(`nodeDrafts.set('SYN-EDIT',{idBase:'原资料',idValue:'尚未保存的资料',focusBase:1,focusValue:1});`);
  const rejected=f.ports.select(f.ids.slice(0,2),snapshot);
  assert.equal(rejected.ok,false);assert.match(rejected.reason,/未保存/);assert.equal(f.snapshot(),before);
  f.h.run('nodeDrafts.clear()');
  assert.equal(f.ports.select(f.ids.slice(0,2),snapshot).ok,true);
  assert.deepEqual([...f.state.graphSelection.ids],f.ids.slice(0,2));
  assert.deepEqual(f.camera,camera,'selecting results does not start a second camera animation');
  assert.equal(f.historyCommands,0);assert.equal(f.h.requests.length,0);
});

for(const [name,invalidate]of[
  ['case round trip',f=>{f.state.caseId=2;f.state.caseGeneration++;f.state.caseId=1;f.state.caseGeneration++}],
  ['canvas round trip',f=>{f.state.canvasId=9;f.state.canvasGeneration++;f.state.canvasId=0;f.state.canvasGeneration++}],
  ['view revision',f=>{f.state.graphView.revision++}],
  ['graph replacement',f=>{f.state.graph=Object.create(f.graph)}],
  ['loading view',f=>{f.state.graphLoading=true}],
])test(`node finder adapter: a ${name} rejects an earlier result snapshot for both locating and selection`,async()=>{
  const f=nodeFinderAdapterScene(),snapshot=f.ports.read();invalidate(f);
  const before=f.snapshot(),camera=f.camera;
  for(const action of ['locate','select']){
    const result=await f.ports[action]([f.ids[0]],snapshot);
    assert.equal(result.ok,false);assert.match(result.reason,/画布已更新/);
  }
  assert.deepEqual(f.camera,camera);assert.equal(f.snapshot(),before);assert.equal(f.historyCommands,0);assert.equal(f.h.requests.length,0);
});

test('node finder adapter: failed focus clears temporary preview priority and never changes existing selection',async()=>{
  const f=nodeFinderAdapterScene(),before=f.snapshot(),camera=f.camera;
  f.state.graphCamera.focus=async()=>({ok:false,reason:'unavailable-space'});
  const result=await f.ports.locate([f.ids[0]],f.ports.read());
  assert.equal(result.ok,false);assert.match(result.reason,/遮挡/);
  assert.equal(f.state.graphView.previewAccounts.size,0,'a rejected camera transaction does not leave an unrelated account permanently prioritized');
  assert.deepEqual(f.camera,camera);assert.equal(f.snapshot(),before);assert.equal(f.historyCommands,0);
});

test('node finder adapter: empty and out-of-scope targets cannot alter the current camera or selection',async()=>{
  const f=nodeFinderAdapterScene();
  f.state.presentation.hiddenAccounts=[f.ids[0]];f.h.run('renderGraph()');f.settle();
  const snapshot=f.ports.read(),before=f.snapshot(),camera=f.camera;
  for(const ids of [[],[f.ids[0]],['SYN-NOT-TRACKED'],[f.ids[1],'SYN-NOT-TRACKED']]){
    for(const action of ['locate','select']){
      const result=await f.ports[action](ids,snapshot);
      assert.equal(result.ok,false);assert.match(result.reason,/当前显示范围/);
    }
  }
  assert.deepEqual(f.camera,camera);assert.equal(f.snapshot(),before);assert.equal(f.h.requests.length,0);
});

test('graph locator: exact account preserves leading zeroes and focuses without changing selection, source or history',async()=>{
  const f=graphLocatorScene(),before=f.snapshot(),camera=f.camera;
  const result=await f.locator.previewAccount(f.ids[0],{binding:f.locator.capture()});
  assert.equal(result.ok,true);assert.notDeepEqual(f.camera,camera,'an offscreen exact account is deliberately brought into view');
  assert.deepEqual([...f.state.graphView.previewAccounts],[f.ids[0]],'search label priority stays separate from the existing selection');
  assert.ok(f.state.graphView.labeledNodes.has(f.ids[0]),'the located account remains identifiable after density and collision filtering');
  const node=f.graph.findById(f.ids[0]).getModel(),point=f.graph.getCanvasByPoint(node.x,node.y);
  assert.ok(Math.abs(point.x-450)<1e-8&&Math.abs(point.y-300)<1e-8,'the node itself reaches the available center');
  assert.equal(f.camera[0],camera[0],'a fitting target retains the reading scale');
  assert.equal(f.snapshot(),before);assert.equal(f.historyCommands,0);assert.equal(f.h.requests.length,0);
  assert.equal(f.graph.findById('123'),undefined,'account identifiers never pass through numeric coercion');
});

test('graph locator: an open right drawer is measured in page coordinates and excluded from the camera center',async()=>{
  const f=graphLocatorScene(),drawer=f.h.element('#drawer'),before=f.snapshot();
  drawer.hidden=false;drawer.inert=false;drawer.style.display='';
  drawer.id='drawer';drawer.classList={contains:name=>name==='open'};drawer.getClientRects=()=>[drawer.getBoundingClientRect()];
  drawer.getBoundingClientRect=()=>({left:990,top:180,right:1290,bottom:780,width:300,height:600});
  const obstacles=f.h.run('graphFocusObstacles()');assert.equal(obstacles.length,1);
  assert.deepEqual(JSON.parse(JSON.stringify(obstacles[0].bounds)),{minX:600,minY:0,maxX:900,maxY:600});
  assert.equal((await f.locator.previewAccount(f.ids[0],{binding:f.locator.capture()})).ok,true);
  const node=f.graph.findById(f.ids[0]).getModel(),point=f.graph.getCanvasByPoint(node.x,node.y);
  assert.ok(Math.abs(point.x-300)<1e-8&&Math.abs(point.y-300)<1e-8,'a sidebar page offset is not subtracted twice and the right drawer does not cover the target');
  assert.equal(f.snapshot(),before);
});

test('graph locator: same-name results are located together and independent query fields combine with OR',async()=>{
  const f=graphLocatorScene(),calls=[],before=f.snapshot();
  const focus=f.env.focusGraphItems;
  f.env.focusGraphItems=(items,options)=>{calls.push(items.map(item=>item.getModel().id));return focus(items,options)};
  const byName=await f.locator.previewFields({name:'同名测试'},{binding:f.locator.capture()});
  assert.equal(byName.ok,true);assert.deepEqual([...calls.at(-1)].sort(),f.ids.slice(0,2).sort(),'a shared name is not arbitrarily reduced to its first account');
  const independent=await f.locator.previewFields({account:f.ids[0],name:'另一户名'},{binding:f.locator.capture()});
  assert.equal(independent.ok,true);assert.deepEqual([...calls.at(-1)].sort(),[f.ids[0],f.ids[2]].sort(),'each entered field retains its independent query meaning');
  const counterparty=await f.locator.previewFields({counterparty:f.ids[1]},{binding:f.locator.capture()});
  assert.equal(counterparty.ok,true);assert.deepEqual([...calls.at(-1)],[f.ids[1]]);
  assert.equal(f.snapshot(),before);assert.equal(f.historyCommands,0);assert.equal(f.h.requests.length,0);
});

test('graph locator: hidden and untracked targets remain explicit and do not silently change display scope',async()=>{
  const f=graphLocatorScene();
  f.state.presentation.hiddenAccounts=[f.ids[0]];f.h.run('renderGraph()');f.settle();
  const before=f.snapshot(),camera=f.camera;
  const hidden=await f.locator.previewAccount(f.ids[0],{binding:f.locator.capture()});
  assert.equal(hidden.ok,false);assert.match(hidden.reason,/当前显示范围/);
  assert.equal(f.locator.availability(f.ids[0]).tracked,true);assert.equal(f.locator.availability(f.ids[0]).visible,false);
  const missing=await f.locator.previewAccount('000-NOT-TRACKED',{binding:f.locator.capture()});
  assert.equal(missing.ok,false);assert.match(missing.reason,/尚未上图/);assert.equal(f.locator.availability('000-NOT-TRACKED').tracked,false);
  assert.deepEqual(f.camera,camera);assert.equal(f.snapshot(),before);assert.equal(f.h.requests.length,0);
});

for(const [name,invalidate]of[
  ['case generation',f=>{f.state.caseGeneration++}],
  ['canvas generation',f=>{f.state.canvasGeneration++}],
  ['view revision',f=>{f.state.graphView.revision++}],
  ['camera intent',f=>{f.state.graphCamera.cancelFocus('synthetic-user-input')}],
  ['camera matrix',f=>{f.graph.translate(73,-16)}],
])test(`graph locator: ${name} invalidates a captured query before it can move the current camera`,async()=>{
  const f=graphLocatorScene(),binding=f.locator.capture();invalidate(f);
  const before=f.snapshot(),camera=f.camera;
  const result=await f.locator.previewAccount(f.ids[0],{binding});
  assert.equal(result.ok,false);assert.match(result.reason,/定位已取消/);assert.deepEqual(f.camera,camera);
  assert.equal(f.snapshot(),before);assert.equal(f.historyCommands,0);
});

test('graph locator: explicit search intent revokes a queued zero-delay Fit before the first camera frame',async()=>{
  const control=graphLocatorScene(),controlFits=control.fits;
  control.state.forceFit=true;control.h.run('renderGraph()');control.settle();
  assert.equal(control.fits,controlFits+1,'without a new request the ordinary queued Fit remains functional');
  const f=graphLocatorScene(),fits=f.fits;
  f.state.forceFit=true;f.h.run('renderGraph()');
  assert.ok(f.h.timers.some(timer=>timer.delay===0&&!timer.ran&&!timer.cancelled),'the opening view really has a pending Fit');
  const before=f.snapshot(),camera=f.camera,binding=f.locator.capture();
  assert.deepEqual(f.camera,camera,'capturing an explicit search claims intent without first moving a node or camera');
  f.settle();assert.equal(f.fits,fits,'the old opening callback cannot replace a newer search intent');
  assert.deepEqual(f.camera,camera);
  assert.equal((await f.locator.previewAccount(f.ids[0],{binding})).ok,true);
  assert.equal(f.snapshot(),before);assert.equal(f.fits,fits);
});

test('graph locator: more than 100 matching accounts asks for refinement without fitting an unreadable overview',async()=>{
  const f=graphLocatorScene(),ids=Array.from({length:101},(_,i)=>`SYN-MANY-${String(i).padStart(3,'0')}`);
  f.state.lastGraph={nodes:ids.map(account=>({...f.node(account),name:'同名群体'})),edges:[],tracked_ids:[]};
  f.state.positions=Object.fromEntries(ids.map((id,i)=>[id,{x:100+i%12*140,y:100+Math.floor(i/12)*120}]));
  f.state.graphView.fullIndex=null;f.h.run('renderGraph()');f.settle();f.state.graphSelection.rebind([ids[0]]);
  const before=f.snapshot(),camera=f.camera,result=await f.locator.previewFields({name:'同名群体'},{binding:f.locator.capture()});
  assert.equal(result.ok,false);assert.match(result.reason,/101.*请补全/);assert.deepEqual(f.camera,camera);assert.equal(f.snapshot(),before);
});

test('neighborhood direction: continuing uses the applied direction and a changed application starts its own visible set',async()=>{
  const f=stableExpansionScene();f.h.bindApp();
  f.state.lastGraph={nodes:['SYN-A','SYN-B','SYN-C'].map(f.node),edges:[
    f.edge('SYN-B','SYN-A',1,'12.34'),f.edge('SYN-A','SYN-C',2,'56.78')],tracked_ids:[1,2]};
  f.state.positions={'SYN-A':{x:100,y:100},'SYN-B':{x:500,y:100},'SYN-C':{x:100,y:500}};
  f.state.graphView.fullIndex=null;f.h.run('renderGraph()');f.settle();
  f.state.presentation.relationFilter={currency:'CNY',min:'1.00',max:'100.00',active:true};
  const before=JSON.stringify(f.state.lastGraph),filter=JSON.stringify(f.state.presentation.relationFilter);
  await f.h.run("exploreNeighborhood('SYN-A',true,{direction:'in'})");f.settle();
  assert.deepEqual([...f.state.graphView.visibleIds].sort(),['SYN-A','SYN-B']);assert.equal(f.state.graphView.direction,'in');
  const camera=f.camera;
  f.h.element('#neighborDirection').value='out';
  await f.h.run("exploreNeighborhood('SYN-A')");f.settle();
  assert.equal(f.state.graphView.direction,'in','an unapplied control value must not change continuing exploration');
  assert.deepEqual(f.camera,camera,'continuation keeps the live camera');
  await f.h.run("exploreNeighborhood('SYN-A',true,{direction:'out'})");f.settle();
  assert.deepEqual([...f.state.graphView.visibleIds].sort(),['SYN-A','SYN-C'],'an earlier direction cannot leak old neighbors into the new application');
  assert.equal(JSON.stringify(f.state.presentation.relationFilter),filter);assert.equal(JSON.stringify(f.state.lastGraph),before);
});

test('stable expansion: tracking new accounts preserves old geometry, selection and camera without entering the crossing core',async()=>{
  const f=stableExpansionScene(),copy=value=>JSON.parse(JSON.stringify(value));
  const before={positions:copy(f.state.positions),camera:f.camera,bends:copy(f.state.edgeBendPos),
    ratios:copy(f.state.edgeLabelPos),facts:copy(f.state.lastGraph),size:[f.h.element('#graphContainer').clientWidth,f.h.element('#graphContainer').clientHeight]};
  const expanded={...copy(f.state.lastGraph),nodes:[...copy(f.state.lastGraph.nodes),f.node('SYN-NEW-1'),f.node('SYN-NEW-2')],
    edges:[...copy(f.state.lastGraph.edges),f.edge('SYN-A','SYN-NEW-1',3,'1234567890123456.78'),f.edge('SYN-NEW-1','SYN-NEW-2',4,'0.02')],tracked_ids:[1,2,3,4]};
  f.h.network(path=>response(path.startsWith('/api/graph?')?expanded:{}));
  await f.h.run('trackFlows([3,4])');f.settle();
  for(const [id,position]of Object.entries(before.positions))assert.deepEqual(copy(f.state.positions[id]),position,`${id} remains at its existing world coordinates`);
  assert.deepEqual(f.camera,before.camera,'append retains the complete zoom and pan matrix');assert.equal(f.fits,0);
  assert.deepEqual(copy(f.state.edgeBendPos),before.bends,'manual bends remain unchanged');
  assert.deepEqual(copy(f.state.edgeLabelPos),before.ratios,'manual label ratios remain unchanged');
  assert.deepEqual([...f.state.graphSelection.ids],['SYN-A']);
  assert.deepEqual(f.state.graph.findAllByState('node','selected').map(item=>item.model.id),['SYN-A']);
  for(const id of ['SYN-NEW-1','SYN-NEW-2']){
    const point=f.state.positions[id];assert.ok(point&&Number.isFinite(point.x)&&Number.isFinite(point.y),'new placement is saved as finite world coordinates');
    assert.equal(point.x>220&&point.x<380&&point.y>220&&point.y<380,false,'new accounts avoid the old crossing core');
    for(const old of Object.values(before.positions))assert.ok(Math.hypot(point.x-old.x,point.y-old.y)>=60,'new accounts do not overlap existing node bodies');
  }
  assert.deepEqual(copy(f.state.lastGraph.edges.slice(0,2)),before.facts.edges,'existing directions and exact monetary strings are unchanged');
  assert.equal(f.state.lastGraph.edges[2].total,'1234567890123456.78');
  assert.deepEqual([f.h.element('#graphContainer').clientWidth,f.h.element('#graphContainer').clientHeight],before.size,'no chrome or canvas resize is needed');
  const placed=copy(f.state.positions);await f.h.run('trackFlows([3,4])');f.settle();
  assert.deepEqual(copy(f.state.positions),placed,'repeated tracking with no new accounts does not run another placement');
  assert.equal(f.state.graph.getNodes().length,6);assert.equal(f.state.graph.getEdges().length,4);
  assert.deepEqual(f.camera,before.camera);assert.equal(f.fits,0);
});

test('stable expansion: hiding and revealing an account restores its stored placement without fitting or moving its neighbors',()=>{
  const f=stableExpansionScene(),positions=JSON.parse(JSON.stringify(f.state.positions)),camera=f.camera;
  f.state.graphView.visibleIds=new Set(['SYN-A','SYN-B']);f.h.run('renderGraph()');f.settle();
  assert.equal(f.state.graph.findById('SYN-C'),undefined);
  f.state.graphView.visibleIds=null;f.h.run('renderGraph()');f.settle();
  for(const [id,point]of Object.entries(positions)){
    const model=f.state.graph.findById(id).getModel();assert.deepEqual({x:model.x,y:model.y},point);
  }
  assert.deepEqual(JSON.parse(JSON.stringify(f.state.positions)),positions);
  assert.deepEqual(f.camera,camera);assert.equal(f.fits,0);
});

test('stable expansion: appending keeps a live dragged position even before the delayed position cache is saved',async()=>{
  const f=stableExpansionScene(),camera=f.camera,stored=JSON.parse(JSON.stringify(f.state.positions));
  f.state.graph.updateItem(f.state.graph.findById('SYN-A'),{x:-70,y:80});
  assert.deepEqual(JSON.parse(JSON.stringify(f.state.positions['SYN-A'])),stored['SYN-A'],'the fixture retains the deliberately stale cache');
  f.state.lastGraph={...f.state.lastGraph,nodes:[...f.state.lastGraph.nodes,f.node('SYN-ADDED-AFTER-DRAG')],
    edges:[...f.state.lastGraph.edges,f.edge('SYN-A','SYN-ADDED-AFTER-DRAG',3,'0.01')],tracked_ids:[1,2,3]};
  f.h.network(path=>response(path.startsWith('/api/graph?')?f.state.lastGraph:{}));
  await f.h.run('loadGraph()');f.settle();
  const anchor=f.state.graph.findById('SYN-A').getModel();assert.deepEqual({x:anchor.x,y:anchor.y},{x:-70,y:80});
  assert.deepEqual(JSON.parse(JSON.stringify(f.state.positions['SYN-A'])),{x:-70,y:80});
  for(const id of ['SYN-B','SYN-C','SYN-D'])assert.deepEqual(JSON.parse(JSON.stringify(f.state.positions[id])),stored[id]);
  assert.deepEqual(f.camera,camera);assert.equal(f.fits,0);
});

test('initial layout: a named canvas without valid saved coordinates runs one automatic layout after rebuilding',async()=>{
  const f=graphInteractions(),workers=[];let fits=0;
  f.env.Worker=class {
    constructor(){workers.push(this)}postMessage(message){this.message=message}terminate(){}
    deliver(nodes){this.onmessage({data:workerReply(f.env,this.message,{result:{nodes}})})}
  };
  f.env.fitGraph=()=>{fits++};
  f.state.canvasId=7;f.state.canvasName='合成无坐标画布';f.state.forceFit=false;
  f.state.lastGraph={nodes:[{account:'SYN-A',name:'合成甲',in_cnt:0,out_cnt:1},{account:'SYN-B',name:'合成乙',in_cnt:1,out_cnt:0}],
    edges:[{source:'SYN-A',target:'SYN-B',ids:[1],cnt:1,total:'0.01',currencies:['CNY']}],tracked_ids:[1]};
  const facts=JSON.stringify(f.state.lastGraph);
  f.h.run('applyCanvasSnapshot({nodes:[{id:"SYN-A",x:null,y:null},{id:"SYN-B",x:null,y:null}],edges:[],display:{layout:"auto"}})');
  assert.equal(workers.length,1,'a named canvas rebuild must not suppress its initial placement');
  assert.equal(workers[0].message.input.kind,'auto');
  assert.deepEqual(Array.from(workers[0].message.input.nodes,node=>node.id),['SYN-A','SYN-B']);
  workers[0].deliver([{id:'SYN-A',x:150,y:200},{id:'SYN-B',x:450,y:200}]);
  for(let pass=0;pass<4;pass++)await new Promise(resolve=>setImmediate(resolve));
  assert.equal(fits,1,'the initial placement fits the completed graph');
  assert.deepEqual(JSON.parse(JSON.stringify(f.state.positions)),{'SYN-A':{x:150,y:200},'SYN-B':{x:450,y:200}});
  f.h.run('renderGraph()');assert.equal(workers.length,1,'saved coordinates prevent another initial placement');
  assert.equal(JSON.stringify(f.state.lastGraph),facts);
});

function openingProjectionScene() {
  const f=graphInteractions({geometry:true}),fits=[],pending=[];
  const cameraEvents=new Map(),container=f.h.element('#graphContainer');
  container.addEventListener=(type,listener)=>{if(!cameraEvents.has(type))cameraEvents.set(type,new Set());cameraEvents.get(type).add(listener)};
  container.removeEventListener=(type,listener)=>cameraEvents.get(type)?.delete(listener);
  const attach=graph=>{
    let matrix=[.15,0,0,0,.15,0,20,30,1];
    const get=graph.get.bind(graph),group={getMatrix:()=>matrix,setMatrix:value=>{matrix=Array.from(value)}};
    graph.get=name=>name==='group'?group:get(name);
    graph.getZoom=()=>matrix[0];
    graph.zoomTo=zoom=>{matrix[0]=matrix[4]=zoom};
    graph.translate=(x,y)=>{matrix[6]+=x;matrix[7]+=y};
    graph.getCanvasByPoint=(x,y)=>({x:x*matrix[0]+matrix[6],y:y*matrix[4]+matrix[7]});
    graph.getPointByCanvas=(x,y)=>({x:(x-matrix[6])/matrix[0],y:(y-matrix[7])/matrix[4]});
    graph.getPointByClient=graph.getPointByCanvas;
  };
  const Graph=f.env.G6.Graph;
  f.env.G6.Graph=class extends Graph { constructor(options){super(options);attach(this)} };
  attach(f.state.graph);
  f.env.fitGraph=(...args)=>{fits.push(f.state.graph.getNodes().map(item=>item.model.id));return f.fitGraph(...args)};
  f.env.AbortController=AbortController;
  f.env.document.readyState='loading';
  f.h.run(readFileSync(new URL('../static/canvas-investigation.js',import.meta.url),'utf8'));
  const nodes=['SYN-A','SYN-B','SYN-C'].map(account=>({account,name:account,in_cnt:1,out_cnt:1}));
  const edge=(source,target,id)=>({source,target,ids:[id],cnt:1,total:'0.01',currencies:['CNY'],
    amount_totals:[{currency:'CNY',amount:'0.01'}],tmin:'2026-01-03 10:02:00',tmax:'2026-01-03 10:02:00'});
  const full={nodes,edges:[edge('SYN-A','SYN-B','1'),edge('SYN-B','SYN-C','2')],tracked_ids:['1','2']};
  const snapshot={nodes:[{id:'SYN-A',x:100,y:100},{id:'SYN-B',x:500,y:100},{id:'SYN-C',x:3000,y:100}],
    edges:[{source:'SYN-A',target:'SYN-B',type:'polyline',controlPoints:[{x:280,y:140}],routeManual:true,labelRatio:.37}],
    presentation:{timeWindow:{start:'2026-01-03 10:02:00',end:'2026-01-04 10:03:00',includeUndated:false}},
    display:{labelMode:'all',edgeLabels:['money','time']}};
  const projected={nodes:nodes.slice(0,2),edges:full.edges.slice(0,1),summary:{selected_count:1},timeline:[]};
  f.h.network(path=>{
    if(path==='/api/canvases/7?case_id=1')return response({id:7,name:'合成时间窗口',snapshot});
    if(path.startsWith('/api/graph?'))return response(full);
    if(path==='/api/canvas/investigation/time'){const request=deferred();pending.push(request);return request.promise}
    if(path.startsWith('/api/export/tracked.xlsx'))return {ok:true,blob:async()=>new Blob(['synthetic'])};
    return response({});
  });
  const flush=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve))};
  const settle=(maxDelay=300)=>{
    for(let pass=0;pass<5;pass++)for(const timer of f.h.timers.filter(timer=>!timer.cancelled&&!timer.ran&&timer.delay<=maxDelay)){
      timer.ran=true;timer.callback();
    }
  };
  return {...f,fits,pending,full,snapshot,projected,flush,settle,
    async open(){await f.h.run('loadCanvas(7)');await flush()},
    async deliver(value=projected,index=pending.length-1){pending[index].resolve(response(value));await flush();settle()},
    cameraEvent(type){for(const listener of cameraEvents.get(type)||[])listener({button:0})},
    geometry:()=>JSON.stringify({positions:f.state.positions,bends:f.state.edgeBendPos,ratios:f.state.edgeLabelPos}),
  };
}

test('opening projection: a named time-window canvas fits the visible result once after the opening timer has elapsed',async()=>{
  const f=openingProjectionScene();await f.open();
  assert.equal(f.pending.length,1);assert.equal(f.state.graph.getNodes().length,0);
  const before=f.geometry();f.settle(300);
  assert.deepEqual(f.fits,[],'the opening fit must wait beyond 250ms rather than fit an empty placeholder');
  await f.deliver();
  assert.deepEqual(f.fits,[['SYN-A','SYN-B']],'the completed projection owns one fit of the actual visible graph');
  assert.equal(f.geometry(),before,'opening fit preserves all saved coordinates, manual bends and label ratios');
  f.h.run('renderGraph()');f.settle();
  assert.equal(f.fits.length,1,'ordinary later renders cannot reuse the opening intent');
  assert.equal(f.state.lastGraph.nodes.length,3);
});

test('opening projection: a fast response fits once and cancels the later opening timer',async()=>{
  const f=openingProjectionScene();await f.open();await f.deliver();
  assert.deepEqual(f.fits,[['SYN-A','SYN-B']]);f.settle();assert.equal(f.fits.length,1);
});

for(const empty of [false,true])test(`opening projection: ${empty?'empty':'populated'} cached result consumes the opening fit without another request`,async()=>{
  const f=openingProjectionScene();await f.open();
  const result=empty?{...f.projected,nodes:[],edges:[]}:f.projected;
  f.settle();await f.deliver(result);
  assert.equal(f.fits.length,empty?0:1,'an empty projection never fits an empty placeholder');
  f.fits.length=0;f.env.savedOpeningSnapshot=f.snapshot;f.h.run('applyCanvasSnapshot(savedOpeningSnapshot)');f.settle();
  assert.equal(f.pending.length,1,'the existing exact-window projection is reused');
  assert.deepEqual(f.fits,empty?[]:[['SYN-A','SYN-B']]);
  f.h.run('renderGraph()');f.settle();assert.equal(f.fits.length,empty?0:1);
});

for(const interaction of ['zoom','pan','camera round trip','focus','layout','window round trip','case A-B-A','export'])
test(`opening projection: ${interaction} during the wait revokes the old automatic fit`,async()=>{
  const f=openingProjectionScene();await f.open();f.settle();
  if(interaction==='zoom'){f.cameraEvent('wheel');f.state.graph.zoomTo(.8)}
  if(interaction==='pan'){f.cameraEvent('pointerdown');f.state.graph.translate(100,50)}
  if(interaction==='camera round trip'){
    f.cameraEvent('pointerdown');f.state.graph.translate(100,50);f.state.graph.translate(-100,-50);
  }
  if(interaction==='focus'){
    const focused=await f.state.graphCamera.focus({x:100,y:100},{duration:0});assert.equal(focused.ok,true);
  }
  if(interaction==='layout')await f.h.run('relayout(true)');
  if(interaction==='window round trip'){
    f.h.run('globalThis.savedWindow=state.presentation.timeWindow;state.presentation.timeWindow={start:"",end:"",includeUndated:true};renderGraph();state.presentation.timeWindow=savedWindow;renderGraph()');
  }
  if(interaction==='case A-B-A')f.h.run('selectCase(2);selectCase(1)');
  if(interaction==='export'){f.h.bindApp();await f.h.sampleExport('xlsx');assert.equal(f.h.downloads.length,1)}
  const camera=JSON.stringify(f.state.graph.get('group').getMatrix()),fits=f.fits.length;
  await f.deliver();
  assert.equal(f.fits.length,fits,'the delayed result cannot reclaim an explicit later camera/workspace action');
  assert.equal(JSON.stringify(f.state.graph.get('group').getMatrix()),camera);
});

test('opening projection: a retired case response cannot fit or complete a newer opening of the same case',async()=>{
  const f=openingProjectionScene();await f.open();f.settle();
  f.h.run('selectCase(2);selectCase(1)');await f.open();f.settle();assert.equal(f.pending.length,2);
  await f.deliver(f.projected,0);assert.equal(f.fits.length,0);assert.equal(f.state.graph.getNodes().length,0);
  await f.deliver(f.projected,1);assert.deepEqual(f.fits,[['SYN-A','SYN-B']]);
});

test('stable expansion: hidden accounts reserve their node bodies and complete multiline label area',()=>{
  const f=stableExpansionScene(),copy=value=>JSON.parse(JSON.stringify(value));
  f.state.lastGraph.nodes.find(node=>node.account==='SYN-D').name='合成隐藏账户用于测试完整多行标签占位不要被新增节点覆盖';
  f.state.presentation=f.env.FundsGraphPresentation.create({nodeStyles:{'SYN-D':{size:96}},nodeLabels:{'SYN-D':{fontSize:20,lineHeight:24}}});
  f.state.graphView.visibleIds=new Set(['SYN-A','SYN-B','SYN-C']);f.h.run('renderGraph()');f.settle();
  assert.equal(f.state.graph.findById('SYN-D'),undefined);
  const original=f.env.FundsGraphLayout,inputs=[];
  f.env.FundsGraphLayout={...original,placeIncremental(input){inputs.push(copy(input));return original.placeIncremental(input)}};
  f.state.lastGraph.nodes.push(f.node('SYN-ADDED-BESIDE-HIDDEN'));
  f.state.lastGraph.edges.push(f.edge('SYN-A','SYN-ADDED-BESIDE-HIDDEN',3,'0.01'));
  f.state.graphView.visibleIds.add('SYN-ADDED-BESIDE-HIDDEN');f.h.run('renderGraph()');f.settle();
  assert.equal(inputs.length,1);
  const reserved=inputs[0].obstacles.map(obstacle=>obstacle.bounds).find(bounds=>bounds&&bounds.minX<=452&&bounds.maxX>=548&&bounds.minY<=52&&bounds.maxY>=220);
  assert.ok(reserved,'the hidden 96 px body and lower multiline text have one retained exclusion area');
  const point=f.state.positions['SYN-ADDED-BESIDE-HIDDEN'];
  assert.equal(point.x>=reserved.minX&&point.x<=reserved.maxX&&point.y>=reserved.minY&&point.y<=reserved.maxY,false,'the real kernel keeps the new account out of that area');
  assert.deepEqual(copy(f.state.positions['SYN-D']),{x:500,y:100});
  f.state.graphView.visibleIds=null;f.h.run('renderGraph()');f.settle();
  const restored=f.state.graph.findById('SYN-D').getModel();assert.deepEqual({x:restored.x,y:restored.y},{x:500,y:100});
});

test('stable expansion: an edge-dense append uses exterior placement without sampling every old curved path',()=>{
  const f=graphInteractions({geometry:true}),copy=value=>JSON.parse(JSON.stringify(value)),inputs=[];
  // Label drawing has its own path work. This regression measures the obstacle
  // collection performed by incremental publication before the drawing pass.
  f.env.applyGraphLabels=()=>{};
  const ids=Array.from({length:42},(_,i)=>`SYN-DENSE-${i}`),edges=[];
  for(const source of ids)for(const target of ids)if(source!==target&&edges.length<801)edges.push({source,target,ids:[edges.length+1],cnt:1,total:'0.01',currencies:['CNY']});
  f.state.lastGraph={nodes:ids.map(account=>({account,name:'合成密集节点',in_cnt:1,out_cnt:1})),edges,tracked_ids:edges.map(edge=>edge.ids[0])};
  f.state.positions=Object.fromEntries(ids.map((id,i)=>[id,{x:100+i%7*180,y:100+Math.floor(i/7)*180}]));
  f.h.run('renderGraph()');
  const before=copy(f.state.positions),original=f.env.FundsGraphLayout,path=f.env.edgePts;let samples=0;
  f.env.FundsGraphLayout={...original,placeIncremental(input){inputs.push(copy(input));return original.placeIncremental(input)}};
  f.env.edgePts=(...args)=>{samples++;return path(...args)};
  f.state.lastGraph.nodes.push({account:'SYN-DENSE-NEW',name:'合成新增',in_cnt:1,out_cnt:0});
  f.state.lastGraph.edges.push({source:ids[0],target:'SYN-DENSE-NEW',ids:[802],cnt:1,total:'0.01',currencies:['CNY']});
  f.h.run('renderGraph()');
  assert.equal(inputs.length,1);assert.equal(inputs[0].externalOnly,true,'edge density triggers the bounded exterior strategy even with few new nodes');
  assert.equal(samples,0,'collecting dense obstacles uses rendered bounds rather than 24 segment curve sampling');
  assert.ok(inputs[0].obstacles.some(obstacle=>obstacle.bounds),'real geometry remains represented by bounding areas');
  for(const [id,position]of Object.entries(before))assert.deepEqual(copy(f.state.positions[id]),position);
  const point=f.state.positions['SYN-DENSE-NEW'];assert.ok(Number.isFinite(point.x)&&Number.isFinite(point.y));
});

test('stable expansion: locating recent additions uses shared framing without changing graph facts or geometry',async()=>{
  const f=stableExpansionScene(),copy=value=>JSON.parse(JSON.stringify(value));
  assert.equal(await f.h.run('locateGraphAdditions()'),false,'an initialized graph with no new accounts has no locate target');
  f.state.lastGraph.nodes.push(f.node('SYN-RECENT-1'),f.node('SYN-RECENT-2'));
  f.state.lastGraph.edges.push(f.edge('SYN-A','SYN-RECENT-1',3,'10000000000000000.01'),f.edge('SYN-RECENT-1','SYN-RECENT-2',4,'0.01'));
  f.state.lastGraph.tracked_ids.push(3,4);f.h.run('renderGraph()');f.settle();
  assert.deepEqual([...f.state.graphView.recentNodeIds].sort(),['SYN-RECENT-1','SYN-RECENT-2']);
  const before={camera:f.camera,positions:copy(f.state.positions),bends:copy(f.state.edgeBendPos),
    ratios:copy(f.state.edgeLabelPos),facts:copy(f.state.lastGraph),selected:[...f.state.graphSelection.ids],
    canvas:[f.h.element('#graphContainer').clientWidth,f.h.element('#graphContainer').clientHeight]};
  f.state.graph.zoomTo=()=>{throw new Error('locate must never change the current zoom')};
  let focusCalls=0;const focus=f.state.graphCamera.focus;
  f.state.graphCamera.focus=(...args)=>{focusCalls++;return focus(...args)};
  assert.equal(await f.h.run('locateGraphAdditions()'),true);
  assert.equal(focusCalls,1,'newly revealed accounts use the same smooth camera transaction as explicit searches');
  assert.equal(f.camera[0],before.camera[0]);assert.equal(f.camera[4],before.camera[4]);
  assert.notDeepEqual(f.camera,before.camera,'the command pans the current view');assert.equal(f.fits,0);
  for(const id of f.state.graphView.recentNodeIds){
    const bounds=f.state.graph.findById(id).getContainer().getCanvasBBox();
    assert.ok(bounds.minX>=24&&bounds.maxX<=before.canvas[0]-24&&bounds.minY>=24&&bounds.maxY<=before.canvas[1]-24,
      `${id} includes the full body and label within the usable viewport`);
  }
  assert.deepEqual(copy(f.state.positions),before.positions);assert.deepEqual(copy(f.state.edgeBendPos),before.bends);
  assert.deepEqual(copy(f.state.edgeLabelPos),before.ratios);assert.deepEqual(copy(f.state.lastGraph),before.facts);
  assert.deepEqual([...f.state.graphSelection.ids],before.selected);
  assert.deepEqual([f.h.element('#graphContainer').clientWidth,f.h.element('#graphContainer').clientHeight],before.canvas);
  const first=f.camera;assert.equal(await f.h.run('locateGraphAdditions()'),true);
  assert.ok(f.camera.every((value,index)=>Math.abs(value-first[index])<1e-7),'repeated locate is idempotent to subpixel precision');
  const settled=f.camera;
  f.state.graphView.recentNodeIds=new Set(['SYN-NO-LONGER-DISPLAYED']);
  assert.equal(await f.h.run('locateGraphAdditions()'),false);assert.deepEqual(f.camera,settled,'missing targets do not move the view');
});

test('stable expansion: locating recent additions omits filtered or missing accounts and uses the unobstructed reading area',async()=>{
  const f=graphLocatorScene(),drawer=f.h.element('#drawer'),calls=[],focus=f.env.focusGraphItems;
  f.state.presentation.hiddenAccounts=[f.ids[1]];f.h.run('renderGraph()');f.settle();
  f.state.graphView.recentNodeIds=new Set([f.ids[0],f.ids[1],'SYN-MISSING']);
  f.graph.get('group').setMatrix([.2,0,0,0,.2,0,93,-47,1]);
  drawer.hidden=false;drawer.inert=false;drawer.id='drawer';drawer.classList={contains:name=>name==='open'};
  drawer.getClientRects=()=>[drawer.getBoundingClientRect()];
  drawer.getBoundingClientRect=()=>({left:990,top:180,right:1290,bottom:780,width:300,height:600});
  f.env.focusGraphItems=(items,options)=>{calls.push(Array.from(items,item=>item.getModel().id));return focus(items,options)};
  const before=f.snapshot();
  assert.equal(await f.h.run('locateGraphAdditions()'),true);
  assert.deepEqual(calls,[[f.ids[0]]],'only current graph members are eligible for locate-new');
  assert.ok(Math.abs(f.camera[0]-.85)<1e-10,'a zoomed-out overview recovers a usable reading scale when the group fits');
  const node=f.graph.findById(f.ids[0]),model=node.getModel(),point=f.graph.getCanvasByPoint(model.x,model.y),bounds=node.getContainer().getCanvasBBox();
  assert.ok(Math.abs(point.x-300)<1e-8&&Math.abs(point.y-300)<1e-8,'the existing editor is excluded from the focused area');
  assert.ok(bounds.minX>=24&&bounds.maxX<=576&&bounds.minY>=24&&bounds.maxY<=576,'full account text remains visible');
  assert.equal(f.snapshot(),before);assert.equal(f.historyCommands,0);assert.equal(f.h.requests.length,0);
  f.state.graphView.recentNodeIds=new Set([f.ids[1],'SYN-MISSING']);
  const camera=f.camera;
  assert.equal(await f.h.run('locateGraphAdditions()'),false);assert.deepEqual(f.camera,camera);assert.equal(calls.length,1);
});

test('stable expansion: continuing a public neighborhood reveals saved accounts without fitting or moving the existing view',async()=>{
  const f=stableExpansionScene(),copy=value=>JSON.parse(JSON.stringify(value)),leaves=Array.from({length:81},(_,i)=>`SYN-NEIGHBOR-${String(i).padStart(3,'0')}`);
  f.h.bindApp();
  f.state.lastGraph={nodes:['SYN-ROOT',...leaves].map(f.node),
    edges:leaves.map((id,i)=>f.edge('SYN-ROOT',id,i+1,i?'0.01':'9999999999999999.99')),tracked_ids:leaves.map((_,i)=>i+1)};
  f.state.positions=Object.fromEntries(['SYN-ROOT',...leaves].map((id,i)=>[id,{x:100+i%10*180,y:100+Math.floor(i/10)*180}]));
  f.state.graphView.fullIndex=f.env.FundsGraphView.createIndex(f.state.lastGraph);
  f.state.graphView.visibleIds=null;f.h.run('renderGraph()');f.settle();
  f.h.element('#neighborAccount').value='SYN-ROOT';f.h.element('#neighborDirection').value='both';
  await f.h.element('#btnNeighborhood').onclick();f.settle();
  assert.equal(f.state.graph.getNodes().length,51);assert.equal(f.state.graphView.remaining,31);
  assert.equal(f.fits,0,'changing the neighborhood focuses the center without a full-graph fit');
  const root=f.state.graph.findById('SYN-ROOT').getModel(),rootPoint=f.state.graph.getCanvasByPoint(root.x,root.y);
  assert.ok(Math.abs(rootPoint.x-450)<1e-8&&Math.abs(rootPoint.y-300)<1e-8,'the explicit center reaches the available viewport center');
  f.state.graph.get('group').setMatrix([1.2,0,0,0,1.2,0,127,-243,1]);
  const before={camera:f.camera,positions:copy(f.state.positions),visible:new Set(f.state.graphView.visibleIds),
    facts:copy(f.state.lastGraph),fits:f.fits,requests:f.h.requests.length};
  await f.h.element('#btnExpandNeighbors').onclick();f.settle();
  assert.equal(f.state.graph.getNodes().length,82);assert.equal(f.state.graphView.remaining,0);
  assert.deepEqual(f.camera,before.camera,'continuing the same root and direction preserves user zoom and pan');
  assert.equal(f.fits,before.fits);assert.deepEqual(copy(f.state.positions),before.positions,'saved positions of both old and newly revealed accounts stay fixed');
  const recent=[...f.state.graphView.recentNodeIds];assert.equal(recent.length,31);
  assert.ok(recent.every(id=>!before.visible.has(id)),'locate targets only newly revealed accounts, even when their coordinates were already saved');
  assert.equal(await f.h.run('locateGraphAdditions()'),true);
  assert.ok(f.camera[0]>0&&f.camera[0]<=before.camera[0],'a large addition group can zoom out to preserve complete visibility');
  assert.equal(f.camera[0],f.camera[4]);
  for(const id of recent){
    const bounds=f.state.graph.findById(id).getContainer().getCanvasBBox();
    assert.ok(bounds.minX>=24-1e-7&&bounds.maxX<=876+1e-7&&bounds.minY>=24-1e-7&&bounds.maxY<=576+1e-7,
      `${id} remains fully visible after locating a large group`);
  }
  assert.equal(f.fits,before.fits);assert.deepEqual(copy(f.state.positions),before.positions);
  assert.deepEqual(copy(f.state.lastGraph),before.facts);assert.equal(f.h.requests.length,before.requests,'display expansion does not mutate tracked facts');
  f.h.element('#neighborDirection').value='out';await f.h.element('#btnNeighborhood').onclick();f.settle();
  assert.equal(f.fits,before.fits,'an explicit change of exploration direction uses camera focus instead of a full fit');
  const finalRoot=f.state.graph.findById('SYN-ROOT').getModel(),finalPoint=f.state.graph.getCanvasByPoint(finalRoot.x,finalRoot.y);
  assert.ok(Math.abs(finalPoint.x-450)<1e-8&&Math.abs(finalPoint.y-300)<1e-8);
  assert.deepEqual(copy(f.state.lastGraph),before.facts);
});

for(const layout of ['auto','partition'])test(`graph persistence: ${layout} layout survives the public snapshot and reopen path`,async()=>{
  const f=stableExpansionScene();f.h.bindApp();f.state.layout=layout;
  const before=JSON.stringify(f.state.lastGraph),positions=JSON.parse(JSON.stringify(f.state.positions));
  const snapshot=await f.h.run('window.__collectSnapshot({scope:"all",embedPhotos:false})');
  assert.equal(snapshot.display.layout,layout,'the saved canvas retains the requested task layout');
  f.env.reopenedLayoutSnapshot=snapshot;f.state.layout='force';
  f.h.run('applyCanvasSnapshot(reopenedLayoutSnapshot)');f.settle();
  assert.equal(f.state.layout,layout,'opening a saved canvas must accept the new layout value');
  assert.deepEqual(JSON.parse(JSON.stringify(f.state.positions)),positions,'opening a saved layout retains its saved coordinates');
  assert.equal(JSON.stringify(f.state.lastGraph),before,'reopen never converts exact money or reverses transactions');
  assert.deepEqual(JSON.parse(JSON.stringify(f.state.edgeBendPos)),{'SYN-A=>SYN-B':[{x:190,y:290}]});
  assert.equal(f.state.graph.getNodes().length,4);
});

for(const transition of ['selectCase(2);selectCase(1)','activateCanvas(7);activateCanvas(0)'])
test(`stable expansion: a delayed append read is revoked by ${transition}`,async()=>{
  const f=stableExpansionScene(),pending=deferred();
  f.h.network(path=>path.startsWith('/api/graph?')?pending.promise:response({}));
  const read=f.h.run('loadGraph()');
  f.h.run(transition);
  f.state.graphLoading=false;
  f.state.lastGraph={nodes:[f.node('SYN-CURRENT')],edges:[],tracked_ids:[90]};
  f.state.positions={'SYN-CURRENT':{x:720,y:830}};f.state.forceFit=false;
  f.h.run('renderGraph()');f.settle();
  const positions=JSON.stringify(f.state.positions),facts=JSON.stringify(f.state.lastGraph),camera=f.camera;
  pending.resolve(response({nodes:[f.node('SYN-STALE')],edges:[],tracked_ids:[99]}));await read;f.settle();
  assert.equal(JSON.stringify(f.state.positions),positions);assert.equal(JSON.stringify(f.state.lastGraph),facts);
  assert.deepEqual(f.state.graph.getNodes().map(item=>item.model.id),['SYN-CURRENT']);
  assert.deepEqual(f.camera,camera,'a stale append cannot restore the previous graph camera');
});

test('graph interaction: node labels retain complete long Chinese names, accounts and optional identity text', () => {
  const h = harness('app.js');
  const name = '合成测试专用长中文名称超过十二个字符的企业账户';
  const account = '000012345678901234567890', identity = 'SYN-IDENTITY-0001';
  const env = h.run('globalThis'); env.longNode = { name, account, id_no: identity, is_main: 1 };
  h.run('state.showIds=true');
  let lines = h.run('nodeLabel(longNode)').split('\n'), accountLine = lines.indexOf(account);
  assert.ok(accountLine > 0);
  assert.equal(lines.slice(0, accountLine).join(''), `★ ${name}`, 'wrapping may add lines but cannot discard characters');
  assert.deepEqual(lines.slice(accountLine), [account, `证件 ${identity}`]);
  h.run('state.showIds=false');
  lines = h.run('nodeLabel(longNode)').split('\n'); accountLine = lines.indexOf(account);
  assert.equal(lines.slice(0, accountLine).join(''), `★ ${name}`);
  assert.deepEqual(lines.slice(accountLine), [account]);
});

test('graph interaction: the app passes all 18 selected IDs into real focus-label selection', () => {
  const f = graphInteractions({ selection: true }), graph = f.state.graph;
  const nodes = Array.from({ length: 18 }, (_, index) => ({ id: `SYN-${index}`, account: `SYN-${index}`,
    x: 70 + index % 6 * 140, y: 70 + Math.floor(index / 6) * 150, size: 30,
    label: `合成节点${index}`, fundsLabel: `合成节点${index}` }));
  graph.data({ nodes, edges: [] });
  f.state.lastGraph = { nodes, edges: [] };
  f.state.graphView.index = f.env.FundsGraphView.createIndex(f.state.lastGraph);
  f.state.graphView.fullIndex = f.state.graphView.index;
  f.state.graphView.nodeItems = new Map(graph.getNodes().map(node => [node.model.id, node]));
  f.state.graphView.labelMode = 'focus';
  f.state.graphSelection.rebind(nodes.map(node => node.id));
  assert.equal(f.state.graphSelection.ids.size, 18);
  assert.equal(f.state.graphView.selectedAccount, null, 'the multi-selection exceeds the single drawer focus threshold');
  const view = f.env.FundsGraphView, calls = [];
  f.env.FundsGraphView = { ...view, labels(index, options) { calls.push(options); return view.labels(index, options); } };
  f.applyLabels(false);
  assert.equal(calls.length, 1);
  assert.deepEqual([...calls[0].selectedIds].sort(), nodes.map(node => node.id).sort());
  assert.equal(f.state.graphView.policy.nodeIds.size, 18, 'focus must keep every explicit selected root');
  assert.equal(f.state.graphView.labeledNodes.size, 18);
});

test('graph interaction: graph ResizeObserver refreshes labels after the container changes size', () => {
  const f = graphInteractions({ observeResize: true }), container = f.h.element('#graphContainer');
  container.clientWidth = 900; container.clientHeight = 600;
  let labelRefreshes = 0, viewportRefreshes = 0;
  const sizes = [];
  f.env.scheduleGraphLabels = () => { labelRefreshes += 1; };
  f.env.scheduleGraphViewport = () => { viewportRefreshes += 1; };
  f.state.graph.changeSize = (width, height) => sizes.push([width, height]);
  const observer = f.observers.find(item => item.targets.includes(container));
  assert.ok(observer);
  container.clientHeight = 682; observer.callback();
  assert.deepEqual(sizes, [[900, 682]]);
  assert.equal(viewportRefreshes, 1);
  assert.equal(labelRefreshes, 1, 'newly exposed text candidates must be recomputed');
});

test('graph interaction: the fit command changes only the camera and creates no layout history entry',()=>{
  const f=graphInteractions(),graph=f.seed(),container=f.h.element('#graphContainer');
  container.clientWidth=900;container.clientHeight=600;
  f.state.positions={'SYN-A':{x:0,y:0},'SYN-B':{x:200,y:200}};
  f.state.edgeBendPos={'SYN-A=>SYN-B':[{x:75,y:130}]};
  f.state.edgeLabelPos={'SYN-A=>SYN-B':.37};
  f.state.edgeTypes={'SYN-A=>SYN-B':'quadratic'};
  f.state.presentation=f.env.FundsGraphPresentation.create({nodeLabels:{'SYN-A':{fontSize:20}},edgeStyles:{'SYN-A=>SYN-B':{lineWidth:5}}});
  let camera={zoom:.3,x:-90,y:120},layoutCalls=0;
  const boxes=[{minX:-100,minY:-30,maxX:100,maxY:80},{minX:180,minY:180,maxX:300,maxY:250},{minX:0,minY:20,maxX:200,maxY:200}];
  for(const [i,item]of [...graph.getNodes(),...graph.getEdges()].entries())item.getContainer=()=>({getCanvasBBox(){
    const box=boxes[i];return{minX:box.minX*camera.zoom+camera.x,minY:box.minY*camera.zoom+camera.y,
      maxX:box.maxX*camera.zoom+camera.x,maxY:box.maxY*camera.zoom+camera.y}}});
  graph.getPointByCanvas=(x,y)=>({x:(x-camera.x)/camera.zoom,y:(y-camera.y)/camera.zoom});
  graph.getCanvasByPoint=(x,y)=>({x:x*camera.zoom+camera.x,y:y*camera.zoom+camera.y});
  graph.getZoom=()=>camera.zoom;graph.zoomTo=zoom=>{camera.zoom=zoom};
  graph.translate=(x,y)=>{camera.x+=x;camera.y+=y};
  f.env.fitGraph=f.fitGraph;f.env.relayout=()=>{layoutCalls++};
  f.env.applyGraphViewport=()=>{};
  f.h.bindApp();
  f.h.run(readFileSync(new URL('../static/canvas-history.js',import.meta.url),'utf8'));
  f.h.run(readFileSync(new URL('../static/canvas-editor.js',import.meta.url),'utf8'));
  f.event('DOMContentLoaded');
  const history=f.env.window.FundsCanvasEditor.history;
  const unchanged=()=>JSON.stringify({positions:f.state.positions,bends:f.state.edgeBendPos,ratios:f.state.edgeLabelPos,
    types:f.state.edgeTypes,presentation:f.state.presentation,models:graph.getNodes().map(item=>item.model),facts:f.state.lastGraph});
  const before=unchanged(),initial={...camera},canvas=[container.clientWidth,container.clientHeight];
  f.h.element('#btnFit').onclick();
  assert.notDeepEqual(camera,initial,'the bound public command uses the real camera fit');
  assert.equal(camera.zoom,1.6);
  assert.equal(layoutCalls,0,'fit does not execute a node layout');
  assert.equal(history.status().count,0,'fit is a view command rather than an undoable layout edit');
  assert.equal(unchanged(),before,'saved coordinates, manual control points, ratios, typography and facts remain unchanged');
  assert.deepEqual([container.clientWidth,container.clientHeight],canvas,'the fit does not change the canvas area');
  const first={...camera};f.h.element('#btnFit').onclick();
  for(const key of ['zoom','x','y'])assert.ok(Math.abs(camera[key]-first[key])<1e-9);
  assert.equal(history.status().count,0);
});

test('graph interaction: repeated public fit repairs stale label offsets and preserves bottom anchors',()=>{
  const f=graphInteractions({geometry:true}),graph=f.state.graph,container=f.h.element('#graphContainer');
  container.clientWidth=900;container.clientHeight=600;
  graph.data({nodes:[{id:'SYN-A',x:200,y:200,label:'合成甲公司\nSYN-A',labelCfg:{style:{fontSize:13,lineHeight:18}}},
    {id:'SYN-B',x:600,y:400,label:'合成乙公司\nSYN-B',labelCfg:{style:{fontSize:13,lineHeight:18}}}],
  edges:[{id:'SYN-E',source:'SYN-A',target:'SYN-B',type:'quadratic',controlPoints:[{x:640,y:260}]}]});
  f.state.positions={'SYN-A':{x:200,y:200},'SYN-B':{x:600,y:400}};
  f.state.edgeBendPos={'SYN-A=>SYN-B':[{x:640,y:260}]};
  f.state.edgeLabelPos={'SYN-A=>SYN-B':.37};
  f.state.graphView.nodeItems=new Map(graph.getNodes().map(item=>[item.model.id,item]));
  f.state.graphView.edgeItems=new Map([['SYN-A=>SYN-B',graph.getEdges()[0]]]);
  f.state.lastGraph={nodes:graph.getNodes().map(item=>({account:item.model.id})),edges:[]};
  f.state.graphView.index=f.env.FundsGraphView.createIndex(f.state.lastGraph);
  f.state.graphView.fullIndex=f.state.graphView.index;f.state.graphView.labelMode='all';
  let camera={zoom:.3,x:-90,y:120},placementCalls=0;
  graph.getPointByCanvas=(x,y)=>({x:(x-camera.x)/camera.zoom,y:(y-camera.y)/camera.zoom});
  graph.getCanvasByPoint=(x,y)=>({x:x*camera.zoom+camera.x,y:y*camera.zoom+camera.y});
  graph.getZoom=()=>camera.zoom;graph.zoomTo=zoom=>{camera.zoom=zoom};
  graph.translate=(x,y)=>{camera.x+=x;camera.y+=y};
  for(const item of [...graph.getNodes(),...graph.getEdges()])for(const shape of item.getContainer().children) {
    shape.getCanvasBBox=()=>{
      const box=shape.getBBox(),model=item.model,dx=model.source?0:model.x,dy=model.source?0:model.y;
      const a=graph.getCanvasByPoint(box.minX+dx,box.minY+dy),b=graph.getCanvasByPoint(box.maxX+dx,box.maxY+dy);
      return{minX:a.x,minY:a.y,maxX:b.x,maxY:b.y};
    };
  }
  f.env.applyGraphLabels=()=>{f.applyLabels();placementCalls++};
  f.env.applyGraphViewport=()=>{};f.env.fitGraph=f.fitGraph;
  const text=graph.getNodes()[0].getContainer().children.find(shape=>shape.name==='text-shape');
  const baseX=text.attr('x'),baseY=text.attr('y');text.attr({x:baseX+600,y:baseY-500});
  text._fundsLabelOffset={baseX,baseY,x:baseX+600,y:baseY-500};
  assert.ok(text._fundsLabelOffset,'the starting graph contains a stale side/top displacement');
  f.env.applyGraphLabels();
  const assertAnchored=()=>{for(const item of graph.getNodes()){
    const shape=item.getContainer().children.find(shape=>shape.name==='text-shape');
    const box=shape.getCanvasBBox(),body=item.getKeyShape().getCanvasBBox();
    assert.ok(Math.abs((box.minX+box.maxX-body.minX-body.maxX)/2)<1e-8);
    assert.ok(Math.abs(box.minY-body.maxY-6)<1e-8);
    assert.equal(shape._fundsLabelOffset,undefined);
  }};
  assertAnchored();
  const unchanged=()=>JSON.stringify({positions:f.state.positions,bends:f.state.edgeBendPos,ratio:f.state.edgeLabelPos,
    models:graph.getNodes().map(item=>item.model),manualPath:graph.getEdges()[0].model.controlPoints});
  const before=unchanged();f.h.bindApp();
  f.h.element('#btnFit').onclick();const first={...camera};
  assert.equal(first.zoom,1.6,'fitting measures canonical text coordinates rather than the old avoidance envelope');
  assertAnchored();
  for(let i=0;i<3;i++) {
    f.h.element('#btnFit').onclick();
    for(const key of ['zoom','x','y'])assert.ok(Math.abs(camera[key]-first[key])<1e-8,
      `the next fit keeps camera ${key}: first=${JSON.stringify(first)},next=${JSON.stringify(camera)}`);
    assert.equal(unchanged(),before,'fit preserves saved node coordinates, manual control points and label ratios');
    assertAnchored();
  }
  assert.equal(placementCalls,9,'each public fit normalizes its label set before measuring and settles the final camera once');
});

for(const mode of ['auto','focus'])for(const [level,fitZoom]of [['names',.625],['detail',.4]]){
  test(`graph interaction: public fit is camera-idempotent from ${level} LOD in ${mode} mode`,()=>{
    const f=graphInteractions({geometry:true}),graph=f.state.graph,container=f.h.element('#graphContainer');
    container.clientWidth=1600;container.clientHeight=1600;
    const nodes=Array.from({length:80},(_,i)=>({id:'SYN-'+String(i).padStart(3,'0'),
      x:i===79?1100:200+i%8*100,y:i===79?1350:100+Math.floor(i/8)*100,
      label:i===79?'外沿完整账户名称'.repeat(15)+'\nSYN-079':'账户 '+i+'\nSYN-'+String(i).padStart(3,'0'),
      labelCfg:{style:{fontSize:13,lineHeight:18}}}));
    graph.data({nodes,edges:[]});
    f.state.positions=Object.fromEntries(nodes.map(node=>[node.id,{x:node.x,y:node.y}]));
    f.state.lastGraph={nodes:nodes.map(node=>({account:node.id,name:node.label})),edges:[],tracked_ids:[]};
    const view=f.state.graphView;view.index=f.env.FundsGraphView.createIndex(f.state.lastGraph);view.fullIndex=view.index;
    view.nodeItems=new Map(graph.getNodes().map(item=>[item.model.id,item]));view.edgeItems=new Map();
    view.labelMode=mode;view.fitZoom=fitZoom;
    if(mode==='focus')f.state.graphSelection={ids:new Set(nodes.map(node=>node.id)),refresh(){}};
    const camera={zoom:1,x:0,y:0};
    graph.getPointByCanvas=(x,y)=>({x:(x-camera.x)/camera.zoom,y:(y-camera.y)/camera.zoom});
    graph.getCanvasByPoint=(x,y)=>({x:x*camera.zoom+camera.x,y:y*camera.zoom+camera.y});
    graph.getZoom=()=>camera.zoom;graph.zoomTo=zoom=>{camera.zoom=zoom};
    graph.translate=(x,y)=>{camera.x+=x;camera.y+=y};
    f.env.applyGraphViewport=()=>{};f.env.fitGraph=f.fitGraph;f.h.bindApp();f.applyLabels();
    assert.equal(view.policy.level,level);
    assert.ok(view.policy.nodeIds.size>20,'the starting view contains more labels than the final overview');
    assert.equal(view.policy.nodeIds.has('SYN-079'),true,'the long outer label contributes to the starting measured envelope');
    const geometry=()=>JSON.stringify({positions:f.state.positions,
      nodes:graph.getNodes().map(item=>({id:item.model.id,x:item.model.x,y:item.model.y})),facts:f.state.lastGraph});
    const before=geometry();
    f.h.element('#btnFit').onclick();const first={...camera};
    assert.equal(view.policy.level,'overview');assert.ok(view.policy.nodeIds.size<=20);
    assert.equal(view.policy.nodeIds.has('SYN-079'),false,'the outer label is absent from the normalized final overview');
    for(let repeat=0;repeat<3;repeat++){
      f.h.element('#btnFit').onclick();
      for(const key of ['zoom','x','y'])assert.ok(Math.abs(camera[key]-first[key])<1e-8,
        `${mode}/${level} fit keeps ${key}: first=${JSON.stringify(first)},next=${JSON.stringify(camera)}`);
      assert.equal(geometry(),before,'LOD normalization and repeated fit never write node coordinates or facts');
    }
  });
}

test('graph interaction: selected-edge line type edits preserve other types and the global default through history', async () => {
  const f = graphInteractions(), graph = f.state.graph;
  f.h.bindApp();
  // Match the real editor's DOM binding: ID selectors must resolve controls so
  // an accidental second wrapper around edgeTypeSel cannot hide in this mock.
  f.env.document.querySelectorAll = selector => selector.split(',').flatMap(part =>
    /^#[\w-]+$/.test(part) ? [f.h.element(part)] : []);
  f.env.setTimeout = setTimeout; f.env.clearTimeout = clearTimeout;
  f.state.lastGraph = { nodes: ['SYN-A', 'SYN-B', 'SYN-C'].map(account => ({ account, in_cnt: 1, out_cnt: 1 })),
    edges: [{ source: 'SYN-A', target: 'SYN-B', cnt: 1 }, { source: 'SYN-B', target: 'SYN-C', cnt: 1 }], tracked_ids: [] };
  f.state.positions = { 'SYN-A': { x: 50, y: 50 }, 'SYN-B': { x: 250, y: 200 }, 'SYN-C': { x: 500, y: 350 } };
  f.state.edgeType = 'polyline';
  f.state.edgeTypes = { 'SYN-A=>SYN-B': 'line', 'SYN-B=>SYN-C': 'polyline' };
  f.h.run('renderGraph();selectItem(state.graph.getEdges()[0])');
  const copy = value => JSON.parse(JSON.stringify(value));
  f.h.network((path, options) => {
    if (path === '/api/track/state') return response({ ok: true, tracked_ids: JSON.parse(options.body).desired_ids });
    if (path.startsWith('/api/graph?')) return response(copy(f.state.lastGraph));
    throw new Error(`Unexpected synthetic editor request: ${path}`);
  });
  f.h.run(readFileSync(new URL('../static/canvas-history.js', import.meta.url), 'utf8'));
  f.h.run(readFileSync(new URL('../static/canvas-editor.js', import.meta.url), 'utf8'));
  f.event('DOMContentLoaded');
  const history = f.env.window.FundsCanvasEditor.history;
  f.h.element('#edgeTypeSel').onchange({ target: { value: 'quadratic' } });
  for (let i = 0; i < 100 && history.status().busy; i += 1) await new Promise(resolve => setTimeout(resolve, 1));
  assert.equal(history.status().busy, false);
  assert.equal(f.state.edgeType, 'polyline');
  assert.deepEqual(copy(f.state.edgeTypes), { 'SYN-A=>SYN-B': 'quadratic', 'SYN-B=>SYN-C': 'polyline' });
  assert.equal(graph.getEdges()[0].model.type, 'quadratic');
  assert.equal(graph.getEdges()[1].model.type, 'polyline');
  assert.equal(history.status().count, 1, 'the real bound editor records exactly one formatting action');
  assert.equal(f.h.requests.length, 0, 'changing line type itself does not mutate tracked membership');
  await f.h.element('#btnUndo').onclick();
  assert.deepEqual(copy(f.state.edgeTypes), { 'SYN-A=>SYN-B': 'line', 'SYN-B=>SYN-C': 'polyline' });
  assert.deepEqual(Array.from(f.state.graph.getEdges(), edge => edge.model.type), ['line', 'polyline']);
  await f.h.element('#btnRedo').onclick();
  assert.deepEqual(copy(f.state.edgeTypes), { 'SYN-A=>SYN-B': 'quadratic', 'SYN-B=>SYN-C': 'polyline' });
  assert.deepEqual(Array.from(f.state.graph.getEdges(), edge => edge.model.type), ['quadratic', 'polyline']);
  f.h.run('closeDrawer()');
  await f.h.element('#edgeTypeSel').onchange({ target: { value: 'line' } });
  assert.equal(history.status().count, 1, 'an unselected control cannot issue a formatting command');
  assert.equal(f.state.edgeType, 'polyline');
  assert.deepEqual(copy(f.state.edgeTypes), { 'SYN-A=>SYN-B': 'quadratic', 'SYN-B=>SYN-C': 'polyline' });
});

test('graph interaction: a synthetic photo node keeps its minimum geometry across an unrelated redraw', () => {
  const f = graphInteractions(), graph = f.state.graph;
  f.state.showPhotos = true;
  f.state.lastGraph = { nodes: [{ account: 'SYN-PHOTO', name: '合成头像', in_cnt: 0, out_cnt: 0, photo_at: 123 }], edges: [] };
  f.state.positions = { 'SYN-PHOTO': { x: 250, y: 200 } };
  f.h.run('renderGraph()');
  assert.equal(graph.getNodes()[0].model.type, 'image');
  assert.equal(graph.getNodes()[0].model.size, 40);
  f.h.run('renderGraph()');
  assert.equal(graph.getNodes()[0].model.size, 40);
  assert.equal(f.h.requests.filter(request => String(request.path).includes('account-photos')).length, 0,
    'the geometry regression neither reads photo pixels nor contacts a server');
});

function cleanLabelScene({ crossing=false }={}) {
  const f=graphInteractions({geometry:true});f.h.bindApp();
  const coordinates=crossing?{A:[100,240],B:[700,240],U:[400,50],D:[400,450]}
    :{A:[200,240],B:[500,240],U:[200,60],D:[200,450],C:[750,390]};
  const pairs=crossing?[['A','B'],['U','D']]:[['A','B'],['U','A'],['A','D'],['B','C']];
  f.state.lastGraph={nodes:Object.keys(coordinates).map(account=>({account,name:'合成 '+account,is_main:account==='A'?1:0,in_cnt:1,out_cnt:1})),
    edges:pairs.map(([source,target],i)=>({source,target,cnt:1,ids:[i+1],amount_totals:[{currency:'CNY',amount:i===0?'10000000000000000.01':'0.01'}]})),
    tracked_ids:pairs.map((_,i)=>i+1)};
  f.state.positions=Object.fromEntries(Object.entries(coordinates).map(([id,[x,y]])=>[id,{x,y}]));
  f.state.edgeType='quadratic';f.state.graphView.labelMode='all';f.state.forceFit=false;
  const facts=JSON.stringify(f.state.lastGraph);
  const edge=key=>f.state.graph.getEdges().find(item=>`${item.model.source}=>${item.model.target}`===key);
  const text=item=>item.getContainer().children.find(shape=>shape.name==='text-shape');
  const shape=(item,name)=>item.getContainer().children.find(shape=>shape.name===name);
  return {...f,edge,text,shape,facts,render(){f.h.run('renderGraph()')}};
}

test('clean graph interaction: hover zoom fit and history preserve manual node coordinates and bottom labels',async()=>{
  const f=cleanLabelScene({crossing:true});f.render();
  const graph=f.state.graph;
  f.state.tracked=new Set(f.state.lastGraph.tracked_ids);
  const completeGraph=plainGraph(f.state.lastGraph);
  f.env.document.querySelectorAll=selector=>selector.split(',').flatMap(part=>/^#[\w-]+$/.test(part)?[f.h.element(part)]:[]);
  f.env.fitGraph=f.fitGraph;
  let cameraX=0,cameraY=0;
  graph.getCanvasByPoint=(x,y)=>({x:x*graph.zoom+cameraX,y:y*graph.zoom+cameraY});
  graph.getPointByCanvas=(x,y)=>({x:(x-cameraX)/graph.zoom,y:(y-cameraY)/graph.zoom});
  graph.zoomTo=zoom=>{graph.zoom=zoom;graph.emit('viewportchange',{action:'zoom'})};
  graph.translate=(x,y)=>{cameraX+=x;cameraY+=y};
  f.h.network((path,options)=>{
    if(path==='/api/track/state')return response({ok:true,tracked_ids:JSON.parse(options.body).desired_ids});
    if(path.startsWith('/api/graph?'))return response(plainGraph(completeGraph));
    throw new Error(`Unexpected synthetic interaction request: ${path}`);
  });
  f.h.run(readFileSync(new URL('../static/canvas-history.js',import.meta.url),'utf8'));
  f.h.run(readFileSync(new URL('../static/canvas-editor.js',import.meta.url),'utf8'));
  f.event('DOMContentLoaded');
  const history=f.env.window.FundsCanvasEditor.history;
  const coordinates=()=>plainGraph(Object.fromEntries(f.state.graph.getNodes().map(item=>[item.model.id,{x:item.model.x,y:item.model.y}])));
  const assertLabels=()=>{for(const item of f.state.graph.getNodes()){
    const label=f.text(item),box=label.getCanvasBBox(),body=item.getKeyShape().getCanvasBBox();
    assert.ok(Math.abs((box.minX+box.maxX-body.minX-body.maxX)/2)<1e-8,`${item.model.id} stays centered`);
    assert.ok(Math.abs(box.minY-body.maxY-6)<1e-8,`${item.model.id} stays below the body by six screen pixels`);
    assert.equal(label._fundsLabelOffset,undefined);assert.equal(f.shape(item,'text-bg-shape'),undefined);
    assert.equal(f.shape(item,'funds-label-leader'),undefined);
  }};
  const original=coordinates();
  f.event('funds:selection-drag-start',{button:0});
  const moving=graph.findById('B');graph.updateItem(moving,{x:moving.model.x+12,y:moving.model.y+9});
  graph.emit('node:drag',{item:moving});f.env.savePositions();
  f.event('funds:selection-drag-end',{detail:{moved:true,cancelled:false}});
  const moved=coordinates();assert.notDeepEqual(moved,original);assert.equal(history.status().count,1);
  for(const zoom of [.4,2,1]){
    graph.emit('node:mouseenter',{item:graph.findById('B')});f.applyLabels();assertLabels();
    graph.zoomTo(zoom);f.applyLabels();assertLabels();
    graph.emit('node:mouseleave',{});f.applyLabels();assertLabels();
    f.h.element('#btnFit').onclick();assertLabels();
    assert.deepEqual(coordinates(),moved,'view changes and label passes never overwrite a manual position');
    assert.deepEqual(plainGraph(f.state.positions),moved,'the saved geometry cache agrees with the live nodes');
    assert.equal(history.status().count,1,'view changes do not add history entries');
  }
  await f.h.element('#btnUndo').onclick();f.applyLabels();assertLabels();
  assert.deepEqual(coordinates(),original,'undo alone restores the prior manual geometry');
  await f.h.element('#btnRedo').onclick();f.applyLabels();assertLabels();
  assert.deepEqual(coordinates(),moved,'redo alone restores the completed drag');
  for(let i=0;i<3;i++)f.applyLabels();assertLabels();assert.deepEqual(coordinates(),moved);
  assert.equal(history.status().count,1);assert.equal(JSON.stringify(f.state.lastGraph),f.facts);
});

test('clean graph interaction: automatic branches use short chord-centered arcs and recompute after node drag',()=>{
  const f=cleanLabelScene();f.render();
  const upper=f.edge('U=>A'),lower=f.edge('A=>D');
  assert.equal(upper.model.curvePolicy,'compact-arc');assert.equal(lower.model.curvePolicy,'compact-arc');
  assert.deepEqual(plainGraph(upper.model.controlPoints),[{x:185.6,y:150}]);
  assert.deepEqual(plainGraph(lower.model.controlPoints),[{x:183.2,y:345}]);
  assert.equal(upper.model.source,'U');assert.equal(upper.model.target,'A');
  assert.equal(lower.model.source,'A');assert.equal(lower.model.target,'D');
  f.state.graph.updateItem(f.state.graph.findById('A'),{x:230,y:260});
  f.state.graph.emit('node:drag',{item:f.state.graph.findById('A')});
  assert.deepEqual(plainGraph(f.edge('U=>A').model.controlPoints),[{x:199,y:162.4}]);
  assert.deepEqual(plainGraph(f.edge('A=>D').model.controlPoints),[{x:199.8,y:352.6}]);
  assert.equal(JSON.stringify(f.state.lastGraph),f.facts);
});

const plainGraph=value=>JSON.parse(JSON.stringify(value));
function assertCompactRoute(f,item){
  const m=item.model,a=f.state.graph.findById(m.source).model,b=f.state.graph.findById(m.target).model;
  assert.equal(m.type,'quadratic');assert.equal(m.controlPoints.length,1);
  const p=m.controlPoints[0],dx=b.x-a.x,dy=b.y-a.y,span=Math.hypot(dx,dy);
  assert.ok([p.x,p.y].every(Number.isFinite),'automatic arc controls stay finite');
  if(span<1e-9)return;
  const projection=((p.x-a.x)*dx+(p.y-a.y)*dy)/(span*span);
  const bend=((p.x-(a.x+b.x)/2)*-dy+(p.y-(a.y+b.y)/2)*dx)/span;
  assert.ok(Math.abs(projection-.5)<1e-10,'the control point is halfway along the chord, so the path cannot overshoot an endpoint');
  assert.ok(bend>0&&bend<=28+1e-8&&bend<=span*.08+1e-8,'the perpendicular bend is bounded and follows transfer direction');
  const rendered=f.env.FundsGraphRouteQuality.sample(item.getKeyShape().attr('path'),{steps:24});
  assert.ok(rendered.valid&&!rendered.truncated);
  assert.ok(rendered.length<=span*1.005,'an unobstructed automatic curve has no detour or hook');
}

test('clean graph interaction: automatic arcs remain continuous across the former 50-pixel branch threshold',()=>{
  const f=cleanLabelScene();f.render();
  const controls=[];
  for(const y of [109.999,110,110.001]){
    f.state.graph.updateItem(f.state.graph.findById('A'),{x:300,y});
    f.h.run('refreshGraphRoutesAndLabels()');
    const item=f.edge('U=>A');assert.equal(item.model.curvePolicy,'compact-arc');assertCompactRoute(f,item);
    controls.push(plainGraph(item.model.controlPoints[0]));
  }
  for(let i=1;i<controls.length;i++)assert.ok(Math.hypot(controls[i].x-controls[i-1].x,controls[i].y-controls[i-1].y)<.002,
    'a subpixel node move cannot flip the bend to the other side or create a route jump');
  assert.equal(JSON.stringify(f.state.lastGraph),f.facts);
});

test('clean graph interaction: opposing automatic transfers retain separate short arcs and their actual direction',()=>{
  const f=cleanLabelScene();
  f.state.lastGraph.edges.push({source:'A',target:'U',cnt:1,ids:[5],amount_totals:[{currency:'USD',amount:'3500.00'}]});
  f.state.lastGraph.tracked_ids.push(5);const facts=JSON.stringify(f.state.lastGraph);f.render();
  const forward=f.edge('U=>A'),reverse=f.edge('A=>U');
  for(const item of [forward,reverse]){assert.equal(item.model.curvePolicy,'compact-arc');assertCompactRoute(f,item);}
  assert.deepEqual(plainGraph(forward.model.controlPoints),[{x:185.6,y:150}]);
  assert.deepEqual(plainGraph(reverse.model.controlPoints),[{x:214.4,y:150}]);
  assert.equal(forward.model.target,'A');assert.equal(reverse.model.target,'U');
  assert.ok(forward.getKeyShape().getPoint(.5).x<200&&reverse.getKeyShape().getPoint(.5).x>200,
    'reverse transfers do not collapse into one ambiguous path');
  assert.equal(JSON.stringify(f.state.lastGraph),facts,'path separation never combines transfer facts or currencies');
  assert.deepEqual(plainGraph(f.state.edgeTypes),{});assert.deepEqual(plainGraph(f.state.edgeBendPos),{});
});

test('clean graph interaction: simple horizontal labels sit above while branch labels stay at real curve midpoints',()=>{
  const f=cleanLabelScene();f.render();
  for(const item of f.state.graph.getEdges()){
    const text=f.text(item),anchor=item.getKeyShape().getPoint(.5),box=text.getBBox();
    assert.equal((box.minX+box.maxX)/2,anchor.x);
    assert.equal(f.shape(item,'text-bg-shape'),undefined);assert.equal(f.shape(item,'funds-label-leader'),undefined);
    assert.equal(text.attr('stroke'),null);assert.equal(text.attr('lineWidth'),0);
    if(item.model.source==='A'&&item.model.target==='B'){
      assert.equal(item.model.labelPlacement,'above');
      assert.equal(box.maxY,anchor.y-item.model.style.lineWidth/2-4);
      assert.equal(f.shape(item,'funds-edge-visible-path'),undefined);
    }else{
      assert.equal(item.model.labelPlacement,'inline');
      assert.equal((box.minY+box.maxY)/2,anchor.y);
      assert.ok(f.shape(item,'funds-edge-visible-path').getClip());
    }
  }
});

test('clean graph interaction: crossing forest labels retain path ownership despite collisions and filtered views',()=>{
  const f=cleanLabelScene({crossing:true});f.render();
  assert.equal(f.h.run('graphHasSimpleGeometry()'),false);
  for(const item of f.state.graph.getEdges()){
    const box=f.text(item).getBBox(),anchor=item.getKeyShape().getPoint(.5);
    assert.equal(item.model.labelPlacement,'inline');
    assert.equal((box.minX+box.maxX)/2,anchor.x);assert.equal((box.minY+box.maxY)/2,anchor.y);
    assert.equal(f.text(item)._fundsLabelOffset,undefined,'edge labels never acquire node collision offsets');
    assert.equal(item.model.curvePolicy,'compact-arc');assertCompactRoute(f,item);assert.equal(f.text(item).visible,true);
    assert.ok(f.shape(item,'funds-edge-visible-path').getClip());
  }
  assert.equal(f.state.graphView.policy.edgeKeys.size,2,'colliding amounts are retained according to label policy');
  assert.equal(f.state.graphView.policy.edgePlacements.size,0,'no free-space displacement is generated for relationship text');
  f.state.graphView.visibleIds=new Set(['A','B']);f.render();
  assert.equal(f.state.graph.getEdges().length,1);
  assert.equal(f.h.run('graphHasSimpleGeometry()'),false,'filtering cannot reclassify the complete crossing graph');
  assert.equal(f.edge('A=>B').model.labelPlacement,'inline','the retained horizontal relationship stays on its own path');
  assert.equal(JSON.stringify(f.state.lastGraph),f.facts);
});

test('clean graph interaction: manual line choices and bends survive automatic route refresh',()=>{
  const f=cleanLabelScene();
  f.state.edgeTypes={'A=>B':'line'};f.state.edgeBendPos={'U=>A':[{x:140,y:150}]};
  f.render();f.h.run('refreshGraphRoutesAndLabels()');
  assert.equal(f.edge('A=>B').model.type,'line');assert.equal(f.edge('A=>B').model.curvePolicy,null);
  assert.deepEqual(plainGraph(f.edge('A=>B').model.controlPoints),[]);
  assert.deepEqual(plainGraph(f.edge('U=>A').model.controlPoints),[{x:140,y:150}]);
  assert.equal(f.edge('U=>A').model.curvePolicy,null);
  assert.equal(f.edge('A=>D').model.curvePolicy,'compact-arc');
  assert.deepEqual(plainGraph(f.state.edgeTypes),{'A=>B':'line'});
  assert.deepEqual(plainGraph(f.state.edgeBendPos),{'U=>A':[{x:140,y:150}]});
  assert.equal(JSON.stringify(f.state.lastGraph),f.facts);
});

test('clean graph interaction: automatic route snapshots reopen as dynamic geometry without polluting manual tables',async()=>{
  const f=cleanLabelScene();f.state.edgeTypes={'A=>B':'line'};f.state.edgeBendPos={'U=>A':[{x:140,y:150}]};f.render();
  const snapshot=await f.h.run('window.__collectSnapshot({scope:"all",embedPhotos:false})');
  assert.equal(snapshot.edges.find(edge=>edge.source==='A'&&edge.target==='D').curvePolicy,'compact-arc');
  assert.equal(snapshot.edges[0].data.amount_totals[0].amount,'10000000000000000.01');
  f.env.cleanSnapshot=snapshot;f.h.run('applyCanvasSnapshot(cleanSnapshot)');
  assert.deepEqual(Object.keys(f.state.edgeTypes).sort(),['A=>B','U=>A']);
  assert.deepEqual(plainGraph(f.state.edgeBendPos),{'U=>A':[{x:140,y:150}]});
  const old=plainGraph(f.edge('A=>D').model.controlPoints);
  f.state.graph.updateItem(f.state.graph.findById('A'),{x:230,y:260});f.h.run('refreshGraphRoutesAndLabels()');
  assert.notDeepEqual(plainGraph(f.edge('A=>D').model.controlPoints),old);
  assert.deepEqual(plainGraph(f.edge('A=>D').model.controlPoints),[{x:199.8,y:352.6}]);
  assert.deepEqual(plainGraph(f.edge('U=>A').model.controlPoints),[{x:140,y:150}]);
  assert.equal(JSON.stringify(f.state.lastGraph),f.facts);
});

for(const manual of [false,undefined,true])test(`clean graph interaction: legacy screen-right snapshot respects routeManual=${String(manual)}`,async()=>{
  const f=cleanLabelScene();f.render();
  const snapshot=await f.h.run('window.__collectSnapshot({scope:"all",embedPhotos:false})');
  const saved=snapshot.edges.find(edge=>edge.source==='A'&&edge.target==='D');
  saved.curvePolicy='screen-right';saved.controlPoints=[{x:347,y:282}];
  if(manual===undefined)delete saved.routeManual;else saved.routeManual=manual;
  f.env.legacyRouteSnapshot=snapshot;f.h.run('applyCanvasSnapshot(legacyRouteSnapshot)');
  const item=f.edge('A=>D');
  if(manual===true){
    assert.equal(item.model.curvePolicy,null,'an explicit manual marker outranks the obsolete automatic policy');
    assert.deepEqual(plainGraph(item.model.controlPoints),[{x:347,y:282}]);
    assert.equal(f.state.edgeTypes['A=>D'],'quadratic');
    assert.deepEqual(plainGraph(f.state.edgeBendPos['A=>D']),[{x:347,y:282}]);
    f.state.graph.updateItem(f.state.graph.findById('A'),{x:230,y:260});f.h.run('refreshGraphRoutesAndLabels()');
    assert.deepEqual(plainGraph(item.model.controlPoints),[{x:347,y:282}],'node motion cannot reinterpret or replace a manual bend');
  }else{
    assert.equal(item.model.curvePolicy,'compact-arc');assertCompactRoute(f,item);
    assert.deepEqual(plainGraph(item.model.controlPoints),[{x:183.2,y:345}],'legacy detours migrate to current short automatic geometry');
    assert.equal(Object.hasOwn(f.state.edgeTypes,'A=>D'),false);assert.equal(Object.hasOwn(f.state.edgeBendPos,'A=>D'),false);
    assert.equal(f.state.graphView.autoRoutes['A=>D'].curvePolicy,'compact-arc','the persisted automatic recipe is migrated too');
  }
  const reopened=await f.h.run('window.__collectSnapshot({scope:"all",embedPhotos:false})');
  const persisted=reopened.edges.find(edge=>edge.source==='A'&&edge.target==='D');
  assert.equal(persisted.routeManual,manual===true);
  assert.equal(persisted.curvePolicy,manual===true?null:'compact-arc');
  assert.equal(JSON.stringify(f.state.lastGraph),f.facts);
});

test('clean graph interaction: label visibility follows focus/all policy while collisions never hide requested amounts',()=>{
  const f=cleanLabelScene({crossing:true});f.render();
  f.state.graphView.labelMode='focus';f.applyLabels(false);
  assert.equal(f.state.graphView.policy.edgeKeys.size,0);
  assert.equal(f.state.graph.getEdges().every(item=>item.model.label===undefined),true);
  assert.equal(f.state.graph.getEdges().every(item=>!f.shape(item,'funds-edge-visible-path')),true,'hidden labels restore continuous keys');
  f.state.graphView.selectedEdge='A=>B';f.applyLabels(false);
  assert.equal(f.edge('A=>B').model.label!==undefined,true);assert.ok(f.text(f.edge('A=>B')));
  assert.equal(f.edge('U=>D').model.label,undefined);
  f.state.graphView.labelMode='all';f.applyLabels(false);
  assert.equal(f.state.graphView.policy.edgeKeys.size,2);
  assert.equal(f.state.graph.getEdges().every(item=>item.model.label!==undefined&&f.text(item).visible),true);
  assert.equal(JSON.stringify(f.state.lastGraph),f.facts);
});

test('clean graph interaction: large tracked graphs reject simple mode before reading every node coordinate',()=>{
  const f=cleanLabelScene();
  f.state.lastGraph={nodes:Array.from({length:1000},(_,i)=>({account:`SYN-LARGE-${i}`})),edges:[]};
  let lookups=0;const find=f.state.graph.findById.bind(f.state.graph);
  f.state.graph.findById=id=>{lookups++;return find(id)};
  assert.equal(f.h.run('graphHasSimpleGeometry()'),false);
  assert.equal(lookups,0,'the known size bound prevents per-edge scans of all tracked coordinates');
});

test('clean graph interaction: complex automatic snapshots reopen unfrozen and retain compact arcs after becoming simple',async()=>{
  const f=cleanLabelScene(),simplePositions=plainGraph(f.state.positions);
  f.state.positions={A:{x:100,y:240},B:{x:700,y:240},U:{x:400,y:50},D:{x:400,y:450},C:{x:100,y:50}};
  f.state.edgeTypes={'A=>B':'line'};f.state.edgeBendPos={'U=>A':[{x:140,y:150}]};f.render();
  assert.equal(f.h.run('graphHasSimpleGeometry()'),false);
  assert.equal(f.edge('A=>D').model.curvePolicy,'compact-arc');assertCompactRoute(f,f.edge('A=>D'));
  const snapshot=await f.h.run('window.__collectSnapshot({scope:"all",embedPhotos:false})');
  const automatic=snapshot.edges.find(edge=>edge.source==='A'&&edge.target==='D');
  assert.equal(automatic.curvePolicy,'compact-arc');assert.equal(automatic.routeManual,false);
  assert.equal(snapshot.edges.find(edge=>edge.source==='A'&&edge.target==='B').routeManual,true);
  assert.equal(snapshot.edges.find(edge=>edge.source==='U'&&edge.target==='A').routeManual,true);
  f.env.complexCleanSnapshot=snapshot;f.h.run('applyCanvasSnapshot(complexCleanSnapshot)');
  assert.deepEqual(Object.keys(f.state.edgeTypes).sort(),['A=>B','U=>A'],'automatic complex routes do not become explicit manual choices');
  assert.deepEqual(plainGraph(f.state.edgeBendPos),{'U=>A':[{x:140,y:150}]});
  for(const [id,position] of Object.entries(simplePositions))f.state.graph.updateItem(f.state.graph.findById(id),position);
  f.h.run('refreshGraphRoutesAndLabels()');
  assert.equal(f.h.run('graphHasSimpleGeometry()'),true);
  assert.equal(f.edge('A=>D').model.curvePolicy,'compact-arc');
  assert.deepEqual(plainGraph(f.edge('A=>D').model.controlPoints),[{x:183.2,y:345}]);
  assert.equal(f.edge('A=>B').model.type,'line');assert.equal(f.edge('A=>B').model.curvePolicy,null);
  assert.deepEqual(plainGraph(f.edge('U=>A').model.controlPoints),[{x:140,y:150}]);
  assert.equal(f.edge('U=>A').model.curvePolicy,null);
  assert.equal(JSON.stringify(f.state.lastGraph),f.facts);
});

for(const kind of ['bend','label'])for(const terminal of ['released-button','blur','window-exit']){
  test(`graph gesture contract: ${terminal} completes a ${kind} drag and rejects unrelated late moves`,()=>{
    const f=graphInteractions(),graph=f.seed();
    f.state.edgeBendPos={'SYN-A=>SYN-B':[{x:100,y:100}]};
    const event={item:graph.getEdges()[0],clientX:100,clientY:100,button:0,preventDefault(){}};
    if(kind==='label')event.target={get:name=>name==='name'?'text-shape':undefined};
    const initial=JSON.stringify(kind==='bend'?f.state.edgeBendPos:f.state.edgeLabelPos);
    graph.emit('edge:mousedown',event);
    f.event('mousemove',{clientX:140,clientY:150,buttons:1});
    const geometry=()=>JSON.stringify(kind==='bend'?f.state.edgeBendPos:f.state.edgeLabelPos);
    const committed=geometry();
    assert.notEqual(committed,initial,'the pressed move creates real geometry');
    if(terminal==='released-button')f.event('mousemove',{clientX:170,clientY:180,buttons:0});
    else if(terminal==='blur')f.event('blur');
    else f.event('mouseout',{relatedTarget:null});
    assert.equal(geometry(),committed,'termination retains the last legitimate pressed position');
    f.event('mousemove',{clientX:20,clientY:30,buttons:0});
    f.event('mousemove',{clientX:190,clientY:180,buttons:1});
    f.event('mouseup',{clientX:190,clientY:180});
    assert.equal(geometry(),committed,'old edge ownership cannot follow hover or a later unrelated press');
  });
}

for(const kind of ['bend','label'])for(const button of [1,2])test(`graph gesture contract: button ${button} cannot begin a ${kind} geometry edit`,()=>{
  const f=graphInteractions(),graph=f.seed();let starts=0;
  f.env.window.addEventListener('funds:edge-drag-start',()=>{starts++});
  f.state.edgeBendPos={'SYN-A=>SYN-B':[{x:100,y:100}]};
  const initial=JSON.stringify([f.state.edgeBendPos,f.state.edgeLabelPos]);
  graph.emit('edge:mousedown',{item:graph.getEdges()[0],clientX:100,clientY:100,button,
    target:kind==='label'?{get:name=>name==='name'?'text-shape':undefined}:undefined,preventDefault(){}});
  f.event('mousemove',{clientX:160,clientY:180,buttons:button===1?4:2});f.event('mouseup');
  assert.equal(starts,0,'secondary buttons must not reserve a history gesture');
  assert.equal(JSON.stringify([f.state.edgeBendPos,f.state.edgeLabelPos]),initial,'context/pan actions cannot edit geometry');
});

const contractTopologies={
  branch:{ids:'ABCDEFGHIJK',pairs:['AB','BC','CF','DA','EB','GF','AH','IB','JC','FK']},
  cycle:{ids:'ABCDEFG',pairs:['AB','BC','CA','CD','DE','EF','EG']},
  hub:{ids:'ABCDEFGHI',pairs:['AB','AC','AD','AE','AF','AG','AH','AI']},
  disconnected:{ids:'ABCDEFGH',pairs:['AB','BC','DE','EF','FD'],coincident:true},
  mesh:{ids:'ABCDEFGHI',pairs:['AD','AE','AF','BD','BE','BF','CD','CE','CF','FG','GH','HI','IG']},
};

function contractScene(topology,options={}){
  const f=graphInteractions({...options,selection:true,geometry:true}),savedLoad=f.env.loadGraph;
  let camera=[.8,0,0,0,.8,0,37,-51,1],requests=0,routeRequests=0;
  const attachCamera=graph=>{
    const get=graph.get.bind(graph),group={getMatrix:()=>camera,setMatrix:matrix=>{camera=[...matrix]}};
    graph.get=name=>name==='group'?group:get(name);
    graph.getZoom=()=>camera[0];graph.zoomTo=zoom=>{camera[0]=camera[4]=zoom;graph.emit('viewportchange',{action:'zoom'})};
    graph.translate=(x,y)=>{camera[6]+=x;camera[7]+=y};
    graph.getCanvasByPoint=(x,y)=>({x:x*camera[0]+camera[6],y:y*camera[4]+camera[7]});
    graph.getPointByCanvas=graph.getPointByClient=(x,y)=>({x:(x-camera[6])/camera[0],y:(y-camera[7])/camera[4]});
    for(const node of graph.getNodes())node.getEdges=()=>graph.getEdges().filter(edge=>edge.model.source===node.model.id||edge.model.target===node.model.id);
  };
  const n=(account,i)=>({account,name:i===2?'合成复杂长中文名称验证完整标签不能被布局丢弃':'合成 '+account,in_cnt:1,out_cnt:1});
  const e=(source,target,id)=>({source,target,ids:[id],cnt:1,total:id===1?'9999999999999999.99':'0.01',
    currencies:['CNY'],amount_totals:[{currency:'CNY',amount:id===1?'9999999999999999.99':'0.01'}]});
  const base={nodes:[...topology.ids].map(n),edges:topology.pairs.map(([a,b],i)=>e(a,b,i+1)),tracked_ids:topology.pairs.map((_,i)=>i+1)};
  const expanded={nodes:[...base.nodes,n('SYN-NEW-1'),n('SYN-NEW-2')],
    edges:[...base.edges,e('A','SYN-NEW-1',90),e('SYN-NEW-2','SYN-NEW-1',91)],tracked_ids:[...base.tracked_ids,90,91]};
  let ids=[...base.tracked_ids];
  f.state.lastGraph=plainGraph(base);f.state.tracked=new Set(ids);f.state.forceFit=false;f.state.edgeType='quadratic';f.state.graphView.labelMode='all';
  f.state.positions=Object.fromEntries([...topology.ids].map((id,i)=>[id,topology.coincident?{x:0,y:0}:{x:100+i%4*190,y:100+Math.floor(i/4)*170}]));
  f.h.run('renderGraph();savePositions()');attachCamera(f.state.graph);
  f.h.bindApp();f.env.loadGraph=savedLoad;f.env.fitGraph=f.fitGraph;f.env.applyGraphViewport=()=>{};
  f.env.document.querySelectorAll=selector=>selector.split(',').flatMap(part=>/^#[\w-]+$/.test(part)?[f.h.element(part)]:[]);
  f.env.Worker=class{
    postMessage(message){if(message.task==='layout')requests++;else routeRequests++;
      this.onmessage({data:workerReply(f.env,message)})}terminate(){}
  };
  f.h.network((path,options)=>{
    if(path==='/api/track/state'){ids=JSON.parse(options.body).desired_ids;return response({ok:true,tracked_ids:ids})}
    if(path.startsWith('/api/graph?'))return response(ids.includes(90)?expanded:base);
    if(path.startsWith('/api/flows?'))return response({page:1,size:50,total:0,rows:[]});
    throw new Error(`Unexpected contract fixture request ${path}`);
  });
  f.h.run(readFileSync(new URL('../static/canvas-history.js',import.meta.url),'utf8'));
  f.h.run(readFileSync(new URL('../static/canvas-editor.js',import.meta.url),'utf8'));f.event('DOMContentLoaded');
  const drain=async promise=>{
    let done=false,error;Promise.resolve(promise).then(()=>{done=true},reason=>{done=true;error=reason});
    for(let pass=0;pass<100&&!done;pass++){
      await new Promise(resolve=>setImmediate(resolve));
      for(const timer of f.h.timers.filter(timer=>!timer.ran&&!timer.cancelled&&timer.delay<=100)){timer.ran=true;timer.callback()}
    }
    assert.equal(done,true,'the public command completes without a stuck layout or gesture');if(error)throw error;return promise;
  };
  const coords=()=>plainGraph(Object.fromEntries(f.state.graph.getNodes().map(item=>[item.model.id,{x:item.model.x,y:item.model.y}])));
  const anchored=()=>{f.applyLabels();for(const item of f.state.graph.getNodes()){
    const text=item.getContainer().children.find(shape=>shape.name==='text-shape');assert.ok(text,`${item.model.id} retains its full label`);
    const box=text.getCanvasBBox(),body=item.getKeyShape().getCanvasBBox();
    assert.ok(Math.abs((box.minX+box.maxX-body.minX-body.maxX)/2)<1e-7,`${item.model.id} is centered`);
    assert.ok(Math.abs(box.minY-body.maxY-6)<1e-7,`${item.model.id} keeps a 6 CSS pixel lower gap`);
    assert.equal(item.getContainer().children.some(shape=>['text-bg-shape','funds-label-leader'].includes(shape.name)),false);
  }
    for(const item of f.state.graph.getEdges()){
      const children=item.getContainer().children,text=children.find(shape=>shape.name==='text-shape');
      assert.ok(text,'all-label mode retains the full relationship amount');
      assert.equal(children.some(shape=>['text-bg-shape','funds-label-leader'].includes(shape.name)),false);
      const box=text.getBBox(),anchor=item.getKeyShape().getPoint(.5);
      assert.ok(Math.abs((box.minX+box.maxX)/2-anchor.x)<1e-7,'relationship labels stay centered on their own logical paths');
      if(item.model.labelPlacement==='inline'){
        assert.ok(Math.abs((box.minY+box.maxY)/2-anchor.y)<1e-7,'complex labels stay in their own path middles');
        assert.ok(children.find(shape=>shape.name==='funds-edge-visible-path')?.getClip(),'inline text retains a real visible-path gap');
      }else assert.ok(box.maxY<anchor.y,'simple horizontal text remains above the line');
      if(item.model.curvePolicy==='compact-arc')assertCompactRoute(f,item);
    }
  };
  return{...f,base,expanded,attachCamera,drain,coords,anchored,get camera(){return [...camera]},get layoutRequests(){return requests},get routeRequests(){return routeRequests},
    history:f.env.window.FundsCanvasEditor.history};
}

for(const kind of ['auto','dagre','partition','structural','force'])for(const [name,topology]of Object.entries(contractTopologies)){
  test(`graph contract matrix: ${kind}/${name} layout -> microdrag -> zoom/fit -> append -> history -> snapshot`,async()=>{
    const f=contractScene(topology),facts=JSON.stringify(f.state.lastGraph);
    f.h.element('#layoutSel').value=kind;
    await f.drain(f.h.element('#layoutSel').onchange({target:f.h.element('#layoutSel')}));
    assert.equal(f.state.layout,kind);
    if(kind==='auto')assert.ok(f.layoutRequests>=1&&f.layoutRequests<=2,'automatic layout compares at most two existing methods');
    else assert.equal(f.layoutRequests,1,'an explicit method runs only its selected layout');
    const arrangedRequests=f.layoutRequests;assert.equal(f.history.status().count,1);
    const arranged=f.coords();assert.equal(Object.keys(arranged).length,topology.ids.length);
    assert.ok(Object.values(arranged).every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)));f.anchored();
    const graph=f.state.graph,point=graph.getCanvasByPoint(arranged.A.x,arranged.A.y),zoom=graph.getZoom();
    graph.emit('node:mousedown',{item:graph.findById('A'),clientX:point.x,clientY:point.y,button:0,preventDefault(){}});
    f.event('mousemove',{clientX:point.x+6,clientY:point.y+2,buttons:1,preventDefault(){}});
    graph.emit('node:click',{item:graph.findById('A')});
    f.event('mouseup',{clientX:point.x+6,clientY:point.y+2,preventDefault(){}});
    const moved=f.coords();assert.ok(Math.abs(moved.A.x-arranged.A.x-6/zoom)<1e-7);assert.ok(Math.abs(moved.A.y-arranged.A.y-2/zoom)<1e-7);
    assert.equal(f.history.status().count,2,'the native microdrag survives G6 click-before-mouseup and creates one history entry');
    for(const scale of [.35,2.4]){graph.zoomTo(scale);f.anchored();f.h.element('#btnFit').onclick();f.anchored();
      const first=f.camera;f.h.element('#btnFit').onclick();
      for(let i=0;i<9;i++)assert.ok(Math.abs(f.camera[i]-first[i])<1e-7,'repeated fit is camera-idempotent');
      assert.deepEqual(f.coords(),moved,'view operations never rewrite manual coordinates');
    }
    await f.drain(f.h.element('#btnUndo').onclick());assert.deepEqual(f.coords(),arranged);
    await f.drain(f.h.element('#btnRedo').onclick());assert.deepEqual(f.coords(),moved);f.anchored();
    assert.equal(JSON.stringify(f.state.lastGraph),facts,'layout, drag and history preserve every business fact');
    const camera=f.camera;await f.drain(f.h.run('trackFlows([90,91])'));f.attachCamera(f.state.graph);f.anchored();
    assert.deepEqual(f.camera,camera,'append does not fit or shrink the camera');
    const appended=f.coords();for(const [id,p]of Object.entries(moved))assert.deepEqual(appended[id],p,'append freezes existing coordinates');
    assert.equal(f.history.status().count,3);assert.equal(f.layoutRequests,arrangedRequests,'append does not execute a global layout');
    await f.drain(f.h.element('#btnUndo').onclick());f.attachCamera(f.state.graph);assert.deepEqual(f.coords(),moved);
    await f.drain(f.h.element('#btnRedo').onclick());f.attachCamera(f.state.graph);assert.deepEqual(f.coords(),appended);f.anchored();
    const snapshot=await f.h.run('window.__collectSnapshot({scope:"all",embedPhotos:false})');
    assert.equal(snapshot.display.layout,kind);assert.equal(snapshot.nodes.length,topology.ids.length+2);
    assert.equal(snapshot.edges[0].data.amount_totals[0].amount,'9999999999999999.99');
    const saved=Object.fromEntries(snapshot.nodes.map(node=>[node.id,{x:node.x,y:node.y}]));
    for(const [id,p]of Object.entries(saved))for(const axis of ['x','y'])
      assert.ok(Math.abs(p[axis]-appended[id][axis])<=.005000001,'the established two-decimal snapshot contract bounds geometry rounding');
    const completeFacts=JSON.stringify(f.state.lastGraph);f.env.contractSnapshot=snapshot;f.h.run('applyCanvasSnapshot(contractSnapshot)');
    f.attachCamera(f.state.graph);f.anchored();assert.deepEqual(f.coords(),saved);assert.equal(f.state.layout,kind);
    assert.equal(JSON.stringify(f.state.lastGraph),completeFacts);assert.equal(f.layoutRequests,arrangedRequests,'reopen retains coordinates instead of recomputing them');
  });
}

for(const kind of ['bend','label'])for(const terminal of ['released-button','blur']){
  test(`graph gesture history: ${kind}/${terminal} commits once, unlocks commands and restores through undo/redo`,async()=>{
    const f=contractScene(contractTopologies.cycle),graph=f.state.graph,edge=graph.getEdges()[0],key='A=>B';
    f.state.edgeTypes[key]='polyline';graph.updateItem(edge,{type:'polyline'});
    f.state.edgeBendPos[key]=[{x:195,y:100}];
    const coordinates=f.coords(),initial=plainGraph({bends:f.state.edgeBendPos,ratios:f.state.edgeLabelPos});
    const anchor=graph.getCanvasByPoint(195,100),event={item:edge,clientX:anchor.x,clientY:anchor.y,button:0,preventDefault(){}};
    if(kind==='label')event.target={get:name=>name==='name'?'text-shape':undefined};
    graph.emit('edge:mousedown',event);f.event('mousemove',{clientX:anchor.x+14,clientY:anchor.y+11,buttons:1});
    if(terminal==='released-button')f.event('mousemove',{clientX:anchor.x+40,clientY:anchor.y+50,buttons:0});else f.event('blur');
    await Promise.resolve();
    const completed=plainGraph({bends:f.state.edgeBendPos,ratios:f.state.edgeLabelPos});
    assert.notDeepEqual(completed,initial);assert.deepEqual(f.coords(),coordinates);
    assert.equal(f.history.status().count,1,'delivery loss closes the edge history gesture without waiting for a missing mouseup');
    f.event('mouseup');await Promise.resolve();assert.equal(f.history.status().count,1,'late mouseup does not duplicate history');
    await f.drain(f.h.element('#btnUndo').onclick());
    assert.deepEqual(plainGraph({bends:f.state.edgeBendPos,ratios:f.state.edgeLabelPos}),initial,'undo is available immediately after delivery loss');
    await f.drain(f.h.element('#btnRedo').onclick());
    assert.deepEqual(plainGraph({bends:f.state.edgeBendPos,ratios:f.state.edgeLabelPos}),completed);assert.deepEqual(f.coords(),coordinates);
    assert.equal(f.history.status().busy,false);
  });
}

function edgeContractScene(kind){
  const f=contractScene(contractTopologies.cycle),graph=f.state.graph,edge=graph.getEdges()[0],key='A=>B';
  f.state.edgeTypes[key]='polyline';f.state.edgeBendPos[key]=[{x:195,y:100}];
  graph.updateItem(edge,{type:'polyline',controlPoints:[{x:195,y:100}]});f.h.run('refreshGraphRoutesAndLabels();savePositions()');
  const anchor=graph.getCanvasByPoint(195,100),event={item:edge,clientX:anchor.x,clientY:anchor.y,button:0,preventDefault(){}};
  if(kind==='label')event.target={get:name=>name==='name'?'text-shape':undefined};
  const geometry=()=>plainGraph({bends:f.state.edgeBendPos,ratios:f.state.edgeLabelPos,snapshots:f.state.edgeSnapshotLabels,
    positions:f.state.positions,types:f.state.edgeTypes});
  return{...f,edge,key,anchor,start:()=>graph.emit('edge:mousedown',event),geometry,
    move:()=>f.event('mousemove',{clientX:anchor.x+14,clientY:anchor.y+11,buttons:1})};
}

for(const kind of ['bend','label'])test(`graph gesture completion: Escape cancels ${kind} geometry without history and unlocks the next edit`,async()=>{
  const f=edgeContractScene(kind),initial=f.geometry();f.start();f.move();assert.notDeepEqual(f.geometry(),initial);
  f.event('keydown',{key:'Escape',target:f.h.element('#graphContainer'),preventDefault(){},stopImmediatePropagation(){}});
  await Promise.resolve();assert.deepEqual(f.geometry(),initial,'explicit cancel restores bends, ratios and snapshot geometry');
  assert.equal(f.history.status().count,0,'a cancelled gesture contributes no undo entry');
  f.event('mousemove',{clientX:200,clientY:200,buttons:1});f.event('mouseup');await Promise.resolve();
  assert.deepEqual(f.geometry(),initial,'late events cannot revive the cancelled edit');
  f.start();f.move();f.event('mouseup');await Promise.resolve();
  assert.equal(f.history.status().count,1,'the next legitimate edit commits immediately');
  await f.drain(f.h.element('#btnUndo').onclick());assert.deepEqual(f.geometry(),initial);
});

for(const kind of ['bend','label'])test(`graph gesture completion: hidden document finishes ${kind} once and rejects later hover geometry`,async()=>{
  const f=edgeContractScene(kind);f.start();f.move();const moving=f.geometry();
  f.env.document.hidden=true;f.documentEvent('visibilitychange');await Promise.resolve();
  const completed=f.geometry(),{snapshots:movingSnapshots,...movingGeometry}=moving,{snapshots:committedSnapshots,...committedGeometry}=completed;
  assert.deepEqual(committedGeometry,movingGeometry,'completion may cache text coordinates but cannot change the completed edit');
  if(kind==='label')assert.equal(committedSnapshots[f.key].manual,true,'completion persists the manual ratio geometry');
  assert.equal(f.history.status().count,1);
  f.env.document.hidden=false;f.documentEvent('visibilitychange');
  f.event('mousemove',{clientX:20,clientY:30,buttons:1});f.event('mouseup');await Promise.resolve();
  assert.deepEqual(f.geometry(),completed);assert.equal(f.history.status().count,1);
  await f.drain(f.h.element('#btnUndo').onclick());assert.equal(f.history.status().canRedo,true);
});

for(const kind of ['bend','label'])for(const gate of ['busy','cancelled-start']){
  test(`graph gesture completion: ${gate} rejects ${kind} before owning geometry`,()=>{
    const f=graphInteractions(),graph=f.seed();let ends=0;
    f.env.window.addEventListener('funds:edge-drag-end',()=>{ends++});
    if(gate==='busy')f.env.window.FundsCanvasEditor={history:{status:()=>({busy:true})}};
    else f.env.window.addEventListener('funds:edge-drag-start',event=>{assert.equal(event.cancelable,true);event.preventDefault()});
    f.state.edgeBendPos={'SYN-A=>SYN-B':[{x:100,y:100}]};
    const before=JSON.stringify([f.state.edgeBendPos,f.state.edgeLabelPos]);
    graph.emit('edge:mousedown',{item:graph.getEdges()[0],clientX:100,clientY:100,button:0,preventDefault(){},
      target:kind==='label'?{get:name=>name==='name'?'text-shape':undefined}:undefined});
    f.event('mousemove',{clientX:160,clientY:180,buttons:1});f.event('mouseup');
    assert.equal(JSON.stringify([f.state.edgeBendPos,f.state.edgeLabelPos]),before);assert.equal(ends,0);
  });
}

for(const kind of ['bend','label'])for(const transition of ['dispose','case-return']){
  test(`graph gesture completion: ${transition} revokes ${kind} late events even when IDs are reused`,()=>{
    const f=graphInteractions(),old=f.seed();f.state.edgeBendPos={'SYN-A=>SYN-B':[{x:100,y:100}]};
    const event={item:old.getEdges()[0],clientX:100,clientY:100,button:0,preventDefault(){}};
    if(kind==='label')event.target={get:name=>name==='name'?'text-shape':undefined};
    old.emit('edge:mousedown',event);f.event('mousemove',{clientX:140,clientY:150,buttons:1});
    if(transition==='dispose'){f.h.run('initGraph()');f.seed()}else f.h.run('selectCase(2);selectCase(1)');
    f.state.edgeBendPos={'SYN-A=>SYN-B':[{x:30,y:40}]};f.state.edgeLabelPos={'SYN-A=>SYN-B':.23};
    const before=JSON.stringify([f.state.edgeBendPos,f.state.edgeLabelPos]);
    f.event('mousemove',{clientX:160,clientY:180,buttons:1});f.event('blur');
    f.event('keydown',{key:'Escape'});f.event('mouseup');
    assert.equal(JSON.stringify([f.state.edgeBendPos,f.state.edgeLabelPos]),before,'old completion or rollback cannot touch the new context');
  });
}

for(const kind of ['bend','label'])for(const click of ['edge','canvas']){
  test(`graph gesture completion: early G6 ${click} click does not discard ${kind} ownership or history`,async()=>{
    const f=edgeContractScene(kind);f.start();f.move();
    f.state.graph.emit(`${click}:click`,{item:click==='edge'?f.edge:undefined});
    const moving=f.geometry();f.event('mouseup');await Promise.resolve();
    const {snapshots:movingSnapshots,...movingGeometry}=moving,{snapshots:committedSnapshots,...committedGeometry}=f.geometry();
    assert.deepEqual(committedGeometry,movingGeometry);assert.equal(f.history.status().count,1);
    await f.drain(f.h.element('#btnUndo').onclick());assert.equal(f.history.status().canRedo,true);
  });
}

test('force layout worker contract: more than eighty nodes retain multiline width, height and bottom offset',async()=>{
  const f=graphInteractions({geometry:true}),workers=[];
  f.env.Worker=class{postMessage(message){workers.push(message)}terminate(){}};
  const nodes=Array.from({length:81},(_,i)=>({account:`SYN-FORCE-${i}`,name:i===80?'完整多行中文长名称用于验证力导向大图不得只按节点圆圈计算':'合成节点',in_cnt:1,out_cnt:1}));
  f.state.lastGraph={nodes,edges:nodes.slice(1).map((n,i)=>({source:nodes[0].account,target:n.account,ids:[i+1],cnt:1,total:'0.01',currencies:['CNY']})),tracked_ids:nodes.slice(1).map((_,i)=>i+1)};
  f.state.positions=Object.fromEntries(nodes.map((node,i)=>[node.account,{x:100+i%9*120,y:100+Math.floor(i/9)*100}]));
  f.state.forceFit=false;f.state.layout='force';f.h.run('renderGraph()');await f.h.run('relayout(false)');
  assert.equal(workers.length,1);const input=workers[0].input,node=input.nodes.find(node=>node.id==='SYN-FORCE-80');
  assert.equal(input.kind,'force');assert.equal(input.nodes.length,81);
  assert.ok(node.width>node.size*2,'complete wrapped text width enters the large-force worker');
  assert.ok(node.height>node.size+40,'all name and account lines enter the measured footprint');
  assert.ok(node.offsetY>20,'the footprint includes the below-node text offset');
  assert.ok(input.edges.every(edge=>edge.labelWidth>0&&edge.labelHeight>0),'relationship text also reaches the large-force worker');
});

function layoutFailureScene(kind='dagre',options={}) {
  const f=contractScene(options.topology||contractTopologies.branch,options),workers=[],tasks=[],kernel=f.env.FundsGraphLayout;
  f.state.layout=kind;f.h.element('#layoutSel').value=kind;
  f.env.Worker=class {
    constructor(){workers.push(this);this.terminated=false;this.tasks=[];}
    postMessage(message){this.message=message;const task={worker:this,message,onmessage:this.onmessage,onerror:this.onerror};
      this.tasks.push(task);tasks.push(task);
      if(message.task==='routes')task.onmessage({data:workerReply(f.env,message)});}
    terminate(){this.terminated=true;}
    deliver(result,overrides={},task=this.tasks.at(-1)){return task.onmessage({data:workerReply(f.env,task.message,{result,...overrides})});}
    fail(){return this.tasks.at(-1).onerror({message:'synthetic worker failure'});}
  };
  const geometry=()=>plainGraph({coordinates:f.coords(),positions:f.state.positions,
    edgeLabelPos:f.state.edgeLabelPos,edgeBendPos:f.state.edgeBendPos,edgeSnapshotLabels:f.state.edgeSnapshotLabels});
  const calculate=input=>{const model=kernel.simulation(input);while(model.step()){}return model.result();};
  const begin=async()=>{const pending=f.h.element('#btnRelayout').onclick();await new Promise(resolve=>setImmediate(resolve));
    assert.equal(workers.length,1,'the public command starts one retained worker');return{pending};};
  const quiet=async()=>{for(let pass=0;pass<4;pass++)await new Promise(resolve=>setImmediate(resolve));};
  return{...f,workers,tasks,kernel,geometry,calculate,begin,quiet};
}

async function warmLayoutScene(f){
  const {pending}=await f.begin(),task=f.tasks.at(-1),result=f.calculate(task.message.input);
  task.worker.deliver(result,{},task);await f.drain(pending);
  assert.equal(f.h.run('layoutJob'),null);assert.equal(f.state.graphView.idleLayoutWorker,task.worker);
  return{worker:task.worker,task,result};
}

test('layout Worker pool: successful commits park one cleared worker and a later layout checks it out exclusively',async()=>{
  const f=layoutFailureScene(),first=await warmLayoutScene(f),worker=first.worker;
  assert.equal(worker.terminated,false);assert.equal(worker.onmessage,null,'no task response handler remains installed while idle');
  assert.notEqual(worker.onerror,first.task.onerror,'the idle owner replaces task failure handlers');
  const selector=f.h.element('#layoutSel');selector.value='structural';
  const pending=selector.onchange({target:selector});await f.quiet();
  const next=f.tasks.at(-1),before=f.geometry();
  assert.equal(f.workers.length,1);assert.equal(next.worker,worker);assert.equal(next.message.task,'layout');
  assert.equal(f.state.graphView.idleLayoutWorker,null,'a computing task cannot also be owned by the idle pool');
  assert.equal(f.h.run('layoutJob.worker'),worker);assert.notEqual(next.message.requestId,first.task.message.requestId);
  // Model both a queued old callback and an old packet reaching the newly
  // installed callback. Neither can terminate or complete the active task.
  first.task.onmessage({data:workerReply(f.env,first.task.message,{result:first.result})});
  next.onmessage({data:workerReply(f.env,first.task.message,{result:first.result})});await f.quiet();
  assert.deepEqual(f.geometry(),before);assert.equal(f.history.status().busy,true);assert.equal(worker.terminated,false);
  assert.equal(f.tasks.length,2,'old replies cannot start another computation');
  worker.deliver(f.calculate(next.message.input),{},next);await f.drain(pending);
  assert.equal(f.state.graphView.idleLayoutWorker,worker);assert.equal(worker.onmessage,null);
  assert.equal(worker.terminated,false);assert.equal(f.history.status().busy,false);
});

test('layout Worker pool: cancelling a checked-out worker retires it and a later command cannot reuse it',async()=>{
  const f=layoutFailureScene(),first=await warmLayoutScene(f),selector=f.h.element('#layoutSel');selector.value='structural';
  const cancelled=selector.onchange({target:selector});await f.quiet();const old=f.tasks.at(-1),before=f.geometry();
  assert.equal(old.worker,first.worker);f.h.run('cancelGraphLayout()');await f.drain(cancelled);
  assert.equal(first.worker.terminated,true);assert.equal(f.state.graphView.idleLayoutWorker,null);
  assert.deepEqual(f.geometry(),before);assert.equal(f.history.status().busy,false);
  const count=f.history.status().count;selector.value='force';const pending=selector.onchange({target:selector});await f.quiet();
  const current=f.tasks.at(-1);assert.notEqual(current.worker,first.worker);assert.equal(f.workers.length,2);
  current.onmessage({data:workerReply(f.env,old.message,{result:f.calculate(old.message.input)})});await f.quiet();
  assert.deepEqual(f.geometry(),before);assert.equal(f.history.status().busy,true);assert.equal(f.history.status().count,count);
  current.worker.deliver(f.calculate(current.message.input),{},current);await f.drain(pending);
  const accepted=f.geometry();old.onmessage({data:workerReply(f.env,old.message,{result:f.calculate(old.message.input)})});await f.quiet();
  assert.deepEqual(f.geometry(),accepted);assert.equal(f.state.graphView.idleLayoutWorker,current.worker);
});

for(const release of ['reset','rebuild','error','messageerror'])test(`layout Worker pool: ${release} releases the idle worker and removes its handlers`,async()=>{
  const f=layoutFailureScene(),{worker}=await warmLayoutScene(f),view=f.state.graphView;
  if(release==='reset')f.h.run('resetGraphView()');
  else if(release==='rebuild')f.h.run('initGraph()');
  else worker[release==='error'?'onerror':'onmessageerror']({message:'synthetic idle failure'});
  assert.equal(worker.terminated,true);assert.equal(view.idleLayoutWorker,null);
  assert.equal(worker.onmessage,null);assert.equal(worker.onerror,null);assert.equal(worker.onmessageerror,null);
  assert.equal(f.h.run('layoutJob'),null,'idle disposal cannot create a new computation');
});

test('layout Worker pool: a warm worker survives the same-view single-node early return and remains releasable',async()=>{
  const f=layoutFailureScene(),{worker}=await warmLayoutScene(f),view=f.state.graphView;
  f.h.run('state.graphView.visibleIds=new Set(["A"]);renderGraph()');
  assert.equal(f.state.graph.getNodes().length,1);assert.equal(f.state.graphView,view);
  assert.equal(view.idleLayoutWorker,worker);const tasks=f.tasks.length;
  await f.h.run('relayout(false)');
  assert.equal(f.tasks.length,tasks,'one node needs no Worker task');assert.equal(f.h.run('layoutJob'),null);
  assert.equal(view.idleLayoutWorker,worker,'the early return must return its checked-out worker to its owner');
  assert.equal(worker.terminated,false);assert.equal(worker.onmessage,null);
  f.h.run('resetGraphView()');assert.equal(worker.terminated,true);assert.equal(view.idleLayoutWorker,null);
});

const invalidLayoutResults={
  null:()=>null,
  missing:nodes=>({nodes:nodes.slice(1)}),
  duplicate:nodes=>({nodes:[...nodes.slice(0,-1),{...nodes[0]}]}),
  extra:nodes=>({nodes:[...nodes,{id:'SYN-UNEXPECTED',x:1,y:2}]}),
  NaN:nodes=>({nodes:nodes.map((node,i)=>i?node:{...node,x:NaN})}),
  Infinity:nodes=>({nodes:nodes.map((node,i)=>i?node:{...node,y:Infinity})}),
  extreme:nodes=>({nodes:nodes.map((node,i)=>i?node:{...node,x:Number.MAX_VALUE,y:-Number.MAX_VALUE})}),
};

for(const [name,invalid]of Object.entries(invalidLayoutResults)) {
  test(`layout pipeline fault: ${name} worker result cannot partially mutate graph or history`,async()=>{
    const f=layoutFailureScene(),before=f.geometry(),camera=f.camera,facts=JSON.stringify(f.state.lastGraph),fallbacks=[];
    f.env.FundsGraphLayout={...f.kernel,run:async input=>{fallbacks.push(input.kind);throw new Error('synthetic retry failure');}};
    const {pending}=await f.begin(),worker=f.workers[0];
    const nodes=worker.message.input.nodes.map((node,i)=>({id:node.id,x:1000+i*250,y:150+i*130}));
    worker.deliver(invalid(nodes));await f.drain(pending);
    assert.deepEqual(fallbacks,['dagre'],'one same-kind retry handles an invalid worker result');
    assert.equal(worker.terminated,true);assert.equal(f.h.run('layoutJob'),null,'failure releases the active layout job');
    assert.deepEqual(f.geometry(),before,'rejected coordinates never enter live or persisted geometry');
    assert.deepEqual(f.camera,camera);assert.equal(f.history.status().count,0);assert.equal(f.history.status().busy,false);
    assert.equal(JSON.stringify(f.state.lastGraph),facts,'failure never changes transaction direction or exact amounts');
  });
}

for(const [name,quality]of [['missing',undefined],['null',null],['invalid',{valid:false,reason:'synthetic-invalid-quality'}],
  ['incomplete',{valid:true}],['nonfinite',{valid:true,overlaps:{count:NaN},fit:{readable:true}}]]){
  test(`layout pipeline fault: ${name} worker quality retries the same method without partial history`,async()=>{
    const f=layoutFailureScene('structural'),before=f.geometry(),camera=f.camera,facts=JSON.stringify(f.state.lastGraph),fallbacks=[];
    f.env.FundsGraphLayout={...f.kernel,run:async input=>{fallbacks.push(input.kind);throw new Error('synthetic same-kind retry failure')}};
    const {pending}=await f.begin(),worker=f.workers[0],result=f.calculate(worker.message.input);
    worker.deliver(result,{quality});await f.drain(pending);
    assert.deepEqual(fallbacks,['structural'],'malformed quality cannot silently change the selected algorithm');
    assert.equal(worker.terminated,true);assert.equal(f.h.run('layoutJob'),null);
    assert.deepEqual(f.geometry(),before);assert.deepEqual(f.camera,camera);assert.equal(JSON.stringify(f.state.lastGraph),facts);
    assert.equal(f.history.status().count,0);assert.equal(f.history.status().busy,false);
  });
}

for(const [name,change]of [['protocol',reply=>({...reply,protocol:0})],['requestId',reply=>({...reply,requestId:reply.requestId+1})],
  ['taskId',reply=>({...reply,taskId:reply.taskId+1})],['task',reply=>({...reply,task:'routes'})]]){
  test(`layout pipeline ownership: a mismatched ${name} reply cannot finish or mutate the current task`,async()=>{
    const f=layoutFailureScene('dagre'),before=f.geometry(),facts=JSON.stringify(f.state.lastGraph),{pending}=await f.begin();
    const worker=f.workers[0],result=f.calculate(worker.message.input),reply=workerReply(f.env,worker.message,{result});
    worker.onmessage({data:change(reply)});await f.quiet();
    assert.deepEqual(f.geometry(),before);assert.equal(JSON.stringify(f.state.lastGraph),facts);
    assert.equal(f.history.status().busy,true);assert.equal(f.history.status().count,0,'an unrelated reply cannot end the whole edit');
    assert.equal(worker.terminated,false,'the current task remains owned until its own reply or deadline');
    worker.deliver(result);await f.drain(pending);
    assert.equal(f.h.run('layoutJob'),null);assert.equal(f.history.status().count,1);assert.equal(f.history.status().busy,false);
  });
}

for(const event of ['resize','loadingdone','loadingerror','resize-and-font']){
  test(`layout pipeline invalidation: ${event} coalesces one restart and retains atomic history ownership`,async()=>{
    const f=layoutFailureScene('dagre',{observeResize:true,observeFonts:true}),before=f.geometry(),facts=JSON.stringify(f.state.lastGraph);
    const {pending}=await f.begin(),old=f.workers[0],oldResult=f.calculate(old.message.input),container=f.h.element('#graphContainer');
    f.state.graph.zoomTo(.91);f.state.graph.translate(27,-18);const intendedCamera=f.camera;
    const resize=()=>{container.clientWidth=780;f.state.graphResizeObserver.callback();container.clientWidth=680;container.clientHeight=460;f.state.graphResizeObserver.callback();};
    if(event.includes('resize'))resize();
    if(event!=='resize'){
      f.fontEvent(event==='loadingerror'?'loadingerror':'loadingdone');
      f.fontEvent(event==='loadingerror'?'loadingerror':'loadingdone');
    }
    assert.equal(old.terminated,true,'changed layout measurements immediately revoke the obsolete worker');
    assert.equal(f.history.status().busy,true);assert.equal(f.history.status().count,0);
    assert.deepEqual(f.geometry(),before,'invalidation leaves the old complete canvas intact');
    old.deliver(oldResult);await f.quiet();assert.deepEqual(f.geometry(),before,'a retired reply cannot submit or roll back geometry');
    const restarts=f.h.timers.filter(timer=>!timer.cancelled&&!timer.ran&&timer.delay===60);
    assert.equal(restarts.length,1,'rapid measurement changes merge into one current 60 ms restart');
    restarts[0].ran=true;restarts[0].callback();await f.quiet();
    assert.equal(f.workers.length,2,'one restart starts one latest layout task');
    const latest=f.workers[1];assert.equal(latest.message.input.width,container.clientWidth);assert.equal(latest.message.input.height,container.clientHeight);
    assert.notEqual(latest.message.requestId,old.message.requestId);
    assert.equal(f.history.status().busy,true,'replacing the worker cannot end the outer edit while its successor is pending');
    assert.equal(f.history.status().count,0);assert.deepEqual(f.geometry(),before);
    old.deliver(oldResult);await f.quiet();assert.deepEqual(f.geometry(),before);
    latest.deliver(f.calculate(latest.message.input));await f.drain(pending);
    const accepted=f.geometry();old.deliver(oldResult);await f.quiet();
    assert.deepEqual(f.geometry(),accepted,'an old result cannot overwrite or roll back a newer committed layout');
    assert.equal(f.history.status().busy,false);assert.equal(f.history.status().count,1);
    assert.deepEqual(f.camera,intendedCamera,'a measurement restart preserves user pan/zoom ownership from the original job');
    assert.equal(JSON.stringify(f.state.lastGraph),facts);assert.equal(f.h.run('layoutJob'),null);
  });
}

for(const explicit of [false,true])test(`layout pipeline interaction: ${explicit?'explicit':'first-screen'} computation permits camera but rejects node and edge geometry`,async()=>{
  const f=layoutFailureScene('dagre'),before=f.geometry(),facts=JSON.stringify(f.state.lastGraph);
  let pending;
  if(explicit)({pending}=await f.begin());else{await f.h.run('relayout(false)');assert.equal(f.workers.length,1);}
  const graph=f.state.graph,worker=f.workers[0],job=f.h.run('layoutJob'),modes=graph.options.modes.default;
  assert.equal(job.phase,'computing');assert.equal(f.history.status().busy,explicit);
  for(const type of ['drag-canvas','zoom-canvas'])assert.equal(modes.find(mode=>mode.type===type).shouldBegin(),true,`${type} remains available on the old complete graph`);
  const node=graph.findById('A'),p=graph.getCanvasByPoint(node.model.x,node.model.y);
  graph.emit('node:mousedown',{item:node,clientX:p.x,clientY:p.y,button:0,preventDefault(){}});
  f.event('mousemove',{clientX:p.x+12,clientY:p.y+8,buttons:1,preventDefault(){}});f.event('mouseup');
  const edge=graph.getEdges()[0],text=edge.getContainer().children.find(shape=>shape.name==='text-shape');
  graph.emit('edge:mousedown',{item:edge,target:text,clientX:p.x,clientY:p.y,button:0,preventDefault(){}});
  f.event('mousemove',{clientX:p.x+25,clientY:p.y+40,buttons:1,preventDefault(){}});f.event('mouseup');
  assert.equal(f.h.run('layoutJob'),job,'a rejected geometry start cannot cancel the background task');
  assert.equal(worker.terminated,false);assert.deepEqual(f.geometry(),before);assert.equal(JSON.stringify(f.state.lastGraph),facts);
  assert.equal(f.state.graphSelection.status().dragging,false);assert.equal(f.history.status().count,0);
  graph.zoomTo(.93);graph.translate(30,-19);const intended=f.camera;
  worker.deliver(f.calculate(worker.message.input));if(explicit)await f.drain(pending);else await f.quiet();
  assert.equal(f.h.run('layoutJob'),null);assert.deepEqual(f.camera,intended,'completion retains camera actions made while computing');
  assert.equal(f.history.status().count,explicit?1:0);assert.equal(f.history.status().busy,false);
});

test('layout pipeline fault: an unresponsive worker expires and retries its selected method once',async()=>{
  const f=layoutFailureScene('force'),fallbacks=[],before=f.coords();
  f.env.FundsGraphLayout={...f.kernel,run:async input=>{fallbacks.push(input.kind);return f.calculate(input);}};
  const {pending}=await f.begin(),worker=f.workers[0];
  const deadline=f.h.timers.find(timer=>!timer.ran&&!timer.cancelled&&timer.delay===8000);
  assert.ok(deadline,'the layout job owns an 8 second worker deadline outside history polling');
  deadline.ran=true;deadline.callback();await f.drain(pending);
  assert.equal(worker.terminated,true);assert.deepEqual(fallbacks,['force'],'timeout recovery never silently changes force to layered');
  assert.equal(f.tasks.filter(task=>task.message.task==='layout').length,1,'recovery cannot spawn an unbounded layout Worker retry loop');
  assert.ok(f.tasks.filter(task=>task.message.task==='routes').length<=1,'the accepted layout may plan one separate route task');
  assert.equal(f.h.run('layoutJob'),null);assert.equal(f.history.status().busy,false);assert.equal(f.history.status().count,1);
  const accepted=f.coords();assert.notDeepEqual(accepted,before);
  worker.deliver({nodes:worker.message.input.nodes.map(node=>({id:node.id,x:-99999,y:-99999}))});await f.quiet();
  assert.deepEqual(f.coords(),accepted,'a late timed-out worker cannot overwrite its recovery result');
});

test('layout pipeline fault: worker failure and a rejected retry unlock the next public command',async()=>{
  const f=layoutFailureScene('structural'),before=f.geometry(),camera=f.camera,fallbacks=[];
  f.env.FundsGraphLayout={...f.kernel,run:async input=>{fallbacks.push(input.kind);throw new Error('synthetic fallback rejection');}};
  const {pending}=await f.begin();f.workers[0].fail();await f.drain(pending);
  assert.deepEqual(fallbacks,['structural']);assert.deepEqual(f.geometry(),before);assert.deepEqual(f.camera,camera);
  assert.equal(f.h.run('layoutJob'),null);assert.equal(f.history.status().busy,false);assert.equal(f.history.status().count,0);
  f.env.FundsGraphLayout=f.kernel;
  const next=f.h.element('#btnRelayout').onclick();await f.quiet();
  assert.equal(f.workers.length,2,'a second user command starts immediately after failure');
  f.workers[1].deliver(f.calculate(f.workers[1].message.input));await f.drain(next);
  assert.equal(f.h.run('layoutJob'),null);assert.equal(f.history.status().count,1);
});

test('layout pipeline fault: a renderer exception restores the entire prior display transaction',async()=>{
  const f=layoutFailureScene(),before=f.geometry(),camera=f.camera;let refreshes=0;
  f.state.graph.refreshPositions=()=>{refreshes++;if(refreshes===1)throw new Error('synthetic batch render failure');};
  const {pending}=await f.begin();f.workers[0].deliver(f.calculate(f.workers[0].message.input));await f.drain(pending);
  assert.ok(refreshes>=2,'rollback refreshes the restored coordinates after the failed batch');
  assert.deepEqual(f.geometry(),before);assert.deepEqual(f.camera,camera);
  assert.equal(f.h.run('layoutJob'),null);assert.equal(f.history.status().busy,false);assert.equal(f.history.status().count,0);
});

for(const revoke of ['cancel','view-revision']) {
  test(`layout pipeline cancellation: ${revoke} rejects a late complete worker result`,async()=>{
    const f=layoutFailureScene(),before=f.geometry(),camera=f.camera;
    const {pending}=await f.begin(),worker=f.workers[0],result=f.calculate(worker.message.input);
    if(revoke==='cancel')f.h.run('cancelGraphLayout()');else f.h.run('state.graphView.revision+=1');
    worker.deliver(result);await f.drain(pending);
    assert.equal(f.h.run('layoutJob'),null,'revoked geometry cannot keep the command busy');
    assert.deepEqual(f.geometry(),before);assert.deepEqual(f.camera,camera);assert.equal(f.history.status().count,0);
  });
}

test('layout pipeline candidates: automatic quality comparison commits only the accepted candidate once',async()=>{
  const f=layoutFailureScene('auto'),facts=JSON.stringify(f.state.lastGraph);let commits=0;
  // This transaction test needs a viewport where clearing overlaps also
  // retains readable text; the separate Pareto tests cover actual tradeoffs.
  f.h.element('#graphContainer').clientWidth=1400;f.h.element('#graphContainer').clientHeight=900;
  f.state.graph.refreshPositions=()=>{commits++;};
  const {pending}=await f.begin();
  const firstInput=f.workers[0].message.input,rejected={nodes:firstInput.nodes.map(node=>({id:node.id,x:250,y:250}))};
  f.workers[0].deliver(rejected);
  for(let pass=0;pass<12&&f.tasks.filter(task=>task.message.task==='layout').length<2;pass++)await new Promise(resolve=>setImmediate(resolve));
  const layouts=f.tasks.filter(task=>task.message.task==='layout');
  assert.equal(layouts.length,2,'an overlapping automatic proposal receives one existing-layout alternative');
  assert.equal(f.workers.length,1,'automatic candidates run serially in one bounded Worker');
  const accepted=f.calculate(layouts[1].message.input),quality=f.env.FundsGraphLayoutQuality;
  const rejectedQuality=quality.evaluate(firstInput,rejected,{fitViewport:f.kernel.fitViewport});
  const acceptedQuality=quality.evaluate(firstInput,accepted,{fitViewport:f.kernel.fitViewport});
  assert.equal(acceptedQuality.fit.readable,true,'the alternative retains the readable-text target in this atomic commit fixture');
  assert.ok(acceptedQuality.overlaps.count<rejectedQuality.overlaps.count,'the alternative clears the first proposal overlap');
  assert.equal(quality.prefer(rejectedQuality,acceptedQuality),acceptedQuality,'the real common quality gate prefers this non-deteriorating alternative');
  layouts[1].worker.deliver(accepted,{},layouts[1]);await f.drain(pending);
  assert.equal(commits,1,'the rejected candidate is measured without writing temporary node coordinates');
  assert.deepEqual(f.coords(),Object.fromEntries(accepted.nodes.map(node=>[node.id,{x:node.x,y:node.y}])));
  assert.equal(f.state.layout,'auto');assert.equal(f.history.status().count,1);assert.equal(f.history.status().busy,false);
  assert.ok(f.state.graphView.layoutQuality,'the accepted graph retains an inspectable quality summary');
  assert.equal(JSON.stringify(f.state.lastGraph),facts);
});

test('layout pipeline candidates: soft quality problems retain an explicitly selected layout',async()=>{
  const f=layoutFailureScene('dagre');
  const {pending}=await f.begin();f.workers[0].deliver({nodes:f.workers[0].message.input.nodes.map(node=>({id:node.id,x:250,y:250}))});
  await f.drain(pending);
  assert.equal(f.state.layout,'dagre');assert.equal(f.tasks.filter(task=>task.message.task==='layout').length,1,'soft scores cannot silently switch an explicit choice');
  assert.ok(f.tasks.filter(task=>task.message.task==='routes').length<=1,'complex coordinates may require a separate route task');
  assert.equal(f.h.run('layoutJob'),null);assert.equal(f.history.status().busy,false);
  assert.ok(f.state.graphView.layoutQuality,'soft quality remains visible in the quality receipt');
});

test('layout pipeline camera: a newer user zoom and pan outrank pending first-screen automatic fit',async()=>{
  const f=layoutFailureScene(),before=f.coords();
  await f.h.run('relayout(true)');assert.equal(f.workers.length,1);
  f.state.graph.zoomTo(1.7);f.state.graph.translate(83,-61);const intended=f.camera;
  f.workers[0].deliver(f.calculate(f.workers[0].message.input));await f.quiet();
  assert.equal(f.h.run('layoutJob'),null);assert.notDeepEqual(f.coords(),before,'the valid node arrangement still commits');
  assert.deepEqual(f.camera,intended,'background layout completion cannot replace the user newer camera');
  assert.equal(f.history.status().count,0,'a first-screen layout is outside explicit edit history');
});

test('layout pipeline fault: a failed layout selector change restores its prior selection without history',async()=>{
  const f=layoutFailureScene('dagre'),before=f.geometry(),camera=f.camera,retries=[];
  f.env.FundsGraphLayout={...f.kernel,run:async input=>{retries.push(input.kind);throw new Error('synthetic selected-method failure');}};
  const selector=f.h.element('#layoutSel');selector.value='force';
  const pending=selector.onchange({target:selector});await f.quiet();
  assert.equal(f.workers.length,1);assert.equal(f.workers[0].message.input.kind,'force');
  f.workers[0].deliver(null);await f.drain(pending);
  assert.deepEqual(retries,['force']);assert.equal(f.state.layout,'dagre');assert.equal(selector.value,'dagre');
  assert.deepEqual(f.geometry(),before);assert.deepEqual(f.camera,camera);
  assert.equal(f.history.status().count,0,'a rejected selection cannot become a display-only undo entry');
  assert.equal(f.history.status().busy,false);assert.equal(f.h.run('layoutJob'),null);
});

test('layout pipeline fault: the total deadline retires a pending yielding retry and consumes its late rejection',async()=>{
  const f=layoutFailureScene('force'),before=f.geometry(),camera=f.camera,retries=[];let rejectRetry,retryOptions;
  f.env.FundsGraphLayout={...f.kernel,run:(input,options)=>{retries.push(input.kind);retryOptions=options;
    return new Promise((resolve,reject)=>{rejectRetry=reject;});}};
  const {pending}=await f.begin();f.workers[0].fail();await f.quiet();
  assert.deepEqual(retries,['force']);assert.equal(f.history.status().busy,true);
  const deadline=f.h.timers.find(timer=>!timer.ran&&!timer.cancelled&&timer.delay===24000);
  assert.ok(deadline,'the whole job has its own bounded deadline, including main-thread recovery');
  deadline.ran=true;deadline.callback();await f.drain(pending);
  assert.equal(f.h.run('layoutJob'),null);assert.equal(f.history.status().busy,false);assert.equal(f.history.status().count,0);
  assert.equal(retryOptions.cancelled(),true,'the yielding kernel sees revocation between steps');
  assert.deepEqual(f.geometry(),before);assert.deepEqual(f.camera,camera);
  rejectRetry(new Error('synthetic late retry rejection'));await f.quiet();
  assert.deepEqual(f.geometry(),before);assert.deepEqual(f.camera,camera);assert.equal(f.h.run('layoutJob'),null);
  assert.equal(retries.length,1,'late failure does not reopen the exhausted recovery loop');
});

test('layout pipeline fault: measuring an incomplete host releases the job and preserves the prior canvas',async()=>{
  const f=layoutFailureScene(),before=f.geometry(),camera=f.camera,measure=f.env.graphLabelBounds;let measurementFailed=false;
  f.env.graphLabelBounds=(...args)=>{if(!measurementFailed){measurementFailed=true;throw new Error('synthetic measurement failure');}return measure(...args);};
  await f.drain(f.h.element('#btnRelayout').onclick());
  assert.equal(measurementFailed,true);assert.equal(f.workers.length,0,'an incomplete measured input never reaches a worker');
  assert.equal(f.h.run('layoutJob'),null,'setup failure cannot strand layout ownership');
  assert.equal(f.history.status().busy,false);assert.equal(f.history.status().count,0);
  assert.deepEqual(f.geometry(),before);assert.deepEqual(f.camera,camera);
  const next=f.h.element('#btnRelayout').onclick();await f.quiet();
  assert.equal(f.workers.length,1,'a later healthy measurement can start immediately');
  f.workers[0].deliver(f.calculate(f.workers[0].message.input));await f.drain(next);
  assert.equal(f.h.run('layoutJob'),null);assert.equal(f.history.status().count,1);
});

test('layout pipeline fault: saving after fit restores camera fit baseline and actual bottom-label geometry',async()=>{
  const f=layoutFailureScene();f.anchored();f.h.run('savePositions()');
  const before=f.geometry(),camera=f.camera,fitZoom=f.state.graphView.fitZoom,save=f.env.savePositions;
  const labels=()=>plainGraph(f.state.graph.getNodes().map(item=>{
    const text=item.getContainer().children.find(shape=>shape.name==='text-shape');
    return{id:item.model.id,text:text.attrs.text,box:text.getCanvasBBox(),body:item.getKeyShape().getCanvasBBox()};
  }));
  const beforeLabels=labels();let saves=0,fits=0,fittedZoom;
  f.env.fitGraph=(...args)=>{fits++;const result=f.fitGraph(...args);fittedZoom=f.state.graphView.fitZoom;return result;};
  f.env.savePositions=()=>{saves++;if(saves===1)throw new Error('synthetic save after fit failure');return save();};
  const {pending}=await f.begin();f.workers[0].deliver(f.calculate(f.workers[0].message.input));await f.drain(pending);
  assert.ok(fits>=1&&fits<=2,'one layout fits once and may refit after its bounded actual-curve proposal before the injected save failure');
  assert.ok(Math.abs(fittedZoom-fitZoom)>1e-7,'the fixture exercises a changed fit baseline before rollback');
  assert.deepEqual(f.geometry(),before);assert.deepEqual(f.camera,camera);assert.equal(f.state.graphView.fitZoom,fitZoom);
  assert.deepEqual(labels(),beforeLabels,'restored labels are drawn using the restored camera before any new user action');
  for(const label of labels())assert.ok(Math.abs(label.box.minY-label.body.maxY-6)<1e-7,'rollback preserves the actual 6 CSS pixel node-label gap');
  assert.equal(f.h.run('layoutJob'),null);assert.equal(f.history.status().busy,false);assert.equal(f.history.status().count,0);
});

test('layout pipeline camera: engine footprints are independent of the current user zoom',async()=>{
  const f=layoutFailureScene(),before=f.coords(),inputs=[];
  for(const zoom of [.2,2.4]) {
    f.state.graph.zoomTo(zoom);await f.h.run('relayout(false)');
    inputs.push(plainGraph(f.workers.at(-1).message.input));f.h.run('cancelGraphLayout()');
  }
  assert.equal(f.workers.length,2);assert.deepEqual(f.coords(),before,'measuring two cameras never moves accounts');
  const [small,large]=inputs;
  assert.deepEqual(small.nodes.map(node=>node.id),large.nodes.map(node=>node.id));
  for(let i=0;i<small.nodes.length;i++)for(const field of ['size','width','height','offsetX','offsetY','fontSize','labelGap','gapWorld']) {
    assert.ok(Math.abs(small.nodes[i][field]-large.nodes[i][field])<1e-7,`${small.nodes[i].id} ${field} uses canonical world geometry`);
  }
  for(let i=0;i<small.edges.length;i++)for(const field of ['labelWidth','labelHeight'])
    assert.ok(Math.abs(small.edges[i][field]-large.edges[i][field])<1e-7,`relationship ${field} is independent of camera zoom`);
  assert.ok(small.nodes.every(node=>node.gapWorld===6&&node.labelGap===6),'engine footprint and fixed screen gap are distinct canonical inputs');
  assert.equal(f.h.run('layoutJob'),null);assert.equal(f.history.status().count,0);
});

test('layout pipeline camera: fixed-screen shadows cannot inflate canonical circle and photo footprints',async()=>{
  const f=layoutFailureScene(),before=f.coords(),inputs=[];
  const fixtures=[
    {id:'A',local:{minX:-16.75,minY:-16.75,maxX:16.75,maxY:16.75},shadowBlur:5},
    {id:'B',local:{minX:-38,minY:-24,maxX:38,maxY:24},shadowBlur:7,photo:true},
  ];
  for(const fixture of fixtures){
    const item=f.state.graph.findById(fixture.id),shape=item.getKeyShape(),local=fixture.local;
    if(fixture.photo){item.model.type='image';item.model.size=40;item.model.img='data:image/png;base64,U1lOVEhFVElD';item.model.fundsLabel='P';}
    shape.getBBox=()=>({...local});
    shape.getCanvasBBox=()=>{
      const a=f.state.graph.getCanvasByPoint(item.model.x+local.minX,item.model.y+local.minY);
      const b=f.state.graph.getCanvasByPoint(item.model.x+local.maxX,item.model.y+local.maxY);
      // G6 canvas BBoxes include a screen-space shadow extension. Dividing
      // this whole box by zoom invents a larger physical body when zoomed out.
      return {minX:Math.min(a.x,b.x)-fixture.shadowBlur,minY:Math.min(a.y,b.y)-fixture.shadowBlur,
        maxX:Math.max(a.x,b.x)+fixture.shadowBlur,maxY:Math.max(a.y,b.y)+fixture.shadowBlur};
    };
  }
  for(const zoom of [.2,1.3]){
    f.state.graph.zoomTo(zoom);await f.h.run('relayout(false)');
    inputs.push(plainGraph(f.workers.at(-1).message.input));f.h.run('cancelGraphLayout()');
  }
  assert.equal(f.workers.length,2);assert.deepEqual(f.coords(),before,'camera measurement never moves accounts');
  const [small,large]=inputs;
  for(const fixture of fixtures){
    const a=small.nodes.find(node=>node.id===fixture.id),b=large.nodes.find(node=>node.id===fixture.id);
    for(const field of ['size','width','height','offsetX','offsetY','fontSize','labelGap','gapWorld'])
      assert.ok(Math.abs(a[field]-b[field])<1e-7,`${fixture.id} ${field} must use local geometry despite fixed-screen shadow expansion`);
  }
  const circle=small.nodes.find(node=>node.id==='A'),photo=small.nodes.find(node=>node.id==='B');
  assert.ok(circle.size<50,'a 33.5-world-unit circle cannot become an 83.5-unit body at zoom .2');
  assert.ok(photo.size>=76&&photo.width>=76,'the photo keeps its actual 76-unit local width instead of its 40-unit model diameter');
  assert.ok(photo.height>=48,'a noncircular photo retains its local height in the complete footprint');
  assert.equal(f.h.run('layoutJob'),null);assert.equal(f.history.status().count,0);
});

test('layout pipeline quality: the rendered receipt measures actual visible text at the committed zoom',async()=>{
  const f=layoutFailureScene(),{pending}=await f.begin();
  f.workers[0].deliver(f.calculate(f.workers[0].message.input));await f.drain(pending);
  const fonts=[];
  for(const item of [...f.state.graph.getNodes(),...f.state.graph.getEdges()])for(const shape of item.getContainer().children) {
    if(shape.name==='text-shape'&&shape.isVisible()&&Number.isFinite(shape.attr('fontSize')))fonts.push(shape.attr('fontSize'));
  }
  assert.ok(fonts.length>0,'the receipt is checked against actual drawn labels');
  const zoom=f.state.graph.getZoom(),rendered=f.state.graphView.layoutQuality.rendered;
  assert.equal(rendered.zoom,zoom);assert.equal(rendered.visibleLabels,fonts.length);
  assert.ok(Math.abs(rendered.minimumFontPixels-Math.min(...fonts)*Math.abs(zoom))<1e-7,'rendered readability uses real text font size times final zoom');
  assert.equal(f.history.status().count,1);assert.equal(f.h.run('layoutJob'),null);
});

// These contracts use the rendered quadratic path, body and text boxes from
// the public graph adapter. The long-labelled third account blocks A -> B;
// the other two relationships are explicitly controlled by the user.
function actualRouteScene({holdRoutes=false}={}){
  const f=contractScene({ids:'ABC',pairs:['AB','BC','CA']}),kernel=f.env.FundsGraphRouting;
  // Exercise a deliberately delayed background job independently of the
  // measured production policy that routes this tiny scene synchronously.
  if(holdRoutes)f.env.graphBackgroundRoutingWorthwhile=()=>true;
  const target={A:{x:100,y:300},B:{x:500,y:300},C:{x:300,y:300}};
  f.state.layout='dagre';f.h.element('#layoutSel').value='dagre';
  f.state.positions=plainGraph(target);
  f.state.edgeTypes={'B=>C':'line','C=>A':'polyline'};
  f.state.edgeBendPos={'C=>A':[{x:200,y:430}]};
  f.h.run('renderGraph();savePositions()');f.attachCamera(f.state.graph);
  let plans=0;const workers=[],tasks=[];
  f.env.FundsGraphRouting={...kernel,plan:input=>{plans++;return kernel.plan(input)}};
  f.env.Worker=class{
    constructor(){workers.push(this);this.terminated=false;this.tasks=[];}
    postMessage(message){this.message=message;const task={worker:this,message,onmessage:this.onmessage,onerror:this.onerror};
      this.tasks.push(task);tasks.push(task);if(holdRoutes&&message.task==='routes')return;
      task.onmessage({data:workerReply(f.env,message,message.task==='layout'?{
        result:{nodes:message.input.nodes.map(node=>({id:node.id,...target[node.id]})),layout:'synthetic-known-obstacle'}}:{})});}
    deliver(overrides={},task=this.tasks.at(-1)){return task.onmessage({data:workerReply(f.env,task.message,overrides)});}
    terminate(){this.terminated=true;}
  };
  const routes=()=>plainGraph(Object.fromEntries(f.state.graph.getEdges().map(item=>[`${item.model.source}=>${item.model.target}`,
    {type:item.model.type,controlPoints:item.model.controlPoints||[],curvePolicy:item.model.curvePolicy||null}])));
  const edge=key=>f.state.graph.getEdges().find(item=>`${item.model.source}=>${item.model.target}`===key);
  const geometry=()=>plainGraph({coordinates:f.coords(),routes:routes(),autoRoutes:f.state.graphView.autoRoutes,
    bends:f.state.edgeBendPos,types:f.state.edgeTypes,ratios:f.state.edgeLabelPos});
  return{...f,target,kernel,routes,edge,geometry,workers,tasks,get plans(){return plans},get camera(){return f.camera}};
}

test('graph route geometry: solid transformed bodies exclude screen shadows while labels keep their measured six-pixel gap',()=>{
  const f=actualRouteScene(),graph=f.state.graph,item=graph.findById('C'),shape=item.getKeyShape();
  const local={minX:-20,minY:-10,maxX:20,maxY:10},angle=Math.PI/6,cos=Math.cos(angle),sin=Math.sin(angle);
  const transform={a:cos*1.5,b:sin*1.5,c:-sin*.75,d:cos*.75,x:item.model.x+8,y:item.model.y-3},shadow=5;
  const corners=[[local.minX,local.minY],[local.minX,local.maxY],[local.maxX,local.minY],[local.maxX,local.maxY]]
    .map(([x,y])=>({x:transform.a*x+transform.c*y+transform.x,y:transform.b*x+transform.d*y+transform.y}));
  const expected={minX:Math.min(...corners.map(p=>p.x)),minY:Math.min(...corners.map(p=>p.y)),
    maxX:Math.max(...corners.map(p=>p.x)),maxY:Math.max(...corners.map(p=>p.y))};
  const closeBox=(actual,wanted,message)=>{for(const key of ['minX','minY','maxX','maxY'])
    assert.ok(Math.abs(actual[key]-wanted[key])<1e-7,`${message}: ${key} ${actual[key]} != ${wanted[key]}`);};
  let localReads=0,matrixReads=0;
  shape.getBBox=()=>{localReads++;return{...local};};
  shape.getTotalMatrix=()=>{matrixReads++;const camera=f.camera,zoom=graph.getZoom();return[
    transform.a*zoom,transform.b*zoom,0,transform.c*zoom,transform.d*zoom,0,
    transform.x*zoom+camera[6],transform.y*zoom+camera[7],1];};
  shape.getCanvasBBox=()=>{const a=graph.getCanvasByPoint(expected.minX,expected.minY),b=graph.getCanvasByPoint(expected.maxX,expected.maxY);
    return{minX:a.x-shadow,minY:a.y-shadow,maxX:b.x+shadow,maxY:b.y+shadow};};
  const text=item.getContainer().children.find(child=>child.name==='text-shape');
  text.getCanvasBBox=()=>{const body=shape.getCanvasBBox(),zoom=graph.getZoom(),center=(body.minX+body.maxX)/2;
    return{minX:center-30*zoom,maxX:center+30*zoom,minY:body.maxY+6,maxY:body.maxY+6+18*zoom};};
  const geometry=f.geometry();
  for(const zoom of [.2,1.3]){
    graph.zoomTo(zoom);
    const scene=f.h.run('graphRouteScene()'),node=scene.nodes.find(node=>node.id==='C');
    closeBox(node.body,expected,'local deformation and node/camera matrices define the solid obstacle');
    const label=text.getCanvasBBox(),a=graph.getPointByCanvas(label.minX,label.minY),b=graph.getPointByCanvas(label.maxX,label.maxY);
    closeBox(node.label,{minX:a.x,minY:a.y,maxX:b.x,maxY:b.y},'label obstacle retains the actual measured text envelope');
    assert.ok(Math.abs((node.label.minY-node.body.maxY)*zoom-shadow-6)<1e-7,'the text stays six screen pixels below the shadow envelope, independently of solid routing geometry');
  }
  assert.ok(localReads>=2&&matrixReads>=2,'the public scene adapter reads local geometry and total transforms');
  assert.deepEqual(f.geometry(),geometry,'route measurement cannot move accounts, routes or manual ratios');
});

test('graph route budget: oversized complete graphs skip every path measurement and planner before allocation',()=>{
  for(const limit of ['edges','nodes']){
    const f=actualRouteScene(),geometry=f.geometry(),camera=f.camera;let pathReads=0,plans=0,assessments=0;
    if(limit==='edges')f.state.lastGraph.edges=Array.from({length:1501},(_,i)=>({source:'A',target:'B',ids:[i+1]}));
    else f.state.lastGraph.nodes=Array.from({length:3001},(_,i)=>({account:'SYN-BUDGET-'+i}));
    const facts=JSON.stringify(f.state.lastGraph);
    for(const item of [...f.state.graph.getNodes(),...f.state.graph.getEdges()])item.getKeyShape=()=>{pathReads++;throw new Error('oversized routing must not measure shapes');};
    f.env.FundsGraphRouting={...f.kernel,plan:()=>{plans++;throw new Error('oversized routing must not allocate its planner scene');}};
    const quality=f.env.FundsGraphRouteQuality;
    f.env.FundsGraphRouteQuality={...quality,evaluate:()=>{assessments++;throw new Error('oversized routing must not assess partial geometry');}};
    assert.doesNotThrow(()=>f.h.run('improveGraphRoutes({fit:true})'));
    assert.equal(pathReads,0,`${limit} cap is checked before any key-shape read`);assert.equal(plans,0);assert.equal(assessments,0);
    const receipt=f.state.graphView.routeQuality;
    assert.equal(receipt.reason,'obstacle-budget');assert.equal(receipt.accepted,false);assert.equal(receipt.evaluated,false);
    assert.equal(receipt.coverage.complete,false,'skipped routing cannot claim a complete collision assessment');
    assert.deepEqual(f.geometry(),geometry);assert.deepEqual(f.camera,camera);assert.equal(JSON.stringify(f.state.lastGraph),facts);
    assert.equal(f.history.status().count,0);assert.equal(f.history.status().busy,false);
  }
});

function dragRouteScene(selectionSize=1){
  const extras=selectionSize>=50?Array.from({length:selectionSize-1},(_,i)=>'SYN-DRAG-'+i):[];
  const f=contractScene({ids:['A','B','C','D','E',...extras],pairs:[['A','B'],['A','C'],['D','E']]});
  f.state.positions={A:{x:400,y:500},B:{x:400,y:100},C:{x:800,y:500},D:{x:100,y:900},E:{x:700,y:900},
    ...Object.fromEntries(extras.map((id,i)=>[id,{x:3000+i%7*500,y:3000+Math.floor(i/7)*300}]))};
  for(const edge of f.state.lastGraph.edges){edge.total='0.01';edge.amount_totals[0].amount='0.01';}
  f.state.edgeTypes={'A=>C':'polyline'};f.state.edgeBendPos={'A=>C':[{x:600,y:650}]};
  f.h.run('renderGraph();savePositions()');f.attachCamera(f.state.graph);
  const selected=selectionSize===2?['A','B']:['B',...extras];f.state.graphSelection.rebind(selected);
  const routes=()=>plainGraph(Object.fromEntries(f.state.graph.getEdges().map(item=>[`${item.model.source}=>${item.model.target}`,
    {type:item.model.type,controlPoints:item.model.controlPoints||[],curvePolicy:item.model.curvePolicy||null}])));
  const geometry=()=>plainGraph({coordinates:f.coords(),routes:routes(),autoRoutes:f.state.graphView.autoRoutes,
    bends:f.state.edgeBendPos,types:f.state.edgeTypes,ratios:f.state.edgeLabelPos});
  const item=f.state.graph.findById('B'),start=f.state.graph.getCanvasByPoint(item.model.x,item.model.y);
  const startDrag=()=>f.state.graph.emit('node:mousedown',{item,clientX:start.x,clientY:start.y,button:0,preventDefault(){}});
  const move=(dx,dy)=>f.event('mousemove',{clientX:start.x+dx,clientY:start.y+dy,buttons:1,preventDefault(){}});
  const end=(dx=20,dy=20)=>f.event('mouseup',{clientX:start.x+dx,clientY:start.y+dy,preventDefault(){}});
  return{...f,selected,routes,geometry,startDrag,move,end};
}

for(const selectionSize of [1,2,50])test(`graph drag routing: ${selectionSize} selected nodes optimize incident routes once before the same history commit`,async()=>{
  const f=dragRouteScene(selectionSize),before=f.geometry(),camera=f.camera,facts=JSON.stringify(f.state.lastGraph);
  const improve=f.env.improveGraphRoutes,calls=[],ended=[];
  f.env.improveGraphRoutes=options=>{calls.push({fit:options.fit,keys:[...options.eligibleKeys].sort(),coordinates:f.coords()});return improve(options)};
  f.env.window.addEventListener('funds:selection-drag-end',()=>ended.push({calls:calls.length,geometry:f.geometry(),saved:plainGraph(f.state.positions)}));
  f.startDrag();f.move(0,10);f.move(0,20);
  assert.equal(calls.length,0,'pointer frames never invoke global route planning');
  if(selectionSize>=50)assert.deepEqual(f.coords(),before.coordinates,'large selection keeps its lightweight preview until release');
  f.end(0,20);await Promise.resolve();
  assert.equal(calls.length,1,'a committed selection move owns exactly one incident-route optimization');
  assert.equal(calls[0].fit,false);assert.deepEqual(calls[0].keys,selectionSize===2?['A=>B','A=>C']:['A=>B']);
  assert.deepEqual(calls[0].coordinates,f.coords(),'preview positions are committed before the route hook runs');
  assert.equal(ended.length,1);assert.equal(ended[0].calls,1);assert.deepEqual(ended[0].saved,f.coords(),'the end event observes final saved node and route geometry');
  assert.equal(f.state.graphView.routeQuality.accepted,true,JSON.stringify(f.state.graphView.routeQuality));assert.equal(f.routes()['A=>B'].curvePolicy,'obstacle-aware');
  assert.deepEqual(f.routes()['A=>C'],before.routes['A=>C'],'manual boundary controls remain anchored');
  assert.deepEqual(f.routes()['D=>E'],before.routes['D=>E'],'unrelated automatic routes stay unchanged');
  assert.deepEqual(plainGraph(f.state.edgeBendPos),before.bends);assert.deepEqual(f.camera,camera);assert.equal(JSON.stringify(f.state.lastGraph),facts);
  assert.equal(f.history.status().count,1);assert.equal(f.history.status().busy,false);
  const moved=f.geometry();f.end();f.event('blur');assert.equal(calls.length,1,'late release and focus loss cannot duplicate routing or history');
  await f.drain(f.h.element('#btnUndo').onclick());f.attachCamera(f.state.graph);assert.deepEqual(f.geometry(),before,'undo restores nodes and their automatic route as one edit');
  await f.drain(f.h.element('#btnRedo').onclick());f.attachCamera(f.state.graph);assert.deepEqual(f.geometry(),moved);assert.equal(calls.length,1);
});

for(const terminal of ['threshold-click','roundtrip','cancel'])test(`graph drag routing: ${terminal} never runs route planning or creates history`,async()=>{
  const f=dragRouteScene(),before=f.geometry();let calls=0;
  f.env.improveGraphRoutes=()=>{calls++};f.startDrag();
  if(terminal==='threshold-click'){f.move(2,2);f.end(2,2);}
  else if(terminal==='roundtrip'){f.move(0,10);f.move(0,0);f.end(0,0);}
  else {f.move(0,10);f.state.graphSelection.rebind(f.selected);f.end(0,10);}
  await Promise.resolve();
  assert.equal(calls,0);assert.deepEqual(f.geometry(),before);assert.equal(f.history.status().count,0);assert.equal(f.history.status().busy,false);
});

for(const fault of ['rejected','planner-error'])test(`graph drag routing: ${fault} preserves the committed move, camera and history completion`,async()=>{
  const f=dragRouteScene(),before=f.geometry(),camera=f.camera,kernel=f.env.FundsGraphRouting,notices=[];
  const improve=f.env.improveGraphRoutes;let calls=0,ends=0;
  f.env.improveGraphRoutes=options=>{calls++;return improve(options)};
  f.env.FundsGraphRouting={...kernel,plan:()=>{
    if(fault==='planner-error')throw new Error('synthetic route planning failure before its recovery try');
    return{routes:[],stats:{synthetic:true}};
  }};
  f.env.toast=(...args)=>notices.push(args);
  f.env.window.addEventListener('funds:selection-drag-end',()=>{ends++;assert.deepEqual(plainGraph(f.state.positions),f.coords())});
  f.startDrag();f.move(0,20);assert.doesNotThrow(()=>f.event('blur'));await Promise.resolve();
  assert.equal(calls,1);assert.equal(ends,1);assert.notDeepEqual(f.coords(),before.coordinates);
  assert.deepEqual(plainGraph(f.state.positions),f.coords());assert.deepEqual(f.camera,camera);
  assert.deepEqual(f.routes()['A=>C'],before.routes['A=>C']);assert.deepEqual(f.routes()['D=>E'],before.routes['D=>E']);
  assert.equal(f.state.graphView.routeQuality.accepted,false);assert.equal(notices.length,fault==='planner-error'?1:0);
  if(fault==='planner-error'){
    assert.equal(f.state.graphView.routeQuality.reason,'route-error');assert.equal(notices[0][1],'err');
    assert.match(notices[0][0],/节点已移动.*优化未完成/,'a route exception is visible while retaining the valid node move');
  }
  assert.equal(f.history.status().count,1);assert.equal(f.history.status().busy,false);
  const moved=f.geometry();f.end(0,20);assert.equal(calls,1);assert.equal(ends,1);
  await f.drain(f.h.element('#btnUndo').onclick());f.attachCamera(f.state.graph);assert.deepEqual(f.geometry(),before);
  await f.drain(f.h.element('#btnRedo').onclick());f.attachCamera(f.state.graph);assert.deepEqual(f.geometry(),moved);assert.equal(calls,1);
});

for(const hasCache of [true,false])test(`graph route layout: a simple endpoint-label obstruction reaches prepared planning and actual admission (${hasCache?'cached automatic route':'uncached automatic route'})`,async()=>{
  const f=contractScene({ids:'ABC',pairs:['AB','AC']}),kernel=f.env.FundsGraphRouting;
  const target={A:{x:400,y:500},B:{x:400,y:100},C:{x:800,y:500}};
  f.state.layout='dagre';f.h.element('#layoutSel').value='dagre';
  f.state.positions=hasCache?{A:{x:450,y:550},B:{x:450,y:150},C:{x:850,y:550}}:plainGraph(target);
  f.state.edgeTypes={'A=>C':'line'};
  f.state.lastGraph.edges[0].total='0.01';f.state.lastGraph.edges[0].amount_totals[0].amount='0.01';
  f.state.graphView.autoRoutes={'A=>B':{source:{...f.state.positions.A},target:{...f.state.positions.B},
    controlPoints:[{x:800,y:350}],curvePolicy:'obstacle-aware'}};
  f.h.run('renderGraph();savePositions()');f.attachCamera(f.state.graph);
  if(!hasCache)delete f.state.graphView.autoRoutes['A=>B'];
  f.env.graphBackgroundRoutingWorthwhile=()=>true;
  const plannedInputs=[],proposals=[];let layoutTasks=0,routeTasks=0;
  f.env.FundsGraphRouting={...kernel,plan:input=>{
    plannedInputs.push(plainGraph(input));const proposal=kernel.plan(input);proposals.push(plainGraph(proposal));return proposal;
  }};
  f.env.Worker=class{
    postMessage(message){
      if(message.task==='layout')layoutTasks++;else routeTasks++;
      this.onmessage({data:workerReply(f.env,message,message.task==='layout'?{
        result:{nodes:message.input.nodes.map(node=>({id:node.id,...target[node.id]})),layout:'synthetic-simple-endpoint-obstruction'}}:{})});
    }
    terminate(){}
  };
  const facts=JSON.stringify(f.state.lastGraph);
  await f.drain(f.h.element('#btnRelayout').onclick());
  assert.equal(layoutTasks,1);assert.equal(routeTasks,1,'simple topology cannot discard a measured background route preparation');
  const input=plannedInputs[0],edge=input.edges.find(edge=>edge.id==='A=>B');
  assert.equal(input.simple,true);assert.equal(edge.manual,false);
  assert.equal(edge.sampledPoints,undefined,'an uncached old automatic path cannot override the fresh compact baseline');
  if(!hasCache)assert.ok(input.edges.find(edge=>edge.id==='A=>C').sampledPoints?.length,'unchanged manual geometry still uses its exact sampled path');
  assert.deepEqual(edge.controlPoints,[{x:428,y:300}],
    'fresh layout planning uses the same compact default arc at the proposed coordinates rather than old obstacle controls or a straight chord');
  assert.ok(proposals[0].stats.baseline.nodeLabelHits>0,'the prepared compact arc detects the real endpoint text obstruction');
  assert.ok(proposals[0].routes.some(route=>route.id==='A=>B'),'the bounded planner proposes a clearer approach to that endpoint');
  const receipt=f.state.graphView.routeQuality;
  assert.equal(receipt.accepted,true,'the prepared proposal still crosses actual rendered-geometry admission');
  assert.ok(receipt.before.coverage.complete&&receipt.after.coverage.complete);
  assert.ok(receipt.after.counts.edgeNodeLabel<receipt.before.counts.edgeNodeLabel);
  assert.equal(f.state.graph.getEdges()[0].model.curvePolicy,'obstacle-aware');
  assert.deepEqual(f.coords(),target);assert.ok(f.camera.every(Number.isFinite));assert.equal(JSON.stringify(f.state.lastGraph),facts);
  assert.deepEqual(plainGraph(f.state.edgeTypes),{'A=>C':'line'});assert.deepEqual(plainGraph(f.state.edgeBendPos),{});
  assert.equal(f.history.status().count,1);assert.equal(f.history.status().busy,false);assert.equal(f.h.run('layoutJob'),null);
});

test('graph route layout: clear compact defaults prepare no alternative candidates and retain routing budgets',()=>{
  const f=contractScene({ids:'AB',pairs:['AB']});
  f.state.positions={A:{x:100,y:300},B:{x:700,y:300}};
  f.h.run('renderGraph();savePositions()');f.attachCamera(f.state.graph);
  f.env.routeLayoutCandidate={result:{nodes:Object.entries(f.state.positions).map(([id,p])=>({id,...p}))},quality:{fit:{zoom:.8}}};
  const input=f.h.run('graphLayoutRouteInput({nodes:[]},routeLayoutCandidate,false)');
  assert.ok(input);assert.equal(input.simple,true);assert.equal(input.maxChecks,120000);assert.equal(input.maxEdges,320);
  assert.deepEqual(plainGraph(input.edges[0].controlPoints),[{x:400,y:328}]);
  assert.equal(input.edges[0].sampledPoints,undefined,'fresh automatic planning does not redundantly sample old geometry');
  const proposal=f.env.FundsGraphRouting.plan(input);
  assert.equal(proposal.stats.baseline.complete,true);assert.equal(proposal.stats.candidates,0,'clear short arcs require no alternative-curve search');
  assert.deepEqual(plainGraph(proposal.routes),[]);
  f.state.lastGraph.edges=Array(1501);
  assert.equal(f.h.run('graphLayoutRouteInput({nodes:[]},routeLayoutCandidate,false)'),null,'oversized scenes retain their pre-allocation edge cap');
  f.state.lastGraph.edges=[];f.state.lastGraph.nodes=Array(3001);
  assert.equal(f.h.run('graphLayoutRouteInput({nodes:[]},routeLayoutCandidate,false)'),null,'oversized scenes retain their pre-allocation node cap');
});

test('graph route contract: actual identities reject trading an old account-label obstruction for a new one',()=>{
  const f=contractScene({ids:'AB',pairs:['AB']}),quality=f.env.FundsGraphRouteQuality;
  const nodes=[{id:'first',label:{minX:40,minY:-5,maxX:60,maxY:5}},
    {id:'second',label:{minX:40,minY:95,maxX:60,maxY:105}}];
  const measure=y=>quality.evaluate({nodes,edges:[{id:'A=>B',source:'A',target:'B',path:[['M',0,y],['L',100,y]]}],witnessLimit:0});
  f.env.identityBefore=measure(0);f.env.identityAfter=measure(100);
  assert.equal(f.env.identityBefore.counts.edgeNodeLabel,f.env.identityAfter.counts.edgeNodeLabel);
  assert.equal(f.h.run('graphRouteQualityImproves(identityBefore,identityAfter)'),false);
  f.env.identityAfter=measure(200);
  assert.equal(f.h.run('graphRouteQualityImproves(identityBefore,identityAfter)'),true,'removing the obstruction is still accepted');
  delete f.env.identityAfter.collisionKeys;
  assert.equal(f.h.run('graphRouteQualityImproves(identityBefore,identityAfter)'),false,'missing identity evidence cannot admit a proposal');
});

test('graph route contract: a simple graph accepts a materially shorter actual route with equal collision counts',()=>{
  const f=contractScene({ids:'AB',pairs:['AB']});
  f.state.positions={A:{x:100,y:300},B:{x:700,y:300}};
  f.state.graphView.autoRoutes={'A=>B':{source:{x:100,y:300},target:{x:700,y:300},
    controlPoints:[{x:400,y:700}],curvePolicy:'obstacle-aware'}};
  f.h.run('renderGraph();savePositions()');f.attachCamera(f.state.graph);
  assert.equal(f.h.run('graphHasSimpleGeometry()'),true,'topology classification cannot skip real route quality evaluation');
  const coordinates=f.coords(),camera=f.camera,facts=JSON.stringify(f.state.lastGraph),kernel=f.env.FundsGraphRouting;
  let plans=0;
  f.env.FundsGraphRouting={...kernel,plan:()=>{plans++;return{routes:[{id:'A=>B',source:'A',target:'B',type:'quadratic',
    controlPoints:[{x:400,y:328}],curvePolicy:'obstacle-aware'}],stats:{synthetic:true}}}};
  f.h.run('improveGraphRoutes({fit:false})');
  const receipt=f.state.graphView.routeQuality;
  assert.equal(plans,1);assert.equal(receipt.accepted,true);
  assert.ok(receipt.before.coverage.complete&&receipt.after.coverage.complete);
  assert.deepEqual(plainGraph(receipt.after.counts),plainGraph(receipt.before.counts),'a path can improve without inventing a collision reduction');
  assert.ok(receipt.before.totalLength-receipt.after.totalLength>=Math.max(8,receipt.before.totalLength*.03),
    'the accepted improvement is a measured reduction in real rendered path length');
  assert.deepEqual(plainGraph(f.state.graph.getEdges()[0].model.controlPoints),[{x:400,y:328}]);
  assert.deepEqual(f.coords(),coordinates);assert.deepEqual(f.camera,camera);assert.equal(JSON.stringify(f.state.lastGraph),facts);
  assert.deepEqual(plainGraph(f.state.edgeTypes),{});assert.deepEqual(plainGraph(f.state.edgeBendPos),{});
  assert.equal(f.history.status().count,0,'a direct quality probe does not create a separate user edit');
});

for(const mode of ['batch','salvage','rollback'])test(`graph route contract: unrelated long relationships cannot dilute a local shortening (${mode})`,()=>{
  const salvage=mode==='salvage',rollback=mode==='rollback',f=contractScene({ids:'ABCD',pairs:['AB','CD']});
  f.state.positions={A:{x:100,y:300},B:{x:700,y:300},C:{x:100,y:-2000},D:{x:50100,y:-2000}};
  f.state.edgeTypes=rollback?{}:{'C=>D':'line'};
  f.state.graphView.autoRoutes={'A=>B':{source:{x:100,y:300},target:{x:700,y:300},
    controlPoints:[{x:400,y:700}],curvePolicy:'obstacle-aware'}};
  f.h.run('renderGraph();savePositions()');f.attachCamera(f.state.graph);
  const graph=f.state.graph,unrelated=graph.getEdges().find(item=>item.model.source==='C');
  const coordinates=f.coords(),camera=f.camera,facts=JSON.stringify(f.state.lastGraph),original=plainGraph(unrelated.model);
  const kernel=f.env.FundsGraphRouting,quality=f.env.FundsGraphRouteQuality;
  const proposal={routes:[{id:'A=>B',source:'A',target:'B',type:'quadratic',
    controlPoints:[{x:400,y:328}],curvePolicy:'obstacle-aware'}],stats:{synthetic:true}};
  if(rollback)proposal.routes.push({id:'C=>D',source:'C',target:'D',type:'quadratic',
    controlPoints:[{x:25100,y:-20000}],curvePolicy:'obstacle-aware'});
  f.env.FundsGraphRouting={...kernel,plan:()=>proposal};f.env.syntheticRouteProposal=proposal;
  let assessments=0,baseline;
  f.env.FundsGraphRouteQuality={...quality,evaluate:input=>{
    const result=quality.evaluate(input);assessments++;if(assessments===1)baseline=result;
    // Force only the whole-batch assessment to fail. The real single-route
    // and final full-scene assessments still use actual sampled geometry.
    return salvage&&assessments===2?{...result,counts:{...result.counts,edgeNode:baseline.counts.edgeNode+1}}:result;
  }};
  f.h.run(rollback?'improveGraphRoutes({fit:false,proposal:syntheticRouteProposal})':'improveGraphRoutes({fit:false})');
  const receipt=f.state.graphView.routeQuality;
  assert.equal(receipt.accepted,true,'the changed route is evaluated against its own length rather than a remote relationship');
  assert.ok(receipt.before.coverage.complete&&receipt.after.coverage.complete);
  assert.deepEqual(plainGraph(receipt.before.counts),plainGraph(receipt.after.counts),'all complete-scene collision categories remain protected');
  assert.ok(receipt.before.totalLength-receipt.after.totalLength<receipt.before.totalLength*.03,
    'the unrelated 50,000-unit relationship deliberately makes the global threshold unsuitable');
  assert.deepEqual(plainGraph(graph.getEdges().find(item=>item.model.source==='A').model.controlPoints),[{x:400,y:328}]);
  assert.deepEqual(plainGraph(unrelated.model),original,'unrelated or rolled-back geometry stays authoritative');
  assert.deepEqual(f.coords(),coordinates);assert.deepEqual(f.camera,camera);assert.equal(JSON.stringify(f.state.lastGraph),facts);
  assert.deepEqual(plainGraph(f.state.edgeTypes),rollback?{}:{'C=>D':'line'});assert.deepEqual(plainGraph(f.state.edgeBendPos),{});
  assert.equal(receipt.actualTrials,salvage?1:0);assert.equal(f.history.status().count,0);
});

test('graph route contract: shortening never trades away any collision category or incomplete coverage',()=>{
  const h=harness('app.js'),counts={edgeNode:1,labelNode:1,edgeNodeLabel:1,edgeLabel:1,labelOverlap:1,collinearOverlap:1,crossings:1};
  const collisionKeys=Object.fromEntries(Object.keys(counts).map(key=>[key,[key+':existing']]));
  const baseline={valid:true,coverage:{complete:true},counts,collisionKeys,totalLength:1000};
  const compare=after=>{h.run('globalThis').routeQualityPair={before:baseline,after};return h.run('graphRouteQualityImproves(routeQualityPair.before,routeQualityPair.after)')};
  assert.equal(compare({...baseline,totalLength:970.01}),false,'a sub-threshold length change is not an improvement');
  assert.equal(compare({...baseline,totalLength:970}),true,'the three-percent boundary is inclusive');
  for(const key of Object.keys(counts))assert.equal(compare({...baseline,totalLength:100,counts:{...counts,[key]:2}}),false,
    `a shorter path cannot increase ${key}`);
  assert.equal(compare({...baseline,totalLength:100,coverage:{complete:false}}),false);
  assert.equal(compare({...baseline,totalLength:100,valid:false}),false);
  assert.equal(compare({...baseline,totalLength:NaN}),false);
  baseline.totalLength=100;
  assert.equal(compare({...baseline,totalLength:92.01}),false,'short routes still require an eight-unit minimum improvement');
  assert.equal(compare({...baseline,totalLength:92}),true,'the eight-unit boundary is inclusive');
});

test('graph route contract: local length evidence keeps all global safety gates and excludes unrelated lengths',()=>{
  const h=harness('app.js'),counts={edgeNode:0,labelNode:0,edgeNodeLabel:0,edgeLabel:0,labelOverlap:0,collinearOverlap:0,crossings:0};
  const collisionKeys=Object.fromEntries(Object.keys(counts).map(key=>[key,[]]));
  const before={valid:true,coverage:{complete:true},counts,collisionKeys,totalLength:51000,edgeLengths:{'A=>B':1000,'C=>D':50000}};
  const after={...before,totalLength:50970,edgeLengths:{'A=>B':970,'C=>D':50000}};
  const compare=(candidate,keys=['A=>B'])=>{
    h.run('globalThis').routeQualityPair={before,after:candidate,keys};
    return h.run('graphRouteQualityImproves(routeQualityPair.before,routeQualityPair.after,routeQualityPair.keys)');
  };
  assert.equal(compare(after),true,'an unrelated long path contributes no weight to the local three-percent threshold');
  assert.equal(compare(after,['A=>B','C=>D']),false,'a genuinely changed long path must be included in the baseline');
  assert.equal(compare(after,[]),false,'a label or policy refresh without changed path geometry is not a length improvement');
  assert.equal(compare({...after,edgeLengths:{'C=>D':50000}}),false,'missing sampled local evidence cannot authorize a route');
  assert.equal(compare({...after,edgeLengths:{...after.edgeLengths,'A=>B':NaN}}),false);
  for(const key of Object.keys(counts))assert.equal(compare({...after,counts:{...counts,[key]:1}}),false,
    `local shortening cannot increase global ${key}`);
  assert.equal(compare({...after,valid:false}),false);
  assert.equal(compare({...after,coverage:{complete:false}}),false);
});

test('graph route contract: public relayout improves actual obstructed curves without fitting or changing node facts',async()=>{
  const f=actualRouteScene(),coordinates=f.coords(),camera=f.camera,facts=JSON.stringify(f.state.lastGraph),manual=f.routes();
  await f.drain(f.h.run('relayout(false)'));
  const quality=f.state.graphView.routeQuality;
  assert.equal(f.plans,1,'one node layout invokes one bounded route planner');
  assert.equal(quality.accepted,true,`the actual sampled G6 route must improve before committing: ${JSON.stringify(quality)}`);
  assert.ok(quality.before.coverage.complete&&quality.after.coverage.complete);
  assert.ok(quality.before.counts.edgeNode>quality.after.counts.edgeNode,'the accepted curve avoids the account that blocked its original path');
  assert.equal(f.edge('A=>B').model.curvePolicy,'obstacle-aware');
  assert.equal(f.edge('A=>B').model.controlPoints.length,1);
  assert.deepEqual(f.coords(),coordinates,'routing never moves the accounts');assert.deepEqual(f.camera,camera,'relayout(false) cannot zoom to conceal a route');
  for(const key of ['B=>C','C=>A'])assert.deepEqual(f.routes()[key],manual[key],'manual line and bend geometry stays authoritative');
  assert.deepEqual(plainGraph(f.state.edgeBendPos),{'C=>A':[{x:200,y:430}]},'automatic controls never become manual bend entries');
  const item=f.edge('A=>B'),text=item.getContainer().children.find(shape=>shape.name==='text-shape'),point=item.getKeyShape().getPoint(.5);
  assert.ok(Math.abs(text.attr('x')-point.x)<1e-7&&Math.abs(text.attr('y')-point.y)<1e-7,'plain relation text stays at its own actual curve midpoint');
  assert.ok(item.getContainer().children.find(shape=>shape.name==='funds-edge-visible-path')?.getClip());
  assert.equal(JSON.stringify(f.state.lastGraph),facts);assert.equal(f.h.run('layoutJob'),null);
});

for(const fault of ['no-improvement','invalid-path','incomplete-assessment'])test(`graph route contract: ${fault} proposal restores exact prior geometry and camera`,async()=>{
  const f=actualRouteScene(),before=f.geometry(),camera=f.camera,quality=f.env.FundsGraphRouteQuality;
  f.env.FundsGraphRouting={...f.kernel,plan:()=>({routes:[{id:'A=>B',source:'A',target:'B',type:'quadratic',
    controlPoints:[{x:fault==='invalid-path'?NaN:300,y:300}],curvePolicy:'obstacle-aware'}],stats:{synthetic:true}})};
  if(fault==='incomplete-assessment'){
    let calls=0;
    f.env.FundsGraphRouteQuality={...quality,evaluate:input=>{const result=quality.evaluate(input);calls++;
      return calls===1?result:{...result,coverage:{...result.coverage,complete:false}}}};
  }
  f.h.run('improveGraphRoutes({fit:false})');
  assert.equal(f.state.graphView.routeQuality.accepted,false);
  assert.deepEqual(f.geometry(),before,'a rejected proposal leaves no temporary control point or auto cache entry');
  assert.deepEqual(f.camera,camera);assert.equal(f.history.status().count,0);assert.equal(f.history.status().busy,false);
  const item=f.edge('A=>B'),text=item.getContainer().children.find(shape=>shape.name==='text-shape');
  assert.ok(Number.isFinite(text.attr('x'))&&Number.isFinite(text.attr('y')),'failed path measurement restores finite visible text');
});

test('graph route contract: drag, fit, undo and redo retain the accepted automatic route recipe and manual curves',async()=>{
  const f=actualRouteScene();await f.drain(f.h.element('#btnRelayout').onclick());
  assert.equal(f.state.graphView.routeQuality.accepted,true);assert.equal(f.history.status().count,1);
  const graph=f.state.graph,arranged=f.geometry(),manual=f.routes()['C=>A'],camera=f.camera;
  const start=graph.findById('A').model,screen=graph.getCanvasByPoint(start.x,start.y),zoom=graph.getZoom();
  graph.emit('node:mousedown',{item:graph.findById('A'),clientX:screen.x,clientY:screen.y,button:0,preventDefault(){}});
  f.event('mousemove',{clientX:screen.x+6,clientY:screen.y+2,buttons:1,preventDefault(){}});f.event('mouseup',{});
  const moved=f.geometry();assert.ok(Math.abs(moved.coordinates.A.x-arranged.coordinates.A.x-6/zoom)<1e-7);
  assert.notDeepEqual(moved.routes['A=>B'].controlPoints,arranged.routes['A=>B'].controlPoints,'the accepted bend follows the manually moved endpoint');
  assert.deepEqual(moved.routes['C=>A'],manual,'a boundary manual bend is not reinterpreted as an automatic one');
  assert.equal(f.history.status().count,2);assert.equal(f.plans,2,'one completed node move adds exactly one incident-route assessment');
  const movedRoutes=f.routes(),recipe=plainGraph(f.state.graphView.autoRoutes);
  for(const scale of [.3,2]){graph.zoomTo(scale);f.applyLabels();f.h.element('#btnFit').onclick();
    assert.deepEqual(f.routes(),movedRoutes);assert.deepEqual(plainGraph(f.state.graphView.autoRoutes),recipe);}
  assert.equal(f.plans,2,'zoom and fit only change the camera');
  await f.drain(f.h.element('#btnUndo').onclick());f.attachCamera(f.state.graph);
  assert.deepEqual(f.geometry(),arranged,'undo restores positions, accepted curves and recipe as one geometry edit');
  await f.drain(f.h.element('#btnRedo').onclick());f.attachCamera(f.state.graph);
  assert.deepEqual(f.geometry(),moved,'redo restores the exact accepted route after endpoint movement');
  assert.deepEqual(plainGraph(f.state.edgeBendPos),{'C=>A':[{x:200,y:430}]});
  assert.equal(f.plans,2);assert.ok(camera.every(Number.isFinite));
});

test('graph route contract: snapshot reopen retains auto controls without promoting them to manual geometry',async()=>{
  const f=actualRouteScene();await f.drain(f.h.run('relayout(false)'));
  const snapshot=await f.h.run('window.__collectSnapshot({scope:"all",embedPhotos:false})');
  const edge=snapshot.edges.find(item=>item.source==='A'&&item.target==='B');
  assert.equal(edge.routeManual,false);assert.equal(edge.curvePolicy,'obstacle-aware');assert.equal(edge.controlPoints.length,1);
  const cache=plainGraph(f.state.graphView.autoRoutes),plans=f.plans;
  f.env.savedRouteSnapshot=snapshot;f.h.run('applyCanvasSnapshot(savedRouteSnapshot)');f.attachCamera(f.state.graph);
  assert.deepEqual(plainGraph(f.edge('A=>B').model.controlPoints),plainGraph(edge.controlPoints));
  assert.equal(f.edge('A=>B').model.curvePolicy,'obstacle-aware');
  assert.equal(Object.hasOwn(f.state.edgeTypes,'A=>B'),false);assert.equal(Object.hasOwn(f.state.edgeBendPos,'A=>B'),false);
  assert.deepEqual(plainGraph(f.state.graphView.autoRoutes['A=>B'].controlPoints),plainGraph(edge.controlPoints));
  assert.equal(f.plans,plans,'reopening saved geometry does not plan a new route');
  const second=await f.h.run('window.__collectSnapshot({scope:"all",embedPhotos:false})');
  assert.deepEqual(plainGraph(second.edges.find(item=>item.source==='A'&&item.target==='B').controlPoints),plainGraph(edge.controlPoints));
  assert.ok(cache['A=>B']);
});

test('graph route contract: filtered display and return to overview preserve the complete graph route cache',async()=>{
  const f=actualRouteScene();await f.drain(f.h.run('relayout(false)'));
  const original=f.geometry(),cache=plainGraph(f.state.graphView.autoRoutes),facts=JSON.stringify(f.state.lastGraph),plans=f.plans;
  f.h.run('state.graphView.visibleIds=new Set(["A","B"]);state.graphView.root="A";state.forceFit=false;renderGraph()');
  assert.equal(f.state.graph.getNodes().length,2);assert.deepEqual(f.routes()['A=>B'],original.routes['A=>B']);
  assert.deepEqual(plainGraph(f.state.graphView.autoRoutes),cache);assert.equal(f.plans,plans);
  await f.drain(f.h.element('#btnGraphOverview').onclick());f.attachCamera(f.state.graph);
  assert.equal(f.state.graph.getNodes().length,3);assert.deepEqual(f.geometry(),original,'returning to the complete display cannot erase accepted route geometry');
  assert.deepEqual(plainGraph(f.state.graphView.autoRoutes),cache);assert.equal(f.plans,plans);
  assert.equal(JSON.stringify(f.state.lastGraph),facts);
});

test('graph performance contract: quality-only measurements equal full scene inputs without planner resampling',()=>{
  const f=actualRouteScene(),quality=f.env.FundsGraphRouteQuality,measure=f.env.graphTextSize;
  const samples=[],inputs=[];let textReads=0;
  f.env.graphTextSize=(...args)=>{textReads++;return measure(...args)};
  f.env.FundsGraphRouteQuality={...quality,sample:(path,options)=>{samples.push(options?.steps);return quality.sample(path,options)},
    evaluate:input=>{inputs.push(plainGraph(input));return quality.evaluate(input)}};
  for(const zoom of [.2,1.7]){
    f.state.graph.zoomTo(zoom);f.applyLabels();samples.length=0;textReads=0;
    const full=f.h.run('graphRouteScene()');
    assert.equal(samples.filter(steps=>steps===24).length,full.renderedEdges,'planner preparation samples each actual relationship once');
    assert.ok(textReads>=full.edges.length,'full planning prepares relationship text geometry');
    samples.length=0;textReads=0;
    const lean=f.h.run('graphRouteScene(undefined,{planner:false})');
    assert.deepEqual(plainGraph(lean.nodes),plainGraph(full.nodes));
    assert.deepEqual(plainGraph(lean.qualityEdges),plainGraph(full.qualityEdges));
    assert.equal(lean.edges.length,0,'quality-only extraction does not allocate planner edge candidates');
    assert.equal(samples.length,0,'actual quality avoids the redundant 24-point planner sampling');
    assert.equal(textReads,0,'real visible text boxes do not need fresh planner font measurement');
    f.h.run('graphActualRouteQuality()');
    assert.deepEqual(inputs.at(-1),plainGraph({nodes:full.nodes,edges:full.qualityEdges,maxChecks:120000}));
    assert.equal(samples.length,0,'the default quality API also uses its quality-only scene');
  }
});

test('graph performance contract: cached single-route trials retain complete fresh geometry including opposing labels',()=>{
  const f=actualRouteScene(),quality=f.env.FundsGraphRouteQuality;
  f.state.lastGraph.edges.push({source:'B',target:'A',ids:[4],cnt:1,total:'0.01',currencies:['CNY'],
    amount_totals:[{currency:'CNY',amount:'0.01'}]});
  f.state.lastGraph.nodes.push({account:'D',name:'合成 D',in_cnt:0,out_cnt:0});
  f.state.positions.D={x:700,y:100};
  f.state.lastGraph.tracked_ids.push(4);f.state.tracked.add(4);
  f.h.run('renderGraph();savePositions()');
  const baselineRoutes=f.routes();
  f.env.FundsGraphRouting={...f.kernel,plan:()=>({routes:[
    {id:'A=>B',source:'A',target:'B',type:'quadratic',controlPoints:[{x:300,y:100}],curvePolicy:'obstacle-aware'},
    {id:'B=>A',source:'B',target:'A',type:'quadratic',controlPoints:[{x:300,y:110}],curvePolicy:'obstacle-aware'}],stats:{synthetic:true}})};
  const inputs=[],results=[];let baseline;
  f.env.FundsGraphRouteQuality={...quality,evaluate:input=>{
    const fresh=f.h.run('graphRouteScene(undefined,{planner:false})');
    assert.deepEqual(plainGraph(input),plainGraph({nodes:fresh.nodes,edges:fresh.qualityEdges,maxChecks:120000}),
      'every incremental assessment includes all current node boxes, unchanged paths and the current changed-edge path/label');
    inputs.push(plainGraph(input));const result=quality.evaluate(input);results.push(plainGraph(result));
    if(inputs.length===1)baseline=result;
    // Force the whole-batch guard to reject, exercising the real single-route
    // recovery path without changing the genuine per-route geometry results.
    return inputs.length===2?{...result,counts:{...result.counts,edgeNode:baseline.counts.edgeNode+1}}:result;
  }};
  f.h.run('improveGraphRoutes({fit:false})');
  assert.equal(f.state.graphView.routeQuality.actualTrials,2,'both opposing candidates enter the bounded recovery loop');
  assert.ok(inputs.length>=5,'baseline, batch, both single-route assessments and final full validation run');
  const firstTrial=inputs[2],secondTrial=inputs[3];
  assert.notDeepEqual(firstTrial.edges.find(edge=>edge.id==='A=>B').path,inputs[0].edges.find(edge=>edge.id==='A=>B').path);
  assert.deepEqual(secondTrial.edges.find(edge=>edge.id==='A=>B'),firstTrial.edges.find(edge=>edge.id==='A=>B'),
    'an accepted earlier path and label remain obstacles during the opposing trial');
  assert.notDeepEqual(secondTrial.edges.find(edge=>edge.id==='B=>A').path,firstTrial.edges.find(edge=>edge.id==='B=>A').path);
  assert.ok(results[3].counts.labelOverlap>results[2].counts.labelOverlap||results[3].counts.edgeLabel>results[2].counts.edgeLabel,
    'the opposing proposal is assessed against the prior accepted label and path rather than a stale baseline');
  assert.equal(f.edge('A=>B').model.curvePolicy,'obstacle-aware');
  assert.deepEqual(f.routes()['B=>A'],baselineRoutes['B=>A'],'the colliding opposing candidate is rejected and the original compact route is retained');
});

function traceAutomaticPaint(f,initial=true){
  const graph=f.state.graph,get=graph.get.bind(graph),events=[];
  let enabled=initial,paints=0;
  graph.get=name=>name==='autoPaint'?enabled:get(name);
  graph.setAutoPaint=value=>{enabled=!!value;events.push({kind:'flag',value:enabled})};
  graph.paint=()=>{paints++;events.push({kind:'paint',value:enabled})};
  return{events,get enabled(){return enabled},get paints(){return paints},
    reset(value){enabled=value;paints=0;events.length=0}};
}

test('background route transaction keeps the old complete canvas until one layout-and-route history commit',async()=>{
  const f=actualRouteScene({holdRoutes:true}),graph=f.state.graph;
  for(const node of graph.getNodes()){node.model.x+=70;node.model.y-=25;}
  f.h.run('refreshGraphRoutesAndLabels();savePositions()');
  const before=f.geometry(),facts=JSON.stringify(f.state.lastGraph),trace=traceAutomaticPaint(f);
  const pending=f.h.element('#btnRelayout').onclick();for(let pass=0;pass<8;pass++)await new Promise(resolve=>setImmediate(resolve));
  const route=f.workers.find(worker=>worker.message?.task==='routes');
  assert.ok(route,'complex node layout plans routes in a separate background task');
  assert.equal(f.workers.length,1,'layout and routes share the same serial Worker lifetime');
  assert.deepEqual(f.tasks.map(task=>task.message.task),['layout','routes']);
  assert.ok(f.tasks[1].message.taskId>f.tasks[0].message.taskId);
  assert.equal(f.workers.filter(worker=>!worker.terminated).length,1,'a job has only one active task Worker');
  assert.equal(f.h.run('layoutJob.phase'),'computing');assert.equal(f.history.status().busy,true);assert.equal(f.history.status().count,0);
  assert.deepEqual(f.geometry(),before,'awaiting route proposals never exposes temporary candidate coordinates, paths or snapshot geometry');
  const obsolete=workerReply(f.env,f.tasks[0].message,{result:{nodes:f.tasks[0].message.input.nodes.map(node=>({id:node.id,x:-9999,y:-9999}))}});
  f.tasks[0].onmessage({data:obsolete});f.tasks[1].onmessage({data:obsolete});
  for(let pass=0;pass<4;pass++)await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(f.geometry(),before);assert.equal(f.h.run('layoutJob.phase'),'computing','a previous task packet cannot finish the route task');
  assert.equal(trace.paints,0,'the retained graph needs no candidate paint');
  assert.equal(await f.env.window.__collectSnapshot({scope:'all'}),null,'save/export snapshot cannot inspect an unfinished layout transaction');
  const exportsBefore=f.h.requests.length;f.h.element('#exportFmt').value='html';await f.h.element('#btnExportPng').onclick();
  assert.equal(f.h.requests.length,exportsBefore,'export is mutually exclusive with the background transaction');
  assert.equal(f.h.downloads.length,0);
  graph.zoomTo(.87);graph.translate(21,-12);const intended=f.camera;
  route.deliver();await f.drain(pending);
  assert.equal(f.h.run('layoutJob'),null);assert.equal(f.history.status().busy,false);assert.equal(f.history.status().count,1);
  assert.equal(f.state.graphView.idleLayoutWorker,route);assert.equal(route.onmessage,null);
  assert.equal(trace.paints,1,'layout and its route proposal share one atomic final paint');
  assert.deepEqual(f.camera,intended);assert.equal(JSON.stringify(f.state.lastGraph),facts);
  const accepted=f.geometry();await f.drain(f.h.element('#btnUndo').onclick());assert.deepEqual(f.geometry(),before);
  await f.drain(f.h.element('#btnRedo').onclick());assert.deepEqual(f.geometry(),accepted);
});

test('background route cancellation releases its waiter and an old route reply cannot roll back a later layout',async()=>{
  const f=actualRouteScene({holdRoutes:true}),before=f.geometry();
  const pending=f.h.element('#btnRelayout').onclick();for(let pass=0;pass<8;pass++)await new Promise(resolve=>setImmediate(resolve));
  const old=f.workers.find(worker=>worker.message?.task==='routes');assert.ok(old);
  f.h.run('cancelGraphLayout()');await f.drain(pending);
  assert.equal(old.terminated,true);assert.equal(f.h.run('layoutJob'),null);assert.equal(f.history.status().busy,false);
  assert.equal(f.history.status().count,0);assert.deepEqual(f.geometry(),before);
  for(const node of f.state.graph.getNodes()){node.model.x+=70;node.model.y-=25;}
  f.h.run('refreshGraphRoutesAndLabels();savePositions()');
  const latest=f.h.element('#btnRelayout').onclick();for(let pass=0;pass<8;pass++)await new Promise(resolve=>setImmediate(resolve));
  const current=f.workers.filter(worker=>worker.message?.task==='routes').at(-1);assert.notEqual(current,old);
  current.deliver();await f.drain(latest);const accepted=f.geometry(),camera=f.camera;
  old.deliver();for(let pass=0;pass<4;pass++)await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(f.geometry(),accepted);assert.deepEqual(f.camera,camera,'retired route tasks have no commit or rollback authority');
  assert.equal(f.history.status().count,1);assert.equal(f.history.status().busy,false);assert.equal(f.h.run('layoutJob'),null);
});

test('graph performance contract: nested fit labels and viewport restore disabled painting while standalone commands draw',()=>{
  const f=actualRouteScene(),trace=traceAutomaticPaint(f),graph=f.state.graph,view=f.state.graphView;
  f.env.applyGraphViewport=f.applyViewport;
  view.clipIndex={containsAll:()=>false,query:()=>({nodeIds:new Set(['A','B']),edgeKeys:new Set(['A=>B'])}),include(){}};
  const actions=[['fit',()=>f.fitGraph()],['labels',()=>f.applyLabels()],
    ['viewport',()=>f.applyViewport(false)],['viewport with labels',()=>f.applyViewport(true)]];
  for(const [name,action]of actions)for(const standalone of [false,true]){
    view.clipResult=null;view.clipped.clear();
    for(const item of [...graph.getNodes(),...graph.getEdges()])item.getContainer().show();
    trace.reset(standalone);action();
    assert.equal(trace.enabled,standalone,`${name} restores the exact entering autoPaint state`);
    assert.equal(trace.paints,standalone?1:0,`${name} ${standalone?'standalone paints once':'inside an outer disabled batch cannot paint'}`);
    if(!standalone)assert.equal(trace.events.some(event=>event.kind==='flag'&&event.value),false,`${name} never enables drawing before the outer batch commits`);
  }
});

for(const fixture of ['simple','complex','narrow-complex'])test(`graph performance contract: ${fixture} public relayout records one edit and performs one final paint`,async()=>{
  const f=fixture==='simple'?contractScene(contractTopologies.branch):actualRouteScene();
  if(fixture==='narrow-complex'){
    f.h.element('#graphContainer').clientWidth=420;f.h.element('#graphContainer').clientHeight=320;
  }
  // Force real geometry work; a repeated layout already at its target is
  // correctly allowed to skip unchanged G6 updates.
  for(const node of f.state.graph.getNodes()){node.model.x+=70;node.model.y-=25;}
  f.h.run('refreshGraphRoutesAndLabels();savePositions()');
  const trace=traceAutomaticPaint(f),facts=JSON.stringify(f.state.lastGraph),reads=[];
  const update=f.state.graph.updateItem.bind(f.state.graph);
  f.state.graph.updateItem=(item,changes)=>{reads.push({enabled:trace.enabled,key:item.model.id||`${item.model.source}=>${item.model.target}`});return update(item,changes)};
  await f.drain(f.h.element('#btnRelayout').onclick());
  assert.equal(f.history.status().count,1);assert.equal(f.history.status().busy,false);assert.equal(f.h.run('layoutJob'),null);
  assert.equal(trace.paints,1,'nested labels, fit and route assessment share one outer final draw');
  assert.equal(trace.enabled,true);assert.deepEqual(trace.events.filter(event=>event.kind==='flag'&&event.value),[{kind:'flag',value:true}],
    'drawing is restored only at the final outer commit');
  assert.ok(reads.length>0);assert.ok(reads.every(read=>read.enabled===false),'every geometry update occurs with canvas autoDraw disabled');
  assert.equal(JSON.stringify(f.state.lastGraph),facts);assert.ok(Object.values(f.coords()).every(point=>Number.isFinite(point.x)&&Number.isFinite(point.y)));
});

test('graph label refresh contract: temporarily hidden admitted text restores its inline path gap and fixed node anchor',()=>{
  const f=actualRouteScene(),graph=f.state.graph,item=f.edge('A=>B'),node=graph.findById('C');
  f.applyLabels();assert.equal(f.state.graphView.selectedEdge,null,'the regression exercises an ordinary unselected relationship');
  const text=item.getContainer().children.find(shape=>shape.name==='text-shape');
  const nodeText=node.getContainer().children.find(shape=>shape.name==='text-shape');
  const oldPath=item.getContainer().children.find(shape=>shape.name==='funds-edge-visible-path');
  assert.ok(oldPath?.getClip());const originalLabel=item.model.label,originalMembership=[...f.state.graphView.labeledEdges];
  text.set('ori-visibility',true);text.hide();oldPath.hide();
  nodeText.set('ori-visibility',true);nodeText.hide();
  let measurements=0;const collide=f.env.avoidGraphLabelCollisions;
  f.env.avoidGraphLabelCollisions=policy=>{measurements++;return collide(policy)};
  f.applyLabels();
  const visible=item.getContainer().children.find(shape=>shape.name==='funds-edge-visible-path');
  assert.equal(text.isVisible(),true);assert.equal(nodeText.isVisible(),true);
  assert.equal(item.model.label,originalLabel);assert.equal(text.attr('text'),item.model.fundsLabel,'the complete exact amount remains visible');
  assert.deepEqual([...f.state.graphView.labeledEdges],originalMembership,'temporary renderer visibility never changes label policy membership');
  assert.ok(visible?.isVisible());assert.ok(visible?.getClip(),'showing a label must recreate its real transparent path gap in the same refresh');
  assert.notEqual(visible,oldPath,'the path restored while hidden is replaced with a fresh clipped visible copy');
  assert.equal(item.getKeyShape().attr('opacity'),0);assert.equal(item.getKeyShape().capture,true);
  assert.deepEqual(plainGraph(visible.attr('path')),plainGraph(item.getKeyShape().attr('path')));
  const point=item.getKeyShape().getPoint(.5),box=text.getBBox();
  assert.ok(Math.abs((box.minX+box.maxX)/2-point.x)<1e-7&&Math.abs((box.minY+box.maxY)/2-point.y)<1e-7);
  const body=node.getKeyShape().getCanvasBBox(),label=nodeText.getCanvasBBox();
  assert.ok(Math.abs(label.minY-body.maxY-6)<1e-7);assert.ok(Math.abs(label.minX+label.maxX-body.minX-body.maxX)<1e-7);
  assert.equal(measurements,2,'a visibility repair retains the final real-geometry collision measurement');
  assert.equal([...item.getContainer().children,...node.getContainer().children].some(shape=>['text-bg-shape','funds-label-leader'].includes(shape.name)),false);
  f.applyLabels();assert.equal(item.getContainer().children.find(shape=>shape.name==='funds-edge-visible-path'),visible,'a later stable refresh retains the repaired path');
});

test('graph label refresh contract: a stable refresh measures collisions once and includes each final item once',()=>{
  const f=actualRouteScene();f.applyLabels();
  const collide=f.env.avoidGraphLabelCollisions,edgeSync=f.env.syncGraphEdgeLabel,geometry=f.env.FundsGraphLabelGeometry;
  let measurements=0,edgeSyncs=0,nodePlacements=0;const included=[];
  f.env.avoidGraphLabelCollisions=policy=>{measurements++;return collide(policy)};
  f.env.syncGraphEdgeLabel=(...args)=>{edgeSyncs++;return edgeSync(...args)};
  f.env.FundsGraphLabelGeometry={...geometry,placeNode:(...args)=>{nodePlacements++;return geometry.placeNode(...args)}};
  f.state.graphView.clipIndex={include:(kind,key)=>included.push([kind,key])};
  const before=f.geometry();f.applyLabels();
  assert.equal(measurements,1,'unchanged labels reuse the first actual collision policy');
  assert.equal(nodePlacements,f.state.graph.getNodes().length,'stable node geometry is synchronized only in the initial common pass');
  assert.equal(edgeSyncs,f.state.graph.getEdges().length,'stable relationships avoid duplicate second and final synchronization');
  const expected=[...[...f.state.graphView.policy.nodeIds].map(key=>['node',key]),...[...f.state.graphView.policy.edgeKeys].map(key=>['edge',key])];
  assert.deepEqual(included,expected,'the final unified viewport-index loop includes each admitted item exactly once');
  assert.deepEqual(f.geometry(),before);
});

test('graph label refresh contract: recreating an admitted label retains a final measured collision pass',()=>{
  const f=actualRouteScene(),item=f.edge('A=>B');f.applyLabels();
  const oldText=item.getContainer().children.find(shape=>shape.name==='text-shape'),canonical=item.model.fundsLabel;
  assert.ok(f.state.graphView.labeledEdges.has('A=>B'));
  f.state.graph.updateItem(item,{label:undefined});
  let measurements=0;const collide=f.env.avoidGraphLabelCollisions;
  f.env.avoidGraphLabelCollisions=policy=>{measurements++;return collide(policy)};
  f.applyLabels();
  const text=item.getContainer().children.find(shape=>shape.name==='text-shape');
  assert.notEqual(text,oldText);assert.equal(text.attr('text'),canonical);assert.equal(item.model.label,canonical);
  assert.equal(measurements,2,'recreated text is measured again after G6 supplies its real glyph envelope');
  assert.ok(item.getContainer().children.find(shape=>shape.name==='funds-edge-visible-path')?.getClip());
  assert.equal(item.getKeyShape().attr('opacity'),0);
  measurements=0;f.applyLabels();assert.equal(measurements,1,'the following stable refresh returns to one actual collision pass');
});

test('background route scheduling follows measured planner work, not graph size or incomplete detection alone',()=>{
  const h=harness('app.js'),state=h.run('state');
  const evaluate=(n,e,kind='dagre')=>h.run(`graphBackgroundRoutingWorthwhile({kind:${JSON.stringify(kind)},nodes:Array(${n}),edges:Array(${e})})`);
  assert.equal(evaluate(1000,990),false,'an unmeasured large graph must not pay for speculative routing');
  state.graphView.routeWork={dagre:{planningMs:0}};
  assert.equal(evaluate(1000,990),false,'a real baseline that skipped planning must not repeatedly pre-plan');
  state.graphView.routeWork.dagre.planningMs=49.9;
  assert.equal(evaluate(400,458),false);
  state.graphView.routeWork.dagre.planningMs=50;
  assert.equal(evaluate(400,458),true,'expensive real planning can run before the next commit');
  assert.equal(evaluate(400,458,'force'),false,'another layout has not established that cost');
  assert.equal(evaluate(18,47),false,'small scenes retain the lower overhead synchronous path');
  state.graphView.routeWork.force={planningMs:500};
  assert.equal(evaluate(400,458,'force'),false,'a moving force baseline must reach its fresh real gate before planning');
});

function annotationPointerFixture() {
  const f=graphInteractions({geometry:true}),container=f.h.element('#graphContainer'),events=new Map();
  container.addEventListener=(name,callback)=>{if(!events.has(name))events.set(name,new Set());events.get(name).add(callback);};
  container.removeEventListener=(name,callback)=>events.get(name)?.delete(callback);
  container.setPointerCapture=()=>{};container.releasePointerCapture=()=>{};
  f.h.run('initGraph()');const graph=f.seed();
  f.state.graphInited=true;f.state.presentation=f.env.FundsGraphPresentation.create({annotations:[{id:'SYN-A',text:'合成标注\n第二行',anchor:'above'}]});
  return {...f,container,graph,events,pointer(name,x=0,y=-40,pointerId=7){for(const fn of [...events.get(name)||[]])fn({
    type:name,button:0,pointerId,clientX:x,clientY:y,preventDefault(){},stopPropagation(){},stopImmediatePropagation(){}});}};
}

test('annotations: rebuilding the graph removes every captured pointer listener before installing one replacement',()=>{
  const f=annotationPointerFixture();
  const counts=new Map([...f.events].map(([name,listeners])=>[name,listeners.size]));
  for(let pass=0;pass<4;pass++) {
    f.h.run('initGraph()');f.seed();
    for(const name of ['pointerdown','pointermove','pointerup','pointercancel','lostpointercapture'])assert.equal(f.events.get(name)?.size,counts.get(name),`${name} after rebuild ${pass}`);
  }
});

for(const zoom of [.25,1,4])test(`annotations: above-node gap stays ten screen pixels at zoom ${zoom}`,()=>{
  const f=annotationPointerFixture();f.graph.zoom=zoom;
  const note=f.state.presentation.annotations[0],item=f.graph.findById('SYN-A');
  const y=f.env.annotationAnchorY(item,note),body=f.env.graphNodeBodyBounds(item);
  assert.ok(Math.abs((item.model.y+y)*zoom-(body.minY-10))<1e-8);
  const before=JSON.stringify(f.state.presentation);f.env.renderNodeAnnotations(false);
  assert.equal(JSON.stringify(f.state.presentation),before,'zoom placement never rewrites saved offsets');
});

test('annotations: viewport rebuild and later label passes include multiline annotation bounds',()=>{
  const f=annotationPointerFixture();
  f.state.lastGraph={nodes:[{account:'SYN-A'},{account:'SYN-B'}],edges:[]};
  f.state.graphView.index=f.env.FundsGraphView.createIndex(f.state.lastGraph);
  f.state.presentation.annotations[0].dx=400;
  const index=f.env.FundsGraphViewport.build;
  let nodes;
  f.env.FundsGraphViewport={...f.env.FundsGraphViewport,build:value=>{nodes=value.nodes;return index(value);}};
  f.rebuildViewport();
  assert.ok(nodes.find(node=>node.id==='SYN-A').bounds.maxX>390,'the displaced note is part of the indexed node envelope');
});

// Independent Bernstein evaluation and a bounded nearest-point search verify
// real curve membership without consulting the application's path sampler.
function labelCurvePoint(points,t) {
  const v=1-t;
  if(points.length===3)return {x:v*v*points[0].x+2*v*t*points[1].x+t*t*points[2].x,
    y:v*v*points[0].y+2*v*t*points[1].y+t*t*points[2].y};
  return {x:v*v*v*points[0].x+3*v*v*t*points[1].x+3*v*t*t*points[2].x+t*t*t*points[3].x,
    y:v*v*v*points[0].y+3*v*v*t*points[1].y+3*v*t*t*points[2].y+t*t*t*points[3].y};
}
function labelCurveDistance(points,anchor) {
  const distance=t=>{const p=labelCurvePoint(points,t);return Math.hypot(p.x-anchor.x,p.y-anchor.y)};
  let best=0;
  for(let i=1;i<=256;i++)if(distance(i/256)<distance(best/256))best=i;
  let lo=Math.max(0,(best-1)/256),hi=Math.min(1,(best+1)/256);
  for(let i=0;i<80;i++){
    const a=lo+(hi-lo)/3,b=hi-(hi-lo)/3;
    if(distance(a)<distance(b))hi=b;else lo=a;
  }
  return distance((lo+hi)/2);
}

for(const kind of ['quadratic','cubic','loop'])test(`manual ${kind} label stays on its actual curve after drag, zoom, hiding and geometry updates`,()=>{
  const f=graphInteractions({geometry:true}),graph=f.state.graph,key=kind==='loop'?'A=>A':'A=>B';
  f.state.lastGraph={nodes:[{account:'A',name:'合成 A',in_cnt:1,out_cnt:1},{account:'B',name:'合成 B',in_cnt:1,out_cnt:1}],
    edges:[{source:'A',target:kind==='loop'?'A':'B',cnt:1,ids:[1],amount_totals:[{currency:'CNY',amount:'10000000000000000.01'}]}],tracked_ids:[1]};
  f.state.positions={A:{x:0,y:0},B:{x:2000,y:0}};
  f.state.edgeType='quadratic';f.state.edgeTypes={[key]:kind==='loop'?'loop':'quadratic'};
  f.state.edgeBendPos={[key]:[{x:1000,y:5000}]};f.state.edgeLabelPos={[key]:.507};f.state.graphView.labelMode='all';
  const facts=JSON.stringify(f.state.lastGraph);f.h.run('renderGraph()');
  const item=graph.getEdges()[0],shape=item.getKeyShape(),attr=shape.attr.bind(shape);
  let curve=kind==='quadratic'?[{x:0,y:0},{x:1000,y:5000},{x:2000,y:0}]
    :kind==='cubic'?[{x:0,y:0},{x:0,y:9000},{x:2000,y:9000},{x:2000,y:0}]
      :[{x:0,y:0},{x:5000,y:5000},{x:-5000,y:5000},{x:0,y:0}];
  if(kind!=='quadratic'){
    shape.attr=(...args)=>args.length===1&&args[0]==='path'
      ?[['M',curve[0].x,curve[0].y],['C',...curve.slice(1).flatMap(p=>[p.x,p.y])]]:attr(...args);
    shape.getPoint=t=>labelCurvePoint(curve,t);
  }
  const check=()=>{
    const text=item.getContainer().children.find(s=>s.name==='text-shape'),box=text.getBBox();
    const anchor={x:(box.minX+box.maxX)/2,y:(box.minY+box.maxY)/2};
    const distance=labelCurveDistance(curve,anchor);
    assert.ok(distance<1e-7,`${kind} label is ${distance} world units away from its own curve`);
    assert.equal(text._fundsLabelOffset,undefined);assert.ok(item.getContainer().children.some(s=>s.name==='funds-edge-visible-path'));
    assert.equal(item.getContainer().children.some(s=>s.name==='funds-label-leader'),false);
    assert.equal(text.attr('text'),item.model.fundsLabel);assert.equal(JSON.stringify(f.state.lastGraph),facts);
  };
  f.applyLabels();check();assert.equal(f.state.edgeLabelPos[key],.507);
  for(const zoom of [.1,4,.5]){graph.zoom=zoom;graph.emit('viewportchange',{action:'zoom'});f.applyLabels();check();assert.equal(f.state.edgeLabelPos[key],.507);}
  const pointer=labelCurvePoint(curve,.519),text=item.getContainer().children.find(s=>s.name==='text-shape');
  graph.emit('edge:mousedown',{item,target:text,preventDefault(){}});
  f.event('mousemove',{clientX:pointer.x*graph.zoom,clientY:pointer.y*graph.zoom,buttons:1});f.event('mouseup');check();
  const ratio=f.state.edgeLabelPos[key];assert.ok(ratio>0&&ratio<1);assert.notEqual(ratio,.519,'stored position remains an arc-length ratio, not a curve parameter');
  f.state.graphView.labelMode='focus';f.applyLabels();assert.equal(item.model.label,undefined);
  f.state.graphView.labelMode='all';f.applyLabels();check();assert.equal(f.state.edgeLabelPos[key],ratio);
  if(kind==='quadratic'){
    graph.updateItem(graph.findById('A'),{x:-300,y:120});graph.emit('node:dragstart');
    graph.emit('node:drag',{item:graph.findById('A')});graph.emit('dragnodeend');
    curve=[{x:-300,y:120},curve[1],curve[2]];
  }else curve=curve.map(p=>({x:p.x+170,y:p.y-90}));
  f.applyLabels();check();assert.equal(f.state.edgeLabelPos[key],ratio);
});

test('manual polyline labels preserve exact segment positions and legacy sampled-path fallback',()=>{
  const f=graphInteractions({geometry:true}),graph=f.state.graph;
  graph.data({nodes:[{id:'A',x:0,y:0},{id:'B',x:300,y:100}],
    edges:[{source:'A',target:'B',type:'polyline',controlPoints:[{x:100,y:0},{x:100,y:100}],label:'CNY 0.01'}]});
  const item=graph.getEdges()[0];f.state.edgeLabelPos={'A=>B':.5};
  f.env.syncGraphEdgeLabel(item);
  const box=item.getContainer().children.find(s=>s.name==='text-shape').getBBox();
  assert.equal((box.minX+box.maxX)/2,100);assert.equal((box.minY+box.maxY)/2,100);
  assert.deepEqual(plainGraph(f.h.run('ptAtRatio({pts:[{x:2,y:4},{x:8,y:12}],acc:[0,10],total:10},.25)')),{x:3.5,y:6});
  const shape=item.getKeyShape(),attr=shape.attr.bind(shape);
  shape.attr=(...args)=>args.length===1&&args[0]==='path'?[['M',0,0],['Q',1,2]]:attr(...args);
  f.env.invalidLabelEdge=item;
  assert.equal(f.h.run('edgePts(invalidLabelEdge)'),null,'an incomplete curve cannot provide a fabricated anchor');
});
