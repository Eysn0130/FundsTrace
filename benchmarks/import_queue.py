"""Serial independent synthetic uploads; generation and full audit are untimed.

Each shape partitions the same global import_upload.fixture_fact dataset. This
driver owns a fresh loopback service and retains its isolated database/evidence.
It measures HTTP availability, never a desktop frame, Windows or a percentile.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import datetime as dt
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import zipfile

from benchmarks import import_upload as base

ROOT, require, write_json = base.ROOT, base.require, base.write_json
SCHEMA = "funds-trace-import-queue.v1"


def fingerprint():
    result = base.fingerprint()
    for path in (Path(__file__), ROOT / "tests/test_import_queue_benchmark.py"):
        result["files"][str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    result["sha256"] = base.runner.semantic_digest(result["files"])
    return result


def ranges_for(total, packages, part):
    width = total // (2 * packages)
    return [(member * (total // 2) + part * width, member * (total // 2) + (part + 1) * width)
            for member in range(2)]


def source_values(index, total, seed):
    fact = base.fixture_fact(index, total, seed)
    own = fact["self_account"]
    counter = fact["payee_account"] if fact["outgoing"] else fact["payer_account"]
    common = [fact["trade_no"], fact["trade_time"], own, "合成主体", counter, "合成对手"]
    if index >= total // 2:
        return [*common, "支出" if fact["outgoing"] else "收入", fact["amount"], fact["balance"] or "",
                fact["currency"], "交易成功", "合成平台支付", f"合成平台用途{index % 7}", "SyntheticPay"]
    return [*common, "合成银行", "支出" if fact["outgoing"] else "收入", fact["amount"],
            fact["balance"] or "", fact["currency"], f"合成银行用途{index % 7}", "合成银行渠道",
            *[f"合成扩展{column}-{'x' * 24}" for column in range(8)]]


def expected_funds(count, totals):
    return {"policy": "evidence-bounded-funds-v1", "status": "bounded", "observation_count": count,
        "confirmed_event_count": 0, "unresolved_observation_count": count, "count": {"lower": 0, "upper": count},
        "totals": [{"currency": currency, "amount": None, "lower": "0.00", "upper": base.runner.money_text(value)}
                   for currency, value in sorted(totals.items())]}


def prepare_inputs(directory, total, packages, seed):
    require(total >= 2 * packages and total % (2 * packages) == 0, "Every package needs equal bank/platform slices")
    directory.mkdir()
    digest = hashlib.sha256()
    for index in range(total):
        digest.update(json.dumps(base.fixture_fact(index, total, seed), sort_keys=True).encode() + b"\n")
    packets, totals = [], defaultdict(int)
    for part in range(packages):
        folder = directory / f"part-{part + 1:03d}"; folder.mkdir()
        ranges = ranges_for(total, packages, part)
        members = []
        for member_index, (start, end) in enumerate(ranges):
            member = base.MEMBERS[member_index]; path = folder / Path(member).name
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(base.PLATFORM_HEADERS if member_index else base.BANK_HEADERS)
                for index in range(start, end):
                    writer.writerow(source_values(index, total, seed))
                    totals["USD" if index % 7 == 0 else "CNY"] += base.runner.cents(index, seed)
            members.append({"member_path": member, "rows": end - start, "global_range": [start, end],
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
        archive = folder / f"part-{part + 1:03d}.zip"
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
            for member in members:
                info = zipfile.ZipInfo(member["member_path"], date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED; info.external_attr = 0o100644 << 16
                target.writestr(info, (folder / Path(member["member_path"]).name).read_bytes(), compresslevel=6)
        loaded_half = ranges[0][1]
        newest = list(range(total // 2 + loaded_half - 1, total // 2 - 1, -1))[:50]
        if len(newest) < 50:
            newest.extend(range(loaded_half - 1, max(-1, loaded_half - (50 - len(newest)) - 1), -1))
        packets.append({"part": part + 1, "archive": str(archive.relative_to(directory)), "rows": total // packages,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "bytes": archive.stat().st_size,
            "members": members, "expected_prefix": {"rows": 2 * loaded_half,
                "funds": expected_funds(2 * loaded_half, totals),
                "first_page": [base.fixture_fact(index, total, seed) for index in newest]}})
    require(len({packet["sha256"] for packet in packets}) == packages, "Packages are not distinct")
    return {"rows": total, "packages": packages, "seed": seed, "global_fact_sha256": digest.hexdigest(),
        "expected": base.expected_summary(total, seed), "packets": packets}


def verify_page(page, expected):
    require(page["total"] == expected["rows"] and page["funds"] == expected["funds"], "Cumulative page funds/count differ")
    require(len(page["rows"]) == len(expected["first_page"]), "Cumulative page size differs")
    for row, fact in zip(page["rows"], expected["first_page"]):
        for field in ("trade_no", "trade_time", "payer_account", "payee_account", "self_account", "amount", "balance", "currency"):
            require(row[field] == fact[field], f"Cumulative page {field} differs")
        require(row["granularity"] == "unresolved_observation" and row["event_state"] == "unknown"
                and row["event_id"] is None, "Cumulative page invented event proof")


class DiskSampler:
    """Owned runtime logical bytes, including sampled open unlinked temp files.

    Filesystem and lsof snapshots are not atomic, nor APFS allocated/free space.
    Full visible+unlinked observations occur once a second; visible sizes are
    sampled more frequently. No file contents or unrelated process FDs are read.
    """
    def __init__(self, runtime, process, rss, path, interval, max_rss, max_disk):
        self.runtime, self.process, self.rss, self.path = runtime, process, rss, path
        self.interval, self.max_rss, self.max_disk = interval, max_rss, max_disk
        self.samples, self.errors, self.guard = [], [], None
        self.incomplete_scans = []
        self.stop_event = threading.Event(); self.thread = threading.Thread(target=self.run, daemon=True)

    def unlinked(self):
        pids = self.rss.samples[-1]["processes"] if self.rss.samples else {self.process.pid: 0}
        result = subprocess.run(["lsof", "-nP", "-a", "-p", ",".join(map(str, pids)), "+L1", "-F", "pftDiksn"],
                                capture_output=True, text=True, timeout=5)
        require(result.returncode in (0, 1), "Cannot sample owned open unlinked files")
        files, entry = {}, {}
        def save():
            if entry.get("t") == "REG" and entry.get("n", "").startswith(str(self.runtime) + os.sep):
                files[(entry.get("D"), entry.get("i"))] = {"bytes": int(entry.get("s", "0")),
                    "path": entry["n"][len(str(self.runtime)) + 1:]}
        for line in result.stdout.splitlines():
            if line.startswith(("p", "f")): save(); entry = {}
            if line: entry[line[0]] = line[1:]
        save()
        return {"bytes": sum(value["bytes"] for value in files.values()), "files": list(files.values())}

    def run(self):
        last_full = -float("inf")
        with self.path.open("w") as stream:
            while not self.stop_event.is_set() and self.process.poll() is None:
                try:
                    start = time.perf_counter(); counts = defaultdict(int)
                    disappeared, examples = 0, []
                    def scan_error(error):
                        nonlocal disappeared
                        missing = Path(error.filename) if error.filename else None
                        if (not isinstance(error, FileNotFoundError) or missing is None
                                or missing.parent != self.runtime / "data"
                                or not missing.name.startswith("funds-trace-binding-")):
                            raise error
                        disappeared += 1
                        if len(examples) < 4:
                            examples.append(str(missing.relative_to(self.runtime)))
                    for directory, _, filenames in os.walk(self.runtime, onerror=scan_error):
                        for name in filenames:
                            path = Path(directory) / name
                            try:
                                if not path.is_file(): continue
                                size = path.stat().st_size
                            except FileNotFoundError: continue
                            category = ("originals" if path.parent == self.runtime / "data/uploads" else
                                        "database" if path.name in {"fund.duckdb", "fund.duckdb.wal"} else "other_visible")
                            counts[category] += size
                    self.runtime.stat()  # Losing the owned root is never a discarded private scan.
                    sample = {"monotonic": start, "visible_bytes": sum(counts.values()), "categories": dict(counts),
                              "visible_scan_complete": not disappeared}
                    if disappeared:
                        sample.update(disappeared_private_directories=disappeared, disappeared_path_examples=examples)
                    if not disappeared and start - last_full >= 1:
                        unlinked = self.unlinked(); last_full = start
                        sample["unlinked_bytes"] = unlinked["bytes"]; sample["unlinked_files"] = unlinked["files"]
                        sample["combined_bytes"] = sample["visible_bytes"] + sample["unlinked_bytes"]
                    sample["sample_wall_seconds"] = time.perf_counter() - start
                    # Partial traversal can enforce a known-byte/RSS guard, but
                    # cannot qualify a peak or a full visible+unlinked sample.
                    (self.incomplete_scans if disappeared else self.samples).append(sample)
                    stream.write(json.dumps(sample) + "\n"); stream.flush()
                    rss = self.rss.samples[-1]["rss_bytes"] if self.rss.samples else 0
                    if rss > self.max_rss or sample.get("combined_bytes", sample["visible_bytes"]) > self.max_disk:
                        self.guard = {"rss_bytes": rss, "disk": sample, "reason": "owned resource guard exceeded"}
                        self.process.terminate(); return
                except Exception as error:
                    if self.process.poll() is None: self.errors.append(f"{type(error).__name__}: {error}")
                self.stop_event.wait(self.interval)

    def start(self): self.thread.start()

    def stop(self):
        self.stop_event.set(); self.thread.join(timeout=10)
        require(not self.thread.is_alive(), "Disk sampler did not stop")

    def summary(self, start, end, *, allow_missing_full=False):
        samples = [row for row in self.samples if start <= row["monotonic"] <= end]
        full = [row for row in samples if "combined_bytes" in row]
        require(samples and not self.errors and not self.guard, f"Invalid disk/resource evidence: {self.errors or self.guard}")
        require(full or allow_missing_full, "No complete disk observation in the measured interval")
        return {"visible_peak_bytes": max(row["visible_bytes"] for row in samples),
            "combined_sampled_peak_bytes": max((row["combined_bytes"] for row in full), default=None),
            "unlinked_sampled_peak_bytes": max((row["unlinked_bytes"] for row in full), default=None),
            "samples": len(samples), "full_samples": len(full),
            "discarded_visible_scans": sum(start <= row["monotonic"] <= end for row in self.incomplete_scans),
            "disappeared_private_directories": sum(row["disappeared_private_directories"] for row in self.incomplete_scans
                if start <= row["monotonic"] <= end),
            "max_sample_gap_seconds": max((right["monotonic"] - left["monotonic"]
                for left, right in zip(samples, samples[1:])), default=0),
            "max_sample_wall_seconds": max(row["sample_wall_seconds"] for row in samples)}


def execute_item(client, address, case_id, inputs, packet, origin, timeout):
    import httpx
    stages, replies = {}, {}
    item_start = time.perf_counter()
    def request(name, method, path, **kwargs):
        start = time.perf_counter(); response = client.request(method, path, **kwargs); value = response.json(); end = time.perf_counter()
        stages[name] = {"start_seconds": start - origin, "end_seconds": end - origin,
            "wall_seconds": end - start, "http_status": response.status_code}
        replies[name] = value
        require(response.is_success, f"{name} HTTP {response.status_code}: {value}")
        return value
    archive = inputs / packet["archive"]
    with archive.open("rb") as stream:
        view = request("upload_preview", "POST", "/api/import/preview", params={"case_id": case_id},
            files={"file": (archive.name, stream, "application/zip")})
    require(view.get("automatic") is True, "Synthetic automatic input unexpectedly needs mapping")
    source_id = view["import_id"]
    started = request("confirm", "POST", "/api/import/commit", json={"case_id": int(case_id), "import_id": source_id,
        "mode": view["mode"], "mapping": view["mapping"], "mark_accounts": ""})
    require(started["import_id"] == source_id, "Commit changed import identity")
    poll_start = time.perf_counter(); polls = []
    while True:
        before = time.perf_counter()
        response = client.get("/api/import/progress", params={"case_id": case_id, "import_id": source_id}); job = response.json()
        polls.append({"start_seconds": before - origin, "wall_seconds": time.perf_counter() - before,
            "http_status": response.status_code, "stage": job.get("stage")})
        require(response.is_success, f"Progress HTTP failed: {job}")
        if job["stage"] in {"done", "error"}: break
        require(time.perf_counter() - origin < timeout, "Queue completion deadline exceeded")
        time.sleep(.05)
    end = time.perf_counter()
    stages["completion_wait"] = {"start_seconds": poll_start - origin, "end_seconds": end - origin,
        "wall_seconds": end - poll_start, "polls": polls}
    replies["completion"] = job
    require(job["stage"] == "done", f"Import failed; later packages were not started: {job}")
    require(job["result"]["ok"] == packet["rows"] and job["result"].get("rows_dup", 0) == 0, "Package receipt differs")
    receipt_start = time.perf_counter()
    with httpx.Client(base_url=address, trust_env=False, timeout=min(timeout, 180)) as independent:
        response = independent.get(f"/api/v1/cases/{case_id}/imports", params={"import_id": source_id}); receipt = response.json()
    receipt_end = time.perf_counter()
    stages["persisted_receipt_get"] = {"start_seconds": receipt_start - origin, "end_seconds": receipt_end - origin,
        "wall_seconds": receipt_end - receipt_start, "http_status": response.status_code}
    replies["persisted_receipt_get"] = receipt
    require(response.is_success and len(receipt["items"]) == 1 and receipt["items"][0]["stage"] == "done", "Durable receipt unavailable")
    require(base.comparable_receipt(receipt["items"][0]["result"]) == base.comparable_receipt(job["result"]), "Durable receipt differs")
    request("first_flow_query", "GET", "/api/flows", params={"case_id": case_id, "page": 1, "size": 50})
    item_end = time.perf_counter()
    return {"part": packet["part"], "rows": packet["rows"], "import_id": source_id, "start_seconds": item_start - origin,
        "end_seconds": item_end - origin, "wall_seconds": item_end - item_start, "stages": stages}, replies


def verify_persisted(run_dir, manifest, case_id, items, replies):
    import duckdb
    start = time.perf_counter(); total, seed = manifest["rows"], manifest["seed"]
    columns = ["trade_no", "trade_time", "payer_account", "payee_account", "amount", "balance", "currency", "self_account"]
    expected = manifest["expected"]; seen = set(); totals = defaultdict(int); nulls = 0
    directions = defaultdict(lambda: {"rows": 0, "cents": 0})
    with duckdb.connect(str(run_dir / "runtime/data/fund.duckdb"), read_only=True, config={"threads": 2}) as con:
        cursor = con.execute("select " + ",".join(columns) + " from flows where case_id=? order by trade_no", [case_id])
        while values := cursor.fetchmany(5000):
            for values_row in values:
                row = dict(zip(columns, values_row)); index = int(row["trade_no"].rsplit("-", 1)[1])
                require(0 <= index < total and index not in seen, "Global source index missing or repeated"); seen.add(index)
                fact = base.fixture_fact(index, total, seed)
                for field in columns:
                    target = (dt.datetime.fromisoformat(fact[field]) if field == "trade_time" else
                              Decimal(fact[field]) if field in {"amount", "balance"} and fact[field] is not None else fact[field])
                    require(row[field] == target, f"{row['trade_no']} {field} differs")
                cents = int(row["amount"] * 100); totals[row["currency"]] += cents; nulls += row["balance"] is None
                direction = f"{row['currency']}:{'out' if row['payer_account'] == row['self_account'] else 'in'}"
                directions[direction]["rows"] += 1; directions[direction]["cents"] += cents
        require(len(seen) == total and con.execute("select count(*) from flows").fetchone()[0] == total, "Flows escaped synthetic case or coverage differs")
        require({key: base.runner.money_text(value) for key, value in sorted(totals.items())} == expected["currency_totals"], "Currency totals differ")
        require({key: {"rows": value["rows"], "cents": str(value["cents"])} for key, value in sorted(directions.items())} == expected["direction_cents"], "Directions differ")
        require(nulls == expected["null_balances"], "Null balances differ")
        for packet, item, response in zip(manifest["packets"], items, replies):
            source_id = item["import_id"]
            receipt = con.execute("select stage,result_json from import_sessions where case_id=? and import_id=?", [case_id, source_id]).fetchone()
            require(receipt and receipt[0] == "done" and base.comparable_receipt(json.loads(receipt[1])) ==
                base.comparable_receipt(response["persisted_receipt_get"]["items"][0]["result"]), "Persisted receipt changed after shutdown")
            links = con.execute("""select f.trade_no,r.member_path,r.source_row,r.disposition,s.disposition,s.source_row,r.record_no
                from flows f join flow_sources s on s.case_id=f.case_id and s.flow_id=f.id
                join import_records r on r.case_id=s.case_id and r.source_id=s.source_id and r.record_no=s.source_row
                where f.case_id=? and s.source_id=?""", [case_id, source_id]).fetchall()
            require(len(links) == packet["rows"], "Source-link coverage differs")
            for trade, member, source_row, disposition, support, link_row, record_no in links:
                index = int(trade.rsplit("-", 1)[1]); mi = int(index >= total // 2); info = packet["members"][mi]
                left, right = info["global_range"]
                require(left <= index < right and (member, source_row, disposition, support, link_row) ==
                    (info["member_path"], index - left + 2, "imported", "imported", record_no), "Per-package local source row differs")
            counts = dict(con.execute("select disposition,count(*) from import_records where case_id=? and source_id=? group by 1", [case_id, source_id]).fetchall())
            require(counts == {"imported": packet["rows"]}, "Source record dispositions differ")
            source = con.execute("select sha256,size_bytes,parser_version from import_sources where case_id=? and source_id=?", [case_id, source_id]).fetchone()
            require(source and source[:2] == (packet["sha256"], packet["bytes"]), "Source archive digest differs")
            members = dict(con.execute("select member_path,sha256 from import_members where case_id=? and source_id=?", [case_id, source_id]).fetchall())
            require(members == {member["member_path"]: member["sha256"] for member in packet["members"]}, "Source member digest differs")
            require(hashlib.sha256((run_dir / f"runtime/data/uploads/{source_id}.zip").read_bytes()).hexdigest() == packet["sha256"], "Retained original differs")
    return {"all_passed": True, "every_flow_and_local_source_row_checked": total, "independent_durable_receipts": len(items),
        "expected": expected, "global_fact_sha256": manifest["global_fact_sha256"], "wall_seconds_outside_timing": time.perf_counter() - start}


def run(args):
    import httpx
    out = args.output.resolve(); require(not out.exists(), "Output exists; preserve prior evidence"); out.mkdir(parents=True)
    runtime = out / "runtime"; runtime.mkdir(); (runtime / "tmp").mkdir(); before = fingerprint()
    record = {"schema_version": SCHEMA, "status": "running", "mode": "correctness-smoke" if args.rows < 10000 else "single-observation",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "source_fingerprint": before,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "environment": base.runner.environment(), "parameters": vars(args) | {"output": str(out)},
        "policy": {"entry": "serial legacy preview/commit/progress; independent durable receipt; first legacy flow page per package",
            "wall": "continuous from first multipart request through final decoded flow query; preparation/startup/full audit/shutdown excluded",
            "probes": False, "rss": "sampled process-tree sum; shared RSS may be counted twice; no percentile or UI claim",
            "disk": "sampled logical owned-runtime visible plus open unlinked bytes; not atomic, allocated blocks or APFS free space"},
        "scratch_environment": {name: str(runtime / "tmp") for name in ("TMPDIR", "TMP", "TEMP")}}
    if args.expected_head: require(record["head"] == args.expected_head, "HEAD differs")
    record["environment"]["load_average_before"] = list(os.getloadavg()); write_json(out / "result.json", record)
    preparation = time.perf_counter(); manifest = prepare_inputs(out / "inputs", args.rows, args.packages, args.seed)
    write_json(out / "input-manifest.json", manifest); record["preparation_seconds"] = time.perf_counter() - preparation
    process = rss = disk = None; items, replies = [], []; origin = end = None
    try:
        with socket.socket() as listener, (out / "server.log").open("w") as log:
            listener.bind(("127.0.0.1", 0)); listener.listen(128); address = f"http://127.0.0.1:{listener.getsockname()[1]}"
            startup = time.perf_counter()
            environment = base.isolated_environment(runtime); environment.update(record["scratch_environment"])
            process = subprocess.Popen([sys.executable, "-B", "-m", "benchmarks.import_upload", "--serve-fd", str(listener.fileno()),
                "--runtime", str(runtime)], cwd=ROOT, env=environment, stdout=log, stderr=log,
                pass_fds=(listener.fileno(),), start_new_session=True)
            listener.close(); record["service_pid"] = process.pid
            rss = base.TreeSampler(process.pid, out / "rss.jsonl", args.rss_interval); rss.start()
            disk = DiskSampler(runtime, process, rss, out / "disk.jsonl", .2, args.max_rss_mib * 1024**2, args.max_disk_mib * 1024**2); disk.start()
            with httpx.Client(base_url=address, trust_env=False, timeout=min(args.timeout, 180)) as client:
                while True:
                    require(process.poll() is None, "Owned service exited during startup")
                    try:
                        if client.get("/api/v1/cases", timeout=.5).status_code == 200: break
                    except httpx.TransportError: pass
                    require(time.perf_counter() - startup < 60, "Service startup timed out"); time.sleep(.05)
                record["startup_seconds"] = time.perf_counter() - startup
                created = client.post("/api/v1/cases", json={"name": "Synthetic serial queue benchmark"})
                require(created.status_code == 201, "Synthetic case creation failed"); case_id = created.json()["id"]; record["case_id"] = case_id
                origin = time.perf_counter()
                for packet in manifest["packets"]:
                    item, response = execute_item(client, address, case_id, out / "inputs", packet, origin, args.timeout)
                    items.append(item); replies.append(response)
                    write_json(out / "queue-progress.json", {"completed": len(items), "items": items})
                end = time.perf_counter()
                record["timing"] = {"continuous_wall_seconds": end - origin, "first_packet_available_seconds": items[0]["end_seconds"],
                    "last_packet_available_seconds": items[-1]["end_seconds"], "items": items}
                for packet, response in zip(manifest["packets"], replies): verify_page(response["first_flow_query"], packet["expected_prefix"])
                record["rss_chain"] = rss.summary(origin, end)
                for item in items:
                    lo, hi = origin + item["start_seconds"], origin + item["end_seconds"]
                    item["rss"] = rss.summary(lo, hi)
                    nearby = [sample for sample in rss.samples if lo <= sample["monotonic"] <= hi]
                    item["rss_at_boundary"] = {"bytes": nearby[-1]["rss_bytes"], "sample_age_seconds": hi - nearby[-1]["monotonic"]}
                # Extra idle observations are explicitly outside the wall interval.
                idle = time.perf_counter(); time.sleep(.35)
                record["rss_after_queue_idle"] = rss.summary(idle, time.perf_counter())
            process.terminate(); process.wait(timeout=30); rss.stop(); disk.stop()
            require(process.returncode in (0, -15), f"Service exit {process.returncode}")
            record["disk_chain"] = disk.summary(origin, end, allow_missing_full=args.rows < 10000)
            record["disk_startup_through_shutdown"] = disk.summary(startup, time.perf_counter())
        record["checks"] = verify_persisted(out, manifest, case_id, items, replies)
        require(fingerprint() == before, "Candidate changed during the run"); record["source_fingerprint_after"] = fingerprint()
        record["status"] = "passed"; record["rows_per_second"] = args.rows / record["timing"]["continuous_wall_seconds"]
    except (Exception, KeyboardInterrupt) as error:
        record["status"] = "failed"; record["rows_per_second"] = None
        record["error"] = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
        if origin and end: record["observed_wall_seconds_not_a_performance_score"] = end - origin
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=30)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
        for sampler in (rss, disk):
            if sampler is not None and sampler.thread.is_alive(): sampler.stop()
        record["service_stopped"] = process is None or process.poll() is not None
        record["resource_guard"] = disk.guard if disk else None
        record["environment"]["load_average_after"] = list(os.getloadavg())
        write_json(out / "http-responses.json", replies); write_json(out / "result.json", record)
    print(json.dumps({"status": record["status"], "output": str(out / "result.json"), "error": record.get("error", {}).get("message")}, ensure_ascii=False))
    return int(record["status"] != "passed")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=100000); parser.add_argument("--packages", type=int, default=2)
    parser.add_argument("--seed", type=int, default=base.SEED); parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-head"); parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--rss-interval", type=float, default=.05)
    parser.add_argument("--max-rss-mib", type=int, default=8192); parser.add_argument("--max-disk-mib", type=int, default=12288)
    args = parser.parse_args(argv)
    if sys.platform == "win32" or not shutil.which("ps") or not shutil.which("lsof"):
        parser.error("HTTP RSS/disk timing requires POSIX ps/lsof; this is not Windows evidence")
    if not 2 <= args.packages <= 20 or not 2 * args.packages <= args.rows <= 100000 or args.rows % (2 * args.packages):
        parser.error("--rows must be <=100000, divisible by 2*packages; --packages must be 2..20")
    if not 0 <= args.seed <= 2147483647 or not 10 <= args.timeout <= 600 or not .01 <= args.rss_interval <= 1:
        parser.error("invalid seed/timeout/RSS interval")
    if args.max_rss_mib < 256 or args.max_disk_mib < 64: parser.error("Resource guards are too small")
    return args


def main(argv=None): return run(parse_args(argv))


if __name__ == "__main__": raise SystemExit(main())
