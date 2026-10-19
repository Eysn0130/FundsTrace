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


def test_paths_accept_account_names_and_expand_shared_names_to_every_account(client, database, make_case):
    case = make_case("Synthetic paths by name")
    insert_flow(database, case, "A", "B", "10.00", minute=1, payer_name="张三", payee_name="李四")
    insert_flow(database, case, "A2", "B", "20.00", minute=5, payer_name="张三", payee_name="李四")
    insert_flow(database, case, "B", "C", "30.00", minute=9, payee_name="王五")

    by_name = client.post("/api/analysis/paths", json={"case_id": case, "src": "张三", "dst": "王五"}).json()
    assert by_name["src_accounts"] == ["A", "A2"]
    assert by_name["dst_accounts"] == ["C"]
    assert by_name["count"] == 2
    # Both A and A2 are start candidates; the shared name is never narrowed silently.
    assert {item["path"][0]["from"] for item in by_name["paths"]} == {"A", "A2"}
    # Default order is unchanged: largest currency-isolated total first.
    assert [item["total"] for item in by_name["paths"]] == ["50.00", "40.00"]

    by_account = client.post("/api/analysis/paths", json={"case_id": case, "src": "A", "dst": "C"}).json()
    assert by_account["count"] == 1 and by_account["src_accounts"] == ["A"]

    missing = client.post("/api/analysis/paths", json={"case_id": case, "src": "查无此人", "dst": "C"}).json()
    assert missing["count"] == 0 and missing["src_accounts"] == []
    assert "没有匹配到账户" in missing["note"]
    assert client.post("/api/analysis/paths",
                       json={"case_id": case, "src": "张三", "dst": "C", "order": "recent"}).status_code == 422


def test_time_order_sorts_by_first_leg_and_flags_chronological_continuity(client, database, make_case):
    case = make_case("Synthetic paths by time")
    insert_flow(database, case, "X", "Y", "1.00", minute=1, payer_name="共同")
    insert_flow(database, case, "Y", "Z", "1.00", minute=2)
    # Same start name, later first leg, and the second leg happened earlier.
    insert_flow(database, case, "W", "V", "1.00", minute=5, payer_name="共同")
    insert_flow(database, case, "V", "Z", "1.00", minute=0)

    timed = client.post("/api/analysis/paths",
                        json={"case_id": case, "src": "共同", "dst": "Z", "order": "time"}).json()
    assert timed["order"] == "time" and timed["count"] == 2
    assert [item["start_time"] for item in timed["paths"]] == ["2026-01-01 10:01:00", "2026-01-01 10:05:00"]
    assert [item["continuous"] for item in timed["paths"]] == [True, False]
    assert [(item["start_time"], item["end_time"]) for item in timed["paths"]] == [
        ("2026-01-01 10:01:00", "2026-01-01 10:02:00"),
        ("2026-01-01 10:05:00", "2026-01-01 10:00:00")]

    # The same query must stay byte-identical when no order is requested.
    default = client.post("/api/analysis/paths", json={"case_id": case, "src": "共同", "dst": "Z"}).json()
    assert default["order"] == "amount"
    assert sorted(item["total"] for item in default["paths"]) == ["2.00", "2.00"]

    results = run(client, case, algorithms=["paths"], path_src="共同", path_dst="Z", path_order="time")
    assert results["paths"]["order"] == "time"
    assert results["paths"]["paths"][0]["continuous"] is True
def test_cycle_needs_a_start_account_where_every_hop_follows_in_time(client, database, make_case):
    """闭环必须能按时间走完：存在某个起点，使每一跳都不早于上一跳。"""
    # 从 B 起算 10:03 → 10:05 → 10:06 递增；从 A 起算 10:06 → 10:03 是倒流。
    rotated = make_case("Synthetic cycle chronological start")
    insert_flow(database, rotated, "A", "B", "1.00", minute=6)
    insert_flow(database, rotated, "B", "C", "1.00", minute=3)
    insert_flow(database, rotated, "C", "A", "1.00", minute=5)
    found = run(client, rotated, algorithms=["cycles"])["cycles"]
    assert found["count"] == 1
    cycle = found["cycles"][0]
    # 环路径按资金实际流动的时间顺序展示，起点是真正的时序起点 B。
    assert cycle["accounts"] == ["B", "C", "A"]
    assert (cycle["start_time"], cycle["end_time"]) == ("2026-01-01 10:03:00", "2026-01-01 10:06:00")
    assert cycle["span_days"] == 0.0  # 3 分钟按 0.1 天精度取整

    # 两处倒流：任何起点都读不出时间顺序，不得报为闭环（跨度仅 7 分钟，远小于 7 天）。
    broken = make_case("Synthetic cycle backwards twice")
    insert_flow(database, broken, "A", "B", "1.00", minute=0)
    insert_flow(database, broken, "B", "C", "1.00", minute=5)
    insert_flow(database, broken, "C", "D", "1.00", minute=2)
    insert_flow(database, broken, "D", "A", "1.00", minute=7)
    assert run(client, broken, algorithms=["cycles"])["cycles"]["cycles"] == []

    # 同一时刻完成不算倒流（非降序即可），2 跳闭环同样保留。
    instant = make_case("Synthetic cycle same instant")
    insert_flow(database, instant, "A", "B", "1.00", minute=3)
    insert_flow(database, instant, "B", "C", "1.00", minute=3)
    insert_flow(database, instant, "C", "A", "1.00", minute=3)
    assert run(client, instant, algorithms=["cycles"])["cycles"]["count"] == 1
    two_hop = make_case("Synthetic two hop chronological")
    insert_flow(database, two_hop, "A", "B", "1.00", minute=0)
    insert_flow(database, two_hop, "B", "A", "1.00", minute=1)
    pair = run(client, two_hop, algorithms=["cycles"])["cycles"]["cycles"][0]
    assert (pair["len"], pair["accounts"]) == (2, ["A", "B"])

def test_cycle_closes_visually_and_ranks_by_return_ratio(client, database, make_case):
    """闭环路径末尾重复起点；回转比例（末笔回流 ÷ 首笔转出）接近 1 的优先。"""
    case = make_case("Synthetic cycle return ratio")
    # 三组独立闭环，回转比例分别为 100%、50%、200%（都在 7 天窗口内、时序递增）。
    insert_flow(database, case, "P1", "Q1", "100.00", minute=0)
    insert_flow(database, case, "Q1", "P1", "100.00", minute=1)
    insert_flow(database, case, "P2", "Q2", "100.00", minute=0)
    insert_flow(database, case, "Q2", "P2", "50.00", minute=1)
    insert_flow(database, case, "P3", "Q3", "100.00", minute=0)
    insert_flow(database, case, "Q3", "P3", "200.00", minute=1)

    cycles = run(client, case, algorithms=["cycles"])["cycles"]["cycles"]
    assert [round(c["return_ratio"], 4) for c in cycles] == [1.0, 0.5, 2.0]
    assert cycles[0]["accounts_names"] == ["P1", "Q1"]
    # 闭环展示：末尾重复起点，读起来是"出去又回来"。
    assert cycles[0]["loop_names"] == ["P1", "Q1", "P1"]
    assert (cycles[0]["first_outflow"], cycles[0]["last_inflow"]) == ("100.00", "100.00")

    # 比例相同的两档按金额合计降序；首笔为 0 时无从比较，比例为空且排最后。
    tie = make_case("Synthetic cycle ratio tie")
    insert_flow(database, tie, "A", "B", "100.00", minute=0)
    insert_flow(database, tie, "B", "A", "200.00", minute=1)
    insert_flow(database, tie, "C", "D", "300.00", minute=0)
    insert_flow(database, tie, "D", "C", "600.00", minute=1)
    # 首笔为 0 的闭环：起点仍要有其他转出才会成为候选，比例因此为空。
    insert_flow(database, tie, "E", "F", "0.00", minute=0)
    insert_flow(database, tie, "F", "E", "10.00", minute=1)
    insert_flow(database, tie, "E", "G", "5.00", minute=2)
    ranked = run(client, tie, algorithms=["cycles"])["cycles"]["cycles"]
    assert [c["total"] for c in ranked[:2]] == ["900.00", "300.00"]
    assert ranked[2]["return_ratio"] is None
    assert ranked[2]["loop_names"][0] == ranked[2]["loop_names"][-1]


def test_path_names_use_all_alias_evidence_before_currency_grouping(client, database, make_case):
    case = make_case("Synthetic later path names")
    insert_flow(database, case, "A", "X", "1.00", minute=0)
    database.x("update flows set payer_name = null where case_id=?", [case])
    insert_flow(database, case, "A", "D", "2.00", minute=1, payer_name="合成户名", payee_name="合成终点")
    insert_flow(database, case, "A", "D", "3.00", minute=2, currency="USD", payer_name="旧名", payee_name="旧终点")
    response = client.post("/api/analysis/paths", json={"case_id": case, "src": "合成户名", "dst": "合成终点"})
    assert response.status_code == 200
    named = response.json()
    assert named["src_accounts"] == ["A"] and named["dst_accounts"] == ["D"]
    assert [(item["currency"], item["total"]) for item in named["paths"]] == [("CNY", "2.00"), ("USD", "3.00")]
    alias = client.post("/api/analysis/paths", json={"case_id": case, "src": "旧名", "dst": "旧终点"}).json()
    assert alias["paths"] == named["paths"]
    # An exact account token wins even when another account uses it as a name.
    insert_flow(database, case, "OTHER", "D", "4.00", minute=3, payer_name="A")
    exact = client.post("/api/analysis/paths", json={"case_id": case, "src": "A", "dst": "D"}).json()
    assert exact["src_accounts"] == ["A"]


def test_path_limit_is_applied_after_time_ranking_across_direct_and_shared_starts(client, database, make_case):
    case = make_case("Synthetic bounded path ranking")
    insert_flow(database, case, "Z", "MID", "1.00", minute=0, payer_name="合成共同")
    insert_flow(database, case, "MID", "D", "1.00", minute=1)
    insert_flow(database, case, "Z", "D", "9.00", minute=5, payer_name="合成共同")
    insert_flow(database, case, "A", "D", "8.00", minute=6, payer_name="合成共同")
    timed = client.post("/api/analysis/paths", json={
        "case_id": case, "src": "合成共同", "dst": "D", "order": "time", "max_paths": 1}).json()
    assert timed["paths"][0]["start_time"] == "2026-01-01 10:00:00"
    assert timed["paths"][0]["hops"] == 2
    assert timed["search"]["complete"] is True
    assert timed["search"]["result_limited"] is True and timed["truncated"] is True
    assert timed["search"]["matches_considered"] == 3
    amount = run(client, case, algorithms=["paths"], path_src="合成共同", path_dst="D", path_max_paths=1)["paths"]
    assert amount["paths"][0]["total"] == "9.00"


def test_cycle_subject_can_be_inside_the_chronological_loop_and_max_len_is_exact(client, database, make_case):
    case = make_case("Synthetic rotated subject cycle")
    insert_flow(database, case, "B", "C", "1.00", minute=3)
    insert_flow(database, case, "C", "A", "1.00", minute=5, payee_name="合成对象")
    insert_flow(database, case, "A", "B", "1.00", minute=6)
    for subject in ("A", "合成对象"):
        params = {"algorithms": ["cycles"], "subjects": [subject], "cycle_max_len": 3}
        result = run(client, case, **params)["cycles"]
        assert result["count"] == 1
        assert result["cycles"][0]["accounts"] == ["B", "C", "A"]
        assert run(client, case, **{**params, "cycle_max_len": 2})["cycles"]["cycles"] == []


def test_cycle_ranking_keeps_best_transaction_representative_before_result_limit(client, database, make_case):
    case = make_case("Synthetic best cycle representative")
    insert_flow(database, case, "A", "B", "100.00", minute=0)
    insert_flow(database, case, "B", "A", "20.00", minute=1)
    insert_flow(database, case, "B", "A", "100.00", minute=2)
    insert_flow(database, case, "C", "D", "50.00", minute=0)
    insert_flow(database, case, "D", "C", "50.00", minute=1)
    result = run(client, case, algorithms=["cycles"], cycle_max=1)["cycles"]
    assert result["count"] == 1
    cycle = result["cycles"][0]
    assert cycle["accounts"] == ["A", "B"]
    assert cycle["return_ratio"] == 1.0 and cycle["last_inflow"] == "100.00"
    assert cycle["end_time"] == "2026-01-01 10:02:00"
    assert result["search"]["complete"] is True
    assert result["search"]["result_limited"] is True and result["truncated"] is True


def test_bounded_path_and_cycle_search_report_partial_negative_results(client, database, make_case, monkeypatch):
    from app import analytics

    case = make_case("Synthetic search receipts")
    insert_flow(database, case, "A", "B", "1.00", minute=0)
    insert_flow(database, case, "B", "C", "1.00", minute=1)
    insert_flow(database, case, "C", "A", "1.00", minute=2)
    monkeypatch.setattr(analytics, "PATH_SEARCH_BUDGET", 1)
    paths = client.post("/api/analysis/paths", json={"case_id": case, "src": "A", "dst": "C"}).json()
    assert paths["count"] == 0 and paths["truncated"] is True
    assert paths["search"]["complete"] is False and paths["search"]["budget_exhausted"] is True
    cycles = analytics.find_cycles(case, budget=1)
    assert cycles["count"] == 0 and cycles["truncated"] is True
    assert cycles["search"]["complete"] is False and cycles["search"]["budget_exhausted"] is True


def test_time_order_changes_neighbor_discovery_and_reports_pruning(client, database, make_case):
    case = make_case("Synthetic time neighbors")
    for minute in range(21):
        insert_flow(database, case, "S", f"N{minute:02d}", f"{minute + 1}.00", minute=minute)
        insert_flow(database, case, f"N{minute:02d}", "D", "1.00", minute=30)
    result = client.post("/api/analysis/paths", json={
        "case_id": case, "src": "S", "dst": "D", "order": "time", "max_paths": 1}).json()
    assert result["paths"][0]["path"][0]["to"] == "N00"
    assert result["search"]["neighbor_pruned"] is True
    assert result["search"]["complete"] is False and result["truncated"] is True
    assert result["search"]["steps"] <= result["search"]["budget"]


def test_search_receipts_aggregate_incomplete_currency_and_start_limits(client, database, make_case, monkeypatch):
    from app import analytics

    case = make_case("Synthetic currency search receipts")
    insert_flow(database, case, "A", "D", "1.00", minute=0)
    insert_flow(database, case, "A", "B", "1.00", minute=0, currency="USD")
    insert_flow(database, case, "B", "D", "1.00", minute=1, currency="USD")
    monkeypatch.setattr(analytics, "PATH_SEARCH_BUDGET", 1)
    result = client.post("/api/analysis/paths", json={"case_id": case, "src": "A", "dst": "D"}).json()
    assert result["count"] == 1 and result["truncated"] is True
    assert result["by_currency"][0]["search"]["complete"] is True
    assert result["by_currency"][1]["search"]["complete"] is False
    assert result["search"]["complete"] is False and result["search"]["budget_exhausted"] is True
    assert result["search"]["steps"] == result["search"]["budget"] == 2

    limited = make_case("Synthetic cycle start limit")
    insert_flow(database, limited, "A", "B", "10.00", minute=0)
    insert_flow(database, limited, "B", "A", "10.00", minute=1)
    insert_flow(database, limited, "C", "D", "1.00", minute=0)
    insert_flow(database, limited, "D", "C", "1.00", minute=1)
    cycles = analytics.find_cycles(limited, start_top=1)
    assert cycles["count"] == 1 and cycles["truncated"] is True
    assert cycles["search"]["start_pruned"] is True and cycles["search"]["complete"] is False


def test_cycle_ranking_uses_exact_money_before_rounding_the_display_ratio(client, database, make_case):
    case = make_case("Synthetic precise cycle ranking")
    insert_flow(database, case, "A", "B", "100000.00", minute=0)
    insert_flow(database, case, "B", "A", "100001.00", minute=1)
    insert_flow(database, case, "C", "D", "200000.00", minute=0)
    insert_flow(database, case, "D", "C", "200004.00", minute=1)
    cycles = run(client, case, algorithms=["cycles"])["cycles"]["cycles"]
    assert [cycle["return_ratio"] for cycle in cycles] == [1.0, 1.0]
    assert [cycle["accounts"] for cycle in cycles] == [["A", "B"], ["C", "D"]]
