"""Embedded analysis case ownership, drafts and stale callbacks with synthetic DOM."""
from pathlib import Path
import subprocess


def test_embedded_analysis_link_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/analysis_link_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
