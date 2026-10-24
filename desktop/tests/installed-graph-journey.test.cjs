'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
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
