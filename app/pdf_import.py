"""Optional local PDF extraction. Only bounded, unverified text enters the journal."""
from contextlib import closing
import errno
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import sys
import tempfile
import time

from .table_import import record_key

PROFILE = "pdf-review-v1"
ROW_KIND = "pdf_record"
MEMBER_PATH = "document.pdf"
CANDIDATE = "pdf_candidate"
CELL_QUALITY_UNAVAILABLE = "pdf_cell_quality_unavailable"
_CANDIDATE_REASONS = frozenset({CANDIDATE, CELL_QUALITY_UNAVAILABLE})
TOOL_VERSION = "4.2.3"
MINERU_REVIEW_REASONS = frozenset({
    "pdf_tool_unavailable", "pdf_input_limit", "pdf_invalid", "pdf_no_tables",
    "pdf_ocr_failed", "pdf_ocr_timeout", "pdf_result_invalid", "pdf_result_limit",
})
MIB = 1024 * 1024
MAX_SOURCE_BYTES = 300 * MIB
MAX_INPUT_BYTES = 32 * MIB
MAX_RESULT_BYTES = 8 * MIB
MAX_PLAN_BYTES = MIB
MAX_PAGES = 100
MAX_TABLES = 200
MAX_ROWS = 1_000
MAX_COLUMNS = 32
MAX_CELL_BYTES = 1024
MAX_RECORD_BYTES = 64 * 1024
SECONDS = 90

_POSITION_FIELDS = ("pdf_page", "pdf_table", "pdf_row")
_CELLS_FIELD = "pdf_candidate_cells"
_BINDING_FIELDS = ("case_id", "import_id", "surface", "suffix", "sha256", "size_bytes")


class ResultLimit(ValueError):
    """The output exceeded the admitted evidence budget."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def valid_source(filename, size, mode, mapping, sheet, row_kind):
    return (isinstance(filename, str) and Path(filename).suffix.lower() == ".pdf"
            and type(size) is int and 0 <= size <= MAX_SOURCE_BYTES and mode == "B"
            and mapping == {} and sheet == "" and row_kind == ROW_KIND)


def _status(reason, rows=None, page_count=None):
    return {"reason": reason, "rows": [] if rows is None else rows,
            **({"page_count": page_count} if page_count is not None else {})}


def _details(page, table, row, cells):
    return {**dict(zip(_POSITION_FIELDS, map(str, (page, table, row)))), _CELLS_FIELD: _json(cells)}


def _validate_review(review):
    if (not isinstance(review, dict) or set(review) not in ({"reason", "rows"}, {"reason", "rows", "page_count"})
            or not isinstance(review["reason"], str) or not isinstance(review["rows"], list)):
        raise ValueError("invalid PDF review")
    reason, rows = review["reason"], review["rows"]
    pages = review.get("page_count", MAX_PAGES)
    if (type(pages) is not int or not 0 < pages <= MAX_PAGES
            or "page_count" in review and reason not in _CANDIDATE_REASONS | {"pdf_no_tables"}):
        raise ValueError("invalid PDF review page count")
    if reason not in _CANDIDATE_REASONS:
        if reason not in MINERU_REVIEW_REASONS or rows:
            raise ValueError("invalid PDF review disposition")
        return
    if not rows:
        raise ValueError("empty PDF review candidates")
    if len(rows) > MAX_ROWS:
        raise ResultLimit("PDF candidate row limit")
    previous = (0, 0, 0)
    tables = set()
    for point in rows:
        if not isinstance(point, list) or len(point) != 4:
            raise ValueError("invalid PDF coordinate")
        page, table, row, cells = point
        if (not all(type(value) is int and value > 0 for value in (page, table, row))
                or (page, table, row) <= previous):
            raise ValueError("invalid PDF coordinate order")
        previous = (page, table, row)
        tables.add((page, table))
        if page > pages or table > MAX_TABLES or row > MAX_ROWS or len(tables) > MAX_TABLES:
            raise ResultLimit("PDF coordinate limit")
        if not isinstance(cells, list) or not cells or any(
                not isinstance(cell, str) or any(ord(char) < 32 and char not in "\n\t\r" for char in cell) for cell in cells):
            raise ValueError("invalid PDF candidate cells")
        if not any(cell.strip() for cell in cells):
            raise ValueError("empty PDF candidate row")
        if (len(cells) > MAX_COLUMNS or any(len(cell.encode()) > MAX_CELL_BYTES for cell in cells)
                or len(_json(_details(page, table, row, cells)).encode()) > MAX_RECORD_BYTES):
            raise ResultLimit("PDF candidate cell limit")
    if len(_json(review).encode()) > MAX_PLAN_BYTES:
        raise ResultLimit("PDF candidate plan limit")


def _plan_payload(session, review):
    return {"profile": PROFILE, **{key: session[key] for key in _BINDING_FIELDS}, "review": review}


def freeze(session, review):
    """Bind admitted candidates to this particular session and retained bytes."""
    _validate_review(review)
    payload = _plan_payload(session, review)
    return {**payload, "plan_sha256": hashlib.sha256(_json(payload).encode()).hexdigest()}


def replay(session, frozen):
    """Reconstruct only a source journal. The monetary rows are always empty."""
    if not isinstance(frozen, dict) or not isinstance(frozen.get("review"), dict):
        raise ValueError("invalid frozen PDF review")
    review = frozen["review"]
    if frozen != freeze(session, review):
        raise ValueError("frozen PDF review binding changed")
    reason, rows = review["reason"], review["rows"]
    details = [_details(*point) for point in rows] if rows else [{}]
    records = [{
        "record_no": number, "member_path": MEMBER_PATH, "source_row": number,
        "record_key": record_key(MEMBER_PATH, session["sha256"], number),
        "disposition": "unresolved", "reason_code": reason, "details": detail,
    } for number, detail in enumerate(details, 1)]
    count = len(records)
    coverage = ({"pdf_total_pages": review["page_count"], "pdf_candidate_pages": len({point[0] for point in rows})}
                if "page_count" in review else {})
    return {
        "profile": PROFILE, "rows": [], "records": records, "mapping_evidence": [],
        "members": [{"member_path": MEMBER_PATH, "role": "pdf", "sha256": session["sha256"],
                     "size_bytes": session["size_bytes"], "encoding": "pdf", "row_count": count}],
        "documents": [{"member_path": MEMBER_PATH, "role": "pdf", "columns": [],
                       "row_count": count, "pdf_status": reason, **coverage}],
        "stats": {"total": count, "ok": 0, "skipped": count, "bad_examples": [],
                  "unresolved": count, "non_monetary": 0, "non_transaction": 0,
                  "auxiliary": 0, "reason_counts": {reason: count}, "currency_totals": {}},
    }


def validate_journal(digest, records):
    """Accept only the source-only evidence permitted by this profile."""
    reason, rows, count, bytes_seen = None, [], 0, 0
    for count, record in enumerate(records, 1):
        number, member, position, key, disposition, code, raw = record
        if count > MAX_ROWS:
            raise ResultLimit("PDF journal row limit")
        if (number != count or position != count or member != MEMBER_PATH
                or disposition != "unresolved" or (reason is not None and code != reason)
                or key != record_key(MEMBER_PATH, digest, count) or not isinstance(raw, str)
                or len(raw.encode()) > MAX_RECORD_BYTES):
            raise ValueError("invalid PDF record")
        reason = code
        bytes_seen += len(raw.encode())
        if bytes_seen > 2 * MAX_PLAN_BYTES + 128 * MAX_ROWS:
            raise ResultLimit("PDF journal byte limit")
        details = json.loads(raw)
        if code in _CANDIDATE_REASONS:
            if (not isinstance(details, dict) or set(details) != {*_POSITION_FIELDS, _CELLS_FIELD}
                    or not isinstance(details[_CELLS_FIELD], str)
                    or any(not isinstance(details[field], str) or not re.fullmatch(r"[1-9][0-9]{0,3}", details[field])
                           for field in _POSITION_FIELDS)):
                raise ValueError("invalid PDF candidate details")
            rows.append([*(int(details[field]) for field in _POSITION_FIELDS), json.loads(details[_CELLS_FIELD])])
        elif details != {} or count != 1:
            raise ValueError("invalid PDF issue details")
    _validate_review(_status(reason, rows))
    return count


def _child_env(directory, model_dir):
    config = directory / "mineru.yaml"
    # JSON is a YAML subset. Encode the absolute path as data.
    config.write_text(_json({"model": {"base_dir": str(model_dir)}}), encoding="utf-8")
    env = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR") if key in os.environ}
    env.update(MINERU_HOME=str(directory), MINERU_CONFIG=str(config), MINERU_MODEL_SOURCE="local",
               MINERU_MODEL_SMALL_BACKEND="onnx", MINERU_MODEL_ONNX_DEVICE="cpu",
               MINERU_PROCESSING_WINDOW_SIZE="4", MINERU_PDF_RENDER_THREADS="1",
               OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
               MINERU_TELEMETRY_DISABLED="1", HF_HUB_OFFLINE="1", PYTHONNOUSERSITE="1",
               TMPDIR=str(directory), TMP=str(directory), TEMP=str(directory))
    return env


def _result_size(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return 0
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("invalid PDF result file")
    if info.st_size > MAX_RESULT_BYTES:
        raise ResultLimit("PDF result file limit")
    return info.st_size


def _posix_exited(pid):
    # Keep the leader waitable until its owned process group has been signalled.
    # Reaping it on observation would permit reuse of the numeric group ID.
    if hasattr(os, "waitid"):
        return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    # CPython on macOS exposes kqueue but not waitid.
    event = select.kevent(pid, filter=select.KQ_FILTER_PROC, flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                          fflags=select.KQ_NOTE_EXIT)
    with closing(select.kqueue()) as queue:
        for result in queue.control([event], 1, 0):
            if result.flags & select.KQ_EV_ERROR:
                if result.data == errno.ESRCH:
                    return True  # Already exited before the event was registered.
                raise OSError(result.data, os.strerror(result.data))
            if result.fflags & select.KQ_NOTE_EXIT:
                return True
    return False


def _darwin_exited_alone(pid):
    """Verify a waitable leader is the only member of its owned group."""
    import ctypes

    if not _posix_exited(pid):
        return False
    list_pids = ctypes.CDLL("/usr/lib/libproc.dylib").proc_listpids
    list_pids.argtypes = (ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int)
    list_pids.restype = ctypes.c_int
    # PROC_PGRP_ONLY selects this group, including zombies. Two slots
    # distinguish our sole leader from any larger or truncated result.
    members = (ctypes.c_int * 2)()
    size = list_pids(2, pid, members, ctypes.sizeof(members))
    return size == ctypes.sizeof(ctypes.c_int) and members[0] == pid


def _stop(process):
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError as error:
            # Darwin returns EPERM even for a group containing just its zombie.
            # Retain the leader's PID while verifying there are no descendants.
            if error.errno != errno.EPERM or sys.platform != "darwin" or not _darwin_exited_alone(process.pid):
                raise
        process.wait()


def _poll(process):
    if os.name == "nt":
        return process.poll()
    if process.returncode is None and _posix_exited(process.pid):
        _stop(process)
    return process.returncode


def _cell_spans(attrs):
    spans = {}
    for name, raw in attrs:
        if name not in ("rowspan", "colspan"):
            continue
        digits = raw.strip() if raw is not None else ""
        if name in spans or not re.fullmatch(r"[0-9]+", digits):
            raise ValueError("malformed PDF HTML cell span")
        digits = digits.lstrip("0")
        if not digits:
            # In particular, rowspan=0 requires an open-ended row group.
            raise ValueError("zero PDF HTML cell span")
        limit = MAX_ROWS if name == "rowspan" else MAX_COLUMNS
        if len(digits) > len(str(limit)) or int(digits) > limit:
            raise ResultLimit("PDF candidate span limit")
        spans[name] = int(digits)
    return spans.get("rowspan", 1), spans.get("colspan", 1)


class _TableRows(HTMLParser):
    """Place source text at the origin of a bounded grid cell."""
    _GROUPS = frozenset({"thead", "tbody", "tfoot"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.started = self.finished = False
        self.rows = []
        self.group = None
        self.rowspans = {}
        self.row = None
        self.column = 0
        self.cell = None
        self.cell_tag = None
        self.cell_bytes = 0
        self.cell_extent = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self.started:
                raise ValueError("nested or multiple PDF HTML tables")
            self.started = True
        elif tag in self._GROUPS:
            if not self.started or self.finished or self.group is not None or self.row is not None or self.rowspans:
                raise ValueError("invalid PDF HTML row group")
            self.group = tag
        elif tag == "tr":
            if not self.started or self.finished or self.row is not None:
                raise ValueError("invalid PDF HTML row")
            if len(self.rows) >= MAX_ROWS:
                raise ResultLimit("PDF candidate row limit")
            # Keep trailing columns covered by earlier rows, even when the
            # current row stops before those columns in the HTML.
            self.row = [""] * (max(self.rowspans, default=-1) + 1)
            self.column = 0
        elif tag in {"td", "th"}:
            if self.row is None or self.cell is not None:
                raise ValueError("invalid PDF HTML cell")
            row_count, column_count = _cell_spans(attrs)
            while self.column in self.rowspans:
                self.column += 1
            bottom, right = len(self.rows) + row_count, self.column + column_count
            if bottom > MAX_ROWS or right > MAX_COLUMNS:
                raise ResultLimit("PDF candidate grid limit")
            if any(column in self.rowspans for column in range(self.column, right)):
                raise ValueError("overlapping PDF HTML cell spans")
            self.cell_extent = (bottom, right)
            self.cell, self.cell_tag, self.cell_bytes = [], tag, 0
        elif tag == "br":
            self.handle_data("\n")
        elif tag in {"script", "style"}:
            raise ValueError("unsupported markup in PDF table")

    def handle_data(self, data):
        if self.cell is not None:
            self.cell_bytes += len(data.encode())
            if self.cell_bytes > MAX_CELL_BYTES:
                raise ResultLimit("PDF candidate cell limit")
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"}:
            if self.cell is None or self.cell_tag != tag:
                raise ValueError("mismatched PDF HTML cell")
            bottom, right = self.cell_extent
            self.row.extend([""] * (right - len(self.row)))
            self.row[self.column] = "".join(self.cell).strip()
            if bottom > len(self.rows) + 1:
                for column in range(self.column, right):
                    self.rowspans[column] = bottom
            self.column = right
            self.cell = None
        elif tag == "tr":
            if self.row is None or self.cell is not None:
                raise ValueError("mismatched PDF HTML row")
            self.rows.append(self.row)
            self.row = None
            self.rowspans = {column: bottom for column, bottom in self.rowspans.items() if bottom > len(self.rows)}
        elif tag in self._GROUPS:
            if self.group != tag or self.row is not None or self.rowspans:
                raise ValueError("PDF HTML cell span crosses a row group bound")
            self.group = None
        elif tag == "table":
            if not self.started or self.finished or self.row is not None or self.group is not None or self.rowspans:
                raise ValueError("mismatched PDF HTML table")
            self.finished = True


def _middle_rows(data):
    if (not isinstance(data, dict) or data.get("schema") != "docvortex.middle"
            or data.get("schema_version") != "2.0" or type(data.get("is_full_document")) is not bool):
        raise ValueError("unsupported PDF middle schema")
    metadata, pages = data.get("metadata"), data.get("pages")
    if not isinstance(metadata, dict) or metadata.get("file_suffix") != "pdf" or not isinstance(pages, list):
        raise ValueError("invalid PDF middle metadata")
    producer, document = metadata.get("producer"), metadata.get("document")
    if not isinstance(producer, dict) or producer.get("name") != "mineru" or producer.get("version") != TOOL_VERSION:
        raise ValueError("invalid PDF producer")
    if not data["is_full_document"] or len(pages) > MAX_PAGES:
        raise ResultLimit("PDF page limit")
    if (not isinstance(document, dict) or document.get("page_count_kind") != "physical"
            or type(document.get("page_count")) is not int or document["page_count"] != len(pages) or not pages):
        raise ValueError("invalid PDF producer or page coverage")
    rows, table_total = [], 0
    for page_number, page in enumerate(pages, 1):
        if (not isinstance(page, dict) or type(page.get("page_idx")) is not int
                or page["page_idx"] != page_number - 1 or not isinstance(page.get("blocks"), list)):
            raise ValueError("invalid PDF page")
        table_number, last_table_index = 0, -1
        for block in page["blocks"]:
            if not isinstance(block, dict) or not isinstance(block.get("type"), str):
                raise ValueError("invalid PDF block")
            if block["type"] != "table":
                continue
            index, parts = block.get("index"), block.get("content")
            if type(index) is not int or index <= last_table_index or not isinstance(parts, list):
                raise ValueError("invalid PDF table")
            last_table_index = index
            table_number += 1
            table_total += 1
            if table_total > MAX_TABLES:
                raise ResultLimit("PDF table limit")
            bodies = [part.get("content") for part in parts
                      if isinstance(part, dict) and part.get("type") == "table_body"]
            if len(bodies) != 1 or not isinstance(bodies[0], str):
                raise ValueError("invalid PDF table body")
            parser = _TableRows()
            parser.feed(bodies[0])
            parser.close()
            if not parser.finished:
                raise ValueError("incomplete PDF table body")
            for row_number, cells in enumerate(parser.rows, 1):
                if not any(cells):
                    continue
                if len(rows) >= MAX_ROWS:
                    raise ResultLimit("PDF candidate row limit")
                rows.append([page_number, table_number, row_number, cells])
    return len(pages), rows


def _run(executable, model_dir, directory, document, output, cancelled, deadline):
    # One sentinel page distinguishes an over-limit document without running
    # OCR over every later page. Partial extracts never publish candidates.
    command = [str(executable), "parse", str(document), "--pages", f"1-{MAX_PAGES + 1}", "--tier", "basic",
               "--format", "middle_json", "-o", str(output)]
    env = _child_env(directory, model_dir)
    if cancelled.is_set():
        return None
    if os.name == "nt":
        from . import windows_import_job
        process = windows_import_job.start(command, cwd=directory, env=env)
    else:
        process = subprocess.Popen(command, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        while _poll(process) is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return _status("pdf_ocr_timeout")
            if cancelled.wait(min(0.05, remaining)):
                return None
            _result_size(output)
        if cancelled.is_set():
            return None
        if time.monotonic() >= deadline:
            return _status("pdf_ocr_timeout")
        if process.returncode != 0:
            return _status("pdf_ocr_failed")
        if not _result_size(output):
            return _status("pdf_result_invalid")
        with output.open("rb") as stream:
            contents = stream.read(MAX_RESULT_BYTES + 1)
        if len(contents) > MAX_RESULT_BYTES:
            raise ResultLimit("PDF result file limit")
        pages, rows = _middle_rows(json.loads(contents))
        # MinerU 4.2.3 exports table HTML and boxes. Its internal OCR scores
        # cannot be tied to these cells from the supported output.
        review = _status(CELL_QUALITY_UNAVAILABLE if rows else "pdf_no_tables", rows, pages)
        _validate_review(review)
        if cancelled.is_set():
            return None
        if time.monotonic() >= deadline:
            return _status("pdf_ocr_timeout")
        return review
    finally:
        if os.name == "nt":
            process.stop()
        else:
            _stop(process)


def _review_seconds():
    setting = os.environ.get("FUND_MINERU_REVIEW_TIMEOUT_SECONDS")
    if setting is None:
        return SECONDS
    if re.fullmatch(r"[1-9][0-9]{1,2}", setting):
        seconds = int(setting)
        if SECONDS <= seconds <= 900:
            return seconds
    raise ValueError("MinerU trial timeout must be an ASCII integer from 90 to 900 seconds")


def extract(source, size, sha256, cancelled):
    """Run the explicitly configured CLI once on a private copy of the upload."""
    if size > MAX_INPUT_BYTES:
        return _status("pdf_input_limit")
    source.seek(0)
    if not re.match(rb"%PDF-[0-9]\.[0-9]", source.read(8)):
        return _status("pdf_invalid")
    source.seek(max(0, size - 4096))
    if b"%%EOF" not in source.read(4096):
        return _status("pdf_invalid")
    executable = Path(os.environ.get("FUND_MINERU_EXECUTABLE", ""))
    configured_dir = os.environ.get("FUND_MINERU_MODEL_DIR")
    deadline = time.monotonic() + _review_seconds()
    try:
        model_dir = Path(configured_dir) if configured_dir else Path.home() / ".mineru" / "models"
        if not (executable.is_absolute() and executable.is_file()):
            return _status("pdf_tool_unavailable")
        if not (model_dir.is_absolute() and model_dir.is_dir()):
            return _status("pdf_tool_unavailable")
        if cancelled.is_set():
            return None
        with tempfile.TemporaryDirectory(prefix="ft-mineru-") as temp:
            directory = Path(temp)
            document, output = directory / "source.pdf", directory / "middle.json"
            source.seek(0)
            digest, length = hashlib.sha256(), 0
            with document.open("xb") as target:
                for block in iter(lambda: source.read(MIB), b""):
                    if cancelled.is_set():
                        return None
                    if time.monotonic() >= deadline:
                        return _status("pdf_ocr_timeout")
                    length += len(block)
                    if length > size:
                        return _status("pdf_invalid")
                    digest.update(block)
                    if target.write(block) != len(block):
                        raise OSError("incomplete private PDF copy")
            if length != size or digest.hexdigest() != sha256:
                return _status("pdf_invalid")
            return _run(executable, model_dir, directory, document, output, cancelled, deadline)
    except ResultLimit:
        return _status("pdf_result_limit")
    except (ValueError, RecursionError):
        return _status("pdf_result_invalid")
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return _status("pdf_ocr_failed")
