"""Pinned G6 validation patch: exact artifact, local CLI and real function behavior."""
import hashlib
from pathlib import Path
import runpy
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "patch_g6_validation.py"
PATCH = runpy.run_path(str(SCRIPT))


def command(path, action):
    return subprocess.run([sys.executable, str(SCRIPT), action, "--path", str(path)],
                          cwd=ROOT, capture_output=True, text=True, timeout=10)


def test_pinned_vendor_is_exactly_the_documented_patch():
    content = (ROOT / "static" / "vendor" / "g6.min.js").read_bytes()
    assert PATCH["variant"](content) == "patched"
    original = content.replace(PATCH["PATCHED"], PATCH["ORIGINAL"], 1)
    assert hashlib.sha256(original).hexdigest() == PATCH["ORIGINAL_SHA256"]
    assert PATCH["patched_bytes"](original) == content


def test_patch_cli_is_explicit_and_idempotent(tmp_path):
    current = (ROOT / "static" / "vendor" / "g6.min.js").read_bytes()
    original = current.replace(PATCH["PATCHED"], PATCH["ORIGINAL"], 1)
    artifact = tmp_path / "g6.min.js"
    artifact.write_bytes(original)
    assert command(artifact, "--check").returncode == 1
    assert artifact.read_bytes() == original
    result = command(artifact, "--apply")
    assert result.returncode == 0, result.stderr
    assert artifact.read_bytes() == current
    assert command(artifact, "--check").returncode == 0
    before = artifact.stat().st_mtime_ns
    assert command(artifact, "--apply").returncode == 0
    assert artifact.stat().st_mtime_ns == before


def test_patch_cli_rejects_unknown_bytes_without_writing(tmp_path):
    for source in [b"unrecognized synthetic file", (ROOT / "static" / "vendor" / "g6.min.js").read_bytes() + b"\n"]:
        artifact = tmp_path / "g6.min.js"
        artifact.write_bytes(source)
        for action in ["--apply", "--check"]:
            result = command(artifact, action)
            assert result.returncode == 1
            assert "unknown G6 bytes" in result.stderr
            assert artifact.read_bytes() == source


def test_patch_cli_rejects_symlink(tmp_path):
    target = tmp_path / "target.js"
    content = (ROOT / "static" / "vendor" / "g6.min.js").read_bytes()
    target.write_bytes(content)
    link = tmp_path / "g6.min.js"
    link.symlink_to(target)
    result = command(link, "--apply")
    assert result.returncode == 1
    assert "not a symlink" in result.stderr
    assert target.read_bytes() == content


def test_real_g6_validator_behavior():
    result = subprocess.run(["node", "--test", str(ROOT / "tests" / "g6_validation_harness.mjs")],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
