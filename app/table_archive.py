"""Bounded bank export envelopes; no extraction and no persistent writes."""
from __future__ import annotations

import codecs
import csv
from decimal import Decimal, localcontext
import hashlib
import io
import re
from pathlib import PurePosixPath
import stat
import zipfile

from . import feedback_import, table_import, table_cancellations, import_attachments, pdf_text_observations, rar_archive


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
AUXILIARY_ROWS_PROFILE = table_import.AUXILIARY_ROWS_ARCHIVE_PROFILE
AUXILIARY_ROWS_EVIDENCE_PROFILE = table_import.AUXILIARY_ROWS_EVIDENCE_ARCHIVE_PROFILE
AUXILIARY_ROWS_XLS_PROFILE = table_import.AUXILIARY_ROWS_XLS_ARCHIVE_PROFILE
BANK_SOURCE_PROFILE = table_import.BANK_SOURCE_ARCHIVE_PROFILE
BANK_SOURCE_EVIDENCE_PROFILE = table_import.BANK_SOURCE_EVIDENCE_ARCHIVE_PROFILE
BANK_SOURCE_XLS_PROFILE = table_import.BANK_SOURCE_XLS_ARCHIVE_PROFILE
TRIAL_MEMBER_PROFILE = table_import.TRIAL_MEMBER_ARCHIVE_PROFILE
PREVIOUS_BANK_SOURCE_EVIDENCE_PROFILE = table_import.PREVIOUS_BANK_SOURCE_EVIDENCE_ARCHIVE_PROFILE
PREVIOUS_BANK_SOURCE_XLS_PROFILE = table_import.PREVIOUS_BANK_SOURCE_XLS_ARCHIVE_PROFILE
BANK_SOURCE_CSV_PROFILES = table_import.BANK_SOURCE_CSV_ARCHIVE_PROFILES
EVIDENCE_PROFILES = frozenset((TRIAL_MEMBER_PROFILE, BANK_SOURCE_EVIDENCE_PROFILE, BANK_SOURCE_XLS_PROFILE, PREVIOUS_BANK_SOURCE_EVIDENCE_PROFILE, PREVIOUS_BANK_SOURCE_XLS_PROFILE, AUXILIARY_ROWS_EVIDENCE_PROFILE, AUXILIARY_ROWS_XLS_PROFILE, SOURCE_OBSERVATION_EVIDENCE_PROFILE, SOURCE_OBSERVATION_XLS_PROFILE, NATIVE31_EVIDENCE_PROFILE, NATIVE31_XLS_PROFILE, UNORIENTED_EVIDENCE_PROFILE, UNORIENTED_XLS_PROFILE, NATIVE_OBSERVATION_EVIDENCE_PROFILE, NATIVE_OBSERVATION_XLS_PROFILE, AUXILIARY_SCOPE_EVIDENCE_PROFILE, AUXILIARY_SCOPE_XLS_PROFILE, POSTING_PAIR_EVIDENCE_PROFILE, POSTING_PAIR_XLS_PROFILE, SOURCE_LEDGER_EVIDENCE_PROFILE, SOURCE_LEDGER_XLS_PROFILE, BANK31_VARIANT_EVIDENCE_PROFILE, BANK31_VARIANT_XLS_PROFILE, SOURCE_TEXT_EVIDENCE_PROFILE, SOURCE_TEXT_XLS_PROFILE, LEGACY_EVIDENCE_PROFILE, EVIDENCE_PROFILE, XLS_EVIDENCE_PROFILE,
                               MICROSECOND_PROFILE, MICROSECOND_XLS_PROFILE,
                               HEADER_WIDTH_PROFILE, HEADER_WIDTH_XLS_PROFILE,
                               GUARDED_EVIDENCE_PROFILE, GUARDED_XLS_PROFILE, PRECISE_EVIDENCE_PROFILE, PRECISE_XLS_PROFILE,
                               REFERENCE_ROLE_EVIDENCE_PROFILE, REFERENCE_ROLE_XLS_PROFILE, OPTIONAL_REFERENCE_EVIDENCE_PROFILE, OPTIONAL_REFERENCE_XLS_PROFILE, TIME_ROLE_EVIDENCE_PROFILE, TIME_ROLE_XLS_PROFILE, ZONED_EVIDENCE_PROFILE, ZONED_XLS_PROFILE, SOURCE_CONTRACT_EVIDENCE_PROFILE, SOURCE_CONTRACT_XLS_PROFILE))
PROFILE = BANK_SOURCE_PROFILE
CANCELLATION_PROFILES = frozenset((*BANK_SOURCE_CSV_PROFILES, AUXILIARY_ROWS_PROFILE, SOURCE_OBSERVATION_PROFILE, NATIVE31_PROFILE, UNORIENTED_PROFILE, NATIVE_OBSERVATION_PROFILE, AUXILIARY_SCOPE_PROFILE, POSTING_PAIR_PROFILE, SOURCE_LEDGER_PROFILE, BANK31_VARIANT_PROFILE, SOURCE_TEXT_PROFILE, CANCELLATION_PROFILE, HEADER_PROFILE, GUARDED_PROFILE, PRECISE_PROFILE, REFERENCE_ROLE_PROFILE, OPTIONAL_REFERENCE_PROFILE, TIME_ROLE_PROFILE, ZONED_PROFILE, SOURCE_CONTRACT_PROFILE, *EVIDENCE_PROFILES))
SUPPORTED_PROFILES = frozenset((POSTING_PAIR_PROFILE, IDENTITY_PROFILE, REFERENCE_PROFILE, *CANCELLATION_PROFILES))
# Explicit precedence: lexical ordering puts v10/v11 below v9.
PROFILE_PRECEDENCE = (TRIAL_MEMBER_PROFILE, BANK_SOURCE_XLS_PROFILE, BANK_SOURCE_EVIDENCE_PROFILE, BANK_SOURCE_PROFILE,
                      PREVIOUS_BANK_SOURCE_XLS_PROFILE, PREVIOUS_BANK_SOURCE_EVIDENCE_PROFILE,
                      table_import.PREVIOUS_BANK_SOURCE_ARCHIVE_PROFILE, AUXILIARY_ROWS_XLS_PROFILE, AUXILIARY_ROWS_EVIDENCE_PROFILE, AUXILIARY_ROWS_PROFILE, SOURCE_OBSERVATION_XLS_PROFILE, SOURCE_OBSERVATION_EVIDENCE_PROFILE, SOURCE_OBSERVATION_PROFILE, NATIVE31_XLS_PROFILE, NATIVE31_EVIDENCE_PROFILE, NATIVE31_PROFILE, UNORIENTED_XLS_PROFILE, UNORIENTED_EVIDENCE_PROFILE, UNORIENTED_PROFILE, NATIVE_OBSERVATION_XLS_PROFILE, NATIVE_OBSERVATION_EVIDENCE_PROFILE, NATIVE_OBSERVATION_PROFILE, AUXILIARY_SCOPE_XLS_PROFILE, AUXILIARY_SCOPE_EVIDENCE_PROFILE, AUXILIARY_SCOPE_PROFILE, POSTING_PAIR_XLS_PROFILE, POSTING_PAIR_EVIDENCE_PROFILE, POSTING_PAIR_PROFILE, SOURCE_LEDGER_XLS_PROFILE, SOURCE_LEDGER_EVIDENCE_PROFILE, SOURCE_LEDGER_PROFILE, BANK31_VARIANT_XLS_PROFILE, BANK31_VARIANT_EVIDENCE_PROFILE, BANK31_VARIANT_PROFILE, SOURCE_TEXT_XLS_PROFILE, SOURCE_TEXT_EVIDENCE_PROFILE, SOURCE_TEXT_PROFILE, SOURCE_CONTRACT_XLS_PROFILE, SOURCE_CONTRACT_EVIDENCE_PROFILE, SOURCE_CONTRACT_PROFILE, ZONED_XLS_PROFILE, ZONED_EVIDENCE_PROFILE, ZONED_PROFILE, TIME_ROLE_XLS_PROFILE, TIME_ROLE_EVIDENCE_PROFILE, TIME_ROLE_PROFILE, OPTIONAL_REFERENCE_XLS_PROFILE, OPTIONAL_REFERENCE_EVIDENCE_PROFILE, OPTIONAL_REFERENCE_PROFILE,
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
MEMBER_RETENTION_POLICY = "archive-member-retention-v1"
UNREADABLE_REASON = "intake_member_unreadable"
RETENTION_DETAIL = "member_retention_policy"
INTAKE_ROLE_DETAIL = "intake_material_role"
INTAKE_DECLARED_ROLES = {"account": "account", "registry": "client", "address": "client",
                         "phone": "client", "description": "description", "settlement_bridge": "description",
                         "settlement_scope": "description", "event_reference_scope": "description",
                         "ledger_registration": "client"}
_DECLARED_SCHEMAS = {
    ("名称", "证件类型", "证件号码", "开户账号", "开户机构", "适用期间"): ("account", 6),
    ("登记主体", "证件类型", "证件号码", "登记编号"): ("registry", 4),
    ("地址用途", "地址文本", "关联主体编号", "有效起日", "有效止日"): ("address", 4),
    table_import.table_source_roles.DATED_ACCOUNT_REGISTRY: ("ledger_registration", 7),
}
_UNREADABLE_MEMBER_ERRORS = {
    "CSV编码无法安全识别",
    "CSV引号或记录结构损坏，文件未接纳",
    "反馈包辅助CSV结构损坏，整包未接纳",
    "CSV分隔符存在歧义，无法安全识别",
    "CSV已识别表头与数据列数不一致，文件未接纳",
    "Excel工作簿损坏、加密或格式不受支持",
}


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


def _auxiliary_role(header, profile):
    """Keep strict CSV eligibility and the final archive role on one grammar."""
    signature = frozenset(header)
    known_auxiliary = signature in _AUXILIARY or any(
        name != "transaction" and signature == frozenset(names)
        for name, names in feedback_import.SCHEMAS.items())
    native_observations = profile in table_import.NATIVE_OBSERVATION_PROFILES
    source_auxiliary = (profile in table_import.SOURCE_CONTRACT_PROFILES
        and signature in _SOURCE_AUXILIARY
        and not (profile in table_import.AUXILIARY_SCOPE_PROFILES and known_auxiliary)
        and not (native_observations and _SOURCE_AUXILIARY.get(signature) in {"account", "subaccount"}))
    role = (_SOURCE_AUXILIARY.get(signature) if source_auxiliary else _AUXILIARY.get(signature)) if len(header) == len(set(header)) else None
    if (profile in EVIDENCE_PROFILES or native_observations) and role is None:
        if native_observations:
            from . import evidence_resolution
            role = evidence_resolution.classify_auxiliary(header, native_observations=True)
        role = role or next((name for name, names in feedback_import.SCHEMAS.items() if name != "transaction" and signature == frozenset(names)), None)
        role = {"task": "manifest", "address": "client", "phone": "client"}.get(role, role)
    return role, source_auxiliary


def should_parse(source, *, native_observations=False, retain_opaque=False):
    """Only the new envelope family opts in; historical flat profiles stay put."""
    try:
        source.seek(0)
        with rar_archive.open_archive(source) as archive:
            infos = archive.infolist()
            leaves = [info for info in infos if not info.is_dir()]
            if retain_opaque and leaves and (
                    any(info.filename.lower().endswith(".pdf") for info in leaves)
                    or not any(info.filename.lower().endswith(".csv") for info in leaves)):
                return True
            if any(info.filename.lower().endswith((".xlsx", ".xls", ".txt", ".tsv")) for info in infos):
                return True
            legacy, ordinary = False, False
            for info in infos:
                if info.filename.lower().endswith(".csv") and not info.flag_bits & 1:
                    with archive.open(info) as stream:
                        sample = stream.read(65536)
                    if retain_opaque:
                        from .services import trial_delayed_pair
                        if trial_delayed_pair.declaration_header(sample):
                            return True
                    header, _ = _header(sample)
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
            return BANK_SOURCE_XLS_PROFILE
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
        pass  # The normal parser owns archive validation and its public error.
    finally:
        source.seek(0)
    return BANK_SOURCE_EVIDENCE_PROFILE


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


def _intake_aux_role(header, role):
    for section in ("address", "phone"):
        if frozenset(header) == frozenset(feedback_import.SCHEMAS[section]):
            return section
    return role


def _declare_intake_role(part, role):
    document = part["documents"][0]
    document.update(intake_role=role, source_rows=document["row_count"] + 1)
    for record in part["records"]:
        record["details"][INTAKE_ROLE_DETAIL] = role


def _settlement_intake(payload, path, declaration):
    from .services import trial_delayed_pair

    encoding, header = declaration
    try:
        trial_delayed_pair.read_declarations(payload)
        part = _auxiliary(payload, path, header, "utf-8-sig", "description")
    except (ValueError, csv.Error):
        part = import_attachments.plan(payload, path)
        part["records"][0].update(disposition="unresolved", reason_code=trial_delayed_pair.COVERAGE_REASON,
            details={"source_role": "description", "source_row": "1", "sheet": "", "financial_effect": "undetermined",
                     "reason": "清算对应表已保留。内容受损、作用域不完整或超出核证范围，请打开原件核对。"})
        part["members"][0].update(role="auxiliary", encoding=encoding)
        part["documents"][0].update(role="description", sheet="", header_row=1)
    else:
        for record in part["records"]:
            if record["disposition"] == "auxiliary":
                record["details"]["reason"] = "独立清算对应声明，供双侧原始过账核对"
    _declare_intake_role(part, trial_delayed_pair.ROLE)
    part["documents"][0].update(columns=table_import._columns(header), header_rows=[1],
                                source_rows=max((row["source_row"] for row in part["records"]), default=1))
    return part


def _reference_scope_intake(payload, path, statement):
    role, _, verified = statement
    plan = import_attachments.plan(payload, path)
    plan["records"][0].update(disposition="auxiliary", reason_code="auxiliary_table", details={
        "source_role": "description", "source_row": "1", "sheet": "", "financial_effect": "not_a_flow",
        "reason": ("跨账簿来源范围及时间口径声明" if verified
                   else "已保留范围说明，尚缺可核证的完整作用范围、流水绑定或时间口径。"),
    })
    plan["members"][0].update(role="auxiliary", encoding="utf-8-sig")
    plan["documents"][0].update(role="description", sheet="", header_row=None)
    _declare_intake_role(plan, role)
    return plan


def _declared_intake_table(payload, path, header, encoding, delimiter):
    declared = _DECLARED_SCHEMAS.get(tuple(header))
    if declared is None or not encoding:
        return None
    role, required = declared
    try:
        reader = csv.reader(io.StringIO(payload.decode(encoding, errors="strict"), newline=""),
                            delimiter=delimiter, strict=True)
        next(reader)
        for row in reader:
            if len(row) != len(header) or not all(value.strip() for value in row[:required]):
                return None
            if role == "ledger_registration":
                table_import.table_source_roles.dated_account_record(row)
    except (ValueError, csv.Error, StopIteration):
        return None
    part = _auxiliary(payload, path, header, encoding, INTAKE_DECLARED_ROLES[role], delimiter=delimiter)
    _declare_intake_role(part, role)
    return part


def _original_description(payload, path, lines):
    part = import_attachments.plan(payload, path)
    record = part["records"][0]
    record.update(disposition="auxiliary", reason_code="auxiliary_table")
    record["details"] = {"source_role": "description", "source_row": "1", "sheet": "",
                         "financial_effect": "not_a_flow", "reason": "原文声明作用域的说明资料",
                         INTAKE_ROLE_DETAIL: "description"}
    part["members"][0].update(role="auxiliary", encoding="utf-8-sig")
    part["documents"][0].update(role="description", sheet="", source_rows=len(lines), intake_role="description")
    return part


def _trial_narrative_note(payload, path):
    from .services import trial_pair_decision, trial_trace_decision

    if not (trial_trace_decision.intake_description(payload) or trial_pair_decision.intake_description(payload)):
        return None
    return _original_description(payload, path, payload.decode("utf-8-sig").splitlines())


def _scoped_note(payload, path):
    if len(payload) > 4096 or PurePosixPath(path).suffix.lower() != ".txt":
        return None
    try:
        lines = payload.decode("utf-8-sig", errors="strict").splitlines()
    except UnicodeError:
        return None
    if not 3 <= len(lines) <= 24 or lines[0] not in ("SYNTHETIC NOTES", "FIELD NOTES", "MATERIAL NOTES"):
        return None
    match = re.fullmatch(r"Scope: ([A-Za-z0-9][A-Za-z0-9_./-]{0,250}) only\.", lines[1])
    if not match:
        return None
    try:
        _path(match[1])
    except table_import.TableImportError:
        return None
    return _original_description(payload, path, lines)


def direct_intake_auxiliary(source, suffix, *, narrative_decisions=False):
    """Recognize the same closed auxiliary declarations outside a directory."""
    if suffix not in {".csv", ".tsv", ".txt"}:
        return None
    source.seek(0)
    sample = source.read(65536)
    source.seek(0)
    from .services import trial_delayed_pair

    profile = table_import.VOUCHER_REFERENCE_EVIDENCE_PROFILE
    scope = trial_delayed_pair.scope_statement(sample) if suffix == ".txt" else None
    if scope is not None:
        payload = table_import._bytes(source)
        statement = trial_delayed_pair.scope_statement(payload)
        if statement is None:
            source.seek(0)
            return None
        header = []
        plan = _reference_scope_intake(payload, "table.txt", statement)
    elif narrative_decisions and suffix == ".txt" and (plan := _trial_narrative_note(sample, "table.txt")) is not None:
        header = []
    elif settlement_header := trial_delayed_pair.declaration_header(sample):
        _, header = settlement_header
        plan = _settlement_intake(table_import._bytes(source), "table.csv", settlement_header)
    else:
        if suffix != ".csv":
            return None
        header, encoding = _header(sample)
        role, source_auxiliary = _auxiliary_role(header, BANK_SOURCE_EVIDENCE_PROFILE)
        if len(header) != len(set(header)) or (role is None and tuple(header) not in _DECLARED_SCHEMAS):
            return None
        payload = table_import._bytes(source)
        if role is None:
            plan = _declared_intake_table(payload, "table.csv", header, encoding, ",")
            if plan is None:
                source.seek(0)
                return None
        else:
            plan = _auxiliary(payload, "table.csv", header, encoding, role,
                              evidence=not source_auxiliary, native_observations=True)
            intake_role = _intake_aux_role(header, role)
            if intake_role != role:
                _declare_intake_role(plan, intake_role)
    document = plan["documents"][0]
    document.update(columns=table_import._columns(header, profile), header_rows=[1] if header else [])
    document.setdefault("source_rows", document["row_count"] + 1)
    plan.update(profile=profile, sheet="")
    from . import evidence_resolution
    evidence_resolution.bind_families(plan)
    table_cancellations.refresh_stats(plan)
    return plan


def _unreadable_member(payload, path):
    part = import_attachments.plan(payload, path)
    record = part["records"][0]
    record.update(disposition="unresolved", reason_code=UNREADABLE_REASON)
    record["details"].update(financial_effect="undetermined", reason="该成员无法安全读入，原件已保留")
    record["details"][RETENTION_DETAIL] = MEMBER_RETENTION_POLICY
    return part


def parse(source, profile=PROFILE, *, _stage=False, _operation_scopes=False, member_retention=None,
          pdf_member_observations=False, narrative_decisions=False, _trial_members=False,
          transaction_status_policy=None):
    if profile not in SUPPORTED_PROFILES:
        raise table_import.TableImportError("不支持的反馈包解析规则")
    if transaction_status_policy is not None and (transaction_status_policy != table_import.TRANSACTION_STATUS_POLICY
            or profile not in table_import.PRECISE_PROFILES):
        raise table_import.TableImportError("不支持的交易状态解释规则")
    if member_retention is not None and (member_retention != MEMBER_RETENTION_POLICY or profile not in EVIDENCE_PROFILES):
        raise table_import.TableImportError("不支持的目录成员保全规则")
    replay_trial = profile == TRIAL_MEMBER_PROFILE
    trial_members = replay_trial or _trial_members and profile == BANK_SOURCE_EVIDENCE_PROFILE
    if replay_trial:
        profile = BANK_SOURCE_EVIDENCE_PROFILE
    plan = {"profile": profile, "mode": "B", "mapping": {}, "columns": [], "sheet": "",
            "rows": [], "records": [], "members": [], "documents": [], "mapping_evidence": []}
    if profile in CANCELLATION_PROFILES:
        plan.update(cancelled_rows=[], cancellations=[])
    if _stage and (profile in EVIDENCE_PROFILES or profile in {SOURCE_OBSERVATION_PROFILE, AUXILIARY_ROWS_PROFILE, *BANK_SOURCE_CSV_PROFILES}):
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
        if part.get("profile") in table_import.TRIAL_TABLE_PROFILES:
            family = prefix
            plan["profile"] = TRIAL_MEMBER_PROFILE
        for record in part["records"]:
            old_path = record["member_path"]
            path = prefix if old_path in {"table.csv", prefix} else f"{prefix}/{old_path}"
            record.update(record_no=offset + record["record_no"], member_path=path,
                          record_key=table_import.record_key(path, hashes[old_path], record["source_row"]))
            if part.get("profile") in table_import.TRIAL_TABLE_PROFILES:
                record["details"][table_import.TRIAL_MEMBER_PROFILE_FIELD] = part["profile"]
            for field in ("paired_record_no", "cancellation_witness_record_no"):
                if field in record["details"]:
                    record["details"][field] = str(offset + int(record["details"][field]))
            if details_pool is not None and not isinstance(record["details"], PackedDetails):
                record["details"] = details_pool.pack(record["details"])
        for kind in ("rows", "cancelled_rows"):
            posting_records = {} if kind == "rows" and profile in table_import.SOURCE_CONTRACT_PROFILES else None
            for row in part.get(kind, ()):
                record = part["records"][row["_source_row"] - 1]
                row.update(_source_row=record["record_no"], _source_sheet="", _source_row_kind=table_import.ROW_KIND,
                           _record_key=record["record_key"])
                if posting_records is not None and (repeat := row.get("_source_posting_repeat_key")):
                    # This member's bytes are fixed and its path is unique in
                    # the archive. Bind to the first retained position before
                    # its rows are encoded into the staging store.
                    key = record["member_path"], repeat
                    row["_source_posting_record_key"] = posting_records.setdefault(key, record["record_key"])
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
                    elif suffix == ".pdf" and pdf_member_observations and member_retention == MEMBER_RETENTION_POLICY:
                        part = pdf_text_observations.archive_plan(payload, path) or import_attachments.plan(payload, path)
                        merge(part, path, prefix)
                    elif suffix not in (".csv", ".tsv", ".txt", ".xlsx") and not (
                            suffix == ".xls" and profile in XLS_EVIDENCE_PROFILES):
                        merge(import_attachments.plan(payload, path), path, prefix)
                    else:
                        try:
                            if member_retention == MEMBER_RETENTION_POLICY and suffix in {".csv", ".tsv", ".txt"}:
                                from .services import trial_delayed_pair
                                if settlement_header := trial_delayed_pair.declaration_header(payload):
                                    merge(_settlement_intake(payload, path, settlement_header), path, prefix)
                                    payload = None
                                    continue
                                if suffix == ".txt" and (statement := trial_delayed_pair.scope_statement(payload)):
                                    merge(_reference_scope_intake(payload, path, statement), path, prefix)
                                    payload = None
                                    continue
                                if (narrative_decisions and suffix == ".txt"
                                        and (description := _trial_narrative_note(payload, path)) is not None):
                                    merge(description, path, prefix)
                                    payload = None
                                    continue
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
                            native_observations = profile in table_import.NATIVE_OBSERVATION_PROFILES
                            role, source_auxiliary = _auxiliary_role(header, profile)
                            if member_retention == MEMBER_RETENTION_POLICY and len(header) != len(set(header)):
                                role, source_auxiliary = None, False
                            member_profile = (profile if profile in EVIDENCE_PROFILES or profile in {GUARDED_PROFILE, PRECISE_PROFILE, REFERENCE_ROLE_PROFILE, OPTIONAL_REFERENCE_PROFILE, TIME_ROLE_PROFILE, ZONED_PROFILE, SOURCE_CONTRACT_PROFILE, SOURCE_TEXT_PROFILE, BANK31_VARIANT_PROFILE, SOURCE_LEDGER_PROFILE, POSTING_PAIR_PROFILE, AUXILIARY_SCOPE_PROFILE, NATIVE_OBSERVATION_PROFILE, UNORIENTED_PROFILE, NATIVE31_PROFILE, SOURCE_OBSERVATION_PROFILE, AUXILIARY_ROWS_PROFILE, *BANK_SOURCE_CSV_PROFILES} else
                                              table_import.ARCHIVE_MEMBER_PROFILE if profile == HEADER_PROFILE
                                              else table_import.IDENTITY_PROFILE)
                            if role or member_profile != profile:
                                decoded_csv = None
                            declared = None
                            if member_retention == MEMBER_RETENTION_POLICY:
                                declared = (_scoped_note(payload, path) if suffix == ".txt" else None)
                                if declared is None and suffix in {".csv", ".tsv", ".txt"}:
                                    declared = _declared_intake_table(payload, path, header, encoding, delimiter)
                            if declared is not None:
                                merge(declared, path, prefix)
                                payload = None
                                continue
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
                                            _decoded_csv=decoded_csv,
                                            _trial_archive_member=trial_members and suffix in {".csv", ".xlsx"},
                                            transaction_status_policy=transaction_status_policy))
                            if role and profile in table_import.AUXILIARY_ROWS_ARCHIVE_PROFILES:
                                part["documents"][0]["columns"] = table_import._columns(header, profile)
                            if role and member_retention == MEMBER_RETENTION_POLICY:
                                document = part["documents"][0]
                                intake_role = _intake_aux_role(header, role)
                                if intake_role != role:
                                    _declare_intake_role(part, intake_role)
                            try:
                                if (profile in CANCELLATION_PROFILES and not role and suffix == ".csv"
                                        and part.get("profile") not in table_import.TRIAL_TABLE_PROFILES):
                                    table_cancellations.apply(part, payload, allow_feedback28=profile in EVIDENCE_PROFILES or native_observations,
                                                              csv_format=part.get("_csv_format"))
                            finally:
                                part.pop("_csv_format", None)
                        except table_import.TableImportError as error:
                            if member_retention != MEMBER_RETENTION_POLICY or str(error) not in _UNREADABLE_MEMBER_ERRORS:
                                raise
                            part = _unreadable_member(payload, path)
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
        if replay_trial and plan["profile"] != TRIAL_MEMBER_PROFILE:
            raise table_import.TableImportError("目录叶成员与冻结解析版本不一致")
        if profile in CANCELLATION_PROFILES:
            plan["stats"] = statistics.result()
            if profile in EVIDENCE_PROFILES or profile in {SOURCE_OBSERVATION_PROFILE, AUXILIARY_ROWS_PROFILE, *BANK_SOURCE_CSV_PROFILES}:
                from . import evidence_resolution
                # Only the complete envelope knows final member positions and
                # source families. Cancellation and merge need no early binding.
                try:
                    if profile in {SOURCE_OBSERVATION_PROFILE, AUXILIARY_ROWS_PROFILE, *BANK_SOURCE_CSV_PROFILES}:
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
