"""Legacy analysis behavior through synthetic CSV imports and public reports."""
import csv
import io
import json
import re
import time
from decimal import Decimal

import pytest

from app import analytics
from test_folder_evidence import assert_unresolved_funds


def import_csv(client, case_id, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(("流水号", "交易时间", "付款人账号", "付款人名称", "收款人账号",
                     "收款人名称", "交易金额", "币种"))
    for index, row in enumerate(rows):
        payer, payee, amount, currency, payer_name, payee_name = row
        writer.writerow((f"SYN-{index}", f"2026-01-01 10:{index:02d}:00", payer,
                         payer_name, payee, payee_name, amount, currency))
    response = client.post(f"/api/v1/cases/{case_id}/imports/preview",
                           files={"file": ("analysis-synthetic.csv", buffer.getvalue().encode())})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["stats"]["ok"] == len(rows)
    base = f"/api/v1/cases/{case_id}/imports/{preview['import_id']}"
    response = client.post(base + "/commit", json={
        "mode": preview["mode"], "mapping": preview["mapping"], "mark_accounts": [],
    })
    assert response.status_code == 200, response.text
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(base)
        assert response.status_code == 200, response.text
        job = response.json()
        if job["stage"] in ("done", "error"):
            assert job["stage"] == "done", job
            assert job["result"]["ok"] == len(rows)
            return
        time.sleep(.01)
    raise AssertionError("Synthetic analysis CSV import timed out")


def analyze(client, case_id, endpoint, **params):
    response = client.post(f"/api/analysis/{endpoint}", json={"case_id": case_id, "params": params})
    assert response.status_code == 200, response.text
    if endpoint == "run":
        return response.json()
    encoded = re.search(r'<script type="application/json" id="funds-trace-report-data">(.*?)</script>',
                        response.text, re.S)
    assert encoded is not None
    return json.loads(encoded.group(1))["results"]


@pytest.mark.parametrize("endpoint", ["run", "report"])
@pytest.mark.parametrize("subject", ["A", "Synthetic A"])
def test_cycle_subject_limits_starts_but_retains_intermediate_accounts(client, make_case, endpoint, subject):
    case_id, other_id = make_case("Subject cycles"), make_case("Other subject cycles")
    import_csv(client, case_id, [
        ("A", "B", "1000.01", "CNY", "Synthetic A", "Synthetic B"),
        ("B", "C", "0.02", "CNY", "Synthetic B", "Synthetic C"),
        ("C", "A", "0.03", "CNY", "Synthetic C", "Synthetic A"),
        ("A", "B", "9.99", "USD", "Synthetic A", "Synthetic B"),
    ])
    import_csv(client, other_id, [
        ("B", "A", "9000.00", "USD", "Synthetic B", "Synthetic A"),
    ])
    params = {"algorithms": ["cycles"], "subjects": [subject], "cycle_min_total": "1000.06"}
    result = analyze(client, case_id, endpoint, **params)["cycles"]
    assert result["count"] == 1
    cycle = result["cycles"][0]
    assert cycle["accounts"] == ["A", "B", "C"]
    assert (cycle["total"], cycle["currency"], cycle["len"]) == (None, "CNY", 3)
    assert_unresolved_funds(cycle["funds"], 3, [("CNY", "1000.06")])
    assert {edge["from"] for edge in cycle["edges"]} == {"A", "B", "C"}
    assert {edge["amount"] for edge in cycle["edges"]} == {None}
    assert {edge["observation_amount"] for edge in cycle["edges"]} == {"1000.01", "0.02", "0.03"}
    for edge in cycle["edges"]:
        assert edge["granularity"] == "unresolved_observation"
        assert_unresolved_funds(edge["funds"], 1, [("CNY", edge["observation_amount"])])
    for override in ({"cycle_min_total": "1000.07"}, {"cycle_max_len": 2},
                     {"subjects": ["Unknown subject"]}):
        assert analyze(client, case_id, endpoint, **{**params, **override})["cycles"]["cycles"] == []


def test_cycle_start_limit_does_not_prune_intermediates_or_explicit_subjects(client, make_case):
    case_id = make_case("Cycle performance guards")
    import_csv(client, case_id, [
        ("A", "B", "100.01", "CNY", "A", "B"),
        ("B", "C", "0.02", "CNY", "B", "C"),
        ("C", "A", "0.03", "CNY", "C", "A"),
        ("D", "E", "100.04", "CNY", "D", "E"),
        ("E", "D", "0.05", "CNY", "E", "D"),
    ])
    largest_start = analytics.find_cycles(case_id, start_top=1)
    assert largest_start["count"] == 1
    assert largest_start["cycles"][0]["accounts"] == ["D", "E"]
    explicit = analytics.find_cycles(case_id, subjects=["A", "D"], start_top=1)
    assert {tuple(cycle["accounts"]) for cycle in explicit["cycles"]} == {("A", "B", "C"), ("D", "E")}
    assert analytics.find_cycles(case_id, subjects=["A", "D"], start_top=1, max_cycles=1)["count"] == 1
    assert analytics.find_cycles(case_id, subjects=["A"], budget=0)["count"] == 0
    assert analytics.find_cycles(case_id, window_days=0)["count"] == 0
    assert analytics.find_cycles(case_id, subjects=["A"], min_total=Decimal("100.07"))["count"] == 0


@pytest.mark.parametrize("endpoint", ["run", "report"])
def test_outlier_name_resolves_all_accounts_before_currency_and_alias_aggregation(client, make_case, endpoint):
    case_id, other_id = make_case("Named outlier subjects"), make_case("Other named subjects")
    rows = []
    for index in range(6):
        account = f"P{index}"
        amounts = ("1000.01", "2000.02", "800.04", "900.05") if index == 0 else ("1.01", "2.02", "4.04", "5.05")
        # The exact shared name appears only once, in CNY. Other rows retain an
        # old alias or no name, including every USD row for these same accounts.
        for amount, currency, name in zip(amounts, ("CNY", "CNY", "USD", "USD"),
                                          ("Synthetic shared owner", f"Old alias {index}", "", "")):
            rows.append(("SOURCE", account, amount, currency, "Synthetic source", name))
    import_csv(client, case_id, rows)
    import_csv(client, other_id, [
        ("SOURCE", "P0", "999999.99", "CNY", "Synthetic source", "Synthetic shared owner"),
    ])
    params = {"algorithms": ["outliers"], "out_sigma": 1.5}
    expected = analyze(client, case_id, endpoint, **params, subjects=[f"P{i}" for i in range(6)])["outliers"]
    named = analyze(client, case_id, endpoint, **params, subjects=["Synthetic shared owner"])["outliers"]
    assert named == expected
    assert [(row["account"], row["currency"], row["dim"], row["observation_value"])
            for row in named["outliers"]] == [
        ("P0", "CNY", "total", "3000.03"), ("P0", "USD", "total", "1700.09"),
    ]
    for row in named["outliers"]:
        assert row["value"] is None and row["metric_basis"] == "observation_clue"
        assert_unresolved_funds(row["funds"], 2, [(row["currency"], row["observation_value"])])
    for unknown in ("Unknown owner", "Synthetic shared"):
        assert analyze(client, case_id, endpoint, **params, subjects=[unknown])["outliers"]["outliers"] == []


@pytest.mark.parametrize("endpoint", ["run", "report"])
def test_fast_flow_self_transfer_cannot_pair_with_its_own_source_id(client, make_case, endpoint):
    case_id, other_id = make_case("Self transfer pairing"), make_case("Other transfer pairing")
    import_csv(client, case_id, [
        ("SELF", "SELF", "1000.01", "CNY", "Synthetic self", "Synthetic self"),
        ("IN", "HUB", "2000.02", "CNY", "Synthetic in", "Synthetic hub"),
        ("HUB", "OUT", "2000.02", "CNY", "Synthetic hub", "Synthetic out"),
        ("SELF", "OTHER", "1000.01", "USD", "Synthetic self", "Synthetic other"),
    ])
    import_csv(client, other_id, [
        ("SELF", "OTHER", "1000.01", "CNY", "Synthetic self", "Synthetic other"),
    ])
    params = {"algorithms": ["fast_flows"], "ff_min_amount": "0.01", "ff_tolerance": 0}
    result = analyze(client, case_id, endpoint, **params)["fast_flows"]
    assert result["count"] == 1
    pair = result["pairs"][0]
    assert (pair["account"], pair["currency"], pair["in_flow"]["amount"], pair["out_total"], pair["diff"]) == (
        "HUB", "CNY", None, None, None)
    assert pair["in_flow"]["observation_amount"] == "2000.02"
    assert [row["observation_amount"] for row in pair["out_flows"]] == ["2000.02"]
    assert_unresolved_funds(pair["in_funds"], 1, [("CNY", "2000.02")])
    assert_unresolved_funds(pair["out_funds"], 1, [("CNY", "2000.02")])
    assert_unresolved_funds(pair["funds"], 2, [("CNY", "4000.04")])
    assert all(pair["in_flow"]["id"] != out["id"] for out in pair["out_flows"])
    assert analyze(client, case_id, endpoint, **params, subjects=["SELF"])["fast_flows"]["pairs"] == []
    assert analyze(client, case_id, endpoint, **params, ff_max_pairs=0)["fast_flows"]["pairs"] == []
    flows = client.get(f"/api/v1/cases/{case_id}/flows").json()["items"]
    assert len(flows) == 4
    assert len([row for row in flows if row["payer_account"] == row["payee_account"] == "SELF"]) == 1
