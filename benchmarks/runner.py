"""Synthetic, process-isolated measurements of the existing Python/SQL paths.

Application imports belong inside ``run_sample`` after its temporary environment
has been installed. The parent process never imports an application module.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
from decimal import Decimal
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

try:
    import resource
except ImportError:  # Windows provides the same process high-water metric via Win32.
    resource = None


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "funds-trace-benchmark.v1"
DEFAULT_SEED = 20261014
SELF_ACCOUNT = "0000000000000001"
LARGE_AMOUNT_CENTS = 900719925474099113
RESULT_PREFIX = "FUNDSTRACE_BENCHMARK_RESULT="
HEADERS = [
    "交易流水号", "交易时间", "本方账号", "本方户名", "对方账号", "对方户名",
    "对方开户行", "收支标志", "交易金额", "交易后余额", "币种", "摘要", "交易渠道",
    *[f"扩展字段{i}" for i in range(8)],
]


class CorrectnessError(RuntimeError):
    """An independently expected invariant was not met."""


def require(condition, message):
    if not condition:
        raise CorrectnessError(message)


def cents(index, seed):
    # One value above binary64's exact cents range detects float round trips.
    return LARGE_AMOUNT_CENTS if index == 2 else (index * 7919 + seed) % 1_000_000 + 1


def unique_valid_indices(rows):
    return (i for i in range(rows) if i % 100 not in (0, 1) and i % 10 != 9)


def timestamp(index):
    return dt.datetime(2026, 1, 1) + dt.timedelta(seconds=index * 181)


def money_text(value):
    return f"{value // 100}.{value % 100:02d}"


def semantic_digest(metrics):
    return hashlib.sha256(json.dumps(metrics, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def expected_metrics(rows, seed):
    """Integer arithmetic on the fixture specification, never importer output."""
    count = outgoing = total = outgoing_total = 0
    counters = set()
    first = last = None
    for i in unique_valid_indices(rows):
        value = cents(i, seed)
        count += 1
        total += value
        if i % 2 == 0:
            outgoing += 1
            outgoing_total += value
        counters.add((i * 37 + seed) % 256)
        if first is None:
            first = timestamp(i)
        last = timestamp(i)
    skipped = 2 * (rows // 100) + min(rows % 100, 2)
    return {
        "source_rows": rows, "accepted_rows": rows - skipped,
        "skipped_rows": skipped, "duplicate_rows": rows // 10,
        "stored_rows": count, "amount_cents": total,
        "outgoing_rows": outgoing, "outgoing_cents": outgoing_total,
        "incoming_rows": count - outgoing, "incoming_cents": total - outgoing_total,
        "accounts": len(counters) + 1,
        "first_time": first.isoformat(sep=" "), "last_time": last.isoformat(sep=" "),
    }


def write_fixture(path, rows, seed):
    # Stream raw rows to a temporary file; no complete raw fixture is retained.
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADERS)
        for source_index in range(rows):
            i = source_index - 1 if source_index % 10 == 9 else source_index
            when = timestamp(i).strftime("%Y/%m/%d %H:%M:%S" if i % 17 == 0 else "%Y-%m-%d %H:%M:%S")
            if i % 100 == 0:
                when = "无效日期"
            value = cents(i, seed)
            amount = money_text(value)
            if i % 7 == 0:
                amount = f"￥{Decimal(value) / 100:,.2f}"
            if i % 100 == 1:
                amount = "--"
            counter = 1000 + (i * 37 + seed) % 256
            writer.writerow([
                f"SYN-{i:09d}", when, SELF_ACCOUNT, "合成主体",
                f"{counter:016d}", f"合成对手{counter}", "" if i % 13 == 0 else "合成银行",
                "支出" if i % 2 == 0 else "收入", amount,
                "" if i % 13 == 0 else money_text(50_000_000 + i),
                "" if i % 11 == 0 else "CNY", "" if i % 11 == 0 else f"合成用途{i % 7}", "合成渠道",
                *[f"合成扩展{column}-{'x' * 24}" for column in range(8)],
            ])


def source_fingerprint():
    paths = sorted((ROOT / "app").rglob("*.py")) + [
        ROOT / "benchmarks/runner.py", ROOT / "scripts/benchmark.py",
        ROOT / "requirements.txt", ROOT / "requirements-dev.lock",
    ]
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    return {"sha256": semantic_digest(hashes), "files": hashes}


def environment():
    dependencies = {}
    for name in ("duckdb", "pandas", "openpyxl", "fastapi"):
        dependencies[name] = importlib.metadata.version(name)
    memory = None
    if sys.platform == "darwin":
        result = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip().isdigit():
            memory = int(result.stdout.strip())
    elif hasattr(os, "sysconf"):
        try:
            memory = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except (ValueError, OSError):
            pass
    return {
        "python": platform.python_version(), "implementation": platform.python_implementation(),
        "platform": platform.platform(), "machine": platform.machine(), "logical_cpus": os.cpu_count(),
        "physical_memory_bytes": memory, "dependencies": dependencies,
        "lockfiles": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("requirements.txt", "requirements-dev.lock")
        },
    }


def rss_high_water_bytes():
    if resource is not None:
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if sys.platform == "darwin" else value * 1024)
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage")
        ]

    current_process = ctypes.windll.kernel32.GetCurrentProcess
    current_process.restype = wintypes.HANDLE
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
    get_memory.restype = wintypes.BOOL
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not get_memory(current_process(), ctypes.byref(counters), counters.cb):
        raise OSError("Cannot read process peak working set")
    return int(counters.PeakWorkingSetSize)


def measure(stages, name, function):
    print(f"  {name}", file=sys.stderr, flush=True)
    wall, cpu = time.perf_counter(), time.process_time()
    result = function()
    stages[name] = {
        "wall_seconds": time.perf_counter() - wall,
        "cpu_seconds": time.process_time() - cpu,
        "rss_high_water_bytes": rss_high_water_bytes(),
    }
    return result


def to_cents(value):
    number = Decimal(str(value))
    require(number.is_finite(), "Non-finite monetary value")
    return int((number * 100).quantize(Decimal("1")))


def bounded_indices(rows, limit, *, exclude_large=False):
    # The legacy graph response still uses binary64 aggregates. Its normal-range
    # subset is explicit; exact-text Excel includes the large-amount sentinel.
    result = []
    for i in unique_valid_indices(rows):
        if not exclude_large or i != 2:
            result.append(i)
            if len(result) == limit:
                break
    return result


def run_sample(args):
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    fingerprint = source_fingerprint()
    stages = {}
    expected = expected_metrics(args.rows, args.seed)
    with tempfile.TemporaryDirectory(prefix="funds-trace-benchmark-") as temporary:
        sandbox = Path(temporary).resolve()
        # Set every runtime path before the first application import. Inherited
        # business directories and license configuration are deliberately replaced.
        os.environ["FUND_DATA_DIR"] = str(sandbox / "data")
        os.environ["MPLCONFIGDIR"] = str(sandbox / "matplotlib")
        os.environ["FUNDS_TRACE_LICENSE_SECRET"] = "synthetic-benchmark-key-not-for-use"
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        sys.dont_write_bytecode = True
        sys.pycache_prefix = str(sandbox / "bytecode")
        csv_path = sandbox / "synthetic.csv"
        measure(stages, "fixture_csv_setup", lambda: write_fixture(csv_path, args.rows, args.seed))

        def initialize():
            from app import db, importer
            from app import main
            require(db.DB_PATH.resolve().is_relative_to(sandbox), "Database path escaped temporary state")
            require(not db.DB_PATH.exists(), "Benchmark must start with a nonexistent database")
            # Disable only the periodic checkpoint scheduler. Explicit checkpoint
            # is measured separately; import/transaction/query code is unchanged.
            db._start_ckpt = lambda: None
            connection = db.con()
            connection.execute(f"set threads = {args.threads}")
            return db, importer, main, connection

        db, importer, main, connection = measure(stages, "cold_application_startup", initialize)
        try:
            case_id = connection.execute("insert into cases(name) values ('Synthetic benchmark A') returning id").fetchone()[0]
            other_case = connection.execute("insert into cases(name) values ('Synthetic benchmark B') returning id").fetchone()[0]
            table, _sheet = measure(stages, "csv_parse", lambda: importer.read_table(csv_path))
            require(len(table) == args.rows, "CSV source row count differs")
            require(table["本方账号"].eq(SELF_ACCOUNT).all(), "CSV parser lost leading zeroes")

            def clean():
                mode, mapping = importer.analyze(table, "B")
                rows, stats = importer.build_rows(table, mode, mapping)
                return mode, rows, stats

            mode, rows, stats = measure(stages, "clean_build_rows", clean)
            # Stage lifetime is explicit: release the source DataFrame after the
            # clean stage; normalized rows remain for same-case reimport.
            del table
            require(stats["ok"] == expected["accepted_rows"] and stats["skipped"] == expected["skipped_rows"], "Cleaning counts differ")
            result = measure(stages, "commit_atomic", lambda: importer.commit_rows(
                "synthetic.csv", mode, rows, stats, case_id=case_id))
            require(result["ok"] == expected["stored_rows"] and result["rows_dup"] == expected["duplicate_rows"], "Initial import counts differ")
            duplicate = measure(stages, "deduplicate_same_case", lambda: importer.commit_rows(
                "synthetic-repeat.csv", mode, rows, stats, case_id=case_id))
            require(duplicate["ok"] == 0 and duplicate["rows_dup"] == expected["accepted_rows"], "Same-case repeat inserted duplicates")

            # Independent second-case fixture checks that dedup is case-local.
            cross_rows = rows[:3]
            cross_result = importer.commit_rows("synthetic-other-case.csv", mode, cross_rows, case_id=other_case)
            require(cross_result["ok"] == 3, "Cross-case import was deduplicated against the primary case")
            del rows

            def statistics_query():
                # SQL equivalent of the existing stats path, plus exact raw
                # DECIMAL sums. No HTTP/client/new-query-API dependency.
                return connection.execute("""
                    select count(*), sum(amount),
                           count(*) filter (where payer_account = ?),
                           sum(amount) filter (where payer_account = ?),
                           count(*) filter (where payee_account = ?),
                           sum(amount) filter (where payee_account = ?),
                           min(trade_time), max(trade_time), count(distinct trade_no),
                           (select count(distinct account) from (
                              select payer_account as account from flows where case_id = ?
                              union all select payee_account from flows where case_id = ?)),
                           (select count(*) from batches where case_id = ?)
                    from flows where case_id = ?
                """, [SELF_ACCOUNT] * 4 + [case_id] * 4).fetchone()

            summary = measure(stages, "query_statistics", statistics_query)

            def page_query():
                count = connection.execute("select count(*) from flows where case_id = ?", [case_id]).fetchone()[0]
                pages = []
                for offset in (0, max(0, count - 100)):
                    pages.append(connection.execute("""select * from flows where case_id = ?
                        order by trade_time desc, id desc limit 100 offset ?""", [case_id, offset]).fetchall())
                return count, pages

            page_total, pages = measure(stages, "query_pages", page_query)
            require(page_total == expected["stored_rows"], "Pagination total differs")
            require(all(len(page) == min(100, page_total) for page in pages), "Pagination page size differs")
            require(all(row[1] == case_id for page in pages for row in page), "Pagination leaked another case")
            require(pages[0][0][4] == timestamp(max(unique_valid_indices(args.rows))), "Pagination order differs")

            graph_indices = bounded_indices(args.rows, args.graph_rows, exclude_large=True)
            excel_indices = bounded_indices(args.rows, args.excel_rows)
            for canvas_id, indices in ((0, graph_indices), (1, excel_indices)):
                exclusion = "and trade_no <> 'SYN-000000002'" if canvas_id == 0 else ""
                connection.execute(f"""insert into tracked_flows(case_id, flow_id, canvas_id)
                    select ?, id, ? from flows where case_id = ? {exclusion}
                    order by trade_no limit ?""", [case_id, canvas_id, case_id, len(indices)])
            graph = measure(stages, "graph_prepare", lambda: main.graph(case_id=case_id, canvas_id=0))
            graph_cents = sum(cents(i, args.seed) for i in graph_indices)
            require(len(graph["tracked_ids"]) == len(graph_indices), "Graph tracked count differs")
            require(sum(edge["cnt"] for edge in graph["edges"]) == len(graph_indices), "Graph transaction count differs")
            require(sum(to_cents(edge["total"]) for edge in graph["edges"]) == graph_cents, "Graph aggregate amount differs")
            require(sum(to_cents(node["out_amt"]) for node in graph["nodes"]) == graph_cents, "Graph node amount differs")
            require(not main.graph(case_id=other_case, canvas_id=0)["tracked_ids"], "Graph leaked another case")
            graph_sizes = {"rows": len(graph_indices), "nodes": len(graph["nodes"]), "edges": len(graph["edges"])}
            del graph

            xlsx_path = sandbox / "synthetic-export.xlsx"

            def export_excel():
                body = main.export_tracked(case_id=case_id, canvas_id=1).body
                xlsx_path.write_bytes(body)
                return len(body)

            excel_size = measure(stages, "excel_export", export_excel)

            def integrity_checks():
                import pandas as pd

                actual = {
                    "source_rows": args.rows, "accepted_rows": stats["ok"], "skipped_rows": stats["skipped"],
                    "duplicate_rows": result["rows_dup"], "stored_rows": summary[0],
                    "amount_cents": to_cents(summary[1]), "outgoing_rows": summary[2],
                    "outgoing_cents": to_cents(summary[3]), "incoming_rows": summary[4],
                    "incoming_cents": to_cents(summary[5]), "accounts": summary[9],
                    "first_time": summary[6].isoformat(sep=" "), "last_time": summary[7].isoformat(sep=" "),
                }
                require(actual == expected, "Exact database aggregate differs from independent fixture arithmetic")
                require(summary[8] == expected["stored_rows"] and summary[10] == 2, "Trade source or batch count differs")
                malformed = connection.execute("""select count(*) from flows where case_id = ? and (
                    length(payer_account) <> 16 or length(payee_account) <> 16
                    or left(payer_account, 2) <> '00' or left(payee_account, 2) <> '00'
                    or currency <> 'CNY' or trade_no !~ '^SYN-[0-9]{9}$'
                    or ((cast(substr(trade_no, 5) as bigint) % 2 = 0) <> (payer_account = ?))
                    or ((cast(substr(trade_no, 5) as bigint) % 2 = 1) <> (payee_account = ?)))
                """, [case_id, SELF_ACCOUNT, SELF_ACCOUNT]).fetchone()[0]
                require(malformed == 0, "Account text, direction, currency or traceability contract differs")
                per_row_errors = connection.execute("""
                    with source as (
                        select *, cast(substr(trade_no, 5) as bigint) as source_index
                        from flows where case_id = ?)
                    select count(*) from source where
                        source_index >= ? or source_index % 100 in (0, 1) or source_index % 10 = 9
                        or cast(amount as decimal(38,2)) * 100 <> case when source_index = 2 then ?
                            else ((source_index * 7919 + ?) % 1000000) + 1 end
                        or trade_time <> timestamp '2026-01-01' + source_index * interval '181 seconds'
                        or case when source_index % 2 = 0 then payee_account else payer_account end
                            <> lpad(cast(1000 + ((source_index * 37 + ?) % 256) as varchar), 16, '0')
                        or (source_index % 13 = 0 and balance is not null)
                        or (source_index % 13 <> 0 and (balance is null or balance * 100 <> 50000000 + source_index))
                """, [case_id, args.rows, LARGE_AMOUNT_CENTS, args.seed, args.seed]).fetchone()[0]
                require(per_row_errors == 0, "Per-row amount, timestamp, counterpart or missing-balance invariant differs")
                sentinel = connection.execute("select amount from flows where case_id = ? and trade_no = 'SYN-000000002'", [case_id]).fetchone()[0]
                require(isinstance(sentinel, Decimal) and to_cents(sentinel) == LARGE_AMOUNT_CENTS, "Large decimal amount lost cents")
                other = connection.execute("select count(*), sum(amount) from flows where case_id = ?", [other_case]).fetchone()
                first_three = list(i for i in unique_valid_indices(10))[:3]
                require(other[0] == 3 and to_cents(other[1]) == sum(cents(i, args.seed) for i in first_three), "Other-case amounts differ")
                require(connection.execute("select count(*) from flows where case_id not in (?, ?)", [case_id, other_case]).fetchone()[0] == 0, "Unexpected case data")
                require(connection.execute("select count(*) from flows f join batches b on f.batch_id = b.id where f.case_id <> b.case_id").fetchone()[0] == 0, "Batch provenance crossed cases")

                # A failure after insert must roll the whole import back, including
                # its batch. This runs in the same transaction path as the timing.
                rollback_case = connection.execute("insert into cases(name) values ('Synthetic rollback probe') returning id").fetchone()[0]

                def fail_after_insert(done, total):
                    raise RuntimeError("Synthetic rollback probe")

                try:
                    importer.commit_rows("synthetic-rollback.csv", mode, cross_rows,
                                         progress_cb=fail_after_insert, case_id=rollback_case)
                except RuntimeError as error:
                    require(str(error) == "Synthetic rollback probe", "Unexpected rollback failure")
                else:
                    raise CorrectnessError("Rollback probe did not fail")
                require(connection.execute("select count(*) from flows where case_id = ?", [rollback_case]).fetchone()[0] == 0, "Failed import left rows")
                require(connection.execute("select count(*) from batches where case_id = ?", [rollback_case]).fetchone()[0] == 0, "Failed import left a batch")

                exported = pd.read_excel(xlsx_path, sheet_name="追踪流水", dtype={"付款账号": str, "收款账号": str, "交易流水号": str, "交易金额": str})
                require(len(exported) == len(excel_indices), "Excel row count differs")
                expected_export_total = sum(cents(i, args.seed) for i in excel_indices)
                require(sum(to_cents(value) for value in exported["交易金额"]) == expected_export_total, "Excel exact cents differ")
                export_indices = [int(value.removeprefix("SYN-")) for value in exported["交易流水号"]]
                require(export_indices == excel_indices, "Excel source ordering differs")
                require([to_cents(value) for value in exported["交易金额"]] == [cents(i, args.seed) for i in excel_indices], "Excel per-row cents differ")
                for payer, payee, index in zip(exported["付款账号"], exported["收款账号"], export_indices):
                    counter = f"{1000 + (index * 37 + args.seed) % 256:016d}"
                    require((payer, payee) == ((SELF_ACCOUNT, counter) if index % 2 == 0 else (counter, SELF_ACCOUNT)), "Excel account or direction differs")
                require(len(pd.read_excel(xlsx_path, sheet_name="待调取账号")) > 0, "Excel pending-account sheet is missing")
                public_aggregates = {key: str(value) if key.endswith("_cents") else value for key, value in actual.items()}
                return {
                    "all_passed": True, "exact_decimal_and_large_sentinel": True,
                    "leading_zero_accounts": True, "direction_dates_and_provenance": True,
                    "same_case_dedup_and_cross_case_isolation": True, "failed_import_rollback": True,
                    "graph_bounded_aggregate": True, "excel_bounded_round_trip": True,
                    "semantic_sha256": semantic_digest(public_aggregates), "aggregates": public_aggregates,
                }

            checks = measure(stages, "integrity_checks", integrity_checks)
            wal_path = Path(str(db.DB_PATH) + ".wal")
            wal_size = wal_path.stat().st_size if wal_path.exists() else 0
            measure(stages, "explicit_checkpoint", lambda: connection.execute("checkpoint"))
            sizes = {
                "csv_bytes": csv_path.stat().st_size, "duckdb_bytes": db.DB_PATH.stat().st_size,
                "wal_before_checkpoint_bytes": wal_size, "xlsx_bytes": excel_size,
            }
            require(fingerprint == source_fingerprint(), "Source changed during a repetition; rerun on a stable candidate")
            return {
                "schema_version": SCHEMA_VERSION, "rows": args.rows, "seed": args.seed,
                "stages": stages, "checks": checks, "file_sizes": sizes,
                "graph_sample": graph_sizes, "excel_sample_rows": len(excel_indices),
                "source_fingerprint": fingerprint,
                "process": {
                    "pid": os.getpid(), "peak_rss_bytes": rss_high_water_bytes(),
                    "wall_seconds": time.perf_counter() - start_wall,
                    "cpu_seconds": time.process_time() - start_cpu,
                    "isolated_temporary_database": True, "inherited_runtime_paths_replaced": True,
                },
            }
        finally:
            with db._lock:
                if db._con is not None:
                    db._con.close()
                    db._con = None


def nearest_rank(values, percentile):
    return sorted(values)[max(0, math.ceil(percentile * len(values)) - 1)]


def summarize(values):
    return {
        "samples": len(values), "median": statistics.median(values),
        "p95_nearest_rank": nearest_rank(values, 0.95), "min": min(values), "max": max(values),
    }


def aggregate_samples(args, samples, initial_fingerprint):
    require(len(samples) == args.repeats, "Missing repetitions")
    for sample in samples:
        require(sample["schema_version"] == SCHEMA_VERSION, "Unexpected worker schema")
        require(sample["rows"] == args.rows and sample["seed"] == args.seed, "Worker parameters differ")
        require(sample["checks"]["all_passed"], "Worker correctness failed")
        require(sample["source_fingerprint"] == initial_fingerprint, "Source changed between repetitions; rerun on a stable candidate")
    require(source_fingerprint() == initial_fingerprint, "Source changed before aggregation")
    summary = {}
    for name in samples[0]["stages"]:
        summary[name] = {
            "wall_seconds": summarize([sample["stages"][name]["wall_seconds"] for sample in samples]),
            "cpu_seconds": summarize([sample["stages"][name]["cpu_seconds"] for sample in samples]),
            "process_rss_high_water_bytes_max": max(sample["stages"][name]["rss_high_water_bytes"] for sample in samples),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "mode": "correctness-smoke" if args.check else "synthetic-performance-observation",
        "parameters": {
            "rows": args.rows, "repeats": args.repeats, "seed": args.seed,
            "duckdb_threads": args.threads, "graph_rows_limit": args.graph_rows,
            "excel_rows_limit": args.excel_rows,
        },
        "policy": {
            "evidence_layer": "Local synthetic Python business functions and DuckDB SQL; no HTTP, browser rendering, installed package or release qualification",
            "isolation": "Every repetition starts a fresh Python child and TemporaryDirectory; FUND_DATA_DIR, MPLCONFIGDIR and synthetic license configuration are always replaced before application imports",
            "raw_fixture_retention": "CSV, database and Excel exist only in the per-child temporary directory and are deleted; report stores aggregate metrics only",
            "money_serialization": "Aggregate integer cents are decimal strings in JSON, preserving values above the binary64 exact-integer range",
            "source_lifetime": "Source DataFrame released after build_rows; normalized rows released after repeat-dedup and the small cross-case fixture",
            "fixture": "21 text columns; Chinese; 10% repeated rows; 2% invalid date/amount rows; optional blanks; mixed date/amount formatting; 256 counterpart accounts; one large exact-decimal sentinel",
            "query": "Exact DECIMAL statistics plus count and two ordered pages of at most 100 rows; first and deep offsets; case-scoped SQL",
            "graph": "Existing main.graph on the first bounded valid unique normal-range rows; no layout or rendering; excludes large-amount sentinel",
            "excel": "Existing main.export_tracked plus temporary file write and separate round-trip validation; first bounded valid unique rows including the large exact-decimal sentinel; monetary text read back as text",
            "checkpoint": "Periodic checkpoint scheduler disabled; explicit checkpoint separately timed; transaction and import implementation unchanged",
            "cpu": "time.process_time: total CPU consumed by the child process, including its worker threads",
            "rss": "ru_maxrss (POSIX) or PeakWorkingSetSize (Windows) is the child process lifetime high-water mark, sampled after each stage; it is NOT stage-exclusive memory and must not be subtracted; parent process RSS is not used",
            "sampling": "One observation per stage per independent child; median and nearest-rank p95; with fewer than 20 repeats p95 is the maximum and is only descriptive, not a stable tail estimate",
            "warmup": "No discarded warmup; child application startup uses a fresh temporary bytecode-cache prefix per repetition; later queries use that child's newly imported database and are not cold-cache storage benchmarks",
        },
        "environment": environment(), "source_fingerprint": initial_fingerprint,
        "checks": {"all_passed": True, "semantic_sha256": samples[0]["checks"]["semantic_sha256"]},
        "summary": summary,
        "process_summary": {
            "wall_seconds": summarize([sample["process"]["wall_seconds"] for sample in samples]),
            "cpu_seconds": summarize([sample["process"]["cpu_seconds"] for sample in samples]),
            "peak_rss_bytes_max": max(sample["process"]["peak_rss_bytes"] for sample in samples),
        },
        "samples": samples,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, help="CSV source rows, 100..1000000; default 10000, or 100 with --check")
    parser.add_argument("--repeats", type=int, default=3, help="Independent child/database repetitions, 1..30 (default 3)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Fixture seed, 0..2147483647")
    parser.add_argument("--threads", type=int, default=2, help="DuckDB and numerical-library thread limit, 1..8 (default 2)")
    parser.add_argument("--graph-rows", type=int, default=2000, help="Graph preparation row cap, 1..2000 (default 2000)")
    parser.add_argument("--excel-rows", type=int, default=10000, help="Excel export row cap, 1..10000 (default 10000)")
    parser.add_argument("--check", action="store_true", help="Correctness-only smoke, 100..1000 rows; never performance evidence")
    parser.add_argument("--output", type=Path, default=ROOT / "output/benchmarks/latest.json", help="Aggregate-only JSON output")
    parser.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.rows = args.rows if args.rows is not None else (100 if args.check else 10000)
    bounds = {"rows": (100, 1_000_000), "repeats": (1, 30), "seed": (0, 2_147_483_647),
              "threads": (1, 8), "graph_rows": (1, 2000), "excel_rows": (1, 10000)}
    for name, (lower, upper) in bounds.items():
        if not lower <= getattr(args, name) <= upper:
            parser.error(f"--{name.replace('_', '-')} must be in {lower}..{upper}")
    if args.check and args.rows > 1000:
        parser.error("--check is restricted to 100..1000 rows")
    if not args.check and args.rows < 10000:
        parser.error("Performance observations require at least 10000 rows; use --check for a smaller correctness smoke")
    if args.output.suffix.lower() != ".json":
        parser.error("--output must use a .json filename")
    args.graph_rows = min(args.rows, args.graph_rows)
    args.excel_rows = min(args.rows, args.excel_rows)
    return args


def main(argv=None):
    args = parse_args(argv)
    try:
        if args._worker:
            sample = run_sample(args)
            print(RESULT_PREFIX + json.dumps(sample, sort_keys=True))
            return 0
        fingerprint = source_fingerprint()
        samples = []
        for repetition in range(args.repeats):
            print(f"Repetition {repetition + 1}/{args.repeats}: {args.rows} synthetic CSV rows", file=sys.stderr, flush=True)
            env = dict(os.environ)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
                env[name] = str(args.threads)
            command = [sys.executable, str(ROOT / "scripts/benchmark.py"), "--_worker",
                       "--rows", str(args.rows), "--repeats", "1", "--seed", str(args.seed),
                       "--threads", str(args.threads), "--graph-rows", str(args.graph_rows),
                       "--excel-rows", str(args.excel_rows)]
            if args.check:
                command.append("--check")
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, text=True, check=False)
            require(result.returncode == 0, f"Repetition {repetition + 1} exited {result.returncode}; no performance report was written")
            lines = [line[len(RESULT_PREFIX):] for line in result.stdout.splitlines() if line.startswith(RESULT_PREFIX)]
            require(len(lines) == 1, "Worker did not return exactly one structured result")
            samples.append(json.loads(lines[0]))
        report = aggregate_samples(args, samples, fingerprint)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=args.output.parent, prefix=".benchmark-", delete=False) as stream:
            temporary_output = Path(stream.name)
            json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        try:
            temporary_output.replace(args.output)
        finally:
            temporary_output.unlink(missing_ok=True)
        print(f"Wrote {args.output} ({args.repeats} isolated repetitions, all integrity checks passed)")
        return 0
    except (CorrectnessError, OSError, ValueError, KeyError) as error:
        print(f"Benchmark failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
