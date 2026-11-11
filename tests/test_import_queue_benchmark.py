"""Independent partition facts and public serial HTTP benchmark smoke."""
import csv
import errno
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from benchmarks import import_queue as queue


def test_shapes_partition_the_same_global_facts_with_local_source_rows(tmp_path):
    shapes = [queue.prepare_inputs(tmp_path / str(parts), 40, parts, queue.base.SEED) for parts in (2, 10)]
    assert shapes[0]["global_fact_sha256"] == shapes[1]["global_fact_sha256"]
    assert shapes[0]["expected"] == shapes[1]["expected"] == queue.base.expected_summary(40, queue.base.SEED)
    for shape in shapes:
        seen = set(); folder = tmp_path / str(shape["packages"])
        assert len({part["sha256"] for part in shape["packets"]}) == shape["packages"]
        for part in shape["packets"]:
            with zipfile.ZipFile(folder / part["archive"]) as archive:
                for member in part["members"]:
                    rows = list(csv.reader(io.StringIO(archive.read(member["member_path"]).decode("utf-8-sig"))))
                    left, right = member["global_range"]
                    assert len(rows) == right - left + 1
                    for position, row in enumerate(rows[1:], start=2):
                        index = int(row[0].rsplit("-", 1)[1])
                        assert index not in seen and index == left + position - 2
                        seen.add(index)
            assert part["expected_prefix"]["rows"] == len(seen)
        assert seen == set(range(40))
        assert shape["packets"][-1]["expected_prefix"]["funds"]["totals"] == [
            {"currency": currency, "amount": None, "lower": "0.00", "upper": value}
            for currency, value in shape["expected"]["currency_totals"].items()]


POSIX_SAMPLER = pytest.mark.skipif(os.name != "posix" or not shutil.which("ps") or not shutil.which("lsof"),
    reason="Timing sampler requires POSIX ps/lsof; partition checks run on every platform")


def test_short_smoke_does_not_invent_a_disk_peak_between_full_samples():
    sampler = queue.DiskSampler.__new__(queue.DiskSampler)
    sampler.errors, sampler.guard = [], None
    sampler.incomplete_scans = []
    sampler.samples = [
        {"monotonic": .9, "visible_bytes": 10, "combined_bytes": 11, "unlinked_bytes": 1, "sample_wall_seconds": .01},
        {"monotonic": 1.4, "visible_bytes": 20, "sample_wall_seconds": .01},
        {"monotonic": 2., "visible_bytes": 30, "combined_bytes": 32, "unlinked_bytes": 2, "sample_wall_seconds": .01}]
    smoke = sampler.summary(1., 1.9, allow_missing_full=True)
    assert smoke["visible_peak_bytes"] == 20 and smoke["full_samples"] == 0
    assert smoke["combined_sampled_peak_bytes"] is None and smoke["unlinked_sampled_peak_bytes"] is None
    with pytest.raises(queue.base.runner.CorrectnessError, match="No complete disk observation"):
        sampler.summary(1., 1.9)
    assert sampler.summary(.8, 2.1)["combined_sampled_peak_bytes"] == 32


@POSIX_SAMPLER
def test_serial_http_queue_checks_all_facts_receipts_and_retains_owned_state(tmp_path):
    inherited = tmp_path / "unrelated"; inherited.mkdir()
    sentinel = inherited / "fund.duckdb"; sentinel.write_bytes(b"Synthetic unrelated database sentinel")
    output = tmp_path / "run"
    inherited_tmp = tmp_path / "unrelated-tmp"; inherited_tmp.mkdir()
    tmp_sentinel = inherited_tmp / "sentinel"; tmp_sentinel.write_bytes(b"Synthetic unrelated scratch sentinel")
    env = dict(os.environ, FUND_DATA_DIR=str(inherited), FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE=str(inherited / "unrelated-key"),
               TMPDIR=str(inherited_tmp), TMP=str(inherited_tmp), TEMP=str(inherited_tmp))
    completed = subprocess.run([sys.executable, "-B", "-m", "benchmarks.import_queue", "--rows", "40", "--packages", "4",
        "--output", str(output)], cwd=queue.ROOT, env=env, capture_output=True, text=True, timeout=90)
    result = json.loads((output / "result.json").read_text())
    assert completed.returncode == 0, completed.stdout + completed.stderr + json.dumps(result)
    assert result["status"] == "passed" and result["mode"] == "correctness-smoke"
    assert result["checks"]["every_flow_and_local_source_row_checked"] == 40
    assert result["checks"]["independent_durable_receipts"] == 4
    items = result["timing"]["items"]
    assert len({item["import_id"] for item in items}) == 4
    assert all(next_["start_seconds"] >= previous["end_seconds"] for previous, next_ in zip(items, items[1:]))
    assert result["timing"]["first_packet_available_seconds"] == items[0]["end_seconds"]
    assert result["timing"]["last_packet_available_seconds"] == items[-1]["end_seconds"]
    assert result["rss_chain"]["peak_bytes"] > 0 and result["disk_chain"]["visible_peak_bytes"] > 0
    assert result["service_stopped"] and result["resource_guard"] is None
    assert result["source_fingerprint"] == result["source_fingerprint_after"]
    assert result["policy"]["probes"] is False
    assert result["scratch_environment"] == {name: str(output / "runtime/tmp") for name in ("TMPDIR", "TMP", "TEMP")}
    assert sentinel.read_bytes() == b"Synthetic unrelated database sentinel"
    assert list(inherited.iterdir()) == [sentinel]
    assert list(inherited_tmp.iterdir()) == [tmp_sentinel]
    assert tmp_sentinel.read_bytes() == b"Synthetic unrelated scratch sentinel"


@pytest.mark.parametrize("values", [["--rows", "30", "--packages", "4"], ["--packages", "1"], ["--rss-interval", "0"]])
def test_bad_measurement_parameters_do_not_create_runtime(tmp_path, values):
    output = tmp_path / "not-created"
    with pytest.raises(SystemExit): queue.parse_args(["--output", str(output), *values])
    assert not output.exists()


def controlled_sampler(tmp_path, monkeypatch, *, steps=1):
    process = SimpleNamespace(pid=os.getpid(), poll=lambda: None,
                              terminate=lambda: pytest.fail("unexpected resource guard"))
    rss = SimpleNamespace(samples=[{"rss_bytes": 1, "processes": {os.getpid(): 1}}])
    sampler = queue.DiskSampler(tmp_path / "runtime", process, rss, tmp_path / "disk.jsonl", .2, 4096, 4096)
    monkeypatch.setattr(sampler, "unlinked", lambda: {"bytes": 0, "files": []})
    ticks = 0
    def next_scan(_):
        nonlocal ticks
        ticks += 1
        if ticks >= steps:
            sampler.stop_event.set()
    monkeypatch.setattr(sampler.stop_event, "wait", next_scan)
    return sampler


def test_disappearing_private_directory_discards_partial_scan_then_uses_complete_sample(tmp_path, monkeypatch):
    sampler = controlled_sampler(tmp_path, monkeypatch, steps=2)
    private = sampler.runtime / "data/funds-trace-binding-synthetic"
    private.mkdir(parents=True)
    (private / "lookup.sqlite").write_bytes(b"synthetic private bytes")
    (sampler.runtime / "data/fund.duckdb").write_bytes(b"synthetic retained database")
    scandir = os.scandir
    def disappears(path):
        if Path(path) == private and private.exists():
            (private / "lookup.sqlite").unlink()
            private.rmdir()
        return scandir(path)
    monkeypatch.setattr(os, "scandir", disappears)
    sampler.run()
    assert not sampler.errors and sampler.guard is None
    assert len(sampler.samples) == len(sampler.incomplete_scans) == 1
    partial, complete = [json.loads(line) for line in sampler.path.read_text().splitlines()]
    assert partial["visible_scan_complete"] is False and "combined_bytes" not in partial
    assert partial["disappeared_private_directories"] == 1
    assert partial["disappeared_path_examples"] == ["data/funds-trace-binding-synthetic"]
    assert complete["visible_scan_complete"] is True and complete["combined_bytes"] == len(b"synthetic retained database")
    summary = sampler.summary(0, float("inf"))
    assert summary["samples"] == summary["full_samples"] == 1
    assert summary["discarded_visible_scans"] == summary["disappeared_private_directories"] == 1


@pytest.mark.parametrize("fault", ["root_missing", "permission", "io"])
def test_sampler_root_loss_permission_and_io_errors_still_fail(tmp_path, monkeypatch, fault):
    sampler = controlled_sampler(tmp_path, monkeypatch)
    if fault != "root_missing":
        private = sampler.runtime / "data/funds-trace-binding-synthetic"
        private.mkdir(parents=True)
        scandir = os.scandir
        def broken(path):
            if Path(path) == private:
                if fault == "permission":
                    raise PermissionError(errno.EACCES, "synthetic scan permission", os.fspath(path))
                raise OSError(errno.EIO, "synthetic scan I/O", os.fspath(path))
            return scandir(path)
        monkeypatch.setattr(os, "scandir", broken)
    sampler.run()
    assert len(sampler.errors) == 1
    assert {"root_missing": "FileNotFoundError", "permission": "PermissionError", "io": "OSError"}[fault] in sampler.errors[0]
    with pytest.raises(queue.base.runner.CorrectnessError, match="Invalid disk/resource evidence"):
        sampler.summary(0, float("inf"))
