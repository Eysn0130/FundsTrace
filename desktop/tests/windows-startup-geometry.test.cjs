'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

test('actual native helper preserves frame geometry and strictly selects owned windows',
  { skip: process.platform !== 'win32' }, async t => {
    const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'FundsTrace synthetic window geometry '));
    t.after(() => fs.rm(directory, { recursive: true, force: true }));
    const script = path.join(directory, 'geometry-fixtures.ps1');
    await fs.writeFile(script, String.raw`param([Parameter(Mandatory = $true)][string]$Helper,
    [Parameter(Mandatory = $true)][string]$Installer)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Helper, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw 'HELPER_PARSE_FAILED' }
$source = [IO.File]::ReadAllText($Helper)
$native = [regex]::Match($source, "(?s)Add-Type @'\r?\n(.*?)\r?\n'@")
if (-not $native.Success) { throw 'NATIVE_SOURCE_MISSING' }
Add-Type -TypeDefinition $native.Groups[1].Value
# A same-rectangle maximize/minimize transition must still invalidate Read.
# Execute its exact state guard with independent before/after combinations.
foreach ($maximized in @($false,$true)) {
    foreach ($minimized in @($false,$true)) {
        $state = [FundsTraceStartupWindow+Evidence]::new()
        $state.Maximized = $maximized; $state.Minimized = $minimized
        [FundsTraceStartupWindow]::AssertStableWindowState($state,$maximized,$minimized)
        foreach ($after in @(@((-not $maximized),$minimized), @($maximized,(-not $minimized)))) {
            $rejected = $false
            try { [FundsTraceStartupWindow]::AssertStableWindowState($state,$after[0],$after[1]) }
            catch { $rejected = $_.Exception.GetBaseException().Message -ceq 'WINDOW_STATE_CHANGED' }
            if (-not $rejected) { throw 'WINDOW_STATE_RACE_ACCEPTED' }
        }
    }
}
# Execute the same strict hit guard used by the real Caption operation. Both
# the empty caption and the noninteractive preview badge must be HTCAPTION=2;
# the former menu's HTCLIENT=1 must never be accepted as an alternative.
$captionHits = [FundsTraceStartupWindow+CaptionEvidence]::new()
$captionHits.CaptionHit = 2; $captionHits.MenuHit = 2
[FundsTraceStartupWindow]::AssertCaptionHits($captionHits)
foreach ($hits in @(@(2,1), @(1,2), @(1,1), @(2,0), @(8,2))) {
    $captionHits.CaptionHit = $hits[0]; $captionHits.MenuHit = $hits[1]
    $rejected = $false
    try { [FundsTraceStartupWindow]::AssertCaptionHits($captionHits) }
    catch { $rejected = $_.Exception.GetBaseException().Message -ceq 'CAPTION_DRAG_PREVIEW_HIT_MISMATCH' }
    if (-not $rejected) { throw 'UNSAFE_CAPTION_HIT_ACCEPTED' }
}
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
# Synthetic handles exercise only the actual pure selection guard, never Win32
# ownership/input calls. Real selection enumerates and revalidates the bound PID.
$ownedPair = [IntPtr[]]@([IntPtr]101, [IntPtr]202)
if ([FundsTraceStartupWindow]::SelectOwnedCandidate([IntPtr[]]@([IntPtr]101), [IntPtr]::Zero, $false) -ne [IntPtr]101) { throw 'UNIQUE_SELECTION_MISMATCH' }
if ([FundsTraceStartupWindow]::SelectOwnedCandidate($ownedPair, [IntPtr]202, $true) -ne [IntPtr]202) { throw 'FOREGROUND_SELECTION_MISMATCH' }
if ([FundsTraceStartupWindow]::SelectOwnedCandidate([IntPtr[]]@([IntPtr]202,[IntPtr]101), [IntPtr]202, $true) -ne [IntPtr]202) { throw 'ENUMERATION_ORDER_SELECTED_WINDOW' }
if ([FundsTraceStartupWindow]::SelectOwnedCandidate($ownedPair, [IntPtr]101, $true) -eq [IntPtr]202) { throw 'FOREGROUND_CHANGE_IGNORED' }
$invalidSelections = @(
    @{ owned = [IntPtr[]]@(); foreground = [IntPtr]202; two = $true },
    @{ owned = [IntPtr[]]@([IntPtr]202); foreground = [IntPtr]202; two = $true },
    @{ owned = [IntPtr[]]@([IntPtr]101,[IntPtr]202,[IntPtr]303); foreground = [IntPtr]202; two = $true },
    @{ owned = $ownedPair; foreground = [IntPtr]202; two = $false },
    @{ owned = $ownedPair; foreground = [IntPtr]303; two = $true },
    @{ owned = $ownedPair; foreground = [IntPtr]::Zero; two = $true },
    @{ owned = [IntPtr[]]@([IntPtr]202,[IntPtr]202); foreground = [IntPtr]202; two = $true },
    @{ owned = [IntPtr[]]@([IntPtr]::Zero,[IntPtr]202); foreground = [IntPtr]202; two = $true }
)
foreach ($selection in $invalidSelections) {
    $rejected = $false
    try { [void][FundsTraceStartupWindow]::SelectOwnedCandidate($selection.owned, $selection.foreground, $selection.two) } catch { $rejected = $true }
    if (-not $rejected) { throw 'UNSAFE_WINDOW_SELECTION_ACCEPTED' }
}
# Execute the real outer catch in isolation: fixed native guard failures stay
# failed, only known input unavailability is downgraded, arbitrary text is omitted.
$outerTry = @($ast.EndBlock.Statements | Where-Object { $_ -is [System.Management.Automation.Language.TryStatementAst] })
if ($outerTry.Count -ne 1 -or $outerTry[0].CatchClauses.Count -ne 1) { throw 'OUTER_FAILURE_HANDLER_MISSING' }
$catchText = $outerTry[0].CatchClauses[0].Body.Extent.Text
$failureHandler = [scriptblock]::Create($catchText.Substring(1, $catchText.Length - 2))
$failures = @(
    @{ code = 'TWO_OWNED_WINDOWS_REQUIRED'; expected = 'OWNED_WINDOW_COUNT'; status = 'failed' },
    @{ code = 'OWNED_FOREGROUND_REQUIRED'; expected = 'FOREGROUND_BINDING'; status = 'failed' },
    @{ code = 'WINDOW_BINDING_CHANGED'; expected = 'FOREGROUND_BINDING'; status = 'failed' },
    @{ code = 'NATIVE_CAPTION_BUTTON_HIT_MISSING'; expected = 'HTCLOSE'; status = 'failed' },
    @{ code = 'CAPTION_STATE_TIMEOUT'; expected = 'STATE_TIMEOUT'; status = 'failed' },
    @{ code = 'INPUT_DESKTOP_UNAVAILABLE'; expected = 'INPUT_DESKTOP_UNAVAILABLE'; status = 'unavailable' },
    @{ code = 'synthetic unapproved exception /private-test-path 0x1234'; expected = 'NATIVE_CLOSE_FAILED'; status = 'failed' },
    @{ action = 'CaptionProbe'; code = 'CAPTION_DRAG_PREVIEW_HIT_MISMATCH'; expected = 'CAPTION_HIT'; status = 'failed' },
    @{ action = 'CaptionProbe'; code = 'CAPTION_DRAG_RESIZED_WINDOW'; expected = 'CAPTION_GEOMETRY'; status = 'failed' },
    @{ action = 'ZoomIn'; code = 'CAPTION_STATE_TIMEOUT'; expected = 'STATE_TIMEOUT'; status = 'failed' },
    @{ action = 'ZoomReset'; code = 'INPUT_DESKTOP_UNAVAILABLE'; expected = 'INPUT_DESKTOP_UNAVAILABLE'; status = 'unavailable' },
    @{ action = 'CaptionMaximize'; code = 'INPUT_DESKTOP_UNAVAILABLE'; expected = 'INPUT_DESKTOP_UNAVAILABLE'; status = 'unavailable' },
    @{ action = 'CaptionRestore'; code = 'INPUT_INJECTION_FAILED'; expected = 'INPUT_INJECTION_FAILED'; status = 'unavailable' },
    @{ action = 'CaptionMaximize'; code = 'CAPTION_UNEXPECTED_WINDOW_STATE'; expected = 'CAPTION_STATE'; status = 'failed' },
    @{ action = 'CaptionRestore'; code = 'WINDOW_STATE_CHANGED'; expected = 'CAPTION_STATE'; status = 'failed' },
    @{ action = 'CaptionProbe'; code = 'synthetic unapproved exception /private-test-path 0x1234'; expected = 'NATIVE_CAPTION_FAILED'; status = 'failed' }
)
foreach ($failure in $failures) {
    $Action = if ($failure.ContainsKey('action')) { $failure.action } else { 'CaptionCloseForeground' }
    $Stage = 'nativeCaptionInteraction'; $Report = [ordered]@{}; $ExitCode = 0
    try { throw $failure.code } catch { . $failureHandler }
    if ($Report.status -ne $failure.status -or $Report.failureCode -ne $failure.expected -or
        $Report.failedCheck -ne 'nativeCaptionInteraction') { throw 'FAILURE_CLASSIFICATION_MISMATCH' }
    if ($failure.status -eq 'unavailable') {
        if ($ExitCode -ne 2 -or $Report.reason -ne $failure.code) { throw 'INPUT_UNAVAILABILITY_LOST' }
    } elseif ($ExitCode -ne 1) { throw 'NON_INPUT_FAILURE_DOWNGRADED' }
    if (($Report | ConvertTo-Json -Compress).Contains('/private-test-path')) { throw 'UNSAFE_EXCEPTION_TEXT_REPORTED' }
}
# Run the installer's actual required-check block, without running an installer.
# The private update fixture remains exempt; ordinary installs must reject every
# omitted check, false result and the truthy JSON string "true".
$installerAst = [System.Management.Automation.Language.Parser]::ParseFile($Installer, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw 'INSTALLER_PARSE_FAILED' }
$gates = @($installerAst.FindAll({ param($node)
    $node -is [System.Management.Automation.Language.IfStatementAst] -and
    $node.Clauses[0].Item1.Extent.Text -ceq '-not $NextInstaller' -and
    $node.Extent.Text.Contains('installedMaximizedWindowRecovery')
}, $true))
if ($gates.Count -ne 1) { throw 'MAXIMIZED_INSTALLER_GATE_MISSING' }
$gate = [scriptblock]::Create($gates[0].Extent.Text)
$required = @('installedPresentationUserResize','installedPresentationMaximizedBeforeClose',
    'installedPresentationMaximizedAfterRelaunch','installedPresentationRestoredAfterRelaunch')
$NextInstaller = ''
$values = @{}; foreach ($name in $required) { $values[$name] = $true }
$AppHelper = [pscustomobject]@{ checks = [pscustomobject]$values }; $Report = @{ checks = @{} }
. $gate
if ($Report.checks.installedMaximizedWindowRecovery -cne 'passed') { throw 'MAXIMIZED_INSTALLER_GATE_NOT_RUN' }
foreach ($name in $required) {
    foreach ($invalid in @('missing','false','string')) {
        $values = @{}; foreach ($key in $required) { $values[$key] = $true }
        if ($invalid -eq 'missing') { $values.Remove($name) }
        elseif ($invalid -eq 'false') { $values[$name] = $false }
        else { $values[$name] = 'true' }
        $AppHelper = [pscustomobject]@{ checks = [pscustomobject]$values }; $Report = @{ checks = @{} }
        $rejected = $false
        try { . $gate } catch { $rejected = $true }
        if (-not $rejected -or $Report.checks.ContainsKey('installedMaximizedWindowRecovery')) { throw 'INVALID_MAXIMIZED_CHECK_ACCEPTED' }
    }
}
$NextInstaller = 'synthetic-private-update-fixture'
$AppHelper = [pscustomobject]@{ checks = [pscustomobject]@{} }; $Report = @{ checks = @{} }
. $gate
if ($Report.checks.ContainsKey('installedMaximizedWindowRecovery')) { throw 'PRIVATE_UPDATE_FIXTURE_GATE_CHANGED' }
Write-Output 'WINDOW_GEOMETRY_FIXTURES_PASSED'
`);
    const result = spawnSync('pwsh.exe', ['-NoLogo', '-NoProfile', '-NonInteractive', '-File', script,
      '-Helper', path.resolve(__dirname, '../../scripts/test-windows-startup-window.ps1'),
      '-Installer', path.resolve(__dirname, '../../scripts/test-windows-installer.ps1')],
    { encoding: 'utf8', timeout: 30000, maxBuffer: 1024 * 1024, windowsHide: true });
    assert.equal(result.error, undefined);
    assert.equal(result.status, 0, String(result.stderr || result.stdout));
    assert.match(result.stdout, /WINDOW_GEOMETRY_FIXTURES_PASSED/);
  });
