"""Synthetic regressions for the opt-in, bounded bank reference semantics."""
import csv
from decimal import Decimal
import io

import pytest

from app import table_import as tables, table_contracts


POLICY = "transaction-reference-v1"
COLUMNS = tuple("交易卡号 交易账号 查询反馈结果原因 交易户名 交易证件号码 交易时间 交易金额 交易余额 收付标志 交易对手账卡号 现金标志 对手户名 对手身份证号 对手开户银行 摘要说明 交易币种 交易网点名称 交易发生地 交易是否成功 传票号 IP地址 MAC地址 对手交易余额 交易流水号 日志号 凭证种类 凭证号 交易柜员号 备注 商户名称 商户代码 交易类型".split())


def source_row(**changes):
    values = {
        "交易卡号": "SYN-CARD", "交易账号": "SYN-OWN", "交易户名": "Synthetic own",
        "交易时间": "2026-01-02 03:04:05", "交易金额": "12.34", "交易余额": "123.45",
        "收付标志": "收入", "交易对手账卡号": "SYN-COUNTER", "对手户名": "Synthetic counter",
        "对手开户银行": "Synthetic bank", "摘要说明": "Synthetic transfer", "交易币种": "CNY",
        "交易是否成功": "成功", "交易流水号": "SYN-TRANSACTION", "凭证号": "SYN-VOUCHER",
    }
    values.update(changes)
    return values


def payload(rows, headers=COLUMNS):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows([[row.get(column, "") for column in headers] for row in rows])
    return output.getvalue().encode("utf-8")


def parse(rows, headers=COLUMNS, **options):
    return tables.parse(payload(rows, headers), suffix=".csv", **options)


def test_independent_voucher_does_not_conflict_with_source_transaction_id():
    data = payload([source_row()])
    # Pin the historical baseline rather than following the process default.
    default = tables.parse(data, suffix=".csv", profile="funds-trace-table-v5")
    assert default["profile"] == "funds-trace-table-v5"
    assert default["records"][0]["reason_code"] == "conflicting_field"
    assert not default["rows"]

    policy = tables.parse(data, suffix=".csv", reference_policy=POLICY,
                          profile="funds-trace-table-v5")
    assert policy["rows"][0]["trade_no"] == "SYN-TRANSACTION"
    assert policy["rows"][0]["amount"] == Decimal("12.34")
    details = policy["records"][0]["details"]
    assert details["trade_no_basis"] == "source_transaction_id"
    assert details.keys() <= tables.DETAILS_FIELDS | tables.REFERENCE_DETAILS_FIELDS
    assert "trade_no_basis" not in tables.DETAILS_FIELDS
    assert "SYN-TRANSACTION" not in str(details)
    assert "SYN-VOUCHER" not in str(details)
    assert policy["records"][0]["record_key"] == default["records"][0]["record_key"]
    assert tables.TRANSACTION_REFERENCE_POLICY == POLICY
    assert tables.BANK_FEEDBACK_32_COLUMNS == frozenset(COLUMNS)

    evidence = {entry["column"]: entry for entry in policy["mapping_evidence"]}
    assert evidence["交易流水号"]["field"] == "trade_no"
    assert evidence["交易流水号"]["basis"] == "source_transaction_id"
    assert evidence["凭证号"]["field"] == "voucher_reference"
    assert evidence["凭证号"]["basis"] == "independent_voucher_reference"
    assert evidence["交易流水号"]["candidate_count"] == evidence["凭证号"]["candidate_count"] == 1


@pytest.mark.parametrize("transaction,voucher,expected,basis", [
    ("SYN-MAIN", "", "SYN-MAIN", "source_transaction_id"),
    ("", "SYN-LEGACY", "SYN-LEGACY", "legacy_voucher_fallback"),
    ("SYN-SAME", "SYN-SAME", "SYN-SAME", "source_transaction_id"),
    ("", "", "", "none"),
])
def test_every_previously_accepted_reference_combination_preserves_all_facts(transaction, voucher, expected, basis):
    rows = [source_row(**{"交易流水号": transaction, "凭证号": voucher})]
    default, policy = parse(rows), parse(rows, reference_policy=POLICY)
    assert len(default["rows"]) == 1
    assert policy["rows"] == default["rows"]
    assert policy["rows"][0]["trade_no"] == expected
    assert policy["records"][0]["details"]["trade_no_basis"] == basis
    without_basis = {key: value for key, value in policy["records"][0]["details"].items()
                     if key not in table_contracts.DETAILS_FIELDS | {"trade_no_basis"}}
    old_details = {key: value for key, value in default["records"][0]["details"].items()
                   if key not in table_contracts.DETAILS_FIELDS}
    assert without_basis == old_details
    for plan in (default, policy):
        assert table_contracts.validate_details(plan["records"][0]["details"], "accepted", "transaction_valid")
    # Reference roles are intentionally part of the v8 layout contract, while
    # the established thirteen transaction facts and physical identity agree.
    assert policy["records"][0]["details"]["contract_sha256"] != default["records"][0]["details"]["contract_sha256"]
    assert policy["records"][0]["record_key"] == default["records"][0]["record_key"]


def test_reordering_complete_schema_keeps_primary_reference_and_financial_facts():
    normal = parse([source_row()], reference_policy=POLICY)
    reordered = parse([source_row()], tuple(reversed(COLUMNS)), reference_policy=POLICY)
    for field in tables.FLOW_FIELDS:
        assert normal["rows"][0][field] == reordered["rows"][0][field]
    evidence = {entry["column"]: entry for entry in reordered["mapping_evidence"]}
    assert evidence["交易流水号"]["column_index"] == tuple(reversed(COLUMNS)).index("交易流水号") + 1


@pytest.mark.parametrize("headers", [
    COLUMNS[:-1],
    COLUMNS + ("unknown",),
    tuple("unknown" if column == "商户代码" else column for column in COLUMNS),
    tuple("交易流水号" if column == "商户代码" else column for column in COLUMNS),
    tuple("凭证号" if column == "商户代码" else column for column in COLUMNS),
])
def test_incomplete_unknown_and_duplicate_schemas_keep_default_semantics(headers):
    default = parse([source_row()], headers)
    policy = parse([source_row()], headers, reference_policy=POLICY)
    assert policy == default
    assert not policy["rows"]
    assert policy["records"][0]["reason_code"] == "conflicting_field"


def test_policy_is_rechecked_at_every_region_boundary():
    partial = tuple(column for column in COLUMNS if column != "商户代码")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    values = source_row()
    writer.writerows([
        COLUMNS, [values.get(column, "") for column in COLUMNS],
        partial, [values.get(column, "") for column in partial],
        tuple(reversed(COLUMNS)), [values.get(column, "") for column in reversed(COLUMNS)],
    ])
    data = output.getvalue().encode("utf-8")
    default = tables.parse(data, suffix=".csv")
    policy = tables.parse(data, suffix=".csv", reference_policy=POLICY)
    assert [record["disposition"] for record in policy["records"]] == [
        "accepted", "non_transaction", "unresolved", "non_transaction", "accepted",
    ]
    assert "trade_no_basis" not in policy["records"][2]["details"]
    assert [record["record_key"] for record in policy["records"]] == [record["record_key"] for record in default["records"]]
    assert [row["trade_no"] for row in policy["rows"]] == ["SYN-TRANSACTION", "SYN-TRANSACTION"]


@pytest.mark.parametrize("column,value,issues", [
    ("交易金额", "-12.34", "negative_amount"),
    ("交易金额", "1.234", "amount_precision,missing_amount"),
    ("交易时间", "unknown", "invalid_time"),
    ("收付标志", "unknown", "invalid_direction,missing_direction"),
    ("交易账号", "", "missing_identity"),
    ("交易是否成功", "unknown", "unknown_status"),
    ("交易币种", "unknown", "invalid_currency"),
])
def test_reference_policy_does_not_accept_other_invalid_financial_fields(column, value, issues):
    changes = {column: value}
    if column == "交易账号":
        changes["交易卡号"] = ""
    plan = parse([source_row(**changes)], reference_policy=POLICY)
    assert not plan["rows"]
    assert plan["records"][0]["reason_code"] == issues.split(",")[0]
    assert plan["records"][0]["details"]["validation_issues"] == issues


@pytest.mark.parametrize("alias,value,reason", [
    ("amount", "14.34", "conflicting_amount"),
    ("direction", "支出", "conflicting_direction"),
    ("transaction time", "2026-02-02 03:04:05", "conflicting_time"),
    ("account", "SYN-OTHER", "conflicting_field"),
    ("transaction id", "SYN-OTHER", "conflicting_field"),
])
def test_additional_same_role_candidates_are_not_suppressed(alias, value, reason):
    row = source_row(**{"交易流水号": "SYN-SAME", "凭证号": "SYN-SAME", alias: value})
    headers = COLUMNS + (alias,)
    default, policy = parse([row], headers), parse([row], headers, reference_policy=POLICY)
    assert policy == default
    assert not policy["rows"]
    assert reason in policy["records"][0]["details"]["validation_issues"].split(",")


@pytest.mark.parametrize("value", ["unknown-policy", "", True, {}, []])
def test_reference_policy_accepts_only_the_closed_internal_identifier(value):
    with pytest.raises(tables.TableImportError, match="引用策略"):
        parse([source_row()], reference_policy=value)


def test_explicit_trade_number_mapping_cannot_replace_the_fixed_reference_policy():
    with pytest.raises(tables.TableImportError, match="引用策略"):
        parse([source_row()], reference_policy=POLICY, mapping={"trade_no": "凭证号"})
