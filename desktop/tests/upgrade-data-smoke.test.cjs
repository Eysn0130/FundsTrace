'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const { fixtureCsv, facts, normalizeSnapshot, canvasSnapshot } = require('./upgrade-data-smoke.cjs');

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
