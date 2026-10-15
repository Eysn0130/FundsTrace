"""Exact monetary boundaries. Approximate statistics must stay separate."""
from decimal import Decimal, InvalidOperation, localcontext
import re

CENT = Decimal("0.01")


def money_decimal(value):
    """Accept exact decimal input; never pretend a float is lossless money."""
    if isinstance(value, (float, bool)) or value is None:
        raise ValueError("金额须为十进制字符串，最多两位小数")
    if not isinstance(value, Decimal) and not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", str(value)):
        raise ValueError("金额须为十进制字符串，最多两位小数")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
        if not amount.is_finite() or amount.adjusted() > 35:
            raise ValueError("金额必须为有限十进制数")
        with localcontext() as context:
            context.prec = 40
            rounded = amount.quantize(CENT)
        if rounded != amount:
            raise ValueError("金额最多两位小数，不自动舍入筛选边界")
        return rounded.copy_abs() if rounded == 0 else rounded
    except (InvalidOperation, TypeError) as error:
        raise ValueError("金额须为十进制字符串，最多两位小数") from error


def money_string(value):
    return format(money_decimal(value), ".2f")


def money_display(value):
    return format(money_decimal(value), ",.2f")


def wire_money(value):
    """Encode money explicitly before FastAPI's default Decimal→float path."""
    if isinstance(value, Decimal):
        return money_string(value)
    if isinstance(value, dict):
        return {key: wire_money(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [wire_money(item) for item in value]
    return value
