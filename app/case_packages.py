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

from . import db, feedback_import, importer, evidence_resolution, import_attachments, event_resolution, table_direction_recheck, table_tiered_headers

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
ZONED_FLOW_COLS = CASE_PKG_COLS["flows"] + ", trade_time_utc, trade_time_original, trade_time_offset"
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
OPERATION_PKG_COLS = {"import_source_operations": "source_id, batch_id, active"}
_ALL_PKG_COLS = {**CASE_PKG_COLS, **PROVENANCE_PKG_COLS, **JOURNAL_PKG_COLS, **EVIDENCE_PKG_COLS, **OPERATION_PKG_COLS}
_PHOTO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.(?:jpg|jpeg|png|webp|bmp)", re.I)
_TYPES = {
    "id": "bigint", "flow_id": "bigint", "batch_id": "bigint", "canvas_id": "bigint",
    "trade_time_utc": "timestamp", "trade_time": "timestamp", "tracked_at": "timestamp", "imported_at": "timestamp",
    "updated_at": "timestamp", "marked_at": "timestamp", "amount": "decimal(18,2)",
    "balance": "decimal(18,2)", "rows_total": "bigint", "rows_imported": "bigint",
    "rows_skipped": "bigint", "rows_dup": "bigint", "crop_cx": "double",
    "crop_cy": "double", "crop_r": "double", "scale": "double",
    "active": "boolean", "source_row": "bigint", "size_bytes": "bigint", "record_no": "bigint", "row_count": "bigint",
}
_BIGINT_MIN, _BIGINT_MAX = -(2**63), 2**63 - 1

# Package budgets do not select a parser version or authorize table truncation.
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_PACKAGE_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAX_PACKAGE_MEMBER_BYTES = 512 * 1024 * 1024
MAX_PACKAGE_META_BYTES = 1024 * 1024
MAX_PACKAGE_MEMBERS = 10_000
MAX_PACKAGE_ROWS = 10_000_000


def photo_name(value):
    """A photo name is a portable basename, never a path supplied by an archive."""
    return isinstance(value, str) and bool(_PHOTO_NAME.fullmatch(value)) and ".." not in value


def require_case(case_id):
    row = db.q("select id, name from cases where id = ?", [case_id])
    if not row:
        raise HTTPException(404, "案件不存在")
    return row[0]


def _parquet_row_count(con, path):
    return con.execute("select num_rows from parquet_file_metadata(?)", [str(path)]).fetchone()[0]


def _require_export_budget(within_limit, detail):
    if not within_limit:
        raise HTTPException(409, f"{detail}，无法生成可导入的案件包")


def export_package(case_id):
    # The lock spans every table and photo read, so writers cannot split the snapshot.
    with tempfile.TemporaryDirectory(prefix="fundcase-export-") as tmp, db.transaction() as con:
        case_name = require_case(case_id)["name"]
        has_zoned = bool(con.execute("select 1 from flows where case_id=? and trade_time_utc is not null limit 1", [case_id]).fetchone())
        buf = io.BytesIO()
        cancellation_profiles = sorted(feedback_import.CANCELLATION_PROFILES)
        cancellation_supported = ",".join("?" for _ in cancellation_profiles)
        has_cancellation_rules = bool(con.execute(f"""select 1 from import_sources where case_id=?
                  and parser_version in ({cancellation_supported}) limit 1""", [case_id, *cancellation_profiles]).fetchone())
        has_table_records = bool(con.execute("""select 1 from import_sources where case_id=?
                  and row_kind='table_record' limit 1""", [case_id]).fetchone())
        has_resolution = bool(con.execute("select 1 from import_sources where case_id=? and parser_version in (" + ",".join("?" for _ in evidence_resolution.PROFILES) + ") limit 1", [case_id, *sorted(evidence_resolution.PROFILES)]).fetchone())
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            has_operations = bool(con.execute("select 1 from import_source_operations where case_id=? limit 1", [case_id]).fetchone())
            metadata={
                "name": case_name, "exported_at": datetime.datetime.now().isoformat(),
                "format": 7 if has_zoned else 6 if has_operations else 5 if has_resolution else 4 if has_table_records else 3 if has_cancellation_rules else 2,
                "source_files": "not_included",
            }
            from .services import materials
            material_metadata=materials.package_metadata(case_id)
            if material_metadata is not None:
                metadata['materials']=material_metadata
            encoded_meta=json.dumps(metadata,ensure_ascii=False,separators=(',',':')).encode('utf-8')
            if len(encoded_meta)>MAX_PACKAGE_META_BYTES:
                raise HTTPException(409,'检材登记元数据超出案件包处理范围，无法生成完整案件包')
            expanded_bytes = len(encoded_meta)
            archive.writestr("meta.json",encoded_meta)

            def write_member(path, name):
                nonlocal expanded_bytes
                size = path.stat().st_size
                _require_export_budget(size <= MAX_PACKAGE_MEMBER_BYTES, "ZIP 成员大小超出处理范围")
                expanded_bytes += size
                _require_export_budget(len(archive.infolist()) < MAX_PACKAGE_MEMBERS, "ZIP 成员数量超出处理范围")
                _require_export_budget(expanded_bytes <= MAX_PACKAGE_EXPANDED_BYTES, "ZIP 展开大小超出处理范围")
                with path.open("rb") as source, archive.open(name, "w") as target:
                    shutil.copyfileobj(source, target, 1024 * 1024)
                _require_export_budget(buf.tell() <= MAX_PACKAGE_BYTES, "压缩文件不能超过 512MiB")

            has_journals = any(con.execute(f"select 1 from {table} where case_id=? limit 1", [case_id]).fetchone()
                               for table in JOURNAL_PKG_COLS)
            rows = 0
            for table, cols in _ALL_PKG_COLS.items():
                if table == "flows" and has_zoned:
                    cols = ZONED_FLOW_COLS
                if table in OPERATION_PKG_COLS and not has_operations:
                    continue
                if table in EVIDENCE_PKG_COLS and not has_resolution:
                    continue
                if table in JOURNAL_PKG_COLS and not has_journals:
                    continue
                path = Path(tmp) / f"{table}.parquet"
                con.sql(f"select {cols} from {table} where case_id = ?", params=[case_id]).write_parquet(str(path))
                rows += _parquet_row_count(con, path)
                _require_export_budget(rows <= MAX_PACKAGE_ROWS, "Parquet 总行数超出处理范围")
                write_member(path, path.name)
            written = set()
            for row in db.q("select fname from account_photos where case_id = ?", [case_id]):
                name = row["fname"]
                if not photo_name(name):
                    raise HTTPException(409, "案件照片文件名无效，无法生成完整案件包")
                path = db.DATA_DIR / "photos" / f"c{case_id}" / name
                if not path.is_file() or path.is_symlink():
                    raise HTTPException(409, "案件照片文件缺失，无法生成完整案件包")
                if name not in written:
                    write_member(path, f"photos/{name}")
                    written.add(name)
        # The central directory and end record also count toward the input limit.
        _require_export_budget(buf.tell() <= MAX_PACKAGE_BYTES, "压缩文件不能超过 512MiB")
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
    from . import feedback_import, source_transaction_status

    archives = dict(check.execute(
        "select source_id,parser_version from import_sources where row_kind='archive_record'").fetchall())
    if "import_members" not in paths:
        if archives:
            _invalid("反馈包来源缺少成员与记录日志")
        return
    for table in ("import_members", "import_records"):
        totals = check.execute(f"select source_id,count(*) from {table} where source_id in (select source_id from import_sources where row_kind='archive_record') group by source_id").fetchall()
        if any(count > ((feedback_import.EVIDENCE_MAX_MEMBERS if archives[source_id] in feedback_import.EVIDENCE_PROFILES else feedback_import.MAX_MEMBERS) if table == "import_members"
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
                or size is None or not 0 <= size <= (feedback_import.EVIDENCE_MAX_MEMBER_BYTES if archives[source_id] in feedback_import.EVIDENCE_PROFILES else feedback_import.MAX_MEMBER_BYTES)
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
        if (len(values) > (feedback_import.EVIDENCE_MAX_MEMBERS if archives[source_id] in feedback_import.EVIDENCE_PROFILES else feedback_import.MAX_MEMBERS)
                or sum(value[1] for value in values) > (feedback_import.EVIDENCE_MAX_EXPANDED_BYTES if archives[source_id] in feedback_import.EVIDENCE_PROFILES else feedback_import.MAX_EXPANDED_BYTES)
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
                or (disposition == "non_transaction" and reason not in {"feedback_only", "failed_transaction"})
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
        literal_status_policy = source_transaction_status.DETAIL_FIELD in details
        if literal_status_policy:
            status = details.get("reported_status")
            status_digest = {"reported_status_sha256", "reported_status_length"} & details.keys()
            if (details[source_transaction_status.DETAIL_FIELD] != source_transaction_status.POLICY
                    or archives[source_id] not in feedback_import.MICROSECOND_PROFILES
                    or role != "transaction" or reason in {"feedback_only", "unknown_status"}
                    or (status is None and status_digest != {"reported_status_sha256", "reported_status_length"})
                    or (status is not None and (status_digest or
                        source_transaction_status.explicitly_failed(status) != (reason == "failed_transaction")))):
                _invalid("反馈包原报状态与处置不一致")
        if reason == "failed_transaction" and (not literal_status_policy or disposition != "non_transaction"):
            _invalid("反馈包失败交易不能产生财务流水")
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
            if field in ("review_reasons", "validation_issues") and any(
                    code == "failed_transaction" or code not in feedback_import.REASON_CODES for code in value.split(",")):
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
            if (reason != "failed_transaction" and "signed_amount" in details
                    and _feedback_amount(details, "signed_amount") != canonical_amount):
                _invalid("反馈包规范金额与原始金额不一致")
            effect = ("source_reported" if disposition in ("imported", "duplicate", "removed") else
                      "analysis_excluded" if disposition == "cancelled" else
                      "no_movement" if disposition == "non_monetary" else
                      "undetermined" if disposition == "unresolved" else "not_a_flow")
            if details.get("financial_effect", "not_a_flow" if role != "transaction" else None) != effect:
                _invalid("反馈包财务影响与自动处置不一致")
            if reason == "feedback_only" and (
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
                    canonical_amount is None or canonical_amount <= 0
                    or not literal_status_policy and details.get("reported_status") not in ("01", "1")
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


_TIERED_DETAILS = {table_tiered_headers.PROOF_FIELD, table_tiered_headers.LEAF_PROOF_FIELD,
                   table_tiered_headers.BINDING_FIELD, table_tiered_headers.DIRECTION_BINDING_FIELD}


class _TieredJournal:
    """Recover one worksheet's regions from its ordered physical records."""

    def __init__(self, member_digest, *, allow_groups=False):
        self.member_digest = member_digest
        self.allow_groups = allow_groups
        self.parent = None
        self.omitted_header = False
        self.has_evidence = False
        self.has_grouped_evidence = False
        self._reset_region()

    def _reset_region(self, header=None):
        self.header = header
        self.proof = None
        self.binding = None
        self.blocked = False

    def _start_header(self, row):
        self._reset_region(row)
        if self.parent is not None:
            if self.parent["parent_row"] + 1 != row:
                _invalid("两层表头上下行不相邻")
            self.proof, self.parent = self.parent, None
            self.blocked = self.proof["status"] != "verified"
            if not self.blocked:
                self.binding = table_tiered_headers.binding(self.member_digest, self.proof)

    def record(self, previous, row, reason, disposition, details):
        if row != previous + 1:
            if row != previous + 2 or self.omitted_header:
                _invalid("分层来源的物理记录次序或首表头缺口无效")
            self.omitted_header = True
            self._start_header(row - 1)
        if reason == "repeated_header":
            if not self.omitted_header:
                _invalid("重复表头之前缺少原始首表头")
            self._start_header(row)
        elif self.parent is not None:
            _invalid("分层表头父行之后缺少子表头")

        fields = _TIERED_DETAILS.intersection(details)
        binding = details.get(table_tiered_headers.BINDING_FIELD)
        if reason == table_tiered_headers.PARENT_REASON:
            proof = table_tiered_headers.checked_proof(details.get(table_tiered_headers.PROOF_FIELD),
                                                       version=None if self.allow_groups else 1)
            if (proof is None or proof["parent_row"] != row or disposition != "non_transaction"
                    or fields != {table_tiered_headers.PROOF_FIELD, table_tiered_headers.BINDING_FIELD}
                    or binding != table_tiered_headers.binding(self.member_digest, proof)
                    or table_direction_recheck.DETAIL_FIELD in details):
                _invalid("分层表头证明与物理父行或列依据不一致")
            self._reset_region()
            self.parent, self.has_evidence = proof, True
            self.has_grouped_evidence |= proof["version"] == 2
            return
        if table_tiered_headers.PROOF_FIELD in fields:
            _invalid("分层表头证明只能保存于父行")

        if table_tiered_headers.LEAF_PROOF_FIELD in fields:
            orphan = table_tiered_headers.checked_leaf_proof(details[table_tiered_headers.LEAF_PROOF_FIELD])
            if (orphan is None or orphan["source_row"] != row or reason != "ambiguous_region_header"
                    or disposition != "unresolved"
                    or fields != {table_tiered_headers.LEAF_PROOF_FIELD, table_tiered_headers.BINDING_FIELD}
                    or binding != table_tiered_headers.binding(self.member_digest, orphan)):
                _invalid("缺上层表头证明与物理位置不一致")
            self.has_evidence = True
            fields.clear()
        directed = table_direction_recheck.DETAIL_FIELD in details
        if reason == "ambiguous_region_header":
            self._reset_region()
            self.blocked = True
        if reason in {"blank_record", "preamble", "ambiguous_region_header"}:
            if fields or directed:
                _invalid("来源边界或空行不能携带区域解释依据")
            return

        effect_row = disposition in {"imported", "duplicate", "removed", "non_monetary"}
        if (self.blocked or self.header is None) and (effect_row or fields or directed):
            _invalid("未证明表头的区域不能赋予流水或方向依据")
        if (binding is not None and binding != self.binding
                or self.binding is not None and (effect_row or directed or reason == "repeated_header") and binding is None):
            _invalid("分层解释与本区域的表头或物理列证明不一致")
        direction_binding = details.get(table_tiered_headers.DIRECTION_BINDING_FIELD)
        if directed:
            encoded = details[table_direction_recheck.DETAIL_FIELD]
            if direction_binding != table_tiered_headers.binding(self.member_digest, [row, encoded]):
                _invalid("收付复判证明与原物理行列不一致")
        elif direction_binding is not None:
            _invalid("收付复判缺少对应证明")

    def finish(self, last_row):
        if self.parent is not None:
            if self.omitted_header or self.parent["parent_row"] != last_row:
                _invalid("分层表头父行之后缺少子表头")
            self._start_header(last_row + 1)


def _pdf_journals(check, paths):
    """PDF candidates can restore only with their source-only operation."""
    from . import pdf_import, pdf_text_observations

    profiles = {pdf_import.PROFILE: pdf_import, **dict.fromkeys(pdf_text_observations.PROFILES, pdf_text_observations)}
    sources = check.execute("select source_id,sha256,size_bytes,parser_version from import_sources where parser_version in (?,?,?)",
                            list(profiles)).fetchall()
    if not sources:
        return
    if not {*JOURNAL_PKG_COLS, "import_source_operations", "batches"}.issubset(paths):
        _invalid("PDF 来源缺少复核日志或导入操作")
    if check.execute("""select 1 from import_sources s
        left join import_source_operations o on o.source_id=s.source_id
        where s.parser_version in (?,?,?) and (o.source_id is null
            or exists(select 1 from flow_sources f where f.source_id=s.source_id)
            or exists(select 1 from flows f where o.active and f.batch_id=o.batch_id)
            or exists(select 1 from import_source_operations p where o.active and p.active
                      and p.batch_id=o.batch_id and p.source_id<>s.source_id)) limit 1""",
                     list(profiles)).fetchone():
        _invalid("PDF 复核来源不能含流水或共用导入批次")
    for source_id, digest, size, profile in sources:
        reader = profiles[profile]
        members = check.execute("""select member_path,role,sha256,size_bytes,encoding,row_count
            from import_members where source_id=? limit 2""", [source_id]).fetchall()
        if (len(members) != 1 or members[0][:5] != (pdf_import.MEMBER_PATH, "pdf", digest, size, "pdf")
                or members[0][5] is None or not 1 <= members[0][5] <= reader.MAX_ROWS):
            _invalid("PDF 来源成员或校验值无效")
        cursor = check.execute("""select record_no,member_path,source_row,record_key,disposition,reason_code,
                case when octet_length(encode(details_json))<=? then details_json end
            from import_records where source_id=? order by record_no limit ?""",
                               [reader.MAX_RECORD_BYTES, source_id, reader.MAX_ROWS + 1])

        try:
            if reader is pdf_import:
                def records():
                    while batch := cursor.fetchmany(64):
                        yield from batch

                count = reader.validate_journal(digest, records())
            else:
                rows = cursor.fetchall()
                restored = reader.replay_journal([json.loads(row[-1]) for row in rows], digest, size,
                                                 reader.MEMBER_PATH, profile=profile)
                fields = ("record_no", "member_path", "source_row", "record_key", "disposition", "reason_code")
                if any(row[:-1] != tuple(record[field] for field in fields)
                       for row, record in zip(rows, restored["records"], strict=True)):
                    raise ValueError("PDF observation journal position changed")
                count = len(rows)
        except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError, RecursionError):
            _invalid("PDF 候选位置或复核记录无效")
        if (count != members[0][5] or check.execute("""select 1 from import_source_operations o
            join batches b on b.id=o.batch_id where o.source_id=? and o.active
            and (b.mode is distinct from 'B' or b.rows_total is distinct from ?
                 or b.rows_skipped is distinct from ? or b.rows_imported is distinct from 0
                 or b.rows_dup is distinct from 0)""", [source_id, count, count]).fetchone()):
            _invalid("PDF 候选与导入批次计数不一致")



def _pdf_text_journals(check, paths):
    """Recover the frozen visible positions and reconciled PDF row facts."""
    from . import import_validation, pdf_text_import

    sources = check.execute("select source_id,sha256,size_bytes from import_sources where parser_version=?",
                            [pdf_text_import.PROFILE]).fetchall()
    if not sources:
        return
    if not {*JOURNAL_PKG_COLS, "flow_sources", "import_source_operations", "batches"}.issubset(paths):
        _invalid("文本账簿 PDF 缺少原页或入库日志")
    flow_fields = import_validation._STORED_FIELDS
    for source_id, digest, size in sources:
        members = check.execute("""select member_path,role,sha256,size_bytes,encoding,row_count
            from import_members where source_id=? limit 2""", [source_id]).fetchall()
        if (len(members) != 1 or members[0][:5] != (pdf_text_import.MEMBER_PATH, "pdf", digest, size, "pdf")
                or members[0][5] is None or not 1 <= members[0][5] <= pdf_text_import.MAX_ROWS):
            _invalid("文本账簿 PDF 的原页元数据无效")
        count = members[0][5]
        operations = check.execute("""select o.active,b.mode,b.rows_total from import_source_operations o
            left join batches b on b.id=o.batch_id where o.source_id=?""", [source_id]).fetchall()
        if len(operations) != 1 or operations[0][0] and operations[0][1:] != ("A", count):
            _invalid("文本账簿 PDF 的入库操作无效")
        active = operations[0][0]
        rows = check.execute("""select r.record_no,r.member_path,r.source_row,r.record_key,r.disposition,r.reason_code,
            case when octet_length(encode(r.details_json))<=2048 then r.details_json end,
            l.flow_id,l.disposition,""" + ",".join("f." + field for field in flow_fields) + """
            from import_records r left join flow_sources l on r.source_id=l.source_id and r.record_no=l.source_row
            left join flows f on f.id=l.flow_id where r.source_id=? order by r.record_no limit ?""",
            [source_id, pdf_text_import.MAX_ROWS + 1]).fetchall()
        if len(rows) != count:
            _invalid("文本账簿 PDF 的逐行证据不完整")
        try:
            plan = pdf_text_import.replay_journal([json.loads(row[6]) for row in rows], digest, size)
        except (KeyError, ValueError, TypeError, ArithmeticError, RecursionError):
            _invalid("文本账簿 PDF 的冻结原位或余额证据无效")
        for stored, expected, facts in zip(rows, plan["records"], plan["rows"], strict=True):
            number, member, position, key, disposition, reason, _, flow_id, link_disposition, *actual = stored
            if (number, member, position, key) != tuple(expected[field] for field in (
                    "record_no", "member_path", "source_row", "record_key")):
                _invalid("文本账簿 PDF 的页行坐标不一致")
            if disposition == "removed" and reason == "flow_deleted" and flow_id is None:
                continue
            if (not active or disposition not in ("imported", "duplicate") or reason != "transaction_valid"
                    or flow_id is None or link_disposition != disposition):
                _invalid("文本账簿 PDF 的来源链接无效")
            try:
                if import_validation._facts(dict(zip(flow_fields, actual))) != import_validation._facts(facts):
                    _invalid("文本账簿 PDF 的金额或交易事实与原页不一致")
            except (ValueError, TypeError, KeyError):
                _invalid("文本账簿 PDF 的交易事实无效")


def _verify_package_reference_fields(details, flow, source_id, known_columns, member_digest):
    from . import table_import

    actual = flow["trade_no"]
    basis = details.get("trade_no_basis")
    if basis not in {"source_transaction_id", "none"} or bool(actual) != (basis == "source_transaction_id"):
        _invalid("已核编号用途不能以独立凭证回填交易号")
    try:
        fields = json.loads(details.get("source_field_evidence", "[]"))
    except ValueError:
        _invalid("编号用途缺少可信原列记录")
    if not isinstance(fields, list) or any(not isinstance(item, dict) for item in fields):
        _invalid("编号用途缺少可信原列记录")
    allowed = {"source_transaction_id": {"交易流水号"}, "independent_voucher_reference": {"凭证号", "凭证编号"}}
    columns, voucher = {}, None
    for item in fields:
        role, heading, index, raw = (item.get(name) for name in ("role", "column", "column_index", "raw"))
        if not isinstance(role, str) or role not in allowed:
            continue
        if (role in columns or not isinstance(heading, str) or table_import._norm(heading) not in allowed[role]
                or type(index) is not int or not 1 <= index <= 64 or index in columns.values()
                or not isinstance(raw, str) or role == "source_transaction_id" and raw.strip() != (actual or "")):
            _invalid("编号用途与原列角色或保留交易号不一致")
        columns[role] = index
        if role == "independent_voucher_reference":
            voucher = raw.strip()
    if occurrence := details.get(table_import.VERIFIED_REFERENCE_OCCURRENCE):
        column, size, voucher_hash, _ = table_import.VERIFIED_REFERENCE_OCCURRENCE_FORMAT.fullmatch(occurrence).groups()
        column, size = int(column), int(size)
        canonical = table_import.flow_digest(flow)
        if voucher is None and details.get("source_field_evidence_limited") == "true" and (
                "source_transaction_id" in columns and column not in columns.values()):
            # The bounded journal can omit a long original value. Its physical
            # position, exact byte length and digest still bind this occurrence.
            expected = table_import.verified_voucher_binding(member_digest, canonical, column, size, voucher_hash)
            columns["independent_voucher_reference"] = column
        else:
            expected = (table_import.verified_voucher_occurrence(member_digest, canonical, column, voucher)
                        if voucher and column == columns.get("independent_voucher_reference") else None)
        if occurrence != expected:
            _invalid("独立凭证记录与原凭证或流水事实不一致")
    if columns.keys() != allowed.keys():
        if details.get("source_field_evidence_limited") != "true":
            _invalid("编号用途缺少主号或凭证原列")
        return
    positions = tuple(columns[role] for role in allowed)
    if known_columns.setdefault(source_id, positions) != positions:
        _invalid("同一原表的编号用途指向不同物理列")


def _table_journals(check, paths):
    """Validate automatic-table journals independently of bank-feedback rules."""
    tables = check.execute("select source_id,parser_version,imported_at,sha256 from import_sources where row_kind='table_record'").fetchall()
    if not tables:
        return
    if not set(JOURNAL_PKG_COLS).issubset(paths):
        _invalid("自动表格来源缺少成员与记录日志")
    from . import table_import, table_archive, table_cancellations, table_contracts, source_ledger, bank_posting_pairs, source_transaction_status
    from .services import trial_delayed_pair
    from . import pdf_text_observations
    source_profiles = {source_id: profile for source_id, profile, _time, _digest in tables}
    source_times = {source_id: time for source_id, _profile, time, _digest in tables}
    csv_tail_sources = {source_id: digest for source_id, profile, _time, digest in tables
                        if profile == table_import.CSV_TAIL_SOURCE_PROFILE}
    csv_tail_ordinary_sources = {source_id: digest for source_id, profile, _time, digest in tables
                                 if profile in table_import.CSV_TAIL_ORDINARY_PROFILES}
    csv_tail_geometry = {**csv_tail_sources, **csv_tail_ordinary_sources}
    deposit_sources = {source_id: digest for source_id, profile, _time, digest in tables
                       if profile == table_import.DEPOSIT_DIRECTION_TRIAL_PROFILE}
    deposit_csv_sources = {source_id: digest for source_id, profile, _time, digest in tables
                           if profile in table_import.DEPOSIT_CSV_TRIAL_PROFILES}
    deposit_mappings = {source_id: json.loads(mapping)["direction"] for source_id, mapping in check.execute(
        "select source_id,mapping_json from import_sources where parser_version=?",
        [table_import.DEPOSIT_DIRECTION_TRIAL_PROFILE]).fetchall()} if deposit_sources else {}
    reference_sources = {source_id: digest for source_id, profile, _time, digest in tables
                         if profile in table_import.VERIFIED_REFERENCE_PROFILES}
    tiered_sources = {source_id: digest for source_id, profile, _time, digest in tables if profile in table_import.TIERED_PROFILES}
    has_cancellation_profile = bool(table_archive.CANCELLATION_PROFILES.intersection(source_profiles.values()))

    for table, limit in (("import_members", table_archive.EVIDENCE_MAX_MEMBERS),
                         ("import_records", table_archive.EVIDENCE_MAX_RECORDS)):
        if check.execute(f"select 1 from {table} j join import_sources s on s.source_id=j.source_id where s.row_kind='table_record' group by j.source_id having count(*)>? limit 1", [limit]).fetchone():
            _invalid("自动表格日志总量超出限制")
    members = check.execute("""select m.source_id,m.member_path,m.role,m.sha256,m.size_bytes,m.encoding,m.row_count,s.size_bytes
        from import_members m join import_sources s on s.source_id=m.source_id
        where s.row_kind='table_record'""").fetchall()
    member_rows, by_source, xls_members, tiered_members = {}, {}, set(), {}
    pdf_member_sizes = {}
    for source_id, path, role, digest, size, encoding, count, source_size in members:
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
                or source_profiles[source_id] in table_import.STANDALONE_XLS_PROFILES and encoding != "xls"
                or encoding == "xls" and source_profiles[source_id] in table_import.XLS_EVIDENCE_ARCHIVE_PROFILES
                   and not re.search(r"\.xls/worksheets/[0-9]{4}$", path, re.IGNORECASE)):
            _invalid("自动表格成员元数据无效")
        if encoding == "xls":
            xls_members.add((source_id, path))
        if source_id in tiered_sources:
            if encoding != "xlsx" or size != 0 or not re.fullmatch(r"worksheets/[0-9]{4}", path) or not 1 <= int(path[-4:]) <= table_import.MAX_SHEETS:
                _invalid("两层表头只适用于已定位的 XLSX 工作表")
            if role == "table":
                tiered_members[source_id, path] = _TieredJournal(
                    digest, allow_groups=source_profiles[source_id] == table_tiered_headers.GROUPED_PROFILE)
        if source_id in csv_tail_geometry and (
                path != "table.csv" or role != "table" or digest != csv_tail_geometry[source_id]
                or encoding not in ("utf-8-sig", "gb18030", "utf-16")
                or size != source_size or not 0 < size <= table_import.MAX_CSV_TAIL_SOURCE_BYTES
                or not 2 <= count < table_import.MAX_CSV_TAIL_SOURCE_ROWS):
            _invalid("末行缺格的CSV来源成员与已登记范围不一致")
        if source_id in deposit_csv_sources and (
                path != "table.csv" or role != "table" or digest != deposit_csv_sources[source_id]
                or encoding not in ("utf-8-sig", "gb18030", "utf-16")
                or size != source_size or not 0 < size <= table_import.MAX_DEPOSIT_DIRECTION_BYTES
                or not (2 if source_id in deposit_sources else 1) <= count < table_import.MAX_DEPOSIT_DIRECTION_ROWS):
            _invalid("存取款字面依据与来源成员不一致")
        member_rows[source_id, path] = (role, digest, count)
        if Path(path).suffix.lower() == ".pdf":
            if role != "attachment":
                _invalid("归档 PDF 成员不能声明交易或辅助身份表")
            pdf_member_sizes[source_id, path] = size
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
                or (s.parser_version not in (""" + source_ledger._profiles() + """) and
                    (r.disposition not in ('imported','duplicate') or r.disposition<>f.disposition))) limit 1""").fetchone():
        _invalid("自动表格流水来源与记录处置不一致")

    flow_fields = tuple(field.strip() for field in ZONED_FLOW_COLS.split(",")
                        if field.strip() not in ("id", "batch_id"))
    cursor = check.execute("""select r.source_id,r.record_no,r.member_path,r.source_row,r.record_key,
            r.disposition,r.reason_code,r.details_json,f.disposition,f.flow_id,"""
            + ",".join(f"v.{field}" for field in flow_fields) + """
        from import_records r join import_sources s on s.source_id=r.source_id
        left join flow_sources f on f.source_id=r.source_id and f.source_row=r.record_no
        left join flows v on v.id=f.flow_id where s.row_kind='table_record'
        order by r.source_id,r.record_no""")
    sequences, counts, evidence_records, known_facts, current_facts = {}, {}, {}, {}, {}
    header_regions, previous_source_rows, sheet_names = {}, {}, {}
    deposit_positions, deposit_terms = {}, {}
    reference_counts, reference_columns, reference_occurrences = {}, {}, {}
    physical_layouts, field_description_sources, pdf_observations = {}, set(), {}
    field_description_members, wrong_field_notes = set(), []
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
            tiered_rule = source_id in tiered_sources
            tiered_journal = tiered_members.get((source_id, path))
            cancellation_rule = source_profiles[source_id] in table_archive.CANCELLATION_PROFILES
            reason_codes = table_import.REASON_CODES | (frozenset(table_cancellations.REASONS)
                                                        if cancellation_rule else frozenset())
            if source_profiles[source_id] in evidence_resolution.PROFILES:
                reason_codes |= frozenset(import_attachments.REASONS) | {table_archive.UNREADABLE_REASON}
                if role == "auxiliary":
                    reason_codes |= {trial_delayed_pair.COVERAGE_REASON}
            pdf_observed = reason in pdf_text_observations.DISPOSITIONS
            if pdf_observed:
                reason_codes |= {reason}
            if (source_id, path) not in xls_members and not tiered_rule:
                reason_codes -= {"invalid_cell_type"}
            if tiered_journal is None:
                reason_codes -= {table_tiered_headers.PARENT_REASON}
            record_dispositions = (*dispositions, *(('cancelled',) if cancellation_rule else ()))
            if (number is None or number != sequences.get(source_id, 0) + 1
                    or source_row is None or not 0 < source_row <= row_count + 1
                    or disposition not in record_dispositions
                    or reason not in (*reason_codes, "flow_deleted")):
                _invalid("自动表格记录位置或处置无效")
            if source_id in csv_tail_sources:
                if (source_row != number + 1 or flow_id is not None or (disposition, reason) not in {
                        ("unresolved", table_import.CSV_TAIL_SOURCE_REASON), ("non_transaction", "blank_record")}):
                    _invalid("末行缺格的CSV只能恢复原位置的来源记录")
            elif reason == table_import.CSV_TAIL_SOURCE_REASON:
                _invalid("末行缺格的CSV处置与来源规则不一致")
            if reason == table_import.CSV_TAIL_INCOMPLETE_REASON and (
                    source_profiles[source_id] not in table_import.CSV_TAIL_COMPLETE_ROW_PROFILES
                    or disposition != "unresolved" or flow_id is not None):
                _invalid("末行缺格记录的处置与来源规则不一致")
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
            ledger_rule = source_profiles[source_id] in table_import.SOURCE_LEDGER_PROFILES
            if not isinstance(details_json, str) or len(details_json.encode("utf-8")) > (source_ledger.MAX_BYTES + 8192 if ledger_rule else 8192):
                _invalid("自动表格语义元数据过大或无效")
            details = json.loads(details_json)
            if pdf_observed:
                reference = source_id, path
                rows_seen = pdf_observations.setdefault(reference, [])
                size = pdf_member_sizes.get(reference, 0)
                family_fields = {"evidence_family", "family_member_sha256"}
                if (source_profiles[source_id] not in table_archive.EVIDENCE_PROFILES
                        or not 0 < size <= pdf_text_observations.MAX_BYTES
                        or not 1 <= row_count <= pdf_text_observations.MAX_ROWS
                        or source_row != len(rows_seen) + 1 or source_row > row_count
                        or disposition != pdf_text_observations.DISPOSITIONS[reason]
                        or flow_id is not None or linked_disposition is not None
                        or len(details_json.encode("utf-8")) > pdf_text_observations.MAX_RECORD_BYTES
                        or not isinstance(details, dict)
                        or (set(details) & family_fields) not in (set(), family_fields)
                        or any(not isinstance(details[field], str) or not re.fullmatch(r"[0-9a-f]{64}", details[field])
                               for field in family_fields if field in details)):
                    _invalid("归档 PDF 来源观察位置或处置无效")
                rows_seen.append((reason, {field: value for field, value in details.items() if field not in family_fields}))
                continue
            detail_fields = table_import.DETAILS_FIELDS
            if source_profiles[source_id] == table_archive.TRIAL_MEMBER_PROFILE:
                detail_fields |= {table_import.TRIAL_MEMBER_PROFILE_FIELD}
            if role == "auxiliary" and source_profiles[source_id] in (
                *table_archive.EVIDENCE_PROFILES, table_import.VOUCHER_REFERENCE_EVIDENCE_PROFILE,
            ):
                detail_fields |= {table_archive.INTAKE_ROLE_DETAIL}
            if reason == table_archive.UNREADABLE_REASON:
                detail_fields |= {table_archive.RETENTION_DETAIL}
            if source_profiles[source_id] in table_import.CONFIRMED_LAYOUT_PROFILES:
                detail_fields |= {table_import.CONFIRMED_LAYOUT_FIELD, table_import.CONFIRMED_NOTE_FIELD}
            if ledger_rule:
                detail_fields |= {source_ledger.FIELD}
            if source_profiles[source_id] in bank_posting_pairs.PROFILES:
                detail_fields |= {bank_posting_pairs.FIELD}
            if source_profiles[source_id] in table_import.PRECISE_PROFILES:
                detail_fields |= {table_import.table_sign_declarations.DETAIL_FIELD}
            if source_profiles[source_id] == table_direction_recheck.PROFILE or tiered_journal is not None:
                detail_fields |= {table_direction_recheck.DETAIL_FIELD}
            if tiered_journal is not None:
                detail_fields |= _TIERED_DETAILS
            if source_profiles[source_id] in table_import.REFERENCE_ROLE_PROFILES:
                detail_fields |= table_import.FIELD_EVIDENCE_DETAILS
            if source_profiles[source_id] in table_import.TIME_ROLE_PROFILES:
                detail_fields |= table_import.TIME_EVIDENCE_DETAILS
            if source_profiles[source_id] == table_import.TRADE_CLOCK_PRECISION_TRIAL_PROFILE:
                detail_fields |= {table_import.CORROBORATED_TRADE_CLOCK_FIELD}
            contract_rule = source_profiles[source_id] in table_import.CONTRACT_PROFILES
            if contract_rule:
                detail_fields |= table_contracts.DETAILS_FIELDS
            if source_profiles[source_id] in table_archive.REFERENCE_PROFILES | table_import.SOURCE_ROLE_PROFILES:
                detail_fields |= table_import.REFERENCE_DETAILS_FIELDS
            if source_id in reference_sources:
                detail_fields |= {table_import.VERIFIED_REFERENCE_DETAIL, table_import.VERIFIED_REFERENCE_OCCURRENCE}
            if cancellation_rule:
                detail_fields |= table_cancellations.DETAILS_FIELDS
            if source_profiles[source_id] in evidence_resolution.PROFILES:
                detail_fields |= {evidence_resolution.DETAIL_FIELD, "occurrence_scope", "evidence_family", "family_member_sha256"} | import_attachments.DETAILS_FIELDS
            if source_profiles[source_id] in event_resolution.PROFILES:
                detail_fields |= {event_resolution.DETAIL_FIELD, event_resolution.LEDGER_FIELD}
            if (not isinstance(details, dict) or not set(details).issubset(detail_fields)
                    or any(not isinstance(value, str) or len(value.encode("utf-8")) > (
                        source_ledger.MAX_BYTES if field == source_ledger.FIELD else
                        table_tiered_headers.MAX_BYTES if field in _TIERED_DETAILS else
                        8192 if field == evidence_resolution.DETAIL_FIELD else 2048 if field in {bank_posting_pairs.FIELD, event_resolution.DETAIL_FIELD, table_import.table_sign_declarations.DETAIL_FIELD, "source_field_evidence", "source_time_evidence"} else 1024)
                           for field, value in details.items())):
                _invalid("自动表格语义元数据无效")
            adopted_reference = details.get(table_import.VERIFIED_REFERENCE_DETAIL)
            if (source_profiles[source_id] == table_import.CSV_TAIL_REFERENCE_PROFILE
                    and (path != "table.csv" or disposition in {"imported", "duplicate"} and adopted_reference is None)):
                _invalid("末行缺格的资金记录缺少同原表的编号用途核证")
            if adopted_reference is not None:
                source_digest = reference_sources[source_id]
                workbook_title = details.get("sheet")
                bound_member = (path == "table.csv" and member_digest == source_digest
                                or path == "worksheets/0001" and bool(workbook_title)
                                and member_digest == table_import.xlsx_member_digest(source_digest, 1, workbook_title))
                if (adopted_reference != table_import.VERIFIED_REFERENCE_POLICY or role != "table"
                        or len(by_source[source_id]) != 1 or not bound_member
                        or header_regions.get((source_id, path)) != 1 or source_row != number + 1
                        or source_row > 256 or details.get("trade_no_basis") == "legacy_voucher_fallback"):
                    _invalid("编号用途与来源物理表区或交易号依据不一致")
                reference_counts[source_id] = reference_counts.get(source_id, 0) + 1
            if (occurrence := details.get(table_import.VERIFIED_REFERENCE_OCCURRENCE)) is not None and (
                    adopted_reference is None or not table_import.VERIFIED_REFERENCE_OCCURRENCE_FORMAT.fullmatch(occurrence)
                    or path != "worksheets/0001" or details.get("trade_no_basis") != "none"):
                _invalid("独立凭证记录与空主号来源证明不一致")
            if (leaf_profile := details.get(table_import.TRIAL_MEMBER_PROFILE_FIELD)) is not None:
                workbook_path = table_import.archive_workbook_path(path)
                leaf_is_workbook = bool(workbook_path and workbook_path.lower().endswith(".xlsx"))
                if (leaf_profile not in table_import.TRIAL_TABLE_PROFILES
                        or leaf_profile == table_import.TRADE_CLOCK_PRECISION_TRIAL_PROFILE
                        or leaf_is_workbook != (leaf_profile in table_import.TRIAL_XLSX_PROFILES)
                        or not leaf_is_workbook and not path.lower().endswith(".csv")):
                    _invalid("目录叶成员来源规则与物理类型不一致")
            if table_import.CONFIRMED_LAYOUT_FIELD in details:
                if role != "table":
                    _invalid("已确认物理映射必须属于交易表")
                try:
                    layout = table_import.validate_confirmed_layout_detail(
                        details[table_import.CONFIRMED_LAYOUT_FIELD], header_regions.get((source_id, path)), source_row
                    )
                except (ValueError, TypeError):
                    _invalid("已确认物理映射与来源位置不一致")
                if disposition == "unresolved" and (
                    layout["policy"] != table_import.CONFIRMED_LAYOUT_EVIDENCE_POLICY or flow_id is not None
                    or not table_import.retained_split_conflict(disposition, reason, details)
                ):
                    _invalid("已确认物理映射的未决记录不符合受理规则")
                regions = physical_layouts.setdefault((source_id, path), {})
                header = layout["header_row"]
                prior, seen = regions.get(header, (layout, 0))
                if prior != layout:
                    _invalid("同一表区的已确认物理映射不一致")
                regions[header] = (layout, seen + 1)
            if tiered_rule:
                sheet_name = details.get("sheet")
                reference = source_id, path
                if reference not in sheet_names:
                    if (not sheet_name or member_digest != table_import.xlsx_member_digest(
                            tiered_sources[source_id], int(path[-4:]), sheet_name)):
                        _invalid("分层表头不属于已登记工作表")
                    sheet_names[reference] = sheet_name
                if sheet_name != sheet_names[reference] or details.get("source_row") != str(source_row):
                    _invalid("分层表头不属于已登记工作表或物理行")
            if tiered_journal is not None:
                tiered_journal.record(previous_row, source_row, reason, disposition, details)
                header_regions[source_id, path] = tiered_journal.header
            ledger_entry = None
            if source_ledger.FIELD in details:
                try:
                    ledger_entry = source_ledger.validate(details[source_ledger.FIELD])
                except (ValueError, TypeError, KeyError):
                    _invalid("来源记账语义无效")
                if ledger_entry["policy"] == source_ledger.UNORIENTED_POLICY and source_profiles[source_id] not in table_import.UNORIENTED_PROFILES:
                    _invalid("未知方向来源记账与解析版本不符")
                if ledger_entry["policy"] == source_ledger.SOURCE_OBSERVATION_POLICY and source_profiles[source_id] not in table_import.SOURCE_OBSERVATION_PROFILES:
                    _invalid("原始观察与解析版本不符")
                if ledger_entry["header_scope"] != header_regions.get((source_id, path)):
                    _invalid("来源记账范围与来源表头不一致")
                if flow_id is not None and (ledger_entry["kind"] == "context"
                        or table_import.flow_digest(ledger_entry["row"]) != table_import.flow_digest(dict(zip(flow_fields, facts)))
                        or any(ledger_entry["row"][field] != dict(zip(flow_fields, facts)).get(field, "")
                               for field in ("self_account", "self_name"))):
                    _invalid("来源记账与保留原始分录不一致")
            literal = ledger_entry.get("direction_literal") if ledger_entry is not None else None
            if source_id in deposit_sources:
                try:
                    fields = json.loads(details.get("source_field_evidence", "[]"))
                except (ValueError, RecursionError):
                    _invalid("存取款字面缺少原列依据")
                directions = [field for field in fields if isinstance(field, dict) and field.get("field") == "direction"] if isinstance(fields, list) else []
                if role != "table" or source_row != number + 1 or header_regions.get((source_id, path)) != 1 or len(directions) != 1:
                    _invalid("存取款字面缺少已登记的原行与方向列")
                [direction] = directions
                index, column, raw = (direction.get(field) for field in ("column_index", "column", "raw"))
                if (direction.get("role") != "direction" or column != deposit_mappings[source_id]
                        or type(index) is not int or not 1 <= index <= table_import.MAX_COLUMNS
                        or raw not in ("存款", "取款") or deposit_positions.setdefault(source_id, (index, column)) != (index, column)
                        or (flow_id is not None and ledger_entry is None)):
                    _invalid("存取款字面与已登记方向列不一致")
                if ledger_entry is not None and (raw != ledger_entry["source_direction"]
                        or (raw == "存款") != (literal is not None)
                        or literal is not None and (index, column) != (literal["column_index"], literal["column"])):
                    _invalid("存取款字面方向与原行证据不一致")
                deposit_terms.setdefault(source_id, set()).add(raw)
            elif literal is not None:
                _invalid("存款字面证据与来源解析版本不一致")
            if bank_posting_pairs.FIELD in details:
                try:
                    pair_contract = bank_posting_pairs.validate(details[bank_posting_pairs.FIELD])
                except (ValueError, TypeError, KeyError):
                    _invalid("银行双侧过账契约无效")
                try:
                    source_fields = json.loads(details.get("source_field_evidence", "[]"))
                except ValueError:
                    _invalid("银行双侧过账的来源字段依据无效")
                if not isinstance(source_fields, list) or any(not isinstance(item, dict)
                        or not isinstance(item.get("role"), str) or not isinstance(item.get("raw"), str) for item in source_fields):
                    _invalid("银行双侧过账的来源字段依据无效")
                if not pair_contract["independent_references"] and any(
                        item.get("role") == "independent_voucher_reference"
                        and bank_posting_pairs.independent_reference(item.get("raw")) for item in source_fields):
                    _invalid("独立凭证与银行双侧过账契约不一致")
                if ledger_entry and ledger_entry["kind"] == "financial" and (
                        pair_contract["source_primary_reference"] != bank_posting_pairs._identifier(ledger_entry["row"]["trade_no"])
                        or pair_contract["source_counterparty_bank"] != bank_posting_pairs._identifier(ledger_entry["row"][
                            "payee_bank" if ledger_entry["direction"] == "out" else "payer_bank"])):
                    _invalid("银行双侧过账契约与来源事实不一致")
            if ("source_time_evidence" in details and not table_import.table_time_formats.validate_source_time_evidence(details["source_time_evidence"])):
                _invalid("来源时间依据无效")
            if table_import.CORROBORATED_TRADE_CLOCK_FIELD in details:
                clock = details[table_import.CORROBORATED_TRADE_CLOCK_FIELD]
                if (not table_import.validate_corroborated_trade_clock(clock, details.get("source_time_evidence"))
                        or flow_id is not None and str(dict(zip(flow_fields, facts))["trade_time"]) != clock):
                    _invalid("来源细时钟与保留时间依据不一致")
            if table_import.table_sign_declarations.DETAIL_FIELD in details:
                if not table_import.table_sign_declarations.validate_proof(
                        details[table_import.table_sign_declarations.DETAIL_FIELD],
                        scoped_legs=source_profiles[source_id] == table_import.BANK_COMPONENT_TRIAL_PROFILE,
                        member=path, source_row=source_row):
                    _invalid("金额符号来源声明无效")
            has_direction_heading = ledger_entry is not None and "direction_heading" in ledger_entry
            if table_direction_recheck.DETAIL_FIELD in details:
                scope = header_regions.get((source_id, path))
                try:
                    review = table_direction_recheck.validate(details[table_direction_recheck.DETAIL_FIELD], scope)
                except (ValueError, TypeError, KeyError, RecursionError):
                    _invalid("收付方向复判依据无效")
                verified = review["status"] == "verified"
                historical_conflict = (reason == "conflicting_field" and details.get("counterparty_basis")
                    in {table_import.HISTORY_CONFLICT_BASIS, table_import.ARCHIVE_HISTORY_CONFLICT_BASIS})
                valid_disposition = (disposition in (*accepted, "removed") and verified
                                     or disposition == "unresolved" and (not verified or historical_conflict))
                if role != "table" or source_row <= scope or not valid_disposition or has_direction_heading != verified:
                    _invalid("收付方向复判与来源处置不一致")
                if tiered_journal and tiered_journal.proof:
                    tier_columns = {claim[0] for claim in tiered_journal.proof["columns"]}
                    if any(column["column_index"] in tier_columns or column["column_index"] > tiered_journal.proof["width"]
                           for column in review["columns"]):
                        _invalid("收付复判列与分层表头已证明的列冲突")
                if verified and (ledger_entry["direction_heading"], ledger_entry["source_direction"], ledger_entry["direction"]) != (
                        review["columns"][0]["column"], review["columns"][0]["raw"], review["direction"]):
                    _invalid("收付方向复判与来源记账不一致")
            elif has_direction_heading:
                _invalid("来源收付标题缺少复判依据")
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
                        if side == "counter":
                            if (source_profiles[source_id] not in table_import.SOURCE_OBSERVATION_PROFILES
                                    or source_ledger.FIELD not in details or ledger_entry["direction"] != "unknown"
                                    or any(item[key] != ledger_entry.get("counterparty", {}).get(key, "") for key in ("account", "name", "bank"))
                                    or item["card"]):
                                _invalid("中性对方声明与来源观察不一致")
                            continue
                        if (item["account"] or item["card"]) != (original.get(side + "_account") or ""):
                            _invalid("身份声明与保留流水端点不一致")
            # Auxiliary members can carry no money contract. Validate any
            # contract they do carry; table members keep the full requirement.
            if (contract_rule and role != "attachment"
                    and (role != "auxiliary" or table_contracts.DETAILS_FIELDS.intersection(details))
                    and not table_contracts.validate_details(details, disposition,
                        "repeated_header" if reason == table_tiered_headers.PARENT_REASON else reason)):
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
                        "blank_record", "repeated_header", "feedback_only", "failed_transaction", table_tiered_headers.PARENT_REASON)
                    or disposition == "auxiliary" and reason not in ("preamble", "auxiliary_table", "non_business_attachment")
                    or disposition == "unresolved" and reason in (
                        "transaction_valid", "flow_deleted", "zero_amount", "blank_record",
                        "repeated_header", "preamble", "auxiliary_table", "feedback_only", "failed_transaction")):
                _invalid("自动表格原因与处置不一致")
            if (reason == "failed_transaction" and details.get("reported_status") != "explicit_failure"
                    or "reported_status" in details and details["reported_status"] not in (
                        "source_reported_success", "explicit_failure",
                        *( ("unknown",) if source_profiles[source_id] in table_import.PRECISE_PROFILES else () ))
                    or details.get("reported_status") == "explicit_failure" and reason != "failed_transaction"):
                _invalid("自动表格交易状态与处置不一致")
            if source_transaction_status.DETAIL_FIELD in details and (
                    details[source_transaction_status.DETAIL_FIELD] != source_transaction_status.POLICY
                    or source_profiles[source_id] not in table_import.PRECISE_PROFILES
                    or role != "table" or "reported_status" not in details):
                _invalid("自动表格原报状态范围或规则无效")
            auxiliary_disposition = (disposition == "auxiliary" and reason == "auxiliary_table"
                                     or disposition == "unresolved" and reason in (
                                         "row_width", "column_count_mismatch", trial_delayed_pair.COVERAGE_REASON))
            wrong_scope = details.get(table_import.CONFIRMED_NOTE_FIELD)
            if (wrong_scope is not None) != (reason == table_import.WRONG_FIELD_NOTE_ROW):
                _invalid("字段说明错址记录缺少原引用位置")
            if wrong_scope is not None:
                if (role != "auxiliary" or details.get("source_role") != "field_description"
                        or disposition != "unresolved" or flow_id is not None
                        or details.get("validation_issues") != reason):
                    _invalid("字段说明错址与来源处置不一致")
                try:
                    scope = table_import.validate_confirmed_note_scope(wrong_scope)
                except (ValueError, TypeError):
                    _invalid("字段说明错址的原引用位置无效")
                wrong_field_notes.append((source_id, path, scope))
                auxiliary_disposition = True
            field_description = (role == "auxiliary" and details.get("source_role") == "field_description"
                                 and source_profiles[source_id] in table_import.CONFIRMED_LAYOUT_PROFILES
                                 and (disposition == "auxiliary" and reason == "auxiliary_table"
                                      or wrong_scope is not None))
            if field_description:
                field_description_sources.add(source_id)
                if wrong_scope is None:
                    field_description_members.add((source_id, path))
            if role == "attachment":
                expected = "non_business_attachment" if details.get("attachment_kind") == "finder_metadata" else "unsupported_attachment"
                if reason == table_archive.UNREADABLE_REASON:
                    if (source_profiles[source_id] not in table_archive.EVIDENCE_PROFILES
                            or details.get(table_archive.RETENTION_DETAIL) != table_archive.MEMBER_RETENTION_POLICY
                            or details.get("attachment_kind") not in (".csv", ".tsv", ".txt", ".xls", ".xlsx")):
                        _invalid("不可读成员处置与来源版本不一致")
                    expected = table_archive.UNREADABLE_REASON
                if source_profiles[source_id] not in evidence_resolution.PROFILES or details.get("source_role") != "attachment" or reason != expected or disposition != ("auxiliary" if expected == "non_business_attachment" else "unresolved"):
                    _invalid("附件处置与保全记录不一致")
            if reason == table_archive.UNREADABLE_REASON and role != "attachment":
                _invalid("不可读记录必须对应原始附件")
            intake_role = details.get(table_archive.INTAKE_ROLE_DETAIL)
            if reason == trial_delayed_pair.COVERAGE_REASON and (
                    intake_role != trial_delayed_pair.ROLE or disposition != "unresolved" or source_row != 1 or row_count != 1):
                _invalid("清算对应表的覆盖缺口记录无效")
            if (intake_role is not None and (not isinstance(intake_role, str)
                    or intake_role not in table_archive.INTAKE_DECLARED_ROLES
                    or table_archive.INTAKE_DECLARED_ROLES[intake_role] != details.get("source_role"))):
                _invalid("材料声明用途与来源角色不符")
            if (role == "auxiliary" and not auxiliary_disposition
                    or role == "table" and reason == "auxiliary_table"
                    or role == "auxiliary" and not field_description and details.get("source_role") not in (
                        "account", "client", "manifest", "subaccount", "freeze",
                        *(("description",) if intake_role in {"description", *trial_delayed_pair.DELAYED_ROLES,
                                                              trial_delayed_pair.SAME_INSTANT_ROLE} else ()))
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
                if adopted_reference is not None:
                    _verify_package_reference_fields(details, flow, source_id, reference_columns, member_digest)
                    if occurrence is not None and reference_occurrences.setdefault((source_id, flow_id), occurrence) != occurrence:
                        _invalid("不同独立凭证记录不能链接为同一流水")
                if has_cancellation_profile:
                    known_facts.setdefault(key, []).append((source_id, flow))
                if cancellation_rule:
                    current_facts[source_id, number] = flow
            elif flow_id is not None and not (ledger_rule and ledger_entry
                    and ledger_entry["kind"] != "context" and role == "table"
                    and (ledger_entry["kind"] == "ledger" or cancelled_original)
                    and linked_disposition in accepted):
                _invalid("非有效交易记录不能关联分析流水")
            if cancellation_rule:
                evidence_records.setdefault(source_id, []).append({
                    "record_no": number, "member_path": path, "source_row": source_row,
                    "record_key": key, "disposition": disposition,
                    "reason_code": reason, "details": details,
                })
    if any(deposit_terms.get(source_id) != {"存款", "取款"} for source_id in deposit_sources):
        _invalid("存取款字面依据缺少同表区的原词对照")
    if any(sequences[source_id] != count for source_id, count in reference_counts.items()):
        _invalid("同一原表的编号用途未覆盖全部来源行")
    physical_layout_sources = {source_id for source_id, _ in physical_layouts}
    if not field_description_sources <= physical_layout_sources:
        _invalid("字段说明缺少同原件的已确认物理表区")
    for source_id, member_path, scope in wrong_field_notes:
        target = physical_layouts.get((source_id, scope["member_path"]), {}).get(scope["header_row"])
        if ((source_id, member_path) not in field_description_members or target is None
                or target[0]["policy"] != scope["policy"] or scope["target_row"] > target[0]["last_row"]):
            _invalid("字段说明错址不属于同原件已确认的数据区")
    if any(reference[0] in physical_layout_sources and values[0] == "table" and reference not in physical_layouts
           for reference, values in member_rows.items()):
        _invalid("已确认原件存在未登记物理映射的交易表")
    for member, regions in physical_layouts.items():
        boundaries = sorted(regions)
        for header, following in zip(boundaries, [*boundaries[1:], previous_source_rows[member] + 1]):
            layout, count = regions[header]
            if layout["last_row"] != following - 1 or count != following - header - 1:
                _invalid("已确认物理映射的表区末行或来源行数不闭合")
    if any(counts.get(reference, 0) != values[2] for reference, values in member_rows.items()):
        _invalid("自动表格成员记录数不闭合")
    for reference, observations in pdf_observations.items():
        _, digest, count = member_rows[reference]
        if len(observations) != count:
            _invalid("归档 PDF 来源观察行数不闭合")
        try:
            restored = pdf_text_observations.replay_journal(
                [details for _, details in observations], digest, pdf_member_sizes[reference], reference[1], archive=True)
            if any(record["reason_code"] != reason for (reason, _), record in zip(observations, restored["records"], strict=True)):
                raise ValueError("archive PDF observation reason changed")
        except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError, RecursionError):
            _invalid("归档 PDF 来源观察原位无效")
    proved_sources = set()
    for (source_id, path), journal in tiered_members.items():
        journal.finish(previous_source_rows.get((source_id, path), 0))
        valid_proof = journal.has_grouped_evidence if journal.allow_groups else journal.has_evidence
        if valid_proof:
            proved_sources.add(source_id)
    if proved_sources != set(tiered_sources):
        _invalid("分层来源缺少实际表头边界证明")
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
    source = io.BytesIO(content) if isinstance(content, (bytes, bytearray, memoryview)) else content
    source.seek(0, os.SEEK_END)
    if source.tell() > MAX_PACKAGE_BYTES:
        _invalid("压缩文件不能超过 512MiB")
    source.seek(0)
    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            if (len(infos) > MAX_PACKAGE_MEMBERS
                    or sum(item.file_size for item in infos) > MAX_PACKAGE_EXPANDED_BYTES):
                _invalid("ZIP 成员数量或展开大小超出处理范围")
            names = [item.filename for item in infos]
            if len(set(names)) != len(names):
                _invalid("ZIP 中有重复文件")
            allowed = {"meta.json", *(f"{t}.parquet" for t in _ALL_PKG_COLS)}
            for item in infos:
                name = item.filename
                limit = MAX_PACKAGE_META_BYTES if name == "meta.json" else MAX_PACKAGE_MEMBER_BYTES
                if not 0 <= item.file_size <= limit:
                    _invalid("ZIP 成员大小超出处理范围")
                if name not in allowed and not (name.startswith("photos/") and photo_name(name[7:])):
                    _invalid("包含格式之外的文件或路径")
                if item.is_dir() or item.flag_bits & 1 or (item.external_attr >> 16) & 0o170000 == 0o120000:
                    _invalid("不支持目录、加密文件或符号链接")
            if not {"meta.json", "flows.parquet"}.issubset(names):
                _invalid("缺少 meta.json / flows.parquet")
            with archive.open("meta.json") as member:
                meta = json.loads(member.read(MAX_PACKAGE_META_BYTES + 1).decode("utf-8"))
            if not isinstance(meta, dict) or type(meta.get("format")) is not int or meta["format"] not in (1, 2, 3, 4, 5, 6, 7):
                _invalid("不支持的格式版本")
            source_members = {f"{table}.parquet" for table in PROVENANCE_PKG_COLS} & set(names)
            if source_members and (meta["format"] not in (2, 3, 4, 5, 6, 7) or len(source_members) != 2):
                _invalid("来源追溯表必须在格式 2、3、4 中成对提供")
            journal_members = {f"{table}.parquet" for table in JOURNAL_PKG_COLS} & set(names)
            if journal_members and (len(journal_members) != 2 or len(source_members) != 2):
                _invalid("反馈包日志须与来源追溯表成对提供")
            if meta["format"] in (2, 3, 4, 5, 6, 7) and meta.get("source_files") != "not_included":
                _invalid("来源文件策略无效")
            if not isinstance(meta.get("name"), str) or not meta["name"].strip():
                _invalid("案件名无效")
            paths, photos = {}, {}
            for name in names:
                if not (name.endswith(".parquet") or name.startswith("photos/")):
                    continue
                path = directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as member, path.open("xb") as target:
                    shutil.copyfileobj(member, target, 1024 * 1024)
                if name.endswith(".parquet"):
                    paths[name[:-8]] = path
                else:
                    photos[name[7:]] = path
    except (zipfile.BadZipFile, UnicodeError, json.JSONDecodeError, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        _invalid(f"ZIP 或元数据损坏（{type(exc).__name__}）")

    try:
        with duckdb.connect(config={"threads": 1, "memory_limit": "256MiB",
                                   "temp_directory": str(directory / "validation-spill"),
                                   "max_temp_directory_size": "2GiB"}) as check:
            rows = sum(_parquet_row_count(check, path) for path in paths.values())
            if rows > MAX_PACKAGE_ROWS:
                _invalid("Parquet 总行数超出处理范围")
            for table, path in paths.items():
                cols = [c.strip() for c in (ZONED_FLOW_COLS if table == "flows" and meta["format"] == 7 else _ALL_PKG_COLS[table]).split(",")]
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
                if table == "flows":
                    if meta["format"] != 7:
                        for field in ("trade_time_utc", "trade_time_original", "trade_time_offset"):
                            check.execute(f"alter table flows add column {field} {_TYPES.get(field, 'varchar')}")
                    from . import import_validation
                    for row in _query_package_rows(check, "select * from flows where trade_time_utc is not null or trade_time_original is not null or trade_time_offset is not null"):
                        try:
                            import_validation._facts(row)
                        except (ValueError, TypeError, KeyError):
                            _invalid("交易时间原文、壁钟、偏移与UTC不一致")
                key = ("id" if table in ("flows", "batches", "canvases")
                       else "flow_id,canvas_id" if table == "tracked_flows"
                       else "source_id" if table in ("import_sources", "evidence_sources", "import_source_operations")
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
                    from . import pdf_import, pdf_text_import, pdf_text_observations, table_import, table_archive
                    pdf_profiles = {pdf_import.PROFILE: pdf_import, pdf_text_import.PROFILE: pdf_text_import,
                                    **dict.fromkeys(pdf_text_observations.PROFILES, pdf_text_observations)}
                    pdf_source = parser_version in pdf_profiles
                    table_source = (row_kind == "table_record" or parser_version in (
                        *table_import.SUPPORTED_PROFILES, *table_archive.SUPPORTED_PROFILES))
                    archive_source = (suffix == ".zip" or row_kind == "archive_record"
                                      or isinstance(parser_version, str) and parser_version.startswith("funds-trace-bank-feedback-"))
                    if pdf_source:
                        pdf_profile = pdf_profiles[parser_version]
                        source_format_valid = pdf_profile.valid_source(filename, size_bytes, mode, mapping, sheet, row_kind)
                    elif table_source:
                        # Uploads already default an empty suffix to CSV while
                        # preserving the original name. Admit only the exact
                        # single-text-member contract; full journal and fact
                        # validation below still applies.
                        suffixless_text = (row_kind == "table_record" and suffix == "" and sheet == ""
                                           and parser_version in table_import.SUPPORTED_PROFILES
                                           and parser_version not in table_import.STANDALONE_XLS_PROFILES
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
                                               and (parser_version not in {table_tiered_headers.PROFILE, *table_import.TRIAL_XLSX_PROFILES}
                                                    or suffix == ".xlsx")
                                               and (parser_version != table_import.CODED_BANK_CSV_TRIAL_PROFILE or suffix == ".csv")
                                               and ((suffix in (".zip", ".rar") and parser_version in table_archive.SUPPORTED_PROFILES and sheet == ""
                                                     and (suffix == ".zip" or parser_version in table_import.NATIVE_OBSERVATION_PROFILES))
                                                    or (suffix in (".csv", ".tsv", ".txt", ".xlsx", ".xls")
                                                        and parser_version in table_import.SUPPORTED_PROFILES
                                                        and (parser_version not in table_import.STANDALONE_XLS_PROFILES or suffix == ".xls")
                                                        and (suffix in (".xlsx", ".xls")) == bool(sheet))
                                                    or suffixless_text))
                        if parser_version == table_import.CSV_TAIL_SOURCE_PROFILE:
                            source_format_valid = (source_format_valid and mode == "B" and not mapping and not sheet
                                                   and (suffix == ".csv" or suffixless_text))
                        if parser_version in table_import.DEPOSIT_CSV_TRIAL_PROFILES:
                            direction = mapping.get("direction") if isinstance(mapping, dict) else None
                            source_format_valid = (source_format_valid and mode == "B" and not sheet
                                                   and (suffix == ".csv" or suffixless_text)
                                                   and isinstance(direction, str)
                                                   and table_import._norm(direction) in table_import.DEPOSIT_DIRECTION_HEADINGS)
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
                            or (not table_source and not pdf_source and not importer._validate(mapping, mode))
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
            has_operations = "import_source_operations" in paths
            if meta["format"] == 7 and "import_sources" in paths and not has_operations:
                _invalid("有来源的格式7须保留导入操作关联")
            if meta["format"] != 7 and has_operations != (meta["format"] == 6):
                _invalid("导入操作关联须使用格式6保留支持生命周期")
            if has_operations:
                if "import_sources" not in paths or "batches" not in paths:
                    _invalid("导入操作缺少来源或批次")
                if check.execute("""select 1 from import_source_operations o
                      left join import_sources s on s.source_id=o.source_id
                      left join batches b on b.id=o.batch_id
                      where s.source_id is null or o.active is null
                        or (o.active and b.id is null) or (not o.active and b.id is not null) limit 1""").fetchone():
                    _invalid("导入操作引用无效")
                if check.execute("""select 1 from flow_sources f join import_source_operations o using(source_id)
                      where not o.active limit 1""").fetchone():
                    _invalid("已撤回的导入操作仍含活动来源链接")
                if has_resolution and check.execute("""select 1 from evidence_sources e
                      left join import_source_operations o using(source_id)
                      where o.source_id is null or o.active is distinct from e.active
                        or o.batch_id is distinct from e.batch_id limit 1""").fetchone():
                    _invalid("身份证据与导入操作关联不一致")
            if meta["format"] not in (6, 7) and has_resolution != (meta["format"] == 5):
                _invalid("身份证据规则须使用格式5保留完整语义")
            if meta["format"] not in (6, 7) and has_tables != (meta["format"] == 4 or meta["format"] == 5 and has_tables):
                _invalid("自动表格记录须使用格式4保留完整语义")
            if meta["format"] not in (6, 7) and not has_tables and not has_resolution and has_cancellations != (meta["format"] == 3):
                _invalid("配对冲正规则须使用格式3保留完整语义")
            if "import_sources" in paths:
                for table in JOURNAL_PKG_COLS:
                    if table in paths and check.execute(f"select 1 from {table} j left join import_sources s on s.source_id=j.source_id where s.source_id is null or s.row_kind not in ('archive_record','table_record','pdf_record','pdf_text_record','pdf_observation_record') limit 1").fetchone():
                        _invalid("记录日志引用了不存在或不支持的来源")
                _feedback_journals(check, paths)
                _table_journals(check, paths)
                _pdf_journals(check, paths)
                _pdf_text_journals(check, paths)
            from .services import materials
            source_ids={r[0] for r in check.execute('select source_id from import_sources').fetchall()} if meta.get('materials') is not None and 'import_sources' in paths else set()
            material_groups=materials.validate_package(meta.get('materials'),source_ids)
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
        _invalid(f"Parquet 数据损坏、字段无效或超出处理预算（{type(exc).__name__}）")
    return meta["name"].strip(), paths, photos, material_groups


def import_package(content):
    with tempfile.TemporaryDirectory(prefix="fundcase-import-") as tmp:
        name, paths, photos, material_groups = _validate_package(content, Path(tmp))
        photo_root = db.DATA_DIR / "photos"
        photo_root.mkdir(parents=True, exist_ok=True)
        # Stage on the destination filesystem; directory publication is one rename.
        with tempfile.TemporaryDirectory(prefix=".fundcase-", dir=photo_root) as stage:
            staged = Path(stage) / "photos"
            staged.mkdir()
            for filename, source in photos.items():
                shutil.copyfile(source, staged / filename)
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
                    available = {item[0] for item in con.execute("describe select * from read_parquet(?)", [str(paths["flows"])]).fetchall()}
                    cols = [c.strip() for c in ZONED_FLOW_COLS.split(",") if c.strip() in available and c.strip() not in ("id", "batch_id")]
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
                        if material_groups:
                            from .services import materials
                            source_mapping=dict(con.execute(f'select old_id,new_id from {mapping}').fetchall())
                            materials.restore(new_cid,material_groups,source_mapping)
                    if "evidence_sources" in paths:
                        con.execute(f"""insert into evidence_sources(case_id,source_id,batch_id,active)
                            select ?,s.new_id,b.new_id,p.active from read_parquet(?) p
                            join {mappings['import_sources']} s on s.old_id=p.source_id
                            left join {mappings['batches']} b on b.old_id=p.batch_id""", [new_cid,str(paths["evidence_sources"])])
                    # Format 5 already carries explicit operation evidence;
                    # older ordinary sources have no portable batch mapping.
                    operation_table = ("import_source_operations" if "import_source_operations" in paths
                                       else "evidence_sources" if "evidence_sources" in paths else None)
                    if operation_table:
                        con.execute(f"""insert into import_source_operations(case_id,source_id,batch_id,active)
                            select ?,s.new_id,b.new_id,p.active from read_parquet(?) p
                            join {mappings['import_sources']} s on s.old_id=p.source_id
                            left join {mappings['batches']} b on b.old_id=p.batch_id""", [new_cid,str(paths[operation_table])])
                    from . import source_ledger
                    source_ledger.refresh_index(con, new_cid)
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
    from .services import trial_review_receipts

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
                for table in ("source_ledger_index", "flow_source_cards", "analysis_state", "event_projection_cache_state", "event_observation_cache", "event_state", "resolved_events", "event_members", "evidence_sources", "import_source_operations", "import_materials", "import_material_groups", "resolution_state", "resolution_revisions", "resolution_entities", "resolved_endpoints", "resolution_subject_aliases", "import_records", "import_members", "flow_sources", "import_sources", "import_sessions"):
                    con.execute(f"delete from {table} where case_id=?", [case_id])
                for table in CASE_PKG_TABLES:
                    con.execute(f"delete from {table} where case_id=?", [case_id])
                trial_review_receipts.delete_case(case_id)
                con.execute("delete from cases where id=?", [case_id])
        except BaseException:
            if had_photos:
                shutil.copytree(backup, photo_dir, dirs_exist_ok=True)
            raise
    return {"ok": True, "deleted_flows": deleted}


def _query_package_rows(connection, sql):
    cursor = connection.execute(sql)
    names = [item[0] for item in cursor.description]
    while rows := cursor.fetchmany(1024):
        for row in rows:
            yield dict(zip(names, row))
