"""Create a native, console PyInstaller backend from an explicit source/resource boundary.

Run with the Python 3.11 environment synced to requirements-desktop.lock. This does
not cross-compile, install dependencies, sign, publish, or use application data.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
import subprocess
import struct
import sys

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "desktop"
STATIC_NAMES = (
    "index.html", "analysis.html", "license.html", "license.js", "app.js",
    "analysis.js", "style.css", "money.js", "graph-view.js", "graph-layout.js",
    "graph-labels.js", "graph-viewport.js", "graph-minimap.js", "logo.svg", "favicon.svg",
    "vendor/g6.min.js", "vendor/G6-PATCH.md", "vendor/G6-LICENSE",
)


def native_arch() -> str:
    if struct.calcsize("P") != 8:
        raise SystemExit("Use a native 64-bit Python build")
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        return "x64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    raise SystemExit(f"Unsupported native architecture: {machine}")


def resource_files() -> list[tuple[Path, str]]:
    resources = [(ROOT / name, name) for name in ("LICENSE", "THIRD_PARTY_NOTICES.md")]
    resources += [(ROOT / "static" / name, str(Path("static") / name)) for name in STATIC_NAMES]
    dist = ROOT / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("Build frontend/dist before packaging the desktop backend")
    for source in sorted(dist.rglob("*")):
        if source.is_symlink():
            raise SystemExit("Frontend resource symlinks are not accepted")
        if source.is_file():
            if source.suffix not in {".html", ".js", ".css", ".svg", ".woff", ".woff2", ".png", ".ico"}:
                raise SystemExit(f"Unexpected frontend build resource: {source.relative_to(ROOT)}")
            resources.append((source, str(source.relative_to(ROOT))))
    for source, _ in resources:
        if source.is_symlink() or not source.is_file():
            raise SystemExit(f"Missing or unsafe distribution resource: {source.relative_to(ROOT)}")
    return resources


def main() -> None:
    if sys.version_info[:2] != (3, 11):
        raise SystemExit("Use Python 3.11 with requirements-desktop.lock")
    if sys.platform not in {"win32", "darwin", "linux"}:
        raise SystemExit("Unsupported native build platform")
    if importlib.metadata.version("pyinstaller") != "6.22.3":
        raise SystemExit("Sync the pinned requirements-desktop.lock first")
    entry = ROOT / "desktop_backend.py"
    if not entry.is_file():
        raise SystemExit("desktop_backend.py is missing")
    arch = native_arch()
    resources = resource_files()
    for location in (BUILD, BUILD / "backend", BUILD / "work", BUILD / "spec"):
        if location.is_symlink():
            raise SystemExit("Desktop build paths must not be symlinks")
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
            "--name", "funds-trace-backend", "--distpath", str(BUILD / "backend"),
            "--workpath", str(BUILD / "work"), "--specpath", str(BUILD / "spec"), "--paths", str(ROOT),
            "--collect-all", "duckdb", "--collect-all", "uvicorn", "--collect-data", "matplotlib",
            "--hidden-import", "app.analysis_routes", "--hidden-import", "openpyxl",
            "--exclude-module", "desktop_synthetic", "--exclude-module", "scripts.preview_synthetic"]
    for package in ("duckdb", "fastapi", "uvicorn", "pandas", "openpyxl", "python-multipart", "matplotlib"):
        args.extend(["--recursive-copy-metadata", package])
    for source, relative in resources:
        destination = str(Path(relative).parent)
        args.extend(["--add-data", f"{source}:{destination}"])
    args.append(str(entry))
    subprocess.run(args, cwd=ROOT, check=True)
    manifest = {"protocol": 1, "platform": sys.platform, "arch": arch,
                "python": platform.python_version(), "pyinstaller": "6.22.3",
                "sourceEntrySha256": hashlib.sha256(entry.read_bytes()).hexdigest(),
                "resources": {relative.replace("\\", "/"): hashlib.sha256(source.read_bytes()).hexdigest()
                              for source, relative in resources}}
    target = BUILD / "backend" / "funds-trace-backend" / "build-manifest.json"
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Native backend created: {target.parent}")


if __name__ == "__main__":
    main()
