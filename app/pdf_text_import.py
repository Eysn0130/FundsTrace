"""Verify bounded text pages and admit the closed, single-page ledger."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from . import table_cancellations, table_import
from .pdf_import import MEMBER_PATH


PROFILE = "pdf-text-ledger-v1"
ROW_KIND = "pdf_text_record"
MAX_BYTES = 64 * 1024
MAX_PAGES = 2
MAX_ROWS = 16
PYPDF_VERSION = "6.21.4"
_TITLES = {"Posted Account Statement"}
_LABELS = ("Transaction time", "Payer", "Payee", "Amount CNY", "Book balance")
_FONTS = {"/Helvetica", "/Helvetica-Bold", "/Courier", "/Courier-Bold", "/Times-Roman"}
_ACCOUNT = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,23}"
_MONEY = r"(?:0|[1-9][0-9]{0,11})\.[0-9]{2}"
_CLOCK = r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}"
_NOTE = "Balance column is the posted book balance. Exactly "


def valid_source(filename, size, mode, mapping, sheet, row_kind):
    return (isinstance(filename, str) and Path(filename).suffix.lower() == ".pdf"
            and type(size) is int and 0 < size <= MAX_BYTES and mode == "A"
            and mapping == {} and sheet == "" and row_kind == ROW_KIND)


def _page_tree(data, reader):
    book = reader(BytesIO(data), strict=True, root_object_recovery_limit=0, xref_stream_maximum_entries=128)
    if book.is_encrypted or not set(book.root_object) <= {"/Type", "/Pages", "/PageMode", "/PageLayout", "/Lang", "/Metadata"}:
        raise ValueError("unsupported PDF document")
    tree = book.root_object["/Pages"]
    kids = tree["/Kids"]
    if (not 1 <= len(kids) <= MAX_PAGES or tree["/Count"] != len(kids)
            or any(child.get_object()["/Type"] != "/Page" for child in kids)):
        raise ValueError("unsupported PDF page tree")
    return book.pages


def _plain_page(page):
    if (not set(page) <= {"/Type", "/Parent", "/Contents", "/Rotate", "/Resources", "/MediaBox", "/CropBox", "/Trans", "/UserUnit"}
            or page.rotation or page.get("/Trans") or page.get("/UserUnit", 1) != 1
            or page.mediabox != page.cropbox or page.mediabox[0] != 0 or page.mediabox[1] != 0):
        raise ValueError("unsupported PDF page geometry")
    width, height = float(page.mediabox.width), float(page.mediabox.height)
    if not (200 <= width <= 1600 and 180 <= height <= 1600):
        raise ValueError("unsupported PDF page size")
    resources = page["/Resources"]
    if not set(resources) <= {"/Font", "/ProcSet"}:
        raise ValueError("PDF resources may alter visible content")
    fonts = resources["/Font"]
    if not 1 <= len(fonts) <= 8:
        raise ValueError("unsupported PDF fonts")
    for entry in fonts.values():
        font = entry.get_object()
        if (not set(font) <= {"/Type", "/Subtype", "/BaseFont", "/Encoding", "/Name"}
                or font.get("/Type") != "/Font" or font.get("/Subtype") != "/Type1"
                or font.get("/BaseFont") not in _FONTS
                or font.get("/Encoding", "/WinAnsiEncoding") != "/WinAnsiEncoding"):
            raise ValueError("unverified PDF font encoding")
    contents = page["/Contents"]
    filters = contents.get("/Filter", [])
    if isinstance(filters, str):
        filters = [filters]
    if (not set(contents) <= {"/Filter"} or list(filters) not in ([], ["/FlateDecode"], ["/ASCII85Decode", "/FlateDecode"])
            or not hasattr(contents, "get_data") or len(contents.get_data()) > MAX_BYTES):
        raise ValueError("unsupported PDF content stream")
    groups, occupied = defaultdict(list), False
    font_size, origin = None, None
    for args, operator in page.get_contents().operations:
        if operator == b"cm" and args == [1, 0, 0, 1, 0, 0]:
            continue
        if operator == b"BT" and not args and not occupied:
            occupied, origin = True, None
        elif operator == b"ET" and not args and occupied:
            occupied, origin = False, None
        elif operator == b"Tf" and occupied and len(args) == 2 and args[0] in fonts and 8 <= args[1] <= 24:
            font_size = args[1]
        elif operator == b"TL" and occupied and len(args) == 1 and 0 <= args[0] <= 50:
            continue
        elif operator == b"T*" and occupied and not args:
            origin = None
        elif operator == b"Tm" and occupied and len(args) == 6 and args[:4] == [1, 0, 0, 1]:
            origin = (float(args[4]), height - float(args[5]))
        elif (operator == b"Tj" and occupied and origin is not None and font_size is not None
              and len(args) == 1 and isinstance(args[0], str) and re.fullmatch(r"[ -~]{1,120}", args[0])
              and args[0].strip() == args[0]):
            x, y = origin
            if not 0 <= x < width or not 0 < y < height:
                raise ValueError("PDF text beyond page")
            groups[y].append((x, args[0]))
            origin = None
        else:
            raise ValueError("unverified PDF painting or text state")
    if occupied or not 3 <= len(groups) <= MAX_ROWS + 5:
        raise ValueError("unverified PDF text inventory")
    return width, height, sorted((y, sorted(values)) for y, values in groups.items())


def _bbox(source, executable, number, width, height, groups):
    # Structural admission above excludes arbitrary painting, external content,
    # hidden text and custom glyph maps before this independent layout read.
    result = subprocess.run([executable, "-f", str(number), "-l", str(number),
                             "-bbox-layout", "-enc", "UTF-8", str(source), "-"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=8, check=True)
    if len(result.stdout) > 192 * 1024:
        raise ValueError("PDF text layout too large")
    pages = ET.fromstring(result.stdout).findall(".//{*}page")
    if len(pages) != 1 or abs(float(pages[0].attrib["width"]) - width) > .01 or abs(
        float(pages[0].attrib["height"]) - height
    ) > .01:
        raise ValueError("PDF layout reader did not see the same page")
    words = []
    for word in pages[0].findall(".//{*}word"):
        box = tuple(float(word.attrib[key]) for key in ("xMin", "yMin", "xMax", "yMax"))
        if not 0 <= box[0] < box[2] <= width or not 0 <= box[1] < box[3] <= height or not word.text:
            raise ValueError("PDF word outside visible page")
        words.append((box, word.text))
    lines, word_lines = [], []
    used = set()
    for y, runs in groups:
        matched = sorted((box, index, text) for index, (box, text) in enumerate(words) if box[1] <= y <= box[3])
        if any(index in used for _, index, _ in matched) or any(
            left[0][2] > right[0][0] + .01 for left, right in zip(matched, matched[1:])
        ):
            raise ValueError("PDF text runs overlap")
        used.update(index for _, index, _ in matched)
        fields, index = [], 0
        for x, text in runs:
            tokens = text.split()
            matched_run = matched[index:index + len(tokens)]
            if [word for _, _, word in matched_run] != tokens or abs(matched_run[0][0][0] - x) > 2:
                raise ValueError("independent PDF text and position disagree")
            boxes = [box for box, _, _ in matched_run]
            fields.append((text, [round(boxes[0][0], 4), round(min(box[1] for box in boxes), 4),
                                  round(boxes[-1][2], 4), round(max(box[3] for box in boxes), 4)]))
            index += len(tokens)
        if index != len(matched):
            raise ValueError("PDF row has unassigned words")
        lines.append(fields)
        word_lines.append([(text, [round(value, 4) for value in box]) for box, _, text in matched])
    if len(used) != len(words):
        raise ValueError("PDF page has unassigned words")
    return lines, word_lines


def _plan(title, account_line, declaration, originals, digest, size):
    count = len(originals)
    allowed = {f"{_NOTE}{count} entries."}
    if count == 2:
        allowed.add(f"{_NOTE}two entries.")
    account = re.fullmatch(rf"Account ({_ACCOUNT}) \| Currency CNY \| Opening ({_MONEY})", account_line)
    if title not in _TITLES or not 1 <= count <= MAX_ROWS or account is None or declaration not in allowed:
        raise ValueError("PDF ledger declarations missing")
    owner, opening = account.groups()
    prior, previous_time, previous_y, records, rows = Decimal(opening), None, 0, [], []
    for number, (values, boxes) in enumerate(originals, 1):
        if (len(values) != 5 or len(boxes) != 5
                or any(len(box) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
                       or not 0 <= box[0] < box[2] <= 1600 or not 0 <= box[1] < box[3] <= 1600 for box in boxes)
                or min(box[1] for box in boxes) < previous_y
                or any(left[2] > right[0] for left, right in zip(boxes, boxes[1:]))):
            raise ValueError("invalid PDF ledger row location")
        clock, payer, payee, amount_text, balance_text = values
        if any(re.fullmatch(pattern, value) is None for pattern, value in
               zip((_CLOCK, _ACCOUNT, _ACCOUNT, _MONEY, _MONEY), values, strict=True)):
            raise ValueError("invalid PDF ledger value")
        stamp, amount, balance = datetime.fromisoformat(clock), Decimal(amount_text), Decimal(balance_text)
        if (amount <= 0 or (payer == owner) == (payee == owner)
                or previous_time is not None and stamp <= previous_time
                or prior + (-amount if payer == owner else amount) != balance):
            raise ValueError("PDF ledger does not reconcile")
        key = table_import.record_key(MEMBER_PATH, digest, number)
        row = dict.fromkeys(table_import.FLOW_FIELDS, "")
        row.update(trade_time=stamp, payer_account=payer, payee_account=payee, self_account=owner,
                   amount=amount, balance=balance, currency="CNY", _source_row=number, _source_sheet="",
                   _source_row_kind=ROW_KIND, _record_key=key)
        rows.append(row)
        records.append({"record_no": number, "member_path": MEMBER_PATH, "source_row": number, "record_key": key,
                        "disposition": "accepted", "reason_code": "transaction_valid",
                        "details": {"pdf_page": "1", "pdf_table": "1", "pdf_row": str(number),
                                    "pdf_text_title": title, "pdf_text_account": account_line,
                                    "pdf_text_declaration": declaration,
                                    "pdf_text_original": json.dumps(values), "pdf_text_boxes": json.dumps(boxes),
                                    "flow_sha256": table_import.flow_digest(row)}})
        prior, previous_time, previous_y = balance, stamp, max(box[3] for box in boxes)
    member = {"member_path": MEMBER_PATH, "role": "pdf", "sha256": digest, "size_bytes": size,
              "encoding": "pdf", "row_count": count}
    document = {"member_path": MEMBER_PATH, "role": "transaction", "columns": [], "row_count": count,
                "pdf_total_pages": 1, "pdf_candidate_pages": 1, "pdf_status": "pdf_text_ledger_verified"}
    plan = {"profile": PROFILE, "rows": rows, "records": records, "members": [member], "documents": [document],
            "mapping_evidence": [], "sheet": "", "mode": "A", "mapping": {}}
    table_cancellations.refresh_stats(plan)
    return plan


def read_pages(source, size, digest):
    """Verify each physical page independently; an unverified sibling is None."""
    if not 0 < size <= MAX_BYTES or not (executable := shutil.which("pdftotext")):
        return None
    try:
        from pypdf import PdfReader, __version__, apply_configuration
        from pypdf.errors import PyPdfError
    except ImportError:
        return None
    if __version__ != PYPDF_VERSION:
        return None
    source.seek(0)
    data = source.read(MAX_BYTES + 1)
    if len(data) != size or sha256(data).hexdigest() != digest:
        return None
    reading_errors = (ValueError, TypeError, KeyError, IndexError, AttributeError, ArithmeticError,
                      RecursionError, OSError, subprocess.SubprocessError, ET.ParseError, PyPdfError)
    try:
        with (
            apply_configuration(maximum_declared_stream_length=MAX_BYTES, zlib_maximum_output_length=MAX_BYTES,
                                stream_parsing_work_maximum_count=5000, page_tree_maximum_depth=3),
            tempfile.TemporaryDirectory(prefix="ft-pdf-text-") as temporary,
        ):
            source_path = Path(temporary) / "source.pdf"
            source_path.write_bytes(data)
            pages = []
            for number, raw_page in enumerate(_page_tree(data, PdfReader), 1):
                try:
                    width, height, groups = _plain_page(raw_page)
                    lines, words = _bbox(source_path, executable, number, width, height, groups)
                    pages.append((width, height, groups, lines, words))
                except reading_errors:
                    pages.append(None)
            return pages
    except reading_errors:
        return None


def read_page(source, size, digest):
    """Get the sole page for the frozen single-page ledger and observation contracts."""
    pages = read_pages(source, size, digest)
    return pages[0] if pages is not None and len(pages) == 1 else None


def ledger_plan(page, digest, size):
    """Apply the closed ledger contract to an independently read page."""
    if page is None:
        return None
    _, _, groups, lines, _ = page
    try:
        if any(len(line) != 1 for line in lines[:3]) or tuple(text for text, _ in lines[3]) != _LABELS:
            return None
        header_x = [x for x, _ in groups[3][1]]
        if any([x for x, _ in row] != header_x for _, row in groups[4:]):
            return None
        values = [([value for value, _ in line], [box for _, box in line]) for line in lines[4:]]
        return _plan(lines[0][0][0], lines[1][0][0], lines[2][0][0], values, digest, size)
    except (ValueError, IndexError, ArithmeticError):
        return None


def read(source, size, digest):
    """Return verified ledger observations, or leave the original in source review."""
    return ledger_plan(read_page(source, size, digest), digest, size)


def replay_journal(details, digest, size):
    """Validate the recorded page positions and ledger facts on archive recovery."""
    if not 1 <= len(details) <= MAX_ROWS:
        raise ValueError("invalid PDF text ledger count")
    values = [(json.loads(item["pdf_text_original"]), json.loads(item["pdf_text_boxes"])) for item in details]
    plan = _plan(details[0]["pdf_text_title"], details[0]["pdf_text_account"], details[0]["pdf_text_declaration"],
                 values, digest, size)
    if any(item != row["details"] for item, row in zip(details, plan["records"], strict=True)):
        raise ValueError("invalid PDF text ledger row receipt")
    return plan
