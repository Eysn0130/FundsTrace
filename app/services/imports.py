"""Persistent import workflow shared by the standalone HTTP surfaces.

The database owns session state; disk filenames never grant access. A completed
receipt is committed with its flows and remains readable after source cleanup.
"""
import datetime as dt
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

from app import db, importer
from .cases import ServiceError, preview_flow, require_case


OWNER_TOKEN = uuid.uuid4().hex
_extensions = {".csv", ".txt", ".tsv", ".xlsx", ".xls"}
_max_upload = 300 * 1024 * 1024
_active_stages = ("parsing", "importing")
_messages = {"parsing": "解析文件…", "importing": "写入流水…", "done": "导入完成",
             "error": "导入失败，请检查文件与列映射后重试", "ready": "等待确认导入"}
_errors = {"import_interrupted": "上次导入被中断，未完成的数据已回滚，请确认后重试",
           "source_changed": "上传文件校验失败，请重新上传文件",
           "source_missing": "上传文件已清理或不存在，请重新上传文件",
           "no_importable_rows": "没有可导入的数据行，请检查列映射",
           "import_failed": _messages["error"]}


def _now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


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


def _parse(source, mode=None, mapping=None, *, suffix=None):
    try:
        frame, sheet = importer.read_table(source, suffix=suffix)
        if mapping is None:
            mode, mapping = importer.analyze(frame, mode_hint=mode)
        mapping = _mapping(mode, mapping, list(frame.columns))
        rows, stats = importer.build_rows(frame, mode, mapping)
        return frame, sheet, mode, mapping, rows, stats
    except ServiceError:
        raise
    except Exception:
        raise _invalid_file() from None


def _preview_result(session, parsed, legacy=False):
    frame, _sheet, mode, mapping, rows, stats = parsed
    result = {"import_id": session["import_id"], "filename": session["filename"],
              "columns": list(frame.columns), "mode": mode, "mapping": mapping,
              "stats": stats, "preview": [preview_flow(row) for row in rows[:8]],
              "self_accounts_in_file": _accounts(rows)}
    if not legacy:
        result["case_id"] = str(session["case_id"])
    return result


async def _upload(case_id, file, surface):
    cid = _cid(case_id) if case_id is not None else None
    filename = Path((file.filename or "transactions.csv").replace("\\", "/")).name
    extension = Path(filename).suffix
    suffix = extension.lower() or ".csv"
    if suffix not in _extensions:
        raise ServiceError(400, "unsupported_file_type", "请上传 CSV、TXT、TSV 或 Excel 流水文件")
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
        parsed = _parse(source, suffix=suffix)
    import_id = uuid.uuid4().hex
    directory = _directory(create=True)
    path = directory / f"{import_id}{suffix}"
    created = False
    try:
        with path.open("xb") as stream:
            created = True
            stream.write(content)
        now = _now()
        session = {"case_id": cid, "import_id": import_id, "filename": filename}
        with db.transaction():
            if cid is not None:
                _cid(cid)
            db.x("""insert into import_sessions(import_id,case_id,surface,filename,suffix,sha256,size_bytes,
                      mode,mapping_json,columns_json,stage,created_at,updated_at,expires_at,source_state)
                    values (?,?,?,?,?,?,?,?,?,?,'ready',?,?,?,'retained')""",
                 [import_id, cid, surface, filename, suffix, hashlib.sha256(content).hexdigest(), len(content),
                  parsed[2], _json(parsed[3]), _json(list(parsed[0].columns)), now, now, now + dt.timedelta(days=7)])
        return _preview_result(session, parsed, legacy=surface == "legacy")
    except BaseException:
        if created:
            path.unlink(missing_ok=True)
        raise


async def upload(case_id, file):
    return await _upload(case_id, file, "v1")


async def legacy_upload(file, case_id=None):
    return await _upload(case_id, file, "legacy")


def _repreview(case_id, import_id, body, surface):
    with db.transaction():
        session = _session(case_id, import_id, surface, bind=True)
        if session["stage"] in _active_stages or session["stage"] == "done":
            raise ServiceError(409, "import_already_started", "任务已开始或已完成，请查看导入进度")
        with _source(session, snapshot=True) as source:
            parsed = _parse(source, body.mode, body.mapping, suffix=session["suffix"])
        now = _now()
        db.x("""update import_sessions set mode=?,mapping_json=?,columns_json=?,stage='ready',error_code=null,
                  updated_at=?,expires_at=? where import_id=?""",
             [parsed[2], _json(parsed[3]), _json(list(parsed[0].columns)), now, now + dt.timedelta(days=7), import_id])
        db.x("delete from import_file_gc where import_id=?", [import_id])
        return _preview_result(session, parsed, legacy=surface == "legacy")


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
        db.x("insert or replace into main_accounts(case_id,account,name,source) values (?,?,?,'auto')", [cid, account, name])
        receipt["main_detected"].append({"account": account, "name": name})


def _receipt(raw):
    return {"total": raw.get("total", 0), "ok": raw.get("ok", 0), "skipped": raw.get("skipped", 0),
            "rows_dup": raw.get("rows_dup", 0), "batch_id": str(raw["batch_id"]),
            "bad_examples": raw.get("bad_examples", []), "self_accounts": raw.get("self_accounts", []),
            "self_accounts_count": raw.get("self_accounts_count", 0), "main_matched": raw.get("main_matched", []),
            "accounts_in_batch": raw.get("accounts_in_batch", 0), "main_detected": raw.get("main_detected", [])}


def _run(import_id):
    try:
        with db._lock:
            found = db.q_raw("select * from import_sessions where import_id=? and owner_token=? and stage='parsing'",
                             [import_id, OWNER_TOKEN])
            if not found:
                return
            session = found[0]
        request = json.loads(session["request_json"])
        with _source(session, snapshot=True) as source:
            _frame, sheet, mode, mapping, rows, stats = _parse(source, request["mode"], request["mapping"], suffix=session["suffix"])
        if not rows:
            raise ServiceError(422, "no_importable_rows", _errors["no_importable_rows"])
        with db.transaction():
            current = _session(session["case_id"], import_id, session["surface"])
            if current["owner_token"] != OWNER_TOKEN or current["stage"] != "parsing":
                return
            db.x("update import_sessions set stage='importing',total=?,updated_at=? where import_id=?",
                 [len(rows), _now(), import_id])
        # One shared transaction includes the receipt, provenance and account
        # changes. Restart can never observe durable rows without their receipt.
        with db.transaction():
            current = _session(session["case_id"], import_id, session["surface"])
            if current["owner_token"] != OWNER_TOKEN or current["stage"] != "importing":
                return
            provenance = {"source_id": import_id, "filename": session["filename"], "sha256": session["sha256"],
                          "size_bytes": session["size_bytes"], "mode": mode, "mapping": mapping,
                          "sheet": sheet, "parser_version": getattr(importer, "PARSER_VERSION", "funds-trace-import-v1"),
                          "row_kind": _frame.attrs.get("source_row_kind", "unknown")}
            receipt = importer.commit_rows(session["filename"], mode, rows, stats,
                                           case_id=session["case_id"], provenance=provenance)
            _mark_mains(session["case_id"], rows, receipt, request)
            receipt = _receipt(receipt)
            db.x("""update import_sessions set stage='done',done=?,total=?,result_json=?,error_code=null,
                      updated_at=?,expires_at=? where import_id=? and owner_token=?""",
                 [len(rows), len(rows), _json(receipt), _now(), _now() + dt.timedelta(days=30), import_id, OWNER_TOKEN])
    except Exception as error:
        code = error.code if isinstance(error, ServiceError) and error.code in _errors else "import_failed"
        with db.transaction():
            now = _now()
            db.x("""update import_sessions set stage='error',done=0,result_json=null,error_code=?,updated_at=?,expires_at=?
                      where import_id=? and owner_token=? and stage in ('parsing','importing')""",
                 [code, now, now + dt.timedelta(days=30), import_id, OWNER_TOKEN])


def _commit(case_id, import_id, body, surface):
    with db.transaction():
        session = _session(case_id, import_id, surface, bind=True)
        if session["stage"] in (*_active_stages, "done"):
            result = {"import_id": import_id, "started": False}
        else:
            _source(session)
            mapping = _mapping(body.mode, body.mapping, json.loads(session["columns_json"] or "[]"))
            request = _request(body, mapping)
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
                      "request": json.loads(row["request_json"]) if row["request_json"] else None,
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
            references = db.q_raw("select count(*) n from flow_sources where source_id=?", [session["import_id"]])[0]["n"]
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
            refs = db.q_raw("select count(*) n from flow_sources where source_id=?", [item["import_id"]])[0]["n"]
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
