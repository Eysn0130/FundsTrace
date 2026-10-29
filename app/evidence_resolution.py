"""Typed, source-backed account projections; original ``flows`` remain immutable.

A card/account declaration is a relation, never an equality union. In particular,
parent/subaccount edges and cards with multiple account targets cannot collapse
ledger entities. Names, balances and proximity are never identity evidence.
"""
from __future__ import annotations

from collections import defaultdict, OrderedDict
from contextlib import contextmanager, nullcontext
import glob
import hashlib
from itertools import islice
import json
from pathlib import Path
import tempfile
import unicodedata

from . import db

POLICY = "typed-account-evidence-v1"
CARD_PROJECTION = "endpoint-cards-v4-scoped-order"
_SCOPED_EQUAL_REASON = "scoped_equal_account_card_declaration"
DETAIL_FIELD = "resolution_evidence"
TABLE_PROFILE = "funds-trace-table-v20"
ARCHIVE_PROFILE = "funds-trace-table-archive-v6"
FEEDBACK_PROFILE = "funds-trace-bank-feedback-v6"
PROFILES = frozenset((TABLE_PROFILE, ARCHIVE_PROFILE, FEEDBACK_PROFILE, "funds-trace-table-v19", "funds-trace-table-archive-v5", "funds-trace-table-archive-v7"))
_PROFILE_SQL = ",".join("?" for _ in PROFILES)
_MAX_TEXT = 512
WRITE_CHUNK = 50_000
_ENDPOINT_ROLES = {role: role for role in ("payer", "payee", "self")}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text(value):
    # Never coerce damaged numeric identifiers back into plausible strings.
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value if len(value) <= _MAX_TEXT and not any(unicodedata.category(c) in {"Cc", "Cf"} for c in value) else ""


def _projection_evidence(data):
    """Missing markers remain literal source facts, never typed identifiers."""
    values = data["endpoints"].values() if data["role"] == "transaction" else (data,)
    for item in values:
        for key in ("account", "card", "parent", "bank"):
            value = item.get(key)
            if isinstance(value, str) and (value.casefold() in {"null", "none", "nan", "n/a"}
                                           or value and not any(character.isalnum() for character in value)):
                item[key] = ""
    return data


def _field(headers, cells, names):
    values = {_text(value) for header, value in zip(headers, cells) if header.strip() in names and _text(value)}
    return next(iter(values)) if len(values) == 1 else ""


_ACCOUNT = {"账号", "帐号", "交易账号", "开户账号", "账户号码"}
_CARD = {"卡号", "交易卡号", "银行卡号"}
_BANK = {"账号开户银行", "开户银行", "开户行", "银行名称", "本方开户银行", "本方开户行"}
_NAME = {"账户开户名称", "开户名称", "户名", "账户名称"}
_CURRENCY = {"币种", "交易币种"}
_PARENT = {"开户账号", "账卡号", "主账户账号"}
_CHILD = {"子账户账号"}


def classify_auxiliary(headers):
    """A small explicit relation grammar, not a value-based table classifier."""
    from . import feedback_import
    signature = frozenset(header.strip() for header in headers)
    for role in ("account", "subaccount"):
        if signature == frozenset(feedback_import.SCHEMAS[role]):
            return role
    names = {header.strip() for header in headers if header.strip()}
    if len(names) != len([header for header in headers if header.strip()]):
        return None
    if names & _CHILD and names & _PARENT:
        allowed = _PARENT | _CHILD | _BANK | _CURRENCY | {"余额", "可用余额", "账户余额", "子账户类别", "账户状态", "账户序号", "子账户序号", "钞汇标识", "钞汇标志"}
        return "subaccount" if names <= allowed else None
    if names & _ACCOUNT and names & _CARD and names & _BANK:
        allowed = _ACCOUNT | _CARD | _BANK | _NAME | _CURRENCY | {"开户人证件类型", "开户人证件号码", "账户余额", "可用余额", "账号开户时间", "账户状态", "账户类型"}
        return "account" if names <= allowed else None
    return None


def auxiliary_evidence(headers, cells, role):
    from . import feedback_import, table_import
    signature = frozenset(header.strip() for header in headers)
    eligible = "yes" if signature in {frozenset(columns) for columns in feedback_import.SCHEMAS.values()} else "no"
    if role in {"task", "manifest"}:
        request, bank = _field(headers, cells, {"请求单号"}), _field(headers, cells, {"银行名称"})
        return _json({"role": "manifest", "scope_eligible": eligible, "request": request, "bank": bank}) if request and bank else None
    if role not in {"account", "subaccount"}:
        return None
    bank = _field(headers, cells, _BANK)
    if role == "subaccount":
        parent, account = _field(headers, cells, _PARENT), _field(headers, cells, _CHILD)
        if not parent or not account:
            return None
        value = {"role": role, "scope_eligible": eligible, "parent": parent, "account": account, "bank": bank,
                 "currency": _field(headers, cells, _CURRENCY)}
    else:
        account, card = _field(headers, cells, _ACCOUNT), _field(headers, cells, _CARD)
        if not account and not card:
            return None
        value = {"role": role, "scope_eligible": eligible, "account": account, "card": card, "bank": bank,
                 "name": _field(headers, cells, _NAME), "currency": _field(headers, cells, _CURRENCY)}
    return _json(value)


def transaction_evidence(headers, cells, candidates, result, pair_reader, *, own_side=""):
    endpoints = {}
    for side in ("payer", "payee", "self", "counter"):
        account, card = pair_reader(cells, candidates, side + "_account", [])
        # Explicitly named card columns remain independently typed.
        if side in {"self", "counter"}:
            direct = _field(headers, cells, {item.get("source_name", "") for item in candidates.get(side + "_card", ())})
            card = card or direct
        bank = result.get(side + "_bank", "")
        if side == "self":
            bank = _field(headers, cells, {"本方开户银行", "本方开户行", "账号开户银行", "交易账户开户银行"})
        mixed_headers = {"交易对手账卡号", "交易对方账卡号", "交易对方帐卡号", "对手账卡号", "本方账卡号", "账卡号"}
        identifier_type = ("account_or_card" if any(item.get("source_name", "").strip() in mixed_headers for item in candidates.get(side + "_account", ())) else "account" if account else "card")
        endpoints[side] = {"account": _text(account), "card": _text(card), "identifier_type": identifier_type, "bank": _text(bank),
                           "name": _text(result.get(side + "_name", ""))}
    own = result.get("self_account") or ""
    if own:
        if own_side in {"payer", "payee"}:
            side = own_side
            copy_type = not endpoints[side]["account"] and bool(endpoints["self"]["account"]) or not any(endpoints[side][key] for key in ("account", "card"))
            for key, value in endpoints["self"].items():
                if key != "identifier_type":
                    endpoints[side][key] = endpoints[side][key] or value
            if copy_type:
                endpoints[side]["identifier_type"] = endpoints["self"]["identifier_type"]
            other = "payee" if side == "payer" else "payer"
            copy_type = not endpoints[other]["account"] and bool(endpoints["counter"]["account"]) or not any(endpoints[other][key] for key in ("account", "card"))
            for key, value in endpoints["counter"].items():
                if key != "identifier_type":
                    endpoints[other][key] = endpoints[other][key] or value
            if copy_type:
                endpoints[other]["identifier_type"] = endpoints["counter"]["identifier_type"]
    # The parser can select a supported explicit party field after completing
    # its roles. Retain that account only if the typed pair has no card fallback.
    for side in ("payer", "payee", "self"):
        if not endpoints[side]["account"] and not endpoints[side]["card"]:
            endpoints[side]["account"] = _text(result.get(side + "_account", ""))
            endpoints[side]["identifier_type"] = "account"
    from . import table_import
    signature = frozenset(header.strip() for header in headers)
    eligible = "yes" if len(headers) == len(signature) and signature in {
        table_import.BANK_FEEDBACK_32_COLUMNS, table_import.BANK_FEEDBACK_31_COLUMNS} else "no"
    return _json({"role": "transaction", "scope_eligible": eligible, "own_side": own_side, "endpoints": {side: endpoints[side] for side in ("payer", "payee", "self")}})


def feedback_evidence(raw, row, context):
    own = {"account": _text(raw["交易账号"]), "card": _text(raw["交易卡号"]), "identifier_type": "account" if raw["交易账号"] else "card", "bank": "", "name": ""}
    other = {"account": _text(raw["交易对手账卡号"]), "card": "", "identifier_type": "account_or_card", "bank": _text(raw["对手开户银行"]), "name": _text(raw["对手户名"])}
    payer, payee = (other, own) if context[2] == "in" else (own, other)
    return _json({"role": "transaction", "scope_eligible": "yes", "own_side": "payee" if context[2] == "in" else "payer", "endpoints": {"payer": payer, "payee": payee, "self": own}})


def validate_evidence(value):
    """Closed portable journal schema, used by package admission as well."""
    if not isinstance(value, str) or len(value.encode()) > 8192:
        raise ValueError("invalid identity evidence")
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError("invalid identity evidence")
    role = data.get("role")
    expected = ({"role", "endpoints", "own_side"} if role == "transaction" else
                {"role", "account", "card", "bank", "name", "currency"} if role == "account" else
                {"role", "parent", "account", "bank", "currency"} if role == "subaccount" else {"role", "request", "bank"} if role == "manifest" else set())
    expected |= {"scope_eligible"} if expected else set()
    if not expected or set(data) != expected or data.get("scope_eligible") not in {"yes", "no"}:
        raise ValueError("invalid identity evidence role")
    values = [data]
    if role == "transaction":
        if data["own_side"] not in {"payer", "payee", ""}:
            raise ValueError("invalid own endpoint role")
        endpoints = data["endpoints"]
        if not isinstance(endpoints, dict) or set(endpoints) != {"payer", "payee", "self"}:
            raise ValueError("invalid identity endpoints")
        values = list(endpoints.values())
        if any(not isinstance(item, dict) or set(item) != {"account", "card", "bank", "name", "identifier_type"} for item in values):
            raise ValueError("invalid identity endpoint")
        if any(item["identifier_type"] not in {"account", "card", "account_or_card"} for item in values):
            raise ValueError("invalid identity type")
    if any(not isinstance(value, str) or value != _text(value)
           for item in values for key, value in item.items() if key not in {"endpoints"}):
        raise ValueError("invalid identity text")
    return data


def occurrence_key(member_sha, position):
    return hashlib.sha256(_json([POLICY, member_sha, position]).encode()).hexdigest()


def bind_occurrences(plan, *, operation_scopes=False):
    from .import_staging import auxiliary_records, row_records, bind_occurrence_fields
    bind_families(plan)
    members = {member["member_path"]: member["sha256"] for member in plan["members"]}
    declarations = defaultdict(set)
    for record in auxiliary_records(plan):
        if record["disposition"] != "auxiliary" or DETAIL_FIELD not in record["details"]:
            continue
        data = _projection_evidence(validate_evidence(record["details"][DETAIL_FIELD]))
        if data["role"] == "manifest" and data["scope_eligible"] == "yes":
            declarations[record["details"]["evidence_family"]].add((data["request"], data["bank"]))
    bindings = {}
    denied = set()
    for record in auxiliary_records(plan):
        if record["disposition"] != "auxiliary" or DETAIL_FIELD not in record["details"]:
            continue
        data = _projection_evidence(validate_evidence(record["details"][DETAIL_FIELD]))
        if data["role"] in {"account", "subaccount"} and data["scope_eligible"] == "yes":
            family = record["details"]["evidence_family"]
            binding = source_scope(record["member_path"], data["role"], declarations[family])
            if binding and data["bank"] and data["bank"] != binding["bank"]:
                denied.add((family, binding["request"], binding["bank"]))
    unscoped = {}
    pairs = ((row, record, kind) for kind in ("rows", "cancelled_rows")
             for row, record in row_records(plan, kinds=(kind,)))
    for row, record, kind in pairs:
        value = record["details"].get(DETAIL_FIELD)
        data = _projection_evidence(validate_evidence(value)) if value else None
        family = record["details"]["evidence_family"]
        key = family, record["member_path"]
        if key not in bindings:
            bindings[key] = source_scope(record["member_path"], "transaction", declarations[family])
        binding = bindings[key] if data and data.get("scope_eligible") == "yes" else None
        if binding and (family, binding["request"], binding["bank"]) in denied:
            binding = None
        own_bank = data["endpoints"]["self"]["bank"] if data and data["role"] == "transaction" else ""
        scope = own_bank or (binding["bank"] if binding else "")
        if operation_scopes and kind == "rows" and not scope:
            scope = _operation_scope(record, unscoped)
        bind_occurrence_fields(row, record,
            occurrence_key(members[record["member_path"]], record["source_row"]),
            scope)
    return plan


def _entity_id(bank, kind, number):
    return "fte_" + hashlib.sha256(_json([POLICY, bank, kind, number]).encode()).hexdigest()[:32]


# Bound both the blocking sort and DuckDB's result buffer. A fetchmany call
# alone does not bound a query that has already materialized every audit note.
REPLAY_CHUNK = 4096
_EVIDENCE_RANGES = f"""select r.source_id,min(r.record_no),max(r.record_no)
        from import_records r join import_sources s on s.case_id=r.case_id and s.source_id=r.source_id
        join evidence_sources b on b.case_id=r.case_id and b.source_id=r.source_id and b.active
        where r.case_id=? and s.parser_version in ({_PROFILE_SQL})
        and r.disposition in ('auxiliary','imported','duplicate')"""
_EVIDENCE_QUERY = """select m.sha256,r.source_row,r.details_json,f.flow_id,r.member_path
        from import_records r join import_sources s on s.case_id=r.case_id and s.source_id=r.source_id
        join evidence_sources b on b.case_id=r.case_id and b.source_id=r.source_id and b.active
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
        left join (select * from flow_sources where case_id=$case_id and source_id=$source_id
                   and source_row between $first and $last) f
            on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no
        where r.case_id=$case_id and s.parser_version in (select unnest($profiles))
        and r.disposition in ('auxiliary','imported','duplicate')
        and r.source_id=$source_id and r.record_no between $first and $last"""


class _ResolutionStaging:
    """Operation-local indexes for flow-sized replay state, never case state."""
    def __init__(self):
        from .import_staging import PlanStore
        self.owner = PlanStore()
        try:
            self.connection = self.owner._con
            self.connection.executescript("""
                create table resolution_seen(namespace integer, identity blob,
                    primary key(namespace,identity)) without rowid;
                create table resolution_endpoints(flow_id integer,role text,entity_id text,state text,reason text,
                    endpoint_account text,endpoint_card text,card_state text,
                    primary key(flow_id,role)) without rowid;
            """)
        except BaseException:
            self.owner.close()
            raise
        self._namespace = 0
        self.pending = OrderedDict()
        self.peak_pending = 0
        self.seen_inserts = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.pending.clear()
        self.owner.close()

    @contextmanager
    def seen(self):
        self._namespace += 1
        namespace = self._namespace
        staging = self
        class Seen:
            def admit(self, identity):
                scope, position, flow_id, digest = identity
                key = _json([scope, position, flow_id, digest.hex()]).encode()
                staging.seen_inserts += 1
                return bool(staging.connection.execute(
                    "insert or ignore into resolution_seen values (?,?)", [namespace, key]).rowcount)
        try:
            yield Seen()
        finally:
            if not self.owner._closed:
                self.connection.execute("delete from resolution_seen where namespace=?", [namespace])

    def get(self, key):
        if key in self.pending:
            return self.pending[key]
        return self.connection.execute("select entity_id,state,reason,endpoint_account,endpoint_card,card_state from resolution_endpoints where flow_id=? and role=?", key).fetchone()

    def __setitem__(self, key, value):
        from .import_staging import PLAN_CHUNK
        self.pending[key] = (*value, "", "", "missing") if len(value) == 3 else value
        self.peak_pending = max(self.peak_pending, len(self.pending))
        if len(self.pending) >= PLAN_CHUNK:
            self.flush()

    def setdefault(self, key, value):
        previous = self.get(key)
        if previous is None:
            self[key] = value
            return value
        return previous

    def flush(self):
        if self.pending:
            self.connection.executemany("insert or replace into resolution_endpoints values (?,?,?,?,?,?,?,?)",
                ((*key, *value) for key, value in self.pending.items()))
            self.pending.clear()

    def rows(self):
        self.flush()
        return _EndpointRows(self)


class _EndpointRows(list):
    """Replayable ordered JSON array with no resident list of endpoint dicts.

    JSONEncoder.iterencode's list protocol uses __iter__; the empty backing
    list is intentional. Persistence consumes this stream only after hashing
    and the audit snapshot have both finished.
    """
    def __init__(self, staging):
        super().__init__()
        self.staging = staging
        self.count = staging.connection.execute("select count(*) from resolution_endpoints").fetchone()[0]

    def __len__(self):
        return self.count

    def __iter__(self):
        cursor = self.staging.connection.execute(
            "select flow_id,role,entity_id,state,reason,endpoint_account,endpoint_card,card_state from resolution_endpoints order by flow_id,role")
        try:
            for flow_id, role, entity_id, state, reason, account, card, card_state in cursor:
                yield {"flow_id": flow_id, "role": role, "entity_id": entity_id, "state": state, "reason": reason,
                       "endpoint_account": account, "endpoint_card": card, "card_state": card_state}
        finally:
            if not self.staging.owner._closed:
                cursor.close()

    def clear(self):
        self.staging.connection.execute("delete from resolution_endpoints")
        self.count = 0

class _CaseEvidence:
    """Re-read the same transaction snapshot without retaining decoded rows."""
    def __init__(self, case_id):
        self.case_id = case_id
        self.record_count = self.auxiliary_count = 0
        self.staging = None

    def __iter__(self):
        return self._iterate(False)

    def auxiliary(self):
        return self._iterate(True)

    def _iterate(self, auxiliary_only):
        auxiliary_filter = " and r.disposition='auxiliary'" if auxiliary_only else ""
        # Integer buckets also handle sparse source positions without stepping
        # through empty ranges. Only keys and two bounds are materialized here.
        ranges = db.con().execute(_EVIDENCE_RANGES + auxiliary_filter
            + f" group by r.source_id,r.record_no // {REPLAY_CHUNK} order by r.source_id,min(r.record_no)",
            [self.case_id, *sorted(PROFILES)]).fetchall()
        query = _EVIDENCE_QUERY + auxiliary_filter + " order by r.source_id,r.record_no"
        namespaces, records, auxiliary = {}, 0, 0
        with self.staging.seen() if self.staging is not None else nullcontext(set()) as seen:
            for source, first, last in ranges:
                cursor = db.con().execute(query, {"case_id": self.case_id, "source_id": source,
                    "first": first, "last": last, "profiles": sorted(PROFILES)})
                rows = cursor.fetchall()
                for source_id, position, details, flow_id, member_path in rows:
                    metadata = json.loads(details)
                    value = metadata.get(DETAIL_FIELD)
                    if not value:
                        continue
                    data = validate_evidence(value)
                    data["_member_path"] = member_path
                    data["_family"] = metadata.get("evidence_family", "")
                    namespace_key = (data["_family"], metadata.get("family_member_sha256", member_path), source_id)
                    if namespace_key not in namespaces:
                        namespaces[namespace_key] = hashlib.sha256(_json(namespace_key).encode()).hexdigest()
                    scope = namespaces[namespace_key]
                    identity = (scope, position, flow_id, hashlib.sha256(value.encode()).digest())
                    if self.staging is not None:
                        if not seen.admit(identity):
                            continue
                    else:
                        if identity in seen:
                            continue
                        seen.add(identity)
                    records += 1
                    auxiliary += data["role"] != "transaction"
                    yield scope, position, flow_id, data
                del rows
        if not auxiliary_only:
            self.record_count, self.auxiliary_count = records, auxiliary


def _load(case_id):
    return _CaseEvidence(case_id)


def _compute(evidence, *, collect_endpoints=True, staging=None):
    """Replay all current declarations, so late conflicts revoke earlier links."""
    if isinstance(evidence, _CaseEvidence):
        evidence.staging = staging
    evidence = contextualize(evidence)
    card_targets, scoped_card_targets, parent_claims = defaultdict(set), defaultdict(set), []
    declarations = []
    explicit_accounts, source_conflicts = set(), 0
    for source_id, position, flow_id, data in evidence:
        source_conflicts += bool(data.get("_source_scope_conflict"))
        if data["role"] == "account":
            declarations.append((source_id, data))
        elif data["role"] == "subaccount":
            parent_claims.append((source_id, data))
        elif data["role"] == "transaction":
            explicit_accounts.update((item["bank"], item["account"]) for item in data["endpoints"].values()
                                     if item["bank"] and item["account"] and item["identifier_type"] == "account")
    for scope, item in declarations:
        possible = {item["bank"]} if item["bank"] else {"unscoped:" + scope}
        if item["account"] and item["card"] and len(possible) == 1 and not item.get("_source_scope_conflict"):
            bank = next(iter(possible))
            card_targets[bank, item["card"]].add(item["account"])
            context = _scoped_relation_context(item, bank)
            if context is not None:
                scoped_card_targets[context, item["card"]].add(item["account"])
    parents, subaccounts, parent_accounts, incomplete_parents = defaultdict(set), set(), set(), set()
    for scope, item in parent_claims:
        possible = {item["bank"]} if item["bank"] else {"unscoped:" + scope}
        if len(possible) == 1:
            bank = next(iter(possible))
            if item["account"]:
                subaccounts.add((bank, item["account"]))
            if item["parent"]:
                parent_accounts.add((bank, item["parent"]))
                if item["account"]:
                    parents[bank, item["account"]].add(item["parent"])
                else:
                    incomplete_parents.add((bank, item["parent"]))
    typed_accounts = {(item["bank"] or "unscoped:" + scope, item["account"]) for scope, item in declarations if item["account"] and item["account"] != item["card"]}
    typed_accounts.update(subaccounts)
    typed_accounts.update(parent_accounts)
    typed_accounts.update(explicit_accounts)
    child_parents = { (bank, parent) for (bank, child), values in parents.items() for parent in values }
    entities = {}
    def entity(bank, kind, number):
        if not number:
            raise ValueError("empty typed entity identifier")
        key = _entity_id(bank, kind, number)
        target = entities.setdefault(key, {"entity_id": key, "kind": kind, "account_number": number,
                   "display_account": number, "bank": "" if bank.startswith("unscoped:") else bank, "cards": set(), "reliable_cards": set(), "parent_entity_id": None,
                   "names": set(), "status": "unscoped" if bank.startswith("unscoped:") else "resolved"})
        return target
    for scope, item in declarations:
        possible = {item["bank"]} if item["bank"] else {"unscoped:" + scope}
        bank = next(iter(possible)) if len(possible) == 1 else "unscoped:" + scope
        if item["account"]:
            target = entity(bank, "subaccount" if (bank, item["account"]) in subaccounts else "account", item["account"])
            if item["card"]:
                target["cards"].add(item["card"])
                if (not bank.startswith("unscoped:") and not item.get("_source_scope_conflict")
                        and card_targets.get((bank, item["card"])) == {item["account"]}):
                    target["reliable_cards"].add(item["card"])
                if item.get("_source_scope_conflict"):
                    target["status"] = "conflict"
            if item["name"]:
                target["names"].add(item["name"])
    for bank, parent in incomplete_parents:
        entity(bank, "account", parent)
    for bank, child in subaccounts:
        entity(bank, "subaccount", child)
    for (bank, child), targets in parents.items():
        target = entity(bank, "subaccount", child)
        if len(targets) == 1:
            parent = entity(bank, "account", next(iter(targets)))
            target["parent_entity_id"] = parent["entity_id"]
        else:
            target["status"] = "conflict"
    projections, conflicts, unresolved = (staging if staging is not None else {}), source_conflicts, 0
    endpoint_count = 0
    # Read proven scoped role bindings before weaker duplicate observations.
    # Otherwise weak-card + explicit-account may create an irreversible source
    # conflict before their retained scoped declaration arrives. Replay twice
    # instead of keeping a flow-sized Python cache or guessing out of conflicts.
    for scoped_first in ((True, False) if collect_endpoints else (False,)):
        for source_id, position, flow_id, data in evidence:
            if flow_id is None or data["role"] != "transaction":
                continue
            for role, item in data["endpoints"].items():
                role = _ENDPOINT_ROLES.get(role, role)
                number, card, bank = item["account"], item["card"], item["bank"]
                if not number and not card:
                    if scoped_first:
                        continue
                    if role != "self":
                        if collect_endpoints:
                            projections.setdefault((flow_id, role), ("", "identifier_unresolved", "source_identifier_not_resolved", "", "", "missing"))
                        else:
                            endpoint_count += 1
                            unresolved += 1
                    continue
                if not bank:
                    bank = "unscoped:" + source_id
                mixed = item["identifier_type"] == "account_or_card"
                ambiguity = False
                reason = "explicit_account" if number else "card_only"
                if mixed:
                    carrier = number or card
                    direct = {carrier} if (bank, carrier) in typed_accounts else set()
                    linked = card_targets.get((bank, carrier), set())
                    candidates = direct | linked
                    if len(candidates) == 1 and (bank, next(iter(candidates))) not in child_parents and (not linked or next(iter(candidates)) != carrier):
                        number, card, reason = next(iter(candidates)), carrier if linked else "", "typed_identifier_relation"
                    elif not direct and linked:
                        number, card, reason = "", carrier, "card_ledger_undetermined"
                    else:
                        number, card, reason, ambiguity = carrier, "", "mixed_identifier_undetermined", True
                scoped_equal = False
                if not number:
                    targets = card_targets.get((bank, card), set())
                    context = _scoped_relation_context(data, bank)
                    scoped_targets = scoped_card_targets.get((context, card), set()) if context is not None else set()
                    # Text equality has no identity authority. Only a native,
                    # manifest-bound typed declaration in this exact source scope
                    # can supply the missing account when its two fields coincide.
                    # An explicitly declared account can also be a parent. This
                    # establishes that account only, never one of its child ledgers.
                    if not item["account"] and item["card"] and scoped_targets == {card}:
                        number, reason, scoped_equal = card, _SCOPED_EQUAL_REASON, True
                    elif len(targets) == 1 and next(iter(targets)) != card and (bank, next(iter(targets))) not in child_parents:
                        number, reason = next(iter(targets)), "account_card_declaration"
                if collect_endpoints and (reason == _SCOPED_EQUAL_REASON) != scoped_first:
                    continue
                kind = "identifier" if ambiguity else "subaccount" if (bank, number) in subaccounts else "account" if number else "card"
                target = entity(bank, kind, number or card)
                if card:
                    target["cards"].add(card)
                if item["name"]:
                    target["names"].add(item["name"])
                # Contradictory explicit C/A must not rewrite A to a declared parent.
                # The allowed parent->child edge explains only an already supplied A.
                targets = scoped_targets if scoped_equal else card_targets.get((bank, card), set()) if card else set()
                allowed = targets | {child for (b, child), ps in parents.items() if b == bank and ps & targets}
                conflict = bool(number and targets and number not in allowed) or mixed and ambiguity and bool(card_targets.get((bank, number), set()))
                state = "conflict" if conflict else "unscoped" if bank.startswith("unscoped:") else "identifier_undetermined" if kind == "identifier" else "card_only" if kind == "card" else "resolved"
                if conflict or target["status"] != "conflict" and state != "resolved":
                    target["status"] = state
                if not collect_endpoints:
                    endpoint_count += 1
                    conflicts += state == "conflict"
                    unresolved += state != "resolved"
                    continue
                card_state = ("missing" if not card else "conflict" if conflict or len(targets) > 1 or data.get("_source_scope_conflict")
                              else "confirmed" if state == "resolved" else "unresolved")
                observed = (item["account"], card, card_state)
                key = (flow_id, role)
                existing = projections.get(key)
                if existing is None:
                    projections[key] = (target["entity_id"], state, reason, *observed)
                elif existing[0] != target["entity_id"]:
                    previous = entities.get(existing[0], {})
                    # A duplicate source without an account declaration is absence
                    # of support, not contrary evidence. Supersede only a typed
                    # card-only endpoint for this identical observation and role,
                    # with the same explicit institution and card. Unknown banks,
                    # conflicting cards and other account claims stay unresolved.
                    if (_same_scoped_card_observation(existing, previous, observed, target)
                            and state == "resolved" and reason == _SCOPED_EQUAL_REASON):
                        projections[key] = (target["entity_id"], state, reason, *observed)
                    elif (_same_scoped_card_observation((target["entity_id"], state, reason, *observed), target,
                                                        existing[3:], previous)
                            and existing[1] == "resolved" and existing[2] == _SCOPED_EQUAL_REASON):
                        pass
                    else:
                        uncertain = entity("uncertain:" + str(flow_id) + ":" + role, "identifier", item["account"] or item["card"])
                        uncertain["status"] = "conflict"
                        projections[key] = (uncertain["entity_id"], "conflict", "conflicting_source_context", "", "", "conflict")
                else:
                    old_account, old_card, old_card_state = existing[3:]
                    account = min(value for value in (old_account, item["account"]) if value) if old_account or item["account"] else ""
                    cards_disagree = bool(old_card and card and old_card != card)
                    unresolved_disagreement = old_card_state == "conflict" and not old_card or card_state == "conflict" and not card
                    merged_card = "" if cards_disagree or unresolved_disagreement else old_card or card
                    merged_card_state = ("conflict" if cards_disagree or "conflict" in (old_card_state, card_state)
                                         else "confirmed" if "confirmed" in (old_card_state, card_state)
                                         else "unresolved" if merged_card else "missing")
                    merged_state = "conflict" if "conflict" in (state, existing[1]) else existing[1]
                    merged_reason = _SCOPED_EQUAL_REASON if _SCOPED_EQUAL_REASON in (reason, existing[2]) else min(reason, existing[2])
                    projections[key] = (existing[0], merged_state, merged_reason, account, merged_card, merged_card_state)
    endpoints = _materialize_endpoints(projections) if collect_endpoints else endpoint_count
    del projections
    if collect_endpoints:
        for projection in endpoints:
            if projection["card_state"] == "confirmed" and projection["entity_id"] in entities:
                entities[projection["entity_id"]]["reliable_cards"].add(projection["endpoint_card"])
        projection = None  # Do not retain the final streamed endpoint during later passes.
        conflicts += sum(item["state"] == "conflict" for item in endpoints)
        unresolved = sum(item["state"] != "resolved" for item in endpoints)
    for target in entities.values():
        target["cards"] = sorted(target["cards"])
        target["reliable_cards"] = sorted(target["reliable_cards"])
        target["names"] = sorted(target["names"])
        # Use the typed ledger number. Source associations do not establish
        # which card is current; a sorted card list is not a temporal history.
        target["name_conflict"] = len(target["names"]) > 1
        if target["name_conflict"] and target["status"] == "resolved":
            target["status"] = "name_conflict"
    return sorted(entities.values(), key=lambda item: item["entity_id"]), endpoints, conflicts, unresolved


def _same_scoped_card_observation(weak, weak_entity, strong_observed, strong_entity):
    return (weak[1] == "card_only" and weak[3] == "" and weak[5] == "unresolved"
            and weak_entity.get("kind") == "card" and strong_entity.get("kind") in {"account", "subaccount"}
            and bool(weak_entity.get("bank")) and weak_entity["bank"] == strong_entity.get("bank")
            and bool(weak[4]) and weak[4] == strong_observed[1] == weak_entity.get("account_number") == strong_entity.get("account_number")
            and strong_observed[2] == "confirmed")


def _materialize_endpoints(projections):
    if isinstance(projections, _ResolutionStaging):
        return projections.rows()
    # Replay keeps a compact tuple per (flow, role), not millions of repeated
    # dictionaries and JSON-decoded role strings. Expand after the replay's
    # deduplication set is released, consuming keys and values as we go.
    keys, endpoints = sorted(projections), []
    for index, key in enumerate(keys):
        entity_id, state, reason, account, card, card_state = projections.pop(key)
        flow_id, role = key
        keys[index] = None
        endpoints.append({"flow_id": flow_id, "role": role, "entity_id": entity_id,
                          "state": state, "reason": reason, "endpoint_account": account, "endpoint_card": card, "card_state": card_state})
    projections.clear()
    return endpoints


def rebuild(case_id):
    """Called inside the importing/deleting transaction; no partial revision."""
    evidence = _load(case_id)
    if (isinstance(evidence, _CaseEvidence) or not evidence) and not db.q_raw(f"select 1 from import_sources where case_id=? and parser_version in ({_PROFILE_SQL}) limit 1", [case_id, *sorted(PROFILES)]):
        return None
    with _ResolutionStaging() as staging:
        return _rebuild(case_id, evidence, staging)


def _rebuild(case_id, evidence, staging):
    entities, endpoints, conflicts, unresolved = _compute(evidence, staging=staging)
    summary = {"policy": POLICY, "entities": len(entities),
               "resolved_endpoints": len(endpoints) - unresolved, "unresolved_endpoints": unresolved,
               "conflicts": conflicts,
               "auxiliary_records": evidence.auxiliary_count if isinstance(evidence, _CaseEvidence) else sum(data["role"] != "transaction" for *_, data in evidence),
               "evidence_records": evidence.record_count if isinstance(evidence, _CaseEvidence) else len(evidence),
               "note": "仅使用明确类型的来源声明；未提供机构、仅卡及矛盾关系保留局部缺口，不按姓名或余额合并。"}
    # Raw evidence has served all decisions and summary counts. Retaining the
    # decoded journal during hashing and persistence duplicates the entire case.
    del evidence
    subject_aliases = _subject_aliases(entities)
    # A revision identifies the entire immutable response, including evidence
    # counts that can change without changing the resolved entity projection.
    revision = _hash_json([summary, entities, endpoints])
    summary["revision"] = revision
    # The caller holds the case-write transaction/lock. Reuse an immutable
    # revision already present; a new one needs no wide ON CONFLICT pipeline.
    retained = db.q_raw("select 1 from resolution_revisions where case_id=? and revision=?", [case_id, revision])
    audit = nullcontext(None) if retained else _decision_snapshot({"entities": entities, "endpoints": endpoints})
    with audit as snapshot:
        db.x("delete from resolved_endpoints where case_id=?", [case_id])
        db.x("delete from resolution_entities where case_id=?", [case_id])
        db.x("delete from resolution_subject_aliases where case_id=?", [case_id])
        if entities:
            _insert_projection("resolution_entities", ["case_id", "entity_id", "kind", "account_number", "display_account", "bank", "details_json"],
                               ([case_id, item["entity_id"], item["kind"], item["account_number"], item["display_account"], item["bank"], _json(item)] for item in _consume_projection(entities)))
        if endpoints:
            _insert_projection("resolved_endpoints", ["case_id", "flow_id", "role", "entity_id", "state", "reason", "endpoint_account", "endpoint_card", "card_state"],
                               ([case_id, item["flow_id"], item["role"], item["entity_id"], item["state"], item["reason"], item["endpoint_account"], item["endpoint_card"], item["card_state"]] for item in _consume_projection(endpoints)))
        if subject_aliases:
            _insert_projection("resolution_subject_aliases", ["case_id", "raw_account", "account"],
                               ([case_id, alias, account] for alias, account in _consume_projection(subject_aliases)))
        # Release the Python projection before the engine loads its immutable
        # audit snapshot. The VARCHAR itself still scales with the case size.
        del entities, endpoints, subject_aliases
        if snapshot is not None:
            inserted = db.q_raw("""insert into resolution_revisions(case_id,revision,summary_json,decisions_json)
                select $case_id,$revision,$summary,
                    case when filename=$filename and size=$size and sha256(content)=$sha256
                         then content else error('Resolution snapshot integrity check failed') end
                from read_text($source) returning revision""",
                {"case_id": case_id, "revision": revision, "summary": _json(summary), **snapshot})
            if inserted != [{"revision": revision}]:
                raise ValueError("Resolution snapshot was not persisted")
    db.x("insert into resolution_state(case_id,revision) values (?,?) on conflict(case_id) do update set revision=excluded.revision", [case_id, revision])
    db.x("insert into resolution_projection_versions values (?,?) on conflict(case_id) do update set card_version=excluded.card_version",
         [case_id, CARD_PROJECTION])
    return summary


def _json_chunks(value):
    """The existing canonical UTF-8 JSON protocol, without a whole-case str."""
    pieces, characters = [], 0
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    for piece in encoder.iterencode(value):
        pieces.append(piece)
        characters += len(piece)
        if characters >= 65536:
            yield "".join(pieces).encode("utf-8")
            pieces.clear()
            characters = 0
    if pieces:
        yield "".join(pieces).encode("utf-8")


def _hash_json(value):
    digest = hashlib.sha256()
    for chunk in _json_chunks(value):
        digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _decision_snapshot(value):
    # A closed named file can be reopened on Windows. The private directory is
    # owned only by this operation; failures clean it before the caller commits.
    with tempfile.TemporaryDirectory(prefix="fundstrace-resolution-", dir=db.DATA_DIR) as directory:
        with tempfile.NamedTemporaryFile(mode="wb", prefix="decisions-", suffix=".json",
                                         dir=directory, delete=False) as output:
            size, digest = 0, hashlib.sha256()
            for chunk in _json_chunks(value):
                if output.write(chunk) != len(chunk):
                    raise OSError("Incomplete resolution snapshot write")
                size += len(chunk)
                digest.update(chunk)
            filename = Path(output.name).as_posix()
        del value
        yield {"filename": filename, "source": glob.escape(filename),
               "size": size, "sha256": digest.hexdigest()}


def _consume_projection(values):
    # The immutable audit and revision are already complete. Release each
    # Python row as its database batch is formed, instead of retaining the
    # entire object graph while the native projection grows.
    if isinstance(values, _EndpointRows):
        yield from values
        values.clear()
        return
    for index, value in enumerate(values):
        values[index] = None
        yield value
    values.clear()


def _insert_projection(table, columns, values):
    """Bound parameter construction while keeping the caller's transaction."""
    from pandas import DataFrame
    values = iter(values)
    while True:
        batch = list(islice(values, WRITE_CHUNK))
        if not batch:
            return
        db.insert_dataframe(table, DataFrame(batch, columns=columns), columns)
        del batch


def _subject_aliases(entities):
    """Current typed references only; no historical merge path is authority.

    Card keys carry their original bank/type/number. A card with multiple ledger
    targets, a current card-only node or an identity conflict cannot transfer a
    user declaration to a ledger. Raw declarations remain stored on their key.
    """
    candidates = defaultdict(set)
    for item in entities:
        candidates[item["entity_id"]].add(item["entity_id"])
    for item in entities:
        if not item["bank"] or item["kind"] not in {"account", "subaccount"} or item["status"] not in {"resolved", "name_conflict"}:
            continue
        for kind in ("account", "subaccount"):
            candidates[_entity_id(item["bank"], kind, item["account_number"])].add(item["entity_id"])
        for card in item["cards"]:
            if card != item["account_number"]:
                candidates[_entity_id(item["bank"], "card", card)].add(item["entity_id"])
    return sorted((alias, next(iter(targets))) for alias, targets in candidates.items() if len(targets) == 1)


def ensure_card_projection():
    """Upgrade derived endpoint/display state once when an old DB opens."""
    stale = db.q_raw("""select s.case_id from resolution_state s
        left join resolution_projection_versions v on v.case_id=s.case_id
        where coalesce(v.card_version,'')<>?""", [CARD_PROJECTION])
    if stale:
        with db.transaction():
            for row in stale:
                rebuild(row["case_id"])
        db.con().execute("checkpoint")


def summary(case_id):
    rows = db.q_raw("select r.summary_json from resolution_state s join resolution_revisions r on r.case_id=s.case_id and r.revision=s.revision where s.case_id=?", [case_id])
    return json.loads(rows[0]["summary_json"]) if rows else None


def registry(case_id):
    from .services.cases import require_case
    with db._lock:
        cid = require_case(str(case_id))
        from . import representative_cards
        items = [_display_details(item["details_json"]) for item in db.q_raw(
            "select details_json from resolution_entities where case_id=? order by entity_id", [cid])]
        representative_cards.decorate(cid, items, key="entity_id",
            source_sql="select * from effective_flows where case_id=?", params=[cid])
        for item in items:
            item.pop("reliable_cards", None)
        return {"case_id": str(cid), "resolution": summary(cid), "entities": items}


class _PreviewEvidence:
    # Each pass decodes one entry. State belongs to the short-lived instance;
    # a local heap class would retain its plan through a method closure cycle.
    def __init__(self, plan):
        self.plan = plan
        self.member_scopes = {m["member_path"]: m["sha256"] for m in plan["members"]}
        self.records = self.auxiliary_count = 0
        self.validated = False

    def __iter__(self):
        return self._iterate(False)

    def auxiliary(self):
        return self._iterate(True)

    def _iterate(self, auxiliary_only):
        from .import_staging import auxiliary_records
        records = auxiliary = 0
        for record in auxiliary_records(self.plan) if auxiliary_only else self.plan["records"]:
            if (auxiliary_only and record["disposition"] != "auxiliary"
                    or DETAIL_FIELD not in record["details"]):
                continue
            data = (json.loads(record["details"][DETAIL_FIELD]) if self.validated and not auxiliary_only
                    else validate_evidence(record["details"][DETAIL_FIELD]))
            data["_member_path"] = record["member_path"]
            data["_family"] = record["details"].get("evidence_family", "")
            records += 1
            auxiliary += data["role"] != "transaction"
            yield (self.member_scopes[record["member_path"]], record["record_no"],
                   record["record_no"] if record["disposition"] == "accepted" else None, data)
        if not auxiliary_only:
            self.records, self.auxiliary_count, self.validated = records, auxiliary, True


def preview(plan):
    if plan["profile"] not in PROFILES:
        return None
    evidence = _PreviewEvidence(plan)
    entities, endpoints, conflicts, unresolved = _compute(evidence, collect_endpoints=False)
    return {"policy": POLICY, "revision": None, "entities": len(entities), "resolved_endpoints": endpoints - unresolved,
            "unresolved_endpoints": unresolved, "conflicts": conflicts,
            "auxiliary_records": evidence.auxiliary_count, "evidence_records": evidence.records,
            "note": "预览本次来源声明；提交时结合案件已保留资料原子重算。"}


def _display_details(value):
    item = json.loads(value)
    # Old derived records may retain the former first-card label. Their typed
    # identity and source history are unchanged; display never implies expiry.
    item["display_account"] = item["account_number"]
    return item


def decorate_accounts(case_id, rows, *, key="account", source_sql=None, params=None):
    """Keep query keys opaque while exposing original display identifiers."""
    ids = sorted({row.get(key) for row in rows if row.get(key)})
    if not ids:
        return rows
    placeholders = ",".join("?" for _ in ids)
    details = {row["entity_id"]: _display_details(row["details_json"]) for row in db.q_raw(
        f"select entity_id,details_json from resolution_entities where case_id=? and entity_id in ({placeholders})", [case_id, *ids])}
    for row in rows:
        account = row.get(key) or ""
        item = details.get(account)
        if item is None:
            continue
        row.update({field: item[field] if item else (account if field in {"display_account", "account_number"} else "" if field in {"bank", "kind", "status"} else [])
                    for field in ("display_account", "account_number", "bank", "kind", "cards", "names", "status")})
        if item and len(item["names"]) > 1:
            row["name"] = "多个来源户名"
        if item and len(item["names"]) == 1 and not row.get("name"):
            row["name"] = item["names"][0]
    from . import representative_cards
    return representative_cards.decorate(case_id, rows, key=key,
        source_sql=source_sql or "select * from effective_flows where case_id=?",
        params=[case_id] if source_sql is None else params)


def complete_family(source):
    """Opt a complete known bank export into its versioned evidence contract.

    Ordinary standalone tables retain their existing public identifiers. A
    directory selection explicitly chooses this contract, including partial
    evidence uploaded later; a ZIP with complete known relations opts in too.
    This inspection never treats member names as institution declarations.
    """
    import csv
    import io
    import zipfile
    from . import table_archive, feedback_import, table_import, event_resolution
    roles, members, declarations = set(), [], set()
    try:
        source.seek(0)
        with zipfile.ZipFile(source) as archive:
            for info in archive.infolist()[:table_archive.EVIDENCE_MAX_MEMBERS + 1]:
                if not info.is_dir() and info.filename.lower().endswith(".csv") and not info.flag_bits & 1:
                    with archive.open(info) as stream:
                        payload = stream.read(65536)
                        headers, encoding = table_archive._header(payload)
                    signature = frozenset(headers)
                    if all(headers.count(header) == 1 for header in event_resolution.HEADERS.values()):
                        return True
                    for role in ("account", "subaccount", "transaction"):
                        known = {frozenset(feedback_import.SCHEMAS[role])}
                        if role == "transaction":
                            known |= {table_import.BANK_FEEDBACK_31_COLUMNS, table_import.BANK_FEEDBACK_32_COLUMNS}
                        if len(headers) == len(signature) and signature in known:
                            roles.add(role)
                            members.append((info.filename, role))
                    if signature == frozenset(feedback_import.SCHEMAS["task"]) and encoding:
                        # A prefix is sufficient for a header, never for CSV
                        # records: a valid large manifest can end mid-record at
                        # 64 KiB. Read the whole manifest under the new profile's
                        # explicit per-member bound before interpreting it.
                        with archive.open(info) as stream:
                            payload = stream.read(feedback_import.EVIDENCE_MAX_MEMBER_BYTES + 1)
                        if len(payload) > feedback_import.EVIDENCE_MAX_MEMBER_BYTES:
                            return False
                        rows = csv.reader(io.StringIO(payload.decode(encoding), newline=""), strict=True)
                        next(rows)
                        for cells in rows:
                            if len(cells) == len(headers):
                                request = _field(headers, cells, {"请求单号"})
                                bank = _field(headers, cells, {"银行名称"})
                                if request and bank:
                                    declarations.add((request, bank))
        # Preserve ordinary historical ZIP replay unless the source itself
        # carries the closed request/institution contract. Selecting a directory
        # explicitly opts into partial/unscoped evidence without this shortcut.
        return ("transaction" in roles and bool(roles & {"account", "subaccount"})
                and any(role == "transaction" and source_scope(path, role, declarations)
                        for path, role in members))
    except (ValueError, OSError, UnicodeError, csv.Error, zipfile.BadZipFile, RuntimeError):
        return False
    finally:
        source.seek(0)


_SCOPE_POLICY = "feedback-export-request-institution-v1"
_ROLE_SUFFIXES = {"transaction": "交易明细信息.csv", "account": "账户信息.csv", "subaccount": "关联子账户信息.csv"}


def source_scope(member_path, role, declarations):
    """Closed export-family declaration, never an arbitrary folder-name guess.

    The exact 14-column manifest supplies request/institution text. The internal
    parent component must equal its request; a complete member basename must be
    exactly that institution plus the allowlisted, header-verified role suffix.
    A GB18030 alternative is admitted only after strict byte roundtrip and all
    the same manifest/role checks. This is a source declaration, not bank auth.
    """
    if role not in _ROLE_SUFFIXES or not isinstance(member_path, str):
        return None
    alternatives = {member_path}
    try:
        encoded = member_path.encode("cp437", errors="strict")
        decoded = encoded.decode("gb18030", errors="strict")
        if decoded.encode("gb18030") == encoded:
            alternatives.add(decoded)
    except (UnicodeError, LookupError):
        pass
    candidates = set()
    for path in alternatives:
        *parents, basename = path.split("/")
        for request, bank in declarations:
            if request in parents and basename == bank + _ROLE_SUFFIXES[role]:
                candidates.add((request, bank))
    if len(candidates) != 1:
        return None
    request, bank = next(iter(candidates))
    return {"policy": _SCOPE_POLICY, "request": request, "bank": bank, "role": role}


def _scoped_relation_context(data, bank):
    """A native member's family, full request-prefix path and exact bank.

    The binding has already passed the complete manifest/role grammar. Retain
    its full request path as well, so repeated request text under another root
    never supplies a typed relation to this member.
    """
    binding, family = data.get("_source_scope"), data.get("_family")
    if (not binding or binding.get("policy") != _SCOPE_POLICY or not family
            or binding.get("bank") != bank or not bank or data.get("_source_scope_conflict")):
        return None
    path, role = data.get("_member_path"), data.get("role")
    if not isinstance(path, str) or role not in _ROLE_SUFFIXES:
        return None
    alternatives = {path}
    try:
        encoded = path.encode("cp437", errors="strict")
        decoded = encoded.decode("gb18030", errors="strict")
        if decoded.encode("gb18030") == encoded:
            alternatives.add(decoded)
    except (UnicodeError, LookupError):
        pass
    prefixes = set()
    for value in alternatives:
        *parents, basename = value.split("/")
        if basename == bank + _ROLE_SUFFIXES[role]:
            prefixes.update(tuple(parents[:index + 1]) for index, component in enumerate(parents)
                            if component == binding["request"])
    return (family, next(iter(prefixes)), bank) if len(prefixes) == 1 else None


class _ContextualEvidence:
    """Finite source bindings owned by one replay, without a closure cycle."""
    def __init__(self, evidence):
        self.evidence = evidence
        self.declarations, self.bindings, self.denied = defaultdict(set), {}, set()
        auxiliary = getattr(evidence, "auxiliary", None)
        for *_, data in auxiliary() if callable(auxiliary) else evidence:
            data = _projection_evidence(data)
            if data["role"] == "manifest" and data["scope_eligible"] == "yes":
                self.declarations[data.get("_family", "")].add((data["request"], data["bank"]))
        for *_, item in auxiliary() if callable(auxiliary) else evidence:
            item = _projection_evidence(item)
            binding = self._binding_for(item)
            if binding and item["role"] in {"account", "subaccount"} and item["bank"] and item["bank"] != binding["bank"]:
                self.denied.add((item.get("_family", ""), binding["request"], binding["bank"]))

    def _binding_for(self, item):
        if item.get("scope_eligible") != "yes":
            return None
        key = (item.get("_family", ""), item.get("_member_path"), item["role"])
        if key not in self.bindings:
            self.bindings[key] = source_scope(key[1], key[2], self.declarations[key[0]])
        return self.bindings[key]

    def __iter__(self):
        for scope, position, flow_id, data in self.evidence:
            data = _projection_evidence(data)
            binding = self._binding_for(data)
            if binding and (data.get("_family", ""), binding["request"], binding["bank"]) in self.denied:
                data["_source_scope_conflict"] = True
                binding = None
            if binding:
                data["_source_scope"] = binding
                bank = binding["bank"]
                if data["role"] == "transaction":
                    own_roles = ["self"] + ([data["own_side"]] if data["own_side"] else [])
                    for own_role in own_roles:
                        item = data["endpoints"][own_role]
                        if not item["bank"]:
                            item["bank"] = bank
                elif not data["bank"]:
                    data["bank"] = bank
                elif data["bank"] != bank:
                    data["_source_scope_conflict"] = True
            yield scope, position, flow_id, data


def contextualize(evidence):
    """Attach only an explicit institution or the closed export declaration.

    Observing a single candidate bank for an identifier is deliberately absent:
    it says nothing about the source's institution or unseen alternatives.
    """
    return _ContextualEvidence(evidence)


def reject_historical_overlap(case_id, plan):
    """Do not silently count the same member under incompatible old semantics.

    Historical journals can lack the institution/type contract needed to migrate
    a flow safely. Their original rows/receipts remain usable. A new evidence
    import must use a separate case when such a financial occurrence overlaps.
    """
    if not plan or plan["profile"] not in PROFILES or not plan["rows"]:
        return
    digests = sorted({member["sha256"] for member in plan["members"]})
    placeholders = ",".join("?" for _ in digests)
    from .import_staging import record_map
    store = plan.get("_plan_store")
    previous = store.mapping() if store is not None else {}
    cursor = db.con().execute(f"""select distinct m.sha256,r.source_row from import_members m
        join import_records r on r.case_id=m.case_id and r.source_id=m.source_id and r.member_path=m.member_path
        join import_sources s on s.case_id=r.case_id and s.source_id=r.source_id
        join flow_sources fs on fs.case_id=r.case_id and fs.source_id=r.source_id and fs.source_row=r.record_no
        where r.case_id=? and m.sha256 in ({placeholders}) and s.parser_version not in ({_PROFILE_SQL})""", [case_id, *digests, *sorted(PROFILES)])
    while batch := cursor.fetchmany(1024):
        for digest, position in batch:
            previous[digest, position] = True
    members = {member["member_path"]: member["sha256"] for member in plan["members"]}
    records = record_map(plan)
    cursor = db.con().execute(f"""select distinct m.sha256,r.source_row,json_extract_string(r.details_json,'$.occurrence_scope') occurrence_scope
        from import_members m join import_records r on r.case_id=m.case_id and r.source_id=m.source_id and r.member_path=m.member_path
        join import_sources s on s.case_id=r.case_id and s.source_id=r.source_id
        join flow_sources fs on fs.case_id=r.case_id and fs.source_id=r.source_id and fs.source_row=r.record_no
        where r.case_id=? and m.sha256 in ({placeholders}) and s.parser_version in ({_PROFILE_SQL})""", [case_id, *digests, *sorted(PROFILES)])
    prior_scopes = store.mapping() if store is not None else {}
    while batch := cursor.fetchmany(1024):
        for digest, position, scope in batch:
            key = digest, position
            values = prior_scopes.get(key, set())
            values.add(scope or "")
            prior_scopes[key] = values
    if prior_scopes:
        for row in plan["rows"]:
            record = records[row["_source_row"]]
            current_scope = row.get("_occurrence_scope") or ""
            for old_scope in prior_scopes.get((members[record["member_path"]], record["source_row"]), set()):
                if current_scope != old_scope and (not current_scope or not old_scope or current_scope.startswith("unscoped:") or old_scope.startswith("unscoped:")):
                    from .services.cases import ServiceError
                    raise ServiceError(409, "evidence_context_overlap", "相同原件已有未确定的来源范围，本次补充上下文无法安全衔接，未重复入账；请仅补辅助资料，或在新案件导入完整目录")
    if previous and any((members[records[row["_source_row"]]["member_path"]], records[row["_source_row"]]["source_row"]) in previous for row in plan["rows"]):
        from .services.cases import ServiceError
        raise ServiceError(409, "historical_evidence_overlap", "本案已有相同原件的历史资金解释，无法安全自动迁移；请在新案件导入完整目录，原案件和回执已保留")


def decorate_analysis(case_id, result):
    """Display identifiers travel alongside stable account/query keys."""
    registry_items = registry(case_id)["entities"]
    by_id = {item["entity_id"]: item for item in registry_items}
    def label(key):
        return by_id.get(key, {}).get("display_account", key)
    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for item in list(value.values()):
                visit(item)
            for key in ("account", "from", "to", "src", "dst", "from_acct", "to_acct"):
                if isinstance(value.get(key), str):
                    value["display_account" if key == "account" else key + "_display_account"] = label(value[key])
            for key in ("accounts", "src_accounts", "dst_accounts", "subjects"):
                if isinstance(value.get(key), list):
                    value["display_subjects" if key == "subjects" else "display_accounts" if key == "accounts" else key.replace("accounts", "display_accounts")] = [label(item) for item in value[key]]
            for name_key in ("src_name", "dst_name", "from_name", "to_name"):
                if isinstance(value.get(name_key), str) and value[name_key] in by_id:
                    value[name_key] = label(value[name_key])
            if isinstance(value.get("accounts_names"), list):
                value["accounts_names"] = [label(item) for item in value["accounts_names"]]
            if isinstance(value.get("loop_names"), list):
                value["loop_names"] = [label(item) for item in value["loop_names"]]
    visit(result)
    current = summary(case_id)
    if current:
        result["statistics_policy"] = POLICY
        result["resolution_revision"] = current["revision"]
        if "source_snapshot" in result:
            result["source_snapshot"].update(statistics_policy=POLICY, resolution_revision=current["revision"])
    return result


def _operation_scope(record, unscoped):
    # The logical family includes complete member positions and original hashes;
    # it is independent of the current case and its import/withdrawal history.
    key = record["details"]["evidence_family"], record["details"]["family_member_sha256"]
    if key not in unscoped:
        unscoped[key] = "unscoped:" + hashlib.sha256(_json(key).encode()).hexdigest()
    return unscoped[key]


def operation_context(parsed, original_sha):
    from .import_staging import row_records, bind_occurrence_fields
    plan = parsed[0].attrs.get("feedback_plan") or parsed[0].attrs.get("table_plan")
    if not plan or plan["profile"] not in PROFILES:
        return
    unscoped = {}
    for row, record in row_records(plan, kinds=("rows",), missing_scope_only=True):
        if not row.get("_occurrence_scope"):
            # Without an institution declaration, only the original registered
            # envelope and its complete member position attest a repeated copy.
            bind_occurrence_fields(row, record, None, _operation_scope(record, unscoped))


def bind_families(plan):
    """Stable logical original families, independent of ZIP order/timestamps.

    Each innermost physical container supplies one family. A directory's direct
    leaves share its controlled family; nested original containers remain
    separate. Fingerprints retain relative member positions, bytes and roles.
    """
    contexts = plan.get("_member_families", {})
    grouped = defaultdict(list)
    relative = {}
    for member in plan["members"]:
        path = member["member_path"]
        container = contexts.get(path, "")
        within = path[len(container) + 1:] if container and path.startswith(container + "/") else path
        relative[path] = within
        canonical_role = "table" if member["role"] in {"table", "transaction"} else "attachment" if member["role"] == "attachment" else "auxiliary"
        grouped[container].append((within, member["sha256"], canonical_role))
    tokens = {container: hashlib.sha256(_json(["logical-source-family-v1", sorted(members)]).encode()).hexdigest()
              for container, members in grouped.items()}
    member_hashes = {path: hashlib.sha256(within.encode()).hexdigest() for path, within in relative.items()}
    from .import_staging import StagedSequence
    if isinstance(plan["records"], StagedSequence):
        plan["records"].store.set_member_details({path: {
            "evidence_family": tokens[contexts.get(path, "")], "family_member_sha256": member_hashes[path]}
            for path in relative})
        return
    for record in plan["records"]:
        path = record["member_path"]
        record["details"].update(evidence_family=tokens[contexts.get(path, "")],
                                 family_member_sha256=member_hashes[path])
