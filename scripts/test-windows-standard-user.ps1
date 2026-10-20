#Requires -Version 7.0
param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [Parameter(Mandatory = $true)][string]$ReportDirectory,
    [ValidateSet('0.1.5', '0.1.7')][string]$PriorVersion = '0.1.5',
    [string]$NextInstaller = '',
    [string]$NextUnpacked = '',
    [string]$TrustedCertificateFixture = ''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# Disposable hosted runner only: never create users or firewall rules on a workstation.
if (-not $IsWindows -or $env:GITHUB_ACTIONS -cne 'true' -or -not $env:RUNNER_TEMP -or
    [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString() -ne 'X64') {
    throw 'Standard-user compatibility requires native Windows x64 GitHub Actions.'
}
$Principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Runner administration is required for disposable test setup.' }
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$InstallerPath = [IO.Path]::GetFullPath($InstallerPath)
$ReportDirectory = [IO.Path]::GetFullPath($ReportDirectory)
if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf) -or (Test-Path -LiteralPath $ReportDirectory)) {
    throw 'Require an existing installer and a new report directory.'
}
$RunId = [guid]::NewGuid().ToString('N')
$AccountName = 'FT兼容' + $RunId.Substring(0, 10)
$StageRoot = Join-Path $env:RUNNER_TEMP ('FundsTrace 普通用户 ' + $RunId)
$LifecycleRoot = Join-Path $StageRoot '安装 生命周期'
$InstallRoot = Join-Path $LifecycleRoot '应用 安装路径'
$Report = [ordered]@{ schemaVersion = 1; status = 'running'; failedCheck = $null; checks = [ordered]@{};
    limits = @{ platform = 'Windows Server 2022'; windows10And11Interactive = $false;
        networkIsolation = 'installed Electron and backend outbound firewall block; loopback retained'; systemDpi = $false } }
$Stage = 'setup'; $User = $null; $Child = $null; $Failed = $false
$RuleNames = @(); $Profiles = @(); $RootOwned = $false
function Quote-Literal([string]$Value) { "'" + $Value.Replace("'", "''") + "'" }
try {
    [void](New-Item -ItemType Directory -Path $ReportDirectory)
    [void](New-Item -ItemType Directory -Path $StageRoot); $RootOwned = $true
    $Stage = 'standardAccount'
    $Password = [Security.SecureString]::new()
    foreach ($Character in ('Aa1!' + [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(36))).ToCharArray()) {
        $Password.AppendChar($Character)
    }
    $Password.MakeReadOnly()
    $User = New-LocalUser -Name $AccountName -Password $Password -AccountNeverExpires -Description 'Disposable FundsTrace CI compatibility account'
    Add-LocalGroupMember -SID ([Security.Principal.SecurityIdentifier]::new('S-1-5-32-545')) -Member $User
    $Credential = [Management.Automation.PSCredential]::new(($env:COMPUTERNAME + '\' + $AccountName), $Password)
    foreach ($Directory in @($StageRoot, $ReportDirectory)) {
        $Acl = Get-Acl -LiteralPath $Directory
        $Acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($User.SID, 'Modify', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
        Set-Acl -LiteralPath $Directory -AclObject $Acl
    }
    $Report.checks.standardAccount = $true
    $Stage = 'offlineRules'
    $Profiles = @(Get-NetFirewallProfile | Select-Object Name, Enabled)
    foreach ($Profile in $Profiles) { Set-NetFirewallProfile -Name $Profile.Name -Enabled True }
    $ActiveProfiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore)
    if ($ActiveProfiles.Count -ne 3 -or @($ActiveProfiles | Where-Object { [string]$_.Enabled -cne 'True' }).Count -gt 0) {
        throw 'All effective firewall profiles must be enabled for isolation.'
    }
    $Report.checks.firewallProfilesEnabled = $true
    foreach ($Program in @((Join-Path $InstallRoot 'FundsTrace.exe'), (Join-Path $InstallRoot 'resources/backend/funds-trace-backend.exe'))) {
        $Name = 'FundsTrace-CI-' + $RunId + '-' + $RuleNames.Count
        [void](New-NetFirewallRule -Name $Name -DisplayName $Name -Direction Outbound -Action Block -Program $Program -Profile Any)
        $RuleNames += $Name
        $Rule = Get-NetFirewallRule -Name $Name -PolicyStore ActiveStore
        if ($Rule.Enabled -ne 'True' -or $Rule.Action -ne 'Block' -or $Rule.Direction -ne 'Outbound') { throw 'Network isolation was not active.' }
    }
    $Report.checks.offlineRules = $true
    $Stage = 'standardUserLifecycle'
    $ChildScript = Join-Path $StageRoot 'run.ps1'
    $Template = @'
$ErrorActionPreference = 'Stop'
try {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = [Security.Principal.WindowsPrincipal]::new($Identity)
    if ($Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Expected standard token.' }
    $env:USERPROFILE = [Environment]::GetFolderPath('UserProfile')
    $env:APPDATA = [Environment]::GetFolderPath('ApplicationData')
    # CreateProcessWithLogonW inherits the invoking runner's environment. This
    # fresh disposable profile uses the normal local AppData location explicitly.
    $env:LOCALAPPDATA = Join-Path $env:USERPROFILE 'AppData\Local'
    if (-not $env:USERPROFILE.Contains('FT兼容') -or -not $env:APPDATA.StartsWith($env:USERPROFILE, [StringComparison]::OrdinalIgnoreCase)) { throw 'Expected own Unicode profile.' }
    $env:TEMP = __STAGE__; $env:TMP = $env:TEMP
    $env:CI = 'true'; $env:GITHUB_ACTIONS = 'true'
    [void](New-Item -ItemType Directory -Path $env:LOCALAPPDATA -Force)
    $CacheProbe = Join-Path $env:LOCALAPPDATA ('FundsTrace CI cache probe ' + [guid]::NewGuid().ToString('N'))
    try {
        [void](New-Item -ItemType Directory -Path $CacheProbe)
        [IO.File]::WriteAllText((Join-Path $CacheProbe 'synthetic.txt'), 'SYN cache write probe')
    } finally { if (Test-Path -LiteralPath $CacheProbe) { Remove-Item -LiteralPath $CacheProbe -Recurse -Force } }
    @{ standardToken = $true; unicodeProfile = $true; ownAppData = $true; ownLocalAppData = $true;
        localCacheBaseWritable = $true } | ConvertTo-Json | Set-Content -LiteralPath __ENV_REPORT__ -Encoding utf8
    $Result = 1
    try {
        & __LIFECYCLE__ -InstallerPath __INSTALLER__ -ReportPath __REPORT__ -CiTemporaryRoot __LIFECYCLE_ROOT__ -PriorVersion __PRIOR_VERSION__ -NextInstaller __NEXT_INSTALLER__ -NextUnpacked __NEXT_UNPACKED__ -TrustedCertificateFixture __CERTIFICATE_FIXTURE__
        $Result = $LASTEXITCODE
    } finally {
        # Installer-private temp files belong to this token. Clean them before
        # the parent removes this account, without widening their ACLs.
        Remove-Item -LiteralPath $env:TEMP -Recurse -Force
        @{ standardToken = $true; unicodeProfile = $true; ownAppData = $true; ownLocalAppData = $true;
            localCacheBaseWritable = $true; ownTemporaryRemoved = $true } | ConvertTo-Json | Set-Content -LiteralPath __ENV_REPORT__ -Encoding utf8
    }
    exit $Result
} catch { [Console]::Error.WriteLine('Standard-user compatibility child failed.'); exit 1 }
'@
    $Replacements = @{
        '__STAGE__' = $StageRoot; '__ENV_REPORT__' = (Join-Path $ReportDirectory 'standard-user-environment.json')
        '__LIFECYCLE__' = (Join-Path $PSScriptRoot 'test-windows-installer.ps1'); '__INSTALLER__' = $InstallerPath
        '__REPORT__' = (Join-Path $ReportDirectory 'native-installer-smoke.json'); '__LIFECYCLE_ROOT__' = $LifecycleRoot
        '__PRIOR_VERSION__' = $PriorVersion
        '__NEXT_INSTALLER__' = $NextInstaller; '__NEXT_UNPACKED__' = $NextUnpacked
        '__CERTIFICATE_FIXTURE__' = $TrustedCertificateFixture
    }
    foreach ($Key in $Replacements.Keys) { $Template = $Template.Replace($Key, (Quote-Literal $Replacements[$Key])) }
    $Tokens = $null; $ParseErrors = $null
    [void][Management.Automation.Language.Parser]::ParseInput($Template, [ref]$Tokens, [ref]$ParseErrors)
    if ($ParseErrors.Count -gt 0) { throw 'Standard-user test script could not be parsed.' }
    [IO.File]::WriteAllText($ChildScript, $Template)
    $Child = Start-Process -FilePath (Join-Path $PSHOME 'pwsh.exe') -ArgumentList @('-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $ChildScript + '"')) -Credential $Credential -LoadUserProfile -WorkingDirectory $ProjectRoot -PassThru
    if (-not $Child.WaitForExit(900000)) { throw 'Standard-user lifecycle timed out.' }
    if ($Child.ExitCode -ne 0) { throw 'Standard-user lifecycle failed.' }
    $Environment = Get-Content -Raw -LiteralPath (Join-Path $ReportDirectory 'standard-user-environment.json') | ConvertFrom-Json
    foreach ($Name in @('standardToken', 'unicodeProfile', 'ownAppData', 'ownLocalAppData', 'localCacheBaseWritable', 'ownTemporaryRemoved')) { if ($Environment.$Name -ne $true) { throw 'Standard-user environment check failed.' } }
    $Lifecycle = Get-Content -Raw -LiteralPath (Join-Path $ReportDirectory 'native-installer-smoke.json') | ConvertFrom-Json
    if ($Lifecycle.status -cne 'passed') { throw 'Standard-user installation checks failed.' }
    $Report.checks.standardUserLifecycle = $true
} catch {
    $Failed = $true; $Report.failedCheck = $Stage
    [Console]::Error.WriteLine("Standard-user compatibility failed at $Stage.")
} finally {
    try {
        $Stage = 'removeFirewallRules'
        foreach ($Name in $RuleNames) { Remove-NetFirewallRule -Name $Name }
        $Report.checks.firewallRulesRemoved = $true
        $Stage = 'restoreFirewallProfiles'
        foreach ($Profile in $Profiles) { Set-NetFirewallProfile -Name $Profile.Name -Enabled $Profile.Enabled }
        $Report.checks.firewallProfilesRestored = $true
        $Stage = 'releaseTestProcess'
        if ($null -ne $Child -and -not $Child.HasExited) { throw 'Owned test process remains active; do not delete its profile.' }
        if ($null -ne $Child) { $Child.Dispose(); $Child = $null }
        $Report.checks.testProcessClosed = $true
        if ($null -ne $User) {
            $Stage = 'unloadTestProfile'
            $Deadline = [DateTime]::UtcNow.AddSeconds(60)
            do {
                $Profile = Get-CimInstance Win32_UserProfile -Filter ("SID='" + $User.SID.Value + "'")
                if ($null -eq $Profile -or -not $Profile.Loaded) { break }
                # Profile service cleanup can finish after the final process exits.
                # Wait for that owned SID; never force-unload a hive or kill by name.
                Start-Sleep -Milliseconds 500
            } while ([DateTime]::UtcNow -lt $Deadline)
            if ($null -ne $Profile) {
                if ($Profile.Loaded) { throw 'Owned test profile remains loaded.' }
                $Profile | Remove-CimInstance
            }
            $Report.checks.testProfileRemoved = $true
            $Stage = 'removeTestAccount'
            Remove-LocalUser -SID $User.SID
            $Report.checks.testAccountRemoved = $true
        }
        $Stage = 'removeTestTemporary'
        if ($RootOwned -and (Test-Path -LiteralPath $StageRoot)) { Remove-Item -LiteralPath $StageRoot -Recurse -Force }
        $Report.checks.testTemporaryRemoved = $true
        $Report.checks.cleanup = $true
    } catch {
        $Failed = $true; if (-not $Report.failedCheck) { $Report.failedCheck = $Stage }
        $Report.cleanupFailureType = $_.Exception.GetType().FullName
    }
    if ($null -ne $Child) { $Child.Dispose() }
    $Report.status = $(if ($Failed) { 'failed' } else { 'passed' })
    $Report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $ReportDirectory 'compatibility.json') -Encoding utf8
}
if ($Failed) { exit 1 }
Write-Host 'Standard-user Unicode profile, program-isolated offline installed startup, upgrade and uninstall retention passed on Windows Server.'
