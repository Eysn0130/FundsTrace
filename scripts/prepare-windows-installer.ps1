#Requires -Version 7.0
param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [Parameter(Mandatory = $true)][string]$InputRoot,
    [Parameter(Mandatory = $true)][string]$Version
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $IsWindows -or $env:GITHUB_ACTIONS -cne 'true' -or
    [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString() -ne 'X64') {
    throw 'Installer preparation requires native Windows x64 GitHub Actions.'
}
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$CandidateRoot = Join-Path $ProjectRoot 'build/desktop/electron/win-unpacked'
$ManifestRoot = Join-Path $ProjectRoot 'build/desktop/backend/funds-trace-backend'
$ArchiveRoot = Join-Path $InputRoot 'nsis'
foreach ($Directory in @($CandidateRoot, $ManifestRoot, $ArchiveRoot)) {
    if (Test-Path -LiteralPath $Directory) { throw 'Installer preparation requires new isolated directories.' }
}
if ($Version -cnotmatch '^\d+\.\d+\.\d+$' -or -not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) {
    throw 'Installer preparation requires an existing stable-version EXE.'
}
$SevenZip = (Get-Command 7z.exe -CommandType Application | Select-Object -First 1).Path
if (-not [IO.File]::Exists($SevenZip)) { throw '7-Zip is unavailable.' }
$IconHelper = Join-Path $ProjectRoot 'desktop/tests/windows-icon-resources.cjs'
$ExpectedIcon = Join-Path $ProjectRoot 'desktop/assets/icon.ico'
$InstallerIcon = (& node $IconHelper $InstallerPath $ExpectedIcon) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $InstallerIcon.status -cne 'passed') { throw 'Installer icon differs from its current version contract.' }
& $SevenZip x $InstallerPath "-o$ArchiveRoot" -y | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not read the NSIS package.' }
$Payload = Join-Path $ArchiveRoot '$PLUGINSDIR/app-64.7z'
if (-not (Test-Path -LiteralPath $Payload -PathType Leaf)) { throw 'NSIS x64 payload is missing.' }
& $SevenZip x $Payload "-o$CandidateRoot" -y | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not unpack the application reference.' }
$Executable = Join-Path $CandidateRoot 'FundsTrace.exe'
$Info = (Get-Item -LiteralPath $Executable).VersionInfo
if (($Info.FileVersion.Split('.')[0..2] -join '.') -cne $Version -or ($Info.ProductVersion.Split('.')[0..2] -join '.') -cne $Version) {
    throw 'Application version differs from the source test contract.'
}
$ApplicationIcon = (& node $IconHelper $Executable $ExpectedIcon) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $ApplicationIcon.status -cne 'passed') { throw 'Application icon differs from its current version contract.' }
[void](New-Item -ItemType Directory -Path $ManifestRoot)
Copy-Item -LiteralPath (Join-Path $CandidateRoot 'resources/backend/build-manifest.json') -Destination $ManifestRoot
[pscustomobject]@{
    candidateRoot = $CandidateRoot; installerIcon = $InstallerIcon; applicationIcon = $ApplicationIcon
    extractedApplicationSha256 = (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash.ToLowerInvariant()
}
