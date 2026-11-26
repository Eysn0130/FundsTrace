"""Source-coordinate rules for adjacent, two-tier transaction headers."""

from bisect import bisect_left, bisect_right
import hashlib
import io
import json
import re
import unicodedata
import zipfile
from xml.parsers import expat


PARENT_REASON = "tiered_header_parent"
PROOF_FIELD = "tiered_header_evidence"
LEAF_PROOF_FIELD = "orphan_tier_evidence"
BINDING_FIELD = "tiered_header_binding"
DIRECTION_BINDING_FIELD = "tiered_direction_binding"
PROFILE = "funds-trace-table-v94"
GROUPED_PROFILE = "funds-trace-table-v100"
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

_GROUP_LABELS = {
    "time": ("交易信息", "交易明细", "交易要素", "交易", "transaction details", "transaction"),
    "clock": ("交易时间", "交易时钟", "transaction time"),
    "party": ("双方信息", "交易双方", "交易双方信息", "party details", "parties"),
    "account": ("账户信息", "account details"),
    "own": ("本方信息", "本方账户信息"),
    "counter": ("对方信息", "对手信息", "交易对手信息", "counterparty details"),
    "movement": ("发生额", "交易发生额", "借贷发生额", "收支发生额", "收支金额", "交易金额", "transaction amounts"),
    "balance": ("账户余额", "余额信息", "余额", "account balances", "balances"),
}
_TRANSACTION_CLOCK_COLUMNS = {
    "date": ("日期", "年月日", "交易日期", "交易发生日期", "交易日期时间", "date", "transaction date", "transaction datetime"),
    "clock": ("时刻", "时分秒", "时间", "交易时刻", "交易时分秒", "交易时间", "time", "transaction clock"),
}
_CURRENCY_COLUMNS = ("币种", "币别", "交易币种", "币种代码", "currency", "currency code", "ccy")
_OWN_COLUMNS = {
    "self_account": ("本方账卡", "本方账卡号", "本方账号", "本方帐号", "本方账户号", "own account"),
    "self_card": ("本方卡号", "own card number"),
    "self_name": ("本方名称", "本方户名", "own account name"),
}
_COUNTER_COLUMNS = {
    "counter_account": ("对方账卡", "对方账卡号", "对方账号", "对方帐号", "对手账号", "交易对方账号", "counterparty account"),
    "counter_card": ("对方卡号", "交易对方卡号", "counterparty card number"),
    "counter_name": ("对方名称", "对方户名", "对手户名", "交易对方名称", "counterparty name"),
    "counter_bank": ("对方开户行", "对方开户银行", "counterparty bank"),
}
_ACCOUNT_LEAVES = ("账号", "帐号", "账户号", "account", "account number")
_CARD_LEAVES = ("卡号", "card number")
_NAME_LEAVES = ("户名", "姓名", "名称", "account name")
_GROUP_COLUMN_LABELS = {
    "time": {
        **_TRANSACTION_CLOCK_COLUMNS,
        "currency": _CURRENCY_COLUMNS,
        "direction": ("收支方向", "借贷方向", "借贷标识", "收付标志", "debit/credit indicator"),
    },
    "clock": _TRANSACTION_CLOCK_COLUMNS,
    "party": {**_OWN_COLUMNS, **_COUNTER_COLUMNS},
    "account": {**_OWN_COLUMNS, **_COUNTER_COLUMNS, "currency": _CURRENCY_COLUMNS},
    "own": {
        **_OWN_COLUMNS, "currency": _CURRENCY_COLUMNS,
        "self_account": _OWN_COLUMNS["self_account"] + _ACCOUNT_LEAVES,
        "self_card": _OWN_COLUMNS["self_card"] + _CARD_LEAVES,
        "self_name": _OWN_COLUMNS["self_name"] + _NAME_LEAVES,
    },
    "counter": {
        **_COUNTER_COLUMNS,
        "counter_account": _COUNTER_COLUMNS["counter_account"] + _ACCOUNT_LEAVES,
        "counter_card": _COUNTER_COLUMNS["counter_card"] + _CARD_LEAVES,
        "counter_name": _COUNTER_COLUMNS["counter_name"] + _NAME_LEAVES,
    },
    "movement": {
        "income_amt": ("贷方", "贷方金额", "贷方发生额", "收入", "收入金额", "credit", "credit amount"),
        "expense_amt": ("借方", "借方金额", "借方发生额", "支出", "支出金额", "debit", "debit amount"),
    },
    "balance": {
        "balance": ("账面余额", "账面", "账簿余额", "book balance", "ledger balance"),
        "available_balance": ("可用余额", "可用", "available balance"),
    },
}
_GROUP_MONEY = frozenset(("movement", "balance"))
_GROUP_LEAF_DOMAINS = frozenset(("time", "movement"))
_GROUP_PARTY_DOMAINS = frozenset(("party", "account", "own", "counter"))
_GROUP_REQUIRED = frozenset(("date", "clock", "currency", "income_amt", "expense_amt"))
_GROUP_OWN_IDS = frozenset(("self_account", "self_card"))
_XLSX_MAX_COL, _XLSX_MAX_ROW = 16_384, 1_048_576
_MERGE_REF = re.compile(r"[A-Z]{1,3}[1-9][0-9]{0,6}(?::[A-Z]{1,3}[1-9][0-9]{0,6})?\Z")


def _token(value):
    return "" if type(value) is not str or len(value) > 64 else "".join(
        unicodedata.normalize("NFKC", value).casefold().split())


_GROUP_NAMES = {_token(label): role for role, labels in _GROUP_LABELS.items() for label in labels}
_GROUP_INITIALS = frozenset(label[0] for label in _GROUP_NAMES)
_GROUP_COLUMNS = {
    group: {_token(label): field for field, labels in fields.items() for label in labels}
    for group, fields in _GROUP_COLUMN_LABELS.items()
}
_GROUP_LEAVES = frozenset(label for columns in _GROUP_COLUMNS.values() for label in columns)


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


def _group_label(value):
    name, unit = _money_label(_token(value))
    return _GROUP_NAMES.get(name), unit


def grouped_parent(row, literal):
    """Prefilter worksheets without reading merge metadata for ordinary tables."""
    groups = set()
    for cell in row:
        value = literal(cell)
        if _has_initial(value, _GROUP_INITIALS) and (group := _group_label(value)[0]):
            groups.add(group)
            if len(groups) == 2:
                return True
    return False


def _group_leaf(value):
    name, _ = _money_label(_token(value))
    return name in _GROUP_LEAVES


def _group_cells(row, literal, is_heading):
    # Occupied cells with no supported label can contradict a group but
    # cannot declare a field. Their contents need not enter the journal.
    values = []
    for cell in row:
        value = literal(cell)
        blank = value is None and cell is None or type(value) is str and not value.strip()
        values.append(None if blank else value if is_heading(value) else False)
    return values


def _group_column(group, parent_unit, leaf):
    name, unit = _money_label(_token(leaf))
    field = _GROUP_COLUMNS[group].get(name)
    if not field:
        return None
    if group not in _GROUP_MONEY:
        return (field, "") if not (parent_unit or unit) else None
    if any(value and value not in _UNITS for value in (parent_unit, unit)):
        return None
    if parent_unit and unit and _UNITS[parent_unit] != _UNITS[unit]:
        return None
    return field, "人民币元" if "人民币元" in (parent_unit, unit) else unit or parent_unit


def _group_role(field):
    return "transaction_time" if field in {"date", "clock"} else field


def _group_proof(upper, lower, parent_row, ranges):
    anchors = {index: (group, unit) for index, label in enumerate(upper, 1)
               for group, unit in (_group_label(label),) if group}
    leaf_names = {_money_label(_token(label))[0] for label in lower}
    leaf_groups = {group for group, leaves in _GROUP_COLUMNS.items() if leaf_names.intersection(leaves)}
    if (len({group for group, _ in anchors.values()}) < 2 or not _GROUP_LEAF_DOMAINS <= leaf_groups
            or leaf_groups.isdisjoint(_GROUP_PARTY_DOMAINS)):
        return None

    width = len(upper)
    grouped, positions, fields, claims = {}, set(), set(), []
    disputed = False
    for span, (start, first_row, end, last_row) in enumerate(ranges):
        if first_row != parent_row or last_row != parent_row or start not in anchors or start == end:
            continue
        group, parent_unit = anchors[start]
        disputed |= (group in grouped or end > width or any(
            other != span and left <= end and start <= right
            for other, (left, _, right, _) in enumerate(ranges)))
        grouped[group] = start
        group_fields = set()
        for column in range(start, min(end, width) + 1):
            disputed |= column != start and upper[column - 1] is not None
            positions.add(column)
            label = lower[column - 1]
            match = _group_column(group, parent_unit, label)
            if match is None:
                disputed = True
                continue
            field, unit = match
            disputed |= field in fields
            fields.add(field)
            group_fields.add(field)
            claims.append([column, upper[start - 1], label, _group_role(field), unit])
        if group in {"account", "own"} and "currency" in group_fields:
            # A currency in the account group qualifies the ledger only when
            # its local sibling establishes the owner and no peer shares it.
            disputed |= group_fields.isdisjoint(_GROUP_OWN_IDS) or not group_fields.isdisjoint(_COUNTER_COLUMNS)
    disputed |= (set(anchors) != set(grouped.values())
                 or not _GROUP_REQUIRED <= fields or fields.isdisjoint(_GROUP_OWN_IDS)
                 or not fields & {"counter_account", "counter_card", "counter_name"}
                 or any(index not in positions and _group_leaf(label) for index, label in enumerate(lower, 1)))
    return {"version": 2, "parent_row": parent_row, "width": width, "status": "ambiguous" if disputed else "verified",
            "ranges": [list(bounds) for bounds in ranges], "parents": upper, "leaves": lower,
            "columns": sorted(claims)}


def inspect_grouped(parent, leaf, parent_row, literal, ranges=()):
    """Bind adjacent labels only inside the worksheet's physical merge spans."""
    if (leaf is None or len(parent) != len(leaf) or not 3 <= len(parent) <= MAX_WIDTH
            or not grouped_parent(parent, literal)):
        return None
    upper = _group_cells(parent, literal, lambda value: _group_label(value)[0] is not None)
    lower = _group_cells(leaf, literal, _group_leaf)
    proof = _group_proof(upper, lower, parent_row, sorted(ranges))
    if proof is not None and (len(proof["columns"]) > MAX_CLAIMS or len(encode(proof).encode("utf-8")) > MAX_BYTES):
        raise ValueError("分组表头证据超出安全处理范围")
    return proof


def _worksheet_merges(source, requested, namespace):
    from openpyxl.utils.cell import range_boundaries

    rows = sorted(requested)
    result = {row: [] for row in rows}
    worksheet, groups, item = (f"{namespace} {name}" for name in ("worksheet", "mergeCells", "mergeCell"))
    stack = []

    def start(element, attributes):
        if element == item and stack == [worksheet, groups]:
            ref = attributes.get("ref", "")
            if not _MERGE_REF.fullmatch(ref):
                raise ValueError("工作表合并位置无效")
            left, first, right, last = range_boundaries(ref)
            if not (1 <= left <= right <= _XLSX_MAX_COL and 1 <= first <= last <= _XLSX_MAX_ROW):
                raise ValueError("工作表合并位置无效")
            # Retain vertical as well as horizontal overlaps. A merge across
            # the two header rows cannot be treated as a horizontal parent.
            for index in range(bisect_left(rows, first - 1), bisect_right(rows, last)):
                ranges = result[rows[index]]
                if len(ranges) >= MAX_CLAIMS:
                    raise ValueError("分组表头合并数量超出安全处理范围")
                ranges.append((left, first, right, last))
        stack.append(element)

    def stop_doctype(*_):
        raise ValueError("工作表合并位置 XML 含不支持的声明")

    parser = expat.ParserCreate(namespace_separator=" ")
    parser.StartElementHandler = start
    parser.EndElementHandler = lambda _: stack.pop()
    parser.StartDoctypeDeclHandler = stop_doctype
    parser.ParseFile(source)
    return {row: tuple(sorted(ranges)) for row, ranges in result.items()}


def xlsx_merge_ranges(raw, requested):
    """Read merged geometry in the normal reader's 1-based worksheet order."""
    if not requested:
        return {}
    from openpyxl.reader.excel import ExcelReader
    from openpyxl.xml.constants import SHEET_MAIN_NS

    # Use the pinned reader's package/relationship resolution, skipping sheet
    # values, styles and shared strings. Open only exact ZIP members.
    try:
        reader = ExcelReader(io.BytesIO(raw), read_only=True, keep_links=False)
        with reader.archive as archive:
            infos = archive.infolist()
            if len(infos) > 2048 or sum(info.file_size for info in infos) > 300 * 1024 * 1024:
                raise ValueError("工作簿展开大小超出安全处理范围")
            names = set(reader.valid_files)
            if len(names) != len(infos):
                raise ValueError("工作簿成员位置存在歧义")
            reader.read_manifest()
            reader.read_workbook()
            worksheets = [rel for _, rel in reader.parser.find_sheets() if rel.target in names and "chartsheet" not in rel.Type]
            result = {}
            for index, rows in requested.items():
                if not 1 <= index <= len(worksheets):
                    raise ValueError("无法定位已读取的工作表")
                part = worksheets[index - 1]
                if part.TargetMode == "External" or not part.Type.endswith("/worksheet"):
                    raise ValueError("无法定位已读取的工作表")
                with archive.open(part.target) as source:
                    result[index] = _worksheet_merges(source, rows, SHEET_MAIN_NS)
            return result
    except (zipfile.BadZipFile, KeyError, OSError, EOFError, SyntaxError, expat.ExpatError) as exc:
        raise ValueError("无法核对工作表物理合并位置") from exc


def encode(proof):
    return json.dumps(proof, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def binding(member_sha, proof):
    return hashlib.sha256(encode([member_sha, proof]).encode("utf-8")).hexdigest()


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


def _checked_grouped_proof(proof):
    if (set(proof) != {"version", "parent_row", "width", "status", "ranges", "parents", "leaves", "columns"}
            or type(proof["parent_row"]) is not int or not 1 <= proof["parent_row"] < _XLSX_MAX_ROW
            or type(proof["width"]) is not int or not 3 <= proof["width"] <= MAX_WIDTH
            or proof["status"] not in ("verified", "ambiguous")):
        return None
    parent_row = proof["parent_row"]
    for heading in (proof["parents"], proof["leaves"]):
        if (type(heading) is not list or len(heading) != proof["width"]
                or any(cell is not None and cell is not False and type(cell) is not str for cell in heading)):
            return None
    ranges = proof["ranges"]
    if type(ranges) is not list or len(ranges) > MAX_CLAIMS:
        return None
    for span in ranges:
        if type(span) is not list or len(span) != 4 or any(type(value) is not int for value in span):
            return None
        left, first, right, last = span
        if (not 1 <= left <= right <= _XLSX_MAX_COL or not 1 <= first <= last <= _XLSX_MAX_ROW
                or first > parent_row + 1 or last < parent_row):
            return None
    claims = proof["columns"]
    if (type(claims) is not list or len(claims) > MAX_CLAIMS or any(
            type(claim) is not list or len(claim) != 5 or type(claim[0]) is not int for claim in claims)):
        return None
    upper = _group_cells(proof["parents"], lambda value: value, lambda value: _group_label(value)[0] is not None)
    lower = _group_cells(proof["leaves"], lambda value: value, _group_leaf)
    expected = _group_proof(upper, lower, parent_row, sorted(ranges))
    # A trustworthy pair can still have an external competing interpretation
    # in the active region. It can be blocked, but never upgraded to verified.
    if expected and proof["status"] == "ambiguous":
        expected["status"] = "ambiguous"
    return proof if proof == expected else None


def checked_proof(encoded, *, version=None):
    try:
        if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > MAX_BYTES:
            return None
        proof = json.loads(encoded)
    except (ValueError, TypeError, RecursionError):
        return None
    if type(proof) is not dict or type(proof.get("version")) is not int or (
            version is not None and proof["version"] != version):
        return None
    if proof["version"] == 2:
        grouped = _checked_grouped_proof(proof)
        return grouped if grouped is not None and encode(grouped) == encoded else None
    if (proof["version"] != 1 or set(proof) != {"version", "parent_row", "width", "status", "columns"}
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
    return proof if len(domains) >= required and encode(proof) == encoded else None


def candidate_columns(proof):
    """Yield the monetary factor and original labels used by the interpreter."""
    grouped = proof["version"] == 2
    for index, parent, leaf, role, unit in proof["columns"]:
        if role == "available_balance":
            continue
        field = "trade_time" if role in _TIME else role
        if grouped and role == "transaction_time" and _GROUP_COLUMNS["time"][_token(leaf)] == "clock":
            field = "time_part"
        yield (index - 1, field, role, unit, _UNITS.get(unit, "1"), parent, leaf)


def generic_account(value):
    return _token(value) in _IDS
