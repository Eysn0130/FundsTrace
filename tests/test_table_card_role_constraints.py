"""Synthetic card/account role evidence must constrain automatic imports."""
import csv
from decimal import Decimal
import io

import pytest

from app import case_packages, table_import as tables
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_contract_lifecycle import facts, source_records
from test_table_mapping_coverage import coverage
from test_table_mapping_versions import profile_preview


def encoded(headers, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue().encode()


def parse(headers, row, *, profile="funds-trace-table-v12"):
    return tables.parse(encoded(headers, [row]), suffix=".csv", profile=profile)


@pytest.mark.parametrize("direction,explicit_side", [("out", "payee_account"), ("in", "payer_account")])
def test_card_owner_does_not_disable_actual_counterparty_account_constraints(direction, explicit_side):
    headers = ["date", "本方卡号", "交易对方账号", explicit_side, "direction", "amount"]
    plan = parse(headers, ["2026-01-01", "SYN-OWN-CARD", "SYN-OTHER-ACCOUNT",
                           "SYN-DIFFERENT-ACCOUNT", direction, "1.23"])
    assert not plan["rows"]
    record = plan["records"][0]
    assert record["disposition"] == "unresolved" and record["reason_code"] == "conflicting_field"


@pytest.mark.parametrize("direction,own_card", [("out", "付款人卡号"), ("in", "收款人卡号")])
def test_explicit_own_party_card_cannot_overwrite_a_conflicting_own_card(direction, own_card):
    headers = ["date", "本方卡号", own_card, "交易对方账号", "direction", "amount"]
    row = ["2026-01-01", "SYN-CARD-A", "SYN-CARD-B", "SYN-OTHER", direction, "1.23"]
    result = parse(headers, row)
    assert not result["rows"] and result["records"][0]["reason_code"] == "conflicting_field"
    row[2] = "SYN-CARD-A"
    assert parse(headers, row)["stats"]["accepted"] == 1


@pytest.mark.parametrize("direction,counter_card", [("out", "收款人卡号"), ("in", "付款人卡号")])
def test_explicit_counterparty_card_cannot_overwrite_a_conflicting_counterparty_card(direction, counter_card):
    headers = ["date", "本方卡号", "交易对方卡号", counter_card, "direction", "amount"]
    row = ["2026-01-01", "SYN-OWN", "SYN-CARD-A", "SYN-CARD-B", direction, "1.23"]
    result = parse(headers, row)
    assert not result["rows"] and result["records"][0]["reason_code"] == "conflicting_field"
    row[3] = "SYN-CARD-A"
    assert parse(headers, row)["stats"]["accepted"] == 1


@pytest.mark.parametrize("direction,side,account_header,card_header", [
    ("out", "payer_account", "付款人账号", card) for card in ("付款人卡号", "付款卡号", "转出卡号")
] + [
    ("in", "payee_account", "收款人账号", card) for card in ("收款人卡号", "收款卡号", "转入卡号")
])
def test_same_party_actual_account_and_card_are_separate_groups_with_actual_priority(
        direction, side, account_header, card_header):
    headers = ["date", "本方账号", "本方卡号", "交易对方账号", account_header, card_header, "direction", "amount"]
    row = ["2026-01-01", "SYN-OWN-ACCOUNT", "SYN-OWN-CARD", "SYN-OTHER",
           "SYN-OWN-ACCOUNT", "SYN-OWN-CARD", direction, "1.23"]
    accepted = parse(headers, row)
    assert accepted["stats"]["accepted"] == 1
    assert accepted["rows"][0][side] == accepted["rows"][0]["self_account"] == "SYN-OWN-ACCOUNT"
    row[headers.index(card_header)] = "SYN-DIFFERENT-CARD"
    rejected = parse(headers, row)
    assert not rejected["rows"] and rejected["records"][0]["reason_code"] == "conflicting_field"


@pytest.mark.parametrize("direction,side,account_header,card_header", [
    ("out", "payee_account", "收款人账号", card) for card in ("收款人卡号", "收款卡号", "转入卡号")
] + [
    ("in", "payer_account", "付款人账号", card) for card in ("付款人卡号", "付款卡号", "转出卡号")
])
def test_counterparty_actual_account_priority_still_checks_the_same_kind_card(
        direction, side, account_header, card_header):
    headers = ["date", "本方卡号", "交易对方账号", "交易对方卡号", account_header, card_header, "direction", "amount"]
    row = ["2026-01-01", "SYN-OWN", "SYN-OTHER-ACCOUNT", "SYN-OTHER-CARD",
           "SYN-OTHER-ACCOUNT", "SYN-OTHER-CARD", direction, "1.23"]
    accepted = parse(headers, row)
    assert accepted["stats"]["accepted"] == 1 and accepted["rows"][0][side] == "SYN-OTHER-ACCOUNT"
    row[headers.index(card_header)] = "SYN-DIFFERENT-CARD"
    rejected = parse(headers, row)
    assert not rejected["rows"] and rejected["records"][0]["reason_code"] == "conflicting_field"


@pytest.mark.parametrize("own_card,accepted", [("SYN-PAYER-CARD", True), ("SYN-PAYEE-CARD", True), ("SYN-NEITHER", False)])
def test_complete_parties_without_direction_compare_cards_only_to_card_evidence(own_card, accepted):
    result = parse(["date", "本方卡号", "付款人账号", "付款人卡号", "收款人账号", "收款人卡号", "amount"],
                   ["2026-01-01", own_card, "SYN-PAYER-ACCOUNT", "SYN-PAYER-CARD",
                    "SYN-PAYEE-ACCOUNT", "SYN-PAYEE-CARD", "1.23"])
    assert result["stats"]["accepted"] == int(accepted)
    if accepted:
        assert (result["rows"][0]["payer_account"], result["rows"][0]["payee_account"], result["rows"][0]["self_account"]) == (
            "SYN-PAYER-ACCOUNT", "SYN-PAYEE-ACCOUNT", own_card)
    else:
        assert result["records"][0]["reason_code"] == "conflicting_field"


@pytest.mark.parametrize("matching_side", ["payer", "payee"])
def test_equal_text_between_an_account_and_party_card_does_not_prove_direction(matching_side):
    payer_card = "SYN-SAME-TEXT" if matching_side == "payer" else "SYN-PAYER-CARD"
    payee_card = "SYN-SAME-TEXT" if matching_side == "payee" else "SYN-PAYEE-CARD"
    result = parse(["date", "本方账号", "交易对方账号", "付款人卡号", "收款人卡号", "amount"],
                   ["2026-01-01", "SYN-SAME-TEXT", "SYN-COUNTER-ACCOUNT", payer_card, payee_card, "1.23"])
    assert result["stats"]["accepted"] == 1
    flow = result["rows"][0]
    assert (flow["payer_account"], flow["payee_account"], flow["self_account"]) == (
        payer_card, payee_card, "SYN-SAME-TEXT")


@pytest.mark.parametrize("direction,own_side,counter_side", [
    ("out", "payer_account", "payee_account"), ("in", "payee_account", "payer_account"),
])
@pytest.mark.parametrize("complete_parties", [False, True])
@pytest.mark.parametrize("blank_account_column", [False, True])
def test_directed_card_fallback_uses_explicit_own_account_without_comparing_different_kinds(
        direction, own_side, counter_side, complete_parties, blank_account_column):
    headers = ["date", "本方卡号", "交易对方账号", own_side, "direction", "amount"]
    row = ["2026-01-01", "SYN-OWN-CARD", "SYN-OTHER-ACCOUNT", "SYN-OWN-ACCOUNT", direction, "1.23"]
    if complete_parties:
        headers.append(counter_side)
        row.append("SYN-OTHER-ACCOUNT")
    if blank_account_column:
        headers.append("本方账号")
        row.append("")
    expected = {own_side: "SYN-OWN-ACCOUNT", counter_side: "SYN-OTHER-ACCOUNT"}
    for reversed_order in (False, True):
        result = parse(list(reversed(headers)) if reversed_order else headers,
                       list(reversed(row)) if reversed_order else row)
        assert result["stats"]["accepted"] == 1
        flow = result["rows"][0]
        assert {field: flow[field] for field in expected} == expected
        assert flow["self_account"] == "SYN-OWN-ACCOUNT" and flow["amount"] == Decimal("1.23")
        assert result["records"][0]["details"]["source_account_column"] == own_side


@pytest.mark.parametrize("direction,own_side,counter_side", [
    ("out", "payer_account", "payee_account"), ("in", "payee_account", "payer_account"),
])
def test_matching_counterparty_redundancy_preserves_card_only_owner(direction, own_side, counter_side):
    headers = ["date", "本方卡号", "交易对方账号", counter_side, "direction", "amount"]
    row = ["2026-01-01", "SYN-CARD", "SYN-OTHER", "SYN-OTHER", direction, "1.23"]
    result = parse(headers, row)
    assert result["stats"]["accepted"] == 1
    assert result["rows"][0][own_side] == result["rows"][0]["self_account"] == "SYN-CARD"
    assert result["rows"][0][counter_side] == "SYN-OTHER"
    assert result["records"][0]["details"]["source_account_column"] == "本方卡号"


@pytest.mark.parametrize("direction,own_side,counter_side", [
    ("out", "payer_account", "payee_account"), ("in", "payee_account", "payer_account"),
])
def test_counterparty_card_and_actual_account_are_independent_kinds(direction, own_side, counter_side):
    headers = ["date", "本方卡号", "交易对方卡号", own_side, counter_side, "direction", "amount"]
    result = parse(headers, ["2026-01-01", "SYN-OWN-CARD", "SYN-OTHER-CARD", "SYN-OWN-ACCOUNT",
                             "SYN-OTHER-ACCOUNT", direction, "1.23"])
    assert result["stats"]["accepted"] == 1
    flow = result["rows"][0]
    assert (flow[own_side], flow[counter_side], flow["self_account"]) == (
        "SYN-OWN-ACCOUNT", "SYN-OTHER-ACCOUNT", "SYN-OWN-ACCOUNT")


@pytest.mark.parametrize("explicit_direction", ["out", "in", ""])
def test_complete_parties_without_actual_owner_account_never_infer_role_from_card_value(explicit_direction):
    headers = ["date", "本方卡号", "payer_account", "payee_account", "direction", "amount"]
    result = parse(headers, ["2026-01-01", "SYN-OWN-CARD", "SYN-PAYER", "SYN-PAYEE", explicit_direction, "1.23"])
    assert result["stats"]["accepted"] == 1
    flow = result["rows"][0]
    assert (flow["payer_account"], flow["payee_account"]) == ("SYN-PAYER", "SYN-PAYEE")
    assert flow["self_account"] == {"out": "SYN-PAYER", "in": "SYN-PAYEE", "": "SYN-OWN-CARD"}[explicit_direction]
    assert result["records"][0]["details"]["source_account_column"] == {
        "out": "payer_account", "in": "payee_account", "": "本方卡号",
    }[explicit_direction]


@pytest.mark.parametrize("direction", ["out", "in", ""])
def test_self_transfer_keeps_explicit_parties_when_card_is_a_different_identifier(direction):
    result = parse(["date", "本方卡号", "payer_account", "payee_account", "direction", "amount"],
                   ["2026-01-01", "SYN-CARD", "SYN-SAME-ACCOUNT", "SYN-SAME-ACCOUNT", direction, "1.23"])
    assert result["stats"]["accepted"] == 1
    flow = result["rows"][0]
    assert flow["payer_account"] == flow["payee_account"] == "SYN-SAME-ACCOUNT"
    assert flow["self_account"] == ("SYN-SAME-ACCOUNT" if direction else "SYN-CARD")


@pytest.mark.parametrize("direction", ["out", "in", ""])
def test_self_transfer_cannot_hide_a_conflicting_actual_counterparty(direction):
    result = parse(["date", "本方卡号", "交易对方账号", "payer_account", "payee_account", "direction", "amount"],
                   ["2026-01-01", "SYN-CARD", "SYN-DIFFERENT", "SYN-SAME-ACCOUNT",
                    "SYN-SAME-ACCOUNT", direction, "1.23"])
    assert not result["rows"]
    assert result["records"][0]["reason_code"] == "conflicting_field"


def test_partial_card_ledger_without_direction_does_not_choose_a_subject_from_party_columns():
    result = parse(["date", "本方卡号", "交易对方账号", "payer_account", "direction", "amount"],
                   ["2026-01-01", "SYN-CARD", "SYN-OTHER", "SYN-OWN-ACCOUNT", "", "1.23"])
    assert not result["rows"] and result["records"][0]["reason_code"] == "missing_direction"


@pytest.mark.parametrize("explicit_account", [False, True])
def test_conflicting_same_card_candidates_cannot_be_suppressed_by_actual_accounts(explicit_account):
    headers = ["date", "本方卡号", "交易卡号", "payer_account", "payee_account", "direction", "amount"]
    row = ["2026-01-01", "SYN-CARD-A", "SYN-CARD-B", "SYN-OWN", "SYN-OTHER", "out", "1.23"]
    if explicit_account:
        headers.append("本方账号")
        row.append("SYN-OWN")
    result = parse(headers, row)
    assert not result["rows"]
    assert result["records"][0]["reason_code"] == "conflicting_field"


def test_conflicting_counterparty_cards_remain_invalid_alongside_actual_accounts():
    result = parse(["date", "本方账号", "交易对方账号", "交易对方卡号", "交易对方卡号",
                    "payer_account", "payee_account", "direction", "amount"],
                   ["2026-01-01", "SYN-OWN", "SYN-OTHER", "SYN-CARD-A", "SYN-CARD-B",
                    "SYN-OWN", "SYN-OTHER", "out", "1.23"])
    assert not result["rows"] and result["records"][0]["reason_code"] == "conflicting_field"


@pytest.mark.parametrize("direction,own_side,counter_side", [
    ("out", "payer_account", "payee_account"), ("in", "payee_account", "payer_account"),
])
def test_equal_redundant_card_and_account_columns_are_order_independent(direction, own_side, counter_side):
    headers = ["date", "本方卡号", "交易卡号", "交易对方账号", "counterparty account", own_side,
               counter_side, "direction", "amount"]
    row = ["2026-01-01", "SYN-CARD", "SYN-CARD", "SYN-OTHER", "SYN-OTHER", "SYN-OWN",
           "SYN-OTHER", direction, "1.23"]
    normal = parse(headers, row)
    reversed_result = parse(list(reversed(headers)), list(reversed(row)))
    assert normal["stats"]["accepted"] == reversed_result["stats"]["accepted"] == 1
    fields = (*tables.FLOW_FIELDS, "self_account", "self_name")
    assert {field: normal["rows"][0][field] for field in fields} == {
        field: reversed_result["rows"][0][field] for field in fields}


@pytest.mark.parametrize("direction,own_side,counter_side", [
    ("out", "payer_account", "payee_account"), ("in", "payee_account", "payer_account"),
])
def test_actual_owner_and_card_difference_does_not_hide_same_account_kind_conflicts(direction, own_side, counter_side):
    headers = ["date", "本方账号", "本方卡号", "交易对方账号", own_side, counter_side, "direction", "amount"]
    row = ["2026-01-01", "SYN-OWN", "SYN-CARD", "SYN-OTHER", "SYN-DIFFERENT-OWN", "SYN-OTHER", direction, "1.23"]
    result = parse(headers, row)
    assert not result["rows"] and result["records"][0]["reason_code"] == "conflicting_direction"
    row[headers.index(own_side)] = "SYN-OWN"
    accepted = parse(headers, row)
    assert accepted["stats"]["accepted"] == 1 and accepted["rows"][0]["self_account"] == "SYN-OWN"


@pytest.mark.parametrize("version", range(5, 12))
def test_frozen_profiles_keep_historical_card_alias_results(version):
    headers = ["date", "本方卡号", "交易对方账号", "payee_account", "direction", "amount"]
    result = parse(headers, ["2026-01-01", "SYN-CARD", "SYN-OTHER", "SYN-DIFFERENT", "out", "1.23"],
                   profile=f"funds-trace-table-v{version}")
    assert result["stats"]["accepted"] == (1 if version == 5 else 0)
    assert result["records"][0]["reason_code"] == ("transaction_valid" if version == 5 else "conflicting_field")
    different_kinds = parse(["date", "本方账号", "本方卡号", "交易对方账号", "direction", "amount"],
                            ["2026-01-01", "SYN-OWN", "SYN-CARD", "SYN-OTHER", "out", "1.23"],
                            profile=f"funds-trace-table-v{version}")
    assert not different_kinds["rows"]
    assert different_kinds["records"][0]["reason_code"] == "conflicting_field"
    mixed_party = parse(["date", "付款人账号", "付款人卡号", "收款人账号", "amount"],
                        ["2026-01-01", "SYN-ACCOUNT", "SYN-CARD", "SYN-OTHER", "1.23"],
                        profile=f"funds-trace-table-v{version}")
    assert not mixed_party["rows"] and mixed_party["records"][0]["reason_code"] == "conflicting_field"


def test_card_role_conflicts_complete_automatically_with_source_report_and_package(client, database, make_case):
    cid = make_case("Synthetic card role constrained import")
    headers = ["date", "本方卡号", "交易对方账号", "payer_account", "付款人卡号", "payee_account", "direction", "amount", "trade_no"]
    data = encoded(headers, [
        ["2026-01-01", "SYN-CARD", "SYN-OTHER", "", "SYN-CARD", "SYN-OTHER", "out", "1.23", "SYN-T1"],
        ["2026-01-02", "SYN-CARD", "SYN-OTHER", "", "SYN-CARD", "SYN-DIFFERENT", "out", "2.34", "SYN-T2"],
        ["2026-01-03", "SYN-CARD", "SYN-OTHER", "SYN-OWN-ACCOUNT", "SYN-CARD", "SYN-OTHER", "out", "3.45", "SYN-T3"],
        ["2026-01-04", "SYN-CARD", "SYN-OTHER", "SYN-OWN-ACCOUNT", "SYN-DIFFERENT-CARD", "SYN-OTHER", "out", "4.56", "SYN-T4"],
    ])
    view = preview(client, cid, data)
    assert view["automatic"] and (view["table_summary"]["accepted"], view["table_summary"]["unresolved"]) == (2, 2)
    job = complete(client, cid, view)
    assert job["stage"] == "done" and (job["result"]["ok"], job["done"], job["total"]) == (2, 4, 4)
    before = facts(database, cid)
    assert [(flow["payer_account"], flow["self_account"], flow["amount"]) for flow in before] == [
        ("SYN-CARD", "SYN-CARD", Decimal("1.23")),
        ("SYN-OWN-ACCOUNT", "SYN-OWN-ACCOUNT", Decimal("3.45")),
    ]
    records = source_records(client, cid, view["import_id"])
    assert [record["disposition"] for record in records] == ["imported", "unresolved", "imported", "unresolved"]
    assert records[1]["reason_code"] == "conflicting_field" and records[1]["flow_id"] is None
    assert records[0]["details"]["source_account_column"] == "本方卡号"
    assert records[2]["details"]["source_account_column"] == "payer_account"
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == data
    repeated = complete(client, cid, preview(client, cid, data, "repeat.csv"))
    assert repeated["stage"] == "done" and (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 2)
    assert facts(database, cid) == before and coverage(client, cid)["unresolved_records"] == 2
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == before and coverage(client, restored) == coverage(client, cid)


def test_pinned_v11_ready_card_facts_remain_original_when_v12_selects_actual_account(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic frozen card source interpretation")
    data = encoded(["date", "本方卡号", "交易对方账号", "payer_account", "direction", "amount"], [
        ["2026-01-01", "SYN-CARD", "SYN-OTHER", "SYN-OWN-ACCOUNT", "out", "1.23"],
    ])
    old = profile_preview(client, cid, data, monkeypatch, "funds-trace-table-v11")
    response = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview", json={
        "mode": "B", "mapping": {**old["mapping"], "payer_account": "本方卡号"},
    })
    assert response.status_code == 200, response.text
    old = response.json()
    current = preview(client, cid, data)
    assert old["table_summary"]["profile"] == "funds-trace-table-v11"
    assert old["table_summary"]["accepted"] == current["table_summary"]["accepted"] == 1
    first = complete(client, cid, old)
    assert first["stage"] == "done" and first["result"]["ok"] == 1
    before = facts(database, cid)
    assert before[0]["payer_account"] == before[0]["self_account"] == "SYN-CARD"
    second = complete(client, cid, current)
    assert second["stage"] == "done" and second["result"]["ok"] == 0
    assert second["result"]["table_summary"]["unresolved"] == 1
    assert facts(database, cid) == before
    record = source_records(client, cid, current["import_id"])[0]
    assert record["details"]["counterparty_basis"] == tables.HISTORY_CONFLICT_BASIS
    assert client.get(f"/api/v1/cases/{cid}/imports/{old['import_id']}").json()["result"] == first["result"]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == before
