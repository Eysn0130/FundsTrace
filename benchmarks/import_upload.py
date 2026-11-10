"""Synthetic HTTP upload baseline, without importing the app in the driver.

Run ``python -B -m benchmarks.import_upload --help`` from the repository root.
Each invocation owns a new loopback socket, server, case and retained runtime.
The measured interval starts immediately before multipart upload and ends after
completion, a separate persisted-receipt GET, and the first G6 flow-page GET.
Input preparation, application startup and post-stop read-only verification are
reported separately. These are server HTTP observations, not GUI/release proof.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import datetime as dt
from decimal import Decimal
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import traceback
import zipfile
import csv

from benchmarks import runner


ROOT = runner.ROOT
SCHEMA = "funds-trace-import-upload.v1"
SEED = 20261105
MEMBERS = ("synthetic/bank.csv", "synthetic/platform.csv")
BANK_HEADERS = runner.HEADERS
PLATFORM_HEADERS = ["订单号", "交易时间", "本方账号", "本方户名", "对方账号", "对方户名",
                    "收/支", "交易金额", "交易后余额", "币种", "交易是否成功", "交易方式", "摘要", "来源平台"]
require = runner.require


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def comparable_receipt(value):
    # v1's ImportResult fills nullable optional summaries absent from legacy
    # result_json. Normalize only those declared adapter defaults, not facts.
    return {key: item for key, item in value.items()
            if key not in {"feedback", "table_summary", "resolution", "source_id"} or item is not None}


def fingerprint():
    paths = sorted((ROOT / "app").rglob("*.py")) + [ROOT / "benchmarks/runner.py",
        Path(__file__), ROOT / "tests/test_import_upload_benchmark.py",
        ROOT / "requirements.txt", ROOT / "requirements-dev.lock"]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    return {"sha256": runner.semantic_digest(hashes), "files": hashes}


def fixture_fact(index, rows, seed):
    """Fixture specification using integer cents; no product parsing or queries."""
    platform = index >= rows // 2
    own = "000SYN-PLATFORM" if platform else runner.SELF_ACCOUNT
    counter = f"000SYN-COUNTER-{(index * 37 + seed) % 256:04d}"
    outgoing = index % 2 == 0
    return {"trade_no": f"SYN-{'P' if platform else 'B'}-{index:09d}",
        "trade_time": (runner.timestamp(index) + dt.timedelta(microseconds=123456)).isoformat(sep=" "),
        "payer_account": own if outgoing else counter,
        "payee_account": counter if outgoing else own,
        "amount": runner.money_text(runner.cents(index, seed)),
        "balance": None if index % 3 == 0 else runner.money_text(50_000_000 + index),
        "currency": "USD" if index % 7 == 0 else "CNY", "self_account": own,
        "member_path": MEMBERS[int(platform)],
        "member_row": index - (rows // 2 if platform else 0) + 2,
        "outgoing": outgoing}


def expected_summary(rows, seed):
    totals, directions = defaultdict(int), defaultdict(lambda: {"rows": 0, "cents": 0})
    nulls = 0
    for index in range(rows):
        currency = "USD" if index % 7 == 0 else "CNY"
        value = runner.cents(index, seed)
        totals[currency] += value
        key = f"{currency}:{'out' if index % 2 == 0 else 'in'}"
        directions[key]["rows"] += 1
        directions[key]["cents"] += value
        nulls += index % 3 == 0
    return {"rows": rows, "null_balances": nulls,
            "currency_totals": {k: runner.money_text(v) for k, v in sorted(totals.items())},
            "direction_cents": {k: {"rows": v["rows"], "cents": str(v["cents"])}
                                for k, v in sorted(directions.items())},
            "confirmed_events": 0, "unresolved_observations": rows}


def verify_first_page(page, rows, seed):
    expected = expected_summary(rows, seed)
    require(page["total"] == rows and len(page["rows"]) == min(50, rows), "First flow query count differs")
    expected_funds = {"policy": "evidence-bounded-funds-v1", "status": "bounded",
        "observation_count": rows, "confirmed_event_count": 0, "unresolved_observation_count": rows,
        "count": {"lower": 0, "upper": rows},
        "totals": [{"currency": currency, "amount": None, "lower": "0.00", "upper": amount}
                   for currency, amount in expected["currency_totals"].items()]}
    require(page["funds"] == expected_funds, "First query lost exact currency totals or invented event certainty")
    require([row["trade_no"] for row in page["rows"]] ==
            [fixture_fact(index, rows, seed)["trade_no"] for index in range(rows - 1, max(-1, rows - 51), -1)],
            "First page ordering differs")
    for row in page["rows"]:
        fact = fixture_fact(int(row["trade_no"].rsplit("-", 1)[1]), rows, seed)
        for field in ("trade_time", "payer_account", "payee_account", "self_account", "amount", "balance", "currency"):
            require(row[field] == fact[field], f"First page {row['trade_no']} {field} differs")
        require(row["granularity"] == "unresolved_observation" and row["event_state"] == "unknown"
                and row["event_id"] is None, "First page invented a confirmed event")


def prepare_inputs(directory, rows, seed):
    directory.mkdir()
    files = []
    for member_index, member in enumerate(MEMBERS):
        path = directory / Path(member).name
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(PLATFORM_HEADERS if member_index else BANK_HEADERS)
            for index in range(rows // 2 * member_index, rows if member_index else rows // 2):
                fact = fixture_fact(index, rows, seed)
                own = fact["self_account"]
                counter = fact["payee_account"] if fact["outgoing"] else fact["payer_account"]
                common = [fact["trade_no"], fact["trade_time"], own, "合成主体",
                          counter, "合成对手"]
                if member_index:
                    writer.writerow([*common, "支出" if fact["outgoing"] else "收入", fact["amount"],
                        fact["balance"] or "", fact["currency"], "交易成功", "合成平台支付",
                        f"合成平台用途{index % 7}", "SyntheticPay"])
                else:
                    writer.writerow([*common, "合成银行", "支出" if fact["outgoing"] else "收入",
                        fact["amount"], fact["balance"] or "", fact["currency"],
                        f"合成银行用途{index % 7}", "合成银行渠道",
                        *[f"合成扩展{column}-{'x' * 24}" for column in range(8)]])
        files.append({"member_path": member, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "bytes": path.stat().st_size, "rows": rows // 2})
    archive_path = directory / "synthetic-upload.zip"
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for member in MEMBERS:
            info = zipfile.ZipInfo(member, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (directory / Path(member).name).read_bytes(), compresslevel=6)
    return {"archive": archive_path.name, "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            "bytes": archive_path.stat().st_size, "members": files, "expected": expected_summary(rows, seed)}


def isolated_environment(runtime):
    # Do not pass inherited data, credentials, provider configuration or proxies.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT",
           "WINDIR", "TMPDIR", "TMP", "TEMP") if key in os.environ}
    env.update(FUND_DATA_DIR=str(runtime / "data"), MPLCONFIGDIR=str(runtime / "matplotlib"),
               PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0",
               FUNDS_TRACE_LICENSE_SECRET="synthetic-upload-benchmark-not-for-use")
    return env


class TreeSampler:
    """Sample simultaneous RSS of the owned PID and descendants using OS ps.

    This is a sampled lower bound, not ru_maxrss, allocation, or unique physical
    memory. Shared resident pages may be counted more than once in a tree sum.
    ps and the driver are excluded. Missing samples invalidate RSS evidence.
    """
    def __init__(self, pid, path, interval):
        self.pid, self.path, self.interval = pid, path, interval
        self.stop_event = threading.Event()
        self.samples, self.errors = [], []
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        with self.path.open("w", encoding="utf-8") as stream:
            while not self.stop_event.is_set():
                try:
                    result = subprocess.run(["ps", "-axo", "pid=,ppid=,rss="],
                                            capture_output=True, text=True, check=True)
                    processes = [tuple(map(int, line.split())) for line in result.stdout.splitlines()]
                    owned = {self.pid}
                    while True:
                        descendants = {pid for pid, ppid, _ in processes if ppid in owned}
                        if descendants <= owned:
                            break
                        owned |= descendants
                    selected = {pid: rss * 1024 for pid, _, rss in processes if pid in owned}
                    sample = {"monotonic": time.perf_counter(), "rss_bytes": sum(selected.values()),
                              "processes": selected}
                    if self.pid in selected:
                        self.samples.append(sample)
                        stream.write(json.dumps(sample) + "\n")
                        stream.flush()
                except Exception as error:
                    self.errors.append(f"{type(error).__name__}: {error}")
                self.stop_event.wait(self.interval)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=10)
        require(not self.thread.is_alive(), "RSS sampler did not stop")

    def summary(self, start, end):
        samples = [s for s in self.samples if start <= s["monotonic"] <= end]
        require(samples and not self.errors, f"Missing RSS evidence: {self.errors}")
        times = [start, *[s["monotonic"] for s in samples], end]
        return {"peak_bytes": max(s["rss_bytes"] for s in samples), "samples": len(samples),
                "max_sample_gap_seconds": max(b - a for a, b in zip(times, times[1:])),
                "requested_interval_seconds": self.interval}


def serve(fd, runtime):
    # The hidden entry is independently safe; it cannot trust a parent launch.
    with socket.socket(fileno=fd) as sock:
        require(sock.family == socket.AF_INET
                and sock.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_STREAM
                and sock.getsockname()[0] == "127.0.0.1" and sock.getsockname()[1] > 0,
                "Server requires an IPv4 loopback listener")
        # SO_ACCEPTCONN is not readable on every POSIX socket implementation.
        # A listener either accepts a queued startup probe or would block.
        sock.setblocking(False)
        try:
            connection, _ = sock.accept()
        except BlockingIOError:
            pass
        except OSError:
            require(False, "Server requires an IPv4 loopback listener")
        else:
            connection.close()
        finally:
            sock.setblocking(True)
        require(runtime.is_absolute() and runtime.is_dir() and runtime.resolve() == runtime,
                "Server requires an existing canonical isolated runtime")
        require(os.environ.get("FUND_DATA_DIR") == str(runtime / "data"), "Isolated data directory mismatch")
        require(os.environ.get("MPLCONFIGDIR") == str(runtime / "matplotlib"), "Isolated matplotlib directory mismatch")
        require(not (runtime / "data").exists(), "Server requires a fresh data directory")
        clean_environment = isolated_environment(runtime)
        os.environ.clear()
        os.environ.update(clean_environment)
        import uvicorn
        from app.main import app
        from app import db
        settings = db.con().execute("select name,value from duckdb_settings() where name in "
                                   "('threads','memory_limit','disabled_optimizers')").fetchall()
        write_json(runtime.parent / "database-settings.json", dict(settings))
        config = uvicorn.Config(app, host="127.0.0.1", port=0, access_log=False, log_level="warning")
        uvicorn.Server(config).run(sockets=[sock])


def verify_persisted(run_dir, rows, seed, case_id, source_id, result):
    """After service exit: independent, read-only DB and retained byte checks."""
    import duckdb
    started = time.perf_counter()
    expected = expected_summary(rows, seed)
    manifest = json.loads((run_dir / "input-manifest.json").read_text())
    with duckdb.connect(str(run_dir / "runtime/data/fund.duckdb"), read_only=True,
                        config={"threads": 2}) as connection:
        receipt = connection.execute("select stage,result_json from import_sessions where case_id=? and import_id=?",
                                     [case_id, source_id]).fetchone()
        require(receipt and receipt[0] == "done" and comparable_receipt(json.loads(receipt[1])) == comparable_receipt(result),
                "Durable receipt differs from independently fetched receipt")
        columns = ["trade_no", "trade_time", "payer_account", "payee_account", "amount", "balance",
                   "currency", "self_account"]
        actual = connection.execute("select " + ",".join(columns) + " from flows where case_id=? order by trade_no",
                                    [case_id]).fetchall()
        require(len(actual) == rows, f"Stored {len(actual)} != expected {rows}")
        totals, directions = defaultdict(int), defaultdict(lambda: {"rows": 0, "cents": 0})
        seen = set()
        for values in actual:
            row = dict(zip(columns, values))
            index = int(row["trade_no"].rsplit("-", 1)[1])
            require(0 <= index < rows and index not in seen, "Missing or duplicate fixture index")
            seen.add(index)
            fact = fixture_fact(index, rows, seed)
            for key in columns:
                target = dt.datetime.fromisoformat(fact[key]) if key == "trade_time" else (
                    Decimal(fact[key]) if key in {"amount", "balance"} and fact[key] is not None else fact[key])
                require(row[key] == target, f"{fact['trade_no']} {key}: {row[key]!r} != {target!r}")
            cents = int(row["amount"] * 100)
            totals[row["currency"]] += cents
            direction = f"{row['currency']}:{'out' if row['payer_account'] == row['self_account'] else 'in'}"
            directions[direction]["rows"] += 1
            directions[direction]["cents"] += cents
        require({k: runner.money_text(v) for k, v in sorted(totals.items())} == expected["currency_totals"],
                "Currency totals differ")
        require({k: {"rows": v["rows"], "cents": str(v["cents"])} for k, v in sorted(directions.items())}
                == expected["direction_cents"], "Direction totals differ")
        require(sum(r[5] is None for r in actual) == expected["null_balances"], "Null balances differ")
        linked = connection.execute("""select f.trade_no,r.member_path,r.source_row,r.disposition,s.disposition,
            s.source_row,r.record_no from flows f join flow_sources s on s.case_id=f.case_id and s.flow_id=f.id
            join import_records r on r.case_id=s.case_id and r.source_id=s.source_id and r.record_no=s.source_row
            where f.case_id=? and s.source_id=? order by f.trade_no""", [case_id, source_id]).fetchall()
        require(len(linked) == rows, "Source link count differs")
        for trade, member, member_row, disposition, support, source_row, record_no in linked:
            fact = fixture_fact(int(trade.rsplit("-", 1)[1]), rows, seed)
            require((member, member_row, disposition, support, source_row) ==
                    (fact["member_path"], fact["member_row"], "imported", "imported", record_no),
                    f"Source position or support differs for {trade}")
        counts = connection.execute("select disposition,count(*) from import_records where case_id=? and source_id=? group by 1",
                                    [case_id, source_id]).fetchall()
        require(dict(counts) == {"imported": rows}, f"Unexpected source dispositions: {counts}")
        sources = connection.execute("select filename,sha256,size_bytes,parser_version from import_sources where case_id=? and source_id=?",
                                     [case_id, source_id]).fetchone()
        require(sources and sources[1:3] == (manifest["sha256"], manifest["bytes"]), "Source digest differs")
        members = connection.execute("select member_path,sha256 from import_members where case_id=? and source_id=? order by member_path",
                                     [case_id, source_id]).fetchall()
        require(dict(members) == {m["member_path"]: m["sha256"] for m in manifest["members"]}, "Member digests differ")
        all_rows = connection.execute("select count(*) from flows").fetchone()[0]
        require(all_rows == rows, "Flows escaped the only synthetic case")
    original = run_dir / f"runtime/data/uploads/{source_id}.zip"
    require(hashlib.sha256(original.read_bytes()).hexdigest() == manifest["sha256"], "Retained upload differs")
    return {"all_passed": True, "expected": expected, "every_flow_and_source_checked": rows,
            "durable_receipt_after_service_exit": True, "retained_original_sha256": manifest["sha256"],
            "parser_version": sources[3], "wall_seconds_outside_timing": time.perf_counter() - started}


def run(args):
    import httpx
    run_dir = args.output.resolve()
    require(not run_dir.exists(), "Output already exists; preserve existing measurements")
    run_dir.mkdir(parents=True)
    runtime = run_dir / "runtime"
    runtime.mkdir()
    before = fingerprint()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if args.expected_head:
        require(head == args.expected_head, f"HEAD {head} != expected {args.expected_head}")
    record = {"schema_version": SCHEMA, "status": "running",
              "mode": "correctness-smoke" if args.rows < 10000 else "single-observation",
              "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "head": head,
              "source_fingerprint": before, "environment": runner.environment(),
              "parameters": {"rows": args.rows, "seed": args.seed, "probes": False,
                  "timeout_seconds": args.timeout, "rss_interval_seconds": args.rss_interval},
              "policy": {"entry": "G6 legacy upload/commit/progress; durable v1 session GET; G6 first flow page",
                  "wall": "continuous from immediately before upload request through fully decoded first flow response",
                  "excluded": "input generation, server startup, case creation, expensive verification and shutdown",
                  "rss": "sampled simultaneous sum of service PID and descendants; driver/input generator/ps excluded; "
                         "lower bound, shared pages may be double counted; no OS cache flushing or CPU exclusivity",
                  "product_settings": "unaltered defaults; normal activation-free app; no synthetic runtime patch"}}
    record["environment"]["dependencies"].update({name: importlib.metadata.version(name)
        for name in ("uvicorn", "httpx", "starlette", "pydantic", "python-multipart")})
    record["environment"]["load_average_before"] = list(os.getloadavg())
    write_json(run_dir / "result.json", record)
    prepare_start = time.perf_counter()
    manifest = prepare_inputs(run_dir / "inputs", args.rows, args.seed)
    write_json(run_dir / "input-manifest.json", manifest)
    record["input_preparation_seconds"] = time.perf_counter() - prepare_start
    env = isolated_environment(runtime)
    require(not (runtime / "data").exists(), "Runtime was not fresh")
    process = sampler = None
    stages, responses = {}, {}
    chain_start = chain_end = None
    try:
        with socket.socket() as listener, (run_dir / "server.log").open("w") as log:
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            port = listener.getsockname()[1]
            startup = time.perf_counter()
            command = [sys.executable, "-B", "-m", "benchmarks.import_upload", "--serve-fd", str(listener.fileno()),
                       "--runtime", str(runtime)]
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log,
                                       pass_fds=(listener.fileno(),), start_new_session=True)
            listener.close()
            sampler = TreeSampler(process.pid, run_dir / "rss.jsonl", args.rss_interval)
            sampler.start()
            address = f"http://127.0.0.1:{port}"
            with httpx.Client(base_url=address, trust_env=False, timeout=args.timeout) as client:
                deadline = time.perf_counter() + 60
                while True:
                    require(process.poll() is None, "Owned service exited before startup")
                    try:
                        ready = client.get("/api/v1/cases", timeout=.5)
                        if ready.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    require(time.perf_counter() < deadline, "Owned service startup timed out")
                    time.sleep(.05)
                record["startup_seconds"] = time.perf_counter() - startup
                created = client.post("/api/v1/cases", json={"name": "Synthetic upload benchmark"})
                require(created.status_code == 201, f"Case creation failed: {created.text}")
                case_id = created.json()["id"]
                record["case_id"] = case_id
                def request(name, method, url, **kwargs):
                    start = time.perf_counter()
                    response = client.request(method, url, **kwargs)
                    value = response.json()
                    end = time.perf_counter()
                    stages[name] = {"start_seconds": start - chain_start, "end_seconds": end - chain_start,
                                    "wall_seconds": end - start, "http_status": response.status_code}
                    responses[name] = value
                    require(response.is_success, f"{name} HTTP {response.status_code}: {value}")
                    return value
                with (run_dir / "inputs" / manifest["archive"]).open("rb") as content:
                    chain_start = time.perf_counter()
                    view = request("upload_preview", "POST", "/api/import/preview", params={"case_id": case_id},
                        files={"file": (manifest["archive"], content, "application/zip")})
                source_id = view["import_id"]
                record["import_id"] = source_id
                started = request("confirm", "POST", "/api/import/commit", json={"case_id": int(case_id),
                    "import_id": source_id, "mode": view["mode"], "mapping": view["mapping"], "mark_accounts": ""})
                require(started["import_id"] == source_id, "Commit changed import binding")
                wait_start = time.perf_counter()
                polls = []
                while True:
                    poll_start = time.perf_counter()
                    response = client.get("/api/import/progress", params={"case_id": case_id, "import_id": source_id})
                    job = response.json()
                    polls.append({"start_seconds": poll_start - chain_start,
                                  "wall_seconds": time.perf_counter() - poll_start,
                                  "http_status": response.status_code, "stage": job.get("stage")})
                    responses["completion"] = job
                    require(response.is_success, f"Progress failed: {job}")
                    if job["stage"] in {"done", "error"}:
                        break
                    require(time.perf_counter() - chain_start < args.timeout, "Import completion timed out")
                    time.sleep(.05)
                stages["completion_wait"] = {"start_seconds": wait_start - chain_start,
                    "end_seconds": time.perf_counter() - chain_start,
                    "wall_seconds": time.perf_counter() - wait_start, "polls": polls}
                require(job["stage"] == "done", f"Import reported failure: {job}")
                # A fresh client and the discovery route read result_json again;
                # do not reuse the progress reply as the durable receipt.
                receipt_start = time.perf_counter()
                with httpx.Client(base_url=address, trust_env=False, timeout=args.timeout) as independent:
                    response = independent.get(f"/api/v1/cases/{case_id}/imports", params={"import_id": source_id})
                    receipt = response.json()
                receipt_end = time.perf_counter()
                stages["persisted_receipt_get"] = {"start_seconds": receipt_start - chain_start,
                    "end_seconds": receipt_end - chain_start, "wall_seconds": receipt_end - receipt_start,
                    "http_status": response.status_code}
                responses["persisted_receipt_get"] = receipt
                require(response.is_success and len(receipt["items"]) == 1 and receipt["items"][0]["stage"] == "done",
                        f"Independent receipt unavailable: {receipt}")
                require(comparable_receipt(receipt["items"][0]["result"]) == comparable_receipt(job["result"]),
                        "Independent receipt differs from completion")
                first = request("first_flow_query", "GET", "/api/flows", params={"case_id": case_id, "page": 1, "size": 50})
                chain_end = time.perf_counter()
                record["timing"] = {"continuous_wall_seconds": chain_end - chain_start, "stages": stages}
                verify_first_page(first, args.rows, args.seed)
                require(job["result"]["ok"] == args.rows and job["result"].get("rows_dup", 0) == 0,
                        "Receipt counts differ from independent expectation")
                record["rss_chain"] = sampler.summary(chain_start, chain_end)
                record["rss_startup_and_chain"] = sampler.summary(startup, chain_end)
                write_json(run_dir / "http-responses.json", responses)
            process.terminate()
            process.wait(timeout=30)
            sampler.stop()
            require(process.returncode in (0, -15), f"Service exit {process.returncode}")
        record["checks"] = verify_persisted(run_dir, args.rows, args.seed, case_id, source_id,
                                            receipt["items"][0]["result"])
        require(fingerprint() == before, "Source changed during measurement")
        record["source_fingerprint_after"] = fingerprint()
        record["status"] = "passed"
        record["rows_per_second"] = args.rows / record["timing"]["continuous_wall_seconds"]
    except Exception as error:
        record["status"] = "failed"
        record["error"] = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
        record["timing"] = record.get("timing", {"stages": stages})
        if chain_start and chain_end:
            record["observed_wall_seconds_not_a_performance_score"] = chain_end - chain_start
        record["rows_per_second"] = None
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if sampler is not None and sampler.thread.is_alive():
            sampler.stop()
        record["service_stopped"] = process is None or process.poll() is not None
        record["environment"]["load_average_after"] = list(os.getloadavg())
        write_json(run_dir / "http-responses.json", responses)
        write_json(run_dir / "result.json", record)
    print(json.dumps({"status": record["status"], "output": str(run_dir / "result.json"),
                      "error": record.get("error", {}).get("message")}, ensure_ascii=False))
    return 0 if record["status"] == "passed" else 1


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10000, help="Total bank + platform rows, even, 2..100000")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-head", help="Refuse a different source revision")
    parser.add_argument("--timeout", type=float, default=180, help="HTTP wait/chain deadline in seconds")
    parser.add_argument("--rss-interval", type=float, default=.05)
    parser.add_argument("--serve-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--runtime", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.serve_fd is not None:
        if args.runtime is None:
            parser.error("Internal server requires runtime")
        return args
    if args.output is None:
        parser.error("--output is required and must be a new directory")
    if sys.platform == "win32":
        parser.error("This socket/ps sampler currently requires POSIX; not Windows evidence")
    if not 2 <= args.rows <= 100000 or args.rows % 2:
        parser.error("--rows must be even, 2..100000")
    if not 0 <= args.seed <= 2147483647:
        parser.error("--seed must be 0..2147483647")
    if not .01 <= args.rss_interval <= 1 or not 10 <= args.timeout <= 600:
        parser.error("--rss-interval must be .01..1; --timeout must be 10..600")
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.serve_fd is not None:
        serve(args.serve_fd, args.runtime)
        return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
