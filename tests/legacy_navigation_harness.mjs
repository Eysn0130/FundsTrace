import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const root = fileURLToPath(new URL('../', import.meta.url));
const cases = [{ id: 1, name: 'Synthetic A' }, { id: 2, name: 'Synthetic B' }, { id: 3, name: 'Synthetic C' }];
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((done, fail) => { resolve = done; reject = fail; }); return { promise, resolve, reject }; };
const response = payload => ({ ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => payload, text: async () => JSON.stringify(payload), blob: async () => new Blob(['synthetic']) });
const analysisResult = (algorithms = ['cycles'], extra = {}) => ({ case_id: 1, algorithms,
  subjects: [], source_snapshot: { flows: 0, accounts: 0 },
  ...(algorithms.includes('cycles') ? { cycles: { count: 0, cycles: [], window_days: 7 } } : {}),
  ...(algorithms.includes('fast_flows') ? { fast_flows: { count: 0, pairs: [], window_minutes: 30, tolerance: .25 } } : {}),
  ...(algorithms.includes('outliers') ? { outliers: { count: 0, outliers: [], sigma: 2.5 } } : {}),
  ...(algorithms.includes('paths') ? { paths: { count: 0, paths: [], src: 'SYN-SOURCE', dst: 'SYN-TARGET', max_hops: 4 } } : {}),
  ...extra });

function layoutWorkerResponse(h, message, nodes) {
  assert.equal(message.protocol, 1); assert.equal(message.task, 'layout');
  assert.ok(message.requestId != null && message.taskId != null);
  const result = { nodes }, quality = h.run('FundsGraphLayoutQuality.evaluate')(message.input, result,
    { fitViewport: h.run('FundsGraphLayout.fitViewport') });
  return { protocol: message.protocol, requestId: message.requestId, taskId: message.taskId, task: message.task, result, quality };
}

async function waitForLayoutCommit(h) {
  // relayout dispatches its transaction. A Worker reply resumes calculation
  // and commit in microtasks; awaiting dispatch alone does not await commit.
  for (let pass = 0; pass < 8 && h.run('layoutJob!==null'); pass++) await flush();
  assert.equal(h.run('layoutJob'), null, 'the complete layout transaction must settle before inspecting committed geometry');
}

export function harness(file, url = 'http://127.0.0.1:8300/', stored = '1', extras = {}) {
  const elements = new Map(), downloads = [], objectUrls = [], requests = [], storage = new Map([['fund_case', stored]]);
  const timers = [], frames = [];
  let document;
  const requestFrame = callback => { frames.push({callback,cancelled:false,ran:false}); return frames.length; };
  const cancelFrame = id => { if (frames[id-1]) frames[id-1].cancelled=true; };
  const location = new URL(url);
  const element = key => {
    if (elements.has(key)) return elements.get(key);
    const canvasCalls = [];
    const canvasContext = {};
    for (const name of ['translate','rotate','fillText','clearRect','drawImage','fillRect','strokeRect','beginPath','rect','arc','moveTo','fill']) {
      canvasContext[name] = (...args) => canvasCalls.push([name,...args]);
    }
    canvasContext.measureText=text=>({width:String(text).length*6,actualBoundingBoxAscent:9,actualBoundingBoxDescent:2});
    const node = { value: '', checked: false, disabled: false, innerHTML: '', textContent: '', style: {}, dataset: {}, href: '', download: '',
      width:200,height:120,canvasCalls,get ownerDocument(){return document},
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      addEventListener() {}, removeEventListener() {},setAttribute() {},removeAttribute() {},toggleAttribute() {},querySelectorAll() { return []; },
      querySelector(selector) { return element(String(key) + ' ' + selector); }, contains(other) { return other === this; }, appendChild() {}, remove() {}, focus() {}, select() {},
      getBoundingClientRect(){return {left:0,top:0,width:this.width,height:this.height}},
      getContext() { return canvasContext; }, toDataURL() { return 'data:image/png;base64,c3ludGhldGlj'; },
      click() { if (this.download || this.href) downloads.push({ href: this.href, filename: this.download }); },
      showModal() { this.open = true; Promise.resolve().then(() => this.close('confirm')); },
      close(value = '') { this.returnValue = value; this.open = false; this.onclose?.(); },
    };
    elements.set(key, node); return node;
  };
  element('#caseSelect').value = '1'; element('#caseSel').value = '1';
  element('#analysisLink').href = '/analysis'; element('#backToGraph').href = '/';
  element('#exportFmt').value = 'png';
  for (const [key, value] of Object.entries({ '#ffWin': '30', '#ffTol': '25', '#cycDays': '7', '#subjects': '', '#ctxX': '5' })) element(key).value = value;
  const algorithms = extras.algorithms || [{ checked: true, value: 'cycles' }];
  let fetcher = async path => response(path === '/api/cases' ? cases : { flows: 0, accounts: 0 });
  class TestURL extends URL { static createObjectURL(blob) { objectUrls.push({url:'blob:synthetic',blob}); return 'blob:synthetic'; } static revokeObjectURL() {} }
  document = { querySelector: element, getElementById: id => element('#' + id), querySelectorAll: selector => selector === '.alg' ? algorithms : [],
    createElement: kind => element(Symbol(kind)), body: element('body'), addEventListener() {}, };
  const window = { location, history: { replaceState(_state, _title, value) { location.href = new URL(value, location).href; } }, addEventListener() {},removeEventListener(){},dispatchEvent(){},
    requestAnimationFrame:requestFrame,cancelAnimationFrame:cancelFrame };
  const sandbox = { window, document, localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, String(value)), removeItem: key => storage.delete(key) },
    ...extras, URL: TestURL, URLSearchParams, Blob, Event, performance, CustomEvent: class { constructor(type) { this.type = type; } }, atob:globalThis.atob,console,requestAnimationFrame:requestFrame,cancelAnimationFrame:cancelFrame,
    setTimeout(callback, delay = 0) { timers.push({ callback, delay, cancelled: false }); return timers.length; },
    clearTimeout(id) { if (timers[id - 1]) timers[id - 1].cancelled = true; },
    confirm: () => true, prompt: () => 'Synthetic new', alert() {},
    fetch: async (path, options = {}) => { requests.push({ path, options }); return fetcher(path, options); }, };
  const context = vm.createContext(sandbox);
  for (const helper of ['money.js', 'table-scroll.js', 'case-workspace.js', 'canvas-workspace.js', 'data-tools.js', 'main-accounts.js', 'workspace-camera.js', 'graph-relations.js', 'graph-presentation.js', 'graph-layout.js', 'graph-layout-quality.js', 'graph-routing.js', 'graph-route-quality.js', 'graph-view.js','graph-viewport.js','graph-minimap.js','graph-labels.js','graph-label-geometry.js','graph-export-geometry.js']) vm.runInContext(readFileSync(`${root}static/${helper}`, 'utf8'), context, { filename: helper });
  context.FundsGraphRelations = context.window.FundsGraphRelations;
  context.FundsGraphPresentation = context.window.FundsGraphPresentation;
  context.FundsGraphView = context.window.FundsGraphView;
  context.FundsGraphViewport = context.window.FundsGraphViewport;
  context.FundsGraphMinimap = context.window.FundsGraphMinimap;
  context.FundsGraphLabels = context.window.FundsGraphLabels;
  context.FundsGraphLabelGeometry = context.window.FundsGraphLabelGeometry;
  context.FundsGraphRouting = context.window.FundsGraphRouting;
  vm.runInContext(readFileSync(`${root}static/${file}`, 'utf8'), context, { filename: file });
  if (file === 'app.js') {
    // Keep the real shared command controller and app adapters. This graph
    // harness replaces only the separate naming surface with its established
    // prompt boundary; menu/form interaction has its own DOM/browser coverage.
    window.FundsCanvasWorkspace = { create: window.FundsCanvasWorkspace.create,
      createView: () => ({ ask: async action => action === 'delete' ? true : context.promptText(action),
        onState() {}, dismiss() {}, complete() {}, attach() {}, showStatus() {},
        failure: message => context.toast(message, 'err') }) };
    // Run the real case-bound bulk controller and adapter. This graph harness
    // supplies no file chooser or secondary form; those have dedicated tests.
    window.FundsDataTools = { create: window.FundsDataTools.create,
      createView: () => ({ show() {}, onState() {}, dismiss() {}, attach() {}, pickFile: async () => null }) };
    window.FundsMainAccountsView = { create: () => ({ show() {}, onState() {}, dismiss() {}, attach() {} }) };
  }
  if (file === 'app.js') vm.runInContext(`
    const installActualExportWorkspace = installExportWorkspace;
    installExportWorkspace = options => {
      globalThis.exportFixtureExecute = options.execute;
      return installActualExportWorkspace(options);
    };
    globalThis.sampleExportForFixture = format => {
      const context = caseContext(), graph = state.graph, view = state.graphView,
        revision = view.revision, layoutRevision = layoutSequence;
      return exportFixtureExecute(format, {context,
        isCurrent: () => isCurrentCase(context) && graph === state.graph && view === state.graphView
          && revision === view.revision && layoutRevision === layoutSequence,
        setPhase() {}});
    };
  `, context);
  return { element, downloads, objectUrls,requests, storage, location, timers,frames, run: code => vm.runInContext(code, context),
    sampleExport: format => { context.fixtureExportFormat = format; return vm.runInContext('sampleExportForFixture(fixtureExportFormat)', context); },
    flushFrames(){const pending=frames.filter(frame=>!frame.cancelled&&!frame.ran);for(const frame of pending){frame.ran=true;frame.callback()}},
    network: fn => { fetcher = fn; },
    bindApp() { vm.runInContext("promptText = async () => 'Synthetic new';", context); vm.runInContext('bindPhotoCrop = () => {}; initEdgeLabels = () => {}; refreshStats = async () => {}; loadGraph = async () => {}; refreshCanvasList = async () => {}; doSearch = async () => {}; bindUI();', context); },
  };
}

test('sidebar composition bounds include clipped accounts and curved paths but exclude accounts outside the current graph', () => {
  const h=harness('app.js');
  h.run(`
    const project=b=>({minX:b.minX*2+100,minY:b.minY*2+50,maxX:b.maxX*2+100,maxY:b.maxY*2+50});
    const item=(model,bounds)=>({getModel:()=>model,getContainer:()=>({getCanvasBBox:()=>project(bounds),get:name=>name==='visible'?false:[]}),
      getKeyShape:()=>({getPoint:()=>({x:450,y:-120})})});
    const a=item({id:'SYN-A',x:10,y:20,size:32,fundsLabel:'A'},{minX:-6,minY:4,maxX:26,maxY:36});
    const b=item({id:'SYN-B',x:900,y:50,size:20,fundsLabel:'B'},{minX:890,minY:40,maxX:910,maxY:60});
    const edge=item({source:'SYN-A',target:'SYN-B',fundsLabel:'0.01 CNY'},{minX:-80,minY:-200,maxX:940,maxY:200});
    state.lastGraph={nodes:[{account:'SYN-A'},{account:'SYN-B'},{account:'OUTSIDE',x:100000}],edges:[]};
    state.graph={getZoom:()=>2,getNodes:()=>[a,b],getEdges:()=>[edge],
      getPointByCanvas:(x,y)=>({x:(x-100)/2,y:(y-50)/2}),getCanvasByPoint:(x,y)=>({x:x*2+100,y:y*2+50})};
    state.graphView.clipResult={nodeIds:new Set(['SYN-A']),edgeKeys:new Set()};
  `);
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(graphWorkspaceBounds())')),
    {minX:-80,minY:-200,maxX:940,maxY:200});
  assert.equal(h.run('state.graphView.clipResult.nodeIds.size'),1,'bounds measurement never changes drawing membership');
  assert.equal(h.run('state.lastGraph.nodes.length'),3,'measurement never changes complete tracked facts');
});

test('sidebar preparation ends a cached drag before composition and unrelated shell gestures cancel only automatic composition', () => {
  const h=harness('app.js');
  h.run(`
    globalThis.shellCalls=[];globalThis.shellListener=null;globalThis.shellDragging=true;
    window.addEventListener=(name,fn)=>{if(name==='fundstrace:workspace-resize')shellListener=fn;};
    state.graphSelection={ids:new Set(['SYN-A','SYN-B']),status:()=>({dragging:shellDragging}),
      rebind:ids=>{shellCalls.push(['drag-end',...ids]);shellDragging=false;}};
    state.graphCamera={beginSidebar:detail=>shellCalls.push(['begin',detail.collapsed]),
      endSidebar:()=>shellCalls.push(['end']),cancelComposition:()=>shellCalls.push(['cancel'])};
    bindWorkspaceCamera();
    shellListener({detail:{reason:'sidebar',phase:'prepare',sidebarCollapsed:true}});
    shellListener({detail:{reason:'sidebar',phase:'start',sidebarCollapsed:true}});
    shellListener({detail:{reason:'sidebar',phase:'end',sidebarCollapsed:true}});
    shellListener({detail:{reason:'sidebar-width',phase:'start'}});
    shellListener({detail:{reason:'toolbar',phase:'start'}});
  `);
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(shellCalls)')),
    [['drag-end','SYN-A','SYN-B'],['begin',true],['end'],['cancel'],['cancel']]);
});

test('sidebar visibility ignores an unpainted label while the growth envelope still reserves its geometry', () => {
  const h=harness('app.js');
  h.run(`
    const body={minX:60,minY:80,maxX:100,maxY:120}, hiddenText={minX:-300,minY:120,maxX:500,maxY:160};
    const shape={get:name=>name==='name'?'text-shape':name==='visible'?false:undefined,getCanvasBBox:()=>hiddenText};
    const model={id:'SYN-A',x:80,y:100,size:40,fundsLabel:'Hidden synthetic long identity label',label:undefined};
    const item={getModel:()=>model,getKeyShape:()=>({getCanvasBBox:()=>body}),
      getContainer:()=>({getCanvasBBox:()=>({minX:-300,minY:80,maxX:500,maxY:160}),get:name=>name==='children'?[shape]:undefined})};
    state.graph={getNodes:()=>[item],getEdges:()=>[],getZoom:()=>1,
      getPointByCanvas:(x,y)=>({x,y}),getCanvasByPoint:(x,y)=>({x,y})};
  `);
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(graphWorkspaceBounds(false))')),{minX:60,minY:80,maxX:100,maxY:120});
  const envelope=JSON.parse(h.run('JSON.stringify(graphWorkspaceBounds())'));
  assert.ok(envelope.minX<=-300&&envelope.maxX>=500,'hidden text still constrains the growth target');
});

test('workspace resize fills the cleared pinned Canvas bitmap in the same frame and cannot touch an export or replacement graph', () => {
  const h=harness('app.js');
  h.run(`
    globalThis.G6={version:'4.8.19'};globalThis.resizePaintCalls=[];globalThis.resizeBitmap='blank';
    const canvas={get:name=>name==='el'?{tagName:'CANVAS'}:null,
      _clearFrame:()=>resizePaintCalls.push('cancel-queued-draw'),
      _drawAll:()=>{resizePaintCalls.push('draw-now');resizeBitmap='nodes';}};
    state.graph={get:name=>name==='canvas'?canvas:null,paint:()=>resizePaintCalls.push('async-paint')};
  `);
  assert.equal(h.run('paintWorkspaceResize(state.graph)'),true);
  assert.equal(h.run('resizeBitmap'),'nodes','the synchronous observer delivery must leave a filled bitmap');
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(resizePaintCalls)')),['cancel-queued-draw','draw-now']);
  h.run('state.graphView.exportDepth=1');
  assert.equal(h.run('paintWorkspaceResize(state.graph)'),false);
  h.run('state.graphView.exportDepth=0;G6.version="future"');
  assert.equal(h.run('paintWorkspaceResize(state.graph)'),false);
  h.run('G6.version="4.8.19"');
  assert.equal(h.run('paintWorkspaceResize({get:state.graph.get})'),false);
  assert.equal(h.run('resizePaintCalls.length'),2);
});

test('all available edge label preferences survive initialization, including currency', () => {
  const h = harness('app.js');
  h.storage.set('fund_edge_labels', '["money","time","summary","currency"]');
  h.run('initEdgeLabels()');
  assert.deepEqual(Array.from(h.run('[...state.edgeLabels]')), ['money','time','summary','currency']);
  h.run('saveEdgeLabels(); state.edgeLabels.clear(); initEdgeLabels()');
  assert.deepEqual(Array.from(h.run('[...state.edgeLabels]')), ['money','time','summary','currency']);
});

test('G6 deep link remains the selected case in analysis navigation', async () => {
  const h = harness('app.js', 'http://127.0.0.1:8300/?case_id=2', '1');
  await h.run('loadCases()');
  assert.equal(h.run('state.caseId'), 2);
  assert.equal(new URL(h.element('#analysisLink').href, h.location).searchParams.get('case_id'), '2');
  assert.equal(h.storage.get('fund_case'), '2');
});

test('a delayed deep-link canvas switch is revoked by a case A-B-A switch and consumes its URL target', async () => {
  const h = harness('app.js', 'http://127.0.0.1:8300/?case_id=1&canvas_id=7&focus_edge=17');
  await h.run('loadCases()');
  let dispatched = 0;
  h.element('#canvasSel').dispatchEvent = () => { dispatched += 1; };
  h.network(() => response([{id:7,name:'Synthetic dedicated loop'}]));
  await h.run('refreshCanvasList()');
  const opening = h.timers.findLast(timer => timer.delay === 0 && !timer.cancelled);
  assert.ok(opening, 'canvas opening is deferred until its selector is ready');
  h.run('selectCase(2);selectCase(1)'); opening.callback();
  assert.equal(dispatched, 0, 'an old case generation cannot dispatch a canvas change');
  assert.equal(h.run('pendingFocusEdge'), 0);
  assert.equal(h.location.searchParams.has('canvas_id'), false);
  assert.equal(h.location.searchParams.has('focus_edge'), false);
});

test('a cycle focus link selects its relationship and focuses both endpoint positions', async () => {
  const h = harness('app.js', 'http://127.0.0.1:8300/?case_id=1&focus_edge=1');
  await h.run('loadCases()'); installSyntheticHub(h, 3);
  h.run(`
    globalThis.deepLinkFocusCalls=[];
    state.graphLoading=false;
    state.graph.findById=id=>[...state.graph.getNodes(),...state.graph.getEdges()].find(item=>item.getModel().id===id);
    for(const item of state.graph.getNodes())item.getContainer().getCanvasBBox=()=>item.getKeyShape().getCanvasBBox();
    state.graphCamera={focus:async(point,options)=>{deepLinkFocusCalls.push({point,bounds:options.bounds});return {ok:true}}};
  `);
  assert.equal(await h.run('applyPendingFocusEdge()'), true);
  assert.equal(h.run('state.graphView.selectedEdge'), 'SYN-HUB=>SYN-00000');
  assert.equal(h.run('deepLinkFocusCalls.length'), 1, 'edge models have no x/y, so focus must use their endpoint nodes');
  const bounds = h.run('deepLinkFocusCalls[0].bounds');
  for (const point of h.run('state.graph.getNodes().slice(0,2).map(item=>item.getModel())')) {
    assert.ok(bounds.minX <= point.x && bounds.maxX >= point.x);
    assert.ok(bounds.minY <= point.y && bounds.maxY >= point.y, JSON.stringify({bounds, point}));
  }
  assert.equal(h.run('pendingFocusEdge'), 0);
  assert.equal(h.location.searchParams.has('focus_edge'), false);
});

test('a cycle focus link waits for the initial layout coordinates and its final fit', async () => {
  const tasks = [];
  class SyntheticWorker {
    postMessage(message) { tasks.push({worker:this, message}); }
    terminate() {}
  }
  const h = harness('app.js', 'http://127.0.0.1:8300/?case_id=1&focus_edge=1', '1', {Worker:SyntheticWorker});
  await h.run('loadCases()'); installSyntheticHub(h, 3);
  h.run(`
    globalThis.layoutFocusCalls=[];state.graphLoading=false;
    state.graph.findById=id=>[...state.graph.getNodes(),...state.graph.getEdges()].find(item=>item.getModel().id===id);
    for(const item of state.graph.getNodes())item.getContainer().getCanvasBBox=()=>item.getKeyShape().getCanvasBBox();
    state.graphCamera={resize(){},focus:async(point,options)=>{
      layoutFocusCalls.push({point,bounds:options.bounds,phase:layoutJob?.phase||'idle'});return {ok:true}}};
  `);
  await h.run('relayout()');
  assert.equal(await h.run('applyPendingFocusEdge()'), false);
  assert.equal(h.run('layoutFocusCalls.length'), 0);
  const task = tasks.at(-1);
  const nodes = h.run('state.graph.getNodes().map((item,index)=>({id:item.getModel().id,x:100+index*300,y:200}))');
  task.worker.onmessage({data:layoutWorkerResponse(h, task.message, nodes)});
  await waitForLayoutCommit(h);
  assert.equal(h.run('layoutFocusCalls.length'), 1);
  assert.equal(h.run('layoutFocusCalls[0].phase'), 'idle');
  assert.equal(h.run('pendingFocusEdge'), 0);
  assert.equal(h.run('state.graphView.selectedEdge'), 'SYN-HUB=>SYN-00000');
});

test('G6 selection updates its URL and rejects a late previous-case query', async () => {
  const h = harness('app.js', 'http://127.0.0.1:8300/?case_id=1');
  await h.run('loadCases()');
  h.run('globalThis.query = doSearch; renderFlows = rows => { globalThis.displayed = rows; };');
  const pending = deferred();
  h.network(path => path.startsWith('/api/flows') ? pending.promise : response({}));
  const old = h.run('query(1)');
  h.bindApp();
  await h.element('#caseSelect').onchange({ target: { value: '2' } });
  pending.resolve(response({ page: 1, size: 50, total: 1, rows: [{ trade_no: 'OLD-SYNTHETIC-A' }] }));
  await old;
  assert.equal(h.location.searchParams.get('case_id'), '2');
  assert.equal(h.run('globalThis.displayed?.[0]?.trade_no'), undefined);
});

test('deleting a case refreshes the remaining selection after the response', async () => {
  const h = harness('app.js', 'http://127.0.0.1:8300/?case_id=2');
  await h.run('loadCases()'); h.bindApp();
  h.network(path => response(path.startsWith('/api/cases/2') ? { deleted_flows: 4 } : [cases[0]]));
  await h.element('#btnDelCase').onclick();
  assert.equal(h.run('state.caseId'), 2, 'opening the delete form does not delete or navigate');
  await h.element('#caseSubmit').onclick();
  assert.equal(h.run('state.caseId'), 1);
  assert.equal(h.location.searchParams.get('case_id'), '1');
});

test('explicitly opening a newly created case discards the old named canvas and context', async () => {
  const h = harness('app.js'); await h.run('loadCases()'); h.bindApp();
  h.run("state.canvasId = 99; state.canvasName = 'Old synthetic canvas'; state.ctx = { flowId: 1 }; state.imp = { import_id: 'old' };");
  h.network((path, options) => response(options.method === 'POST' ? cases[2] : cases));
  await h.element('#btnNewCase').onclick();
  h.element('#caseName').value = 'Synthetic new';
  await h.element('#caseSubmit').onclick();
  assert.equal(h.run('state.caseId'), 1, 'creation preserves the current case until explicit open');
  assert.equal(h.run('state.canvasId'), 99);
  assert.equal(h.run('state.canvasName'), 'Old synthetic canvas');
  assert.equal(h.run('state.ctx.flowId'), 1);
  assert.equal(h.run('state.imp.import_id'), 'old');
  await h.element('#caseOpenResult').onclick();
  assert.equal(h.run('state.caseId'), 3);
  assert.equal(h.run('state.canvasId'), 0);
  assert.equal(h.run('state.canvasName'), '');
  assert.equal(h.run('state.ctx'), null);
  assert.equal(h.run('state.imp'), null);
  assert.equal(h.element('#canvasSel').value, '0');
});

test('the real app binding exports a case package through its shared blob download host', async () => {
  const h = harness('app.js', 'http://127.0.0.1:8300/?case_id=2');
  await h.run('loadCases()'); h.bindApp();
  h.network(path => {
    assert.equal(path, '/api/cases/2/export');
    return response({});
  });
  await h.element('#btnExportCase').onclick();
  await h.element('#caseSubmit').onclick();
  assert.equal(h.run('caseWorkspace.status().phase'), 'done');
  assert.equal(h.downloads.length, 1);
  assert.equal(h.downloads[0].filename, '案件包_2.fundcase');
  assert.equal(h.objectUrls[0].blob instanceof Blob, true);
  assert.equal(h.run('state.caseId'), 2);
});

test('a failed canvas-list refresh preserves the active binding, selection and account drafts', async () => {
  const h = harness('app.js'); await h.run('loadCases()');
  h.run(`state.canvasId=7;state.canvasName='Synthetic retained canvas';
    state.graphView.selectedAccount='SYN-SELECTED';
    nodeDrafts.set('synthetic-draft',{idBase:'SYN-BASE',idValue:'SYN-DIRTY',focusBase:'1',focusValue:'1'});
    globalThis.retainedCanvasBinding=JSON.stringify(caseContext());
    globalThis.retainedCanvasDrafts=JSON.stringify([...nodeDrafts]);`);
  const select = h.element('#canvasSel'); select.value = '7'; select.innerHTML = '<option value="7">Synthetic retained canvas</option>';
  h.network(() => ({ ...response({ detail: 'Synthetic canvas-list failure' }), ok: false, status: 503 }));
  await assert.rejects(h.run('refreshCanvasList()'), /Synthetic canvas-list failure/);
  assert.equal(h.run('JSON.stringify(caseContext())===retainedCanvasBinding'), true);
  assert.equal(h.run('JSON.stringify([...nodeDrafts])===retainedCanvasDrafts'), true);
  assert.equal(h.run('state.canvasName'), 'Synthetic retained canvas');
  assert.equal(h.run('state.graphView.selectedAccount'), 'SYN-SELECTED');
  assert.equal(select.value, '7');
  assert.equal(select.innerHTML, '<option value="7">Synthetic retained canvas</option>');
});

test('a known successful canvas save still exports the case when the following list refresh fails', async () => {
  const h = harness('app.js');
  h.run('globalThis.actualCanvasList=refreshCanvasList'); installSyntheticHub(h, 3);
  h.run(`refreshCanvasList=actualCanvasList;state.canvasId=7;state.canvasName='Synthetic saved canvas';
    state.graphView.selectedAccount='SYN-HUB';globalThis.caseSaveNotices=[];toast=(...args)=>caseSaveNotices.push(args);
    nodeDrafts.set('synthetic-draft',{idBase:'SYN-BASE',idValue:'SYN-DIRTY',focusBase:'1',focusValue:'1'});
    globalThis.caseSaveBinding=JSON.stringify(caseContext());globalThis.caseSaveDrafts=JSON.stringify([...nodeDrafts]);`);
  h.element('#canvasSel').value = '7';
  h.network((path, options) => {
    if (path === '/api/canvases/save') { assert.equal(options.method, 'POST'); return response({ canvas_id: 7, name: 'Synthetic saved canvas' }); }
    if (path === '/api/canvases?case_id=1') return { ...response({ detail: 'Synthetic list unavailable' }), ok: false, status: 503 };
    assert.equal(path, '/api/cases/1/export'); return response({});
  });
  await h.element('#btnExportCase').onclick(); h.element('#caseSaveCanvas').checked = true;
  await h.element('#caseSubmit').onclick();
  assert.deepEqual(h.requests.map(item => item.path), ['/api/canvases/save', '/api/canvases?case_id=1', '/api/cases/1/export']);
  const saved = JSON.parse(h.requests[0].options.body);
  assert.equal(saved.case_id, 1); assert.equal(saved.canvas_id, 7); assert.equal(saved.snapshot.view.scope, 'all');
  assert.equal(saved.snapshot.nodes.length, 3); assert.equal(saved.snapshot.edges.length, 2);
  assert.equal(saved.snapshot.edges[0].data.amount_totals[0].amount, '10000000000000000.01');
  assert.equal(h.run('caseWorkspace.status().phase'), 'done'); assert.equal(h.downloads.length, 1);
  assert.equal(h.run('JSON.stringify(caseContext())===caseSaveBinding'), true);
  assert.equal(h.run('JSON.stringify([...nodeDrafts])===caseSaveDrafts'), true);
  assert.equal(h.run('state.graphView.selectedAccount'), 'SYN-HUB'); assert.equal(h.element('#canvasSel').value, '7');
  assert.equal(h.run('caseSaveNotices.filter(item=>item[1]==="err" && /已保存.*暂未刷新/.test(item[0])).length'), 1);
});

test('late G6 graph, statistics and batches cannot refill a switched case', async () => {
  const h = harness('app.js'); await h.run('loadCases()');
  h.run('globalThis.savedGraph = loadGraph; globalThis.savedStats = refreshStats; globalThis.savedBatches = loadBatches;');
  const graph = deferred(), stats = deferred(), batches = deferred();
  h.network(path => path.startsWith('/api/graph') ? graph.promise : path.startsWith('/api/stats') ? stats.promise : batches.promise);
  const pending = h.run('Promise.all([savedGraph(), savedStats(), savedBatches()])');
  h.bindApp(); await h.element('#caseSelect').onchange({ target: { value: '2' } });
  graph.resolve(response({ nodes: [{ account: 'OLD-A' }], edges: [], tracked_ids: [99] }));
  stats.resolve(response({ flows: 99, accounts: 4 }));
  batches.resolve(response([{ id: 99, filename: 'OLD-A.csv' }]));
  await pending;
  assert.equal(h.run('state.lastGraph.nodes.length'), 0);
  assert.equal(h.run('state.stats.flows'), undefined);
  assert.equal(h.element('#batchList').innerHTML, '');
});

test('analysis honors the explicit case and keeps its graph return link bound', async () => {
  const h = harness('analysis.js', 'http://127.0.0.1:8300/analysis?case_id=2', '1');
  await flush();
  assert.equal(h.run('caseId'), 2);
  assert.equal(new URL(h.element('#backToGraph').href, h.location).searchParams.get('case_id'), '2');
});

test('analysis case change discards previous results and a late run', async () => {
  const h = harness('analysis.js'); await flush();
  h.run("render = value => { document.querySelector('#results').innerHTML = value.marker; }; lastResults = { marker: 'OLD-A' }; document.querySelector('#results').innerHTML = 'OLD-A';");
  const pending = deferred(); h.network(path => path === '/api/analysis/run' ? pending.promise : response({ flows: 0, accounts: 0 }));
  const old = h.run('run()');
  h.element('#caseSel').onchange({ target: { value: '2' } });
  assert.equal(h.run('lastResults'), null);
  assert.equal(h.element('#btnReport').disabled, true);
  pending.resolve(response(analysisResult(['cycles'], { marker: 'LATE-A' }))); await old;
  assert.notEqual(h.element('#results').innerHTML, 'LATE-A');
  assert.equal(h.run('lastResults'), null);
});

test('analysis returning to the same case still rejects an earlier generation', async () => {
  const h = harness('analysis.js'); await flush();
  h.run("render = value => { document.querySelector('#results').innerHTML = value.marker; };");
  const pending = deferred(); h.network(path => path === '/api/analysis/run' ? pending.promise : response({ flows: 0, accounts: 0 }));
  const old = h.run('run()');
  h.element('#caseSel').onchange({ target: { value: '2' } });
  h.element('#caseSel').onchange({ target: { value: '1' } });
  pending.resolve(response(analysisResult(['cycles'], { marker: 'OLD-GENERATION-A' }))); await old;
  assert.equal(h.run('lastResults'), null);
  assert.notEqual(h.element('#results').innerHTML, 'OLD-GENERATION-A');
});

test('report uses the completed run parameters and suppresses late cross-case download', async () => {
  const h = harness('analysis.js'); await flush();
  h.run("render = value => { document.querySelector('#results').innerHTML = value.marker; };");
  h.network(() => response(analysisResult(['cycles'], { marker: 'CURRENT-A' }))); await h.run('run()');
  h.element('#cycDays').value = '90';
  const pending = deferred(); h.network(path => path === '/api/analysis/report' ? pending.promise : response({ flows: 0, accounts: 0 }));
  const report = h.element('#btnReport').onclick();
  await flush(); // The scripted user accepts the explicit recomputation dialog.
  const request = h.requests.findLast(item => item.path === '/api/analysis/report');
  assert.equal(JSON.parse(request.options.body).params.cycle_window_days, 7);
  assert.equal(Object.hasOwn(JSON.parse(request.options.body).params, 'ff_window_minutes'), false);
  h.element('#caseSel').onchange({ target: { value: '2' } });
  pending.resolve(response({})); await report;
  assert.equal(h.downloads.length, 0);
});


test('legacy exact money preserves huge cents, sign, zero and rejects floats', () => {
  const h = harness('app.js');
  assert.equal(h.run("fmtMoney('9999999999999999.99')"), '9,999,999,999,999,999.99');
  assert.equal(h.run("fmtShort('10000000000000000.01')"), '10,000,000,000,000,000.01');
  assert.equal(h.run("fmtMoney('-0.01')"), '-0.01');
  assert.equal(h.run("fmtMoney('0.00')"), '0.00');
  assert.equal(h.run("fmtMoney(null)"), '');
  assert.equal(h.run("fmtMoney(0.1)"), '金额格式错误');
  assert.equal(h.run("fmtCount(12345)"), '12,345');
  assert.equal(h.run("fmtMoney('NaN')"), '金额格式错误');
});

test('graph labels keep exact cents and separate currency totals', () => {
  const h = harness('app.js');
  assert.equal(h.run("edgeLabel({cnt:2,total:'10000000000000000.01',currencies:['CNY'],amount_totals:[{currency:'CNY',amount:'10000000000000000.01'}]})"), '2笔·¥10,000,000,000,000,000.01');
  const mixed = h.run("edgeLabel({cnt:3,total:null,currencies:['CNY','USD'],amount_totals:[{currency:'CNY',amount:'0.01'},{currency:'USD',amount:'9999999999999999.99'}]})");
  assert.equal(mixed, '3笔·¥0.01\nUSD 9,999,999,999,999,999.99');
  h.run("renderFlows([{id:1,amount:'9999999999999999.99',currency:'USD'}])");
  assert.match(h.element('#flowTbody').innerHTML, /USD 9,999,999,999,999,999\.99/);
  assert.doesNotMatch(h.element('#flowTbody').innerHTML, /¥/);
});

test('amount filtering sends decimal boundaries unchanged', async () => {
  const h = harness('app.js'); await h.run('loadCases()');
  h.element('#fMin').value = '9999999999999999.98';
  h.element('#fMax').value = '9999999999999999.99';
  h.network(() => response({page:1,size:50,total:0,rows:[]}));
  await h.run('doSearch(1)');
  const query = new URL(h.requests.findLast(r => r.path.startsWith('/api/flows')).path, h.location).searchParams;
  assert.equal(query.get('min_amount'), '9999999999999999.98');
  assert.equal(query.get('max_amount'), '9999999999999999.99');
});

test('analysis money and count dimensions have distinct display rules', async () => {
  const h = harness('analysis.js'); await flush();
  assert.equal(h.run("money('9999999999999999.99','USD')"), 'USD 9,999,999,999,999,999.99');
  assert.equal(h.run("dimensionValue({dim:'total',value:'0.01',currency:'CNY'})"), '¥0.01');
  assert.equal(h.run("dimensionValue({dim:'cnt',value:12,currency:'CNY'})"), '12 笔');
  assert.equal(h.run("dimensionValue({dim:'night',value:5,currency:'CNY'})"), '5 笔');
  h.run("render({cycles:{count:1,cycles:[{accounts_names:['SYN-A','SYN-B'],edges:[],len:2,total:'9999999999999999.99',currency:'USD'}]}},['cycles'])");
  assert.match(h.element('#results').innerHTML, /USD 9,999,999,999,999,999\.99/);
});

for(const transition of ['selectCase(2);selectCase(1)','activateCanvas(7);activateCanvas(0)'])
test(`worker layout rejects ${transition} and cancelled graph results`, async () => {
  const workers = [], tasks = [];
  class SyntheticWorker {
    constructor() { workers.push(this); this.terminated = false; }
    postMessage(message) { this.message = message; tasks.push({worker:this,message}); }
    terminate() { this.terminated = true; }
    deliver(nodes,message=this.message) { this.onmessage({data:layoutWorkerResponse(h,message,nodes)}); }
  }
  const h = harness('app.js', 'http://127.0.0.1:8300/', '1', { Worker: SyntheticWorker });
  await h.run('loadCases()');
  h.run(`globalThis.layoutWrites = [];
    const models = [{id:'SYN-A',x:10,y:20},{id:'SYN-B',x:30,y:40}];
    state.graph = { getNodes:()=>models.map(model=>({getModel:()=>model})),getEdges:()=>[],
      setAutoPaint(){},paint(){},fitView(){},getZoom:()=>1,
      updateItem(node,p){layoutWrites.push(p);Object.assign(node.getModel(),p)},clear(){} };
  `);
  await h.run('relayout()');
  const old = tasks.at(-1);
  h.run(transition);
  old.worker.deliver([{id:'SYN-A',x:999,y:999}],old.message);
  assert.equal(old.worker.terminated, true);
  assert.equal(h.run('layoutWrites.length'), 0);
  await h.run('relayout()');
  const accepted = tasks.at(-1);
  assert.notEqual(accepted.message.requestId,old.message.requestId);
  accepted.worker.deliver([{id:'SYN-A',x:101,y:202},{id:'SYN-B',x:303,y:404}],accepted.message);
  await waitForLayoutCommit(h);
  assert.equal(h.run('layoutWrites.length'), 2);
  assert.equal(h.run("state.positions['SYN-A'].x"), 101);
  await h.run('relayout()');
  const pending = tasks.at(-1);
  assert.notEqual(pending.message.requestId,accepted.message.requestId);
  // A warm Worker may own a new task. Deliver the previous successful reply
  // through its current handler: request identity, not constructor index,
  // must protect the pending transaction from stale geometry.
  pending.worker.deliver([{id:'SYN-A',x:999,y:999}],accepted.message);
  assert.equal(h.run('layoutWrites.length'), 2);
  assert.equal(h.run("state.positions['SYN-A'].x"), 101);
  h.run('cancelGraphLayout()');
  pending.worker.deliver([{id:'SYN-A',x:999,y:999}],pending.message);
  assert.equal(pending.worker.terminated, true);
  assert.equal(h.run('layoutWrites.length'), 2);
  assert.equal(h.run("state.positions['SYN-A'].x"), 101);
  for(const requestId of new Set(tasks.map(task=>task.message.requestId))) assert.ok(
    tasks.filter(task=>task.message.requestId===requestId&&task.message.task==='layout').length<=2,
    'each request retains the bounded two-candidate layout policy');
});

test('deterministic layout bounds geometry work including coincident saved positions', () => {
  const h = harness('app.js');
  h.run(`globalThis.layoutInput = {width:1000,height:700,kind:'force',
    nodes:Array.from({length:1000},(_,i)=>({id:'SYN-'+i,x:500,y:350})),
    edges:Array.from({length:999},(_,i)=>({source:'SYN-'+i,target:'SYN-'+(i+1)}))};
    globalThis.layoutModel = FundsGraphLayout.simulation(layoutInput);
    while(layoutModel.step()){};
    globalThis.layoutResult = layoutModel.result();`);
  assert.equal(h.run('layoutResult.nodes.length'), 1000);
  const positions=JSON.parse(h.run('JSON.stringify(layoutResult.nodes)'));
  assert.ok(positions.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)));
  assert.deepEqual(positions.map(p=>p.id).sort(),Array.from({length:1000},(_,i)=>'SYN-'+i).sort());
  // World geometry can extend beyond the initial viewport; a camera Fit must
  // not be confused with squeezing a thousand 48px bodies into hard walls.
  for(let i=0;i<positions.length;i++)for(let j=0;j<i;j++) {
    assert.ok(Math.abs(positions[i].x-positions[j].x)>=66-1e-6||Math.abs(positions[i].y-positions[j].y)>=66-1e-6,
      `${positions[i].id}/${positions[j].id} complete default bodies lack their spacing`);
  }
  assert.equal(h.run('layoutInput.nodes.every(p=>p.x===500&&p.y===350)'),true);
  assert.ok(h.run('layoutResult.interactions') < 1000 * 999 * 120 / 2);
  assert.equal(h.run("JSON.stringify(FundsGraphLayout.seed('SYN-1',1000,700)) === JSON.stringify(FundsGraphLayout.seed('SYN-1',1000,700))"), true);
});


test('path controls are sent losslessly with the frozen run and report snapshot', async () => {
  const h = harness('analysis.js'); await flush();
  h.run("document.querySelectorAll('.alg')[0].value='paths'");
  h.element('#pSrc').value = '0009007199254740993'; h.element('#pDst').value = 'SYN-END'; h.element('#pHops').value = '6';
  h.network(() => response(analysisResult(['paths'])));
  await h.run('run()');
  const runRequest = h.requests.findLast(r => r.path === '/api/analysis/run');
  assert.equal(JSON.parse(runRequest.options.body).params.path_src, '0009007199254740993');
  assert.equal(JSON.parse(runRequest.options.body).params.path_dst, 'SYN-END');
  assert.equal(JSON.parse(runRequest.options.body).params.path_max_hops, 6);
  h.element('#pSrc').value = 'CHANGED-UNRUN';
  await h.element('#btnReport').onclick();
  const reportRequest = h.requests.findLast(r => r.path === '/api/analysis/report');
  assert.equal(JSON.parse(reportRequest.options.body).params.path_src, '0009007199254740993');
  const before = h.requests.length;
  h.element('#pSrc').value = ''; await h.run('run()');
  assert.equal(h.requests.length, before);
});


test('zero matching tolerance remains zero instead of defaulting to 25 percent', async () => {
  const h = harness('analysis.js', undefined, undefined, { algorithms: [{ value: 'fast_flows', checked: true }] }); await flush();
  h.element('#ffTol').value = '0';
  assert.equal(h.run('collectParams().params.ff_tolerance'), 0);
  h.element('#ffTol').value = 'NaN';
  assert.throws(() => h.run('collectParams()'), /配对金额容差/);
});


test('unknown currency is never silently relabelled as CNY', () => {
  const h = harness('app.js');
  assert.equal(h.run("moneyLabel('0.01','')"), '未标注币种 0.01');
  assert.equal(h.run("moneyLabel('0.01',null)"), '未标注币种 0.01');
  assert.equal(h.run("edgeLabel({cnt:1,total:'0.01',currencies:[''],amount_totals:[{currency:'',amount:'0.01'}]})"), '1笔·未标注币种 0.01');
  h.run("renderFlows([{id:1,amount:'0.01',currency:''}])");
  assert.match(h.element('#flowTbody').innerHTML, /未标注币种 0\.01/);
  assert.doesNotMatch(h.element('#flowTbody').innerHTML, /¥/);
});

for (const requested of ['paths','cycles','fast_flows','outliers','unrecognized']) {
  test(`ribbon whole-case ${requested} shortcut preselects only its algorithm without running it`, async () => {
    const algorithms=['paths','cycles','fast_flows','outliers'].map(value=>({value,checked:true}));
    const h=harness('analysis.js',`http://127.0.0.1:8300/analysis?case_id=2&algorithm=${requested}`,'1',{algorithms});
    await flush();
    assert.deepEqual(algorithms.filter(input=>input.checked).map(input=>input.value),
      requested==='unrecognized'?['paths','cycles','fast_flows','outliers']:[requested]);
    assert.equal(h.run('caseId'),2);assert.equal(h.requests.some(item=>item.path==='/api/analysis/run'),false);
  });
}


test('G6 optimizes pan and zoom and restores labels at every supported resting zoom', () => {
  let configuration;
  class SyntheticGraph { constructor(options) { configuration = options; } on() {} get() { return null; } }
  class SyntheticMinimap {}
  const h = harness('app.js', 'http://127.0.0.1:8300/', '1', { G6: { Graph: SyntheticGraph, Minimap: SyntheticMinimap } });
  h.run('initGraph()');
  const modes = configuration.modes.default;
  assert.equal(modes.find(mode => mode.type === 'drag-canvas').enableOptimize, true);
  const zoom = modes.find(mode => mode.type === 'zoom-canvas');
  assert.equal(zoom.enableOptimize, true);
  assert.ok(zoom.optimizeZoom < configuration.minZoom);
  const drag = modes.find(mode => mode.type === 'drag-node');
  assert.ok(drag); assert.equal(typeof drag.shouldBegin, 'function');
  assert.equal(drag.shouldBegin(), true);
  h.run('state.graphLoading=true'); assert.equal(drag.shouldBegin(), false);
  h.run('state.graphLoading=false;layoutJob={phase:"computing"}'); assert.equal(drag.shouldBegin(), false);
  h.run('layoutJob={phase:"committing"}'); assert.equal(drag.shouldBegin(), false);
  h.run('layoutJob=null;window.FundsCanvasEditor={history:{status:()=>({busy:true})}}'); assert.equal(drag.shouldBegin(), false);
  h.run('window.FundsCanvasEditor.history.status=()=>({busy:false})'); assert.equal(drag.shouldBegin(), true);
  assert.equal(configuration.animate, false);
  for (const kind of ['defaultNode','defaultEdge']) {
    assert.equal(configuration[kind].labelCfg.style.background,undefined,'labels are plain text without a backplate');
    assert.equal(configuration[kind].labelCfg.style.stroke,null);
    assert.equal(configuration[kind].labelCfg.style.lineWidth,0);
  }
  for(const kind of ['defaultNode','defaultEdge']) {
    assert.equal(configuration[kind].labelCfg.style.fontSize,13);
    assert.equal(configuration[kind].labelCfg.style.lineHeight,18);
  }
});

test('first tracking into an initialized empty graph lays out once and preserves saved positions', () => {
  const h = harness('app.js');
  h.run(`globalThis.layoutCalls = 0;globalThis.firstLayoutKinds=[];
    relayout = async fit => { layoutCalls++;firstLayoutKinds.push({kind:state.layout,fit}); };
    let renderedModels = [];
    state.graph = {
      data(data){renderedModels=data.nodes},render(){},changeData(data){renderedModels=data.nodes},
      getNodes:()=>renderedModels.map(model=>({getModel:()=>model})),getEdges:()=>[],
      setAutoPaint(){},paint(){},stopLayout(){},getZoom:()=>1,updateItem(item,update){Object.assign(item.getModel(),update)}
    };
    renderGraph();
    state.lastGraph = {nodes:[{account:'SYN-A',out_cnt:1,in_cnt:0},{account:'SYN-B',out_cnt:0,in_cnt:1}],edges:[]};
    renderGraph(); renderGraph();
  `);
  assert.equal(h.run('layoutCalls'), 1);
  assert.equal(h.run('firstLayoutKinds[0].kind'),'auto','a fresh unsaved graph discovers its structure through the automatic layout');
  assert.equal(h.run('firstLayoutKinds[0].fit'),true,'only the first unsaved placement fits the new graph to the canvas');
  h.run(`state.graph.data({nodes:[]});state.positions={'SYN-A':{x:111,y:222},'SYN-B':{x:333,y:444}};renderGraph();`);
  assert.equal(h.run('layoutCalls'), 1);
  assert.equal(h.run("state.graph.getNodes()[0].getModel().x"), 111);
  assert.equal(h.run("state.graph.getNodes()[1].getModel().y"), 444);
});

function snapshotGraph(h, photo = false) {
  h.bindApp();
  h.element('#caseSelect').selectedOptions = [{ textContent: 'Synthetic A' }];
  h.run(`state.caseId=1; state.graphInited=true;
    globalThis.snapshotNode = {id:'SYN-A',x:123.456,y:234.567,label:'Synthetic A',data:{account:'SYN-A',
      out_cnt:2,in_cnt:0,out_amt:'10000000000000000.01',in_amt:'0.00',
      out_totals:[{currency:'CNY',amount:'10000000000000000.01'}],in_totals:[],photo_at:${photo ? '123' : 'null'}}};
    globalThis.snapshotEdge = {source:'SYN-A',target:'SYN-B',label:'2笔·¥10,000,000,000,000,000.01',data:{cnt:2,ids:[1,2],
      total:'10000000000000000.01',currencies:['CNY'],amount_totals:[{currency:'CNY',amount:'10000000000000000.01'}]}};
    state.graph={getNodes:()=>[{getModel:()=>snapshotNode}],getEdges:()=>[{getModel:()=>snapshotEdge,getContainer:()=>({get:()=>[]})}],clear(){}};
  `);
}

test('public graph snapshot retains exact structured monetary metadata independent of labels', async () => {
  const h = harness('app.js'); snapshotGraph(h);
  const snapshot = await h.run('window.__collectSnapshot()');
  assert.equal(snapshot.case_name, 'Synthetic A');
  assert.equal(snapshot.nodes[0].data.out_amt, '10000000000000000.01');
  assert.equal(snapshot.nodes[0].data.out_totals[0].amount, '10000000000000000.01');
  assert.equal(snapshot.edges[0].data.total, '10000000000000000.01');
  assert.equal(snapshot.edges[0].data.amount_totals[0].amount, '10000000000000000.01');
  assert.deepEqual(Array.from(snapshot.edges[0].data.ids), [1, 2]);
  h.run("snapshotEdge.data.amount_totals[0].amount='0.01'");
  assert.equal(snapshot.edges[0].data.amount_totals[0].amount, '10000000000000000.01');
});

test('public graph snapshot discards a photo-delayed export after a case switch', async () => {
  const h = harness('app.js'); snapshotGraph(h, true);
  const pending = deferred(); h.network(() => pending.promise);
  const snapshot = h.run('window.__collectSnapshot()');
  h.run('selectCase(2)'); h.element('#caseSelect').selectedOptions = [{ textContent: 'Synthetic B' }];
  pending.resolve(response({}));
  assert.equal(await snapshot, null);
});

test('public graph snapshot discards a revoked canvas even when its id returns', async () => {
  const h = harness('app.js'); snapshotGraph(h, true);
  const pending = deferred(); h.network(() => pending.promise);
  const snapshot = h.run('window.__collectSnapshot()');
  h.run('state.canvasId=2;cancelGraphLayout();state.canvasId=0;cancelGraphLayout()');
  pending.resolve(response({}));
  assert.equal(await snapshot, null);
});

function installSyntheticHub(h, size = 1000, { bodyPadding = 0, fixedScreenShadow = false } = {}) {
  const cents=1000000000000000001n+BigInt(size-2),outTotal=`${cents/100n}.${String(cents%100n).padStart(2,'0')}`;
  h.bindApp();
  h.element('#caseSelect').selectedOptions = [{ textContent: 'Synthetic A' }];
  h.element('#graphContainer').clientWidth = 1000; h.element('#graphContainer').clientHeight = 700;
  h.element('#neighborDirection').value = 'both';
  h.run(`globalThis.labelShapeWrites = 0; globalThis.createdLabelShapes=0;globalThis.initialGraphLabelShapes=null;globalThis.graphPaints = 0;globalThis.imageReads=0;
    globalThis.syntheticZoom = 0.45;
    globalThis.syntheticBodyPadding = ${JSON.stringify(bodyPadding)};
    globalThis.syntheticFixedScreenShadow = ${JSON.stringify(fixedScreenShadow)};
    globalThis.syntheticLabelBounds=null;
    globalThis.syntheticGraphEvents=new Map();
    globalThis.syntheticViewport={minX:0,minY:0,maxX:1000,maxY:700};
    globalThis.syntheticFitViewport=null;globalThis.fitVisibility=[];globalThis.imageVisibility=[];
    const account = i => 'SYN-' + String(i).padStart(5,'0');
    const leaves = Array.from({length:${size-1}},(_,i)=>({account:account(i),name:'Synthetic '+i,
      in_cnt:i===0?2:1,out_cnt:0,in_amt:i===0?null:'0.01',out_amt:'0.00',
      in_totals:i===0?[{currency:'CNY',amount:'10000000000000000.01'},{currency:'USD',amount:'0.01'}]
        :[{currency:'CNY',amount:'0.01'}],out_totals:[]}));
    globalThis.syntheticFullGraph = {nodes:[{account:'SYN-HUB',name:'Synthetic Hub',is_main:1,
      in_cnt:0,out_cnt:${size},in_amt:'0.00',out_amt:null,in_totals:[],
      out_totals:[{currency:'CNY',amount:'${outTotal}'},{currency:'USD',amount:'0.01'}]},...leaves],
      edges:leaves.map((leaf,i)=>({source:'SYN-HUB',target:leaf.account,cnt:i===0?2:1,
        ids:i===0?[1,2]:[i+2],total:i===0?null:'0.01',currencies:i===0?['CNY','USD']:['CNY'],
        amount_totals:i===0?[{currency:'CNY',amount:'10000000000000000.01'},{currency:'USD',amount:'0.01'}]
          :[{currency:'CNY',amount:'0.01'}]})),tracked_ids:Array.from({length:${size}},(_,i)=>i+1)};
    const makeShape = (name,owner) => {createdLabelShapes++;return {visible:true,values:{},
      get(key){return key==='name'?name:key==='visible'?this.visible:this.values[key]},
      set(key,value){this.values[key]=value},
      remove(){const index=owner.indexOf(this);if(index>=0)owner.splice(index,1)},
      show(){this.visible=true;labelShapeWrites++},hide(){this.visible=false;labelShapeWrites++},
      getBBox(){const x=this.values.x||0,y=this.values.y||0,size=this.values.fontSize||10;
        const lines=String(this.values.text||'').split('\\n'),width=Math.max(...lines.map(line=>line.length))*size*.6;
        const height=size+(lines.length-1)*(this.values.lineHeight||size+4);
        return {minX:x-width/2,maxX:x+width/2,minY:y-height/2,maxY:y+height/2}},
      attr(key,value){if(arguments.length===0)return this.values;if(typeof key==='object'){Object.assign(this.values,key);return}if(value!==undefined)this.values[key]=value;
        return this.values[key]??0}}};
    const makeItem = model => {const shapes=[];
      const group={visible:true,get(key){return key==='children'?shapes:key==='visible'?this.visible:undefined},
        show(){this.visible=true},hide(){this.visible=false}};
      const bodyBounds=()=>{const size=Array.isArray(model.size)?model.size:[model.size||32,model.size||32];
        const pad=typeof syntheticBodyPadding==='number'?{left:syntheticBodyPadding,top:syntheticBodyPadding,
          right:syntheticBodyPadding,bottom:syntheticBodyPadding}:syntheticBodyPadding;
        return {minX:-size[0]/2-pad.left,minY:-size[1]/2-pad.top,maxX:size[0]/2+pad.right,maxY:size[1]/2+pad.bottom}};
      const projectLocal=bounds=>{const center=state.graph.getCanvasByPoint?.(model.x,model.y)||{x:model.x,y:model.y},zoom=Math.abs(state.graph.getZoom?.()||1);
        return {minX:center.x+bounds.minX*zoom,minY:center.y+bounds.minY*zoom,
          maxX:center.x+bounds.maxX*zoom,maxY:center.y+bounds.maxY*zoom}};
      const key={getBBox:bodyBounds,getCanvasBBox:()=>{
        if(model.source)return null;
        const box=projectLocal(bodyBounds()),shadow=syntheticFixedScreenShadow?Math.max(0,Number(model.style?.shadowBlur)||0):0;
        // The pinned Canvas renderer expands the projected shape with a fixed
        // screen-space shadow envelope; this padding does not scale with zoom.
        return {minX:box.minX-shadow,minY:box.minY-shadow,maxX:box.maxX+shadow,maxY:box.maxY+shadow};
      }};
      const item={getModel:()=>model,shapes,getContainer:()=>group,...(model.source?{}:{getKeyShape:()=>key}),
        syncLabel(){if(typeof model.label!=='string'||!model.label.length)shapes.length=0;
          else if(!shapes.length){const text=makeShape('text-shape',shapes),background=makeShape('text-bg-shape',shapes);
            text.attr({...model.labelCfg?.style,text:model.label});
            text.getCanvasBBox=background.getCanvasBBox=()=>{
              const base=typeof syntheticLabelBounds==='function'?syntheticLabelBounds(model):null;
              if(model.source)return base;
              if(!base)return projectLocal(text.getBBox());
              const zoom=Math.abs(state.graph.getZoom()),dx=text.attr('x')*zoom,dy=text.attr('y')*zoom;
              return {minX:base.minX+dx,minY:base.minY+dy,maxX:base.maxX+dx,maxY:base.maxY+dy};
            };
            shapes.push(text,background)}}};
      item.syncLabel();return item};
    let graphNodes=[],graphEdges=[];
    const replace = data => {const before=createdLabelShapes;graphNodes=data.nodes.map(makeItem);graphEdges=data.edges.map(makeItem);
      if(initialGraphLabelShapes===null)initialGraphLabelShapes=createdLabelShapes-before};
    state.caseId=1;state.graphInited=false;state.forceFit=false;state.positions={};
    syntheticFullGraph.nodes.forEach((node,i)=>{state.positions[node.account]={x:80+i%100*8,y:i===0?0:80+Math.floor(i/100)*45}});
    state.lastGraph=syntheticFullGraph;state.tracked=new Set(syntheticFullGraph.tracked_ids);
    globalThis.originalGraphFacts=JSON.stringify(syntheticFullGraph);
    state.graph={data:replace,changeData:replace,render(){},getNodes:()=>graphNodes,getEdges:()=>graphEdges,
      get(){return null},on(name,callback){if(!syntheticGraphEvents.has(name))syntheticGraphEvents.set(name,[]);syntheticGraphEvents.get(name).push(callback)},
      setAutoPaint(){},paint(){graphPaints++},stopLayout(){},
      fitView(){fitVisibility.push([...graphNodes,...graphEdges].every(item=>item.getContainer().get('visible')));
        if(syntheticFitViewport)syntheticViewport={...syntheticFitViewport}},getZoom:()=>syntheticZoom,
      getPointByCanvas(x,y){const v=syntheticViewport;return {x:v.minX+x/1000*(v.maxX-v.minX),y:v.minY+y/700*(v.maxY-v.minY)}},
      getCanvasByPoint(x,y){const v=syntheticViewport;return{x:(x-v.minX)*1000/(v.maxX-v.minX),y:(y-v.minY)*700/(v.maxY-v.minY)}},
      zoomTo(value){syntheticZoom=value},changeSize(){},toDataURL(){imageReads++;
        imageVisibility.push([...graphNodes,...graphEdges].every(item=>item.getContainer().get('visible')));
        return 'data:image/png;base64,c3ludGhldGlj'},
      updateItem(item,update){Object.assign(item.getModel(),update);if(Object.hasOwn(update,'label'))item.syncLabel()},
      clearItemStates(){},setItemState(){},clear(){graphNodes=[];graphEdges=[]}};
    state.minimap=FundsGraphMinimap.create({canvas:document.querySelector('#graphMinimap'),onNavigate(){}});
    renderGraph();
  `);
}

test('main-account role refresh patches authoritative roles while retaining working geometry, camera, filters, membership and selection', async () => {
  const h = harness('app.js'); installSyntheticHub(h, 3);
  h.run(readFileSync(`${root}static/canvas-history.js`, 'utf8'));
  h.run(`
    state.presentation.nodeStyles={'SYN-HUB':{fill:'#234567',stroke:'#abcdef',size:72}};
    state.presentation.relationFilter={active:true,currency:'CNY',min:'1.00',max:'999.99'};
    state.graphView.visibleIds=new Set(['SYN-HUB','SYN-00000','SYN-00001']);
    state.graphView.root='SYN-HUB';state.graphView.direction='out';
    globalThis.roleSelectionNotifications=0;
    state.graphSelection={ids:new Set(['SYN-HUB','SYN-00000']),refresh(){},
      rebind(ids,patch){if([...ids].join('|')!==[...this.ids].join('|'))roleSelectionNotifications++;patch();this.ids=new Set(ids)}};
    window.FundsCanvasEditor={history:window.FundsCanvasHistory.create({capture:()=>({positions:state.positions}),restore:async()=>true})};
    window.FundsCanvasEditor.history.record('合成样式', {size:32}, {size:72});
    globalThis.roleFresh=JSON.parse(JSON.stringify(syntheticFullGraph));
    roleFresh.nodes[0].is_main=0;roleFresh.nodes[0].is_same_name=1;
    roleFresh.nodes[1].is_main=1;roleFresh.nodes[1].is_same_name=0;
    roleFresh.nodes[1].name='Synthetic unrelated changed name';
    globalThis.roleRows=[{account:'SYN-00000',name:'Synthetic 0',source:'manual'},
      {account:'SYN-OFF-CANVAS',name:'Synthetic external',source:'auto'}];
  `);
  const snapshot = () => JSON.parse(h.run(`JSON.stringify({
    positions:state.positions,geometry:state.graph.getNodes().map(item=>{const m=item.getModel();return [m.id,m.x,m.y]}),
    camera:[syntheticZoom,syntheticViewport],tracked:[...state.tracked],edges:state.lastGraph.edges,
    presentation:state.presentation,selected:[...state.graphSelection.ids],
    visible:[...state.graphView.visibleIds],root:state.graphView.root,direction:state.graphView.direction,
    history:window.FundsCanvasEditor.history.status()})`));
  const before = snapshot(), revision = h.run('state.graphView.revision');
  h.run(`state.graph.changeData=()=>{throw Error('role refresh must not rebuild membership')};
    state.graph.fitView=()=>{throw Error('role refresh must not refit the camera')};
    state.graph.changeSize=()=>{throw Error('role refresh must not reflow the canvas')}`);
  h.network(path => { assert.equal(path, '/api/graph?case_id=1&canvas_id=0'); return response(h.run('roleFresh')); });
  await h.run('refreshMainAccountRoles(caseContext(),roleRows)');
  assert.deepEqual(snapshot(), before);
  assert.equal(h.run('state.graphView.revision'), revision + 1);
  assert.deepEqual(h.run('[...state.mainAccounts]'), h.run('roleRows.map(row=>row.account)'));
  assert.equal(h.run('state.lastGraph.nodes[0].is_main'), 0); assert.equal(h.run('state.lastGraph.nodes[0].is_same_name'), 1);
  assert.equal(h.run('state.lastGraph.nodes[1].is_main'), 1);
  assert.match(h.run('state.graph.getNodes()[0].getModel().fundsLabel'), /^◆ /);
  assert.match(h.run('state.graph.getNodes()[1].getModel().fundsLabel'), /^★ /);
  assert.equal(h.run('state.graph.getNodes()[0].getModel().style.fill'), '#234567');
  assert.equal(h.run('state.graph.getNodes()[0].getModel().style.stroke'), '#abcdef');
  assert.equal(h.run('state.graph.getNodes()[0].getModel().size'), 72);
  assert.equal(h.run('state.lastGraph.nodes[1].name'), 'Synthetic 0', 'role refresh cannot overwrite unrelated authoritative metadata');
  assert.equal(h.run('roleSelectionNotifications'), 0);
  assert.equal(h.requests.filter(request => request.options.method && request.options.method !== 'GET').length, 0);
});

for (const revoke of ['case A-B-A', 'canvas A-B-A', 'view revision', 'graph replacement']) {
  test(`a main-account role read withholds all late publication after ${revoke}`, async () => {
    const h = harness('app.js'); installSyntheticHub(h, 3); const late = deferred();
    h.network(() => late.promise);
    const pending = h.run(`refreshMainAccountRoles(caseContext(),[{account:'SYN-00000',name:'Synthetic 0',source:'manual'}])`);
    if (revoke === 'case A-B-A') h.run('selectCase(2);selectCase(1)');
    if (revoke === 'canvas A-B-A') h.run('activateCanvas(7);activateCanvas(0)');
    if (revoke === 'view revision') h.run('state.graphView.revision++');
    if (revoke === 'graph replacement') h.run('state.graph={...state.graph}');
    const before = h.run('JSON.stringify({facts:state.lastGraph,main:[...state.mainAccounts],revision:state.graphView.revision})');
    late.resolve(response({ nodes: [{account:'SYN-00000',is_main:1}] })); await pending;
    assert.equal(h.run('JSON.stringify({facts:state.lastGraph,main:[...state.mainAccounts],revision:state.graphView.revision})'), before);
  });
}

test('a main-account role read rejects inconsistent membership before patching any graph metadata', async () => {
  const h = harness('app.js'); installSyntheticHub(h, 3);
  const before = h.run('JSON.stringify({facts:state.lastGraph,main:[...state.mainAccounts],revision:state.graphView.revision})');
  h.network(() => response({ nodes: [{account:'SYN-HUB',is_main:0}] }));
  await assert.rejects(h.run(`refreshMainAccountRoles(caseContext(),[])`), /图谱已变化/);
  assert.equal(h.run('JSON.stringify({facts:state.lastGraph,main:[...state.mainAccounts],revision:state.graphView.revision})'), before);
});

test('a sent main-account update excludes real canvas membership, commands and undo callbacks', async () => {
  const h = harness('app.js'); installSyntheticHub(h, 3); const pending = deferred();
  const env = h.run('globalThis'), listeners = new Map();
  env.window.addEventListener = (type, fn) => { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(fn); };
  env.window.removeEventListener = (type, fn) => { listeners.set(type, (listeners.get(type) || []).filter(item => item !== fn)); };
  env.window.dispatchEvent = event => { for (const fn of listeners.get(event.type) || []) fn(event); };
  h.run(readFileSync(`${root}static/canvas-history.js`, 'utf8'));
  h.run(readFileSync(`${root}static/canvas-editor.js`, 'utf8'));
  for (const fn of listeners.get('DOMContentLoaded') || []) fn();
  h.run(`window.FundsCanvasEditor.history.record('合成保留历史',{positions:{'SYN-HUB':{x:1,y:2}}},{positions:{'SYN-HUB':{x:3,y:4}}})`);
  const before = h.run('JSON.stringify({history:window.FundsCanvasEditor.history.status(),tracked:[...state.tracked],positions:state.positions})');
  h.network(path => path === '/api/main-accounts/auto' ? pending.promise : response([]));
  const manager = env.window.FundsMainAccountsActions;
  assert.equal(manager.open(h.element('#btnMainActions')), true); await flush();
  assert.equal(manager.prepare('auto'), true); const operation = manager.submit(); await flush();
  assert.equal(manager.status().sent, true);
  assert.equal(await h.run('trackFlows([999])'), false);
  const membership = await env.window.FundsCanvasEditor.membership.set([999], true);
  assert.equal(membership.status, 'unavailable'); assert.equal(membership.ok, false);
  let commandRan = false;
  assert.equal(await env.window.FundsCanvasEditor.command('合成禁止并发', () => { commandRan = true; }), false);
  assert.equal(commandRan, false); assert.equal(await h.element('#btnUndo').onclick(), false);
  assert.equal(h.run('JSON.stringify({history:window.FundsCanvasEditor.history.status(),tracked:[...state.tracked],positions:state.positions})'), before);
  assert.deepEqual(h.requests.filter(request => request.options.method).map(request => request.path), ['/api/main-accounts/auto']);
  pending.resolve({ ok: false, status: 409, text: async () => JSON.stringify({detail:'Synthetic write rejection'}) }); await operation;
  assert.equal(manager.blocked({caseId:1}), false);
});

test('public neighborhood controls bound a 1000-account graph without changing tracked facts', async () => {
  const h = harness('app.js'); installSyntheticHub(h);
  const writes = h.requests.length;
  h.element('#neighborAccount').value = 'SYN-HUB';
  h.element('#btnNeighborhood').onclick();
  assert.equal(h.run('state.graph.getNodes().length'), 51);
  assert.equal(h.run('state.graph.getEdges().length'), 50);
  assert.equal(h.run('state.graphView.remaining'), 949);
  assert.equal(h.run('state.graphView.root'), 'SYN-HUB');
  assert.equal(h.run('state.graphView.index.nodeIds.size'), 51);
  assert.equal(h.element('#graphViewStatus').textContent, '', 'canvas keeps explanatory status text out of the graph');
  assert.equal(h.run('state.tracked.size'), 1000);
  assert.equal(h.run('state.lastGraph.nodes.length'), 1000);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===originalGraphFacts'), true);
  h.element('#btnExpandNeighbors').onclick();
  assert.equal(h.run('state.graph.getNodes().length'), 101);
  assert.equal(h.run('state.graphView.remaining'), 899);
  assert.equal(h.requests.length, writes);
  h.element('#btnGraphOverview').onclick();
  assert.equal(h.run('state.graphView.visibleIds'), null);
  assert.equal(h.run('state.graphView.root'), null);
  assert.equal(h.run('state.graph.getNodes().length'), 1000);
  assert.equal(h.run('state.graph.getEdges().length'), 999);
  assert.equal(h.run('state.positions["SYN-00998"].x'), 872);
  assert.equal(h.run('state.graph.getNodes().find(item=>item.getModel().id==="SYN-00998").getModel().x'), 872);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===originalGraphFacts'), true);
  assert.equal(h.requests.length, writes);
});

test('graph refresh reveals newly tracked endpoints inside an existing neighborhood only', async () => {
  const h = harness('app.js'); h.run('globalThis.actualGraphLoader = loadGraph'); installSyntheticHub(h, 120);
  h.run('loadGraph = actualGraphLoader; exploreNeighborhood("SYN-HUB", true)');
  const updated = JSON.parse(h.run('JSON.stringify(state.lastGraph)'));
  updated.nodes.push({ account: 'SYN-NEW-A', in_cnt: 0, out_cnt: 1 }, { account: 'SYN-NEW-B', in_cnt: 1, out_cnt: 0 });
  updated.edges.push({ source: 'SYN-NEW-A', target: 'SYN-NEW-B', ids: [121], cnt: 1, total: '0.01' });
  updated.tracked_ids.push(121);
  h.network(() => response(updated)); await h.run('loadGraph()');
  const visible = Array.from(h.run('state.graph.getNodes().map(item => item.getModel().id)'));
  assert.equal(visible.length, 53);
  assert.ok(visible.includes('SYN-NEW-A')); assert.ok(visible.includes('SYN-NEW-B'));
  assert.ok(!visible.includes('SYN-00118'), 'unrelated previously hidden accounts stay hidden');
  assert.equal(h.run('state.graphView.root'), 'SYN-HUB');
  assert.equal(h.run('state.graphView.remaining'), 69);
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(state.lastGraph)')), updated);
});

test('explicit tracking reveals an already tracked hidden endpoint without duplicating facts', async () => {
  const h = harness('app.js'); h.run('globalThis.actualGraphLoader = loadGraph'); installSyntheticHub(h, 120);
  h.run('loadGraph = actualGraphLoader; exploreNeighborhood("SYN-HUB", true); state.justAdded = new Set([120])');
  const graph = JSON.parse(h.run('JSON.stringify(state.lastGraph)'));
  h.network(() => response(graph)); await h.run('loadGraph()');
  assert.ok(h.run('state.graphView.visibleIds.has("SYN-00118")'));
  assert.equal(h.run('state.graph.getNodes().length'), 52);
  assert.equal(h.run('state.tracked.size'), 120);
  assert.equal(h.run('state.graphView.remaining'), 68);
});

test('graph refresh prunes removed neighborhood endpoints and updates remaining neighbors', async () => {
  const h = harness('app.js'); h.run('globalThis.actualGraphLoader = loadGraph'); installSyntheticHub(h, 120);
  h.run('loadGraph = actualGraphLoader; exploreNeighborhood("SYN-HUB", true)');
  const graph = JSON.parse(h.run('JSON.stringify(state.lastGraph)'));
  graph.nodes = graph.nodes.filter(node => node.account !== 'SYN-00000' && node.account !== 'SYN-00118');
  graph.edges = graph.edges.filter(edge => edge.target !== 'SYN-00000' && edge.target !== 'SYN-00118');
  graph.tracked_ids = graph.tracked_ids.filter(id => ![1, 2, 120].includes(id));
  h.network(() => response(graph)); await h.run('loadGraph()');
  assert.equal(h.run('state.graphView.visibleIds.has("SYN-00000")'), false);
  assert.equal(h.run('state.graphView.remaining'), 68);
});

test('an older graph response cannot replace a newer same-canvas graph', async () => {
  const h = harness('app.js'); h.run('state.caseId = 1; renderGraph = () => {}');
  const old = deferred(), latest = deferred(); let calls = 0;
  h.network(() => ++calls === 1 ? old.promise : latest.promise);
  const first = h.run('loadGraph()'), second = h.run('loadGraph()');
  latest.resolve(response({nodes:[{account:'SYN-NEW'}],edges:[],tracked_ids:[1,2]})); await second;
  old.resolve(response({nodes:[{account:'SYN-OLD'}],edges:[],tracked_ids:[1]})); await first;
  assert.deepEqual(Array.from(h.run('state.tracked')), [1,2]);
  assert.equal(h.run('state.lastGraph.nodes[0].account'), 'SYN-NEW');
});

test('an older graph retry cannot supersede a newer successful read', async () => {
  const h = harness('app.js'); h.run('state.caseId = 1; renderGraph = () => {}');
  let calls = 0;
  h.network(() => ++calls === 1 ? Promise.reject(new Error('synthetic offline'))
    : response({nodes:[{account:'SYN-NEW'}],edges:[],tracked_ids:[1,2]}));
  const first = h.run('loadGraph()'); await flush();
  await h.run('loadGraph()');
  h.timers.find(timer => timer.delay === 1500).callback(); await first;
  assert.equal(calls, 2);
  assert.deepEqual(Array.from(h.run('state.tracked')), [1,2]);
});

test('G6 label visibility keeps canonical money intact through selection and hover', () => {
  const h = harness('app.js'); installSyntheticHub(h);
  assert.ok(h.run('state.graphView.policy.nodeIds.size')>0);
  assert.ok(h.run('state.graphView.policy.nodeIds.size')<20,'overlapping screen labels are pruned within the policy cap');
  assert.equal(h.run('state.graphView.policy.edgeKeys.size'), 0);
  assert.equal(h.run('state.graph.getEdges()[0].shapes.length'), 0);
  assert.equal(h.run('state.graph.getEdges()[0].getModel().label'), undefined);
  assert.equal(h.run('state.graph.getEdges()[0].getModel().fundsLabel'), '2笔·¥10,000,000,000,000,000.01\nUSD 0.01');
  h.run(`selectItem(state.graph.getEdges()[0]);applyGraphLabels();`);
  assert.equal(h.run('state.graph.getEdges()[0].shapes[0].visible'), true);
  assert.equal(h.run('state.graph.getEdges()[0].shapes.some(shape=>shape.get("name")==="text-bg-shape")'), false);
  assert.equal(h.run('state.graphView.policy.edgeKeys.size'), 1);
  h.run(`selectItem(null);state.graphView.hoveredAccount='SYN-00998';applyGraphLabels();`);
  assert.equal(h.run('state.graphView.policy.nodeIds.has("SYN-00998")'), true);
  assert.equal(h.run('state.graph.getEdges()[0].shapes.length'), 0);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===originalGraphFacts'), true);
  assert.equal(h.run('state.graph.getEdges()[0].getModel().data.amount_totals[0].amount'), '10000000000000000.01');
  assert.equal(h.run('state.graph.getEdges()[0].getModel().data.amount_totals[1].currency'), 'USD');
  h.run('state.graphView.labelMode="all";applyGraphLabels()');
  assert.equal(h.run('state.graphView.policy.nodeIds.size'), 1000);
  assert.equal(h.run('state.graphView.policy.edgeKeys.size'), 999);
  assert.equal(h.run('state.graph.getEdges().every(item=>item.shapes.every(shape=>shape.visible))'), true);
});

test('A-B-A case navigation resets neighborhood and rejects a stale label callback', () => {
  const h = harness('app.js'); installSyntheticHub(h);
  h.run(`exploreNeighborhood('SYN-HUB',true);state.graphView.hoveredAccount='SYN-00002';
    state.graphView.selectedEdge='SYN-HUB=>SYN-00000';scheduleGraphLabels(true)`);
  const oldTimer = h.timers.findLast(timer => timer.delay === 140);
  assert.ok(oldTimer);
  h.run('selectCase(2);selectCase(1)');
  for (const field of ['visibleIds', 'root', 'selectedAccount', 'hoveredAccount', 'selectedEdge', 'policy', 'fullIndex', 'index']) {
    assert.equal(h.run(`state.graphView.${field}`), null);
  }
  assert.equal(h.run('state.graphView.remaining'), 0);
  assert.equal(h.run('state.graph.getNodes().length'), 0);
  assert.equal(h.run('state.lastGraph.nodes.length'), 0);
  const shapes = h.run('labelShapeWrites'), paints = h.run('graphPaints');
  oldTimer.callback(); // Even an already-queued callback must recheck its binding.
  assert.equal(h.run('labelShapeWrites'), shapes); assert.equal(h.run('graphPaints'), paints);
  assert.equal(h.run('state.graphView.policy'), null);
});

test('neighborhood snapshots distinguish current display from complete canvas persistence', async () => {
  const h = harness('app.js'); installSyntheticHub(h);
  h.run(`exploreNeighborhood('SYN-HUB',true);syntheticZoom=0.45;state.graphView.labelMode='focus';applyGraphLabels()`);
  const visible = await h.run('window.__collectSnapshot()');
  const full = await h.run('window.__collectSnapshot({scope:"all"})');
  assert.equal(visible.nodes.length, 51); assert.equal(visible.edges.length, 50);
  assert.equal(visible.view.scope, 'visible'); assert.equal(visible.view.total_nodes, 1000);
  assert.equal(visible.view.total_edges, 999); assert.equal(visible.view.visible_nodes, 51);
  assert.equal(full.nodes.length, 1000); assert.equal(full.edges.length, 999); assert.equal(full.view.scope, 'all');
  assert.ok(full.nodes.find(node => node.id === 'SYN-00998'));
  assert.equal(full.nodes.find(node => node.id === 'SYN-00998').x, 872);
  assert.equal(visible.edges[0].data.amount_totals[0].amount, '10000000000000000.01');
  assert.equal(visible.edges[0].data.amount_totals[1].amount, '0.01');
  assert.equal(visible.edges[0].data.amount_totals[1].currency, 'USD');
  assert.equal(full.edges[0].label, visible.edges[0].label);
  assert.equal(visible.nodes.every(node => typeof node.labelVisible === 'boolean'), true);
  assert.equal(visible.edges.every(edge => typeof edge.labelVisible === 'boolean'), true);
  assert.equal(visible.edges.some(edge => !edge.labelVisible), true);
  assert.equal(full.nodes.every(node => node.labelVisible), true);
  assert.equal(full.edges.every(edge => edge.labelVisible), true);
  h.run('syntheticFullGraph.edges[0].amount_totals[0].amount="0.02"');
  assert.equal(visible.edges[0].data.amount_totals[0].amount, '10000000000000000.01');
  assert.equal(full.edges[0].data.amount_totals[0].amount, '10000000000000000.01');
});

test('PNG and PDF export complete only within their frozen graph binding', async () => {
  const pngBase64='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jDioAAAAASUVORK5CYII=';
  for (const format of ['png', 'pdf']) {
    const h = harness('app.js'); installSyntheticHub(h); h.element('#exportFmt').value = format;
    if(format==='png')h.run(`state.graph.toDataURL=()=>{imageReads++;return 'data:image/png;base64,${pngBase64}'}`);
    h.network(() => response({}));
    const pending = h.element('#btnExportPng').onclick();
    const imageTimer = h.timers.findLast(timer => timer.delay === 400);
    assert.ok(imageTimer); imageTimer.callback(); await pending;
    assert.equal(h.run('imageReads'), 1);
    assert.equal(h.downloads.length, 1); assert.equal(h.downloads[0].filename, `资金图谱_案件1.${format}`);
    const requests = h.requests.filter(request => request.path === '/api/export/graph.pdf');
    assert.equal(requests.length, format === 'pdf' ? 1 : 0);
    if (format === 'pdf') assert.equal(JSON.parse(requests[0].options.body).image, 'data:image/png;base64,c3ludGhldGlj');
    else {
      assert.equal(h.downloads[0].href,'blob:synthetic','PNG uses the proven object URL download path');
      assert.equal(h.objectUrls.length,1);
      assert.equal(h.objectUrls[0].blob.type,'image/png');
      const bytes=Buffer.from(await h.objectUrls[0].blob.arrayBuffer());
      assert.deepEqual([...bytes.subarray(0,8)],[137,80,78,71,13,10,26,10]);
      assert.deepEqual(bytes,Buffer.from(pngBase64,'base64'),'data URL conversion preserves every PNG byte');
      assert.equal(h.requests.length,0,'synchronous PNG decoding does not issue another HTTP request');
    }
  }
});

test('PNG and PDF reject their delayed image timer after case, canvas, view or graph replacement', async () => {
  for (const format of ['png', 'pdf']) {
    for (const change of ['selectCase(2)', 'activateCanvas(7)', 'state.graphView.revision+=1', 'state.graph={clear(){}}']) {
      const h = harness('app.js'); installSyntheticHub(h); h.element('#exportFmt').value = format;
      const pending = h.element('#btnExportPng').onclick();
      const imageTimer = h.timers.findLast(timer => timer.delay === 400);
      h.run(change); imageTimer.callback(); await pending;
      assert.equal(h.run('imageReads'), 0, `${format}: ${change}`);
      assert.equal(h.downloads.length, 0, `${format}: ${change}`);
      assert.equal(h.objectUrls.length,0,`${format}: ${change} cannot create a stale download URL`);
      assert.equal(h.requests.filter(request => request.path === '/api/export/graph.pdf').length, 0);
    }
  }
});

test('PDF ignores a late HTTP response after its case has been revoked', async () => {
  const h = harness('app.js'); installSyntheticHub(h); h.element('#exportFmt').value = 'pdf';
  const pendingResponse = deferred(); h.network(() => pendingResponse.promise);
  const exported = h.element('#btnExportPng').onclick();
  h.timers.findLast(timer => timer.delay === 400).callback(); await flush();
  assert.equal(h.requests.filter(request => request.path === '/api/export/graph.pdf').length, 1);
  h.run('selectCase(2);selectCase(1)');
  pendingResponse.resolve(response({})); await exported;
  assert.equal(h.downloads.length, 0);
});

function installPhotoCrop(h) {
  // bindApp intentionally stubs unrelated setup; keep this feature's actual binder.
  h.run('globalThis.realBindPhotoCrop=bindPhotoCrop'); installSyntheticHub(h); h.run('realBindPhotoCrop()');
}

test('photo confirmation posts the captured account and crop within a live case binding', async () => {
  const h = harness('app.js', 'http://127.0.0.1:8300/', '1', { FormData }); installPhotoCrop(h);
  h.network(() => response({}));
  h.run(`openPhotoCrop(new Blob(['synthetic photo']), 'SYN-00000', null);pcState.cx=0.25;pcState.r=0.3`);
  await h.element('#pcConfirm').onclick();
  const post = h.requests.find(request => request.path === '/api/account-photos/set');
  assert.ok(post); assert.equal(post.options.method, 'POST');
  assert.equal(post.options.body.get('case_id'), '1'); assert.equal(post.options.body.get('account'), 'SYN-00000');
  assert.equal(post.options.body.get('crop_cx'), '0.2500'); assert.equal(post.options.body.get('crop_r'), '0.3000');
  assert.equal(h.element('#photoCropModal').style.display, 'none');
});

test('revoked photo crop cannot start requests and a late old-photo GET cannot post into another case', async () => {
  for (const change of ['selectCase(2)', 'activateCanvas(7)']) {
    const h = harness('app.js', 'http://127.0.0.1:8300/', '1', { FormData }); installPhotoCrop(h);
    h.run(`openPhotoCrop(null,'SYN-00000',null)`); h.run(change);
    assert.equal(h.run('pcState.binding'), null); assert.equal(h.element('#photoCropModal').style.display, 'none');
    await h.element('#pcConfirm').onclick(); assert.equal(h.requests.length, 0);
  }
  const h = harness('app.js', 'http://127.0.0.1:8300/', '1', { FormData }); installPhotoCrop(h);
  const pendingImage = deferred(); h.network(() => pendingImage.promise);
  h.run(`openPhotoCrop(null,'SYN-00000',null)`);
  const confirmed = h.element('#pcConfirm').onclick();
  assert.equal(h.requests.length, 1); assert.match(h.requests[0].path, /orig\?case_id=1&account=SYN-00000/);
  h.run('selectCase(2)'); pendingImage.resolve(response({})); await confirmed;
  assert.equal(h.requests.filter(request => request.options.method === 'POST').length, 0);
  assert.equal(h.run('state.forceRebuild'), false);
});

test('photo update ignores a late POST response after an A-B-A case transition', async () => {
  const h = harness('app.js', 'http://127.0.0.1:8300/', '1', { FormData }); installPhotoCrop(h);
  const pendingPost = deferred(); h.network(() => pendingPost.promise);
  h.run(`openPhotoCrop(new Blob(['synthetic photo']),'SYN-00000',null)`);
  const confirmed = h.element('#pcConfirm').onclick();
  assert.equal(h.requests.length, 1); assert.equal(h.requests[0].options.body.get('case_id'), '1');
  h.run('selectCase(2);selectCase(1)'); pendingPost.resolve(response({})); await confirmed;
  assert.equal(h.run('state.forceRebuild'), false); assert.equal(h.run('pcState.binding'), null);
  assert.equal(h.element('#photoCropModal').style.display, 'none');
});

test('old absolute labels project onto their own path while explicit drag ratios take priority', () => {
  const h = harness('app.js'); installSyntheticHub(h,2);installSnapshotPaths(h);
  h.run(`applyCanvasSnapshot({nodes:[{id:'SYN-HUB',x:100,y:200},{id:'SYN-00000',x:500,y:200}],
    edges:[{source:'SYN-HUB',target:'SYN-00000',type:'line',lx:170,ly:230,bw:40,bh:10}],display:{labelMode:'all'}});
    restoreEdgeLabelPositions();`);
  assert.equal(h.run('state.edgeLabelPos["SYN-HUB=>SYN-00000"]'), .175);
  assert.equal(h.run('state.graph.getEdges()[0].shapes[0].attr("x")'), 170);
  assert.equal(h.run('state.graph.getEdges()[0].shapes[0].attr("y")'), 200);
  assert.equal(h.run('state.graph.getEdges()[0].shapes.some(shape=>shape.get("name")==="text-bg-shape")'),false);
  h.run('state.edgeLabelPos["SYN-HUB=>SYN-00000"]=0.25;restoreEdgeLabelPositions()');
  assert.equal(h.run('state.graph.getEdges()[0].shapes[0].attr("x")'), 200);
  assert.equal(h.run('state.graph.getEdges()[0].shapes[0].attr("y")'), 200);
});

test('canvas A-B-A activation revokes neighborhood, crop and stale label work even when id returns', () => {
  const h = harness('app.js'); installSyntheticHub(h);
  h.run(`exploreNeighborhood('SYN-HUB',true);openPhotoCrop(null,'SYN-00000',null);
    scheduleGraphLabels(true);globalThis.oldCanvasGeneration=state.canvasGeneration`);
  const oldTimer = h.timers.findLast(timer => timer.delay === 140);
  h.run('activateCanvas(7);activateCanvas(0)');
  assert.equal(h.run('state.canvasId'), 0);
  assert.equal(h.run('state.canvasGeneration-oldCanvasGeneration'), 2);
  assert.equal(h.run('state.graphView.visibleIds'), null); assert.equal(h.run('state.graphView.root'), null);
  assert.equal(h.run('state.graphView.selectedAccount'), null); assert.equal(h.run('pcState.binding'), null);
  assert.equal(h.element('#photoCropModal').style.display, 'none');
  const paints = h.run('graphPaints'), shapes = h.run('labelShapeWrites'); oldTimer.callback();
  assert.equal(h.run('graphPaints'), paints); assert.equal(h.run('labelShapeWrites'), shapes);
});

function installCanvasControls(h, size = 1000) {
  h.run('globalThis.realCanvasBinder=bindCanvases;globalThis.realCanvasLoader=loadGraph');
  installSyntheticHub(h, size);
  h.run('loadGraph=realCanvasLoader;realCanvasBinder();state.canvasId=10;state.canvasName="Canvas A"');
}

test('cloning a formatted selected edge loads the named canvas after G6 retires its old label geometry', async () => {
  const h = harness('app.js'); installCanvasControls(h, 4);
  h.run('relayout=async()=>{};selectItem(state.graph.getEdges()[0])');
  await h.element('#edgeTypeSel').onchange({target:{value:'line'}});
  const oldEdge = h.run('_selItem'), graph = h.run('state.graph'), clear = graph.clear.bind(graph);
  assert.ok(oldEdge, 'formatting retains the selected relationship for the following clone');
  assert.equal(oldEdge.getModel().type, 'line');
  let retired = false, retiredGeometryReads = 0;
  // G6 clear can retire a path before its Item model/destroyed flag changes.
  // A later label cleanup must not ask that path to recompute its geometry.
  oldEdge.getKeyShape = () => {
    if (retired) { retiredGeometryReads++; throw new TypeError('Synthetic retired G6 label path'); }
    return null;
  };
  graph.clear = () => { clear(); retired = true; };
  h.network(path => response(path === '/api/canvases/clone'
    ? {canvas_id:20,name:'Selected edge clone',copied:4}
    : h.run('JSON.parse(JSON.stringify(syntheticFullGraph))')));
  assert.equal(await h.element('#btnCanvasClone').onclick(), true);
  const request = JSON.parse(h.requests.find(item => item.path === '/api/canvases/clone').options.body);
  assert.equal(request.from_canvas, 10); assert.equal(request.snapshot.nodes.length, 4);
  assert.equal(request.snapshot.edges[0].type, 'line', 'the committed clone retains the selected edge formatting');
  assert.equal(h.run('state.canvasId'), 20); assert.equal(h.run('state.canvasName'), 'Selected edge clone');
  assert.equal(h.run('state.graphLoading'), false); assert.equal(h.run('state.graphInited'), true);
  assert.equal(h.run('state.lastGraph.nodes.length'), 4); assert.equal(h.run('state.graph.getNodes().length'), 4);
  assert.equal(h.run('state.graph.getEdges()[0].getModel().type'), 'line');
  assert.equal(h.run('_selItem'), null); assert.equal(h.run('state.graphView.selectedEdge'), null);
  assert.equal(h.run('window.FundsCanvasActions.status().pending'), false);
  assert.equal(h.run('window.FundsCanvasActions.status().unknown'), false);
  assert.equal(oldEdge.getModel().type, 'line', 'a readable old model does not imply live rendering geometry');
  assert.equal(retiredGeometryReads, 0);
});

test('a pending canvas switch revokes the old graph before neighborhood, export or save actions', async () => {
  const h = harness('app.js'); installCanvasControls(h);
  const pendingGraph = deferred();
  const graphB = { nodes: [{account:'ONLY-CANVAS-B',name:'Synthetic B',in_cnt:0,out_cnt:0,in_amt:'0.00',out_amt:'0.00',in_totals:[],out_totals:[]}],
    edges: [], tracked_ids: [90001] };
  h.network(path => path.startsWith('/api/graph?') ? pendingGraph.promise
    : response(path.startsWith('/api/canvases/20?') ? {id:20,name:'Canvas B',snapshot:{nodes:[],edges:[]}}
      : {canvas_id:20,name:'Canvas B'}));
  const switching = h.element('#canvasSel').onchange({target:{value:'20',selectedOptions:[{textContent:'Canvas B'}]}});
  assert.equal(h.run('state.canvasId'), 20);
  assert.equal(h.run('state.graph.getNodes().length'), 0);
  assert.equal(h.run('state.lastGraph.nodes.length'), 0); assert.equal(h.run('state.tracked.size'), 0);
  assert.equal(h.run('Object.keys(state.positions).length'), 0);
  assert.equal(await h.run('window.__collectSnapshot({scope:"all"})'), null);
  h.element('#neighborAccount').value='SYN-HUB'; h.element('#btnNeighborhood').onclick();
  assert.equal(h.run('state.graphView.visibleIds'), null);
  h.element('#exportFmt').value='html'; await h.element('#btnExportPng').onclick();
  await h.element('#btnCanvasSave').onclick(); await h.element('#btnCanvasClone').onclick();
  assert.equal(h.requests.filter(request => request.options.method === 'POST').length, 0);
  assert.equal(h.downloads.length, 0);
  pendingGraph.resolve(response(graphB)); await switching;
  assert.equal(h.run('state.lastGraph.nodes[0].account'), 'ONLY-CANVAS-B');
  assert.equal(h.run('state.graph.getNodes().length'), 1);
  assert.equal(h.run('state.graph.getNodes()[0].getModel().id'), 'ONLY-CANVAS-B');
  assert.deepEqual(Array.from(h.run('state.tracked')), [90001]);
  assert.equal(h.run('state.graphLoading'), false);
  assert.equal(h.run('state.positions["SYN-HUB"]'), undefined);
  await h.element('#btnCanvasSave').onclick();
  const saved = JSON.parse(h.requests.find(request => request.path === '/api/canvases/save').options.body);
  assert.equal(saved.canvas_id, 20); assert.equal(saved.snapshot.nodes.length, 1);
  assert.equal(saved.snapshot.nodes[0].id, 'ONLY-CANVAS-B');
});

test('cloning a neighborhood freezes the complete old canvas before revocation and restores its positions', async () => {
  const h = harness('app.js'); installCanvasControls(h);
  h.run('relayout=async()=>{};exploreNeighborhood("SYN-HUB",true)');
  h.network(path => response(path === '/api/canvases/clone'
    ? {canvas_id:20,name:'Canvas Clone',copied:1000}
    : h.run('JSON.parse(JSON.stringify(syntheticFullGraph))')));
  await h.element('#btnCanvasClone').onclick();
  const request = JSON.parse(h.requests.find(item => item.path === '/api/canvases/clone').options.body);
  assert.equal(request.from_canvas, 10); assert.equal(request.snapshot.nodes.length, 1000);
  assert.equal(request.snapshot.edges.length, 999);
  assert.equal(request.snapshot.nodes.find(node => node.id === 'SYN-00998').x, 872);
  assert.equal(request.snapshot.edges[0].data.amount_totals[0].amount, '10000000000000000.01');
  assert.equal(h.run('state.canvasId'), 20); assert.equal(h.run('state.graphLoading'), false);
  assert.equal(h.run('state.lastGraph.nodes.length'), 1000); assert.equal(h.run('state.graph.getNodes().length'), 1000);
  assert.equal(h.run('state.graphView.visibleIds'), null);
  assert.equal(h.run('state.positions["SYN-00998"].x'), 872);
});

test('canvas snapshot resolves before publishing the new graph and cannot erase a later neighborhood', async () => {
  const h = harness('app.js'); installCanvasControls(h);
  h.run('relayout=async()=>{}');
  const savedShape = await h.run('window.__collectSnapshot({scope:"all"})');
  const rawGraph = h.run('JSON.parse(JSON.stringify(syntheticFullGraph))');
  const pendingSnapshot = deferred(), pendingGraph = deferred();
  h.network(path => path.startsWith('/api/canvases/20?') ? pendingSnapshot.promise : pendingGraph.promise);
  const switching = h.element('#canvasSel').onchange({target:{value:'20',selectedOptions:[{textContent:'Canvas B'}]}});
  await flush();
  assert.equal(h.requests.filter(request => request.path.startsWith('/api/graph?')).length, 0);
  assert.equal(h.run('state.graph.getNodes().length'), 0); assert.equal(h.run('state.lastGraph.nodes.length'), 0);
  assert.equal(h.run('state.graphLoading'), true);
  assert.equal(await h.run('window.__collectSnapshot({scope:"all"})'), null);
  pendingSnapshot.resolve(response({id:20,name:'Canvas B',snapshot:savedShape})); await flush();
  assert.equal(h.requests.filter(request => request.path.startsWith('/api/graph?')).length, 1);
  assert.equal(h.run('state.graph.getNodes().length'), 0);
  pendingGraph.resolve(response(rawGraph)); await switching;
  assert.equal(h.run('state.graphLoading'), false); assert.equal(h.run('state.graph.getNodes().length'), 1000);
  h.element('#neighborAccount').value='SYN-HUB'; h.element('#btnNeighborhood').onclick();
  assert.equal(h.run('state.graph.getNodes().length'), 51); assert.equal(h.run('state.graphView.root'), 'SYN-HUB');
  const restoreShape = h.timers.findLast(timer => timer.delay === 250);
  assert.ok(restoreShape); restoreShape.callback();
  assert.equal(h.element('#neighborAccount').value, 'SYN-HUB');
  assert.equal(h.run('state.graphView.root'), 'SYN-HUB'); assert.equal(h.run('state.graph.getNodes().length'), 51);
});

test('initial G6 item creation honors the label budget and public label modes add and remove actual shapes', () => {
  const h = harness('app.js'); installSyntheticHub(h);
  assert.equal(h.run('initialGraphLabelShapes'), 40, 'the initial G6 data creates exactly 20 text/background pairs before collision cleanup or lazy recovery');
  const remaining=h.run('[...state.graph.getNodes(),...state.graph.getEdges()].reduce((sum,item)=>sum+item.shapes.length,0)');
  assert.ok(remaining>0&&remaining<40,'the geometric collision pass removes overlapping labels after bounded initial creation');
  assert.equal(remaining,h.run('state.graphView.policy.nodeIds.size'));
  assert.equal(h.run('state.graph.getEdges().every(item=>item.getModel().label===undefined)'), true);
  h.element('#graphLabelMode').onchange({target:{value:'all'}});
  h.timers.findLast(timer=>timer.delay===0).callback();
  assert.equal(h.run('state.graph.getNodes().filter(item=>item.shapes.length===1).length'), 1000);
  assert.equal(h.run('state.graph.getEdges().filter(item=>item.shapes.length===1).length'), 999);
  assert.equal(h.run('state.graph.getEdges()[0].getModel().label'), '2笔·¥10,000,000,000,000,000.01\nUSD 0.01');
  h.element('#graphLabelMode').onchange({target:{value:'focus'}});
  h.timers.findLast(timer=>timer.delay===0).callback();
  assert.equal(h.run('[...state.graph.getNodes(),...state.graph.getEdges()].every(item=>item.shapes.length===0)'), true);
  h.run(`selectItem(state.graph.getNodes().find(item=>item.getModel().id==='SYN-00998'));applyGraphLabels()`);
  assert.deepEqual(Array.from(h.run('state.graph.getNodes().filter(item=>item.shapes.length===1).map(item=>item.getModel().id)')).sort(),
    ['SYN-00998','SYN-HUB'],'focus restores the selected leaf and its unobstructed adjacent hub label');
  assert.equal(h.run('state.graphView.policy.nodeIds.has("SYN-00998")'),true);
  assertAppliedLabelPlacements(h);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===originalGraphFacts'), true);
});

for(const zoom of [.4,1,2]) test(`lazy node labels recover from rendered body envelopes after clipping, focus and appended redraws without hover at ${zoom}x`, async () => {
  const h=harness('app.js');installSyntheticHub(h,3,{bodyPadding:{left:4,top:3,right:12,bottom:8}});
  h.run(`syntheticZoom=${zoom};syntheticViewport={minX:0,minY:0,maxX:1000/syntheticZoom,maxY:700/syntheticZoom};
    const atScreen=(x,y)=>({x:x/syntheticZoom,y:y/syntheticZoom});
    state.lastGraph={...state.lastGraph,nodes:state.lastGraph.nodes.map((node,i)=>i===1?{...node,photo_at:'synthetic-photo'}:node)};
    state.showPhotos=true;state.graphView.fullIndex=null;
    state.positions={'SYN-HUB':atScreen(200,200),'SYN-00000':atScreen(500,200),'SYN-00001':atScreen(800,200)};
    renderGraph();`);
  const originalIds=['SYN-00000','SYN-00001','SYN-HUB'];
  const visibleIds=()=>Array.from(h.run('state.graph.getNodes().filter(item=>labelShapes(item).length===1).map(item=>item.getModel().id)')).sort();
  const mode=value=>{h.element('#graphLabelMode').onchange({target:{value}});h.timers.findLast(timer=>timer.delay===0).callback();};
  assert.deepEqual(visibleIds(),originalIds,'initial rendered labels clear the actual expanded node bodies');
  assert.equal(h.run('state.graphView.nodeItems.get("SYN-00000").getModel().type'),'image','a photo node follows the same missing-label recovery path');
  assertAppliedLabelPlacements(h);
  mode('focus');
  assert.deepEqual(visibleIds(),[],'focus removes label shapes instead of keeping hidden geometry');
  mode('auto');
  assert.deepEqual(visibleIds(),originalIds,'labels without existing shapes reappear automatically outside the actual body envelope');
  assertAppliedLabelPlacements(h);
  h.run(`const returning=state.graphView.nodeItems.get('SYN-00000');
    state.graph.updateItem(returning,atScreen(5000,200));rebuildGraphViewport();applyGraphLabels();`);
  assert.equal(h.run('state.graphView.nodeItems.get("SYN-00000").getContainer().get("visible")'),false);
  assert.equal(h.run('labelShapes(state.graphView.nodeItems.get("SYN-00000")).length'),0,'offscreen labels release their actual shapes');
  h.run(`state.graph.updateItem(state.graphView.nodeItems.get('SYN-00000'),atScreen(500,200));
    rebuildGraphViewport();applyGraphLabels();`);
  assert.deepEqual(visibleIds(),originalIds,'dragging the node into view restores its label before any hover');
  assertAppliedLabelPlacements(h);
  const beforeAppend=JSON.parse(h.run('JSON.stringify(state.lastGraph)'));
  h.run(`globalThis.appendRedraws=0;const previousChangeData=state.graph.changeData;
    state.graph.changeData=data=>{appendRedraws++;previousChangeData(data)};
    const appended={...state.lastGraph.nodes[2],account:'SYN-APPENDED',name:'Synthetic appended'};
    state.lastGraph={...state.lastGraph,nodes:[...state.lastGraph.nodes,appended],
      edges:[...state.lastGraph.edges,{source:'SYN-00001',target:'SYN-APPENDED',cnt:1,ids:[4],total:'0.01',
        currencies:['CNY'],amount_totals:[{currency:'CNY',amount:'0.01'}]}],tracked_ids:[...state.lastGraph.tracked_ids,4]};
    state.tracked.add(4);state.positions['SYN-APPENDED']=atScreen(800,500);state.graphView.fullIndex=null;
    globalThis.appendedLabelFacts=JSON.stringify(state.lastGraph);renderGraph();`);
  assert.equal(h.run('appendRedraws'),1,'the appended graph passes through the real changeData render path');
  assert.deepEqual(visibleIds(),[...originalIds,'SYN-APPENDED'].sort(),'the append retains returning labels and creates the new label');
  assert.equal(h.run('state.graphView.hoveredAccount'),null,'hover is never required for label creation or recovery');
  assertAppliedLabelPlacements(h);
  h.run(`state.graph.updateItem(state.graphView.nodeItems.get('SYN-00001'),atScreen(200,200+50*syntheticZoom));
    rebuildGraphViewport();applyGraphLabels();`);
  assert.equal(h.run('state.graphView.policy.nodeIds.has("SYN-HUB")'),false,'a real neighboring node body still suppresses an overlapping optional label');
  assert.equal(h.run('labelShapes(state.graphView.nodeItems.get("SYN-HUB")).length'),0);
  h.run(`state.graph.updateItem(state.graphView.nodeItems.get('SYN-00001'),atScreen(800,200));
    rebuildGraphViewport();applyGraphLabels();`);
  assert.deepEqual(visibleIds(),[...originalIds,'SYN-APPENDED'].sort(),'removing the real collision restores the label without hover');
  assertAppliedLabelPlacements(h);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===appendedLabelFacts'),true,'all label and viewport decisions leave tracked facts unchanged');
  const snapshot=await h.run('window.__collectSnapshot({scope:"all",embedPhotos:false})');
  assert.equal(snapshot.nodes.length,4);assert.equal(snapshot.edges.length,3);
  assert.deepEqual(JSON.parse(JSON.stringify(snapshot.edges[0].data.amount_totals)),beforeAppend.edges[0].amount_totals);
  assert.equal(snapshot.edges[0].data.amount_totals[0].amount,'10000000000000000.01');
  assert.deepEqual(Array.from(snapshot.edges[0].data.ids),[1,2]);
});

for(const [initialZoom,nextZoom] of [[.4,.8],[.945,1.167],[1,2]]) test(`node label shapes survive fixed-screen shadows when zoom changes ${initialZoom} to ${nextZoom} and back`,()=>{
  const h=harness('app.js');installSyntheticHub(h,6,{fixedScreenShadow:true});
  h.run(`syntheticZoom=${initialZoom};syntheticViewport={minX:0,minY:0,maxX:1000/syntheticZoom,maxY:700/syntheticZoom};
    const atInitialScreen=(x,y)=>({x:x/syntheticZoom,y:y/syntheticZoom});
    state.positions={'SYN-HUB':atInitialScreen(150,150),'SYN-00000':atInitialScreen(450,150),
      'SYN-00001':atInitialScreen(750,150),'SYN-00002':atInitialScreen(150,450),
      'SYN-00003':atInitialScreen(450,450),'SYN-00004':atInitialScreen(850,450)};
    state.graphView.labelMode='auto';renderGraph();
    globalThis.zoomLabelCoordinates=JSON.stringify(state.graph.getNodes().map(item=>{
      const m=item.getModel();return{id:m.id,x:m.x,y:m.y}}));`);
  const allIds=['SYN-00000','SYN-00001','SYN-00002','SYN-00003','SYN-00004','SYN-HUB'];
  const actualTextIds=()=>Array.from(h.run(`state.graph.getNodes().filter(item=>item.getContainer().get('visible')!==false
    &&labelShapes(item).some(shape=>shape.get('visible')!==false&&shape.attr('text')===item.getModel().fundsLabel))
    .map(item=>item.getModel().id)`)).sort();
  const settle=zoom=>{
    h.run(`state.graph.zoomTo(${zoom});syntheticViewport={minX:0,minY:0,maxX:1000/syntheticZoom,maxY:700/syntheticZoom};scheduleGraphLabels(true)`);
    h.timers.findLast(timer=>timer.delay===140&&!timer.cancelled).callback();
  };
  const refreshHover=id=>{
    h.run(`state.graphView.hoveredAccount=${JSON.stringify(id)};scheduleGraphLabels()`);
    h.timers.findLast(timer=>timer.delay===0&&!timer.cancelled).callback();
  };
  assert.deepEqual(actualTextIds(),allIds,'initial auto labels have real visible canonical text shapes');
  assertAppliedLabelPlacements(h);
  settle(nextZoom);
  assert.ok(h.run(`state.graph.getNodes().some(item=>{const b=graphNodeBodyBounds(item);return b.minX>1000||b.minY>700})`),
    'the same zoom gesture moves at least one complete node beyond the physical viewport');
  const staying=nextZoom/initialZoom>1.5?['SYN-00000','SYN-HUB']:allIds.filter(id=>id!=='SYN-00004');
  assert.deepEqual(actualTextIds(),staying,'unobstructed nodes left on screen retain actual text without hover');
  assertAppliedLabelPlacements(h);
  refreshHover('SYN-HUB');
  assert.deepEqual(actualTextIds(),staying,'hover does not become the only way to create ordinary labels');
  refreshHover(null);
  assert.deepEqual(actualTextIds(),staying,'leaving hover preserves every unobstructed on-screen text shape');
  assertAppliedLabelPlacements(h);
  settle(initialZoom);
  assert.deepEqual(actualTextIds(),allIds,'returning the camera restores all canonical text shapes');
  assertAppliedLabelPlacements(h);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===originalGraphFacts'),true);
  assert.equal(h.run(`JSON.stringify(state.graph.getNodes().map(item=>{
    const m=item.getModel();return{id:m.id,x:m.x,y:m.y}}))===zoomLabelCoordinates`),true,
    'zoom and label recovery never move nodes or alter saved coordinates');
});

test('node label obstacles follow same-zoom live movement and shadow style changes without weakening real collisions',()=>{
  const h=harness('app.js');installSyntheticHub(h,3,{fixedScreenShadow:true});
  h.run(`syntheticZoom=1;syntheticViewport={minX:0,minY:0,maxX:1000,maxY:700};
    state.positions={'SYN-HUB':{x:200,y:150},'SYN-00000':{x:500,y:150},'SYN-00001':{x:800,y:150}};
    state.graphView.labelMode='auto';renderGraph();`);
  const text=id=>h.run(`labelShapes(state.graphView.nodeItems.get(${JSON.stringify(id)})).filter(shape=>
    shape.get('visible')!==false&&shape.attr('text')===state.graphView.nodeItems.get(${JSON.stringify(id)}).getModel().fundsLabel).length`);
  for(const id of ['SYN-HUB','SYN-00000','SYN-00001'])assert.equal(text(id),1);
  h.run(`const changing=state.graphView.nodeItems.get('SYN-HUB');
    state.graph.updateItem(changing,{style:{...changing.getModel().style,shadowBlur:0}});applyGraphLabels()`);
  assert.equal(text('SYN-HUB'),1,'shrinking the live screen shadow cannot leave an obsolete obstacle over its label');
  assertAppliedLabelPlacements(h);
  h.run(`const blocker=state.graphView.nodeItems.get('SYN-00001');
    const label=labelShapes(state.graphView.nodeItems.get('SYN-HUB'))[0].getCanvasBBox();
    state.graph.updateItem(blocker,{x:200,y:(label.minY+label.maxY)/2});
    refreshGraphRoutesAndLabels();applyGraphLabels()`);
  assert.equal(text('SYN-HUB'),0,'a neighboring node moved over the label suppresses its actual text at the same zoom');
  h.run(`state.graph.updateItem(state.graphView.nodeItems.get('SYN-00001'),{x:800,y:150});
    refreshGraphRoutesAndLabels();applyGraphLabels()`);
  for(const id of ['SYN-HUB','SYN-00000','SYN-00001'])assert.equal(text(id),1,'moving the real blocker away restores ordinary text');
  assertAppliedLabelPlacements(h);
  assert.equal(h.run('state.graphView.hoveredAccount'),null);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===originalGraphFacts'),true);
  assert.deepEqual(JSON.parse(h.run(`JSON.stringify(state.graph.getNodes().map(item=>{
    const m=item.getModel();return{id:m.id,x:m.x,y:m.y}}))`)),[
    {id:'SYN-HUB',x:200,y:150},{id:'SYN-00000',x:500,y:150},{id:'SYN-00001',x:800,y:150}]);
});

test('viewport clipping retains a crossing edge and both snapshot scopes preserve offscreen facts and canonical labels', async () => {
  const h = harness('app.js'); installSyntheticHub(h);
  h.run(`state.graph.updateItem(state.graph.getNodes().find(item=>item.getModel().id==='SYN-HUB'),{x:-4000,y:350});
    state.graph.updateItem(state.graph.getNodes().find(item=>item.getModel().id==='SYN-00000'),{x:5000,y:350});
    rebuildGraphViewport();applyGraphLabels();`);
  assert.equal(h.run('state.graph.getNodes().find(item=>item.getModel().id==="SYN-HUB").getContainer().get("visible")'), false);
  assert.equal(h.run('state.graph.getNodes().find(item=>item.getModel().id==="SYN-00000").getContainer().get("visible")'), false);
  assert.equal(h.run('state.graph.getEdges()[0].getContainer().get("visible")'), true, 'offscreen endpoints do not remove an edge crossing the view');
  assert.ok(h.run('state.graphView.drawnNodes') < 1000);
  for (const scope of ['visible','all']) {
    const snapshot = await h.run(`window.__collectSnapshot({scope:'${scope}'})`);
    assert.equal(snapshot.nodes.length,1000); assert.equal(snapshot.edges.length,999);
    assert.equal(snapshot.nodes.find(node=>node.id==='SYN-HUB').x,-4000);
    assert.equal(snapshot.nodes.find(node=>node.id==='SYN-00000').x,5000);
    assert.equal(snapshot.edges[0].label,'2笔·¥10,000,000,000,000,000.01\nUSD 0.01');
    assert.equal(snapshot.edges[0].data.amount_totals[0].amount,'10000000000000000.01');
    assert.equal(snapshot.edges[0].data.amount_totals[1].amount,'0.01');
    assert.equal(snapshot.edges[0].data.amount_totals[1].currency,'USD');
    assert.equal(snapshot.view.visible_nodes,1000,'display scope counts semantic graph membership, not viewport culling');
  }
  assert.equal(h.run('JSON.stringify(state.lastGraph)===originalGraphFacts'), true);
  h.run(`syntheticViewport={minX:4000,minY:4000,maxX:5000,maxY:4700};
    state.graph.getNodes().find(item=>item.getModel().id==='SYN-00998').getContainer().getCanvasBBox=()=>({minX:20,minY:20,maxX:40,maxY:40});
    state.graph.getEdges()[0].getContainer().getCanvasBBox=()=>({minX:20,minY:20,maxX:40,maxY:40});
    rebuildGraphViewport();`);
  assert.equal(h.run('state.graph.getNodes().find(item=>item.getModel().id==="SYN-00998").getContainer().get("visible")'),true,
    'real rendered label/image bounds remain visible even when the node center is offscreen');
  assert.equal(h.run('state.graph.getNodes().find(item=>item.getModel().id==="SYN-00997").getContainer().get("visible")'),false);
  assert.equal(h.run('state.graph.getEdges()[0].getContainer().get("visible")'),true,
    'real rendered edge bounds augment the conservative path envelope');
});

test('case and canvas A-B-A revoke queued clipping and synchronously erase the minimap', () => {
  for (const transition of ['selectCase(2);selectCase(1)','activateCanvas(7);activateCanvas(0)']) {
    const h = harness('app.js'); installSyntheticHub(h); h.flushFrames();
    h.run('applyGraphViewport();scheduleGraphViewport()');
    const stale = h.frames.filter(frame=>!frame.cancelled&&!frame.ran).map(frame=>frame.callback);
    assert.ok(stale.length>=2);
    h.run(transition);
    assert.equal(h.element('#graphMinimap').canvasCalls.at(-1)[0],'clearRect');
    assert.equal(h.run('state.graph.getNodes().length'),0);
    assert.equal(h.run('state.graphView.clipIndex'),null);
    const paints=h.run('graphPaints'),draws=h.element('#graphMinimap').canvasCalls.length;
    stale.forEach(callback=>callback());
    assert.equal(h.run('graphPaints'),paints);assert.equal(h.element('#graphMinimap').canvasCalls.length,draws);
    assert.equal(h.run('state.graphView.clipIndex'),null);
  }
});

test('unchanged viewport membership does not add a second G6 paint to native transforms', () => {
  const h=harness('app.js');installSyntheticHub(h);
  h.run('applyGraphViewport()');
  const before=h.run('graphPaints');
  h.run('applyGraphViewport();applyGraphViewport()');
  assert.equal(h.run('graphPaints'),before);
  h.run('syntheticViewport={minX:0,minY:0,maxX:200,maxY:200};applyGraphViewport()');
  assert.equal(h.run('graphPaints'),before+1);
  h.run('applyGraphViewport()');
  assert.equal(h.run('graphPaints'),before+1);
});

test('fit and PNG temporarily show every graph item then restore viewport clipping', async () => {
  const h = harness('app.js'); installSyntheticHub(h);
  h.run('syntheticViewport={minX:0,minY:0,maxX:300,maxY:300};applyGraphViewport(true)');
  const visible = h.run('state.graphView.drawnNodes');
  assert.ok(visible>0 && visible<1000);
  h.element('#btnFit').onclick();
  assert.equal(h.run('fitVisibility.at(-1)'),true,'fit bounds inspect the complete displayed graph');
  assert.equal(h.run('state.graphView.drawnNodes'),visible);
  assert.ok(h.run('state.graphView.clipped.size')>0);
  h.element('#exportFmt').value='png';
  const pending=h.element('#btnExportPng').onclick();
  assert.equal(h.run('state.graphView.exportDepth'),1);
  assert.equal(h.run('state.graphView.clipped.size'),0);
  h.timers.findLast(timer=>timer.delay===400).callback(); await pending;
  assert.equal(h.run('imageVisibility.at(-1)'),true);
  assert.equal(h.run('state.graphView.exportDepth'),0);
  assert.equal(h.run('state.graphView.drawnNodes'),visible);
  assert.ok(h.run('state.graphView.clipped.size')>0);
  assert.equal(h.downloads.length,1);
});

test('panning away and back preserves a projected legacy path ratio and exact canonical money', async () => {
  for (const saveFirst of [false,true]) {
    const h = harness('app.js'); installSyntheticHub(h,2);installSnapshotPaths(h);
    h.run(`state.positions={'SYN-HUB':{x:100,y:200},'SYN-00000':{x:500,y:200}};
      state.edgeType='line';state.graphView.labelMode='all';
      state.edgeSnapshotLabels['SYN-HUB=>SYN-00000']={lx:170,ly:230,bw:40,bh:10,manual:true};
      renderGraph();${saveFirst?'savePositions();':''}
      syntheticViewport={minX:10000,minY:10000,maxX:11000,maxY:10700};applyGraphViewport(true)`);
    assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),0);
    assert.equal(h.run('state.graph.getEdges()[0].getContainer().get("visible")'),false);
    h.run('syntheticViewport={minX:0,minY:0,maxX:1000,maxY:700};applyGraphViewport(true)');
    assert.equal(h.run('state.graph.getEdges()[0].getContainer().get("visible")'),true);
    assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),1);
    assert.equal(h.run('state.edgeLabelPos["SYN-HUB=>SYN-00000"]'),.175);
    assert.equal(h.run('state.graph.getEdges()[0].shapes[0].attr("x")'),170);
    assert.equal(h.run('state.graph.getEdges()[0].shapes[0].attr("y")'),200);
    const snapshot=await h.run('window.__collectSnapshot()');
    assert.equal(snapshot.edges[0].lx,170);assert.equal(snapshot.edges[0].ly,200);
    assert.equal(snapshot.edges[0].label,'2笔·¥10,000,000,000,000,000.01\nUSD 0.01');
    assert.equal(snapshot.edges[0].data.amount_totals[0].amount,'10000000000000000.01');
  }
});

test('all and selected focus labels retain exact crossing-edge amounts while both endpoints remain offscreen', async () => {
  const h = harness('app.js'); installSyntheticHub(h);
  h.run(`state.lastGraph={nodes:syntheticFullGraph.nodes.slice(0,2),edges:[syntheticFullGraph.edges[0]],tracked_ids:[1,2]};
    state.positions={'SYN-HUB':{x:-4000,y:350},'SYN-00000':{x:5000,y:350}};
    state.edgeSnapshotLabels={'SYN-HUB=>SYN-00000':{lx:500,ly:350,bw:300,bh:40}};
    globalThis.crossingFacts=JSON.stringify(state.lastGraph);state.forceFit=false;renderGraph();`);
  for (const mode of ['all','focus','all']) {
    if (mode==='focus') h.run('selectItem(state.graph.getEdges()[0])');
    h.element('#graphLabelMode').onchange({target:{value:mode}});
    h.timers.findLast(timer=>timer.delay===0).callback();
    assert.equal(h.run('state.graphView.drawnNodes'),0);
    assert.equal(h.run('state.graphView.drawnEdges'),1);
    assert.equal(h.run('state.graph.getNodes().every(item=>!item.getContainer().get("visible")&&item.shapes.length===0)'),true);
    assert.equal(h.run('state.graph.getEdges()[0].getContainer().get("visible")'),true);
    assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),1);
    assert.equal(h.run('state.graph.getEdges()[0].shapes.every(shape=>shape.visible)'),true);
    assert.equal(h.run('state.graph.getEdges()[0].getModel().fundsLabel'),'2笔·¥10,000,000,000,000,000.01\nUSD 0.01');
    assert.equal(h.run('state.graph.getEdges()[0].getModel().label'),'2笔·¥10,000,000,000,000,000.01\nUSD 0.01');
    const snapshot=await h.run('window.__collectSnapshot()');
    assert.equal(snapshot.nodes.length,2);assert.equal(snapshot.edges.length,1);
    assert.equal(snapshot.edges[0].labelVisible,true);
    assert.equal(snapshot.edges[0].label,'2笔·¥10,000,000,000,000,000.01\nUSD 0.01');
    assert.equal(snapshot.edges[0].data.amount_totals[0].amount,'10000000000000000.01');
    assert.equal(snapshot.edges[0].data.amount_totals[1].amount,'0.01');
    assert.equal(snapshot.edges[0].data.amount_totals[1].currency,'USD');
    assert.equal(h.run('state.caseId'),1);assert.equal(h.run('JSON.stringify(state.lastGraph)===crossingFacts'),true);
  }
  h.run('scheduleGraphLabels(true)');const stale=h.timers.findLast(timer=>timer.delay===140);
  h.run('selectCase(2);selectCase(1)');stale.callback();
  assert.equal(h.run('state.graph.getEdges().length'),0);
  assert.equal(h.run('state.lastGraph.edges.length'),0);
  assert.equal(await h.run('window.__collectSnapshot()'),null);
});

test('graph replacement revokes the vendor zoom timer and restores original shapes before disposal', () => {
  for (const [transition,boundary] of [
    ['selectCase(2)','clear'], ['activateCanvas(7)','clear'],
    ['renderGraph()','changeData'], ['state.forceRebuild=true;renderGraph()','destroy'],
  ]) {
    const h = harness('app.js'); installSyntheticHub(h);
    h.run(`globalThis.vendorTimerCalls=0;globalThis.zoomLifecycle=[];
      globalThis.oldZoomShapes=state.graph.getNodes().flatMap(item=>item.shapes);
      globalThis.expectedZoomVisibility=oldZoomShapes.map((shape,index)=>index%2===0);
      oldZoomShapes.forEach((shape,index)=>{shape.set('ori-visibility',expectedZoomVisibility[index]);shape.hide()});
      globalThis.zoomBehavior={type:'zoom-canvas',values:{optimized:true,enableOptimize:true},
        get(key){return key==='type'?this.type:this.values[key]},set(key,value){this.values[key]=value}};
      zoomBehavior.values.timeout=setTimeout(()=>{vendorTimerCalls++;
        for(const shape of oldZoomShapes){if(shape.disposed)throw new Error('vendor timer touched disposed shape');shape.show()}},100);
      const controller={currentBehaves:[zoomBehavior],get(key){return this[key]}};
      state.graph.get=key=>key==='modeController'?controller:null;
      const oldGraph=state.graph,oldClear=oldGraph.clear,oldChangeData=oldGraph.changeData,oldDestroy=oldGraph.destroy||oldClear;
      const guard=(kind,fn)=>function(...args){zoomLifecycle.push({kind,timeout:zoomBehavior.get('timeout'),
        optimized:zoomBehavior.get('optimized'),visible:oldZoomShapes.map(shape=>shape.visible),
        disposed:oldZoomShapes.some(shape=>shape.disposed===true)});
        oldZoomShapes.forEach(shape=>shape.disposed=true);return fn.apply(this,args)};
      oldGraph.clear=guard('clear',oldClear);oldGraph.changeData=guard('changeData',oldChangeData);oldGraph.destroy=guard('destroy',oldDestroy);
      globalThis.G6={Graph:class{
        constructor(){this.nodes=[];this.edges=[]}
        on(){}get(){return null}data(data){this.nodes=data.nodes.map(makeItem);this.edges=data.edges.map(makeItem)}
        changeData(data){this.data(data)}render(){}getNodes(){return this.nodes}getEdges(){return this.edges}
        setAutoPaint(){}paint(){}stopLayout(){}fitView(){}getZoom(){return .45}zoomTo(){}changeSize(){}
        getPointByCanvas(x,y){return{x,y}}updateItem(item,value){Object.assign(item.getModel(),value);if(Object.hasOwn(value,'label'))item.syncLabel()}
        clearItemStates(){}setItemState(){}clear(){this.nodes=[];this.edges=[]}destroy(){this.clear()}
      }};`);
    const vendorTimer=h.timers.findLast(timer=>timer.delay===100);
    assert.ok(vendorTimer);
    h.run(transition);
    const observations=Array.from(h.run('zoomLifecycle'));
    const observed=observations.find(item=>item.kind===boundary);
    assert.ok(observed,transition);
    assert.equal(vendorTimer.cancelled,true,transition);
    assert.equal(observed.timeout,undefined,`${transition}: timer reference revoked before disposal`);
    assert.equal(observed.optimized,false,`${transition}: next gesture starts with clean optimization state`);
    assert.equal(observed.disposed,false,`${transition}: original shapes still exist when restored`);
    assert.deepEqual(Array.from(observed.visible),Array.from(h.run('expectedZoomVisibility')),
      `${transition}: restore the original visible and hidden states before replacing items`);
    // Model the event loop: cancelled callbacks are removed rather than invoked manually.
    for(const timer of h.timers.filter(timer=>timer.delay===100&&!timer.cancelled))timer.callback();
    assert.equal(h.run('vendorTimerCalls'),0,transition);
    assert.equal(h.run('zoomBehavior.get("timeout")'),undefined);
    assert.equal(h.run('zoomBehavior.get("optimized")'),false);
  }
});

test('dense overview defers arrows but selected, hovered, all, neighborhood and image exports preserve direction', async () => {
  for(const format of ['png','pdf']) {
    const h=harness('app.js');installSyntheticHub(h,1002);h.network(()=>response({}));
    assert.equal(h.run('state.graph.getEdges().length'),1001);
    assert.equal(h.run('state.graphView.edgeDensityMode'),true);
    assert.equal(h.run('state.graph.getEdges().every(item=>item.getModel().style.endArrow===false&&item.getModel().style.strokeOpacity===1&&item.getModel().style.stroke==="#3f4854")'),true);
    h.run(`selectItem(state.graph.getEdges()[0]);applyGraphLabels();state.graphView.hoveredAccount='SYN-00001';applyGraphLabels()`);
    assert.equal(h.run('state.graph.getEdges()[0].getModel().style.strokeOpacity'),1);
    assert.equal(h.run('typeof state.graph.getEdges()[0].getModel().style.endArrow.path'),'string');
    assert.equal(h.run('state.graph.getEdges()[1].getModel().style.strokeOpacity'),1);
    assert.equal(h.run('typeof state.graph.getEdges()[1].getModel().style.endArrow.path'),'string');
    assert.equal(h.run('state.graph.getEdges()[2].getModel().style.endArrow'),false);
    h.run(`state.graphView.selectedAccount='SYN-HUB';state.graphView.hoveredAccount='SYN-01000';applyGraphLabels()`);
    assert.ok(h.run('state.graphView.focusedEdges.size')<=80,'selected and hovered centers share one direction budget');
    assert.equal(h.run('state.graphView.focusedEdges.has("SYN-HUB=>SYN-00000")'),true,'the selected edge reserves a direction slot');
    assert.equal(h.run('state.graphView.focusedEdges.has("SYN-HUB=>SYN-01000")'),true,'a distant hovered leaf is not starved by the selected hub');
    assert.equal(h.run('typeof state.graph.getEdges()[1000].getModel().style.endArrow.path'),'string');
    h.run('selectItem(null);state.graphView.hoveredAccount=null;applyGraphLabels()');
    assert.equal(h.run('state.graph.getEdges().every(item=>item.getModel().style.endArrow===false)'),true);
    h.element('#graphLabelMode').onchange({target:{value:'all'}});h.timers.findLast(timer=>timer.delay===0).callback();
    assert.equal(h.run('state.graph.getEdges().every(item=>typeof item.getModel().style.endArrow.path==="string"&&item.getModel().style.strokeOpacity===1)'),true);
    h.element('#graphLabelMode').onchange({target:{value:'auto'}});h.timers.findLast(timer=>timer.delay===0).callback();
    assert.equal(h.run('state.graph.getEdges().every(item=>item.getModel().style.endArrow===false)'),true);
    const full=await h.run('window.__collectSnapshot({scope:"all"})');
    assert.equal(full.nodes.length,1002);assert.equal(full.edges.length,1001);
    assert.equal(full.edges[0].label,'2笔·¥10,000,000,000,000,000.01\nUSD 0.01');
    assert.equal(full.edges[0].data.amount_totals[0].amount,'10000000000000000.01');
    assert.equal(full.edges[0].data.amount_totals[1].currency,'USD');
    assert.equal(h.run('JSON.stringify(state.lastGraph)===originalGraphFacts'),true);
    h.element('#exportFmt').value=format;const pending=h.element('#btnExportPng').onclick();
    assert.equal(h.run('state.graph.getEdges().every(item=>typeof item.getModel().style.endArrow.path==="string"&&item.getModel().style.strokeOpacity===1)'),true);
    h.timers.findLast(timer=>timer.delay===400).callback();await pending;
    assert.equal(h.run('state.graphView.exportDepth'),0);
    assert.equal(h.run('state.graph.getEdges().every(item=>item.getModel().style.endArrow===false)'),true);
    assert.equal(h.downloads.length,1);
    h.element('#neighborAccount').value='SYN-HUB';h.element('#btnNeighborhood').onclick();
    assert.equal(h.run('state.graph.getEdges().length'),50);
    assert.equal(h.run('state.graphView.edgeDensityMode'),false);
    assert.equal(h.run('state.graph.getEdges().every(item=>typeof item.getModel().style.endArrow.path==="string"&&item.getModel().style.strokeOpacity===1)'),true);
    assert.equal(h.run('state.lastGraph.edges.length'),1001);
  }
});

test('complete graph reuse and frozen viewport inverse avoid work proportional to every label update', () => {
  const h=harness('app.js');installSyntheticHub(h);
  h.run(`const originalViewApi=FundsGraphView;globalThis.indexBuilds=0;
    state.graphView.fullIndex=originalViewApi.createIndex(state.lastGraph);
    FundsGraphView={...originalViewApi,createIndex(graph){indexBuilds++;return originalViewApi.createIndex(graph)}};
    renderGraph();renderGraph();`);
  assert.equal(h.run('state.graphView.index===state.graphView.fullIndex'),true);
  assert.equal(h.run('indexBuilds'),0);
  h.run(`globalThis.modelReads=0;
    for(const item of [...state.graph.getNodes(),...state.graph.getEdges()]) {
      const original=item.getModel;item.getModel=()=>{modelReads++;return original()};
    }
    applyGraphLabels(false);`);
  assert.ok(h.run('modelReads')<200,'a bounded 20-label update must not read all 1999 graph models');
  h.run('scheduleGraphLabels(true);globalThis.redundantLabelTimer=state.graphView.labelTimer;fitGraph();');
  assert.equal(h.timers[h.run('redundantLabelTimer')-1].cancelled,true,'fit settles labels and cancels its redundant delayed pass');
  assert.equal(h.run('state.graphView.labelTimer'),null);
  h.run(`globalThis.inverseReads=0;const originalInverse=state.graph.getPointByCanvas;
    state.graph.getPointByCanvas=(x,y)=>{inverseReads++;return originalInverse(x,y)};
    for(const item of [...state.graph.getNodes(),...state.graph.getEdges()]) {
      item.getContainer().getCanvasBBox=()=>({minX:0,minY:0,maxX:1,maxY:1});
    }
    rebuildGraphViewport();`);
  assert.equal(h.run('inverseReads'),5,'three affine inverse samples plus two viewport corners, independent of item count');
  assert.equal(h.run('state.graph.getNodes().length'),1000);assert.equal(h.run('state.graph.getEdges().length'),999);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===originalGraphFacts'),true);
});

const paddedLabelBox = (bounds, padding) => ({ minX: bounds.minX-padding, minY: bounds.minY-padding,
  maxX: bounds.maxX+padding, maxY: bounds.maxY+padding });
const boxesIntersect = (a,b) => a.minX<=b.maxX && a.maxX>=b.minX && a.minY<=b.maxY && a.maxY>=b.minY;
function assertAppliedLabelPlacements(h, { retainAll=false }={}) {
  const result=JSON.parse(JSON.stringify(h.run(`({
    conflicts:state.graphView.policy.conflicts||0,gap:6*(state.graphView.exportLabelScale||1),
    bodies:[...state.graphView.nodeItems].map(([key,item])=>({key,bounds:graphNodeBodyBounds(item)})),
    placements:[...[...state.graphView.policy.nodePlacements].map(([key,p])=>({key,kind:'node',...p,
      required:state.graphSelection?.ids.has(key)||key===state.graphView.selectedAccount||key===state.graphView.hoveredAccount})),
      ...[...state.graphView.policy.edgePlacements].map(([key,p])=>({key,kind:'edge',...p,
        required:key===state.graphView.selectedEdge||state.edgeLabelPos[key]!=null
          ||state.edgeSnapshotLabels[key]?.manual!==false&&state.edgeSnapshotLabels[key]?.lx!=null}))].map(p=>({
      ...p,shapes:labelShapes((p.kind==='node'?state.graphView.nodeItems:state.graphView.edgeItems).get(p.key)).map(shape=>{
        const offset=shape._fundsLabelOffset,actual={x:shape.attr('x'),y:shape.attr('y')};
        const base=offset?state.graph.getCanvasByPoint(offset.baseX,offset.baseY):null;
        const applied=offset?state.graph.getCanvasByPoint(actual.x,actual.y):null;
        return{offset,actual,bounds:shape.getCanvasBBox?.(),body:p.kind==='node'?graphNodeBodyBounds(state.graphView.nodeItems.get(p.key)):null,
          screenOffset:base&&{dx:applied.x-base.x,dy:applied.y-base.y}}})}))})`)));
  assert.equal(result.conflicts,result.placements.filter(p=>p.bounds&&p.conflicted).length,'every retained conflict is counted');
  for(const placement of result.placements) {
    assert.ok(placement.bounds,`${placement.kind} ${placement.key} has measured placement geometry`);
    if(placement.conflicted) assert.ok(retainAll||placement.required,`${placement.key} cannot silently retain an ordinary conflict`);
    else {
      assert.ok(placement.bounds.minX>=4&&placement.bounds.minY>=4&&placement.bounds.maxX<=996&&placement.bounds.maxY<=696,
        `${placement.key} fits the actual viewport`);
      for(const body of result.bodies.filter(body=>body.bounds)) assert.equal(
        boxesIntersect(paddedLabelBox(placement.bounds,3),paddedLabelBox(body.bounds,2)),false,
        `${placement.key} avoids node body ${body.key}`);
    }
    assert.equal(placement.shapes.length,1,`${placement.key} is represented by one plain G6 text shape`);
    for(const shape of placement.shapes) {
      if(placement.dx||placement.dy) {
        assert.ok(shape.offset,`${placement.key} applies its chosen offset`);
        assert.equal(shape.actual.x,shape.offset.x);assert.equal(shape.actual.y,shape.offset.y);
        assert.ok(Math.abs(shape.screenOffset.dx-placement.dx)<1e-8);
        assert.ok(Math.abs(shape.screenOffset.dy-placement.dy)<1e-8);
      } else assert.equal(shape.offset,undefined,'an unshifted placement has no stale automatic offset');
      if(placement.kind==='node') {
        assert.ok(Math.abs((shape.bounds.minX+shape.bounds.maxX-shape.body.minX-shape.body.maxX)/2)<1e-8,
          `${placement.key} stays horizontally centered on its own node`);
        assert.ok(Math.abs(shape.bounds.minY-shape.body.maxY-result.gap)<1e-8,
          `${placement.key} remains below the body by the fixed screen gap`);
      }
    }
  }
  for(let i=0;i<result.placements.length;i++) for(let j=i+1;j<result.placements.length;j++) {
    const a=result.placements[i],b=result.placements[j];
    if(a.conflicted||b.conflicted)continue;
    assert.equal(boxesIntersect(paddedLabelBox(a.bounds,3),paddedLabelBox(b.bounds,3)),false,
      `${a.key} and ${b.key} have separate final placements`);
  }
  return result;
}

test('screen label collisions preserve required focus, explicit all and image export without changing exact facts', async () => {
  const h=harness('app.js');installSyntheticHub(h);
  h.run(`state.lastGraph={nodes:syntheticFullGraph.nodes.slice(0,3),edges:syntheticFullGraph.edges.slice(0,2),tracked_ids:[1,2,3]};
    state.graphView.fullIndex=FundsGraphView.createIndex(state.lastGraph);
    state.positions={'SYN-HUB':{x:500,y:350},'SYN-00000':{x:500,y:350},'SYN-00001':{x:500,y:350}};
    globalThis.collidingFacts=JSON.stringify(state.lastGraph);
    syntheticLabelBounds=()=>({minX:200,minY:200,maxX:300,maxY:240});renderGraph();`);
  const labelCount=()=>h.run('[...state.graph.getNodes(),...state.graph.getEdges()].filter(item=>labelShapes(item).length===1).length');
  const automatic=assertAppliedLabelPlacements(h);
  assert.equal(automatic.placements.length,1,'coincident nodes retain the highest-priority anchored label in auto mode');
  assert.ok(automatic.placements.every(placement=>placement.dx===0&&placement.dy===0),'collisions never move node text away from its bottom anchor');
  assert.equal(labelCount(),automatic.placements.length+2,'both edge labels remain at their own paths despite node collision decisions');
  assert.equal(h.run('state.graphView.policy.nodeIds.has("SYN-HUB")'),true,'main node wins ordinary priority');
  h.run(`selectItem(state.graph.getEdges()[0]);state.graphView.hoveredAccount='SYN-00001'`);
  h.element('#graphLabelMode').onchange({target:{value:'focus'}});h.timers.findLast(timer=>timer.delay===0).callback();
  assertAppliedLabelPlacements(h);
  assert.equal(h.run('state.graphView.policy.edgeKeys.has("SYN-HUB=>SYN-00000")'),true);
  assert.equal(h.run('state.graphView.policy.nodeIds.has("SYN-00001")'),true);
  h.element('#graphLabelMode').onchange({target:{value:'all'}});h.timers.findLast(timer=>timer.delay===0).callback();
  assert.equal(labelCount(),5,'all explicitly retains three node and two edge labels');
  assertAppliedLabelPlacements(h,{retainAll:true});
  h.run('selectItem(null);state.graphView.hoveredAccount=null');
  h.element('#graphLabelMode').onchange({target:{value:'auto'}});h.timers.findLast(timer=>timer.delay===0).callback();
  assertAppliedLabelPlacements(h);
  const visible=await h.run('window.__collectSnapshot()'),full=await h.run('window.__collectSnapshot({scope:"all"})');
  for(const snapshot of [visible,full]) {
    assert.equal(snapshot.nodes.length,3);assert.equal(snapshot.edges.length,2);
    assert.equal(snapshot.edges[0].label,'2笔·¥10,000,000,000,000,000.01\nUSD 0.01');
    assert.equal(snapshot.edges[0].data.amount_totals[0].amount,'10000000000000000.01');
    assert.equal(snapshot.edges[0].data.amount_totals[1].amount,'0.01');
    assert.equal(snapshot.edges[0].data.amount_totals[1].currency,'USD');
  }
  assert.equal(visible.edges[0].labelVisible,h.run('state.graphView.policy.edgeKeys.has("SYN-HUB=>SYN-00000")'));
  assert.equal(full.edges[0].labelVisible,true);
  const beforeExport=h.run('[...state.graphView.policy.nodeIds,...state.graphView.policy.edgeKeys].sort().join("|")');
  h.element('#exportFmt').value='png';const pending=h.element('#btnExportPng').onclick();
  assert.equal(labelCount(),5,'image export retains every label at its fixed node or relationship anchor');
  assertAppliedLabelPlacements(h,{retainAll:true});
  h.timers.findLast(timer=>timer.delay===400).callback();await pending;
  assertAppliedLabelPlacements(h);
  assert.equal(h.run('[...state.graphView.policy.nodeIds,...state.graphView.policy.edgeKeys].sort().join("|")'),beforeExport,
    'automatic placement membership resumes after capture');
  assert.equal(h.downloads.length,1);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===collidingFacts'),true);
});

function installSavedLabelPair(h, { mode='auto', manualRatio=null }={}) {
  installSyntheticHub(h);
  installSnapshotPaths(h,{stubInit:false});
  // These label-reanchoring fixtures use a straight logical path throughout.
  // Declare it before the first render projects legacy absolute coordinates;
  // an automatic compact arc has a different nearest arc-length ratio.
  h.run(`state.lastGraph={nodes:syntheticFullGraph.nodes.slice(0,2),edges:[syntheticFullGraph.edges[0]],tracked_ids:[1,2]};
    state.graphView.fullIndex=FundsGraphView.createIndex(state.lastGraph);state.graphView.labelMode='${mode}';
    state.positions={'SYN-HUB':{x:2700,y:2800},'SYN-00000':{x:3000,y:2800}};
    state.edgeTypes={'SYN-HUB=>SYN-00000':'line'};
    state.edgeSnapshotLabels={'SYN-HUB=>SYN-00000':{lx:2771,ly:2823,bw:300,bh:40}};
    globalThis.pairFacts=JSON.stringify(state.lastGraph);state.forceFit=false;renderGraph();
    const edge=state.graph.getEdges()[0];
    edge.getKeyShape=()=>({attr(name){if(name!=='path')return undefined;
      const a=state.graph.getNodes().find(node=>node.getModel().id==='SYN-HUB').getModel();
      const b=state.graph.getNodes().find(node=>node.getModel().id==='SYN-00000').getModel();
      return [['M',a.x,a.y],['L',b.x,b.y]]},getPoint(r){
        const path=this.attr('path');return{x:path[0][1]+(path[1][1]-path[0][1])*r,y:path[0][2]+(path[1][2]-path[0][2])*r}}});
    ${manualRatio===null?'':`state.edgeLabelPos['SYN-HUB=>SYN-00000']=${manualRatio};`}
    ${mode==='auto'?'selectItem(edge);':''}
  `);
}

async function assertPairSnapshot(h, expectedX, expectedY) {
  const snapshot=await h.run('window.__collectSnapshot({scope:"all"})');
  const rendered=h.run('state.graph.getEdges()[0].shapes.some(shape=>shape.get("name")==="text-shape")');
  assert.equal(snapshot.nodes.length,2);assert.equal(snapshot.edges.length,1);
  assert.equal(snapshot.edges[0].lx,rendered?Number(expectedX.toFixed(2)):expectedX);
  assert.equal(snapshot.edges[0].ly,rendered?Number(expectedY.toFixed(2)):expectedY);
  assert.equal(snapshot.edges[0].label,'2笔·¥10,000,000,000,000,000.01\nUSD 0.01');
  assert.equal(snapshot.edges[0].data.amount_totals[0].amount,'10000000000000000.01');
  assert.equal(snapshot.edges[0].data.amount_totals[1].amount,'0.01');
  assert.equal(snapshot.edges[0].data.amount_totals[1].currency,'USD');
  assert.deepEqual(Array.from(snapshot.edges[0].data.ids),[1,2]);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===pairFacts'),true);
}

test('public relayout moves saved absolute labels with the new path, including hidden full saves and manual ratios', async () => {
  for(const settings of [{mode:'focus',manualRatio:null},{mode:'auto',manualRatio:null},{mode:'auto',manualRatio:0.75}]) {
    const workers=[];
    class PairWorker {
      constructor(){workers.push(this)}postMessage(message){this.message=message}terminate(){}
      deliver(nodes){this.onmessage({data:layoutWorkerResponse(h,this.message,nodes)})}
    }
    const h=harness('app.js','http://127.0.0.1:8300/','1',{Worker:PairWorker});installSavedLabelPair(h,settings);
    await h.element('#btnRelayout').onclick();
    assert.equal(workers.length,1);
    workers[0].deliver([{id:'SYN-HUB',x:100,y:100},{id:'SYN-00000',x:300,y:100}]);
    await waitForLayoutCommit(h);
    const ratio=settings.manualRatio??71/300,expectedX=100+200*ratio;
    assert.ok(Math.abs(h.run('state.edgeLabelPos["SYN-HUB=>SYN-00000"]')-ratio)<1e-12);
    assert.ok(Math.abs(h.run('state.edgeSnapshotLabels["SYN-HUB=>SYN-00000"].lx')-expectedX)<1e-10);
    assert.equal(h.run('state.edgeSnapshotLabels["SYN-HUB=>SYN-00000"].ly'),100);
    if(settings.mode==='focus') assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),0);
    else {
      assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),1);
      assert.ok(Math.abs(h.run('state.graph.getEdges()[0].shapes[0].attr("x")')-expectedX)<1e-10);
      assert.equal(h.run('state.graph.getEdges()[0].shapes[0].attr("y")'),100);
    }
    await assertPairSnapshot(h,expectedX,100);
  }
});

test('ordinary small-graph relayout reserves node and relationship text extents even when focus hides labels',async()=>{
  const workers=[];
  class MeasuredLayoutWorker {
    constructor(){workers.push(this)}postMessage(message){this.message=message}terminate(){}
  }
  const h=harness('app.js','http://127.0.0.1:8300/','1',{Worker:MeasuredLayoutWorker});installSyntheticHub(h,3);
  h.run(`state.graphView.labelMode='focus';state.graphView.selectedAccount=null;state.graphView.selectedEdge=null;
    state.graphView.hoveredAccount=null;renderGraph();`);
  assert.equal(h.run('state.graph.getEdges().every(item=>labelShapes(item).length===0)'),true,
    'the fixture has lazy relationship labels instead of precreated text shapes');
  const before=h.run('JSON.stringify(state.lastGraph)');
  await h.element('#btnRelayout').onclick();
  assert.equal(workers.length,1);
  const input=workers[0].message.input;
  assert.equal(input.nodes.length,3);assert.equal(input.edges.length,2);
  for(const node of input.nodes) {
    assert.ok(node.width>node.size,'ordinary layout accounts for the full node text width');
    assert.ok(node.height>node.size,'the two-line node text contributes vertical spacing');
    assert.ok(node.offsetY>0,'text below the circle has an asymmetric envelope');
  }
  assert.ok(Math.abs(input.edges[0].labelWidth-186)<1e-9,
    'the 29-character full amount uses the harness six-pixel glyph metric plus the text padding');
  assert.equal(h.run('JSON.stringify(state.lastGraph)'),before,'measurement does not rewrite transaction facts');
  h.run('cancelGraphLayout()');
});

test('public node drag reanchors lazy and visible labels while preserving an existing manual path ratio', async () => {
  for(const settings of [{mode:'focus',manualRatio:null},{mode:'auto',manualRatio:null},{mode:'auto',manualRatio:0.75}]) {
    const h=harness('app.js');installSavedLabelPair(h,settings);
    h.run(`syntheticViewport={minX:2500,minY:2500,maxX:3500,maxY:3200};applyGraphViewport(true);
      const currentGraph=state.graph;state.minimap.destroy();state.minimap=null;state.graph=null;
      globalThis.G6={Graph:function(){return currentGraph}};initGraph();
      for(const callback of syntheticGraphEvents.get('node:dragstart'))callback({});
      state.graph.updateItem(state.graph.getNodes().find(node=>node.getModel().id==='SYN-00000'),{x:3300,y:2800});
      for(const callback of syntheticGraphEvents.get('dragnodeend'))callback({});
      for(const callback of syntheticGraphEvents.get('node:dragend'))callback({});
    `);
    h.timers.findLast(timer=>timer.delay===0).callback();
    const ratio=settings.manualRatio??71/300,expectedX=2700+600*ratio;
    assert.ok(Math.abs(h.run('state.edgeLabelPos["SYN-HUB=>SYN-00000"]')-ratio)<1e-12);
    assert.equal(h.run('state.positions["SYN-00000"].x'),3300);
    assert.ok(Math.abs(h.run('state.edgeSnapshotLabels["SYN-HUB=>SYN-00000"].lx')-expectedX)<1e-10);
    assert.equal(h.run('state.edgeSnapshotLabels["SYN-HUB=>SYN-00000"].ly'),2800);
    if(settings.mode==='focus')assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),0);
    else assert.ok(Math.abs(h.run('state.graph.getEdges()[0].shapes[0].attr("x")')-expectedX)<1e-10);
    await assertPairSnapshot(h,expectedX,2800);
  }
});

function installMeasuredFitCamera(h, boxes, initial={zoom:1,x:0,y:0}) {
  const env=h.run('globalThis');env.fitMeasuredBoxes=boxes;env.fitInitialCamera=initial;
  h.run(`globalThis.measuredFit={...fitInitialCamera,zooms:[],translations:[],fallbacks:[]};
    state.graph.getPointByCanvas=(x,y)=>({x:(x-measuredFit.x)/measuredFit.zoom,y:(y-measuredFit.y)/measuredFit.zoom});
    state.graph.getCanvasByPoint=(x,y)=>({x:x*measuredFit.zoom+measuredFit.x,y:y*measuredFit.zoom+measuredFit.y});
    state.graph.getZoom=()=>measuredFit.zoom;
    state.graph.zoomTo=zoom=>{measuredFit.zoom=zoom;measuredFit.zooms.push(zoom)};
    state.graph.translate=(x,y)=>{measuredFit.x+=x;measuredFit.y+=y;measuredFit.translations.push({x,y})};
    state.graph.fitView=padding=>measuredFit.fallbacks.push([...padding]);
    for(const item of [...state.graph.getNodes(),...state.graph.getEdges()])item.getContainer().getCanvasBBox=()=>{
      const box=fitMeasuredBoxes[item.getModel().id]||fitMeasuredBoxes.default;
      const a=state.graph.getCanvasByPoint(box.minX,box.minY),b=state.graph.getCanvasByPoint(box.maxX,box.maxY);
      return{minX:a.x,minY:a.y,maxX:b.x,maxY:b.y}};`);
  return () => JSON.parse(h.run('JSON.stringify({zoom:measuredFit.zoom,x:measuredFit.x,y:measuredFit.y})'));
}

test('public small-graph fit clamps zoom and centers actual bounds without an excessive G6 fit request', () => {
  const h=harness('app.js');installSavedLabelPair(h);
  h.run(`globalThis.smallFit={zoom:1,x:0,y:0,zooms:[],translations:[],unsafeFits:0};
    globalThis.fitWorldBounds={minX:1984,minY:1984,maxX:2017,maxY:2016};
    state.graph.getPointByCanvas=(x,y)=>({x:(x-smallFit.x)/smallFit.zoom,y:(y-smallFit.y)/smallFit.zoom});
    state.graph.getCanvasByPoint=(x,y)=>({x:x*smallFit.zoom+smallFit.x,y:y*smallFit.zoom+smallFit.y});
    state.graph.getZoom=()=>smallFit.zoom;
    state.graph.zoomTo=zoom=>{smallFit.zoom=zoom;smallFit.zooms.push(zoom)};
    state.graph.translate=(x,y)=>{smallFit.x+=x;smallFit.y+=y;smallFit.translations.push({x,y})};
    state.graph.fitView=()=>{smallFit.unsafeFits++;throw new Error('unbounded G6 small fit should not be requested')};
    for(const item of [...state.graph.getNodes(),...state.graph.getEdges()])item.getContainer().getCanvasBBox=()=>{
      const a=state.graph.getCanvasByPoint(fitWorldBounds.minX,fitWorldBounds.minY);
      const b=state.graph.getCanvasByPoint(fitWorldBounds.maxX,fitWorldBounds.maxY);
      return{minX:a.x,minY:a.y,maxX:b.x,maxY:b.y}};
  `);
  h.element('#btnFit').onclick();
  assert.deepEqual(Array.from(h.run('smallFit.zooms')),[1.6]);
  assert.equal(h.run('smallFit.unsafeFits'),0);
  assert.ok(Math.abs(h.run('state.graph.getCanvasByPoint(2000.5,2000).x')-500)<1e-10);
  assert.ok(Math.abs(h.run('state.graph.getCanvasByPoint(2000.5,2000).y')-350)<1e-10);
  h.run('fitGraph(1.25)');
  assert.equal(h.run('smallFit.zooms.at(-1)'),1.25,'callers retain an explicit lower zoom cap');
  h.run('fitWorldBounds={minX:-1e9,minY:-1e9,maxX:1e9,maxY:1e9}');h.element('#btnFit').onclick();
  assert.ok(Math.abs(h.run('smallFit.zooms.at(-1)')-0.02)<1e-12);
  assert.equal(h.run('smallFit.unsafeFits'),0);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===pairFacts'),true);
});

test('public graph fit uses free corner space while avoiding legend and minimap with the full rendered bounds',()=>{
  const h=harness('app.js');installSavedLabelPair(h);
  const container=h.element('#graphContainer');
  const rectangle=(left,top,width,height)=>({left,top,right:left+width,bottom:top+height,width,height});
  const containerRect=rectangle(100,200,1000,700);
  container.getBoundingClientRect=()=>({...containerRect});
  for(const [selector,rect] of [['#legend',rectangle(110,800,300,80)],['#graphMinimapPanel',rectangle(890,700,200,190)]]) {
    const node=h.element(selector);node.hidden=false;
    node.getBoundingClientRect=()=>({...rect});node.getClientRects=()=>[{...rect}];
  }
  h.run(`globalThis.chromeFit={zoom:1,x:0,y:0,zooms:[],translations:[],unsafeFits:0};
    globalThis.chromeWorldBounds={minX:1000,minY:2000,maxX:1900,maxY:2600};
    const positions={'SYN-HUB':{x:1050,y:2050},'SYN-00000':{x:1850,y:2550}};
    for(const node of state.graph.getNodes())state.graph.updateItem(node,positions[node.getModel().id]);
    state.positions=JSON.parse(JSON.stringify(positions));
    state.graph.getPointByCanvas=(x,y)=>({x:(x-chromeFit.x)/chromeFit.zoom,y:(y-chromeFit.y)/chromeFit.zoom});
    state.graph.getCanvasByPoint=(x,y)=>({x:x*chromeFit.zoom+chromeFit.x,y:y*chromeFit.zoom+chromeFit.y});
    state.graph.getZoom=()=>chromeFit.zoom;
    state.graph.zoomTo=zoom=>{chromeFit.zoom=zoom;chromeFit.zooms.push(zoom)};
    state.graph.translate=(x,y)=>{chromeFit.x+=x;chromeFit.y+=y;chromeFit.translations.push({x,y})};
    state.graph.fitView=()=>{chromeFit.unsafeFits++;throw new Error('small graph must use measured bounds')};
    for(const item of [...state.graph.getNodes(),...state.graph.getEdges()])item.getContainer().getCanvasBBox=()=>{
      const a=state.graph.getCanvasByPoint(chromeWorldBounds.minX,chromeWorldBounds.minY);
      const b=state.graph.getCanvasByPoint(chromeWorldBounds.maxX,chromeWorldBounds.maxY);
      return{minX:a.x,minY:a.y,maxX:b.x,maxY:b.y}};
    rebuildGraphViewport();`);
  const positions=h.run('JSON.stringify(state.positions)'),models=h.run('JSON.stringify(state.graph.getNodes().map(item=>({id:item.getModel().id,x:item.getModel().x,y:item.getModel().y})))');
  assert.equal(h.run('graphChromeObstacles().length'),2,'the fit reads actual nonzero-origin DOM rectangles');
  h.element('#btnFit').onclick();
  const bounds=JSON.parse(h.run('JSON.stringify(state.graph.getNodes()[0].getContainer().getCanvasBBox())'));
  for(const obstacle of h.run('graphChromeObstacles()')) {
    const padded=paddedLabelBox(obstacle.bounds,24),epsilon=1e-8;
    assert.ok(bounds.maxX<=padded.minX+epsilon||bounds.minX>=padded.maxX-epsilon||
      bounds.maxY<=padded.minY+epsilon||bounds.minY>=padded.maxY-epsilon,
    `the complete measured graph retains 24 px of clearance from ${obstacle.key}`);
  }
  assert.ok(bounds.minX>=24-1e-8&&bounds.minY>=24-1e-8&&bounds.maxX<=976+1e-8&&bounds.maxY<=676+1e-8,
    'the complete graph stays within the 24 px canvas padding');
  assert.ok(h.run('chromeFit.zoom')>440/600,
    'corner overlays must not reserve their full height across the entire canvas as the old bottom strip did');
  assert.equal(h.run('chromeFit.unsafeFits'),0);
  assert.ok(h.run('chromeFit.zooms.length')>=1&&h.run('chromeFit.zooms.length')<=2,
    'a fixed measured envelope needs at most the common starting scale and its final fitted scale');
  assert.equal(h.run('chromeFit.translations.length'),1);
  assert.equal(h.run('JSON.stringify(state.positions)'),positions);
  assert.equal(h.run('JSON.stringify(state.graph.getNodes().map(item=>({id:item.getModel().id,x:item.getModel().x,y:item.getModel().y})))'),models);
  assert.deepEqual([container.clientWidth,container.clientHeight],[1000,700]);
  assert.deepEqual(container.getBoundingClientRect(),containerRect);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===pairFacts'),true);
});

test('public fit includes rendered node and relationship labels and is independent of the previous camera',()=>{
  const cameras=[];
  for(const initial of [{zoom:.07,x:400,y:-200},{zoom:2.7,x:-800,y:500},{zoom:1,x:0,y:0}]) {
    const h=harness('app.js');installSavedLabelPair(h);
    // The relationship label and the left node label extend well beyond both
    // node bodies. Using only model positions would incorrectly hit the cap.
    const boxes={
      'SYN-HUB':{minX:-400,minY:-50,maxX:30,maxY:90},
      'SYN-00000':{minX:260,minY:-40,maxX:330,maxY:90},
      default:{minX:-50,minY:-300,maxX:800,maxY:500},
    };
    const camera=installMeasuredFitCamera(h,boxes,initial);
    h.element('#btnFit').onclick();const first=camera();cameras.push(first);
    assert.ok(Math.abs(first.zoom-952/1200)<1e-8,'the full rendered width limits fitting');
    assert.equal(h.run('measuredFit.fallbacks.length'),0,'measured fit does not ask G6 to refit differently');
    for(const box of Object.values(boxes)) {
      const a=h.run(`state.graph.getCanvasByPoint(${box.minX},${box.minY})`);
      const b=h.run(`state.graph.getCanvasByPoint(${box.maxX},${box.maxY})`);
      assert.ok(a.x>=24-1e-8&&a.y>=24-1e-8&&b.x<=976+1e-8&&b.y<=676+1e-8,
        'all measured text and node extents remain visible within the real canvas');
    }
    h.element('#btnFit').onclick();const second=camera();
    for(const key of ['zoom','x','y'])assert.ok(Math.abs(second[key]-first[key])<1e-8,`repeat fit preserves ${key}`);
  }
  for(const camera of cameras.slice(1))for(const key of ['zoom','x','y'])
    assert.ok(Math.abs(camera[key]-cameras[0][key])<1e-8,`different starting cameras converge to the same ${key}`);
});

test('public fit tolerates degenerate, invalid and minimum-size host bounds without producing an invalid camera',()=>{
  for(const fixture of [
    {bounds:{minX:100,minY:200,maxX:100,maxY:200},zoom:1.6},
    {bounds:{minX:-1e9,minY:-1e9,maxX:1e9,maxY:1e9},zoom:.02},
    {bounds:{minX:NaN,minY:0,maxX:20,maxY:30},fallback:true},
    {bounds:{minX:10,minY:20,maxX:100,maxY:200},size:[32,32],fallback:true},
  ]) {
    const h=harness('app.js');installSavedLabelPair(h);
    if(fixture.size){h.element('#graphContainer').clientWidth=fixture.size[0];h.element('#graphContainer').clientHeight=fixture.size[1]}
    const camera=installMeasuredFitCamera(h,{default:fixture.bounds});
    h.element('#btnFit').onclick();
    assert.ok(Object.values(camera()).every(Number.isFinite),'camera state is always finite');
    assert.ok(camera().zoom>=.02&&camera().zoom<=1.6);
    if(fixture.fallback)assert.deepEqual(JSON.parse(h.run('JSON.stringify(measuredFit.fallbacks)')),[[24,24,24,24]]);
    else assert.ok(Math.abs(camera().zoom-fixture.zoom)<1e-8);
    assert.equal(h.run('JSON.stringify(state.lastGraph)===pairFacts'),true);
  }
});

test('short relationship labels keep path ownership despite endpoint and third-node conflicts in all and export', async () => {
  const h=harness('app.js');installSyntheticHub(h);
  h.run(`state.lastGraph={nodes:syntheticFullGraph.nodes.slice(0,3),edges:[syntheticFullGraph.edges[0]],tracked_ids:[1,2]};
    state.graphView.fullIndex=FundsGraphView.createIndex(state.lastGraph);state.graphView.labelMode='auto';
    state.positions={'SYN-HUB':{x:450,y:350},'SYN-00000':{x:550,y:350},'SYN-00001':{x:500,y:318}};
    syntheticLabelBounds=model=>model.source?{minX:300,minY:340,maxX:700,maxY:360}
      :{minX:model.x-10,minY:400,maxX:model.x+10,maxY:420};
    globalThis.endpointFacts=JSON.stringify(state.lastGraph);state.forceFit=false;renderGraph();`);
  let geometry=assertAppliedLabelPlacements(h);
  assert.equal(geometry.placements.some(p=>p.kind==='edge'),false,'edge labels never enter free-space node placement');
  assert.equal(h.run('state.graphView.policy.edgeKeys.has("SYN-HUB=>SYN-00000")'),true);
  const thirdBody=geometry.bodies.find(body=>body.key==='SYN-00001').bounds;
  assert.equal(boxesIntersect({minX:300,minY:308,maxX:700,maxY:328},thirdBody),true,
    'the third node deliberately blocks the first upward edge-label candidate');
  assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),1);
  h.run(`for(const node of state.graph.getNodes())node.getKeyShape=()=>({
    getBBox(){return{minX:-20,minY:-20,maxX:20,maxY:20}},getCanvasBBox(){const m=node.getModel();
    return{minX:m.x-20,minY:m.y-20,maxX:m.x+20,maxY:m.y+20}}});rebuildGraphViewport();`);
  h.element('#graphLabelMode').onchange({target:{value:'auto'}});h.timers.findLast(timer=>timer.delay===0).callback();
  geometry=assertAppliedLabelPlacements(h);
  assert.equal(geometry.placements.some(p=>p.kind==='edge'),false);
  assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),1,'obstacles do not hide a policy-visible amount');
  assert.equal(h.run('state.graph.getEdges()[0].shapes[0]._fundsLabelOffset'),undefined,'node collision offsets never attach to a relationship');
  const visible=await h.run('window.__collectSnapshot()'),full=await h.run('window.__collectSnapshot({scope:"all"})');
  for(const snapshot of [visible,full]) {
    assert.equal(snapshot.nodes.length,3);assert.equal(snapshot.edges.length,1);
    assert.equal(snapshot.edges[0].label,'2笔·¥10,000,000,000,000,000.01\nUSD 0.01');
    assert.equal(snapshot.edges[0].data.amount_totals[0].amount,'10000000000000000.01');
    assert.equal(snapshot.edges[0].data.amount_totals[1].amount,'0.01');
    assert.equal(snapshot.edges[0].data.amount_totals[1].currency,'USD');
  }
  assert.equal(visible.edges[0].labelVisible,true);assert.equal(full.edges[0].labelVisible,true);
  h.run('selectItem(state.graph.getEdges()[0])');h.timers.findLast(timer=>timer.delay===0).callback();
  assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),1,'selected edge remains required');
  assertAppliedLabelPlacements(h);
  assert.equal(h.run('state.graph.getEdges()[0].getModel().fundsLabel'),'2笔·¥10,000,000,000,000,000.01\nUSD 0.01');
  h.run('selectItem(null)');h.element('#graphLabelMode').onchange({target:{value:'all'}});h.timers.findLast(timer=>timer.delay===0).callback();
  assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),1,'all retains its exact amount');
  assertAppliedLabelPlacements(h,{retainAll:true});
  h.element('#graphLabelMode').onchange({target:{value:'auto'}});h.timers.findLast(timer=>timer.delay===0).callback();
  assertAppliedLabelPlacements(h);
  h.element('#exportFmt').value='png';const pending=h.element('#btnExportPng').onclick();
  assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),1,'image capture keeps the amount label');
  assertAppliedLabelPlacements(h,{retainAll:true});
  h.timers.findLast(timer=>timer.delay===400).callback();await pending;
  assertAppliedLabelPlacements(h);
  assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),1);
  assert.equal(h.downloads.length,1);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===endpointFacts'),true);
});

test('named workspace dialogs keep actions reachable, close before actions, and restore keyboard focus', async () => {
  const h = harness('app.js'); await h.run('loadCases()');
  h.run(`globalThis.dialogEvents={};globalThis.dialogFocus=0;globalThis.dialogShows=0;
    const dialog=document.querySelector('#dataActions'),trigger=document.querySelector('#btnDataActions');
    const closeButton=document.querySelector('#dialogClose'),contextLabel=document.querySelector('#dialogContext');
    trigger.dataset.actionDialog='dataActions';trigger.focus=()=>dialogFocus++;
    dialog.open=false;dialog.showModal=()=>{dialog.open=true;dialogShows++};
    dialog.close=()=>{dialog.open=false;dialogEvents.close?.()};
    dialog.addEventListener=(name,listener,capture)=>{dialogEvents[name]=listener;if(name==='click')globalThis.actionCapture=capture};
    dialog.querySelectorAll=selector=>selector==='[data-dialog-close]'?[closeButton]:selector==='[data-current-case]'?[contextLabel]:[];
    document.querySelectorAll=selector=>selector==='.action-dialog'?[dialog]:selector==='[data-action-dialog]'?[trigger]:[];
    state.canvasName='Synthetic saved canvas';bindWorkspaceActions();`);
  h.element('#btnDataActions').onclick();
  assert.equal(h.element('#dataActions').open,true);
  assert.equal(h.element('#dialogContext').textContent,'当前案件：Synthetic A · 画布：Synthetic saved canvas');
  h.element('#btnDataActions').onclick();
  assert.equal(h.run('dialogShows'),1,'repeated opening does not throw or duplicate a native dialog');
  assert.equal(h.run('actionCapture'),true,'the existing async action starts after the dialog is dismissed');
  h.run('dialogEvents.click({target:{closest:()=>({})}})');
  assert.equal(h.element('#dataActions').open,false);
  assert.equal(h.run('dialogFocus'),1);
  h.element('#btnDataActions').onclick();h.element('#dialogClose').onclick();
  assert.equal(h.element('#dataActions').open,false);
  assert.equal(h.run('dialogFocus'),2,'explicit close restores the trigger like the native Escape close');
});

test('tracked Excel export downloads a fetched blob without a popup and retains the captured case and canvas', async () => {
  const h = harness('app.js');await h.run('loadCases()');h.bindApp();
  h.run(`state.canvasId=7;state.tracked=new Set([901]);globalThis.exportNotices=[];toast=(...args)=>exportNotices.push(args);
    window.open=()=>{throw new Error('popups are unavailable')};`);
  h.network(() => response({}));
  await h.element('#btnExportXlsx').onclick();
  assert.equal(h.requests.at(-1).path,'/api/export/tracked.xlsx?case_id=1&canvas_id=7');
  assert.equal(h.downloads.length,1);
  assert.equal(h.downloads[0].filename,'资金追踪流水_案件1_画布7.xlsx');
  assert.equal(h.objectUrls[0].blob instanceof Blob,true);
  assert.equal(h.run('exportWorkspace.status().format'),'xlsx');
  assert.match(h.element('#exportFeedback').textContent,/已开始下载/);
  assert.equal(h.run('exportNotices.length'),0,'the export workspace owns download feedback');
});

test('tracked Excel export rejects a delayed response or body after case and canvas revocation', async () => {
  const h = harness('app.js');await h.run('loadCases()');h.bindApp();
  h.run(`state.tracked=new Set([901]);globalThis.exportNotices=[];toast=(...args)=>exportNotices.push(args);`);
  const lateResponse=deferred();let bodyReads=0;
  h.network(() => lateResponse.promise);
  const first=h.element('#btnExportXlsx').onclick();
  assert.equal(h.requests.filter(request=>request.path.startsWith('/api/export/tracked.xlsx?')).length,1);
  h.run('selectCase(2);selectCase(1)');
  lateResponse.resolve({...response({}),blob:async()=>{bodyReads++;return new Blob(['stale'])}});await first;
  assert.equal(bodyReads,0,'a revoked response is discarded before reading its body');
  h.run('state.tracked=new Set([902])');
  const lateBody=deferred();h.network(() => ({...response({}),blob:()=>lateBody.promise}));
  const second=h.element('#btnExportXlsx').onclick();await flush();
  assert.equal(h.requests.filter(request=>request.path.startsWith('/api/export/tracked.xlsx?')).length,2);
  h.run('state.canvasGeneration+=1');lateBody.resolve(new Blob(['stale-canvas']));await second;
  assert.equal(h.downloads.length,0);assert.equal(h.objectUrls.length,0);
  assert.equal(h.run('exportNotices.length'),0,'revoked exports do not report success or an unrelated-case error');
});

test('tracked Excel export reports an HTTP failure and never downloads its body', async () => {
  const h = harness('app.js');await h.run('loadCases()');h.bindApp();
  h.run(`state.tracked=new Set([901]);globalThis.exportNotices=[];toast=(...args)=>exportNotices.push(args);`);
  h.network(() => ({ok:false,status:503,blob(){throw new Error('error body must not download')}}));
  await h.element('#btnExportXlsx').onclick();
  assert.equal(h.downloads.length,0);
  assert.equal(h.element('#exportError').hidden,false);
  assert.match(h.element('#exportError').textContent,/HTTP 503/);
  assert.equal(h.run('exportNotices.length'),0,'HTTP errors stay with the bound export workspace');
});

test('HTML export rejects a late HTTP error after the case leaves and returns', async () => {
  const h=harness('app.js');installSyntheticHub(h,5);h.element('#exportFmt').value='html';
  h.run('globalThis.exportNotices=[];toast=(...args)=>exportNotices.push(args)');
  const pending=deferred();h.network(()=>pending.promise);const exporting=h.element('#btnExportPng').onclick();await flush();
  assert.equal(h.requests.filter(request=>request.path==='/api/export/graph.html').length,1);
  h.run('selectCase(2);selectCase(1)');pending.reject(new Error('合成旧HTML生成失败'));await exporting;
  assert.equal(h.downloads.length,0);assert.equal(h.objectUrls.length,0);assert.equal(h.run('exportNotices.length'),0);
  h.run("exportWorkspace.open(document.querySelector('#btnExportActions'))");
  assert.equal(h.element('#exportError').hidden,true);assert.doesNotMatch(h.element('#exportFeedback').textContent,/合成旧HTML生成失败/);
});

test('HTML and PDF discard delayed file bodies after a canvas leaves and returns', async () => {
  for(const format of ['html','pdf']){
    const h=harness('app.js');samplingGraph(h);h.element('#exportFmt').value=format;
    const body=deferred();let bodyReads=0;
    h.network(()=>({...response({}),blob:()=>{bodyReads++;return body.promise}}));
    const exporting=h.element('#btnExportPng').onclick();
    if(format==='pdf')await finishSample(h);else await flush();
    assert.equal(bodyReads,1,format+' reached the real response body boundary');
    h.run('activateCanvas(8);activateCanvas(0)');body.resolve(new Blob(['合成迟到文件']));await exporting;
    assert.equal(h.downloads.length,0,format);assert.equal(h.objectUrls.length,0,format);
  }
});

test('persistent flow horizontal control synchronizes real scrolling, clamps keyboard moves and disables without overflow', () => {
  const h=harness('app.js');
  h.run(`globalThis.flowRangeListeners={};globalThis.flowViewportListeners={};
    const viewport=document.querySelector('#flowTableScroll'),range=document.querySelector('#flowHorizontalPosition');
    viewport.scrollWidth=520;viewport.clientWidth=300;viewport.scrollLeft=40;
    viewport.addEventListener=(type,listener)=>flowViewportListeners[type]=listener;
    range.addEventListener=(type,listener)=>flowRangeListeners[type]=listener;
    bindFlowTableScroll();`);
  const viewport=h.element('#flowTableScroll'),range=h.element('#flowHorizontalPosition');
  assert.equal(range.max,'220');assert.equal(range.value,'40');assert.equal(range.disabled,false);
  range.value='999';range.oninput();assert.equal(viewport.scrollLeft,220);assert.equal(h.element('#flowScrollRight').disabled,true);
  h.element('#flowScrollLeft').onclick();assert.equal(viewport.scrollLeft,10);
  h.run(`flowRangeListeners.keydown({key:'Home',preventDefault(){}})`);assert.equal(viewport.scrollLeft,0);
  h.run(`flowRangeListeners.keydown({key:'ArrowLeft',preventDefault(){}})`);assert.equal(viewport.scrollLeft,0);
  h.run(`flowRangeListeners.keydown({key:'ArrowRight',preventDefault(){}})`);assert.equal(viewport.scrollLeft,24);
  h.run(`flowRangeListeners.keydown({key:'End',preventDefault(){}})`);assert.equal(viewport.scrollLeft,220);
  viewport.scrollLeft=72;h.run('flowViewportListeners.scroll()');assert.equal(range.value,'72');
  viewport.scrollWidth=280;h.run('syncFlowTableScroll()');
  assert.equal(range.max,'0');assert.equal(range.value,'0');assert.equal(range.disabled,true);
  assert.equal(h.element('#flowScrollLeft').disabled,true);assert.equal(h.element('#flowScrollRight').disabled,true);
  assert.equal(h.requests.length,0,'scrolling never queries data or changes graph geometry');
});

test('flow horizontal control measures row and container changes and disconnects an old observer on rebind', () => {
  const observers=[];
  class FlowResizeObserver {
    constructor(callback){this.callback=callback;this.targets=[];this.disconnected=false;observers.push(this)}
    observe(target){this.targets.push(target)}disconnect(){this.disconnected=true}
  }
  const h=harness('app.js','http://127.0.0.1:8300/','1',{ResizeObserver:FlowResizeObserver});
  const viewport=h.element('#flowTableScroll'),range=h.element('#flowHorizontalPosition');
  viewport.scrollWidth=500;viewport.clientWidth=300;viewport.scrollLeft=0;h.run('bindFlowTableScroll()');
  assert.equal(observers[0].targets.length,2);assert.equal(range.max,'200');
  viewport.clientWidth=350;observers[0].callback();assert.equal(range.max,'150');
  viewport.scrollWidth=600;h.run('renderFlows([])');assert.equal(range.max,'250','new row contents refresh the available range');
  h.run('bindFlowTableScroll()');assert.equal(observers[0].disconnected,true);assert.equal(observers[1].targets.length,2);
});

function horizontalTableFixture(h, kind, metrics) {
  const ids = kind === 'flow'
    ? {viewport:'flowTableScroll',range:'flowHorizontalPosition',left:'flowScrollLeft',right:'flowScrollRight',table:'flowTable'}
    : {viewport:'impPreviewScroll',range:'impHorizontalPosition',left:'impScrollLeft',right:'impScrollRight',table:'impPreviewTable'};
  const nodes = Object.fromEntries(Object.entries(ids).map(([name,id]) => [name,h.element('#'+id)]));
  const listeners = new Map();
  for (const node of Object.values(nodes)) {
    const events = new Map(); listeners.set(node,events);
    node.addEventListener = (type,listener) => {
      if (!events.has(type)) events.set(type,new Set());
      events.get(type).add(listener);
    };
    node.removeEventListener = (type,listener) => events.get(type)?.delete(listener);
  }
  Object.assign(nodes.viewport,metrics);
  return {...nodes, fire(name,type,details={}) {
    const node=nodes[name],event={type,target:node,preventDefault(){this.defaultPrevented=true},...details};
    node['on'+type]?.(event);
    for (const listener of [...(listeners.get(node).get(type)||[])]) listener(event);
    return event;
  }};
}

test('shared horizontal controls keep flow and import table input, buttons, keyboard and native scrolling independent', () => {
  const h=harness('app.js'),bind=h.run('bindTableHorizontalScroll');
  const flow=horizontalTableFixture(h,'flow',{scrollWidth:820,clientWidth:300,scrollLeft:40});
  const preview=horizontalTableFixture(h,'import',{scrollWidth:1400,clientWidth:400,scrollLeft:100});
  const flowControl=bind(flow);bind(preview);
  assert.equal(flow.range.max,'520');assert.equal(preview.range.max,'1000');
  flow.range.value='9999';flow.fire('range','input');
  assert.equal(flow.viewport.scrollLeft,520);assert.equal(preview.viewport.scrollLeft,100);
  preview.range.value='50';preview.fire('range','input');
  assert.equal(preview.viewport.scrollLeft,50);assert.equal(flow.viewport.scrollLeft,520);
  preview.fire('left','click');assert.equal(preview.viewport.scrollLeft,0);assert.equal(flow.viewport.scrollLeft,520);
  flow.fire('left','click');assert.equal(flow.viewport.scrollLeft,310);assert.equal(preview.viewport.scrollLeft,0);
  flow.fire('right','click');assert.equal(flow.viewport.scrollLeft,520);assert.equal(preview.viewport.scrollLeft,0);
  preview.fire('right','click');assert.equal(preview.viewport.scrollLeft,280);
  for(const key of ['Home','ArrowLeft','ArrowRight'])assert.equal(flow.fire('range','keydown',{key}).defaultPrevented,true);
  assert.equal(flow.viewport.scrollLeft,24);assert.equal(preview.viewport.scrollLeft,280);
  for(const key of ['End','PageUp','ArrowLeft'])preview.fire('range','keydown',{key});
  assert.equal(preview.viewport.scrollLeft,696);assert.equal(flow.viewport.scrollLeft,24);
  preview.viewport.scrollLeft=715;preview.fire('viewport','scroll');
  assert.equal(preview.range.value,'715');assert.equal(flow.range.value,'24');
  flow.viewport.scrollLeft=64;flow.fire('viewport','scroll');
  assert.equal(flow.range.value,'64');assert.equal(preview.range.value,'715');
  flow.viewport.scrollWidth=280;flowControl.sync();
  assert.equal(flow.range.max,'0');assert.equal(flow.range.value,'0');assert.equal(flow.range.disabled,true);
  assert.equal(flow.left.disabled,true);assert.equal(flow.right.disabled,true);
  assert.equal(preview.range.disabled,false);assert.equal(preview.range.value,'715');
  assert.equal(h.requests.length,0,'table controls do not query transaction data');
});

test('shared horizontal controls remeasure their own table and release events, queued observation and timers on destroy', () => {
  const observers=[];
  class TableResizeObserver {
    constructor(callback){this.callback=callback;this.targets=[];this.disconnected=false;observers.push(this)}
    observe(target){this.targets.push(target)}disconnect(){this.disconnected=true}
  }
  const h=harness('app.js','http://127.0.0.1:8300/','1',{ResizeObserver:TableResizeObserver});
  const bind=h.run('bindTableHorizontalScroll');
  const flow=horizontalTableFixture(h,'flow',{scrollWidth:1000,clientWidth:300,scrollLeft:0});
  const preview=horizontalTableFixture(h,'import',{scrollWidth:1400,clientWidth:400,scrollLeft:0});
  bind(flow);const old=bind(preview);
  assert.deepEqual(observers[0].targets,[flow.viewport,flow.table]);
  assert.deepEqual(observers[1].targets,[preview.viewport,preview.table]);
  flow.viewport.clientWidth=500;observers[0].callback();
  assert.equal(flow.range.max,'500');assert.equal(preview.range.max,'1000');
  preview.viewport.scrollWidth=600;observers[1].callback();
  assert.equal(preview.range.max,'200');assert.equal(flow.range.max,'500');
  preview.viewport.scrollLeft=180;preview.fire('viewport','scroll');
  const idle=h.timers.findLast(timer=>timer.delay===800&&!timer.cancelled);
  assert.ok(idle);old.destroy();assert.equal(observers[1].disconnected,true);assert.equal(idle.cancelled,true);
  preview.viewport.scrollLeft=73;preview.range.value='untouched';preview.range.max='untouched';
  preview.fire('range','input');preview.fire('left','click');preview.fire('right','click');
  preview.fire('range','keydown',{key:'End'});preview.fire('viewport','scroll');observers[1].callback();
  assert.equal(preview.viewport.scrollLeft,73,'destroyed property handlers cannot move the old viewport');
  assert.equal(preview.range.value,'untouched','removed listeners and a queued observer cannot resynchronize a destroyed control');
  assert.equal(preview.range.max,'untouched');assert.equal(flow.range.max,'500');
  bind(preview);old.destroy();
  preview.range.value='150';preview.fire('range','input');
  assert.equal(preview.viewport.scrollLeft,150,'destroying an old binding again cannot remove its replacement handlers');
  assert.equal(flow.viewport.scrollLeft,0);assert.equal(observers[2].disconnected,false);
});

test('import preview retains all thirteen normalized columns and complete text and decimal money', () => {
  const h=harness('app.js');
  const summary='合成预览摘要：完整保留交易来源、核查说明和编号 SYNTHETIC-PREVIEW-000000000000000001。';
  h.run('renderImport')({filename:'synthetic-preview.xlsx',mode:'A',
    columns:['time','from','to','amount','summary'],mapping:{trade_time:'time',payer_account:'from',payee_account:'to',amount:'amount',summary:'summary'},
    stats:{total:1,ok:1,skipped:0},preview:[{trade_time:'2026-10-15 08:09:07',payer_name:'Synthetic payer',payer_account:'000012345678901234567890',
      payee_name:'Synthetic payee',payee_account:'SYNTHETIC-PREVIEW-ACCOUNT-B',amount:'9999999999999999.99',currency:'USD',balance:'10000000000000000.01',
      summary,trade_no:'SYNTHETIC-TRADE-000000000000000001',payer_bank:'Synthetic payer bank',payee_bank:'Synthetic payee bank',
      channel:'Synthetic wire channel',self_account:'000098765432109876543210',self_name:'Synthetic main account'}]});
  const head=h.element('#impPreviewHead').innerHTML,body=h.element('#impTbody').innerHTML;
  assert.equal((head.match(/<th(?:\s|>)/g)||[]).length,13);
  assert.equal((body.match(/<td(?:\s|>)/g)||[]).length,13);
  for(const label of ['交易时间','付款方','收款方','金额','摘要','币种','余额','交易流水号','付款开户行','收款开户行','交易类型/渠道','本方账号','本方户名'])assert.ok(head.includes('>'+label+'</th>'),label);
  for(const value of ['2026-10-15 08:09:07','000012345678901234567890','SYNTHETIC-PREVIEW-ACCOUNT-B',summary,
    'SYNTHETIC-TRADE-000000000000000001','Synthetic payer bank','Synthetic payee bank','Synthetic wire channel','000098765432109876543210','Synthetic main account',
    'USD 9,999,999,999,999,999.99','USD 10,000,000,000,000,000.01'])assert.ok(body.includes(value),value);
  assert.equal(h.element('#impPreviewNote').textContent,'前 1 行 · 规范化后 · 13 列');
  assert.equal(h.requests.length,0);
});

test('dense flow rows retain native columns, complete date and seconds, exact money, and full account titles', () => {
  const h=harness('app.js');h.run(`renderFlows([{id:9,trade_time:'2026-10-15 08:09:07',payer_name:'Synthetic payer',payer_account:'000012345678901234567890',payee_name:'Synthetic payee',payee_account:'SYN-B',amount:'9999999999999999.99',currency:'USD',summary:'Synthetic summary'}])`);
  const html=h.element('#flowTbody').innerHTML;
  assert.equal((html.match(/<td(?:\s|>)/g)||[]).length,7);
  assert.match(html,/<span class="flow-date">2026-10-15<\/span><span class="flow-clock">08:09:07<\/span>/);
  assert.match(html,/USD 9,999,999,999,999,999\.99/);
  assert.match(html,/title="000012345678901234567890"/);
  assert.match(html,/class="btn tiny primary act-track">上图/);
  assert.match(html,/aria-label="查看前后 X 笔流水">前后<\/button>/);
});

test('flow display choices apply without replacing selections and retain all source text and exact money after rendering', () => {
  const h=harness('app.js');h.bindApp();
  const viewport=h.element('#flowTableScroll'),density=h.element('#flowTableDensity'),wrap=h.element('#flowTableWrap');
  assert.equal(viewport.dataset.flowDensity,'compact');assert.equal(viewport.dataset.flowWrap,'wrap');
  h.run(`globalThis.longFlow={id:17,trade_time:'2026-10-15 08:09:07',payer_name:'合成付款单位完整名称甲乙丙丁',payer_account:'0000123456789012345678901234567890',payee_name:'合成收款单位完整名称戊己庚辛',payee_account:'SYNTHETIC-ACCOUNT-00000000000000000001',amount:'9999999999999999.99',currency:'USD',summary:'合成摘要第一段完整文字，包含核查依据与完整流水编号 SYNTHETIC-20261015-000000001。第二段也必须完整显示。'};renderFlows([longFlow]);`);
  const original=h.element('#flowTbody').innerHTML;
  density.value='comfortable';density.onchange();wrap.value='single';wrap.onchange();
  assert.equal(viewport.dataset.flowDensity,'comfortable');assert.equal(viewport.dataset.flowWrap,'single');
  assert.equal(h.element('#flowTbody').innerHTML,original,'display changes preserve existing checkboxes and row focus');
  h.run('renderFlows([longFlow])');
  assert.equal(viewport.dataset.flowDensity,'comfortable');assert.equal(viewport.dataset.flowWrap,'single');
  const html=h.element('#flowTbody').innerHTML;
  assert.equal((html.match(/<td(?:\s|>)/g)||[]).length,7);
  assert.match(html,/>合成付款单位完整名称甲乙丙丁<\/span>/);
  assert.match(html,/>0000123456789012345678901234567890<\/span>/);
  assert.match(html,/>SYNTHETIC-ACCOUNT-00000000000000000001<\/span>/);
  assert.match(html,/<span class="flow-summary">合成摘要第一段完整文字，包含核查依据与完整流水编号 SYNTHETIC-20261015-000000001。第二段也必须完整显示。<\/span>/);
  assert.match(html,/USD 9,999,999,999,999,999\.99/);
  wrap.value='wrap';wrap.onchange();density.value='compact';density.onchange();
  assert.equal(viewport.dataset.flowDensity,'compact');assert.equal(viewport.dataset.flowWrap,'wrap');
  assert.equal(h.element('#flowTbody').innerHTML,html);
  assert.equal(h.requests.length,0,'display preferences do not query or mutate business data');
});

test('query and reset clear page selection and its master state while display choices preserve the selected row', async () => {
  const h=harness('app.js');h.run('globalThis.originalSearch=doSearch');h.bindApp();h.run(`doSearch=originalSearch;
    globalThis.pageCheckboxes=[];
    const body=document.querySelector('#flowTbody'),queryAll=document.querySelectorAll;
    Object.defineProperty(body,'innerHTML',{get(){return this.renderedHtml||''},set(html){
      this.renderedHtml=html;
      pageCheckboxes=[...html.matchAll(/class="flow-ck" data-id="([^"]+)"/g)].map(match=>({checked:false,dataset:{id:match[1]}}));
    }});
    document.querySelectorAll=selector=>selector==='#flowTbody .flow-ck'?pageCheckboxes:queryAll(selector);`);
  h.network(()=>response({page:1,total:1,size:100,rows:[{id:23,amount:'123.45',currency:'USD'}]}));
  await h.element('#btnSearch').onclick();
  const master=h.element('#flowCkAll');
  const selectPage=()=>{master.checked=true;master.onchange({target:master});master.indeterminate=true;};
  selectPage();assert.equal(h.element('#flowCkCount').textContent,'已勾选 1 笔');
  const density=h.element('#flowTableDensity'),wrap=h.element('#flowTableWrap');
  density.value='comfortable';density.onchange();wrap.value='single';wrap.onchange();
  assert.equal(h.run('pageCheckboxes[0].checked'),true);assert.equal(master.checked,true);
  assert.equal(h.element('#flowCkCount').textContent,'已勾选 1 笔');
  await h.element('#btnSearch').onclick();
  assert.equal(h.run('pageCheckboxes[0].checked'),false);assert.equal(master.checked,false);assert.equal(master.indeterminate,false);
  assert.equal(h.element('#flowCkCount').textContent,'已勾选 0 笔');
  selectPage();h.element('#btnReset').onclick();await flush();
  assert.equal(h.run('pageCheckboxes[0].checked'),false);assert.equal(master.checked,false);assert.equal(master.indeterminate,false);
  assert.equal(h.element('#flowCkCount').textContent,'已勾选 0 笔');
  selectPage();h.run('selectCase(2)');
  assert.equal(h.run('pageCheckboxes.length'),0);assert.equal(master.checked,false);assert.equal(master.indeterminate,false);
  assert.equal(h.element('#flowCkCount').textContent,'已勾选 0 笔');
});

// Audit regression: a valid empty canvas is different from an invalidated capture.
test('empty initialized canvases can be saved and cloned without inventing a revoked snapshot', async () => {
  for (const action of ['#btnCanvasSave','#btnCanvasClone']) {
    const h=harness('app.js');installCanvasControls(h);
    h.run('state.lastGraph={nodes:[],edges:[],tracked_ids:[]};state.graph.clear();state.graphInited=true');
    h.network(path=>response(path==='/api/canvases/clone'?{canvas_id:20,name:'Empty',copied:0}:path==='/api/canvases/save'?{canvas_id:10,name:'Empty'}:{nodes:[],edges:[],tracked_ids:[]}));
    await h.element(action).onclick();
    const request=h.requests.find(item=>item.path=== (action.includes('Clone')?'/api/canvases/clone':'/api/canvases/save'));
    assert.ok(request);const snapshot=JSON.parse(request.options.body).snapshot;
    assert.deepEqual(snapshot.nodes,[]);assert.deepEqual(snapshot.edges,[]);
  }
});

test('save and clone reject delayed snapshots after case or canvas A-B-A binding changes', async () => {
  for (const action of ['#btnCanvasSave','#btnCanvasClone']) for (const transition of [
    'selectCase(2);selectCase(1)','activateCanvas(20);activateCanvas(10)',
  ]) {
    const h=harness('app.js');installCanvasControls(h);
    h.run('state.lastGraph.nodes[0].photo_at=123;renderGraph()');
    const delayed=deferred(),captured=deferred(),env=h.run('globalThis'),collect=env.window.__collectSnapshot;
    env.window.__collectSnapshot=async options=>{const snapshot=await collect(options);captured.resolve();await delayed.promise;return snapshot};
    const pending=h.element(action).onclick();await captured.promise;
    assert.equal(h.requests.some(item=>String(item.path).includes('account-photos')),false,'save/clone do not use photo loading as a delay');
    h.run(transition);delayed.resolve();await pending;
    assert.equal(h.requests.filter(item=>item.options.method==='POST').length,0,`${action} must not persist an invalidated snapshot (${transition})`);
  }
});

test('saved mixed edge types, bends and label geometry survive redraw and full snapshot collection', async () => {
  const h=harness('app.js');installSyntheticHub(h,5);
  h.run(`globalThis.savedInitGraph=initGraph;initGraph=()=>{};
    // Only this saved-geometry fixture supplies paths; other harness cases keep
    // their existing measured/absent-shape behavior unchanged.
    const attachSavedEdgePaths=()=>{
      for(const edge of state.graph.getEdges())edge.getKeyShape=()=>({attr(name){
        if(name!=='path')return undefined;
        const model=edge.getModel(),a=state.graph.getNodes().find(node=>node.getModel().id===model.source)?.getModel();
        const b=state.graph.getNodes().find(node=>node.getModel().id===model.target)?.getModel();
        return a&&b?[['M',a.x,a.y],...(model.controlPoints||[]).map(point=>['L',point.x,point.y]),['L',b.x,b.y]]:null;
      }});
    };
    const changeSavedData=state.graph.changeData;
    state.graph.changeData=data=>{changeSavedData(data);attachSavedEdgePaths()};attachSavedEdgePaths();
    state.lastGraph.edges.push({...state.lastGraph.edges[0],source:'SYN-HUB',target:'SYN-HUB'});
    applyCanvasSnapshot({nodes:state.graph.getNodes().map(item=>({...item.getModel(),
      ...(item.getModel().id==='SYN-HUB'?{x:80,y:98.5}:item.getModel().id==='SYN-00000'?{x:180,y:98.5}:{})})),edges:[
      {source:'SYN-HUB',target:'SYN-00000',type:'line',lx:123.25,ly:98.5,bw:40,bh:10},
      {source:'SYN-HUB',target:'SYN-00001',type:'polyline',controlPoints:[{x:7,y:8}]},
      {source:'SYN-HUB',target:'SYN-00002',type:'quadratic'},
      {source:'SYN-HUB',target:'SYN-HUB',type:'loop'}]});renderGraph();`);
  assert.equal(h.element('#edgeTypeSel').disabled,true,'object formatting requires a selected relationship');
  const globalType=h.run('state.edgeType');
  let snapshot=await h.run('window.__collectSnapshot({scope:"all"})');
  assert.deepEqual(Array.from(snapshot.edges,edge=>edge.type),['line','polyline','quadratic','quadratic','loop']);
  assert.deepEqual(JSON.parse(JSON.stringify(snapshot.edges[1].controlPoints)),[{x:7,y:8}]);
  assert.equal(snapshot.edges[0].lx,123.25);assert.equal(snapshot.edges[0].label,'2笔·¥10,000,000,000,000,000.01\nUSD 0.01');
  h.element('#edgeTypeSel').onchange({target:{value:'line'}});
  snapshot=await h.run('window.__collectSnapshot({scope:"all"})');
  assert.deepEqual(Array.from(snapshot.edges,edge=>edge.type),['line','polyline','quadratic','quadratic','loop'],
    'an unselected control cannot replace saved per-edge types');
  h.run('selectItem(state.graph.getEdges()[2])');
  assert.equal(h.element('#edgeTypeSel').disabled,false);
  assert.equal(h.element('#edgeTypeSel').value,'quadratic');
  h.element('#edgeTypeSel').onchange({target:{value:'line'}});
  h.run('selectItem(state.graph.getEdges()[2])');
  assert.equal(h.element('#edgeTypeSel').value, 'line');
  snapshot=await h.run('window.__collectSnapshot({scope:"all"})');
  assert.deepEqual(Array.from(snapshot.edges,edge=>edge.type),['line','polyline','line','quadratic','loop'],
    'only the selected relationship changes, retaining every other saved or default type');
  assert.deepEqual(JSON.parse(JSON.stringify(snapshot.edges[1].controlPoints)),[{x:7,y:8}]);
  assert.equal(snapshot.edges[0].lx,123.25);
  assert.equal(h.run('state.edgeType'),globalType);
  h.run('activateCanvas(20)');assert.deepEqual(Object.keys(h.run('state.edgeTypes')),[]);
});

function samplingGraph(h) {
  installSyntheticHub(h,5);
  h.run(`globalThis.sampleSizes=[];globalThis.sampled=[];globalThis.sampleSize=[1000,700];
    state.graph.changeSize=(w,h)=>{sampleSize=[w,h];sampleSizes.push([w,h])};
    state.graph.toDataURL=()=>{sampled.push([...sampleSize]);return 'data:image/png;base64,c3ludGhldGlj'};`);
}
async function finishSample(h) {
  const timer=h.timers.findLast(timer=>timer.delay===400&&!timer.ran);
  assert.ok(timer,'one capture is pending');timer.ran=true;timer.callback();await flush();
}

test('overlapping PNG and PDF exports serialize their 2x and 3x sampling', async () => {
  for (const formats of [['png','png'],['png','pdf'],['pdf','png']]) {
    const h=harness('app.js');samplingGraph(h);h.network(()=>response({}));
    const a=h.sampleExport(formats[0]);
    const b=h.sampleExport(formats[1]);
    assert.equal(h.run('state.graphView.exportDepth'),1);
    assert.equal(h.timers.filter(timer=>timer.delay===400).length,1);
    await finishSample(h);await finishSample(h);await Promise.all([a,b]);
    assert.deepEqual(JSON.parse(h.run('JSON.stringify(sampled)')),formats.map(fmt=>fmt==='pdf'?[3000,2100]:[2000,1400]));
    assert.equal(h.run('state.graphView.exportDepth'),0);assert.equal(h.downloads.length,2);
    assert.deepEqual(JSON.parse(h.run('JSON.stringify(sampleSize)')),[1000,700]);
  }
});

test('PNG and PDF sampling keep bottom anchors at the sampling pixel ratio and restore the screen camera',async()=>{
  const h=harness('app.js');samplingGraph(h);h.network(()=>response({}));
  h.run(`globalThis.labelCamera=[1,0,0,0,1,0,19,23,1];globalThis.labelSampleAudit=[];
    const graph=state.graph,get=graph.get;
    const camera={getMatrix:()=>[...labelCamera],setMatrix(matrix){labelCamera=[...matrix]}};
    graph.get=name=>name==='group'?camera:name==='width'?sampleSize[0]:name==='height'?sampleSize[1]:get(name);
    graph.getZoom=()=>labelCamera[0];
    graph.getCanvasByPoint=(x,y)=>({x:x*labelCamera[0]+labelCamera[6],y:y*labelCamera[4]+labelCamera[7]});
    graph.getPointByCanvas=(x,y)=>({x:(x-labelCamera[6])/labelCamera[0],y:(y-labelCamera[7])/labelCamera[4]});
    graph.fitView=()=>{const scale=sampleSize[0]/1000;labelCamera=[scale,0,0,0,scale,0,0,0,1]};
    state.positions={'SYN-HUB':{x:200,y:300},'SYN-00000':{x:450,y:300},
      'SYN-00001':{x:720,y:500},'SYN-00002':{x:720,y:100},'SYN-00003':{x:100,y:500}};
    state.graphView.labelMode='focus';state.graphView.selectedAccount='SYN-00000';renderGraph();
    // A stale earlier side/top displacement must be repaired before sampling.
    const upper=state.graphView.nodeItems.get('SYN-00000');resetAutoLabelOffset(upper);
    const upperText=labelShapes(upper)[0],baseX=upperText.attr('x'),baseY=upperText.attr('y');
    upperText.attr({x:baseX,y:baseY-72});
    upperText._fundsLabelOffset={baseX,baseY,x:baseX,y:baseY-72};
    applyGraphLabels();
    globalThis.nodeLabelAnchors=()=>Object.fromEntries(graph.getNodes().flatMap(item=>{
      const text=labelShapes(item).find(shape=>shape.get('name')==='text-shape');if(!text)return [];
      const box=text.getCanvasBBox(),body=item.getKeyShape().getCanvasBBox();
      return [[item.getModel().id,{gap:box.minY-body.maxY,
        center:(box.minX+box.maxX-body.minX-body.maxX)/2,staleOffset:!!text._fundsLabelOffset}]];
    }));
    const sample=graph.toDataURL;
    graph.toDataURL=()=>{labelSampleAudit.push({size:[...sampleSize],camera:[...labelCamera],anchors:nodeLabelAnchors(),
      shown:[...state.graphView.policy.nodeIds]});return sample()};
  `);
  const before=JSON.parse(h.run('JSON.stringify(nodeLabelAnchors())'));
  const camera=JSON.parse(h.run('JSON.stringify(labelCamera)'));
  const facts=h.run('JSON.stringify(state.lastGraph)');
  const originalVisible=Object.keys(before),hidden=Array.from(h.run('state.lastGraph.nodes.map(node=>node.account)')).filter(id=>!originalVisible.includes(id));
  assert.ok(originalVisible.includes('SYN-00000'));
  assert.deepEqual(before['SYN-00000'],{gap:6,center:0,staleOffset:false},'the stale upward placement is repaired');
  assert.ok(hidden.length,'focus mode leaves labels that export may reveal and settle');
  for(const format of ['png','pdf']){
    h.element('#exportFmt').value=format;const pending=h.element('#btnExportPng').onclick();
    await finishSample(h);await pending;
    const audit=JSON.parse(h.run('JSON.stringify(labelSampleAudit.at(-1))'));
    assert.deepEqual(audit.size,format==='png'?[2000,1400]:[3000,2100]);
    assert.notDeepEqual(audit.camera,camera,'sampling uses its own fitted high-resolution camera');
    const expectedGap=format==='png'?12:18;
    for(const id of originalVisible){assert.ok(Math.abs(audit.anchors[id].gap-expectedGap)<1e-8,`${format} preserves the six-pixel gap at its pixel ratio`);
      assert.ok(Math.abs(audit.anchors[id].center)<1e-8);assert.equal(audit.anchors[id].staleOffset,false);}
    for(const id of hidden){assert.ok(audit.shown.includes(id),'export reveals all label-policy nodes');
      assert.ok(Object.hasOwn(audit.anchors,id),'newly revealed labels receive actual G6 text');
      assert.ok(Math.abs(audit.anchors[id].gap-expectedGap)<1e-8);assert.ok(Math.abs(audit.anchors[id].center)<1e-8);}
    assert.deepEqual(JSON.parse(h.run('JSON.stringify(nodeLabelAnchors())')),before,'normal screen anchors are restored');
    assert.deepEqual(JSON.parse(h.run('JSON.stringify(labelCamera)')),camera,'the exact previous camera is restored');
    assert.deepEqual(JSON.parse(h.run('JSON.stringify(sampleSize)')),[1000,700]);
    assert.equal(h.run('state.graphView.exportNodeOffsets'),undefined,'export-only placement state is released');
    assert.equal(h.run('state.graphView.exportDepth'),0);
  }
  assert.equal(h.downloads.length,2);assert.equal(h.run('JSON.stringify(state.lastGraph)'),facts);
});

test('sampling failures release export state and permit the next export', async () => {
  for (const phase of ['changeSize','fitView','toDataURL']) {
    const h=harness('app.js');samplingGraph(h);
    h.run(`globalThis.originalSampleMethod=state.graph.${phase};globalThis.failSample=true;
      state.graph.${phase}=(...args)=>{if(failSample){failSample=false;throw new Error('synthetic ${phase} failure')};return originalSampleMethod(...args)}`);
    h.element('#exportFmt').value='png';const a=h.element('#btnExportPng').onclick();
    await flush();if(h.timers.some(timer=>timer.delay===400&&!timer.ran))await finishSample(h);await a;
    assert.equal(h.run('state.graphView.exportDepth'),0,phase);
    const b=h.element('#btnExportPng').onclick();await finishSample(h);await b;
    assert.equal(h.run('state.graphView.exportDepth'),0);assert.equal(h.downloads.length,1);
  }
});

test('stale image capture never restores over a redraw or a new case/canvas binding', async () => {
  for (const transition of ['renderGraph()','selectCase(2);selectCase(1)','activateCanvas(7);activateCanvas(0)']) {
    const h=harness('app.js');samplingGraph(h);h.element('#exportFmt').value='png';
    const pending=h.element('#btnExportPng').onclick();const stale=h.timers.findLast(timer=>timer.delay===400);
    h.run(transition);const writes=h.run('sampleSizes.length');stale.callback();await pending;
    assert.equal(h.run('sampleSizes.length'),writes);assert.equal(h.downloads.length,0);
    assert.equal(h.run('state.graphView.exportDepth'),0);
  }
});

test('node browse clears tracked-only and all residual search filters', async () => {
  const h=harness('app.js');h.run('globalThis.realSearch=doSearch');installSyntheticHub(h,5);
  const drawerClasses = new Set();
  h.element('#drawer').classList = { add: (...names) => names.forEach(name => drawerClasses.add(name)),
    remove: (...names) => names.forEach(name => drawerClasses.delete(name)), contains: name => drawerClasses.has(name) };
  h.network(path=>response(path.startsWith('/api/main-accounts')?[]:{}));
  await h.run('showNodeDrawer("SYN-HUB")');
  assert.equal(typeof h.element('#ndBrowse').onclick, 'function', h.element('#drawerBody').innerHTML);
  for (const selector of ['#fAccount','#fName','#fCounter','#fKw','#fMin','#fMax','#fFrom','#fTo'])h.element(selector).value='residual';
  h.element('#fTracked').checked=true;
  h.run('doSearch=realSearch;renderFlows=()=>{}');h.network(()=>response({page:1,size:50,total:0,rows:[]}));
  h.element('#ndBrowse').onclick();await flush();
  assert.equal(h.element('#fTracked').checked,false);
  const query=new URL(h.requests.at(-1).path,'http://synthetic.local').searchParams;
  assert.equal(query.get('account'),'SYN-HUB');assert.equal(query.get('tracked'),null);
  for(const key of ['name','counter','keyword','min_amount','max_amount','date_from','date_to'])assert.equal(query.get(key),null);
});

test('queued capture cancellation cannot resize or restore the camera of a newer export', async () => {
  const h=harness('app.js');samplingGraph(h);
  h.run(`globalThis.sampleCamera=[1,0,0,0,1,0,12,15,1];
    globalThis.cameraGroup={getMatrix:()=>sampleCamera,setMatrix:matrix=>{sampleCamera=[...matrix]}};
    state.graph.get=key=>key==='group'?cameraGroup:null;
    globalThis.originalFit=state.graph.fitView;
    state.graph.fitView=(...args)=>{sampleCamera=[2,0,0,0,2,0,100,100,1];return originalFit(...args)};`);
  const a=h.sampleExport('png');
  const b=h.sampleExport('pdf');
  const old=h.timers.findLast(timer=>timer.delay===400);
  h.run('renderGraph()');await flush();
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(sampleCamera)')),[1,0,0,0,1,0,12,15,1]);
  const c=h.sampleExport('png');
  const before=h.run('sampleSizes.length');old.callback();assert.equal(h.run('sampleSizes.length'),before);
  await finishSample(h);await Promise.all([a,b,c]);
  assert.equal(h.downloads.length,1);assert.equal(h.run('state.graphView.exportDepth'),0);
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(sampleCamera)')),[1,0,0,0,1,0,12,15,1]);
});

test('full neighborhood snapshots retain saved line types for edges outside the current view', async () => {
  const h=harness('app.js');installSyntheticHub(h,120);
  await h.run(`initGraph=()=>{};applyCanvasSnapshot({nodes:state.graph.getNodes().map(item=>{
    const model=item.getModel();return {id:model.id,x:model.x,y:model.y};}),edges:[
    {source:'SYN-HUB',target:'SYN-00118',type:'polyline',controlPoints:[{x:23,y:45}],lx:44,ly:55,bw:20,bh:10}]});
    exploreNeighborhood('SYN-HUB',true)`);
  const snapshot=await h.run('window.__collectSnapshot({scope:"all"})');
  const edge=snapshot.edges.find(edge=>edge.target==='SYN-00118');
  assert.equal(edge.type,'polyline');assert.equal(edge.lx,44);
  assert.deepEqual(JSON.parse(JSON.stringify(edge.controlPoints)),[{x:23,y:45}]);
});

test('relation filters remove edges independently of retained endpoints and full saves retain every fact', async () => {
  const h=harness('app.js');installSyntheticHub(h,5);
  h.run(`state.lastGraph.edges=[
    {source:'SYN-00000',target:'SYN-00001',cnt:1,ids:[1],amount_totals:[{currency:'CNY',amount:'0.01'}]},
    {source:'SYN-00000',target:'SYN-00002',cnt:1,ids:[2],amount_totals:[{currency:'CNY',amount:'9999999999999999.99'}]},
    {source:'SYN-00002',target:'SYN-00001',cnt:1,ids:[3],amount_totals:[{currency:'CNY',amount:'9999999999999999.99'}]}];
    globalThis.originalRibbonFacts=JSON.stringify(state.lastGraph);
    state.presentation=newGraphPresentation({relationFilter:{currency:'CNY',min:'9999999999999999.99'},amountWidth:{enabled:true,currency:'CNY'}});
    state.forceFit=false;renderGraph();`);
  assert.equal(h.run('state.graph.getNodes().length'),3);
  assert.deepEqual(Array.from(h.run('state.graph.getEdges().map(item=>edgeKey(item.getModel()))')).sort(),
    ['SYN-00000=>SYN-00002','SYN-00002=>SYN-00001']);
  h.run('state.graphView.hoveredAccount="SYN-00000";applyGraphLabels();renderGraph();');
  assert.equal(h.run('state.graph.getEdges().length'),2);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===originalRibbonFacts'),true);
  const visible=await h.run('window.__collectSnapshot()'),full=await h.run('window.__collectSnapshot({scope:"all"})');
  assert.equal(visible.edges.length,2);assert.equal(full.edges.length,3);assert.equal(full.nodes.length,5);
  assert.equal(visible.view.mode,'filtered');assert.equal(visible.view.relation_filter.min,'9999999999999999.99');
  assert.equal(visible.edges[0].style.lineWidth,7);
  assert.equal(full.presentation.relationFilter.currency,'CNY');
  assert.equal(full.edges.find(e=>e.target==='SYN-00002').data.amount_totals[0].amount,'9999999999999999.99');
});

test('graph redraw revokes item and viewport caches before G6 changeData callbacks', () => {
  const h=harness('app.js');installSyntheticHub(h,5);
  h.run(`const change=state.graph.changeData;globalThis.ribbonCachesRevoked=false;
    state.graph.changeData=function(data){
      ribbonCachesRevoked=state.graphView.nodeItems.size===0&&state.graphView.edgeItems.size===0&&state.graphView.clipIndex===null;
      applyGraphViewport();return change.call(this,data);
    };renderGraph();`);
  assert.equal(h.run('ribbonCachesRevoked'),true);
  assert.equal(h.run('state.graphView.nodeItems.size'),5);
});

test('empty saved canvases restore presentation and synchronize existing display controls', async () => {
  const h=harness('app.js');h.run('globalThis.bindSavedRibbonCanvases=bindCanvases;');installSyntheticHub(h,2);
  h.run(`globalThis.restoredControls=0;window.FundsCanvasEditor={syncControls(){
    restoredControls++;document.querySelector('#showPhotoCk').checked=state.showPhotos;
    document.querySelector('#layoutSel').value=state.layout;
  }};bindSavedRibbonCanvases();`);
  h.network(path=>response(path.startsWith('/api/canvases/7') ? {id:7,snapshot:{nodes:[],edges:[],
    presentation:{relationFilter:{currency:'USD',min:'0.01'}},display:{showPhotos:false,layout:'dagre'}}}
    : {nodes:[],edges:[],tracked_ids:[]}));
  await h.element('#canvasSel').onchange({target:{value:'7',selectedOptions:[{textContent:'Synthetic empty'}]}});
  assert.equal(h.run('state.presentation.relationFilter.currency'),'USD');
  assert.equal(h.run('state.showPhotos'),false);assert.equal(h.element('#showPhotoCk').checked,false);
  assert.equal(h.element('#layoutSel').value,'dagre');assert.equal(h.run('restoredControls'),1);
});

test('a rejected PDF request cannot publish an error after its binding is revoked', async () => {
  const h=harness('app.js');samplingGraph(h);
  h.run('globalThis.exportNotices=[];toast=(...args)=>exportNotices.push(args)');
  let rejectPdf;h.network(()=>new Promise((_,reject)=>{rejectPdf=reject}));
  h.element('#exportFmt').value='pdf';const pending=h.element('#btnExportPng').onclick();await finishSample(h);
  h.run('selectCase(2);selectCase(1)');rejectPdf(new Error('synthetic expired request'));await pending;
  assert.equal(h.downloads.length,0);assert.equal(h.run('exportNotices.length'),0);
});

function installSnapshotPaths(h,{stubInit=true}={}) {
  h.run(`const attachSnapshotPaths=()=>{
    for(const edge of state.graph.getEdges())edge.getKeyShape=()=>({
      attr(name){if(name!=='path')return undefined;
        const m=edge.getModel(),a=state.graph.getNodes().find(node=>node.getModel().id===m.source)?.getModel();
        const b=state.graph.getNodes().find(node=>node.getModel().id===m.target)?.getModel();
        return a&&b?[['M',a.x,a.y],...(m.controlPoints||[]).map(p=>['L',p.x,p.y]),['L',b.x,b.y]]:null},
      getPoint(r){const path=this.attr('path');if(!path)return null;
        const a=path[0],b=path[path.length-1];return{x:a[1]+(b[1]-a[1])*r,y:a[2]+(b[2]-a[2])*r}}
    });
  };
  const snapshotData=state.graph.data,snapshotChangeData=state.graph.changeData;
  state.graph.data=data=>{snapshotData(data);attachSnapshotPaths()};
  state.graph.changeData=data=>{snapshotChangeData(data);attachSnapshotPaths()};
  attachSnapshotPaths();${stubInit?'initGraph=()=>{};':''}`);
}

test('graph persistence: old absolute labels, explicit path ratios and automatic labels have distinct round-trip semantics', async () => {
  for(const variant of ['legacy','ratio','automatic']) {
    const h=harness('app.js');installSyntheticHub(h,2);installSnapshotPaths(h);
    const env=h.run('globalThis');
    env.labelSnapshot={nodes:[{id:'SYN-HUB',x:100,y:200},{id:'SYN-00000',x:500,y:200}],
      edges:[{source:'SYN-HUB',target:'SYN-00000',type:'line',lx:170,ly:230,bw:40,bh:10,
        ...(variant==='ratio'?{labelRatio:0.25,labelManual:true}:variant==='automatic'?{labelRatio:null,labelManual:false}:{})}],
      display:{edgeType:'line',labelMode:'all'}};
    h.run('applyCanvasSnapshot(labelSnapshot)');
    const delayed=h.timers.filter(timer=>timer.delay===250&&!timer.cancelled&&!timer.ran);
    for(const timer of delayed){timer.ran=true;timer.callback()}
    const key='SYN-HUB=>SYN-00000';
    if(variant==='ratio') {
      assert.equal(h.run(`state.edgeLabelPos['${key}']`),0.25);
      assert.equal(h.run('state.graph.getEdges()[0].shapes[0].attr("x")'),200,'new path ratio outranks stale absolute x');
      assert.equal(h.run('state.graph.getEdges()[0].shapes[0].attr("y")'),200,'delayed restoration cannot reinstate stale y');
    } else if(variant==='legacy') {
      assert.equal(h.run(`state.edgeSnapshotLabels['${key}'].manual`),true,'unflagged legacy coordinates remain manual');
      assert.equal(h.run('state.graph.getEdges()[0].shapes[0].attr("x")'),170);
      assert.equal(h.run('state.graph.getEdges()[0].shapes[0].attr("y")'),200,'legacy text projects onto its relationship instead of floating off-path');
      assert.equal(h.run(`state.edgeLabelPos['${key}']`),.175);
    } else {
      h.run('savePositions();captureEdgeLabelRatios()');
      assert.equal(h.run(`state.edgeSnapshotLabels['${key}'].manual`),false);
      assert.equal(h.run(`state.edgeLabelPos['${key}']`),undefined,'automatic cache does not become a manual anchor');
    }
    const saved=await h.run('window.__collectSnapshot({scope:"all",embedPhotos:false})');
    assert.equal(saved.edges[0].labelManual,variant!=='automatic');
    assert.equal(saved.edges[0].labelRatio,variant==='ratio'?0.25:variant==='legacy'?.175:null);
    assert.equal(saved.edges[0].data.amount_totals[0].amount,'10000000000000000.01');
  }
});

test('graph persistence: a hidden relationship retains its ratio and restores on the current real path after endpoints move', async () => {
  const h=harness('app.js');installSyntheticHub(h,3);installSnapshotPaths(h);
  h.run(`state.lastGraph={nodes:syntheticFullGraph.nodes.slice(0,3),tracked_ids:[1,2,3],edges:[
    {source:'SYN-HUB',target:'SYN-00000',cnt:1,ids:[1],amount_totals:[{currency:'CNY',amount:'0.01'}]},
    {source:'SYN-HUB',target:'SYN-00001',cnt:1,ids:[2],amount_totals:[{currency:'CNY',amount:'10.00'}]},
    {source:'SYN-00001',target:'SYN-00000',cnt:1,ids:[3],amount_totals:[{currency:'CNY',amount:'10.00'}]}]};
    globalThis.hiddenRatioFacts=JSON.stringify(state.lastGraph);
    state.graphView.fullIndex=FundsGraphView.createIndex(state.lastGraph);state.edgeType='line';
    state.presentation=newGraphPresentation({relationFilter:{currency:'CNY',min:'10.00'}});
    state.positions={'SYN-HUB':{x:100,y:200},'SYN-00000':{x:500,y:200},'SYN-00001':{x:300,y:400}};
    state.edgeLabelPos={'SYN-HUB=>SYN-00000':0.25};
    state.edgeSnapshotLabels={'SYN-HUB=>SYN-00000':{lx:1,ly:2,manual:true}};renderGraph();
    for(const node of state.graph.getNodes())if(node.getModel().id!=='SYN-00001')state.graph.updateItem(node,{x:node.getModel().x+100});
    savePositions();`);
  assert.equal(h.run('state.graph.getNodes().length'),3);
  assert.equal(h.run('state.graph.getEdges().length'),2,'the small relationship is filtered independently of its endpoints');
  const full=await h.run('window.__collectSnapshot({scope:"all",embedPhotos:false})');
  const hidden=full.edges.find(edge=>edge.target==='SYN-00000'&&edge.source==='SYN-HUB');
  assert.equal(hidden.labelRatio,0.25);assert.equal(hidden.labelManual,true);
  assert.equal(hidden.lx,null);assert.equal(hidden.ly,null,'no stale or guessed absolute point is exported without the real path');
  h.run('globalThis.hiddenRatioSnapshot=null');h.run('globalThis').hiddenRatioSnapshot=full;
  h.run('applyCanvasSnapshot(hiddenRatioSnapshot);state.presentation=newGraphPresentation();state.graphView.labelMode="all";renderGraph()');
  const restored=await h.run('window.__collectSnapshot({scope:"all",embedPhotos:false})');
  const visible=restored.edges.find(edge=>edge.target==='SYN-00000'&&edge.source==='SYN-HUB');
  assert.equal(visible.labelRatio,0.25);assert.equal(visible.labelManual,true);
  assert.equal(visible.lx,300);assert.equal(visible.ly,200,'the restored ratio follows the moved endpoints');
  assert.equal(visible.data.amount_totals[0].amount,'0.01');
  assert.equal(h.run('JSON.stringify(state.lastGraph)===hiddenRatioFacts'),true);
});

test('graph persistence: snapshots carry effective typography and node overrides while bottom anchors are recomputed', async () => {
  const h=harness('app.js');installSyntheticHub(h,3);installSnapshotPaths(h);
  h.run(`state.presentation=newGraphPresentation({nodeLabels:{'SYN-HUB':{fontSize:24,lineHeight:8,fontFamily:'Microsoft YaHei',fontWeight:'bold'}},
      nodeStyles:{'SYN-00000':{size:64,stroke:'#aabbcc',lineWidth:3}}});
    state.graphView.labelMode='all';state.positions={'SYN-HUB':{x:500,y:350},'SYN-00000':{x:500,y:350},'SYN-00001':{x:500,y:350}};
    syntheticLabelBounds=()=>({minX:200,minY:200,maxX:300,maxY:240});renderGraph();`);
  const expected=h.run('state.graph.getNodes().map(item=>({id:item.getModel().id,x:item.getModel().x,y:item.getModel().y}))');
  assertAppliedLabelPlacements(h,{retainAll:true});
  const saved=await h.run('window.__collectSnapshot({scope:"all",embedPhotos:false})');
  const main=saved.nodes.find(node=>node.id==='SYN-HUB');
  const typography=style=>Object.fromEntries(['fill','fontSize','lineHeight','fontFamily','fontWeight','fontStyle'].map(key=>[key,style[key]]));
  assert.deepEqual(typography(main.labelStyle),{fill:'#dfe3ea',fontSize:24,lineHeight:28,
    fontFamily:'Microsoft YaHei',fontWeight:'bold',fontStyle:'normal'});
  assert.deepEqual(typography(saved.edges[0].labelStyle),{fill:'#dfe3ea',fontSize:13,lineHeight:18,
    fontFamily:'Arial',fontWeight:'normal',fontStyle:'normal'});
  assert.equal(main.labelStyle.background,undefined);assert.equal(saved.edges[0].labelStyle.background,undefined);
  assert.equal(main.labelStyle.stroke,null);assert.equal(saved.edges[0].labelStyle.lineWidth,0);
  assert.deepEqual(JSON.parse(JSON.stringify(main.labelStyle)),JSON.parse(h.run('JSON.stringify(state.graph.getNodes()[0].getModel().labelCfg.style)')));
  assert.deepEqual(JSON.parse(JSON.stringify(saved.edges[0].labelStyle)),JSON.parse(h.run('JSON.stringify(state.graph.getEdges()[0].getModel().labelCfg.style)')));
  assert.deepEqual(JSON.parse(JSON.stringify(saved.presentation.nodeStyles['SYN-00000'])),{size:64,stroke:'#aabbcc',lineWidth:3});
  for(const source of expected) {
    const node=saved.nodes.find(node=>node.id===source.id);
    assert.deepEqual(JSON.parse(JSON.stringify(node.labelOffset)),{x:0,y:0},'snapshots serialize no side/top collision displacement');
    assert.equal(node.x,source.x);assert.equal(node.y,source.y);
    assert.equal(node.labelLeader,undefined,'bottom anchors never serialize a leader');
  }
  h.run('globalThis').typographySnapshot=saved;h.run('applyCanvasSnapshot(typographySnapshot)');
  assert.equal(h.run('state.graph.getNodes()[0].getModel().labelCfg.style.fontSize'),24);
  assert.equal(h.run('state.graph.getNodes()[0].getModel().labelCfg.style.lineHeight'),28);
  assert.equal(h.run('state.graph.getNodes()[1].getModel().size'),64);
  assert.equal(h.run('state.graph.getNodes()[1].getModel().style.stroke'),'#aabbcc');
  assertAppliedLabelPlacements(h,{retainAll:true});
});

test('graph persistence: non-embedded snapshots, save and clone never fetch or retain photo pixels', async () => {
  for(const action of [null,'#btnCanvasSave','#btnCanvasClone']) {
    const h=harness('app.js');h.run('globalThis.photoFreeCanvasBinder=bindCanvases');installSyntheticHub(h,3);
    h.run('photoFreeCanvasBinder();state.canvasId=10;state.canvasName="Synthetic source";state.lastGraph.nodes[0].photo_at=123;renderGraph()');
    h.network(path=>response(path==='/api/canvases/clone'?{canvas_id:20,name:'Synthetic clone',copied:3}
      :path==='/api/canvases/save'?{canvas_id:10,name:'Synthetic saved'}:[]));
    let snapshot;
    if(action) {
      await h.element(action).onclick();
      const request=h.requests.find(item=>item.path===(action==='#btnCanvasSave'?'/api/canvases/save':'/api/canvases/clone'));
      assert.ok(request,`${action} still persists its complete snapshot`);snapshot=JSON.parse(request.options.body).snapshot;
    } else snapshot=await h.run('window.__collectSnapshot({scope:"all",embedPhotos:false})');
    assert.equal(snapshot.nodes.length,3);
    assert.equal(snapshot.nodes.some(node=>Object.hasOwn(node,'img')),false);
    assert.equal(h.requests.filter(request=>String(request.path).includes('/api/account-photos/')).length,0,
      `${action||'snapshot'} never requests photo bytes`);
  }
});

test('graph persistence: HTML export explicitly retains the default embedded synthetic photo',async()=>{
  class SyntheticPhotoReader {
    readAsDataURL(){this.result='data:image/png;base64,c3ludGhldGlj';this.onload()}
  }
  const h=harness('app.js',undefined,undefined,{FileReader:SyntheticPhotoReader});installSyntheticHub(h,2);
  h.run('state.lastGraph.nodes[0].photo_at=123;renderGraph()');
  h.network(()=>response({}));h.element('#exportFmt').value='html';await h.element('#btnExportPng').onclick();
  assert.equal(h.requests.filter(request=>String(request.path).includes('/api/account-photos/')).length,1);
  const request=h.requests.find(request=>request.path==='/api/export/graph.html');assert.ok(request);
  const snapshot=JSON.parse(request.options.body);
  assert.equal(snapshot.nodes[0].img,'data:image/png;base64,c3ludGhldGlj');
  assert.equal(snapshot.nodes[1].img,undefined);
  assert.equal(h.downloads.length,1);
});

test('HTML photo collection is a hideable generating phase and its late snapshot cannot submit after rebinding',async()=>{
  class SyntheticPhotoReader { readAsDataURL(){this.result='data:image/png;base64,c3ludGhldGlj';this.onload()} }
  for(const revoked of [false,true]){
    const h=harness('app.js',undefined,undefined,{FileReader:SyntheticPhotoReader});installSyntheticHub(h,2);
    h.run('state.lastGraph.nodes[0].photo_at=123;renderGraph()');
    h.element('#exportActions').showModal=function(){this.open=true};
    h.run("exportWorkspace.open(document.querySelector('#btnExportActions'))");
    const photo=deferred();h.network(path=>String(path).includes('/api/account-photos/')?photo.promise:response({}));
    h.element('#exportFmt').value='html';const exporting=h.element('#btnExportPng').onclick();await flush();
    assert.equal(h.requests.filter(request=>String(request.path).includes('/api/account-photos/')).length,1);
    assert.equal(h.requests.filter(request=>request.path==='/api/export/graph.html').length,0);
    assert.equal(h.run('exportWorkspace.status().phase'),'generating');assert.equal(h.element('#btnExportClose').disabled,false);
    h.element('#btnExportClose').onclick();assert.equal(h.element('#exportActions').open,false);assert.equal(h.run('exportWorkspace.status().pending'),true);
    if(revoked)h.run('selectCase(2);selectCase(1)');
    photo.resolve(response({}));await exporting;
    assert.equal(h.requests.filter(request=>request.path==='/api/export/graph.html').length,revoked?0:1);
    assert.equal(h.downloads.length,revoked?0:1);assert.equal(h.element('#exportActions').open,false);
    if(!revoked){const payload=JSON.parse(h.requests.find(request=>request.path==='/api/export/graph.html').options.body);
      assert.equal(payload.nodes[0].data.out_totals[0].amount,'10000000000000000.01');}
  }
});

function clampedSamplingGraph(h){
  samplingGraph(h);
  h.run(`globalThis.sampleMaxZoom=4;globalThis.sampleZoomWrites=[];globalThis.sampleFitClamps=[];globalThis.sampleCapReads=[];
    globalThis.sampleCamera=[3.5,0,0,0,3.5,0,27,31,1];
    const graph=state.graph,get=graph.get,renderImage=graph.toDataURL;
    const camera={getMatrix:()=>[...sampleCamera],setMatrix(matrix){sampleCamera=[...matrix]}};
    graph.get=name=>name==='maxZoom'?sampleMaxZoom:name==='group'?camera:name==='width'?sampleSize[0]:name==='height'?sampleSize[1]:get(name);
    graph.set=(name,value)=>{if(name==='maxZoom'){sampleMaxZoom=value;sampleZoomWrites.push(value)}};
    graph.getZoom=()=>sampleCamera[0];
    graph.getCanvasByPoint=(x,y)=>({x:x*sampleCamera[0]+sampleCamera[6],y:y*sampleCamera[4]+sampleCamera[7]});
    graph.getPointByCanvas=(x,y)=>({x:(x-sampleCamera[6])/sampleCamera[0],y:(y-sampleCamera[7])/sampleCamera[4]});
    graph.zoomTo=value=>{sampleCamera[0]=sampleCamera[4]=Math.min(sampleMaxZoom,value)};
    graph.fitView=()=>{const ratio=sampleSize[0]/1000,requested=3.5*ratio,actual=Math.min(requested,sampleMaxZoom);
      sampleFitClamps.push({requested,actual,cap:sampleMaxZoom});sampleCamera=[actual,0,0,0,actual,0,80*ratio,100*ratio,1]};
    graph.toDataURL=(...args)=>{sampleCapReads.push(sampleMaxZoom);return renderImage(...args)};
  `);
}

test('image sampling raises the real zoom clamp for PNG PDF PNG and restores each screen cap and camera',async()=>{
  const h=harness('app.js');clampedSamplingGraph(h);h.network(()=>response({}));
  const facts=h.run('JSON.stringify(state.lastGraph)'),camera=JSON.parse(h.run('JSON.stringify(sampleCamera)'));
  for(const [format,cap,zoom] of [['png',8,7],['pdf',12,10.5],['png',8,7]]){
    h.element('#exportFmt').value=format;const exporting=h.element('#btnExportPng').onclick();
    assert.equal(h.run('sampleMaxZoom'),cap);assert.deepEqual(JSON.parse(h.run('JSON.stringify(sampleFitClamps.at(-1))')),{requested:zoom,actual:zoom,cap});
    await finishSample(h);await exporting;assert.equal(h.run('sampleMaxZoom'),4);
    assert.deepEqual(JSON.parse(h.run('JSON.stringify(sampleCamera)')),camera);assert.equal(h.run('JSON.stringify(state.lastGraph)'),facts);
  }
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(sampleCapReads)')),[8,12,8]);
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(sampleZoomWrites)')),[8,4,12,4,8,4]);assert.equal(h.downloads.length,3);
});

test('sampling exceptions restore the real zoom cap and permit a fresh capture',async()=>{
  for(const phase of ['fitView','toDataURL']){
    const h=harness('app.js');clampedSamplingGraph(h);
    h.run(`const original=state.graph.${phase};let fail=true;state.graph.${phase}=(...args)=>{if(fail){fail=false;throw new Error('合成采样异常')};return original(...args)};`);
    h.element('#exportFmt').value='png';const failed=h.element('#btnExportPng').onclick();await flush();
    if(h.timers.some(timer=>timer.delay===400&&!timer.ran))await finishSample(h);await failed;
    assert.equal(h.run('sampleMaxZoom'),4,phase);assert.equal(h.run('state.graphView.exportDepth'),0,phase);assert.equal(h.downloads.length,0);
    const retry=h.element('#btnExportPng').onclick();assert.equal(h.run('sampleMaxZoom'),8);await finishSample(h);await retry;
    assert.equal(h.run('sampleMaxZoom'),4);assert.equal(h.downloads.length,1);
  }
});

test('cancelled and late image jobs restore only their original zoom cap',async()=>{
  for(const transition of ['renderGraph()','selectCase(2);selectCase(1)','globalThis.retiredSamplingGraph=state.graph;state.graph={clear(){}}']){
    const h=harness('app.js');clampedSamplingGraph(h);h.element('#exportFmt').value='pdf';const exporting=h.element('#btnExportPng').onclick();
    const old=h.timers.findLast(timer=>timer.delay===400);assert.equal(h.run('sampleMaxZoom'),12);h.run(transition);old.callback();await exporting;
    assert.equal(h.run('sampleMaxZoom'),4,transition);assert.equal(h.downloads.length,0,transition);
  }
  const h=harness('app.js');clampedSamplingGraph(h);h.element('#exportFmt').value='png';const oldJob=h.element('#btnExportPng').onclick();
  const old=h.timers.findLast(timer=>timer.delay===400);h.run('renderGraph()');await oldJob;assert.equal(h.run('sampleMaxZoom'),4);
  const newJob=h.element('#btnExportPng').onclick();assert.equal(h.run('sampleMaxZoom'),8);old.callback();assert.equal(h.run('sampleMaxZoom'),8,'a late timer cannot restore over the new sampler');
  await finishSample(h);await newJob;assert.equal(h.run('sampleMaxZoom'),4);assert.equal(h.downloads.length,1);
});

function installSamplingVisuals(h, visible=true, initialize=true) {
  if(initialize)samplingGraph(h);
  const state=h.run('state'),graph=state.graph;
  const createSelection=(ids,shown)=>({ids:new Set(ids),visible:shown,calls:[],
    status(){return{visualsVisible:this.visible}},
    setVisualsVisible(value){this.visible=Boolean(value);this.calls.push(this.visible);return true},
    refresh(){},rebind(){},clear(){this.ids.clear()},destroy(){}});
  const selection=createSelection(['SYN-HUB','SYN-00000'],visible);state.graphSelection=selection;
  for(const item of [...graph.getNodes(),...graph.getEdges()]) {
    item.testStates=new Set();item.hasState=name=>item.testStates.has(name);item.getStates=()=>[...item.testStates];
  }
  graph.getNodes()[0].testStates.add('recent');
  graph.findAllByState=(kind,name)=>(kind==='node'?graph.getNodes():graph.getEdges()).filter(item=>item.hasState(name));
  graph.setItemState=(item,name,on)=>on?item.testStates.add(name):item.testStates.delete(name);
  graph.clearItemStates=(item,names)=>{for(const target of item?[item]:[...graph.getNodes(),...graph.getEdges()]) {
    if(!names)target.testStates.clear();else for(const name of Array.isArray(names)?names:[names])target.testStates.delete(name);
  }};
  const outline={visible,values:{},get(name){return name==='name'?'funds-edge-selection-outline':name==='visible'?this.visible:this.values[name]},
    attr(values){if(typeof values==='object')Object.assign(this.values,values);return this.values[values]},
    hide(){this.visible=false},show(){this.visible=true},isVisible(){return this.visible}};
  graph.getEdges()[0].shapes.push(outline);
  graph.getEdges()[0].getKeyShape=()=>({attr:name=>name==='path'?[['M',80,80],['L',88,80]]:undefined});
  state.graphView.selectedEdge='SYN-HUB=>SYN-00000';
  const sample=graph.toDataURL,sampled=[];
  let fail=false;
  graph.toDataURL=()=>{
    sampled.push({selection:selection.status().visualsVisible,outline:outline.visible,recent:graph.getNodes().some(item=>item.hasState('recent'))});
    if(fail)throw new Error('synthetic sampling failure');return sample();
  };
  return{state,selection,outline,sampled,createSelection,fail(){fail=true}};
}

test('graph persistence: selected edge suppresses inherited G6 shadows and leaves selected state out of PNG pixels',async()=>{
  const h=harness('app.js'),f=installSamplingVisuals(h),graph=f.state.graph,edge=graph.getEdges()[0];
  let styles={selected:{shadowBlur:18,shadowColor:'#1890ff',stroke:'#1890ff',lineWidth:10}};
  edge.get=name=>name==='styles'?styles:undefined;
  edge.set=(name,value)=>{if(name==='styles')styles=value};
  const baseShadow={shadowBlur:0,shadowColor:'transparent'},defaultSelectedShadow={shadowBlur:18,shadowColor:'#1890ff'};
  edge.getKeyShape=()=>({attr(name){
    if(name==='path')return[['M',80,80],['L',88,80]];
    const active=edge.hasState('selected')?{...defaultSelectedShadow,...styles.selected}:baseShadow;
    return name===undefined?active:active[name];
  }});
  h.run('refreshSelectedEdgeAppearance()');
  assert.equal(styles.selected.shadowBlur,0,'the state cache overrides G6 default selection glow');
  assert.equal(styles.selected.shadowColor,'transparent');
  assert.equal(styles.selected.stroke,'#66d9ef','selection uses the approved cyan without changing the saved business style');
  assert.equal(styles.selected.lineWidth,edge.getModel().style.lineWidth,'selection retains the encoded line width');
  assert.equal(edge.hasState('selected'),true);
  assert.equal(edge.getKeyShape().attr('shadowBlur'),0);
  const toDataURL=graph.toDataURL,selectedDuringPixels=[];
  graph.toDataURL=()=>{selectedDuringPixels.push(edge.hasState('selected'));return toDataURL()};
  const ids=[...f.selection.ids];h.element('#exportFmt').value='png';const pending=h.element('#btnExportPng').onclick();
  assert.equal(edge.hasState('selected'),false,'hiding the custom outline alone is insufficient');
  await finishSample(h);await pending;
  assert.deepEqual(selectedDuringPixels,[false]);
  assert.equal(edge.hasState('selected'),true,'the same binding restores interactive edge selection');
  assert.equal(styles.selected.shadowBlur,0);assert.equal(styles.selected.shadowColor,'transparent');
  assert.deepEqual([...f.selection.ids],ids);
  assert.equal(h.downloads.length,1);
});

test('graph persistence: large zoomed amounts obey label policy without leaving their relationship for body avoidance',()=>{
  const h=harness('app.js');installSyntheticHub(h,3);
  h.run(`state.lastGraph={nodes:syntheticFullGraph.nodes.slice(0,3),edges:[syntheticFullGraph.edges[0]],tracked_ids:[1,2]};
    state.graphView.fullIndex=FundsGraphView.createIndex(state.lastGraph);state.graphView.labelMode='all';
    state.positions={'SYN-HUB':{x:100,y:100},'SYN-00000':{x:200,y:100},'SYN-00001':{x:150,y:193.33333333333334}};
    state.presentation=newGraphPresentation({nodeStyles:{'SYN-HUB':{size:96},'SYN-00000':{size:96},'SYN-00001':{size:96}}});
    syntheticZoom=3;syntheticViewport={minX:0,minY:0,maxX:1000/3,maxY:700/3};
    syntheticLabelBounds=model=>model.source?{minX:250,minY:280,maxX:650,maxY:320}:null;
    renderGraph();state.graphView.selectedEdge=null;state.edgeSnapshotLabels={};state.edgeLabelPos={};
    globalThis.largeLabelFacts=JSON.stringify(state.lastGraph);
    globalThis.edgeOnlyPolicy={nodeIds:new Set(),edgeKeys:new Set(['SYN-HUB=>SYN-00000'])};`);
  const state=h.run('state'),bodies=[
    {key:'source',bounds:{minX:156,minY:156,maxX:444,maxY:444}},
    {key:'target',bounds:{minX:456,minY:156,maxX:744,maxY:444}},
    {key:'third',bounds:{minX:306,minY:436,maxX:594,maxY:724}},
  ];
  assert.equal(boxesIntersect({minX:250,minY:451,maxX:650,maxY:491},bodies[2].bounds),true,
    'the third node blocks the first candidate below the endpoint union');
  for(const variant of [{mode:'auto',depth:0},{mode:'all',depth:0},{mode:'all',depth:1,stale:true}]) {
    state.graphView.labelMode=variant.mode;state.graphView.exportDepth=variant.depth;state.graphView.exportFooterHeight=0;
    state.graphView.clipResult={nodeIds:new Set(variant.stale?[]:state.graphView.nodeItems.keys()),edgeKeys:new Set(['SYN-HUB=>SYN-00000'])};
    h.run('for(const item of [...state.graph.getNodes(),...state.graph.getEdges()])resetAutoLabelOffset(item)');
    assert.equal(h.run('graphScreenBodies().size'),3,'export uses every node body despite the stale viewport subset');
    const placed=h.run('avoidGraphLabelCollisions(edgeOnlyPolicy)');
    assert.equal(placed.edgeKeys.has('SYN-HUB=>SYN-00000'),true,'a requested amount remains visible despite body conflicts');
    assert.equal(placed.edgePlacements.size,0,'relationship text never receives a free-space candidate');
    h.run('applyGraphLabels()');
    assert.equal(state.graph.getEdges()[0].shapes[0]._fundsLabelOffset,undefined,
      'screen-space node displacement is not applied to relationship text at 3x zoom');
  }
  state.graphView.exportDepth=0;
  assert.equal(h.run('JSON.stringify(state.lastGraph)===largeLabelFacts'),true);
});

for(const phase of ['success','sample-error','cancel','already-hidden','edge-hidden','rebind']) {
  test(`graph persistence: clean image sampling restores only its own visual state after ${phase}`,async()=>{
    const h=harness('app.js'),visible=phase!=='already-hidden',f=installSamplingVisuals(h,visible);
    if(phase==='edge-hidden')f.outline.visible=false;
    const outlineVisible=f.outline.visible;
    const ids=[...f.selection.ids],recent=f.state.graph.getNodes()[0];
    if(phase==='sample-error')f.fail();
    h.element('#exportFmt').value='png';const pending=h.element('#btnExportPng').onclick();
    assert.equal(f.selection.visible,false,'selection decoration is suspended before sampling');
    assert.equal(f.outline.visible,false,'selected edge decoration is suspended too');
    assert.equal(recent.hasState('recent'),false,'recent-tracking decoration is suspended too');
    assert.deepEqual([...f.selection.ids],ids,'clean rendering never clears selected IDs');
    if(phase==='cancel')h.run('cancelGraphImageExport()');
    else if(phase==='rebind') {
      h.run('state.caseGeneration+=1');
      f.state.graphSelection=f.createSelection(['SYN-NEW-BINDING'],true);
      f.outline.visible=true;recent.testStates.delete('recent');
    }
    if(phase!=='cancel')await finishSample(h);
    await pending;
    assert.equal(f.state.graphView.exportDepth,0);
    if(phase==='rebind') {
      assert.equal(f.selection.visible,false,'a stale completion does not revive the old selection');
      assert.equal(f.state.graphSelection.visible,true);
      assert.deepEqual(f.state.graphSelection.calls,[],'a stale completion does not change the replacement controller');
      assert.deepEqual([...f.state.graphSelection.ids],['SYN-NEW-BINDING']);
      assert.equal(f.outline.visible,true,'stale cleanup does not overwrite replacement visuals');
      assert.equal(recent.hasState('recent'),false);
    } else {
      assert.equal(f.selection.visible,visible,'the exact previous selection visibility is restored');
      assert.equal(f.outline.visible,outlineVisible,'the exact previous edge-outline visibility is restored independently of node selection');
      assert.equal(recent.hasState('recent'),true,'recent state is restored after success, failure or cancellation');
    }
    assert.deepEqual([...f.selection.ids],ids);
    for(const sample of f.sampled)assert.deepEqual(sample,{selection:false,outline:false,recent:false});
    assert.equal(h.downloads.length,['success','already-hidden','edge-hidden'].includes(phase)?1:0);
  });
}

test('graph persistence: delayed recent feedback stays out of PNG sampling and resumes only for its own binding',async()=>{
  for(const revoked of [false,true]) {
    const h=harness('app.js');samplingGraph(h);
    h.run('state.justAdded=new Set([1]);renderGraph()');
    const recentTimer=h.timers.findLast(timer=>timer.delay===80&&!timer.cancelled);
    assert.ok(recentTimer,'the real renderer queued recent-tracking feedback');
    const f=installSamplingVisuals(h,true,false);
    for(const node of f.state.graph.getNodes())node.testStates.delete('recent');
    h.element('#exportFmt').value='png';const pending=h.element('#btnExportPng').onclick();
    assert.equal(f.state.graphView.exportDepth,1);
    if(revoked)h.run('state.caseGeneration+=2');
    recentTimer.ran=true;recentTimer.callback();
    assert.equal(f.state.graph.getNodes().some(node=>node.hasState('recent')),false,
      'the 80 ms callback cannot reintroduce decoration into the 400 ms capture');
    await finishSample(h);await pending;
    for(const sample of f.sampled)assert.equal(sample.recent,false);
    const highlighted=f.state.graph.getNodes().filter(node=>node.hasState('recent')).map(node=>node.getModel().id).sort();
    assert.deepEqual(Array.from(highlighted),revoked?[]:['SYN-00000','SYN-HUB'],
      'pending feedback resumes after export, but never after a binding revocation');
    assert.equal(h.downloads.length,revoked?0:1);
  }
});

async function pendingSnapshotCamera(h){
  samplingGraph(h);installSnapshotPaths(h);h.network(()=>response({}));
  h.run(`globalThis.snapshotCamera=[1,0,0,0,1,0,19,23,1];globalThis.snapshotFitCalls=[];globalThis.snapshotSamples=[];
    const graph=state.graph,get=graph.get;
    const camera={getMatrix:()=>[...snapshotCamera],setMatrix(matrix){snapshotCamera=[...matrix]}};
    graph.get=name=>name==='group'?camera:name==='width'?sampleSize[0]:name==='height'?sampleSize[1]:get(name);
    graph.getZoom=()=>snapshotCamera[0];graph.zoomTo=value=>{snapshotCamera[0]=snapshotCamera[4]=value};
    graph.translate=(x,y)=>{snapshotCamera[6]+=x;snapshotCamera[7]+=y};
    graph.getCanvasByPoint=(x,y)=>({x:x*snapshotCamera[0]+snapshotCamera[6],y:y*snapshotCamera[4]+snapshotCamera[7]});
    graph.getPointByCanvas=(x,y)=>({x:(x-snapshotCamera[6])/snapshotCamera[0],y:(y-snapshotCamera[7])/snapshotCamera[4]});
    graph.fitView=()=>{const scale=sampleSize[0]/1000;snapshotCamera=[scale,0,0,0,scale,0,0,0,1]};
    const originalFit=fitGraph;fitGraph=(...args)=>{snapshotFitCalls.push({depth:state.graphView.exportDepth,args});return originalFit(...args)};
    const sample=graph.toDataURL;graph.toDataURL=()=>{snapshotSamples.push({camera:[...snapshotCamera],size:[...sampleSize]});return sample()};
  `);
  const snapshot=await h.run('window.__collectSnapshot({scope:"all",embedPhotos:false})');
  h.run('globalThis.pendingCameraSnapshot=undefined');h.run('globalThis').pendingCameraSnapshot=snapshot;
  h.run('applyCanvasSnapshot(pendingCameraSnapshot)');
  const timer=h.timers.findLast(timer=>timer.delay===250&&!timer.ran&&!timer.cancelled);
  assert.ok(timer,'the real reopen path schedules its delayed camera restoration');
  return{timer,camera:()=>JSON.parse(h.run('JSON.stringify(snapshotCamera)'))};
}

for(const format of ['png','pdf'])test(`graph persistence timer: reopened canvas delayed fit cannot change active ${format} sampling camera`,async()=>{
  const h=harness('app.js'),f=await pendingSnapshotCamera(h);h.element('#exportFmt').value=format;
  const screen=f.camera(),pending=h.element('#btnExportPng').onclick();
  assert.equal(h.run('state.graphView.exportDepth'),1);const sampling=f.camera();assert.notDeepEqual(sampling,screen);
  f.timer.ran=true;f.timer.callback();const afterRestore=f.camera();
  await finishSample(h);await pending;
  assert.deepEqual(afterRestore,sampling,'the 250 ms reopen callback cannot refit the camera owned by the 400 ms sampler');
  assert.equal(h.run('snapshotFitCalls.some(call=>call.depth>0)'),false,'reopen cannot invoke normal-screen fit inside image sampling');
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(snapshotSamples[0].camera)')),sampling);
  assert.deepEqual(f.camera(),screen,'sampling still restores the original screen camera');
});

test('graph persistence timer: delayed reopen fit cannot overwrite a newer user zoom and pan',async()=>{
  const h=harness('app.js'),f=await pendingSnapshotCamera(h);
  h.run('state.graph.zoomTo(2.3);state.graph.translate(77,-59)');const intended=f.camera();
  f.timer.ran=true;f.timer.callback();
  assert.deepEqual(f.camera(),intended,'a valid same-binding camera interaction outranks the pending initial fit');
});

test('graph persistence timer: delayed reopen fit cannot run while the user relayout worker is pending',async()=>{
  const h=harness('app.js'),f=await pendingSnapshotCamera(h),workers=[];
  h.run('globalThis').Worker=class{postMessage(message){workers.push(message)}terminate(){}};
  await h.run('relayout(false)');assert.equal(workers.length,1);
  const before=f.camera(),fits=h.run('snapshotFitCalls.length');f.timer.ran=true;f.timer.callback();
  assert.equal(h.run('snapshotFitCalls.length'),fits,'the old open callback cannot fit pre-layout geometry while a later job owns placement');
  assert.deepEqual(f.camera(),before);
});

for(const host of ['null-identity','missing-api'])test(`image camera boundary: ${host} distinguishes an identity transform from an absent camera API`,async()=>{
  const h=harness('app.js');samplingGraph(h);h.network(()=>response({}));
  h.run(`globalThis.identityRestores=[];globalThis.identityFitCalls=0;globalThis.identityCamera=null;
    const originalFit=fitGraph;fitGraph=(...args)=>{identityFitCalls++;return originalFit(...args)};`);
  if(host==='null-identity')h.run(`
    const graph=state.graph,get=graph.get,identity=()=>[1,0,0,0,1,0,0,0,1];
    const effective=()=>identityCamera||identity();
    const group={getMatrix:()=>identityCamera&&[...identityCamera],setMatrix(matrix){identityRestores.push([...matrix]);identityCamera=[...matrix]}};
    graph.get=name=>name==='group'?group:name==='width'?sampleSize[0]:name==='height'?sampleSize[1]:get(name);
    graph.getZoom=()=>effective()[0];
    graph.zoomTo=value=>{identityCamera=[...effective()];identityCamera[0]=identityCamera[4]=value};
    graph.getCanvasByPoint=(x,y)=>{const m=effective();return{x:x*m[0]+m[6],y:y*m[4]+m[7]}};
    graph.getPointByCanvas=(x,y)=>{const m=effective();return{x:(x-m[6])/m[0],y:(y-m[7])/m[4]}};
    // A legitimate content fit need not equal the user's identity transform.
    graph.fitView=()=>{const scale=sampleSize[0]/1000;identityCamera=[1.25*scale,0,0,0,1.25*scale,0,47*scale,31*scale,1]};
  `);
  const pending=h.element('#btnExportPng').onclick();await finishSample(h);await pending;
  assert.equal(h.downloads.length,1);assert.equal(h.run('state.graphView.exportDepth'),0);
  if(host==='null-identity'){
    assert.deepEqual(JSON.parse(h.run('JSON.stringify(identityCamera)')),[1,0,0,0,1,0,0,0,1],
      'G6 null matrix means identity and must be restored without replacing it with content fit');
    assert.deepEqual(JSON.parse(h.run('JSON.stringify(identityRestores)')),[[1,0,0,0,1,0,0,0,1]]);
    assert.equal(h.run('identityFitCalls'),0,'a complete camera API never uses the legacy fit fallback');
  }else assert.equal(h.run('identityFitCalls'),1,'an older host without a camera API retains the established fallback');
});

test('graph persistence: clipping indexes complete bottom-anchored text before a later pan',()=>{
  const h=harness('app.js');installSyntheticHub(h,3);
  h.run(`state.lastGraph={nodes:syntheticFullGraph.nodes.slice(0,3),edges:[],tracked_ids:[]};
    state.graphView.fullIndex=FundsGraphView.createIndex(state.lastGraph);syntheticZoom=1;
    state.positions={'SYN-HUB':{x:500,y:350},'SYN-00000':{x:500,y:410},'SYN-00001':{x:500,y:290}};
    state.presentation=newGraphPresentation({nodeStyles:{'SYN-00000':{size:96},'SYN-00001':{size:96}}});
    state.graphView.labelMode='focus';state.graphView.selectedAccount='SYN-HUB';
    syntheticLabelBounds=model=>model.id==='SYN-HUB'?{minX:350,minY:340,maxX:650,maxY:360}:null;
    renderGraph();`);
  const graph=h.run('state.graph'),root=graph.getNodes().find(item=>item.getModel().id==='SYN-HUB'),model=root.getModel();
  h.run('for(const item of state.graph.getNodes())resetAutoLabelOffset(item)');
  const project=bounds=>{
    const a=graph.getCanvasByPoint(bounds.minX,bounds.minY),b=graph.getCanvasByPoint(bounds.maxX,bounds.maxY);
    return{minX:Math.min(a.x,b.x),minY:Math.min(a.y,b.y),maxX:Math.max(a.x,b.x),maxY:Math.max(a.y,b.y)};
  };
  const body=()=>project({minX:model.x-model.size/2,minY:model.y-model.size/2,maxX:model.x+model.size/2,maxY:model.y+model.size/2});
  root.getKeyShape=()=>({getBBox:()=>({minX:-model.size/2,minY:-model.size/2,maxX:model.size/2,maxY:model.size/2}),getCanvasBBox:body});
  for(const shape of root.shapes){
    shape.getBBox=()=>({minX:shape.attr('x')-150,minY:shape.attr('y')-120,maxX:shape.attr('x')+150,maxY:shape.attr('y')+120});
    shape.getCanvasBBox=()=>project({minX:model.x+shape.getBBox().minX,minY:model.y+shape.getBBox().minY,
      maxX:model.x+shape.getBBox().maxX,maxY:model.y+shape.getBBox().maxY});
    shape.attr({x:0,y:-200});
  }
  const group=root.getContainer();
  group.addShape=(_kind,config)=>{
    const shape={values:{...config.attrs},get:name=>name==='name'?config.name:undefined,
      attr(values){if(typeof values==='object')Object.assign(this.values,values);return this.values[values]},
      toBack(){},remove(){const index=root.shapes.indexOf(this);if(index>=0)root.shapes.splice(index,1)},
      getCanvasBBox(){const points=this.values.path.map(segment=>({x:model.x+segment[1],y:model.y+segment[2]}));
        return project({minX:Math.min(...points.map(p=>p.x)),minY:Math.min(...points.map(p=>p.y)),
          maxX:Math.max(...points.map(p=>p.x)),maxY:Math.max(...points.map(p=>p.y))})}};
    root.shapes.push(shape);return shape;
  };
  group.getCanvasBBox=()=>{
    const bounds=[body(),...root.shapes.filter(shape=>shape.visible!==false).map(shape=>shape.getCanvasBBox())];
    return{minX:Math.min(...bounds.map(b=>b.minX)),minY:Math.min(...bounds.map(b=>b.minY)),
      maxX:Math.max(...bounds.map(b=>b.maxX)),maxY:Math.max(...bounds.map(b=>b.maxY))};
  };
  h.run('rebuildGraphViewport()');
  const base=JSON.parse(h.run('JSON.stringify(graphItemBounds(state.graphView.nodeItems.get("SYN-HUB")))'));
  const state=h.run('state'),index=state.graphView.clipIndex,included=[];
  state.graphView.clipIndex={...index,include(kind,key,bounds){if(kind==='node'&&key==='SYN-HUB')included.push(bounds&&{...bounds});return index.include(kind,key,bounds)}};
  h.run('applyGraphLabels(false)');
  const final=JSON.parse(h.run('JSON.stringify(graphItemBounds(state.graphView.nodeItems.get("SYN-HUB")))'));
  assert.equal(model.fundsLabelLeader,undefined,'automatic text movement never produces a leader');
  assert.equal(root.shapes.some(shape=>shape._fundsLabelOffset),false,'node text does not receive a collision displacement');
  assert.equal(root.shapes.some(shape=>['text-bg-shape','funds-label-leader'].includes(shape.get('name'))),false);
  assert.ok(final.maxY>600&&base.maxY<480,'the complete multiline bottom label extends into the later viewport and its margin');
  assert.ok(included.some(bounds=>bounds&&bounds.maxY>=final.maxY),'clipIndex.include observes final anchored text bounds');
  const text=root.shapes.find(shape=>shape.get('name')==='text-shape');
  assert.ok(Math.abs(text.getCanvasBBox().minY-body().maxY-6)<1e-8);
  h.run('syntheticViewport={minX:0,minY:600,maxX:1000,maxY:1300};applyGraphViewport()');
  assert.equal(state.graphView.clipResult.nodeIds.has('SYN-HUB'),true,'visible multiline text keeps its offscreen node group drawable');
  assert.equal(group.get('visible'),true);
});

for(const outcome of ['success','capture-error'])test(`print image ${outcome}: white paper, black labels and explicit note color restore to screen`,async()=>{
  const h=harness('app.js');samplingGraph(h);h.network(()=>response({}));
  const state=h.run('state'),graph=state.graph,values={label:'#dfe3ea',note:'#e3a12b'},samples=[];
  const shape=(key,name)=>({get:property=>property==='type'?'text':property==='name'?name:undefined,
    attr(property,value){if(arguments.length===2)values[key]=value;return values[key]}});
  graph.getNodes()[0].shapes.push(shape('label','synthetic-print-label'),shape('note','funds-node-annotation'));
  const original=graph.toDataURL;
  graph.toDataURL=(type,background)=>{samples.push({type,background,...values});if(outcome==='capture-error')throw new Error('合成图像失败');return original();};
  const exporting=h.element('#btnExportPng').onclick();await finishSample(h);await exporting;
  assert.deepEqual(samples,[{type:'image/png',background:'#ffffff',label:'#111111',note:'#e3a12b'}]);
  assert.deepEqual(values,{label:'#dfe3ea',note:'#e3a12b'});assert.equal(state.graphView.exportDepth,0);
  assert.equal(h.downloads.length,outcome==='success'?1:0);
});

test('annotations: collected node snapshots carry only safe display metadata for offline HTML and canvas restore',async()=>{
  const h=harness('app.js');samplingGraph(h);
  h.run(`state.presentation=newGraphPresentation({annotations:[{id:'SYN-HUB',text:'主体\\n合成核对',anchor:'above',dx:21,dy:-12,size:20,fontFamily:'KaiTi',color:'#C084FC',html:'<b>ignored</b>'}]})`);
  const snapshot=await h.run('window.__collectSnapshot({scope:"all",embedPhotos:false})');
  assert.deepEqual(JSON.parse(JSON.stringify(snapshot.nodes.find(node=>node.id==='SYN-HUB').annotation)),{
    id:'SYN-HUB',text:'主体\n合成核对',anchor:'above',dx:21,dy:-12,size:20,fontFamily:'KaiTi',color:'#c084fc'});
  assert.equal(snapshot.nodes.find(node=>node.id==='SYN-00000').annotation,undefined);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===originalGraphFacts'),true);
});
