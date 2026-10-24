"""Static CI orchestration guards; native Windows execution remains a separate gate."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_fresh_gui_precedes_fixture_generation_and_current_backend_seed():
    source = (ROOT / 'scripts/test-windows-installer.ps1').read_text()
    start = source.index("    if ($FreshInstallOnly) {\n        $Stage = 'freshInstalled'")
    first = source[start:source.index("    } else {\n    $Stage = 'priorInstallerDigest'", start)]
    positions = [first.index(value) for value in (
        'Assert-Installed $CurrentVersion', 'Assert-InstalledResources',
        "$OwnUserData = $true", "@('data', 'preferences.json')", "'--fresh-startup'",
        "$FreshStartup.status -cne 'passed'", "$Report.firstStartupPreferencesSha256",
        'scripts/windows_import_fixtures.py', 'desktop/tests/installed-formats-smoke.cjs',
    )]
    assert positions == sorted(positions)
    assert '$PriorInstaller -RawArguments' not in first
    assert "FUND_DATA_DIR" not in first
    assert "--mode', 'seed'" not in first
    assert source.index("$Stage = 'syntheticDataSeeded'", start) > start + len(first)
    assert "[switch]$FreshInstallOnly" in source
    assert '$FreshInstallOnly -and ($PublishedInAppUpdate -or $NextInstaller -or $NextUnpacked -or $TrustedCertificateFixture)' in source


def test_fresh_report_retains_distinct_mode_identity_and_preference_merge():
    source = (ROOT / 'scripts/test-windows-installer.ps1').read_text()
    assert "$Report.mode = 'fresh'; $Report.fixtureOrigin = 'current-seeded'" in source
    assert "'fresh-startup-smoke.json'" in source
    assert "'installed-formats-smoke.json'" in source
    assert '$Report.firstStartupPreferencesSha256' in source
    assert '$BeforeMerge -cne $Report.firstStartupPreferencesSha256' in source
    assert 'foreach ($Key in $FixturePreferences.Keys) { $ClientPreferences[$Key] = $FixturePreferences[$Key] }' in source
    assert 'foreach ($Key in $UnchangedPreferences.Keys)' in source
    assert 'otherClientKeysPreserved = $true' in source
    assert '$Report.checks.Remove($Name)' in source
    assert "'priorInstalled'" in source and "'legacyCasePackageCompatible'" in source
    # Failure cleanup retains the established ownership and normal uninstall bounds.
    assert 'if ($MayHaveInstalled -and $null -ne $InstallRoot)' in source
    assert 'if ($OwnUserData -and (Test-Path -LiteralPath $UserRoot))' in source
    assert 'System.IO.FileAttributes]::ReparsePoint' in source


def test_standard_user_passes_fresh_flag_and_checks_installer_bound_receipt():
    source = (ROOT / 'scripts/test-windows-standard-user.ps1').read_text()
    assert '[switch]$FreshInstallOnly' in source
    assert '-FreshInstallOnly:__FRESH_INSTALL__' in source
    assert "$Template.Replace('__FRESH_INSTALL__'" in source
    assert "$Report.mode = 'fresh'" in source
    for value in ('$Lifecycle.mode', '$Lifecycle.fixtureOrigin', '$Lifecycle.targetVersion',
                  '$Lifecycle.currentVersion', '$Lifecycle.currentInstallerSha256',
                  '$Lifecycle.currentInstallerBytes', '$Lifecycle.checks.cleanup'):
        assert value in source
    assert 'foreach ($Check in $Lifecycle.checks.PSObject.Properties)' in source
    # Disposable SID and profile isolation remain shared with the old upgrade branch.
    assert "'FT兼容' + $RunId.Substring(0, 10)" in source
    assert 'Remove-LocalUser -SID $User.SID' in source
    assert 'if ($Profile.Loaded)' in source
