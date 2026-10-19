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
const REQUIRED_CHECKS = Object.freeze([
  'installedGraphModulesLoaded', 'installedCsvPreviewMapping', 'installedCsvCommittedExact',
  'installedQueryExact', 'installedFirstTrackExact', 'installedSecondTrackVisible',
  'installedContextTrackedConsistent', 'installedNeighborhoodTrackVisible',
  'installedCanvasCloneExact', 'installedCanvasSaveRestoreTypes',
  'installedCanvasUndoRedoTypes', 'installedCaseIsolationAndReturn',
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
  const drawn = item => {
    const group = item.getContainer(), shape = item.getKeyShape(), bounds = shape?.getCanvasBBox?.();
    return !!shape && !shape.get('destroyed') && shape.get('visible') !== false
      && group.get('visible') !== false && (bounds?.width > 0 || bounds?.height > 0);
  };
  return { ready: !state.graphLoading && state.graphInited && !layoutJob
      && !window.FundsCanvasEditor.history.status().busy,
    accounts: state.lastGraph.nodes.map(node => node.account),
    trackedIds: [...state.tracked].map(String),
    edges: state.lastGraph.edges.map(edge => ({ source: edge.source, target: edge.target,
      ids: edge.ids.map(String), total: edge.total, cnt: edge.cnt, amount_totals: edge.amount_totals })),
    renderedNodes: state.graph.getNodes().map(item => {
      const model = item.getModel(), client = state.graph.getClientByPoint(model.x, model.y);
      return { id: model.id, drawnInContainer: drawn(item) && client.x >= box.left && client.x <= box.right
        && client.y >= box.top && client.y <= box.bottom };
    }),
    renderedEdges: state.graph.getEdges().map(item => {
      const model = item.getModel(); return { source: model.source, target: model.target, type: model.type,
        ids: model.data.ids.map(String), amount_totals: model.data.amount_totals, drawn: drawn(item) };
    }) };
})()`;

async function runInstalledGraphJourney({ cdp, page, evaluate, clickElement, until, check, createdCase, seedCase, captureGraph }) {
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
      await until(async () => {
        try { assertTrackedGraph(await evaluate(GRAPH_PROBE), ids, count); return true; }
        catch { return false; }
      }, 20000);
      const selectionProbe = `({ nodes: [...state.graphSelection.ids].map(String).sort(), edge: state.graphView.selectedEdge })`;
      const selection = await evaluate(selectionProbe);
      await clickElement(`document.querySelector('#ribbonTab-common')`);
      await clickElement(`document.querySelector('#btnFindNode')`);
      await until(() => evaluate(`!document.querySelector('#findNodePopover').hidden`), 10000);
      await fill('#graphFindInput', 'SYN-INST-');
      await clickElement(`document.querySelector('#btnGraphFindSearch')`);
      await until(() => evaluate(`document.querySelectorAll('#graphFindResults .find-result').length === ${visible.length}
        && !document.querySelector('#btnGraphFindAll').disabled`), 10000);
      await clickElement(`document.querySelector('#btnGraphFindAll')`);
      await until(() => evaluate(`document.querySelector('#graphFindStatus').dataset.state === 'done'
        && document.querySelector('#findNodePopover').getAttribute('aria-busy') === 'false'
        && document.querySelector('#graphFindStatus').textContent === ${JSON.stringify(`已定位全部 ${visible.length} 个账户`)}`), 15000);
      await clickElement(`document.querySelector('#btnGraphFindClose')`);
      await until(() => evaluate(`document.querySelector('#findNodePopover').hidden`), 10000);
      assert.deepEqual(await evaluate(selectionProbe), selection);
      assertTrackedGraph(await evaluate(GRAPH_PROBE), ids, count);
    }
    await until(async () => {
      try { assertGraph(await evaluate(GRAPH_PROBE), ids, count, visible); return true; }
      catch { return false; }
    }, 20000);
    assertGraph(await evaluate(GRAPH_PROBE), ids, count, visible);
  }
  const editedEdge = `${ACCOUNTS[0]}=>${ACCOUNTS[1]}`;
  async function selectEdge() {
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
    await clickElement(`document.querySelector('#ribbonTab-style')`);
    // Expanded styling is inline; the compact ribbon exposes the real popover.
    await clickElement(`document.querySelector('.ribbon-tabbar [data-ribbon-collapse="true"]')`);
    await clickElement(`document.querySelector('#btnEdgeStyle')`);
    await select('#edgeTypeSel', value);
    expected[editedEdge] = value;
    await until(async () => {
      const snapshot = await evaluate(GRAPH_PROBE);
      try { assert.equal(snapshot.ready, true); assertEdgeTypes(snapshot, expected); return true; }
      catch { return false; }
    }, 15000);
    assert.equal(await evaluate(`state.edgeTypes[${JSON.stringify(editedEdge)}] === ${JSON.stringify(value)}
      && state.graphView.selectedEdge === ${JSON.stringify(editedEdge)}`), true);
    await clickElement(`document.querySelector('#btnEdgeStyle')`);
    await clickElement(`document.querySelector('#ribbonCompact [data-ribbon-collapse="false"]')`);
    return expected;
  }
  async function prompt(selector, name) {
    await clickElement(`document.querySelector(${JSON.stringify(selector)})`);
    await until(() => evaluate(`!!document.querySelector('#canvasActionForm[open]')`), 10000);
    await fill('#canvasActionName', name);
    await clickElement(`document.querySelector('#canvasActionSubmit')`);
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
    let flowIds;
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
    await check('installedSecondTrackVisible', async () => { await track(1); await graph(2, flowIds); });
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
    if (captureGraph) await captureGraph();
    let clonedTypes, savedTypes, redoneTypes;
    await check('installedCanvasCloneExact', async () => {
      clonedTypes = await type('line');
      await clickElement(`document.querySelector('#ribbonTab-common')`);
      await prompt('#btnCanvasClone', CANVAS);
      await until(() => evaluate(`state.canvasId > 0 && state.canvasName === ${JSON.stringify(CANVAS)} && !state.graphLoading`), 15000);
      await graph(3, flowIds);
      assertEdgeTypes(await evaluate(GRAPH_PROBE), clonedTypes);
      assert.equal(await evaluate(`state.edgeTypes[${JSON.stringify(editedEdge)}] === 'line'`), true);
    });
    await check('installedCanvasSaveRestoreTypes', async () => {
      savedTypes = await type('polyline'); await prompt('#btnCanvasSave', CANVAS);
      await until(() => evaluate(`[...document.querySelectorAll('#toasts .toast')].some(node => node.textContent.includes('形态已保存'))`), 10000);
      await select('#canvasSel', '0'); await graph(3, flowIds);
      await type('quadratic');
      await select('#canvasSel', CANVAS, true); await graph(3, flowIds);
      assertEdgeTypes(await evaluate(GRAPH_PROBE), savedTypes);
      assert.equal(await evaluate(`state.edgeTypes[${JSON.stringify(editedEdge)}] === 'polyline'`), true);
    });
    await check('installedCanvasUndoRedoTypes', async () => {
      redoneTypes = await type('line'); await clickElement(`document.querySelector('#btnUndo')`);
      await until(async () => {
        try { const snapshot = await evaluate(GRAPH_PROBE); assert.equal(snapshot.ready, true); assertEdgeTypes(snapshot, savedTypes); return true; }
        catch { return false; }
      }, 15000);
      assert.equal(await evaluate(`state.edgeTypes[${JSON.stringify(editedEdge)}] === 'polyline'`), true);
      await graph(3, flowIds); await clickElement(`document.querySelector('#btnRedo')`);
      await until(async () => {
        try { const snapshot = await evaluate(GRAPH_PROBE); assert.equal(snapshot.ready, true); assertEdgeTypes(snapshot, redoneTypes); return true; }
        catch { return false; }
      }, 15000);
      assert.equal(await evaluate(`state.edgeTypes[${JSON.stringify(editedEdge)}] === 'line'`), true);
      await graph(3, flowIds);
    });
    await check('installedCaseIsolationAndReturn', async () => {
      await select('#caseSelect', seedCase, true);
      await until(() => evaluate(`!state.graphLoading && !layoutJob && state.stats.flows === 0
        && state.search.total === 0 && state.lastGraph.nodes.length === 0 && state.tracked.size === 0
        && state.cases.find(item => item.id === state.caseId)?.name === ${JSON.stringify(seedCase)}`), 15000);
      await select('#caseSelect', createdCase, true);
      await until(() => evaluate(`state.stats.flows === 3 && state.search.total === 3 && state.canvasId === 0
        && state.cases.find(item => item.id === state.caseId)?.name === ${JSON.stringify(createdCase)}`), 15000);
      await graph(3, flowIds);
    });
  } finally {
    // The sole deletion target is this operation's fresh temporary CSV folder.
    await fs.rm(directory, { recursive: true, force: true });
  }
}

module.exports = { ACCOUNTS, AMOUNTS, SUMMARIES, REQUIRED_CHECKS, fixtureCsv, assertGraph, assertEdgeTypes, assertImportCompletion, runInstalledGraphJourney };
