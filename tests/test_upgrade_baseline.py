"""Fail closed when release-pair inputs or installed evidence drift."""
from copy import deepcopy
import json
import os
import textwrap
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import upgrade_baseline


ROOT = Path(__file__).resolve().parents[1]
PRIOR_SHA256 = "fc9c6513079b0ac456b0dabe47b0f8a175eaf9b3b62eceda77a8abddaf6776aa"
CURRENT_SHA256 = "e" * 64


@pytest.fixture
def baseline():
    return {"schemaVersion": 1, "targetVersion": "0.1.7", "prior": {
        "version": "0.1.6", "sha256": PRIOR_SHA256, "bytes": 160013027}}


def test_checked_in_pair_and_cli_are_bound_to_the_target_desktop_package():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/upgrade_baseline.py"),
                             "--baseline", str(ROOT / "desktop/upgrade-baseline.json"),
                             "--package", str(ROOT / "desktop/package.json")],
                            text=True, capture_output=True, check=True)
    value = json.loads(result.stdout)
    package = json.loads((ROOT / "desktop/package.json").read_text(encoding="utf-8"))
    declared = json.loads((ROOT / "desktop/upgrade-baseline.json").read_text(encoding="utf-8"))
    assert value["targetVersion"] == package["version"] == declared["targetVersion"]
    assert value["prior"] == declared["prior"]


def test_sample_pair_uses_only_the_owned_release_url(baseline):
    value = upgrade_baseline.validate_baseline(baseline, "0.1.7")
    assert value["priorInstallerUrl"] == ("https://github.com/Eysn0130/FundsTrace/releases/download/"
                                          "v0.1.6/FundsTrace-0.1.6-windows-x64-unsigned.exe")


def test_portable_baseline_identity_ignores_json_whitespace_and_checkout_newlines(baseline, tmp_path):
    one = tmp_path / "lf.json"
    two = tmp_path / "crlf.json"
    one.write_text(json.dumps(baseline) + "\n", encoding="utf-8")
    two.write_bytes(("\ufeff" + json.dumps(baseline, indent=2) + "\n").replace("\n", "\r\n").encode("utf-8"))
    assert upgrade_baseline.load_baseline(one, "0.1.7") == upgrade_baseline.load_baseline(two, "0.1.7")


def test_missing_malformed_and_duplicate_baseline_files_are_rejected(tmp_path):
    file = tmp_path / "baseline.json"
    with pytest.raises(FileNotFoundError):
        upgrade_baseline.load_baseline(file, "0.1.7")
    for content in ["{", '{"schemaVersion":1,"schemaVersion":2,"targetVersion":"0.1.7","prior":{}}']:
        file.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError):
            upgrade_baseline.load_baseline(file, "0.1.7")


@pytest.mark.parametrize("change", [
    lambda b: b.pop("prior"),
    lambda b: b.update(extra="unreviewed"),
    lambda b: b.update(schemaVersion=True),
    lambda b: b.update(schemaVersion=2),
    lambda b: b.update(targetVersion="0.1.6"),
    lambda b: b.update(targetVersion="0.1.7-beta.1"),
    lambda b: b["prior"].pop("bytes"),
    lambda b: b["prior"].update(url="https://example.invalid/installer.exe"),
    lambda b: b["prior"].update(version="0.1.7"),
    lambda b: b["prior"].update(version="0.1.8"),
    lambda b: b["prior"].update(version="00.1.6"),
    lambda b: b["prior"].update(version="0.1.1\u0666"),
    lambda b: b["prior"].update(sha256="A" * 64),
    lambda b: b["prior"].update(sha256="a" * 63),
    lambda b: b["prior"].update(sha256=123),
    lambda b: b["prior"].update(bytes=True),
    lambda b: b["prior"].update(bytes=0),
    lambda b: b["prior"].update(bytes=-1),
    lambda b: b["prior"].update(bytes=160013027.0),
    lambda b: b["prior"].update(bytes=2**53),
])
def test_incomplete_invalid_or_unreviewed_baseline_is_rejected(baseline, change):
    change(baseline)
    with pytest.raises(ValueError):
        upgrade_baseline.validate_baseline(baseline, "0.1.7")


def test_bumping_the_package_requires_an_explicit_baseline_update(baseline, tmp_path):
    file = tmp_path / "baseline.json"
    file.write_text(json.dumps(baseline), encoding="utf-8")
    package = tmp_path / "package.json"
    package.write_text('{"version":"0.1.8"}', encoding="utf-8")
    result = subprocess.run([sys.executable, str(ROOT / "scripts/upgrade_baseline.py"),
                             "--baseline", str(file), "--package", str(package)],
                            text=True, capture_output=True)
    assert result.returncode != 0 and not result.stdout


def test_release_order_uses_numeric_versions_instead_of_lexical_order(baseline):
    baseline["targetVersion"] = "0.1.10"
    baseline["prior"]["version"] = "0.1.9"
    assert upgrade_baseline.validate_baseline(baseline, "0.1.10")["prior"]["version"] == "0.1.9"


def test_transfer_report_identity_rejects_another_pair_installer_or_missing_evidence(baseline):
    identity = upgrade_baseline.validate_baseline(baseline, "0.1.7")
    report = {"upgradeBaseline": identity, "priorVersion": "0.1.6", "currentVersion": "0.1.7",
              "priorInstallerSha256": PRIOR_SHA256, "priorInstallerBytes": 160013027,
              "currentInstallerSha256": CURRENT_SHA256, "currentInstallerBytes": 160000000}
    upgrade_baseline.validate_installer_identity(report, identity, CURRENT_SHA256, 160000000)
    for field, wrong in [("priorVersion", "0.1.5"), ("currentVersion", "0.1.8"),
                         ("priorInstallerSha256", "f" * 64), ("priorInstallerBytes", 160013026),
                         ("currentInstallerSha256", "a" * 64), ("currentInstallerBytes", 160000001)]:
        changed = {**report, field: wrong}
        with pytest.raises(ValueError):
            upgrade_baseline.validate_installer_identity(changed, identity, CURRENT_SHA256, 160000000)
    missing = dict(report)
    del missing["upgradeBaseline"]
    with pytest.raises(ValueError):
        upgrade_baseline.validate_installer_identity(missing, identity, CURRENT_SHA256, 160000000)
    changed = deepcopy(report)
    changed["upgradeBaseline"]["schemaVersion"] = True
    with pytest.raises(ValueError):
        upgrade_baseline.validate_installer_identity(changed, identity, CURRENT_SHA256, 160000000)


@pytest.mark.parametrize("inputs,accepted", [
    ({}, True),
    ({"BUILD_INSTALLER": "true"}, True),
    ({"BUILD_INSTALLER": "true", "IN_APP_UPDATE": "true"}, True),
    ({"IN_APP_UPDATE": "true"}, False),
    ({"OFFICIAL_NETWORK_DIAGNOSTIC": "true"}, False),
    ({"OFFICIAL_NETWORK_DIAGNOSTIC": "true", "BUILD_INSTALLER": "true", "IN_APP_UPDATE": "true"}, False),
    ({"OFFICIAL_NETWORK_DIAGNOSTIC": "true", "TARGET_SHA256": "a" * 64, "TARGET_BYTES": "1234"}, False),
    ({"OFFICIAL_NETWORK_DIAGNOSTIC": "true", "TARGET_SHA256": "a" * 64, "TARGET_BYTES": "1234", "IN_APP_UPDATE": "true"}, True),
    ({"OFFICIAL_NETWORK_DIAGNOSTIC": "true", "TARGET_SHA256": "a" * 64, "TARGET_BYTES": "1234", "IN_APP_UPDATE": "true", "UPDATE_FIXTURE_PROBE": "tls"}, False),
    ({"UPDATE_FIXTURE_PROBE": "tls"}, True),
    ({"UPDATE_FIXTURE_PROBE": "dialog", "BUILD_INSTALLER": "true"}, False),
    ({"TRANSFER_RUN_ID": "123", "DRAFT_TAG": "v0.1.9"}, True),
    ({"TRANSFER_RUN_ID": "123"}, False),
    ({"DRAFT_TAG": "v0.1.9"}, False),
    ({"TARGET_SHA256": "a" * 64, "TARGET_BYTES": "1234"}, True),
    ({"TARGET_SHA256": "a" * 64}, False),
    ({"TARGET_BYTES": "1234"}, False),
    ({"TARGET_SHA256": "a" * 64, "TARGET_BYTES": str(2**53)}, False),
    ({"TARGET_SHA256": "a" * 64, "TARGET_BYTES": "1234", "BUILD_INSTALLER": "true"}, False),
    ({"TARGET_SHA256": "a" * 64, "TARGET_BYTES": "1234", "IN_APP_UPDATE": "true"}, True),
    ({"TARGET_SHA256": "a" * 64, "IN_APP_UPDATE": "true"}, False),
    ({"TRANSFER_RUN_ID": "123", "DRAFT_TAG": "v0.1.9", "IN_APP_UPDATE": "true"}, False),
    ({"UPDATE_FIXTURE_PROBE": "tls", "IN_APP_UPDATE": "true"}, False),
    ({"TARGET_SHA256": "a" * 64, "TARGET_BYTES": "1234", "UPDATE_FIXTURE_PROBE": "tls"}, False),
    ({"TARGET_SHA256": "a" * 64, "TARGET_BYTES": "1234", "TRANSFER_RUN_ID": "123", "DRAFT_TAG": "v0.1.9"}, False),
])
def test_workflow_rejects_mixed_or_incomplete_manual_modes_before_windows_jobs(inputs, accepted):
    workflow = (ROOT / ".github/workflows/windows-candidate.yml").read_text(encoding="utf-8")
    validation = workflow.split("        shell: python\n        run: |\n", 1)[1].split("  update-fixture-probe:", 1)[0]
    environment = {**os.environ, "BUILD_INSTALLER": "false", "IN_APP_UPDATE": "false", "OFFICIAL_NETWORK_DIAGNOSTIC": "false",
                   "UPDATE_FIXTURE_PROBE": "none", "TRANSFER_RUN_ID": "", "DRAFT_TAG": "",
                   "TARGET_SHA256": "", "TARGET_BYTES": "", **inputs}
    result = subprocess.run([sys.executable, "-c", textwrap.dedent(validation)], env=environment,
                            text=True, capture_output=True)
    assert (result.returncode == 0) is accepted
