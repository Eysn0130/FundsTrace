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

from . import feedback_import, table_import, table_cancellations, import_attachments, rar_archive


IDENTITY_PROFILE = "funds-trace-table-archive-v1"
REFERENCE_PROFILE = "funds-trace-table-archive-v2"
PREVIOUS_PROFILE = REFERENCE_PROFILE
CANCELLATION_PROFILE = "funds-trace-table-archive-v3"
HEADER_PROFILE = "funds-trace-table-archive-v4"
LEGACY_EVIDENCE_PROFILE = "funds-trace-table-archive-v5"
EVIDENCE_PROFILE = "funds-trace-table-archive-v6"
XLS_EVIDENCE_PROFILE = "funds-trace-table-archive-v7"
MICROSECOND_PROFILE = table_import.MICROSECOND_ARCHIVE_PROFILE
MICROSECOND_XLS_PROFILE = table_import.MICROSECOND_XLS_ARCHIVE_PROFILE
HEADER_WIDTH_PROFILE = table_import.HEADER_WIDTH_ARCHIVE_PROFILE
HEADER_WIDTH_XLS_PROFILE = table_import.HEADER_WIDTH_XLS_ARCHIVE_PROFILE
GUARDED_PROFILE = table_import.GUARDED_ARCHIVE_PROFILE
GUARDED_EVIDENCE_PROFILE = table_import.GUARDED_EVIDENCE_ARCHIVE_PROFILE
GUARDED_XLS_PROFILE = table_import.GUARDED_XLS_ARCHIVE_PROFILE
PRECISE_PROFILE = table_import.PRECISE_ARCHIVE_PROFILE
PRECISE_EVIDENCE_PROFILE = table_import.PRECISE_EVIDENCE_ARCHIVE_PROFILE
PRECISE_XLS_PROFILE = table_import.PRECISE_XLS_ARCHIVE_PROFILE
REFERENCE_ROLE_PROFILE = table_import.REFERENCE_ROLE_ARCHIVE_PROFILE
REFERENCE_ROLE_EVIDENCE_PROFILE = table_import.REFERENCE_ROLE_EVIDENCE_ARCHIVE_PROFILE
REFERENCE_ROLE_XLS_PROFILE = table_import.REFERENCE_ROLE_XLS_ARCHIVE_PROFILE
OPTIONAL_REFERENCE_PROFILE = table_import.OPTIONAL_REFERENCE_ARCHIVE_PROFILE
OPTIONAL_REFERENCE_EVIDENCE_PROFILE = table_import.OPTIONAL_REFERENCE_EVIDENCE_ARCHIVE_PROFILE
OPTIONAL_REFERENCE_XLS_PROFILE = table_import.OPTIONAL_REFERENCE_XLS_ARCHIVE_PROFILE
TIME_ROLE_PROFILE = table_import.TIME_ROLE_ARCHIVE_PROFILE
TIME_ROLE_EVIDENCE_PROFILE = table_import.TIME_ROLE_EVIDENCE_ARCHIVE_PROFILE
TIME_ROLE_XLS_PROFILE = table_import.TIME_ROLE_XLS_ARCHIVE_PROFILE
ZONED_PROFILE = table_import.ZONED_ARCHIVE_PROFILE
ZONED_EVIDENCE_PROFILE = table_import.ZONED_EVIDENCE_ARCHIVE_PROFILE
ZONED_XLS_PROFILE = table_import.ZONED_XLS_ARCHIVE_PROFILE
SOURCE_CONTRACT_PROFILE = table_import.SOURCE_CONTRACT_ARCHIVE_PROFILE
SOURCE_CONTRACT_EVIDENCE_PROFILE = table_import.SOURCE_CONTRACT_EVIDENCE_ARCHIVE_PROFILE
SOURCE_CONTRACT_XLS_PROFILE = table_import.SOURCE_CONTRACT_XLS_ARCHIVE_PROFILE
SOURCE_TEXT_PROFILE = table_import.SOURCE_TEXT_ARCHIVE_PROFILE
SOURCE_TEXT_EVIDENCE_PROFILE = table_import.SOURCE_TEXT_EVIDENCE_ARCHIVE_PROFILE
SOURCE_TEXT_XLS_PROFILE = table_import.SOURCE_TEXT_XLS_ARCHIVE_PROFILE
BANK31_VARIANT_PROFILE = table_import.BANK31_VARIANT_ARCHIVE_PROFILE
BANK31_VARIANT_EVIDENCE_PROFILE = table_import.BANK31_VARIANT_EVIDENCE_ARCHIVE_PROFILE
BANK31_VARIANT_XLS_PROFILE = table_import.BANK31_VARIANT_XLS_ARCHIVE_PROFILE
POSTING_PAIR_PROFILE = table_import.POSTING_PAIR_ARCHIVE_PROFILE
POSTING_PAIR_EVIDENCE_PROFILE = table_import.POSTING_PAIR_EVIDENCE_ARCHIVE_PROFILE
POSTING_PAIR_XLS_PROFILE = table_import.POSTING_PAIR_XLS_ARCHIVE_PROFILE
SOURCE_LEDGER_PROFILE = table_import.SOURCE_LEDGER_ARCHIVE_PROFILE
SOURCE_LEDGER_EVIDENCE_PROFILE = table_import.SOURCE_LEDGER_EVIDENCE_ARCHIVE_PROFILE
SOURCE_LEDGER_XLS_PROFILE = table_import.SOURCE_LEDGER_XLS_ARCHIVE_PROFILE
XLS_EVIDENCE_PROFILES = table_import.XLS_EVIDENCE_ARCHIVE_PROFILES
AUXILIARY_SCOPE_PROFILE = table_import.AUXILIARY_SCOPE_ARCHIVE_PROFILE
AUXILIARY_SCOPE_EVIDENCE_PROFILE = table_import.AUXILIARY_SCOPE_EVIDENCE_ARCHIVE_PROFILE
AUXILIARY_SCOPE_XLS_PROFILE = table_import.AUXILIARY_SCOPE_XLS_ARCHIVE_PROFILE
NATIVE_OBSERVATION_PROFILE = table_import.NATIVE_OBSERVATION_ARCHIVE_PROFILE
NATIVE_OBSERVATION_EVIDENCE_PROFILE = table_import.NATIVE_OBSERVATION_EVIDENCE_ARCHIVE_PROFILE
NATIVE_OBSERVATION_XLS_PROFILE = table_import.NATIVE_OBSERVATION_XLS_ARCHIVE_PROFILE
UNORIENTED_PROFILE = table_import.UNORIENTED_ARCHIVE_PROFILE
UNORIENTED_EVIDENCE_PROFILE = table_import.UNORIENTED_EVIDENCE_ARCHIVE_PROFILE
UNORIENTED_XLS_PROFILE = table_import.UNORIENTED_XLS_ARCHIVE_PROFILE

NATIVE31_PROFILE = table_import.NATIVE31_ARCHIVE_PROFILE
NATIVE31_EVIDENCE_PROFILE = table_import.NATIVE31_EVIDENCE_ARCHIVE_PROFILE
NATIVE31_XLS_PROFILE = table_import.NATIVE31_XLS_ARCHIVE_PROFILE

SOURCE_OBSERVATION_PROFILE = table_import.SOURCE_OBSERVATION_ARCHIVE_PROFILE
SOURCE_OBSERVATION_EVIDENCE_PROFILE = table_import.SOURCE_OBSERVATION_EVIDENCE_ARCHIVE_PROFILE
SOURCE_OBSERVATION_XLS_PROFILE = table_import.SOURCE_OBSERVATION_XLS_ARCHIVE_PROFILE
EVIDENCE_PROFILES = frozenset((SOURCE_OBSERVATION_EVIDENCE_PROFILE, SOURCE_OBSERVATION_XLS_PROFILE, NATIVE31_EVIDENCE_PROFILE, NATIVE31_XLS_PROFILE, UNORIENTED_EVIDENCE_PROFILE, UNORIENTED_XLS_PROFILE, NATIVE_OBSERVATION_EVIDENCE_PROFILE, NATIVE_OBSERVATION_XLS_PROFILE, AUXILIARY_SCOPE_EVIDENCE_PROFILE, AUXILIARY_SCOPE_XLS_PROFILE, POSTING_PAIR_EVIDENCE_PROFILE, POSTING_PAIR_XLS_PROFILE, SOURCE_LEDGER_EVIDENCE_PROFILE, SOURCE_LEDGER_XLS_PROFILE, BANK31_VARIANT_EVIDENCE_PROFILE, BANK31_VARIANT_XLS_PROFILE, SOURCE_TEXT_EVIDENCE_PROFILE, SOURCE_TEXT_XLS_PROFILE, LEGACY_EVIDENCE_PROFILE, EVIDENCE_PROFILE, XLS_EVIDENCE_PROFILE,
                               MICROSECOND_PROFILE, MICROSECOND_XLS_PROFILE,
                               HEADER_WIDTH_PROFILE, HEADER_WIDTH_XLS_PROFILE,
                               GUARDED_EVIDENCE_PROFILE, GUARDED_XLS_PROFILE, PRECISE_EVIDENCE_PROFILE, PRECISE_XLS_PROFILE,
                               REFERENCE_ROLE_EVIDENCE_PROFILE, REFERENCE_ROLE_XLS_PROFILE, OPTIONAL_REFERENCE_EVIDENCE_PROFILE, OPTIONAL_REFERENCE_XLS_PROFILE, TIME_ROLE_EVIDENCE_PROFILE, TIME_ROLE_XLS_PROFILE, ZONED_EVIDENCE_PROFILE, ZONED_XLS_PROFILE, SOURCE_CONTRACT_EVIDENCE_PROFILE, SOURCE_CONTRACT_XLS_PROFILE))
PROFILE = SOURCE_OBSERVATION_PROFILE
CANCELLATION_PROFILES = frozenset((SOURCE_OBSERVATION_PROFILE, NATIVE31_PROFILE, UNORIENTED_PROFILE, NATIVE_OBSERVATION_PROFILE, AUXILIARY_SCOPE_PROFILE, POSTING_PAIR_PROFILE, SOURCE_LEDGER_PROFILE, BANK31_VARIANT_PROFILE, SOURCE_TEXT_PROFILE, CANCELLATION_PROFILE, HEADER_PROFILE, GUARDED_PROFILE, PRECISE_PROFILE, REFERENCE_ROLE_PROFILE, OPTIONAL_REFERENCE_PROFILE, TIME_ROLE_PROFILE, ZONED_PROFILE, SOURCE_CONTRACT_PROFILE, *EVIDENCE_PROFILES))
SUPPORTED_PROFILES = frozenset((POSTING_PAIR_PROFILE, IDENTITY_PROFILE, REFERENCE_PROFILE, *CANCELLATION_PROFILES))
# Explicit precedence: lexical ordering puts v10/v11 below v9.
PROFILE_PRECEDENCE = (SOURCE_OBSERVATION_XLS_PROFILE, SOURCE_OBSERVATION_EVIDENCE_PROFILE, SOURCE_OBSERVATION_PROFILE, NATIVE31_XLS_PROFILE, NATIVE31_EVIDENCE_PROFILE, NATIVE31_PROFILE, UNORIENTED_XLS_PROFILE, UNORIENTED_EVIDENCE_PROFILE, UNORIENTED_PROFILE, NATIVE_OBSERVATION_XLS_PROFILE, NATIVE_OBSERVATION_EVIDENCE_PROFILE, NATIVE_OBSERVATION_PROFILE, AUXILIARY_SCOPE_XLS_PROFILE, AUXILIARY_SCOPE_EVIDENCE_PROFILE, AUXILIARY_SCOPE_PROFILE, POSTING_PAIR_XLS_PROFILE, POSTING_PAIR_EVIDENCE_PROFILE, POSTING_PAIR_PROFILE, SOURCE_LEDGER_XLS_PROFILE, SOURCE_LEDGER_EVIDENCE_PROFILE, SOURCE_LEDGER_PROFILE, BANK31_VARIANT_XLS_PROFILE, BANK31_VARIANT_EVIDENCE_PROFILE, BANK31_VARIANT_PROFILE, SOURCE_TEXT_XLS_PROFILE, SOURCE_TEXT_EVIDENCE_PROFILE, SOURCE_TEXT_PROFILE, SOURCE_CONTRACT_XLS_PROFILE, SOURCE_CONTRACT_EVIDENCE_PROFILE, SOURCE_CONTRACT_PROFILE, ZONED_XLS_PROFILE, ZONED_EVIDENCE_PROFILE, ZONED_PROFILE, TIME_ROLE_XLS_PROFILE, TIME_ROLE_EVIDENCE_PROFILE, TIME_ROLE_PROFILE, OPTIONAL_REFERENCE_XLS_PROFILE, OPTIONAL_REFERENCE_EVIDENCE_PROFILE, OPTIONAL_REFERENCE_PROFILE,
                      REFERENCE_ROLE_XLS_PROFILE, REFERENCE_ROLE_EVIDENCE_PROFILE, REFERENCE_ROLE_PROFILE,
                      PRECISE_XLS_PROFILE, PRECISE_EVIDENCE_PROFILE, PRECISE_PROFILE,
                      GUARDED_XLS_PROFILE, GUARDED_EVIDENCE_PROFILE, GUARDED_PROFILE,
                      HEADER_WIDTH_XLS_PROFILE, HEADER_WIDTH_PROFILE,
                      MICROSECOND_XLS_PROFILE, MICROSECOND_PROFILE, XLS_EVIDENCE_PROFILE,
                      EVIDENCE_PROFILE, LEGACY_EVIDENCE_PROFILE, HEADER_PROFILE,
                      CANCELLATION_PROFILE, REFERENCE_PROFILE, IDENTITY_PROFILE)
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
_SOURCE_AUXILIARY = {frozenset(names): role for role, names in [('account', ['账户开户名称', '开户人证件号码', '交易卡号', '交易账号', '账号开户时间', '账户余额', '可用余额', '币种', '开户网点代码', '开户网点', '账户状态', '钞汇标志名称', '开户人证件类型', '销户日期', '账户类型', '开户联系方式', '通信地址', '联系电话', '代理人', '代理人电话', '备注', '开户省份', '开户城市', '账号开户银行', '客户代码', '法人代表', '客户工商执照号码', '法人代表证件号码', '住宅地址', '邮政编码', '代办人证件号码', '邮箱地址', '关联资金账户', '地税纳税号', '单位电话', '代办人证件类型', '住宅电话', '法人代表证件类型', '国税纳税号', '单位地址', '工作单位', '销户网点', '最后交易时间', '账户销户银行', '任务流水号']), ('freeze', ['银行名称', '账号', '冻结措施类型', '冻结措施类型名称', '冻结金额', '冻结机关', '冻结开始日期', '冻结截止日期', '措施序号', '备注', '任务流水号', '查控主体类别']), ('client', ['客户名称', '证照类型', '证照号码', '单位地址', '联系电话', '联系手机', '单位电话', '住宅电话', '工作单位', '邮箱地址', '代办人姓名', '代办人证件类型', '代办人证件号码', '国税纳税号', '地税纳税号', '法人代表', '法人代表证件类型', '法人代表证件号码', '出生日期', '户籍地址', '客户工商执照号码']), ('subaccount', ['银行名称', '开户账号', '子账户账号', '余额', '可用余额', '子账户类别', '子账户序号', '币种', '钞汇标识', '账户状态', '账户人姓名', '账户序号', '备注']), ('client', ['开户名称', '证照类型', '证照号码', '联系电话']), ('client', ['开户名称', '证照类型', '证照号码', '住宅地址', '住宅电话']), ('manifest', ['任务流水号', '银行名称', '主体类别', '证账号码', '账卡号', '发送时间', '反馈时间', '反馈结果', '反馈非明细结果', '反馈明细结果', '入库时间', '入库状态', '请求单号', '查询结果'])]}



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


def should_parse(source, *, native_observations=False):
    """Only the new envelope family opts in; historical flat profiles stay put."""
    try:
        source.seek(0)
        with rar_archive.open_archive(source) as archive:
            infos = archive.infolist()
            if any(info.filename.lower().endswith((".xlsx", ".xls", ".txt", ".tsv")) for info in infos):
                return True
            legacy, ordinary = False, False
            for info in infos:
                if info.filename.lower().endswith(".csv") and not info.flag_bits & 1:
                    with archive.open(info) as stream:
                        header, _ = _header(stream.read(65536))
                    legacy = legacy or frozenset(header) == frozenset(feedback_import.COLUMNS)
                    ordinary = ordinary or bool(header and frozenset(header) not in {frozenset(value) for value in feedback_import.SCHEMAS.values()})
            if native_observations and legacy:
                return True
            if ordinary and not legacy:
                return True
            if any(not info.is_dir() and info.filename.lower().endswith((".zip", ".rar")) for info in infos):
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


def evidence_profile(source):
    """Opt actual XLS members into the XLS-capable profile without reading business table payloads.

    Only nested ZIP payloads are opened to inspect their member metadata. The
    normal parser still validates every leaf and owns the public error. A
    container by itself must not upgrade historical CSV interpretation.
    """
    budget = {"members": 0, "bytes": 0}

    def contains_xls(stream, depth=0):
        with rar_archive.open_archive(stream) as archive:
            names = set()
            for info in archive.infolist():
                budget["members"] += 1
                if budget["members"] > EVIDENCE_MAX_MEMBERS:
                    raise ValueError("profile_member_limit")
                name = _path(info.orig_filename)
                if name in names or info.flag_bits & 1 or stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
                    raise ValueError("invalid_profile_member")
                names.add(name)
                if info.is_dir():
                    continue
                if not 0 <= info.file_size <= MAX_MEMBER_BYTES:
                    raise ValueError("profile_member_size_limit")
                budget["bytes"] += info.file_size
                if budget["bytes"] > EVIDENCE_MAX_EXPANDED_BYTES:
                    raise ValueError("profile_expanded_size_limit")
                suffix = PurePosixPath(name).suffix.lower()
                if suffix == ".xls":
                    return True
                if suffix in {".zip", ".rar"}:
                    if depth >= MAX_DEPTH:
                        raise ValueError("profile_depth_limit")
                    with archive.open(info) as member:
                        payload = member.read(MAX_MEMBER_BYTES + 1)
                    if len(payload) != info.file_size:
                        raise ValueError("profile_member_size_mismatch")
                    if info.filename.lower().endswith(".rar") and not rar_archive.is_rar(io.BytesIO(payload)):
                        continue  # Opaque historical attachments retain their own disposition.
                    if contains_xls(io.BytesIO(payload), depth + 1):
                        return True
            return False

    try:
        source.seek(0)
        if contains_xls(source):
            return SOURCE_OBSERVATION_XLS_PROFILE
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
        pass  # The normal parser owns archive validation and its public error.
    finally:
        source.seek(0)
    return SOURCE_OBSERVATION_EVIDENCE_PROFILE


def _path(name):
    if (not isinstance(name, str) or not name or len(name) > 4096 or "\\" in name
            or ":" in name or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or name.startswith("/") or any(part in ("", ".", "..") for part in name.rstrip("/").split("/"))):
        raise table_import.TableImportError("反馈包包含不安全或不明确的成员路径")
    return name.rstrip("/")


def _auxiliary(payload, path, header, encoding, role, *, evidence=False, native_observations=False, delimiter=","):
    try:
        text = payload.decode(encoding, errors="strict")
        csv.field_size_limit(max(csv.field_size_limit(), table_import.MAX_FIELD))
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
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
                value = evidence_resolution.auxiliary_evidence(header, cells, role, native_observations=native_observations)
                if value:
                    records[-1]["details"][evidence_resolution.DETAIL_FIELD] = value
    except (UnicodeError, csv.Error, StopIteration):
        raise table_import.TableImportError("反馈包辅助CSV结构损坏，整包未接纳") from None
    return {"rows": [], "records": records,
            "members": [{"member_path": path, "role": "auxiliary", "sha256": digest,
                         "size_bytes": len(payload), "encoding": encoding, "row_count": len(records)}],
            "documents": [{"member_path": path, "sheet": "", "header_row": 1, "row_count": len(records), "role": role}],
            "mapping_evidence": [], "columns": [], "mapping": {}, "mode": "B"}


def parse(source, profile=PROFILE, *, _stage=False, _operation_scopes=False):
    if profile not in SUPPORTED_PROFILES:
        raise table_import.TableImportError("不支持的反馈包解析规则")
    plan = {"profile": profile, "mode": "B", "mapping": {}, "columns": [], "sheet": "",
            "rows": [], "records": [], "members": [], "documents": [], "mapping_evidence": []}
    if profile in CANCELLATION_PROFILES:
        plan.update(cancelled_rows=[], cancellations=[])
    if _stage and profile in EVIDENCE_PROFILES:
        from .import_staging import stage_plan
        stage_plan(plan)
    budget = {"members": 0, "bytes": 0}
    member_limit = EVIDENCE_MAX_MEMBERS if profile in EVIDENCE_PROFILES else MAX_MEMBERS
    record_limit = EVIDENCE_MAX_RECORDS if profile in EVIDENCE_PROFILES else MAX_RECORDS
    expanded_limit = EVIDENCE_MAX_EXPANDED_BYTES if profile in EVIDENCE_PROFILES else MAX_EXPANDED_BYTES
    seen = set()
    selected = False
    statistics = table_cancellations.Statistics() if profile in CANCELLATION_PROFILES else None
    from .import_staging import DetailsPool, PackedDetails
    details_pool = DetailsPool() if profile in EVIDENCE_PROFILES else None
    if details_pool is not None:
        plan["_details_pool"] = details_pool

    def working_pool():
        nonlocal details_pool
        if details_pool is not None and details_pool._closed:
            details_pool = DetailsPool()
            plan["_details_pool"] = details_pool
        return details_pool

    def merge(part, prefix, family=""):
        nonlocal selected
        working_pool()
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
        from itertools import chain
        for row in chain(part["rows"], part.get("cancelled_rows", [])):
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
        if statistics is not None:
            statistics.add(part)
        for field in ("rows", "records", "members"):
            plan[field].extend(part[field])
        if profile in CANCELLATION_PROFILES:
            plan["cancelled_rows"].extend(part.get("cancelled_rows", []))
            plan["cancellations"].extend({key: value + offset for key, value in pair.items()}
                                         for pair in part.get("cancellations", []))
        if "_plan_store" in plan:
            # visit() retains its previous `part` while parsing the next RHS.
            # Release the just-staged member before starting that next parser.
            for key in ("rows", "records", "cancelled_rows", "cancellations"):
                part.get(key, []).clear()
            # All note bytes now belong to the complete plan. The member's
            # packing spill need not duplicate the entire archive on disk.
            if details_pool is not None:
                plan["_plan_store"].member_note_encoded_bytes += details_pool._memory_bytes + details_pool._spool_bytes
                details_pool.close()

    def visit(stream, prefix="", depth=0):
        try:
            with rar_archive.open_archive(stream) as archive:
                local = set()
                for info in archive.infolist():
                    budget["members"] += 1
                    if budget["members"] > member_limit:
                        raise table_import.TableImportError("反馈包成员数超出安全处理范围")
                    # ZipInfo.filename may already truncate a NUL or normalize
                    # a platform separator. Validate the retained raw name.
                    name = _path(info.orig_filename)
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
                    if profile not in EVIDENCE_PROFILES and suffix not in (".csv", ".zip", ".rar"):
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
                    if suffix == ".zip" or suffix == ".rar" and rar_archive.is_rar(io.BytesIO(payload)):
                        if depth >= MAX_DEPTH:
                            raise table_import.TableImportError("反馈包嵌套层数超出安全处理范围")
                        visit(io.BytesIO(payload), path, depth + 1)
                    elif suffix not in (".csv", ".tsv", ".txt", ".xlsx") and not (
                            suffix == ".xls" and profile in XLS_EVIDENCE_PROFILES):
                        merge(import_attachments.plan(payload, path), path, prefix)
                    else:
                        # XLS always goes through the typed BIFF reader, even
                        # when its bytes resemble a recognized auxiliary CSV.
                        header, encoding = ([], None) if suffix == ".xls" else _header(payload)
                        delimiter = ","
                        decoded_csv = None
                        if profile in table_import.SOURCE_CONTRACT_PROFILES and suffix in {".csv", ".tsv", ".txt"}:
                            decoded_csv = table_import._DecodedCSV(payload, suffix, profile)
                            header = [cell.strip() for cell in decoded_csv.header]
                            encoding, delimiter = decoded_csv.encoding, decoded_csv.delimiter
                        if (profile not in EVIDENCE_PROFILES and profile not in table_import.NATIVE_OBSERVATION_PROFILES
                                and len(header) == len(feedback_import.COLUMNS)
                                and frozenset(header) == frozenset(feedback_import.COLUMNS)):
                            # Generic mapping cannot downgrade a verified profile's
                            # cancellation/identity semantics inside a new envelope.
                            raise table_import.TableImportError("新反馈包包含需使用原规则的旧版交易表，整包未接纳；未降级解释资金记录")
                        signature = frozenset(header)
                        known_auxiliary = signature in _AUXILIARY or any(
                            name != "transaction" and signature == frozenset(names)
                            for name, names in feedback_import.SCHEMAS.items())
                        native_observations = profile in table_import.NATIVE_OBSERVATION_PROFILES
                        source_auxiliary = (profile in table_import.SOURCE_CONTRACT_PROFILES
                            and signature in _SOURCE_AUXILIARY
                            and not (profile in table_import.AUXILIARY_SCOPE_PROFILES and known_auxiliary)
                            and not (native_observations and _SOURCE_AUXILIARY.get(signature) in {"account", "subaccount"}))
                        role = (_SOURCE_AUXILIARY.get(frozenset(header)) if source_auxiliary else _AUXILIARY.get(frozenset(header))) if len(header) == len(set(header)) else None
                        if (profile in EVIDENCE_PROFILES or native_observations) and role is None:
                            if native_observations:
                                from . import evidence_resolution
                                role = evidence_resolution.classify_auxiliary(header, native_observations=True)
                            signature = frozenset(header)
                            role = role or next((name for name, names in feedback_import.SCHEMAS.items() if name != "transaction" and signature == frozenset(names)), None)
                            role = {"task": "manifest", "address": "client", "phone": "client"}.get(role, role)
                        member_profile = (profile if profile in EVIDENCE_PROFILES or profile in {GUARDED_PROFILE, PRECISE_PROFILE, REFERENCE_ROLE_PROFILE, OPTIONAL_REFERENCE_PROFILE, TIME_ROLE_PROFILE, ZONED_PROFILE, SOURCE_CONTRACT_PROFILE, SOURCE_TEXT_PROFILE, BANK31_VARIANT_PROFILE, SOURCE_LEDGER_PROFILE, POSTING_PAIR_PROFILE, AUXILIARY_SCOPE_PROFILE, NATIVE_OBSERVATION_PROFILE, UNORIENTED_PROFILE, NATIVE31_PROFILE, SOURCE_OBSERVATION_PROFILE} else
                                          table_import.ARCHIVE_MEMBER_PROFILE if profile == HEADER_PROFILE
                                          else table_import.IDENTITY_PROFILE)
                        if role or member_profile != profile:
                            decoded_csv = None
                        with localcontext() as context:
                            if profile in CANCELLATION_PROFILES:
                                context.prec = max(context.prec, 50)
                            working_pool()
                            part = (_auxiliary(payload, path, header, encoding, role, evidence=profile in EVIDENCE_PROFILES and not source_auxiliary,
                                               native_observations=native_observations, delimiter=delimiter) if role
                                    else table_import.parse(
                                        io.BytesIO(payload), suffix=suffix,
                                        profile=member_profile,
                                        reference_policy=(table_import.TRANSACTION_REFERENCE_POLICY
                                                          if profile in REFERENCE_PROFILES else None),
                                        _details_pool=details_pool,
                                        _defer_occurrence_binding=profile in EVIDENCE_PROFILES,
                                        _retain_csv_format=profile in CANCELLATION_PROFILES and suffix == ".csv",
                                        _decoded_csv=decoded_csv))
                        try:
                            if profile in CANCELLATION_PROFILES and not role and suffix == ".csv":
                                table_cancellations.apply(part, payload, allow_feedback28=profile in EVIDENCE_PROFILES or native_observations,
                                                          csv_format=part.get("_csv_format"))
                        finally:
                            part.pop("_csv_format", None)
                        merge(part, path, prefix)
                    payload = None
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
        if profile in table_import.SOURCE_CONTRACT_PROFILES:
            from .import_staging import row_records
            member_hashes = {member["member_path"]: member["sha256"] for member in plan["members"]}
            posting_records = {}
            for row, record in row_records(plan, kinds=("rows",)):
                if row.get("_source_posting_repeat_key"):
                    key = record["member_path"], member_hashes[record["member_path"]], row["_source_posting_repeat_key"]
                    row["_source_posting_record_key"] = posting_records.setdefault(key, record["record_key"])
        if profile in CANCELLATION_PROFILES:
            plan["stats"] = statistics.result()
            if profile in EVIDENCE_PROFILES or profile == SOURCE_OBSERVATION_PROFILE:
                from . import evidence_resolution
                # Only the complete envelope knows final member positions and
                # source families. Cancellation and merge need no early binding.
                try:
                    if profile == SOURCE_OBSERVATION_PROFILE:
                        evidence_resolution.bind_families(plan)
                    elif _operation_scopes:
                        evidence_resolution.bind_occurrences(plan, operation_scopes=True)
                    else:
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
        if plan.get("_plan_store") is not None:
            plan["_plan_store"].close()
        raise
