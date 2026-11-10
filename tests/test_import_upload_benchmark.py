"""Public loopback benchmark regression; small synthetic state only."""
import json
import os
import shutil
import socket
import subprocess
import sys

import pytest

from benchmarks import import_upload


POSIX_SAMPLER = pytest.mark.skipif(os.name != "posix" or shutil.which("ps") is None,
    reason="HTTP RSS benchmark requires POSIX pass_fds and ps; funds smoke runs on every platform")


@POSIX_SAMPLER
def test_http_upload_benchmark_owns_service_and_verifies_persistent_facts(tmp_path):
    inherited = tmp_path / "unrelated-state"
    inherited.mkdir()
    sentinel = inherited / "fund.duckdb"
    sentinel.write_bytes(b"synthetic sentinel: never open this database")
    license_file = inherited / "license.dat"
    license_file.write_text("synthetic inherited license sentinel")
    inherited_mpl = tmp_path / "unrelated-matplotlib"
    env = dict(os.environ, FUND_DATA_DIR=str(inherited), MPLCONFIGDIR=str(inherited_mpl),
               FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE=str(inherited / "private-key-sentinel"))
    output = tmp_path / "run"
    completed = subprocess.run([sys.executable, "-B", "-m", "benchmarks.import_upload", "--rows", "10",
        "--output", str(output)], cwd=import_upload.ROOT, env=env, capture_output=True, text=True, timeout=90)
    assert completed.returncode == 0, completed.stdout + completed.stderr + (output / "result.json").read_text()
    result = json.loads((output / "result.json").read_text())
    assert result["status"] == "passed" and result["mode"] == "correctness-smoke"
    assert result["checks"]["all_passed"] and result["checks"]["every_flow_and_source_checked"] == 10
    assert result["checks"]["durable_receipt_after_service_exit"]
    assert result["timing"]["continuous_wall_seconds"] >= sum(
        stage["wall_seconds"] for stage in result["timing"]["stages"].values())
    assert result["rss_chain"]["peak_bytes"] > 0 and result["rss_chain"]["samples"] > 0
    assert result["service_stopped"] and result["source_fingerprint"] == result["source_fingerprint_after"]
    responses = json.loads((output / "http-responses.json").read_text())
    assert responses["upload_preview"]["automatic"]
    assert import_upload.comparable_receipt(responses["completion"]["result"]) == import_upload.comparable_receipt(
        responses["persisted_receipt_get"]["items"][0]["result"])
    assert responses["first_flow_query"]["total"] == 10
    assert sentinel.read_bytes() == b"synthetic sentinel: never open this database"
    assert license_file.read_text() == "synthetic inherited license sentinel"
    assert sorted(p.name for p in inherited.iterdir()) == ["fund.duckdb", "license.dat"]
    assert not inherited_mpl.exists()
    assert not (output / "probes.jsonl").exists()


def test_fixture_expected_money_is_independent_and_archive_is_deterministic(tmp_path):
    first = import_upload.prepare_inputs(tmp_path / "first", 10, import_upload.SEED)
    second = import_upload.prepare_inputs(tmp_path / "second", 10, import_upload.SEED)
    assert first == second
    expected = first["expected"]
    assert expected["rows"] == 10 and expected["null_balances"] == 4
    # Explicit integer arithmetic, independent of production money parsing.
    values = [((i * 7919 + 20261105) % 1000000 + 1) for i in range(10)]
    values[2] = 900719925474099113
    assert expected["currency_totals"] == {
        "CNY": import_upload.runner.money_text(sum(v for i, v in enumerate(values) if i not in (0, 7))),
        "USD": import_upload.runner.money_text(values[0] + values[7])}
    assert import_upload.fixture_fact(0, 10, import_upload.SEED)["payer_account"] == import_upload.runner.SELF_ACCOUNT
    assert import_upload.fixture_fact(5, 10, import_upload.SEED)["payee_account"] == "000SYN-PLATFORM"


def test_public_upload_funds_and_sources_smoke_on_every_platform(client, make_case, tmp_path):
    from test_folder_evidence import complete
    cid = make_case("Synthetic cross-platform import baseline guard")
    manifest = import_upload.prepare_inputs(tmp_path / "inputs", 10, import_upload.SEED)
    content = (tmp_path / "inputs" / manifest["archive"]).read_bytes()
    response = client.post("/api/import/preview", params={"case_id": cid},
                           files={"file": (manifest["archive"], content)})
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["automatic"]
    job = complete(client, cid, view, legacy=True)
    assert job["stage"] == "done" and job["result"]["ok"] == 10
    sessions = client.get(f"/api/v1/cases/{cid}/imports", params={"import_id": view["import_id"]}).json()["items"]
    assert len(sessions) == 1 and sessions[0]["stage"] == "done"
    assert import_upload.comparable_receipt(sessions[0]["result"]) == import_upload.comparable_receipt(job["result"])
    page = client.get("/api/flows", params={"case_id": cid, "page": 1, "size": 50}).json()
    import_upload.verify_first_page(page, 10, import_upload.SEED)
    for row in page["rows"]:
        fact = import_upload.fixture_fact(int(row["trade_no"].rsplit("-", 1)[1]), 10, import_upload.SEED)
        sources = client.get(f"/api/v1/cases/{cid}/flows/{row['id']}/sources").json()["items"]
        assert len(sources) == 1
        assert (sources[0]["member_path"], sources[0]["member_row"], sources[0]["sha256"]) == (
            fact["member_path"], fact["member_row"], manifest["sha256"])
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == content


@POSIX_SAMPLER
def test_semantic_failure_is_retained_and_never_reported_as_throughput(tmp_path, monkeypatch):
    def failed_verification(*args, **kwargs):
        raise import_upload.runner.CorrectnessError("Synthetic independent validation failure")
    monkeypatch.setattr(import_upload, "verify_persisted", failed_verification)
    output = tmp_path / "failed"
    args = import_upload.parse_args(["--rows", "2", "--output", str(output)])
    assert import_upload.run(args) == 1
    result = json.loads((output / "result.json").read_text())
    assert result["status"] == "failed" and result["rows_per_second"] is None
    assert result["service_stopped"] and result["observed_wall_seconds_not_a_performance_score"] > 0
    assert result["error"]["message"] == "Synthetic independent validation failure"
    assert (output / "http-responses.json").is_file() and (output / "runtime/data/fund.duckdb").is_file()


def test_windows_timing_cli_exits_before_creating_runtime(tmp_path, monkeypatch, capsys):
    output = tmp_path / "unsupported"
    monkeypatch.setattr(import_upload.sys, "platform", "win32")
    with pytest.raises(SystemExit) as error:
        import_upload.main(["--output", str(output)])
    assert error.value.code == 2 and "requires POSIX" in capsys.readouterr().err
    assert not output.exists()


def reject_hidden_server(tmp_path, fd, expected, **kwargs):
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    sentinel = unrelated / "fund.duckdb"
    sentinel.write_bytes(b"synthetic unrelated database: never open")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    program = '''
import builtins, sys
from benchmarks import import_upload
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == "app" or name.startswith("app."):
        raise AssertionError("application import before server validation")
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
expected = sys.argv.pop()
try:
    import_upload.main(sys.argv[1:])
except (OSError, ValueError, import_upload.runner.CorrectnessError) as error:
    assert expected in str(error), str(error)
else:
    raise AssertionError("invalid hidden server entry was accepted")
assert not any(name == "app" or name.startswith("app.") for name in sys.modules)
'''
    completed = subprocess.run([sys.executable, "-B", "-c", program, "--serve-fd", str(fd),
        "--runtime", str(runtime), expected], cwd=import_upload.ROOT,
        env=dict(os.environ, FUND_DATA_DIR=str(unrelated)), capture_output=True, text=True, timeout=30, **kwargs)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert sentinel.read_bytes() == b"synthetic unrelated database: never open"
    assert list(runtime.iterdir()) == []


def test_hidden_server_rejects_invalid_fd_before_any_application_import(tmp_path):
    reject_hidden_server(tmp_path, -1, "negative file descriptor")


@POSIX_SAMPLER
@pytest.mark.parametrize("listening", [False, True])
def test_hidden_server_checks_listener_and_exact_isolated_data_before_import(tmp_path, listening):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        if listening:
            listener.listen()
        reject_hidden_server(tmp_path, listener.fileno(),
            "Isolated data directory mismatch" if listening else "loopback listener",
            pass_fds=(listener.fileno(),))


@pytest.mark.parametrize("arguments", [["--rows", "3"], ["--rows", "100002"], ["--seed", "-1"],
    ["--timeout", "0"], ["--rss-interval", "0"], ["--rss-interval", "nan"]])
def test_cli_rejects_invalid_measurement_parameters(tmp_path, arguments):
    with pytest.raises(SystemExit) as error:
        import_upload.parse_args(["--output", str(tmp_path / "run"), *arguments])
    assert error.value.code == 2
