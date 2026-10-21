'use strict';
// Read PE resources only. This helper never loads or modifies the executable.
const fs = require('node:fs/promises');
const crypto = require('node:crypto');
const { NtExecutable, NtExecutableResource, Resource } = require('resedit');

const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
function requireIcon(condition, code) {
  if (!condition) {
    const error = new Error(code);
    error.code = code;
    throw error;
  }
}
function frame(width, height, bitCount, bytes) {
  return { width: width || 256, height: height || 256, bitCount, bytes: bytes.length, sha256: digest(bytes) };
}
const frameKey = value => JSON.stringify([value.width, value.height, value.bitCount, value.bytes, value.sha256]);
const ordered = frames => frames.slice().sort((a, b) => frameKey(a).localeCompare(frameKey(b)));

function icoFrames(bytes) {
  requireIcon(bytes.length >= 6 && bytes.readUInt16LE(0) === 0 && bytes.readUInt16LE(2) === 1, 'ICON_ICO_INVALID');
  const count = bytes.readUInt16LE(4), directoryEnd = 6 + count * 16;
  requireIcon(count > 0 && directoryEnd <= bytes.length, 'ICON_ICO_INVALID');
  const result = [];
  for (let i = 0; i < count; i++) {
    const offset = 6 + i * 16;
    const size = bytes.readUInt32LE(offset + 8), start = bytes.readUInt32LE(offset + 12);
    requireIcon(bytes[offset + 3] === 0 && size > 0 && start >= directoryEnd && start + size <= bytes.length, 'ICON_ICO_INVALID');
    result.push(frame(bytes[offset], bytes[offset + 1], bytes.readUInt16LE(offset + 6), bytes.subarray(start, start + size)));
  }
  return ordered(result);
}

async function verifyExecutableIcon(executable, expectedIco) {
  requireIcon(typeof executable === 'string' && executable.length > 0 && typeof expectedIco === 'string' && expectedIco.length > 0, 'ICON_INPUT_INVALID');
  let executableBytes, icoBytes;
  try { [executableBytes, icoBytes] = await Promise.all([fs.readFile(executable), fs.readFile(expectedIco)]); }
  catch { requireIcon(false, 'ICON_INPUT_UNREADABLE'); }
  const expectedFrames = icoFrames(icoBytes);
  let entries;
  try {
    // Certificate validation is a separate gate; reading signed resources must
    // not require rewriting or stripping a certificate.
    entries = NtExecutableResource.from(NtExecutable.from(executableBytes, { ignoreCert: true })).entries;
  } catch { requireIcon(false, 'ICON_PE_INVALID'); }
  const groups = entries.filter(entry => entry.type === 14);
  requireIcon(groups.length > 0, 'ICON_GROUP_MISSING');
  // Shell/shortcut icon index 0 selects the first resource group. Checking any
  // matching group would allow a stale default icon plus a hidden approved one.
  const selected = groups.filter(entry => entry.id === groups[0].id);
  const languages = new Set();
  for (const entry of selected) {
    requireIcon(!languages.has(entry.lang), 'ICON_GROUP_INVALID');
    languages.add(entry.lang);
    const binary = Buffer.from(entry.bin);
    requireIcon(binary.length >= 6 && binary.readUInt16LE(0) === 0 && binary.readUInt16LE(2) === 1, 'ICON_GROUP_INVALID');
    const count = binary.readUInt16LE(4);
    requireIcon(count > 0 && binary.length === 6 + count * 14, 'ICON_GROUP_INVALID');
    const [group] = Resource.IconGroupEntry.fromEntries([entry]);
    requireIcon(group.icons.length === expectedFrames.length, 'ICON_FRAME_MISMATCH');
    const actual = group.icons.map((icon, index) => {
      requireIcon(binary[6 + index * 14 + 3] === 0 && icon.iconID > 0, 'ICON_GROUP_INVALID');
      const matches = entries.filter(resource => resource.type === 3 && resource.id === icon.iconID && resource.lang === entry.lang);
      requireIcon(matches.length === 1, 'ICON_RESOURCE_MISSING');
      const bytes = Buffer.from(matches[0].bin);
      requireIcon(bytes.length > 0 && bytes.length === icon.dataSize, 'ICON_RESOURCE_SIZE_MISMATCH');
      return frame(icon.width, icon.height, icon.bitCount, bytes);
    });
    requireIcon(ordered(actual).every((value, index) => frameKey(value) === frameKey(expectedFrames[index])), 'ICON_FRAME_MISMATCH');
  }
  return { schemaVersion: 1, status: 'passed', selection: 'default-icon-index-0',
    executableSha256: digest(executableBytes), expectedIcoSha256: digest(icoBytes),
    verifiedLanguageVariants: selected.length, frames: expectedFrames,
    limits: { renderedShellAppearance: false, windows11Interactive: false } };
}

module.exports = { verifyExecutableIcon };

if (require.main === module) {
  (async () => {
    requireIcon(process.argv.length === 4, 'ICON_ARGUMENTS_INVALID');
    process.stdout.write(JSON.stringify(await verifyExecutableIcon(process.argv[2], process.argv[3])) + '\n');
  })().catch(error => {
    const code = /^ICON_[A-Z_]+$/.test(error.code || '') ? error.code : 'ICON_VERIFICATION_FAILED';
    process.stdout.write(JSON.stringify({ schemaVersion: 1, status: 'failed', code }) + '\n');
    process.exitCode = 1;
  });
}
