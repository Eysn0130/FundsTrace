"""Verify balance roles at their source and prepare a mapping only where admitted."""
import json

from .. import db, table_import
from ..api.models import RepreviewRequest
from ..table_mapping import identity_field
from . import assistance, import_ready_evidence, imports
from .cases import ServiceError


_QUESTION = "核对同一表区的本方原报余额，分开当前余额、可用余额和对方余额。余额时点须另核。"
_REPORTED_BALANCE_HEADERS = frozenset({"余额", "账户余额", "帐户余额", "交易余额", "账面余额",
                                      "当前余额", "本次余额", "联机余额", "本次交易余额"})
_OWN_ACCOUNT_HEADERS = frozenset({"本人账号", "本人帐号", "本方账号", "本方帐号", "交易账号",
                                  "账号", "帐号", "公司账号"})
_OWN_CARD_HEADERS = frozenset({"本方卡号", "交易卡号", "本人卡号", "卡号"})
_PEER_HEADERS = frozenset({"对方帐号", "对方账号", "交易对方账号", "对手账号", "对方卡号", "交易对方卡号"})
_MAX_ROWS = 64
_SOURCE_PROFILES = table_import.CSV_TAIL_ORDINARY_PROFILES | {table_import.VOUCHER_REFERENCE_PROFILE}
_SOURCE_ROLE_PROOF_VERSION = 2


def _numeric_rows(rows, column, factor):
    if factor is None:
        return []
    observed = []
    for number, cells in rows:
        if len(cells) < column or not (raw := cells[column - 1]["value"]).strip():
            continue
        amount, error = table_import._money(raw, {"factor": factor})
        if error:
            return []
        if amount is not None:
            observed.append(number)
    return observed


def _balance_column(header, rows, profile, *, source_only=False):
    names = [table_import._header_parts(cell["value"], profile) for cell in header]
    roles = table_import._candidates([cell["value"] for cell in header], profile)
    # Literal posting roles can establish a reported balance's source context
    # while the posting itself still lacks an admissible amount mapping.
    posting_context = roles.get("amount") or (source_only and all(
        len(roles.get(field, ())) == 1 for field in ("trade_time", "direction", "currency")))
    if not roles.get("trade_time") or not posting_context or roles.get("payer_account") or roles.get("payee_account"):
        return None
    peer_roles = {"counter_account", "counter_card"}
    account_columns = {item["index"] + 1 for item in roles.get("self_account", ())}
    card_columns = {item["index"] + 1 for item in roles.get("self_card", ())}
    peers = {item["index"] + 1 for field in peer_roles for item in roles.get(field, ())}
    for index, (name, _, _) in enumerate(names, 1):
        field = identity_field(name)
        if field == "self_card" or name in _OWN_CARD_HEADERS:
            card_columns.add(index)
        elif field == "self_account" or name in _OWN_ACCOUNT_HEADERS:
            account_columns.add(index)
        if field in peer_roles or name in _PEER_HEADERS:
            peers.add(index)
    # A legacy parser alias can label a physical card as an account.
    account_columns -= card_columns
    owner_columns = account_columns or card_columns
    owner_role = "account_column" if account_columns else "card_column"
    columns = [index for index, (name, _, _) in enumerate(names, 1) if name in _REPORTED_BALANCE_HEADERS]
    if not owner_columns or (not source_only and len(owner_columns) != 1) or not peers or len(columns) != 1:
        return None
    owner = min(owner_columns)
    [column] = columns
    if any(entry["index"] + 1 != column for entry in roles.get("balance", ())):
        return None
    populated = []
    for number, cells in rows:
        if len(cells) > len(header) or (not source_only and len(cells) != len(header)):
            return None
        if not any(cell["value"].strip() for cell in cells):
            continue
        if (len(cells) < max(owner_columns) or not (account := cells[owner - 1]["value"].strip())
                or any(cells[index - 1]["value"].strip() != account for index in owner_columns)):
            return None
        # Two explicitly labeled roles can report equal account text. Keep the
        # literal source role without granting a financial mapping in that case.
        if not source_only and any(index <= len(cells) and account == cells[index - 1]["value"].strip() for index in peers):
            return None
        populated.append((number, cells))
    factor = names[column - 1][1]
    numbers = _numeric_rows(populated, column, factor)
    return (column, factor, numbers, owner, owner_role) if numbers else None


def _source_layout(session, request, manifest, profile):
    members = manifest.get("layout_members", ())
    if (profile not in _SOURCE_PROFILES or session["suffix"] != ".csv" or len(members) != 1
            or request.get(imports._INTAKE_KEY) != imports._INTAKE_POLICY or request.get(imports._CONFIRMED_LAYOUT)
            or session["size_bytes"] > import_ready_evidence.import_layout_trial._MAX_FILE_BYTES):
        return None
    [entry] = members
    scope = entry["scope"]
    if (scope["member_path"] != "table.csv" or scope["header_rows"] not in ([], [1])
            or not 2 <= scope["source_rows"] <= _MAX_ROWS + 1):
        return None
    labels = {table_import._header_parts(label, profile)[0] for label in entry["document"]["columns"]}
    if not scope["header_rows"] or labels & (table_import.AVAILABLE_BALANCE_SOURCE_HEADERS | _REPORTED_BALANCE_HEADERS):
        return entry
    return None


def _verify_source_roles(session, entry, profile):
    scope = entry["scope"]
    with imports._source(session, snapshot=True) as original:
        physical = import_ready_evidence._cells(
            original, session, entry, set(range(1, scope["source_rows"] + 1)), profile, for_layout=True)
    header = physical.pop(1)
    return _source_roles_from_grid(scope, header, physical, profile, session["mode"])


def _source_roles_from_grid(scope, header, physical, profile, mode, *, preserve_executable_role=False):
    rows = sorted(physical.items())
    if (any(cell["kind"] not in {"text", "empty"} for cell in header)
            or any(len(cells) > len(header) for _, cells in rows)):
        return []
    headings = [cell["value"] for cell in header]
    names = [table_import._header_parts(label, profile) for label in headings]
    evidence = []

    def record(role, column, factor, numbers, **context):
        evidence.append({"member_path": scope["member_path"], "header_row": 1, "column": column,
                         "heading": headings[column - 1],
                         "source_rows": numbers, "role": role, "unit_factor": format(factor, "f"),
                         "book_balance_timing": "unproved", "financial_admission": False, **context})

    if (mode == "B" and (proof := _balance_column(header, rows, profile, source_only=True))
            and (profile in table_import.CSV_TAIL_ORDINARY_PROFILES or not scope["header_rows"]
                 or preserve_executable_role or _balance_column(header, rows, profile) is None)):
        column, factor, numbers, owner, owner_role = proof
        subaccounts = [i for i, (name, _, _) in enumerate(names, 1) if name == "子账户序号"]
        context = {owner_role: owner}
        if owner_role == "account_column" and len(subaccounts) == 1:
            [subaccount] = subaccounts
            if all(len(physical[number]) >= subaccount and physical[number][subaccount - 1]["value"].strip()
                   for number in numbers):
                context["subaccount_column"] = subaccount
        record("own_reported_balance", column, factor, numbers, **context)

    # The literal available-balance heading proves a balance kind. It supplies
    # neither a holder nor a booked-balance time reference. In an unregistered
    # layout, limit the assertion to a first row naming a transaction clock and
    # a separate reported balance.
    available = [(i, factor) for i, (name, factor, _) in enumerate(names, 1)
                 if name in table_import.AVAILABLE_BALANCE_SOURCE_HEADERS]
    if len(available) == 1:
        [(column, factor)] = available
        paired = any(name in _REPORTED_BALANCE_HEADERS | {"交易后余额", "本方交易后余额"} for name, _, _ in names)
        if scope["header_rows"] or paired and table_import._candidates(headings, profile).get("trade_time"):
            if numbers := _numeric_rows(rows, column, factor):
                record("available_balance", column, factor, numbers)
    return evidence


def _source_receipt(session, profile, manifest, roles):
    return {"sha256": session["sha256"], "profile": profile, "revision": manifest["revision"], "roles": roles,
            "proof_version": _SOURCE_ROLE_PROOF_VERSION}


def remember_source_roles(session, request, manifest, profile):
    """Keep a bounded source interpretation alongside the original ready revision."""
    request.pop(import_ready_evidence._SOURCE_BALANCE_ROLES, None)
    if entry := _source_layout(session, request, manifest, profile):
        try:
            roles = _verify_source_roles(session, entry, profile)
        except ServiceError as error:
            if error.code != "ready_evidence_unreadable":
                raise
            return
        if roles:
            request[import_ready_evidence._SOURCE_BALANCE_ROLES] = _source_receipt(session, profile, manifest, roles)


def _remembered(session, request, profile, *, include_legacy=False):
    receipt = request.get(import_ready_evidence._SOURCE_BALANCE_ROLES)
    if (not receipt or receipt["sha256"] != session["sha256"] or receipt["profile"] != profile
            or session["stage"] == "ready" and receipt["revision"] != request.get(import_ready_evidence._KEY, {}).get("revision")
            or not include_legacy and receipt.get("proof_version") != _SOURCE_ROLE_PROOF_VERSION):
        return []
    return receipt["roles"]


def _recheck_legacy_roles(session, found, recorded):
    path, headings, rows, registered, profile = found
    stored = {(role["role"], role["column"]): set(role["source_rows"])
              for role in recorded if role["member_path"] == path and role["header_row"] == 1}
    scope = {"member_path": path, "header_rows": [1] if registered else []}
    header = [{"kind": "text" if value else "empty", "value": value} for value in headings]
    physical = {number: [{"value": value} for value in cells] for number, cells in rows}
    restored = []
    for role in _source_roles_from_grid(scope, header, physical, profile, session["mode"], preserve_executable_role=True):
        positions = stored.get((role["role"], role["column"]), set())
        if matched := [number for number in role["source_rows"] if number in positions]:
            restored.append({**role, "source_rows": matched})
    return restored


def intake_notes(session, found):
    """Expose the saved roles after the ordinary source card verifies the original."""
    path, _, _, _, profile = found
    request = json.loads(session["request_json"] or "{}")
    roles = _remembered(session, request, profile, include_legacy=True)
    if roles and request[import_ready_evidence._SOURCE_BALANCE_ROLES].get("proof_version") != _SOURCE_ROLE_PROOF_VERSION:
        roles = _recheck_legacy_roles(session, found, roles)
    notes = []
    for role in roles:
        if role["member_path"] != path:
            continue
        column = role["column"]
        heading = role["heading"]
        positions = [column]
        if role["role"] == "own_reported_balance":
            text = f"原表第 {column} 列「{heading}」核为本方原报余额"
            for field, label in (("account_column", "账号"), ("card_column", "卡号")):
                if position := role.get(field):
                    positions.append(position)
                    text += f"，按第 {position} 列原{label}回查"
            if subaccount := role.get("subaccount_column"):
                positions.append(subaccount)
                text += f"，第 {subaccount} 列为原表子账户序号"
            text += "。笔后账面时点与财务准入另核。"
        else:
            text = (f"原表第 {column} 列「{heading}」核为可用余额。"
                    "持有人、账面余额和笔后账面时点须各自有据。")
        notes.append({"member_path": path, "header_row": 1, "columns": sorted(positions),
                      "rows": role["source_rows"], "text": text, "balance_role": role})
    return notes


def run(case_id, import_id, body):
    with db.transaction():
        session, manifest, profile = import_ready_evidence._ready(case_id, import_id, body.revision, for_layout=True)
        facts = {**import_ready_evidence._binding(session, manifest, profile),
                 "member_path": body.member_path, "header_row": body.header_row, "field": body.field}

        def outcome(reason, *, state="pending", records=(), prepared=None, source_roles=()):
            packet = assistance._packet("import", _QUESTION, facts, [], list(records), ("source_role", "coverage"))
            return {"state": state, "reason": reason, "packet": packet, "first_calls": 0, "recheck_calls": 0,
                    "prepared_preview": prepared, "source_balance_roles": list(source_roles)}

        stored = json.loads(session["request_json"] or "{}")
        members = manifest.get("layout_members", ())
        source_layout = _source_layout(session, stored, manifest, profile)
        mappable = (profile == table_import.VOUCHER_REFERENCE_PROFILE and session["suffix"] == ".csv"
                    and session["mode"] == "B" and len(members) == 1
                    and stored.get(imports._INTAKE_KEY) == imports._INTAKE_POLICY
                    and not (stored.get(imports._CONFIRMED_LAYOUT) or stored.get("_table_overrides") or stored.get("reference_policy")))
        if not (mappable or source_layout):
            return outcome("balance_outside_mappable_layout")
        [entry] = members
        scope = entry["scope"]
        if body.member_path != scope["member_path"] or body.header_row != 1:
            raise ServiceError(422, "outside_layout_review", "所选余额原件位置不在本次查阅范围")
        claim = body.candidate
        proof = None
        if (mappable and scope["header_rows"] == [1] and 2 <= scope["source_rows"] <= _MAX_ROWS + 1
                and not (source_layout and claim and claim.role == "available_balance")):
            with imports._source(session, snapshot=True) as original:
                physical = import_ready_evidence._cells(
                    original, session, entry, set(range(1, scope["source_rows"] + 1)), profile, for_layout=True)
            proof = _balance_column(physical[1], ((row, cells) for row, cells in physical.items() if row != 1), profile)
        if proof is None:
            if source_layout is None:
                return outcome("balance_not_proved" if mappable and scope["header_rows"] == [1]
                               and 2 <= scope["source_rows"] <= _MAX_ROWS + 1 else "balance_outside_mappable_layout")
            remembered = _remembered(session, stored, profile)
            if remembered:
                imports._source(session)
            roles = remembered or _verify_source_roles(session, source_layout, profile)
            selected = [role for role in roles if claim is None or (role["role"] == claim.role
                        and role["column"] == claim.column and claim.source_row in role["source_rows"])]
            if not selected:
                return outcome("balance_citation_rejected" if claim and roles else "balance_not_proved")
            if not remembered:
                stored[import_ready_evidence._SOURCE_BALANCE_ROLES] = _source_receipt(session, profile, manifest, roles)
                db.x("update import_sessions set request_json=? where import_id=?", [imports._json(stored), import_id])
            records = [{"id": f"C{role['column']}", "raw": {"heading": role["heading"]},
                        "structured": {"role_check": "supported"},
                        "locator": {"member_path": role["member_path"], "source_row": 1, "column": role["column"]}}
                       for role in selected]
            return outcome("source_balance_roles_verified", state="rule", records=records, source_roles=selected)
        column, factor, values, _, _ = proof
        if claim is not None and (claim.role != "own_reported_balance" or claim.column != column or claim.source_row not in values):
            return outcome("balance_citation_rejected")

        label = entry["document"]["columns"][column - 1]
        current_mapping = json.loads(session["mapping_json"])
        if current_mapping.get("balance") not in (None, "", label):
            return outcome("balance_conflicts_with_current_mapping")
        mapping = {**current_mapping, "balance": label}
        prepared = imports.repreview(case_id, import_id, RepreviewRequest(
            mode=session["mode"], mapping=mapping, ready_revision=body.revision))
        evidence = [item for item in prepared["table_summary"]["mapping_evidence"] if item["field"] == "balance"]
        if (prepared["mapping"] != mapping or prepared["mode"] != session["mode"] or len(evidence) != 1
                or evidence[0]["column_index"] != str(column)
                or evidence[0]["unit_factor"] != format(factor, "f")):
            raise ServiceError(409, "balance_preview_changed", "本方余额映射或金额单位与原表核证结果不一致")

        binding = {"column": column, "member_path": body.member_path, "header_row": body.header_row,
                   "source_rows": list(values), "unit_factor": format(factor, "f"), "interpretation": "own_reported_balance"}
        facts["source_balance_binding"] = binding
        prepared.update(automatic=False, source_balance_binding=binding)
        heading = physical[1][column - 1]["value"]
        record = {"id": f"C{column}", "raw": {"heading": heading}, "structured": {"role_check": "supported"},
                  "locator": {"member_path": body.member_path, "source_row": 1, "column": column}}
        return outcome("source_balance_binding_prepared", state="proposal" if claim else "rule",
                       records=(record,), prepared=prepared)
