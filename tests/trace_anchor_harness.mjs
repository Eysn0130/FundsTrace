import assert from 'node:assert/strict';
import test from 'node:test';
import { harness } from './legacy_navigation_harness.mjs';

const response = (body, status = 200) => ({ ok: status === 200, status,
  headers: { get: () => 'application/json' }, json: async () => body, text: async () => JSON.stringify(body) });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const tick = () => new Promise(resolve => setImmediate(resolve));
const ids = ['9007199254740993', '9007199254740994'];
const flow = (id, observations = [id]) => ({ id, observation_ids: observations, trade_time: '2026-01-01 12:00:00.123456',
  payer_account: 'SYN-A', payee_account: 'SYN-B', payer_name: '合成甲', payee_name: '合成乙',
  amount: '9999999999999999.99', currency: 'CNY', summary: '合成流水' });

function fixture(relationIds = ids, extra = {}) {
  const h = harness('app.js'), env = h.run('globalThis'), state = h.run('state'), calls = [], notices = [];
  const element = tag => {
    const attributes = new Map(), listeners = new Map(), query = new Map();
    const node = { tagName: tag.toUpperCase(), children: [], style: {}, disabled: false, hidden: false, checked: false,
      innerHTML: '', textContent: '', open: false, classList: { add() {}, remove() {} },
      setAttribute(name, value) { attributes.set(name, String(value)); },
      appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
      replaceChildren(...children) { this.children = children; },
      remove() { this.removed = true; if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(child => child !== this); },
      querySelector(selector) { if (!query.has(selector)) query.set(selector, element(selector === 'tbody' ? 'tbody' : selector.includes('prev') || selector.includes('next') || selector.includes('cancel') || selector.includes('submit') || selector.includes('retry') ? 'button' : 'span')); return query.get(selector); },
      addEventListener(type, callback) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(callback); },
      removeEventListener(type, callback) { listeners.set(type, (listeners.get(type) || []).filter(value => value !== callback)); },
      emit(type, event = {}) { for (const callback of listeners.get(type) || []) callback(event); },
      showModal() { this.open = true; }, close() { this.open = false; this.emit('close'); },
      focus() { env.document.activeElement = this; }, closest(selector) { return this.tagName === 'INPUT' && selector.includes('input') ? this : null; } };
    return node;
  };
  env.document.createElement = element; env.document.body = element('body'); env.AbortController = AbortController;
  h.element('#graphContainer').focus = () => { env.document.activeElement = h.element('#graphContainer'); };
  const model = { id: 'SYN-EDGE', source: 'SYN-A', target: 'SYN-B', data: { ids: relationIds, ...extra } };
  const item = { getModel: () => model };
  const graph = { getNodes: () => [], getEdges: () => [item], clear() {}, paint() {} };
  state.caseId = 1; state.graph = graph; state.graphLoading = false;
  state.graphView.edgeItems.set('SYN-A=>SYN-B', item);
  env.traceItem = item;
  env.window.FundsCanvasEditor = { history: { status: () => ({ busy: false }) }, traceAnchors(values, options) {
    calls.push({ ids: [...values], options }); return Promise.resolve(true);
  } };
  env.toast = (message, type) => notices.push({ message, type });
  h.network(async () => response({ rows: [flow(ids[0]), flow(ids[1])], total: 2, analysis_revision: 'SYN-REVISION' }));
  const open = direction => h.run(`traceEdgeDirection(globalThis.traceItem, '${direction}')`);
  const workspace = () => h.run('edgeTraceSelection');
  return { h, env, state, graph, item, calls, notices, open, workspace };
}

test('one raw flow traces directly with its exact ID and requested direction', async () => {
  const f = fixture([ids[0]]);
  assert.equal(await f.open('source'), true);
  assert.deepEqual(f.calls[0].ids, [ids[0]]); assert.equal(f.calls[0].options.direction, 'source');
  assert.equal(f.calls[0].options.x, 5); assert.equal(f.workspace(), null); assert.equal(f.h.requests.length, 0);
});

test('aggregate relations require explicit anchor selection even for one confirmed event', async () => {
  const f = fixture(ids, { funds: { count: { lower: 1, upper: 1 } } });
  f.h.network(async () => response({ rows: [flow(ids[0], ids)], total: 1 }));
  await f.open('target'); const w = f.workspace();
  assert.ok(w.dialog.open); assert.equal(w.selected.size, 0); assert.equal(w.submit.disabled, true);
  assert.equal(f.calls.length, 0); w.cancel.onclick();
  assert.equal(f.workspace(), null); assert.equal(w.dialog.removed, true); assert.equal(f.calls.length, 0);
  assert.equal(f.env.document.activeElement,f.h.element('#graphContainer'));
});

test('selecting text in a picker row does not change anchor selection',async()=>{
  const f=fixture();await f.open('source');const w=f.workspace();
  f.env.window.getSelection=()=>({toString:()=> 'SYN-A'});
  w.tbody.children[0].onclick({target:{closest:()=>null}});
  assert.equal(w.boxes[0].checked,false);assert.equal(w.selected.size,0);w.cancel.onclick();
});

test('the anchor table horizontal controls scroll overflow and dispose on cancel', async () => {
  const f = fixture(); await f.open('source'); const w = f.workspace();
  const viewport = w.dialog.querySelector('.trace-anchor-table-scroll');
  const range = w.dialog.querySelector('[data-trace-position]');
  const right = w.dialog.querySelector('[data-trace-right]');
  viewport.clientWidth = 240; viewport.scrollWidth = 600; viewport.scrollLeft = 0;
  w.scroll.sync(); assert.equal(range.disabled, false);
  right.onclick(); assert.equal(viewport.scrollLeft, 168);
  range.value = '360'; range.oninput(); assert.equal(viewport.scrollLeft, 360);
  assert.equal(right.disabled, true);
  w.cancel.onclick(); assert.equal(range.oninput, null); assert.equal(right.onclick, null);
});

test('only selected relationship observations submit once and preserve large string IDs', async () => {
  const f = fixture(); f.h.network(async () => response({ rows: [flow(ids[0], ids)], total: 1 }));
  await f.open('source'); const w = f.workspace(), gate = deferred();
  f.env.window.FundsCanvasEditor.traceAnchors = (values, options) => { f.calls.push({ ids: [...values], options }); return gate.promise; };
  w.boxes[0].checked = true; w.boxes[0].onchange();
  const first = w.submit.onclick(), second = w.submit.onclick();
  assert.equal(f.calls.length, 1); assert.deepEqual(f.calls[0].ids, ids);
  assert.equal(f.calls[0].options.direction, 'source'); assert.equal(w.cancel.disabled, true);
  gate.resolve(true); await Promise.all([first, second]); assert.equal(f.workspace(), null);
});

test('relationship pagination remains scoped and retains explicitly selected rows', async () => {
  const scoped = Array.from({ length: 51 }, (_, i) => String(9007199254740993n + BigInt(i))), f = fixture(scoped);
  f.h.network(async (path, options) => {
    assert.equal(path, '/api/flows/by-ids'); const body = JSON.parse(options.body);
    assert.deepEqual(body.flow_ids, scoped); assert.equal(body.size, 50); assert.equal(body.case_id, 1);
    return response({ rows: body.page === 1 ? scoped.slice(0, 50).map(id => flow(id)) : [flow(scoped[50])], total: 51, analysis_revision: 'SYN-SAME' });
  });
  await f.open('target'); const w = f.workspace(); w.boxes[0].checked = true; w.boxes[0].onchange();
  w.next.onclick(); await tick(); assert.equal(w.page, 2); assert.equal(w.boxes.length, 1);
  w.boxes[0].checked = true; w.boxes[0].onchange(); await w.submit.onclick();
  assert.deepEqual(f.calls[0].ids, [scoped[0], scoped[50]]);
});

test('a foreign response is refused and cannot become a trace selection', async () => {
  const f = fixture(); f.h.network(async () => response({ rows: [flow('9007199254740995')], total: 1 }));
  await f.open('target'); const w = f.workspace();
  assert.equal(w.phase, 'error'); assert.match(w.status.textContent, /不属于当前关系/);
  assert.equal(w.submit.disabled, true); assert.equal(f.calls.length, 0); w.cancel.onclick();
});

test('a closed or changed binding clears the old dialog before a delayed read returns', async () => {
  const f = fixture(), gate = deferred(); f.h.network(() => gate.promise);
  const old = f.open('source'), w = f.workspace();
  f.h.run('selectCase(2);selectCase(1)');
  assert.equal(f.workspace(), null); assert.equal(w.dialog.removed, true); assert.equal(w.selected.size, 0);
  gate.resolve(response({ rows: [flow(ids[0])], total: 1 })); await old;
  assert.equal(f.workspace(), null); assert.equal(f.calls.length, 0);
});

test('source-ledger relationships are explained and never sent for funds tracking', async () => {
  const f = fixture([ids[0]], { kind: 'source_ledger' });
  assert.equal(await f.open('target'), false); assert.equal(f.calls.length, 0); assert.equal(f.workspace(), null);
  assert.match(f.notices[0].message, /资金影响.*不能据此追踪/);
});

test('source-ledger observations in a mixed relation remain visible but cannot be chosen for tracing',async()=>{
  const f=fixture();f.h.network(async()=>response({rows:[{...flow(ids[0]),source_ledger:{economic_effect:'unknown'}},flow(ids[1])],total:2}));
  await f.open('source');const w=f.workspace();assert.equal(w.boxes[0].disabled,true);assert.equal(w.boxes[1].disabled,false);
  w.boxes[0].checked=true;w.boxes[0].onchange();assert.equal(w.selected.size,0);assert.equal(f.calls.length,0);
  w.boxes[1].checked=true;w.boxes[1].onchange();await w.submit.onclick();
  assert.deepEqual(f.calls[0].ids,[ids[1]]);
});

test('a failed relationship read stays local, can retry, and never tracks implicitly',async()=>{
  const f=fixture();let reads=0;
  f.h.network(async()=>++reads===1?response({detail:'合成读取失败'},503):response({rows:[flow(ids[0])],total:1}));
  await f.open('source');const w=f.workspace();assert.equal(w.phase,'error');assert.match(w.status.textContent,/合成读取失败/);
  assert.equal(w.retry.hidden,false);assert.equal(w.submit.disabled,true);assert.equal(f.notices.length,0);
  w.retry.onclick();await tick();assert.equal(w.phase,'ready');assert.equal(w.boxes.length,1);assert.equal(w.selected.size,0);
  assert.equal(f.calls.length,0);w.cancel.onclick();
});

for (const mode of ['export', 'drag']) test(`trace entry refuses ${mode} while its scene is owned by another operation`,async()=>{
  const f=fixture([ids[0]]);
  if(mode==='export')f.state.graphView.exportDepth=1;
  else f.state.graphSelection={status:()=>({dragging:true})};
  assert.equal(await f.open('target'),false);assert.equal(f.calls.length,0);assert.equal(f.workspace(),null);
});
