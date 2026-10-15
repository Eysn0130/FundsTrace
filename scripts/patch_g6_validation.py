#!/usr/bin/env python3
"""Check or apply the pinned, local G6 4.8.19 endpoint-membership patch."""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "static" / "vendor" / "g6.min.js"
ORIGINAL_SHA256 = "9832ed1b47589504cb6da481ae385a43a51f0cece60815cf5bfeaeefac3b21d7"
PATCHED_SHA256 = "c1fd44126c37e95f372f7fcf75734e7294e247d976595b95b3120eae9b7f56b0"
ORIGINAL = b"c=Object(g.__spreadArray)(Object(g.__spreadArray)([],s,!0),u,!0),l=(n||[]).find((function(t){return!c.includes(t.source)||!c.includes(t.target)}))"
PATCHED = b"c=new Set(Object(g.__spreadArray)(Object(g.__spreadArray)([],s,!0),u,!0)),l=(n||[]).find((function(t){return!c.has(t.source)||!c.has(t.target)}))"


class PatchError(ValueError):
    pass


def variant(content):
    digest = hashlib.sha256(content).hexdigest()
    if digest == ORIGINAL_SHA256 and content.count(ORIGINAL) == 1 and PATCHED not in content:
        return "original"
    if digest == PATCHED_SHA256 and content.count(PATCHED) == 1 and ORIGINAL not in content:
        return "patched"
    raise PatchError(f"unknown G6 bytes; refusing to accept or overwrite SHA-256 {digest}")


def patched_bytes(content):
    if variant(content) == "patched":
        return content
    result = content.replace(ORIGINAL, PATCHED, 1)
    if variant(result) != "patched":  # Both the complete hash and unique fragment must match.
        raise PatchError("replacement did not produce the pinned patched artifact")
    return result


def apply(path, original):
    """Replace only the exact artifact inspected above; retain its file mode."""
    result = patched_bytes(original)
    if result == original:
        return False
    mode = stat.S_IMODE(path.stat().st_mode)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".g6-validation-", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(result)
        temporary.chmod(mode)
        if path.is_symlink() or path.read_bytes() != original:
            raise PatchError("G6 changed while preparing its patch; no replacement performed")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="require the exact patched artifact; never write")
    action.add_argument("--apply", action="store_true", help="patch the exact original artifact, or accept an existing patch")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH, help="artifact path; defaults to the project vendor file")
    args = parser.parse_args(argv)
    try:
        if args.path.is_symlink() or not args.path.is_file():
            raise PatchError("G6 path must be a regular file, not a symlink")
        content = args.path.read_bytes()
        state = variant(content)
        if args.check:
            if state != "patched":
                raise PatchError("the pinned original is present but its membership patch is not applied")
            print(f"G6 validation patch verified: {PATCHED_SHA256}")
        else:
            changed = apply(args.path, content)
            print(f"G6 validation patch {'applied' if changed else 'already applied'}: {PATCHED_SHA256}")
    except (OSError, PatchError) as error:
        print(f"G6 validation patch: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
