"""Validated versioned case packages and compensating photo operations."""
import datetime
import hashlib
import io
import json
import math
import os
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
import re
import shutil
import tempfile
import uuid
import zipfile
import zlib

import duckdb
from fastapi import HTTPException

from . import db, feedback_import, importer, evidence_resolution, import_attachments, event_resolution

CASE_PKG_COLS = {
    "flows": "id, batch_id, trade_no, trade_time, payer_name, payer_account, payer_bank, "
             "payee_name, payee_account, payee_bank, amount, balance, currency, summary, "
             "channel, self_account, self_name",
    "batches": "id, filename, mode, rows_total, rows_imported, rows_skipped, rows_dup, imported_at",
    "tracked_flows": "flow_id, canvas_id, tracked_at",
    "canvases": "id, name, snapshot, updated_at",
    "main_accounts": "account, name, source, marked_at",
    "account_ids": "account, id_no, updated_at",
    "account_photos": "account, fname, updated_at, crop_cx, crop_cy, crop_r",
    "focus_nodes": "account, scale",
}
CASE_PKG_TABLES = list(CASE_PKG_COLS)
PROVENANCE_PKG_COLS = {
    "import_sources": "source_id, filename, sha256, size_bytes, mode, mapping_json, sheet, parser_version, row_kind, imported_at",
    "flow_sources": "source_id, source_row, flow_id, disposition",
}
JOURNAL_PKG_COLS = {
    "import_members": "source_id, member_path, role, sha256, size_bytes, encoding, row_count",
    "import_records": "source_id, record_no, member_path, source_row, record_key, disposition, reason_code, details_json",
}
EVIDENCE_PKG_COLS = {"evidence_sources": "source_id, batch_id, active"}
_ALL_PKG_COLS = {**CASE_PKG_COLS, **PROVENANCE_PKG_COLS, **JOURNAL_PKG_COLS, **EVIDENCE_PKG_COLS}
_PHOTO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.(?:jpg|jpeg|png|webp|bmp)", re.I)
_TYPES = {
    "id": "bigint", "flow_id": "bigint", "batch_id": "bigint", "canvas_id": "bigint",
    "trade_time": "timestamp", "tracked_at": "timestamp", "imported_at": "timestamp",
    "updated_at": "timestamp", "marked_at": "timestamp", "amount": "decimal(18,2)",
    "balance": "decimal(18,2)", "rows_total": "bigint", "rows_imported": "bigint",
    "rows_skipped": "bigint", "rows_dup": "bigint", "crop_cx": "double",
    "crop_cy": "double", "crop_r": "double", "scale": "double",
    "active": "boolean", "source_row": "bigint", "size_bytes": "bigint", "record_no": "bigint", "row_count": "bigint",
}
_BIGINT_MIN, _BIGINT_MAX = -(2**63), 2**63 - 1


def photo_name(value):
    """A photo name is a portable basename, never a path supplied by an archive."""
    return isinstance(value, str) and bool(_PHOTO_NAME.fullmatch(value)) and ".." not in value


def require_case(case_id):
    row = db.q("select id, name from cases where id = ?", [case_id])
    if not row:
        raise HTTPException(404, "案件不存在")
    return row[0]


def export_package(case_id):
    # The lock spans every table and photo read, so writers cannot split the snapshot.
    with tempfile.TemporaryDirectory(prefix="fundcase-export-") as tmp, db.transaction() as con:
        case_name = require_case(case_id)["name"]
        buf = io.BytesIO()
        cancellation_profiles = sorted(feedback_import.CANCELLATION_PROFILES)
        cancellation_supported = ",".join("?" for _ in cancellation_profiles)
        has_cancellation_rules = bool(con.execute(f"""select 1 from import_sources where case_id=?
                  and parser_version in ({cancellation_supported}) limit 1""", [case_id, *cancellation_profiles]).fetchone())
        has_table_records = bool(con.execute("""select 1 from import_sources where case_id=?
                  and row_kind='table_record' limit 1""", [case_id]).fetchone())
        has_resolution = bool(con.execute("select 1 from import_sources where case_id=? and parser_version in (" + ",".join("?" for _ in evidence_resolution.PROFILES) + ") limit 1", [case_id, *sorted(evidence_resolution.PROFILES)]).fetchone())
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr("meta.json", json.dumps({
                "name": case_name, "exported_at": datetime.datetime.now().isoformat(),
                "format": 5 if has_resolution else 4 if has_table_records else 3 if has_cancellation_rules else 2,
                "source_files": "not_included",
            }, ensure_ascii=False))
            has_journals = any(con.execute(f"select 1 from {table} where case_id=? limit 1", [case_id]).fetchone()
                               for table in JOURNAL_PKG_COLS)
            for table, cols in _ALL_PKG_COLS.items():
                if table in EVIDENCE_PKG_COLS and not has_resolution:
                    continue
                if table in JOURNAL_PKG_COLS and not has_journals:
                    continue
                path = Path(tmp) / f"{table}.parquet"
                con.sql(f"select {cols} from {table} where case_id = ?", params=[case_id]).write_parquet(str(path))
                archive.writestr(path.name, path.read_bytes())
            written = set()
            for row in db.q("select fname from account_photos where case_id = ?", [case_id]):
                name = row["fname"]
                if not photo_name(name):
                    raise HTTPException(409, "案件照片文件名无效，无法生成完整案件包")
                path = db.DATA_DIR / "photos" / f"c{case_id}" / name
                if not path.is_file() or path.is_symlink():
                    raise HTTPException(409, "案件照片文件缺失，无法生成完整案件包")
                if name not in written:
                    archive.writestr(f"photos/{name}", path.read_bytes())
                    written.add(name)
        return buf.getvalue(), case_name


def _invalid(message):
    raise HTTPException(400, f"案件包无效：{message}")


def _check_integer_columns(check, path, table, cols):
    """Reject lossy numeric casts while allowing nullable legacy counters/references."""
    fields = [field for field in cols if _TYPES.get(field) == "bigint"]
    if not fields:
        return
    cursor = check.execute(f"select {','.join(fields)} from read_parquet(?)", [str(path)])
    while rows := cursor.fetchmany(1024):
        for row in rows:
            for field, value in zip(fields, row):
                if value is None:
                    continue
                try:
                    integer = Decimal(str(value))
                    valid = (not isinstance(value, bool) and integer.is_finite()
                             and _BIGINT_MIN <= integer <= _BIGINT_MAX
                             and integer == integer.to_integral_value())
                except (InvalidOperation, ValueError, TypeError):
                    valid = False
                if not valid:
                    _invalid(f"{table}.{field} 须为 BIGINT 范围内的整数")


def _canvas_snapshot(value, flow_ids, mapping=None):
    """Keep display geometry, validating the fields its public consumer iterates.

    Legacy empty/metadata-free snapshots remain valid. Stored transaction metadata
    can outlive deleted batches; discard those missing IDs, rather than rejecting
    an otherwise valid package. The live graph is rebuilt from the imported DB.
    """
    if not isinstance(value, str):
        _invalid("画布快照无效")
    snapshot = json.loads(value)
    if not isinstance(snapshot, dict):
        _invalid("画布快照无效")

    def point(item):
        return (isinstance(item, dict)
                and all(type(item.get(axis)) in (int, float) and math.isfinite(item[axis])
                        for axis in ("x", "y")))

    for field in ("nodes", "edges"):
        if field not in snapshot:
            continue
        entries = snapshot[field]
        if not isinstance(entries, list):
            _invalid(f"画布快照 {field} 须为数组")
        for item in entries:
            keys = ("id",) if field == "nodes" else ("source", "target")
            if (not isinstance(item, dict)
                    or any(not isinstance(item.get(key), str) or not item[key] for key in keys)):
                _invalid(f"画布快照 {field} 对象标识无效")
            if field == "nodes":
                if not point(item):
                    _invalid("画布快照节点坐标无效")
                continue
            points = item.get("controlPoints")
            if points is not None and (not isinstance(points, list) or not all(point(p) for p in points)):
                _invalid("画布快照关系控制点无效")
            data = item.get("data")
            if data is None:
                continue
            if not isinstance(data, dict):
                _invalid("画布快照关系元数据无效")
            if "ids" not in data:
                continue
            if not isinstance(data["ids"], list):
                _invalid("画布快照流水标识须为数组")
            kept = []
            for flow_id in data["ids"]:
                if not (type(flow_id) is int or isinstance(flow_id, str) and re.fullmatch(r"[0-9]+", flow_id)):
                    _invalid("画布快照流水标识须为整数")
                old_id = int(flow_id)
                if not 0 < old_id <= _BIGINT_MAX:
                    _invalid("画布快照流水标识超出 BIGINT 范围")
                if old_id in flow_ids:
                    new_id = mapping[old_id] if mapping is not None else old_id
                    kept.append(str(new_id) if isinstance(flow_id, str) else new_id)
            data["ids"] = kept
    return json.dumps(snapshot, ensure_ascii=False)


def _feedback_amount(details, field="canonical_amount"):
    """Read only an exact, finite, representable amount from semantic metadata."""
    value = details.get(field)
    if not isinstance(value, str) or not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", value):
        return None
    try:
        number = Decimal(value)
        with localcontext() as context:
            context.prec = 40
            if (number.is_finite() and number.copy_abs() <= Decimal("9999999999999999.99")
                    and number == number.quantize(Decimal("0.01"))):
                return number
    except InvalidOperation:
        pass
    return None


def _feedback_journals(check, paths):
    """Check the closed archive record ledger without needing the original ZIP."""
    from . import feedback_import

    archives = dict(check.execute(
        "select source_id,parser_version from import_sources where row_kind='archive_record'").fetchall())
    if "import_members" not in paths:
        if archives:
            _invalid("反馈包来源缺少成员与记录日志")
        return
    for table in ("import_members", "import_records"):
        totals = check.execute(f"select source_id,count(*) from {table} where source_id in (select source_id from import_sources where row_kind='archive_record') group by source_id").fetchall()
        if any(count > ((feedback_import.EVIDENCE_MAX_MEMBERS if archives[source_id] == feedback_import.EVIDENCE_PROFILE else feedback_import.MAX_MEMBERS) if table == "import_members"
                        else feedback_import.max_records(archives[source_id])) for source_id, count in totals):
            _invalid("反馈包日志总量超出限制")
    members = check.execute("select m.source_id,m.member_path,m.role,m.sha256,m.size_bytes,m.encoding,m.row_count from import_members m join import_sources s on s.source_id=m.source_id where s.row_kind='archive_record'").fetchall()
    records = check.execute("select r.source_id,r.record_no,r.member_path,r.source_row,r.record_key,r.disposition,r.reason_code,r.details_json from import_records r join import_sources s on s.source_id=r.source_id where s.row_kind='archive_record' order by r.source_id,r.record_no").fetchall()
    by_source, member_rows, seen_paths = {}, {}, {}
    for source_id, path, role, sha256, size, encoding, count in members:
        if (source_id not in archives or not isinstance(path, str) or not path
                or len(path) > 65535 or "\\" in path or ":" in path
                or any(ord(char) < 32 or ord(char) == 127 for char in path)
                or any(part in ("", ".", "..") for part in path.split("/"))
                or Path(path).suffix.lower() != ".csv"
                or role not in feedback_import.ROLES
                or not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256)
                or size is None or not 0 <= size <= (feedback_import.EVIDENCE_MAX_MEMBER_BYTES if archives[source_id] == feedback_import.EVIDENCE_PROFILE else feedback_import.MAX_MEMBER_BYTES)
                or count is None or not 0 <= count <= feedback_import.max_records(archives[source_id])
                or encoding not in ("utf-8-sig", "gb18030")):
            _invalid("反馈包成员元数据无效")
        if path in seen_paths.setdefault(source_id, set()):
            _invalid("反馈包成员路径重复")
        seen_paths[source_id].add(path)
        by_source.setdefault(source_id, []).append((path, size, count))
        member_rows[(source_id, path)] = (role, sha256, count)
    if set(by_source) != set(archives):
        _invalid("反馈包来源与成员不一致")
    for source_id, values in by_source.items():
        if (len(values) > (feedback_import.EVIDENCE_MAX_MEMBERS if archives[source_id] == feedback_import.EVIDENCE_PROFILE else feedback_import.MAX_MEMBERS)
                or sum(value[1] for value in values) > (feedback_import.EVIDENCE_MAX_EXPANDED_BYTES if archives[source_id] == feedback_import.EVIDENCE_PROFILE else feedback_import.MAX_EXPANDED_BYTES)
                or sum(value[2] for value in values) > feedback_import.max_records(archives[source_id])):
            _invalid("反馈包成员总量超出限制")
    linked_flows = {(source_id, row): (disposition, name or "", account or "",
                                     (payer_name or "", payer_account or "", payer_bank or ""),
                                     (payee_name or "", payee_account or "", payee_bank or ""), amount, currency)
                    for source_id, row, disposition, name, account, payer_name, payer_account, payer_bank,
                    payee_name, payee_account, payee_bank, amount, currency in check.execute(
                        "select s.source_id,s.source_row,s.disposition,f.self_name,f.self_account,"
                        "f.payer_name,f.payer_account,f.payer_bank,f.payee_name,f.payee_account,f.payee_bank,f.amount,f.currency "
                        "from flow_sources s join flows f on s.flow_id=f.id "
                        "join import_sources p on s.source_id=p.source_id where p.row_kind='archive_record'").fetchall()}
    positions, sequences, accepted, identity_refs, cancellations, known_facts = {}, {}, {}, [], {}, {}
    record_currencies = {}
    legacy_conflicts = []
    for source_id, number, path, source_row, key, disposition, reason, details_json in records:
        member = member_rows.get((source_id, path))
        if member is None:
            _invalid("反馈包记录引用了不存在的成员")
        role, member_hash, row_count = member
        legacy = archives[source_id] == feedback_import.IDENTITY_PROFILE
        currency_rule = archives[source_id] in feedback_import.CURRENCY_PROFILES
        cancellation_rule = archives[source_id] in feedback_import.CANCELLATION_PROFILES
        dispositions = ("review",) if legacy else ("non_monetary", "unresolved", *(('cancelled',) if cancellation_rule else ()))
        if (number is None or number != sequences.get(source_id, 0) + 1
                or source_row is None or not 2 <= source_row <= row_count + 1
                or disposition not in ("imported", "duplicate", "non_transaction", "auxiliary", "removed", *dispositions)
                or reason not in (*feedback_import.REASON_CODES, "flow_deleted")
                or (role == "transaction") == (disposition == "auxiliary")
                or (disposition in ("imported", "duplicate") and reason != "transaction_valid")
                or (disposition == "removed" and reason != "flow_deleted")
                or (disposition == "non_transaction" and reason != "feedback_only")
                or (disposition == "auxiliary" and reason != "auxiliary_table")
                or (disposition == "non_monetary" and reason != "zero_amount")
                or ((disposition == "cancelled") != (reason == "matched_cancellation"))
                or (disposition in ("review", "unresolved") and reason in ("transaction_valid", "auxiliary_table", "feedback_only", "flow_deleted"))):
            _invalid("反馈包记录位置或处置无效")
        if source_row in positions.setdefault((source_id, path), set()):
            _invalid("反馈包成员记录位置重复")
        positions[(source_id, path)].add(source_row)
        sequences[source_id] = number
        if not isinstance(details_json, str) or len(details_json.encode("utf-8")) > 8192:
            _invalid("反馈包记录语义元数据过大或无效")
        details = json.loads(details_json)
        cancellation_fields = {"cancellation_policy", "cancellation_role", "paired_record_no",
                               "cancellation_witness_record_no"}
        detail_fields = feedback_import.DETAILS_FIELDS - (
            {"financial_effect", "validation_issues", "canonical_amount"} if legacy else {"review_reasons"})
        if not currency_rule:
            detail_fields -= feedback_import.CURRENCY_DETAILS_FIELDS
        if archives[source_id] in evidence_resolution.PROFILES:
            detail_fields |= {evidence_resolution.DETAIL_FIELD, "occurrence_scope", "evidence_family", "family_member_sha256"}
        if archives[source_id] in evidence_resolution.PROFILES and (not isinstance(details, dict) or any(not isinstance(details.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", details[field]) for field in ("evidence_family", "family_member_sha256"))):
            _invalid("身份证据逻辑来源指纹无效")
        if not isinstance(details, dict) or not set(details).issubset(detail_fields):
            _invalid("反馈包记录语义元数据无效")
        if disposition != "cancelled" and set(details) & cancellation_fields:
            _invalid("非配对排除记录不能声明冲正关联")
        for field, value in details.items():
            if not isinstance(value, str) or len(value.encode("utf-8")) > (8192 if field == evidence_resolution.DETAIL_FIELD else 1024):
                _invalid("反馈包记录语义值无效")
            if field == evidence_resolution.DETAIL_FIELD:
                evidence = evidence_resolution.validate_evidence(value)
                if evidence["role"] != ("transaction" if role == "transaction" else "manifest" if role == "task" else role):
                    _invalid("反馈包身份声明与来源角色不一致")
            if field == "role" and value != role:
                _invalid("反馈包记录角色不一致")
            if field == "source_account_column" and value not in ("", "交易账号", "交易卡号"):
                _invalid("反馈包账号列元数据无效")
            if field == "source_columns" and value not in ("", "交易账号", "交易卡号", "交易账号,交易卡号"):
                _invalid("反馈包账号列元数据无效")
            if field == "event_type" and value not in ("reported", "reversal_text", "revocation_text", "reversal_revocation_text", "unknown_negative"):
                _invalid("反馈包事件语义无效")
            if field == "account_card_relation" and value not in ("both_present_equal", "both_present_distinct", "account_only", "card_only", "missing"):
                _invalid("反馈包账号关系元数据无效")
            if field in ("review_reasons", "validation_issues") and any(code not in feedback_import.REASON_CODES for code in value.split(",")):
                _invalid("反馈包待核查原因无效")
            if legacy and field == "review_reasons" and "own_identity_conflict" in value.split(","):
                if disposition != "review" or reason != "own_identity_conflict":
                    _invalid("旧版身份保护须保留明确冲突处置")
            if field.endswith("_sha256") and not re.fullmatch(r"[0-9a-f]{64}", value):
                _invalid("反馈包语义摘要无效")
            if field.endswith("_length") and (not re.fullmatch(r"[0-9]{1,10}", value) or int(value) > feedback_import.MAX_FIELD_BYTES):
                _invalid("反馈包语义长度无效")
            if field == "own_identity_record":
                if not re.fullmatch(r"[1-9][0-9]{0,8}", value):
                    _invalid("反馈包身份来源记录无效")
                identity_refs.append((source_id, int(value)))
        if currency_rule:
            currency_fields = set(details) & feedback_import.CURRENCY_DETAILS_FIELDS
            needs_currency = (role == "transaction" and disposition != "non_transaction"
                              and "invalid_currency" not in details.get("validation_issues", "").split(","))
            if needs_currency:
                expected_currency = feedback_import.currency_details(details.get("source_currency"))
                if expected_currency is None or any(details.get(field) != value for field, value in expected_currency.items()):
                    _invalid("反馈包币种规范化依据无效")
                record_currencies[source_id, number] = (details["source_currency"], details["canonical_currency"])
            elif currency_fields:
                _invalid("无有效交易币种的记录不能声明币种规范化")
        canonical_amount = _feedback_amount(details)
        if legacy and reason == "own_identity_conflict":
            if details.get("review_reasons") != "own_identity_conflict":
                _invalid("旧版身份保护须保留冲突原因")
            legacy_conflicts.append((path, member_hash))
        if not legacy:
            if "canonical_amount" in details and (canonical_amount is None or role != "transaction"
                    or not re.fullmatch(r"-?(?:0|[1-9][0-9]*)\.[0-9]{2}", details["canonical_amount"])):
                _invalid("反馈包规范金额无效")
            if "signed_amount" in details and _feedback_amount(details, "signed_amount") != canonical_amount:
                _invalid("反馈包规范金额与原始金额不一致")
            effect = ("source_reported" if disposition in ("imported", "duplicate", "removed") else
                      "analysis_excluded" if disposition == "cancelled" else
                      "no_movement" if disposition == "non_monetary" else
                      "undetermined" if disposition == "unresolved" else "not_a_flow")
            if details.get("financial_effect", "not_a_flow" if role != "transaction" else None) != effect:
                _invalid("反馈包财务影响与自动处置不一致")
            if disposition == "non_transaction" and (
                    details.get("signed_amount") != "" or details.get("source_direction") != ""
                    or any(field in details for field in ("signed_amount_sha256", "signed_amount_length",
                                                          "source_direction_sha256", "source_direction_length",
                                                          "validation_issues"))):
                _invalid("查询反馈记录不能包含交易金额、方向或未解决问题")
            if disposition == "auxiliary" and {field: value for field, value in details.items() if field not in {evidence_resolution.DETAIL_FIELD, "evidence_family", "family_member_sha256"}} != {"role": role}:
                _invalid("辅助记录只能保留其来源角色")
            issues = details.get("validation_issues", "").split(",")
            if disposition == "non_monetary" and (canonical_amount != Decimal("0.00") or "zero_amount" not in issues):
                _invalid("非金额记录须保留可验证的真实零金额")
            if disposition == "unresolved" and (issues[0] != reason or canonical_amount == Decimal("0.00")):
                _invalid("未解决记录须保留未确定的财务影响与原因")
            if disposition in ("imported", "duplicate", "removed") and (
                    canonical_amount is None or canonical_amount <= 0 or details.get("reported_status") not in ("01", "1")
                    or "validation_issues" in details):
                _invalid("反馈包交易须保留正金额和明确的来源状态")
            if disposition in ("non_transaction", "auxiliary") and "canonical_amount" in details:
                _invalid("非交易记录不能声明规范交易金额")
            if disposition == "cancelled":
                if (details.get("cancellation_policy") != "paired-cancellation-v1"
                        or details.get("cancellation_role") not in ("original", "cancellation")
                        or any(not re.fullmatch(r"[1-9][0-9]{0,8}", details.get(field, ""))
                               for field in ("paired_record_no", "cancellation_witness_record_no"))
                        or canonical_amount is None or canonical_amount == 0
                        or details.get("reported_status") not in ("01", "1")
                        or details.get("source_direction") not in ("进", "入", "出")
                        or (canonical_amount > 0) != (details["cancellation_role"] == "original")
                        or (canonical_amount > 0 and "validation_issues" in details)
                        or (canonical_amount < 0 and details.get("validation_issues") not in (
                            "negative_reversal_text", "negative_revocation_text", "negative_reversal_revocation_text"))):
                    _invalid("冲正排除须保留有效配对规则、金额及来源状态")
                cancellations[source_id, number] = (path, details, canonical_amount)
        identity_hash = ""
        cancelled_original = disposition == "cancelled" and details["cancellation_role"] == "original"
        if disposition in ("imported", "duplicate", "removed") or cancelled_original:
            identity_hash = details.get("own_identity_sha256")
            if (not isinstance(identity_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", identity_hash)
                    or details.get("source_direction") not in ("出", "进", "入")):
                _invalid("反馈包记录缺少有效的本方身份摘要")
            if disposition not in ("removed", "cancelled"):
                linked = linked_flows.get((source_id, number))
                if linked is None:
                    _invalid("反馈包身份摘要缺少流水来源")
                _, name, account, payer, payee, amount, currency = linked
                own_name, own_account, own_bank = payer if details["source_direction"] == "出" else payee
                if own_name != name or own_account != account:
                    _invalid("反馈包流水本方名称或账号与规范身份不一致")
                if not legacy and (amount is None or amount <= 0 or amount != canonical_amount):
                    _invalid("反馈包交易金额与来源金额不一致")
                if currency_rule and currency != details["canonical_currency"]:
                    _invalid("反馈包交易币种与规范币种不一致")
                expected_identity = hashlib.sha256(json.dumps(
                    [name, own_bank], ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
                if identity_hash != expected_identity:
                    _invalid("反馈包身份摘要与流水本方身份不一致")
        elif "own_identity_sha256" in details or "own_identity_record" in details:
            _invalid("非流水记录不能声明已解析的本方身份")
        expected_key = hashlib.sha256(json.dumps(
            [feedback_import.IDENTITY_PROFILE, path, member_hash, source_row, identity_hash],
            ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        if key != expected_key:
            _invalid("反馈包记录键与成员或本方身份不一致")
        if disposition in ("imported", "duplicate"):
            accepted[(source_id, number)] = disposition
            known_facts.setdefault(key, []).append((linked_flows[source_id, number], details["source_direction"]))
    for (source_id, path), (_, _, count) in member_rows.items():
        if len(positions.get((source_id, path), ())) != count:
            _invalid("反馈包成员记录数不闭合")
    record_lookup = {(source_id, number): (path, disposition, key)
                     for source_id, number, path, _row, key, disposition, *_ in records}
    for (source_id, number), (path, details, amount) in cancellations.items():
        paired_no = int(details["paired_record_no"])
        witness_no = int(details["cancellation_witness_record_no"])
        paired = cancellations.get((source_id, paired_no))
        witness = record_lookup.get((source_id, witness_no))
        if (len({number, paired_no, witness_no}) != 3 or paired is None
                or paired[0] != path or witness is None or witness[0] != path
                or member_rows[source_id, path][0] != "transaction"
                or paired[1].get("paired_record_no") != str(number)
                or paired[1].get("cancellation_witness_record_no") != str(witness_no)
                or paired[1].get("cancellation_role") == details["cancellation_role"]
                or paired[2].copy_negate() != amount
                or (paired[1]["source_direction"] == "出") != (details["source_direction"] == "出")
                or paired[1].get("source_account_column") != details.get("source_account_column")):
            _invalid("冲正排除配对须双向完整、同来源且等额异号")
        if archives[source_id] in feedback_import.CURRENCY_PROFILES:
            scope_currency = record_currencies[source_id, number]
            if (record_currencies.get((source_id, paired_no)) != scope_currency
                    or record_currencies.get((source_id, witness_no)) != scope_currency):
                _invalid("冲正排除配对及见证记录须保留相同原始及规范币种")
        if details["cancellation_role"] == "original":
            # A package may retain older facts hidden by a v3/v4/v5 assessment.
            # Validate against those actual facts too, not just two mutually
            # consistent (but possibly forged) cancellation amounts.
            for linked, old_direction in known_facts.get(record_lookup[source_id, number][2], []):
                _, name, account, payer, payee, old_amount, old_currency = linked
                own_name, own_account, own_bank = payer if details["source_direction"] == "出" else payee
                identity = feedback_import.identity_digest(name, own_bank)
                if (old_amount != amount or (old_direction == "出") != (details["source_direction"] == "出")
                        or own_name != name or own_account != account
                        or (archives[source_id] in feedback_import.CURRENCY_PROFILES
                            and old_currency != details["canonical_currency"])
                        or identity != details["own_identity_sha256"]):
                    _invalid("冲正排除与保留原流水的金额、方向或身份不一致")
    cancellation_members = {(path, member_rows[source_id, path][1])
                            for (source_id, _), (path, _, _) in cancellations.items()}
    if any(member not in cancellation_members for member in legacy_conflicts):
        _invalid("旧版身份保护须引用同一交易成员的配对冲正历史")
    account_records = {(source_id, number) for source_id, number, path, *_ in records
                       if member_rows[(source_id, path)][0] == "account"}
    if any(reference not in account_records for reference in identity_refs):
        _invalid("反馈包身份来源引用了无效记录")
    links = {reference: values[0] for reference, values in linked_flows.items()}
    if links != accepted:
        _invalid("反馈包处置与流水来源不一致")


def _table_journals(check, paths):
    """Validate automatic-table journals independently of bank-feedback rules."""
    tables = check.execute("select source_id,parser_version,imported_at from import_sources where row_kind='table_record'").fetchall()
    if not tables:
        return
    if not set(JOURNAL_PKG_COLS).issubset(paths):
        _invalid("自动表格来源缺少成员与记录日志")
    from . import table_import, table_archive, table_cancellations, table_contracts
    source_profiles = {source_id: profile for source_id, profile, _time in tables}
    source_times = {source_id: time for source_id, _profile, time in tables}
    has_cancellation_profile = bool(table_archive.CANCELLATION_PROFILES.intersection(source_profiles.values()))

    for table, limit in (("import_members", table_archive.EVIDENCE_MAX_MEMBERS),
                         ("import_records", table_archive.EVIDENCE_MAX_RECORDS)):
        if check.execute(f"select 1 from {table} j join import_sources s on s.source_id=j.source_id where s.row_kind='table_record' group by j.source_id having count(*)>? limit 1", [limit]).fetchone():
            _invalid("自动表格日志总量超出限制")
    members = check.execute("""select m.source_id,m.member_path,m.role,m.sha256,m.size_bytes,m.encoding,m.row_count
        from import_members m join import_sources s on s.source_id=m.source_id
        where s.row_kind='table_record'""").fetchall()
    member_rows, by_source, xls_members = {}, {}, set()
    for source_id, path, role, digest, size, encoding, count in members:
        if (not isinstance(path, str) or not path or len(path) > 65535
                or "\\" in path or ":" in path
                or any(ord(char) < 32 or ord(char) == 127 for char in path)
                or any(part in ("", ".", "..") for part in path.split("/"))
                or role not in (("table", "auxiliary", "attachment") if source_profiles[source_id] in evidence_resolution.PROFILES else ("table", "auxiliary"))
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or size is None or not 0 <= size <= table_archive.MAX_MEMBER_BYTES
                or count is None or not 0 <= count <= table_import.MAX_RECORDS
                or encoding not in (("binary",) if role == "attachment" else ("utf-8-sig", "gb18030", "utf-16", "xlsx", "xls"))
                or encoding == "xls" and source_profiles[source_id] not in table_import.XLS_READER_PROFILES
                or source_profiles[source_id] == table_import.XLS_PROFILE and encoding != "xls"
                or encoding == "xls" and source_profiles[source_id] == table_import.XLS_EVIDENCE_ARCHIVE_PROFILE
                   and not re.search(r"\.xls/worksheets/[0-9]{4}$", path, re.IGNORECASE)):
            _invalid("自动表格成员元数据无效")
        if encoding == "xls":
            xls_members.add((source_id, path))
        member_rows[source_id, path] = (role, digest, count)
        by_source.setdefault(source_id, []).append((size, count))
    if set(by_source) != {row[0] for row in tables}:
        _invalid("自动表格来源与成员不一致")
    for source_id, values in by_source.items():
        modern = source_profiles[source_id] in evidence_resolution.PROFILES
        if (len(values) > (table_archive.EVIDENCE_MAX_MEMBERS if modern else table_archive.MAX_MEMBERS)
                or sum(size for size, _ in values) > (table_archive.EVIDENCE_MAX_EXPANDED_BYTES if modern else table_archive.MAX_EXPANDED_BYTES)
                or sum(count for _, count in values) > (table_archive.EVIDENCE_MAX_RECORDS if modern else table_import.MAX_RECORDS)):
            _invalid("自动表格成员总量超出限制")
    if check.execute("""select 1 from import_records r join import_sources s on s.source_id=r.source_id
            where s.row_kind='table_record' group by r.source_id,r.member_path,r.source_row
            having count(*)>1 limit 1""").fetchone():
        _invalid("自动表格成员记录位置重复")
    if check.execute("""select 1 from flow_sources f join import_sources s on s.source_id=f.source_id
            left join import_records r on r.source_id=f.source_id and r.record_no=f.source_row
            where s.row_kind='table_record' and (r.record_no is null
                or r.disposition not in ('imported','duplicate') or r.disposition<>f.disposition) limit 1""").fetchone():
        _invalid("自动表格流水来源与记录处置不一致")

    flow_fields = tuple(field.strip() for field in CASE_PKG_COLS["flows"].split(",")
                        if field.strip() not in ("id", "batch_id"))
    cursor = check.execute("""select r.source_id,r.record_no,r.member_path,r.source_row,r.record_key,
            r.disposition,r.reason_code,r.details_json,f.disposition,f.flow_id,"""
            + ",".join(f"v.{field}" for field in flow_fields) + """
        from import_records r join import_sources s on s.source_id=r.source_id
        left join flow_sources f on f.source_id=r.source_id and f.source_row=r.record_no
        left join flows v on v.id=f.flow_id where s.row_kind='table_record'
        order by r.source_id,r.record_no""")
    sequences, counts, evidence_records, known_facts, current_facts = {}, {}, {}, {}, {}
    header_regions, previous_source_rows = {}, {}
    accepted = ("imported", "duplicate")
    dispositions = (*accepted, "unresolved", "non_monetary", "non_transaction", "auxiliary", "removed")
    while rows := cursor.fetchmany(1024):
        for values in rows:
            (source_id, number, path, source_row, key, disposition, reason,
             details_json, linked_disposition, flow_id, *facts) = values
            member = member_rows.get((source_id, path))
            if member is None:
                _invalid("自动表格记录引用了不存在的成员")
            role, member_digest, row_count = member
            cancellation_rule = source_profiles[source_id] in table_archive.CANCELLATION_PROFILES
            reason_codes = table_import.REASON_CODES | (frozenset(table_cancellations.REASONS)
                                                        if cancellation_rule else frozenset())
            if source_profiles[source_id] in evidence_resolution.PROFILES:
                reason_codes |= frozenset(import_attachments.REASONS)
            if (source_id, path) not in xls_members:
                reason_codes -= {"invalid_cell_type"}
            record_dispositions = (*dispositions, *(('cancelled',) if cancellation_rule else ()))
            if (number is None or number != sequences.get(source_id, 0) + 1
                    or source_row is None or not 0 < source_row <= row_count + 1
                    or disposition not in record_dispositions
                    or reason not in (*reason_codes, "flow_deleted")):
                _invalid("自动表格记录位置或处置无效")
            sequences[source_id] = number
            counts[source_id, path] = counts.get((source_id, path), 0) + 1
            # The first header is the one omitted journal row; later headers
            # are retained with their own disposition. A sequence declaration
            # cannot move an entry into another table region during a replay.
            previous_row = previous_source_rows.get((source_id, path), 0)
            if role == "table" and source_row == previous_row + 2:
                header_regions[source_id, path] = source_row - 1
            if reason == "repeated_header":
                header_regions[source_id, path] = source_row
            previous_source_rows[source_id, path] = source_row
            if key != table_import.record_key(path, member_digest, source_row):
                _invalid("自动表格记录键与成员或位置不一致")
            if not isinstance(details_json, str) or len(details_json.encode("utf-8")) > 8192:
                _invalid("自动表格语义元数据过大或无效")
            details = json.loads(details_json)
            detail_fields = table_import.DETAILS_FIELDS
            contract_rule = source_profiles[source_id] in table_import.CONTRACT_PROFILES
            if contract_rule:
                detail_fields |= table_contracts.DETAILS_FIELDS
            if source_profiles[source_id] in table_archive.REFERENCE_PROFILES | table_import.SOURCE_ROLE_PROFILES:
                detail_fields |= table_import.REFERENCE_DETAILS_FIELDS
            if cancellation_rule:
                detail_fields |= table_cancellations.DETAILS_FIELDS
            if source_profiles[source_id] in evidence_resolution.PROFILES:
                detail_fields |= {evidence_resolution.DETAIL_FIELD, "occurrence_scope", "evidence_family", "family_member_sha256"} | import_attachments.DETAILS_FIELDS
            if source_profiles[source_id] in event_resolution.PROFILES:
                detail_fields |= {event_resolution.DETAIL_FIELD, event_resolution.LEDGER_FIELD}
            if (not isinstance(details, dict) or not set(details).issubset(detail_fields)
                    or any(not isinstance(value, str) or len(value.encode("utf-8")) > (
                        8192 if field == evidence_resolution.DETAIL_FIELD else 2048 if field == event_resolution.DETAIL_FIELD else 1024)
                           for field, value in details.items())):
                _invalid("自动表格语义元数据无效")
            if event_resolution.DETAIL_FIELD in details:
                event_resolution.validate_reference(details[event_resolution.DETAIL_FIELD])
                if role != "table" or disposition not in {"imported", "duplicate", "unresolved", "cancelled", "removed", "non_monetary"}:
                    _invalid("事件参考声明与来源处置不一致")
            if event_resolution.LEDGER_FIELD in details:
                ledger_evidence = event_resolution.validate_ledger(details[event_resolution.LEDGER_FIELD])
                if role != "table" or disposition not in {"imported", "duplicate", "cancelled", "removed"}:
                    _invalid("账本声明与来源处置不一致")
                if (ledger_evidence["posting_scope"] and
                        int(ledger_evidence["posting_scope"]) != header_regions.get((source_id, path))):
                    _invalid("记账顺序作用域与来源表头区域不一致")
            if source_profiles[source_id] in evidence_resolution.PROFILES and any(not isinstance(details.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", details[field]) for field in ("evidence_family", "family_member_sha256")):
                _invalid("身份证据逻辑来源指纹无效")
            if evidence_resolution.DETAIL_FIELD in details:
                evidence = evidence_resolution.validate_evidence(details[evidence_resolution.DETAIL_FIELD])
                expected_role = details.get("source_role") if role == "auxiliary" else "transaction"
                if evidence["role"] != expected_role:
                    _invalid("自动表格身份声明与来源角色不一致")
                if flow_id is not None:
                    original = dict(zip(flow_fields, facts))
                    for side, item in evidence.get("endpoints", {}).items():
                        if (item["account"] or item["card"]) != (original.get(side + "_account") or ""):
                            _invalid("身份声明与保留流水端点不一致")
            if contract_rule and role != "attachment" and not table_contracts.validate_details(details, disposition, reason):
                _invalid("自动表格来源契约元数据无效")
            if ("trade_no_basis" in details and details["trade_no_basis"] not in (
                    "source_transaction_id", "legacy_voucher_fallback", "none")):
                _invalid("自动表格交易编号依据无效")
            if "source_row" in details and details["source_row"] != str(source_row):
                _invalid("自动表格语义位置与来源记录不一致")
            effect = ("source_reported" if disposition in (*accepted, "removed") else
                      "analysis_excluded" if disposition == "cancelled" else
                      "undetermined" if disposition == "unresolved" else
                      "no_movement" if disposition == "non_monetary" else "not_a_flow")
            if details.get("financial_effect") != effect:
                _invalid("自动表格财务影响与处置不一致")
            if (disposition in accepted and reason != "transaction_valid"
                    or disposition == "removed" and reason != "flow_deleted"
                    or (disposition == "cancelled") != (reason == "matched_cancellation")
                    or disposition == "non_monetary" and reason != "zero_amount"
                    or disposition == "non_transaction" and reason not in (
                        "blank_record", "repeated_header", "feedback_only", "failed_transaction")
                    or disposition == "auxiliary" and reason not in ("preamble", "auxiliary_table", "non_business_attachment")
                    or disposition == "unresolved" and reason in (
                        "transaction_valid", "flow_deleted", "zero_amount", "blank_record",
                        "repeated_header", "preamble", "auxiliary_table", "feedback_only", "failed_transaction")):
                _invalid("自动表格原因与处置不一致")
            if (reason == "failed_transaction" and details.get("reported_status") != "explicit_failure"
                    or "reported_status" in details and details["reported_status"] not in (
                        "source_reported_success", "explicit_failure")
                    or details.get("reported_status") == "explicit_failure" and reason != "failed_transaction"):
                _invalid("自动表格交易状态与处置不一致")
            auxiliary_disposition = (disposition == "auxiliary" and reason == "auxiliary_table"
                                     or disposition == "unresolved" and reason in ("row_width", "column_count_mismatch"))
            if role == "attachment":
                expected = "non_business_attachment" if details.get("attachment_kind") == "finder_metadata" else "unsupported_attachment"
                if source_profiles[source_id] not in evidence_resolution.PROFILES or details.get("source_role") != "attachment" or reason != expected or disposition != ("auxiliary" if expected == "non_business_attachment" else "unresolved"):
                    _invalid("附件处置与保全记录不一致")
            if (role == "auxiliary" and not auxiliary_disposition
                    or role == "table" and reason == "auxiliary_table"
                    or role == "auxiliary" and details.get("source_role") not in (
                        "account", "client", "manifest", "subaccount", "freeze")
                    or role not in ("auxiliary", "attachment") and "source_role" in details):
                _invalid("自动表格辅助来源角色不一致")
            issues = details.get("validation_issues")
            if (not contract_rule and (reason in table_contracts.VALIDATION_ISSUES
                    or table_contracts.VALIDATION_ISSUES.intersection((issues or "").split(",")))):
                _invalid("自动表格余额契约不属于该解析版本")
            if (details.get("counterparty_basis") == table_import.ROLE_CONFLICT_BASIS
                    and (source_profiles[source_id] not in table_import.ROLE_PROFILES or disposition != "unresolved"
                         or "conflicting_field" not in (issues or "").split(","))):
                _invalid("自动表格身份冲突标记无效")
            if (details.get("counterparty_basis") == table_import.HISTORY_CONFLICT_BASIS
                    and (source_profiles[source_id] not in table_import.SUPPORTED_PROFILES or disposition != "unresolved"
                         or "conflicting_field" not in (issues or "").split(","))):
                _invalid("自动表格历史冲突标记无效")
            if (details.get("counterparty_basis") == table_import.ARCHIVE_HISTORY_CONFLICT_BASIS
                    and (source_profiles[source_id] not in table_import.SUPPORTED_PROFILES | table_archive.SUPPORTED_PROFILES
                         or disposition != "unresolved" or reason != "conflicting_field"
                         or "conflicting_field" not in (issues or "").split(","))):
                _invalid("归档参与的历史冲突标记无效")
            if ("validation_basis" in details
                    and (details["validation_basis"] != table_import.FINANCIAL_CONFLICT_BASIS
                         or source_profiles[source_id] not in table_import.FINANCIAL_PROFILES or disposition != "unresolved"
                         or not table_import.FINANCIAL_VALIDATION_ISSUES.intersection((issues or "").split(",")))):
                _invalid("自动表格资金校验冲突标记无效")
            cancelled_negative = (disposition == "cancelled"
                                  and details.get("cancellation_role") == "cancellation"
                                  and issues == "negative_amount")
            if issues is not None and not cancelled_negative and (disposition != "unresolved" or not issues
                    or issues.split(",")[0] != reason
                    or any(issue not in reason_codes for issue in issues.split(","))):
                _invalid("自动表格未确定原因无效")
            digest = details.get("flow_sha256")
            cancelled_original = disposition == "cancelled" and details.get("cancellation_role") == "original"
            if disposition in (*accepted, "removed") or cancelled_original:
                if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                    _invalid("自动表格交易缺少规范事实摘要")
            elif digest is not None:
                _invalid("非流水记录不能声明规范交易摘要")
            if disposition in accepted:
                flow = dict(zip(flow_fields, facts))
                if (linked_disposition != disposition or flow_id is None
                        or flow["amount"] is None or flow["amount"] <= 0
                        or flow["trade_time"] is None
                        or digest != table_import.flow_digest(flow)):
                    _invalid("自动表格流水与来源规范事实不一致")
                if has_cancellation_profile:
                    known_facts.setdefault(key, []).append((source_id, flow))
                if cancellation_rule:
                    current_facts[source_id, number] = flow
            elif flow_id is not None:
                _invalid("非有效交易记录不能关联分析流水")
            if cancellation_rule:
                evidence_records.setdefault(source_id, []).append({
                    "record_no": number, "member_path": path, "source_row": source_row,
                    "record_key": key, "disposition": disposition,
                    "reason_code": reason, "details": details,
                })
    if any(counts.get(reference, 0) != values[2] for reference, values in member_rows.items()):
        _invalid("自动表格成员记录数不闭合")
    for source_id, records in evidence_records.items():
        try:
            table_cancellations.validate_evidence(records)
        except ValueError:
            _invalid("自动表格成对效果排除证据不闭合")
        for record in records:
            details = record["details"]
            if details.get("cancellation_policy") != table_cancellations.POLICY:
                continue
            if record["disposition"] == "cancelled":
                actual = []
                for previous_source, flow in known_facts.get(record["record_key"], ()):
                    previous_time, proof_time = source_times[previous_source], source_times[source_id]
                    later = (previous_time is not None and proof_time is not None
                             and previous_time > proof_time)
                    if details.get("cancellation_role") == "cancellation":
                        if not later:
                            _invalid("自动表格负向排除与保留历史流水冲突")
                        continue
                    matching = (details["flow_sha256"] == table_import.flow_digest(flow)
                                and details["cancellation_own_account_sha256"] == table_cancellations.hash_value(flow.get("self_account") or "")
                                and details["cancellation_own_name_sha256"] == table_cancellations.hash_value(flow.get("self_name") or ""))
                    # A later source can disclose a competing interpretation.
                    # Preserve that history: coverage reports its conflict and
                    # effective_flows only excludes facts matching this proof.
                    # Equal/missing operation times cannot establish that order.
                    if not later or matching:
                        actual.append(flow)
            else:
                # Witnesses and other observations describe this assessment's
                # linked flow. Other sources may retain different mappings of
                # that physical row without changing this proof's facts.
                linked = current_facts.get((source_id, record["record_no"]))
                actual = (linked,) if linked is not None else ()
            for flow in actual:
                _table_cancellation_flow(details, flow, table_cancellations)


def _table_cancellation_flow(details, flow, policy):
    """Bind slim source evidence to an actual flow authorized by its record role.

    Without a retained flow the original digest remains a historical commitment;
    a case package cannot establish the authenticity of an omitted source file.
    """
    from . import table_import

    own_account, own_name = flow.get("self_account") or "", flow.get("self_name") or ""
    account_hash = policy.hash_value(own_account)
    actual_time = flow["trade_time"].strftime("%Y-%m-%d %H:%M:%S.%f")
    primary_reference = (policy.hash_value(flow.get("trade_no") or "")
                         if details.get("trade_no_basis") == "source_transaction_id" else "")
    account_columns = ((details["cancellation_account_column"],)
                       if "cancellation_account_column" in details else policy.ACCOUNT_COLUMNS)
    actual_scopes = {policy.scope_digest(column, account_hash, flow["currency"])
                     for column in account_columns}
    if (details["cancellation_time"] != actual_time
            or details["cancellation_movement"] != "positive"
            or details["cancellation_scope_sha256"] not in actual_scopes
            or details["cancellation_reference_sha256"] != primary_reference):
        _invalid("自动表格成员观察与保留流水事实不一致")
    if "cancellation_amount" not in details:
        return
    direction = details["cancellation_direction"]
    side = "payer" if direction == "out" else "payee"
    if (details.get("flow_sha256") != table_import.flow_digest(flow)
            or _feedback_amount(details, "cancellation_amount") != flow["amount"]
            or _feedback_amount(details, "cancellation_balance") != flow["balance"]
            or details["cancellation_currency"] != flow["currency"]
            or direction not in ("in", "out")
            or own_account != (flow.get(f"{side}_account") or "")
            or own_name != (flow.get(f"{side}_name") or "")
            or details["cancellation_own_account_sha256"] != account_hash
            or details["cancellation_own_name_sha256"] != policy.hash_value(own_name)):
        _invalid("自动表格成对效果排除与保留流水事实不一致")


def _validate_package(content, directory):
    """Validate and canonicalize every input before opening the database transaction."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if len(set(names)) != len(names):
                _invalid("ZIP 中有重复文件")
            allowed = {"meta.json", *(f"{t}.parquet" for t in _ALL_PKG_COLS)}
            for item in infos:
                name = item.filename
                if name not in allowed and not (name.startswith("photos/") and photo_name(name[7:])):
                    _invalid("包含格式之外的文件或路径")
                if item.is_dir() or item.flag_bits & 1 or (item.external_attr >> 16) & 0o170000 == 0o120000:
                    _invalid("不支持目录、加密文件或符号链接")
            if not {"meta.json", "flows.parquet"}.issubset(names):
                _invalid("缺少 meta.json / flows.parquet")
            meta = json.loads(archive.read("meta.json").decode("utf-8"))
            if not isinstance(meta, dict) or type(meta.get("format")) is not int or meta["format"] not in (1, 2, 3, 4, 5):
                _invalid("不支持的格式版本")
            source_members = {f"{table}.parquet" for table in PROVENANCE_PKG_COLS} & set(names)
            if source_members and (meta["format"] not in (2, 3, 4, 5) or len(source_members) != 2):
                _invalid("来源追溯表必须在格式 2、3、4 中成对提供")
            journal_members = {f"{table}.parquet" for table in JOURNAL_PKG_COLS} & set(names)
            if journal_members and (len(journal_members) != 2 or len(source_members) != 2):
                _invalid("反馈包日志须与来源追溯表成对提供")
            if meta["format"] in (2, 3, 4, 5) and meta.get("source_files") != "not_included":
                _invalid("来源文件策略无效")
            if not isinstance(meta.get("name"), str) or not meta["name"].strip():
                _invalid("案件名无效")
            paths, photos = {}, {}
            for name in names:
                if name.endswith(".parquet"):
                    table = name[:-8]
                    path = directory / name
                    path.write_bytes(archive.read(name))
                    paths[table] = path
                elif name.startswith("photos/"):
                    photos[name[7:]] = archive.read(name)
    except (zipfile.BadZipFile, UnicodeError, json.JSONDecodeError, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        _invalid(f"ZIP 或元数据损坏（{type(exc).__name__}）")

    try:
        with duckdb.connect(config={"threads": 1}) as check:
            for table, path in paths.items():
                cols = [c.strip() for c in _ALL_PKG_COLS[table].split(",")]
                actual = [r[0] for r in check.execute("describe select * from read_parquet(?)", [str(path)]).fetchall()]
                if len(actual) != len(cols) or set(actual) != set(cols):
                    _invalid(f"{table} 列不符合格式")
                _check_integer_columns(check, path, table, cols)
                if table in (*PROVENANCE_PKG_COLS, *JOURNAL_PKG_COLS):
                    integral_fields = tuple(field for field in cols if _TYPES.get(field) == "bigint")
                    for field in integral_fields:
                        if check.execute(f"select 1 from read_parquet(?) where {field} is null or not regexp_full_match(cast({field} as varchar),'[0-9]+') limit 1", [str(path)]).fetchone():
                            _invalid(f"{table} 整数字段无效")
                casts = ",".join(f"cast({c} as {_TYPES.get(c, 'varchar')}) as {c}" for c in cols)
                check.execute(f"create table {table} as select {casts} from read_parquet(?)", [str(path)])
                key = ("id" if table in ("flows", "batches", "canvases")
                       else "flow_id,canvas_id" if table == "tracked_flows"
                       else "source_id" if table in ("import_sources", "evidence_sources")
                       else "source_id,source_row" if table == "flow_sources"
                       else "source_id,member_path" if table == "import_members"
                       else "source_id,record_no" if table == "import_records" else "account")
                if check.execute(f"select 1 from {table} group by {key} having count(*) > 1 limit 1").fetchone():
                    _invalid(f"{table} 标识重复")
                required = key.split(",")
                if check.execute(f"select 1 from {table} where " + " or ".join(f"{c} is null" for c in required) + " limit 1").fetchone():
                    _invalid(f"{table} 标识为空")
                if "id" in required and check.execute(f"select 1 from {table} where id <= 0 limit 1").fetchone():
                    _invalid(f"{table} 标识无效")
            if "import_sources" in paths:
                for record in check.execute("select source_id, filename, sha256, size_bytes, mode, mapping_json, sheet, parser_version, row_kind, imported_at from import_sources").fetchall():
                    source_id, filename, sha256, size_bytes, mode, mapping_json, sheet, parser_version, row_kind, imported_at = record
                    mapping = json.loads(mapping_json) if isinstance(mapping_json, str) else None
                    suffix = Path(filename).suffix.lower() if isinstance(filename, str) else ""
                    from . import table_import, table_archive
                    table_source = (row_kind == "table_record" or parser_version in (
                        *table_import.SUPPORTED_PROFILES, *table_archive.SUPPORTED_PROFILES))
                    archive_source = (suffix == ".zip" or row_kind == "archive_record"
                                      or isinstance(parser_version, str) and parser_version.startswith("funds-trace-bank-feedback-"))
                    if table_source:
                        # Uploads already default an empty suffix to CSV while
                        # preserving the original name. Admit only the exact
                        # single-text-member contract; full journal and fact
                        # validation below still applies.
                        suffixless_text = (row_kind == "table_record" and suffix == "" and sheet == ""
                                           and parser_version in table_import.SUPPORTED_PROFILES
                                           and parser_version != table_import.XLS_PROFILE
                                           and set(JOURNAL_PKG_COLS).issubset(paths))
                        if suffixless_text:
                            text_members = check.execute("""select member_path,role,sha256,size_bytes,encoding
                                from import_members where source_id=?""", [source_id]).fetchall()
                            suffixless_text = (len(text_members) == 1
                                               and text_members[0][0] == "table.csv"
                                               and text_members[0][1] in ("table", "auxiliary")
                                               and text_members[0][2] == sha256
                                               and text_members[0][3] == size_bytes
                                               and text_members[0][4] in ("utf-8-sig", "gb18030", "utf-16"))
                        source_format_valid = (row_kind == "table_record"
                                               and ((suffix == ".zip" and parser_version in table_archive.SUPPORTED_PROFILES and sheet == "")
                                                    or (suffix in (".csv", ".tsv", ".txt", ".xlsx", ".xls")
                                                        and parser_version in table_import.SUPPORTED_PROFILES
                                                        and (parser_version != table_import.XLS_PROFILE or suffix == ".xls")
                                                        and (suffix in (".xlsx", ".xls")) == bool(sheet))
                                                    or suffixless_text))
                    elif archive_source:
                        source_format_valid = (suffix == ".zip" and row_kind == "archive_record"
                                               and parser_version in feedback_import.SUPPORTED_PROFILES
                                               and mode == "B" and mapping == feedback_import.MAPPING
                                               and sheet == "")
                    else:
                        source_format_valid = (suffix in (".csv", ".tsv", ".txt", ".xlsx", ".xls")
                                               and (suffix in (".xlsx", ".xls")) == bool(sheet)
                                               and row_kind in ("csv_record", "worksheet_row", "unknown")
                                               and not (row_kind == "worksheet_row" and suffix not in (".xlsx", ".xls"))
                                               and not (row_kind == "csv_record" and suffix in (".xlsx", ".xls")))
                    if (not isinstance(source_id, str) or not re.fullmatch(r"[0-9a-f]{12,32}", source_id)
                            or not isinstance(filename, str) or not filename.strip()
                            or not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256)
                            or size_bytes is None or size_bytes < 0 or mode not in ("A", "B")
                            or not isinstance(mapping, dict)
                            or any(k not in (table_import.mapping_fields(parser_version) if table_source else
                                           (*importer.MODE_FIELDS.get(mode, []), "_time2", "self_account", "self_name"))
                                   or not isinstance(v, str) or not v.strip() for k, v in mapping.items())
                            or (not table_source and not importer._validate(mapping, mode))
                            or not isinstance(sheet, str) or not isinstance(parser_version, str) or not parser_version
                            or not source_format_valid
                            or imported_at is None):
                        _invalid("来源元数据无效")
                if check.execute("select 1 from flow_sources s left join import_sources p on s.source_id=p.source_id left join flows f on s.flow_id=f.id where p.source_id is null or f.id is null or s.source_row<=0 or s.source_row is null or s.disposition is null or s.disposition not in ('imported','duplicate') limit 1").fetchone():
                    _invalid("来源行或流水引用无效")
            cancellation_profiles = sorted(feedback_import.CANCELLATION_PROFILES)
            cancellation_supported = ",".join("?" for _ in cancellation_profiles)
            has_cancellations = bool(check.execute(
                f"select 1 from import_sources where parser_version in ({cancellation_supported}) limit 1",
                cancellation_profiles).fetchone()) if "import_sources" in paths else False
            has_tables = bool(check.execute("select 1 from import_sources where row_kind='table_record' limit 1").fetchone()) if "import_sources" in paths else False
            resolution_supported = ",".join("?" for _ in evidence_resolution.PROFILES)
            has_resolution = bool(check.execute(f"select 1 from import_sources where parser_version in ({resolution_supported}) limit 1", sorted(evidence_resolution.PROFILES)).fetchone()) if "import_sources" in paths else False
            if has_resolution:
                if "evidence_sources" not in paths:
                    _invalid("身份证据来源缺少导入操作关联")
                if check.execute("""select 1 from evidence_sources e left join import_sources s on s.source_id=e.source_id
                      left join batches b on b.id=e.batch_id
                      where s.source_id is null or e.active is null or (e.active and b.id is null) limit 1""").fetchone():
                    _invalid("身份证据导入操作引用无效")
                profiles = sorted(evidence_resolution.PROFILES)
                if check.execute(f"select 1 from import_sources s left join evidence_sources e on e.source_id=s.source_id where s.parser_version in ({resolution_supported}) and e.source_id is null limit 1", profiles).fetchone():
                    _invalid("身份证据来源未关联导入操作")
            elif "evidence_sources" in paths:
                _invalid("历史格式不能声明身份证据操作")
            if has_resolution != (meta["format"] == 5):
                _invalid("身份证据规则须使用格式5保留完整语义")
            if has_tables != (meta["format"] == 4 or meta["format"] == 5 and has_tables):
                _invalid("自动表格记录须使用格式4保留完整语义")
            if not has_tables and not has_resolution and has_cancellations != (meta["format"] == 3):
                _invalid("配对冲正规则须使用格式3保留完整语义")
            if "import_sources" in paths:
                for table in JOURNAL_PKG_COLS:
                    if table in paths and check.execute(f"select 1 from {table} j left join import_sources s on s.source_id=j.source_id where s.source_id is null or s.row_kind not in ('archive_record','table_record') limit 1").fetchone():
                        _invalid("记录日志引用了不存在或不支持的来源")
                _feedback_journals(check, paths)
                _table_journals(check, paths)
            if "batches" in paths and check.execute("select 1 from flows f left join batches b on f.batch_id=b.id where f.batch_id is not null and b.id is null limit 1").fetchone():
                _invalid("流水引用了不存在的批次")
            if "tracked_flows" in paths:
                if check.execute("select 1 from tracked_flows t left join flows f on t.flow_id=f.id where f.id is null limit 1").fetchone():
                    _invalid("追踪引用了不存在的流水")
                if "canvases" in paths:
                    bad_canvas = check.execute("select 1 from tracked_flows t left join canvases c on t.canvas_id=c.id where t.canvas_id<>0 and c.id is null limit 1").fetchone()
                else:
                    bad_canvas = check.execute("select 1 from tracked_flows where canvas_id<>0 limit 1").fetchone()
                if bad_canvas:
                    _invalid("追踪引用了不存在的画布")
            if "canvases" in paths:
                flow_ids = {row[0] for row in check.execute("select id from flows").fetchall()}
                for canvas_id, snapshot in check.execute("select id,snapshot from canvases").fetchall():
                    checked = _canvas_snapshot(snapshot, flow_ids)
                    check.execute("update canvases set snapshot=? where id=?", [checked, canvas_id])
            referenced = set()
            if "account_photos" in paths:
                for (name,) in check.execute("select fname from account_photos").fetchall():
                    if not photo_name(name):
                        _invalid("照片文件名无效")
                    referenced.add(name)
            if referenced != set(photos):
                _invalid("照片文件与账号照片记录不一致")
            # Import only the checked values; no later parse can bypass validation.
            for table in paths:
                path = directory / f"validated-{table}.parquet"
                check.table(table).write_parquet(str(path))
                paths[table] = path
    except (duckdb.Error, ValueError, TypeError, OverflowError) as exc:
        _invalid(f"Parquet 数据损坏或字段无效（{type(exc).__name__}）")
    return meta["name"].strip(), paths, photos


def import_package(content):
    with tempfile.TemporaryDirectory(prefix="fundcase-import-") as tmp:
        name, paths, photos = _validate_package(content, Path(tmp))
        photo_root = db.DATA_DIR / "photos"
        photo_root.mkdir(parents=True, exist_ok=True)
        # Stage on the destination filesystem; directory publication is one rename.
        with tempfile.TemporaryDirectory(prefix=".fundcase-", dir=photo_root) as stage:
            staged = Path(stage) / "photos"
            staged.mkdir()
            for filename, data in photos.items():
                (staged / filename).write_bytes(data)
            destination = None
            moved = False
            try:
                with db.transaction() as con:
                    existing = {r["name"] for r in db.q("select name from cases")}
                    final_name, suffix = name, 1
                    while final_name in existing:
                        final_name = f"{name}(导入{suffix})"
                        suffix += 1
                    new_cid = con.execute("insert into cases(name) values (?) returning id", [final_name]).fetchone()[0]
                    prefix = "pkg_" + uuid.uuid4().hex
                    mappings = {}
                    for table, sequence in (("batches", "batch_seq"), ("canvases", "canvas_seq"), ("flows", "flow_seq")):
                        mapping = f"{prefix}_{table}"
                        mappings[table] = mapping
                        if table in paths:
                            con.execute(f"create temp table {mapping} as select id old_id, nextval('{sequence}') new_id from read_parquet(?)", [str(paths[table])])
                        else:
                            con.execute(f"create temp table {mapping}(old_id bigint, new_id bigint)")
                    for table in ("batches", "canvases"):
                        if table in paths:
                            if table == "canvases":
                                flow_mapping = dict(con.execute(f"select old_id,new_id from {mappings['flows']}").fetchall())
                                rows = con.execute(f"select m.new_id,p.name,p.snapshot,p.updated_at from read_parquet(?) p join {mappings[table]} m on p.id=m.old_id", [str(paths[table])]).fetchall()
                                for canvas_id, canvas_name, snapshot, updated_at in rows:
                                    remapped = _canvas_snapshot(snapshot, flow_mapping, flow_mapping)
                                    con.execute("insert into canvases(id,case_id,name,snapshot,updated_at) values (?,?,?,?,?)", [canvas_id, new_cid, canvas_name, remapped, updated_at])
                                continue
                            cols = [c.strip() for c in CASE_PKG_COLS[table].split(",") if c.strip() != "id"]
                            con.execute(f"insert into {table}(id,case_id,{','.join(cols)}) select m.new_id,?,{','.join('p.'+c for c in cols)} from read_parquet(?) p join {mappings[table]} m on p.id=m.old_id", [new_cid, str(paths[table])])
                    cols = [c.strip() for c in CASE_PKG_COLS["flows"].split(",") if c.strip() not in ("id", "batch_id")]
                    con.execute(f"insert into flows(id,case_id,batch_id,{','.join(cols)}) select m.new_id,?,b.new_id,{','.join('p.'+c for c in cols)} from read_parquet(?) p join {mappings['flows']} m on p.id=m.old_id left join {mappings['batches']} b on p.batch_id=b.old_id", [new_cid, str(paths["flows"])])
                    if "tracked_flows" in paths:
                        con.execute(f"insert into tracked_flows(case_id,flow_id,canvas_id,tracked_at) select ?,f.new_id,case when p.canvas_id=0 then 0 else c.new_id end,p.tracked_at from read_parquet(?) p join {mappings['flows']} f on p.flow_id=f.old_id left join {mappings['canvases']} c on p.canvas_id=c.old_id", [new_cid, str(paths["tracked_flows"])])
                    for table in ("main_accounts", "account_ids", "account_photos", "focus_nodes"):
                        if table in paths:
                            cols = CASE_PKG_COLS[table]
                            con.execute(f"insert into {table}(case_id,{cols}) select ?,{cols} from read_parquet(?)", [new_cid, str(paths[table])])
                    if "import_sources" in paths:
                        mapping = f"{prefix}_sources"
                        mappings["import_sources"] = mapping
                        con.execute(f"create temp table {mapping} as select source_id old_id, replace(cast(uuid() as varchar),'-','') new_id from read_parquet(?)", [str(paths["import_sources"])])
                        cols = [c.strip() for c in PROVENANCE_PKG_COLS["import_sources"].split(",") if c.strip() != "source_id"]
                        con.execute(f"insert into import_sources(source_id,case_id,{','.join(cols)},origin) select m.new_id,?,{','.join('p.'+c for c in cols)},'package' from read_parquet(?) p join {mapping} m on p.source_id=m.old_id", [new_cid, str(paths["import_sources"])])
                        con.execute(f"insert into flow_sources(case_id,source_id,source_row,flow_id,disposition) select ?,s.new_id,p.source_row,f.new_id,p.disposition from read_parquet(?) p join {mapping} s on p.source_id=s.old_id join {mappings['flows']} f on p.flow_id=f.old_id", [new_cid, str(paths["flow_sources"])])
                        for table, fields in JOURNAL_PKG_COLS.items():
                            if table in paths:
                                cols = [field.strip() for field in fields.split(",") if field.strip() != "source_id"]
                                con.execute(f"insert into {table}(case_id,source_id,{','.join(cols)}) select ?,s.new_id,{','.join('p.'+field for field in cols)} from read_parquet(?) p join {mapping} s on p.source_id=s.old_id", [new_cid, str(paths[table])])
                    if "evidence_sources" in paths:
                        con.execute(f"""insert into evidence_sources(case_id,source_id,batch_id,active)
                            select ?,s.new_id,b.new_id,p.active from read_parquet(?) p
                            join {mappings['import_sources']} s on s.old_id=p.source_id
                            left join {mappings['batches']} b on b.old_id=p.batch_id""", [new_cid,str(paths["evidence_sources"])])
                    if "import_sources" in paths:
                        evidence_resolution.rebuild(new_cid)
                    event_resolution.rebuild(new_cid)
                    from . import analysis_snapshot
                    analysis_snapshot.invalidate(new_cid)
                    for mapping in mappings.values():
                        con.execute(f"drop table {mapping}")
                    destination = photo_root / f"c{new_cid}"
                    if destination.exists():
                        raise OSError("新案件照片目录已存在")
                    if photos:
                        os.replace(staged, destination)
                        moved = True
                    count = con.execute("select count(*) from flows where case_id=?", [new_cid]).fetchone()[0]
                return {"ok": True, "case_id": new_cid, "name": final_name, "flows": count}
            except BaseException:
                if moved:
                    os.replace(destination, staged)
                raise


def delete_package_case(case_id):
    if case_id == 1:
        raise HTTPException(400, "默认案件不可删除")
    # Keep a restorable copy until database commit. Failed file removal rolls back
    # every table, and failed database commit restores the original photo bytes.
    with db._lock, tempfile.TemporaryDirectory(prefix="fundcase-delete-") as tmp:
        backup = Path(tmp) / "photos"
        photo_dir = db.DATA_DIR / "photos" / f"c{case_id}"
        had_photos = False
        try:
            with db.transaction() as con:
                require_case(case_id)
                if photo_dir.exists():
                    shutil.copytree(photo_dir, backup)
                    had_photos = True
                    shutil.rmtree(photo_dir)
                deleted = con.execute("select count(*) from flows where case_id=?", [case_id]).fetchone()[0]
                con.execute("insert into import_file_gc(import_id,suffix,sha256,delete_after) select import_id,suffix,sha256,current_timestamp at time zone 'UTC' from import_sessions where case_id=? and source_state='retained' on conflict(import_id) do nothing", [case_id])
                for table in ("analysis_state", "event_projection_cache_state", "event_observation_cache", "event_state", "resolved_events", "event_members", "evidence_sources", "resolution_state", "resolution_revisions", "resolution_entities", "resolved_endpoints", "resolution_subject_aliases", "import_records", "import_members", "flow_sources", "import_sources", "import_sessions"):
                    con.execute(f"delete from {table} where case_id=?", [case_id])
                for table in CASE_PKG_TABLES:
                    con.execute(f"delete from {table} where case_id=?", [case_id])
                con.execute("delete from cases where id=?", [case_id])
        except BaseException:
            if had_photos:
                shutil.copytree(backup, photo_dir, dirs_exist_ok=True)
            raise
    return {"ok": True, "deleted_flows": deleted}
