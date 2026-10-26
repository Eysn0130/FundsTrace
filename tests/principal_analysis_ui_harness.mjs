import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/principal-analysis.js', import.meta.url), 'utf8');
const page = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const flush = async () => { for (let index = 0; index < 4; index++) await new Promise(resolve => setImmediate(resolve)); };
const sourceId = '9007199254740993', targetId = '9007199254740995';

function fixture() {
  const elements = new Map(), listeners = new Map(), calls = [], downloads = [], urls = [], timers = [];
  class Element {
    constructor(tag = 'div', id = '') {
      this.tagName = tag.toUpperCase(); this.id = id; this.children = []; this.parentElement = null;
      this.value = ''; this.checked = false; this.disabled = false; this.open = false; this.hidden = false;
      this.dataset = {}; this.style = {}; this.attributes = {}; this.listeners = new Map(); this.content = '';
      if (id) elements.set(id, this);
    }
    append(...children) { for (const child of children) { child.parentElement = this; this.children.push(child); } }
    replaceChildren(...children) { this.children = []; this.content = ''; this.append(...children); }
    set textContent(value) { this.children = []; this.content = String(value); }
    get textContent() { return this.content + this.children.map(child => child.textContent).join(''); }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(type, listener) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(listener); }
    querySelectorAll(selector) {
      const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
      if (selector === '*') return descendants;
      if (selector === 'input:checked') return descendants.filter(child => child.tagName === 'INPUT' && child.checked);
      if (selector === 'input') return descendants.filter(child => child.tagName === 'INPUT');
      if (selector === 'button') return descendants.filter(child => child.tagName === 'BUTTON');
      if (selector === 'tr[data-flow-id]') return descendants.filter(child => child.tagName === 'TR' && child.dataset.flowId);
      return [];
    }
    showModal() { this.open = true; }
    close() { if (!this.open) return; this.open = false; for (const listener of this.listeners.get('close') || []) listener({ target: this }); }
    focus() { this.focused = true; }
    scrollIntoView() { this.scrolled = true; }
    remove() { this.parentElement.children = this.parentElement.children.filter(child => child !== this); this.parentElement = null; }
    async dispatch(type, extra = {}) { await Promise.all((this.listeners.get(type) || []).map(listener => listener({ target: this, preventDefault() {}, ...extra }))); }
    click() { if (this.disabled) return Promise.resolve(); if (this.tagName === 'A') { downloads.push({ href: this.href, filename: this.download }); return; } return this.dispatch('click'); }
  }
  const el = id => elements.get(id) || new Element('div', id);
  for (const id of ['ctxPrincipal', 'ctxModal', 'ctxBook', 'ctxTbody', 'principalDialog', 'principalClose', 'principalForm',
    'principalContext', 'principalStart', 'principalEnd', 'principalOpening', 'principalBalanceKind', 'principalCoverage',
    'principalNoOverdraft', 'principalFundsAvailable', 'principalTargets', 'principalRun', 'principalExport', 'principalStatus', 'principalResults',
    'principalNetwork','principalNetworkEnabled','principalBoundary','principalAccountSearch','principalAccountChoices',
    'principalAccountConditions','principalNetworkTargets']) el(id);
  el('ctxModal').style.display = 'flex';
  const selected = new Element('input'); selected.dataset.id = targetId; selected.checked = true;
  const targetRow = new Element('tr'); targetRow.dataset.flowId = targetId; el('ctxTbody').append(targetRow);
  const data = { account: 'SYN-B', anchor: { id: sourceId, payee_account: 'SYN-B', payer_account: 'SYN-A', self_account: 'SYN-B',
    currency: 'CNY', amount: '9999999999999999.99', trade_time: '2026-01-01 10:00:00' }, rows: [
    { id: targetId, dir: 'out', self_account: 'SYN-B', currency: 'CNY', amount: '9999999999999999.98', trade_time: '2026-01-01 11:00:00', payee_name: '<script>Synthetic</script>' },
    { id: '9007199254740996', dir: 'out', self_account: 'SYN-C', currency: 'CNY', amount: '4.00' },
    { id: '9007199254740997', dir: 'out', self_account: 'SYN-B', currency: 'USD', amount: '5.00' },
    { id: '9007199254740998', dir: 'in', self_account: 'SYN-B', currency: 'CNY', amount: '6.00' },
  ] };
  const state = { caseId: 1, canvasId: 0, caseGeneration: 1, canvasGeneration: 1, graphLoading: false,
    lastGraph: { nodes: [{ account: 'SYN-B' }, {account:'fte_synthetic_c',display_account:'SYN-C-DISPLAY',name:'Synthetic C'}], edges: [{ amount_totals: [{ currency: 'CNY', amount: '9999999999999999.99' }] }] } };
  const context = () => ({ caseId: state.caseId, canvasId: state.canvasId, generation: state.caseGeneration, canvasGeneration: state.canvasGeneration });
  state.ctx = { context: context(), phase: 'ready', closed: false, book: 'all', data };
  const document = { readyState: 'complete', getElementById: id => elements.get(id), createElement: tag => new Element(tag), body: new Element('body'),
    querySelectorAll: selector => selector === '#ctxTbody .ctx-ck:checked' && selected.checked ? [selected] : [] };
  const window = { addEventListener(type, listener) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(listener); } };
  const env = { document, window, state, AbortController, caseContext: context,
    isCurrentCase: candidate => JSON.stringify(candidate) === JSON.stringify(context()),
    jsonOpts: request => ({ headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request) }),
    syncContextSelection() {}, renderCtxRows() {},
    loadContext() { state.ctx = { ...state.ctx, phase: 'loading', data: null }; },
    closeContext() { state.ctx = { ...state.ctx, closed: true }; el('ctxModal').style.display = 'none'; },
    selectCase(id) { state.caseId = id; state.caseGeneration++; },
    activateCanvas(id) { state.canvasId = id; state.canvasGeneration++; },
    readGraphData() { return Promise.resolve(null); },
    URL: { createObjectURL(blob) { urls.push(blob); return `blob:synthetic-${urls.length}`; }, revokeObjectURL() {} },
    setTimeout: fn => timers.push(fn),
    fetch(path, options) { const pending = deferred(); calls.push({ path, options, body: JSON.parse(options.body), ...pending }); return pending.promise; },
  };
  vm.createContext(env); vm.runInContext(source, env, { filename: 'principal-analysis.js' });
  const result = overrides => ({ case_id: '1', account: 'SYN-B', currency: 'CNY', status: 'ok', revision: 'synthetic-revision', ledger_count: 3,
    targets: [{ id: targetId, amount: '9999999999999999.98', min: '9999999999999900.01', max: '9999999999999999.98', fifo: '9999999999999990.02' }], ...overrides });
  return { env, state, data, el, calls, downloads, urls, targetRow, result,
    open: () => el('ctxPrincipal').click(), submit: () => el('principalForm').dispatch('submit'),
    fill() { el('principalStart').value = '2026-01-01T00:00'; el('principalEnd').value = '2026-01-01T23:59'; },
    respond(index, payload = result(), status = 200, blob = {}) { calls[index].resolve({ ok: status === 200, status, json: async () => clone(payload), blob: async () => blob }); },
    emit(type) { for (const listener of listeners.get(type) || []) listener(); },
  };
}

test('markup preserves context table and puts a labelled native dialog within the active context modal', () => {
  const dialog = page.indexOf('id="principalDialog"'), context = page.indexOf('id="ctxModal"');
  assert(dialog > context && dialog < page.indexOf('id="textInputModal"'));
  assert.match(page, /id="principalDialog"[^>]*aria-labelledby="principalTitle"/);
  assert.match(page, /id="principalOpening"[^>]*type="text"/);
  assert(!/id="principal(?:Coverage|NoOverdraft|FundsAvailable)"[^>]*\bchecked/.test(page));
  assert.match(page, /id="principalNoOverdraft"[^>]*>我声明无透支或授信/);
  assert.match(page, /id="principalFundsAvailable"[^>]*>我已明确资金全额可用（无未建模冻结或在途资金）/);
  assert.match(page, /未上图和低于显示阈值的竞争交易仍参与计算/);
  assert.match(page, /principal-analysis\.js/);
});

test('unknown conditions stay unknown; neither opening nor complete interval is invented', async () => {
  const h = fixture(); await h.open();
  assert.equal(h.el('principalOpening').value, ''); assert.equal(h.el('principalCoverage').checked, false);
  assert.equal(h.el('principalNoOverdraft').checked, false); assert.equal(h.el('principalFundsAvailable').checked, false); assert.equal(h.el('principalStart').value, '');
  assert.equal(h.el('principalTargets').querySelectorAll('input').length, 1, 'only same-currency own debits are target choices');
  h.fill(); const done = h.submit();
  assert.deepEqual(h.calls[0].body.conditions, { coverage: 'unknown', no_overdraft: false, funds_available: false, opening_balance: null, balance_kind: 'unknown' });
  h.respond(0, h.result({ status: 'unknown', targets: [], reasons: ['ledger_coverage_unknown','opening_balance_unknown'] })); await done;
  assert.match(h.el('principalStatus').textContent, /完整性未知.*余额未知/);
  assert.doesNotMatch(h.el('principalResults').textContent, /在这些条件下确定/);
});

test('credit and funds availability are independent declarations and reset when reopened', async () => {
  for (const [noOverdraft, fundsAvailable] of [[true, false], [false, true], [true, true]]) {
    const h = fixture(); await h.open(); h.fill();
    h.el('principalNoOverdraft').checked = noOverdraft; h.el('principalFundsAvailable').checked = fundsAvailable;
    const done = h.submit();
    assert.equal(h.calls[0].body.conditions.no_overdraft, noOverdraft);
    assert.equal(h.calls[0].body.conditions.funds_available, fundsAvailable);
    h.respond(0, h.result({status:'unknown', targets:[]})); await done;
    const declarations = h.el('principalResults').textContent;
    assert(declarations.includes(noOverdraft ? '已声明无透支或授信' : '透支或授信情况未知'));
    assert(declarations.includes(fundsAvailable ? '已声明资金全额可用（无未建模冻结或在途资金）' : '资金可用性未知'));
    await h.el('principalClose').click(); await h.open();
    assert.equal(h.el('principalNoOverdraft').checked, false); assert.equal(h.el('principalFundsAvailable').checked, false);
  }
});

test('unknown funds availability and ledger identity have distinct Chinese explanations', async () => {
  const h = fixture(); await h.open(); h.fill(); const done = h.submit();
  h.respond(0, h.result({status:'unknown',targets:[],reasons:['credit_availability_unknown','funds_availability_unknown','account_ledger_identity_unknown']})); await done;
  assert.match(h.el('principalStatus').textContent, /透支或授信情况尚未明确/);
  assert.match(h.el('principalStatus').textContent, /资金是否全额可用尚未明确（可能存在未建模冻结或在途资金）/);
  assert.match(h.el('principalStatus').textContent, /本方账务账户身份尚未明确/);
});

test('large exact strings survive request, result and revision-bound export without changing graph facts', async () => {
  const h = fixture(), graph = clone(h.state.lastGraph); await h.open(); h.fill();
  h.el('principalOpening').value = '9999999999999999.99'; h.el('principalCoverage').checked = true; h.el('principalNoOverdraft').checked = true; h.el('principalFundsAvailable').checked = true;
  const done = h.submit(); assert.equal(h.calls[0].body.source_flow_id, sourceId); assert.equal(h.calls[0].body.target_flow_ids[0], targetId);
  assert.equal(h.calls[0].body.conditions.opening_balance, '9999999999999999.99'); h.respond(0); await done;
  assert.match(h.el('principalResults').textContent, /9999999999999900\.01 — 9999999999999999\.98/);
  assert.match(h.el('principalResults').textContent, /9999999999999990\.02/);
  assert.match(h.el('principalResults').textContent, /上界不能相加/);
  const exported = h.el('principalExport').click();
  assert.deepEqual(h.calls[1].body, { ...h.calls[0].body, expected_revision: 'synthetic-revision' });
  h.respond(1); await exported;
  assert.equal(h.downloads.length, 1); assert.equal(h.downloads[0].filename, 'FundsTrace-条件来源份额.html');
  assert.deepEqual(h.state.lastGraph, graph);
});

test('only equal conditional bounds get a conditional certainty label; FIFO remains separate', async () => {
  const h = fixture(); await h.open(); h.fill(); const done = h.submit();
  h.respond(0,h.result({ targets: [{ id:targetId,amount:'100.00',min:'100.00',max:'100.00',fifo:'100.00' }] })); await done;
  assert.match(h.el('principalResults').textContent, /在这些条件下确定/);
  assert.match(h.el('principalResults').textContent, /FIFO 规则份额/);
  assert.match(h.el('principalResults').textContent, /评分不参与计算/);
});

test('editing while a response is pending aborts and discards that result even if the response arrives', async () => {
  const h = fixture(); await h.open(); h.fill(); const done = h.submit();
  await h.el('principalForm').dispatch('input'); assert.equal(h.calls[0].options.signal.aborted,true);
  h.respond(0); await done; assert.equal(h.el('principalResults').hidden,true); assert.equal(h.el('principalExport').disabled,true);
  const second = h.submit(); h.respond(1); await second; assert.equal(h.el('principalResults').hidden,false);
});

test('close and reopen cannot revive a response from the old dialog binding', async () => {
  const h = fixture(); await h.open(); h.fill(); const done = h.submit();
  await h.el('principalClose').click(); await h.open(); h.fill(); const second = h.submit();
  h.respond(1,h.result({revision:'current'})); await second; h.respond(0,h.result({revision:'obsolete'})); await done;
  assert.match(h.el('principalResults').textContent,/current/); assert.doesNotMatch(h.el('principalResults').textContent,/obsolete/);
});

for (const action of ['case','canvas','context','graph']) {
  test(`${action} change revokes pending reads before any replacement arrives`, async () => {
    const h = fixture(); await h.open(); h.fill(); const done = h.submit();
    if (action === 'case') { h.env.selectCase(2); h.env.selectCase(1); }
    if (action === 'canvas') { h.env.activateCanvas(2); h.env.activateCanvas(0); }
    if (action === 'context') h.env.loadContext();
    if (action === 'graph') await h.env.readGraphData();
    assert.equal(h.el('principalDialog').open,false); assert.equal(h.calls[0].options.signal.aborted,true);
    h.respond(0); await done; assert.equal(h.el('principalResults').hidden,true);
  });
}

test('graph publication, import updates and ctx replacement also invalidate displayed results', async () => {
  for (const action of ['graph','import','context']) {
    const h = fixture(); await h.open(); h.fill(); const done = h.submit(); h.respond(0); await done;
    if (action === 'graph') { h.state.lastGraph = clone(h.state.lastGraph); h.emit('funds:graph-metric'); }
    if (action === 'import') h.emit('funds:import-state');
    if (action === 'context') { h.state.ctx = { ...h.state.ctx }; h.env.syncContextSelection(); }
    assert.equal(h.el('principalDialog').open,false); assert.equal(h.el('principalResults').hidden,true);
    assert.equal(h.el('principalExport').disabled,true);
  }
});

test('export revision conflict removes old amounts and disables export until a new calculation', async () => {
  const h = fixture(); await h.open(); h.fill(); const done = h.submit(); h.respond(0); await done;
  const exported=h.el('principalExport').click(); h.respond(1,{detail:'changed'},409); await exported;
  assert.equal(h.el('principalResults').hidden,true); assert.equal(h.el('principalExport').disabled,true);
  assert.equal(h.downloads.length,0); assert.match(h.el('principalStatus').textContent,/旧结果已失效/);
});

test('export blob arriving after case change cannot trigger a download', async () => {
  const h = fixture(); await h.open(); h.fill(); const done = h.submit(); h.respond(0); await done;
  const blob = deferred(), exported = h.el('principalExport').click();
  h.calls[1].resolve({ok:true,status:200,blob:()=>blob.promise}); await flush();
  h.env.selectCase(2); blob.resolve({}); await exported;
  assert.equal(h.downloads.length,0); assert.equal(h.urls.length,0);
});

test('counterparty observation is not accepted as this account opening/receipt evidence', async () => {
  const h = fixture(); h.data.anchor.self_account = 'SYN-A'; await h.open();
  assert.equal(h.el('principalRun').disabled,true); assert.match(h.el('principalStatus').textContent,/对手方的观察和余额不能代替/);
  assert.equal(h.calls.length,0);
});

test('target labels use counterparty display fields and never expose resolved entity IDs without a display number', async () => {
  for (const [fields, expected] of [
    [{counter_display_account:'SYN-COUNTER-DISPLAY',payee_display_account:'SYN-SIDE-DISPLAY'},'SYN-COUNTER-DISPLAY'],
    [{payee_display_account:'SYN-SIDE-DISPLAY'},'SYN-SIDE-DISPLAY'],
    [{},'显示号码未提供'],
    [{payee_account:'SYN-LEGACY-NUMBER'},'SYN-LEGACY-NUMBER'],
  ]) {
    const h=fixture(); h.state.lastGraph.nodes=[];
    Object.assign(h.data.rows[0],{payee_name:'',payee_account:'fte_synthetic_hidden',counter_account:'fte_synthetic_hidden'},fields);
    await h.open(); const label=h.el('principalTargets').textContent;
    assert.match(label,new RegExp(expected)); assert.doesNotMatch(label,/fte_synthetic_hidden/);
  }
});

test('invalid or misbound results cannot display source certainty', async () => {
  for (const override of [{case_id:'2'}, {account:'SYN-X'}, {currency:'USD'}, {status:'ok',targets:[{id:targetId,amount:'1.00'}]}]) {
    const h=fixture(); await h.open(); h.fill(); const done=h.submit(); h.respond(0,h.result(override)); await done;
    assert.equal(h.el('principalResults').hidden,true); assert.equal(h.el('principalExport').disabled,true);
  }
});

test('result target links return to the existing context row without rewriting graph amounts', async () => {
  const h=fixture(), graph=clone(h.state.lastGraph); await h.open(); h.fill(); const done=h.submit(); h.respond(0); await done;
  await h.el('principalResults').querySelectorAll('button')[0].click();
  assert.equal(h.el('principalDialog').open,false); assert.equal(h.targetRow.focused,true); assert.equal(h.targetRow.scrolled,true);
  assert.deepEqual(h.state.lastGraph,graph);
});

test('network accounts use real graph keys with visible display numbers and independently unknown conditions', async () => {
  const h=fixture(); await h.open(); h.fill(); h.el('principalNetworkEnabled').checked=true;
  const choice=h.el('principalAccountChoices').querySelectorAll('input')[0];
  assert.equal(choice.value,'fte_synthetic_c'); assert.match(h.el('principalAccountChoices').textContent,/SYN-C-DISPLAY/);
  assert.doesNotMatch(h.el('principalAccountChoices').textContent,/fte_synthetic_c/);
  choice.checked=true; await choice.dispatch('change');
  const done=h.submit(); assert.equal(h.calls[0].path,'/api/analysis/principal/network');
  assert.equal(h.calls[0].body.boundary,'unknown'); assert(!Object.hasOwn(h.calls[0].body,'account'));
  assert.deepEqual(h.calls[0].body.accounts,[
    {account:'SYN-B',conditions:{coverage:'unknown',no_overdraft:false,funds_available:false,opening_balance:null,balance_kind:'unknown'}},
    {account:'fte_synthetic_c',conditions:{coverage:'unknown',no_overdraft:false,funds_available:false,opening_balance:null,balance_kind:'unknown'}},
  ]);
  h.respond(0,h.result({status:'unknown',targets:[],reasons:['external_boundary_returns_unknown'],ledger_count:undefined})); await done;
  assert.match(h.el('principalStatus').textContent,/回流尚未明确/); assert.match(h.el('principalResults').textContent,/尚未进入账本计算/);
});

test('network funds availability is explicit for each ledger and independent of its credit declaration', async () => {
  const h = fixture(); await h.open(); h.fill(); h.el('principalNetworkEnabled').checked = true;
  h.el('principalNoOverdraft').checked = true;
  const choice = h.el('principalAccountChoices').querySelectorAll('input')[0]; choice.checked = true; await choice.dispatch('change');
  const [, , noOverdraft, fundsAvailable] = h.el('principalAccountConditions').querySelectorAll('input');
  assert.equal(noOverdraft.checked, false); assert.equal(fundsAvailable.checked, false);
  fundsAvailable.checked = true; await fundsAvailable.dispatch('change');
  const done = h.submit();
  assert.equal(h.calls[0].body.accounts[0].conditions.no_overdraft, true);
  assert.equal(h.calls[0].body.accounts[0].conditions.funds_available, false);
  assert.equal(h.calls[0].body.accounts[1].conditions.no_overdraft, false);
  assert.equal(h.calls[0].body.accounts[1].conditions.funds_available, true);
  h.respond(0, h.result({status:'unknown',targets:[]})); await done;
  assert.match(h.el('principalResults').textContent, /来源账户：完整性未知；已声明无透支或授信；资金可用性未知/);
  assert.match(h.el('principalResults').textContent, /SYN-C-DISPLAY：完整性未知；透支或授信情况未知；已声明资金全额可用/);
});

test('network exact amounts, fused event targets and terminal FIFO totals share the network export request', async () => {
  const h=fixture(); await h.open(); h.fill(); h.el('principalNetworkEnabled').checked=true; h.el('principalBoundary').checked=true;
  const choice=h.el('principalAccountChoices').querySelectorAll('input')[0]; choice.checked=true; await choice.dispatch('change');
  const [opening,coverage,noOverdraft,fundsAvailable]=h.el('principalAccountConditions').querySelectorAll('input');
  opening.value='9999999999999999.99'; await opening.dispatch('input'); coverage.checked=true; await coverage.dispatch('change'); noOverdraft.checked=true; await noOverdraft.dispatch('change');
  fundsAvailable.checked=true; await fundsAvailable.dispatch('change');
  h.el('principalNetworkTargets').value=targetId+', 9007199254740996';
  const done=h.submit(); assert.equal(h.calls[0].body.accounts[1].conditions.opening_balance,'9999999999999999.99');
  const networkResult=h.result({source_amount:'9999999999999999.99',account:undefined,
    fifo_witness:{external_total:'9999999999999900.01',retained_total:'99.98'},
    targets:[{id:'event:synthetic',flow_ids:[targetId,'9007199254740996'],amount:'9999999999999999.98',min:'1.00',max:'99.98',fifo:'50.00'}]});
  h.respond(0,networkResult); await done;
  assert.equal(h.el('principalResults').hidden,false); assert.match(h.el('principalResults').textContent,/FIFO 终止流出 9999999999999900\.01，期末留存 99\.98/);
  assert.match(h.el('principalResults').textContent,/沿途交易份额不能相加为本金/); assert.doesNotMatch(h.el('principalResults').textContent,/event:synthetic/);
  const exported=h.el('principalExport').click(); assert.equal(h.calls[1].path,'/api/analysis/principal/network/export');
  assert.deepEqual(h.calls[1].body,{...h.calls[0].body,expected_revision:'synthetic-revision'}); h.respond(1); await exported;
  assert.equal(h.downloads.length,1);
});
