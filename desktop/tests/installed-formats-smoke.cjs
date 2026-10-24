'use strict';
// CI-only public API acceptance against the installed frozen backend. Never imports app.*.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { createHash } = require('node:crypto');
const fs = require('node:fs/promises');
const path = require('node:path');
const { launchConfig, HEADER } = require('../host-policy.cjs');
const { startBackend } = require('../backend.cjs');

const FIELDS = ['trade_no', 'trade_time', 'payer_name', 'payer_account', 'payer_bank', 'payee_name',
  'payee_account', 'payee_bank', 'amount', 'balance', 'currency', 'summary', 'channel', 'self_account', 'self_name'];
const CHECKS = ['fourFormatsExact', 'deterministicControls', 'sourceReportsAndTraces', 'publicRawPackageFacts',
  'cancellationChainExact', 'lateConflictExact', 'suffixlessPackageExact', 'cleanProcessRestore',
  'reimportAddsZero', 'desktopAuthentication', 'activationFree', 'ownedRuntime', 'eofShutdown', 'cleanup'];
const FILES = ['chain-conflict.csv', 'chain-conflict.zip', 'chain.csv', 'chain.zip', 'formats.csv', 'formats.xls',
  'formats.xlsx', 'formats.zip', 'ordinary', 'regions.csv', 'regions.xlsx', 'typed-controls.xls'];
const digest = value => createHash('sha256').update(value).digest('hex');
const sorted = values => values.sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
const post = value => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function argsOf(args) {
  const result = {};
  assert.equal(args.length, 8);
  while (args.length) {
    const key = args.shift(), value = args.shift();
    assert.ok(['--backend', '--fixtures', '--python', '--report'].includes(key) && !result[key]);
    assert.ok(value && path.isAbsolute(value)); result[key] = path.resolve(value);
  }
  return { backend: result['--backend'], fixtures: result['--fixtures'], python: result['--python'], report: result['--report'] };
}
function within(root, file) {
  const relative = path.relative(root, file);
  return relative !== '' && relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}
function literalFacts(rows) {
  return sorted(rows.map(row => Object.fromEntries(FIELDS.map(key => [key, row[key] ?? (key === 'balance' ? null : '')]))));
}
function timeOf(value) {
  assert.equal(typeof value, 'string');
  assert.match(value, /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$/);
  return value.replace(' ', 'T');
}
function moneyFacts(rows) {
  return sorted(rows.map(row => [row.trade_no, timeOf(row.trade_time), row.payer_account, row.payee_account,
    row.amount, row.balance, row.currency, row.self_account]));
}
const SIMPLE = [
  ['SYN-F1', '2026-01-01T10:00:00', '000SYN-A', '000SYN-B', '123.45', null, 'CNY', '000SYN-A'],
  ['SYN-F2', '2026-01-01T11:00:00', '000SYN-B', '000SYN-A', '2.03', null, 'CNY', '000SYN-A'],
  ['SYN-F3', '2026-01-01T12:00:00', '000SYN-A', '000SYN-C', '4.56', null, 'USD', '000SYN-A'],
];
const CHAIN = [
  ['SYN-W', '2026-01-01T10:00:00', 'SYN-A', 'SYN-OTHER', '1.00', '100.00', 'CNY', 'SYN-A'],
  ['SYN-P', '2026-01-01T10:01:00', 'SYN-A', 'SYN-OTHER', '10.00', '90.00', 'CNY', 'SYN-A'],
  ['SYN-Q', '2026-01-01T10:03:00', 'SYN-A', 'SYN-OTHER', '20.00', '80.00', 'CNY', 'SYN-A'],
  ['SYN-XW', '2026-01-01T11:00:00', 'SYN-X', 'SYN-OTHER', '2.00', '200.00', 'CNY', 'SYN-X'],
  ['SYN-XP', '2026-01-01T11:01:00', 'SYN-X', 'SYN-OTHER', '7.00', '193.00', 'CNY', 'SYN-X'],
];
function expectedFacts(rows, chain = false) {
  return rows.map(values => {
    const row = Object.fromEntries(FIELDS.map(key => [key, key === 'balance' ? null : '']));
    ['trade_no', 'trade_time', 'payer_account', 'payee_account', 'amount', 'balance', 'currency', 'self_account'].forEach((key, index) => { row[key] = values[index]; });
    if (chain) { row.payer_name = row.self_name = 'Synthetic owner'; row.summary = 'Synthetic summary'; }
    return row;
  });
}
function assertMoney(actual, expected) { assert.deepEqual(moneyFacts(actual), sorted(structuredClone(expected))); }
function effectiveFacts(rows) {
  return literalFacts(rows.map(row => ({ ...row, trade_time: timeOf(row.trade_time) })));
}
function statFacts(value) {
  return Object.fromEntries(['flows', 'accounts', 'amount_totals', 'import_coverage'].map(key => [key, value[key]]));
}
function sourceKey(source) { return JSON.stringify([source.filename, source.sha256, source.parser_version || source.profile]); }
function assertFourFormatSources(pkg, committed, inputs) {
  const filenames = ['formats.csv', 'formats.xlsx', 'formats.xls', 'formats.zip'];
  const profiles = ['funds-trace-table-v17', 'funds-trace-table-v17', 'funds-trace-table-v18', 'funds-trace-table-archive-v4'];
  const tables = pkg.tables;
  assert.equal(committed.length, 4);
  assert.equal(new Set(committed.map(item => item.sourceId)).size, 4);
  assert.equal(tables.import_sources.length, 4);
  assert.equal(new Set(tables.import_sources.map(source => source.source_id)).size, 4);
  assert.deepEqual(tables.import_sources.map(source => source.source_id).sort(), committed.map(item => item.sourceId).sort());
  assert.equal(tables.import_records.length, 12);
  assert.equal(tables.flow_sources.length, 12);
  for (const [index, filename] of filenames.entries()) {
    const entry = committed[index];
    assert.equal(entry.filename, filename); assert.equal(entry.profile, profiles[index]);
    const source = tables.import_sources.find(row => row.source_id === entry.sourceId);
    assert.ok(source); assert.equal(source.filename, filename); assert.equal(source.parser_version, profiles[index]);
    assert.equal(source.sha256, digest(inputs.get(filename))); assert.equal(source.size_bytes, inputs.get(filename).length);
    const records = tables.import_records.filter(row => row.source_id === entry.sourceId);
    const links = tables.flow_sources.filter(row => row.source_id === entry.sourceId);
    assert.equal(records.length, 3); assert.equal(links.length, 3);
    assert.deepEqual(records.map(row => row.record_no).sort((a, b) => a - b), [1, 2, 3]);
    assert.deepEqual(links.map(row => row.source_row).sort((a, b) => a - b), [1, 2, 3]);
    for (const number of [1, 2, 3]) {
      const record = records.find(row => row.record_no === number), link = links.find(row => row.source_row === number);
      const flow = tables.flows.find(row => row.trade_no === `SYN-F${number}`);
      assert.ok(flow); assert.equal(link.flow_id, flow.id);
      assert.equal(record.source_row, number + 1); assert.equal(record.reason_code, 'transaction_valid');
      assert.equal(record.disposition, index === 0 ? 'imported' : 'duplicate');
      assert.equal(link.disposition, index === 0 ? 'imported' : 'duplicate');
    }
    if (filename === 'formats.zip') {
      assert.ok(records.every(row => row.member_path === 'bank/container.zip/nested/renamed-transactions.csv'));
      const member = tables.import_members.find(row => row.source_id === entry.sourceId);
      assert.ok(member); assert.equal(member.sha256, digest(inputs.get('formats.csv')));
      assert.equal(member.size_bytes, inputs.get('formats.csv').length);
    }
  }
}
function canonicalPackage(value) {
  const tables = value.tables;
  const batches = tables.batches.map(({ id, ...batch }) => batch);
  const batchIds = new Map(tables.batches.map(({ id, ...batch }) => [id, JSON.stringify(batch)]));
  assert.equal(batchIds.size, tables.batches.length);
  const raw = sorted(tables.flows.map(row => {
    if (row.batch_id !== null) assert.ok(batchIds.has(row.batch_id), 'Dangling public flow batch');
    return { ...literalFacts([row])[0], batch_id: row.batch_id === null ? null : batchIds.get(row.batch_id) };
  }));
  const flows = new Map(tables.flows.map(row => {
    const fact = { ...literalFacts([row])[0], batch_id: row.batch_id === null ? null : batchIds.get(row.batch_id) };
    return [row.id, JSON.stringify(fact)];
  }));
  const sources = new Map(tables.import_sources.map(row => [row.source_id, sourceKey(row)]));
  const canvases = new Map(tables.canvases.map(row => [row.id, row.name]));
  function normalize(table) {
    return sorted(tables[table].map(original => {
      const row = { ...original };
      delete row.id;
      if ('source_id' in row) { assert.ok(sources.has(row.source_id)); row.source_id = sources.get(row.source_id); }
      if ('flow_id' in row) { assert.ok(flows.has(row.flow_id)); row.flow_id = flows.get(row.flow_id); }
      if ('canvas_id' in row && row.canvas_id !== null && row.canvas_id !== '0') { assert.ok(canvases.has(row.canvas_id)); row.canvas_id = canvases.get(row.canvas_id); }
      return row;
    }));
  }
  return { raw, batches: sorted(batches), rawAmountTotals: value.rawAmountTotals,
    sources: normalize('import_sources'), members: normalize('import_members'), records: normalize('import_records'),
    links: normalize('flow_sources'), tracked: normalize('tracked_flows'), canvases: normalize('canvases') };
}
function journalCounts(report) {
  const counts = {};
  for (const item of report.items) {
    const key = `${item.disposition}/${item.reason_code}`; counts[key] = (counts[key] || 0) + 1;
  }
  return counts;
}
async function command(command, args, env) {
  const child = spawn(command, args, { env, windowsHide: true, shell: false, stdio: ['ignore', 'pipe', 'pipe'] });
  let output = '', error = '';
  child.stdout.on('data', chunk => { output += chunk; }); child.stderr.on('data', chunk => { error += chunk; });
  let timer;
  try {
    const result = await Promise.race([new Promise((resolve, reject) => {
      child.once('error', reject); child.once('exit', (code, signal) => resolve({ code, signal }));
    }), new Promise((_, reject) => { timer = setTimeout(() => { child.kill(); reject(new Error('Owned fixture inspection timeout')); }, 120000); })]);
    assert.deepEqual(result, { code: 0, signal: null }, 'Public package inspection failed');
    assert.ok(output.length < 4096 && error.length < 65536);
  } finally { clearTimeout(timer); }
}
async function walk(root, base = root) {
  const result = [];
  for (const entry of await fs.readdir(root, { withFileTypes: true })) {
    const file = path.join(root, entry.name); assert.equal(entry.isSymbolicLink(), false);
    result.push(path.relative(base, file).split(path.sep).join('/'));
    if (entry.isDirectory()) result.push(...await walk(file, base));
  }
  return result.sort();
}
async function run(options) {
  assert.equal(process.platform, 'win32'); assert.equal(process.arch, 'x64');
  assert.equal(process.env.GITHUB_ACTIONS, 'true');
  const parent = path.dirname(options.fixtures);
  assert.equal((await fs.lstat(parent)).isSymbolicLink(), false);
  assert.equal((await fs.lstat(options.fixtures)).isSymbolicLink(), false);
  const receipt = JSON.parse(await fs.readFile(path.join(options.fixtures, 'fixture-receipt.json'), 'utf8'));
  assert.equal(receipt.kind, 'fundstrace-windows-import-fixtures'); assert.equal(receipt.format, 1);
  assert.equal(path.resolve(receipt.outputRoot), options.fixtures); assert.equal(path.resolve(receipt.python), options.python);
  assert.deepEqual(receipt.versions, { openpyxl: '3.1.5', xlwt: '1.3.0', duckdb: '1.5.6' });
  assert.equal(receipt.developmentLockSha256, digest(await fs.readFile(path.join(__dirname, '../../requirements-dev.lock'))));
  assert.equal(receipt.generatorSha256, digest(await fs.readFile(path.join(__dirname, '../../scripts/windows_import_fixtures.py'))));
  assert.deepEqual(receipt.files.map(file => file.name).sort(), FILES);
  assert.deepEqual((await fs.readdir(options.fixtures)).sort(), [...FILES, 'fixture-receipt.json'].sort());
  const inputs = new Map();
  for (const item of receipt.files) {
    assert.equal(path.basename(item.name), item.name);
    const file = path.join(options.fixtures, item.name); assert.equal((await fs.lstat(file)).isSymbolicLink(), false);
    const data = await fs.readFile(file); assert.equal(data.length, item.bytes); assert.equal(digest(data), item.sha256);
    inputs.set(item.name, data);
  }
  const report = { schemaVersion: 1, status: 'failed', mode: 'installed-frozen-public-imports',
    checks: Object.fromEntries(CHECKS.map(key => [key, false])), fixtureFiles: receipt.files,
    fixtureVersions: receipt.versions, developmentLockSha256: receipt.developmentLockSha256,
    backendManifestSha256: digest(await fs.readFile(path.join(options.backend, 'build-manifest.json'))),
    backendExecutableSha256: digest(await fs.readFile(path.join(options.backend, 'funds-trace-backend.exe'))) };
  const runtime = await fs.mkdtemp(path.join(parent, 'formats-owned-'));
  const instances = [], summaries = [];
  let stage = 'owned-runtime', failed;
  const baseEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(FUND_|FUNDS_TRACE_|PYTHON|MPLCONFIGDIR|XDG_CACHE_HOME|TMPDIR$|TEMP$|TMP$)/i.test(key)));
  async function boot(label) {
    const root = path.join(runtime, label); await fs.mkdir(root);
    const env = { ...baseEnv, PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1', PYTHONNOUSERSITE: '1' };
    for (const [key, leaf] of [['MPLCONFIGDIR', 'mpl'], ['XDG_CACHE_HOME', 'cache'], ['TEMP', 'tmp'], ['TMP', 'tmp'], ['TMPDIR', 'tmp']]) {
      const target = path.join(root, leaf); await fs.mkdir(target, { recursive: true }); env[key] = target;
    }
    const dataDir = path.join(root, 'data'); assert.ok(within(runtime, dataDir));
    await assert.rejects(fs.stat(path.join(dataDir, 'fund.duckdb')), { code: 'ENOENT' });
    const config = launchConfig({ packaged: true, resourcesPath: path.dirname(options.backend), dataDir, env });
    assert.equal(config.cwd, options.backend); assert.equal(path.basename(config.command), 'funds-trace-backend.exe');
    for (const key of ['TEMP', 'TMP', 'TMPDIR', 'MPLCONFIGDIR', 'XDG_CACHE_HOME']) assert.ok(within(root, config.env[key]));
    let child;
    const instance = { root, dataDir };
    instance.backend = startBackend(config, { spawnProcess: (...args) => {
      child = spawn(...args);
      instance.exited = new Promise(resolve => { child.once('exit', (code, signal) => resolve({ code, signal })); child.once('error', () => resolve({ code: null, signal: null })); });
      return child;
    } });
    instances.push(instance); instance.ready = await instance.backend.ready;
    assert.equal((await fs.lstat(path.join(dataDir, 'fund.duckdb'))).isFile(), true);
    report.checks.ownedRuntime = true;
    return instance;
  }
  async function eof(instance) {
    assert.equal(await instance.backend.stop(), true);
    let timer;
    try { assert.deepEqual(await Promise.race([instance.exited, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Owned backend exit timeout')), 15000); })]), { code: 0, signal: null }); }
    finally { clearTimeout(timer); }
    await assert.rejects(fetch(`${instance.ready.origin}/__desktop/health`, { signal: AbortSignal.timeout(3000) }));
  }
  async function request(ready, endpoint, options = {}, status = 200) {
    const response = await fetch(`${ready.origin}${endpoint}`, { ...options, redirect: 'manual',
      headers: { [HEADER]: ready.token, ...options.headers }, signal: AbortSignal.timeout(120000) });
    assert.equal(response.status, status, endpoint); return response;
  }
  async function json(ready, endpoint, options, status) { return (await request(ready, endpoint, options, status)).json(); }
  async function create(ready, name) { return (await json(ready, '/api/v1/cases', post({ name: `SYN frozen ${name}` }), 201)).id; }
  async function preview(ready, cid, input, filename = input) {
    const body = new FormData(); body.append('file', new Blob([inputs.get(input)]), filename);
    const view = await json(ready, `/api/v1/cases/${cid}/imports/preview`, { method: 'POST', body });
    assert.equal(view.automatic, true); return view;
  }
  async function complete(ready, cid, view) {
    const base = `/api/v1/cases/${cid}/imports/${view.import_id}`;
    assert.equal((await json(ready, `${base}/commit`, post({ mode: view.mode, mapping: view.mapping }))).started, true);
    const deadline = Date.now() + 120000;
    while (true) {
      const job = await json(ready, base);
      if (job.stage === 'done') return job;
      assert.notEqual(job.stage, 'error'); assert.ok(Date.now() < deadline); await delay(100);
    }
  }
  async function upload(ready, cid, file, name = file) { const view = await preview(ready, cid, file, name); return { view, job: await complete(ready, cid, view) }; }
  async function stats(ready, cid, n, totals, coverage) {
    const value = await json(ready, `/api/v1/cases/${cid}/stats`);
    assert.equal(value.flows, n); assert.deepEqual(value.amount_totals, totals);
    if (coverage) for (const [key, expected] of Object.entries(coverage)) assert.equal(value.import_coverage[key], expected, key);
    return value;
  }
  async function inspect(ready, cid, label, envRoot) {
    const file = path.join(runtime, `${label}.fundcase`), output = path.join(runtime, `${label}.json`);
    await fs.writeFile(file, Buffer.from(await (await request(ready, `/api/cases/${cid}/export`)).arrayBuffer()));
    const env = { ...baseEnv, PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1', PYTHONNOUSERSITE: '1', TEMP: path.join(envRoot, 'tmp'), TMP: path.join(envRoot, 'tmp'), TMPDIR: path.join(envRoot, 'tmp') };
    await command(options.python, [path.join(__dirname, '../../scripts/windows_import_fixtures.py'), '--inspect-package', file, '--output-json', output], env);
    const value = JSON.parse(await fs.readFile(output, 'utf8'));
    assert.equal(value.packageSha256, digest(await fs.readFile(file))); return { file, value };
  }
  async function provenance(ready, cid, pkg) {
    const sources = pkg.tables.import_sources, reports = [], traces = [];
    const flows = new Map(pkg.tables.flows.map(row => [row.id, JSON.stringify(literalFacts([row])[0])]));
    for (const source of sources) {
      const value = await json(ready, `/api/v1/cases/${cid}/sources/${source.source_id}/report`);
      assert.equal(value.filename, source.filename); assert.equal(value.sha256, source.sha256); assert.equal(value.profile, source.parser_version);
      const members = pkg.tables.import_members.filter(row => row.source_id === source.source_id).map(({ source_id, ...row }) => row);
      assert.deepEqual(sorted(value.members), sorted(members));
      const records = pkg.tables.import_records.filter(row => row.source_id === source.source_id);
      assert.equal(value.items.length, records.length);
      const recordNumbers = value.items.map(item => item.record_no);
      assert.equal(new Set(recordNumbers).size, recordNumbers.length, 'Duplicate public report record');
      assert.deepEqual(recordNumbers.sort((a, b) => a - b), records.map(record => record.record_no).sort((a, b) => a - b));
      for (const item of value.items) {
        const row = records.find(record => record.record_no === item.record_no); assert.ok(row);
        for (const key of ['record_no', 'member_path', 'source_row', 'record_key', 'disposition', 'reason_code']) assert.deepEqual(item[key], row[key]);
        assert.deepEqual(item.details, JSON.parse(row.details_json));
        const links = pkg.tables.flow_sources.filter(link => link.source_id === source.source_id && link.source_row === item.record_no);
        assert.equal(item.flow_id, links.length ? links[0].flow_id : null);
      }
      reports.push({ source: sourceKey(source), members: sorted(value.members), items: value.items.map(({ flow_id, ...item }) => ({ ...item, flow: flow_id === null ? null : flows.get(flow_id) })) });
    }
    for (const flow of pkg.tables.flows) {
      const value = await json(ready, `/api/v1/cases/${cid}/flows/${flow.id}/sources?page_size=200`);
      assert.equal(value.items.length, value.total);
      const links = pkg.tables.flow_sources.filter(link => link.flow_id === flow.id); assert.equal(value.total, links.length);
      const traceKeys = value.items.map(item => JSON.stringify([item.source_id, item.source_row]));
      assert.equal(new Set(traceKeys).size, traceKeys.length, 'Duplicate public flow source');
      assert.deepEqual(traceKeys.sort(), links.map(link => JSON.stringify([link.source_id, link.source_row])).sort());
      const normalized = [];
      for (const item of value.items) {
        const source = sources.find(row => row.source_id === item.source_id); assert.ok(source);
        const link = links.find(row => row.source_id === item.source_id && row.source_row === item.source_row); assert.ok(link);
        const record = pkg.tables.import_records.find(row => row.source_id === item.source_id && row.record_no === item.source_row); assert.ok(record);
        for (const key of ['filename', 'sha256', 'size_bytes', 'mode', 'sheet', 'row_kind', 'parser_version']) assert.deepEqual(item[key], source[key]);
        assert.equal(item.member_path, record.member_path); assert.equal(item.member_row, record.source_row); assert.equal(item.disposition, link.disposition);
        assert.deepEqual(item.record_details, JSON.parse(record.details_json));
        normalized.push([sourceKey(source), item.source_row, item.disposition, item.member_path, item.member_row, item.record_details]);
      }
      traces.push([flows.get(flow.id), sorted(normalized)]);
    }
    return { reports: sorted(reports), traces: sorted(traces) };
  }
  const exported = [];
  try {
    stage = 'frozen-first-process'; const first = await boot('produce'), ready = first.ready;
    const denied = await fetch(`${ready.origin}/api/v1/cases`, { signal: AbortSignal.timeout(10000) }); assert.equal(denied.status, 403); await denied.arrayBuffer();
    const license = await json(ready, '/api/license/info'); assert.equal(license.activation_required, false); assert.equal(license.activated, false);
    report.checks.desktopAuthentication = report.checks.activationFree = true;
    async function save(cid, label, expectedRaw, rawTotals) {
      const item = await inspect(ready, cid, label, first.root);
      assertMoney(item.value.tables.flows, expectedRaw);
      assert.deepEqual(literalFacts(item.value.tables.flows), literalFacts(expectedFacts(expectedRaw, label.startsWith('chain'))));
      assert.deepEqual(item.value.rawAmountTotals, rawTotals);
      const expectedEffective = label === 'chain' ? CHAIN.filter(row => ['SYN-W', 'SYN-XW'].includes(row[0]))
        : label === 'chain-conflict' ? CHAIN.filter(row => row[0] !== 'SYN-XP') : expectedRaw;
      const effectiveRows = (await json(ready, `/api/v1/cases/${cid}/flows?page_size=200`)).items;
      assert.deepEqual(effectiveFacts(effectiveRows), literalFacts(expectedFacts(expectedEffective, label.startsWith('chain'))));
      const exactStats = await json(ready, `/api/v1/cases/${cid}/stats`);
      assert.equal(exactStats.flows, expectedEffective.length); assert.equal(exactStats.accounts, label === 'suffixless' ? 2 : 3);
      const snapshot = { package: canonicalPackage(item.value), provenance: await provenance(ready, cid, item.value),
        effective: effectiveFacts(effectiveRows), stats: statFacts(exactStats) };
      exported.push({ cid, label, ...item, snapshot }); return item.value;
    }
    stage = 'four-formats'; const simple = await create(ready, 'four formats'), fourFormatImports = [];
    for (const [index, file] of ['formats.csv', 'formats.xlsx', 'formats.xls', 'formats.zip'].entries()) {
      const { view, job } = await upload(ready, simple, file);
      assert.equal(job.result.ok, index === 0 ? 3 : 0); assert.equal(job.result.rows_dup, index === 0 ? 0 : 3);
      assert.equal(view.table_summary.profile, file.endsWith('.xls') ? 'funds-trace-table-v18' : file.endsWith('.zip') ? 'funds-trace-table-archive-v4' : 'funds-trace-table-v17');
      assert.equal(view.table_summary.unresolved, 0);
      fourFormatImports.push({ sourceId: view.import_id, filename: file, profile: view.table_summary.profile });
      assertMoney((await json(ready, `/api/v1/cases/${simple}/flows?page_size=200`)).items, SIMPLE);
    }
    const simpleStats = await stats(ready, simple, 3, [{ currency: 'CNY', amount: '125.48' }, { currency: 'USD', amount: '4.56' }]); assert.equal(simpleStats.accounts, 3);
    const profile = await json(ready, `/api/accounts/000SYN-A/profile?case_id=${simple}`);
    assert.deepEqual(profile.out_totals, [{ currency: 'CNY', amount: '123.45' }, { currency: 'USD', amount: '4.56' }]);
    assert.deepEqual(profile.in_totals, [{ currency: 'CNY', amount: '2.03' }]);
    const effective = (await json(ready, `/api/v1/cases/${simple}/flows?page_size=200`)).items;
    await json(ready, '/api/track', post({ case_id: simple, flow_ids: effective.map(row => row.id) }));
    await json(ready, '/api/canvases/from-flows', post({ case_id: simple, name: 'SYN frozen saved canvas', flow_ids: effective.map(row => row.id), snapshot: {} }));
    const simplePkg = await save(simple, 'four-formats', SIMPLE, [{ currency: 'CNY', amount: '125.48' }, { currency: 'USD', amount: '4.56' }]);
    assertFourFormatSources(simplePkg, fourFormatImports, inputs);
    report.checks.fourFormatsExact = true;
    stage = 'deterministic-controls';
    for (const file of ['regions.csv', 'regions.xlsx', 'typed-controls.xls']) {
      const cid = await create(ready, file), { view, job } = await upload(ready, cid, file);
      assert.equal(job.result.ok, file.startsWith('regions') ? 2 : 1); assert.equal(view.table_summary.unresolved, 2);
      const journal = await json(ready, `/api/v1/cases/${cid}/sources/${view.import_id}/report`);
      assert.deepEqual(journal.items.map(row => row.reason_code), file.startsWith('regions')
        ? ['transaction_valid', 'ambiguous_region_header', 'ambiguous_region_header', 'repeated_header', 'transaction_valid']
        : ['transaction_valid', 'formula_value', 'invalid_cell_type']);
      const rows = (await json(ready, `/api/v1/cases/${cid}/flows?page_size=200`)).items;
      if (file.startsWith('regions')) assert.deepEqual(sorted(rows.map(row => [timeOf(row.trade_time), row.payer_account, row.payee_account, row.amount, row.currency])), sorted([
        ['2026-01-01T00:00:00', 'SYN-A', 'SYN-B', '1.23', 'CNY'], ['2026-01-05T00:00:00', 'SYN-SENDER', 'SYN-RECIPIENT', '3.45', 'USD']]));
      else assert.equal(rows[0].payer_account, '00011112222333344445');
    }
    report.checks.deterministicControls = true;
    for (const conflict of [false, true]) {
      stage = conflict ? 'late-conflict' : 'cancellation-chain';
      const cid = await create(ready, stage), prefix = conflict ? 'chain-conflict' : 'chain';
      let view = await preview(ready, cid, `${prefix}.csv`, 'ordinary.csv');
      if (conflict) view = await json(ready, `/api/v1/cases/${cid}/imports/${view.import_id}/preview`, post({ mode: view.mode, mapping: { ...view.mapping, summary: '商户名称' } }));
      else await complete(ready, cid, view);
      const zip = await upload(ready, cid, `${prefix}.zip`, 'nested.zip'); assert.equal(zip.view.table_summary.cancelled, 6);
      const zipJournalBefore = await json(ready, `/api/v1/cases/${cid}/sources/${zip.view.import_id}/report`);
      if (conflict) await complete(ready, cid, view);
      const expected = { unresolved_records: conflict ? 7 : 0, cancelled_records: conflict ? 2 : 6,
        excluded_flows: conflict ? 1 : 3, financial_interpretation_complete: !conflict };
      const state = await stats(ready, cid, conflict ? 4 : 2, [{ currency: 'CNY', amount: conflict ? '33.00' : '3.00' }], expected); assert.equal(state.accounts, 3);
      const ordinary = await json(ready, `/api/v1/cases/${cid}/sources/${view.import_id}/report`);
      const zipJournal = await json(ready, `/api/v1/cases/${cid}/sources/${zip.view.import_id}/report`); assert.deepEqual(zipJournal, zipJournalBefore);
      assert.deepEqual(journalCounts(ordinary), conflict ? { 'unresolved/conflicting_field': 1, 'imported/transaction_valid': 3, 'unresolved/negative_amount': 3, 'duplicate/transaction_valid': 1 }
        : { 'imported/transaction_valid': 5, 'unresolved/negative_amount': 3 });
      assert.deepEqual(journalCounts(zipJournal), conflict ? { 'imported/transaction_valid': 2, 'cancelled/matched_cancellation': 6 }
        : { 'duplicate/transaction_valid': 2, 'cancelled/matched_cancellation': 6 });
      assertMoney((await json(ready, `/api/v1/cases/${cid}/flows?page_size=200`)).items, CHAIN.filter(row => conflict ? row[0] !== 'SYN-XP' : ['SYN-W', 'SYN-XW'].includes(row[0])));
      const rawPackage = await save(cid, prefix, CHAIN, [{ currency: 'CNY', amount: '40.00' }]);
      for (const source of rawPackage.tables.import_sources) {
        const input = source.filename === 'ordinary.csv' ? `${prefix}.csv` : `${prefix}.zip`;
        assert.equal(source.sha256, digest(inputs.get(input))); assert.equal(source.size_bytes, inputs.get(input).length);
        const records = rawPackage.tables.import_records.filter(row => row.source_id === source.source_id);
        assert.deepEqual(records.map(row => row.source_row), [2, 3, 4, 5, 6, 7, 8, 9]);
        assert.ok(records.every(row => row.member_path === (source.filename === 'ordinary.csv' ? 'table.csv' : 'bank/renamed.csv')));
      }
      report.checks[conflict ? 'lateConflictExact' : 'cancellationChainExact'] = true;
    }
    stage = 'suffixless'; const p01 = await create(ready, 'suffixless'); const imported = await upload(ready, p01, 'ordinary'); assert.equal(imported.job.result.ok, 1);
    const p01raw = [['', '2026-02-01T00:00:00', 'SYN-P01-A', 'SYN-P01-B', '2.03', null, 'USD', '']];
    const p01pkg = await save(p01, 'suffixless', p01raw, [{ currency: 'USD', amount: '2.03' }]);
    assert.equal(p01pkg.tables.import_sources[0].filename, 'ordinary'); assert.equal(p01pkg.tables.import_sources[0].sha256, digest(inputs.get('ordinary')));
    assert.equal(p01pkg.tables.import_sources[0].size_bytes, inputs.get('ordinary').length);
    assert.equal(p01pkg.tables.import_sources[0].parser_version, 'funds-trace-table-v17');
    assert.equal(p01pkg.tables.import_records[0].member_path, 'table.csv'); assert.equal(p01pkg.tables.import_records[0].source_row, 2);
    report.checks.suffixlessPackageExact = report.checks.sourceReportsAndTraces = report.checks.publicRawPackageFacts = true;
    await eof(first);
    stage = 'clean-process-restore'; const restored = await boot('restore');
    const empty = await json(restored.ready, '/api/v1/cases'); assert.ok(empty.items.length === 1);
    for (const item of exported) {
      const body = new FormData(); body.append('file', new Blob([await fs.readFile(item.file)]), `${item.label}.fundcase`);
      const result = await json(restored.ready, '/api/cases/import', { method: 'POST', body }); const cid = String(result.case_id);
      const reexport = await inspect(restored.ready, cid, `${item.label}-restored`, restored.root);
      assert.deepEqual(canonicalPackage(reexport.value), item.snapshot.package);
      assert.deepEqual(await provenance(restored.ready, cid, reexport.value), item.snapshot.provenance);
      const priorStats = await json(restored.ready, `/api/v1/cases/${cid}/stats`);
      assert.deepEqual(statFacts(priorStats), item.snapshot.stats);
      assert.deepEqual(effectiveFacts((await json(restored.ready, `/api/v1/cases/${cid}/flows?page_size=200`)).items), item.snapshot.effective);
      const replay = item.label === 'four-formats' ? ['formats.csv', 'formats.xlsx', 'formats.xls', 'formats.zip'] : item.label === 'suffixless' ? ['ordinary'] : item.label === 'chain' ? ['chain.csv', 'chain.zip'] : ['chain-conflict.zip'];
      for (const file of replay) { const repeated = await upload(restored.ready, cid, file, file.startsWith('chain') ? (file.endsWith('.zip') ? 'nested.zip' : 'ordinary.csv') : file); assert.equal(repeated.job.result.ok, 0); }
      const afterStats = await json(restored.ready, `/api/v1/cases/${cid}/stats`);
      assert.deepEqual(statFacts(afterStats), item.snapshot.stats);
      assert.deepEqual(effectiveFacts((await json(restored.ready, `/api/v1/cases/${cid}/flows?page_size=200`)).items), item.snapshot.effective);
      if (item.label === 'chain-conflict') assert.equal(afterStats.import_coverage.unresolved_records, 7);
      summaries.push({ label: item.label, rawFlows: item.value.rawFlows, rawAmountTotals: item.value.rawAmountTotals,
        effectiveFlows: afterStats.flows, effectiveAmountTotals: afterStats.amount_totals, coverage: afterStats.import_coverage,
        restoredFactsAndSources: true, reimportAddsZero: replay.length > 0, replayCount: replay.length });
    }
    report.checks.cleanProcessRestore = report.checks.reimportAddsZero = true;
    await eof(restored); report.checks.eofShutdown = true;
  } catch (error) { failed = error; report.failedStage = stage; }
  finally {
    for (const instance of instances) { try { await instance.backend.stop(); let timer; try { await Promise.race([instance.exited, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Owned backend final exit timeout')), 15000); })]); } finally { clearTimeout(timer); } } catch (error) { failed ||= error; } }
    try { report.cleanupEntries = await walk(runtime); await fs.rm(runtime, { recursive: true }); await assert.rejects(fs.stat(runtime), { code: 'ENOENT' }); report.checks.cleanup = true; }
    catch (error) { failed ||= error; }
    report.cases = summaries;
    try {
      assert.equal(digest(await fs.readFile(path.join(options.backend, 'build-manifest.json'))), report.backendManifestSha256);
      assert.equal(digest(await fs.readFile(path.join(options.backend, 'funds-trace-backend.exe'))), report.backendExecutableSha256);
    } catch (error) { failed ||= error; }
    if (!failed && CHECKS.every(key => report.checks[key] === true)) report.status = 'passed';
    await fs.writeFile(options.report, JSON.stringify(report, null, 2) + '\n');
  }
  if (failed) throw failed;
  assert.equal(report.status, 'passed'); return report;
}

module.exports = { assertFourFormatSources, argsOf, within, canonicalPackage, journalCounts, moneyFacts, timeOf, effectiveFacts, statFacts, expectedFacts, CHECKS, FILES, SIMPLE, CHAIN, run };
if (require.main === module) run(argsOf(process.argv.slice(2))).then(() => process.stdout.write('{"status":"passed","installedFormats":true}\n')).catch(() => {
  process.stderr.write('Installed frozen import format acceptance failed; inspect the owned stage receipt.\n'); process.exitCode = 1;
});
