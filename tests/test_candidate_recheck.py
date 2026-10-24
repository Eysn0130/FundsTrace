"""Synthetic source/artifact controls; never install or contact GitHub."""
import hashlib
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import zipfile

import pytest

from scripts import candidate_recheck as checks

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "a" * 40
VERSION = "0.1.13"


def json_bytes(value):
    return (json.dumps(value, indent=2) + "\n").encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def origin():
    run = {"id": 123, "event": "workflow_dispatch", "status": "completed", "conclusion": "success",
           "path": checks.WORKFLOW, "head_sha": COMMIT,
           "repository": {"full_name": checks.REPOSITORY}, "head_repository": {"full_name": checks.REPOSITORY}}
    artifact = {"id": 456, "name": "fundstrace-windows-x64-candidate-123", "expired": False,
                "size_in_bytes": 99, "workflow_run": {"id": 123, "head_sha": COMMIT}}
    return run, artifact


@pytest.fixture
def artifact(tmp_path):
    base = tmp_path / "source" / "artifact"
    base.mkdir(parents=True)
    baseline = {"schemaVersion": 1, "targetVersion": VERSION,
                "prior": {"version": "0.1.12", "sha256": "b" * 64, "bytes": 123},
                "priorInstallerUrl": "https://github.com/Eysn0130/FundsTrace/releases/download/v0.1.12/"
                                     "FundsTrace-0.1.12-windows-x64-unsigned.exe"}
    raw_baseline = {key: value for key, value in baseline.items() if key != "priorInstallerUrl"}
    baseline["baselineSha256"] = sha(json.dumps(raw_baseline, sort_keys=True, separators=(",", ":")).encode())
    installer = b"MZ synthetic original EXE"
    native = json_bytes({"platform": "win32", "arch": "x64", "protocol": 1})
    payload = {"FundsTrace.exe": b"MZ synthetic installed EXE",
               "resources/backend/build-manifest.json": native,
               "locales/zh-CN.pak": b"synthetic locale"}
    installer_name = f"electron/FundsTrace-{VERSION}-windows-x64-unsigned.exe"
    files = {installer_name: installer, "backend/funds-trace-backend/build-manifest.json": native,
             "fresh-standard-user/compatibility.json": json_bytes({"status": "passed", "mode": "fresh",
                 "targetVersion": VERSION, "currentInstallerSha256": sha(installer),
                 "currentInstallerBytes": len(installer)})}
    file_records = [{"path": checks.PREFIX + installer_name, "bytes": len(installer), "sha256": sha(installer)}]
    file_records += [{"path": checks.PAYLOAD + name, "bytes": len(value), "sha256": sha(value)}
                     for name, value in payload.items()]
    candidate = {"schemaVersion": 1, "candidate": "unsigned-installer", "platform": "win32", "arch": "x64",
                 "checks": {"installerLifecycle": "passed", "freshInstallerLifecycle": "passed"},
                 "executableVersion": {"fileVersion": VERSION + ".0", "productVersion": VERSION},
                 "upgradeBaseline": baseline, "installerUpgradeFrom": "0.1.12", "files": file_records,
                 "nativeManifestSha256": sha(native),
                 "freshUserCompatibilitySha256": sha(files["fresh-standard-user/compatibility.json"])}
    files["windows-candidate.json"] = json_bytes(candidate)
    for name, value in files.items():
        file = base / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(value)
    sums = {checks.PREFIX + name: sha(value) for name, value in files.items()}
    sums.update({checks.PAYLOAD + name: sha(value) for name, value in payload.items()})
    (base / "windows-candidate.sha256").write_text("".join(f"{value}  {name}\n" for name, value in sums.items()))
    return {"base": base, "payload": payload, "baseline": baseline, "sha": sha(installer),
            "bytes": len(installer), "candidate": candidate}


def verify(value):
    return checks.verify_artifact(value["base"], VERSION, value["sha"], value["bytes"], value["baseline"])


def unpack(value, tmp_path):
    root = tmp_path / "unpacked"
    root.mkdir()
    for name, content in value["payload"].items():
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
    return root


def test_unique_artifact_id_is_selected_from_the_exact_successful_source(origin):
    run, artifact = origin
    result = checks.validate_source(run, [artifact], "123", COMMIT)
    assert result["artifactId"] == 456 and result["artifactName"] == artifact["name"]


def test_download_reads_the_exact_selected_artifact_id_without_a_name_based_or_write_command(artifact, origin, tmp_path, monkeypatch):
    run, item = origin
    archive = tmp_path / "api-download.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for file in artifact["base"].rglob("*"):
            if file.is_file():
                zipped.write(file, file.relative_to(artifact["base"]).as_posix())
    content = archive.read_bytes()
    item["digest"] = "sha256:" + sha(content)
    project = tmp_path / "project"
    (project / "desktop").mkdir(parents=True)
    (project / "desktop/package.json").write_bytes(json_bytes({"version": VERSION}))
    baseline = {key: artifact["baseline"][key] for key in ("schemaVersion", "targetVersion", "prior")}
    (project / "desktop/upgrade-baseline.json").write_bytes(json_bytes(baseline))
    endpoints = []
    def api(endpoint):
        endpoints.append(endpoint)
        if endpoint == "actions/runs/123":
            return run
        assert endpoint == "actions/runs/123/artifacts?per_page=100&page=1"
        return {"total_count": 1, "artifacts": [item]}
    commands = []
    def download(command, *, stdout, check):
        assert check is True
        commands.append(command)
        stdout.write(content)
    monkeypatch.setattr(checks, "gh_json", api)
    monkeypatch.setattr(checks.subprocess, "run", download)
    source_root = tmp_path / "downloaded"
    monkeypatch.setattr(sys, "argv", ["candidate_recheck.py", "download", "--run-id", "123", "--commit", COMMIT,
        "--project-root", str(project), "--source-root", str(source_root), "--sha256", artifact["sha"],
        "--bytes", str(artifact["bytes"])])
    checks.main()
    assert commands == [["gh", "api", "repos/Eysn0130/FundsTrace/actions/artifacts/456/zip"]]
    assert checks.read_json(source_root / "source.json")["artifactId"] == 456
    assert (source_root / "artifact/windows-candidate.json").read_bytes() == (artifact["base"] / "windows-candidate.json").read_bytes()


@pytest.mark.parametrize("field,wrong", [
    ("id", 999), ("event", "push"), ("status", "in_progress"), ("conclusion", "failure"),
    ("path", ".github/workflows/other.yml"), ("head_sha", "b" * 40),
    ("repository", {"full_name": "other/FundsTrace"}), ("head_repository", {"full_name": "fork/FundsTrace"}),
])
def test_wrong_source_run_cannot_supply_an_artifact(origin, field, wrong):
    run, artifact = origin
    run[field] = wrong
    with pytest.raises(ValueError):
        checks.validate_source(run, [artifact], "123", COMMIT)


@pytest.mark.parametrize("change", [
    lambda values: values.clear(), lambda values: values.append({**values[0], "id": 789}),
    lambda values: values.append(dict(values[0])), lambda values: values[0].update(expired=True),
    lambda values: values[0].update(workflow_run={"id": 999, "head_sha": COMMIT}),
    lambda values: values[0].update(workflow_run={"id": 123, "head_sha": "c" * 40}),
    lambda values: values[0].update(name="fundstrace-windows-x64-candidate-999"),
])
def test_missing_duplicate_expired_and_rebound_artifacts_fail(origin, change):
    run, artifact = origin
    values = [artifact]
    change(values)
    with pytest.raises(ValueError):
        checks.validate_source(run, values, "123", COMMIT)


def test_installer_artifact_is_verified_before_payload_exists_and_preserves_originals(artifact, tmp_path):
    manifest = (artifact["base"] / "windows-candidate.json").read_bytes()
    sums = (artifact["base"] / "windows-candidate.sha256").read_bytes()
    source = verify(artifact)
    assert not (artifact["base"] / "electron/win-unpacked").exists()
    assert checks.verify_payload(source, unpack(artifact, tmp_path))["fileCount"] == 3
    assert (artifact["base"] / "windows-candidate.json").read_bytes() == manifest
    assert (artifact["base"] / "windows-candidate.sha256").read_bytes() == sums


@pytest.mark.parametrize("fault", ["missing", "extra", "bytes", "hash", "native"])
def test_unpacked_payload_requires_the_exact_set_bytes_hashes_and_native_manifest(artifact, tmp_path, fault):
    source = verify(artifact)
    root = unpack(artifact, tmp_path)
    file = root / "FundsTrace.exe"
    if fault == "missing":
        file.unlink()
    elif fault == "extra":
        (root / "unlisted.dll").write_bytes(b"extra")
    elif fault == "bytes":
        file.write_bytes(file.read_bytes() + b"extra")
    elif fault == "hash":
        file.write_bytes(b"x" * file.stat().st_size)
    else:
        (root / "resources/backend/build-manifest.json").write_bytes(b"{}")
    with pytest.raises(ValueError):
        checks.verify_payload(source, root)


@pytest.mark.parametrize("fault", ["sha", "bytes", "evidence_missing", "evidence_extra", "nested_checksums", "manifest", "native"])
def test_original_artifact_rejects_changed_or_incomplete_evidence(artifact, fault):
    if fault == "sha":
        artifact["sha"] = "d" * 64
    elif fault == "bytes":
        artifact["bytes"] += 1
    elif fault == "evidence_missing":
        (artifact["base"] / "fresh-standard-user/compatibility.json").unlink()
    elif fault == "evidence_extra":
        (artifact["base"] / "extra.json").write_text("{}")
    elif fault == "nested_checksums":
        (artifact["base"] / "extra").mkdir()
        (artifact["base"] / "extra/windows-candidate.sha256").write_text("unbound nested checksums")
    elif fault == "manifest":
        (artifact["base"] / "windows-candidate.json").write_text("{}")
    else:
        (artifact["base"] / "backend/funds-trace-backend/build-manifest.json").write_text("{}")
    with pytest.raises(ValueError):
        verify(artifact)


def test_original_manifest_declaration_cannot_disagree_with_artifact_and_payload(artifact, tmp_path):
    source = verify(artifact)
    root = unpack(artifact, tmp_path)
    # Even if a caller tampers with the remembered checksum, the original
    # candidate declaration still has to equal both native manifest files.
    source["nativeManifestSha256"] = "d" * 64
    with pytest.raises(ValueError, match="three native"):
        checks.verify_payload(source, root)


def test_artifact_native_manifest_must_equal_the_original_candidate_declaration(artifact):
    native = artifact["base"] / "backend/funds-trace-backend/build-manifest.json"
    native.write_bytes(json_bytes({"platform": "win32", "arch": "x64", "protocol": 1, "other": "different"}))
    sums = artifact["base"] / "windows-candidate.sha256"
    lines = sums.read_text().splitlines()
    name = "build/desktop/backend/funds-trace-backend/build-manifest.json"
    sums.write_text("\n".join(f"{checks.digest(native)}  {name}" if line.endswith("  " + name) else line
                              for line in lines) + "\n")
    with pytest.raises(ValueError, match="Original native manifest differs"):
        verify(artifact)


@pytest.mark.parametrize("field,wrong", [("mode", "upgrade"), ("currentInstallerBytes", 99),
                                          ("currentInstallerSha256", "e" * 64)])
def test_original_fresh_report_cannot_claim_another_mode_or_exe(artifact, field, wrong):
    file = artifact["base"] / "fresh-standard-user/compatibility.json"
    value = checks.read_json(file)
    value[field] = wrong
    file.write_bytes(json_bytes(value))
    candidate_file = artifact["base"] / "windows-candidate.json"
    candidate = checks.read_json(candidate_file)
    candidate["freshUserCompatibilitySha256"] = checks.digest(file)
    candidate_file.write_bytes(json_bytes(candidate))
    sums = artifact["base"] / "windows-candidate.sha256"
    replacements = {"build/desktop/fresh-standard-user/compatibility.json": checks.digest(file),
                    "build/desktop/windows-candidate.json": checks.digest(candidate_file)}
    lines = []
    for line in sums.read_text().splitlines():
        digest, name = line.split("  ", 1)
        lines.append(f"{replacements.get(name, digest)}  {name}")
    sums.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="fresh-user identity"):
        verify(artifact)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/drive", "a\\b", "a/../b"])
def test_artifact_extraction_rejects_unsafe_paths(tmp_path, name):
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(name, b"no")
    with pytest.raises(ValueError):
        checks.extract_artifact(archive, tmp_path / "extract")


def test_original_artifact_changed_after_download_cannot_be_used(artifact, tmp_path):
    source = verify(artifact)
    (artifact["base"] / "windows-candidate.sha256").write_text("altered\n")
    with pytest.raises(ValueError, match="changed after download"):
        checks.verify_payload(source, unpack(artifact, tmp_path))


def test_second_run_receipt_has_candidate_source_and_no_release_claims():
    value = {"status": "passed", "source": checks.SOURCE_KIND, "rebuilt": False,
             "sourceRun": "123", "testRun": "999", "sourceCommit": COMMIT, "testCommit": COMMIT,
             "artifactId": 456, "artifactName": "fundstrace-windows-x64-candidate-123",
             "targetVersion": VERSION, "targetSha256": "a" * 64, "targetBytes": 123,
             "originalManifestSha256": "b" * 64, "originalChecksumsSha256": "c" * 64,
             "nativeManifestSha256": "d" * 64, "upgradeBaseline": {"targetVersion": VERSION},
             "reports": {"fresh/compatibility.json": {"bytes": 2, "sha256": "a" * 64}}}
    assert checks.validate_receipt(value) is value
    for update in ({"source": "published GitHub Release installer"}, {"targetReleaseTag": "v0.1.13"},
                   {"rebuilt": True}, {"testCommit": "b" * 40}, {"testRun": "123"}, {"artifactId": 0}):
        with pytest.raises(ValueError):
            checks.validate_receipt({**value, **update})


@pytest.fixture
def lifecycle_reports(artifact, tmp_path):
    source = verify(artifact)
    root = tmp_path / "reports"
    (root / "standard-user").mkdir(parents=True)
    (root / "fresh-standard-user").mkdir()
    identity = {"status": "passed", "upgradeBaseline": source["upgradeBaseline"],
                "priorVersion": "0.1.12", "currentVersion": VERSION,
                "priorInstallerSha256": source["upgradeBaseline"]["prior"]["sha256"],
                "priorInstallerBytes": source["upgradeBaseline"]["prior"]["bytes"],
                "currentInstallerSha256": source["targetSha256"], "currentInstallerBytes": source["targetBytes"],
                "checks": {"cleanup": "passed"}}
    compatibility = {"status": "passed", "checks": {"standardAccount": True, "cleanup": True}}
    fresh = {"status": "passed", "mode": "fresh", "targetVersion": VERSION,
             "currentInstallerSha256": source["targetSha256"], "currentInstallerBytes": source["targetBytes"]}
    preferences = b'{"window":{"width":1080},"sidebar":true}\n'
    preference_evidence = {"firstStartupPreferencesBase64": base64.b64encode(preferences).decode("ascii"),
                           "firstStartupPreferencesSha256": sha(preferences),
                           "firstStartupPreferencesBytes": len(preferences)}
    files = {"native-installer-smoke.json": identity,
             "native-installed-app-smoke.json": {"status": "passed"},
             "native-import-recovery-smoke.json": {"status": "passed"},
             "standard-user/native-installer-smoke.json": identity,
             "standard-user/compatibility.json": compatibility,
             "standard-user/standard-user-environment.json": {"ownTemporaryRemoved": True},
             "fresh-standard-user/compatibility.json": {**compatibility, **fresh},
             "fresh-standard-user/native-installer-smoke.json": {**fresh, **preference_evidence, "fixtureOrigin": "current-seeded",
                                                                  "checks": {"freshInstalled": "passed", "freshStartup": "passed",
                                                                             "frozenBackendFormats": "passed", "cleanup": "passed"}},
             "fresh-standard-user/fresh-startup-smoke.json": {"status": "passed", "mode": "fresh-startup",
                                                              "checks": {key: True for key in (
                     "freshStateInitiallyAbsent", "installedExecutableStarted", "authenticatedWorkspaceLoaded", "defaultCaseEmpty",
                     "activationFree", "noPageExceptions", "normalClose", "backendReleasedAfterClose", "clientCreatedDefaultData")}},
             "fresh-standard-user/installed-formats-smoke.json": {"status": "passed", "checks": {key: True for key in (
                     "fourFormatsExact", "deterministicControls", "sourceReportsAndTraces", "publicRawPackageFacts",
                     "cancellationChainExact", "lateConflictExact", "suffixlessPackageExact", "cleanProcessRestore",
                     "reimportAddsZero", "desktopAuthentication", "activationFree", "ownedRuntime", "eofShutdown", "cleanup")}}}
    for name, value in files.items():
        (root / name).write_bytes(json_bytes(value))
    (root / "fresh-standard-user/native-installed-app.png").write_bytes(b"synthetic screenshot")
    return root, source


def test_receipt_binds_fresh_upgrade_reports_and_environment_without_a_status_field(lifecycle_reports):
    root, source = lifecycle_reports
    result = checks.collect_reports(root, source)
    assert result["fresh-standard-user/native-installed-app.png"]["sha256"] == sha(b"synthetic screenshot")
    assert "standard-user/standard-user-environment.json" in result


@pytest.mark.parametrize("key", ["freshInstalled", "freshStartup", "frozenBackendFormats"])
def test_receipt_requires_each_fresh_lifecycle_step(lifecycle_reports, key):
    root, source = lifecycle_reports
    file = root / "fresh-standard-user/native-installer-smoke.json"
    value = checks.read_json(file)
    del value["checks"][key]
    file.write_bytes(json_bytes(value))
    with pytest.raises(ValueError):
        checks.collect_reports(root, source)


@pytest.mark.parametrize("folder", ["standard-user", "fresh-standard-user"])
@pytest.mark.parametrize("fault", ["missing", "false"])
def test_receipt_requires_explicit_successful_cleanup_in_both_wrappers(lifecycle_reports, folder, fault):
    root, source = lifecycle_reports
    file = root / folder / "compatibility.json"
    value = checks.read_json(file)
    if fault == "missing":
        del value["checks"]["cleanup"]
    else:
        value["checks"]["cleanup"] = False
    file.write_bytes(json_bytes(value))
    with pytest.raises(ValueError):
        checks.collect_reports(root, source)


@pytest.mark.parametrize("key", ["priorInstalled", "upgradedExistingCaseData", "legacyCasePackageCompatible"])
def test_receipt_fresh_lifecycle_cannot_claim_old_install_or_upgrade_success(lifecycle_reports, key):
    root, source = lifecycle_reports
    file = root / "fresh-standard-user/native-installer-smoke.json"
    value = checks.read_json(file)
    value["checks"][key] = "passed"
    file.write_bytes(json_bytes(value))
    with pytest.raises(ValueError):
        checks.collect_reports(root, source)


@pytest.mark.parametrize("name,field,wrong", [
    ("native-installer-smoke.json", "currentInstallerSha256", "e" * 64),
    ("standard-user/compatibility.json", "checks", {"cleanup": False}),
    ("fresh-standard-user/native-installer-smoke.json", "fixtureOrigin", "prior-version"),
    ("fresh-standard-user/fresh-startup-smoke.json", "mode", "upgrade"),
    ("fresh-standard-user/installed-formats-smoke.json", "checks", {"fourFormats": False}),
])
def test_receipt_rejects_incomplete_cleanup_or_wrong_fresh_and_upgrade_semantics(lifecycle_reports, name, field, wrong):
    root, source = lifecycle_reports
    file = root / name
    value = checks.read_json(file)
    value[field] = wrong
    file.write_bytes(json_bytes(value))
    with pytest.raises(ValueError):
        checks.collect_reports(root, source)


@pytest.mark.parametrize("name,key", [
    ("fresh-startup-smoke.json", "defaultCaseEmpty"),
    ("fresh-startup-smoke.json", "normalClose"),
    ("installed-formats-smoke.json", "cancellationChainExact"),
    ("installed-formats-smoke.json", "cleanup"),
])
def test_all_true_subset_cannot_stand_in_for_required_native_checks(lifecycle_reports, name, key):
    root, source = lifecycle_reports
    file = root / "fresh-standard-user" / name
    value = checks.read_json(file)
    del value["checks"][key]
    file.write_bytes(json_bytes(value))
    with pytest.raises(ValueError):
        checks.collect_reports(root, source)


@pytest.mark.parametrize("fault", ["missing", "base64", "sha", "bytes", "noncanonical", "too_large", "nonobject", "invalid_json"])
def test_first_startup_preferences_need_original_bounded_json_bytes(lifecycle_reports, fault):
    root, source = lifecycle_reports
    file = root / "fresh-standard-user/native-installer-smoke.json"
    value = checks.read_json(file)
    if fault == "missing":
        del value["firstStartupPreferencesBase64"]
    elif fault == "base64":
        value["firstStartupPreferencesBase64"] = "not base64!"
    elif fault == "sha":
        value["firstStartupPreferencesSha256"] = "a" * 64
    elif fault == "bytes":
        value["firstStartupPreferencesBytes"] += 1
    elif fault == "noncanonical":
        value["firstStartupPreferencesBase64"] += "\n"
    else:
        original = {"too_large": b'{"padding":"' + b"x" * 16384 + b'"}',
                    "nonobject": b"[]", "invalid_json": b'{"value":NaN}'}[fault]
        value.update(firstStartupPreferencesBase64=base64.b64encode(original).decode("ascii"),
                     firstStartupPreferencesSha256=sha(original), firstStartupPreferencesBytes=len(original))
    file.write_bytes(json_bytes(value))
    with pytest.raises(ValueError):
        checks.collect_reports(root, source)


@pytest.mark.parametrize("inputs,accepted", [
    ({"RECHECK_RUN_ID": "123"}, False), ({"RECHECK_SHA256": "a" * 64}, False),
    ({"RECHECK_BYTES": "123"}, False), ({"RECHECK_RUN_ID": "123", "RECHECK_BYTES": "123"}, False),
    ({"RECHECK_RUN_ID": "123", "RECHECK_SHA256": "a" * 64, "RECHECK_BYTES": "123"}, True),
    ({"RECHECK_RUN_ID": "123", "RECHECK_SHA256": "a" * 64, "RECHECK_BYTES": "123", "BUILD_INSTALLER": "true"}, False),
    ({"RECHECK_RUN_ID": "123", "RECHECK_SHA256": "a" * 64, "RECHECK_BYTES": "123", "IN_APP_UPDATE": "true"}, False),
    ({"RECHECK_RUN_ID": "123", "RECHECK_SHA256": "a" * 64, "RECHECK_BYTES": "123", "TARGET_BYTES": "123"}, False),
    ({"RECHECK_RUN_ID": "123", "RECHECK_SHA256": "a" * 64, "RECHECK_BYTES": "123", "UPDATE_FIXTURE_PROBE": "tls"}, False),
    ({"RECHECK_RUN_ID": "123", "RECHECK_SHA256": "a" * 64, "RECHECK_BYTES": "123", "TRANSFER_RUN_ID": "123"}, False),
])
def test_partial_or_mixed_recheck_modes_cannot_fall_through_to_build(inputs, accepted):
    workflow = (ROOT / ".github/workflows/windows-candidate.yml").read_text()
    validation = workflow.split("        shell: python\n        run: |\n", 1)[1].split("  update-fixture-probe:", 1)[0]
    environment = {**os.environ, "BUILD_INSTALLER": "false", "IN_APP_UPDATE": "false",
                   "UPDATE_FIXTURE_PROBE": "none", "TRANSFER_RUN_ID": "", "DRAFT_TAG": "",
                   "TARGET_SHA256": "", "TARGET_BYTES": "", "RECHECK_RUN_ID": "",
                   "RECHECK_SHA256": "", "RECHECK_BYTES": "", **inputs}
    result = subprocess.run([sys.executable, "-c", textwrap.dedent(validation)], env=environment,
                            text=True, capture_output=True)
    assert (result.returncode == 0) is accepted


def test_workflow_keeps_recheck_read_only_separate_and_original_fresh_reports_in_artifacts():
    workflow = (ROOT / ".github/workflows/windows-candidate.yml").read_text()
    recheck = workflow.split("\n  candidate-recheck:\n", 1)[1]
    assert "actions: read" in recheck and "contents: read" in recheck and "contents: write" not in recheck
    assert "retention-days: 90" in recheck and "fundstrace-candidate-recheck-${{ github.run_id }}" in recheck
    assert "build-windows.ps1" not in recheck and "transfer_windows_candidate.py" not in recheck
    assert recheck.count("GH_TOKEN:") == 1
    assert workflow.count("源码 FundsTrace/build/desktop/fresh-standard-user/") >= 3
    for job in ("update-fixture-probe", "windows-candidate", "transfer-verified-candidate", "published-upgrade"):
        section = workflow.split(f"\n  {job}:\n", 1)[1].split("    runs-on:", 1)[0]
        assert all(f"inputs.{field} == ''" in section for field in
                   ("candidate_recheck_run_id", "candidate_recheck_sha256", "candidate_recheck_bytes"))


def test_published_preparation_retains_release_semantics_and_recheck_never_uses_transfer():
    published = (ROOT / "scripts/test-windows-published-upgrade.ps1").read_text()
    recheck = (ROOT / "scripts/test-windows-candidate-recheck.ps1").read_text()
    assert "source = 'published GitHub Release installer'" in published and 'targetReleaseTag = "v$Version"' in published
    assert "prepare-windows-installer.ps1" in published and "prepare-windows-installer.ps1" in recheck
    assert "targetReleaseTag" not in recheck and "transfer_windows_candidate" not in recheck
    assert "-FreshInstallOnly" in recheck and "-PriorVersion $Source.upgradeBaseline.prior.version" in recheck
