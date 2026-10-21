'use strict';
// Inspect the actual packaged host archive; never load its Main or runtime data.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const asar = require('@electron/asar');
const { createTransformer } = require('app-builder-lib/out/fileTransformer');
const { getCollectorByPackageManager, PM } = require('app-builder-lib/out/node-module-collector');
const { TmpDir } = require('temp-file');
const builder = require('../electron-builder.cjs');

const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const dependencyMaps = new Map();
function runtimeDependencies(source) {
  if (!dependencyMaps.has(source)) dependencyMaps.set(source, (async () => {
    const temporary = new TmpDir('fundstrace-resource-verification');
    try {
      const metadata = JSON.parse(await fs.readFile(path.join(source, 'package.json'), 'utf8'));
      const { nodeModules } = await getCollectorByPackageManager(PM.NPM, source, temporary).getNodeModules({ packageName: metadata.name });
      const result = new Map();
      function collect(modules, parent = '') {
        for (const module of modules) {
          const target = `${parent}node_modules/${module.name}`;
          result.set(target, module.dir);
          collect(module.dependencies || [], `${target}/`);
        }
      }
      collect(nodeModules);
      return result;
    } finally { await temporary.cleanup(); }
  })());
  return dependencyMaps.get(source);
}
async function verifyHostArchive(archive, source = path.resolve(__dirname, '..')) {
  const entries = asar.listPackage(archive).map(name => name.replaceAll('\\', '/').replace(/^\//, '')).sort();
  const hostEntries = entries.filter(name => name !== 'node_modules' && !name.startsWith('node_modules/'));
  const expectedHostEntries = new Set(builder.files);
  for (const name of builder.files) {
    for (let parent = path.posix.dirname(name); parent !== '.'; parent = path.posix.dirname(parent)) expectedHostEntries.add(parent);
  }
  assert.deepEqual(hostEntries, [...expectedHostEntries].sort(), 'Packaged host must contain exactly the reviewed files');
  const expected = JSON.parse(await fs.readFile(path.join(source, 'package.json'), 'utf8'));
  const actual = JSON.parse(asar.extractFile(archive, 'package.json').toString('utf8'));
  assert.equal(actual.name, expected.name);
  assert.equal(actual.version, expected.version);
  assert.equal(actual.main, 'main.cjs');
  assert.deepEqual(actual.dependencies, expected.dependencies);
  // Dependencies are packaged by electron-builder in addition to files. Every
  // included dependency byte must match this candidate's locked npm install.
  const dependencyFiles = entries.filter(name => name.startsWith('node_modules/'));
  const modules = await runtimeDependencies(source);
  const transform = createTransformer(source, builder);
  for (const name of dependencyFiles) {
    if (asar.statFile(archive, path.normalize(name)).files) continue;
    // electron-builder hoists production versions independently of the npm
    // development layout. Match the exact pinned collector destination first.
    const tail = name.slice(name.lastIndexOf('node_modules/') + 'node_modules/'.length);
    const parts = tail.split('/');
    const packageName = parts[0].startsWith('@') ? parts.slice(0, 2).join('/') : parts[0];
    const owner = name.slice(0, name.lastIndexOf('node_modules/')) + `node_modules/${packageName}`;
    assert.ok(modules.has(owner), `Unexpected packaged dependency: ${owner}`);
    const local = path.join(modules.get(owner), name.slice(owner.length + 1));
    const transformed = await transform(local);
    const expectedBytes = transformed == null ? await fs.readFile(local) : Buffer.from(transformed);
    assert.equal(digest(asar.extractFile(archive, path.normalize(name))), digest(expectedBytes), `Packaged dependency drift: ${name}`);
  }
  for (const owner of modules.keys()) assert.ok(entries.includes(`${owner}/package.json`), `Missing update dependency: ${owner}`);
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
module.exports = { verifyHostArchive, runtimeDependencies };

if (require.main === module) {
  (async () => {
    assert.equal(process.argv.length, 4);
    const [candidate, report] = process.argv.slice(2);
    assert.ok(path.isAbsolute(candidate) && path.isAbsolute(report));
    const result = await verifyHostArchive(path.join(candidate, 'resources', 'app.asar'));
    await fs.writeFile(report, JSON.stringify(result, null, 2) + '\n', { flag: 'wx' });
    console.log('Packaged host allowlist, identity, preload/preferences and source hashes verified.');
  })().catch(error => { console.error(`Packaged host resource integrity failed: ${String(error.message).slice(0, 1000)}`); process.exitCode = 1; });
}
