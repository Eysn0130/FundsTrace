"""Versioned, member-local bank postings; this does not establish settlement.

The ordinary primary reference is a candidate within this one source family.
It becomes a posting pair only with reciprocal account roles and independent
balance witnesses. Other source families keep their existing event contract.
"""
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import unicodedata

from . import db, source_ledger
from .money import money_decimal

POLICY = "bank-feedback31-paired-postings-v1"
FIELD = "bank_posting_pair_evidence"
PROOF_FIELD = "current_bank_posting_pair_proof"
PENDING_FIELD = "current_bank_posting_pair_pending"
PROFILES = frozenset(("funds-trace-table-v69", "funds-trace-table-v70", "funds-trace-table-v71", "funds-trace-table-v66", "funds-trace-table-v67", "funds-trace-table-v68",
                     "funds-trace-table-archive-v57", "funds-trace-table-archive-v58", "funds-trace-table-archive-v59",
                     "funds-trace-table-v63","funds-trace-table-v60", "funds-trace-table-v64","funds-trace-table-v61", "funds-trace-table-v65","funds-trace-table-v62",
                     "funds-trace-table-archive-v54","funds-trace-table-archive-v51", "funds-trace-table-archive-v55","funds-trace-table-archive-v52", "funds-trace-table-archive-v56","funds-trace-table-archive-v53", "funds-trace-table-v57", "funds-trace-table-v58", "funds-trace-table-v59",
                     "funds-trace-table-archive-v48", "funds-trace-table-archive-v49", "funds-trace-table-archive-v50", "funds-trace-table-v54", "funds-trace-table-v55", "funds-trace-table-v56",
                     "funds-trace-table-archive-v45", "funds-trace-table-archive-v46", "funds-trace-table-archive-v47", "funds-trace-table-v51", "funds-trace-table-v52", "funds-trace-table-v53",
                     "funds-trace-table-archive-v42", "funds-trace-table-archive-v43", "funds-trace-table-archive-v44"))


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


def _inputs(case_id):
    from . import table_import
    from .table_cancellation_links import active_support_sql, ordinary_csv_member
    rows = db.q_raw(f"""select r.source_id,r.record_no,r.member_path,r.source_row,r.disposition,
        s.parser_version,s.filename,s.sheet,s.row_kind,s.sha256 source_sha256,
        m.sha256 member_sha256,m.role,m.encoding,f.flow_id,
        json_extract_string(r.details_json,'$.{source_ledger.FIELD}') ledger_evidence,
        json_extract_string(r.details_json,'$.{FIELD}') pair_evidence,
        json_extract_string(r.details_json,'$.trade_no_basis') trade_no_basis
        from import_records r join import_sources s on s.case_id=r.case_id and s.source_id=r.source_id
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
        left join flow_sources f on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no
        where r.case_id=? and s.parser_version in (select unnest(?::varchar[]))
          and r.disposition<>'removed' and {active_support_sql('s')}
        order by r.source_id,r.member_path,r.source_row""", [case_id, sorted(table_import.SOURCE_LEDGER_PROFILES)])
    references, unlocated, direct_members = set(), set(), set()
    for row in rows:
        if ordinary_csv_member(row["parser_version"], {"filename": row["filename"], "sheet": row["sheet"],
                "row_kind": row["row_kind"], "sha256": row["source_sha256"]}, {"member_path": row["member_path"],
                "sha256": row["member_sha256"], "role": row["role"], "encoding": row["encoding"]}):
            direct_members.add(row["member_sha256"])
        pair_evidence = row.pop("pair_evidence")
        row["contract"] = validate(pair_evidence) if pair_evidence is not None else None
        if row["contract"] and row["parser_version"] in PROFILES and row["contract"]["source_primary_reference"]:
            references.add(row["contract"]["source_primary_reference"])
        ledger_evidence = row.pop("ledger_evidence")
        row["evidence"] = source_ledger.validate(ledger_evidence) if ledger_evidence is not None else None
        if row["disposition"] == "unresolved" and row["evidence"] is None:
            unlocated.add((row["source_id"], row["member_path"]))
    if not references:
        return [], {}, unlocated
    books, candidates, book_entries = defaultdict(list), [], {}
    for row in rows:
        evidence, contract = row["evidence"], row["contract"]
        row["position_path"] = None if row["member_sha256"] in direct_members else row["member_path"]
        if evidence:
            scope = source_ledger._scope(row)
            # The importer's registered posting-repeat contract already shares
            # an ID for repeated source copies. It adds no chronology evidence.
            entry = (scope, row["flow_id"], _facts(row)) if row["flow_id"] is not None else (scope, row["record_no"])
            if entry not in book_entries:
                row["_physical_rows"] = {row["source_row"]}
                books[scope].append(row)
                book_entries[entry] = row
            else:
                book_entries[entry]["_physical_rows"].add(row["source_row"])
        row["authority"] = contract is not None and row["parser_version"] in PROFILES
        reference = (contract["source_primary_reference"] if row["authority"] else
                     _identifier(evidence["row"]["trade_no"]) if evidence and
                     row["trade_no_basis"] == "source_transaction_id" else "")
        if reference not in references:
            continue
        bank = contract["source_counterparty_bank"] if row["authority"] else _identifier(
            evidence["row"]["payee_bank" if evidence["direction"] == "out" else "payer_bank"])
        row.update(reference=reference, bank=bank)
        candidates.append(row)
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


def project(case_id, *, excluded_ids=(), source_roles=False):
    """Retain observations; unproved corresponding pairs withhold certain funds."""
    candidates, books, unlocated = _inputs(case_id)
    if not candidates:
        return [], [], []
    ids = sorted({int(row["flow_id"]) for row in candidates if row["flow_id"] is not None})
    effective = {str(row["id"]): row for row in db.q_raw("""select id,payer_account,payee_account,self_account,
        currency,amount,trade_time,trade_time_utc from effective_flows
        where case_id=? and id in (select unnest(?::bigint[]))""", [case_id, ids])}
    book_ids = sorted({int(row["flow_id"]) for group in books.values() for row in group if row["flow_id"] is not None})
    current_rows = {str(row["id"]): row for row in db.q_raw("""select id,trade_no,payer_account,payee_account,
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
            member_references[row["source_id"], row["member_path"], row["evidence"]["header_scope"], row["reference"]].add(_position(row))
    events, memberships = [], {}
    for (bank, reference), rows in sorted(groups.items()):
        positions = defaultdict(list)
        for row in rows:
            positions[_position(row)].append(row)
        reason, proof = "bank_pair_incomplete_evidence", None
        # Counterparty bank is a row role, not a primary-reference issuer namespace.
        reused_in_member = any(row["evidence"] and len(member_references[
            row["source_id"], row["member_path"], row["evidence"]["header_scope"], reference]) > 2 for row in rows)
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
    if not source_roles:
        from . import bank_single_postings
        events.extend(bank_single_postings.project(case_id, candidates, books, unlocated,
            effective, current_rows, memberships, excluded_ids))
    return events, [memberships[key] for key in sorted(memberships)], candidates


def source_role_pairs(case_id):
    """Prove raw reciprocal roles before typed endpoints are projected.

    The caller still requires the same closed, explicit institution binding on
    both observations. A numeric bank field alone never names an institution.
    Old parser versions retain their original endpoint interpretation.
    """
    from . import table_import
    present = db.q_raw(f"""select 1 from import_sources s join import_records r
        on r.case_id=s.case_id and r.source_id=s.source_id where s.case_id=?
        and s.parser_version in (select unnest(?::varchar[])) and r.disposition<>'removed'
        and regexp_full_match(json_extract_string(json_extract_string(r.details_json,'$.{FIELD}'),
            '$.source_counterparty_bank'),'[0-9]+')
        group by r.source_id,r.member_path,
            json_extract_string(json_extract_string(r.details_json,'$.{FIELD}'),'$.source_primary_reference')
        having count(*)>1 limit 1""", [case_id, sorted(table_import.NATIVE31_PROFILES)])
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
