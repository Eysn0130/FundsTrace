'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');
const asar = require('@electron/asar');

const root = path.resolve(__dirname, '../..');

test('installed archive preference reader checks the actual store without writing presentation settings', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'fundstrace-upgrade-preferences-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const script = (await fs.readFile(path.join(root, 'scripts/test-windows-installer.ps1'), 'utf8')).replaceAll('\r\n', '\n');
  const reader = script.split("    $PreferenceReadCode = @'\n")[1].split("\n'@")[0];
  const host = path.join(directory, 'host'), archive = path.join(directory, 'app.asar'), user = path.join(directory, 'user');
  await fs.mkdir(host); await fs.mkdir(user);
  for (const name of ['preferences.cjs', 'host-policy.cjs']) await fs.copyFile(path.join(root, 'desktop', name), path.join(host, name));
  // asar 3.4.1 resolves createPackage at out.end(), before the destination
  // stream finishes. Wait for the producing process to exit before reading;
  // Windows can otherwise expose a short read padded with NUL bytes.
  const packed = spawnSync(process.execPath, [require.resolve('@electron/asar/bin/asar.js'), 'pack', host, archive],
    { cwd: root, encoding: 'utf8', timeout: 15000 });
  assert.equal(packed.status, 0, packed.stderr || packed.stdout);
  for (const name of ['preferences.cjs', 'host-policy.cjs']) {
    assert.deepEqual(asar.extractFile(archive, name), await fs.readFile(path.join(host, name)),
      'the completed fixture archive must preserve the complete module bytes');
  }
  const preferences = { fund_case: '3', fund_edge_type: 'polyline', fund_show_ids: '1',
    fund_show_photos: '0', fund_edge_labels: '["money","currency"]' };
  const file = path.join(user, 'preferences.json'), raw = Buffer.from(JSON.stringify(preferences));
  await fs.writeFile(file, raw);
  const invoke = (expected, input = archive) => spawnSync(process.execPath, ['-e', reader, input, user, JSON.stringify(expected)],
    { cwd: root, encoding: 'utf8', timeout: 15000 });
  const accepted = invoke(preferences);
  assert.equal(accepted.status, 0, accepted.stderr || accepted.stdout);
  assert.deepEqual(await fs.readFile(file), raw);
  assert.deepEqual(await fs.readdir(user), ['preferences.json']);
  const rejected = invoke({ ...preferences, fund_case: '4' });
  assert.notEqual(rejected.status, 0, 'a changed preference must fail the actual archived-store comparison');
  assert.deepEqual(await fs.readFile(file), raw);
  const damaged = path.join(directory, 'incomplete.asar'), bytes = await fs.readFile(archive);
  const info = asar.statFile(archive, 'preferences.cjs'), start = 8 + asar.getRawHeader(archive).headerSize + Number(info.offset);
  bytes.fill(0, start, start + info.size);
  await fs.writeFile(damaged, bytes);
  const incomplete = invoke(preferences, damaged);
  assert.notEqual(incomplete.status, 0, 'NUL-filled short-read output must fail before compiling a module');
  assert.match(incomplete.stderr, /Invalid archived module: name=preferences\.cjs, bytes=\d+, sha256=[0-9a-f]{64}, utf8=true, integrity=false/);
  assert.doesNotMatch(incomplete.stderr, /'use strict'|Invalid or unexpected token/,
    'invalid source bytes must produce metadata diagnostics rather than source text');
  assert.deepEqual(await fs.readFile(file), raw);
});
