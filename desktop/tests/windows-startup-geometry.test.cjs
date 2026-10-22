'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

test('actual native helper converts restored hidden-frame geometry at 96/144/192 DPI without losing raw evidence',
  { skip: process.platform !== 'win32' }, async t => {
    const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'FundsTrace synthetic window geometry '));
    t.after(() => fs.rm(directory, { recursive: true, force: true }));
    const script = path.join(directory, 'geometry-fixtures.ps1');
    await fs.writeFile(script, String.raw`param([Parameter(Mandatory = $true)][string]$Helper)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $errors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($Helper, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw 'HELPER_PARSE_FAILED' }
$source = [IO.File]::ReadAllText($Helper)
$native = [regex]::Match($source, "(?s)Add-Type @'\r?\n(.*?)\r?\n'@")
if (-not $native.Success) { throw 'NATIVE_SOURCE_MISSING' }
Add-Type -TypeDefinition $native.Groups[1].Value
function New-Rect([int[]]$Numbers) {
    $result = [FundsTraceStartupWindow+Rectangle]::new()
    $result.X = $Numbers[0]; $result.Y = $Numbers[1]
    $result.Width = $Numbers[2]; $result.Height = $Numbers[3]
    return $result
}
function Assert-Rect($Actual, [int[]]$Expected) {
    if ($null -eq $Actual -or $Actual.X -ne $Expected[0] -or $Actual.Y -ne $Expected[1] -or
        $Actual.Width -ne $Expected[2] -or $Actual.Height -ne $Expected[3]) { throw 'RECTANGLE_MISMATCH' }
}
# Independent physical-pixel examples, including the observed 96-DPI CI frame,
# two other DPI levels, negative monitor coordinates and below-minimum requests.
$fixtures = @(
    @{ dpi = 96; raw = @(24,32,976,672); client = @(32,32,960,664); area = @(0,0,1024,728);
       inset = 8; rawMin = @(976,648); electronMin = @(960,640); request = @(960,640); target = @(24,44,976,648) },
    @{ dpi = 144; raw = @(-2532,48,1464,1008); client = @(-2520,48,1440,996); area = @(-2880,0,2880,1620);
       inset = 12; rawMin = @(1464,972); electronMin = @(1440,960); request = @(1280,720); target = @(-2412,270,1944,1092) },
    @{ dpi = 192; raw = @(-2368,-1400,1952,1344); client = @(-2352,-1400,1920,1328); area = @(-2560,-1440,2560,1440);
       inset = 16; rawMin = @(1952,1296); electronMin = @(1920,1280); request = @(683,324); target = @(-1979,-1044,1398,664) },
    # A restored raw frame can equal its monitor while its inset client does not.
    @{ dpi = 96; raw = @(0,0,1920,1080); client = @(8,0,1904,1072); area = @(0,0,1920,1080);
       inset = 8; rawMin = @(976,648); electronMin = @(960,640); request = @(960,640); target = @(472,220,976,648) }
)
foreach ($fixture in $fixtures) {
    $evidence = [FundsTraceStartupWindow+Evidence]::new()
    $evidence.Window = New-Rect $fixture.raw; $evidence.Client = New-Rect $fixture.client
    $evidence.WorkArea = New-Rect $fixture.area; $evidence.Dpi = $fixture.dpi
    $evidence.MinTrackWidth = $fixture.rawMin[0]; $evidence.MinTrackHeight = $fixture.rawMin[1]
    [FundsTraceStartupWindow]::NormalizeRestoredGeometry($evidence, $true)
    if (-not $evidence.NormalizationAvailable -or $evidence.FrameInsets.Left -ne $fixture.inset -or
        $evidence.FrameInsets.Top -ne 0 -or $evidence.FrameInsets.Right -ne $fixture.inset -or
        $evidence.FrameInsets.Bottom -ne $fixture.inset) { throw 'MEASURED_INSETS_MISMATCH' }
    Assert-Rect $evidence.ElectronWindow $fixture.client
    if ($evidence.ElectronMinTrackWidth -ne $fixture.electronMin[0] -or
        $evidence.ElectronMinTrackHeight -ne $fixture.electronMin[1]) { throw 'MINIMUM_MISMATCH' }
    $target = [FundsTraceStartupWindow]::ResizeBounds($evidence, $fixture.request[0], $fixture.request[1])
    Assert-Rect $target $fixture.target
    if ($fixture.area[2] -eq 1024) {
        Assert-Rect ([FundsTraceStartupWindow]::ResizeBounds($evidence, 2000, 2000)) @(-8,0,1040,736)
    }
    Assert-Rect $evidence.Window $fixture.raw; Assert-Rect $evidence.Client $fixture.client
    if ($evidence.MinTrackWidth -ne $fixture.rawMin[0] -or $evidence.MinTrackHeight -ne $fixture.rawMin[1]) { throw 'RAW_MINIMUM_CHANGED' }
    [FundsTraceStartupWindow]::NormalizeRestoredGeometry($evidence, $false)
    if ($evidence.NormalizationAvailable -or $null -ne $evidence.ElectronWindow -or $null -ne $evidence.FrameInsets) { throw 'NON_RESTORED_NORMALIZED' }
    Assert-Rect $evidence.Window $fixture.raw; Assert-Rect $evidence.Client $fixture.client
    $rejected = $false
    try { [void][FundsTraceStartupWindow]::ResizeBounds($evidence, 960, 640) } catch { $rejected = $true }
    if (-not $rejected) { throw 'NON_RESTORED_RESIZE_ACCEPTED' }
}
$invalid = [FundsTraceStartupWindow+Evidence]::new()
$invalid.Window = New-Rect @(24,30,976,674); $invalid.Client = New-Rect @(32,32,960,664)
$invalid.MinTrackWidth = 976; $invalid.MinTrackHeight = 648
$rejected = $false
try { [FundsTraceStartupWindow]::NormalizeRestoredGeometry($invalid, $true) } catch { $rejected = $true }
if (-not $rejected -or $invalid.NormalizationAvailable) { throw 'UNEXPECTED_CAPTION_FRAME_ACCEPTED' }
Write-Output 'WINDOW_GEOMETRY_FIXTURES_PASSED'
`);
    const result = spawnSync('pwsh.exe', ['-NoLogo', '-NoProfile', '-NonInteractive', '-File', script,
      '-Helper', path.resolve(__dirname, '../../scripts/test-windows-startup-window.ps1')],
    { encoding: 'utf8', timeout: 30000, maxBuffer: 1024 * 1024, windowsHide: true });
    assert.equal(result.error, undefined);
    assert.equal(result.status, 0, String(result.stderr || result.stdout));
    assert.match(result.stdout, /WINDOW_GEOMETRY_FIXTURES_PASSED/);
  });
