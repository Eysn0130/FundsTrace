"""Worksheet geometry and portable role evidence for grouped headers."""
import io

import pytest
from openpyxl import Workbook

from app import table_tiered_headers as tier


PARENTS = ("余额信息", None, "收支金额（分）", None, "交易双方", None, None, "交易要素", None, None)
LEAVES = ("可用余额", "账面余额", "收入金额", "支出金额", "对方户名", "本方账号", "对方账号", "币别", "交易时刻", "交易日期")
RANGES = ((1, 2, 2, 2), (3, 2, 4, 2), (5, 2, 7, 2), (8, 2, 10, 2))
SEPARATE_PARENTS = ("发生额", None, "交易时间", None, "账户余额", None, "对方信息", None, "账户信息", None, None, None)
SEPARATE_LEAVES = ("支出", "收入", "时刻", "日期", "账面", "可用", "对方名称", "对方账卡", "币种", "本方账卡", "交易状态", "交易金额")
SEPARATE_RANGES = tuple((column, 3, column + 1, 3) for column in range(1, 11, 2))


def test_grouped_proof_uses_registered_worksheet_ranges_and_qualified_columns():
    book = Workbook()
    book.active.append(("参考说明",))
    book.active.merge_cells("A1:I1")
    sheet = book.create_sheet("移位合成表头")
    sheet.append(("标题",))
    sheet.append(PARENTS)
    sheet.append(LEAVES)
    for left, first, right, last in RANGES:
        sheet.merge_cells(start_column=left, start_row=first, end_column=right, end_row=last)
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()

    ranges = tier.xlsx_merge_ranges(buffer.getvalue(), {1: {1}, 2: {2}})
    assert ranges == {1: {1: ((1, 1, 9, 1),)}, 2: {2: RANGES}}
    proof = tier.inspect_grouped(PARENTS, LEAVES, 2, lambda value: value, ranges[2][2])
    assert proof["status"] == "verified"
    assert tier.checked_proof(tier.encode(proof), version=2) == proof
    assert tier.checked_proof(tier.encode(proof), version=1) is None
    assert [(column + 1, field, factor) for column, field, _, _, factor, *_ in tier.candidate_columns(proof)] == [
        (2, "balance", "1"), (3, "income_amt", "0.01"), (4, "expense_amt", "0.01"),
        (5, "counter_name", "1"), (6, "self_account", "1"), (7, "counter_account", "1"),
        (8, "currency", "1"), (9, "time_part", "1"), (10, "trade_time", "1"),
    ]


@pytest.mark.parametrize(("ranges", "leaves"), [
    ((), LEAVES),
    ((RANGES[0], *RANGES[2:]), LEAVES),
    ((RANGES[0], (2, 2, 4, 2), *RANGES[2:]), LEAVES),
    ((*RANGES[:-1], (8, 2, 10, 3)), LEAVES),
    (RANGES, (*LEAVES[:3], "支出金额（元）", *LEAVES[4:])),
    (RANGES, (*LEAVES[:5], "交易日期", *LEAVES[6:])),
])
def test_grouped_proof_retains_incomplete_or_conflicting_geometry(ranges, leaves):
    proof = tier.inspect_grouped(PARENTS, leaves, 2, lambda value: value, ranges)
    assert proof["status"] == "ambiguous"
    assert tier.checked_proof(tier.encode(proof), version=2) == proof
    proof["status"] = "verified"
    assert tier.checked_proof(tier.encode(proof), version=2) is None


def test_grouped_proof_separates_own_ledger_counterparty_and_transaction_clock():
    proof = tier.inspect_grouped(SEPARATE_PARENTS, SEPARATE_LEAVES, 3, lambda value: value, SEPARATE_RANGES)
    assert proof["status"] == "verified"
    assert tier.checked_proof(tier.encode(proof), version=2) == proof
    assert [(index + 1, field) for index, field, *_ in tier.candidate_columns(proof)] == [
        (1, "expense_amt"), (2, "income_amt"), (3, "time_part"), (4, "trade_time"),
        (5, "balance"), (7, "counter_name"), (8, "counter_account"), (9, "currency"), (10, "self_account"),
    ]


def test_plain_identity_labels_require_a_proved_side_parent():
    parents = ("本方账户信息", None, "发生额", None, "对手信息", None, "交易信息", None, None)
    leaves = ("账号", "户名", "收入金额", "支出金额", "账号", "户名", "日期", "时刻", "币种")
    ranges = ((1, 4, 2, 4), (3, 4, 4, 4), (5, 4, 6, 4), (7, 4, 9, 4))
    proof = tier.inspect_grouped(parents, leaves, 4, lambda value: value, ranges)
    assert proof["status"] == "verified" and tier.checked_proof(tier.encode(proof), version=2) == proof
    columns = {index + 1: field for index, field, *_ in tier.candidate_columns(proof)}
    assert (columns[1], columns[2], columns[5], columns[6]) == (
        "self_account", "self_name", "counter_account", "counter_name")
    ambiguous = tier.inspect_grouped(("双方信息", *parents[1:]), leaves, 4, lambda value: value, ranges)
    assert ambiguous["status"] == "ambiguous"


@pytest.mark.parametrize(("index", "label"), [
    (2, "币种"),
    (6, "币种"),
    (6, "本方户名"),
    (9, "对方开户行"),
    (10, "借方"),
    (11, "币种代码"),
])
def test_grouped_proof_rejects_wrong_scope_and_unbound_currency(index, label):
    leaves = (*SEPARATE_LEAVES[:index], label, *SEPARATE_LEAVES[index + 1:])
    proof = tier.inspect_grouped(SEPARATE_PARENTS, leaves, 3, lambda value: value, SEPARATE_RANGES)
    assert proof["status"] == "ambiguous"
    assert tier.checked_proof(tier.encode(proof), version=2) == proof


def test_grouped_proof_rejects_competing_currency_in_another_recognized_group():
    parents = (*SEPARATE_PARENTS, "交易信息", None)
    leaves = (*SEPARATE_LEAVES, "币种", "收支方向")
    ranges = (*SEPARATE_RANGES, (13, 3, 14, 3))
    proof = tier.inspect_grouped(parents, leaves, 3, lambda value: value, ranges)
    assert proof["status"] == "ambiguous"
    assert tier.checked_proof(tier.encode(proof), version=2) == proof


def test_grouped_account_currency_needs_an_identifier_in_its_own_span():
    parents = (*SEPARATE_PARENTS, "双方信息", None)
    leaves = (*SEPARATE_LEAVES[:9], "本方户名", *SEPARATE_LEAVES[10:], "本方账号", "本方卡号")
    ranges = (*SEPARATE_RANGES, (13, 3, 14, 3))
    proof = tier.inspect_grouped(parents, leaves, 3, lambda value: value, ranges)
    assert proof["status"] == "ambiguous"
    assert tier.checked_proof(tier.encode(proof), version=2) == proof
