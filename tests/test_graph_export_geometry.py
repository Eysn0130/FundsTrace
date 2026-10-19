"""Export-only sampling geometry preserves exact labels and screen state."""
from pathlib import Path
import subprocess


def test_graph_export_geometry_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test", str(root / "tests/graph_export_geometry_harness.mjs")],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
