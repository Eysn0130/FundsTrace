'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');
const {createHash} = require('node:crypto');
const test = require('node:test');

const sourceFile = path.join(__dirname, 'installed-app-smoke.cjs');
const sentinel = 'FundsTrace synthetic installer lifecycle v1\n';
async function isolatedFunction(name, end, globals = {}) {
  const source = await fs.readFile(sourceFile, 'utf8');
  const start = source.indexOf(`function ${name}(`), finish = source.indexOf(end, start);
  assert.ok(start >= 0 && finish > start);
  const expression = source.slice(start - (source.slice(start - 6, start) === 'async ' ? 6 : 0), finish);
  return vm.runInNewContext(`${expression}; ${name}`, { assert, fs, path, SENTINEL:sentinel, ...globals });
}

test('fresh-startup is explicit, unique and exclusive with every existing special GUI mode', async () => {
  const base = [process.execPath, sourceFile, path.resolve('synthetic-install'), '--report', path.resolve('synthetic-report.json')];
  const read = async extra => (await isolatedFunction('argumentsOf', '\nfunction within', { process:{ argv:[...base, ...extra] } }))();
  const normal = await read([]);
  assert.equal(normal.freshStartup, false); assert.equal(normal.startupClose, false);
  const fresh = await read(['--fresh-startup']);
  assert.equal(fresh.freshStartup, true); assert.equal(fresh.startupClose, false);
  for (const other of [ ['--fresh-startup'], ['--startup-close'],
    ['--update-fixture', path.resolve('update.json')], ['--production-update', path.resolve('production.json')],
    ['--presentation-baseline', path.resolve('baseline.json')] ])
    await assert.rejects(read(['--fresh-startup', ...other]));
  assert.equal((await read(['--startup-close'])).startupClose, true);
  assert.equal((await read(['--presentation-baseline', path.resolve('baseline.json')])).freshStartup, false);
});

test('fresh preflight requires sentinel-only user state and does not create data or preferences', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'fundstrace-fresh-preflight-'));
  t.after(() => fs.rm(directory, { recursive:true, force:true }));
  const check = await isolatedFunction('assertStartupData', '\nasync function readPresentationPreferences');
  await assert.rejects(check(directory, true), { code:'ENOENT' });
  await fs.writeFile(path.join(directory, 'installer-lifecycle-sentinel.txt'), sentinel);
  await check(directory, true);
  assert.deepEqual(await fs.readdir(directory), ['installer-lifecycle-sentinel.txt']);
  const preferences = path.join(directory, 'preferences.json');
  await fs.writeFile(preferences, '{}');
  await assert.rejects(check(directory, true));
  assert.equal(await fs.readFile(preferences, 'utf8'), '{}');
  await fs.rm(preferences);
  const data = path.join(directory, 'data');
  await fs.mkdir(data); await assert.rejects(check(directory, true));
  await fs.writeFile(path.join(data, 'fund.duckdb'), 'synthetic database marker');
  await assert.rejects(check(directory, true));
  await check(directory, false); // Existing mature startup still requires its seeded database.
  assert.equal(await fs.readFile(path.join(data, 'fund.duckdb'), 'utf8'), 'synthetic database marker');
  await fs.rm(data, { recursive:true });
  await assert.rejects(check(directory, false), { code:'ENOENT' });
  await fs.writeFile(path.join(directory, 'installer-lifecycle-sentinel.txt'), 'unowned');
  await assert.rejects(check(directory, true));
});

test('fresh empty-case oracle uses renderer authenticated APIs and exact monetary strings', async () => {
  const source = await fs.readFile(sourceFile, 'utf8');
  const start = source.indexOf("  if (config.freshStartup) {\n    await check('authenticatedWorkspaceLoaded'");
  const body = source.slice(start, source.indexOf("    report.checks.defaultCaseEmpty = true;", start));
  const expression = body.split('await evaluate(`')[1].split('`), true);')[0];
  const verify = async (items, stats, status = 200) => vm.runInNewContext(expression, {
    fetch:async url => ({ ok:status === 200, json:async () => url.endsWith('/stats') ? stats : { items } }),
  });
  const items = [{ id:'1', name:'默认案件' }];
  const stats = { case_id:'1', flows:0, accounts:0, batches:0, amount_total:'0.00', amount_totals:[] };
  assert.equal(await verify(items, stats), true);
  for (const field of ['flows', 'accounts', 'batches']) assert.equal(await verify(items, {...stats, [field]:1}), false);
  assert.equal(await verify([...items, { id:'2', name:'unexpected seeded case' }], stats), false);
  assert.equal(await verify(items, {...stats, amount_total:0}), false);
  assert.equal(await verify(items, {...stats, amount_totals:[{currency:'CNY', amount:'0.00'}]}), false);
  assert.equal(await verify(items, stats, 403), false);
  assert.match(body, /denied\.status, 403/);
  assert.match(source, /if \(!value\.freshStartup && !value\.updateFixture/);
  const freshEnd = source.slice(start, source.indexOf('  if (config.presentationBaseline)', start));
  assert.doesNotMatch(freshEnd, /readPresentationPreferences|restorePresentationPreferences|runInstalledGraphJourney/);
  assert.match(freshEnd, /CaptionClose/); assert.match(freshEnd, /backendReleasedAfterClose/);
});


test('first-client preference evidence preserves exact bytes and rejects pre-merge or receipt tampering', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'fundstrace-fresh-preference-evidence-'));
  t.after(() => fs.rm(directory, {recursive:true, force:true}));
  const installer = await fs.readFile(path.resolve(__dirname, '../../scripts/test-windows-installer.ps1'), 'utf8');
  const code = installer.split("$FreshPreferenceEvidenceCode = @'\n")[1].split("\n'@")[0];
  const file = path.join(directory, 'preferences.json');
  // Include unusual but valid JSON whitespace; evidence must preserve bytes, not reserialize them.
  const validRaw = Buffer.from(' {"fund_window_state":"initial-client-value","fund_sidebar_width":"321","caption":"首启"}\r\n');
  await fs.writeFile(file, validRaw);
  const run = (mode, expected) => spawnSync(process.execPath,
    ['-e', code, mode, file, ...(expected ? [JSON.stringify(expected)] : [])],
    {encoding:'utf8', timeout:15000});
  const captured = run('capture');
  assert.equal(captured.status, 0, captured.stderr);
  const evidence = JSON.parse(captured.stdout);
  const decoded = Buffer.from(evidence.base64, 'base64');
  assert.deepEqual(decoded, validRaw);
  assert.equal(evidence.bytes, validRaw.length);
  assert.equal(evidence.sha256, createHash('sha256').update(validRaw).digest('hex'));
  assert.equal(run('verify', evidence).status, 0);
  for (const changed of [ {...evidence, bytes:evidence.bytes + 1},
    {...evidence, sha256:'0'.repeat(64)}, {...evidence, base64:`${evidence.base64} `},
    {...evidence, base64:Buffer.from('{}').toString('base64')} ])
    assert.notEqual(run('verify', changed).status, 0);
  // A semantic-equivalent rewrite by any process before the fixture merge still fails.
  await fs.writeFile(file, JSON.stringify(JSON.parse(validRaw.toString('utf8'))));
  assert.notEqual(run('verify', evidence).status, 0);
  await fs.writeFile(file, validRaw); assert.equal(run('verify', evidence).status, 0);
  assert.deepEqual(await fs.readFile(file), validRaw);
  await fs.writeFile(file, Buffer.alloc(16385, 32)); assert.notEqual(run('capture').status, 0);
  await fs.writeFile(file, '[]'); assert.notEqual(run('capture').status, 0);
});
