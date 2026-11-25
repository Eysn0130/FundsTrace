"""Verification and publication of a cited correspondence from an active original."""
from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
from itertools import permutations
import json
import re

from .. import analysis_snapshot, db, event_resolution
from . import trace_review, trial_delayed_pair as delayed
from .cases import ServiceError


POLICY = delayed.DECISION_POLICY
_SCOPE_FIELDS = ("issuer", "channel", "business_scope", "reference")
_NOTICE_FIELDS = ("issuer", "channel", "business_scope", "day", "payer", "payee")
_LABEL_PREFIX = dict(zip(_NOTICE_FIELDS, ("I", "C", "B", "D", "P", "Q"), strict=True))
_SIDES = ("payer", "payee")
_MEMBER_FIELDS = ("source_id", "sha256", "member_path", "member_sha256")
_MAX_GROUP_ROWS = 32
_MAX_OPTIONS = 8
_MAX_NOTICE_BYTES = 16 * 1024
_MAX_OFFER_NOTICES = 4
_HEADER = re.compile(r"(?:合成)?独立清算机构 (?P<issuer>[^\r\n]{1,128}) 的跨行对应说明。")
_SCOPE = re.compile(r"本函仅适用于 (?P<issuer>[^/]{1,128}) / (?P<channel>[^/]{1,128}) / (?P<business_scope>[^、]{1,128})、"
                    r"(?P<day>[0-9]{4}-[0-9]{2}-[0-9]{2})、付款账户 (?P<payer>[^ ]{1,128}) 和收款账户 "
                    r"(?P<payee>[^； ]{1,128})；不适用于其他签发机构、通道、业务域、账户或日期。")
_CORRESPONDENCE = re.compile(r"在上列适用范围内，付款账簿局部流水 (?P<payer_reference>[^ ]{1,128}) 对应收款账簿局部流水 "
                             r"(?P<payee_reference>[^， ]{1,128})，二者是(?:同一笔|另一笔) "
                             r"(?P<amount>(?:0|[1-9][0-9]{0,35})\.[0-9]{2}) (?P<currency>[A-Z]{3}) (?P<status>已完成且未冲正|已冲正)支付。")
_CLOCK_DECLARATION = ("本域流水号仅在上述签发机构及业务域内解释。共享参考号可被不同支付复用，不能单靠相等参考号认定同笔。"
                      "付款与入账记录的时刻分别为发起和入账，两个时钟不能直接比较。")
_UNIQUE_DECLARATION = "这些局部流水号各只归属所述一笔支付，不存在复用或该两笔的冲正。"
_SINGLE_DECLARATION = ("本函仅确认上列这一笔支付。上述两个局部流水号仅归属该笔支付，且该笔未发生冲正。"
                       "其余记录的对应关系未核定。")
_UNPROVED_DECLARATION = ("本函仅确认上述机构业务域存在两笔等额支付；现有资料未说明每个付款局部流水与哪个收款局部流水相对应。"
                         "不得把局部流水的排列位置当作配对约定。")


@dataclass(frozen=True)
class Notice:
    lines: tuple[str, ...]
    header: re.Match
    scope: re.Match
    locator: dict


@dataclass(frozen=True)
class Group:
    scope: dict
    rows: dict
    choices: dict
    witnesses: dict
    notices: tuple
    proofs: dict
    single_pair: bool

    @property
    def flow_ids(self):
        return sorted(str(row["flow_id"]) for row in self.rows.values())


def _notice(payload, locator=None):
    if len(payload) > _MAX_NOTICE_BYTES:
        return None
    try:
        lines = tuple(payload.decode("utf-8-sig").splitlines())
    except UnicodeError:
        return None
    if not 2 <= len(lines) <= 16 or any(len(line) > 1024 for line in lines):
        return None
    header, scope = _HEADER.fullmatch(lines[0]), _SCOPE.fullmatch(lines[1])
    if header and scope:
        return Notice(lines, header, scope, locator or {})
    return None


def intake_description(payload):
    """Recognize the read-only envelope without deciding what it establishes."""
    return _notice(payload) is not None


def _active_notices(case_id):
    materials, reason = delayed._source_materials(case_id, ("description",))
    if reason:
        return ()
    return tuple(note for _, payload, locator in materials if (note := _notice(payload, locator)))


def _redact(line, match, aliases):
    for (start, end), label in sorted(((match.span(key), label) for key, label in aliases.items()), reverse=True):
        line = line[:start] + label + line[end:]
    return line


def _witnesses(notices, scope, rows):
    labels = {}

    def name(key, value):
        known = labels.setdefault(key, {})
        return known.setdefault(value, f"{_LABEL_PREFIX[key]}{len(known) + 1}")

    for key in _NOTICE_FIELDS:
        name(key, scope[key])
    references = {(row["side"], row["trade_no"]): alias for alias, row in rows.items()}
    result = {}
    for index, notice in enumerate(notices, 1):
        for number, line in enumerate(notice.lines, 1):
            if number == 1:
                text = _redact(line, notice.header, {"issuer": name("issuer", notice.header["issuer"])})
            elif number == 2:
                text = _redact(line, notice.scope, {key: name(key, notice.scope[key]) for key in _NOTICE_FIELDS})
            elif match := _CORRESPONDENCE.fullmatch(line):
                text = _redact(line, match, {f"{side}_reference": references.get((side, match[f"{side}_reference"]), "未列入的原行")
                                            for side in _SIDES})
            elif line in {_CLOCK_DECLARATION, _UNIQUE_DECLARATION, _SINGLE_DECLARATION, _UNPROVED_DECLARATION}:
                text = line
            else:
                continue  # Unsupported free text is never sent to a model.
            result[f"W{len(result) + 1}"] = {"text": text, "source_row": number, "document": f"D{index}"}
    return result


def _group_rows(case_id, anchor_id):
    seeds = db.q_raw("""select distinct reference_key,evidence from event_observation_cache
        where case_id=? and flow_id=? and effective and typed_accounts""", [case_id, anchor_id])
    if len(seeds) != 1:
        return None
    [seed] = seeds
    raw = db.q_raw("""select c.flow_id,c.effective,c.typed_accounts,c.payer_account,c.payee_account,c.self_account,
        c.currency,c.amount,c.trade_time,c.trade_time_utc,f.trade_no,f.payer_account raw_payer,f.payee_account raw_payee
        from event_observation_cache c left join flows f on f.case_id=c.case_id and f.id=c.flow_id
        where c.case_id=? and c.reference_key=? order by c.flow_id limit ?""",
        [case_id, seed["reference_key"], _MAX_GROUP_ROWS + 1])
    if len(raw) > _MAX_GROUP_ROWS or any(not row["effective"] or not row["typed_accounts"] or row["flow_id"] is None
                                        for row in raw):
        return None
    unique = {}
    for row in raw:
        if unique.setdefault(row["flow_id"], row) != row:
            return None
    anchor = unique.get(anchor_id)
    if not anchor or not anchor["trade_time"] or anchor["payer_account"] == anchor["payee_account"]:
        return None
    scope_fields = ("payer_account", "payee_account", "raw_payer", "raw_payee", "currency", "amount")
    if not all(anchor[key] for key in scope_fields) or anchor["amount"] <= 0:
        return None
    day = anchor["trade_time"].date()
    rows = {}
    side_refs = defaultdict(set)
    for row in unique.values():
        if (any(row[key] != anchor[key] for key in scope_fields) or not row["trade_time"] or row["trade_time"].date() != day
                or row["self_account"] not in (anchor["payer_account"], anchor["payee_account"]) or not row["trade_no"]):
            return None
        side = "payer" if row["self_account"] == anchor["payer_account"] else "payee"
        if row["trade_no"] in side_refs[side]:
            return None
        side_refs[side].add(row["trade_no"])
        rows[f"R{len(rows) + 1}"] = {**row, "side": side}
    # Two and three payments have respectively two and six complete matchings.
    if not 2 <= len(side_refs["payer"]) == len(side_refs["payee"]) <= 3:
        return None
    scope = {**event_resolution.validate_reference(seed["evidence"]), "day": day.isoformat(),
             "payer": anchor["raw_payer"], "payee": anchor["raw_payee"], "currency": anchor["currency"],
             "amount": format(anchor["amount"], ".2f")}
    return scope, rows


def _group(case_id, anchor_id):
    if (found := _group_rows(case_id, anchor_id)) is None:
        return None
    scope, rows = found
    notices = tuple(note for note in _active_notices(case_id)
                    if all(note.scope[key] == scope[key] for key in ("payer", "payee", "day")))
    if not 0 < len(notices) <= _MAX_OFFER_NOTICES:
        return None
    payers, payees = ([alias for alias, row in rows.items() if row["side"] == side] for side in _SIDES)
    proofs = _corroborated_pairs(notices, scope)
    options = [(payer, payee) for payer in payers for payee in payees]
    sources = {pair: set(proofs.get(tuple(rows[alias]["trade_no"] for alias in pair), ())) for pair in options}
    assignments = [list(zip(payers, order, strict=True)) for order in permutations(payees)]
    complete = any(set.intersection(*(sources[pair] for pair in assignment)) for assignment in assignments)
    single_pair = any(sources.values()) and not complete
    if single_pair:
        anchor = next(alias for alias, row in rows.items() if row["flow_id"] == anchor_id)
        assignments = [[pair] for pair in options if anchor in pair]
    choices = {f"O{index}": {"pairs": [list(pair) for pair in assignment]}
               for index, assignment in enumerate(assignments, 1)}
    assert len(choices) <= _MAX_OPTIONS
    return Group(scope, rows, choices, _witnesses(notices, scope, rows), notices, proofs, single_pair)


def _fingerprint(group, revision, anchor_id):
    values = [revision, anchor_id, group.scope, group.flow_ids, [notice.locator for notice in group.notices]]
    return sha256(event_resolution._json(values).encode()).hexdigest()


def _current(case_id, body):
    anchor = int(body.anchor_id or 0)
    review = trace_review._require(body.review_token, int(case_id), int(body.canvas_id or 0), anchor_id=anchor)
    group = _group(int(case_id), anchor)
    if group is None or not set(map(int, group.flow_ids)) <= review.flow_ids:
        return None, review
    states = event_resolution.members(case_id, [str(anchor)])
    if not states or states[0]["status"] != "conflict" or states[0]["reason"] != "reference_reused":
        return None, review
    return group, review


def prepare(case_id, body):
    """Offer the same original scope to deterministic, model and review callers."""
    with db._lock:
        group, review = _current(case_id, body)
        if group is None:
            return None
        anchor = next(alias for alias, row in group.rows.items() if row["flow_id"] == review.anchor_id)
        rows = {}
        for side in _SIDES:
            book = {alias: row for alias, row in group.rows.items() if row["side"] == side}
            bases = {row["trade_time_utc"] is not None for row in book.values()}
            times = {alias: row["trade_time_utc"] or row["trade_time"] for alias, row in book.items()}
            ranks = {stamp: index for index, stamp in enumerate(sorted(set(times.values())), 1)} if len(bases) == 1 else {}
            for alias, row in book.items():
                rows[alias] = {"role": side, "clock": "source_reported_time",
                               "clock_basis": "explicit_utc" if row["trade_time_utc"] is not None else "source_wall_clock",
                               "clock_comparability": "cross_ledger_scope_requires_original",
                               "book_clock_rank": ranks.get(times[alias]), "reference_group": "G1",
                               "amount": group.scope["amount"], "currency": group.scope["currency"],
                               "scope": {"issuer": "I1", "channel": "C1", "business_scope": "B1", "day": "D1",
                                         "payer": "P1", "payee": "Q1"}}
        return {"scope_sha256": _fingerprint(group, review.revision, review.anchor_id),
                "anchor": anchor, "choices": group.choices, "rows": rows, "witnesses": group.witnesses,
                "candidate_count": len(group.choices), "more_candidates": False, "single_pair": group.single_pair}


def _claims(notice, scope):
    """Check all source text, including its clock, uniqueness and exceptions."""
    ending = notice.lines[-1]
    if (len(notice.lines) < 5 or notice.header["issuer"] != scope["issuer"]
            or notice.lines[2] != _CLOCK_DECLARATION
            or ending not in {_UNIQUE_DECLARATION, _SINGLE_DECLARATION}
            or any(notice.scope[key] != scope[key] for key in _NOTICE_FIELDS)):
        return None
    if (ending == _SINGLE_DECLARATION and len(notice.lines) != 5
            or ending == _UNIQUE_DECLARATION and len(notice.lines) < 6):
        return None
    claims = {}
    for line, original in enumerate(notice.lines[3:-1], 4):
        statement = _CORRESPONDENCE.fullmatch(original)
        if statement is None:
            return None
        local_ids = tuple(statement[side + "_reference"] for side in _SIDES)
        if local_ids in claims:
            return None
        supports = statement["status"] == "已完成且未冲正" and all(
            statement[key] == scope[key] for key in ("amount", "currency"))
        claims[local_ids] = line, supports
    if any(len({pair[side] for pair in claims}) != len(claims) for side in range(len(_SIDES))):
        return None
    return claims


def _corroborated_pairs(notices, scope):
    """Keep directly supported pairs whose local postings have no competing claim."""
    proofs, postings = defaultdict(dict), defaultdict(set)
    blocked = set()
    for notice in notices:
        if any(notice.scope[key] != scope[key] for key in _NOTICE_FIELDS):
            continue
        claims = _claims(notice, scope)
        if claims is None:
            return {}  # Unclassified evidence has no reliable per-posting boundary.
        for pair, (line, supports) in claims.items():
            roles = set(enumerate(pair))
            if supports:
                proofs[pair][notice.locator["member_sha256"]] = notice, line
                for role in roles:
                    postings[role].add(pair)
            else:
                blocked.update(roles)
    blocked.update(role for role, pairs in postings.items() if len(pairs) > 1)
    return {pair: sources for pair, sources in proofs.items() if blocked.isdisjoint(enumerate(pair))}


def _selected_decision(group, anchor, choice, refs):
    if not isinstance(choice, str) or choice not in group.choices or not isinstance(refs, (tuple, list)) or len(refs) > 8:
        return None
    cited = {}
    for ref in refs:
        if not isinstance(ref, str) or ref not in group.witnesses and ref not in group.rows:
            return None
        if witness := group.witnesses.get(ref):
            cited.setdefault(witness["document"], set()).add(witness["source_row"])
    selected = [[group.rows[alias] for alias in pair] for pair in group.choices[choice]["pairs"]]
    local_pairs = [tuple(row["trade_no"] for row in pair) for pair in selected]
    anchor_pair = next(key for key, pair in zip(local_pairs, selected, strict=True)
                       if any(row["flow_id"] == anchor for row in pair))
    for index, note in enumerate(group.notices, 1):
        if not (lines := cited.get(f"D{index}")):
            continue
        digest = note.locator["member_sha256"]
        if not all(digest in group.proofs.get(pair, {}) for pair in local_pairs):
            continue
        if group.proofs[anchor_pair][digest][1] in lines:
            return {"anchor": anchor, "pairs": [[row["flow_id"] for row in pair] for pair in selected],
                    "member_sha256": digest}
    return None


def _prepare_projection(case_id, decision, states, group=None, *, replay=False):
    if group is None:
        group = _group(case_id, decision["anchor"])
    if group is None:
        return []
    selected_ids = [str(flow) for pair in decision["pairs"] for flow in pair]
    if len(decision["pairs"]) == 1:
        if (len(selected_ids) != 2 or selected_ids[0] == selected_ids[1] or str(decision["anchor"]) not in selected_ids
                or not set(selected_ids) <= set(group.flow_ids)):
            return []
    elif sorted(selected_ids) != group.flow_ids:
        return []
    by_flow = {row["flow_id"]: row for row in group.rows.values()}
    approved = []
    for selected in decision["pairs"]:
        pair = tuple(by_flow[flow]["trade_no"] for flow in selected)
        sources = group.proofs.get(pair, {})
        proof = sources.get(decision["member_sha256"])
        if proof is None and replay and sources:
            proof = sources[min(sources)]
        if proof is None:
            if replay:
                continue
            return []
        approved.append((pair, selected, *proof))
    if not approved:
        return []
    wanted = {ref for pair, _, _, _ in approved for ref in pair}
    postings = delayed._posting_rows(case_id, wanted)
    if len(postings) > delayed._MAX_MATCHES:
        return []
    references = defaultdict(list)
    for row in postings:
        cells = delayed._reference_cells(row)
        locators = {cell["raw"].strip() for cell in cells} | {row["trade_no"]}
        for ref in locators & wanted:
            references[ref].append({**row, "reference_cells": cells})
    key = tuple(group.scope[field] for field in _SCOPE_FIELDS)
    events = []
    for pair, selected, note, line in approved:
        original = {**note.locator, "source_row": line}
        scope = {**note.locator, "source_row": 2}
        values = {**group.scope, "payer_reference": pair[0], "payee_reference": pair[1]}
        rows, reason = delayed._match_pair({**values, "separate_scope": True}, original, references,
                                           lambda row: delayed._original_row(case_id, row), scope, same_bank_books=True)
        if reason or [row["flow_id"] for row in rows] != selected:
            if replay:
                continue
            return []
        event = delayed._event(key, values, original, rows, scope)
        event["event_id"] = "ftev_" + sha256(f"{POLICY}:{event['event_id']}".encode()).hexdigest()
        members = [states.get(str(flow), {}) for flow in selected]
        pending = all(row.get("status") == "conflict" and row.get("reason") == "reference_reused" for row in members)
        confirmed = all(row.get("status") == "confirmed" and row.get("event_id") == event["event_id"] for row in members)
        if not (pending or confirmed):
            if replay:
                continue
            return []
        stored = json.loads(event["evidence_json"])
        stored.update(policy=POLICY, decision=decision)
        event["evidence_json"] = event_resolution._json(stored)
        events.append(event)
    return events


def project(case_id, memberships, new=None):
    """Recheck live decisions from the original evidence on ordinary rebuilds."""
    saved = db.q_raw("""select distinct json_extract_string(evidence_json,'$.decision') decision
        from resolved_events where case_id=? and json_extract_string(evidence_json,'$.policy')=?""", [case_id, POLICY])
    decisions = [json.loads(row["decision"]) for row in saved]
    states = {row["flow_id"]: row for row in memberships}
    projected = {}
    for decision in decisions:
        for event in _prepare_projection(case_id, decision, states, replay=True):
            projected[event["event_id"]] = event
    if new is not None:
        for event in _prepare_projection(case_id, new, states):
            projected[event["event_id"]] = event
    participants = defaultdict(int)
    for event in projected.values():
        for flow in event["flow_ids"]:
            participants[flow] += 1
    events = [event for event in projected.values() if all(participants[flow] == 1 for flow in event["flow_ids"])]
    members = [{"flow_id": flow, "event_id": event["event_id"], "status": "confirmed", "reason": delayed.REASON}
               for event in events for flow in event["flow_ids"]]
    return events, members


def _outcome(case_id, decision, revision, prior_revision):
    ids = [str(flow) for pair in decision["pairs"] for flow in pair]
    members = event_resolution.members(case_id, ids)
    if len(members) != len(ids) or any(item["status"] != "confirmed" for item in members):
        raise ServiceError(409, "pair_decision_stale", "当前原件或候选已改变，请重新核对")
    events = {row["event_id"]: delayed.confirmation(case_id, row["flow_id"]) for row in members}
    if any(proof is None or proof["policy"] != POLICY or proof["decision"] != decision for proof in events.values()):
        raise ServiceError(409, "pair_decision_stale", "当前原件或候选已改变，请重新核对")
    anchor = next(member["event_id"] for member in members if member["flow_id"] == str(decision["anchor"]))
    locations = {}
    for proof in events.values():
        for side in proof["sides"]:
            locations[f"F{side['flow_id']}"] = delayed._witness_links(case_id, dict(side), material=True)
        for field in ("witness", "scope_witness"):
            note = proof[field]
            identity = (note["source_id"], note["member_path"], note["source_row"])
            if identity not in locations:
                locations[identity] = delayed._witness_links(case_id, dict(note), material=True, record_row=1)
    locator_fields = (*_MEMBER_FIELDS, "source_row", "record_url", "file_url")
    records = [{"id": ref if isinstance(ref, str) else f"W{index}",
                "locator": {field: locator[field] for field in locator_fields}, "raw": {}, "structured": {}}
               for index, (ref, locator) in enumerate(locations.items(), 1)]
    [financial] = db.q_raw("select amount,currency from resolved_events where case_id=? and event_id=?", [case_id, anchor])
    key = sha256(event_resolution._json([case_id, revision, anchor, POLICY]).encode()).hexdigest()
    return {"receipt": {"receipt_id": key, "proposition": "same_transaction", "status": "accepted", "reason": delayed.REASON,
                        "refs": [row["id"] for row in records], "evidence_window_truncated": False, "event_id": anchor,
                        "observations": 2, "currency": financial["currency"], "amount": format(financial["amount"], ".2f")},
            "records": records, "analysis_revision": revision, "prior_revision": prior_revision, "event_ids": sorted(events)}


def _offered_group(case_id, body, scope_sha256):
    group, review = _current(case_id, body)
    if group is None or _fingerprint(group, review.revision, review.anchor_id) != scope_sha256:
        raise ServiceError(409, "pair_decision_stale", "当前原件或候选已改变，请重新核对")
    return group, review


def explicit_claim(case_id, body, scope_sha256):
    """Find a unique, completely proved assignment among the offered choices."""
    case_id = int(case_id)
    with db._lock:
        group, review = _offered_group(case_id, body, scope_sha256)
        anchor = next(row for row in group.rows.values() if row["flow_id"] == review.anchor_id)
        side_index = _SIDES.index(anchor["side"])
        for index, note in enumerate(group.notices, 1):
            claims = _claims(note, group.scope)
            if claims is None:
                continue
            anchor_lines = [line for pair, (line, supports) in claims.items()
                            if supports and pair[side_index] == anchor["trade_no"]]
            if len(anchor_lines) != 1:
                continue
            witness = next((ref for ref, item in group.witnesses.items()
                            if item["document"] == f"D{index}" and item["source_row"] == anchor_lines[0]), None)
            if witness is None:
                continue
            # The selector compares every applicable original to each assignment.
            # The live group and decoded notices stay common to the whole scan.
            choices = [(choice, decision) for choice in group.choices
                       if (decision := _selected_decision(group, review.anchor_id, choice, (witness,))) is not None]
            if not choices:
                continue  # A corroborating original can support only the anchor.
            if len(choices) != 1:
                return None
            [(choice, decision)] = choices
            states = {member["flow_id"]: member for member in event_resolution.members(case_id, group.flow_ids)}
            return (choice, witness) if _prepare_projection(case_id, decision, states, group) else None
    return None


def verify(case_id, body, scope_sha256, choice, refs):
    """Adjudicate a submitted proposal without changing facts or receipts."""
    if choice == "DEFER":
        return None
    case_id = int(case_id)
    with db._lock:
        group, review = _offered_group(case_id, body, scope_sha256)
        decision = _selected_decision(group, review.anchor_id, choice, refs)
        if decision is None:
            return None
        states = {member["flow_id"]: member for member in event_resolution.members(case_id, group.flow_ids)}
        if not _prepare_projection(case_id, decision, states, group):
            return None
        return {"choice": choice, "refs": refs}


def adopt(case_id, body, scope_sha256, choice, refs, *, on_accept=None):
    """Publish only a verified assignment on the existing bound card transaction."""
    from . import assistance

    if choice == "DEFER":
        return None
    case_id = int(case_id)
    with db.transaction():
        group, review = _offered_group(case_id, body, scope_sha256)
        decision = _selected_decision(group, review.anchor_id, choice, refs)
        if decision is None:
            return None
        states = {member["flow_id"]: member for member in event_resolution.members(case_id, group.flow_ids)}
        proposed = _prepare_projection(case_id, decision, states, group)
        if not proposed:
            return None
        expected = {}
        for event in proposed:
            proof = json.loads(event["evidence_json"])
            for origin in [*proof["sides"], proof["witness"]]:
                expected[origin["source_id"]] = origin["sha256"]
        tickets = assistance.verified_product_evidence(case_id, expected)
        if tickets.keys() != expected.keys():
            raise ServiceError(409, "pair_decision_stale", "当前原件或候选已改变，请重新核对")
        event_resolution.rebuild(case_id, pair_decision=decision)
        analysis_snapshot.invalidate(case_id, refresh_source_index=False)
        outcome = _outcome(case_id, decision, analysis_snapshot.revision(case_id), review.revision)
        if on_accept is not None:
            on_accept(outcome)
        if any(assistance._original_stamp(path) != stamp for _, path, stamp in tickets.values()):
            raise ServiceError(409, "pair_decision_stale", "当前原件或候选已改变，请重新核对")
        return outcome
