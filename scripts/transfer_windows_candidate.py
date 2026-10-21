"""Archive verified Windows evidence, then upload only its installer to a draft.

This is a manually dispatched transfer, not a build or public-release operation.
GitHub's job token stays in gh's environment; no credentials enter reports.
"""
from pathlib import Path, PurePosixPath
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import re
import subprocess
import tempfile
import zipfile

if __package__:
    from .release_evidence import prepare_evidence, verify_evidence
    from .upgrade_baseline import parse_baseline, validate_installer_identity
else:
    from release_evidence import prepare_evidence, verify_evidence
    from upgrade_baseline import parse_baseline, validate_installer_identity

REPO = "Eysn0130/FundsTrace"
EVIDENCE_BRANCH = "codex/release-evidence"
GRAPH_CHECKS = (
    "installedGraphModulesLoaded", "installedCsvPreviewMapping", "installedCsvCommittedExact",
    "installedQueryExact", "installedFirstTrackExact", "installedSecondTrackVisible",
    "installedAutomaticNodeLabelRecovery",
    "installedPartialViewportNodeLabels",
    "installedAnnotationDialogSave", "installedAnnotationUndoRedo", "installedAnnotationOffsetDrag",
    "installedAnnotationNodeFollow", "installedCanvasSaveRestoreAnnotation",
    "installedContextTrackedConsistent", "installedNeighborhoodTrackVisible", "installedCanvasCloneExact",
    "installedCanvasSaveRestoreTypes", "installedCanvasUndoRedoTypes", "installedKeyboardUndoRedoOutsideGraph", "installedCaseIsolationAndReturn",
)


def gh(*args):
    return subprocess.check_output(["gh", *args])


def api(method, endpoint, payload=None):
    """Use gh's existing authentication; public evidence is sent over stdin."""
    command = ["gh", "api", "--method", method, f"repos/{REPO}/{endpoint}"]
    if payload is not None:
        command += ["--input", "-"]
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    return json.loads(subprocess.check_output(command, input=body))


def blob_sha(content):
    return hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()


def archive_tree(commit):
    tree_sha = api("GET", f"git/commits/{commit}")["tree"]["sha"]
    tree = api("GET", f"git/trees/{tree_sha}?recursive=1")
    assert tree.get("truncated") is False, "Evidence tree was truncated"
    return tree_sha, tree["tree"]


def verify_archive_commit(commit, prefix, files):
    """Read immutable Git objects back, including their original file bytes."""
    assert re.fullmatch(r"[0-9a-f]{40}", commit)
    _, tree = archive_tree(commit)
    stored = {item["path"]: item for item in tree
              if item["type"] == "blob" and item["path"].startswith(prefix + "/")}
    assert set(stored) == set(files), "Archive file set differs"
    for name, content in files.items():
        assert stored[name]["mode"] == "100644" and stored[name]["sha"] == blob_sha(content)

    def verify_file(item):
        name, content = item
        value = api("GET", f"git/blobs/{stored[name]['sha']}")
        assert value["encoding"] == "base64" and value["size"] == len(content)
        returned = base64.b64decode("".join(value["content"].split()), validate=True)
        assert hashlib.sha256(returned).digest() == hashlib.sha256(content).digest(), name

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(verify_file, files.items()))


def archive_evidence(directory):
    """Append one version on the dedicated branch; never rewrite a version or ref."""
    directory = Path(directory)
    manifest = verify_evidence(directory)
    tag = manifest["tag"]
    prefix = f"releases/{tag}"
    files = {f"{prefix}/{file.relative_to(directory).as_posix()}": file.read_bytes()
             for file in directory.rglob("*") if file.is_file()}
    refs = api("GET", f"git/matching-refs/heads/{EVIDENCE_BRANCH}")
    matches = [item for item in refs if item["ref"] == f"refs/heads/{EVIDENCE_BRANCH}"]
    assert len(matches) <= 1
    head = matches[0]["object"]["sha"] if matches else None
    base_tree = None
    if head:
        base_tree, tree = archive_tree(head)
        if any(item["path"] == prefix or item["path"].startswith(prefix + "/") for item in tree):
            # An interrupted transfer can reuse identical evidence, never replace it.
            verify_archive_commit(head, prefix, files)
            return {"branch": EVIDENCE_BRANCH, "commit": head, "path": prefix,
                    "url": f"https://github.com/{REPO}/tree/{head}/{prefix}",
                    "manifestSha256": digest(directory / "manifest.json")}

    def create_blob(item):
        name, content = item
        value = api("POST", "git/blobs", {
            "encoding": "base64", "content": base64.b64encode(content).decode("ascii")})
        assert value["sha"] == blob_sha(content)
        return {"path": name, "mode": "100644", "type": "blob", "sha": value["sha"]}

    with ThreadPoolExecutor(max_workers=4) as pool:
        entries = list(pool.map(create_blob, sorted(files.items())))
    payload = {"tree": entries}
    if base_tree:
        payload["base_tree"] = base_tree
    tree_sha = api("POST", "git/trees", payload)["sha"]
    commit = api("POST", "git/commits", {
        "message": f"Archive Windows verification for {tag}", "tree": tree_sha,
        "parents": [head] if head else []})["sha"]
    if head:
        api("PATCH", f"git/refs/heads/{EVIDENCE_BRANCH}", {"sha": commit, "force": False})
    else:
        api("POST", "git/refs", {"ref": f"refs/heads/{EVIDENCE_BRANCH}", "sha": commit})
    verify_archive_commit(commit, prefix, files)
    return {"branch": EVIDENCE_BRANCH, "commit": commit, "path": prefix,
            "url": f"https://github.com/{REPO}/tree/{commit}/{prefix}",
            "manifestSha256": digest(directory / "manifest.json")}


def verify_draft_assets(release, name, expected=None):
    """Resume only an unchanged sole EXE; never replace or remove an asset."""
    assert release.get("draft") is True, "Release is no longer a draft"
    assets = release.get("assets")
    assert isinstance(assets, list) and len(assets) <= 1, "Unexpected draft assets"
    if not assets:
        return False
    asset = assets[0]
    assert asset.get("name") == name and asset.get("state") == "uploaded", "Unexpected draft asset"
    assert type(asset.get("size")) is int and asset["size"] > 0
    assert isinstance(asset.get("digest"), str) and re.fullmatch(r"sha256:[0-9a-f]{64}", asset["digest"])
    if expected is not None:
        assert asset["size"] == expected["bytes"] and asset["digest"] == "sha256:" + expected["sha256"], "Draft installer differs from candidate"
    return True


def upload_verified_installer(installer, tag, source, expected, archive):
    """Only a verified permanent archive permits the sole draft asset upload."""
    assert re.fullmatch(r"[0-9a-f]{40}", archive["commit"])
    assert archive["branch"] == EVIDENCE_BRANCH and archive["path"] == f"releases/{tag}"
    assert archive["url"] == f"https://github.com/{REPO}/tree/{archive['commit']}/{archive['path']}"
    release = api("GET", f"releases/tags/{tag}")
    existing = verify_draft_assets(release, installer.name, expected)
    assert api("GET", f"commits/{tag}")["sha"] == source, "Tag moved before upload"
    assert installer.name == f"FundsTrace-{tag[1:]}-windows-x64-unsigned.exe"
    assert installer.stat().st_size == expected["bytes"] and digest(installer) == expected["sha256"]
    if not existing:
        subprocess.run(["gh", "release", "upload", tag, "--repo", REPO, str(installer)], check=True)
    uploaded = api("GET", f"releases/tags/{tag}")
    assert verify_draft_assets(uploaded, installer.name, expected)
    # Preserve concurrently edited user notes; never replace them with the
    # snapshot taken before a potentially long upload.
    notes = uploaded.get("body") or ""
    link = f"[验证记录]({archive['url']})"
    if link not in notes:
        notes = notes.rstrip() + f"\n\n{link}\n"
        with tempfile.TemporaryDirectory(prefix="fundstrace-draft-notes-") as temporary:
            file = Path(temporary) / "notes.md"
            file.write_text(notes, encoding="utf-8")
            subprocess.run(["gh", "release", "edit", tag, "--repo", REPO,
                            "--notes-file", str(file)], check=True)
    final = api("GET", f"releases/tags/{tag}")
    assert verify_draft_assets(final, installer.name, expected)
    assert link in (final.get("body") or ""), "Archive link missing after notes update"
    assert api("GET", f"commits/{tag}")["sha"] == source, "Tag moved after upload"


def standard_archive(standard, output):
    """Stable bytes allow an interrupted transfer to reuse its existing archive."""
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(standard.iterdir()):
            assert file.is_file() and not file.is_symlink() and file.suffix in {".json", ".png"}
            info = zipfile.ZipInfo(file.name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, file.read_bytes())


def data(file):
    return json.loads(file.read_text(encoding="utf-8-sig"))


def digest(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def variants(value):
    values = {hashlib.sha256(value).hexdigest()}
    try:
        value.decode("utf-8")
        lf = value.replace(b"\r\n", b"\n")
        values.update(hashlib.sha256(item).hexdigest() for item in (lf, lf.replace(b"\n", b"\r\n")))
    except UnicodeDecodeError:
        pass
    return values


def verify_host_report(host, version, package, source_file):
    """Bind every packaged host entry to this candidate, including its updater."""
    assert host["status"] == "passed" and host["version"] == version
    assert all(value is True for value in host["checks"].values())
    expected = {"main.cjs", "backend.cjs", "host-policy.cjs", "preferences.cjs", "preload.cjs"}
    if package.get("dependencies", {}).get("electron-updater"):
        expected.add("updates.cjs")
    assert set(host["hostFiles"]) == expected
    for name, sha in host["hostFiles"].items():
        assert sha in variants(source_file(name))


def verify_upgrade_origin(candidate, installer, baseline, current_sha256, current_bytes):
    """Require the source baseline, candidate declaration and installed bytes to agree."""
    assert candidate["installerUpgradeFrom"] == baseline["prior"]["version"]
    assert json.dumps(candidate["upgradeBaseline"], sort_keys=True) == json.dumps(baseline, sort_keys=True)
    assert installer["status"] == "passed"
    validate_installer_identity(installer, baseline, current_sha256, current_bytes)
    return baseline["prior"]["version"]


def verify_icon_report(icons, candidate, installer, expected_icon_sha):
    """Keep PE evidence tied to the actual candidate files and approved icon."""
    assert icons["status"] == "passed"
    for name in ("application", "installer"):
        icon = icons[name]
        assert icon["status"] == "passed" and icon["selection"] == "default-icon-index-0"
        assert icon["expectedIcoSha256"] == expected_icon_sha
        assert type(icon["verifiedLanguageVariants"]) is int and icon["verifiedLanguageVariants"] > 0 and icon["frames"]
    assert icons["installer"]["executableSha256"] == installer["sha256"]
    applications = [item for item in candidate["files"] if item["path"] == "build/desktop/electron/win-unpacked/FundsTrace.exe"]
    assert len(applications) == 1 and icons["application"]["executableSha256"] == applications[0]["sha256"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    assert re.fullmatch(r"[1-9]\d*", args.run_id)
    assert re.fullmatch(r"v0\.1\.\d+", args.tag)
    run = json.loads(gh("api", f"repos/{REPO}/actions/runs/{args.run_id}"))
    assert run["conclusion"] == "success" and run["event"] == "workflow_dispatch"
    assert run["path"] == ".github/workflows/windows-candidate.yml"
    source = run["head_sha"]
    assert re.fullmatch(r"[0-9a-f]{40}", source)
    assert json.loads(gh("api", f"repos/{REPO}/commits/{args.tag}"))["sha"] == source
    release = api("GET", f"releases/tags/{args.tag}")
    verify_draft_assets(release, f"FundsTrace-{args.tag[1:]}-windows-x64-unsigned.exe")
    subprocess.run(["git", "fetch", "--no-tags", "origin", source], check=True)
    source_package = json.loads(subprocess.check_output(["git", "show", f"{source}:desktop/package.json"]))
    assert source_package["version"] == args.tag[1:]
    baseline = parse_baseline(subprocess.check_output(
        ["git", "show", f"{source}:desktop/upgrade-baseline.json"]), source_package["version"])

    with tempfile.TemporaryDirectory(prefix="fundstrace-verified-transfer-") as temporary:
        root = Path(temporary)
        subprocess.run(["gh", "run", "download", args.run_id, "--repo", REPO,
                        "--name", f"fundstrace-windows-x64-candidate-{args.run_id}", "--dir", str(root)], check=True)
        candidates = list(root.rglob("windows-candidate.json"))
        assert len(candidates) == 1
        base = candidates[0].parent
        candidate = data(candidates[0])
        assert candidate["candidate"] == "unsigned-installer"
        assert candidate["platform"] == "win32" and candidate["arch"] == "x64"
        assert all(value == "passed" for value in candidate["checks"].values())
        assert all(str(candidate["executableVersion"][key]).split(".")[:3] == args.tag[1:].split(".")
                   for key in ("fileVersion", "productVersion"))
        installer_name = f"FundsTrace-{args.tag[1:]}-windows-x64-unsigned.exe"
        installer_records = [item for item in candidate["files"] if item["path"].endswith("/" + installer_name)]
        assert len(installer_records) == 1
        expected = installer_records[0]
        manifest_path = base / "backend/funds-trace-backend/build-manifest.json"
        manifest = data(manifest_path)
        assert manifest["platform"] == "win32" and manifest["arch"] == "x64" and manifest["protocol"] == 1
        sources_checked = 0
        for name, sha in manifest["resources"].items():
            assert not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts
            if name.startswith("frontend/dist/"):
                continue  # The Windows build verified these actual compiled resources, then installed them.
            assert name in {"LICENSE", "THIRD_PARTY_NOTICES.md"} or name.startswith("static/")
            assert sha in variants(subprocess.check_output(["git", "show", f"{source}:{name}"]))
            sources_checked += 1
        assert "static/graph-selection.js" in manifest["resources"]
        assert len(manifest["resources"]) == sources_checked + 3
        assert manifest["sourceEntrySha256"] in variants(subprocess.check_output(["git", "show", f"{source}:desktop_backend.py"]))

        host_path = base / "packaged-host-resources.json"
        host = data(host_path)
        package = json.loads(subprocess.check_output(["git", "show", f"{source}:desktop/package.json"]))
        verify_host_report(host, args.tag[1:], package,
                           lambda name: subprocess.check_output(["git", "show", f"{source}:desktop/{name}"]))

        icons = data(base / "native-icon-resources.json")
        expected_icon_sha = hashlib.sha256(subprocess.check_output(
            ["git", "show", f"{source}:desktop/assets/icon.ico"])).hexdigest()
        verify_icon_report(icons, candidate, expected, expected_icon_sha)

        reports = {name: data(base / name) for name in (
            "native-backend-smoke.json", "native-installer-smoke.json", "native-installed-app-smoke.json",
            "native-import-recovery-smoke.json")}
        for value in reports.values():
            assert value["status"] == "passed"
        standard = base / "standard-user"
        assert data(standard / "compatibility.json")["status"] == "passed"
        prior_version = verify_upgrade_origin(candidate, reports["native-installer-smoke.json"],
                                               baseline, expected["sha256"], expected["bytes"])
        for folder in (base, standard):
            app = data(folder / "native-installed-app-smoke.json")
            assert app["status"] == "passed" and all(value is True for value in app["checks"].values())
            assert all(app["checks"][name] is True for name in (*GRAPH_CHECKS,
                "installedWorkspaceWindowIcon", "installedAnalysisWindowIcon"))
            installer = data(folder / "native-installer-smoke.json")
            assert verify_upgrade_origin(candidate, installer, baseline, expected["sha256"], expected["bytes"]) == prior_version
            assert installer["currentVersion"] == args.tag[1:] and all(value == "passed" for value in installer["checks"].values())
            assert all(installer["checks"][name] == "passed" for name in (
                "upgradeBaseline", "presentationPreferencesSeeded", "obsoleteInstalledFileRemoved",
                "installedExecutableIcons", "installedShortcutIcons",
                "upgradedPresentationPreferences", "sameVersionReinstalled", "reinstalledResourceIntegrity",
                "reinstalledDataFilesUnchanged", "reinstalledExistingCaseData",
                "reinstalledPresentationPreferences", "presentationPreferencesRetained"))
            assert all(installer["priorDataChecks"][name] is True for name in (
                "priorBackendSeeded", "priorExactMoneyAndCurrency", "priorCanvasAndTracking",
                "priorPackageExported", "priorEofReopened"))
            assert all(installer["upgradedDataChecks"][name] is True for name in (
                "upgradedFactsUnchanged", "upgradedCanvasAndTracking", "legacyPackageRoundTrip",
                "upgradedAnalysisPaths", "upgradedAnalysisCycles", "upgradedAtomicCanvas",
                "upgradedEofReopened", "upgradedSourceFilesUnchanged"))
            assert all(installer["reinstalledDataChecks"][name] is True for name in (
                "upgradedFactsUnchanged", "upgradedCanvasAndTracking", "legacyPackageRoundTrip",
                "upgradedAnalysisPaths", "upgradedAnalysisCycles", "upgradedAtomicCanvas",
                "upgradedEofReopened", "upgradedSourceFilesUnchanged"))
            if candidate.get("inAppUpdateFixture") is True:
                cycle = app["updateCycle"]
                version_parts = [int(part) for part in args.tag[1:].split(".")]
                next_version = f"{version_parts[0]}.{version_parts[1]}.{version_parts[2] + 1}"
                assert cycle["currentVersion"] == args.tag[1:] and cycle["nextVersion"] == next_version
                assert cycle["privateFixtureOnly"] is True and cycle["productionFeedModified"] is False
                assert cycle["tlsVerificationDisabled"] is False
                assert re.fullmatch(r"[0-9a-f]{64}", cycle["installerSha256"])
                assert all(cycle["requests"][name] > 0 for name in ("manifest", "truncate", "corrupt", "valid"))
                assert all(app["checks"][name] is True for name in (
                    "installedInterruptedUpdateRejected", "installedCorruptUpdateRejected",
                    "installedUpdateProgressAndReady", "installedUpdateLaterRetainsApplication",
                    "installedUpdateConfirmedRestart", "installedUpdateAutoRelaunch",
                    "installedUpdateExactResources", "installedUpdateRelaunchNormalClose"))
                assert installer["checks"]["installedInAppUpdate"] == "passed"
                assert all(installer["inAppUpdateDataChecks"][name] is True for name in (
                    "upgradedFactsUnchanged", "upgradedCanvasAndTracking", "legacyPackageRoundTrip",
                    "upgradedAnalysisPaths", "upgradedAnalysisCycles", "upgradedAtomicCanvas",
                    "upgradedEofReopened", "upgradedSourceFilesUnchanged"))
        for key, file in (
            ("nativeManifestSha256", manifest_path), ("hostResourcesReportSha256", host_path),
            ("iconResourcesReportSha256", base / "native-icon-resources.json"),
            ("smokeReportSha256", base / "native-backend-smoke.json"),
            ("installerReportSha256", base / "native-installer-smoke.json"),
            ("installedAppReportSha256", base / "native-installed-app-smoke.json"),
            ("importRecoveryReportSha256", base / "native-import-recovery-smoke.json"),
            ("standardUserCompatibilitySha256", standard / "compatibility.json"),
        ):
            assert digest(file) == candidate[key]
        for file in root.rglob("*"):
            assert not file.is_symlink()
        installers = list(root.rglob(installer_name))
        assert len(installers) == 1
        installer = installers[0]
        assert installer.stat().st_size == expected["bytes"] and digest(installer) == expected["sha256"]
        with installer.open("rb") as stream:
            assert stream.read(2) == b"MZ"
        sums = {}
        for line in (base / "windows-candidate.sha256").read_text(encoding="utf-8-sig").splitlines():
            sha, name = line.split("  ", 1)
            assert re.fullmatch(r"[0-9a-f]{64}", sha) and name not in sums
            sums[name] = sha
        for file in base.rglob("*"):
            if file.is_file() and file.name != "windows-candidate.sha256":
                relative = "build/desktop/" + file.relative_to(base).as_posix()
                assert digest(file) == sums[relative], f"Artifact checksum mismatch: {relative}"
        zip_path = base / "standard-user-compatibility.zip"
        standard_archive(standard, zip_path)
        summary = {"schemaVersion": 1, "status": "passed", "sourceCommit": source,
            "installerUpgradeFrom": prior_version,
            "sourceRun": args.run_id, "tag": args.tag, "installer": expected,
            "upgradeBaseline": baseline,
            "resourceCount": len(manifest["resources"]), "hostFileCount": len(host["hostFiles"]) + 1,
            "sourceResourcesIndependentlyChecked": sources_checked,
            "installedAppChecks": len(reports["native-installed-app-smoke.json"]["checks"]),
            "standardAppChecks": len(data(standard / "native-installed-app-smoke.json")["checks"]),
            "standardUserArchiveSha256": digest(zip_path), "publicReleasePublished": False}
        summary_path = base / "release-verification.json"
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        with tempfile.TemporaryDirectory(prefix="fundstrace-evidence-") as evidence_temporary:
            archive_dir = Path(evidence_temporary) / args.tag
            prepare_evidence(base, archive_dir, tag=args.tag, source_commit=source,
                             source_run=args.run_id, installer=expected)
            archive = archive_evidence(archive_dir)
        upload_verified_installer(installer, args.tag, source, expected, archive)
        print(json.dumps({**summary, "evidenceArchive": archive, "uploadedAssets": [installer.name]}))


if __name__ == "__main__":
    main()
