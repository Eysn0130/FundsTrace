"""Public release evidence is immutable, complete, and bound to its candidate."""
from pathlib import Path
import hashlib
import json
import zipfile

import pytest

from scripts.release_evidence import (
    CANDIDATE_REPORTS, EVIDENCE_FILES, STANDARD_FILES, prepare_evidence, verify_evidence,
)


TAG = "v0.1.6"
SOURCE = "a" * 40
RUN = "39295484779"


def sha(value):
    return hashlib.sha256(value).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


@pytest.fixture
def candidate(tmp_path):
    base = tmp_path / "desktop"
    base.mkdir()
    for name, relative in EVIDENCE_FILES.items():
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\nsynthetic\x00" if name.endswith(".png") else b'{"status":"passed","note":"synthetic"}\r\n')
    installer = {"path": "build/desktop/electron/FundsTrace-0.1.6-windows-x64-unsigned.exe",
                 "bytes": 12, "sha256": sha(b"MZ synthetic")}
    standard = {name: (b"\x89PNG\r\nstandard\x00" if name.endswith(".png") else b'{"synthetic":true}\r\n')
                for name in STANDARD_FILES}
    with zipfile.ZipFile(base / "standard-user-compatibility.zip", "w") as archive:
        for name, value in sorted(standard.items()):
            archive.writestr(name, value)
    candidate_manifest = {
        "schemaVersion": 1, "candidate": "unsigned-installer", "platform": "win32", "arch": "x64",
        "checks": {"installerLifecycle": "passed"},
        "executableVersion": {"fileVersion": "0.1.6", "productVersion": "0.1.6.0"}, "files": [installer],
        **{key: sha((base / EVIDENCE_FILES[name]).read_bytes()) for key, name in CANDIDATE_REPORTS.items()},
        "standardUserCompatibilitySha256": sha(standard["compatibility.json"]),
    }
    write_json(base / "windows-candidate.json", candidate_manifest)
    sums = {"build/desktop/" + relative: sha((base / relative).read_bytes())
            for name, relative in EVIDENCE_FILES.items()
            if name not in {"windows-candidate.sha256", "standard-user-compatibility.zip", "release-verification.json"}}
    sums[installer["path"]] = installer["sha256"]
    sums.update({"build/desktop/standard-user/" + name: sha(value) for name, value in standard.items()})
    (base / "windows-candidate.sha256").write_bytes(
        ("\r\n".join(f"{digest}  {name}" for name, digest in sums.items()) + "\r\n").encode())
    write_json(base / "release-verification.json", {
        "schemaVersion": 1, "status": "passed", "tag": TAG, "sourceCommit": SOURCE, "sourceRun": RUN,
        "installer": installer, "standardUserArchiveSha256": sha((base / "standard-user-compatibility.zip").read_bytes()),
    })
    return base, installer


def prepare(candidate, output, **kwargs):
    base, installer = candidate
    return prepare_evidence(base, output, tag=TAG, source_commit=SOURCE, source_run=RUN,
                            installer=installer, **kwargs)


def rehash(directory, name):
    path = directory / "evidence" / name
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    entry = next(item for item in manifest["files"] if item["path"] == "evidence/" + name)
    entry.update(bytes=path.stat().st_size, sha256=sha(path.read_bytes()))
    write_json(manifest_path, manifest)
    # README is deterministic and does not include hashes or sizes of individual evidence files.


def test_archive_preserves_only_public_evidence_byte_for_byte(candidate, tmp_path):
    base, installer = candidate
    (base / "electron").mkdir()
    (base / "electron/private-runtime.duckdb").write_bytes(b"never archive this")
    output = tmp_path / "archive"
    manifest = prepare(candidate, output)
    assert len(manifest["files"]) == 12
    assert manifest["installer"] == {"name": Path(installer["path"]).name, "bytes": 12, "sha256": installer["sha256"]}
    for name, relative in EVIDENCE_FILES.items():
        assert (output / "evidence" / name).read_bytes() == (base / relative).read_bytes()
    assert {path.name for path in output.iterdir()} == {"README.md", "manifest.json", "evidence"}
    assert verify_evidence(output) == manifest
    assert not list(output.rglob("*.exe")) and not list(output.rglob("*.duckdb"))


@pytest.mark.parametrize("change", ["corrupt", "missing", "extra", "symlink"])
def test_verification_rejects_changed_archive(candidate, tmp_path, change):
    output = tmp_path / "archive"
    prepare(candidate, output)
    path = output / "evidence/native-installed-app.png"
    if change == "corrupt":
        path.write_bytes(path.read_bytes() + b"changed")
    elif change == "missing":
        path.unlink()
    elif change == "extra":
        (output / "evidence/unapproved.json").write_text("{}")
    else:
        real = tmp_path / "elsewhere.png"
        real.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(real)
    with pytest.raises(ValueError):
        verify_evidence(output)


@pytest.mark.parametrize("relative", ["../native-installed-app.png", "/tmp/other", "evidence/../other", "evidence\\other", "evidence/C:other"])
def test_verification_rejects_manifest_paths(candidate, tmp_path, relative):
    output = tmp_path / "archive"
    prepare(candidate, output)
    path = output / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["files"][0]["path"] = relative
    write_json(path, manifest)
    with pytest.raises(ValueError):
        verify_evidence(output)


@pytest.mark.parametrize("linked", ["file", "directory"])
def test_prepare_rejects_symlink_sources(candidate, tmp_path, linked):
    base, _ = candidate
    if linked == "file":
        source = base / "native-installed-app.png"
        real = tmp_path / "real.png"
        real.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(real)
    else:
        original = base / "backend"
        real = tmp_path / "real-backend"
        original.rename(real)
        original.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        prepare(candidate, tmp_path / "archive")


def test_prepare_requires_new_output_and_preserves_existing(candidate, tmp_path):
    output = tmp_path / "archive"
    output.mkdir()
    (output / "keep.txt").write_text("do not replace")
    with pytest.raises(ValueError, match="new"):
        prepare(candidate, output)
    assert (output / "keep.txt").read_text() == "do not replace"


@pytest.mark.parametrize("field,value", [
    ("path", "build/desktop/electron/FundsTrace-0.1.5-windows-x64-unsigned.exe"),
    ("bytes", True), ("sha256", "not-a-digest"),
])
def test_prepare_rejects_installer_metadata_for_another_package(candidate, tmp_path, field, value):
    base, installer = candidate
    installer[field] = value
    with pytest.raises(ValueError):
        prepare((base, installer), tmp_path / "archive")


@pytest.mark.parametrize("key,value", [("sourceCommit", "b" * 40), ("sourceRun", "123"), ("tag", "v0.1.5")])
def test_verification_binds_release_to_source_and_run(candidate, tmp_path, key, value):
    output = tmp_path / "archive"
    prepare(candidate, output)
    path = output / "evidence/release-verification.json"
    summary = json.loads(path.read_text())
    summary[key] = value
    write_json(path, summary)
    rehash(output, path.name)
    with pytest.raises(ValueError, match="mismatch"):
        verify_evidence(output)


def test_verification_binds_installer_candidate_and_summary(candidate, tmp_path):
    output = tmp_path / "archive"
    prepare(candidate, output)
    path = output / "evidence/release-verification.json"
    summary = json.loads(path.read_text())
    summary["installer"]["sha256"] = "b" * 64
    write_json(path, summary)
    rehash(output, path.name)
    with pytest.raises(ValueError, match="installer mismatch"):
        verify_evidence(output)


def test_verification_checks_zip_members_against_original_hashes(candidate, tmp_path):
    output = tmp_path / "archive"
    prepare(candidate, output)
    path = output / "evidence/standard-user-compatibility.zip"
    with zipfile.ZipFile(path) as original:
        contents = {name: original.read(name) for name in original.namelist()}
    contents["native-installed-app.png"] += b"changed"
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in contents.items():
            archive.writestr(name, value)
    summary_path = output / "evidence/release-verification.json"
    summary = json.loads(summary_path.read_text())
    summary["standardUserArchiveSha256"] = sha(path.read_bytes())
    write_json(summary_path, summary)
    rehash(output, path.name)
    rehash(output, summary_path.name)
    with pytest.raises(ValueError, match="Standard-user original checksum mismatch"):
        verify_evidence(output)


def test_optional_original_release_snapshot_is_complete_and_bound(candidate, tmp_path):
    base, installer = candidate
    assets = [{"name": name, "size": (base / relative).stat().st_size,
               "digest": "sha256:" + sha((base / relative).read_bytes())}
              for name, relative in EVIDENCE_FILES.items()]
    assets.append({"name": Path(installer["path"]).name, "size": installer["bytes"], "digest": "sha256:" + installer["sha256"]})
    snapshot = {"tag_name": TAG, "body": "原发布正文\n保留原始验证记录。", "assets": assets}
    output = tmp_path / "archive"
    manifest = prepare(candidate, output, release_snapshot=snapshot)
    assert len(manifest["files"]) == 13
    assert json.loads((output / "evidence/release.original.json").read_text()) == snapshot
    snapshot["assets"][0]["digest"] = "sha256:" + "b" * 64
    with pytest.raises(ValueError, match="Original Release asset digest mismatch"):
        prepare(candidate, tmp_path / "bad-snapshot", release_snapshot=snapshot)


OPTIONAL_EVIDENCE = {
    name: name for name in (
        "native-icon-resources.json", "native-installed-app.taskbar.png",
        "native-installed-app.update-downloading.png", "native-installed-app.update-ready.png",
    )
}
UPDATE_SCREENSHOTS = {"native-installed-app.update-downloading.png", "native-installed-app.update-ready.png"}
TASKBAR_SCREENSHOT = "native-installed-app.taskbar.png"


def standard_contents(base):
    with zipfile.ZipFile(base / "standard-user-compatibility.zip") as archive:
        return {item.filename: archive.read(item) for item in archive.infolist()}


def write_standard(base, contents):
    with zipfile.ZipFile(base / "standard-user-compatibility.zip", "w") as archive:
        for name, value in sorted(contents.items()):
            archive.writestr(name, value)


def rebind_candidate(base, installer, *, tag="v0.1.9"):
    """Recompute independent fixture hashes after an intentional source edit."""
    value = json.loads((base / "windows-candidate.json").read_text())
    value["executableVersion"] = {"fileVersion": tag[1:], "productVersion": tag[1:] + ".0"}
    value["files"] = [installer]
    for key, name in CANDIDATE_REPORTS.items():
        value[key] = sha((base / EVIDENCE_FILES[name]).read_bytes())
    if "iconResourcesReportSha256" in value and (base / "native-icon-resources.json").exists():
        value["iconResourcesReportSha256"] = sha((base / "native-icon-resources.json").read_bytes())
    standard = standard_contents(base)
    value["standardUserCompatibilitySha256"] = sha(standard["compatibility.json"])
    write_json(base / "windows-candidate.json", value)
    sums = {"build/desktop/" + relative: sha((base / relative).read_bytes())
            for name, relative in {**EVIDENCE_FILES, **OPTIONAL_EVIDENCE}.items()
            if name not in {"windows-candidate.sha256", "standard-user-compatibility.zip", "release-verification.json"}
            and (base / relative).exists()}
    sums[installer["path"]] = installer["sha256"]
    sums.update({"build/desktop/standard-user/" + name: sha(content) for name, content in standard.items()})
    (base / "windows-candidate.sha256").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items())))
    write_json(base / "release-verification.json", {
        "schemaVersion": 1, "status": "passed", "tag": tag, "sourceCommit": SOURCE, "sourceRun": RUN,
        "installer": installer, "standardUserArchiveSha256": sha((base / "standard-user-compatibility.zip").read_bytes()),
    })


def modern_candidate(candidate, *, main_capture="captured", standard_capture="captured", update=True):
    base, old_installer = candidate
    installer = {**old_installer, "path": "build/desktop/electron/FundsTrace-0.1.9-windows-x64-unsigned.exe"}
    value = json.loads((base / "windows-candidate.json").read_text())
    value["checks"]["nativeIconResources"] = "passed"
    value["iconResourcesReportSha256"] = "0" * 64
    value["inAppUpdateFixture"] = update
    write_json(base / "windows-candidate.json", value)
    resource = {"schemaVersion": 1, "status": "passed", "selection": "default-icon-index-0"}
    write_json(base / "native-icon-resources.json", {
        "schemaVersion": 1, "status": "passed", "application": resource, "installer": resource,
        "limits": {"taskbarAppearance": False, "windows10And11Interactive": False},
    })
    standard = standard_contents(base)
    for account, capture_status in (("main", main_capture), ("standard", standard_capture)):
        content = b"\x89PNG\r\n" + account.encode() + b" taskbar\x00"
        capture = ({"status": "captured", "width": 1024, "height": 40,
                    "sha256": sha(content), "reviewed": False}
                   if capture_status == "captured" else {"status": "unavailable", "reason": capture_status})
        window = {"schemaVersion": 1, "status": "passed", "mode": "visibleWindowIcons",
                  "checks": {name: "passed" for name in (
                      "inputBinding", "nativeIconReader", "installedProcessBinding", "visibleWindowIcons")}}
        app = {"status": "passed", "checks": {"installedWorkspaceWindowIcon": True, "installedAnalysisWindowIcon": True},
               "workspaceWindowIcons": {**window, "taskbarCapture": capture}, "analysisWindowIcons": window}
        shortcuts = {"schemaVersion": 1, "status": "passed", "mode": "shortcuts",
                     "checks": {name: "passed" for name in (
                         "inputBinding", "nativeIconReader", "desktopShortcut", "startMenuShortcut")}}
        installed = {"status": "passed", "checks": {"installedExecutableIcons": "passed", "installedShortcutIcons": "passed"},
                     "iconResources": {"FundsTrace.exe": resource, "Uninstall FundsTrace.exe": resource}, "shortcutIcons": shortcuts}
        if account == "main":
            write_json(base / "native-installed-app-smoke.json", app)
            write_json(base / "native-installer-smoke.json", installed)
            if capture_status == "captured":
                (base / TASKBAR_SCREENSHOT).write_bytes(content)
            if update:
                for name in UPDATE_SCREENSHOTS:
                    (base / name).write_bytes(b"\x89PNG\r\n" + name.encode())
        else:
            standard["native-installed-app-smoke.json"] = (json.dumps(app) + "\n").encode()
            standard["native-installer-smoke.json"] = (json.dumps(installed) + "\n").encode()
            if capture_status == "captured":
                standard[TASKBAR_SCREENSHOT] = content
            if update:
                standard.update({name: b"\x89PNG\r\nstandard " + name.encode() for name in UPDATE_SCREENSHOTS})
    write_standard(base, standard)
    rebind_candidate(base, installer)
    return base, installer


def prepare_modern(candidate, output, **kwargs):
    base, installer = candidate
    return prepare_evidence(base, output, tag="v0.1.9", source_commit=SOURCE, source_run=RUN,
                            installer=installer, **kwargs)


def test_archive_includes_declared_update_icon_and_both_accounts_taskbar(candidate, tmp_path):
    modern = modern_candidate(candidate)
    output = tmp_path / "modern"
    manifest = prepare_modern(modern, output)
    assert {item["path"].removeprefix("evidence/") for item in manifest["files"]} == set(EVIDENCE_FILES) | set(OPTIONAL_EVIDENCE)
    with zipfile.ZipFile(output / "evidence/standard-user-compatibility.zip") as archive:
        assert set(archive.namelist()) == STANDARD_FILES | UPDATE_SCREENSHOTS | {TASKBAR_SCREENSHOT}
    assert verify_evidence(output) == manifest


@pytest.mark.parametrize("main_capture,standard_capture", [
    ("captured", "captured"), ("captured", "SHELL_TASKBAR_UNAVAILABLE"),
    ("TASKBAR_CAPTURE_FAILED", "captured"), ("SHELL_TASKBAR_UNAVAILABLE", "TASKBAR_CAPTURE_FAILED"),
])
def test_taskbar_requirements_follow_each_account_report(candidate, tmp_path, main_capture, standard_capture):
    modern = modern_candidate(candidate, main_capture=main_capture, standard_capture=standard_capture, update=False)
    output = tmp_path / "archive"
    manifest = prepare_modern(modern, output)
    assert ("evidence/" + TASKBAR_SCREENSHOT in {item["path"] for item in manifest["files"]}) == (main_capture == "captured")
    with zipfile.ZipFile(output / "evidence/standard-user-compatibility.zip") as archive:
        assert (TASKBAR_SCREENSHOT in archive.namelist()) == (standard_capture == "captured")


@pytest.mark.parametrize("change", ["check-only", "digest-only", "check-failed", "digest-invalid", "v019-downgrade"])
def test_icon_declarations_must_be_paired_and_cannot_be_downgraded(candidate, tmp_path, change):
    base, installer = modern_candidate(candidate, update=False)
    path = base / "windows-candidate.json"
    value = json.loads(path.read_text())
    if change in {"check-only", "v019-downgrade"}:
        value.pop("iconResourcesReportSha256")
    if change in {"digest-only", "v019-downgrade"}:
        value["checks"].pop("nativeIconResources")
    if change == "check-failed":
        value["checks"]["nativeIconResources"] = "not-run"
    if change == "digest-invalid":
        value["iconResourcesReportSha256"] = "wrong"
    write_json(path, value)
    # Refresh only the candidate's original checksum, preserving the deliberately invalid declaration.
    sums = (base / "windows-candidate.sha256").read_text().splitlines()
    (base / "windows-candidate.sha256").write_text("\n".join(
        f"{sha(path.read_bytes())}  build/desktop/windows-candidate.json" if line.endswith("  build/desktop/windows-candidate.json") else line
        for line in sums) + "\n")
    with pytest.raises(ValueError):
        prepare_modern((base, installer), tmp_path / "archive")


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_update_declaration_must_be_boolean(candidate, tmp_path, value):
    base, installer = modern_candidate(candidate, update=False)
    path = base / "windows-candidate.json"
    data = json.loads(path.read_text())
    data["inAppUpdateFixture"] = value
    write_json(path, data)
    rebind_candidate(base, installer)
    with pytest.raises(ValueError):
        prepare_modern((base, installer), tmp_path / "archive")


@pytest.mark.parametrize("account,name", [
    ("main", "native-icon-resources.json"), ("main", TASKBAR_SCREENSHOT),
    ("standard", TASKBAR_SCREENSHOT), ("main", "native-installed-app.update-downloading.png"),
    ("main", "native-installed-app.update-ready.png"), ("standard", "native-installed-app.update-downloading.png"),
    ("standard", "native-installed-app.update-ready.png"),
])
def test_declared_artifacts_cannot_be_omitted(candidate, tmp_path, account, name):
    base, installer = modern_candidate(candidate)
    if account == "main":
        (base / name).unlink()
    else:
        contents = standard_contents(base)
        del contents[name]
        write_standard(base, contents)
    rebind_candidate(base, installer)
    with pytest.raises(ValueError):
        prepare_modern((base, installer), tmp_path / "archive")


@pytest.mark.parametrize("account", ["main", "standard"])
@pytest.mark.parametrize("change", ["capture-digest", "capture-status", "reviewed", "unknown-reason", "unavailable-with-sha", "unavailable-with-png"])
def test_taskbar_report_and_artifact_must_agree(candidate, tmp_path, account, change):
    unavailable = change in {"unknown-reason", "unavailable-with-sha", "unavailable-with-png"}
    statuses = {"main_capture": "captured", "standard_capture": "captured"}
    if unavailable:
        statuses[account + "_capture"] = "SHELL_TASKBAR_UNAVAILABLE"
    base, installer = modern_candidate(candidate, update=False, **statuses)
    contents = standard_contents(base)
    path = base / "native-installed-app-smoke.json"
    app = json.loads(path.read_text() if account == "main" else contents[path.name])
    capture = app["workspaceWindowIcons"]["taskbarCapture"]
    if change == "capture-digest":
        capture["sha256"] = "f" * 64
    elif change == "capture-status":
        capture["status"] = "not-run"
    elif change == "reviewed":
        capture["reviewed"] = True
    elif change == "unknown-reason":
        capture["reason"] = "arbitrary"
    elif change == "unavailable-with-sha":
        capture["sha256"] = "f" * 64
    else:
        if account == "main":
            (base / TASKBAR_SCREENSHOT).write_bytes(b"unexpected capture")
        else:
            contents[TASKBAR_SCREENSHOT] = b"unexpected capture"
    if account == "main":
        write_json(path, app)
    else:
        contents[path.name] = (json.dumps(app) + "\n").encode()
    write_standard(base, contents)
    rebind_candidate(base, installer)
    with pytest.raises(ValueError):
        prepare_modern((base, installer), tmp_path / "archive")


@pytest.mark.parametrize("account,report,field", [
    ("main", "native-installer-smoke.json", "iconResources"),
    ("standard", "native-installer-smoke.json", "shortcutIcons"),
    ("main", "native-installed-app-smoke.json", "workspaceWindowIcons"),
    ("standard", "native-installed-app-smoke.json", "analysisWindowIcons"),
    ("main", "native-icon-resources.json", "application"),
])
def test_declared_icon_reports_require_passed_nested_reports(candidate, tmp_path, account, report, field):
    base, installer = modern_candidate(candidate, update=False)
    contents = standard_contents(base)
    data = json.loads((base / report).read_text() if account == "main" else contents[report])
    if field == "iconResources":
        data[field]["FundsTrace.exe"]["status"] = "failed"
    else:
        data[field]["status"] = "failed"
    if account == "main":
        write_json(base / report, data)
    else:
        contents[report] = (json.dumps(data) + "\n").encode()
        write_standard(base, contents)
    rebind_candidate(base, installer)
    with pytest.raises(ValueError):
        prepare_modern((base, installer), tmp_path / "archive")


@pytest.mark.parametrize("account", ["main", "standard"])
@pytest.mark.parametrize("change", ["missing-installer-check", "failed-window-check"])
def test_declared_icon_reports_require_their_checks(candidate, tmp_path, account, change):
    base, installer = modern_candidate(candidate, update=False)
    contents = standard_contents(base)
    name = "native-installer-smoke.json" if change == "missing-installer-check" else "native-installed-app-smoke.json"
    data = json.loads((base / name).read_text() if account == "main" else contents[name])
    if change == "missing-installer-check":
        del data["checks"]["installedExecutableIcons"]
    else:
        data["workspaceWindowIcons"]["checks"]["installedProcessBinding"] = "failed"
    if account == "main":
        write_json(base / name, data)
    else:
        contents[name] = (json.dumps(data) + "\n").encode()
        write_standard(base, contents)
    rebind_candidate(base, installer)
    with pytest.raises(ValueError):
        prepare_modern((base, installer), tmp_path / "archive")


@pytest.mark.parametrize("change", ["icon-candidate-sha", "icon-original-sha", "main-taskbar-original-sha", "standard-taskbar-original-sha"])
def test_declared_artifacts_are_bound_to_candidate_checksums(candidate, tmp_path, change):
    base, installer = modern_candidate(candidate, update=False)
    if change == "icon-candidate-sha":
        path = base / "windows-candidate.json"
        data = json.loads(path.read_text())
        data["iconResourcesReportSha256"] = "f" * 64
        write_json(path, data)
        name = "build/desktop/windows-candidate.json"
        digest = sha(path.read_bytes())
    else:
        name = "build/desktop/" + {
            "icon-original-sha": "native-icon-resources.json",
            "main-taskbar-original-sha": TASKBAR_SCREENSHOT,
            "standard-taskbar-original-sha": "standard-user/" + TASKBAR_SCREENSHOT,
        }[change]
        digest = "f" * 64
    path = base / "windows-candidate.sha256"
    path.write_text("\n".join(f"{digest}  {name}" if line.endswith("  " + name) else line
                              for line in path.read_text().splitlines()) + "\n")
    with pytest.raises(ValueError):
        prepare_modern((base, installer), tmp_path / "archive")


@pytest.mark.parametrize("attack", ["duplicate", "traversal", "backslash", "directory", "symlink", "encrypted", "nul"])
def test_standard_zip_rejects_unsafe_or_ambiguous_members(candidate, tmp_path, attack):
    base, installer = candidate
    path = base / "standard-user-compatibility.zip"
    contents = standard_contents(base)
    names = {"traversal": "../outside.json", "backslash": "folder\\outside.json", "directory": "extra/",
             "nul": "compatibility.jsonXevil"}
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in contents.items():
            info = zipfile.ZipInfo(names.get(attack, name) if name == "compatibility.json" else name)
            if attack == "symlink" and name == "compatibility.json":
                info.external_attr = 0o120777 << 16
            archive.writestr(info, value)
        if attack == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate"):
                archive.writestr("compatibility.json", contents["compatibility.json"])
    value = path.read_bytes()
    if attack == "encrypted":
        # Mark all local and central records encrypted; no read should be attempted.
        value = value.replace(b"PK\x03\x04\x14\x00\x00\x00", b"PK\x03\x04\x14\x00\x01\x00")
        value = value.replace(b"PK\x01\x02\x14\x03\x14\x00\x00\x00", b"PK\x01\x02\x14\x03\x14\x00\x01\x00")
    elif attack == "nul":
        value = value.replace(b"compatibility.jsonXevil", b"compatibility.json\x00evil")
    path.write_bytes(value)
    summary_path = base / "release-verification.json"
    summary = json.loads(summary_path.read_text())
    summary["standardUserArchiveSha256"] = sha(value)
    write_json(summary_path, summary)
    with pytest.raises(ValueError):
        prepare(candidate, tmp_path / "archive")


def test_v019_cannot_claim_only_the_historical_twelve_files(candidate, tmp_path):
    base, old_installer = candidate
    installer = {**old_installer, "path": "build/desktop/electron/FundsTrace-0.1.9-windows-x64-unsigned.exe"}
    rebind_candidate(base, installer)
    with pytest.raises(ValueError, match="requires native icon evidence"):
        prepare_modern((base, installer), tmp_path / "archive")


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "symlink", "bytes"])
def test_modern_archive_verifier_rejects_changed_declared_set(candidate, tmp_path, change):
    modern = modern_candidate(candidate)
    output = tmp_path / "archive"
    prepare_modern(modern, output)
    path = output / "evidence" / TASKBAR_SCREENSHOT
    if change == "missing":
        path.unlink()
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"] = [item for item in manifest["files"] if item["path"] != "evidence/" + TASKBAR_SCREENSHOT]
        write_json(manifest_path, manifest)
    elif change == "duplicate":
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"].append(dict(manifest["files"][0]))
        write_json(manifest_path, manifest)
    elif change == "extra":
        (output / "evidence/unapproved.png").write_bytes(b"extra")
    elif change == "symlink":
        elsewhere = tmp_path / "elsewhere.png"
        elsewhere.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(elsewhere)
    else:
        path.write_bytes(path.read_bytes() + b"changed")
        rehash(output, path.name)
    with pytest.raises(ValueError):
        verify_evidence(output)


def test_modern_snapshot_uses_actual_archive_set(candidate, tmp_path):
    base, installer = modern_candidate(candidate)
    assets = [{"name": name, "size": (base / relative).stat().st_size,
               "digest": "sha256:" + sha((base / relative).read_bytes())}
              for name, relative in {**EVIDENCE_FILES, **OPTIONAL_EVIDENCE}.items()]
    assets.append({"name": Path(installer["path"]).name, "size": installer["bytes"], "digest": "sha256:" + installer["sha256"]})
    snapshot = {"tag_name": "v0.1.9", "body": "Synthetic original release", "assets": assets}
    manifest = prepare_modern((base, installer), tmp_path / "archive", release_snapshot=snapshot)
    assert len(manifest["files"]) == 17
    snapshot["assets"] = snapshot["assets"][:-2] + snapshot["assets"][-1:]
    with pytest.raises(ValueError):
        prepare_modern((base, installer), tmp_path / "incomplete", release_snapshot=snapshot)
