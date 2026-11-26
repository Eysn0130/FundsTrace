"""Source-only direction proof for a registered, unadmitted table."""
import json

from app import table_import
from app.table_mapping import identity_field
from . import import_ready_evidence, imports


_PENDING = "registered_direction_pending"
_MAX_ROWS = 12
_MAX_COLUMNS = 32
_FIELDS = ("self_account", "direction", "amount")
_CREDIT_WORDS = frozenset({"贷", "贷方", "贷记", "收", "收方", "收入", "入", "入账"})
_DEBIT_WORDS = frozenset({"借", "借方", "借记", "付", "付方", "支出", "出", "出账"})


def pending_scope(summary, layout_members, *, mode):
    """Locate distinct registered roles when every source row is pending on direction."""
    if (summary["profile"] != table_import.VOUCHER_REFERENCE_PROFILE or mode != "B"
            or len(layout_members) != 1 or summary["accepted"] or len(summary["documents"]) != 1):
        return None
    [member] = layout_members
    scope = member["scope"]
    if scope["member_path"] != "table.csv" or scope["header_rows"] != [1]:
        return None
    count = scope["source_rows"] - 1
    if not 1 <= count <= _MAX_ROWS or summary["reason_counts"] != {"invalid_direction": count}:
        return None
    columns = {}
    for field in _FIELDS:
        proofs = [proof for proof in summary["mapping_evidence"] if proof["field"] == field
                  and proof["member_path"] == scope["member_path"] and proof["header_row"] == "1"]
        if len(proofs) != 1:
            return None
        [proof] = proofs
        if proof["candidate_count"] != "1" or proof["basis"] not in import_ready_evidence._DIRECT_RULES:
            return None
        columns[field] = int(proof["column_index"])
    return columns if len(set(columns.values())) == len(_FIELDS) else None


def _registered_columns(session, request):
    if (request.get(imports._INTAKE_KEY) != imports._INTAKE_POLICY
            or request.get(imports._CONFIRMED_LAYOUT) or request.get("_table_overrides")):
        return None
    if session["stage"] == "ready":
        return request.get(import_ready_evidence._KEY, {}).get(_PENDING)
    if session["stage"] == "done":
        summary = json.loads(session["result_json"] or "{}").get("table_summary")
        if summary is not None:
            members = import_ready_evidence._layout_members(summary, session)
            return pending_scope(summary, members, mode=session["mode"])
    return None


def _roles(headings, profile):
    candidates = table_import._candidates(headings, profile)
    if any(candidates.get(field) for field in ("income_amt", "expense_amt", "payer_account", "payee_account")):
        return None
    roles = {}
    for field in _FIELDS:
        choices = candidates.get(field, ())
        if len(choices) != 1:
            return None
        [candidate] = choices
        if field == "amount" and candidate["signed"]:
            return None
        roles[field] = candidate["index"] + 1
    if any(identity_field(heading) == "self_account" and number != roles["self_account"]
           for number, heading in enumerate(headings, 1)):
        return None
    return roles


def _verified_rows(rows, roles, *, allow_plain=False):
    direction, amount = roles["direction"], roles["amount"]
    owners, certified = set(), []
    compound_seen = False
    last_required_column = max(roles.values())
    for row, cells in rows:
        if len(cells) < last_required_column:
            return None
        owner = cells[roles["self_account"] - 1].strip()
        marker = table_import._enum(cells[direction - 1])
        words = tuple(part.strip() for part in marker.split("/"))
        value, issue = table_import._money(cells[amount - 1], {"factor": 1})
        if (not owner.isascii() or not owner.isdecimal() or not owner.strip("0")
                or not 1 <= len(words) <= 2 or value is None or issue or value <= 0):
            return None
        if (allow_plain and marker in table_import._IN) or all(word in _CREDIT_WORDS for word in words):
            side = "in"
        elif (allow_plain and marker in table_import._OUT) or all(word in _DEBIT_WORDS for word in words):
            side = "out"
        else:
            return None
        compound_seen |= len(words) == 2
        owners.add(owner)
        certified.append({"source_row": row, "direction": side, "amount_column": amount})
    return certified if (allow_plain or compound_seen) and len(owners) == 1 else None


def intake_proof(session, request, items, found):
    path, headings, rows, registered, profile = found
    if (session["suffix"] != ".csv" or path != "table.csv" or not registered
            or profile != table_import.VOUCHER_REFERENCE_PROFILE or len(items) != 1
            or not 1 < len(headings) <= _MAX_COLUMNS or any(len(cells) != len(headings) for _, cells in rows)):
        return None
    [item] = items
    if (item["member_path"] != path or item["member_sha256"] != session["sha256"] or item["header_row"] != 1
            or not 1 <= (count := item["row_count"]) <= _MAX_ROWS or item["last_row"] != count + 1
            or item["unresolved"] != count or item["accepted"] or item["reason"] != "invalid_direction"
            or item.get("status") == "withdrawn" or [row for row, _ in rows] != list(range(2, count + 2))):
        return None
    declared = _registered_columns(session, request)
    if not declared or (roles := _roles(headings, profile)) != declared:
        return None
    sheet = {"path": path, "rows": [headings, *(cells for _, cells in rows)]}
    if table_import._source_sign_contracts([sheet], profile).get(path):
        return None
    verified = _verified_rows(rows, roles)
    if verified is None:
        return None
    proof = {"scope": "source_only", "direction_column": roles["direction"], "amount_column": roles["amount"],
             "financial_admission": False, "rows": verified}
    return {"owner": roles["self_account"], "direction": roles["direction"], "amount": roles["amount"]}, proof


def retained_tail_proof(session, request, items, found):
    """Read the stated direction without granting a retained CSV any financial authority."""
    path, headings, rows, registered, profile = found
    if (profile != table_import.CSV_TAIL_SOURCE_PROFILE or not registered or not rows
            or session["suffix"] != ".csv" or path != "table.csv" or len(items) != 1
            or request.get(imports._INTAKE_KEY) != imports._INTAKE_POLICY):
        return None
    [item] = items
    if (item["member_path"] != path or item["member_sha256"] != session["sha256"] or item["header_row"] != 1
            or item["reason"] != table_import.CSV_TAIL_SOURCE_REASON or item["status"] == "withdrawn"
            or item["accepted"] or item["unresolved"] != len(rows)
            or [number for number, _ in rows] != list(range(2, item["last_row"] + 1))):
        return None
    source_roles = table_import.VOUCHER_REFERENCE_PROFILE
    roles = _roles(headings, source_roles)
    sheet = {"path": path, "rows": [headings, *(cells for _, cells in rows)]}
    if roles is None or table_import._source_sign_contracts([sheet], source_roles).get(path):
        return None
    verified = _verified_rows(rows, roles, allow_plain=True)
    if verified is None:
        return None
    columns = {"owner": roles["self_account"], "direction": roles["direction"], "amount": roles["amount"]}
    proof = {"scope": "source_only", "direction_column": roles["direction"], "amount_column": roles["amount"],
             "financial_admission": False, "rows": verified}
    return columns, proof
