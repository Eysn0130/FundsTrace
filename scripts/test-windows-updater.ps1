#Requires -Version 7.0
param([Parameter(Mandatory=$true)][string]$InstallRoot,
    [Parameter(Mandatory=$true)][string]$NextInstaller,
    [Parameter(Mandatory=$true)][string]$NextUnpacked,
    [Parameter(Mandatory=$true)][string]$ReportPath,
    [Parameter(Mandatory=$true)][string]$ScreenshotPath)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
trap {
    if ($IsWindows -and $env:GITHUB_ACTIONS -ceq 'true' -and [IO.Path]::IsPathRooted($ReportPath)) {
        @{ status='failed'; failedCheck='privateFixturePreflight'; failureType=$_.Exception.GetType().FullName;
            failureLine=$_.InvocationInfo.ScriptLineNumber } | ConvertTo-Json | Set-Content -LiteralPath $ReportPath -Encoding utf8
    }
    [Console]::Error.WriteLine('Native update cycle failed at privateFixturePreflight.')
    exit 1
}
if (-not $IsWindows -or $env:GITHUB_ACTIONS -cne 'true' -or $env:CI -cne 'true') { throw 'Disposable Windows CI only.' }
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$UserRoot = Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'FundsTrace'
if ([IO.File]::ReadAllText((Join-Path $UserRoot 'installer-lifecycle-sentinel.txt')) -cne "FundsTrace synthetic installer lifecycle v1`n") {
    throw 'Synthetic installation ownership is required.'
}
foreach ($Value in @($InstallRoot,$NextInstaller,$NextUnpacked,$ReportPath,$ScreenshotPath)) {
    if (-not [IO.Path]::IsPathRooted($Value)) { throw 'Absolute CI fixture paths required.' }
}
$Version = (Get-Content -Raw (Join-Path $ProjectRoot 'desktop/package.json') | ConvertFrom-Json).version
$Parts = $Version.Split('.')
$NextVersion = "$($Parts[0]).$($Parts[1]).$([int]$Parts[2] + 1)"
$PrivateVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo((Join-Path $NextUnpacked 'FundsTrace.exe')).ProductVersion
if (($PrivateVersion.Split('.')[0..2] -join '.') -cne $NextVersion) {
    throw 'Private fixture version mismatch.'
}
$FixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ('FundsTrace private updater ' + [guid]::NewGuid().ToString('N'))
$RootCert = $null; $LeafCert = $null; $RootTrusted = $false; $Report = $null; $Failed = $false; $Relaunched = $null
$Stage = 'privateCertificate'
try {
    [void](New-Item -ItemType Directory -Path $FixtureRoot)
    $RootCert = New-SelfSignedCertificate -Type Custom -Subject 'CN=SYN FundsTrace disposable update root' `
        -CertStoreLocation 'Cert:\CurrentUser\My' -KeyAlgorithm RSA -KeyLength 2048 -HashAlgorithm SHA256 `
        -KeyUsage CertSign,CRLSign,DigitalSignature -KeyExportPolicy Exportable `
        -TextExtension @('2.5.29.19={critical}{text}ca=true') -NotAfter ([DateTime]::UtcNow.AddDays(2))
    $LeafCert = New-SelfSignedCertificate -Type Custom -DnsName 'analytix.top','download.analytix.top' -Signer $RootCert `
        -CertStoreLocation 'Cert:\CurrentUser\My' -KeyAlgorithm RSA -KeyLength 2048 -HashAlgorithm SHA256 `
        -KeyUsage DigitalSignature,KeyEncipherment -KeyExportPolicy Exportable `
        -TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.1') -NotAfter ([DateTime]::UtcNow.AddDays(1))
    $RootFile = Join-Path $FixtureRoot 'root.cer'
    [void](Export-Certificate -Cert $RootCert -FilePath $RootFile)
    [void](Import-Certificate -FilePath $RootFile -CertStoreLocation 'Cert:\CurrentUser\Root'); $RootTrusted = $true
    $Passphrase = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
    $Pfx = Join-Path $FixtureRoot 'fixture.pfx'
    [void](Export-PfxCertificate -Cert $LeafCert -FilePath $Pfx -CryptoAlgorithmOption AES256_SHA256 -Password (ConvertTo-SecureString $Passphrase -AsPlainText -Force))
    $Fixture = Join-Path $FixtureRoot 'fixture.json'
    @{ version=$NextVersion; installer=$NextInstaller; pfx=$Pfx; passphrase=$Passphrase } |
        ConvertTo-Json | Set-Content -LiteralPath $Fixture -Encoding utf8
    $Stage = 'installedUpdateJourney'
    $Output = & node.exe (Join-Path $ProjectRoot 'desktop/tests/installed-app-smoke.cjs') $InstallRoot `
        --report $ReportPath --screenshot $ScreenshotPath --update-fixture $Fixture
    if ($LASTEXITCODE -ne 0) { throw 'Installed update journey failed.' }
    $Report = ($Output -join "`n") | ConvertFrom-Json -AsHashtable
    if ($Report.status -cne 'passed' -or $Report.updateCycle.nextVersion -cne $NextVersion) { throw 'Update report mismatch.' }
    $Stage = 'automaticRelaunch'
    $Executable = [IO.Path]::GetFullPath((Join-Path $InstallRoot 'FundsTrace.exe'))
    $QueryPath = $Executable.Replace('\','\\').Replace("'","\'")
    $Deadline = [DateTime]::UtcNow.AddSeconds(90)
    do {
        $Registration = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\b56dd6f8-a8a1-5427-b970-3e6cc301a2c0' -ErrorAction SilentlyContinue
        $InstalledVersion = $(if ($null -ne $Registration) { [string]$Registration.DisplayVersion } else { '' })
        if ($InstalledVersion -ceq $NextVersion) {
            foreach ($Owned in @(Get-CimInstance Win32_Process -Filter "ExecutablePath='$QueryPath'")) {
                $Process = Get-Process -Id $Owned.ProcessId -ErrorAction SilentlyContinue
                if ($null -ne $Process -and $Process.MainWindowHandle -ne [IntPtr]::Zero) { $Relaunched = $Process; break }
            }
        }
        if ($null -eq $Relaunched) { Start-Sleep -Milliseconds 200 }
    } while ($null -eq $Relaunched -and [DateTime]::UtcNow -lt $Deadline)
    if ($null -eq $Relaunched) { throw 'Updated application did not automatically reopen.' }
    $Stage = 'updatedInstalledResources'
    foreach ($Relative in @('FundsTrace.exe','resources/app.asar','resources/backend/build-manifest.json','resources/backend/funds-trace-backend.exe')) {
        if ((Get-FileHash -LiteralPath (Join-Path $InstallRoot $Relative)).Hash -cne (Get-FileHash -LiteralPath (Join-Path $NextUnpacked $Relative)).Hash) {
            throw 'Installed update differs from the private candidate.'
        }
    }
    $Report.checks['installedUpdateAutoRelaunch'] = $true
    $Report.checks['installedUpdateExactResources'] = $true
    $Stage = 'updatedNormalClose'
    if (-not $Relaunched.CloseMainWindow() -or -not $Relaunched.WaitForExit(30000)) { throw 'Updated application did not close normally.' }
    $Report.checks['installedUpdateRelaunchNormalClose'] = $true
} catch {
    $Failed = $true
    if ($null -eq $Report -and (Test-Path -LiteralPath $ReportPath)) { $Report = Get-Content -Raw -LiteralPath $ReportPath | ConvertFrom-Json -AsHashtable }
    if ($null -eq $Report) { $Report=@{ status='failed'; failedCheck=$Stage } }
    if ($Report.status -cne 'failed') { $Report.status='failed'; $Report.failedCheck=$Stage }
    $Report['wrapperFailedCheck']=$Stage
    $Report['failureType']=$_.Exception.GetType().FullName
    $Report['failureLine']=$_.InvocationInfo.ScriptLineNumber
    $Report['failureId']=$(if ($_.FullyQualifiedErrorId -cmatch '^[A-Za-z0-9_.,:+-]{1,160}$') { $_.FullyQualifiedErrorId } else { 'UNCLASSIFIED' })
    [Console]::Error.WriteLine("Native update cycle failed at $Stage.")
} finally {
    try {
        if ($null -ne $Relaunched -and -not $Relaunched.HasExited) {
            [void]$Relaunched.CloseMainWindow()
            if (-not $Relaunched.WaitForExit(30000)) { throw 'Owned updated application remains active.' }
        }
        if ($RootTrusted) { Remove-Item -LiteralPath ('Cert:\CurrentUser\Root\' + $RootCert.Thumbprint) }
        foreach ($Certificate in @($LeafCert,$RootCert)) {
            if ($null -ne $Certificate) { Remove-Item -LiteralPath ('Cert:\CurrentUser\My\' + $Certificate.Thumbprint) -DeleteKey }
        }
        if (Test-Path -LiteralPath $FixtureRoot) { Remove-Item -LiteralPath $FixtureRoot -Recurse -Force }
    } catch { $Failed=$true; if ($null -ne $Report) { $Report.status='failed'; $Report.failedCheck='privateFixtureCleanup' } }
    if ($null -ne $Report) { $Report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $ReportPath -Encoding utf8 }
}
if ($Failed) { exit 1 }
$Report | ConvertTo-Json -Depth 12 -Compress
exit 0
