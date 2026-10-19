"""Validated versioned case packages and compensating photo operations."""
import datetime
import io
import json
import math
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import shutil
import tempfile
import uuid
import zipfile
import zlib

import duckdb
from fastapi import HTTPException

from . import db, importer

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
_ALL_PKG_COLS = {**CASE_PKG_COLS, **PROVENANCE_PKG_COLS}
_PHOTO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.(?:jpg|jpeg|png|webp|bmp)", re.I)
_TYPES = {
    "id": "bigint", "flow_id": "bigint", "batch_id": "bigint", "canvas_id": "bigint",
    "trade_time": "timestamp", "tracked_at": "timestamp", "imported_at": "timestamp",
    "updated_at": "timestamp", "marked_at": "timestamp", "amount": "decimal(18,2)",
    "balance": "decimal(18,2)", "rows_total": "bigint", "rows_imported": "bigint",
    "rows_skipped": "bigint", "rows_dup": "bigint", "crop_cx": "double",
    "crop_cy": "double", "crop_r": "double", "scale": "double",
    "source_row": "bigint", "size_bytes": "bigint",
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
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr("meta.json", json.dumps({
                "name": case_name, "exported_at": datetime.datetime.now().isoformat(), "format": 2,
                "source_files": "not_included",
            }, ensure_ascii=False))
            for table, cols in _ALL_PKG_COLS.items():
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
            if not isinstance(meta, dict) or type(meta.get("format")) is not int or meta["format"] not in (1, 2):
                _invalid("不支持的格式版本")
            source_members = {f"{table}.parquet" for table in PROVENANCE_PKG_COLS} & set(names)
            if source_members and (meta["format"] != 2 or len(source_members) != 2):
                _invalid("来源追溯表必须在格式 2 中成对提供")
            if meta["format"] == 2 and meta.get("source_files") != "not_included":
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
                if table in PROVENANCE_PKG_COLS:
                    integral_fields = ("source_row", "flow_id") if table == "flow_sources" else ("size_bytes",)
                    for field in integral_fields:
                        if check.execute(f"select 1 from read_parquet(?) where {field} is null or not regexp_full_match(cast({field} as varchar),'[0-9]+') limit 1", [str(path)]).fetchone():
                            _invalid(f"{table} 整数字段无效")
                casts = ",".join(f"cast({c} as {_TYPES.get(c, 'varchar')}) as {c}" for c in cols)
                check.execute(f"create table {table} as select {casts} from read_parquet(?)", [str(path)])
                key = ("id" if table in ("flows", "batches", "canvases")
                       else "flow_id,canvas_id" if table == "tracked_flows"
                       else "source_id" if table == "import_sources"
                       else "source_id,source_row" if table == "flow_sources" else "account")
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
                    if (not isinstance(source_id, str) or not re.fullmatch(r"[0-9a-f]{12,32}", source_id)
                            or not isinstance(filename, str) or not filename.strip()
                            or not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256)
                            or size_bytes is None or size_bytes < 0 or mode not in ("A", "B")
                            or not isinstance(mapping, dict)
                            or any(k not in (*importer.MODE_FIELDS.get(mode, []), "_time2", "self_account", "self_name")
                                   or not isinstance(v, str) or not v.strip() for k, v in mapping.items())
                            or not importer._validate(mapping, mode)
                            or not isinstance(sheet, str) or not isinstance(parser_version, str) or not parser_version
                            or suffix not in (".csv", ".tsv", ".txt", ".xlsx", ".xls")
                            or (suffix in (".xlsx", ".xls")) != bool(sheet)
                            or row_kind not in ("csv_record", "worksheet_row", "unknown")
                            or (row_kind == "worksheet_row" and suffix not in (".xlsx", ".xls"))
                            or (row_kind == "csv_record" and suffix in (".xlsx", ".xls"))
                            or imported_at is None):
                        _invalid("来源元数据无效")
                if check.execute("select 1 from flow_sources s left join import_sources p on s.source_id=p.source_id left join flows f on s.flow_id=f.id where p.source_id is null or f.id is null or s.source_row<=0 or s.source_row is null or s.disposition is null or s.disposition not in ('imported','duplicate') limit 1").fetchone():
                    _invalid("来源行或流水引用无效")
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
                for table in ("flow_sources", "import_sources", "import_sessions"):
                    con.execute(f"delete from {table} where case_id=?", [case_id])
                for table in CASE_PKG_TABLES:
                    con.execute(f"delete from {table} where case_id=?", [case_id])
                con.execute("delete from cases where id=?", [case_id])
        except BaseException:
            if had_photos:
                shutil.copytree(backup, photo_dir, dirs_exist_ok=True)
            raise
    return {"ok": True, "deleted_flows": deleted}
