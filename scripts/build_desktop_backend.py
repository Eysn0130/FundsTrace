"""Create a native, console PyInstaller backend from an explicit source/resource boundary.

Run with the Python 3.11 environment synced to requirements-desktop.lock. This does
not cross-compile, install dependencies, sign, publish, or use application data.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import re
from pathlib import Path
import subprocess
import struct
import sys

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "desktop"
UI_ICON_NAMES = (
    "device-floppy", "file-plus", "file-export", "arrow-back-up", "arrow-forward-up",
    "layout-distribute-horizontal", "arrows-maximize", "refresh", "chevron-left",
    "chevron-right", "chevron-up", "chevron-down", "trash", "settings", "upload",
    "download", "user-search", "copy", "pencil", "search", "filter", "x",
    "layout-sidebar-left-collapse", "layout-sidebar-left-expand", "check", "dots",
    "network", "selection", "selection-clear", "swap", "list", "group", "link",
    "layers", "chart", "route", "play", "star", "shape", "photo", "align-left",
    "align-top", "distribute-vertical", "lock", "focus", "zoom-out", "zoom-in",
    "zoom-reset", "eye", "eye-off", "eye-restore", "legend", "map", "calendar",
    "target", "font-text", "home", "folder",
)
STATIC_NAMES = (
    "navigation.css", "appearance.css", "appearance.js", "analysis-link.js", "import-workflow.js",
    "workspace-drafts.js", "workspace-session.js", "account-workspace.js", "app-navigation.js",
    "index.html", "analysis.html", "license.html", "license.js", "app.js",
    "analysis.js", "analysis.css", "case-workspace.js", "table-scroll.js", "style.css", "money.js", "graph-view.js", "graph-layout.js", "graph-layout-worker.js", "graph-layout-quality.js", "graph-routing.js", "graph-route-quality.js",
    "graph-labels.js", "graph-viewport.js", "graph-minimap.js", "graph-selection.js", "logo.png", "favicon.ico", "favicon-32.png",
    "vendor/g6.min.js", "vendor/G6-PATCH.md", "vendor/G6-LICENSE",
    "workspace-shell.js", "canvas-history.js", "canvas-editor.js", "canvas-investigation.js", "desktop-updates.js", "desktop-updates.css",
    "graph-relations.js", "graph-presentation.js", "graph-label-geometry.js", "graph-export-geometry.js", "ribbon.js", "ribbon-commands.js",
    "toolbar-menu.js", "relation-filter.js", "neighborhood.js", "node-finder.js", "canvas-workspace.js", "data-tools.js", "workspace-camera.js", "main-accounts.js", "main-accounts-view.js",
    "table-workspace.js", "ui-dialogs.js", "workflow-ui.js", "ui-icons/LICENSE",
    "entity-picker.js", "entity-picker.css", "entity-search.js", "entity-search.css",
    *(f"ui-icons/{name}.svg" for name in UI_ICON_NAMES),
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
    resources = [(ROOT / name, name) for name in (
        "LICENSE", "THIRD_PARTY_NOTICES.md", "LICENSES/MIT.txt",
        "docs/licensing.md", "docs/licensing.en.md",
    )]
    resources += [(ROOT / "static" / name, str(Path("static") / name)) for name in STATIC_NAMES]
    public_key = ROOT / "activation-public-key.pem"
    if public_key.exists():
        from cryptography.exceptions import UnsupportedAlgorithm
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        if public_key.is_symlink() or not public_key.is_file() or public_key.stat().st_size > 4096:
            raise SystemExit("Invalid activation public key file")
        pem = public_key.read_bytes().strip()
        if not re.fullmatch(rb"-----BEGIN PUBLIC KEY-----\r?\n(?:[A-Za-z0-9+/=]+\r?\n)+-----END PUBLIC KEY-----", pem):
            raise SystemExit("Activation resource must contain only one public key and no extra content")
        try:
            key = serialization.load_pem_public_key(pem)
        except (ValueError, TypeError, UnsupportedAlgorithm):
            raise SystemExit("Activation configuration must contain an Ed25519 public key, never a private key")
        if not isinstance(key, Ed25519PublicKey):
            raise SystemExit("Only an Ed25519 activation public key is supported")
        resources.append((public_key, "activation-public-key.pem"))
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
    for package in ("duckdb", "fastapi", "uvicorn", "pandas", "openpyxl", "xlrd", "olefile", "python-multipart", "matplotlib", "cryptography"):
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
