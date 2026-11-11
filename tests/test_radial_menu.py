"""Public radial-menu behavior with disposable DOM/G6 fixtures only."""
from pathlib import Path
import subprocess


def test_radial_menu_geometry_actions_and_lifecycle():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test", str(root / "tests/radial_menu_harness.mjs")],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
