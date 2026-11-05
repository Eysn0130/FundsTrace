"""Proved local native31 booked movements; no external-settlement claim."""
from collections import defaultdict
from datetime import datetime
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


def _known_references(case_id, candidates):
    """Count physical named-primary observations across every active source."""
    from .bank_posting_pairs import _identifier
    from .table_cancellation_links import active_support_sql
    references, direct = defaultdict(set), {r["member_sha256"] for r in candidates if r["position_path"] is None}
    for row in candidates:
        references[row["reference"]].add(_physical(row))
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


def _key(row, amount=None):
    from .bank_posting_pairs import _identifier
    amount = money_decimal(row["amount"]) if amount is None else amount
    payer, payee, own = (_identifier(row[key]) for key in ("payer_account", "payee_account", "self_account"))
    if (amount <= 0 or not payer or not payee or payer == payee or not row["currency"]
            or own not in {payer, payee}):
        return None
    return payer, payee, row["currency"], amount


def _cross_index(case_id, books, current_rows, wanted):
    """Potential opposite owners block certainty; these keys never merge facts."""
    index = defaultdict(set)
    def add(row, amount=None):
        from .bank_posting_pairs import _identifier
        key = _key(row, amount)
        if key not in wanted:
            return
        index[key].add(_identifier(row["self_account"]))
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
    return index


def project(case_id, candidates, books, unlocated, effective, current_rows, memberships, excluded_ids):
    from .bank_posting_pairs import _facts, _identifier, _matches_source
    references = _known_references(case_id, candidates)
    prepared = _prepare(books, unlocated)
    by_reference = defaultdict(list)
    for row in candidates:
        by_reference[row["reference"]].append(row)
    wanted = {_key(row["evidence"]["row"], money_decimal(row["evidence"]["signed_amount"]))
              for row in candidates if row["authority"] and row["evidence"] and row["evidence"]["kind"] == "financial"}
    wanted.discard(None)
    cross = _cross_index(case_id, books, current_rows, wanted)
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
