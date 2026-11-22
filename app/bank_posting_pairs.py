"""Versioned, member-local bank postings; this does not establish settlement.

The ordinary primary reference is a candidate within this one source family.
It becomes a posting pair only with reciprocal account roles and independent
balance witnesses. Other source families keep their existing event contract.
"""
from collections import defaultdict
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from itertools import groupby
import hashlib
import json
import unicodedata

from . import db, source_ledger, table_direction_recheck
from .money import money_decimal

POLICY = "bank-feedback31-paired-postings-v1"
FIELD = "bank_posting_pair_evidence"
PROOF_FIELD = "current_bank_posting_pair_proof"
PENDING_FIELD = "current_bank_posting_pair_pending"
PROFILES = frozenset((table_direction_recheck.PROFILE, "funds-trace-table-archive-v66", "funds-trace-table-archive-v67", "funds-trace-table-archive-v68",
                     "funds-trace-table-v78", "funds-trace-table-v79", "funds-trace-table-v80",
                     "funds-trace-table-archive-v63", "funds-trace-table-archive-v64", "funds-trace-table-archive-v65",
                     "funds-trace-table-v75", "funds-trace-table-v76", "funds-trace-table-v77",
                     "funds-trace-table-archive-v60", "funds-trace-table-archive-v61", "funds-trace-table-archive-v62", "funds-trace-table-v69", "funds-trace-table-v72", "funds-trace-table-v70", "funds-trace-table-v73", "funds-trace-table-v71", "funds-trace-table-v74", "funds-trace-table-v66", "funds-trace-table-v67", "funds-trace-table-v68",
                     "funds-trace-table-archive-v57", "funds-trace-table-archive-v58", "funds-trace-table-archive-v59",
                     "funds-trace-table-v63","funds-trace-table-v60", "funds-trace-table-v64","funds-trace-table-v61", "funds-trace-table-v65","funds-trace-table-v62",
                     "funds-trace-table-archive-v54","funds-trace-table-archive-v51", "funds-trace-table-archive-v55","funds-trace-table-archive-v52", "funds-trace-table-archive-v56","funds-trace-table-archive-v53", "funds-trace-table-v57", "funds-trace-table-v58", "funds-trace-table-v59",
                     "funds-trace-table-archive-v48", "funds-trace-table-archive-v49", "funds-trace-table-archive-v50", "funds-trace-table-v54", "funds-trace-table-v55", "funds-trace-table-v56",
                     "funds-trace-table-archive-v45", "funds-trace-table-archive-v46", "funds-trace-table-archive-v47", "funds-trace-table-v51", "funds-trace-table-v52", "funds-trace-table-v53",
                     "funds-trace-table-archive-v42", "funds-trace-table-archive-v43", "funds-trace-table-archive-v44"))


class _CurrentFacts(Mapping):
    """Read-only raw flow facts; keep values without a per-row hash table."""
    _FIELDS = ("id", "trade_no", "payer_account", "payee_account", "self_account",
               "currency", "amount", "balance", "trade_time", "trade_time_utc")
    __slots__ = ("id", "trade_no", "payer_account", "payee_account", "self_account",
                 "currency", "amount", "_balance", "trade_time", "trade_time_utc", "_balance_text")

    def __init__(self, row, source_balance=None):
        for field in self._FIELDS:
            object.__setattr__(self, "_balance" if field == "balance" else field, row[field])
        object.__setattr__(self, "_balance_text", None)
        balance = row["balance"]
        # The caller supplies already validated source text. Match the entire
        # Decimal representation; numeric equality would lose scale/zero sign.
        if (type(balance) is Decimal and balance.is_finite() and type(source_balance) is str
                and Decimal(source_balance).as_tuple() == balance.as_tuple()):
            object.__setattr__(self, "_balance", None)
            object.__setattr__(self, "_balance_text", source_balance)

    @property
    def balance(self):
        # Books already own this immutable text. Do not retain a decoded cache.
        return Decimal(self._balance_text) if self._balance_text is not None else self._balance

    def __getitem__(self, key):
        if key not in self._FIELDS:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self):
        return iter(self._FIELDS)

    def __len__(self):
        return len(self._FIELDS)

    def __setattr__(self, key, value):
        raise AttributeError("Current facts are read-only")

    def __delattr__(self, key):
        raise AttributeError("Current facts are read-only")


class _ProofFields(Mapping):
    """Fixed private proof mappings share operations, never per-row storage."""
    __slots__ = ()

    def __getitem__(self, key):
        if key not in self._FIELDS:
            raise KeyError(key)
        return getattr(self, key)

    def __setitem__(self, key, value):
        if key not in self._FIELDS:
            raise KeyError(key)
        setattr(self, key, value)

    def __iter__(self):
        return iter(self._FIELDS)

    def __len__(self):
        return len(self._FIELDS)


class _ProofRow(_ProofFields):
    _FIELDS = ("trade_no", "payer_account", "payee_account", "self_account", "amount", "balance",
               "currency", "trade_time", "trade_time_utc")
    __slots__ = _FIELDS

    def __init__(self, row):
        for key in self._FIELDS:
            setattr(self, key, row[key])

    def update(self, other=(), **values):
        for key, value in dict(other, **values).items():
            self[key] = value


class _ProofEvidence(_ProofFields):
    """Fixed retained proof fields, including the existing mutable row alias."""
    _FIELDS = ("kind", "header_scope", "source_currency", "direction", "own_card",
               "signed_amount", "source_direction", "balance_before", "row")
    __slots__ = _FIELDS

    def __init__(self, evidence, row):
        for key in self._FIELDS:
            setattr(self, key, row if key == "row" else evidence[key])


class _ProofContract(_ProofFields):
    """The complete validated posting contract in a fixed set of fields."""
    _FIELDS = ("independent_references", "policy", "source_counterparty_bank", "source_primary_reference")
    __slots__ = _FIELDS

    def __init__(self, contract, bank):
        self.independent_references = contract["independent_references"]
        self.policy = POLICY
        self.source_counterparty_bank = bank
        self.source_primary_reference = contract["source_primary_reference"]


class _PostingRow(_ProofFields):
    """Retained posting metadata; optional fields stay absent until assigned."""
    _FIELDS = ("source_id", "record_no", "member_path", "source_row", "disposition",
               "parser_version", "member_sha256", "flow_id", "trade_no_basis", "contract", "evidence",
               "_candidate", "position_path", "_physical_rows", "authority", "reference", "bank", "effective")
    _INITIAL_FIELDS = _FIELDS[:9]
    _KEYS = frozenset(_FIELDS)
    _ABSENT = object()
    __slots__ = _FIELDS

    def __init__(self, fetched):
        for key in self._INITIAL_FIELDS:
            setattr(self, key, fetched[key])

    def __getitem__(self, key):
        if key in self._KEYS:
            value = getattr(self, key, self._ABSENT)
            if value is not self._ABSENT:
                return value
        raise KeyError(key)

    def __delitem__(self, key):
        self[key]
        delattr(self, key)

    def __iter__(self):
        # Production assigns fields in this order; _candidate is removed and
        # the remaining optional fields are never deleted and reinserted.
        return (key for key in self._FIELDS if getattr(self, key, self._ABSENT) is not self._ABSENT)

    def __len__(self):
        return sum(1 for _ in self)

    def update(self, other=(), **values):
        for key, value in other.items() if isinstance(other, Mapping) else other:
            self[key] = value
        for key, value in values.items():
            self[key] = value

    def pop(self, key, default=_ABSENT):
        try:
            value = self[key]
        except KeyError:
            if default is self._ABSENT:
                raise
            return default
        delattr(self, key)
        return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _identifier(value):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value if (0 < len(value) <= 128 and any(c.isalnum() for c in value)
        and value.casefold() not in {"null", "none", "nan", "n/a", "unknown", "未知", "不详", "无"}
        and not any(unicodedata.category(c) in {"Cc", "Cf"} for c in value)
        and not all(c in "0.-_ " for c in value)) else ""


def independent_reference(value):
    # An unsupported long reference is still present, never an absent ID.
    return isinstance(value, str) and value.strip() not in {"", "0", "-", "--"}


def capture(headers, cells):
    from . import table_import
    if len(headers) != len(set(headers)) or frozenset(headers) != table_import.BANK_FEEDBACK_31_COLUMNS:
        return None
    raw = dict(zip(headers, cells))
    return _json({"policy": POLICY, "source_primary_reference": _identifier(raw.get("交易流水号")),
                  "source_counterparty_bank": _identifier(raw.get("对手开户银行")),
                  "independent_references": any(independent_reference(raw.get(key)) for key in ("日志号", "传票号", "凭证号"))})


def validate(value):
    if not isinstance(value, str) or len(value.encode("utf-8")) > 2048:
        raise ValueError("无效的银行双侧过账契约")
    data = json.loads(value)
    if (not isinstance(data, dict) or set(data) != {"policy", "source_primary_reference", "source_counterparty_bank", "independent_references"}
            or type(data["independent_references"]) is not bool
            or data["policy"] != POLICY or any(not isinstance(data[key], str)
                or data[key] != _identifier(data[key]) for key in ("source_primary_reference", "source_counterparty_bank"))):
        raise ValueError("无效的银行双侧过账契约")
    return data


def has_sources(case_id):
    return bool(db.q_raw("""select 1 from import_sources s join import_records r
        on r.case_id=s.case_id and r.source_id=s.source_id where s.case_id=?
        and s.parser_version in (select unnest(?::varchar[]))
        and contains(r.details_json,?) limit 1""", [case_id, sorted(PROFILES), '"' + FIELD + '"']))


@contextmanager
def _input_rows(case_id):
    """Drain one source PK page before borrowing the connection again.

    The page bounds record count, not arbitrary source-payload bytes. The outer
    lock or existing private snapshot keeps every page in the same read view.
    """
    from . import table_import
    from .table_cancellation_links import active_support_sql
    with db.read_lock():
        connection = getattr(db._read_state, "connection", None)
        if connection is None:
            connection = db.con()

        def iterate():
            for source_case, source_id, record_nos in source_ledger._source_record_pages(connection, case_id):
                with db.raw_rows(f"""with _pair_sources as materialized (
                    select s.case_id,s.source_id,s.parser_version,s.filename,s.sheet,s.row_kind,s.sha256
                    from import_sources s where s.case_id=? and s.source_id=?
                      and s.parser_version in (select unnest(?::varchar[])) and {active_support_sql('s')}
                    ), _pair_records as materialized (
                    select r.case_id,r.source_id,r.record_no,r.member_path,r.source_row,r.disposition,
                        json_extract_string(r.details_json,
                          ['$.{source_ledger.FIELD}','$.{FIELD}','$.trade_no_basis']) posting_details
                    from import_records r where r.case_id=? and r.source_id=? and r.disposition<>'removed'
                      and r.record_no between ?::bigint and ?::bigint
                      and r.record_no in (select unnest(?::bigint[]))
                      and exists (select 1 from _pair_sources s where s.case_id=r.case_id and s.source_id=r.source_id)
                      and exists (select 1 from import_members m where m.case_id=r.case_id
                        and m.source_id=r.source_id and m.member_path=r.member_path)
                    ), _pair_links as materialized (
                    select f.case_id,f.source_id,f.source_row,f.flow_id from flow_sources f
                    where f.case_id=? and f.source_id=? and f.source_row between ?::bigint and ?::bigint
                      and f.source_row in (select unnest(?::bigint[]))
                    ) select r.source_id,r.record_no,r.member_path,r.source_row,r.disposition,
                    s.parser_version,s.filename,s.sheet,s.row_kind,s.sha256 source_sha256,
                    m.sha256 member_sha256,m.role,m.encoding,f.flow_id,
                    r.posting_details[1] ledger_evidence,r.posting_details[2] pair_evidence,
                    r.posting_details[3] trade_no_basis
                    from _pair_records r join _pair_sources s on s.case_id=r.case_id and s.source_id=r.source_id
                    join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
                    left join _pair_links f on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no""",
                    [source_case, source_id, sorted(table_import.SOURCE_LEDGER_PROFILES), source_case, source_id,
                     record_nos[0], record_nos[-1], record_nos, source_case, source_id,
                     record_nos[0], record_nos[-1], record_nos]) as raw:
                    yield from raw

        rows = iterate()
        try:
            yield rows
        finally:
            rows.close()


def _inputs(case_id):
    from .table_cancellation_links import ordinary_csv_member
    # Do not carry the full source-details payload through link joins/sorting.
    # Select the original source/member scope before extracting its contracts.
    rows = []
    member_metadata = {}
    kinds = {value: value for value in ("financial", "context", "ledger")}
    directions = {value: value for value in ("in", "out", "unknown")}
    currencies = {value: value for value in ("CNY", "USD", "EUR")}
    labels = {}

    def shared_label(value):
        # Only this read owns the pool. High-cardinality/long labels and
        # non-builtin strings keep their original objects and representations.
        if type(value) is str and len(value) <= 128:
            previous = labels.get(value)
            if previous is not None:
                return previous
            if len(labels) < 64:
                labels[value] = value
        return value

    references, unlocated, direct_members = set(), set(), set()
    with _input_rows(case_id) as raw:
        for fetched in raw:
            contract = validate(fetched["pair_evidence"]) if fetched["pair_evidence"] is not None else None
            evidence = source_ledger.validate(fetched["ledger_evidence"]) if fetched["ledger_evidence"] is not None else None
            if ordinary_csv_member(fetched["parser_version"], {"filename": fetched["filename"], "sheet": fetched["sheet"],
                    "row_kind": fetched["row_kind"], "sha256": fetched["source_sha256"]}, {"member_path": fetched["member_path"],
                    "sha256": fetched["member_sha256"], "role": fetched["role"], "encoding": fetched["encoding"]}):
                direct_members.add(fetched["member_sha256"])
            if contract is not None:
                contract = _ProofContract(contract, shared_label(contract["source_counterparty_bank"]))
            authority = contract is not None and fetched["parser_version"] in PROFILES
            reference = (contract["source_primary_reference"] if authority else
                         _identifier(evidence["row"]["trade_no"]) if evidence and
                         fetched["trade_no_basis"] == "source_transaction_id" else "")
            bank = (contract["source_counterparty_bank"] if authority else shared_label(_identifier(
                evidence["row"]["payee_bank" if evidence["direction"] == "out" else "payer_bank"])) if evidence else "")
            if authority and reference:
                references.add(reference)
            if fetched["disposition"] == "unresolved" and evidence is None:
                unlocated.add((fetched["source_id"], fetched["member_path"]))
            if evidence is not None:
                source = evidence["row"]
                if (contract is not None and type(source["trade_no"]) is str
                        and type(contract["source_primary_reference"]) is str
                        and source["trade_no"] == contract["source_primary_reference"]):
                    source["trade_no"] = contract["source_primary_reference"]
                if (type(evidence["signed_amount"]) is str and type(source["amount"]) is str
                        and evidence["signed_amount"] == source["amount"]):
                    evidence["signed_amount"] = source["amount"]
                # Validate the complete snapshot above, then retain only the
                # fields consumed by pair/single-posting and balance proofs.
                evidence = _ProofEvidence(evidence, _ProofRow(source))
                for key, literals in (("kind", kinds), ("direction", directions), ("source_currency", currencies)):
                    value = evidence[key]
                    if type(value) is str:
                        evidence[key] = literals.get(value, value)
                value = evidence["row"]["currency"]
                if type(value) is str:
                    evidence["row"]["currency"] = currencies.get(value, value)
                evidence["source_direction"] = shared_label(evidence["source_direction"])
                del source
            row = _PostingRow(fetched)
            for field in ("disposition", "trade_no_basis"):
                row[field] = shared_label(row[field])
            metadata_fields = ("source_id", "member_path", "parser_version", "member_sha256")
            metadata = tuple(row[field] for field in metadata_fields)
            member_key = metadata[:2]
            previous = member_metadata.get(member_key)
            if previous is None:
                member_metadata[member_key] = metadata
            elif all(type(value) is type(old) and value == old for value, old in zip(metadata, previous)):
                for field, value in zip(metadata_fields, previous):
                    row[field] = value
            row.update(contract=contract, evidence=evidence, _candidate=(authority, reference, bank))
            rows.append(row)
            del fetched
    del member_metadata, shared_label, labels
    # Preserve source order without sorting the wide evidence inside DuckDB.
    rows.sort(key=lambda row: (row["source_id"], row["member_path"], row["source_row"]))
    if not references:
        return [], {}, unlocated
    books, candidates, book_entries = defaultdict(list), [], {}
    book_member = None
    for row in rows:
        member = row["source_id"], row["member_path"]
        if member != book_member:
            # Scope includes this sorted prefix; retired entries cannot recur.
            book_entries.clear()
            book_member = member
        evidence = row["evidence"]
        authority, reference, bank = row.pop("_candidate")
        row["position_path"] = None if row["member_sha256"] in direct_members else row["member_path"]
        if evidence:
            scope = source_ledger._scope(row)
            book = books.get(scope)
            source = evidence["row"]
            if book:
                anchor = book[0]["evidence"]
                if (type(evidence["own_card"]) is str and type(anchor["own_card"]) is str
                        and evidence["own_card"] == anchor["own_card"]):
                    evidence["own_card"] = anchor["own_card"]
                own = source["self_account"]
                first_own = anchor["row"]["self_account"]
                if type(own) is str and type(first_own) is str and own == first_own:
                    source["self_account"] = first_own
                # Do not keep the replaced strings alive through the facts key.
                scope = source_ledger._scope(row)
            own = source["self_account"]
            for side in ("payer_account", "payee_account"):
                if type(own) is str and type(source[side]) is str and source[side] == own:
                    source[side] = own
            # The importer's registered posting-repeat contract already shares
            # an ID for repeated source copies. It adds no chronology evidence.
            entry = (scope, row["flow_id"], _facts(row)) if row["flow_id"] is not None else (scope, row["record_no"])
            if entry not in book_entries:
                row["_physical_rows"] = (row["source_row"],)
                books[scope].append(row)
                book_entries[entry] = row
            else:
                positions = book_entries[entry]["_physical_rows"]
                if type(positions) is tuple:
                    if row["source_row"] != positions[0]:
                        book_entries[entry]["_physical_rows"] = {positions[0], row["source_row"]}
                else:
                    positions.add(row["source_row"])
        row["authority"] = authority
        if reference not in references:
            continue
        row.update(reference=reference, bank=bank)
        candidates.append(row)
    for book in books.values():
        for row in book:
            if type(row["_physical_rows"]) is set:
                row["_physical_rows"] = tuple(sorted(row["_physical_rows"]))
    return candidates, books, unlocated


def _position(row):
    evidence = row["evidence"]
    entry = ("posting", row["flow_id"]) if row["flow_id"] is not None and evidence and evidence["kind"] == "financial" else ("row", row["source_row"])
    return (row["position_path"], row["member_sha256"], evidence["header_scope"] if evidence else None, entry)


def _facts(row):
    evidence = row["evidence"]
    if evidence is None:
        return None
    source = evidence["row"]
    return tuple(source[key] for key in ("payer_account", "payee_account", "self_account", "amount", "balance",
        "currency", "trade_time", "trade_time_utc")) + (evidence["direction"], evidence["own_card"])


def _eligible_posting(row):
    return (row["authority"] and row["evidence"] and row["effective"]
        and row["evidence"]["kind"] == "financial" and row["disposition"] in {"imported", "duplicate"}
        and not row["contract"]["independent_references"]
        and row["trade_no_basis"] == "source_transaction_id"
        and row["evidence"]["row"]["trade_no"] == row["reference"])


def _has_opposite_candidate(rows):
    """Corresponding source postings can be pending without proving an event.

    The exact same actual member/header must contain reciprocal raw accounts,
    opposite ownership/directions, equal positive amounts/currency and a shared
    wall clock or explicit instant. Balances are deliberately not proof here.
    """
    if len(rows) < 2:
        return False
    directions = defaultdict(set)
    for row in rows:
        if not _eligible_posting(row):
            continue
        evidence, source = row["evidence"], row["evidence"]["row"]
        if (not evidence["source_currency"] or not source["payer_account"] or not source["payee_account"]
                or source["payer_account"] == source["payee_account"]
                or money_decimal(evidence["signed_amount"]) <= 0):
            continue
        base = (row["source_id"], row["member_path"], evidence["header_scope"],
                source["payer_account"], source["payee_account"], evidence["source_currency"], source["amount"])
        for basis, stamp in (("wall", source["trade_time"]), ("utc", source["trade_time_utc"])):
            if stamp:
                group = directions[(*base, basis, stamp)]
                group.add(evidence["direction"])
                if group == {"in", "out"}:
                    return True
    return False


def project(case_id, *, excluded_ids=(), source_roles=False, single_proof_bytes=False):
    """Retain observations; unproved corresponding pairs withhold certain funds."""
    candidates, books, unlocated = _inputs(case_id)
    if not candidates:
        return [], [], []
    ids = sorted({int(row["flow_id"]) for row in candidates if row["flow_id"] is not None})
    source_rows = {}
    for group in books.values():
        for row in group:
            if row["flow_id"] is not None:
                source_rows.setdefault(int(row["flow_id"]), row)
    book_ids = sorted(source_rows)
    currencies = {value: value for value in ("CNY", "USD", "EUR")}
    current_rows = {}
    with db.read_lock():
        for start in range(0, len(book_ids), 4096):
            page = book_ids[start:start + 4096]
            with db.raw_rows("""select id,trade_no,payer_account,payee_account,
                self_account,currency,amount,balance,trade_time,trade_time_utc from flows
                where case_id=? and id in (select unnest(?::bigint[]))
                  and id between ?::bigint and ?::bigint""", [case_id, page, page[0], page[-1]]) as raw:
                for row in raw:
                    value = row["currency"]
                    if type(value) is str:
                        row["currency"] = currencies.get(value, value)
                    record = source_rows.get(row["id"])
                    if record is not None:
                        if type(row["id"]) is int and type(record["flow_id"]) is int and row["id"] == record["flow_id"]:
                            row["id"] = record["flow_id"]
                        source = record["evidence"]["row"]
                        for field in ("trade_no", "payer_account", "payee_account", "self_account", "currency"):
                            if type(row[field]) is str and type(source[field]) is str and row[field] == source[field]:
                                row[field] = source[field]
                    current_rows[str(row["id"])] = _CurrentFacts(
                        row, source.get("balance") if record is not None else None)
    del source_rows, book_ids
    effective = {}
    with db.read_lock():
        for start in range(0, len(ids), 4096):
            page = ids[start:start + 4096]
            with db.raw_rows("""select id,payer_account,payee_account,self_account,
                currency,amount,trade_time,trade_time_utc from effective_flows
                where case_id=? and id in (select unnest(?::bigint[]))
                  and id between ?::bigint and ?::bigint""", [case_id, page, page[0], page[-1]]) as raw:
                for row in raw:
                    key = str(row["id"])
                    current = current_rows.get(key)
                    # Consumers only read these maps. Extra raw fields are harmless;
                    # Decimal scale and datetime metadata must still match exactly.
                    same = current is not None
                    if current is not None:
                        for field, value in row.items():
                            if (type(value) is type(current[field]) and value == current[field]
                                    and (not isinstance(value, Decimal) or value.as_tuple() == current[field].as_tuple())
                                    and (not isinstance(value, datetime) or
                                         value.tzinfo is current[field].tzinfo and value.fold == current[field].fold)):
                                row[field] = current[field]
                            else:
                                same = False
                    effective[key] = current if same else row
    del ids
    excluded_ids = set(map(str, excluded_ids))
    events, memberships = _pair_projection(candidates, books, unlocated, effective, current_rows,
                                           excluded_ids, source_roles=source_roles)
    if not source_roles:
        from . import bank_single_postings
        single_options = {"proof_bytes": True} if single_proof_bytes else {}
        events.extend(bank_single_postings.project(case_id, candidates, books, unlocated,
            effective, current_rows, memberships, excluded_ids, **single_options))
    del books, unlocated, effective, current_rows, excluded_ids
    return events, [memberships[key] for key in sorted(memberships)], candidates


def _reused_member_references(candidates):
    """Retain only member/reference namespaces with three distinct positions."""
    namespace = lambda row: (row["source_id"], row["member_path"], row["evidence"]["header_scope"], row["reference"])
    ordered = [row for row in candidates if row["evidence"]]
    ordered.sort(key=lambda row: row["reference"])
    ordered.sort(key=lambda row: row["evidence"]["header_scope"])
    ordered.sort(key=lambda row: row["member_path"])
    ordered.sort(key=lambda row: row["source_id"])
    reused = set()
    for key, rows in groupby(ordered, key=namespace):
        positions = set()
        for row in rows:
            positions.add(_position(row))
            if len(positions) > 2:
                reused.add(key)
                break
    return reused


def _pair_projection(candidates, books, unlocated, effective, current_rows, excluded_ids, *, source_roles):
    """Own pair-only indexes so they die before single-posting projection."""
    reused_members = _reused_member_references(candidates)
    unscoped = set()
    for row in candidates:
        if not row["bank"]:
            unscoped.add(row["reference"])
        row["effective"] = str(row["flow_id"]) in effective
    events, memberships = [], {}
    ordered = sorted(candidates, key=lambda row: row["reference"])
    ordered.sort(key=lambda row: row["bank"])
    for (bank, reference), group in groupby(ordered, key=lambda row: (row["bank"], row["reference"])):
        rows = list(group)
        positions = defaultdict(list)
        for row in rows:
            positions[_position(row)].append(row)
            if len(positions) > 2:
                break
        reason, proof = "bank_pair_incomplete_evidence", None
        # Counterparty bank is a row role, not a primary-reference issuer namespace.
        reused_in_member = any(row["evidence"] and (
            row["source_id"], row["member_path"], row["evidence"]["header_scope"], reference) in reused_members
            for row in rows)
        if reused_in_member:
            reason = "bank_pair_reference_reused"
        elif bank and reference not in unscoped and len(positions) == 2:
            sides = list(positions.values())
            # Duplicate support must retain one compatible physical observation.
            compatible = (all(len({_facts(row) for row in side if row["evidence"]}) == 1 for side in sides)
                          and not any(row["contract"]["independent_references"] for row in rows if row["authority"]))
            from . import table_import
            scopes = [{(row["source_id"], row["member_path"], row["evidence"]["header_scope"])
                       for row in side if _eligible_posting(row) and (not source_roles or
                           row["parser_version"] in table_import.NATIVE31_PROFILES
                           and row["bank"].isascii() and row["bank"].isdigit())}
                      for side in sides]
            common = scopes[0] & scopes[1]
            if compatible and common:
                scope = sorted(common)[0]
                pair = [next(row for row in side if _eligible_posting(row)
                             and (row["source_id"], row["member_path"], row["evidence"]["header_scope"]) == scope)
                        for side in sides]
                proof, reason = _prove(pair, books, unlocated, effective, current_rows,
                                       require_projected_roles=not source_roles)
        elif len(positions) > 2:
            reason = "bank_pair_reference_reused"
        flow_ids = sorted({str(row["flow_id"]) for row in rows if row["effective"]} - excluded_ids)
        if any(str(row["flow_id"]) in excluded_ids for row in rows):
            proof, reason = None, "bank_pair_explicit_reference_takes_precedence"
        pending_group = (hashlib.sha256(_json({"policy": POLICY, "source_counterparty_bank": bank,
                            "source_primary_reference": reference}).encode()).hexdigest()
                         if not proof and bank and not any(str(row["flow_id"]) in excluded_ids for row in rows)
                         and _has_opposite_candidate(rows) else None)
        event_id = None
        if proof:
            proof.update(policy=POLICY, source_counterparty_bank=bank, source_primary_reference=reference)
            identity = {key: value for key, value in proof.items() if key not in {"source_id", "member_path"}}
            event_id = "ftev_" + hashlib.sha256(_json(identity).encode()).hexdigest()
            first = (current_rows if source_roles else effective)[flow_ids[0]]
            events.append({"event_id": event_id, **{key: first[key] for key in
                ("payer_account", "payee_account", "currency", "amount", "trade_time")},
                "evidence_json": _json(proof), "flow_ids": flow_ids})
            events[-1]["trade_time"] = first["trade_time_utc"] or first["trade_time"]
        for flow_id in flow_ids:
            memberships[flow_id] = {"flow_id": flow_id, "event_id": event_id,
                "status": "confirmed" if proof else "unknown", "reason": reason, "pending_group": pending_group}
    return events, memberships


def source_role_pairs(case_id):
    """Prove raw reciprocal roles before typed endpoints are projected.

    The caller still requires the same closed, explicit institution binding on
    both observations. A numeric bank field alone never names an institution.
    Old parser versions retain their original endpoint interpretation.
    """
    from . import table_import
    from .table_cancellation_links import active_support_sql
    present = db.q_raw(f"""with _role_sources as materialized (
        select s.case_id,s.source_id from import_sources s where s.case_id=?
          and s.parser_version in (select unnest(?::varchar[])) and {active_support_sql('s')}
        ), _role_records as materialized (
        select r.case_id,r.source_id,r.record_no,r.member_path,
            json_extract_string(r.details_json,'$.{FIELD}') pair_evidence
        from import_records r where r.case_id=? and r.disposition<>'removed'
          and exists (select 1 from _role_sources s where s.case_id=r.case_id and s.source_id=r.source_id)
        ) select 1 from _role_records r join flow_sources f
        on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no
        join flows v on v.case_id=f.case_id and v.id=f.flow_id
        where regexp_full_match(json_extract_string(r.pair_evidence,'$.source_counterparty_bank'),'[0-9]+')
        group by r.source_id,r.member_path,
            json_extract_string(r.pair_evidence,'$.source_primary_reference')
        having count(distinct f.flow_id)>1 and count(distinct v.self_account)>1
        limit 1""", [case_id, sorted(table_import.NATIVE31_PROFILES), case_id])
    if not present:
        return []
    events, _, _ = project(case_id, source_roles=True)
    result = []
    for event in events:
        proof = json.loads(event["evidence_json"])
        if len(event["flow_ids"]) == 2:
            result.append({"flow_ids": event["flow_ids"], "source_id": proof["source_id"],
                "member_path": proof["member_path"], "bank_code": proof["source_counterparty_bank"],
                "own_accounts": {side["own_account"] for side in proof["sides"]}})
    return result


def _matches_source(source, current):
    # An unlinked source-only context has no current raw row to witness.
    return (current is not None
        and all(current[key] == source[key] for key in ("trade_no", "payer_account", "payee_account", "self_account", "currency"))
        and current["amount"] == money_decimal(source["amount"])
        and current["balance"] == (money_decimal(source["balance"]) if source["balance"] is not None else None)
        and all(current[key] == (datetime.fromisoformat(source[key]) if source[key] else None) for key in ("trade_time", "trade_time_utc")))


def _prove(pair, books, unlocated, effective, current_rows, *, require_projected_roles=True):
    a, b = pair
    ea, eb = a["evidence"], b["evidence"]
    ra, rb = ea["row"], eb["row"]
    fail = "bank_pair_incompatible_postings"
    if (ea["direction"] == eb["direction"] or ra["self_account"] == rb["self_account"]
            or not ra["payer_account"] or not ra["payee_account"] or ra["payer_account"] == ra["payee_account"]
            or (ra["payer_account"], ra["payee_account"]) != (rb["payer_account"], rb["payee_account"])
            or {ra["self_account"], rb["self_account"]} != {ra["payer_account"], ra["payee_account"]}
            or not ea["source_currency"] or ea["source_currency"] != eb["source_currency"]
            or ra["amount"] != rb["amount"] or min(money_decimal(ea["signed_amount"]), money_decimal(eb["signed_amount"])) <= 0
            or bool(ra["trade_time_utc"]) != bool(rb["trade_time_utc"])
            or (ra["trade_time_utc"] or ra["trade_time"]) != (rb["trade_time_utc"] or rb["trade_time"])):
        return None, fail
    fa, fb = (effective[str(row["flow_id"])] for row in pair)
    for row in pair:
        if not _matches_source(row["evidence"]["row"], current_rows.get(str(row["flow_id"]))):
            return None, "bank_pair_current_facts_changed"
    if require_projected_roles and (any(fa[key] != fb[key] for key in ("payer_account", "payee_account", "currency", "amount"))
            or (fa["trade_time_utc"] or fa["trade_time"]) != (fb["trade_time_utc"] or fb["trade_time"])):
        return None, fail
    sides = []
    for row in pair:
        evidence = row["evidence"]
        change = source_ledger._balance_change(row, books[source_ledger._scope(row)],
            unlocated=(row["source_id"], row["member_path"]) in unlocated)
        signed = money_decimal(evidence["signed_amount"])
        expected = signed.copy_negate() if evidence["direction"] == "out" else signed
        if (change["status"] != "observed" or change["basis"] != "unique_preceding_source_time"
                or money_decimal(change["difference"]) != expected):
            return None, "bank_pair_independent_balance_witness_missing"
        witness = next(item for item in books[source_ledger._scope(row)] if item["source_row"] == change["previous_source_row"])
        if not _matches_source(witness["evidence"]["row"], current_rows.get(str(witness["flow_id"]))):
            return None, "bank_pair_current_balance_witness_changed"
        sides.append({"source_row": row["source_row"], "own_account": evidence["row"]["self_account"],
            "source_direction": evidence["source_direction"], "amount": evidence["signed_amount"],
            "before": change["before"], "after": change["after"], "difference": change["difference"],
            "previous_source_row": change["previous_source_row"]})
    return {"source_id": a["source_id"], "member_path": a["member_path"], "member_sha256": a["member_sha256"],
            "header_scope": ea["header_scope"], "sides": sorted(sides, key=lambda side: side["own_account"]),
            "independent_references_absent": True,
            "meaning": "paired_source_postings_only; settlement_and_ultimate_ownership_unproved"}, "bank_primary_reference_and_opposite_balance_postings"
