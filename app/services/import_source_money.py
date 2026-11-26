"""Retain source-only monetary field decisions with the ordinary import receipt."""
import json

from .. import table_import
from . import import_balance_trial, import_layout_trial, import_ready_evidence, import_verbatim_money
from .cases import ServiceError


RECEIPT_KEY = "_source_money_roles_v1"
_ROLES = {
    "amount": "原报交易金额", "currency": "来源币种", "balance": "原报余额",
    "converted_cny": "人民币折算金额", "converted_usd": "美元折算金额",
    "converted_unspecified": "换算金额",
    "transfer_in_amount": "转入相关货币金额", "transfer_out_amount": "转出相关货币金额",
    "other_amount": "其他金额原列", "other_currency": "其他币种原列",
}
_MAIN_FIELDS = ("amount", "currency", "balance", "converted_cny", "converted_usd")


def _positions(rows, column):
    result = {"value_rows": [], "blank_rows": [], "missing_rows": []}
    for number, cells in sorted(rows.items()):
        if len(cells) < column:
            result["missing_rows"].append(number)
        elif cells[column - 1]["value"].strip():
            result["value_rows"].append(number)
        else:
            result["blank_rows"].append(number)
    return result


def _fields(header, rows, profile, *, registered=True):
    headings = [cell["value"] for cell in header]
    columns = range(1, len(header) + 1)
    candidates = table_import._candidates(headings, profile)
    if not candidates.get("trade_time") or any(len(cells) > len(header) for cells in rows.values()):
        return []
    if import_verbatim_money.has_later_header(rows, profile):
        return []
    located = import_verbatim_money.source_columns(headings, rows, profile, "amount_roles")
    if not any(match for match, _ in located.values()):
        return []
    if not registered:
        amount, _ = located.get("amount", (None, ()))
        balance, _ = located.get("balance", (None, ()))
        if (not amount or not balance or not candidates.get("direction")
                or any(len(row) != len(header) for row in rows.values())
                or not import_balance_trial._numeric_rows(sorted(rows.items()), amount[0], amount[1])):
            return []
    result = []
    for role in _MAIN_FIELDS:
        determined = role in located
        match, unit_fields = located.get(role, (None, ()))
        if role not in {"amount", "balance", "currency"} and determined and not match:
            continue
        if match is None:
            proved_absence = registered and determined
            result.append({"role": role, "state": "absent" if proved_absence else "unproved",
                           "unit_status": "not_applicable" if proved_absence else "unproved"})
            continue
        column, _, _ = match
        if role in {"amount", "balance"}:
            scale = import_verbatim_money._column_scale(role, match, rows, unit_fields, columns)
            unit_status = ("not_declared" if scale == "NOT_DECLARED" else "unproved"
                           if scale in {None, "UNPROVED"} else "declared")
        else:
            scale = None
            unit_status = "not_applicable" if role == "currency" else "not_declared"
        field = {"role": role, "state": "located", "column": column,
                 "unit_status": unit_status, **_positions(rows, column)}
        if unit_status == "declared":
            field["declared_unit"] = scale
        if unit_fields:
            field["unit_columns"] = [item["index"] + 1 for item in unit_fields]
        result.append(field)
    seen = {field.get("column") for field in result}
    for column, heading in enumerate(headings, 1):
        name = table_import._norm(heading)
        if role := import_verbatim_money.related_amount_role(name):
            result.append({"role": role, "state": "located", "column": column,
                           "unit_status": "not_declared", **_positions(rows, column)})
        elif column not in seen and import_verbatim_money._physically_blank(rows, column):
            if import_verbatim_money._could_name_role("amount", name):
                role = "other_amount"
            elif import_verbatim_money._could_name_role("currency", name):
                role = "other_currency"
            if role:
                result.append({"role": role, "state": "located", "column": column,
                               "unit_status": "unproved" if role == "other_amount" else "not_applicable",
                               **_positions(rows, column)})
    return result


def remember(session, request, manifest, profile):
    """Called inside the ordinary ready transaction, using the registered bytes."""
    from . import imports

    request.pop(RECEIPT_KEY, None)
    members = manifest.get("layout_members", ())
    if (request.get(imports._INTAKE_KEY) != imports._INTAKE_POLICY or request.get("_table_overrides")
            or request.get(imports._CONFIRMED_LAYOUT) or session["suffix"] != ".csv"
            or profile not in import_balance_trial._SOURCE_PROFILES or len(members) != 1
            or session["size_bytes"] > import_layout_trial._MAX_FILE_BYTES):
        return
    [entry] = members
    scope = entry["scope"]
    if (scope["member_path"] != "table.csv" or scope["header_rows"] not in ([], [1])
            or not 2 <= scope["source_rows"] <= import_balance_trial._MAX_ROWS + 1):
        return
    positions = set(range(1, scope["source_rows"] + 1))
    try:
        with imports._source(session, snapshot=True) as source:
            grid = import_ready_evidence._cells(source, session, entry, positions, profile, for_layout=True)
    except ServiceError as error:
        if error.code != "ready_evidence_unreadable":
            raise
        return
    if set(grid) != positions or any(cell["kind"] not in {"text", "empty"} for cell in grid[1]):
        return
    header = grid.pop(1)
    registered = scope["header_rows"] == [1]
    roles = _fields(header, grid, profile, registered=registered)
    # Ordinary single-amount tables already carry their source mapping.
    # Record an extra interpretation where separate monetary roles or a
    # physically present, entirely unpopulated balance need to be shown.
    extra_interpretation = (not registered or any(
        table_import.explicit_converted_amount_currency(cell["value"]) is not None
        or import_verbatim_money.related_amount_role(table_import._norm(cell["value"]))
        for cell in header
    ) or any(field["role"] == "balance" and field["state"] == "located" and not field["value_rows"] for field in roles))
    if roles and extra_interpretation:
        request[RECEIPT_KEY] = {"sha256": session["sha256"], "profile": profile,
                                "revision": manifest["revision"], "member_path": scope["member_path"],
                                "parser_registered_header": registered, "roles": roles}


def _text(field):
    label = _ROLES[field["role"]]
    if field["state"] == "absent":
        return f"本表区无{label}列。"
    if field["state"] == "unproved":
        return f"{label}列待核。"
    units = {"not_applicable": "", "not_declared": "原列未明示缩放单位。",
             "unproved": "适用的缩放单位待核。"}
    status = field["unit_status"]
    scale = (import_verbatim_money._UNITS[field["declared_unit"]] + "。"
             if status == "declared" else units[status])
    rule = {"currency": "按原格保留币种原值，原码含义须有适用依据。",
            "converted_cny": "按该折算原列回查，汇率另核。",
            "converted_usd": "按该折算原列回查，汇率另核。",
            "converted_unspecified": "折算目标币种与汇率待核。",
            "other_amount": "具体用途按本列原表头核对。",
            "other_currency": "具体用途按本列原表头核对。"}.get(field["role"], "")
    presence = "".join(
        f"第 {'、'.join(map(str, field[key]))} 行{description}。"
        for key, description in (("blank_rows", "原格为空"), ("missing_rows", "缺少原格")) if field[key]
    )
    return f"{label}。{scale}{rule}{presence}"


def intake_notes(session, found):
    """The caller has re-opened and verified the active registered original."""
    path, _, _, registered, profile = found
    request = json.loads(session["request_json"] or "{}")
    receipt = request.get(RECEIPT_KEY)
    if (not receipt or receipt.get("parser_registered_header") != registered or receipt["sha256"] != session["sha256"]
            or receipt["profile"] != profile or receipt["member_path"] != path
            or session["stage"] == "ready"
            and receipt["revision"] != request.get(import_ready_evidence._KEY, {}).get("revision")):
        return []
    notes = []
    for field in receipt["roles"]:
        columns = [field["column"], *field.get("unit_columns", ())] if "column" in field else []
        notes.append({"member_path": path, "header_row": 1, "columns": sorted(set(columns)),
                      "rows": field.get("value_rows", []), "text": _text(field), "money_role": field})
    return notes
