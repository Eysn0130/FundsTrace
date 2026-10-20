"""Transfer must accept the updater host and reject omitted or changed bytes."""
from copy import deepcopy
import hashlib

import pytest

from scripts.transfer_windows_candidate import verify_host_report, verify_upgrade_origin


def fixture(updater):
    names = ["main.cjs", "backend.cjs", "host-policy.cjs", "preferences.cjs", "preload.cjs"]
    if updater:
        names.append("updates.cjs")
    files = {name: f"synthetic reviewed {name}\n".encode() for name in names}
    report = {"status": "passed", "version": "0.1.8", "checks": {"sourceHashes": True},
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


def test_upgrade_origin_keeps_legacy_reports_and_requires_pinned_v017_digest():
    assert verify_upgrade_origin({}, {"status": "passed", "priorVersion": "0.1.5"}) == "0.1.5"
    assert verify_upgrade_origin({"installerUpgradeFrom": "0.1.7"}, {
        "status": "passed", "priorVersion": "0.1.7",
        "priorInstallerSha256": "52eabd3c6b0f77270c61066b8d1830b84cb5b316153d399625c7d21badfbeb7a",
    }) == "0.1.7"


@pytest.mark.parametrize("change", ["missingDigest", "wrongDigest", "wrongVersion", "failed", "unapprovedVersion"])
def test_upgrade_origin_rejects_mismatched_or_unpinned_installer_reports(change):
    candidate = {"installerUpgradeFrom": "0.1.7"}
    report = {"status": "passed", "priorVersion": "0.1.7",
              "priorInstallerSha256": "52eabd3c6b0f77270c61066b8d1830b84cb5b316153d399625c7d21badfbeb7a"}
    if change == "missingDigest":
        del report["priorInstallerSha256"]
    elif change == "wrongDigest":
        report["priorInstallerSha256"] = "0" * 64
    elif change == "wrongVersion":
        report["priorVersion"] = "0.1.5"
    elif change == "failed":
        report["status"] = "failed"
    else:
        candidate["installerUpgradeFrom"] = "0.1.6"
    with pytest.raises(AssertionError):
        verify_upgrade_origin(candidate, report)
