import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const require = createRequire(import.meta.url);
const presentation = require('../static/graph-presentation.js');
const edge = (source, target, amount, currency = 'CNY') => ({ source, target,
  amount_totals: [{ currency, amount }], ids: ['9007199254740993'], cnt: 1 });
const graph = () => ({ nodes: ['A', 'B', 'C', 'D', 'ISOLATED'].map(account => ({ account, id_no: 'SYNTHETIC', photo: 'private-source-photo' })),
  edges: [edge('A', 'B', '100.00'), edge('B', 'A', '0.01'), edge('B', 'C', '10.00'),
    edge('A', 'C', '0.10'), edge('C', 'D', '1.00', 'USD')] });
const sorted = iterable => [...iterable].sort();

test('defaults are pure JSON and UMD/CommonJS provide the same API', () => {
  const expected = { version: 1, relationFilter: { currency: null, min: '', max: '', active: false },
    amountWidth: { enabled: false, currency: null }, nodeLabels: {}, nodeStyles: {}, edgeLabels: {}, edgeStyles: {},
    hiddenAccounts: [], onlyAccounts: null, annotations: [] };
  assert.deepEqual(presentation.create(), expected);
  assert.deepEqual(presentation.create(null), expected);
  const root = fileURLToPath(new URL('../', import.meta.url)), sandbox = {};
  sandbox.window = sandbox;
  for (const name of ['money', 'graph-relations', 'graph-presentation']) {
    vm.runInNewContext(readFileSync(`${root}static/${name}.js`, 'utf8'), sandbox);
  }
  assert.equal(JSON.stringify(sandbox.FundsGraphPresentation.create()), JSON.stringify(expected));
  assert.equal(typeof sandbox.FundsGraphPresentation.displayData, 'function');
});

test('canvas palettes change only implicit label ink and leave explicit colours and persistent state intact', () => {
  assert.deepEqual(presentation.palette('light'), { ink: '#111111', mutedEdge: '#c9c9c9', edge: '#7f8b99', selection: '#2367ad' });
  assert.deepEqual(presentation.palette('dark'), { ink: '#dfe3ea', mutedEdge: '#3f4854', edge: '#7f8b99', selection: '#66d9ef' });
  assert.equal(Object.isFrozen(presentation.palette('light')), true);
  assert.equal(presentation.palette('system'), presentation.palette('dark'), 'the caller supplies a resolved theme');
  const custom = { fontSize: 20, fill: '#dfe3ea' }, before = JSON.stringify(custom);
  for (const kind of ['node', 'edge']) {
    const dark = presentation.effectiveLabel(kind), light = presentation.effectiveLabel(kind, { fontSize: 20 }, 'light');
    assert.equal(dark.fill, '#dfe3ea'); assert.equal(light.fill, '#111111');
    assert.deepEqual(presentation.effectiveLabel(kind, undefined, 'light'), { ...dark, fill: '#111111' });
    assert.equal(presentation.effectiveLabel(kind, custom, 'light').fill, '#dfe3ea', 'an explicit old-default colour stays explicit');
    assert.equal(presentation.effectiveLabel(kind, { fill: '#abc' }, 'light').fill, '#abc');
    assert.equal(light.lineHeight, 24, 'theme leaves font-size bounds unchanged');
  }
  assert.equal(JSON.stringify(custom), before);
  const saved = presentation.create({ theme: 'light', nodeLabels: { A: { fontSize: 20 } }, edgeLabels: { 'A=>B': custom } });
  assert.deepEqual(saved.nodeLabels.A, { fontSize: 20 });
  assert.deepEqual(saved.edgeLabels['A=>B'], custom);
  assert.equal(Object.hasOwn(saved, 'theme'), false, 'live appearance never enters saved graph presentation');
});

test('label style retains only approved fonts, bounded geometry and literal colors', () => {
  assert.deepEqual(presentation.sanitizeLabel({ fontFamily: 'Microsoft YaHei', fontSize: 8, fontWeight: 'bold',
    fontStyle: 'italic', fill: '#ABCDEF', lineHeight: 64, text: '<script>alert(1)</script>', underline: true }),
  { fontFamily: 'Microsoft YaHei', fontSize: 8, fontWeight: 'bold', fontStyle: 'italic', fill: '#ABCDEF', lineHeight: 64 });
  for (const fontFamily of ['Microsoft YaHei', 'PingFang SC', 'SimSun', 'SimHei', 'Arial', 'system-ui', 'sans-serif']) {
    assert.equal(presentation.sanitizeLabel({ fontFamily }).fontFamily, fontFamily);
  }
  for (const fontWeight of ['normal', 'bold', '400', '700', 400, 700]) {
    assert.equal(presentation.sanitizeLabel({ fontWeight }).fontWeight, fontWeight);
  }
  assert.deepEqual(presentation.sanitizeLabel({ fontFamily: 'url(evil)', fontSize: 32.01, fontWeight: 900,
    fontStyle: '<img>', fill: 'red', lineHeight: Infinity }), {});
  for (const fontSize of [7.99, NaN, -1, '12']) assert.deepEqual(presentation.sanitizeLabel({ fontSize }), {});
  assert.deepEqual(presentation.sanitizeLabel({ fontSize: 32, fill: '#abc', fontStyle: 'normal', lineHeight: 8 }),
    { fontSize: 32, fontStyle: 'normal', fill: '#abc', lineHeight: 8 });
  for (const invalid of [null, [], 'text', 7]) assert.deepEqual(presentation.sanitizeLabel(invalid), {});
});

test('edge styles accept only bounded width, hex stroke and supported dash patterns', () => {
  const source = { stroke: '#123456', lineWidth: 0.5, lineDash: [6, 4], endArrow: false,
    label: '<svg>', amount_totals: [{ currency: 'CNY', amount: '999.00' }] };
  assert.deepEqual(presentation.sanitizeEdge(source), { stroke: '#123456', lineWidth: 0.5, lineDash: [6, 4] });
  const result = presentation.sanitizeEdge(source); result.lineDash[0] = 10;
  assert.deepEqual(source.lineDash, [6, 4]);
  for (const lineDash of [[], [6, 4], [2, 3]]) {
    assert.deepEqual(presentation.sanitizeEdge({ lineWidth: 12, lineDash }), { lineWidth: 12, lineDash });
  }
  for (const lineDash of [[6, 4, 0], [4, 6], ['6', '4'], [2, 2], '6,4']) {
    assert.deepEqual(presentation.sanitizeEdge({ lineDash }), {});
  }
  for (const lineWidth of [0.49, 12.01, Infinity, '1.5']) assert.deepEqual(presentation.sanitizeEdge({ lineWidth }), {});
  for (const stroke of ['#12345', 'url(data:evil)', '#12345678', '<img>']) assert.deepEqual(presentation.sanitizeEdge({ stroke }), {});
});

test('persistent state drops transaction facts, photos, HTML and arbitrary fields', () => {
  const state = presentation.create({ version: 99, trackedIds: ['1'], nodes: graph().nodes, edges: graph().edges,
    html: '<script>', photo: 'secret', amount: '999999999.99', relationFilter: { currency: 'CNY', min: '1', max: '100' },
    amountWidth: { enabled: true, currency: 'CNY', total: '99999.99' },
    nodeLabels: { A: { fontSize: 12, text: 'SECRET LABEL', photo: 'secret', data: { amount: '1' } },
      B: { html: '<img>' }, C: 'SECRET LABEL' },
    edgeLabels: { 'A=>B': { fill: '#123', amount: '99.99' } },
    edgeStyles: { 'A=>B': { stroke: '#fed', ids: ['1'], source: 'C', target: 'D' } },
    hiddenAccounts: ['A', 'A', 'B', 1, null], onlyAccounts: ['B', 'B', 'C', undefined] });
  assert.deepEqual(state, { version: 1, relationFilter: { currency: 'CNY', min: '1.00', max: '100.00', active: true },
    amountWidth: { enabled: true, currency: 'CNY' }, nodeLabels: { A: { fontSize: 12 } }, nodeStyles: {},
    edgeLabels: { 'A=>B': { fill: '#123' } }, edgeStyles: { 'A=>B': { stroke: '#fed' } },
    hiddenAccounts: ['A', 'B'], onlyAccounts: ['B', 'C'], annotations: [] });
  const serialized = JSON.stringify(state);
  for (const secret of ['secret', 'SECRET', 'trackedIds', 'amount_totals', '999999999', '<script>', 'photo', 'ids']) {
    assert.equal(serialized.includes(secret), false);
  }
});

test('invalid persisted filters reset inactive and width encoding requires an explicit string currency', () => {
  for (const relationFilter of [{ currency: 'CNY', min: '-1' }, { currency: 'CNY', min: '1.001' },
    { currency: 'CNY', min: '2', max: '1' }, { min: '1' }]) {
    assert.equal(presentation.create({ relationFilter }).relationFilter.active, false);
  }
  assert.deepEqual(presentation.create({ relationFilter: { currency: '', min: '0' },
    amountWidth: { enabled: true, currency: '' } }).amountWidth, { enabled: true, currency: '' });
  for (const currency of [null, undefined, 156]) {
    assert.deepEqual(presentation.create({ amountWidth: { enabled: true, currency } }).amountWidth,
      { enabled: false, currency: null });
  }
  assert.deepEqual(presentation.create({ amountWidth: { enabled: false, currency: 'USD' } }).amountWidth,
    { enabled: false, currency: 'USD' });
});

test('state JSON round trips, clones nested data and resists special account keys', () => {
  const input = JSON.parse('{"nodeLabels":{"__proto__":{"fill":"#123"},"constructor":{"fontSize":12}},"edgeStyles":{"A=>B":{"lineDash":[2,3]}},"hiddenAccounts":["A"]}');
  const state = presentation.create(input), saved = JSON.parse(JSON.stringify(state));
  assert.deepEqual(presentation.create(saved), state);
  assert.equal(Object.hasOwn(state.nodeLabels, '__proto__'), true);
  assert.equal(Object.getPrototypeOf(state.nodeLabels), Object.prototype);
  assert.equal({}.fill, undefined);
  state.hiddenAccounts.push('B'); state.edgeStyles['A=>B'].lineDash[0] = 9;
  assert.deepEqual(input.hiddenAccounts, ['A']); assert.deepEqual(input.edgeStyles['A=>B'].lineDash, [2, 3]);
});

test('neighborhood, money filter and hidden/only scopes compose in order without mutating facts', () => {
  const source = graph(), before = JSON.stringify(source);
  const state = presentation.create({ relationFilter: { currency: 'CNY', min: '1.00' },
    hiddenAccounts: ['C'], onlyAccounts: ['A', 'B', 'C'] });
  const result = presentation.displayData(source, state, new Set(['A', 'B', 'C']));
  assert.deepEqual(sorted(result.nodeIds), ['A', 'B']);
  assert.deepEqual([...result.edgeKeys], ['A=>B']);
  assert.equal(result.nodes[0], source.nodes[0]); assert.equal(result.edges[0], source.edges[0]);
  assert.equal(result.stats.relationVisibleEdges, 2); assert.equal(result.stats.hiddenEdges, 1);
  assert.equal(result.stats.fullEdges, 5); assert.equal(result.stats.visibleEdges, 1);
  assert.equal(JSON.stringify(source), before);
  assert.equal(source.edges[0].ids[0], '9007199254740993');
  assert.equal(source.edges[0].amount_totals[0].amount, '100.00');
});

test('node-only visibility keeps isolated nodes and empty-only means show none', () => {
  const source = graph();
  const hidden = presentation.displayData(source, { hiddenAccounts: ['B'] });
  assert.deepEqual(sorted(hidden.nodeIds), ['A', 'C', 'D', 'ISOLATED']);
  assert.deepEqual([...hidden.edgeKeys], ['A=>C', 'C=>D']);
  assert.equal(source.nodes.length, 5); assert.equal(source.edges.length, 5);
  assert.equal(presentation.displayData(source, { onlyAccounts: [] }).nodes.length, 0);
  assert.deepEqual(presentation.displayData(source, {}, []).edges, []);
  assert.equal(presentation.displayData(source, {}, ['A', 'MISSING']).nodes.length, 1);
  assert.equal(presentation.displayData(null).nodes.length, 0);
  assert.throws(() => presentation.displayData(source, {}, 'A'), TypeError);
});

test('directed style keys stay separate and styles never rewrite source labels or money', () => {
  const source = graph();
  const state = presentation.create({ edgeLabels: { 'A=>B': { fill: '#f00' }, 'B=>A': { fill: '#00f' } },
    edgeStyles: { 'A=>B': { lineWidth: 8 }, 'B=>A': { lineWidth: 1 } },
    nodeLabels: { A: { fontFamily: 'SimSun' } } });
  const before = JSON.stringify(source), displayed = presentation.displayData(source, state);
  assert.equal(state.edgeStyles['A=>B'].lineWidth, 8); assert.equal(state.edgeStyles['B=>A'].lineWidth, 1);
  assert.equal(displayed.edges.length, 5); assert.equal(JSON.stringify(source), before);
  assert.equal(Object.hasOwn(displayed.nodes[0], 'style'), false);
  assert.equal(Object.hasOwn(displayed.edges[0], 'style'), false);
});

 test('effective labels constrain loaded line spacing and node appearance stays display-only',()=>{
  assert.equal(presentation.effectiveLabel('node',{fontSize:32,lineHeight:8}).lineHeight,36);
  assert.equal(presentation.effectiveLabel('edge',{fontSize:24}).lineHeight,28);
  for(const kind of ['node','edge']) {
    const style=presentation.effectiveLabel(kind,{});
    assert.equal(style.fontSize,13,'default graph text is readable at ordinary fitted zoom');
    assert.equal(style.lineHeight,18);
    assert.equal(presentation.effectiveLabel(kind,{fontSize:10,lineHeight:14}).fontSize,10,
      'an explicitly saved smaller font remains the user override');
    assert.equal(presentation.effectiveLabel(kind,{fontSize:10,lineHeight:14}).lineHeight,14);
  }
  const input={nodeStyles:{A:{size:48,stroke:'#123abc',lineWidth:2,fill:'secret',amount:'9.99',img:'private'}}};
  const state=presentation.create(input);
  assert.deepEqual(state.nodeStyles,{A:{size:48,stroke:'#123abc',lineWidth:2}});
  assert.deepEqual(presentation.create(JSON.parse(JSON.stringify(state))),state);
  assert.deepEqual(presentation.sanitizeNode({size:97,lineWidth:6,stroke:'red'}),{});
});

test('effective node and relationship labels always discard legacy backplates, borders and leaders',()=>{
  for(const kind of ['node','edge']){
    const style=presentation.effectiveLabel(kind,{fontFamily:'Microsoft YaHei',fontSize:16,lineHeight:22,
      background:{fill:'#000000',stroke:'#ffffff'},stroke:'#ff0000',lineWidth:5,labelLeader:{x:1,y:2}});
    assert.equal(style.background,undefined);assert.equal(style.stroke,null);assert.equal(style.lineWidth,0);
    assert.equal(style.labelLeader,undefined);assert.equal(style.fontSize,16);assert.equal(style.lineHeight,22);
    assert.equal(style.fontFamily,'Microsoft YaHei');
  }
});

test('node annotations keep only anchored, bounded text with safe styling', () => {
  const presentation = require('../static/graph-presentation.js');
  const kept = presentation.create({
    annotations: [
      { id: 'SYN-A', text: '  实际控制人  ', anchor: 'above', dx: 20, dy: -8, size: 15, fontFamily: 'SimHei', color: '#FF0000' },
      { id: 'SYN-B', text: '文字下方', anchor: 'sideways' },
      { id: 'SYN-C', text: '   ' },
      { text: '没有主体', anchor: 'above' },
      { id: 'SYN-D', text: '越界偏移', dx: 9999, dy: -9999, fontFamily: 'Comic Sans MS', color: 'red' },
    ],
  }).annotations;
  assert.deepEqual(kept, [
    { id: 'SYN-A', text: '实际控制人', anchor: 'above', dx: 20, dy: -8, size: 15, fontFamily: 'SimHei', color: '#ff0000' },
    { id: 'SYN-B', text: '文字下方', anchor: 'below', dx: 0, dy: 0, size: 13, fontFamily: 'Arial', color: '#e3a12b' },
    { id: 'SYN-D', text: '越界偏移', anchor: 'below', dx: 400, dy: -400, size: 13, fontFamily: 'Arial', color: '#e3a12b' },
  ]);
  // 旧快照没有标注字段时保持空数组，不报错。
  assert.deepEqual(presentation.create({ version: 1 }).annotations, []);
  // 字号被夹在 9..40，且只接受白名单字体与 #RRGGBB 颜色。
  const clamped = presentation.create({ annotations: [{ id: 'SYN-E', text: '字号', size: 99 }] }).annotations[0];
  assert.equal(clamped.size, 40);
  assert.equal(clamped.fontFamily, 'Arial');
  assert.equal(clamped.color, '#e3a12b');
});
test('annotations keep multiple lines within six rows', () => {
  const presentation = require('../static/graph-presentation.js');
  const multiline = presentation.create({
    annotations: [{ id: 'SYN-A', text: '第一行\r\n  第二行  \n\n\n\n第三行\n四\n五\n六\n七\n八' }],
  }).annotations[0];
  // CRLF 归一、逐行去空白、连续空行压成一个空行、超过 6 行截断。
  assert.equal(multiline.text, '第一行\n第二行\n\n第三行\n四\n五');
  assert.equal(multiline.text.split('\n').length, 6);
  const collapsed = presentation.create({ annotations: [{ id: 'SYN-B', text: '\n\n只有一行\n\n' }] }).annotations[0];
  assert.equal(collapsed.text, '只有一行');
});
test('annotations preserve exact long account IDs and normalize duplicate anchors without changing identities', () => {
  const id = 'SYN-' + 'A'.repeat(100);
  const result = presentation.create({ annotations: [{ id, text: '第一条' }, { id, text: '重复' }] }).annotations;
  assert.equal(result.length, 1); assert.equal(result[0].id, id);
});
