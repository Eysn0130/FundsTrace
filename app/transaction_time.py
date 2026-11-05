"""Source wall clocks and explicit UTC instants are separate time domains.

Naive historical values are never assigned a zone. Stable display order across
domains is not evidence of chronology.
"""
from datetime import datetime, timezone

FIELDS = ("trade_time_utc", "trade_time_original", "trade_time_offset")


def parsed(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value



def bound(value):
    import re
    stamp = parsed(value)
    if stamp is not None and stamp.tzinfo is not None:
        if isinstance(value, str):
            match = re.search(r"(Z|[+-][0-9]{2}:[0-9]{2})$", value)
            if not match or match[0] == "-00:00" or (match[0] != "Z" and
                    (int(match[0][1:3]) >= 24 or int(match[0][4:]) >= 60)):
                raise ValueError("时间边界的显式偏移无效或未知")
        if stamp.utcoffset().total_seconds() % 60:
            raise ValueError("时间偏移须精确到分钟")
    return stamp

def basis(row):
    return "utc" if row.get("trade_time_utc") is not None else "unqualified"


def clock(row):
    value = parsed(row.get("trade_time_utc") or row.get("trade_time"))
    return value.replace(tzinfo=None) if value is not None else None


def text(value):
    return value.isoformat(sep=" ") if isinstance(value, datetime) else value


def public(row):
    return {"trade_time": row.get("trade_time_original") or text(row.get("trade_time")),
            "trade_time_wall": text(row.get("trade_time_wall", row.get("trade_time"))),
            "trade_time_utc": (text(row["trade_time_utc"]).replace(" ", "T").removesuffix("Z") + "Z")
                if row.get("trade_time_utc") is not None else None,
            "trade_time_original": row.get("trade_time_original"),
            "trade_time_offset": row.get("trade_time_offset"), "trade_time_basis": basis(row)}


def domains(rows):
    return {basis(row) for row in rows if row.get("trade_time") is not None}


def summary(rows):
    kinds = domains(rows)
    values = [clock(row) for row in rows if row.get("trade_time") is not None]
    kind = next(iter(kinds)) if len(kinds) == 1 else "mixed" if kinds else "unknown"
    suffix = "Z" if kind == "utc" else ""
    return {"time_basis": kind, "time_ordering": "unknown" if len(kinds) > 1 else "within_basis",
            "time_min": text(min(values)) + suffix if values and len(kinds) == 1 else None,
            "time_max": text(max(values)) + suffix if values and len(kinds) == 1 else None}


def sql_clock(alias=""):
    p = alias + "." if alias else ""
    return f"coalesce({p}trade_time_utc,{p}trade_time)"


def sql_order(alias=""):
    p = alias + "." if alias else ""
    return f"({p}trade_time_utc is not null) desc,{sql_clock(alias)} desc nulls last,{p}id desc"


def sql_bounds(alias="", low="tmin", high="tmax"):
    p = alias + "." if alias else ""
    domain = f"case when {p}trade_time_utc is not null then 'utc' else 'unqualified' end"
    kinds = f"count(distinct {domain}) filter(where {p}trade_time is not null)"
    suffix = f"case when bool_or({p}trade_time_utc is not null) then 'Z' else '' end"
    return ",".join(f"case when {kinds}=1 then cast({op}({sql_clock(alias)}) as varchar)||{suffix} end {name}"
                    for op, name in (("min", low), ("max", high))) + f",case when {kinds}>1 then 'mixed' when bool_or({p}trade_time_utc is not null) then 'utc' else 'unqualified' end time_basis"


def window(start=None, end=None, alias=""):
    """Bare bounds select source wall clocks; offset bounds select UTC only."""
    bounds = [bound(value) for value in (start, end) if value is not None and value != ""]
    aware = {value.tzinfo is not None for value in bounds}
    if len(aware) > 1:
        raise ValueError("时间边界须同为来源壁钟，或同带显式偏移")
    absolute = aware == {True}
    p = alias + "." if alias else ""
    column = p + ("trade_time_utc" if absolute else "trade_time")
    clauses, params = [], []
    for value, op in ((start, ">="), (end, "<=")):
        if value is None or value == "":
            continue
        raw = value
        value = bound(value)
        if absolute:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        elif op == "<=" and isinstance(raw, str) and len(raw) == 10:
            value = value.replace(hour=23, minute=59, second=59, microsecond=999999)
        clauses.append(column + op + "?")
        params.append(value)
    return " and ".join(clauses), params, "utc" if absolute else "source_wall_clock"


def matches(row, start, end):
    bounds = [value for value in (start, end) if value is not None]
    absolute = bool(bounds and bounds[0].tzinfo is not None)
    value = parsed(row.get("trade_time_utc") if absolute else row.get("trade_time"))
    if value is None:
        return False
    if absolute:
        start = start.astimezone(timezone.utc).replace(tzinfo=None) if start else None
        end = end.astimezone(timezone.utc).replace(tzinfo=None) if end else None
    return (start is None or value >= start) and (end is None or value <= end)
