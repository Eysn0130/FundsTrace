"""Synthetic source coordinates for scoped local header proposals."""
import csv
from copy import deepcopy
from decimal import Decimal
import io
import json
import threading
import zipfile

from openpyxl import Workbook
import pytest

from app import header_model, header_regions, import_preview_cache, table_archive, table_import
from test_import_lifecycle_api import complete


KNOWN = ["交易日期", "payer_account", "payee_account", "amount", "balance", "unused", "unused_two"]
KNOWN_ROW = ["2026-01-01", "SYN-A", "SYN-B", "2.00", "95.00", "交易", "本笔发生额"]
PARENTS = ["入账", "收款方", "付款方", "账面余额", "本笔发生额", "交易", "本笔发生额复核"]
REVIEW_PARENTS = [PARENTS[0], "划入一方", "划出一方", *PARENTS[3:]]
LEAVES = ["年月日", "账户标识", "账户标识", "元", "分", "年月日", "分"]
ROLES = ["posting_time", "payee_account", "payer_account", "balance", "amount", "transaction_time", "amount"]
NEW_ROW = ["2026-01-20", "SYN-REC", "SYN-PAY", "100.00", "301", "2026-01-07", "301"]
PROFILE = table_import.VOUCHER_REFERENCE_PROFILE


def payload(rows, suffix=".csv"):
    if suffix == ".xlsx":
        book = Workbook()
        book.active.title = "Synthetic private sheet"
        for row in rows:
            book.active.append(row)
        output = io.BytesIO()
        book.save(output)
        book.close()
        return output.getvalue()
    output = io.StringIO(newline="")
    csv.writer(output, delimiter="\t" if suffix == ".tsv" else ",").writerows(rows)
    return output.getvalue().encode()


def archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as container:
        for path, content in entries:
            container.writestr(path, content)
    return output.getvalue()


def proposal(roles, levels=2):
    return {"header_rows": list(range(1, levels + 1)),
            "columns": [{"column": index, "role": role, **({"unit": LEAVES[index - 1]}
                        if role in {"amount", "balance"} else {})} for index, role in enumerate(roles, 1)]}


def reject_call(*args):
    raise AssertionError("unexpected local model request")


def parse(data, suffix, policy):
    return table_import.parse(data, suffix=suffix, profile=PROFILE, _header_policy=policy)


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("levels", [1, 2])
def test_new_section_uses_original_roles_and_validates_the_last_amount(monkeypatch, suffix, levels):
    monkeypatch.setattr(header_model.local_model, "suggest", reject_call)
    section = [PARENTS, LEAVES] if levels == 2 else [[f"{p}/{leaf}" for p, leaf in zip(PARENTS, LEAVES)]]
    data = payload([KNOWN, KNOWN_ROW, *section, *([NEW_ROW] * 127), [*NEW_ROW[:-1], "302"]], suffix)
    policy = header_regions.HeaderRegionPolicy(threading.Event())
    plan = parse(data, suffix, policy)
    assert len(plan["rows"]) == 128
    assert [(row["payer_account"], row["payee_account"], row["amount"], row["balance"], str(row["trade_time"])[:10])
            for row in plan["rows"][1:]] == [("SYN-PAY", "SYN-REC", Decimal("3.01"), Decimal("100.00"), "2026-01-07")] * 127
    assert plan["records"][-1]["reason_code"] == "conflicting_amount"
    assert plan["records"][-1]["source_row"] == 130 + levels
    leaf = 2 + levels
    document = plan["documents"][0]
    assert document["header_rows"] == [1, leaf]
    assert document["header_assistance"] == [{"header_row": leaf, "header_rows": list(range(3, leaf + 1)),
        "status": "verified", "reason": "source_header_verified", "columns": list(range(1, 8))}]
    assert {(item["column_index"], item["unit_factor"], item["candidate_count"], item["column"])
            for item in plan["mapping_evidence"] if item["header_row"] == leaf and item["field"] == "amount"} == {
        (5, "0.01", 2, "分 [列5]" if levels == 2 else "本笔发生额/分"),
        (7, "0.01", 2, "分 [列7]" if levels == 2 else "本笔发生额复核/分")}
    assert {item.get("parent_row") for item in plan["mapping_evidence"] if item["header_row"] == leaf} == ({3} if levels == 2 else {None})
    assert policy.decisions[0]["header_rows"] == list(range(3, leaf + 1))

    replay = header_regions.HeaderRegionPolicy(threading.Event(), replay=deepcopy(policy.decisions))
    assert parse(data, suffix, replay) == plan
    assert replay.decisions == policy.decisions


def test_ambiguous_header_revokes_old_roles_until_an_independent_known_header(monkeypatch):
    swapped = [{"column": 2, "role": "payer_account"}, {"column": 3, "role": "payee_account"}]
    wrong = {"header_rows": [1, 2], "columns": swapped}
    monkeypatch.setattr(header_model.local_model, "suggest", lambda *args: (wrong, wrong))
    data = payload([KNOWN, KNOWN_ROW, REVIEW_PARENTS, LEAVES, NEW_ROW, KNOWN, KNOWN_ROW])
    policy = header_regions.HeaderRegionPolicy(threading.Event())
    plan = parse(data, ".csv", policy)
    assert [row["amount"] for row in plan["rows"]] == [Decimal("2.00")] * 2
    assert [(record["source_row"], record["reason_code"]) for record in plan["records"]] == [
        (2, "transaction_valid"), (3, "ambiguous_region_header"), (4, "ambiguous_region_header"),
        (5, "ambiguous_region_header"), (6, "repeated_header"), (7, "transaction_valid")]
    assert policy.decisions[0]["reason"] == "ambiguous_source_header"
    assert "suggestions" not in policy.decisions[0]
    monkeypatch.setattr(header_model.local_model, "suggest", reject_call)
    replay = header_regions.HeaderRegionPolicy(threading.Event(), replay=deepcopy(policy.decisions))
    assert parse(data, ".csv", replay) == plan
    assert replay.decisions == policy.decisions
    changed = deepcopy(policy.decisions)
    changed[0]["suggestions"] = [{"column_index": 2, "role": "payer_account"}]
    replay = header_regions.HeaderRegionPolicy(threading.Event(), replay=changed)
    assert [row["amount"] for row in parse(data, ".csv", replay)["rows"]] == [Decimal("2.00")] * 2
    assert replay.decisions[0]["reason"] == "invalid_header_replay"
    assert "suggestions" not in replay.decisions[0]


@pytest.mark.parametrize("parent", ["", "内部标签"])
def test_unproved_parent_in_leading_region_retains_records_and_recovers_known_header(monkeypatch, parent):
    monkeypatch.setattr(header_model.local_model, "suggest", reject_call)
    parents = [*PARENTS]
    parents[2] = parent
    policy = header_regions.HeaderRegionPolicy(threading.Event())
    plan = parse(payload([parents, LEAVES, NEW_ROW, KNOWN, KNOWN_ROW]), ".csv", policy)
    assert [row["amount"] for row in plan["rows"]] == [Decimal("2.00")]
    assert [record["source_row"] for record in plan["records"]] == [1, 2, 3, 4, 5]
    assert [record["reason_code"] for record in plan["records"]][:3] == ["ambiguous_region_header"] * 3
    assert policy.decisions[0]["reason"] == "ambiguous_source_header"


def test_nested_members_bind_same_bytes_to_independent_header_sources(monkeypatch):
    monkeypatch.setattr(header_model.local_model, "suggest", reject_call)
    leaf = payload([PARENTS, LEAVES, NEW_ROW], ".tsv")
    book = payload([PARENTS, LEAVES, NEW_ROW], ".xlsx")
    data = archive([(f"{folder}.zip", archive([("internal/very-private.tsv", leaf), ("book.xlsx", book)]))
                    for folder in ("A", "B")])
    policy = header_regions.HeaderRegionPolicy(threading.Event())
    plan = table_archive.parse(io.BytesIO(data), profile=table_archive.BANK_SOURCE_EVIDENCE_PROFILE, _header_policy=policy)
    try:
        assert [row["amount"] for row in plan["rows"]] == [Decimal("3.01")] * 4
        assert [(decision["member_path"], decision["sheet"], decision["header_rows"]) for decision in policy.decisions] == [
            ("A.zip/internal/very-private.tsv", "", [1, 2]), ("A.zip/book.xlsx/worksheets/0001", "Synthetic private sheet", [1, 2]),
            ("B.zip/internal/very-private.tsv", "", [1, 2]), ("B.zip/book.xlsx/worksheets/0001", "Synthetic private sheet", [1, 2])]
        assert len({decision["member_sha256"] for decision in policy.decisions}) == 2
        assert [member["member_path"] for member in plan["members"]] == [decision["member_path"] for decision in policy.decisions]
    finally:
        plan["_details_pool"].close()

    changed = deepcopy(policy.decisions)
    changed[0], changed[2] = changed[2], changed[0]
    replay = header_regions.HeaderRegionPolicy(threading.Event(), replay=changed)
    replayed = table_archive.parse(io.BytesIO(data), profile=table_archive.BANK_SOURCE_EVIDENCE_PROFILE, _header_policy=replay)
    try:
        assert len(replayed["rows"]) == 2
        assert [decision["reason"] for decision in replay.decisions] == [
            "invalid_header_replay", "source_header_verified", "invalid_header_replay", "source_header_verified"]
    finally:
        replayed["_details_pool"].close()


def test_deterministic_schema_and_ordinary_rows_never_call_model(monkeypatch):
    monkeypatch.setattr(header_model.local_model, "suggest", reject_call)
    known = KNOWN + ["extra1", "extra2", "extra3", "extra4"]
    # Header-like values in unrelated extra columns don't transform a dated
    # and quantified ordinary row into a second tier.
    ordinary = KNOWN_ROW + ["年月日", "账户标识", "账户标识", "分"]
    data = payload([known, ordinary, list(reversed(known)), list(reversed(ordinary))])
    policy = header_regions.HeaderRegionPolicy(threading.Event())
    assert parse(data, ".csv", policy) == parse(data, ".csv", None)
    assert policy.decisions == []


def test_generic_date_never_replaces_empty_verified_transaction_date(monkeypatch):
    monkeypatch.setattr(header_model.local_model, "suggest", reject_call)
    headings = [*(f"{parent}/{leaf}" for parent, leaf in zip(PARENTS, LEAVES)), "date"]
    unresolved = [*NEW_ROW, "2026-01-20"]
    unresolved[5] = ""
    policy = header_regions.HeaderRegionPolicy(threading.Event())
    plan = parse(payload([headings, unresolved]), ".csv", policy)
    assert policy.decisions[0]["status"] == "verified"
    assert not plan["rows"]
    assert plan["records"][-1]["reason_code"] == "unsupported_time_role"


def test_missing_parent_row_cannot_reuse_previous_layout(monkeypatch):
    monkeypatch.setattr(header_model.local_model, "suggest", reject_call)
    policy = header_regions.HeaderRegionPolicy(threading.Event())
    plan = parse(payload([KNOWN, KNOWN_ROW, [""] * 7, LEAVES, NEW_ROW]), ".csv", policy)
    assert [row["amount"] for row in plan["rows"]] == [Decimal("2.00")]
    assert [record["reason_code"] for record in plan["records"]][-3:] == [
        "blank_record", "ambiguous_region_header", "ambiguous_region_header"]
    assert policy.decisions[0]["header_rows"] == [4]


def test_words_in_incomplete_transaction_never_revoke_known_region(monkeypatch):
    monkeypatch.setattr(header_model.local_model, "suggest", reject_call)
    policy = header_regions.HeaderRegionPolicy(threading.Event())
    data = payload([KNOWN, KNOWN_ROW, ["", "账面余额", "本笔发生额", "", "", "", ""], KNOWN_ROW])
    plan = parse(data, ".csv", policy)
    assert [row["amount"] for row in plan["rows"]] == [Decimal("2.00"), Decimal("2.00")]
    assert policy.decisions == []


@pytest.mark.parametrize("restore", [False, True], ids=["edited-mapping", "original-mapping-restored"])
def test_public_repreview_preserves_the_frozen_region(client, database, make_case, monkeypatch, restore):
    cid = make_case("Synthetic frozen compound header")
    imports = f"/api/v1/cases/{cid}/imports"
    flows = f"/api/v1/cases/{cid}/flows"
    calls = []

    def suggest(*args):
        calls.append(True)
        reversed_roles = [ROLES[0], ROLES[2], ROLES[1], *ROLES[3:]]
        wrong = proposal(reversed_roles)
        return (wrong, wrong) if restore else None

    def facts(row):
        return (row["trade_time"][:10], row["payer_account"], row["payee_account"],
                row["amount"], row["balance"], row["summary"])

    monkeypatch.setattr(header_model.local_model, "suggest", suggest)
    raw = payload([KNOWN, KNOWN_ROW, PARENTS, LEAVES, NEW_ROW])
    upload = client.post(imports + "/preview", files={"file": ("synthetic.csv", raw)})
    assert upload.status_code == 200, upload.text
    original = upload.json()
    original_mapping = {"mode": original["mode"], "mapping": original["mapping"]}
    regional_facts = ("2026-01-07", "SYN-PAY", "SYN-REC", "3.01", "100.00", "")
    assert facts(original["preview"][-1]) == regional_facts
    assert calls == []
    base = f"{imports}/{original['import_id']}"
    edit = {"mode": original["mode"], "mapping": {**original["mapping"], "summary": "unused"}}
    response = client.post(base + "/preview", json=edit)
    assert response.status_code == 200, response.text
    edited = response.json()
    latest, commit_view = edited, edited
    if restore:
        response = client.post(base + "/preview", json=original_mapping)
        assert response.status_code == 200, response.text
        latest, commit_view = response.json(), original
    # Close the connection to force the issue through the saved import request.
    with database._lock:
        database._con.close()
        database._con = None
    receipt = complete(client, cid, commit_view)
    actual = [facts(row) for row in client.get(flows).json()["items"]]
    assert actual == [regional_facts, ("2026-01-01", "SYN-A", "SYN-B", "2.00", "95.00", "" if restore else "交易")]
    assert facts(edited["preview"][-1]) == regional_facts
    assert edited["preview"][0]["summary"] == "交易"
    assert latest["mapping"] == commit_view["mapping"]
    fingerprint = original["table_summary"]["header_plan_sha256"]
    assert fingerprint and all(item["table_summary"]["header_plan_sha256"] == fingerprint
                               for item in (edited, latest, receipt["result"]))
    assert client.get(base).json() == receipt
    assert calls == []


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("other_column", ["unavailable", "duplicate", "wrong-role", "same-wrong-role", "invalid", "omitted", "matching"])
def test_public_unresolved_header_hints_never_change_funds(client, make_case, monkeypatch, suffix, other_column):
    cid = make_case("Synthetic optional header suggestions")
    base = f"/api/v1/cases/{cid}/imports"
    valid = {"column": 3, "role": "payer_account"}
    wrong = {"column": 3, "role": "payee_account"}
    others = {
        "duplicate": [valid, valid],
        "wrong-role": [wrong],
        "same-wrong-role": [wrong],
        "invalid": [{**valid, "unit": "元"}],
        "omitted": [],
        "matching": [valid],
    }
    suggestions = [] if other_column == "unavailable" else [{"column_index": 2, "role": "payee_account"}]
    if other_column == "matching":
        suggestions.append({"column_index": 3, "role": "payer_account"})
    calls = []

    def suggest(prompt, *_args):
        calls.append(prompt)
        if other_column == "unavailable":
            return None
        payee = {"column": 2, "role": "payee_account"}
        return (
            {"header_rows": [1, 2], "columns": [
                payee, wrong if other_column == "same-wrong-role" else valid,
                {"column": 4, "role": "amount"}, {"column": 8, "role": "payer_account"},
            ]},
            {"header_rows": [1, 2], "columns": [payee, *others[other_column]]},
        )

    monkeypatch.setattr(header_model.local_model, "suggest", suggest)
    data = payload([
        KNOWN + ["note"], KNOWN_ROW + [""],
        REVIEW_PARENTS + ["synthetic private header"], LEAVES + ["账户标识"], NEW_ROW + [""],
    ], suffix)
    response = client.post(base + "/preview", files={"file": ("synthetic-original" + suffix, data)})
    assert response.status_code == 200, response.text
    initial = response.json()
    assert len(calls) == 1
    prompt = json.loads(calls[0].splitlines()[-1])
    assert prompt == {"header_rows": [1, 2], "columns": [
        {"column": 2, "header": ["划入一方", "账户标识"], "candidate_roles": ["payer_account", "payee_account"]},
        {"column": 3, "header": ["划出一方", "账户标识"], "candidate_roles": ["payer_account", "payee_account"]},
    ]}
    assert all(secret not in calls[0] for secret in (
        "synthetic private header", "Synthetic private sheet", "synthetic-original", "SYN-PAY", "SYN-REC", "301"))

    expected = [{"header_row": 4, "header_rows": [3, 4], "status": "review",
                 "reason": "ambiguous_source_header", "columns": [],
                 **({"suggestions": suggestions} if suggestions else {})}]
    url = f"{base}/{initial['import_id']}"
    import_preview_cache.discard()
    monkeypatch.setattr(header_model.local_model, "suggest", reject_call)
    response = client.post(url + "/preview", json={key: initial[key] for key in ("mode", "mapping")})
    assert response.status_code == 200, response.text
    repreview = response.json()
    receipt = complete(client, cid, repreview)["result"]
    for phase in (initial, repreview, receipt):
        table = phase["table_summary"]
        assert table["documents"][0]["header_assistance"] == expected
        assert table["accepted"] == 1 and table["currency_totals"] == {"CNY": "2.00"}
        assert all(item["header_row"] == "1" for item in table["mapping_evidence"])
        assert table["header_plan_sha256"] == initial["table_summary"]["header_plan_sha256"]
    assert [(row["payer_account"], row["payee_account"], row["amount"], row["balance"])
            for row in client.get(f"/api/v1/cases/{cid}/flows").json()["items"]] == [
                ("SYN-A", "SYN-B", "2.00", "95.00")]
    records = client.get(url + "/records").json()["items"]
    assert [(row["source_row"], row["reason_code"]) for row in records][-3:] == [
        (3, "ambiguous_region_header"), (4, "ambiguous_region_header"), (5, "ambiguous_region_header")]


@pytest.mark.parametrize("layout", ["review", "opaque-parent", "verified", "standalone"])
def test_public_native_leaf_alias_respects_same_column_parent(client, make_case, monkeypatch, layout):
    cid = make_case("Synthetic account role from two-tier header")
    base = f"/api/v1/cases/{cid}/imports"
    leaves = ["date", "account", "交易金额", "direction", "元", "对方账号"]
    row = ["2026-01-07", "SYN-A", "3.01", "out", "95.00", "SYN-B"]
    if layout == "verified":
        leaves[-1] = "account"
        parents = ["交易", "付款方", "", "", "账面余额", "收款方"]
    elif layout == "opaque-parent":
        parents = ["", "受益方", "", "", "账面余额", ""]
        row = ["2026-01-07", "SYN-BENEF", "3.01", "出", "95.00", "SYN-COUNTER"]
    else:
        parents = ["交易", "划入一方", "", "", "账面余额", ""]
    calls = []

    def suggest(prompt, *_args):
        calls.append(json.loads(prompt.splitlines()[-1]))
        candidate = {"header_rows": [1, 2], "columns": [
            {"column": 2, "role": "payee_account"}, {"column": 6, "role": "payer_account"},
        ]}
        return candidate, candidate

    monkeypatch.setattr(header_model.local_model, "suggest", suggest if layout == "review" else reject_call)
    data = payload([leaves, row] if layout == "standalone" else [parents, leaves, row])
    response = client.post(base + "/preview", files={"file": ("qualified-leaf.csv", data)})
    assert response.status_code == 200, response.text
    view = response.json()
    receipt = complete(client, cid, view)["result"]
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    for phase in (view, receipt):
        table = phase["table_summary"]
        regions = table["documents"][0].get("header_assistance", [])
        if layout in {"review", "opaque-parent"}:
            assert table["accepted"] == 0
            assert table["reason_counts"] == {"ambiguous_region_header": 3}
            suggestions = ({"suggestions": [{"column_index": 2, "role": "payee_account"}]}
                           if layout == "review" else {})
            assert regions == [{"header_row": 2, "header_rows": [1, 2], "status": "review",
                                "reason": "ambiguous_source_header", "columns": [],
                                **suggestions}]
        else:
            assert table["accepted"] == 1 and table["currency_totals"] == {"CNY": "3.01"}
            assert regions == ([{"header_row": 2, "header_rows": [1, 2], "status": "verified",
                                 "reason": "source_header_verified", "columns": [1, 2, 5, 6]}]
                               if layout == "verified" else [])
    if layout == "review":
        assert flows == []
        assert calls == [{"header_rows": [1, 2], "columns": [
            {"column": 2, "header": ["划入一方", "account"], "candidate_roles": ["payer_account", "payee_account"]}]}]
    elif layout == "opaque-parent":
        assert flows == []
    else:
        assert [(flow["trade_time"][:10], flow["payer_account"], flow["payee_account"], flow["amount"])
                for flow in flows] == [("2026-01-07", "SYN-A", "SYN-B", "3.01")]
        if layout == "verified":
            assert flows[0]["balance"] == "95.00"


def test_one_original_uses_at_most_four_review_pairs(monkeypatch):
    calls = []
    candidate = {"header_rows": [1, 2], "columns": [{"column": 2, "role": "payee_account"}]}

    def suggest(_prompt, _schema, _cancelled, deadline):
        calls.append(deadline)
        return candidate, candidate

    monkeypatch.setattr(header_model.local_model, "suggest", suggest)
    policy = header_regions.HeaderRegionPolicy(threading.Event())
    plan = parse(payload([REVIEW_PARENTS, LEAVES, NEW_ROW] * 6), ".csv", policy)
    assert plan["rows"] == []
    assert len(calls) == 4
    assert [decision["status"] for decision in policy.decisions] == ["review"] * 6
    assert ["suggestions" in decision for decision in policy.decisions] == [True] * 4 + [False] * 2


@pytest.mark.parametrize("parent,leaf,expected", [
    ("账户余额", "交易金额", None),
    ("交易金额(分)", "交易金额(元)", None),
    ("分", "交易金额(元)", None),
    ("交易金额(分)", "交易金额", "1.00"),
    ("分", "交易金额", "1.00"),
    ("交易金额(分)", "交易金额(cents)", "1.00"),
    ("", "交易金额", "100.00"),
    ("栏目", "交易金额", "100.00"),
], ids=["balance-versus-amount", "cents-versus-yuan", "standalone-unit-conflict",
        "unit-above-unspecified", "standalone-unit-above-unspecified", "equivalent-units",
        "missing-parent", "generic-parent"])
def test_public_two_tier_roles_and_units_also_cover_unproposed_columns(client, make_case, monkeypatch, parent, leaf, expected):
    cid = make_case("Synthetic conflicting compound header")
    base = f"/api/v1/cases/{cid}/imports"
    parents = ["交易", "付款方", "收款方", parent, "账面余额"]
    leaves = ["年月日", "账户标识", "账户标识", leaf, "元"]
    monkeypatch.setattr(header_model.local_model, "suggest", reject_call)
    data = payload([parents, leaves, ["2026-01-07", "SYN-PAY", "SYN-REC", "100.00", "95.00"]])
    response = client.post(base + "/preview", files={"file": ("conflict.csv", data)})
    assert response.status_code == 200, response.text
    view = response.json()
    receipt = complete(client, cid, view)["result"]
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    if expected is None:
        assert receipt["ok"] == view["stats"]["ok"] == 0
        assert receipt["table_summary"]["reason_counts"] == {"ambiguous_region_header": 3}
        assert flows == []
        records = client.get(f"{base}/{view['import_id']}/records").json()["items"]
        assert [(row["source_row"], row["disposition"]) for row in records] == [(1, "unresolved"), (2, "unresolved"), (3, "unresolved")]
    else:
        assert receipt["ok"] == view["stats"]["ok"] == 1
        assert [(row["payer_account"], row["payee_account"], row["amount"], row["balance"]) for row in flows] == [
            ("SYN-PAY", "SYN-REC", expected, "95.00")]
        if leaf == "交易金额" and expected == "1.00":
            amount = next(item for item in receipt["table_summary"]["mapping_evidence"] if item["field"] == "amount")
            assert (amount["header_row"], amount["parent_row"], amount["column_index"], amount["unit_factor"]) == (
                "2", "1", "4", "0.01")


def test_explicit_conflict_prevents_review_hint_request(monkeypatch):
    monkeypatch.setattr(header_model.local_model, "suggest", reject_call)
    parents = ["交易", "划出一方", "收款方", "账户余额", "账面余额"]
    leaves = ["年月日", "账户标识", "账户标识", "交易金额", "元"]
    policy = header_regions.HeaderRegionPolicy(threading.Event())
    plan = parse(payload([parents, leaves, ["2026-01-07", "SYN-PAY", "SYN-REC", "100.00", "95.00"]]), ".csv", policy)
    assert plan["rows"] == []
    assert policy.decisions[0]["status"] == "review"
    assert "suggestions" not in policy.decisions[0]


@pytest.mark.parametrize("extra", ["备注2", "2号备注"], ids=["trailing-number", "leading-number"])
@pytest.mark.parametrize("proven", [True, False], ids=["verified", "quarantined"])
def test_public_unrelated_numbered_header_cannot_remove_the_regional_boundary(client, make_case, monkeypatch, proven, extra):
    cid = make_case("Synthetic numbered metadata heading")
    base = f"/api/v1/cases/{cid}/imports"
    monkeypatch.setattr(header_model.local_model, "suggest", reject_call)
    parents = [*PARENTS, "备注"]
    if not proven:
        parents[2] = "内部标签"
    data = payload([KNOWN + ["note"], KNOWN_ROW + [""], parents, LEAVES + [extra], NEW_ROW + [""]])
    response = client.post(base + "/preview", files={"file": ("numbered.csv", data)})
    assert response.status_code == 200, response.text
    view = response.json()
    response = client.post(f"{base}/{view['import_id']}/preview",
                           json={"mode": view["mode"], "mapping": {**view["mapping"], "summary": "note"}})
    assert response.status_code == 200, response.text
    edited = response.json()
    receipt = complete(client, cid, edited)["result"]
    assert receipt["ok"] == view["stats"]["ok"] == edited["stats"]["ok"] == (2 if proven else 1)
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    expected = [("SYN-PAY", "SYN-REC", "3.01"), ("SYN-A", "SYN-B", "2.00")] if proven else [("SYN-A", "SYN-B", "2.00")]
    assert [(row["payer_account"], row["payee_account"], row["amount"]) for row in flows] == expected
    assistance = receipt["table_summary"]["documents"][0]["header_assistance"][0]
    assert assistance["header_rows"] == [3, 4]
    assert assistance["status"] == ("verified" if proven else "review")
    assert "suggestions" not in assistance


@pytest.mark.parametrize("placement", ["first-region", "later-region", "only-region"])
def test_public_sign_declaration_requires_a_single_proven_region(client, make_case, monkeypatch, placement):
    cid = make_case("Synthetic sign convention across regions")
    base = f"/api/v1/cases/{cid}/imports"
    statement = "本文件交易金额正值表示本方支出，绝对值为元；负值表示本方收入"
    known = ["交易时间", "本方账号", "对方账号", "交易金额", "收付标志", "备注", "交易余额"]
    regular = ["2026-01-01", "SYN-A", "SYN-B", "2.00", "出", statement if placement == "first-region" else "", "100.00"]
    parent = ["交易", "本方", "对方", "栏位", "方向", "备注", "账面余额"]
    leaf = ["年月日", "账户标识", "账户标识", "交易金额", "方向", "备注", "元"]
    regional = ["2026-01-07", "SYN-A", "SYN-B", "3.01", "" if placement == "only-region" else "入",
                "" if placement == "first-region" else statement, "103.01"]
    monkeypatch.setattr(header_model.local_model, "suggest", reject_call)
    rows = [parent, leaf, regional] if placement == "only-region" else [known, regular, parent, leaf, regional]
    response = client.post(base + "/preview", files={"file": ("sign.csv", payload(rows))})
    assert response.status_code == 200, response.text
    view = response.json()
    receipt = complete(client, cid, view)["result"]
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    if placement == "only-region":
        assert receipt["ok"] == view["stats"]["ok"] == 1
        assert [(row["payer_account"], row["payee_account"], row["amount"]) for row in flows] == [("SYN-A", "SYN-B", "3.01")]
    else:
        assert receipt["ok"] == view["stats"]["ok"] == 0
        assert receipt["table_summary"]["reason_counts"]["invalid_direction"] == 2
        assert flows == []
