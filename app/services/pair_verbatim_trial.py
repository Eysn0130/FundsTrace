"""A scoped, read-only local trial of physical pair evidence."""

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256

from .. import db
from . import assistance, desktop_model_trial as models, imports, import_verbatim_balance, trace_review, trial_pair_comparison
from .cases import ServiceError


@dataclass(frozen=True)
class LocalPairSource:
    source_id: str
    sha256: str
    member_path: str
    rows: tuple[int, ...]


@dataclass(frozen=True)
class LocalPairReview:
    case_id: int
    anchor_id: int
    peer_id: int
    canvas_id: int
    review_token: str
    sources: tuple[LocalPairSource, ...]
    columns: tuple[int, ...]
    question: str
    mode: str
    provider_factory: Callable[[], models._LoopbackProvider]


_PREFIX = "仅按所列两份CSV的原报字面核对。共享列位按顺序定位；null为实缺。\n原据="
_LEVEL = {
    "BOOK": "能核为同一账面往来时保留两条原观察，所证两侧在画布合计一笔、一次金额。",
    "LEAD": "只能保留候选及两条原观察，在画布各按所选原行计笔、计额。",
    "SEPARATE": "在现有范围可排除对应，保留两条原观察，在画布分别计笔、计额。",
    "DEFER": "材料不足，保留两条原观察，画布等待核据再决定能否折计。",
}
_EFFECT = {"UNKNOWN": "不能核定正式效力", "PROVED": "原据证实正式效力"}


def _scope_error():
    return ServiceError(409, "verbatim_pair_scope_changed", "原件或核对范围已更新，请重新读取")


def _positions(values):
    return (type(values) is tuple and 1 <= len(values) <= 16 and
            all(type(value) is int and value > 0 for value in values) and tuple(sorted(set(values))) == values)


def _physical_table(case_id, authority, columns):
    registered = trial_pair_comparison._registered_feedback_member(case_id, authority.source_id)
    if registered is None:
        raise _scope_error()
    source, locator, document = registered
    if (source["sha256"], locator["member_path"]) != (authority.sha256, authority.member_path):
        raise _scope_error()
    _, mapping = trace_review._original_layout(case_id, locator)
    if (not trial_pair_comparison._bank32_layout(locator, document)
            or not trial_pair_comparison._bank32_registered_roles(document["columns"], mapping, locator["member_path"])
            or not max(authority.rows) <= document["source_rows"] <= trial_pair_comparison._BANK32_REFERENCE_ROWS
            or locator["size_bytes"] > trial_pair_comparison._REFERENCE_SCAN_BYTES):
        raise _scope_error()
    selected = {1, *authority.rows}
    physical = trace_review._original_cells(imports._source(source, snapshot=True), locator, document, (), selected)
    if set(physical) != selected or max(columns) > len(physical[1]):
        raise _scope_error()
    return {
        "摘要": authority.sha256, "成员": authority.member_path,
        "表头": [f"C{column}｜{physical[1][column - 1]['value']}" for column in columns],
        "原行": {f"R{position}": [row[column - 1]["value"] if column <= len(row) else None for column in columns]
               for position in authority.rows if (row := physical[position]) is not None},
    }


def _source_data(case_id, grant, active):
    with db._lock:
        if not active():
            raise _scope_error()
        review = trace_review._require(grant.review_token, case_id, grant.canvas_id, anchor_id=grant.anchor_id)
        selected_ids = {grant.anchor_id, grant.peer_id}
        if len(selected_ids) != 2 or not selected_ids <= review.flow_ids:
            raise _scope_error()
        locators = {row["flow_id"]: row for row in trace_review._locators(case_id, selected_ids, include_ledger=True)}
        if set(locators) != selected_ids:
            raise _scope_error()
        origins = {row["source_id"]: row for row in locators.values()}
        if len(origins) != 2 or set(origins) != {item.source_id for item in grant.sources}:
            raise _scope_error()
        files, selected, selected_sources = {}, {}, {}
        for number, authority in enumerate(grant.sources, 1):
            origin = origins[authority.source_id]
            position = origin["source_row"]
            if (authority.sha256, authority.member_path) != (origin["sha256"], origin["member_path"]) or position not in authority.rows:
                raise _scope_error()
            source = f"S{number}"
            selected[origin["flow_id"]] = f"{source}R{position}"
            selected_sources[origin["flow_id"]] = source
            files[source] = _physical_table(case_id, authority, grant.columns)
        visible = assistance._flow_rows(case_id, selected_ids)
        if set(visible) != selected_ids:
            raise _scope_error()
        ledger = trace_review.pair_review(case_id, visible[grant.anchor_id], product=True).get("ledger_correspondence") or {}
        trace_review._require(grant.review_token, case_id, grant.canvas_id, anchor_id=grant.anchor_id)
        if not active():
            raise _scope_error()
        binding = {"case_id": str(case_id), "canvas_id": str(grant.canvas_id), "revision": review.revision}
    witnesses = set()
    if ledger.get("peer_id") == str(grant.peer_id):
        for flow_id, positions in zip((grant.anchor_id, grant.peer_id), ledger["witness_rows"], strict=True):
            source = selected_sources[flow_id]
            item = files[source]
            witnesses.update(f"{source}R{position}" for position in positions if f"R{position}" in item["原行"])
            witnesses.update(f"{source}{column}" for entry in item["表头"]
                             if (parts := entry.split("｜", 1))[1] in {"交易对手账卡号", "余额", "交易余额"}
                             for column in parts[:1])
    return {"范围": binding, "锚": selected[grant.anchor_id], "物理列": list(grant.columns), "原表": files}, selected[grant.peer_id], witnesses


def _questions(evidence):
    anchor_source = evidence["锚"].split("R", 1)[0]
    peers = {f"{source}{row}": f"原表 {source} 第{row[1:]}行" for source, table in evidence["原表"].items()
             if source != anchor_source for row in table["原行"]}
    witnesses = {f"{source}{row}": f"原表 {source} 第{row[1:]}行" for source, table in evidence["原表"].items() for row in table["原行"]}
    witnesses.update({f"{source}{column.split('｜', 1)[0]}": f"原表 {source} {column}"
                      for source, table in evidence["原表"].items() for column in table["表头"]})
    return {key: {"type": "choice", "instructions": wording, "criteria": choices} for key, wording, choices in (
        ("level", "按待核问题和原据选择账面处理。", _LEVEL),
        ("peer", "引用该判断所据的对侧物理原行。", {**peers, "NONE": "没有可核原行"}),
        ("effect", "原据是否核定正式效力？", _EFFECT),
        ("witness", "引用能区分对应或竞争的一项物理原行或原列。", {**witnesses, "NONE": "没有区分依据"}),
    )}


def _position_reply(value, questions):
    if not isinstance(value, dict) or value.keys() != questions.keys():
        return None
    return value if all(type(code) is str and code in questions[name]["criteria"] for name, code in value.items()) else None


def run(case_id, body, endpoints, grant, active):
    if (type(grant) is not LocalPairReview or grant.mode not in {"two_plus_one", "single_qwen"}
            or not import_verbatim_balance._host_allowed(endpoints)):
        raise ServiceError(409, "verbatim_pair_host_unavailable", "没有可核准的本机原件模型执行环境")
    cid = int(case_id)
    if (body.kind != "pair" or (cid, int(body.anchor_id or 0), int(body.canvas_id or 0), body.review_token)
            != (grant.case_id, grant.anchor_id, grant.canvas_id, grant.review_token)):
        raise _scope_error()
    if (type(grant.sources) is not tuple or len(grant.sources) != 2 or
            any(type(source) is not LocalPairSource or not _positions(source.rows) for source in grant.sources)
            or not _positions(grant.columns) or type(grant.question) is not str or not 1 <= len(grant.question) <= 600):
        raise ServiceError(422, "verbatim_pair_material_outside_scope", "没有核准的两侧原件范围")
    evidence, peer, _ = _source_data(cid, grant, active)
    questions = _questions(evidence)
    original_material = _PREFIX + models._json(evidence)
    # D3 encodes each choice separately. Every choice needs the same user question.
    state = "待核问题=" + models._json(grant.question) + "\n" + original_material
    provider = grant.provider_factory()
    if not isinstance(provider, models._LoopbackProvider) or provider.endpoints != endpoints:
        raise ServiceError(409, "verbatim_pair_host_unavailable", "没有可核准的本机原件模型执行环境")

    def fresh():
        current, bound_peer, witnesses = _source_data(cid, grant, active)
        if current != evidence or bound_peer != peer:
            raise _scope_error()
        return witnesses

    def qwen(prior=()):
        text = state + "\n输出字段及代号=" + models._json(questions)
        if prior:
            text += "\n请按同一原据复核首答=" + models._json(prior)
        return provider._completion(text, import_verbatim_balance._schema(questions), before_request=fresh)

    def checked(tag, answer):
        draft = _position_reply(answer, questions)
        witnesses = fresh()
        agrees = bool(witnesses and draft and draft["level"] == "BOOK" and draft["peer"] == peer and draft["effect"] == "UNKNOWN")
        original = bool(draft and draft["witness"] in witnesses)
        return {"model": tag, "proposal": draft, "admitted_original": agrees and original,
                "book_and_effect_match": agrees, "distinctive_physical_reference_verified": original}

    heads = models.first_reviewers(endpoints) if grant.mode == "two_plus_one" else (models._RECHECK,)
    replies, rechecks, failures = [], 0, 0
    for tag in heads:
        fresh()
        try:
            answer = qwen() if grant.mode == "single_qwen" else provider._native(tag, state, questions)
        except models.ProviderError:
            answer = None
            failures += 1
        replies.append(checked(tag, answer))
    admitted = [reply["admitted_original"] for reply in replies]
    accepted = all(admitted)
    if len(heads) == 2 and all(reply["proposal"] for reply in replies) and any(admitted) and not accepted:
        try:
            answer = qwen([item["proposal"] for item in replies])
        except models.ProviderError:
            answer = None
            failures += 1
        rechecks = 1
        reviewed = checked(models._RECHECK, answer)
        replies.append(reviewed)
        accepted = reviewed["admitted_original"]

    facts = {**evidence["范围"], "kind": "pair", "anchor_id": str(grant.anchor_id),
             "local_pair_read_only": True, "financial_admission": False,
             "local_model_original_sha256": sha256(original_material.encode()).hexdigest(), "local_model_mode": grant.mode,
             "local_question_sha256": sha256(grant.question.encode()).hexdigest(),
             "local_model_replies": replies, "local_model_columns": list(grant.columns),
             "local_model_source_rows": {key: len(item["原行"]) for key, item in evidence["原表"].items()}}
    packet = assistance._packet("pair", "本机原表物理范围内的只读对应核对。", facts, [], [], ("source_proof",))
    reason = "provider_error" if failures else "verbatim_pair_book_verified" if accepted else "verbatim_pair_unproved"
    return {"state": "unavailable" if failures else "proposal" if accepted else "pending",
            "reason": reason, "packet": packet, "first_calls": len(heads), "recheck_calls": rechecks}
