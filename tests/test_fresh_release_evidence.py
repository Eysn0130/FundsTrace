"""Fresh installation evidence is declared, exact, and version compatible."""
import json
import base64
import zipfile

import pytest

from scripts import release_evidence as evidence
from scripts.transfer_windows_candidate import standard_archive
from test_release_evidence import (
    SOURCE, RUN, candidate, sha, write_json, startup_candidate,
    rebind_candidate, standard_contents,
)


FORMAT_CHECKS = (
    "fourFormatsExact", "deterministicControls", "sourceReportsAndTraces", "publicRawPackageFacts",
    "cancellationChainExact", "lateConflictExact", "suffixlessPackageExact", "cleanProcessRestore",
    "reimportAddsZero", "desktopAuthentication", "activationFree", "ownedRuntime", "eofShutdown", "cleanup",
)


def fresh_candidate(candidate):
    base, old = startup_candidate(candidate)
    installer = {**old, "path": "build/desktop/electron/FundsTrace-0.1.13-windows-x64-unsigned.exe"}
    rebind_candidate(base, installer, tag="v0.1.13")
    fresh = standard_contents(base)
    for name in evidence.UPDATE_SCREENSHOTS:
        fresh.pop(name, None)
    identity = {"schemaVersion": 1, "status": "passed", "mode": "fresh", "targetVersion": "0.1.13",
                "currentInstallerSha256": installer["sha256"], "currentInstallerBytes": installer["bytes"]}
    fresh["compatibility.json"] = json.dumps({**identity, "checks": {"cleanup": True}}).encode()
    lifecycle = json.loads(fresh["native-installer-smoke.json"])
    lifecycle.update(identity, fixtureOrigin="current-seeded")
    preferences = b'{ "fund_window_state": {"x": 7} }\r\n'
    lifecycle.update(firstStartupPreferencesBase64=base64.b64encode(preferences).decode("ascii"),
                     firstStartupPreferencesSha256=sha(preferences), firstStartupPreferencesBytes=len(preferences))
    lifecycle["checks"].update({key: "passed" for key in (
        "freshInstalled", "freshStartup", "frozenBackendFormats", "cleanup")})
    fresh["native-installer-smoke.json"] = json.dumps(lifecycle).encode()
    fresh["fresh-startup-smoke.json"] = json.dumps({
        "schemaVersion": 1, "status": "passed", "mode": "fresh-startup",
        "checks": {key: True for key in (
            "freshStateInitiallyAbsent", "installedExecutableStarted", "authenticatedWorkspaceLoaded",
            "defaultCaseEmpty", "activationFree", "noPageExceptions", "normalClose",
            "backendReleasedAfterClose", "clientCreatedDefaultData")}}).encode()
    fresh["installed-formats-smoke.json"] = json.dumps({
        "schemaVersion": 1, "status": "passed", "checks": dict.fromkeys(FORMAT_CHECKS, True)}).encode()
    folder = base / "fresh-standard-user"
    folder.mkdir()
    for name, value in fresh.items():
        (folder / name).write_bytes(value)
    standard_archive(folder, base / evidence.FRESH_ARCHIVE, fresh)
    value = json.loads((base / "windows-candidate.json").read_text())
    value["checks"]["freshInstallerLifecycle"] = "passed"
    value["freshUserCompatibilitySha256"] = sha(fresh["compatibility.json"])
    write_json(base / "windows-candidate.json", value)
    sums = base / "windows-candidate.sha256"
    with sums.open("a") as output:
        output.write(f"{sha((base / 'windows-candidate.json').read_bytes())}  build/desktop/windows-candidate.json\n")
        for name, contents in fresh.items():
            output.write(f"{sha(contents)}  build/desktop/fresh-standard-user/{name}\n")
    # Replace the earlier candidate hash, while retaining all original-report bindings.
    lines = sums.read_text().splitlines()
    candidate_line = lines[-len(fresh)-1]
    lines = [line for line in lines if not line.endswith("  build/desktop/windows-candidate.json")]
    sums.write_text("\n".join([*lines, candidate_line]) + "\n")
    summary = json.loads((base / "release-verification.json").read_text())
    summary["freshUserArchiveSha256"] = sha((base / evidence.FRESH_ARCHIVE).read_bytes())
    write_json(base / "release-verification.json", summary)
    return base, installer, fresh


def test_fresh_archive_roundtrip_and_historical_contract(candidate, tmp_path):
    base, installer, fresh = fresh_candidate(candidate)
    output = tmp_path / "archive"
    manifest = evidence.prepare_evidence(base, output, tag="v0.1.13", source_commit=SOURCE,
                                         source_run=RUN, installer=installer)
    assert evidence.verify_evidence(output) == manifest
    assert (output / "evidence" / evidence.FRESH_ARCHIVE).read_bytes() == (base / evidence.FRESH_ARCHIVE).read_bytes()
    assert set(evidence._standard_contents(output / "evidence" / evidence.FRESH_ARCHIVE)) == set(fresh)
    assert evidence.fresh_declaration({"checks": {}}, "v0.1.12") is False
    with pytest.raises(ValueError, match="requires fresh"):
        evidence.fresh_declaration({"checks": {}}, "v0.1.13")


@pytest.mark.parametrize("field", ["check", "digest"])
def test_fresh_declaration_cannot_be_partial(field):
    value = {"checks": {"freshInstallerLifecycle": "passed"}, "freshUserCompatibilitySha256": "a" * 64}
    if field == "check":
        value["checks"].clear()
    else:
        value.pop("freshUserCompatibilitySha256")
    with pytest.raises(ValueError, match="declaration"):
        evidence.fresh_declaration(value, "v0.1.12")


@pytest.mark.parametrize("attack", ["mode", "sha", "bytes", "version", "cleanup", "format", "prior", "extra", "hash",
                                   "preferences-missing", "preferences-encoding", "preferences-hash", "preferences-size"])
def test_fresh_source_identity_checks_and_allowlist_are_required(candidate, tmp_path, attack):
    base, installer, fresh = fresh_candidate(candidate)
    name = "native-installer-smoke.json"
    value = json.loads(fresh[name])
    if attack == "mode": value["mode"] = "upgrade"
    elif attack == "sha": value["currentInstallerSha256"] = "b" * 64
    elif attack == "bytes": value["currentInstallerBytes"] += 1
    elif attack == "version": value["targetVersion"] = "0.1.12"
    elif attack == "cleanup": value["checks"]["cleanup"] = "failed"
    elif attack == "prior": value["checks"]["priorInstalled"] = "passed"
    elif attack == "preferences-missing": value.pop("firstStartupPreferencesBase64")
    elif attack == "preferences-encoding": value["firstStartupPreferencesBase64"] += "\n"
    elif attack == "preferences-hash": value["firstStartupPreferencesSha256"] = "0" * 64
    elif attack == "preferences-size": value["firstStartupPreferencesBytes"] += 1
    elif attack == "format":
        name = "installed-formats-smoke.json"
        value = json.loads(fresh[name]); value["checks"].pop("cancellationChainExact")
    elif attack == "extra": fresh["unapproved.json"] = b'{}'
    elif attack == "hash": fresh["native-installed-app.png"] += b'changed'
    fresh[name] = json.dumps(value).encode()
    if attack != "hash":
        # Bind the changed reports too: the semantic guard must reject these
        # even when the outer artifact digests have been honestly regenerated.
        sums = base / "windows-candidate.sha256"
        lines = [line for line in sums.read_text().splitlines()
                 if "  build/desktop/fresh-standard-user/" not in line]
        lines.extend(f"{sha(contents)}  build/desktop/fresh-standard-user/{item}"
                     for item, contents in fresh.items())
        sums.write_text("\n".join(lines) + "\n")
    with zipfile.ZipFile(base / evidence.FRESH_ARCHIVE, "w") as archive:
        for item, contents in fresh.items(): archive.writestr(item, contents)
    summary = json.loads((base / "release-verification.json").read_text())
    summary["freshUserArchiveSha256"] = sha((base / evidence.FRESH_ARCHIVE).read_bytes())
    write_json(base / "release-verification.json", summary)
    with pytest.raises(ValueError):
        evidence.prepare_evidence(base, tmp_path / "archive", tag="v0.1.13", source_commit=SOURCE,
                                  source_run=RUN, installer=installer)
