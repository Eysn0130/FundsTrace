#Requires -Version 7.0
param([switch]$Installer, [switch]$VerifyInstallerLifecycle)
if ($VerifyInstallerLifecycle -and -not $Installer) { throw "Installer lifecycle verification requires -Installer." }
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (-not [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::Windows)) { throw "Run this pipeline natively on Windows x64." }
if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString() -ne "X64") { throw "This candidate targets Windows x64." }
$ProjectRoot = Split-Path -Parent $PSScriptRoot
function Invoke-Checked([scriptblock]$Command) { & $Command; if ($LASTEXITCODE -ne 0) { throw "Build command failed (exit $LASTEXITCODE)." } }
function Get-Sha256([string]$File) { (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant() }
Push-Location $ProjectRoot
try {
    $NodeInfo = (& node -p "JSON.stringify({version:process.versions.node,arch:process.arch})") | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $NodeInfo.version -ne "22.22.1" -or $NodeInfo.arch -ne "x64") { throw "Use Node 22.22.1 running as an x64 process." }
    $UvVersion = (& uv --version).Trim()
    if ($LASTEXITCODE -ne 0) { throw "A working uv installation is required." }
    Invoke-Checked { uv venv --python 3.11 build/desktop/venv }
    $PythonExe = Join-Path $ProjectRoot "build/desktop/venv/Scripts/python.exe"
    $PythonInfo = (& $PythonExe -c "import json, platform, struct, sys; print(json.dumps({'version': platform.python_version(), 'major': sys.version_info.major, 'minor': sys.version_info.minor, 'bits': struct.calcsize('P') * 8, 'machine': platform.machine().lower()}))") | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $PythonInfo.major -ne 3 -or $PythonInfo.minor -ne 11 -or $PythonInfo.bits -ne 64 -or $PythonInfo.machine -notin @("amd64", "x86_64")) { throw "Use Python 3.11 running as an x64 process." }
    Invoke-Checked { uv pip sync --python $PythonExe --require-hashes requirements-desktop.lock }
    Push-Location frontend
    try { Invoke-Checked { npm.cmd ci }; Invoke-Checked { npm.cmd run build } } finally { Pop-Location }
    Invoke-Checked { & $PythonExe scripts/build_desktop_backend.py }
    Push-Location desktop
    try {
        Invoke-Checked { npm.cmd ci }
        Invoke-Checked { npm.cmd test }
        if ($Installer) { Invoke-Checked { npm.cmd run pack:win } }
        else { Invoke-Checked { npm.cmd run pack -- --win --x64 } }
    } finally { Pop-Location }

    # Evidence contains only versions, relative candidate filenames and content hashes.
    $EvidenceRoot = Join-Path $ProjectRoot "build/desktop"
    $NativeManifest = Join-Path $EvidenceRoot "backend/funds-trace-backend/build-manifest.json"
    $SmokeReport = Join-Path $EvidenceRoot "native-backend-smoke.json"
    $CandidateDir = Join-Path $EvidenceRoot "electron/win-unpacked"
    if (-not (Test-Path -LiteralPath (Join-Path $CandidateDir "FundsTrace.exe") -PathType Leaf)) { throw "The Windows directory candidate is missing." }
    $LocaleNames = @(Get-ChildItem -LiteralPath (Join-Path $CandidateDir 'locales') -Filter '*.pak' -File | Sort-Object Name | ForEach-Object { $_.Name })
    if (($LocaleNames -join ',') -cne 'en-US.pak,zh-CN.pak,zh-TW.pak') { throw 'The packaged language fallback set differs from the candidate policy.' }
    $Version = (Get-Content -Raw -LiteralPath (Join-Path $ProjectRoot "desktop/package.json") | ConvertFrom-Json).version
    $ExpectedVersion = [version](($Version -split '[-+]', 2)[0])
    $ExecutableInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo((Join-Path $CandidateDir "FundsTrace.exe"))
    if ($ExecutableInfo.FileMajorPart -ne $ExpectedVersion.Major -or $ExecutableInfo.FileMinorPart -ne $ExpectedVersion.Minor -or
        $ExecutableInfo.FileBuildPart -ne $ExpectedVersion.Build -or $ExecutableInfo.ProductMajorPart -ne $ExpectedVersion.Major -or
        $ExecutableInfo.ProductMinorPart -ne $ExpectedVersion.Minor -or $ExecutableInfo.ProductBuildPart -ne $ExpectedVersion.Build) {
        throw "Packaged executable version metadata differs from the candidate version."
    }
    $ExecutableVersion = [ordered]@{ fileVersion = $ExecutableInfo.FileVersion; productVersion = $ExecutableInfo.ProductVersion }
    $PackagedBackend = Join-Path $CandidateDir "resources/backend"
    if ((Get-Sha256 (Join-Path $PackagedBackend "build-manifest.json")) -ne (Get-Sha256 $NativeManifest)) { throw "The packaged backend manifest differs from the native build." }
    $Resources = (Get-Content -Raw -LiteralPath $NativeManifest | ConvertFrom-Json).resources
    foreach ($Resource in $Resources.PSObject.Properties) {
        if ((Get-Sha256 (Join-Path $PackagedBackend "_internal/$($Resource.Name)")) -ne $Resource.Value -or (Get-Sha256 (Join-Path $ProjectRoot $Resource.Name)) -ne $Resource.Value) { throw "Packaged frontend resource drift." }
    }
    # Test what is actually inside the directory candidate, not the pre-packaging copy.
    Invoke-Checked { node desktop/tests/backend-smoke.cjs $PackagedBackend --report $SmokeReport }
    $CandidateFiles = @(Get-ChildItem -LiteralPath $CandidateDir -File -Recurse)
    if ($Installer) {
        $InstallerPath = Join-Path $EvidenceRoot "electron/FundsTrace-$Version-windows-x64-unsigned.exe"
        if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) { throw "The unsigned installer candidate is missing." }
        $CandidateFiles += Get-Item -LiteralPath $InstallerPath
    }
    $InstallerReport = Join-Path $EvidenceRoot "native-installer-smoke.json"
    $InstalledAppReport = Join-Path $EvidenceRoot "native-installed-app-smoke.json"
    $ImportRecoveryReport = Join-Path $EvidenceRoot 'native-import-recovery-smoke.json'
    $CompatibilityDirectory = Join-Path $EvidenceRoot 'standard-user'
    if ($VerifyInstallerLifecycle) {
        Invoke-Checked { & "$PSScriptRoot/test-windows-installer.ps1" -InstallerPath $InstallerPath -ReportPath $InstallerReport }
        Invoke-Checked { & "$PSScriptRoot/test-windows-standard-user.ps1" -InstallerPath $InstallerPath -ReportDirectory $CompatibilityDirectory }
    }
    $Files = @($CandidateFiles | Sort-Object FullName | ForEach-Object {
        [ordered]@{ path = [System.IO.Path]::GetRelativePath($ProjectRoot, $_.FullName).Replace('\', '/'); bytes = $_.Length; sha256 = Get-Sha256 $_.FullName }
    })
    $InstalledFiles = @($Files | Where-Object { $_.path.StartsWith('build/desktop/electron/win-unpacked/') })
    $Sizes = [ordered]@{
        installedBytes = ($InstalledFiles | ForEach-Object { [long]$_.bytes } | Measure-Object -Sum).Sum
        backendBytes = ($InstalledFiles | Where-Object { $_.path.Contains('/resources/backend/') } | ForEach-Object { [long]$_.bytes } | Measure-Object -Sum).Sum
        localeBytes = ($InstalledFiles | Where-Object { $_.path.Contains('/locales/') } | ForEach-Object { [long]$_.bytes } | Measure-Object -Sum).Sum
        localeFiles = $LocaleNames
        installerBytes = $(if ($Installer) { (Get-Item -LiteralPath $InstallerPath).Length } else { $null })
        largestFiles = @($InstalledFiles | Sort-Object { $_.bytes } -Descending | Select-Object -First 10)
    }
    $Locks = [ordered]@{}
    foreach ($File in @("requirements-desktop.lock", "frontend/package-lock.json", "desktop/package-lock.json")) { $Locks[$File] = Get-Sha256 (Join-Path $ProjectRoot $File) }
    $Manifest = [ordered]@{
        schemaVersion = 1
        candidate = $(if ($Installer) { "unsigned-installer" } else { "unsigned-directory" })
        platform = "win32"; arch = "x64"
        executableVersion = $ExecutableVersion
        toolchain = [ordered]@{ node = $NodeInfo.version; python = $PythonInfo.version; uv = $UvVersion; powershell = $PSVersionTable.PSVersion.ToString() }
        lockSha256 = $Locks
        nativeManifestSha256 = Get-Sha256 $NativeManifest
        smokeReportSha256 = Get-Sha256 $SmokeReport
        checks = [ordered]@{ desktopNodeTests = "passed"; nativeBackendSmoke = "passed"; installerLifecycle = $(if ($VerifyInstallerLifecycle) { "passed" } else { "not-run" }) }
        sizes = $Sizes
        limits = [ordered]@{ guiAcceptance = $false; installedAcceptance = $false; signed = $false; published = $false }
        installerReportSha256 = $(if ($VerifyInstallerLifecycle) { Get-Sha256 $InstallerReport } else { $null })
        installedAppReportSha256 = $(if ($VerifyInstallerLifecycle) { Get-Sha256 $InstalledAppReport } else { $null })
        importRecoveryReportSha256 = $(if ($VerifyInstallerLifecycle) { Get-Sha256 $ImportRecoveryReport } else { $null })
        standardUserCompatibilitySha256 = $(if ($VerifyInstallerLifecycle) { Get-Sha256 (Join-Path $CompatibilityDirectory 'compatibility.json') } else { $null })
        files = $Files
    }
    $ManifestPath = Join-Path $EvidenceRoot "windows-candidate.json"
    $Manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ManifestPath -Encoding utf8
    $Sums = @($Files | ForEach-Object { "$($_.sha256)  $($_.path)" })
    foreach ($File in @($NativeManifest, $SmokeReport, $ManifestPath)) {
        $Relative = [System.IO.Path]::GetRelativePath($ProjectRoot, $File).Replace('\', '/')
        $Sums += "$(Get-Sha256 $File)  $Relative"
    }
    if ($VerifyInstallerLifecycle) {
        $Sums += "$(Get-Sha256 $InstallerReport)  build/desktop/native-installer-smoke.json"
        $Sums += "$(Get-Sha256 $InstalledAppReport)  build/desktop/native-installed-app-smoke.json"
        $Sums += "$(Get-Sha256 (Join-Path $EvidenceRoot 'native-installed-app.png'))  build/desktop/native-installed-app.png"
        $Sums += "$(Get-Sha256 $ImportRecoveryReport)  build/desktop/native-import-recovery-smoke.json"
        foreach ($File in @(Get-ChildItem -LiteralPath $CompatibilityDirectory -File)) {
            $Relative = [IO.Path]::GetRelativePath($ProjectRoot, $File.FullName).Replace('\', '/')
            $Sums += "$(Get-Sha256 $File.FullName)  $Relative"
        }
    }
    $Sums | Set-Content -LiteralPath (Join-Path $EvidenceRoot "windows-candidate.sha256") -Encoding utf8
    Write-Host "Unsigned local candidate and safe manifest/SHA-256 evidence created under build/desktop. Windows GUI acceptance and release publication remain separate."
} finally { Pop-Location }
