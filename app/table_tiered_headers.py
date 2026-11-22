"""Source-coordinate rules for adjacent, two-tier transaction headers."""

import hashlib
import json
import re
import unicodedata


PARENT_REASON = "tiered_header_parent"
PROOF_FIELD = "tiered_header_evidence"
LEAF_PROOF_FIELD = "orphan_tier_evidence"
BINDING_FIELD = "tiered_header_binding"
CODE_FIELD = "source_direction_code"
CODE_ISSUE_FIELD = "direction_codebook_issue"
PROFILE = "funds-trace-table-v88"
XLS_PROFILE = "funds-trace-table-v89"
EVIDENCE_PROFILE = "funds-trace-table-v90"
BANK_PROFILE = "funds-trace-table-v91"
BANK_XLS_PROFILE = "funds-trace-table-v92"
BANK_EVIDENCE_PROFILE = "funds-trace-table-v93"
PROFILES = frozenset((PROFILE, XLS_PROFILE, EVIDENCE_PROFILE, BANK_PROFILE, BANK_XLS_PROFILE, BANK_EVIDENCE_PROFILE))
OCCURRENCE_PROFILES = frozenset((EVIDENCE_PROFILE, BANK_EVIDENCE_PROFILE))
MAX_WIDTH = 256
MAX_CLAIMS = 16
MAX_BYTES = 4096

_PARENTS = {
    "交易": "transaction_time", "交易发生": "transaction_time", "transaction": "transaction_time",
    "入账": "posting_time", "记账": "posting_time", "posting": "posting_time",
    "付款方": "payer_account", "付款人": "payer_account", "payer": "payer_account",
    "收款方": "payee_account", "收款人": "payee_account", "payee": "payee_account",
    "本方": "self_account", "本方账户": "self_account", "own": "self_account",
    "对方": "counter_account", "交易对手": "counter_account", "counterparty": "counter_account",
    "本笔发生额": "amount", "本笔发生额复核": "amount", "交易金额": "amount", "transactionamount": "amount",
    "账面余额": "balance", "交易余额": "balance", "bookbalance": "balance",
    "收入金额": "income_amt", "支出金额": "expense_amt",
}
_TIME = frozenset(("transaction_time", "posting_time"))
_ACCOUNTS = frozenset(("payer_account", "payee_account", "self_account", "counter_account"))
_MONEY = frozenset(("amount", "balance", "income_amt", "expense_amt"))
_DATES = frozenset(("日期", "年月日", "日期时间", "时间", "date", "datetime", "time"))
_IDS = frozenset(("账号", "帐号", "账户号", "账户标识", "account", "accountidentifier", "accountnumber"))
_UNITS = {"元": "1", "人民币元": "1", "yuan": "1", "角": "0.1", "jiao": "0.1",
          "分": "0.01", "cent": "0.01", "cents": "0.01", "千元": "1000", "万元": "10000"}
_DIRECTIONS = frozenset(("方向", "收付方向", "借贷方向", "收支方向", "收付标志", "direction"))
_MONEY_LABEL = re.compile(r"([^()/\[\]]+)(?:\(([^()]+)\)|\[([^\[\]]+)\]|/([^/]+))\Z")
_PARENT_INITIALS = frozenset(key[0] for key in _PARENTS)
_LEAF_TERMS = {**dict.fromkeys(_DATES, "time"), **dict.fromkeys(_IDS, "account"),
               **dict.fromkeys(_UNITS, "money"), **dict.fromkeys(_DIRECTIONS, "direction")}
_LEAF_INITIALS = frozenset(key[0] for key in _LEAF_TERMS)


def _token(value):
    return "" if type(value) is not str or len(value) > 64 else "".join(
        unicodedata.normalize("NFKC", value).casefold().split())


def _has_initial(value, initials):
    if type(value) is not str or not value or len(value) > 64:
        return False
    return unicodedata.normalize("NFKC", value.lstrip()[:1]).casefold() in initials


def _money_label(token):
    if match := _MONEY_LABEL.fullmatch(token):
        unit = next(part for part in match.groups()[1:] if part is not None)
        return match[1], unit.removeprefix("单位").removeprefix(":")
    return token, ""


def _column(parent, leaf):
    base, declared = _money_label(parent)
    role = _PARENTS.get(base)
    if not declared and (role in _TIME and leaf in _DATES or role in _ACCOUNTS and leaf in _IDS):
        return role, ""
    if role not in _MONEY:
        return None
    if leaf in _UNITS:
        unit = leaf
    else:
        name, unit = _money_label(leaf)
        if _PARENTS.get(name) != role or not (declared or unit):
            return None
    if declared and (declared not in _UNITS or unit and _UNITS.get(unit) != _UNITS[declared]):
        return None
    unit = declared if declared == "人民币元" else unit or declared
    return (role, unit) if unit in _UNITS else None


def inspect(parent, leaf, parent_row, literal):
    """Return a closed proof or an ambiguous boundary, using only paired cells."""
    if leaf is None or len(parent) != len(leaf) or not 3 <= len(parent) <= MAX_WIDTH:
        return None
    upper = [literal(cell) for cell in parent]
    if not any(_possible_parent(value) for value in upper):
        return None
    lower = [literal(cell) for cell in leaf]
    columns, domains, potential_times, potential_money, potential_ids = [], set(), 0, 0, 0
    disputed = False
    for index, (raw_parent, raw_leaf) in enumerate(zip(upper, lower), 1):
        above, below = _token(raw_parent), _token(raw_leaf)
        role = _PARENTS.get(_money_label(above)[0])
        if matched := _column(above, below):
            columns.append([index, raw_parent, raw_leaf, *matched])
            domains.add("time" if role in _TIME else "account" if role in _ACCOUNTS else "money")
        elif (role or below in _DATES or below in _IDS or below in _UNITS):
            disputed = True
        potential_times += below in _DATES or role in _TIME
        potential_ids += below in _IDS or role in _ACCOUNTS
        potential_money += below in _UNITS or role in _MONEY
    lower_terms = {_token(value) for value in lower}
    # A real tier pair needs two independent categories of same-column
    # evidence. A damaged third category can mark a boundary, never a role.
    if (len(domains) < 2 or not potential_times or not potential_money
            or not (potential_ids >= 2 or potential_ids and lower_terms & _DIRECTIONS)):
        return None
    status = "ambiguous" if disputed or len(columns) > MAX_CLAIMS else "verified"
    proof = {"version": 1, "parent_row": parent_row, "width": len(parent), "status": status,
             "columns": columns[:MAX_CLAIMS]}
    if len(columns) > MAX_CLAIMS or len(encode(proof).encode("utf-8")) > MAX_BYTES:
        # Oversized labels mark a boundary, with one retained claim per type.
        by_domain = {}
        for claim in columns:
            by_domain.setdefault(_domain(claim[3]), claim)
        proof.update(status="ambiguous", columns=sorted(by_domain.values()))
    return proof


def _possible_parent(value):
    return _has_initial(value, _PARENT_INITIALS) and _money_label(_token(value))[0] in _PARENTS


def _domain(role):
    return "time" if role in _TIME else "account" if role in _ACCOUNTS else "money"


def encode(proof):
    return json.dumps(proof, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def binding(proof):
    return hashlib.sha256(encode(proof).encode("utf-8")).hexdigest()


def leaf_proof(row, source_row, literal):
    """Recognize generic leaves as a boundary without assigning their roles."""
    if not 4 <= len(row) <= MAX_WIDTH:
        return None
    witnesses = {"time": [], "account": [], "money": [], "direction": []}
    for index, value in enumerate(row, 1):
        value = literal(value)
        if not _has_initial(value, _LEAF_INITIALS):
            continue
        role = _LEAF_TERMS.get(_token(value))
        if role and len(witnesses[role]) < (2 if role == "account" else 1):
            witnesses[role].append([index, value])
    count = len(witnesses["account"])
    if not (witnesses["time"] and witnesses["money"] and (count == 2 or count == 1 and witnesses["direction"])):
        return None
    columns = witnesses["time"] + witnesses["account"] + witnesses["money"]
    if count == 1:
        columns += witnesses["direction"]
    return {"version": 1, "source_row": source_row, "width": len(row), "columns": sorted(columns)}


def checked_leaf_proof(encoded):
    try:
        proof = json.loads(encoded)
    except (ValueError, TypeError, RecursionError):
        return None
    if (type(proof) is not dict or set(proof) != {"version", "source_row", "width", "columns"}
            or type(proof["version"]) is not int or proof["version"] != 1
            or type(proof["source_row"]) is not int or proof["source_row"] < 1
            or type(proof["width"]) is not int or not 4 <= proof["width"] <= MAX_WIDTH
            or type(proof["columns"]) is not list or len(proof["columns"]) != 4):
        return None
    values = [None] * proof["width"]
    for claim in proof["columns"]:
        if type(claim) is not list or len(claim) != 2:
            return None
        index, value = claim
        if (type(index) is not int or not 1 <= index <= len(values)
                or values[index - 1] is not None or type(value) is not str):
            return None
        values[index - 1] = value
    return proof if leaf_proof(values, proof["source_row"], lambda value: value) == proof and encode(proof) == encoded else None


def checked_proof(encoded):
    try:
        proof = json.loads(encoded)
    except (ValueError, TypeError, RecursionError):
        return None
    if (type(proof) is not dict or set(proof) != {"version", "parent_row", "width", "status", "columns"}
            or type(proof["version"]) is not int or proof["version"] != 1
            or type(proof["parent_row"]) is not int or proof["parent_row"] < 1
            or type(proof["width"]) is not int or not 3 <= proof["width"] <= MAX_WIDTH
            or proof["status"] not in ("verified", "ambiguous") or type(proof["columns"]) is not list
            or not 2 <= len(proof["columns"]) <= MAX_CLAIMS):
        return None
    previous = 0
    domains = set()
    for claim in proof["columns"]:
        if type(claim) is not list or len(claim) != 5:
            return None
        index, parent, leaf, role, unit = claim
        if (type(index) is not int or not previous < index <= proof["width"]
                or _column(_token(parent), _token(leaf)) != (role, unit)):
            return None
        previous = index
        domains.add(_domain(role))
    required = 3 if proof["status"] == "verified" else 2
    return proof if len(domains) >= required and encode(proof) == encoded and len(encoded.encode("utf-8")) <= MAX_BYTES else None


def candidate_columns(proof):
    """Yield the monetary factor and original labels used by the interpreter."""
    for index, parent, leaf, role, unit in proof["columns"]:
        yield (index - 1, "trade_time" if role in _TIME else role, role,
               unit, _UNITS.get(unit, "1"), parent, leaf)


def generic_account(value):
    return _token(value) in _IDS
