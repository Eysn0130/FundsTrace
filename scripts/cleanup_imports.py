"""Plan or apply retention cleanup against an explicitly selected managed runtime."""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="Explicit runtime directory containing fund.duckdb")
    parser.add_argument("--case-id", type=int, help="Limit to one existing case; omit to include deleted-case GC queues")
    parser.add_argument("--apply", action="store_true", help="Delete eligible registered files; default only reports the plan")
    args = parser.parse_args()
    directory = args.data_dir.expanduser().resolve()
    if not (directory / "fund.duckdb").is_file():
        parser.error("Select an existing managed runtime containing fund.duckdb")
    os.environ["FUND_DATA_DIR"] = str(directory)
    os.environ["MPLCONFIGDIR"] = str(directory / "mpl-cache")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app import db
    from app.services import imports
    from app.services.cases import ServiceError

    db._start_ckpt = lambda: None
    try:
        result = imports.cleanup_uploads(case_id=args.case_id, dry_run=not args.apply)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["failed"] else 0
    except ServiceError as error:
        print(json.dumps({"error": {"code": error.code, "message": error.message}}, ensure_ascii=False))
        return 1
    finally:
        with db._lock:
            if db._con is not None:
                db._con.close()
                db._con = None


if __name__ == "__main__":
    raise SystemExit(main())
