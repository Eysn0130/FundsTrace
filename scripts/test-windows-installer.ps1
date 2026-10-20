#Requires -Version 7.0
param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [Parameter(Mandatory = $true)][string]$ReportPath,
    [string]$CiTemporaryRoot,
    [ValidateSet('0.1.5', '0.1.7')][string]$PriorVersion = '0.1.5',
    [string]$NextInstaller,
    [string]$NextUnpacked,
    [string]$TrustedCertificateFixture,
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

$ProjectRoot = Split-Path -Parent $PSScriptRoot
# Existing appId com.eysn.fundstrace, UUIDv5 in electron-builder's unchanged namespace.
$InstallKey = 'Software\b56dd6f8-a8a1-5427-b970-3e6cc301a2c0'
$UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\b56dd6f8-a8a1-5427-b970-3e6cc301a2c0'
$SentinelContent = "FundsTrace synthetic installer lifecycle v1`n"
$Stage = 'inputPaths'
$script:NativeStep = $null
$Report = [ordered]@{
    schemaVersion = 1; status = 'running'; priorVersion = $null; currentVersion = $null; failedCheck = $null
    failureType = $null; failureId = $null; failedNativeStep = $null; nativeErrorCode = $null
    checks = [ordered]@{
        ciFreshUser = 'not-run'; priorInstallerDigest = 'not-run'; priorInstalled = 'not-run'
        syntheticDataSeeded = 'not-run'; priorCaseDataWrittenByInstalledBackend = 'not-run'
        upgradedSameIdentity = 'not-run'; installedResourceIntegrity = 'not-run'
        upgradedExistingCaseData = 'not-run'; legacyCasePackageCompatible = 'not-run'
        upgradedAnalysisAndAtomicCanvas = 'not-run'
        installedBackendWithoutActivation = 'not-run'; installedBackendAuthentication = 'not-run'
        syntheticCaseCreatedAndReopened = 'not-run'; eofShutdown = 'not-run'
        installedApplicationStarted = 'not-run'; installedApplicationSingleInstance = 'not-run'
        installedApplicationNormalClose = 'not-run'
        installedGraphJourney = 'not-run'
        installedImportRecovery = 'not-run'
        uninstallRegistrationRemoved = 'not-run'; installedExecutableRemoved = 'not-run'
        syntheticDataRetained = 'not-run'; cleanup = 'not-run'
    }
    limits = [ordered]@{ gui = $false; importRecovery = $false }
}
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
function Invoke-Native {
    param([string]$FileName, [string[]]$Arguments = @(), [string]$RawArguments = '',
        [hashtable]$Environment = @{}, [int]$TimeoutSeconds = 180)
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
            if ($ErrorText -cmatch '\AUpgrade data smoke failed at ([A-Za-z]{1,80}) line ([0-9]{1,5})\.\r?\n\z') {
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
    $Stage = 'workingDirectory'
    if (-not [System.IO.Directory]::Exists($ProjectRoot)) { throw 'Source working directory is unavailable.' }
    $Stage = 'candidateIdentity'
    $CurrentVersion = (Get-Content -Raw -LiteralPath (Join-Path $ProjectRoot 'desktop/package.json') | ConvertFrom-Json).version
    if ($CurrentVersion -cnotmatch '^\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?$') { throw 'INVALID_CANDIDATE_VERSION' }
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

    $Stage = 'priorInstallerDigest'
    $PriorDigests = @{
        '0.1.5' = 'f5c337e37adb30dc30c8cb895722703c91461ed0de49acee8dd487d454d8f1fc'
        '0.1.7' = '52eabd3c6b0f77270c61066b8d1830b84cb5b316153d399625c7d21badfbeb7a'
    }
    Invoke-WebRequest -Uri "https://github.com/Eysn0130/FundsTrace/releases/download/v$PriorVersion/FundsTrace-$PriorVersion-windows-x64-unsigned.exe" -OutFile $PriorInstaller -TimeoutSec 180 -MaximumRedirection 5
    if ((Get-Sha256 $PriorInstaller) -cne $PriorDigests[$PriorVersion]) {
        throw 'The prior installer digest differs from the pinned release.'
    }
    $Report.checks.priorInstallerDigest = 'passed'
    $Stage = 'priorInstalled'
    $MayHaveInstalled = $true
    # /D is NSIS syntax: absolute path, last argument, deliberately no quotes even with spaces.
    [void](Invoke-Native -FileName $PriorInstaller -RawArguments "/S /D=$InstallRoot" -TimeoutSeconds 240)
    Assert-Installed $PriorVersion
    $Report.priorVersion = $PriorVersion; $Report.priorInstallerSha256 = $PriorDigests[$PriorVersion]
    $Report.checks.priorInstalled = 'passed'

    $Stage = 'syntheticDataSeeded'
    # The earlier absence check establishes ownership; never redirect or reuse a maintainer's data.
    if (Test-Path -LiteralPath $UserRoot) { throw 'Unexpected data appeared before synthetic seeding.' }
    [void](New-Item -ItemType Directory -Path $UserRoot)
    $OwnUserData = $true
    [System.IO.File]::WriteAllText((Join-Path $UserRoot 'installer-lifecycle-sentinel.txt'), $SentinelContent)
    $DataDirectory = Join-Path $UserRoot 'data'
    [void](New-Item -ItemType Directory -Path $DataDirectory)
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
    $Report.priorDataChecks = $PriorData
    if (-not (Test-Path -LiteralPath (Join-Path $DataDirectory 'fund.duckdb') -PathType Leaf)) { throw 'Synthetic database was not created.' }
    $Report.checks.syntheticDataSeeded = 'passed'
    $Report.checks.priorCaseDataWrittenByInstalledBackend = 'passed'

    $Stage = 'upgradedSameIdentity'
    [void](Invoke-Native -FileName $InstallerPath -RawArguments "/S /D=$InstallRoot" -TimeoutSeconds 240)
    Assert-Installed $CurrentVersion
    Assert-Sentinel
    $Report.currentVersion = $CurrentVersion; $Report.checks.upgradedSameIdentity = 'passed'

    $Stage = 'installedResourceIntegrity'
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
    $Report.checks.installedResourceIntegrity = 'passed'

    $Stage = 'upgradedExistingCaseData'
    $UpgradedData = (Invoke-Native -FileName $NodeExe -Arguments @(
        $UpgradeHelper, $InstallRoot, '--mode', 'verify', '--fixture', $UpgradeFixture
    ) -Environment $CompatibilityEnvironment -TimeoutSeconds 240) | ConvertFrom-Json
    foreach ($Name in @('upgradedFactsUnchanged', 'upgradedCanvasAndTracking', 'legacyPackageRoundTrip',
        'upgradedAnalysisPaths', 'upgradedAnalysisCycles', 'upgradedAtomicCanvas', 'upgradedEofReopened', 'upgradedSourceFilesUnchanged')) {
        if ($UpgradedData.$Name -isnot [bool] -or -not $UpgradedData.$Name) { throw 'Existing data did not survive upgrade or package migration.' }
    }
    $Report.upgradedDataChecks = $UpgradedData
    $Report.checks.upgradedExistingCaseData = 'passed'
    $Report.checks.legacyCasePackageCompatible = 'passed'
    $Report.checks.upgradedAnalysisAndAtomicCanvas = 'passed'

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
        'activationFree', 'singleInstance', 'normalClose', 'backendReleasedAfterClose',
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
    Invoke-Uninstall
    $MayHaveInstalled = $false
    $Report.checks.uninstallRegistrationRemoved = 'passed'
    $Report.checks.installedExecutableRemoved = 'passed'
    $Stage = 'syntheticDataRetained'
    Assert-Sentinel
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
else { Write-Host "Native installer lifecycle passed: pinned $PriorVersion upgrade, prior-version case compatibility, installed application startup/single-instance/close, activation-free data, EOF and uninstall retention. Win10/11 interactive acceptance remains separate." }
exit 0
