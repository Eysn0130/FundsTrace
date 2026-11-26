"""Closed, source-scoped sign declarations; no free-text direction inference."""
import json
import re
import unicodedata

VERSION = "source-sign-declaration-v1"
SCOPED_VERSION = "source-sign-declaration-v2"
DETAIL_FIELD = "source_sign_declaration"
NOTE_HEADERS = frozenset(("备注", "说明", "金额说明", "金额符号约定"))
LEG_HEADER = "腿别"
LEG_NOTE_HEADERS = NOTE_HEADERS | {"腿说明"}
LEG_OUTSIDE = "signed_leg_outside_scope"
LEG_UNPROVED = "signed_leg_unproved"
UNITS = {"元": "1", "分": "0.01", "万元": "10000"}
_PATTERN = re.compile(
    r"(本文件|本表)(交易金额|金额|amount)(负值|负数|正值|正数)表示本方(支出|收入),"
    r"绝对值为(万元|元|分);(负值|负数|正值|正数)表示本方(支出|收入)[。.]?")
_SCOPE_LIMIT = re.compile(
    r"(?:^|[;。])(?:本文件|本表区|本表)?金额符号(?:规则|约定|含义)"
    r"(?:仅|只)(?:适用(?:于)?|限(?:于)?)[^,;。]{1,64}(?:[;。]|$)")
_LEG_SCOPE = re.compile(
    r"(?:本表|本表区)腿别(?P<own>[-\w]{1,24})为本方腿;腿别(?P<peer>[-\w]{1,24})为对方腿;"
    r"金额符号(?:规则|约定)(?:仅|只)(?:适用于|限于)(?P=own)[。.]?")


def normalized(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def looks_like(value):
    return isinstance(value, str) and bool(re.search(r"(?:负值|负数|正值|正数).*(?:收入|支出)", value))


def limits_scope(value):
    """Recognize an explicit qualifier without guessing how to select its rows."""
    return isinstance(value, str) and "金额符号" in value and bool(_SCOPE_LIMIT.search(normalized(value)))


def _leg_scope(text):
    matched = _LEG_SCOPE.fullmatch(normalized(text)) if isinstance(text, str) else None
    if matched and matched["own"] != matched["peer"]:
        return matched.groupdict()
    return None


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


def compile_sources(layouts, *, guard_scope_limits=False, scoped_legs=False):
    """All declarations are collected before any monetary row is interpreted.

    A layout is one unambiguous sheet with a single financial header. File
    claims bind every financial sheet by the same exact amount header. A table
    claim binds only its physical sheet, never another archive member.
    """
    claims, limits = [], {}
    invalid = False
    for layout in layouts:
        for position, row in enumerate(layout["rows"], 1):
            for column, raw in enumerate(row, 1):
                if (guard_scope_limits or scoped_legs) and limits_scope(raw):
                    limits.setdefault(layout["member"], []).append((position, column, raw))
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
    if not claims and not invalid and not (scoped_legs and limits):
        return {}
    result = {}
    for layout in layouts:
        applicable = [x for x in claims if x["scope"] == "本文件" or x["member"] == layout["member"]]
        restrictions = limits.get(layout["member"], [])
        if not applicable and not invalid and not (scoped_legs and restrictions):
            continue
        malformed = invalid or not layout["single_region"] or not layout["amount_header"]
        malformed |= any(x["field"] != layout["amount_header"] for x in applicable)
        leg = None
        if limits and (restrictions or any(claim["scope"] == "本文件" for claim in applicable)):
            leg = (_restricted_leg(layout, restrictions)
                   if scoped_legs and applicable and all(claim["scope"] == "本表" for claim in applicable) else None)
            malformed |= leg is None
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
        data = {"version": SCOPED_VERSION if leg else VERSION, "claims": list(representatives.values())}
        if leg:
            data["scope"] = leg
        proof = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if len(proof.encode()) > 2048:
            result[layout["member"]] = {"error": "invalid_direction"}
        else:
            result[layout["member"]] = {"negative": negative, "positive": positive, "factor": factor,
                                        "proof": proof, **({"leg": leg} if leg else {})}
    return result


def _restricted_leg(layout, restrictions):
    leg_column = layout.get("leg_column")
    currency_column = layout.get("currency_column")
    if type(leg_column) is not int or type(currency_column) is not int or len(restrictions) != 1:
        return None
    row, column, text = restrictions[0]
    roles = _leg_scope(text)
    if (roles is None or row <= layout["header_row"] or column - 1 not in layout.get("leg_note_indices", ())
            or (row, column) in layout.get("invalid_cells", ())):
        return None
    return {**roles, "member": layout["member"], "header_row": layout["header_row"],
            "leg_column": leg_column + 1, "currency_column": currency_column + 1,
            "scope_row": row, "scope_column": column, "text": text}


def contract_for_row(contract, cells, source_row):
    """Apply the original sign convention within the independently declared leg."""
    if contract is None or (scope := contract.get("leg")) is None:
        return contract
    leg_index = scope["leg_column"] - 1
    currency_index = scope["currency_column"] - 1
    if max(leg_index, currency_index) >= len(cells) or source_row <= scope["header_row"]:
        return {"error": LEG_UNPROVED}
    raw = cells[leg_index]
    sign_leg = normalized(raw) if isinstance(raw, str) else ""
    if sign_leg == scope["peer"]:
        return {"error": LEG_OUTSIDE}
    if sign_leg != scope["own"] or not isinstance(cells[currency_index], str) or not cells[currency_index].strip():
        return {"error": LEG_UNPROVED}
    data = json.loads(contract["proof"])
    data["row"] = {"source_row": source_row, "leg_value": raw}
    proof = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(proof.encode()) > 2048:
        return {"error": LEG_UNPROVED}
    return {**contract, "proof": proof}


def validate_proof(value, *, scoped_legs=False, member=None, source_row=None):
    try:
        doc = json.loads(value)
        if not isinstance(doc, dict) or not isinstance(doc.get("claims"), list) or not doc["claims"]:
            return False
        if doc.get("version") == SCOPED_VERSION:
            if not scoped_legs or not _validate_restricted_row(doc, member, source_row):
                return False
        elif set(doc) != {"version", "claims"} or doc.get("version") != VERSION:
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


def _validate_restricted_row(doc, member, source_row):
    if set(doc) != {"version", "claims", "scope", "row"}:
        return False
    scope, row = doc["scope"], doc["row"]
    if not isinstance(scope, dict) or not isinstance(row, dict) or set(row) != {"source_row", "leg_value"}:
        return False
    keys = {"own", "peer", "member", "header_row", "leg_column", "currency_column", "scope_row", "scope_column", "text"}
    numbers = ("header_row", "leg_column", "currency_column", "scope_row", "scope_column")
    roles = _leg_scope(scope.get("text"))
    if (set(scope) != keys or roles != {key: scope[key] for key in ("own", "peer")}
            or scope["member"] != member or source_row != row["source_row"]
            or not all(type(scope[key]) is int and scope[key] >= 1 for key in numbers)
            or type(row["source_row"]) is not int or row["source_row"] <= scope["header_row"]
            or scope["scope_row"] <= scope["header_row"]
            or not isinstance(row["leg_value"], str) or normalized(row["leg_value"]) != scope["own"]):
        return False
    return all(isinstance(claim, dict) and claim.get("scope") == "本表"
               and claim.get("member") == member and type(claim.get("row")) is int
               and claim["row"] > scope["header_row"] for claim in doc["claims"])
