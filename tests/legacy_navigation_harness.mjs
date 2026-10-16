import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const root = fileURLToPath(new URL('../', import.meta.url));
const cases = [{ id: 1, name: 'Synthetic A' }, { id: 2, name: 'Synthetic B' }, { id: 3, name: 'Synthetic C' }];
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const response = payload => ({ ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => payload, text: async () => JSON.stringify(payload), blob: async () => new Blob(['synthetic']) });

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
      addEventListener() {}, removeEventListener() {},setAttribute() {},querySelectorAll() { return []; }, appendChild() {}, remove() {}, focus() {}, select() {},
      getBoundingClientRect(){return {left:0,top:0,width:this.width,height:this.height}},
      getContext() { return canvasContext; }, toDataURL() { return 'data:image/png;base64,c3ludGhldGlj'; },
      click() { if (this.download || this.href) downloads.push({ href: this.href, filename: this.download }); },
    };
    elements.set(key, node); return node;
  };
  element('#caseSelect').value = '1'; element('#caseSel').value = '1';
  element('#analysisLink').href = '/analysis'; element('#backToGraph').href = '/';
  for (const [key, value] of Object.entries({ '#ffWin': '30', '#ffTol': '0.25', '#cycDays': '7', '#subjects': '', '#ctxX': '5' })) element(key).value = value;
  const algorithm = { checked: true, value: 'cycles' };
  let fetcher = async path => response(path === '/api/cases' ? cases : { flows: 0, accounts: 0 });
  class TestURL extends URL { static createObjectURL(blob) { objectUrls.push({url:'blob:synthetic',blob}); return 'blob:synthetic'; } static revokeObjectURL() {} }
  document = { querySelector: element, querySelectorAll: selector => selector === '.alg' ? [algorithm] : [],
    createElement: kind => element(Symbol(kind)), body: element('body'), addEventListener() {}, };
  const window = { location, history: { replaceState(_state, _title, value) { location.href = new URL(value, location).href; } }, addEventListener() {},removeEventListener(){},
    requestAnimationFrame:requestFrame,cancelAnimationFrame:cancelFrame };
  const sandbox = { window, document, localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, String(value)), removeItem: key => storage.delete(key) },
    ...extras, URL: TestURL, URLSearchParams, Blob, atob:globalThis.atob,console,requestAnimationFrame:requestFrame,cancelAnimationFrame:cancelFrame,
    setTimeout(callback, delay = 0) { timers.push({ callback, delay, cancelled: false }); return timers.length; },
    clearTimeout(id) { if (timers[id - 1]) timers[id - 1].cancelled = true; },
    confirm: () => true, prompt: () => 'Synthetic new', alert() {},
    fetch: async (path, options = {}) => { requests.push({ path, options }); return fetcher(path, options); }, };
  const context = vm.createContext(sandbox);
  for (const helper of ['money.js', 'graph-layout.js', 'graph-view.js','graph-viewport.js','graph-minimap.js','graph-labels.js']) vm.runInContext(readFileSync(`${root}static/${helper}`, 'utf8'), context, { filename: helper });
  context.FundsGraphView = context.window.FundsGraphView;
  context.FundsGraphViewport = context.window.FundsGraphViewport;
  context.FundsGraphMinimap = context.window.FundsGraphMinimap;
  context.FundsGraphLabels = context.window.FundsGraphLabels;
  vm.runInContext(readFileSync(`${root}static/${file}`, 'utf8'), context, { filename: file });
  return { element, downloads, objectUrls,requests, storage, location, timers,frames, run: code => vm.runInContext(code, context),
    flushFrames(){const pending=frames.filter(frame=>!frame.cancelled&&!frame.ran);for(const frame of pending){frame.ran=true;frame.callback()}},
    network: fn => { fetcher = fn; },
    bindApp() { vm.runInContext("promptText = async () => 'Synthetic new';", context); vm.runInContext('bindPhotoCrop = () => {}; bindCanvases = () => {}; initEdgeLabels = () => {}; refreshStats = async () => {}; loadGraph = async () => {}; refreshCanvasList = async () => {}; doSearch = async () => {}; bindUI();', context); },
  };
}

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
  assert.equal(h.run('state.caseId'), 1);
  assert.equal(h.location.searchParams.get('case_id'), '1');
});

test('new case discards the old named canvas and context', async () => {
  const h = harness('app.js'); await h.run('loadCases()'); h.bindApp();
  h.run("state.canvasId = 99; state.canvasName = 'Old synthetic canvas'; state.ctx = { flowId: 1 }; state.imp = { import_id: 'old' };");
  h.network((path, options) => response(options.method === 'POST' ? cases[2] : cases));
  await h.element('#btnNewCase').onclick();
  assert.equal(h.run('state.caseId'), 3);
  assert.equal(h.run('state.canvasId'), 0);
  assert.equal(h.run('state.canvasName'), '');
  assert.equal(h.run('state.ctx'), null);
  assert.equal(h.run('state.imp'), null);
  assert.equal(h.element('#canvasSel').value, '0');
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
  pending.resolve(response({ marker: 'LATE-A' })); await old;
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
  pending.resolve(response({ marker: 'OLD-GENERATION-A' })); await old;
  assert.equal(h.run('lastResults'), null);
  assert.notEqual(h.element('#results').innerHTML, 'OLD-GENERATION-A');
});

test('report uses the completed run parameters and suppresses late cross-case download', async () => {
  const h = harness('analysis.js'); await flush();
  h.run("render = value => { document.querySelector('#results').innerHTML = value.marker; };");
  h.network(() => response({ marker: 'CURRENT-A' })); await h.run('run()');
  h.element('#ffWin').value = '90';
  const pending = deferred(); h.network(path => path === '/api/analysis/report' ? pending.promise : response({ flows: 0, accounts: 0 }));
  const report = h.element('#btnReport').onclick();
  const request = h.requests.findLast(item => item.path === '/api/analysis/report');
  assert.equal(JSON.parse(request.options.body).params.ff_window_minutes, 30);
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

test('analysis money and approximate dimensions have distinct display rules', async () => {
  const h = harness('analysis.js'); await flush();
  assert.equal(h.run("money('9999999999999999.99','USD')"), 'USD 9,999,999,999,999,999.99');
  assert.equal(h.run("dimensionValue({dim:'total',value:'0.01',currency:'CNY'})"), '¥0.01');
  assert.equal(h.run("dimensionValue({dim:'cnt',value:12,currency:'CNY'})"), '12');
  assert.equal(h.run("dimensionValue({dim:'night',value:0.125,currency:'CNY'})"), '0.125');
  h.run("render({cycles:{count:1,cycles:[{accounts_names:['SYN-A','SYN-B'],edges:[],len:2,total:'9999999999999999.99',currency:'USD'}]}},['cycles'])");
  assert.match(h.element('#results').innerHTML, /USD 9,999,999,999,999,999\.99/);
});

test('worker layout rejects A-B-A and cancelled graph results', async () => {
  const workers = [];
  class SyntheticWorker {
    constructor() { workers.push(this); this.terminated = false; }
    postMessage(message) { this.message = message; }
    terminate() { this.terminated = true; }
    deliver(nodes) { this.onmessage({data:{requestId:this.message.requestId,result:{nodes}}}); }
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
  const old = workers[0];
  h.run('selectCase(2);selectCase(1)');
  old.deliver([{id:'SYN-A',x:999,y:999}]);
  assert.equal(old.terminated, true);
  assert.equal(h.run('layoutWrites.length'), 0);
  await h.run('relayout()');
  workers[1].deliver([{id:'SYN-A',x:101,y:202},{id:'SYN-B',x:303,y:404}]);
  assert.equal(h.run('layoutWrites.length'), 2);
  assert.equal(h.run("state.positions['SYN-A'].x"), 101);
  await h.run('relayout()'); h.run('cancelGraphLayout()');
  workers[2].deliver([{id:'SYN-A',x:999,y:999}]);
  assert.equal(h.run("state.positions['SYN-A'].x"), 101);
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
  assert.equal(h.run('layoutResult.nodes.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)&&p.x>=70&&p.x<=930&&p.y>=70&&p.y<=630)'), true);
  assert.ok(h.run('layoutResult.interactions') < 1000 * 999 * 120 / 2);
  assert.equal(h.run("JSON.stringify(FundsGraphLayout.seed('SYN-1',1000,700)) === JSON.stringify(FundsGraphLayout.seed('SYN-1',1000,700))"), true);
});


test('path controls are sent losslessly with the frozen run and report snapshot', async () => {
  const h = harness('analysis.js'); await flush();
  h.run("document.querySelectorAll('.alg')[0].value='paths'");
  h.element('#pSrc').value = '0009007199254740993'; h.element('#pDst').value = 'SYN-END'; h.element('#pHops').value = '6';
  h.network(() => response({paths:{count:0,paths:[]}}));
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
  const h = harness('analysis.js'); await flush();
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
  assert.ok(modes.includes('drag-node'));
  assert.equal(configuration.animate, false);
  assert.equal(configuration.defaultEdge.labelCfg.style.background.fill,'transparent');
});

test('first tracking into an initialized empty graph lays out once and preserves saved positions', () => {
  const h = harness('app.js');
  h.run(`globalThis.layoutCalls = 0; relayout = async () => { layoutCalls++; };
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

function installSyntheticHub(h, size = 1000) {
  const cents=1000000000000000001n+BigInt(size-2),outTotal=`${cents/100n}.${String(cents%100n).padStart(2,'0')}`;
  h.bindApp();
  h.element('#caseSelect').selectedOptions = [{ textContent: 'Synthetic A' }];
  h.element('#graphContainer').clientWidth = 1000; h.element('#graphContainer').clientHeight = 700;
  h.element('#neighborDirection').value = 'both';
  h.run(`globalThis.labelShapeWrites = 0; globalThis.createdLabelShapes=0;globalThis.graphPaints = 0;globalThis.imageReads=0;
    globalThis.syntheticZoom = 0.45;
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
    const makeShape = name => {createdLabelShapes++;return {visible:true,values:{},
      get(key){return key==='name'?name:this.values[key]},
      set(key,value){this.values[key]=value},
      show(){this.visible=true;labelShapeWrites++},hide(){this.visible=false;labelShapeWrites++},
      attr(key,value){if(typeof key==='object'){Object.assign(this.values,key);return}if(value!==undefined)this.values[key]=value;
        return this.values[key]??0}}};
    const makeItem = model => {const shapes=[];
      const group={visible:true,get(key){return key==='children'?shapes:key==='visible'?this.visible:undefined},
        show(){this.visible=true},hide(){this.visible=false}};
      const item={getModel:()=>model,shapes,getContainer:()=>group,
        syncLabel(){if(typeof model.label!=='string'||!model.label.length)shapes.length=0;
          else if(!shapes.length){const text=makeShape('text-shape'),background=makeShape('text-bg-shape');
            text.getCanvasBBox=background.getCanvasBBox=()=>typeof syntheticLabelBounds==='function'?syntheticLabelBounds(model):null;
            shapes.push(text,background)}}};
      item.syncLabel();return item};
    let graphNodes=[],graphEdges=[];
    const replace = data => {graphNodes=data.nodes.map(makeItem);graphEdges=data.edges.map(makeItem)};
    state.caseId=1;state.graphInited=false;state.forceFit=false;state.positions={};
    syntheticFullGraph.nodes.forEach((node,i)=>{state.positions[node.account]={x:80+i%100*8,y:80+Math.floor(i/100)*45}});
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

test('public neighborhood controls bound a 1000-account graph without changing tracked facts', async () => {
  const h = harness('app.js'); installSyntheticHub(h);
  const writes = h.requests.length;
  h.element('#neighborAccount').value = 'SYN-HUB';
  h.element('#btnNeighborhood').onclick();
  assert.equal(h.run('state.graph.getNodes().length'), 51);
  assert.equal(h.run('state.graph.getEdges().length'), 50);
  assert.equal(h.run('state.graphView.remaining'), 949);
  assert.equal(h.run('state.graphView.root'), 'SYN-HUB');
  assert.match(h.element('#graphViewStatus').textContent, /51\/1000 个账户/);
  assert.match(h.element('#graphViewStatus').textContent, /949 个邻居未展开/);
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
  assert.equal(h.run('state.graph.getEdges()[0].shapes[1].visible'), true);
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

test('absolute snapshot label coordinates survive restoration while real drag ratios take priority', () => {
  const h = harness('app.js'); installSyntheticHub(h);
  h.run(`selectItem(state.graph.getEdges()[0]);applyGraphLabels();
    globalThis.realRenderGraph=renderGraph;renderGraph=()=>{};
    applyCanvasSnapshot({nodes:[],edges:[{source:'SYN-HUB',target:'SYN-00000',lx:123.25,ly:98.5,bw:40,bh:10}]});
    renderGraph=realRenderGraph;state.forceRebuild=false;
    const edge=state.graph.getEdges()[0];edge.shapes[1].attr({width:40,height:10});
    edge.getKeyShape=()=>({attr:key=>key==='path'?[['M',0,0],['L',100,0]]:undefined});
    restoreEdgeLabelPositions();`);
  assert.equal(h.run('state.edgeLabelPos["SYN-HUB=>SYN-00000"]'), undefined);
  assert.equal(h.run('state.graph.getEdges()[0].shapes[0].attr("x")'), 123.25);
  assert.equal(h.run('state.graph.getEdges()[0].shapes[0].attr("y")'), 98.5);
  assert.equal(h.run('state.graph.getEdges()[0].shapes[1].attr("x")'), 103.25);
  assert.equal(h.run('state.graph.getEdges()[0].shapes[1].attr("y")'), 93.5);
  h.run('state.edgeLabelPos["SYN-HUB=>SYN-00000"]=0.25;restoreEdgeLabelPositions()');
  assert.equal(h.run('state.graph.getEdges()[0].shapes[0].attr("x")'), 25);
  assert.equal(h.run('state.graph.getEdges()[0].shapes[0].attr("y")'), 0);
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

function installCanvasControls(h) {
  h.run('globalThis.realCanvasBinder=bindCanvases;globalThis.realCanvasLoader=loadGraph');
  installSyntheticHub(h);
  h.run('loadGraph=realCanvasLoader;realCanvasBinder();state.canvasId=10;state.canvasName="Canvas A"');
}

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
  assert.equal(h.run('createdLabelShapes'), 40, '20 initial node labels, each with text and background');
  const remaining=h.run('[...state.graph.getNodes(),...state.graph.getEdges()].reduce((sum,item)=>sum+item.shapes.length,0)');
  assert.ok(remaining>0&&remaining<40,'the geometric collision pass removes overlapping labels after bounded initial creation');
  assert.equal(remaining,h.run('state.graphView.policy.nodeIds.size*2'));
  assert.equal(h.run('state.graph.getEdges().every(item=>item.getModel().label===undefined)'), true);
  h.element('#graphLabelMode').onchange({target:{value:'all'}});
  h.timers.findLast(timer=>timer.delay===0).callback();
  assert.equal(h.run('state.graph.getNodes().filter(item=>item.shapes.length===2).length'), 1000);
  assert.equal(h.run('state.graph.getEdges().filter(item=>item.shapes.length===2).length'), 999);
  assert.equal(h.run('state.graph.getEdges()[0].getModel().label'), '2笔·¥10,000,000,000,000,000.01\nUSD 0.01');
  h.element('#graphLabelMode').onchange({target:{value:'focus'}});
  h.timers.findLast(timer=>timer.delay===0).callback();
  assert.equal(h.run('[...state.graph.getNodes(),...state.graph.getEdges()].every(item=>item.shapes.length===0)'), true);
  h.run(`selectItem(state.graph.getNodes().find(item=>item.getModel().id==='SYN-00998'));applyGraphLabels()`);
  assert.equal(h.run('state.graph.getNodes().filter(item=>item.shapes.length===2).length'), 2);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===originalGraphFacts'), true);
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

test('panning away removes labels and panning back restores dragged label coordinates and exact canonical money', async () => {
  for (const saveFirst of [false,true]) {
    const h = harness('app.js'); installSyntheticHub(h);
    h.run(`selectItem(state.graph.getEdges()[0]);applyGraphLabels();
      state.graph.getEdges()[0].shapes[0].attr({x:123.25,y:98.5});
      state.graph.getEdges()[0].shapes[1].attr({x:103.25,y:93.5,width:40,height:10});${saveFirst?'savePositions();':''}
      syntheticViewport={minX:10000,minY:10000,maxX:11000,maxY:10700};applyGraphViewport(true)`);
    assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),0);
    assert.equal(h.run('state.graph.getEdges()[0].getContainer().get("visible")'),false);
    h.run('syntheticViewport={minX:0,minY:0,maxX:1000,maxY:700};applyGraphViewport(true)');
    assert.equal(h.run('state.graph.getEdges()[0].getContainer().get("visible")'),true);
    assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),2);
    assert.equal(h.run('state.graph.getEdges()[0].shapes[0].attr("x")'),123.25);
    assert.equal(h.run('state.graph.getEdges()[0].shapes[0].attr("y")'),98.5);
    const snapshot=await h.run('window.__collectSnapshot()');
    assert.equal(snapshot.edges[0].lx,123.25);assert.equal(snapshot.edges[0].ly,98.5);
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
    assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),2);
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

test('screen label collisions preserve required focus, explicit all and image export without changing exact facts', async () => {
  const h=harness('app.js');installSyntheticHub(h);
  h.run(`state.lastGraph={nodes:syntheticFullGraph.nodes.slice(0,3),edges:syntheticFullGraph.edges.slice(0,2),tracked_ids:[1,2,3]};
    state.graphView.fullIndex=FundsGraphView.createIndex(state.lastGraph);
    state.positions={'SYN-HUB':{x:500,y:350},'SYN-00000':{x:500,y:350},'SYN-00001':{x:500,y:350}};
    globalThis.collidingFacts=JSON.stringify(state.lastGraph);
    syntheticLabelBounds=()=>({minX:200,minY:200,maxX:300,maxY:240});renderGraph();`);
  const labelCount=()=>h.run('[...state.graph.getNodes(),...state.graph.getEdges()].filter(item=>item.shapes.length===2).length');
  assert.equal(labelCount(),1,'measured coincident bounds leave one ordinary label: '+JSON.stringify(h.run(`({
    nodes:[...state.graphView.policy.nodeIds],edges:[...state.graphView.policy.edgeKeys],
    items:[...state.graph.getNodes(),...state.graph.getEdges()].filter(item=>item.shapes.length===2).map(item=>item.getModel().id||edgeKey(item.getModel()))})`)));
  assert.equal(h.run('state.graphView.policy.nodeIds.has("SYN-HUB")'),true,'main node wins ordinary priority');
  h.run(`selectItem(state.graph.getEdges()[0]);state.graphView.hoveredAccount='SYN-00001'`);
  h.element('#graphLabelMode').onchange({target:{value:'focus'}});h.timers.findLast(timer=>timer.delay===0).callback();
  assert.equal(labelCount(),2,'required selected edge and hovered node survive even when they overlap');
  assert.equal(h.run('state.graphView.policy.edgeKeys.has("SYN-HUB=>SYN-00000")'),true);
  assert.equal(h.run('state.graphView.policy.nodeIds.has("SYN-00001")'),true);
  h.element('#graphLabelMode').onchange({target:{value:'all'}});h.timers.findLast(timer=>timer.delay===0).callback();
  assert.equal(labelCount(),5,'all explicitly retains three node and two edge labels');
  h.run('selectItem(null);state.graphView.hoveredAccount=null');
  h.element('#graphLabelMode').onchange({target:{value:'auto'}});h.timers.findLast(timer=>timer.delay===0).callback();
  assert.equal(labelCount(),1);
  const visible=await h.run('window.__collectSnapshot()'),full=await h.run('window.__collectSnapshot({scope:"all"})');
  for(const snapshot of [visible,full]) {
    assert.equal(snapshot.nodes.length,3);assert.equal(snapshot.edges.length,2);
    assert.equal(snapshot.edges[0].label,'2笔·¥10,000,000,000,000,000.01\nUSD 0.01');
    assert.equal(snapshot.edges[0].data.amount_totals[0].amount,'10000000000000000.01');
    assert.equal(snapshot.edges[0].data.amount_totals[1].amount,'0.01');
    assert.equal(snapshot.edges[0].data.amount_totals[1].currency,'USD');
  }
  assert.equal(visible.edges[0].labelVisible,false);assert.equal(full.edges[0].labelVisible,true);
  h.element('#exportFmt').value='png';const pending=h.element('#btnExportPng').onclick();
  assert.equal(labelCount(),5,'image export explicitly bypasses screen collision pruning');
  h.timers.findLast(timer=>timer.delay===400).callback();await pending;
  assert.equal(labelCount(),1,'automatic collision pruning resumes after image capture');
  assert.equal(h.downloads.length,1);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===collidingFacts'),true);
});

function installSavedLabelPair(h, { mode='auto', manualRatio=null }={}) {
  installSyntheticHub(h);
  h.run(`state.lastGraph={nodes:syntheticFullGraph.nodes.slice(0,2),edges:[syntheticFullGraph.edges[0]],tracked_ids:[1,2]};
    state.graphView.fullIndex=FundsGraphView.createIndex(state.lastGraph);state.graphView.labelMode='${mode}';
    state.positions={'SYN-HUB':{x:2700,y:2800},'SYN-00000':{x:3000,y:2800}};
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
      deliver(nodes){this.onmessage({data:{requestId:this.message.requestId,result:{nodes}}})}
    }
    const h=harness('app.js','http://127.0.0.1:8300/','1',{Worker:PairWorker});installSavedLabelPair(h,settings);
    await h.element('#btnRelayout').onclick();
    assert.equal(workers.length,1);
    workers[0].deliver([{id:'SYN-HUB',x:100,y:100},{id:'SYN-00000',x:300,y:100}]);
    const ratio=settings.manualRatio??71/300,expectedX=100+200*ratio;
    assert.ok(Math.abs(h.run('state.edgeLabelPos["SYN-HUB=>SYN-00000"]')-ratio)<1e-12);
    assert.ok(Math.abs(h.run('state.edgeSnapshotLabels["SYN-HUB=>SYN-00000"].lx')-expectedX)<1e-10);
    assert.equal(h.run('state.edgeSnapshotLabels["SYN-HUB=>SYN-00000"].ly'),100);
    if(settings.mode==='focus') assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),0);
    else {
      assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),2);
      assert.ok(Math.abs(h.run('state.graph.getEdges()[0].shapes[0].attr("x")')-expectedX)<1e-10);
      assert.equal(h.run('state.graph.getEdges()[0].shapes[0].attr("y")'),100);
    }
    await assertPairSnapshot(h,expectedX,100);
  }
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
  assert.deepEqual(Array.from(h.run('smallFit.zooms')),[1.1]);
  assert.equal(h.run('smallFit.unsafeFits'),0);
  assert.ok(Math.abs(h.run('state.graph.getCanvasByPoint(2000.5,2000).x')-500)<1e-10);
  assert.ok(Math.abs(h.run('state.graph.getCanvasByPoint(2000.5,2000).y')-350)<1e-10);
  h.run('fitWorldBounds={minX:-1e9,minY:-1e9,maxX:1e9,maxY:1e9}');h.element('#btnFit').onclick();
  assert.ok(Math.abs(h.run('smallFit.zooms.at(-1)')-0.02)<1e-12);
  assert.equal(h.run('smallFit.unsafeFits'),0);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===pairFacts'),true);
});

test('automatic short-edge labels avoid endpoint nodes while selected, all and image export retain exact amounts', async () => {
  const h=harness('app.js');installSyntheticHub(h);
  h.run(`state.lastGraph={nodes:syntheticFullGraph.nodes.slice(0,2),edges:[syntheticFullGraph.edges[0]],tracked_ids:[1,2]};
    state.graphView.fullIndex=FundsGraphView.createIndex(state.lastGraph);state.graphView.labelMode='auto';
    state.positions={'SYN-HUB':{x:450,y:350},'SYN-00000':{x:550,y:350}};
    syntheticLabelBounds=model=>model.source?{minX:300,minY:340,maxX:700,maxY:360}
      :{minX:model.x-10,minY:400,maxX:model.x+10,maxY:420};
    globalThis.endpointFacts=JSON.stringify(state.lastGraph);state.forceFit=false;renderGraph();`);
  assert.equal(h.run('state.graphView.policy.nodeIds.size'),2,'node text lies below the edge text and does not cause its rejection');
  assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),0,'endpoint center/radius fallback rejects the obstructed amount');
  h.run(`for(const node of state.graph.getNodes())node.getKeyShape=()=>({getCanvasBBox(){const m=node.getModel();
    return{minX:m.x-20,minY:m.y-20,maxX:m.x+20,maxY:m.y+20}}});`);
  h.element('#graphLabelMode').onchange({target:{value:'auto'}});h.timers.findLast(timer=>timer.delay===0).callback();
  assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),0,'actual endpoint circle bounds also reject the ordinary amount');
  const visible=await h.run('window.__collectSnapshot()'),full=await h.run('window.__collectSnapshot({scope:"all"})');
  for(const snapshot of [visible,full]) {
    assert.equal(snapshot.nodes.length,2);assert.equal(snapshot.edges.length,1);
    assert.equal(snapshot.edges[0].label,'2笔·¥10,000,000,000,000,000.01\nUSD 0.01');
    assert.equal(snapshot.edges[0].data.amount_totals[0].amount,'10000000000000000.01');
    assert.equal(snapshot.edges[0].data.amount_totals[1].amount,'0.01');
    assert.equal(snapshot.edges[0].data.amount_totals[1].currency,'USD');
  }
  assert.equal(visible.edges[0].labelVisible,false);assert.equal(full.edges[0].labelVisible,true);
  h.run('selectItem(state.graph.getEdges()[0])');h.timers.findLast(timer=>timer.delay===0).callback();
  assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),2,'selected edge is required despite endpoint overlap');
  assert.equal(h.run('state.graph.getEdges()[0].getModel().fundsLabel'),'2笔·¥10,000,000,000,000,000.01\nUSD 0.01');
  h.run('selectItem(null)');h.element('#graphLabelMode').onchange({target:{value:'all'}});h.timers.findLast(timer=>timer.delay===0).callback();
  assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),2,'all is an explicit endpoint-filter override');
  h.element('#graphLabelMode').onchange({target:{value:'auto'}});h.timers.findLast(timer=>timer.delay===0).callback();
  assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),0);
  h.element('#exportFmt').value='png';const pending=h.element('#btnExportPng').onclick();
  assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),2,'image capture bypasses endpoint filtering');
  h.timers.findLast(timer=>timer.delay===400).callback();await pending;
  assert.equal(h.run('state.graph.getEdges()[0].shapes.length'),0);
  assert.equal(h.downloads.length,1);
  assert.equal(h.run('JSON.stringify(state.lastGraph)===endpointFacts'),true);
});

test('named workspace dialogs keep actions reachable, close before actions, and restore keyboard focus', async () => {
  const h = harness('app.js'); await h.run('loadCases()');
  h.run(`globalThis.dialogEvents={};globalThis.dialogFocus=0;globalThis.dialogShows=0;
    const dialog=document.querySelector('#caseActions'),trigger=document.querySelector('#btnCaseActions');
    const closeButton=document.querySelector('#dialogClose'),contextLabel=document.querySelector('#dialogContext');
    trigger.dataset.actionDialog='caseActions';trigger.focus=()=>dialogFocus++;
    dialog.open=false;dialog.showModal=()=>{dialog.open=true;dialogShows++};
    dialog.close=()=>{dialog.open=false;dialogEvents.close?.()};
    dialog.addEventListener=(name,listener,capture)=>{dialogEvents[name]=listener;if(name==='click')globalThis.actionCapture=capture};
    dialog.querySelectorAll=selector=>selector==='[data-dialog-close]'?[closeButton]:selector==='[data-current-case]'?[contextLabel]:[];
    document.querySelectorAll=selector=>selector==='.action-dialog'?[dialog]:selector==='[data-action-dialog]'?[trigger]:[];
    state.canvasName='Synthetic saved canvas';bindWorkspaceActions();`);
  h.element('#btnCaseActions').onclick();
  assert.equal(h.element('#caseActions').open,true);
  assert.equal(h.element('#dialogContext').textContent,'当前案件：Synthetic A · 画布：Synthetic saved canvas');
  h.element('#btnCaseActions').onclick();
  assert.equal(h.run('dialogShows'),1,'repeated opening does not throw or duplicate a native dialog');
  assert.equal(h.run('actionCapture'),true,'the existing async action starts after the dialog is dismissed');
  h.run('dialogEvents.click({target:{closest:()=>({})}})');
  assert.equal(h.element('#caseActions').open,false);
  assert.equal(h.run('dialogFocus'),1);
  h.element('#btnCaseActions').onclick();h.element('#dialogClose').onclick();
  assert.equal(h.element('#caseActions').open,false);
  assert.equal(h.run('dialogFocus'),2,'explicit close restores the trigger like the native Escape close');
});

test('tracked Excel export downloads a fetched blob without a popup and retains the captured case and canvas', async () => {
  const h = harness('app.js');await h.run('loadCases()');h.bindApp();
  h.run(`state.canvasId=7;globalThis.exportNotices=[];toast=(...args)=>exportNotices.push(args);
    window.open=()=>{throw new Error('popups are unavailable')};`);
  h.network(() => response({}));
  await h.element('#btnExportXlsx').onclick();
  assert.equal(h.requests.at(-1).path,'/api/export/tracked.xlsx?case_id=1&canvas_id=7');
  assert.equal(h.downloads.length,1);
  assert.equal(h.downloads[0].filename,'资金追踪流水_案件1_画布7.xlsx');
  assert.equal(h.objectUrls[0].blob instanceof Blob,true);
  assert.match(h.run('exportNotices[0][0]'),/Excel 已开始下载/);
});

test('tracked Excel export rejects a delayed response or body after case and canvas revocation', async () => {
  const h = harness('app.js');await h.run('loadCases()');h.bindApp();
  h.run(`globalThis.exportNotices=[];toast=(...args)=>exportNotices.push(args);`);
  const lateResponse=deferred();let bodyReads=0;
  h.network(() => lateResponse.promise);
  const first=h.element('#btnExportXlsx').onclick();
  h.run('selectCase(2);selectCase(1)');
  lateResponse.resolve({...response({}),blob:async()=>{bodyReads++;return new Blob(['stale'])}});await first;
  assert.equal(bodyReads,0,'a revoked response is discarded before reading its body');
  const lateBody=deferred();h.network(() => ({...response({}),blob:()=>lateBody.promise}));
  const second=h.element('#btnExportXlsx').onclick();await flush();
  h.run('state.canvasGeneration+=1');lateBody.resolve(new Blob(['stale-canvas']));await second;
  assert.equal(h.downloads.length,0);assert.equal(h.objectUrls.length,0);
  assert.equal(h.run('exportNotices.length'),0,'revoked exports do not report success or an unrelated-case error');
});

test('tracked Excel export reports an HTTP failure and never downloads its body', async () => {
  const h = harness('app.js');await h.run('loadCases()');h.bindApp();
  h.run(`globalThis.exportNotices=[];toast=(...args)=>exportNotices.push(args);`);
  h.network(() => ({ok:false,status:503,blob(){throw new Error('error body must not download')}}));
  await h.element('#btnExportXlsx').onclick();
  assert.equal(h.downloads.length,0);
  assert.match(h.run('exportNotices[0][0]'),/Excel 导出失败.*HTTP 503/);
  assert.equal(h.run('exportNotices[0][1]'),'err');
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

test('photo-delayed save and clone abort after redraw or binding A-B-A changes', async () => {
  for (const action of ['#btnCanvasSave','#btnCanvasClone']) for (const transition of [
    "state.edgeType='line';renderGraph()",'selectCase(2);selectCase(1)','activateCanvas(20);activateCanvas(10)',
  ]) {
    const h=harness('app.js');installCanvasControls(h);
    h.run('state.lastGraph.nodes[0].photo_at=123;renderGraph()');
    const photo=deferred();h.network(path=>String(path).includes('account-photos')?photo.promise:response([]));
    const pending=h.element(action).onclick();await flush();
    assert.ok(h.requests.some(item=>String(item.path).includes('account-photos')));
    h.run(transition);photo.resolve(response({}));await pending;
    assert.equal(h.requests.filter(item=>item.options.method==='POST').length,0,`${action} must not persist an invalidated snapshot (${transition})`);
  }
});

test('saved mixed edge types, bends and label geometry survive redraw and full snapshot collection', async () => {
  const h=harness('app.js');installSyntheticHub(h,5);
  h.run(`globalThis.savedInitGraph=initGraph;initGraph=()=>{};
    state.lastGraph.edges.push({...state.lastGraph.edges[0],source:'SYN-HUB',target:'SYN-HUB'});
    applyCanvasSnapshot({nodes:state.graph.getNodes().map(item=>item.getModel()),edges:[
      {source:'SYN-HUB',target:'SYN-00000',type:'line',lx:123.25,ly:98.5,bw:40,bh:10},
      {source:'SYN-HUB',target:'SYN-00001',type:'polyline',controlPoints:[{x:7,y:8}]},
      {source:'SYN-HUB',target:'SYN-00002',type:'quadratic'},
      {source:'SYN-HUB',target:'SYN-HUB',type:'loop'}]});renderGraph();`);
  assert.equal(h.element('#edgeTypeSel').value, 'saved');
  let snapshot=await h.run('window.__collectSnapshot({scope:"all"})');
  assert.deepEqual(Array.from(snapshot.edges,edge=>edge.type),['line','polyline','quadratic','quadratic','loop']);
  assert.deepEqual(JSON.parse(JSON.stringify(snapshot.edges[1].controlPoints)),[{x:7,y:8}]);
  assert.equal(snapshot.edges[0].lx,123.25);assert.equal(snapshot.edges[0].label,'2笔·¥10,000,000,000,000,000.01\nUSD 0.01');
  h.element('#edgeTypeSel').onchange({target:{value:'line'}});
  assert.equal(h.element('#edgeTypeSel').value, 'line');
  snapshot=await h.run('window.__collectSnapshot({scope:"all"})');
  assert.deepEqual(Array.from(snapshot.edges,edge=>edge.type),['line','polyline','line','line','loop']);
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
    h.element('#exportFmt').value=formats[0];const a=h.element('#btnExportPng').onclick();
    h.element('#exportFmt').value=formats[1];const b=h.element('#btnExportPng').onclick();
    assert.equal(h.run('state.graphView.exportDepth'),1);
    assert.equal(h.timers.filter(timer=>timer.delay===400).length,1);
    await finishSample(h);await finishSample(h);await Promise.all([a,b]);
    assert.deepEqual(JSON.parse(h.run('JSON.stringify(sampled)')),formats.map(fmt=>fmt==='pdf'?[3000,2100]:[2000,1400]));
    assert.equal(h.run('state.graphView.exportDepth'),0);assert.equal(h.downloads.length,2);
    assert.deepEqual(JSON.parse(h.run('JSON.stringify(sampleSize)')),[1000,700]);
  }
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
  h.network(path=>response(path.startsWith('/api/main-accounts')?[]:{}));
  await h.run('showNodeDrawer("SYN-HUB")');
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
  h.element('#exportFmt').value='png';const a=h.element('#btnExportPng').onclick();
  h.element('#exportFmt').value='pdf';const b=h.element('#btnExportPng').onclick();
  const old=h.timers.findLast(timer=>timer.delay===400);
  h.run('renderGraph()');await flush();
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(sampleCamera)')),[1,0,0,0,1,0,12,15,1]);
  h.element('#exportFmt').value='png';const c=h.element('#btnExportPng').onclick();
  const before=h.run('sampleSizes.length');old.callback();assert.equal(h.run('sampleSizes.length'),before);
  await finishSample(h);await Promise.all([a,b,c]);
  assert.equal(h.downloads.length,1);assert.equal(h.run('state.graphView.exportDepth'),0);
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(sampleCamera)')),[1,0,0,0,1,0,12,15,1]);
});

test('full neighborhood snapshots retain saved line types for edges outside the current view', async () => {
  const h=harness('app.js');installSyntheticHub(h,120);
  h.run(`initGraph=()=>{};applyCanvasSnapshot({nodes:[],edges:[
    {source:'SYN-HUB',target:'SYN-00118',type:'polyline',controlPoints:[{x:23,y:45}],lx:44,ly:55,bw:20,bh:10}]});
    exploreNeighborhood('SYN-HUB',true)`);
  const snapshot=await h.run('window.__collectSnapshot({scope:"all"})');
  const edge=snapshot.edges.find(edge=>edge.target==='SYN-00118');
  assert.equal(edge.type,'polyline');assert.equal(edge.lx,44);
  assert.deepEqual(JSON.parse(JSON.stringify(edge.controlPoints)),[{x:23,y:45}]);
});

test('a rejected PDF request cannot publish an error after its binding is revoked', async () => {
  const h=harness('app.js');samplingGraph(h);
  h.run('globalThis.exportNotices=[];toast=(...args)=>exportNotices.push(args)');
  let rejectPdf;h.network(()=>new Promise((_,reject)=>{rejectPdf=reject}));
  h.element('#exportFmt').value='pdf';const pending=h.element('#btnExportPng').onclick();await finishSample(h);
  h.run('selectCase(2);selectCase(1)');rejectPdf(new Error('synthetic expired request'));await pending;
  assert.equal(h.downloads.length,0);assert.equal(h.run('exportNotices.length'),0);
});
