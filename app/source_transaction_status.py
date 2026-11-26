"""Scope the isolated status interpretation to the source's named field."""

COLUMN = "交易是否成功"
POLICY = "transaction-failure-literal-v1"
DETAIL_FIELD = "source_transaction_status_policy"


def explicitly_failed(value):
    return type(value) is str and "失败" in value
