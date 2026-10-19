import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const require = createRequire(import.meta.url);
const relations = require('../static/graph-relations.js');
const key = relations.edgeKey;
const edge = (source, target, amount, currency = 'CNY', extra = {}) => ({ source, target,
  amount_totals: [{ currency, amount }], ...extra });
const graph = () => ({ nodes: ['A', 'B', 'C', 'D', 'ISOLATED'].map(account => ({ account })), edges: [
  edge('A', 'B', '9007199254740993.01'), edge('B', 'A', '9007199254740993.02'),
  edge('B', 'C', '0.01'), edge('A', 'C', '0.10'), edge('C', 'D', '4.00', 'USD'),
  edge('D', 'A', '0.00', ''),
] });
const sorted = values => [...values].sort();

test('browser UMD and CommonJS expose the same public policy', () => {
  const root = fileURLToPath(new URL('../', import.meta.url)), sandbox = {};
  sandbox.window = sandbox;
  vm.runInNewContext(readFileSync(`${root}static/money.js`, 'utf8'), sandbox);
  vm.runInNewContext(readFileSync(`${root}static/graph-relations.js`, 'utf8'), sandbox);
  assert.equal(typeof sandbox.FundsGraphRelations.data, 'function');
  assert.equal(sandbox.FundsGraphRelations.validateFilter({ currency: 'USD', min: '1' }).filter.min, '1.00');
  assert.equal(relations.edgeKey({ source: 'A', target: 'B', id: 'unstable-g6-id' }), 'A=>B');
});

test('filter validation is explicit, exact and independent of a forged active flag', () => {
  assert.deepEqual(relations.normalizeFilter(), { currency: null, min: '', max: '', active: false });
  assert.deepEqual(relations.normalizeFilter({ currency: 'CNY', min: ' 0.1 ', max: '9007199254740993.02' }),
    { currency: 'CNY', min: '0.10', max: '9007199254740993.02', active: true });
  assert.equal(relations.normalizeFilter({ currency: '', active: false }).active, true);
  for (const value of ['-1', '-0', 'NaN', 'Infinity', '1.001', '1e3', '1,000', '.50', '+1', 1, NaN, Infinity]) {
    const validation = relations.validateFilter({ currency: 'CNY', min: value });
    assert.equal(validation.ok, false, String(value));
    assert.equal(validation.code, 'INVALID_AMOUNT'); assert.equal(validation.field, 'min');
    assert.ok(validation.error.length > 0);
  }
  assert.equal(relations.validateFilter({ min: '1' }).code, 'CURRENCY_REQUIRED');
  assert.equal(relations.validateFilter({ currency: 156 }).code, 'INVALID_CURRENCY');
  assert.equal(relations.validateFilter({ currency: 'CNY', min: '9007199254740993.02', max: '9007199254740993.01' }).code,
    'REVERSED_RANGE');
  assert.equal(relations.validateFilter(null).code, 'INVALID_FILTER');
});

test('bounds include exact cents above Number precision and retain direction', () => {
  const source = graph();
  const result = relations.data(source, { currency: 'CNY', min: '9007199254740993.02', max: '9007199254740993.02' });
  assert.deepEqual([...result.edgeKeys], ['B=>A']);
  assert.deepEqual(sorted(result.nodeIds), ['A', 'B']);
  assert.equal(result.edges[0], source.edges[1]);
  assert.equal(result.nodes[0], source.nodes[0]);
  assert.deepEqual(relations.data(source, { currency: 'CNY', min: '0.01', max: '0.10' }).edges.map(key), ['B=>C', 'A=>C']);
});

test('normalized bounds fit the saved HTML export contract without changing cent precision', () => {
  const limit = '9'.repeat(37) + '.99';
  assert.equal(relations.normalizeFilter({currency:'CNY',min:limit}).min, limit);
  assert.equal(relations.normalizeFilter({currency:'CNY',max:'0'.repeat(50) + '1.2'}).max, '1.20');
  for (const field of ['min','max']) {
    const rejected = relations.validateFilter({currency:'CNY',[field]:'9'.repeat(38)});
    assert.equal(rejected.ok,false); assert.equal(rejected.code,'AMOUNT_TOO_LONG'); assert.equal(rejected.field,field);
  }
  assert.equal(relations.validateFilter({currency:'X'.repeat(33)}).field,'currency');
});

test('edge-level membership excludes a small relation whose endpoints both survive', () => {
  const source = { nodes: ['A', 'B', 'C', 'ISOLATED'].map(account => ({ account })), edges: [
    edge('A', 'B', '2.00'), edge('B', 'C', '1.00'), edge('A', 'C', '0.01'),
  ] };
  const result = relations.data(source, { currency: 'CNY', min: '0.10' });
  assert.ok(result.nodeIds.has('A')); assert.ok(result.nodeIds.has('C'));
  assert.equal(result.edgeKeys.has('A=>C'), false);
  assert.ok(result.edgeKeys.has('B=>C'));
  assert.equal(result.edges.includes(source.edges[2]), false);
  assert.ok(result.edges.includes(source.edges[1]));
  assert.equal(result.nodes.some(node => node.account === 'ISOLATED'), false);
  assert.equal(relations.data(source, { currency: 'CNY', min: '0.10' }, { keepIsolated: true }).nodes.length, source.nodes.length);
});

test('currency selection never mixes totals, defaults to CNY or falls back to total/count', () => {
  const multi = edge('A', 'B', '1.00', 'CNY', { total: null, cnt: 999,
    amount_totals: [{ currency: 'CNY', amount: '1.00' }, { currency: 'USD', amount: '100.00' }] });
  const source = { nodes: [{ account: 'A' }, { account: 'B' }], edges: [multi,
    { source: 'B', target: 'A', total: '1000.00', cnt: 1000 }] };
  assert.equal(relations.data(source, { currency: 'CNY', min: '10' }).edges.length, 0);
  assert.deepEqual(relations.data(source, { currency: 'USD', min: '10' }).edges, [multi]);
  assert.equal(relations.data(source, { currency: 'USD' }).stats.missingCurrencyEdges, 1);
  const unknown = relations.data(graph(), { currency: '', min: '0', max: '0' });
  assert.deepEqual([...unknown.edgeKeys], ['D=>A']);
  assert.deepEqual(relations.currencyChoices([...graph().edges, multi]), [
    { value: '', label: '未标注币种', unknown: true },
    { value: 'CNY', label: 'CNY', unknown: false }, { value: 'USD', label: 'USD', unknown: false },
  ]);
});

test('inactive and empty policies preserve isolated nodes without mutating facts', () => {
  const source = graph(), before = JSON.stringify(source);
  for (const item of [...source.nodes, ...source.edges]) Object.freeze(item);
  Object.freeze(source.nodes); Object.freeze(source.edges); Object.freeze(source);
  const inactive = relations.data(source);
  assert.equal(inactive.nodes.length, 5); assert.equal(inactive.edges.length, 6);
  inactive.nodes.pop(); inactive.edges.pop(); inactive.nodeIds.clear(); inactive.edgeKeys.clear();
  relations.data(source, { currency: 'CNY', min: '0.10' });
  relations.encodeWidths(source.edges, 'CNY');
  assert.equal(JSON.stringify(source), before);
  assert.deepEqual(relations.data().nodes, []);
  assert.deepEqual(relations.data(null).edges, []);
  assert.deepEqual(relations.data({ nodes: [], edges: [] }, { currency: 'USD' }).edges, []);
  assert.equal(relations.encodeWidths([], 'USD').legend.minAmount, null);
  assert.equal(relations.encodeWidths(null, 'USD').widths.size, 0);
  assert.deepEqual(relations.currencyChoices(null), []);
});

test('bad matching totals fail filtering explicitly and remain distinct from missing currency in widths', () => {
  for (const amount of ['-1.00', '1.001', 'NaN', 10]) {
    const bad = edge('A', 'B', amount);
    const source = { nodes: [{ account: 'A' }, { account: 'B' }], edges: [bad] };
    assert.throws(() => relations.data(source, { currency: 'CNY' }), { code: 'INVALID_AMOUNT' });
    const encoded = relations.encodeWidths([bad, edge('B', 'A', '1.00', 'USD')], 'CNY');
    assert.equal(encoded.widths.size, 0); assert.equal(encoded.invalid.size, 1);
    assert.equal(encoded.missing.has('B=>A'), true); assert.equal(encoded.missing.has('A=>B'), false);
  }
  const duplicate = edge('A', 'B', '1.00');
  duplicate.amount_totals.push({ currency: 'CNY', amount: '2.00' });
  assert.throws(() => relations.data({ nodes: [], edges: [duplicate] }, { currency: 'CNY' }),
    { code: 'AMBIGUOUS_CURRENCY_TOTAL' });
});

test('widths are bounded and monotone, including adjacent enormous cent values', () => {
  const edges = ['9007199254740993.00', '9007199254740993.01', '9007199254740993.02'].map((amount, i) => edge('R', `N${i}`, amount));
  const result = relations.encodeWidths(edges, 'CNY', { minWidth: 2, maxWidth: 8 });
  const values = edges.map(item => result.widths.get(key(item)));
  assert.equal(values[0], 2); assert.equal(values[2], 8);
  assert.ok(values[0] < values[1] && values[1] < values[2]);
  assert.equal(result.legend.minAmount, '9007199254740993.00');
  assert.equal(result.legend.maxAmount, '9007199254740993.02');
  assert.equal(result.legend.scale, 'sqrt');
  const extreme = relations.encodeWidths([edge('A', 'B', '0.01'), edge('B', 'C', '99999999999999999999999999999999.99')], 'CNY');
  assert.equal(extreme.widths.get('A=>B'), 1.5); assert.equal(extreme.widths.get('B=>C'), 7);
  assert.equal(result.missing.size, 0); assert.equal(result.invalid.size, 0);
});

test('equal positive amounts share a middle width; zero amounts use minimum width', () => {
  const equal = relations.encodeWidths([edge('A', 'B', '1'), edge('B', 'A', '1.00')], 'CNY');
  assert.equal(equal.widths.get('A=>B'), 4.25); assert.equal(equal.widths.get('B=>A'), 4.25);
  assert.equal(equal.legend.equalWidth, 4.25);
  assert.equal(relations.encodeWidths([edge('A', 'B', '0.00')], 'CNY').widths.get('A=>B'), 1.5);
  assert.throws(() => relations.encodeWidths([], null), { code: 'CURRENCY_REQUIRED' });
  for (const options of [{ minWidth: 0 }, { maxWidth: NaN }, { minWidth: 4, maxWidth: 3 }, { minWidth: '2' }]) {
    assert.throws(() => relations.encodeWidths([], 'CNY', options), { code: 'INVALID_WIDTH_RANGE' });
  }
});
