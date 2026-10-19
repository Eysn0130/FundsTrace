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
