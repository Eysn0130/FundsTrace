import json
import threading

import pytest

from app import header_model, header_regions, import_preview_cache, table_import
from test_header_regions import KNOWN, KNOWN_ROW, LEAVES, NEW_ROW, REVIEW_PARENTS, parse, payload, reject_call
from test_import_lifecycle_api import complete


def answer(**votes):
    return {"header_rows": [1, 2], "columns": [
        {"column": int(column.removeprefix("col")), "role": role} for column, role in votes.items()]}


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_public_conflict_samples_only_local_shapes_and_freezes_suggestions(client, make_case, monkeypatch, suffix):
    case = make_case("Synthetic staged header review")
    base = f"/api/v1/cases/{case}"
    calls = []
    expected_payee = "payee_account"
    expected_payer = "payer_account"

    def suggest(prompt, _schema, _cancelled, _deadline):
        calls.append(prompt)
        phase = json.loads(prompt.splitlines()[-1])["phase"]
        if phase == "headings":
            return answer(col2=expected_payee, col3=expected_payer), answer(col2=expected_payee, col3=expected_payee)
        if phase == "recheck":
            return answer(col3=expected_payer), answer(col3=expected_payee)
        return answer(col3=expected_payer), answer(col3=expected_payer)

    monkeypatch.setattr(header_model.local_model, "suggest", suggest)
    leading_zero, alphanumeric = list(NEW_ROW), list(NEW_ROW)
    leading_zero[2], alphanumeric[2] = "0002246801", "SYN-STAGED-32"
    later = ["2026-01-22", "SYN-LATER", "合成收款名称", "5.00", "96.00", "", ""]
    data = payload([KNOWN, KNOWN_ROW, REVIEW_PARENTS, LEAVES, leading_zero, alphanumeric, KNOWN, later], suffix)
    result = client.post(base + "/imports/preview", files={"file": ("synthetic-staged" + suffix, data)})
    assert result.status_code == 200, result.text
    view = result.json()
    requests = [json.loads(prompt.splitlines()[-1]) for prompt in calls]
    assert [item["phase"] for item in requests] == ["headings", "recheck", "shape"]
    assert [[cell["column"] for cell in item["columns"]] for item in requests] == [[2, 3], [3], [3]]
    assert requests[2]["columns"][0]["source_shapes"] == ["alphanumeric", "leading_zero"]
    assert not any("source_shapes" in cell for item in requests[:2] for cell in item["columns"])
    assert all(value not in "".join(calls) for value in (
        "0002246801", "SYN-STAGED-32", "SYN-REC", "合成收款名称", "Synthetic private sheet", "synthetic-staged"))

    decisions = view["table_summary"]["documents"][0]["header_assistance"]
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision["status"] == "review" and decision["columns"] == []
    assert decision["suggestions"] == [
        {"column_index": 2, "role": expected_payee}, {"column_index": 3, "role": expected_payer}]
    assert view["table_summary"]["accepted"] == 2
    assert view["table_summary"]["currency_totals"] == {"CNY": "7.00"}

    import_preview_cache.discard(view["import_id"])
    monkeypatch.setattr(header_model.local_model, "suggest", reject_call)
    monkeypatch.setattr(table_import, "_sample_header_shapes", reject_call)
    receipt = complete(client, case, view)["result"]
    assert receipt["table_summary"]["header_plan_sha256"] == view["table_summary"]["header_plan_sha256"]
    assert receipt["table_summary"]["documents"][0]["header_assistance"] == decisions
    rows = client.get(base + f"/imports/{view['import_id']}/records").json()["items"]
    assert all(row["flow_id"] is None for row in rows if row["source_row"] in (3, 4, 5, 6))
    assert [(row["payer_account"], row["amount"]) for row in client.get(base + "/flows").json()["items"]] == [
        ("SYN-LATER", "5.00"), ("SYN-A", "2.00")]


@pytest.mark.parametrize("scenario,expected_phases,expected_suggestions", [
    ("matched", ["headings"], [2, 3]),
    ("initial-unavailable", ["headings"], []),
    ("rechecked", ["headings", "recheck"], [2, 3]),
    ("unavailable", ["headings", "recheck"], [2]),
    ("invalid-envelope", ["headings", "recheck"], [2]),
    ("wrong-agreement", ["headings", "recheck", "shape"], [2]),
    ("cancelled", ["headings", "recheck"], []),
])
def test_only_source_supported_pairs_are_kept(monkeypatch, scenario, expected_phases, expected_suggestions):
    phases = []
    cancelled = threading.Event()
    valid = answer(col2="payee_account", col3="payer_account")
    wrong = answer(col2="payee_account", col3="payee_account")
    right3, wrong3 = answer(col3="payer_account"), answer(col3="payee_account")

    def suggest(prompt, *_args):
        phase = json.loads(prompt.splitlines()[-1])["phase"]
        phases.append(phase)
        if phase == "headings":
            if scenario == "initial-unavailable":
                return None
            return valid, valid if scenario == "matched" else wrong
        if phase == "recheck":
            if scenario == "unavailable":
                return None
            if scenario == "invalid-envelope":
                return right3, {**right3, "header_rows": [1, 3]}
            if scenario == "cancelled":
                cancelled.set()
            return right3, right3 if scenario in ("rechecked", "cancelled") else wrong3
        return wrong3, wrong3

    monkeypatch.setattr(header_model.local_model, "suggest", suggest)
    if "shape" not in expected_phases:
        monkeypatch.setattr(table_import, "_sample_header_shapes", reject_call)
    policy = header_regions.HeaderRegionPolicy(cancelled)
    plan = parse(payload([REVIEW_PARENTS, LEAVES, NEW_ROW]), ".csv", policy)
    assert phases == expected_phases
    assert len(policy.decisions) == 1 and plan["rows"] == []
    assert [item["column_index"] for item in policy.decisions[0].get("suggestions", [])] == expected_suggestions


def test_sample_cannot_cross_a_row_with_unavailable_financial_anchors(monkeypatch):
    phases = []

    def disagree(prompt, *_args):
        phases.append(json.loads(prompt.splitlines()[-1])["phase"])
        return answer(col3="payer_account"), answer(col3="payee_account")

    monkeypatch.setattr(header_model.local_model, "suggest", disagree)
    broken_row = [None, "SYN-AMBIG-1", "SYN-AMBIG-2", None, None, None, None]
    policy = header_regions.HeaderRegionPolicy(threading.Event())
    assert parse(payload([REVIEW_PARENTS, LEAVES, broken_row, NEW_ROW]), ".csv", policy)["rows"] == []
    assert phases == ["headings", "recheck"]


def test_shared_request_budget_limits_sampling_and_only_the_first_24_rows_contribute(monkeypatch):
    calls = []
    deadlines = []

    def disagree(prompt, _schema, _cancelled, deadline):
        deadlines.append(deadline)
        calls.append(json.loads(prompt.splitlines()[-1]))
        return answer(col3="payer_account"), answer(col3="payee_account")

    monkeypatch.setattr(header_model.local_model, "suggest", disagree)
    early = list(NEW_ROW)
    early[2] = "0123456"
    late = list(NEW_ROW)
    late[2] = "SYNTHETICLETTERS"
    rows = [REVIEW_PARENTS, LEAVES, *([early] * 24), late, REVIEW_PARENTS, LEAVES, late]
    policy = header_regions.HeaderRegionPolicy(threading.Event())
    parse(payload(rows), ".csv", policy)
    assert [item["phase"] for item in calls] == ["headings", "recheck", "shape", "headings"]
    assert calls[2]["columns"][1]["source_shapes"] == ["leading_zero"]
    assert all(deadline <= policy._deadline for deadline in deadlines)
