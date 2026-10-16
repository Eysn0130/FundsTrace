"""Select source checks and enforce their aggregate GitHub Actions result."""

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


GROUPS = ("python", "react", "desktop")
DOC_MEDIA = {
    ".avif", ".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".svg",
    ".webp", ".mp4", ".mov", ".webm", ".eot", ".otf", ".ttf", ".woff",
    ".woff2",
}
DESKTOP_INPUTS = {
    "desktop_backend.py", "desktop_synthetic.py", "requirements-desktop.in",
    "requirements-desktop.lock", "scripts/build_desktop_backend.py",
    "scripts/build-windows.ps1", "scripts/test-windows-installer.ps1",
}
PYTHON_SCRIPTS = {
    "scripts/benchmark.py", "scripts/cleanup_imports.py",
    "scripts/patch_g6_validation.py", "scripts/preview_synthetic.py",
}


def classify(paths):
    """Use a small allowlist; unrecognized inputs require every check group."""
    required = set()
    for path in paths:
        name = PurePosixPath(path).name
        suffix = PurePosixPath(path).suffix.lower()
        if name.lower() in {"agents.md", "claude.md"}:
            required.update(GROUPS)
        elif suffix == ".md" or path == "LICENSE":
            continue
        elif path.startswith("docs/") and (
            suffix in DOC_MEDIA
            or (path.startswith("docs/brand/licenses/") and suffix == ".txt")
        ):
            continue
        elif path in DESKTOP_INPUTS or path.startswith("desktop/"):
            required.update(("python", "desktop"))
        elif path.startswith("frontend/"):
            required.add("react")
        elif path.startswith(("contracts/", "app/api/")) or path == "scripts/export_api_contract.py":
            required.update(("python", "react"))
        elif path.startswith(("app/", "static/", "tests/", "benchmarks/")) or path in {
            "run.py", "run.bat", *PYTHON_SCRIPTS,
        }:
            required.add("python")
        else:
            required.update(GROUPS)
    return {group: "true" if group in required else "false" for group in GROUPS}


def changed_paths(base, head, root):
    for label, revision in (("base", base), ("head", head)):
        if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
            raise ValueError(f"{label} must be a full 40-character commit SHA")
    result = subprocess.run(
        ["git", "diff", "--name-only", "-z", "--no-renames", f"{base}...{head}"],
        cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    # Disabling rename detection retains both the deleted and added paths.
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def check_gate(needs):
    jobs = {"plan", *GROUPS}
    if not isinstance(needs, dict) or set(needs) != jobs:
        raise ValueError("CI_NEEDS must contain exactly plan, python, react and desktop")
    if not all(isinstance(needs[job], dict) for job in jobs):
        raise ValueError("each CI job must have a result object")
    if needs["plan"].get("result") != "success":
        raise ValueError("plan did not succeed")
    outputs = needs["plan"].get("outputs")
    if not isinstance(outputs, dict) or set(outputs) != set(GROUPS) or any(
        not isinstance(outputs[group], str) or outputs[group] not in {"true", "false"}
        for group in GROUPS
    ):
        raise ValueError("plan outputs must be exactly python/react/desktop with true/false strings")
    for group in GROUPS:
        expected = "success" if outputs[group] == "true" else "skipped"
        actual = needs[group].get("result")
        if actual != expected:
            raise ValueError(f"{group} must be {expected}; got {actual!r}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="select check groups from a commit diff")
    plan.add_argument("--base", required=True)
    plan.add_argument("--head", required=True)
    commands.add_parser("gate", help="validate CI_NEEDS job results")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            paths = changed_paths(args.base, args.head, Path(__file__).resolve().parents[1])
            outputs = classify(paths)
            output_file = os.environ.get("GITHUB_OUTPUT")
            if output_file:
                with open(output_file, "a", encoding="utf-8") as output:
                    output.writelines(f"{group}={outputs[group]}\n" for group in GROUPS)
            print(f"CI plan ({len(paths)} changed paths): " + ", ".join(
                f"{group}={outputs[group]}" for group in GROUPS
            ))
        else:
            check_gate(json.loads(os.environ.get("CI_NEEDS", "")))
            print("CI gate passed")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"CI {args.command} failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
