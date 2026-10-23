import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/entity-search.js', import.meta.url), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const caseId = '9007199254740993', account = '00090071992547409931234567890123456789';
const item = (overrides = {}) => ({ account, name: '合成同名主体', id_no: '00000020000101000X', matched_field: 'name', matched_text: '合成别名', ...overrides });
const receipt = (overrides = {}) => ({ case_id: caseId, source: 'flows', query: '合成', items: [item()], has_more: false, next_offset: null, ...overrides });
const request = (overrides = {}) => ({ caseId, source: 'flows', query: '合成', offset: 0, limit: 30, signal: new AbortController().signal, ...overrides });
function harness(fetch = async () => ({ ok: true, json: async () => receipt() })) {
  const root = {}, context = vm.createContext({ window: root, URLSearchParams, fetch });
  vm.runInContext(source, context, { filename: 'entity-search.js' });
  return { helper: root.FundsEntitySearch, root };
}

test('the real provider uses a case-bound GET with literal text and bounded cursor, preserving full string identities', async () => {
  const calls = [], q = ' 合成 & %_\\ ';
  const h = harness(async (...args) => { calls.push(args); return { ok: true, json: async () => receipt({ query: q.normalize('NFKC').trim() }) }; });
  const input = request({ query: q, offset: 60 });
  const result = plain(await h.helper.accounts(input));
  const url = new URL(calls[0][0], 'http://synthetic.local');
  assert.equal(url.pathname, '/api/account-suggestions'); assert.equal(url.searchParams.get('case_id'), caseId);
  assert.equal(url.searchParams.get('source'), 'flows'); assert.equal(url.searchParams.get('q'), q);
  assert.equal(url.searchParams.get('offset'), '60'); assert.equal(url.searchParams.get('page_size'), '30');
  assert.equal(calls[0][1].signal, input.signal); assert.deepEqual(Object.keys(calls[0][1]), ['signal']);
  assert.equal(result.items[0].key, `${caseId}:${account}`); assert.equal(result.items[0].value, account);
  assert.equal(result.items[0].account, account); assert.equal(result.items[0].idNo, '00000020000101000X');
  assert.equal(result.items[0].matchedField, 'label'); assert.equal(result.items[0].detail, '合成别名');
  assert.equal(result.hasMore, false);
});

test('the provider maps account and identity hits without inventing missing identity data', async () => {
  const rows = [item({ matched_field: 'account', matched_text: account, id_no: null }), item({ account: '00012', matched_field: 'id', matched_text: '00000020000101000X' })];
  const h = harness(async () => ({ ok: true, json: async () => receipt({ items: rows }) }));
  const result = plain(await h.helper.accounts(request()));
  assert.deepEqual(result.items.map(value => value.matchedField), ['account', 'idNo']);
  assert.equal(Object.hasOwn(result.items[0], 'idNo'), false); assert.equal(result.items[1].account, '00012');
});

for (const [name, overrides] of [
  ['another case', { case_id: 'foreign' }], ['another source', { source: 'identity' }], ['another query', { query: 'other' }],
  ['missing array', { items: null }], ['numeric account', { items: [item({ account: 123 })] }],
  ['numeric identity', { items: [item({ id_no: 123 })] }], ['null row', { items: [null] }],
  ['duplicate key', { items: [item(), item()] }], ['unknown match', { items: [item({ matched_field: 'unknown' })] }],
  ['non-string match', { items: [item({ matched_text: 123 })] }], ['non-boolean continuation', { has_more: 'yes' }],
  ['non-null final cursor', { next_offset: 30 }], ['empty continuation', { items: [], has_more: true, next_offset: 30 }],
  ['short continuation', { has_more: true, next_offset: 1 }],
]) test(`the provider rejects ${name} receipts instead of exposing candidates`, async () => {
  const h = harness(async () => ({ ok: true, json: async () => receipt(overrides) }));
  await assert.rejects(h.helper.accounts(request()));
});

test('numeric case IDs are rejected even when their decimal rendering equals the requested string', async () => {
  const h = harness(async () => ({ ok: true, json: async () => receipt({ case_id: 1 }) }));
  await assert.rejects(h.helper.accounts(request({ caseId: '1' })));
});

test('a full continuation page uses the exact next cursor; sparse and foreign cursors fail closed', async () => {
  const rows = [item(), item({ account: '00012' })];
  for (const next_offset of [3, 7]) {
    const h = harness(async () => ({ ok: true, json: async () => receipt({ items: rows, has_more: true, next_offset }) }));
    if (next_offset === 7) {
      const result = await h.helper.accounts(request({ offset: 5, limit: 2 })); assert.equal(result.nextOffset, 7);
    } else await assert.rejects(h.helper.accounts(request({ offset: 5, limit: 2 })));
  }
  const h = harness(async () => ({ ok: true, json: async () => receipt({ items: rows, has_more: true, next_offset: 100 }) }));
  await assert.rejects(h.helper.accounts(request({ offset: 5, limit: 2 })));
});

test('a read failure rejects and permits a fresh retry with the same exact query', async () => {
  let calls = 0;
  const h = harness(async () => ++calls === 1 ? { ok: false } : { ok: true, json: async () => receipt() });
  await assert.rejects(h.helper.accounts(request()), /候选读取/);
  assert.equal((await h.helper.accounts(request())).items[0].account, account); assert.equal(calls, 2);
});

test('local results rank exact before prefix before contains and page full numbers independently of homonyms', () => {
  const h = harness(), rows = [
    { key: 'a', label: '同名', value: '000123', account: '000123' },
    { key: 'b', label: '同名', value: '123', account: '123' },
    { key: 'c', label: '同名', value: '123000', account: '123000', idNo: 'DEMO-003' },
  ];
  const first = plain(h.helper.local(rows, { query: '１２３', offset: 0, limit: 2 }));
  assert.deepEqual(first.items.map(value => value.account), ['123', '123000']); assert.equal(first.hasMore, true);
  const second = plain(h.helper.local(rows, { query: '123', offset: first.nextOffset, limit: 2 }));
  assert.deepEqual(second.items.map(value => value.account), ['000123']); assert.equal(second.hasMore, false);
  assert.equal(h.helper.local(rows, { query: '同名', offset: 0, limit: 30 }).items.length, 3);
});

// Only the progressive-enhancement seam is simulated here. Core editing,
// cancellation, virtualization and late-response behavior use its own harness.
class Element {
  constructor(tag, doc) { this.tagName = tag; this.ownerDocument = doc; this.children = []; this.parentElement = null; this.attributes = {}; this.listeners = {}; this.dataset = {}; this.value = ''; this.disabled = this.readOnly = false; }
  append(...nodes) { for (const node of nodes) { node.remove(); this.children.push(node); node.parentElement = this; } }
  before(node) { const parent = this.parentElement; node.remove(); parent.children.splice(parent.children.indexOf(this), 0, node); node.parentElement = parent; }
  remove() { if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1); this.parentElement = null; }
  closest(selectors) { return selectors.split(',').some(selector => this.className === selector.trim().slice(1)) ? this : this.parentElement?.closest(selectors) || null; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  fire(type) { const event = { preventDefault() {} }; for (const fn of this.listeners[type] || []) fn(event); }
  focus() { this.ownerDocument.activeElement = this; }
}
function mountHarness(options = {}) {
  const h = harness(), doc = { createElement: tag => new Element(tag, doc) }, form = new Element('form', doc), input = new Element('input', doc);
  input.labels = [{ textContent: '账户' }];
  form.append(input); let captured, selected = null, opens = 0, closes = 0, invalidations = 0;
  const controller = { open() { opens++; }, close() { closes++; }, invalidate() { invalidations++; }, getSelected() { return selected; } };
  h.root.FundsEntityPicker = { create(value) { captured = value; return controller; } };
  const submits = [], picks = [];
  h.helper.mount(input, { caseId: () => caseId, scope: () => `${caseId}:1`, kind: 'account', onSelect: value => picks.push(value), onSubmit: (...args) => submits.push(args), ...options });
  return { ...h, form, input, submits, picks, captured: () => captured, opens: () => opens, closes: () => closes, invalidations: () => invalidations, select(value) { selected = value; input.value = value.value; captured.onSelect(value); } };
}

test('mount keeps native input identity; selection and typing do not execute, and the SVG submits the current string and exact binding', () => {
  const h = mountHarness(), input = h.input; input.value = '合'; input.fire('input');
  assert.equal(input.getAttribute('aria-label'), '账户');
  assert.equal(h.captured().input, input); assert.equal(h.submits.length, 0);
  const chosen = { key: caseId + ':' + account, value: account, account, label: '合成主体' }; h.select(chosen);
  assert.equal(h.submits.length, 0); assert.deepEqual(h.picks, [chosen]);
  const button = input.parentElement.children[1]; assert.equal(button.tagName, 'button');
  assert.equal(button.attributes['aria-label'], '查询'); assert.equal(button.children[0].src, '/static/ui-icons/search.svg');
  button.fire('click'); assert.deepEqual(h.submits, [[account, chosen]]); assert.equal(h.closes(), 1);
  input.disabled = true; button.fire('click'); input.disabled = false; input.readOnly = true; button.fire('click'); assert.equal(h.submits.length, 1);
});

test('fill-only mounts open candidates and action:false mounts preserve an existing search affordance', () => {
  const fill = mountHarness({ onSubmit: undefined }); fill.input.parentElement.children[1].fire('click');
  assert.equal(fill.opens(), 1); assert.equal(fill.submits.length, 0); assert.equal(fill.picks.length, 0);
  const existing = mountHarness({ action: false }); assert.equal(existing.input.parentElement, existing.form); assert.equal(existing.form.children.length, 1);
});

test('data invalidation changes every mounted scope and revokes candidates without submitting a query', () => {
  const h = mountHarness(), before = h.captured().scope();
  h.helper.invalidateAll();
  assert.notEqual(h.captured().scope(), before); assert.equal(h.invalidations(), 1); assert.equal(h.submits.length, 0); assert.equal(h.picks.length, 0);
});
