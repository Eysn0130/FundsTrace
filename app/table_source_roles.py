"""Closed header signatures for the supported feedback workbook table roles.

This only classifies a complete header. It does not infer an owner from query
conditions, interpret a financial value, or turn auxiliary balances into flows.
Column order and trailing unused worksheet columns do not change the role.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date


def _normalize(header):
    return unicodedata.normalize("NFKC", header).strip().casefold()


SCOPED_FIELD_NOTE_HEADER = ("适用表区", "字段", "原始用途说明")
NARRATIVE_FIELD_NOTE_HEADER = ("适用区域", "字段", "说明原文")
NARRATIVE_FIELD_NOTE_POLICY = "narrative-field-notes-v1"
SCOPED_FIELD_NOTE_POLICY = "scoped-field-notes-v1"
CODED_FIELD_NOTE_POLICY = "coded-field-notes-v1"
CODED_FIELD_NOTE_SHEET = "字段说明"
_USE_CODE = re.compile(r"用途码 ([A-Z][A-Z0-9_-]{0,15})")
_FIELD_NOTE_SPAN = re.compile(r"(.+)!A([1-9][0-9]*):([A-Z]{1,3})([1-9][0-9]*)")
_CODE_OWNER = "本表逐笔交易的本方账号"
_CODE_EXCLUDED = {"归集备用账号，不记录本表逐笔交易本方账号", "归档备查账号，不记录本表逐笔交易本方账号"}
DATED_ACCOUNT_REGISTRY = ("登记主体标识", "开户名称", "开户账号", "卡号", "关系有效起日", "关系有效止日", "签发方")
CODED_BANK_DIRECTION = "交易方向(D:借|C:贷)"
CODED_BANK_DIRECTIONS = {"C:贷": "in", "D:借": "out"}
_CODED_BANK_CSV_COLUMNS = {
    3: "交易日期", 4: "交易时间", 6: "客户名称", 7: "交易卡号", 8: "交易流水", 11: "账户代码",
    12: CODED_BANK_DIRECTION, 13: "币种名称", 14: "交易金额", 15: "联机余额", 16: "摘要名称",
    17: "文字摘要", 21: "对方帐号", 22: "对方客户名称", 23: "对方帐号开户机构名称",
    49: "交易摘要", 52: "币种", 54: "证件号",
}
_INLINE_TITLES = r"(?:对账单|银行对账单|材料说明|文件说明|数据说明|(?:合成)?(?:示例|样例))"
_INLINE_CAPTION = re.compile(rf"(?:{_INLINE_TITLES}[:：]\s*)?(?:以下|下方)"
                             r"[^：:；;，,。\r\n]{1,18}用途(?:只|仅)由(?:页内|本页|下方|本表区)"
                             r"说明(?:限定|确定)[。.]?")
INLINE_FIELD_NOTE = re.compile(r"^仅(?:对|适用于)(?:本页|本表区)(?:下方|下列|所列)?(?:逐笔)?(?:明细|流水|交易)[:：]")


def inline_field_caption(text):
    """Recognize a cover caption deferring column roles to the sheet note."""
    return (re.fullmatch(_INLINE_TITLES, _normalize(text)) is not None
            or _INLINE_CAPTION.fullmatch(text.strip()) is not None)


def dated_account_record(row):
    if len(row) != len(DATED_ACCOUNT_REGISTRY) or any(
            not cell.strip() or len(cell) > 128 or "\n" in cell or "\r" in cell for cell in row):
        raise ValueError("incomplete registration source row")
    subject, name, account, card, first, last, issuer = (value.strip() for value in row)
    start, end = date.fromisoformat(first), date.fromisoformat(last)
    if start.isoformat() != first or end.isoformat() != last or start > end:
        raise ValueError("invalid registration period")
    return subject, name, account, card, start, end, issuer


def _full_field_note_span(area, sheet, header, last, columns):
    scope = _FIELD_NOTE_SPAN.fullmatch(area)
    if scope is None or scope[1] != sheet or scope[2] != str(header) or scope[4] != str(last):
        return False
    width = 0
    for letter in scope[3]:
        width = width * 26 + ord(letter) - 64
    return width == columns


def scoped_field_note(cells, sheet, header, last, headings):
    """Locate one unique heading in a declaration covering its full table."""
    if len(cells) != 3 or not all(isinstance(cell, str) and cell.strip() for cell in cells):
        return None
    area, label, statement = (cell.strip() for cell in cells)
    if not _full_field_note_span(area, sheet, header, last, len(headings)):
        return None
    return _field_note_column(label, statement, headings)


def _field_note_column(label, statement, headings):
    if not statement.startswith(label):
        return None
    matches = [number for number, heading in enumerate(headings, 1)
               if isinstance(heading, str) and _normalize(heading) == _normalize(label)]
    if len(matches) == 1:
        return matches[0], statement[len(label):].strip().rstrip("。")
    return None


def has_account_use_code_notes(rows):
    """Recognize the declared source format before deciding whether it proves a role."""
    return tuple(rows.get(1, ())) == SCOPED_FIELD_NOTE_HEADER and any(
        number > 1 and len(row) >= 3 and (
            type(row[1]) is str and row[1].startswith("用途码 ")
            or type(row[2]) is str and "使用用途码 " in row[2])
        for number, row in rows.items())


@dataclass(frozen=True)
class AccountUseCodeProof:
    column: int
    binding_row: int
    definition_row: int


@dataclass(frozen=True)
class AccountUseCodeIssue:
    reason: str
    code: str
    source_rows: tuple[int, ...]


def review_account_use_codes(rows, *, sheet, header_row, last_row, headings, columns):
    """Verify the unique owner, or identify a definite gap in the closed note format."""
    columns = set(columns)
    # A preceding source line can supply a competing role outside this contract.
    if header_row != 1 or len(columns) < 2 or not has_account_use_code_notes(rows):
        return None
    bindings, definitions, conflicts = {}, {}, {}
    outside, duplicate = [], False
    for number, cells in rows.items():
        if number == 1 or not any(cell not in ("", None) for cell in cells):
            continue
        if len(cells) != 3 or any(type(cell) is not str or not cell.strip() for cell in cells):
            return None
        area, label, statement = (cell.strip() for cell in cells)
        if not _FIELD_NOTE_SPAN.fullmatch(area):
            return None
        column = None
        if name := _USE_CODE.fullmatch(label):
            code = name[1]
            prefix = f"{label} 表示"
            purpose = statement.removeprefix(prefix).removesuffix("。")
            if not statement.startswith(prefix) or purpose not in {_CODE_OWNER, *_CODE_EXCLUDED}:
                return None
            is_owner = purpose == _CODE_OWNER
        else:
            target = _field_note_column(label, statement, headings)
            if target is None:
                return None
            column, purpose = target
            prefix = "使用"
            name = _USE_CODE.fullmatch(purpose.removeprefix(prefix))
            if column not in columns or not purpose.startswith(prefix) or name is None:
                return None
            code = name[1]
        if not _full_field_note_span(area, sheet, header_row, last_row, len(headings)):
            outside.append((column, code, number))
        elif column is not None:
            duplicate |= column in bindings
            bindings[column] = (code, number)
        elif code in definitions:
            previous_owner, previous_row = definitions[code]
            if previous_owner != is_owner:
                conflicts.setdefault(code, (previous_row, number))
            duplicate = True
        else:
            definitions[code] = (is_owner, number)
    bound_codes = {code for code, _ in bindings.values()}
    for code, rows in conflicts.items():
        if code in bound_codes:
            return AccountUseCodeIssue("use_code_conflicting_definitions", code, rows)
    if outside:
        for column, code, row in outside:
            if (column is not None and column not in bindings
                    or column is None and code in bound_codes and code not in definitions):
                return AccountUseCodeIssue("use_code_wrong_scope", code, (row,))
        return None
    if duplicate or bindings.keys() != columns:
        return None
    if not definitions.keys() <= bound_codes:
        return None
    for code, row in bindings.values():
        if code not in definitions:
            return AccountUseCodeIssue("use_code_missing_definition", code, (row,))
    owners = [AccountUseCodeProof(column, row, definitions[code][1])
              for column, (code, row) in bindings.items() if definitions[code][0]]
    if len(owners) != 1:
        return None
    [proof] = owners
    return proof


def verify_account_use_codes(rows, *, sheet, header_row, last_row, headings, columns, citation=None):
    proof = review_account_use_codes(rows, sheet=sheet, header_row=header_row, last_row=last_row,
                                   headings=headings, columns=columns)
    if not isinstance(proof, AccountUseCodeProof):
        return None
    if citation is not None and citation != (proof.column, proof.binding_row, proof.definition_row):
        return None
    return proof


def narrative_field_note(cells, sheet, header, last, headings):
    """Bind an independent explanation to its physical heading and full region."""
    if len(cells) != 3 or not all(isinstance(value, str) and value.strip() for value in cells):
        return None
    area, label, statement = (value.strip() for value in cells)
    return scoped_field_note((area, label, label + statement), sheet, header, last, headings)


_ACCOUNT = (
    "账号", "卡号", "账户类别", "币种", "可用余额", "账户余额", "账户状态", "开户日期",
    "开户网点", "销户日期", "销户网点", "开户网点代码", "最后交易时间", "钞汇标志", "备注",
)
_ACCOUNT_INQUIRY = frozenset(("调查户名", "开户银行网点", "证件号", "客户号", "账户类型", "开户日期", "销户日期", "备注"))
_CLIENT = (
    "客户名称", "法人代表", "法人代表证件类型", "法人代表证件号码", "联系手机", "单位地址",
    "住宅地址", "住宅电话", "联系电话", "证件号码", "工作单位", "客户工商执照号码", "单位电话",
    "账单地址", "邮箱地址", "地税纳税号", "国税纳税号", "证件类型代码", "代办人证件类型",
    "代办人姓名", "代办人证件号码", "任务流水号", "查询反馈结果", "标识",
)
_SUBACCOUNT = (
    "账卡号", "子账户序号", "子账户类别", "子账户账号", "币种", "钞汇标志", "账户余额",
    "账户状态", "可用余额",
)
_TRANSACTION_EXPLICIT = (
    "本方卡号", "姓名(查询条件)", "证件号码(查询条件)", "交易时间", "交易金额", "借贷标志",
    "交易余额", "交易对方名称", "交易对方证件号码", "交易对方账号", "交易对方账号开户行",
    "交易类型", "交易摘要", "交易流水号", "任务流水号", "传票号", "凭证号", "IP地址", "MAC地址",
    "交易对手余额", "终端号", "商户名称", "商户号", "查询反馈结果", "查询帐号", "交易网点名称",
    "交易柜员号", "交易是否成功", "查询卡号", "本方账号", "现金标志", "日志号", "币种",
    "凭证种类", "备注", "交易对方卡号", "交易网点代码", "交易发生地",
)
_TRANSACTION_QUERY_ONLY = (
    "姓名(查询条件)", "证件号码(查询条件)", "交易时间", "交易金额", "借贷标志", "交易余额",
    "交易对方名称", "交易对方证件号码", "交易对方账号开户行", "交易类型", "交易摘要",
    "交易流水号", "任务流水号", "传票号", "凭证号", "IP地址", "MAC地址", "交易对手余额",
    "终端号", "商户名称", "商户号", "查询反馈结果", "查询帐号", "交易网点名称",
    "交易对方账卡号", "交易柜员号", "交易是否成功", "查询卡号", "现金标志",
    "交易对方帐卡号类型", "日志号", "币种", "凭证种类", "备注", "交易网点代码", "交易发生地",
)


def _signature(headers):
    return frozenset(_normalize(header) for header in headers)


def is_account_inquiry(headers):
    """Recognize a preserved query worksheet without assigning identity roles."""
    if not 11 <= len(headers) <= 13:
        return False
    labels = [_normalize(value) for value in headers]
    while labels and not labels[-1]:
        labels.pop()
    names = frozenset(labels)
    suffix = "期账户号"
    return (11 <= len(names) == len(labels) and "" not in names and _ACCOUNT_INQUIRY <= names
            and sum(name.endswith(suffix) and bool(name[:-len(suffix)].strip()) for name in names) == 2)


def is_coded_bank_csv(headers):
    return len(headers) == 55 and all(headers[position - 1] == label and headers.count(label) == 1
                                      for position, label in _CODED_BANK_CSV_COLUMNS.items())


_ROLES = {
    _signature(_ACCOUNT): "account",
    _signature(_ACCOUNT[1:]): "account",
    _signature(_CLIENT): "client",
    _signature(header for header in _CLIENT if header != "账单地址"): "client",
    _signature(_SUBACCOUNT): "subaccount",
    _signature(_TRANSACTION_EXPLICIT): "transaction_explicit",
    _signature(header for header in _TRANSACTION_EXPLICIT if header != "查询帐号"):
        "transaction_explicit",
    _signature(_TRANSACTION_QUERY_ONLY): "transaction_query_only",
}


def query_condition(header):
    """Recognize an annotation label, without assigning any data-field role."""
    name = _normalize(header)
    suffix = "(查询条件)"
    return (name.endswith(suffix) and bool(name[:-len(suffix)].strip())
            and name.count(suffix) == 1
            and not any(unicodedata.category(char) in {"Cc", "Cf"} for char in name))


_QUERY_REQUIRED_SIGNATURE = _signature(
    header for header in _TRANSACTION_QUERY_ONLY if not query_condition(header))


def classify(headers, *, query_conditions=False):
    """Return a known table role, or ``None`` for any incomplete/unknown header.

    Only trailing empty header cells are permitted. Duplicate nonempty names
    remain ambiguous even if their source spelling differs by normalization.
    The opt-in annotation variant is reserved for the v14 transaction contract.
    """
    names = [_normalize(header) for header in headers]
    while names and not names[-1]:
        names.pop()
    if not names or "" in names or len(names) != len(set(names)):
        return None
    signature = frozenset(names)
    role = _ROLES.get(signature)
    if role or not query_conditions:
        return role
    # v14's uniform transaction contract permits different query annotation
    # labels. All 34 substantive columns, both annotations and their unique
    # positions must still exist; no partial/generic/auxiliary table qualifies.
    annotations = [name for name in names if query_condition(name)]
    if len(annotations) == 2 and signature - set(annotations) == _QUERY_REQUIRED_SIGNATURE:
        return "transaction_query_only"
    return None
