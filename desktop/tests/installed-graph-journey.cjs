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
function assertGraph(snapshot, flowIds, count, visibleAccounts = ACCOUNTS.slice(0, count + 1)) {
  assert.ok(snapshot && snapshot.ready);
  assert.deepEqual(sorted(snapshot.accounts), sorted(ACCOUNTS.slice(0, count + 1)));
  assert.deepEqual(sorted(snapshot.trackedIds), sorted(flowIds.slice(0, count)));
  const facts = snapshot.edges.map(edge => ({ source: edge.source, target: edge.target,
    ids: sorted(edge.ids), total: edge.total, cnt: edge.cnt, amount_totals: edge.amount_totals }));
  assert.deepEqual(facts.sort((a, b) => a.source.localeCompare(b.source)), expectedEdges(flowIds, count));
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
  async function graph(count, ids, visible) {
    await until(async () => {
      try { assertGraph(await evaluate(GRAPH_PROBE), ids, count, visible); return true; }
      catch { return false; }
    }, 20000);
    assertGraph(await evaluate(GRAPH_PROBE), ids, count, visible);
  }
  async function type(value) {
    await select('#edgeTypeSel', value);
    await until(() => evaluate(`!layoutJob && !FundsCanvasEditor.history.status().busy
      && state.graph.getEdges().length === 3 && state.graph.getEdges().every(edge => edge.getModel().type === ${JSON.stringify(value)})`), 15000);
  }
  async function prompt(selector, name) {
    await clickElement(`document.querySelector(${JSON.stringify(selector)})`);
    await until(() => evaluate(`document.querySelector('#textInputModal').style.display === 'flex'`), 10000);
    await fill('#tiInput', name);
    await clickElement(`document.querySelector('#tiOk')`);
  }

  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'funds-trace-installed-graph-'));
  try {
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1,
      mobile: false, screenWidth: 1600, screenHeight: 1000 }, page.sessionId);
    await check('installedGraphModulesLoaded', async () => {
      assert.equal(await evaluate(`typeof FundsGraphSelection?.create === 'function'
        && !!state.graphSelection && !state.graphSelection.status().destroyed
        && typeof FundsCanvasEditor?.history.undo === 'function'
        && typeof window.FundsDesktopPreferences?.getItem === 'function'`), true);
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
          && document.querySelectorAll('#impTbody tr').length === 3;
      })()`), true);
    });
    let flowIds;
    await check('installedCsvCommittedExact', async () => {
      await clickElement(`document.querySelector('#impCommit')`);
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
    const track = index => clickElement(`document.querySelector('#flowTbody tr[data-id="${flowIds[index]}"] .act-track')`);
    await check('installedFirstTrackExact', async () => { await track(0); await graph(1, flowIds); });
    await check('installedSecondTrackVisible', async () => { await track(1); await graph(2, flowIds); });
    await check('installedContextTrackedConsistent', async () => {
      await clickElement(`document.querySelector('#flowTbody tr[data-id="${flowIds[1]}"] .act-ctx')`);
      await until(() => evaluate(`document.querySelector('#ctxModal').style.display === 'flex'
        && String(state.ctx?.data?.anchor?.id) === ${JSON.stringify(flowIds[1])}
        && !!document.querySelector('#ctxTbody tr.anchor .tag.on')`), 10000);
      assert.equal(await evaluate(`state.tracked.has(Number(${JSON.stringify(flowIds[1])}))
        && document.querySelector('#ctxTbody tr.anchor .tag.on').textContent === '已上图'
        && document.querySelector('#ctxTbody tr.anchor td.amt').textContent.replaceAll(',', '') === ${JSON.stringify('¥' + AMOUNTS[1])}`), true);
      await graph(2, flowIds); await clickElement(`document.querySelector('#ctxClose')`);
    });
    await check('installedNeighborhoodTrackVisible', async () => {
      await fill('#neighborAccount', ACCOUNTS[0]);
      await clickElement(`document.querySelector('#btnNeighborhood')`);
      await graph(2, flowIds, ACCOUNTS.slice(0, 2));
      await track(2); await graph(3, flowIds);
      assert.equal(await evaluate(`state.graphView.root === ${JSON.stringify(ACCOUNTS[0])}
        && state.graphView.visibleIds.has(${JSON.stringify(ACCOUNTS[2])})
        && state.graphView.visibleIds.has(${JSON.stringify(ACCOUNTS[3])})`), true);
    });
    if (captureGraph) await captureGraph();
    await check('installedCanvasCloneExact', async () => {
      await type('line'); await prompt('#btnCanvasClone', CANVAS);
      await until(() => evaluate(`state.canvasId > 0 && state.canvasName === ${JSON.stringify(CANVAS)} && !state.graphLoading`), 15000);
      await graph(3, flowIds);
      assert.equal(await evaluate(`state.graph.getEdges().every(edge => edge.getModel().type === 'line')
        && Object.keys(state.edgeTypes).length === 3`), true);
    });
    await check('installedCanvasSaveRestoreTypes', async () => {
      await type('polyline'); await prompt('#btnCanvasSave', CANVAS);
      await until(() => evaluate(`[...document.querySelectorAll('#toasts .toast')].some(node => node.textContent.includes('形态已保存'))`), 10000);
      await select('#canvasSel', '0'); await graph(3, flowIds);
      await type('quadratic');
      await select('#canvasSel', CANVAS, true); await graph(3, flowIds);
      assert.equal(await evaluate(`state.graph.getEdges().every(edge => edge.getModel().type === 'polyline')
        && Object.keys(state.edgeTypes).length === 3 && document.querySelector('#edgeTypeSel').value === 'saved'`), true);
    });
    await check('installedCanvasUndoRedoTypes', async () => {
      await type('line'); await clickElement(`document.querySelector('#btnUndo')`);
      await until(() => evaluate(`!FundsCanvasEditor.history.status().busy
        && state.graph.getEdges().every(edge => edge.getModel().type === 'polyline')
        && document.querySelector('#edgeTypeSel').value === 'saved'`), 15000);
      await graph(3, flowIds); await clickElement(`document.querySelector('#btnRedo')`);
      await until(() => evaluate(`!FundsCanvasEditor.history.status().busy
        && state.graph.getEdges().every(edge => edge.getModel().type === 'line')
        && Object.keys(state.edgeTypes).length === 0`), 15000);
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

module.exports = { ACCOUNTS, AMOUNTS, SUMMARIES, REQUIRED_CHECKS, fixtureCsv, assertGraph, runInstalledGraphJourney };
