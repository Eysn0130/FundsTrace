"""Run the complete synthetic regression suite without using business state."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="funds-trace-tests-") as temporary:
        env = dict(os.environ)
        env["FUND_DATA_DIR"] = str(Path(temporary) / "data")
        env["MPLCONFIGDIR"] = str(Path(temporary) / "matplotlib")
        # Public synthetic fixture configuration, never an inherited real key.
        env["FUNDS_TRACE_LICENSE_SECRET"] = "synthetic-regression-key-not-for-use"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.call(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *sys.argv[1:]],
            cwd=root, env=env,
        )


if __name__ == "__main__":
    raise SystemExit(main())
