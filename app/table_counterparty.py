"""Read explicitly labelled counterparty fields in one source cell."""
import re
import unicodedata


HEADERS = frozenset({"交易对手信息", "交易对方信息"})
_FIELDS = {
    "户名": "counter_name", "姓名": "counter_name", "对方户名": "counter_name", "对方名称": "counter_name",
    "账号": "counter_account", "账户号": "counter_account", "对方账号": "counter_account",
    "卡号": "counter_card", "对方卡号": "counter_card",
    "备注": "remark", "摘要": "summary",
}
_SEPARATOR = re.compile(r"[;；]")
_LABEL = re.compile(r"([^:：]+)[:：](.+)\Z")
_NUMBER = re.compile(r"[0-9]{1,40}\Z")


def _label(text):
    return unicodedata.normalize("NFKC", text).strip()


def source_column(headers):
    matching = [index for index, raw in enumerate(headers) if isinstance(raw, str) and _label(raw) in HEADERS]
    return matching[0] if len(matching) == 1 else None


def components(raw):
    """Return roles only when the delimiters and labels cover the entire cell."""
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 1024:
        return {}
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in raw):
        return {}
    pieces = {}
    for item in _SEPARATOR.split(raw.strip()):
        if not (match := _LABEL.fullmatch(item.strip())):
            return {}
        role = _FIELDS.get(_label(match[1]))
        value = match[2].strip()
        if not role or not value or role in pieces:
            return {}
        if role in {"counter_account", "counter_card"} and not _NUMBER.fullmatch(value):
            return {}
        if role == "counter_name" and (value.isdecimal() or ":" in value or "：" in value):
            return {}
        pieces[role] = value
    if "counter_name" not in pieces or not pieces.keys() & {"counter_account", "counter_card"}:
        return {}
    return {role: value for role, value in pieces.items() if role.startswith("counter_")}
