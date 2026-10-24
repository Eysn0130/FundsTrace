'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { fixtureCsv, assertGraph, assertEdgeTypes, assertNoAnnotation, assertAnnotation, assertAnnotationFollow,
  assertWorkspaceRestored, assertAutomaticLeafLabel, assertPartialViewportLabels, assertImportCompletion,
  assertAutomaticImportPreview, assertAutomaticImportCompletion, AUTOMATIC_IMPORT_PROBE,
  assertFinderResults, FINDER_PROBE,
  runInstalledGraphJourney, safeJourneyFailure, REQUIRED_CHECKS } = require('./installed-graph-journey.cjs');

function observed() {
  // Independent two-transfer observation with exact decimal strings. The
  // inspector must reject the original defect even if backend counts are right.
  return {
    ready: true,
    accounts: ['SYN-INST-C', 'SYN-INST-A', 'SYN-INST-B'], trackedIds: ['72', '71'],
    edges: [
      { source: 'SYN-INST-B', target: 'SYN-INST-C', ids: ['72'], total: '0.02', cnt: 1,
        amount_totals: [{ currency: 'CNY', amount: '0.02' }] },
      { source: 'SYN-INST-A', target: 'SYN-INST-B', ids: ['71'], total: '12345678901234.01', cnt: 1,
        amount_totals: [{ currency: 'CNY', amount: '12345678901234.01' }] },
    ],
    renderedNodes: ['SYN-INST-C', 'SYN-INST-B', 'SYN-INST-A'].map(id => ({ id, drawnInContainer: true })),
    renderedEdges: [
      { source: 'SYN-INST-A', target: 'SYN-INST-B', ids: ['71'], type: 'quadratic', drawn: true,
        amount_totals: [{ currency: 'CNY', amount: '12345678901234.01' }] },
      { source: 'SYN-INST-B', target: 'SYN-INST-C', ids: ['72'], type: 'quadratic', drawn: true,
        amount_totals: [{ currency: 'CNY', amount: '0.02' }] },
    ],
  };
}

test('installed fixture is three separate chain transfers with exact cents and recognized headers', () => {
  const csv = fixtureCsv();
  assert.ok(csv.startsWith('\uFEFF交易流水号,交易时间,付款户名,付款账号,收款户名,收款账号,交易金额,币种,摘要\r\n'));
  const rows = csv.slice(1).trimEnd().split('\r\n').slice(1).map(line => line.split(','));
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.map(row => [row[3], row[5], row[6]]), [
    ['SYN-INST-A', 'SYN-INST-B', '12345678901234.01'],
    ['SYN-INST-B', 'SYN-INST-C', '0.02'], ['SYN-INST-C', 'SYN-INST-D', '345.67'],
  ]);
});

test('import acceptance requires the visible completed receipt with all three committed transfers', () => {
  const receipt = () => ({ open: true, busy: false, resultVisible: true, commitHidden: true,
    action: '完成', status: '导入完成', steps: ['complete', 'complete', 'complete'],
    summary: '新增 3 笔 · 重复 0 笔 · 跳过 0 行' });
  assert.doesNotThrow(() => assertImportCompletion(receipt()));
  for (const mutate of [
    value => { value.open = false; }, value => { value.busy = true; },
    value => { value.resultVisible = false; }, value => { value.commitHidden = false; },
    value => { value.action = '关闭'; }, value => { value.steps[2] = 'working'; },
    value => { value.summary = '新增 2 笔 · 重复 0 笔 · 跳过 1 行'; },
  ]) {
    const value = receipt(); mutate(value); assert.throws(() => assertImportCompletion(value));
  }
});

function automaticObservation() {
  const transfers = [
    ['SYN-INST-1', '2026-01-01 10:00:00', 'SYN sender 0', 'SYN-INST-A', 'SYN recipient 0', 'SYN-INST-B', '12345678901234.01', 'SYN installed chain first'],
    ['SYN-INST-2', '2026-01-01 10:01:00', 'SYN sender 1', 'SYN-INST-B', 'SYN recipient 1', 'SYN-INST-C', '0.02', 'SYN installed chain second'],
    ['SYN-INST-3', '2026-01-01 10:02:00', 'SYN sender 2', 'SYN-INST-C', 'SYN recipient 2', 'SYN-INST-D', '345.67', 'SYN installed chain third'],
  ];
  return { preview: { automatic: true, mode: 'A',
    mapping: { trade_no: '交易流水号', trade_time: '交易时间', payer_name: '付款户名', payer_account: '付款账号',
      payee_name: '收款户名', payee_account: '收款账号', amount: '交易金额', currency: '币种', summary: '摘要' },
    stats: { total: 3, ok: 3, skipped: 0, currency_totals: { CNY: '12345678901579.70' } },
    preview: transfers.map(([trade_no, trade_time, payer_name, payer_account, payee_name, payee_account, amount, summary], i) =>
      ({ trade_no, trade_time, payer_name, payer_account, payee_name, payee_account, amount, summary,
        currency: 'CNY', source_member: 'table.csv', source_row: i + 2, source_record_no: i + 1 })) },
    headers: ['来源文件／原始行', '交易时间', '付款方', '收款方', '金额', '摘要'],
    rows: [
      ['table.csv原始第 2 行 · 记录 1', '2026-01-01 10:00:00', 'SYN sender 0SYN-INST-A', 'SYN recipient 0SYN-INST-B', '¥12,345,678,901,234.01', 'SYN installed chain first'],
      ['table.csv原始第 3 行 · 记录 2', '2026-01-01 10:01:00', 'SYN sender 1SYN-INST-B', 'SYN recipient 1SYN-INST-C', '¥0.02', 'SYN installed chain second'],
      ['table.csv原始第 4 行 · 记录 3', '2026-01-01 10:02:00', 'SYN sender 2SYN-INST-C', 'SYN recipient 2SYN-INST-D', '¥345.67', 'SYN installed chain third'],
    ], mappingHidden: true,
    receipt: { open: true, busy: false, resultVisible: true, commitHidden: true, action: '完成',
      title: '自动处理完成', status: '自动处理完成，全部记录已保存', steps: ['complete', 'complete', 'complete'],
      summary: '已保存 3 条记录 · 新增资金流水 3 笔 · 重复 0 笔 · 未计入资金流水 0 条 · 已配对冲正/撤销 0 条（双方均不计有效资金） · 无资金变动 0 条 · 非交易记录 0 条 · 辅助资料 0 条' } };
}

test('automatic acceptance checks every mapped transaction, exact total, source row and classified column', () => {
  const value = automaticObservation(), before = structuredClone(value);
  assertAutomaticImportPreview(value); assertAutomaticImportCompletion(value.receipt);
  assert.deepEqual(value, before);
  assert.throws(() => assertImportCompletion(value.receipt));
});

for (const [name, mutate] of [
  ['manual mode', value => { value.preview.automatic = false; }],
  ['missing automatic flag', value => { delete value.preview.automatic; }],
  ['wrong mode', value => { value.preview.mode = 'B'; }],
  ['wrong total', value => { value.preview.stats.total = 4; }],
  ['unaccepted row', value => { value.preview.stats.ok = 2; }],
  ['skipped row', value => { value.preview.stats.skipped = 1; }],
  ['lost total cents', value => { value.preview.stats.currency_totals.CNY = '12345678901579.71'; }],
  ['numeric total', value => { value.preview.stats.currency_totals.CNY = 12345678901579.7; }],
  ['extra currency', value => { value.preview.stats.currency_totals.USD = '0.00'; }],
  ['missing transaction', value => { value.preview.preview.pop(); }],
  ['extra transaction', value => { value.preview.preview.push(structuredClone(value.preview.preview[0])); }],
  ['manual mapping visible', value => { value.mappingHidden = false; }],
  ['old manual columns', value => { value.headers = ['交易时间', '付款方', '收款方', '金额', '摘要', '币种']; }],
  ['missing rendered row', value => { value.rows.pop(); }],
  ['extra rendered row', value => { value.rows.push([...value.rows[0]]); }],
]) test(`automatic preview rejects ${name}`, () => {
  const value = automaticObservation(); mutate(value);
  assert.throws(() => assertAutomaticImportPreview(value));
});

test('all nine automatic mappings must be exact and complete', () => {
  for (const field of Object.keys(automaticObservation().preview.mapping)) {
    for (const remove of [false, true]) {
      const value = automaticObservation();
      if (remove) delete value.preview.mapping[field]; else value.preview.mapping[field] = 'Wrong source column';
      assert.throws(() => assertAutomaticImportPreview(value), `${field}: ${remove ? 'missing' : 'wrong'}`);
    }
  }
  const extra = automaticObservation(); extra.preview.mapping.extra = '金额';
  assert.throws(() => assertAutomaticImportPreview(extra));
});

for (const [field, wrong] of [
  ['trade_no', 'SYN-INST-WRONG'], ['trade_time', '2026-01-02 10:00:00'],
  ['payer_name', 'Wrong payer'], ['payer_account', 'SYN-INST-Z'],
  ['payee_name', 'Wrong payee'], ['payee_account', 'SYN-INST-Z'],
  ['amount', '0.03'], ['currency', 'USD'], ['summary', 'Wrong summary'],
  ['source_member', 'other.csv'], ['source_row', 99], ['source_record_no', 99],
]) test(`automatic preview checks ${field} on every transaction`, () => {
  for (let i = 0; i < 3; i++) {
    const value = automaticObservation(); value.preview.preview[i][field] = wrong;
    assert.throws(() => assertAutomaticImportPreview(value), `row ${i + 1}`);
  }
});

test('numeric amounts, string source indices and consistently reversed roles cannot pass automatic acceptance', () => {
  for (let i = 0; i < 3; i++) {
    for (const [field, wrong] of [['amount', 0.02], ['source_row', String(i + 2)], ['source_record_no', String(i + 1)]]) {
      const value = automaticObservation(); value.preview.preview[i][field] = wrong;
      assert.throws(() => assertAutomaticImportPreview(value));
    }
    const value = automaticObservation(), row = value.preview.preview[i];
    [row.payer_account, row.payee_account] = [row.payee_account, row.payer_account];
    [row.payer_name, row.payee_name] = [row.payee_name, row.payer_name];
    [value.rows[i][2], value.rows[i][3]] = [value.rows[i][3], value.rows[i][2]];
    assert.throws(() => assertAutomaticImportPreview(value));
  }
});

test('each of the six rendered cells must match its source, time, parties, exact money and summary', () => {
  for (let row = 0; row < 3; row++) for (let column = 0; column < 6; column++) {
    const value = automaticObservation(); value.rows[row][column] = 'Wrong rendered value';
    assert.throws(() => assertAutomaticImportPreview(value), `${row}:${column}`);
  }
});

for (const [name, mutate] of [
  ['closed dialog', value => { value.open = false; }], ['busy operation', value => { value.busy = true; }],
  ['hidden result', value => { value.resultVisible = false; }], ['missing result', value => { delete value.resultVisible; }],
  ['visible submit', value => { value.commitHidden = false; }], ['missing submit state', value => { delete value.commitHidden; }],
  ['wrong action', value => { value.action = '关闭'; }], ['wrong title', value => { value.title = '导入完成'; }],
  ['manual status', value => { value.status = '导入完成'; }], ['partial completion', value => { value.steps[2] = 'working'; }],
  ['missing step', value => { value.steps.pop(); }], ['extra step', value => { value.steps.push('complete'); }],
  ['missing receipt summary', value => { delete value.summary; }],
  ['manual receipt summary', value => { value.summary = '新增 3 笔 · 重复 0 笔 · 跳过 0 行'; }],
]) test(`automatic completion rejects ${name}`, () => {
  const receipt = automaticObservation().receipt; mutate(receipt);
  assert.throws(() => assertAutomaticImportCompletion(receipt));
});

test('automatic completion requires every exact saved, added and disposition count', () => {
  for (const [from, to] of [['已保存 3 条记录', '已保存 2 条记录'], ['新增资金流水 3 笔', '新增资金流水 2 笔'],
    ['重复 0 笔', '重复 1 笔'], ['未计入资金流水 0 条', '未计入资金流水 1 条'],
    ['已配对冲正/撤销 0 条', '已配对冲正/撤销 1 条'], ['无资金变动 0 条', '无资金变动 1 条'],
    ['非交易记录 0 条', '非交易记录 1 条'], ['辅助资料 0 条', '辅助资料 1 条']]) {
    const receipt = automaticObservation().receipt; receipt.summary = receipt.summary.replace(from, to);
    assert.throws(() => assertAutomaticImportCompletion(receipt), from);
  }
});

test('automatic probe only observes retained preview and completion DOM, rejecting missing visible results', () => {
  const value = automaticObservation(), before = structuredClone(value);
  const nodes = {
    '#importModal': { style: { display: 'flex' }, getAttribute: name => { assert.equal(name, 'aria-busy'); return 'false'; } },
    '#impResult': { hidden: false }, '#impMappingSection': { hidden: true }, '#impCommit': { hidden: true },
    '#impCancel': { textContent: value.receipt.action }, '#impResultTitle': { textContent: value.receipt.title },
    '#impFlowStatus': { textContent: value.receipt.status }, '#impResultText': { textContent: value.receipt.summary },
  };
  const steps = Object.fromEntries(['impFlowFile', 'impFlowFields', 'impFlowConfirm']
    .map((id, i) => [id, { dataset: { state: value.receipt.steps[i] } }]));
  const document = { querySelector: selector => nodes[selector] || null, getElementById: id => steps[id] || null,
    querySelectorAll: selector => selector === '#impPreviewHead th' ? value.headers.map(textContent => ({ textContent }))
      : selector === '#impTbody tr' ? value.rows.map(row => ({ children: row.map(textContent => ({ textContent })) })) : [] };
  const probe = () => new Function('state', 'document', '"use strict"; return ' + AUTOMATIC_IMPORT_PROBE)({ imp: value.preview }, document);
  const result = probe(); assert.deepEqual(result, value);
  assertAutomaticImportPreview(result); assertAutomaticImportCompletion(result.receipt);
  assert.deepEqual(value, before);
  nodes['#impResult'].hidden = true; assert.throws(() => assertAutomaticImportCompletion(probe().receipt));
  delete nodes['#impResult']; assert.throws(() => assertAutomaticImportCompletion(probe().receipt));
});

for (const pendingReads of [0, 2]) test(`automatic journey waits for completion with ${pendingReads} pending reads and never submits again`, async () => {
  const finished = automaticObservation(), stop = new Error('SYN stop before unchanged graph checks');
  const checks = [], waits = [], clicked = [], files = []; let remaining = pendingReads, closed = false, reachedStop = false;
  const evaluate = async expression => {
    if (expression === AUTOMATIC_IMPORT_PROBE) {
      const value = structuredClone(finished);
      if (remaining-- > 0) { value.receipt.busy = true; value.receipt.resultVisible = false; value.receipt.steps[2] = 'working'; }
      return value;
    }
    if (expression.includes('return summaries.map')) return ['71', '72', '73'];
    if (expression.includes("style.display === 'none'")) return closed;
    if (expression.includes('requestAnimationFrame') || expression.includes('typeof FundsGraphSelection')
        || expression.includes("style.display === 'flex'") || expression.includes('state.lastGraph.nodes.length')) return true;
    assert.fail('Unexpected CSV journey observation');
  };
  const cdp = { send: async (method, params) => {
    if (method === 'DOM.getDocument') return { root: { nodeId: 1 } };
    if (method === 'DOM.querySelector') return { nodeId: 2 };
    if (method === 'DOM.setFileInputFiles') { files.push(params.files[0]); assert.equal(fs.readFileSync(params.files[0], 'utf8'), fixtureCsv()); }
    else assert.equal(method, 'Emulation.setDeviceMetricsOverride');
    return {};
  } };
  await assert.rejects(runInstalledGraphJourney({ cdp, page: { sessionId: 'SYN' }, evaluate,
    clickElement: async expression => {
      clicked.push(expression); assert.ok(!expression.includes('#impCommit'));
      if (expression.includes('#impCancel')) closed = true;
    }, until: async (predicate, timeout) => {
      waits.push({ timeout, closed });
      for (let attempt = 0; attempt < 5; attempt++) if (await predicate()) return true;
      assert.fail('CSV completion did not settle');
    }, check: async (name, action) => {
      if (name === 'installedQueryExact') { reachedStop = true; throw stop; }
      checks.push(name); await action();
    } }), error => reachedStop && error.message === 'INSTALLED_GRAPH_JOURNEY_FAILED'
      && safeJourneyFailure(error)?.step === 'csv-verify' && safeJourneyFailure(error)?.kind === 'runtime');
  assert.deepEqual(checks, ['installedGraphModulesLoaded', 'installedCsvPreviewMapping', 'installedCsvCommittedExact']);
  assert.equal(waits.filter(value => value.timeout === 45000 && !value.closed).length, 1);
  assert.equal(files.length, 1); assert.equal(fs.existsSync(path.dirname(files[0])), false);
  assert.equal(clicked.filter(value => value.includes('#impCancel')).length, 1);
});

function finderObservation(accounts = ['SYN-INST-A', 'SYN-INST-B']) {
  return { finder: { open: true, searched: true, query: 'SYN-INST-', pending: false, error: '', busy: '',
    resultTotal: accounts.length, visibleCount: accounts.length, results: accounts.map(account => ({ account })) },
    open: true, inputValue: 'SYN-INST-', countVisible: true,
    countText: `匹配结果 · ${accounts.length} 个`, allEnabled: true };
}

test('finder accepts exact two, three, four and visible-subset accounts in any order without mutation', () => {
  for (const accounts of [['SYN-INST-A', 'SYN-INST-B'], ['SYN-INST-A', 'SYN-INST-B', 'SYN-INST-C'],
    ['SYN-INST-A', 'SYN-INST-B', 'SYN-INST-C', 'SYN-INST-D'], ['SYN-INST-A', 'SYN-INST-C']]) {
    const visible = Object.freeze([...accounts]), value = finderObservation([...accounts].reverse());
    const before = structuredClone(value);
    Object.freeze(value.finder.results); Object.freeze(value.finder); Object.freeze(value);
    assertFinderResults(value, visible);
    assert.deepEqual(value, before); assert.deepEqual(visible, accounts);
  }
});

for (const [name, change] of [
  ['closed DOM panel', value => { value.open = false; }],
  ['missing controller', value => { value.finder = null; }],
  ['closed controller', value => { value.finder.open = false; }],
  ['unsearched query', value => { value.finder.searched = false; }],
  ['stale controller query', value => { value.finder.query = 'SYN-INST-A'; }],
  ['stale input', value => { value.inputValue = 'SYN-INST-A'; }],
  ['pending query', value => { value.finder.pending = true; }],
  ['controller error', value => { value.finder.error = 'Synthetic error'; }],
  ['blocked controller', value => { value.finder.busy = 'Synthetic busy'; }],
  ['wrong total', value => { value.finder.resultTotal = 3; }],
  ['string total', value => { value.finder.resultTotal = '2'; }],
  ['wrong visible count', value => { value.finder.visibleCount = 1; }],
  ['missing results', value => { delete value.finder.results; }],
  ['nonarray results', value => { value.finder.results = { account: 'SYN-INST-A' }; }],
  ['same-count wrong account', value => { value.finder.results[1].account = 'SYN-INST-Z'; }],
  ['duplicate account', value => { value.finder.results[1].account = 'SYN-INST-A'; }],
  ['missing account', value => { delete value.finder.results[1].account; }],
  ['numeric account', value => { value.finder.results[1].account = 2; }],
  ['empty account', value => { value.finder.results[1].account = ''; }],
  ['null result', value => { value.finder.results[1] = null; }],
  ['fewer results', value => { value.finder.results.pop(); }],
  ['more results', value => { value.finder.results.push({ account: 'SYN-INST-C' }); }],
  ['hidden count', value => { value.countVisible = false; }],
  ['wrong count label', value => { value.countText = '匹配结果 · 3 个'; }],
  ['missing count label', value => { delete value.countText; }],
  ['disabled locate button', value => { value.allEnabled = false; }],
  ['missing locate button', value => { delete value.allEnabled; }],
]) test(`finder result acceptance rejects ${name}`, () => {
  const value = finderObservation(); change(value);
  assert.throws(() => assertFinderResults(value, ['SYN-INST-A', 'SYN-INST-B']));
});

test('finder does not normalize duplicated results or substitute the full tracked graph for the visible subset', () => {
  assert.throws(() => assertFinderResults(finderObservation(['SYN-INST-A', 'SYN-INST-A']), ['SYN-INST-A', 'SYN-INST-A']));
  assert.throws(() => assertFinderResults(finderObservation(['SYN-INST-A', 'SYN-INST-B', 'SYN-INST-C']), ['SYN-INST-A', 'SYN-INST-B']));
});

test('finder probe reads controller plus real panel, input, result count and locate button without requiring candidates', () => {
  const expected = finderObservation(), before = structuredClone(expected);
  const node = properties => ({ hidden: false, getClientRects: () => [{}],
    getBoundingClientRect: () => ({ width: 240, height: 24 }),
    style: { display: 'block', visibility: 'visible', opacity: '1' }, ...properties });
  const nodes = {
    '#findNodePopover': node({}), '#graphFindInput': node({ value: 'SYN-INST-', expanded: 'false' }),
    '#graphFindCount': node({ textContent: '匹配结果 · 2 个' }), '#btnGraphFindAll': node({ disabled: false }),
  };
  const window = { FundsNodeFinderActions: { getState: () => expected.finder } };
  const document = { querySelector: selector => nodes[selector] || null };
  const probe = () => new Function('window', 'document', 'getComputedStyle', '"use strict"; return ' + FINDER_PROBE)
    (window, document, value => value.style);
  const result = probe(); assert.deepEqual(result, expected);
  assertFinderResults(result, ['SYN-INST-B', 'SYN-INST-A']); assert.deepEqual(expected, before);
  for (const selector of Object.keys(nodes)) {
    const original = nodes[selector]; delete nodes[selector];
    assert.throws(() => assertFinderResults(probe(), ['SYN-INST-A', 'SYN-INST-B']), selector);
    nodes[selector] = original;
  }
  delete window.FundsNodeFinderActions;
  assert.throws(() => assertFinderResults(probe(), ['SYN-INST-A', 'SYN-INST-B']));
  window.FundsNodeFinderActions = { getState: () => expected.finder };
  for (const change of [value => { value.hidden = true; }, value => { value.getClientRects = () => []; },
    value => { value.getBoundingClientRect = () => ({ width: 0, height: 0 }); },
    value => { value.style.display = 'none'; }, value => { value.style.visibility = 'hidden'; },
    value => { value.style.opacity = '0'; }]) {
    const original = nodes['#graphFindCount']; nodes['#graphFindCount'] = node({ textContent: '匹配结果 · 2 个' });
    change(nodes['#graphFindCount']);
    assert.throws(() => assertFinderResults(probe(), ['SYN-INST-A', 'SYN-INST-B']));
    nodes['#graphFindCount'] = original;
  }
  nodes['#btnGraphFindAll'].disabled = true;
  assert.throws(() => assertFinderResults(probe(), ['SYN-INST-A', 'SYN-INST-B']));
});

test('graph acceptance compares IDs, endpoints and full decimal amounts', () => {
  assert.doesNotThrow(() => assertGraph(observed(), ['71', '72', '73'], 2));
  for (const mutate of [
    snapshot => { snapshot.edges[1].total = '12345678901234.00'; },
    snapshot => { snapshot.edges[0].amount_totals[0].amount = 0.02; },
    snapshot => { snapshot.edges[0].ids = ['71']; },
    snapshot => { snapshot.trackedIds = ['71', '71']; },
    snapshot => { snapshot.renderedEdges[1].amount_totals[0].currency = 'USD'; },
  ]) {
    const snapshot = observed(); mutate(snapshot);
    assert.throws(() => assertGraph(snapshot, ['71', '72', '73'], 2));
  }
});

test('original false tracked state fails when new G6 node or edge is missing', () => {
  const missingNode = observed(); missingNode.renderedNodes.pop();
  assert.throws(() => assertGraph(missingNode, ['71', '72', '73'], 2));
  const missingEdge = observed(); missingEdge.renderedEdges.pop();
  assert.throws(() => assertGraph(missingEdge, ['71', '72', '73'], 2));
});

test('rendered item presence alone does not pass an offscreen or hidden drawing', () => {
  const offscreen = observed(); offscreen.renderedNodes[0].drawnInContainer = false;
  assert.throws(() => assertGraph(offscreen, ['71', '72', '73'], 2));
  const hidden = observed(); hidden.renderedEdges[0].drawn = false;
  assert.throws(() => assertGraph(hidden, ['71', '72', '73'], 2));
  const loading = observed(); loading.ready = false;
  assert.throws(() => assertGraph(loading, ['71', '72', '73'], 2));
});

test('automatic leaf label acceptance rejects missing, hidden, hovered and colliding labels', () => {
  function snapshot() {
    const value = observed();
    value.viewport = { width: 900, height: 600 };
    value.labelContext = { mode: 'auto', selectedIds: [], previewIds: [], selectedAccount: null,
      hoveredAccount: null, selectedEdge: null, exportDepth: 0 };
    Object.assign(value.renderedNodes[0], { fundsLabel: 'SYN recipient 1\nSYN-INST-C',
      label: 'SYN recipient 1\nSYN-INST-C', bodyBounds: { minX: 400, minY: 100, maxX: 440, maxY: 140 },
      labelShape: { text: 'SYN recipient 1\nSYN-INST-C', drawn: true,
        bounds: { minX: 390, minY: 150, maxX: 450, maxY: 180 } } });
    return value;
  }
  assert.doesNotThrow(() => assertAutomaticLeafLabel(snapshot(), 'SYN-INST-C'));
  for (const mutate of [
    value => { value.renderedNodes[0].label = undefined; },
    value => { value.renderedNodes[0].labelShape = null; },
    value => { value.renderedNodes[0].labelShape.drawn = false; },
    value => { value.renderedNodes[0].labelShape.text = 'truncated label'; },
    value => { value.renderedNodes[0].labelShape.bounds.maxX = 901; },
    value => { value.renderedNodes[0].bodyBounds.maxY = 160; },
    value => { value.renderedEdges[0].labelBounds = { minX: 430, minY: 150, maxX: 480, maxY: 170 }; },
    value => { value.labelContext.mode = 'all'; },
    value => { value.labelContext.hoveredAccount = 'SYN-INST-C'; },
    value => { value.labelContext.selectedIds = ['SYN-INST-C']; },
    value => { value.labelContext.previewIds = ['SYN-INST-C']; },
  ]) {
    const value = snapshot(); mutate(value);
    assert.throws(() => assertAutomaticLeafLabel(value, 'SYN-INST-C'));
  }
});

test('neighborhood may hide an endpoint while retaining the full graph and exact tracked facts', () => {
  const snapshot = observed();
  snapshot.renderedNodes = snapshot.renderedNodes.filter(node => node.id !== 'SYN-INST-C');
  snapshot.renderedEdges.pop();
  assert.doesNotThrow(() => assertGraph(snapshot, ['71', '72', '73'], 2, ['SYN-INST-A', 'SYN-INST-B']));
  snapshot.accounts.pop();
  assert.throws(() => assertGraph(snapshot, ['71', '72', '73'], 2, ['SYN-INST-A', 'SYN-INST-B']));
});

test('partial viewport acceptance requires settled unforced labels on every retained chain node', () => {
  function snapshot() {
    const value = observed();
    value.viewport = { width: 900, height: 600 };
    value.labelContext = { mode: 'auto', selectedIds: [], previewIds: [], selectedAccount: null,
      hoveredAccount: null, selectedEdge: null, exportDepth: 0 };
    value.labelsSettled = true; value.clipFull = false; value.clipNodeIds = ['SYN-INST-B','SYN-INST-C'];
    for (const [index,id] of ['SYN-INST-B','SYN-INST-C'].entries()) {
      const x = 100 + 300 * index, text = `SYN account ${id}`;
      Object.assign(value.renderedNodes.find(node => node.id === id), { fundsLabel: text, label: text,
        bodyBounds: { minX: x, minY: 100, maxX: x + 40, maxY: 140 },
        labelShape: { text, drawn: true, bounds: { minX: x - 20, minY: 150, maxX: x + 60, maxY: 180 } } });
    }
    Object.assign(value.renderedNodes.find(node => node.id === 'SYN-INST-A'), { drawnInContainer: false,
      bodyBounds: { minX: -240, minY: 100, maxX: -200, maxY: 140 } });
    return value;
  }
  assert.doesNotThrow(() => assertPartialViewportLabels(snapshot(), ['71','72','73']));
  for (const mutate of [
    value => { for (const node of value.renderedNodes) { node.label = undefined; node.labelShape = null; } },
    value => { value.renderedNodes.find(node => node.id === 'SYN-INST-B').labelShape.drawn = false; },
    value => { value.clipNodeIds.push('SYN-INST-A'); },
    value => { value.clipFull = true; },
    value => { value.renderedNodes.find(node => node.id === 'SYN-INST-A').drawnInContainer = true; },
    value => { value.renderedNodes.find(node => node.id === 'SYN-INST-C').bodyBounds.minX = -1; },
    value => { value.labelsSettled = false; },
    value => { value.labelContext.hoveredAccount = 'SYN-INST-A'; },
    value => { value.edges[1].total = '12345678901234.00'; },
  ]) {
    const value = snapshot(); mutate(value);
    assert.throws(() => assertPartialViewportLabels(value, ['71','72','73']));
  }
});

test('per-relationship styling rejects changes to another relationship or a missing saved edge', () => {
  const snapshot = observed(); snapshot.renderedEdges[0].type = 'polyline';
  const expected = { 'SYN-INST-A=>SYN-INST-B': 'polyline', 'SYN-INST-B=>SYN-INST-C': 'quadratic' };
  assert.doesNotThrow(() => assertEdgeTypes(snapshot, expected));
  snapshot.renderedEdges[1].type = 'polyline';
  assert.throws(() => assertEdgeTypes(snapshot, expected), 'an old global style change must fail the selected-edge check');
  snapshot.renderedEdges[1].type = 'quadratic'; snapshot.renderedEdges.pop();
  assert.throws(() => assertEdgeTypes(snapshot, expected));
});

function annotationObserved() {
  const value = observed();
  value.viewport = { width: 900, height: 600 };
  value.camera = [1.5,0,0,0,1.5,0,100,50,1];
  for (const [id,x] of [['SYN-INST-A',-60], ['SYN-INST-B',120], ['SYN-INST-C',280]]) {
    Object.assign(value.renderedNodes.find(node => node.id === id), { x, y: 30 });
  }
  value.annotations = [{ id: 'SYN-INST-C', text: 'SYN installed note\nVerified exact cents',
    anchor: 'above', dx: 24, dy: -12, size: 16, fontFamily: 'Microsoft YaHei', color: '#e3a12b' }];
  value.renderedAnnotations = [{ id: 'SYN-INST-C', type: 'text', text: 'SYN installed note\nVerified exact cents',
    fill: '#e3a12b', fontSize: 16, fontFamily: 'Microsoft YaHei', textAlign: 'center', textBaseline: 'bottom',
    x: 24, y: -42, drawn: true, bounds: { minX: 430, minY: 25, maxX: 610, maxY: 70 } }];
  return value;
}
const expectedAnnotation = Object.freeze({ id: 'SYN-INST-C', text: 'SYN installed note\nVerified exact cents',
  anchor: 'above', dx: 24, dy: -12, size: 16, fontFamily: 'Microsoft YaHei', color: '#e3a12b' });

test('annotation acceptance requires the complete saved note and its actual visible G6 text shape', () => {
  assert.doesNotThrow(() => assertAnnotation(annotationObserved(), expectedAnnotation));
  for (const mutate of [
    value => { value.annotations[0].text = 'SYN installed note'; },
    value => { value.annotations[0].anchor = 'below'; },
    value => { value.annotations[0].dx = 0; }, value => { value.annotations[0].dy = 0; },
    value => { value.annotations[0].color = '#000000'; }, value => { value.annotations[0].size = 13; },
    value => { value.annotations[0].fontFamily = 'Arial'; },
    value => { value.renderedAnnotations = []; },
    value => { value.renderedAnnotations.push({ ...value.renderedAnnotations[0] }); },
    value => { value.renderedAnnotations[0].id = 'SYN-INST-B'; },
    value => { value.renderedAnnotations[0].type = 'rect'; },
    value => { value.renderedAnnotations[0].text = 'SYN installed note'; },
    value => { value.renderedAnnotations[0].fill = '#000000'; },
    value => { value.renderedAnnotations[0].fontSize = 13; },
    value => { value.renderedAnnotations[0].fontFamily = 'Arial'; },
    value => { value.renderedAnnotations[0].textBaseline = 'top'; },
    value => { value.renderedAnnotations[0].x = 0; },
    value => { value.renderedAnnotations[0].drawn = false; },
    value => { value.renderedAnnotations[0].bounds = null; },
    value => { value.renderedAnnotations[0].bounds.maxY = 25; },
    value => { value.renderedAnnotations[0].bounds.minX = 1000; value.renderedAnnotations[0].bounds.maxX = 1200; },
    value => { value.ready = false; },
  ]) {
    const value = annotationObserved(); mutate(value);
    assert.throws(() => assertAnnotation(value, expectedAnnotation));
  }
});

test('annotation undo or an empty isolated case must clear both presentation data and rendered text', () => {
  const cleared = { ready: true, annotations: [], renderedAnnotations: [] };
  assert.doesNotThrow(() => assertNoAnnotation(cleared));
  assert.throws(() => assertNoAnnotation({ ...cleared, annotations: annotationObserved().annotations }));
  assert.throws(() => assertNoAnnotation({ ...cleared, renderedAnnotations: annotationObserved().renderedAnnotations }));
  assert.throws(() => assertNoAnnotation({ ...cleared, ready: false }));
});

test('returning to a default or named canvas requires its retained scope, annotation, geometry, style and exact facts', () => {
  for (const canvas of [{ canvasId: '0', canvasName: '默认画布' },
    { canvasId: '9', canvasName: 'SYN named canvas' }]) {
    const before = annotationObserved();
    before.binding = { caseId: '7', caseName: 'SYN retained case', ...canvas };
    before.renderedEdges[0].type = 'line';
    assert.doesNotThrow(() => assertWorkspaceRestored(structuredClone(before), before,
      ['71', '72', '73'], 2, expectedAnnotation));
    for (const mutate of [
      value => { value.binding.caseId = '8'; },
      value => { value.binding.caseName = 'SYN other case'; },
      value => { value.binding.canvasId = canvas.canvasId === '0' ? '9' : '0'; },
      value => { value.binding.canvasName = 'SYN other canvas'; },
      value => { delete value.binding; },
      value => { value.annotations = []; value.renderedAnnotations = []; },
      value => { value.annotations[0].dx = 0; },
      value => { value.renderedNodes[0].x += 1; },
      value => { value.renderedEdges[0].type = 'polyline'; },
      value => { value.edges[1].total = '12345678901234.00'; },
      value => { value.trackedIds.pop(); },
      value => { value.renderedNodes.pop(); },
    ]) {
      const after = structuredClone(before); mutate(after);
      assert.throws(() => assertWorkspaceRestored(after, before, ['71', '72', '73'], 2, expectedAnnotation));
    }
  }
});

test('moving one annotated node translates its actual shape without changing offset, siblings or exact facts', () => {
  function moved() {
    const value = annotationObserved();
    Object.assign(value.renderedNodes.find(node => node.id === 'SYN-INST-C'), { x: 312, y: 54 });
    value.renderedAnnotations[0].bounds = { minX: 478, minY: 61, maxX: 658, maxY: 106 };
    return value;
  }
  assert.doesNotThrow(() => assertAnnotationFollow(annotationObserved(), moved(), expectedAnnotation));
  assertGraph(moved(), ['71','72','73'], 2);
  for (const mutate of [
    value => { Object.assign(value.renderedNodes.find(node => node.id === 'SYN-INST-C'), { x: 280, y: 30 }); },
    value => { value.renderedNodes.find(node => node.id === 'SYN-INST-B').x += 32; },
    value => { value.camera[6] += 48; },
    value => { value.renderedAnnotations[0].y += 24; },
    value => { value.renderedAnnotations[0].bounds = { minX: 430, minY: 25, maxX: 610, maxY: 70 }; },
    value => { value.renderedAnnotations[0].bounds.maxX += 3; },
  ]) {
    const value = moved(); mutate(value);
    assert.throws(() => assertAnnotationFollow(annotationObserved(), value, expectedAnnotation));
  }
  const lostCents = moved(); lostCents.edges[1].total = '12345678901234.00';
  assert.throws(() => assertGraph(lostCents, ['71','72','73'], 2));
});

test('installed journey reports fixed, distinct mandatory behavior checks', () => {
  assert.equal(new Set(REQUIRED_CHECKS).size, 20);
  assert.ok(REQUIRED_CHECKS.includes('installedKeyboardUndoRedoOutsideGraph'));
  assert.ok(REQUIRED_CHECKS.includes('installedSecondTrackVisible'));
  assert.ok(REQUIRED_CHECKS.includes('installedAutomaticNodeLabelRecovery'));
  assert.ok(REQUIRED_CHECKS.includes('installedPartialViewportNodeLabels'));
  assert.ok(REQUIRED_CHECKS.includes('installedNeighborhoodTrackVisible'));
  assert.ok(REQUIRED_CHECKS.includes('installedCanvasSaveRestoreTypes'));
  for (const name of ['installedAnnotationDialogSave', 'installedAnnotationUndoRedo', 'installedAnnotationOffsetDrag',
    'installedAnnotationNodeFollow', 'installedCanvasSaveRestoreAnnotation']) assert.ok(REQUIRED_CHECKS.includes(name));
  assert.ok(REQUIRED_CHECKS.every(name => /^installed[A-Z][A-Za-z]+$/.test(name)));
});

test('failure diagnostics retain only known step and failure enums', () => {
  const error = new Error('private URL and business content must not leave the helper');
  error.installedGraphDiagnostic = { step: 'canvas-submit', kind: 'timeout', payload: error.message };
  assert.deepEqual(safeJourneyFailure(error), { step: 'canvas-submit', kind: 'timeout' });
  assert.deepEqual(safeJourneyFailure({ installedGraphDiagnostic: { step: 'node-label-pan-return', kind: 'assertion' } }),
    { step: 'node-label-pan-return', kind: 'assertion' });
  assert.deepEqual(safeJourneyFailure({ installedGraphDiagnostic: { step: 'node-label-partial-leave', kind: 'assertion' } }),
    { step: 'node-label-partial-leave', kind: 'assertion' });
  for (const step of ['annotation-node','annotation-open','annotation-fill','annotation-save','annotation-rendered',
    'annotation-undo','annotation-redo','annotation-style-reset','annotation-offset','annotation-follow','annotation-restored']) {
    assert.deepEqual(safeJourneyFailure({ installedGraphDiagnostic: { step, kind: 'runtime', payload: error.message } }), { step, kind: 'runtime' });
  }
  for (const detail of [{ step: error.message, kind: 'timeout' }, { step: 'canvas-submit', kind: error.message }]) {
    assert.equal(safeJourneyFailure({ installedGraphDiagnostic: detail }), null);
  }
  assert.equal(safeJourneyFailure(error.message), null);
});

const graphHelperPath = path.join(__dirname, 'installed-graph-journey.cjs');
const graphHelperSource = () => fs.readFileSync(graphHelperPath, 'utf8').replace(/\r\n/g, '\n');
const locationValue = (line, column) => ({ helper: 'installed-graph-journey', line, column });
function assertionLocationReader(filename = graphHelperPath, newline = 'LF') {
  const source = graphHelperSource(), start = source.indexOf('function readAssertionLocation('), end = source.indexOf('function safeJourneyFailure(', start);
  assert.ok(start >= 0 && end > start, 'W09 must provide the internal assertion location reader');
  const reader = source.slice(start, end);
  return vm.runInNewContext(`${newline === 'CRLF' ? reader.replace(/\n/g, '\r\n') : reader};readAssertionLocation`, { __filename: filename });
}
function assertionFrom(action) {
  try { action(); } catch (error) { assert.equal(error.code, 'ERR_ASSERTION'); return error; }
  assert.fail('the independent negative observation must fail its existing assertion');
}

test('real visibility, annotation and restored-coordinate assertions locate their actual helper call sites', () => {
  const read = assertionLocationReader(), source = graphHelperSource().split('\n');
  const invisible = observed(); invisible.renderedNodes[0].drawnInContainer = false;
  const note = annotationObserved(); note.renderedAnnotations[0].text = 'SYN wrong shape';
  const before = annotationObserved(); before.binding = { caseId: '7', caseName: 'SYN case', canvasId: '0', canvasName: '默认画布' };
  const after = structuredClone(before); after.renderedNodes[0].x += 1;
  for (const [action, statement] of [
    [() => assertGraph(invisible, ['71', '72', '73'], 2), 'for (const node of snapshot.renderedNodes) assert.equal(node.drawnInContainer, true);'],
    [() => assertAnnotation(note, expectedAnnotation), 'assert.equal(shape.text, expected.text);'],
    [() => assertWorkspaceRestored(after, before, ['71', '72', '73'], 2, expectedAnnotation), 'assert.deepEqual(nodePositions(snapshot), nodePositions(before));'],
  ]) {
    const error = assertionFrom(action), line = source.findIndex(value => value.includes(statement));
    assert.ok(line >= 0);
    assert.deepEqual(JSON.parse(JSON.stringify(read(error))), locationValue(line + 1, source[line].indexOf('assert.') + 8));
    assert.doesNotMatch(JSON.stringify(read(error)), /SYN|\.cjs|stack|actual|expected|message/);
  }
});

test('the actual location reader accepts exact Posix and Windows frames and rejects forged, ambiguous and unsafe stacks', () => {
  for (const newline of ['LF', 'CRLF']) for (const filename of ['/SYN private folder/installed-graph-journey.cjs', 'C:\\SYN private folder\\installed-graph-journey.cjs']) {
    const read = assertionLocationReader(filename, newline), plain = value => value === null ? null : JSON.parse(JSON.stringify(value));
    const error = (frames, message = 'SYN_PRIVATE_MESSAGE') => {
      const stack = `AssertionError [ERR_ASSERTION]: ${message}\n${frames.join('\n')}`;
      return { message: newline === 'CRLF' ? message.replace(/\n/g, '\r\n') : message,
        stack: newline === 'CRLF' ? stack.replace(/\n/g, '\r\n') : stack };
    };
    for (const [frame, line, column] of [[`    at ${filename}:1:1`, 1, 1], [`    at helper (${filename}:100000:10000)`, 100000, 10000],
      [`    at async ${filename}:42:7`, 42, 7], [`    at async helper (${filename}:12:3)`, 12, 3]]) {
      assert.deepEqual(plain(read(error([frame]))), locationValue(line, column));
    }
    const fakeMessage = `SYN_PRIVATE_NAME_AMOUNT_URL\n    at forged (${filename}:9:9)\nSYN_PRIVATE_END`;
    assert.deepEqual(plain(read(error([`    at real (${filename}:31:8)`], fakeMessage))), locationValue(31, 8));
    assert.deepEqual(plain(read(error([`    at first (${filename}:4:5)`, `    at second (${filename}:6:7)`]))), locationValue(4, 5));
    const capped = error([`    at exact (${filename}:6:7)`]);
    capped.stack += '\n' + 'x'.repeat(65536 - capped.stack.length - 1);
    assert.equal(capped.stack.length, 65536); assert.deepEqual(plain(read(capped)), locationValue(6, 7));
    for (const frames of [[`    at first (${filename}:0:1)`, `    at second (${filename}:6:7)`],
      [`    at first (${filename}:100001:1)`], [`    at first (${filename}:1:10001)`], [`    at first (${filename}:1:0)`],
      [`    at first (${filename}:1.5:2)`], [`    at first (${filename}:-1:2)`],
      [`    at other (/different/installed-graph-journey.cjs:4:5)`], [`    at other (${filename}.bak:4:5)`],
      [`SYN_PRIVATE ${filename}:4:5`], [`    at ${filename}:4:5 trailing`],
      [...Array(20).fill('    at other (/different/helper.cjs:1:1)'), `    at late (${filename}:6:7)`]]) assert.equal(read(error(frames)), null);
    for (const value of [{}, { message: 1, stack: 'SYN_PRIVATE' }, { message: 'SYN_PRIVATE', stack: {} },
      { message: 'SYN_PRIVATE', stack: 'x'.repeat(65537) }, { message: 'SYN_PRIVATE', stack: `Error: SYN_PRIVATE\n    at ${filename}:1:1` },
      { message: 'different', stack: error([`    at ${filename}:1:1`]).stack },
      { get message() { throw new Error('SYN_PRIVATE_GETTER'); }, stack: 'SYN_PRIVATE' },
      { message: 'SYN_PRIVATE', get stack() { throw new Error('SYN_PRIVATE_GETTER'); } }]) assert.equal(read(value), null);
  }
});

test('safe journey diagnostics clone only valid assertion coordinates and omit unsafe getters or extra fields', () => {
  const base = { step: 'graph-visible', kind: 'assertion' }, valid = locationValue(1, 10000);
  const detail = { ...base, assertionLocation: { ...valid, raw: 'SYN_PRIVATE_PATH_STACK_MESSAGE' } };
  const projected = safeJourneyFailure({ installedGraphDiagnostic: detail });
  assert.deepEqual(projected, { ...base, assertionLocation: valid }); assert.notEqual(projected.assertionLocation, detail.assertionLocation);
  for (const location of [null, {}, { ...valid, helper: 'SYN_PRIVATE' }, { ...valid, line: 0 }, { ...valid, line: 100001 },
    { ...valid, column: 0 }, { ...valid, column: 10001 }, { ...valid, line: 1.5 }, { ...valid, column: '1' },
    { ...valid, get line() { throw new Error('SYN_PRIVATE_GETTER'); } }]) {
    assert.deepEqual(safeJourneyFailure({ installedGraphDiagnostic: { ...base, assertionLocation: location } }), base);
  }
  const getter = { ...base, get assertionLocation() { throw new Error('SYN_PRIVATE_GETTER'); } };
  assert.deepEqual(safeJourneyFailure({ installedGraphDiagnostic: getter }), base);
  for (const kind of ['timeout', 'runtime']) assert.deepEqual(safeJourneyFailure({ installedGraphDiagnostic: { ...base, kind, assertionLocation: valid } }), { ...base, kind });
  assert.deepEqual(safeJourneyFailure({ installedGraphDiagnostic: { ...base, assertionLocation: locationValue(100000, 1) } }),
    { ...base, assertionLocation: locationValue(100000, 1) });
  assert.doesNotMatch(JSON.stringify(projected), /SYN_PRIVATE|raw|stack|message|actual|expected/);
});

test('the real journey catch preserves assertion failure and its sole CSV cleanup even when location reading is unavailable', async () => {
  const invisible = observed(); invisible.renderedNodes[0].drawnInContainer = false;
  const actual = assertionFrom(() => assertGraph(invisible, ['71', '72', '73'], 2));
  const lines = graphHelperSource().split('\n'), line = lines.findIndex(value => value.includes('for (const node of snapshot.renderedNodes) assert.equal(node.drawnInContainer, true);'));
  const expectedLocation = locationValue(line + 1, lines[line].indexOf('assert.') + 8);
  let runtimeStackReads = 0;
  const unavailable = { code: 'ERR_ASSERTION', message: 'SYN_PRIVATE', get stack() { throw new Error('SYN_PRIVATE_GETTER'); } };
  const timeout = { message: 'TIMEOUT', get stack() { runtimeStackReads++; throw new Error('SYN_PRIVATE_GETTER'); } };
  for (const original of [actual, unavailable, timeout]) {
    const files = [], finished = automaticObservation();
    const evaluate = async expression => expression === AUTOMATIC_IMPORT_PROBE ? structuredClone(finished)
      : expression.includes('return summaries.map') ? ['71', '72', '73'] : true;
    const cdp = { send: async (method, params) => {
      if (method === 'DOM.getDocument') return { root: { nodeId: 1 } };
      if (method === 'DOM.querySelector') return { nodeId: 2 };
      if (method === 'DOM.setFileInputFiles') files.push(params.files[0]);
      return {};
    } };
    await assert.rejects(runInstalledGraphJourney({ cdp, page: { sessionId: 'SYN' }, evaluate, clickElement: async () => {},
      until: async predicate => assert.equal(await predicate(), true),
      check: async (name, action) => { if (name === 'installedQueryExact') throw original; await action(); } }), failure => {
      assert.equal(failure.message, 'INSTALLED_GRAPH_JOURNEY_FAILED');
      const detail = safeJourneyFailure(failure);
      assert.equal(detail.step, 'csv-verify'); assert.equal(detail.kind, original === timeout ? 'timeout' : 'assertion');
      if (original === actual) assert.deepEqual(detail.assertionLocation, expectedLocation);
      else assert.equal(detail.assertionLocation, undefined);
      assert.doesNotMatch(JSON.stringify(detail), /SYN_PRIVATE|\.cjs|stack|message|actual|expected/); return true;
    });
    assert.equal(files.length, 1); assert.equal(fs.existsSync(path.dirname(files[0])), false);
  }
  assert.equal(runtimeStackReads, 0, 'non-assertion failures never inspect their stack');
});

test('the actual restore sequence restores each parent step only after its graph substep succeeds', async () => {
  const source = graphHelperSource(), start = source.indexOf("        step = 'canvas-default';"), end = source.indexOf("        step = 'annotation-restored';", start);
  assert.ok(start >= 0 && end > start);
  for (const stop of ['default', 'named', 'graph']) {
    const events = [], stopError = new Error('SYN stop after the unchanged restore action');
    const context = vm.createContext({ flowIds: ['71', '72', '73'], savedAnnotation: {}, defaultWorkspace: {}, savedTypes: {}, editedEdge: 'SYN edge', CANVAS: 'SYN canvas', GRAPH_PROBE: 'SYN graph probe', assert,
      select: async (...args) => events.push(['select', ...args]), graph: async () => { context.step = 'graph-visible'; if (stop === 'graph') throw stopError; },
      annotation: async () => ({}), evaluate: async () => true,
      assertWorkspaceRestored: () => { events.push(['default', context.step]); if (stop === 'default') throw stopError; },
      assertEdgeTypes: () => { events.push(['named', context.step]); throw stopError; } });
    await assert.rejects(vm.runInContext(`(async()=>{${source.slice(start, end)}})()`, context), error => error === stopError);
    assert.equal(context.step, stop === 'graph' ? 'graph-visible' : stop === 'default' ? 'canvas-default' : 'canvas-restored');
    if (stop !== 'graph') assert.deepEqual(events.find(event => event[0] === 'default'), ['default', 'canvas-default']);
    if (stop === 'named') assert.deepEqual(events.find(event => event[0] === 'named'), ['named', 'canvas-restored']);
  }
});

const cloneAccounts = ['SYN-INST-A', 'SYN-INST-B', 'SYN-INST-C', 'SYN-INST-D'];
const cloneKeys = cloneAccounts.slice(0, 3).map((id, index) => `${id}=>${cloneAccounts[index + 1]}`);
const cloneBinding = { caseId: '7', canvasId: '9', caseGeneration: 11, canvasGeneration: 3,
  origin: 'http://127.0.0.1:12345', timeOrigin: 812345 };
const cloneCase = 'SYN current installer lifecycle case', cloneCanvas = 'SYN installed saved graph';
const cloneSummary = types => ({ types, exactKeySet: true });
const clonePlain = value => JSON.parse(JSON.stringify(value));
function clonePrefixContext(extras = {}, newline = 'LF') {
  const source = graphHelperSource(), end = source.indexOf('const REQUIRED_CHECKS =');
  const context = vm.createContext({ require, __filename: graphHelperPath, ...extras });
  vm.runInContext(newline === 'CRLF' ? source.slice(0, end).replace(/\n/g, '\r\n') : source.slice(0, end), context);
  for (const name of ['cloneShapeTypes', 'cloneShapePreClone', 'readCloneShapeProbe', 'readCloneShapeObservation', 'safeCloneShape']) {
    assert.equal(typeof context[name], 'function', `W11 must provide ${name}`);
  }
  return context;
}
function cloneRendererFixture(newline = 'LF') {
  const models = cloneKeys.map((_, index) => ({ source: cloneAccounts[index], target: cloneAccounts[index + 1],
    type: ['quadratic', 'line', 'loop'][index], controlPoints: [{ x: 'SYN_PRIVATE_COORDINATE' }], curvePolicy: 'compact-arc' }));
  const timers = [], requests = [], document = {}, graph = { getEdges: () => models.map(model => ({ getModel: () => model })),
    getNodes: () => cloneAccounts.map(id => ({ getModel: () => ({ id }) })) };
  const status = { pending: true, unknown: false, context: { caseId: '7', canvasId: '0', generation: 11, canvasGeneration: 0 } };
  const state = { caseId: 7, canvasId: 9, caseGeneration: 11, canvasGeneration: 3, canvasName: cloneCanvas,
    cases: [{ id: 7, name: cloneCase }], graph, graphLoading: false, graphInited: true, edgeType: 'quadratic',
    edgeTypes: { [cloneKeys[0]]: 'line' }, edgeBendPos: { [cloneKeys[0]]: [{}, {}] },
    graphView: { autoRoutes: { [cloneKeys[0]]: {} }, exportDepth: 0 },
    lastGraph: { nodes: cloneAccounts.map(account => ({ account })), edges: models.map(model => ({ ...model })) } };
  const response = { id: 9, name: cloneCanvas, snapshot: { display: { edgeType: 'polyline' },
    edges: models.map(model => ({ ...model, type: 'polyline', routeManual: true, controlPoints: [{}, {}], curvePolicy: 'obstacle-aware' })) } };
  const actions = { status: () => status }, context = clonePrefixContext({ document, location: { origin: cloneBinding.origin }, performance: { timeOrigin: cloneBinding.timeOrigin }, state,
    window: { FundsCanvasActions: actions, FundsCanvasEditor: { history: { status: () => ({ busy: false }) } } }, FundsCanvasActions: actions,
    layoutJob: null, AbortController,
    setTimeout(callback, ms) { const timer = { callback, ms, cleared: false }; timers.push(timer); return timer; },
    clearTimeout(timer) { timer.cleared = true; } }, newline);
  const fixture = { context, state, models, status, response, timers, requests, mutate: null, mode: null,
    run: expression => vm.runInContext(expression, context),
    probe: () => context.readCloneShapeProbe({ ...cloneBinding }, cloneCase, cloneCanvas, [...cloneAccounts]) };
  context.fetch = async (url, options) => {
    requests.push({ url, options }); assert.equal(url, '/api/canvases/9?case_id=7'); assert.equal(options.method || 'GET', 'GET');
    assert.ok(options.signal); assert.equal(requests.length, 1, 'diagnostics never retry');
    if (fixture.mode === 'network') throw new Error('SYN_PRIVATE_NETWORK');
    if (fixture.mode === 'timeout') return new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(Object.assign(new Error('SYN_PRIVATE_ABORT'), { name: 'AbortError' }))));
    fixture.mutate?.();
    return { ok: fixture.mode !== 'http', json: async () => { if (fixture.mode === 'parse') throw new Error('SYN_PRIVATE_PARSE'); return response; } };
  };
  return fixture;
}
async function cloneBlockFixture({ newline = 'LF', mode = 'type' } = {}) {
  const renderer = cloneRendererFixture(newline), cleanup = [], timers = [];
  const expected = Object.fromEntries(cloneKeys.map(key => [key, 'line']));
  const snapshot = { diagnosticBinding: { ...cloneBinding }, renderedEdges: cloneKeys.map((_, index) => ({ source: cloneAccounts[index],
    target: cloneAccounts[index + 1], type: mode === 'type' && index === 0 ? 'polyline' : 'line' })) };
  if (mode === 'duplicate') snapshot.renderedEdges[2] = { ...snapshot.renderedEdges[0] };
  const preClone = { binding: { caseName: cloneCase, canvasId: '0', canvasName: '默认画布' },
    renderedEdges: snapshot.renderedEdges.map((edge, index) => ({ ...edge, type: ['polyline', 'quadratic', 'loop'][index] })) };
  let graphReads = 0, probeReads = 0, annotations = 0;
  const otherError = assertionFrom(() => assert.equal('SYN_PRIVATE_OTHER_ASSERTION', 'other'));
  const context = clonePrefixContext({ require: name => name === 'node:fs/promises' ? { rm: async (...args) => cleanup.push(args) } : require(name),
    GRAPH_PROBE: 'SYN_GRAPH_PROBE', editedEdge: cloneKeys[0], createdCase: cloneCase, savedAnnotation: {}, flowIds: ['71', '72', '73'], directory: 'SYN_OWNED_DIRECTORY',
    assertEdgeTypes, check: async (name, action) => { assert.equal(name, 'installedCanvasCloneExact'); await action(); },
    type: async () => expected, annotation: async () => { if (++annotations > 1 && mode === 'other') throw otherError; return preClone; },
    clickElement: async () => {}, prompt: async () => {}, graph: async () => {}, until: async predicate => assert.equal(await predicate(), true),
    evaluate: async expression => { if (expression === 'SYN_GRAPH_PROBE') { graphReads++; return snapshot; }
      if (expression.includes('readCloneShapeProbe')) { probeReads++;
        assert.ok(expression.includes(JSON.stringify(cloneBinding.origin)), 'the wrapper serializes the frozen origin');
        assert.ok(expression.includes(JSON.stringify(cloneBinding.timeOrigin)), 'the wrapper serializes the frozen timeOrigin');
        return renderer.run(expression); } return true; },
    setTimeout(callback, ms) { const timer = { callback, ms, cleared: false }; timers.push(timer); return timer; },
    clearTimeout(timer) { timer.cleared = true; } }, newline);
  const source = graphHelperSource(), start = source.indexOf("    await check('installedCanvasCloneExact'"), end = source.indexOf("    await check('installedCanvasSaveRestoreTypes'", start);
  const tailStart = source.lastIndexOf('  } catch (error) {'), tailEnd = source.indexOf('\n}\n\nmodule.exports', tailStart);
  assert.ok(start >= 0 && end > start && tailStart >= 0 && tailEnd > tailStart);
  return { renderer, context, cleanup, timers, snapshot, expected, preClone,
    get graphReads() { return graphReads; }, get probeReads() { return probeReads; },
    run: () => vm.runInContext(`(async()=>{let step=null,cloneTypeFailure=null,cloneFailureSnapshot=null,clonePreClone=null;
      let clonedTypes,defaultWorkspace;try{${source.slice(start, end)}${source.slice(tailStart + 2, tailEnd)}})()`, context) };
}

for (const newline of ['LF', 'CRLF']) test(`the exact clone assertion retains its original failure, snapshot read and cleanup with ${newline} helpers`, async () => {
  const failed = await cloneBlockFixture({ newline });
  await assert.rejects(failed.run(), error => {
    assert.equal(error.message, 'INSTALLED_GRAPH_JOURNEY_FAILED'); const detail = safeJourneyFailure(error);
    assert.equal(detail.step, 'canvas-clone-verify'); assert.equal(detail.kind, 'assertion'); assert.ok(detail.assertionLocation);
    assert.deepEqual(detail.cloneShape.expected, cloneSummary(['line', 'line', 'line']));
    assert.deepEqual(detail.cloneShape.actual, cloneSummary(['polyline', 'line', 'line']));
    assert.deepEqual(detail.cloneShape.preClone, cloneSummary(['polyline', 'quadratic', 'loop'])); return true;
  });
  assert.equal(failed.graphReads, 1); assert.equal(failed.probeReads, 1); assert.equal(failed.renderer.requests.length, 1);
  assert.deepEqual(clonePlain(failed.cleanup), [['SYN_OWNED_DIRECTORY', { recursive: true, force: true }]]);
  assert.ok(failed.timers.some(timer => timer.ms === 4000)); assert.ok(failed.timers.every(timer => timer.cleared));
  for (const mode of ['success', 'duplicate', 'other']) {
    const f = await cloneBlockFixture({ newline, mode });
    if (mode === 'success') await f.run();
    else await assert.rejects(f.run(), error => { assert.equal(error.message, 'INSTALLED_GRAPH_JOURNEY_FAILED'); assert.equal(safeJourneyFailure(error).cloneShape, undefined); return true; });
    assert.equal(f.graphReads, 1); assert.equal(f.probeReads, 0); assert.equal(f.renderer.requests.length, 0); assert.equal(f.cleanup.length, 1);
  }
});

for (const newline of ['LF', 'CRLF']) test(`the one clone probe admits only stable owned snapshots and clears its Abort timer with ${newline} helpers`, async () => {
  const f = cloneRendererFixture(newline); f.mutate = () => { f.status.pending = false; f.models[0].type = 'line'; };
  const value = clonePlain(await f.probe()); assert.equal(value.state, 'available'); assert.equal(value.sourceCaseMatches, true);
  assert.equal(value.pending, true); assert.equal(value.afterRead.pending, false); assert.equal(value.live[0].type, 'quadratic');
  assert.equal(value.afterRead.liveTypes[0], 'line'); assert.equal(value.live[0].manualType, 'line'); assert.equal(value.live[0].bendCount, 2);
  assert.equal(value.saved.state, 'available'); assert.equal(value.saved.edgeType, 'polyline'); assert.equal(value.saved.edges.length, 3);
  assert.equal(f.timers[0].ms, 3000); assert.ok(f.timers.every(timer => timer.cleared)); assert.doesNotMatch(JSON.stringify(value), /SYN_PRIVATE|caseId|canvasId|coordinate|origin|timeOrigin/);
  for (const mutate of [g => { g.context.location.origin = 'https://invalid.example'; }, g => { g.context.performance.timeOrigin++; }]) {
    const g = cloneRendererFixture(newline); mutate(g); const result = await g.probe();
    assert.equal(result.state, 'unavailable'); assert.equal(result.reason, 'binding'); assert.equal(g.requests.length, 0);
  }
  for (const mutate of [g => { g.state.caseGeneration += 2; }, g => { g.state.canvasGeneration++; }, g => { g.context.document = {}; },
    g => { g.context.location.origin = 'https://invalid.example'; }, g => { g.context.performance.timeOrigin++; }, g => { g.state.graph = { ...g.state.graph }; }]) {
    const g = cloneRendererFixture(newline); g.mutate = () => mutate(g); const result = await g.probe();
    assert.equal(result.state, 'unavailable'); assert.equal(result.reason, 'binding'); assert.equal(result.saved.state, 'unavailable');
    assert.ok(g.timers.every(timer => timer.cleared));
  }
  for (const mutate of [g => { g.state.canvasName = 'SYN wrong canvas'; }, g => { g.state.cases[0].name = 'SYN wrong case'; },
    g => { g.models.pop(); }, g => { g.models[2] = { ...g.models[0] }; }, g => { g.state.lastGraph.edges[0].target = 'SYN_PRIVATE_OTHER'; }]) {
    const g = cloneRendererFixture(newline); mutate(g); const result = await g.probe(); assert.equal(result.state, 'unavailable');
    assert.equal(result.reason, 'ownership'); assert.equal(g.requests.length, 0);
  }
  for (const mode of ['network', 'http', 'parse', 'timeout', 'response-id']) {
    const g = cloneRendererFixture(newline); g.mode = mode; if (mode === 'response-id') g.response.id = 10;
    const pending = g.probe(); if (mode === 'timeout') g.timers.find(timer => timer.ms === 3000).callback();
    const result = await pending; assert.equal(result.state, 'unavailable'); assert.equal(result.reason, mode);
    assert.equal(result.saved.state, 'unavailable'); assert.equal(g.requests.length, 1); assert.ok(g.timers.every(timer => timer.cleared));
  }
  const wrong = cloneRendererFixture(newline); wrong.response.snapshot.edges[2] = { ...wrong.response.snapshot.edges[0] };
  const rejected = await wrong.probe(); assert.equal(rejected.saved.state, 'unavailable');
  const missingApi = cloneRendererFixture(newline); delete missingApi.context.fetch;
  const unavailable = await missingApi.probe(); assert.equal(unavailable.state, 'unavailable'); assert.equal(unavailable.reason, 'api');
  const nodeTimers = [], node = clonePrefixContext({ setTimeout(callback, ms) { const timer = { callback, ms, cleared: false }; nodeTimers.push(timer); return timer; }, clearTimeout(timer) { timer.cleared = true; } }, newline);
  let calls = 0; await node.readCloneShapeObservation(async () => { calls++; }, null, cloneCase); assert.equal(calls, 0);
  const pending = node.readCloneShapeObservation(() => new Promise(() => {}), { ...cloneBinding }, cloneCase);
  nodeTimers.find(timer => timer.ms === 4000).callback(); const timedOut = await pending;
  assert.equal(timedOut.state, 'unknown'); assert.equal(timedOut.reason, 'cdp'); assert.ok(nodeTimers.every(timer => timer.cleared));
});

for (const newline of ['LF', 'CRLF']) test(`clone summaries and the final whitelist preserve three slots without private values with ${newline} helpers`, async () => {
  const api = clonePrefixContext({}, newline), exact = Object.fromEntries(cloneKeys.map((key, index) => [key, ['line', 'polyline', 'loop'][index]]));
  assert.deepEqual(clonePlain(api.cloneShapeTypes(exact)), cloneSummary(['line', 'polyline', 'loop']));
  const extra = { ...exact, SYN_PRIVATE_KEY: 'SYN_PRIVATE_VALUE' }; assert.equal(api.cloneShapeTypes(extra).exactKeySet, false);
  const absent = { ...exact }; delete absent[cloneKeys[1]]; assert.deepEqual(clonePlain(api.cloneShapeTypes(absent)), { types: ['line', null, 'loop'], exactKeySet: false });
  const duplicate = { renderedEdges: cloneKeys.map((_, index) => ({ source: cloneAccounts[index], target: cloneAccounts[index + 1], type: 'line' })) };
  duplicate.renderedEdges[2] = { ...duplicate.renderedEdges[0] };
  assert.deepEqual(clonePlain(api.cloneShapePreClone(duplicate)), { types: [null, 'line', null], exactKeySet: false });
  const probe = clonePlain(await cloneRendererFixture(newline).probe());
  const raw = { expected: cloneSummary(['line', 'line', 'line']), actual: cloneSummary(['polyline', 'line', 'line']), preClone: cloneSummary(['quadratic', 'loop', 'line']), probe, private: 'SYN_PRIVATE_PATH' };
  const projected = clonePlain(api.safeCloneShape(raw)); assert.deepEqual(projected.actual, raw.actual); assert.equal(projected.private, undefined);
  const malformed = { ...raw, probe: { private: 'SYN_PRIVATE' } }, degraded = clonePlain(api.safeCloneShape(malformed));
  assert.deepEqual(degraded.actual, raw.actual); assert.equal(degraded.probe.state, 'unavailable'); assert.equal(degraded.probe.reason, 'malformed');
  for (const field of ['expected', 'actual', 'preClone']) assert.ok(!api.safeCloneShape({ ...raw, [field]: { types: ['line', 'line', 'line', 'loop'], exactKeySet: true } }));
  const dirty = structuredClone(raw); dirty.probe.live[0].type = 'SYN_PRIVATE'; dirty.probe.live[0].bendCount = 65;
  dirty.probe.saved.edges[0].curvePolicy = 'SYN_PRIVATE'; dirty.probe.saved.edges[0].controlPointCount = -1;
  const clean = clonePlain(api.safeCloneShape(dirty)); assert.equal(clean.probe.live[0].type, null); assert.equal(clean.probe.live[0].bendCount, null);
  assert.equal(clean.probe.saved.edges[0].curvePolicy, null); assert.equal(clean.probe.saved.edges[0].controlPointCount, null);
  const boundary = structuredClone(raw); boundary.probe.live[0].bendCount = 0; boundary.probe.live[1].controlPointCount = 64;
  boundary.probe.live[2].curvePolicy = 'screen-right'; const admitted = clonePlain(api.safeCloneShape(boundary));
  assert.equal(admitted.probe.live[0].bendCount, 0); assert.equal(admitted.probe.live[1].controlPointCount, 64); assert.equal(admitted.probe.live[2].curvePolicy, 'screen-right');
  const getter = { ...raw, get probe() { throw new Error('SYN_PRIVATE_GETTER'); } }; assert.deepEqual(clonePlain(api.safeCloneShape(getter)).actual, raw.actual);
  assert.ok(!api.safeCloneShape({ ...raw, get actual() { throw new Error('SYN_PRIVATE_GETTER'); } }));
  assert.doesNotMatch(JSON.stringify([projected, clean, degraded]), /SYN_PRIVATE|requestId|caseId|canvasId|controlPoints/);
  for (const [step, kind] of [['canvas-clone-verify', 'assertion'], ['canvas-restored', 'assertion'], ['canvas-clone-verify', 'runtime']]) {
    const detail = safeJourneyFailure({ installedGraphDiagnostic: { step, kind, cloneShape: raw } });
    assert.equal(!!detail.cloneShape, step === 'canvas-clone-verify' && kind === 'assertion');
  }
});
