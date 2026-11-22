"""Literal, region-local direction codes declared in their own column heading."""

import json
import re


PROFILE = "funds-trace-table-v87"
POLICY = "source-direction-heading-v1"
DETAIL_FIELD = "source_direction_recheck"
_PREFIX = "收付标志（"
_CODE = r"[A-Za-z]{0,3}[0-9]{1,12}"
_HEADING = re.compile(rf"收付标志（({_CODE})=(收入|支出)；({_CODE})=(收入|支出)）")
_PENDING = frozenset({
    "unsupported_declaration", "conflicting_declaration", "non_literal_code", "undeclared_code",
    "validation_failed",
})
_MAX_EXCERPT = 128  # UTF-8 JSON bytes, including quotes and escapes.


def is_heading(value):
    return type(value) is str and value.startswith(_PREFIX)


def could_be_literal(value):
    """Only error cells that could be mistaken for this policy's text need a guard."""
    return is_heading(value) or type(value) is str and re.fullmatch(_CODE, value) is not None


def directions(heading):
    match = _HEADING.fullmatch(heading) if is_heading(heading) else None
    if match is None:
        return {}
    first_code, first_role, second_code, second_role = match.groups()
    if first_code == second_code or first_role == second_role:
        return {}
    return {code: "in" if role == "收入" else "out"
            for code, role in ((first_code, first_role), (second_code, second_role))}


def _excerpt(value):
    if (type(value) is str and len(value) <= _MAX_EXCERPT
            and len(json.dumps(value, ensure_ascii=False).encode()) <= _MAX_EXCERPT):
        return value
    return None


def column_excerpt(index, heading, code):
    return {"column": _excerpt(heading), "column_index": index + 1, "raw": _excerpt(code)}


def serialize(status, header_row, columns, direction=None):
    return json.dumps({"policy": POLICY, "status": status, "header_row": header_row,
                       "columns": columns, "direction": direction}, ensure_ascii=False, separators=(",", ":"))


def validate(value, header_row):
    """Check the recorded coordinates and closed meaning of a package excerpt."""
    doc = json.loads(value)
    if (not isinstance(doc, dict) or set(doc) != {"policy", "status", "header_row", "columns", "direction"}
            or doc["policy"] != POLICY or type(doc["header_row"]) is not int
            or doc["header_row"] < 1 or doc["header_row"] != header_row):
        raise ValueError("Invalid direction declaration scope")
    columns = doc["columns"]
    if (not isinstance(columns, list) or not 1 <= len(columns) <= 2
            or any(not isinstance(column, dict) or set(column) != {"column", "column_index", "raw"}
                   or type(column["column_index"]) is not int or not 1 <= column["column_index"] <= 256
                   or any(column[key] is not None and _excerpt(column[key]) is None for key in ("column", "raw"))
                   for column in columns)
            or len({column["column_index"] for column in columns}) != len(columns)
            or columns[0]["column"] is not None and not is_heading(columns[0]["column"])):
        raise ValueError("Invalid direction declaration columns")
    if doc["status"] == "verified":
        meaning = directions(columns[0]["column"]).get(columns[0]["raw"])
        if len(columns) != 1 or meaning is None or meaning != doc["direction"]:
            raise ValueError("Direction does not match the declared code")
    elif type(doc["status"]) is not str or doc["status"] not in _PENDING or doc["direction"] is not None:
        raise ValueError("Invalid direction declaration status")
    return doc
