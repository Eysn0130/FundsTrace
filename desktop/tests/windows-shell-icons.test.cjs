'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { deflateSync } = require('node:zlib');
const { NtExecutable, NtExecutableResource, Data, Resource } = require('resedit');

function wrongIcon() {
  // Opaque magenta is deliberately unrelated to the approved navy/silver/amber
  // art. Both standard shell sizes are valid images, so rejection tests pixels.
  const ico = new Data.IconFile();
  for (const size of [16, 32]) {
    const pixelBytes = size * size * 4, maskBytes = Math.ceil(size / 32) * 4 * size;
    const dib = Buffer.alloc(40 + pixelBytes + maskBytes);
    dib.writeUInt32LE(40, 0); dib.writeInt32LE(size, 4); dib.writeInt32LE(size * 2, 8);
    dib.writeUInt16LE(1, 12); dib.writeUInt16LE(32, 14); dib.writeUInt32LE(pixelBytes, 20);
    for (let offset = 40; offset < 40 + pixelBytes; offset += 4) {
      dib[offset] = 255; dib[offset + 2] = 255; dib[offset + 3] = 255;
    }
    ico.icons.push({ data: Data.IconItem.from(size, size, dib) });
  }
  return Buffer.from(ico.generate());
}

function wrongPngIcon() {
  // Match the DIB negative's opaque magenta pixels using PNG-backed frames.
  // The shell comparison must reject this encoding as well as the DIB one.
  function chunk(type, data) {
    const result = Buffer.alloc(data.length + 12);
    result.writeUInt32BE(data.length, 0);
    result.write(type, 4, 4, 'ascii');
    data.copy(result, 8);
    let crc = 0xffffffff;
    for (const byte of result.subarray(4, result.length - 4)) {
      crc ^= byte;
      for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
    result.writeUInt32BE((crc ^ 0xffffffff) >>> 0, result.length - 4);
    return result;
  }
  const sizes = [16, 32];
  const frames = sizes.map(size => {
    const header = Buffer.alloc(13), stride = 1 + size * 4;
    header.writeUInt32BE(size, 0); header.writeUInt32BE(size, 4);
    header[8] = 8; header[9] = 6; // 8-bit RGBA, no palette or interlacing.
    const pixels = Buffer.alloc(stride * size); // Each row starts with filter 0.
    for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
      const offset = y * stride + 1 + x * 4;
      pixels[offset] = 255; pixels[offset + 2] = 255; pixels[offset + 3] = 255;
    }
    return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
      chunk('IHDR', header), chunk('IDAT', deflateSync(pixels, { level: 9 })), chunk('IEND', Buffer.alloc(0))]);
  });
  const directory = Buffer.alloc(6 + sizes.length * 16);
  directory.writeUInt16LE(1, 2); directory.writeUInt16LE(sizes.length, 4);
  let offset = directory.length;
  frames.forEach((frame, index) => {
    const entry = 6 + index * 16;
    directory[entry] = sizes[index]; directory[entry + 1] = sizes[index];
    directory.writeUInt16LE(1, entry + 4); directory.writeUInt16LE(32, entry + 6);
    directory.writeUInt32LE(frame.length, entry + 8); directory.writeUInt32LE(offset, entry + 12);
    offset += frame.length;
  });
  return Buffer.concat([directory, ...frames]);
}

const windowsActions = process.platform === 'win32' && process.env.GITHUB_ACTIONS === 'true';
test('native shell shortcut icons match approved art and reject different pixels', {
  skip: windowsActions ? false : 'Windows GitHub Actions only', timeout: 90000,
}, async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'FundsTrace 合成 快捷方式 '));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const approved = path.resolve(__dirname, '../assets/icon.ico');
  const helper = await fs.readFile(path.resolve(__dirname, '../../scripts/test-windows-brand-icon.ps1'), 'utf8');
  const source = /^[\t ]*Add-Type[\t ]+@'\r?\n([\s\S]*?)\r?\n[\t ]*'@[\t ]*\r?$/m.exec(helper)?.[1];
  assert.ok(source, 'NATIVE_SHELL_SOURCE_UNAVAILABLE');
  const nativeSource = path.join(root, 'native-reader.cs'), wrong = path.join(root, 'wrong.ico'), wrongPng = path.join(root, 'wrong-png.ico');
  // Add a Unicode fixture writer only to this temporary compilation. WSH's
  // shortcut property getter is lossy on the English Windows runner.
  const fixtureSource = source.replace('public static class FundsTraceBrandIcons', 'public static partial class FundsTraceBrandIcons') + `
public static partial class FundsTraceBrandIcons {
    public static void CreateSyntheticShortcut(string linkPath, string target) {
        object link = new ShellLinkObject();
        try {
            var wide = (IShellLinkW)link;
            System.Runtime.InteropServices.Marshal.ThrowExceptionForHR(wide.SetPath(target));
            System.Runtime.InteropServices.Marshal.ThrowExceptionForHR(wide.SetIconLocation(target, 0));
            ((System.Runtime.InteropServices.ComTypes.IPersistFile)link).Save(linkPath, true);
        } finally { System.Runtime.InteropServices.Marshal.FinalReleaseComObject(link); }
    }
}
`;
  await fs.writeFile(nativeSource, fixtureSource, { flag: 'wx' });
  await fs.writeFile(wrong, wrongIcon(), { flag: 'wx' });
  await fs.writeFile(wrongPng, wrongPngIcon(), { flag: 'wx' });
  // Preserve a real native PE's headers/sections. An empty resource-parser
  // fixture does not establish that the Windows shell accepts its PE headers.
  // Only the temporary copy is changed; it is never executed or installed.
  const exe = NtExecutable.from(await fs.readFile(process.execPath), { ignoreCert: true });
  const resources = NtExecutableResource.from(exe);
  for (let index = resources.entries.length - 1; index >= 0; index--) {
    if ([3, 14].includes(resources.entries[index].type)) resources.entries.splice(index, 1);
  }
  const icons = Data.IconFile.from(await fs.readFile(approved)).icons.map(icon => icon.data);
  Resource.IconGroupEntry.replaceIconsForResource(resources.entries, 103, 1033, icons);
  resources.outputResource(exe);
  await fs.writeFile(path.join(root, 'synthetic.exe'), Buffer.from(exe.generate()), { flag: 'wx' });
  const script = path.join(root, 'inspect.ps1');
  await fs.writeFile(script, `param([string]$Root, [string]$Approved, [string]$Wrong, [string]$WrongPng, [string]$NativeSource)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest
$Report = [ordered]@{ schemaVersion = 1; status = 'running'; failedCheck = $null; checks = [ordered]@{} }
$Stage = 'nativeSource'; $Failed = $false; $Comparison = $null
try {
    Add-Type -Path $NativeSource
    $Stage = 'fileIdentity'
    $Target = Join-Path $Root 'synthetic.exe'
    $Alias = Join-Path $Root 'same-file-alias.exe'
    $Copy = Join-Path $Root 'same-bytes-different-file.exe'
    New-Item -ItemType HardLink -Path $Alias -Target $Target | Out-Null
    [IO.File]::Copy($Target, $Copy)
    if (-not [FundsTraceBrandIcons]::SameFile($Alias, $Target) -or
        -not [FundsTraceBrandIcons]::SameFile($Target, $Target) -or
        [FundsTraceBrandIcons]::SameFile($Copy, $Target) -or
        [FundsTraceBrandIcons]::SameFile((Join-Path $Root 'missing.exe'), $Target)) { throw 'FILE_IDENTITY_MISMATCH' }
    $Report.checks.fileIdentity = $true
    $Stage = 'createShortcut'
    [FundsTraceBrandIcons]::CreateSyntheticShortcut((Join-Path $Root 'synthetic.lnk'), $Target)
    $Stage = 'unicodeShortcutBinding'
    $Binding = [FundsTraceBrandIcons]::ReadShortcut((Join-Path $Root 'synthetic.lnk'))
    if (-not [FundsTraceBrandIcons]::SameFile($Binding.TargetPath, $Target) -or
        -not [FundsTraceBrandIcons]::SameFile($Binding.IconPath, $Target) -or $Binding.IconIndex -ne 0 -or
        -not $Binding.TargetPath.Contains('合成')) { throw 'FILE_BINDING_MISMATCH' }
    $Report.checks.unicodeShortcutBinding = $true
    foreach ($Small in @($false, $true)) {
        $Size = $(if ($Small) { 'Small' } else { 'Large' })
        $Stage = 'approved' + $Size
        $Comparison = $null
        $Comparison = [FundsTraceBrandIcons]::Shortcut((Join-Path $Root 'synthetic.lnk'), $Approved, $Small)
        if (-not $Comparison.Matched -or $Comparison.Width -lt 1 -or $Comparison.Height -lt 1) { throw 'APPROVED_ICON_MISMATCH' }
        $Report.checks[$Stage] = $true
        $Stage = 'wrong' + $Size
        $Comparison = $null
        $Comparison = [FundsTraceBrandIcons]::Shortcut((Join-Path $Root 'synthetic.lnk'), $Wrong, $Small)
        if ($Comparison.Matched) { throw 'WRONG_ICON_ACCEPTED' }
        $Report.checks[$Stage] = $true
        $Stage = 'wrongPng' + $Size
        $Comparison = $null
        $Comparison = [FundsTraceBrandIcons]::Shortcut((Join-Path $Root 'synthetic.lnk'), $WrongPng, $Small)
        if ($Comparison.Matched) { throw 'WRONG_ICON_ACCEPTED' }
        $Report.checks[$Stage] = $true
    }
} catch {
    $Failed = $true; $Report.failedCheck = $Stage
    $Report.failureCode = 'UNCLASSIFIED'
    $Codes = @('FILE_IDENTITY_MISMATCH', 'FILE_BINDING_MISMATCH', 'APPROVED_ICON_MISMATCH', 'WRONG_ICON_ACCEPTED', 'SHORTCUT_ICON_FAILED',
        'ICON_INFO_FAILED', 'ICON_BITMAP_FAILED', 'ICON_DIMENSIONS_INVALID', 'ICON_DC_FAILED',
        'ICON_DIB_FAILED', 'ICON_SELECT_FAILED', 'ICON_DRAW_FAILED', 'ICON_FLUSH_FAILED', 'EXPECTED_ICON_LOAD_FAILED')
    $Cause = $_.Exception
    while ($null -ne $Cause) {
        if ($Cause.Message -cin $Codes) { $Report.failureCode = $Cause.Message; break }
        $Cause = $Cause.InnerException
    }
    if ($null -ne $Comparison) {
        $Report.comparison = [ordered]@{ width = $Comparison.Width; height = $Comparison.Height; matched = $Comparison.Matched
            actual = $Comparison.ActualSha256; expected = $Comparison.ExpectedSha256 }
        try {
            # Distinguish an invalid PE fixture from normalization of identical
            # art by the shell's image list. This is diagnostic only, never a pass.
            $Reference = [FundsTraceBrandIcons]::Shortcut($Approved, $Approved, $Small)
            $Report.reference = [ordered]@{ width = $Reference.Width; height = $Reference.Height; matched = $Reference.Matched
                actual = $Reference.ActualSha256; expected = $Reference.ExpectedSha256 }
        } catch { }
        try { $Report.preview = [Convert]::ToBase64String([FundsTraceBrandIcons]::ShortcutPixels((Join-Path $Root 'synthetic.lnk'), $Small)) }
        catch { }
    }
} finally {
    $Report.status = $(if ($Failed) { 'failed' } else { 'passed' })
    $Report | ConvertTo-Json -Depth 4 -Compress
}
if ($Failed) { exit 1 }
`, { flag: 'wx' });
  let stdout;
  try {
    ({ stdout } = await promisify(execFile)('pwsh.exe', ['-NoLogo', '-NoProfile', '-NonInteractive', '-File', script,
      '-Root', root, '-Approved', approved, '-Wrong', wrong, '-WrongPng', wrongPng, '-NativeSource', nativeSource], {
      timeout: 60000, maxBuffer: 128 * 1024, windowsHide: true,
    }));
  } catch (error) {
    // Never echo compiler/COM messages, source paths, command arguments or raw
    // stderr. Preserve only fixed stages and process outcome for CI diagnosis.
    let failedCheck = null, failureCode = null, comparison = null, reference = null, preview = null;
    try {
      const value = JSON.parse(String(error.stdout || '').replace(/^\uFEFF/, ''));
      if (['nativeSource', 'fileIdentity', 'createShortcut', 'unicodeShortcutBinding', 'approvedLarge', 'approvedSmall', 'wrongLarge', 'wrongSmall', 'wrongPngLarge', 'wrongPngSmall', 'comCleanup'].includes(value.failedCheck)) failedCheck = value.failedCheck;
      if (['UNCLASSIFIED', 'FILE_IDENTITY_MISMATCH', 'FILE_BINDING_MISMATCH', 'APPROVED_ICON_MISMATCH', 'WRONG_ICON_ACCEPTED', 'SHORTCUT_ICON_FAILED',
        'ICON_INFO_FAILED', 'ICON_BITMAP_FAILED', 'ICON_DIMENSIONS_INVALID', 'ICON_DC_FAILED', 'ICON_DIB_FAILED',
        'ICON_SELECT_FAILED', 'ICON_DRAW_FAILED', 'ICON_FLUSH_FAILED', 'EXPECTED_ICON_LOAD_FAILED'].includes(value.failureCode)) failureCode = value.failureCode;
      const safeFrame = frame => frame && Number.isInteger(frame.width) && frame.width > 0 && frame.width <= 1024
          && Number.isInteger(frame.height) && frame.height > 0 && frame.height <= 1024
          && typeof frame.matched === 'boolean' && /^[a-f0-9]{64}$/.test(frame.actual) && /^[a-f0-9]{64}$/.test(frame.expected)
        ? { width: frame.width, height: frame.height, matched: frame.matched, actual: frame.actual, expected: frame.expected } : null;
      comparison = safeFrame(value.comparison); reference = safeFrame(value.reference);
      if (typeof value.preview === 'string' && value.preview.length <= 33000 && /^[A-Za-z0-9+/]+={0,2}$/.test(value.preview)) {
        const bytes = Buffer.from(value.preview, 'base64');
        if (bytes.length >= 8 && bytes.readInt32LE(0) > 0 && bytes.readInt32LE(0) <= 64
          && bytes.readInt32LE(4) > 0 && bytes.readInt32LE(4) <= 64
          && bytes.length === 8 + bytes.readInt32LE(0) * bytes.readInt32LE(4) * 6) preview = value.preview;
      }
    } catch {}
    throw new Error(JSON.stringify({ code: error.killed ? 'NATIVE_SHELL_TIMEOUT' : 'NATIVE_SHELL_PROCESS_FAILED', failedCheck, failureCode, comparison, reference, preview,
      exitCode: Number.isInteger(error.code) ? error.code : null,
      signal: ['SIGTERM', 'SIGKILL', 'SIGABRT'].includes(error.signal) ? error.signal : null }));
  }
  let result;
  try { result = JSON.parse(stdout.replace(/^\uFEFF/, '')); }
  catch { throw new Error('NATIVE_SHELL_JSON_INVALID'); }
  assert.deepEqual(result, { schemaVersion: 1, status: 'passed', failedCheck: null,
    checks: { fileIdentity: true, unicodeShortcutBinding: true, approvedLarge: true, wrongLarge: true, wrongPngLarge: true, approvedSmall: true, wrongSmall: true, wrongPngSmall: true } });
});
