"""Certify scoped delayed pairs from current, independently registered originals."""
import csv
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import io
import json
import re
from urllib.parse import urlencode
from zipfile import BadZipFile

from .. import bank_posting_pairs, db, evidence_resolution, import_originals, table_time_formats, transaction_time
from ..event_resolution import NONUNIQUE_SCOPE, UNAVAILABLE_SCOPE, UNREVIEWED_SCOPE


POLICY = "registered-delayed-settlement-v1"
SEPARATE_POLICY = "registered-delayed-local-flow-settlement-v1"
DECISION_POLICY = "registered-pair-decision-v1"
ROLE = "settlement_bridge"
SCOPE_ROLE = "settlement_scope"
SAME_INSTANT_ROLE = "event_reference_scope"
DELAYED_ROLES = (ROLE, SCOPE_ROLE)
EVENT_SOURCE_ROLES = (*DELAYED_ROLES, SAME_INSTANT_ROLE)
REASON = "scoped_settlement_and_opposite_ledgers"
COVERAGE_REASON = "settlement_bridge_coverage_incomplete"
MISSING_CORRESPONDENCE = "settlement_bridge_missing_correspondence"
MISSING_SCOPE = "settlement_bridge_missing_scope_statement"
MAX_BYTES = 256 * 1024
MAX_RECORDS = 200
MAX_SOURCES = 32
_MAX_MATCHES = 8192
_SCOPE = ("issuer", "channel", "business_scope", "reference")
_PARTIES = ("payer", "payee")
_OPEN_REASONS = {"missing_opposite_ledger", "conflicting_event_facts", "time_basis_incomparable"}
_LEGACY_PAIR = "scoped_reference_and_opposite_ledgers"
_SEPARATE_REASONS = _OPEN_REASONS | {_LEGACY_PAIR, "reference_reused", "multiple_entries_on_one_side", NONUNIQUE_SCOPE}
_REVERSAL_STATES = frozenset({"撤销", "已撤销", "冲正", "已冲正", "退款", "退汇", "失败", "已失败",
                              "refund", "reversal", "reversed", "cancelled", "canceled", "failed"})
_FIELDS = (*_SCOPE, "payer_reference", "payee_reference", *_PARTIES, "amount", "currency", "status", "clock_roles")
HEADERS = ("签发方", "通道", "业务域", "唯一跨账簿参考", "付款侧流水", "收款侧流水", "付款账号", "收款账号",
           "金额", "币种", "状态", "时钟口径")
SEPARATE_SCOPE_HEADERS = (*HEADERS[:3], "跨账簿参考号", "付款流水", "收款流水", *HEADERS[6:-1])
_SUPPORTED_HEADERS = frozenset({HEADERS, (*HEADERS[:3], "跨账簿参考", *HEADERS[4:]), SEPARATE_SCOPE_HEADERS})
_ISSUER_LINE = re.compile(r"(?:合成)?独立清算机构 (.+) 的资料。")
_DELAYED_SCOPE_LINE = re.compile(r"([^/]+) / ([^/]+) 参考号(.*)")
_DELAYED_REFERENCE = "可在不同支付间复用，不能仅凭参考号相等断定同笔。"
_DELAYED_BINDING = "每行完成且未冲正的清算记录明确绑定付款侧与收款侧流水；该范围内两侧各自的流水号各只属于一笔完成且未冲正的支付。"
_DELAYED_CLOCK = "银行文件中的付款侧交易时间是发起时间，收款侧交易时间是入账时间，二者并非同一时钟。"
_SAME_INSTANT_LINE = re.compile(r"([^:]+): ([^/]+) / ([^/]+) 中跨账簿参考号(.*)")
_UNIQUE_REFERENCE = "每个值仅对应一笔支付"
_SAME_INSTANT_CLOCK = "两侧记载交易发生时刻"
_REUSABLE_REFERENCE = "每个值可对应多笔支付，不能凭参考号认定同笔"
_CLOCK_ROLES = "付款发起时刻和收款入账时刻"
_WITNESS_FIELDS = ("source_id", "sha256", "member_path", "member_sha256")


def _clock(raw):
    value = table_time_formats.source_time_value(raw)
    return datetime.fromisoformat(value["utc"][:-1]) if value["basis"] == "explicit_offset" else None


def _scope(values):
    return _scope_values(tuple(values[key] for key in _SCOPE))


def _scope_values(values):
    return values if all(value and bank_posting_pairs._identifier(value) == value for value in values) else None


def declaration_header(payload):
    """Recognize the fixed header even when the first body bytes cannot be read."""
    sample = payload[:4096]
    encodings = ("utf-16",) if sample.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
    for encoding in encodings:
        lines = sample.decode(encoding, errors="replace").splitlines()
        if not lines:
            continue
        for separator in (",", "\t", ";", "|"):
            try:
                header = next(csv.reader([lines[0]], delimiter=separator, strict=True), ())
            except csv.Error:
                continue
            columns = tuple(cell.strip() for cell in header)
            if columns in _SUPPORTED_HEADERS:
                return encoding, columns
    return None


def read_declarations(payload):
    """Read a closed version-one auxiliary format, retaining bad declarations."""
    if len(payload) > MAX_BYTES:
        raise ValueError("清算对应表超出核证大小")
    reader = csv.reader(io.StringIO(payload.decode("utf-8-sig"), newline=""), strict=True)
    header = tuple(next(reader, ()))
    if header not in _SUPPORTED_HEADERS:
        raise ValueError("清算对应表的字段不完整")
    separate_scope = header == SEPARATE_SCOPE_HEADERS
    fields_in_source = _FIELDS[:-1] if separate_scope else _FIELDS
    result = []
    for number, cells in enumerate(reader, 2):
        if number > MAX_RECORDS + 1:
            raise ValueError("清算对应表超出核证行数")
        if not any(cell.strip() for cell in cells):
            continue
        fields = dict.fromkeys(_FIELDS, "")
        fields.update(zip(fields_in_source, cells))
        if _scope(fields) is None:
            raise ValueError("清算对应表包含无法限定作用域的声明")
        fields["complete"] = len(cells) == len(fields_in_source)
        fields["separate_scope"] = separate_scope
        result.append((number, fields))
    return result


def scope_statement(payload):
    """Identify the declared scope and whether its claims match a supported contract."""
    if statement := _read_scope_statement(payload):
        role, key, claim = statement
        return role, key, claim is not None
    return None


def _read_scope_statement(payload):
    # Multi-line text is read as one registered document, with the first line
    # serving as its stable original position even when the claims are incomplete.
    try:
        lines = payload[:MAX_BYTES + 1].decode("utf-8-sig", errors="strict").splitlines()
    except UnicodeError:
        return None
    if not lines:
        return None
    if issuer := _ISSUER_LINE.fullmatch(lines[0]):
        domain = _DELAYED_SCOPE_LINE.fullmatch(lines[1]) if len(lines) > 1 else None
        key = _scope_values((issuer[1], domain[1], domain[2])) if domain else None
        complete = (len(payload) <= MAX_BYTES and key is not None and domain[3] == _DELAYED_REFERENCE
                    and len(lines) == 4 and lines[2:] == [_DELAYED_BINDING, _DELAYED_CLOCK])
        return SCOPE_ROLE, key, "affirmative" if complete else None
    if reference := _SAME_INSTANT_LINE.fullmatch(lines[0]):
        key = _scope_values(reference.groups()[:3])
        claim = None
        if len(payload) <= MAX_BYTES and key is not None and len(lines) == 1:
            reference_clause, _, clock_clause = reference[4].removesuffix("。").partition("；")
            if reference_clause == _UNIQUE_REFERENCE and clock_clause == _SAME_INSTANT_CLOCK:
                claim = "affirmative"
            elif reference_clause == _REUSABLE_REFERENCE:
                claim = "nonunique"
        return SAME_INSTANT_ROLE, key, claim
    return None


def _source_query(presence=False):
    fields = ("1" if presence else
              f"distinct p.source_id,p.sha256,m.member_path,m.sha256 member_sha256,m.size_bytes,s.suffix,"
              "json_extract_string(r.details_json,'$.intake_material_role') intake_role,"
              f"r.reason_code='{COVERAGE_REASON}' coverage_incomplete")
    order = "" if presence else "order by m.sha256,p.source_id,m.member_path"
    return f"""select {fields} from import_records r
        join import_sources p on p.case_id=r.case_id and p.source_id=r.source_id
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
        join import_source_operations o on o.case_id=p.case_id and o.source_id=p.source_id and o.active
        join batches b on b.case_id=o.case_id and b.id=o.batch_id
        left join import_sessions s on s.case_id=p.case_id and s.import_id=p.source_id
        where p.case_id=? and m.role='auxiliary'
          and json_extract_string(r.details_json,'$.intake_material_role') in (select unnest(?::varchar[]))
        {order} limit ?"""


def has_sources(case_id, roles=EVENT_SOURCE_ROLES):
    return bool(db.q_raw(_source_query(presence=True), [case_id, list(roles), 1]))


def has_projection(case_id):
    return bool(db.q_raw("""select 1 from event_members where case_id=?
        and (reason in (?,?,?,?) or starts_with(reason,'settlement_bridge_')) limit 1""",
        [case_id, REASON, NONUNIQUE_SCOPE, UNAVAILABLE_SCOPE, UNREVIEWED_SCOPE]))


def _source_materials(case_id, roles):
    from . import imports
    from .cases import ServiceError

    materials = []
    sources = db.q_raw(_source_query(), [case_id, list(roles), MAX_SOURCES + 1])
    if len(sources) > MAX_SOURCES:
        return [], "settlement_bridge_scope_too_large"
    if any(source["coverage_incomplete"] or source["size_bytes"] > MAX_BYTES for source in sources):
        return [], COVERAGE_REASON
    read_digests = set()
    for source in sources:
        digest = source["member_sha256"]
        if digest in read_digests:
            continue  # Another retained copy can prove the same declaration, never a second vote.
        try:
            original, _ = imports.get_source_file(case_id, source["source_id"])
            with original:
                if source["suffix"] in {".zip", ".rar"}:
                    with import_originals._member(original, source["member_path"]) as member:
                        content = member.read(MAX_BYTES + 1)
                else:
                    content = original.read(MAX_BYTES + 1)
                if len(content) != source["size_bytes"] or hashlib.sha256(content).hexdigest() != digest:
                    continue
        except (ServiceError, OSError, ValueError, BadZipFile):
            continue
        read_digests.add(digest)
        materials.append((source["intake_role"], content, {field: source[field] for field in _WITNESS_FIELDS}))
    if read_digests != {source["member_sha256"] for source in sources}:
        return [], "settlement_bridge_original_unavailable"
    return materials, ""


def _witness_links(case_id, location, *, material=False, record_row=None):
    from . import imports

    source_id, member_path, source_row = (location[key] for key in ("source_id", "member_path", "source_row"))
    base = f"/api/v1/cases/{case_id}"
    query = urlencode({"member_path": member_path, "source_row": source_row if record_row is None else record_row})
    location["record_url"] = f"{base}/imports/{source_id}/records?{query}"
    location["file_url"] = None
    if imports.source_is_available(case_id, source_id):
        resource = "file"
        if material:
            session = imports._source_session(case_id, source_id)
            request = json.loads(session["request_json"] or "{}")
            if any(item["member_path"] == member_path for item in request.get(imports._INTAKE_ITEMS, ())):
                resource = "member-file?" + urlencode({"member_path": member_path})
        location["file_url"] = f"{base}/sources/{source_id}/{resource}"
    return location


def _scope_evidence(materials, role, *, exclude_nonunique=False):
    scopes = defaultdict(list)
    for candidate_role, content, witness in materials:
        if candidate_role != role:
            continue
        statement = _read_scope_statement(content)
        if statement is None or statement[0] != role or statement[1] is None:
            continue
        _, key, claim = statement
        if not (exclude_nonunique and claim == "nonunique"):
            scopes[key].append({**witness, "source_row": 1} if claim == "affirmative" else None)
    return scopes


def same_instant_scope_witness(case_id, reference_evidence):
    """Resolve present source authority for a single issuer, channel and domain."""
    materials, reason = _source_materials(case_id, (SAME_INSTANT_ROLE, SCOPE_ROLE))
    if reason:
        return None
    key = tuple(reference_evidence[field] for field in _SCOPE[:3])
    if key in _scope_evidence(materials, SCOPE_ROLE):
        return None
    claims = _scope_evidence(materials, SAME_INSTANT_ROLE).get(key)
    if claims and all(claims):
        return _witness_links(case_id, claims[0], material=True)
    return None


def same_instant_counterevidence(case_id):
    """Keep the plain reference rule open when a scoped original denies or leaves its authority unclear."""
    materials, unavailable = _source_materials(case_id, (SAME_INSTANT_ROLE,))
    if unavailable:
        return None
    restrictions = {}
    for _, content, _ in materials:
        if not (statement := _read_scope_statement(content)):
            continue
        _, scope, claim = statement
        if scope is not None and claim == "nonunique":
            restrictions[scope] = NONUNIQUE_SCOPE
        elif scope is not None and claim is None:
            restrictions.setdefault(scope, UNREVIEWED_SCOPE)
    return restrictions


def _posting_rows(case_id, references):
    """Walk the complete active local-reference domain, including unresolved rows."""
    return db.q_raw("""with refs as (select unnest(?::varchar[]) reference)
        select p.source_id,p.sha256,p.parser_version,m.member_path,m.sha256 member_sha256,m.encoding,r.source_row,r.disposition,
          coalesce(t.suffix,'') suffix, f.id flow_id,f.trade_no,f.payer_account,f.payee_account,f.self_account,
          f.currency,f.amount,f.balance,f.trade_time,f.trade_time_utc,
          ef.id effective_id,ef.payer_account payer_entity,ef.payee_account payee_entity,ef.self_account own_entity,
          ef.payer_bank,payer_entity_info.kind payer_kind,ef.payee_bank,payee_entity_info.kind payee_kind,
          coalesce(try_cast(json_extract_string(json_extract_string(r.details_json,'$.source_ledger_evidence'),'$.header_scope') as bigint),
                   try_cast(json_extract_string(json_extract_string(r.details_json,'$.analysis_evidence'),'$.posting_scope') as bigint),
                   1) header_row,
          json_extract_string(r.details_json,'$.resolution_evidence') identity_evidence,
          json_extract_string(r.details_json,'$.event_reference_evidence') scoped,
          json_extract_string(r.details_json,'$.trade_no_basis') trade_no_basis,
          json_extract_string(r.details_json,'$.source_field_evidence') fields,
          json_extract_string(r.details_json,'$.source_field_evidence_limited') fields_limited,
          json_extract_string(r.details_json,'$.source_time_evidence') clocks,
          json_extract_string(r.details_json,'$.source_time_evidence_limited') clocks_limited
        from import_records r join import_sources p on p.case_id=r.case_id and p.source_id=r.source_id
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
        join evidence_sources active on active.case_id=r.case_id and active.source_id=r.source_id and active.active
        left join import_sessions t on t.case_id=p.case_id and t.import_id=p.source_id
        left join flow_sources fs on fs.case_id=r.case_id and fs.source_id=r.source_id and fs.source_row=r.record_no
        left join flows f on f.case_id=fs.case_id and f.id=fs.flow_id
        left join effective_flows ef on ef.case_id=f.case_id and ef.id=f.id
        left join resolution_entities payer_entity_info on payer_entity_info.case_id=ef.case_id
          and payer_entity_info.entity_id=ef.payer_account
        left join resolution_entities payee_entity_info on payee_entity_info.case_id=ef.case_id
          and payee_entity_info.entity_id=ef.payee_account
        where r.case_id=? and r.disposition not in ('removed','auxiliary','non_transaction')
          and (f.trade_no in (select reference from refs)
            or exists (select 1 from unnest(try_cast(json_extract_string(r.details_json,'$.source_field_evidence') as json[])) x(cell)
                join refs on reference=trim(json_extract_string(cell,'$.raw'))
                where json_extract_string(cell,'$.role')='source_transaction_id'
                  or (json_extract_string(cell,'$.role')='trade_no' and json_extract_string(cell,'$.column')='交易流水号')))
        order by p.source_id,r.record_no limit ?""",
        [sorted(references), case_id, _MAX_MATCHES + 1])


def _reference_cells(row):
    return [cell for cell in json.loads(row["fields"] or "[]")
            if (cell["role"] == "source_transaction_id" or (cell["role"], cell["column"]) == ("trade_no", "交易流水号"))
            and cell["raw"].strip()]


def _valid(values):
    if not values["complete"]:
        return False
    for name in (*_PARTIES, "payer_reference", "payee_reference"):
        text = values[name]
        if not text or bank_posting_pairs._identifier(text) != text:
            return False
    return (values["status"] == "已完成且未冲正" and values["payer"] != values["payee"]
            and re.fullmatch(r"[A-Z]{3}", values["currency"]) is not None
            and re.fullmatch(r"(?:0|[1-9][0-9]{0,35})\.[0-9]{2}", values["amount"]) is not None
            and Decimal(values["amount"]) > 0
            and (values["separate_scope"] or values["clock_roles"] == _CLOCK_ROLES))


def _trade_clock(raw, row):
    utc = _clock(raw)
    if row["trade_time_utc"] is not None:
        return utc == row["trade_time_utc"]
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?", raw):
        try:
            return datetime.fromisoformat(raw) == row["trade_time"]
        except ValueError:
            return False
    return False


def _posting(values, side, rows):
    from ..event_resolution import validate_reference

    reference = values[f"{side}_reference"]
    expected_own = values[side]
    exact, possible = [], []
    money_conflicts = set()
    for row in rows:
        identity = json.loads(row["identity_evidence"] or "{}")
        own = identity.get("endpoints", {}).get("self", {})
        registered = validate_reference(row["scoped"]) if row["scoped"] else {}
        same_scope = (values["separate_scope"] and registered
                      and all(registered[key] == values[key] for key in _SCOPE[:3])
                      and identity.get("own_side") in {None, side})
        if same_scope:
            possible.append((row["flow_id"], None))
        if (own.get("account") or row["self_account"] or expected_own) != expected_own:
            continue
        possible.append((row["flow_id"], own.get("bank")))
        if (row["effective_id"] is None or row["disposition"] not in {"imported", "duplicate"}
                or row["trade_no_basis"] not in {None, "source_transaction_id"} or row["fields_limited"] or row["clocks_limited"]
                or {cell["raw"].strip() for cell in row["reference_cells"]} != {reference}
                or not row["identity_evidence"] or not row["clocks"]
                or row["trade_no"] != reference or not row["scoped"] or not row["suffix"]):
            continue
        if any(registered[key] != values[key] for key in _SCOPE):
            continue
        identity = evidence_resolution.validate_evidence(row["identity_evidence"])
        endpoints = identity.get("endpoints", {})
        clock = json.loads(row["clocks"])
        clock_refs = [item for item in clock["values"] if item["role"] == "transaction" and _trade_clock(item["raw"], row)]
        if (row["self_account"] != expected_own or row["own_entity"] != row[f"{side}_entity"]
                or row["payer_entity"] == row["payee_entity"] or identity.get("own_side") != side
                or any(row[f"{role}_account"] != values[role] or not row[f"{role}_bank"]
                       or row[f"{role}_kind"] not in {"account", "subaccount"} for role in _PARTIES)
                or any(endpoints[key]["identifier_type"] != "account" or endpoints[key]["account"] != values[role]
                       or endpoints[key]["bank"] != row[f"{role}_bank"]
                       for key, role in (("self", side), ("payer", "payer"), ("payee", "payee")))
                or clock["ordinary_time_role"] != "transaction" or len(clock_refs) != 1):
            continue
        if row["currency"] != values["currency"]:
            money_conflicts.add("settlement_bridge_currency_conflict")
            continue
        if row["amount"] != Decimal(values["amount"]):
            money_conflicts.add("settlement_bridge_amount_conflict")
            continue
        exact.append({**row, "clock_ref": clock_refs[0]})
    if not exact:
        return [], min(money_conflicts, default="settlement_bridge_missing_posting")
    flow_ids = {row["flow_id"] for row in exact}
    bank = exact[0][f"{side}_bank"]
    if len(flow_ids) != 1 or any(flow_id not in flow_ids and competition_bank in {bank, None, ""}
                                  for flow_id, competition_bank in possible):
        return [], "settlement_bridge_reference_reused"
    return exact, ""


def _original_row(case_id, row):
    """Match the declared financial fields and clock to actual raw cells."""
    from ..event_resolution import HEADERS as SCOPE_HEADERS, validate_reference
    from ..feedback_cancellations import cancellation_text
    from ..money import money_decimal
    from . import imports
    from .cases import ServiceError

    try:
        original, _ = imports.get_source_file(case_id, row["source_id"])
        with original:
            cells = import_originals.read_cells(original, row["suffix"], row, {"header_row": row["header_row"]}, (),
                                                {row["source_row"]}, row["parser_version"])[row["source_row"]]
        by_header = {cell["header"]: cell["value"].strip() for cell in cells}
        registration = validate_reference(row["scoped"])
        financial = {"付款账号": row["payer_account"], "收款账号": row["payee_account"], "本方账号": row["self_account"],
                     "付款人开户行": row["payer_bank"], "收款人开户行": row["payee_bank"], "交易币种": row["currency"]}
        annotations = {name: by_header.get(name, "") for name in ("摘要说明", "备注", "交易类型")}
        if (not all(by_header.get(name) == value for name, value in financial.items())
                or money_decimal(by_header.get("交易金额", "")) != row["amount"]
                or any(by_header.get(name, "").casefold() in _REVERSAL_STATES for name in ("交易类型", "业务类型", "交易状态"))
                or cancellation_text(annotations)
                or not all(by_header.get(name) == registration[key] for key, name in SCOPE_HEADERS.items())):
            return False
        return len(row["reference_cells"]) == 1 and all(
            cells[item["column_index"] - 1]["header"] == item["column"]
            and cells[item["column_index"] - 1]["value"].strip() == item["raw"].strip()
            for item in (*row["reference_cells"], row["clock_ref"]))
    except (ServiceError, OSError, ValueError, IndexError, csv.Error, BadZipFile):
        return False


def _event(key, values, witness, postings, scope_witness=None):
    from ..event_resolution import _json

    sides = [{**{field: row[field] for field in (*_WITNESS_FIELDS, "source_row")},
              "flow_id": str(row["flow_id"]), "local_reference": row["trade_no"], "raw_time": row["clock_ref"]["raw"],
              "clock_kind": "initiation" if side == "payer" else "credit_posting",
              "utc": row["trade_time_utc"].isoformat(timespec="microseconds") + "Z" if row["trade_time_utc"] else None}
             for side, row in zip(_PARTIES, postings)]
    facts = {"payer_account": postings[0]["payer_entity"], "payee_account": postings[0]["payee_entity"],
             "currency": values["currency"], "amount": Decimal(values["amount"]),
             "trade_time": postings[0]["trade_time_utc"] or postings[0]["trade_time"]}
    policy = SEPARATE_POLICY if scope_witness else POLICY
    event_key = [policy, key, *map(str, facts.values()), *(values[f"{side}_reference"] for side in _PARTIES)]
    event_id = "ftev_" + hashlib.sha256(_json(event_key).encode()).hexdigest()
    proof = {"policy": policy, **dict(zip(_SCOPE, key)), "witness": witness, "sides": sides,
             "clock_basis": "utc" if all(side["utc"] for side in sides) else "incomparable"}
    if scope_witness:
        proof["scope_witness"] = scope_witness
    return {"event_id": event_id, **facts, "evidence_json": _json(proof),
            "flow_ids": sorted(side["flow_id"] for side in sides)}


def _match_pair(values, witness, by_reference, check_original, scope_witness=None, *, same_bank_books=False):
    postings = []
    member_digests = {witness["member_sha256"]}
    if scope_witness:
        member_digests.add(scope_witness["member_sha256"])
    for side in _PARTIES:
        rows, reason = _posting(values, side, by_reference[values[f"{side}_reference"]])
        if reason:
            return None, f"{reason}_{side}"
        independent = [row for row in rows if row["member_sha256"] not in member_digests]
        registered = next((row for row in independent if check_original(row)), None)
        if registered is None:
            return None, ("settlement_bridge_original_unavailable" if independent else "settlement_bridge_not_independent")
        postings.append(registered)
    payer, payee = postings
    banks = {payer[f"{role}_bank"].casefold() for role in _PARTIES}
    if (not same_bank_books and len(banks) != 2
            or values["issuer"].casefold() in banks or payer["member_sha256"] == payee["member_sha256"]):
        return None, "settlement_bridge_not_independent"
    if any(payer[f"{role}_entity"] != payee[f"{role}_entity"] for role in _PARTIES):
        return None, "settlement_bridge_endpoint_conflict"
    initiated, credited = payer["trade_time_utc"], payee["trade_time_utc"]
    # The independent scope can bind two clock roles recorded in the same second.
    if (initiated is not None and credited is not None
            and (initiated > credited or (initiated == credited and not values["separate_scope"]))):
        return None, "settlement_bridge_clock_conflict"
    return postings, ""


def _legacy_agrees(values, flow_ids, by_reference):
    declared = set()
    for side in _PARTIES:
        reference = values[f"{side}_reference"]
        declared.update(str(row["flow_id"]) for row in by_reference[reference]
                        if str(row["flow_id"]) in flow_ids and row["self_account"] == values[side]
                        and row["trade_no"] == reference and row["payer_account"] == values["payer"]
                        and row["payee_account"] == values["payee"] and row["currency"] == values["currency"]
                        and row["amount"] == Decimal(values["amount"]))
    return declared == flow_ids


def _withheld(memberships, reason):
    return [{**row, "status": "unknown" if row["status"] == "confirmed" else row["status"], "event_id": None, "reason": reason}
            for row in memberships if row["reason"] in _SEPARATE_REASONS]


def _project_unique_references(declarations, claimed, scoped, existing, by_reference, check_original, separate_keys):
    events, decisions = [], {}
    for key, claims in sorted(declarations.items()):
        if key in separate_keys:
            continue
        entries = scoped[key]
        flow_ids = {str(row["flow_id"]) for row in entries if row["effective"]}
        reasons = {existing[fid]["reason"] for fid in flow_ids if fid in existing}
        legacy_confirmed = reasons == {_LEGACY_PAIR}
        if not flow_ids or (not legacy_confirmed and reasons - _OPEN_REASONS):
            continue
        values, witness = claims[0]
        if (any(other != values for other, _ in claims)
                or any(len(claimed[key[:3], side, values[side], values[f"{side}_reference"]]) != 1 for side in _PARTIES)
                or len(flow_ids) > 2 or any(not row["effective"] for row in entries)):
            postings, reason = None, "settlement_bridge_reference_reused"
        elif not _valid(values):
            postings, reason = None, "settlement_bridge_declaration_unverified"
        elif legacy_confirmed:
            if _legacy_agrees(values, flow_ids, by_reference):
                continue
            postings, reason = None, "settlement_bridge_scoped_conflict"
        else:
            postings, reason = _match_pair(values, witness, by_reference, check_original)
        event_id = None
        if postings:
            if {str(row["flow_id"]) for row in postings} != flow_ids or len(flow_ids) != 2:
                reason = "settlement_bridge_reference_reused"
            else:
                event = _event(key, values, witness, postings)
                event_id, reason = event["event_id"], REASON
                events.append(event)
        for flow_id in flow_ids:
            status = ("confirmed" if event_id else "conflict" if legacy_confirmed
                      else existing.get(flow_id, {}).get("status", "unknown"))
            decisions[flow_id] = {"flow_id": flow_id, "event_id": event_id, "status": status, "reason": reason}
    return events, decisions


def _scoped_posting_ids(key, values, by_reference):
    from ..event_resolution import validate_reference

    flow_ids = set()
    for side in _PARTIES:
        for row in by_reference[values[f"{side}_reference"]]:
            if row["effective_id"] is None or not row["scoped"]:
                continue
            registered = validate_reference(row["scoped"])
            identity = json.loads(row["identity_evidence"] or "{}")
            if key == tuple(registered[field] for field in _SCOPE) and identity.get("own_side") in {None, side}:
                flow_ids.add(str(row["flow_id"]))
    return flow_ids


def _project_local_references(declarations, scopes, claimed, scoped, existing, by_reference, check_original,
                              separate_keys, unique_keys, unbound_keys):
    events, decisions = [], {}
    eligible = {}
    for key, rows in scoped.items():
        if key not in separate_keys and (key in unique_keys or key[:3] not in scopes):
            continue
        eligible[key] = {str(row["flow_id"]) for row in rows if row["effective"]
                         and existing.get(str(row["flow_id"]), {}).get("reason") in _SEPARATE_REASONS}
        for flow_id in eligible[key]:
            prior = existing[flow_id]
            decisions[flow_id] = {"flow_id": flow_id, "event_id": None,
                                 "status": "conflict" if prior["status"] in {"confirmed", "conflict"} else "unknown",
                                 "reason": MISSING_CORRESPONDENCE}

    for _, claims in sorted(declarations.items()):
        key, values, witness = claims[0]
        flow_ids = set()
        for claim_key, claim, _ in claims:
            active = eligible.get(claim_key, set())
            # An incomplete reference or a source contract disagreement can
            # affect the full reference group, even without a usable local ID.
            flow_ids.update(active if claim_key in unbound_keys or claim_key in unique_keys
                            else active & _scoped_posting_ids(claim_key, claim, by_reference))
        if not flow_ids:
            continue
        note = scopes.get(key[:3], ())
        competed = (key in unbound_keys or any(claim_key != key or other != values for claim_key, other, _ in claims)
                    or any(len(claimed[key[:3], side, values[f"{side}_reference"]]) != 1 for side in _PARTIES))
        postings, event_id = None, None
        if competed:
            reason = "settlement_bridge_reference_reused"
        elif key in unique_keys:
            reason = "settlement_bridge_scoped_conflict"
        elif not note:
            reason = MISSING_SCOPE
        elif not all(note) or not _valid(values):
            reason = "settlement_bridge_declaration_unverified"
        else:
            postings, reason = _match_pair(values, witness, by_reference, check_original, note[0])
        if postings:
            if {str(row["flow_id"]) for row in postings} != flow_ids or len(flow_ids) != 2:
                reason = "settlement_bridge_reference_reused"
            else:
                event = _event(key, values, witness, postings, note[0])
                event_id, reason = event["event_id"], REASON
                events.append(event)
        for flow_id in flow_ids:
            prior = decisions[flow_id]
            if prior["reason"] == "settlement_bridge_reference_reused":
                continue
            status = "confirmed" if event_id else prior["status"]
            if reason.startswith("settlement_bridge_missing_posting_") and existing[flow_id]["reason"] == "reference_reused":
                status = "unknown"
            decisions[flow_id] = {"flow_id": flow_id, "event_id": event_id, "status": status, "reason": reason}
    return events, decisions


def project(case_id, observations, memberships):
    """Project on import and withdrawal while the source transaction is held."""
    materials, reason = _source_materials(case_id, DELAYED_ROLES)
    if reason:
        return [], _withheld(memberships, reason)
    declarations = defaultdict(list)
    separate_declarations = defaultdict(list)
    scopes = _scope_evidence(materials, SCOPE_ROLE)
    if scopes:
        notes, unavailable = _source_materials(case_id, (SAME_INSTANT_ROLE,))
        if unavailable:
            return [], _withheld(memberships, unavailable)
        for key in scopes.keys() & _scope_evidence(notes, SAME_INSTANT_ROLE, exclude_nonunique=True).keys():
            scopes[key].append(None)
    separate_keys, unbound_keys, wanted = set(), set(), set()
    claimed, claimed_local = defaultdict(set), defaultdict(set)
    for role, content, witness in materials:
        if role != ROLE:
            continue
        try:
            claims = read_declarations(content)
        except (ValueError, csv.Error):
            return [], _withheld(memberships, COVERAGE_REASON)
        for number, values in claims:
            key = _scope(values)
            location = {**witness, "source_row": number}
            local_ids = tuple(values[f"{side}_reference"] for side in _PARTIES)
            if values["separate_scope"]:
                separate_declarations[(*key[:3], *local_ids)].append((key, values, location))
                separate_keys.add(key)
                if not _scope_values(local_ids):
                    unbound_keys.add(key)
            else:
                declarations[key].append((values, location))
            relation = (key, *local_ids, *(values[side] for side in _PARTIES))
            for side, local in zip(_PARTIES, local_ids):
                if local:
                    wanted.add(local)
                claimed[key[:3], side, values[side], local].add(key)
                claimed_local[key[:3], side, local].add(relation)

    rows = _posting_rows(case_id, wanted) if wanted else []
    if len(rows) > _MAX_MATCHES:
        return [], _withheld(memberships, "settlement_bridge_reference_scope_too_large")
    from ..event_resolution import validate_reference

    existing = {row["flow_id"]: row for row in memberships}
    scoped = defaultdict(list)
    for row in observations:
        scope = validate_reference(row["evidence"])
        scoped[tuple(scope[key] for key in _SCOPE)].append(row)

    by_reference = defaultdict(list)
    for row in rows:
        cells = _reference_cells(row)
        refs = {cell["raw"].strip() for cell in cells}
        for ref in (refs | {row["trade_no"]}) & wanted:
            by_reference[ref].append({**row, "reference_cells": cells})
    verified = {}

    def check_original(row):
        location = row["source_id"], row["member_path"], row["source_row"]
        if location not in verified:
            verified[location] = _original_row(case_id, row)
        return verified[location]

    events, decisions = _project_unique_references(declarations, claimed, scoped, existing, by_reference,
                                                   check_original, separate_keys)
    separated, resolved = _project_local_references(separate_declarations, scopes, claimed_local, scoped, existing,
                                                     by_reference, check_original, separate_keys, declarations,
                                                     unbound_keys)
    return events + separated, list((decisions | resolved).values())


def confirmation(case_id, anchor_id):
    rows = db.q_raw("""select e.evidence_json from event_members m join resolved_events e
        on e.case_id=m.case_id and e.event_id=m.event_id
        where m.case_id=? and m.flow_id=? and m.status='confirmed' and json_extract_string(e.evidence_json,'$.policy') in (?,?,?)""",
        [case_id, anchor_id, POLICY, SEPARATE_POLICY, DECISION_POLICY])
    return json.loads(rows[0]["evidence_json"]) if rows else None


def review_proof(case_id, anchor_id):
    from . import assistance

    proof = confirmation(case_id, anchor_id)
    if proof is None:
        return None
    materials = [proof["witness"]]
    if note := proof.get("scope_witness"):
        materials.append(note)
    expected = {location["source_id"]: location["sha256"] for location in (*materials, *proof["sides"])}
    if assistance.verified_product_originals(case_id, expected) != set(expected):
        return None
    for location in materials:
        _witness_links(case_id, location, material=True, record_row=1 if proof["policy"] == DECISION_POLICY else None)
    for location in proof["sides"]:
        _witness_links(case_id, location, material=bool(note))
    return proof


def opposite_candidates(case_id, anchor, limit, excluded=()):
    """Recall within the displayed clock basis without inferring clock equality."""
    clock_column = "trade_time_utc" if anchor.get("trade_time_utc") is not None else "trade_time"
    stamp = transaction_time.clock(anchor)
    rows = db.q_raw(f"""select b.id from effective_visible_flows a join effective_visible_flows b on b.case_id=a.case_id
        and b.id<>a.id and b.payer_account=a.payer_account and b.payee_account=a.payee_account
        and b.currency=a.currency and b.amount=a.amount and b.self_account<>a.self_account
        where a.case_id=? and a.id=? and a.payer_account<>a.payee_account
          and a.self_account in (a.payer_account,a.payee_account) and b.self_account in (a.payer_account,a.payee_account)
          and a.resolution_policy is not null and b.resolution_policy is not null and a.payer_bank<>a.payee_bank
          and (b.trade_time_utc is null)=(a.trade_time_utc is null)
          and b.{clock_column} between ? and ? and b.id not in (select unnest(?::bigint[]))
        order by b.{clock_column},b.id limit ?""",
        [case_id, anchor["id"], stamp - timedelta(days=1), stamp + timedelta(days=1), list(excluded), limit + 1])
    return [str(row["id"]) for row in rows[:limit]], len(rows) > limit
