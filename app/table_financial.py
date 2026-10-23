"""Closed financial header vocabulary and strict local-clock syntax.

These are explicit declarations, not statistical guesses about source values.
Only the new table profile opts in; historical profiles retain their vocabulary.
"""
import datetime as dt
import re


ALIASES = {
    "amount": ("交易发生金额", "交易发生额", "本次交易金额", "trade amount", "txn amount"),
    "income_amt": ("收入发生额", "收入发生金额"),
    "expense_amt": ("支出发生额", "支出发生金额"),
    "balance": ("交易后余额", "本次交易余额"),
    "direction": ("收付方向", "借贷方向", "收支类型", "收付类型", "借贷标记", "收付标记"),
    "trade_time": ("交易日期时间", "交易发生时间", "交易发生日期", "transaction timestamp", "trade date", "trade time"),
    "time_part": ("交易时间时分秒", "transaction clock"),
    "amount_unit": ("金额单位", "交易金额单位", "amount unit", "transaction amount unit"),
    "income_amt_unit": ("收入金额单位", "收入发生额单位", "credit amount unit"),
    "expense_amt_unit": ("支出金额单位", "支出发生额单位", "debit amount unit"),
    "balance_unit": ("余额单位", "交易余额单位", "balance unit"),
}
MONEY_FIELDS = ("amount", "income_amt", "expense_amt", "balance")


def clock(raw):
    """Parse an explicit clock, without padding lost digits or inventing a date."""
    match = re.fullmatch(r"(\d{2})(\d{2})(\d{2})", raw)
    if match:
        hour, minute, second = map(int, match.groups())
        microsecond = 0
    else:
        match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?", raw)
        if not match:
            return None
        hour, minute, second, fraction = match.groups()
        hour, minute, second = int(hour), int(minute), int(second or 0)
        microsecond = int((fraction or "").ljust(6, "0") or 0)
    try:
        return dt.time(hour, minute, second, microsecond)
    except ValueError:
        return None
