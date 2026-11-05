"""Public runner isolation and benchmark evidence-contract regressions."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from benchmarks import runner
from funds_assertions import unresolved_funds


ROOT = Path(__file__).resolve().parents[1]


def test_check_runner_replaces_inherited_state_and_reports_only_aggregates(tmp_path):
    inherited = tmp_path / "existing-business-state"
    inherited.mkdir()
    existing_db = inherited / "fund.duckdb"
    existing_db.write_bytes(b"DO NOT OPEN: synthetic isolation sentinel")
    license_file = inherited / "license.dat"
    license_file.write_text("synthetic-existing-license-sentinel")
    inherited_mpl = tmp_path / "existing-matplotlib"
    output = tmp_path / "smoke.json"
    env = dict(os.environ)
    env.update({
        "FUND_DATA_DIR": str(inherited), "MPLCONFIGDIR": str(inherited_mpl),
        "FUNDS_TRACE_LICENSE_SECRET": "synthetic-inherited-value-must-be-replaced",
    })
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/benchmark.py"), "--check", "--rows", "100",
         "--repeats", "1", "--output", str(output)],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert existing_db.read_bytes() == b"DO NOT OPEN: synthetic isolation sentinel"
    assert license_file.read_text() == "synthetic-existing-license-sentinel"
    assert not inherited_mpl.exists()
    assert sorted(path.name for path in inherited.iterdir()) == ["fund.duckdb", "license.dat"]
    report = json.loads(output.read_text())
    assert report["schema_version"] == runner.SCHEMA_VERSION
    assert report["mode"] == "correctness-smoke"
    assert report["checks"]["all_passed"] is True
    sample = report["samples"][0]
    aggregate = sample["checks"]["aggregates"]
    assert aggregate["source_rows"] == 100
    assert aggregate["accepted_rows"] == 98
    assert aggregate["duplicate_rows"] == 10
    assert aggregate["stored_rows"] == 88
    assert aggregate["outgoing_rows"] == 49
    assert aggregate["incoming_rows"] == 39
    assert isinstance(aggregate["amount_cents"], str)
    assert int(aggregate["amount_cents"]) > runner.LARGE_AMOUNT_CENTS
    assert sample["checks"]["exact_decimal_and_large_sentinel"] is True
    assert sample["checks"]["failed_import_rollback"] is True
    assert sample["graph_sample"]["rows"] == 87
    indices = list(runner.unique_valid_indices(100))
    graph_upper = runner.money_text(sum(runner.cents(index, runner.DEFAULT_SEED) for index in indices if index != 2))
    assert sample["graph_sample"]["funds"] == unresolved_funds(87, ("CNY", graph_upper))
    assert sample["checks"]["graph_bounded_aggregate"] is True
    assert sample["checks"]["excel_bounded_round_trip"] is True
    assert sample["excel_sample_rows"] == 88
    assert sample["file_sizes"]["duckdb_bytes"] > 0
    assert report["process_summary"]["peak_rss_bytes_max"] > 0
    assert report["summary"]["commit_atomic"]["wall_seconds"]["samples"] == 1
    assert "NOT stage-exclusive" in report["policy"]["rss"]
    assert len(report["source_fingerprint"]["sha256"]) == 64
    serialized = output.read_text()
    assert str(inherited) not in serialized
    assert "synthetic-inherited-value" not in serialized
    assert runner.SELF_ACCOUNT not in serialized
    assert '"trade_no"' not in serialized


@pytest.mark.parametrize("arguments", [
    ["--rows", "100"], ["--rows", "1000001"], ["--repeats", "0"],
    ["--check", "--rows", "10000"], ["--excel-rows", "10001"], ["--threads", "9"],
])
def test_runner_rejects_out_of_bounds_or_mislabelled_samples(arguments):
    with pytest.raises(SystemExit) as error:
        runner.parse_args(arguments)
    assert error.value.code == 2


def test_percentiles_are_explicit_nearest_rank_observations():
    assert runner.summarize([3.0, 1.0, 2.0]) == {
        "samples": 3, "median": 2.0, "p95_nearest_rank": 3.0, "min": 1.0, "max": 3.0,
    }
    assert runner.nearest_rank(list(range(1, 101)), 0.95) == 95


def test_aggregation_rejects_failed_integrity_or_changed_candidate(monkeypatch):
    args = runner.parse_args(["--check", "--repeats", "1"])
    fingerprint = {"sha256": "candidate-a", "files": {}}
    monkeypatch.setattr(runner, "source_fingerprint", lambda: fingerprint)
    sample = {
        "schema_version": runner.SCHEMA_VERSION, "rows": 100, "seed": args.seed,
        "source_fingerprint": fingerprint, "checks": {"all_passed": False},
    }
    with pytest.raises(runner.CorrectnessError, match="correctness failed"):
        runner.aggregate_samples(args, [sample], fingerprint)
    changed = deepcopy(sample)
    changed["checks"]["all_passed"] = True
    changed["source_fingerprint"]["sha256"] = "candidate-b"
    with pytest.raises(runner.CorrectnessError, match="Source changed"):
        runner.aggregate_samples(args, [changed], fingerprint)
