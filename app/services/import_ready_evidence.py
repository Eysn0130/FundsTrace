"""Bounded source excerpts and rule-only column proof for one ready preview."""
from bisect import bisect_left
from collections import Counter, defaultdict
import csv
import json
import re
import uuid
import zipfile

from app import db, import_originals, table_import, table_tiered_headers
from app.table_mapping import ACCOUNT_CARD_FIELDS, document_number_header, identity_field
from . import import_layout_trial
from .cases import ServiceError


_KEY = "_ready_evidence_v1"
_SOURCE_BALANCE_ROLES = "_source_balance_roles_v1"
_MAX_MANIFEST_BYTES = 256 * 1024
_DIRECT_RULES = {"exact_header_alias", "explicit_identity_role", "role_qualified_header", "verified_region_header"}
_ROLE_FIELDS = ACCOUNT_CARD_FIELDS | {"self_name", "counter_name", "payer_name", "payee_name"}
_POSITIONAL_ROLES = {
    "self_account": {"交易账号", "交易帐号"},
    "self_card": {"本方卡号", "交易卡号"},
    "counter_card": {"对方卡号", "对手卡号", "交易对方卡号"},
}
_NON_LEDGER_HEADINGS = {"备注", "摘要", "说明", "remark", "memo"}


def column_role_refutation(field, heading):
    if field in ACCOUNT_CARD_FIELDS and document_number_header(heading):
        return "document_number_column"
    if heading in _NON_LEDGER_HEADINGS:
        return "incompatible_source_role"
    return None


def _manifest(summary, previews, *, automatic):
    # These are the same bounded, physical source positions already published
    # by the preview. Neither a model nor a caller defines a new table region.
    sample_rows = defaultdict(set)
    for row in previews:
        if row.get("source_member") and row.get("source_row"):
            sample_rows[row["source_member"]].add(row["source_row"])
    for entry in summary["unresolved_examples"]:
        if entry["reason_code"] != "ambiguous_region_header":
            sample_rows[entry["member_path"]].add(entry["source_row"])

    regions = {}
    total = 0
    partial = any(summary["reason_counts"].get(reason) for reason in ("unknown_table", "ambiguous_region_header"))
    members = {m["member_path"]: m["encoding"] for m in summary["members"] if m["role"] == "table"}
    for document in summary["documents"]:
        member = document["member_path"]
        if member not in members:
            continue
        header_rows = document["header_rows"]
        total += len(header_rows)
        if not header_rows:
            partial = True
            continue
        parent_rows = document.get("tier_parent_rows", [])
        by_header = defaultdict(list)
        for row in sorted(sample_rows.get(member, ())):
            idx = bisect_left(header_rows, row)
            parent_idx = bisect_left(parent_rows, row)
            if (idx and (idx == len(header_rows) or header_rows[idx] != row)
                    and (parent_idx == len(parent_rows) or parent_rows[parent_idx] != row)):
                by_header[header_rows[idx - 1]].append(row)
        for header, rows in by_header.items():
            parent_idx = bisect_left(parent_rows, header - 1)
            parent = [header - 1] if parent_idx < len(parent_rows) and parent_rows[parent_idx] == header - 1 else []
            scope = {"member_path": member, "sheet": document["sheet"], "header_row": header,
                     "header_rows": [*parent, header], "sample_rows": rows}
            regions[(member, header)] = {
                "scope": scope, "encoding": members[member], "rules": {},
                # CSV delimiter detection needs the document's first header.
                # For cell lookup each request stays inside this one region.
                "document": {"header_row": document["header_row"], "columns": document["columns"],
                             "sheet": document["sheet"], "header_rows": [header], "tier_parent_rows": parent},
            }

    if automatic:
        for proof in summary["mapping_evidence"]:
            region = regions.get((proof["member_path"], int(proof["header_row"])))
            field = proof["field"]
            if region is None or field not in _ROLE_FIELDS:
                continue
            rules = region["rules"]
            # Keep ambiguity, including candidates with unsupported provenance.
            rules[field] = (proof if field not in rules and proof["candidate_count"] == "1"
                            and proof["basis"] in _DIRECT_RULES else None)
    return {"revision": uuid.uuid4().hex, "all_regions_sampled": not partial and len(regions) == total,
            "regions": list(regions.values())}


def _layout_members(summary, session):
    if session["suffix"] not in {".csv", ".txt", ".tsv", ".xlsx"}:
        return []
    encodings = {member["member_path"]: member["encoding"] for member in summary["members"] if member["role"] == "table"}
    return [
        {"scope": {key: document[key] for key in ("member_path", "sheet", "source_rows", "header_rows")},
         "document": {**{key: document[key] for key in ("header_row", "columns", "sheet", "header_rows")},
                      "tier_parent_rows": document.get("tier_parent_rows", [])},
         "encoding": encodings[document["member_path"]]}
        for document in summary["documents"]
        if document["source_rows"] and document["member_path"] in encodings
    ]


def remember_preview(import_id, result):
    """Called by the owning import service in its ready-state transaction."""
    from . import import_balance_trial, import_direction_role_decision, import_source_money, imports

    if not (summary := result.get("table_summary")):
        return
    stored = db.q_raw("""select import_id,request_json,suffix,mode,sha256,size_bytes,source_state
        from import_sessions where import_id=?""", [import_id])[0]
    request = json.loads(stored["request_json"] or "{}")
    # A manual override may have eliminated a competing automatic candidate.
    # This generation can expose cells, but cannot prove an independent role.
    manifest = _manifest(summary, result["preview"], automatic=not request.get("_table_overrides"))
    layout_members = _layout_members(summary, stored)
    if layout_members:
        manifest["layout_members"] = layout_members
        manifest["layout_source_rows"] = sum(doc["source_rows"] for doc in summary["documents"])
        if (request.get(imports._INTAKE_KEY) == imports._INTAKE_POLICY and not request.get("_table_overrides")
                and not request.get(imports._CONFIRMED_LAYOUT)
                and (columns := import_direction_role_decision.pending_scope(summary, layout_members, mode=result["mode"]))):
            manifest[import_direction_role_decision._PENDING] = columns
    import_balance_trial.remember_source_roles(stored, request, manifest, summary["profile"])
    import_source_money.remember(stored, request, manifest, summary["profile"])
    request.pop(_KEY, None)
    if manifest["regions"] or layout_members:
        request[_KEY] = manifest
    encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
    if layout_members and len(encoded.encode("utf-8")) > _MAX_MANIFEST_BYTES:
        manifest.pop("layout_members")
        manifest.pop("layout_source_rows")
        layout_members = []
        request.pop(_SOURCE_BALANCE_ROLES, None)
        request.pop(import_source_money.RECEIPT_KEY, None)
        if not manifest["regions"]:
            request.pop(_KEY)
        encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) <= _MAX_MANIFEST_BYTES and _KEY in request:
        if manifest["regions"]:
            result["ready_evidence"] = {"revision": manifest["revision"],
                                        "all_regions_sampled": manifest["all_regions_sampled"],
                                        "scopes": [entry["scope"] for entry in manifest["regions"]]}
        if layout_members:
            result["layout_review"] = {"revision": manifest["revision"], "members": [entry["scope"] for entry in layout_members]}
    else:
        request.pop(_KEY, None)
        request.pop(_SOURCE_BALANCE_ROLES, None)
        request.pop(import_source_money.RECEIPT_KEY, None)
        encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
    db.x("update import_sessions set request_json=? where import_id=?", [encoded, import_id])


def require_revision(session, revision):
    from . import imports

    if (session["stage"] != "ready" or session["source_state"] != "retained"
            or not session["expires_at"] or session["expires_at"] <= imports._now()):
        raise ServiceError(409, "ready_evidence_unavailable", "导入预览已失效，请重新预览")
    manifest = json.loads(session["request_json"] or "{}").get(_KEY)
    if not manifest or manifest["revision"] != revision:
        raise ServiceError(409, "ready_evidence_stale", "原件核对对应的预览已更新，请重新预览")
    return manifest


def _ready(case_id, import_id, revision, *, for_layout=False):
    from . import imports

    session = imports._session(case_id, import_id)
    manifest = require_revision(session, revision)
    if not for_layout and not manifest["regions"]:
        raise ServiceError(409, "ready_evidence_unavailable", "导入预览尚无可独立核证的表区")
    return session, manifest, imports._table_profile(session)


def _regions(manifest):
    return {(entry["scope"]["member_path"], entry["scope"]["header_row"]): entry
            for entry in manifest["regions"]}


def _select(regions, member, header):
    if (entry := regions.get((member, header))) is None:
        raise ServiceError(422, "outside_ready_evidence", "所选工作表或表头不在本次预览的核对范围内")
    return entry


def _cells(snapshot, session, entry, rows, profile, *, for_layout=False, physical_presence=False):
    snapshot.seek(0)
    try:
        return import_originals.read_cells(
            snapshot, session["suffix"], {"member_path": entry["scope"]["member_path"], "encoding": entry["encoding"]},
            entry["document"], [], rows, profile, for_layout=for_layout, physical_presence=physical_presence,
        )
    except (OSError, ValueError, KeyError, IndexError, StopIteration, csv.Error,
            zipfile.BadZipFile, import_originals.ET.ParseError):
        raise ServiceError(409, "ready_evidence_unreadable", "无法在本次预览范围内读取原件，请核对文件") from None


def _binding(session, manifest, profile):
    return {"case_id": str(session["case_id"]), "import_id": session["import_id"],
            "sha256": session["sha256"], "revision": manifest["revision"], "parser_version": profile}


def layout(case_id, import_id, body):
    from . import imports

    session, manifest, profile = _ready(case_id, import_id, body.revision, for_layout=True)
    selected = next((entry for entry in manifest.get("layout_members", ())
                     if entry["scope"]["member_path"] == body.member_path), None)
    if selected is None or body.row_start > selected["scope"]["source_rows"] or body.column_start > table_import.MAX_COLUMNS:
        raise ServiceError(422, "outside_layout_review", "所选原件位置不在本次查阅范围")
    scope = selected["scope"]
    row_last = min(body.row_start + 7, scope["source_rows"])
    positions = range(body.row_start, row_last + 1)
    with imports._source(session, snapshot=True) as snapshot:
        cells = _cells(snapshot, session, selected, set(positions), profile, for_layout=True)
    last_col = body.column_start + 15
    _ready(case_id, import_id, body.revision, for_layout=True)
    return {**_binding(session, manifest, profile), "member": scope, "row_start": body.row_start,
            "column_start": body.column_start, "more_rows": row_last < scope["source_rows"],
            "more_columns": any(row and row[-1]["column"] > last_col for row in cells.values()),
            "rows": [{"source_row": number, "cells": [c for c in cells[number] if body.column_start <= c["column"] <= last_col]}
                     for number in positions]}


def column_evidence(case_id, import_id, body):
    """Profile every physical row in one bounded, current table region."""
    from . import imports

    session, manifest, profile = _ready(case_id, import_id, body.revision, for_layout=True)
    entry = next((item for item in manifest.get("layout_members", ())
                  if item["scope"]["member_path"] == body.member_path), None)
    if entry is None:
        raise ServiceError(422, "outside_layout_review", "表区不属于本次预览")
    scope = entry["scope"]
    head = body.header_row
    if not 1 <= head < scope["source_rows"] or not 1 <= body.column_start <= table_import.MAX_COLUMNS:
        raise ServiceError(422, "outside_layout_review", "表区或物理列起点超出原件")
    last = min((row - 1 for row in scope["header_rows"] if row > head), default=scope["source_rows"])
    first = head + 1
    base = {**_binding(session, manifest, profile), "member_path": body.member_path, "header_row": head}
    coverage = {"first_row": first, "last_row": last, "scanned_rows": 0, "complete": False, "reason": None}
    if (session["size_bytes"] > import_layout_trial._MAX_FILE_BYTES
            or manifest["layout_source_rows"] > import_layout_trial._MAX_SOURCE_ROWS):
        return {**base, "coverage": {**coverage, "reason": "layout_trial_limit"}, "columns": []}
    if first > last:
        raise ServiceError(422, "outside_layout_review", "所选表头后没有数据原行")
    try:
        with imports._source(session, snapshot=True) as source:
            observed = _cells(source, session, entry, set(range(head, last + 1)), profile, for_layout=True)
    except ServiceError as error:
        if error.code != "ready_evidence_unreadable":
            raise
        return {**base, "coverage": {**coverage, "reason": error.code}, "columns": []}
    _ready(case_id, import_id, body.revision, for_layout=True)

    headings = {cell["column"]: cell["value"] for cell in observed.pop(head)}
    if not headings or body.column_start > max(headings):
        raise ServiceError(422, "outside_layout_review", "当前表头没有该物理列")
    compared = body.compare_columns
    if len(compared) != len(set(compared)) or not set(compared) <= headings.keys():
        raise ServiceError(422, "outside_layout_review", "竞争列须为本表区的不重复物理列")
    rows = {row: {cell["column"]: (cell["kind"], cell["value"]) for cell in cells}
            for row, cells in sorted(observed.items())}
    missing = ("missing", None)
    columns = []
    for column in range(body.column_start, min(body.column_start + 16, max(headings) + 1)):
        values = {row: cells.get(column, missing) for row, cells in rows.items()}
        kinds = Counter(kind for kind, _ in values.values())
        lengths = Counter(len(value) for _, value in values.values() if value)
        leading_zero = [row for row, (_, value) in values.items() if value and len(value) > 1
                        and value.startswith("0") and value.isdecimal()]
        samples = {first, (first + last) // 2, last}
        first_kind = {}
        for row, (kind, _) in values.items():
            first_kind.setdefault(kind, row)
        samples.update(first_kind.values())
        if leading_zero:
            samples.add(leading_zero[0])
        blank = next((row for row, (_, value) in values.items() if not value), None)
        if blank is not None:
            samples.add(blank)
        comparisons = []
        for other in compared:
            if other == column:
                continue
            pairs = [(number, values[number], cells.get(other, missing)) for number, cells in rows.items()]
            differences = [number for number, left, right in pairs if left != right]
            populated = [(left, right) for _, left, right in pairs if left[1] and right[1]]
            first_difference = differences[0] if differences else None
            if first_difference is not None:
                samples.add(first_difference)
            comparisons.append({"column": other, "different": len(differences), "first_difference": first_difference,
                                "both_present": len(populated), "equal_present": sum(left == right for left, right in populated),
                                "missing_rows": len(rows) - len(populated)})
        if len(rows) <= 8:
            samples.update(rows)
        columns.append({"column": column, "heading": headings[column], "kinds": dict(kinds),
                        "nonempty": sum(lengths.values()), "leading_zero": len(leading_zero),
                        "length_counts": {str(length): count for length, count in sorted(lengths.items())},
                        "comparisons": comparisons,
                        "samples": [{"source_row": row, "kind": values[row][0],
                                     "value": values[row][1][:120] if values[row][1] is not None else None,
                                     "excerpt": len(values[row][1] or "") > 120} for row in sorted(samples)]})
    return {**base, "coverage": {**coverage, "scanned_rows": len(rows), "complete": True}, "columns": columns}


def trial_layout(case_id, import_id, body, *, evidence_layout=False):
    from . import imports

    session, manifest, profile = _ready(case_id, import_id, body.revision, for_layout=True)
    members = {entry["scope"]["member_path"]: entry for entry in manifest.get("layout_members", ())}
    selection = import_layout_trial.check_scope(session, members, body.regions, manifest.get("layout_source_rows", 0))
    request = json.loads(session["request_json"] or "{}")
    try:
        with imports._source(session, snapshot=True) as snapshot:
            result = import_layout_trial.evaluate(snapshot, session["suffix"], profile, selection,
                                                   request.get("reference_policy"), evidence_layout=evidence_layout,
                                                   reject_opening_clock=request.get(imports._INTAKE_KEY) == imports._INTAKE_POLICY)
    except table_import.TableImportError:
        raise ServiceError(422, "invalid_layout_mapping", "无法按指定物理表区和列试算，请复查原件") from None
    _ready(case_id, import_id, body.revision, for_layout=True)
    return {**_binding(session, manifest, profile), "review_status": "review_only", **result}


def confirmed_layout(session, body, profile, *, scoped_notes=False, evidence_layout=False):
    manifest = require_revision(session, body.revision)
    members = {entry["scope"]["member_path"]: entry for entry in manifest.get("layout_members", ())}
    if session["suffix"] not in {".csv", ".xlsx"} or profile not in table_import.CONFIRMED_LAYOUT_PROFILES:
        raise ServiceError(422, "layout_apply_unsupported", "正式映射仅支持当前规则的小型独立 CSV 和 XLSX")
    import_layout_trial.check_scope(session, members, body.regions, manifest.get("layout_source_rows", 0))
    policy = table_import.CONFIRMED_LAYOUT_EVIDENCE_POLICY if evidence_layout else table_import.CONFIRMED_LAYOUT_POLICY
    result = {"policy": policy, "sha256": session["sha256"],
            "encoding": members["table.csv"]["encoding"] if session["suffix"] == ".csv" else "xlsx",
            "regions": [region.model_dump() for region in body.regions]}
    if scoped_notes:
        result["field_notes_policy"] = table_import.table_source_roles.SCOPED_FIELD_NOTE_POLICY
    return result


def read(case_id, import_id, body):
    from . import imports

    session, manifest, profile = _ready(case_id, import_id, body.revision)
    selected = _select(_regions(manifest), body.member_path, body.header_row)
    scope = selected["scope"]
    rows, columns = set(body.source_rows), set(body.columns)
    if (len(rows) != len(body.source_rows) or len(columns) != len(body.columns)
            or bool(rows) != bool(columns) or not rows <= set(scope["sample_rows"])):
        raise ServiceError(422, "outside_ready_evidence", "请从本次预览选择原行和物理列，且位置不得重复")
    headers = scope["header_rows"]
    with imports._source(session, snapshot=True) as snapshot:
        cells = _cells(snapshot, session, selected, set(headers) | rows, profile)
    if columns - {cell["column"] for cell in cells[body.header_row]}:
        raise ServiceError(422, "outside_ready_evidence", "物理列不在所选表头范围内")
    _ready(case_id, import_id, body.revision)
    return {**_binding(session, manifest, profile), "scope": scope,
            "headers": [{"source_row": row, "cells": cells[row]} for row in headers],
            "rows": [{"source_row": row, "cells": [cell for cell in cells[row] if cell["column"] in columns]}
                     for row in body.source_rows]}


def propose(case_id, import_id, body):
    from . import imports

    session, manifest, profile = _ready(case_id, import_id, body.revision)
    regions = _regions(manifest)
    claims = defaultdict(set)
    for item in body.candidate_columns:
        columns = claims[(item.member_path, item.header_row)]
        if item.column in columns:
            raise ServiceError(422, "outside_ready_evidence", "原列位置不得重复")
        columns.add(item.column)

    if claims:
        with imports._source(session, snapshot=True) as original:
            for (member, header), columns in claims.items():
                selected = _select(regions, member, header)
                actual = _cells(original, session, selected, {header}, profile)
                if columns - {cell["column"] for cell in actual[header]}:
                    raise ServiceError(422, "outside_ready_evidence", "物理列不在所选表头范围内")
    else:
        imports._source(session)

    _ready(case_id, import_id, body.revision)
    return {**_binding(session, manifest, profile), "field": body.field, "action": body.action,
            "candidate_columns": body.candidate_columns, "review_status": "review_only"}


def _grouped_header(snapshot, entry, actual, profile, header):
    parent_row = header - 1
    if (profile != table_tiered_headers.GROUPED_PROFILE or parent_row not in actual
            or parent_row not in entry["document"].get("tier_parent_rows", ())):
        return None
    width = max(len(actual[parent_row]), len(actual[header]), len(entry["document"]["columns"]))

    def physical(row):
        values = [None] * width
        for cell in actual[row]:
            values[cell["column"] - 1] = cell["value"] if cell["kind"] == "text" else False
        return values

    try:
        member = int(entry["scope"]["member_path"].rsplit("/", 1)[-1])
        geometry = table_tiered_headers.xlsx_merge_ranges(table_import._bytes(snapshot), {member: {parent_row}})
        return table_tiered_headers.inspect_grouped(physical(parent_row), physical(header), parent_row,
                                                  lambda value: value, geometry[member][parent_row])
    except (KeyError, ValueError, table_import.TableImportError):
        raise ServiceError(409, "ready_evidence_unreadable", "无法回查父表头与原列的物理合并范围") from None


def grouped_parent_labels(session, entry, profile, header):
    from . import imports

    if profile != table_tiered_headers.GROUPED_PROFILE or header - 1 not in entry["document"].get("tier_parent_rows", ()):
        return {}
    with imports._source(session, snapshot=True) as snapshot:
        actual = _cells(snapshot, session, entry, {header - 1, header}, profile, for_layout=True)
        proof = _grouped_header(snapshot, entry, actual, profile, header)
    return {col: parent for col, parent, *_ in proof["columns"]} if proof else {}


def _proven_column(entry, actual, field, grouped=None):
    proof = entry["rules"].get(field)
    if proof is None:
        return None
    column = int(proof["column_index"])
    head = entry["scope"]["header_row"]
    cells = actual[head]
    if not 0 < column <= len(cells):
        return None
    cell = cells[column - 1]
    if cell["kind"] != "text" or cell["header"] != proof["column"]:
        return None
    if proof["basis"] == "verified_region_header":
        parent = int(proof["parent_row"])
        if parent not in actual or proof.get("source_role") != field:
            return None
        verified = grouped if grouped is not None else table_tiered_headers.inspect(
            actual[parent], cells, parent, lambda value: value["value"] if value["kind"] == "text" else "")
        if not verified or verified["status"] != "verified" or not any(
            index == column and role == field for index, _, _, role, _ in verified["columns"]
        ):
            return None
    else:
        heading = table_import._norm(cell["value"])
        if field == "self_name" and heading == "主户名":
            headings = tuple(item["value"].strip() if item["kind"] == "text" else "" for item in cells)
            return column if table_import.is_bank_feedback39(headings) else None
        if field == "self_account" and heading == "账户号" and proof["basis"] == "explicit_identity_role":
            headings = tuple(item["value"].strip() if item["kind"] == "text" else "" for item in cells)
            resolved = table_import._candidates(headings, table_import.VOUCHER_REFERENCE_PROFILE)
            return column if table_import.is_bank_split_ledger(headings, resolved) else None
        if identity_field(heading) != field and heading not in _POSITIONAL_ROLES.get(field, ()):
            return None
    return column


def _assess_column(entry, original, header, field, column, grouped=None):
    heading = table_import._norm(original[header][column - 1]["value"])
    if reason := column_role_refutation(field, heading):
        return "refuted", reason
    if _proven_column(entry, original, field, grouped) == column:
        return "supported", "registered_source_role"
    if any(other != field and _proven_column(entry, original, other, grouped) == column for other in entry["rules"]):
        return "refuted", "incompatible_source_role"
    return "insufficient", "source_role_unproven"


def layout_column_verdicts(case_id, import_id, revision, member, header, field, columns):
    """Check local role proof where the layout page overlaps a registered region."""
    from . import imports

    session, manifest, profile = _ready(case_id, import_id, revision, for_layout=True)
    region = _regions(manifest).get((member, header))
    if not region or not region["rules"]:
        return {}
    with imports._source(session, snapshot=True) as snapshot:
        original = _cells(snapshot, session, region, set(region["scope"]["header_rows"]), profile)
        grouped = _grouped_header(snapshot, region, original, profile, header)
    _ready(case_id, import_id, revision, for_layout=True)
    return {column: _assess_column(region, original, header, field, column, grouped)
            for column in columns if column <= len(original[header])}


def declared_counter_header(facts):
    """Locate literal counterparty labels in the complete first header, without proving a mapping."""
    from . import imports

    field = facts["field"]
    if field not in {"counter_account", "counter_card", "counter_name"} or facts["header_row"] != 1:
        return None
    case_id, import_id, revision = (facts[key] for key in ("case_id", "import_id", "revision"))
    session, manifest, profile = _ready(case_id, import_id, revision, for_layout=True)
    entries = manifest["layout_members"]
    if session["suffix"] != ".csv" or len(entries) != 1 or session["size_bytes"] > import_layout_trial._MAX_FILE_BYTES:
        return None
    [entry] = entries
    scope = entry["scope"]
    if (scope["member_path"] != facts["member_path"] or scope["header_rows"] not in ([], [1])
            or scope["source_rows"] > import_layout_trial._MAX_SOURCE_ROWS):
        return None
    with imports._source(session, snapshot=True) as snapshot:
        header = _cells(snapshot, session, entry, {1}, profile, for_layout=True)[1]
    if not header or any(cell["kind"] not in {"text", "empty"} for cell in header):
        return None
    declared = table_import._candidates([cell["value"] for cell in header], profile)
    if not all(declared.get(name) for name in ("trade_time", "direction", "currency")):
        return None
    matches = []
    for cell in header:
        label = table_import._norm(cell["value"])
        if identity_field(label) == field or label in _POSITIONAL_ROLES.get(field, ()):
            matches.append({"column": cell["column"], "heading": cell["value"]})
    if not matches:
        return None
    _ready(case_id, import_id, revision, for_layout=True)
    return {"scope": "source_header_only", "field": field, "member_path": scope["member_path"],
            "header_row": 1, "width": header[-1]["column"], "matches": matches}


def _intake_field_cells(session, items):
    """Read one retained table from its existing ready or completed registration."""
    from . import imports

    path = {".csv": "table.csv", ".xlsx": "worksheets/0001"}.get(session["suffix"])
    if (path is None or {item["member_path"] for item in items} != {path}
            or any(item["header_row"] not in (None, 1) for item in items)
            or session["size_bytes"] > import_layout_trial._MAX_FILE_BYTES):
        return None
    profile = imports._table_profile(session)
    if session["stage"] == "ready":
        request = json.loads(session["request_json"] or "{}")
        manifest = request.get(_KEY, {})
        members = manifest.get("layout_members", ())
        if (len(members) != 1 or session["expires_at"] is None or session["expires_at"] <= imports._now()):
            return None
        [entry] = members
    else:
        summary = json.loads(session["result_json"] or "{}").get("table_summary") or {}
        documents, members = summary.get("documents", ()), summary.get("members", ())
        if len(documents) != 1 or len(members) != 1 or members[0]["member_path"] != path:
            return None
        [document] = documents
        entry = {"scope": document, "document": document, "encoding": members[0]["encoding"]}
    scope = entry["scope"]
    if (scope["member_path"] != path or scope["header_rows"] not in ([], [1])
            or not 1 <= scope["source_rows"] <= import_layout_trial._MAX_SOURCE_ROWS):
        return None
    try:
        original = (imports._source(session, snapshot=True) if session["stage"] == "ready"
                    else imports.get_source_file(session["case_id"], session["import_id"])[0])
        with original:
            cells = _cells(original, session, entry, set(range(1, scope["source_rows"] + 1)), profile, for_layout=True)
    except ServiceError as error:
        if error.code not in {"source_changed", "source_missing", "unsafe_upload_storage", "import_not_found",
                              "ready_evidence_unreadable"}:
            raise
        return None
    header = cells.pop(1)
    if not header or any(cell["kind"] != "text" for cell in header):
        return None
    headings = [cell["value"] for cell in header]
    rows = [(number, [cell["value"] for cell in cells[number]]) for number in sorted(cells)
            if any(cell["value"].strip() for cell in cells[number])]
    return path, headings, rows, scope["header_rows"] == [1], profile


def _declared_intake_notes(names, rows, note, registered, *, profile=table_import.IDENTITY_PROFILE,
                           bound_reported_balance=None):
    """Describe literal column labels and observed value shapes, independently of mapping."""
    def single(*labels):
        matches = [column for column, name in enumerate(names, 1) if name in labels]
        return matches[0] if len(matches) == 1 else None

    def values(column):
        return {number: row[column - 1] for number, row in rows if len(row) >= column}

    def counts(column, *, numerical=False):
        observed = values(column)
        known = [value for value in observed.values() if value.strip()]
        zeros = sum(bool(re.fullmatch(r"[+-]?0+(?:\.0+)?", value.strip())) if numerical
                    else value.strip() == "0" for value in known)
        return (f"第 {column} 列「{names[column - 1]}」{len(known)}行非空、{len(observed) - len(known)}行空白"
                + (f"、{len(rows) - len(observed)}行缺原格" if len(observed) != len(rows) else "")
                + (f"、{zeros}行原值为零" if zeros else ""))

    def pair(columns, following, *, numerical=False):
        row_numbers = sorted(set().union(*(values(column) for column in columns)))
        text = "；".join(counts(column, numerical=numerical) for column in columns) if row_numbers else "本次可核对这些原表头"
        note(columns, f"{text}。{following}", row_numbers)

    def later_role_header(columns, role_for_heading, *role_pairs):
        # A complete known pair, or two different known roles at the
        # cited positions, leaves the first header's full scope unproved.
        known = set().union(*role_pairs)
        last_column = max(columns)
        for _, row in rows:
            roles = [role_for_heading(table_import._norm(value)) for value in row]
            found = set(roles)
            if any(pair <= found for pair in role_pairs):
                return True
            if last_column <= len(roles):
                cited = {roles[column - 1] for column in columns}
                if len(cited) == 2 and cited <= known:
                    return True
        return False

    book_labels = {"本方账号", "本方帐号", "交易账号", "账号", "帐号", "公司账号"}
    book_balance_labels = {"当前余额", "账户余额", "帐户余额", "交易余额"}
    book, recorded_balance = single(*book_labels), single(*book_balance_labels)
    has_money_columns = single("交易金额", "发生额", "交易发生额") or (
        single("借方发生额") and single("贷方发生额"))
    if (registered and book and recorded_balance and recorded_balance != bound_reported_balance and has_money_columns
            and {"交易时间", "交易日期", "日期"} & set(names)
            and not any(identity_field(name) in {"payer_account", "payee_account"} for name in names)):
        def book_role(heading):
            if heading in book_labels:
                return "book"
            if heading in book_balance_labels:
                return "balance"
            return None

        if not later_role_header((book, recorded_balance), book_role, {"book", "balance"}):
            subledger = single("子账户序号")
            columns = (book, subledger, recorded_balance) if subledger else (book, recorded_balance)
            pair(columns, "本方流水的原表余额按此列回查。余额类型、笔后时点及账簿覆盖须另核。",
                 numerical=True)

    account = single("账号", "帐号")
    opening = single("开户日期", "开户时间", "账号开户日期", "账号开户时间", "账户开户日期", "账户开户时间")
    subaccount = single("子账户账号", "子账户号", "子账号")
    balance, available = single("余额", "账面余额", "账户余额", "帐户余额"), single("可用余额")
    currency = single("币种")
    if account and opening and balance and available and currency and single("账户名称", "账户开户名称"):
        closing = single("销户日期", "销户时间", "账号销户日期", "账号销户时间", "账户销户日期", "账户销户时间")
        last_trade = single("上次交易日", "上次交易日期", "最后交易日", "最后交易日期", "最后交易时间")
        if closing and last_trade:
            pair((opening, closing, last_trade), "表头呈现账户资料，分别记载开户、销户和账户历史交易日期。逐笔交易时点须另有原据。")
        else:
            pair((account, opening), "原表声明账户资料和开户时点。交易时点须由独立原列证明。")
    if account and subaccount and balance and available and currency:
        pair((account, subaccount), "表头呈现账户和子账户类辅助资料。同一原行分别记载两种标识，账户归属与绑定须另有原据。")
    if balance and available and currency:
        if rows and not values(balance) and not values(available):
            note((balance, available),
                 f"原表头第 {balance} 列「{names[balance - 1]}」和第 {available} 列「{names[available - 1]}」职责分别保留。"
                 "本次数据行没有两列的原格，余额数值和时点待核。")
        elif registered or account and (opening or subaccount):
            pair((balance, available), "余额与可用余额分别保留。账面口径、余额时点和交易发生额须各自有据。")

    posting_labels = {"交易金额", "交易发生额", "发生额"}
    posting, after_amount = single(*posting_labels), single("交易后金额")
    if not registered and posting and after_amount and rows:
        from . import import_verbatim_money

        columns = (posting, after_amount)
        complete_window = all(number == position and len(row) == len(names)
                              for position, (number, row) in enumerate(rows, 2))
        # Check the value shape without adopting a source unit or a balance role.
        unscaled = {"factor": 1}
        if (complete_window
                and not later_role_header(columns, lambda name: "posting" if name in posting_labels else name,
                                          {"posting", "交易后金额"})
                and all((populated := [value for value in values(column).values() if value.strip()])
                        and all(table_import._money(value, unscaled)[0] is not None for value in populated)
                        for column in columns)
                and not import_verbatim_money.has_later_header(
                    {number: [{"value": value} for value in row] for number, row in rows}, profile)):
            note(columns, f"交易发生额按第 {posting} 列原格核对。第 {after_amount} 列的交易后金额仅保留原值和位置，"
                 "余额口径待核，当前缺少直接采用为余额的依据。", [number for number, _ in rows])

    after_labels = ("交易后余额", "本方交易后余额")
    peer_after_labels = ("对手交易后余额", "对方交易后余额")
    available_label = "交易后可用余额"
    after, after_available = single(*after_labels), single(available_label)
    if after and after_available:
        balance_headings = {*after_labels, *peer_after_labels}
        available_headings = {available_label, "对手交易后可用余额", "对方交易后可用余额"}
        # Require one header window, with a rectangular read when the parser
        # could not register the table boundary.
        one_window = True
        for _, row in rows:
            headings = {table_import._norm(value) for value in row}
            if (not registered and len(row) != len(names)
                    or headings.intersection(balance_headings) and headings.intersection(available_headings)):
                one_window = False
                break
        if one_window:
            left, right = values(after), values(after_available)
            both = [(value.strip(), right[number].strip()) for number, value in left.items()
                    if value.strip() and number in right and right[number].strip()]
            equal = sum(first == second for first, second in both)
            pair((after, after_available),
                 f"两列均有原值的共{len(both)}行，其中{equal}行去首尾空白后字面相同。"
                 "按原列逐行回查，两列不可互换或互补。用于账簿计算须分别核实账簿、期间与余额口径。",
                 numerical=True)
    if not registered:
        endpoints = {"payer_account", "payee_account"}
        parties = [(column, role) for column, name in enumerate(names, 1)
                   if (role := identity_field(name)) in endpoints]
        columns = [column for column, _ in parties]
        if (len(parties) == 2 and {role for _, role in parties} == endpoints
                and all(len(row) == len(names) for _, row in rows)
                and not later_role_header(columns, identity_field, endpoints)):
            pair(columns, "付款账号与收款账号各按原列回查。仅据这两列，本账归属待核。")
            amount = single("金额", "交易金额", "支付金额")
            fee = single("手续费", "交易手续费")
            if amount:
                monetary = (amount, fee) if fee else (amount,)
                basis = "金额与手续费按各自原格回查。" if fee else "金额按原格回查。"
                currency_labels = table_import._ALIASES["currency"] | {"币种代码", "币种名称", "交易币种代码"}
                if not currency_labels.intersection(names):
                    basis += "原表未列明币种字段，实际币种和金额单位待核。"
                pair(monetary, basis, numerical=True)
            markers = [column for label in ("往来标志", "借贷标志") if (column := single(label))]
            if len(markers) == 2 and all(
                (originals := [value.strip() for value in values(column).values() if value.strip()])
                and all(value.isascii() and value.isdecimal() for value in originals) for column in markers
            ):
                pair(markers, "两种数字标志分别保留，代码释义及本账收付待核。")
        return

    peer_balance_labels = {"交易对手余额", "对手交易余额", "交易对方余额", "对方交易余额", "对手余额", "对方余额"}
    peer_recorded_balance = single(*peer_balance_labels)
    if recorded_balance and peer_recorded_balance:
        def recorded_role(heading):
            if heading in book_balance_labels:
                return "recorded"
            if heading in peer_balance_labels:
                return "peer"
            return None

        positions = (recorded_balance, peer_recorded_balance)
        if not later_role_header(positions, recorded_role, {"recorded", "peer"}):
            pair(positions, "原表余额与对方余额按各自原列核对。空白和缺原格分别保留。"
                 "本账归属及余额时点须另有原据。", numerical=True)

    peer_after = single(*peer_after_labels)
    if after and peer_after:
        pair((after, peer_after), "原表分别标示交易后余额和对手交易后余额。用于账簿计算还须核实各列的本账及期间。",
             numerical=True)
    debit, credit = single("借方发生额"), single("贷方发生额")
    if debit and credit:
        columns = (debit, credit)
        if not later_role_header(columns, lambda name: name, {"借方发生额", "贷方发生额"}):
            currency_labels = ("币种", "币种名称", "币种代码", "交易币种", "交易币种代码", "币别", "交易币别",
                               "货币", "currency", "currency code", "ccy")
            currency_sources = set(names).intersection(map(table_import._norm, currency_labels))
            currency_note = "" if currency_sources else "该原表表头未列明币种字段。"
            pair(columns, "借方发生额与贷方发生额按两处原列回查，逐行保留空白和原值零。" + currency_note,
                 numerical=True)

    trade_date = single("交易日期")
    trade_clocks = [column for column, name in enumerate(names, 1) if name == "交易时间"]
    if trade_date and len(trade_clocks) == 2 and rows:
        for precise, minute in (trade_clocks, trade_clocks[::-1]):
            compatible = []
            for number, row in rows:
                if len(row) < max(trade_date, precise, minute):
                    break
                day, precise_value, minute_value = (row[column - 1].strip() for column in (trade_date, precise, minute))
                second_clock = table_import._time(f"{day} {precise_value}")
                minute_clock = table_import._time(minute_value)
                if (not table_import._date_only(day) or not re.fullmatch(r"[0-9]{1,2}:[0-9]{2}:[0-9]{2}", precise_value)
                        or not re.fullmatch(r"[0-9]{4}[-/.][0-9]{1,2}[-/.][0-9]{1,2}[ T][0-9]{1,2}:[0-9]{2}", minute_value)
                        or second_clock is None or second_clock.replace(second=0) != minute_clock):
                    break
                compatible.append(number)
            if len(compatible) == len(rows):
                note((trade_date, precise, minute), f"第 {precise} 列原交易时间到秒，第 {minute} 列只到分钟。"
                     "同日同分相符。分钟列的秒未写明，原值按各列保留。", compatible)
                break

    compound = [column for column, name in enumerate(names, 1) if name == "对方账号及户名"]
    if len(compound) == 1:
        [column] = compound
        observed = values(column)
        if observed and len(observed) == len(rows) and all(
            value.strip().casefold() in {"", "null", "none", "无", "未提供", "-", "—"} for value in observed.values()
        ):
            note(compound, "对方账号与户名同列。下列原行只有空值或占位，无法提取账号或户名。",
                 sorted(observed))
        else:
            pair((column,), "对方账号与户名同列，组分须按原值中明确的边界核对。")

    owner_card, owner_account = single("交易卡号"), single("交易账号", "交易帐号")
    if owner_card and owner_account:
        pair((owner_card, owner_account), "交易卡号和交易账号以各自原格作证。卡账绑定须另有原据。")
    shared_labels = ("对手账/卡号", "对方账/卡号")
    shared_id, peer_card = single(*shared_labels), single("对手卡号", "对方卡号")
    peer_account = single("对手账号", "对手帐号", "对方账号", "对方帐号")
    if shared_id and peer_card and peer_account:
        left, right = values(shared_id), values(peer_account)
        equal = [number for number, value in left.items() if value.strip() and value == right.get(number)]
        following = "账/卡合列、卡号列和账号列各自保留。"
        if equal:
            following += f"其中{len(equal)}行合列与账号列字面相同。卡号用途需独立原据。"
        pair((shared_id, peer_card, peer_account), following)
    elif peer_account and peer_card and not set(shared_labels).intersection(names):
        qualified = {label: role for role, labels in _POSITIONAL_ROLES.items() for label in labels}

        def identity_role(name):
            return qualified.get(name) or identity_field(name)

        party_roles = {"counter_account", "counter_card"}
        owner_roles = {"self_account", "self_card"}
        declared = Counter(map(identity_role, names))
        columns = (peer_account, peer_card)
        if all(declared[role] == 1 for role in party_roles) and not later_role_header(
            columns, identity_role, party_roles, owner_roles
        ):
            left, right = values(peer_account), values(peer_card)
            equal = sum(value == right.get(number) for number, value in left.items() if value.strip())
            following = "原表分别标明对手账号与对手卡号，请按两列逐行回查原始字符串。两列不可互换或用一列替代。"
            if equal:
                following += f"有{equal}行两个原始字符串相同。"
            following += "映射中的对手标识及两列字面相等均不足以证明卡账绑定。"
            pair(columns, following)
    voucher_labels = ("凭证号", "凭证编号")
    primary, voucher = single("交易流水号"), single(*voucher_labels)
    if primary and voucher and not any(value.strip() for column in (primary, voucher) for value in values(column).values()):
        pair((primary, voucher), "交易流水号与凭证号分列。缺少编号原值时留空。")

    order, teller = single("交易序号"), single("柜员流水号")
    if order and teller:
        def reference_role(name):
            return "凭证号" if name in voucher_labels else name

        columns = (order, teller)
        if not later_role_header(columns, reference_role, {"交易序号", "柜员流水号"}, {"交易流水号", "凭证号"}):
            pair(columns, "两列按各自原列回查，编号用途不可互换。单列编号或字面相同不能证明同笔或结算。")

    original_trade, sub_trade = single("原交易流水号"), single("子交易流水号")
    if teller and original_trade and sub_trade:
        columns = (teller, original_trade, sub_trade)
        if not later_role_header(columns, lambda name: name, {"柜员流水号", "原交易流水号", "子交易流水号"}):
            pair(columns, "三列分别是柜员记账引用、原交易引用与子交易引用，原字符串和空白均按各自原格回查。"
                 "这些引用的跨记录对应、冲正和交易效力仍需独立原据。")

    summary, remarks = single("摘要", "交易摘要", "摘要说明"), single("备注", "交易备注", "说明", "附加说明")
    if summary and remarks:
        left, right = values(summary), values(remarks)
        different = [row for row, value in left.items()
                     if value.strip() and isinstance(other := right.get(row), str) and other.strip() and value != other]
        following = "摘要与备注各自照原列回查。"
        if different:
            following += f"有{len(different)}行两栏原文均有值且不同。"
        pair((summary, remarks), following)

    peer_name_mix, peer_id_mix = single("对方账号姓名"), single("对方账号卡号")
    if peer_name_mix and peer_id_mix:
        textual = [number for number, value in values(peer_name_mix).items()
                   if re.search(r"[一-龯]", value) and not any(char.isdecimal() for char in value)]
        joined_ids = [number for number, value in values(peer_id_mix).items()
                      if re.fullmatch(r"[A-Za-z0-9]+(?:\s+[A-Za-z0-9]+)+", value.strip())
                      and re.search(r"[A-Za-z]", value) and re.search(r"[0-9]", value)]
        following = (f"第 {peer_name_mix} 列{len(textual)}行有汉字且无数字，第 {peer_id_mix} 列"
                     f"{len(joined_ids)}行含字母和数字并见空白分段。"
                     "姓名、账号和卡号的逐项拆分须有来源口径。")
        pair((peer_name_mix, peer_id_mix), following)


def _transfer_method_note(headings, rows, note, profile):
    methods = [index for index, heading in enumerate(headings) if table_import._norm(heading) == "现转标志"]
    if len(methods) != 1:
        return
    [method] = methods
    candidates = table_import._candidates(headings, profile)
    times = candidates.get("trade_time", ())
    amounts = candidates.get("amount", ())
    if not times or len(amounts) != 1:
        return
    [amount] = amounts
    transferred = []
    for position, (number, cells) in enumerate(rows, 2):
        if (number != position or len(cells) <= max(method, amount["index"])
                or not any(table_import._time(table_import._value(cells, clock)) for clock in times)
                or table_import._money(cells[amount["index"]], amount)[0] is None
                or table_import._norm(cells[method]) in {"现转标志", "收付标志", "借贷标志"}):
            break
        if cells[method].strip() in {"转账", "转帐"}:
            transferred.append(number)
    if transferred:
        column = method + 1
        note([column], f"第 {column} 列「现转标志」的这些原行记载方式为转账。"
             "收付方向、现金属性和交易效力各需相应原据。", transferred)


def _transaction_description_notes(headings, rows, note, profile):
    """Keep declared transaction types, channels and statuses distinct on the source card."""
    if profile != table_import.VOUCHER_REFERENCE_PROFILE or not rows or any(len(row) != len(headings) for _, row in rows):
        return
    names = [table_import._norm(heading) for heading in headings]
    types = [index for index, name in enumerate(names) if name == "业务类型" or name.endswith("交易类型")]
    candidates = table_import._candidates(headings, profile)
    if not types or not candidates.get("direction") or not candidates.get("amount"):
        return
    channels = {entry["index"] for entry in candidates.get("channel", ())}
    for index in types:
        reported = [number for number, row in rows if row[index].strip()]
        if reported:
            statement = f"第 {index + 1} 列「{headings[index]}」记载原表交易类型，按各原格回查。"
            if index in channels:
                statement += "这项原值可归集到现有渠道字段，实际渠道和办理方式各按来源说明核对。"
            note([index + 1], statement, reported)
    for index, name in enumerate(names):
        if name in {"交易渠道", "交易方式", "办理渠道"}:
            populated = [number for number, row in rows if row[index].strip()]
            if populated:
                note([index + 1], f"第 {index + 1} 列按原表记录{name}。", populated)
        current_status = name in {"交易是否成功", "交易状态"}
        if not current_status and not name.endswith(("交易状态码", "交易状态编码", "交易状态代码")):
            continue
        values = [(number, row[index].strip()) for number, row in rows]
        label = f"第 {index + 1} 列「{headings[index]}」"
        if not any(value for _, value in values):
            note([index + 1], f"{label}的这些状态原格为空。", [number for number, _ in values])
        if numbers := [number for number, value in values if value.isascii() and value.isdecimal()]:
            note([index + 1], f"{label}有数字状态原码，需核对应码表。", numbers)
        for markers, word in (({"成功", "交易成功", "success", "succeeded"}, "成功"),
                              ({"失败", "交易失败", "failed", "failure", "不成功"}, "失败")):
            positions = [number for number, value in values if table_import._enum(value) in markers]
            if positions:
                statement = (f"{label}原表报告{word}，最终结算需另核。" if current_status
                             else f"{label}的这些格填写{word}。这项状态的适用范围按原表说明核对。")
                note([index + 1], statement, positions)


def _split_direction_source_notes(session, items, found, note):
    """Explain explicit one-sided entries while leaving an undecoded code unadmitted."""
    path, headings, rows, registered, profile = found
    if (profile != table_import.VOUCHER_REFERENCE_PROFILE or session["suffix"] != ".csv"
            or not registered or len(items) != 1 or not rows or any(len(row) != len(headings) for _, row in rows)):
        return
    [item] = items
    if (item["member_path"] != path or item["member_sha256"] != session["sha256"] or item["header_row"] != 1
            or item["reason"] != "invalid_direction" or item["accepted"] or item["unresolved"] != len(rows)
            or item["status"] == "withdrawn"):
        return
    from . import imports

    request = json.loads(session["request_json"] or "{}")
    if request.get(imports._CONFIRMED_LAYOUT) or request.get("_table_overrides"):
        return
    candidates = table_import._candidates(headings, profile)
    fields = ("self_account", "direction", "expense_amt", "income_amt")
    if (any(len(candidates.get(field, ())) != 1 for field in fields)
            or candidates.get("payer_account") or candidates.get("payee_account")):
        return
    owner, code, outgoing, incoming = (candidates[field][0] for field in fields)
    if (len({entry["index"] for entry in (owner, code, outgoing, incoming)}) != len(fields)
            or outgoing["factor"] != incoming["factor"] or outgoing["unit_currency"] != incoming["unit_currency"]
            or any(entry["signed"] for entry in (outgoing, incoming))):
        return
    accounts = {row[owner["index"]].strip() for _, row in rows}
    if len(accounts) != 1 or any(not value.isascii() or not value.isdecimal() or not value.strip("0") for value in accounts):
        return
    original = {"path": path, "rows": [headings, *(row for _, row in rows)]}
    if table_import._source_sign_contracts([original], profile).get(path):
        return
    crosschecks = {entry["index"]: entry for entry in candidates.get("amount", ())}
    side_columns = {outgoing["index"], incoming["index"]}
    for index, name in enumerate(headings):
        if index not in side_columns and table_import._norm(name).endswith("交易金额"):
            crosschecks.setdefault(index, {"factor": outgoing["factor"]})
    supported = []
    for number, cells in rows:
        marker = cells[code["index"]].strip()
        if not marker.isascii() or not marker.isdecimal():
            continue
        raw_amounts = [cells[entry["index"]].strip() for entry in (outgoing, incoming)]
        amounts = [table_import._money(raw, entry) for raw, entry in zip(raw_amounts, (outgoing, incoming), strict=True)]
        if any(issue or (bool(raw) if value is None else value < 0)
               for raw, (value, issue) in zip(raw_amounts, amounts, strict=True)):
            continue
        nonzero = [(entry, value, side) for entry, (value, _), side in zip(
            (outgoing, incoming), amounts, ("出账", "入账"), strict=True) if value]
        if len(nonzero) != 1:
            continue
        [(entry, amount, side)] = nonzero
        if any(cells[index].strip() and table_import._money(cells[index], role) != (amount, None)
               for index, role in crosschecks.items()):
            continue
        column = entry["index"] + 1
        other_column = (incoming if side == "出账" else outgoing)["index"] + 1
        opposite = "为空" if not cells[other_column - 1].strip() else "记为零"
        supported.append(number)
        note([column, other_column], f"第 {number} 行按原表分列记为本账{side}。发生额取第 {column} 列原格，"
             f"原值 {cells[column - 1].strip()}。第 {other_column} 列原格{opposite}。", [number])
    if supported:
        column = code["index"] + 1
        note([column], f"第 {column} 列为方向原码。正式入账需核对适用于该列的来源码表或原注。", supported)
        return column, frozenset(supported)


def _party_account_notes(names, rows, note):
    """Compare the four explicitly labelled CSV accounts within each original row."""
    labels = ("主账户账号", "真实付款人账号", "真实收款人账号", "对方账号")
    headings = [name.replace("帐号", "账号") for name in names]
    if any(headings.count(label) != 1 for label in labels):
        return
    if any(len(set(labels).intersection(table_import._norm(value).replace("帐号", "账号") for value in row)) >= 2
           for _, row in rows):
        return  # There may be another table below the current header.
    indices = [headings.index(label) for label in labels]
    columns = [index + 1 for index in indices]
    note(columns, "原表分别标注" + "、".join(f"第 {column} 列为{label}" for column, label in zip(columns, labels, strict=True))
         + "。本次按主账户的视角核对同一原行。账户归属、交易结算和正式入账仍须各自核证。")

    payers, payees, unresolved = [], [], []
    for number, row in rows:
        if len(row) != len(headings):
            unresolved.append(number)
            continue
        values = [row[index] for index in indices]
        if not all(value.isascii() and value.isdecimal() and value.strip("0") for value in values):
            unresolved.append(number)
            continue
        main, payer, payee, other = values
        if payer != payee and (main, other) == (payer, payee):
            payers.append(number)
        elif payer != payee and (main, other) == (payee, payer):
            payees.append(number)
        else:
            unresolved.append(number)
    if payers:
        note(columns, "按这四列，主账户是付款方。主账户账号与真实付款人账号原字串相同，"
             "对方账号与真实收款人账号原字串相同。", payers)
    if payees:
        note(columns, "按这四列，主账户是收款方。主账户账号与真实收款人账号原字串相同，"
             "对方账号与真实付款人账号原字串相同。", payees)
    if unresolved:
        note(columns, "这些原行的四个账号原格无法分别对应付款和收款两端。请按原格核对收付职责。", unresolved)


def intake_source_field_notes(session, items):
    """Describe verified original cells without applying any financial mapping."""
    found = _intake_field_cells(session, items)
    if found is None:
        return []
    path, headings, rows, registered, profile = found
    names = [table_import._norm(value) for value in headings]
    from . import import_balance_trial, import_source_money

    notes = import_balance_trial.intake_notes(session, found)
    bound_reported_balance = next((item["balance_role"]["column"] for item in notes
                                   if item["balance_role"]["role"] == "own_reported_balance"), None)
    notes.extend(import_source_money.intake_notes(session, found))

    def note(columns, text, positions=()):
        notes.append({"member_path": path, "header_row": 1, "columns": sorted(set(columns)),
                      "rows": list(positions), "text": text})

    if session["suffix"] == ".csv":
        _party_account_notes(names, rows, note)
    _declared_intake_notes(names, rows, note, registered, profile=profile, bound_reported_balance=bound_reported_balance)
    split_scope = _split_direction_source_notes(session, items, found, note)
    if registered:
        role_profile = table_import.VOUCHER_REFERENCE_PROFILE if profile == table_import.CSV_TAIL_SOURCE_PROFILE else profile
        _transfer_method_note(headings, rows, note, role_profile)
        description_profile = (table_import.VOUCHER_REFERENCE_PROFILE
                               if profile == table_import.DEPOSIT_DIRECTION_TRIAL_PROFILE else profile)
        _transaction_description_notes(headings, rows, note, description_profile)
    if registered and profile == table_import.DEPOSIT_DIRECTION_TRIAL_PROFILE:
        deposit_column = table_import.deposit_direction_column(headings, [cells for _, cells in rows])
        if deposit_column is not None:
            candidates = table_import._candidates(headings, table_import.VOUCHER_REFERENCE_PROFILE)
            [amount] = candidates["amount"]
            for word, side in (("存款", "收入"), ("取款", "支出")):
                positions = [row for row, cells in rows if cells[deposit_column].strip() == word]
                note([deposit_column + 1, amount["index"] + 1],
                     f"第 {deposit_column + 1} 列原词「{word}」的来源方向核为本方{side}。"
                     f"发生额按第 {amount['index'] + 1} 列原格核对。", positions)
            if not candidates.get("currency"):
                note([amount["index"] + 1], "原表未列示币种。正式准入的流水沿用现有默认币种。", (row for row, _ in rows))
    from . import import_posting_role_trial

    posting_notes, resolved_direction_column = import_posting_role_trial.intake_notes(session, items, found)
    notes.extend(posting_notes)
    if session["suffix"] != ".csv" or any(len(row) != len(names) for _, row in rows):
        return notes
    candidates = table_import._candidates(headings, profile)
    if not candidates.get("trade_time") or not any(candidates.get(field) for field in ("amount", "direction", "income_amt")):
        return notes

    document = [column for column, name in enumerate(names, 1)
                if document_number_header(name) or name in {"新证件号", "本人身份证号"}]
    account_labels = [(column, name) for column, name in enumerate(names, 1) if name in {"主账号", "账号"}]
    if document and {name for _, name in account_labels} == {"主账号", "账号"}:
        account_positions = "，".join(f"第 {column} 列{name}" for column, name in account_labels)
        note([*document, *(column for column, _ in account_labels)],
             f"第 {'、'.join(map(str, document))} 列为证件标识，{account_positions}。"
             "账户用途按本次流水的范围核对。")

    party_labels = [(column, name) for column, name in enumerate(names, 1)
                    if name in {"本人卡号", "本人账号", "对方卡号", "对方账号"}]
    if {"本人卡号", "本人账号"} <= {name for _, name in party_labels}:
        positions = "，".join(f"第 {column} 列{name}" for column, name in party_labels)
        note([column for column, _ in party_labels],
             f"原表明确标注{positions}。账户绑定和正式流水准入仍需原件核证。")
        shapes = []
        for column, _ in party_labels:
            values = [row[column - 1].strip() for _, row in rows]
            nonempty = sum(bool(value) for value in values)
            leading = any(value.isdecimal() and len(value) > 1 and value.startswith("0") for value in values)
            shapes.append(f"第 {column} 列{nonempty}处非空、{len(values) - nonempty}处空白"
                          + ("，有前导零" if leading else "") + ("，有字面零" if "0" in values else ""))
        if rows:
            note([column for column, _ in party_labels], "标识列样值按原字串保留。" + "。".join(shapes) + "。",
                 (number for number, _ in rows))
        peers = [(column, name) for column, name in party_labels if name in {"对方卡号", "对方账号"}]
        if {name for _, name in peers} == {"对方卡号", "对方账号"}:
            missing = [number for number, row in rows if all(not row[column - 1].strip() for column, _ in peers)]
            if missing:
                note([column for column, _ in peers], "这些原行的对方卡号和对方账号列均为空，按原表保留。", missing)

    debits, credits = (candidates.get(field, ()) for field in ("expense_amt", "income_amt"))
    date_time = [column for column, name in enumerate(names, 1) if name in {"交易日期", "交易时间"}]
    if len(debits) == len(credits) == 1 and {names[column - 1] for column in date_time} == {"交易日期", "交易时间"}:
        split = [debits[0]["index"] + 1, credits[0]["index"] + 1]
        parallel = [column for column, name in enumerate(names, 1)
                    if "交易金额" in name and column not in split and len(name) <= 16]
        if parallel:
            dates = "，".join(f"第 {column} 列{names[column - 1]}" for column in date_time)
            note([*date_time, *split, *parallel],
                 f"{dates}，第 {split[0]}、{split[1]} 列分别声明支出、收入。第 "
                 f"{'、'.join(map(str, parallel))} 列另有交易金额标签，需核对它与分列发生额的口径。",
                 (number for number, row in rows if all(row[column - 1].strip() for column in date_time)))
            counterpart = [(column, role) for column, name in enumerate(names, 1)
                           if (role := identity_field(name)) in {"counter_account", "counter_name"}]
            if len(counterpart) == 2 and {role for _, role in counterpart} == {"counter_account", "counter_name"}:
                declared = "，".join(f"第 {column} 列{'对方账号' if role == 'counter_account' else '对方户名'}"
                                    for column, role in counterpart)
                note([column for column, _ in counterpart], f"原表分别声明{declared}。")

    for marker in candidates.get("direction", ()):
        column = marker["index"] + 1
        if column == resolved_direction_column or names[column - 1] not in {"借贷标志", "收付标志"}:
            continue
        split_rows = split_scope[1] if split_scope and split_scope[0] == column else ()
        unknown = [number for number, row in rows
                   if number not in split_rows and row[column - 1].strip()
                   and table_import._enum(row[column - 1]) not in table_import._IN | table_import._OUT]
        if unknown:
            note([column], "这些原行的方向值缺少可采用的释义，收付仍待核。", unknown)
        elif party_labels and rows and len(candidates["direction"]) == 1:
            incoming = [number for number, row in rows if table_import._enum(row[column - 1]) in table_import._IN]
            outgoing = [number for number, row in rows if table_import._enum(row[column - 1]) in table_import._OUT]
            if incoming or outgoing:
                statements = []
                for label, group in (("入账", incoming), ("出账", outgoing)):
                    if group:
                        shown = "、".join(map(str, group[:6]))
                        ending = f"等 {len(group)} 行" if len(group) > 6 else "行"
                        statements.append(f"原第 {shown} {ending}记为{label}")
                note([column], "按原方向栏，" + "，".join(statements) + "。结算状态需另核。",
                     sorted(incoming + outgoing))
    return notes


def verify(case_id, import_id, body):
    from . import imports

    session, manifest, profile = _ready(case_id, import_id, body.revision)
    regions = _regions(manifest)
    claims = {(item.member_path, item.header_row): item.column for item in body.claims}
    if len(claims) != len(body.claims):
        raise ServiceError(422, "outside_ready_evidence", "每个表头区域只能提交一个原列主张")

    verdicts = []
    with imports._source(session, snapshot=True) as snapshot:
        for (member, header), column in claims.items():
            selected = _select(regions, member, header)
            original = _cells(snapshot, session, selected, set(selected["scope"]["header_rows"]), profile)
            if not 0 < column <= len(original[header]):
                raise ServiceError(422, "outside_ready_evidence", "物理列不在所选表头范围内")
            grouped = _grouped_header(snapshot, selected, original, profile, header)
            verdicts.append(_assess_column(selected, original, header, body.field, column, grouped))
    status, reason = next((item for item in verdicts if item[0] == "refuted"),
                          next((item for item in verdicts if item[0] == "insufficient"),
                               ("supported", "registered_source_role")))
    if status == "supported" and (not manifest["all_regions_sampled"] or set(claims) != set(regions)):
        status, reason = "insufficient", "incomplete_source_scope"
    _ready(case_id, import_id, body.revision)
    return {**_binding(session, manifest, profile), "field": body.field, "evidence_status": status, "reason": reason}
