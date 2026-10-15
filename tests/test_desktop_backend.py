"""Desktop front door: unguessable ownership, no license bypass, EOF recovery."""
import io
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import urllib.error
import urllib.request

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from desktop_backend import DesktopBoundary, read_bootstrap


TOKEN = "a" * 64  # Public fixture, not a deployed runtime session.
HEADER = {"X-FundsTrace-Desktop": TOKEN}


@pytest.mark.parametrize("change", [
    {"protocol": True}, {"protocol": 2}, {"token": "short"}, {"token": "A" * 64},
    {"dataDir": "relative"}, {"synthetic": "false"}, {"unexpected": 1},
])
def test_bootstrap_rejects_invalid_or_ambiguous_settings(tmp_path, change):
    data = {"protocol": 1, "token": TOKEN, "dataDir": str(tmp_path), "synthetic": False}
    data.update(change)
    with pytest.raises(ValueError):
        read_bootstrap(io.StringIO(json.dumps(data) + "\n"))


def test_bootstrap_never_enables_synthetic_in_frozen_distribution(tmp_path, monkeypatch):
    data = {"protocol": 1, "token": TOKEN, "dataDir": str(tmp_path), "synthetic": True}
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(ValueError, match="unavailable"):
        read_bootstrap(io.StringIO(json.dumps(data) + "\n"))
    with pytest.raises(ValueError):
        read_bootstrap(io.StringIO("x" * 4097))


def test_desktop_front_door_rejects_missing_duplicate_cross_origin_and_wrong_host():
    app = FastAPI()

    @app.get("/private")
    def private():
        return {"reached": True}

    with TestClient(DesktopBoundary(app, TOKEN, 14321), base_url="http://127.0.0.1:14321") as client:
        for headers in [{}, {"X-FundsTrace-Desktop": "b" * 64}, {**HEADER, "Origin": "http://attacker.invalid"},
                        {**HEADER, "Origin": "null"}, {**HEADER, "Host": "localhost:14321"},
                        {**HEADER, "Sec-Fetch-Site": "cross-site"},
                        [("X-FundsTrace-Desktop", TOKEN), ("X-FundsTrace-Desktop", TOKEN)]]:
            response = client.get("/private", headers=headers)
            assert response.status_code == 403
            assert TOKEN not in response.text
        response = client.get("/private", headers={**HEADER, "Origin": "http://127.0.0.1:14321"})
        assert response.json() == {"reached": True}
        assert response.headers["cache-control"] == "no-store"
        assert "script-src 'self';" in response.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert "'unsafe-inline'" not in response.headers["content-security-policy"].split("script-src", 1)[1].split(";", 1)[0]
        assert client.get("/__desktop/health", headers=HEADER).json() == {
            "protocol": 1, "service": "FundsTrace", "ready": True}


def test_desktop_session_does_not_replace_product_activation(database, monkeypatch):
    from app.main import app
    monkeypatch.delenv("FUNDS_TRACE_LICENSE_SECRET", raising=False)
    with TestClient(DesktopBoundary(app, TOKEN, 14321), base_url="http://127.0.0.1:14321") as client:
        assert client.get("/__desktop/health", headers=HEADER).status_code == 200
        assert client.get("/api/cases", headers=HEADER).status_code == 403
        assert client.get("/license", headers=HEADER).status_code == 200
        script = client.get("/static/license.js", headers=HEADER, follow_redirects=False)
        assert script.status_code == 200
        assert "javascript" in script.headers["content-type"]
        assert "btnCopyMc" in script.text


@pytest.mark.parametrize("synthetic", [False, True])
def test_owned_process_reports_real_listener_and_exits_on_parent_eof(tmp_path, synthetic):
    root = Path(__file__).resolve().parents[1]
    data = tmp_path / "中文 空格 数据"
    working = tmp_path / "中文 工作目录"
    working.mkdir()
    ignored = tmp_path / "must-not-be-used"
    env = dict(os.environ, FUND_DATA_DIR=str(ignored), PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    env.pop("FUNDS_TRACE_LICENSE_SECRET", None)
    child = subprocess.Popen([sys.executable, str(root / "desktop_backend.py")], cwd=working,
                             env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8")
    ready_queue = queue.Queue()
    threading.Thread(target=lambda: ready_queue.put(child.stdout.readline()), daemon=True).start()
    try:
        child.stdin.write(json.dumps({"protocol": 1, "token": TOKEN, "dataDir": str(data), "synthetic": synthetic}) + "\n")
        child.stdin.flush()
        line = ready_queue.get(timeout=40)
        ready = json.loads(line)
        assert set(ready) == {"protocol", "port"} and ready["protocol"] == 1
        assert type(ready["port"]) is int and 0 < ready["port"] < 65536
        assert TOKEN not in line
        origin = f"http://127.0.0.1:{ready['port']}"

        def get(path, headers=HEADER):
            return urllib.request.urlopen(urllib.request.Request(origin + path, headers=headers), timeout=10)

        assert json.load(get("/__desktop/health"))["ready"] is True
        with pytest.raises(urllib.error.HTTPError) as unauthorized:
            get("/api/cases", {})
        assert unauthorized.value.code == 403
        if synthetic:
            cases = json.load(get("/api/cases"))
            assert {case["name"] for case in cases} >= {"合成演示 · 流水核对", "合成演示 · 同账号隔离对照"}
            graph = json.load(get("/api/graph?case_id=2"))
            assert (len(graph["nodes"]), len(graph["edges"])) == (200, 199)
            assert any(t["amount"] == "9999999999999999.99" for e in graph["edges"] for t in e["amount_totals"])
        else:
            assert json.load(get("/api/license/info"))["activated"] is False
            assert b'license.js' in get("/license").read()
        assert not ignored.exists()
        child.stdin.close()
        child.wait(timeout=15)
        assert child.returncode == 0
        assert data.joinpath("fund.duckdb").is_file()
        with pytest.raises(urllib.error.URLError):
            get("/__desktop/health")
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()
