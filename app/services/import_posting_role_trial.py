"""Verify original posting labels without granting financial admission."""
import json

from app import table_import
from app.table_mapping import identity_field
from . import assistance, import_ready_evidence, imports
from .cases import ServiceError


_QUESTION = "核对本账收付方向与本次发生额所用的原列。现金属性、对手和交易效力各自待核。"
_ROLES = {"owner": {"本人账号", "本人帐号"}, "direction": {"存取"}, "amount": {"发生额"}}
_MARKERS = {"存": "in", "取": "out"}
_MAX_COLUMNS = 32
_MAX_DATA_ROWS = 12


def _verified_columns(headings, profile, direction, amount):
    normalized = [table_import._norm(value) for value in headings]
    if not (1 <= direction <= len(headings) and 1 <= amount <= len(headings) and direction != amount):
        return None
    roles = {"direction": direction, "amount": amount}
    for role, position in roles.items():
        names = _ROLES[role]
        if normalized[position - 1] not in names or sum(name in names for name in normalized) != 1:
            return None
    owners = [i + 1 for i, name in enumerate(normalized) if name in _ROLES["owner"]]
    if len(owners) != 1:
        return None
    [owner] = owners
    competing = table_import._candidates(headings, profile)
    owner_candidates = {entry["index"] + 1 for entry in competing.get("self_account", ())}
    owner_candidates.update(i + 1 for i, heading in enumerate(headings) if identity_field(heading) == "self_account")
    if owner_candidates - {owner}:
        return None
    for field, position in roles.items():
        if any(entry["index"] + 1 != position for entry in competing.get(field, ())):
            return None
    if competing.get("income_amt") or competing.get("expense_amt"):
        return None
    return {**roles, "owner": owner}


def _certified_rows(headings, rows, roles):
    """Every original record must contain all proved posting-role cells."""
    width = len(headings)
    required = max(roles.values())
    result, accounts = [], set()
    for number, cells in rows:
        if not required <= len(cells) <= width:
            return None
        owner, marker, amount_text = (cells[roles[field] - 1].strip() for field in ("owner", "direction", "amount"))
        amount, issue = table_import._money(amount_text, {"factor": 1})
        if (not owner.isascii() or not owner.isdecimal() or not owner.strip("0") or marker not in _MARKERS
                or issue or amount is None or amount <= 0):
            return None
        accounts.add(owner)
        result.append({"source_row": number, "direction": _MARKERS[marker], "amount_column": roles["amount"]})
    return result if len(accounts) == 1 else None



def _source_scope(session, items, found):
    path, headings, rows, registered, profile = found
    if (session["suffix"] != ".csv" or profile != table_import.VOUCHER_REFERENCE_PROFILE
            or registered or path != "table.csv" or len(items) != 1
            or items[0]["member_path"] != path or items[0].get("status") == "withdrawn"
            or items[0].get("reason") != "unknown_table" or items[0].get("accepted") != 0
            or not 1 <= len(rows) <= _MAX_DATA_ROWS or not 1 < len(headings) <= _MAX_COLUMNS):
        return False
    return [number for number, _ in rows] == list(range(2, items[0]["row_count"] + 1))


def _posting_roles(session, request, items, found):
    if (request.get(imports._INTAKE_KEY) != imports._INTAKE_POLICY
            or request.get(imports._CONFIRMED_LAYOUT) or request.get("_table_overrides")
            or not _source_scope(session, items, found)):
        return None
    _, headings, rows, _, profile = found
    positions = {}
    for role in ("direction", "amount"):
        found_columns = [index for index, heading in enumerate(headings, 1)
                         if table_import._norm(heading) in _ROLES[role]]
        if len(found_columns) != 1:
            return None
        positions[role] = found_columns[0]
    roles = _verified_columns(headings, profile, positions["direction"], positions["amount"])
    if roles is None or (certified := _certified_rows(headings, rows, roles)) is None:
        return None
    result = {"scope": "source_only", "direction_column": roles["direction"], "amount_column": roles["amount"],
              "rows": certified, "financial_admission": False}
    return roles, result


def run(case_id, import_id, body):
    from . import import_direction_role_decision

    session, manifest, profile = import_ready_evidence._ready(case_id, import_id, body.revision, for_layout=True)
    facts = {**import_ready_evidence._binding(session, manifest, profile), "member_path": body.member_path,
             "header_row": body.header_row, "field": body.field}

    def answer(reason, *, ruled=False, records=()):
        packet = assistance._packet("import", _QUESTION, facts, [], list(records), ("source_role", "coverage"))
        return {"state": "rule" if ruled else "pending", "reason": reason, "packet": packet,
                "first_calls": 0, "recheck_calls": 0}

    request = json.loads(session["request_json"] or "{}")
    members = manifest.get("layout_members", ())
    if (session["suffix"] != ".csv" or profile != table_import.VOUCHER_REFERENCE_PROFILE
            or request.get(imports._INTAKE_KEY) != imports._INTAKE_POLICY
            or request.get(imports._CONFIRMED_LAYOUT) or request.get("_table_overrides") or len(members) != 1):
        return answer("source_posting_scope_incomplete")
    [entry] = members
    scope = entry["scope"]
    if body.member_path != scope["member_path"] or body.member_path != "table.csv" or body.header_row != 1:
        raise ServiceError(422, "outside_layout_review", "所选原件位置不在本次查阅范围")
    registered = bool(scope["header_rows"])
    if registered and (scope["header_rows"] != [1] or not manifest.get(import_direction_role_decision._PENDING)):
        return answer("source_posting_already_registered")
    last = scope["source_rows"]
    if (not 1 < last <= _MAX_DATA_ROWS + 1
            or session["size_bytes"] > import_ready_evidence.import_layout_trial._MAX_FILE_BYTES):
        return answer("source_posting_scope_incomplete")
    with imports._source(session, snapshot=True) as original:
        physical = import_ready_evidence._cells(original, session, entry, set(range(1, last + 1)), profile, for_layout=True)
    import_ready_evidence._ready(case_id, import_id, body.revision, for_layout=True)
    header = physical[1]
    if any(cell["kind"] != "text" for cell in header):
        return answer("source_posting_scope_incomplete")
    headings = [cell["value"] for cell in header]
    rows = [(number, [cell["value"] for cell in physical[number]]) for number in range(2, last + 1)]
    facts.update(last_row=last, physical_columns=len(headings), source_rows=[number for number, _ in rows])
    found = body.member_path, headings, rows, registered, profile
    items = request.get(imports._INTAKE_ITEMS, ())
    proof = (import_direction_role_decision.intake_proof(session, request, items, found) if registered
             else _posting_roles(session, request, items, found))
    if proof is None:
        return answer("source_posting_roles_unproved")
    columns, roles = proof
    facts["source_posting_roles"] = roles
    records = [{"id": f"C{column}", "raw": {"heading": headings[column - 1]}, "structured": {},
                "locator": {"member_path": body.member_path, "source_row": 1, "column": column}}
               for column in sorted(columns.values())]
    records.extend({"id": f"R{number}", "raw": {}, "structured": {},
                    "locator": {"member_path": body.member_path, "source_row": number}} for number, _ in rows)
    return answer("source_posting_roles_rule", ruled=True, records=records)


def _source_boundary_notes(session, items, found):
    path, headings, rows, _, profile = found
    request = json.loads(session["request_json"] or "{}")
    if (request.get(imports._INTAKE_KEY) != imports._INTAKE_POLICY or not _source_scope(session, items, found)
            or any(len(row) > len(headings) for _, row in rows)):
        return []
    names = [table_import._norm(value) for value in headings]
    owners = [i + 1 for i, name in enumerate(names) if name in _ROLES["owner"]]
    missing_labels = {"对手账号": {"对方账号", "对手账号"}, "对手户名": {"对方户名", "对手户名"}, "币种": {"币种", "交易币种"}}
    positioned = {field: [i + 1 for i, name in enumerate(names) if name in labels]
                  for field, labels in missing_labels.items()}
    if len(owners) != 1 or any(not columns for columns in positioned.values()):
        return []
    [owner] = owners
    if any(len(row) < owner or not row[owner - 1].strip() for _, row in rows):
        return []
    notes = []
    def note(columns, positions, message):
        notes.append({"member_path": path, "header_row": 1, "columns": sorted(set(columns)), "rows": positions, "text": message})
    def populated(column):
        return [number for number, row in rows if len(row) >= column and row[column - 1].strip()]
    referenced = {owner}
    for field, columns in positioned.items():
        for column in columns:
            blank_rows = [number for number, row in rows if len(row) >= column and not row[column - 1].strip()]
            absent_rows = [number for number, row in rows if len(row) < column]
            if blank_rows:
                note([column], blank_rows, f"第 {column} 列的这些{field}原格为空。")
            if absent_rows:
                note([column], absent_rows, f"第 {column} 列的这些{field}原格缺失，未补空值。")
            referenced.add(column)

    candidates = table_import._candidates(headings, profile)
    identity_roles = {"counter_account", "counter_card", "counter_name"}
    alternatives = {
        "对手标识": {entry["index"] + 1 for field in identity_roles for entry in candidates.get(field, ())}
                   | {i + 1 for i, heading in enumerate(headings) if identity_field(heading) in identity_roles}
                   | set(positioned["对手账号"]) | set(positioned["对手户名"]),
        "币种": {entry["index"] + 1 for entry in candidates.get("currency", ())}
                 | {i + 1 for i, name in enumerate(names) if name in {"币别", "币种代码", "货币类型"}},
        "现金标志": {i + 1 for i, name in enumerate(names)
                     if name in {"现金标志", "现金属性", "是否现金", "现转标志", "现转"}},
        "交易状态": {entry["index"] + 1 for entry in candidates.get("status", ())}
                     | {i + 1 for i, name in enumerate(names) if name in {"交易效力", "交易有效性", "有效标志"}},
    }
    for field, columns in alternatives.items():
        for column in sorted(columns):
            if positions := populated(column):
                note([column], positions, f"第 {column} 列的{field}有原值，原意和适用范围另核。")
                referenced.add(column)
    numbers = [number for number, _ in rows]
    note(referenced, numbers, "该表区仍保留为原材料。对手识别、现金属性及交易效力各按原证继续核对，整区尚无正式财务准入。")
    return notes


def intake_notes(session, items, found):
    """Check current originals for source roles and separate unavailable facts."""
    from . import import_direction_role_decision

    boundary = _source_boundary_notes(session, items, found)
    request = json.loads(session["request_json"] or "{}")
    proof = (_posting_roles(session, request, items, found)
             or import_direction_role_decision.intake_proof(session, request, items, found)
             or import_direction_role_decision.retained_tail_proof(session, request, items, found))
    if proof is None:
        return boundary, None
    roles, certified = proof
    path, headings, rows, registered, profile = found
    notes = []
    def note(columns, positions, message):
        notes.append({"member_path": path, "header_row": 1, "columns": sorted(columns), "rows": positions, "text": message})
    columns = [roles["owner"], roles["direction"], roles["amount"]]
    for side, term in (("in", "入账"), ("out", "出账")):
        group = [item["source_row"] for item in certified["rows"] if item["direction"] == side]
        if group:
            note(columns, group, f"这些原行按原表所报方向记为本方{term}，发生额按第 {roles['amount']} 列原格回查。")
    numbers = [number for number, _ in rows]
    if profile == table_import.CSV_TAIL_SOURCE_PROFILE:
        candidates = table_import._candidates(headings, table_import.VOUCHER_REFERENCE_PROFILE)
        balances = candidates.get("balance", ())
        if len(balances) == 1:
            balance_column = balances[0]["index"] + 1
            if balance_column != roles["amount"] and all(len(cells) >= balance_column for _, cells in rows):
                note([roles["amount"], balance_column], numbers,
                     f"第 {roles['amount']} 列是发生额，第 {balance_column} 列是本账余额，两列不可互代。余额时点仍需原据。")
        for field in ("currency", "status"):
            matching = candidates.get(field, ())
            if len(matching) != 1:
                continue
            [candidate] = matching
            column = candidate["index"] + 1
            observed = [(number, cells[column - 1].strip()) for number, cells in rows
                        if len(cells) >= column and cells[column - 1].strip()]
            if observed and field == "currency":
                note([column], [number for number, _ in observed], f"第 {column} 列记录原表币种，可按原格回查。")
            elif observed and all(code.isascii() and code.isdecimal() for _, code in observed):
                note([column], [number for number, _ in observed],
                     f"第 {column} 列记录交易状态原码，交易效力需核对适用的状态码说明。")
    else:
        balances = [column for column, heading in enumerate(headings, 1) if "余额" in table_import._norm(heading)]
        if len(balances) == 1:
            note([roles["amount"], *balances], numbers, "发生额与余额类原列分别回查。余额时点及口径仍需独立证据。")
    if registered and profile == table_import.VOUCHER_REFERENCE_PROFILE:
        note([roles["direction"]], numbers, "收付已按原件核明，当前正式导入解析尚未采用这组组合写法。")
        currencies = table_import._candidates(headings, profile).get("currency", ())
        if len(currencies) == 1:
            [currency] = currencies
            index = currency["index"]
            codes = [cells[index].strip() for _, cells in rows]
            if not currency.get("coded_bank_csv") and all(code.isascii() and code.isdecimal() for code in codes):
                note([index + 1], numbers, "币种栏为数字原码。正式导入还需核对适用于本表区的币种码表。")
    if not boundary:
        note(columns, numbers, "对手、现金属性及交易效力分别待核，整区尚无正式财务准入。")
    return [*notes, *boundary], roles["direction"]
