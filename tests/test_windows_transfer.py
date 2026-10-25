"""Transfer must accept the updater host and reject omitted or changed bytes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from scripts import transfer_windows_candidate as transfer
from scripts import build_desktop_backend as builder
from scripts.transfer_windows_candidate import verify_host_report, verify_upgrade_origin, verify_icon_report
from scripts.upgrade_baseline import validate_baseline


BACKEND_LICENSES = ("LICENSES/MIT.txt", "docs/licensing.md", "docs/licensing.en.md")


def backend_resources(*, licenses=()):
    files = {name: f"Synthetic reviewed {name}\n".encode() for name in (
        "LICENSE", "THIRD_PARTY_NOTICES.md", "static/graph-selection.js", *licenses,
    )}
    resources = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
    resources.update({name: "c" * 64 for name in (
        "frontend/dist/index.html", "frontend/dist/assets/index.js", "frontend/dist/assets/index.css",
    )})
    return files, resources


def test_transfer_accepts_the_actual_packager_license_resources(tmp_path, monkeypatch):
    # Exercise the real packager collector against synthetic files, without
    # building the frontend/backend or reading a local activation key.
    contents = {name: f"Synthetic reviewed {name}\n".encode() for name in (
        "LICENSE", "THIRD_PARTY_NOTICES.md", "LICENSES/MIT.txt",
        "docs/licensing.md", "docs/licensing.en.md", "static/graph-selection.js",
        "frontend/dist/index.html", "frontend/dist/assets/index.js", "frontend/dist/assets/index.css",
    )}
    for name, content in contents.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder, "STATIC_NAMES", ("graph-selection.js",))
    resources = {name.replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path, name in builder.resource_files()}
    checked = []

    def source_file(name):
        checked.append(name)
        return contents[name]

    assert transfer.verify_backend_resources(resources, source_file) == 6
    assert set(checked) == {"LICENSE", "THIRD_PARTY_NOTICES.md", "LICENSES/MIT.txt",
                            "docs/licensing.md", "docs/licensing.en.md", "static/graph-selection.js"}


def test_backend_resource_transfer_preserves_historical_manifest():
    files, resources = backend_resources()
    checked = []

    def source_file(name):
        checked.append(name)
        return files[name]

    assert transfer.verify_backend_resources(resources, source_file) == len(files)
    assert set(checked) == set(files)


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_backend_resource_transfer_checks_all_three_current_license_sources(newline):
    files, resources = backend_resources(licenses=BACKEND_LICENSES)
    for name in BACKEND_LICENSES:
        resources[name] = hashlib.sha256(files[name].replace(b"\n", newline)).hexdigest()
    checked = []

    def source_file(name):
        checked.append(name)
        return files[name]

    assert transfer.verify_backend_resources(resources, source_file) == len(files)
    assert set(checked) == set(files)


@pytest.mark.parametrize("name", BACKEND_LICENSES)
def test_backend_resource_transfer_rejects_changed_license_hash(name):
    files, resources = backend_resources(licenses=BACKEND_LICENSES)
    resources[name] = hashlib.sha256(files[name] + b"unreviewed change").hexdigest()
    checked = []

    def source_file(source):
        checked.append(source)
        return files[source]

    with pytest.raises(AssertionError):
        transfer.verify_backend_resources(resources, source_file)
    assert name in checked, "The allowed license must reach source-byte verification"


@pytest.mark.parametrize("name", [
    "docs/other.md", "docs/licensing.fr.md", "docs/licensing.md/extra.md",
    "LICENSES/OTHER.txt", "LICENSES/MIT.txt.bak",
    "static/../docs/licensing.md", "frontend/dist/../index.html", "/static/graph-selection.js",
])
def test_backend_resource_transfer_rejects_other_documents_and_unsafe_paths(name):
    files, resources = backend_resources()
    resources[name] = hashlib.sha256(b"unapproved source").hexdigest()
    checked = []

    def source_file(source):
        checked.append(source)
        return files[source]

    with pytest.raises(AssertionError):
        transfer.verify_backend_resources(resources, source_file)
    assert name not in checked, "Disallowed paths must be rejected before reading source"


@pytest.mark.parametrize("change", ["missingCompiled", "extraCompiled", "missingGraphSelection"])
def test_backend_resource_transfer_preserves_compiled_count_and_required_graph_contract(change):
    files, resources = backend_resources()
    if change == "missingCompiled":
        del resources["frontend/dist/index.html"]
    elif change == "extraCompiled":
        resources["frontend/dist/assets/extra.js"] = "d" * 64
    else:
        del resources["static/graph-selection.js"]
    with pytest.raises(AssertionError):
        transfer.verify_backend_resources(resources, files.__getitem__)


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


def test_transfer_preserves_official_brand_host_with_both_exact_source_icons():
    files, report, package = fixture(True, version="0.1.9", brand=True)
    verify_host_report(report, "0.1.9", package, files.__getitem__)


SHELL_HOST_FILES = [
    "startup.cjs", "window-shell.cjs", "titlebar/index.html", "titlebar/style.css",
    "titlebar/renderer.js", "titlebar/preload.cjs", "startup/index.html", "startup/style.css",
    "startup/renderer.js", "startup/preload.cjs", "LICENSE",
]
APPEARANCE_IMPORT = b"const appearance = require('./appearance.cjs');\n"


@pytest.fixture(scope="module")
def current_host():
    root = Path(__file__).resolve().parents[1]
    # Read the actual packaging declaration, including files sourced outside
    # desktop/, without packing or loading the application.
    script = """
const path = require('node:path');
const builder = require('./desktop/electron-builder.cjs');
const files = builder.files.flatMap(entry => typeof entry === 'string'
  ? [[entry, path.join('desktop', entry)]]
  : entry.filter.map(name => [path.posix.join(entry.to, name),
      path.relative(process.cwd(), path.join(entry.from, name))]));
process.stdout.write(JSON.stringify(files));
"""
    result = subprocess.run(["node", "-e", script], cwd=root, text=True,
                            capture_output=True, check=True)
    paths = dict(json.loads(result.stdout))
    files = {name: (root / source).read_bytes() for name, source in paths.items()
             if name != "package.json"}
    report = {"status": "passed", "version": "0.1.10", "checks": {"sourceHashes": True},
              "hostFiles": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}}
    package = json.loads((root / "desktop/package.json").read_text(encoding="utf-8"))
    return files, report, package, paths


def test_transfer_accepts_actual_startup_shell_package_and_source_assets(current_host):
    files, report, package, paths = current_host
    assert paths["LICENSE"] == "LICENSE"
    verify_host_report(report, "0.1.10", package, files.__getitem__)


@pytest.mark.parametrize("name", [*SHELL_HOST_FILES, "appearance.cjs"])
@pytest.mark.parametrize("change", ["missing", "changed"])
def test_transfer_rejects_missing_or_changed_startup_shell_source(current_host, name, change):
    files, original, package, _ = current_host
    report = deepcopy(original)
    if change == "missing":
        del report["hostFiles"][name]
    else:
        report["hostFiles"][name] = hashlib.sha256(files[name] + b"changed bytes").hexdigest()
    with pytest.raises(AssertionError):
        verify_host_report(report, "0.1.10", package, files.__getitem__)


@pytest.mark.parametrize("name", ["other.cjs", "assets/unapproved.png"])
def test_startup_shell_host_rejects_unreviewed_extra_files(current_host, name):
    files, original, package, _ = current_host
    report = deepcopy(original)
    report["hostFiles"][name] = hashlib.sha256(b"unreviewed extra").hexdigest()
    with pytest.raises(AssertionError):
        verify_host_report(report, "0.1.10", package, files.__getitem__)


def test_startup_shell_version_cannot_claim_the_old_brand_host():
    files, report, package = fixture(True, version="0.1.10", brand=True)
    with pytest.raises(AssertionError):
        verify_host_report(report, "0.1.10", package, files.__getitem__)


def test_transfer_preserves_same_version_startup_shell_without_appearance():
    files, report, package = fixture(True, version="0.1.10", brand=True)
    for name in SHELL_HOST_FILES:
        files[name] = f"synthetic reviewed {name}\n".encode()
        report["hostFiles"][name] = hashlib.sha256(files[name]).hexdigest()
    verify_host_report(report, "0.1.10", package, files.__getitem__)

    # Neither an unreviewed report claim nor a commented import upgrades the
    # exact reviewed source candidate's required host resource boundary.
    report["hostFiles"]["appearance.cjs"] = hashlib.sha256(b"unreviewed appearance").hexdigest()
    with pytest.raises(AssertionError):
        verify_host_report(report, "0.1.10", package, files.__getitem__)
    files["main.cjs"] += b"// " + APPEARANCE_IMPORT
    report["hostFiles"]["main.cjs"] = hashlib.sha256(files["main.cjs"]).hexdigest()
    with pytest.raises(AssertionError):
        verify_host_report(report, "0.1.10", package, files.__getitem__)


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_reviewed_main_source_requires_appearance_even_if_report_omits_it(newline):
    files, report, package = fixture(True, version="0.1.10", brand=True)
    for name in SHELL_HOST_FILES:
        files[name] = f"synthetic reviewed {name}\n".encode()
        report["hostFiles"][name] = hashlib.sha256(files[name]).hexdigest()
    files["main.cjs"] += APPEARANCE_IMPORT.replace(b"\n", newline)
    report["hostFiles"]["main.cjs"] = hashlib.sha256(files["main.cjs"]).hexdigest()
    with pytest.raises(AssertionError):
        verify_host_report(report, "0.1.10", package, files.__getitem__)
    files["appearance.cjs"] = b"synthetic reviewed appearance\n"
    report["hostFiles"]["appearance.cjs"] = hashlib.sha256(files["appearance.cjs"]).hexdigest()
    verify_host_report(report, "0.1.10", package, files.__getitem__)


@pytest.mark.parametrize("name", SHELL_HOST_FILES)
def test_official_brand_host_cannot_claim_startup_shell_entries(name):
    files, report, package = fixture(True, version="0.1.9", brand=True)
    report["hostFiles"][name] = hashlib.sha256(b"unreviewed extra").hexdigest()
    with pytest.raises(AssertionError):
        verify_host_report(report, "0.1.9", package, files.__getitem__)


def test_transfer_reads_root_license_and_desktop_assets_from_candidate_commit(current_host, monkeypatch):
    files, report, package, paths = current_host
    commit = "a" * 40
    requested = []

    def read_source(command):
        requested.append(command)
        assert command[:2] == ["git", "show"]
        _, source = command[2].split(":", 1)
        name = next(name for name, path in paths.items() if path == source)
        return files[name]

    monkeypatch.setattr(transfer.subprocess, "check_output", read_source)
    verify_host_report(report, "0.1.10", package,
                       lambda name: transfer.source_host_file(commit, name))
    assert ["git", "show", f"{commit}:LICENSE"] in requested
    assert ["git", "show", f"{commit}:desktop/startup.cjs"] in requested


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


STARTUP_CLOSE_CHECKS = (
    "installedStartupCloseBusinessPaused", "installedStartupCloseNativeCaption",
    "installedStartupCloseCleanExit", "installedStartupCloseBackendReleased",
    "installedStartupCloseNoPageExceptions",
)


def startup_close_report(version="0.1.12"):
    return {"currentVersion": version,
            "checks": {"installedApplicationStartupClose": "passed"},
            "startupCloseChecks": dict.fromkeys(STARTUP_CLOSE_CHECKS, True)}


@pytest.mark.parametrize("version", ["0.1.12", "0.1.13"])
def test_startup_close_transfer_accepts_complete_current_and_future_reports(version):
    report = startup_close_report(version)
    for account_report in (report, deepcopy(report)):
        transfer.verify_startup_close_report(account_report, version)


@pytest.mark.parametrize("change", ["missing", False, "true", True])
def test_startup_close_transfer_rejects_missing_or_invalid_installer_gate(change):
    report = startup_close_report()
    if change == "missing":
        del report["checks"]["installedApplicationStartupClose"]
    else:
        report["checks"]["installedApplicationStartupClose"] = change
    with pytest.raises(AssertionError):
        transfer.verify_startup_close_report(report, "0.1.12")


@pytest.mark.parametrize("name", STARTUP_CLOSE_CHECKS)
@pytest.mark.parametrize("change", ["missing", False, "true", 1])
def test_startup_close_transfer_requires_each_check_to_be_boolean_true(name, change):
    report = startup_close_report()
    if change == "missing":
        del report["startupCloseChecks"][name]
    else:
        report["startupCloseChecks"][name] = change
    with pytest.raises(AssertionError):
        transfer.verify_startup_close_report(report, "0.1.12")


@pytest.mark.parametrize("change", ["missing", None, []])
def test_startup_close_transfer_rejects_missing_or_malformed_detail_dictionary(change):
    report = startup_close_report()
    if change == "missing":
        del report["startupCloseChecks"]
    else:
        report["startupCloseChecks"] = change
    with pytest.raises(AssertionError):
        transfer.verify_startup_close_report(report, "0.1.12")


@pytest.mark.parametrize("version", ["0.1.10", "0.1.11"])
def test_startup_close_transfer_preserves_legacy_report_contract(version):
    report = {"currentVersion": version, "checks": {"installedApplicationStarted": "passed"}}
    transfer.verify_startup_close_report(report, version)


def test_startup_close_transfer_rejects_another_release_target():
    with pytest.raises(AssertionError):
        transfer.verify_startup_close_report(startup_close_report("0.1.11"), "0.1.12")


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
