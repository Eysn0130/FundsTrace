"""Narrow posting inputs retain the complete selected source evidence bag."""
from collections import defaultdict
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from itertools import islice
from decimal import Decimal, localcontext
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import sys
import threading
import weakref

import pytest

from app import bank_posting_pairs as pairs, source_ledger, table_import
from app.table_cancellation_links import active_support_sql, ordinary_csv_member
from test_bank_posting_pairs import encoded, entries
from test_folder_evidence import archive
from test_source_ledger import add_source


def native_entries():
    rows = entries()
    for row in rows:
        row["对手开户银行"] = "123456"
        row["摘要说明"] = "SYN wide retained source text " + "x" * 4096
    return rows


def legacy_inputs(database, case_id, monkeypatch):
    # Frozen pre-stream query and complete input assembly. In particular this
    # oracle must not call the compacting production _inputs implementation.
    query = f"""select r.source_id,r.record_no,r.member_path,r.source_row,r.disposition,
        s.parser_version,s.filename,s.sheet,s.row_kind,s.sha256 source_sha256,
        m.sha256 member_sha256,m.role,m.encoding,f.flow_id,
        json_extract_string(r.details_json,'$.{source_ledger.FIELD}') ledger_evidence,
        json_extract_string(r.details_json,'$.{pairs.FIELD}') pair_evidence,
        json_extract_string(r.details_json,'$.trade_no_basis') trade_no_basis
        from import_records r join import_sources s on s.case_id=r.case_id and s.source_id=r.source_id
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
        left join flow_sources f on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no
        where r.case_id=? and s.parser_version in (select unnest(?::varchar[]))
          and r.disposition<>'removed' and {active_support_sql('s')}
        order by r.source_id,r.member_path,r.source_row"""
    rows = database.q_raw(query, [case_id, sorted(table_import.SOURCE_LEDGER_PROFILES)])
    references, unlocated, direct_members = set(), set(), set()
    for row in rows:
        if ordinary_csv_member(row["parser_version"], {"filename": row["filename"], "sheet": row["sheet"],
                "row_kind": row["row_kind"], "sha256": row["source_sha256"]}, {"member_path": row["member_path"],
                "sha256": row["member_sha256"], "role": row["role"], "encoding": row["encoding"]}):
            direct_members.add(row["member_sha256"])
        contract = row.pop("pair_evidence")
        row["contract"] = pairs.validate(contract) if contract is not None else None
        if row["contract"] and row["parser_version"] in pairs.PROFILES and row["contract"]["source_primary_reference"]:
            references.add(row["contract"]["source_primary_reference"])
        ledger = row.pop("ledger_evidence")
        row["evidence"] = source_ledger.validate(ledger) if ledger is not None else None
        if row["disposition"] == "unresolved" and row["evidence"] is None:
            unlocated.add((row["source_id"], row["member_path"]))
    if not references:
        return [], {}, unlocated
    books, candidates, entries = defaultdict(list), [], {}
    for row in rows:
        evidence, contract = row["evidence"], row["contract"]
        row["position_path"] = None if row["member_sha256"] in direct_members else row["member_path"]
        if evidence:
            scope = source_ledger._scope(row)
            entry = (scope, row["flow_id"], pairs._facts(row)) if row["flow_id"] is not None else (scope, row["record_no"])
            if entry not in entries:
                row["_physical_rows"] = {row["source_row"]}
                books[scope].append(row)
                entries[entry] = row
            else:
                entries[entry]["_physical_rows"].add(row["source_row"])
        row["authority"] = contract is not None and row["parser_version"] in pairs.PROFILES
        reference = (contract["source_primary_reference"] if row["authority"] else
                     pairs._identifier(evidence["row"]["trade_no"]) if evidence and
                     row["trade_no_basis"] == "source_transaction_id" else "")
        if reference not in references:
            continue
        bank = contract["source_counterparty_bank"] if row["authority"] else pairs._identifier(
            evidence["row"]["payee_bank" if evidence["direction"] == "out" else "payer_bank"])
        row.update(reference=reference, bank=bank)
        candidates.append(row)
    return candidates, books, unlocated


def casewide_inputs(case_id):
    from app import db, table_import
    validate, FIELD, POLICY, PROFILES = pairs.validate, pairs.FIELD, pairs.POLICY, pairs.PROFILES
    _identifier, _facts = pairs._identifier, pairs._facts
    from app.table_cancellation_links import active_support_sql, ordinary_csv_member
    # Do not carry the full source-details payload through link joins/sorting.
    # Select the original source/member scope before extracting its contracts.
    rows = []
    member_metadata = {}
    contract_keys = {key: key for key in ("policy", "source_primary_reference", "source_counterparty_bank", "independent_references")}
    kinds = {value: value for value in ("financial", "context", "ledger")}
    directions = {value: value for value in ("in", "out", "unknown")}
    currencies = {value: value for value in ("CNY", "USD", "EUR")}
    references, unlocated, direct_members = set(), set(), set()
    with db.raw_rows(f"""with _pair_sources as materialized (
        select s.case_id,s.source_id,s.parser_version,s.filename,s.sheet,s.row_kind,s.sha256
        from import_sources s where s.case_id=?
          and s.parser_version in (select unnest(?::varchar[])) and {active_support_sql('s')}
        ), _pair_records as materialized (
        select r.case_id,r.source_id,r.record_no,r.member_path,r.source_row,r.disposition,
            json_extract_string(r.details_json,'$.{source_ledger.FIELD}') ledger_evidence,
            json_extract_string(r.details_json,'$.{FIELD}') pair_evidence,
            json_extract_string(r.details_json,'$.trade_no_basis') trade_no_basis
        from import_records r where r.case_id=? and r.disposition<>'removed'
          and exists (select 1 from _pair_sources s where s.case_id=r.case_id and s.source_id=r.source_id)
          and exists (select 1 from import_members m where m.case_id=r.case_id
            and m.source_id=r.source_id and m.member_path=r.member_path)
        ) select r.source_id,r.record_no,r.member_path,r.source_row,r.disposition,
        s.parser_version,s.filename,s.sheet,s.row_kind,s.sha256 source_sha256,
        m.sha256 member_sha256,m.role,m.encoding,f.flow_id,
        r.ledger_evidence,r.pair_evidence,r.trade_no_basis
        from _pair_records r join _pair_sources s on s.case_id=r.case_id and s.source_id=r.source_id
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
        left join flow_sources f on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no""",
        [case_id, sorted(table_import.SOURCE_LEDGER_PROFILES), case_id]) as raw:
        for fetched in raw:
            contract = validate(fetched["pair_evidence"]) if fetched["pair_evidence"] is not None else None
            evidence = source_ledger.validate(fetched["ledger_evidence"]) if fetched["ledger_evidence"] is not None else None
            if ordinary_csv_member(fetched["parser_version"], {"filename": fetched["filename"], "sheet": fetched["sheet"],
                    "row_kind": fetched["row_kind"], "sha256": fetched["source_sha256"]}, {"member_path": fetched["member_path"],
                    "sha256": fetched["member_sha256"], "role": fetched["role"], "encoding": fetched["encoding"]}):
                direct_members.add(fetched["member_sha256"])
            if contract is not None:
                contract = {contract_keys[key]: value for key, value in contract.items()}
                contract["policy"] = POLICY
            authority = contract is not None and fetched["parser_version"] in PROFILES
            reference = (contract["source_primary_reference"] if authority else
                         _identifier(evidence["row"]["trade_no"]) if evidence and
                         fetched["trade_no_basis"] == "source_transaction_id" else "")
            bank = (contract["source_counterparty_bank"] if authority else _identifier(
                evidence["row"]["payee_bank" if evidence["direction"] == "out" else "payer_bank"]) if evidence else "")
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
                # Validate the complete snapshot above, then retain only the
                # fields consumed by pair/single-posting and balance proofs.
                evidence = {key: evidence[key] for key in ("kind", "header_scope", "source_currency", "direction",
                    "own_card", "signed_amount", "source_direction", "balance_before")}
                evidence["row"] = {key: source[key] for key in ("trade_no", "payer_account", "payee_account",
                    "self_account", "amount", "balance", "currency", "trade_time", "trade_time_utc")}
                for key, literals in (("kind", kinds), ("direction", directions), ("source_currency", currencies)):
                    value = evidence[key]
                    if type(value) is str:
                        evidence[key] = literals.get(value, value)
                value = evidence["row"]["currency"]
                if type(value) is str:
                    evidence["row"]["currency"] = currencies.get(value, value)
                del source
            row = {key: fetched[key] for key in ("source_id", "record_no", "member_path", "source_row", "disposition",
                "parser_version", "member_sha256", "flow_id", "trade_no_basis")}
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
    del member_metadata
    # Preserve source order without sorting the wide evidence inside DuckDB.
    rows.sort(key=lambda row: (row["source_id"], row["member_path"], row["source_row"]))
    if not references:
        return [], {}, unlocated
    books, candidates, book_entries = defaultdict(list), [], {}
    for row in rows:
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



def before_scalar_inputs(case_id):
    from app import db, table_import
    validate, FIELD, POLICY, PROFILES = pairs.validate, pairs.FIELD, pairs.POLICY, pairs.PROFILES
    _identifier, _facts = pairs._identifier, pairs._facts
    from app.table_cancellation_links import active_support_sql, ordinary_csv_member
    # Do not carry the full source-details payload through link joins/sorting.
    # Select the original source/member scope before extracting its contracts.
    rows = []
    member_metadata = {}
    contract_keys = {key: key for key in ("policy", "source_primary_reference", "source_counterparty_bank", "independent_references")}
    kinds = {value: value for value in ("financial", "context", "ledger")}
    directions = {value: value for value in ("in", "out", "unknown")}
    currencies = {value: value for value in ("CNY", "USD", "EUR")}
    references, unlocated, direct_members = set(), set(), set()
    with db.raw_rows(f"""with _pair_sources as materialized (
        select s.case_id,s.source_id,s.parser_version,s.filename,s.sheet,s.row_kind,s.sha256
        from import_sources s where s.case_id=?
          and s.parser_version in (select unnest(?::varchar[])) and {active_support_sql('s')}
        ), _pair_records as materialized (
        select r.case_id,r.source_id,r.record_no,r.member_path,r.source_row,r.disposition,
            json_extract_string(r.details_json,'$.{source_ledger.FIELD}') ledger_evidence,
            json_extract_string(r.details_json,'$.{FIELD}') pair_evidence,
            json_extract_string(r.details_json,'$.trade_no_basis') trade_no_basis
        from import_records r where r.case_id=? and r.disposition<>'removed'
          and exists (select 1 from _pair_sources s where s.case_id=r.case_id and s.source_id=r.source_id)
          and exists (select 1 from import_members m where m.case_id=r.case_id
            and m.source_id=r.source_id and m.member_path=r.member_path)
        ) select r.source_id,r.record_no,r.member_path,r.source_row,r.disposition,
        s.parser_version,s.filename,s.sheet,s.row_kind,s.sha256 source_sha256,
        m.sha256 member_sha256,m.role,m.encoding,f.flow_id,
        r.ledger_evidence,r.pair_evidence,r.trade_no_basis
        from _pair_records r join _pair_sources s on s.case_id=r.case_id and s.source_id=r.source_id
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
        left join flow_sources f on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no""",
        [case_id, sorted(table_import.SOURCE_LEDGER_PROFILES), case_id]) as raw:
        for fetched in raw:
            contract = validate(fetched["pair_evidence"]) if fetched["pair_evidence"] is not None else None
            evidence = source_ledger.validate(fetched["ledger_evidence"]) if fetched["ledger_evidence"] is not None else None
            if ordinary_csv_member(fetched["parser_version"], {"filename": fetched["filename"], "sheet": fetched["sheet"],
                    "row_kind": fetched["row_kind"], "sha256": fetched["source_sha256"]}, {"member_path": fetched["member_path"],
                    "sha256": fetched["member_sha256"], "role": fetched["role"], "encoding": fetched["encoding"]}):
                direct_members.add(fetched["member_sha256"])
            if contract is not None:
                contract = {contract_keys[key]: value for key, value in contract.items()}
                contract["policy"] = POLICY
            authority = contract is not None and fetched["parser_version"] in PROFILES
            reference = (contract["source_primary_reference"] if authority else
                         _identifier(evidence["row"]["trade_no"]) if evidence and
                         fetched["trade_no_basis"] == "source_transaction_id" else "")
            bank = (contract["source_counterparty_bank"] if authority else _identifier(
                evidence["row"]["payee_bank" if evidence["direction"] == "out" else "payer_bank"]) if evidence else "")
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
                # Validate the complete snapshot above, then retain only the
                # fields consumed by pair/single-posting and balance proofs.
                evidence = {key: evidence[key] for key in ("kind", "header_scope", "source_currency", "direction",
                    "own_card", "signed_amount", "source_direction", "balance_before")}
                evidence["row"] = {key: source[key] for key in ("trade_no", "payer_account", "payee_account",
                    "self_account", "amount", "balance", "currency", "trade_time", "trade_time_utc")}
                for key, literals in (("kind", kinds), ("direction", directions), ("source_currency", currencies)):
                    value = evidence[key]
                    if type(value) is str:
                        evidence[key] = literals.get(value, value)
                value = evidence["row"]["currency"]
                if type(value) is str:
                    evidence["row"]["currency"] = currencies.get(value, value)
                del source
            row = {key: fetched[key] for key in ("source_id", "record_no", "member_path", "source_row", "disposition",
                "parser_version", "member_sha256", "flow_id", "trade_no_basis")}
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
    del member_metadata
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



def before_scalar_project(case_id, *, excluded_ids=(), source_roles=False):
    """Retain observations; unproved corresponding pairs withhold certain funds."""
    from app import db
    _CurrentFacts, _pair_projection = pairs._CurrentFacts, pairs._pair_projection
    candidates, books, unlocated = before_scalar_inputs(case_id)
    if not candidates:
        return [], [], []
    ids = sorted({int(row["flow_id"]) for row in candidates if row["flow_id"] is not None})
    source_rows = {}
    for group in books.values():
        for row in group:
            if row["flow_id"] is not None:
                source_rows.setdefault(int(row["flow_id"]), row["evidence"]["row"])
    book_ids = sorted(source_rows)
    currencies = {value: value for value in ("CNY", "USD", "EUR")}
    current_rows = {}
    with db.raw_rows("""select id,trade_no,payer_account,payee_account,
        self_account,currency,amount,balance,trade_time,trade_time_utc from flows
        where case_id=? and id in (select unnest(?::bigint[]))""", [case_id, book_ids]) as raw:
        for row in raw:
            value = row["currency"]
            if type(value) is str:
                row["currency"] = currencies.get(value, value)
            source = source_rows.get(row["id"])
            if source is not None:
                for field in ("trade_no", "payer_account", "payee_account", "self_account", "currency"):
                    if type(row[field]) is str and type(source[field]) is str and row[field] == source[field]:
                        row[field] = source[field]
            current_rows[str(row["id"])] = _CurrentFacts(row)
    del source_rows, book_ids
    effective = {}
    with db.raw_rows("""select id,payer_account,payee_account,self_account,
        currency,amount,trade_time,trade_time_utc from effective_flows
        where case_id=? and id in (select unnest(?::bigint[]))""", [case_id, ids]) as raw:
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
        from app import bank_single_postings
        events.extend(bank_single_postings.project(case_id, candidates, books, unlocated,
            effective, current_rows, memberships, excluded_ids))
    return events, [memberships[key] for key in sorted(memberships)], candidates



def before_release_project(case_id, *, excluded_ids=(), source_roles=False):
    """Retain observations; unproved corresponding pairs withhold certain funds."""
    from app import db
    _CurrentFacts, _pair_projection = pairs._CurrentFacts, pairs._pair_projection
    candidates, books, unlocated = pairs._inputs(case_id)
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
    with db.raw_rows("""select id,trade_no,payer_account,payee_account,
        self_account,currency,amount,balance,trade_time,trade_time_utc from flows
        where case_id=? and id in (select unnest(?::bigint[]))""", [case_id, book_ids]) as raw:
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
            current_rows[str(row["id"])] = _CurrentFacts(row)
    del source_rows, book_ids
    effective = {}
    with db.raw_rows("""select id,payer_account,payee_account,self_account,
        currency,amount,trade_time,trade_time_utc from effective_flows
        where case_id=? and id in (select unnest(?::bigint[]))""", [case_id, ids]) as raw:
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
        from app import bank_single_postings
        events.extend(bank_single_postings.project(case_id, candidates, books, unlocated,
            effective, current_rows, memberships, excluded_ids))
    return events, [memberships[key] for key in sorted(memberships)], candidates



def before_raw_pages_project(case_id, *, excluded_ids=(), source_roles=False):
    """Retain observations; unproved corresponding pairs withhold certain funds."""
    from app import db
    _CurrentFacts, _pair_projection = pairs._CurrentFacts, pairs._pair_projection
    candidates, books, unlocated = pairs._inputs(case_id)
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
    with db.raw_rows("""select id,trade_no,payer_account,payee_account,
        self_account,currency,amount,balance,trade_time,trade_time_utc from flows
        where case_id=? and id in (select unnest(?::bigint[]))""", [case_id, book_ids]) as raw:
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
            current_rows[str(row["id"])] = _CurrentFacts(row)
    del source_rows, book_ids
    effective = {}
    with db.raw_rows("""select id,payer_account,payee_account,self_account,
        currency,amount,trade_time,trade_time_utc from effective_flows
        where case_id=? and id in (select unnest(?::bigint[]))""", [case_id, ids]) as raw:
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
        from app import bank_single_postings
        events.extend(bank_single_postings.project(case_id, candidates, books, unlocated,
            effective, current_rows, memberships, excluded_ids))
    del books, unlocated, effective, current_rows, excluded_ids
    return events, [memberships[key] for key in sorted(memberships)], candidates



def before_effective_pages_project(case_id, *, excluded_ids=(), source_roles=False):
    """Retain observations; unproved corresponding pairs withhold certain funds."""
    from app import db
    _CurrentFacts, _pair_projection = pairs._CurrentFacts, pairs._pair_projection
    candidates, books, unlocated = pairs._inputs(case_id)
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
                    current_rows[str(row["id"])] = _CurrentFacts(row)
    del source_rows, book_ids
    effective = {}
    with db.raw_rows("""select id,payer_account,payee_account,self_account,
        currency,amount,trade_time,trade_time_utc from effective_flows
        where case_id=? and id in (select unnest(?::bigint[]))""", [case_id, ids]) as raw:
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
        from app import bank_single_postings
        events.extend(bank_single_postings.project(case_id, candidates, books, unlocated,
            effective, current_rows, memberships, excluded_ids))
    del books, unlocated, effective, current_rows, excluded_ids
    return events, [memberships[key] for key in sorted(memberships)], candidates



def proof_inputs(value):
    """Compare the audited proof contract, separate from old display payloads."""
    def retained(row, *, candidate=False):
        result = {key: row[key] for key in ("source_id", "record_no", "member_path", "source_row", "disposition",
            "parser_version", "member_sha256", "flow_id", "trade_no_basis", "position_path", "authority", "contract")}
        evidence = row["evidence"]
        result["evidence"] = None
        if evidence is not None:
            result["evidence"] = {key: evidence[key] for key in ("kind", "header_scope", "source_currency",
                "direction", "own_card", "signed_amount", "source_direction", "balance_before")}
            result["evidence"]["row"] = {key: evidence["row"][key] for key in ("trade_no", "payer_account",
                "payee_account", "self_account", "amount", "balance", "currency", "trade_time", "trade_time_utc")}
        if "_physical_rows" in row:
            # This field denotes a set of physical positions, independent of
            # its build-time or frozen container representation.
            result["_physical_rows"] = tuple(sorted(row["_physical_rows"]))
        if candidate:
            result.update(reference=row["reference"], bank=row["bank"])
        if "effective" in row:
            result["effective"] = row["effective"]
        return result

    candidates, books, unlocated = value
    return ([retained(row, candidate=True) for row in candidates],
            {scope: [retained(row) for row in rows] for scope, rows in books.items()}, unlocated)


def exact_value(value):
    if type(value) in (pairs._PostingRow, pairs._ProofContract, pairs._ProofEvidence, pairs._ProofRow):
        # Only these fixed private proof containers change. Recurse through every
        # original scalar and nested container with the same strict checks.
        return exact_value(dict(value))
    if isinstance(value, pairs._CurrentFacts):
        # Only the private raw-row container changes; scalar representations
        # and the complete key set still use the strict comparison below.
        return exact_value(dict(value))
    if isinstance(value, Decimal):
        return (type(value), value.as_tuple())
    if isinstance(value, datetime):
        return (type(value), value.isoformat(), value.tzinfo, value.tzname(), value.fold)
    if isinstance(value, dict):
        return {key: exact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return (type(value), tuple(exact_value(item) for item in value))
    if isinstance(value, set):
        return (type(value), frozenset(exact_value(item) for item in value))
    return (type(value), value)


@contextmanager
def project_map_reads(database, monkeypatch, *, transform=None, raw_transform=None, reject_bulk=False):
    """Observe the old/new public SQL footprints without changing their scope."""
    original_query, original_rows = database.q_raw, database.raw_rows
    state = {"order": [], "requested": [], "current": {}, "effective": {}, "exclusions": [], "fallback": []}

    def stage(query):
        if "id in (select unnest(?::bigint[]))" not in query:
            return None
        if query.lstrip().startswith("select id,trade_no,"):
            return "current"
        if query.lstrip().startswith("select id,payer_account,"):
            return "effective"
        return None

    def prepare(kind, row):
        if kind == "effective" and transform is not None:
            row = transform(row)
        if kind == "current" and raw_transform is not None:
            row = raw_transform(row)
        if kind is not None:
            state[kind][str(row["id"])] = row
        return row

    def query(sql, params=None):
        kind = stage(sql)
        if kind is not None:
            if reject_bulk:
                pytest.fail("flow maps must consume bounded rows, not a full q_raw result")
            state["order"].append(kind)
            state["requested"].append((kind, params[0], set(params[1])))
        result = original_query(sql, params)
        if "f.id not in (select unnest(?::bigint[]))" in sql:
            state["exclusions"].append(set(params[1]))
            state["fallback"].append(result)
        return [prepare(kind, row) for row in result]

    @contextmanager
    def rows(sql, params=None):
        kind = stage(sql)
        if kind is not None:
            state["order"].append(kind)
            state["requested"].append((kind, params[0], set(params[1])))
        with original_rows(sql, params) as raw:
            yield (prepare(kind, row) for row in raw)

    with monkeypatch.context() as reads:
        reads.setattr(database, "q_raw", query)
        reads.setattr(database, "raw_rows", rows)
        yield state


def legacy_known_references(case_id, candidates):
    """Count physical named-primary observations across every active source."""
    from app.bank_posting_pairs import _identifier
    from app.table_cancellation_links import active_support_sql
    from app import db, bank_single_postings
    references, direct = defaultdict(set), {r["member_sha256"] for r in candidates if r["position_path"] is None}
    for row in candidates:
        references[row["reference"]].add(bank_single_postings._physical(row))
    cursor = db.con().execute(f"""select r.member_path,r.source_row,m.sha256,
        json_extract_string(field.value,'$.column') source_column,
        json_extract_string(field.value,'$.raw') reference
        from import_records r join import_sources s on s.case_id=r.case_id and s.source_id=r.source_id
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path,
        json_each(json_extract_string(r.details_json,'$.source_field_evidence')) field
        where r.case_id=? and r.disposition<>'removed' and {active_support_sql('s')}""", [case_id])
    while batch := cursor.fetchmany(4096):
        for path, position, digest, column, raw in batch:
            if not isinstance(column, str) or column.replace("\\t", "").strip() != "交易流水号":
                continue
            reference = _identifier(raw)
            if reference in references:
                references[reference].add((None if digest in direct else path, digest, position))
    return references


def legacy_cross_index(case_id, books, current_rows, wanted):
    """Potential opposite owners block certainty; these keys never merge facts."""
    from app import db
    from app.bank_single_postings import _key
    from app.money import money_decimal
    index = defaultdict(set)
    def add(row, amount=None):
        from app.bank_posting_pairs import _identifier
        key = _key(row, amount)
        if key not in wanted:
            return
        index[key].add(_identifier(row["self_account"]))
    for book in books.values():
        for record in book:
            e = record["evidence"]
            add(e["row"], money_decimal(e["signed_amount"]))
    # Older registered/legacy observations may predate source-ledger snapshots.
    from app.table_cancellation_links import active_support_sql
    rows = db.q_raw(f"""select f.payer_account,f.payee_account,f.self_account,f.currency,f.amount,
        f.trade_time,f.trade_time_utc from flows f where f.case_id=? and f.amount>0
        and f.self_account in (f.payer_account,f.payee_account)
        and f.id not in (select unnest(?::bigint[])) and
          (not exists(select 1 from flow_sources fs where fs.case_id=f.case_id and fs.flow_id=f.id)
           or exists(select 1 from flow_sources fs join import_sources s
             on s.case_id=fs.case_id and s.source_id=fs.source_id
             where fs.case_id=f.case_id and fs.flow_id=f.id and {active_support_sql('s')}))""",
        [case_id, list(map(int, current_rows))])
    for row in rows:
        add(row)
    return index


@pytest.mark.parametrize("own_mask", [1, 2, 3])
@pytest.mark.parametrize("owners,mask", [
    ((), 0), (("SYN-PAYER",), 1), (("SYN-PAYEE",), 2), (("SYN-PAYER", "SYN-PAYEE"), 3),
])
def test_cross_index_retains_only_observed_opposing_owners(database, monkeypatch, owners, mask, own_mask):
    from app import bank_single_postings
    base = {"payer_account": " SYN-PAYER ", "payee_account": " SYN-PAYEE ",
            "self_account": " SYN-PAYER ", "currency": "USD", "amount": "7.53",
            "trade_time": None, "trade_time_utc": None}
    rows = [dict(base, self_account=" " + owner + " ") for owner in owners for _ in range(3)]
    invalid = [dict(base, self_account="SYN-OTHER"), dict(base, payer_account=""),
               dict(base, payee_account=base["payer_account"]), dict(base, currency=""),
               dict(base, amount="0.00"), dict(base, amount="-7.53")]
    not_wanted = dict(base, amount="9.01")
    scanned = rows + invalid + [not_wanted]
    books = {("SYN-BOOK",): [{"evidence": {"row": row, "signed_amount": row["amount"],
                                            "kind": "context", "direction": "out"}} for row in scanned]}
    current = {"17": {}, "19": {}}
    key = ("SYN-PAYER", "SYN-PAYEE", "USD", Decimal("7.53"))
    unseen = ("SYN-PAYER", "SYN-PAYEE", "EUR", Decimal("7.53"))
    wanted = {key: own_mask, unseen: own_mask}
    queries, calls = [], []
    original_key = bank_single_postings._key

    def query(sql, params):
        queries.append((sql, params))
        return scanned

    def observed_key(row, amount=None):
        calls.append((row, amount))
        return original_key(row, amount)

    monkeypatch.setattr(database, "q_raw", query)
    monkeypatch.setattr(bank_single_postings, "_key", observed_key)
    old = legacy_cross_index(17, books, current, set(wanted))
    old_calls = list(calls)
    calls.clear()
    actual = bank_single_postings._cross_index(17, books, current, wanted)
    opposite_mask = {1: 2, 2: 1, 3: 3}[own_mask]
    matched = mask & opposite_mask
    assert actual == ({key: matched} if matched else {})
    assert calls == old_calls
    for owner, bit, opposite in ((key[0], 1, 2), (key[1], 2, 1)):
        if own_mask & bit:
            assert bool(actual.get(key, 0) & opposite) == bool(old.get(key, set()) - {owner})
    assert bank_single_postings._cross_index(17, books, current, wanted) == actual
    assert wanted == {key: own_mask, unseen: own_mask}
    assert calls == old_calls * 2
    assert len(queries) == 3 and queries[0] == queries[1] == queries[2]
    assert queries[0][1] == [17, [17, 19]]


def legacy_prepare(books, unlocated):
    from app.bank_single_postings import _scope, _clock
    merged = defaultdict(list)
    for book in books.values():
        for row in book:
            merged[_scope(row)].append(row)
    result = {}
    for scope, book in merged.items():
        times, bases = defaultdict(list), set()
        reason = "single_posting_book_unlocated" if scope[:2] in unlocated else None
        for row in book:
            e = row["evidence"]
            basis, stamp = _clock(e["row"])
            bases.add(basis)
            if stamp is None:
                reason = "single_posting_book_unlocated"
            times[stamp].extend([row] * len(row.get("_physical_rows", {row["source_row"]})))
        if len(bases) != 1:
            reason = "single_posting_book_time_basis_incomparable"
        ordered = sorted(t for t in times if t is not None)
        previous = {stamp: ordered[index - 1] if index else None for index, stamp in enumerate(ordered)}
        result[scope] = reason, times, previous
    return result


@pytest.mark.parametrize("scenario", ["same-record", "multi-physical", "sticky", "normalized", "clock-none", "basis-mixed"])
def test_prepared_time_states_preserve_full_normalized_scope_and_old_buckets(scenario):
    from app import bank_single_postings

    def record(position, stamp):
        return {"source_id": "SYN-source", "member_path": "SYN.csv", "source_row": position,
            "_physical_rows": (position,), "evidence": {"header_scope": 1, "source_currency": "CNY",
                "own_card": "SYN-CARD", "row": {"self_account": "SYN-A", "trade_time": stamp, "trade_time_utc": None}}}

    a, b = record(2, "2039-04-05 09:00:00"), record(3, "2039-04-05 09:00:01")
    if scenario == "same-record":
        rows = [a, a, b]
    elif scenario in {"multi-physical", "sticky"}:
        a["_physical_rows"] = (2, 2002)
        rows = [a, b] if scenario == "multi-physical" else [a, record(2003, a["evidence"]["row"]["trade_time"]), b]
    else:
        rows = [a, b]
    if scenario == "normalized":
        b["evidence"]["own_card"] = " SYN-CARD "
        b["evidence"]["row"].update(self_account=" SYN-A ", trade_time=a["evidence"]["row"]["trade_time"])
    if scenario in {"clock-none", "basis-mixed"}:
        a["evidence"]["row"]["trade_time"] = None
    if scenario == "basis-mixed":
        b["evidence"]["row"]["trade_time_utc"] = "2039-04-05 01:00:01"
    books = defaultdict(list)
    for row in rows:
        books[source_ledger._scope(row)].append(row)
        assert len(row["_physical_rows"]) > 0
    before = {scope: [id(row) for row in book] for scope, book in books.items()}
    unlocated = {(a["source_id"], a["member_path"])} if scenario == "basis-mixed" else set()
    expected, actual = legacy_prepare(books, unlocated), bank_single_postings._prepare(books, unlocated)
    assert actual.keys() == expected.keys() and len(actual) == 1
    for scope, (reason, buckets, previous) in expected.items():
        new_reason, times, new_previous = actual[scope]
        assert new_reason == reason and new_previous == previous and times.keys() == buckets.keys()
        for stamp, bucket in buckets.items():
            assert bucket
            assert times[stamp] is (bucket[0] if len(bucket) == 1 else None)
    assert {scope: [id(row) for row in book] for scope, book in books.items()} == before
    if scenario == "basis-mixed":
        assert next(iter(actual.values()))[0] == "single_posting_book_time_basis_incomparable"
    elif scenario == "clock-none":
        assert next(iter(actual.values()))[0] == "single_posting_book_unlocated"


@pytest.mark.parametrize("scenario,reason", [
    ("current", "single_posting_time_not_unique"),
    ("previous", "single_posting_previous_time_not_unique"),
    ("missing", "single_posting_previous_missing"),
])
def test_single_current_and_previous_time_ambiguity_keep_exact_old_public_reason(client, database, make_case, monkeypatch, scenario, reason):
    from test_bank_single_postings import entries as single_entries
    cid = make_case("SYN prepared time reason " + scenario)
    rows = single_entries()
    if scenario == "current":
        rows[2].update({"交易时间": rows[1]["交易时间"], "交易金额": "7.53", "交易余额": "980.13"})
    elif scenario == "previous":
        rows.append(dict(rows[0], **{"交易流水号": "SYN-PREV-OTHER"}))
    else:
        rows = [rows[1]]
    add_source(client, cid, encoded(rows))
    expected = legacy_projection(database, cid, monkeypatch)
    actual = pairs.project(cid)
    target = next(row for row in actual[2] if row["reference"] == "SYN-SINGLE-1")
    assert next(row for row in actual[1] if row["flow_id"] == str(target["flow_id"]))["reason"] == reason
    assert exact_value(actual[:2]) == exact_value(expected[:2])
    assert proof_inputs((actual[2], {}, set())) == proof_inputs((expected[2], {}, set()))


def legacy_single_projection(case_id, candidates, books, unlocated, effective, current_rows, memberships, excluded_ids):
    # Frozen complete pre-saturation Single project, independent of current project.
    from app import db
    from app.bank_posting_pairs import _facts, _identifier, _matches_source
    from app.bank_single_postings import POLICY, _key, _clock, _scope, _physical, _json
    from app.money import money_decimal
    references = legacy_known_references(case_id, candidates)
    prepared = legacy_prepare(books, unlocated)
    by_reference = defaultdict(list)
    for row in candidates:
        by_reference[row["reference"]].append(row)
    wanted = {_key(row["evidence"]["row"], money_decimal(row["evidence"]["signed_amount"]))
              for row in candidates if row["authority"] and row["evidence"] and row["evidence"]["kind"] == "financial"}
    wanted.discard(None)
    cross = legacy_cross_index(case_id, books, current_rows, wanted)
    ids = sorted(map(int, effective))
    conflicts = {str(row["flow_id"]) for row in db.q_raw("""select distinct flow_id from resolved_endpoints
        where case_id=? and flow_id in (select unnest(?::bigint[])) and (state='conflict' or card_state='conflict')""", [case_id, ids])}
    events = []
    for reference, rows in sorted(by_reference.items()):
        flow_ids = sorted({str(row["flow_id"]) for row in rows if row["effective"]})
        if (not flow_ids or any(fid in excluded_ids or memberships.get(fid, {}).get("status") == "confirmed"
                or memberships.get(fid, {}).get("pending_group") for fid in flow_ids)):
            continue
        reason, proof, chosen = "single_posting_source_incomplete", None, None
        if len(references[reference]) != 1:
            reason = "single_posting_primary_reused"
        elif len({_facts(row) for row in rows if row["evidence"]}) > 1:
            reason = "single_posting_source_conflict"
        else:
            for row in rows:
                e = row["evidence"]
                if not (row["authority"] and e and row["effective"] and e["kind"] == "financial"
                        and row["disposition"] in {"imported", "duplicate"} and row["trade_no_basis"] == "source_transaction_id"
                        and e["row"]["trade_no"] == reference and e["source_currency"] in {"CNY", "USD", "EUR"}
                        and row["bank"] and _identifier(e["row"]["self_account"])):
                    continue
                source = e["row"]
                key = _key(source, money_decimal(e["signed_amount"]))
                if key is None:
                    continue
                basis, stamp = _clock(source)
                competing = bool(cross.get(key, set()) - {_identifier(source["self_account"])} )
                if competing:
                    reason = "single_posting_cross_book_candidate"
                    break
                if str(row["flow_id"]) in conflicts:
                    reason = "single_posting_identity_conflict"
                    break
                if not _matches_source(source, current_rows.get(str(row["flow_id"]))):
                    reason = "single_posting_current_facts_changed"
                    break
                book_reason, times, previous = prepared[_scope(row)]
                if book_reason:
                    reason = book_reason
                    continue
                if len(times[stamp]) != 1:
                    reason = "single_posting_time_not_unique"
                    continue
                before_stamp = previous.get(stamp)
                prior = times.get(before_stamp, []) if before_stamp is not None else []
                if len(prior) != 1:
                    reason = "single_posting_previous_time_not_unique" if prior else "single_posting_previous_missing"
                    continue
                prior = prior[0]
                before = prior["evidence"]["row"]["balance"]
                if source["balance"] is None or before is None:
                    reason = "single_posting_balance_missing"
                    continue
                expected = money_decimal(e["signed_amount"])
                if e["direction"] == "out": expected = -expected
                delta = money_decimal(source["balance"]) - money_decimal(before)
                if delta != expected:
                    reason = "single_posting_balance_mismatch"
                    continue
                if not _matches_source(prior["evidence"]["row"], current_rows.get(str(prior["flow_id"]))):
                    reason = "single_posting_current_witness_changed"
                    continue
                fa = effective[str(row["flow_id"])]
                if (fa["self_account"] != fa["payer_account" if e["direction"] == "out" else "payee_account"]
                        or not fa["payer_account"] or not fa["payee_account"] or fa["payer_account"] == fa["payee_account"]):
                    reason = "single_posting_identity_conflict"
                    continue
                if any(not _matches_source(source, current_rows.get(fid)) or any(
                        effective[fid][field] != fa[field] for field in
                        ("payer_account", "payee_account", "self_account", "currency", "amount", "trade_time", "trade_time_utc"))
                        for fid in flow_ids):
                    reason = "single_posting_current_support_conflict"
                    break
                proof = {"policy": POLICY, "source_id": row["source_id"], "member_path": row["member_path"],
                    "member_sha256": row["member_sha256"], "source_rows": [row["source_row"]],
                    "source_primary_reference": reference, "own_account": source["self_account"], "own_card": e["own_card"],
                    "currency": e["source_currency"], "amount": e["signed_amount"],
                    "source_time": source["trade_time"], "source_time_utc": source["trade_time_utc"],
                    "balance_change": {"before": before, "after": source["balance"], "difference": format(delta, ".2f"),
                        "previous_source_row": prior["source_row"], "basis": "unique_preceding_source_time"},
                    "meaning": "local_booked_movement_only; external_settlement_unproved"}
                chosen = row
                break
        if proof:
            identity = POLICY, _physical(chosen), reference
            event_id = "ftev_" + hashlib.sha256(_json(identity).encode()).hexdigest()
            first = effective[str(chosen["flow_id"])]
            events.append({"event_id": event_id, **{key: first[key] for key in
                ("payer_account", "payee_account", "currency", "amount")},
                "trade_time": first["trade_time_utc"] or first["trade_time"], "evidence_json": _json(proof), "flow_ids": flow_ids})
            for fid in flow_ids:
                memberships[fid] = {"flow_id": fid, "event_id": event_id, "status": "confirmed",
                    "reason": "single_posting_independent_balance_proof", "pending_group": None}
        else:
            for fid in flow_ids:
                membership = memberships[fid]
                membership["reason"] = reason
                if reason == "single_posting_cross_book_candidate":
                    membership["pending_group"] = "single_" + hashlib.sha256(reference.encode()).hexdigest()
    return events


def legacy_projection(database, case_id, monkeypatch, *, excluded_ids=(), source_roles=False):
    """Frozen v4 project: old map queries and proof orchestration, independent of project."""
    candidates, books, unlocated = legacy_inputs(database, case_id, monkeypatch)
    if not candidates:
        return [], [], []
    ids = sorted({int(row["flow_id"]) for row in candidates if row["flow_id"] is not None})
    effective = {str(row["id"]): row for row in database.q_raw("""select id,payer_account,payee_account,self_account,
        currency,amount,trade_time,trade_time_utc from effective_flows
        where case_id=? and id in (select unnest(?::bigint[]))""", [case_id, ids])}
    book_ids = sorted({int(row["flow_id"]) for group in books.values() for row in group if row["flow_id"] is not None})
    current_rows = {str(row["id"]): row for row in database.q_raw("""select id,trade_no,payer_account,payee_account,
        self_account,currency,amount,balance,trade_time,trade_time_utc from flows
        where case_id=? and id in (select unnest(?::bigint[]))""", [case_id, book_ids])}
    excluded_ids = set(map(str, excluded_ids))
    groups, unscoped, member_references = defaultdict(list), set(), defaultdict(set)
    for row in candidates:
        if not row["bank"]:
            unscoped.add(row["reference"])
        groups[row["bank"], row["reference"]].append(row)
        row["effective"] = str(row["flow_id"]) in effective
        if row["evidence"]:
            member_references[row["source_id"], row["member_path"], row["evidence"]["header_scope"], row["reference"]].add(pairs._position(row))
    events, memberships = [], {}
    for (bank, reference), rows in sorted(groups.items()):
        positions = defaultdict(list)
        for row in rows:
            positions[pairs._position(row)].append(row)
        reason, proof = "bank_pair_incomplete_evidence", None
        # Counterparty bank is a row role, not a primary-reference issuer namespace.
        reused_in_member = any(row["evidence"] and len(member_references[
            row["source_id"], row["member_path"], row["evidence"]["header_scope"], reference]) > 2 for row in rows)
        if reused_in_member:
            reason = "bank_pair_reference_reused"
        elif bank and reference not in unscoped and len(positions) == 2:
            sides = list(positions.values())
            # Duplicate support must retain one compatible physical observation.
            compatible = (all(len({pairs._facts(row) for row in side if row["evidence"]}) == 1 for side in sides)
                          and not any(row["contract"]["independent_references"] for row in rows if row["authority"]))
            from app import table_import
            scopes = [{(row["source_id"], row["member_path"], row["evidence"]["header_scope"])
                       for row in side if pairs._eligible_posting(row) and (not source_roles or
                           row["parser_version"] in table_import.NATIVE31_PROFILES
                           and row["bank"].isascii() and row["bank"].isdigit())}
                      for side in sides]
            common = scopes[0] & scopes[1]
            if compatible and common:
                scope = sorted(common)[0]
                pair = [next(row for row in side if pairs._eligible_posting(row)
                             and (row["source_id"], row["member_path"], row["evidence"]["header_scope"]) == scope)
                        for side in sides]
                proof, reason = pairs._prove(pair, books, unlocated, effective, current_rows,
                                       require_projected_roles=not source_roles)
        elif len(positions) > 2:
            reason = "bank_pair_reference_reused"
        flow_ids = sorted({str(row["flow_id"]) for row in rows if row["effective"]} - excluded_ids)
        if any(str(row["flow_id"]) in excluded_ids for row in rows):
            proof, reason = None, "bank_pair_explicit_reference_takes_precedence"
        pending_group = (hashlib.sha256(pairs._json({"policy": pairs.POLICY, "source_counterparty_bank": bank,
                            "source_primary_reference": reference}).encode()).hexdigest()
                         if not proof and bank and not any(str(row["flow_id"]) in excluded_ids for row in rows)
                         and pairs._has_opposite_candidate(rows) else None)
        event_id = None
        if proof:
            proof.update(policy=pairs.POLICY, source_counterparty_bank=bank, source_primary_reference=reference)
            identity = {key: value for key, value in proof.items() if key not in {"source_id", "member_path"}}
            event_id = "ftev_" + hashlib.sha256(pairs._json(identity).encode()).hexdigest()
            first = (current_rows if source_roles else effective)[flow_ids[0]]
            events.append({"event_id": event_id, **{key: first[key] for key in
                ("payer_account", "payee_account", "currency", "amount", "trade_time")},
                "evidence_json": pairs._json(proof), "flow_ids": flow_ids})
            events[-1]["trade_time"] = first["trade_time_utc"] or first["trade_time"]
        for flow_id in flow_ids:
            memberships[flow_id] = {"flow_id": flow_id, "event_id": event_id,
                "status": "confirmed" if proof else "unknown", "reason": reason, "pending_group": pending_group}
    if not source_roles:
        events.extend(legacy_single_projection(case_id, candidates, books, unlocated,
            effective, current_rows, memberships, excluded_ids))
    return events, [memberships[key] for key in sorted(memberships)], candidates

@pytest.mark.parametrize("retired", [False, True], ids=["one-linked-posting", "retired-source"])
def test_source_roles_skip_projection_without_two_active_linked_postings(client, database, make_case, monkeypatch, retired):
    cid = make_case("SYN source-role projection scope")
    rows = native_entries()
    if not retired:
        rows = [rows[1], dict(rows[1])]
    _, job = add_source(client, cid, encoded(rows))
    if retired:
        database.x("update import_source_operations set active=false where case_id=? and source_id=?",
                   [cid, job["result"]["source_id"]])

    def unexpected_projection(*args, **kwargs):
        pytest.fail("No active pair of distinct linked postings can supply source roles")

    monkeypatch.setattr(pairs, "project", unexpected_projection)
    assert pairs.source_role_pairs(cid) == []


@pytest.mark.parametrize("link", ["same-owner", "foreign-case", "missing-flow"])
def test_source_roles_skip_two_linked_flows_without_two_current_own_accounts(client, database, make_case, monkeypatch, link):
    cid = make_case("SYN one-sided repeated primary reference")
    first = native_entries()[1]
    second = dict(first)
    second.update({"交易时间": "2038-02-04 09:00:02", "交易余额": "925.18"})
    _, job = add_source(client, cid, encoded([first, second]))
    page = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()
    assert len({row["id"] for row in page["items"]}) == 2
    assert {row["raw_self_account"] for row in page["items"]} == {"SYN-A"}
    if link != "same-owner":
        linked = 999999
        if link == "foreign-case":
            other = make_case("SYN foreign raw owner")
            add_source(client, other, encoded([native_entries()[3]]))
            linked = database.q_raw("select id from flows where case_id=?", [other])[0]["id"]
        database.x("update flow_sources set flow_id=? where case_id=? and source_id=? and source_row=2",
                   [linked, cid, job["result"]["source_id"]])
    events, memberships, _ = pairs.project(cid, source_roles=True)
    assert events == [] and memberships
    assert {row["status"] for row in memberships} == {"unknown"}

    def unexpected_projection(*args, **kwargs):
        pytest.fail("One current own account cannot supply reciprocal source roles")

    monkeypatch.setattr(pairs, "project", unexpected_projection)
    assert pairs.source_role_pairs(cid) == []


@pytest.mark.parametrize("state", ["paired", "legacy-no-operation", "missing-balance", "context", "unlocated", "missing-link", "foreign-link"])
def test_selected_input_bags_match_legacy_for_balances_context_and_null_links(client, database, make_case, monkeypatch, state):
    cid = make_case("SYN selected posting input bag")
    rows = native_entries()
    if state == "missing-balance":
        rows[3]["交易余额"] = ""
    if state == "unlocated":
        rows[2]["交易金额"] = "SYN invalid amount"
    _, job = add_source(client, cid, encoded(rows))
    source_id = job["result"]["source_id"]
    if state == "legacy-no-operation":
        database.x("delete from import_source_operations where case_id=? and source_id=?", [cid, source_id])
    if state == "context":
        record = database.q_raw("select record_no,details_json from import_records where case_id=? and source_id=? order by record_no limit 1", [cid, source_id])[0]
        details = json.loads(record["details_json"])
        evidence = source_ledger.validate(details[source_ledger.FIELD])
        evidence.update(kind="context", issues=["balance_amount_conflict"])
        details[source_ledger.FIELD] = json.dumps(evidence)
        database.x("update import_records set details_json=? where case_id=? and source_id=? and record_no=?",
                   [json.dumps(details), cid, source_id, record["record_no"]])
    if state == "missing-link":
        database.x("delete from flow_sources where case_id=? and source_id=? and source_row=(select min(record_no) from import_records where case_id=? and source_id=?)",
                   [cid, source_id, cid, source_id])
    if state == "foreign-link":
        other = make_case("SYN foreign linked posting")
        linked = database.q_raw("insert into flows(case_id,trade_no,amount,currency) values (?,'SYN-FOREIGN',0,'CNY') returning id", [other])[0]["id"]
        database.x("update flow_sources set flow_id=? where case_id=? and source_id=? and source_row=(select min(record_no) from import_records where case_id=? and source_id=?)",
                   [linked, cid, source_id, cid, source_id])
    before = legacy_inputs(database, cid, monkeypatch)
    actual = pairs._inputs(cid)
    assert proof_inputs(actual) == proof_inputs(before)
    candidates, books, _ = actual
    assert len(candidates) == 4 and sum(map(len, books.values())) == (3 if state == "unlocated" else 4)
    if state == "unlocated":
        assert actual[2] == {(source_id, "table.csv")}
    expected = legacy_projection(database, cid, monkeypatch, source_roles=True)
    assert pairs.project(cid, source_roles=True)[:2] == expected[:2]
    result = pairs.source_role_pairs(cid)
    assert len(result) == (1 if state in {"paired", "legacy-no-operation"} else 0)
    if result:
        assert result[0]["own_accounts"] == {"SYN-A", "SYN-B"}
        assert result[0]["bank_code"] == "123456"


@pytest.mark.parametrize("old_competitor", [False, True], ids=["current-competitor", "old-ledger-competitor"])
def test_input_scope_preserves_multi_support_competitors_withdrawal_and_foreign_case(client, database, make_case, monkeypatch, old_competitor):
    cid = make_case("SYN mutable posting scope")
    rows = native_entries()
    add_source(client, cid, encoded(rows))
    add_source(client, cid, encoded(rows, archive=True), "support.zip")
    assert proof_inputs(pairs._inputs(cid)) == proof_inputs(legacy_inputs(database, cid, monkeypatch))
    assert len(pairs.source_role_pairs(cid)) == 1

    third = dict(rows[1])
    third.update({"交易时间": "2038-02-04 09:00:02", "交易余额": "925.18"})
    other = make_case("SYN foreign posting scope")
    _, foreign = add_source(client, other, encoded([third]), "foreign.csv")
    # A foreign malformed snapshot must stay outside this case's JSON path.
    database.x("update import_records set details_json='SYN invalid JSON' where case_id=? and source_id=?",
               [other, foreign["result"]["source_id"]])
    assert proof_inputs(pairs._inputs(cid)) == proof_inputs(legacy_inputs(database, cid, monkeypatch))
    assert len(pairs.source_role_pairs(cid)) == 1

    from app.services import imports
    with monkeypatch.context() as parser:
        if old_competitor:
            parser.setattr(imports, "TABLE_PROFILE", table_import.SOURCE_LEDGER_PROFILE)
        _, late = add_source(client, cid, encoded([third]), "late.csv")
    assert proof_inputs(pairs._inputs(cid)) == proof_inputs(legacy_inputs(database, cid, monkeypatch))
    assert pairs.source_role_pairs(cid) == []
    response = client.delete(f"/api/batches/{late['result']['batch_id']}", params={"case_id": cid})
    assert response.status_code == 200, response.text
    assert proof_inputs(pairs._inputs(cid)) == proof_inputs(legacy_inputs(database, cid, monkeypatch))
    assert len(pairs.source_role_pairs(cid)) == 1


@pytest.mark.parametrize("outside", ["profile", "removed", "inactive", "member"])
def test_input_projection_does_not_evaluate_json_outside_the_original_scope(client, database, make_case, monkeypatch, outside):
    cid = make_case("SYN selected JSON evaluation scope")
    add_source(client, cid, encoded(native_entries()))
    profile = "SYN-unregistered-profile" if outside == "profile" else table_import.PROFILE
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,mapping_json,sheet,parser_version,row_kind)
        values ('SYN-OUTSIDE',?,'outside.csv',?,0,'B','{}','',?,'table_record')""", [cid, "a" * 64, profile])
    member_case = make_case("SYN foreign member metadata") if outside == "member" else cid
    database.x("insert into import_members values (?,'SYN-OUTSIDE','table.csv','table',?,0,'utf-8-sig',1)", [member_case, "a" * 64])
    disposition = "removed" if outside == "removed" else "imported"
    database.x("""insert into import_records values (?,'SYN-OUTSIDE',1,'table.csv',2,'SYN-OUTSIDE',?,'transaction_valid','SYN invalid JSON')""",
               [cid, disposition])
    if outside == "inactive":
        database.x("insert into import_source_operations values (?,'SYN-OUTSIDE',null,false)", [cid])
    assert proof_inputs(pairs._inputs(cid)) == proof_inputs(legacy_inputs(database, cid, monkeypatch))


@pytest.mark.parametrize("field", [source_ledger.FIELD, pairs.FIELD])
def test_positive_source_role_gate_still_validates_selected_persisted_contracts(client, database, make_case, monkeypatch, field):
    cid = make_case("SYN selected invalid posting contract")
    _, job = add_source(client, cid, encoded(native_entries()))
    source_id = job["result"]["source_id"]
    record = database.q_raw("select record_no,details_json from import_records where case_id=? and source_id=? order by record_no limit 1", [cid, source_id])[0]
    details = json.loads(record["details_json"])
    details[field] = "{}"
    database.x("update import_records set details_json=? where case_id=? and source_id=? and record_no=?",
               [json.dumps(details), cid, source_id, record["record_no"]])
    with pytest.raises(ValueError):
        legacy_inputs(database, cid, monkeypatch)
    with pytest.raises(ValueError):
        pairs.source_role_pairs(cid)


def test_inputs_stream_full_validation_and_preserve_stored_display(client, database, make_case, monkeypatch):
    cid = make_case("SYN bounded validated posting inputs")
    add_source(client, cid, encoded(native_entries()))
    journal = database.q_raw("select * from import_records where case_id=? order by source_id,record_no", [cid])
    flows = database.q_raw("select * from flows where case_id=? order by id", [cid])
    displayed = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()["items"]
    expected_inputs = legacy_inputs(database, cid, monkeypatch)
    expected_projection = legacy_projection(database, cid, monkeypatch)
    original_query, original_rows = database.q_raw, database.raw_rows
    reader_active, streams = False, []

    def reject_bulk_input(query, params=None):
        assert not reader_active, "proof queries must follow complete reader exit"
        if "_pair_sources as materialized" in query:
            pytest.fail("posting inputs must not materialize a full q_raw result")
        return original_query(query, params)

    @contextmanager
    def counted_rows(query, params=None):
        nonlocal reader_active
        reader_active = True
        kind = ("current" if query.lstrip().startswith("select id,trade_no,") else
                "effective" if query.lstrip().startswith("select id,payer_account,") else
                "conflicts" if query.lstrip().startswith("select flow_id from resolved_endpoints") else
                "references" if "selected_records as materialized" in query else "inputs")
        state = {"count": 0, "exhausted": False, "kind": kind}
        try:
            with original_rows(query, params) as raw:
                def counted():
                    for row in raw:
                        state["count"] += 1
                        yield row
                    state["exhausted"] = True
                yield counted()
        finally:
            reader_active = False
            streams.append(state)

    with monkeypatch.context() as bounded:
        bounded.setattr(database, "q_raw", reject_bulk_input)
        bounded.setattr(database, "raw_rows", counted_rows)
        actual_inputs = pairs._inputs(cid)
        actual_projection = pairs.project(cid)
    assert streams == [{"count": 4, "exhausted": True, "kind": kind}
                       for kind in ("inputs", "inputs", "current", "effective", "references")] + [
                           {"count": 0, "exhausted": True, "kind": "conflicts"}]
    assert proof_inputs(actual_inputs) == proof_inputs(expected_inputs)
    assert actual_projection[:2] == expected_projection[:2]
    assert proof_inputs((actual_projection[2], {}, set())) == proof_inputs((expected_projection[2], {}, set()))
    for row in actual_inputs[0]:
        assert not {"filename", "sheet", "row_kind", "source_sha256", "role", "encoding"} & row.keys()
        assert set(row["evidence"]) == {"kind", "header_scope", "source_currency", "direction", "own_card",
            "signed_amount", "source_direction", "balance_before", "row"}
        assert set(row["evidence"]["row"]) == {"trade_no", "payer_account", "payee_account", "self_account",
            "amount", "balance", "currency", "trade_time", "trade_time_utc"}
    assert database.q_raw("select * from import_records where case_id=? order by source_id,record_no", [cid]) == journal
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == flows
    assert client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()["items"] == displayed


@pytest.mark.parametrize("extra_positions", [0, 1, 9])
def test_physical_positions_freeze_complete_unique_rows_and_preserve_canonical_aliases(client, database, make_case, monkeypatch, extra_positions):
    cid = make_case("SYN physical support container representation")
    rows = native_entries()
    rows.extend(dict(rows[1]) for _ in range(extra_positions))
    add_source(client, cid, encoded(rows))
    query, reader = database.q_raw, database.raw_rows
    fetched = []

    def capture(sql, params=None):
        result = query(sql, params)
        if sql.lstrip().startswith("select r.source_id,r.record_no,"):
            fetched.extend(deepcopy(result))
        return result

    with monkeypatch.context() as original:
        original.setattr(database, "q_raw", capture)
        legacy_inputs(database, cid, monkeypatch)
    target = next(row for row in fetched if row["source_row"] == 3)
    supports = [row for row in fetched if row["flow_id"] == target["flow_id"]]
    # Replaying an identical fetched position is a private reader fixture,
    # not a claim that the public parser admits duplicate source positions.
    fixture = fetched + [deepcopy(row) for row in supports for _ in range(64)]
    # The frozen reader's SQL orders its result before assembly.
    fixture.sort(key=lambda row: (row["source_id"], row["member_path"], row["source_row"]))

    def original_inputs(sql, params=None):
        if sql.lstrip().startswith("select r.source_id,r.record_no,"):
            return deepcopy(fixture)
        return query(sql, params)

    @contextmanager
    def bounded_inputs(sql, params=None):
        if "_pair_sources as materialized" in sql:
            yield iter(deepcopy(fixture))
        else:
            with reader(sql, params) as records:
                yield records

    original_facts = pairs._facts
    anchor, states = None, []

    def facts(row):
        nonlocal anchor
        if row["flow_id"] == target["flow_id"]:
            if anchor is None:
                anchor = row
            else:
                physical = anchor["_physical_rows"]
                states.append((row["source_row"], type(physical), tuple(sorted(physical))))
        return original_facts(row)

    with monkeypatch.context() as replay:
        replay.setattr(database, "q_raw", original_inputs)
        replay.setattr(database, "raw_rows", bounded_inputs)
        expected = legacy_inputs(database, cid, monkeypatch)
        with monkeypatch.context() as building:
            building.setattr(pairs, "_facts", facts)
            actual = pairs._inputs(cid)
        assert proof_inputs(actual) == proof_inputs(expected)
        for scope, book in actual[1].items():
            assert len(book) == len(expected[1][scope])
            for current, old in zip(book, expected[1][scope]):
                physical = current["_physical_rows"]
                assert type(physical) is tuple and physical
                assert physical == tuple(sorted(set(physical)))
                assert physical == tuple(sorted(old["_physical_rows"]))
                assert all(type(position) is int for position in physical)
                assert any(row is current for row in actual[0])
        first = target["source_row"]
        repeated_first = [state for state in states if state[0] == first]
        assert len(repeated_first) == 64
        assert all(kind is tuple and positions == (first,) for _, kind, positions in repeated_first)
        if extra_positions:
            second = sorted({row["source_row"] for row in supports})[1]
            same_second = [state for state in states if state[0] == second]
            assert same_second[0] == (second, tuple, (first,))
            assert all(kind is set and positions == (first, second) for _, kind, positions in same_second[1:])
        expected_projection = legacy_projection(database, cid, monkeypatch)
        projected = pairs.project(cid)
        assert exact_value(projected[:2]) == exact_value(expected_projection[:2])
        assert proof_inputs((projected[2], {}, set())) == proof_inputs((expected_projection[2], {}, set()))


def test_inputs_reuse_only_equal_metadata_within_each_physical_member(client, database, make_case, monkeypatch):
    cid = make_case("SYN member metadata pointer sharing")
    raw = encoded(native_entries())
    add_source(client, cid, raw)
    add_source(client, cid, archive([("A.csv", raw), ("B.csv", raw)]), "members.zip")
    expected = legacy_projection(database, cid, monkeypatch)
    original = database.raw_rows

    @contextmanager
    def independent_metadata(query, params=None):
        with original(query, params) as fetched:
            def rows():
                for row in fetched:
                    if "_pair_sources as materialized" in query:
                        for field in ("source_id", "member_path", "parser_version", "member_sha256"):
                            row[field] = row[field].encode("utf-8").decode("utf-8")
                    yield row
            yield rows()

    with monkeypatch.context() as copied:
        copied.setattr(database, "raw_rows", independent_metadata)
        inputs = pairs._inputs(cid)
        actual = pairs.project(cid)
    assert exact_value(actual[:2]) == exact_value(expected[:2])
    assert proof_inputs((actual[2], {}, set())) == proof_inputs((expected[2], {}, set()))
    members = defaultdict(list)
    for row in inputs[0]:
        members[row["source_id"], row["member_path"]].append(row)
    assert len(members) == 3
    for group in members.values():
        assert len(group) == 4
        for field in ("source_id", "member_path", "parser_version", "member_sha256"):
            assert len({id(row[field]) for row in group}) == 1
    a, b = [group[0] for key, group in members.items() if key[1] in {"A.csv", "B.csv"}]
    assert a["source_id"] == b["source_id"] and a["source_id"] is not b["source_id"]
    assert a["parser_version"] == b["parser_version"] and a["parser_version"] is not b["parser_version"]


@pytest.mark.parametrize("source_roles", [False, True], ids=["events", "source-roles"])
def test_member_scratch_retires_without_losing_casewide_books_or_proofs(client, database, make_case, monkeypatch, source_roles):
    cid = make_case("SYN complete member scratch lifetime")
    rows = native_entries()
    rows.extend([dict(rows[1]), dict(rows[3])])
    raw = encoded(rows)
    add_source(client, cid, raw)
    _, imported = add_source(client, cid, archive([("A.csv", raw), ("B.csv", raw), ("C.csv", raw)]), "copies.zip")
    sid = imported["result"]["source_id"]
    first = database.q_raw("""select record_no,details_json from import_records
        where case_id=? and source_id=? and member_path='B.csv' order by source_row limit 1""", [cid, sid])[0]
    details = json.loads(first["details_json"])
    details.pop(source_ledger.FIELD)
    database.x("""update import_records set details_json=?,disposition='unresolved'
        where case_id=? and source_id=? and record_no=?""", [json.dumps(details), cid, sid, first["record_no"]])
    expected_inputs = casewide_inputs(cid)
    with monkeypatch.context() as frozen:
        frozen.setattr(pairs, "_inputs", casewide_inputs)
        expected_projection = pairs.project(cid, source_roles=source_roles)
    old_projection = legacy_projection(database, cid, monkeypatch, source_roles=source_roles)
    lines, start = inspect.getsourcelines(pairs._inputs)
    observed_line = start + lines.index('        evidence = row["evidence"]\n')
    states = []

    def observe(frame, event, arg):
        if event == "line" and frame.f_code is pairs._inputs.__code__ and frame.f_lineno == observed_line:
            row = frame.f_locals["row"]
            states.append(((row["source_id"], row["member_path"]), len(frame.f_locals["book_entries"]), row["evidence"] is None))
        return observe

    previous_trace = sys.gettrace()
    sys.settrace(observe)
    try:
        actual_inputs = pairs._inputs(cid)
    finally:
        sys.settrace(previous_trace)
    assert exact_value(actual_inputs) == exact_value(expected_inputs)
    transitions = [state for index, state in enumerate(states) if index == 0 or state[0] != states[index - 1][0]]
    assert len(transitions) == 4
    assert all(size == 0 for _, size, _ in transitions)
    assert any(member == (sid, "B.csv") and missing for member, _, missing in transitions)
    assert (sid, "B.csv") in actual_inputs[2]
    assert any(len(row["_physical_rows"]) > 1 for book in actual_inputs[1].values() for row in book)
    actual = pairs.project(cid, source_roles=source_roles)
    assert exact_value(actual[:2]) == exact_value(expected_projection[:2]) == exact_value(old_projection[:2])
    assert exact_value(actual[2]) == exact_value(expected_projection[2])
    assert proof_inputs((actual[2], {}, set())) == proof_inputs((old_projection[2], {}, set()))


@pytest.mark.parametrize("unknown_currency", [False, True], ids=["known-currency", "independent-unknown-currency"])
def test_inputs_share_validated_immutable_strings_without_changing_any_values(client, database, make_case, monkeypatch, unknown_currency):
    cid = make_case("SYN immutable proof scalar sharing")
    rows = native_entries()
    for row in rows:
        row["交易卡号"] = "SYN-CARD-" + row["交易账号"]
    add_source(client, cid, encoded(rows))
    if unknown_currency:
        for record in database.q_raw("select source_id,record_no,details_json from import_records where case_id=?", [cid]):
            details = json.loads(record["details_json"])
            evidence = json.loads(details[source_ledger.FIELD])
            evidence["source_currency"] = ""
            source_ledger.validate(json.dumps(evidence))
            details[source_ledger.FIELD] = json.dumps(evidence)
            database.x("update import_records set details_json=? where case_id=? and source_id=? and record_no=?",
                       [json.dumps(details), cid, record["source_id"], record["record_no"]])
    original = database.q_raw("select * from import_records where case_id=? order by source_id,record_no", [cid])
    expected = legacy_projection(database, cid, monkeypatch)
    inputs = pairs._inputs(cid)
    actual = pairs.project(cid)
    assert exact_value(actual[:2]) == exact_value(expected[:2])
    assert proof_inputs((actual[2], {}, set())) == proof_inputs((expected[2], {}, set()))
    assert len(actual[0]) == (0 if unknown_currency else 1)
    candidates, books, _ = inputs
    for key in ("policy", "source_primary_reference", "source_counterparty_bank", "independent_references"):
        assert len({id(next(name for name in row["contract"] if name == key)) for row in candidates}) == 1
    assert all(row["contract"]["policy"] is pairs.POLICY for row in candidates)
    assert all(sys.getsizeof(row["contract"]) < sys.getsizeof(dict(row["contract"])) for row in candidates)
    for kind in ("financial",):
        assert len({id(row["evidence"]["kind"]) for row in candidates if row["evidence"]["kind"] == kind}) == 1
    for direction in ("in", "out"):
        assert len({id(row["evidence"]["direction"]) for row in candidates if row["evidence"]["direction"] == direction}) == 1
    assert len({id(row["evidence"]["row"]["currency"]) for row in candidates}) == 1
    for row in candidates:
        evidence, source = row["evidence"], row["evidence"]["row"]
        anchor = books[source_ledger._scope(row)][0]["evidence"]
        assert source["self_account"] is anchor["row"]["self_account"]
        assert evidence["own_card"] is anchor["own_card"]
        assert source["self_account"] != evidence["own_card"]
        assert source["trade_no"] is row["contract"]["source_primary_reference"]
        for side in ("payer_account", "payee_account"):
            if source[side] == source["self_account"]:
                assert source[side] is source["self_account"]
        assert evidence["source_currency"] == ("" if unknown_currency else "CNY")
        assert source["currency"] == "CNY"
    assert database.q_raw("select * from import_records where case_id=? order by source_id,record_no", [cid]) == original


@pytest.mark.parametrize("third_position", [False, True], ids=["repeated-position", "late-cross-bank-third"])
def test_reference_overflow_uses_distinct_positions_across_all_banks(client, database, make_case, monkeypatch, third_position):
    cid = make_case("SYN complete member reference overflow")
    rows = native_entries()
    rows.extend(dict(rows[1]) for _ in range(8))
    if third_position:
        extra = dict(rows[1])
        extra.update({"交易金额": "11.13", "交易余额": "951.46", "交易时间": "2038-02-04 09:00:02",
                      "对手开户银行": "889900"})
        rows.append(extra)
    _, job = add_source(client, cid, encoded(rows))
    source_id = job["result"]["source_id"]
    candidates, _, _ = legacy_inputs(database, cid, monkeypatch)
    expected_keys = {(source_id, "table.csv", 1, "SYN-PAIR")} if third_position else set()
    assert pairs._reused_member_references(candidates) == expected_keys
    if third_position:
        extra = next(row for row in candidates if row["bank"] == "889900")
        for changed in ("source_id", "member_path", "header_scope"):
            replacement = dict(extra)
            replacement["evidence"] = dict(extra["evidence"])
            if changed == "header_scope":
                replacement["evidence"][changed] = 2
            else:
                replacement[changed] = "a" * 32 if changed == "source_id" else "other.csv"
            isolated = [replacement if row is extra else row for row in candidates]
            assert pairs._reused_member_references(isolated) == set()
    for roles in (False, True):
        expected = legacy_projection(database, cid, monkeypatch, source_roles=roles)
        actual = pairs.project(cid, source_roles=roles)
        assert exact_value(actual[:2]) == exact_value(expected[:2])
        assert proof_inputs((actual[2], {}, set())) == proof_inputs((expected[2], {}, set()))
        assert len(actual[0]) == (0 if third_position else 1)
        if roles and third_position:
            assert {row["reason"] for row in actual[1] if row["pending_group"]} == {"bank_pair_reference_reused"}


def test_member_reference_order_keeps_full_namespace_and_equal_key_input_order(monkeypatch):
    rows = []
    for source, member, header, reference in [
        ("source-b", "中.csv", 2, "a"), ("source-a", "é.csv", 1, "A"),
        ("source-a", "e\u0301.csv", 2, "a"), ("source-a", "é.csv", 2, "A"),
        ("source-b", "中.csv", 1, "a"), ("source-a", "é.csv", 1, "a"),
    ]:
        for bank in ("200", "", "100"):
            rows.append({"source_id": source, "member_path": member, "reference": reference,
                "bank": bank, "evidence": {"header_scope": header, "kind": "financial"},
                "position_path": None, "member_sha256": "a" * 64, "flow_id": len(rows) // 3,
                "source_row": len(rows) + 2, "record_no": len(rows) + 1})
    rows.insert(4, {"evidence": None, "record_no": 100})
    original = list(rows)
    expected = sorted((row for row in rows if row["evidence"]), key=lambda row:
        (row["source_id"], row["member_path"], row["evidence"]["header_scope"], row["reference"]))
    visited = []
    original_position = pairs._position

    def position(row):
        visited.append(row)
        return original_position(row)

    monkeypatch.setattr(pairs, "_position", position)
    assert pairs._reused_member_references(rows) == set()
    assert [id(row) for row in visited] == [id(row) for row in expected]
    assert [id(row) for row in rows] == [id(row) for row in original]


def test_pair_group_order_and_original_candidates_match_complete_old_projection(client, database, make_case, monkeypatch):
    cid = make_case("SYN complete stable pair groups")
    rows = []
    for index, (bank, reference) in enumerate((("200", "a"), ("100", "中"), ("100", "A"), ("", "z"))):
        group = native_entries()
        for row in group:
            for field in ("交易账号", "交易卡号", "交易对手账卡号"):
                if row[field]:
                    row[field] += f"-{index}"
            row["对手开户银行"] = bank
            row["交易流水号"] = reference if row["交易流水号"] == "SYN-PAIR" else row["交易流水号"] + f"-{index}"
        rows.extend(group)
    rows.extend((dict(rows[1]), dict(rows[1]), dict(rows[3])))
    add_source(client, cid, encoded(rows))
    original_prove, original_projection = pairs._prove, pairs._pair_projection
    proved = []

    def prove(pair, *args, **kwargs):
        proved.append([(row["bank"], row["reference"], row["record_no"]) for row in pair])
        return original_prove(pair, *args, **kwargs)

    def projection(candidates, *args, **kwargs):
        order = [id(row) for row in candidates]
        result = original_projection(candidates, *args, **kwargs)
        assert [id(row) for row in candidates] == order
        return result

    monkeypatch.setattr(pairs, "_prove", prove)
    monkeypatch.setattr(pairs, "_pair_projection", projection)
    for roles in (False, True):
        proved.clear()
        expected = legacy_projection(database, cid, monkeypatch, source_roles=roles)
        old_proved = list(proved)
        proved.clear()
        actual = pairs.project(cid, source_roles=roles)
        assert proved == old_proved
        assert len(actual[0]) == len(expected[0]) == 3
        assert exact_value(actual[:2]) == exact_value(expected[:2])
        assert proof_inputs((actual[2], {}, set())) == proof_inputs((expected[2], {}, set()))


@pytest.mark.parametrize("change", ["late-facts", "late-independent", "third-namespace", "tail-excluded"])
def test_pair_scratch_saturation_keeps_late_facts_all_members_and_exclusions(client, database, make_case, monkeypatch, change):
    cid = make_case("SYN complete pair scratch " + change)
    add_source(client, cid, encoded(native_entries()))
    original_inputs = legacy_inputs(database, cid, monkeypatch)
    a, b = [row for row in original_inputs[0] if row["reference"] == "SYN-PAIR"]
    prior_a, prior_b = [row for row in original_inputs[0] if row["reference"] != "SYN-PAIR"]
    group = [deepcopy(a) for _ in range(64)] + [deepcopy(b) for _ in range(64)]
    late = deepcopy(a)
    excluded = ()
    if change == "late-facts":
        late["evidence"]["row"]["balance"] = "962.58"
        group.append(late)
    elif change == "late-independent":
        late["contract"]["independent_references"] = True
        group.append(late)
    else:
        late["evidence"]["header_scope"] = 2
        late["flow_id"] = prior_a["flow_id"]
        tail = deepcopy(late)
        tail["flow_id"] = prior_b["flow_id"]
        group.extend((late, tail))
        if change == "tail-excluded":
            excluded = (str(tail["flow_id"]),)
    fixture = ([prior_a, prior_b, *group], original_inputs[1], original_inputs[2])
    assert source_ledger.validate(pairs._json(late["evidence"]))
    assert pairs.validate(pairs._json(late["contract"]))
    namespace_positions = defaultdict(set)
    for row in group:
        namespace_positions[row["source_id"], row["member_path"], row["evidence"]["header_scope"]].add(pairs._position(row))
    assert all(len(positions) <= 2 for positions in namespace_positions.values())
    assert len({pairs._position(row) for row in group}) == (2 if change.startswith("late-") else 4)
    original_position, original_reused = pairs._position, pairs._reused_member_references
    member_scan, scratch_visits = False, []

    def reused(rows):
        nonlocal member_scan
        member_scan = True
        try:
            return original_reused(rows)
        finally:
            member_scan = False

    def position(row):
        if not member_scan and row["reference"] == "SYN-PAIR":
            scratch_visits.append(row)
        return original_position(row)

    # Both algorithms receive the same validated observation values; this
    # scratch fixture does not claim the parser emits repeated identical rows.
    monkeypatch.setitem(globals(), "legacy_inputs", lambda *args, **kwargs: deepcopy(fixture))
    monkeypatch.setattr(pairs, "_inputs", lambda *args: deepcopy(fixture))
    for roles in (False, True):
        expected = legacy_projection(database, cid, monkeypatch, excluded_ids=excluded, source_roles=roles)
        scratch_visits.clear()
        with monkeypatch.context() as observed:
            observed.setattr(pairs, "_position", position)
            observed.setattr(pairs, "_reused_member_references", reused)
            actual = pairs.project(cid, excluded_ids=excluded, source_roles=roles)
        assert len(scratch_visits) == 129
        assert exact_value(actual[:2]) == exact_value(expected[:2])
        assert proof_inputs((actual[2], {}, set())) == proof_inputs((expected[2], {}, set()))
        assert not actual[0]
        if change == "tail-excluded":
            main = [row for row in actual[1] if row["flow_id"] in {str(a["flow_id"]), str(b["flow_id"])}]
            assert all(row["reason"] == "bank_pair_explicit_reference_takes_precedence" and not row["pending_group"] for row in main)


def test_pair_only_indexes_leave_scope_before_single_postings(client, database, make_case, monkeypatch):
    from app import bank_single_postings
    cid = make_case("SYN pair phase index lifetime")
    add_source(client, cid, encoded(native_entries()))
    expected = legacy_projection(database, cid, monkeypatch)
    original_reused, original_single = pairs._reused_member_references, bank_single_postings.project
    refs = []

    def reused(candidates):
        result = original_reused(candidates)
        refs.append(weakref.ref(result))
        return result

    def singles(*args):
        assert refs and all(ref() is None for ref in refs)
        return original_single(*args)

    monkeypatch.setattr(pairs, "_reused_member_references", reused)
    monkeypatch.setattr(bank_single_postings, "project", singles)
    actual = pairs.project(cid)
    assert exact_value(actual[:2]) == exact_value(expected[:2])


def add_reference_field_record(database, cid, reference, column, *, state="legacy", disposition="unresolved"):
    source_id = "SYN-NAMED-FIELD"
    source_case = cid + 1000 if state == "foreign" else cid
    member_case = cid + 1000 if state == "missing-member" else source_case
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,mapping_json,sheet,parser_version,row_kind)
        values (?,?, 'legacy.csv',?,0,'B','{}','','funds-trace-import-v1','csv_record')""", [source_id, source_case, "d" * 64])
    database.x("insert into import_members values (?,?, 'legacy.csv','table',?,0,'utf-8-sig',1)",
               [member_case, source_id, "e" * 64])
    details = json.dumps({"source_field_evidence": json.dumps([{"column": column, "raw": reference}])})
    database.x("insert into import_records values (?,?,1,'legacy.csv',2,'SYN-KEY',?,'SYN-REASON',?)",
               [source_case, source_id, disposition, details])
    if state in {"managed", "retired"}:
        batch = database.q_raw("select id from batches where case_id=? limit 1", [cid])[0]["id"]
        database.x("insert into import_source_operations values (?,?,?,?)", [source_case, source_id, batch, state == "managed"])


def add_paged_reference_records(database, cid, reference):
    source_id = "SYN-PAGED-REFERENCES"
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,mapping_json,sheet,parser_version,row_kind)
        values (?,?,'legacy.csv',?,0,'B','{}','','funds-trace-import-v1','csv_record')""", [source_id, cid, "d" * 64])
    record_nos = [-10, 0] + [index * 3 for index in range(1, 8191)] + [9223372036854775807]
    record_nos[-2] = 9007199254740993
    database.x("insert into import_members values (?,?, 'legacy.csv','table',?,0,'utf-8-sig',?)",
               [cid, source_id, "e" * 64, len(record_nos)])
    records = []
    for index, number in enumerate(record_nos):
        details, position = {}, 20000 + index
        if index in (4096, 8192):
            details = {"source_field_evidence": json.dumps([{"column": "交易流水号", "raw": reference}])}
            position = 7 if index == 4096 else 9
        records.append([cid, source_id, number, "legacy.csv", position, "SYN-key", "unresolved", "SYN-reason", json.dumps(details)])
    database.executemany("insert into import_records values (?,?,?,?,?,?,?,?,?)", records)
    batch = database.q_raw("select id from batches where case_id=? limit 1", [cid])[0]["id"]
    database.x("insert into import_source_operations values (?,?,?,true)", [cid, source_id, batch])
    return source_id, record_nos


def test_reference_pages_pass_empty_first_page_and_preserve_exact_bigint_physical_rows(client, database, make_case, monkeypatch):
    from app import bank_single_postings
    from test_bank_single_postings import entries as single_entries
    cid = make_case("SYN exact reference keyset pages")
    add_source(client, cid, encoded(single_entries()[:2]))
    assert len(pairs.project(cid)[0]) == 1
    source_id, numbers = add_paged_reference_records(database, cid, "SYN-SINGLE-1")
    candidates, _, _ = legacy_inputs(database, cid, monkeypatch)
    expected = legacy_known_references(cid, candidates)
    old_projection = legacy_projection(database, cid, monkeypatch)
    query, reader = database.q_raw, database.raw_rows
    pages, output, bounds = [], [], []

    def keys(sql, params=None):
        result = query(sql, params)
        if sql.lstrip().startswith("select r.record_no") and params[1] == source_id:
            bounds.append(params[2:])
            pages.append([row["record_no"] for row in result])
        return result

    @contextmanager
    def wide(sql, params=None):
        count, positions = 0, []
        with reader(sql, params) as raw:
            def observed():
                nonlocal count
                for row in raw:
                    count += 1
                    positions.append(row["source_row"])
                    yield row
            yield observed()
        if params[1] == source_id:
            assert params[2:4] == [params[-1][0], params[-1][-1]]
            output.append((count, positions))

    with monkeypatch.context() as observed:
        observed.setattr(database, "q_raw", keys)
        observed.setattr(database, "raw_rows", wide)
        actual = bank_single_postings._reused_primary_references(cid, candidates)
    assert actual == {reference for reference, positions in expected.items() if len(positions) != 1} == {"SYN-SINGLE-1"}
    assert [len(page) for page in pages] == [4096, 4096, 1, 0]
    assert [number for page in pages for number in page] == numbers
    assert bounds[0] == [None, None]
    assert output == [(0, []), (1, [7]), (1, [9])]
    projected = pairs.project(cid)
    assert exact_value(projected[:2]) == exact_value(old_projection[:2])
    assert proof_inputs((projected[2], {}, set())) == proof_inputs((old_projection[2], {}, set()))
    assert projected[0] == []
    assert {row["reason"] for row in projected[1] if row["flow_id"] == str(candidates[1]["flow_id"])} == {"single_posting_primary_reused"}
    with pytest.raises(ValueError, match="SYN rollback"):
        with database.transaction():
            database.x("update import_source_operations set active=false where case_id=? and source_id=?", [cid, source_id])
            assert bank_single_postings._reused_primary_references(cid, candidates) == set()
            raise ValueError("SYN rollback")
    assert bank_single_postings._reused_primary_references(cid, candidates) == actual


def test_reference_pages_keep_global_direct_digest_across_real_source_copies(client, database, make_case, monkeypatch):
    from app import bank_single_postings
    from test_bank_single_postings import entries as single_entries
    cid = make_case("SYN global direct physical reference copies")
    raw = encoded(single_entries()[:2])
    add_source(client, cid, raw)
    add_source(client, cid, archive([("copy.csv", raw)]), "copy.zip")
    candidates, _, _ = legacy_inputs(database, cid, monkeypatch)
    assert len({row["source_id"] for row in candidates}) == 2
    assert any(row["position_path"] is None for row in candidates)
    expected = legacy_known_references(cid, candidates)
    assert bank_single_postings._reused_primary_references(cid, candidates) == set()
    assert all(len(positions) == 1 for positions in expected.values())
    actual, old = pairs.project(cid), legacy_projection(database, cid, monkeypatch)
    assert len(actual[0]) == 1
    assert exact_value(actual[:2]) == exact_value(old[:2])
    assert proof_inputs((actual[2], {}, set())) == proof_inputs((old[2], {}, set()))


@pytest.mark.parametrize("private", [False, True], ids=["shared-locked", "private-snapshot"])
def test_reference_pages_see_one_complete_view_during_concurrent_retirement(client, database, make_case, monkeypatch, private):
    from contextlib import nullcontext
    from app import bank_single_postings
    from test_bank_single_postings import entries as single_entries
    cid = make_case("SYN coherent paged references " + str(private))
    add_source(client, cid, encoded(single_entries()[:2]))
    source_id, _ = add_paged_reference_records(database, cid, "SYN-SINGLE-1")
    candidates, _, _ = legacy_inputs(database, cid, monkeypatch)
    expected = {reference for reference, positions in legacy_known_references(cid, candidates).items() if len(positions) != 1}
    assert expected == {"SYN-SINGLE-1"}
    first_page, started, finished = threading.Event(), threading.Event(), threading.Event()
    reader = database.raw_rows
    snapshot_active = False

    def retire():
        assert first_page.wait(3)
        started.set()
        database.x("update import_source_operations set active=false where case_id=? and source_id=?", [cid, source_id])
        finished.set()

    @contextmanager
    def pause(sql, params=None):
        with reader(sql, params) as rows:
            yield rows
        if params[1] == source_id and not first_page.is_set():
            first_page.set()
            assert started.wait(3)
            if bool(snapshot_active):
                assert finished.wait(3), "private snapshot blocked the writer"
            else:
                assert not finished.wait(.05), "retirement crossed a locked reference scan"

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(retire)
        with monkeypatch.context() as observer:
            observer.setattr(database, "raw_rows", pause)
            with (database.read_snapshot() if private else nullcontext(False)) as snapshot_active:
                assert bank_single_postings._reused_primary_references(cid, candidates) == expected
        pending.result(3)
    assert finished.is_set()
    assert bank_single_postings._reused_primary_references(cid, candidates) == set()


@pytest.mark.parametrize("all_reused", [False, True])
def test_reused_primary_references_saturate_only_after_distinct_physical_positions(database, monkeypatch, all_reused):
    from app import bank_single_postings
    digest = "a" * 64
    direct_digest = "b" * 64

    def candidate(reference, position, *, direct=False):
        return {"reference": reference, "position_path": None if direct else "table.csv",
                "member_sha256": direct_digest if direct else digest, "source_row": position}

    candidates = [candidate("SYN-CANDIDATE-REUSED", 2), candidate("SYN-CANDIDATE-REUSED", 2),
                  candidate("SYN-CANDIDATE-REUSED", 3), candidate("SYN-LATE-REUSED", 4)]
    if not all_reused:
        candidates += [candidate("SYN-UNIQUE", 5), candidate("SYN-UNIQUE", 5),
                       candidate("SYN-DIRECT", 6, direct=True)]
    else:
        candidates.append(candidate("SYN-LATE-REUSED", 7))
    batches = [
        [("table.csv", 4, digest, "交易流水号", "SYN-LATE-REUSED")] * 4,
        [("table.csv", 7, digest, "交\\t易流水号", "SYN-LATE-REUSED"),
         ("table.csv", 5, digest, "交易流水号", "SYN-UNIQUE"),
         ("other-copy.csv", 6, direct_digest, "交易流水号", "SYN-DIRECT")],
        [("elsewhere.csv", 100, digest, "交易流水号", "SYN-UNRELATED"),
         ("table.csv", 8, digest, "prefix交易流水号suffix", "SYN-UNIQUE"),
         ("table.csv", 9, digest, 17, "SYN-UNIQUE")],
    ]
    scans = []

    class Cursor:
        def __init__(self):
            self.remaining = iter(batches)
            self.scan = []
            scans.append(self.scan)

        def fetchmany(self, size):
            assert size == 4096
            batch = next(self.remaining, [])
            self.scan.append(len(batch))
            return batch

    class Connection:
        def execute(self, sql, params):
            assert params[0] == 17
            return Cursor()

    original_identifier = pairs._identifier
    identifiers = []

    def identifier(value):
        identifiers.append(value)
        return original_identifier(value)

    monkeypatch.setattr(pairs, "_identifier", identifier)
    with monkeypatch.context() as old_cursor:
        old_cursor.setattr(database, "con", lambda: Connection())
        expected = legacy_known_references(17, candidates)
    old_identifiers = list(identifiers)
    identifiers.clear()
    page_rows, lower_bounds = [], []

    def keys(sql, params=None):
        assert params[0] == 17
        if sql.lstrip().startswith("select s.source_id"):
            return [{"source_id": "SYN-A"}, {"source_id": "SYN-B"}]
        source, lower = params[1:3]
        lower_bounds.append((source, lower))
        limit = 1 if source == "SYN-A" else 2
        next_key = 1 if lower is None else lower + 1
        return [{"record_no": next_key}] if next_key <= limit else []

    @contextmanager
    def wide(sql, params=None):
        assert "selected_records as materialized" in sql
        key = params[-1][0]
        assert params[2:] == [key, key, [key]]
        batch = batches[0 if params[1] == "SYN-A" else key]
        page_rows.append(len(batch))
        yield (dict(zip(("member_path", "source_row", "sha256", "source_column", "reference"), row)) for row in batch)

    monkeypatch.setattr(database, "q_raw", keys)
    monkeypatch.setattr(database, "raw_rows", wide)
    actual = bank_single_postings._reused_primary_references(17, candidates)
    assert type(actual) is set
    assert actual == {reference for reference, positions in expected.items() if len(positions) != 1}
    assert actual == {"SYN-CANDIDATE-REUSED", "SYN-LATE-REUSED"}
    assert scans == [[4, 3, 3, 0]]
    assert page_rows == [4, 3, 3]
    assert lower_bounds == [("SYN-A", None), ("SYN-A", 1), ("SYN-B", None), ("SYN-B", 1), ("SYN-B", 2)]
    assert identifiers == old_identifiers
    assert "SYN-UNRELATED" in identifiers


def test_single_group_order_and_complete_old_projection_stay_independent(client, database, make_case, monkeypatch):
    from app import bank_single_postings
    from test_bank_single_postings import entries as single_entries
    cid = make_case("SYN stable Single grouping and independent oracle")
    rows = single_entries()
    rows[1]["交易流水号"], rows[2]["交易流水号"] = "z-SYN-SINGLE", "A-SYN-SINGLE"
    raw = encoded(rows)
    add_source(client, cid, raw)
    add_source(client, cid, raw)
    expected = legacy_projection(database, cid, monkeypatch)
    assert len(expected[0]) == 2
    original = bank_single_postings.project
    orders = []

    def compare(case_id, candidates, books, unlocated, effective, current, memberships, excluded):
        assert len({row["reference"] for row in candidates}) < len(candidates)
        seed = deepcopy(memberships)
        for ordered in (list(reversed(candidates)), candidates[::2] + candidates[1::2], candidates):
            before = exact_value(ordered)
            old_memberships, new_memberships = deepcopy(seed), deepcopy(seed)
            old = legacy_single_projection(case_id, ordered, books, unlocated, effective, current, old_memberships, excluded)
            actual = original(case_id, ordered, books, unlocated, effective, current, new_memberships, excluded)
            assert exact_value((actual, new_memberships)) == exact_value((old, old_memberships))
            assert exact_value(ordered) == before
            orders.append([row["reference"] for row in ordered])
        memberships.clear()
        memberships.update(new_memberships)
        return actual

    monkeypatch.setattr(bank_single_postings, "project", compare)
    actual = pairs.project(cid)
    assert len(orders) == 3 and orders[0] != orders[-1]
    assert exact_value(actual[:2]) == exact_value(expected[:2])
    assert proof_inputs((actual[2], {}, set())) == proof_inputs((expected[2], {}, set()))

    def forbidden(*args, **kwargs):
        pytest.fail("the complete old oracle must not call current Single.project")

    monkeypatch.setattr(bank_single_postings, "project", forbidden)
    monkeypatch.setattr(bank_single_postings, "_cross_index", forbidden)
    monkeypatch.setattr(bank_single_postings, "_prepare", forbidden)
    frozen = legacy_projection(database, cid, monkeypatch)
    assert exact_value(frozen) == exact_value(expected)


def test_known_reference_query_returns_only_necessary_named_field_rows(client, database, make_case, monkeypatch):
    from app import bank_single_postings
    cid = make_case("SYN bounded named-field SQL output")
    add_source(client, cid, encoded(native_entries()[:2]))
    candidates, _, _ = legacy_inputs(database, cid, monkeypatch)
    expected = legacy_known_references(cid, candidates)
    original_rows = database.raw_rows
    returned = []

    @contextmanager
    def rows(query, params=None):
        assert "selected_records as materialized" in query
        with original_rows(query, params) as raw:
            def observed():
                for row in raw:
                    returned.append(tuple(row[field] for field in ("member_path", "source_row", "sha256", "source_column", "reference")))
                    yield row
            yield observed()

    monkeypatch.setattr(database, "raw_rows", rows)
    assert bank_single_postings._reused_primary_references(cid, candidates) == {ref for ref, positions in expected.items() if len(positions) != 1}
    # This measures the SQL result crossing into Python, not hash-build input
    # or a claim about DuckDB temporary disk consumption.
    assert len(returned) == 2
    assert {row[3] for row in returned} == {"交易流水号"}


@pytest.mark.parametrize("column,matching", [
    ("交易流水号", True), ("交\\t易流水号", True), ("交易流\\t水号", True),
    ("\u2003交易流水号\u3000", True), ("\\t\u00a0交易流水号\\t", True),
    ("交\t易流水号", False), ("prefix交易流水号suffix", False), (17, False), ("凭证号", False),
])
def test_known_reference_sql_prefilter_keeps_original_python_column_domain(client, database, make_case, monkeypatch, column, matching):
    from app import bank_single_postings
    cid = make_case("SYN named-reference column boundary")
    add_source(client, cid, encoded(native_entries()[:2]))
    add_reference_field_record(database, cid, "SYN-PAIR", column)
    candidates, _, _ = legacy_inputs(database, cid, monkeypatch)
    expected = legacy_known_references(cid, candidates)
    actual = bank_single_postings._reused_primary_references(cid, candidates)
    assert actual == {ref for ref, positions in expected.items() if len(positions) != 1}
    assert ("SYN-PAIR" in actual) == matching
    baseline = legacy_projection(database, cid, monkeypatch)
    projected = pairs.project(cid)
    assert exact_value(projected[:2]) == exact_value(baseline[:2])
    assert len(projected[0]) == (0 if matching else 1)


@pytest.mark.parametrize("state,disposition,matching", [
    ("legacy", "unresolved", True), ("managed", "auxiliary", True),
    ("legacy", "non_monetary", True), ("retired", "unresolved", False),
    ("legacy", "removed", False), ("foreign", "unresolved", False),
    ("missing-member", "unresolved", False),
])
def test_known_reference_sql_scope_keeps_legacy_active_and_unlinked_rows(client, database, make_case, monkeypatch, state, disposition, matching):
    from app import bank_single_postings
    cid = make_case("SYN named-reference source scope")
    add_source(client, cid, encoded(native_entries()[:2]))
    add_reference_field_record(database, cid, "SYN-PAIR", "交\\t易流水号", state=state, disposition=disposition)
    candidates, _, _ = legacy_inputs(database, cid, monkeypatch)
    expected = legacy_known_references(cid, candidates)
    actual = bank_single_postings._reused_primary_references(cid, candidates)
    assert actual == {ref for ref, positions in expected.items() if len(positions) != 1}
    assert ("SYN-PAIR" in actual) == matching
    assert exact_value(pairs.project(cid)[:2]) == exact_value(legacy_projection(database, cid, monkeypatch)[:2])


@pytest.mark.parametrize("shape", ["array", "object", "duplicate-key", "object-fields", "empty-array", "empty-object",
    "nested-array", "mixed-array", "string", "number", "boolean", "json-null", "sql-null", "missing"])
def test_known_reference_json_list_shape_matches_frozen_json_each(client, database, make_case, monkeypatch, shape):
    from app import bank_single_postings
    cid = make_case("SYN reference JSON child shapes")
    add_source(client, cid, encoded(native_entries()[:2]))
    add_reference_field_record(database, cid, "SYN-PAIR", "交易流水号")
    field = {"column": "交易流水号", "raw": "SYN-PAIR"}
    shapes = {"array": json.dumps([field]), "object": json.dumps({"one": field}),
        "duplicate-key": '{"same":{"column":"交易流水号","raw":"SYN-PAIR"},"same":{"column":"交易流水号","raw":"SYN-PREV-A"}}',
        "object-fields": json.dumps(field), "empty-array": "[]", "empty-object": "{}",
        "nested-array": json.dumps([[field]]), "mixed-array": json.dumps([None, 17, field, [field]]),
        "string": json.dumps("SYN scalar"), "number": "17", "boolean": "true", "json-null": "null", "sql-null": None}
    details = {} if shape == "missing" else {"source_field_evidence": shapes[shape]}
    database.x("update import_records set details_json=? where source_id='SYN-NAMED-FIELD'", [json.dumps(details)])
    candidates, _, _ = legacy_inputs(database, cid, monkeypatch)
    expected = legacy_known_references(cid, candidates)
    actual = bank_single_postings._reused_primary_references(cid, candidates)
    assert actual == {ref for ref, positions in expected.items() if len(positions) != 1}
    matching = shape in {"array", "object", "duplicate-key", "mixed-array"}
    assert ("SYN-PAIR" in actual) == matching
    assert ("SYN-PREV-A" in actual) == (shape == "duplicate-key")
    assert exact_value(pairs.project(cid)[:2]) == exact_value(legacy_projection(database, cid, monkeypatch)[:2])


@pytest.mark.parametrize("malformed", ["[", "{bad"])
def test_known_reference_selected_malformed_json_is_not_swallowed(client, database, make_case, monkeypatch, malformed):
    from app import bank_single_postings
    cid = make_case("SYN malformed selected reference JSON")
    add_source(client, cid, encoded(native_entries()[:2]))
    add_reference_field_record(database, cid, "SYN-PAIR", "交易流水号")
    database.x("update import_records set details_json=? where source_id='SYN-NAMED-FIELD'",
               [json.dumps({"source_field_evidence": malformed})])
    candidates, _, _ = legacy_inputs(database, cid, monkeypatch)
    with pytest.raises(Exception, match="Malformed JSON"):
        legacy_known_references(cid, candidates)
    with pytest.raises(Exception, match="Malformed JSON"):
        bank_single_postings._reused_primary_references(cid, candidates)


@pytest.mark.parametrize("field", ["payer_name", "summary", "trade_time_original", "trade_time_offset", "reported_status", "policy"])
def test_discarded_fields_are_validated_before_compaction(client, database, make_case, monkeypatch, field):
    cid = make_case("SYN invalid discarded posting field")
    _, job = add_source(client, cid, encoded(native_entries()))
    source_id = job["result"]["source_id"]
    record = database.q_raw("select record_no,details_json from import_records where case_id=? and source_id=? order by record_no limit 1", [cid, source_id])[0]
    details = json.loads(record["details_json"])
    ledger = json.loads(details[source_ledger.FIELD])
    if field == "policy":
        ledger[field] = "SYN unregistered ledger policy"
    elif field == "reported_status":
        ledger[field] = 17
    else:
        ledger["row"][field] = 17
    details[source_ledger.FIELD] = json.dumps(ledger)
    encoded_details = json.dumps(details)
    database.x("update import_records set details_json=? where case_id=? and source_id=? and record_no=?",
               [encoded_details, cid, source_id, record["record_no"]])
    with pytest.raises(ValueError):
        legacy_inputs(database, cid, monkeypatch)
    with pytest.raises(ValueError):
        pairs._inputs(cid)
    with pytest.raises(ValueError):
        pairs.source_role_pairs(cid)
    assert database.q_raw("select 1 ready") == [{"ready": 1}]
    assert database.q_raw("select details_json from import_records where case_id=? and source_id=? and record_no=?",
                          [cid, source_id, record["record_no"]]) == [{"details_json": encoded_details}]


def test_current_facts_mapping_is_complete_read_only_and_preserves_original_objects():
    from collections.abc import Mapping
    stamp = datetime(2040, 2, 3, 4, 5, 6, 123456, tzinfo=timezone(timedelta(hours=8)), fold=1)
    original = {"id": 0, "trade_no": "", "payer_account": "SYN-A", "payee_account": "SYN-B",
                "self_account": "SYN-A", "currency": "", "amount": Decimal("0.00"),
                "balance": None, "trade_time": stamp, "trade_time_utc": None}
    before = dict(original)
    row = pairs._CurrentFacts(original)
    assert isinstance(row, Mapping) and not isinstance(row, dict)
    assert len(row) == 10 and list(row) == list(before)
    assert list(row.keys()) == list(before) and list(row.items()) == list(before.items())
    assert all(row[key] is value and row.get(key, object()) is value for key, value in before.items())
    assert exact_value(dict(row)) == exact_value(before)
    default = object()
    for missing in ("missing", "items", "__slots__", "__class__"):
        with pytest.raises(KeyError):
            row[missing]
        assert row.get(missing, default) is default
    with pytest.raises(TypeError):
        row["id"] = 1
    with pytest.raises(TypeError):
        del row["balance"]
    with pytest.raises(AttributeError):
        row.id = 1
    with pytest.raises(AttributeError):
        del row.amount
    with pytest.raises(AttributeError):
        row.update({"id": 1})
    assert not hasattr(row, "__dict__")
    assert all(original[key] is value for key, value in before.items())
    original.update(trade_no="SYN-REASSIGNED", amount=Decimal("1.13"))
    assert all(row[key] is value for key, value in before.items())


@pytest.mark.parametrize("variant", ["equal", "typed", "decimal-scale", "numeric-type", "datetime-fold", "named-timezone"])
def test_project_streams_maps_and_shares_only_identical_representations(client, database, make_case, monkeypatch, variant):
    from app import bank_single_postings
    cid = make_case("SYN map representation sharing")
    rows = native_entries()
    rows[1].update({"交易金额": "37.50", "交易余额": "962.50"})
    rows[3].update({"交易金额": "37.50", "交易余额": "2037.50"})
    if variant == "typed":
        from test_native31_projection import scoped_pair
        add_source(client, cid, scoped_pair(rows), "typed.zip")
    else:
        add_source(client, cid, encoded(rows))
    journal = database.q_raw("select * from import_records where case_id=? order by source_id,record_no", [cid])
    flows = database.q_raw("select * from flows where case_id=? order by id", [cid])
    displayed = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()["items"]
    raw_zone = timezone(timedelta(hours=8), "SYN raw zone")
    effective_zone = timezone(timedelta(hours=8), "SYN effective zone")

    def with_zone(row, zone):
        row = dict(row)
        for key in ("trade_time", "trade_time_utc"):
            if row[key] is not None:
                row[key] = row[key].replace(tzinfo=zone)
        return row

    raw_transform = (lambda row: with_zone(row, raw_zone)) if variant == "named-timezone" else None

    def transform(row):
        row = dict(row)
        if variant == "decimal-scale":
            row["amount"] = row["amount"].quantize(Decimal("0.001"))
        elif variant == "numeric-type":
            row["amount"] = float(row["amount"])
        elif variant == "datetime-fold":
            for key in ("trade_time", "trade_time_utc"):
                if row[key] is not None:
                    row[key] = row[key].replace(fold=1)
        elif variant == "named-timezone":
            row = with_zone(row, effective_zone)
        return row

    with project_map_reads(database, monkeypatch, transform=transform, raw_transform=raw_transform) as old:
        expected = legacy_projection(database, cid, monkeypatch)
        expected_native = legacy_projection(database, cid, monkeypatch, source_roles=True)
    assert old["order"] == ["effective", "current"] * 2
    # Aware map rows no longer match the original naive ledger timestamps.
    assert len(expected[0]) == len(expected_native[0]) == (0 if variant == "named-timezone" else 1)
    if variant == "named-timezone":
        assert raw_zone == effective_zone and raw_zone is not effective_zone
    captured = {}
    original_single = bank_single_postings.project

    def capture(case_id, candidates, books, unlocated, effective, current, memberships, excluded):
        captured.update(effective=effective, current=current)
        return original_single(case_id, candidates, books, unlocated, effective, current, memberships, excluded)

    with monkeypatch.context() as consumer:
        consumer.setattr(bank_single_postings, "project", capture)
        with project_map_reads(database, monkeypatch, transform=transform, raw_transform=raw_transform, reject_bulk=True) as streamed:
            actual = pairs.project(cid)
            assert set(captured["effective"]) == set(old["effective"])
            assert set(captured["current"]) == set(old["current"])
            for key, effective in old["effective"].items():
                raw = old["current"][key]
                same = all(exact_value(value) == exact_value(raw[field]) for field, value in effective.items())
                assert (captured["effective"][key] is captured["current"][key]) is same
                assert exact_value({field: captured["effective"][key][field] for field in effective}) == exact_value(effective)
                if not same:
                    assert type(captured["effective"][key]) is dict
                    assert set(captured["effective"][key]) == set(effective)
                for field, value in effective.items():
                    compatible = exact_value(value) == exact_value(raw[field])
                    if isinstance(value, datetime):
                        compatible = compatible and value.tzinfo is raw[field].tzinfo
                    assert (captured["effective"][key][field] is captured["current"][key][field]) is compatible
            assert any(row["trade_time_utc"] is None for row in captured["current"].values())
            assert all(type(row) is pairs._CurrentFacts for row in captured["current"].values())
            currencies = {}
            for row in captured["current"].values():
                value = row["currency"]
                if type(value) is str and value in {"CNY", "USD", "EUR"}:
                    assert currencies.setdefault(value, value) is value
            assert exact_value(captured["current"]) == exact_value(old["current"])
            actual_native = pairs.project(cid, source_roles=True)
            expected_roles = []
            for event in expected_native[0]:
                proof = json.loads(event["evidence_json"])
                expected_roles.append({"flow_ids": event["flow_ids"], "source_id": proof["source_id"],
                    "member_path": proof["member_path"], "bank_code": proof["source_counterparty_bank"],
                    "own_accounts": {side["own_account"] for side in proof["sides"]}})
            assert pairs.source_role_pairs(cid) == expected_roles
    assert streamed["order"] == ["current", "effective"] * 3
    assert exact_value(actual[:2]) == exact_value(expected[:2])
    assert exact_value(actual_native[:2]) == exact_value(expected_native[:2])
    assert proof_inputs((actual[2], {}, set())) == proof_inputs((expected[2], {}, set()))
    assert database.q_raw("select * from import_records where case_id=? order by source_id,record_no", [cid]) == journal
    assert exact_value(database.q_raw("select * from flows where case_id=? order by id", [cid])) == exact_value(flows)
    assert client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()["items"] == displayed


@pytest.mark.parametrize("variant", ["equal", "first-conflict", "raw-subclass", "source-subclass", "zero-id", "unmatched-raw-id"])
def test_current_maps_share_only_equal_builtin_source_strings(client, database, make_case, monkeypatch, variant):
    from numpy import str_ as NonBuiltinText
    cid = make_case("SYN source/raw scalar sharing " + variant)
    add_source(client, cid, encoded(native_entries()))
    target = database.q_raw("select id from flows where case_id=? and trade_no='SYN-PAIR' order by id limit 1", [cid])[0]["id"]
    if variant == "zero-id":
        database.x("update flows set id=0 where case_id=? and id=?", [cid, target])
        database.x("update flow_sources set flow_id=0 where case_id=? and flow_id=?", [cid, target])
        target = 0
    fields = ("trade_no", "payer_account", "payee_account", "self_account", "currency")
    original_inputs, original_projection = pairs._inputs, pairs._pair_projection

    def inputs(*args, **kwargs):
        candidates, books, unlocated = original_inputs(cid)
        if variant == "source-subclass":
            for book in books.values():
                for row in book:
                    for field in fields:
                        row["evidence"]["row"][field] = NonBuiltinText(row["evidence"]["row"][field])
        if variant == "first-conflict":
            row = deepcopy(next(row for book in books.values() for row in book if row["flow_id"] == target))
            row["evidence"]["header_scope"] = 2
            row["evidence"]["own_card"] = "SYN-FIRST-CARD"
            row["evidence"]["row"].update(trade_no="SYN-FIRST-REFERENCE",
                payer_account="SYN-FIRST-OWNER", self_account="SYN-FIRST-OWNER")
            books = {source_ledger._scope(row): [row], **books}
        return candidates, books, unlocated

    fetched = {}

    def raw_transform(row):
        row = dict(row)
        if variant == "raw-subclass":
            for field in fields:
                row[field] = NonBuiltinText(row[field])
        if variant == "unmatched-raw-id" and row["id"] == target:
            row["id"] = "SYN-NONNUMERIC-RAW-ID"
        fetched[str(row["id"])] = dict(row)
        return row

    # Controlled representations supplement the public matrix above. Both
    # complete algorithms receive the same inputs and actual SQL row values.
    monkeypatch.setitem(globals(), "legacy_inputs", inputs)
    monkeypatch.setattr(pairs, "_inputs", inputs)
    with project_map_reads(database, monkeypatch, raw_transform=raw_transform) as old:
        expected = legacy_projection(database, cid, monkeypatch, source_roles=True)
    fetched.clear()
    captured = {}

    def capture(candidates, books, unlocated, effective, current, excluded, **kwargs):
        donors = {}
        for book in books.values():
            for row in book:
                if row["flow_id"] is not None:
                    donors.setdefault(int(row["flow_id"]), row["evidence"]["row"])
        captured.update(current=current, donors=donors)
        return original_projection(candidates, books, unlocated, effective, current, excluded, **kwargs)

    monkeypatch.setattr(pairs, "_pair_projection", capture)
    with project_map_reads(database, monkeypatch, raw_transform=raw_transform, reject_bulk=True) as streamed:
        actual = pairs.project(cid, source_roles=True)
    assert streamed["requested"] == list(reversed(old["requested"]))
    assert exact_value(captured["current"]) == exact_value(old["current"])
    for key, row in captured["current"].items():
        donor = captured["donors"].get(row["id"])
        assert set(row) == {"id", *fields, "amount", "balance", "trade_time", "trade_time_utc"}
        assert row is not donor
        if donor is None:
            assert row["id"] == "SYN-NONNUMERIC-RAW-ID"
            continue
        for field in fields:
            compatible = type(row[field]) is str and type(donor[field]) is str and row[field] == donor[field]
            if compatible:
                assert row[field] is donor[field]
            elif field != "currency":
                assert row[field] is fetched[key][field]
        saved = dict(donor)
        raw_saved = dict(row)
        for field in fields:
            donor[field] = "SYN-REASSIGNED-" + field
        assert all(row[field] is raw_saved[field] for field in fields)
        donor.update(saved)
    if variant == "zero-id":
        assert 0 in captured["donors"] and "0" in captured["current"]
    if variant == "first-conflict":
        assert captured["donors"][target]["trade_no"] == "SYN-FIRST-REFERENCE"
        assert captured["current"][str(target)]["trade_no"] is fetched[str(target)]["trade_no"]
    assert exact_value(actual[:2]) == exact_value(expected[:2])
    assert proof_inputs((actual[2], {}, set())) == proof_inputs((expected[2], {}, set()))


@pytest.mark.parametrize("currency", ["CNY", "USD", "EUR", "SYN", "cny", ""])
def test_map_currency_sharing_keeps_unknown_case_and_empty_raw_objects(client, database, make_case, monkeypatch, currency):
    from app import bank_single_postings
    cid = make_case("SYN literal map currency sharing boundary")
    add_source(client, cid, encoded(native_entries()))
    fetched = {}

    def transform(row):
        row = dict(row)
        row["currency"] = currency.encode().decode()
        return row

    def raw_transform(row):
        row = transform(row)
        fetched[str(row["id"])] = row["currency"]
        return row

    with project_map_reads(database, monkeypatch, transform=transform, raw_transform=raw_transform):
        expected = legacy_projection(database, cid, monkeypatch)
    fetched.clear()
    captured = {}
    original_single = bank_single_postings.project

    def capture(case_id, candidates, books, unlocated, effective, current, memberships, excluded):
        captured.update(effective=effective, current=current)
        return original_single(case_id, candidates, books, unlocated, effective, current, memberships, excluded)

    with monkeypatch.context() as consumer:
        consumer.setattr(bank_single_postings, "project", capture)
        with project_map_reads(database, monkeypatch, transform=transform, raw_transform=raw_transform, reject_bulk=True):
            actual = pairs.project(cid)
    raw_currencies = [row["currency"] for row in captured["current"].values()]
    assert all(type(value) is str and value == currency for value in raw_currencies)
    if currency in {"CNY", "USD", "EUR"}:
        assert all(value is raw_currencies[0] for value in raw_currencies)
    else:
        assert all(row["currency"] is fetched[key] for key, row in captured["current"].items())
    assert exact_value(actual[:2]) == exact_value(expected[:2])
    assert proof_inputs((actual[2], {}, set())) == proof_inputs((expected[2], {}, set()))


@pytest.mark.parametrize("link", ["present", "foreign", "missing"])
def test_project_keeps_candidate_book_and_cross_index_scopes_distinct(client, database, make_case, monkeypatch, link):
    from app import bank_single_postings
    from app.services import imports
    cid = make_case("SYN exact map and exclusion scopes")
    _, main = add_source(client, cid, encoded(native_entries()))
    main_id = main["result"]["source_id"]
    native_ids = {row["flow_id"] for row in database.q_raw("select flow_id from flow_sources where case_id=? and source_id=?", [cid, main_id])}
    record = database.q_raw("select details_json from import_records where case_id=? and source_id=? and record_no=2", [cid, main_id])[0]
    candidate_only = database.q_raw("select flow_id from flow_sources where case_id=? and source_id=? and source_row=2", [cid, main_id])[0]["flow_id"]
    details = json.loads(record["details_json"])
    details.pop(source_ledger.FIELD)
    database.x("update import_records set details_json=? where case_id=? and source_id=? and record_no=2", [json.dumps(details), cid, main_id])
    book = dict(native_entries()[0])
    book.update({"交易账号": "SYN-BOOK", "交易卡号": "SYN-BOOK", "交易流水号": "SYN-BOOK-ONLY",
                 "交易金额": "7.53", "交易币种": "USD", "交易对手账卡号": "SYN-OTHER"})
    with monkeypatch.context() as parser:
        parser.setattr(imports, "TABLE_PROFILE", table_import.SOURCE_LEDGER_PROFILE)
        _, book_job = add_source(client, cid, encoded([book]), "book-only.csv")
    book_source = book_job["result"]["source_id"]
    book_only = database.q_raw("select flow_id from flow_sources where case_id=? and source_id=?", [cid, book_source])[0]["flow_id"]
    linked = book_only
    if link == "foreign":
        other = make_case("SYN foreign requested book row")
        foreign = dict(book, **{"交易账号": "SYN-FOREIGN", "交易卡号": "SYN-FOREIGN"})
        add_source(client, other, encoded([foreign]))
        linked = database.q_raw("select id from flows where case_id=?", [other])[0]["id"]
    elif link == "missing":
        linked = 999999
    if link != "present":
        database.x("update flow_sources set flow_id=? where case_id=? and source_id=?", [linked, cid, book_source])
    requested_book = native_ids - {candidate_only} | {linked}
    current_ids = native_ids - {candidate_only} | ({book_only} if link == "present" else set())
    with project_map_reads(database, monkeypatch) as old:
        expected = legacy_projection(database, cid, monkeypatch)
    assert set(old["effective"]) == set(map(str, native_ids))
    assert set(old["current"]) == set(map(str, current_ids))
    assert old["exclusions"] == [current_ids]
    captured = {}
    original_single, original_cross = bank_single_postings.project, bank_single_postings._cross_index

    def capture(case_id, candidates, books, unlocated, effective, current, memberships, excluded):
        captured.update(effective=effective, current=current)
        return original_single(case_id, candidates, books, unlocated, effective, current, memberships, excluded)

    def cross(*args):
        captured["wanted"] = dict(args[3])
        result = original_cross(*args)
        captured["cross"] = result
        return result

    with monkeypatch.context() as consumers:
        consumers.setattr(bank_single_postings, "project", capture)
        consumers.setattr(bank_single_postings, "_cross_index", cross)
        with project_map_reads(database, monkeypatch, reject_bulk=True) as streamed:
            actual = pairs.project(cid)
    assert streamed["requested"] == [("current", cid, requested_book), ("effective", cid, native_ids)]
    assert set(captured["current"]) == set(map(str, current_ids))
    assert set(captured["effective"]) == set(map(str, native_ids))
    assert str(candidate_only) in captured["effective"] and str(candidate_only) not in captured["current"]
    assert str(book_only) not in captured["effective"]
    assert streamed["exclusions"] == [current_ids]
    fallback = {(row["self_account"], row["currency"], row["amount"]) for row in streamed["fallback"][0]}
    assert ("SYN-A", "CNY", Decimal("37.41")) in fallback
    assert (("SYN-BOOK", "USD", Decimal("7.53")) in fallback) is (link != "present")
    assert all(row["self_account"] != "SYN-FOREIGN" for row in streamed["fallback"][0])
    # The reused primary is never eligible for single-posting proof. Its
    # historical witness still participates in the intact fallback scan.
    key = ("SYN-A", "SYN-B", "CNY", Decimal("37.41"))
    assert key not in captured["wanted"] and key not in captured["cross"]
    assert exact_value(actual[:2]) == exact_value(expected[:2])
    assert proof_inputs((actual[2], {}, set())) == proof_inputs((expected[2], {}, set()))


@pytest.mark.parametrize("state", ["paired", "missing-links", "old-competitor"])
def test_unordered_fetch_restores_legacy_binary_order_and_exact_posting_proofs(client, database, make_case, monkeypatch, state):
    cid = make_case("SYN unordered posting source rows")
    rows = native_entries()
    rows.extend([dict(rows[1]), dict(rows[3])])
    raw = encoded(rows)
    add_source(client, cid, raw)
    paths = ["中.csv", "a.csv", "é.csv", "A.csv", "Ω.csv", "e\u0301.csv"]
    members = []
    for path in paths:
        context = dict(rows[0])
        context.update({"交易账号": "SYN-CONTEXT-" + path, "交易卡号": "SYN-CONTEXT-" + path,
                        "交易流水号": "SYN-CONTEXT-" + path})
        members.append((path, raw if path == "é.csv" else encoded([context for _ in rows])))
    _, support = add_source(client, cid, archive(members), "copies.zip")
    if state == "missing-links":
        database.x("""delete from flow_sources f where f.case_id=? and exists (
            select 1 from import_records r where r.case_id=f.case_id and r.source_id=f.source_id
              and r.record_no=f.source_row and r.source_row in (2,4))""", [cid])
    elif state == "old-competitor":
        from app.services import imports
        third = dict(rows[1])
        third.update({"交易时间": "2038-02-04 09:00:02", "交易余额": "925.18"})
        with monkeypatch.context() as old:
            old.setattr(imports, "TABLE_PROFILE", table_import.SOURCE_LEDGER_PROFILE)
            add_source(client, cid, encoded([third]), "old-competitor.csv")

    expected_inputs = legacy_inputs(database, cid, monkeypatch)
    expected_projection = legacy_projection(database, cid, monkeypatch, source_roles=True)
    assert len(expected_projection[0]) == (1 if state == "paired" else 0)
    source_count = database.q_raw('select count(*) n from import_sources where case_id=?', [cid])[0]['n']
    original, fetched = database.raw_rows, []

    @contextmanager
    def unordered(query, params=None):
        kind = ("current" if query.lstrip().startswith("select id,trade_no,") else
                "effective" if query.lstrip().startswith("select id,payer_account,") else "inputs")
        fetched.append(kind)
        with original(query, params) as raw:
            def reversed_batches():
                while batch := tuple(islice(raw, 7)):
                    yield from reversed(batch)
            yield reversed_batches()

    with monkeypatch.context() as fetch:
        fetch.setattr(database, "raw_rows", unordered)
        actual_inputs = pairs._inputs(cid)
        actual_projection = pairs.project(cid, source_roles=True)
    assert fetched == ["inputs"] * (2 * source_count) + ["current", "effective"]
    assert proof_inputs(actual_inputs) == proof_inputs(expected_inputs)
    assert actual_projection[:2] == expected_projection[:2]
    assert proof_inputs((actual_projection[2], {}, set())) == proof_inputs((expected_projection[2], {}, set()))
    members = [row["member_path"] for row in actual_inputs[0]
               if row["source_id"] == support["result"]["source_id"]]
    assert members == [path for path in ["A.csv", "a.csv", "e\u0301.csv", "é.csv", "Ω.csv", "中.csv"] for _ in rows]


@pytest.mark.parametrize('signed,amount,kind,subclass', [
    ('1.00', '1.00', 'financial', None),
    ('-1.00', '1.00', 'financial', None),
    ('1.0', '1.00', 'financial', None),
    ('1e0', '1.00', 'financial', None),
    ('-0.00', '0.00', 'ledger', None),
    ('-1.00', '-1.00', 'ledger', None),
    ('1.00', '1.00', 'financial', 'signed'),
    ('1.00', '1.00', 'financial', 'amount'),
])
def test_signed_amount_shares_only_identical_builtin_snapshot_text(client, database, make_case, monkeypatch, signed, amount, kind, subclass):
    cid = make_case('SYN immutable amount snapshot text')
    add_source(client, cid, encoded(native_entries()))
    record = database.q_raw('select source_id,record_no,details_json from import_records where case_id=? order by record_no limit 1', [cid])[0]
    details = json.loads(record['details_json'])
    evidence = json.loads(details[source_ledger.FIELD])
    evidence.update(signed_amount=signed, kind=kind)
    evidence['row']['amount'] = amount
    details[source_ledger.FIELD] = json.dumps(evidence)
    database.x('update import_records set details_json=? where case_id=? and source_id=? and record_no=?',
               [json.dumps(details), cid, record['source_id'], record['record_no']])
    stored = database.q_raw('select * from import_records where case_id=? order by record_no', [cid])
    flows = database.q_raw('select * from flows where case_id=? order by id', [cid])
    validate = source_ledger.validate

    class Text(str):
        pass

    def validated(value):
        result = validate(value)
        if subclass and result['row']['trade_no'] == 'SYN-PREV-A':
            if subclass == 'signed': result['signed_amount'] = Text(result['signed_amount'])
            else: result['row']['amount'] = Text(result['row']['amount'])
        return result

    with monkeypatch.context() as represented:
        represented.setattr(source_ledger, 'validate', validated)
        expected_inputs = before_scalar_inputs(cid)
        expected = before_scalar_project(cid)
        actual_inputs = pairs._inputs(cid)
        actual = pairs.project(cid)
    assert exact_value(actual_inputs) == exact_value(expected_inputs)
    assert exact_value(actual) == exact_value(expected)
    row = next(row for row in actual_inputs[0] if row['record_no'] == record['record_no'])
    value = row['evidence']
    assert str(value['signed_amount']) == signed and str(value['row']['amount']) == amount
    assert (value['signed_amount'] is value['row']['amount']) is (subclass is None and signed == amount)
    assert database.q_raw('select * from import_records where case_id=? order by record_no', [cid]) == stored
    assert exact_value(database.q_raw('select * from flows where case_id=? order by id', [cid])) == exact_value(flows)


@pytest.mark.parametrize('variant', ['equal-bigint', 'source-int-subclass', 'source-string', 'source-decimal', 'source-bool', 'raw-int-subclass'])
def test_raw_ids_borrow_only_equal_builtin_book_integers(client, database, make_case, monkeypatch, variant):
    cid = make_case('SYN immutable raw integer owner')
    add_source(client, cid, encoded(native_entries()))
    old_id = database.q_raw("select id from flows where case_id=? and trade_no='SYN-PAIR' order by id limit 1", [cid])[0]['id']
    target = 0 if variant == 'source-bool' else 9007199254741009
    database.x('update flows set id=? where case_id=? and id=?', [target, cid, old_id])
    database.x('update flow_sources set flow_id=? where case_id=? and flow_id=?', [target, cid, old_id])
    stored = database.q_raw('select * from import_records where case_id=? order by record_no', [cid])
    flows = database.q_raw('select * from flows where case_id=? order by id', [cid])
    frozen_inputs, original_projection = before_scalar_inputs, pairs._pair_projection

    class Integer(int):
        pass

    def inputs(case_id):
        result = frozen_inputs(case_id)
        for book in result[1].values():
            for row in book:
                if row['flow_id'] == target:
                    if variant == 'source-int-subclass': row['flow_id'] = Integer(target)
                    elif variant == 'source-string': row['flow_id'] = str(target)
                    elif variant == 'source-decimal': row['flow_id'] = Decimal(str(target))
                    elif variant == 'source-bool': row['flow_id'] = False
        return result

    fetched, captured = {}, {}

    def raw_transform(row):
        if row['id'] == target:
            row = dict(row)
            row['id'] = Integer(target) if variant == 'raw-int-subclass' else int(str(target))
            fetched['id'] = row['id']
        return row

    def capture(candidates, books, unlocated, effective, current, excluded, **kwargs):
        donor = next(row for book in books.values() for row in book if row['flow_id'] is not None and int(row['flow_id']) == target)
        captured.update(donor=donor, current=current)
        return original_projection(candidates, books, unlocated, effective, current, excluded, **kwargs)

    monkeypatch.setitem(globals(), 'before_scalar_inputs', inputs)
    monkeypatch.setattr(pairs, '_inputs', inputs)
    monkeypatch.setattr(pairs, '_pair_projection', capture)
    with project_map_reads(database, monkeypatch, raw_transform=raw_transform):
        expected = before_scalar_project(cid, source_roles=True)
    with project_map_reads(database, monkeypatch, raw_transform=raw_transform, reject_bulk=True):
        actual = pairs.project(cid, source_roles=True)
    assert exact_value(actual) == exact_value(expected)
    donor, raw = captured['donor'], captured['current'][str(target)]
    should_share = type(fetched['id']) is int and type(donor['flow_id']) is int and fetched['id'] == donor['flow_id']
    assert (raw['id'] is donor['flow_id']) is should_share
    assert exact_value(raw['id']) == exact_value(fetched['id'])
    with pytest.raises(TypeError):
        raw['id'] = 1
    before_id = raw['id']
    donor['flow_id'] = target + 17
    assert raw['id'] is before_id and raw['id'] == target
    assert database.q_raw('select * from import_records where case_id=? order by record_no', [cid]) == stored
    assert exact_value(database.q_raw('select * from flows where case_id=? order by id', [cid])) == exact_value(flows)


@pytest.mark.parametrize('source_roles', [False, True], ids=['events', 'source-roles'])
def test_dead_facts_release_before_member_list_and_returned_values_stay_alive(monkeypatch, source_roles):
    from app import db, bank_single_postings
    refs, calls = {}, []
    source = {'trade_no': 'SYN-ref', 'payer_account': 'SYN-A', 'payee_account': 'SYN-B',
              'self_account': 'SYN-A', 'currency': 'CNY'}
    candidates = [{'flow_id': value, 'evidence': {'row': dict(source)}} for value in (1, 2, 3)]

    class Marker:
        pass

    class Members(dict):
        def __iter__(self):
            # Only weak refs are retained by the observer. Row 3 is last, so
            # ordinary loop locals cannot accidentally keep row 1 alive.
            assert refs['dead']() is None
            assert refs['returned']() is not None
            calls.append('member-list')
            return super().__iter__()

    @contextmanager
    def rows(query, params=None):
        current = query.lstrip().startswith('select id,trade_no,')
        assert params == [17, [1, 2, 3], 1, 3]

        def generate():
            for value in (1, 2, 3):
                row = dict(id=value, **source, amount=Decimal('1.00'), trade_time=None, trade_time_utc=None)
                if current:
                    row['balance'] = None
                    if value in (1, 2):
                        row['balance'] = Marker()
                        refs['dead' if value == 1 else 'returned'] = weakref.ref(row['balance'])
                else:
                    del row['trade_no']
                yield row

        iterator = generate()
        try:
            yield iterator
        finally:
            iterator.close()

    def paired(rows, books, unlocated, effective, current, excluded, **kwargs):
        assert refs['dead']() is not None and refs['returned']() is not None
        assert kwargs == {'source_roles': source_roles}
        calls.append('pair')
        return [{'retained': current['2']['balance']}], Members({str(value): {'flow_id': str(value)} for value in (1, 2, 3)})

    def single(*args):
        assert refs['dead']() is not None
        calls.append('single')
        return []

    monkeypatch.setattr(pairs, '_inputs', lambda _: (candidates, {('SYN-source', 'SYN-member'): candidates}, set()))
    monkeypatch.setattr(db, 'raw_rows', rows)
    monkeypatch.setattr(pairs, '_pair_projection', paired)
    monkeypatch.setattr(bank_single_postings, 'project', single)
    events, members, observations = pairs.project(17, source_roles=source_roles)
    assert calls == ['pair', *([] if source_roles else ['single']), 'member-list']
    assert observations is candidates and members == [{'flow_id': str(value)} for value in (1, 2, 3)]
    assert events[0]['retained'] is refs['returned']() and refs['dead']() is None


@pytest.mark.parametrize('source_roles', [False, True], ids=['events', 'source-roles'])
@pytest.mark.parametrize('linked', [False, True], ids=['empty-queries', 'linked'])
def test_return_release_keeps_complete_W_projection(client, database, make_case, source_roles, linked):
    cid = make_case('SYN return fact-container release')
    add_source(client, cid, encoded(native_entries()))
    if not linked:
        database.x('delete from flow_sources where case_id=?', [cid])
    expected = before_release_project(cid, source_roles=source_roles)
    actual = pairs.project(cid, source_roles=source_roles)
    assert exact_value(actual) == exact_value(expected)
    assert len(actual[0]) == (1 if linked else 0)
    assert len(actual[1]) == (4 if linked else 0)
    assert len(actual[2]) == 4


@pytest.mark.parametrize('source_roles', [False, True], ids=['events', 'source-roles'])
def test_return_release_preserves_empty_candidate_early_exit(monkeypatch, source_roles):
    from app import db
    monkeypatch.setattr(pairs, '_inputs', lambda _: ([], {}, set()))

    @contextmanager
    def forbidden(*args, **kwargs):
        raise AssertionError('Empty candidates must not read raw/effective maps')
        yield

    monkeypatch.setattr(db, 'raw_rows', forbidden)
    assert pairs.project(17, source_roles=source_roles) == before_release_project(17, source_roles=source_roles) == ([], [], [])


def raw_page_case(database, make_case):
    cid, foreign = make_case('SYN raw page case'), make_case('SYN foreign raw page case')
    valid = [-9, 0, *range(1000, 5098), 9007199254740993, 9223372036854775807]
    wanted = sorted([*valid, 42, 43])
    source = {'trade_no': 'SYN-raw-page', 'payer_account': 'SYN-A', 'payee_account': 'SYN-B',
              'self_account': 'SYN-A', 'currency': 'CNY', 'amount': '1.13', 'balance': '9.00',
              'trade_time': None, 'trade_time_utc': None}
    records = [{'flow_id': value, 'evidence': {'row': dict(source)}} for value in wanted]
    candidate_only = {'flow_id': 600, 'evidence': {'row': dict(source)}}
    candidates = [*records[::2], records[3], records[0], candidate_only, {'flow_id': None}]
    with database.transaction() as connection:
        connection.executemany("""insert into flows(id,case_id,trade_no,payer_account,payee_account,self_account,
            currency,amount,balance) values (?,?,'SYN-raw-page','SYN-A','SYN-B','SYN-A','CNY','1.13','9.00')""",
            [(value, cid) for value in [*valid, 3, 600, 20000]] + [(42, foreign)])
    return cid, candidates, {('SYN-scope',): records}, valid, wanted


@pytest.mark.parametrize('source_roles', [False, True], ids=['events', 'source-roles'])
def test_raw_pages_keep_exact_X_maps_and_bound_each_native_result(database, make_case, monkeypatch, source_roles):
    from app import bank_single_postings
    cid, candidates, books, valid, wanted = raw_page_case(database, make_case)
    monkeypatch.setattr(pairs, '_inputs', lambda _: (deepcopy(candidates), deepcopy(books), set()))
    snapshots = []

    def capture(rows, book, unlocated, effective, current, excluded, **kwargs):
        snapshots.append((exact_value(current), exact_value(effective), set(current), set(effective)))
        return [], {}

    monkeypatch.setattr(pairs, '_pair_projection', capture)
    monkeypatch.setattr(bank_single_postings, 'project', lambda *args: [])
    with database.import_transaction():
        expected = before_raw_pages_project(cid, source_roles=source_roles)
    original, statements, state = database.raw_rows, [], {'active': False}

    @contextmanager
    def observe(query, params=None):
        raw = query.lstrip().startswith('select id,trade_no,')
        assert not state['active']
        if raw:
            statements.append((query, params))
        with original(query, params) as records:
            state['active'] = True
            finished = False

            def consume():
                nonlocal finished
                yield from records
                finished = True

            try:
                yield consume()
            finally:
                state['active'] = False
        assert finished

    monkeypatch.setattr(database, 'raw_rows', observe)
    with database.import_transaction():
        actual = pairs.project(cid, source_roles=source_roles)
    assert exact_value(actual) == exact_value(expected)
    assert snapshots[0] == snapshots[1]
    assert snapshots[1][2] == {str(value) for value in valid}
    assert '600' in snapshots[1][3] and '600' not in snapshots[1][2]
    assert not {'3', '42', '43', '20000'} & snapshots[1][2]
    assert len(statements) == 2
    assert [value for _, params in statements for value in params[1]] == wanted
    for query, params in statements:
        assert 0 < len(params[1]) <= 4096
        assert params[2:] == [params[1][0], params[1][-1]]
        assert 'id in (select unnest(?::bigint[]))' in query
        assert 'id between ?::bigint and ?::bigint' in query
    assert not state['active']


@pytest.mark.parametrize('private', [False, True], ids=['shared-lock', 'private-snapshot'])
def test_raw_pages_keep_one_view_across_concurrent_writer(database, make_case, monkeypatch, private):
    from contextlib import nullcontext
    from app import bank_single_postings
    cid, candidates, books, _, _ = raw_page_case(database, make_case)
    monkeypatch.setattr(pairs, '_inputs', lambda _: (deepcopy(candidates), deepcopy(books), set()))
    started, finished, captured = threading.Event(), threading.Event(), {}
    snapshot_active = False
    original, pages, futures = database.raw_rows, [], []

    def write():
        started.set()
        with database.transaction():
            database.x('update flows set balance=? where case_id=? and id=?', ['9.01', cid, 9223372036854775807])
        finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        @contextmanager
        def observe(query, params=None):
            raw = query.lstrip().startswith('select id,trade_no,')
            if raw: pages.append(params)
            with original(query, params) as records:
                yield records
            if raw and len(pages) == 1:
                futures.append(pool.submit(write))
                assert started.wait(3)
                if snapshot_active:
                    assert finished.wait(3)
                else:
                    assert not finished.wait(0.05)

        def capture(rows, book, unlocated, effective, current, excluded, **kwargs):
            captured['balance'] = current['9223372036854775807']['balance']
            return [], {}

        monkeypatch.setattr(database, 'raw_rows', observe)
        monkeypatch.setattr(pairs, '_pair_projection', capture)
        monkeypatch.setattr(bank_single_postings, 'project', lambda *args: [])
        context = database.read_snapshot() if private else nullcontext(False)
        with context as snapshot_active:
            pairs.project(cid)
        assert len(pages) == 2
        for future in futures: future.result(timeout=3)
    assert captured['balance'] == Decimal('9.00')
    assert database.q_raw('select balance from flows where case_id=? and id=?', [cid, 9223372036854775807]) == [{'balance': Decimal('9.01')}]


@pytest.mark.parametrize('source_roles', [False, True], ids=['events', 'source-roles'])
@pytest.mark.parametrize('linked', [False, True], ids=['empty-book-ids', 'linked'])
def test_raw_pages_keep_complete_public_X_projection(client, database, make_case, source_roles, linked):
    cid = make_case('SYN exact raw-page public projection')
    add_source(client, cid, encoded(native_entries()))
    if not linked: database.x('delete from flow_sources where case_id=?', [cid])
    expected = before_raw_pages_project(cid, source_roles=source_roles)
    actual = pairs.project(cid, source_roles=source_roles)
    assert exact_value(actual) == exact_value(expected)
    assert len(actual[0]) == (1 if linked else 0)


@pytest.mark.parametrize('source_roles', [False, True], ids=['events', 'source-roles'])
def test_effective_pages_continue_after_empty_page_and_keep_typed_values(database, make_case, monkeypatch, source_roles):
    from app import bank_single_postings
    cid, _, books, _, _ = raw_page_case(database, make_case)
    candidates = [*next(iter(books.values())), {'flow_id': 600}, {'flow_id': 20000}, {'flow_id': None}]
    ids = sorted({int(row['flow_id']) for row in candidates if row['flow_id'] is not None})
    with database.transaction() as connection:
        connection.execute('update flows set amount=0 where case_id=? and id in (select unnest(?::bigint[]))', [cid, ids[:4096]])
        connection.execute('insert into source_ledger_index(case_id,flow_id) select case_id,id from flows where case_id=? and id in (select unnest(?::bigint[]))', [cid, ids[:4096]])
        connection.execute("insert into resolution_entities values (?,'SYN-TYPED-A','account','SYN-A','SYN-A','','{}')", [cid])
        connection.execute("""insert into resolved_endpoints(case_id,flow_id,role,entity_id,state,reason)
            values (?,9223372036854775807,'payer','SYN-TYPED-A','confirmed','SYN-test')""", [cid])
    monkeypatch.setattr(pairs, '_inputs', lambda _: (deepcopy(candidates), deepcopy(books), set()))
    snapshots = []

    def capture(rows, book, unlocated, effective, current, excluded, **kwargs):
        snapshots.append((exact_value(current), exact_value(effective)))
        if effective:
            target = '9223372036854775807'
            assert type(effective[target]) is dict and set(effective[target]) == {
                'id','payer_account','payee_account','self_account','currency','amount','trade_time','trade_time_utc'}
            assert effective[target]['payer_account'] == 'SYN-TYPED-A' and current[target]['payer_account'] == 'SYN-A'
            assert '20000' in effective and '20000' not in current
        return [], {}

    monkeypatch.setattr(pairs, '_pair_projection', capture)
    monkeypatch.setattr(bank_single_postings, 'project', lambda *args: [])
    with database.import_transaction(): expected = before_effective_pages_project(cid, source_roles=source_roles)
    original, queries, counts = database.raw_rows, [], []

    @contextmanager
    def observe(query, params=None):
        effective = query.lstrip().startswith('select id,payer_account,')
        if effective: queries.append((query, params))
        count = 0
        with original(query, params) as rows:
            def consume():
                nonlocal count
                for row in rows:
                    count += 1
                    yield row
            yield consume()
        if effective: counts.append(count)

    monkeypatch.setattr(database, 'raw_rows', observe)
    with database.import_transaction(): actual = pairs.project(cid, source_roles=source_roles)
    assert exact_value(actual) == exact_value(expected) and snapshots[0] == snapshots[1]
    assert len(queries) == 2 and counts[0] == 0 and counts[1] > 0
    assert [value for _, params in queries for value in params[1]] == ids
    for query, params in queries:
        assert len(params[1]) <= 4096 and params[2:] == [params[1][0], params[1][-1]]
        assert 'id between ?::bigint and ?::bigint' in query


@pytest.mark.parametrize('private', [False, True], ids=['shared-lock', 'private-snapshot'])
def test_effective_pages_keep_one_view_across_concurrent_writer(database, make_case, monkeypatch, private):
    from contextlib import nullcontext
    from app import bank_single_postings
    cid, _, books, _, _ = raw_page_case(database, make_case)
    candidates = [*next(iter(books.values())), {'flow_id': 600}, {'flow_id': None}]
    monkeypatch.setattr(pairs, '_inputs', lambda _: (deepcopy(candidates), deepcopy(books), set()))
    started, finished, captured = threading.Event(), threading.Event(), {}
    snapshot_active = False
    original, pages, futures = database.raw_rows, [], []

    def write():
        started.set()
        with database.transaction():
            database.x('update flows set payer_account=? where case_id=? and id=?', ['SYN-LATE-A', cid, 9223372036854775807])
        finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        @contextmanager
        def observe(query, params=None):
            effective = query.lstrip().startswith('select id,payer_account,')
            if effective: pages.append(params)
            with original(query, params) as rows: yield rows
            if effective and len(pages) == 1:
                futures.append(pool.submit(write))
                assert started.wait(3)
                if snapshot_active: assert finished.wait(3)
                else: assert not finished.wait(0.05)

        def capture(rows, book, unlocated, effective, current, excluded, **kwargs):
            captured['payer'] = effective['9223372036854775807']['payer_account']
            return [], {}

        monkeypatch.setattr(database, 'raw_rows', observe)
        monkeypatch.setattr(pairs, '_pair_projection', capture)
        monkeypatch.setattr(bank_single_postings, 'project', lambda *args: [])
        context = database.read_snapshot() if private else nullcontext(False)
        with context as snapshot_active: pairs.project(cid)
        assert len(pages) == 2
        for future in futures: future.result(timeout=3)
    assert captured['payer'] == 'SYN-A'
    assert database.q_raw('select payer_account from flows where case_id=? and id=?', [cid, 9223372036854775807]) == [{'payer_account': 'SYN-LATE-A'}]


@pytest.mark.parametrize('source_roles', [False, True], ids=['events', 'source-roles'])
@pytest.mark.parametrize('linked', [False, True], ids=['empty-ids', 'linked'])
def test_effective_pages_keep_complete_public_Y_projection(client, database, make_case, source_roles, linked):
    cid = make_case('SYN exact effective-page public projection')
    add_source(client, cid, encoded(native_entries()))
    if not linked: database.x('delete from flow_sources where case_id=?', [cid])
    expected = before_effective_pages_project(cid, source_roles=source_roles)
    actual = pairs.project(cid, source_roles=source_roles)
    assert exact_value(actual) == exact_value(expected)
    assert len(actual[0]) == (1 if linked else 0)


def before_input_pages_inputs(case_id):
    from app import db, table_import
    from app.table_cancellation_links import active_support_sql, ordinary_csv_member
    validate, FIELD, POLICY, PROFILES = pairs.validate, pairs.FIELD, pairs.POLICY, pairs.PROFILES
    _identifier, _facts = pairs._identifier, pairs._facts
    # Frozen complete case-wide Z input implementation, before payload paging.
    # Do not carry the full source-details payload through link joins/sorting.
    # Select the original source/member scope before extracting its contracts.
    rows = []
    member_metadata = {}
    contract_keys = {key: key for key in ("policy", "source_primary_reference", "source_counterparty_bank", "independent_references")}
    kinds = {value: value for value in ("financial", "context", "ledger")}
    directions = {value: value for value in ("in", "out", "unknown")}
    currencies = {value: value for value in ("CNY", "USD", "EUR")}
    references, unlocated, direct_members = set(), set(), set()
    with db.raw_rows(f"""with _pair_sources as materialized (
        select s.case_id,s.source_id,s.parser_version,s.filename,s.sheet,s.row_kind,s.sha256
        from import_sources s where s.case_id=?
          and s.parser_version in (select unnest(?::varchar[])) and {active_support_sql('s')}
        ), _pair_records as materialized (
        select r.case_id,r.source_id,r.record_no,r.member_path,r.source_row,r.disposition,
            json_extract_string(r.details_json,'$.{source_ledger.FIELD}') ledger_evidence,
            json_extract_string(r.details_json,'$.{FIELD}') pair_evidence,
            json_extract_string(r.details_json,'$.trade_no_basis') trade_no_basis
        from import_records r where r.case_id=? and r.disposition<>'removed'
          and exists (select 1 from _pair_sources s where s.case_id=r.case_id and s.source_id=r.source_id)
          and exists (select 1 from import_members m where m.case_id=r.case_id
            and m.source_id=r.source_id and m.member_path=r.member_path)
        ) select r.source_id,r.record_no,r.member_path,r.source_row,r.disposition,
        s.parser_version,s.filename,s.sheet,s.row_kind,s.sha256 source_sha256,
        m.sha256 member_sha256,m.role,m.encoding,f.flow_id,
        r.ledger_evidence,r.pair_evidence,r.trade_no_basis
        from _pair_records r join _pair_sources s on s.case_id=r.case_id and s.source_id=r.source_id
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
        left join flow_sources f on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no""",
        [case_id, sorted(table_import.SOURCE_LEDGER_PROFILES), case_id]) as raw:
        for fetched in raw:
            contract = validate(fetched["pair_evidence"]) if fetched["pair_evidence"] is not None else None
            evidence = source_ledger.validate(fetched["ledger_evidence"]) if fetched["ledger_evidence"] is not None else None
            if ordinary_csv_member(fetched["parser_version"], {"filename": fetched["filename"], "sheet": fetched["sheet"],
                    "row_kind": fetched["row_kind"], "sha256": fetched["source_sha256"]}, {"member_path": fetched["member_path"],
                    "sha256": fetched["member_sha256"], "role": fetched["role"], "encoding": fetched["encoding"]}):
                direct_members.add(fetched["member_sha256"])
            if contract is not None:
                contract = {contract_keys[key]: value for key, value in contract.items()}
                contract["policy"] = POLICY
            authority = contract is not None and fetched["parser_version"] in PROFILES
            reference = (contract["source_primary_reference"] if authority else
                         _identifier(evidence["row"]["trade_no"]) if evidence and
                         fetched["trade_no_basis"] == "source_transaction_id" else "")
            bank = (contract["source_counterparty_bank"] if authority else _identifier(
                evidence["row"]["payee_bank" if evidence["direction"] == "out" else "payer_bank"]) if evidence else "")
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
                evidence = {key: evidence[key] for key in ("kind", "header_scope", "source_currency", "direction",
                    "own_card", "signed_amount", "source_direction", "balance_before")}
                evidence["row"] = {key: source[key] for key in ("trade_no", "payer_account", "payee_account",
                    "self_account", "amount", "balance", "currency", "trade_time", "trade_time_utc")}
                for key, literals in (("kind", kinds), ("direction", directions), ("source_currency", currencies)):
                    value = evidence[key]
                    if type(value) is str:
                        evidence[key] = literals.get(value, value)
                value = evidence["row"]["currency"]
                if type(value) is str:
                    evidence["row"]["currency"] = currencies.get(value, value)
                del source
            row = {key: fetched[key] for key in ("source_id", "record_no", "member_path", "source_row", "disposition",
                "parser_version", "member_sha256", "flow_id", "trade_no_basis")}
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
    del member_metadata
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


def input_page_case(client, database, make_case, *, copy_paths=('中.csv', 'A.csv')):
    cid = make_case('SYN source-PK-paged posting inputs')
    raw = encoded(native_entries())
    _, imported = add_source(client, cid, raw)
    source_id = imported['result']['source_id']
    add_source(client, cid, archive([(path, raw) for path in copy_paths]), 'copies.zip')
    first = [-7, 0] + [10 + 2 * index for index in range(4094)]
    tail = [9007199254740993, 9007199254741053, 9223372036854775806, 9223372036854775807]
    records = database.q_raw('select record_no from import_records where case_id=? and source_id=? order by record_no',
                             [cid, source_id])
    assert len(records) == 4
    with database.transaction() as connection:
        for record, number in zip(records, tail):
            connection.execute('update import_records set record_no=? where case_id=? and source_id=? and record_no=?',
                               [number, cid, source_id, record['record_no']])
            connection.execute('update flow_sources set source_row=? where case_id=? and source_id=? and source_row=?',
                               [number, cid, source_id, record['record_no']])
        connection.executemany("""insert into import_records values
            (?, ?, ?, 'table.csv', ?, 'SYN-removed', 'removed', 'SYN-removed', '{}')""",
            [(cid, source_id, number, index + 100) for index, number in enumerate(first)])
    return cid, source_id, first, tail


@pytest.mark.parametrize('leading', ['empty-page', 'support-prefix'])
def test_input_pages_drain_empty_payloads_and_keep_complete_source_pk_scope(client, database, make_case, monkeypatch, leading):
    cid, source_id, first, tail = input_page_case(client, database, make_case)
    if leading == 'support-prefix':
        details = json.loads(database.q_raw('select details_json from import_records where case_id=? and source_id=? and record_no=?',
                                            [cid, source_id, tail[0]])[0]['details_json'])
        evidence = json.loads(details[source_ledger.FIELD])
        evidence.update(kind='context', issues=['balance_amount_conflict'])
        evidence['row'].update(trade_no='SYN-PREFIX', trade_time='2038-02-04T08:59:59')
        details[source_ledger.FIELD] = json.dumps(evidence)
        details.pop(pairs.FIELD)
        database.x("update import_records set details_json=?,disposition='unresolved' where case_id=? and source_id=? and record_no=?",
                   [json.dumps(details), cid, source_id, first[0]])
        database.x("update import_records set disposition='unresolved' where case_id=? and source_id=? and record_no=?",
                   [cid, source_id, first[1]])
    expected = before_input_pages_inputs(cid)
    stored = database.q_raw('select * from import_records where case_id=? order by source_id,record_no', [cid])
    links = database.q_raw('select * from flow_sources where case_id=? order by source_id,source_row', [cid])
    original_rows, original_pages = database.raw_rows, source_ledger._source_record_pages
    payloads, keys, state = [], [], {'active': False}

    def pages(connection, case_id):
        class Connection:
            def execute(self, query, params=None):
                assert not state['active'], 'the previous payload must be exhausted before key enumeration'
                if 'limit 4096' in query:
                    keys.append(params)
                return connection.execute(query, params or [])

        yield from original_pages(Connection(), case_id)

    @contextmanager
    def observe(query, params=None):
        assert not state['active']
        item = {'query': query, 'params': params, 'count': 0, 'exhausted': False}
        payloads.append(item)
        with original_rows(query, params) as rows:
            state['active'] = True

            def consume():
                for row in rows:
                    item['count'] += 1
                    yield row
                item['exhausted'] = True

            try:
                yield consume()
            finally:
                state['active'] = False
        assert item['exhausted']

    with monkeypatch.context() as bounded:
        bounded.setattr(source_ledger, '_source_record_pages', pages)
        bounded.setattr(database, 'raw_rows', observe)
        actual = pairs._inputs(cid)
    assert exact_value(actual) == exact_value(expected)
    assert len(actual[0]) == 12
    assert sum(map(len, actual[1].values())) == (13 if leading == 'support-prefix' else 12)
    assert actual[2] == ({(source_id, 'table.csv')} if leading == 'support-prefix' else set())
    selected = [item for item in payloads if item['params'][1] == source_id]
    assert [item['params'][7] for item in selected] == [first, tail]
    assert [item['count'] for item in selected] == [2 if leading == 'support-prefix' else 0, 4]
    assert [params[2:] for params in keys if params[:2] == [cid, source_id]] == [
        [None, None], [first[-1], first[-1]], [tail[-1], tail[-1]]]
    assert sum(item['count'] for item in payloads) == (14 if leading == 'support-prefix' else 12)
    assert len(links) == 12
    for item in payloads:
        query, params = item['query'], item['params']
        assert item['exhausted'] and item['count'] <= 4096
        assert 0 < len(params[7]) <= 4096 and params[5:7] == [params[7][0], params[7][-1]]
        assert params[:2] == params[3:5] == params[8:10]
        assert params[5:8] == params[10:13]
        assert params[0] == cid
        assert 'r.record_no in (select unnest(?::bigint[]))' in query
        assert 'r.record_no between ?::bigint and ?::bigint' in query
        assert 'f.source_row in (select unnest(?::bigint[]))' in query
        assert 'f.source_row between ?::bigint and ?::bigint' in query
        assert '_pair_links as materialized' in query
    assert not state['active']
    assert database.q_raw('select * from import_records where case_id=? order by source_id,record_no', [cid]) == stored
    assert database.q_raw('select * from flow_sources where case_id=? order by source_id,source_row', [cid]) == links


@pytest.mark.parametrize('damage', ['outer-json', 'ledger-contract', 'discarded-field', 'removed'])
def test_input_pages_validate_complete_selected_tail_and_close_on_failure(client, database, make_case, damage):
    cid, source_id, _, tail = input_page_case(client, database, make_case)
    record = database.q_raw('select details_json from import_records where case_id=? and source_id=? and record_no=?',
                            [cid, source_id, tail[-1]])[0]
    details = json.loads(record['details_json'])
    if damage == 'ledger-contract':
        details[source_ledger.FIELD] = '{}'
    elif damage == 'discarded-field':
        evidence = json.loads(details[source_ledger.FIELD])
        evidence['row']['summary'] = None
        details[source_ledger.FIELD] = json.dumps(evidence)
    value = '{SYN malformed JSON' if damage in {'outer-json', 'removed'} else json.dumps(details)
    database.x('update import_records set details_json=?,disposition=? where case_id=? and source_id=? and record_no=?',
               [value, 'removed' if damage == 'removed' else 'imported', cid, source_id, tail[-1]])
    if damage == 'removed':
        assert exact_value(pairs._inputs(cid)) == exact_value(before_input_pages_inputs(cid))
    else:
        with pytest.raises(Exception) as expected:
            before_input_pages_inputs(cid)
        with pytest.raises(type(expected.value)):
            pairs._inputs(cid)
    # A failed tail validation must release the producer's borrowed result/lock.
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(database.q_raw, 'select 1 n').result(timeout=3) == [{'n': 1}]
    assert database.q_raw('select details_json from import_records where case_id=? and source_id=? and record_no=?',
                          [cid, source_id, tail[-1]]) == [{'details_json': value}]


@pytest.mark.parametrize('private', [False, True], ids=['shared-lock', 'private-snapshot'])
def test_input_pages_keep_one_view_across_concurrent_writer(client, database, make_case, monkeypatch, private):
    from contextlib import nullcontext
    cid, source_id, _, tail = input_page_case(client, database, make_case)
    expected = before_input_pages_inputs(cid)
    record = database.q_raw('select details_json from import_records where case_id=? and source_id=? and record_no=?',
                            [cid, source_id, tail[-1]])[0]
    details = json.loads(record['details_json'])
    contract = json.loads(details[pairs.FIELD])
    contract['source_counterparty_bank'] = 'SYN-LATE-BANK'
    details[pairs.FIELD] = json.dumps(contract)
    changed = json.dumps(details)
    started, finished = threading.Event(), threading.Event()
    original, payloads, futures = database.raw_rows, [], []
    snapshot_active, dispatched = False, False

    def write():
        started.set()
        with database.transaction():
            database.x('update import_records set details_json=? where case_id=? and source_id=? and record_no=?',
                       [changed, cid, source_id, tail[-1]])
        finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        @contextmanager
        def observe(query, params=None):
            nonlocal dispatched
            payloads.append(params)
            with original(query, params) as rows:
                yield rows
            if params[1] == source_id and not dispatched:
                dispatched = True
                futures.append(pool.submit(write))
                assert started.wait(3)
                if snapshot_active:
                    assert finished.wait(3)
                else:
                    assert not finished.wait(0.05)

        with monkeypatch.context() as concurrent:
            concurrent.setattr(database, 'raw_rows', observe)
            context = database.read_snapshot() if private else nullcontext(False)
            with context as snapshot_active:
                actual = pairs._inputs(cid)
        for future in futures:
            future.result(timeout=3)
    assert dispatched and len([params for params in payloads if params[1] == source_id]) == 2
    assert exact_value(actual) == exact_value(expected)
    assert database.q_raw('select details_json from import_records where case_id=? and source_id=? and record_no=?',
                          [cid, source_id, tail[-1]]) == [{'details_json': changed}]
    assert exact_value(pairs._inputs(cid)) != exact_value(expected)


@pytest.mark.parametrize('source_roles', [False, True], ids=['events', 'source-roles'])
@pytest.mark.parametrize('linked', [False, True], ids=['missing-links', 'linked'])
def test_input_pages_keep_complete_Z_projection(client, database, make_case, monkeypatch, source_roles, linked):
    # One complete repeated member supports the same four posting positions.
    cid, _, _, _ = input_page_case(client, database, make_case, copy_paths=('中.csv',))
    if not linked:
        database.x('delete from flow_sources where case_id=?', [cid])
    with monkeypatch.context() as frozen:
        frozen.setattr(pairs, '_inputs', before_input_pages_inputs)
        expected = pairs.project(cid, source_roles=source_roles)
    actual = pairs.project(cid, source_roles=source_roles)
    assert exact_value(actual) == exact_value(expected)
    assert len(actual[0]) == (1 if linked else 0)


def before_label_pool_inputs(case_id):
    from app.table_cancellation_links import ordinary_csv_member
    _input_rows, validate = pairs._input_rows, pairs.validate
    POLICY, PROFILES = pairs.POLICY, pairs.PROFILES
    _identifier, _facts = pairs._identifier, pairs._facts
    # Frozen complete R5 input assembly, before bounded label sharing.
    # Do not carry the full source-details payload through link joins/sorting.
    # Select the original source/member scope before extracting its contracts.
    rows = []
    member_metadata = {}
    contract_keys = {key: key for key in ("policy", "source_primary_reference", "source_counterparty_bank", "independent_references")}
    kinds = {value: value for value in ("financial", "context", "ledger")}
    directions = {value: value for value in ("in", "out", "unknown")}
    currencies = {value: value for value in ("CNY", "USD", "EUR")}
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
                contract = {contract_keys[key]: value for key, value in contract.items()}
                contract["policy"] = POLICY
            authority = contract is not None and fetched["parser_version"] in PROFILES
            reference = (contract["source_primary_reference"] if authority else
                         _identifier(evidence["row"]["trade_no"]) if evidence and
                         fetched["trade_no_basis"] == "source_transaction_id" else "")
            bank = (contract["source_counterparty_bank"] if authority else _identifier(
                evidence["row"]["payee_bank" if evidence["direction"] == "out" else "payer_bank"]) if evidence else "")
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
                evidence = {key: evidence[key] for key in ("kind", "header_scope", "source_currency", "direction",
                    "own_card", "signed_amount", "source_direction", "balance_before")}
                evidence["row"] = {key: source[key] for key in ("trade_no", "payer_account", "payee_account",
                    "self_account", "amount", "balance", "currency", "trade_time", "trade_time_utc")}
                for key, literals in (("kind", kinds), ("direction", directions), ("source_currency", currencies)):
                    value = evidence[key]
                    if type(value) is str:
                        evidence[key] = literals.get(value, value)
                value = evidence["row"]["currency"]
                if type(value) is str:
                    evidence["row"]["currency"] = currencies.get(value, value)
                del source
            row = {key: fetched[key] for key in ("source_id", "record_no", "member_path", "source_row", "disposition",
                "parser_version", "member_sha256", "flow_id", "trade_no_basis")}
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
    del member_metadata
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

def label_rows(count, *, bases=None, dispositions=None, directions=None, banks=None, authority=None):
    def chosen(value, index, default):
        return value(index) if value is not None else default

    for index in range(count):
        bank = chosen(banks, index, 'SYN-BANK-LABEL')
        source = dict.fromkeys(source_ledger.FLOW_FIELDS, '')
        source.update(trade_no='SYN-LABEL-REF', payer_account='SYN-A', payee_account='SYN-B',
                      payee_bank=bank, self_account='SYN-A', amount='1.00', balance='999.00',
                      currency='CNY', trade_time='2038-02-04T09:00:01',
                      trade_time_utc=None, trade_time_original=None, trade_time_offset=None)
        evidence = {'policy': source_ledger.POLICY, 'kind': 'financial', 'row': source,
                    'signed_amount': '1.00', 'source_currency': 'CNY', 'header_scope': 1,
                    'source_direction': chosen(directions, index, '出'), 'direction': 'out',
                    'own_card': 'SYN-A', 'reported_status': '01', 'issues': [], 'balance_before': None}
        authorized = authority(index) if authority is not None else True
        contract = {'policy': pairs.POLICY, 'source_primary_reference': 'SYN-LABEL-REF',
                    'source_counterparty_bank': bank, 'independent_references': False}
        # Simulate independently decoded storage strings, without changing values.
        disposition = chosen(dispositions, index, 'duplicate')
        basis = chosen(bases, index, 'source_transaction_id')
        yield {'source_id': 'SYN-LABEL-SOURCE', 'record_no': index + 1, 'member_path': 'table.csv',
               'source_row': index + 2, 'disposition': disposition.encode().decode() if type(disposition) is str else disposition,
               'parser_version': table_import.PROFILE if authorized else table_import.SOURCE_LEDGER_PROFILE,
               'filename': 'SYN.csv', 'sheet': '', 'row_kind': 'table_record',
               'source_sha256': 'a' * 64, 'member_sha256': 'a' * 64, 'role': 'table',
               'encoding': 'utf-8-sig', 'flow_id': index + 1000,
               'trade_no_basis': basis.encode().decode() if type(basis) is str else basis,
               'ledger_evidence': json.dumps(evidence), 'pair_evidence': json.dumps(contract) if authorized else None}


def label_reader(count, **kwargs):
    @contextmanager
    def read(_):
        yield label_rows(count, **kwargs)
    return read


def retained_label_stats(inputs):
    rows = {id(row): row for row in inputs[0]}
    for book in inputs[1].values():
        rows.update((id(row), row) for row in book)
    objects, fields = {}, defaultdict(set)
    values = defaultdict(set)
    occurrences = defaultdict(int)
    for row in rows.values():
        labels = {'disposition': row['disposition'], 'trade_no_basis': row['trade_no_basis']}
        if row['evidence'] is not None:
            labels['source_direction'] = row['evidence']['source_direction']
        if row['contract'] is not None:
            labels['contract_bank'] = row['contract']['source_counterparty_bank']
        if 'bank' in row:
            labels['bank'] = row['bank']
        for field, value in labels.items():
            if type(value) is str:
                objects[id(value)] = value
                fields[field].add(id(value))
                values[value].add(id(value))
                occurrences[value] += 1
    return {'rows': len(rows), 'unique_objects': len(objects),
            'retained_bytes': sum(sys.getsizeof(value) for value in objects.values()),
            'field_objects': {key: len(value) for key, value in fields.items()},
            'shared_values': sum(occurrences[value] > 1 and len(ids) == 1 for value, ids in values.items())}


def test_label_pool_reduces_independent_retained_objects_without_changing_inputs(monkeypatch, record_property):
    count = 4096
    monkeypatch.setattr(pairs, '_input_rows', label_reader(count))
    expected = before_label_pool_inputs(1)
    actual = pairs._inputs(1)
    assert exact_value(actual) == exact_value(expected)
    before, after = retained_label_stats(expected), retained_label_stats(actual)
    record_property('label_retention', json.dumps({'before': before, 'after': after}, sort_keys=True))
    assert before['rows'] == after['rows'] == count
    assert before['unique_objects'] >= 3 * count
    assert after['unique_objects'] == 4
    assert after['retained_bytes'] < before['retained_bytes']


def test_label_pool_retention_on_synthetic_native_rows(client, database, make_case, record_property):
    cid = make_case('SYN native label residency')
    add_source(client, cid, encoded(native_entries() * 256))
    expected, actual = before_label_pool_inputs(cid), pairs._inputs(cid)
    assert exact_value(actual) == exact_value(expected)
    before, after = retained_label_stats(expected), retained_label_stats(actual)
    record_property('native_label_retention', json.dumps({'before': before, 'after': after}, sort_keys=True))
    assert before['rows'] == after['rows'] == 1024
    assert after['retained_bytes'] < before['retained_bytes']


def test_label_pool_has_a_strict_budget_and_preserves_long_unknown_labels(monkeypatch):
    monkeypatch.setattr(pairs, '_input_rows', label_reader(160, bases=lambda i: f'SYN-unique-{i % 80:03d}'))
    expected, actual = before_label_pool_inputs(1), pairs._inputs(1)
    assert exact_value(actual) == exact_value(expected)
    stats = retained_label_stats(actual)
    assert 3 < stats['shared_values'] <= 64
    assert stats['unique_objects'] > 64  # Overflow values are retained, not lost or silently folded.
    for length in (128, 129):
        value = 'SYN-' + 'x' * (length - 4)
        monkeypatch.setattr(pairs, '_input_rows', label_reader(8, bases=lambda i: value))
        expected, actual = before_label_pool_inputs(1), pairs._inputs(1)
        assert exact_value(actual) == exact_value(expected)
        assert len({id(row['trade_no_basis']) for row in actual[0]}) == (1 if length == 128 else 8)


def test_label_pool_is_local_to_each_complete_input_read(monkeypatch):
    monkeypatch.setattr(pairs, '_input_rows', label_reader(8))
    first, second = pairs._inputs(1), pairs._inputs(1)
    assert exact_value(first) == exact_value(second)
    assert first[0][0]['disposition'] is not second[0][0]['disposition']
    assert first[0][0]['contract']['source_counterparty_bank'] is not second[0][0]['contract']['source_counterparty_bank']


@pytest.mark.parametrize('field', ['disposition', 'trade_no_basis', 'source_direction', 'bank'])
def test_label_pool_never_replaces_nonbuiltin_string_objects(monkeypatch, field):
    class Label(str):
        pass

    original_read = label_reader(8)
    @contextmanager
    def read(case_id):
        with original_read(case_id) as rows:
            def changed():
                for row in rows:
                    if field in {'disposition', 'trade_no_basis'}:
                        row[field] = Label(row[field])
                    yield row
            yield changed()
    monkeypatch.setattr(pairs, '_input_rows', read)
    original_pair, original_ledger = pairs.validate, source_ledger.validate
    if field == 'bank':
        def pair(value):
            contract = original_pair(value)
            contract['source_counterparty_bank'] = Label(contract['source_counterparty_bank'])
            return contract
        monkeypatch.setattr(pairs, 'validate', pair)
    elif field == 'source_direction':
        def ledger(value):
            evidence = original_ledger(value)
            evidence['source_direction'] = Label(evidence['source_direction'])
            return evidence
        monkeypatch.setattr(source_ledger, 'validate', ledger)
    expected, actual = before_label_pool_inputs(1), pairs._inputs(1)
    assert exact_value(actual) == exact_value(expected)
    def value(row):
        return row['bank'] if field == 'bank' else row['evidence']['source_direction'] if field == 'source_direction' else row[field]
    assert all(type(value(row)) is Label for row in actual[0])
    assert len({id(value(row)) for row in actual[0]}) == 8


def test_label_pool_preserves_raw_labels_missing_values_and_nonauthority_banks(monkeypatch):
    banks = ['123', '１２３', 'SYN Bank', 'syn bank']
    directions = ['出', ' 出 ']
    bases = ['source_transaction_id', 'SOURCE_TRANSACTION_ID', ' source_transaction_id ', None]
    dispositions = ['duplicate', 'DUPLICATE', ' duplicate ', 'SYN-unknown']
    monkeypatch.setattr(pairs, '_input_rows', label_reader(16, banks=lambda i: banks[i % 4],
        directions=lambda i: directions[i % 2], bases=lambda i: bases[i % 4],
        dispositions=lambda i: dispositions[i % 4]))
    expected, actual = before_label_pool_inputs(1), pairs._inputs(1)
    assert exact_value(actual) == exact_value(expected)
    assert {row['bank'] for row in actual[0]} == set(banks)
    assert {row['evidence']['source_direction'] for row in actual[0]} == set(directions)
    monkeypatch.setattr(pairs, '_input_rows', label_reader(16, authority=lambda i: i == 0,
        banks=lambda i: 'SYN-BANK-LABEL' if i == 0 else '  SYN RAW BANK  '))
    expected, actual = before_label_pool_inputs(1), pairs._inputs(1)
    assert exact_value(actual) == exact_value(expected)
    fallback = [row for row in actual[0] if not row['authority']]
    assert len(fallback) == 15 and all(row['contract'] is None and row['bank'] == 'SYN RAW BANK' for row in fallback)
    assert len({id(row['bank']) for row in fallback}) == 1


@pytest.mark.parametrize('source_roles', [False, True], ids=['events', 'source-roles'])
@pytest.mark.parametrize('linked', [False, True], ids=['missing-links', 'linked'])
def test_label_pool_keeps_full_R5_input_and_project_dtos(client, database, make_case, monkeypatch, source_roles, linked):
    cid, _, _, _ = input_page_case(client, database, make_case, copy_paths=('中.csv',))
    if not linked:
        database.x('delete from flow_sources where case_id=?', [cid])
    expected_inputs = before_label_pool_inputs(cid)
    assert exact_value(pairs._inputs(cid)) == exact_value(expected_inputs)
    with monkeypatch.context() as frozen:
        frozen.setattr(pairs, '_inputs', before_label_pool_inputs)
        expected = pairs.project(cid, source_roles=source_roles)
    actual = pairs.project(cid, source_roles=source_roles)
    assert exact_value(actual) == exact_value(expected)
    assert len(actual[0]) == (1 if linked else 0)


class BeforeBalanceCurrentFacts(Mapping):
    """Read-only raw flow facts; keep values without a per-row hash table."""
    __slots__ = ("id", "trade_no", "payer_account", "payee_account", "self_account",
                 "currency", "amount", "balance", "trade_time", "trade_time_utc")

    def __init__(self, row):
        for field in self.__slots__:
            object.__setattr__(self, field, row[field])

    def __getitem__(self, key):
        if key not in self.__slots__:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self):
        return iter(self.__slots__)

    def __len__(self):
        return len(self.__slots__)

    def __setattr__(self, key, value):
        raise AttributeError("Current facts are read-only")

    def __delattr__(self, key):
        raise AttributeError("Current facts are read-only")


def before_balance_inputs(case_id):
    from app.table_cancellation_links import ordinary_csv_member
    _input_rows, validate = pairs._input_rows, pairs.validate
    POLICY, PROFILES = pairs.POLICY, pairs.PROFILES
    _identifier, _facts = pairs._identifier, pairs._facts
    # Do not carry the full source-details payload through link joins/sorting.
    # Select the original source/member scope before extracting its contracts.
    rows = []
    member_metadata = {}
    contract_keys = {key: key for key in ("policy", "source_primary_reference", "source_counterparty_bank", "independent_references")}
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
                contract = {contract_keys[key]: value for key, value in contract.items()}
                contract["policy"] = POLICY
                contract["source_counterparty_bank"] = shared_label(contract["source_counterparty_bank"])
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
                evidence = {key: evidence[key] for key in ("kind", "header_scope", "source_currency", "direction",
                    "own_card", "signed_amount", "source_direction", "balance_before")}
                evidence["row"] = {key: source[key] for key in ("trade_no", "payer_account", "payee_account",
                    "self_account", "amount", "balance", "currency", "trade_time", "trade_time_utc")}
                for key, literals in (("kind", kinds), ("direction", directions), ("source_currency", currencies)):
                    value = evidence[key]
                    if type(value) is str:
                        evidence[key] = literals.get(value, value)
                value = evidence["row"]["currency"]
                if type(value) is str:
                    evidence["row"]["currency"] = currencies.get(value, value)
                evidence["source_direction"] = shared_label(evidence["source_direction"])
                del source
            row = {key: fetched[key] for key in ("source_id", "record_no", "member_path", "source_row", "disposition",
                "parser_version", "member_sha256", "flow_id", "trade_no_basis")}
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


def before_balance_project(case_id, *, excluded_ids=(), source_roles=False):
    """Frozen complete R6 projection, including its original fact container."""
    from app import db
    _inputs, _CurrentFacts = before_balance_inputs, BeforeBalanceCurrentFacts
    _pair_projection = pairs._pair_projection
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
                    current_rows[str(row["id"])] = _CurrentFacts(row)
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
        from app import bank_single_postings
        events.extend(bank_single_postings.project(case_id, candidates, books, unlocated,
            effective, current_rows, memberships, excluded_ids))
    del books, unlocated, effective, current_rows, excluded_ids
    return events, [memberships[key] for key in sorted(memberships)], candidates

def balance_facts_row(balance, number=1):
    stamp = datetime(2040, 2, 3, 4, 5, 6, 123456, tzinfo=timezone(timedelta(hours=8), 'SYN zone'), fold=1)
    return {'id': number, 'trade_no': 'SYN-balance', 'payer_account': 'SYN-A', 'payee_account': 'SYN-B',
            'self_account': 'SYN-A', 'currency': 'CNY', 'amount': Decimal('37.50'), 'balance': balance,
            'trade_time': stamp, 'trade_time_utc': None}


@pytest.mark.parametrize('precision', [3, 28, 50])
@pytest.mark.parametrize('value,text,encoded', [
    ('1.00', '1.00', True), ('-0.00', '-0.00', True),
    ('9999999999999999.99', '9999999999999999.99', True),
    ('1.00', '1.0', False), ('1.00', '1', False),
    ('0.00', '-0.00', False), ('-0.00', '0.00', False),
    ('100.00', '1E+2', False), ('1.00', '1.01', False), ('1.00', None, False),
])
def test_balance_storage_preserves_exact_representation_and_precision(precision, value, text, encoded):
    original = balance_facts_row(Decimal(value))
    expected = BeforeBalanceCurrentFacts(original)
    with localcontext() as context:
        context.prec = precision
        actual = pairs._CurrentFacts(original, text)
        assert exact_value(dict(actual)) == exact_value(dict(expected))
        assert len(actual) == 10 and list(actual) == list(expected)
        assert list(actual.keys()) == list(expected.keys())
        assert [key for key, _ in actual.items()] == list(expected)
        assert type(actual.balance) is Decimal and actual.balance.as_tuple() == original['balance'].as_tuple()
        assert actual['balance'].as_tuple() == actual.get('balance').as_tuple() == original['balance'].as_tuple()
        for field in original:
            if field != 'balance':
                assert actual[field] is getattr(actual, field) is original[field]
        assert db_clean_mapping(actual) == db_clean_mapping(expected)
        if encoded:
            assert object.__getattribute__(actual, '_balance_text') is text
            assert object.__getattribute__(actual, '_balance') is None
            assert actual.balance is not actual.balance  # No permanent decoded cache.
        else:
            assert object.__getattribute__(actual, '_balance_text') is None
            assert actual.balance is original['balance']
        for private in ('_balance', '_balance_text', '_FIELDS'):
            with pytest.raises(KeyError):
                actual[private]
            assert private not in dict(actual)
        with pytest.raises(AttributeError):
            actual.balance = Decimal('9.00')
        with pytest.raises(AttributeError):
            del actual.balance
        assert not hasattr(actual, '__dict__')


def db_clean_mapping(value):
    from app import db
    return db.clean(dict(value))


def test_balance_storage_none_nonbuiltin_and_nonfinite_values_keep_original_objects():
    class Balance(Decimal):
        pass
    class Text(str):
        pass
    marker = object()
    cases = [(None, None), (None, '0.00'), (1.0, '1.00'), (1, '1.00'), ('1.00', '1.00'),
             (Balance('1.00'), '1.00'), (Decimal('1.00'), Text('1.00')),
             (Decimal('Infinity'), '1.00'), (Decimal('NaN'), '1.00'), (marker, '1.00')]
    for balance, text in cases:
        original = balance_facts_row(balance)
        actual = pairs._CurrentFacts(original, text)
        assert object.__getattribute__(actual, '_balance_text') is None
        assert actual.balance is actual['balance'] is balance
        assert exact_value(dict(actual)) == exact_value(dict(BeforeBalanceCurrentFacts(original)))


def test_balance_storage_snapshots_text_without_holding_or_following_source_dictionary():
    class Source(dict):
        pass
    text = '9876543210123456.78'.encode().decode()
    source = Source(balance=text)
    original = balance_facts_row(Decimal(text))
    actual = pairs._CurrentFacts(original, source['balance'])
    source['balance'] = '0.00'
    original['balance'] = Decimal('2.00')
    reference = weakref.ref(source)
    del source
    assert reference() is None
    assert object.__getattribute__(actual, '_balance_text') is text
    assert actual.balance.as_tuple() == Decimal(text).as_tuple()
    assert actual.balance is not actual.balance


def balance_storage_stats(rows, books):
    # Count the changed storage slice, with existing book text counted in both
    # graphs. Getter temporaries and unrelated unchanged fields are excluded.
    objects = {id(books): books}
    for text in books:
        if text is not None:
            objects[id(text)] = text
    instances = encoded = decimals = 0
    for row in rows:
        objects[id(row)] = row
        instances += sys.getsizeof(row)
        if hasattr(row, '_balance_text'):
            text = object.__getattribute__(row, '_balance_text')
            stored = object.__getattribute__(row, '_balance')
            encoded += text is not None
            if text is not None:
                objects[id(text)] = text
        else:
            stored = row.balance
        if stored is not None:
            objects[id(stored)] = stored
            decimals += type(stored) is Decimal
    return {'rows': len(rows), 'instance_bytes': instances, 'encoded': encoded,
            'stored_decimal_objects': decimals, 'retained_bytes': sum(sys.getsizeof(value) for value in objects.values())}


@pytest.mark.parametrize('scenario', ['all-exact', 'half-none', 'quarter-mismatch', 'all-none', 'all-mismatch'])
def test_balance_storage_counts_marker_cost_and_ineligible_rows(record_property, scenario):
    count = 512
    before, after, books = [], [], []
    eligible = none = mismatched = 0
    for number in range(count):
        text = f'{number}.00'
        if scenario == 'all-none' or scenario == 'half-none' and number < count // 2:
            value, text = None, None
            none += 1
        elif scenario == 'all-mismatch' or scenario == 'quarter-mismatch' and number < count // 4:
            value = Decimal(f'{number}.01')
            mismatched += 1
        else:
            value = Decimal(text)
            eligible += 1
        books.append(text)  # Existing validated-source ownership precedes construction.
        row = balance_facts_row(value, number)
        before.append(BeforeBalanceCurrentFacts(row))
        after.append(pairs._CurrentFacts(row, text))
        assert exact_value(dict(after[-1])) == exact_value(dict(before[-1]))
    old, new = balance_storage_stats(before, books), balance_storage_stats(after, books)
    record_property('balance_storage', json.dumps({'scenario': scenario, 'none': none, 'mismatch': mismatched,
        'eligible': eligible, 'before': old, 'after': new,
        'net_saved_retained_bytes': old['retained_bytes'] - new['retained_bytes']}, sort_keys=True))
    if eligible:
        assert new['retained_bytes'] < old['retained_bytes']
    else:
        assert new['retained_bytes'] > old['retained_bytes']  # New slot is a real cost on ineligible rows.
    assert new['encoded'] == eligible
    assert new['instance_bytes'] > old['instance_bytes']
    assert new['stored_decimal_objects'] == mismatched


@pytest.mark.parametrize('source_roles', [False, True], ids=['events', 'source-roles'])
@pytest.mark.parametrize('precision', [3, 28, 50])
@pytest.mark.parametrize('balance_mode', ['huge', 'negative-zero', 'different-scale'])
def test_balance_storage_keeps_complete_R6_input_and_project_dtos(client, database, make_case, monkeypatch, source_roles, precision, balance_mode):
    cid = make_case('SYN precise balance DTO')
    rows = native_entries()
    # The old source validator applies abs(signed) under the ambient context;
    # 37.50 remains numerically exact there at precision 3. Balances stay large.
    rows[1].update({'交易金额': '37.50', '交易余额': '962.50'})
    rows[3].update({'交易金额': '37.50', '交易余额': '2037.50'})
    if balance_mode == 'huge':
        for row, balance in zip(rows, ['9999999999999999.99', '9999999999999962.49',
                                      '-9999999999999999.99', '-9999999999999962.49']):
            row['交易余额'] = balance
    elif balance_mode == 'negative-zero':
        for row in rows:
            row['交易余额'] = '0.00'
    add_source(client, cid, encoded(rows))
    assert exact_value(pairs._inputs(cid)) == exact_value(before_balance_inputs(cid))
    stored = database.q_raw('select * from flows where case_id=? order by id', [cid])
    captured = {}
    original_pair = pairs._pair_projection
    def capture(candidates, books, unlocated, effective, current, excluded, **kwargs):
        captured['current'] = current
        return original_pair(candidates, books, unlocated, effective, current, excluded, **kwargs)
    monkeypatch.setattr(pairs, '_pair_projection', capture)
    def raw_balance(row):
        row = dict(row)
        if balance_mode == 'negative-zero':
            row['balance'] = Decimal('-0.00')
        elif balance_mode == 'different-scale':
            row['balance'] = Decimal(str(row['balance']) + '0')
        return row
    with project_map_reads(database, monkeypatch, raw_transform=raw_balance):
        with localcontext() as context:
            context.prec = precision
            expected = before_balance_project(cid, source_roles=source_roles)
            expected_current = {key: dict(value) for key, value in captured['current'].items()}
            actual = pairs.project(cid, source_roles=source_roles)
    assert exact_value(actual) == exact_value(expected)
    assert exact_value({key: dict(value) for key, value in captured['current'].items()}) == exact_value(expected_current)
    assert exact_value(database.q_raw('select * from flows where case_id=? order by id', [cid])) == exact_value(stored)


@pytest.mark.parametrize('state', ['exact', 'changed', 'none'])
def test_balance_storage_sql_handoff_and_raw_mismatch_keep_all_compatible_identity(client, database, make_case, monkeypatch, state):
    from app import bank_single_postings
    cid = make_case('SYN exact source balance handoff')
    add_source(client, cid, encoded(native_entries()))
    if state == 'changed':
        database.x('update flows set balance=balance+0.01 where case_id=?', [cid])
    elif state == 'none':
        database.x('update flows set balance=null where case_id=?', [cid])
    captured = {}
    original_pair = pairs._pair_projection
    def capture(candidates, books, unlocated, effective, current, excluded, **kwargs):
        captured.update(effective=effective, current=current)
        return original_pair(candidates, books, unlocated, effective, current, excluded, **kwargs)
    monkeypatch.setattr(pairs, '_pair_projection', capture)
    expected = before_balance_project(cid)
    old = {key: dict(value) for key, value in captured['current'].items()}
    actual = pairs.project(cid)
    assert exact_value(actual) == exact_value(expected)
    assert exact_value({key: dict(value) for key, value in captured['current'].items()}) == exact_value(old)
    for key, current in captured['current'].items():
        stored_as_text = object.__getattribute__(current, '_balance_text') is not None
        assert stored_as_text is (state == 'exact')
        if captured['effective'].get(key) is current:
            for field in ('id', 'payer_account', 'payee_account', 'self_account', 'currency', 'amount', 'trade_time', 'trade_time_utc'):
                assert captured['effective'][key][field] is current[field]


@pytest.mark.parametrize('kind', ['financial', 'context', 'ledger'])
def test_proof_evidence_shell_reduces_retained_bytes_with_complete_values(monkeypatch, record_property, kind):
    fetched = list(label_rows(64))
    for item in fetched:
        evidence = json.loads(item['ledger_evidence'])
        evidence['kind'] = kind
        if kind == 'ledger':
            evidence['signed_amount'] = evidence['row']['amount'] = '-1.00'
            evidence['issues'] = ['negative_amount']
        elif kind == 'context':
            evidence['issues'] = ['unknown_status']
        item['ledger_evidence'] = json.dumps(evidence)

    @contextmanager
    def read(_):
        yield iter(fetched)

    monkeypatch.setattr(pairs, '_input_rows', read)
    expected, actual = before_balance_inputs(17), pairs._inputs(17)
    assert exact_value(actual) == exact_value(expected)
    old = {id(row['evidence']): row['evidence'] for row in expected[0]}
    new = {id(row['evidence']): row['evidence'] for row in actual[0]}
    assert len(old) == len(new) == 64
    old_candidates = {id(row): row for row in expected[0]}
    new_candidates = {id(row): row for row in actual[0]}
    assert len(old_candidates) == len(new_candidates) == 64
    before = sum(sys.getsizeof(value) for value in old.values())
    after = sum(sys.getsizeof(value) for value in new.values())
    before_rows = sum(sys.getsizeof(value['row']) for value in old.values())
    after_rows = sum(sys.getsizeof(value['row']) for value in new.values())
    before_candidates = sum(sys.getsizeof(row) for row in old_candidates.values())
    after_candidates = sum(sys.getsizeof(row) for row in new_candidates.values())
    stats = {'kind': kind, 'rows': len(new), 'before_bytes': before, 'after_bytes': after,
             'before_row_bytes': before_rows, 'after_row_bytes': after_rows,
             'before_candidate_bytes': before_candidates, 'after_candidate_bytes': after_candidates}
    record_property('proof_evidence_shell', json.dumps(stats, sort_keys=True))
    print(json.dumps(stats, sort_keys=True))
    assert after < before
    assert after_rows < before_rows
    assert after_candidates * 2 <= before_candidates


def test_proof_evidence_mapping_keeps_keys_scalar_objects_and_mutable_row_alias():
    class Text(str):
        pass
    class Original(dict):
        pass

    source = {'trade_no': Text('SYN-TYPED-REFERENCE'), 'payer_account': 'SYN-A', 'payee_account': 'SYN-B',
              'self_account': 'SYN-A', 'amount': Text('1.00'), 'balance': None, 'currency': '',
              'trade_time': '2040-02-03T04:05:06.123456+08:00', 'trade_time_utc': None}
    original = Original(kind=Text('financial'), header_scope=1, source_currency=Text('CNY'),
                        direction=Text('out'), own_card=Text('SYN-CARD'), signed_amount=source['amount'],
                        source_direction=Text('出'), balance_before=None, row=source)
    before = dict(original)
    evidence = pairs._ProofEvidence(original, source)
    assert isinstance(evidence, Mapping) and not isinstance(evidence, dict)
    assert len(evidence) == 9 and list(evidence) == list(before)
    assert list(evidence.keys()) == list(before) and list(evidence.items()) == list(before.items())
    assert all(evidence[key] is value and evidence.get(key) is value for key, value in before.items())
    assert exact_value(evidence) == exact_value(before)
    assert evidence['row'] is source
    source['trade_no'] = Text('SYN-CHANGED-REFERENCE')
    assert evidence['row']['trade_no'] is source['trade_no']
    replacement = Text('SYN-SHARED-ANCHOR-CARD')
    evidence['own_card'] = replacement
    assert evidence['own_card'] is replacement and original['own_card'] is before['own_card']
    default = object()
    for key in ('missing', 'items', '__slots__', '_FIELDS', '__class__'):
        with pytest.raises(KeyError):
            evidence[key]
        with pytest.raises(KeyError):
            evidence[key] = default
        assert evidence.get(key, default) is default
    with pytest.raises(AttributeError, match='__delitem__'):
        del evidence['row']
    with pytest.raises(AttributeError):
        evidence.missing = default
    assert not hasattr(evidence, '__dict__')
    reference = weakref.ref(original)
    del original
    assert reference() is None


def test_proof_row_mapping_keeps_fields_objects_alias_updates_and_no_source_dictionary():
    class Text(str):
        pass
    class Original(dict):
        pass

    stamp = datetime(2040, 2, 3, 4, 5, 6, 123456, tzinfo=timezone(timedelta(hours=8), 'SYN proof'), fold=1)
    original = Original(trade_no=Text('SYN-TYPED-REFERENCE'), payer_account=Text('SYN-A'),
                        payee_account=Text('SYN-B'), self_account=Text('SYN-A'), amount=Text('1.00'),
                        balance=Decimal('-0.00'), currency=Text('CNY'), trade_time=stamp, trade_time_utc=None)
    before = dict(original)
    row = pairs._ProofRow(original)
    assert isinstance(row, Mapping) and not isinstance(row, dict)
    assert len(row) == 9 and list(row) == list(before)
    assert list(row.keys()) == list(before) and list(row.items()) == list(before.items())
    assert all(row[key] is value and row.get(key) is value for key, value in before.items())
    assert exact_value(row) == exact_value(before)
    owner, reference = Text('SYN-ANCHOR-OWNER'), Text('SYN-ANCHOR-REFERENCE')
    row['payer_account'] = owner
    row.update({'self_account': owner}, trade_no=reference)
    assert row['payer_account'] is row['self_account'] is owner
    assert row['trade_no'] is reference and original['trade_no'] is before['trade_no']
    default = object()
    for key in ('missing', 'items', '__slots__', '_FIELDS', '__class__'):
        with pytest.raises(KeyError):
            row[key]
        with pytest.raises(KeyError):
            row[key] = default
        assert row.get(key, default) is default
    assert not hasattr(row, '__dict__')
    reference = weakref.ref(original)
    del original
    assert reference() is None
