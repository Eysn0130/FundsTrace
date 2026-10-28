import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const workflow = readFileSync(new URL('../static/workflow-ui.js', import.meta.url), 'utf8');
const app = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
const money = readFileSync(new URL('../static/money.js', import.meta.url), 'utf8');
const index = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const flush = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };
const response = value => ({ ok: true, status: 200, json: async () => value, blob: async () => ({ synthetic: value }), text: async () => JSON.stringify(value) });

function feedback(overrides = {}) {
  return { profile: 'funds-trace-bank-feedback-v2', record_total: 6, transaction_total: 5, accepted: 2, review: 0, non_monetary: 1, unresolved: 1,
    non_transaction: 1, auxiliary: 1, reason_counts: { invalid_direction: 1 },
    currency_totals: { CNY: '9007199254740993.07', USD: '0.03' },
    review_examples: [], unresolved_examples: [{ member_path: 'synthetic/交易.txt', source_row: 4, reason_code: 'invalid_direction' }],
    members: [{ member_path: 'synthetic/交易.txt', role: 'transaction', sha256: 'a'.repeat(64), size_bytes: 40, encoding: 'gb18030', row_count: 5 },
      { member_path: 'synthetic/冻结.txt', role: 'freeze', sha256: 'b'.repeat(64), size_bytes: 20, encoding: 'gb18030', row_count: 1 }], ...overrides };
}
function preview(overrides = {}) {
  return { import_id: 'SYN-IMPORT', filename: 'synthetic.zip', mode: 'B', columns: ['交易金额'], mapping: { amount: '交易金额' },
    self_accounts_in_file: [], stats: { total: 5, ok: 2, skipped: 3 }, feedback: feedback(),
    preview: [{ trade_time: '2026-01-01 10:00:00', payer_name: '', payer_account: 'SYN-A', payee_name: '', payee_account: '',
      amount: '9007199254740993.07', currency: 'CNY', source_member: 'synthetic/交易.txt', source_row: 2, source_record_no: 1 }], ...overrides };
}

function harness(url = 'http://127.0.0.1:8300/') {
  const nodes = new Map(), events = new Map(), uploads = [], requests = [], downloads = [], notices = [];
  const location = new URL(url);
  const modes = [{ value: 'A', checked: false }, { value: 'B', checked: true }];
  let fetcher = () => { throw new Error('Unexpected request'); };
  const element = selector => {
    const id = selector.replace(/^#/, '');
    if (id === 'impAdvanced') return null;
    if (nodes.has(id)) return nodes.get(id);
    const node = { id, style: { display: 'none' }, value: '', files: [], hidden: false, disabled: false, textContent: '', innerHTML: '',
      setAttribute() {}, removeAttribute() {}, addEventListener() {}, after(child) { nodes.set(child.id, child); },
      append(...children) { for (const child of children) if (child.id) nodes.set(child.id, child); },
      click() { if (this.href) downloads.push({ href: this.href, filename: this.download }); else if (!this.disabled) return this.onclick?.(); },
      remove() {}, classList: { add() {}, remove() {} } };
    nodes.set(id, node); return node;
  };
  const selects = () => [...element('impMapping').innerHTML.matchAll(/<select\b([^>]*)>([\s\S]*?)<\/select>/g)].map(([, attrs, options]) => ({
    dataset: { field: attrs.match(/data-field="([^"]*)"/)?.[1] }, value: [...options.matchAll(/<option\b([^>]*)>/g)].find(([, value]) => value.includes('selected'))?.[1].match(/value="([^"]*)"/)?.[1] || '' }));
  const state = { caseId: 1, generation: 1, cases: [{ id: 1, name: '合成案件' }], imp: null };
  const context = vm.createContext({ state,
    $: element, $$: selector => selector === 'input[name=impMode]' ? modes : selector === '#impMapping select' ? selects() : selector.includes('impMapping select,') ? [...selects(), ...modes, element('impSelfAccts')] : [],
    document: { createElement: () => element('created-' + nodes.size), body: { appendChild() {} } },
    window: { location, history: { replaceState(_state, _unused, value) { location.href = String(value); } },
      addEventListener: (name, callback) => events.set(name, callback) },
    caseContext: () => ({ caseId: state.caseId, generation: state.generation }),
    isCurrentCase: binding => binding.caseId === state.caseId && binding.generation === state.generation,
    jsonOpts: value => ({ headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) }),
    hidePbar() {}, setPbarBusy() {}, setPbar() {}, refreshAll: async () => {}, toast: value => notices.push(value),
    fetch: (path, options) => { requests.push({ path, options }); return fetcher(path, options); },
    URL: class extends URL { static createObjectURL() { return 'blob:synthetic'; } static revokeObjectURL() {} },
    FormData: class { constructor() { this.entries = []; } append(...values) { this.entries.push(values); } },
    XMLHttpRequest: class {
      constructor() { this.upload = {}; uploads.push(this); }
      open(method, path) { this.method = method; this.path = path; } send(form) { this.form = form; }
      finish(value) { this.status = 200; this.responseText = JSON.stringify(value); this.onload(); }
    },
  });
  vm.runInContext(money, context);
  vm.runInContext(app.slice(app.indexOf('function esc('), app.indexOf('function toast(')), context);
  vm.runInContext(app.slice(app.indexOf('const FIELD_LABELS ='), app.indexOf('function setPbar(')), context);
  // Detail drawers are independent of imports and are exercised in their own harness.
  vm.runInContext(workflow.replace('    bindDetailRequests();', ''), context);
  events.get('DOMContentLoaded')();
  return { element, state, requests, uploads, downloads, notices, network(handler) { fetcher = handler; },
    restore() { return context.window.FundsImportWorkflow.restore(); },
    open() { element('btnImport').onclick(); },
    async ready(value = preview()) { this.open(); element('impFile').files = [{ name: value.filename }]; const task = element('impFile').onchange(); uploads.at(-1).finish(value); await task; },
    commit() { return element('impCommit').onclick(); },
    close() { element('impCancel').onclick(); },
    changeCase() { state.caseId = 2; state.generation += 1; element('importModal').style.display = 'none'; },
  };
}

test('folder selection retains every relative filename and uses one case-bound import', async () => {
  const h = harness(), summary = tableSummary();
  h.network(async path => path.includes('/commit') ? response({ import_id: 'SYN-IMPORT' })
    : response({ stage: 'done', result: { source_id: 'SYN-SOURCE', ok: 1, table_summary: summary } }));
  h.open();
  const files = [{ name: '账户.csv', webkitRelativePath: '资料/银行/账户.csv' },
    { name: '流水.csv', webkitRelativePath: '资料/银行/流水.csv' }];
  h.element('impDirectory').files = files;
  const task = h.element('impDirectory').onchange(), upload = h.uploads.at(-1);
  assert.equal(upload.path, '/api/import/directory/preview?case_id=1');
  assert.deepEqual(upload.form.entries.map(([key, file, path]) => [key, file.name, path]), [
    ['files', '账户.csv', '资料/银行/账户.csv'], ['files', '流水.csv', '资料/银行/流水.csv']]);
  upload.finish(preview({ automatic: true, feedback: undefined, table_summary: summary })); await task;
  assert.equal(h.requests.filter(item => item.path.includes('/commit')).length, 1);
  assert.equal(h.element('impResultTitle').textContent, '自动处理完成');
});

test('a stale folder preview cannot import into a switched case', async () => {
  const h = harness(); h.open();
  h.element('impDirectory').files = [{ name: '交易.csv', webkitRelativePath: '资料/交易.csv' }];
  const task = h.element('impDirectory').onchange(); h.changeCase();
  h.uploads.at(-1).finish(preview({ automatic: true, feedback: undefined, table_summary: tableSummary() }));
  await task; assert.equal(h.requests.length, 0); assert.equal(h.state.imp, null);
});

test('feedback preview locks fixed mapping, preserves exact totals and labels unknown counterpart with source rows', async () => {
  const h = harness(); await h.ready();
  assert.match(index, /accept="[^"]*\.zip/);
  assert.equal(h.element('impMappingSection').hidden, true);
  assert.equal(h.element('impMode').hidden, true);
  assert.equal(h.element('impRe').hidden, true);
  assert.match(h.element('impFeedback').innerHTML, /9,007,199,254,740,993\.07/);
  assert.match(h.element('impFeedback').innerHTML, /USD 0\.03/);
  assert.match(h.element('impFeedback').innerHTML, /未计入资金流水 1 条/);
  assert.match(h.element('impFeedback').innerHTML, /无资金变动 1 条/);
  assert.match(h.element('impFeedback').innerHTML, /非本次新增金额或净结算金额/);
  assert.match(h.element('impFeedback').innerHTML, /收付方向无法识别/);
  assert.match(h.element('impFeedback').innerHTML, /冻结信息/);
  assert.equal(h.element('impFeedback').innerHTML.includes('invalid_direction'), false);
  assert.match(h.element('impTbody').innerHTML, /原始第 2 行 · 记录 1/);
  assert.match(h.element('impTbody').innerHTML, /未知对手/);
  assert.match(h.element('impTbody').innerHTML, /户名未提供/);
  assert.match(h.element('impTbody').innerHTML, /账号缺失/);
  await h.element('impRe').onclick();
  assert.equal(h.requests.length, 0, 'fixed feedback packages cannot be repreviewed from manual controls');
});

function tableSummary(overrides = {}) {
  return { record_total: 3, accepted: 1, unresolved: 1, non_monetary: 1, non_transaction: 0,
    auxiliary: 0, cancelled: 0, reason_counts: { invalid_direction: 1, zero_amount: 1 },
    currency_totals: { CNY: '9007199254740993.07' },
    mapping_evidence: [{ member_path: 'synthetic/table.csv', field: 'amount', column: '交易金额（分）', unit_factor: '0.01' }],
    documents: [{ member_path: 'synthetic/table.csv', sheet: '', header_row: 1, row_count: 3 }],
    unresolved_examples: [{ member_path: 'synthetic/table.csv', source_row: 3, reason_code: 'invalid_direction' }],
    ...overrides };
}

for (const suffix of ['csv', 'xlsx', 'zip']) {
  test(`fresh automatic ${suffix} uploads finish with one commit and disclose all dispositions`, async () => {
    const h = harness(), summary = tableSummary();
    h.network(async path => path.includes('/commit') ? response({ import_id: 'SYN-IMPORT' })
      : response({ stage: 'done', result: { source_id: 'SYN-SOURCE', ok: 1, table_summary: summary } }));
    await h.ready(preview({ filename: `synthetic.${suffix}`, automatic: true, feedback: undefined,
      table_summary: summary, stats: { total: 3, ok: 1, skipped: 2 } }));
    assert.deepEqual(h.requests.map(item => item.path), ['/api/import/commit', '/api/import/progress?import_id=SYN-IMPORT&case_id=1']);
    assert.equal(h.element('impResultTitle').textContent, '自动处理完成');
    assert.match(h.element('impResultText').textContent, /已保存 3 条记录 · 新增流水记录 1 条/);
    assert.match(h.element('impResultFeedback').innerHTML, /未计入资金流水 1 条 · 无资金变动 1 条/);
    assert.match(h.element('impResultFeedback').innerHTML, /金额单位换算系数：0.01/);
    assert.match(h.element('impResultFeedback').innerHTML, /9,007,199,254,740,993\.07/);
    assert.equal(h.element('impResultDownloads').hidden, false);
    await h.commit();
    assert.equal(h.requests.filter(item => item.path.includes('/commit')).length, 1);
  });
}

test('an entirely unresolved automatic table still saves its closed source ledger without a manual gate', async () => {
  const h = harness(), summary = tableSummary({ accepted: 0, unresolved: 3, non_monetary: 0, currency_totals: {} });
  h.network(async path => path.includes('/commit') ? response({ import_id: 'SYN-IMPORT' })
    : response({ stage: 'done', result: { source_id: 'SYN-SOURCE', ok: 0, table_summary: summary } }));
  await h.ready(preview({ filename: 'synthetic.csv', automatic: true, feedback: undefined, table_summary: summary,
    preview: [], stats: { total: 3, ok: 0, skipped: 3 } }));
  assert.equal(h.requests.filter(item => item.path.includes('/commit')).length, 1);
  assert.match(h.element('impResultText').textContent, /已保存 3 条记录 · 新增流水记录 0 条/);
  assert.match(h.element('impResultFeedback').innerHTML, /未计入资金流水 3 条/);
  assert.equal(/需复核|待复核/.test(h.element('impResultFeedback').innerHTML), false);
});

test('automatic tables submit every validated source mapping without exposing manual matching controls', async () => {
  const h = harness(), summary = tableSummary();
  const mapping = { amount: '交易金额', status: '交易状态', voucher_reference: '凭证号', time_part: '交易时刻' };
  h.network(async path => path.includes('/commit') ? response({ import_id: 'SYN-IMPORT' })
    : response({ stage: 'done', result: { source_id: 'SYN-SOURCE', ok: 1, table_summary: summary } }));
  await h.ready(preview({ automatic: true, feedback: undefined, table_summary: summary, mapping,
    columns: Object.values(mapping) }));
  assert.deepEqual(JSON.parse(h.requests[0].options.body).mapping, mapping);
  assert.equal(h.element('impMappingSection').hidden, true);
  assert.equal(h.element('impMode').hidden, true);
  assert.equal(h.element('impSelfLine').hidden, true);
  assert.equal(h.element('impRe').hidden, true);
});

test('automatic upload receipt loss keeps the import lease and recovery only reads its known task', async () => {
  const h = harness(), summary = tableSummary();
  h.network(async () => { throw new Error('synthetic lost commit receipt'); });
  await h.ready(preview({ automatic: true, feedback: undefined, table_summary: summary }));
  assert.equal(h.requests.filter(item => item.path.includes('/commit')).length, 1);
  assert.equal(h.element('impRetryProgress').hidden, false);
  h.network(async () => response({ stage: 'done', result: { source_id: 'SYN-SOURCE', ok: 1, table_summary: summary } }));
  await h.element('impRetryProgress').onclick();
  assert.equal(h.requests.filter(item => item.path.includes('/commit')).length, 1);
  assert.match(h.element('impResultText').textContent, /已保存 3 条记录/);
});

test('restoring an automatic table assessment keeps its summary and never silently repeats the write', async () => {
  const h = harness('http://127.0.0.1:8300/?case_id=1&import_id=SYN-IMPORT'), summary = tableSummary();
  h.network(async path => path.includes('/repreview')
    ? response(preview({ automatic: true, feedback: undefined, table_summary: summary }))
    : response({ case_id: '1', items: [{ case_id: '1', surface: 'legacy', import_id: 'SYN-IMPORT',
      stage: 'error', source_available: true, request: { mode: 'B', mapping: { amount: '交易金额' } } }] }));
  await h.restore();
  assert.equal(h.requests.filter(item => item.path.includes('/commit')).length, 0);
  assert.match(h.element('impFeedback').innerHTML, /表格自动识别 · 全量校验/);
  assert.match(h.element('impRecoveryText').textContent, /确认后才会再次导入/);
});

test('restored completed imports download the original with its recorded filename', async () => {
  const h = harness('http://127.0.0.1:8300/?case_id=1&import_id=SYN-IMPORT');
  h.network(async path => path.includes('/progress')
    ? response({ stage: 'done', result: { source_id: 'SYN-SOURCE', ok: 2, feedback: feedback() } })
    : path.includes('/sources/') ? response({ synthetic: true })
      : response({ case_id: '1', items: [{ case_id: '1', surface: 'legacy', import_id: 'SYN-IMPORT',
        filename: 'restored-synthetic.zip', stage: 'done', source_available: true }] }));
  await h.restore();
  assert.equal(h.requests.filter(item => item.path.includes('/commit')).length, 0);
  await h.element('impDownloadOriginal').onclick();
  assert.deepEqual(h.downloads, [{ href: 'blob:synthetic', filename: 'restored-synthetic.zip' }]);
});

test('entirely unresolved packages complete automatically after import authorization and preserve original evidence', async () => {
  const h = harness(), summary = feedback({ record_total: 3, transaction_total: 3, accepted: 0, non_monetary: 0, unresolved: 3, non_transaction: 0, auxiliary: 0, currency_totals: {} });
  await h.ready(preview({ feedback: summary, preview: [], stats: { total: 3, ok: 0, skipped: 3 } }));
  assert.equal(h.element('impCommit').disabled, false);
  assert.equal(h.element('impCommit').textContent, '开始导入');
  h.network(async path => path.includes('/commit') ? response({ import_id: 'SYN-IMPORT' }) : path.includes('/progress') ? response({ stage: 'done', result: { source_id: 'SYN-SOURCE', ok: 0, skipped: 3, feedback: summary } }) : response({ complete: true }));
  await h.commit();
  assert.equal(h.element('impResultTitle').textContent, '自动处理完成');
  assert.equal(h.element('impResultDownloads').hidden, false);
  assert.match(h.element('impResultFeedback').innerHTML, /未计入资金流水 3 条/);
  assert.match(h.element('impResultFeedback').innerHTML, /自动处理已完成，未计入资金流水的记录已保留来源和原因，无需逐条确认；这些记录不参与金额、交易笔数和关系统计/);
  assert.match(h.element('impResultText').textContent, /已保存 3 条记录 · 新增流水记录 0 条/);
  assert.match(h.element('impResultText').textContent, /未计入资金流水 3 条 · 来源反馈 0 条 · 辅助资料 0 条/);
  assert.equal(/待复核|需复核|保存处置结果/.test(h.element('impResultFeedback').innerHTML), false);
  assert.equal(h.element('impResultText').textContent.includes('跳过'), false);
  assert.equal(h.notices.some(notice => notice.includes('跳过')), false);
  await h.element('impDownloadReport').onclick();
  await h.element('impDownloadOriginal').onclick();
  assert.deepEqual(h.requests.slice(-2).map(item => item.path), ['/api/v1/cases/1/sources/SYN-SOURCE/report', '/api/v1/cases/1/sources/SYN-SOURCE/file']);
  assert.deepEqual(h.downloads.map(item => item.filename), ['feedback-SYN-SOURCE-dispositions.json', 'synthetic.zip']);
});

test('paired cancellations save automatically with zero new flows and keep historical exclusions separate', async () => {
  const h = harness(), summary = feedback({ profile: 'funds-trace-bank-feedback-v3', record_total: 2, transaction_total: 2,
    accepted: 0, cancelled: 2, excluded_flows: 0, non_monetary: 0, unresolved: 0, non_transaction: 0, auxiliary: 0,
    reason_counts: { matched_cancellation: 2 }, currency_totals: {}, unresolved_examples: [], members: [{ ...feedback().members[0], row_count: 2 }] });
  await h.ready(preview({ feedback: summary, preview: [], stats: { total: 2, ok: 0, skipped: 2 } }));
  assert.equal(h.element('impCommit').disabled, false);
  assert.match(h.element('impFeedback').innerHTML, /已配对冲正\/撤销 2 条（双方均不计有效资金）/);
  assert.match(h.element('impFeedback').innerHTML, /已配对冲正\/撤销，双方均不计有效资金：2 条/);
  assert.equal(/待复核|需复核|不自动抵扣或推定配对|历史留存流水已退出有效统计/.test(h.element('impFeedback').innerHTML), false);
  h.network(async path => path.includes('/commit') ? response({ import_id: 'SYN-IMPORT' }) : response({ stage: 'done', result: { source_id: 'SYN-SOURCE', ok: 0, rows_dup: 0, feedback: { ...summary, excluded_flows: 1 } } }));
  await h.commit();
  assert.match(h.element('impResultText').textContent, /已保存 2 条记录 · 新增流水记录 0 条 · 重复 0 条 · 已配对冲正\/撤销 2 条（双方均不计有效资金）/);
  assert.match(h.element('impResultFeedback').innerHTML, /本案另有 1 笔历史留存流水已退出有效统计/);
  assert.match(h.element('impResultFeedback').innerHTML, /普通退款按来源记账方向与金额处理/);
  assert.match(h.element('impResultFeedback').innerHTML, /无资金变动 0 条 · 未计入资金流水 0 条/);
});

test('late feedback downloads never publish after a case switch and summary strings are escaped', async () => {
  const h = harness(), hostile = '<img src=x onerror="SYN">';
  const summary = feedback({ profile: hostile, reason_counts: { [hostile]: 1 } });
  await h.ready(preview({ feedback: summary }));
  assert.equal(h.element('impFeedback').innerHTML.includes('<img'), false);
  let finish;
  h.network(async path => path.includes('/commit') ? response({ import_id: 'SYN-IMPORT' }) : path.includes('/progress') ? response({ stage: 'done', result: { source_id: 'SYN-SOURCE', ok: 2, feedback: summary } }) : new Promise(resolve => { finish = resolve; }));
  await h.commit();
  const task = h.element('impDownloadReport').onclick(); await flush(); h.changeCase(); finish(response({ complete: true })); await task;
  assert.equal(h.downloads.length, 0);
});

test('normal CSV previews retain manual mapping and zero-row imports remain blocked', async () => {
  const h = harness(); await h.ready(preview({ filename: 'synthetic.csv', feedback: undefined, stats: { total: 3, ok: 0, skipped: 3 } }));
  assert.equal(h.element('impMappingSection').hidden, false);
  assert.equal(h.element('impMode').hidden, false);
  assert.equal(h.element('impFeedback').hidden, true);
  assert.equal(h.element('impRe').hidden, false);
  assert.equal(h.element('impCommit').disabled, true);
  await h.commit(); assert.equal(h.requests.length, 0);
});

test('historical v1 review classifications remain visible as retained unresolved records without a human gate', async () => {
  const h = harness(), summary = feedback({ profile: 'funds-trace-bank-feedback-v1', non_monetary: undefined, unresolved: undefined, review: 2,
    unresolved_examples: undefined, review_examples: [{ member_path: 'synthetic/交易.txt', source_row: 4, reason_code: 'negative_unknown' }] });
  await h.ready(preview({ feedback: summary }));
  assert.match(h.element('impFeedback').innerHTML, /未计入资金流水 2 条/);
  assert.match(h.element('impFeedback').innerHTML, /无资金变动 0 条/);
  assert.equal(/待复核|需复核|保存处置结果/.test(h.element('impFeedback').innerHTML), false);
  assert.equal(h.element('impCommit').textContent, '开始导入');
});

test('case totals retain the source interpretation note and discard statistics arriving after a case switch', async () => {
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
  assert.equal(element('#importCoverageNote').hidden, false);
  assert.match(element('#importCoverageNote').textContent, /349 条资金含义未确定的来源条目未计入统计/);
  assert.match(element('#importCoverageNote').textContent, /不代表结算净额/);
  assert.match(element('#importCoverageNote').textContent, /无资金变动的 233 条记录已单独保存/);
  assert.match(element('#importCoverageNote').textContent, /已配对冲正\/撤销 4 条（双方均不计有效资金）/);
  assert.match(element('#importCoverageNote').textContent, /本案另有 2 笔历史留存流水已退出有效统计/);
  const oldLoad = vm.runInContext('refreshStats()', context);
  state.caseId = 2; state.generation += 1; state.stats = {};
  vm.runInContext('updateHeader()', context);
  assert.equal(element('#importCoverageNote').hidden, true);
  assert.equal(element('#importCoverageNote').textContent, '');
  pending({ flows: 80, accounts: 2, import_coverage: { scope: 'source_reported_flows', unresolved_records: 349, non_monetary_records: 233, financial_interpretation_complete: false } });
  await oldLoad;
  assert.equal(navigationSyncs, 2, 'a stale statistics response cannot refresh navigation for the new case');
  assert.equal(element('#importCoverageNote').hidden, true);
  assert.equal(element('#importCoverageNote').textContent, '');
});
