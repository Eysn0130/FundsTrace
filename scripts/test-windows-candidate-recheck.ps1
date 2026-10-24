#Requires -Version 7.0
param([Parameter(Mandatory = $true)][string]$SourceRoot)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest
if (-not $IsWindows -or $env:GITHUB_ACTIONS -cne 'true' -or -not $env:RUNNER_TEMP -or
    [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString() -ne 'X64') {
    throw 'Candidate recheck requires native Windows x64 GitHub Actions.'
}
if ($env:GH_TOKEN -or $env:GITHUB_TOKEN) { throw 'Lifecycle execution must not inherit a GitHub download token.' }
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EvidenceRoot = Join-Path $ProjectRoot 'build/desktop'
$ReportRoot = Join-Path $EvidenceRoot 'candidate-recheck'
$SourceRoot = [IO.Path]::GetFullPath($SourceRoot)
$TemporaryPrefix = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\') + '\'
if (-not $SourceRoot.StartsWith($TemporaryPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    (Test-Path -LiteralPath $ReportRoot)) { throw 'Recheck requires owned source inputs and new reports.' }
$SourcePath = Join-Path $SourceRoot 'source.json'
$Source = Get-Content -Raw -LiteralPath $SourcePath | ConvertFrom-Json
if ($Source.source -cne 'same candidate Actions artifact' -or $Source.rebuilt -ne $false -or
    $Source.sourceCommit -cne $env:GITHUB_SHA -or [string]$Source.sourceRun -ceq $env:GITHUB_RUN_ID) {
    throw 'Recheck source must be the same commit and a different original run.'
}
[void](New-Item -ItemType Directory -Path $ReportRoot)
$PythonExe = Join-Path $ProjectRoot '.venv/Scripts/python.exe'
$Prepared = & "$PSScriptRoot/prepare-windows-installer.ps1" -InstallerPath $Source.installerPath -InputRoot $SourceRoot -Version $Source.targetVersion
& $PythonExe "$PSScriptRoot/candidate_recheck.py" verify-payload --source $SourcePath --payload-root $Prepared.candidateRoot
if ($LASTEXITCODE -ne 0) { throw 'Original manifest and exact unpacked payload differ.' }
# This directory contains only this run's observations, never the source run's reports.
$Preparation = [ordered]@{
    schemaVersion = 1; status = 'passed'; source = 'same candidate Actions artifact'; rebuilt = $false
    sourceRun = $Source.sourceRun; sourceCommit = $Source.sourceCommit; artifactId = $Source.artifactId
    artifactName = $Source.artifactName; testRun = $env:GITHUB_RUN_ID; testCommit = $env:GITHUB_SHA
    targetVersion = $Source.targetVersion; targetSha256 = $Source.targetSha256; targetBytes = $Source.targetBytes
    originalManifestSha256 = $Source.originalManifestSha256; originalChecksumsSha256 = $Source.originalChecksumsSha256
    upgradeBaseline = $Source.upgradeBaseline; installerIcon = $Prepared.installerIcon
    applicationIcon = $Prepared.applicationIcon; extractedApplicationSha256 = $Prepared.extractedApplicationSha256
}
$Preparation | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $ReportRoot 'preparation.json') -Encoding utf8
& "$PSScriptRoot/test-windows-standard-user.ps1" -InstallerPath $Source.installerPath -ReportDirectory (Join-Path $ReportRoot 'fresh-standard-user') -FreshInstallOnly
if ($LASTEXITCODE -ne 0) { throw 'Same-EXE fresh standard-user lifecycle failed.' }
& "$PSScriptRoot/test-windows-installer.ps1" -InstallerPath $Source.installerPath -ReportPath (Join-Path $ReportRoot 'native-installer-smoke.json') -PriorVersion $Source.upgradeBaseline.prior.version
if ($LASTEXITCODE -ne 0) { throw 'Same-EXE upgrade lifecycle failed.' }
& "$PSScriptRoot/test-windows-standard-user.ps1" -InstallerPath $Source.installerPath -ReportDirectory (Join-Path $ReportRoot 'standard-user') -PriorVersion $Source.upgradeBaseline.prior.version
if ($LASTEXITCODE -ne 0) { throw 'Same-EXE upgrade standard-user lifecycle failed.' }
& $PythonExe "$PSScriptRoot/candidate_recheck.py" verify-payload --source $SourcePath --payload-root $Prepared.candidateRoot
if ($LASTEXITCODE -ne 0) { throw 'Original inputs or reference payload changed during lifecycle execution.' }
& $PythonExe "$PSScriptRoot/candidate_recheck.py" receipt --source $SourcePath --report-root $ReportRoot --commit $env:GITHUB_SHA --test-run $env:GITHUB_RUN_ID --output (Join-Path $ReportRoot 'candidate-recheck.json')
if ($LASTEXITCODE -ne 0) { throw 'Same-EXE recheck receipt validation failed.' }
