"""Runtime locations shared by database, licensing and launch logging."""
import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", ROOT))
else:
    ROOT = Path(__file__).resolve().parent.parent
    BUNDLE_DIR = ROOT

# Keep the existing environment name and database filename for compatibility.
DATA_DIR = Path(os.environ.get("FUND_DATA_DIR", ROOT / "data")).expanduser().resolve()
