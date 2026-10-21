"""Validate the explicit, source-controlled Windows installer upgrade pair."""
import argparse
import hashlib
import json
from pathlib import Path
import re


REPOSITORY = "Eysn0130/FundsTrace"


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value):
        raise ValueError("Upgrade versions must be stable three-part versions")
    return tuple(map(int, value.split(".")))


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate upgrade baseline field")
        value[key] = item
    return value


def validate_baseline(value, package_version):
    """Return portable evidence identity; never accept a stale target after a bump."""
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "targetVersion", "prior"}:
        raise ValueError("Unexpected upgrade baseline fields")
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise ValueError("Unsupported upgrade baseline schema")
    target = _version(value["targetVersion"])
    _version(package_version)
    if value["targetVersion"] != package_version:
        raise ValueError("Upgrade baseline target differs from desktop package version")
    prior = value["prior"]
    if not isinstance(prior, dict) or set(prior) != {"version", "sha256", "bytes"}:
        raise ValueError("Unexpected prior installer fields")
    if _version(prior["version"]) >= target:
        raise ValueError("Prior installer must be older than the target")
    if not isinstance(prior["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", prior["sha256"]):
        raise ValueError("Prior installer SHA-256 must be lowercase hexadecimal")
    if type(prior["bytes"]) is not int or not 0 < prior["bytes"] <= 2**53 - 1:
        raise ValueError("Prior installer byte count must be a positive safe integer")
    # Hash normalized JSON so Windows CRLF checkout does not change identity.
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    version = prior["version"]
    return {"schemaVersion": 1, "targetVersion": value["targetVersion"], "prior": dict(prior),
            "baselineSha256": hashlib.sha256(canonical).hexdigest(),
            "priorInstallerUrl": f"https://github.com/{REPOSITORY}/releases/download/v{version}/"
                                 f"FundsTrace-{version}-windows-x64-unsigned.exe"}


def load_baseline(path, package_version):
    return parse_baseline(Path(path).read_text(encoding="utf-8-sig"), package_version)


def parse_baseline(content, package_version):
    value = json.loads(content, object_pairs_hook=_object)
    return validate_baseline(value, package_version)


def validate_installer_identity(report, baseline, current_sha256, current_bytes):
    """Bind lifecycle evidence to this commit's pair and the transferred EXE."""
    expected = {"upgradeBaseline": baseline, "priorVersion": baseline["prior"]["version"],
                "currentVersion": baseline["targetVersion"],
                "priorInstallerSha256": baseline["prior"]["sha256"],
                "priorInstallerBytes": baseline["prior"]["bytes"],
                "currentInstallerSha256": current_sha256, "currentInstallerBytes": current_bytes}
    for name, value in expected.items():
        if (name not in report or type(report[name]) is not type(value)
                or json.dumps(report[name], sort_keys=True) != json.dumps(value, sort_keys=True)):
            raise ValueError(f"Installer report differs from verified pair: {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    package = json.loads(args.package.read_text(encoding="utf-8-sig"))
    print(json.dumps(load_baseline(args.baseline, package["version"]), separators=(",", ":")))


if __name__ == "__main__":
    main()
