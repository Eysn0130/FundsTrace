"""Read BIFF8 cells only after their workbook and formula coordinates close.

xlrd's public Cell API exposes formula *results*, so the independent BIFF
metadata pass supplies formula provenance before any cached value is accepted.
No formula is evaluated and OLE streams are always opened read-only.
"""
import datetime as dt
import hashlib
import io
import json
import math
import re
import struct
from dataclasses import dataclass


_BOF = 0x0809
_EOF = 0x000A
_BOUNDSHEET = 0x0085
_FILEPASS = 0x002F
_FORMULA = 0x0006
_ARRAY = 0x0221
_SHARED = 0x04BC


@dataclass(frozen=True, slots=True)
class _Sheet:
    offset: int
    name: str
    kind: int
    worksheet: bool
    formulas: frozenset


def _record(stream, position, end):
    from .table_import import TableImportError

    if position + 4 > end:
        raise TableImportError("XLS的BIFF记录边界损坏，文件未接纳")
    tag, size = struct.unpack_from("<HH", stream, position)
    following = position + 4 + size
    if following > end:
        raise TableImportError("XLS的BIFF记录长度损坏，文件未接纳")
    if tag in (0x0206, 0x0406):
        # xlrd also understands historical formula opcodes. They are not BIFF8
        # records and must never enter this reader as ordinary cached values.
        raise TableImportError("XLS含有不支持的旧版公式记录，文件未接纳")
    return tag, memoryview(stream)[position + 4:following], following


def _bof(stream, position, end, kind):
    from .table_import import TableImportError

    tag, payload, following = _record(stream, position, end)
    if tag != _BOF or len(payload) != 16:
        raise TableImportError("XLS工作簿或工作表起始记录无效，文件未接纳")
    version, actual_kind = struct.unpack_from("<HH", payload)
    if version != 0x0600 or actual_kind != kind:
        raise TableImportError("仅支持未加密的Excel 97–2003 BIFF8 XLS工作簿")
    return following


def _boundsheet(payload):
    from .table_import import TableImportError

    if len(payload) < 8:
        raise TableImportError("XLS工作表身份记录损坏，文件未接纳")
    offset, visibility, kind, length, flags = struct.unpack_from("<IBBBB", payload)
    if not 1 <= length <= 31 or visibility & 3 == 3:
        raise TableImportError("XLS工作表身份记录无效，文件未接纳")
    byte_length = length * (2 if flags & 1 else 1)
    if len(payload) != 8 + byte_length:
        raise TableImportError("XLS工作表名称边界损坏，文件未接纳")
    name = bytes(payload[8:]).decode("utf-16le" if flags & 1 else "latin1")
    if (any(character in name for character in "\x00\x03:\\*?/[]")
            or name.startswith("'") or name.endswith("'")):
        raise TableImportError("XLS工作表名称无效，文件未接纳")
    return offset, name, kind


def _formula_range(payload, tag):
    from .table_import import MAX_COLUMNS, TableImportError

    header = 14 if tag == _ARRAY else 10
    if len(payload) < header:
        raise TableImportError("XLS共享或数组公式记录损坏，文件未接纳")
    first_row, last_row, first_col, last_col = struct.unpack_from("<HHBB", payload)
    token_length = struct.unpack_from("<H", payload, header - 2)[0]
    if (first_row > last_row or first_col > last_col or last_col >= MAX_COLUMNS
            or token_length > len(payload) - header):
        raise TableImportError("XLS共享或数组公式范围无效，文件未接纳")
    return first_row, last_row, first_col, last_col


def _metadata(stream):
    from .table_import import MAX_COLUMNS, MAX_SHEETS, TableImportError

    end = len(stream)
    position = _bof(stream, 0, end, 0x0005)
    bounds = []
    while True:
        tag, payload, position = _record(stream, position, end)
        if tag == _FILEPASS:
            raise TableImportError("不支持加密的XLS工作簿，文件未接纳")
        if tag == _BOF:
            raise TableImportError("XLS工作簿全局结构未闭合，文件未接纳")
        if tag == _BOUNDSHEET:
            bounds.append(_boundsheet(payload))
            if len(bounds) > MAX_SHEETS:
                raise TableImportError("工作表数量超出安全处理范围")
        if tag == _EOF:
            if payload:
                raise TableImportError("XLS工作簿结束记录无效，文件未接纳")
            break
    if (not bounds or len({entry[0] for entry in bounds}) != len(bounds)
            or len({entry[1].casefold() for entry in bounds}) != len(bounds)):
        raise TableImportError("XLS工作表身份或位置不唯一，文件未接纳")
    sheets = {}
    for offset, name, kind in sorted(bounds):
        if offset != position:
            raise TableImportError("XLS工作表位置与记录结构不一致，文件未接纳")
        expected_kind = {0: 0x0010, 1: 0x0040, 2: 0x0020}.get(kind)
        if expected_kind is None:
            raise TableImportError("XLS含有不支持的工作表类型，文件未接纳")
        position = _bof(stream, offset, end, expected_kind)
        formulas, shared_users, array_users = set(), {}, {}
        shared_ranges, array_ranges = {}, {}
        preceding_formula = None
        depth, wsbool = 1, None
        while True:
            record_position = position
            tag, payload, position = _record(stream, position, end)
            if tag == _FILEPASS:
                raise TableImportError("不支持加密的XLS工作簿，文件未接纳")
            if tag == _BOF:
                # Embedded charts have their own BOF/EOF pair; their EOF must
                # never terminate the containing transaction worksheet.
                _bof(stream, record_position, end, 0x0020)
                depth += 1
                continue
            if tag == _BOUNDSHEET:
                raise TableImportError("XLS工作表结构未闭合，文件未接纳")
            if tag == 0x0081 and depth == 1 and kind == 0:
                if len(payload) != 2 or wsbool is not None:
                    raise TableImportError("XLS工作表类型记录不唯一或损坏，文件未接纳")
                wsbool = struct.unpack_from("<H", payload)[0]
            if tag == _FORMULA and depth == 1 and kind == 0:
                if len(payload) < 22:
                    raise TableImportError("XLS公式记录损坏，文件未接纳")
                row, col = struct.unpack_from("<HH", payload)
                token_length = struct.unpack_from("<H", payload, 20)[0]
                if col >= MAX_COLUMNS or token_length > len(payload) - 22:
                    raise TableImportError("XLS公式坐标或记录长度无效，文件未接纳")
                flags = struct.unpack_from("<H", payload, 14)[0]
                anchor = None
                if token_length >= 5 and payload[22] == 0x01:  # PtgExp
                    anchor = struct.unpack_from("<HH", payload, 23)
                if flags & 8 and anchor is None:
                    raise TableImportError("XLS共享公式缺少锚点信息，文件未接纳")
                if (row, col) in formulas:
                    raise TableImportError("XLS公式单元格坐标不唯一，文件未接纳")
                formulas.add((row, col))
                if anchor is not None:
                    users = shared_users if flags & 8 else array_users
                    users.setdefault(anchor, set()).add((row, col))
                preceding_formula = ((row, col), flags, anchor)
            elif tag in (_ARRAY, _SHARED) and depth == 1 and kind == 0:
                rectangle = _formula_range(payload, tag)
                first_row, last_row, first_col, last_col = rectangle
                if preceding_formula is None:
                    raise TableImportError("XLS共享或数组公式缺少前置单元格，文件未接纳")
                coordinate, flags, anchor = preceding_formula
                if (anchor != coordinate or not first_row <= coordinate[0] <= last_row
                        or not first_col <= coordinate[1] <= last_col
                        or anchor in shared_ranges or anchor in array_ranges):
                    raise TableImportError("XLS共享或数组公式锚点不闭合，文件未接纳")
                if tag == _SHARED:
                    use_count = payload[7]
                    if not flags & 8 or not use_count:
                        raise TableImportError("XLS共享公式缺少使用者依据，文件未接纳")
                    shared_ranges[anchor] = (rectangle, use_count)
                else:
                    if flags & 8 or coordinate != (first_row, first_col):
                        raise TableImportError("XLS数组公式起始单元格不一致，文件未接纳")
                    array_ranges[anchor] = rectangle
                preceding_formula = None
            if tag == _EOF:
                if payload:
                    raise TableImportError("XLS工作表结束记录无效，文件未接纳")
                depth -= 1
                if depth == 0:
                    break
        if kind == 0 and wsbool is None:
            raise TableImportError("XLS工作表缺少类型依据，文件未接纳")
        if set(shared_users) != set(shared_ranges) or set(array_users) != set(array_ranges):
            raise TableImportError("XLS共享或数组公式引用不闭合，文件未接纳")
        for anchor, (rectangle, use_count) in shared_ranges.items():
            first_row, last_row, first_col, last_col = rectangle
            users = shared_users[anchor]
            if len(users) != use_count or any(
                    not first_row <= row <= last_row or not first_col <= col <= last_col
                    for row, col in users):
                raise TableImportError("XLS共享公式范围或使用者数量不一致，文件未接纳")
        for anchor, rectangle in array_ranges.items():
            first_row, last_row, first_col, last_col = rectangle
            users = array_users[anchor]
            area = (last_row - first_row + 1) * (last_col - first_col + 1)
            if len(users) != area or any(
                    not first_row <= row <= last_row or not first_col <= col <= last_col
                    for row, col in users):
                raise TableImportError("XLS数组公式未覆盖完整范围，文件未接纳")
        worksheet = kind == 0 and not wsbool & 0x0010
        sheets[offset] = _Sheet(offset, name, kind, worksheet,
                                frozenset(formulas))
    if any(stream[position:]):
        raise TableImportError("XLS工作表之外含有未闭合记录，文件未接纳")
    return [sheets[offset] for offset, _, _ in bounds]


def _date_value(value, number_format, datemode):
    import xlrd
    from openpyxl.styles.numbers import is_datetime, is_timedelta_format

    # Excel format tokens are case-insensitive. The shared classifier expects
    # lower-case tokens; keep the original format unchanged for identity rules.
    fmt = number_format.lower()
    if not math.isfinite(value) or value < 0 or is_timedelta_format(fmt):
        return value, False, True
    # Ignore display literals/locale/padding when distinguishing month-only
    # date formats from clock formats. A display format cannot erase a date
    # component already encoded in a non-elapsed Excel date serial.
    tokens = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.|_.|\*.', "", fmt.split(";")[0])
    kind = is_datetime(tokens)
    clock = kind == "time" and any(token in tokens for token in "hs")
    date_format = kind == "date" or (kind == "time" and not clock and "m" in tokens)
    if datemode == 0 and (math.floor(value) == 60 or value < 1 and not clock):
        return value, False, True
    if value < 1 and not clock and not date_format and kind != "datetime":
        return value, False, True
    try:
        converted = xlrd.xldate_as_datetime(value, datemode)
        if value < 1 and clock:
            return converted.time(), False, False
        if date_format and converted.time() == dt.time():
            return converted.date(), True, False
        return converted, False, False
    except (ValueError, OverflowError):
        return value, False, True


def _rows(sheet, book, metadata):
    import xlrd
    from openpyxl.styles.numbers import is_timedelta_format
    from .table_import import _WorkbookValue

    pending_empty = 0
    for row_index in range(sheet.nrows):
        row = []
        for col_index in range(sheet.row_len(row_index)):
            cell = sheet.cell(row_index, col_index)
            formula = (row_index, col_index) in metadata.formulas
            if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK) and not formula:
                row.append(None)
                continue
            if cell.ctype == xlrd.XL_CELL_TEXT and not formula:
                row.append(cell.value)
                continue
            fmt = book.format_map[book.xf_list[cell.xf_index].format_key].format_str
            value = cell.value
            date_only, invalid = False, False
            if cell.ctype == xlrd.XL_CELL_DATE:
                value, date_only, invalid = _date_value(value, fmt, book.datemode)
            elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                value, invalid = bool(value), True
            elif cell.ctype == xlrd.XL_CELL_ERROR:
                value, invalid = xlrd.error_text_from_code.get(value, "#ERROR"), True
            elif cell.ctype == xlrd.XL_CELL_NUMBER:
                if is_timedelta_format(fmt):
                    # xlrd classifies [m]/[s] durations as NUMBER, unlike [h].
                    # A duration cannot become transaction money or a date.
                    invalid = True
                elif math.isfinite(value):
                    if value.is_integer():
                        value = int(value)
                else:
                    invalid = True
            row.append(_WorkbookValue(value, fmt, formula, date_only, invalid))
        while row and (row[-1] is None or row[-1] == ""):
            row.pop()
        if row:
            for _ in range(pending_empty):
                yield []
            pending_empty = 0
            yield row
        else:
            pending_empty += 1


def read_workbook(raw):
    """Return XLSX-compatible sheet dictionaries from unencrypted BIFF8 bytes."""
    import olefile
    import xlrd
    from .table_import import MAX_BYTES, TableImportError, _collect_rows

    book = None
    try:
        if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
            raise TableImportError("XLS文件大小超出安全处理范围")
        with olefile.OleFileIO(io.BytesIO(raw), write_mode=False,
                               raise_defects=olefile.DEFECT_INCORRECT) as container:
            candidates = [path for path in container.listdir(streams=True, storages=False)
                          if len(path) == 1 and path[0].casefold() in ("workbook", "book")]
            if len(candidates) != 1:
                raise TableImportError("XLS工作簿流身份不唯一或缺失，文件未接纳")
            if container.get_size(candidates[0]) > MAX_BYTES:
                raise TableImportError("XLS工作簿展开大小超出安全处理范围")
            stream = container.openstream(candidates[0]).read()
        metadata = _metadata(stream)
        if not any(sheet.worksheet for sheet in metadata):
            raise TableImportError("XLS不含可支持的普通工作表，文件未接纳")
        book = xlrd.open_workbook(file_contents=raw, formatting_info=True,
                                  on_demand=True, ragged_rows=True, logfile=io.StringIO())
        if (book.biff_version != 80 or book.datemode not in (0, 1)
                or book.sheet_names() != [sheet.name for sheet in metadata if sheet.kind == 0]):
            raise TableImportError("XLS工作表身份与读取结果不一致，文件未接纳")
        digest = hashlib.sha256(raw).hexdigest()
        count, result = 0, []
        for index, source in enumerate(sheet for sheet in metadata if sheet.worksheet):
            try:
                sheet = book.sheet_by_name(source.name)
                rows = _collect_rows(_rows(sheet, book, source), previous_count=count)
                width = max((len(row) for row in rows), default=0)
                for row in rows:
                    row.extend([None] * (width - len(row)))
                count += len(rows)
                result.append({
                    "path": f"worksheets/{index + 1:04d}", "name": source.name,
                    "rows": rows, "sha256": hashlib.sha256(json.dumps([
                        digest, index + 1, source.name], ensure_ascii=False).encode()).hexdigest(),
                    "size": 0, "encoding": "xls", "delimiter": "",
                })
            finally:
                book.unload_sheet(source.name)
        return result
    except TableImportError:
        raise
    except Exception:
        raise TableImportError("XLS工作簿损坏、加密或格式不受支持") from None
    finally:
        if book is not None:
            book.release_resources()
