"""Persistent import workflow shared by the standalone HTTP surfaces.

The database owns session state; disk filenames never grant access. A completed
receipt is committed with its flows and remains readable after source cleanup.
"""
import asyncio
from bisect import bisect_right
import datetime as dt
import csv
from contextlib import closing, nullcontext
from copy import deepcopy
from decimal import Decimal, localcontext
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import tempfile
import uuid
import zipfile
from itertools import islice
from urllib.parse import urlencode

import anyio

from app import db, importer, feedback_import, table_import, table_archive, import_validation, evidence_resolution
from app import import_preview_cache, pdf_import, pdf_text_import, pdf_text_observations, rar_archive, table_direction_recheck
from app.table_mapping import ACCOUNT_CARD_FIELDS, document_number_header
from .cases import ServiceError, preview_flow, require_case
from . import import_ready_evidence
from .main_accounts import mark_automatic


OWNER_TOKEN = uuid.uuid4().hex
TABLE_PROFILE = table_import.PROFILE
_CONFIRMED_LAYOUT = "_confirmed_layout_v1"
SOURCE_CURRENCY_INTERPRETATION_KEY = "_source_currency_interpretation"
SOURCE_CURRENCY_INTERPRETATION_VERSION = 1
_TRIAL_LAYOUT_DECISION = "_trial_layout_decision_v1"
_INTAKE_POLICY = "intake-v1"
_INTAKE_KEY = "_intake_policy"
_INTAKE_ITEMS = "_intake_items_v1"
_PDF_MEMBER_OBSERVATIONS = "_pdf_member_observations_v1"
_NARRATIVE_DECISIONS = "_narrative_decisions_v1"
_ACCOUNT_USE_CODE_TRIAL = "_account_use_code_trial_v1"
_REFERENCE_COLUMNS_TRIAL = "_verified_reference_columns_v1"
_TRANSACTION_STATUS_POLICY = "_transaction_status_policy"
_REFERENCE_MODEL_BASIS = "model_verified_source_columns"
_REFERENCE_RULE_BASIS = "rule_verified_source_columns"
_PDF_TEXT_READERS = {
    pdf_text_import.PROFILE: pdf_text_import, **dict.fromkeys(pdf_text_observations.PROFILES, pdf_text_observations),
}
_PDF_PROFILES = (pdf_import.PROFILE, *_PDF_TEXT_READERS)
_extensions = {".csv", ".txt", ".tsv", ".xlsx", ".xls", ".zip", ".rar"}
_max_upload = 300 * 1024 * 1024
_preview_lock = threading.Lock()
JOURNAL_CHUNK_RECORDS = 5_000
JOURNAL_CHUNK_BYTES = 8 * 1024 * 1024
_active_stages = ("parsing", "importing")
_messages = {"parsing": "解析文件…", "importing": "写入流水…", "done": "导入完成",
             "error": "导入失败，请检查文件与列映射后重试", "ready": "等待确认导入"}
_errors = {"import_interrupted": "上次导入被中断，未完成的数据已回滚，请确认后重试",
           "pdf_observations_changed": "PDF 来源观察无法按预览重新核对，请重新预览保留的原件",
           "layout_preview_changed": "已确认物理表区的案件来源发生变化，请上传原件重新核对",
           "source_changed": "上传文件校验失败，请重新上传文件",
           "reference_original_active": "同一原件已有活动导入。请查看原回执或撤回旧导入后重新确认。",
           "source_missing": "上传文件已清理或不存在，请重新上传文件",
           "evidence_context_overlap": "相同原件的来源范围发生变化，无法安全衔接，未重复入账；请仅补辅助资料或在新案件导入完整目录",
           "trial_occurrence_conflict": "原件与历史流水的来源关联存在冲突，已停止本次导入",
           "no_importable_rows": "没有可导入的数据行，请检查列映射",
           "import_integrity_failed": "入库一致性校验失败，本次写入已全部回滚；原有数据未改动，请重试或联系维护人员",
           "import_failed": _messages["error"]}


def _now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _json(value):
    from app.import_staging import json_default
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=json_default)


def _close_staging(parsed):
    if parsed is not None:
        for name in ("table_plan", "feedback_plan"):
            plan = parsed[0].attrs.get(name)
            pool = plan.pop("_details_pool", None) if plan is not None else None
            if pool is not None:
                pool.close()
            store = plan.pop("_plan_store", None) if plan is not None else None
            if store is not None:
                store.close()


class _JournalRecords:
    """Replay a finalized operation journal without retaining its dictionaries.

    Only commit uses this after binding, cancellation proof and summary capture.
    Anonymous temporary storage is never a registered or reusable source file.
    """
    def __init__(self, records):
        self._stream = tempfile.TemporaryFile(mode="w+b", prefix="funds-trace-journal-", dir=db.DATA_DIR)
        self._count = len(records)
        digest = hashlib.sha256()
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(self._stream.fileno(), 0o600)
            for index in range(self._count):
                encoded = (_json(records[index]) + "\n").encode("utf-8")
                if self._stream.write(encoded) != len(encoded):
                    raise OSError("Incomplete temporary journal write")
                digest.update(encoded)
                # The plan and parser share this same list; leave no second
                # reference to all finalized source dictionaries during write.
                records[index] = None
            records.clear()
            self._stream.flush()
            self._digest = digest.digest()
        except BaseException:
            self._stream.close()
            raise

    def __len__(self):
        return self._count

    def __iter__(self):
        self._stream.seek(0)
        digest, count = hashlib.sha256(), 0
        for encoded in self._stream:
            digest.update(encoded)
            count += 1
            if count > self._count:
                raise importer.ImportError("来源语义临时快照不完整")
            yield json.loads(encoded)
        if count != self._count or digest.digest() != self._digest:
            raise importer.ImportError("来源语义临时快照不完整")

    def close(self):
        self._stream.close()


def _invalid_file():
    return ServiceError(400, "invalid_import_file", "无法读取流水文件，请检查文件格式、交易时间和列映射")


def _known_file_failure(error):
    """Trust only explicit format errors and their complete format-only chain."""
    known = (table_import.TableImportError, feedback_import.FeedbackImportError,
             csv.Error, UnicodeError, zipfile.BadZipFile)
    visiting, checked = set(), set()

    def visit(value):
        identity = id(value)
        if identity in visiting or not isinstance(value, known):
            return False
        if identity in checked:
            return True
        visiting.add(identity)
        if any(child is not None and not visit(child)
               for child in (value.__cause__, value.__context__)):
            return False
        visiting.remove(identity)
        checked.add(identity)
        return True

    return visit(error)


def _missing():
    return ServiceError(404, "import_not_found", "导入会话不存在或不属于当前案件，请重新上传文件")


def _cid(case_id):
    return require_case(str(case_id))


def _reject_document_number_mapping(mapping):
    if any(field in ACCOUNT_CARD_FIELDS and document_number_header(table_import._norm(column))
           for field, column in mapping.items()):
        raise ServiceError(422, "invalid_mapping", "证件号码列不能映射为交易账号或卡号")


def _mapping(mode, mapping, columns):
    if mode not in importer.MODE_FIELDS or not isinstance(mapping, dict):
        raise ServiceError(422, "invalid_mapping", "流水模式或列映射无效，请重新预览")
    allowed = set(importer.MODE_FIELDS[mode]) | {"_time2", "self_account", "self_name"}
    if any(key not in allowed or not isinstance(column, str) or (column and column not in columns)
           for key, column in mapping.items()):
        raise ServiceError(422, "invalid_mapping", "列映射包含未知字段或文件中不存在的列，请重新预览")
    result = {key: column for key, column in mapping.items() if column}
    _reject_document_number_mapping(result)
    if not importer._validate(result, mode):
        raise ServiceError(422, "invalid_mapping", "列映射缺少交易时间、金额或交易双方，请补全后重新预览")
    return result


def _directory(create=False):
    root = Path(db.DATA_DIR).resolve()
    directory = root / "uploads"
    if create:
        directory.mkdir(exist_ok=True)
    if directory.is_symlink() or directory.resolve() != directory:
        raise ServiceError(409, "unsafe_upload_storage", "上传存储不可用，请联系维护人员")
    return directory


def _path(import_id, suffix):
    if not isinstance(import_id, str) or not re.fullmatch(r"[0-9a-f]{32}", import_id) or suffix not in {*_extensions, ".pdf"}:
        raise _missing()
    return _directory() / f"{import_id}{suffix}"


def _source(session, *, snapshot=False):
    """Verify bytes from one no-follow FD; optionally retain that exact stream.

    Callers parsing or sending data use the anonymous snapshot instead of
    reopening the canonical path after validation. Commit verification omits
    the snapshot copy before the background worker starts.
    """
    if session["source_state"] != "retained":
        raise ServiceError(409, "source_missing", _errors["source_missing"])
    path = _path(session["import_id"], session["suffix"])
    digest = hashlib.sha256()
    size = 0
    verified = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b", dir=db.DATA_DIR) if snapshot else None
    try:
        try:
            if path.is_symlink():
                raise OSError("Symlink upload")
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode):
                    raise OSError("Not a regular upload")
                if opened.st_size != session["size_bytes"]:
                    raise ServiceError(409, "source_changed", _errors["source_changed"])
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    size += len(block)
                    if size > session["size_bytes"]:
                        raise ServiceError(409, "source_changed", _errors["source_changed"])
                    digest.update(block)
                    if verified is not None:
                        verified.write(block)
        except OSError:
            raise ServiceError(409, "source_missing", _errors["source_missing"]) from None
        if size != session["size_bytes"] or digest.hexdigest() != session["sha256"]:
            raise ServiceError(409, "source_changed", _errors["source_changed"])
        if verified is not None:
            verified.seek(0)
        return verified
    except BaseException:
        if verified is not None:
            verified.close()
        raise


def _session(case_id, import_id, surface="v1", bind=False):
    if not isinstance(import_id, str) or not re.fullmatch(r"[0-9a-f]{32}", import_id):
        raise _missing()
    # A progress request may own a committed read-only snapshot while the
    # importer writes. Legacy binding still requires the writable transaction.
    context = db.read_lock() if not bind and db.in_read_snapshot() else db.transaction()
    with context:
        cid = _cid(case_id) if case_id is not None else None
        rows = db.q_raw("select * from import_sessions where import_id=? and surface=?", [import_id, surface])
        if not rows:
            raise _missing()
        row = rows[0]
        if row["case_id"] is None and bind and cid is not None and surface == "legacy":
            db.x("update import_sessions set case_id=?,updated_at=? where import_id=? and case_id is null",
                 [cid, _now(), import_id])
            row["case_id"] = cid
        if row["case_id"] != cid:
            raise _missing()
        return row


def _accounts(rows):
    seen = {}
    for row in rows:
        account = (row.get("self_account") or "").strip()
        if account:
            entry = seen.setdefault(account, {"account": account, "name": (row.get("self_name") or "").strip(), "count": 0})
            entry["count"] += 1
    return sorted(seen.values(), key=lambda entry: -entry["count"])


def _parse(source, mode=None, mapping=None, *, suffix=None, feedback_profile=None, table_profile=None,
           reference_policy=None, verified_reference=None, _auto_event_profile=False, manual_mapping_evidence=False, confirmed_layout=None,
           intake_policy=None, pdf_member_observations=False, narrative_decisions=False, trial_members=False,
           transaction_status_policy=None):
    # Never retain a previous large preview beside a newly parsed plan.
    import_preview_cache.discard()
    status_option = {"transaction_status_policy": transaction_status_policy} if transaction_status_policy else {}
    try:
        if reference_policy is not None and table_profile not in table_import.VOUCHER_REFERENCE_PROFILES:
            raise ServiceError(422, "invalid_reference_policy", "引用策略仅适用于新版单文件表格，请重新上传预览")
        if table_profile not in (None, importer.PARSER_VERSION, *table_import.SUPPORTED_PROFILES, *table_archive.SUPPORTED_PROFILES):
            raise ServiceError(400, "invalid_table_file", "来源解析规则不受支持，请重新上传预览")
        if table_profile in (*table_import.SUPPORTED_PROFILES, *table_archive.SUPPORTED_PROFILES):
            if suffix == ".rar" and not rar_archive.is_rar(source):
                raise table_import.TableImportError("RAR文件签名不符，整包未接纳")
            plan = (table_archive.direct_intake_auxiliary(source, suffix, narrative_decisions=narrative_decisions)
                    if intake_policy == _INTAKE_POLICY and not mapping and confirmed_layout is None else None)
            if plan is None:
                plan = (table_archive.parse(source, profile=table_profile, _stage=True, _operation_scopes=True,
                                            member_retention=(table_archive.MEMBER_RETENTION_POLICY
                                                              if intake_policy == _INTAKE_POLICY and table_profile in table_archive.EVIDENCE_PROFILES
                                                              else None),
                                            pdf_member_observations=pdf_member_observations,
                                            _trial_members=trial_members,
                                            narrative_decisions=narrative_decisions, **status_option) if table_profile in table_archive.SUPPORTED_PROFILES
                        else table_import.parse(source, suffix=suffix, mode=mode, mapping=mapping,
                                                profile=table_profile,
                                                voucher_reference=reference_policy is not None,
                                                _manual_mapping_evidence=manual_mapping_evidence,
                                                _confirmed_layout=confirmed_layout,
                                                _trial_xlsx=(intake_policy == _INTAKE_POLICY and _auto_event_profile
                                                             and table_profile == table_import.VOUCHER_REFERENCE_PROFILE
                                                             and suffix == ".xlsx"),
                                                _trial_csv=(intake_policy == _INTAKE_POLICY and _auto_event_profile
                                                            and table_profile == table_import.VOUCHER_REFERENCE_PROFILE
                                                            and suffix == ".csv"),
                                                **status_option,
                                                **({"_verified_reference": verified_reference} if verified_reference is not None else {}),
                                                **({"_auto_event_profile": True} if _auto_event_profile else {})))
            try:
                if plan["profile"] in evidence_resolution.PROFILES and "_plan_store" not in plan:
                    from app.import_staging import stage_plan
                    stage_plan(plan)
                frame = importer.pd.DataFrame(columns=plan["columns"])
                frame.attrs.update(source_row_kind=table_import.ROW_KIND, table_plan=plan)
                stats = dict(plan["stats"])
                stats["bad_examples"] = [{key: value for key, value in example.items()
                                          if key in ("row", "source_row", "reason")}
                                         for example in stats["bad_examples"]]
                return frame, plan["sheet"], plan["mode"], plan["mapping"], plan["rows"], stats
            except BaseException:
                pool = plan.pop("_details_pool", None)
                if pool is not None:
                    pool.close()
                if plan.get("_plan_store") is not None:
                    plan["_plan_store"].close()
                raise
        if suffix == ".zip":
            if mode is not None and (mode != "B" or mapping != feedback_import.MAPPING):
                raise ServiceError(422, "fixed_feedback_mapping", "资金反馈包使用已验证的固定格式规则，请重新上传预览")
            plan = feedback_import.parse(source, profile=feedback_profile or feedback_import.PROFILE, **status_option)
            frame = importer.pd.DataFrame(columns=feedback_import.COLUMNS)
            frame.attrs.update(source_row_kind="archive_record", feedback_plan=plan)
            stats = dict(plan["stats"])
            stats["bad_examples"] = [{"row": f"包内记录 {r['record_no']}", "source_row": r["record_no"],
                                      "reason": r["reason_code"]}
                                     for r in plan["records"] if r["disposition"] in ("review", "unresolved")][:3]
            return frame, "", "B", dict(feedback_import.MAPPING), plan["rows"], stats
        frame, sheet = importer.read_table(source, suffix=suffix)
        if mapping is None:
            mode, mapping = importer.analyze(frame, mode_hint=mode)
        mapping = _mapping(mode, mapping, list(frame.columns))
        rows, stats = importer.build_rows(frame, mode, mapping)
        return frame, sheet, mode, mapping, rows, stats
    except (feedback_import.FeedbackImportError, table_import.TableImportError) as error:
        code = "invalid_feedback_archive" if isinstance(error, feedback_import.FeedbackImportError) else "invalid_table_file"
        failure = ServiceError(400, code, str(error))
        failure.failure_kind = "file" if _known_file_failure(error) else "unknown"
        raise failure from None
    except ServiceError:
        raise
    except Exception:
        raise _invalid_file() from None


def _bind_feedback_plan(case_id, parsed):
    """Prevent changed auxiliary identity from adding the same physical money."""
    frame, _sheet, _mode, _mapping, rows, stats = parsed
    plan = frame.attrs.get("feedback_plan")
    if case_id is not None:
        evidence_resolution.reject_historical_overlap(case_id, plan)
    if (case_id is None or plan is None
            or not (rows or plan.get("cancelled_rows"))):
        return
    members = {member["member_path"]: member["sha256"] for member in plan["members"]}
    transaction_members = [(member["member_path"], member["sha256"])
                           for member in plan["members"] if member["role"] == "transaction"]
    wanted = ",".join("(?,?)" for _ in transaction_members)
    profiles = sorted(feedback_import.SUPPORTED_PROFILES)
    supported = ",".join("?" for _ in profiles)
    cancellation_profiles = sorted(feedback_import.CANCELLATION_PROFILES)
    cancellation_supported = ",".join("?" for _ in cancellation_profiles)
    existing = {}
    prior_cancellation_members = set()
    for previous in db.q_raw(f"""
            select distinct r.member_path,m.sha256,r.source_row,
                   case when p.parser_version in ({supported}) and p.row_kind='archive_record'
                             and (r.disposition in ('imported','duplicate') or
                                  (p.parser_version in ({cancellation_supported}) and r.disposition='cancelled'
                                   and json_extract_string(r.details_json,'$.cancellation_role')='original'))
                        then json_extract_string(r.details_json,'$.own_identity_sha256') else null end identity_hash,
                   (p.parser_version in ({cancellation_supported}) and r.disposition='cancelled'
                    and json_extract_string(r.details_json,'$.cancellation_role')='original') prior_cancellation
            from import_records r
            join import_sources p on p.case_id=r.case_id and p.source_id=r.source_id
            join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
            join (values {wanted}) wanted(member_path,sha256) on wanted.member_path=m.member_path and wanted.sha256=m.sha256
            left join flow_sources s on s.case_id=r.case_id and s.source_id=r.source_id and s.source_row=r.record_no
            left join flows f on f.case_id=s.case_id and f.id=s.flow_id
            where r.case_id=? and (f.id is not null or
              (p.parser_version in ({cancellation_supported}) and r.disposition='cancelled'
               and json_extract_string(r.details_json,'$.cancellation_role')='original'))
            """, profiles + cancellation_profiles * 2
                  + [value for member in transaction_members for value in member] + [case_id] + cancellation_profiles):
        identity = previous["identity_hash"]
        if previous["prior_cancellation"]:
            prior_cancellation_members.add((previous["member_path"], previous["sha256"]))
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
            identity = None
        existing.setdefault((previous["member_path"], previous["sha256"], previous["source_row"]), set()).add(identity)
    # Historical v1 parsing remains unchanged for unrelated members. A pending
    # v1 job may not re-create an occurrence from a member with a v3/v4/v5 cancellation
    # under a different auxiliary identity and evade its effective exclusion.
    legacy_guard = plan["profile"] == feedback_import.IDENTITY_PROFILE
    if legacy_guard and not prior_cancellation_members:
        return
    records = {record["record_no"]: record for record in plan["records"]}
    changed = False
    conflicting_originals = []
    for row in list(plan.get("cancelled_rows", [])):
        record = records[row["_source_row"]]
        previous = existing.get((record["member_path"], members[record["member_path"]], record["source_row"]))
        if previous and previous != {record["details"]["own_identity_sha256"]}:
            conflicting_originals.append(record["record_no"])
    if conflicting_originals:
        feedback_import.revoke_cancellations(plan, conflicting_originals)
        changed = True
    kept = []
    for row in rows:
        record = records[row["_source_row"]]
        path, source_row = record["member_path"], record["source_row"]
        if legacy_guard and (path, members[path]) not in prior_cancellation_members:
            kept.append(row)
            continue
        previous = existing.get((path, members[path], source_row))
        if previous and previous != {record["details"]["own_identity_sha256"]}:
            changed = True
            record.update(disposition="review" if legacy_guard else "unresolved", reason_code="own_identity_conflict",
                          record_key=feedback_import.record_key(path, members[path], source_row))
            if legacy_guard:
                record["details"]["review_reasons"] = "own_identity_conflict"
            else:
                record["details"].update(financial_effect="undetermined", validation_issues="own_identity_conflict")
            for field in ("own_identity_sha256", "own_identity_record"):
                record["details"].pop(field, None)
        else:
            kept.append(row)
    if not changed:
        return
    rows[:] = kept  # The parsed tuple and plan deliberately share this list.
    if plan["profile"] in feedback_import.CANCELLATION_PROFILES:
        feedback_import.refresh_stats(plan)
        stats.clear()
        stats.update(plan["stats"])
        stats["bad_examples"] = [{"row": f"包内记录 {record['record_no']}", "source_row": record["record_no"],
                                  "reason": record["reason_code"]}
                                 for record in plan["records"] if record["disposition"] in ("review", "unresolved")][:3]
        return
    counts, reasons, totals, directions = {}, {}, {}, {}
    for record in plan["records"]:
        disposition, reason = record["disposition"], record["reason_code"]
        counts[disposition] = counts.get(disposition, 0) + 1
        reasons[reason] = reasons.get(reason, 0) + 1
    with localcontext() as context:
        context.prec = 40
        for row in rows:
            currency = row["currency"]
            totals[currency] = totals.get(currency, Decimal("0.00")) + row["amount"]
            by_direction = directions.setdefault(currency, {"in": Decimal("0.00"), "out": Decimal("0.00")})
            direction = "out" if records[row["_source_row"]]["details"]["source_direction"] == "出" else "in"
            by_direction[direction] += row["amount"]
    updated = plan["stats"]
    updated.update(ok=len(rows), reason_counts=reasons,
                   skipped=sum(counts.get(kind, 0) for kind in ("review", "unresolved", "non_monetary", "non_transaction")),
                   currency_totals={currency: format(amount, ".2f") for currency, amount in totals.items()},
                   currency_direction_totals={currency: {direction: format(amount, ".2f")
                                                         for direction, amount in values.items()}
                                              for currency, values in directions.items()})
    for kind in ("review", "unresolved", "non_monetary", "non_transaction", "auxiliary"):
        updated[kind] = counts.get(kind, 0)
    updated["bad_examples"] = [{"source_row": record["record_no"], "reason": record["reason_code"],
                                "member_path": record["member_path"], "member_row": record["source_row"]}
                               for record in plan["records"] if record["disposition"] in ("review", "unresolved")][:3]
    stats.clear()
    stats.update(updated)
    stats["bad_examples"] = [{"row": f"包内记录 {record['record_no']}", "source_row": record["record_no"],
                              "reason": record["reason_code"]}
                             for record in plan["records"] if record["disposition"] in ("review", "unresolved")][:3]


def _table_history_conflicts(plan, rows, stats, conflicting, *, basis=table_import.HISTORY_CONFLICT_BASIS):
    if not conflicting:
        return
    for number in conflicting:
        record = plan["records"][number - 1]
        record.update(disposition="unresolved", reason_code="conflicting_field")
        record["details"].pop("flow_sha256", None)
        record["details"].update(financial_effect="undetermined", validation_issues="conflicting_field",
                                 counterparty_basis=basis,
                                 reason="同一来源已存在不同资金解释，本次未新增流水，防止重复累计")
    from app.import_staging import StagedSequence
    if isinstance(rows, StagedSequence):
        rows.remove_numbers(conflicting)
    else:
        rows[:] = [row for row in rows if row["_source_row"] not in conflicting]
    if plan["profile"] in table_archive.CANCELLATION_PROFILES:
        from app import table_cancellations
        dependent = plan["_plan_store"].mapping() if plan.get("_plan_store") is not None else set()
        for pair in plan.get("cancellations", []):
            if pair["witness_record_no"] in conflicting:
                dependent.add(pair["original_record_no"])
        if dependent:
            table_cancellations.revoke_pairs(plan, dependent, reason="cancellation_historical_conflict")
        else:
            table_cancellations.refresh_stats(plan)
    else:
        table_import.refresh_stats(plan)
    stats.clear()
    stats.update(plan["stats"])
    stats["bad_examples"] = [{key: value for key, value in example.items() if key in ("row", "source_row", "reason")}
                             for example in stats["bad_examples"]]


def _ordinary_csv_member(profile, source, member):
    """The same closed text-CSV eligibility used by D01, never an Excel alias."""
    from app.table_cancellation_links import ordinary_csv_member
    return ordinary_csv_member(profile, source, member)


def _bind_archive_header_history(case_id, plan, rows, stats, source):
    """Compare direct physical identities when archive-v4 has assessed them.

    Aggregate history once per identity. A plain CSV bridge does not make two
    different archive member paths equivalent, and no historical fact changes.
    """
    if not rows:
        return
    archive = plan["profile"] in table_archive.SUPPORTED_PROFILES
    members = {member["member_path"]: member for member in plan["members"]}
    ordinary = {path for path, member in members.items()
                if _ordinary_csv_member(plan["profile"], source, member)}
    if not archive and not ordinary:
        return
    fields = (*table_import.STORED_FLOW_FIELDS, "self_account", "self_name")
    def signature(row, default_currency=""):
        return (importer._row_key(row, default_currency=default_currency),
                row.get("self_account") or "", row.get("self_name") or "")
    header_profiles = (table_archive.HEADER_PROFILE, table_archive.GUARDED_PROFILE, table_archive.PRECISE_PROFILE, table_archive.REFERENCE_ROLE_PROFILE, table_archive.OPTIONAL_REFERENCE_PROFILE, table_archive.TIME_ROLE_PROFILE, table_archive.ZONED_PROFILE, table_archive.SOURCE_CONTRACT_PROFILE, table_archive.SOURCE_TEXT_PROFILE, table_archive.BANK31_VARIANT_PROFILE, table_archive.SOURCE_LEDGER_PROFILE, table_archive.POSTING_PAIR_PROFILE, table_archive.AUXILIARY_SCOPE_PROFILE, table_archive.NATIVE_OBSERVATION_PROFILE, table_archive.UNORIENTED_PROFILE, table_archive.NATIVE31_PROFILE, table_archive.SOURCE_OBSERVATION_PROFILE, table_archive.AUXILIARY_ROWS_PROFILE, *table_archive.BANK_SOURCE_CSV_PROFILES)
    profiles = sorted(table_import.SUPPORTED_PROFILES | table_archive.SUPPORTED_PROFILES)
    digests = sorted({member["sha256"] for member in members.values()})
    cursor = db.con().execute(f"""
        select i.parser_version,i.filename,i.sheet,i.row_kind,i.sha256,
          r.member_path,m.sha256,m.role,m.encoding,r.source_row,r.disposition,r.reason_code,
          f.id,{','.join('f.' + field for field in fields)}
        from import_records r
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
        join import_sources i on i.case_id=r.case_id and i.source_id=r.source_id
        left join flow_sources s on s.case_id=r.case_id and s.source_id=r.source_id and s.source_row=r.record_no
        left join flows f on f.case_id=s.case_id and f.id=s.flow_id
        where r.case_id=? and i.row_kind=? and i.parser_version in ({','.join('?' for _ in profiles)})
          and m.sha256 in ({','.join('?' for _ in digests)})
          and (i.parser_version in ({','.join('?' for _ in header_profiles)}) or r.disposition in ('imported','duplicate') and f.id is not null)
        """, [case_id, table_import.ROW_KIND, *profiles, *digests, *header_profiles])
    batch = cursor.fetchmany(1024)
    if not batch:
        return
    # Most new packages have no stored interpretations to compare. Only build
    # physical-position indexes when the same case has relevant history.
    store = plan.get("_plan_store")
    exact_wanted = store.mapping() if store is not None else {}
    for row in rows:
        record = plan["records"][row["_source_row"] - 1]
        path = record["member_path"]
        exact_wanted[path, members[path]["sha256"], record["source_row"]] = True
    bridge_wanted = store.mapping() if store is not None else {}
    for _path, digest, position in exact_wanted:
        bridge_wanted[digest, position] = True
    exact, bridge, v4_exact, v4_bridge, blocked_exact, blocked_bridge = [store.mapping() if store is not None else {} for _ in range(6)]
    different = "conflicting historical interpretations"
    while batch:
        for (profile, filename, sheet, row_kind, source_sha, path, digest, role, encoding,
             position, disposition, reason, fid, *facts) in batch:
            prior_archive = profile in table_archive.SUPPORTED_PROFILES
            prior_ordinary = _ordinary_csv_member(profile,
                {"filename": filename, "sheet": sheet, "row_kind": row_kind, "sha256": source_sha},
                {"member_path": path, "sha256": digest, "role": role, "encoding": encoding})
            if archive and prior_archive:
                key = (path, digest, position)
                if key not in exact_wanted:
                    continue
                history, presence, blocked = exact, v4_exact, blocked_exact
            elif prior_ordinary or ordinary and prior_archive:
                key = (digest, position)
                if key not in bridge_wanted:
                    continue
                history, presence, blocked = bridge, v4_bridge, blocked_bridge
            else:
                continue
            if profile in header_profiles:
                presence[key] = True
                if disposition == "unresolved" and reason in ("ambiguous_region_header", "invalid_currency"):
                    blocked[key] = True
            if fid is not None and disposition in ("imported", "duplicate"):
                value = signature(dict(zip(fields, facts)))
                history[key] = value if key not in history else history[key] if history[key] == value else different
        batch = cursor.fetchmany(1024)
    conflicting = store.mapping() if store is not None else set()
    for row in rows:
        number = row["_source_row"]
        record = plan["records"][number - 1]
        physical = (record["member_path"], members[record["member_path"]]["sha256"], record["source_row"])
        bridged = physical[1:]
        has_v4 = plan["profile"] in header_profiles or (physical in v4_exact if archive else bridged in v4_bridge)
        if not has_v4:
            continue
        value = signature(row, default_currency="CNY")
        if (physical in blocked_exact or bridged in blocked_bridge
                or physical in exact and exact[physical] != value
                or bridged in bridge and bridge[bridged] != value):
            conflicting.add(number)
    _table_history_conflicts(plan, rows, stats, conflicting, basis=table_import.ARCHIVE_HISTORY_CONFLICT_BASIS)


def _bind_table_history(case_id, plan, rows, stats):
    """A new interpretation of the same physical row is not a second payment.

    Preserve v5-to-v5 advanced mappings. A role-aware assessment on either side
    requires exact historical facts; an old ready session cannot duplicate one.
    """
    if not rows:
        return
    records = plan["records"]
    len(records)  # Preserve closed staged-record rejection without decoding.
    members = [(member["member_path"], member["sha256"]) for member in plan["members"]]
    wanted = ",".join("(?,?)" for _ in members)
    fields = table_import.STORED_FLOW_FIELDS
    role_profiles = sorted(table_import.ROLE_PROFILES)
    roles = ",".join("?" for _ in role_profiles)
    cursor = db.con().execute(f"""
        select r.member_path,r.source_row,{','.join('f.' + field for field in fields)}
        from import_records r
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
        join (values {wanted}) wanted(member_path,sha256) on wanted.member_path=m.member_path and wanted.sha256=m.sha256
        join import_sources i on i.case_id=r.case_id and i.source_id=r.source_id
        join flow_sources s on s.case_id=r.case_id and s.source_id=r.source_id and s.source_row=r.record_no
        join flows f on f.case_id=s.case_id and f.id=s.flow_id
        where r.case_id=? and (? in ({roles}) or i.parser_version in ({roles}))
        """, [value for member in members for value in member]
              + [case_id, plan["profile"], *role_profiles, *role_profiles])
    batch = cursor.fetchmany(1024)
    if not batch:
        return
    store = plan.get("_plan_store")
    requested = store.mapping() if store is not None else {}
    for row in rows:
        record = records[row["_source_row"] - 1]
        requested[record["member_path"], record["source_row"]] = row
    conflicting = store.mapping() if store is not None else set()
    while batch:
        for path, position, *facts in batch:
            row = requested.get((path, position))
            if row is not None and importer._row_key(dict(zip(fields, facts))) != importer._row_key(row, default_currency="CNY"):
                conflicting.add(row["_source_row"])
        batch = cursor.fetchmany(1024)
    _table_history_conflicts(plan, rows, stats, conflicting)


def _bind_stored_cancellation_history(case_id, plan, rows, stats, source):
    """An old ready interpretation cannot add conflicting v4 original/negative facts."""
    from app import table_cancellation_links, table_cancellations
    members = {member["member_path"]: member for member in plan["members"]}
    ordinary = any(_ordinary_csv_member(plan["profile"], source, member) for member in members.values())
    archive = plan["profile"] in table_archive.SUPPORTED_PROFILES
    if not rows or not (ordinary or archive):
        return
    store = plan.get("_plan_store")
    expected = store.mapping() if store is not None else {}
    cursor = db.con().execute(f"""with recursive {table_cancellation_links.ctes(scoped=True)}
            select member_path,sha256,original_row,cancellation_row,original_details from a02_valid""", [case_id])
    while proofs := cursor.fetchmany(1024):
        for path, digest, original_row, cancellation_row, details in proofs:
            notes = json.loads(details)
            identity = (None if ordinary else path, digest)
            original = (*identity, original_row)
            values = expected.get(original, set())
            values.add((notes.get("flow_sha256"), notes.get("cancellation_own_account_sha256"), notes.get("cancellation_own_name_sha256")))
            expected[original] = values
            cancelled = (*identity, cancellation_row)
            values = expected.get(cancelled, set())
            values.add(None)
            expected[cancelled] = values
    if not expected:
        return
    conflicting = store.mapping() if store is not None else set()
    for row in rows:
        record = plan["records"][row["_source_row"] - 1]
        member = members[record["member_path"]]
        key = (None if ordinary else record["member_path"], member["sha256"], record["source_row"])
        actual = (table_import.flow_digest(row), table_cancellations.hash_value(row.get("self_account") or ""),
                  table_cancellations.hash_value(row.get("self_name") or ""))
        if key in expected and any(value != actual for value in expected[key]):
            conflicting.add(record["record_no"])
    _table_history_conflicts(plan, rows, stats, conflicting, basis=table_import.ARCHIVE_HISTORY_CONFLICT_BASIS)


def _bind_shared_cancellation_plan_history(case_id, plan, stats):
    """Recheck all three proof positions against direct actual history before write."""
    from app import table_cancellation_links, table_cancellations
    from app.import_staging import record_map
    records = record_map(plan)
    members = {member["member_path"]: member["sha256"] for member in plan["members"]}
    exact, bridge = {}, {}
    for pair in plan["cancellations"]:
        for role, field in (("original", "original_record_no"), ("cancellation", "cancellation_record_no"),
                            ("witness", "witness_record_no")):
            record = records[pair[field]]
            position = (record["member_path"], members[record["member_path"]], record["source_row"])
            item = (pair["original_record_no"], role, record["details"])
            exact.setdefault(position, []).append(item)
            bridge.setdefault(position[1:], []).append(item)
    digests = sorted({digest for _path, digest, _row in exact})
    fields = (*table_import.STORED_FLOW_FIELDS, "self_account", "self_name")
    cursor = db.con().execute(f"""select i.parser_version,i.filename,i.sheet,i.row_kind,i.sha256,
            r.member_path,m.sha256,m.role,m.encoding,r.source_row,r.disposition,
            ({table_cancellation_links.conflict_sql('i', 'r')}) validation_conflict,
            s.flow_id,f.id,f.amount<=0 and exists(select 1 from source_ledger_index l
                where l.case_id=f.case_id and l.flow_id=f.id) ledger_observation,
            json_extract_string(r.details_json,'$.source_ledger_evidence') historical_ledger,{','.join('f.' + field for field in fields)}
        from import_records r
        join import_sources i on i.case_id=r.case_id and i.source_id=r.source_id
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
        left join flow_sources s on s.case_id=r.case_id and s.source_id=r.source_id and s.source_row=r.record_no
        left join flows f on f.case_id=s.case_id and f.id=s.flow_id
        where r.case_id=? and m.sha256 in ({','.join('?' for _ in digests)})""", [case_id, *digests])
    conflicting = set()
    while batch := cursor.fetchmany(1024):
        for (profile, filename, sheet, row_kind, source_sha, path, digest, member_role, encoding,
             position, disposition, conflict, linked_id, fid, ledger_observation, historical_ledger, *facts) in batch:
            source = {"filename": filename, "sheet": sheet, "row_kind": row_kind, "sha256": source_sha}
            member = {"member_path": path, "sha256": digest, "role": member_role, "encoding": encoding}
            candidates = (bridge.get((digest, position), []) if _ordinary_csv_member(profile, source, member)
                          else exact.get((path, digest, position), []))
            actual = dict(zip(fields, facts)) if fid is not None else None
            for original, role, notes in candidates:
                invalid = conflict or linked_id is not None and fid is None or disposition in ("imported", "duplicate") and fid is None
                if actual is not None:
                    ledger_witness = ledger_observation and (role == "cancellation" or role == "witness"
                        and notes.get("cancellation_quality") in {"valid_zero", "negative_only"})
                    snapshot_value = notes.get("source_ledger_evidence") or historical_ledger
                    if ledger_witness and snapshot_value:
                        from app import source_ledger
                        snapshot = source_ledger.validate(snapshot_value)
                        invalid = invalid or snapshot["kind"] != "ledger" or table_import.flow_digest(actual) != table_import.flow_digest(snapshot["row"])
                        invalid = invalid or snapshot["signed_amount"] != notes.get("cancellation_amount")
                        invalid = invalid or snapshot["row"]["balance"] != notes.get("cancellation_balance")
                        invalid = invalid or snapshot["direction"] != notes.get("cancellation_direction")
                        invalid = invalid or snapshot["row"]["currency"] != notes.get("cancellation_currency")
                        invalid = invalid or dt.datetime.fromisoformat(snapshot["row"]["trade_time"]) != dt.datetime.fromisoformat(notes["cancellation_time"])
                    else:
                        invalid = invalid or role == "cancellation" or role == "witness" and notes.get("cancellation_quality") != "accepted"
                        invalid = invalid or table_import.flow_digest(actual) != notes.get("flow_sha256")
                    invalid = invalid or any(table_cancellations.hash_value(actual.get(field) or "") != notes.get(note)
                        for field, note in (("self_account", "cancellation_own_account_sha256"),
                                            ("self_name", "cancellation_own_name_sha256")))
                if invalid:
                    conflicting.add(original)
    if conflicting:
        table_cancellations.revoke_pairs(plan, conflicting, reason="cancellation_historical_conflict")
        stats.clear()
        stats.update(plan["stats"])


def _bind_table_plan(case_id, parsed, *, source=None):
    plan = parsed[0].attrs.get("table_plan")
    if plan is not None and plan["profile"] in _PDF_PROFILES:
        return
    store = plan.get("_plan_store") if plan is not None else None
    with store.working_maps() if store is not None and case_id is not None else nullcontext():
        _bind_table_plan_history(case_id, parsed, source=source)


def _bind_table_plan_history(case_id, parsed, *, source=None):
    """Bind cancellation evidence to every stored interpretation of its original.

    A physical source occurrence can have several historical mappings. None of
    those facts or receipts is rewritten when a newer parser excludes money.
    """
    frame, _sheet, _mode, _mapping, rows, stats = parsed
    plan = frame.attrs.get("table_plan")
    if case_id is not None:
        if plan is not None:
            if links := evidence_resolution.trial_occurrence_links(case_id, plan):
                plan["_trial_occurrence_links"] = links
            else:
                plan.pop("_trial_occurrence_links", None)
        evidence_resolution.reject_historical_overlap(case_id, plan)
    if case_id is not None and plan is not None:
        _bind_archive_header_history(case_id, plan, rows, stats, source)
        _bind_stored_cancellation_history(case_id, plan, rows, stats, source)
    if case_id is not None and plan is not None and plan["profile"] in table_import.SUPPORTED_PROFILES:
        _bind_table_history(case_id, plan, rows, stats)
        return
    if (case_id is None or plan is None or plan["profile"] not in table_archive.CANCELLATION_PROFILES
            or not plan.get("cancelled_rows")):
        return
    from app import table_cancellations
    if (plan["profile"] in table_import.AUXILIARY_SCOPE_PROFILES or plan["profile"] in (table_archive.HEADER_PROFILE, table_archive.GUARDED_PROFILE, table_archive.PRECISE_PROFILE, table_archive.REFERENCE_ROLE_PROFILE, table_archive.OPTIONAL_REFERENCE_PROFILE, table_archive.TIME_ROLE_PROFILE, table_archive.ZONED_PROFILE, table_archive.SOURCE_CONTRACT_PROFILE, table_archive.SOURCE_TEXT_PROFILE, table_archive.BANK31_VARIANT_PROFILE, table_archive.SOURCE_LEDGER_PROFILE, table_archive.POSTING_PAIR_PROFILE)):
        _bind_shared_cancellation_plan_history(case_id, plan, stats)
        return
    from app.import_staging import record_map
    records = record_map(plan)
    members = {member["member_path"]: member["sha256"] for member in plan["members"]}
    store = plan.get("_plan_store")
    requested = store.mapping() if store is not None else {}
    for row in plan["cancelled_rows"]:
        record = records[row["_source_row"]]
        requested[record["member_path"], members[record["member_path"]], record["source_row"]] = (row["_source_row"], row)
    for pair in plan.get("cancellations", []):
        record = records[pair["cancellation_record_no"]]
        requested[(record["member_path"], members[record["member_path"]], record["source_row"])] = (pair["original_record_no"], None)
    wanted_members = sorted({(path, digest) for path, digest, _row in requested})
    wanted = ",".join("(?,?)" for _ in wanted_members)
    fields = (*table_import.STORED_FLOW_FIELDS, "self_account", "self_name")
    conflicting = store.mapping() if store is not None else set()
    cursor = db.con().execute(f"""
            select r.member_path,m.sha256,r.source_row,{','.join('f.' + key for key in fields)}
            from import_records r
            join import_members m on m.case_id=r.case_id and m.source_id=r.source_id
              and m.member_path=r.member_path
            join (values {wanted}) wanted(member_path,sha256)
              on wanted.member_path=m.member_path and wanted.sha256=m.sha256
            join flow_sources s on s.case_id=r.case_id and s.source_id=r.source_id and s.source_row=r.record_no
            join flows f on f.case_id=s.case_id and f.id=s.flow_id
            where r.case_id=?
            """, [value for member in wanted_members for value in member] + [case_id])
    while batch := cursor.fetchmany(1024):
        for path, digest, position, *facts in batch:
            physical = (path, digest, position)
            if physical not in requested:
                continue
            original_no, row = requested[physical]
            previous = dict(zip(fields, facts))
            if (row is None or importer._row_key(previous) != importer._row_key(row)
                    or any((previous.get(key) or "") != (row.get(key) or "") for key in ("self_account", "self_name"))):
                conflicting.add(original_no)
    if conflicting:
        table_cancellations.revoke_pairs(plan, conflicting, reason="cancellation_historical_conflict")
        stats.clear()
        stats.update(plan["stats"])


def _preview_result(session, parsed, legacy=False, *, preserve_staging=False):
    from app.import_staging import record_map
    frame, _sheet, mode, mapping, rows, stats = parsed
    result = {"import_id": session["import_id"], "filename": session["filename"],
              "columns": list(frame.columns), "mode": mode, "mapping": mapping,
              "stats": stats, "preview": [preview_flow(row) for row in rows[:8]],
              "self_accounts_in_file": _accounts(rows)}
    plan = frame.attrs.get("feedback_plan")
    if plan is not None:
        result["feedback"] = _feedback_summary(plan)
        positions = record_map(plan)
        for preview, row in zip(result["preview"], rows):
            record = positions[row["_source_row"]]
            preview.update(source_member=record["member_path"], source_row=record["source_row"],
                           source_record_no=record["record_no"])
    table_plan = frame.attrs.get("table_plan")
    if table_plan is not None:
        result["table_summary"] = _table_summary(table_plan)
        request = json.loads(session.get("request_json") or "{}")
        result["reference_policy"] = request.get("reference_policy")
        result["reference_policy_basis"] = request.get("reference_policy_basis")
        if request.get(_REFERENCE_COLUMNS_TRIAL) and result["reference_policy_basis"] == _REFERENCE_MODEL_BASIS:
            result["layout_model_assisted"] = True
        regions = {}
        for entry in table_plan["mapping_evidence"]:
            if entry["field"] == "trade_no" or result["reference_policy"] is not None and entry["field"] == "voucher_reference":
                regions.setdefault((entry["member_path"], entry["header_row"]), set()).add(table_import._norm(entry["column"]))
        result["reference_policy_available"] = (not legacy and table_plan["profile"] in table_import.VOUCHER_REFERENCE_PROFILES
            and any("交易流水号" in names and names & {"凭证号", "凭证编号"} for names in regions.values()))
        positions = record_map(table_plan)
        for preview, row in zip(result["preview"], rows):
            record = positions[row["_source_row"]]
            preview.update(source_member=record["member_path"], source_row=record["source_row"],
                           source_record_no=record["record_no"])
    if table_plan is not None or plan is not None:
        active_plan = table_plan or plan
        if active_plan["profile"] in evidence_resolution.OCCURRENCE_PROFILES and not preserve_staging:
            # All preview samples/counts are captured. Identity replay needs
            # the complete journal, not another copy of financial staging.
            # Commit independently reparses the retained original.
            rows.clear()
            active_plan.get("cancelled_rows", []).clear()
        result["resolution"] = evidence_resolution.preview(active_plan)
    if not legacy:
        result["case_id"] = str(session["case_id"])
        if _CONFIRMED_LAYOUT in json.loads(session.get("request_json") or "{}"):
            result["layout_applied"] = True
    # Detach only the response metadata, never the parser's full rows/staging.
    # In-process callers must not mutate a sealed plan through this result.
    return deepcopy(result)


def _table_summary(plan):
    from app.import_staging import StagedSequence
    stats = plan["stats"]
    records = plan["records"]
    unresolved = (records.select(disposition="unresolved") if isinstance(records, StagedSequence)
                  else (record for record in records if record["disposition"] == "unresolved"))
    return {"profile": plan["profile"], "record_total": len(plan["records"]),
            "accepted": len(plan["rows"]),
            "cancelled": stats.get("cancelled", 0), "excluded_flows": 0, "effective_added": None,
            **{key: stats[key] for key in ("unresolved", "non_monetary", "non_transaction", "auxiliary", "reason_counts", "currency_totals")},
            "members": plan["members"], "documents": plan["documents"],
            "mapping_evidence": [{key: str(value) for key, value in entry.items()}
                                 for entry in plan["mapping_evidence"]],
            "unsupported_attachments": stats["reason_counts"].get("unsupported_attachment", 0),
            "non_business_attachments": stats["reason_counts"].get("non_business_attachment", 0),
            "unresolved_examples": [{**{key: record[key] for key in ("member_path", "source_row", "reason_code")},
                                     "details": {key: value for key, value in record["details"].items()
                                                 if key in table_import.FIELD_EVIDENCE_DETAILS | table_import.TIME_EVIDENCE_DETAILS
                                                 | {"validation_issues", table_direction_recheck.DETAIL_FIELD,
                                                    table_import.table_tiered_headers.LEAF_PROOF_FIELD}
                                                 or record["reason_code"] in pdf_text_observations.DISPOSITIONS
                                                 and key in pdf_text_observations.DETAILS_FIELDS}}
                                    for record in islice(unresolved, 10)]}


def _intake_materials(parsed):
    plan = parsed[0].attrs.get("table_plan") or parsed[0].attrs.get("feedback_plan")
    if plan is None:
        return []
    documents = {doc["member_path"]: doc for doc in plan.get("documents", ())}
    metadata = {member["member_path"]: member for member in plan["members"]}
    windows = {}
    leaves = {}

    def item(path, header=None, page=None, source_row=None):
        key = path, header, page, source_row
        if key not in leaves:
            member = metadata[path]
            doc = documents.get(path, {})
            headers = windows.get(path, ())
            following = bisect_right(headers, header) if header else 0
            last = (headers[following] - 1 if header and following < len(headers) else
                    doc.get("source_rows") if header else source_row)
            leaves[key] = {
                "member_path": path, "member_sha256": member["sha256"], "size_bytes": member["size_bytes"],
                "sheet": doc.get("sheet") or None, "header_row": header, "last_row": last,
                "page": page, "source_row": source_row,
                "role": doc.get("intake_role") or doc.get("role") or member["role"],
                "row_count": 0, "accepted": 0, "auxiliary": 0, "unresolved": 0,
                "reason": None, "issue_reason": None,
            }
        return leaves[key]

    for path, member in metadata.items():
        doc = documents.get(path, {})
        windows[path] = tuple(doc.get("header_rows") or ([doc["header_row"]] if doc.get("header_row") else []))
        if member["role"] == "pdf":
            for page in range(1, doc.get("pdf_total_pages", 0) + 1):
                leaf = item(path, page=page)
                leaf["reason"] = "pdf_page_role_unresolved"
                leaf["unresolved"] = 1

    for row in plan["records"]:
        path, position = row["member_path"], row["source_row"]
        detail = row["details"]
        reason, disposition = row["reason_code"], row["disposition"]
        headers = windows[path]
        boundary = bisect_right(headers, position)
        page = int(detail["pdf_page"]) if "pdf_page" in detail else None
        note = reason == "preamble"
        locator = item(path, headers[boundary - 1] if boundary and not note else None,
                       page=page, source_row=position if note else None)
        if note:
            locator["role"] = "description"
        if not locator["row_count"] and page is not None:
            locator["unresolved"] = 0
            locator["reason"] = None
        locator["row_count"] += 1
        locator["accepted"] += disposition in ("accepted", "imported", "duplicate")
        locator["auxiliary"] += disposition == "auxiliary"
        locator["unresolved"] += disposition in ("review", "unresolved")
        if locator["reason"] is None:
            locator["reason"] = reason
        if disposition in ("review", "unresolved") and locator["issue_reason"] is None:
            locator["issue_reason"] = reason

    # A registered member remains browseable when it has no journal rows.
    seen = {key[0] for key in leaves}
    for path in metadata.keys() - seen:
        item(path)
    for leaf in leaves.values():
        if leaf["role"] == "table":
            leaf["role"] = "transaction" if leaf["accepted"] else "unknown"
        if issue := leaf.pop("issue_reason"):
            leaf["reason"] = issue
    return sorted(leaves.values(), key=lambda leaf: (
        leaf["member_path"], leaf["page"] or 0, leaf["header_row"] or leaf["source_row"] or 0))


def _intake_outcome(item, stage, available):
    role, reason = item["role"], item["reason"]
    if reason == table_archive.UNREADABLE_REASON or reason in {"pdf_invalid", "pdf_ocr_failed", "pdf_ocr_timeout"}:
        return "unreadable", "replace_or_retry"
    if role in ("pdf", pdf_text_observations.ROLE):
        return "review", "review_page" if item["page"] else "review_original" if available else "replace_original"
    if item["unresolved"]:
        if item["accepted"] or item["auxiliary"]:
            return "partial", "review_rows"
        return "review", "review_original" if role in ("unknown", "attachment") else "review_rows"
    if item["accepted"]:
        return ("imported", "review_rows") if stage == "done" else ("ready", "confirm_import")
    if item["auxiliary"]:
        return ("registered", "review_original") if stage == "done" else ("ready", "confirm_registration")
    return "review", "review_original"


def _table_profile(session):
    profile = json.loads(session.get("request_json") or "{}").get("_table_profile", importer.PARSER_VERSION)
    if profile not in (importer.PARSER_VERSION, *_PDF_PROFILES, *table_import.SUPPORTED_PROFILES, *table_archive.SUPPORTED_PROFILES):
        raise ServiceError(400, "invalid_table_file", "来源解析规则不受支持，请重新上传预览")
    return profile


def _table_overrides(session):
    return bool(json.loads(session.get("request_json") or "{}").get("_table_overrides", False))


def _pdf_parsed(session, frozen):
    try:
        plan = pdf_import.replay(session, frozen)
    except (ValueError, TypeError, KeyError) as error:
        raise import_validation.IntegrityError("frozen PDF review changed") from error
    frame = importer.pd.DataFrame()
    frame.attrs.update(source_row_kind=pdf_import.ROW_KIND, table_plan=plan)
    return frame, "", "B", {}, plan["rows"], dict(plan["stats"])


def _read_pdf_text(source, size, digest, profile=None):
    pages = pdf_text_import.read_pages(source, size, digest)
    if profile in (None, pdf_text_import.PROFILE):
        page = pages[0] if pages is not None and len(pages) == 1 else None
        ledger = pdf_text_import.ledger_plan(page, digest, size)
        if ledger is not None or profile == pdf_text_import.PROFILE:
            return ledger
    return pdf_text_observations.plan(pages, digest, size, profile=profile)


def _pdf_text_parsed(plan, profile=None):
    if plan is None:
        if profile == pdf_text_import.PROFILE:
            raise ServiceError(409, "source_changed", "PDF 原页无法按已登记的账面约束重新核证")
        raise ServiceError(409, "pdf_observations_changed", _errors["pdf_observations_changed"])
    frame = importer.pd.DataFrame()
    frame.attrs.update(source_row_kind=_PDF_TEXT_READERS[plan["profile"]].ROW_KIND, table_plan=plan)
    return frame, "", plan["mode"], {}, plan["rows"], dict(plan["stats"])


def _validate_pdf_request(body, profile):
    if (body.mode != ("A" if profile == pdf_text_import.PROFILE else "B") or body.mapping
            or getattr(body, "reference_policy", None) is not None
            or _account_values(getattr(body, "self_accounts", None))
            or _account_values(getattr(body, "mark_accounts", None))):
        raise ServiceError(422, "invalid_pdf_mapping", "PDF 原页规则不接受手工映射或主账号覆盖")


def _validate_csv_tail_request(body, *, apply_physical_layout=False):
    if (apply_physical_layout or body.mode != "B" or body.mapping
            or getattr(body, "reference_policy", None) is not None
            or _account_values(getattr(body, "self_accounts", None))
            or _account_values(getattr(body, "mark_accounts", None))):
        raise ServiceError(422, table_import.CSV_TAIL_SOURCE_REASON, "末行缺格的CSV仅可按原登记范围保留来源")


def _reject_nonposting_amount_mapping(columns, mapping, profile):
    if table_import.mapping_uses_nonposting_amount(columns, mapping, profile):
        raise ServiceError(422, "invalid_mapping", "折算或转入转出相关金额原列不能用作原报金额、余额或来源币种")


def _table_mapping(mode, mapping, columns, *, profile, reject_nonposting_amounts=False):
    fields = table_import.mapping_fields(profile)
    if (mode not in ("A", "B") or not isinstance(mapping, dict)
            or any(key not in fields or not isinstance(value, str)
                   or not value or value not in columns for key, value in mapping.items())):
        raise ServiceError(422, "invalid_mapping", "表格映射字段不存在或不明确，请重新预览")
    _reject_document_number_mapping(mapping)
    if reject_nonposting_amounts:
        _reject_nonposting_amount_mapping(columns, mapping, profile)
    return dict(mapping)


def _feedback_summary(plan):
    stats = plan["stats"]
    return {"profile": plan["profile"], "record_total": len(plan["records"]),
            "transaction_total": stats["total"], "accepted": len(plan["rows"]),
            "review": stats["review"], "non_transaction": stats["non_transaction"],
            "non_monetary": stats["non_monetary"], "unresolved": stats["unresolved"],
            "cancelled": stats.get("cancelled", 0),
            "auxiliary": stats["auxiliary"], "reason_counts": stats["reason_counts"],
            "currency_totals": stats["currency_totals"], "members": plan["members"],
            "review_examples": [{k: r[k] for k in ("member_path", "source_row", "reason_code")}
                                for r in plan["records"] if r["disposition"] == "review"][:10],
            "unresolved_examples": [{k: r[k] for k in ("member_path", "source_row", "reason_code")}
                                    for r in plan["records"] if r["disposition"] == "unresolved"][:10]}


def _feedback_profile(session):
    # Historical ready sessions were evaluated under v1. New sessions pin the
    # internal rule version at preview, including retries after a process restart.
    request = json.loads(session.get("request_json") or "{}")
    return request.get("_feedback_profile", feedback_import.IDENTITY_PROFILE)


def _write_feedback_journal(cid, source_id, plan):
    """Persist disposition metadata; original cells stay in the uploaded file."""
    refs = plan["_plan_store"].mapping() if plan.get("_plan_store") is not None else {}
    cursor = db.con().execute("select source_row,disposition from flow_sources where case_id=? and source_id=?", [cid, source_id])
    while batch := cursor.fetchmany(4096):
        for position, disposition in batch:
            if disposition not in {"imported", "duplicate"}:
                raise importer.ImportError("反馈包来源关联处置无效")
            refs[position] = "imported" if disposition == "imported" else "duplicate"
    columns = ["case_id", "source_id", "member_path", "role", "sha256", "size_bytes", "encoding", "row_count"]
    members = [[cid, source_id, *[member[key] for key in columns[2:]]] for member in plan["members"]]
    if members:
        db.insert_dataframe("import_members", importer.pd.DataFrame(members, columns=columns), columns)
    del members
    columns = ["case_id", "source_id", "record_no", "member_path", "source_row", "record_key", "disposition", "reason_code", "details_json"]
    values, encoded_bytes = [], 0
    def write_batch():
        frame = importer.pd.DataFrame(values, columns=columns)
        db.insert_dataframe("import_records", frame, columns)
        del frame
        values.clear()
    # Validate while encoding: staged records otherwise decode twice. The
    # caller's import transaction also rolls back earlier chunks on a late
    # sequence/link/count failure; no receipt is published before this returns.
    accepted_count = 0
    for number, record in enumerate(plan["records"], 1):
        if record["record_no"] != number:
            raise importer.ImportError("反馈包来源记录序号不连续")
        if record["disposition"] == "accepted":
            if record["record_no"] not in refs:
                raise importer.ImportError("反馈包来源关联不完整")
            accepted_count += 1
        details = _json(record["details"])
        size = len(details.encode("utf-8"))
        if size > JOURNAL_CHUNK_BYTES:
            raise importer.ImportError("来源语义元数据超出处理范围")
        if values and (len(values) >= JOURNAL_CHUNK_RECORDS or encoded_bytes + size > JOURNAL_CHUNK_BYTES):
            write_batch()
            encoded_bytes = 0
        values.append([cid, source_id, record["record_no"], record["member_path"], record["source_row"], record["record_key"],
                       refs.get(record["record_no"], record["disposition"]), record["reason_code"], details])
        encoded_bytes += size
    if accepted_count != len(refs):
        raise importer.ImportError("反馈包来源关联不完整")
    if values:
        write_batch()


class _PreviewCancelled(Exception):
    pass


async def _execute_preview(operation, *args, receive=None):
    # One admitted preview, with no queue of large parser plans or ZIP copies.
    # This also covers directory construction; it must not re-enter admission.
    if not _preview_lock.acquire(blocking=False):
        raise ServiceError(409, "preview_busy", "另一个文件正在预览，请等待完成后重试")
    cancelled = threading.Event()
    monitor = None

    async def watch_disconnect():
        # Multipart parsing has already consumed the request body.
        while (await receive())["type"] != "http.disconnect":
            pass
        cancelled.set()

    try:
        if receive is not None:
            monitor = asyncio.create_task(watch_disconnect())
        worker = asyncio.create_task(anyio.to_thread.run_sync(operation, cancelled, *args))
        try:
            return await asyncio.shield(worker)
        except _PreviewCancelled:
            raise ServiceError(499, "preview_cancelled", "预览已取消") from None
        except asyncio.CancelledError:
            cancelled.set()
            # FastAPI closes request UploadFiles when this call unwinds. Both
            # raw Task.cancel and AnyIO cancellation must wait for the worker.
            with anyio.CancelScope(shield=True):
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
            if not worker.cancelled():
                worker.exception()
            raise
    finally:
        try:
            if monitor is not None:
                monitor.cancel()
                with anyio.CancelScope(shield=True):
                    await asyncio.gather(monitor, return_exceptions=True)
        finally:
            _preview_lock.release()


def _upload_size(source):
    source.seek(0, os.SEEK_END)
    size = source.tell()
    source.seek(0)
    return size


def _upload_sync(cancelled, case_id, source, filename, surface, resolve_evidence=False, intake_trial=False, *, trial_members=False):
    cid = _cid(case_id) if case_id is not None else None
    filename = Path((filename or "transactions.csv").replace("\\", "/")).name
    extension = Path(filename).suffix
    suffix = extension.lower() or ".csv"
    is_pdf = suffix == ".pdf" and intake_trial and surface == "v1"
    pdf_member_observations = intake_trial and suffix in {".zip", ".rar"}
    if suffix not in _extensions and not is_pdf:
        raise ServiceError(400, "unsupported_file_type", "请上传 CSV、TXT、TSV、Excel、ZIP 或 RAR 文件")
    if is_pdf and cid is None:
        raise ServiceError(422, "pdf_case_required", "请先选择案件，再上传 PDF")
    # Decide the parser before shortening the display name, and never remove
    # the real extension (including an unsupported one) by truncation.
    filename = (filename[:-len(extension)][:240 - len(extension)] + extension
                if extension else filename[:240])
    expected_size = _upload_size(source)
    if expected_size > _max_upload:
        raise ServiceError(400, "file_too_large", "文件超过 300MB，请拆分后重新上传")
    # The request owns this seekable spool until the worker has returned.
    # Failed parsing never creates a retained original.
    parsed, created, reference_basis = None, False, None
    status_policy = table_import.TRANSACTION_STATUS_POLICY if surface == "v1" and intake_trial and not is_pdf else None
    try:
        if cancelled.is_set():
            raise _PreviewCancelled()
        if is_pdf:
            expected_digest = hashlib.file_digest(source, "sha256").hexdigest()
            source.seek(0)
            text_plan = _read_pdf_text(source, expected_size, expected_digest)
            if text_plan is None:
                source.seek(0)
                review = pdf_import.extract(source, expected_size, expected_digest, cancelled)
                if review is None or cancelled.is_set():
                    raise _PreviewCancelled()
        else:
            if suffix in {".zip", ".rar"}:
                resolve_evidence = (resolve_evidence or intake_trial or evidence_resolution.complete_family(source)
                                    or evidence_resolution.declared_auxiliary_family(source))
                archive_profile = table_archive.evidence_profile(source)
            table_profile = (((archive_profile if resolve_evidence or archive_profile in table_archive.XLS_EVIDENCE_PROFILES else table_archive.PROFILE)
                              if suffix == ".rar" or table_archive.should_parse(source, native_observations=True, retain_opaque=intake_trial) else None)
                             if suffix in {".zip", ".rar"} else table_import.VOUCHER_REFERENCE_XLS_PROFILE if suffix == ".xls" else table_import.VOUCHER_REFERENCE_EVIDENCE_PROFILE if resolve_evidence else TABLE_PROFILE)
            parsed = _parse(source, suffix=suffix, table_profile=table_profile,
                            feedback_profile=feedback_import.MICROSECOND_EVIDENCE_PROFILE if resolve_evidence and suffix == ".zip" and table_profile is None else None,
                            _auto_event_profile=table_profile in table_import.CLOSED_BANK_SUCCESSORS,
                            intake_policy=_INTAKE_POLICY if intake_trial else None,
                            pdf_member_observations=pdf_member_observations, narrative_decisions=intake_trial,
                            trial_members=trial_members, transaction_status_policy=status_policy)
            if surface == "v1" and intake_trial and suffix == ".xlsx":
                reference_basis = parsed[0].attrs.get("table_plan", {}).get("_reference_policy_basis")
            if reference_basis is not None:
                _close_staging(parsed)
                parsed = None
                if cancelled.is_set():
                    raise _PreviewCancelled()
                source.seek(0)
                parsed = _parse(source, suffix=suffix, table_profile=table_import.VOUCHER_REFERENCE_PROFILE,
                                reference_policy=table_import.TRANSACTION_REFERENCE_POLICY,
                                intake_policy=_INTAKE_POLICY, narrative_decisions=True, transaction_status_policy=status_policy)
        if cancelled.is_set():
            raise _PreviewCancelled()
        import_id = uuid.uuid4().hex
        directory = _directory(create=True)
        path = directory / f"{import_id}{suffix}"
        digest, size = hashlib.sha256(), 0
        source.seek(0)
        with path.open("xb") as stream:
            created = True
            for block in iter(lambda: source.read(1024 * 1024), b""):
                if cancelled.is_set():
                    raise _PreviewCancelled()
                size += len(block)
                if size > _max_upload:
                    raise ServiceError(400, "file_too_large", "文件超过 300MB，请拆分后重新上传")
                if stream.write(block) != len(block):
                    raise OSError("Incomplete retained upload write")
                digest.update(block)
        now = _now()
        session = {"case_id": cid, "import_id": import_id, "filename": filename, "surface": surface,
                   "suffix": suffix, "size_bytes": size, "sha256": digest.hexdigest()}
        if is_pdf:
            if size != expected_size or session["sha256"] != expected_digest:
                raise ServiceError(409, "source_changed", "PDF 原件在预览期间发生变化")
            if text_plan is not None:
                parsed = _pdf_text_parsed(text_plan)
                request = {"_table_profile": text_plan["profile"]}
            else:
                frozen = pdf_import.freeze(session, review)
                parsed = _pdf_parsed(session, frozen)
                request = {"_table_profile": pdf_import.PROFILE, "_pdf_plan": frozen}
        elif "table_plan" in parsed[0].attrs:
            request = {"_table_profile": parsed[0].attrs["table_plan"]["profile"],
                       SOURCE_CURRENCY_INTERPRETATION_KEY: SOURCE_CURRENCY_INTERPRETATION_VERSION}
        else:
            request = {"_feedback_profile": parsed[0].attrs["feedback_plan"]["profile"]} if suffix == ".zip" else {}
        if reference_basis is not None:
            request.update(mode=parsed[2], mapping=parsed[3], reference_policy=table_import.TRANSACTION_REFERENCE_POLICY,
                           reference_policy_basis=reference_basis)
        if intake_trial:
            request[_INTAKE_KEY] = _INTAKE_POLICY
            request[_NARRATIVE_DECISIONS] = True
            if suffix == ".xlsx":
                request[_ACCOUNT_USE_CODE_TRIAL] = True
        if status_policy:
            request[_TRANSACTION_STATUS_POLICY] = status_policy
        if pdf_member_observations:
            request[_PDF_MEMBER_OBSERVATIONS] = True
        evidence_resolution.operation_context(parsed, session["sha256"])
        pure_stamp = None if is_pdf else import_preview_cache.stamp(parsed)
        with db.transaction():
            if cancelled.is_set():
                raise _PreviewCancelled()
            if cid is not None:
                _cid(cid)
                _bind_feedback_plan(cid, parsed)
                _bind_table_plan(cid, parsed, source=session)
            if intake_trial:
                request[_INTAKE_ITEMS] = _intake_materials(parsed)
            session["request_json"] = _json(request) if request else None
            db.x("""insert into import_sessions(import_id,case_id,surface,filename,suffix,sha256,size_bytes,
                      mode,mapping_json,columns_json,request_json,stage,created_at,updated_at,expires_at,source_state)
                    values (?,?,?,?,?,?,?,?,?,?,?,'ready',?,?,?,'retained')""",
                 [import_id, cid, surface, filename, suffix, session["sha256"], size,
                  parsed[2], _json(parsed[3]), _json(list(parsed[0].columns)),
                  session["request_json"],
                  now, now, now + dt.timedelta(days=7)])
            # Preview still consumes operation-private staging. A late disk
            # failure must roll back the ready session with its retained file.
            evidence_resolution.operation_context(parsed, session["sha256"])
            reference_choice_conflict = parsed[0].attrs.get("table_plan", {}).get("_reference_choice_conflict", False)
            result = _preview_result(session, parsed, legacy=surface == "legacy", preserve_staging=pure_stamp is not None)
            if surface == "v1" and not is_pdf:
                import_ready_evidence.remember_preview(import_id, result)
            if pure_stamp is not None:
                cache_session = db.q_raw("select * from import_sessions where import_id=?", [import_id])[0]
                if import_preview_cache.retain(
                        import_preview_cache.key(cache_session, _table_profile(cache_session), _table_overrides(cache_session)),
                        parsed, pure_stamp):
                    parsed = None  # The finite cache now owns this one original plan.
        deliberate_confirmation = (result.get("table_summary") or {}).get("profile") in {
            table_import.CSV_TAIL_SOURCE_PROFILE, *table_import.CSV_TAIL_ORDINARY_PROFILES,
            table_import.DEPOSIT_DIRECTION_TRIAL_PROFILE}
        result["automatic"] = not deliberate_confirmation and reference_basis is None and not (
            result.get("reference_policy_available") and reference_choice_conflict)
        return result
    except BaseException:
        if created:
            import_preview_cache.discard(import_id)
            path.unlink(missing_ok=True)
        raise
    finally:
        _close_staging(parsed)


async def _upload(case_id, file, surface, *, resolve_evidence=False, intake_trial=False, receive=None):
    pdf = intake_trial and Path(file.filename or "").suffix.lower() == ".pdf"
    return await _execute_preview(_upload_sync, case_id, file.file, file.filename, surface, resolve_evidence,
                                  intake_trial, receive=receive if pdf else None)


async def upload(case_id, file, *, intake_trial=False, receive=None):
    return await _upload(case_id, file, "v1", intake_trial=intake_trial, receive=receive)


async def legacy_upload(file, case_id=None):
    return await _upload(case_id, file, "legacy")


def _directory_preview_sync(cancelled, case_id, files, surface, intake_trial=False):
    """Preserve relative paths/bytes in one registered original before preview.

    Starlette admits at most 1000 multipart files. Use the same explicit bound
    here; larger exports can be supplied as a bounded ZIP archive instead.
    """
    _cid(case_id)
    if not files or len(files) > 1000:
        raise ServiceError(400, "directory_file_count", "目录一次支持1至1000个文件；更大目录请使用ZIP")
    names, total, members = set(), 0, []
    for file in sorted(files, key=lambda item: item.filename or ""):
        name = file.filename or ""
        if name.endswith("/"):
            raise ServiceError(400, "unsafe_directory_path", "目录文件必须有明确的相对文件名")
        try:
            name = table_archive._path(name)
        except table_import.TableImportError:
            raise ServiceError(400, "unsafe_directory_path", "目录包含不安全或不明确的相对路径") from None
        if name in names:
            raise ServiceError(400, "duplicate_directory_path", "目录包含重复相对路径")
        names.add(name)
        if len(name.encode("utf-8")) > 4096 or name.endswith("/"):
            raise ServiceError(400, "unsafe_directory_path", "目录相对路径超出支持范围")
        size = _upload_size(file.file)
        total += size
        if size > table_archive.MAX_MEMBER_BYTES or total > _max_upload:
            raise ServiceError(400, "file_too_large", "目录原件累计超过300MB或单文件超过256MB，请分包上传")
        members.append((name, file.file))
    # Anonymous, operation-private disk storage; no complete ZIP in memory and
    # no unregistered permanent original if construction or parsing fails.
    with tempfile.TemporaryFile(mode="w+b", prefix="funds-trace-preview-", dir=db.DATA_DIR) as source:
        total = 0
        with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name, member in members:
                if cancelled.is_set():
                    raise _PreviewCancelled()
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                size = 0
                with archive.open(info, "w") as target:
                    for block in iter(lambda: member.read(1024 * 1024), b""):
                        if cancelled.is_set():
                            raise _PreviewCancelled()
                        size += len(block)
                        total += len(block)
                        if size > table_archive.MAX_MEMBER_BYTES or total > _max_upload:
                            raise ServiceError(400, "file_too_large", "目录原件累计超过300MB或单文件超过256MB，请分包上传")
                        target.write(block)
        return _upload_sync(cancelled, case_id, source, "目录原件.zip", surface, True, intake_trial, trial_members=intake_trial)


async def upload_directory(case_id, files, *, surface="v1", intake_trial=False):
    return await _execute_preview(_directory_preview_sync, case_id, files, surface, intake_trial)


def _reference_non_number_facts(rows):
    return {row["_source_row"]: import_validation._facts({**row, "trade_no": ""}) for row in rows}


def _before_reference_rule(source, session, request, proof):
    baseline = _parse(source, suffix=session["suffix"], table_profile=_table_profile(session),
                      intake_policy=request.get(_INTAKE_KEY),
                      transaction_status_policy=request.get(_TRANSACTION_STATUS_POLICY),
                      narrative_decisions=request.get(_NARRATIVE_DECISIONS) is True)
    try:
        plan = baseline[0].attrs["table_plan"]
        if fallback_rows := proof.get("fallback_rows"):
            expected = set(fallback_rows)
            eligible = {record["record_no"] for record in plan["records"]
                        if record["source_row"] in expected and record["disposition"] == "accepted"
                        and record["details"].get("trade_no_basis") == "legacy_voucher_fallback"}
            if len(eligible) != len(expected):
                raise ServiceError(422, "reference_layout_not_admitted", "原件缺号与既有凭证填补的范围不符")
            numbers = {row["_source_row"]: "" if row["_source_row"] in eligible else row["trade_no"]
                       for row in baseline[4]}
            return _reference_non_number_facts(baseline[4]), set(), numbers
        disagreement_rows = set(proof["differing_rows"])
        eligible = {record["record_no"] for record in plan["records"]
                    if record["source_row"] in disagreement_rows and record["disposition"] == "unresolved"
                    and record["reason_code"] == "conflicting_field"
                    and record["details"].get("validation_issues") == "conflicting_field"}
        return _reference_non_number_facts(baseline[4]), eligible, None
    finally:
        _close_staging(baseline)


def _repreview(case_id, import_id, body, surface, *, apply_physical_layout=False, trial_roles=False,
               trial_layout_decision=None, field_note_policy=None, reference_trial=None, reference_rule=False):
    with db.transaction():
        session = _session(case_id, import_id, surface, bind=True)
        if session["stage"] in _active_stages or session["stage"] == "done":
            raise ServiceError(409, "import_already_started", "任务已开始或已完成，请查看导入进度")
        revision = body.revision if apply_physical_layout else getattr(body, "ready_revision", None)
        if surface == "v1" and revision is not None:
            import_ready_evidence.require_revision(session, revision)
        profile = _table_profile(session)
        request = json.loads(session.get("request_json") or "{}")
        layout = request.get(_CONFIRMED_LAYOUT)
        if profile == table_import.CSV_TAIL_SOURCE_PROFILE:
            _validate_csv_tail_request(body, apply_physical_layout=apply_physical_layout)
        if profile == table_import.CSV_TAIL_REFERENCE_PROFILE and (
                apply_physical_layout or body.mode != session["mode"] or body.mapping != json.loads(session["mapping_json"])
                or "reference_policy" in body.model_fields_set and reference_trial is None
                and body.reference_policy != request.get("reference_policy")):
            raise ServiceError(422, "invalid_reference_policy", "末行缺格的编号用途仅可按原列核证")
        if profile in table_import.CSV_TAIL_ORDINARY_PROFILES and (
                apply_physical_layout or body.mode != session["mode"] or body.mapping != json.loads(session["mapping_json"])
                or getattr(body, "reference_policy", None) is not None):
            raise ServiceError(422, "invalid_mapping", "末行缺格仅按已核证的原表字段办理")
        if profile in table_import.DEPOSIT_CSV_TRIAL_PROFILES and (
                apply_physical_layout or body.mode != "B" or body.mapping != json.loads(session["mapping_json"])
                or getattr(body, "reference_policy", None) is not None):
            raise ServiceError(422, "deposit_direction_fixed", "存取款方向仅按本次核证的原表范围办理")
        if profile in _PDF_PROFILES:
            if apply_physical_layout:
                raise ServiceError(422, "invalid_pdf_mapping", "PDF 原页不使用表格布局映射")
            _validate_pdf_request(body, profile)
            if profile in _PDF_TEXT_READERS:
                with _source(session, snapshot=True) as original:
                    parsed = _pdf_text_parsed(
                        _read_pdf_text(original, session["size_bytes"], session["sha256"], profile), profile)
            else:
                _source(session)
                try:
                    parsed = _pdf_parsed(session, request.get("_pdf_plan"))
                except import_validation.IntegrityError:
                    raise ServiceError(409, "pdf_plan_changed", "冻结 PDF 复核结果无法核对，请重新上传原件") from None
            result = _preview_result(session, parsed, legacy=surface == "legacy")
            if profile == pdf_text_observations.PAGE_PROFILE:
                request[_INTAKE_ITEMS] = _intake_materials(parsed)
                db.x("update import_sessions set request_json=? where import_id=?", [_json(request), import_id])
            now = _now()
            db.x("update import_sessions set stage='ready',error_code=null,updated_at=?,expires_at=? where import_id=?",
                 [now, now + dt.timedelta(days=7), import_id])
            db.x("delete from import_file_gc where import_id=?", [import_id])
            result["automatic"] = True
            return result
        if apply_physical_layout:
            prior_code_layout = layout if (trial_roles and layout and layout.get("field_notes_policy")
                                           == table_import.table_source_roles.CODED_FIELD_NOTE_POLICY) else None
            if prior_code_layout is not None:
                field_note_policy = prior_code_layout["field_notes_policy"]
                trial_layout_decision = request.get(_TRIAL_LAYOUT_DECISION)
            layout = import_ready_evidence.confirmed_layout(
                session, body, profile, scoped_notes=trial_roles,
                evidence_layout=trial_roles and trial_layout_decision is not None,
            )
            if prior_code_layout is not None and layout["regions"] != prior_code_layout["regions"]:
                raise ServiceError(409, "trial_source_role_conflict", "原件用途码已核定当前物理表区和本方原列")
            if trial_roles and field_note_policy is not None:
                layout["field_notes_policy"] = field_note_policy
            if trial_roles:
                from . import import_column_trial

                if import_column_trial.mapping_conflict(session, layout["regions"]):
                    raise ServiceError(409, "trial_source_role_conflict", "原件说明与所选物理列冲突，请调整映射")
        elif layout and (revision is None or body.mode != session["mode"] or body.mapping != json.loads(session["mapping_json"])):
            raise ServiceError(409, "preview_changed", "已确认物理表区须按同一预览使用或重新确认表区")
        import_preview_cache.discard(import_id)
        with _source(session, snapshot=True) as source:
            is_table = profile in (*table_import.SUPPORTED_PROFILES, *table_archive.SUPPORTED_PROFILES)
            overrides = bool(layout) or is_table and (_table_overrides(session) or body.mode != session["mode"]
                                                     or body.mapping != json.loads(session["mapping_json"]))
            if is_table and not layout:
                columns = json.loads(session["columns_json"])
                _table_mapping(body.mode, body.mapping, columns, profile=profile, reject_nonposting_amounts=True)
                if overrides and profile in table_archive.SUPPORTED_PROFILES:
                    raise ServiceError(422, "invalid_mapping", "反馈包按成员独立校验，不能使用整包字段覆盖")
                if (surface == "v1" and request.get(_INTAKE_KEY) == _INTAKE_POLICY
                        and body.mapping != json.loads(session["mapping_json"])
                        and table_import.mapping_contradicts_source_roles(columns, body.mapping, profile)):
                    raise ServiceError(422, "invalid_mapping", "原表头已明确该列的其他职责，不能采用这项映射")
            explicit_reference_policy = ("reference_policy" in getattr(body, "model_fields_set", vars(body))
                                         and (profile != table_import.CSV_TAIL_REFERENCE_PROFILE or reference_trial is not None))
            reference_policy = body.reference_policy if explicit_reference_policy else request.get("reference_policy")
            if reference_policy is not None and (surface != "v1" or profile not in table_import.VOUCHER_REFERENCE_PROFILES):
                raise ServiceError(422, "invalid_reference_policy", "引用策略仅适用于新版单文件表格的 v1 预览")
            clear_reference_trial = explicit_reference_policy or apply_physical_layout or overrides
            reference_proof = reference_trial or (None if clear_reference_trial else request.get(_REFERENCE_COLUMNS_TRIAL))
            if reference_proof is not None and _reference_history_conflicts(session, verified=True):
                raise ServiceError(409, "reference_original_active", _errors["reference_original_active"])
            if reference_rule:
                prior_facts, eligible, expected_numbers = _before_reference_rule(source, session, request, reference_trial)
                source.seek(0)
            use_request_mapping = not layout and (not is_table or overrides)
            parsed = _parse(source, body.mode if use_request_mapping else None,
                            body.mapping if use_request_mapping else None, suffix=session["suffix"],
                            feedback_profile=_feedback_profile(session) if session["suffix"] == ".zip" else None,
                            table_profile=profile, reference_policy=reference_policy, verified_reference=reference_proof,
                            manual_mapping_evidence=overrides, confirmed_layout=layout,
                            intake_policy=request.get(_INTAKE_KEY),
                            transaction_status_policy=request.get(_TRANSACTION_STATUS_POLICY),
                            pdf_member_observations=request.get(_PDF_MEMBER_OBSERVATIONS) is True,
                            narrative_decisions=request.get(_NARRATIVE_DECISIONS) is True)
        try:
            if (reference_trial is not None and not (reference_rule and expected_numbers is not None)
                    and parsed[5]["accepted"] <= reference_trial["previous_accepted"]):
                raise ServiceError(422, "reference_layout_not_admitted", "分列试算未增加可采纳的原始交易记录")
            evidence_resolution.operation_context(parsed, session["sha256"])
            _bind_feedback_plan(session["case_id"], parsed)
            _bind_table_plan(session["case_id"], parsed, source=session)
            if reference_rule:
                selected_facts = _reference_non_number_facts(parsed[4])
                additions = selected_facts.keys() - prior_facts.keys()
                if expected_numbers is not None:
                    facts_changed = (selected_facts != prior_facts or expected_numbers != {
                        row["_source_row"]: row["trade_no"] for row in parsed[4]})
                else:
                    facts_changed = (len(prior_facts) != reference_trial["previous_accepted"]
                                     or not additions or not additions <= eligible
                                     or any(selected_facts.get(number) != facts for number, facts in prior_facts.items()))
                if facts_changed:
                    raise ServiceError(422, "reference_layout_not_admitted", "分列试算未能保全既有事实并解决编号冲突")
            now = _now()
            db.x("""update import_sessions set mode=?,mapping_json=?,columns_json=?,stage='ready',error_code=null,
                      updated_at=?,expires_at=? where import_id=?""",
                 [parsed[2], _json(parsed[3]), _json(list(parsed[0].columns)), now, now + dt.timedelta(days=7), import_id])
            db.x("delete from import_file_gc where import_id=?", [import_id])
            if is_table:
                request.update(mode=parsed[2], mapping=parsed[3], _table_overrides=overrides,
                               _manual_mapping_evidence_v1=overrides)
                request[SOURCE_CURRENCY_INTERPRETATION_KEY] = SOURCE_CURRENCY_INTERPRETATION_VERSION
                if layout:
                    plan = parsed[0].attrs["table_plan"]
                    if not table_import.confirmed_plan_admissible(plan, layout["policy"]):
                        raise ServiceError(409, "layout_preview_changed", "当前案件中存在来源冲突，请重新核对预览")
                    request[_CONFIRMED_LAYOUT] = {
                        **layout, "plan_sha256": plan["_confirmed_layout_sha256"],
                        "bound_plan_sha256": table_import.confirmed_plan_sha256(plan),
                    }
                    if apply_physical_layout:
                        request.pop(_TRIAL_LAYOUT_DECISION, None)
                        if trial_layout_decision is not None:
                            request[_TRIAL_LAYOUT_DECISION] = trial_layout_decision
                if reference_policy is None:
                    request.pop("reference_policy", None)
                else:
                    request["reference_policy"] = reference_policy
                if explicit_reference_policy or apply_physical_layout:
                    request.pop("reference_policy_basis", None)
                if clear_reference_trial and request.pop(_REFERENCE_COLUMNS_TRIAL, None):
                    request.pop("reference_policy_basis", None)
                if reference_trial is not None:
                    request["reference_policy_basis"] = _REFERENCE_RULE_BASIS if reference_rule else _REFERENCE_MODEL_BASIS
                    request[_REFERENCE_COLUMNS_TRIAL] = reference_trial
                if request.get(_INTAKE_KEY) == _INTAKE_POLICY:
                    request[_INTAKE_ITEMS] = _intake_materials(parsed)
                db.x("update import_sessions set request_json=? where import_id=?", [_json(request), import_id])
                session["request_json"] = _json(request)
            result = _preview_result(session, parsed, legacy=surface == "legacy")
            if request.get(_TRIAL_LAYOUT_DECISION, {}).get("origin") == "model_assisted":
                result["layout_model_assisted"] = True
            if surface == "v1":
                import_ready_evidence.remember_preview(import_id, result)
            return result
        finally:
            _close_staging(parsed)


def repreview(case_id, import_id, body):
    return _repreview(case_id, import_id, body, "v1")


def _reference_history_conflicts(session, *, verified):
    """Identical originals may repeat only under the same reference interpretation."""
    return bool(db.q_raw("""select 1 from import_sources s join import_source_operations o
        on o.case_id=s.case_id and o.source_id=s.source_id and o.active
        left join import_sessions prior on prior.case_id=s.case_id and prior.import_id=s.source_id
        where s.case_id=? and s.sha256=? and s.source_id<>? and s.parser_version<>?
        and (coalesce(json_extract_string(prior.request_json, '$._verified_reference_columns_v1.policy')=?, false) <> ?
          or prior.import_id is null and s.parser_version in (?,?) and exists (
              select 1 from import_records r where r.case_id=s.case_id and r.source_id=s.source_id
                and json_extract_string(r.details_json, '$.source_reference_policy')=?))
        limit 1""", [session["case_id"], session["sha256"], session["import_id"], table_import.CSV_TAIL_SOURCE_PROFILE,
                      table_import.VERIFIED_REFERENCE_POLICY, verified, *sorted(table_import.VERIFIED_REFERENCE_PROFILES),
                      table_import.VERIFIED_REFERENCE_POLICY]))


def prepare_reference_trial(case_id, import_id, revision, proof, *, rule=False):
    from app.api.models import RepreviewRequest

    session = _session(case_id, import_id)
    body = RepreviewRequest(mode=session["mode"], mapping=json.loads(session["mapping_json"]),
                            ready_revision=revision, reference_policy=table_import.TRANSACTION_REFERENCE_POLICY)
    prepared = _repreview(case_id, import_id, body, "v1", reference_trial=proof, reference_rule=rule)
    return {**prepared, "automatic": False}


def apply_layout(case_id, import_id, body, *, trial_roles=False, trial_layout_decision=None,
                 field_note_policy=None):
    return _repreview(case_id, import_id, body, "v1", apply_physical_layout=True, trial_roles=trial_roles,
                      trial_layout_decision=trial_layout_decision, field_note_policy=field_note_policy)


def legacy_repreview(body):
    return _repreview(body.case_id, body.import_id, body, "legacy")


def _account_values(value):
    if value is None:
        return None
    values = re.split(r"[,，;；\s]+", value.strip()) if isinstance(value, str) else value
    return list(dict.fromkeys(account.strip() for account in values if account.strip()))


def _request(body, mapping):
    return {"mode": body.mode, "mapping": mapping,
            "self_accounts": _account_values(getattr(body, "self_accounts", [])) or [],
            "mark_accounts": _account_values(getattr(body, "mark_accounts", None))}


def _mark_mains(cid, receipt, request):
    row_self = {item["account"]: item.get("name") or "" for item in receipt.get("self_accounts", []) if item.get("account")}
    if request["mark_accounts"] is None:
        mains = row_self
    else:
        mains = {account: row_self.get(account, "") for account in request["mark_accounts"]}
    for account in request["self_accounts"]:
        mains.setdefault(account, "")
    receipt["main_detected"] = []
    for account, name in mains.items():
        marked = mark_automatic(cid, account, name)
        receipt["main_detected"].append({"account": marked["account"], "name": marked["name"]})


def _receipt(raw):
    result = {"total": raw.get("total", 0), "ok": raw.get("ok", 0), "skipped": raw.get("skipped", 0),
            "rows_dup": raw.get("rows_dup", 0), "batch_id": str(raw["batch_id"]),
            "bad_examples": raw.get("bad_examples", []), "self_accounts": raw.get("self_accounts", []),
            "self_accounts_count": raw.get("self_accounts_count", 0), "main_matched": raw.get("main_matched", []),
            "accounts_in_batch": raw.get("accounts_in_batch", 0), "main_detected": raw.get("main_detected", [])}
    for key in ("feedback", "table_summary", "source_id", "resolution", "layout_decision"):
        if key in raw:
            result[key] = raw[key]
    return result


def _run(import_id):
    journal_records = None
    parsed = None
    try:
        with db._lock:
            found = db.q_raw("select * from import_sessions where import_id=? and owner_token=? and stage='parsing'",
                             [import_id, OWNER_TOKEN])
            if not found:
                return
            session = found[0]
        request = json.loads(session["request_json"])
        layout = request.get(_CONFIRMED_LAYOUT)
        profile = _table_profile(session)
        is_pdf_review = profile == pdf_import.PROFILE
        is_pdf_text = profile == pdf_text_import.PROFILE
        is_pdf = profile in _PDF_PROFILES
        pdf_source_only = is_pdf and not is_pdf_text
        is_table = profile in (*table_import.SUPPORTED_PROFILES, *table_archive.SUPPORTED_PROFILES)
        overrides = _table_overrides(session)
        with _source(session, snapshot=True) as source:
            if profile in _PDF_TEXT_READERS:
                parsed = _pdf_text_parsed(_read_pdf_text(source, session["size_bytes"], session["sha256"], profile), profile)
            elif is_pdf_review:
                parsed = _pdf_parsed(session, request.get("_pdf_plan"))
            else:
                try:
                    if layout is None and _REFERENCE_COLUMNS_TRIAL not in request:
                        parsed = import_preview_cache.take(import_preview_cache.key(session, _table_profile(session), overrides))
                except (OSError, ValueError):
                    raise import_validation.IntegrityError("preview scratch failed integrity verification") from None
                if parsed is None:
                    parsed = _parse(
                        source, request["mode"] if not is_table or overrides else None,
                        request["mapping"] if not is_table or overrides else None, suffix=session["suffix"],
                        feedback_profile=_feedback_profile(session) if session["suffix"] == ".zip" else None,
                        table_profile=_table_profile(session),
                        reference_policy=request.get("reference_policy") if is_table else None,
                        verified_reference=request.get(_REFERENCE_COLUMNS_TRIAL),
                        manual_mapping_evidence=bool(request.get("_manual_mapping_evidence_v1")), confirmed_layout=layout,
                        intake_policy=request.get(_INTAKE_KEY),
                        transaction_status_policy=request.get(_TRANSACTION_STATUS_POLICY),
                        pdf_member_observations=request.get(_PDF_MEMBER_OBSERVATIONS) is True,
                        narrative_decisions=request.get(_NARRATIVE_DECISIONS) is True)
            evidence_resolution.operation_context(parsed, session["sha256"])
            _frame, sheet, mode, mapping, rows, stats = parsed
        plan = _frame.attrs.get("feedback_plan")
        table_plan = _frame.attrs.get("table_plan")
        if profile == table_import.CSV_TAIL_REFERENCE_PROFILE and rows and _REFERENCE_COLUMNS_TRIAL not in request:
            raise ServiceError(422, "invalid_reference_policy", "请先核证末行缺格原表的编号用途")
        if not rows and plan is None and table_plan is None:
            raise ServiceError(422, "no_importable_rows", _errors["no_importable_rows"])
        with db.transaction():
            current = _session(session["case_id"], import_id, session["surface"])
            if current["owner_token"] != OWNER_TOKEN or current["stage"] != "parsing":
                return
            processed_total = len((plan or table_plan)["records"]) if plan or table_plan else len(rows)
            db.x("update import_sessions set stage='importing',total=?,updated_at=? where import_id=?",
                 [processed_total, _now(), import_id])
        # One shared transaction includes the receipt, provenance and account
        # changes. Restart can never observe durable rows without their receipt.
        with db.import_transaction() as con:
            current = _session(session["case_id"], import_id, session["surface"])
            if current["owner_token"] != OWNER_TOKEN or current["stage"] != "importing":
                return
            if profile in table_import.VERIFIED_REFERENCE_PROFILES and _reference_history_conflicts(
                    session, verified=_REFERENCE_COLUMNS_TRIAL in request):
                raise ServiceError(409, "reference_original_active", _errors["reference_original_active"])
            _bind_feedback_plan(session["case_id"], parsed)
            _bind_table_plan(session["case_id"], parsed, source=session)
            if request.get(_INTAKE_KEY) == _INTAKE_POLICY:
                materials_before = request.get(_INTAKE_ITEMS, [])
                materials_after = _intake_materials(parsed)
                if (profile == pdf_text_observations.PAGE_PROFILE or request.get(_PDF_MEMBER_OBSERVATIONS) is True) and (
                    [item for item in materials_before if item["role"] == pdf_text_observations.ROLE]
                    != [item for item in materials_after if item["role"] == pdf_text_observations.ROLE]
                ):
                    raise ServiceError(409, "pdf_observations_changed", _errors["pdf_observations_changed"])
                request[_INTAKE_ITEMS] = materials_after
            if layout and table_import.confirmed_plan_sha256(table_plan) != layout.get("bound_plan_sha256"):
                raise ServiceError(409, "layout_preview_changed", "案件来源发生变化，请重新核对预览")
            archive_csv = (table_plan is not None and table_plan["profile"] in table_archive.SUPPORTED_PROFILES
                           and table_plan["profile"] not in table_archive.EVIDENCE_PROFILES)
            standalone_keys = None
            if archive_csv and con.execute(
                    "select 1 from flows where case_id=? limit 1", [session["case_id"]]).fetchone() is not None:
                standalone_keys = {}
            manifest = (import_validation.table_manifest(table_plan, rows, verify_archive_csv=archive_csv,
                                                         standalone_csv_keys=standalone_keys)
                        if table_plan is not None else None)
            provenance = {"source_id": import_id, "filename": session["filename"], "sha256": session["sha256"],
                          "size_bytes": session["size_bytes"], "mode": mode, "mapping": mapping,
                          "sheet": sheet, "parser_version": (plan or table_plan)["profile"] if plan or table_plan else getattr(importer, "PARSER_VERSION", "funds-trace-import-v1"),
                          "row_kind": _frame.attrs.get("source_row_kind", "unknown")}
            summary_snapshot = {}
            if provenance["parser_version"] in evidence_resolution.PROFILES:
                if plan is not None:
                    summary_snapshot["feedback"] = _feedback_summary(plan)
                if table_plan is not None:
                    summary_snapshot["table_summary"] = _table_summary(table_plan)
                # All mutations/proofs of records are finished. Their exact
                # read-only replay no longer overlaps the financial writer.
                if "_plan_store" not in (plan or table_plan):
                    journal_records = _JournalRecords((plan or table_plan)["records"])
                    (plan or table_plan)["records"] = journal_records
                    _close_staging(parsed)
            receipt = importer.commit_rows(session["filename"], mode, rows, stats,
                                           case_id=session["case_id"], provenance=provenance,
                                           standalone_csv_keys=standalone_keys,
                                           trial_occurrence_links=(table_plan or {}).get("_trial_occurrence_links"),
                                           **({"verified_reference_rows": {
                                               record["record_no"]: record["details"][table_import.VERIFIED_REFERENCE_OCCURRENCE]
                                               for record in table_plan["records"]
                                               if record["disposition"] == "accepted"
                                               and table_import.VERIFIED_REFERENCE_OCCURRENCE in record["details"]
                                           }} if request.get(_REFERENCE_COLUMNS_TRIAL, {}).get("fallback_rows") else {}),
                                           # Ledger publication below refreshes after raw-write verification.
                                           refresh_source_index=not (pdf_source_only or plan is None and table_plan is not None
                                               and table_plan["profile"] in table_import.SOURCE_LEDGER_PROFILES))
            receipt["source_id"] = import_id
            db.x("insert into import_source_operations(case_id,source_id,batch_id,active) values (?,?,?,true)",
                 [session["case_id"], import_id, receipt["batch_id"]])
            if provenance["parser_version"] in evidence_resolution.PROFILES:
                db.x("insert into evidence_sources(case_id,source_id,batch_id,active) values (?,?,?,true)", [session["case_id"], import_id, receipt["batch_id"]])
            if provenance["parser_version"] in evidence_resolution.OCCURRENCE_PROFILES:
                receipt.update(summary_snapshot)
                rows.clear()
                (plan or table_plan).get("cancelled_rows", []).clear()
            if plan is not None:
                _write_feedback_journal(session["case_id"], import_id, plan)
                if "feedback" not in receipt:
                    receipt["feedback"] = _feedback_summary(plan)
                receipt["feedback"]["excluded_flows"] = db.q_raw("""
                    select (select count(*) from flows where case_id=?) -
                           (select count(*) from effective_flows where case_id=?) n
                    """, [session["case_id"], session["case_id"]])[0]["n"]
            if table_plan is not None:
                _write_feedback_journal(session["case_id"], import_id, table_plan)
                import_validation.verify_table_write(session["case_id"], import_id, table_plan["profile"], manifest)
                if is_pdf_text:
                    actual = db.q_raw("""select f.self_account from flows f join flow_sources s
                        on f.case_id=s.case_id and f.id=s.flow_id where s.case_id=? and s.source_id=?
                        order by s.source_row""", [session["case_id"], import_id])
                    if [row["self_account"] for row in actual] != [row["self_account"] for row in rows]:
                        raise import_validation.IntegrityError("PDF ledger own account changed")
                del manifest
                if (provenance["parser_version"] in evidence_resolution.OCCURRENCE_PROFILES
                        or ("_plan_store" in table_plan and table_plan["profile"] in table_import.SOURCE_LEDGER_PROFILES)):
                    if "table_summary" not in receipt:
                        receipt["table_summary"] = _table_summary(table_plan)
                    # The ledger now reads the written journal in this transaction.
                    # The receipt already holds the summary and main declarations.
                    rows = []
                    table_plan.update(rows=rows, records=[], cancelled_rows=[])
                    _close_staging(parsed)
                if table_plan["profile"] in table_import.SOURCE_LEDGER_PROFILES:
                    from app import source_ledger
                    receipt["source_ledger"] = source_ledger.commit(session["case_id"], import_id, receipt["batch_id"])
                if "table_summary" not in receipt:
                    receipt["table_summary"] = _table_summary(table_plan)
                from app import source_ledger
                receipt["table_summary"].update(
                    excluded_flows=db.q_raw("""select (select count(*) from flows where case_id=?) -
                        (select count(*) from effective_flows where case_id=?) n""",
                        [session["case_id"], session["case_id"]])[0]["n"] - source_ledger.nonpositive_count(session["case_id"]),
                    effective_added=db.q_raw("select count(*) n from effective_flows where case_id=? and batch_id=?",
                                             [session["case_id"], receipt["batch_id"]])[0]["n"])
            if not is_pdf:
                _mark_mains(session["case_id"], receipt, request)
            if provenance["parser_version"] in evidence_resolution.PROFILES:
                # Provenance/write fidelity and the durable receipt summary are
                # now fixed. Release parser staging before replaying the journal.
                rows.clear()
                (plan or table_plan)["records"] = []
                (plan or table_plan).get("cancelled_rows", []).clear()
                if journal_records is not None:
                    journal_records.close()
                    journal_records = None
                _close_staging(parsed)
            if not pdf_source_only:
                receipt["resolution"] = evidence_resolution.rebuild(session["case_id"])
                from .. import event_resolution
                event_resolution.rebuild(session["case_id"], changed_sources=[import_id])
            from . import materials
            materials.register(session['case_id'],import_id,request.get('material_group_id') or import_id)
            if receipt["ok"] and _TRIAL_LAYOUT_DECISION in request:
                receipt["layout_decision"] = request[_TRIAL_LAYOUT_DECISION]
            receipt = _receipt(receipt)
            if is_pdf:
                request.pop("_pdf_plan", None)
            if is_pdf or request.get(_INTAKE_KEY) == _INTAKE_POLICY:
                db.x("update import_sessions set request_json=? where import_id=?", [_json(request), import_id])
            db.x("""update import_sessions set stage='done',done=?,total=?,result_json=?,error_code=null,
                      updated_at=?,expires_at=? where import_id=? and owner_token=?""",
                 [processed_total, processed_total, _json(receipt), _now(), _now() + dt.timedelta(days=30), import_id, OWNER_TOKEN])
    except Exception as error:
        code = ("import_integrity_failed" if isinstance(error, import_validation.IntegrityError) else
                error.code if isinstance(error, ServiceError) and error.code in _errors else "import_failed")
        with db.transaction():
            now = _now()
            db.x("""update import_sessions set stage='error',done=0,result_json=null,error_code=?,updated_at=?,expires_at=?
                      where import_id=? and owner_token=? and stage in ('parsing','importing')""",
                 [code, now, now + dt.timedelta(days=30), import_id, OWNER_TOKEN])
    finally:
        import_preview_cache.discard(import_id)
        _close_staging(parsed)
        if journal_records is not None:
            journal_records.close()


def _check_saved_layout_amount_roles(session, manifest, layout, profile):
    """A previously ready physical selection may predate the amount-role check."""
    members = {entry["scope"]["member_path"]: entry for entry in manifest.get("layout_members", ())}
    with _source(session, snapshot=True) as snapshot:
        for region in layout["regions"]:
            entry = members.get(region["member_path"])
            if entry is None:
                raise ServiceError(409, "preview_changed", "已核证表区的物理范围已失效，请重新预览")
            row = region["header_row"]
            cells = import_ready_evidence._cells(snapshot, session, entry, {row}, profile, for_layout=True)[row]
            columns = table_import._columns([cell["value"] for cell in cells], profile)
            if any(not 1 <= column <= len(columns) for column in region["mapping"].values()):
                raise ServiceError(409, "preview_changed", "已核证表区的物理范围已失效，请重新预览")
            mapping = {field: columns[column - 1] for field, column in region["mapping"].items()}
            _table_mapping(region["mode"], mapping, columns, profile=profile, reject_nonposting_amounts=True)


def _needs_currency_repreview(session, stored, columns, profile):
    if stored.get(SOURCE_CURRENCY_INTERPRETATION_KEY) == SOURCE_CURRENCY_INTERPRETATION_VERSION:
        return False
    # A legacy archive preview retained only the first member's mapping.
    # Its original decisions cannot be recovered from that partial metadata.
    if profile in table_archive.SUPPORTED_PROFILES:
        return True
    if profile not in table_import.CURRENCY_CODE_ALIAS_PROFILES:
        return False
    members = stored.get(import_ready_evidence._KEY, {}).get("layout_members", ())
    if not members:
        return True
    new_headings = table_import.SOURCE_CURRENCY_HEADINGS - {"币种代码"}

    def affected(headings):
        return any(table_import._header_parts(heading, profile)[0] in new_headings for heading in headings)

    if affected(table_import._mapping_headers(columns, profile)):
        return True
    later_headers = []
    for entry in members:
        document = entry["document"]
        if affected(table_import._mapping_headers(document["columns"], profile)):
            return True
        later = set(entry["scope"]["header_rows"]) - {document["header_row"]}
        if later:
            later_headers.append((entry, later))
    if later_headers:
        with _source(session, snapshot=True) as snapshot:
            for entry, rows in later_headers:
                cells = import_ready_evidence._cells(snapshot, session, entry, rows, profile, for_layout=True)
                if any(affected([cell["value"] for cell in header]) for header in cells.values()):
                    return True
    return False


def _commit(case_id, import_id, body, surface, *, trial_roles=False):
    with db.transaction():
        session = _session(case_id, import_id, surface, bind=True)
        revision = getattr(body, "ready_revision", None)
        manifest = None
        if surface == "v1" and session["stage"] not in (*_active_stages, "done") and revision is not None:
            manifest = import_ready_evidence.require_revision(session, revision)
        from . import import_source_money, materials
        material_group_id=materials.commit_group(session['case_id'],import_id,getattr(body,'material_group_id',None),session.get('request_json'))
        if session["stage"] in (*_active_stages, "done"):
            result = {"import_id": import_id, "started": False}
        else:
            _source(session)
            if trial_roles:
                from . import import_column_trial

                if import_column_trial.mapping_conflict(session):
                    raise ServiceError(409, "trial_source_role_conflict", "原件说明与已登记映射冲突，请核对原件并重新预览")
            profile = _table_profile(session)
            is_pdf = profile in _PDF_PROFILES
            is_table = profile in (*table_import.SUPPORTED_PROFILES, *table_archive.SUPPORTED_PROFILES)
            if is_pdf:
                _validate_pdf_request(body, profile)
            if profile == table_import.CSV_TAIL_SOURCE_PROFILE:
                _validate_csv_tail_request(body)
            stored = json.loads(session.get("request_json") or "{}")
            if stored.get(_PDF_MEMBER_OBSERVATIONS) is True and (
                _account_values(getattr(body, "self_accounts", None))
                or _account_values(getattr(body, "mark_accounts", None))
            ):
                items = stored.get(_INTAKE_ITEMS, [])
                if any(item["role"] == pdf_text_observations.ROLE for item in items) and not any(
                    item["accepted"] for item in items
                ):
                    raise ServiceError(422, "invalid_pdf_mapping", "PDF 来源观察缺少账主依据，不能用于设置主账号")
            stored_policy = stored.get("reference_policy")
            if (profile == table_import.CSV_TAIL_REFERENCE_PROFILE and _REFERENCE_COLUMNS_TRIAL not in stored
                    and any(item["accepted"] for item in stored.get(_INTAKE_ITEMS, ()))):
                raise ServiceError(422, "invalid_reference_policy", "请先核证末行缺格原表的编号用途")
            layout = stored.get(_CONFIRMED_LAYOUT)
            if ((layout or _REFERENCE_COLUMNS_TRIAL in stored
                    or surface == "v1" and stored.get(import_source_money.RECEIPT_KEY) or profile in {
                    table_import.DEPOSIT_DIRECTION_TRIAL_PROFILE, *table_import.CSV_TAIL_ORDINARY_PROFILES})
                    and revision is None):
                raise ServiceError(409, "preview_changed", "提交已核证表区需要当前预览修订")
            if profile in table_import.VERIFIED_REFERENCE_PROFILES and _reference_history_conflicts(
                    session, verified=_REFERENCE_COLUMNS_TRIAL in stored):
                raise ServiceError(409, "reference_original_active", _errors["reference_original_active"])
            if "reference_policy" in getattr(body, "model_fields_set", vars(body)):
                if body.reference_policy is not None and (surface != "v1" or _table_profile(session) not in table_import.VOUCHER_REFERENCE_PROFILES):
                    raise ServiceError(422, "invalid_reference_policy", "引用策略仅适用于新版单文件表格的 v1 预览")
                if body.reference_policy != stored_policy:
                    raise ServiceError(409, "preview_changed", "引用策略与已校验预览不同，请先重新预览")
            columns = json.loads(session["columns_json"] or "[]")
            if is_table and _needs_currency_repreview(session, stored, columns, profile):
                raise ServiceError(409, "preview_changed", "来源币种表头的处理规则已更新，请重新预览确认")
            mapping = ({} if is_pdf else _table_mapping(body.mode, body.mapping, columns, profile=profile) if is_table
                       else _mapping(body.mode, body.mapping, columns))
            if is_table and (body.mode != session["mode"] or mapping != json.loads(session["mapping_json"])):
                raise ServiceError(409, "preview_changed", "映射与已校验预览不同，请先重新预览")
            if is_table:
                _reject_nonposting_amount_mapping(columns, mapping, profile)
                if layout:
                    _check_saved_layout_amount_roles(session, manifest, layout, profile)
            if session["suffix"] == ".zip" and not is_table and (body.mode != "B" or mapping != feedback_import.MAPPING):
                raise ServiceError(422, "fixed_feedback_mapping", "资金反馈包的格式规则不可更改，请重新预览")
            request = _request(body, mapping)
            request['material_group_id']=material_group_id
            if SOURCE_CURRENCY_INTERPRETATION_KEY in stored:
                request[SOURCE_CURRENCY_INTERPRETATION_KEY] = stored[SOURCE_CURRENCY_INTERPRETATION_KEY]
            if stored.get(_INTAKE_KEY) == _INTAKE_POLICY:
                request[_INTAKE_KEY] = _INTAKE_POLICY
                request[_INTAKE_ITEMS] = stored.get(_INTAKE_ITEMS, [])
                for key in (import_ready_evidence._SOURCE_BALANCE_ROLES, import_source_money.RECEIPT_KEY):
                    if source_roles := stored.get(key):
                        request[key] = source_roles
            if stored.get(_PDF_MEMBER_OBSERVATIONS) is True:
                request[_PDF_MEMBER_OBSERVATIONS] = True
            if stored.get(_NARRATIVE_DECISIONS) is True:
                request[_NARRATIVE_DECISIONS] = True
            if stored.get(_ACCOUNT_USE_CODE_TRIAL) is True:
                request[_ACCOUNT_USE_CODE_TRIAL] = True
            if status_policy := stored.get(_TRANSACTION_STATUS_POLICY):
                request[_TRANSACTION_STATUS_POLICY] = status_policy
            if is_pdf:
                request["_table_profile"] = profile
                if profile == pdf_import.PROFILE:
                    request["_pdf_plan"] = stored.get("_pdf_plan")
            if session["suffix"] == ".zip":
                request["_feedback_profile"] = _feedback_profile(session)
            if is_table:
                overrides = _table_overrides(session)
                request.update(_table_profile=_table_profile(session), _table_overrides=overrides,
                               _manual_mapping_evidence_v1=overrides)
                if stored_policy is not None:
                    request["reference_policy"] = stored_policy
                    if "reference_policy_basis" in stored:
                        request["reference_policy_basis"] = stored["reference_policy_basis"]
                    if _REFERENCE_COLUMNS_TRIAL in stored:
                        request[_REFERENCE_COLUMNS_TRIAL] = stored[_REFERENCE_COLUMNS_TRIAL]
                if layout:
                    request[_CONFIRMED_LAYOUT] = layout
                    if _TRIAL_LAYOUT_DECISION in stored:
                        request[_TRIAL_LAYOUT_DECISION] = stored[_TRIAL_LAYOUT_DECISION]
            db.x("""update import_sessions set mode=?,mapping_json=?,request_json=?,stage='parsing',done=0,total=0,
                      result_json=null,error_code=null,attempt=attempt+1,owner_token=?,updated_at=?
                      where import_id=?""", [body.mode, _json(mapping), _json(request), OWNER_TOKEN, _now(), import_id])
            result = {"import_id": import_id, "started": True}
    if result["started"]:
        try:
            threading.Thread(target=_run, args=(import_id,), daemon=True, name=f"import-{import_id[:8]}").start()
        except Exception:
            import_preview_cache.discard(import_id)
            with db.transaction():
                now = _now()
                db.x("""update import_sessions set stage='error',error_code='import_failed',updated_at=?,expires_at=?
                          where import_id=? and owner_token=? and stage='parsing'""",
                     [now, now + dt.timedelta(days=30), import_id, OWNER_TOKEN])
            raise ServiceError(500, "import_start_failed", "无法启动导入任务，请重试") from None
    if surface == "v1":
        result["case_id"] = str(case_id)
    return result


def commit(case_id, import_id, body, *, trial_roles=False):
    return _commit(case_id, import_id, body, "v1", trial_roles=trial_roles)


def legacy_commit(body):
    return _commit(body.case_id, body.import_id, body, "legacy")


def _progress(case_id, import_id, surface):
    session = _session(case_id, import_id, surface)
    if session["stage"] == "ready":
        raise ServiceError(404, "import_job_not_found", "导入任务不存在，请确认已开始导入")
    stage, error_code = session["stage"], session["error_code"]
    error = _errors.get(error_code, _messages["error"]) if stage == "error" else None
    result = {"import_id": import_id, "stage": stage, "done": session["done"], "total": session["total"],
              "message": error or _messages[stage], "result": json.loads(session["result_json"]) if session["result_json"] else None,
              "error": error, "error_code": error_code,
              "source_available": _source_available(session),
              "recoverable": stage == "error" and _source_available(session)}
    feedback = (result["result"] or {}).get("feedback")
    if stage == "done" and feedback:
        result["message"] = f"自动处理完成，全部 {feedback['record_total']} 条来源记录已保存"
    if stage == "done" and (result["result"] or {}).get("source_id"):
        # Read only committed journal facts. In-flight writes have no per-file
        # completion receipt and must not be presented as successful imports.
        source_id = result["result"]["source_id"]
        operations = db.q_raw("select active from import_source_operations where case_id=? and source_id=?",
                              [session["case_id"], source_id])
        result["withdrawn"] = bool(operations and not operations[0]["active"])
        members = {}
        for row in db.q_raw("""select member_path,disposition,count(*) n from import_records
                where case_id=? and source_id=? group by member_path,disposition order by member_path,disposition""",
                [session["case_id"], source_id]):
            members.setdefault(row["member_path"], {})[row["disposition"]] = row["n"]
        result["member_results"] = [{"member_path": path, "dispositions": counts} for path, counts in members.items()]
    if surface == "v1":
        result["case_id"] = str(case_id)
    return result


def progress(case_id, import_id):
    with db.read_snapshot():
        return _progress(case_id, import_id, "v1")


def legacy_progress(import_id, case_id=None):
    with db.read_snapshot():
        return _progress(case_id, import_id, "legacy")



def _source_available(session):
    # Availability is a cheap filesystem indication. Commit/repreview/download
    # always perform the full content digest check before using a source.
    if session["source_state"] != "retained":
        return False
    try:
        path = _path(session["import_id"], session["suffix"])
        return stat.S_ISREG(path.lstat().st_mode)
    except (OSError, ServiceError):
        return False


def _source_session(case_id, source_id):
    with db._lock:
        cid = _cid(case_id)
        rows = db.q_raw("select * from import_sources where case_id=? and source_id=? and origin='upload'", [cid, source_id])
        sessions = db.q_raw("select * from import_sessions where case_id=? and import_id=?", [cid, source_id])
        if (not rows or not sessions or rows[0]["sha256"] != sessions[0]["sha256"]
                or rows[0]["size_bytes"] != sessions[0]["size_bytes"]):
            raise _missing()
        return sessions[0]


def source_is_available(case_id, source_id):
    try:
        return _source_available(_source_session(case_id, source_id))
    except ServiceError:
        return False


def _registered_original(cid, source_id):
    rows = db.q_raw("""select p.filename,p.sha256,p.size_bytes,p.imported_at,m.material_name,
                s.import_id,s.source_state,s.suffix,b.id active_batch_id,o.active
            from import_sources p
            left join import_sessions s on s.case_id=p.case_id and s.import_id=p.source_id
                and s.sha256=p.sha256 and s.size_bytes=p.size_bytes
            left join import_materials m on m.case_id=p.case_id and m.source_id=p.source_id
            left join import_source_operations o on o.case_id=p.case_id and o.source_id=p.source_id
            left join batches b on b.case_id=p.case_id and b.id=o.batch_id and o.active
                and s.stage='done' and cast(b.id as varchar)=json_extract_string(s.result_json,'$.batch_id')
                and json_extract_string(s.result_json,'$.source_id')=p.source_id
            where p.case_id=? and p.source_id=? and p.origin='upload'""", [cid, source_id])
    if not rows:
        raise _missing()
    return rows[0]


def source_metadata(case_id, source_id):
    with db.read_snapshot():
        cid = _cid(case_id)
        original = _registered_original(cid, source_id)
        batch_id = original["active_batch_id"]
        return {"case_id": str(cid), "source_id": source_id, "filename": original["filename"],
                "sha256": original["sha256"], "size_bytes": original["size_bytes"],
                "registered_at": original["imported_at"].isoformat(timespec="seconds") + "Z",
                "material_name": original["material_name"], "source_available": _source_available(original),
                "active_batch_id": str(batch_id) if batch_id is not None else None, "withdrawn": original["active"] is False}


def withdraw_source(case_id, source_id, expected_batch_id):
    with db.transaction():
        cid = _cid(case_id)
        original = _registered_original(cid, source_id)
        batch_id = original["active_batch_id"]
        if batch_id is None or str(batch_id) != expected_batch_id:
            raise ServiceError(409, "source_operation_changed", "该原件的导入状态已变化，请重新核对文件信息。")
        if db.q_raw("select 1 from import_sessions where case_id=? and stage in ('parsing','importing') limit 1", [cid]):
            raise ServiceError(409, "import_in_progress", "请在当前案件的导入结算后撤回。")
        _withdraw_batch(cid, batch_id)
        return {"case_id": str(cid), "source_id": source_id, "batch_id": str(batch_id)}


def withdraw_batch(case_id, batch_id):
    with db.transaction():
        _withdraw_batch(_cid(case_id), batch_id)
    return {"ok": True}


def _withdraw_batch(case_id, batch_id):
    if not db.q("select id from batches where id=? and case_id=?", [batch_id, case_id]):
        raise ServiceError(404, "batch_not_found", "批次不存在")
    changed_sources = [row["source_id"] for row in db.q_raw("""
        select s.source_id from flow_sources s join flows f on f.id=s.flow_id and f.case_id=s.case_id
          where s.case_id=? and f.batch_id=?
        union select source_id from import_source_operations where case_id=? and batch_id=?
        """, [case_id, batch_id, case_id, batch_id])]
    # Withdraw this operation's support first. A retained source metadata
    # row alone is not support: require a live operation and its flow link.
    db.x("update import_source_operations set active=false where case_id=? and batch_id=?", [case_id, batch_id])
    db.x("update evidence_sources set active=false where case_id=? and batch_id=?", [case_id, batch_id])
    db.x("""update flows as f set batch_id=s.batch_id from (
             select fs.flow_id,min(e.batch_id) batch_id from flow_sources fs
             join import_source_operations e on e.case_id=fs.case_id and e.source_id=fs.source_id and e.active
             join batches b on b.case_id=e.case_id and b.id=e.batch_id
             where fs.case_id=? group by fs.flow_id
             ) s where f.case_id=? and f.batch_id=? and f.id=s.flow_id""", [case_id, case_id, batch_id])
    # batch_id is retained storage ownership, not the historical receipt.
    # Unlinked legacy imports keep their existing deletion behavior; never
    # infer another operation from filenames or resurrect removed facts.
    db.x("delete from tracked_flows where case_id=? and flow_id in "
         "(select id from flows where batch_id=? and case_id=?)", [case_id, batch_id, case_id])
    from app import source_ledger
    db.x("""update import_records set disposition='removed',reason_code='flow_deleted'
             where case_id=? and not (
                disposition in ('unresolved','non_monetary','cancelled')
                and json_extract_string(details_json,'$.source_ledger_evidence') is not null
                and source_id in (select source_id from import_sources where parser_version in (""" + source_ledger._profiles() + """)))
             and (source_id,record_no) in
             (select s.source_id,s.source_row from flow_sources s join flows f on f.id=s.flow_id and f.case_id=s.case_id
              where s.case_id=? and (f.batch_id=? or s.source_id in
                (select source_id from import_source_operations where case_id=? and batch_id=?)))""",
         [case_id, case_id, batch_id, case_id, batch_id])
    db.x("""delete from flow_sources where case_id=? and
             (flow_id in (select id from flows where batch_id=? and case_id=?) or
              source_id in (select source_id from import_source_operations where case_id=? and batch_id=?))""",
         [case_id, batch_id, case_id, case_id, batch_id])
    db.x("delete from flows where batch_id=? and case_id=?", [batch_id, case_id])
    db.x("delete from batches where id=? and case_id=?", [batch_id, case_id])
    evidence_resolution.rebuild(case_id)
    from app import event_resolution, analysis_snapshot
    event_resolution.rebuild(case_id, changed_sources=changed_sources)
    analysis_snapshot.invalidate(case_id)


def get_source_file(case_id, source_id):
    with db._lock:
        session = _source_session(case_id, source_id)
        return _source(session, snapshot=True), session["filename"]


def _restored_intake(case_id, source_id):
    cid, source = _journal_source(case_id, source_id, include_pdf=True)
    if source["origin"] != "package":
        raise _missing()
    rows = db.q_raw("""select m.member_path,m.role,m.sha256,m.size_bytes,
        coalesce(json_extract_string(r.details_json,'$.intake_material_role'),
                 json_extract_string(r.details_json,'$.source_role')) source_role,
        try_cast(json_extract_string(r.details_json,'$.pdf_page') as integer) page,
        r.disposition,r.reason_code,count(r.record_no) n
        from import_members m left join import_records r
          on r.case_id=m.case_id and r.source_id=m.source_id and r.member_path=m.member_path
        where m.case_id=? and m.source_id=? group by all order by m.member_path,page,r.reason_code""",
        [cid, source_id])
    leaves = {}
    for row in rows:
        path, page, disposition = row["member_path"], row["page"], row["disposition"]
        item = leaves.setdefault((path, page), {
            "member_path": path, "member_sha256": row["sha256"], "size_bytes": row["size_bytes"],
            "sheet": None, "header_row": None, "last_row": None, "page": page, "source_row": None,
            "row_count": 0, "accepted": 0, "auxiliary": 0, "unresolved": 0, "reason": row["reason_code"],
            "role": row["source_role"] or row["role"],
        })
        item["row_count"] += row["n"]
        item["accepted"] += row["n"] if disposition in ("imported", "duplicate") else 0
        item["auxiliary"] += row["n"] if disposition == "auxiliary" else 0
        if disposition in ("unresolved", "review"):
            item["unresolved"] += row["n"]
            item["reason"] = row["reason_code"]
    stage = "done" if db.q_raw(
        "select 1 from import_source_operations where case_id=? and source_id=? and active", [cid, source_id]
    ) else "withdrawn"
    items = []
    for item in leaves.values():
        if source["parser_version"] == pdf_text_import.PROFILE:
            item["role"] = "transaction"
        elif item["role"] == "table":
            item["role"] = "transaction" if item["accepted"] else "unknown"
        status, _ = _intake_outcome(item, stage, False)
        query = urlencode({"member_path": item["member_path"]})
        items.append({**item, "status": status if stage == "done" else "withdrawn",
                      "next_action": "replace_original", "original_url": None,
                      "records_url": f"/api/v1/cases/{cid}/imports/{source_id}/records?{query}"})
    return {"version": _INTAKE_POLICY, "case_id": str(cid), "source_id": source_id,
            "sha256": source["sha256"], "stage": stage, "source_available": False, "items": items}


def intake(case_id, import_id):
    """Display the current activity and original positions of an isolated intake."""
    with db.read_snapshot():
        try:
            session = _session(case_id, import_id)
        except ServiceError as error:
            if error.code != "import_not_found":
                raise
            return _restored_intake(case_id, import_id)
        request = json.loads(session["request_json"] or "{}")
        if request.get(_INTAKE_KEY) != _INTAKE_POLICY:
            raise ServiceError(404, "intake_not_registered", "这次导入没有材料分类记录")
        stage = session["stage"]
        if stage == "done" and not db.q_raw(
            "select 1 from import_source_operations where case_id=? and source_id=? and active",
            [session["case_id"], import_id],
        ):
            stage = "withdrawn"
        available = _source_available(session)
        base = f"/api/v1/cases/{session['case_id']}"
        items = []
        for item in request.get(_INTAKE_ITEMS, ()):
            status, next_action = _intake_outcome(item, stage, available)
            if stage == "withdrawn":
                status = "withdrawn"
            if stage == "error":
                status, next_action = "error", "retry_import"
            if not available:
                next_action = "replace_original"
            member_path = item["member_path"]
            query = urlencode({"member_path": member_path})
            page = item.get("page")
            items.append({**item, "status": status, "next_action": next_action,
                          "original_url": f"{base}/sources/{import_id}/member-file?{query}"
                                          + (f"#page={page}" if page else "") if available else None,
                          "records_url": f"{base}/imports/{import_id}/records?{query}"
                                         + (f"&pdf_page={page}" if page else "") if stage == "done" else None})
        result = {"version": _INTAKE_POLICY, "case_id": str(session["case_id"]), "source_id": import_id,
                  "sha256": session["sha256"], "stage": stage, "source_available": available, "items": items}
        if available and stage in {"ready", "done"}:
            notes = import_ready_evidence.intake_source_field_notes(session, items)
            if notes:
                result["source_field_notes"] = notes
        return result


def get_source_member_file(case_id, source_id, member_path):
    """Open only a known material position and verify any archive leaf bytes."""
    from .. import import_originals

    with db.read_snapshot():
        session = _session(case_id, source_id)
        request = json.loads(session["request_json"] or "{}")
        if request.get(_INTAKE_KEY) != _INTAKE_POLICY:
            raise _missing()
        member = next((item for item in request.get(_INTAKE_ITEMS, ()) if item["member_path"] == member_path), None)
        if member is None:
            raise _missing()
    original = _source(session, snapshot=True)
    if session["suffix"] not in (".zip", ".rar"):
        return original, session["filename"]
    worksheet = (table_import.archive_workbook_path(member_path)
                 if member.get("sheet") and member["size_bytes"] == 0 else None)
    leaf_path = worksheet or member_path
    with closing(original):
        output = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b", dir=db.DATA_DIR)
        try:
            with import_originals._member(original, leaf_path) as leaf:
                size, digest = 0, hashlib.sha256()
                for block in iter(lambda: leaf.read(1024 * 1024), b""):
                    size += len(block)
                    digest.update(block)
                    output.write(block)
                # The digest-checked archive owns the exact workbook bytes. A
                # worksheet's logical digest describes its cells, not that file.
                if not worksheet and (size != member["size_bytes"] or digest.hexdigest() != member["member_sha256"]):
                    raise ValueError("member digest changed")
            output.seek(0)
            return output, Path(leaf_path).name
        except (OSError, ValueError, zipfile.BadZipFile):
            output.close()
            raise ServiceError(409, "source_changed", "材料成员无法按登记摘要回读") from None
        except BaseException:
            output.close()
            raise


def _journal_source(case_id, source_id, *, include_pdf=False):
    cid = _cid(case_id)
    if not isinstance(source_id, str) or not re.fullmatch(r"[0-9a-f]{12,32}", source_id):
        raise _missing()
    row_kinds = ["archive_record", "table_record"]
    if include_pdf:
        row_kinds.extend((pdf_import.ROW_KIND, pdf_text_import.ROW_KIND, pdf_text_observations.ROW_KIND))
    found = db.q_raw("select * from import_sources where case_id=? and source_id=? and row_kind in (select unnest(?::varchar[]))",
                     [cid, source_id, row_kinds])
    if not found:
        raise _missing()
    return cid, found[0]


def _record_items(cid, source_id, *, disposition=None, member_path=None, source_row=None, pdf_page=None, limit=None, offset=0, include_preview=False, record_nos=None):
    from .. import bank_single_postings
    where, params = "r.case_id=? and r.source_id=?", [cid, source_id]
    if disposition is not None:
        where += " and r.disposition=?"
        params.append(disposition)
    if member_path is not None:
        where += " and r.member_path=?"
        params.append(member_path)
    if source_row is not None:
        where += " and r.source_row=?"
        params.append(source_row)
    if pdf_page is not None:
        where += " and json_extract_string(r.details_json,'$.pdf_page')=?"
        params.append(str(pdf_page))
    if record_nos is not None:
        where += ' and r.record_no in (select unnest(?::bigint[]))'
        params.append(record_nos)
        if record_nos:
            where += ' and r.record_no between ?::bigint and ?::bigint'
            params += [min(record_nos), max(record_nos)]
    paging = " limit ? offset ?" if limit is not None else ""
    if limit is not None:
        params += [limit, offset]
    rows = db.q_raw(f"""with _record_page as materialized (
                        select r.* from import_records r where {where} order by r.record_no{paging}
                        ) select r.*,f.flow_id,m.status current_event_state,m.reason current_event_reason,m.pending_group current_pending_group,
                        e.evidence_json current_single_proof from _record_page r left join flow_sources f
                        on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no
                        left join event_members m on m.case_id=f.case_id and m.flow_id=f.flow_id
                        left join resolved_events e on e.case_id=m.case_id
                            and e.event_id=case when m.status='confirmed' then m.event_id end
                            and json_extract_string(e.evidence_json,'$.policy')='{bank_single_postings.POLICY}'
                        order by r.record_no""", params)
    # Only this already bounded page is projected. Original record values and
    # frozen receipt counts remain in their existing provenance contracts.
    flow_ids = list({row["flow_id"] for row in rows if row["flow_id"] is not None}) if include_preview else []
    previews = ({row["id"]: preview_flow(row) for row in db.q_raw(
        "select * from flows where case_id=? and id in (select unnest(?::bigint[]))", [cid, flow_ids])}
        if flow_ids and limit is not None else {})
    return [{**{k: r[k] for k in ("record_no", "member_path", "source_row", "record_key", "disposition", "reason_code")},
             "details": {**json.loads(r["details_json"]),
                 **({bank_single_postings.PROOF_FIELD: r["current_single_proof"]} if r["current_single_proof"] else {}),
                 **({bank_single_postings.PENDING_FIELD if (r["current_event_reason"] or '').startswith('single_posting_') else "current_bank_posting_pair_pending": r["current_event_reason"]} if r["current_pending_group"] and r["current_event_state"] == "unknown" else {}),
                 **({"current_event_state": r["current_event_state"], "current_event_reason": r["current_event_reason"]} if (r["current_event_reason"] or '').startswith('single_posting_') else {}),
                 **({"current_event_state": "unknown", "current_event_reason": "time_basis_incomparable"} if r["current_event_reason"] == "time_basis_incomparable" else {"current_event_state": "conflict"} if r["current_event_state"] == "conflict" else {})},
             "flow_id": str(r["flow_id"]) if r["flow_id"] is not None else None,
             **({"preview": {**previews[r["flow_id"]], "source_member": r["member_path"],
                              "source_row": r["source_row"], "source_record_no": r["record_no"]}}
                if r["flow_id"] in previews else {})}
            for r in rows]


def list_records(case_id, source_id, page=1, page_size=100, disposition=None, member_path=None, include_preview=False, source_row=None, *, pdf_page=None):
    with db._lock:
        cid, _ = _journal_source(case_id, source_id, include_pdf=True)
        condition, params = (" and disposition=?", [disposition]) if disposition is not None else ("", [])
        if member_path is not None:
            condition += " and member_path=?"
            params.append(member_path)
        if source_row is not None:
            condition += " and source_row=?"
            params.append(source_row)
        if pdf_page is not None:
            condition += " and json_extract_string(details_json,'$.pdf_page')=?"
            params.append(str(pdf_page))
        total = db.q_raw(f"select count(*) n from import_records where case_id=? and source_id=?{condition}",
                         [cid, source_id, *params])[0]["n"]
        return {"case_id": str(cid), "source_id": source_id, "total": total, "page": page, "page_size": page_size,
                "items": _record_items(cid, source_id, disposition=disposition, member_path=member_path, source_row=source_row, pdf_page=pdf_page, include_preview=include_preview,
                                       limit=page_size, offset=(page - 1) * page_size)}


def review_records(case_id, source_id, member_path, view='all', page=1, page_size=50, issue_index=None):
    """Page/filter frozen source rows; original bytes are a separate read-only view."""
    from .. import import_originals
    from . import import_layout_trial

    groups = {'all': None, 'review': ('review', 'unresolved'), 'accepted': ('imported', 'duplicate')}
    with db._lock:
        cid, source = _journal_source(case_id, source_id, include_pdf=True)
        members = db.q_raw('select * from import_members where case_id=? and source_id=? and member_path=?', [cid, source_id, member_path])
        if not members:
            raise _missing()
        scope, params = 'case_id=? and source_id=? and member_path=?', [cid, source_id, member_path]
        pdf_observation_member = False
        if members[0]['role'] == 'attachment':
            pdf_observation_member = Path(member_path).suffix.lower() == ".pdf" and bool(db.q_raw(
                f"select 1 from import_records where {scope} and reason_code in (select unnest(?::varchar[])) limit 1",
                [*params, list(pdf_text_observations.DISPOSITIONS)]))
            if not pdf_observation_member:
                raise ServiceError(422, 'source_rows_unavailable', '附件原件已保留，内部数据行尚不能读取')
        raw_counts = {row['disposition']: row['n'] for row in db.q_raw(
            f'select disposition,count(*) n from import_records where {scope} group by disposition', params)}
        counts = {name: sum(raw_counts.values()) if group is None else sum(raw_counts.get(key, 0) for key in group) for name, group in groups.items()}
        group = groups[view]
        where = scope + (' and disposition in (select unnest(?::varchar[]))' if group is not None else '')
        filtered = [*params, list(group)] if group is not None else params
        issue = None
        if issue_index is not None and counts['review'] and view != 'accepted':
            index = min(issue_index, counts['review'])
            rows = db.q_raw(f"select record_no,source_row,reason_code from import_records where {scope} and disposition in ('review','unresolved') order by record_no limit 1 offset ?", [*params, index - 1])
            issue = {'index': index, **rows[0]}
            rank = db.q_raw(f'select count(*) n from import_records where {where} and record_no<=?', [*filtered, issue['record_no']])[0]['n']
            page = (rank - 1) // page_size + 1
        page = min(page, max(1, (counts[view] + page_size - 1) // page_size))
        numbers = [row['record_no'] for row in db.q_raw(f'select record_no from import_records where {where} order by record_no limit ? offset ?', [*filtered, page_size, (page - 1) * page_size])]
        items = _record_items(cid, source_id, record_nos=numbers, limit=page_size, include_preview=True)
        for item in items:
            item['original'] = (pdf_text_observations.original_cells(item['details'])
                                if item['reason_code'] in pdf_text_observations.DISPOSITIONS else None)
        if source["parser_version"] in _PDF_PROFILES or pdf_observation_member:
            try:
                stream, _ = (get_source_member_file(cid, source_id, member_path) if pdf_observation_member
                             else get_source_file(cid, source_id))
                stream.close()
                original_error = None
            except ServiceError as error:
                if error.status not in (404, 409):
                    raise
                original_error = "PDF 原件不可用"
            return {"case_id": str(cid), "source_id": source_id, "member_path": member_path, "view": view,
                    "counts": counts, "items": items, "total": counts[view], "page": page, "page_size": page_size,
                    "issue": issue, "original_error": original_error}
        sessions = db.q_raw('select * from import_sessions where case_id=? and import_id=?', [cid, source_id])
        session = sessions[0] if sessions else None
        receipt = json.loads(session['result_json'] or '{}') if session else {}
        summary = receipt.get('table_summary') or {}
        documents = [document for document in summary.get('documents', []) if document['member_path'] == member_path]
        document = documents[0] if documents else {'header_row': 1} if source['parser_version'] in feedback_import.SUPPORTED_PROFILES else {}
        request = json.loads(session["request_json"] or "{}") if session else {}
        # Use the same raw-cell reader as the ready layout window for retained
        # small, unclassified trial tables. Its short rows stay unpadded.
        unclassified_trial_csv = (
            session is not None and session["suffix"] == ".csv" and member_path == "table.csv"
            and source["parser_version"] == table_import.VOUCHER_REFERENCE_PROFILE
            and request.get(_INTAKE_KEY) == _INTAKE_POLICY and document.get("header_rows") == []
            and set(summary.get("reason_counts", ())) == {"unknown_table"}
            and 1 <= document.get("source_rows", 0) <= import_layout_trial._MAX_SOURCE_ROWS
            and session["size_bytes"] <= import_layout_trial._MAX_FILE_BYTES
        )
        evidence = [entry for entry in summary.get('mapping_evidence', []) if entry.get('member_path') == member_path]
        if not evidence:
            evidence = [{'field': field, 'column': column} for field, column in json.loads(source['mapping_json']).items()]
        if source['parser_version'] in feedback_import.SUPPORTED_PROFILES:
            # The closed feedback schema reads this status independently of its
            # fixed financial MAPPING. This is display evidence, not a remap.
            evidence.append({'field': 'status', 'column': '交易是否成功'})
        # Verify/snapshot inside the owning scope; decoding holds no DB lock.
        original_error, original = None, None
        try:
            if numbers:
                original, _filename = get_source_file(cid, source_id)
        except ServiceError as error:
            if error.status not in (404, 409):
                raise
            original_error = '原件不可用，无法显示原始单元格'
    if original is not None:
        try:
            with original:
                cells = import_originals.read_cells(original, session['suffix'], members[0], document, evidence,
                                                    {item['source_row'] for item in items}, source['parser_version'],
                                                    for_layout=unclassified_trial_csv)
            for item in items:
                item['original'] = cells[item['source_row']]
        except (OSError, ValueError, KeyError, IndexError, StopIteration, csv.Error, zipfile.BadZipFile, import_originals.ET.ParseError):
            original_error = '原始单元格暂无法读取，请另存原件查看'
    return {'case_id': str(cid), 'source_id': source_id, 'member_path': member_path, 'view': view,
            'counts': counts, 'items': items, 'total': counts[view], 'page': page, 'page_size': page_size,
            'issue': issue, 'original_error': original_error}


def get_disposition_report(case_id, source_id):
    """Snapshot the complete journal before download; no pagination truncation."""
    stream = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b", dir=db.DATA_DIR)
    try:
        with db._lock:
            cid, source = _journal_source(case_id, source_id, include_pdf=True)
            members = db.q_raw("""select member_path,role,sha256,size_bytes,encoding,row_count from import_members
                                    where case_id=? and source_id=? order by member_path""", [cid, source_id])
            metadata = {"case_id": str(cid), "source_id": source_id, "filename": source["filename"],
                        "sha256": source["sha256"], "profile": source["parser_version"],
                        "source_available": source_is_available(cid, source_id), "source_files": "not_included",
                        "members": members}
            stream.write((_json(metadata)[:-1] + ',"items":[').encode("utf-8"))
            offset, first = 0, True
            while items := _record_items(cid, source_id, limit=1000, offset=offset):
                for record in items:
                    if not first:
                        stream.write(b",")
                    stream.write(_json(record).encode("utf-8"))
                    first = False
                offset += len(items)
            stream.write(b"]}")
        stream.seek(0)
        return stream, f"import-{source_id}-report.json"
    except BaseException:
        stream.close()
        raise


def _source_references(source_id):
    from . import trial_delayed_pair

    profiles = sorted({*feedback_import.SUPPORTED_PROFILES, *table_import.SUPPORTED_PROFILES,
                       *table_archive.SUPPORTED_PROFILES})
    placeholders = ",".join("?" for _ in profiles)
    return db.q_raw(f"""select (select count(*) from flow_sources where source_id=?) +
                         (select count(*) from import_records r join import_sources s
                          on s.case_id=r.case_id and s.source_id=r.source_id where r.source_id=?
                          and (r.disposition not in ('non_transaction','auxiliary','removed')
                               or s.parser_version not in ({placeholders})
                               or (json_extract_string(r.details_json,'$.intake_material_role') in (?,?,?,?)
                                   and exists (select 1 from import_source_operations o
                                       where o.case_id=r.case_id and o.source_id=r.source_id and o.active)))) n""",
                    [source_id, source_id, *profiles, *trial_delayed_pair.DELAYED_ROLES,
                     trial_delayed_pair.SAME_INSTANT_ROLE, "ledger_registration"])[0]["n"]


def list_sessions(case_id, limit=100, import_id=None):
    cid = _cid(case_id)
    limit = max(1, min(int(limit), 200))
    if import_id is None:
        rows = db.q_raw("select * from import_sessions where case_id=? order by created_at desc,import_id desc limit ?", [cid, limit])
    else:
        rows = db.q_raw("select * from import_sessions where case_id=? and import_id=?", [cid, import_id])
    items = []
    for row in rows:
        available = _source_available(row)
        items.append({"case_id": str(cid), "import_id": row["import_id"], "surface": row["surface"],
                      "filename": row["filename"], "mode": row["mode"], "mapping": json.loads(row["mapping_json"]),
                      "stage": row["stage"], "source_state": row["source_state"],
                      "created_at": row["created_at"].isoformat(timespec="seconds") + "Z",
                      "expires_at": row["expires_at"].isoformat(timespec="seconds") + "Z" if row["expires_at"] else None,
                      "error_code": row["error_code"], "attempt": row["attempt"],
                      "source_available": available, "recoverable": row["stage"] == "error" and available,
                      "request": {k: v for k, v in json.loads(row["request_json"]).items() if not k.startswith("_")}
                                 if row["request_json"] and "mode" in json.loads(row["request_json"]) else None,
                      "result": json.loads(row["result_json"]) if row["result_json"] else None})
    return {"case_id": str(cid), "items": items}


def recover_interrupted():
    """Call once during startup, after the single database owner is established."""
    import_preview_cache.discard()
    with db.transaction():
        now = _now()
        rows = db.q_raw("""update import_sessions set stage='error',done=0,result_json=null,error_code='import_interrupted',
                           updated_at=?,expires_at=? where stage in ('parsing','importing')
                           and (owner_token is null or owner_token<>?) returning import_id""",
                        [now, now + dt.timedelta(days=30), OWNER_TOKEN])
    return {"recovered": len(rows)}


def cleanup_uploads(case_id=None, now=None, dry_run=True):
    """Delete only registered, expired and unreferenced source files.

    Unlink happens under the database lock, so commit cannot start between the
    eligibility check and deletion. Failed unlinks retain their durable queue.
    No unknown files or directories are scanned.
    """
    now = now or _now()
    if now.tzinfo is not None:
        now = now.astimezone(dt.timezone.utc).replace(tzinfo=None)
    cid = _cid(case_id) if case_id is not None else None
    counts = {"dry_run": bool(dry_run), "eligible": 0, "deleted": 0, "missing": 0, "failed": 0,
              "protected": 0, "queued": 0}
    with db._lock:
        condition, params = ("and s.case_id=?", [cid]) if cid is not None else ("", [])
        sessions = db.q_raw(f"""select s.* from import_sessions s where s.source_state='retained'
                                and s.expires_at is not null and s.expires_at<=? {condition}""", [now, *params])
        for session in sessions:
            references = _source_references(session["import_id"])
            if session["stage"] in _active_stages or references:
                counts["protected"] += 1
                continue
            counts["eligible"] += 1
            if not dry_run:
                db.x("insert or ignore into import_file_gc(import_id,suffix,sha256,delete_after) values (?,?,?,?)",
                     [session["import_id"], session["suffix"], session["sha256"], now])
        queued = db.q_raw("""select g.*,s.case_id,s.stage,s.source_state,s.import_id session_id,s.expires_at from import_file_gc g
                              left join import_sessions s on s.import_id=g.import_id where g.delete_after<=?""", [now])
        for item in queued:
            if cid is not None and item["case_id"] != cid:
                continue
            refs = _source_references(item["import_id"])
            if (item["stage"] in _active_stages or refs
                    or (item["session_id"] is not None and (item["source_state"] != "retained"
                                                           or item["expires_at"] is None or item["expires_at"] > now))):
                counts["protected"] += 1
                continue
            counts["queued"] += 1
            if dry_run:
                continue
            import_preview_cache.discard(item["import_id"])
            try:
                path = _path(item["import_id"], item["suffix"])
                # Never follow or remove an attacker-controlled symlink.
                if path.is_symlink():
                    raise OSError("Symlink upload")
                try:
                    metadata = path.lstat()
                except FileNotFoundError:
                    metadata = None
                mode = metadata.st_mode if metadata is not None else None
                if mode is not None:
                    if not stat.S_ISREG(mode):
                        raise OSError("Not a regular upload")
                    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                    with os.fdopen(descriptor, "rb") as stream:
                        opened = os.fstat(stream.fileno())
                        if not stat.S_ISREG(opened.st_mode):
                            raise OSError("Not a regular upload")
                        digest = hashlib.sha256()
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(block)
                        current = path.lstat()
                        after_read = os.fstat(stream.fileno())
                        if (digest.hexdigest() != item["sha256"]
                                or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                                or (after_read.st_size, after_read.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns)):
                            raise OSError("Registered upload changed")
                    # Delete only bytes matching the durable queue, preserving
                    # a replaced path for inspection and a later explicit retry.
                path.unlink(missing_ok=True)
            except (OSError, ServiceError):
                counts["failed"] += 1
                continue
            with db.transaction():
                db.x("update import_sessions set source_state='cleaned',updated_at=? where import_id=?",
                     [now, item["import_id"]])
                db.x("delete from import_file_gc where import_id=?", [item["import_id"]])
            counts["missing" if mode is None else "deleted"] += 1
    return counts
