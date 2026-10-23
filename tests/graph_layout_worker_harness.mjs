import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const filenames = ['graph-layout.js', 'graph-layout-quality.js', 'graph-routing.js'];
const sources = new Map(filenames.map(name => [name, readFileSync(new URL(`../static/${name}`, import.meta.url), 'utf8')]));
const adapter = readFileSync(new URL('../static/graph-layout-worker.js', import.meta.url), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
function createWorker({ failImportOnce } = {}) {
  const messages = [], imports = [], importBatches = [], context = vm.createContext({});
  let pendingFailure = failImportOnce;
  context.self = context;
  context.postMessage = value => messages.push(plain(value));
  context.importScripts = (...urls) => {
    importBatches.push([...urls]);
    for (const url of urls) {
      imports.push(url);
      const filename = new URL(url, 'http://synthetic.invalid').pathname.split('/').pop();
      assert.ok(sources.has(filename), `unexpected Worker import ${url}`);
      if (filename === pendingFailure) {
        pendingFailure = undefined;
        throw new Error(`synthetic import failure: ${filename}`);
      }
      vm.runInContext(sources.get(filename), context, { filename, timeout: 5000 });
    }
  };
  vm.runInContext(adapter, context, { filename: 'graph-layout-worker.js' });
  return { context, imports, importBatches, send(data) {
    const before = messages.length;
    context.onmessage({ data });
    assert.equal(messages.length, before + 1, 'each task sends exactly one terminal response');
    return messages.at(-1);
  } };
}
const direct = vm.createContext({});
for (const name of filenames) vm.runInContext(sources.get(name), direct, { filename: name });
function layoutDirect(input) {
  const model = direct.FundsGraphLayout.simulation(input);
  while (model.step()) {}
  const result = model.result();
  return plain({ result, quality: direct.FundsGraphLayoutQuality.evaluate(input, result,
    { fitViewport: direct.FundsGraphLayout.fitViewport }) });
}
function layoutInput(kind, shape) {
  const nodes = Array.from({ length: 18 }, (_, i) => ({ id: `SYN-${String(i).padStart(3, '0')}`, size: 36,
    width: 110 + i % 3 * 12, height: 78, offsetY: 22, fontSize: 13, labelGap: 6, labelHeight: 34,
    x: i % 6 * 140, y: Math.floor(i / 6) * 130 }));
  let edges;
  if (shape === 'star') edges = nodes.slice(1).map(node => ({ source: nodes[0].id, target: node.id }));
  else if (shape === 'disconnected') edges = nodes.filter((_, i) => i % 3 === 0).map((node, i) => ({ source: node.id, target: nodes[i * 3 + 1].id }));
  else edges = nodes.slice(shape === 'cycle' ? 0 : 1).map((node, i) => ({ source: nodes[i].id,
    target: nodes[(i + 1) % nodes.length].id }));
  return { kind, width: 920, height: 640, maxZoom: 1.6,
    chromeObstacles: [{ bounds: { minX: 720, minY: 470, maxX: 920, maxY: 640 } }], nodes,
    edges: edges.map((edge, i) => ({ ...edge, id: `SYN-E${i}`, labelWidth: 120, labelHeight: 18, fontSize: 13,
      amount: '99999999999999999999.99', currency: i % 2 ? 'USD' : 'CNY' })) };
}
const node = (id, x, y) => ({ id, x, y, body: { minX: x - 18, minY: y - 18, maxX: x + 18, maxY: y + 18 },
  label: { minX: x - 54, minY: y + 24, maxX: x + 54, maxY: y + 58 } });
const edge = (source, target, extra = {}) => ({ id: `${source}=>${target}`, source, target,
  type: 'quadratic', labelWidth: 100, labelHeight: 18, amount: '123456789012345678.90', currency: 'CNY', ...extra });
function routeInput() {
  return { nodes: [node('A', 0, 0), node('B', 360, 0), node('C', 180, 0), node('D', 180, 160)],
    edges: [edge('A', 'B'), edge('B', 'A'), edge('A', 'D', { manual: true, labelRatio: .2, controlPoints: [{ x: -80, y: 140 }] }),
      edge('C', 'C', { type: 'loop' }), edge('C', 'D', { type: 'line' })], maxChecks: 120000 };
}

test('route-only Worker loads routing on demand without loading layout or quality', () => {
  const worker = createWorker();
  assert.deepEqual(worker.imports, []);
  const response = worker.send({ protocol: 1, requestId: 1, taskId: 2, task: 'routes', input: routeInput() });
  assert.ok(response.proposal);
  assert.equal(response.result, undefined);
  worker.send({ protocol: 1, requestId: 1, taskId: 3, task: 'routes', input: routeInput() });
  assert.deepEqual(worker.imports, ['/static/graph-routing.js?v=compact-arcs-1']);
  assert.deepEqual(worker.importBatches, [worker.imports]);
});

test('layout-only Worker loads layout and quality once without loading routing', () => {
  const worker = createWorker(), handler = worker.context.onmessage;
  assert.deepEqual(worker.imports, []);
  for (const taskId of [1, 2]) {
    const response = worker.send({ protocol: 1, requestId: 2, taskId, task: 'layout', input: layoutInput('dagre', 'chain') });
    assert.equal(response.quality.valid, true);
    assert.equal(worker.context.onmessage, handler, 'lazy import cannot leave the legacy layout-only handler installed');
  }
  assert.deepEqual(worker.imports, ['/static/graph-layout.js?v=canvas-display-1',
    '/static/graph-layout-quality.js?v=canvas-display-1']);
  assert.deepEqual(worker.importBatches, [worker.imports]);
});

test('layout can preload routing in one import batch for an equivalent later route task', () => {
  const worker = createWorker(), input = layoutInput('dagre', 'chain'), handler = worker.context.onmessage;
  const layout = worker.send({ protocol: 1, requestId: 'preloaded', taskId: 1, task: 'layout', input, prepareRoutes: true });
  assert.deepEqual(layout, { protocol: 1, requestId: 'preloaded', taskId: 1, task: 'layout', ...layoutDirect(input) });
  const routeGraph = routeInput(), before = JSON.stringify([input, routeGraph]);
  const routes = worker.send({ protocol: 1, requestId: 'preloaded', taskId: 2, task: 'routes', input: routeGraph });
  assert.deepEqual(routes, { protocol: 1, requestId: 'preloaded', taskId: 2, task: 'routes',
    proposal: plain(direct.FundsGraphRouting.plan(routeGraph)) });
  const again = worker.send({ protocol: 1, requestId: 'preloaded', taskId: 3, task: 'layout', input, prepareRoutes: true });
  assert.deepEqual(again.result, layout.result);
  assert.deepEqual(again.quality, layout.quality);
  assert.deepEqual(worker.imports, ['/static/graph-layout.js?v=canvas-display-1',
    '/static/graph-layout-quality.js?v=canvas-display-1', '/static/graph-routing.js?v=compact-arcs-1']);
  assert.deepEqual(worker.importBatches, [worker.imports]);
  assert.equal(worker.context.onmessage, handler);
  assert.equal(JSON.stringify([input, routeGraph]), before);
});

test('five existing layouts retain exact results, complete quality, input order and facts', () => {
  const worker = createWorker();
  for (const [i, kind] of ['auto', 'dagre', 'partition', 'structural', 'force'].entries()) {
    const input = layoutInput(kind, ['chain', 'cycle', 'disconnected', 'star', 'cycle'][i]);
    const before = JSON.stringify(input), expected = layoutDirect(input);
    const response = worker.send({ protocol: 1, requestId: 'layout-job', taskId: i, task: 'layout', input });
    assert.deepEqual(response, { protocol: 1, requestId: 'layout-job', taskId: i, task: 'layout', ...expected });
    assert.equal(JSON.stringify(input), before);
    assert.deepEqual(response.result.nodes.map(item => item.id), input.nodes.map(item => item.id));
    assert.equal(response.quality.valid, true);
    assert.equal(response.quality.fit.method, 'host-viewport-estimate');
  }
});

test('routing retains complete proposals and stats for reverse, manual, self, simple and partial-budget cases', () => {
  const worker = createWorker();
  const cases = [routeInput(), { ...routeInput(), simple: true }, { ...routeInput(), maxChecks: 1 },
    { ...routeInput(), conservative: true, routingBounds: { minX: -100, minY: -140, maxX: 460, maxY: 260 } }];
  for (const [i, input] of cases.entries()) {
    const before = JSON.stringify(input), expected = plain(direct.FundsGraphRouting.plan(input));
    const response = worker.send({ protocol: 1, requestId: 3, taskId: `route-${i}`, task: 'routes', input });
    assert.deepEqual(response, { protocol: 1, requestId: 3, taskId: `route-${i}`, task: 'routes', proposal: expected });
    assert.equal(JSON.stringify(input), before);
    assert.ok(response.proposal.routes.every(route => !['A=>D', 'C=>C', 'C=>D'].includes(route.id)));
  }
  assert.equal(cases[2].maxChecks, 1);
  assert.equal(direct.FundsGraphRouting.plan(cases[2]).stats.limited, true);
});

test('one Worker accepts serial mixed tasks without stale results or shared input mutation', () => {
  const worker = createWorker(), input = layoutInput('dagre', 'chain'), handler = worker.context.onmessage;
  const first = worker.send({ protocol: 1, requestId: 4, taskId: 1, task: 'layout', input });
  assert.equal(worker.context.onmessage, handler);
  const middle = worker.send({ protocol: 1, requestId: 4, taskId: 2, task: 'routes', input: routeInput() });
  const last = worker.send({ protocol: 1, requestId: 4, taskId: 3, task: 'layout', input });
  assert.equal(middle.taskId, 2);
  assert.deepEqual(last.result, first.result);
  assert.deepEqual(last.quality, first.quality);
  assert.equal(worker.context.onmessage, handler);
  assert.deepEqual(worker.imports, ['/static/graph-layout.js?v=canvas-display-1',
    '/static/graph-layout-quality.js?v=canvas-display-1', '/static/graph-routing.js?v=compact-arcs-1']);
});

test('failed lazy import restores the protocol handler and retries only the missing kernel', () => {
  const worker = createWorker({ failImportOnce: 'graph-layout-quality.js' }), handler = worker.context.onmessage;
  const request = { protocol: 1, requestId: 'import-recovery', taskId: 1, task: 'layout', input: layoutInput('dagre', 'chain') };
  const failed = worker.send(request);
  assert.equal(failed.error, 'synthetic import failure: graph-layout-quality.js');
  assert.equal(worker.context.onmessage, handler);
  const route = worker.send({ ...request, taskId: 2, task: 'routes', input: routeInput() });
  assert.deepEqual(route.proposal, plain(direct.FundsGraphRouting.plan(routeInput())));
  const recovered = worker.send({ ...request, taskId: 3 });
  assert.deepEqual({ result: recovered.result, quality: recovered.quality }, layoutDirect(request.input));
  assert.equal(worker.context.onmessage, handler);
  assert.deepEqual(worker.imports, ['/static/graph-layout.js?v=canvas-display-1',
    '/static/graph-layout-quality.js?v=canvas-display-1', '/static/graph-routing.js?v=compact-arcs-1',
    '/static/graph-layout-quality.js?v=canvas-display-1']);
});

test('malformed protocols, tasks, inputs and kernel exceptions always receive a terminal error', () => {
  const worker = createWorker(), base = { protocol: 1, requestId: 5, taskId: 1, task: 'layout', input: layoutInput('auto', 'chain') };
  for (const request of [null, [], 'invalid', {}, { ...base, protocol: 0 }, { ...base, task: 'unknown' },
    { ...base, requestId: undefined }, { ...base, taskId: NaN }, { ...base, input: null },
    { ...base, input: { nodes: [], edges: {} } }, { ...base, input: { nodes: [null], edges: [] } }]) {
    const response = worker.send(request);
    assert.equal(typeof response.error, 'string');
    assert.ok(response.error.length > 0);
    assert.equal(response.result, undefined);
    assert.equal(response.quality, undefined);
    assert.equal(response.proposal, undefined);
    if (request && typeof request === 'object' && !Array.isArray(request)) {
      assert.equal(response.protocol, request.protocol);
      assert.equal(response.requestId, request.requestId);
      assert.equal(response.task, request.task);
    }
  }
  assert.equal(worker.send(base).quality.valid, true, 'a failed task does not poison subsequent jobs');
});
