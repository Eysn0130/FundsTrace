"""Proved local native31 booked movements; no external-settlement claim."""
from collections import defaultdict
from datetime import datetime
from itertools import groupby
import hashlib
import json

from . import db, source_ledger
from .money import money_decimal

POLICY = "independent-native31-single-posting-v3"
PROOF_FIELD = "current_bank_single_posting_proof"
PENDING_FIELD = "current_bank_single_posting_pending"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _physical(row):
    return row["position_path"], row["member_sha256"], row["source_row"]


def _scope(row):
    from .bank_posting_pairs import _identifier
    e = row["evidence"]
    return (row["source_id"], row["member_path"], e["header_scope"], e["source_currency"],
            _identifier(e["row"]["self_account"]), _identifier(e["own_card"]))


def _clock(row):
    utc = row.get("trade_time_utc")
    value = utc or row.get("trade_time")
    return bool(utc), datetime.fromisoformat(value) if isinstance(value, str) else value


def _prepare(books, unlocated):
    merged = defaultdict(list)
    for book in books.values():
        # Each source-ledger book already shares the raw scope. Normalize it
        # once while preserving row order when multiple raw scopes merge.
        if book:
            merged[_scope(book[0])].extend(book)
    result = {}
    for scope, book in merged.items():
        times, bases = {}, set()
        reason = "single_posting_book_unlocated" if scope[:2] in unlocated else None
        for row in book:
            e = row["evidence"]
            basis, stamp = _clock(e["row"])
            bases.add(basis)
            if stamp is None:
                reason = "single_posting_book_unlocated"
            if stamp not in times and len(row.get("_physical_rows", {row["source_row"]})) == 1:
                times[stamp] = row
            else:
                times[stamp] = None
        if len(bases) != 1:
            reason = "single_posting_book_time_basis_incomparable"
        ordered = sorted(t for t in times if t is not None)
        previous = {stamp: ordered[index - 1] if index else None for index, stamp in enumerate(ordered)}
        result[scope] = reason, times, previous
    return result


def _reused_primary_references(case_id, candidates):
    """Find candidate primaries across bounded pages of every active source."""
    from .bank_posting_pairs import _identifier
    from .table_cancellation_links import active_support_sql
    with db.read_lock():
        first, reused = {}, set()
        direct = {r["member_sha256"] for r in candidates if r["position_path"] is None}
        for row in candidates:
            reference, physical = row["reference"], _physical(row)
            if reference not in reused and first.setdefault(reference, physical) != physical:
                reused.add(reference)
                del first[reference]
        sources = db.q_raw(f"""select s.source_id from import_sources s
            where s.case_id=? and {active_support_sql('s')}
              and exists(select 1 from import_members m
                  where m.case_id=s.case_id and m.source_id=s.source_id)
            order by s.source_id""", [case_id])
        for source in sources:
            source_id, lower = source["source_id"], None
            while True:
                keys = db.q_raw(f"""select r.record_no from import_records r
                    join import_sources s on s.case_id=r.case_id and s.source_id=r.source_id
                    join import_members m on m.case_id=r.case_id and m.source_id=r.source_id
                        and m.member_path=r.member_path
                    where r.case_id=? and r.source_id=? and r.disposition<>'removed'
                      and {active_support_sql('s')}
                      and (?::bigint is null or r.record_no>?::bigint)
                    order by r.record_no limit 4096""", [case_id, source_id, lower, lower])
                if not keys:
                    break
                record_nos = [row["record_no"] for row in keys]
                lower = record_nos[-1]
                # Limit selected records before evaluating JSON. Raw details
                # here are bounded by the selected PK page, never case-wide.
                with db.raw_rows(f"""with selected_records as materialized (
                    select r.member_path,r.source_row,m.sha256,r.details_json
                    from import_records r join import_sources s
                      on s.case_id=r.case_id and s.source_id=r.source_id
                    join import_members m on m.case_id=r.case_id and m.source_id=r.source_id
                      and m.member_path=r.member_path
                    where r.case_id=? and r.source_id=? and r.disposition<>'removed'
                      and r.record_no between ?::bigint and ?::bigint
                      and r.record_no in (select unnest(?::bigint[]))
                      and {active_support_sql('s')}
                    ), reference_inputs as not materialized (
                    select member_path,source_row,sha256,
                        json_extract_string(details_json,'$.source_field_evidence') field_evidence
                    from selected_records
                    ), reference_matches as materialized (
                    select member_path,source_row,sha256,list_filter(
                        case json_type(field_evidence)
                          when 'ARRAY' then json_extract(field_evidence,'$[*]')
                          when 'OBJECT' then json_extract(field_evidence,'$.*')
                          else []::json[] end,
                        field -> contains(replace(json_extract_string(field,'$.column'),'\\t',''),'交易流水号')) matches
                    from reference_inputs
                    ), reference_fields as (
                    select r.member_path,r.source_row,r.sha256,
                        json_extract_string(u.field,'$.column') source_column,
                        json_extract_string(u.field,'$.raw') reference
                    from reference_matches r,unnest(r.matches) u(field)
                    ) select * from reference_fields
                    where contains(replace(source_column,'\\t',''),'交易流水号')""",
                    [case_id, source_id, record_nos[0], record_nos[-1], record_nos]) as rows:
                    for row in rows:
                        column = row["source_column"]
                        if not isinstance(column, str) or column.replace("\\t", "").strip() != "交易流水号":
                            continue
                        reference = _identifier(row["reference"])
                        if reference in first and first[reference] != (
                                None if row["sha256"] in direct else row["member_path"], row["sha256"], row["source_row"]):
                            reused.add(reference)
                            del first[reference]
        return reused


def _key(row, amount=None):
    from .bank_posting_pairs import _identifier
    amount = money_decimal(row["amount"]) if amount is None else amount
    payer, payee, own = (_identifier(row[key]) for key in ("payer_account", "payee_account", "self_account"))
    if (amount <= 0 or not payer or not payee or payer == payee or not row["currency"]
            or own not in {payer, payee}):
        return None
    return payer, payee, row["currency"], amount


def _eligible_single(row):
    from .bank_posting_pairs import _identifier
    e = row["evidence"]
    return (row["authority"] and e and row["effective"] and e["kind"] == "financial"
            and row["disposition"] in {"imported", "duplicate"} and row["trade_no_basis"] == "source_transaction_id"
            and e["row"]["trade_no"] == row["reference"] and e["source_currency"] in {"CNY", "USD", "EUR"}
            and row["bank"] and _identifier(e["row"]["self_account"]))


def _cross_index(case_id, books, current_rows, wanted):
    """Retain only observed owner bits that can block a wanted opposite owner."""
    cross = {}
    def add(row, amount=None):
        from .bank_posting_pairs import _identifier
        key = _key(row, amount)
        owners = wanted.get(key, 0)
        if not owners:
            return
        owner = 1 if _identifier(row["self_account"]) == key[0] else 2
        if owners & (2 if owner == 1 else 1):
            cross[key] = cross.get(key, 0) | owner
    for book in books.values():
        for record in book:
            e = record["evidence"]
            add(e["row"], money_decimal(e["signed_amount"]))
    # Older registered/legacy observations may predate source-ledger snapshots.
    from .table_cancellation_links import active_support_sql
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
    return cross


def project(case_id, candidates, books, unlocated, effective, current_rows, memberships, excluded_ids, *, proof_bytes=False):
    from .bank_posting_pairs import _facts, _identifier, _matches_source
    reused = _reused_primary_references(case_id, candidates)
    blocked = set()
    for row in candidates:
        fid = str(row["flow_id"])
        membership = memberships.get(fid, {})
        if row["effective"] and (fid in excluded_ids or membership.get("status") == "confirmed"
                                  or membership.get("pending_group")):
            blocked.add(row["reference"])
    wanted = {}
    for row in candidates:
        if row["reference"] in reused or row["reference"] in blocked or not _eligible_single(row):
            continue
        e = row["evidence"]
        key = _key(e["row"], money_decimal(e["signed_amount"]))
        if key is not None:
            owner = 1 if _identifier(e["row"]["self_account"]) == key[0] else 2
            wanted[key] = wanted.get(key, 0) | owner
    cross = _cross_index(case_id, books, current_rows, wanted)
    del wanted, blocked
    prepared = _prepare(books, unlocated)
    conflicts = set()
    with db.raw_rows("""select flow_id from resolved_endpoints
        where case_id=? and (state='conflict' or card_state='conflict')""", [case_id]) as rows:
        for row in rows:
            fid = str(row["flow_id"])
            if fid in effective:
                conflicts.add(fid)
    events = []
    ordered = sorted(candidates, key=lambda row: row["reference"])
    for reference, group in groupby(ordered, key=lambda row: row["reference"]):
        rows = list(group)
        flow_ids = sorted({str(row["flow_id"]) for row in rows if row["effective"]})
        if (not flow_ids or any(fid in excluded_ids or memberships.get(fid, {}).get("status") == "confirmed"
                or memberships.get(fid, {}).get("pending_group") for fid in flow_ids)):
            continue
        reason, proof, chosen = "single_posting_source_incomplete", None, None
        if reference in reused:
            reason = "single_posting_primary_reused"
        elif len(rows) > 1 and len({_facts(row) for row in rows if row["evidence"]}) > 1:
            reason = "single_posting_source_conflict"
        else:
            for row in rows:
                e = row["evidence"]
                if not _eligible_single(row):
                    continue
                source = e["row"]
                key = _key(source, money_decimal(e["signed_amount"]))
                if key is None:
                    continue
                basis, stamp = _clock(source)
                competing = bool(cross.get(key, 0) & (2 if _identifier(source["self_account"]) == key[0] else 1))
                if competing:
                    reason = "single_posting_cross_book_candidate"
                    break
                candidate_id = str(row["flow_id"])
                if candidate_id in conflicts:
                    reason = "single_posting_identity_conflict"
                    break
                if not _matches_source(source, current_rows.get(candidate_id)):
                    reason = "single_posting_current_facts_changed"
                    break
                book_reason, times, previous = prepared[_scope(row)]
                if book_reason:
                    reason = book_reason
                    continue
                if times[stamp] is None:
                    reason = "single_posting_time_not_unique"
                    continue
                before_stamp = previous.get(stamp)
                if before_stamp is None:
                    reason = "single_posting_previous_missing"
                    continue
                prior = times[before_stamp]
                if prior is None:
                    reason = "single_posting_previous_time_not_unique"
                    continue
                before = prior["evidence"]["row"]["balance"]
                if source["balance"] is None or before is None:
                    reason = "single_posting_balance_missing"
                    continue
                expected = key[3]
                if e["direction"] == "out": expected = -expected
                delta = money_decimal(source["balance"]) - money_decimal(before)
                if delta != expected:
                    reason = "single_posting_balance_mismatch"
                    continue
                if not _matches_source(prior["evidence"]["row"], current_rows.get(str(prior["flow_id"]))):
                    reason = "single_posting_current_witness_changed"
                    continue
                fa = effective[candidate_id]
                if (fa["self_account"] != fa["payer_account" if e["direction"] == "out" else "payee_account"]
                        or not fa["payer_account"] or not fa["payee_account"] or fa["payer_account"] == fa["payee_account"]):
                    reason = "single_posting_identity_conflict"
                    continue
                if any(not _matches_source(source, current_rows.get(fid)) or any(
                        effective[fid][field] != fa[field] for field in
                        ("payer_account", "payee_account", "self_account", "currency", "amount", "trade_time", "trade_time_utc"))
                        for fid in flow_ids if fid != candidate_id):
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
            evidence_json = _json(proof)
            if proof_bytes:
                evidence_json = evidence_json.encode("utf-8")
            events.append({"event_id": event_id, **{key: first[key] for key in
                ("payer_account", "payee_account", "currency", "amount")},
                "trade_time": first["trade_time_utc"] or first["trade_time"], "evidence_json": evidence_json, "flow_ids": flow_ids})
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
