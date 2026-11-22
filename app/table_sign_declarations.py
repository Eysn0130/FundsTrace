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

DIRECTION_CODE_FIELD = "direction_codebook"
DIRECTION_CODE_VERSION = "source-direction-code-v1"
DIRECTION_HEADERS = frozenset(("收付方向", "收付标志", "收付标识", "收支方向", "借贷方向"))
DIRECTION_NOTE_HEADERS = frozenset(("备注", "说明", "方向说明", "收付方向说明", "收付方向码表"))
_DIRECTION_LABEL = "|".join(sorted(DIRECTION_HEADERS))
_DIRECTION_MENTION = re.compile(rf"(?:本表|本文件)(?:{_DIRECTION_LABEL})")
_DIRECTION_PATTERN = re.compile(
    rf"本表({_DIRECTION_LABEL}):?([01])(?:为|表示)(?:本方)?(支出|收入),"
    r"([01])(?:为|表示)(?:本方)?(支出|收入)[。.]?")


def normalized(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def looks_like(value):
    return isinstance(value, str) and bool(re.search(r"(?:负值|负数|正值|正数).*(?:收入|支出)", value))


def mentions_direction_codes(value):
    if not isinstance(value, str) or value.isascii():
        return False
    text = normalized(value)
    mention = _DIRECTION_MENTION.search(text)
    return mention is not None and (text.find("0", mention.end()) >= 0 or text.find("1", mention.end()) >= 0)


def direction_codes(value, header):
    """Read a complete binary declaration against its registered direction header."""
    if type(value) is not str or len(value) > 128 or type(header) is not str or len(header) > 64:
        return None
    found = _DIRECTION_PATTERN.fullmatch(normalized(value))
    if found is None:
        return None
    label, first, first_role, second, second_role = found.groups()
    header = normalized(header)
    if header not in DIRECTION_HEADERS or label not in {header, "收付方向"} or first == second or first_role == second_role:
        return None
    return {code: "in" if role == "收入" else "out" for code, role in ((first, first_role), (second, second_role))}


def validate_direction_codes(proof):
    """Check the portable proof using only its physical coordinates and source text."""
    if not isinstance(proof, dict) or set(proof) != {
            "version", "header_row", "last_row", "direction_column", "direction_header",
            "declaration_row", "declaration_column", "statement", "codes"} or proof["version"] != DIRECTION_CODE_VERSION:
        return False
    if (any(type(proof[field]) is not int or proof[field] < 1 for field in
            ("header_row", "last_row", "direction_column", "declaration_row", "declaration_column"))
            or not proof["header_row"] < proof["declaration_row"] <= proof["last_row"]
            or proof["direction_column"] == proof["declaration_column"]):
        return False
    codes = direction_codes(proof["statement"], proof["direction_header"])
    if codes is None or codes != proof["codes"]:
        return False
    try:
        return len(json.dumps(proof, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 1024
    except (TypeError, ValueError):
        return False


def compile_direction_region(rows, *, header_row, last_row, direction_column, direction_header, note_columns, literal, width=None):
    """Scan the supplied data rows and note columns in physical source order."""
    columns = sorted(set(note_columns))
    if not columns:
        return None
    proof = None
    invalid = False
    for index in range(header_row, min(last_row, len(rows))):
        row = rows[index]
        if not row or width is not None and len(row) != width:
            continue
        for column in columns:
            if not 1 <= column <= len(row):
                continue
            statement = literal(row[column - 1])
            if not mentions_direction_codes(statement):
                continue
            codes = direction_codes(statement, direction_header)
            if codes is None:
                invalid = True
            elif proof is None:
                proof = {"version": DIRECTION_CODE_VERSION, "header_row": header_row, "last_row": last_row,
                         "direction_column": direction_column, "direction_header": direction_header,
                         "declaration_row": index + 1, "declaration_column": column, "statement": statement,
                         "codes": codes}
            elif proof["codes"] != codes:
                return {"error": "conflicting_direction"}
    if invalid or proof is not None and (last_row > len(rows) or not validate_direction_codes(proof)):
        return {"error": "invalid_direction"}
    return {"codes": proof["codes"], "proof": proof} if proof is not None else None


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
