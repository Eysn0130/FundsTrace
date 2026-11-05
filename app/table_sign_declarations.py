"""Closed, source-scoped sign declarations; no free-text direction inference."""
import json
import re
import unicodedata

VERSION = "source-sign-declaration-v1"
DETAIL_FIELD = "source_sign_declaration"
NOTE_HEADERS = frozenset(("备注", "说明", "金额说明", "金额符号约定"))
UNITS = {"元": "1", "分": "0.01", "万元": "10000"}
_PATTERN = re.compile(
    r"(本文件|本表)(交易金额|金额|amount)(负值|负数|正值|正数)表示本方(支出|收入),"
    r"绝对值为(万元|元|分);(负值|负数|正值|正数)表示本方(支出|收入)[。.]?")


def normalized(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def looks_like(value):
    return isinstance(value, str) and bool(re.search(r"(?:负值|负数|正值|正数).*(?:收入|支出)", value))


def parse(value):
    if not isinstance(value, str):
        return None
    found = _PATTERN.fullmatch(normalized(value))
    if found is None:
        return None
    scope, field, first, first_direction, unit, second, second_direction = found.groups()
    if first[0] == second[0] or first_direction == second_direction:
        return None
    directions = {first[0]: "out" if first_direction == "支出" else "in",
                  second[0]: "out" if second_direction == "支出" else "in"}
    return {"scope": scope, "field": field, "negative": directions["负"],
            "positive": directions["正"], "unit": unit, "factor": UNITS[unit]}


def compile_sources(layouts):
    """All declarations are collected before any monetary row is interpreted.

    A layout is one unambiguous sheet with a single financial header. File
    claims bind every financial sheet by the same exact amount header. A table
    claim binds only its physical sheet, never another archive member.
    """
    claims = []
    invalid = False
    for layout in layouts:
        for position, row in enumerate(layout["rows"], 1):
            for column, raw in enumerate(row, 1):
                if not looks_like(raw):
                    continue
                parsed = parse(raw)
                if (parsed is None or (position, column) in layout.get("invalid_cells", ())
                        or position <= layout["header_row"] or not layout["single_region"]
                        or column - 1 not in layout["note_indices"]):
                    invalid = True
                    continue
                claims.append({**parsed, "member": layout["member"], "row": position,
                               "column": column, "text": raw})
    if not claims and not invalid:
        return {}
    result = {}
    for layout in layouts:
        applicable = [x for x in claims if x["scope"] == "本文件" or x["member"] == layout["member"]]
        if not applicable and not invalid:
            continue
        malformed = invalid or not layout["single_region"] or not layout["amount_header"]
        malformed |= any(x["field"] != layout["amount_header"] for x in applicable)
        meanings = {(x["negative"], x["positive"], x["factor"]) for x in applicable}
        if malformed or len(meanings) != 1:
            result[layout["member"]] = {"error": "conflicting_direction" if len(meanings) > 1 else "invalid_direction"}
            continue
        negative, positive, factor = next(iter(meanings))
        # Full source rows retain every occurrence. The bounded proof cites the
        # first occurrence of each identical scoped claim, after all conflicts
        # were checked; repetition cannot change financial interpretation.
        representatives = {}
        for claim in applicable:
            identity = tuple(claim[k] for k in ("scope", "field", "negative", "positive", "factor", "member"))
            representatives.setdefault(identity, claim)
        proof = json.dumps({"version": VERSION, "claims": list(representatives.values())}, ensure_ascii=False, separators=(",", ":"))
        if len(proof.encode()) > 2048:
            result[layout["member"]] = {"error": "invalid_direction"}
        else:
            result[layout["member"]] = {"negative": negative, "positive": positive, "factor": factor, "proof": proof}
    return result


def validate_proof(value):
    try:
        doc = json.loads(value)
        if set(doc) != {"version", "claims"} or doc["version"] != VERSION or not doc["claims"]:
            return False
        meanings = set()
        for claim in doc["claims"]:
            if set(claim) != {"scope", "field", "negative", "positive", "unit", "factor", "member", "row", "column", "text"}:
                return False
            parsed = parse(claim["text"])
            if parsed is None or any(claim[k] != v for k, v in parsed.items()):
                return False
            if (not isinstance(claim["member"], str) or not claim["member"]
                    or type(claim["row"]) is not int or claim["row"] < 2
                    or type(claim["column"]) is not int or claim["column"] < 1):
                return False
            meanings.add((claim["negative"], claim["positive"], claim["factor"]))
        return len(meanings) == 1
    except (ValueError, TypeError, KeyError):
        return False
