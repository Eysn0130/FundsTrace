"""Exact-money regressions through synthetic analysis and report HTTP surfaces."""
import json
import re

import pytest


def insert_flow(database, case_id, payer, payee, amount, *, currency="CNY", minute=0,
                payer_name=None, payee_name=None):
    database.x("""insert into flows(case_id,trade_no,trade_time,payer_account,payer_name,
                  payee_account,payee_name,amount,currency) values (?,?,?,?,?,?,?,?,?)""",
               [case_id, f"SYN-{payer}-{payee}-{minute}", f"2026-01-01 10:{minute:02d}:00",
                payer, payer_name or payer, payee, payee_name or payee, amount, currency])


def run(client, case_id, **params):
    response = client.post("/api/analysis/run", json={"case_id": case_id, "params": params})
    assert response.status_code == 200, response.text
    return response.json()


def test_paths_cycles_exact_large_amounts_and_currency_case_boundaries(client, database, make_case):
    case = make_case("Synthetic exact paths")
    other_case = make_case("Synthetic isolated paths")
    insert_flow(database, case, "A", "B", "9999999999999999.99")
    insert_flow(database, case, "B", "C", "0.01", minute=1)
    insert_flow(database, case, "C", "A", "0.02", minute=2)
    insert_flow(database, case, "A", "B", "20.10", currency="USD")
    insert_flow(database, case, "B", "C", "30.20", currency="USD", minute=1)
    # An EUR first leg cannot connect to the CNY/USD second leg.
    insert_flow(database, case, "A", "B", "5.01", currency="EUR")
    insert_flow(database, other_case, "B", "C", "54321.98")
    paths = client.post("/api/analysis/paths", json={"case_id": case, "src": "A", "dst": "C"}).json()
    assert [(item["currency"], item["total"]) for item in paths["paths"]] == [
        ("CNY", "10000000000000000.00"), ("USD", "50.30")]
    assert paths["paths"][0]["path"][0]["amount"] == "9999999999999999.99"
    assert all(step["currency"] == item["currency"] for item in paths["paths"] for step in item["path"])
    results = run(client, case)
    assert results["cycles"]["count"] == 1
    cycle = results["cycles"]["cycles"][0]
    assert (cycle["currency"], cycle["total"], cycle["len"]) == ("CNY", "10000000000000000.02", 3)
    assert {edge["amount"] for edge in cycle["edges"]} == {"9999999999999999.99", "0.01", "0.02"}
    isolated = client.post("/api/analysis/paths", json={"case_id": other_case, "src": "A", "dst": "C"}).json()
    assert isolated["paths"] == []
    assert results["money_contract"]["avg_amount"].endswith("ROUND_HALF_EVEN")
    assert "z" in results["money_contract"]["approximate_metrics"]


def test_fast_flows_one_cent_difference_at_large_magnitude_is_not_equal(client, database, make_case):
    case = make_case()
    insert_flow(database, case, "IN", "HUB", "9999999999999999.99")
    insert_flow(database, case, "HUB", "OUT", "9999999999999999.98", minute=1)
    params = {"ff_min_amount": "9999999999999999.99", "ff_tolerance": 0}
    assert run(client, case, **params)["fast_flows"]["pairs"] == []
    insert_flow(database, case, "HUB", "CENT", "0.01", minute=2)
    pair = run(client, case, **params)["fast_flows"]["pairs"][0]
    assert pair["in_flow"]["amount"] == pair["out_total"] == "9999999999999999.99"
    assert pair["diff"] == "0.00"
    # A threshold one cent higher must exclude the incoming transaction.
    assert run(client, case, **{**params, "ff_min_amount": "10000000000000000.00"})["fast_flows"]["pairs"] == []


@pytest.mark.parametrize(("out_amount", "expected"), [
    ("750.02", False), ("750.03", True), ("1250.05", True), ("1250.06", False)])
def test_fast_flow_tolerance_uses_exact_inclusive_cent_boundaries(client, database, make_case, out_amount, expected):
    case = make_case()
    insert_flow(database, case, "IN", "HUB", "1000.04")
    insert_flow(database, case, "HUB", "OUT", out_amount, minute=1)
    pairs = run(client, case, ff_min_amount="1000.04", ff_tolerance="0.25")["fast_flows"]["pairs"]
    assert bool(pairs) is expected
    if expected:
        assert pairs[0]["out_total"] == out_amount
        assert isinstance(pairs[0]["max_delay_min"], (int, float))


def test_fast_flow_pairing_does_not_cross_currency(client, database, make_case):
    case = make_case()
    insert_flow(database, case, "IN", "HUB", "1000.01", currency="CNY")
    insert_flow(database, case, "HUB", "OUT", "1000.01", currency="USD", minute=1)
    assert run(client, case, ff_min_amount="0.01", ff_tolerance=0)["fast_flows"]["pairs"] == []


def test_cycle_minimum_has_exact_inclusive_cent_boundary(client, database, make_case):
    case = make_case()
    insert_flow(database, case, "A", "B", "0.10")
    insert_flow(database, case, "B", "A", "0.20", minute=1)
    cycles = run(client, case, cycle_min_total="0.30")["cycles"]["cycles"]
    assert cycles[0]["total"] == "0.30"
    assert cycles[0]["len"] == 2
    assert run(client, case, cycle_min_total="0.31")["cycles"]["cycles"] == []


@pytest.mark.parametrize("value", [1.25, True, "NaN", "Infinity", "1.001", "-0.01"])
@pytest.mark.parametrize("endpoint", ["run", "report"])
@pytest.mark.parametrize("threshold", ["ff_min_amount", "cycle_min_total"])
def test_analysis_rejects_lossy_or_invalid_money_thresholds(client, make_case, value, endpoint, threshold):
    response = client.post(f"/api/analysis/{endpoint}", json={"case_id": make_case(), "params": {threshold: value}})
    assert response.status_code == 422


def test_monetary_outlier_mean_has_explicit_half_even_rounding(client, database, make_case):
    case = make_case()
    # A has mean 1000.005 -> 1000.00 (HALF_EVEN), and is the mean-amount outlier.
    for minute, amount in enumerate(("1000.00", "1000.01")):
        insert_flow(database, case, "SOURCE", "A", amount, minute=minute)
    for account, amount in zip("BCDEF", ("250.00", "275.00", "225.00", "300.00", "200.00")):
        for minute in range(4):
            insert_flow(database, case, "SOURCE", account, amount, minute=minute)
    result = run(client, case, subjects=list("ABCDEF"), out_sigma=1.5)["outliers"]
    row = next(item for item in result["outliers"] if item["account"] == "A")
    assert row["dim"] == "avg_amount"
    assert row["value"] == "1000.00"
    assert row["currency"] == "CNY"
    assert isinstance(row["z"], float)


def test_report_readback_preserves_exact_money_and_script_characters(client, database, make_case):
    case_name = "Synthetic </script><script>case()</script>"
    case = make_case(case_name)
    insert_flow(database, case, "IN", "HUB", "9999999999999999.99", payer_name="</script><img src=x>")
    insert_flow(database, case, "HUB", "OUT", "9999999999999999.98", minute=1)
    insert_flow(database, case, "HUB", "CENT", "0.01", minute=2)
    response = client.post("/api/analysis/report", json={"case_id": case, "params": {
        "ff_min_amount": "9999999999999999.99", "ff_tolerance": 0}})
    assert response.status_code == 200
    assert "CNY 9,999,999,999,999,999.99" in response.text
    assert "9,999,999,999,999,999.98" in response.text
    assert "差额 0.00" in response.text
    assert "CNY 0.01" in response.text
    encoded = re.search(r'<script type="application/json" id="funds-trace-report-data">(.*?)</script>', response.text, re.S)
    assert encoded
    snapshot = json.loads(encoded.group(1))
    assert snapshot["schema_version"] == "funds-report.v1"
    assert snapshot["case"]["case_name"] == case_name
    pair = snapshot["results"]["fast_flows"]["pairs"][0]
    assert pair["in_flow"]["amount"] == pair["out_total"] == "9999999999999999.99"
    assert pair["out_flows"][0]["amount"] == "9999999999999999.98"
    assert pair["in_flow"]["from"] == "</script><img src=x>"
    assert "</script><img src=x>" not in response.text
    assert "</script><script>case()" not in response.text


def test_all_algorithms_and_report_overview_share_one_source_snapshot(client, database, make_case, monkeypatch):
    from app import analytics

    case = make_case()
    insert_flow(database, case, "IN", "HUB", "1000.01")
    insert_flow(database, case, "HUB", "OUT", "1000.01", minute=1)
    original_query = analytics.db_q
    loads = []

    def read_then_concurrent_import(sql, params):
        rows = original_query(sql, params)
        loads.append(params[0])
        # The completed read is the analysis snapshot; later imports must not
        # change only some algorithms or the report's overview.
        insert_flow(database, case, "LATER", "HUB", "1000.02", minute=2)
        insert_flow(database, case, "HUB", "LATE-OUT", "1000.02", minute=3)
        return rows

    monkeypatch.setattr(analytics, "db_q", read_then_concurrent_import)
    response = client.post("/api/analysis/report", json={"case_id": case, "params": {"ff_tolerance": 0}})
    assert response.status_code == 200
    encoded = re.search(r'<script type="application/json" id="funds-trace-report-data">(.*?)</script>', response.text, re.S)
    snapshot = json.loads(encoded.group(1))
    assert loads == [case]
    assert snapshot["case"]["stats"] == {"flows": 2, "accounts": 3}
    assert snapshot["results"]["fast_flows"]["count"] == 1
    assert snapshot["results"]["source_snapshot"] == snapshot["case"]["stats"]
    assert database.q("select count(*) n from flows where case_id=?", [case])[0]["n"] == 4


def test_run_and_report_wire_existing_path_controls_with_exact_cents(client, database, make_case):
    case = make_case()
    insert_flow(database, case, "A", "B", "9999999999999999.99")
    insert_flow(database, case, "B", "C", "0.02", minute=1)
    params = {"path_src": "A", "path_dst": "C", "path_max_hops": 2}
    paths = run(client, case, **params)["paths"]
    assert paths["paths"][0]["total"] == "10000000000000000.01"
    assert paths["paths"][0]["currency"] == "CNY"
    assert run(client, case, **{**params, "path_max_hops": 1})["paths"]["paths"] == []
    response = client.post("/api/analysis/report", json={"case_id": case, "params": params})
    assert response.status_code == 200
    assert "多跳资金路径追踪" in response.text
    assert "CNY 10,000,000,000,000,000.01" in response.text
    assert "CNY 9,999,999,999,999,999.99" in response.text
    assert "CNY 0.02" in response.text
    encoded = re.search(r'<script type="application/json" id="funds-trace-report-data">(.*?)</script>', response.text, re.S)
    assert json.loads(encoded.group(1))["results"]["paths"] == paths


def test_selected_algorithms_run_and_report_share_scope_without_executing_others(client, database, make_case, monkeypatch):
    from app import analytics

    case = make_case("Synthetic selected analysis")
    insert_flow(database, case, "IN", "HUB", "9999999999999999.99")
    insert_flow(database, case, "HUB", "OUT", "9999999999999999.99", minute=1)

    def unselected(*args, **kwargs):
        raise AssertionError("An unselected algorithm was executed")

    monkeypatch.setattr(analytics, "find_cycles", unselected)
    monkeypatch.setattr(analytics, "account_outliers", unselected)
    monkeypatch.setattr(analytics, "_trace_paths", unselected)
    params = {"algorithms": ["fast_flows"], "ff_tolerance": 0}
    results = run(client, case, **params)
    assert results["algorithms"] == ["fast_flows"]
    assert not {"cycles", "outliers", "paths"} & results.keys()
    response = client.post("/api/analysis/report", json={"case_id": case, "params": params})
    assert response.status_code == 200
    encoded = re.search(r'<script type="application/json" id="funds-trace-report-data">(.*?)</script>', response.text, re.S)
    snapshot = json.loads(encoded.group(1))["results"]
    assert snapshot == results
    assert snapshot["fast_flows"]["pairs"][0]["in_flow"]["amount"] == "9999999999999999.99"
    assert "本次分析范围：</b>快进快出金额守恒配对" in response.text
    assert "CNY 9,999,999,999,999,999.99" in response.text
    for omitted in ("资金回流环路", "交易行为离群账户", "多跳资金路径追踪", "未发现资金闭环"):
        assert omitted not in response.text


def test_paths_only_scope_retains_exact_snapshot_without_global_no_findings_conclusion(client, database, make_case):
    case = make_case()
    insert_flow(database, case, "A", "B", "9999999999999999.99")
    insert_flow(database, case, "B", "C", "0.02", minute=1)
    params = {"algorithms": ["paths"], "path_src": "A", "path_dst": "C", "path_max_hops": 2}
    results = run(client, case, **params)
    assert results["algorithms"] == ["paths"]
    assert not {"cycles", "fast_flows", "outliers"} & results.keys()
    response = client.post("/api/analysis/report", json={"case_id": case, "params": params})
    assert response.status_code == 200
    assert "本次分析范围：</b>多跳资金路径追踪" in response.text
    assert "找到 1 条同币种资金路径" in response.text
    assert "本次分析未发现显著异常资金模式" not in response.text
    assert "CNY 10,000,000,000,000,000.01" in response.text
    encoded = re.search(r'<script type="application/json" id="funds-trace-report-data">(.*?)</script>', response.text, re.S)
    assert json.loads(encoded.group(1))["results"] == results


def test_zero_selected_result_is_scoped_and_legacy_defaults_remain(client, database, make_case):
    case = make_case()
    insert_flow(database, case, "IN", "HUB", "1000.01")
    insert_flow(database, case, "HUB", "OUT", "1000.01", minute=1)
    legacy = run(client, case)
    assert legacy["algorithms"] == ["cycles", "fast_flows", "outliers"]
    assert legacy["fast_flows"]["count"] == 1
    params = {"algorithms": ["cycles"]}
    response = client.post("/api/analysis/report", json={"case_id": case, "params": params})
    assert response.status_code == 200
    assert "本次分析范围：</b>资金回流环路检测" in response.text
    assert "未发现符合本次所选算法及参数的结果" in response.text
    assert "快进快出金额守恒配对" not in response.text
    assert "行为离群账户" not in response.text


@pytest.mark.parametrize("algorithms", [[], None, "cycles", {}, ["unknown"], [" cycles"],
                                         ["cycles", "cycles"], [1], [True], [["cycles"]]])
@pytest.mark.parametrize("endpoint", ["run", "report"])
def test_analysis_rejects_empty_unknown_or_malformed_algorithm_selection(client, make_case, algorithms, endpoint):
    response = client.post(f"/api/analysis/{endpoint}", json={
        "case_id": make_case(), "params": {"algorithms": algorithms}})
    assert response.status_code == 422


@pytest.mark.parametrize("endpoint", ["run", "report"])
def test_selected_paths_require_valid_origin_and_destination(client, make_case, endpoint):
    case = make_case()
    for params in ({"algorithms": ["paths"]}, {"algorithms": ["paths"], "path_src": "A"},
                   {"algorithms": ["paths"], "path_src": "A", "path_dst": "  "}):
        response = client.post(f"/api/analysis/{endpoint}", json={"case_id": case, "params": params})
        assert response.status_code == 422


@pytest.mark.parametrize(("parameter", "minimum", "maximum", "algorithm", "result_key", "default"), [
    ("cycle_window_days", 1, 365, "cycles", "window_days", 7),
    ("ff_window_minutes", 5, 1440, "fast_flows", "window_minutes", 30),
])
@pytest.mark.parametrize("endpoint", ["run", "report"])
def test_exposed_analysis_windows_require_bounded_integers(client, make_case, parameter, minimum,
                                                         maximum, algorithm, result_key, default, endpoint):
    case = make_case()
    for value in (minimum - 1, maximum + 1, -1, minimum + 0.5, True, False, str(minimum), None):
        response = client.post(f"/api/analysis/{endpoint}", json={
            "case_id": case, "params": {parameter: value}})
        assert response.status_code == 422, (parameter, value)
    for value in (None, minimum, maximum):
        params = {} if value is None else {parameter: value}
        response = client.post(f"/api/analysis/{endpoint}", json={"case_id": case, "params": params})
        assert response.status_code == 200
        if endpoint == "run":
            results = response.json()
        else:
            encoded = re.search(r'<script type="application/json" id="funds-trace-report-data">(.*?)</script>', response.text, re.S)
            results = json.loads(encoded.group(1))["results"]
        assert results[algorithm][result_key] == (default if value is None else value)


@pytest.mark.parametrize("params", [
    {"path_src": "A"}, {"path_src": " ", "path_dst": "B"},
    {"path_src": "A", "path_dst": "B", "path_max_hops": 0},
    {"path_src": "A", "path_dst": "B", "path_max_hops": 13},
    {"path_src": "A", "path_dst": "B", "path_max_hops": True}])
def test_path_controls_reject_incomplete_or_out_of_bounds_requests(client, make_case, params):
    assert client.post("/api/analysis/run", json={"case_id": make_case(), "params": params}).status_code == 422


@pytest.mark.parametrize("endpoint", ["run", "report", "paths"])
def test_analysis_missing_case_is_not_another_case(client, endpoint):
    body = {"case_id": 2147483647}
    if endpoint == "paths":
        body.update(src="A", dst="B")
    assert client.post(f"/api/analysis/{endpoint}", json=body).status_code == 404
