"""Bounded bank export envelopes; no extraction and no persistent writes."""
from __future__ import annotations

import codecs
import csv
from decimal import Decimal, localcontext
import hashlib
import io
from pathlib import PurePosixPath
import stat
import zipfile

from . import feedback_import, table_import, table_cancellations, import_attachments


IDENTITY_PROFILE = "funds-trace-table-archive-v1"
REFERENCE_PROFILE = "funds-trace-table-archive-v2"
PREVIOUS_PROFILE = REFERENCE_PROFILE
CANCELLATION_PROFILE = "funds-trace-table-archive-v3"
HEADER_PROFILE = "funds-trace-table-archive-v4"
LEGACY_EVIDENCE_PROFILE = "funds-trace-table-archive-v5"
EVIDENCE_PROFILE = "funds-trace-table-archive-v6"
EVIDENCE_PROFILES = frozenset((LEGACY_EVIDENCE_PROFILE, EVIDENCE_PROFILE))
PROFILE = HEADER_PROFILE
CANCELLATION_PROFILES = frozenset((CANCELLATION_PROFILE, HEADER_PROFILE, *EVIDENCE_PROFILES))
SUPPORTED_PROFILES = frozenset((IDENTITY_PROFILE, REFERENCE_PROFILE, *CANCELLATION_PROFILES))
REFERENCE_PROFILES = frozenset((REFERENCE_PROFILE, *CANCELLATION_PROFILES))
MAX_MEMBERS = 256
EVIDENCE_MAX_MEMBERS = 2048
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_RECORDS = 1_000_000
EVIDENCE_MAX_RECORDS = 2_000_000
EVIDENCE_MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_DEPTH = 2

_TRANSACTION = frozenset(table_import.BANK_FEEDBACK_32_COLUMNS)
AUXILIARY_SCHEMAS = {
    "manifest": tuple("任务流水号 银行名称 主体类别 证账号码 账卡号 发送时间 反馈时间 反馈结果 入库状态 请求单号 查询结果".split()),
    "client": tuple("客户名称 证照类型 证照号码 住址地址 单位地址 联系电话 联系手机 单位电话 住宅电话 工作单位 邮箱地址 代办人姓名 代办人证件类型 代办人证件号码 国税纳税号 地税纳税号 法人代表 法人代表证件类型 法人代表证件号码 出生日期 户籍地址 客户工商执照号码".split()),
    "account": tuple("账户开户名称 开户人证件号码 交易卡号 交易账号 账号开户时间 账户余额 可用余额 币种 开户网点代码 开户网点 账户状态 炒汇标志名称 销户日期 账户类型 开户联系方式 通信地址 联系电话 代理人 代理人电话 备注 开户省份 开户城市 账号开户银行 客户代码 法人代表 客户工商执照号码 法人代表证件号码 住宅地址 邮政编码 代办人证件号码 邮箱地址 关联资金账户 地税纳税号 单位电话 代办人证件类型 住宅电话 法人代表证件类型 国税纳税号 单位地址 工作单位 销户网点 最后交易时间 账户销户银行".split()),
    "subaccount": tuple("开户账号 子账户账号 余额 可用余额 子账户类别 币种 钞汇标识 账户状态 账户序号".split()),
    "freeze": tuple("账号 冻结措施类型 冻结金额 冻结机关 冻结开始日期 冻结截止日期 措施序号 备注".split()),
}
_AUXILIARY = {frozenset(columns): role for role, columns in AUXILIARY_SCHEMAS.items()}


def _header(payload):
    encodings = ("utf-16",) if payload.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
    for encoding in encodings:
        try:
            text = codecs.getincrementaldecoder(encoding)(errors="strict").decode(payload[:65536], final=False)
            row = next(csv.reader(io.StringIO(text, newline=""), strict=True))
            return [cell.strip() for cell in row], encoding
        except (UnicodeError, csv.Error, StopIteration):
            continue
    return [], None


def should_parse(source):
    """Only the new envelope family opts in; historical flat profiles stay put."""
    try:
        source.seek(0)
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            if any(info.filename.lower().endswith((".xlsx", ".txt", ".tsv")) for info in infos):
                return True
            legacy, ordinary = False, False
            for info in infos:
                if info.filename.lower().endswith(".csv") and not info.flag_bits & 1:
                    with archive.open(info) as stream:
                        header, _ = _header(stream.read(65536))
                    legacy = legacy or frozenset(header) == frozenset(feedback_import.COLUMNS)
                    ordinary = ordinary or bool(header and frozenset(header) not in {frozenset(value) for value in feedback_import.SCHEMAS.values()})
            if ordinary and not legacy:
                return True
            if any(not info.is_dir() and info.filename.lower().endswith(".zip") for info in infos):
                return True
            for info in infos:
                if info.filename.lower().endswith(".csv") and not info.flag_bits & 1:
                    with archive.open(info) as stream:
                        header, _ = _header(stream.read(65536))
                    if len(header) == len(_TRANSACTION) and frozenset(header) == _TRANSACTION:
                        return True
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
        return False
    finally:
        source.seek(0)
    return False


def _path(name):
    if (not isinstance(name, str) or not name or len(name) > 4096 or "\\" in name
            or ":" in name or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or name.startswith("/") or any(part in ("", ".", "..") for part in name.rstrip("/").split("/"))):
        raise table_import.TableImportError("反馈包包含不安全或不明确的成员路径")
    return name.rstrip("/")


def _auxiliary(payload, path, header, encoding, role, *, evidence=False):
    try:
        text = payload.decode(encoding, errors="strict")
        csv.field_size_limit(max(csv.field_size_limit(), table_import.MAX_FIELD))
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        next(reader)
        digest = hashlib.sha256(payload).hexdigest()
        records = []
        from . import table_contracts
        contract_details = table_contracts.compile_contract({}).details() if evidence else {}
        for position, cells in enumerate(reader, start=2):
            if len(records) >= (EVIDENCE_MAX_RECORDS if evidence else MAX_RECORDS) or any(len(cell) > table_import.MAX_FIELD for cell in cells):
                raise table_import.TableImportError("反馈包记录或字段超出安全处理范围")
            valid = len(cells) == len(header)
            reason = "auxiliary_table" if valid else "column_count_mismatch"
            records.append({"record_no": len(records) + 1, "member_path": path, "source_row": position,
                            "record_key": table_import.record_key(path, digest, position),
                            "disposition": "auxiliary" if valid else "unresolved", "reason_code": reason,
                            "details": {"sheet": "", "source_row": str(position), "source_role": role,
                                        "financial_effect": "not_a_flow" if valid else "undetermined",
                                        "reason": "辅助来源资料，不生成资金流水" if valid else "辅助记录列数与表头不一致",
                                        **contract_details}})
            if valid and evidence:
                from . import evidence_resolution
                value = evidence_resolution.auxiliary_evidence(header, cells, role)
                if value:
                    records[-1]["details"][evidence_resolution.DETAIL_FIELD] = value
    except (UnicodeError, csv.Error, StopIteration):
        raise table_import.TableImportError("反馈包辅助CSV结构损坏，整包未接纳") from None
    return {"rows": [], "records": records,
            "members": [{"member_path": path, "role": "auxiliary", "sha256": digest,
                         "size_bytes": len(payload), "encoding": encoding, "row_count": len(records)}],
            "documents": [{"member_path": path, "sheet": "", "header_row": 1, "row_count": len(records), "role": role}],
            "mapping_evidence": [], "columns": [], "mapping": {}, "mode": "B"}


def parse(source, profile=PROFILE):
    if profile not in SUPPORTED_PROFILES:
        raise table_import.TableImportError("不支持的反馈包解析规则")
    plan = {"profile": profile, "mode": "B", "mapping": {}, "columns": [], "sheet": "",
            "rows": [], "records": [], "members": [], "documents": [], "mapping_evidence": []}
    if profile in CANCELLATION_PROFILES:
        plan.update(cancelled_rows=[], cancellations=[])
    budget = {"members": 0, "bytes": 0}
    member_limit = EVIDENCE_MAX_MEMBERS if profile in EVIDENCE_PROFILES else MAX_MEMBERS
    record_limit = EVIDENCE_MAX_RECORDS if profile in EVIDENCE_PROFILES else MAX_RECORDS
    expanded_limit = EVIDENCE_MAX_EXPANDED_BYTES if profile in EVIDENCE_PROFILES else MAX_EXPANDED_BYTES
    seen = set()
    selected = False
    from .import_staging import DetailsPool, PackedDetails
    details_pool = DetailsPool() if profile in EVIDENCE_PROFILES else None
    if details_pool is not None:
        plan["_details_pool"] = details_pool

    def merge(part, prefix, family=""):
        nonlocal selected
        offset = len(plan["records"])
        if offset + len(part["records"]) > record_limit:
            raise table_import.TableImportError("反馈包记录数超出安全处理范围")
        hashes = {member["member_path"]: member["sha256"] for member in part["members"]}
        for record in part["records"]:
            old_path = record["member_path"]
            path = prefix if old_path in {"table.csv", prefix} else f"{prefix}/{old_path}"
            record.update(record_no=offset + record["record_no"], member_path=path,
                          record_key=table_import.record_key(path, hashes[old_path], record["source_row"]))
            for field in ("paired_record_no", "cancellation_witness_record_no"):
                if field in record["details"]:
                    record["details"][field] = str(offset + int(record["details"][field]))
            if details_pool is not None and not isinstance(record["details"], PackedDetails):
                record["details"] = details_pool.pack(record["details"])
        for row in (*part["rows"], *part.get("cancelled_rows", [])):
            record = part["records"][row["_source_row"] - 1]
            row.update(_source_row=record["record_no"], _source_sheet="", _source_row_kind=table_import.ROW_KIND,
                       _record_key=record["record_key"])
        for member in part["members"]:
            member["member_path"] = prefix if member["member_path"] in {"table.csv", prefix} else f"{prefix}/{member['member_path']}"
        for field in ("documents", "mapping_evidence"):
            for entry in part[field]:
                if entry.get("member_path"):
                    entry["member_path"] = prefix if entry["member_path"] in {"table.csv", prefix} else f"{prefix}/{entry['member_path']}"
            plan[field].extend(part[field])
        if not selected and part["columns"]:
            plan.update(mode=part["mode"], mapping=part["mapping"], columns=part["columns"])
            selected = True
        plan.setdefault("_member_families", {}).update({member["member_path"]: family for member in part["members"]})
        for field in ("rows", "records", "members"):
            plan[field].extend(part[field])
        if profile in CANCELLATION_PROFILES:
            plan["cancelled_rows"].extend(part.get("cancelled_rows", []))
            plan["cancellations"].extend({key: value + offset for key, value in pair.items()}
                                         for pair in part.get("cancellations", []))

    def visit(stream, prefix="", depth=0):
        try:
            with zipfile.ZipFile(stream) as archive:
                local = set()
                for info in archive.infolist():
                    budget["members"] += 1
                    if budget["members"] > member_limit:
                        raise table_import.TableImportError("反馈包成员数超出安全处理范围")
                    name = _path(info.filename)
                    if name in local:
                        raise table_import.TableImportError("反馈包包含重复成员路径")
                    local.add(name)
                    if info.flag_bits & 1 or stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
                        raise table_import.TableImportError("反馈包不接受加密成员或符号链接")
                    if info.is_dir():
                        continue
                    path = f"{prefix}/{name}" if prefix else name
                    if path in seen:
                        raise table_import.TableImportError("反馈包成员来源位置冲突")
                    seen.add(path)
                    suffix = PurePosixPath(name).suffix.lower()
                    if profile not in EVIDENCE_PROFILES and suffix not in (".csv", ".zip"):
                        raise table_import.TableImportError("反馈包包含尚未适配的成员类型，整包未接纳")
                    if not 0 <= info.file_size <= MAX_MEMBER_BYTES:
                        raise table_import.TableImportError("反馈包单成员超出安全处理大小")
                    chunks, size = [], 0
                    with archive.open(info) as leaf:
                        while block := leaf.read(min(1024 * 1024, MAX_MEMBER_BYTES - size + 1)):
                            size += len(block)
                            budget["bytes"] += len(block)
                            if size > MAX_MEMBER_BYTES or budget["bytes"] > expanded_limit:
                                raise table_import.TableImportError("反馈包累计展开量超出安全处理范围")
                            chunks.append(block)
                    if size != info.file_size:
                        raise table_import.TableImportError("反馈包成员大小校验失败")
                    payload = b"".join(chunks)
                    # The complete, size-checked payload owns these bytes now.
                    # Keep no second chunk buffer through member interpretation.
                    chunks.clear()
                    if suffix == ".zip":
                        if depth >= MAX_DEPTH:
                            raise table_import.TableImportError("反馈包嵌套层数超出安全处理范围")
                        visit(io.BytesIO(payload), path, depth + 1)
                    elif suffix not in (".csv", ".tsv", ".txt", ".xlsx"):
                        merge(import_attachments.plan(payload, path), path, prefix)
                    else:
                        header, encoding = _header(payload)
                        if (profile not in EVIDENCE_PROFILES and len(header) == len(feedback_import.COLUMNS)
                                and frozenset(header) == frozenset(feedback_import.COLUMNS)):
                            # Generic mapping cannot downgrade a verified profile's
                            # cancellation/identity semantics inside a new envelope.
                            raise table_import.TableImportError("新反馈包包含需使用原规则的旧版交易表，整包未接纳；未降级解释资金记录")
                        role = _AUXILIARY.get(frozenset(header)) if len(header) == len(set(header)) else None
                        if profile in EVIDENCE_PROFILES and role is None:
                            signature = frozenset(header)
                            role = next((name for name, names in feedback_import.SCHEMAS.items() if name != "transaction" and signature == frozenset(names)), None)
                            role = {"task": "manifest", "address": "client", "phone": "client"}.get(role, role)
                        with localcontext() as context:
                            if profile in CANCELLATION_PROFILES:
                                context.prec = max(context.prec, 50)
                            part = (_auxiliary(payload, path, header, encoding, role, evidence=profile in EVIDENCE_PROFILES) if role
                                    else table_import.parse(
                                        io.BytesIO(payload), suffix=suffix,
                                        profile=(profile if profile in EVIDENCE_PROFILES else
                                                 table_import.ARCHIVE_MEMBER_PROFILE if profile == HEADER_PROFILE
                                                 else table_import.IDENTITY_PROFILE),
                                        reference_policy=(table_import.TRANSACTION_REFERENCE_POLICY
                                                          if profile in REFERENCE_PROFILES else None),
                                        _details_pool=details_pool,
                                        _defer_occurrence_binding=profile in EVIDENCE_PROFILES,
                                        _retain_csv_format=profile in CANCELLATION_PROFILES and suffix == ".csv"))
                        try:
                            if profile in CANCELLATION_PROFILES and not role and suffix == ".csv":
                                table_cancellations.apply(part, payload, allow_feedback28=profile in EVIDENCE_PROFILES,
                                                          csv_format=part.get("_csv_format"))
                        finally:
                            part.pop("_csv_format", None)
                        merge(part, path, prefix)
        except (zipfile.BadZipFile, RuntimeError, OSError, ValueError) as error:
            if isinstance(error, table_import.TableImportError):
                raise
            raise table_import.TableImportError("反馈包读取或完整性校验失败，整包未接纳") from None

    try:
        try:
            source.seek(0)
            visit(source)
        finally:
            # Break the recursive function's self-cell: it also owns merge/plan,
            # which must not wait for cyclic GC after a completed or failed parse.
            visit = None
        if not plan["members"]:
            raise table_import.TableImportError("反馈包没有可处理的数据成员")
        if profile in CANCELLATION_PROFILES:
            table_cancellations.refresh_stats(plan)
            if profile in EVIDENCE_PROFILES:
                from . import evidence_resolution
                # Only the complete envelope knows final member positions and
                # source families. Cancellation and merge need no early binding.
                try:
                    evidence_resolution.bind_occurrences(plan)
                except (RuntimeError, OSError, ValueError) as error:
                    if isinstance(error, table_import.TableImportError):
                        raise
                    # Deferred member validation keeps the same public failure
                    # contract it had inside visit(), and the owner closes below.
                    raise table_import.TableImportError("反馈包读取或完整性校验失败，整包未接纳") from None
            return plan
        counts = {key: 0 for key in ("accepted", "unresolved", "non_monetary", "non_transaction", "auxiliary")}
        reasons, totals, examples = {}, {}, []
        for record in plan["records"]:
            counts[record["disposition"]] += 1
            reasons[record["reason_code"]] = reasons.get(record["reason_code"], 0) + 1
            if record["disposition"] == "unresolved" and len(examples) < 3:
                examples.append({"row": "来源记录", "source_row": record["record_no"], "reason": record["details"]["reason"]})
        with localcontext() as context:
            context.prec = 40
            for row in plan["rows"]:
                totals[row["currency"]] = totals.get(row["currency"], Decimal(0)) + row["amount"]
        plan["stats"] = {"total": len(plan["records"]), "ok": len(plan["rows"]),
                         "skipped": len(plan["records"]) - len(plan["rows"]), "bad_examples": examples,
                         **counts, "reason_counts": reasons,
                         "currency_totals": {currency: format(amount, ".2f") for currency, amount in sorted(totals.items())}}
        return plan
    except BaseException:
        if details_pool is not None:
            details_pool.close()
        raise
