"""Case-bound review of postings, candidate provenance and manual selection."""
from dataclasses import dataclass
from decimal import Decimal
import csv
import json
from time import monotonic
from uuid import uuid4
import zipfile

from fastapi import HTTPException

from .. import analysis_snapshot, db, event_resolution, feedback_import, funds_projection
from .. import import_originals, ledger_navigation, principal_evidence, source_ledger, transaction_time
from . import imports
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


# All callers that issue, use or revoke a review hold the existing DB lock.
_reviews: dict[str, Review] = {}
_MAX_OPEN = 128
_VALID_FOR_SECONDS = 900
_EDGE_RELATED_LIMIT = 500


def _membership(case_id, canvas_id):
    return db.q_raw("""select sha256(coalesce(string_agg(flow_id::varchar,',' order by flow_id),''))
        as membership_digest from tracked_flows where case_id=? and canvas_id=?""",
        [case_id, canvas_id])[0]["membership_digest"]


def _issue(case_id, canvas_id, anchor_id, revision, rows, forward, reverse, *, from_edge=False):
    while len(_reviews) >= _MAX_OPEN:
        del _reviews[next(iter(_reviews))]
    token = uuid4().hex
    _reviews[token] = Review(case_id, canvas_id, anchor_id, revision, frozenset(rows),
                             monotonic() + _VALID_FOR_SECONDS, _membership(case_id, canvas_id) if from_edge else None,
                             tuple(frozenset(row["id"] for row in direction) for direction in (forward, reverse)))
    return token


def _require(token, case_id, canvas_id, *, anchor_id=None):
    review = _reviews.get(token)
    if (review is None or review.expires <= monotonic()
            or (review.case_id, review.canvas_id) != (case_id, canvas_id)
            or anchor_id is not None and review.anchor_id != anchor_id):
        raise HTTPException(409, "核对窗口已失效，请重新打开核对")
    if analysis_snapshot.revision(case_id) != review.revision:
        raise HTTPException(409, "流水来源或关联已更新，请重新打开核对")
    if review.canvas_membership is not None and _membership(case_id, canvas_id) != review.canvas_membership:
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


def _locators(case_id, flow_ids):
    return db.q_raw("""select fs.flow_id,s.source_id,s.filename,s.parser_version,r.member_path,r.source_row,
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


def _recorded_counter_columns(case_id, locators):
    source_ids = {entry["source_id"] for entry in locators.values()
                  if not entry["identity_evidence"] and not entry["native_pair_contract"] and entry["header_scope"]}
    if not source_ids:
        return
    evidence = {}
    card_regions = set()
    for receipt in db.q_raw("""select import_id,result_json from import_sessions
            where case_id=? and import_id in (select unnest(?::varchar[]))""", [case_id, list(source_ids)]):
        summary = json.loads(receipt["result_json"] or "{}").get("table_summary") or {}
        for field in summary.get("mapping_evidence") or []:
            key = (receipt["import_id"], field.get("member_path"), str(field.get("header_row")))
            if field.get("field") == "counter_card":
                card_regions.add(key)
            elif (field.get("field") == "counter_account" and field.get("basis") == "exact_header_alias"
                  and str(field.get("candidate_count")) == "1"):
                evidence[key] = field.get("column")
    for entry in locators.values():
        key = (entry["source_id"], entry["member_path"], entry["header_scope"])
        # An empty account cell may have been replaced by a card in the
        # financial endpoint. Column metadata cannot establish this row's type.
        if key not in card_regions:
            entry["counter_column"] = evidence.get(key)


def _outline(row, locator):
    own = row.get("self_account")
    owner = "payer" if own and own == row.get("payer_account") else "payee" if own and own == row.get("payee_account") else None
    counter = {"payer": "payee", "payee": "payer"}.get(owner)
    ledger = row.get("source_ledger") or {}
    identity = json.loads(locator.get("identity_evidence") or "{}").get("endpoints", {})
    own_fields = identity.get("self", {})
    counter_fields = identity.get(counter, {}) if counter else identity.get("counter", {})
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
    return {
        "card": card,
        "account": account,
        "name": row.get("self_name") or (row.get(owner + "_name") if owner else None),
        "direction": locator.get("direction") or ledger.get("source_direction"),
        "counter_card": counter_card,
        "counter_account": counter_account,
        "counter_kind": counter_fields.get("identifier_type"),
        "counter_column": label,
        "counter_name": (row.get(counter + "_name") if counter else ledger.get("counter_name")),
        "remark": next(iter(remarks)) if len(remarks) == 1 else None,
    }


def _clocked(row):
    return {**row, "trade_time": transaction_time.text(transaction_time.clock(row))}


def _decorate_rows(case_id, rows, roles):
    locators = {entry["flow_id"]: entry for entry in _locators(case_id, rows)}
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
    return funds, {"times": [stamps[0], stamps[-1]] if stamps and comparable else [],
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
    out, out_details = _line_evidence(case_id, forward)
    incoming, in_details = _line_evidence(case_id, reverse)
    return {"forward": out, "reverse": incoming,
            "annotations": {"forward": out_details, "reverse": in_details},
            "totals": _line_totals(out, incoming)}


def _basis(observed, role):
    stamps = list(dict.fromkeys(transaction_time.text(row["trade_time"]) for row in observed))
    # The same day can be read in the table. Keep explicit dates across days.
    if all(stamp[10:11] in {" ", "T"} and stamp[:10] == stamps[0][:10] for stamp in stamps):
        stamps = [stamp[11:16] if len(stamp) == 19 and stamp.endswith(":00") else stamp[11:] for stamp in stamps]
    times = "、".join(stamps) if len(stamps) <= 2 else f"{stamps[0]} 至 {stamps[-1]}"
    if role == "source":
        return f"{times} 的 {len(observed)} 条入账在本方锚点之前，本金来源待核。"
    return f"{times} 的 {len(observed)} 条出账在本方锚点之后，本金去向待核。"


def read(case_id, canvas_id, anchor_id, count, first, context_reader, score, *, overview_reference=False):
    anchor = first["anchor"]
    by_side = {first["side"]: first}
    for side in ("payer", "payee"):
        if side not in by_side and anchor.get(side + "_account"):
            by_side[side] = context_reader(anchor_id, count, side, case_id, for_review=True)
    rows, roles = {anchor_id: anchor}, {}
    suggestions = {"sources": [], "targets": []}
    principal = {}
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
        if not overview_reference:
            principal[role] = principal_evidence.tracking_result(case_id, account, own, matching, role, own_entry=own)
        ranked = score([_clocked(row) for row in matching], _clocked(own), limit=10)[role + "s"]
        for item in ranked:
            ids = item["ids"]
            observed = _ordered([rows[key] for key in ids])
            display = observed[0].get(("payer" if role == "source" else "payee") + "_display_account")
            suggestions[role + "s"].append({**item, "display_account": display, "ids": list(map(str, ids)),
                                               "evidence": {"basis": _basis(observed, role)}})

    if overview_reference:
        return suggestions

    pair = event_resolution.review_pair(case_id, anchor)
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

    _decorate_rows(case_id, rows, roles)
    anchor["context_relation"] = "anchor"

    payer, payee = anchor.get("payer_account"), anchor.get("payee_account")
    selected = {str(anchor_id), *(pair["candidate_ids"] if pair["status"] == "confirmed" else [])}
    forward = [row for row in rows.values() if not row.get("source_ledger") and str(row["id"]) in selected]
    reverse = []
    directional_funds = _lines(case_id, forward, reverse)

    cards = _cards(rows, payer, payee)
    token = _issue(case_id, canvas_id, anchor_id, first["analysis_revision"], rows, forward, reverse)
    return {**{key: value for key, value in first.items() if key != "_own_entry"},
            "anchor": {**anchor, "id": str(anchor_id)},
            "rows": _wire_rows(rows),
            "ordering": {"status": "partial" if reasons else "ok", "reasons": sorted(reasons)},
            "suggestions": suggestions,
            "review": {"token": token, "directional_funds": directional_funds, "pair": pair, "principal": principal,
                       "cards": cards}}


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


def edge(body, context_reader, score):
    """Read an unanchored view with amounts from the selected canvas edge IDs."""
    case_id, canvas_id, left, right = body.case_id, body.canvas_id, body.source, body.target
    revision = analysis_snapshot.revision(case_id)
    if revision != body.analysis_revision:
        raise HTTPException(409, "图谱依据已更新，请刷新后核对")
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
    _decorate_rows(case_id, rows, {})
    _edge_pair_badges(case_id, rows)
    front_money = [row for row in forward if not row.get("source_ledger")]
    back_money = [row for row in reverse if not row.get("source_ledger")]
    directional_funds = _lines(case_id, front_money, back_money)

    reference = None
    suggestions = {"sources": [], "targets": []}
    primary = directional_funds["forward"]
    if not limited and primary["status"] == "exact" and primary["count"] == {"lower": 1, "upper": 1}:
        seed = forward[0]
        pair = event_resolution.review_pair(case_id, seed)
        if pair["status"] == "confirmed" and set(map(str, body.forward_ids)) <= set(pair["candidate_ids"]):
            reference = {key: seed.get(key) for key in ("trade_time", "amount", "currency")}
            from_case = context_reader(seed["id"], 50, "payer", case_id, for_review=True)
            eligible = set(map(str, rows))
            proposals = read(case_id, canvas_id, seed["id"], 50, from_case, context_reader, score, overview_reference=True)
            suggestions = {key: [item for item in items if set(item["ids"]) <= eligible] for key, items in proposals.items()}

    cards = _cards(rows, left, right)
    token = _issue(case_id, canvas_id, 0, revision, rows, front_money, back_money, from_edge=True)
    return {"anchor": {**forward[0], "id": None}, "analysis_revision": revision, "rows": _wire_rows(rows),
            "ordering": {"status": "partial" if limited else "ok", "reasons": ["edge_context_limit"] if limited else []},
            "suggestions": suggestions, "review": {"token": token, "kind": "edge", "reference": reference, "cards": cards,
                                                   "directional_funds": directional_funds}}


def original(case_id, canvas_id, anchor_id, flow_id, token):
    with db._lock:
        review = _require(token, case_id, canvas_id, anchor_id=anchor_id)
        if flow_id not in review.flow_ids:
            raise HTTPException(404, "核对窗口内不存在该流水")
        current = db.q("select * from effective_visible_flows where case_id=? and id=?", [case_id, flow_id])
        if not current:
            raise HTTPException(404, "核对窗口内不存在该流水")
        pair = event_resolution.review_pair(case_id, current[0])
        visible_ids = [key for key in pair["candidate_ids"] if int(key) in review.flow_ids]
        pair = {**pair, "more": pair["more"] or len(visible_ids) < len(pair["candidate_ids"]),
                "candidate_ids": visible_ids}
        locators = _locators(case_id, [flow_id])
        if not locators:
            return {"pair": pair, "originals": [], "message": "这条流水没有已登记的原行位置。"}
        locator = locators[0]
        source_id, position = locator["source_id"], locator["source_row"]
        receipt = db.q_raw("select result_json from import_sessions where case_id=? and import_id=?",
                           [case_id, source_id])
        result = json.loads(receipt[0]["result_json"] or "{}") if receipt else {}
        summary = result.get("table_summary") or {}
        documents = summary.get("documents") or []
        document = next((item for item in documents if item["member_path"] == locator["member_path"]), {})
        if not document and locator["parser_version"] in feedback_import.SUPPORTED_PROFILES:
            document = {"header_row": 1}
        display = {key: locator[key] for key in ("filename", "member_path", "sheet", "source_row")}
        unavailable = {"pair": pair, "originals": [{**display, "cells": [], "download_url": None}],
                       "message": "原行位置仍可核对，原件字节当前不可用。"}
        if not locator["suffix"]:
            return unavailable
        evidence = [item for item in (summary.get("mapping_evidence") or [])
                    if item.get("member_path") == locator["member_path"]]
        try:
            stream, _ = imports.get_source_file(case_id, source_id)
        except ServiceError as error:
            if error.status not in (404, 409):
                raise
            return unavailable
    download_url = f"/api/v1/cases/{case_id}/sources/{source_id}/file"
    try:
        with stream:
            cells = import_originals.read_cells(stream, locator["suffix"], locator, document, evidence,
                                                {position}, locator["parser_version"])[position]
    except (OSError, ValueError, KeyError, IndexError, StopIteration, csv.Error,
            zipfile.BadZipFile, import_originals.ET.ParseError):
        cells, message = [], "本次无法读取原始单元格，可另存原件核对。"
    else:
        message = ""
    with db._lock:
        _require(token, case_id, canvas_id, anchor_id=anchor_id)
    return {"pair": pair, "originals": [{**display, "cells": cells, "download_url": download_url}], "message": message}
