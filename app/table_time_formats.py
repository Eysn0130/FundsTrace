"""Closed time-format declarations for deterministic table headers.

The caller owns the field-name vocabulary and parser version. This module only
recognizes complete format annotations and validates their exact semantics; it
does not infer dates, Excel serials, time zones, or omitted clock precision.
"""
import datetime as dt
import re
import unicodedata


_FORMATS = {
    "YYYY-MM-DD": ("date", r"[0-9]{4}-[0-9]{2}-[0-9]{2}", "%Y-%m-%d"),
    "YYYY/MM/DD": ("date", r"[0-9]{4}/[0-9]{2}/[0-9]{2}", "%Y/%m/%d"),
    "YYYYMMDD": ("date", r"[0-9]{8}", "%Y%m%d"),
    "YYYY-MM-DD HH:MM:SS": ("datetime", r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", "%Y-%m-%d %H:%M:%S"),
    "YYYYMMDDHHMMSS": ("datetime", r"[0-9]{14}", "%Y%m%d%H%M%S"),
    "YYYYMMDD HHMMSS": ("datetime", r"[0-9]{8} [0-9]{6}", "%Y%m%d %H%M%S"),
    "HH:MM:SS": ("clock", r"[0-9]{2}:[0-9]{2}:[0-9]{2}", "%H:%M:%S"),
    "HHMMSS": ("clock", r"[0-9]{6}", "%H%M%S"),
}


def header_parts(header):
    """Return ``(base, spec)`` only for a complete known format annotation."""
    if not isinstance(header, str):
        return None
    raw = unicodedata.normalize("NFKC", header).strip()
    match = re.fullmatch(r"([^()\[\]]+?)\s*(?:\(([^()\[\]]+)\)|\[([^()\[\]]+)\])", raw)
    if not match:
        return None
    base = match[1].strip()
    spec = re.sub(r"\s+", " ", (match[2] or match[3]).strip()).upper()
    return (base, spec) if base and spec in _FORMATS else None


def validate(value, spec, date_only=False):
    """Check every declared component without truncating or repairing values."""
    declaration = _FORMATS.get(spec)
    if declaration is None:
        return False
    kind, pattern, parser_format = declaration
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None or value.microsecond:
            return False
        if kind == "date":
            return value.time() == dt.time()
        return kind == "datetime" and not date_only
    if isinstance(value, dt.date):
        return kind == "date"
    if isinstance(value, dt.time):
        return kind == "clock" and not date_only and value.tzinfo is None and not value.microsecond
    if not isinstance(value, str):
        return False
    raw = value.strip()
    if not re.fullmatch(pattern, raw):
        return False
    try:
        dt.datetime.strptime(raw, parser_format)
    except ValueError:
        return False
    return not date_only or kind == "date"
