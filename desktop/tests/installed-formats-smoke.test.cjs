'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const vm = require('node:vm');
const { createHash } = require('node:crypto');
const test = require('node:test');
const helper = require('./installed-formats-smoke.cjs');
const { argsOf, within, canonicalPackage, journalCounts, moneyFacts, timeOf, effectiveFacts, statFacts, CHECKS, FILES, SIMPLE } = helper;
const sourceText = fs.readFileSync(path.join(__dirname, 'installed-formats-smoke.cjs'), 'utf8');
const digest = value => createHash('sha256').update(value).digest('hex');

test('installed formats CLI requires all four absolute independent values', () => {
  const root = path.resolve('synthetic');
  assert.deepEqual(argsOf(['--backend', root, '--fixtures', root, '--python', root, '--report', root]), { backend: root, fixtures: root, python: root, report: root });
  for (const args of [[], ['--backend', root], ['--backend', 'relative', '--fixtures', root, '--python', root, '--report', root],
    ['--backend', root, '--backend', root, '--python', root, '--report', root], ['--backend', root, '--fixture', root, '--python', root, '--report', root]]) assert.throws(() => argsOf(args));
});

test('owned runtime checks exclude parent and sibling escapes', () => {
  const root = path.resolve('owned');
  assert.equal(within(root, path.join(root, 'data')), true);
  for (const file of [root, path.dirname(root), `${root}-sibling`, path.join(root, '..', 'elsewhere')]) assert.equal(within(root, file), false);
});

test('public API and package time strings denote the same exact second', () => {
  assert.equal(timeOf('2026-01-01 10:00:00'), '2026-01-01T10:00:00');
  assert.equal(timeOf('2026-01-01T10:00:00'), '2026-01-01T10:00:00');
  assert.throws(() => timeOf('2026-01-01'));
  assert.throws(() => timeOf('2026-01-01 10:00:00.1'));
});

test('money oracle keeps exact decimal strings, direction, time and leading zeros', () => {
  const fields = ['trade_no', 'trade_time', 'payer_account', 'payee_account', 'amount', 'balance', 'currency', 'self_account'];
  const rows = SIMPLE.map(values => Object.fromEntries(fields.map((key, i) => [key, values[i]])));
  assert.equal(moneyFacts(rows).length, 3);
  assert.equal(rows[0].amount, '123.45'); assert.equal(rows[0].payer_account, '000SYN-A');
  for (const [key, value] of [['amount', 123.45], ['currency', 'USD'], ['payer_account', 'SYN-A'], ['trade_time', '2026-01-01T10:00:01']]) {
    const changed = structuredClone(rows); changed[0][key] = value; assert.notDeepEqual(moneyFacts(changed), moneyFacts(rows));
  }
});

function sample() {
  return { rawAmountTotals: [{ currency: 'CNY', amount: '1.23' }], tables: {
    batches: [{ id: '11', filename: 'ordinary', mode: 'A', rows_total: 1, rows_imported: 1, rows_skipped: 0, rows_dup: 0, imported_at: '2026-01-01T00:00:00' }],
    flows: [{ id: '91', batch_id: '11', trade_no: 'SYN', trade_time: '2026-01-01T10:00:00', payer_account: '000A', payee_account: '000B', amount: '1.23', balance: null, currency: 'CNY' }],
    import_sources: [{ source_id: 'a', filename: 'ordinary', sha256: 'a'.repeat(64), parser_version: 'funds-trace-table-v17', mapping_json: '{}', imported_at: '2026-01-01T00:00:00' }],
    import_members: [{ source_id: 'a', member_path: 'table.csv', sha256: 'a'.repeat(64) }],
    import_records: [{ source_id: 'a', record_no: 1, source_row: 2, member_path: 'table.csv', disposition: 'imported', reason_code: 'transaction_valid', details_json: '{}' }],
    flow_sources: [{ source_id: 'a', source_row: 1, flow_id: '91', disposition: 'imported' }],
    tracked_flows: [{ flow_id: '91', canvas_id: '10', tracked_at: '2026-01-01T00:00:00' }],
    canvases: [{ id: '10', name: 'SYN canvas', snapshot: '{}', updated_at: '2026-01-01T00:00:00' }],
  } };
}

test('package comparison remaps only restore IDs and preserves complete facts/journal', () => {
  const original = sample(), restored = structuredClone(original);
  restored.tables.flows[0].id = '200'; restored.tables.flows[0].batch_id = '100'; restored.tables.batches[0].id = '100';
  for (const table of ['import_sources', 'import_members', 'import_records', 'flow_sources']) restored.tables[table][0].source_id = 'restored';
  restored.tables.flow_sources[0].flow_id = restored.tables.tracked_flows[0].flow_id = '200';
  restored.tables.tracked_flows[0].canvas_id = restored.tables.canvases[0].id = '201';
  assert.deepEqual(canonicalPackage(original), canonicalPackage(restored));
  for (const [table, key, value] of [['flows', 'payer_account', 'A'], ['flows', 'amount', '9.99'], ['import_records', 'details_json', '{"changed":true}'], ['import_records', 'source_row', 3], ['import_sources', 'sha256', 'b'.repeat(64)]]) {
    const changed = structuredClone(restored); changed.tables[table][0][key] = value;
    assert.notDeepEqual(canonicalPackage(original), canonicalPackage(changed));
  }
  const defaultCanvas = sample(); defaultCanvas.tables.tracked_flows[0].canvas_id = '0';
  assert.equal(canonicalPackage(defaultCanvas).tracked[0].canvas_id, '0');
  const dangling = sample(); dangling.tables.flow_sources[0].flow_id = 'missing'; assert.throws(() => canonicalPackage(dangling));
});

test('immutable journal and effective coverage are distinct exact checks', () => {
  assert.deepEqual(journalCounts({ items: [{ disposition: 'cancelled', reason_code: 'matched_cancellation' }, { disposition: 'cancelled', reason_code: 'matched_cancellation' }, { disposition: 'unresolved', reason_code: 'negative_amount' }] }), { 'cancelled/matched_cancellation': 2, 'unresolved/negative_amount': 1 });
  for (const key of ['fourFormatsExact', 'cancellationChainExact', 'lateConflictExact', 'suffixlessPackageExact', 'sourceReportsAndTraces', 'publicRawPackageFacts', 'cleanProcessRestore', 'cleanup']) assert.ok(CHECKS.includes(key));
  assert.equal(new Set(CHECKS).size, CHECKS.length); assert.equal(FILES.length, 12); assert.ok(FILES.includes('ordinary'));
});

test('restore acceptance compares effective full facts and coverage with the producing process', () => {
  const row = sample().tables.flows[0];
  assert.deepEqual(effectiveFacts([{ ...row, trade_time: '2026-01-01 10:00:00' }]), effectiveFacts([row]));
  assert.notDeepEqual(effectiveFacts([row]), effectiveFacts([{ ...row, summary: 'SYN changed' }]));
  const initial = { flows: 2, accounts: 3, amount_totals: [{ currency: 'CNY', amount: '3.00' }], import_coverage: { unresolved_records: 0, cancelled_records: 6, excluded_flows: 3 } };
  assert.deepEqual(statFacts({ ...initial, case_id: '999' }), initial);
  for (const [key, value] of [['flows', 3], ['accounts', 4], ['amount_totals', [{ currency: 'CNY', amount: '40.00' }]], ['import_coverage', { unresolved_records: 0, cancelled_records: 2, excluded_flows: 1 }]]) assert.notDeepEqual(statFacts({ ...initial, [key]: value }), initial);
});


async function executeActualCliTail(succeeds) {
  const marker = 'if (require.main === module)';
  const offset = sourceText.lastIndexOf(marker);
  assert.ok(offset > 0, 'Actual CLI tail must remain present');
  const targetModule = {}, stdout = [], stderr = [];
  const context = { require: { main: targetModule }, module: targetModule,
    argsOf: args => { assert.deepEqual(Array.from(args), ['SYN-CLI']); return { synthetic: true }; },
    run: async options => { assert.equal(options.synthetic, true); if (!succeeds) throw new Error('SYN acceptance rejected'); return { status: 'passed' }; },
    process: { argv: ['node', 'installed-formats-smoke.cjs', 'SYN-CLI'],
      stdout: { write: text => stdout.push(text) }, stderr: { write: text => stderr.push(text) }, exitCode: 0 } };
  await vm.runInNewContext(sourceText.slice(offset), context, { timeout: 1000 });
  return { stdout, stderr, exitCode: context.process.exitCode };
}

test('actual CLI success tail satisfies lifecycle status contract with one safe JSON line', async () => {
  const result = await executeActualCliTail(true);
  assert.equal(result.exitCode, 0); assert.deepEqual(result.stderr, []); assert.equal(result.stdout.length, 1);
  assert.equal(result.stdout[0].split('\n').length, 2);
  assert.deepEqual(JSON.parse(result.stdout[0]), { status: 'passed', installedFormats: true });
});

test('actual CLI failure tail emits no passed summary', async () => {
  const result = await executeActualCliTail(false);
  assert.equal(result.exitCode, 1); assert.deepEqual(result.stdout, []);
  assert.equal(result.stderr.length, 1); assert.doesNotMatch(result.stderr[0], /status.*passed|installedFormats.*true/);
});

function fourSourcesFixture() {
  const files = ['formats.csv', 'formats.xlsx', 'formats.xls', 'formats.zip'];
  const inputs = new Map(files.map(file => [file, Buffer.from(`SYN fixed input ${file}`)]));
  const profiles = ['funds-trace-table-v17', 'funds-trace-table-v17', 'funds-trace-table-v18', 'funds-trace-table-archive-v4'];
  const rows = [
    { id: '501', trade_no: 'SYN-F1', trade_time: '2026-01-01T10:00:00', payer_account: '000SYN-A', payee_account: '000SYN-B', amount: '123.45', currency: 'CNY', self_account: '000SYN-A', balance: null },
    { id: '502', trade_no: 'SYN-F2', trade_time: '2026-01-01T11:00:00', payer_account: '000SYN-B', payee_account: '000SYN-A', amount: '2.03', currency: 'CNY', self_account: '000SYN-A', balance: null },
    { id: '503', trade_no: 'SYN-F3', trade_time: '2026-01-01T12:00:00', payer_account: '000SYN-A', payee_account: '000SYN-C', amount: '4.56', currency: 'USD', self_account: '000SYN-A', balance: null },
  ];
  const tables = { flows: rows, import_sources: [], import_members: [], import_records: [], flow_sources: [] };
  const fourFormatImports = [];
  files.forEach((filename, index) => {
    const source_id = String(index + 1).repeat(32), member_path = filename === 'formats.zip' ? 'bank/container.zip/nested/renamed-transactions.csv'
      : filename === 'formats.csv' ? 'table.csv' : 'worksheets/0001';
    const source = { source_id, filename, sha256: digest(inputs.get(filename)), size_bytes: inputs.get(filename).length,
      parser_version: profiles[index], row_kind: 'table_record', mode: 'B', mapping_json: '{}', sheet: filename.endsWith('xls') || filename.endsWith('xlsx') ? 'workbook' : '' };
    tables.import_sources.push(source);
    fourFormatImports.push({ sourceId: source_id, filename, profile: profiles[index] });
    tables.import_members.push({ source_id, member_path, role: 'table', sha256: filename === 'formats.zip' ? digest(inputs.get('formats.csv')) : source.sha256,
      size_bytes: filename === 'formats.zip' ? inputs.get('formats.csv').length : source.size_bytes, encoding: 'utf-8-sig', row_count: 3 });
    for (let number = 1; number <= 3; number++) {
      const disposition = index === 0 ? 'imported' : 'duplicate';
      tables.import_records.push({ source_id, record_no: number, source_row: number + 1, member_path,
        record_key: `SYN-record-${index}-${number}`, disposition, reason_code: 'transaction_valid', details_json: JSON.stringify({ synthetic: `${index}-${number}` }) });
      tables.flow_sources.push({ source_id, source_row: number, flow_id: String(500 + number), disposition });
    }
  });
  return { pkg: { tables }, inputs, fourFormatImports };
}

function assertActualFirstSourceSection(fixture) {
  const anchor = "const simplePkg = await save(simple, 'four-formats', SIMPLE,";
  const begin = sourceText.indexOf('\n', sourceText.indexOf(anchor)) + 1;
  const end = sourceText.indexOf('    report.checks.fourFormatsExact = true;', begin);
  assert.ok(begin > 0 && end > begin, 'Actual first-export source checks must remain present');
  const names = [...Object.keys(helper), 'assert', 'digest', 'simplePkg', 'inputs', 'fourFormatImports'];
  const values = [...Object.values(helper), assert, digest, fixture.pkg, fixture.inputs, fixture.fourFormatImports];
  return new Function(...names, sourceText.slice(begin, end))(...values);
}

function removeSource(fixture, sourceId) {
  for (const table of ['import_sources', 'import_members', 'import_records', 'flow_sources']) fixture.pkg.tables[table] = fixture.pkg.tables[table].filter(row => row.source_id !== sourceId);
}

test('actual first-export oracle accepts exactly the four committed sources and all original row links', () => {
  const fixture = fourSourcesFixture(); assert.doesNotThrow(() => assertActualFirstSourceSection(fixture));
});

for (const defect of ['missing-xls', 'duplicate-source', 'extra-source', 'wrong-commit-id', 'wrong-filename', 'wrong-profile', 'wrong-bytes', 'wrong-sha', 'record-duplicate', 'record-number', 'record-disposition', 'link-missing', 'link-duplicate', 'link-disposition', 'physical-row']) {
  test(`actual first-export oracle rejects ${defect}`, () => {
    const fixture = fourSourcesFixture(), tables = fixture.pkg.tables, xlsId = fixture.fourFormatImports[2].sourceId;
    if (defect === 'missing-xls') removeSource(fixture, xlsId);
    if (defect === 'duplicate-source') { removeSource(fixture, xlsId); tables.import_sources.push(structuredClone(tables.import_sources[0])); }
    if (defect === 'extra-source') tables.import_sources.push({ ...tables.import_sources[0], source_id: 'extra' });
    if (defect === 'wrong-commit-id') {
      for (const table of ['import_sources', 'import_members', 'import_records', 'flow_sources']) {
        for (const row of tables[table]) if (row.source_id === xlsId) row.source_id = 'not-committed';
      }
    }
    if (defect === 'wrong-filename') tables.import_sources[2].filename = 'formats.csv';
    if (defect === 'wrong-profile') tables.import_sources[2].parser_version = 'funds-trace-table-v17';
    if (defect === 'wrong-bytes') tables.import_sources[2].size_bytes++;
    if (defect === 'wrong-sha') tables.import_sources[2].sha256 = 'f'.repeat(64);
    if (defect === 'record-duplicate') tables.import_records[8] = structuredClone(tables.import_records[6]);
    if (defect === 'record-number') tables.import_records[8].record_no = 4;
    if (defect === 'record-disposition') tables.import_records[8].disposition = 'imported';
    if (defect === 'link-missing') tables.flow_sources.splice(8, 1);
    if (defect === 'link-duplicate') tables.flow_sources[8] = structuredClone(tables.flow_sources[6]);
    if (defect === 'link-disposition') tables.flow_sources[8].disposition = 'imported';
    if (defect === 'physical-row') tables.import_records[8].source_row = 5;
    assert.throws(() => assertActualFirstSourceSection(fixture));
  });
}

function publicProvenanceResponses(pkg) {
  const responses = new Map();
  for (const source of pkg.tables.import_sources) {
    const members = pkg.tables.import_members.filter(row => row.source_id === source.source_id).map(({ source_id, ...row }) => row);
    const items = pkg.tables.import_records.filter(row => row.source_id === source.source_id).map(row => ({
      record_no: row.record_no, member_path: row.member_path, source_row: row.source_row, record_key: row.record_key,
      disposition: row.disposition, reason_code: row.reason_code, details: JSON.parse(row.details_json),
      flow_id: pkg.tables.flow_sources.find(link => link.source_id === source.source_id && link.source_row === row.record_no).flow_id,
    }));
    responses.set(`/api/v1/cases/99/sources/${source.source_id}/report`, { filename: source.filename, sha256: source.sha256, profile: source.parser_version, members, items });
  }
  for (const flow of pkg.tables.flows) {
    const items = pkg.tables.flow_sources.filter(link => link.flow_id === flow.id).map(link => {
      const source = pkg.tables.import_sources.find(row => row.source_id === link.source_id);
      const record = pkg.tables.import_records.find(row => row.source_id === link.source_id && row.record_no === link.source_row);
      return { source_id: link.source_id, source_row: link.source_row, disposition: link.disposition,
        ...Object.fromEntries(['filename', 'sha256', 'size_bytes', 'mode', 'sheet', 'row_kind', 'parser_version'].map(key => [key, source[key]])),
        member_path: record.member_path, member_row: record.source_row, record_details: JSON.parse(record.details_json) };
    });
    responses.set(`/api/v1/cases/99/flows/${flow.id}/sources?page_size=200`, { total: items.length, items });
  }
  return responses;
}

async function assertActualProvenance(pkg, responses) {
  const start = sourceText.indexOf('  async function provenance('), end = sourceText.indexOf('\n  const exported = [];', start);
  assert.ok(start >= 0 && end > start, 'Actual public provenance function must remain present');
  const fakeJson = async (_ready, endpoint) => { assert.ok(responses.has(endpoint), endpoint); return structuredClone(responses.get(endpoint)); };
  const names = [...Object.keys(helper), 'json', 'assert', 'sorted', 'sourceKey', 'literalFacts'];
  const values = [...Object.values(helper), fakeJson, assert,
    rows => rows.sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))),
    source => JSON.stringify([source.filename, source.sha256, source.parser_version || source.profile]),
    rows => rows.map(row => Object.fromEntries(['trade_no', 'trade_time', 'payer_name', 'payer_account', 'payer_bank', 'payee_name', 'payee_account', 'payee_bank', 'amount', 'balance', 'currency', 'summary', 'channel', 'self_account', 'self_name'].map(key => [key, row[key] ?? (key === 'balance' ? null : '')])))];
  const actualFunction = new Function(...names, `return (${sourceText.slice(start, end).trim()});`)(...values);
  return actualFunction({}, '99', pkg);
}

test('actual provenance accepts complete public sets independent of order', async () => {
  const { pkg } = fourSourcesFixture(), responses = publicProvenanceResponses(pkg);
  for (const value of responses.values()) value.items.reverse();
  await assert.doesNotReject(assertActualProvenance(pkg, responses));
});

for (const defect of ['trace-duplicate-replaces-last', 'report-record-duplicate-replaces-last', 'trace-missing', 'trace-wrong-row', 'report-wrong-flow']) {
  test(`actual public provenance rejects ${defect}`, async () => {
    const { pkg } = fourSourcesFixture(), responses = publicProvenanceResponses(pkg);
    const trace = responses.get('/api/v1/cases/99/flows/501/sources?page_size=200');
    const report = responses.get(`/api/v1/cases/99/sources/${pkg.tables.import_sources[0].source_id}/report`);
    if (defect === 'trace-duplicate-replaces-last') trace.items[3] = structuredClone(trace.items[0]);
    if (defect === 'report-record-duplicate-replaces-last') report.items[2] = structuredClone(report.items[0]);
    if (defect === 'trace-missing') { trace.items.pop(); trace.total--; }
    if (defect === 'trace-wrong-row') trace.items[3].source_row = 2;
    if (defect === 'report-wrong-flow') report.items[2].flow_id = '501';
    await assert.rejects(assertActualProvenance(pkg, responses));
  });
}

test('canonical package preserves complete batch metadata and allows only ID renumbering', () => {
  const original = sample(), restored = structuredClone(original);
  restored.tables.batches[0].id = restored.tables.flows[0].batch_id = '999';
  const { id, ...expectedBatch } = original.tables.batches[0];
  assert.deepEqual(canonicalPackage(original).batches, [expectedBatch]);
  assert.deepEqual(canonicalPackage(original), canonicalPackage(restored));
});

for (const defect of ['batch-lost-null-substitution', 'batch-field-changed', 'batch-association-swapped', 'batch-null-substitution', 'batch-dangling']) {
  test(`canonical package rejects or distinguishes ${defect}`, () => {
    const original = sample(), changed = structuredClone(original);
    if (defect === 'batch-lost-null-substitution') { changed.tables.batches = []; changed.tables.flows[0].batch_id = null; }
    if (defect === 'batch-field-changed') changed.tables.batches[0].rows_dup = 1;
    if (defect === 'batch-association-swapped') {
      for (const value of [original, changed]) value.tables.batches.push({ ...value.tables.batches[0], id: '22', filename: 'SYN other batch' });
      changed.tables.flows[0].batch_id = '22';
    }
    if (defect === 'batch-null-substitution') changed.tables.flows[0].batch_id = null;
    if (defect === 'batch-dangling') { changed.tables.flows[0].batch_id = 'missing'; assert.throws(() => canonicalPackage(changed)); return; }
    assert.notDeepEqual(canonicalPackage(original), canonicalPackage(changed));
  });
}

test('canonical batch identity preserves identical-content batch multiplicity and a real null association', () => {
  const original = sample(), duplicated = structuredClone(original);
  duplicated.tables.batches.push({ ...duplicated.tables.batches[0], id: '22' });
  assert.notDeepEqual(canonicalPackage(original), canonicalPackage(duplicated));
  assert.equal(canonicalPackage(duplicated).batches.length, 2);
  const nullLinked = sample(); nullLinked.tables.flows[0].batch_id = null;
  const nullRestored = structuredClone(nullLinked); nullRestored.tables.batches[0].id = '999';
  assert.deepEqual(canonicalPackage(nullLinked), canonicalPackage(nullRestored));
  assert.notDeepEqual(canonicalPackage(nullLinked), canonicalPackage(original));
});
