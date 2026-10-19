"""Synthetic sidebar composition and camera ownership without node relayout."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_workspace_camera_composition_reversibility_and_export_ownership():
    result = subprocess.run(['node', '--test', str(ROOT / 'tests/workspace_camera_harness.mjs')],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
