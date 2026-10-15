"""Report regressions use synthetic API input and inspect delivered HTML."""
import json
import re


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
