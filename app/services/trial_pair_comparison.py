"""Compare the sides recorded on two current, explicitly selected source rows."""
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import timedelta
import csv
from decimal import Decimal, localcontext
from hashlib import file_digest
import re
from zipfile import BadZipFile, ZipFile

from .. import bank_posting_pairs, db, event_resolution, feedback_import, import_originals
from .. import source_ledger, source_transaction_status, table_import, table_source_roles
from . import imports, trace_review
from .cases import ServiceError


_XLSX_MEMBER = re.compile(r"worksheets/([0-9]{4})\Z")
_ACCOUNT_DIGITS = re.compile(r"[0-9]{1,128}")
_COMPARED_DISPOSITIONS = {"imported", "duplicate", "unresolved"}
_REFERENCE_SCAN_ROWS = 10_000
_BANK32_REFERENCE_ROWS = 25_000
_BANK32_TIED_NEIGHBORS = 6
_BANK32_CONTEXT_LIMIT = 64
_BANK32_CROSS_SOURCE_LIMIT = 4
_BANK32_CROSS_SOURCE_SECONDS = 120
_REFERENCE_SCAN_BYTES = 8 * 1024 * 1024
_REFERENCE_EXAMPLES = 12
_FEEDBACK_XLSX_TEXT = {1, 2, 6, 7, 12, 13, 17, 21, 22, 23, 24, 26, 27}
_FEEDBACK_XLSX_KINDS = {**{column: {"text"} for column in _FEEDBACK_XLSX_TEXT},
                        3: {"text", "date"}, 4: {"text", "number"}, 5: {"text", "number"}, 16: {"text", "number"}}


def _active_original(case_id, source_id):
    original = imports._registered_original(case_id, source_id)
    if not original["active"] or original["active_batch_id"] is None:
        raise ServiceError(409, "source_operation_changed", "原件的导入状态已变化，请重新选择")
    return original


def _table_member(original, member, document, selected):
    position = _XLSX_MEMBER.fullmatch(member["member_path"])
    sheet = document.get("sheet")
    source_rows = document.get("source_rows")
    if (original["suffix"] != ".xlsx" or member["encoding"] != "xlsx" or member["role"] != "table"
            or position is None or not 1 <= (index := int(position[1])) <= table_import.MAX_SHEETS
            or not sheet or document.get("header_rows") != [1] or type(source_rows) is not int
            or table_import.xlsx_member_digest(original["sha256"], index, sheet) != member["sha256"]):
        return False
    return all(row["disposition"] in _COMPARED_DISPOSITIONS and row["header_scope"] == "1"
               and (row["disposition"] != "unresolved" or row["validation_issues"] == "conflicting_field")
               and 1 < row["source_row"] <= source_rows
               and row["record_key"] == table_import.record_key(member["member_path"], member["sha256"], row["source_row"])
               for row in selected)


def _columns(header):
    signature = table_import.UNIFORM_FEEDBACK_35_COLUMNS
    width = len(signature)
    names = [cell["value"].strip() if cell["kind"] == "text" else "" for cell in header]
    if frozenset(names[:width]) != signature or any(names[width:]):
        return None
    return names.index("本方账号") + 1, names.index("借贷标志") + 1


def _posting(cells, columns, *, uniform):
    owners, directions = columns
    if len(cells) < max(columns) or uniform and any(
        cell["kind"] not in {"text", "number"} or cell["value"].strip()
        for cell in cells[len(table_import.UNIFORM_FEEDBACK_35_COLUMNS):]
    ):
        return None
    own, marker = cells[owners - 1], cells[directions - 1]
    account = own["value"].strip()
    # Matching suffixes and placeholders cannot establish a shared ledger.
    if own["kind"] != "text" or not _ACCOUNT_DIGITS.fullmatch(account) or not account.strip("0"):
        return None
    direction = table_import._enum(marker["value"]) if marker["kind"] == "text" else ""
    if direction in table_import._IN:
        return account, "in"
    if direction in table_import._OUT:
        return account, "out"
    return None


def _certified_posting_columns(document, evidence, columns, account_heading, direction_heading, account_basis,
                               *, query_column=None):
    claims = [entry for entry in evidence if str(entry.get("header_row")) == "1"
              and entry.get("field") in {"self_account", "direction"}]
    expected = {
        ("self_account", account_heading, str(columns[0]), "2" if query_column else "1", account_basis),
        ("direction", direction_heading, str(columns[1]), "1", "exact_header_alias"),
    }
    if query_column:
        expected.add(("self_account", "查询帐号", str(query_column), "2", "uniform_query_owner"))
    return (document.get("mode") == "B" and len(claims) == len(expected)
            and {(item.get("field"), item.get("column"), str(item.get("column_index")),
                  str(item.get("candidate_count")), item.get("basis")) for item in claims} == expected)


def _accepted_postings(header, physical, selected, document, evidence):
    # The regular import already certified these exact row roles. Re-read the
    # enrolled account and direction from the current source to answer the
    # limited same-ledger question without inferring an economic event.
    names = [cell["value"].strip() if cell["kind"] == "text" else "" for cell in header]
    role = table_source_roles.classify(names, query_conditions=True)
    account_heading = {"transaction_explicit": "本方账号", "transaction_query_only": "查询帐号"}.get(role)
    direction_heading = "借贷标志"
    if account_heading not in names or direction_heading not in names:
        return None
    columns = names.index(account_heading) + 1, names.index(direction_heading) + 1
    query_owner = role == "transaction_query_only"
    if query_owner and not _certified_posting_columns(
            document, evidence, columns, account_heading, direction_heading, "uniform_query_owner"):
        return None
    query_column = names.index("查询帐号") + 1 if role == "transaction_explicit" and "查询帐号" in names else None
    certified_alias = query_column and _certified_posting_columns(
        document, evidence, columns, account_heading, direction_heading, "exact_header_alias",
        query_column=query_column)
    postings = []
    for row in selected:
        uses_alias = certified_alias and row["account_column"] == "查询帐号"
        if (row["disposition"] not in {"imported", "duplicate"}
                or (row["account_column"] != account_heading and not uses_alias)
                or row["direction_basis"] != "explicit_direction" or not row["ledger_evidence"]):
            return None
        try:
            book = source_ledger.validate(row["ledger_evidence"])
        except ValueError:
            return None
        cells = physical.get(row["source_row"], ())
        posting = _posting(cells, columns, uniform=False)
        if book["kind"] != "financial" or posting != (book["row"]["self_account"], book["direction"]):
            return None
        # An explicit-owner table can enroll the agreeing inquiry field first.
        # Verify both physical values before citing the explicit owner.
        if uses_alias and _posting(cells, (query_column, columns[1]), uniform=False) != posting:
            return None
        if (query_owner or uses_alias) and cells[columns[1] - 1]["value"].strip() != book["source_direction"]:
            return None
        postings.append(posting)
    return columns, postings


def _matching_reference(first, second):
    value = bank_posting_pairs._identifier(first)
    return (bool(value) and value == second.strip()
            and value not in {"未提供", "未取得", "未详", "缺失", "待核", "无流水号"}
            and not any(char in value for char in "*＊?？…•")
            and not re.fullmatch(r"(?:[xX]{3,}[0-9]+|[0-9]+[xX]{3,}|[+-]?0+(?:\.0+)?)", value))


def _financial_records_fit(locator, last, selected):
    return all(row["disposition"] in {"imported", "duplicate"} and row["header_scope"] == "1"
               and 1 < row["source_row"] <= last
               and row["record_key"] == table_import.record_key(locator["member_path"], locator["sha256"], row["source_row"])
               for row in selected)


def _csv_source_rows(case_id, locator, document, selected, *, standalone, source_session=None,
                     row_limit=_REFERENCE_SCAN_ROWS):
    last = document.get("source_rows")
    if (locator["role"] != "table" or not locator["member_path"].lower().endswith(".csv")
            or document.get("header_rows") != [1] or type(last) is not int or not 1 < last <= row_limit
            or locator["size_bytes"] > _REFERENCE_SCAN_BYTES or not _financial_records_fit(locator, last, selected)):
        return None
    try:
        if source_session is not None:
            stream = imports._source(source_session, snapshot=True)
        elif standalone:
            stream, _ = imports.get_source_file(case_id, locator["source_id"])
        else:
            stream, _ = imports.get_source_member_file(case_id, locator["source_id"], locator["member_path"])
        with closing(stream):
            if not standalone and file_digest(stream, "sha256").hexdigest() != locator["sha256"]:
                raise ServiceError(409, "source_changed", "来源成员无法按登记摘要回读")
            stream.seek(0)
            raw = import_originals._csv_rows(stream, locator["encoding"], document, set(range(1, last + 1)),
                                             locator["parser_version"])
    except (OSError, UnicodeError, ValueError, csv.Error):
        return None
    headings = [cell.strip() for cell in raw.get(1, ())]
    if len(raw) != last or any(any(cell.strip() for cell in row) and len(row) != len(headings) for row in raw.values()):
        return None
    return raw, headings


def _reference_competition(raw, headings, selected, reference_column):
    first, second = (raw[position] for position in selected)
    offset = reference_column - 1
    reference = first[offset].strip()
    if not _matching_reference(reference, second[offset]):
        return None
    others = sorted(number for number, row in raw.items()
                    if number > 1 and number not in selected and len(row) == len(headings)
                    and row[offset].strip() == reference)
    return {"column": reference_column, "first_row": 2, "last_row": len(raw),
            "other_source_rows": others[:_REFERENCE_EXAMPLES], "other_count": len(others),
            "selected_differences": [{"column": column, "heading": heading}
                                     for column, (heading, left, right) in enumerate(zip(headings, first, second, strict=True), 1)
                                     if left.strip() != right.strip()]}


def _csv_reference(case_id, locator, document, selected, evidence, *, standalone):
    parsed = _csv_source_rows(case_id, locator, document, selected, standalone=standalone)
    if parsed is None:
        return None
    raw, headings = parsed
    expected = table_import.BANK_FEEDBACK_31_COLUMNS if standalone else set(feedback_import.COLUMNS)
    if len(headings) != len(expected) or set(headings) != expected:
        return None
    owner, direction, ref = (headings.index(field) + 1 for field in ("交易账号", "收付标志", "交易流水号"))
    if standalone and not _certified_posting_columns(
            document, evidence, (owner, direction), "交易账号", "收付标志", "exact_header_alias"):
        return None
    positions = [row["source_row"] for row in selected]
    values = [raw[position] for position in positions]
    if any(len(row) != len(headings) for row in values):
        return None
    postings = []
    for record, row in zip(selected, values, strict=True):
        try:
            book = source_ledger.validate(record["ledger_evidence"] or "")
        except ValueError:
            return None
        account, raw_marker = row[owner - 1].strip(), row[direction - 1].strip()
        marker = table_import._enum(raw_marker)
        side = "in" if marker in table_import._IN else "out" if marker in table_import._OUT else None
        if (book["kind"] != "financial" or record["direction_basis"] != "explicit_direction"
                or not _ACCOUNT_DIGITS.fullmatch(account) or not account.strip("0")
                or not side or (account, side) != (book["row"]["self_account"], book["direction"])
                or standalone and raw_marker != book["source_direction"]):
            return None
        postings.append((account, side))
    return (owner, direction), postings, _reference_competition(raw, headings, positions, ref)


def _feedback_direction(value):
    marker = table_import._enum(value)
    return "in" if marker in table_import._IN else "out" if marker in table_import._OUT else None


def _feedback_currency(value):
    return table_import._CURRENCY.get(value, value.upper())


def _feedback_clock(value):
    return None if table_import._date_only(value) else table_import._time(value)


def _feedback_posting(record, cells, *, allow_missing_card=False):
    try:
        book = source_ledger.validate(record["ledger_evidence"] or "")
    except ValueError:
        return None
    card, account, raw_clock, raw_amount = (cell.strip() for cell in cells[:4])
    raw_marker, counter, raw_currency = (cells[index].strip() for index in (5, 6, 12))
    side = _feedback_direction(raw_marker)
    clock = _feedback_clock(raw_clock)
    amount, _ = table_import._money(raw_amount, {"factor": Decimal(1)})
    currency = _feedback_currency(raw_currency)
    snapshot = book["row"]
    if (book["kind"] != "financial" or book["reported_status"] != "unknown"
            or record["direction_basis"] != "explicit_direction"
            or not _ACCOUNT_DIGITS.fullmatch(account) or not account.strip("0")
            or (card or not allow_missing_card) and (not _ACCOUNT_DIGITS.fullmatch(card) or not card.strip("0"))
            or not side or (side, raw_marker) != (book["direction"], book["source_direction"])
            or (account, card) != (snapshot["self_account"], book["own_card"])
            or counter != snapshot["payer_account" if side == "in" else "payee_account"]
            or not clock or snapshot["trade_time_utc"] is not None
            or clock.isoformat(sep=" ") != snapshot["trade_time"]
            or amount is None or amount <= 0 or amount != Decimal(book["signed_amount"])
            or not currency or currency != book["source_currency"]
            or cells[20].strip() != snapshot["trade_no"]):
        return None
    return book


def _bounded_workbook_rows(original, locator, document, selected):
    last = document.get("source_rows")
    if (original is None or not _table_member(original, locator, document, ())
            or not 1 < last <= _REFERENCE_SCAN_ROWS or original["size_bytes"] > _REFERENCE_SCAN_BYTES
            or not _financial_records_fit(locator, last, selected)):
        return None
    index = int(_XLSX_MEMBER.fullmatch(locator["member_path"])[1])
    try:
        with closing(imports._source(original, snapshot=True)) as stream:
            with ZipFile(stream) as archive:
                if sum(member.file_size for member in archive.infolist()) > table_import.MAX_LAYOUT_XLSX_BYTES:
                    return None
            stream.seek(0)
            raw, kinds = import_originals._xlsx_rows(stream, index, document["sheet"], set(range(1, last + 1)))
    except (OSError, KeyError, IndexError, StopIteration, ValueError, BadZipFile, import_originals.ET.ParseError):
        return None
    return raw, kinds


def _xlsx_feedback_rows(original, locator, document, selected):
    found = _bounded_workbook_rows(original, locator, document, selected)
    if found is None:
        return None
    raw, kinds = found
    width = len(feedback_import.COLUMNS)
    for number, row in raw.items():
        if any(value.strip() for value in row[width:]):
            return None
        if number == 1:
            if any(kinds.get((number, column)) != "text" for column in range(1, width + 1)):
                return None
        elif any(row[column - 1].strip() and kinds.get((number, column)) not in allowed
                 for column, allowed in _FEEDBACK_XLSX_KINDS.items() if column <= len(row)):
            return None
        # Trailing coordinates may be absent in worksheet XML. XLSX defines
        # them as blank without giving them the authority of an original value.
        raw[number] = row[:width] + [""] * max(0, width - len(row))
    return raw, [value.strip() for value in raw[1]]


def _feedback_original_rows(case_id, locator, document, selected=(), *, source_session=None):
    if locator["suffix"] == ".xlsx":
        parsed = _xlsx_feedback_rows(source_session, locator, document, selected)
    else:
        parsed = _csv_source_rows(case_id, locator, document, selected, standalone=True, source_session=source_session)
    if parsed is None or tuple(parsed[1]) != feedback_import.COLUMNS or document.get("mode") != "B":
        return None
    return parsed


def _feedback_material(case_id, locator, document, selected=(), *, source_session=None):
    parsed = _feedback_original_rows(case_id, locator, document, selected, source_session=source_session)
    if parsed is None:
        return None
    raw, headings = parsed
    references, owners, accounts = defaultdict(list), defaultdict(set), set()
    for number, row in raw.items():
        if number == 1 or len(row) != len(headings):
            continue
        card, account = (value.strip() for value in row[:2])
        if account:
            accounts.add(account)
            if card:
                owners[card].add(account)
        for offset in (16, 20, 21):
            if value := row[offset].strip():
                references[offset, value].append(number)
    return raw, headings, references, owners, accounts


def _feedback_selected_books(raw, headings, selected):
    positions = [row["source_row"] for row in selected]
    rows = [raw[position] for position in positions]
    if any(len(row) != len(headings) for row in rows):
        return None
    books = [_feedback_posting(record, cells) for record, cells in zip(selected, rows, strict=True)]
    return books if all(books) else None


def _feedback_matching_books(material, selected, *, books=None):
    raw, headings, references, owners, account_numbers = material
    if books is None and not (books := _feedback_selected_books(raw, headings, selected)):
        return None
    positions = [row["source_row"] for row in selected]
    rows = [raw[position] for position in positions]
    accounts = [book["row"]["self_account"] for book in books]
    cards = [book["own_card"] for book in books]
    directions = [book["direction"] for book in books]
    first, second = rows
    reference = first[20].strip()
    if (accounts[0] == accounts[1] or cards[0] == cards[1] or set(directions) != {"in", "out"}
            or [row[6].strip() for row in rows] != cards[::-1]
            or not first[15].strip() or first[15].strip() != second[15].strip()
            or any(books[0]["row"][key] != books[1]["row"][key] for key in ("trade_time", "amount", "currency"))
            or not _matching_reference(reference, second[20]) or sorted(positions) != references[20, reference]
            or any(card in account_numbers or owners.get(card) != {account}
                   for card, account in zip(cards, accounts, strict=True))):
        return None
    return books


def _feedback_observations(case_id, locator, document, selected, *, source_session=None):
    material = _feedback_material(case_id, locator, document, selected, source_session=source_session)
    if material is None:
        return None
    raw, headings, references, _, _ = material
    if not (books := _feedback_selected_books(raw, headings, selected)):
        return None
    positions = [row["source_row"] for row in selected]
    competition = _reference_competition(raw, headings, positions, 21)
    postings = [(book["row"]["self_account"], book["direction"]) for book in books]
    if _feedback_matching_books(material, selected, books=books):
        correspondence = {"own_card_column": 1, "counterparty_identifier_column": 7, "reference_column": 21,
                          "status_column": 16, "time_column": 3, "amount_column": 4, "currency_column": 13}
        return (2, 6), postings, competition, correspondence, None
    records = [raw[position] for position in positions]
    first_book, second_book = books
    own_cards = first_book["own_card"], second_book["own_card"]
    differences = {"opposite_directions": first_book["direction"] != second_book["direction"],
                   "reciprocal_card_text": own_cards[0] != own_cards[1]
                       and tuple(row[6].strip() for row in records) == own_cards[::-1],
                   "same_recorded_clock": first_book["row"]["trade_time"] == second_book["row"]["trade_time"],
                   "same_currency": first_book["source_currency"] == second_book["source_currency"],
                   "same_amount": first_book["signed_amount"] == second_book["signed_amount"],
                   "same_reference": competition is not None,
                   "reference_occurrences": [len(references[20, row[20].strip()]) if _matching_reference(row[20], row[20]) else None
                                             for row in records],
                   "columns": {"own_card": 1, "clock": 3, "amount": 4, "direction": 6,
                               "peer": 7, "currency": 13, "reference": 21}}
    return (2, 6), postings, competition, None, differences


def _query_posting_differences(original, locator, document, selected, physical_head, postings):
    """Compare original textual facts in a registered query-only workbook."""
    headings = [cell["value"].strip() if cell["kind"] == "text" else "" for cell in physical_head]
    if not all(postings) or table_source_roles.classify(headings, query_conditions=True) != "transaction_query_only":
        return None
    names = {"own_card": "查询卡号", "peer": "交易对方账卡号", "clock": "交易时间",
             "amount": "交易金额", "direction": "借贷标志", "currency": "币种",
             "reference": "交易流水号", "voucher": "凭证号"}
    if any(heading not in headings for heading in names.values()):
        return None
    if not (found := _bounded_workbook_rows(original, locator, document, selected)):
        return None
    raw, kinds = found
    width = len(headings)
    for number, cells in raw.items():
        if any(value.strip() for value in cells[width:]):
            return None
        raw[number] = cells[:width] + [""] * max(0, width - len(cells))
    if [value.strip() for value in raw[1]] != headings:
        return None
    columns = {key: headings.index(name) + 1 for key, name in names.items()}
    positions = [row["source_row"] for row in selected]
    first, second = records = [{key: raw[position][column - 1].strip() for key, column in columns.items()}
                               for position in positions]
    if any(values[key] and kinds.get((position, columns[key])) != "text"
           for position, values in zip(positions, records, strict=True) for key in columns if key != "amount"):
        return None
    amounts = [table_import._money(row["amount"], {"factor": Decimal(1)})[0] for row in records]
    clocks = [_feedback_clock(row["clock"]) for row in records]
    currencies = [_feedback_currency(row["currency"]) for row in records]
    if (not all(amount is not None and amount > 0 for amount in amounts) or not all(clocks)
            or not all(currency in table_import._ISO_CURRENCIES for currency in currencies)):
        return None
    reference_column = columns["reference"]
    competition = _reference_competition(raw, headings, positions, reference_column)
    counts = Counter(row[reference_column - 1].strip() for position, row in raw.items() if position > 1)
    own_cards = first["own_card"], second["own_card"]
    distinct_cards = own_cards[0] != own_cards[1] and all(
        _ACCOUNT_DIGITS.fullmatch(card) and card.strip("0") for card in own_cards)
    facts = {"opposite_directions": postings[0][1] != postings[1][1],
             "reciprocal_card_text": distinct_cards and (second["peer"], first["peer"]) == own_cards,
             "same_recorded_clock": clocks[0] == clocks[1], "same_currency": currencies[0] == currencies[1],
             "same_amount": amounts[0] == amounts[1], "same_reference": competition is not None,
             "reference_occurrences": [counts[row["reference"]] if _matching_reference(row["reference"], row["reference"])
                                       else None for row in records],
             "different_vouchers": bool(first["voucher"] and second["voucher"] and first["voucher"] != second["voucher"]),
             "columns": columns}
    return facts, competition


def _feedback_ledger_neighbors(raw, width):
    ledgers, missing_clocks = defaultdict(list), set()
    currency_gaps, unlocated_currency_gaps = defaultdict(list), set()
    for number, row in raw.items():
        if number == 1 or len(row) != width:
            continue
        account, currency = row[1].strip(), _feedback_currency(row[12].strip())
        if not account:
            continue
        clock = _feedback_clock(row[2].strip())
        scope = account, currency
        if currency not in table_import._ISO_CURRENCIES:
            if clock is None:
                unlocated_currency_gaps.add(account)
            else:
                currency_gaps[account].append(clock)
        elif clock is None:
            missing_clocks.add(scope)
        else:
            ledgers[scope].append((clock, number))
    for clocks in currency_gaps.values():
        clocks.sort()
    neighbors = {}
    for scope, ledger in ledgers.items():
        account, _ = scope
        if scope in missing_clocks or account in unlocated_currency_gaps:
            continue
        ledger.sort()
        counts = Counter(clock for clock, _ in ledger)
        gaps = currency_gaps[account]
        for before, current, after in zip(ledger, ledger[1:], ledger[2:]):
            gap = bisect_left(gaps, before[0])
            if gap < len(gaps) and gaps[gap] <= after[0]:
                continue
            if all(counts[point[0]] == 1 for point in (before, current, after)):
                neighbors[current[1]] = (before[1], after[1])
    return neighbors


def _balance_witness(raw, neighbors, position):
    if position not in neighbors:
        return None
    before, after = neighbors[position]
    factor = {"factor": Decimal(1)}
    balances = [table_import._money(raw[number][4], factor)[0] for number in (before, position, after)]
    if any(value is None for value in balances):
        return None
    with localcontext() as context:
        context.prec = 50
        for number, earlier_balance, current_balance in zip((position, after), balances[:-1], balances[1:], strict=True):
            row = raw[number]
            side = _feedback_direction(row[5])
            amount, _ = table_import._money(row[3], factor)
            if side is None or amount is None or amount <= 0:
                return None
            if current_balance - earlier_balance != (amount if side == "in" else -amount):
                return None
    return before, after


def _compatible_feedback_voucher(first, second):
    kind = first[22].strip()
    if kind != second[22].strip():
        return False
    if not kind:
        # An untyped value can be reused by the whole retrieval job.
        return True
    cancellation = {"摘要说明": "", "备注": "", "交易类型": kind.removesuffix("凭证")}
    return (_matching_reference(first[23], second[23]) and not feedback_import.cancellation_text(cancellation))


def _registered_feedback_member(case_id, source_id):
    original = _active_original(case_id, source_id)
    if original["suffix"] not in {".csv", ".xlsx"}:
        return None
    members = db.q_raw("""select m.member_path,m.role,m.encoding,m.sha256,m.size_bytes
        from import_members m join import_sources s on s.case_id=m.case_id and s.source_id=m.source_id
        where m.case_id=? and m.source_id=? and s.parser_version=?""",
        [case_id, source_id, table_import.VOUCHER_REFERENCE_PROFILE])
    if len(members) != 1:
        return None
    member = members[0]
    if original["suffix"] == ".csv" and (member["member_path"] != "table.csv" or member["sha256"] != original["sha256"]):
        return None
    locator = {**member, "source_id": source_id, "suffix": original["suffix"],
               "parser_version": table_import.VOUCHER_REFERENCE_PROFILE}
    document, _ = trace_review._original_layout(case_id, locator)
    return original, locator, document


def read_registered_feedback_table(case_id, source_id, *, include_bank32=False):
    """Read an active, bounded feedback table with its physical columns."""
    with db.read_lock():
        try:
            registered = _registered_feedback_member(case_id, source_id)
            if registered is None:
                return None
            original, locator, document = registered
            if include_bank32 and _bank32_layout(locator, document):
                _, evidence = trace_review._original_layout(case_id, locator)
                if not _bank32_registered_roles(document["columns"], evidence, locator["member_path"]):
                    return None
                parsed = _csv_source_rows(case_id, locator, document, (), standalone=True, source_session=original)
                if parsed is not None and tuple(parsed[1]) != tuple(document["columns"]):
                    return None
            else:
                parsed = _feedback_original_rows(case_id, locator, document, source_session=original)
        except ServiceError as error:
            if error.status not in (404, 409):
                raise
            return None
    if parsed is None:
        return None
    rows, headings = parsed
    return {"source_id": source_id, "source_sha256": original["sha256"],
            "member_path": locator["member_path"], "member_sha256": locator["sha256"],
            "headings": tuple(headings), "rows": rows}

@dataclass
class _Bank32Journal:
    locator: dict
    last_row: int
    rows: dict
    columns: dict
    references: dict
    owners: dict
    accounts: set
    ledger: dict
    clock_gaps: dict
    incomplete_books: set

    @property
    def source_id(self):
        return self.locator["source_id"]


def _bank32_layout(locator, document):
    headers = document.get("columns", ())
    return (locator["suffix"] == ".csv" and locator["member_path"] == "table.csv"
            and locator["parser_version"] == table_import.VOUCHER_REFERENCE_PROFILE and document.get("mode") == "B"
            and len(headers) == len(table_import.BANK_FEEDBACK_32_COLUMNS)
            and frozenset(headers) == table_import.BANK_FEEDBACK_32_COLUMNS)


def _bank32_registered_roles(headings, evidence, member_path):
    fields = {"self_card": "交易卡号", "self_account": "交易账号", "trade_time": "交易时间",
              "amount": "交易金额", "balance": "交易余额", "direction": "收付标志",
              "counter_account": "交易对手账卡号", "currency": "交易币种", "status": "交易是否成功",
              "trade_no": "交易流水号"}
    wanted = {field: (label, str(headings.index(label) + 1),
                      {"self_card": "explicit_identity_role", "trade_no": "source_transaction_id"}.get(field, "exact_header_alias"))
              for field, label in fields.items()}
    rows = [entry for entry in evidence if entry["field"] in fields and entry["member_path"] == member_path
            and entry["header_row"] == "1"]
    return (len(rows) == len(fields) and {entry["field"] for entry in rows} == fields.keys() and all(
        entry["candidate_count"] == "1" and (entry["column"], entry["column_index"], entry["basis"]) == wanted[entry["field"]]
        and (entry["field"] not in {"amount", "balance"} or entry["unit_factor"] == "1")
        for entry in rows))


def _bank32_clock(value):
    return _feedback_clock(value) if feedback_import._FRACTIONAL_TIME.fullmatch(value.strip()) else None


def _bank32_journal(case_id, original, locator, document, evidence):
    if not _bank32_layout(locator, document) or not _bank32_registered_roles(document["columns"], evidence, locator["member_path"]):
        return None
    if locator["sha256"] != original["sha256"] or locator["size_bytes"] != original["size_bytes"]:
        return None
    parsed = _csv_source_rows(case_id, locator, document, (), standalone=True, source_session=original,
                              row_limit=_BANK32_REFERENCE_ROWS)
    if parsed is None:
        return None
    physical, headings = parsed
    if tuple(headings) != tuple(document["columns"]):
        return None
    # Use the existing posting checks with role-indexed source cells. The
    # original 32-column coordinates remain authoritative in all receipts.
    projection = [headings.index(label) for label in feedback_import.COLUMNS]
    columns = {label: index + 1 for index, label in enumerate(headings)}
    rows, references, owners, accounts = {}, defaultdict(list), defaultdict(set), set()
    ledger, currency_gaps, incomplete = defaultdict(lambda: defaultdict(list)), defaultdict(list), set()
    for position, physical_row in physical.items():
        if position <= 1 or len(physical_row) != len(headings):
            continue
        row = rows[position] = tuple(physical_row[index].strip() for index in projection)
        card, account, clock_text = row[:3]
        if card:
            owners[card].add(account)
        for column in (16, 20, 21):
            if value := row[column]:
                references[column, value].append(position)
        if not account:
            continue
        accounts.add(account)
        # Keep the original row and identifier evidence for source review.
        # An explicitly failed attempt contributes no balance step or clock gap.
        if source_transaction_status.explicitly_failed(row[15]):
            continue
        clock, currency = _bank32_clock(clock_text), _feedback_currency(row[12])
        if clock is None:
            incomplete.add((account, currency if currency in table_import._ISO_CURRENCIES else None))
        elif currency not in table_import._ISO_CURRENCIES:
            currency_gaps[account].append(clock)
        else:
            ledger[account, currency][clock].append(position)
    ordered = {key: (sorted(group), group) for key, group in ledger.items()}
    return _Bank32Journal(locator, document["source_rows"], rows, columns, references, owners, accounts, ordered,
                          {account: sorted(gaps) for account, gaps in currency_gaps.items()}, incomplete)


def _bank32_posting_text(row, *, literal_failure_only=False):
    status = table_import._enum(row[15])
    if (source_transaction_status.explicitly_failed(row[15]) or not literal_failure_only and status
            and not (status.isascii() and status.isdecimal())
            and status not in {"成功", "交易成功", "success", "succeeded"}):
        return False
    source = dict(zip(feedback_import.COLUMNS, row, strict=True))
    voucher_kind = {"摘要说明": "", "备注": "", "交易类型": source["凭证种类"].removesuffix("凭证")}
    if feedback_import.cancellation_text(source) or feedback_import.cancellation_text(voucher_kind):
        return False
    # An explicit reversal type rules out an ordinary posting match even
    # when its accompanying reference is not sufficient to prove cancellation.
    return not feedback_import.cancellation_text({**voucher_kind, "交易类型": source["交易类型"]})


def _bank32_balance_step(row, *, literal_failure_only=False):
    side = _feedback_direction(row[5])
    amount, _ = table_import._money(row[3], {"factor": 1})
    balance, _ = table_import._money(row[4], {"factor": 1})
    if (side is None or amount is None or amount <= 0 or balance is None
            or not _bank32_posting_text(row, literal_failure_only=literal_failure_only)):
        return None
    return balance - (amount if side == "in" else -amount), balance


def _bank32_balance_witness(journal, position, *, literal_failure_only=False):
    row = journal.rows[position]
    account, currency, clock = row[1], _feedback_currency(row[12]), _bank32_clock(row[2])
    scope = account, currency
    if scope in journal.incomplete_books or (account, None) in journal.incomplete_books or scope not in journal.ledger:
        return None
    clocks, groups = journal.ledger[scope]
    offset = bisect_left(clocks, clock)
    if not 0 < offset < len(clocks) - 1 or groups[clock] != [position]:
        return None
    before_clock, after_clock = clocks[offset - 1], clocks[offset + 1]
    before, after = groups[before_clock], groups[after_clock]
    if len(after) != 1 or len(before) > _BANK32_TIED_NEIGHBORS:
        return None
    earlier_clock = before_clock
    if len(before) > 1:
        if offset < 2 or len(groups[clocks[offset - 2]]) != 1:
            return None
        earlier_clock = clocks[offset - 2]
    gaps = journal.clock_gaps.get(account, ())
    if bisect_left(gaps, earlier_clock) != bisect_right(gaps, after_clock):
        return None
    first, = groups[earlier_clock]
    needed = [*before, position, *after] if earlier_clock != before_clock else [position, *after]
    remaining = list(before) if earlier_clock != before_clock else []
    with localcontext() as context:
        context.prec = 50
        initial = _bank32_balance_step(journal.rows[first], literal_failure_only=literal_failure_only)
        if initial is None:
            return None
        _, opening = initial
        steps = {number: _bank32_balance_step(journal.rows[number], literal_failure_only=literal_failure_only)
                 for number in needed}
        if any(step is None for step in steps.values()):
            return None
        while remaining:
            choices = [number for number in remaining if steps[number][0] == opening]
            if len(choices) != 1:
                return None
            [following] = choices
            opening = steps[following][1]
            remaining.remove(following)
        [after_position] = after
        if (opening != steps[position][0] or steps[position][1] != steps[after_position][0]):
            return None
    # Coordinates identify the local evidence window without presenting a
    # chronological order among entries whose source clocks are identical.
    return tuple(sorted({first, *before, *after}))


def _bank32_journal_records(case_id, journal, positions, *, allow_missing_card=False):
    if not positions:
        return {}
    rows = db.q_raw("""select r.source_row,r.record_no,r.record_key,r.disposition,s.flow_id,
        json_extract_string(r.details_json,'$.direction_basis') direction_basis,
        json_extract_string(r.details_json,'$.source_ledger_evidence') ledger_evidence,
        json_extract_string(json_extract_string(r.details_json,'$.source_ledger_evidence'),'$.header_scope') header_scope
        from import_records r join flow_sources s on s.case_id=r.case_id and s.source_id=r.source_id and s.source_row=r.record_no
        where r.case_id=? and r.source_id=? and r.member_path=?
        and r.source_row in (select unnest(?::bigint[]))""",
        [case_id, journal.source_id, journal.locator["member_path"], sorted(positions)])
    by_position = defaultdict(list)
    for row in rows:
        by_position[row["source_row"]].append(row)
    candidates = [r[0] for r in by_position.values() if len(r) == 1
                  and _financial_records_fit(journal.locator, journal.last_row, r)]
    live = {row["id"]: row for row in db.q_raw("""select id,trade_time,trade_time_utc,self_account,payer_account,payee_account,
        currency,amount,balance from source_effective_flows where case_id=? and id in (select unnest(?::bigint[]))""",
        [case_id, [row["flow_id"] for row in candidates]])} if candidates else {}
    current = {}
    for record in candidates:
        if (flow := live.get(record["flow_id"])) is None:
            continue
        source = journal.rows[record["source_row"]]
        book = _feedback_posting(record, source, allow_missing_card=allow_missing_card)
        if book is None or not _bank32_clock(source[2]):
            continue
        snapshot = book["row"]
        balance, _ = table_import._money(source[4], {"factor": 1})
        if (balance is None or snapshot["balance"] is None or balance != Decimal(snapshot["balance"])
                or balance != flow["balance"] or flow["amount"] != Decimal(snapshot["amount"])
                or flow["trade_time_utc"] is not None or flow["trade_time"] != _bank32_clock(source[2])
                or any(flow[name] != snapshot[name] for name in ("self_account", "payer_account", "payee_account", "currency"))):
            continue
        current[record["source_row"]] = {**record, "book": book}
    return current


def _bank32_account_lead(case_id, journal, positions, *, with_registration=True):
    """A local account-to-account lead leaves identity, pairing and settlement open."""
    if len(journal.rows) != journal.last_row - 1:
        return None
    first, second = (journal.rows[position] for position in positions)
    a, b = first[1], second[1]
    clock = _bank32_clock(first[2])
    amount = table_import._money(first[3], {"factor": 1})[0]
    currency = _feedback_currency(first[12])
    refs = (16, 20, 21, 23)
    if (a == b or a in journal.owners or b in journal.owners or (first[6], second[6]) != (b, a)
            or clock is None or clock != _bank32_clock(second[2])
            or currency not in table_import._ISO_CURRENCIES or currency != _feedback_currency(second[12])
            or amount is None or amount <= 0 or amount != table_import._money(second[3], {"factor": 1})[0]
            or {_feedback_direction(first[5]), _feedback_direction(second[5])} != {"in", "out"}
            or any(row[index] for row in (first, second) for index in refs)):
        return None
    observations = []
    for position, row in zip(positions, (first, second), strict=True):
        witnesses = _bank32_balance_witness(journal, position)
        if not witnesses:
            return None
        direction = _feedback_direction(row[5])
        count = sum(candidate[1] == row[1] and candidate[6] == row[6]
                    and _feedback_currency(candidate[12]) == currency and _feedback_direction(candidate[5]) == direction
                    and table_import._money(candidate[3], {"factor": 1})[0] == amount for candidate in journal.rows.values())
        observations.append({"source_row": position, "direction": direction, "balance_witness_rows": list(witnesses),
                             "whole_member_candidate_count": count})
    lead = {"kind": "reciprocal_accounts", "own_account_column": journal.columns["交易账号"],
            "counterparty_identifier_column": journal.columns["交易对手账卡号"],
            "balance_column": journal.columns["交易余额"],
            "empty_reference_columns": [journal.columns[feedback_import.COLUMNS[index]] for index in refs],
            "observations": observations}
    if with_registration:
        from . import trial_trace_lookup

        lexical = [(position, row[1], currency, row[0]) for position, row in zip(positions, (first, second), strict=True)]
        if context := trial_trace_lookup.account_registration_context(case_id, lexical):
            lead["registration_context"] = context
    return lead


def account_pair_lead(case_id, flow_ids, journals, *, with_registration=True):
    """Check an already recalled pair against its active, same-member original."""
    matches = []
    with db.read_lock():
        for journal in journals.values():
            if not isinstance(journal, _Bank32Journal):
                continue
            positions = sorted(_bank32_flow_positions(case_id, journal, flow_ids))
            if len(positions) != 2:
                continue
            records = _bank32_journal_records(case_id, journal, positions, allow_missing_card=True)
            if {row["flow_id"] for row in records.values()} != flow_ids:
                continue
            if lead := _bank32_account_lead(case_id, journal, positions, with_registration=with_registration):
                matches.append(lead)
    return matches[0] if len(matches) == 1 else None


def _active_bank32_journal(case_id, locator, source_cache=None):
    source_id = locator["source_id"]
    if source_cache is not None and isinstance(journal := source_cache.get(source_id), _Bank32Journal):
        return journal if (journal.locator["sha256"] == locator["sha256"]
                           and journal.locator["member_path"] == locator["member_path"]) else None
    try:
        registered = _registered_feedback_member(case_id, source_id)
        if registered is None:
            return None
        original, member, document = registered
        if (member["member_path"] != locator["member_path"] or member["sha256"] != locator["member_sha256"]
                or original["sha256"] != locator["sha256"]):
            return None
        _, evidence = trace_review._original_layout(case_id, member)
        journal = _bank32_journal(case_id, original, member, document, evidence)
        if source_cache is not None and journal is not None:
            source_cache[source_id] = journal
        return journal
    except ServiceError as error:
        if error.status not in (404, 409):
            raise
        return None


def cross_source_card_candidates(case_id, anchor, origin, limit, *, source_cache=None):
    """Recall current reciprocal CSV observations without granting an event link."""
    profile = table_import.VOUCHER_REFERENCE_PROFILE
    if origin["parser_version"] != profile or origin["suffix"] != ".csv" or origin["header_scope"] != "1":
        return None
    try:
        book = source_ledger.validate(origin["ledger_evidence"] or "")
    except ValueError:
        return None
    first = book["row"]
    side, card = book["direction"], book.get("own_card")
    if book["kind"] != "financial" or side not in {"in", "out"} or card != anchor.get("source_own_card"):
        return None
    own = first["self_account"]
    counter = first["payee_account" if side == "out" else "payer_account"]
    if (not all(_ACCOUNT_DIGITS.fullmatch(value or "") and len(set(value)) > 1 for value in (card, own, counter))
            or card == counter or first["trade_time_utc"] is not None or (stamp := _bank32_clock(first["trade_time"])) is None):
        return None
    amount = Decimal(first["amount"])
    currency = first["currency"]
    if amount <= 0:
        return None

    peer_self = "raw_payee_account" if side == "out" else "raw_payer_account"
    peer_counter = "raw_payer_account" if side == "out" else "raw_payee_account"
    possible = db.q_raw(f"""with anchor_clock as (select ?::timestamp stamp, ?::integer * interval '1 second' span)
        select f.id from effective_visible_flows f cross join anchor_clock p
        where f.case_id=? and f.id<>? and f.source_own_card=? and f.{peer_counter}=?
          and f.raw_self_account=f.{peer_self} and f.raw_self_account<>? and f.amount=? and f.currency=?
          and f.trade_time_utc is null and f.trade_time between p.stamp - p.span and p.stamp + p.span
        order by abs(date_diff('microsecond',p.stamp,f.trade_time)),f.id limit ?""",
        [stamp, _BANK32_CROSS_SOURCE_SECONDS, case_id, anchor["id"], counter, card, own, amount, currency, _BANK32_CONTEXT_LIMIT + 1])
    if not possible:
        return None

    journal = _active_bank32_journal(case_id, origin, source_cache)
    if journal is None or card in journal.accounts or counter in journal.accounts or journal.owners.get(card) != {own}:
        return None
    anchor_position = origin["source_row"]
    valid = _bank32_journal_records(case_id, journal, {anchor_position}).get(anchor_position)
    if (not valid or valid["flow_id"] != int(anchor["id"])
            or not _bank32_posting_text(journal.rows[anchor_position], literal_failure_only=True)):
        return None

    by_source = defaultdict(list)
    by_flow = {item["flow_id"]: item for item in trace_review._locators(case_id,
               [row["id"] for row in possible[:_BANK32_CONTEXT_LIMIT]], include_ledger=True)}
    complete = len(possible) <= _BANK32_CONTEXT_LIMIT
    for item in possible[:_BANK32_CONTEXT_LIMIT]:
        locator = by_flow.get(item["id"])
        if (locator is None or locator["parser_version"] != profile or locator["suffix"] != ".csv"
                or locator["sha256"] == origin["sha256"] or locator["source_id"] == origin["source_id"]):
            complete = False
            continue
        by_source[locator["source_id"]].append(locator)
    if len(by_source) > _BANK32_CROSS_SOURCE_LIMIT:
        complete = False

    matches = {}
    for locators in list(by_source.values())[:_BANK32_CROSS_SOURCE_LIMIT]:
        peer = _active_bank32_journal(case_id, locators[0], source_cache)
        if peer is None:
            complete = False
            continue
        rows = _bank32_journal_records(case_id, peer, {loc["source_row"] for loc in locators})
        for loc in locators:
            position = loc["source_row"]
            verified = rows.get(position)
            if verified is None or verified["flow_id"] != loc["flow_id"]:
                complete = False
                continue
            raw = peer.rows[position]
            if (raw[0] != counter or raw[6] != card or raw[1] == own or peer.owners.get(counter) != {raw[1]}
                    or card in peer.accounts or counter in peer.accounts
                    or not _bank32_posting_text(raw, literal_failure_only=True)
                    or verified["book"]["direction"] == side):
                continue
            matches[str(loc["flow_id"])] = abs((_bank32_clock(raw[2]) - stamp).total_seconds())
    if not matches and complete:
        return None
    ordered = sorted(matches, key=lambda key: (matches[key], int(key)))
    shown = ordered[:limit]
    return {"candidate_ids": shown, "candidate_total": len(matches), "candidate_total_exact": complete,
            "more": not complete or len(matches) > len(shown),
            "source_card_clock_window_seconds": _BANK32_CROSS_SOURCE_SECONDS,
            "source_card_clock_gaps": {key: matches[key] for key in shown}}


def _book_identifiers(journal, position):
    account = journal.rows[position][1]
    return {account, *(card for card, owners in journal.owners.items() if owners == {account})}


def _cross_source_unique_row(journal, position, amount, currency, opposite_identifiers):
    """Check competing occurrences beyond the clock window in this source."""
    if len(journal.rows) != journal.last_row - 1:
        return False
    _, account, _, _, _, direction, *_ = journal.rows[position]
    side = _feedback_direction(direction)
    matches = []
    for number, row in journal.rows.items():
        # The source labels this as a mixed account/card field. A candidate
        # carrying the opposite original account or its other proved card
        # still competes, even when the current two rows match card to card.
        if (row[1] != account or row[6] not in opposite_identifiers
                or source_transaction_status.explicitly_failed(row[15])):
            continue
        observed = _feedback_direction(row[5])
        row_currency = _feedback_currency(row[12])
        if (observed is not None and observed != side
                or row_currency in table_import._ISO_CURRENCIES and row_currency != currency):
            continue
        value, _ = table_import._money(row[3], {"factor": 1})
        if value is not None and abs(value) != amount:
            continue
        if observed is None or row_currency != currency or value != amount or not _bank32_posting_text(
                row, literal_failure_only=True):
            return False
        matches.append(number)
    return matches == [position]


def _active_mixed_counterparty_rival(case_id, anchors, journals, positions, amount, currency):
    ids = [int(row["id"]) for row in anchors]
    identifiers = [_book_identifiers(journal, position)
                   for journal, position in zip(journals, positions, strict=True)]
    for index, (journal, position) in enumerate(zip(journals, positions, strict=True)):
        row = journal.rows[position]
        own_role = "raw_payer_account" if _feedback_direction(row[5]) == "out" else "raw_payee_account"
        counter_role = "raw_payee_account" if own_role == "raw_payer_account" else "raw_payer_account"
        other = 1 - index
        if db.q_raw(f"""with clock_span as (select ?::timestamp stamp, ?::integer * interval '1 second' span)
            select 1 from effective_visible_flows f cross join clock_span w
            where f.case_id=? and f.id not in (select unnest(?::bigint[])) and f.amount=? and f.currency=?
              and f.trade_time_utc is null and f.trade_time between w.stamp-w.span and w.stamp+w.span
              and f.raw_self_account=f.{own_role}
              and (f.raw_self_account=? or f.source_own_card in (select unnest(?::varchar[])))
              and f.{counter_role} in (select unnest(?::varchar[]))
            limit 1""", [_bank32_clock(journals[other].rows[positions[other]][2]), _BANK32_CROSS_SOURCE_SECONDS,
                           case_id, ids, amount, currency, row[1], sorted(identifiers[index]), sorted(identifiers[other])]):
            return True
    return False


def cross_source_card_pair(case_id, anchor, origin, candidates, *, source_cache):
    """Bound one reciprocal pair for the ordinary canvas, leaving settlement open."""
    if not candidates or not candidates["candidate_total_exact"] or candidates["candidate_total"] != 1:
        return None
    [peer_id] = candidates["candidate_ids"]
    [peer] = db.q_raw(
        "select * from effective_visible_flows where case_id=? and id=?", [case_id, int(peer_id)]) or [None]
    [locator] = trace_review._locators(case_id, [int(peer_id)], include_ledger=True) or [None]
    if peer is None or locator is None:
        return None
    reverse = cross_source_card_candidates(case_id, peer, locator, 2, source_cache=source_cache)
    if (not reverse or not reverse["candidate_total_exact"] or reverse["candidate_total"] != 1
            or reverse["candidate_ids"] != [str(anchor["id"])]):
        return None
    amount, currency = Decimal(anchor["amount"]), anchor["currency"]
    positions = (origin["source_row"], locator["source_row"])
    originals = [origin, locator]
    journals = [_active_bank32_journal(case_id, original, source_cache) for original in originals]
    if any(journal is None for journal in journals):
        return None
    witnesses = []
    for index, (journal, position) in enumerate(zip(journals, positions, strict=True)):
        other = 1 - index
        if (not _cross_source_unique_row(journal, position, amount, currency,
                                         _book_identifiers(journals[other], positions[other]))
                or not (balance := _bank32_balance_witness(journal, position, literal_failure_only=True))):
            return None
        witnesses.append(balance)
    if _active_mixed_counterparty_rival(case_id, [anchor, peer], journals, positions, amount, currency):
        return None
    return {"flow_ids": (int(anchor["id"]), int(peer_id)), "source_rows": positions,
            "source_id": origin["source_id"], "member_path": origin["member_path"],
            "witness_rows": witnesses, "amount": amount, "currency": currency,
            "origins": [{key: original[key] for key in ("source_id", "member_path", "sha256", "source_row")}
                        for original in originals]}


def _canvas_cross_source_pairs(case_id, selected, source_cache):
    # Only the current selection can contribute a display deduction. Recall
    # still uses the full active scope in both directions before accepting it.
    anchors = db.q_raw("""with selected as materialized (
        select *, case when raw_payer_account=raw_self_account then raw_payee_account
                       when raw_payee_account=raw_self_account then raw_payer_account end counterpart
        from effective_visible_flows where case_id=? and id in (select unnest(?::bigint[]))
          and source_own_card<>'' and raw_self_account<>'' and trade_time_utc is null and amount>0
    ) select a.* from selected a where exists (select 1 from selected b
        where a.id<b.id and a.source_own_card=b.counterpart and b.source_own_card=a.counterpart
          and a.raw_self_account<>b.raw_self_account and a.amount=b.amount and a.currency=b.currency
          and (a.raw_self_account=a.raw_payer_account)<>(b.raw_self_account=b.raw_payer_account)
          and b.trade_time between a.trade_time-? * interval '1 second' and a.trade_time+? * interval '1 second')""",
        [case_id, sorted(selected), _BANK32_CROSS_SOURCE_SECONDS, _BANK32_CROSS_SOURCE_SECONDS])
    locators = {row["flow_id"]: row for row in trace_review._locators(case_id, [row["id"] for row in anchors],
                                                                  include_ledger=True)}
    for anchor in anchors:
        if (origin := locators.get(anchor["id"])) is None:
            continue
        candidates = cross_source_card_candidates(case_id, anchor, origin, 2, source_cache=source_cache)
        if candidates and all(int(key) in selected for key in candidates["candidate_ids"]):
            if pair := cross_source_card_pair(case_id, anchor, origin, candidates, source_cache=source_cache):
                yield pair


def _bank32_peer_candidates(journal, anchor_position, peer_position, amount, currency):
    anchor, peer = journal.rows[anchor_position], journal.rows[peer_position]
    stamp = _bank32_clock(anchor[2])
    clocks, groups = journal.ledger[peer[1], currency]
    window = timedelta(hours=24)
    lower = stamp - window if stamp - clocks[0] > window else clocks[0]
    upper = stamp + window if clocks[-1] - stamp > window else clocks[-1]
    start, end = bisect_left(clocks, lower), bisect_right(clocks, upper)
    direction = _feedback_direction(peer[5])
    return [position for clock in clocks[start:end] for position in groups[clock]
            if position != peer_position and (row := journal.rows[position])[0] == peer[0] and row[6] == anchor[0]
            and _feedback_direction(row[5]) == direction and table_import._money(row[3], {"factor": 1})[0] == amount]


def _bank32_competing_reference(journal, pair, amount, currency):
    """Same-role source references can veto an otherwise fitting log."""
    for position, peer_position in (pair, pair[::-1]):
        source, peer = journal.rows[position], journal.rows[peer_position]
        rivals = set()
        for column in (16, 20):
            value = source[column]
            if value != peer[column] and _matching_reference(value, value):
                references = journal.references.get((column, value), ())
                if len(references) > 1:
                    rivals.update(references)
        if rivals and any(other in rivals and _bank32_posting_text(journal.rows[other])
                          for other in _bank32_peer_candidates(journal, position, peer_position, amount, currency)):
            return True
    return False


def _bank32_local_pairs(case_id, journal, anchors, *, selected_records=()):
    groups = set()
    for position in anchors:
        row = journal.rows.get(position)
        if row is None:
            continue
        log = row[21]
        references = journal.references.get((21, log), ())
        if _ACCOUNT_DIGITS.fullmatch(log) and log.strip("0") and len(references) == 2:
            groups.add(tuple(references))
    positions = {position for pair in groups for position in pair} | {r["source_row"] for r in selected_records}
    current = _bank32_journal_records(case_id, journal, positions)
    found = []
    for pair in sorted(groups):
        if not set(pair) <= current.keys():
            continue
        first, second = (journal.rows[position] for position in pair)
        flow_ids = tuple(current[position]["flow_id"] for position in pair)
        book = current[pair[0]]["book"]
        amount, currency = Decimal(book["signed_amount"]), book["source_currency"]
        account_a, account_b = first[1], second[1]
        if (len(set(flow_ids)) != 2 or account_a == account_b or first[0] == second[0]
                or (first[6], second[6]) != (second[0], first[0])
                or any(row[0] in journal.accounts or journal.owners[row[0]] != {row[1]} for row in (first, second))
                or _feedback_direction(first[5]) == _feedback_direction(second[5])
                or _bank32_clock(first[2]) != _bank32_clock(second[2])
                or not first[15] or first[15] != second[15]
                or current[pair[0]]["book"]["source_currency"] != current[pair[1]]["book"]["source_currency"]
                or current[pair[0]]["book"]["signed_amount"] != current[pair[1]]["book"]["signed_amount"]
                or not _compatible_feedback_voucher(first, second)
                or bool(first[23] or second[23]) and not _matching_reference(first[23], second[23])
                or _bank32_competing_reference(journal, pair, amount, currency)):
            continue
        witnesses = tuple(_bank32_balance_witness(journal, position) for position in pair)
        if all(witnesses):
            found.append({"flow_ids": flow_ids, "source_rows": pair, "source_id": journal.source_id,
                          "member_path": journal.locator["member_path"], "witness_rows": witnesses,
                          "amount": amount, "currency": currency,
                          "source_reference_kind": "ledger_log"})
    return found, current


def _bank32_compare(case_id, original, locator, document, evidence, selected, result):
    try:
        journal = _bank32_journal(case_id, original, locator, document, evidence)
    except ServiceError as error:
        if error.status not in (404, 409):
            raise
        return result
    if journal is None:
        return result
    positions = [row["source_row"] for row in selected]
    with db._lock:
        active = _active_original(case_id, journal.source_id)
        if any(active[key] != original[key] for key in ("active_batch_id", "sha256")):
            raise ServiceError(409, "source_operation_changed", "原件的导入状态已变化，请重新选择")
        pairs, current = _bank32_local_pairs(case_id, journal, positions, selected_records=selected)
        if missing := set(positions) - current.keys():
            current.update(_bank32_journal_records(case_id, journal, missing, allow_missing_card=True))
    if any(row["source_row"] not in current or current[row["source_row"]]["record_no"] != row["record_no"]
           or current[row["source_row"]]["ledger_evidence"] != row["ledger_evidence"] for row in selected):
        return result
    cols = journal.columns
    first, second = (journal.rows[position] for position in positions)
    for row in result["rows"]:
        book = current[row["source_row"]]["book"]
        row.update(own_account_column=cols["交易账号"], direction_column=cols["收付标志"], direction=book["direction"])
    primary = cols["交易流水号"]
    # Report original occurrence counts, including placeholders, with no
    # authority to match this source's ledger logs.
    primary_counts = [len(journal.references.get((20, row[20]), ())) or None for row in (first, second)]
    same_reference = _matching_reference(first[20], second[20]) and primary_counts[0] == 2
    result["posting_comparison"] = {
        "opposite_directions": result["rows"][0]["direction"] != result["rows"][1]["direction"],
        "reciprocal_card_text": first[0] != second[0] and (first[6], second[6]) == (second[0], first[0]),
        "same_recorded_clock": _bank32_clock(first[2]) == _bank32_clock(second[2]),
        "same_currency": _feedback_currency(first[12]) == _feedback_currency(second[12]),
        "same_amount": current[positions[0]]["book"]["signed_amount"] == current[positions[1]]["book"]["signed_amount"],
        "same_reference": same_reference,
        "reference_text_matches": bool(first[20] and first[20] == second[20]),
        "reference_occurrences": primary_counts,
        "different_vouchers": bool(first[23] and second[23] and first[23] != second[23]),
        "columns": {"own_card": cols["交易卡号"], "clock": cols["交易时间"], "amount": cols["交易金额"],
                    "direction": cols["收付标志"], "peer": cols["交易对手账卡号"], "currency": cols["交易币种"],
                    "reference": primary, "journal": cols["日志号"], "slip": cols["传票号"]},
    }
    if first[1] == second[1]:
        if result["rows"][0]["direction"] == result["rows"][1]["direction"]:
            result.update(status="excluded", reason="same_ledger_same_direction")
        else:
            result["reason"] = "different_direction"
        return result
    result["reason"] = "different_ledger"
    if any(set(pair["source_rows"]) == set(positions) for pair in pairs):
        result["observation_correspondence"] = {
            "own_card_column": cols["交易卡号"], "counterparty_identifier_column": cols["交易对手账卡号"],
            "reference_column": cols["日志号"], "status_column": cols["交易是否成功"],
            "time_column": cols["交易时间"], "amount_column": cols["交易金额"], "currency_column": cols["交易币种"],
            "source_reference_kind": "ledger_log",
        }
    elif len(pairs) == 2 and len({flow for pair in pairs for flow in pair["flow_ids"]}) == 4:
        result.update(status="excluded", reason="other_ledger_correspondence",
                      alternative_correspondences=[list(pair["source_rows"]) for pair in pairs])
    elif lead := _bank32_account_lead(case_id, journal, sorted(positions)):
        result.update(reason="source_account_text_pending", account_pair_lead=lead)
    return result



def _bank32_flow_positions(case_id, journal, flow_ids):
    return {row["source_row"] for row in db.q_raw("""select r.source_row from import_records r
        join flow_sources fs on fs.case_id=r.case_id and fs.source_id=r.source_id and fs.source_row=r.record_no
        where r.case_id=? and r.source_id=? and r.member_path=?
          and fs.flow_id in (select unnest(?::bigint[]))""",
        [case_id, journal.source_id, journal.locator["member_path"], sorted(flow_ids)])}


def _bank32_context_candidates(case_id, journal, pair, anchors):
    for index, anchor_id in enumerate(pair["flow_ids"]):
        if anchor_id not in anchors:
            continue
        anchor_position, peer_position = pair["source_rows"][index], pair["source_rows"][1 - index]
        stamp = _bank32_clock(journal.rows[anchor_position][2])
        nearby = _bank32_peer_candidates(journal, anchor_position, peer_position, pair["amount"], pair["currency"])
        nearby.sort(key=lambda position: (abs(_bank32_clock(journal.rows[position][2]) - stamp), position))
        current = _bank32_journal_records(case_id, journal, set(nearby[:_BANK32_CONTEXT_LIMIT - 1]))
        ids = list(dict.fromkeys(str(current[position]["flow_id"]) for position in nearby
                                 if position in current and current[position]["flow_id"] not in pair["flow_ids"]))
        pair.setdefault("competition", {})[anchor_id] = {
            "candidate_ids": ids, "candidate_total": 1 + len(ids), "all_candidates_linked": len(current) == len(nearby),
        }


def _canvas_feedback_pairs(case_id, source_id, registered):
    try:
        if registered is None:
            return []
        original, locator, document = registered
        material = _feedback_material(case_id, locator, document, source_session=original)
    except ServiceError as error:
        if error.status not in (404, 409):
            raise
        return []
    if material is None:
        return []
    raw, headings, references, _, _ = material
    neighbors = _feedback_ledger_neighbors(raw, len(headings))
    unbound_cards = {row[0].strip() for number, row in raw.items()
                     if number > 1 and len(row) == len(headings) and not row[1].strip()}
    witnesses = {}
    for (column, _), positions in references.items():
        if column != 20 or len(positions) != 2:
            continue
        pair = tuple(positions)
        first, second = (raw[position] for position in pair)
        cards = first[0].strip(), second[0].strip()
        if cards != (second[6].strip(), first[6].strip()) or unbound_cards.intersection(cards):
            continue
        if not _compatible_feedback_voucher(first, second):
            continue
        # Independent conflicting references and explicit reversal evidence
        # prevent even the narrower recorded-action count.
        for offset in (16, 21):
            left, right = first[offset].strip(), second[offset].strip()
            if (left or right) and (not _matching_reference(left, right) or references[offset, left] != positions):
                break
        else:
            if any(feedback_import.cancellation_text(dict(zip(headings, row, strict=True))) for row in (first, second)):
                continue
            local = tuple(_balance_witness(raw, neighbors, position) for position in pair)
            if all(local):
                witnesses[pair] = local
    if not witnesses:
        return []
    positions = sorted({position for pair in witnesses for position in pair})
    postings = db.q_raw("""select r.source_row,r.record_key,r.disposition,s.flow_id,
            json_extract_string(r.details_json,'$.direction_basis') direction_basis,
            json_extract_string(r.details_json,'$.source_ledger_evidence') ledger_evidence,
            json_extract_string(json_extract_string(r.details_json,'$.source_ledger_evidence'),'$.header_scope') header_scope
        from import_records r join flow_sources s on s.case_id=r.case_id and s.source_id=r.source_id
          and s.source_row=r.record_no
        where r.case_id=? and r.source_id=? and r.member_path=?
          and r.source_row in (select unnest(?::bigint[]))""", [case_id, source_id, locator["member_path"], positions])
    journal = defaultdict(list)
    for item in postings:
        journal[item["source_row"]].append(item)
    pairs = []
    for pair, local in witnesses.items():
        if any(len(journal[position]) != 1 for position in pair):
            continue
        selected = [journal[position][0] for position in pair]
        flow_ids = tuple(item["flow_id"] for item in selected)
        if len(set(flow_ids)) != 2 or not _financial_records_fit(locator, len(raw), selected):
            continue
        books = _feedback_matching_books(material, selected)
        if not books:
            continue
        if locator["suffix"] == ".xlsx" and any(
            book["row"]["balance"] is None or Decimal(book["row"]["balance"]) !=
            table_import._money(raw[position][4], {"factor": Decimal(1)})[0]
            for position, book in zip(pair, books, strict=True)
        ):
            continue
        pairs.append({"flow_ids": flow_ids, "source_rows": pair, "amount": Decimal(books[0]["row"]["amount"]),
                      "currency": books[0]["row"]["currency"], "source_id": source_id,
                      "member_path": locator["member_path"], "witness_rows": local})
    return pairs


def _canvas_source_pairs(case_id, lookup, sources, *, include_competition):
    hits = db.q_raw("""select distinct s.source_id from flow_sources s
        join import_sources p on p.case_id=s.case_id and p.source_id=s.source_id
        where s.case_id=? and s.flow_id in (select unnest(?::bigint[]))
          and p.parser_version=? and p.origin='upload' order by s.source_id""",
        [case_id, sorted(lookup), table_import.VOUCHER_REFERENCE_PROFILE])
    for hit in hits:
        source_id = hit["source_id"]
        if source_id not in sources:
            try:
                registered = _registered_feedback_member(case_id, source_id)
                if registered and _bank32_layout(registered[1], registered[2]):
                    original, locator, document = registered
                    _, evidence = trace_review._original_layout(case_id, locator)
                    sources[source_id] = _bank32_journal(case_id, original, locator, document, evidence) or []
                else:
                    sources[source_id] = _canvas_feedback_pairs(case_id, source_id, registered)
            except ServiceError as error:
                if error.status not in (404, 409):
                    raise
                sources[source_id] = []
        result = sources[source_id]
        if isinstance(result, _Bank32Journal):
            positions = _bank32_flow_positions(case_id, result, lookup)
            pairs, _ = _bank32_local_pairs(case_id, result, positions)
            if include_competition:
                for pair in pairs:
                    _bank32_context_candidates(case_id, result, pair, lookup)
        else:
            pairs = result
        for pair in pairs:
            if lookup.intersection(pair["flow_ids"]):
                yield pair


def canvas_ledger_pairs(case_id, flow_ids, *, include_competition=False, cross_source_display=False, source_cache=None):
    """Find source-proven pairs touching this selection without editing any flow."""
    selected = set(flow_ids)
    if not selected:
        return []
    sources = {} if source_cache is None else source_cache
    with db.read_lock():
        found = list(_canvas_source_pairs(case_id, selected, sources, include_competition=include_competition))
        if cross_source_display and len(selected) > 1:
            found.extend(_canvas_cross_source_pairs(case_id, selected, sources))
        if not found:
            return []
        # A single selected side cannot hide an ambiguity attached to its
        # unselected counterpart in another active source.
        opposite = {flow for pair in found for flow in pair["flow_ids"]} - selected
        if opposite:
            found.extend(_canvas_source_pairs(case_id, opposite, sources, include_competition=False))
        all_ids = {flow for pair in found for flow in pair["flow_ids"]}
        effective = {row["id"]: (row["currency"], row["amount"]) for row in db.q_raw("""
              select id,currency,amount from source_effective_flows
              where case_id=? and id in (select unnest(?::bigint[]))""", [case_id, sorted(all_ids)])}

    pairs, partners, conflicting = {}, defaultdict(set), set()
    for pair in found:
        left, right = key = tuple(sorted(pair["flow_ids"]))
        if not {left, right} <= effective.keys():
            continue
        if any(effective[flow] != (pair["currency"], pair["amount"]) for flow in key):
            conflicting.update(key)
            continue
        pairs.setdefault(key, pair)
        partners[left].add(right)
        partners[right].add(left)
    return [pair for key, pair in pairs.items() if selected.intersection(key)
            and all(flow not in conflicting and len(partners[flow]) == 1 for flow in key)]


def _recorded_next_spend(journal, account, currency, since):
    book = account, currency
    if book in journal.incomplete_books or (account, None) in journal.incomplete_books or book not in journal.ledger:
        return None
    clocks, groups = journal.ledger[book]
    credits = []
    gaps = journal.clock_gaps.get(account, ())
    for stamp in clocks[bisect_right(clocks, since):]:
        postings = [(position, _feedback_direction(journal.rows[position][5])) for position in groups[stamp]]
        debits = [position for position, side in postings if side == "out"]
        if any(side is None for _, side in postings) or len(debits) > 1:
            return None
        credits.extend(position for position, side in postings if side == "in")
        if len(credits) >= _BANK32_CONTEXT_LIMIT:
            return None
        if debits:
            if bisect_left(gaps, since) != bisect_right(gaps, stamp):
                return None
            [debit] = debits
            return debit, credits
    return None


def recorded_destination(case_id, selected, *, source_cache=None):
    """Follow verified payer observations to one shared debit in their receiving source ledger."""
    if not 1 <= len(selected) <= 4:
        return None
    from . import trace_group_evidence

    journals = {} if source_cache is None else source_cache
    pairs = canvas_ledger_pairs(case_id, selected, source_cache=journals)
    if (not 1 <= len(pairs) <= 2 or any(pair.get("source_reference_kind") != "ledger_log" for pair in pairs)
            or len({(pair["source_id"], pair["member_path"], pair["currency"]) for pair in pairs}) != 1):
        return None
    covered = {key for pair in pairs for key in pair["flow_ids"]}
    if not selected <= covered or event_resolution.members(case_id, covered):
        return None
    journal = journals[pairs[0]["source_id"]]
    if len(journal.rows) != journal.last_row - 1:
        return None
    currency = pairs[0]["currency"]
    bridge, postings, receivers, payers = [], [], set(), set()
    for pair in pairs:
        first, second = zip(pair["source_rows"], pair["flow_ids"], strict=True)
        payer, receiver = (first, second) if _feedback_direction(journal.rows[first[0]][5]) == "out" else (second, first)
        payer_row, payer_id = payer
        receiver_row, receiver_id = receiver
        if payer_id not in selected or (units := trace_group_evidence._positive_units(pair["amount"])) is None:
            return None
        payers.add(journal.rows[payer_row][1])
        receiver_cells = journal.rows[receiver_row]
        receivers.add(receiver_cells[1])
        postings.append({"direction": "in", "units": units, "clock": _bank32_clock(receiver_cells[2]),
                         "flow_id": receiver_id, "source_row": receiver_row})
        bridge.append({"payer_id": str(payer_id), "payer_row": payer_row,
                       "receiver_id": str(receiver_id), "receiver_row": receiver_row})
    if len(payers) != 1 or len(receivers) != 1:
        return None
    [account] = receivers
    earliest = min(item["clock"] for item in postings)
    if not (next_spend := _recorded_next_spend(journal, account, currency, earliest)):
        return None
    target_row, credits = next_spend
    target = journal.rows[target_row]
    target_clock = _bank32_clock(target[2])
    endpoint = target[6]
    if (any(item["clock"] >= target_clock for item in postings) or not _bank32_posting_text(target)
            or not _ACCOUNT_DIGITS.fullmatch(endpoint) or not endpoint.strip("0")):
        return None
    alternatives = set(credits) - {item["source_row"] for item in postings}
    original = _bank32_journal_records(case_id, journal, {target_row, *alternatives})
    if set(original) != {target_row, *alternatives}:
        return None
    debit = original[target_row]
    debit_id = debit["flow_id"]
    if debit_id in covered or (target_units := trace_group_evidence._positive_units(debit["book"]["signed_amount"])) is None:
        return None
    for item, posting in zip(bridge, postings, strict=True):
        item["upper"] = trace_group_evidence._money(min(posting["units"], target_units))
    order = sorted(range(len(bridge)), key=lambda index: postings[index]["clock"])
    bound = trace_group_evidence.selected_order_upper_bound(
        [*postings, {"direction": "out", "units": target_units, "clock": target_clock,
                     "flow_id": debit_id, "source_row": target_row}], currency)
    return {"source_id": journal.source_id, "member_path": journal.locator["member_path"], "currency": currency,
            "receiver_account": account, "bridges": [bridge[index] for index in order],
            "debit": {"id": str(debit_id), "source_row": target_row, "counterparty": endpoint,
                      "counterparty_column": journal.columns["交易对手账卡号"],
                      "amount": trace_group_evidence._money(target_units)},
            "other_credits": [{"id": str(original[pos]["flow_id"]), "source_row": pos} for pos in credits if pos in alternatives],
            "joint_upper": bound["joint_upper"], "basis": "selected_postings_valid_and_reported_clock_orders_availability",
            "actual_share": None, "settlement": "unproved"}


def _observations_remain_active(case_id, source_id, selected):
    links = db.q_raw("""select r.record_no,s.flow_id,
            json_extract_string(r.details_json,'$.source_ledger_evidence') ledger_evidence
        from import_records r
        join flow_sources s on s.case_id=r.case_id and s.source_id=r.source_id and s.source_row=r.record_no
        where r.case_id=? and r.source_id=? and r.disposition in ('imported','duplicate')
        and r.record_no in (select unnest(?::bigint[]))""",
        [case_id, source_id, [row["record_no"] for row in selected]])
    expected = {row["record_no"]: row["ledger_evidence"] for row in selected}
    flow_ids = {row["flow_id"] for row in links}
    if (len(links) != 2 or len(flow_ids) != 2
            or {row["record_no"]: row["ledger_evidence"] for row in links} != expected):
        return False
    # Check visibility only for the two live links. Endpoint and card
    # projections do not determine whether a source flow is still effective.
    effective = db.q_raw("""select id from source_effective_flows
        where case_id=? and id in (select unnest(?::bigint[]))""", [case_id, sorted(flow_ids)])
    return {row["id"] for row in effective} == flow_ids


def compare(case_id, source_id, body):
    """Answer the two selected rows and, on a verified native CSV, list same-column references."""
    if len(set(body.record_nos)) != 2:
        raise ServiceError(422, "pair_comparison_invalid_selection", "请选择两条不同的来源原行")
    cid = int(case_id)
    path = body.member_path
    with db._lock:
        original = _active_original(cid, source_id)
        cid, source = imports._journal_source(cid, source_id, include_pdf=True)
        members = db.q_raw("""select member_path,role,encoding,sha256,size_bytes from import_members
                                where case_id=? and source_id=? and member_path=?""", [cid, source_id, path])
        rows = db.q_raw("""select record_no,source_row,record_key,disposition,
                             json_extract_string(details_json,'$.validation_issues') validation_issues,
                             json_extract_string(details_json,'$.source_account_column') account_column,
                             json_extract_string(details_json,'$.direction_basis') direction_basis,
                             json_extract_string(details_json,'$.source_ledger_evidence') ledger_evidence,
                             json_extract_string(json_extract_string(details_json,'$.source_ledger_evidence'),'$.header_scope') header_scope
                           from import_records where case_id=? and source_id=? and member_path=?
                             and record_no in (select unnest(?::bigint[]))""", [cid, source_id, path, body.record_nos])
        if not members or len(rows) != 2 or rows[0]["source_row"] == rows[1]["source_row"]:
            raise ServiceError(422, "pair_comparison_outside_source", "请选择当前来源表区内两条不同原行")
        by_record = {row["record_no"]: row for row in rows}
        selected = [by_record[number] for number in body.record_nos]
        result = {
            "case_id": str(cid), "source_id": source_id, "member_path": path, "scope": "selected_source_rows",
            "status": "pending", "reason": "source_role_unproved", "event_attribution": "unknown",
            "rows": [{"record_no": row["record_no"], "source_row": row["source_row"],
                      "own_account_column": None, "direction_column": None} for row in selected],
        }
        archive = (source["parser_version"] == table_import.BANK_SOURCE_EVIDENCE_ARCHIVE_PROFILE
                   and original["suffix"] == ".zip")
        standalone_csv = (source["parser_version"] == table_import.BANK_SOURCE_PROFILE
                          and original["suffix"] == ".csv" and path == "table.csv")
        feedback_csv = (source["parser_version"] == table_import.VOUCHER_REFERENCE_PROFILE
                        and original["suffix"] == ".csv" and path == "table.csv")
        csv_source = archive or standalone_csv or feedback_csv
        if not csv_source and source["parser_version"] != table_import.VOUCHER_REFERENCE_PROFILE:
            return result
        member = members[0]
        if (standalone_csv or feedback_csv) and member["sha256"] != original["sha256"]:
            return result
        locator = {**member, "source_id": source_id, "suffix": original["suffix"], "parser_version": source["parser_version"]}
        document, evidence = trace_review._original_layout(cid, locator)
        if not csv_source and not _table_member(original, member, document, selected):
            return result
        feedback_workbook = (not csv_source and tuple(document.get("columns", ())) == feedback_import.COLUMNS
                             and len(db.q_raw("select member_path from import_members where case_id=? and source_id=?",
                                              [cid, source_id])) == 1)
        feedback_observations = feedback_csv or feedback_workbook
        if not csv_source and not feedback_workbook:
            stream, _ = imports.get_source_file(cid, source_id)
    if feedback_csv and _bank32_layout(locator, document):
        return _bank32_compare(cid, original, locator, document, evidence, selected, result)
    positions = [row["source_row"] for row in selected]
    if csv_source or feedback_workbook:
        if feedback_observations:
            certified = _feedback_observations(cid, locator, document, selected,
                                               source_session=original if feedback_workbook else None)
            if certified is None:
                return result
            columns, postings, competition, correspondence, differences = certified
            if correspondence:
                result["observation_correspondence"] = correspondence
            if differences:
                result["posting_comparison"] = differences
        else:
            certified = _csv_reference(cid, locator, document, selected, evidence, standalone=standalone_csv)
            if certified is None:
                return result
            columns, postings, competition = certified
        if competition is not None:
            result["reference_competition"] = competition
            result["selected_reference_column"] = competition["column"]
    else:
        physical = trace_review._original_cells(stream, locator, document, (), {1, *positions})
        header = physical.get(1, ())
        columns = _columns(header)
        if columns is None:
            accepted = _accepted_postings(header, physical, selected, document, evidence)
            if accepted is None:
                return result
            columns, postings = accepted
            if compared := _query_posting_differences(original, locator, document, selected, header, postings):
                result["posting_comparison"], competition = compared
                if competition is not None:
                    result["reference_competition"] = competition
        else:
            postings = [_posting(physical.get(row, ()), columns, uniform=True) for row in positions]
        refs = [i for i, cell in enumerate(header)
                if cell["kind"] == "text" and cell["value"].strip() == "交易流水号"]
        if len(refs) == 1 and all(postings):
            [index] = refs
            cells = [physical[position][index] for position in positions if len(physical[position]) > index]
            if (len(cells) == 2 and all(cell["kind"] == "text" for cell in cells)
                    and _matching_reference(cells[0]["value"], cells[1]["value"])):
                result["selected_reference_column"] = index + 1
    with db._lock:
        active = _active_original(cid, source_id)
        if (any(active[key] != original[key] for key in ("active_batch_id", "sha256"))
                or (feedback_observations or result.get("posting_comparison"))
                   and not _observations_remain_active(cid, source_id, selected)):
            raise ServiceError(409, "source_operation_changed", "原件的导入状态已变化，请重新选择")
    for item, posting in zip(result["rows"], postings, strict=True):
        if posting is not None:
            item.update(own_account_column=columns[0], direction_column=columns[1], direction=posting[1])
    comparison = result.get("posting_comparison")
    if comparison and (not comparison["same_amount"] or not comparison["same_currency"]):
        result.update(status="excluded", reason="different_amount_currency")
    elif all(postings):
        (first_account, first_direction), (second_account, second_direction) = postings
        if first_account != second_account:
            result["reason"] = "different_ledger"
        elif first_direction != second_direction:
            result["reason"] = "different_direction"
        else:
            result.update(status="excluded", reason="same_ledger_same_direction")
    return result
