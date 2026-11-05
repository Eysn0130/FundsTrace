"""Closed time-format declarations for deterministic table headers.

The caller owns the field-name vocabulary and parser version. This module only
recognizes complete format annotations and validates their exact semantics; it
does not infer dates, Excel serials, time zones, or omitted clock precision.
"""
import datetime as dt
import json
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


_PRECISE_FORMAT = ("clock", r"[0-9]{12}", "%H%M%S%f")

def precise_clock(raw):
    if not isinstance(raw, str) or not re.fullmatch(_PRECISE_FORMAT[1], raw):
        return None
    try:
        return dt.datetime.strptime(raw, _PRECISE_FORMAT[2]).time()
    except ValueError:
        return None

def header_parts(header, *, precise=False):
    """Return ``(base, spec)`` only for a complete known format annotation."""
    if not isinstance(header, str):
        return None
    raw = unicodedata.normalize("NFKC", header).strip()
    match = re.fullmatch(r"([^()\[\]]+?)\s*(?:\(([^()\[\]]+)\)|\[([^()\[\]]+)\])", raw)
    if not match:
        return None
    base = match[1].strip()
    spec = re.sub(r"\s+", " ", (match[2] or match[3]).strip()).upper()
    return (base, spec) if base and (spec in _FORMATS or precise and spec == "HHMMSSFFFFFF") else None


def validate(value, spec, date_only=False, *, precise=False):
    """Check every declared component without truncating or repairing values."""
    declaration = _PRECISE_FORMAT if precise and spec == "HHMMSSFFFFFF" else _FORMATS.get(spec)
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
        return kind == "clock" and not date_only and value.tzinfo is None and (not value.microsecond or precise and spec == "HHMMSSFFFFFF")
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


def source_time_value(raw):
    """Describe explicit source offsets without projecting into naive ledgers."""
    result = {"wall_clock": raw, "offset": None, "utc": None, "basis": "source_wall_clock"}
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)(Z|[+-]\d{2}:\d{2})", raw)
    if match is None:
        return result
    wall, offset = match.groups()
    result.update(wall_clock=wall, offset=offset, basis="invalid_offset")
    if offset != "Z" and (int(offset[1:3]) >= 24 or int(offset[4:]) >= 60):
        return result
    # A negative zero offset may express an unknown local offset. Do not use it
    # as positive evidence of a UTC instant.
    if offset == "-00:00":
        result["basis"] = "unknown_offset"
        return result
    try:
        value = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        utc = value.astimezone(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return result
    result.update(utc=utc, basis="explicit_offset")
    return result


def validate_source_time_evidence(raw):
    try:
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {"policy", "ordinary_time_role", "values"}
                or value["policy"] != "source-time-roles-v1" or value["ordinary_time_role"] not in {None, "transaction", "unspecified"}
                or not isinstance(value["values"], list)):
            return False
        for item in value["values"]:
            if (not isinstance(item, dict) or set(item) != {"role", "column", "column_index", "raw", "wall_clock", "offset", "utc", "basis"}
                    or item["role"] not in {"transaction", "posting", "receipt", "settlement", "unspecified"} or not isinstance(item["column"], str)
                    or type(item["column_index"]) is not int or item["column_index"] < 1 or not isinstance(item["raw"], str)
                    or any(item[key] != expected for key, expected in source_time_value(item["raw"]).items())):
                return False
        return True
    except (TypeError, ValueError, KeyError):
        return False
