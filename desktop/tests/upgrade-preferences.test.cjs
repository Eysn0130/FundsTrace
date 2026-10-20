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
  await asar.createPackage(host, archive);
  const preferences = { fund_case: '3', fund_edge_type: 'polyline', fund_show_ids: '1',
    fund_show_photos: '0', fund_edge_labels: '["money","currency"]' };
  const file = path.join(user, 'preferences.json'), raw = Buffer.from(JSON.stringify(preferences));
  await fs.writeFile(file, raw);
  const invoke = expected => spawnSync(process.execPath, ['-e', reader, archive, user, JSON.stringify(expected)],
    { cwd: root, encoding: 'utf8', timeout: 15000 });
  const accepted = invoke(preferences);
  assert.equal(accepted.status, 0, accepted.stderr || accepted.stdout);
  assert.deepEqual(await fs.readFile(file), raw);
  assert.deepEqual(await fs.readdir(user), ['preferences.json']);
  const rejected = invoke({ ...preferences, fund_case: '4' });
  assert.notEqual(rejected.status, 0, 'a changed preference must fail the actual archived-store comparison');
  assert.deepEqual(await fs.readFile(file), raw);
});
