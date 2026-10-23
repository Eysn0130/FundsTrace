import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
const start = source.indexOf('function captureGraphLocation()');
const end = source.indexOf('\nfunction installNodeFinder()', start);
assert.ok(start >= 0 && end > start, 'the real graph locator boundary must be present');
const locatorSource = source.slice(start, end);
assert.ok(locatorSource.includes('window.FundsGraphLocator = Object.freeze('));

// Run the product's public locator, account membership and binding checks.
// Geometry/animation have separate graph_interaction coverage; this camera
// probe records precisely which existing graph items a lookup would frame.
function scene(accounts, visible = accounts.map(row => row.account)) {
  const calls = [], notices = [];
  const items = new Map(accounts.map((row, index) => [row.account, {
    getModel: () => ({ id: row.account, x: index * 80, y: 0 }),
  }]));
  const matrix = [1, 0, 0, 0, 1, 0, 0, 0, 1];
  const state = { caseId: '1', caseGeneration: 1, canvasId: '0', canvasGeneration: 1,
    graphView: { revision: 1, index: { nodeIds: new Set(visible) } }, lastGraph: { nodes: accounts },
    graph: { findById: id => items.get(id), get: key => key === 'group' ? { getMatrix: () => matrix } : undefined },
    graphCamera: { cancelFocus() {}, getIntent: () => 1 },
  };
  const context = vm.createContext({ state, window: {}, graphLocateFeedback: null,
    caseContext: () => ({ caseId: state.caseId, generation: state.caseGeneration, canvasId: state.canvasId, canvasGeneration: state.canvasGeneration }),
    isCurrentCase: binding => binding.caseId === state.caseId && binding.generation === state.caseGeneration
      && binding.canvasId === state.canvasId && binding.canvasGeneration === state.canvasGeneration,
    toast: value => notices.push(value), fmtCount: value => String(value), clearTimeout() {},
    recordFocus: targets => { calls.push(Array.from(targets, item => item.getModel().id)); return { ok: true }; },
  });
  vm.runInContext(locatorSource, context, { filename: 'app.js:graph-locator' });
  vm.runInContext('focusGraphItems = async items => recordFocus(items);', context);
  return { state, calls, notices, locator: context.window.FundsGraphLocator };
}

test('exact graph location: an absent selected account never falls back to a containing account', async () => {
  const f = scene([{ account: '10001', name: '0001', id_no: 'ID-0001' }]);
  const result = await f.locator.previewFields({ exactAccount: '0001' }, { binding: f.locator.capture() });
  assert.equal(result.ok, false);
  assert.match(result.reason, /没有匹配账户/);
  assert.deepEqual(f.calls, [], 'a missing selection must not frame an unrelated account, name or identity');
});

test('exact graph location: differently cased account keys retain the selected object', async () => {
  const f = scene([{ account: 'MiXeD', name: '同名' }, { account: 'mixed', name: '同名' }]);
  for (const account of ['MiXeD', 'mixed']) {
    const result = await f.locator.previewFields({ exactAccount: account }, { binding: f.locator.capture() });
    assert.equal(result.ok, true);
    assert.deepEqual(f.calls.at(-1), [account]);
  }
});

test('exact graph location: selected counterparties use the same literal key rule', async () => {
  const absent = scene([{ account: '10001', name: '0001' }]);
  assert.equal((await absent.locator.previewFields({ exactCounterparty: '0001' }, { binding: absent.locator.capture() })).ok, false);
  assert.deepEqual(absent.calls, []);
  const present = scene([{ account: '0001' }, { account: '10001' }, { account: 'MiXeD' }, { account: 'mixed' }]);
  for (const account of ['0001', 'MiXeD', 'mixed']) {
    assert.equal((await present.locator.previewFields({ exactCounterparty: account }, { binding: present.locator.capture() })).ok, true);
    assert.deepEqual(present.calls.at(-1), [account]);
  }
});

test('exact graph location: full leading-zero account strings are never numerically coerced', async () => {
  const account = '00090071992547409931234567890', neighbor = '90071992547409931234567890';
  const f = scene([{ account }, { account: neighbor }]);
  assert.equal((await f.locator.previewFields({ exactAccount: account }, { binding: f.locator.capture() })).ok, true);
  assert.deepEqual(f.calls, [[account]]);
});

test('exact graph location: filtered targets do not change the current view to locate neighbors', async () => {
  const f = scene([{ account: '0001' }, { account: '10001' }], ['10001']);
  const before = [...f.state.graphView.index.nodeIds];
  const result = await f.locator.previewFields({ exactAccount: '0001' }, { binding: f.locator.capture() });
  assert.equal(result.ok, false); assert.match(result.reason, /不在当前显示范围/);
  assert.deepEqual(f.calls, []); assert.deepEqual([...f.state.graphView.index.nodeIds], before);
});

test('exact graph location: a late A-B-A binding cannot frame the next case generation', async () => {
  const f = scene([{ account: '0001' }]), binding = f.locator.capture();
  f.state.caseId = '2'; f.state.caseGeneration += 1;
  f.state.caseId = '1'; f.state.caseGeneration += 1;
  const result = await f.locator.previewFields({ exactAccount: '0001' }, { binding });
  assert.equal(result.ok, false); assert.match(result.reason, /画布或视角已变化/); assert.deepEqual(f.calls, []);
});

test('exact graph location: a free name still frames every matching existing account', async () => {
  const f = scene([{ account: '0001', name: '张三丰' }, { account: '10001', name: '张三丰' }]);
  assert.equal((await f.locator.previewFields({ name: '张' }, { binding: f.locator.capture() })).ok, true);
  assert.deepEqual(f.calls, [['0001', '10001']]);
});

test('free counterparty identity lookup frames only matching current account identities', async () => {
  const f = scene([{ account: '0001', id_no: 'SYN-ID-A' }, { account: '10001', id_no: 'SYN-ID-B' }]);
  assert.equal((await f.locator.previewFields({ counterparty: 'id-a' }, { binding: f.locator.capture() })).ok, true);
  assert.deepEqual(f.calls, [['0001']]);
});
