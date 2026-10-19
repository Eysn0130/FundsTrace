import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const app = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
const index = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const money = readFileSync(new URL('../static/money.js', import.meta.url), 'utf8');
const fields = {
  A: ['trade_no', 'trade_time', '_time2', 'self_account', 'payer_name', 'payer_account', 'payer_bank', 'payee_name', 'payee_account', 'payee_bank', 'amount', 'balance', 'currency', 'summary', 'channel'],
  B: ['trade_no', 'trade_time', '_time2', 'self_name', 'self_account', 'counter_name', 'counter_account', 'counter_bank', 'direction', 'income_amt', 'expense_amt', 'amount', 'balance', 'currency', 'summary', 'channel'],
};
const decode = value => value.replace(/&(amp|lt|gt|quot|#39);/g, (_match, name) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" }[name]));
const plain = value => decode(value.replace(/<[^>]*>/g, ' ')).replace(/\s+/g, ' ').trim();

function harness() {
  const nodes = new Map();
  const create = id => {
    const node = { id, style: {}, dataset: {}, textContent: '', open: false, isConnected: true,
      classList: { add() {}, remove() {}, toggle() {} }, setAttribute() {} };
    let markup = '';
    Object.defineProperty(node, 'innerHTML', { get: () => markup, set(value) {
      markup = value;
      if (id !== 'impMapping') return;
      // Replacing a browser subtree creates a new details identity. Its open
      // state must come from markup or an explicit restoration by renderImport.
      const previous = nodes.get('impAdvanced');
      if (previous) previous.isConnected = false;
      nodes.delete('impAdvanced');
      const details = value.match(/<details\b([^>]*\bid="impAdvanced"[^>]*)>/);
      if (details) { const advanced = create('impAdvanced'); advanced.open = /\bopen(?:\s|=|$)/.test(details[1]); }
    } });
    nodes.set(id, node); return node;
  };
  const element = selector => {
    const id = selector.replace(/^#/, '');
    if (id === 'impAdvanced') return nodes.get(id) || null;
    if (!nodes.has(id)) create(id);
    return nodes.get(id);
  };
  const modes = [{ value: 'A', checked: false }, { value: 'B', checked: false }];
  const selects = () => [...element('impMapping').innerHTML.matchAll(/<select\b([^>]*)>([\s\S]*?)<\/select>/g)].map(([, attributes, options]) => {
    const field = attributes.match(/\bdata-field="([^"]+)"/)?.[1];
    const selected = [...options.matchAll(/<option\b([^>]*)>([\s\S]*?)<\/option>/g)].find(([, attrs]) => /\bselected(?:\s|=|$)/.test(attrs));
    return { dataset: { field }, value: selected ? decode(selected[1].match(/\bvalue="([^"]*)"/)?.[1] || '') : '' };
  });
  const context = vm.createContext({ $: element,
    $$: selector => selector === 'input[name=impMode]' ? modes : selector === '#impMapping select' ? selects() : [],
    state: { imp: null } });
  vm.runInContext(money, context);
  vm.runInContext(app.slice(app.indexOf('function esc('), app.indexOf('function toast(')), context);
  vm.runInContext(app.slice(app.indexOf('const FIELD_LABELS ='), app.indexOf('async function loadBatches()')), context);
  return { element, modes,
    render(result) { context.fixture = result; return vm.runInContext('state.imp = fixture; renderImport(fixture)', context); },
    mapping() { return JSON.parse(vm.runInContext('JSON.stringify(collectMapping())', context)); },
    rows() { return [...element('impMapping').innerHTML.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/g)]
      .filter(([, row]) => /\bdata-field="/.test(row)).map(([, row]) => ({ field: row.match(/\bdata-field="([^"]+)"/)[1], html: row, text: plain(row) })); },
  };
}

function fixture(mode) {
  const columns = fields[mode].map(field => `合成-${field}`);
  return { import_id: 'SYN-IMPORT', filename: '合成文件.csv', mode, columns,
    mapping: Object.fromEntries(fields[mode].map((field, index) => [field, columns[index]])),
    self_accounts_in_file: [{ account: 'SYN-SELF', name: '合成本方', count: 2 }],
    preview: [{ trade_time: '2026-10-16 12:34:56', payer_name: '合成付款方', payer_account: 'SYN-A',
      payee_name: '合成收款方', payee_account: 'SYN-B', amount: '9007199254740993.07', currency: 'CNY', summary: '合成摘要' }],
    stats: { total: 3, ok: 2, skipped: 1, bad_examples: [{ row: '合成第3行', reason: '合成时间缺失' }] } };
}

test('the import source matrix preserves all mode A and B fields and selected source columns', () => {
  for (const mode of ['A', 'B']) {
    const h = harness(), result = fixture(mode); h.render(result);
    const rows = h.rows();
    assert.deepEqual(rows.map(row => row.field).sort(), [...fields[mode]].sort(), `mode ${mode} keeps its ${fields[mode].length} editable mappings in matrix rows`);
    assert.match(h.element('impMapping').innerHTML, /<table\b/);
    assert.deepEqual(h.mapping(), { mode, mapping: result.mapping });
    for (const row of rows) {
      assert.match(row.html, /<select\b/);
      assert.match(row.text, /已识别|已映射/);
    }
  }
});

test('matrix requirements describe actual alternatives and the optional mode A self account', () => {
  for (const mode of ['A', 'B']) {
    const h = harness(); h.render(fixture(mode));
    const rows = Object.fromEntries(h.rows().map(row => [row.field, row.text]));
    assert.match(rows.trade_time || '', /必填/);
    for (const field of ['trade_no', '_time2', 'balance', 'currency', 'summary', 'channel']) assert.match(rows[field] || '', /选填/, field);
    if (mode === 'A') {
      assert.match(rows.self_account || '', /选填/);
      assert.doesNotMatch(rows.self_account || '', /必填/);
      assert.match(rows.amount || '', /必填/);
      for (const field of ['payer_name', 'payer_account', 'payee_name', 'payee_account']) assert.match(rows[field] || '', /二选一|至少.*一/, field);
    } else {
      assert.match(rows.self_account || '', /必填/);
      for (const field of ['counter_name', 'counter_account']) assert.match(rows[field] || '', /二选一|至少.*一/, field);
      for (const field of ['direction', 'amount']) assert.match(rows[field] || '', /替代|二选|收入.*支出/, field);
    }
  }
});

test('single-account examples derive counterpart and direction from normalized payer and payee fields', () => {
  const cases = [
    { row: { self_account: 'SYN-SELF', self_name: '合成本方', payer_account: 'SYN-IN-COUNTER', payer_name: '合成来款方',
      payee_account: 'SYN-SELF', payee_name: '合成本方' }, account: 'SYN-IN-COUNTER', name: '合成来款方', direction: '收入' },
    { row: { self_account: 'SYN-SELF', self_name: '合成本方', payer_account: 'SYN-SELF', payer_name: '合成本方',
      payee_account: 'SYN-OUT-COUNTER', payee_name: '合成收款方' }, account: 'SYN-OUT-COUNTER', name: '合成收款方', direction: '支出' },
  ];
  for (const sample of cases) {
    const h = harness(), result = fixture('B');
    result.preview = [{ ...result.preview[0], ...sample.row }];
    h.render(result);
    const examples = Object.fromEntries(h.rows().map(row => [row.field,
      plain([...row.html.matchAll(/<td\b[^>]*>([\s\S]*?)<\/td>/g)][2]?.[1] || '')]));
    assert.equal(examples.counter_name, sample.name);
    assert.equal(examples.counter_account, sample.account);
    assert.equal(examples.direction, sample.direction);
  }
});

test('unmapped fields have an explicit matrix state while source names and preview money remain lossless and escaped', () => {
  const h = harness(), result = fixture('A');
  const hostile = '<img src=x onerror="SYN"> & 合成列';
  result.filename = hostile;
  result.columns[0] = hostile; result.mapping.trade_no = hostile;
  delete result.mapping.currency;
  result.preview[0].currency = '<b>SYN-CURRENCY</b> & "C"';
  h.render(result);
  const row = h.rows().find(item => item.field === 'currency');
  assert.match(row?.text || '', /未映射|未识别|未选择/);
  assert.equal(h.mapping().mapping.trade_no, hostile, 'browser option values retain the original source column exactly');
  for (const id of ['impMeta', 'impMapping', 'impTbody']) assert.equal(h.element(id).innerHTML.includes('<img'), false, id);
  const table = h.element('impTbody').innerHTML;
  assert.match(table, /9,007,199,254,740,993\.07/);
  assert.match(table, /&lt;b&gt;SYN-CURRENCY&lt;\/b&gt; &amp; &quot;C&quot;/);
  assert.equal(table.includes('<b>SYN-CURRENCY</b>'), false);
});

test('advanced mapping expansion and supplementary account controls survive matrix rerendering', () => {
  const h = harness(); h.render(fixture('A'));
  const advanced = h.element('impMapping').innerHTML.match(/<details\b[^>]*\bid="impAdvanced"[\s\S]*?<\/details>/)?.[0] || '';
  assert.match(advanced, /<table\b/, 'secondary mappings remain in a native expandable matrix');
  assert.match(advanced, /<select\b/);
  for (const id of ['impMainSelWrap', 'impSelfAccts', 'impBad']) assert.equal(index.includes(`id="${id}"`), true, id);
  const previous = h.element('impAdvanced'); previous.open = true;
  h.element('impSelfAccts').value = 'SYN-SUPPLEMENT';
  h.render(fixture('A'));
  assert.notEqual(h.element('impAdvanced'), previous, 'the test models actual subtree replacement');
  assert.equal(previous.isConnected, false);
  h.render(fixture('B'));
  assert.equal(h.element('impAdvanced').open, true);
  assert.equal(h.element('impSelfAccts').value, 'SYN-SUPPLEMENT');
  assert.match(h.element('impMainSel').innerHTML, /SYN-SELF/);
  assert.match(h.element('impBad').innerHTML, /合成第3行/);
  assert.match(h.element('impBad').innerHTML, /合成时间缺失/);
});
