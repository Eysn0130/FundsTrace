"""Canvas command serialization and binding safety use synthetic host adapters."""
from pathlib import Path
import subprocess


def test_canvas_workspace_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/canvas_workspace_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
