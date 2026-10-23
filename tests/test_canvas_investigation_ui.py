"""Synthetic investigation controller UI events, projection and stale-frame guards."""
from pathlib import Path
import subprocess


def test_canvas_investigation_ui():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test", str(root / "tests/canvas_investigation_ui_harness.mjs")],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
