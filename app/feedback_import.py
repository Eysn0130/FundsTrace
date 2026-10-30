"""Deterministic, in-memory reader for the versioned bank-feedback CSV ZIP.

Only the closed schemas below are admitted.  The original upload remains the
source: this module returns normalized facts and small processing references,
not a second copy of every input cell.  No database or filesystem is accessed.
"""
from __future__ import annotations

import csv
import datetime
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import io
import json
import re
import stat
import threading
import zipfile
import zlib

from app.feedback_cancellations import CancellationIndex, Observation, PAIR_FIELDS, POLICY, cancellation_text


# Rule changes must not give unchanged physical occurrences new identities.
# v1 remains executable for historical compatibility and upgrade tests.
IDENTITY_PROFILE = "funds-trace-bank-feedback-v1"
PREVIOUS_PROFILE = "funds-trace-bank-feedback-v2"
CANCELLATION_PROFILE = "funds-trace-bank-feedback-v3"
TEXT_PROFILE = "funds-trace-bank-feedback-v4"
CURRENCY_PROFILE = "funds-trace-bank-feedback-v5"
EVIDENCE_PROFILE = "funds-trace-bank-feedback-v6"
MICROSECOND_PROFILE = "funds-trace-bank-feedback-v7"
MICROSECOND_EVIDENCE_PROFILE = "funds-trace-bank-feedback-v8"
MICROSECOND_PROFILES = frozenset({MICROSECOND_PROFILE, MICROSECOND_EVIDENCE_PROFILE})
EVIDENCE_PROFILES = frozenset({EVIDENCE_PROFILE, MICROSECOND_EVIDENCE_PROFILE})
PROFILE = MICROSECOND_PROFILE
CURRENCY_PROFILES = frozenset({CURRENCY_PROFILE, EVIDENCE_PROFILE, *MICROSECOND_PROFILES})
TEXT_PROFILES = frozenset({TEXT_PROFILE, *CURRENCY_PROFILES})
CANCELLATION_PROFILES = frozenset({CANCELLATION_PROFILE, *TEXT_PROFILES})
SUPPORTED_PROFILES = frozenset({IDENTITY_PROFILE, PREVIOUS_PROFILE, *CANCELLATION_PROFILES})
MAX_MEMBERS = 256
EVIDENCE_MAX_MEMBERS = 2048
MAX_EXPANDED_BYTES = 300 * 1024 * 1024
EVIDENCE_MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 128 * 1024 * 1024
EVIDENCE_MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_RECORDS = 300_000
TEXT_MAX_RECORDS = 1_000_000
EVIDENCE_MAX_RECORDS = 2_000_000
MAX_FIELD_BYTES = 1024 * 1024

COLUMNS = (
    "交易卡号", "交易账号", "交易时间", "交易金额", "交易余额", "收付标志",
    "交易对手账卡号", "现金标志", "对手户名", "对手身份证号", "对手开户银行",
    "摘要说明", "交易币种", "交易网点名称", "交易发生地", "交易是否成功",
    "传票号", "IP地址", "MAC地址", "对手交易余额", "交易流水号", "日志号",
    "凭证种类", "凭证号", "交易柜员号", "备注", "交易类型", "查询反馈结果原因",
)
SCHEMAS = {
    "transaction": COLUMNS,
    "account": (
        "账户开户名称", "开户人证件号码", "交易卡号", "交易账号", "账号开户时间",
        "账户余额", "可用余额", "币种", "开户网点代码", "开户网点", "账户状态",
        "钞汇标志名称", "销户日期", "账户类型", "备注", "账号开户银行", "销户网点",
        "最后交易时间",
    ),
    "client": (
        "客户名称", "证照类型", "证照号码", "单位地址", "单位电话", "工作单位",
        "邮箱地址", "代办人姓名", "代办人证件类型", "代办人证件号码", "国税纳税号",
        "地税纳税号", "法人代表", "客户工商执照号码",
    ),
    "subaccount": (
        "银行名称", "开户账号", "子账户账号", "余额", "可用余额", "子账户类别",
        "子账户序号", "币种", "钞汇标识", "账户状态", "账户序号",
    ),
    "address": ("开户名称", "证照类型", "证照号码", "住宅地址", "住宅电话"),
    "phone": ("开户名称", "证照类型", "证照号码", "联系电话"),
    "task": (
        "任务流水号", "银行名称", "主体类别", "证账号码", "账卡号", "发送时间",
        "反馈时间", "反馈结果", "反馈非明细结果", "反馈明细结果", "入库时间",
        "入库状态", "请求单号", "查询结果",
    ),
    "freeze": (
        "银行名称", "账号", "冻结措施类型", "冻结金额", "冻结机关", "冻结开始日期",
        "冻结截止日期", "措施序号", "备注",
    ),
}
ROLES = frozenset(SCHEMAS)
MAPPING = {
    "trade_no": "交易流水号", "trade_time": "交易时间", "amount": "交易金额",
    "balance": "交易余额", "direction": "收付标志", "self_account": "交易账号",
    "counter_account": "交易对手账卡号", "counter_name": "对手户名",
    "counter_bank": "对手开户银行", "currency": "交易币种",
    "summary": "摘要说明", "channel": "交易类型",
}
SUPPORTED_CURRENCIES = frozenset({"CNY", "USD", "EUR", "JPY", "HKD", "GBP"})
CURRENCY_DETAILS_FIELDS = frozenset({"source_currency", "canonical_currency", "currency_basis"})
REASON_CODES = frozenset({
    "transaction_valid", "auxiliary_table", "feedback_only", "negative_reversal_text",
    "negative_revocation_text", "negative_reversal_revocation_text", "negative_unknown",
    "missing_core", "invalid_amount", "amount_precision", "amount_overflow",
    "zero_amount", "invalid_direction", "invalid_time", "invalid_currency",
    "unknown_status", "invalid_balance", "balance_precision", "balance_overflow",
    "reversal_text_requires_review",
    "own_identity_conflict",
    "matched_cancellation",
})
DETAILS_FIELDS = frozenset({
    "role", "source_direction", "source_direction_sha256", "source_direction_length",
    "reported_status", "reported_status_sha256", "reported_status_length",
    "signed_amount", "signed_amount_sha256", "signed_amount_length", "event_type",
    "source_account_column", "source_columns", "account_card_relation", "review_reasons",
    "own_identity_record", "own_identity_sha256",
    "financial_effect", "validation_issues", "canonical_amount",
    *CURRENCY_DETAILS_FIELDS,
    *PAIR_FIELDS,
})

_SCHEMA_SIGNATURES = {frozenset(headers): role for role, headers in SCHEMAS.items()}
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\Z")
_FRACTIONAL_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?\Z")
_AMOUNT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z")
_CENT = Decimal("0.01")
_MAX_MONEY = Decimal("9999999999999999.99")
_CSV_LOCK = threading.Lock()


class FeedbackImportError(ValueError):
    """A safe failure code; neither the message nor code contains source cells."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"资金反馈包校验失败（{code}）")


def max_records(profile):
    """A v4/v5 resource allowance; historical limits stay fixed."""
    return EVIDENCE_MAX_RECORDS if profile in EVIDENCE_PROFILES else TEXT_MAX_RECORDS if profile in TEXT_PROFILES else MAX_RECORDS


def currency_details(raw):
    """The v5 closed currency contract, never a general ISO or symbol guess."""
    canonical = "CNY" if raw == "RMB" else raw if raw in SUPPORTED_CURRENCIES | {"AUD"} else None
    if canonical is None:
        return None
    return {"source_currency": raw, "canonical_currency": canonical,
            "currency_basis": "literal_rmb_alias" if raw == "RMB" else "literal_iso_code"}


def identity_digest(name: str, bank: str) -> str:
    """Bind accepted facts only to the own identity actually resolved for them."""
    framed = json.dumps([name, bank], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


def record_key(member_path: str, member_sha256: str, source_row: int, identity_digest: str = "") -> str:
    framed = json.dumps([IDENTITY_PROFILE, member_path, member_sha256, source_row, identity_digest],
                        ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


def _validate_members(archive, profile=PROFILE):
    member_limit = EVIDENCE_MAX_MEMBERS if profile in EVIDENCE_PROFILES else MAX_MEMBERS
    member_bytes = EVIDENCE_MAX_MEMBER_BYTES if profile in EVIDENCE_PROFILES else MAX_MEMBER_BYTES
    expanded_limit = EVIDENCE_MAX_EXPANDED_BYTES if profile in EVIDENCE_PROFILES else MAX_EXPANDED_BYTES
    infos = archive.infolist()
    if not infos or len(infos) > member_limit:
        raise FeedbackImportError("member_count")
    seen, expanded, data_infos = set(), 0, []
    for info in infos:
        name = info.filename
        is_directory = info.is_dir()
        parts = (name[:-1] if is_directory else name).split("/")
        if (name != info.orig_filename or not name or "\\" in name or ":" in name
                or any(part in ("", ".", "..") for part in parts)
                or any(ord(char) < 32 or ord(char) == 127 for char in name)
                or name.startswith("/")):
            raise FeedbackImportError("unsafe_member_path")
        if name in seen:
            raise FeedbackImportError("duplicate_member")
        seen.add(name)
        if stat.S_ISLNK(info.external_attr >> 16):
            raise FeedbackImportError("symlink_member")
        if info.flag_bits & 1:
            raise FeedbackImportError("encrypted_member")
        file_type = stat.S_IFMT(info.external_attr >> 16)
        if file_type not in (0, stat.S_IFDIR if is_directory else stat.S_IFREG):
            raise FeedbackImportError("special_member")
        if is_directory:
            if info.file_size != 0:
                raise FeedbackImportError("directory_payload")
            # An explicit folder is ZIP metadata, never an extracted path or
            # a data member. Reading it also applies ZIP/header integrity checks.
            _read_member(archive, info, 0)
            continue
        if not name.lower().endswith(".csv"):
            raise FeedbackImportError("non_csv_member")
        if info.file_size > member_bytes or info.file_size < 0:
            raise FeedbackImportError("member_size")
        expanded += info.file_size
        if expanded > expanded_limit:
            raise FeedbackImportError("expanded_size")
        data_infos.append(info)
    if not data_infos:
        raise FeedbackImportError("non_csv_member")
    return data_infos


def _read_member(archive, info, expanded, profile=PROFILE):
    member_bytes = EVIDENCE_MAX_MEMBER_BYTES if profile in EVIDENCE_PROFILES else MAX_MEMBER_BYTES
    expanded_limit = EVIDENCE_MAX_EXPANDED_BYTES if profile in EVIDENCE_PROFILES else MAX_EXPANDED_BYTES
    chunks, size = [], 0
    with archive.open(info, "r") as member:
        while True:
            # Reading through EOF checks ZIP CRC; the extra byte detects a
            # dishonest size or a resource-bound overrun before further work.
            chunk = member.read(min(1024 * 1024, member_bytes - size + 1))
            if not chunk:
                break
            size += len(chunk)
            expanded += len(chunk)
            if size > member_bytes:
                raise FeedbackImportError("member_size")
            if expanded > expanded_limit:
                raise FeedbackImportError("expanded_size")
            chunks.append(chunk)
    if size != info.file_size:
        raise FeedbackImportError("member_size_mismatch")
    return b"".join(chunks), expanded


def _decode(payload, *, profile=PROFILE):
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = payload.decode(encoding, errors="strict")
        except UnicodeDecodeError:
            continue
        # v4/v5 defer this one transport character to the parsed cell boundary.
        # Every historical profile still rejects it before CSV interpretation.
        allowed = "\t\r\n\x1c" if profile in TEXT_PROFILES else "\t\r\n"
        if any(ord(char) < 32 and char not in allowed for char in text):
            raise FeedbackImportError("binary_member")
        return text, encoding
    raise FeedbackImportError("invalid_encoding")


def _validate_csv_quotes(text):
    # csv.reader(strict=True) still permits a quote *inside* an unquoted
    # field.  Reject that RFC violation as well, before any record is admitted.
    state, field_chars = "start", 0
    for char in text:
        if state == "quoted":
            if char == '"':
                state = "after_quote"
            else:
                field_chars += 1
        elif state == "after_quote":
            if char == '"':
                state = "quoted"
                field_chars += 1
            elif char in ",\r\n":
                state, field_chars = "start", 0
            else:
                raise FeedbackImportError("invalid_csv")
        elif char in ",\r\n":
            state, field_chars = "start", 0
        elif char == '"':
            if state != "start":
                raise FeedbackImportError("invalid_csv")
            state = "quoted"
        else:
            state = "unquoted"
            field_chars += 1
        if field_chars > MAX_FIELD_BYTES:
            raise FeedbackImportError("field_size")
    if state == "quoted":
        raise FeedbackImportError("invalid_csv")


def _csv_next(reader):
    # csv.field_size_limit is process global.  Keep its temporary change
    # scoped to one read, never across a yielded record or a caller exception.
    with _CSV_LOCK:
        old_limit = csv.field_size_limit()
        csv.field_size_limit(max(old_limit, MAX_FIELD_BYTES))
        try:
            return next(reader, None)
        except csv.Error:
            raise FeedbackImportError("invalid_csv") from None
        finally:
            csv.field_size_limit(old_limit)


def _table(text, encoding, *, profile=PROFILE):
    _validate_csv_quotes(text)
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    header = _csv_next(reader)
    if header is None:
        raise FeedbackImportError("missing_header")
    codec = "utf-8" if encoding == "utf-8-sig" else encoding
    if any(len(cell.encode(codec)) > MAX_FIELD_BYTES for cell in header):
        raise FeedbackImportError("field_size")
    if any("\x1c" in cell for cell in header):
        raise FeedbackImportError("binary_member")
    header = [cell.strip() for cell in header]
    if not header or len(header) != len(set(header)):
        raise FeedbackImportError("duplicate_header")
    role = _SCHEMA_SIGNATURES.get(frozenset(header))
    if role is None or len(header) != len(SCHEMAS[role]):
        raise FeedbackImportError("unknown_schema")
    # Empty tables retain a role and member reference too. The caller never
    # stores raw rows, and no global parser lock spans the yielded result.
    yield role, header
    while (cells := _csv_next(reader)) is not None:
        if len(cells) != len(header):
            raise FeedbackImportError("csv_width")
        if any(len(cell.encode(codec)) > MAX_FIELD_BYTES for cell in cells):
            raise FeedbackImportError("field_size")
        if any("\x1c" in value and not (profile in TEXT_PROFILES and role == "transaction" and key == "交易发生地")
               for key, value in zip(header, cells)):
            raise FeedbackImportError("binary_member")
        # str.strip() treats U+001C as whitespace. Keep this free text cell
        # verbatim; it is retained in the original upload, never mapped to money.
        yield {key: value if profile in TEXT_PROFILES and role == "transaction" and key == "交易发生地"
                else value.strip() for key, value in zip(header, cells)}


def _money(value, field):
    if not value or not _AMOUNT.fullmatch(value):
        return None, f"invalid_{field}"
    try:
        number = Decimal(value)
        if not number.is_finite():
            return None, f"invalid_{field}"
        if number.copy_abs() > _MAX_MONEY:
            return number, f"{field}_overflow"
        with localcontext() as context:
            context.prec = 40
            cents = number.quantize(_CENT)
        if cents != number:
            return number, f"{field}_precision"
        return cents, None
    except InvalidOperation:
        return None, f"invalid_{field}"


def _time(value, *, fractional=False):
    pattern = _FRACTIONAL_TIME if fractional else _TIME
    if not pattern.fullmatch(value) or not 1900 <= int(value[:4]) <= 2100:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f" if fractional and "." in value else "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _add_money(left, right):
    # The maximum admitted record count and DECIMAL(18,2) values need fewer
    # than 25 significant digits in totals. Do not inherit caller rounding.
    with localcontext() as context:
        context.prec = 40
        return left + right


def _remember(details, key, value):
    if len(value) <= 80:
        details[key] = value
    else:
        details[key + "_sha256"] = hashlib.sha256(value.encode("utf-8")).hexdigest()
        details[key + "_length"] = str(len(value))


def _transaction(source, profile=PROFILE):
    account, card = source["交易账号"], source["交易卡号"]
    own_account = account or card
    account_column = "交易账号" if account else "交易卡号" if card else ""
    relation = ("both_present_equal" if account == card else "both_present_distinct") if account and card else (
        "account_only" if account else "card_only" if card else "missing")
    text = "\n".join(source[column] for column in ("摘要说明", "备注", "交易类型"))
    reversal, revocation = "冲正" in text, "撤销" in text
    event = ("reversal_revocation_text" if reversal and revocation else "reversal_text" if reversal
             else "revocation_text" if revocation else "reported")
    details = {"event_type": event, "source_account_column": account_column,
               "source_columns": ",".join(column for column in ("交易账号", "交易卡号") if source[column]),
               "account_card_relation": relation}
    _remember(details, "source_direction", source["收付标志"])
    _remember(details, "reported_status", source["交易是否成功"])
    _remember(details, "signed_amount", source["交易金额"])
    feedback_only = not any((source["交易时间"], source["交易金额"], source["收付标志"]))
    if feedback_only and (profile != IDENTITY_PROFILE or not own_account):
        if profile != IDENTITY_PROFILE:
            details["financial_effect"] = "not_a_flow"
        return None, "non_transaction", "feedback_only", details, None

    currency = source["交易币种"]
    normalized_currency = currency_details(currency) if profile in CURRENCY_PROFILES else None
    if normalized_currency is not None:
        details.update(normalized_currency)
        currency = normalized_currency["canonical_currency"]
    amount, amount_reason = _money(source["交易金额"], "amount")
    balance, balance_reason = (_money(source["交易余额"], "balance")
                               if source["交易余额"] else (None, None))
    trade_time = _time(source["交易时间"], fractional=profile in MICROSECOND_PROFILES)
    direction = {"进": "in", "入": "in", "出": "out"}.get(source["收付标志"])
    reasons = []
    if amount is not None and amount < 0:
        reasons.append("negative_reversal_revocation_text" if reversal and revocation else
                       "negative_reversal_text" if reversal else "negative_revocation_text" if revocation
                       else "negative_unknown")
        if not reversal and not revocation:
            details["event_type"] = "unknown_negative"
    if not all((own_account, source["交易时间"], source["交易金额"], source["收付标志"])):
        reasons.append("missing_core")
    if amount_reason:
        reasons.append(amount_reason)
    elif amount == 0:
        reasons.append("zero_amount")
    if direction is None:
        reasons.append("invalid_direction")
    if trade_time is None:
        reasons.append("invalid_time")
    if (normalized_currency is None if profile in CURRENCY_PROFILES else currency not in SUPPORTED_CURRENCIES):
        reasons.append("invalid_currency")
    # These codes are reported source codes, not proof of settlement.  No
    # interpretation is assigned to an unknown or absent reported code.
    if source["交易是否成功"] not in {"01", "1"}:
        reasons.append("unknown_status")
    if balance_reason:
        reasons.append(balance_reason)
    if (profile == IDENTITY_PROFILE and (reversal or revocation)
            and not (amount is not None and amount < 0)):
        reasons.append("reversal_text_requires_review")
    if profile != IDENTITY_PROFILE:
        if amount_reason is None:
            details["canonical_amount"] = format(amount, ".2f")
        # Zero is an exact reported monetary value, independent of missing
        # direction or date. Preserve those quality issues; invent no flow or
        # successful settlement, and never round a nonzero amount into zero.
        if reasons:
            details["validation_issues"] = ",".join(reasons)
        if amount_reason is None and amount == 0:
            details["financial_effect"] = "no_movement"
            return None, "non_monetary", "zero_amount", details, None
        if reasons:
            details["financial_effect"] = "undetermined"
            return None, "unresolved", reasons[0], details, None
        # Per-record admission preserves source direction and positive money.
        # The separate paired policy may exclude a fully matched cancellation;
        # a textual label alone never changes this reported movement.
        details["financial_effect"] = "source_reported"
    if reasons:
        details["review_reasons"] = ",".join(reasons)
        return None, "review", reasons[0], details, None

    summary, remark = source["摘要说明"], source["备注"]
    if remark and remark != summary:
        summary = (summary + "\n" if summary else "") + "备注：" + remark
    own = {"name": "", "account": own_account, "bank": ""}
    counter = {"name": source["对手户名"], "account": source["交易对手账卡号"],
               "bank": source["对手开户银行"]}
    payer, payee = (counter, own) if direction == "in" else (own, counter)
    row = {"trade_no": source["交易流水号"], "trade_time": trade_time,
           **{"payer_" + key: value for key, value in payer.items()},
           **{"payee_" + key: value for key, value in payee.items()},
           "amount": amount, "balance": balance, "currency": currency,
           "summary": summary, "channel": source["交易类型"],
           "self_account": own_account, "self_name": ""}
    return row, "accepted", "transaction_valid", details, (account_column, own_account, direction)


def _cancellation_observation(source, record, profile=PROFILE):
    column = "交易账号" if source["交易账号"] else "交易卡号"
    # Keep the physical source token as the cancellation scope, even when v5
    # maps RMB to CNY for totals. Normalization never creates a cross-token pair.
    scope = ((record["member_path"], column, source[column], source["交易币种"])
             if source[column] and source["交易币种"] else None)
    amount, amount_issue = _money(source["交易金额"], "amount")
    balance, balance_issue = _money(source["交易余额"], "balance")
    issues = record["details"].get("validation_issues", "").split(",")
    eligible = (record["disposition"] == "unresolved" and len(issues) == 1
                and issues[0] in {"negative_reversal_text", "negative_revocation_text",
                                  "negative_reversal_revocation_text"}
                and cancellation_text(source))
    return Observation(record["record_no"], scope, source["交易流水号"], _time(source["交易时间"], fractional=profile in MICROSECOND_PROFILES),
                       amount if amount_issue is None else None,
                       balance if balance_issue is None else None,
                       {"进": "in", "入": "in", "出": "out"}.get(source["收付标志"]),
                       record["disposition"] == "accepted", eligible,
                       record["disposition"] == "non_transaction")


def refresh_stats(plan):
    """Recompute the parser summary after disposition or identity binding changes."""
    records, rows = plan["records"], plan["rows"]
    counts = dict.fromkeys(("accepted", "review", "unresolved", "non_monetary",
                           "non_transaction", "auxiliary", "cancelled"), 0)
    reasons, currency_totals, direction_totals = {}, {}, {}
    by_number = {}
    for record in records:
        counts[record["disposition"]] += 1
        reason = record["reason_code"]
        reasons[reason] = reasons.get(reason, 0) + 1
        by_number[record["record_no"]] = record
    for row in rows:
        currency, amount = row["currency"], row["amount"]
        currency_totals[currency] = _add_money(currency_totals.get(currency, Decimal(0)), amount)
        source_direction = by_number[row["_source_row"]]["details"]["source_direction"]
        direction = {"进": "in", "入": "in", "出": "out"}[source_direction]
        totals = direction_totals.setdefault(currency, {"in": Decimal(0), "out": Decimal(0)})
        totals[direction] = _add_money(totals[direction], amount)
    stats = {"total": len(records) - counts["auxiliary"], "ok": len(rows),
             "skipped": sum(counts[key] for key in ("review", "unresolved", "non_monetary",
                                                     "non_transaction", "cancelled")),
             "bad_examples": [{"source_row": record["record_no"], "reason": record["reason_code"],
                               "member_path": record["member_path"], "member_row": record["source_row"]}
                              for record in records if record["disposition"] in ("review", "unresolved")][:3],
             **{key: counts[key] for key in ("review", "non_transaction", "auxiliary", "non_monetary", "unresolved")},
             "record_total": len(records), "members": len(plan["members"]), "reason_counts": reasons,
             "currency_totals": {key: format(value, ".2f") for key, value in currency_totals.items()},
             "currency_direction_totals": {
                 key: {direction: format(value, ".2f") for direction, value in totals.items()}
                 for key, totals in direction_totals.items()}}
    if plan["profile"] in CANCELLATION_PROFILES or counts["cancelled"]:
        stats["cancelled"] = counts["cancelled"]
    plan["stats"] = stats
    return stats


def revoke_cancellations(plan, original_nos):
    """Revoke identity-conflicting pairs with one scan and one stats refresh."""
    requested = set(original_nos)
    revoked, retained = [], []
    for pair in plan.get("cancellations", []):
        (revoked if pair["original_record_no"] in requested else retained).append(pair)
    if not revoked:
        return 0
    records = {record["record_no"]: record for record in plan["records"]}
    hashes = {member["member_path"]: member["sha256"] for member in plan["members"]}
    revoked_originals = set()
    for pair in revoked:
        original_no = pair["original_record_no"]
        revoked_originals.add(original_no)
        original, cancellation = records[original_no], records[pair["cancellation_record_no"]]
        original.update(disposition="unresolved", reason_code="own_identity_conflict",
                        record_key=record_key(original["member_path"], hashes[original["member_path"]],
                                              original["source_row"]))
        original["details"].pop("own_identity_record", None)
        original["details"].pop("own_identity_sha256", None)
        original["details"]["validation_issues"] = "own_identity_conflict"
        cancellation.update(disposition="unresolved",
                            reason_code=cancellation["details"]["validation_issues"].split(",")[0])
        for record in (original, cancellation):
            record["details"]["financial_effect"] = "undetermined"
            for key in PAIR_FIELDS:
                record["details"].pop(key, None)
    plan["cancelled_rows"][:] = [row for row in plan["cancelled_rows"] if row["_source_row"] not in revoked_originals]
    plan["cancellations"][:] = retained
    refresh_stats(plan)
    return len(revoked)


def revoke_cancellation(plan, original_no):
    """Fail closed when one paired original conflicts with a historical identity."""
    return bool(revoke_cancellations(plan, [original_no]))


def parse(source, *, profile=PROFILE) -> dict:
    """Parse a seekable binary ZIP, or reject the complete preview safely.

    Member order is the ZIP directory order.  CSV ``source_row`` counts logical
    records including the header; ``record_no`` counts every member's data
    record globally.  Empty rows are records and must have the declared width.
    """
    if profile not in SUPPORTED_PROFILES:
        raise FeedbackImportError("unknown_profile")
    if profile in EVIDENCE_PROFILES:
        # The outer original stays registered unchanged; the ephemeral reader
        # only presents nested CSV leaves to the same semantic interpreter.
        source = _flatten_family(source)
    record_limit = max_records(profile)
    rows, records, members, contexts, accounts = [], [], [], [], {}
    cancellation_index = CancellationIndex() if profile in CANCELLATION_PROFILES else None
    expanded, transaction_total = 0, 0
    counts = {"accepted": 0, "review": 0, "non_transaction": 0, "auxiliary": 0,
              "non_monetary": 0, "unresolved": 0}
    reason_counts, currency_totals, direction_totals = {}, {}, {}
    try:
        source.seek(0)
        with zipfile.ZipFile(source, "r") as archive:
            infos = (_validate_members(archive, profile) if profile in EVIDENCE_PROFILES
                     else _validate_members(archive))
            for info in infos:
                payload, expanded = (_read_member(archive, info, expanded, profile) if profile in EVIDENCE_PROFILES
                                     else _read_member(archive, info, expanded))
                digest = hashlib.sha256(payload).hexdigest()
                text, encoding = _decode(payload, profile=profile)
                table = _table(text, encoding, profile=profile)
                role, header = next(table)
                member = {"member_path": info.filename, "role": role, "sha256": digest,
                          "size_bytes": len(payload), "encoding": encoding, "row_count": 0}
                members.append(member)
                for source_row, raw in enumerate(table, start=2):
                    record_no = len(records) + 1
                    if record_no > record_limit:
                        raise FeedbackImportError("record_count")
                    member["row_count"] += 1
                    context = row = None
                    if role == "transaction":
                        transaction_total += 1
                        row, disposition, reason, details, context = _transaction(raw, profile)
                    else:
                        disposition, reason, details = "auxiliary", "auxiliary_table", {"role": role}
                        if role == "account":
                            identity = (raw["账户开户名称"], raw["账号开户银行"])
                            for column in ("交易账号", "交易卡号"):
                                if raw[column]:
                                    key = (column, raw[column])
                                    previous = accounts.get(key)
                                    if key not in accounts:
                                        accounts[key] = (*identity, record_no)
                                    elif previous is not None and previous[:2] != identity:
                                        accounts[key] = None
                    if profile in EVIDENCE_PROFILES:
                        from . import evidence_resolution
                        value = (evidence_resolution.feedback_evidence(raw, row, context) if role == "transaction" and row is not None
                                 else evidence_resolution.auxiliary_evidence(header, [raw[h] for h in header], role))
                        if value:
                            details[evidence_resolution.DETAIL_FIELD] = value
                    record = {"record_no": record_no, "member_path": info.filename,
                              "source_row": source_row,
                              "disposition": disposition, "reason_code": reason, "details": details}
                    records.append(record)
                    if cancellation_index is not None and role == "transaction":
                        cancellation_index.add(_cancellation_observation(raw, record, profile))
                    counts[disposition] += 1
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1
                    if row is not None:
                        row.update(_source_row=record_no, _source_sheet="",
                                   _source_row_kind="archive_record")
                        rows.append(row)
                        contexts.append((row, context, record))
                        currency = row["currency"]
                        currency_totals[currency] = _add_money(currency_totals.get(currency, Decimal(0)), row["amount"])
                        by_direction = direction_totals.setdefault(currency, {"in": Decimal(0), "out": Decimal(0)})
                        by_direction[context[2]] = _add_money(by_direction[context[2]], row["amount"])
                # Do not retain previous members' raw payloads or cell rows.
                del payload, text, table
    except FeedbackImportError:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, OSError,
            RuntimeError, NotImplementedError, ValueError, TypeError, AttributeError, zlib.error):
        raise FeedbackImportError("invalid_archive") from None

    member_hashes = {member["member_path"]: member["sha256"] for member in members}
    for record in records:
        if record["disposition"] != "accepted":
            record["record_key"] = record_key(record["member_path"],
                                              member_hashes[record["member_path"]], record["source_row"])
    for row, (column, own_account, direction), record in contexts:
        prefix = "payee_" if direction == "in" else "payer_"
        identity = accounts.get((column, own_account)) if profile not in EVIDENCE_PROFILES else None
        if identity is not None:
            name, bank, identity_record = identity
            row["self_name"] = name
            row[prefix + "name"], row[prefix + "bank"] = name, bank
            record["details"]["own_identity_record"] = str(identity_record)
        resolved_identity = identity_digest(row["self_name"], row[prefix + "bank"])
        record["details"]["own_identity_sha256"] = resolved_identity
        record["record_key"] = record_key(record["member_path"], member_hashes[record["member_path"]],
                                           record["source_row"], resolved_identity)
        row["_record_key"] = record["record_key"]
    bad_examples = [{"source_row": record["record_no"], "reason": record["reason_code"],
                     "member_path": record["member_path"], "member_row": record["source_row"]}
                    for record in records if record["disposition"] in ("review", "unresolved")][:3]
    stats = {"total": transaction_total, "ok": len(rows),
             "skipped": counts["review"] + counts["unresolved"] + counts["non_monetary"] + counts["non_transaction"],
             "bad_examples": bad_examples, "review": counts["review"],
             "non_transaction": counts["non_transaction"], "auxiliary": counts["auxiliary"],
             "non_monetary": counts["non_monetary"], "unresolved": counts["unresolved"],
             "record_total": len(records), "members": len(members),
             "reason_counts": reason_counts,
             "currency_totals": {key: format(value, ".2f") for key, value in currency_totals.items()},
             "currency_direction_totals": {
                 key: {direction: format(value, ".2f") for direction, value in totals.items()}
                 for key, totals in direction_totals.items()}}
    plan = {"profile": profile, "rows": rows, "records": records, "members": members, "stats": stats}
    if profile in EVIDENCE_PROFILES:
        plan["_member_families"] = getattr(source, "_feedback_families", {})
    if cancellation_index is not None:
        pairs = cancellation_index.pairs()
        originals = {pair["original_record_no"] for pair in pairs}
        plan["cancelled_rows"] = [row for row in rows if row["_source_row"] in originals]
        rows[:] = [row for row in rows if row["_source_row"] not in originals]
        plan["cancellations"] = pairs
        for pair in pairs:
            original_no, cancellation_no = pair["original_record_no"], pair["cancellation_record_no"]
            for number, role, paired in ((original_no, "original", cancellation_no),
                                         (cancellation_no, "cancellation", original_no)):
                record = records[number - 1]
                record.update(disposition="cancelled", reason_code="matched_cancellation")
                record["details"].update(financial_effect="analysis_excluded", cancellation_policy=POLICY,
                                         cancellation_role=role, paired_record_no=str(paired),
                                         cancellation_witness_record_no=str(pair["witness_record_no"]))
        refresh_stats(plan)
    if profile in EVIDENCE_PROFILES:
        from . import evidence_resolution
        evidence_resolution.bind_occurrences(plan)
    return plan


def _family_members(source, *, payloads=False):
    """Walk the closed feedback family without extracting archive paths."""
    from . import table_archive
    found, seen = [], set()
    budget = {"members": 0, "bytes": 0}

    def visit(stream, prefix="", depth=0):
        with zipfile.ZipFile(stream) as archive:
            local = set()
            for info in archive.infolist():
                budget["members"] += 1
                name = table_archive._path(info.filename)
                if (name in local or budget["members"] > EVIDENCE_MAX_MEMBERS or info.filename != info.orig_filename
                        or info.flag_bits & 1 or stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFDIR if info.is_dir() else stat.S_IFREG)):
                    raise FeedbackImportError("unsafe_member_path")
                local.add(name)
                if info.is_dir():
                    if info.file_size:
                        raise FeedbackImportError("directory_payload")
                    continue
                path = f"{prefix}/{name}" if prefix else name
                if path in seen or not 0 <= info.file_size <= table_archive.MAX_MEMBER_BYTES:
                    raise FeedbackImportError("member_size")
                seen.add(path)
                budget["bytes"] += info.file_size
                if budget["bytes"] > EVIDENCE_MAX_EXPANDED_BYTES:
                    raise FeedbackImportError("expanded_size")
                if name.lower().endswith(".zip"):
                    if depth >= table_archive.MAX_DEPTH:
                        raise FeedbackImportError("archive_depth")
                    visit(io.BytesIO(archive.read(info)), path, depth + 1)
                elif name.lower().endswith(".csv"):
                    with archive.open(info) as leaf:
                        sample = leaf.read(65536)
                        headers, _ = table_archive._header(sample)
                        if frozenset(headers) not in _SCHEMA_SIGNATURES:
                            raise FeedbackImportError("unknown_schema")
                        content = sample + leaf.read() if payloads else None
                    if payloads and len(content) != info.file_size:
                        raise FeedbackImportError("member_size_mismatch")
                    found.append((path, content, prefix))
                else:
                    raise FeedbackImportError("non_csv_member")
    try:
        source.seek(0)
        visit(source)
        return found
    finally:
        # The recursive closure owns every discovered payload until its
        # self-cell is broken, including when traversal rejects a later member.
        visit = None
        source.seek(0)


def is_family(source):
    try:
        return bool(_family_members(source))
    except (FeedbackImportError, ValueError, OSError, zipfile.BadZipFile, RuntimeError):
        return False


def _flatten_family(source):
    members = _family_members(source, payloads=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for path, content, _family in members:
            archive.writestr(path, content)
    buffer.seek(0)
    buffer._feedback_families = {path: family for path, _content, family in members}
    return buffer
