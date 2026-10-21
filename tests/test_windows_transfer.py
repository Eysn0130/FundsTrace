"""Transfer must accept the updater host and reject omitted or changed bytes."""
from copy import deepcopy
import hashlib

import pytest

from scripts.transfer_windows_candidate import verify_host_report, verify_upgrade_origin, verify_icon_report
from scripts.upgrade_baseline import validate_baseline


def fixture(updater, *, version="0.1.8", brand=False):
    names = ["main.cjs", "backend.cjs", "host-policy.cjs", "preferences.cjs", "preload.cjs"]
    if updater:
        names.append("updates.cjs")
    files = {name: f"synthetic reviewed {name}\n".encode() for name in names}
    if brand:
        # Both fixtures include CRLF; binary assets must never receive the text
        # checkout normalization allowed for reviewed JavaScript sources.
        files.update({"assets/icon.png": b"\x89PNG\r\n\x1a\nSYN icon pixels\r\n",
                      "assets/icon.ico": b"\x00\x00\x01\x00SYN icon frames\r\n"})
    report = {"status": "passed", "version": version, "checks": {"sourceHashes": True},
              "hostFiles": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}
    package = {"dependencies": {"electron-updater": "6.8.9"}} if updater else {}
    return files, report, package


@pytest.mark.parametrize("updater", [False, True])
def test_transfer_preserves_historical_hosts_and_verifies_updater_hosts(updater):
    files, report, package = fixture(updater)
    verify_host_report(report, "0.1.8", package, files.__getitem__)


@pytest.mark.parametrize("change", ["missing", "unexpected", "changed", "failed"])
def test_transfer_rejects_incomplete_or_unreviewed_updater_payloads(change):
    files, original, package = fixture(True)
    report = deepcopy(original)
    if change == "missing":
        del report["hostFiles"]["updates.cjs"]
    elif change == "unexpected":
        report["hostFiles"]["other.cjs"] = "0" * 64
    elif change == "changed":
        report["hostFiles"]["updates.cjs"] = hashlib.sha256(b"unreviewed update command").hexdigest()
    else:
        report["checks"]["sourceHashes"] = False
    with pytest.raises(AssertionError):
        verify_host_report(report, "0.1.8", package, files.__getitem__)


@pytest.mark.parametrize("version", ["0.1.9", "0.1.10"])
def test_transfer_accepts_current_host_with_both_exact_source_icons(version):
    files, report, package = fixture(True, version=version, brand=True)
    verify_host_report(report, version, package, files.__getitem__)


@pytest.mark.parametrize("name", ["assets/icon.png", "assets/icon.ico"])
@pytest.mark.parametrize("change", ["missing", "changed", "normalized"])
def test_transfer_rejects_missing_changed_or_newline_normalized_brand_asset(name, change):
    files, report, package = fixture(True, version="0.1.9", brand=True)
    if change == "missing":
        del report["hostFiles"][name]
    elif change == "changed":
        report["hostFiles"][name] = hashlib.sha256(files[name] + b"changed pixels").hexdigest()
    else:
        report["hostFiles"][name] = hashlib.sha256(files[name].replace(b"\r\n", b"\n")).hexdigest()
    with pytest.raises(AssertionError):
        verify_host_report(report, "0.1.9", package, files.__getitem__)


def test_current_version_cannot_omit_both_brand_assets_as_a_legacy_host():
    files, report, package = fixture(True, version="0.1.9")
    with pytest.raises(AssertionError):
        verify_host_report(report, "0.1.9", package, files.__getitem__)


@pytest.mark.parametrize("name", ["other.cjs", "assets/unapproved.png"])
def test_current_brand_host_rejects_unreviewed_extra_files(name):
    files, report, package = fixture(True, version="0.1.9", brand=True)
    report["hostFiles"][name] = hashlib.sha256(b"unreviewed extra").hexdigest()
    with pytest.raises(AssertionError):
        verify_host_report(report, "0.1.9", package, files.__getitem__)


def test_historical_host_cannot_claim_new_brand_entries():
    files, report, package = fixture(True, brand=True)
    with pytest.raises(AssertionError):
        verify_host_report(report, "0.1.8", package, files.__getitem__)


def upgrade_fixture():
    baseline = validate_baseline({"schemaVersion": 1, "targetVersion": "0.1.9", "prior": {
        "version": "0.1.8", "sha256": "a" * 64, "bytes": 1234}}, "0.1.9")
    candidate = {"installerUpgradeFrom": "0.1.8", "upgradeBaseline": deepcopy(baseline)}
    report = {"status": "passed", "upgradeBaseline": deepcopy(baseline),
              "priorVersion": "0.1.8", "currentVersion": "0.1.9", "priorInstallerSha256": "a" * 64,
              "priorInstallerBytes": 1234, "currentInstallerSha256": "b" * 64, "currentInstallerBytes": 2345}
    return baseline, candidate, report


def test_upgrade_origin_binds_both_accounts_to_candidate_baseline_and_current_installer():
    baseline, candidate, report = upgrade_fixture()
    for account_report in (report, deepcopy(report)):
        assert verify_upgrade_origin(candidate, account_report, baseline, "b" * 64, 2345) == "0.1.8"


@pytest.mark.parametrize("field,wrong", [
    ("priorVersion", "0.1.7"), ("currentVersion", "0.1.8"),
    ("priorInstallerSha256", "c" * 64), ("priorInstallerBytes", 2345),
    ("currentInstallerSha256", "a" * 64), ("currentInstallerBytes", 1234),
    ("upgradeBaseline", {}), ("status", "failed"),
])
def test_upgrade_origin_rejects_mismatched_account_identity(field, wrong):
    baseline, candidate, report = upgrade_fixture()
    report[field] = wrong
    with pytest.raises((AssertionError, ValueError)):
        verify_upgrade_origin(candidate, report, baseline, "b" * 64, 2345)


@pytest.mark.parametrize("change", ["missingBaseline", "otherBaseline", "oldVersion", "missingOrigin"])
def test_upgrade_origin_rejects_undeclared_or_stale_candidate_baseline(change):
    baseline, candidate, report = upgrade_fixture()
    if change == "missingBaseline":
        del candidate["upgradeBaseline"]
    elif change == "otherBaseline":
        candidate["upgradeBaseline"]["prior"]["bytes"] += 1
    elif change == "oldVersion":
        candidate["installerUpgradeFrom"] = "0.1.5"
    else:
        del candidate["installerUpgradeFrom"]
    with pytest.raises((AssertionError, KeyError)):
        verify_upgrade_origin(candidate, report, baseline, "b" * 64, 2345)


def icon_fixture():
    icon = {"status": "passed", "selection": "default-icon-index-0", "expectedIcoSha256": "c" * 64,
            "verifiedLanguageVariants": 1, "frames": [{"width": 32, "height": 32, "sha256": "d" * 64}]}
    report = {"status": "passed", "application": {**icon, "executableSha256": "a" * 64},
              "installer": {**icon, "executableSha256": "b" * 64}}
    candidate = {"files": [{"path": "build/desktop/electron/win-unpacked/FundsTrace.exe", "sha256": "a" * 64}]}
    return report, candidate, {"sha256": "b" * 64}


def test_icon_transfer_requires_approved_icon_and_exact_application_and_installer():
    report, candidate, installer = icon_fixture()
    verify_icon_report(report, candidate, installer, "c" * 64)


@pytest.mark.parametrize("field,wrong", [("expectedIcoSha256", "e" * 64),
    ("executableSha256", "e" * 64), ("selection", "another-hidden-group"),
    ("verifiedLanguageVariants", 0), ("verifiedLanguageVariants", True), ("frames", []), ("status", "failed")])
@pytest.mark.parametrize("entry", ["application", "installer"])
def test_icon_transfer_rejects_other_executable_unapproved_or_incomplete_pe_evidence(entry, field, wrong):
    report, candidate, installer = icon_fixture()
    report[entry][field] = wrong
    with pytest.raises(AssertionError):
        verify_icon_report(report, candidate, installer, "c" * 64)
