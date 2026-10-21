'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { NtExecutable, NtExecutableResource, Data, Resource } = require('resedit');
const { verifyExecutableIcon } = require('./windows-icon-resources.cjs');

// Valid 32-bit DIB icons with independently chosen solid colours. The 256px
// entry exercises ICO's zero-byte dimension encoding without product assets.
function solidFrame(size, rgb) {
  const pixels = size * size * 4, mask = Math.ceil(size / 32) * 4 * size;
  const bytes = Buffer.alloc(40 + pixels + mask);
  bytes.writeUInt32LE(40, 0); bytes.writeInt32LE(size, 4); bytes.writeInt32LE(size * 2, 8);
  bytes.writeUInt16LE(1, 12); bytes.writeUInt16LE(32, 14); bytes.writeUInt32LE(pixels, 20);
  for (let offset = 40; offset < 40 + pixels; offset += 4) {
    bytes[offset] = rgb[2]; bytes[offset + 1] = rgb[1]; bytes[offset + 2] = rgb[0]; bytes[offset + 3] = 255;
  }
  return { size, bytes };
}
function ico(frames) {
  const directory = Buffer.alloc(6 + frames.length * 16);
  directory.writeUInt16LE(1, 2); directory.writeUInt16LE(frames.length, 4);
  let start = directory.length;
  frames.forEach(({ size, bytes }, index) => {
    const offset = 6 + index * 16;
    directory[offset] = directory[offset + 1] = size === 256 ? 0 : size;
    directory.writeUInt16LE(1, offset + 4); directory.writeUInt16LE(32, offset + 6);
    directory.writeUInt32LE(bytes.length, offset + 8); directory.writeUInt32LE(start, offset + 12);
    start += bytes.length;
  });
  return Buffer.concat([directory, ...frames.map(value => value.bytes)]);
}
const approved = () => [solidFrame(16, [19, 34, 53]), solidFrame(32, [190, 198, 208]), solidFrame(256, [224, 161, 45])];
function executable(groups, change) {
  const exe = NtExecutable.createEmpty(false, false), resources = NtExecutableResource.from(exe);
  for (const { id = 101, lang = 1033, frames } of groups) {
    Resource.IconGroupEntry.replaceIconsForResource(resources.entries, id, lang, Data.IconFile.from(ico(frames)).icons.map(value => value.data));
  }
  if (change) change(resources.entries);
  resources.outputResource(exe);
  return Buffer.from(exe.generate());
}
async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'FundsTrace synthetic PE icons '));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const expected = path.join(root, 'approved.ico');
  await fs.writeFile(expected, ico(approved()));
  async function write(name, bytes) { const file = path.join(root, name); await fs.writeFile(file, bytes); return file; }
  return { root, expected, write };
}

test('actual PE default icon matches every approved frame despite resource IDs, order and extra groups', async t => {
  const value = await fixture(t);
  const binary = executable([{ id: 7, frames: approved().reverse() }, { id: 9, frames: [solidFrame(16, [0, 0, 0])] },
    { id: 7, lang: 2052, frames: approved() }]);
  const file = await value.write('synthetic-app.exe', binary);
  const result = await verifyExecutableIcon(file, value.expected);
  assert.equal(result.status, 'passed'); assert.equal(result.verifiedLanguageVariants, 2);
  assert.deepEqual(result.frames.map(({ width, height }) => [width, height]).sort((a, b) => a[0] - b[0]), [[16, 16], [32, 32], [256, 256]]);
  assert.ok(result.frames.every(frame => frame.bitCount === 32));
  assert.deepEqual(await fs.readFile(file), binary, 'inspection must not modify the executable');
  assert.deepEqual(await fs.readFile(value.expected), ico(approved()), 'inspection must not modify the approved icon');
  assert.equal(JSON.stringify(result).includes(value.root), false);
});

test('approved PNG-backed source frames survive PE resource encoding without relaxed comparisons', async t => {
  const value = await fixture(t), expected = path.resolve(__dirname, '../assets/icon.ico');
  const icons = Data.IconFile.from(await fs.readFile(expected)).icons;
  assert.ok(icons.length > 0 && icons.every(icon => icon.data.isRaw()
    && Buffer.from(icon.data.bin).subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))));
  const exe = NtExecutable.createEmpty(false, false), resources = NtExecutableResource.from(exe);
  Resource.IconGroupEntry.replaceIconsForResource(resources.entries, 103, 1033, icons.map(icon => icon.data));
  resources.outputResource(exe);
  const file = await value.write('synthetic-png-resources.exe', Buffer.from(exe.generate()));
  const result = await verifyExecutableIcon(file, expected);
  assert.equal(result.status, 'passed'); assert.equal(result.frames.length, icons.length);
});

test('missing approved size and dangling RT_ICON references are rejected', async t => {
  const value = await fixture(t);
  const missingSize = await value.write('missing-size.exe', executable([{ frames: approved().slice(0, 2) }]));
  await assert.rejects(verifyExecutableIcon(missingSize, value.expected), { code: 'ICON_FRAME_MISMATCH' });
  const dangling = await value.write('missing-payload.exe', executable([{ frames: approved() }], entries => {
    entries.splice(entries.findIndex(entry => entry.type === 3), 1);
  }));
  await assert.rejects(verifyExecutableIcon(dangling, value.expected), { code: 'ICON_RESOURCE_MISSING' });
});

test('stale pixels and incorrect dimensions fail even when every icon size remains present', async t => {
  const value = await fixture(t), stale = approved();
  stale[1] = solidFrame(32, [255, 0, 255]);
  const staleFile = await value.write('stale.exe', executable([{ frames: stale }]));
  await assert.rejects(verifyExecutableIcon(staleFile, value.expected), { code: 'ICON_FRAME_MISMATCH' });
  const wrongSize = await value.write('wrong-dimensions.exe', executable([{ frames: approved() }], entries => {
    const group = entries.find(entry => entry.type === 14);
    new Uint8Array(group.bin)[6] = 24;
  }));
  await assert.rejects(verifyExecutableIcon(wrongSize, value.expected), { code: 'ICON_FRAME_MISMATCH' });
});

test('an approved secondary group cannot hide a stale default or language variant', async t => {
  const value = await fixture(t), stale = approved();
  stale[0] = solidFrame(16, [255, 0, 255]);
  for (const groups of [
    [{ id: 1, frames: stale }, { id: 2, frames: approved() }],
    [{ id: 1, frames: approved() }, { id: 1, lang: 2052, frames: stale }],
  ]) {
    const file = await value.write(`stale-${groups[1].lang || 'group'}.exe`, executable(groups));
    await assert.rejects(verifyExecutableIcon(file, value.expected), { code: 'ICON_FRAME_MISMATCH' });
  }
});

test('CLI returns safe JSON for app, installer, uninstaller and unreadable inputs', async t => {
  const value = await fixture(t);
  const script = path.join(__dirname, 'windows-icon-resources.cjs');
  for (const name of ['FundsTrace.exe', 'installer.exe', 'Uninstall FundsTrace.exe']) {
    const file = await value.write(name, executable([{ frames: approved() }]));
    const result = spawnSync(process.execPath, [script, file, value.expected], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr); assert.equal(result.stderr, '');
    assert.equal(JSON.parse(result.stdout).status, 'passed');
    assert.equal(result.stdout.includes(value.root), false);
  }
  const result = spawnSync(process.execPath, [script, path.join(value.root, 'unreadable-private-name.exe'), value.expected], { encoding: 'utf8' });
  assert.equal(result.status, 1); assert.equal(result.stderr, '');
  assert.deepEqual(JSON.parse(result.stdout), { schemaVersion: 1, status: 'failed', code: 'ICON_INPUT_UNREADABLE' });
  assert.equal(result.stdout.includes(value.root), false); assert.equal(result.stdout.includes('private-name'), false);
});
