"""Read-only acquisition and exact-byte checks for a second run of one Windows EXE.

This module never imports the transfer entry point or writes to GitHub.  The
download command is the only operation needing a token; later commands are local.
"""
from pathlib import Path, PurePosixPath
import argparse
import base64
import hashlib
import json
import re
import stat
import subprocess
import zipfile

if __package__:
    from .upgrade_baseline import load_baseline, validate_installer_identity
else:
    from upgrade_baseline import load_baseline, validate_installer_identity

REPOSITORY = "Eysn0130/FundsTrace"
WORKFLOW = ".github/workflows/windows-candidate.yml"
PREFIX = "build/desktop/"
PAYLOAD = PREFIX + "electron/win-unpacked/"
SOURCE_KIND = "same candidate Actions artifact"
FRESH_STARTUP_CHECKS = frozenset({
    "freshStateInitiallyAbsent", "installedExecutableStarted", "authenticatedWorkspaceLoaded", "defaultCaseEmpty",
    "activationFree", "noPageExceptions", "normalClose", "backendReleasedAfterClose", "clientCreatedDefaultData",
})
FORMAT_CHECKS = frozenset({
    "fourFormatsExact", "deterministicControls", "sourceReportsAndTraces", "publicRawPackageFacts",
    "cancellationChainExact", "lateConflictExact", "suffixlessPackageExact", "cleanProcessRestore",
    "reimportAddsZero", "desktopAuthentication", "activationFree", "ownedRuntime", "eofShutdown", "cleanup",
})


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    def unique(items):
        value = {}
        for key, item in items:
            require(key not in value, "Duplicate JSON key")
            value[key] = item
        return value
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique)


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, indent=2, ensure_ascii=False)
        output.write("\n")


def safe_name(value):
    require(isinstance(value, str) and value and "\\" not in value and ":" not in value,
            "Invalid artifact path")
    path = PurePosixPath(value)
    require(not path.is_absolute() and str(path) == value and
            all(part not in {".", ".."} for part in path.parts), "Unsafe artifact path")
    return value


def validate_source(run, artifacts, run_id, commit):
    """Bind exactly one original artifact ID to this repository and commit."""
    require(re.fullmatch(r"[1-9][0-9]*", str(run_id)) and
            re.fullmatch(r"[0-9a-f]{40}", commit), "Invalid source identity")
    require(run.get("id") == int(run_id) and run.get("event") == "workflow_dispatch" and
            run.get("status") == "completed" and run.get("conclusion") == "success" and
            run.get("path") == WORKFLOW and run.get("head_sha") == commit,
            "Source run must be a successful exact workflow dispatch at the checked-out commit")
    require(run.get("repository", {}).get("full_name") == REPOSITORY and
            run.get("head_repository", {}).get("full_name") == REPOSITORY,
            "Source repository differs")
    require(isinstance(artifacts, list) and all(isinstance(item, dict) for item in artifacts),
            "Invalid artifact listing")
    ids = [item.get("id") for item in artifacts]
    require(all(type(item) is int and item > 0 for item in ids) and len(ids) == len(set(ids)),
            "Duplicate or invalid artifact IDs")
    name = f"fundstrace-windows-x64-candidate-{run_id}"
    matches = [item for item in artifacts if item.get("name") == name]
    require(len(matches) == 1, "Expected exactly one original candidate artifact")
    artifact = matches[0]
    origin = artifact.get("workflow_run", {})
    require(artifact.get("expired") is False and type(artifact.get("size_in_bytes")) is int and
            artifact["size_in_bytes"] > 0 and origin.get("id") == int(run_id) and
            origin.get("head_sha") == commit, "Artifact expired or has a different source")
    return {"sourceRun": str(run_id), "sourceCommit": commit,
            "artifactId": artifact["id"], "artifactName": name,
            "artifactDigest": artifact.get("digest")}


def extract_artifact(archive, destination):
    """Only plain unique files under an exclusive directory; no extractall."""
    destination = Path(destination)
    destination.mkdir()
    with zipfile.ZipFile(archive) as zipped:
        names = set()
        folded = set()
        for item in zipped.infolist():
            require(item.orig_filename == item.filename, "Artifact member name was normalized")
            name = item.filename.rstrip("/") if item.is_dir() else item.filename
            safe_name(name)
            require(name not in names and name.casefold() not in folded,
                    "Duplicate or case-colliding artifact member")
            names.add(name)
            folded.add(name.casefold())
            mode = item.external_attr >> 16
            require(not stat.S_ISLNK(mode), "Artifact links are forbidden")
            target = destination.joinpath(*PurePosixPath(name).parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(item) as source, target.open("xb") as output:
                    while block := source.read(1024 * 1024):
                        output.write(block)


def records(candidate):
    values = candidate.get("files")
    require(isinstance(values, list) and values, "Candidate file records missing")
    result = {}
    folded = set()
    for value in values:
        require(isinstance(value, dict), "Invalid candidate file record")
        name = safe_name(value.get("path"))
        require(name not in result and name.casefold() not in folded and
                type(value.get("bytes")) is int and value["bytes"] >= 0 and
                isinstance(value.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]),
                "Duplicate or malformed candidate file record")
        result[name] = value
        folded.add(name.casefold())
    return result


def verify_file(path, expected):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and
            path.stat().st_size == expected["bytes"] and digest(path) == expected["sha256"],
            f"File bytes or SHA differ: {expected['path']}")


def verify_artifact(root, version, target_sha256, target_bytes, baseline):
    root = Path(root)
    manifests = list(root.rglob("windows-candidate.json"))
    require(len(manifests) == 1, "Expected one original candidate manifest")
    base = manifests[0].parent
    require(all(file.is_relative_to(base) for file in root.rglob("*") if file.is_file()),
            "Artifact has files outside its evidence root")
    candidate = read_json(manifests[0])
    require(candidate.get("schemaVersion") == 1 and candidate.get("candidate") == "unsigned-installer" and
            candidate.get("platform") == "win32" and candidate.get("arch") == "x64",
            "Candidate platform or kind differs")
    checks = candidate.get("checks", {})
    require(checks and all(value == "passed" for value in checks.values()) and
            checks.get("installerLifecycle") == "passed", "Original candidate checks are incomplete")
    require(all(str(candidate.get("executableVersion", {}).get(key, "")).split(".")[:3] == version.split(".")
                for key in ("fileVersion", "productVersion")), "Candidate version differs")
    require(candidate.get("upgradeBaseline") == baseline and
            candidate.get("installerUpgradeFrom") == baseline["prior"]["version"],
            "Candidate upgrade baseline differs")
    expected = records(candidate)
    installer_name = f"FundsTrace-{version}-windows-x64-unsigned.exe"
    installer_path = PREFIX + "electron/" + installer_name
    require(set(expected) == {installer_path} | {name for name in expected if name.startswith(PAYLOAD)} and
            PAYLOAD + "FundsTrace.exe" in expected and
            PAYLOAD + "resources/backend/build-manifest.json" in expected,
            "Candidate payload or installer set differs")
    installer_record = expected[installer_path]
    require(installer_record["sha256"] == target_sha256 and installer_record["bytes"] == target_bytes,
            "Original installer differs from the frozen inputs")
    sums = {}
    for line in (base / "windows-candidate.sha256").read_text(encoding="utf-8-sig").splitlines():
        parts = line.split("  ", 1)
        require(len(parts) == 2 and re.fullmatch(r"[0-9a-f]{64}", parts[0]), "Malformed original checksum")
        name = safe_name(parts[1])
        require(name.startswith(PREFIX) and name not in sums, "Duplicate or outside checksum path")
        sums[name] = parts[0]
    actual = {}
    for file in base.rglob("*"):
        require(not file.is_symlink(), "Artifact links forbidden")
        if file.is_file() and file != base / "windows-candidate.sha256":
            name = PREFIX + file.relative_to(base).as_posix()
            require(name in sums and digest(file) == sums[name], "Original artifact checksum mismatch")
            actual[name] = file
    # Installer artifacts deliberately omit win-unpacked; all other checksum
    # records must be physically present before NSIS preparation starts.
    require(set(sums) - set(actual) == {name for name in sums if name.startswith(PAYLOAD)},
            "Original artifact evidence is incomplete")
    require(all(sums.get(name) == item["sha256"] for name, item in expected.items()),
            "Candidate files and original checksums differ")
    require({name for name in sums if name.startswith(PAYLOAD)} == {name for name in expected if name.startswith(PAYLOAD)},
            "Payload checksum set differs")
    installer = base / "electron" / installer_name
    verify_file(installer, installer_record)
    with installer.open("rb") as stream:
        require(stream.read(2) == b"MZ", "Original installer is not a PE file")
    native = base / "backend/funds-trace-backend/build-manifest.json"
    require(digest(native) == candidate.get("nativeManifestSha256"), "Original native manifest differs")
    native_value = read_json(native)
    require(native_value.get("platform") == "win32" and native_value.get("arch") == "x64" and
            native_value.get("protocol") == 1, "Original backend identity differs")
    require(checks.get("freshInstallerLifecycle") == "passed" and
            digest(base / "fresh-standard-user/compatibility.json") == candidate.get("freshUserCompatibilitySha256"),
            "Original fresh-user declaration or digest missing")
    fresh = read_json(base / "fresh-standard-user/compatibility.json")
    require(fresh.get("status") == "passed" and fresh.get("mode") == "fresh" and
            fresh.get("targetVersion") == version and fresh.get("currentInstallerSha256") == target_sha256 and
            fresh.get("currentInstallerBytes") == target_bytes, "Original fresh-user identity differs")
    return {"evidenceRoot": str(base), "installerPath": str(installer),
            "originalManifestSha256": digest(manifests[0]),
            "originalChecksumsSha256": digest(base / "windows-candidate.sha256"),
            "nativeManifestSha256": digest(native), "targetVersion": version,
            "targetSha256": target_sha256, "targetBytes": target_bytes,
            "upgradeBaseline": baseline}


def verify_payload(source, payload_root):
    base = Path(source["evidenceRoot"])
    require(digest(base / "windows-candidate.json") == source["originalManifestSha256"] and
            digest(base / "windows-candidate.sha256") == source["originalChecksumsSha256"],
            "Original manifests changed after download")
    candidate = read_json(base / "windows-candidate.json")
    expected = {name[len(PAYLOAD):]: value for name, value in records(candidate).items() if name.startswith(PAYLOAD)}
    root = Path(payload_root)
    actual = {}
    for file in root.rglob("*"):
        require(not file.is_symlink(), "Payload links forbidden")
        if file.is_file():
            actual[file.relative_to(root).as_posix()] = file
    require(set(actual) == set(expected), "Unpacked payload exact file set differs")
    for name, file in actual.items():
        verify_file(file, expected[name])
    require(candidate["nativeManifestSha256"] == source["nativeManifestSha256"] ==
            digest(base / "backend/funds-trace-backend/build-manifest.json") ==
            digest(root / "resources/backend/build-manifest.json"), "The three native manifests differ")
    return {"status": "passed", "fileCount": len(actual), "nativeManifestSha256": source["nativeManifestSha256"]}


def validate_receipt(value):
    require(value.get("source") == SOURCE_KIND and value.get("rebuilt") is False and
            "targetReleaseTag" not in value and "productionInAppUpdate" not in value and
            value.get("sourceCommit") == value.get("testCommit"), "Recheck receipt source semantics differ")
    require(re.fullmatch(r"[0-9a-f]{40}", value.get("testCommit", "")) and
            re.fullmatch(r"[1-9][0-9]*", value.get("sourceRun", "")) and
            re.fullmatch(r"[1-9][0-9]*", value.get("testRun", "")) and
            value["sourceRun"] != value["testRun"] and type(value.get("artifactId")) is int and
            value["artifactId"] > 0 and value.get("artifactName") ==
            f"fundstrace-windows-x64-candidate-{value['sourceRun']}", "Recheck receipt identity differs")
    require(value.get("status") == "passed" and value.get("reports") and
            all(re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) and type(item["bytes"]) is int
                for item in value["reports"].values()), "Recheck subreport digests missing")
    require(all(isinstance(value.get(key), str) and re.fullmatch(r"[0-9a-f]{64}", value[key]) for key in
                ("targetSha256", "originalManifestSha256", "originalChecksumsSha256", "nativeManifestSha256")) and
            type(value.get("targetBytes")) is int and value["targetBytes"] > 0 and
            value.get("upgradeBaseline", {}).get("targetVersion") == value.get("targetVersion"),
            "Recheck receipt EXE, manifest or baseline identity missing")
    return value


def verify_first_preferences(fresh):
    """Keep the first real GUI's bounded original JSON, not only its digest."""
    encoded = fresh.get("firstStartupPreferencesBase64")
    size = fresh.get("firstStartupPreferencesBytes")
    expected_sha = fresh.get("firstStartupPreferencesSha256")
    require(isinstance(encoded, str) and 0 < len(encoded) <= 21848 and
            type(size) is int and 0 < size <= 16384 and isinstance(expected_sha, str) and
            re.fullmatch(r"[0-9a-f]{64}", expected_sha), "First startup preferences original evidence missing")
    try:
        original = base64.b64decode(encoded, validate=True)
        require(base64.b64encode(original).decode("ascii") == encoded and len(original) == size and
                hashlib.sha256(original).hexdigest() == expected_sha, "First startup preferences bytes or SHA differ")
        def reject_constant(_):
            raise ValueError("Non-JSON preference constant")
        value = json.loads(original.decode("utf-8-sig"), parse_constant=reject_constant)
        require(isinstance(value, dict), "First startup preferences must be a JSON object")
    except (ValueError, UnicodeError) as error:
        raise ValueError("First startup preferences original JSON is invalid") from error


def collect_reports(report_root, source):
    """Bind this run's completed lifecycles, including their safe screenshots."""
    report_root = Path(report_root)
    reports = {}
    for folder in (report_root, report_root / "standard-user", report_root / "fresh-standard-user"):
        for file in sorted(folder.iterdir()):
            require(not file.is_symlink(), "Recheck output links forbidden")
            if file.is_dir():
                continue
            require(not file.is_symlink() and file.suffix in {".json", ".png"}, "Unexpected recheck output file")
            if file.suffix == ".json":
                parsed = read_json(file)
                if "status" in parsed:
                    require(parsed["status"] == "passed", "Recheck child report did not pass")
            reports[file.relative_to(report_root).as_posix()] = {"bytes": file.stat().st_size, "sha256": digest(file)}
    required = {"native-installer-smoke.json", "native-installed-app-smoke.json", "native-import-recovery-smoke.json",
                "standard-user/compatibility.json", "standard-user/native-installer-smoke.json",
                "fresh-standard-user/compatibility.json", "fresh-standard-user/native-installer-smoke.json",
                "fresh-standard-user/fresh-startup-smoke.json", "fresh-standard-user/installed-formats-smoke.json"}
    require(required <= set(reports), "Recheck lifecycle reports missing")
    for prefix in ("", "standard-user/"):
        installer = read_json(report_root / (prefix + "native-installer-smoke.json"))
        validate_installer_identity(installer, source["upgradeBaseline"], source["targetSha256"], source["targetBytes"])
        require(installer.get("checks", {}).get("cleanup") == "passed", "Upgrade lifecycle cleanup missing")
    for prefix in ("standard-user/", "fresh-standard-user/"):
        compatibility = read_json(report_root / (prefix + "compatibility.json"))
        require(compatibility.get("status") == "passed" and compatibility.get("checks") and
                compatibility["checks"].get("cleanup") is True and
                all(value is True for value in compatibility["checks"].values()), "User cleanup or compatibility incomplete")
    fresh = read_json(report_root / "fresh-standard-user/native-installer-smoke.json")
    wrapper = read_json(report_root / "fresh-standard-user/compatibility.json")
    for value in (fresh, wrapper):
        require(value.get("mode") == "fresh" and value.get("targetVersion") == source["targetVersion"] and
                value.get("currentInstallerSha256") == source["targetSha256"] and
                value.get("currentInstallerBytes") == source["targetBytes"], "Fresh lifecycle identity differs")
    require(fresh.get("fixtureOrigin") == "current-seeded" and fresh.get("checks") and
            all(value == "passed" for value in fresh["checks"].values()) and
            all(fresh["checks"].get(key) == "passed" for key in
                ("freshInstalled", "freshStartup", "frozenBackendFormats", "cleanup")) and
            all(fresh["checks"].get(key) != "passed" for key in
                ("priorInstalled", "upgradedExistingCaseData", "legacyCasePackageCompatible")),
            "Fresh lifecycle or cleanup incomplete")
    verify_first_preferences(fresh)
    startup = read_json(report_root / "fresh-standard-user/fresh-startup-smoke.json")
    formats = read_json(report_root / "fresh-standard-user/installed-formats-smoke.json")
    require(startup.get("mode") == "fresh-startup" and startup.get("status") == "passed" and
            FRESH_STARTUP_CHECKS <= startup.get("checks", {}).keys() and
            all(value is True for value in startup["checks"].values()),
            "Fresh first startup evidence incomplete")
    require(formats.get("status") == "passed" and FORMAT_CHECKS <= formats.get("checks", {}).keys() and
            all(value is True for value in formats["checks"].values()), "Frozen format evidence incomplete")
    return reports


def gh_json(endpoint):
    return json.loads(subprocess.check_output(["gh", "api", f"repos/{REPOSITORY}/{endpoint}"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("download")
    for key in ("run-id", "commit", "source-root", "project-root", "sha256"):
        download.add_argument("--" + key, required=True)
    download.add_argument("--bytes", type=int, required=True)
    payload = commands.add_parser("verify-payload")
    payload.add_argument("--source", required=True)
    payload.add_argument("--payload-root", required=True)
    receipt = commands.add_parser("receipt")
    for key in ("source", "report-root", "commit", "test-run", "output"):
        receipt.add_argument("--" + key, required=True)
    args = parser.parse_args()
    if args.command == "download":
        require(re.fullmatch(r"[0-9a-f]{64}", args.sha256) and 0 < args.bytes <= 2**53 - 1,
                "Frozen installer digest and byte count required")
        source_root = Path(args.source_root)
        require(not source_root.exists(), "SourceRoot must be new and exclusive")
        project = Path(args.project_root)
        version = read_json(project / "desktop/package.json")["version"]
        baseline = load_baseline(project / "desktop/upgrade-baseline.json", version)
        run = gh_json(f"actions/runs/{args.run_id}")
        artifacts = []
        page = 1
        while True:
            listing = gh_json(f"actions/runs/{args.run_id}/artifacts?per_page=100&page={page}")
            batch = listing["artifacts"]
            artifacts.extend(batch)
            if len(batch) < 100:
                require(len(artifacts) == listing["total_count"], "Incomplete artifact listing")
                break
            page += 1
        origin = validate_source(run, artifacts, args.run_id, args.commit)
        source_root.mkdir(parents=True)
        archive = source_root / "original-artifact.zip"
        with archive.open("xb") as output:
            subprocess.run(["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{origin['artifactId']}/zip"],
                           stdout=output, check=True)
        artifact_digest = origin["artifactDigest"]
        require(isinstance(artifact_digest, str) and artifact_digest == "sha256:" + digest(archive),
                "Downloaded artifact digest differs from the selected artifact ID")
        extract_artifact(archive, source_root / "artifact")
        origin.update(verify_artifact(source_root / "artifact", version, args.sha256, args.bytes, baseline))
        origin.update(source=SOURCE_KIND, rebuilt=False)
        write_json(source_root / "source.json", origin)
    elif args.command == "verify-payload":
        value = verify_payload(read_json(args.source), args.payload_root)
        print(json.dumps(value))
    else:
        value = read_json(args.source)
        current = verify_artifact(value["evidenceRoot"], value["targetVersion"], value["targetSha256"],
                                  value["targetBytes"], value["upgradeBaseline"])
        require(all(current[key] == value[key] for key in current), "Original evidence changed during recheck")
        value.update(schemaVersion=1, status="passed", testRun=args.test_run, testCommit=args.commit,
                     reports=collect_reports(args.report_root, value))
        validate_receipt(value)
        write_json(args.output, value)


if __name__ == "__main__":
    main()
