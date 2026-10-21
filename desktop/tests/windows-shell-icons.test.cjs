'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
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

const windowsActions = process.platform === 'win32' && process.env.GITHUB_ACTIONS === 'true';
test('native shell shortcut icons match approved art and reject different pixels', {
  skip: windowsActions ? false : 'Windows GitHub Actions only', timeout: 90000,
}, async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'FundsTrace synthetic shell icons '));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const approved = path.resolve(__dirname, '../assets/icon.ico');
  const helper = await fs.readFile(path.resolve(__dirname, '../../scripts/test-windows-brand-icon.ps1'), 'utf8');
  const source = /^[\t ]*Add-Type[\t ]+@'\r?\n([\s\S]*?)\r?\n[\t ]*'@[\t ]*\r?$/m.exec(helper)?.[1];
  assert.ok(source, 'NATIVE_SHELL_SOURCE_UNAVAILABLE');
  const nativeSource = path.join(root, 'native-reader.cs'), wrong = path.join(root, 'wrong.ico');
  await fs.writeFile(nativeSource, source, { flag: 'wx' });
  await fs.writeFile(wrong, wrongIcon(), { flag: 'wx' });
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
  await fs.writeFile(script, `param([string]$Root, [string]$Approved, [string]$Wrong, [string]$NativeSource)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest
$Report = [ordered]@{ schemaVersion = 1; status = 'running'; failedCheck = $null; checks = [ordered]@{} }
$Stage = 'nativeSource'; $Failed = $false; $Shell = $null; $Link = $null; $Comparison = $null
try {
    Add-Type -Path $NativeSource
    $Stage = 'createShortcut'
    $Shell = New-Object -ComObject WScript.Shell
    $Link = $Shell.CreateShortcut((Join-Path $Root 'synthetic.lnk'))
    $Link.TargetPath = Join-Path $Root 'synthetic.exe'
    $Link.IconLocation = (Join-Path $Root 'synthetic.exe') + ',0'
    $Link.Save()
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
    }
} catch {
    $Failed = $true; $Report.failedCheck = $Stage
    $Report.failureCode = 'UNCLASSIFIED'
    $Codes = @('APPROVED_ICON_MISMATCH', 'WRONG_ICON_ACCEPTED', 'SHORTCUT_ICON_FAILED',
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
    }
} finally {
    try {
        if ($null -ne $Link) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Link) }
        if ($null -ne $Shell) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Shell) }
    } catch { $Failed = $true; $Report.failedCheck = 'comCleanup' }
    $Report.status = $(if ($Failed) { 'failed' } else { 'passed' })
    $Report | ConvertTo-Json -Depth 4 -Compress
}
if ($Failed) { exit 1 }
`, { flag: 'wx' });
  let stdout;
  try {
    ({ stdout } = await promisify(execFile)('pwsh.exe', ['-NoLogo', '-NoProfile', '-NonInteractive', '-File', script,
      '-Root', root, '-Approved', approved, '-Wrong', wrong, '-NativeSource', nativeSource], {
      timeout: 60000, maxBuffer: 128 * 1024, windowsHide: true,
    }));
  } catch (error) {
    // Never echo compiler/COM messages, source paths, command arguments or raw
    // stderr. Preserve only fixed stages and process outcome for CI diagnosis.
    let failedCheck = null, failureCode = null, comparison = null, reference = null;
    try {
      const value = JSON.parse(String(error.stdout || '').replace(/^\uFEFF/, ''));
      if (['nativeSource', 'createShortcut', 'approvedLarge', 'approvedSmall', 'wrongLarge', 'wrongSmall', 'comCleanup'].includes(value.failedCheck)) failedCheck = value.failedCheck;
      if (['UNCLASSIFIED', 'APPROVED_ICON_MISMATCH', 'WRONG_ICON_ACCEPTED', 'SHORTCUT_ICON_FAILED',
        'ICON_INFO_FAILED', 'ICON_BITMAP_FAILED', 'ICON_DIMENSIONS_INVALID', 'ICON_DC_FAILED', 'ICON_DIB_FAILED',
        'ICON_SELECT_FAILED', 'ICON_DRAW_FAILED', 'ICON_FLUSH_FAILED', 'EXPECTED_ICON_LOAD_FAILED'].includes(value.failureCode)) failureCode = value.failureCode;
      const safeFrame = frame => frame && Number.isInteger(frame.width) && frame.width > 0 && frame.width <= 1024
          && Number.isInteger(frame.height) && frame.height > 0 && frame.height <= 1024
          && typeof frame.matched === 'boolean' && /^[a-f0-9]{64}$/.test(frame.actual) && /^[a-f0-9]{64}$/.test(frame.expected)
        ? { width: frame.width, height: frame.height, matched: frame.matched, actual: frame.actual, expected: frame.expected } : null;
      comparison = safeFrame(value.comparison); reference = safeFrame(value.reference);
    } catch {}
    throw new Error(JSON.stringify({ code: error.killed ? 'NATIVE_SHELL_TIMEOUT' : 'NATIVE_SHELL_PROCESS_FAILED', failedCheck, failureCode, comparison, reference,
      exitCode: Number.isInteger(error.code) ? error.code : null,
      signal: ['SIGTERM', 'SIGKILL', 'SIGABRT'].includes(error.signal) ? error.signal : null }));
  }
  let result;
  try { result = JSON.parse(stdout.replace(/^\uFEFF/, '')); }
  catch { throw new Error('NATIVE_SHELL_JSON_INVALID'); }
  assert.deepEqual(result, { schemaVersion: 1, status: 'passed', failedCheck: null,
    checks: { approvedLarge: true, wrongLarge: true, approvedSmall: true, wrongSmall: true } });
});
