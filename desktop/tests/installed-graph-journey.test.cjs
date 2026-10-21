'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { fixtureCsv, assertGraph, assertEdgeTypes, assertNoAnnotation, assertAnnotation, assertAnnotationFollow,
  assertAutomaticLeafLabel, assertPartialViewportLabels, assertImportCompletion, safeJourneyFailure, REQUIRED_CHECKS } = require('./installed-graph-journey.cjs');

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

test('annotation undo or a different canvas must clear both presentation data and rendered text', () => {
  const cleared = { ready: true, annotations: [], renderedAnnotations: [] };
  assert.doesNotThrow(() => assertNoAnnotation(cleared));
  assert.throws(() => assertNoAnnotation({ ...cleared, annotations: annotationObserved().annotations }));
  assert.throws(() => assertNoAnnotation({ ...cleared, renderedAnnotations: annotationObserved().renderedAnnotations }));
  assert.throws(() => assertNoAnnotation({ ...cleared, ready: false }));
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
