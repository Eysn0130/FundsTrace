"""Synthetic public display command, serialization and pinned-layout regressions."""
from pathlib import Path
import subprocess


def test_canvas_display_features():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test", str(root / "tests/canvas_display_features_harness.mjs")],
        cwd=root, capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
