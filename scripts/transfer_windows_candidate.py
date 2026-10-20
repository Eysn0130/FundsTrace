"""Validate an existing Windows run and upload its exact assets to an owned draft.

This is a manually dispatched transfer, not a build or public-release operation.
GitHub's job token stays in gh's environment; no credentials enter reports.
"""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import zipfile

REPO = "Eysn0130/FundsTrace"
GRAPH_CHECKS = (
    "installedGraphModulesLoaded", "installedCsvPreviewMapping", "installedCsvCommittedExact",
    "installedQueryExact", "installedFirstTrackExact", "installedSecondTrackVisible",
    "installedAutomaticNodeLabelRecovery",
    "installedContextTrackedConsistent", "installedNeighborhoodTrackVisible", "installedCanvasCloneExact",
    "installedCanvasSaveRestoreTypes", "installedCanvasUndoRedoTypes", "installedKeyboardUndoRedoOutsideGraph", "installedCaseIsolationAndReturn",
)


def gh(*args):
    return subprocess.check_output(["gh", *args])


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


def verify_upgrade_origin(candidate, installer):
    """Bind each account's upgrade report to the pinned source installer."""
    pinned = {
        "0.1.5": "f5c337e37adb30dc30c8cb895722703c91461ed0de49acee8dd487d454d8f1fc",
        "0.1.7": "52eabd3c6b0f77270c61066b8d1830b84cb5b316153d399625c7d21badfbeb7a",
    }
    prior = candidate.get("installerUpgradeFrom", "0.1.5")
    assert prior in pinned
    assert installer["status"] == "passed" and installer["priorVersion"] == prior
    # Historical v0.1.5 reports predate this field; new v0.1.7 reports must carry it.
    if prior != "0.1.5" or "priorInstallerSha256" in installer:
        assert installer.get("priorInstallerSha256") == pinned[prior]
    return prior


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
    release = json.loads(gh("release", "view", args.tag, "--repo", REPO, "--json", "isDraft,assets"))
    assert release["isDraft"] and not release["assets"], "Only a new empty draft may receive assets"
    subprocess.run(["git", "fetch", "--no-tags", "origin", source], check=True)

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

        reports = {name: data(base / name) for name in (
            "native-backend-smoke.json", "native-installer-smoke.json", "native-installed-app-smoke.json",
            "native-import-recovery-smoke.json")}
        for value in reports.values():
            assert value["status"] == "passed"
        standard = base / "standard-user"
        assert data(standard / "compatibility.json")["status"] == "passed"
        prior_version = verify_upgrade_origin(candidate, reports["native-installer-smoke.json"])
        for folder in (base, standard):
            app = data(folder / "native-installed-app-smoke.json")
            assert app["status"] == "passed" and all(value is True for value in app["checks"].values())
            assert all(app["checks"][name] is True for name in GRAPH_CHECKS)
            installer = data(folder / "native-installer-smoke.json")
            assert verify_upgrade_origin(candidate, installer) == prior_version
            assert installer["currentVersion"] == args.tag[1:] and all(value == "passed" for value in installer["checks"].values())
            assert all(installer["priorDataChecks"][name] is True for name in (
                "priorBackendSeeded", "priorExactMoneyAndCurrency", "priorCanvasAndTracking",
                "priorPackageExported", "priorEofReopened"))
            assert all(installer["upgradedDataChecks"][name] is True for name in (
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
            ("smokeReportSha256", base / "native-backend-smoke.json"),
            ("installerReportSha256", base / "native-installer-smoke.json"),
            ("installedAppReportSha256", base / "native-installed-app-smoke.json"),
            ("importRecoveryReportSha256", base / "native-import-recovery-smoke.json"),
            ("standardUserCompatibilitySha256", standard / "compatibility.json"),
        ):
            assert digest(file) == candidate[key]
        for file in root.rglob("*"):
            assert not file.is_symlink()
        installer_name = f"FundsTrace-{args.tag[1:]}-windows-x64-unsigned.exe"
        installers = list(root.rglob(installer_name))
        assert len(installers) == 1
        installer = installers[0]
        expected = next(item for item in candidate["files"] if item["path"].endswith("/" + installer_name))
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
        with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(standard.iterdir()):
                assert file.is_file() and file.suffix in {".json", ".png"}
                archive.write(file, file.name)
        summary = {"schemaVersion": 1, "status": "passed", "sourceCommit": source,
            "installerUpgradeFrom": prior_version,
            "sourceRun": args.run_id, "tag": args.tag, "installer": expected,
            "resourceCount": len(manifest["resources"]), "hostFileCount": len(host["hostFiles"]) + 1,
            "sourceResourcesIndependentlyChecked": sources_checked,
            "installedAppChecks": len(reports["native-installed-app-smoke.json"]["checks"]),
            "standardAppChecks": len(data(standard / "native-installed-app-smoke.json")["checks"]),
            "standardUserArchiveSha256": digest(zip_path), "publicReleasePublished": False}
        summary_path = base / "release-verification.json"
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        assets = [installer, manifest_path, host_path, candidates[0], base / "windows-candidate.sha256",
                  base / "native-installed-app.png", base / "native-installed-app.graph.png", zip_path, summary_path,
                  *(base / name for name in reports)]
        if candidate.get("inAppUpdateFixture") is True:
            for phase in ("downloading", "ready"):
                screenshot = base / f"native-installed-app.update-{phase}.png"
                assert screenshot.is_file()
                assets.append(screenshot)
        assert len({file.name for file in assets}) == len(assets)
        subprocess.run(["gh", "release", "upload", args.tag, "--repo", REPO, *(str(file) for file in assets)], check=True)
        print(json.dumps(summary))


if __name__ == "__main__":
    main()
