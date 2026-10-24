"""Synthetic BIFF8 reader checks; no real accounts or bank data."""
import datetime as dt
import hashlib
import io
import json
import struct

import olefile
import pytest
import xlwt

from app import table_import
from xls_fixtures import Cell, workbook


def workbook_bytes(rows, *, datemode=0, name="Synthetic XLS", extra_sheets=()):
    sheets = [(title, [[Cell(*value) if isinstance(value, tuple) else value
                       for value in row] for row in values])
              for title, values in [(name, rows), *extra_sheets]]
    return workbook(sheets, datemode=datemode)


def records(stream):
    position = 0
    while position + 4 <= len(stream):
        tag, size = struct.unpack_from("<HH", stream, position)
        if tag == size == 0:
            break
        yield position, tag, size
        position += size + 4


def mutate_stream(raw, edit):
    output = io.BytesIO(raw)
    with olefile.OleFileIO(output, write_mode=True) as container:
        stream = bytearray(container.openstream("Workbook").read())
        edit(stream)
        container.write_stream("Workbook", bytes(stream))
    return output.getvalue()


def formula_workbook(raw, *, cache_kind="number", cached=3.0, range_kind=None,
                     opcode=0x0006, shared_range=None):
    """Build synthetic cached/shared/array formulas without an Excel process.

    Existing Formula cells form one rectangle unless a sparse shared_range is
    supplied. All formulas belong to one worksheet. This changes BIFF records
    inside unused OLE stream padding and fixes BoundSheet offsets; production
    never writes OLE.
    """
    assert opcode in {0x0006, 0x0206, 0x0406}

    def patch(stream):
        original = [(position, tag, bytes(stream[position + 4:position + 4 + size]))
                    for position, tag, size in records(stream)]
        formulas = [(position, struct.unpack_from("<HH", payload), payload)
                    for position, tag, payload in original if tag == 0x0006]
        assert formulas, "fixture needs at least one xlwt.Formula"
        anchor_position, anchor, anchor_payload = formulas[0]
        coordinates = [coordinate for _, coordinate, _ in formulas]
        low_row, high_row = min(row for row, _ in coordinates), max(row for row, _ in coordinates)
        low_col, high_col = min(col for _, col in coordinates), max(col for _, col in coordinates)
        if range_kind:
            assert range_kind in {"shared", "array"}
            assert anchor == (low_row, low_col)
            assert len(set(coordinates)) == len(formulas), "only one formula worksheet per fixture"
            if shared_range is None:
                assert len(formulas) == (high_row - low_row + 1) * (high_col - low_col + 1)
            else:
                assert range_kind == "shared"
                low_row, high_row, low_col, high_col = shared_range
                assert all(low_row <= row <= high_row and low_col <= col <= high_col
                           for row, col in coordinates)
        if cache_kind == "number":
            cache = struct.pack("<d", cached)
        else:
            codes = {"string": 0, "boolean": 1, "error": 2, "empty": 3}
            assert cache_kind in codes
            scalar = int(cached) if cache_kind in {"boolean", "error"} else 0
            cache = bytes([codes[cache_kind], 0, scalar, 0, 0, 0, 255, 255])
        shared = None
        if range_kind:
            tokens = anchor_payload[22:22 + struct.unpack_from("<H", anchor_payload, 20)[0]]
            rectangle = struct.pack("<HHBB", low_row, high_row, low_col, high_col)
            shared = ((0x04BC, rectangle + bytes([0, len(formulas)]) + struct.pack("<H", len(tokens)) + tokens)
                      if range_kind == "shared" else
                      (0x0221, rectangle + bytes(6) + struct.pack("<H", len(tokens)) + tokens))
        rebuilt, offsets, new_records = bytearray(), {}, []
        for old_position, tag, payload in original:
            offsets[old_position] = len(rebuilt)
            formula_record = tag == 0x0006
            if formula_record:
                payload = bytearray(payload)
                payload[6:14] = cache
                if range_kind:
                    tokens = bytes([0x01]) + struct.pack("<HH", *anchor)
                    flags = struct.unpack_from("<H", payload, 14)[0]
                    struct.pack_into("<H", payload, 14, flags | (8 if range_kind == "shared" else 0))
                    payload = payload[:20] + struct.pack("<H", len(tokens)) + tokens
                tag = opcode
            new_records.append((len(rebuilt), tag))
            rebuilt.extend(struct.pack("<HH", tag, len(payload)) + payload)
            if old_position == anchor_position and shared is not None:
                shared_tag, shared_payload = shared
                rebuilt.extend(struct.pack("<HH", shared_tag, len(shared_payload)) + shared_payload)
            if formula_record and cache_kind == "string":
                text = str(cached).encode("utf-16le")
                payload = struct.pack("<HB", len(text) // 2, 1) + text
                rebuilt.extend(struct.pack("<HH", 0x0207, len(payload)) + payload)
        for position, tag in new_records:
            if tag == 0x0085:
                old_offset = struct.unpack_from("<I", rebuilt, position + 4)[0]
                struct.pack_into("<I", rebuilt, position + 4, offsets[old_offset])
        assert len(rebuilt) <= len(stream), "synthetic OLE stream lacks padding"
        stream[:] = rebuilt + bytes(len(stream) - len(rebuilt))
    return mutate_stream(raw, patch)


def sheet_types_workbook(raw, *, types=None, hidden=(), nested_chart=None):
    """Change metadata for dispatch/closure tests, without pretending to render charts."""
    types = types or {}

    def patch(stream):
        entries = list(records(stream))
        bounds = [position for position, tag, _ in entries if tag == 0x0085]
        for index, position in enumerate(bounds):
            offset = struct.unpack_from("<I", stream, position + 4)[0]
            if index in hidden:
                stream[position + 8] = 2
            kind = types.get(index, "worksheet")
            if kind in {"chart", "macro"}:
                stream[position + 9] = 2 if kind == "chart" else 1
                struct.pack_into("<H", stream, offset + 6, 0x0020 if kind == "chart" else 0x0040)
            elif kind == "dialog":
                wsbool = next(record_position for record_position, tag, _ in entries
                              if record_position >= offset and tag == 0x0081)
                bits = struct.unpack_from("<H", stream, wsbool + 4)[0]
                struct.pack_into("<H", stream, wsbool + 4, bits | 0x10)
            else:
                assert kind == "worksheet"
        if nested_chart is not None:
            assert nested_chart in {"closed", "unclosed"}
            first_sheet = struct.unpack_from("<I", stream, bounds[0] + 4)[0]
            enclosing_eof = next(position for position, tag, _ in entries
                                 if position > first_sheet and tag == 0x000A)
            bof = bytearray(stream[first_sheet:first_sheet + 20])
            struct.pack_into("<H", bof, 6, 0x0020)
            inserted = bof + (struct.pack("<HH", 0x000A, 0) if nested_chart == "closed" else b"")
            actual_end = entries[-1][0] + entries[-1][2] + 4
            assert actual_end + len(inserted) <= len(stream)
            stream[enclosing_eof + len(inserted):actual_end + len(inserted)] = stream[enclosing_eof:actual_end]
            stream[enclosing_eof:enclosing_eof + len(inserted)] = inserted
            for position in bounds:
                offset = struct.unpack_from("<I", stream, position + 4)[0]
                if offset > enclosing_eof:
                    struct.pack_into("<I", stream, position + 4, offset + len(inserted))
    return mutate_stream(raw, patch)


def read(raw):
    from app.table_xls import read_workbook
    return read_workbook(raw)


def test_xls_reader_retains_exact_text_types_formats_and_sheet_provenance():
    raw = workbook_bytes([
        ["date", "payer_account", "payee_account", "amount", "notes"],
        ["2026-01-01", "0001234567890123456789", (12345, "00000000"),
         123.45, "=this is ordinary text"],
        ["2026-01-02", 12345, "交易时间", 2, "  keep original whitespace  "],
    ], extra_sheets=[("Second", [["content"], ["SYN-SECOND"]])])
    sheets = read(raw)
    assert len(sheets) == 2
    first = sheets[0]
    assert (first["path"], first["name"], first["encoding"], first["delimiter"],
            first["size"]) == ("worksheets/0001", "Synthetic XLS", "xls", "", 0)
    assert first["sha256"] == hashlib.sha256(json.dumps([
        hashlib.sha256(raw).hexdigest(), 1, "Synthetic XLS"],
        ensure_ascii=False).encode()).hexdigest()
    assert first["rows"][1][1] == "0001234567890123456789"
    assert first["rows"][1][2].value == 12345
    assert first["rows"][1][2].number_format == "00000000"
    assert first["rows"][1][3].value == 123.45
    assert first["rows"][1][4] == "=this is ordinary text"
    assert first["rows"][2][1].value == 12345
    assert type(first["rows"][2][1].value) is int
    assert first["rows"][2][4] == "  keep original whitespace  "
    assert sheets[1]["path"] == "worksheets/0002"
    assert sheets[1]["rows"] == [["content"], ["SYN-SECOND"]]


@pytest.mark.parametrize("datemode", [0, 1])
def test_xls_reader_uses_declared_date_system_and_does_not_invent_time_dates(datemode):
    day = dt.datetime(2026, 2, 1)
    moment = dt.datetime(2026, 2, 1, 9, 15, 30)
    cells = read(workbook_bytes([[
        (day, "YYYY-MM-DD"), (moment, "YYYY-MM-DD hh:mm:ss"),
        (0.5, "hh:mm:ss"), (0, "hh:mm:ss"),
    ]], datemode=datemode))[0]["rows"][0]
    assert cells[0].value == day.date()
    assert cells[0].date_only
    assert cells[1].value == moment
    assert not cells[1].date_only
    assert cells[2].value == dt.time(12)
    assert cells[3].value == dt.time()


@pytest.mark.parametrize("number_format", ["mm", "mmmm"])
def test_xls_reader_preserves_full_dates_with_legal_month_only_formats(number_format):
    original = dt.datetime(2026, 2, 1, 9, 15, 30)
    value = read(workbook_bytes([[(original, number_format)]]))[0]["rows"][0][0]
    assert not value.invalid_type
    assert value.value == original
    assert not value.date_only


@pytest.mark.parametrize("serial", [60, 60.5])
def test_xls_reader_marks_nonexistent_1900_leap_day_as_invalid(serial):
    cells = read(workbook_bytes([[(59, "YYYY-MM-DD"),
                                 (serial, "YYYY-MM-DD hh:mm:ss"),
                                 (61, "YYYY-MM-DD")]]))[0]["rows"][0]
    assert cells[0].value == dt.date(1900, 2, 28)
    assert cells[1].invalid_type
    assert not cells[1].formula
    assert cells[2].value == dt.date(1900, 3, 1)


@pytest.mark.parametrize("serial", [0, 0.5])
@pytest.mark.parametrize("number_format", ["YYYY-MM-DD", "YYYY-MM-DD hh:mm:ss"])
def test_xls_reader_rejects_1900_day_zero_in_date_formats(serial, number_format):
    value = read(workbook_bytes([[(serial, number_format)]]))[0]["rows"][0][0]
    assert value.invalid_type


def test_xls_reader_accepts_1904_day_zero_without_reinterpreting_it():
    value = read(workbook_bytes([[(0, "YYYY-MM-DD")]], datemode=1))[0]["rows"][0][0]
    assert value.value == dt.date(1904, 1, 1)
    assert value.date_only and not value.invalid_type


@pytest.mark.parametrize("serial,number_format", [
    (-0.5, "hh:mm:ss"), (0.5, "[h]:mm:ss"),
    (0.5, "[m]"), (0.5, "[s]"),
])
def test_xls_reader_does_not_invent_times_from_negative_or_elapsed_values(serial, number_format):
    value = read(workbook_bytes([[(serial, number_format)]]))[0]["rows"][0][0]
    assert value.invalid_type


@pytest.mark.parametrize("serial,expected", [
    (1, dt.datetime(1900, 1, 1)),
    (1.5, dt.datetime(1900, 1, 1, 12)),
])
def test_xls_reader_preserves_encoded_dates_even_when_the_format_only_shows_a_clock(serial, expected):
    value = read(workbook_bytes([[(serial, "hh:mm:ss")]]))[0]["rows"][0][0]
    assert not value.invalid_type and not value.date_only
    assert value.value == expected


def test_xls_reader_marks_boolean_error_and_overflow_dates_as_invalid():
    book = xlwt.Workbook()
    sheet = book.add_sheet("Invalid types")
    sheet.write(0, 0, True)
    sheet.write(0, 1, 1e20, xlwt.easyxf(num_format_str="YYYY-MM-DD"))
    sheet.row(0).set_cell_error(2, "#DIV/0!")
    sheet.write(0, 3, 7)
    output = io.BytesIO()
    book.save(output)
    cells = read(output.getvalue())[0]["rows"][0]
    assert cells[0].invalid_type
    assert cells[1].invalid_type
    assert cells[2].invalid_type
    assert not cells[3].invalid_type


@pytest.mark.parametrize("cached", [3.0, -2.03, 0.0])
def test_xls_reader_marks_cached_numeric_formulas_without_executing_them(cached):
    raw = workbook_bytes([[xlwt.Formula("1+2"), "=1+2"]])
    cells = read(formula_workbook(raw, cached=cached))[0]["rows"][0]
    assert cells[0].formula
    assert cells[0].value == cached
    assert cells[1] == "=1+2"


@pytest.mark.parametrize("tag", [0x0206, 0x0406])
@pytest.mark.parametrize("cache_kind,cached", [("number", 3.0), ("string", "amount"), ("boolean", 1)])
def test_xls_reader_rejects_old_formula_opcode_in_biff8(tag, cache_kind, cached):
    raw = formula_workbook(workbook_bytes([[xlwt.Formula("1+2")]]),
                           cache_kind=cache_kind, cached=cached, opcode=tag)
    with pytest.raises(table_import.TableImportError):
        read(raw)


@pytest.mark.parametrize("range_kind", [None, "shared", "array"])
@pytest.mark.parametrize("cache_kind,cached", [
    ("number", 3.0), ("string", "amount"), ("empty", 0),
    ("boolean", 1), ("error", 7),
])
def test_xls_reader_marks_all_formula_kinds_and_cache_types(range_kind, cache_kind, cached):
    raw = workbook_bytes([["ordinary", xlwt.Formula("1+2")],
                          ["ordinary", xlwt.Formula("1+2")]],
                         extra_sheets=[("Following sheet", [["untouched"]])])
    sheets = read(formula_workbook(raw, range_kind=range_kind,
                                  cache_kind=cache_kind, cached=cached))
    assert all(row[1].formula for row in sheets[0]["rows"])
    assert all(row[0] == "ordinary" for row in sheets[0]["rows"])
    assert sheets[1]["rows"] == [["untouched"]]
    if cache_kind in {"boolean", "error"}:
        assert all(row[1].invalid_type for row in sheets[0]["rows"])


def test_xls_reader_keeps_empty_formula_as_a_source_coordinate():
    rows = read(formula_workbook(workbook_bytes([[xlwt.Formula("1+2")]]),
                                 cache_kind="empty"))[0]["rows"]
    assert len(rows) == len(rows[0]) == 1
    assert rows[0][0].formula


@pytest.mark.parametrize("top_left_formula", [False, True])
def test_xls_reader_preserves_literals_inside_sparse_shared_formula_ranges(top_left_formula):
    if top_left_formula:
        raw = workbook_bytes([[xlwt.Formula("1+2"), "literal", xlwt.Formula("1+2")]])
        bounds, literal_cells = (0, 0, 0, 2), [(0, 1)]
    else:
        raw = workbook_bytes([["literal", xlwt.Formula("1+2")],
                              ["literal", xlwt.Formula("1+2")]])
        bounds, literal_cells = (0, 1, 0, 1), [(0, 0), (1, 0)]
    rows = read(formula_workbook(raw, range_kind="shared", shared_range=bounds))[0]["rows"]
    assert all(rows[row][col] == "literal" for row, col in literal_cells)
    assert sum(isinstance(value, table_import._WorkbookValue) and value.formula
               for row in rows for value in row) == 2


@pytest.mark.parametrize("edit", [
    lambda stream: struct.pack_into("<H", stream, 4, 0x0500),
    lambda stream: struct.pack_into("<H", stream, 6, 0x0010),
    lambda stream: struct.pack_into("<H", stream, 2, 65535),
])
def test_xls_reader_rejects_old_or_malformed_global_bof(edit):
    with pytest.raises(table_import.TableImportError):
        read(mutate_stream(workbook_bytes([["synthetic"]]), edit))


@pytest.mark.parametrize("kind", ["offset", "duplicate_offset", "duplicate_name", "bof", "eof", "filepass"])
def test_xls_reader_rejects_unclosed_or_ambiguous_sheet_structure(kind):
    raw = workbook_bytes([["first"]], name="One", extra_sheets=[("Two", [["second"]])])

    def patch(stream):
        entries = list(records(stream))
        bounds = [(position, size) for position, tag, size in entries if tag == 0x0085]
        if kind == "offset":
            struct.pack_into("<I", stream, bounds[0][0] + 4, 1)
        elif kind == "duplicate_offset":
            stream[bounds[1][0] + 4:bounds[1][0] + 8] = stream[bounds[0][0] + 4:bounds[0][0] + 8]
        elif kind == "duplicate_name":
            stream[bounds[1][0] + 10:bounds[1][0] + 4 + bounds[1][1]] = stream[bounds[0][0] + 10:bounds[0][0] + 4 + bounds[0][1]]
        elif kind == "bof":
            position = struct.unpack_from("<I", stream, bounds[0][0] + 4)[0]
            struct.pack_into("<H", stream, position + 6, 0x0005)
        elif kind == "eof":
            position = [position for position, tag, _ in entries if tag == 0x000A][-1]
            struct.pack_into("<H", stream, position, 0xFFFF)
        else:
            position = next(position for position, tag, _ in entries if tag == 0x0042)
            struct.pack_into("<H", stream, position, 0x002F)

    with pytest.raises(table_import.TableImportError):
        read(mutate_stream(raw, patch))


@pytest.mark.parametrize("raw", [b"", b"<html><table></table></html>", b"PK\x03\x04fake xlsx", b"not a workbook"])
def test_xls_reader_rejects_non_ole_inputs(raw):
    with pytest.raises(table_import.TableImportError):
        read(raw)


def test_xls_reader_uses_case_insensitive_ole_stream_identity():
    raw = workbook_bytes([["ordinary"]])
    name = "Workbook".encode("utf-16le")
    assert raw.count(name) == 1
    lower = raw.replace(name, "workbook".encode("utf-16le"))
    assert read(lower)[0]["rows"] == [["ordinary"]]


def test_xls_reader_checks_actual_limits_and_ignores_formatted_empty_cells(monkeypatch):
    raw = workbook_bytes([["real"], [], [], [(None, "00000000")]])
    assert read(raw)[0]["rows"] == [["real"]]
    monkeypatch.setattr(table_import, "MAX_COLUMNS", 1)
    with pytest.raises(table_import.TableImportError):
        read(workbook_bytes([["a", "b"]]))


def test_xls_reader_does_not_expand_high_formatted_empty_coordinates():
    book = xlwt.Workbook()
    sheet = book.add_sheet("Sparse formatting")
    sheet.write(0, 0, "ordinary")
    sheet.write(65535, 255, None, xlwt.easyxf(num_format_str="00000000"))
    output = io.BytesIO()
    book.save(output)
    assert read(output.getvalue())[0]["rows"] == [["ordinary"]]


def test_xls_reader_checks_total_rows_across_sheets(monkeypatch):
    monkeypatch.setattr(table_import, "MAX_RECORDS", 3)
    with pytest.raises(table_import.TableImportError):
        read(workbook_bytes([["a"], ["b"]], extra_sheets=[("More", [["c"], ["d"]])]))


def test_xls_reader_checks_sheet_and_field_limits(monkeypatch):
    raw = workbook_bytes([["first"]], extra_sheets=[("Second", [["second"]])])
    monkeypatch.setattr(table_import, "MAX_SHEETS", 1)
    with pytest.raises(table_import.TableImportError):
        read(raw)
    monkeypatch.setattr(table_import, "MAX_SHEETS", 256)
    monkeypatch.setattr(table_import, "MAX_FIELD", 3)
    with pytest.raises(table_import.TableImportError):
        read(workbook_bytes([["four"]]))


@pytest.mark.parametrize("sheet_type", ["chart", "macro", "dialog"])
def test_xls_reader_closes_but_does_not_import_nonworksheet_substreams(sheet_type):
    # This is a metadata/dispatch specimen, not a claim that the transformed
    # chart/macro can be rendered or executed by Office.
    raw = workbook_bytes([["ordinary"]], extra_sheets=[("Non transaction", [["must not import"]])])

    sheets = read(sheet_types_workbook(raw, types={1: sheet_type}))
    assert len(sheets) == 1 and sheets[0]["rows"] == [["ordinary"]]


def test_xls_reader_reads_hidden_normal_worksheets():
    raw = workbook_bytes([["hidden but ordinary"]])
    assert read(sheet_types_workbook(raw, hidden=(0,)))[0]["rows"] == [["hidden but ordinary"]]


@pytest.mark.parametrize("sheet_type", ["chart", "macro", "dialog"])
def test_xls_reader_refuses_a_workbook_without_supported_worksheets(sheet_type):
    with pytest.raises(table_import.TableImportError, match="普通工作表"):
        read(sheet_types_workbook(workbook_bytes([["not a transaction worksheet"]]),
                                 types={0: sheet_type}))


def test_xls_reader_closes_embedded_chart_before_its_enclosing_worksheet():
    raw = workbook_bytes([["ordinary"]], extra_sheets=[("Following", [["also ordinary"]])])
    sheets = read(sheet_types_workbook(raw, nested_chart="closed"))
    assert [sheet["rows"] for sheet in sheets] == [[["ordinary"]], [["also ordinary"]]]
    with pytest.raises(table_import.TableImportError):
        read(sheet_types_workbook(raw, nested_chart="unclosed"))


@pytest.mark.parametrize("sheet_type", [6, 3])
def test_xls_reader_rejects_old_vba_and_unknown_boundsheet_types(sheet_type):
    def patch(stream):
        position = next(position for position, tag, _ in records(stream) if tag == 0x0085)
        stream[position + 9] = sheet_type

    with pytest.raises(table_import.TableImportError):
        read(mutate_stream(workbook_bytes([["ordinary"]]), patch))


@pytest.mark.parametrize("missing", [False, True])
def test_xls_reader_requires_unique_wsbool_type_evidence(missing):
    def patch(stream):
        first = next(position for position, tag, _ in records(stream) if tag == 0x0081)
        if missing:
            struct.pack_into("<H", stream, first, 0xFFFF)
        else:
            # Another two-byte sheet record becomes a contradictory second WsBool.
            later = next(position for position, tag, size in records(stream)
                         if position > first and size == 2)
            struct.pack_into("<H", stream, later, 0x0081)
    with pytest.raises(table_import.TableImportError):
        read(mutate_stream(workbook_bytes([["ordinary"]]), patch))


@pytest.mark.parametrize("range_kind", ["shared", "array"])
@pytest.mark.parametrize("defect", ["reversed_rows", "reversed_columns", "length", "missing_anchor"])
def test_xls_reader_rejects_damaged_formula_range_metadata(range_kind, defect):
    raw = formula_workbook(workbook_bytes([[xlwt.Formula("1+2")],
                                           [xlwt.Formula("1+2")]]), range_kind=range_kind)

    def patch(stream):
        position = next(position for position, tag, _ in records(stream)
                        if tag == (0x04BC if range_kind == "shared" else 0x0221))
        if defect == "reversed_rows":
            struct.pack_into("<HH", stream, position + 4, 2, 1)
        elif defect == "reversed_columns":
            stream[position + 8:position + 10] = bytes([2, 1])
        elif defect == "length":
            struct.pack_into("<H", stream, position + 4 + (8 if range_kind == "shared" else 12), 65535)
        else:
            struct.pack_into("<HH", stream, position + 4, 1, 2)
            stream[position + 8:position + 10] = bytes([1, 1])

    with pytest.raises(table_import.TableImportError):
        read(mutate_stream(raw, patch))


def test_xls_reader_requires_formula_for_every_declared_array_coordinate():
    raw = formula_workbook(workbook_bytes([[xlwt.Formula("1+2")],
                                           [xlwt.Formula("1+2")]]), range_kind="array")

    def patch(stream):
        position = next(position for position, tag, _ in records(stream) if tag == 0x0221)
        struct.pack_into("<H", stream, position + 6, 2)

    with pytest.raises(table_import.TableImportError, match="完整范围"):
        read(mutate_stream(raw, patch))


def test_xls_reader_checks_shared_formula_declared_use_count():
    raw = formula_workbook(workbook_bytes([[xlwt.Formula("1+2")],
                                           [xlwt.Formula("1+2")]]), range_kind="shared")

    def patch(stream):
        position = next(position for position, tag, _ in records(stream) if tag == 0x04BC)
        stream[position + 11] = 1

    with pytest.raises(table_import.TableImportError, match="使用者数量"):
        read(mutate_stream(raw, patch))


def test_xls_reader_unloads_each_sheet_and_releases_resources_after_success_and_failure(monkeypatch):
    import xlrd

    original_open = xlrd.open_workbook
    observed = []

    def open_book(*args, **kwargs):
        book = original_open(*args, **kwargs)
        state = {"unloaded": [], "released": False, "ragged": kwargs.get("ragged_rows")}
        observed.append(state)
        original_unload, original_release = book.unload_sheet, book.release_resources

        def unload(name):
            state["unloaded"].append(name)
            original_unload(name)

        def release():
            state["released"] = True
            original_release()

        book.unload_sheet, book.release_resources = unload, release
        return book

    monkeypatch.setattr(xlrd, "open_workbook", open_book)
    raw = workbook_bytes([["first"]], extra_sheets=[("Second", [["second"]])])
    assert len(read(raw)) == 2
    assert observed[-1] == {"unloaded": ["Synthetic XLS", "Second"], "released": True, "ragged": True}
    monkeypatch.setattr(table_import, "MAX_FIELD", 3)
    with pytest.raises(table_import.TableImportError):
        read(raw)
    assert observed[-1] == {"unloaded": ["Synthetic XLS"], "released": True, "ragged": True}


def test_xls_reader_keeps_biff8_last_legal_row_without_truncation():
    book = xlwt.Workbook()
    sheet = book.add_sheet("Last legal row")
    sheet.write(0, 0, "first")
    sheet.write(65535, 0, "last")
    output = io.BytesIO()
    book.save(output)
    rows = read(output.getvalue())[0]["rows"]
    assert len(rows) == 65536
    assert rows[0] == ["first"]
    assert rows[-1] == ["last"]
