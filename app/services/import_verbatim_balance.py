"""Read-only monetary verification for one approved local trial source."""
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
import json
import sys

from .. import db
from . import assistance, desktop_model_trial as models, import_balance_trial as balance
from . import import_layout_trial, import_ready_evidence, import_verbatim_money as money, imports, trial_user_decision
from .cases import ServiceError


@dataclass(frozen=True)
class LocalBalanceReview:
    """An in-process grant from the isolated host. HTTP callers cannot supply it."""

    case_id: int
    import_id: str
    revision: str
    source_sha256: str
    member_path: str
    columns: tuple[int, ...]
    mode: str
    provider_factory: Callable[[], models._LoopbackProvider]
    purpose: str = "balance"


_QUESTION = (
    "先按活动原表角色核对余额。引用本方原报余额的物理列及有值原行。"
    "只有可用余额或对端余额时如实标明，不能代替本方原报余额。"
    "依据不足就待核。仅在原据明示时点时才回答笔后。"
)
_ROLES = {"OWN": "本方原报余额", "AVAILABLE": "可用余额", "PEER": "对端余额", "DEFER": "证据不足"}
_CERTIFIED = {"OWN": "own_reported_balance", "AVAILABLE": "available_balance"}
_TIMING = {"UNPROVED": "无法核实笔后时点", "POST": "原件明确是笔后时点"}
_PREFIX = "只核本原件所列物理位置及字面原值，不写入数据。空字符串为原格空，null为实缺。\n原据="


def _host_allowed(endpoints):
    if getattr(sys, "frozen", False) or sys.platform != "darwin" or not endpoints:
        return False
    if not models.has_model_roles(endpoints) or any(host != "127.0.0.1" for host, _ in endpoints.values()):
        return False
    root = db.DATA_DIR.resolve()
    return (
        root.name == "data"
        and root.parent.name.startswith("funds-trace-model-trial-")
        and root.parent.is_dir()
        and not (root.parent.stat().st_mode & 0o077)
    )


def _registered_original(case_id, import_id, body, grant, active):
    if not active():
        raise ServiceError(409, "original_balance_review_revoked", "本机原件核对授权已失效")
    with db._lock:
        session, manifest, profile = import_ready_evidence._ready(case_id, import_id, body.revision, for_layout=True)
        if session["sha256"] != grant.source_sha256:
            raise ServiceError(409, "original_balance_scope_changed", "原件或核对范围已更新，请重新读取")
        request = json.loads(session["request_json"] or "{}")
        members = manifest.get("layout_members", ())
        if (profile not in balance._SOURCE_PROFILES or session["suffix"] != ".csv" or len(members) != 1
                or request.get(imports._INTAKE_KEY) != imports._INTAKE_POLICY or request.get(imports._CONFIRMED_LAYOUT)
                or session["size_bytes"] > import_layout_trial._MAX_FILE_BYTES):
            raise ServiceError(422, "original_balance_scope_unavailable", "本次原件核对仅限已登记的短表区")
        [entry] = members
        scope = entry["scope"]
        if scope["member_path"] != grant.member_path or scope["header_rows"] not in ([], [1]):
            raise ServiceError(409, "original_balance_scope_changed", "原件或核对范围已更新，请重新读取")
        if not 2 <= scope["source_rows"] <= 5:
            raise ServiceError(422, "original_balance_scope_unavailable", "本次原件核对仅限已登记的短表区")
        positions = range(1, scope["source_rows"] + 1)
        with imports._source(session, snapshot=True) as original:
            grid = import_ready_evidence._cells(original, session, entry, set(positions), profile, for_layout=True)
        if set(grid) != set(positions):
            raise ServiceError(409, "original_balance_scope_changed", "不能核回当前表区的全部物理原行")
        header = grid.pop(1)
        if any(not 1 <= col <= len(header) for col in grant.columns):
            raise ServiceError(422, "original_balance_columns_unavailable", "授权原列不在当前表区")
        binding = {**import_ready_evidence._binding(session, manifest, profile),
                   "member_path": scope["member_path"], "header_row": 1, "field": "balance"}
        data = {
            "范围": binding,
            "表头": {f"C{col}｜{header[col - 1]['value']}": f"C{col}" for col in grant.columns},
            "原行": {
                f"R{number}": {f"C{col}": [cells[col - 1]["kind"], cells[col - 1]["value"]] if len(cells) >= col else None
                               for col in grant.columns}
                for number, cells in sorted(grid.items())
            },
        }
        if grant.purpose == "balance":
            roles = balance._source_roles_from_grid(scope, header, grid, profile, session["mode"], preserve_executable_role=True)
        else:
            roles = money.proof(header, grid, profile, grant.columns, grant.purpose,
                                registered=scope["header_rows"] == [1])
    return binding, data, roles


def _schema(questions):
    return {"type": "object", "additionalProperties": False, "required": list(questions),
            "properties": {field: {"enum": list(question["criteria"])} for field, question in questions.items()}}


def _questions(data):
    columns = {value: key for key, value in data["表头"].items()}
    rows = {position: position for position in data["原行"]}
    choices = {
        "role": (_QUESTION, _ROLES),
        "column": ("引用承担该角色的原物理列。", {**columns, "NONE": "没有可核原列"}),
        "row": ("引用该列实际有值的一条原行。", {**rows, "NONE": "没有可核原行"}),
        "timing": ("原据是否明确说明余额为笔后？", _TIMING),
    }
    return {key: {"type": "choice", "instructions": wording, "criteria": values}
            for key, (wording, values) in choices.items()}


def _reply(answer, questions):
    if not isinstance(answer, dict) or answer.keys() != questions.keys():
        return None
    if any(type(code) is not str or code not in questions[field]["criteria"] for field, code in answer.items()):
        return None
    return answer


def _verified(answer, roles):
    if not answer or answer["timing"] != "UNPROVED" or answer["row"] == "NONE":
        return None
    expected = _CERTIFIED.get(answer["role"])
    if expected == "available_balance" and any(role["role"] == "own_reported_balance" for role in roles):
        return None
    return next((role for role in roles if role["role"] == expected and answer["column"] == f"C{role['column']}"
                 and int(answer["row"][1:]) in role["source_rows"]), None)


def run(case_id, import_id, body, endpoints, grant, active):
    if (type(grant) is not LocalBalanceReview or grant.mode not in {"two_plus_one", "single_qwen"}
            or grant.purpose not in {"balance", "units", "amount_roles"}
            or not _host_allowed(endpoints)):
        raise ServiceError(409, "original_balance_host_unavailable", "没有可核准的本机原件模型执行环境")
    if ((int(case_id), import_id, body.revision, body.member_path, body.header_row)
            != (grant.case_id, grant.import_id, grant.revision, grant.member_path, 1)):
        raise ServiceError(409, "original_balance_scope_changed", "原件或核对范围已更新，请重新读取")
    monetary = grant.purpose != "balance"
    column_limit = 18 if monetary else 10
    if (not 1 <= len(grant.columns) <= column_limit or any(type(col) is not int for col in grant.columns)
            or tuple(sorted(set(grant.columns))) != grant.columns):
        raise ServiceError(422, "original_balance_columns_unavailable", "没有核准的有界原列")

    def current():
        return _registered_original(case_id, import_id, body, grant, active)

    binding, evidence, _ = current()
    question = money.question(grant.purpose) if monetary else _QUESTION
    questions = money.questions(evidence, grant.purpose) if monetary else _questions(evidence)
    prefix = "原问题=" + question + "\n" if monetary else ""
    material = prefix + _PREFIX + models._json(evidence)
    digest = sha256(material.encode()).hexdigest()
    provider = grant.provider_factory()
    if not isinstance(provider, models._LoopbackProvider) or provider.endpoints != endpoints:
        raise ServiceError(409, "original_balance_host_unavailable", "没有可核准的本机原件模型执行环境")

    def fresh():
        _, updated, proved = current()
        if updated != evidence:
            raise ServiceError(409, "original_balance_scope_changed", "原件或核对范围已更新，请重新读取")
        return proved

    def qwen(extra=""):
        output = material + ("\n" + question if not monetary else "") + "\n输出字段及代号=" + models._json(questions) + extra
        return provider._completion(output, _schema(questions), before_request=fresh)

    heads = models.first_reviewers(endpoints) if grant.mode == "two_plus_one" else (models._RECHECK,)
    drafts, reply_records, checked, rechecks, transport_failures = [], [], [], 0, 0

    def verify_reply(tag, draft, proved):
        verified = money.verified(draft, proved, questions) if monetary else _verified(draft, proved)
        record = {"model": tag, "proposal": draft, "admitted_original": verified is not None}
        if monetary and (interpretations := money.output_interpretations(draft, proved)):
            record.update(program_output_interpretations=interpretations,
                          literal_verification_reason="verbatim_money_unproved")
        reply_records.append(record)
        return verified

    for tag in heads:
        fresh()
        try:
            reply = qwen() if grant.mode == "single_qwen" else provider._native(tag, material, questions)
        except models.ProviderError:
            reply = None
            transport_failures += 1
        draft = _reply(reply, questions)
        drafts.append(draft)
        proved = fresh()
        checked.append(verify_reply(tag, draft, proved))

    choice = None
    if len(heads) == 1 or (checked[0] is not None and checked[0] == checked[1]):
        choice = checked[0]
    elif all(drafts) and (money.reviewable(drafts, proved) if monetary else any(checked)):
        # All approved columns and every physical data row were in both first
        # packets. A missing cited cell is not a license to read outside them.
        # Only the first proposals, never the verifier's booleans, reach Qwen.
        fresh()
        reviewed = trial_user_decision.review(
            {"首判原答": drafts},
            lambda view: qwen("\n请按同一原表复核有实质差异的原答=" + models._json(view)),
            models.ProviderError,
        )
        rechecks = reviewed.calls
        draft = _reply(reviewed.reply, questions)
        choice = verify_reply(models._RECHECK, draft, fresh())
        transport_failures += bool(reviewed.failure)

    records = []
    if choice and not monetary:
        col = choice["column"]
        records.append({"id": f"C{col}", "raw": {"heading": choice["heading"]}, "structured": {"role_check": "supported"},
                        "locator": {"member_path": body.member_path, "source_row": body.header_row, "column": col}})
    facts = {**binding, "financial_admission": False,
             "local_model_mode": grant.mode, "local_model_original_sha256": digest,
             "local_model_columns": list(grant.columns), "local_model_source_rows": list(range(2, len(evidence["原行"]) + 2)),
             "local_model_replies": reply_records, "local_model_added_reads": 0}
    if monetary:
        facts.update(local_original_money_read_only=True, local_model_money_finding=choice)
        reason = "verbatim_money_verified" if choice else "verbatim_money_unproved"
    else:
        facts["local_original_balance_read_only"] = True
        reason = "verbatim_balance_verified" if choice else "verbatim_balance_unproved"
    if transport_failures:
        reason = "provider_error"
    packet = assistance._packet("import", question, facts, [], records, ("source_role", "coverage"))
    return {"state": "unavailable" if transport_failures else "rule" if choice else "pending",
            "reason": reason, "packet": packet, "first_calls": len(heads), "recheck_calls": rechecks,
            "source_balance_roles": [choice] if choice and not monetary and not transport_failures else []}

