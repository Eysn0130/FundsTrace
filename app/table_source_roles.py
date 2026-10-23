"""Closed header signatures for the supported feedback workbook table roles.

This only classifies a complete header. It does not infer an owner from query
conditions, interpret a financial value, or turn auxiliary balances into flows.
Column order and trailing unused worksheet columns do not change the role.
"""

import unicodedata


def _normalize(header):
    return unicodedata.normalize("NFKC", header).strip().casefold()


_ACCOUNT = (
    "账号", "卡号", "账户类别", "币种", "可用余额", "账户余额", "账户状态", "开户日期",
    "开户网点", "销户日期", "销户网点", "开户网点代码", "最后交易时间", "钞汇标志", "备注",
)
_CLIENT = (
    "客户名称", "法人代表", "法人代表证件类型", "法人代表证件号码", "联系手机", "单位地址",
    "住宅地址", "住宅电话", "联系电话", "证件号码", "工作单位", "客户工商执照号码", "单位电话",
    "账单地址", "邮箱地址", "地税纳税号", "国税纳税号", "证件类型代码", "代办人证件类型",
    "代办人姓名", "代办人证件号码", "任务流水号", "查询反馈结果", "标识",
)
_SUBACCOUNT = (
    "账卡号", "子账户序号", "子账户类别", "子账户账号", "币种", "钞汇标志", "账户余额",
    "账户状态", "可用余额",
)
_TRANSACTION_EXPLICIT = (
    "本方卡号", "姓名(查询条件)", "证件号码(查询条件)", "交易时间", "交易金额", "借贷标志",
    "交易余额", "交易对方名称", "交易对方证件号码", "交易对方账号", "交易对方账号开户行",
    "交易类型", "交易摘要", "交易流水号", "任务流水号", "传票号", "凭证号", "IP地址", "MAC地址",
    "交易对手余额", "终端号", "商户名称", "商户号", "查询反馈结果", "查询帐号", "交易网点名称",
    "交易柜员号", "交易是否成功", "查询卡号", "本方账号", "现金标志", "日志号", "币种",
    "凭证种类", "备注", "交易对方卡号", "交易网点代码", "交易发生地",
)
_TRANSACTION_QUERY_ONLY = (
    "姓名(查询条件)", "证件号码(查询条件)", "交易时间", "交易金额", "借贷标志", "交易余额",
    "交易对方名称", "交易对方证件号码", "交易对方账号开户行", "交易类型", "交易摘要",
    "交易流水号", "任务流水号", "传票号", "凭证号", "IP地址", "MAC地址", "交易对手余额",
    "终端号", "商户名称", "商户号", "查询反馈结果", "查询帐号", "交易网点名称",
    "交易对方账卡号", "交易柜员号", "交易是否成功", "查询卡号", "现金标志",
    "交易对方帐卡号类型", "日志号", "币种", "凭证种类", "备注", "交易网点代码", "交易发生地",
)


def _signature(headers):
    return frozenset(_normalize(header) for header in headers)


_ROLES = {
    _signature(_ACCOUNT): "account",
    _signature(_ACCOUNT[1:]): "account",
    _signature(_CLIENT): "client",
    _signature(header for header in _CLIENT if header != "账单地址"): "client",
    _signature(_SUBACCOUNT): "subaccount",
    _signature(_TRANSACTION_EXPLICIT): "transaction_explicit",
    _signature(header for header in _TRANSACTION_EXPLICIT if header != "查询帐号"):
        "transaction_explicit",
    _signature(_TRANSACTION_QUERY_ONLY): "transaction_query_only",
}


def query_condition(header):
    """Recognize an annotation label, without assigning any data-field role."""
    name = _normalize(header)
    suffix = "(查询条件)"
    return (name.endswith(suffix) and bool(name[:-len(suffix)].strip())
            and name.count(suffix) == 1
            and not any(unicodedata.category(char) in {"Cc", "Cf"} for char in name))


def classify(headers, *, query_conditions=False):
    """Return a known table role, or ``None`` for any incomplete/unknown header.

    Only trailing empty header cells are permitted. Duplicate nonempty names
    remain ambiguous even if their source spelling differs by normalization.
    The opt-in annotation variant is reserved for the v14 transaction contract.
    """
    names = [_normalize(header) for header in headers]
    while names and not names[-1]:
        names.pop()
    if not names or "" in names or len(names) != len(set(names)):
        return None
    signature = frozenset(names)
    role = _ROLES.get(signature)
    if role or not query_conditions:
        return role
    # v14's uniform transaction contract permits different query annotation
    # labels. All 34 substantive columns, both annotations and their unique
    # positions must still exist; no partial/generic/auxiliary table qualifies.
    annotations = [name for name in names if query_condition(name)]
    required = _signature(header for header in _TRANSACTION_QUERY_ONLY
                          if not query_condition(header))
    if len(annotations) == 2 and signature - set(annotations) == required:
        return "transaction_query_only"
    return None
