"""Read source reference columns and verify a bounded, ready-only policy proposal."""
from collections import Counter
from dataclasses import dataclass
from functools import partial
from itertools import combinations
import json
import re
from threading import Lock

from app import table_import
from . import assistance, desktop_model_trial as model, import_ready_evidence, imports
from .cases import ServiceError


_CHOICES = {"SEPARATE": "编号分别有独立来源用途，分列保留",
            "KEEP": "现有证据支持继续沿用预览解释", "DEFER": "原表用途未能判定"}
_QUESTION = "核对当前原表交易流水号与凭证引用的用途，选择是否分别保留并标明原列。"
_OTHER_NUMBER_HEADERS = frozenset(("日志号", "传票号", "清算编号", "清算流水号", "清算号", "任务号", "任务流水号",
                                   "冲正原号", "原交易流水号", "原始交易号", "交易参考号", "参考号", "凭证序号"))
_SAFE_HEADERS = frozenset().union(*table_import._ALIASES.values(), *table_import._FINANCIAL_ALIASES.values(),
                                  map(table_import._norm, table_import.UNIFORM_FEEDBACK_35_COLUMNS),
                                  _OTHER_NUMBER_HEADERS)


@dataclass(frozen=True)
class LocalReferenceReview:
    """In-process authorization for physical rows of a single ready original."""

    case_id: int
    import_id: str
    revision: str
    source_sha256: str
    member_path: str
    rows: tuple[int, ...]


def _valid_witness_rows(rows):
    return (1 <= len(rows) <= assistance._LIMIT
            and all(type(row) is int and row >= 2 for row in rows)
            and tuple(sorted(set(rows))) == tuple(rows))


class DesktopReferenceScope:
    """One startup-approved original in one case, bound to its first ready upload."""

    def __init__(self, value):
        if (type(value) is not dict or value.keys() != {"case_id", "source_sha256", "member_path", "rows"}
                or type(value["case_id"]) is not int or not 0 < value["case_id"] < 2**63
                or type(value["source_sha256"]) is not str or not re.fullmatch("[0-9a-f]{64}", value["source_sha256"])
                or type(value["member_path"]) is not str
                or not re.fullmatch(r"table[.]csv|worksheets/[0-9]{4}", value["member_path"])
                or type(value["rows"]) is not list or not _valid_witness_rows(value["rows"])
                or value["rows"][-1] > import_ready_evidence.import_layout_trial._MAX_SOURCE_ROWS):
            raise ValueError("Invalid desktop reference review scope")
        self._case_id = value["case_id"]
        self._sha256 = value["source_sha256"]
        self._member = value["member_path"]
        self._rows = tuple(value["rows"])
        self._grant = None
        self._lock = Lock()

    def bind_upload(self, value):
        if (int(value["case_id"]) != self._case_id or not value.get("reference_policy_available")
                or value.get("reference_policy") is not None or value.get("layout_model_trial_conflict")):
            return
        layout = value.get("layout_review") or {}
        members = layout.get("members", ())
        if len(members) != 1:
            return
        [member] = members
        if (member["member_path"] != self._member or member["header_rows"] != [1]
                or self._rows[-1] > member["source_rows"]):
            return
        with self._lock:
            if self._grant is not None:
                return
            try:
                session, _, _ = import_ready_evidence._ready(
                    self._case_id, value["import_id"], layout["revision"], for_layout=True)
            except ServiceError as error:
                if error.code not in {"import_not_found", "ready_evidence_stale", "ready_evidence_unavailable"}:
                    raise
                return
            if session["sha256"] == self._sha256:
                self._grant = LocalReferenceReview(
                    self._case_id, value["import_id"], layout["revision"], self._sha256, self._member, self._rows)

    def grant_for(self, case_id, import_id, body):
        grant = self._grant
        if grant is not None and (
            (int(case_id), import_id, body.revision, body.member_path, body.header_row)
            == (grant.case_id, grant.import_id, grant.revision, grant.member_path, 1)
        ):
            return grant
        return None


def _reference_counts(rows, column, *, physical=False):
    cells = [row[column - 1] if len(row) >= column and (not physical or row[column - 1]["present"]) else None
             for row in rows]
    kinds = Counter(cell["kind"] if cell is not None else "missing_physical_cell" for cell in cells)
    values = [cell["value"].strip() if cell is not None and cell["kind"] in {"text", "number"} else ""
              for cell in cells]
    result = {"kinds": dict(kinds), "nonempty": sum(bool(value) for value in values),
              "lengths": dict(Counter(len(value) for value in values if value))}
    if "missing_physical_cell" in kinds:
        result["missing_rows"] = [number for number, cell in enumerate(cells, 2) if cell is None]
    return result, values


def _verbatim_material(data, records, rows, member_path):
    columns = {record["id"]: record["locator"]["column"] for record in records}
    original_rows = {}
    for number in rows:
        cells = data[number - 2]
        original_rows[f"R{number}"] = {
            ref: {"kind": cell["kind"], "value": cell["value"]}
            if len(cells) >= column and (cell := cells[column - 1])["present"] else None
            for ref, column in columns.items()
        }
    return {"member_path": member_path, "原行": original_rows,
            "原格说明": "空字符串表示原格空，null表示没有物理原格。原值、类型及前导零按原格保留。"
                       "比较统计按两格去首尾空白后的文本计算。",
            "candidate_columns": {
                record["id"]: {**record["raw"], **_reference_counts(data, record["locator"]["column"], physical=True)[0]}
                for record in records}}


def _material(case_id, import_id, body, *, correct_fallback=False, witness_rows=()):
    session, manifest, profile = import_ready_evidence._ready(case_id, import_id, body.revision, for_layout=True)
    binding = import_ready_evidence._binding(session, manifest, profile)
    facts = {**binding, "member_path": body.member_path, "header_row": body.header_row}
    request = json.loads(session["request_json"] or "{}")

    def early(reason):
        return assistance._packet("import", _QUESTION, facts, [], [], ("source_role",)), None, reason, None

    if request.get("reference_policy") == table_import.TRANSACTION_REFERENCE_POLICY:
        return early("reference_policy_already_selected")
    if (profile not in table_import.VERIFIED_REFERENCE_PROFILES or session["suffix"] not in {".csv", ".xlsx"}
            or request.get(imports._INTAKE_KEY) != imports._INTAKE_POLICY):
        return early("reference_policy_not_applicable")
    if request.get("_table_overrides") or request.get(imports._CONFIRMED_LAYOUT):
        return early("reference_policy_manual_override")
    if imports._reference_history_conflicts(session, verified=True):
        raise ServiceError(409, "reference_original_active", imports._errors["reference_original_active"])
    members = manifest.get("layout_members", ())
    if (len(members) != 1 or manifest["layout_source_rows"] > import_ready_evidence.import_layout_trial._MAX_SOURCE_ROWS
            or session["size_bytes"] > import_ready_evidence.import_layout_trial._MAX_FILE_BYTES):
        return early("reference_scope_incomplete")
    [entry] = members
    scope = entry["scope"]
    if (scope["member_path"] != body.member_path or scope["header_rows"] != [1] or body.header_row != 1
            or not 1 < scope["source_rows"]):
        return early("reference_scope_incomplete")
    last = scope["source_rows"]
    with imports._source(session, snapshot=True) as source:
        physical = import_ready_evidence._cells(source, session, entry, set(range(1, last + 1)), profile,
                                                for_layout=True, physical_presence=bool(witness_rows))
    import_ready_evidence._ready(case_id, import_id, body.revision, for_layout=True)
    header = physical[1]
    width = len(header)
    data = [physical[number] for number in range(2, last + 1)]
    strict_width = session["suffix"] == ".csv" and profile != table_import.CSV_TAIL_REFERENCE_PROFILE
    if not width or width > 64 or any(
        len(row) > 64 or strict_width and len(row) != width
        or len(row) > width and (not correct_fallback or any(
            cell["kind"] not in {"text", "number", "empty"} or cell["value"] != "" for cell in row[width:]))
        for row in data
    ):
        return early("reference_scope_incomplete")
    headings = [cell["value"] if cell["kind"] == "text" else "" for cell in header]
    positions = table_import.explicit_reference_columns(headings, profile)
    if positions is None:
        return early("reference_columns_unproved")
    # A workbook can omit later cells without changing physical column numbers.
    # Both interpreted source roles must still be present in every retained row.
    if any(len(row) < max(positions) for row in data):
        return early("reference_scope_incomplete")
    candidate_columns = set(positions) | {
        index for index, value in enumerate(headings, 1) if len(value) <= 64 and table_import._norm(value) in _OTHER_NUMBER_HEADERS
    }
    if len(candidate_columns) > 8:
        return early("reference_scope_incomplete")
    records, values = [], {}
    for column in sorted(candidate_columns):
        stats, raw = _reference_counts(data, column)
        if column in positions and set(stats["kinds"]) - ({"text", "number", "empty"} if correct_fallback else {"text", "number"}):
            return early("reference_columns_unproved")
        records.append({"id": f"C{column}", "raw": {"heading": headings[column - 1]},
                        "structured": stats, "locator": {"member_path": body.member_path, "source_row": 1,
                                                         "column": column}})
        values[column] = raw
    comparisons = {}
    for first, second in combinations(values, 2):
        paired = [(number, left, right) for number, (left, right) in
                  enumerate(zip(values[first], values[second], strict=True), 2) if left and right]
        comparisons[first, second] = {
            "columns": [f"C{first}", f"C{second}"], "both_nonempty": len(paired),
            "equal_when_both_nonempty": sum(left == right for _, left, right in paired),
            "first_difference": next((number for number, left, right in paired if left != right), None),
        }
    target = comparisons[tuple(sorted(positions))]
    primary, voucher = (values[column] for column in positions)
    fallback_rows = [number for number, (left, right) in enumerate(zip(primary, voucher, strict=True), 2)
                     if not left and right] if correct_fallback else []
    if not fallback_rows and (correct_fallback or target["first_difference"] is None):
        return early("reference_columns_agree")
    differing_rows = [number for number, (left, right) in enumerate(zip(primary, voucher, strict=True), 2)
                      if left and right and left != right]
    items = [item for item in request.get(imports._INTAKE_ITEMS, ())
             if item["member_path"] == body.member_path and item["header_row"] == 1]
    if len(items) != 1 or (not correct_fallback and items[0]["unresolved"] == 0):
        return early("reference_policy_not_applicable")
    facts.update(last_row=last, covered_rows=last - 1, first_difference=target["first_difference"],
                 comparisons=list(comparisons.values()), header_columns=[
                     {"column": index, "heading": value if len(value) <= 64 and table_import._norm(value) in _SAFE_HEADERS else None}
                     for index, value in enumerate(headings, 1)])
    refs = [record["id"] for record in records]
    candidates = [assistance._candidate(choice, _CHOICES[choice], refs) for choice in ("SEPARATE", "KEEP")]
    packet = assistance._packet("import", _QUESTION, facts, candidates, records, ("source_role",))
    proof = {"policy": table_import.VERIFIED_REFERENCE_POLICY, "sha256": session["sha256"],
             "packet_sha256": packet["packet_sha256"], "member_path": body.member_path,
             "header_row": 1, "last_row": last, "columns": list(positions), "previous_accepted": items[0]["accepted"],
             "differing_rows": differing_rows}
    if fallback_rows:
        proof["fallback_rows"] = fallback_rows
    verbatim = None
    if witness_rows:
        if witness_rows[-1] > last or not set(witness_rows).intersection(differing_rows):
            raise ServiceError(422, "original_reference_rows_unavailable", "核准范围须包括能区分编号的原行")
        verbatim = _verbatim_material(data, records, witness_rows, scope["member_path"])
    return packet, proof, None, verbatim


def prepare_upload(value):
    """Select the independently proven reference roles on a first trial preview."""
    from app.api.models import ImportReferenceTrialRequest

    layout = value.get("layout_review") or {}
    members = layout.get("members", ())
    if (value.get("reference_policy") is not None or len(members) != 1
            or members[0]["header_rows"] != [1]):
        return value
    member = members[0]["member_path"]
    table = value.get("table_summary") or {}
    # A registered standalone workbook may already know both roles and still
    # replay the historical projection that fills an empty main ID from a
    # voucher. Test that case on its own, even when all rows were admitted.
    fallback = (member.startswith("worksheets/") and table.get("profile") == table_import.VOUCHER_REFERENCE_PROFILE
                and {entry["basis"] for entry in table.get("mapping_evidence", ())
                     if entry["field"] in {"trade_no", "voucher_reference"}
                     and entry["member_path"] == member and entry["header_row"] == "1"}
                == {"source_transaction_id", "independent_voucher_reference"})
    if not fallback and (not value.get("layout_model_trial") or value.get("automatic") is not False
                         or not value.get("reference_policy_available") or member != "table.csv"):
        return value
    body = ImportReferenceTrialRequest(revision=layout["revision"], member_path=member, header_row=1)
    case_id, import_id = value["case_id"], value["import_id"]
    try:
        _, proof, reason, _ = _material(case_id, import_id, body, correct_fallback=fallback)
        if reason is not None:
            return value
        return imports.prepare_reference_trial(case_id, import_id, body.revision, proof, rule=True)
    except ServiceError as error:
        if error.code not in {"reference_layout_not_admitted", "reference_original_active", "invalid_table_file",
                              "ready_evidence_unreadable", "ready_evidence_stale", "ready_evidence_unavailable",
                              "source_changed", "source_missing", "unsafe_upload_storage"}:
            raise
        return value


def _questions(refs, *, verbatim=False):
    citations = {ref: f"{ref}｜{record['heading']}" if verbatim else f"引用物理列 {ref}"
                 for ref, record in refs.items()} | {"NONE": "没有足够原列证据"}
    return {key: {"type": "choice", "instructions": prompt, "criteria": choices}
            for key, prompt, choices in (
                ("choice", _QUESTION, _CHOICES),
                ("transaction", "选承担每笔交易流水标识的原列。没有可核证据选NONE。", citations),
                ("voucher", "选记载独立凭证引用的原列。没有可核证据选NONE。", citations),
            )}


def _checked(answer, questions):
    if (not isinstance(answer, dict) or answer.keys() != questions.keys()
            or any(not isinstance(value, str) or value not in questions[key]["criteria"] for key, value in answer.items())):
        raise model.ProviderError("reference reply outside original column scope")
    return answer


def _supported(answer, positions):
    return (answer["choice"] == "SEPARATE"
            and [answer["transaction"], answer["voucher"]] == [f"C{position}" for position in positions])


def _review(request, heads, view, questions, positions):
    first, first_calls, recheck_calls = [], 0, 0
    for tag in heads:
        first_calls += 1
        try:
            first.append(_checked(request(tag, view, questions), questions))
        except model.ProviderError:
            continue
    if len(first) != len(heads):
        return None, "provider_error", first_calls, recheck_calls
    try:
        supported = [_supported(answer, positions) for answer in first]
        if all(supported):
            return first[0], "first_agreement", first_calls, recheck_calls
        if any(supported):
            recheck_calls += 1
            third = _checked(request(model._RECHECK, view, questions, first), questions)
            return third, "recheck_supported", first_calls, recheck_calls
        reason = "reference_keep_existing" if all(answer["choice"] == "KEEP" for answer in first) else "no_agreement"
        return None, reason, first_calls, recheck_calls
    except model.ProviderError:
        return None, "provider_error", first_calls, recheck_calls


def _authorized_reader(case_id, import_id, body, endpoints, grant, active):
    from .import_verbatim_balance import _host_allowed

    if type(grant) is not LocalReferenceReview or active is None or not _host_allowed(endpoints):
        raise ServiceError(409, "original_reference_host_unavailable", "没有可核准的本机原件模型执行环境")
    if ((int(case_id), import_id, body.revision, body.member_path, body.header_row)
            != (grant.case_id, grant.import_id, grant.revision, grant.member_path, 1)):
        raise ServiceError(409, "original_reference_scope_changed", "原件或核对范围已更新，请重新读取")
    if type(grant.rows) is not tuple or not _valid_witness_rows(grant.rows):
        raise ServiceError(422, "original_reference_rows_unavailable", "没有核准的有界编号原行")

    def current():
        if not active():
            raise ServiceError(409, "original_reference_review_revoked", "本机原件核对授权已失效")
        session, _, _ = import_ready_evidence._ready(case_id, import_id, body.revision, for_layout=True)
        if session["sha256"] != grant.source_sha256:
            raise ServiceError(409, "original_reference_scope_changed", "原件或核对范围已更新，请重新读取")
        imports._source(session)
        import_ready_evidence._ready(case_id, import_id, body.revision, for_layout=True)
        if not active():
            raise ServiceError(409, "original_reference_review_revoked", "本机原件核对授权已失效")

    return current


def run(case_id, import_id, body, endpoints, *, grant=None, active=None):
    fresh, witness_rows = None, ()
    if grant is not None and endpoints is not None:
        fresh = _authorized_reader(case_id, import_id, body, endpoints, grant, active)
        fresh()
        witness_rows = grant.rows
    packet, proof, early, verbatim = _material(case_id, import_id, body, witness_rows=witness_rows)
    result = {"state": "pending", "reason": early, "packet": packet, "first_calls": 0, "recheck_calls": 0}
    if early is not None:
        return result
    if endpoints is None:
        return {**result, "state": "unavailable", "reason": "not_configured"}
    facts = packet["facts"]
    view = {key: facts[key] for key in ("header_row", "last_row", "covered_rows", "header_columns", "comparisons")}
    view["candidate_columns"] = {record["id"]: {**record["raw"], **record["structured"]}
                                 for record in packet["records"]}
    request = model._LoopbackProvider(endpoints).reference_policy
    if verbatim is not None:
        view.update(verbatim)
        request = partial(request, before_request=fresh)
    questions = _questions(view["candidate_columns"], verbatim=verbatim is not None)
    answer, reason, first, recheck = _review(request, model.first_reviewers(endpoints), view, questions, proof["columns"])
    if fresh is not None:
        fresh()
    result.update(first_calls=first, recheck_calls=recheck, reason=reason)
    if reason == "provider_error":
        return {**result, "state": "unavailable"}
    if answer is None:
        return result
    if not _supported(answer, proof["columns"]):
        return {**result, "reason": "reference_citations_unverified"}
    _, current, issue, _ = _material(case_id, import_id, body)
    if issue is not None or current != proof:
        raise ServiceError(409, "assistance_stale", "原件或核对范围已更新，请重新读取")
    try:
        prepared = imports.prepare_reference_trial(case_id, import_id, body.revision, proof)
    except ServiceError as error:
        if error.code != "reference_layout_not_admitted":
            raise
        return {**result, "reason": error.code}
    packet["selection"] = {"status": "review_only", "choice": "SEPARATE",
                           "refs": [answer["transaction"], answer["voucher"]], "gap": None}
    return {**result, "state": "proposal", "prepared_preview": prepared}
