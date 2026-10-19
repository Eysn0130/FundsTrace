"""Case-bound data tools exercise real controller with synthetic host adapters."""
from pathlib import Path
import subprocess


def test_data_tools_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/data_tools_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
