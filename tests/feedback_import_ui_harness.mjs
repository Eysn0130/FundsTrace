import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const app = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
const money = readFileSync(new URL('../static/money.js', import.meta.url), 'utf8');
const index = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const flush = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };
const response = value => ({ ok: true, status: 200, json: async () => value, blob: async () => ({ synthetic: value }), text: async () => JSON.stringify(value) });

test('hidden case summaries retain source interpretation and discard statistics arriving after a case switch', async () => {
  const nodes = new Map();
  const element = selector => {
    if (!nodes.has(selector)) nodes.set(selector, { hidden: true, textContent: '', innerHTML: '', style: {},
      setAttribute() {}, classList: { toggle() {} } });
    return nodes.get(selector);
  };
  const state = { caseId: 1, generation: 1, tracked: new Set(), stats: {} };
  let pending, navigationSyncs = 0;
  const context = vm.createContext({ state, $: element, window: {}, exportWorkspace: null, caseWorkspace: null,
    syncMinimapNavigation: () => { navigationSyncs++; },
    caseContext: () => ({ caseId: state.caseId, generation: state.generation }),
    isCurrentCase: binding => binding.caseId === state.caseId && binding.generation === state.generation,
    api: async () => new Promise(resolve => { pending = resolve; }) });
  vm.runInContext(money, context);
  vm.runInContext(app.slice(app.indexOf('function esc('), app.indexOf('function toast(')), context);
  vm.runInContext(app.slice(app.indexOf('async function refreshStats()'), app.indexOf('/* ---------------- 流水查询')), context);
  const load = vm.runInContext('refreshStats()', context);
  pending({ flows: 80, accounts: 2, import_coverage: { scope: 'source_reported_flows', unresolved_records: 349, non_monetary_records: 233, cancelled_records: 4, excluded_flows: 2, financial_interpretation_complete: false } });
  await load;
  assert.equal(navigationSyncs, 1, 'current statistics refresh navigation through its separate owner');
  assert.equal(element('#importCoverageNote').hidden, true);
  assert.match(element('#importCoverageNote').textContent, /当前流水 80 笔（已确认同笔只计一次）/);
  assert.match(element('#importCoverageNote').textContent, /来源审计保留 349 条资金含义未确定的条目/);
  assert.match(element('#importCoverageNote').textContent, /不代表结算净额/);
  assert.match(element('#importCoverageNote').textContent, /233 条无资金变动记录/);
  assert.match(element('#importCoverageNote').textContent, /4 条已配对冲正\/撤销记录/);
  assert.match(element('#importCoverageNote').textContent, /撤回后仍保留，不表示当前仍有对应流水或历史问题已解决/);
  assert.match(element('#importCoverageNote').textContent, /本案有 2 条留存来源流水未计入有效统计/);
  state.stats = { flows: null, accounts: 2, funds: { observation_count: 2 },
    import_coverage: { scope: 'source_reported_flows', unresolved_records: 1 } };
  vm.runInContext('updateHeader()', context);
  assert.match(element('#statLine').innerHTML, /来源观察 <b>2 条<\/b>/);
  assert.doesNotMatch(element('#statLine').innerHTML, /流水 <b>2/);
  assert.match(element('#statLine').title, /来源观察条数/);
  assert.doesNotMatch(element('#statLine').title, /展示合计保留原始金额/);
  assert.equal(element('#importCoverageNote').hidden, true);
  assert.match(element('#importCoverageNote').textContent, /当前有效交易笔数未确定；来源观察 2 条/);
  assert.match(element('#importCoverageNote').textContent, /来源审计保留 1 条资金含义未确定的条目/);
  assert.match(element('#importCoverageNote').textContent, /同笔关系未确定，金额是否确定及其范围以当前资金统计为准/);
  assert.doesNotMatch(element('#importCoverageNote').textContent, /当前有效交易 0 笔|展示合计保留原始金额/);
  state.stats.flows = 2;
  Object.assign(state.stats.funds, { status: 'bounded', confirmed_event_count: 1, unresolved_observation_count: 1, observation_count: 3 });
  vm.runInContext('updateHeader()', context);
  assert.match(element('#statLine').innerHTML, /流水 <b>2 笔<\/b>/);
  assert.match(element('#importCoverageNote').textContent, /当前流水 2 笔/);
  assert.doesNotMatch(element('#statLine').innerHTML, /1笔＋1条|流水 <b>1 笔/);
  for (const flows of [0, 3]) {
    state.stats.flows = flows;
    state.stats.funds.observation_count = flows;
    vm.runInContext('updateHeader()', context);
    assert.equal(element('#importCoverageNote').hidden, true);
    assert.ok(element('#importCoverageNote').textContent.includes(`当前流水 ${flows} 笔（已确认同笔只计一次）`));
    assert.match(element('#importCoverageNote').textContent, /展示合计保留原始金额/);
    assert.doesNotMatch(element('#importCoverageNote').textContent, /笔数未确定|；来源观察/);
  }
  const oldLoad = vm.runInContext('refreshStats()', context);
  state.caseId = 2; state.generation += 1; state.stats = {};
  vm.runInContext('updateHeader()', context);
  assert.equal(element('#importCoverageNote').hidden, true);
  assert.equal(element('#importCoverageNote').textContent, '');
  const beforeStaleNavigation = navigationSyncs;
  pending({ flows: 80, accounts: 2, import_coverage: { scope: 'source_reported_flows', unresolved_records: 349, non_monetary_records: 233, financial_interpretation_complete: false } });
  await oldLoad;
  assert.equal(navigationSyncs, beforeStaleNavigation, 'a stale statistics response cannot refresh navigation for the new case');
  assert.equal(element('#importCoverageNote').hidden, true);
  assert.equal(element('#importCoverageNote').textContent, '');
});
