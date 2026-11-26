"""Validate a cited balance declaration against its own registered ledger."""
from contextlib import closing
import csv
from datetime import datetime, timedelta
from hashlib import sha256
from io import StringIO
from itertools import groupby
from pathlib import PurePosixPath
import re
import unicodedata
from urllib.parse import urlencode
from zipfile import BadZipFile

from .. import analysis_snapshot, db, event_resolution, import_originals, principal_ledger, table_import, transaction_time
from . import cases, imports, trace_group_evidence


_MAX_NOTES = 8
_MAX_CASE_NOTES = 32
_NOTE_BYTES = 4096
_NOTE_SCAN_ORIGINAL_BYTES = 512 * 1024 * 1024
_BOOK_BYTES = 32 * 1024 * 1024
_ISSUER = re.compile(r"这是(?:合成)?机构 ([^、，。；]{1,96}) 对所提供明细的用途说明。")
_DATE_TIME = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?"
_SCOPE = re.compile(
    r"本说明只适用于本机构账户 ([^、，。；\s]{1,96})、币种 ([A-Z]{3})、"
    rf"({_DATE_TIME}) 至 ({_DATE_TIME})；不适用于其他账户、币种或期间。"
)
_COLUMNS = ("交易时间", "付款账号", "收款账号", "本方账号", "本方开户银行", "交易金额", "余额", "交易币种")
_COVERAGE = "在上述范围内，提供的明细是该时段记账后的完整逐笔入出记录；按明细时刻及本账记账顺序排列。"
_POST_BOOK = re.compile(
    r"(?:本范围内每条明细的|在上述范围内，表中)“余额”是该笔(?:记账完成|交易入账)后的账面余额"
    r"(?:，不是可用余额，也不是授信额度)?。"
)
_LIMIT = "本账户在此期间没有透支；本说明不保证范围外的交易、现实资金最终归属或账户授信情况。"
_SOURCE_ERRORS = {"import_not_found", "source_missing", "source_changed", "unsafe_upload_storage"}


def _note(payload):
    if len(payload) > _NOTE_BYTES:
        return None
    try:
        lines = payload.decode("utf-8-sig").splitlines()
        if not 4 <= len(lines) <= 8 or any(
            not line or len(line) > 180 or any(
                unicodedata.category(char) in {"Cc", "Cf"} for char in line) for line in lines
        ):
            return None
        issuer, scope = _ISSUER.fullmatch(lines[0]), _SCOPE.fullmatch(lines[1])
        if issuer is None or scope is None or not lines[2].startswith("在上述范围内，提供的明细"):
            return None
        account, currency, since, until = scope.groups()
        start, end = datetime.fromisoformat(since), datetime.fromisoformat(until)
    except (UnicodeError, ValueError):
        return None
    if start > end:
        return None
    return {"issuer": issuer[1], "account": account, "currency": currency, "start": start,
            "end": end, "lines": lines}


def intake_description(payload):
    """Recognize the document envelope independently of its balance conclusion."""
    return _note(payload) is not None


def _issuer_key(value):
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _selected_scope(case_id, sources, targets, card):
    from . import assistance

    selected_ids = set(map(int, sources + targets))
    selected = assistance._flow_rows(case_id, selected_ids)
    ledgers = {(row["self_account"], row["raw_self_account"]) for row in selected.values()}
    if selected.keys() != selected_ids or len(ledgers) != 1:
        return None
    [(ledger, account)] = ledgers
    found = db.q_raw("""select json_extract_string(details_json,'$.bank') issuer,
              json_extract_string(details_json,'$.account_number') account
        from resolution_entities where case_id=? and entity_id=? and kind='account'""", [case_id, ledger])
    if not found or account != found[0]["account"] or not found[0]["issuer"]:
        return None
    return {"ledger": ledger, "account": account, "issuer": found[0]["issuer"], "currency": card["currency"],
            "start": card["interval"]["start"], "end": card["interval"]["end"], "sources": sources, "targets": targets}


def _book_members(case_id, ids):
    return db.q_raw("""select m.source_id,m.member_path,m.sha256 member_sha256,m.size_bytes,m.encoding,
               s.sha256 source_sha256,u.suffix
        from flow_sources f
        join import_records r on r.case_id=f.case_id and r.source_id=f.source_id and r.record_no=f.source_row
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
        join import_sources s on s.case_id=m.case_id and s.source_id=m.source_id and s.origin='upload'
        join import_source_operations o on o.case_id=m.case_id and o.source_id=m.source_id and o.active
        join import_sessions u on u.case_id=m.case_id and u.import_id=m.source_id and u.stage='done'
        where f.case_id=? and f.flow_id in (select unnest(?::bigint[]))
          and r.disposition in ('imported','duplicate') and right(lower(m.member_path),4)='.csv'
          and u.suffix in ('.zip','.rar') and json_extract_string(u.request_json,'$._intake_policy')=?
        group by all having count(distinct f.flow_id)=? order by m.source_id,m.member_path limit ?""",
        [case_id, ids, imports._INTAKE_POLICY, len(ids), _MAX_NOTES + 1])


def _case_notes(case_id, books):
    """Find case-wide counterevidence while binding positive leads to their ledger."""
    if not books:
        return [], True
    by_source = {}
    for book in books:
        directory = str(PurePosixPath(book["member_path"]).parent)
        by_source.setdefault((book["source_id"], directory), []).append(book)
    candidates = db.q_raw("""select m.source_id,m.member_path,m.sha256 member_sha256,m.size_bytes,
               s.sha256 source_sha256,s.size_bytes source_size_bytes,u.suffix
        from import_members m
        join import_sources s on s.case_id=m.case_id and s.source_id=m.source_id
        join import_source_operations o on o.case_id=m.case_id and o.source_id=m.source_id and o.active
        left join import_sessions u on u.case_id=m.case_id and u.import_id=m.source_id and u.stage='done'
        where m.case_id=?
          and right(lower(m.member_path),4)='.txt'
          and exists(select 1 from import_records r where r.case_id=m.case_id and r.source_id=m.source_id
              and r.member_path=m.member_path and r.disposition='auxiliary'
              and json_extract_string(r.details_json,'$.intake_material_role')='description')
        order by m.source_id,m.member_path limit ?""", [case_id, _MAX_CASE_NOTES + 1])
    source_sizes = {item["source_id"]: item["source_size_bytes"] for item in candidates}
    if len(candidates) > _MAX_CASE_NOTES or sum(source_sizes.values()) > _NOTE_SCAN_ORIGINAL_BYTES:
        return [], False
    result = []
    for _, group in groupby(candidates, key=lambda item: item["source_id"]):
        first = next(group)
        try:
            with closing(_open_source(case_id, first)) as original:
                for item in (first, *group):
                    if note := _note(_read_leaf(original, item, _NOTE_BYTES)):
                        key = (item["source_id"], str(PurePosixPath(item["member_path"]).parent))
                        matching = by_source.get(key, ())
                        book = matching[0] if len(matching) == 1 else None
                        result.append((item, book, note))
        except cases.ServiceError as error:
            if error.code not in _SOURCE_ERRORS:
                raise
            return [], False
        except (OSError, ValueError, BadZipFile):
            return [], False
    return result, True


def _open_source(case_id, item):
    session = imports._source_session(case_id, item["source_id"])
    if session["sha256"] != item["source_sha256"] or session["suffix"] != item["suffix"]:
        raise ValueError("original source registration changed")
    return imports._source(session, snapshot=True)


def _read_leaf(original, item, limit):
    if item["size_bytes"] > limit:
        raise ValueError("registered evidence exceeds the trial limit")
    original.seek(0)
    if item["suffix"] == ".txt":
        raw = original.read(limit + 1)
    else:
        with import_originals._member(original, item["member_path"]) as member:
            raw = member.read(limit + 1)
    if len(raw) != item["size_bytes"] or sha256(raw).hexdigest() != item["member_sha256"]:
        raise ValueError("registered evidence member changed")
    return raw


def _balance_lines(note):
    for number, line in enumerate(note["lines"][3:], 4):
        if "“余额”" in line:
            yield number


def evidence_options(case_id, card):
    """Expose physical notes before interpreting the stated balance semantics."""
    interval_ids = card.get("_range_record_ids")
    if (not interval_ids or card["conditional_group_share"] is not None or card["interval"]["basis"] != "unqualified"
            or not all(card["checks"][key] for key in ("ledger_roles", "currency_amount", "interval_records"))):
        return {}, [], []
    books = _book_members(case_id, list(map(int, interval_ids)))
    if len(books) > _MAX_NOTES:
        return {"trace_balance_scan_complete": False}, [], []
    notes, complete = _case_notes(case_id, books)
    if not notes:
        return {"trace_balance_scan_complete": complete}, [], []
    scope = _selected_scope(case_id, card["source_ids"], card["target_ids"], card)
    if scope is None:
        return {}, [], []
    records = []
    for member, book, note in notes:
        if book is None:
            continue
        base = f'/api/v1/cases/{case_id}'
        source = member["source_id"]
        query = urlencode({"member_path": member["member_path"]})
        for number in _balance_lines(note):
            positions = (1, 2, 3, *(pos for pos, line in enumerate(note["lines"][3:], 4)
                                    if pos == number or line == _LIMIT))
            records.append({"id": f"B{len(records) + 1}",
                            "raw": {"lines": [{"source_row": pos, "text": note["lines"][pos - 1]} for pos in positions]},
                            "structured": {"evidence_kind": "trace_balance_description", "heading": "余额",
                                           "ledger_member": book["member_path"]},
                            "locator": {"source_id": source, "sha256": member["source_sha256"],
                                        "member_path": member["member_path"], "member_sha256": member["member_sha256"],
                                        "source_row": number,
                                        "record_url": f'{base}/imports/{source}/records?{query}',
                                        "file_url": f'{base}/sources/{source}/member-file?{query}'}})
    if len(records) > _MAX_NOTES:
        return {"trace_balance_scan_complete": False}, [], []
    candidates = [{"id": row["id"], "description": "核对余额原说明的作用范围及物理列",
                   "refs": [row["id"]], "refuted": False} for row in records]
    return {"trace_balance_scan_complete": complete, "trace_balance_context": scope}, candidates, records


def model_records(packet):
    """Give the judge comparable scope text while withholding original identifiers."""
    scope = packet["facts"].get("trace_balance_context")
    if scope is None:
        return {}
    start, end = (datetime.fromisoformat(scope[key]) for key in ("start", "end"))
    label_day = start.date()

    def relative(stamp):
        delta = (stamp.date() - label_day).days
        return f"第{delta + 1}日 {stamp:%H:%M:%S}"

    facts = {"ledger": "账簿A", "issuer": "机构A", "currency": scope["currency"], "heading": "余额",
             "query_start": relative(start), "query_end": relative(end)}
    records, issuers, accounts = {}, {_issuer_key(scope["issuer"]): "机构A"}, {scope["account"]: "账簿A"}

    def alias(group, value, label):
        return group.setdefault(value, label + chr(ord("A") + len(group)))

    for row in packet["records"]:
        if row["structured"].get("evidence_kind") != "trace_balance_description":
            continue
        lines = [entry.copy() for entry in row["raw"]["lines"]]
        issuer = _ISSUER.fullmatch(lines[0]["text"])[1]
        account, currency, since, until = _SCOPE.fullmatch(lines[1]["text"]).groups()
        bank_alias = alias(issuers, _issuer_key(issuer), "机构")
        ledger_alias = alias(accounts, account, "账簿")
        lines[0]["text"] = f"{bank_alias}对所提供明细的用途说明。"
        lines[1]["text"] = (f"本说明只适用于本机构账户 {ledger_alias}、币种 {currency}、"
                            f"{relative(datetime.fromisoformat(since))} 至 {relative(datetime.fromisoformat(until))}"
                            "；不适用于其他账户、币种或期间。")
        for line in lines[2:]:
            # The short position-specific wording can carry qualifiers. Remove
            # identifiers outside the closed issuer/account/currency envelope.
            value = line["text"]
            for private in sorted({issuer, account, scope["issuer"], scope["account"]}, key=len, reverse=True):
                value = value.replace(private, "〔标识〕")
            line["text"] = re.sub(r"[A-Za-z0-9][A-Za-z0-9_.@+/-]*", "〔标识〕", value)
        records[row["id"]] = {**facts, "source_row": row["locator"]["source_row"], "lines": lines}
    return records


def _applies(note, scope, *, complete=True):
    start, end = (datetime.fromisoformat(scope[key]) for key in ("start", "end"))
    covered = note["start"] <= start <= end <= note["end"] if complete else note["start"] <= end and start <= note["end"]
    return (note["account"] == scope["account"] and _issuer_key(note["issuer"]) == _issuer_key(scope["issuer"])
            and note["currency"] == scope["currency"] and covered)


def _post_book_row(note):
    lines = note["lines"]
    if lines[2] != _COVERAGE or len(lines) > 5:
        return None
    body = lines[3:]
    for index, line in enumerate(body):
        if _POST_BOOK.fullmatch(line) and body[:index] + body[index + 1:] in ([], [_LIMIT]):
            return index + 4
    return None


def rule_balance_refs(packet):
    """Find explicit scoped declarations already present in the evidence packet."""
    facts = packet["facts"]
    scope = facts.get("trace_balance_context")
    if scope is None or not facts.get("trace_balance_scan_complete"):
        return []
    choices = []
    for record in packet["records"]:
        if (record["structured"].get("evidence_kind") != "trace_balance_description"
                or not record["locator"].get("file_url")):
            continue
        snippet = "\n".join(line["text"] for line in record["raw"]["lines"]).encode()
        note = _note(snippet)
        if note and _applies(note, scope) and _post_book_row(note) == record["locator"]["source_row"]:
            choices.append(record["id"])
    return choices


def _book_columns(headers):
    if not all(headers.count(key) == 1 for key in _COLUMNS):
        return None
    columns = [headers.index(key) for key in _COLUMNS]
    candidates = table_import._candidates(headers, table_import.VOUCHER_REFERENCE_EVIDENCE_PROFILE)
    for field, index in zip(("trade_time", "payer_account", "payee_account", "self_account", None,
                             "amount", "balance", "currency"), columns, strict=True):
        if field is None:
            continue
        options = candidates.get(field, [])
        if len(options) != 1 or options[0]["index"] != index or options[0]["factor"] != 1:
            return None
    return columns


def _physical_book(case_id, book, note, rows, ordered):
    links = db.q_raw("""select r.source_row, f.flow_id::varchar id, t.payer_account,t.payee_account
        from import_records r join flow_sources f
          on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no
        join flows t on t.case_id=f.case_id and t.id=f.flow_id
        where r.case_id=? and r.source_id=? and r.member_path=? and r.disposition in ('imported','duplicate')""",
        [case_id, book["source_id"], book["member_path"]])
    locations = {entry["source_row"]: entry for entry in links}
    if len(links) != len(locations):
        return None
    recorded = {entry["id"]: entry for entry in rows}
    matched = []
    try:
        with closing(_open_source(case_id, book)) as original:
            raw = _read_leaf(original, book, _BOOK_BYTES)
        reader = csv.reader(StringIO(raw.decode(book["encoding"]), newline=""), strict=True)
        headings = [cell.strip() for cell in next(reader, [])]
        columns = _book_columns(headings)
        if columns is None or reader.line_num != 1:
            return None
        for position, cells in enumerate(reader, 2):
            if reader.line_num != position or len(cells) != len(headings):
                return None
            clock, payer, payee, own, bank, amount, balance, currency = (cells[i].strip() for i in columns)
            if own != note["account"] or currency != note["currency"]:
                continue
            stamp = transaction_time.parsed(clock)
            if stamp is None or stamp.tzinfo is not None:
                return None
            if not note["start"] <= stamp <= note["end"]:
                continue
            origin = locations.get(position)
            row = None if origin is None else recorded.get(origin["id"])
            direction = trace_group_evidence._direction(
                {"payer_account": payer, "payee_account": payee, "self_account": own}, own)
            if (row is None or row["raw_self_account"] != own or _issuer_key(bank) != _issuer_key(note["issuer"])
                    or stamp != row["trade_time"] or direction != row["direction"]
                    or (payer, payee) != (origin["payer_account"], origin["payee_account"])
                    or principal_ledger.units(amount) != row["amount"]
                    or principal_ledger.units(balance) != principal_ledger.units(row["balance"])):
                return None
            matched.append(row["id"])
    except cases.ServiceError as error:
        if error.code not in _SOURCE_ERRORS:
            raise
        return None
    except (ArithmeticError, OSError, TypeError, ValueError, csv.Error, BadZipFile):
        return None
    if matched != [entry["id"] for entry in ordered]:
        return None
    return columns[_COLUMNS.index("余额")] + 1


def verify_and_recompute(case_id, packet, choice, refs, *, ledger_scope=None):
    """Accept only the cited original scope, then run the usual ledger solver."""
    from . import assistance

    facts = packet["facts"]
    scope = facts.get("trace_balance_context")
    record = next((row for row in packet["records"] if row["id"] == choice), None)
    if (scope is None or not facts.get("trace_balance_scan_complete") or choice not in refs or record is None
            or record["structured"].get("evidence_kind") != "trace_balance_description"):
        return None
    case_id = int(case_id)
    with db._lock:
        analysis_snapshot.revision(case_id, facts["revision"])
        selected = assistance._flow_rows(case_id, [int(key) for key in scope["sources"] + scope["targets"]])
        if len(selected) != len(scope["sources"]) + len(scope["targets"]):
            return None
        incoming = [selected[int(key)] for key in scope["sources"]]
        outgoing = [selected[int(key)] for key in scope["targets"]]
        account, currency = scope["ledger"], scope["currency"]
        if ledger_scope is not None and (ledger_scope["revision"] != facts["revision"] or ledger_scope["ledger_id"] != account):
            return None
        interval = trace_group_evidence.prepare(case_id, account, incoming + outgoing, strict_source_clock=True)
        baseline = trace_group_evidence.evidence(account, incoming, outgoing, interval, retain_scope=True)
        if not baseline["checks"]["interval_records"] or baseline["conditional_group_share"] is not None:
            return None
        books = _book_members(case_id, list(map(int, baseline["_range_record_ids"])))
        notes, complete = _case_notes(case_id, books)
        if not complete or len(books) > _MAX_NOTES:
            return None
        locator = record["locator"]
        approved = next(((member, book, note) for member, book, note in notes
                         if book is not None
                         and all(member[key] == locator[key] for key in ("source_id", "member_path", "member_sha256"))
                         and book["member_path"] == record["structured"]["ledger_member"]), None)
        if approved is None:
            return None
        member, book, note = approved
        if not _applies(note, scope) or _post_book_row(note) != locator["source_row"]:
            return None
        if any(other is not member
               and _applies(statement, scope, complete=False) and _post_book_row(statement) is None
               for other, _, statement in notes):
            return None
        coverage = principal_ledger.read(case_id, account, currency, note["start"], note["end"])
        rows = coverage["rows"]
        if coverage["status"] != "ready" or principal_ledger.has_day_only_order_conflict(rows):
            return None
        metadata = event_resolution.ledger_metadata(case_id, [int(row["id"]) for row in rows])
        ordered = trace_group_evidence._ordered(rows, metadata)
        if ordered is None or any(entry.get("conflict") for entry in metadata.values()):
            return None
        column = _physical_book(case_id, book, note, rows, ordered)
        if column is None:
            return None
        for row in ordered:
            metadata.setdefault(row["id"], {})["balance_kind"] = "post_book"
        if not trace_group_evidence._own_balances(ordered, metadata)[2]:
            return None
        interval["metadata"] = {**interval["metadata"], **metadata}
        verified = trace_group_evidence.evidence(account, incoming, outgoing, interval, retain_scope=True)
        if verified["conditional_group_share"] is None:
            return None
        ids = set(verified["_range_record_ids"])
        available_records = sorted((row for row in packet["records"]
                                    if row["id"].startswith("F") and row["id"][1:] in ids and row["locator"].get("file_url")),
                                   key=lambda row: int(row["id"][1:]))
        needed = {row["locator"]["source_id"]: row["locator"]["sha256"] for row in available_records}
        needed[book["source_id"]] = book["source_sha256"]
        if ledger_scope is not None:
            needed.update(ledger_scope["proofs"])
        tickets = assistance.verified_product_evidence(case_id, needed)
        if tickets.keys() != needed.keys():
            return None
        result = {key: verified[key] for key in ("interval", "checks", "opening_balance", "closing_balance",
                                                  "conditional_group_share", "other_entries", "reasons",
                                                  "attribution_status")}
        result.update(range_originals_verified=True, ledger_row_count=len(ids),
                      range_ref_ids=[*(row["id"] for row in available_records), choice],
                      trace_balance_proof={"note_ref": choice, "note_member": member["member_path"],
                                           "note_member_sha256": member["member_sha256"], "note_row": locator["source_row"],
                                           "ledger_member": book["member_path"], "ledger_member_sha256": book["member_sha256"],
                                           "source_id": book["source_id"], "header_row": 1, "column": column,
                                           "coverage_start": transaction_time.text(note["start"]),
                                           "coverage_end": transaction_time.text(note["end"]),
                                           "declared_ledger_rows": len(rows)})
        return result, tickets
