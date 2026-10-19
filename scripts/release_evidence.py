"""Create and independently verify a local, public-only Windows evidence archive.

No Git or network operations are performed here.  Callers publish the verified
directory to the dedicated evidence branch before changing Release assets.
"""
from pathlib import Path, PurePosixPath
import hashlib
import json
import re
import shutil
import stat
import zipfile


REPO = "Eysn0130/FundsTrace"
EVIDENCE_FILES = {
    "build-manifest.json": "backend/funds-trace-backend/build-manifest.json",
    "packaged-host-resources.json": "packaged-host-resources.json",
    "windows-candidate.json": "windows-candidate.json",
    "windows-candidate.sha256": "windows-candidate.sha256",
    "native-installed-app.png": "native-installed-app.png",
    "native-installed-app.graph.png": "native-installed-app.graph.png",
    "standard-user-compatibility.zip": "standard-user-compatibility.zip",
    "release-verification.json": "release-verification.json",
    "native-backend-smoke.json": "native-backend-smoke.json",
    "native-installer-smoke.json": "native-installer-smoke.json",
    "native-installed-app-smoke.json": "native-installed-app-smoke.json",
    "native-import-recovery-smoke.json": "native-import-recovery-smoke.json",
}
STANDARD_FILES = {
    "compatibility.json", "native-import-recovery-smoke.json",
    "native-installed-app-smoke.json", "native-installed-app.graph.png",
    "native-installed-app.png", "native-installer-smoke.json",
    "standard-user-environment.json",
}
CANDIDATE_REPORTS = {
    "nativeManifestSha256": "build-manifest.json",
    "hostResourcesReportSha256": "packaged-host-resources.json",
    "smokeReportSha256": "native-backend-smoke.json",
    "installerReportSha256": "native-installer-smoke.json",
    "installedAppReportSha256": "native-installed-app-smoke.json",
    "importRecoveryReportSha256": "native-import-recovery-smoke.json",
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique)


def _relative(value):
    _require(isinstance(value, str) and value and "\\" not in value, "Invalid relative path")
    path = PurePosixPath(value)
    _require(not path.is_absolute() and path.as_posix() == value
             and all(part not in {".", ".."} and ":" not in part for part in path.parts),
             "Unsafe or noncanonical relative path")
    return path


def _regular(root, relative):
    path = root
    _require(root.is_dir() and not root.is_symlink(), "Evidence root must be a real directory")
    parts = _relative(relative).parts
    for part in parts:
        path = path / part
        _require(not path.is_symlink(), f"Symlink is not allowed: {relative}")
    _require(path.is_file() and stat.S_ISREG(path.stat().st_mode), f"Missing regular file: {relative}")
    return path


def _identity(tag, source_commit, source_run):
    _require(isinstance(tag, str) and re.fullmatch(r"v0\.1\.(?:0|[1-9]\d*)", tag), "Invalid release tag")
    _require(isinstance(source_commit, str) and re.fullmatch(r"[0-9a-f]{40}", source_commit), "Invalid source commit")
    _require(isinstance(source_run, str) and re.fullmatch(r"[1-9]\d*", source_run), "Invalid source run")


def _installer(value, tag, *, manifest=False):
    _require(isinstance(value, dict), "Invalid installer metadata")
    expected = f"FundsTrace-{tag[1:]}-windows-x64-unsigned.exe"
    name_field = "name" if manifest else "path"
    expected_path = expected if manifest else f"build/desktop/electron/{expected}"
    _require(value.get(name_field) == expected_path, "Installer does not match release tag")
    _require(type(value.get("bytes")) is int and value["bytes"] > 0, "Invalid installer size")
    _require(_sha(value.get("sha256")), "Invalid installer digest")
    if manifest:
        _require(set(value) == {"name", "bytes", "sha256"}, "Unexpected installer metadata")
    return {"name": expected, "bytes": value["bytes"], "sha256": value["sha256"]}


def _checksums(path):
    sums = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line:
            continue
        _require("  " in line, "Invalid candidate checksum line")
        sha, name = line.split("  ", 1)
        _relative(name)
        _require(_sha(sha) and name.startswith("build/desktop/") and name not in sums,
                 "Invalid or duplicate candidate checksum")
        sums[name] = sha
    _require(sums, "Empty candidate checksums")
    return sums


def _snapshot(snapshot, tag, installer, files):
    _require(isinstance(snapshot, dict) and snapshot.get("tag_name", snapshot.get("tagName")) == tag,
             "Original Release snapshot has another tag")
    _require(isinstance(snapshot.get("body"), str), "Original Release body is missing")
    assets = snapshot.get("assets")
    _require(isinstance(assets, list), "Original Release assets are missing")
    expected = {**files, installer["name"]: installer}
    _require(len(assets) == len(expected), "Original Release asset set is incomplete")
    seen = set()
    for asset in assets:
        _require(isinstance(asset, dict), "Invalid original Release asset")
        name = asset.get("name")
        _require(isinstance(name, str) and name in expected and name not in seen,
                 "Unexpected or duplicate original Release asset")
        seen.add(name)
        info = expected[name]
        _require(type(asset.get("size")) is int and asset["size"] == info["bytes"],
                 f"Original Release asset size mismatch: {name}")
        _require(asset.get("digest") == "sha256:" + info["sha256"],
                 f"Original Release asset digest mismatch: {name}")


def _readme(manifest):
    files = "\n".join(f"- [{item['path'].removeprefix('evidence/')}](./{item['path']})" for item in manifest["files"])
    installer = manifest["installer"]
    return (f"# FundsTrace {manifest['tag']} 验证归档\n\n"
            f"源码提交：`{manifest['sourceCommit']}`。\n\n"
            f"[原生 Windows 验证运行](https://github.com/{REPO}/actions/runs/{manifest['sourceRun']})。\n\n"
            f"安装包：`{installer['name']}`，{installer['bytes']} 字节；SHA-256：`{installer['sha256']}`。"
            "安装包保留在原 Release，本归档不复制安装包。\n\n"
            "证据来自 Windows Server 2022 自动化与合成案件，包含安装、升级和卸载保留数据检查。"
            "安装包未签名；这些结果不等于 Windows 10/11 实机、DPI、原生对话框或 SmartScreen 验收。\n\n"
            "归档文件的字节数与 SHA-256 记录在 [manifest.json](./manifest.json)。\n\n"
            f"{files}\n")


def prepare_evidence(base: Path, output: Path, *, tag: str, source_commit: str,
                     source_run: str, installer: dict, release_snapshot: dict | None = None) -> dict:
    """Copy only the 12 public evidence assets into a new local directory.

    The original files are copied byte-for-byte.  ``installer`` is the candidate
    manifest's path/bytes/sha256 entry; the executable itself is never copied.
    Invalid inputs raise ValueError, OSError or a JSON/ZIP decoding error.
    """
    base, output = Path(base), Path(output)
    _identity(tag, source_commit, source_run)
    installer = _installer(installer, tag)
    _require(not output.exists() and not output.is_symlink(), "Output directory must be new")
    _require(not output.parent.is_symlink() and output.parent.is_dir(), "Output parent must be a real directory")
    sources = {name: _regular(base, relative) for name, relative in EVIDENCE_FILES.items()}
    _require(base.resolve() not in output.resolve().parents and output.resolve() not in base.resolve().parents,
             "Evidence output and source must be separate directories")
    output.mkdir()
    (output / "evidence").mkdir()
    for name, path in sources.items():
        shutil.copyfile(path, output / "evidence" / name)
    if release_snapshot is not None:
        (output / "evidence/release.original.json").write_text(
            json.dumps(release_snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = []
    for path in sorted((output / "evidence").iterdir()):
        files.append({"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size,
                      "sha256": _digest(path)})
    manifest = {"schemaVersion": 1, "tag": tag, "sourceCommit": source_commit,
                "sourceRun": source_run, "installer": installer, "files": files}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "README.md").write_text(_readme(manifest), encoding="utf-8")
    return verify_evidence(output)


def verify_evidence(directory: Path) -> dict:
    """Verify the complete archive without consulting original files or network."""
    directory = Path(directory)
    manifest = _json(_regular(directory, "manifest.json"))
    _require(isinstance(manifest, dict) and set(manifest) == {
        "schemaVersion", "tag", "sourceCommit", "sourceRun", "installer", "files"}, "Invalid evidence manifest")
    _require(type(manifest["schemaVersion"]) is int and manifest["schemaVersion"] == 1, "Unsupported evidence schema")
    _identity(manifest["tag"], manifest["sourceCommit"], manifest["sourceRun"])
    installer = _installer(manifest["installer"], manifest["tag"], manifest=True)
    entries = manifest["files"]
    _require(isinstance(entries, list), "Invalid evidence file list")
    files = {}
    allowed = {"evidence/" + name for name in EVIDENCE_FILES}
    optional = "evidence/release.original.json"
    for entry in entries:
        _require(isinstance(entry, dict) and set(entry) == {"path", "bytes", "sha256"}, "Invalid evidence file entry")
        relative = entry["path"]
        _relative(relative)
        _require(relative in allowed | {optional} and relative not in files, "Unexpected or duplicate evidence path")
        _require(type(entry["bytes"]) is int and entry["bytes"] >= 0 and _sha(entry["sha256"]), "Invalid evidence file metadata")
        path = _regular(directory, relative)
        _require(path.stat().st_size == entry["bytes"] and _digest(path) == entry["sha256"],
                 f"Evidence checksum mismatch: {relative}")
        files[relative] = entry
    _require(set(files) in (allowed, allowed | {optional}), "Evidence file set is incomplete")
    expected_paths = set(files) | {"manifest.json", "README.md", "evidence"}
    actual_paths = set()
    for path in directory.rglob("*"):
        _require(not path.is_symlink(), "Symlinks are not allowed in evidence")
        relative = path.relative_to(directory).as_posix()
        _require(relative in expected_paths, f"Unexpected archive entry: {relative}")
        actual_paths.add(relative)
    _require(actual_paths == expected_paths, "Archive entries are incomplete")
    _require(_regular(directory, "README.md").read_text(encoding="utf-8") == _readme(manifest), "Evidence README differs from manifest")
    evidence = directory / "evidence"
    candidate = _json(evidence / "windows-candidate.json")
    summary = _json(evidence / "release-verification.json")
    _require(isinstance(candidate, dict) and isinstance(summary, dict), "Invalid candidate or release verification object")
    _require(type(candidate.get("schemaVersion")) is int and candidate["schemaVersion"] == 1
             and candidate.get("candidate") == "unsigned-installer"
             and candidate.get("platform") == "win32" and candidate.get("arch") == "x64", "Invalid Windows candidate")
    _require(isinstance(candidate.get("checks"), dict) and candidate["checks"]
             and all(value == "passed" for value in candidate["checks"].values()), "Windows candidate did not pass")
    for key in ("fileVersion", "productVersion"):
        value = candidate.get("executableVersion", {}).get(key)
        _require(isinstance(value, str) and value in {manifest["tag"][1:], manifest["tag"][1:] + ".0"}, "Candidate version mismatch")
    _require(summary.get("schemaVersion") == 1 and summary.get("status") == "passed", "Release verification did not pass")
    for key in ("tag", "sourceCommit", "sourceRun"):
        _require(summary.get(key) == manifest[key], f"Release verification {key} mismatch")
    _require(_installer(summary.get("installer"), manifest["tag"]) == installer, "Release verification installer mismatch")
    candidates = candidate.get("files")
    _require(isinstance(candidates, list), "Candidate files are missing")
    candidate_paths = set()
    for entry in candidates:
        _require(isinstance(entry, dict), "Invalid candidate file entry")
        relative = entry.get("path")
        _relative(relative)
        _require(relative.startswith("build/desktop/") and relative not in candidate_paths
                 and type(entry.get("bytes")) is int and entry["bytes"] >= 0 and _sha(entry.get("sha256")),
                 "Invalid or duplicate candidate file metadata")
        candidate_paths.add(relative)
    installer_entries = [item for item in candidates if isinstance(item, dict)
                         and str(item.get("path", "")).endswith("/" + installer["name"])]
    _require(len(installer_entries) == 1 and _installer(installer_entries[0], manifest["tag"]) == installer,
             "Candidate installer mismatch")
    sums = _checksums(evidence / "windows-candidate.sha256")
    installer_path = f"build/desktop/electron/{installer['name']}"
    _require(sums.get(installer_path) == installer["sha256"], "Installer candidate checksum mismatch")
    for name, source_path in EVIDENCE_FILES.items():
        if name not in {"windows-candidate.sha256", "standard-user-compatibility.zip", "release-verification.json"}:
            _require(sums.get("build/desktop/" + source_path) == files["evidence/" + name]["sha256"],
                     f"Original artifact checksum mismatch: {name}")
    for key, name in CANDIDATE_REPORTS.items():
        _require(candidate.get(key) == files["evidence/" + name]["sha256"], f"Candidate report checksum mismatch: {name}")
    zip_path = evidence / "standard-user-compatibility.zip"
    _require(_sha(summary.get("standardUserArchiveSha256"))
             and _digest(zip_path) == summary["standardUserArchiveSha256"], "Standard-user ZIP checksum mismatch")
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        _require(len(members) == len(STANDARD_FILES) and {item.filename for item in members} == STANDARD_FILES,
                 "Unexpected or missing standard-user ZIP members")
        for item in members:
            _relative(item.filename)
            _require(not item.is_dir() and not stat.S_ISLNK(item.external_attr >> 16)
                     and not item.flag_bits & 1, "Unsafe standard-user ZIP member")
            value = archive.read(item)
            _require(hashlib.sha256(value).hexdigest() == sums.get("build/desktop/standard-user/" + item.filename),
                     f"Standard-user original checksum mismatch: {item.filename}")
        _require(candidate.get("standardUserCompatibilitySha256") == sums.get("build/desktop/standard-user/compatibility.json"),
                 "Standard-user candidate checksum mismatch")
    if optional in files:
        _snapshot(_json(evidence / "release.original.json"), manifest["tag"], installer,
                  {name: files["evidence/" + name] for name in EVIDENCE_FILES})
    return manifest
