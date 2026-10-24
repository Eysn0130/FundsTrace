#Requires -Version 7.0
param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [Parameter(Mandatory = $true)][string]$ReportPath,
    [string]$CiTemporaryRoot,
    [string]$PriorVersion = '',
    [string]$NextInstaller,
    [string]$NextUnpacked,
    [string]$TrustedCertificateFixture,
    [switch]$PublishedInAppUpdate,
    [switch]$FreshInstallOnly,
    [switch]$PreflightOnly
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest
# This is a destructive test of a disposable hosted user's own installation only.
# Reject local/other-platform invocation before writing reports, downloading or inspecting user state.
if (-not [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::Windows) -or
    [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString() -ne 'X64' -or
    $env:GITHUB_ACTIONS -cne 'true') {
    [Console]::Error.WriteLine('Installer lifecycle requires native Windows x64 GitHub Actions.')
    exit 1
}

if ($FreshInstallOnly -and ($PublishedInAppUpdate -or $NextInstaller -or $NextUnpacked -or $TrustedCertificateFixture)) {
    throw 'Fresh installation cannot be combined with an update journey.'
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
# Existing appId com.eysn.fundstrace, UUIDv5 in electron-builder's unchanged namespace.
$InstallKey = 'Software\b56dd6f8-a8a1-5427-b970-3e6cc301a2c0'
$UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\b56dd6f8-a8a1-5427-b970-3e6cc301a2c0'
$SentinelContent = "FundsTrace synthetic installer lifecycle v1`n"
$Stage = 'inputPaths'
$script:NativeStep = $null
$Report = [ordered]@{
    schemaVersion = 1; status = 'running'; priorVersion = $null; currentVersion = $null; failedCheck = $null
    upgradeBaseline = $null; priorInstallerSha256 = $null; priorInstallerBytes = $null
    currentInstallerSha256 = $null; currentInstallerBytes = $null
    failureType = $null; failureId = $null; failedNativeStep = $null; nativeErrorCode = $null
    checks = [ordered]@{
        upgradeBaseline = 'not-run'; ciFreshUser = 'not-run'; priorInstallerDigest = 'not-run'; priorInstalled = 'not-run'
        syntheticDataSeeded = 'not-run'; priorCaseDataWrittenByInstalledBackend = 'not-run'
        presentationPreferencesSeeded = 'not-run'; obsoleteInstalledFileRemoved = 'not-run'
        upgradedSameIdentity = 'not-run'; installedResourceIntegrity = 'not-run'
        installedExecutableIcons = 'not-run'; installedShortcutIcons = 'not-run'
        upgradedPresentationPreferences = 'not-run'
        upgradedExistingCaseData = 'not-run'; legacyCasePackageCompatible = 'not-run'
        upgradedAnalysisAndAtomicCanvas = 'not-run'
        sameVersionReinstalled = 'not-run'; reinstalledResourceIntegrity = 'not-run'
        reinstalledDataFilesUnchanged = 'not-run'; reinstalledExistingCaseData = 'not-run'
        reinstalledPresentationPreferences = 'not-run'
        installedBackendWithoutActivation = 'not-run'; installedBackendAuthentication = 'not-run'
        syntheticCaseCreatedAndReopened = 'not-run'; eofShutdown = 'not-run'
        installedApplicationStarted = 'not-run'; installedApplicationSingleInstance = 'not-run'
        installedApplicationNormalClose = 'not-run'
        installedApplicationStartupClose = 'not-run'
        installedGraphJourney = 'not-run'
        installedImportRecovery = 'not-run'
        uninstallRegistrationRemoved = 'not-run'; installedExecutableRemoved = 'not-run'
        syntheticDataRetained = 'not-run'; presentationPreferencesRetained = 'not-run'; cleanup = 'not-run'
    }
    limits = [ordered]@{ gui = $false; importRecovery = $false }
}
if ($FreshInstallOnly) {
    $Report.mode = 'fresh'; $Report.fixtureOrigin = 'current-seeded'
    foreach ($Name in @('priorInstallerDigest', 'priorInstalled', 'obsoleteInstalledFileRemoved',
        'priorCaseDataWrittenByInstalledBackend', 'upgradedSameIdentity', 'upgradedPresentationPreferences',
        'upgradedExistingCaseData', 'legacyCasePackageCompatible', 'upgradedAnalysisAndAtomicCanvas')) {
        $Report.checks.Remove($Name)
    }
    foreach ($Name in @('freshInstalled', 'freshStartup', 'frozenBackendFormats',
        'currentCaseDataWrittenByInstalledBackend', 'currentPresentationPreferences', 'currentSeededCaseData',
        'currentPackageCompatible', 'currentAnalysisAndAtomicCanvas')) { $Report.checks[$Name] = 'not-run' }
}
# Capture only the first normal client's bounded preferences, and verify the same
# raw bytes immediately before the explicit five-key fixture merge.
$FreshPreferenceEvidenceCode = @'
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {createHash} = require('node:crypto');
const [mode, file, expectedJson] = process.argv.slice(1);
assert.ok(mode === 'capture' || mode === 'verify');
const stat = fs.lstatSync(file);
assert.ok(stat.isFile() && !stat.isSymbolicLink() && stat.size > 0 && stat.size <= 16384);
const raw = fs.readFileSync(file);
assert.equal(raw.length, stat.size);
const preferences = JSON.parse(raw.toString('utf8'));
assert.ok(preferences && typeof preferences === 'object' && !Array.isArray(preferences));
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const evidence = {base64:raw.toString('base64'), sha256:sha256(raw), bytes:raw.length};
if (mode === 'verify') {
  const expected = JSON.parse(expectedJson);
  assert.equal(typeof expected.base64, 'string');
  assert.ok(expected.base64.length > 0 && expected.base64.length <= 21848);
  assert.ok(typeof expected.sha256 === 'string' && /^[a-f0-9]{64}$/.test(expected.sha256));
  assert.ok(Number.isSafeInteger(expected.bytes) && expected.bytes > 0 && expected.bytes <= 16384);
  const decoded = Buffer.from(expected.base64, 'base64');
  assert.equal(decoded.toString('base64'), expected.base64);
  assert.equal(decoded.length, expected.bytes);
  assert.equal(sha256(decoded), expected.sha256);
  assert.deepEqual(raw, decoded);
}
process.stdout.write(JSON.stringify(evidence));
'@
$OwnUserData = $false
$OwnTemporary = $false
$MayHaveInstalled = $false
$ReportReady = $false
$Failed = $false
$TemporaryRoot = $null
$InstallRoot = $null
$UserRoot = $null
$script:TimedOutProcesses = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

function Get-Sha256([string]$File) {
    (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Get-InstallRecords {
    foreach ($View in @([Microsoft.Win32.RegistryView]::Registry64, [Microsoft.Win32.RegistryView]::Registry32)) {
        $Base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::CurrentUser, $View)
        $Install = $null; $Uninstall = $null
        try {
            $Install = $Base.OpenSubKey($InstallKey, $false)
            $Uninstall = $Base.OpenSubKey($UninstallKey, $false)
            [pscustomobject]@{
                HasInstall = $null -ne $Install; HasUninstall = $null -ne $Uninstall
                Location = $(if ($null -ne $Install) { [string]$Install.GetValue('InstallLocation', '') } else { '' })
                Version = $(if ($null -ne $Uninstall) { [string]$Uninstall.GetValue('DisplayVersion', '') } else { '' })
                Name = $(if ($null -ne $Uninstall) { [string]$Uninstall.GetValue('DisplayName', '') } else { '' })
            }
        } finally {
            if ($null -ne $Install) { $Install.Dispose() }
            if ($null -ne $Uninstall) { $Uninstall.Dispose() }
            $Base.Dispose()
        }
    }
}
function Assert-Installed([string]$Version) {
    $Found = $false
    foreach ($Record in @(Get-InstallRecords)) {
        if (-not $Record.HasInstall -and -not $Record.HasUninstall) { continue }
        if (-not $Record.HasInstall -or -not $Record.HasUninstall) { throw 'INSTALL_REGISTRY_PAIR_MISSING' }
        # electron-builder 26.15.3 defaults UNINSTALL_DISPLAY_NAME to productName + version.
        if ($Record.Name -cne "FundsTrace $Version") { throw 'INSTALL_DISPLAY_NAME_MISMATCH' }
        if ($Record.Version -cne $Version) { throw 'INSTALL_VERSION_MISMATCH' }
        if ([System.IO.Path]::GetFullPath($Record.Location).TrimEnd('\') -ine $InstallRoot.TrimEnd('\')) {
            throw 'INSTALL_LOCATION_MISMATCH'
        }
        $Found = $true
    }
    if (-not $Found -or -not (Test-Path -LiteralPath (Join-Path $InstallRoot 'FundsTrace.exe') -PathType Leaf)) {
        throw 'Installed application is missing.'
    }
}
function Test-Removed {
    $Registered = @(@(Get-InstallRecords) | Where-Object { $_.HasInstall -or $_.HasUninstall }).Count -gt 0
    return -not $Registered -and -not (Test-Path -LiteralPath (Join-Path $InstallRoot 'FundsTrace.exe'))
}
function Wait-Removed {
    $Deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        if (Test-Removed) { return }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $Deadline)
    throw 'Uninstall did not remove the application and registration.'
}
function Get-SafeIconFailure([string]$Text) {
    $Unavailable = [ordered]@{ kind = 'brand-icon'; status = 'diagnostic-unavailable' }
    if (-not $Text -or $Text.Length -gt 65536) { return $Unavailable }
    try { $Icon = ConvertFrom-Json -InputObject $Text -AsHashtable -Depth 12 -ErrorAction Stop }
    catch { return $Unavailable }
    $AllowedStages = @('ciPlatform', 'inputBinding', 'nativeIconReader', 'shortcutReader', 'desktopShortcut', 'startMenuShortcut')
    if ($Icon -isnot [Collections.IDictionary] -or
        ($Icon['schemaVersion'] -isnot [int] -and $Icon['schemaVersion'] -isnot [long]) -or
        $Icon['schemaVersion'] -ne 1 -or $Icon['status'] -isnot [string] -or $Icon['status'] -cne 'failed' -or
        $Icon['failedCheck'] -isnot [string] -or $AllowedStages -cnotcontains $Icon['failedCheck'] -or
        ($Icon.Contains('mode') -and ($Icon['mode'] -isnot [string] -or $Icon['mode'] -cne 'shortcuts'))) { return $Unavailable }
    $Safe = [ordered]@{
        kind = 'brand-icon'; schemaVersion = 1; status = 'failed'; failedCheck = [string]$Icon['failedCheck']
        failureCode = 'UNCLASSIFIED'; checks = [ordered]@{}; iconComparisons = @()
    }
    $AllowedCodes = @(
        'UNCLASSIFIED', 'INVALID_PATH', 'MISSING_CANDIDATE_INPUT', 'ICON_INFO_FAILED', 'ICON_BITMAP_FAILED',
        'ICON_DIMENSIONS_INVALID', 'ICON_DC_FAILED', 'ICON_DIB_FAILED', 'ICON_SELECT_FAILED', 'ICON_DRAW_FAILED',
        'ICON_FLUSH_FAILED', 'EXPECTED_ICON_LOAD_FAILED', 'SHORTCUT_ICON_FAILED', 'SHORTCUT_DIRECTORY_UNAVAILABLE',
        'SHORTCUT_MISSING', 'SHORTCUT_TARGET_MISMATCH', 'SHORTCUT_ICON_LOCATION_MISMATCH', 'SHORTCUT_ICON_PIXEL_MISMATCH'
    )
    if ($Icon.Contains('failureCode') -and $Icon['failureCode'] -is [string] -and $AllowedCodes -ccontains $Icon['failureCode']) {
        $Safe.failureCode = [string]$Icon['failureCode']
    }
    if ($Icon.Contains('checks') -and $Icon['checks'] -is [Collections.IDictionary]) {
        foreach ($Name in @('inputBinding', 'nativeIconReader', 'desktopShortcut', 'startMenuShortcut')) {
            if ($Icon['checks'].Contains($Name) -and $Icon['checks'][$Name] -is [string] -and
                @('not-run', 'passed', 'failed') -ccontains $Icon['checks'][$Name]) {
                $Safe.checks[$Name] = [string]$Icon['checks'][$Name]
            }
        }
    }
    if ($Icon.Contains('pathBindings') -and $Icon['pathBindings'] -is [Collections.IDictionary]) {
        $Bindings = [ordered]@{}
        foreach ($Name in @('desktopShortcut', 'startMenuShortcut')) {
            if (-not $Icon['pathBindings'].Contains($Name) -or
                $Icon['pathBindings'][$Name] -isnot [Collections.IDictionary]) { continue }
            $Binding = $Icon['pathBindings'][$Name]; $SafeBinding = [ordered]@{}; $Malformed = $false
            foreach ($Field in @('targetTextMatch', 'targetFileMatch', 'iconLocationTextMatch', 'iconLocationFileMatch')) {
                if ($Binding.Contains($Field)) {
                    if ($Binding[$Field] -isnot [bool]) { $Malformed = $true; break }
                    $SafeBinding[$Field] = [bool]$Binding[$Field]
                }
            }
            if (-not $Malformed -and $SafeBinding.Count -gt 0) { $Bindings[$Name] = $SafeBinding }
        }
        if ($Bindings.Count -gt 0) { $Safe.pathBindings = $Bindings }
    }
    $Sources = @('desktopShortcut/shell-small', 'desktopShortcut/shell-large', 'startMenuShortcut/shell-small', 'startMenuShortcut/shell-large')
    if ($Icon.Contains('iconComparisons') -and $Icon['iconComparisons'] -is [Array] -and $Icon['iconComparisons'].Count -le 4) {
        foreach ($Frame in $Icon['iconComparisons']) {
            if ($Frame -isnot [Collections.IDictionary] -or $Frame['Source'] -isnot [string] -or $Sources -cnotcontains $Frame['Source'] -or
                ($Frame['Width'] -isnot [int] -and $Frame['Width'] -isnot [long]) -or
                ($Frame['Height'] -isnot [int] -and $Frame['Height'] -isnot [long]) -or
                $Frame['Width'] -lt 1 -or $Frame['Width'] -gt 1024 -or $Frame['Height'] -lt 1 -or $Frame['Height'] -gt 1024 -or
                $Frame['Matched'] -isnot [bool] -or $Frame['ActualSha256'] -isnot [string] -or $Frame['ExpectedSha256'] -isnot [string] -or
                $Frame['ActualSha256'] -cnotmatch '\A[a-f0-9]{64}\z' -or $Frame['ExpectedSha256'] -cnotmatch '\A[a-f0-9]{64}\z') { continue }
            $Safe.iconComparisons += [ordered]@{
                Source = [string]$Frame['Source']; Width = [int]$Frame['Width']; Height = [int]$Frame['Height']; Matched = [bool]$Frame['Matched']
                ActualSha256 = [string]$Frame['ActualSha256']; ExpectedSha256 = [string]$Frame['ExpectedSha256']
            }
        }
    }
    return $Safe
}
function Invoke-Native {
    param([string]$FileName, [string[]]$Arguments = @(), [string]$RawArguments = '',
        [hashtable]$Environment = @{}, [int]$TimeoutSeconds = 180, [switch]$CaptureIconFailure)
    $script:NativeStep = 'configure'
    $script:NativeDiagnostic = $null
    $Start = [System.Diagnostics.ProcessStartInfo]::new()
    $Start.FileName = $FileName; $Start.WorkingDirectory = $ProjectRoot
    $Start.UseShellExecute = $false; $Start.CreateNoWindow = $true
    $Start.RedirectStandardOutput = $true; $Start.RedirectStandardError = $true
    if ($RawArguments) { $Start.Arguments = $RawArguments }
    else { foreach ($Argument in $Arguments) { $Start.ArgumentList.Add($Argument) } }
    $script:NativeStep = 'environment'
    foreach ($Name in @($Start.Environment.Keys)) {
        if ($Name.StartsWith('FUND_', [StringComparison]::OrdinalIgnoreCase) -or
            $Name.StartsWith('FUNDS_TRACE_', [StringComparison]::OrdinalIgnoreCase) -or
            $Name.StartsWith('PYTHON', [StringComparison]::OrdinalIgnoreCase)) { [void]$Start.Environment.Remove($Name) }
    }
    foreach ($Name in $Environment.Keys) { $Start.Environment[$Name] = [string]$Environment[$Name] }
    $Process = [System.Diagnostics.Process]::new(); $Process.StartInfo = $Start
    $TimedOut = $false
    try {
        $script:NativeStep = 'start'
        if (-not $Process.Start()) { throw 'Native process could not start.' }
        $Output = $Process.StandardOutput.ReadToEndAsync()
        $Errors = $Process.StandardError.ReadToEndAsync()
        $script:NativeStep = 'wait'
        if (-not $Process.WaitForExit($TimeoutSeconds * 1000)) {
            $TimedOut = $true
            $script:TimedOutProcesses.Add($Process)
            # Do not kill an installer or discover processes by executable name.
            [void]$Process.CloseMainWindow()
            throw 'Native process timed out.'
        }
        $Process.WaitForExit()
        $Text = $Output.GetAwaiter().GetResult()
        $ErrorText = $Errors.GetAwaiter().GetResult()
        $script:NativeStep = 'exit'
        if ($Process.ExitCode -ne 0) {
            if ($CaptureIconFailure) {
                # Child output is untrusted: retain only fixed icon schema fields,
                # never raw stdout/stderr, exception messages or filesystem paths.
                $script:NativeDiagnostic = Get-SafeIconFailure $Text
            } elseif ($ErrorText -cmatch '\AUpgrade data smoke failed at ([A-Za-z]{1,80}) line ([0-9]{1,5})\.\r?\n\z') {
                $script:NativeDiagnostic = "upgrade-data:$($Matches[1]):$($Matches[2])"
            }
            throw 'Native process failed.'
        }
        $script:NativeStep = $null
        return $Text
    } finally { if (-not $TimedOut) { $Process.Dispose() } }
}
function Invoke-Uninstall {
    $Uninstaller = Join-Path $InstallRoot 'Uninstall FundsTrace.exe'
    if (-not (Test-Path -LiteralPath $Uninstaller -PathType Leaf)) { throw 'Owned uninstaller is missing.' }
    # _?= keeps the real uninstaller in the waited process; NSIS requires this last and unquoted.
    [void](Invoke-Native -FileName $Uninstaller -RawArguments "/S _?=$InstallRoot" -TimeoutSeconds 180)
    Wait-Removed
}
function Assert-Sentinel {
    if ([System.IO.File]::ReadAllText((Join-Path $UserRoot 'installer-lifecycle-sentinel.txt')) -cne $SentinelContent) {
        throw 'Synthetic data sentinel changed.'
    }
}
function Assert-InstalledResources {
    foreach ($Relative in @('FundsTrace.exe', 'resources/app.asar', 'resources/backend/funds-trace-backend.exe',
        'resources/backend/build-manifest.json', 'resources/licenses/Analytix-Apache-2.0.txt')) {
        if ((Get-Sha256 (Join-Path $InstallRoot $Relative)) -cne (Get-Sha256 (Join-Path $CandidateRoot $Relative))) {
            throw 'Installed candidate content differs from win-unpacked.'
        }
    }
    $InstalledBackend = Join-Path $InstallRoot 'resources/backend'
    if ((Get-Sha256 (Join-Path $InstalledBackend 'build-manifest.json')) -cne (Get-Sha256 $NativeManifest)) {
        throw 'Installed backend manifest differs from the native build.'
    }
    $Manifest = Get-Content -Raw -LiteralPath $NativeManifest | ConvertFrom-Json
    if ($Manifest.protocol -ne 1 -or $Manifest.platform -cne 'win32' -or $Manifest.arch -cne 'x64') {
        throw 'Unexpected native manifest identity.'
    }
    $InternalRoot = [System.IO.Path]::GetFullPath((Join-Path $InstalledBackend '_internal')) + [System.IO.Path]::DirectorySeparatorChar
    foreach ($Resource in $Manifest.resources.PSObject.Properties) {
        $ResourcePath = [System.IO.Path]::GetFullPath((Join-Path $InternalRoot $Resource.Name))
        if (-not $ResourcePath.StartsWith($InternalRoot, [StringComparison]::OrdinalIgnoreCase) -or
            $Resource.Value -cnotmatch '^[0-9a-f]{64}$' -or (Get-Sha256 $ResourcePath) -cne $Resource.Value) {
            throw 'Installed resource differs from the native manifest.'
        }
    }
}
function Assert-PresentationPreferences {
    Assert-Sentinel
    if ((Get-Sha256 $PreferenceFile) -cne $PreferenceSha256) { throw 'Presentation preference bytes changed.' }
    # Read the old/new installed archive's actual store, without loading Main or
    # granting a test IPC. Normal NSIS uses the synthetic five-key fixture;
    # published updates retain the complete baseline generated by the old UI.
    $PreferenceReadCode = @'
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const { TextDecoder } = require('node:util');
const vm = require('node:vm');
const asar = require('./desktop/node_modules/@electron/asar');
const [archive, directory, expectedJson] = process.argv.slice(1);
function installed(name) {
  assert.ok(['preferences.cjs', 'host-policy.cjs'].includes(name));
  const bytes = asar.extractFile(archive, name), info = asar.statFile(archive, name);
  const sha256 = createHash('sha256').update(bytes).digest('hex');
  let source, utf8 = true;
  try { source = new TextDecoder('utf-8', { fatal: true }).decode(bytes); } catch { utf8 = false; }
  const integrity = !info.integrity || (info.integrity.algorithm === 'SHA256' && info.integrity.hash === sha256);
  const diagnostic = `name=${name}, bytes=${bytes.length}, sha256=${sha256}, utf8=${utf8}, integrity=${integrity}`;
  if (bytes.length !== info.size || !utf8 || !integrity || bytes.includes(0)) {
    throw new Error(`Invalid archived module: ${diagnostic}`);
  }
  const module = { exports: {} };
  const context = { module, exports: module.exports,
    require(request) {
      if (['node:fs', 'node:path', 'node:crypto'].includes(request)) return require(request);
      assert.equal(request, './host-policy.cjs'); return installed('host-policy.cjs');
    } };
  // Compile separately so a parse failure never prints source text into CI logs.
  let compiled;
  try { compiled = new vm.Script(source, { filename: name }); }
  catch { throw new Error(`Archived module syntax rejected: ${diagnostic}`); }
  compiled.runInNewContext(context, { timeout: 5000 });
  return module.exports;
}
const store = installed('preferences.cjs').preferenceStore(directory);
for (const [key, value] of Object.entries(JSON.parse(expectedJson))) assert.equal(store.getItem(key), value);
'@
    [void](Invoke-Native -FileName $NodeExe -Arguments @('-e', $PreferenceReadCode,
        (Join-Path $InstallRoot 'resources/app.asar'), $UserRoot, $PreferenceStoreJson) -TimeoutSeconds 15)
}

try {
    if (-not [System.IO.Path]::IsPathRooted($InstallerPath) -or -not [System.IO.Path]::IsPathRooted($ReportPath)) {
        throw 'Absolute candidate and report paths are required.'
    }
    $InstallerPath = [System.IO.Path]::GetFullPath($InstallerPath)
    $ReportPath = [System.IO.Path]::GetFullPath($ReportPath)
    $Stage = 'reportLocation'
    if (-not (Test-Path -LiteralPath (Split-Path -Parent $ReportPath) -PathType Container)) { throw 'Report directory is missing.' }
    $ReportReady = $true
    $Report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ReportPath -Encoding utf8
    $CandidateRoot = Join-Path $ProjectRoot 'build/desktop/electron/win-unpacked'
    $NativeManifest = Join-Path $ProjectRoot 'build/desktop/backend/funds-trace-backend/build-manifest.json'
    $PythonExe = Join-Path $ProjectRoot '.venv/Scripts/python.exe'
    $Stage = 'nodeLookup'
    $NodeExe = (Get-Command node.exe -CommandType Application | Select-Object -First 1).Path
    if (-not [System.IO.File]::Exists($NodeExe)) { throw 'Node executable is unavailable.' }
    $Stage = 'pythonLookup'
    if (-not [System.IO.File]::Exists($PythonExe)) { throw 'The isolated Python executable is unavailable.' }
    $Stage = 'workingDirectory'
    if (-not [System.IO.Directory]::Exists($ProjectRoot)) { throw 'Source working directory is unavailable.' }
    $Stage = 'candidateIdentity'
    $CurrentVersion = (Get-Content -Raw -LiteralPath (Join-Path $ProjectRoot 'desktop/package.json') | ConvertFrom-Json).version
    if ($CurrentVersion -cnotmatch '^\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?$') { throw 'INVALID_CANDIDATE_VERSION' }
    $Report.targetVersion = $CurrentVersion
    $Stage = 'upgradeBaseline'
    $Baseline = (Invoke-Native -FileName $PythonExe -Arguments @(
        (Join-Path $ProjectRoot 'scripts/upgrade_baseline.py'), '--baseline',
        (Join-Path $ProjectRoot 'desktop/upgrade-baseline.json'), '--package',
        (Join-Path $ProjectRoot 'desktop/package.json')
    ) -TimeoutSeconds 15) | ConvertFrom-Json
    if ($PriorVersion -and $PriorVersion -cne $Baseline.prior.version) { throw 'Requested prior version differs from the current upgrade baseline.' }
    $PriorVersion = $Baseline.prior.version
    $Report.upgradeBaseline = $Baseline
    $Report.checks.upgradeBaseline = 'passed'
    $Stage = 'candidateIdentity'
    $IdentityCode = "const b=require('./desktop/electron-builder.cjs');if(b.appId!=='com.eysn.fundstrace'||b.nsis.guid!==undefined)process.exit(1);"
    [void](Invoke-Native -FileName $NodeExe -Arguments @('-e', $IdentityCode) -TimeoutSeconds 15)
    $Stage = 'defaultAppData'
    $ApplicationData = [Environment]::GetFolderPath([Environment+SpecialFolder]::ApplicationData)
    if (-not $ApplicationData -or -not [System.IO.Path]::IsPathRooted($ApplicationData) -or
        -not $env:APPDATA -or [System.IO.Path]::GetFullPath($env:APPDATA) -ine [System.IO.Path]::GetFullPath($ApplicationData)) {
        throw 'Default user AppData is unavailable.'
    }
    $Stage = 'ciFreshUser'
    $UserRoot = Join-Path $ApplicationData 'FundsTrace'
    if ((Test-Path -LiteralPath $UserRoot) -or @(@(Get-InstallRecords) | Where-Object { $_.HasInstall -or $_.HasUninstall }).Count -gt 0) {
        throw 'The CI user already has FundsTrace state.'
    }
    $Report.checks.ciFreshUser = 'passed'
    if (-not $PreflightOnly) {
    $Stage = 'candidateInputs'
    foreach ($Required in @($InstallerPath, $NativeManifest, $PythonExe, (Join-Path $CandidateRoot 'FundsTrace.exe'))) {
        if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) { throw 'Required candidate input is missing.' }
    }
    $Report.currentInstallerSha256 = Get-Sha256 $InstallerPath
    $Report.currentInstallerBytes = (Get-Item -LiteralPath $InstallerPath).Length
    $Stage = 'createTemporary'
    $TemporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("FundsTrace 安装 生命周期 " + [guid]::NewGuid().ToString('N'))
    if ($CiTemporaryRoot) {
        $RequestedRoot = [System.IO.Path]::GetFullPath($CiTemporaryRoot)
        if (-not [System.IO.Path]::IsPathRooted($CiTemporaryRoot) -or
            (Split-Path -Parent $RequestedRoot).TrimEnd('\') -ine ([System.IO.Path]::GetTempPath()).TrimEnd('\') -or
            (Test-Path -LiteralPath $RequestedRoot)) { throw 'CI temporary root must be a new direct child of the test temp directory.' }
        $TemporaryRoot = $RequestedRoot
    }
    [void](New-Item -ItemType Directory -Path $TemporaryRoot)
    $OwnTemporary = $true
    $InstallRoot = Join-Path $TemporaryRoot '应用 安装路径'
    $PriorInstaller = Join-Path $TemporaryRoot 'prior-stable.exe'

    if ($FreshInstallOnly) {
        $Stage = 'freshInstalled'
        $MayHaveInstalled = $true
        [void](Invoke-Native -FileName $InstallerPath -RawArguments "/S /D=$InstallRoot" -TimeoutSeconds 240)
        Assert-Installed $CurrentVersion
        Assert-InstalledResources
        $Report.currentVersion = $CurrentVersion
        $Report.checks.freshInstalled = 'passed'
        # Establish ownership before the real first GUI, without creating data or preferences.
        if (Test-Path -LiteralPath $UserRoot) { throw 'Unexpected state appeared before first GUI.' }
        [void](New-Item -ItemType Directory -Path $UserRoot)
        $OwnUserData = $true
        [System.IO.File]::WriteAllText((Join-Path $UserRoot 'installer-lifecycle-sentinel.txt'), $SentinelContent)
        foreach ($Relative in @('data', 'preferences.json')) {
            if (Test-Path -LiteralPath (Join-Path $UserRoot $Relative)) { throw 'First GUI must start without database or preferences.' }
        }
        $Stage = 'freshStartup'
        $FreshStartupPath = Join-Path (Split-Path -Parent $ReportPath) 'fresh-startup-smoke.json'
        $FreshStartup = (Invoke-Native -FileName $NodeExe -Arguments @(
            (Join-Path $ProjectRoot 'desktop/tests/installed-app-smoke.cjs'), $InstallRoot,
            '--report', $FreshStartupPath, '--fresh-startup'
        ) -TimeoutSeconds 180) | ConvertFrom-Json
        if ($FreshStartup.status -cne 'passed' -or $FreshStartup.mode -cne 'fresh-startup') { throw 'First installed GUI startup failed.' }
        foreach ($Name in @('freshStateInitiallyAbsent', 'installedExecutableStarted', 'authenticatedWorkspaceLoaded',
            'defaultCaseEmpty', 'activationFree', 'noPageExceptions', 'normalClose', 'backendReleasedAfterClose', 'clientCreatedDefaultData')) {
            if ($FreshStartup.checks.$Name -isnot [bool] -or -not $FreshStartup.checks.$Name) { throw 'First installed GUI startup check is missing.' }
        }
        Assert-Sentinel
        $FirstPreferenceFile = Join-Path $UserRoot 'preferences.json'
        if (-not (Test-Path -LiteralPath (Join-Path $UserRoot 'data/fund.duckdb') -PathType Leaf) -or
            -not (Test-Path -LiteralPath $FirstPreferenceFile -PathType Leaf)) { throw 'Installed client did not create its own state.' }
        $FirstPreferenceEvidence = (Invoke-Native -FileName $NodeExe -Arguments @(
            '-e', $FreshPreferenceEvidenceCode, 'capture', $FirstPreferenceFile
        ) -TimeoutSeconds 15) | ConvertFrom-Json
        $Report.firstStartupPreferencesBase64 = $FirstPreferenceEvidence.base64
        $Report.firstStartupPreferencesSha256 = $FirstPreferenceEvidence.sha256
        $Report.firstStartupPreferencesBytes = $FirstPreferenceEvidence.bytes
        $Report.checks.freshStartup = 'passed'

        $Stage = 'frozenBackendFormats'
        $FormatFixture = Join-Path $TemporaryRoot 'format-fixtures'
        # The existing development lock supplies fixture-only xlwt; no runtime dependency or seed env.
        $FixtureEnvironment = @{ PYTHONUTF8 = '1'; PYTHONDONTWRITEBYTECODE = '1'; PYTHONNOUSERSITE = '1' }
        [void](Invoke-Native -FileName $PythonExe -Arguments @(
            (Join-Path $ProjectRoot 'scripts/windows_import_fixtures.py'), '--output-dir', $FormatFixture
        ) -Environment $FixtureEnvironment -TimeoutSeconds 60)
        $FormatReportPath = Join-Path (Split-Path -Parent $ReportPath) 'installed-formats-smoke.json'
        $Formats = (Invoke-Native -FileName $NodeExe -Arguments @(
            (Join-Path $ProjectRoot 'desktop/tests/installed-formats-smoke.cjs'),
            '--backend', (Join-Path $InstallRoot 'resources/backend'), '--fixtures', $FormatFixture,
            '--python', $PythonExe, '--report', $FormatReportPath
        ) -TimeoutSeconds 420) | ConvertFrom-Json
        if ($Formats.status -cne 'passed') { throw 'Installed frozen backend format checks failed.' }
        $Report.checks.frozenBackendFormats = 'passed'
    } else {
    $Stage = 'priorInstallerDigest'
    Invoke-WebRequest -Uri $Baseline.priorInstallerUrl -OutFile $PriorInstaller -TimeoutSec 180 -MaximumRedirection 5
    $Report.priorInstallerSha256 = Get-Sha256 $PriorInstaller
    $Report.priorInstallerBytes = (Get-Item -LiteralPath $PriorInstaller).Length
    if ($Report.priorInstallerSha256 -cne $Baseline.prior.sha256 -or $Report.priorInstallerBytes -ne $Baseline.prior.bytes) {
        throw 'The prior installer digest or byte count differs from the pinned release.'
    }
    $Report.checks.priorInstallerDigest = 'passed'
    $Stage = 'priorInstalled'
    $MayHaveInstalled = $true
    # /D is NSIS syntax: absolute path, last argument, deliberately no quotes even with spaces.
    [void](Invoke-Native -FileName $PriorInstaller -RawArguments "/S /D=$InstallRoot" -TimeoutSeconds 240)
    Assert-Installed $Baseline.prior.version
    $Report.priorVersion = $Baseline.prior.version; $Report.checks.priorInstalled = 'passed'
    $ObsoleteFile = Join-Path $InstallRoot 'fundstrace-upgrade-obsolete-sentinel.txt'
    if (Test-Path -LiteralPath $ObsoleteFile) { throw 'Unexpected obsolete test file already exists.' }
    [System.IO.File]::WriteAllText($ObsoleteFile, $SentinelContent)

    }

    $Stage = 'syntheticDataSeeded'
    # The earlier absence check establishes ownership; never redirect or reuse a maintainer's data.
    if (-not $FreshInstallOnly) {
        if (Test-Path -LiteralPath $UserRoot) { throw 'Unexpected data appeared before synthetic seeding.' }
        [void](New-Item -ItemType Directory -Path $UserRoot)
        $OwnUserData = $true
        [System.IO.File]::WriteAllText((Join-Path $UserRoot 'installer-lifecycle-sentinel.txt'), $SentinelContent)
    } else { Assert-Sentinel }
    $DataDirectory = Join-Path $UserRoot 'data'
    if (-not $FreshInstallOnly) { [void](New-Item -ItemType Directory -Path $DataDirectory) }
    $PythonEnvironment = @{ FUND_DATA_DIR = $DataDirectory; PYTHONUTF8 = '1'; PYTHONDONTWRITEBYTECODE = '1'; PYTHONNOUSERSITE = '1' }
    $UpgradeFixture = Join-Path $TemporaryRoot 'upgrade-fixture'
    $UpgradeHelper = Join-Path $ProjectRoot 'desktop/tests/upgrade-data-smoke.cjs'
    # .NET and Node can resolve different Windows temp roots. Bind both helpers to
    # the same root that this lifecycle has already checked and used for ownership.
    $CompatibilityTemp = [System.IO.Path]::GetTempPath()
    $CompatibilityEnvironment = @{ TEMP = $CompatibilityTemp; TMP = $CompatibilityTemp }
    $PriorData = (Invoke-Native -FileName $NodeExe -Arguments @(
        $UpgradeHelper, $InstallRoot, '--mode', 'seed', '--fixture', $UpgradeFixture
    ) -Environment $CompatibilityEnvironment -TimeoutSeconds 180) | ConvertFrom-Json
    foreach ($Name in @('priorBackendSeeded', 'priorExactMoneyAndCurrency', 'priorCanvasAndTracking', 'priorPackageExported', 'priorEofReopened')) {
        if ($PriorData.$Name -isnot [bool] -or -not $PriorData.$Name) { throw 'Old installed backend did not create the compatibility fixture.' }
    }
    if ($FreshInstallOnly) {
        $Report.currentSeededDataChecks = [ordered]@{
            currentBackendSeeded = $PriorData.priorBackendSeeded; currentExactMoneyAndCurrency = $PriorData.priorExactMoneyAndCurrency
            currentCanvasAndTracking = $PriorData.priorCanvasAndTracking; currentPackageExported = $PriorData.priorPackageExported
            currentEofReopened = $PriorData.priorEofReopened
        }
    } else { $Report.priorDataChecks = $PriorData }
    if (-not (Test-Path -LiteralPath (Join-Path $DataDirectory 'fund.duckdb') -PathType Leaf)) { throw 'Synthetic database was not created.' }
    $Report.checks.syntheticDataSeeded = 'passed'
    if ($FreshInstallOnly) { $Report.checks.currentCaseDataWrittenByInstalledBackend = 'passed' }
    else { $Report.checks.priorCaseDataWrittenByInstalledBackend = 'passed' }
    $Stage = 'presentationPreferencesSeeded'
    $PreferenceFile = Join-Path $UserRoot 'preferences.json'
    if (-not $FreshInstallOnly -and (Test-Path -LiteralPath $PreferenceFile)) { throw 'Unexpected preferences appeared before synthetic seeding.' }
    $PriorSnapshot = Get-Content -Raw -LiteralPath (Join-Path $UpgradeFixture 'snapshot.json') | ConvertFrom-Json
    $PreferenceJson = [ordered]@{ fund_case = [string]$PriorSnapshot.original.caseId; fund_edge_type = 'polyline'
        fund_show_ids = '1'; fund_show_photos = '0'; fund_edge_labels = '["money","time","summary","currency"]' } | ConvertTo-Json -Compress
    # Use the actual store's canonical form; the prior UI saves its current case
    # on startup, so a missing newline would create a false byte-retention failure.
    if ($FreshInstallOnly) {
        $FirstPreferenceExpected = @{ base64 = $Report.firstStartupPreferencesBase64
            sha256 = $Report.firstStartupPreferencesSha256; bytes = $Report.firstStartupPreferencesBytes } | ConvertTo-Json -Compress
        [void](Invoke-Native -FileName $NodeExe -Arguments @(
            '-e', $FreshPreferenceEvidenceCode, 'verify', $PreferenceFile, $FirstPreferenceExpected
        ) -TimeoutSeconds 15)
        $BeforeMerge = Get-Sha256 $PreferenceFile
        if ($BeforeMerge -cne $Report.firstStartupPreferencesSha256) { throw 'First GUI preferences changed before fixture merge.' }
        $ClientPreferences = Get-Content -Raw -LiteralPath $PreferenceFile | ConvertFrom-Json -AsHashtable
        if ($ClientPreferences -isnot [Collections.IDictionary]) { throw 'First GUI preferences must be an object.' }
        $UnchangedPreferences = @{}
        $FixturePreferences = $PreferenceJson | ConvertFrom-Json -AsHashtable
        foreach ($Key in $ClientPreferences.Keys) {
            if (-not $FixturePreferences.Contains($Key)) { $UnchangedPreferences[$Key] = $ClientPreferences[$Key] }
        }
        foreach ($Key in $FixturePreferences.Keys) { $ClientPreferences[$Key] = $FixturePreferences[$Key] }
        [System.IO.File]::WriteAllText($PreferenceFile, ($ClientPreferences | ConvertTo-Json -Depth 10 -Compress) + "`n")
        $MergedPreferences = Get-Content -Raw -LiteralPath $PreferenceFile | ConvertFrom-Json -AsHashtable
        foreach ($Key in $UnchangedPreferences.Keys) {
            if (($UnchangedPreferences[$Key] | ConvertTo-Json -Depth 10 -Compress) -cne
                ($MergedPreferences[$Key] | ConvertTo-Json -Depth 10 -Compress)) { throw 'Fixture merge changed a client-owned preference.' }
        }
        $Report.presentationPreferenceFixture = [ordered]@{ beforeSha256 = $BeforeMerge
            afterSha256 = (Get-Sha256 $PreferenceFile); mergedKeys = @($FixturePreferences.Keys | Sort-Object)
            otherClientKeysPreserved = $true }
    } else { [System.IO.File]::WriteAllText($PreferenceFile, $PreferenceJson + "`n") }
    $PreferenceStoreJson = $PreferenceJson
    $PreferenceSha256 = Get-Sha256 $PreferenceFile
    Assert-PresentationPreferences
    $Report.checks.presentationPreferencesSeeded = 'passed'

    if (-not $FreshInstallOnly) {
    $Stage = 'upgradedSameIdentity'
    if ($PublishedInAppUpdate) {
        if ($NextInstaller) { throw 'Published and private update modes cannot be combined.' }
        $Stage = 'priorUiPresentationBaseline'
        $PriorPresentationConfig = Join-Path $TemporaryRoot 'prior-presentation-baseline.json'
        @{ version=$Baseline.prior.version; preferences=($PreferenceJson | ConvertFrom-Json) } |
            ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $PriorPresentationConfig -Encoding utf8
        $PriorPresentationReport = Join-Path (Split-Path -Parent $ReportPath) 'native-prior-presentation-baseline.json'
        $PriorPresentation = (Invoke-Native -FileName $NodeExe -Arguments @(
            (Join-Path $ProjectRoot 'desktop/tests/installed-app-smoke.cjs'), $InstallRoot,
            '--report', $PriorPresentationReport, '--presentation-baseline', $PriorPresentationConfig
        ) -TimeoutSeconds 180) | ConvertFrom-Json
        foreach ($Name in @('priorPresentationStartupCompleted', 'priorPresentationPreferencesBefore',
            'priorPresentationNativeUserResize', 'priorPresentationWindowPersisted', 'priorPresentationNativeCaptionClose',
            'priorPresentationCleanExit', 'priorPresentationBackendReleased', 'priorPresentationBaselineBytes',
            'priorPresentationNoPageExceptions')) {
            if ($PriorPresentation.checks.$Name -isnot [bool] -or -not $PriorPresentation.checks.$Name) {
                throw 'The prior UI did not establish its presentation baseline.'
            }
        }
        $PriorPresentationBaseline = $PriorPresentation.presentationBaseline
        if ($PriorPresentation.status -cne 'passed' -or
            $PriorPresentationBaseline.priorVersion -cne $Baseline.prior.version -or
            $PriorPresentationBaseline.originalSha256 -cne $PreferenceSha256 -or
            $PriorPresentationBaseline.baselineSha256 -cnotmatch '^[a-f0-9]{64}$' -or
            $PriorPresentationBaseline.windowStateRendererReadable -isnot [bool] -or
            $PriorPresentationBaseline.windowStateRendererReadable) {
            throw 'The prior UI presentation baseline has an invalid identity.'
        }
        foreach ($Name in @('originalPreferencesUnchanged', 'onlyExpectedKeys', 'windowStateMatchesNative',
            'nativeUserResizeObserved', 'nativeCaptionCloseObserved', 'processExitedZero', 'backendReleased',
            'closePreservedPreferenceBytes')) {
            if ($PriorPresentationBaseline.$Name -isnot [bool] -or -not $PriorPresentationBaseline.$Name) {
                throw 'The prior UI presentation baseline was not fully verified.'
            }
        }
        if ((Get-Sha256 $PreferenceFile) -cne $PriorPresentationBaseline.baselineSha256) {
            throw 'Prior presentation bytes changed after the old client closed.'
        }
        # Only the real old client's native resize/close established this full
        # file. Freeze its SHA before upgrading, then retain exact byte checks.
        # Renderer expectations stay at the original five keys: window state is
        # Main-owned and must remain unreadable through the preference IPC.
        $PreferenceSha256 = $PriorPresentationBaseline.baselineSha256
        $PreferenceStoreJson = [System.IO.File]::ReadAllText($PreferenceFile)
        Assert-PresentationPreferences
        $Report.priorUiPresentationBaseline = $PriorPresentationBaseline
        $Report.checks.priorUiPresentationBaseline = 'passed'
        $Stage = 'upgradedSameIdentity'
        $UpdateConfig = Join-Path $TemporaryRoot 'production-update.json'
        $UpdateSettings = Get-Content -Raw -LiteralPath (Join-Path $InstallRoot 'resources/app-update.yml')
        if ($UpdateSettings -cnotmatch '(?m)^provider: generic\s*$' -or
            $UpdateSettings -cnotmatch '(?m)^url: https://analytix\.top/fundstrace/\s*$') {
            throw 'The installed prior release does not use the fixed official update source.'
        }
        $Report.checks.productionUpdateOfficialFeedConfigured = 'passed'
        $CacheMatch = [regex]::Match($UpdateSettings, '(?m)^updaterCacheDirName: ([a-z0-9-]+)\s*$')
        if (-not $CacheMatch.Success) { throw 'Installed updater cache identity is missing.' }
        $Sha512 = [Convert]::ToBase64String([Security.Cryptography.SHA512]::HashData([IO.File]::ReadAllBytes($InstallerPath)))
        @{ currentVersion=$Baseline.prior.version; version=$CurrentVersion; installerSha256=$Report.currentInstallerSha256
            installerSha512=$Sha512; installerBytes=$Report.currentInstallerBytes; preferences=($PreferenceJson | ConvertFrom-Json)
            cacheDirectoryName=$CacheMatch.Groups[1].Value } |
            ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $UpdateConfig -Encoding utf8
        $UpdateReportPath = Join-Path (Split-Path -Parent $ReportPath) 'native-production-update.json'
        $UpdateScreenshot = Join-Path (Split-Path -Parent $ReportPath) 'native-production-update.png'
        $UpdateJson = Invoke-Native -FileName $NodeExe -Arguments @(
            (Join-Path $ProjectRoot 'desktop/tests/installed-app-smoke.cjs'), $InstallRoot,
            '--report', $UpdateReportPath, '--screenshot', $UpdateScreenshot, '--production-update', $UpdateConfig
        ) -TimeoutSeconds 720
        $UpdateReport = $UpdateJson | ConvertFrom-Json
        if ($UpdateReport.status -cne 'passed' -or $UpdateReport.updateCycle.currentVersion -cne $Baseline.prior.version -or
            $UpdateReport.updateCycle.nextVersion -cne $CurrentVersion) { throw 'Published update journey did not pass.' }
        $Stage = 'productionUpdateAutoRelaunch'
        $QueryPath = (Join-Path $InstallRoot 'FundsTrace.exe').Replace('\','\\').Replace("'","\'")
        $Relaunched = $null
        $Deadline = [DateTime]::UtcNow.AddSeconds(90)
        do {
            foreach ($Owned in @(Get-CimInstance Win32_Process -Filter "ExecutablePath='$QueryPath'")) {
                $Process = Get-Process -Id $Owned.ProcessId -ErrorAction SilentlyContinue
                if ($null -ne $Process -and $Process.MainWindowHandle -ne [IntPtr]::Zero -and
                    $Process.MainWindowTitle -ceq '循迹 FundsTrace' -and
                    [Diagnostics.FileVersionInfo]::GetVersionInfo((Join-Path $InstallRoot 'FundsTrace.exe')).ProductVersion -like "$CurrentVersion*") {
                    Assert-Installed $CurrentVersion
                    $Relaunched = $Process; break
                }
            }
            if ($null -eq $Relaunched) { Start-Sleep -Milliseconds 200 }
        } while ($null -eq $Relaunched -and [DateTime]::UtcNow -lt $Deadline)
        if ($null -eq $Relaunched) { throw 'Published update did not automatically reopen the new version.' }
        $Report.checks.productionUpdateAutoRelaunch = 'passed'
        $Stage = 'productionUpdateRelaunchClose'
        Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class FundsTraceUpdateWindowState {
  [StructLayout(LayoutKind.Sequential)] struct Rect { public int Left, Top, Right, Bottom; }
  delegate bool EnumWindowProc(IntPtr window, IntPtr state);
  [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr window);
  [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr window);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr window);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindowProc callback, IntPtr state);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr window, out Rect rectangle);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr window, StringBuilder name, int maximum);
  [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
  public static bool OwnedWindow(IntPtr window, uint processId) {
    uint actual;
    return IsWindow(window) && GetWindowThreadProcessId(window, out actual) != 0 && actual == processId;
  }
  public static int VisibleWindowCount(uint processId) {
    int count = 0;
    EnumWindowProc callback = (window, state) => {
      if (OwnedWindow(window, processId) && IsWindowVisible(window)) count++;
      return true;
    };
    if (!EnumWindows(callback, IntPtr.Zero)) throw new InvalidOperationException("WINDOW_ENUMERATION_FAILED");
    return count;
  }
  public static string Kind(IntPtr window) {
    var name = new StringBuilder(256);
    if (GetClassName(window, name, name.Capacity) == 0) return "unavailable";
    var value = name.ToString();
    return value == "Chrome_WidgetWin_1" || value == "Chrome_WidgetWin_0" || value == "#32770" ? value : "other";
  }
  public static int[] CaptureBounds(IntPtr window, uint processId) {
    Rect rectangle;
    if (!OwnedWindow(window, processId) || !IsWindowVisible(window) || !GetWindowRect(window, out rectangle))
      throw new InvalidOperationException("BOUND_WINDOW_UNAVAILABLE");
    int width = rectangle.Right - rectangle.Left, height = rectangle.Bottom - rectangle.Top;
    if (width <= 0 || height <= 0 || width > 8192 || height > 8192 || (long)width * height > 16777216)
      throw new InvalidOperationException("BOUND_WINDOW_GEOMETRY_INVALID");
    return new[] { rectangle.Left, rectangle.Top, width, height };
  }
}
'@
        $Relaunched.Refresh()
        # Keep the original HWND for read-only failure observations. Handles,
        # IDs, titles and paths never enter the report.
        $CloseWindow = $Relaunched.MainWindowHandle
        $SameWindowsSession = $null
        try { $SameWindowsSession = [Diagnostics.Process]::GetCurrentProcess().SessionId -eq $Relaunched.SessionId }
        catch { <# Session metadata is diagnostic only. #> }
        $Report.productionUpdateClose = [ordered]@{ windowPresent=($Relaunched.MainWindowHandle -ne [IntPtr]::Zero)
            workspaceTitle=($Relaunched.MainWindowTitle -ceq '循迹 FundsTrace')
            windowEnabled=[FundsTraceUpdateWindowState]::IsWindowEnabled($Relaunched.MainWindowHandle)
            windowVisible=[FundsTraceUpdateWindowState]::IsWindowVisible($Relaunched.MainWindowHandle)
            sameWindowsSession=$SameWindowsSession
            responding=$Relaunched.Responding; requestSent=$false; exited=$false }
        $Report.productionUpdateClose.requestSent = $Relaunched.CloseMainWindow()
        if (-not $Report.productionUpdateClose.requestSent) { throw 'Updated application did not accept a normal close request.' }
        $Report.productionUpdateClose.exited = $Relaunched.WaitForExit(30000)
        if (-not $Report.productionUpdateClose.exited) {
            $AfterClose = [ordered]@{ windowQuerySucceeded=$false; processAlive=$null
                originalWindowExists=$null; originalWindowProcessBound=$null; visibleTopLevelWindowCount=$null
                originalWindowVisible=$null; originalWindowEnabled=$null; currentWindowIsOriginal=$null; currentWindowKind=$null
                mainWindowPresent=$null; workspaceTitle=$null; windowEnabled=$null; windowVisible=$null; responding=$null
                backendQuerySucceeded=$false; directOwnedBackendCount=$null }
            $Report.productionUpdateClose.afterTimeout = $AfterClose
            try {
                $Relaunched.Refresh()
                $AfterClose.processAlive = -not $Relaunched.HasExited
                $AfterClose.originalWindowExists = [FundsTraceUpdateWindowState]::IsWindow($CloseWindow)
                $AfterClose.originalWindowProcessBound = [FundsTraceUpdateWindowState]::OwnedWindow($CloseWindow, [uint32]$Relaunched.Id)
                $AfterClose.originalWindowVisible = [FundsTraceUpdateWindowState]::IsWindowVisible($CloseWindow)
                $AfterClose.originalWindowEnabled = [FundsTraceUpdateWindowState]::IsWindowEnabled($CloseWindow)
                $AfterClose.visibleTopLevelWindowCount = [FundsTraceUpdateWindowState]::VisibleWindowCount([uint32]$Relaunched.Id)
                if ($AfterClose.processAlive) {
                    $CurrentWindow = $Relaunched.MainWindowHandle
                    $AfterClose.currentWindowIsOriginal = $CurrentWindow -eq $CloseWindow
                    $AfterClose.currentWindowKind = [FundsTraceUpdateWindowState]::Kind($CurrentWindow)
                    $AfterClose.mainWindowPresent = $CurrentWindow -ne [IntPtr]::Zero
                    if ($AfterClose.mainWindowPresent) {
                        $AfterClose.workspaceTitle = $Relaunched.MainWindowTitle -ceq '循迹 FundsTrace'
                        $AfterClose.windowEnabled = [FundsTraceUpdateWindowState]::IsWindowEnabled($CurrentWindow)
                        $AfterClose.windowVisible = [FundsTraceUpdateWindowState]::IsWindowVisible($CurrentWindow)
                        $AfterClose.responding = $Relaunched.Responding
                    }
                }
                $AfterClose.windowQuerySucceeded = $true
            } catch { <# Missing observations remain null; never substitute a successful close. #> }
            try {
                $BackendQueryPath = (Join-Path $InstallRoot 'resources/backend/funds-trace-backend.exe').Replace('\','\\').Replace("'","\'")
                $AfterClose.directOwnedBackendCount = @(Get-CimInstance Win32_Process -Filter (
                    "ParentProcessId=$($Relaunched.Id) AND ExecutablePath='$BackendQueryPath'") -ErrorAction Stop).Count
                $AfterClose.backendQuerySucceeded = $true
            } catch { <# An unavailable query is not evidence that the owned backend exited. #> }
            $Capture = [ordered]@{ attempted=$true; captured=$false; processBound=$null; failureCode='CAPTURE_NOT_COMPLETED'
                source='bound-owned-visible-window-composite'; scope='synthetic-CI-profile-only; not-DPI-acceptance' }
            $Report.productionUpdateClose.capture = $Capture
            $CaptureBitmap = $null; $CaptureGraphics = $null; $CaptureDpi = [IntPtr]::Zero
            try {
                # Caption acceptance requires normal window geometry. This
                # bound capture also observes an owned post-close modal.
                $CapturePath = Join-Path (Split-Path -Parent $ReportPath) 'native-production-update.update-relaunch-close-timeout.png'
                if (Test-Path -LiteralPath $CapturePath) { throw 'Diagnostic capture must be new.' }
                $Relaunched.Refresh()
                $CaptureDpi = [FundsTraceUpdateWindowState]::SetThreadDpiAwarenessContext([IntPtr](-4))
                $CaptureBounds = [FundsTraceUpdateWindowState]::CaptureBounds($Relaunched.MainWindowHandle, [uint32]$Relaunched.Id)
                $Capture.processBound = $true
                Add-Type -AssemblyName System.Drawing
                $CaptureBitmap = [Drawing.Bitmap]::new($CaptureBounds[2], $CaptureBounds[3])
                $CaptureGraphics = [Drawing.Graphics]::FromImage($CaptureBitmap)
                $CaptureGraphics.CopyFromScreen($CaptureBounds[0], $CaptureBounds[1], 0, 0, $CaptureBitmap.Size)
                $CaptureBitmap.Save($CapturePath, [Drawing.Imaging.ImageFormat]::Png)
                $Capture.captured = $true; $Capture.failureCode = $null
            } catch { $Capture.failureCode = 'BOUND_WINDOW_CAPTURE_FAILED' }
            finally {
                if ($CaptureGraphics) { $CaptureGraphics.Dispose() }
                if ($CaptureBitmap) { $CaptureBitmap.Dispose() }
                if ($CaptureDpi -ne [IntPtr]::Zero) { [void][FundsTraceUpdateWindowState]::SetThreadDpiAwarenessContext($CaptureDpi) }
            }
            throw 'Updated application did not finish its normal close.'
        }
        $Report.checks.productionUpdateRelaunchClose = 'passed'
    } else {
        # Let NSIS find the existing custom directory from its registered identity.
        [void](Invoke-Native -FileName $InstallerPath -RawArguments '/S' -TimeoutSeconds 240)
    }
    Assert-Installed $CurrentVersion
    Assert-Sentinel
    $Report.currentVersion = $CurrentVersion; $Report.checks.upgradedSameIdentity = 'passed'
    $Stage = 'obsoleteInstalledFileRemoved'
    if (Test-Path -LiteralPath $ObsoleteFile) { throw 'The old installation retained the obsolete test file.' }
    $Report.checks.obsoleteInstalledFileRemoved = 'passed'

    }

    $Stage = 'installedResourceIntegrity'
    $ExpectedIcon = Join-Path $ProjectRoot 'desktop/assets/icon.ico'
    $IconHelper = Join-Path $ProjectRoot 'desktop/tests/windows-icon-resources.cjs'
    $Report.iconResources = [ordered]@{}
    foreach ($Name in @('FundsTrace.exe', 'Uninstall FundsTrace.exe')) {
        $IconJson = Invoke-Native -FileName $NodeExe -Arguments @($IconHelper, (Join-Path $InstallRoot $Name), $ExpectedIcon) -TimeoutSeconds 30
        $Icon = $IconJson | ConvertFrom-Json
        if ($Icon.status -cne 'passed') { throw 'Installed executable icon mismatch.' }
        $Report.iconResources[$Name] = $Icon
    }
    $Report.checks.installedExecutableIcons = 'passed'
    $Stage = 'installedShortcutIcons'
    $ShortcutJson = Invoke-Native -FileName (Join-Path $PSHOME 'pwsh.exe') -Arguments @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-File', (Join-Path $PSScriptRoot 'test-windows-brand-icon.ps1'),
        '-InstallRoot', $InstallRoot, '-ExpectedIco', $ExpectedIcon
    ) -TimeoutSeconds 30 -CaptureIconFailure
    $Report.shortcutIcons = $ShortcutJson | ConvertFrom-Json
    if ($Report.shortcutIcons.status -cne 'passed') { throw 'Installed shortcut icon mismatch.' }
    $Report.checks.installedShortcutIcons = 'passed'
    $Stage = 'installedResourceIntegrity'
    Assert-InstalledResources
    $InstalledBackend = Join-Path $InstallRoot 'resources/backend'
    $Report.checks.installedResourceIntegrity = 'passed'
    $Stage = $(if ($FreshInstallOnly) { 'currentPresentationPreferences' } else { 'upgradedPresentationPreferences' })
    Assert-PresentationPreferences
    $Report.checks[$Stage] = 'passed'

    $Stage = $(if ($FreshInstallOnly) { 'currentSeededCaseData' } else { 'upgradedExistingCaseData' })
    $UpgradedData = (Invoke-Native -FileName $NodeExe -Arguments @(
        $UpgradeHelper, $InstallRoot, '--mode', 'verify', '--fixture', $UpgradeFixture
    ) -Environment $CompatibilityEnvironment -TimeoutSeconds 240) | ConvertFrom-Json
    foreach ($Name in @('upgradedFactsUnchanged', 'upgradedCanvasAndTracking', 'legacyPackageRoundTrip',
        'upgradedAnalysisPaths', 'upgradedAnalysisCycles', 'upgradedAtomicCanvas', 'upgradedEofReopened', 'upgradedSourceFilesUnchanged')) {
        if ($UpgradedData.$Name -isnot [bool] -or -not $UpgradedData.$Name) { throw 'Existing data did not survive upgrade or package migration.' }
    }
    if ($FreshInstallOnly) {
        $Report.currentDataChecks = [ordered]@{}
        foreach ($Property in $UpgradedData.PSObject.Properties) {
            $Name = $Property.Name -creplace '^upgraded', 'current' -creplace '^legacyPackage', 'currentPackage'
            $Report.currentDataChecks[$Name] = $Property.Value
        }
        $Report.checks.currentSeededCaseData = 'passed'; $Report.checks.currentPackageCompatible = 'passed'
        $Report.checks.currentAnalysisAndAtomicCanvas = 'passed'
    } else {
        $Report.upgradedDataChecks = $UpgradedData
        $Report.checks.upgradedExistingCaseData = 'passed'
        $Report.checks.legacyCasePackageCompatible = 'passed'
        $Report.checks.upgradedAnalysisAndAtomicCanvas = 'passed'
    }

    $Stage = 'sameVersionReinstalled'
    $ReinstallDatabaseSha256 = Get-Sha256 (Join-Path $DataDirectory 'fund.duckdb')
    [void](Invoke-Native -FileName $InstallerPath -RawArguments '/S' -TimeoutSeconds 240)
    Assert-Installed $CurrentVersion
    Assert-Sentinel
    $Report.checks.sameVersionReinstalled = 'passed'
    $Stage = 'reinstalledResourceIntegrity'
    Assert-InstalledResources
    $Report.checks.reinstalledResourceIntegrity = 'passed'
    $Stage = 'reinstalledDataFilesUnchanged'
    if ((Get-Sha256 (Join-Path $DataDirectory 'fund.duckdb')) -cne $ReinstallDatabaseSha256) { throw 'Same-version reinstall changed the closed database.' }
    $Report.checks.reinstalledDataFilesUnchanged = 'passed'
    $Stage = 'reinstalledPresentationPreferences'
    Assert-PresentationPreferences
    $Report.checks.reinstalledPresentationPreferences = 'passed'
    $Stage = 'reinstalledExistingCaseData'
    $ReinstalledData = (Invoke-Native -FileName $NodeExe -Arguments @(
        $UpgradeHelper, $InstallRoot, '--mode', 'verify', '--fixture', $UpgradeFixture
    ) -Environment $CompatibilityEnvironment -TimeoutSeconds 240) | ConvertFrom-Json
    foreach ($Name in @('upgradedFactsUnchanged', 'upgradedCanvasAndTracking', 'legacyPackageRoundTrip',
        'upgradedAnalysisPaths', 'upgradedAnalysisCycles', 'upgradedAtomicCanvas', 'upgradedEofReopened', 'upgradedSourceFilesUnchanged')) {
        if ($ReinstalledData.$Name -isnot [bool] -or -not $ReinstalledData.$Name) { throw 'Same-version reinstall did not retain compatible case data.' }
    }
    if ($FreshInstallOnly) {
        $Report.reinstalledDataChecks = [ordered]@{}
        foreach ($Property in $ReinstalledData.PSObject.Properties) {
            $Name = $Property.Name -creplace '^upgraded', 'current' -creplace '^legacyPackage', 'currentPackage'
            $Report.reinstalledDataChecks[$Name] = $Property.Value
        }
    } else { $Report.reinstalledDataChecks = $ReinstalledData }
    $Report.checks.reinstalledExistingCaseData = 'passed'

    $Stage = 'installedBackendWithoutActivation'
    $HelperJson = Invoke-Native -FileName $NodeExe -Arguments @((Join-Path $ProjectRoot 'desktop/tests/installed-data-smoke.cjs'), $InstallRoot) -TimeoutSeconds 210
    $Helper = $HelperJson | ConvertFrom-Json
    $ExpectedHelperChecks = @('defaultUserData', 'seededCaseRead', 'activationFree', 'desktopAuthentication', 'caseCreatedAndReopened', 'eofShutdown', 'noLicenseFileGenerated')
    if (@($Helper.PSObject.Properties).Count -ne $ExpectedHelperChecks.Count) { throw 'Unexpected installed backend report.' }
    foreach ($Name in $ExpectedHelperChecks) {
        if ($Helper.$Name -isnot [bool] -or -not $Helper.$Name) { throw 'Installed backend did not satisfy its check.' }
    }
    $Report.checks.installedBackendWithoutActivation = 'passed'
    $Report.checks.installedBackendAuthentication = 'passed'
    $Report.checks.syntheticCaseCreatedAndReopened = 'passed'
    $Report.checks.eofShutdown = 'passed'

    $Stage = 'installedImportRecovery'
    $ImportReportPath = Join-Path (Split-Path -Parent $ReportPath) 'native-import-recovery-smoke.json'
    $ImportJson = Invoke-Native -FileName $NodeExe -Arguments @(
        (Join-Path $ProjectRoot 'desktop/tests/import-recovery-smoke.cjs'), $InstalledBackend, '--report', $ImportReportPath
    ) -TimeoutSeconds 240
    $ImportHelper = $ImportJson | ConvertFrom-Json
    if ($ImportHelper.status -cne 'passed') { throw 'Installed backend import recovery failed.' }
    $Report.checks.installedImportRecovery = 'passed'
    $Report.limits.importRecovery = 'installed-backend-public-api-process-restart; not Windows interactive task recovery'

    $Stage = 'installedApplicationStartupClose'
    $StartupClosePath = Join-Path $TemporaryRoot 'installed-startup-close.json'
    $StartupCloseJson = Invoke-Native -FileName $NodeExe -Arguments @(
        (Join-Path $ProjectRoot 'desktop/tests/installed-app-smoke.cjs'), $InstallRoot,
        '--report', $StartupClosePath, '--startup-close'
    ) -TimeoutSeconds 180
    $StartupCloseHelper = $StartupCloseJson | ConvertFrom-Json
    if ($StartupCloseHelper.status -cne 'passed') { throw 'Installed startup close did not complete.' }
    $Report.startupCloseChecks = [ordered]@{}
    foreach ($Name in @('installedStartupCloseBusinessPaused', 'installedStartupCloseNativeCaption',
        'installedStartupCloseCleanExit', 'installedStartupCloseBackendReleased', 'installedStartupCloseNoPageExceptions')) {
        if ($StartupCloseHelper.checks.$Name -isnot [bool] -or -not $StartupCloseHelper.checks.$Name) {
            throw 'Installed startup close did not satisfy its check.'
        }
        $Report.startupCloseChecks[$Name] = $true
    }
    $Report.checks.installedApplicationStartupClose = 'passed'

    $Stage = 'installedApplicationStarted'
    $AppReportPath = Join-Path (Split-Path -Parent $ReportPath) 'native-installed-app-smoke.json'
    $AppScreenshotPath = Join-Path (Split-Path -Parent $ReportPath) 'native-installed-app.png'
    if ($NextInstaller) {
    if (-not $NextUnpacked -or -not $TrustedCertificateFixture) { throw 'Private update candidate or certificate fixture is missing.' }
    $AppJson = Invoke-Native -FileName (Join-Path $PSHOME 'pwsh.exe') -Arguments @(
        '-NoLogo','-NoProfile','-NonInteractive','-File',(Join-Path $PSScriptRoot 'test-windows-updater.ps1'),
        '-InstallRoot',$InstallRoot,'-NextInstaller',$NextInstaller,'-NextUnpacked',$NextUnpacked,
        '-ReportPath',$AppReportPath,'-ScreenshotPath',$AppScreenshotPath,'-TrustedCertificateFixture',$TrustedCertificateFixture
    ) -TimeoutSeconds 600
    } else {
    $AppJson = Invoke-Native -FileName $NodeExe -Arguments @(
        (Join-Path $ProjectRoot 'desktop/tests/installed-app-smoke.cjs'), $InstallRoot,
        '--report', $AppReportPath, '--screenshot', $AppScreenshotPath
    ) -TimeoutSeconds 360
    }
    $AppHelper = $AppJson | ConvertFrom-Json
    if ($AppHelper.status -cne 'passed') { throw 'Installed application did not reach a usable workspace.' }
    foreach ($Name in @('installedExecutableStarted', 'authenticatedWorkspaceLoaded', 'nodeSelectionModuleLoaded',
        'installedStartupAnimationAndBounds',
        'installedPersistentCaptionStartup', 'installedPersistentCaptionZoomReloadAndNativeControls',
        'installedPersistentCaptionNavigation', 'installedAnalysisCaptionBoundToWindow',
        'activationFree', 'singleInstance', 'normalClose', 'backendReleasedAfterClose',
        'installedWorkspaceWindowIcon', 'installedAnalysisWindowIcon',
        'installedGraphModulesLoaded', 'installedCsvPreviewMapping', 'installedCsvCommittedExact',
        'installedQueryExact', 'installedFirstTrackExact', 'installedSecondTrackVisible',
        'installedAutomaticNodeLabelRecovery',
        'installedPartialViewportNodeLabels',
        'installedContextTrackedConsistent', 'installedNeighborhoodTrackVisible',
        'installedCanvasCloneExact', 'installedCanvasSaveRestoreTypes',
        'installedCanvasUndoRedoTypes', 'installedKeyboardUndoRedoOutsideGraph', 'installedCaseIsolationAndReturn', 'installedGraphScreenshot')) {
        if ($AppHelper.checks.$Name -isnot [bool] -or -not $AppHelper.checks.$Name) { throw 'Installed application did not satisfy its check.' }
    }
    $Report.checks.installedApplicationStarted = 'passed'
    $Report.checks.installedApplicationSingleInstance = 'passed'
    $Report.checks.installedApplicationNormalClose = 'passed'
    $Report.checks.installedGraphJourney = 'passed'
    if ($NextInstaller) {
        foreach ($Name in @('installedUpdateAvailableFromTrustedPrivateHttps','installedInterruptedUpdateRejected',
            'installedCorruptUpdateRejected','installedUpdateProgressAndReady','installedUpdateLaterRetainsApplication',
            'installedUpdateConfirmedRestart','installedUpdateAutoRelaunch','installedUpdateExactResources','installedUpdateRelaunchNormalClose')) {
            if ($AppHelper.checks.$Name -isnot [bool] -or -not $AppHelper.checks.$Name) { throw 'Installed update cycle did not satisfy its check.' }
        }
        $AfterUpdate = (Invoke-Native -FileName $NodeExe -Arguments @(
            $UpgradeHelper,$InstallRoot,'--mode','verify','--fixture',$UpgradeFixture
        ) -Environment $CompatibilityEnvironment -TimeoutSeconds 240) | ConvertFrom-Json
        foreach ($Property in $AfterUpdate.PSObject.Properties) { if ($Property.Value -isnot [bool] -or -not $Property.Value) { throw 'Native update changed existing synthetic case data.' } }
        $Report.inAppUpdateDataChecks = $AfterUpdate
        $Report.checks.installedInAppUpdate = 'passed'
    }

    $Stage = 'uninstallRegistrationRemoved'
    $UninstallPreferencesSha256 = Get-Sha256 $PreferenceFile
    Invoke-Uninstall
    $MayHaveInstalled = $false
    $Report.checks.uninstallRegistrationRemoved = 'passed'
    $Report.checks.installedExecutableRemoved = 'passed'
    $Stage = 'syntheticDataRetained'
    Assert-Sentinel
    $Stage = 'presentationPreferencesRetained'
    if ((Get-Sha256 $PreferenceFile) -cne $UninstallPreferencesSha256) { throw 'Uninstall changed retained presentation preferences.' }
    $Report.checks.presentationPreferencesRetained = 'passed'
    $Stage = 'syntheticDataRetained'
    $VerifyCode = @'
import os
import hashlib
import json
import re
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import duckdb
source = Path(os.environ["FUND_DATA_DIR"])
fixture = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert not (source / "license.dat").exists()
connection = duckdb.connect(str(source / "fund.duckdb"), read_only=True)
try:
    names = {row[0] for row in connection.execute("select name from cases").fetchall()}
    assert {"SYN prior installer lifecycle case", "SYN current installer lifecycle case"} <= names
    fields = ("id", "case_id", "batch_id", "trade_no", "trade_time", "payer_name", "payer_account", "payer_bank",
              "payee_name", "payee_account", "payee_bank", "amount", "balance", "currency", "summary",
              "channel", "self_account", "self_name")
    def wire(value, key):
        if key in ("id", "case_id", "batch_id"):
            return None if value is None else str(value)
        if key in ("amount", "balance"):
            return None if value is None else format(value, ".2f")
        if key == "trade_time":
            return value.isoformat(sep=" ") if isinstance(value, datetime) else value
        return value or ""
    for previous in (fixture["original"], fixture["isolated"]):
        cid = int(previous["caseId"])
        rows = connection.execute("select " + ",".join(fields) + " from flows where case_id=? order by trade_no", [cid]).fetchall()
        assert [{key: wire(value, key) for key, value in zip(fields, row)} for row in rows] == previous["rows"]
        tracked = lambda canvas: sorted(str(row[0]) for row in connection.execute(
            "select flow_id from tracked_flows where case_id=? and canvas_id=?", [cid, canvas]).fetchall())
        assert tracked(0) == previous["defaultTrackedIds"]
        canvases = connection.execute("select id,name,snapshot from canvases where case_id=? order by name", [cid]).fetchall()
        assert [{"id": str(row[0]), "name": row[1], "snapshot": json.loads(row[2]), "trackedIds": tracked(row[0])}
                for row in canvases] == previous["canvases"]
        mains = connection.execute("select account,name,source from main_accounts where case_id=? order by marked_at,account", [cid]).fetchall()
        assert [dict(zip(("account", "name", "source"), row)) for row in mains] == previous["mains"]
        identities = connection.execute("select account,id_no from account_ids where case_id=? order by account", [cid]).fetchall()
        assert [dict(zip(("account", "id_no"), row)) for row in identities] == previous["identities"]
    for entry in fixture["sources"]:
        assert re.fullmatch(r"uploads/[0-9a-f]{32}\.csv", entry["relative"])
        original = source / entry["relative"]
        assert not original.is_symlink() and original.resolve().is_relative_to(source.resolve())
        assert hashlib.sha256(original.read_bytes()).hexdigest() == entry["sha256"]
finally:
    connection.close()
'@
    [void](Invoke-Native -FileName $PythonExe -Arguments @('-c', $VerifyCode, (Join-Path $UpgradeFixture 'snapshot.json')) -Environment $PythonEnvironment -TimeoutSeconds 60)
    $Report.checks.syntheticDataRetained = 'passed'
    }
} catch {
    $Failed = $true
    $Report.failedCheck = $Stage
    $Report.failureType = $_.Exception.GetType().FullName
    $Report.failureId = $(if ($_.FullyQualifiedErrorId -cmatch '^[A-Za-z0-9_.,:+-]{1,120}$') { $_.FullyQualifiedErrorId } else { 'UNCLASSIFIED' })
    $Report.failedNativeStep = $script:NativeStep
    $Report.nativeDiagnostic = $script:NativeDiagnostic
    $NativeException = $_.Exception
    while ($null -ne $NativeException) {
        if ($NativeException -is [System.ComponentModel.Win32Exception]) {
            $Report.nativeErrorCode = $NativeException.NativeErrorCode
            break
        }
        $NativeException = $NativeException.InnerException
    }
    if ($Report.checks.Contains($Stage)) { $Report.checks[$Stage] = 'failed' }
} finally {
    try {
        # Always attempt normal uninstall of this test's own installation, including on failure.
        if ($MayHaveInstalled -and $null -ne $InstallRoot) {
            if (Test-Path -LiteralPath (Join-Path $InstallRoot 'Uninstall FundsTrace.exe') -PathType Leaf) { Invoke-Uninstall }
            elseif (-not (Test-Removed)) { throw 'Partial installation needs normal cleanup.' }
        }
        foreach ($Process in $script:TimedOutProcesses) {
            if (-not $Process.HasExited) { throw 'Owned timed-out process is still running.' }
            $Process.Dispose()
        }
        if ($OwnTemporary -and -not (Test-Removed)) { throw 'Installed state remains after cleanup.' }
        if ($OwnUserData -and (Test-Path -LiteralPath $UserRoot)) {
            if (((Get-Item -LiteralPath $UserRoot).Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Owned data root changed.' }
            Remove-Item -LiteralPath $UserRoot -Recurse -Force
        }
        if ($OwnTemporary -and (Test-Path -LiteralPath $TemporaryRoot)) { Remove-Item -LiteralPath $TemporaryRoot -Recurse -Force }
        $Report.checks.cleanup = 'passed'
    } catch {
        $Failed = $true; $Report.checks.cleanup = 'failed'
        if ($null -eq $Report.failedCheck) { $Report.failedCheck = 'cleanup' }
    }
    $Report.status = $(if ($Failed) { 'failed' } elseif ($PreflightOnly) { 'preflight-passed' } else { 'passed' })
    if ($ReportReady) {
        try { $Report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ReportPath -Encoding utf8 }
        catch { $Failed = $true }
    }
}
if ($Failed) {
    [Console]::Error.WriteLine("Installer lifecycle failed at $($Report.failedCheck), native step $($Report.failedNativeStep), type $($Report.failureType), id $($Report.failureId), native error $($Report.nativeErrorCode).")
    exit 1
}
if ($PreflightOnly) { Write-Host 'Native installer preflight passed; no installation was attempted.' }
elseif ($FreshInstallOnly) { Write-Host 'Native fresh installer lifecycle passed: first normal empty GUI before current-backend fixture, formats, reinstall and uninstall retention.' }
else { Write-Host "Native installer lifecycle passed: pinned $($Baseline.prior.version) to $CurrentVersion upgrade, obsolete-file removal, preferences, same-version reinstall, case compatibility, installed application startup/single-instance/close, activation-free data, EOF and uninstall retention. Win10/11 interactive acceptance remains separate." }
exit 0
