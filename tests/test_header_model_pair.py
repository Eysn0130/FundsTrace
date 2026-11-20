"""Synthetic process ownership and deadlines for independent header review."""
import errno
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from app import header_model


class Child:
    def __init__(self, directory, alias, key_digest):
        self.directory = directory
        self.alias = alias
        self.key_digest = key_digest
        self.terminated = self.exited = self.killed = False
        self.exit_status = 0

    def poll(self):
        return self.exit_status if self.exited else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        assert self.terminated
        if timeout == 0 and not self.killed:
            raise subprocess.TimeoutExpired("synthetic child", timeout)
        self.exited = True

    def kill(self):
        self.killed = True


@pytest.fixture
def pair(monkeypatch, tmp_path):
    server = tmp_path / "synthetic-server"
    server.touch()
    first, second = tmp_path / "primary.gguf", tmp_path / "review.gguf"
    samples = ((first, b"synthetic primary"), (second, b"synthetic reviewer"))
    slots = tuple(zip(("FUND_HEADER_MODEL", "FUND_HEADER_REVIEW_MODEL"), (len(raw) for _, raw in samples)))
    artifacts = {}
    for (key, size), (path, raw) in zip(slots, samples):
        path.write_bytes(raw)
        monkeypatch.setenv(key, str(path))
        artifacts[size] = (key, "synthetic-revision", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(header_model, "_MODEL_SLOTS", slots)
    monkeypatch.setattr(header_model, "MODEL_ARTIFACTS", artifacts)
    monkeypatch.setenv("FUND_HEADER_LLAMA_SERVER", str(server))
    monkeypatch.setenv("FUND_SYNTHETIC_UNUSED_CHILD_ENV", "synthetic")

    attempts, launched, requests = [], [], []
    runtime = SimpleNamespace(version=header_model.SERVER_BUILD, launch_errors={}, failed_starts=set(),
                              after_reply=lambda _index, _cancelled, _deadline: None,
                              content=lambda index: json.dumps({"synthetic pass": index}),
                              real_popen=subprocess.Popen)

    def version(command, *, env, capture_output, timeout, creationflags):
        assert command == [str(server), "--version"]
        assert capture_output and 0 < timeout <= 3
        assert creationflags == getattr(subprocess, "CREATE_NO_WINDOW", 0)
        assert "FUND_SYNTHETIC_UNUSED_CHILD_ENV" not in env
        return SimpleNamespace(returncode=0, stdout=runtime.version, stderr=b"")

    def launch(command, *, env, cwd, stdin, stdout, stderr, creationflags):
        assert all(child.exited and not child.directory.exists() for _, child in launched)
        assert all(not directory.exists() for _, directory in attempts)
        assert creationflags == getattr(subprocess, "CREATE_NO_WINDOW", 0)
        assert "FUND_SYNTHETIC_UNUSED_CHILD_ENV" not in env
        assert all(Path(env[key]) == cwd for key in ("TMPDIR", "TMP", "TEMP"))
        assert "--offline" in command and "--no-cache-prompt" in command and "--ctx-size" in command
        key_file = Path(command[command.index("--api-key-file") + 1])
        assert key_file.parent == cwd
        key_digest = hashlib.sha256(key_file.read_text().strip().encode()).hexdigest()
        model = Path(command[command.index("--model") + 1])
        attempts.append((model, cwd))
        index = len(attempts)
        if index in runtime.launch_errors:
            raise runtime.launch_errors[index]
        child = Child(cwd, command[command.index("--alias") + 1], key_digest)
        if index in runtime.failed_starts:
            child.exited, child.exit_status = True, 18
        launched.append((model, child))
        return child

    def call(address, key, path, body, cancelled, deadline):
        _, child = launched[-1]
        assert not child.exited
        assert hashlib.sha256(key.encode()).hexdigest() == child.key_digest
        if path == "/v1/models":
            assert body is None
            return {"data": [{"id": child.alias}]}
        assert path == "/v1/chat/completions"
        request = json.loads(body)
        assert request.pop("model") == child.alias
        requests.append((address, request, deadline))
        index = len(launched)
        runtime.after_reply(index, cancelled, deadline)
        return {"choices": [{"message": {"content": runtime.content(index)}}]}

    broker = header_model.LocalHeaderModel()
    monkeypatch.setattr(header_model.subprocess, "run", version)
    monkeypatch.setattr(header_model.subprocess, "Popen", launch)
    monkeypatch.setattr(broker, "_call", call)
    runtime.broker, runtime.paths, runtime.launched, runtime.requests = broker, (first, second), launched, requests
    runtime.attempts = attempts
    return runtime


def test_review_models_use_separate_children_and_identical_inference_input(pair):
    started = time.monotonic()
    schema = {"type": "object"}
    assert pair.broker.suggest("synthetic prompt", schema, threading.Event(), started + 90) == (
        {"synthetic pass": 1}, {"synthetic pass": 2})
    assert tuple(model for model, _ in pair.launched) == pair.paths
    a, b = (child for _, child in pair.launched)
    first, second = pair.requests
    assert a.directory != b.directory and a.alias != b.alias and a.key_digest != b.key_digest
    assert first[0] != second[0]
    assert first[1] == second[1]
    assert first[1]["messages"][-1] == {"role": "user", "content": "synthetic prompt"}
    assert first[1]["response_format"]["json_schema"]["schema"] == schema
    assert first[2] == second[2] and first[2] <= started + header_model.REQUEST_SECONDS + 0.1
    assert all(child.terminated and child.exited and not child.directory.exists() for _, child in pair.launched)


@pytest.mark.parametrize("pass_index", [1, 2])
@pytest.mark.parametrize("fault", ["launch", "transport"])
def test_pair_retries_only_the_interrupted_pass_and_keeps_its_deadline(pair, pass_index, fault):
    if fault == "launch":
        pair.launch_errors[pass_index] = OSError(errno.EAGAIN, "synthetic temporary refusal")
    else:
        def disconnect(index, _cancelled, _deadline):
            if index == pass_index:
                raise ConnectionResetError("synthetic disconnected socket")

        pair.after_reply = disconnect
    started = time.monotonic()
    reply = pair.broker.suggest("synthetic retry", {}, threading.Event(), started + 90)
    assert reply == ({"synthetic pass": 2 if fault == "transport" and pass_index == 1 else 1},
                     {"synthetic pass": 3 if fault == "transport" else 2})
    assert [model for model, _ in pair.attempts] == (
        [pair.paths[0], *pair.paths] if pass_index == 1 else [*pair.paths, pair.paths[1]])
    assert all(not directory.exists() for _, directory in pair.attempts)
    assert all(child.exited and child.terminated for _, child in pair.launched)
    assert len({request[2] for request in pair.requests}) == 1
    assert pair.requests[0][2] <= started + header_model.REQUEST_SECONDS + 0.1
    assert all(request[1] == pair.requests[0][1] for request in pair.requests)


@pytest.mark.parametrize("fault", ["repeated-launch", "repeated-transport", "resource", "startup-exit",
                                   "transport-exit", "timeout", "malformed"])
def test_pair_without_both_responses_does_not_retry_permanent_faults_or_return_a_partial_reply(pair, fault):
    if fault == "repeated-launch":
        pair.launch_errors = {index: OSError(errno.EAGAIN, "synthetic temporary refusal") for index in (2, 3)}
    elif fault == "resource":
        pair.launch_errors[2] = OSError(errno.ENOMEM, "synthetic allocation failure")
    elif fault == "startup-exit":
        pair.failed_starts.add(2)
    elif fault == "malformed":
        pair.content = lambda index: "{}" if index == 1 else "malformed"
    else:
        def interrupt(index, _cancelled, _deadline):
            if index == 1:
                return
            if fault == "timeout":
                raise TimeoutError("synthetic socket read timeout")
            if fault == "transport-exit":
                pair.launched[-1][1].exited = True
            raise ConnectionResetError("synthetic disconnected socket")

        pair.after_reply = interrupt
    assert pair.broker.suggest("synthetic failure", {}, threading.Event(), time.monotonic() + 12) is None
    assert [model for model, _ in pair.attempts] == (
        [*pair.paths, pair.paths[1]] if fault.startswith("repeated-") else list(pair.paths))
    assert all(not directory.exists() for _, directory in pair.attempts)
    assert all(child.exited for _, child in pair.launched)


def test_pair_shares_its_single_retry_across_both_passes(pair):
    pair.launch_errors[1] = OSError(errno.EAGAIN, "synthetic temporary refusal")

    def disconnect(index, _cancelled, _deadline):
        if index == 2:
            raise ConnectionResetError("synthetic disconnected reviewer")

    pair.after_reply = disconnect
    assert pair.broker.suggest("synthetic failure", {}, threading.Event(), time.monotonic() + 12) is None
    assert [model for model, _ in pair.attempts] == [pair.paths[0], *pair.paths]
    assert all(child.exited and not child.directory.exists() for _, child in pair.launched)


@pytest.mark.parametrize("cause", ["cancel", "deadline"])
def test_transport_failure_after_request_budget_ends_releases_without_restarting(pair, monkeypatch, cause):
    now = [100.0]
    monkeypatch.setattr(header_model, "time", SimpleNamespace(monotonic=lambda: now[0]))

    def disconnect(_index, cancelled, deadline):
        if cause == "cancel":
            cancelled.set()
        else:
            now[0] = deadline + 0.01
        raise ConnectionResetError("synthetic disconnected socket")

    pair.after_reply = disconnect
    assert pair.broker.suggest("synthetic expired pair", {}, threading.Event(), 200.0) is None
    [(model, directory)] = pair.attempts
    assert model == pair.paths[0] and not directory.exists()
    assert pair.launched[0][1].exited


def test_real_child_exiting_during_startup_is_reaped_and_temp_files_removed(pair, monkeypatch):
    attempts = []

    def start_bad_child(_command, **kwargs):
        child = pair.real_popen([sys.executable, "-I", "-c", "raise SystemExit(19)"], **kwargs)
        attempts.append((child, kwargs["cwd"]))
        return child

    def not_listening(*_args):
        raise ConnectionRefusedError("synthetic child did not listen")

    with monkeypatch.context() as patch:
        patch.setattr(header_model.subprocess, "Popen", start_bad_child)
        patch.setattr(pair.broker, "_call", not_listening)
        assert pair.broker.suggest("synthetic bad child", {}, threading.Event(), time.monotonic() + 3) is None
    [(child, directory)] = attempts
    assert child.returncode == 19 and not directory.exists()
    assert pair.broker.suggest("synthetic next pair", {}, threading.Event(), time.monotonic() + 12) is not None


@pytest.mark.parametrize("stop_at", [1, 2])
@pytest.mark.parametrize("cause", ["cancel", "deadline"])
def test_interrupted_pair_releases_each_started_child(pair, monkeypatch, stop_at, cause):
    now = [100.0]
    monkeypatch.setattr(header_model, "time", SimpleNamespace(monotonic=lambda: now[0]))

    def interrupt(index, cancelled, deadline):
        if index == stop_at:
            if cause == "cancel":
                cancelled.set()
            else:
                now[0] = deadline + 0.01

    pair.after_reply = interrupt
    assert pair.broker.suggest("synthetic stop", {}, threading.Event(), 200.0) is None
    assert len(pair.launched) == stop_at
    assert all(child.terminated and child.exited and not child.directory.exists() for _, child in pair.launched)
    assert pair.launched[-1][1].killed == (cause == "deadline")


def test_verification_cache_reuses_only_identical_artifacts(pair, monkeypatch):
    opened = []
    original = Path.open

    def track(path, *args, **kwargs):
        if path in pair.paths and args == ("rb",):
            opened.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", track)
    for _ in range(2):
        assert pair.broker.suggest("synthetic cache", {}, threading.Event(), time.monotonic() + 12) is not None
    assert opened == list(pair.paths)
    reviewer = pair.paths[1]
    replacement = reviewer.with_name("replacement.gguf")
    replacement.write_bytes(b"?" * reviewer.stat().st_size)
    replacement.replace(reviewer)
    assert pair.broker.suggest("synthetic replacement", {}, threading.Event(), time.monotonic() + 12) is None
    assert opened == [*pair.paths, reviewer]
    assert len(pair.launched) == 4


@pytest.mark.parametrize("fault", ["missing-reviewer", "missing-file", "same-artifact", "reversed", "wrong-server"])
def test_review_configuration_must_pin_both_artifacts_and_runtime(pair, monkeypatch, fault):
    if fault == "missing-reviewer":
        monkeypatch.delenv("FUND_HEADER_REVIEW_MODEL")
    elif fault == "missing-file":
        pair.paths[1].unlink()
    elif fault == "same-artifact":
        monkeypatch.setenv("FUND_HEADER_REVIEW_MODEL", str(pair.paths[0]))
    elif fault == "reversed":
        monkeypatch.setenv("FUND_HEADER_MODEL", str(pair.paths[1]))
        monkeypatch.setenv("FUND_HEADER_REVIEW_MODEL", str(pair.paths[0]))
    else:
        pair.version = b"synthetic unexpected runtime"
    assert pair.broker.suggest("synthetic configuration", {}, threading.Event(), time.monotonic() + 12) is None
    assert pair.launched == []
