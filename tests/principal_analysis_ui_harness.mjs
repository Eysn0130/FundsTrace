import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const page = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const source = readFileSync(new URL('../static/principal-analysis.js', import.meta.url), 'utf8');
const app = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
const context = vm.createContext({});
vm.runInContext(readFileSync(new URL('../static/money.js', import.meta.url), 'utf8'), context);
vm.runInContext(source, context);
const facts = context.FundsFacts;

test('existing flow context requires no new principal action, dialog, or condition form', () => {
  assert.doesNotMatch(page, /id="(?:ctxPrincipal|principalDialog|principalForm|principalCoverage|principalOpening|principalNetworkTargets)"/);
  assert.match(page, /id="ctxModal"/);
  assert.match(page, /id="ctxTrack"/);
  assert.doesNotMatch(source, /fetch\(|addEventListener\(|showModal\(|createElement\(/);
  assert.match(app, /FundsFacts\?\.principal\(r\.principal\)/);
});

test('confirmed event has one primary amount; unresolved amount is not exact or zero', () => {
  const exact = {status:'exact',count:{lower:1,upper:1},totals:[{currency:'CNY',amount:'100.00',lower:'100.00',upper:'100.00'}]};
  assert.equal(facts.count(exact), '1 个事件');
  assert.equal(facts.totals(exact), '¥100.00');
  const pending = {status:'bounded',count:{lower:0,upper:2},totals:[{currency:'CNY',amount:null,lower:'0.00',upper:'200.00'}]};
  assert.match(facts.count(pending), /0–2.*待核/);
  assert.equal(facts.totals(pending), 'CNY 0.00–200.00（待核）');
  assert.match(facts.totals({totals:[{currency:null,amount:null,lower:'0.00',upper:null}]}), /币种待定.*上界未知/);
});

test('backend reasons and exact large ranges are presented without fabricating FIFO', () => {
  const text = facts.principal({status:'unknown',currency:'CNY',assessment:[{status:'unknown',message:'账本覆盖未获证明'}],targets:[{amount:'9999999999999999.99',min:'0.00',max:'9999999999999999.99',fifo:null}]});
  assert.match(text, /9,999,999,999,999,999\.99/);
  assert.match(text, /账本覆盖未获证明/);
  assert.match(text, /FIFO 证据不足/);
  assert.match(text, /上界不可相加/);
  assert.equal(facts.principal(null), '');
});

test('the actual edge label uses one primary projection and geometric observation counts stay separate', () => {
  const scope = vm.createContext({ window: { FundsFacts: facts }, FundsMoney: context.FundsMoney, state: {edgeLabels:new Set(['money'])} });
  const section = (start, end) => app.slice(app.indexOf(start), app.indexOf(end, app.indexOf(start)));
  vm.runInContext(section('function esc(', 'function toast('), scope);
  vm.runInContext(section('function fmtEdgeSpan(', 'function renderGraph('), scope);
  scope.edge = {cnt:2,total:'200.00',amount_totals:[{currency:'CNY',amount:'200.00'}],funds:{status:'exact',count:{lower:1,upper:1},totals:[{currency:'CNY',amount:'100.00',lower:'100.00',upper:'100.00'}]}};
  assert.equal(vm.runInContext('edgeLabel(edge)', scope), '1 个事件·¥100.00');
  scope.edge.cnt = null; scope.edge.funds = {status:'bounded',count:{lower:0,upper:2},totals:[{currency:'CNY',amount:null,lower:'0.00',upper:'200.00'}]};
  assert.equal(vm.runInContext('edgeLabel(edge)', scope), '0–2 个事件（待核）·CNY 0.00–200.00（待核）');
  assert.match(app, /in_observation_count \?\? n.in_cnt/);
  assert.match(app, /'out_funds', 'in_funds'/);
  assert.match(app, /'currencies', 'funds', 'event_summary'/);
});

test('the existing case header and search result render only the authoritative primary amount', async () => {
  const elements = new Map();
  const element = selector => elements.get(selector) || elements.set(selector, {innerHTML:'',textContent:'',style:{},classList:{toggle(){}}}).get(selector);
  const exact = {status:'exact',count:{lower:1,upper:1},totals:[{currency:'CNY',amount:'100.00',lower:'100.00',upper:'100.00'}]};
  const state = {stats:{flows:2,accounts:2,funds:exact},lastGraph:{funds:exact},tracked:new Set([1,2]),search:{}};
  const scope = vm.createContext({window:{FundsFacts:facts},FundsMoney:context.FundsMoney,state,$:element,
    exportWorkspace:null,caseWorkspace:null,syncMinimapNavigation(){},caseContext:()=>({}),isCurrentCase:()=>true,
    searchParams:()=>'',api:async()=>({funds:exact,rows:[{id:1}],page:1,size:50,total:1}),renderFlows(rows){assert.equal(rows.length,1);}});
  const section = (start,end) => app.slice(app.indexOf(start),app.indexOf(end,app.indexOf(start)));
  vm.runInContext(section('function esc(', 'function toast('),scope);
  vm.runInContext(section('function updateHeader()', '/* ---------------- 流水查询'),scope);
  vm.runInContext(section('async function doSearch(', 'function partyCell('),scope);
  vm.runInContext('updateHeader()',scope); await vm.runInContext('doSearch()',scope);
  assert.match(element('#statLine').innerHTML,/1 个事件.*100\.00/);
  assert.doesNotMatch(element('#statLine').innerHTML,/200\.00|2.*笔/);
  assert.equal(element('#resultInfo').textContent,'1 个事件 · ¥100.00');
});
