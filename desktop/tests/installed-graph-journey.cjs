'use strict';
// CI-only journey through the installed renderer. All writes originate from real
// pointer/keyboard/file-input actions. Runtime probes only inspect synthetic facts.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');

const ACCOUNTS = Object.freeze(['SYN-INST-A', 'SYN-INST-B', 'SYN-INST-C', 'SYN-INST-D']);
const AMOUNTS = Object.freeze(['12345678901234.01', '0.02', '345.67']);
const SUMMARIES = Object.freeze(['SYN installed chain first', 'SYN installed chain second', 'SYN installed chain third']);
const MAPPING = Object.freeze({ trade_no: '交易流水号', trade_time: '交易时间', payer_name: '付款户名',
  payer_account: '付款账号', payee_name: '收款户名', payee_account: '收款账号', amount: '交易金额', currency: '币种', summary: '摘要' });
const CANVAS = 'SYN installed saved graph';
const ANNOTATION = Object.freeze({ id: ACCOUNTS[3], text: 'SYN installed note\nVerified exact cents',
  anchor: 'above', dx: 0, dy: 0, size: 16, fontFamily: 'Microsoft YaHei', color: '#e3a12b' });
const JOURNEY_STEPS = new Set(['graph-facts', 'finder-open', 'finder-query', 'finder-locate', 'finder-close',
  'graph-visible', 'node-label-auto', 'node-label-pan-out', 'node-label-pan-return',
  'node-label-partial-zoom', 'node-label-partial', 'node-label-partial-leave', 'node-label-partial-return',
  'edge-pointer', 'style-tab', 'style-select', 'style-confirm', 'canvas-open', 'canvas-fill',
  'canvas-submit', 'canvas-closed', 'canvas-activated', 'canvas-clone-verify', 'canvas-default', 'canvas-restored',
  'history-undo', 'history-redo', 'case-empty', 'case-return',
  'annotation-node', 'annotation-open', 'annotation-fill', 'annotation-save', 'annotation-rendered',
  'annotation-undo', 'annotation-redo', 'annotation-style-reset', 'annotation-offset', 'annotation-follow', 'annotation-restored']);
function safeJourneyFailure(error) {
  const detail = error?.installedGraphDiagnostic;
  return detail && JOURNEY_STEPS.has(detail.step) && ['assertion', 'timeout', 'runtime'].includes(detail.kind)
    ? { step: detail.step, kind: detail.kind } : null;
}
const REQUIRED_CHECKS = Object.freeze([
  'installedGraphModulesLoaded', 'installedCsvPreviewMapping', 'installedCsvCommittedExact',
  'installedQueryExact', 'installedFirstTrackExact', 'installedSecondTrackVisible',
  'installedAutomaticNodeLabelRecovery',
  'installedPartialViewportNodeLabels',
  'installedContextTrackedConsistent', 'installedNeighborhoodTrackVisible',
  'installedAnnotationDialogSave', 'installedAnnotationUndoRedo', 'installedAnnotationOffsetDrag',
  'installedAnnotationNodeFollow', 'installedCanvasSaveRestoreAnnotation',
  'installedCanvasCloneExact', 'installedCanvasSaveRestoreTypes',
  'installedCanvasUndoRedoTypes', 'installedKeyboardUndoRedoOutsideGraph', 'installedCaseIsolationAndReturn',
]);

function fixtureCsv() {
  const header = Object.values(MAPPING);
  const rows = AMOUNTS.map((amount, i) => [
    `SYN-INST-${i + 1}`, `2026-01-01 10:0${i}:00`, `SYN sender ${i}`, ACCOUNTS[i],
    `SYN recipient ${i}`, ACCOUNTS[i + 1], amount, 'CNY', SUMMARIES[i],
  ]);
  return `\uFEFF${[header, ...rows].map(row => row.join(',')).join('\r\n')}\r\n`;
}
function sorted(values) { return [...values].sort(); }
function expectedEdges(flowIds, count) {
  return AMOUNTS.slice(0, count).map((amount, i) => ({ source: ACCOUNTS[i], target: ACCOUNTS[i + 1],
    ids: [flowIds[i]], total: amount, cnt: 1, amount_totals: [{ currency: 'CNY', amount }] }));
}
function assertTrackedGraph(snapshot, flowIds, count) {
  assert.ok(snapshot && snapshot.ready);
  assert.deepEqual(sorted(snapshot.accounts), sorted(ACCOUNTS.slice(0, count + 1)));
  assert.deepEqual(sorted(snapshot.trackedIds), sorted(flowIds.slice(0, count)));
  const facts = snapshot.edges.map(edge => ({ source: edge.source, target: edge.target,
    ids: sorted(edge.ids), total: edge.total, cnt: edge.cnt, amount_totals: edge.amount_totals }));
  assert.deepEqual(facts.sort((a, b) => a.source.localeCompare(b.source)), expectedEdges(flowIds, count));
}
function assertGraph(snapshot, flowIds, count, visibleAccounts = ACCOUNTS.slice(0, count + 1)) {
  assertTrackedGraph(snapshot, flowIds, count);
  assert.deepEqual(sorted(snapshot.renderedNodes.map(node => node.id)), sorted(visibleAccounts));
  const expectedVisible = expectedEdges(flowIds, count).filter(edge => visibleAccounts.includes(edge.source) && visibleAccounts.includes(edge.target));
  assert.deepEqual(sorted(snapshot.renderedEdges.map(edge => `${edge.source}=>${edge.target}`)),
    sorted(expectedVisible.map(edge => `${edge.source}=>${edge.target}`)));
  for (const node of snapshot.renderedNodes) assert.equal(node.drawnInContainer, true);
  for (const edge of snapshot.renderedEdges) {
    const fact = expectedVisible.find(item => item.source === edge.source && item.target === edge.target);
    assert.deepEqual(edge.ids, fact.ids);
    assert.deepEqual(edge.amount_totals, fact.amount_totals);
    assert.equal(edge.drawn, true);
  }
}
function renderedEdgeTypes(snapshot) {
  const entries = snapshot.renderedEdges.map(edge => [`${edge.source}=>${edge.target}`, edge.type]);
  assert.equal(new Set(entries.map(([key]) => key)).size, entries.length);
  return Object.fromEntries(entries);
}
function assertEdgeTypes(snapshot, expected) {
  assert.deepEqual(renderedEdgeTypes(snapshot), expected);
}
function assertNoAnnotation(snapshot) {
  assert.equal(snapshot.ready, true);
  assert.deepEqual(snapshot.annotations, []);
  assert.deepEqual(snapshot.renderedAnnotations, []);
}
function assertAnnotation(snapshot, expected) {
  assert.equal(snapshot.ready, true);
  assert.deepEqual(snapshot.annotations, [expected]);
  assert.equal(snapshot.renderedAnnotations.length, 1);
  const shape = snapshot.renderedAnnotations[0];
  assert.equal(shape.id, expected.id);
  assert.equal(shape.type, 'text');
  assert.equal(shape.text, expected.text);
  assert.equal(shape.fill, expected.color);
  assert.equal(shape.fontSize, expected.size);
  assert.equal(shape.fontFamily, expected.fontFamily);
  assert.equal(shape.textAlign, 'center');
  assert.equal(shape.textBaseline, expected.anchor === 'above' ? 'bottom' : 'top');
  assert.equal(shape.x, expected.dx);
  assert.ok(Number.isFinite(shape.y));
  assert.equal(shape.drawn, true);
  const bounds = shape.bounds;
  assert.ok(bounds && Object.values(bounds).every(Number.isFinite)
    && bounds.maxX > bounds.minX && bounds.maxY > bounds.minY);
  assert.ok(bounds.maxX > 0 && bounds.minX < snapshot.viewport.width
    && bounds.maxY > 0 && bounds.minY < snapshot.viewport.height);
}
function assertAnnotationFollow(before, after, expected) {
  assertAnnotation(before, expected); assertAnnotation(after, expected);
  assert.deepEqual(after.camera, before.camera);
  const positions = value => Object.fromEntries(value.renderedNodes.map(node => [node.id, { x: node.x, y: node.y }]));
  const oldNodes = positions(before), newNodes = positions(after);
  assert.deepEqual(sorted(Object.keys(newNodes)), sorted(Object.keys(oldNodes)));
  const oldNode = oldNodes[expected.id], newNode = newNodes[expected.id];
  assert.ok(oldNode && newNode && Math.hypot(newNode.x - oldNode.x, newNode.y - oldNode.y) > 4);
  for (const id of Object.keys(oldNodes)) if (id !== expected.id) assert.deepEqual(newNodes[id], oldNodes[id]);
  const oldShape = before.renderedAnnotations[0], newShape = after.renderedAnnotations[0];
  assert.equal(newShape.x, oldShape.x); assert.ok(Math.abs(newShape.y - oldShape.y) < .01);
  const dx = (newNode.x - oldNode.x) * before.camera[0] + (newNode.y - oldNode.y) * before.camera[3];
  const dy = (newNode.x - oldNode.x) * before.camera[1] + (newNode.y - oldNode.y) * before.camera[4];
  for (const key of ['minX', 'maxX']) assert.ok(Math.abs(newShape.bounds[key] - oldShape.bounds[key] - dx) < .75);
  for (const key of ['minY', 'maxY']) assert.ok(Math.abs(newShape.bounds[key] - oldShape.bounds[key] - dy) < .75);
}
function assertAutomaticNodeLabel(snapshot, id) {
  assert.equal(snapshot.ready, true);
  assert.deepEqual(snapshot.labelContext, { mode: 'auto', selectedIds: [], previewIds: [],
    selectedAccount: null, hoveredAccount: null, selectedEdge: null, exportDepth: 0 });
  const node = snapshot.renderedNodes.find(item => item.id === id);
  assert.ok(node && node.drawnInContainer);
  assert.ok(typeof node.fundsLabel === 'string' && node.fundsLabel.length > 0);
  assert.equal(node.label, node.fundsLabel);
  assert.equal(node.labelShape?.text, node.fundsLabel);
  assert.equal(node.labelShape?.drawn, true);
  const bounds = node.labelShape.bounds, viewport = snapshot.viewport;
  assert.ok(bounds && Object.values(bounds).every(Number.isFinite)
    && bounds.maxX > bounds.minX && bounds.maxY > bounds.minY);
  assert.ok(bounds.minX >= 4 && bounds.maxX <= viewport.width - 4
    && bounds.minY >= 4 && bounds.maxY <= viewport.height - 4);
  const intersects = other => other && bounds.minX <= other.maxX && bounds.maxX >= other.minX
    && bounds.minY <= other.maxY && bounds.maxY >= other.minY;
  // This leaf's label must have room in automatic mode. Requiring its actual
  // painted envelope to miss bodies and relationship text avoids demanding a
  // label which normal collision policy is allowed to suppress.
  assert.equal(snapshot.renderedNodes.some(item => intersects(item.bodyBounds)), false);
  assert.equal(snapshot.renderedEdges.some(item => intersects(item.labelBounds)), false);
}
function assertAutomaticLeafLabel(snapshot, id) {
  assertAutomaticNodeLabel(snapshot, id);
  assert.equal(snapshot.edges.filter(edge => edge.source === id || edge.target === id).length, 1);
}
function assertPartialViewportLabels(snapshot, flowIds) {
  assertTrackedGraph(snapshot, flowIds, 2);
  assert.equal(snapshot.labelsSettled, true);
  assert.equal(snapshot.clipFull, false);
  assert.deepEqual(sorted(snapshot.clipNodeIds), sorted(ACCOUNTS.slice(1, 3)));
  assert.deepEqual(sorted(snapshot.renderedNodes.map(node => node.id)), sorted(ACCOUNTS.slice(0, 3)));
  const outside = snapshot.renderedNodes.find(node => node.id === ACCOUNTS[0]);
  assert.equal(outside.drawnInContainer, false);
  for (const id of ACCOUNTS.slice(1, 3)) {
    assertAutomaticNodeLabel(snapshot, id);
    const body = snapshot.renderedNodes.find(node => node.id === id).bodyBounds;
    assert.ok(body && body.minX >= 4 && body.minY >= 4
      && body.maxX <= snapshot.viewport.width - 4 && body.maxY <= snapshot.viewport.height - 4);
  }
}
function assertImportCompletion(receipt) {
  assert.equal(receipt.open, true);
  assert.equal(receipt.busy, false);
  assert.equal(receipt.resultVisible, true);
  assert.equal(receipt.commitHidden, true);
  assert.equal(receipt.action, '完成');
  assert.equal(receipt.status, '导入完成');
  assert.deepEqual(receipt.steps, ['complete', 'complete', 'complete']);
  assert.equal(receipt.summary, '新增 3 笔 · 重复 0 笔 · 跳过 0 行');
}

// No calls to trackFlows, renderGraph, snapshots, history travel or other product
// actions are allowed here. G6 access is observation of the actual rendered items.
const GRAPH_PROBE = `(() => {
  const box = document.querySelector('#graphContainer').getBoundingClientRect();
  const bounds = shape => {
    const value = shape?.getCanvasBBox?.();
    return value && [value.minX,value.minY,value.maxX,value.maxY].every(Number.isFinite)
      ? { minX: value.minX, minY: value.minY, maxX: value.maxX, maxY: value.maxY } : null;
  };
  const drawn = item => {
    const group = item.getContainer(), shape = item.getKeyShape(), bounds = shape?.getCanvasBBox?.();
    return !!shape && !shape.get('destroyed') && shape.get('visible') !== false
      && group.get('visible') !== false && (bounds?.width > 0 || bounds?.height > 0);
  };
  return { ready: !state.graphLoading && state.graphInited && !layoutJob
      && !window.FundsCanvasEditor.history.status().busy,
    viewport: { width: box.width, height: box.height },
    zoom: state.graph.getZoom(),
    labelsSettled: state.graphView.viewportFrame == null && state.graphView.labelTimer == null && !state.graphView.zoomSettling,
    clipNodeIds: [...state.graphView.clipResult?.nodeIds || []].map(String), clipFull: state.graphView.clipResult?.full === true,
    camera: Array.from(state.graph.get('group').getMatrix() || [1,0,0,0,1,0,0,0,1]),
    labelContext: { mode: state.graphView.labelMode, selectedIds: [...state.graphSelection.ids].map(String).sort(),
      previewIds: [...state.graphView.previewAccounts].map(String).sort(), selectedAccount: state.graphView.selectedAccount,
      hoveredAccount: state.graphView.hoveredAccount, selectedEdge: state.graphView.selectedEdge,
      exportDepth: state.graphView.exportDepth },
    accounts: state.lastGraph.nodes.map(node => node.account),
    trackedIds: [...state.tracked].map(String),
    annotations: (state.presentation?.annotations || []).map(note => ({ id: note.id, text: note.text,
      anchor: note.anchor, dx: note.dx, dy: note.dy, size: note.size, fontFamily: note.fontFamily, color: note.color })),
    renderedAnnotations: state.graph.getNodes().flatMap(item => item.getContainer().get('children')
      .filter(shape => shape.get('name') === 'funds-node-annotation').map(shape => ({
        id: item.getModel().id, type: shape.get('type'), text: shape.attr('text'), fill: shape.attr('fill'),
        fontSize: shape.attr('fontSize'), fontFamily: shape.attr('fontFamily'),
        textAlign: shape.attr('textAlign'), textBaseline: shape.attr('textBaseline'),
        x: shape.attr('x'), y: shape.attr('y'), bounds: bounds(shape),
        drawn: !shape.get('destroyed') && shape.get('visible') !== false && item.getContainer().get('visible') !== false,
      }))),
    edges: state.lastGraph.edges.map(edge => ({ source: edge.source, target: edge.target,
      ids: edge.ids.map(String), total: edge.total, cnt: edge.cnt, amount_totals: edge.amount_totals })),
    renderedNodes: state.graph.getNodes().map(item => {
      const model = item.getModel(), client = state.graph.getClientByPoint(model.x, model.y);
      const text = item.getContainer().get('children').find(shape => shape.get('name') === 'text-shape');
      return { id: model.id, drawnInContainer: drawn(item) && client.x >= box.left && client.x <= box.right
        && client.y >= box.top && client.y <= box.bottom, x: model.x, y: model.y,
        fundsLabel: model.fundsLabel, label: model.label, bodyBounds: bounds(item.getKeyShape()),
        labelShape: text ? { text: text.attr('text'), bounds: bounds(text),
          drawn: !text.get('destroyed') && text.get('visible') !== false
            && item.getContainer().get('visible') !== false } : null };
    }),
    renderedEdges: state.graph.getEdges().map(item => {
      const model = item.getModel(); return { source: model.source, target: model.target, type: model.type,
        ids: model.data.ids.map(String), amount_totals: model.data.amount_totals, drawn: drawn(item),
        labelBounds: bounds(item.getContainer().get('children').find(shape => shape.get('name') === 'text-shape')) };
    }) };
})()`;

async function runInstalledGraphJourney({ cdp, page, evaluate, clickElement, until, check, createdCase, seedCase, captureGraph }) {
  let step = null, flowIds;
  const key = async (name, code, virtual, modifiers = 0) => {
    for (const type of ['keyDown', 'keyUp']) await cdp.send('Input.dispatchKeyEvent', {
      type, key: name, code, windowsVirtualKeyCode: virtual, nativeVirtualKeyCode: virtual, modifiers,
    }, page.sessionId);
  };
  async function fill(selector, value) {
    await clickElement(`document.querySelector(${JSON.stringify(selector)})`);
    await key('a', 'KeyA', 65, 2);
    await cdp.send('Input.insertText', { text: value }, page.sessionId);
    assert.equal(await evaluate(`document.querySelector(${JSON.stringify(selector)}).value === ${JSON.stringify(value)}`), true);
  }
  async function select(selector, value, byText = false) {
    const index = await evaluate(`(() => {
      const options = [...document.querySelector(${JSON.stringify(selector)}).options].filter(option => !option.disabled && !option.hidden);
      return options.findIndex(option => ${byText ? 'option.textContent' : 'option.value'} === ${JSON.stringify(String(value))});
    })()`);
    assert.ok(Number.isInteger(index) && index >= 0);
    await clickElement(`document.querySelector(${JSON.stringify(selector)})`);
    await key('Home', 'Home', 36);
    for (let i = 0; i < index; i += 1) await key('ArrowDown', 'ArrowDown', 40);
    await key('Enter', 'Enter', 13);
    await until(() => evaluate(`(() => { const node = document.querySelector(${JSON.stringify(selector)});
      return ${byText ? 'node.selectedOptions[0]?.textContent' : 'node.value'} === ${JSON.stringify(String(value))}; })()`), 10000);
  }
  async function graph(count, ids, visible = ACCOUNTS.slice(0, count + 1), { frame = true } = {}) {
    if (frame) {
      // Tracking deliberately preserves the camera. Find > Locate all is the
      // user's explicit framing action, without changing selection or members.
      step = 'graph-facts';
      await until(async () => {
        try { assertTrackedGraph(await evaluate(GRAPH_PROBE), ids, count); return true; }
        catch { return false; }
      }, 20000);
      const selectionProbe = `({ nodes: [...state.graphSelection.ids].map(String).sort(), edge: state.graphView.selectedEdge })`;
      const selection = await evaluate(selectionProbe);
      step = 'finder-open';
      await clickElement(`document.querySelector('#ribbonTab-common')`);
      await clickElement(`document.querySelector('#btnFindNode')`);
      await until(() => evaluate(`!document.querySelector('#findNodePopover').hidden`), 10000);
      step = 'finder-query';
      await fill('#graphFindInput', 'SYN-INST-');
      await clickElement(`document.querySelector('#btnGraphFindSearch')`);
      await until(() => evaluate(`document.querySelectorAll('#graphFindResults .find-result').length === ${visible.length}
        && !document.querySelector('#btnGraphFindAll').disabled`), 10000);
      step = 'finder-locate';
      await clickElement(`document.querySelector('#btnGraphFindAll')`);
      await until(() => evaluate(`document.querySelector('#graphFindStatus').dataset.state === 'done'
        && document.querySelector('#findNodePopover').getAttribute('aria-busy') === 'false'
        && document.querySelector('#graphFindStatus').textContent === ${JSON.stringify(`已定位全部 ${visible.length} 个账户`)}`), 15000);
      step = 'finder-close';
      await clickElement(`document.querySelector('#btnGraphFindClose')`);
      await until(() => evaluate(`document.querySelector('#findNodePopover').hidden`), 10000);
      assert.deepEqual(await evaluate(selectionProbe), selection);
      assertTrackedGraph(await evaluate(GRAPH_PROBE), ids, count);
    }
    step = 'graph-visible';
    await until(async () => {
      try { assertGraph(await evaluate(GRAPH_PROBE), ids, count, visible); return true; }
      catch { return false; }
    }, 20000);
    assertGraph(await evaluate(GRAPH_PROBE), ids, count, visible);
  }
  async function dragCanvas(from, to) {
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...from }, page.sessionId);
    await evaluate(`new Promise(resolve => requestAnimationFrame(resolve))`);
    assert.equal(await evaluate(`state.graphView.hoveredAccount === null`), true);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', ...from, button: 'left', buttons: 1, clickCount: 1 }, page.sessionId);
    for (let i = 1; i <= 12; i += 1) {
      const point = { x: from.x + (to.x - from.x) * i / 12, y: from.y + (to.y - from.y) * i / 12 };
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...point, button: 'left', buttons: 1 }, page.sessionId);
      await evaluate(`new Promise(resolve => requestAnimationFrame(resolve))`);
      assert.equal(await evaluate(`state.graphView.hoveredAccount === null`), true);
    }
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...to, button: 'left', buttons: 0, clickCount: 1 }, page.sessionId);
  }
  async function automaticNodeLabelRecovery(ids) {
    const id = ACCOUNTS[2]; // The newly appended endpoint, with only one relationship.
    step = 'node-label-auto';
    await until(async () => {
      try { assertAutomaticLeafLabel(await evaluate(GRAPH_PROBE), id); return true; }
      catch { return false; }
    }, 10000);
    const before = await evaluate(GRAPH_PROBE);
    assertAutomaticLeafLabel(before, id);
    // Pan the leaf towards its outer side of the graph, leaving another node
    // inside G6's canvas-drag boundary. Pick a blank parallel strip so neither
    // direction can sweep the pointer over a node and force its label by hover.
    const strip = await evaluate(`(() => {
      const container = document.querySelector('#graphContainer'), box = container.getBoundingClientRect();
      const envelopes = [...state.graph.getNodes(), ...state.graph.getEdges()]
        .flatMap(item => item.getContainer().get('children').map(shape => shape.getCanvasBBox?.()))
        .filter(bounds => bounds && [bounds.minX,bounds.minY,bounds.maxX,bounds.maxY].every(Number.isFinite));
      const target = state.graph.findById(${JSON.stringify(id)}).getContainer().getCanvasBBox();
      const extents = { minX: Math.min(...envelopes.map(bounds => bounds.minX)),
        maxX: Math.max(...envelopes.map(bounds => bounds.maxX)),
        minY: Math.min(...envelopes.map(bounds => bounds.minY)), maxY: Math.max(...envelopes.map(bounds => bounds.maxY)) };
      const directions = [
        { axis: 'x', sign: 1, room: target.minX - extents.minX, distance: box.width - target.minX + 80 },
        { axis: 'x', sign: -1, room: extents.maxX - target.maxX, distance: target.maxX + 80 },
        { axis: 'y', sign: 1, room: target.minY - extents.minY, distance: box.height - target.minY + 80 },
        { axis: 'y', sign: -1, room: extents.maxY - target.maxY, distance: target.maxY + 80 },
      ].sort((a,b) => b.room - a.room);
      for (const direction of directions) {
        const horizontal = direction.axis === 'x', length = horizontal ? box.width : box.height;
        const crossLength = horizontal ? box.height : box.width;
        for (const cross of [24, crossLength - 24, 56, crossLength - 56]) {
          if (envelopes.some(bounds => horizontal
            ? cross >= bounds.minY - 12 && cross <= bounds.maxY + 12
            : cross >= bounds.minX - 12 && cross <= bounds.maxX + 12)) continue;
          const low = { x: Math.round(box.left + (horizontal ? length * .2 : cross)),
            y: Math.round(box.top + (horizontal ? cross : length * .2)) };
          const high = { x: Math.round(box.left + (horizontal ? length * .8 : cross)),
            y: Math.round(box.top + (horizontal ? cross : length * .8)) };
          if ([low,high].every(point => document.elementFromPoint(point.x,point.y)?.tagName === 'CANVAS'
            && container.contains(document.elementFromPoint(point.x,point.y)))) return { ...direction, low, high };
        }
      }
      return null;
    })()`);
    assert.ok(strip, 'the installed graph must have a reachable blank canvas drag strip');
    step = 'node-label-pan-out';
    const start = strip.sign > 0 ? strip.low : strip.high, end = strip.sign > 0 ? strip.high : strip.low;
    const capacity = Math.abs(end[strip.axis] - start[strip.axis]), gestures = [];
    // The first moving event initializes G6's origin. Compensate its one-step
    // delta while leaving an 80-pixel margin beyond the visible canvas.
    for (let remaining = strip.distance * 12 / 11; remaining > 0;) {
      const amount = Math.min(capacity, remaining), to = { ...start, [strip.axis]: start[strip.axis] + strip.sign * amount };
      gestures.push({ from: start, to }); await dragCanvas(start, to); remaining -= amount;
    }
    await until(() => evaluate(`(() => {
      const item = state.graph.findById(${JSON.stringify(id)}), model = item.getModel();
      const point = state.graph.getClientByPoint(model.x,model.y), box = document.querySelector('#graphContainer').getBoundingClientRect();
      return (point.x < box.left || point.x > box.right || point.y < box.top || point.y > box.bottom)
        && model.label === undefined;
    })()`), 10000);
    assertTrackedGraph(await evaluate(GRAPH_PROBE), ids, 2);
    step = 'node-label-pan-return';
    for (const gesture of gestures.reverse()) await dragCanvas(gesture.to, gesture.from);
    await until(async () => {
      try { assertAutomaticLeafLabel(await evaluate(GRAPH_PROBE), id); return true; }
      catch { return false; }
    }, 10000);
    const after = await evaluate(GRAPH_PROBE);
    assertAutomaticLeafLabel(after, id);
    assertGraph(after, ids, 2);
    assert.deepEqual(after.renderedNodes.map(node => [node.id,node.x,node.y]),
      before.renderedNodes.map(node => [node.id,node.x,node.y]));
    assert.equal(after.camera.length, before.camera.length);
    // G6 begins a drag on its first moving event, which may consume that
    // event's delta. Allow one pointer step of return error while requiring
    // identical zoom/rotation and no perpendicular camera movement.
    const dragStep = capacity / 12, translation = strip.axis === 'x' ? 6 : 7;
    after.camera.forEach((value, i) => assert.ok(Math.abs(value - before.camera[i]) <= (i === translation ? dragStep + 1 : .01)));
  }
  async function partialViewportNodeLabels(ids, appendZoom) {
    step = 'node-label-partial-zoom';
    // Normal public Fit establishes a useful reading frame. The wheel target
    // also exceeds the zoom at which the append originally built its geometry,
    // so shrinking and rebuilding cannot mask a cached screen-shadow defect.
    await clickElement(`document.querySelector('#ribbonTab-view')`);
    await clickElement(`document.querySelector('#btnFit')`);
    await until(() => evaluate(`!layoutJob && state.graphView.viewportFrame == null
      && state.graphView.labelTimer == null && !state.graphView.zoomSettling`), 10000);
    const before = await evaluate(GRAPH_PROBE);
    assertGraph(before, ids, 2);
    for (const id of ACCOUNTS.slice(0, 3)) assertAutomaticNodeLabel(before, id);
    const envelopesBeforeWheel = await evaluate(`Object.fromEntries(state.graph.getNodes().map(item => {
      const bounds = item.getContainer().getCanvasBBox();
      const a = state.graph.getPointByCanvas(bounds.minX,bounds.minY), b = state.graph.getPointByCanvas(bounds.maxX,bounds.maxY);
      return [item.getModel().id,{ minX: a.x, minY: a.y, maxX: b.x, maxY: b.y }];
    }))`);
    const positions = snapshot => snapshot.renderedNodes.map(node => [node.id,node.x,node.y]);
    const unchanged = snapshot => {
      assertTrackedGraph(snapshot, ids, 2);
      assert.deepEqual(positions(snapshot), positions(before));
    };
    const targetZoom = Math.max(appendZoom, before.zoom) * 1.4;
    assert.ok(Number.isFinite(targetZoom) && targetZoom <= 4, 'the wheel regression needs a reading frame below the maximum zoom');
    // Anchor near the retained leaf, in blank space. Scaling away from this
    // point leaves that end of the chain readable while the remote end moves
    // towards the viewport boundary. No node is hovered to force its label.
    const anchor = await evaluate(`(() => {
      const container = document.querySelector('#graphContainer'), box = container.getBoundingClientRect();
      const item = state.graph.findById(${JSON.stringify(ACCOUNTS[0])}), model = item.getModel();
      const center = state.graph.getClientByPoint(model.x,model.y);
      const bounds = state.graph.getNodes().flatMap(node => node.getContainer().get('children')
        .map(shape => shape.getCanvasBBox?.())).filter(Boolean);
      for (const [dx,dy] of [[0,-48],[0,48],[-48,0],[48,0],[0,-96],[0,96]]) {
        const point = { x: Math.round(center.x + dx), y: Math.round(center.y + dy) };
        const x = point.x - box.left, y = point.y - box.top;
        if (x < 24 || x > box.width - 24 || y < 24 || y > box.height - 24
          || bounds.some(bound => x >= bound.minX - 8 && x <= bound.maxX + 8 && y >= bound.minY - 8 && y <= bound.maxY + 8)) continue;
        const hit = document.elementFromPoint(point.x,point.y);
        if (hit?.tagName === 'CANVAS' && container.contains(hit)) return point;
      }
      return null;
    })()`);
    assert.ok(anchor, 'the retained leaf must have a reachable blank wheel anchor');
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...anchor }, page.sessionId);
    for (let i = 0; i < 12 && (await evaluate(`state.graph.getZoom()`)) < targetZoom; i += 1) {
      const previousZoom = await evaluate(`state.graph.getZoom()`);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseWheel', ...anchor, deltaX: 0, deltaY: -120 }, page.sessionId);
      await until(() => evaluate(`state.graph.getZoom() > ${previousZoom}
        && state.graphView.viewportFrame == null && state.graphView.labelTimer == null && !state.graphView.zoomSettling`), 10000);
      assert.equal(await evaluate(`state.graphView.hoveredAccount === null`), true);
    }
    const zoomed = await evaluate(GRAPH_PROBE);
    assert.ok(zoomed.zoom >= targetZoom);
    assert.equal(zoomed.labelsSettled, true);
    unchanged(zoomed);
    for (const id of ACCOUNTS.slice(0, 2)) assertAutomaticNodeLabel(zoomed, id);
    // The original A/B transfer has the long amount label and enough spacing
    // to clip A while retaining B/C. Only accept a pan which sends A beyond
    // the 120-pixel clipping margin and keeps complete B/C labels in the canvas.
    // A monotonic translation between these bounds retains them for the whole
    // gesture, rather than briefly showing them only after all nodes return.
    const plan = await evaluate(`(() => {
      const container = document.querySelector('#graphContainer'), box = container.getBoundingClientRect();
      const items = state.graph.getNodes(), envelopes = [...items,...state.graph.getEdges()]
        .flatMap(item => item.getContainer().get('children').map(shape => shape.getCanvasBBox?.()))
        .filter(bound => bound && [bound.minX,bound.minY,bound.maxX,bound.maxY].every(Number.isFinite));
      const references = ${JSON.stringify(envelopesBeforeWheel)};
      const completeEnvelope = id => {
        const actual = state.graph.findById(id).getContainer().getCanvasBBox(), reference = references[id];
        const a = state.graph.getCanvasByPoint(reference.minX,reference.minY), b = state.graph.getCanvasByPoint(reference.maxX,reference.maxY);
        // A label already released after zoom must still reserve its complete
        // pre-wheel envelope when planning how to bring it back into view.
        return { minX: Math.min(actual.minX,a.x,b.x), minY: Math.min(actual.minY,a.y,b.y),
          maxX: Math.max(actual.maxX,a.x,b.x), maxY: Math.max(actual.maxY,a.y,b.y) };
      };
      const retained = ${JSON.stringify(ACCOUNTS.slice(1, 3))}.map(completeEnvelope);
      const target = completeEnvelope(${JSON.stringify(ACCOUNTS[0])});
      const bounds = { minX: Math.min(...retained.map(b => b.minX)), maxX: Math.max(...retained.map(b => b.maxX)),
        minY: Math.min(...retained.map(b => b.minY)), maxY: Math.max(...retained.map(b => b.maxY)) };
      const choices = [
        { axis: 'x', delta: Math.max(24,box.width + 160 - target.minX), min: 4 - bounds.minX, max: box.width - 4 - bounds.maxX },
        { axis: 'x', delta: -Math.max(24,target.maxX + 160), min: 4 - bounds.minX, max: box.width - 4 - bounds.maxX },
        { axis: 'y', delta: Math.max(24,box.height + 160 - target.minY), min: 4 - bounds.minY, max: box.height - 4 - bounds.maxY },
        { axis: 'y', delta: -Math.max(24,target.maxY + 160), min: 4 - bounds.minY, max: box.height - 4 - bounds.maxY },
      ].map(choice => ({ ...choice, delta: choice.delta * 12 / 11 }))
        .filter(choice => choice.delta >= choice.min && choice.delta <= choice.max)
        .sort((a,b) => Math.abs(a.delta) - Math.abs(b.delta));
      for (const choice of choices) {
        const horizontal = choice.axis === 'x', length = horizontal ? box.width : box.height;
        const crossLength = horizontal ? box.height : box.width;
        for (const cross of [24,crossLength - 24,56,crossLength - 56]) {
          if (envelopes.some(bound => horizontal ? cross >= bound.minY - 12 && cross <= bound.maxY + 12
            : cross >= bound.minX - 12 && cross <= bound.maxX + 12)) continue;
          const low = { x: Math.round(box.left + (horizontal ? length * .2 : cross)),
            y: Math.round(box.top + (horizontal ? cross : length * .2)) };
          const high = { x: Math.round(box.left + (horizontal ? length * .8 : cross)),
            y: Math.round(box.top + (horizontal ? cross : length * .8)) };
          if ([low,high].every(point => document.elementFromPoint(point.x,point.y)?.tagName === 'CANVAS'
            && container.contains(document.elementFromPoint(point.x,point.y)))) return { ...choice, low, high };
        }
      }
      return null;
    })()`);
    assert.ok(plan, 'the fitted chain must permit clipping only A while B/C retain room for their labels');
    step = 'node-label-partial';
    const from = plan.delta > 0 ? plan.low : plan.high, end = plan.delta > 0 ? plan.high : plan.low;
    const capacity = Math.abs(end[plan.axis] - from[plan.axis]), gestures = [];
    for (let remaining = Math.abs(plan.delta); remaining > 0;) {
      const amount = Math.min(capacity,remaining), to = { ...from, [plan.axis]: from[plan.axis] + Math.sign(plan.delta) * amount };
      gestures.push({ from, to }); await dragCanvas(from,to); remaining -= amount;
    }
    await until(() => evaluate(`state.graphView.viewportFrame == null && state.graphView.labelTimer == null && !state.graphView.zoomSettling`), 10000);
    // The clipping index also reserves a conservative envelope for missing
    // text. If that extends past the measured pre-wheel group, advance through
    // bounded real gestures while independently preserving both retained labels.
    for (let i = 0; i < 8 && await evaluate(`state.graphView.clipResult?.nodeIds.has(${JSON.stringify(ACCOUNTS[0])})`); i += 1) {
      const current = await evaluate(GRAPH_PROBE), delta = Math.sign(plan.delta) * 40;
      unchanged(current);
      assert.equal(current.zoom, zoomed.zoom);
      for (const id of ACCOUNTS.slice(1, 3)) {
        assertAutomaticNodeLabel(current, id);
        const node = current.renderedNodes.find(item => item.id === id);
        for (const bounds of [node.bodyBounds,node.labelShape.bounds]) {
          const dx = plan.axis === 'x' ? delta : 0, dy = plan.axis === 'y' ? delta : 0;
          assert.ok(bounds.minX + dx >= 4 && bounds.minY + dy >= 4
            && bounds.maxX + dx <= current.viewport.width - 4 && bounds.maxY + dy <= current.viewport.height - 4,
          'a clipping correction must keep both retained bodies and labels inside the viewport');
        }
      }
      const to = { ...from, [plan.axis]: from[plan.axis] + delta };
      gestures.push({ from, to }); await dragCanvas(from,to);
      await until(() => evaluate(`state.graphView.viewportFrame == null && state.graphView.labelTimer == null && !state.graphView.zoomSettling`), 10000);
    }
    await until(() => evaluate(`!state.graphView.clipResult?.nodeIds.has(${JSON.stringify(ACCOUNTS[0])})
      && state.graphView.viewportFrame == null && state.graphView.labelTimer == null && !state.graphView.zoomSettling`), 10000);
    const partial = await evaluate(GRAPH_PROBE);
    assertPartialViewportLabels(partial, ids); unchanged(partial);
    assert.equal(partial.zoom, zoomed.zoom);
    const translation = plan.axis === 'x' ? 6 : 7;
    partial.camera.forEach((value,i) => { if (i !== translation) assert.ok(Math.abs(value - zoomed.camera[i]) < .01); });
    step = 'node-label-partial-leave';
    const leafCenter = await evaluate(`(() => { const model = state.graph.findById(${JSON.stringify(ACCOUNTS[2])}).getModel();
      return state.graph.getClientByPoint(model.x,model.y); })()`);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...leafCenter }, page.sessionId);
    await until(() => evaluate(`state.graphView.hoveredAccount === ${JSON.stringify(ACCOUNTS[2])}`), 10000);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...from }, page.sessionId);
    await until(() => evaluate(`state.graphView.hoveredAccount === null && state.graphView.viewportFrame == null
      && state.graphView.labelTimer == null && !state.graphView.zoomSettling`), 10000);
    const left = await evaluate(GRAPH_PROBE);
    assertPartialViewportLabels(left, ids); unchanged(left);
    assert.deepEqual(left.camera, partial.camera);
    step = 'node-label-partial-return';
    for (const gesture of gestures.reverse()) await dragCanvas(gesture.to,gesture.from);
    await until(() => evaluate(`state.graphView.viewportFrame == null && state.graphView.labelTimer == null && !state.graphView.zoomSettling`), 10000);
    const returned = await evaluate(GRAPH_PROBE);
    unchanged(returned);
    assert.equal(returned.zoom, zoomed.zoom);
    returned.camera.forEach((value,i) => assert.ok(Math.abs(value - zoomed.camera[i]) <= (i === translation ? capacity / 12 + 1 : .01)));
    for (const id of ACCOUNTS.slice(0, 2)) assertAutomaticNodeLabel(returned, id);
    // Leave the following journeys at the same ordinary Fit reading frame.
    await clickElement(`document.querySelector('#btnFit')`);
    await graph(2,ids,undefined,{ frame: false });
    const restored = await evaluate(GRAPH_PROBE);
    unchanged(restored);
    for (const id of ACCOUNTS.slice(0, 3)) assertAutomaticNodeLabel(restored, id);
    assert.ok(Number.isFinite(restored.zoom) && restored.zoom > 0 && restored.zoom <= 1.6);
  }
  const editedEdge = `${ACCOUNTS[0]}=>${ACCOUNTS[1]}`;
  async function selectEdge() {
    step = 'edge-pointer';
    // Read the live G6 path, then let Chromium deliver an actual pointer click.
    // Ratios away from the label gap also avoid either account's hit area.
    const points = await evaluate(`(() => {
      const edge = state.graph.getEdges().find(item => {
        const model = item.getModel(); return model.source + '=>' + model.target === ${JSON.stringify(editedEdge)};
      });
      const shape = edge?.getKeyShape(), box = document.querySelector('#graphContainer').getBoundingClientRect();
      return [.28,.72,.38,.62,.18,.82].map(ratio => {
        const point = shape?.getPoint?.(ratio); if (!point) return null;
        const client = state.graph.getClientByPoint(point.x, point.y), hit = document.elementFromPoint(client.x, client.y);
        return client.x > box.left && client.x < box.right && client.y > box.top && client.y < box.bottom
          && hit && document.querySelector('#graphContainer').contains(hit) ? client : null;
      }).filter(Boolean);
    })()`);
    assert.ok(points.length, 'the relationship must have a reachable rendered path');
    for (const point of points) {
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...point }, page.sessionId);
      for (const type of ['mousePressed', 'mouseReleased']) await cdp.send('Input.dispatchMouseEvent', {
        type, ...point, button: 'left', clickCount: 1,
      }, page.sessionId);
      if (await evaluate(`state.graphView.selectedEdge === ${JSON.stringify(editedEdge)}`)) return;
    }
    assert.fail('pointer clicks did not select the intended relationship');
  }
  async function type(value) {
    const expected = renderedEdgeTypes(await evaluate(GRAPH_PROBE));
    await selectEdge();
    step = 'style-tab';
    await clickElement(`document.querySelector('#ribbonTab-style')`);
    // The expanded style tab exposes this select inline.
    step = 'style-select';
    await select('#edgeTypeSel', value);
    expected[editedEdge] = value;
    step = 'style-confirm';
    await until(async () => {
      const snapshot = await evaluate(GRAPH_PROBE);
      try { assert.equal(snapshot.ready, true); assertEdgeTypes(snapshot, expected); return true; }
      catch { return false; }
    }, 15000);
    assert.equal(await evaluate(`state.edgeTypes[${JSON.stringify(editedEdge)}] === ${JSON.stringify(value)}
      && state.graphView.selectedEdge === ${JSON.stringify(editedEdge)}`), true);
    return expected;
  }
  async function prompt(selector, name) {
    step = 'canvas-open';
    await clickElement(`document.querySelector(${JSON.stringify(selector)})`);
    await until(() => evaluate(`!!document.querySelector('#canvasActionForm[open]')`), 10000);
    step = 'canvas-fill';
    await fill('#canvasActionName', name);
    step = 'canvas-submit';
    await clickElement(`document.querySelector('#canvasActionSubmit')`);
    step = 'canvas-closed';
    await until(() => evaluate(`!document.querySelector('#canvasActionForm[open]')`), 15000);
  }
  async function rowAction(flowId, action) {
    // The seven-column sidebar keeps row actions at its right edge. Use its
    // visible scrollbar control rather than clicking a clipped table button.
    if (await evaluate(`document.querySelector('#flowTableScroll').scrollWidth
      > document.querySelector('#flowTableScroll').clientWidth + 1`)) {
      await clickElement(`document.querySelector('#flowHorizontalPosition')`);
      await key('End', 'End', 35);
      await until(() => evaluate(`(() => {
        const viewport = document.querySelector('#flowTableScroll');
        return viewport.scrollLeft >= viewport.scrollWidth - viewport.clientWidth - 1;
      })()`), 10000);
    }
    await clickElement(`document.querySelector('#flowTbody tr[data-id="${flowId}"] .${action}')`);
  }
  async function pointerClick(point) {
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...point }, page.sessionId);
    for (const type of ['mousePressed', 'mouseReleased']) await cdp.send('Input.dispatchMouseEvent', {
      type, ...point, button: 'left', buttons: type === 'mousePressed' ? 1 : 0, clickCount: 1,
    }, page.sessionId);
  }
  async function pointerDrag(from, to) {
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...from }, page.sessionId);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', ...from,
      button: 'left', buttons: 1, clickCount: 1 }, page.sessionId);
    for (let i = 1; i <= 12; i += 1) {
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved',
        x: from.x + (to.x - from.x) * i / 12, y: from.y + (to.y - from.y) * i / 12,
        button: 'left', buttons: 1 }, page.sessionId);
      await evaluate(`new Promise(resolve => requestAnimationFrame(resolve))`);
    }
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...to,
      button: 'left', buttons: 0, clickCount: 1 }, page.sessionId);
  }
  async function selectAnnotationNode() {
    step = 'annotation-node';
    const point = await evaluate(`(() => {
      const model = state.graph.findById(${JSON.stringify(ANNOTATION.id)})?.getModel();
      if (!model) return null;
      const client = state.graph.getClientByPoint(model.x, model.y), hit = document.elementFromPoint(client.x, client.y);
      return hit?.tagName === 'CANVAS' && document.querySelector('#graphContainer').contains(hit) ? client : null;
    })()`);
    assert.ok(point);
    await pointerClick(point);
    await until(() => evaluate(`JSON.stringify([...state.graphSelection.ids].map(String)) === ${JSON.stringify(JSON.stringify([ANNOTATION.id]))}
      && state.graphView.selectedEdge === null`), 10000);
  }
  async function openAnnotation() {
    await clickElement(`document.querySelector('#ribbonTab-common')`);
    await selectAnnotationNode(); step = 'annotation-open';
    await clickElement(`document.querySelector('#btnAnnotation')`);
    await until(() => evaluate(`document.querySelector('#annotationDialog').open
      && document.querySelector('#annotationDialog').dataset.node === ${JSON.stringify(ANNOTATION.id)}`), 10000);
  }
  async function annotation(expected) {
    await until(async () => {
      try { const snapshot = await evaluate(GRAPH_PROBE); assert.equal(snapshot.labelsSettled, true);
        expected ? assertAnnotation(snapshot, expected) : assertNoAnnotation(snapshot); return true; }
      catch { return false; }
    }, 15000);
    const snapshot = await evaluate(GRAPH_PROBE);
    expected ? assertAnnotation(snapshot, expected) : assertNoAnnotation(snapshot);
    assertGraph(snapshot, flowIds, 3);
    return snapshot;
  }
  const positions = snapshot => snapshot.renderedNodes.map(node => [node.id, node.x, node.y])
    .sort((a, b) => a[0].localeCompare(b[0]));

  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'funds-trace-installed-graph-'));
  try {
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1,
      mobile: false, screenWidth: 1600, screenHeight: 1000 }, page.sessionId);
    await check('installedGraphModulesLoaded', async () => {
      assert.equal(await evaluate(`typeof FundsGraphSelection?.create === 'function'
        && !!state.graphSelection && !state.graphSelection.status().destroyed
        && typeof FundsCanvasEditor?.history.undo === 'function'
        && typeof window.FundsDesktopPreferences?.getItem === 'function'
        && typeof window.FundsNodeFinder?.create === 'function'
        && typeof window.FundsNodeFinderActions?.getState === 'function'`), true);
    });
    const csvFile = path.join(directory, 'synthetic-installed-chain.csv');
    await fs.writeFile(csvFile, fixtureCsv(), { flag: 'wx' });
    await check('installedCsvPreviewMapping', async () => {
      await clickElement(`document.querySelector('#btnImport')`);
      await until(() => evaluate(`document.querySelector('#importModal').style.display === 'flex'`), 10000);
      const { root } = await cdp.send('DOM.getDocument', {}, page.sessionId);
      const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector: '#impFile' }, page.sessionId);
      assert.ok(nodeId);
      await cdp.send('DOM.setFileInputFiles', { nodeId, files: [csvFile] }, page.sessionId);
      await until(() => evaluate(`document.querySelector('#impStep2').style.display === 'block'
        && state.imp?.stats?.total === 3 && state.imp?.stats?.ok === 3`), 30000);
      assert.equal(await evaluate(`(() => {
        const expected = ${JSON.stringify(AMOUNTS)}, summaries = ${JSON.stringify(SUMMARIES)}, mapping = ${JSON.stringify(MAPPING)};
        return state.imp.mode === 'A' && state.imp.stats.skipped === 0
          && ['trade_time','payer_account','payee_account','amount','currency','summary'].every(field =>
            document.querySelector('#impMapping select[data-field="' + field + '"]')?.value === mapping[field])
          && state.imp.preview.length === 3 && state.imp.preview.every((row,i) =>
            row.amount === expected[i] && row.summary === summaries[i] && row.currency === 'CNY')
          && document.querySelectorAll('#impTbody tr').length === 3
          && [...document.querySelectorAll('#impTbody tr')].every((row, i) =>
            row.children[3].textContent.replaceAll(',', '') === '¥' + expected[i]
            && row.children[4].textContent === summaries[i] && row.children[5].textContent === 'CNY');
      })()`), true);
    });
    await check('installedCsvCommittedExact', async () => {
      await clickElement(`document.querySelector('#impCommit')`);
      const receipt = () => evaluate(`({
        open: document.querySelector('#importModal').style.display === 'flex',
        busy: document.querySelector('#importModal').getAttribute('aria-busy') !== 'false',
        resultVisible: !document.querySelector('#impResult').hidden,
        commitHidden: document.querySelector('#impCommit').hidden,
        action: document.querySelector('#impCancel').textContent,
        status: document.querySelector('#impFlowStatus').textContent,
        steps: ['impFlowFile','impFlowFields','impFlowConfirm'].map(id => document.getElementById(id).dataset.state),
        summary: document.querySelector('#impResultText').textContent,
      })`);
      await until(async () => {
        try { assertImportCompletion(await receipt()); return true; }
        catch { return false; }
      }, 45000);
      assertImportCompletion(await receipt());
      await clickElement(`document.querySelector('#impCancel')`);
      await until(() => evaluate(`document.querySelector('#importModal').style.display === 'none'
        && state.search.total === 3 && state.stats.flows === 3 && document.querySelectorAll('#flowTbody tr[data-id]').length === 3`), 45000);
      flowIds = await evaluate(`(() => {
        const summaries = ${JSON.stringify(SUMMARIES)}, amounts = ${JSON.stringify(AMOUNTS)};
        return summaries.map((summary, i) => {
          const row = [...document.querySelectorAll('#flowTbody tr[data-id]')].find(item => item.querySelector('.flow-summary')?.textContent === summary);
          if (!row || row.querySelector('td.amt')?.textContent.replaceAll(',', '') !== '¥' + amounts[i]) return null;
          return row.dataset.id;
        });
      })()`);
      assert.equal(flowIds.length, 3); assert.equal(new Set(flowIds).size, 3);
      assert.ok(flowIds.every(id => typeof id === 'string' && /^[1-9]\d*$/.test(id)));
      assert.equal(await evaluate(`state.lastGraph.nodes.length === 0 && state.lastGraph.edges.length === 0 && state.tracked.size === 0`), true);
    });
    await check('installedQueryExact', async () => {
      await fill('#fKw', SUMMARIES[1]); await clickElement(`document.querySelector('#btnSearch')`);
      await until(() => evaluate(`state.search.total === 1 && document.querySelector('#flowTbody tr[data-id]')?.dataset.id === ${JSON.stringify(flowIds[1])}`), 10000);
      await clickElement(`document.querySelector('#btnReset')`);
      await until(() => evaluate(`state.search.total === 3 && document.querySelectorAll('#flowTbody tr[data-id]').length === 3`), 10000);
    });
    const track = index => rowAction(flowIds[index], 'act-track');
    await check('installedFirstTrackExact', async () => { await track(0); await graph(1, flowIds); });
    const appendZoom = await evaluate(`state.graph.getZoom()`);
    await check('installedSecondTrackVisible', async () => { await track(1); await graph(2, flowIds); });
    await check('installedAutomaticNodeLabelRecovery', async () => { await automaticNodeLabelRecovery(flowIds); });
    await check('installedPartialViewportNodeLabels', async () => { await partialViewportNodeLabels(flowIds,appendZoom); });
    await check('installedContextTrackedConsistent', async () => {
      await rowAction(flowIds[1], 'act-ctx');
      await until(() => evaluate(`document.querySelector('#ctxModal').style.display === 'flex'
        && String(state.ctx?.data?.anchor?.id) === ${JSON.stringify(flowIds[1])}
        && !!document.querySelector('#ctxTbody tr.anchor .ctx-on')`), 10000);
      assert.equal(await evaluate(`state.tracked.has(Number(${JSON.stringify(flowIds[1])}))
        && document.querySelector('#ctxTbody tr.anchor .ctx-on').textContent === '已上图'
        && document.querySelector('#ctxTbody tr.anchor td.amt').textContent.replaceAll(',', '') === ${JSON.stringify('¥' + AMOUNTS[1])}`), true);
      await graph(2, flowIds, undefined, { frame: false }); await clickElement(`document.querySelector('#ctxClose')`);
    });
    await check('installedNeighborhoodTrackVisible', async () => {
      await clickElement(`document.querySelector('#ribbonTab-trace')`);
      await clickElement(`document.querySelector('#btnNeighborActions')`);
      await until(() => evaluate(`!document.querySelector('#neighborPopover').hidden`), 10000);
      await fill('#neighborAccount', ACCOUNTS[0]);
      await clickElement(`document.querySelector('#btnNeighborSearch')`);
      await until(() => evaluate(`!document.querySelector('#neighborResolvedName').hidden
        && document.querySelector('#btnNeighborSearch').getAttribute('aria-busy') === 'false'
        && !document.querySelector('#btnNeighborhood').disabled`), 10000);
      await clickElement(`document.querySelector('#btnNeighborhood')`);
      await until(() => evaluate(`state.graphView.root === ${JSON.stringify(ACCOUNTS[0])}
        && document.querySelector('#neighborDraftState').dataset.state === 'applied'
        && document.querySelector('#neighborPopover').getAttribute('aria-busy') === 'false'`), 15000);
      await clickElement(`document.querySelector('#btnNeighborClose')`);
      await until(() => evaluate(`document.querySelector('#neighborPopover').hidden`), 10000);
      await graph(2, flowIds, ACCOUNTS.slice(0, 2));
      await track(2); await graph(3, flowIds);
      assert.equal(await evaluate(`state.graphView.root === ${JSON.stringify(ACCOUNTS[0])}
        && state.graphView.visibleIds.has(${JSON.stringify(ACCOUNTS[2])})
        && state.graphView.visibleIds.has(${JSON.stringify(ACCOUNTS[3])})`), true);
    });
    await check('installedAnnotationDialogSave', async () => {
      await openAnnotation(); step = 'annotation-fill';
      await fill('#annotationText', ANNOTATION.text);
      await select('#annotationSize', ANNOTATION.size);
      await select('#annotationFont', ANNOTATION.fontFamily);
      await clickElement(`document.querySelector('input[name="annotationAnchor"][value="above"]')`);
      // The installed journey keeps the normal default color. Custom colors
      // have browser coverage; opening Windows' native color chooser is outside
      // this renderer-input journey and is never replaced with an eval write.
      assert.equal(await evaluate(`document.querySelector('#annotationColor').value`), ANNOTATION.color);
      step = 'annotation-save';
      await clickElement(`document.querySelector('#annotationSave')`);
      await until(() => evaluate(`!document.querySelector('#annotationDialog').open`), 15000);
      await clickElement(`document.querySelector('#ribbonTab-view')`);
      await clickElement(`document.querySelector('#btnFit')`);
      step = 'annotation-rendered';
      const snapshot = await annotation(ANNOTATION);
      const node = snapshot.renderedNodes.find(item => item.id === ANNOTATION.id);
      assert.ok(snapshot.renderedAnnotations[0].bounds.maxY < node.bodyBounds.minY);
    });
    await check('installedAnnotationUndoRedo', async () => {
      step = 'annotation-undo';
      await clickElement(`document.querySelector('#ribbonTab-view')`);
      await key('z', 'KeyZ', 90, 2); await annotation(null);
      step = 'annotation-redo';
      await clickElement(`document.querySelector('#ribbonTab-view')`);
      await key('Z', 'KeyZ', 90, 10); await annotation(ANNOTATION);
      step = 'style-tab';
      await clickElement(`document.querySelector('#ribbonTab-style')`);
      step = 'annotation-style-reset';
      await clickElement(`document.querySelector('[data-ribbon-command="styleReset"]')`);
      await annotation(null);
      step = 'annotation-undo';
      await key('z', 'KeyZ', 90, 2); await annotation(ANNOTATION);
    });
    const savedAnnotation = { ...ANNOTATION, dx: 24, dy: -12 };
    await check('installedAnnotationOffsetDrag', async () => {
      step = 'annotation-offset';
      const before = await annotation(ANNOTATION);
      const gesture = await evaluate(`(() => {
        const item = state.graph.findById(${JSON.stringify(ANNOTATION.id)}), box = document.querySelector('#graphContainer').getBoundingClientRect();
        const shape = item.getContainer().get('children').find(child => child.get('name') === 'funds-node-annotation');
        const bounds = shape.getCanvasBBox(), x = (bounds.minX + bounds.maxX) / 2, y = (bounds.minY + bounds.maxY) / 2;
        const point = state.graph.getPointByCanvas(x, y), from = { x: box.left + x, y: box.top + y };
        const to = state.graph.getClientByPoint(point.x + 24, point.y - 12);
        return [from,to].every(client => document.elementFromPoint(client.x, client.y)?.tagName === 'CANVAS') ? { from, to } : null;
      })()`);
      assert.ok(gesture); await pointerDrag(gesture.from, gesture.to);
      const after = await annotation(savedAnnotation);
      assert.deepEqual(positions(after), positions(before)); assert.deepEqual(after.camera, before.camera);
      assert.ok(Math.abs(after.renderedAnnotations[0].y - before.renderedAnnotations[0].y + 12) < .01);
    });
    await check('installedAnnotationNodeFollow', async () => {
      await selectAnnotationNode(); step = 'annotation-follow';
      const before = await annotation(savedAnnotation);
      const gesture = await evaluate(`(() => {
        const model = state.graph.findById(${JSON.stringify(ANNOTATION.id)}).getModel();
        const box = document.querySelector('#graphContainer').getBoundingClientRect(), from = state.graph.getClientByPoint(model.x, model.y);
        const dx = from.x < box.left + box.width / 2 ? 32 : -32, dy = from.y < box.top + box.height / 2 ? 24 : -24;
        const to = state.graph.getClientByPoint(model.x + dx, model.y + dy);
        return [from,to].every(client => document.elementFromPoint(client.x, client.y)?.tagName === 'CANVAS') ? { from, to } : null;
      })()`);
      assert.ok(gesture); await pointerDrag(gesture.from, gesture.to);
      const after = await annotation(savedAnnotation);
      assertAnnotationFollow(before, after, savedAnnotation);
    });
    if (captureGraph) await captureGraph();
    let clonedTypes, savedTypes, redoneTypes;
    let savedPositions;
    await check('installedCanvasCloneExact', async () => {
      clonedTypes = await type('line');
      await clickElement(`document.querySelector('#ribbonTab-common')`);
      await prompt('#btnCanvasClone', CANVAS);
      step = 'canvas-activated';
      await until(() => evaluate(`state.canvasId > 0 && state.canvasName === ${JSON.stringify(CANVAS)} && !state.graphLoading`), 15000);
      await graph(3, flowIds);
      step = 'canvas-clone-verify';
      assertEdgeTypes(await evaluate(GRAPH_PROBE), clonedTypes);
      await annotation(savedAnnotation);
      assert.equal(await evaluate(`state.edgeTypes[${JSON.stringify(editedEdge)}] === 'line'`), true);
    });
    await check('installedCanvasSaveRestoreTypes', async () => {
      await check('installedCanvasSaveRestoreAnnotation', async () => {
        savedTypes = await type('polyline'); await prompt('#btnCanvasSave', CANVAS);
        await until(() => evaluate(`[...document.querySelectorAll('#toasts .toast')].some(node => node.textContent.includes('形态已保存'))`), 10000);
        savedPositions = positions(await annotation(savedAnnotation));
        step = 'canvas-default';
        await select('#canvasSel', '0'); await graph(3, flowIds);
        await type('line');
        // The default canvas has no saved presentation. Reopening the named
        // canvas must read its saved note after this empty annotation view.
        await annotation(null);
        step = 'canvas-restored';
        await select('#canvasSel', CANVAS, true); await graph(3, flowIds);
        assertEdgeTypes(await evaluate(GRAPH_PROBE), savedTypes);
        assert.equal(await evaluate(`state.edgeTypes[${JSON.stringify(editedEdge)}] === 'polyline'`), true);
        step = 'annotation-restored';
        assert.deepEqual(positions(await annotation(savedAnnotation)), savedPositions);
      });
    });
    await check('installedCanvasUndoRedoTypes', async () => {
      await check('installedKeyboardUndoRedoOutsideGraph', async () => {
        redoneTypes = await type('line'); step = 'history-undo';
        await clickElement(`document.querySelector('#ribbonTab-view')`);
        assert.equal(await evaluate(`document.activeElement === document.querySelector('#ribbonTab-view')`), true);
        await key('z', 'KeyZ', 90, 2);
        await until(async () => {
          try { const snapshot = await evaluate(GRAPH_PROBE); assert.equal(snapshot.ready, true); assertEdgeTypes(snapshot, savedTypes); return true; }
          catch { return false; }
        }, 15000);
        assert.equal(await evaluate(`state.edgeTypes[${JSON.stringify(editedEdge)}] === 'polyline'`), true);
        await graph(3, flowIds); step = 'history-redo';
        await clickElement(`document.querySelector('#ribbonTab-view')`);
        await key('Z', 'KeyZ', 90, 10);
        await until(async () => {
          try { const snapshot = await evaluate(GRAPH_PROBE); assert.equal(snapshot.ready, true); assertEdgeTypes(snapshot, redoneTypes); return true; }
          catch { return false; }
        }, 15000);
        assert.equal(await evaluate(`state.edgeTypes[${JSON.stringify(editedEdge)}] === 'line'`), true);
        await graph(3, flowIds);
      });
    });
    await check('installedCaseIsolationAndReturn', async () => {
      step = 'case-empty';
      await select('#caseSelect', seedCase, true);
      await until(() => evaluate(`!state.graphLoading && !layoutJob && state.stats.flows === 0
        && state.search.total === 0 && state.lastGraph.nodes.length === 0 && state.tracked.size === 0
        && state.cases.find(item => item.id === state.caseId)?.name === ${JSON.stringify(seedCase)}`), 15000);
      assertNoAnnotation(await evaluate(GRAPH_PROBE));
      step = 'case-return';
      await select('#caseSelect', createdCase, true);
      await until(() => evaluate(`state.stats.flows === 3 && state.search.total === 3 && state.canvasId === 0
        && state.cases.find(item => item.id === state.caseId)?.name === ${JSON.stringify(createdCase)}`), 15000);
      await graph(3, flowIds);
      assertNoAnnotation(await evaluate(GRAPH_PROBE));
      await select('#canvasSel', CANVAS, true); await graph(3, flowIds);
      step = 'annotation-restored';
      assert.deepEqual(positions(await annotation(savedAnnotation)), savedPositions);
    });
  } catch (error) {
    const failure = new Error('INSTALLED_GRAPH_JOURNEY_FAILED');
    failure.installedGraphDiagnostic = { step, kind: error?.code === 'ERR_ASSERTION' ? 'assertion'
      : error?.message === 'TIMEOUT' ? 'timeout' : 'runtime' };
    throw failure;
  } finally {
    // The sole deletion target is this operation's fresh temporary CSV folder.
    await fs.rm(directory, { recursive: true, force: true });
  }
}

module.exports = { ACCOUNTS, AMOUNTS, SUMMARIES, REQUIRED_CHECKS, fixtureCsv, assertGraph, assertEdgeTypes,
  assertNoAnnotation, assertAnnotation, assertAnnotationFollow, assertAutomaticLeafLabel, assertPartialViewportLabels,
  assertImportCompletion, safeJourneyFailure, runInstalledGraphJourney };
