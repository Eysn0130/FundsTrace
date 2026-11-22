"""Bounded, read-only cell lookup in an already verified retained original.

No financial interpretation, classification, filesystem extraction or writes.
Coordinates and headers come from the frozen journal/receipt, not a new parse.
"""
import csv
from bisect import bisect_right
from collections import Counter
import datetime as dt
import hashlib
import io
import json
import posixpath
import re
import stat
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager

from . import feedback_import, rar_archive, table_archive, table_import


MAX_PAGE_BYTES = 2 * 1024 * 1024


def _csv_records(wrapper, delimiter):
    budget = [0]

    def lines():
        while line := wrapper.readline(MAX_PAGE_BYTES + 1):
            budget[0] += len(line)
            if budget[0] > MAX_PAGE_BYTES:
                raise ValueError('原始记录内容较大，请另存原件查看')
            yield line

    for row in csv.reader(lines(), delimiter=delimiter, strict=True):
        yield row
        budget[0] = 0


def _unknown_csv_delimiter(wrapper):
    """Resolve equivalent structural readings without materializing cell tables."""
    choices = []
    for delimiter in (',', '\t', ';', '|'):
        wrapper.seek(0)
        digest, width, multiple, valid = hashlib.sha256(), None, False, True
        try:
            for row in _csv_records(wrapper, delimiter):
                if width is None:
                    width = len(row)
                if row and any(value.strip() for value in row) and len(row) != width:
                    valid = False
                    break
                multiple = multiple or len(row) > 1
                digest.update(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\n')
        except csv.Error:
            valid = False
        if valid:
            choices.append((delimiter, multiple, digest.digest()))
    if any(multiple for _, multiple, _ in choices):
        choices = [entry for entry in choices if entry[1]]
    distinct = {}
    for delimiter, _, digest in choices:
        distinct.setdefault(digest, delimiter)
    if len(distinct) != 1:
        raise ValueError('原始列结构不能唯一对应')
    return next(iter(distinct.values()))


@contextmanager
def _member(source, path, depth=0):
    """Follow only the registered exact member, including closed nested archives."""
    if depth > table_archive.MAX_DEPTH:
        raise ValueError('归档层级超出来源阅览范围')
    with rar_archive.open_archive(source) as archive:
        infos = archive.infolist()
        if len(infos) > table_archive.EVIDENCE_MAX_MEMBERS:
            raise ValueError('归档成员数超出来源阅览范围')
        seen, matches = set(), []
        for info in infos:
            name = table_archive._path(info.orig_filename)
            if name in seen or info.flag_bits & 1 or stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
                raise ValueError('归档来源位置无法唯一核实')
            seen.add(name)
            if not info.is_dir() and (path == name or path.startswith(name + '/')):
                matches.append((name, info))
        if len(matches) != 1:
            raise ValueError('无法定位已登记的原始成员')
        name, info = matches[0]
        if not 0 <= info.file_size <= table_archive.MAX_MEMBER_BYTES:
            raise ValueError('归档成员超出来源阅览范围')
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode='w+b') as payload:
            size = 0
            with archive.open(info) as stream:
                while block := stream.read(min(1024 * 1024, table_archive.MAX_MEMBER_BYTES - size + 1)):
                    size += len(block)
                    if size > table_archive.MAX_MEMBER_BYTES:
                        raise ValueError('归档成员超出来源阅览范围')
                    payload.write(block)
            if size != info.file_size:
                raise ValueError('原始成员大小校验失败')
            payload.seek(0)
            if path == name:
                yield payload
            elif name.lower().endswith(('.zip', '.rar')):
                with _member(payload, path[len(name) + 1:], depth + 1) as nested:
                    yield nested
            else:
                raise ValueError('无法定位已登记的原始成员')


def _csv_rows(source, encoding, document, wanted, profile):
    if encoding not in {'utf-8-sig', 'utf-8', 'gb18030', 'gbk', 'utf-16'}:
        raise ValueError('来源编码未登记，无法读取原始单元格')
    csv.field_size_limit(max(csv.field_size_limit(), table_import.MAX_FIELD))
    wrapper = io.TextIOWrapper(source, encoding=encoding, newline='')
    try:
        header_row = document.get('header_row')
        expected = document.get('columns', [])
        choices = []
        if header_row and expected:
            for delimiter in (',', '\t', ';', '|'):
                wrapper.seek(0)
                try:
                    header = next(row for number, row in enumerate(_csv_records(wrapper, delimiter), 1) if number == header_row)
                except (csv.Error, StopIteration):
                    continue
                if table_import._columns(header, profile) == expected:
                    choices.append(delimiter)
        else:
            if profile in table_import.SOURCE_CONTRACT_PROFILES:
                choices = [_unknown_csv_delimiter(wrapper)]
            elif profile in feedback_import.SUPPORTED_PROFILES:
                choices = [',']
            else:
                sample = wrapper.read(65536)
                try:
                    choices = [csv.Sniffer().sniff(sample, delimiters=',\t;|').delimiter]
                except csv.Error:
                    choices = [_unknown_csv_delimiter(wrapper)]
        if len(choices) != 1:
            raise ValueError('原始列结构不能与冻结表头唯一对应')
        wrapper.seek(0)
        result = {}
        for number, row in enumerate(_csv_records(wrapper, choices[0]), 1):
            if number in wanted:
                if len(row) > table_import.MAX_COLUMNS or any(len(value) > table_import.MAX_FIELD for value in row):
                    raise ValueError('原始单元格超出来源阅览范围')
                result[number] = row
            if number >= max(wanted):
                break
        return result
    finally:
        wrapper.detach()


def _xlsx_rows(source, sheet_index, sheet_name, wanted):
    """Stream original XML scalars; never round-trip floats or execute formulas."""
    def metadata(archive, name):
        if archive.getinfo(name).file_size > MAX_PAGE_BYTES:
            raise ValueError('工作簿元数据超出来源阅览范围')
        return ET.fromstring(archive.read(name))

    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if len(infos) > 2048 or sum(info.file_size for info in infos) > 300 * 1024 * 1024:
            raise ValueError('工作簿展开大小超出来源阅览范围')
        links = {item.attrib['Id']: item for item in metadata(archive, 'xl/_rels/workbook.xml.rels')}
        sheets = [sheet for sheet in metadata(archive, 'xl/workbook.xml').findall('.//{*}sheet')
                  if links[next(value for key, value in sheet.attrib.items() if key.endswith('}id'))].attrib.get('Type', '').endswith('/worksheet')]
        sheet = sheets[sheet_index - 1]
        if sheet_name and sheet.attrib['name'] != sheet_name:
            raise ValueError('工作表与冻结来源不一致')
        rid = next(value for key, value in sheet.attrib.items() if key.endswith('}id'))
        relation = links[rid]
        if relation.attrib.get('TargetMode') == 'External':
            raise ValueError('不读取外部工作表')
        target = relation.attrib['Target']
        path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/' + target)
        if not path.startswith('xl/') or '\\' in path:
            raise ValueError('工作表来源位置无效')
        result, strings, kinds, size = {}, set(), {}, 0
        number, previous_row, parent = None, 0, None
        with archive.open(path) as stream:
            for event, node in ET.iterparse(stream, events=('start', 'end')):
                tag = node.tag.rsplit('}', 1)[-1]
                if event == 'start':
                    if tag == 'sheetData':
                        parent = node
                    elif tag == 'row':
                        number = int(node.attrib.get('r', previous_row + 1))
                        previous_row = number
                        if number in wanted:
                            result[number] = []
                    continue
                if tag == 'c':
                    if number in wanted:
                        cells = result[number]
                        column = len(cells) + 1
                        if 'r' in node.attrib:
                            coordinate = re.fullmatch(r'([A-Z]+)[0-9]+', node.attrib['r'])
                            if coordinate is None:
                                raise ValueError('工作表单元格坐标无效')
                            column = 0
                            for char in coordinate[1]:
                                column = column * 26 + ord(char) - 64
                        if not 1 <= column <= table_import.MAX_COLUMNS:
                            raise ValueError('原始列数超出来源阅览范围')
                        cells.extend([''] * (column - len(cells)))
                        formula, value = node.find('{*}f'), node.find('{*}v')
                        kind = node.attrib.get('t')
                        kinds[number, column] = 'formula' if formula is not None else {'s': 'text', 'str': 'text', 'inlineStr': 'text', 'd': 'date', 'b': 'boolean', 'e': 'error'}.get(kind, 'number')
                        if formula is not None:
                            if not formula.text:
                                raise ValueError('共享公式原文暂无法读取')
                            text = '=' + formula.text
                        elif kind == 'inlineStr':
                            inline = node.find('{*}is')
                            text = ''.join(n.text or '' for n in [*inline.findall('{*}t'), *inline.findall('{*}r/{*}t')]) if inline is not None else ''
                        elif kind == 's' and value is not None:
                            text = ('shared', int(value.text)); strings.add(text[1])
                        else:
                            text = value.text or '' if value is not None else ''
                        if isinstance(text, str):
                            size += len(text.encode('utf-8'))
                            if len(text) > 16384 or size > MAX_PAGE_BYTES:
                                raise ValueError('原始页内容较大，请另存原件查看')
                        cells[column - 1] = text
                    node.clear()
                elif tag == 'row':
                    node.clear()
                    if parent is not None:
                        parent.clear()
                    if number >= max(wanted):
                        break
        shared = {}
        if strings:
            with archive.open('xl/sharedStrings.xml') as stream:
                index, root = 0, None
                for event, node in ET.iterparse(stream, events=('start', 'end')):
                    if event == 'start':
                        if root is None:
                            root = node
                        continue
                    if node.tag.rsplit('}', 1)[-1] != 'si':
                        continue
                    if index in strings:
                        text = ''.join(n.text or '' for n in [*node.findall('{*}t'), *node.findall('{*}r/{*}t')])
                        size += len(text.encode('utf-8'))
                        if len(text) > 16384 or size > MAX_PAGE_BYTES:
                            raise ValueError('原始页内容较大，请另存原件查看')
                        shared[index] = text
                    node.clear()
                    if root is not None:
                        root.clear()
                    if index >= max(strings):
                        break
                    index += 1
        return {number: [shared[value[1]] if isinstance(value, tuple) else value for value in result.get(number, [])] for number in wanted}, kinds


def _xls_rows(source, sheet_index, sheet_name, wanted):
    import olefile
    import xlrd
    from . import table_xls
    raw = source.read(table_import.MAX_BYTES + 1)
    if len(raw) > table_import.MAX_BYTES:
        raise ValueError('工作簿超出来源阅览范围')
    with olefile.OleFileIO(io.BytesIO(raw), write_mode=False, raise_defects=olefile.DEFECT_INCORRECT) as container:
        paths = [p for p in container.listdir() if len(p) == 1 and p[0].casefold() in {'workbook', 'book'}]
        if len(paths) != 1 or container.get_size(paths[0]) > table_import.MAX_BYTES:
            raise ValueError('工作簿流不能唯一核实')
        metadata = table_xls._metadata(container.openstream(paths[0]).read())
    source_sheet = [sheet for sheet in metadata if sheet.worksheet][sheet_index - 1]
    if sheet_name and source_sheet.name != sheet_name:
        raise ValueError('工作表与冻结来源不一致')
    book = xlrd.open_workbook(file_contents=raw, formatting_info=True, on_demand=True, ragged_rows=True, logfile=io.StringIO())
    try:
        result, kinds = {}, {}
        for number, row in enumerate(table_xls._rows(book.sheet_by_name(source_sheet.name), book, source_sheet), 1):
            if number in wanted:
                if any(isinstance(value, table_import._WorkbookValue) and value.formula for value in row):
                    raise ValueError('XLS公式原文暂无法读取')
                result[number] = [_scalar(table_import._raw(value)) for value in row]
                for column, value in enumerate(row, 1):
                    raw = table_import._raw(value)
                    kinds[number, column] = ('boolean' if isinstance(raw, bool) else 'error' if isinstance(value, table_import._WorkbookValue) and value.invalid_type
                                             else 'date' if isinstance(raw, (dt.date, dt.time)) else 'number' if isinstance(raw, (int, float)) else 'text')
            if number >= max(wanted):
                break
        return result, kinds
    finally:
        book.release_resources()


def _scalar(value):
    if value is None:
        return ''
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=' ')
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    return str(value)


def read_cells(source, suffix, member, document, evidence, positions, profile):
    if not positions:
        return {}
    headers = sorted({n for n in [document.get('header_row'), *document.get('header_rows', [])] if isinstance(n, int) and n > 0})
    selected_headers = {number: headers[bisect_right(headers, number) - 1] if bisect_right(headers, number) else None for number in positions}
    wanted = set(positions) | {number for number in selected_headers.values() if number is not None}
    path = member['member_path']
    workbook = member['encoding'] in {'xlsx', 'xls'}
    physical = path.rsplit('/worksheets/', 1)[0] if workbook and '/worksheets/' in path else path

    def read(stream):
        if workbook:
            match = re.search(r'(?:^|/)worksheets/([0-9]{4})$', path)
            if match is None:
                raise ValueError('工作表坐标未登记')
            reader = _xlsx_rows if member['encoding'] == 'xlsx' else _xls_rows
            return reader(stream, int(match[1]), document.get('sheet', ''), wanted)
        return _csv_rows(stream, member['encoding'], document, wanted, profile), {}

    if suffix in {'.zip', '.rar'}:
        with _member(source, physical) as stream:
            rows, kinds = read(stream)
    else:
        rows, kinds = read(source)
    result, size = {}, 0
    by_position, by_name = {}, {}
    for entry in evidence:
        header_row, column = entry.get('header_row'), entry.get('column_index')
        if header_row is not None and column is not None:
            fields = by_position.setdefault((str(header_row), str(column)), [])
        elif header_row is None and column is None:
            fields = by_name.setdefault(entry.get('column'), [])
        else:
            continue
        if entry.get('field') and entry['field'] not in fields:
            fields.append(entry['field'])
    # Historical name-only evidence can identify a column in a single
    # recorded header, provided its display label occurs exactly once.
    if by_name and not by_position and len(headers) == 1:
        header_row, = headers
        names = table_import._columns(rows.get(header_row, []), profile)
        counts = Counter(names)
        for index, name in enumerate(names, 1):
            if counts[name] == 1 and name in by_name:
                by_position[str(header_row), str(index)] = by_name[name]
    for number in positions:
        if number not in rows:
            raise ValueError('原始行与已登记位置不一致')
        header_number = selected_headers[number]
        names = table_import._columns(rows.get(header_number, []), profile)
        cells = []
        for index, value in enumerate(rows[number]):
            header = names[index] if index < len(names) else f'原始第{index + 1}列'
            fields = by_position.get((str(header_number), str(index + 1)), [])
            size += len(value.encode('utf-8')) + len(header.encode('utf-8'))
            if len(value) > 16384 or size > MAX_PAGE_BYTES:
                raise ValueError('原始页内容较大，请另存原件查看')
            cells.append({'column': index + 1, 'header': header, 'value': value, 'fields': fields, 'kind': kinds.get((number, index + 1), 'text')})
        result[number] = cells
    return result
