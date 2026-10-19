"""Reports retain the scope and evidential limits of bounded graph searches."""
import json
import re

from test_analysis_money import insert_flow


def test_cycle_report_preserves_exact_legs_and_explains_subject_and_ratio_scope(client, database, make_case):
    case = make_case("Synthetic bounded report")
    insert_flow(database, case, "A", "B", "100.01", minute=6)
    insert_flow(database, case, "B", "C", "100.01", minute=3)
    insert_flow(database, case, "C", "A", "100.01", minute=5)
    response = client.post("/api/analysis/report", json={"case_id": case, "params": {
        "algorithms": ["cycles"], "subjects": ["A"],
    }})
    assert response.status_code == 200, response.text
    encoded = re.search(r'<script type="application/json" id="funds-trace-report-data">(.*?)</script>', response.text, re.S)
    result = json.loads(encoded.group(1))["results"]
    cycle = result["cycles"]["cycles"][0]
    assert cycle["accounts"] == ["B", "C", "A"]
    assert cycle["total"] == "300.03"
    assert cycle["first_outflow"] == cycle["last_inflow"] == "100.01"
    for phrase in ("回转比例（近似值）", "不证明同一笔本金", "本次有界搜索候选", "实际时序起点"):
        assert phrase in response.text


def test_empty_report_does_not_claim_exhaustive_cycle_absence(client, make_case):
    response = client.post("/api/analysis/report", json={"case_id": make_case(), "params": {"algorithms": ["cycles"]}})
    assert response.status_code == 200, response.text
    assert "本次搜索未发现" in response.text
    assert "本次有界搜索候选" in response.text
