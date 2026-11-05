import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const sandbox = { window: {} };
for (const name of ['money', 'graph-relations', 'graph-presentation', 'workspace-drafts']) {
  vm.runInNewContext(readFileSync(new URL(`../static/${name}.js`, import.meta.url), 'utf8'), sandbox);
}
const create = sandbox.window.FundsWorkspaceDrafts.create;
const plain = value => JSON.parse(JSON.stringify(value));

test('case and canvas drafts remain separate with exact string IDs through A–B–A switching', () => {
  const store = create(), a = '9007199254740993123', b = '9007199254740993124';
  store.capture({ caseId: a, canvasId: '0' }, { positions: { node: { x: 10, y: 20 } } });
  store.capture({ caseId: a, canvasId: 'canvas:2' }, { positions: { node: { x: 30, y: 40 } } });
  store.capture({ caseId: b, canvasId: '0' }, { positions: { node: { x: 50, y: 60 } } });
  assert.deepEqual(plain(store.get(a, '0').positions.node), { x: 10, y: 20 });
  assert.deepEqual(plain(store.get(a, 'canvas:2').positions.node), { x: 30, y: 40 });
  assert.deepEqual(plain(store.get(b, '0').positions.node), { x: 50, y: 60 });
  assert.equal(store.lastCanvas(a), 'canvas:2');
  assert.equal(store.lastCanvas(b), '0');
  assert.equal(store.get(b, 'canvas:2'), null);
  assert.deepEqual(plain(store.retainedCaseIds()), [a, b]);
  assert.throws(() => store.capture({ caseId: Number.MAX_SAFE_INTEGER + 1, canvasId: 0 }, {}), /案件与画布/);
});

test('capture and every read isolate nested geometry, presentation, query and form values', () => {
  const store = create();
  const source = {
    positions: { node: { x: 7, y: 9 } }, edgeBendPos: { edge: [{ x: 2, y: 3 }] },
    presentation: { nodeStyles: { node: { size: 45 } }, hiddenAccounts: ['hidden'] },
    query: { account: '001234', anchorIds: ['9007199254740993999'] },
    nodeDrafts: [{ account: 'node', idValue: 'unsubmitted synthetic identity', focusValue: 2 }],
    camera: [2, 0, 0, 0, 2, 0, 17, 31, 1],
  };
  store.capture({ caseId: 1, canvasId: 0 }, source);
  source.positions.node.x = 999; source.edgeBendPos.edge[0].y = 999;
  source.presentation.nodeStyles.node.size = 96; source.query.anchorIds.push('other');
  source.nodeDrafts[0].idValue = 'changed'; source.camera[6] = 999;
  const first = store.get('1', '0');
  assert.equal(first.positions.node.x, 7); assert.equal(first.edgeBendPos.edge[0].y, 3);
  assert.equal(first.presentation.nodeStyles.node.size, 45);
  assert.deepEqual(plain(first.query.anchorIds), ['9007199254740993999']);
  assert.equal(first.nodeDrafts[0].idValue, 'unsubmitted synthetic identity');
  assert.equal(first.camera[6], 17);
  first.presentation.hiddenAccounts.push('changed'); first.query.account = 'changed'; first.positions.node.x = 999;
  assert.deepEqual(plain(store.get(1, 0).presentation.hiddenAccounts), ['hidden']);
  assert.equal(store.get(1, 0).query.account, '001234'); assert.equal(store.get(1, 0).positions.node.x, 7);
});

test('full graph and business fields cannot enter drafts even beside valid geometry and form inputs', () => {
  const store = create(), fact = 'SYNTHETIC_FACT_MUST_NOT_RETAIN';
  store.capture({ caseId: 'case', canvasId: 'canvas' }, {
    trackedIds: ['flow'], lastGraph: { nodes: [{ amount: fact }] }, nodes: [{ id: 'node', amount: fact }],
    edges: [{ amount: fact }], totals: fact, photo: fact,
    positions: { node: { x: 1, y: 2, name: fact, amount: fact, photo: fact } },
    edgeSnapshotLabels: { edge: { lx: 8, ly: 9, amount: fact, text: fact, manual: true } },
    presentation: { nodeLabels: { node: { fontSize: 15, text: fact } }, amount_totals: fact },
    graphView: { labelMode: 'all', fullIndex: fact, stats: fact },
    query: { kw: 'typed query', min_amount: '9007199254740993123.45', page: 3, rows: [{ amount: fact }], total: fact },
    selection: { nodeIds: ['node'], account: 'node', profile: fact },
    nodeDrafts: [{ account: 'node', idValue: 'typed identity', idBase: fact, profile: fact, photo: fact }],
  });
  const draft = store.get('case', 'canvas');
  assert.equal(JSON.stringify(draft).includes(fact), false);
  assert.deepEqual(plain(draft.positions), { node: { x: 1, y: 2 } });
  assert.deepEqual(plain(draft.edgeSnapshotLabels.edge), { lx: 8, ly: 9, manual: true });
  assert.deepEqual(plain(draft.nodeDrafts), [{ account: 'node', idValue: 'typed identity' }]);
  assert.equal(draft.query.min_amount, '9007199254740993123.45', 'typed filter money remains lossless text');
  assert.equal(draft.query.page, 3);
});

test('invalid geometry is ignored while display and selection identifiers remain lossless', () => {
  const store = create(), exact = '00009007199254740993123';
  const positions = Object.fromEntries([['__proto__', { x: 5, y: 6 }], [exact, { x: 11, y: 13 }], ['bad', { x: NaN, y: 0 }]]);
  store.capture({ caseId: 'A', canvasId: '0' }, {
    positions, edgeLabelPos: { valid: 0.25, outside: 1.1 }, edgeTypes: { edge: 'polyline', invalid: 'unknown' },
    autoRoutes: { edge: { source: { x: 1, y: 2 }, target: { x: 8, y: 9 }, controlPoints: [{ x: 3, y: 4 }], curvePolicy: 'compact-arc' } },
    camera: [1, 0, 0, 0, 1, 0, 18, 19, 1], cameraOffset: { x: 4, y: 5 }, cameraTransform: { scale: 2, x: 4, y: 5 },
    layout: 'dagre', edgeLabels: ['money', 'currency', 'other'],
    graphView: { visibleIds: [exact, exact, 12], root: exact, direction: 'in', labelMode: 'focus' },
    selection: { nodeIds: [exact, 12], edgeKey: `${exact}=>other` },
  });
  const draft = store.get('A', '0');
  assert.deepEqual(plain(draft.positions), { ['__proto__']: { x: 5, y: 6 }, [exact]: { x: 11, y: 13 } });
  assert.deepEqual(plain(draft.edgeLabelPos), { valid: 0.25 });
  assert.deepEqual(plain(draft.edgeTypes), { edge: 'polyline' });
  assert.deepEqual(plain(draft.graphView.visibleIds), [exact]); assert.equal(draft.graphView.root, exact);
  assert.deepEqual(plain(draft.selection.nodeIds), [exact]); assert.equal(draft.selection.edgeKey, `${exact}=>other`);
  assert.deepEqual(plain(draft.cameraTransform), { scale: 2, x: 4, y: 5 });
  assert.equal(draft.autoRoutes.edge.controlPoints[0].x, 3);
});

test('explicit canvas/case deletion clears only that scope and remembered canvas follows surviving captures', () => {
  const store = create();
  store.capture({ caseId: 'A', canvasId: '0' }, {});
  store.capture({ caseId: 'A', canvasId: '2' }, {});
  store.capture({ caseId: 'B', canvasId: '0' }, {});
  store.capture({ caseId: 'A', canvasId: '0' }, { query: { kw: 'latest' } });
  assert.equal(store.lastCanvas('A'), '0');
  assert.equal(store.removeCanvas('A', '0'), true); assert.equal(store.get('A', '0'), null);
  assert.equal(store.lastCanvas('A'), '2'); assert.ok(store.get('B', '0'));
  assert.equal(store.removeCase('A'), true); assert.equal(store.lastCanvas('A'), null);
  assert.deepEqual(plain(store.retainedCaseIds()), ['B']);
  store.clear(); assert.equal(store.get('B', '0'), null); assert.deepEqual(plain(store.retainedCaseIds()), []);
});

test('retention is session-local and capturing many other canvases never silently evicts earlier work', () => {
  const store = create();
  store.capture({ caseId: 'first', canvasId: '0' }, { query: { kw: 'keep my work' } });
  for (let i = 0; i < 150; i++) store.capture({ caseId: 'other', canvasId: String(i) }, { positions: { node: { x: i, y: i } } });
  assert.equal(store.get('first', '0').query.kw, 'keep my work');
  assert.equal(store.get('other', '0').positions.node.x, 0);
  assert.equal(store.lastCanvas('other'), '149');
  assert.equal(create().get('first', '0'), null, 'a fresh session store has no previous user work');
});


test('resolution freshness retains explicit null and valid hashes but not unknown revision or graph facts',()=>{
  const store=create(),scope={caseId:'case',canvasId:'canvas'},view={visibleIds:['node'],root:'node',direction:'out'};
  store.capture(scope,{graphView:view});
  assert.equal(Object.hasOwn(store.get('case','canvas'),'resolutionRevision'),false);
  for(const revision of [null,'a'.repeat(64)]){
    store.capture(scope,{graphView:view,resolutionRevision:revision,graphData:{private:'must not retain'},lastGraph:{amount:'must not retain'}});
    const draft=store.get('case','canvas');assert.equal(Object.hasOwn(draft,'resolutionRevision'),true);
    assert.equal(draft.resolutionRevision,revision);assert.equal('graphData' in draft,false);assert.equal('lastGraph' in draft,false);
  }
  for(const revision of [1,{},'invalid',false]){
    store.capture(scope,{graphView:view,resolutionRevision:revision});assert.equal(Object.hasOwn(store.get('case','canvas'),'resolutionRevision'),false);
  }
});
