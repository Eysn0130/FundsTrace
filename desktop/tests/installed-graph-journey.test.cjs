'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { fixtureCsv, assertGraph, assertEdgeTypes, assertImportCompletion, safeJourneyFailure, REQUIRED_CHECKS } = require('./installed-graph-journey.cjs');

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

test('neighborhood may hide an endpoint while retaining the full graph and exact tracked facts', () => {
  const snapshot = observed();
  snapshot.renderedNodes = snapshot.renderedNodes.filter(node => node.id !== 'SYN-INST-C');
  snapshot.renderedEdges.pop();
  assert.doesNotThrow(() => assertGraph(snapshot, ['71', '72', '73'], 2, ['SYN-INST-A', 'SYN-INST-B']));
  snapshot.accounts.pop();
  assert.throws(() => assertGraph(snapshot, ['71', '72', '73'], 2, ['SYN-INST-A', 'SYN-INST-B']));
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

test('installed journey reports fixed, distinct mandatory behavior checks', () => {
  assert.equal(new Set(REQUIRED_CHECKS).size, 12);
  assert.ok(REQUIRED_CHECKS.includes('installedSecondTrackVisible'));
  assert.ok(REQUIRED_CHECKS.includes('installedNeighborhoodTrackVisible'));
  assert.ok(REQUIRED_CHECKS.includes('installedCanvasSaveRestoreTypes'));
  assert.ok(REQUIRED_CHECKS.every(name => /^installed[A-Z][A-Za-z]+$/.test(name)));
});

test('failure diagnostics retain only known step and failure enums', () => {
  const error = new Error('private URL and business content must not leave the helper');
  error.installedGraphDiagnostic = { step: 'canvas-submit', kind: 'timeout', payload: error.message };
  assert.deepEqual(safeJourneyFailure(error), { step: 'canvas-submit', kind: 'timeout' });
  for (const detail of [{ step: error.message, kind: 'timeout' }, { step: 'canvas-submit', kind: error.message }]) {
    assert.equal(safeJourneyFailure({ installedGraphDiagnostic: detail }), null);
  }
  assert.equal(safeJourneyFailure(error.message), null);
});
