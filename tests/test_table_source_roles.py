"""Synthetic complete headers test table roles independently of source values."""

import pytest

from app.table_source_roles import classify


ACCOUNT = [
    "账号", "卡号", "账户类别", "币种", "可用余额", "账户余额", "账户状态", "开户日期",
    "开户网点", "销户日期", "销户网点", "开户网点代码", "最后交易时间", "钞汇标志", "备注",
]
CLIENT = [
    "客户名称", "法人代表", "法人代表证件类型", "法人代表证件号码", "联系手机", "单位地址",
    "住宅地址", "住宅电话", "联系电话", "证件号码", "工作单位", "客户工商执照号码", "单位电话",
    "账单地址", "邮箱地址", "地税纳税号", "国税纳税号", "证件类型代码", "代办人证件类型",
    "代办人姓名", "代办人证件号码", "任务流水号", "查询反馈结果", "标识",
]
SUBACCOUNT = [
    "账卡号", "子账户序号", "子账户类别", "子账户账号", "币种", "钞汇标志", "账户余额",
    "账户状态", "可用余额",
]
TRANSACTION_EXPLICIT = [
    "本方卡号", "姓名(查询条件)", "证件号码(查询条件)", "交易时间", "交易金额", "借贷标志",
    "交易余额", "交易对方名称", "交易对方证件号码", "交易对方账号", "交易对方账号开户行",
    "交易类型", "交易摘要", "交易流水号", "任务流水号", "传票号", "凭证号", "IP地址", "MAC地址",
    "交易对手余额", "终端号", "商户名称", "商户号", "查询反馈结果", "查询帐号", "交易网点名称",
    "交易柜员号", "交易是否成功", "查询卡号", "本方账号", "现金标志", "日志号", "币种",
    "凭证种类", "备注", "交易对方卡号", "交易网点代码", "交易发生地",
]
TRANSACTION_QUERY_ONLY = [
    "姓名(查询条件)", "证件号码(查询条件)", "交易时间", "交易金额", "借贷标志", "交易余额",
    "交易对方名称", "交易对方证件号码", "交易对方账号开户行", "交易类型", "交易摘要",
    "交易流水号", "任务流水号", "传票号", "凭证号", "IP地址", "MAC地址", "交易对手余额",
    "终端号", "商户名称", "商户号", "查询反馈结果", "查询帐号", "交易网点名称",
    "交易对方账卡号", "交易柜员号", "交易是否成功", "查询卡号", "现金标志",
    "交易对方帐卡号类型", "日志号", "币种", "凭证种类", "备注", "交易网点代码", "交易发生地",
]

SCHEMAS = [
    (ACCOUNT, "account"),
    (ACCOUNT[1:], "account"),
    (CLIENT, "client"),
    ([header for header in CLIENT if header != "账单地址"], "client"),
    (SUBACCOUNT, "subaccount"),
    (TRANSACTION_EXPLICIT, "transaction_explicit"),
    ([header for header in TRANSACTION_EXPLICIT if header != "查询帐号"], "transaction_explicit"),
    (TRANSACTION_QUERY_ONLY, "transaction_query_only"),
]


@pytest.mark.parametrize("headers,role", SCHEMAS)
def test_complete_schemas_keep_their_explicit_roles(headers, role):
    assert classify(headers) == role
    assert classify(list(reversed(headers))) == role


@pytest.mark.parametrize("headers,role", SCHEMAS)
def test_only_trailing_empty_headers_are_ignored(headers, role):
    assert classify([*headers, "", " ", "\u3000"]) == role
    assert classify(["", *headers]) is None
    assert classify([headers[0], " ", *headers[1:]]) is None


@pytest.mark.parametrize("headers,role", SCHEMAS)
def test_unknown_missing_and_duplicate_headers_do_not_match(headers, role):
    assert classify([*headers, "未定义来源字段"]) is None
    assert classify(headers[:-1]) is None
    assert classify([*headers, headers[0]]) is None
    assert classify([*headers, " " + headers[0] + " "]) is None


def test_only_known_optional_columns_are_optional():
    assert classify([header for header in ACCOUNT if header != "备注"]) is None
    assert classify([header for header in CLIENT if header != "任务流水号"]) is None
    assert classify([header for header in TRANSACTION_EXPLICIT if header != "本方卡号"]) is None
    assert classify([header for header in TRANSACTION_EXPLICIT if header != "本方账号"]) is None
    assert classify([header for header in TRANSACTION_QUERY_ONLY if header != "查询帐号"]) is None


def test_literal_normalization_preserves_query_and_account_semantics():
    normalized = ["  " + header + "\u3000" for header in TRANSACTION_EXPLICIT]
    normalized[normalized.index("  IP地址\u3000")] = "ｉｐ地址"
    normalized[normalized.index("  MAC地址\u3000")] = "ｍａｃ地址"
    normalized[normalized.index("  姓名(查询条件)\u3000")] = "姓名（查询条件）"
    assert classify(normalized) == "transaction_explicit"
    assert classify([header.replace("(查询条件)", "") for header in TRANSACTION_EXPLICIT]) is None
    assert classify([header.replace("账号", "帐号") for header in TRANSACTION_EXPLICIT]) is None
    assert classify([header.replace("IP地址", "IP 地址") for header in TRANSACTION_EXPLICIT]) is None


def test_query_identifiers_never_create_an_explicit_owner_role():
    assert classify(TRANSACTION_QUERY_ONLY) == "transaction_query_only"
    assert classify([*TRANSACTION_QUERY_ONLY, "本方账号"]) is None
    assert classify(["交易时间", "交易金额", "借贷标志", "查询帐号", "查询卡号"]) is None


def test_auxiliary_balances_do_not_make_a_partial_transaction_schema():
    assert classify([*ACCOUNT, "交易金额"]) is None
    assert classify([*SUBACCOUNT, "交易时间"]) is None
    assert classify(["账户余额", "可用余额", "币种"]) is None


@pytest.mark.parametrize("headers", [[], [""], ["", " ", "\u3000"]])
def test_empty_header_is_unknown(headers):
    assert classify(headers) is None
