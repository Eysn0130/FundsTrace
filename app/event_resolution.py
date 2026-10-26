"""Source-declared cross-ledger events, separate from observations and balances.

An ordinary transaction/voucher number is deliberately not an event key. Only
the closed, explicitly named reference contract below authorizes this projection.
It records what the source declares; it does not authenticate a bank statement.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, localcontext
import hashlib
import json
import re
import unicodedata

from . import db

POLICY = "scoped-event-reference-v1"
LEDGER_POLICY = "explicit-ledger-fields-v1"
DETAIL_FIELD = "event_reference_evidence"
LEDGER_FIELD = "analysis_evidence"
PROFILES = frozenset(("funds-trace-table-v20", "funds-trace-table-archive-v6"))
HEADERS = {"issuer": "参考号机构", "channel": "参考号渠道", "business_scope": "参考号业务域",
           "reference": "跨账簿事件参考号"}
POST_BALANCE_HEADERS = frozenset(("交易后账面余额", "本笔交易后账面余额"))
_MAX_FIELD = 128


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text(value):
    return (isinstance(value, str) and bool(value) and value == value.strip()
            and len(value) <= _MAX_FIELD and value.casefold() not in {"null", "none", "nan", "n/a"}
            and any(char.isalnum() for char in value)
            and not any(unicodedata.category(char) in {"Cc", "Cf"} for char in value))


def parse_fields(headers, cells, *, accepted, posting_scope=None):
    """Read only exact source declarations, never infer from generic fields.

    Incomplete or duplicate reference headers confer no event authority. A
    complete declaration on an unresolved financial row is retained so it can
    invalidate a formerly compatible event instead of silently disappearing.
    """
    result = {}
    if all(headers.count(header) == 1 for header in HEADERS.values()):
        values = {key: cells[headers.index(header)] for key, header in HEADERS.items()}
        if all(_text(value) for value in values.values()):
            result[DETAIL_FIELD] = _json({"policy": POLICY, **values})
    if not accepted:
        return result
    balance_kind = "post_book" if sum(header in POST_BALANCE_HEADERS for header in headers) == 1 else "unknown"
    order = ""
    if (headers.count("记账顺序号") == 1 and type(posting_scope) is int
            and 0 < posting_scope <= 10 ** 38 - 1):
        value = cells[headers.index("记账顺序号")]
        if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,37}", value):
            order = value
    if balance_kind != "unknown" or order:
        result[LEDGER_FIELD] = _json({"policy": LEDGER_POLICY, "balance_kind": balance_kind,
                                    "posting_order": order, "posting_scope": str(posting_scope) if order else ""})
    return result


def validate_reference(value):
    if not isinstance(value, str) or len(value.encode()) > 2048:
        raise ValueError("invalid event reference evidence")
    data = json.loads(value)
    if (not isinstance(data, dict) or set(data) != {"policy", *HEADERS}
            or data.get("policy") != POLICY or not all(_text(data.get(key)) for key in HEADERS)):
        raise ValueError("invalid scoped event reference")
    return data


def validate_ledger(value):
    if not isinstance(value, str) or len(value.encode()) > 512:
        raise ValueError("invalid ledger evidence")
    data = json.loads(value)
    if (not isinstance(data, dict) or set(data) != {"policy", "balance_kind", "posting_order", "posting_scope"}
            or data.get("policy") != LEDGER_POLICY or data.get("balance_kind") not in {"unknown", "post_book"}
            or not isinstance(data.get("posting_order"), str)
            or not isinstance(data.get("posting_scope"), str)
            or bool(data["posting_order"]) != bool(data["posting_scope"])
            or data["posting_order"] and (not re.fullmatch(r"[1-9][0-9]{0,37}", data["posting_order"])
                                          or not re.fullmatch(r"[1-9][0-9]{0,37}", data["posting_scope"]))):
        raise ValueError("invalid explicit ledger fields")
    return data


def resolve(observations):
    """Pure reference-group decision; duplicate links are not new evidence."""
    groups, flow_keys = defaultdict(list), defaultdict(set)
    for row in observations:
        evidence = validate_reference(row["evidence"])
        key = tuple(evidence[field] for field in HEADERS)
        groups[key].append(row)
        if row.get("flow_id") is not None:
            flow_keys[str(row["flow_id"])].add(key)
    events, memberships = [], {}
    for key, rows in sorted(groups.items()):
        unique = {}
        reason = ""
        for row in rows:
            flow_id = row.get("flow_id")
            if flow_id is None or not row.get("effective"):
                reason = "unresolved_or_excluded_observation"
                continue
            flow_id = str(flow_id)
            facts = tuple(row.get(field) for field in ("payer_account", "payee_account", "currency", "amount", "trade_time"))
            own = row.get("self_account")
            if (not all(facts) or facts[0] == facts[1] or not isinstance(facts[3], Decimal)
                    or facts[3] <= 0 or own not in facts[:2] or not row.get("typed_accounts")):
                reason = "incomplete_ledger_identity"
            if len(flow_keys[flow_id]) != 1:
                reason = "conflicting_reference_declarations"
            previous = unique.get(flow_id)
            if previous is not None and previous != (facts, own):
                reason = "conflicting_observation_facts"
            unique[flow_id] = facts, own
        if not reason and len(unique) == 2:
            values = list(unique.values())
            facts, own = values[0]
            if facts != values[1][0]:
                reason = "conflicting_event_facts"
            elif {own, values[1][1]} != set(facts[:2]):
                reason = "multiple_entries_on_one_side"
        elif not reason:
            reason = "reference_reused" if len(unique) > 2 else "missing_opposite_ledger"
        event_id = None
        status = "unknown" if reason in {"missing_opposite_ledger", "incomplete_ledger_identity"} else "conflict"
        if not reason:
            facts = next(iter(unique.values()))[0]
            evidence = {"policy": POLICY, **dict(zip(HEADERS, key))}
            event_id = "ftev_" + hashlib.sha256(_json([evidence, [str(value) for value in facts]]).encode()).hexdigest()
            events.append({"event_id": event_id, **dict(zip(("payer_account", "payee_account", "currency", "amount", "trade_time"), facts)),
                           "evidence_json": _json(evidence), "flow_ids": sorted(unique)})
            status, reason = "confirmed", "scoped_reference_and_opposite_ledgers"
        for flow_id in unique:
            membership = {"flow_id": flow_id, "event_id": event_id, "status": status, "reason": reason}
            previous = memberships.get(flow_id)
            if previous is not None and previous != membership:
                membership = {"flow_id": flow_id, "event_id": None, "status": "conflict", "reason": "conflicting_reference_declarations"}
            memberships[flow_id] = membership
    return events, sorted(memberships.values(), key=lambda row: row["flow_id"])


def _observations(case_id):
    profiles = sorted(PROFILES)
    placeholders = ",".join("?" for _ in profiles)
    # Avoid the effective-flow joins entirely for ordinary imports. The closed
    # journal key cannot occur unescaped inside a quoted source value.
    present = db.q_raw(f"""select 1 from import_sources s
        join evidence_sources b on b.case_id=s.case_id and b.source_id=s.source_id and b.active
        join import_records r on r.case_id=s.case_id and r.source_id=s.source_id
        where s.case_id=? and s.parser_version in ({placeholders}) and r.disposition<>'removed'
          and contains(r.details_json, '"{DETAIL_FIELD}"') limit 1""", [case_id, *profiles])
    if not present:
        return []
    # Only the two explicit-contract profiles can supply event declarations.
    # All other bank fields remain ordinary observations, without JSON replay.
    query = f"""select fs.flow_id, f.id is not null effective,
        f.payer_account,f.payee_account,f.self_account,f.currency,f.amount,f.trade_time,
        (select count(*)=3 and bool_and(ep.state='resolved' and entity.kind in ('account','subaccount') and entity.bank<>'')
         from resolved_endpoints ep join resolution_entities entity on entity.case_id=ep.case_id and entity.entity_id=ep.entity_id
         where ep.case_id=fs.case_id and ep.flow_id=fs.flow_id) scoped_accounts,
        json_extract_string(r.details_json,'$.{DETAIL_FIELD}') evidence,
        json_extract_string(r.details_json,'$.resolution_evidence') identity_evidence
        from import_records r join import_sources s on s.case_id=r.case_id and s.source_id=r.source_id
        join evidence_sources b on b.case_id=r.case_id and b.source_id=r.source_id and b.active
        left join flow_sources fs on fs.case_id=r.case_id and fs.source_id=r.source_id and fs.source_row=r.record_no
        left join effective_flows f on f.case_id=fs.case_id and f.id=fs.flow_id
        where r.case_id=? and s.parser_version in ({placeholders}) and r.disposition<>'removed'
          and json_extract_string(r.details_json,'$.{DETAIL_FIELD}') is not null"""
    rows = db.q_raw(query, [case_id, *profiles])
    for row in rows:
        try:
            identity = json.loads(row.pop("identity_evidence") or "{}")
            row["typed_accounts"] = bool(row.pop("scoped_accounts")) and all(
                identity.get("endpoints", {}).get(side, {}).get("identifier_type") == "account"
                and identity["endpoints"][side].get("account") for side in ("payer", "payee", "self"))
        except (ValueError, TypeError, KeyError):
            row["typed_accounts"] = False
    return rows


def rebuild(case_id):
    """Recompute derived event membership inside the caller's write transaction."""
    observations = _observations(case_id)
    # A case with no explicit declaration has no event projection to maintain.
    # Its ordinary statistics already report the observation denominator.
    if not observations and not db.q_raw("select 1 from event_state where case_id=?", [case_id]):
        return None
    events, memberships = resolve(observations)
    effective_ids = {str(row["flow_id"]) for row in observations if row.get("effective") and row.get("flow_id") is not None}
    counts = db.q_raw("select count(*) n from effective_flows where case_id=?", [case_id])[0]["n"]
    confirmed = sum(row["status"] == "confirmed" for row in memberships)
    conflicted = sum(row["status"] == "conflict" and row["flow_id"] in effective_ids for row in memberships)
    with localcontext() as context:
        context.prec = 50
        totals = defaultdict(lambda: Decimal(0))
        for event in events:
            totals[event["currency"]] += event["amount"]
        result = {"policy": POLICY, "confirmed_events": len(events), "confirmed_entries": confirmed,
                  "conflict_entries": conflicted, "unknown_entries": counts - confirmed - conflicted,
                  "observed_entries": counts, "reference_observations": len(observations),
                  "excluded_reference_entries": len({str(row["flow_id"]) for row in observations if row.get("flow_id") is not None} - effective_ids),
                  "unresolved_reference_observations": sum(row.get("flow_id") is None for row in observations),
                  "confirmed_amount_totals": [{"currency": currency, "amount": format(amount, ".2f")} for currency, amount in sorted(totals.items())],
                  "complete_event_total": False,
                  "note": "仅明确作用域且双边唯一的来源声明可关联；未关联记录仍保留，已关联事件额不是全案经济事件总额。"}
    canonical = [{key: str(value) if isinstance(value, Decimal) or hasattr(value, "isoformat") else value for key, value in event.items()} for event in events]
    revision = hashlib.sha256(_json([result, canonical, memberships]).encode()).hexdigest()
    result["revision"] = revision
    db.x("delete from event_members where case_id=?", [case_id])
    db.x("delete from resolved_events where case_id=?", [case_id])
    from .evidence_resolution import _insert_projection
    event_columns = ("case_id", "event_id", "payer_account", "payee_account", "currency", "amount", "trade_time", "evidence_json")
    _insert_projection("resolved_events", list(event_columns),
                       ([case_id, *[event[key] for key in event_columns[1:]]] for event in events))
    _insert_projection("event_members", ["case_id", "flow_id", "event_id", "status", "reason"],
                       ([case_id, member["flow_id"], member["event_id"], member["status"], member["reason"]] for member in memberships))
    db.x("insert into event_state values (?,?,?) on conflict(case_id) do update set revision=excluded.revision,summary_json=excluded.summary_json",
         [case_id, revision, _json(result)])
    return result


def summary(case_id):
    rows = db.q_raw("select summary_json from event_state where case_id=?", [case_id])
    return json.loads(rows[0]["summary_json"]) if rows else None


def members(case_id, flow_ids=None):
    if flow_ids is not None and not flow_ids:
        return []
    if flow_ids is not None:
        flow_ids = list(dict.fromkeys(map(str, flow_ids)))
        if len(flow_ids) > 4096:
            result = []
            for offset in range(0, len(flow_ids), 4096):
                result.extend(members(case_id, flow_ids[offset:offset + 4096]))
            return sorted(result, key=lambda row: int(row["flow_id"]))
    clause = "" if flow_ids is None else " and flow_id in (" + ",".join("?" for _ in flow_ids) + ")"
    rows = db.q_raw("select flow_id,event_id,status,reason from event_members where case_id=?" + clause + " order by flow_id",
                    [case_id, *(flow_ids or [])])
    return [{**row, "flow_id": str(row["flow_id"])} for row in rows]


def decorate_edges(case_id, edges):
    """Attach selected confirmed-event totals beside unchanged observations.

    A selected member may refer to a confirmed event whose opposite ledger is
    outside the canvas. That event counts once on its directed edge; unrelated
    events and unknown amounts never enter the selected event totals.
    """
    selected = set()
    for edge in edges:
        edge.pop("event_summary", None)
        selected.update(map(str, edge.get("ids", [])))
    if not selected or not db.q_raw("select 1 from event_state where case_id=?", [case_id]):
        return edges
    ids, by_id = list(selected), {}
    for offset in range(0, len(ids), 4096):
        batch = ids[offset:offset + 4096]
        rows = db.q_raw("""select f.id,m.status,e.event_id,e.payer_account,e.payee_account,e.currency,e.amount
            from flows f left join event_members m on m.case_id=f.case_id and m.flow_id=f.id
            left join resolved_events e on e.case_id=m.case_id and e.event_id=m.event_id
            where f.case_id=? and f.id in (select unnest(?::bigint[]))""", [case_id, batch])
        by_id.update((str(row["id"]), row) for row in rows)
    with localcontext() as context:
        context.prec = 50
        for edge in edges:
            rows = [by_id[flow_id] for flow_id in set(map(str, edge.get("ids", []))) if flow_id in by_id]
            if not any(row["status"] is not None for row in rows):
                continue
            events, confirmed, conflicted = {}, 0, 0
            for row in rows:
                if (row["status"] == "confirmed" and row["event_id"] is not None
                        and (row["payer_account"], row["payee_account"]) == (edge["source"], edge["target"])):
                    events[row["event_id"]] = row
                    confirmed += 1
                elif row["status"] == "conflict":
                    conflicted += 1
            totals = defaultdict(lambda: Decimal(0))
            for event in events.values():
                totals[event["currency"]] += event["amount"]
            edge["event_summary"] = {"confirmed_events": len(events), "confirmed_entries": confirmed,
                "conflict_entries": conflicted, "unknown_entries": len(rows) - confirmed - conflicted,
                "confirmed_amount_totals": [{"currency": currency, "amount": format(amount, ".2f")}
                                            for currency, amount in sorted(totals.items())]}
    return edges


def ledger_metadata(case_id, flow_ids):
    """Return explicit fields only when every retained declaration agrees.

    A sequence is local to its source, member, header region, currency and own
    ledger. The opaque scopes only authorize comparison; their lexical order
    has no meaning. Replays preserve common scopes without adding authority.
    """
    if not flow_ids:
        return {}
    if len(flow_ids) > 4096:
        result = {}
        for offset in range(0, len(flow_ids), 4096):
            result.update(ledger_metadata(case_id, flow_ids[offset:offset + 4096]))
        return result
    profiles, values = sorted(PROFILES), defaultdict(list)
    query = f"""select fs.flow_id,fs.source_id,r.member_path,
        coalesce(o.entity_id,f.self_account) own_account,f.currency,
        json_extract_string(r.details_json,'$.{LEDGER_FIELD}') evidence
        from flow_sources fs join import_records r on r.case_id=fs.case_id and r.source_id=fs.source_id and r.record_no=fs.source_row
        join import_sources s on s.case_id=fs.case_id and s.source_id=fs.source_id
        join evidence_sources b on b.case_id=s.case_id and b.source_id=s.source_id and b.active
        join flows f on f.case_id=fs.case_id and f.id=fs.flow_id
        left join resolved_endpoints o on o.case_id=f.case_id and o.flow_id=f.id and o.role='self'
        where fs.case_id=? and fs.flow_id in ({','.join('?' for _ in flow_ids)})
          and s.parser_version in ({','.join('?' for _ in profiles)})"""
    for row in db.q_raw(query, [case_id, *flow_ids, *profiles]):
        evidence = validate_ledger(row["evidence"]) if row["evidence"] else None
        scope = None
        if evidence:
            region = evidence.pop("posting_scope")
            if region and row["own_account"]:
                scope = hashlib.sha256(_json([row["source_id"], row["member_path"], region,
                                             row["own_account"], row["currency"]]).encode()).hexdigest()
        values[str(row["flow_id"])].append((row["source_id"], evidence, scope))
    result = {}
    for flow_id, declarations in values.items():
        evidence = {_json(value) for _, value, _ in declarations}
        if len(evidence) == 1 and declarations[0][1] is not None:
            result[flow_id] = {**declarations[0][1],
                "source_ids": sorted({source for source, _, _ in declarations}),
                "order_scopes": sorted({scope for _, _, scope in declarations if scope})}
        else:
            result[flow_id] = {"policy": LEDGER_POLICY, "balance_kind": "unknown", "posting_order": "",
                               "source_ids": [], "order_scopes": [], "conflict": True}
    return result
