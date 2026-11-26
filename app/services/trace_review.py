"""Case-bound review of postings, candidate provenance and manual selection."""
from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal
import csv
import json
from time import monotonic
from uuid import uuid4
import zipfile

from fastapi import HTTPException

from .. import analysis_snapshot, bank_posting_pairs, db, event_resolution, feedback_import, funds_projection
from .. import import_originals, ledger_navigation, principal_evidence, source_ledger, table_import, transaction_time
from ..table_mapping import ACCOUNT_CARD_FIELDS, identity_field
from . import import_layout_trial, import_report, imports, trace_group_evidence
from .cases import ServiceError


@dataclass(frozen=True)
class Review:
    case_id: int
    canvas_id: int
    anchor_id: int
    revision: str
    flow_ids: frozenset[int]
    expires: float
    canvas_membership: str | None
    directions: tuple[frozenset[int], frozenset[int]]
    trace_cards: dict = field(default_factory=dict)
    limited_principal_targets: frozenset[int] = frozenset()


# All callers that issue, use or revoke a review hold the existing DB lock.
_reviews: dict[str, Review] = {}
_MAX_OPEN = 128
_VALID_FOR_SECONDS = 900
_EDGE_RELATED_LIMIT = 500
_PAIR_REVIEW_LIMIT = 64
_CARD_MIRROR_REASON = "source_card_text_pending"
_ACCOUNT_MIRROR_REASON = "source_account_text_pending"


def canvas_membership(case_id, canvas_id):
    return db.q_raw("""select sha256(coalesce(string_agg(flow_id::varchar,',' order by flow_id),''))
        as membership_digest from tracked_flows where case_id=? and canvas_id=?""",
        [case_id, canvas_id])[0]["membership_digest"]


def _issue(case_id, canvas_id, anchor_id, revision, rows, forward, reverse, *, membership=None, trace_cards=None,
           limited_principal_targets=()):
    while len(_reviews) >= _MAX_OPEN:
        del _reviews[next(iter(_reviews))]
    token = uuid4().hex
    _reviews[token] = Review(case_id, canvas_id, anchor_id, revision, frozenset(rows),
                             monotonic() + _VALID_FOR_SECONDS, membership,
                             tuple(frozenset(row["id"] for row in direction) for direction in (forward, reverse)),
                             {key: tuple(item["evidence"] for item in items)
                              for key, items in (trace_cards or {}).items()},
                             frozenset(limited_principal_targets))
    return token


def _require(token, case_id, canvas_id, *, anchor_id=None):
    review = _reviews.get(token)
    if (review is None or review.expires <= monotonic()
            or (review.case_id, review.canvas_id) != (case_id, canvas_id)
            or anchor_id is not None and review.anchor_id != anchor_id):
        raise HTTPException(409, "核对窗口已失效，请重新打开核对")
    if analysis_snapshot.revision(case_id) != review.revision:
        raise HTTPException(409, "流水来源或关联已更新，请重新打开核对")
    if canvas_id and not db.q_raw("select 1 from canvases where case_id=? and id=?", [case_id, canvas_id]):
        raise HTTPException(409, "核对画布已失效，请重新打开核对")
    if review.canvas_membership is not None and canvas_membership(case_id, canvas_id) != review.canvas_membership:
        raise HTTPException(409, "图谱上的关系成员已更新，请重新打开核对")
    return review


def verify_selection(token, case_id, canvas_id, removed, added):
    review = _require(token, case_id, canvas_id)
    if removed or not set(added) <= review.flow_ids:
        raise HTTPException(409, "只能上图本次核对中手选的原始记录")


def revoke(token, case_id, canvas_id):
    review = _reviews.get(token)
    if review is not None and (review.case_id, review.canvas_id) == (case_id, canvas_id):
        del _reviews[token]


def summary(token, case_id, canvas_id, flow_ids):
    review = _require(token, case_id, canvas_id)
    selected = set(flow_ids)
    if not selected <= review.flow_ids:
        raise HTTPException(409, "核对窗口外的流水不能加入连线统计")
    records = db.q("""select * from effective_flows where case_id=? and id in (select unnest(?::bigint[]))""",
                   [case_id, list(selected & (review.directions[0] | review.directions[1]))])
    rows = {row["id"]: row for row in records}
    _decorate_rows(case_id, rows, {})
    return _lines(case_id, [row for row in records if row["id"] in review.directions[0]],
                  [row for row in records if row["id"] in review.directions[1]])


def _ordered(rows):
    bases = {transaction_time.basis(row) for row in rows if transaction_time.clock(row) is not None}
    if len(bases) > 1:
        return list(rows)
    return sorted(rows, key=lambda row: (transaction_time.clock(row) is None, transaction_time.clock(row)))


def _locators(case_id, flow_ids, *, include_ledger=False):
    ledger = ("json_extract_string(r.details_json,'$.source_ledger_evidence') ledger_evidence,"
              "json_extract_string(r.details_json,'$.source_time_evidence') time_evidence,"
              "json_extract_string(r.details_json,'$.source_time_evidence_limited') time_evidence_limited,"
              "m.sha256 member_sha256,"
              f"json_extract_string(r.details_json,'$.{table_import.TRIAL_MEMBER_PROFILE_FIELD}') trial_member_profile,"
              if include_ledger else "")
    return db.q_raw(f"""select fs.flow_id,s.source_id,s.sha256,s.filename,s.parser_version,r.member_path,r.source_row,
              r.disposition,{ledger}
              r.record_no,json_extract_string(r.details_json,'$.sheet') sheet,m.encoding,session.suffix,
              json_extract_string(r.details_json,'$.resolution_evidence') identity_evidence,
              json_extract_string(r.details_json,'$.source_field_evidence') field_evidence,
              json_extract_string(r.details_json,'$.source_account_column') account_column,
              json_extract_string(r.details_json,'$.bank_posting_pair_evidence') is not null native_pair_contract,
              json_extract_string(json_extract_string(r.details_json,'$.source_ledger_evidence'),'$.header_scope') header_scope,
              json_extract_string(json_extract_string(r.details_json,'$.source_ledger_evidence'),'$.source_direction') direction
        from flow_sources fs
        join import_sources s on s.case_id=fs.case_id and s.source_id=fs.source_id
        join import_records r on r.case_id=fs.case_id and r.source_id=fs.source_id and r.record_no=fs.source_row
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
        left join import_sessions session on session.case_id=s.case_id and session.import_id=s.source_id
        where fs.case_id=? and fs.flow_id in (select unnest(?::bigint[])) and r.disposition<>'removed'
        qualify row_number() over (partition by fs.flow_id order by
            (session.suffix is not null) desc,(r.disposition='imported') desc,s.source_id,r.record_no)=1
        order by fs.flow_id""", [case_id, list(flow_ids)])


def _review_table_summary(receipt):
    summary = json.loads(receipt["result_json"] or "{}").get("table_summary") or {}
    request = json.loads(receipt["request_json"] or "{}")
    if request.get("_table_overrides") and not request.get("_manual_mapping_evidence_v1"):
        # Old overrides could replace all competing candidates before their
        # selected column was recorded as an exact hint. Keep the location
        # as a competing mapping, without giving it source-role authority.
        summary["mapping_evidence"] = [
            {**entry, "basis": "manual_override"}
            if entry.get("field") in ACCOUNT_CARD_FIELDS and entry.get("basis") == "exact_header_alias" else entry
            for entry in (summary.get("mapping_evidence") or [])
        ]
    return summary


def _recorded_counter_columns(case_id, locators):
    source_ids = {entry["source_id"] for entry in locators.values()
                  if not entry["identity_evidence"] and not entry["native_pair_contract"] and entry["header_scope"]}
    if not source_ids:
        return
    evidence = {}
    card_regions = set()
    for receipt in db.q_raw("""select import_id,result_json,request_json from import_sessions
            where case_id=? and import_id in (select unnest(?::varchar[]))""", [case_id, list(source_ids)]):
        summary = _review_table_summary(receipt)
        for field in summary.get("mapping_evidence") or []:
            key = (receipt["import_id"], field.get("member_path"), str(field.get("header_row")))
            basis, label = field.get("basis"), field.get("column")
            if field.get("field") == "counter_card":
                card_regions.add(key)
            elif (field.get("field") == "counter_account" and str(field.get("candidate_count")) == "1"
                  and (basis == "exact_header_alias" or basis == "explicit_identity_role"
                       and label in {"交易对方账卡号", "交易对手账卡号"})):
                evidence[key] = label
    for entry in locators.values():
        key = (entry["source_id"], entry["member_path"], entry["header_scope"])
        # An empty account cell may have been replaced by a card in the
        # financial endpoint. Column metadata cannot establish this row's type.
        if key not in card_regions:
            entry["counter_column"] = evidence.get(key)


def _review_book_direction(row, identity, locator, marker, excerpts):
    own = row.get("self_account")
    direction = trace_group_evidence._direction(row, own) if own else None
    if direction is None:
        return None, None
    own_side = "payer" if direction == "out" else "payee"
    ledger = json.loads(locator.get("ledger_evidence") or "{}")
    if identity.get("role") == "transaction":
        if identity.get("own_side") not in (None, "", own_side):
            return None, None
        proved = {role + "_account": endpoint.get("account") or endpoint.get("card")
                  for role, endpoint in identity["endpoints"].items()}
    elif not identity and ledger.get("kind") == "financial" and ledger["direction"] == direction:
        proved = ledger["row"]
    else:
        return None, None
    source = {field: bank_posting_pairs._identifier(row.get("raw_" + field))
              for field in ("self_account", "payer_account", "payee_account")}
    if (trace_group_evidence._direction(source, source["self_account"]) != direction
            or any(value != proved.get(field) for field, value in source.items())):
        return None, None
    markers = {cell["raw"] for cell in excerpts if cell.get("field") == "direction" and cell.get("raw")}
    if marker:
        markers.add(marker)
    if not markers:
        return direction, "source_own_endpoint"
    for original in markers:
        label = table_import._enum(original)
        meaning = "in" if label in table_import._IN else "out" if label in table_import._OUT else None
        if meaning is None and original == ledger.get("source_direction"):
            meaning = ledger.get("direction")
        if meaning != direction:
            return None, None
    return direction, "source_marker"


def _review_time_precision(row, locator):
    stamp = transaction_time.parsed(row.get("trade_time"))
    if stamp is None:
        return None, None
    midnight = stamp.time() == time.min
    source_day = stamp.date().isoformat()
    unproved_clock = ("date_or_midnight", source_day) if midnight else (None, None)
    if locator.get("time_evidence_limited"):
        return unproved_clock
    evidence = json.loads(locator.get("time_evidence") or "{}")
    inputs = [entry["raw"] for entry in evidence.get("values", ()) if entry["role"] in {"transaction", "unspecified"}]
    if not inputs:
        return unproved_clock
    if all(table_import._date_only(raw) for raw in inputs):
        return ("date", source_day) if midnight else (None, None)
    # Workbook cells with a date format can also be recorded as midnight text.
    if (locator.get("sheet") and midnight
            and all(table_import._date_only(raw) or raw == transaction_time.text(stamp) for raw in inputs)):
        return unproved_clock
    return "time", None


def _recorded_card_posting(row, locator):
    if (locator is None or locator["parser_version"] != table_import.BANK_FEEDBACK39_TRIAL_PROFILE
            or locator["disposition"] != "imported" or not locator["header_scope"]
            or _review_time_precision(row, locator)[0] != "time"):
        return None
    ledger = json.loads(locator["ledger_evidence"] or "{}")
    card = ledger.get("own_card")
    if (ledger.get("kind") != "financial" or card != row.get("source_own_card")
            or not isinstance(card, str) or not 12 <= len(card) <= 24
            or not card.isascii() or not card.isdecimal() or len(set(card)) < 2):
        return None
    direction, _ = _review_book_direction(row, {}, locator, locator.get("direction"), [])
    if direction is None:
        return None
    other = row.get("raw_payee_account" if direction == "out" else "raw_payer_account")
    return direction, card, other


def pair_review(case_id, anchor, *, product=False, limit=_PAIR_REVIEW_LIMIT, source_cache=None):
    """Add source-scoped leads to the existing economic review."""
    official = event_resolution.review_pair(case_id, anchor, limit=limit)
    if product and official["status"] == "unknown" and official["reason"] in {
            "voucher_candidate_only", "no_scoped_event_proof"}:
        from . import trial_pair_comparison

        anchor_id = int(anchor["id"])
        journals = {} if source_cache is None else source_cache
        pairs = trial_pair_comparison.canvas_ledger_pairs(case_id, {anchor_id}, include_competition=True,
                                                           source_cache=journals)
        if len(pairs) == 1 and not event_resolution.members(case_id, pairs[0]["flow_ids"]):
            [proof] = pairs
            [peer] = set(proof["flow_ids"]) - {anchor_id}
            peer = str(peer)
            if limit < _PAIR_REVIEW_LIMIT:
                official = event_resolution.review_pair(case_id, anchor, limit=_PAIR_REVIEW_LIMIT)
            competition = proof.get("competition", {}).get(anchor_id)
            ids = list(dict.fromkeys([peer, *(competition["candidate_ids"] if competition else ()), *official["candidate_ids"]]))
            shown = ids[:limit]
            vouchers = set(official.get("voucher_candidate_ids", ()))
            total = max(len(ids), competition["candidate_total"]) if competition else len(ids)
            exact = not official["more"] and (not competition or competition["all_candidates_linked"])
            return {**official, "candidate_ids": shown, "voucher_candidate_ids": [key for key in shown if key in vouchers],
                    "candidate_total": total, "candidate_total_exact": exact,
                    "more": official["more"] or total > len(shown) or not exact,
                    "ledger_correspondence": {"peer_id": peer, "source_id": proof["source_id"],
                        "member_path": proof["member_path"], "source_rows": list(proof["source_rows"]),
                        "witness_rows": [list(positions) for positions in proof["witness_rows"]],
                        "currency": proof["currency"], "amount": format(proof["amount"], ".2f")}}
        if official["reason"] == "no_scoped_event_proof" and not official["more"]:
            peers = {int(key) for key in official["candidate_ids"]} - {anchor_id}
            if len(peers) == 1 and (lead := trial_pair_comparison.account_pair_lead(case_id, {anchor_id, *peers}, journals)):
                return {**official, "reason": _ACCOUNT_MIRROR_REASON, "account_pair_lead": lead}
    if not product or official["candidate_ids"] or official["status"] != "unknown" or not anchor.get("source_own_card"):
        return official
    [origin] = _locators(case_id, [anchor["id"]], include_ledger=True) or [None]
    if origin and official["reason"] == "no_scoped_event_proof":
        journals = {} if source_cache is None else source_cache
        if cross := trial_pair_comparison.cross_source_card_candidates(case_id, anchor, origin, limit, source_cache=journals):
            result = {**official, **cross, "reason": _CARD_MIRROR_REASON if cross["candidate_ids"] else official["reason"]}
            proof = trial_pair_comparison.cross_source_card_pair(case_id, anchor, origin, cross, source_cache=journals)
            if proof and not event_resolution.members(case_id, proof["flow_ids"]):
                result["ledger_correspondence"] = {"peer_id": cross["candidate_ids"][0], "source_id": proof["source_id"],
                    "member_path": proof["member_path"], "source_rows": list(proof["source_rows"]),
                    "witness_rows": [list(rows) for rows in proof["witness_rows"]], "origins": proof["origins"],
                    "currency": proof["currency"], "amount": format(proof["amount"], ".2f")}
            return result
    if not (known := _recorded_card_posting(anchor, origin)):
        return official
    receipt = db.q_raw("""select result_json,request_json from import_sessions
        where case_id=? and import_id=? and stage='done'""", [case_id, origin["source_id"]])
    scope_fields = [] if not receipt else (_review_table_summary(receipt[0]).get("mapping_evidence") or [])
    required = {"self_card": ("交易卡号", "explicit_identity_role"), "self_name": ("主户名", "explicit_identity_role"),
                "counter_account": ("交易对手账卡号", "exact_header_alias")}
    for field in scope_fields:
        role = field.get("field")
        if (role in required and field.get("member_path") == origin["member_path"]
                and str(field.get("header_row")) == origin["header_scope"]
                and required[role] == (field.get("column"), field.get("basis"))
                and str(field.get("candidate_count")) == "1"):
            required.pop(role)
    if required:
        return official
    direction, card, counter = known
    stamp = transaction_time.clock(anchor)
    if not counter or counter == card or stamp is None:
        return official
    # The peer's counter text must equal the anchor's explicitly recorded card.
    # Use raw fields on both sides. Equality here grants no identity relationship.
    peer_counter = "raw_payer_account" if direction == "out" else "raw_payee_account"
    clock = "trade_time_utc" if transaction_time.basis(anchor) == "utc" else "trade_time"
    possible = db.q_raw(f"""with anchor_clock as (select ?::timestamp stamp)
        select f.* from effective_visible_flows f cross join anchor_clock a
        join lateral (select min(r.source_row) source_row from flow_sources fs join import_records r
             on r.case_id=fs.case_id and r.source_id=fs.source_id and r.record_no=fs.source_row
             where fs.case_id=f.case_id and fs.flow_id=f.id and fs.source_id=?
             and r.member_path=? and r.disposition='imported'
             and json_extract_string(json_extract_string(r.details_json,'$.source_ledger_evidence'),
                                     '$.header_scope')=?) posting on posting.source_row is not null
        where f.case_id=? and f.id<>? and f.amount=? and f.amount>0 and f.currency=?
        and (f.trade_time_utc is null)=?
        and f.{clock} between a.stamp - interval '24 hours' and a.stamp + interval '24 hours'
        and f.raw_self_account<>? and f.source_own_card=? and f.{peer_counter}=?
        order by abs(date_diff('microsecond',a.stamp,f.{clock})),posting.source_row,f.id limit ?""",
        [stamp, origin["source_id"], origin["member_path"], origin["header_scope"],
         case_id, anchor["id"], anchor["amount"], anchor["currency"], clock == "trade_time",
         anchor["raw_self_account"], counter, card, limit + 1])
    if not possible:
        return official
    locators = {row["flow_id"]: row for row in _locators(case_id, (row["id"] for row in possible), include_ledger=True)}
    scope = ("source_id", "member_path", "header_scope")
    wanted = ("in" if direction == "out" else "out", counter, card)
    candidates = [str(row["id"]) for row in possible if (location := locators.get(row["id"]))
                  and all(location[key] == origin[key] for key in scope)
                  and _recorded_card_posting(row, location) == wanted]
    if not candidates:
        return {**official, "more": True} if len(possible) > limit else official
    try:
        original, _ = imports.get_source_file(case_id, origin["source_id"])
        original.close()
    except ServiceError as exc:
        if exc.status not in (404, 409):
            raise
        return official
    return {**official, "reason": _CARD_MIRROR_REASON, "candidate_ids": candidates[:limit],
            "more": official["more"] or len(possible) > limit}


def _outline(row, locator):
    own = row.get("self_account")
    owner = "payer" if own and own == row.get("payer_account") else "payee" if own and own == row.get("payee_account") else None
    counter = {"payer": "payee", "payee": "payer"}.get(owner)
    ledger = row.get("source_ledger") or {}
    identity = json.loads(locator.get("identity_evidence") or "{}")
    endpoints = identity.get("endpoints", {})
    own_fields = endpoints.get("self", {})
    counter_fields = endpoints.get(counter, {}) if counter else endpoints.get("counter", {})
    if not counter_fields and counter and locator.get("native_pair_contract"):
        # This closed bank format calls the column 交易对手账卡号. It cannot
        # distinguish an account from a card by the value alone.
        counter_fields = {"account": row.get("raw_" + counter + "_account"),
                          "card": "", "identifier_type": "account_or_card"}
    label = locator.get("counter_column") or ""
    if not counter_fields and counter and label.endswith(("账号", "帐号", "账卡号", "帐卡号")):
        counter_fields = {"account": row.get("raw_" + counter + "_account"),
                          "identifier_type": "account_or_card" if label.endswith("卡号") else "account"}
    card = own_fields.get("card") or row.get("self_source_card") or ledger.get("own_card")
    account = own_fields.get("account") if own_fields else row.get("raw_self_account")
    if locator.get("account_column") in {"交易卡号", "本方卡号"}:
        card, account = card or account, None
    counter_card = counter_fields.get("card")
    counter_account = counter_fields.get("account")
    excerpts = json.loads(locator.get("field_evidence") or "[]")
    remarks = {cell["raw"] for cell in excerpts if cell.get("role") == "remark" and cell.get("raw")}
    direction = locator.get("direction") or ledger.get("source_direction")
    book_direction, basis = _review_book_direction(row, identity, locator, direction, excerpts)
    precision, source_date = _review_time_precision(row, locator)
    return {
        "card": card,
        "account": account,
        "name": row.get("self_name") or (row.get(owner + "_name") if owner else None),
        "direction": direction,
        "book_direction": book_direction,
        "book_direction_basis": basis,
        "trade_time_precision": precision,
        "trade_time_source_date": source_date,
        "counter_card": counter_card,
        "counter_account": counter_account,
        "counter_kind": counter_fields.get("identifier_type"),
        "counter_column": label,
        "counter_name": (row.get(counter + "_name") if counter else ledger.get("counter_name")),
        "remark": next(iter(remarks)) if len(remarks) == 1 else None,
    }


def _decorate_rows(case_id, rows, roles):
    locators = {entry["flow_id"]: entry for entry in _locators(case_id, rows, include_ledger=True)}
    _recorded_counter_columns(case_id, locators)
    for row in rows.values():
        row["review_roles"] = roles.get(row["id"], [])
        locator = locators.get(row["id"])
        row["review_fields"] = _outline(row, locator or {})
        row["review_original"] = (None if locator is None else
            {key: locator[key] for key in ("filename", "member_path", "sheet", "source_row")})


def _cards(rows, payer, payee):
    parties = {"payer": {}, "payee": {}}
    for row in rows.values():
        fields = row["review_fields"]
        row["review_cards"] = {}
        for side, subject in (("payer", payer), ("payee", payee)):
            if not subject:
                continue
            if row.get("self_account") == subject:
                account, card = fields.get("account"), fields.get("card")
            elif subject in (row.get("payer_account"), row.get("payee_account")):
                account = fields.get("counter_account") if fields.get("counter_kind") == "account" else None
                card = fields.get("counter_card")
            else:
                continue
            if not account and not card:
                continue
            options = parties[side]
            choice = options.setdefault((account, card), {"id": str(len(options)), "account": account, "card": card})
            row["review_cards"][side] = choice["id"]
    return {side: list(options.values()) for side, options in parties.items()}


def _wire_rows(rows):
    return [{**row, "id": str(row["id"])} for row in _ordered(rows.values())]


def _line_evidence(case_id, rows):
    canonical, funds = funds_projection.from_rows(case_id, rows)
    booked = funds_projection.transaction_fields(canonical)
    confirmed = {row["fact_event_id"] for row in canonical if row["fact_status"] == "confirmed"}
    members = event_resolution.members(case_id, [row["id"] for row in rows]) if confirmed else []
    ids = {member["flow_id"] for member in members if member["event_id"] in confirmed}
    proved = _ordered([row for row in rows if str(row["id"]) in ids])
    stamps = [transaction_time.text(row["trade_time"]) for row in proved if row.get("trade_time")]
    # Do not place a date-only or unqualified observation ahead of a clock in
    # a different basis. The source dates remain available in each record.
    comparable = len({transaction_time.basis(row) for row in proved if row.get("trade_time")}) <= 1
    notes = [row.get("review_fields", {}).get("remark") for row in proved]
    summaries = [row.get("summary") for row in proved]
    return funds, booked, {"times": [stamps[0], stamps[-1]] if stamps and comparable else [],
                   "remarks": list(dict.fromkeys(filter(None, notes)))[:6],
                   "summaries": list(dict.fromkeys(filter(None, summaries)))[:6]}


def _line_totals(forward, reverse):
    if any(funds["status"] != "exact" or len(funds["totals"]) > 1 for funds in (forward, reverse)):
        return None
    totals = [item for funds in (forward, reverse) for item in funds["totals"]]
    currencies = {item["currency"] for item in totals}
    if len(currencies) != 1 or None in currencies or any(item["amount"] is None for item in totals):
        return None
    front = Decimal(forward["totals"][0]["amount"]) if forward["totals"] else Decimal(0)
    back = Decimal(reverse["totals"][0]["amount"]) if reverse["totals"] else Decimal(0)
    return {"currency": currencies.pop(), "total": f"{front + back:.2f}", "net": f"{abs(front - back):.2f}",
            "direction": "forward" if front > back else "reverse" if front < back else "balanced"}


def _lines(case_id, forward, reverse):
    out, out_booked, out_details = _line_evidence(case_id, forward)
    incoming, in_booked, in_details = _line_evidence(case_id, reverse)
    return {"forward": out, "reverse": incoming,
            "booked": {"forward": out_booked, "reverse": in_booked},
            "annotations": {"forward": out_details, "reverse": in_details},
            "totals": _line_totals(out, incoming)}


def _evidence_locations(case_id, evidence):
    ids = {int(key) for item in evidence for key in item["evidence_row_ids"]}
    locations = {}
    for locator in _locators(case_id, ids) if ids else ():
        source = locator["source_id"]
        locations[str(locator["flow_id"])] = {
            **{key: locator[key] for key in ("filename", "sheet", "member_path", "source_row")},
            "download_url": (f"/api/v1/cases/{case_id}/sources/{source}/file"
                             if imports.source_is_available(case_id, source) else None),
        }
    for item in evidence:
        item["locations"] = [locations[key] for key in item["evidence_row_ids"] if key in locations]


def _clues(case_id, account, anchors, rows, role, *, product=False, calculated_target_ids=None):
    if not rows:
        return []
    party = "payer" if role == "source" else "payee"
    groups = {}
    for row in rows:
        groups.setdefault(row[party + "_account"], []).append(row)
    interval = trace_group_evidence.prepare(case_id, account, [*anchors, *rows], strict_source_clock=product)
    items = []
    for counter, entries in groups.items():
        incoming, outgoing = (entries, anchors) if role == "source" else (anchors, entries)
        details = trace_group_evidence.evidence(account, incoming, outgoing, interval, retain_scope=product)
        if calculated_target_ids is not None and details["conditional_group_share"] is not None:
            calculated_target_ids.update(row["id"] for row in entries)
        row = entries[0]
        items.append({"account": counter, "display_account": row.get(party + "_display_account"),
                      "name": row.get(party + "_name") or "", "ids": [str(entry["id"]) for entry in entries],
                      "anchor_ids": [str(entry["id"]) for entry in anchors],
                      "score": details["score"], "cnt": len(entries), "currency": details["currency"],
                      "total": details["observed_in" if role == "source" else "observed_out"],
                      "evidence": details})
    ranked = sorted(items, key=lambda item: -(item["score"] or 0))[:10]
    _evidence_locations(case_id, [item["evidence"] for item in ranked])
    return ranked


def _public_clues(suggestions):
    # The full interval proof stays with the case-bound review on the server.
    return {key: [{**item, "evidence": {name: value for name, value in item["evidence"].items()
                                       if name != "_range_record_ids"}} for item in items]
            for key, items in suggestions.items()}


def _add_recorded_destination(case_id, selections, rows, roles, *, source_cache=None):
    from . import trial_pair_comparison

    if any(not row.get("source_own_card") or row.get("source_ledger") for row in selections):
        return None
    destination = trial_pair_comparison.recorded_destination(
        case_id, {row["id"] for row in selections}, source_cache=source_cache)
    if destination is None:
        return None
    target = int(destination["debit"]["id"])
    cited = {target, *(int(bridge[key]) for bridge in destination["bridges"] for key in ("payer_id", "receiver_id")),
             *(int(item["id"]) for item in destination["other_credits"])}
    needed = cited - rows.keys()
    originals = db.q("""select * from effective_visible_flows
        where case_id=? and id in (select unnest(?::bigint[]))""", [case_id, sorted(needed)]) if needed else []
    if {row["id"] for row in originals} != needed:
        return None
    rows.update((row["id"], row) for row in originals)
    rows[target]["context_relation"] = "after"
    target_roles = roles.setdefault(target, [])
    if "target" not in target_roles:
        target_roles.append("target")
    return destination


def _reported_same_account_debit(case_id, own, locator, physical, candidates, postings, rows):
    """Read a source-reported debit that the financial ledger cannot orient."""
    position = locator["source_row"]
    since = postings[position][0].replace(microsecond=0)
    debits = [(stamp.replace(microsecond=0), number) for number, (stamp, side) in postings.items()
              if side == "out" and stamp.replace(microsecond=0) >= since]
    if not debits:
        return None
    when, target = min(debits)
    if when == since or sum(stamp == when for stamp, _ in debits) != 1:
        return None
    target_cells = {cell["column"]: cell for cell in physical[target]}
    counterparties = candidates.get("counter_account", ())
    if len(counterparties) != 1:
        return None
    peer_column = counterparties[0]["index"] + 1
    peer = target_cells.get(peer_column)
    account = own["raw_self_account"]
    if (not account or not peer or peer["kind"] != "text" or peer["value"].strip() != account
            or identity_field(table_import._norm(physical[1][peer_column - 1]["value"])) != "counter_account"):
        return None
    names = candidates.get("counter_name", ())
    name = target_cells.get(names[0]["index"] + 1) if len(names) == 1 else None
    other = [number for number, (stamp, side) in postings.items() if number != position and side == "in"
             and since <= stamp.replace(microsecond=0) <= when]
    needed = {position, target, *other}
    active = db.q_raw("""select r.source_row original_position, f.* from import_records r
        join flow_sources s on s.case_id=r.case_id and s.source_id=r.source_id and s.source_row=r.record_no
        join effective_visible_flows f on f.case_id=s.case_id and f.id=s.flow_id
        where r.case_id=? and r.source_id=? and r.member_path=? and r.disposition in ('imported','duplicate')
        and r.source_row in (select unnest(?::bigint[]))""",
        [case_id, locator["source_id"], locator["member_path"], sorted(needed)])
    by_position = {row.pop("original_position"): row for row in active}
    if len(active) != len(needed) or by_position.keys() != needed or by_position[position]["id"] != own["id"]:
        return None
    debit = by_position[target]
    if debit["self_account"] != debit["payer_account"] or debit["self_account"] != debit["payee_account"]:
        return None
    ids = {row["id"]: number for number, row in by_position.items()}
    locations = _locators(case_id, ids, include_ledger=True)
    if len(locations) != len(needed):
        return None
    for entry in locations:
        number = ids[entry["flow_id"]]
        row = by_position[number]
        if (entry["source_id"] != locator["source_id"] or entry["member_path"] != locator["member_path"]
                or entry["source_row"] != number or entry["header_scope"] != "1"
                or row["raw_self_account"] != account or row["currency"] != own["currency"]
                or transaction_time.basis(row) != "unqualified" or transaction_time.clock(row) != postings[number][0]):
            return None
        try:
            book = source_ledger.validate(entry["ledger_evidence"])
        except ValueError:
            return None
        if (book["kind"] != "financial" or book["direction"] != postings[number][1]
                or book["source_currency"] != own["currency"] or book["row"]["self_account"] != account):
            return None

    upper = None
    amounts = candidates.get("amount", ())
    if len(amounts) == 1:
        [amount] = amounts
        column = amount["index"] + 1
        selected = []
        for number in (position, target):
            cells = {cell["column"]: cell for cell in physical[number]}
            cell = cells.get(column)
            if not cell or cell["kind"] not in {"text", "number"}:
                break
            value, issue = table_import._money(cell["value"], amount)
            row = by_position[number]
            if issue or value != row["amount"] or (units := trace_group_evidence._positive_units(value)) is None:
                break
            stamp, side = postings[number]
            selected.append({"flow_id": row["id"], "source_row": number, "clock": stamp,
                             "direction": side, "units": units})
        if len(selected) == 2:
            bound = trace_group_evidence.selected_order_upper_bound(selected, own["currency"])
            upper = {"amount": bound["joint_upper"], "currency": bound["currency"], "amount_column": column,
                     "header_declares_unit": amount["unit"] != "元/默认基础单位"}

    for number, row in by_position.items():
        if row["id"] not in rows:
            relation = "same_time" if postings[number][0] == postings[position][0] else "after"
            rows[row["id"]] = {**db.clean(row), "context_relation": relation}
    cited = {number: {"id": str(row["id"]), "source_row": number} for number, row in by_position.items()}
    return {"anchor": cited[position], "debit": {**cited[target],
            "counterparty_account": {"column": peer_column, "value": peer["value"]},
            "counterparty_name": ({"column": name["column"], "value": name["value"]}
                                  if name and name["kind"] == "text" and name["value"].strip() else None)},
            "other_credits": [cited[number] for number in other], "conditional_upper": upper, "actual_share": None}


def _recorded_destination_in_source(case_id, own, rows, *, allow_absence):
    """Use a bounded active table for a source-level destination, independently of fund attribution."""
    if transaction_time.basis(own) != "unqualified":
        return {}
    locators = _locators(case_id, [own["id"]], include_ledger=True)
    if len(locators) != 1:
        return {}
    [locator] = locators
    workbook = locator["suffix"] == ".xlsx"
    if workbook:
        prefix, _, index = locator["member_path"].rpartition("/")
        if (prefix != "worksheets" or len(index) != 4 or not index.isascii() or not index.isdecimal()
                or not 1 <= int(index) <= table_import.MAX_SHEETS or not locator["sheet"]
                or table_import.xlsx_member_digest(locator["sha256"], int(index), locator["sheet"]) != locator["member_sha256"]):
            return {}
    elif (not allow_absence or locator["suffix"] != ".csv" or locator["member_path"] != "table.csv"
          or locator["member_sha256"] != locator["sha256"]):
        return {}
    document, _ = _original_layout(case_id, locator)
    last = document.get("source_rows")
    position = locator["source_row"]
    if (document.get("header_rows") != [1] or type(last) is not int
            or not 1 < position <= last <= import_layout_trial._MAX_SOURCE_ROWS
            or workbook and (document.get("sheet") != locator["sheet"] or document.get("mode") != "B")):
        return {}
    positions = set(range(1, last + 1))
    try:
        source = imports._registered_original(case_id, locator["source_id"])
        if (source["active_batch_id"] is None or source["sha256"] != locator["sha256"]
                or source["size_bytes"] > import_layout_trial._MAX_FILE_BYTES):
            return {}
        physical = _original_cells(imports._source(source, snapshot=True), locator, document, (), positions,
                                   for_layout=True)
    except ServiceError as error:
        if error.code not in {"import_not_found", "source_changed", "source_missing", "unsafe_upload_storage"}:
            raise
        return {}
    if physical.keys() != positions:
        return {}
    profile = locator["parser_version"]
    from . import import_verbatim_money, trial_pair_comparison

    if import_verbatim_money.has_later_header({number: physical[number] for number in range(2, last + 1)}, profile):
        return {}
    header = [cell["value"] for cell in physical[1]]
    headings = [table_import._norm(value) for value in header]
    if any(value in trace_group_evidence._CLOCK_COLUMNS for value in headings):
        return {}
    candidates = table_import._candidates(header, profile)
    fields = ("self_account", "currency", "direction", "trade_time")
    if any(len(candidates.get(field, ())) != 1 for field in fields) or candidates.get("time_part"):
        return {}
    columns = [candidates[field][0]["index"] for field in fields]
    if len(set(columns)) != len(fields):
        return {}
    if workbook and identity_field(headings[columns[0]]) != "self_account":
        return {}
    clock_name = table_import._header_parts(header[columns[-1]], profile)[0]
    if table_import._source_time_role(clock_name) != "transaction":
        return {}
    account = own.get("raw_self_account") or own.get("self_account")
    currency = own.get("currency")
    anchor_clock = transaction_time.clock(own)
    subledger_columns = [i for i, heading in enumerate(headings)
                         if workbook and heading in {"子账户序号", "子账户账号", "子账户号", "子账号"}]
    anchor_subledger = tuple(physical[position][i]["value"].strip() for i in subledger_columns
                             if i < len(physical[position]))
    account_digits = trial_pair_comparison._ACCOUNT_DIGITS
    postings = {}
    for number in range(2, last + 1):
        values = [cell["value"].strip() for cell in physical[number]]
        if not any(values):
            continue
        has_extra_values = len(values) > len(header) and (not workbook or any(values[len(header):]))
        if max(columns) >= len(values) or has_extra_values or not values[columns[0]]:
            return {}
        row_account, raw_currency, direction, raw_clock = (values[index] for index in columns)
        if workbook and (physical[number][columns[0]]["kind"] != "text"
                         or not account_digits.fullmatch(row_account) or not row_account.strip("0")):
            return {}
        if row_account != account:
            continue
        row_currency = table_import._CURRENCY.get(raw_currency, raw_currency.upper())
        if row_currency not in table_import._ISO_CURRENCIES:
            return {}
        if row_currency != currency:
            continue
        if subledger_columns:
            subledger = tuple(values[i] for i in subledger_columns if i < len(values))
            if (len(subledger) != len(subledger_columns) or subledger != anchor_subledger
                    or any(physical[number][i]["kind"] != "text" or not account_digits.fullmatch(values[i])
                           or not values[i].strip("0") for i in subledger_columns)):
                return {}
        side = table_import._enum(direction)
        stamp = table_import._time(raw_clock)
        if (stamp is None or not table_import._TRADE_DATED_SECONDS.search(raw_clock)
                or side not in table_import._IN | table_import._OUT):
            return {}
        postings[number] = stamp, "in" if side in table_import._IN else "out"
    if postings.get(position) != (anchor_clock, "in"):
        return {}
    scope = {"member_path": locator["member_path"], "first_row": 2, "last_row": last,
             "matching_rows": len(postings), "anchor_row": position, "clock_column": columns[-1] + 1}
    if workbook:
        reported = _reported_same_account_debit(case_id, own, locator, physical, candidates, postings, rows)
        if reported:
            return {"reported_destination": {**scope, **reported}}
    if not allow_absence:
        return {}
    # A debit within the anchor's reported second may have finer timing
    # than the source states. This card needs a strict earlier boundary.
    same_or_later = {number: side for number, (stamp, side) in postings.items()
                     if stamp.replace(microsecond=0) >= anchor_clock.replace(microsecond=0)}
    if "out" in same_or_later.values():
        return {}
    # The workbook journal can carry a conflict the literal direction cell
    # alone cannot settle, for example a populated opposing amount column.
    if workbook and db.q_raw("""select 1 from import_records
        where case_id=? and source_id=? and member_path=? and disposition='unresolved'
        and source_row in (select unnest(?::bigint[]))
        limit 1""", [case_id, locator["source_id"], locator["member_path"], list(same_or_later)]):
        return {}
    return {"recorded_destination_absence": scope}


def read(case_id, canvas_id, anchor_id, count, first, context_reader, *, overview_reference=False, product=False):
    anchor = first["anchor"]
    by_side = {first["side"]: first}
    for side in ("payer", "payee"):
        if side not in by_side and anchor.get(side + "_account"):
            by_side[side] = context_reader(anchor_id, count, side, case_id, for_review=True)
    rows, roles = {anchor_id: anchor}, {}
    suggestions = {"sources": [], "targets": []}
    principal = {}
    recorded_scope = {}
    limited_principal_targets = ()
    reasons = set()
    for side, view in by_side.items():
        role = "source" if side == "payer" else "target"
        relation = "before" if role == "source" else "after"
        direction = "in" if role == "source" else "out"
        reasons.update(view["ordering"]["reasons"])
        for row in view["rows"]:
            rows.setdefault(row["id"], row)
        own = view["_own_entry"]
        account = view["account"]
        if not own or anchor.get("source_ledger"):
            principal[role] = {"status": "unknown", "reasons": ["own_ledger_anchor_missing"]}
            continue
        neighbors = ledger_navigation.neighbors(case_id, account, own, role, count, own_entry=own)
        reasons.update(neighbors["reasons"])
        matching = []
        for row in neighbors["rows"]:
            row.update(dir=direction, context_relation=relation)
            matching.append(row)
            # Preserve posted raw/derived fields, attaching the proved relation
            # for this side rather than the other ledger's nearby timestamp.
            rows.setdefault(row["id"], row)["context_relation"] = relation
            roles.setdefault(row["id"], []).append(role)
        calculated_targets = None
        if not overview_reference:
            result = principal_evidence.tracking_result(case_id, account, own, matching, role, own_entry=own)
            principal[role] = result
            if product and role == "target" and result["status"] == "limited" and result.get("targets") == []:
                calculated_targets = set()
        suggestions[role + "s"] = _clues(case_id, account, [own], matching, role, product=product,
                                         calculated_target_ids=calculated_targets)
        if product and not overview_reference and role == "target" and own["id"] == anchor_id:
            recorded_scope = _recorded_destination_in_source(
                case_id, own, rows, allow_absence=not matching and neighbors["status"] == "ok")
        if calculated_targets is not None:
            limited_principal_targets = {row_id for row_id, row_roles in roles.items()
                                         if row_id != anchor_id and "target" in row_roles} - calculated_targets

    if overview_reference:
        return suggestions

    journals = {}
    pair = pair_review(case_id, anchor, product=product, source_cache=journals)
    additional = [int(key) for key in pair["candidate_ids"] if int(key) not in rows]
    if additional:
        neighbors = db.q("""select * from effective_visible_flows
            where case_id=? and id in (select unnest(?::bigint[])) order by id""", [case_id, additional])
        source_ledger.decorate(case_id, neighbors)
        for row in neighbors:
            row["context_relation"] = "same_transaction" if pair["status"] == "confirmed" else "pair_candidate"
            rows[row["id"]] = row
    for key in pair["candidate_ids"]:
        candidate = rows.get(int(key))
        if candidate is not None and pair["status"] == "confirmed" and candidate["id"] != anchor_id:
            candidate.update(event_state="confirmed", event_id=anchor.get("event_id"),
                             context_group=anchor.get("context_group"), context_relation="same_transaction")

    accounts = [anchor[key] for key in ("payer_account", "payee_account") if anchor.get(key)]
    undated = db.q("""with accounts as (select unnest(?::varchar[]) account)
        select * from effective_visible_flows where case_id=? and trade_time is null and trade_time_utc is null
        and (payer_account in (select account from accounts) or payee_account in (select account from accounts)
             or self_account in (select account from accounts)) order by id limit ?""",
        [accounts, case_id, 2 * count + 1])
    if len(undated) > 2 * count:
        reasons.add("undated_context_limit")
    else:
        source_ledger.decorate(case_id, undated)
        for row in undated:
            row["context_relation"] = "unordered"
            rows.setdefault(row["id"], row)

    destination = (_add_recorded_destination(case_id, [anchor], rows, roles, source_cache=journals)
                   if product and pair.get("ledger_correspondence") else None)
    _decorate_rows(case_id, rows, roles)
    anchor["context_relation"] = "anchor"

    payer, payee = anchor.get("payer_account"), anchor.get("payee_account")
    selected = {str(anchor_id), *(pair["candidate_ids"] if pair["status"] == "confirmed" else [])}
    forward = [row for row in rows.values() if not row.get("source_ledger") and str(row["id"]) in selected]
    reverse = []
    directional_funds = _lines(case_id, forward, reverse)

    cards = _cards(rows, payer, payee)
    token = _issue(case_id, canvas_id, anchor_id, first["analysis_revision"], rows, forward, reverse,
                   trace_cards=suggestions, limited_principal_targets=limited_principal_targets)
    return {**{key: value for key, value in first.items() if key != "_own_entry"},
            "anchor": {**anchor, "id": str(anchor_id)},
            "rows": _wire_rows(rows),
            "ordering": {"status": "partial" if reasons else "ok", "reasons": sorted(reasons)},
            "suggestions": _public_clues(suggestions),
            "review": {"token": token, "directional_funds": directional_funds, "pair": pair, "principal": principal,
                       "cards": cards, **({"recorded_destination": destination} if destination else {}),
                       **recorded_scope}}


def _edge_members(case_id, canvas_id, left, right, ids):
    rows = db.q("""select f.* from effective_visible_flows f join tracked_flows t
        on t.case_id=f.case_id and t.flow_id=f.id
        where f.case_id=? and t.canvas_id=? and f.payer_account=? and f.payee_account=?
        and f.id in (select unnest(?::bigint[])) order by f.id""", [case_id, canvas_id, left, right, ids])
    if len(rows) != len(set(ids)):
        raise HTTPException(409, "所选关系已变更，请刷新图谱后重新打开核对")
    source_ledger.decorate(case_id, rows)
    return rows


def _edge_pair_badges(case_id, rows):
    members = {row["flow_id"]: row for row in event_resolution.members(case_id, rows)}
    observed = {}
    for row in rows.values():
        member = members.get(str(row["id"]), {})
        if member.get("status") == "conflict":
            row["pair_badge"] = "conflict"
        if row.get("source_ledger") or member.get("status") == "confirmed" or not transaction_time.clock(row):
            continue
        key = tuple(row.get(field) for field in ("payer_account", "payee_account", "currency", "amount"))
        observed.setdefault((*key, transaction_time.basis(row), transaction_time.clock(row)), []).append(row)
    for group in observed.values():
        endpoint = {group[0].get("payer_account"), group[0].get("payee_account")}
        if None not in endpoint and len(endpoint) == 2 and {row.get("self_account") for row in group} >= endpoint:
            for row in group:
                row.setdefault("pair_badge", "pending")


def _aggregate_edge_clues(case_id, account, observations, event_count, role, visible, *, product=False):
    direction = "out" if role == "source" else "in"
    own = {}
    for observation in observations:
        row = (observation if observation.get("self_account") == account
               else ledger_navigation.own_anchor(case_id, account, observation))
        if not row or trace_group_evidence._direction(row, account) != direction:
            return []
        own[row["id"]] = row
    anchors = list(own.values())
    if (len(anchors) != event_count or any(transaction_time.clock(row) is None for row in anchors)
            or len({transaction_time.basis(row) for row in anchors}) != 1
            or len({row["currency"] for row in anchors}) != 1):
        return []
    # Overview clues are limited to original postings strictly before the
    # first selected debit or strictly after the last selected credit.
    before = role == "source"
    boundary = (min if before else max)(anchors, key=transaction_time.clock)
    clock = transaction_time.clock(boundary)
    neighbors = ledger_navigation.neighbors(case_id, account, boundary, role, 50, own_entry=boundary)
    rows = [row for row in neighbors["rows"] if row["id"] in visible and
            (transaction_time.clock(row) < clock if before else transaction_time.clock(row) > clock)]
    return _clues(case_id, account, anchors, rows, role, product=product)


def edge(body, context_reader, *, product=False):
    """Read an unanchored view with amounts from the selected canvas edge IDs."""
    case_id, canvas_id, left, right = body.case_id, body.canvas_id, body.source, body.target
    revision = analysis_snapshot.revision(case_id)
    if revision != body.analysis_revision:
        raise HTTPException(409, "图谱依据已更新，请刷新后核对")
    membership = canvas_membership(case_id, canvas_id)
    if membership != body.membership_digest:
        raise HTTPException(409, "图谱上的关系成员已更新，请刷新后核对")
    forward = _edge_members(case_id, canvas_id, left, right, body.forward_ids)
    reverse = _edge_members(case_id, canvas_id, right, left, body.reverse_ids) if body.reverse_ids else []
    rows = {row["id"]: row for row in (*forward, *reverse)}
    edge_ids = tuple(rows)
    related = db.q("""select * from effective_visible_flows where case_id=?
        and (payer_account in (?,?) or payee_account in (?,?) or self_account in (?,?))
        and id not in (select unnest(?::bigint[]))
        order by trade_time nulls last,id limit ?""",
        [case_id, left, right, left, right, left, right, list(edge_ids), _EDGE_RELATED_LIMIT + 1])
    limited = len(related) > _EDGE_RELATED_LIMIT
    visible = related[:_EDGE_RELATED_LIMIT]
    source_ledger.decorate(case_id, visible)
    rows.update((row["id"], row) for row in visible)
    roles = {}
    destination = _add_recorded_destination(case_id, forward, rows, roles) if product else None
    _decorate_rows(case_id, rows, roles)
    _edge_pair_badges(case_id, rows)
    front_money = [row for row in forward if not row.get("source_ledger")]
    back_money = [row for row in reverse if not row.get("source_ledger")]
    directional_funds = _lines(case_id, front_money, back_money)

    reference = None
    suggestions = {"sources": [], "targets": []}
    primary = directional_funds["forward"]
    count = primary["count"]
    if not limited and primary["status"] == "exact" and primary["count"] == {"lower": 1, "upper": 1}:
        seed = forward[0]
        pair = event_resolution.review_pair(case_id, seed)
        if pair["status"] == "confirmed" and set(map(str, body.forward_ids)) <= set(pair["candidate_ids"]):
            reference = {key: seed.get(key) for key in ("trade_time", "amount", "currency")}
            from_case = context_reader(seed["id"], 50, "payer", case_id, for_review=True)
            eligible = set(map(str, rows))
            proposals = read(case_id, canvas_id, seed["id"], 50, from_case, context_reader,
                             overview_reference=True, product=product)
            suggestions = {key: [item for item in items if set(item["ids"]) <= eligible] for key, items in proposals.items()}
    elif (not limited and primary["status"] == "exact" and count["lower"] == count["upper"]
          and 1 < count["lower"] <= 50 and len(forward) <= 100):
        suggestions = {"sources": _aggregate_edge_clues(case_id, left, forward, count["lower"], "source", rows, product=product),
                       "targets": _aggregate_edge_clues(case_id, right, forward, count["lower"], "target", rows, product=product)}

    cards = _cards(rows, left, right)
    token = _issue(case_id, canvas_id, 0, revision, rows, front_money, back_money, membership=membership,
                   trace_cards=suggestions)
    return {"anchor": {**forward[0], "id": None}, "analysis_revision": revision, "rows": _wire_rows(rows),
            "ordering": {"status": "partial" if limited else "ok", "reasons": ["edge_context_limit"] if limited else []},
            "suggestions": _public_clues(suggestions), "review": {"token": token, "kind": "edge", "reference": reference, "cards": cards,
                                                   "directional_funds": directional_funds,
                                                   **({"recorded_destination": destination} if destination else {})}}


def _original_layout(case_id, locator):
    receipt = db.q_raw("select result_json,request_json from import_sessions where case_id=? and import_id=?",
                       [case_id, locator["source_id"]])
    summary = _review_table_summary(receipt[0]) if receipt else {}
    member = locator["member_path"]
    document = next((item for item in (summary.get("documents") or []) if item["member_path"] == member), {})
    if not document and locator["parser_version"] in feedback_import.SUPPORTED_PROFILES:
        document = {"header_row": 1}
    evidence = [item for item in (summary.get("mapping_evidence") or []) if item.get("member_path") == member]
    return document, evidence


def _original_cells(stream, locator, document, evidence, positions, *, for_layout=False):
    source_roles = [entry for entry in evidence
                    if not (entry.get("field") in ACCOUNT_CARD_FIELDS and entry.get("basis") == "manual_override")]
    try:
        with stream:
            return import_originals.read_cells(stream, locator["suffix"], locator, document, source_roles,
                                               positions, locator["parser_version"], for_layout=for_layout)
    except (OSError, ValueError, KeyError, IndexError, StopIteration, csv.Error,
            zipfile.BadZipFile, import_originals.ET.ParseError):
        return {}


def _citation_scope(locator):
    row = locator["header_scope"] or ""
    if not row.isdecimal() or not 0 < int(row) < locator["source_row"]:
        return None
    return {**{key: locator[key] for key in ("source_id", "sha256", "parser_version", "member_path", "source_row")},
            "header_row": int(row)}


def _counterparty_region(locator, evidence):
    # A document can contain both layouts. Classify only the frozen field
    # roles from this posting's physical table area.
    region = [entry for entry in evidence if entry.get("header_row") == locator["header_scope"]]
    fields = {entry["field"]: entry for entry in region}
    if table_import._shape(fields, locator["parser_version"]) != "B":
        return []
    return [entry for entry in region if entry["field"] in {"counter_account", "counter_card"}]


def _counterparty_heading_kind(entry):
    heading = table_import._norm(entry["column"])
    if entry["basis"] == "verified_region_header" and entry.get("source_role") == "counter_account":
        return "account"
    if entry["basis"] in {"exact_header_alias", "explicit_identity_role", "role_qualified_header"}:
        if heading in table_import._CARD_HEADERS:
            return "card"
        if entry["field"] == "counter_account" and identity_field(heading) == "counter_account":
            return "account"
    return None


def _reviewed_record(case_id, canvas_id, anchor_id, flow_id, token):
    review = _require(token, case_id, canvas_id, anchor_id=anchor_id)
    if flow_id not in review.flow_ids:
        raise HTTPException(404, "核对窗口内不存在该流水")
    current = db.q("select * from effective_visible_flows where case_id=? and id=?", [case_id, flow_id])
    if not current:
        raise HTTPException(404, "核对窗口内不存在该流水")
    return review, current[0]


def original(case_id, canvas_id, anchor_id, flow_id, token, *, product=False):
    with db._lock:
        review, current = _reviewed_record(case_id, canvas_id, anchor_id, flow_id, token)
        pair = pair_review(case_id, current, product=product)
        visible_ids = [key for key in pair["candidate_ids"] if int(key) in review.flow_ids]
        pair = {**pair, "more": pair["more"] or len(visible_ids) < len(pair["candidate_ids"]),
                "candidate_ids": visible_ids}
        if "voucher_candidate_ids" in pair:
            pair["voucher_candidate_ids"] = [key for key in pair["voucher_candidate_ids"] if int(key) in review.flow_ids]
        if "source_card_clock_gaps" in pair:
            pair["source_card_clock_gaps"] = {key: gap for key, gap in pair["source_card_clock_gaps"].items()
                                             if key in visible_ids}
        if pair.get("ledger_correspondence") and pair["ledger_correspondence"]["peer_id"] not in visible_ids:
            pair.pop("ledger_correspondence")
        if not visible_ids:
            pair.pop("account_pair_lead", None)
        locators = _locators(case_id, [flow_id])
        if not locators:
            return {"pair": pair, "originals": [], "message": "这条流水没有已登记的原行位置。"}
        locator = locators[0]
        source_id, position = locator["source_id"], locator["source_row"]
        document, evidence = _original_layout(case_id, locator)
        display = {key: locator[key] for key in ("filename", "member_path", "sheet", "source_row")}
        unavailable = {"pair": pair, "originals": [{**display, "cells": [], "download_url": None}],
                       "message": "原行位置仍可核对，原件字节当前不可用。"}
        if not locator["suffix"]:
            return unavailable
        try:
            stream, _ = imports.get_source_file(case_id, source_id)
        except ServiceError as error:
            if error.status not in (404, 409):
                raise
            return unavailable
    download_url = f"/api/v1/cases/{case_id}/sources/{source_id}/file"
    cells = _original_cells(stream, locator, document, evidence, {position}).get(position, [])
    message = "" if cells else "本次无法读取原始单元格，可另存原件核对。"
    if cells and (lead := pair.get("account_pair_lead")):
        witnesses = "，".join(f"第 {entry['source_row']} 行可用相邻第 "
                            f"{'、'.join(map(str, entry['balance_witness_rows']))} 行核算"
                            for entry in lead["observations"])
        message = (f"本方账号第 {lead['own_account_column']} 列与对方账卡第 "
                   f"{lead['counterparty_identifier_column']} 列按账号互指。{witnesses}。尚缺能关联两条原行的对应凭据。")
        if location := lead.get("registration_context"):
            for match in location["matches"]:
                message += (f"原第 {match['posting_source_row']} 行的账号与币种可查对开户资料第 {match['source_row']} 行"
                            f"第 {match['account_column']}、{match['currency_column']} 列。")
                if match["card_equals_account"]:
                    message += (f"开户资料第 {match['card_column']} 列卡号和第 {match['account_column']} 列账号同值。"
                                "各来源仍按各自原格读取。")
                if not match["posting_card_present"]:
                    message += "该流水的本方卡号原格为空。"
            if location.get("different_name") and location.get("different_document"):
                message += "查到的两条开户登记姓名和证件号各不相同。"
            message += "开户资料仅供回查登记角色。适用时点、实际控制、该笔交易对应及结算仍待核。"
    if cells and pair["reason"] == _CARD_MIRROR_REASON:
        linked = bool(pair.get("ledger_correspondence", {}).get("origins"))
        message = ("本案两侧原件的卡号互指、原报方向相反且币额相同。本次按一笔账面往来核算，结算效力仍待核。"
                   if linked else "两侧同币同额，本方卡号与对方账卡号的原文双向相符。各自时刻可回查，仍缺可区分的同笔凭据。")
        if window := pair.get("source_card_clock_window_seconds"):
            gaps = pair.get("source_card_clock_gaps", {})
            extra = f"本次按双方本方卡号召回本案活动来源中原报时刻差 {window} 秒以内的候选，两侧原件记载的收付方向相反。"
            if pair["more"]:
                extra += "本次候选范围未查全。"
            if len(gaps) == 1:
                seconds = f"{next(iter(gaps.values())):.6f}".rstrip("0").rstrip(".")
                extra += (f"两侧原报时刻相差 {seconds} 秒。"
                          if linked else f"原报时刻相差 {seconds} 秒，两侧时钟可比性仍需核对。")
            message = f"{message} {extra}"
    region = _counterparty_region(locator, evidence)
    can_verify = bool(cells) and any(_counterparty_heading_kind(entry) for entry in region)
    with db._lock:
        review = _require(token, case_id, canvas_id, anchor_id=anchor_id)
        if product and cells and flow_id in review.limited_principal_targets:
            finding = "该原行可以回查已记录的往来。先后顺序不足以确定本金归属，本次未核定资金份额。"
            message = f"{message} {finding}" if message else finding
    return {"pair": pair, "originals": [{**display, "cells": cells, "download_url": download_url,
            "citation_scope": _citation_scope(locator) if can_verify else None}], "message": message}


def _counterparty_column_kind(entry, raw_rows, header_row, cell):
    if (entry.get("candidate_count") != "1" or cell["header"] != entry.get("column")
            or cell["kind"] != "text"):
        return None
    kind = _counterparty_heading_kind(entry)
    if entry["basis"] == "verified_region_header":
        parent_row = int(entry["parent_row"])
        parent, leaf = raw_rows.get(parent_row, []), raw_rows.get(header_row, [])
        proof = table_import.table_tiered_headers.inspect(
            parent, leaf, parent_row, lambda value: value["value"] if value["kind"] == "text" else "",
        )
        if not proof or proof["status"] != "verified" or not any(
            index == cell["column"] and role == "counter_account" for index, _, _, role, _ in proof["columns"]
        ):
            return None
    return kind


def _counterparty_column_verdict(raw_rows, locator, row, region, candidate, citation):
    if not raw_rows:
        return "insufficient", "original_unavailable"
    ledger = json.loads(locator["ledger_evidence"] or "{}")
    direction = ledger.get("direction")
    if not region or ledger.get("kind") != "financial" or direction not in {"in", "out"}:
        return "insufficient", "counterparty_role_unproven"
    counter = "payer_account" if direction == "in" else "payee_account"
    observed = ledger["row"]
    identifier = observed[counter]
    if not identifier or (observed["self_account"], identifier) != (row["raw_self_account"], row["raw_" + counter]):
        return "insufficient", "counterparty_role_unproven"
    cells = {cell["column"]: cell for cell in raw_rows[locator["source_row"]]}
    if not any(int(entry["column_index"]) == citation["column"] for entry in region):
        return "refuted", "counterparty_column_mismatch"
    columns = {}
    for entry in region:
        column = int(entry["column_index"])
        cell = cells.get(column)
        kind = None if cell is None else _counterparty_column_kind(entry, raw_rows, citation["header_row"], cell)
        if not kind or kind in columns:
            return "insufficient", "counterparty_column_not_unique"
        columns[kind] = (column, table_import._text(cell["value"]))
    # In the admitted source row, an explicit account takes precedence. A
    # card can supply the endpoint only when the explicit account is blank.
    for kind in ("account", "card"):
        column, value = columns.get(kind, (None, ""))
        if value:
            if value != identifier:
                return "insufficient", "counterparty_role_unproven"
            if (candidate, citation["column"]) != (kind, column):
                return "refuted", "counterparty_column_mismatch"
            return "supported", "registered_counterparty_column"
    return "insufficient", "counterparty_role_unproven"


def verify_counterparty_column(case_id, canvas_id, anchor_id, flow_id, token, revision, candidate, citation):
    """Admit a field-only historical receipt against the current locked binding."""
    def outcome(status, reason):
        return {"proposition": "counterparty_identifier_column", "classification": "rule_control",
                "analysis_revision": revision, "business_status": "pending", "evidence_status": status,
                "reason": reason, "flow_id": str(flow_id), "candidate": candidate, "citation": citation}

    with db._lock:
        review, row = _reviewed_record(case_id, canvas_id, anchor_id, flow_id, token)
        if review.revision != revision:
            raise HTTPException(409, "流水来源或关联已更新，请重新打开核对")
        locators = _locators(case_id, [flow_id], include_ledger=True)
        locator = locators[0] if locators else None
        scope = _citation_scope(locator) if locator else None
        if not scope:
            return outcome("insufficient", "counterparty_role_unproven")
        if any(citation[key] != value for key, value in scope.items()):
            return outcome("refuted", "citation_scope_mismatch")
        if locator["disposition"] not in {"imported", "duplicate"} or not locator["suffix"]:
            return outcome("insufficient", "original_unavailable")
        source, member = scope["source_id"], scope["member_path"]
        import_report._scope(case_id, source, member, revision)
        document, evidence = _original_layout(case_id, locator)
        region = _counterparty_region(locator, evidence)
        positions = {locator["source_row"], *(int(entry["parent_row"]) for entry in region if "parent_row" in entry)}
        if len(positions) > 1:
            positions.add(scope["header_row"])
        session = imports._source_session(case_id, source)

    # The reopened bytes must match the registered SHA; decode that independent
    # snapshot outside the case writer lock.
    try:
        stream = imports._source(session, snapshot=True)
    except ServiceError as error:
        if error.status != 409:
            raise
        status, reason = "insufficient", "original_unavailable"
    else:
        raw_rows = _original_cells(stream, locator, document, evidence, positions)
        status, reason = _counterparty_column_verdict(raw_rows, locator, row, region, candidate, citation)
    with db._lock:
        _require(token, case_id, canvas_id, anchor_id=anchor_id)
        _, registered, _, _ = import_report._scope(case_id, source, member, revision)
        if (registered["sha256"], registered["parser_version"]) != (scope["sha256"], scope["parser_version"]):
            raise HTTPException(409, "原件依据已更新，请重新打开核对")
        if not imports.source_is_available(case_id, source):
            return outcome("insufficient", "original_unavailable")
        return outcome(status, reason)
