'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { fixtureCsv, facts, normalizeSnapshot, canvasSnapshot, assertOriginalSnapshot } = require('./upgrade-data-smoke.cjs');

test('old-version fixture has a chronological CNY cycle and a separate exact USD path', () => {
  const [header, ...rows] = fixtureCsv().toString('utf8').trimEnd().split('\r\n').map(line => line.split(','));
  assert.equal(header[0], '\uFEFF交易流水号');
  assert.deepEqual(rows.map(row => [row[3], row[5], row[6], row[8]]), [
    ['SYN-UP-A', 'SYN-UP-B', '9999999999999999.99', 'CNY'],
    ['SYN-UP-B', 'SYN-UP-C', '0.01', 'CNY'],
    ['SYN-UP-C', 'SYN-UP-A', '0.02', 'CNY'],
    ['SYN-UP-A', 'SYN-UP-B', '20.10', 'USD'],
    ['SYN-UP-B', 'SYN-UP-C', '30.20', 'USD'],
  ]);
  assert.equal(rows[0][7], '-0.01'); assert.equal(rows[1][7], '');
  assert.deepEqual(rows.map(row => row[1]), ['2026-01-01 10:00:00', '2026-01-01 10:01:00',
    '2026-01-01 10:02:00', '2026-01-01 10:03:00', '2026-01-01 10:04:00']);
  assert.equal(fixtureCsv(true).toString('utf8').trim().split('\r\n').length, 2);
});

test('package comparison remaps only flow identities while preserving saved geometry and money', () => {
  const rows = [{ id: '9007199254740993', trade_no: 'SYN-UP-1', currency: 'CNY',
    payer_account: 'SYN-UP-A', payee_account: 'SYN-UP-B', amount: '9999999999999999.99' }];
  const snapshot = canvasSnapshot(rows), before = structuredClone(snapshot);
  const normalized = normalizeSnapshot(snapshot, rows);
  assert.deepEqual(normalized.edges[0].data.ids, ['SYN-UP-1']);
  assert.deepEqual(normalized.edges[0].controlPoints, [{ x: 240, y: 130 }]);
  assert.equal(normalized.edges[0].data.total, '9999999999999999.99');
  assert.equal(normalized.edges[0].labelRatio, 0.35); assert.equal(normalized.display.labelMode, 'all');
  assert.deepEqual(snapshot, before);
  assert.throws(() => normalizeSnapshot(snapshot, [{ ...rows[0], id: '9007199254740992' }]));
  const replacement = structuredClone(snapshot); replacement.edges[0].data.ids = ['17'];
  assert.deepEqual(normalizeSnapshot(replacement, [{ ...rows[0], id: '17' }]), normalized);
});

test('original fact comparison detects exact amount changes and package comparison ignores allocated IDs', () => {
  const original = { id: '9007199254740993', case_id: '2', batch_id: '3', trade_no: 'SYN-UP-1',
    amount: '9999999999999999.99', balance: null, currency: 'CNY' };
  const imported = { ...original, id: '11', case_id: '12', batch_id: '13' };
  assert.notDeepEqual(facts([original]), facts([imported]));
  assert.deepEqual(facts([original], false), facts([imported], false));
  assert.notDeepEqual(facts([original], false), facts([{ ...imported, amount: '9999999999999999.98' }], false));
});

function emptyCoverage() {
  return { scope: 'source_reported_flows', unresolved_records: 0, non_monetary_records: 0,
    cancelled_records: 0, excluded_flows: 0, financial_interpretation_complete: true };
}
function originalSnapshot() {
  const row = { id: '9007199254740993', case_id: '2', batch_id: '3', trade_no: 'SYN-UP-1',
    trade_time: '2026-01-01 10:00:00', payer_account: 'SYN-UP-A', payee_account: 'SYN-UP-B',
    amount: '9999999999999999.99', balance: '-0.01', currency: 'CNY' };
  return { caseId: '2', rows: [row], stats: { case_id: '2', flows: 1, accounts: 2, batches: 1,
    amount_total: '9999999999999999.99', currency: 'CNY',
    amount_totals: [{ currency: 'CNY', amount: '9999999999999999.99' }] },
    canvases: [{ id: '7', name: 'SYN saved canvas', snapshot: canvasSnapshot([row]), trackedIds: [row.id] }],
    defaultTrackedIds: [row.id], mains: [{ account: 'SYN-UP-A', name: 'Synthetic main', source: 'manual' }],
    identities: [{ account: 'SYN-UP-A', id_no: '000000000000000000' }] };
}
function upgradedSnapshot() {
  const value = originalSnapshot(); value.stats.import_coverage = emptyCoverage(); return value;
}

test('old same-version reopen and current-seeded snapshots retain strict complete comparison', () => {
  const old = originalSnapshot(), current = upgradedSnapshot();
  assertOriginalSnapshot(structuredClone(old), old);
  assertOriginalSnapshot(structuredClone(current), current);
  assertOriginalSnapshot(structuredClone(current), current, true);
  assert.throws(() => assertOriginalSnapshot(current, old));
  assert.throws(() => assertOriginalSnapshot(current, old, false));
  assert.throws(() => assertOriginalSnapshot(current, old, 'true'));
});

test('explicit upgrade accepts only the exact new coverage and never modifies either input', () => {
  const old = originalSnapshot(), current = upgradedSnapshot();
  const oldBefore = structuredClone(old), currentBefore = structuredClone(current);
  Object.freeze(old.stats); Object.freeze(old); Object.freeze(current.stats); Object.freeze(current);
  assertOriginalSnapshot(current, old, true);
  assert.deepEqual(old, oldBefore); assert.deepEqual(current, currentBefore);
});

for (const [name, change] of [
  ['missing', value => { delete value.stats.import_coverage; }],
  ['null', value => { value.stats.import_coverage = null; }],
  ['undefined', value => { value.stats.import_coverage = undefined; }],
  ['scope', value => { value.stats.import_coverage.scope = 'all_flows'; }],
  ...['unresolved_records', 'non_monetary_records', 'cancelled_records', 'excluded_flows']
    .map(field => [field, value => { value.stats.import_coverage[field] = 1; }]),
  ['count type', value => { value.stats.import_coverage.unresolved_records = '0'; }],
  ['incomplete', value => { value.stats.import_coverage.financial_interpretation_complete = false; }],
  ['extra field', value => { value.stats.import_coverage.extra = 0; }],
  ['inherited', value => {
    delete value.stats.import_coverage; Object.setPrototypeOf(value.stats, { import_coverage: emptyCoverage() });
  }],
]) test(`upgrade coverage addition rejects ${name}`, () => {
  const old = originalSnapshot(), current = upgradedSnapshot(); change(current);
  assert.throws(() => assertOriginalSnapshot(current, old, true));
  assert.equal(Object.hasOwn(old.stats, 'import_coverage'), false);
});

for (const field of Object.keys(emptyCoverage())) test(`upgrade coverage requires ${field}`, () => {
  const current = upgradedSnapshot(); delete current.stats.import_coverage[field];
  assert.throws(() => assertOriginalSnapshot(current, originalSnapshot(), true));
});

for (const [name, change] of [
  ['case identity', value => { value.caseId = '3'; }],
  ['amount total', value => { value.stats.amount_total = '9999999999999999.98'; }],
  ['currency total', value => { value.stats.amount_totals[0].amount = '9999999999999999.98'; }],
  ['flow count', value => { value.stats.flows = 2; }],
  ['account count', value => { value.stats.accounts = 3; }],
  ['batch count', value => { value.stats.batches = 2; }],
  ['flow amount', value => { value.rows[0].amount = '9999999999999999.98'; }],
  ['balance', value => { value.rows[0].balance = '0.01'; }],
  ['currency', value => { value.rows[0].currency = 'USD'; }],
  ['time', value => { value.rows[0].trade_time = '2026-01-01 10:00:01'; }],
  ['payer account', value => { value.rows[0].payer_account = 'SYN-UP-C'; }],
  ['canvas geometry', value => { value.canvases[0].snapshot.nodes[0].x += 1; }],
  ['canvas tracking', value => { value.canvases[0].trackedIds = []; }],
  ['default tracking', value => { value.defaultTrackedIds = []; }],
  ['main account', value => { value.mains[0].account = 'SYN-UP-C'; }],
  ['identity', value => { value.identities[0].id_no = '111111111111111111'; }],
  ['deleted old field', value => { delete value.identities; }],
  ['other added field', value => { value.stats.extra = 0; }],
]) test(`coverage compatibility does not hide changed ${name}`, () => {
  const old = originalSnapshot(), current = upgradedSnapshot(); change(current);
  const oldBefore = structuredClone(old), currentBefore = structuredClone(current);
  assert.throws(() => assertOriginalSnapshot(current, old, true));
  assert.deepEqual(old, oldBefore); assert.deepEqual(current, currentBefore);
});

test('existing coverage cannot be deleted, replaced or normalized by the upgrade comparison', () => {
  const current = upgradedSnapshot();
  for (const change of [value => { delete value.stats.import_coverage; },
    value => { value.stats.import_coverage = null; },
    value => { value.stats.import_coverage.excluded_flows = 1; }]) {
    const changed = structuredClone(current); change(changed);
    assert.throws(() => assertOriginalSnapshot(changed, current, true));
  }
  const existing = upgradedSnapshot(); existing.stats.import_coverage.unresolved_records = 1;
  existing.stats.import_coverage.financial_interpretation_complete = false;
  assertOriginalSnapshot(structuredClone(existing), existing, true);
  assert.throws(() => assertOriginalSnapshot(current, existing, true));
});

test('coverage compatibility is opt-in only at the three upgraded verification calls', () => {
  const source = fs.readFileSync(path.join(__dirname, 'upgrade-data-smoke.cjs'), 'utf8').replace(/\r\n/g, '\n');
  assert.match(source, /async function verifyOriginal\(ready, stored, dataDir, allowCoverageAddition = false\)/);
  const [seed, verify] = source.split("if (args.mode === 'seed') {")[1].split('\n    } else {\n');
  assert.equal((seed.match(/verifyOriginal\(ready, stored, dataDir\)/g) || []).length, 1);
  assert.doesNotMatch(seed, /verifyOriginal\(ready, stored, dataDir, true\)/);
  assert.equal((verify.match(/verifyOriginal\(ready, stored, dataDir, true\)/g) || []).length, 3);
  assert.doesNotMatch(verify, /verifyOriginal\(ready, stored, dataDir\)/);
});
