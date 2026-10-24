#Requires -Version 7.0
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string]$TargetSha256,
    [Parameter(Mandatory = $true)][ValidateRange(1, [long]::MaxValue)][long]$TargetBytes,
    [switch]$InAppUpdate
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest
# Reuse released bytes in a disposable hosted account; never rebuild a same-version substitute.
if (-not $IsWindows -or $env:GITHUB_ACTIONS -cne 'true' -or
    [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString() -ne 'X64') {
    throw 'Published upgrade verification requires native Windows x64 GitHub Actions.'
}
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EvidenceRoot = Join-Path $ProjectRoot 'build/desktop'
$InputRoot = Join-Path $EvidenceRoot 'published-upgrade-inputs'
$CandidateRoot = Join-Path $EvidenceRoot 'electron/win-unpacked'
$ManifestRoot = Join-Path $EvidenceRoot 'backend/funds-trace-backend'
foreach ($Directory in @($InputRoot, $CandidateRoot, $ManifestRoot)) {
    if (Test-Path -LiteralPath $Directory) { throw 'Published upgrade inputs must be new isolated directories.' }
}
$Version = (Get-Content -Raw -LiteralPath (Join-Path $ProjectRoot 'desktop/package.json') | ConvertFrom-Json).version
if ($Version -cnotmatch '^\d+\.\d+\.\d+$') { throw 'Require a stable release version.' }
$PythonExe = Join-Path $ProjectRoot '.venv/Scripts/python.exe'
$Baseline = (& $PythonExe (Join-Path $ProjectRoot 'scripts/upgrade_baseline.py') --baseline (Join-Path $ProjectRoot 'desktop/upgrade-baseline.json') --package (Join-Path $ProjectRoot 'desktop/package.json')) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $Baseline.targetVersion -cne $Version) { throw 'Published target must match the current package and upgrade baseline.' }
[void](New-Item -ItemType Directory -Path $InputRoot)
$Installer = Join-Path $InputRoot "FundsTrace-$Version-windows-x64-unsigned.exe"
$Url = "https://github.com/Eysn0130/FundsTrace/releases/download/v$Version/FundsTrace-$Version-windows-x64-unsigned.exe"
Invoke-WebRequest -Uri $Url -OutFile $Installer -TimeoutSec 240 -MaximumRedirection 5
$ActualSha256 = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualSha256 -cne $TargetSha256 -or (Get-Item -LiteralPath $Installer).Length -ne $TargetBytes) {
    throw 'Published target installer differs from the frozen digest and size.'
}
$Prepared = & "$PSScriptRoot/prepare-windows-installer.ps1" -InstallerPath $Installer -InputRoot $InputRoot -Version $Version
$InputReport = [ordered]@{
    schemaVersion = 1; targetVersion = $Version; targetSha256 = $ActualSha256; targetBytes = $TargetBytes
    testCommit = $env:GITHUB_SHA; targetReleaseTag = "v$Version"
    source = 'published GitHub Release installer'; rebuilt = $false; upgradeBaseline = $Baseline
    productionInAppUpdate = [bool]$InAppUpdate
    installerIcon = $Prepared.installerIcon; applicationIcon = $Prepared.applicationIcon
    extractedApplicationSha256 = $Prepared.extractedApplicationSha256
    limits = @{ windows10And11Interactive = $false; systemDpi = $false; powerLoss = $false }
}
$InputReport | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $EvidenceRoot 'published-upgrade-inputs.json') -Encoding utf8
& "$PSScriptRoot/test-windows-installer.ps1" -InstallerPath $Installer -ReportPath (Join-Path $EvidenceRoot 'native-installer-smoke.json') -PublishedInAppUpdate:$InAppUpdate
if ($LASTEXITCODE -ne 0) { throw 'Published upgrade lifecycle failed.' }
& "$PSScriptRoot/test-windows-standard-user.ps1" -InstallerPath $Installer -ReportDirectory (Join-Path $EvidenceRoot 'standard-user') -PublishedInAppUpdate:$InAppUpdate
if ($LASTEXITCODE -ne 0) { throw 'Published upgrade standard-user lifecycle failed.' }
