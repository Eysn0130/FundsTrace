"""Evidence assistance for an explicitly enabled local desktop trial."""
from collections.abc import Callable, Mapping
from datetime import time
from hashlib import sha256
from http.client import HTTPConnection, HTTPException
import json
import re
from socket import SHUT_RDWR
from threading import Timer
from time import monotonic
from types import SimpleNamespace

from .. import bank_posting_pairs, principal_ledger, transaction_time
from . import assistance, import_column_trial, import_ready_evidence, imports, trace_explanation, trace_group_evidence, trial_user_decision
from .cases import ServiceError


_FIRST = ("d1-2b", "d3-nano")
_RECHECK = "qwen35-4b"
_MAX_BYTES = 128 * 1024
_SECONDS = 60
_CONTEXT = 8192
_PREDICT = 256
_MAX_REFS = 6
# Assistance exposes at most 120 characters per reference. Equal prefixes at
# this boundary do not establish equal source values.
_REFERENCE_EXCERPT = 120
_NO_THINK = "<|im_start|>assistant\n<think>\n\n</think>\n\n"
_DEFER = "DEFER"
_UNREADABLE = {"import_not_found", "source_missing", "source_changed", "unsafe_upload_storage"}

# One explicit, independently checkable fact about each chosen row. Agreement
# about where to look cannot by itself prove a role, transfer or attribution.
_FINDINGS = {
    "pair": ("比较所选候选和锚点的交易时钟。只比较同一 clock_basis；按 clock_day 比日期，"
             "同日按 clock_order，day_or_midnight 不可判定同日先后。",
             {"same": "时基相同且时钟相等", "earlier": "时基相同且候选更早",
              "later": "时基相同且候选更晚", "incomparable": "时基不同或缺少可比较时钟"}),
    "trace": ("查所选原行在来源账簿上的方向，区分原件标注与本方等于付款或收款方。",
              {"in": "材料明确入账", "out": "材料明确出账", "unknown": "材料无法确定方向"}),
}

ModelEndpoints = dict[str, tuple[str, int]]


class ProviderError(Exception):
    """A transport or reply failed without exposing source or response text."""


def first_reviewers(endpoints: Mapping[str, object] | None) -> tuple[str, str]:
    """Keep Lite available only with its explicit isolated comparison configuration."""
    if endpoints is not None and "d3-lite" in endpoints:
        return _FIRST[0], "d3-lite"
    return _FIRST


def has_model_roles(endpoints: Mapping[str, object]) -> bool:
    return endpoints.keys() == {*first_reviewers(endpoints), _RECHECK}


def parse_model_endpoints(value: str | dict | None) -> ModelEndpoints | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            raise ValueError("invalid desktop model endpoints") from None
    if not isinstance(value, dict) or not has_model_roles(value):
        raise ValueError("invalid desktop model endpoints")
    endpoints = {}
    for tag, origin in value.items():
        match = re.fullmatch(r"http://(127\.0\.0\.1|\[::1\]):([1-9][0-9]{0,4})", origin) if isinstance(origin, str) else None
        if not match or int(match[2]) > 65535:
            raise ValueError("invalid desktop model endpoint")
        endpoints[tag] = (match[1].strip("[]"), int(match[2]))
    if len(set(endpoints.values())) != len(endpoints):
        raise ValueError("desktop models require distinct endpoints")
    return endpoints


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_excerpt(value):
    """Keep vocabulary useful for column roles while removing embedded identifiers."""
    value = re.sub(r"(?:https?://|file:)\S+|(?:[\w.-]+[/\\])+[\w.-]+|[A-Za-z0-9_-]{24,}|[0-9]{4,}",
                   "…", value)
    return value.replace("<", "＜").replace(">", "＞")[:120]


def _source_positions(packet):
    """Verify case-bound originals before placing any review rows in a prompt."""
    records = packet["records"]
    if packet["domain"] == "import":
        # The layout assistance reader already read the digest-checked source.
        return {record["id"] for record in records if record["locator"].get("source_row")}
    sources = {}
    for record in records:
        locator = record["locator"]
        if locator.get("file_url"):
            source = locator["source_id"]
            sources[source] = locator.get("sha256") or sources.get(source)
    if packet["facts"].get("product_review"):
        available = assistance.verified_product_originals(packet["facts"]["case_id"], sources)
        return {row["id"] for row in records if row["locator"].get("file_url")
                and row["locator"]["source_id"] in available}
    available = set()
    for source_id in sorted(sources):
        try:
            # Same byte validation as a download without copying the original.
            session = imports._source_session(packet["facts"]["case_id"], source_id)
            imports._source(session)
        except ServiceError as exc:
            if exc.code not in _UNREADABLE:
                raise
        else:
            available.add(source_id)
    return {record["id"] for record in records
            if record["locator"].get("file_url") and record["locator"]["source_id"] in available}


def _candidate_ids(packet, positions):
    records = {record["id"]: record for record in packet["records"] if record["id"] in positions}
    locations, ids = set(), []
    for candidate in packet["candidates"]:
        ref = candidate["id"]
        if candidate["refuted"] or ref not in records or ref not in candidate["refs"]:
            continue
        record = records[ref]
        locator = record["locator"]
        location = tuple(locator.get(key) for key in ("source_id", "member_path", "source_row", "column"))
        if location not in locations:
            locations.add(location)
            ids.append(ref)
    return ids


def _source_clock(record):
    utc = record["structured"]["trade_time_utc"]
    stamp = transaction_time.parsed(utc or record["raw"]["trade_time"])
    return (stamp.replace(tzinfo=None) if stamp is not None else None,
            not utc and stamp is not None and stamp.time() == time.min)


def _clock_relation(first, second):
    first_basis, (first_stamp, first_day_only) = first
    second_basis, (second_stamp, second_day_only) = second
    if first_stamp is None or second_stamp is None:
        return "incomparable", False
    if first_basis != second_basis:
        return "incomparable", True
    # A bare midnight may denote any point during that day.
    if first_stamp.date() == second_stamp.date() and (first_day_only or second_day_only):
        return "incomparable", False
    return ("earlier" if first_stamp < second_stamp else "later" if first_stamp > second_stamp else "same"), True


def _own_ledger_direction(raw):
    parties = {f"{role}_account": raw[f"raw_{role}_account"] for role in ("self", "payer", "payee")}
    account = parties["self_account"]
    return trace_group_evidence._direction(parties, account) if account else None


def _trace_rule_matches(packet):
    """Reconcile the current, complete set of physical rows to the product interval."""
    facts = packet["facts"]
    if (facts["more_evidence"] or not facts["other_entries"]
            or not all(facts["checks"][key] for key in ("ledger_roles", "currency_amount", "own_balance", "interval_records"))):
        return False
    sources, targets = set(facts["source_refs"]), set(facts["target_refs"])
    rows = {row["id"]: row for row in packet["records"]}
    others = {item["id"] for item in packet["candidates"] if not item["refuted"]}
    if (not sources or not targets or len(sources) != facts["source_count"] or len(targets) != facts["target_count"]
            or sources & targets or (sources | targets) & others or set(rows) != sources | targets | others):
        return False
    accounts = {row["raw"]["raw_self_account"] for row in rows.values()}
    currency = facts["currency"]
    if len(accounts) != 1 or not next(iter(accounts)) or currency.strip().casefold() in principal_ledger.UNKNOWN_CURRENCIES:
        return False
    locations = set()
    totals = {side: {"count": 0, "units": 0} for side in ("in", "out")}
    for ref, row in rows.items():
        raw, locator = row["raw"], row["locator"]
        location = (locator["source_id"], locator["member_path"], locator["source_row"])
        if location in locations or raw["currency"] != currency:
            return False
        locations.add(location)
        direction = _own_ledger_direction(raw)
        declared = row["structured"]["book_direction"]
        units = trace_group_evidence._positive_units(raw["amount"])
        if direction is None or declared not in (None, "unknown", direction) or units is None:
            return False
        if ref in sources and direction != "in" or ref in targets and direction != "out":
            return False
        if ref in others:
            totals[direction]["count"] += 1
            totals[direction]["units"] += units
    return facts["other_entries"] == {
        side: {"count": total["count"], "amount": principal_ledger.money(total["units"])}
        for side, total in totals.items()
    }


def _trace_result(packet, positions):
    facts = packet["facts"]
    if "source_clock_scope_unproved" in facts["reasons"]:
        return "pending", "source_clock_scope_unproved"
    records = packet["records"]
    selected = {*facts["source_refs"], *facts["target_refs"]}
    if facts["conditional_group_share"] is not None:
        if "range_originals_verified" in facts:
            return (("rule", "ledger_range_available") if facts["range_originals_verified"]
                    else ("pending", "source_unavailable"))
        if not (selected | {row["id"] for row in records}) <= positions:
            return "pending", "source_unavailable"
        if _trace_rule_matches(packet):
            return "rule", "ledger_range_available"
    elif not facts["checks"]["own_balance"]:
        accounts = {row["raw"]["raw_self_account"] for row in records if row["id"] in selected}
        if not any(row["id"] in positions and row["structured"]["balance_kind"] == "post_book"
                   and row["raw"]["balance"] is not None and row["raw"]["currency"] == facts["currency"]
                   and row["raw"]["raw_self_account"] in accounts and _own_ledger_direction(row["raw"])
                   for row in records):
            return "pending", "ledger_evidence_missing"
    return None


def _trace_time_exclusion_refs(packet):
    facts = packet["facts"]
    if (not facts.get("product_review") or facts["conditional_group_share"] is not None
            or "selected_debits_precede_all_credits" not in facts["reasons"]
            or "source_clock_scope_unproved" in facts["reasons"]
            or not facts["checks"]["ledger_roles"] or not facts["checks"]["currency_amount"]):
        return set()
    credits, debits = set(facts["source_refs"]), set(facts["target_refs"])
    if (not credits or not debits or credits & debits
            or len(credits) != facts["source_count"] or len(debits) != facts["target_count"]):
        return set()
    return credits | debits


def _scope(packet, positions, candidate_ids):
    domain, facts = packet["domain"], packet["facts"]
    anchor = facts.get("anchor_ref")
    if domain == "pair" and anchor not in positions:
        return None
    records = {record["id"]: record for record in packet["records"] if record["id"] in positions}
    aliases = {ref: f"R{i}" for i, ref in enumerate(records, 1)}
    choices = [aliases[ref] for ref in candidate_ids]
    accounts, references, posts, books = {}, {}, {}, {}

    def pseudonym(group, value):
        return group.setdefault(value, len(group) + 1) if value else None

    # Rank only within each clock domain. Date-only possibilities receive a
    # source day but no fabricated within-day order.
    days, ranks = {}, {}
    clocks, origins = {}, {}
    for ref, record in records.items():
        basis = record["structured"]["clock_basis"]
        if facts.get("product_review"):
            locator = record["locator"]
            book = pseudonym(books, (locator.get("source_id"), locator.get("member_path")))
            if basis != "utc":
                basis = f"{basis}:S{book}"
            origins[ref] = f"S{book}"
        clocks[ref] = basis, _source_clock(record)
    for basis, stamp, uncertain in sorted({(basis, stamp, uncertain) for basis, (stamp, uncertain)
                                           in clocks.values() if stamp is not None}):
        basis_days = days.setdefault(basis, {})
        basis_days.setdefault(stamp.date(), len(basis_days) + 1)
        if not uncertain:
            same_day = ranks.setdefault((basis, stamp.date()), {})
            same_day[stamp] = len(same_day) + 1
    summaries, observed, checkable = {}, {}, set()
    for ref, record in records.items():
        raw, structured = record["raw"], record["structured"]
        alias = aliases[ref]
        basis, (stamp, date_only_possible) = clocks[ref]
        source_direction = structured["book_direction"] or "unknown"
        direction = source_direction
        if domain == "trace":
            own_direction = _own_ledger_direction(raw)
            if (raw["raw_payer_account"] and raw["raw_payer_account"] == raw["raw_payee_account"]
                    or own_direction and source_direction not in ("unknown", own_direction)):
                direction = "unknown"
            else:
                direction = own_direction or source_direction
        summary = {"clock_basis": basis, "clock_day": days.get(basis, {}).get(stamp.date()) if stamp else None,
                   "clock_order": ranks.get((basis, stamp.date()), {}).get(stamp) if stamp else None,
                   "clock_precision": "day_or_midnight" if date_only_possible else "time" if stamp else "unknown",
                   "posting_text_groups": [pseudonym(posts, value) for value in raw["posted_times"] if value],
                   "account_groups": {role: pseudonym(accounts, raw[f"raw_{role}_account"])
                                      for role in ("self", "payer", "payee")},
                   "amount": raw["amount"], "currency": raw["currency"], "balance": raw["balance"],
                   "book_direction": direction, "declared_direction": source_direction,
                   "balance_kind": structured["balance_kind"],
                   "reference_excerpt": any(len(value) >= _REFERENCE_EXCERPT for _, _, value in raw["reference_cells"]),
                   "reference_groups": [[role, pseudonym(references, (role, clean))]
                                        for role, _, value in raw["reference_cells"]
                                        if len(value) < _REFERENCE_EXCERPT
                                        and (clean := bank_posting_pairs._identifier(value))]}
        if domain == "pair":
            observed[alias], certain = _clock_relation(clocks[ref], clocks[anchor])
            if certain:
                checkable.add(alias)
            lead = structured.get("earlier_book_lead")
            summary.update(same_amount_currency=structured.get("same_amount_currency"),
                           same_endpoints=structured.get("same_endpoints"),
                           earlier_book_lead=aliases[lead] if lead in aliases
                           and _clock_relation(clocks[lead], clocks[ref]) == ("earlier", True) else None)
            if ref in origins and basis == clocks[anchor][0] == "utc" and stamp and clocks[anchor][1][0] and certain:
                delta = stamp - clocks[anchor][1][0]
                summary["trade_offset_us"] = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
        else:
            observed[alias] = direction
        if ref in origins:
            summary["source_book"] = origins[ref]
            summary["physical_row"] = record["locator"]["source_row"]
        summaries[alias] = summary
    if domain == "pair":
        anchor_alias = aliases[anchor]
        subject = summaries[anchor_alias]
        ref_groups = {tuple(group) for group in subject["reference_groups"]}
        traits = {key: (observed[key], summaries[key]["clock_basis"], summaries[key]["clock_day"],
                        summaries[key]["clock_order"], summaries[key]["book_direction"],
                        summaries[key]["balance_kind"], summaries[key]["same_endpoints"],
                        summaries[key]["same_amount_currency"], bool(summaries[key]["earlier_book_lead"]),
                        any(tuple(group) in ref_groups for group in summaries[key]["reference_groups"]))
                  for key in choices}
        context = {"anchor": anchor_alias, "more_candidates": facts["more_candidates"]}
    else:
        checkable = {key for key in choices if observed[key] != "unknown"}
        traits = {key: (observed[key], summaries[key]["clock_basis"], summaries[key]["clock_day"],
                        summaries[key]["clock_order"], summaries[key]["amount"], summaries[key]["currency"], summaries[key]["balance_kind"])
                  for key in choices}
        context = {"sources": [aliases[ref] for ref in facts["source_refs"] if ref in aliases],
                   "targets": [aliases[ref] for ref in facts["target_refs"] if ref in aliases],
                   "ledger_checks": facts["checks"], "conditional_share": facts["conditional_group_share"],
                   "opening_balance": facts["opening_balance"], "closing_balance": facts["closing_balance"],
                   "other_entries": facts["other_entries"], "reasons": facts["reasons"],
                   "more_evidence": facts["more_evidence"]}
        if facts.get("product_review"):
            context["ledger_rows"] = facts.get("ledger_row_count")
        if (len(context["sources"]) != facts["source_count"] or len(context["targets"]) != facts["target_count"]
                or not context["sources"] or not context["targets"]):
            return None
    if len(set(traits.values())) < 2:
        return None
    view = {"domain": domain, "question": packet["question"], "context": context, "records": summaries,
            "candidates": choices}
    return view, {alias: ref for ref, alias in aliases.items()}, observed, checkable


def _shutdown(sock):
    try:
        sock.shutdown(SHUT_RDWR)
    except OSError:
        pass


def _column_material(view, *, compact, adjacent):
    """Keep the evidence inventory fixed when comparing layout and wording."""
    records = view["records"]
    lines = ["只依据当前原列及来源说明判断用途。统计仅供定位，依据不足时待核。"]
    if (table_rows := view.get("table_rows")) and table_rows[1]:
        lines.append(f"当前表区第{table_rows[0]}至{table_rows[1]}行，其他表区说明不可引用。")
    printed = set()

    def write_note(note):
        if note in printed:
            return
        row = records[note]
        title = "原件说明" if row["完整"] else "不完整的说明摘录"
        lines.append(f"[{note}] 原行{row['说明原行']} {row.get('说明来源', '')} {title} {' '.join(row['说明'])}")
        printed.add(note)

    for column in view["candidates"]:
        row = records[column]
        heading = " / ".join(value for value in (row.get("父表头"), row["表头"]) if value)
        location = f"原行{row['原行']} 列{row['物理列']}" if compact else f"第{row['原行']}原行的第{row['物理列']}个物理列"
        samples = _json(row["样值形态"]) if "样值形态" in row else "、".join(row["样本类型"])
        links = row.get("说明位置") or []
        line = f"[{column}] {location} 表头 {heading} 样本形状 {samples} 相关说明 {','.join(links) or '无'}"
        profile = row.get("全列统计")
        if profile:
            comparisons = [item for item in profile["comparisons"] if f"C{item['column']}" in view["candidates"]][:3]
            if compact:
                line += f"；扫描/非空/前导零 {profile['scanned_rows']}/{profile['nonempty']}/{profile['leading_zero']}"
            else:
                line += (f"；全列实际扫描了{profile['scanned_rows']}行，其中非空有{profile['nonempty']}行，"
                         f"带前导零有{profile['leading_zero']}行")
            line += f"；类型 {_json(profile['kinds'])}，见证位置 {_json(profile['sample_positions'])}"
            for item in comparisons:
                line += (f"；对C{item['column']}同值{item['equal_present']}/{item['both_present']}，"
                         f"空缺{item['missing_rows']}，差{item['different']}，首差{item['first_difference'] or '无'}")
        lines.append(line)
        if adjacent:
            for note in links:
                write_note(note)
    for note in view["notes"]:
        write_note(note)
    return "\n".join(lines)


class _LoopbackProvider:
    def __init__(self, endpoints):
        self.endpoints = endpoints
        self.deadline = monotonic() + _SECONDS

    def business_decision(self, tag, view):
        # Keep the original text once. D3 evaluates one encoded prompt per
        # question, so repeating the evidence in choice labels uses its batch.
        state = ("只根据给出的业务范围及原件。无法核定时选DEFER。\n" + _json({
            "业务范围": view["context"],
            "原件": view["witnesses"],
        }))
        questions = {
            "choice": {"type": "choice", "instructions": view["question"],
                       "criteria": view["choices"]},
            "witness": {"type": "choice", "instructions": "指出能区分所选动作的原始说明。无同范围证据选NONE。",
                       "criteria": {**{ref: f"引用原件 [{ref}]" for ref in view["witnesses"]},
                                    "NONE": "没有可核的原始说明"}},
        }
        return self._native(tag, state, questions)

    def arbitrate_business(self, view, first):
        context = _json({"case": {key: view[key] for key in ("question", "choices", "witnesses", "context")},
                         "first": first})
        material = ("两份首判对同一业务命题有分歧。查看原件及边界，独立判断应选的业务动作与原始见证。"
                    "材料不能区分时选DEFER且witness用NONE。\n" + context)
        schema = {"type": "object", "additionalProperties": False, "required": ["choice", "witness"],
                  "properties": {"choice": {"enum": list(view["choices"])},
                                 "witness": {"enum": [*view["witnesses"], "NONE"]}}}
        return self._review_completion(material, schema, view)

    def reference_policy(self, tag, view, questions, prior=None, *, before_request=None):
        material = ("依据当前原表区域回答编号用途。没有可核支持时保持待核。"
                    "相等或不同只描述原值，不能证明同一经济事件。\n")
        if tag != _RECHECK:
            if before_request is not None:
                before_request()
            return self._native(tag, material + _json(view), questions)
        schema = {"type": "object", "additionalProperties": False, "required": list(questions),
                  "properties": {key: {"enum": list(question["criteria"])}
                                 for key, question in questions.items()}}
        return self._completion(material + _json({"原表": view, "首判": prior or [], "问题": questions}), schema,
                                before_request=before_request)

    def _remaining(self):
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise ProviderError("model deadline reached")
        return remaining

    def _request(self, tag, route, payload):
        body = _json(payload).encode()
        if len(body) > _MAX_BYTES:
            raise ProviderError("model input too large")
        host, port = self.endpoints[tag]
        # A direct numeric-loopback connection has no environment proxy,
        # redirect handler or user-controlled URL path.
        conn = HTTPConnection(host, port, timeout=self._remaining())
        try:
            conn.connect()
            timer = Timer(self._remaining(), _shutdown, args=(conn.sock,))
            timer.daemon = True
            timer.start()
            try:
                conn.request("POST", route, body=body, headers={"Content-Type": "application/json"})
                with conn.getresponse() as response:
                    if response.status != 200 or response.length is not None and response.length > _MAX_BYTES:
                        raise ProviderError("model HTTP response rejected")
                    data = response.read(_MAX_BYTES + 1)
                self._remaining()
            finally:
                timer.cancel()
            if len(data) > _MAX_BYTES:
                raise ProviderError("model response too large")
            value = json.loads(data)
        except (OSError, HTTPException, ValueError, RecursionError):
            raise ProviderError("model transport or response failed") from None
        finally:
            conn.close()
        if not isinstance(value, dict):
            raise ProviderError("model response is not an object")
        return value

    def _native(self, tag, state, questions):
        value = self._request(tag, "/v1/systemone", {"state": state, "questions": questions})
        usage, answers = value.get("usage"), value.get("answers")
        if (not isinstance(usage, dict) or type(usage.get("output_tokens")) is not int or usage["output_tokens"] != 0
                or not isinstance(answers, dict) or answers.keys() != questions.keys()):
            raise ProviderError("invalid native decision response")
        result = {}
        for key, question in questions.items():
            answer = answers[key]
            code = answer.get("choice") if isinstance(answer, dict) else None
            if not isinstance(code, str) or code not in question["criteria"] or answer.get("error"):
                raise ProviderError("invalid native choice")
            result[key] = code
        return result

    def first(self, tag, view):
        def choice(instructions, criteria):
            return {"type": "choice", "instructions": instructions, "criteria": criteria}

        question = view["question"]
        state = ("只依据当前摘要。定位待人工复核的位置，证据缺失时待核。"
                 "同组编号只表示同项文字或原值相等；不能证明同一事件。"
                 "notes_required 标出必须引的说明。\n" + _json(view))
        options = {f"O{i}": ref for i, ref in enumerate([*view["candidates"], _DEFER], 1)}
        stage = choice(question, {code: ("当前材料不能给出有据的候选，待核。" if ref == _DEFER
                                         else f"建议人工核对 [{ref}]。") for code, ref in options.items()})
        selected = options[self._native(tag, state, {"decision": stage})["decision"]]
        if selected == _DEFER:
            return {"answer": _DEFER, "evidence": [], "finding": None}
        fact_question, findings = _FINDINGS[view["domain"]]
        codes = {f"F{i}": key for i, key in enumerate(findings, 1)}
        review = self._native(tag, state, {
            "finding": choice(f"只查 [{selected}]。{fact_question}", {code: findings[key] for code, key in codes.items()}),
            "direct": choice(f"[{selected}] 有直接依据可建议核对该位置吗？",
                             {"Y": "有可复核的依据", "N": "证据不足"}),
            "additional": choice(f"为 [{selected}] 选一项有直接关系的独立佐证。",
                                 {**{ref: f"引用 [{ref}]" for ref in view["records"] if ref != selected},
                                  "NONE": "没有独立佐证"}),
        })
        refs = [selected] if review["direct"] == "Y" else []
        if review["additional"] != "NONE":
            refs.append(review["additional"])
        return {"answer": selected, "evidence": refs, "finding": codes[review["finding"]]}

    def columns(self, tag, view):
        records, target = view["records"], view["target"]
        state = _column_material(view, compact=False, adjacent=True)
        choices = {}
        for column in view["candidates"]:
            choices[column] = {"type": "choice",
                               "instructions": f"依据原表头与独立说明，判定[{column}]『{records[column]['表头']}』是否是{target}。",
                               "criteria": {"Y": f"有明确证据指向该列承载这笔交易{target.removeprefix('每笔流水实际收支的').removeprefix('每笔流水的')}",
                                            "N": "原件表明此列另有用途", "U": "现有原件不够判定此列用途"}}
        decided = self._native(tag, state, choices)
        note_options = {}
        for note in view["notes"]:
            row = records[note]
            kind = "独立说明" if row["完整"] else "不完整的说明摘录"
            quote = " ".join(row["说明"])
            excerpt = quote[:80] + ("…" if len(quote) > 80 else "")
            note_options[note] = f"[{note}] 原件第{row['说明原行']}行{kind}：{excerpt}"
        note_options["NONE"] = "没有可核查的原件说明直接支持这项用途判断。"
        citations = {}
        for column, code in decided.items():
            if code == "U":
                continue
            row = records[column]
            use = f"承载{target}" if code == "Y" else f"另作他用，不能当作{target}"
            relevant = row.get("说明位置")
            options = ({note: note_options[note] for note in relevant} | {"NONE": note_options["NONE"]}
                       if relevant is not None else note_options)
            citations[column] = {
                "type": "choice",
                "instructions": (f"核对表头第{row['原行']}行第{row['物理列']}列 [{column}]『{row['表头']}』。"
                                 f"哪条原件说明直接写明该列在当前表区{use}？"),
                "criteria": options,
            }
        cited = self._native(tag, state, citations) if citations else {}
        findings = {"Y": "supported", "N": "refuted", "U": "unknown"}
        return {column: {"finding": findings[code],
                         "evidence": [column, cited[column]] if cited.get(column) not in {None, "NONE"} else []}
                for column, code in decided.items()}

    def reconsider_columns(self, view, prior, conflicts):
        material = ("逐列核实原表头是否承担「" + view["target"] + "」。"
                    "每个物理列作为一个键。原件明确支持时，值用 supported:说明编号；"
                    "原件明确另有用途时用 refuted:说明编号；依据不足用 unknown。"
                    "只引用该列的相关说明。首判给出各自的意见和引文，请依据原件独立判断。\n"
                    + _column_material(view, compact=True, adjacent=True) + "\n"
                    + _json({"首判原答": prior, "争议列": conflicts}))
        choices = {}
        for column in view["candidates"]:
            notes = view["records"][column].get("说明位置", view["notes"])
            choices[column] = ["unknown", *(f"{finding}:{note}" for finding in ("supported", "refuted") for note in notes)]
        schema = {"type": "object", "properties": {column: {"enum": options} for column, options in choices.items()},
                  "required": list(choices), "additionalProperties": False}
        result = self._review_completion(material, schema, view)
        if isinstance(result, dict) and set(result) == {"read"}:
            return result
        if not isinstance(result, dict) or result.keys() != choices.keys():
            raise ProviderError("invalid column recheck scope")
        verified_shape = {}
        for column, code in result.items():
            if code not in choices[column]:
                raise ProviderError("invalid column recheck citation")
            finding, _, note = code.partition(":")
            verified_shape[column] = {"finding": finding, "evidence": [column, note] if note else []}
        return verified_shape

    def _review_completion(self, material, schema, view):
        if extra := view.get("added_original"):
            material += ("\n已按所选位置读取一项原据，该证据仅适用于标明的范围。"
                         "结合新增原据裁决，材料不足时保留待核。\n" + _json(extra))
        offers = view.get("read_offers")
        if offers:
            material += ("\n若以下一项原据能区分争点，在read填该位置，否则填NONE并作决定。"
                         "选择取证时，其他判断字段填待核。\n" + _json(offers))
            schema = {**schema, "required": ["read", *schema["required"]],
                      "properties": {"read": {"enum": ["NONE", *offers]}, **schema["properties"]}}
        result = self._completion(material.replace("<", "＜").replace(">", "＞"), schema)
        if offers:
            if not isinstance(result, dict) or type(result.get("read")) is not str:
                raise ProviderError("invalid supplementary evidence request")
            choice = result.pop("read")
            if choice != "NONE":
                return {"read": choice}
        return result

    def recheck(self, view, prior, conflicts):
        prompt_question, findings = _FINDINGS[view["domain"]]
        material = ("依据当前原件摘要独立复核首判所引位置。"
                    "证据仍不足时答 DEFER，finding 用 null。否则引用所选原位及 notes_required 的必需说明。"
                    f"核实所选原位：{prompt_question}。finding 允许值：{_json(findings)}。"
                    "只输出 answer、evidence、finding 对象。\n"
                    + _json({"evidence": view, "first": prior, "disputes": conflicts}).replace("<", "＜").replace(">", "＞"))
        schema = {"type": "object", "properties": {
            "answer": {"type": "string", "enum": [*view["candidates"], _DEFER]},
            "evidence": {"type": "array", "items": {"type": "string", "enum": list(view["records"])},
                         "maxItems": _MAX_REFS},
            "finding": {"enum": [*findings, None]},
        }, "required": ["answer", "evidence", "finding"], "additionalProperties": False}
        return self._completion(material, schema)

    def _completion(self, material, schema, *, on_generation=None, before_request=None):
        def request(route, payload):
            if before_request is not None:
                before_request()
            return self._request(_RECHECK, route, payload)

        rendered = request("/apply-template", {
            "messages": [{"role": "user", "content": material}], "chat_template_kwargs": {"enable_thinking": False}})
        expected = "<|im_start|>user\n" + material.strip() + "<|im_end|>\n" + _NO_THINK
        if rendered.get("prompt") != expected:
            raise ProviderError("thinking-disabled prompt did not match")
        tokens = request("/tokenize", {"content": expected, "add_special": False,
                                                     "parse_special": True}).get("tokens")
        if (not isinstance(tokens, list) or not tokens or len(tokens) + _PREDICT > _CONTEXT
                or any(type(token) is not int or token < 0 for token in tokens)):
            raise ProviderError("invalid model prompt tokens")
        result = request("/completion", {
            "prompt": tokens, "n_predict": _PREDICT, "temperature": 0, "seed": 20261122,
            "json_schema": schema, "cache_prompt": False,
        })
        if on_generation is not None:
            generated = result.get("tokens_predicted")
            if result.get("model") != _RECHECK or type(generated) is not int or generated <= 0:
                raise ProviderError("unverified explanation generation")
            on_generation()
        if result.get("truncated") is not False or result.get("stop_type") not in ("eos", "word"):
            raise ProviderError("incomplete recheck response")
        try:
            return json.loads(result["content"])
        except (KeyError, TypeError, ValueError, RecursionError):
            raise ProviderError("malformed recheck response") from None


def _checked(reply, view):
    if not isinstance(reply, dict) or reply.keys() != {"answer", "evidence", "finding"}:
        raise ProviderError("malformed model reply")
    answer, refs, finding = reply["answer"], reply["evidence"], reply["finding"]
    if (not isinstance(answer, str) or answer not in [_DEFER, *view["candidates"]]
            or not isinstance(refs, list) or len(refs) > _MAX_REFS
            or any(not isinstance(ref, str) or ref not in view["records"] for ref in refs)
            or len(set(refs)) != len(refs)):
        raise ProviderError("reply outside model scope")
    if answer == _DEFER:
        valid_finding = finding is None
    else:
        valid_finding = isinstance(finding, str) and finding in _FINDINGS[view["domain"]][1]
    if not valid_finding:
        raise ProviderError("invalid finding")
    return reply


def _run_column_trial(initial, current, endpoints, provider, *, experimental_review_read=False):
    early, prepared, proofs = import_column_trial.prepare(initial, _safe_excerpt)
    first_calls = recheck_calls = 0
    view = prepared if early is None else None
    added_read = None

    def result(state, reason, verdicts=None, selection=None, additional=()):
        verdicts = verdicts or {}
        reply = None
        if selection is not None:
            column, note = selection
            reply = SimpleNamespace(packet_sha256=initial["packet_sha256"], choice=column,
                                    refs=list(dict.fromkeys([column, note, *additional])), gap=None)
        packet = current(reply) if first_calls or reply else initial
        if added_read:
            packet["facts"]["review_read"] = added_read
        return {"state": state, "reason": reason, "packet": packet,
                "first_calls": first_calls, "recheck_calls": recheck_calls,
                "column_checks": [{"column_ref": column, "finding": verdicts.get(column, ("unknown", None))[0],
                                   "note_ref": verdicts.get(column, ("unknown", None))[1]}
                                  for column in view["candidates"]] if view else []}

    if initial["facts"].get("support_coverage_complete") is False:
        return result("pending", "support_scope_incomplete")
    if early == "single_original_lead":
        return result("pending", early, selection=(prepared, next(iter(proofs[prepared]))))
    if early:
        if early == "insufficient_evidence" and not any(row["id"].startswith("N") for row in initial["records"]):
            label = import_ready_evidence.declared_counter_header(initial["facts"])
            if label:
                return {**result("pending", "source_header_role"), "header_role": label}
        return result("rule" if early == "rule_confirmed" else "pending", early)
    if provider is None and endpoints is None:
        return result("unavailable", "not_configured")
    coverage = initial["facts"].get("column_coverage")
    if coverage and not coverage["complete"]:
        return result("pending", "candidate_scope_incomplete")
    provider = provider if provider is not None else _LoopbackProvider(endpoints)
    heads = first_reviewers(endpoints)
    replies, verified, conflicts = [], [], []
    for tag in heads:
        if first_calls:
            current()
        first_calls += 1
        try:
            reply = provider.columns(tag, view)
        except ProviderError:
            continue
        if import_column_trial.checked(reply, view):
            replies.append(reply)
            valid, contradictions = import_column_trial.verified(reply, proofs)
            verified.append(valid)
            conflicts.extend(contradictions)
    if len(replies) != len(heads):
        return result("unavailable", "provider_error")
    agreed = {column: evidence for column, evidence in verified[0].items()
              if column in verified[1] and verified[1][column][0] == evidence[0]}
    abstentions = []
    resolvable = agreed | {item["column"]: (item["verified"], item["note"]) for item in conflicts}
    for supported, other in zip(verified, reversed(replies), strict=True):
        for column, (finding, note) in supported.items():
            if other[column]["finding"] == "unknown":
                abstentions.append({"column": column, "note": note, "claimed": "unknown", "verified": finding})
                resolvable[column] = (finding, note)
    if abstentions and import_column_trial.complete(view, resolvable):
        conflicts.extend(abstentions)
    if conflicts:
        current()
        offers = import_column_trial.review_offers(initial, view) if experimental_review_read else {}
        disputed = sorted({conflict["column"] for conflict in conflicts})
        reviewed = trial_user_decision.review(
            view, lambda material: provider.reconsider_columns(material, replies, disputed), ProviderError,
            offers=offers, read=lambda key: import_column_trial.read_review_row(current(), view, offers[key]))
        recheck_calls, added_read = reviewed.calls, reviewed.read_evidence
        if reviewed.failure:
            return result("unavailable" if reviewed.failure == "provider_error" else "pending", reviewed.failure)
        answer = reviewed.reply
        if not import_column_trial.checked(answer, view):
            return result("unavailable", "provider_error")
        reviewed, _ = import_column_trial.verified(answer, proofs)
        # An uncertainty in the third route cannot erase another column's
        # paired, independently checked original evidence.
        verdicts = agreed | {column: verdict for column, verdict in reviewed.items() if column in disputed}
        if choice := import_column_trial.complete(view, verdicts):
            return result("proposal", "recheck_supported", verdicts, choice)
        return result("pending", "recheck_unresolved", verdicts)
    if choice := import_column_trial.complete(view, agreed):
        return result("proposal", "first_agreement", agreed, choice, additional=[verified[1][choice[0]][1]])
    return result("pending", "no_support", agreed)


def _review_receipt(packet, reason, *, admitted=False):
    facts = packet["facts"]
    if not facts.get("product_review"):
        return None
    domain = packet["domain"]
    receipt = {"receipt_id": packet["packet_sha256"], "status": "pending", "reason": reason,
               "proposition": "same_transaction" if domain == "pair" else "conditional_range",
               "evidence_window_truncated": bool(facts.get("more_candidates", facts.get("more_evidence")))}
    if (domain == "pair" and facts.get("rule_reason", "").startswith(("settlement_bridge_", "source_reference_scope_"))
            and not admitted):
        receipt["reason"] = facts["rule_reason"]
    if domain == "pair" and admitted and facts.get("pair_receipt"):
        receipt.update(status="accepted", **facts["pair_receipt"], refs=[
            row["id"] for row in packet["records"] if row["id"] == facts["anchor_ref"]
            or row["id"].startswith("W") or row["structured"].get("official_candidate")])
    if domain == "trace" and admitted:
        interval = facts["conditional_group_share"]
        receipt.update(status="accepted", currency=facts["currency"], lower=interval["min"], upper=interval["max"],
                       ledger_rows=facts.get("ledger_row_count"), refs=facts.get("range_ref_ids") or [
                           row["id"] for row in packet["records"] if row["locator"].get("file_url")])
    if receipt["status"] == "accepted":
        scope = [facts["case_id"], facts["canvas_id"], facts["revision"], receipt["proposition"]]
        if domain == "pair":
            scope.append(receipt["event_id"])
        else:
            scope.extend(["trace-evidence-v1", facts["range_scope"]])
            if facts.get("ledger_scope_id"):
                scope.append(facts["ledger_scope_id"])
            if proof := facts.get("trace_balance_proof"):
                scope.append([proof.get(key) for key in ("source_id", "note_member", "note_member_sha256",
                                                          "note_row", "ledger_member", "ledger_member_sha256")])
        receipt["receipt_id"] = sha256(_json(scope).encode()).hexdigest()
    return receipt


def _pair_support_positions(packet):
    return {packet["facts"]["anchor_ref"]} | {
        row["id"] for row in packet["records"] if row["id"].startswith("W") or row["structured"].get("official_candidate")}


def decide_business(endpoints, view, verify):
    provider = _LoopbackProvider(endpoints) if endpoints is not None else None
    return trial_user_decision.decide(view, provider, first_reviewers(endpoints), verify, ProviderError)


def _current(prepare, digest, reply=None):
    packet = prepare(reply)
    if packet["packet_sha256"] != digest:
        raise ServiceError(409, "assistance_stale", "原件或核对范围已更新，请重新读取")
    return packet


def explain_trace(prepare, endpoints, result):
    """Generate optional prose after product acceptance, then check the live scope."""
    if endpoints is None:
        return
    digest = result["packet"]["packet_sha256"]
    packet = _current(prepare, digest)
    positions = _source_positions(packet)
    request = trace_explanation.prepare(packet, result["receipt"], positions)
    if request is None:
        return

    def count_generation():
        result["explanation_calls"] = 1

    try:
        answer = _LoopbackProvider(endpoints)._completion(request.material, request.schema, on_generation=count_generation)
    except ProviderError:
        answer = None
    current = _current(prepare, digest)
    if _source_positions(current) != positions:
        raise ServiceError(409, "assistance_stale", "原件或核对范围已更新，请重新读取")
    if text := request.validated_text(answer):
        result.update(explanation=text, explanation_model=_RECHECK)


def _trace_business(initial, current, endpoints, range_tickets, ledger_scope):
    from . import trial_trace_decision

    records = trial_trace_decision.model_records(initial)
    if not records:
        return None
    facts = initial["facts"]
    cid = int(facts["case_id"])
    view = {
        "question": "找出适用于当前账簿、币种及期间，并明确说明余额为逐笔记账后的账面余额的原说明。"
                    "仅说明余额或可用余额、或者另一账簿及期间的说明均不足。",
        "choices": {**{key: f"采用当前范围的说明 {key}" for key in records}, "DEFER": "证据不足，保留待核"},
        "witnesses": {key: f"原说明第 {record['source_row']} 行" for key, record in records.items()},
        "context": records,
    }

    def verify(packet, choice, witness):
        if choice != witness:
            return None
        return trial_trace_decision.verify_and_recompute(cid, packet, choice, (witness,), ledger_scope=ledger_scope)

    decision = None
    for witness in trial_trace_decision.rule_balance_refs(initial):
        if proof := verify(initial, witness, witness):
            decision = trial_user_decision.Decision(witness, witness, proof, "rule_evidence")
            break
    if decision is None:
        decision = decide_business(endpoints, view, lambda choice, witness: verify(initial, choice, witness))
    reason = "ledger_evidence_missing" if decision.reason in {"not_configured", "decision_unproved"} else decision.reason
    result = {"state": "pending", "reason": reason, "packet": initial,
              "first_calls": decision.first_calls, "recheck_calls": decision.recheck_calls}
    if decision.proof is not None:
        fresh = current()
        adopted = verify(fresh, decision.choice, decision.witness)
        if adopted is not None:
            patch, tickets = adopted
            fresh["facts"].update(patch)
            fresh["selection"] = {"status": "review_only", "choice": decision.choice,
                                  "refs": [decision.witness], "gap": None}
            if range_tickets is not None:
                range_tickets.update(tickets)
            result.update(state="rule" if decision.reason == "rule_evidence" else "proposal", packet=fresh)
        else:
            result["reason"] = "decision_scope_not_admitted"
    result["receipt"] = _review_receipt(result["packet"], result["reason"],
                                        admitted=result["state"] in {"rule", "proposal"})
    return result


def pair_business(case_id, body, endpoints):
    from . import trial_pair_decision, trial_review_receipts

    if body.kind != "pair" or not body.anchor_id:
        return None
    cid = int(case_id)
    offer = trial_pair_decision.prepare(cid, body)
    if offer is None:
        return None
    packet = assistance.review_assistance(case_id, body, product=True)
    rows = offer["rows"]
    common = next(iter(rows.values()))
    view = {
        "question": (
            ("只选择锚行与一条对侧原行，引用给出双方局部流水且适用于本域的独立原句。其余配对保留待核。"
             if offer["single_pair"] else "选择同一竞争组中由独立原据支持的一对一对应方案。")
            + "两侧时钟不能直接比较时，以原文局部流水绑定为准。"
              "同额、相同参考号或接近时间不足以证明同笔。原文作用域不符或不能排除交换匹配，选择DEFER。"
        ),
        "choices": {**{key: _json(option) for key, option in offer["choices"].items()},
                    "DEFER": "原件无法核定相应匹配，保留待核"},
        "witnesses": {key: f"{witness['document']} 第{witness['source_row']}行 {witness['text']}"
                      for key, witness in offer["witnesses"].items()},
        "context": {
            "锚点": offer["anchor"],
            "共同范围": common["scope"], "币种": common["currency"], "金额": common["amount"],
            "参考号": common["reference_group"], "时基": common["clock_comparability"],
            "原行（收付角色、来源时钟、同账时间序号）": {
                key: [row["role"], row["clock_basis"], row["book_clock_rank"]] for key, row in rows.items()},
            "方案数": offer["candidate_count"], "另有未列候选": offer["more_candidates"],
        },
    }

    def verify(choice, witness):
        return trial_pair_decision.verify(cid, body, offer["scope_sha256"], choice, (witness,))

    claim = trial_pair_decision.explicit_claim(cid, body, offer["scope_sha256"])
    if claim is not None and (proof := verify(*claim)) is not None:
        decision = trial_user_decision.Decision(*claim, proof, "rule_evidence")
    else:
        decision = decide_business(endpoints, view, verify)
    reason = "pair_evidence_missing" if decision.reason in {"not_configured", "decision_unproved"} else decision.reason
    result = {"state": "pending", "reason": reason, "packet": packet,
              "first_calls": decision.first_calls, "recheck_calls": decision.recheck_calls}
    if decision.proof is not None:
        adopted = trial_pair_decision.adopt(
            cid, body, offer["scope_sha256"], decision.choice, (decision.witness,),
            on_accept=lambda proof: trial_review_receipts.record_adopted_pair(cid, proof))
        if adopted is not None:
            packet["facts"].update(accepted_revision=adopted["analysis_revision"],
                                   event_ids=adopted["event_ids"])
            packet["records"] = adopted["records"]
            return {**result, "state": "rule" if decision.reason == "rule_evidence" else "proposal",
                    "receipt": adopted["receipt"]}
        result["reason"] = "decision_scope_not_admitted"
    return {**result, "receipt": _review_receipt(packet, result["reason"])}


def run(prepare: Callable[[SimpleNamespace | None], dict], endpoints: ModelEndpoints | None, *, provider=None,
        range_tickets=None, ledger_scope=None, experimental_review_read=False):
    """Ask both first-pass models, rechecking only a source-backed factual conflict."""
    initial = prepare(None)
    domain = initial["domain"]
    digest = initial["packet_sha256"]
    first_calls = recheck_calls = 0

    def result(state, reason, packet=initial, *, admitted=False):
        return {"state": state, "reason": reason, "packet": packet,
                "first_calls": first_calls, "recheck_calls": recheck_calls,
                "receipt": _review_receipt(packet, reason, admitted=admitted)}

    def current(reply=None):
        return _current(prepare, digest, reply)

    if domain == "import":
        return _run_column_trial(initial, current, endpoints, provider,
                                 experimental_review_read=experimental_review_read)

    positions = None
    if domain == "trace":
        exclusion_refs = _trace_time_exclusion_refs(initial)
        if (initial["facts"].get("trace_balance_context")
                and (decision := _trace_business(initial, current, endpoints, range_tickets, ledger_scope)) is not None):
            if decision["state"] != "pending" or not exclusion_refs:
                return decision
            first_calls, recheck_calls = decision["first_calls"], decision["recheck_calls"]
        if exclusion_refs:
            reason = "trace_time_exclusion" if exclusion_refs <= _source_positions(initial) else "source_unavailable"
            return result("pending", reason, current())
        positions = set() if "range_originals_verified" in initial["facts"] else _source_positions(initial)
        if trace_result := _trace_result(initial, positions):
            return result(*trace_result, current(), admitted=trace_result[0] == "rule")

    if domain == "pair" and initial["facts"]["rule_status"] == "confirmed":
        if initial["facts"].get("product_review") and not initial["facts"].get("pair_receipt"):
            return result("pending", "candidate_scope_incomplete", current())
        packet = current() if initial["facts"].get("product_review") else initial
        if not _pair_support_positions(packet) <= _source_positions(packet):
            return result("pending", "source_unavailable", current())
        return result("rule", "rule_confirmed", current(), admitted=True)
    if domain == "pair" and initial["facts"].get("product_review"):
        if initial["facts"].get("ledger_correspondence"):
            return result("rule", "ledger_observations_linked", current())
        reason = initial["facts"]["rule_reason"]
        if (reason in {"source_card_text_pending", "source_account_text_pending", "settlement_bridge_missing_scope_statement",
                       "settlement_bridge_missing_posting_payer",
                       "settlement_bridge_missing_posting_payee"}
                or reason.startswith(("source_reference_scope_", "settlement_bridge_currency_conflict_",
                                      "settlement_bridge_amount_conflict_"))):
            return result("pending", reason, current())
    provisional = {row["id"] for row in initial["records"]
                   if row["locator"].get("file_url")}
    candidate_ids = _candidate_ids(initial, provisional)
    if len(candidate_ids) < 2:
        return result("pending", "insufficient_evidence")
    if positions is None:
        positions = _source_positions(initial)
    if positions != provisional:
        candidate_ids = _candidate_ids(initial, positions)
    scope = _scope(initial, positions, candidate_ids) if len(candidate_ids) >= 2 else None
    if not scope:
        return result("pending", "insufficient_evidence")
    if provider is None and endpoints is None:
        return result("unavailable", "not_configured")
    provider = provider if provider is not None else _LoopbackProvider(endpoints)
    view, aliases, observed, checkable = scope

    def backed(reply):
        chosen, evidence = reply["answer"], set(reply["evidence"])
        return chosen in evidence

    def refresh(reply=None):
        packet = current(reply)
        if _source_positions(packet) != positions:
            raise ServiceError(409, "assistance_stale", "原件或核对范围已更新，请重新读取")
        return packet

    def propose(chosen, refs, reason):
        reply = SimpleNamespace(packet_sha256=digest, choice=aliases[chosen],
                                refs=[aliases[ref] for ref in refs], gap=None)
        packet = refresh(reply)
        # A cited fact is only a lead. Sign only a complete product proof
        # re-read in this scope, regardless of how the lead was obtained.
        if domain == "pair":
            admitted = bool(packet["facts"].get("pair_receipt")) and _pair_support_positions(packet) <= positions
        else:
            admitted = _trace_result(packet, positions) == ("rule", "ledger_range_available")
        return result("proposal", reason, packet, admitted=admitted)

    heads = first_reviewers(endpoints)
    replies = []
    for tag in heads:
        if first_calls:
            refresh()
        first_calls += 1
        try:
            replies.append(_checked(provider.first(tag, view), view))
        except ProviderError:
            continue
    if len(replies) != len(heads):
        return result("unavailable", "provider_error", refresh())

    supported = [reply for reply in replies if backed(reply)]
    verified = [reply for reply in supported if reply["answer"] in checkable and reply["finding"] == observed[reply["answer"]]]
    product_pair = domain == "pair" and initial["facts"].get("product_review")
    conflicts = [{"row": reply["answer"], "reported": reply["finding"], "reason": "cited_observation_dispute"}
                 for reply in supported if reply["answer"] in checkable
                 and reply["finding"] not in {"unknown", "incomparable"}
                 and (not product_pair or observed[reply["answer"]] != "incomparable")
                 and reply["finding"] != observed[reply["answer"]]]
    if not conflicts and product_pair and len(verified) == 2:
        if verified[0]["answer"] != verified[1]["answer"]:
            conflicts = [{"row": reply["answer"], "reported": reply["finding"], "reason": "competing_opposite_entries"}
                         for reply in verified]
    if conflicts:
        refresh()
        recheck_calls += 1
        try:
            checked = _checked(provider.recheck(view, replies, conflicts), view)
        except ProviderError:
            return result("unavailable", "provider_error", refresh())
        chosen = checked["answer"]
        if backed(checked) and checked["finding"] == observed[chosen]:
            return propose(chosen, checked["evidence"], "recheck_supported")
        return result("pending", "recheck_unresolved", refresh())
    if len(supported) < len(heads) or any(reply["finding"] != observed[reply["answer"]] for reply in supported):
        return result("pending", "no_support", refresh())
    first, second = supported
    if first["answer"] != second["answer"]:
        if domain == "trace" and initial["facts"].get("product_review") and len(verified) == 2:
            return propose(first["answer"], [first["answer"], second["answer"]], "complementary_original_rows")
        return result("pending", "no_agreement", refresh())
    refs = list(dict.fromkeys([first["answer"], *first["evidence"], *second["evidence"]]))[:_MAX_REFS]
    return propose(first["answer"], refs, "first_agreement")
