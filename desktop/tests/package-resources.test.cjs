'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const asar = require('@electron/asar');
const builder = require('../electron-builder.cjs');
const { verifyHostArchive } = require('./package-resources.cjs');

test('actual ASAR inspection detects omitted preload, unexpected files and stale host content', async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'fundstrace-archive-test-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const source = path.resolve(__dirname, '..');
  async function archive(name, change) {
    const directory = path.join(root, name), file = path.join(root, name + '.asar');
    await fs.mkdir(directory);
    for (const entry of builder.files) await fs.copyFile(path.join(source, entry), path.join(directory, entry));
    if (change) await change(directory);
    await asar.createPackage(directory, file);
    return file;
  }
  const complete = await verifyHostArchive(await archive('complete'));
  assert.equal(complete.status, 'passed'); assert.equal(complete.checks.preferencesAndPreloadIncluded, true);
  await assert.rejects(verifyHostArchive(await archive('omitted', dir => fs.unlink(path.join(dir, 'preload.cjs')))), /exactly the reviewed files/);
  await assert.rejects(verifyHostArchive(await archive('unexpected', dir => fs.writeFile(path.join(dir, 'license.dat'), 'synthetic-only'))), /exactly the reviewed files/);
  await assert.rejects(verifyHostArchive(await archive('stale', dir => fs.appendFile(path.join(dir, 'main.cjs'), '\n// synthetic drift\n'))), /Packaged host drift/);
});
