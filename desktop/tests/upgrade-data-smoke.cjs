'use strict';
// CI-only compatibility fixture created by the actual prior installed backend.
// Tokens and HTTP bodies remain in memory; stdout contains fixed check names only.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { createHash } = require('node:crypto');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { launchConfig, HEADER } = require('../host-policy.cjs');
const { startBackend } = require('../backend.cjs');

const SENTINEL = 'FundsTrace synthetic installer lifecycle v1\n';
const EMPTY_CASE = 'SYN prior installer lifecycle case';
const CASE = 'SYN prior compatibility populated case';
const OTHER_CASE = 'SYN prior compatibility isolated case';
const CANVAS = 'SYN prior compatibility saved canvas';
const ACCOUNTS = ['SYN-UP-A', 'SYN-UP-B', 'SYN-UP-C'];
const NAMES = ['合成兼容甲', '合成兼容乙', '合成兼容丙'];
const MONEY = ['9999999999999999.99', '0.01', '0.02', '20.10', '30.20'];
const TOTALS = [{ currency: 'CNY', amount: '10000000000000000.02' }, { currency: 'USD', amount: '50.30' }];
const FIELDS = ['trade_no', 'trade_time', 'payer_name', 'payer_account', 'payer_bank', 'payee_name',
  'payee_account', 'payee_bank', 'amount', 'balance', 'currency', 'summary', 'channel', 'self_account', 'self_name'];
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const post = body => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
let stage = 'ciGuard';

function within(root, target) {
  const relative = path.relative(root, target);
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}
function argumentsOf(args) {
  assert.equal(args.length, 5);
  const installRoot = args.shift(), values = {};
  while (args.length) {
    const key = args.shift(), value = args.shift();
    assert.ok(['--mode', '--fixture'].includes(key) && !values[key] && value); values[key] = value;
  }
  assert.ok(path.isAbsolute(installRoot) && path.isAbsolute(values['--fixture']));
  assert.ok(['seed', 'verify'].includes(values['--mode']));
  return { installRoot: path.resolve(installRoot), fixture: path.resolve(values['--fixture']), mode: values['--mode'] };
}
function fixtureCsv(isolated = false) {
  const header = ['交易流水号', '交易时间', '付款户名', '付款账号', '收款户名', '收款账号', '交易金额', '交易后余额', '币种', '摘要'];
  const rows = isolated ? [['SYN-UP-OTHER', '2026-01-01 10:00:00', NAMES[0], ACCOUNTS[0], NAMES[1], ACCOUNTS[1], '777.77', '-0.01', 'CNY', 'SYN isolated only']]
    : MONEY.map((amount, i) => {
      const start = i < 3 ? i : i - 3, end = (start + 1) % 3;
      return [`SYN-UP-${i + 1}`, `2026-01-01 10:0${i}:00`, NAMES[start], ACCOUNTS[start], NAMES[end], ACCOUNTS[end], amount,
        i === 0 ? '-0.01' : '', i < 3 ? 'CNY' : 'USD', `SYN prior compatibility leg ${i + 1}`];
    });
  return Buffer.from(`\uFEFF${[header, ...rows].map(row => row.join(',')).join('\r\n')}\r\n`, 'utf8');
}
function facts(rows, keepIds = true) {
  return rows.map(row => Object.fromEntries([...(keepIds ? ['id', 'case_id', 'batch_id'] : []), ...FIELDS]
    .map(key => [key, row[key]]))).sort((a, b) => a.trade_no.localeCompare(b.trade_no));
}
function normalizeSnapshot(snapshot, rows) {
  const tradeById = new Map(rows.map(row => [String(row.id), row.trade_no]));
  const value = structuredClone(snapshot);
  for (const edge of value.edges || []) if (edge.data?.ids) {
    edge.data.ids = edge.data.ids.map(id => {
      assert.ok(tradeById.has(String(id))); return tradeById.get(String(id));
    }).sort();
  }
  return value;
}
function canvasSnapshot(rows) {
  return { case_name: CASE, w: 1200, h: 800,
    nodes: ACCOUNTS.map((id, i) => ({ id, x: 140 + i * 240, y: 180 + (i % 2) * 100, label: NAMES[i],
      size: 32, style: { fill: '#ffffff', stroke: '#0969da' }, labelStyle: { fontFamily: 'Arial', fontSize: 13 },
      labelOffset: { x: 0, y: 0 }, labelVisible: true, data: {} })),
    edges: rows.filter(row => row.currency === 'CNY').map((row, i) => ({ source: row.payer_account, target: row.payee_account,
      type: i === 0 ? 'polyline' : 'line', controlPoints: i === 0 ? [{ x: 240, y: 130 }] : [],
      routeManual: true, routeAutoEligible: true, curvePolicy: null, labelPlacement: 'inline',
      style: { stroke: '#0969da', lineWidth: 2 }, labelStyle: { fontFamily: 'Arial', fontSize: 13 },
      labelRatio: 0.35, labelManual: true, labelOffset: { x: 0, y: 0 }, labelVisible: true,
      lx: null, ly: null, bw: null, bh: null, label: `CNY ${row.amount}`, data: { ids: [row.id], cnt: 1, total: row.amount,
        currencies: ['CNY'], amount_totals: [{ currency: 'CNY', amount: row.amount }] } })),
    display: { layout: 'dagre', edgeType: 'line', edgeLabels: ['money'], showIds: false, showPhotos: false,
      labelMode: 'all', labelRenderPolicy: 'clean-midpoint-v1' },
    presentation: { version: 1, relationFilter: { currency: null, min: '', max: '', active: false },
      amountWidth: { enabled: false, currency: null }, nodeLabels: {}, nodeStyles: {}, edgeLabels: {},
      edgeStyles: { 'SYN-UP-A=>SYN-UP-B': { stroke: '#0969da', lineWidth: 2 } }, hiddenAccounts: [], onlyAccounts: null },
    view: { scope: 'all', mode: 'overview', total_nodes: 3, total_edges: 3, visible_nodes: 3, visible_edges: 3, label_mode: 'all' } };
}
async function response(ready, endpoint, options = {}, status = 200) {
  const value = await fetch(`${ready.origin}${endpoint}`, { ...options, redirect: 'manual',
    headers: { [HEADER]: ready.token, ...options.headers }, signal: AbortSignal.timeout(120000) });
  assert.equal(value.status, status); return value;
}
async function json(ready, endpoint, options, status) { return (await response(ready, endpoint, options, status)).json(); }
async function bytes(ready, endpoint, options) { return Buffer.from(await (await response(ready, endpoint, options)).arrayBuffer()); }
async function upload(ready, caseId, isolated = false) {
  const body = new FormData(), csv = fixtureCsv(isolated);
  body.append('file', new Blob([csv], { type: 'text/csv' }), 'synthetic-prior-compatibility.csv');
  const base = `/api/v1/cases/${caseId}`, preview = await json(ready, `${base}/imports/preview`, { method: 'POST', body });
  assert.equal(preview.stats.ok, isolated ? 1 : 5); assert.equal(preview.stats.skipped, 0);
  const endpoint = `${base}/imports/${preview.import_id}`;
  assert.equal((await json(ready, `${endpoint}/commit`, post({ mode: preview.mode, mapping: preview.mapping,
    self_accounts: [], mark_accounts: [] }))).started, true);
  const deadline = Date.now() + 120000;
  while (true) {
    const job = await json(ready, endpoint);
    if (job.stage === 'done') break;
    assert.notEqual(job.stage, 'error'); assert.ok(Date.now() < deadline); await delay(100);
  }
  return { sourceId: preview.import_id, sha256: digest(csv), relative: `uploads/${preview.import_id}.csv` };
}
async function capture(ready, caseId) {
  const base = `/api/v1/cases/${caseId}`;
  const rows = (await json(ready, `${base}/flows?page_size=200`)).items;
  const canvases = await json(ready, `/api/canvases?case_id=${caseId}`);
  const saved = [];
  for (const canvas of canvases) {
    const value = await json(ready, `/api/canvases/${canvas.id}?case_id=${caseId}`);
    const graph = await json(ready, `/api/graph?case_id=${caseId}&canvas_id=${canvas.id}`);
    saved.push({ id: String(canvas.id), name: canvas.name, snapshot: value.snapshot,
      trackedIds: graph.tracked_ids.map(String).sort() });
  }
  const graph = await json(ready, `/api/graph?case_id=${caseId}`);
  return { caseId: String(caseId), rows: facts(rows), stats: await json(ready, `${base}/stats`),
    canvases: saved.sort((a, b) => a.name.localeCompare(b.name)), defaultTrackedIds: graph.tracked_ids.map(String).sort(),
    mains: (await json(ready, `/api/main-accounts?case_id=${caseId}`)).map(({ account, name, source }) => ({ account, name, source })),
    identities: (await json(ready, `/api/account-ids/list?case_id=${caseId}`)).map(({ account, id_no }) => ({ account, id_no })) };
}
async function assertSources(ready, dataDir, stored) {
  for (const entry of stored.sources) {
    assert.match(entry.sourceId, /^[0-9a-f]{32}$/);
    assert.equal(entry.relative, `uploads/${entry.sourceId}.csv`);
    assert.equal(digest(await bytes(ready, `/api/v1/cases/${entry.caseId}/sources/${entry.sourceId}/file`)), entry.sha256);
    const file = path.join(dataDir, entry.relative); assert.equal((await fs.lstat(file)).isSymbolicLink(), false);
    assert.equal(digest(await fs.readFile(file)), entry.sha256);
  }
}
async function verifyOriginal(ready, stored, dataDir) {
  assert.deepEqual(await capture(ready, stored.original.caseId), stored.original);
  assert.deepEqual(await capture(ready, stored.isolated.caseId), stored.isolated);
  const empty = await json(ready, `/api/v1/cases/${stored.emptyCaseId}/stats`); assert.equal(empty.flows, 0);
  await assertSources(ready, dataDir, stored);
}
async function importedMatches(ready, original, importedId) {
  const imported = await capture(ready, importedId);
  assert.deepEqual(facts(imported.rows, false), facts(original.rows, false));
  assert.deepEqual(imported.stats.amount_totals, TOTALS);
  assert.deepEqual(imported.mains, original.mains); assert.deepEqual(imported.identities, original.identities);
  const byTrade = (ids, rows) => ids.map(id => rows.find(row => row.id === String(id))?.trade_no).sort();
  assert.deepEqual(byTrade(imported.defaultTrackedIds, imported.rows), byTrade(original.defaultTrackedIds, original.rows));
  assert.equal(imported.canvases.length, original.canvases.length);
  for (const old of original.canvases) {
    const restored = imported.canvases.find(item => item.name === old.name); assert.ok(restored);
    assert.deepEqual(normalizeSnapshot(restored.snapshot, imported.rows), normalizeSnapshot(old.snapshot, original.rows));
    assert.deepEqual(byTrade(restored.trackedIds, imported.rows), byTrade(old.trackedIds, original.rows));
  }
  return imported;
}
async function analysis(ready, imported) {
  const caseId = Number(imported.caseId), checks = {};
  stage = 'upgradedAnalysisPaths';
  const paths = await json(ready, '/api/analysis/paths', post({ case_id: caseId, src: NAMES[0], dst: NAMES[2], order: 'time' }));
  assert.deepEqual(paths.src_accounts, [ACCOUNTS[0]]); assert.deepEqual(paths.dst_accounts, [ACCOUNTS[2]]);
  assert.deepEqual(paths.paths.map(item => ({ currency: item.currency, total: item.total, continuous: item.continuous })),
    [{ currency: 'CNY', total: '10000000000000000.00', continuous: true }, { currency: 'USD', total: '50.30', continuous: true }]);
  checks.upgradedAnalysisPaths = true;
  stage = 'upgradedAnalysisCycles';
  const result = await json(ready, '/api/analysis/run', post({ case_id: caseId,
    params: { algorithms: ['cycles'], subjects: [NAMES[1]], cycle_max_len: 3 } }));
  assert.equal(result.cycles.count, 1);
  const cycle = result.cycles.cycles[0]; assert.deepEqual(cycle.accounts, ACCOUNTS);
  assert.equal(cycle.currency, 'CNY'); assert.equal(cycle.total, '10000000000000000.02');
  assert.deepEqual(cycle.edges.map(edge => edge.amount), MONEY.slice(0, 3)); checks.upgradedAnalysisCycles = true;
  stage = 'upgradedAtomicCanvas';
  const before = imported.canvases.length, flowIds = cycle.edges.map(edge => edge.id);
  const request = { case_id: caseId, name: CANVAS, flow_ids: flowIds, snapshot: canvasSnapshot(imported.rows) };
  const created = await json(ready, '/api/canvases/from-flows', post(request)); assert.equal(created.added, 3);
  assert.ok(!imported.canvases.some(canvas => canvas.id === String(created.canvas_id)));
  const repeated = await json(ready, '/api/canvases/from-flows', post(request));
  assert.equal(repeated.canvas_id, created.canvas_id); assert.equal(repeated.added, 0);
  assert.equal((await json(ready, `/api/canvases?case_id=${caseId}`)).length, before + 1);
  const graph = await json(ready, `/api/graph?case_id=${caseId}&canvas_id=${created.canvas_id}`);
  assert.deepEqual(graph.tracked_ids.map(String).sort(), flowIds.map(String).sort());
  checks.upgradedAtomicCanvas = true; return checks;
}

async function main() {
  assert.equal(process.platform, 'win32'); assert.equal(process.arch, 'x64');
  assert.equal(process.env.GITHUB_ACTIONS, 'true'); assert.ok(Number(process.versions.node.split('.')[0]) >= 22);
  const args = argumentsOf(process.argv.slice(2));
  assert.ok(process.env.APPDATA && path.isAbsolute(process.env.APPDATA));
  const userRoot = path.join(process.env.APPDATA, 'FundsTrace'), dataDir = path.join(userRoot, 'data');
  assert.equal(await fs.readFile(path.join(userRoot, 'installer-lifecycle-sentinel.txt'), 'utf8'), SENTINEL);
  assert.equal((await fs.lstat(userRoot)).isSymbolicLink(), false);
  assert.ok(within(os.tmpdir(), args.fixture) && !within(userRoot, args.fixture) && !within(args.installRoot, args.fixture));
  if (args.mode === 'seed') await fs.mkdir(args.fixture);
  assert.equal((await fs.lstat(args.fixture)).isSymbolicLink(), false);
  const snapshotFile = path.join(args.fixture, 'snapshot.json'), packageFile = path.join(args.fixture, 'legacy.fundcase');
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) => !/^(FUND_|FUNDS_TRACE_|PYTHON|NODE_OPTIONS$)/i.test(name)));
  const config = launchConfig({ packaged: true, resourcesPath: path.join(args.installRoot, 'resources'), dataDir, env });
  const owned = []; let current;
  const boot = async () => {
    const instance = {};
    instance.backend = startBackend(config, { spawnProcess: (...values) => {
      const child = spawn(...values);
      instance.exited = new Promise(resolve => {
        child.once('exit', (code, signal) => resolve({ code, signal }));
        child.once('error', () => resolve({ code: null, signal: null }));
      });
      return child;
    } });
    current = instance; owned.push(instance); return instance.backend.ready;
  };
  const stop = async () => {
    await current.backend.stop();
    let timer;
    try {
      const exit = await Promise.race([current.exited, new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('EOF_TIMEOUT')), 15000);
      })]);
      assert.deepEqual(exit, { code: 0, signal: null });
    } finally { clearTimeout(timer); }
    await assert.rejects(fetch(`${(await current.backend.ready).origin}/__desktop/health`, { signal: AbortSignal.timeout(10000) }));
  };
  const checks = {};
  try {
    stage = `${args.mode}BackendReady`; let ready = await boot();
    if (args.mode === 'seed') {
      const cases = (await json(ready, '/api/v1/cases')).items;
      assert.ok(cases.length <= 1 && cases.every(item => item.name === '默认案件'));
      const create = async name => (await json(ready, '/api/v1/cases', post({ name }), 201)).id;
      const emptyCaseId = await create(EMPTY_CASE), caseId = await create(CASE), otherId = await create(OTHER_CASE);
      stage = 'priorPublicImport';
      const source = await upload(ready, caseId), otherSource = await upload(ready, otherId, true);
      const rows = (await json(ready, `/api/v1/cases/${caseId}/flows`)).items;
      assert.deepEqual(facts(rows).map(row => row.amount), MONEY);
      assert.deepEqual((await json(ready, `/api/v1/cases/${caseId}/stats`)).amount_totals, TOTALS);
      checks.priorBackendSeeded = true; checks.priorExactMoneyAndCurrency = true;
      stage = 'priorPublicCanvas';
      await json(ready, '/api/main-accounts', post({ case_id: Number(caseId), account: ACCOUNTS[0], name: NAMES[0] }));
      await json(ready, '/api/account-ids/set', post({ case_id: Number(caseId), account: ACCOUNTS[0], id_no: '000000000000000000' }));
      const canvas = await json(ready, '/api/canvases/save', post({ case_id: Number(caseId), name: CANVAS, snapshot: canvasSnapshot(rows) }));
      const cnyIds = rows.filter(row => row.currency === 'CNY').map(row => row.id);
      for (const canvasId of [0, canvas.canvas_id]) await json(ready, '/api/track', post({ case_id: Number(caseId), canvas_id: canvasId, flow_ids: cnyIds }));
      const stored = { schemaVersion: 1, emptyCaseId, original: await capture(ready, caseId), isolated: await capture(ready, otherId),
        sources: [{ ...source, caseId }, { ...otherSource, caseId: otherId }] };
      assert.equal(stored.original.canvases.length, 1); assert.deepEqual(stored.original.defaultTrackedIds, cnyIds.map(String).sort());
      checks.priorCanvasAndTracking = true;
      stage = 'priorPackageExport'; const exported = await bytes(ready, `/api/cases/${caseId}/export`);
      await fs.writeFile(packageFile, exported, { flag: 'wx' }); stored.packageSha256 = digest(exported); checks.priorPackageExported = true;
      await stop();
      stage = 'priorEofReopen'; ready = await boot(); await verifyOriginal(ready, stored, dataDir); await stop(); checks.priorEofReopened = true;
      stored.priorDatabaseSha256 = digest(await fs.readFile(path.join(dataDir, 'fund.duckdb')));
      await fs.writeFile(snapshotFile, `${JSON.stringify(stored, null, 2)}\n`, { flag: 'wx' });
    } else {
      const stored = JSON.parse(await fs.readFile(snapshotFile, 'utf8')); assert.equal(stored.schemaVersion, 1);
      stage = 'upgradedOriginalFacts'; await verifyOriginal(ready, stored, dataDir);
      checks.upgradedFactsUnchanged = true; checks.upgradedCanvasAndTracking = true; checks.upgradedSourceFilesUnchanged = true;
      stage = 'legacyPackageImport'; const exported = await fs.readFile(packageFile); assert.equal(digest(exported), stored.packageSha256);
      const body = new FormData(); body.append('file', new Blob([exported]), 'synthetic-prior.fundcase');
      const importedResult = await json(ready, '/api/cases/import', { method: 'POST', body });
      const imported = await importedMatches(ready, stored.original, String(importedResult.case_id)); checks.legacyPackageRoundTrip = true;
      stage = 'upgradedAnalysis'; Object.assign(checks, await analysis(ready, imported));
      await verifyOriginal(ready, stored, dataDir); await stop();
      stage = 'upgradedEofReopen'; ready = await boot(); await verifyOriginal(ready, stored, dataDir); await stop(); checks.upgradedEofReopened = true;
    }
  } finally { for (const instance of owned) await instance.backend.stop(); }
  process.stdout.write(`${JSON.stringify(checks)}\n`);
}
if (require.main === module) main().catch(error => {
  // Keep assertion bodies, process diagnostics and local paths out of CI logs.
  const line = String(error.stack || '').match(/upgrade-data-smoke\.cjs:(\d+):/)?.[1] || '0';
  process.stderr.write(`Upgrade data smoke failed at ${stage} line ${line}.\n`); process.exitCode = 1;
});
module.exports = { fixtureCsv, facts, normalizeSnapshot, canvasSnapshot };
