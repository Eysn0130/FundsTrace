"""Report regressions use synthetic API input and inspect delivered HTML."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def test_report_dependencies_load_only_for_a_report_request(tmp_path):
    # A fresh process prevents collection-time imports or earlier report tests
    # from hiding an eager Matplotlib import. All HTTP and database work is real.
    source = r'''
import json
import re
import sys

from app.main import app
from app import db
from fastapi.testclient import TestClient

def assert_report_unloaded():
    assert "app.report" not in sys.modules
    assert not any(name == "matplotlib" or name.startswith("matplotlib.") for name in sys.modules)

assert_report_unloaded()
with TestClient(app) as client:
    assert client.get("/api/cases").status_code == 200
    created = client.post("/api/cases", json={"name": "Synthetic lazy report"})
    assert created.status_code == 200
    cid = created.json()["id"]
    db.x("insert into flows(case_id, trade_no, trade_time, payer_account, payee_account, amount, currency) "
         "values (?, 'SYN-1', '2026-01-01 10:00:00', 'SYN-A', 'SYN-B', '125.01', 'CNY'), "
         "(?, 'SYN-2', '2026-01-01 10:10:00', 'SYN-B', 'SYN-A', '125.01', 'CNY')", [cid, cid])
    payload = {"case_id": cid, "params": {"algorithms": ["cycles"]}}
    analysis = client.post("/api/analysis/run", json=payload)
    assert analysis.status_code == 200
    assert analysis.json()["cycles"]["count"] == 1
    assert_report_unloaded()

    response = client.post("/api/analysis/report", json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-disposition"].startswith("attachment; filename*=UTF-8''")
    assert "资金回流环路" in response.text
    assert "CNY 250.02" in response.text
    encoded = re.search(r'<script type="application/json" id="funds-trace-report-data">(.*?)</script>', response.text, re.S)
    assert encoded is not None
    snapshot = json.loads(encoded.group(1))
    assert snapshot["schema_version"] == "funds-report.v1"
    assert snapshot["case_id"] == str(cid)
    assert snapshot["case"]["case_name"] == "Synthetic lazy report"
    assert snapshot["case"]["stats"] == {"flows": 2, "accounts": 2}
    assert snapshot["results"]["source_snapshot"] == snapshot["case"]["stats"]
    assert snapshot["results"]["cycles"]["cycles"][0]["total"] == "250.02"
    assert "app.report" in sys.modules
    assert "matplotlib.pyplot" in sys.modules
'''
    env = {**os.environ, "FUND_DATA_DIR": str(tmp_path / "data"),
           "MPLCONFIGDIR": str(tmp_path / "matplotlib"), "PYTHONDONTWRITEBYTECODE": "1",
           "FUNDS_TRACE_LICENSE_SECRET": "synthetic-regression-key-not-for-use"}
    env.pop("FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE", None)
    result = subprocess.run([sys.executable, "-c", source], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_cycle_report_is_downloadable_and_escapes_user_text(client, database, make_case):
    cid = make_case("Synthetic <script>alert('case')</script>")
    # Two chronological transfers form one independently specified cycle.
    database.x("insert into flows(case_id, trade_no, trade_time, payer_name, payer_account, "
               "payee_name, payee_account, amount) values "
               "(?, 'SYN-1', '2026-01-01 10:00:00', '<img src=x onerror=alert(1)>', 'SYN-A', 'Receiver', 'SYN-B', 125), "
               "(?, 'SYN-2', '2026-01-01 10:10:00', 'Receiver', 'SYN-B', '<img src=x onerror=alert(1)>', 'SYN-A', 125)",
               [cid, cid])
    response = client.post("/api/analysis/report", json={"case_id": cid, "params": {}})
    assert response.status_code == 200
    assert "资金回流环路" in response.text
    assert "&lt;script&gt;" in response.text
    assert "<script>alert('case')</script>" not in response.text
    assert "<img src=x onerror=alert(1)>" not in response.text


def test_analysis_accepts_subject_string(client, make_case):
    response = client.post("/api/analysis/run", json={"case_id": make_case(), "params": {"subjects": "SYN-A，SYN-B"}})
    assert response.status_code == 200
    assert response.json()["subjects"] == ["SYN-A", "SYN-B"]


def test_graph_export_preserves_literal_template_markers_and_escapes_case_name(client):
    payload = {
        "case_name": "Synthetic <script>alert('case')</script> __DATA__",
        "nodes": [{"id": "SYN-A", "label": "__CASE__ __G6__ </script><script>alert('node')</script>"}],
        "edges": [],
    }
    response = client.post("/api/export/graph.html", json=payload)
    assert response.status_code == 200
    assert "<script>alert('case')</script>" not in response.text
    assert "Synthetic &lt;script&gt;" in response.text
    encoded = re.search(r"var DATA = (.*?);\nfunction buildWM", response.text, re.S)
    assert encoded is not None
    decoded = json.loads(encoded.group(1).replace("<\\/", "</"))
    assert decoded["case_name"] == payload["case_name"]
    assert decoded["nodes"] == payload["nodes"]
    assert "</script><script>alert('node')" not in response.text
