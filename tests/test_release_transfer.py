"""Offline regression coverage for permanent evidence and draft-only transfer."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import threading
import zipfile

import pytest

from scripts import transfer_windows_candidate as transfer


TAG = "v0.1.6"
SOURCE = "a" * 40


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def git_blob_sha(content):
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


class MemoryGitHub:
    """Small Git-object server with append-only, non-force ref semantics."""

    def __init__(self):
        self.lock = threading.RLock()
        self.head = None
        self.blobs = {}
        self.trees = {}
        self.commits = {}
        self.calls = []
        self.corrupt_blob_reads = False
        self.race_next_update = False
        self.competing_head = None

    @property
    def writes(self):
        return [call for call in self.calls if call[0] != "GET"]

    def object_sha(self, value):
        return hashlib.sha1(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def is_ancestor(self, older, newer):
        return older == newer or any(self.is_ancestor(older, parent)
                                     for parent in self.commits[newer]["parents"])

    def files_at(self, commit):
        tree = self.trees[self.commits[commit]["tree"]]
        return {name: self.blobs[entry["sha"]] for name, entry in tree.items()}

    def api(self, method, endpoint, payload=None):
        with self.lock:
            self.calls.append((method, endpoint, copy.deepcopy(payload)))
            if method == "GET" and endpoint.startswith("git/matching-refs/heads/"):
                return ([{"ref": "refs/heads/" + transfer.EVIDENCE_BRANCH,
                          "object": {"sha": self.head}}] if self.head else [])
            if method == "GET" and endpoint.startswith("git/commits/"):
                commit = self.commits[endpoint.removeprefix("git/commits/")]
                return {"tree": {"sha": commit["tree"]}}
            if method == "GET" and endpoint.startswith("git/trees/"):
                tree_sha = endpoint.removeprefix("git/trees/").split("?")[0]
                return {"truncated": False, "tree": list(self.trees[tree_sha].values())}
            if method == "GET" and endpoint.startswith("git/blobs/"):
                content = self.blobs[endpoint.removeprefix("git/blobs/")]
                if self.corrupt_blob_reads:
                    content = bytes([content[0] ^ 1]) + content[1:]
                return {"encoding": "base64", "size": len(content),
                        "content": base64.b64encode(content).decode()}
            if method == "POST" and endpoint == "git/blobs":
                assert payload["encoding"] == "base64"
                content = base64.b64decode(payload["content"], validate=True)
                value = git_blob_sha(content)
                self.blobs[value] = content
                return {"sha": value}
            if method == "POST" and endpoint == "git/trees":
                entries = copy.deepcopy(self.trees.get(payload.get("base_tree"), {}))
                entries.update({entry["path"]: copy.deepcopy(entry) for entry in payload["tree"]})
                value = self.object_sha(entries)
                self.trees[value] = entries
                return {"sha": value}
            if method == "POST" and endpoint == "git/commits":
                value = self.object_sha(payload)
                self.commits[value] = copy.deepcopy(payload)
                return {"sha": value}
            if method == "POST" and endpoint == "git/refs":
                assert payload["ref"] == "refs/heads/" + transfer.EVIDENCE_BRANCH
                assert self.head is None, "Reference already exists"
                self.head = payload["sha"]
                return {"object": {"sha": self.head}}
            if method == "PATCH" and endpoint == "git/refs/heads/" + transfer.EVIDENCE_BRANCH:
                assert payload["force"] is False
                if self.race_next_update:
                    competing = {"message": "Another verified version", "tree": self.commits[self.head]["tree"],
                                 "parents": [self.head]}
                    self.competing_head = self.object_sha(competing)
                    self.commits[self.competing_head] = competing
                    self.head = self.competing_head
                    self.race_next_update = False
                if not self.is_ancestor(self.head, payload["sha"]):
                    raise RuntimeError("GitHub rejected a non-fast-forward update")
                self.head = payload["sha"]
                return {"object": {"sha": self.head}}
            raise AssertionError(f"Unexpected GitHub call: {method} {endpoint}")


@pytest.fixture
def archive_server(monkeypatch):
    server = MemoryGitHub()
    monkeypatch.setattr(transfer, "api", server.api)
    # Full archive-file validation has separate public-helper tests. Here only
    # isolate that layer so Git persistence and readback remain under test.
    monkeypatch.setattr(transfer, "verify_evidence", lambda folder: json.loads(
        (Path(folder) / "manifest.json").read_text(encoding="utf-8")))
    return server


def evidence_directory(tmp_path, tag=TAG):
    folder = tmp_path / tag
    (folder / "evidence").mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"tag": tag}) + "\n", encoding="utf-8")
    (folder / "README.md").write_text("Synthetic verification only.\n", encoding="utf-8")
    (folder / "evidence/report.json").write_bytes(b'{"status":"passed"}\r\n')
    return folder


def archived_files(folder, tag=TAG):
    return {f"releases/{tag}/{file.relative_to(folder).as_posix()}": file.read_bytes()
            for file in folder.rglob("*") if file.is_file()}


def test_first_archive_creates_branch_and_reads_back_exact_bytes(tmp_path, archive_server):
    folder = evidence_directory(tmp_path)
    result = transfer.archive_evidence(folder)
    assert result["commit"] == archive_server.head
    assert archive_server.commits[result["commit"]]["parents"] == []
    assert archive_server.files_at(result["commit"]) == archived_files(folder)
    assert result["url"] == f"https://github.com/{transfer.REPO}/tree/{result['commit']}/releases/{TAG}"
    assert result["manifestSha256"] == sha256((folder / "manifest.json").read_bytes())
    assert len([call for call in archive_server.writes if call[1] == "git/refs"]) == 1
    assert len([call for call in archive_server.calls if call[1].startswith("git/blobs/")]) == 3


def test_new_version_appends_without_removing_old_evidence(tmp_path, archive_server):
    original = evidence_directory(tmp_path)
    previous = transfer.archive_evidence(original)
    newer = evidence_directory(tmp_path, "v0.1.7")
    result = transfer.archive_evidence(newer)
    assert archive_server.commits[result["commit"]]["parents"] == [previous["commit"]]
    assert archive_server.files_at(result["commit"]) == {
        **archived_files(original), **archived_files(newer, "v0.1.7")}
    updates = [call[2] for call in archive_server.writes if call[0] == "PATCH"]
    assert updates == [{"sha": result["commit"], "force": False}]


def test_identical_version_retry_only_reads_existing_archive(tmp_path, archive_server):
    folder = evidence_directory(tmp_path)
    original = transfer.archive_evidence(folder)
    writes = list(archive_server.writes)
    assert transfer.archive_evidence(folder) == original
    assert archive_server.writes == writes


def test_same_version_changed_bytes_cannot_replace_archive(tmp_path, archive_server):
    folder = evidence_directory(tmp_path)
    original = transfer.archive_evidence(folder)
    stored = archive_server.files_at(original["commit"])
    writes = list(archive_server.writes)
    (folder / "evidence/report.json").write_bytes(b'{"status":"different"}\n')
    with pytest.raises(AssertionError):
        transfer.archive_evidence(folder)
    assert archive_server.head == original["commit"]
    assert archive_server.files_at(original["commit"]) == stored
    assert archive_server.writes == writes


def test_archive_requires_uncorrupted_remote_blob_readback(tmp_path, archive_server):
    folder = evidence_directory(tmp_path)
    archive_server.corrupt_blob_reads = True
    with pytest.raises(AssertionError):
        transfer.archive_evidence(folder)
    assert any(call[1].startswith("git/blobs/") for call in archive_server.calls)


def test_concurrent_branch_update_fails_without_force_or_retry(tmp_path, archive_server):
    first = transfer.archive_evidence(evidence_directory(tmp_path))
    archive_server.race_next_update = True
    with pytest.raises(RuntimeError, match="non-fast-forward"):
        transfer.archive_evidence(evidence_directory(tmp_path, "v0.1.7"))
    assert archive_server.head == archive_server.competing_head
    assert archive_server.commits[archive_server.head]["parents"] == [first["commit"]]
    assert len([call for call in archive_server.writes if call[0] == "PATCH"]) == 1
    assert all(call[2]["force"] is False for call in archive_server.writes if call[0] == "PATCH")
    assert not any(name.startswith("releases/v0.1.7/")
                   for name in archive_server.files_at(archive_server.head))


@pytest.fixture
def draft_transfer(tmp_path, monkeypatch):
    installer = tmp_path / "FundsTrace-0.1.6-windows-x64-unsigned.exe"
    installer.write_bytes(b"MZ synthetic installer\x00")
    expected = {"bytes": installer.stat().st_size, "sha256": sha256(installer.read_bytes())}
    commit = "b" * 40
    archive = {"branch": transfer.EVIDENCE_BRANCH, "commit": commit, "path": f"releases/{TAG}",
               "url": f"https://github.com/{transfer.REPO}/tree/{commit}/releases/{TAG}",
               "manifestSha256": "c" * 64}
    state = {"release": {"draft": True, "assets": [], "body": "Synthetic release notes."},
             "tag": SOURCE, "commands": [], "notes": None, "wrong_uploaded_digest": False}

    def fake_api(method, endpoint, payload=None):
        assert method == "GET" and payload is None
        if endpoint == f"releases/tags/{TAG}":
            return copy.deepcopy(state["release"])
        if endpoint == f"commits/{TAG}":
            return {"sha": state["tag"]}
        raise AssertionError(f"Unexpected release API call: {endpoint}")

    def fake_run(command, *, check):
        assert check is True
        state["commands"].append(command)
        if command[:4] == ["gh", "release", "upload", TAG]:
            assert command == ["gh", "release", "upload", TAG, "--repo", transfer.REPO, str(installer)]
            state["release"]["assets"] = [{"name": installer.name, "state": "uploaded",
                "size": expected["bytes"], "digest": "sha256:" + (
                    "d" * 64 if state["wrong_uploaded_digest"] else expected["sha256"])}]
        else:
            assert command[:4] == ["gh", "release", "edit", TAG]
            assert command[4:7] == ["--repo", transfer.REPO, "--notes-file"]
            assert len(command) == 8
            state["notes"] = Path(command[7]).read_text(encoding="utf-8")

    monkeypatch.setattr(transfer, "api", fake_api)
    monkeypatch.setattr(transfer.subprocess, "run", fake_run)
    return installer, expected, archive, state


def test_transfer_uploads_only_original_installer_and_keeps_release_draft(draft_transfer):
    installer, expected, archive, state = draft_transfer
    transfer.upload_verified_installer(installer, TAG, SOURCE, expected, archive)
    assert len(state["commands"]) == 2
    assert state["release"]["draft"] is True
    assert [asset["name"] for asset in state["release"]["assets"]] == [installer.name]
    assert state["notes"].startswith("Synthetic release notes.")
    for value in (archive["url"], archive["manifestSha256"], SOURCE, installer.name, expected["sha256"]):
        assert value in state["notes"]


@pytest.mark.parametrize("changed", ["tag", "existing_asset", "published"])
def test_transfer_rejects_changed_release_or_tag_before_upload(draft_transfer, changed):
    installer, expected, archive, state = draft_transfer
    if changed == "tag":
        state["tag"] = "e" * 40
    elif changed == "existing_asset":
        state["release"]["assets"] = [{"name": "already-there.json"}]
    else:
        state["release"]["draft"] = False
    with pytest.raises(AssertionError):
        transfer.upload_verified_installer(installer, TAG, SOURCE, expected, archive)
    assert state["commands"] == []


def test_transfer_rejects_uploaded_digest_mismatch_before_editing_notes(draft_transfer):
    installer, expected, archive, state = draft_transfer
    state["wrong_uploaded_digest"] = True
    with pytest.raises(AssertionError):
        transfer.upload_verified_installer(installer, TAG, SOURCE, expected, archive)
    assert len(state["commands"]) == 1
    assert state["commands"][0][2] == "upload"
    assert state["notes"] is None and state["release"]["draft"] is True


def test_standard_archive_is_identical_despite_mtime_and_creation_order(tmp_path):
    content = {"report.json": b'{"synthetic":true}\r\n', "window.png": b"\x89PNG\r\nsynthetic\x00"}
    directories = [tmp_path / "first", tmp_path / "second"]
    outputs = [tmp_path / "first.zip", tmp_path / "second.zip"]
    for index, directory in enumerate(directories):
        directory.mkdir()
        for name in sorted(content, reverse=bool(index)):
            file = directory / name
            file.write_bytes(content[name])
            os.utime(file, (1000000000 + index * 500000000,) * 2)
        transfer.standard_archive(directory, outputs[index])
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    with zipfile.ZipFile(outputs[0]) as archive:
        assert archive.namelist() == sorted(content)
        assert {name: archive.read(name) for name in archive.namelist()} == content
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
        assert all(info.external_attr >> 16 == 0o100644 for info in archive.infolist())
