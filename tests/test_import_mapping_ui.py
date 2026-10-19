"""Synthetic import matrix rendering and lossless mapping, without a server."""
from pathlib import Path
import subprocess


def test_import_mapping_ui_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/import_mapping_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
