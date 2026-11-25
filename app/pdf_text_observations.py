"""Retain exact, independently visible PDF fields without asserting a ledger."""
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import re

from . import pdf_text_import, table_cancellations, table_import
from .pdf_import import MEMBER_PATH


PROFILE = "pdf-text-observation-v1"
PAGE_PROFILE = "pdf-text-observation-v2"
PROFILES = (PROFILE, PAGE_PROFILE)
ROW_KIND = "pdf_observation_record"
ROLE = "pdf_observation"
REASON = "pdf_book_scope_unproven"
PAGE_REASON = "pdf_page_evidence_conflict"
ROW_REASON = "pdf_row_evidence_conflict"
COPY_REASON = "pdf_repeated_page"
DISPOSITIONS = {REASON: "unresolved", PAGE_REASON: "unresolved", ROW_REASON: "unresolved",
                COPY_REASON: "non_transaction"}
NOTICE = "已核对原页的日期、付款方、收款方、币种和金额。请补充账簿归属及 Balance 列口径后再核定入账。"
_NOTICES = {
    REASON: NOTICE,
    PAGE_REASON: "本页的可见文字、页序或作用范围未能核实。请打开原页核对表头、币种及续页或复制说明。",
    ROW_REASON: "该行文字或列位置未能核实。请在原页按行框核对付款方、收款方和金额。",
    COPY_REASON: "原文声明这是第一页的文档复制，所示内容与原页一致。可打开两页核对。",
}
MAX_BYTES = pdf_text_import.MAX_BYTES
MAX_ROWS = pdf_text_import.MAX_ROWS
MAX_RECORD_BYTES = 2048
_TITLES = frozenset({"transaction summary", "transaction statement", "transaction report", "transaction list",
                     "transaction details", "bank transaction statement", "transfer statement",
                     "synthetic test document"})
_DISCLOSURE = "Fictional example - no real accounts or identities."
_FOOTERS = frozenset({"End of statement.", "End of this single-page synthetic statement."})
_CURRENCY = r"[A-Z]{3}"
_HEADER_FIELDS = rf"(Payer) +(Payee) +(Amount(?: {_CURRENCY})?) +(Balance)"
_HEADER = re.compile(rf"(Date and time|Transaction time) +{_HEADER_FIELDS}")
_PAGE_HEADER = re.compile(rf"(Date/time|Date and time|Transaction time) +{_HEADER_FIELDS}")
_PAGE_TITLES = _TITLES | {"statement", "account statement", "synthetic statement"}
_PAGE_TITLE = re.compile(r"(.+?)( - NO REAL IDENTITIES| - PAGE COPY| CONTINUED - PAGE (2))?", re.IGNORECASE)
_COPY_DECLARATION = "The second page repeats the first as a document copy."
_DECLARATION = re.compile(rf"Currency: +({_CURRENCY})(?: +Opening balance: +-?{pdf_text_import._MONEY})?")
_ROW = re.compile(rf"({pdf_text_import._CLOCK}) +({pdf_text_import._ACCOUNT}) +({pdf_text_import._ACCOUNT})"
                  rf" +({pdf_text_import._MONEY}) +(-?{pdf_text_import._MONEY})")
_ROW_START = re.compile(rf"{pdf_text_import._CLOCK} +")
_CONTEXT_FIELDS = ("title", "header", "header_boxes", "declaration", "currency_box", "page_size")
_PAGE_CONTEXT_FIELDS = (*_CONTEXT_FIELDS, "title_box", "copy_declaration", "copy_declaration_box")
_JSON_FIELDS = frozenset({"header", "header_boxes", "currency_box", "page_size", "title_box", "copy_declaration_box"})
DETAILS_FIELDS = frozenset({"reason", "source_role", "pdf_page", "pdf_table", "pdf_row", "pdf_text_currency",
                            "pdf_text_original", "pdf_text_boxes", "pdf_text_observation_profile", "pdf_text_readers",
                            "pdf_text_line", "pdf_text_line_box", "pdf_text_page_count",
                            "pdf_text_copy_of_page", "pdf_text_copy_of_row",
                            *(f"pdf_text_{field}" for field in _PAGE_CONTEXT_FIELDS)})


def valid_source(filename, size, mode, mapping, sheet, row_kind):
    return (isinstance(filename, str) and Path(filename).suffix.lower() == ".pdf"
            and type(size) is int and 0 < size <= MAX_BYTES and mode == "B"
            and mapping == {} and sheet == "" and row_kind == ROW_KIND)


def _line(fields):
    return " ".join(value for value, _ in fields)


def _bounds(points):
    return [points[0][0], min(box[1] for box in points), points[-1][2], max(box[3] for box in points)]


def _field_boxes(words, fields):
    # The words already agree across both readers. Retain the enclosing box
    # of each field, even when the entire table line is one PDF text run.
    boxes, offset = [], 0
    for field in fields:
        tokens = field.split()
        field_words = words[offset:offset + len(tokens)]
        if [text for text, _ in field_words] != tokens:
            raise ValueError("PDF field words disagree")
        boxes.append(_bounds([box for _, box in field_words]))
        offset += len(tokens)
    if offset != len(words):
        raise ValueError("unassigned words in PDF field row")
    return boxes


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _box(values, width, height):
    return (isinstance(values, list) and len(values) == 4 and all(map(_number, values))
            and 0 <= values[0] < values[2] <= width and 0 <= values[1] < values[3] <= height)


def _currency(context, pattern):
    width, height = context["page_size"]
    header, declaration = context["header"], context["declaration"]
    heading = pattern.fullmatch(" ".join(header))
    if (heading is None or header != list(heading.groups()) or not _number(width) or not _number(height)
            or not 200 <= width <= 1600 or not 180 <= height <= 1600):
        raise ValueError("invalid PDF source observation context")
    declared = _DECLARATION.fullmatch(declaration) if declaration else None
    currency = header[3].removeprefix("Amount ") if header[3] != "Amount" else None
    if declared:
        page_currency = declared[1]
        if currency is not None and page_currency != currency:
            raise ValueError("conflicting PDF currency declarations")
        currency = page_currency
    if (not currency or declaration and not declared
            or not _box(context["currency_box"], width, height)):
        raise ValueError("PDF source currency is not explicitly located")
    _validate_columns(context, [])
    code_box = context["currency_box"]
    headers = context["header_boxes"]
    if (declared and code_box[3] > min(box[1] for box in headers)
            or not declared and not _contains(headers[3], code_box)):
        raise ValueError("PDF currency is outside its declared position")
    return currency


def _contains(outer, inner):
    return (outer[0] <= inner[0] < inner[2] <= outer[2]
            and outer[1] <= inner[1] < inner[3] <= outer[3])


def _validate_columns(context, originals):
    width, height = context["page_size"]
    columns = [context["header_boxes"], *(boxes for _, boxes in originals)]
    previous_y = 0
    for boxes in columns:
        if (not isinstance(boxes, list) or len(boxes) != 5
                or any(not _box(box, width, height) for box in boxes)
                or min(box[1] for box in boxes) < previous_y):
            raise ValueError("invalid PDF observation row geometry")
        previous_y = max(box[3] for box in boxes)
    if any(max(boxes[column][2] for boxes in columns) > min(boxes[column + 1][0] for boxes in columns)
           for column in range(4)) or any(
        max(head[0], cell[0]) >= min(head[2], cell[2])
        for boxes in columns[1:] for head, cell in zip(columns[0], boxes, strict=True)
    ):
        raise ValueError("PDF observation columns cannot be located uniquely")


def _validate_values(values):
    match = _ROW.fullmatch(" ".join(values))
    if match is None or values != list(match.groups()) or Decimal(values[3]) <= 0:
        raise ValueError("invalid PDF source observation row")
    datetime.fromisoformat(values[0])


def _context_evidence(context):
    return {f"pdf_text_{field}": json.dumps(value) if field in _JSON_FIELDS else value
            for field, value in context.items()}


def _record(path, digest, number, reason, details):
    return {"record_no": number, "member_path": path, "source_row": number,
            "record_key": table_import.record_key(path, digest, number),
            "disposition": DISPOSITIONS[reason], "reason_code": reason, "details": details}


def _observation_plan(context, originals, digest, size, path, archive):
    if context["title"].lower() not in _TITLES or not 1 <= len(originals) <= MAX_ROWS:
        raise ValueError("invalid PDF source observation context")
    currency = _currency(context, _HEADER)
    _validate_columns(context, originals)
    evidence = _context_evidence(context)
    records = []
    for number, (values, boxes) in enumerate(originals, 1):
        _validate_values(values)
        details = {**evidence, "pdf_page": "1", "pdf_table": "1", "pdf_row": str(number),
                   "pdf_text_original": json.dumps(values), "pdf_text_boxes": json.dumps(boxes),
                   "pdf_text_currency": currency, "pdf_text_observation_profile": PROFILE,
                   "pdf_text_readers": "pypdf+pdftotext",
                   "source_role": ROLE, "reason": NOTICE}
        records.append(_record(path, digest, number, REASON, details))
    return _source_plan(records, digest, size, path, archive, PROFILE, 1, 1)


def _source_plan(records, digest, size, path, archive, profile, total_pages, candidate_pages):
    count = len(records)
    # Keep the existing financial archive family's physical attachment role.
    member = {"member_path": path, "role": "attachment" if archive else "pdf", "sha256": digest,
              "size_bytes": size, "encoding": "binary" if archive else "pdf", "row_count": count}
    document = {"member_path": path, "role": ROLE, "columns": [], "row_count": count,
                "pdf_total_pages": total_pages, "pdf_candidate_pages": candidate_pages, "pdf_status": REASON}
    plan = {"profile": profile, "rows": [], "records": records, "members": [member], "documents": [document],
            "columns": [], "mapping_evidence": [], "sheet": "", "mode": "B", "mapping": {}}
    table_cancellations.refresh_stats(plan)
    return plan


def _read_table(page, *, extended):
    width, height, _, lines, words = page
    index = 2 if _line(lines[1]) == _DISCLOSURE else 1
    copy_declaration, copy_box = "", None
    if extended and _line(lines[index]) == _COPY_DECLARATION:
        copy_declaration = _COPY_DECLARATION
        copy_box = _bounds([box for _, box in words[index]])
        index += 1
    declaration = _line(lines[index])
    if declared := _DECLARATION.fullmatch(declaration):
        code_box = words[index][1][1]
        index += 1
    else:
        declaration = ""
    pattern = _PAGE_HEADER if extended else _HEADER
    heading = pattern.fullmatch(_line(lines[index]))
    if heading is None or not declared and heading[4] == "Amount":
        raise ValueError("PDF observations require a known header and visible currency")
    header = list(heading.groups())
    if not declared:
        code_box = words[index][-2][1]
    context = {"title": _line(lines[0]), "header": header, "header_boxes": _field_boxes(words[index], header),
               "declaration": declaration, "currency_box": code_box, "page_size": [width, height]}
    if extended:
        context.update(title_box=_bounds([box for _, box in words[0]]),
                       copy_declaration=copy_declaration, copy_declaration_box=copy_box)
    last = len(lines) - (_line(lines[-1]) in _FOOTERS)
    return context, zip(lines[index + 1:last], words[index + 1:last], strict=True)


def _legacy_plan(page, digest, size, path, archive):
    if page is None:
        return None
    try:
        context, lines = _read_table(page, extended=False)
        originals = []
        for fields, words in lines:
            match = _ROW.fullmatch(_line(fields))
            if match is None:
                return None
            values = list(match.groups())
            originals.append((values, _field_boxes(words, values)))
        return _observation_plan(context, originals, digest, size, path, archive)
    except (ValueError, IndexError, ArithmeticError):
        return None


def _page_context(context, number, total):
    title = _PAGE_TITLE.fullmatch(context["title"])
    copy_text = context["copy_declaration"]
    copy_box = context["copy_declaration_box"]
    width, height = context["page_size"]
    title_box = context["title_box"]
    if (title is None or title[1].lower() not in _PAGE_TITLES
            or title[3] and number != int(title[3])
            or not _box(title_box, width, height)
            or (title[2] or "").lower() == " - page copy" and not copy_text):
        raise ValueError("PDF page scope cannot be located")
    currency = _currency(context, _PAGE_HEADER)
    header_top = min(box[1] for box in context["header_boxes"])
    after_title = context["currency_box"][1] if context["declaration"] else header_top
    if (title_box[3] > after_title
            or copy_text and (total != 2 or title[3] or copy_text != _COPY_DECLARATION
                              or not _box(copy_box, width, height)
                              or not title_box[3] <= copy_box[1] < copy_box[3] <= after_title)
            or not copy_text and copy_box is not None):
        raise ValueError("PDF page declarations are not independently located")
    return title[1].lower(), bool(title[3]), currency


def _read_observation_page(page, number, total):
    if page is None:
        return None
    try:
        context, lines = _read_table(page, extended=True)
        _page_context(context, number, total)
        rows = []
        for fields, words in lines:
            line = _line(fields)
            if not _ROW_START.match(line) or len(line) > 300:
                # Outside the known table, words can change the meaning of
                # the whole page, for example a cancellation or scope note.
                return None
            try:
                match = _ROW.fullmatch(line)
                if match is None:
                    raise ValueError("unreadable row fields")
                values = list(match.groups())
                boxes = _field_boxes(words, values)
                _validate_values(values)
                _validate_columns(context, [(values, boxes)])
                rows.append((values, boxes))
            except (ValueError, ArithmeticError):
                rows.append((line, _bounds([box for _, box in words])))
        return (context, rows) if rows else None
    except (ValueError, IndexError, ArithmeticError):
        return None


def _rows_match(first, second):
    return (len(first) == len(second)
            and all(isinstance(left, list) and left == right
                    for (left, _), (right, _) in zip(first, second, strict=True)))


def _page_plan(pages, digest, size, path, archive):
    total = len(pages)
    if (not 1 <= total <= pdf_text_import.MAX_PAGES
            or not any(page and (page[0]["header"][0] == "Date/time"
                                or page[0]["copy_declaration"] or " CONTINUED - PAGE " in page[0]["title"].upper())
                       for page in pages)):
        raise ValueError("missing page-level PDF contract")
    first_context, first_rows = pages[0] or (None, [])
    records, candidates = [], set()
    first_values = {tuple(values) for values, _ in first_rows if isinstance(values, list)}
    first_title = _page_context(first_context, 1, total) if first_context else None
    for number, page in enumerate(pages, 1):
        base = {"pdf_page": str(number), "pdf_text_page_count": str(total),
                "pdf_text_observation_profile": PAGE_PROFILE, "source_role": ROLE}
        if page is None:
            records.append(_record(path, digest, len(records) + 1, PAGE_REASON,
                                   {**base, "reason": _NOTICES[PAGE_REASON]}))
            continue
        context, rows = page
        title, continued, currency = _page_context(context, number, total)
        width, height = context["page_size"]
        header_bottom = max(box[3] for box in context["header_boxes"])
        if not rows:
            raise ValueError("empty PDF observation page")
        copy = conflict = comparable = False
        if number == 2 and first_context:
            comparable = (title == first_title[0] and currency == first_title[2]
                          and context["header"] == first_context["header"])
            first_copy, current_copy = first_context["copy_declaration"], context["copy_declaration"]
            if first_copy or current_copy:
                copy = (first_copy == current_copy and comparable
                        and context["declaration"] == first_context["declaration"] and _rows_match(first_rows, rows))
                conflict = not copy
            elif continued:
                conflict = not comparable
        elif number == 2:
            conflict = bool(context["copy_declaration"])

        evidence = {**base, **_context_evidence(context), "pdf_text_readers": "pypdf+pdftotext",
                    "pdf_table": "1", "pdf_text_currency": currency}
        last_y = header_bottom
        for row, (values, boxes) in enumerate(rows, 1):
            fields = {**evidence, "pdf_row": str(row)}
            if isinstance(values, list):
                _validate_values(values)
                _validate_columns(context, [(values, boxes)])
                if min(box[1] for box in boxes) < last_y:
                    raise ValueError("PDF page lines overlap")
                last_y = max(box[3] for box in boxes)
                fields.update(pdf_text_original=json.dumps(values), pdf_text_boxes=json.dumps(boxes))
                if copy:
                    reason = COPY_REASON
                    fields.update(pdf_text_copy_of_page="1", pdf_text_copy_of_row=str(row))
                elif conflict or number == 2 and comparable and tuple(values) in first_values:
                    reason = PAGE_REASON
                else:
                    reason = REASON
                if reason in {REASON, COPY_REASON}:
                    candidates.add(number)
            else:
                if (not isinstance(values, str) or not 0 < len(values) <= 300 or not _ROW_START.match(values)
                        or not _box(boxes, width, height) or boxes[1] < last_y):
                    raise ValueError("invalid unresolved PDF line")
                last_y = boxes[3]
                reason = ROW_REASON
                fields.update(pdf_text_line=values, pdf_text_line_box=json.dumps(boxes))
            fields["reason"] = _NOTICES[reason]
            records.append(_record(path, digest, len(records) + 1, reason, fields))
    if not 1 <= len(records) <= MAX_ROWS or any(
        len(json.dumps(record["details"], ensure_ascii=False).encode()) > MAX_RECORD_BYTES for record in records
    ):
        raise ValueError("PDF page observation budget exceeded")
    return _source_plan(records, digest, size, path, archive, PAGE_PROFILE, total, len(candidates))


def plan(pages, digest, size, *, path=MEMBER_PATH, archive=False, profile=None):
    if pages is None:
        return None
    if profile in {None, PROFILE}:
        legacy = _legacy_plan(pages[0], digest, size, path, archive) if len(pages) == 1 else None
        if legacy is not None or profile == PROFILE:
            return legacy
    try:
        observed = [_read_observation_page(page, number, len(pages)) for number, page in enumerate(pages, 1)]
        return _page_plan(observed, digest, size, path, archive)
    except (ValueError, IndexError, ArithmeticError):
        return None


def archive_plan(payload, path):
    size, digest = len(payload), sha256(payload).hexdigest()
    return plan(pdf_text_import.read_pages(BytesIO(payload), size, digest), digest, size, path=path, archive=True)


def _restore_context(details, fields):
    return {field: json.loads(details[f"pdf_text_{field}"]) if field in _JSON_FIELDS else details[f"pdf_text_{field}"]
            for field in fields}


def _replay_pages(details, digest, size, path, archive):
    count = int(details[0]["pdf_text_page_count"])
    if not 1 <= count <= pdf_text_import.MAX_PAGES:
        raise ValueError("invalid PDF page count")
    pages = [[] for _ in range(count)]
    for record in details:
        page = int(record["pdf_page"])
        if not 1 <= page <= count:
            raise ValueError("invalid PDF source page")
        pages[page - 1].append(record)
    observed = []
    for rows in pages:
        if not rows:
            raise ValueError("missing PDF page receipt")
        if "pdf_text_title" not in rows[0]:
            observed.append(None)
            continue
        context = _restore_context(rows[0], _PAGE_CONTEXT_FIELDS)
        originals = [
            (json.loads(row["pdf_text_original"]), json.loads(row["pdf_text_boxes"])) if "pdf_text_original" in row
            else (row["pdf_text_line"], json.loads(row["pdf_text_line_box"])) for row in rows
        ]
        observed.append((context, originals))
    return _page_plan(observed, digest, size, path, archive)


def replay_journal(details, digest, size, path, *, archive=False, profile=None):
    """Validate frozen observations without inventing a recovered original."""
    if not 1 <= len(details) <= MAX_ROWS:
        raise ValueError("invalid PDF source observation count")
    profile = profile or details[0]["pdf_text_observation_profile"]
    if profile == PAGE_PROFILE:
        result = _replay_pages(details, digest, size, path, archive)
    elif profile == PROFILE:
        context = _restore_context(details[0], _CONTEXT_FIELDS)
        originals = [(json.loads(row["pdf_text_original"]), json.loads(row["pdf_text_boxes"])) for row in details]
        result = _observation_plan(context, originals, digest, size, path, archive)
    else:
        raise ValueError("invalid PDF source observation profile")
    if any(row != record["details"] for row, record in zip(details, result["records"], strict=True)):
        raise ValueError("invalid PDF source observation receipt")
    return result


def original_cells(details):
    if "pdf_text_original" not in details:
        return None
    labels, values = json.loads(details["pdf_text_header"]), json.loads(details["pdf_text_original"])
    # Expose the page's labels and text without mapping endpoints or Balance
    # to the account ledger's fields.
    cells = [*zip(labels, values, strict=True), ("Currency", details["pdf_text_currency"])]
    return [{"column": number, "header": label, "value": value, "fields": [], "kind": "text"}
            for number, (label, value) in enumerate(cells, 1)]
