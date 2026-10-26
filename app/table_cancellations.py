"""Closed 32-column cancellation evidence for effective-funds analysis.

No source text, accounts, or transaction references are persisted here. This is
an analytical exclusion policy and does not assert bank settlement finality.
"""
from __future__ import annotations

from collections import defaultdict, deque
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import re

from . import table_import
from .feedback_cancellations import cancellation_text

POLICY = "table-paired-cancellation-v1"
MOVEMENTS = frozenset({"positive", "negative", "zero", "unknown", "non_transaction", "header"})
STATUSES = frozenset({"code_success", "text_success", "other"})
ACTIONS = frozenset({"affirmative", "none", "conflict"})
QUALITIES = frozenset({"accepted", "negative_only", "valid_zero", "other"})
DIRECTIONS = frozenset({"in", "out", ""})
ACCOUNT_COLUMNS = frozenset({"交易账号", "交易卡号"})
ROLES = frozenset({"original", "cancellation"})
OBSERVATION_FIELDS = frozenset({"cancellation_policy", "cancellation_scope_sha256", "cancellation_time",
                                "cancellation_reference_sha256", "cancellation_movement"})
HASH_FIELDS = frozenset({"cancellation_own_account_sha256", "cancellation_own_name_sha256",
                        "cancellation_own_identity_sha256", "cancellation_counter_account_sha256",
                        "cancellation_counter_identity_sha256", "cancellation_merchant_sha256"})
CRITICAL_FIELDS = HASH_FIELDS | frozenset({"cancellation_amount", "cancellation_balance", "cancellation_direction",
    "cancellation_currency", "cancellation_account_column", "cancellation_status", "cancellation_action", "cancellation_quality"})
PAIR_FIELDS = frozenset({"cancellation_role", "paired_record_no", "cancellation_witness_record_no"})
DETAILS_FIELDS = OBSERVATION_FIELDS | CRITICAL_FIELDS | PAIR_FIELDS
REASONS = {
    "matched_cancellation": "来源成对冲正或撤销通过闭合验证，两条来源记录的资金效果不计入分析",
    "cancellation_structure": "来源成员结构不满足成对冲正验证条件，负数保留待核",
    "cancellation_missing_reference": "缺少实际交易流水号，不能以凭证号证明成对冲正",
    "cancellation_reference_not_unique": "实际交易流水号在同一本方币种范围内不恰好出现两次",
    "cancellation_action_unproven": "冲正或撤销动作未明确完成，或存在退款及失败状态冲突",
    "cancellation_status_unproven": "负数记录未使用已适配的成功状态代码",
    "cancellation_missing_scope": "缺少明确本方标识或来源币种，不能确定冲正范围",
    "cancellation_original_invalid": "对应正数记录未通过原有完整资金校验",
    "cancellation_time_ambiguous": "成对记录与余额见证的来源时间不唯一相邻",
    "cancellation_unlocated_record": "同一范围存在无法定位时间的资金记录",
    "cancellation_missing_balance": "成对记录或前序见证缺少可精确解释的余额",
    "cancellation_amount_mismatch": "成对有符号金额不能精确抵消",
    "cancellation_direction_mismatch": "成对来源收付方向不一致",
    "cancellation_identity_conflict": "成对记录的非空身份或对手商户标识存在冲突",
    "cancellation_witness_untrusted": "前序余额见证不是已验证资金记录、有效零金额或已证实冲回",
    "cancellation_balance_mismatch": "两步余额变化与成对金额不能精确闭合",
    "cancellation_validation_failed": "负数记录除金额符号外仍有其他资金校验问题",
    "cancellation_historical_conflict": "历史同来源资金事实与当前成对证据不一致，保留待核",
}
REASON_CODES = frozenset(REASONS)
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_CONFLICT = re.compile(r"退款|退货|退税|失败|待定|待确认|处理中|未完成|不成功|无法办理|未知状态|状态未知")
_MONEY = re.compile(r"-?(?:0|[1-9]\d*)\.\d{2}\Z")


def hash_value(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def scope_digest(account_column, account_hash, currency):
    return hashlib.sha256(json.dumps([POLICY, account_column, account_hash, currency],
                                    ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _action(source):
    if any(_CONFLICT.search(source[column]) for column in ("摘要说明", "备注", "交易类型")):
        return "conflict"
    return "affirmative" if cancellation_text(source) else "none"


def _status(value):
    value = table_import._enum(value)
    return "code_success" if value in {"1", "01"} else (
        "text_success" if value in {"成功", "交易成功", "success", "succeeded"} else "other")


def _observation(record, source):
    money = {"factor": Decimal(1)}
    amount, _ = table_import._money(source["交易金额"], money)
    balance, _ = table_import._money(source["交易余额"], money)
    time = table_import._time(source["交易时间"])
    direction = table_import._enum(source["收付标志"])
    direction = "in" if direction in table_import._IN else "out" if direction in table_import._OUT else ""
    currency_raw = source["交易币种"]
    currency = table_import._CURRENCY.get(currency_raw, currency_raw.upper()) if currency_raw else ""
    if currency not in table_import._ISO_CURRENCIES:
        currency = ""
    column = "交易账号" if source["交易账号"] else "交易卡号"
    account = source[column]
    account_hash = hash_value(account)
    scope = scope_digest(column, account_hash, currency) if account and currency else ""
    quality = ("accepted" if record["disposition"] == "accepted" else
               "valid_zero" if record["disposition"] == "non_monetary" and record["reason_code"] == "zero_amount" else
               "negative_only" if record["disposition"] == "unresolved" and record["details"].get("validation_issues") == "negative_amount" else "other")
    if record["reason_code"] == "repeated_header":
        movement = "header"
    elif record["reason_code"] in {"blank_record", "feedback_only"}:
        movement = "non_transaction"
    else:
        movement = "unknown" if amount is None else "positive" if amount > 0 else "negative" if amount < 0 else "zero"
    notes = {"cancellation_policy": POLICY, "cancellation_scope_sha256": scope,
             "cancellation_time": time.isoformat(sep=" ", timespec="microseconds") if time else "",
             "cancellation_reference_sha256": hash_value(source["交易流水号"]), "cancellation_movement": movement}
    critical = {"cancellation_amount": format(amount, ".2f") if amount is not None else "",
                "cancellation_balance": format(balance, ".2f") if balance is not None else "",
                "cancellation_direction": direction, "cancellation_currency": currency,
                "cancellation_account_column": column, "cancellation_status": _status(source["交易是否成功"]),
                "cancellation_action": _action(source), "cancellation_quality": quality,
                "cancellation_own_account_sha256": account_hash,
                "cancellation_own_name_sha256": hash_value(source.get("交易户名", "")),
                "cancellation_own_identity_sha256": hash_value(source.get("交易证件号码", "")),
                "cancellation_counter_account_sha256": hash_value(source["交易对手账卡号"]),
                "cancellation_counter_identity_sha256": hash_value(source["对手身份证号"]),
                "cancellation_merchant_sha256": hash_value(source.get("商户代码", ""))}
    return {"number": record["record_no"], "record": record, "notes": notes, "critical": critical,
            "amount": amount, "balance": balance, "scope": scope, "time": time,
            "reference": notes["cancellation_reference_sha256"], "movement": movement,
            "quality": quality, "direction": direction, "status": critical["cancellation_status"],
            "action": critical["cancellation_action"]}


def _indexes(observations):
    times, references, unlocated = defaultdict(lambda: defaultdict(list)), defaultdict(list), set()
    for observation in observations:
        scope = observation["scope"]
        if not scope:
            continue
        if observation["time"] is not None:
            times[scope][observation["time"]].append(observation)
        elif observation["movement"] in {"positive", "negative", "unknown"}:
            unlocated.add(scope)
        if observation["reference"]:
            references[scope, observation["reference"]].append(observation)
    prior = {}
    for scope, slots in times.items():
        previous = None
        for time in sorted(slots):
            current = slots[time]
            if len(current) == 1 and previous is not None and len(previous) == 1:
                prior[current[0]["number"]] = previous[0]
            previous = current
    return references, prior, unlocated


def _pair_failure(cancellation, references, prior, unlocated, proved):
    if not cancellation["scope"]:
        return "cancellation_missing_scope", None, None
    if cancellation["scope"] in unlocated:
        return "cancellation_unlocated_record", None, None
    if cancellation["quality"] != "negative_only":
        return "cancellation_validation_failed", None, None
    if cancellation["status"] != "code_success":
        return "cancellation_status_unproven", None, None
    if cancellation["action"] != "affirmative":
        return "cancellation_action_unproven", None, None
    if not cancellation["reference"]:
        return "cancellation_missing_reference", None, None
    matches = references[cancellation["scope"], cancellation["reference"]]
    if len(matches) != 2:
        return "cancellation_reference_not_unique", None, None
    original = next(item for item in matches if item is not cancellation)
    if original["quality"] != "accepted" or original["amount"] is None or original["amount"] <= 0:
        return "cancellation_original_invalid", None, None
    if original["action"] == "conflict":
        return "cancellation_action_unproven", None, None
    if original["amount"] != cancellation["amount"].copy_negate():
        return "cancellation_amount_mismatch", None, None
    if not original["direction"] or original["direction"] != cancellation["direction"]:
        return "cancellation_direction_mismatch", None, None
    for field in HASH_FIELDS - {"cancellation_own_account_sha256"}:
        left, right = original["critical"][field], cancellation["critical"][field]
        if left and right and left != right:
            return "cancellation_identity_conflict", None, None
    if prior.get(cancellation["number"]) is not original:
        return "cancellation_time_ambiguous", None, None
    witness = prior.get(original["number"])
    if witness is None:
        return "cancellation_time_ambiguous", None, None
    if any(item["balance"] is None for item in (original, cancellation, witness)):
        return "cancellation_missing_balance", None, None
    if witness["quality"] not in {"accepted", "valid_zero"} and witness["number"] not in proved:
        return "cancellation_witness_untrusted", None, None
    with localcontext() as context:
        context.prec = 50
        effect = original["amount"] if original["direction"] == "in" else original["amount"].copy_negate()
        if (original["balance"] - witness["balance"] != effect
                or cancellation["balance"] - original["balance"] != effect.copy_negate()
                or cancellation["balance"] != witness["balance"]):
            return "cancellation_balance_mismatch", None, None
    return None, original, witness


def refresh_stats(plan):
    counts = {key: 0 for key in ("accepted", "unresolved", "non_monetary", "non_transaction", "auxiliary", "cancelled")}
    reasons, totals, examples = {}, {}, []
    for record in plan["records"]:
        counts[record["disposition"]] += 1
        reasons[record["reason_code"]] = reasons.get(record["reason_code"], 0) + 1
        if record["disposition"] == "unresolved" and len(examples) < 3:
            examples.append({"row": "来源记录", "source_row": record["record_no"], "reason": record["details"]["reason"]})
    with localcontext() as context:
        context.prec = 50
        for row in plan["rows"]:
            totals[row["currency"]] = totals.get(row["currency"], Decimal(0)) + row["amount"]
    plan["stats"] = {"total": len(plan["records"]), "ok": len(plan["rows"]),
                     "skipped": len(plan["records"]) - len(plan["rows"]), "bad_examples": examples,
                     **counts, "reason_counts": reasons,
                     "currency_totals": {currency: format(amount, ".2f") for currency, amount in sorted(totals.items())}}


def apply(plan, payload, *, allow_feedback28=False):
    """Apply only after the existing reference-aware parser validated the CSV."""
    plan.update(cancelled_rows=[], cancellations=[])
    sheets = table_import._csv(payload, ".csv")
    raw = sheets[0]["rows"]
    def exact(cells):
        columns = [table_import._text(value) for value in cells]
        from . import feedback_import
        return (len(columns) == 32 and len(set(columns)) == 32 and frozenset(columns) == table_import.BANK_FEEDBACK_32_COLUMNS
                or allow_feedback28 and len(columns) == 28 and len(set(columns)) == 28 and frozenset(columns) == frozenset(feedback_import.COLUMNS))
    eligible = bool(raw and exact(raw[0]))
    observations = []
    records = {record["source_row"]: record for record in plan["records"]}
    if eligible:
        headers = [table_import._text(value) for value in raw[0]]
        for offset in range(1, len(raw)):
            cells = raw[offset]
            if allow_feedback28:
                # All later checks use the complete observations below. Do
                # not retain another full set of cells (or a raw[1:] slice)
                # beside those proofs in the evidence-envelope path.
                raw[offset] = None
            index = offset + 1
            record = records[index]
            if len(cells) != len(headers) or (record["reason_code"] == "repeated_header" and not exact(cells)):
                eligible = False
                continue
            if record["reason_code"] == "repeated_header":
                headers = [table_import._text(value) for value in cells]
            source = dict(zip(headers, (table_import._text(value) for value in cells)))
            observation = _observation(record, source)
            observations.append(observation)
            if not observation["scope"] and observation["movement"] not in {"header", "non_transaction", "zero"}:
                eligible = False
    if allow_feedback28:
        raw.clear()
    references, prior, unlocated = _indexes(observations)
    proved, selected = set(), set()
    rows = {row["_source_row"]: row for row in plan["rows"]}
    for observation in sorted(observations, key=lambda item: (item["time"] is None, item["time"] or table_import.dt.datetime.max, item["number"])):
        if (observation["movement"] != "negative"
                or observation["record"]["disposition"] != "unresolved"):
            continue
        failure, original, witness = (_pair_failure(observation, references, prior, unlocated, proved)
                                       if eligible else ("cancellation_structure", None, None))
        if failure:
            observation["record"].update(disposition="unresolved", reason_code=failure)
            observation["record"]["details"].update(reason=REASONS[failure], financial_effect="undetermined")
            prior_issues = [issue for issue in observation["record"]["details"].get("validation_issues", "").split(",")
                            if issue and issue != failure]
            observation["record"]["details"]["validation_issues"] = ",".join((failure, *prior_issues))
            continue
        pair = {"original_record_no": original["number"], "cancellation_record_no": observation["number"], "witness_record_no": witness["number"]}
        plan["cancellations"].append(pair)
        plan["cancelled_rows"].append(rows[original["number"]])
        proved.add(observation["number"])
        selected.update((original["number"], observation["number"], witness["number"]))
        for item, role, other in ((original, "original", observation), (observation, "cancellation", original)):
            item["record"].update(disposition="cancelled", reason_code="matched_cancellation")
            item["record"]["details"].update(financial_effect="analysis_excluded", reason=REASONS["matched_cancellation"],
                cancellation_role=role, paired_record_no=str(other["number"]), cancellation_witness_record_no=str(witness["number"]))
    if plan["cancellations"]:
        for item in observations:
            item["record"]["details"].update(item["notes"])
            if item["number"] in selected:
                item["record"]["details"].update(item["critical"])
        excluded = {pair["original_record_no"] for pair in plan["cancellations"]}
        plan["rows"] = [row for row in plan["rows"] if row["_source_row"] not in excluded]
    refresh_stats(plan)
    return plan


def _number(value):
    if isinstance(value, bool) or not re.fullmatch(r"[1-9]\d*", str(value)):
        raise ValueError("invalid cancellation record number")
    return int(value)


def _decimal(value, *, optional=False):
    if optional and value == "":
        return None
    if not isinstance(value, str) or not _MONEY.fullmatch(value):
        raise ValueError("invalid cancellation amount")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError("invalid cancellation amount") from None
    if amount.copy_abs() > table_import._MAX_MONEY or format(amount, ".2f") != value:
        raise ValueError("invalid cancellation amount")
    return amount


def validate_evidence(records):
    """Rebuild full-member indexes and verify closed, rooted proofs from notes."""
    by_number, by_member = {}, defaultdict(list)
    for record in records:
        number = _number(record["record_no"])
        if number in by_number:
            raise ValueError("duplicate cancellation record number")
        by_number[number] = record
        by_member[record["member_path"]].append(record)
    result = []
    for member_records in by_member.values():
        marked = [record for record in member_records if record["details"].get("cancellation_policy") == POLICY]
        if not marked:
            if any(record["details"].keys() & DETAILS_FIELDS or record["reason_code"] == "matched_cancellation" for record in member_records):
                raise ValueError("missing cancellation observations")
            continue
        if len(marked) != len(member_records):
            raise ValueError("incomplete cancellation observations")
        observations, has_roles = [], False
        for record in marked:
            notes = record["details"]
            if not OBSERVATION_FIELDS <= notes.keys() or notes["cancellation_movement"] not in MOVEMENTS:
                raise ValueError("invalid cancellation observations")
            movement = notes["cancellation_movement"]
            expected_movement = ("header" if record["reason_code"] == "repeated_header" else
                                 "non_transaction" if record["reason_code"] in {"blank_record", "feedback_only"} else
                                 "positive" if record["disposition"] in {"accepted", "imported", "duplicate", "removed"} else
                                 "zero" if record["disposition"] == "non_monetary" else None)
            if ((expected_movement is not None and movement != expected_movement)
                    or (movement in {"header", "non_transaction"} and expected_movement != movement)):
                raise ValueError("conflicting cancellation observation")
            scope, reference = notes["cancellation_scope_sha256"], notes["cancellation_reference_sha256"]
            if any(value and not _HEX.fullmatch(value) for value in (scope, reference)):
                raise ValueError("invalid cancellation indexes")
            time_text = notes["cancellation_time"]
            time = table_import._time(time_text) if time_text else None
            if time_text and (time is None or time.isoformat(sep=" ", timespec="microseconds") != time_text):
                raise ValueError("invalid cancellation time")
            critical = {field: notes[field] for field in CRITICAL_FIELDS if field in notes}
            role = notes.get("cancellation_role")
            if role is not None:
                has_roles = True
                if role not in ROLES or not PAIR_FIELDS <= notes.keys() or record["disposition"] != "cancelled" or record["reason_code"] != "matched_cancellation" or notes.get("financial_effect") != "analysis_excluded":
                    raise ValueError("invalid cancellation roles")
            elif notes.keys() & PAIR_FIELDS or record["reason_code"] == "matched_cancellation":
                raise ValueError("incomplete cancellation role")
            item = {"number": _number(record["record_no"]), "record": record, "notes": notes,
                    "scope": scope, "time": time, "reference": reference, "movement": notes["cancellation_movement"],
                    "quality": "other", "amount": None, "balance": None, "direction": "", "action": "none", "status": "other", "critical": critical}
            if critical:
                if critical.keys() != CRITICAL_FIELDS:
                    raise ValueError("incomplete cancellation facts")
                if (critical["cancellation_direction"] not in DIRECTIONS or critical["cancellation_quality"] not in QUALITIES
                        or critical["cancellation_action"] not in ACTIONS or critical["cancellation_status"] not in STATUSES
                        or critical["cancellation_account_column"] not in ACCOUNT_COLUMNS
                        or critical["cancellation_currency"] not in table_import._ISO_CURRENCIES
                        or not critical["cancellation_own_account_sha256"]
                        or any(value and not _HEX.fullmatch(value) for field in HASH_FIELDS for value in (critical[field],))
                        or scope != scope_digest(critical["cancellation_account_column"], critical["cancellation_own_account_sha256"], critical["cancellation_currency"])):
                    raise ValueError("invalid cancellation facts")
                item.update(amount=_decimal(critical["cancellation_amount"]), balance=_decimal(critical["cancellation_balance"], optional=True),
                            quality=critical["cancellation_quality"], direction=critical["cancellation_direction"],
                            action=critical["cancellation_action"], status=critical["cancellation_status"])
                expected = "positive" if item["amount"] > 0 else "negative" if item["amount"] < 0 else "zero"
                if item["movement"] != expected:
                    raise ValueError("conflicting cancellation movement")
                if item["quality"] == "accepted":
                    if (item["amount"] <= 0 or item["direction"] not in {"in", "out"}
                            or item["status"] not in {"code_success", "text_success"}
                            or record["disposition"] not in {"accepted", "imported", "duplicate", "removed", "cancelled"}
                            or role == "cancellation" or not _HEX.fullmatch(notes.get("flow_sha256", ""))):
                        raise ValueError("invalid accepted cancellation fact")
                elif item["quality"] == "valid_zero":
                    if (item["amount"] != 0 or item["status"] not in {"code_success", "text_success"}
                            or record["disposition"] != "non_monetary" or record["reason_code"] != "zero_amount"):
                        raise ValueError("invalid zero cancellation witness")
                elif item["quality"] == "negative_only":
                    if item["amount"] >= 0 or notes.get("validation_issues") != "negative_amount":
                        raise ValueError("invalid negative cancellation fact")
            elif role:
                raise ValueError("missing cancellation facts")
            observations.append(item)
        if not has_roles:
            raise ValueError("orphan cancellation observations")
        if any(not item["scope"] and item["movement"] not in {"header", "non_transaction", "zero"} for item in observations):
            raise ValueError("unlocated member cancellation record")
        references, prior, unlocated = _indexes(observations)
        proved = set()
        originals, cancellations = set(), set()
        for item in sorted(observations, key=lambda value: (value["time"] is None, value["time"] or table_import.dt.datetime.max, value["number"])):
            if item["notes"].get("cancellation_role") != "cancellation":
                continue
            failure, original, witness = _pair_failure(item, references, prior, unlocated, proved)
            if failure:
                raise ValueError("cancellation proof does not close")
            notes = item["notes"]
            if (original["notes"].get("cancellation_role") != "original"
                    or _number(notes["paired_record_no"]) != original["number"]
                    or _number(original["notes"]["paired_record_no"]) != item["number"]
                    or _number(notes["cancellation_witness_record_no"]) != witness["number"]
                    or _number(original["notes"]["cancellation_witness_record_no"]) != witness["number"]):
                raise ValueError("cancellation roles do not agree")
            originals.add(original["number"])
            cancellations.add(item["number"])
            proved.add(item["number"])
            result.append({"original_record_no": original["number"], "cancellation_record_no": item["number"], "witness_record_no": witness["number"]})
        if originals != {item["number"] for item in observations if item["notes"].get("cancellation_role") == "original"}:
            raise ValueError("orphan cancellation original")
        needed = originals | cancellations | {pair["witness_record_no"] for pair in result}
        if any(bool(item["critical"]) != (item["number"] in needed) for item in observations):
            raise ValueError("unexpected cancellation facts")
    return result


def revoke_pairs(plan, original_record_nos, reason="cancellation_historical_conflict"):
    """Fail closed for incompatible historical facts, including dependent chains."""
    if reason not in REASONS or reason == "matched_cancellation":
        raise ValueError("invalid cancellation revocation reason")
    requested = set(original_record_nos)
    rejected = {pair["cancellation_record_no"] for pair in plan.get("cancellations", []) if pair["original_record_no"] in requested}
    dependents = defaultdict(list)
    for pair in plan.get("cancellations", []):
        dependents[pair["witness_record_no"]].append(pair["cancellation_record_no"])
    pending = deque(rejected)
    while pending:
        for number in dependents[pending.popleft()]:
            if number not in rejected:
                rejected.add(number)
                pending.append(number)
    revoked = [pair for pair in plan.get("cancellations", []) if pair["cancellation_record_no"] in rejected]
    numbers = {pair[key] for pair in revoked for key in ("original_record_no", "cancellation_record_no")}
    plan["cancellations"] = [pair for pair in plan.get("cancellations", []) if pair["cancellation_record_no"] not in rejected]
    plan["cancelled_rows"] = [row for row in plan.get("cancelled_rows", []) if row["_source_row"] not in numbers]
    needed = {pair[key] for pair in plan["cancellations"] for key in ("original_record_no", "cancellation_record_no", "witness_record_no")}
    active_members = {record["member_path"] for record in plan["records"] if record["record_no"] in needed}
    for record in plan["records"]:
        notes = record["details"]
        if record["record_no"] in numbers:
            record.update(disposition="unresolved", reason_code=reason)
            notes.update(financial_effect="undetermined", reason=REASONS[reason])
            prior_issues = [issue for issue in notes.get("validation_issues", "").split(",")
                            if issue and issue != reason]
            notes["validation_issues"] = ",".join((reason, *prior_issues))
            notes.pop("flow_sha256", None)
            for key in PAIR_FIELDS:
                notes.pop(key, None)
        if record["record_no"] not in needed:
            for key in CRITICAL_FIELDS:
                notes.pop(key, None)
        if record["member_path"] not in active_members:
            for key in OBSERVATION_FIELDS:
                notes.pop(key, None)
    refresh_stats(plan)
    return plan
