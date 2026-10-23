"""Synthetic uniform transaction contracts bind query account/card by kind."""
from decimal import Decimal

import pytest

from app import table_import as tables
from app.table_source_roles import classify
from test_table_source_role_lifecycle import csv_bytes, transaction, xlsx_bytes
from test_table_source_roles import TRANSACTION_EXPLICIT, TRANSACTION_QUERY_ONLY


def query_row(**changes):
    return transaction(**{
        "查询帐号": "SYN-OWN", "查询卡号": "SYN-CARD",
        "交易对方账卡号": "SYN-OTHER", **changes,
    })


def parse(headers, rows, suffix=".csv", **options):
    data = (csv_bytes(headers, rows) if suffix == ".csv"
            else xlsx_bytes([("Synthetic uniform transactions", headers, rows)]))
    return tables.parse(data, suffix=suffix, **options)


@pytest.mark.parametrize("annotations", [False, True])
def test_complete_uniform_query_template_automatically_binds_owner(annotations):
    headers = list(TRANSACTION_QUERY_ONLY)
    if annotations:
        headers[:2] = ["企业名称(查询条件)", "合成证照(查询条件)"]
    result = parse(headers, [query_row()])
    assert result["stats"]["accepted"] == 1
    assert result["profile"] == "funds-trace-table-v17"
    flow = result["rows"][0]
    assert (flow["payer_account"], flow["payee_account"], flow["amount"]) == (
        "SYN-OWN", "SYN-OTHER", Decimal("12.34"))
    assert flow["payer_name"] == ""
    assert result["mapping"]["self_account"] == "查询帐号"
    assert result["mapping"]["self_card"] == "查询卡号"
    assert result["records"][0]["details"]["source_account_column"] == "查询帐号"


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("direction,own_side", [("借", "payer"), ("贷", "payee")])
@pytest.mark.parametrize("account,card,expected,column", [
    ("SYN-ACCOUNT", "SYN-DIFFERENT-CARD", "SYN-ACCOUNT", "查询帐号"),
    ("", "SYN-CARD-ONLY", "SYN-CARD-ONLY", "查询卡号"),
    ("SYN-ACCOUNT-ONLY", "", "SYN-ACCOUNT-ONLY", "查询帐号"),
])
def test_owner_kind_direction_and_fallback_survive_automatic_repreview(
        suffix, reverse, direction, own_side, account, card, expected, column):
    headers = list(reversed(TRANSACTION_QUERY_ONLY)) if reverse else TRANSACTION_QUERY_ONLY
    rows = [query_row(**{"查询帐号": account, "查询卡号": card, "借贷标志": direction})]
    # Repreview uses identical uploaded bytes. Regenerating an XLSX can change
    # ZIP timestamps and correctly produce a different physical source key.
    data = (csv_bytes(headers, rows) if suffix == ".csv"
            else xlsx_bytes([("Synthetic uniform transactions", headers, rows)]))
    first = tables.parse(data, suffix=suffix)
    second = tables.parse(data, suffix=suffix, mode=first["mode"], mapping=first["mapping"])
    assert second["stats"]["accepted"] == first["stats"]["accepted"] == 1
    assert second["rows"] == first["rows"]
    flow = second["rows"][0]
    assert flow["self_account"] == flow[own_side + "_account"] == expected
    assert flow[("payee" if own_side == "payer" else "payer") + "_account"] == "SYN-OTHER"
    assert second["records"][0]["details"]["source_account_column"] == column


@pytest.mark.parametrize("same_account,same_card", [(True, True), (False, True), (True, False)])
@pytest.mark.parametrize("override", [None, "self_account", "self_card"])
def test_complete_explicit_template_validates_same_kind_query_roles(same_account, same_card, override):
    row = query_row(**{"查询帐号": "SYN-OWN" if same_account else "SYN-CONFLICT-ACCOUNT",
                       "查询卡号": "SYN-CARD" if same_card else "SYN-CONFLICT-CARD"})
    mapping = None if override is None else {override: "本方账号" if override == "self_account" else "本方卡号"}
    result = parse(TRANSACTION_EXPLICIT, [row], mapping=mapping)
    assert result["stats"]["accepted"] == int(same_account and same_card)
    if same_account and same_card:
        assert result["rows"][0]["self_account"] == "SYN-OWN"
    else:
        assert result["records"][0]["reason_code"] == "conflicting_field"
        assert result["records"][0]["details"]["financial_effect"] == "undetermined"


@pytest.mark.parametrize("populated,expected", [
    ({"本方账号": "SYN-ACCOUNT"}, "本方账号"),
    ({"查询帐号": "SYN-ACCOUNT"}, "查询帐号"),
    ({"本方卡号": "SYN-CARD"}, "本方卡号"),
    ({"查询卡号": "SYN-CARD"}, "查询卡号"),
])
def test_complete_explicit_template_records_the_actual_nonempty_owner_column(populated, expected):
    row = query_row(**{"本方账号": "", "查询帐号": "", "本方卡号": "", "查询卡号": "", **populated})
    result = parse(TRANSACTION_EXPLICIT, [row])
    assert result["stats"]["accepted"] == 1
    assert result["rows"][0]["self_account"] == populated[expected]
    assert result["records"][0]["details"]["source_account_column"] == expected


@pytest.mark.parametrize("annotation", ["交易金额(查询条件)", "本方账号(查询条件)", "交易时间(查询条件)"])
def test_query_annotations_never_become_transaction_candidates(annotation):
    headers = [annotation, "合成证照(查询条件)", *TRANSACTION_QUERY_ONLY[2:]]
    row = query_row(**{annotation: "100", "合成证照(查询条件)": "SYN-QUERY-METADATA"})
    result = parse(headers, [row])
    assert result["stats"]["accepted"] == 1
    assert result["rows"][0]["amount"] == Decimal("12.34")
    assert result["rows"][0]["self_name"] == ""
    assert not any(item["column"] in headers[:2] for item in result["mapping_evidence"])
    with pytest.raises(tables.TableImportError, match="查询条件注记"):
        parse(headers, [row], mapping={"self_name": annotation})


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "interior_empty", "partial"])
def test_incomplete_or_generic_query_headers_confer_no_owner(mutation):
    headers = list(TRANSACTION_QUERY_ONLY)
    if mutation == "missing":
        headers.remove("任务流水号")
    elif mutation == "extra":
        headers.append("额外来源字段")
    elif mutation == "duplicate":
        headers.append("查询帐号")
    elif mutation == "interior_empty":
        headers.insert(3, "")
    else:
        headers = ["交易时间", "交易金额", "借贷标志", "查询帐号", "查询卡号"]
    assert classify(headers, query_conditions=True) is None
    result = parse(headers, [query_row()])
    assert not result["rows"] and "self_account" not in result["mapping"]
    assert result["records"][0]["reason_code"] == "unknown_table"


@pytest.mark.parametrize("labels", [
    ["(查询条件)", "合成证照(查询条件)"],
    ["合成名称(查询条件)尾部", "合成证照(查询条件)"],
    ["合成名称(查询条件)(查询条件)", "合成证照(查询条件)"],
    ["合成名称(查询条件)", " 合成名称（查询条件） "],
    ["合成\u200b名称(查询条件)", "合成证照(查询条件)"],
])
def test_malformed_query_annotations_do_not_complete_the_contract(labels):
    headers = [*labels, *TRANSACTION_QUERY_ONLY[2:]]
    assert classify(headers, query_conditions=True) is None
    assert not parse(headers, [query_row()])["rows"]


def test_missing_owner_and_invalid_financial_rows_are_retained_with_effect():
    rows = [query_row(**{"查询帐号": "", "查询卡号": ""}),
            query_row(**{"交易金额": ""}), query_row(**{"交易时间": ""}),
            query_row(**{"借贷标志": ""}), query_row(**{"交易是否成功": "02"}),
            query_row(**{"交易金额": "-12.34"})]
    result = parse(TRANSACTION_QUERY_ONLY, rows)
    assert result["stats"]["unresolved"] == 6 and not result["rows"]
    assert [item["reason_code"] for item in result["records"]] == [
        "missing_identity", "missing_amount", "missing_time", "missing_direction", "unknown_status", "negative_amount"]
    missing = result["records"][0]["details"]
    assert "查询帐号" in missing["reason"] and "关联分析可能不完整" in missing["reason"]
    assert all(item["details"]["financial_effect"] == "undetermined" for item in result["records"])


@pytest.mark.parametrize("field,column", [
    ("self_account", "交易对方账卡号"), ("self_card", "交易对方账卡号"),
    ("counter_account", "查询帐号"), ("self_card", "查询帐号"),
])
def test_mapping_cannot_turn_a_counterparty_into_the_missing_owner(field, column):
    rows = [query_row(**{"查询帐号": "", "查询卡号": ""})]
    result = parse(TRANSACTION_QUERY_ONLY, rows)
    assert result["records"][0]["reason_code"] == "missing_identity"
    with pytest.raises(tables.TableImportError, match="本方映射"):
        parse(TRANSACTION_QUERY_ONLY, rows, mapping={**result["mapping"], field: column})


@pytest.mark.parametrize("profile", ["funds-trace-table-v12", "funds-trace-table-v13"])
def test_historical_profiles_keep_query_conditions_unbound(profile):
    original = parse(TRANSACTION_QUERY_ONLY, [query_row()], profile=profile)
    assert original["records"][0]["reason_code"] == "missing_identity" and not original["rows"]
    assert "self_account" not in original["mapping"]
    variant = ["企业名称(查询条件)", "合成证照(查询条件)", *TRANSACTION_QUERY_ONLY[2:]]
    assert classify(variant) is None
    assert parse(variant, [query_row()], profile=profile)["records"][0]["reason_code"] == "unknown_table"
    explicit = parse(TRANSACTION_EXPLICIT, [transaction()], profile=profile)
    assert explicit["stats"]["accepted"] == 1 and explicit["rows"][0]["self_account"] == "SYN-OWN"
