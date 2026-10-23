#Requires -Version 7.0
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string]$TargetSha256,
    [Parameter(Mandatory = $true)][ValidateRange(1, [long]::MaxValue)][long]$TargetBytes,
    [switch]$InAppUpdate,
    [switch]$OfficialNetworkDiagnostic
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest
# Reuse released bytes in a disposable hosted account; never rebuild a same-version substitute.
if (-not $IsWindows -or $env:GITHUB_ACTIONS -cne 'true' -or
    [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString() -ne 'X64') {
    throw 'Published upgrade verification requires native Windows x64 GitHub Actions.'
}
if ($OfficialNetworkDiagnostic -and (-not $InAppUpdate -or $env:CI -cne 'true')) {
    throw 'Official network diagnostics require the isolated in-app update journey.'
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
if ($OfficialNetworkDiagnostic -and ($Version -cne '0.1.12' -or $Baseline.prior.version -cne '0.1.11')) {
    throw 'Official network diagnostics are restricted to the current frozen release pair.'
}
$SevenZip = (Get-Command 7z.exe -CommandType Application | Select-Object -First 1).Path
if (-not [IO.File]::Exists($SevenZip)) { throw '7-Zip is unavailable.' }
[void](New-Item -ItemType Directory -Path $InputRoot)
$Installer = Join-Path $InputRoot "FundsTrace-$Version-windows-x64-unsigned.exe"
$Url = "https://github.com/Eysn0130/FundsTrace/releases/download/v$Version/FundsTrace-$Version-windows-x64-unsigned.exe"
Invoke-WebRequest -Uri $Url -OutFile $Installer -TimeoutSec 240 -MaximumRedirection 5
$ActualSha256 = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualSha256 -cne $TargetSha256 -or (Get-Item -LiteralPath $Installer).Length -ne $TargetBytes) {
    throw 'Published target installer differs from the frozen digest and size.'
}
$IconHelper = Join-Path $ProjectRoot 'desktop/tests/windows-icon-resources.cjs'
$ExpectedIcon = Join-Path $ProjectRoot 'desktop/assets/icon.ico'
$InstallerIcon = (& node $IconHelper $Installer $ExpectedIcon) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $InstallerIcon.status -cne 'passed') { throw 'Published installer icon differs from its current version contract.' }
$ArchiveRoot = Join-Path $InputRoot 'nsis'
& $SevenZip x $Installer "-o$ArchiveRoot" -y | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not read the published NSIS package.' }
$Payload = Join-Path $ArchiveRoot '$PLUGINSDIR/app-64.7z'
if (-not (Test-Path -LiteralPath $Payload -PathType Leaf)) { throw 'Published NSIS x64 payload is missing.' }
& $SevenZip x $Payload "-o$CandidateRoot" -y | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not unpack the published application reference.' }
$Executable = Join-Path $CandidateRoot 'FundsTrace.exe'
$Info = (Get-Item -LiteralPath $Executable).VersionInfo
if (($Info.FileVersion.Split('.')[0..2] -join '.') -cne $Version -or ($Info.ProductVersion.Split('.')[0..2] -join '.') -cne $Version) {
    throw 'Published application version differs from the source test contract.'
}
$ApplicationIcon = (& node $IconHelper $Executable $ExpectedIcon) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $ApplicationIcon.status -cne 'passed') { throw 'Published application icon differs from its current version contract.' }
[void](New-Item -ItemType Directory -Path $ManifestRoot)
Copy-Item -LiteralPath (Join-Path $CandidateRoot 'resources/backend/build-manifest.json') -Destination $ManifestRoot
$InputReport = [ordered]@{
    schemaVersion = 1; targetVersion = $Version; targetSha256 = $ActualSha256; targetBytes = $TargetBytes
    testCommit = $env:GITHUB_SHA; targetReleaseTag = "v$Version"
    source = 'published GitHub Release installer'; rebuilt = $false; upgradeBaseline = $Baseline
    productionInAppUpdate = [bool]$InAppUpdate
    officialNetworkDiagnostic = [bool]$OfficialNetworkDiagnostic
    installerIcon = $InstallerIcon; applicationIcon = $ApplicationIcon
    extractedApplicationSha256 = (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash.ToLowerInvariant()
    limits = @{ windows10And11Interactive = $false; systemDpi = $false; powerLoss = $false
        acceptanceEligible = -not [bool]$OfficialNetworkDiagnostic }
}
$InputReport | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $EvidenceRoot 'published-upgrade-inputs.json') -Encoding utf8
if ($InAppUpdate -and -not $OfficialNetworkDiagnostic) {
    # A separate, read-only network preflight does not populate the updater's
    # cache or substitute for any of the installed client's acceptance gates.
    $TargetSha512 = [Convert]::ToBase64String([Convert]::FromHexString((Get-FileHash -LiteralPath $Installer -Algorithm SHA512).Hash))
    & node (Join-Path $ProjectRoot 'desktop/tests/public-update-transport-preflight.cjs') `
        --report (Join-Path $EvidenceRoot 'native-public-update-preflight.json') `
        --version $Version --sha256 $TargetSha256 --sha512 $TargetSha512 --bytes $TargetBytes
    if ($LASTEXITCODE -ne 0) { throw 'Public update transport preflight failed; installed-client acceptance was not started.' }
}
if ($OfficialNetworkDiagnostic) {
    # Observe the old updater even when the independent preflight would fail.
    # This separate diagnosis cannot pass the normal preflight or lifecycle.
    @{ schemaVersion=1; status='diagnostic-only-skipped'; passed=$false; acceptanceEligible=$false } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $EvidenceRoot 'native-public-update-preflight.json') -Encoding utf8
}
& "$PSScriptRoot/test-windows-installer.ps1" -InstallerPath $Installer -ReportPath (Join-Path $EvidenceRoot 'native-installer-smoke.json') -PublishedInAppUpdate:$InAppUpdate -OfficialNetworkDiagnostic:$OfficialNetworkDiagnostic
if ($OfficialNetworkDiagnostic) { throw 'Official network diagnostic completed without release acceptance; standard-user qualification was not started.' }
if ($LASTEXITCODE -ne 0) { throw 'Published upgrade lifecycle failed.' }
& "$PSScriptRoot/test-windows-standard-user.ps1" -InstallerPath $Installer -ReportDirectory (Join-Path $EvidenceRoot 'standard-user') -PublishedInAppUpdate:$InAppUpdate
if ($LASTEXITCODE -ne 0) { throw 'Published upgrade standard-user lifecycle failed.' }
