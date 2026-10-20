'use strict';
// Inspect the actual packaged host archive; never load its Main or runtime data.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const asar = require('@electron/asar');
const builder = require('../electron-builder.cjs');

const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
async function verifyHostArchive(archive, source = path.resolve(__dirname, '..')) {
  const entries = asar.listPackage(archive).map(name => name.replaceAll('\\', '/').replace(/^\//, '')).sort();
  assert.deepEqual(entries.filter(name => name !== 'node_modules' && !name.startsWith('node_modules/')), [...builder.files].sort(), 'Packaged host must contain exactly the reviewed files');
  const expected = JSON.parse(await fs.readFile(path.join(source, 'package.json'), 'utf8'));
  const actual = JSON.parse(asar.extractFile(archive, 'package.json').toString('utf8'));
  assert.equal(actual.name, expected.name);
  assert.equal(actual.version, expected.version);
  assert.equal(actual.main, 'main.cjs');
  assert.deepEqual(actual.dependencies, expected.dependencies);
  // Dependencies are packaged by electron-builder in addition to files. Every
  // included dependency byte must match this candidate's locked npm install.
  const dependencyFiles = entries.filter(name => name.startsWith('node_modules/'));
  for (const name of dependencyFiles) {
    const local = path.join(source, name);
    const stat = await fs.stat(local);
    if (stat.isDirectory()) continue;
    assert.equal(digest(asar.extractFile(archive, name)), digest(await fs.readFile(local)), `Packaged dependency drift: ${name}`);
  }
  for (const name of ['node_modules/electron-updater/package.json', 'node_modules/electron-updater/out/main.js'])
    assert.ok(entries.includes(name), `Missing update runtime: ${name}`);
  const hostFiles = {};
  for (const name of builder.files.filter(name => name !== 'package.json')) {
    const bytes = asar.extractFile(archive, name);
    assert.equal(digest(bytes), digest(await fs.readFile(path.join(source, name))), `Packaged host drift: ${name}`);
    hostFiles[name] = digest(bytes);
  }
  return { schemaVersion: 1, status: 'passed', version: actual.version,
    checks: { exactHostAllowlist: true, packageIdentity: true, sourceHashes: true, preferencesAndPreloadIncluded: true },
    hostFiles, appArchiveSha256: digest(await fs.readFile(archive)) };
}
module.exports = { verifyHostArchive };

if (require.main === module) {
  (async () => {
    assert.equal(process.argv.length, 4);
    const [candidate, report] = process.argv.slice(2);
    assert.ok(path.isAbsolute(candidate) && path.isAbsolute(report));
    const result = await verifyHostArchive(path.join(candidate, 'resources', 'app.asar'));
    await fs.writeFile(report, JSON.stringify(result, null, 2) + '\n', { flag: 'wx' });
    console.log('Packaged host allowlist, identity, preload/preferences and source hashes verified.');
  })().catch(() => { console.error('Packaged host resource integrity failed.'); process.exitCode = 1; });
}
