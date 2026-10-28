"""Persistent import workflow shared by the standalone HTTP surfaces.

The database owns session state; disk filenames never grant access. A completed
receipt is committed with its flows and remains readable after source cleanup.
"""
import datetime as dt
from decimal import Decimal, localcontext
import hashlib
import io
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

from app import db, importer, feedback_import, table_import, table_archive, import_validation, evidence_resolution
from .cases import ServiceError, preview_flow, require_case
from .main_accounts import mark_automatic


OWNER_TOKEN = uuid.uuid4().hex
TABLE_PROFILE = table_import.PROFILE
_extensions = {".csv", ".txt", ".tsv", ".xlsx", ".xls", ".zip"}
_max_upload = 300 * 1024 * 1024
JOURNAL_CHUNK_RECORDS = 5_000
JOURNAL_CHUNK_BYTES = 8 * 1024 * 1024
_active_stages = ("parsing", "importing")
_messages = {"parsing": "解析文件…", "importing": "写入流水…", "done": "导入完成",
             "error": "导入失败，请检查文件与列映射后重试", "ready": "等待确认导入"}
_errors = {"import_interrupted": "上次导入被中断，未完成的数据已回滚，请确认后重试",
           "source_changed": "上传文件校验失败，请重新上传文件",
           "source_missing": "上传文件已清理或不存在，请重新上传文件",
           "evidence_context_overlap": "相同原件的来源范围发生变化，无法安全衔接，未重复入账；请仅补辅助资料或在新案件导入完整目录",
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


def _missing():
    return ServiceError(404, "import_not_found", "导入会话不存在或不属于当前案件，请重新上传文件")


def _cid(case_id):
    return require_case(str(case_id))


def _mapping(mode, mapping, columns):
    if mode not in importer.MODE_FIELDS or not isinstance(mapping, dict):
        raise ServiceError(422, "invalid_mapping", "流水模式或列映射无效，请重新预览")
    allowed = set(importer.MODE_FIELDS[mode]) | {"_time2", "self_account", "self_name"}
    if any(key not in allowed or not isinstance(column, str) or (column and column not in columns)
           for key, column in mapping.items()):
        raise ServiceError(422, "invalid_mapping", "列映射包含未知字段或文件中不存在的列，请重新预览")
    result = {key: column for key, column in mapping.items() if column}
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
    if not isinstance(import_id, str) or not re.fullmatch(r"[0-9a-f]{32}", import_id) or suffix not in _extensions:
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
    with db.transaction():
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
           _auto_event_profile=False):
    try:
        if table_profile not in (None, importer.PARSER_VERSION, *table_import.SUPPORTED_PROFILES, *table_archive.SUPPORTED_PROFILES):
            raise ServiceError(400, "invalid_table_file", "来源解析规则不受支持，请重新上传预览")
        if table_profile in (*table_import.SUPPORTED_PROFILES, *table_archive.SUPPORTED_PROFILES):
            plan = (table_archive.parse(source, profile=table_profile, _stage=True) if table_profile in table_archive.SUPPORTED_PROFILES
                    else table_import.parse(source, suffix=suffix, mode=mode, mapping=mapping,
                                            profile=table_profile,
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
            plan = feedback_import.parse(source, profile=feedback_profile or feedback_import.PROFILE)
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
    except feedback_import.FeedbackImportError as error:
        raise ServiceError(400, "invalid_feedback_archive", str(error)) from None
    except table_import.TableImportError as error:
        raise ServiceError(400, "invalid_table_file", str(error)) from None
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
    fields = (*table_import.FLOW_FIELDS, "self_account", "self_name")
    def signature(row, default_currency=""):
        return (importer._row_key(row, default_currency=default_currency),
                row.get("self_account") or "", row.get("self_name") or "")
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
          and (i.parser_version=? or r.disposition in ('imported','duplicate') and f.id is not null)
        """, [case_id, table_import.ROW_KIND, *profiles, *digests, table_archive.PROFILE])
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
            if profile == table_archive.PROFILE:
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
        has_v4 = plan["profile"] == table_archive.PROFILE or (physical in v4_exact if archive else bridged in v4_bridge)
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
    store = plan.get("_plan_store")
    requested = store.mapping() if store is not None else {}
    for row in rows:
        record = plan["records"][row["_source_row"] - 1]
        requested[record["member_path"], record["source_row"]] = row
    members = [(member["member_path"], member["sha256"]) for member in plan["members"]]
    wanted = ",".join("(?,?)" for _ in members)
    fields = table_import.FLOW_FIELDS
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
    conflicting = store.mapping() if store is not None else set()
    while batch := cursor.fetchmany(1024):
        for path, position, *facts in batch:
            row = requested.get((path, position))
            if row is not None and importer._row_key(dict(zip(fields, facts))) != importer._row_key(row, default_currency="CNY"):
                conflicting.add(row["_source_row"])
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
    fields = (*table_import.FLOW_FIELDS, "self_account", "self_name")
    cursor = db.con().execute(f"""select i.parser_version,i.filename,i.sheet,i.row_kind,i.sha256,
            r.member_path,m.sha256,m.role,m.encoding,r.source_row,r.disposition,
            ({table_cancellation_links.conflict_sql('i', 'r')}) validation_conflict,
            s.flow_id,f.id,{','.join('f.' + field for field in fields)}
        from import_records r
        join import_sources i on i.case_id=r.case_id and i.source_id=r.source_id
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
        left join flow_sources s on s.case_id=r.case_id and s.source_id=r.source_id and s.source_row=r.record_no
        left join flows f on f.case_id=s.case_id and f.id=s.flow_id
        where r.case_id=? and m.sha256 in ({','.join('?' for _ in digests)})""", [case_id, *digests])
    conflicting = set()
    while batch := cursor.fetchmany(1024):
        for (profile, filename, sheet, row_kind, source_sha, path, digest, member_role, encoding,
             position, disposition, conflict, linked_id, fid, *facts) in batch:
            source = {"filename": filename, "sheet": sheet, "row_kind": row_kind, "sha256": source_sha}
            member = {"member_path": path, "sha256": digest, "role": member_role, "encoding": encoding}
            candidates = (bridge.get((digest, position), []) if _ordinary_csv_member(profile, source, member)
                          else exact.get((path, digest, position), []))
            actual = dict(zip(fields, facts)) if fid is not None else None
            for original, role, notes in candidates:
                invalid = conflict or linked_id is not None and fid is None or disposition in ("imported", "duplicate") and fid is None
                if actual is not None:
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
    """Bind cancellation evidence to every stored interpretation of its original.

    A physical source occurrence can have several historical mappings. None of
    those facts or receipts is rewritten when a newer parser excludes money.
    """
    frame, _sheet, _mode, _mapping, rows, stats = parsed
    plan = frame.attrs.get("table_plan")
    if case_id is not None:
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
    if plan["profile"] == table_archive.PROFILE:
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
    fields = (*table_import.FLOW_FIELDS, "self_account", "self_name")
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


def _preview_result(session, parsed, legacy=False):
    frame, _sheet, mode, mapping, rows, stats = parsed
    result = {"import_id": session["import_id"], "filename": session["filename"],
              "columns": list(frame.columns), "mode": mode, "mapping": mapping,
              "stats": stats, "preview": [preview_flow(row) for row in rows[:8]],
              "self_accounts_in_file": _accounts(rows)}
    plan = frame.attrs.get("feedback_plan")
    if plan is not None:
        result["feedback"] = _feedback_summary(plan)
        wanted = {row["_source_row"] for row in rows[:8]}
        positions = {r["record_no"]: r for r in plan["records"] if r["record_no"] in wanted}
        for preview, row in zip(result["preview"], rows):
            record = positions[row["_source_row"]]
            preview.update(source_member=record["member_path"], source_row=record["source_row"],
                           source_record_no=record["record_no"])
    table_plan = frame.attrs.get("table_plan")
    if table_plan is not None:
        result["table_summary"] = _table_summary(table_plan)
        wanted = {row["_source_row"] for row in rows[:8]}
        positions = {r["record_no"]: r for r in table_plan["records"] if r["record_no"] in wanted}
        for preview, row in zip(result["preview"], rows):
            record = positions[row["_source_row"]]
            preview.update(source_member=record["member_path"], source_row=record["source_row"],
                           source_record_no=record["record_no"])
    if table_plan is not None or plan is not None:
        active_plan = table_plan or plan
        if active_plan["profile"] in evidence_resolution.PROFILES:
            # All preview samples/counts are captured. Identity replay needs
            # the complete journal, not another copy of financial staging.
            # Commit independently reparses the retained original.
            rows.clear()
            active_plan.get("cancelled_rows", []).clear()
        result["resolution"] = evidence_resolution.preview(active_plan)
    if not legacy:
        result["case_id"] = str(session["case_id"])
    return result


def _table_summary(plan):
    stats = plan["stats"]
    return {"profile": plan["profile"], "record_total": len(plan["records"]),
            "accepted": len(plan["rows"]),
            "cancelled": stats.get("cancelled", 0), "excluded_flows": 0, "effective_added": None,
            **{key: stats[key] for key in ("unresolved", "non_monetary", "non_transaction", "auxiliary", "reason_counts", "currency_totals")},
            "members": plan["members"], "documents": plan["documents"],
            "mapping_evidence": [{key: str(value) for key, value in entry.items()}
                                 for entry in plan["mapping_evidence"]],
            "unsupported_attachments": stats["reason_counts"].get("unsupported_attachment", 0),
            "non_business_attachments": stats["reason_counts"].get("non_business_attachment", 0),
            "unresolved_examples": [{key: record[key] for key in ("member_path", "source_row", "reason_code")}
                                    for record in islice((record for record in plan["records"]
                                                        if record["disposition"] == "unresolved"), 10)]}


def _table_profile(session):
    profile = json.loads(session.get("request_json") or "{}").get("_table_profile", importer.PARSER_VERSION)
    if profile not in (importer.PARSER_VERSION, *table_import.SUPPORTED_PROFILES, *table_archive.SUPPORTED_PROFILES):
        raise ServiceError(400, "invalid_table_file", "来源解析规则不受支持，请重新上传预览")
    return profile


def _table_overrides(session):
    return bool(json.loads(session.get("request_json") or "{}").get("_table_overrides", False))


def _table_mapping(mode, mapping, columns, *, profile):
    fields = table_import.mapping_fields(profile)
    if (mode not in ("A", "B") or not isinstance(mapping, dict)
            or any(key not in fields or not isinstance(value, str)
                   or not value or value not in columns for key, value in mapping.items())):
        raise ServiceError(422, "invalid_mapping", "表格映射字段不存在或不明确，请重新预览")
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
    accepted_count = 0
    for number, record in enumerate(plan["records"], 1):
        if record["record_no"] != number:
            raise importer.ImportError("反馈包来源记录序号不连续")
        if record["disposition"] == "accepted":
            if record["record_no"] not in refs:
                raise importer.ImportError("反馈包来源关联不完整")
            accepted_count += 1
    if accepted_count != len(refs):
        raise importer.ImportError("反馈包来源关联不完整")
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
    for record in plan["records"]:
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
    if values:
        write_batch()


async def _upload(case_id, file, surface, *, resolve_evidence=False):
    cid = _cid(case_id) if case_id is not None else None
    filename = Path((file.filename or "transactions.csv").replace("\\", "/")).name
    extension = Path(filename).suffix
    suffix = extension.lower() or ".csv"
    if suffix not in _extensions:
        raise ServiceError(400, "unsupported_file_type", "请上传 CSV、TXT、TSV、Excel 或资金反馈 ZIP 文件")
    # Decide the parser before shortening the display name, and never remove
    # the real extension (including an unsupported one) by truncation.
    filename = (filename[:-len(extension)][:240 - len(extension)] + extension
                if extension else filename[:240])
    content = await file.read(_max_upload + 1)
    if len(content) > _max_upload:
        raise ServiceError(400, "file_too_large", "文件超过 300MB，请拆分后重新上传")
    # Preview parsing can be lengthy. Keep its already-buffered input in memory
    # until it succeeds, so exiting during parsing cannot orphan an original.
    with io.BytesIO(content) as source:
        if suffix == ".zip":
            resolve_evidence = resolve_evidence or evidence_resolution.complete_family(source)
            archive_profile = table_archive.evidence_profile(source)
        table_profile = (((archive_profile if resolve_evidence or archive_profile == table_archive.XLS_EVIDENCE_PROFILE else table_archive.PROFILE) if not (resolve_evidence and feedback_import.is_family(source)) and table_archive.should_parse(source) else None)
                         if suffix == ".zip" else table_import.XLS_PROFILE if suffix == ".xls" else table_import.EVIDENCE_PROFILE if resolve_evidence else TABLE_PROFILE)
        parsed = _parse(source, suffix=suffix, table_profile=table_profile,
                        feedback_profile=feedback_import.EVIDENCE_PROFILE if resolve_evidence and suffix == ".zip" and table_profile is None else None,
                        _auto_event_profile=table_profile == table_import.CURRENCY_REGION_PROFILE
                        and suffix in {".csv", ".tsv", ".txt", ".xlsx"})
    created = False
    try:
        import_id = uuid.uuid4().hex
        directory = _directory(create=True)
        path = directory / f"{import_id}{suffix}"
        with path.open("xb") as stream:
            created = True
            stream.write(content)
        now = _now()
        session = {"case_id": cid, "import_id": import_id, "filename": filename,
                   "sha256": hashlib.sha256(content).hexdigest()}
        evidence_resolution.operation_context(parsed, session["sha256"])
        with db.transaction():
            if cid is not None:
                _cid(cid)
                _bind_feedback_plan(cid, parsed)
                _bind_table_plan(cid, parsed, source=session)
            db.x("""insert into import_sessions(import_id,case_id,surface,filename,suffix,sha256,size_bytes,
                      mode,mapping_json,columns_json,request_json,stage,created_at,updated_at,expires_at,source_state)
                    values (?,?,?,?,?,?,?,?,?,?,?,'ready',?,?,?,'retained')""",
                 [import_id, cid, surface, filename, suffix, hashlib.sha256(content).hexdigest(), len(content),
                  parsed[2], _json(parsed[3]), _json(list(parsed[0].columns)),
                  _json({"_table_profile": parsed[0].attrs["table_plan"]["profile"]}) if "table_plan" in parsed[0].attrs
                  else _json({"_feedback_profile": parsed[0].attrs["feedback_plan"]["profile"]}) if suffix == ".zip" else None,
                  now, now, now + dt.timedelta(days=7)])
            # Preview still consumes operation-private staging. A late disk
            # failure must roll back the ready session with its retained file.
            evidence_resolution.operation_context(parsed, session["sha256"])
            result = _preview_result(session, parsed, legacy=surface == "legacy")
        result["automatic"] = True
        return result
    except BaseException:
        if created:
            path.unlink(missing_ok=True)
        raise
    finally:
        _close_staging(parsed)


async def upload(case_id, file):
    return await _upload(case_id, file, "v1")


async def legacy_upload(file, case_id=None):
    return await _upload(case_id, file, "legacy")


async def upload_directory(case_id, files, *, surface="v1"):
    """Preserve relative paths/bytes in one registered original before preview.

    Starlette admits at most 1000 multipart files. Use the same explicit bound
    here; larger exports can be supplied as a bounded ZIP archive instead.
    """
    _cid(case_id)
    if not files or len(files) > 1000:
        raise ServiceError(400, "directory_file_count", "目录一次支持1至1000个文件；更大目录请使用ZIP")
    names, total = set(), 0
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
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
            # Every selected file is retained. Unsupported attachments receive
            # an explicit disposition and a financial coverage gap.
            payload = await file.read(min(table_archive.MAX_MEMBER_BYTES, _max_upload - total) + 1)
            total += len(payload)
            if len(payload) > table_archive.MAX_MEMBER_BYTES or total > _max_upload:
                raise ServiceError(400, "file_too_large", "目录原件累计超过300MB或单文件超过256MB，请分包上传")
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload)
    from fastapi import UploadFile
    return await _upload(case_id, UploadFile(io.BytesIO(buffer.getvalue()), filename="目录原件.zip"), surface, resolve_evidence=True)


def _repreview(case_id, import_id, body, surface):
    with db.transaction():
        session = _session(case_id, import_id, surface, bind=True)
        if session["stage"] in _active_stages or session["stage"] == "done":
            raise ServiceError(409, "import_already_started", "任务已开始或已完成，请查看导入进度")
        with _source(session, snapshot=True) as source:
            is_table = _table_profile(session) in (*table_import.SUPPORTED_PROFILES, *table_archive.SUPPORTED_PROFILES)
            overrides = is_table and (_table_overrides(session) or body.mode != session["mode"]
                                      or body.mapping != json.loads(session["mapping_json"]))
            if is_table:
                _table_mapping(body.mode, body.mapping, json.loads(session["columns_json"]),
                               profile=_table_profile(session))
                if overrides and _table_profile(session) in table_archive.SUPPORTED_PROFILES:
                    raise ServiceError(422, "invalid_mapping", "反馈包按成员独立校验，不能使用整包字段覆盖")
            parsed = _parse(source, body.mode if not is_table or overrides else None,
                            body.mapping if not is_table or overrides else None, suffix=session["suffix"],
                            feedback_profile=_feedback_profile(session) if session["suffix"] == ".zip" else None,
                            table_profile=_table_profile(session))
        try:
            evidence_resolution.operation_context(parsed, session["sha256"])
            _bind_feedback_plan(session["case_id"], parsed)
            _bind_table_plan(session["case_id"], parsed, source=session)
            now = _now()
            db.x("""update import_sessions set mode=?,mapping_json=?,columns_json=?,stage='ready',error_code=null,
                      updated_at=?,expires_at=? where import_id=?""",
                 [parsed[2], _json(parsed[3]), _json(list(parsed[0].columns)), now, now + dt.timedelta(days=7), import_id])
            db.x("delete from import_file_gc where import_id=?", [import_id])
            if is_table:
                request = json.loads(session.get("request_json") or "{}")
                request["_table_overrides"] = overrides
                db.x("update import_sessions set request_json=? where import_id=?", [_json(request), import_id])
            return _preview_result(session, parsed, legacy=surface == "legacy")
        finally:
            _close_staging(parsed)


def repreview(case_id, import_id, body):
    return _repreview(case_id, import_id, body, "v1")


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


def _mark_mains(cid, rows, receipt, request):
    row_self = {}
    for row in rows:
        account = (row.get("self_account") or "").strip()
        if account:
            row_self.setdefault(account, (row.get("self_name") or "").strip())
    if request["mark_accounts"] is None:
        mains = {item["account"]: item.get("name") or "" for item in receipt.get("self_accounts", []) if item.get("account")}
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
    for key in ("feedback", "table_summary", "source_id", "resolution"):
        if key in raw:
            result[key] = raw[key]
    return result


def _standalone_csv_keys(plan, rows):
    """Bind archive rows to the same bytes and physical rows as a plain CSV.

    These keys exist only during commit. The journal keeps the full archive
    member path and its original record identity.
    """
    members = {member["member_path"]: member for member in plan["members"]}
    keys = {}
    for row in rows:
        position = row["_source_row"]
        record = plan["records"][position - 1]
        member = members[record["member_path"]]
        source_row, digest = record["source_row"], member["sha256"]
        if (record["record_no"] != position or record["disposition"] != "accepted"
                or type(source_row) is not int or source_row <= 0
                or member["role"] != "table"
                or member["encoding"] not in ("utf-8-sig", "gb18030", "utf-16")
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or record["record_key"] != table_import.record_key(record["member_path"], digest, source_row)):
            raise import_validation.IntegrityError("invalid archive CSV identity")
        keys[position] = table_import.record_key("table.csv", digest, source_row)
    return keys


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
        is_table = _table_profile(session) in (*table_import.SUPPORTED_PROFILES, *table_archive.SUPPORTED_PROFILES)
        overrides = _table_overrides(session)
        with _source(session, snapshot=True) as source:
            parsed = _parse(
                source, request["mode"] if not is_table or overrides else None,
                request["mapping"] if not is_table or overrides else None, suffix=session["suffix"],
                feedback_profile=_feedback_profile(session) if session["suffix"] == ".zip" else None,
                table_profile=_table_profile(session))
            evidence_resolution.operation_context(parsed, session["sha256"])
            _frame, sheet, mode, mapping, rows, stats = parsed
        plan = _frame.attrs.get("feedback_plan")
        table_plan = _frame.attrs.get("table_plan")
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
        with db.import_transaction():
            current = _session(session["case_id"], import_id, session["surface"])
            if current["owner_token"] != OWNER_TOKEN or current["stage"] != "importing":
                return
            _bind_feedback_plan(session["case_id"], parsed)
            _bind_table_plan(session["case_id"], parsed, source=session)
            manifest = import_validation.table_manifest(table_plan, rows) if table_plan is not None else None
            standalone_keys = (_standalone_csv_keys(table_plan, rows)
                               if table_plan is not None and table_plan["profile"] in table_archive.SUPPORTED_PROFILES
                               and table_plan["profile"] not in table_archive.EVIDENCE_PROFILES else None)
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
                                           standalone_csv_keys=standalone_keys)
            receipt["source_id"] = import_id
            main_rows = rows
            if provenance["parser_version"] in evidence_resolution.PROFILES:
                db.x("insert into evidence_sources(case_id,source_id,batch_id,active) values (?,?,?,true)", [session["case_id"], import_id, receipt["batch_id"]])
                # Keep only the first self-name declaration needed later; the
                # immutable manifest and full journal still verify every fact.
                main_rows = [{"self_account": item["account"], "self_name": item.get("name") or ""}
                             for item in receipt.get("self_accounts", [])]
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
                del manifest
                if "table_summary" not in receipt:
                    receipt["table_summary"] = _table_summary(table_plan)
                receipt["table_summary"].update(
                    excluded_flows=db.q_raw("""select (select count(*) from flows where case_id=?) -
                        (select count(*) from effective_flows where case_id=?) n""",
                        [session["case_id"], session["case_id"]])[0]["n"],
                    effective_added=db.q_raw("select count(*) n from effective_flows where case_id=? and batch_id=?",
                                             [session["case_id"], receipt["batch_id"]])[0]["n"])
            _mark_mains(session["case_id"], main_rows, receipt, request)
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
            receipt["resolution"] = evidence_resolution.rebuild(session["case_id"])
            from .. import event_resolution
            event_resolution.rebuild(session["case_id"], changed_sources=[import_id])
            receipt = _receipt(receipt)
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
        _close_staging(parsed)
        if journal_records is not None:
            journal_records.close()


def _commit(case_id, import_id, body, surface):
    with db.transaction():
        session = _session(case_id, import_id, surface, bind=True)
        if session["stage"] in (*_active_stages, "done"):
            result = {"import_id": import_id, "started": False}
        else:
            _source(session)
            is_table = _table_profile(session) in (*table_import.SUPPORTED_PROFILES, *table_archive.SUPPORTED_PROFILES)
            mapping = (_table_mapping(body.mode, body.mapping, json.loads(session["columns_json"] or "[]"),
                                      profile=_table_profile(session)) if is_table
                       else _mapping(body.mode, body.mapping, json.loads(session["columns_json"] or "[]")))
            if is_table and (body.mode != session["mode"] or mapping != json.loads(session["mapping_json"])):
                raise ServiceError(409, "preview_changed", "映射与已校验预览不同，请先重新预览")
            if session["suffix"] == ".zip" and not is_table and (body.mode != "B" or mapping != feedback_import.MAPPING):
                raise ServiceError(422, "fixed_feedback_mapping", "资金反馈包的格式规则不可更改，请重新预览")
            request = _request(body, mapping)
            if session["suffix"] == ".zip":
                request["_feedback_profile"] = _feedback_profile(session)
            if is_table:
                request.update(_table_profile=_table_profile(session), _table_overrides=_table_overrides(session))
            db.x("""update import_sessions set mode=?,mapping_json=?,request_json=?,stage='parsing',done=0,total=0,
                      result_json=null,error_code=null,attempt=attempt+1,owner_token=?,updated_at=?
                      where import_id=?""", [body.mode, _json(mapping), _json(request), OWNER_TOKEN, _now(), import_id])
            result = {"import_id": import_id, "started": True}
    if result["started"]:
        try:
            threading.Thread(target=_run, args=(import_id,), daemon=True, name=f"import-{import_id[:8]}").start()
        except Exception:
            with db.transaction():
                now = _now()
                db.x("""update import_sessions set stage='error',error_code='import_failed',updated_at=?,expires_at=?
                          where import_id=? and owner_token=? and stage='parsing'""",
                     [now, now + dt.timedelta(days=30), import_id, OWNER_TOKEN])
            raise ServiceError(500, "import_start_failed", "无法启动导入任务，请重试") from None
    if surface == "v1":
        result["case_id"] = str(case_id)
    return result


def commit(case_id, import_id, body):
    return _commit(case_id, import_id, body, "v1")


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
    if surface == "v1":
        result["case_id"] = str(case_id)
    return result


def progress(case_id, import_id):
    return _progress(case_id, import_id, "v1")


def legacy_progress(import_id, case_id=None):
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


def get_source_file(case_id, source_id):
    with db._lock:
        session = _source_session(case_id, source_id)
        return _source(session, snapshot=True), session["filename"]


def _journal_source(case_id, source_id):
    cid = _cid(case_id)
    if not isinstance(source_id, str) or not re.fullmatch(r"[0-9a-f]{12,32}", source_id):
        raise _missing()
    found = db.q_raw("select * from import_sources where case_id=? and source_id=? and row_kind in ('archive_record','table_record')",
                     [cid, source_id])
    if not found:
        raise _missing()
    return cid, found[0]


def _record_items(cid, source_id, *, disposition=None, limit=None, offset=0):
    where, params = "r.case_id=? and r.source_id=?", [cid, source_id]
    if disposition is not None:
        where += " and r.disposition=?"
        params.append(disposition)
    paging = " limit ? offset ?" if limit is not None else ""
    if limit is not None:
        params += [limit, offset]
    rows = db.q_raw(f"""select r.*,f.flow_id from import_records r left join flow_sources f
                        on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no
                        where {where} order by r.record_no{paging}""", params)
    return [{**{k: r[k] for k in ("record_no", "member_path", "source_row", "record_key", "disposition", "reason_code")},
             "details": json.loads(r["details_json"]), "flow_id": str(r["flow_id"]) if r["flow_id"] is not None else None}
            for r in rows]


def list_records(case_id, source_id, page=1, page_size=100, disposition=None):
    with db._lock:
        cid, _ = _journal_source(case_id, source_id)
        condition, params = (" and disposition=?", [disposition]) if disposition is not None else ("", [])
        total = db.q_raw(f"select count(*) n from import_records where case_id=? and source_id=?{condition}",
                         [cid, source_id, *params])[0]["n"]
        return {"case_id": str(cid), "source_id": source_id, "total": total, "page": page, "page_size": page_size,
                "items": _record_items(cid, source_id, disposition=disposition, limit=page_size, offset=(page - 1) * page_size)}


def get_disposition_report(case_id, source_id):
    """Snapshot the complete journal before download; no pagination truncation."""
    stream = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b", dir=db.DATA_DIR)
    try:
        with db._lock:
            cid, source = _journal_source(case_id, source_id)
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
    profiles = sorted({*feedback_import.SUPPORTED_PROFILES, *table_import.SUPPORTED_PROFILES,
                       *table_archive.SUPPORTED_PROFILES})
    placeholders = ",".join("?" for _ in profiles)
    return db.q_raw(f"""select (select count(*) from flow_sources where source_id=?) +
                         (select count(*) from import_records r join import_sources s
                          on s.case_id=r.case_id and s.source_id=r.source_id where r.source_id=?
                          and (r.disposition not in ('non_transaction','auxiliary','removed')
                               or s.parser_version not in ({placeholders}))) n""",
                    [source_id, source_id, *profiles])[0]["n"]


def list_sessions(case_id, limit=100):
    cid = _cid(case_id)
    limit = max(1, min(int(limit), 200))
    rows = db.q_raw("""select * from import_sessions where case_id=? order by created_at desc,import_id desc limit ?""", [cid, limit])
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
