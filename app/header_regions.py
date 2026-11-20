"""Source-coordinate checks for optional local header proposals."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import re
import time
import unicodedata

from . import header_model


VERSION_V1 = "funds-trace-header-regions-v1"
VERSION_V2 = "funds-trace-header-regions-v2"
VERSION_V3 = "funds-trace-header-regions-v3"
VERSION = VERSION_V3
SUPPORTED_VERSIONS = (VERSION_V1, VERSION_V2, VERSION_V3)
MAX_DECISIONS = 64
MAX_MODEL_CALLS = 8
MAX_COLUMNS = 16
MAX_SOURCE_COLUMNS = 256
OPERATION_SECONDS = 30
SOURCE_SHAPE_LABELS = frozenset({"empty", "binary_digit", "leading_zero", "digits", "letters", "alphanumeric",
                                 "date", "signed_number", "decimal", "other"})
MAX_SHAPES_PER_COLUMN = 5
_TEXT = re.compile(r"[a-z\u3400-\u9fff][a-z\u3400-\u9fff /_()\[\]-]{0,63}\Z")
_COMPOUND = re.compile(r"([^/()\[\]]+)(?:/([^/()\[\]]+)|\(([^/()\[\]]+)\)|\[([^/()\[\]]+)\])\Z")

# Exact parent/leaf pairs prove the source role without a model.
_PARENTS = {
    "交易": "transaction_time", "交易发生": "transaction_time", "transaction": "transaction_time",
    "入账": "posting_time", "记账": "posting_time", "posting": "posting_time",
    "付款方": "payer_account", "付款人": "payer_account", "payer": "payer_account",
    "收款方": "payee_account", "收款人": "payee_account", "payee": "payee_account",
    "本方": "self_account", "本方账户": "self_account", "own": "self_account",
    "对方": "counter_account", "交易对手": "counter_account", "counterparty": "counter_account",
    "本笔发生额": "amount", "本笔发生额复核": "amount", "交易金额": "amount", "transaction amount": "amount",
    "账面余额": "balance", "交易余额": "balance", "book balance": "balance",
}
_PARENT_INITIALS = frozenset(first for name in _PARENTS for first in (name[0], name[0].upper()))
_DATE_LEAVES = frozenset({"日期", "年月日", "日期时间", "时间", "date", "datetime", "time"})
_ACCOUNT_LEAVES = frozenset({"账号", "帐号", "账户号", "账户标识", "account", "account identifier", "account number"})
_UNITS = frozenset({"元", "分", "角", "人民币元", "千元", "万元", "yuan", "cent", "cents", "jiao"})
_TIME_ROLES = frozenset({"transaction_time", "posting_time"})
_ACCOUNT_ROLES = frozenset({"payer_account", "payee_account", "self_account", "counter_account"})
_MONEY_ROLES = frozenset({"amount", "balance"})
_DIRECTIONS = frozenset({"方向", "收付方向", "借贷方向", "收支方向", "direction"})
_DIRECT = {"交易发生日": "transaction_time", "入账日": "posting_time", "账务记账日": "posting_time",
           "本笔发生额": "amount", "本笔发生额复核": "amount", "账面余额": "balance"}
# These public labels support a review hint when paired with an account
# heading, but are not sufficient to assign the parties of a transaction.
_REVIEW_ACCOUNT_ROLES = {"划出一方": "payer_account", "划入一方": "payee_account"}
_REVIEW_OPTIONS = list(_REVIEW_ACCOUNT_ROLES.values())
_RESULT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["header_rows", "columns"], "properties": {
    "header_rows": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "integer", "minimum": 1, "maximum": 2}},
    "columns": {"type": "array", "maxItems": MAX_COLUMNS, "items": {
        "type": "object", "additionalProperties": False, "required": ["column", "role"], "properties": {
            "column": {"type": "integer", "minimum": 1, "maximum": MAX_SOURCE_COLUMNS},
            "role": {"type": "string", "enum": _REVIEW_OPTIONS},
        },
    }},
}}


def _normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def supports_opaque_tiers(version):
    return version in (VERSION_V2, VERSION_V3)


def _text_row(row, source_cell, *, layout_leaf=False, opaque=False):
    if row is None or not 3 <= len(row) <= MAX_SOURCE_COLUMNS:
        return None
    tokens = []
    for cell in row:
        value = source_cell(cell)
        if value is not None and type(value) is not str:
            if not opaque:
                return None
            tokens.append(None)
            continue
        # An unrelated text heading can delimit the region without becoming
        # a role or a model input. Keep the early exit for data-like values.
        token = _normalize(value[:65]) if value else ""
        if token and not token[0].isalpha():
            if not (layout_leaf or opaque):
                return None
            tokens.append(None)
        else:
            tokens.append(token if len(value or "") <= 64 and _TEXT.fullmatch(token) else "")
    return tokens


def _pair(parent, leaf):
    role = _PARENTS.get(parent)
    if (role in _TIME_ROLES and leaf in _DATE_LEAVES
            or role in _ACCOUNT_ROLES and leaf in _ACCOUNT_LEAVES
            or role in _MONEY_ROLES and leaf in _UNITS):
        return role, leaf if role in _MONEY_ROLES else ""
    return None


def _single(token):
    if token in _DIRECT:
        return (token,), (_DIRECT[token], "")
    if match := _COMPOUND.fullmatch(token):
        parent, leaf = match[1].strip(), (match[2] or match[3] or match[4]).strip()
        return (parent, leaf), _pair(parent, leaf)
    for parent in _PARENTS:
        if token.startswith(parent) and (claim := _pair(parent, token[len(parent):].strip())):
            return (parent, token[len(parent):].strip()), claim
    for parent in _REVIEW_ACCOUNT_ROLES:
        if token.startswith(parent) and (leaf := token[len(parent):].strip()) in _ACCOUNT_LEAVES:
            return (parent, leaf), None
    return (token,), None


@dataclass(frozen=True, slots=True)
class HeaderObservation:
    depth: int
    claims: tuple
    open_role: bool
    anchors: frozenset
    review_columns: tuple = ()
    opaque: bool = False

    def proof(self, first_row):
        last_row = first_row + self.depth - 1
        return [{"column_index": index, "field": "trade_time" if role in _TIME_ROLES else role,
                 "role": role, "source_row": last_row,
                 **({"parent_row": first_row} if self.depth == 2 else {}),
                 **({"unit": unit} if unit else {})} for index, _, role, unit in self.claims]


def _two_tier(tokens, leaves, *, opaque=False):
    columns, review_columns, open_role = [], [], False
    for index, (parent, leaf) in enumerate(zip(tokens, leaves), 1):
        if claim := _pair(parent, leaf):
            columns.append((index, (parent, leaf), *claim))
        elif leaf in _DATE_LEAVES | _ACCOUNT_LEAVES | _UNITS or parent in _PARENTS:
            open_role = True
            if leaf in _ACCOUNT_LEAVES and (role := _REVIEW_ACCOUNT_ROLES.get(parent)):
                review_columns.append((index, parent, leaf, role))
    if opaque:
        # Even a damaged key cell can define a boundary when other columns
        # independently supply two kinds of same-column proof. The opaque
        # value supplies no role. An incomplete region will be held for review.
        parent_roles = {_PARENTS.get(token) for token in tokens}
        leaf_terms = set(leaves)
        roles = {role for _, _, role, _ in columns}
        groups = ((_TIME_ROLES, _DATE_LEAVES), (_ACCOUNT_ROLES, _ACCOUNT_LEAVES), (_MONEY_ROLES, _UNITS))
        if (not (None in tokens or None in leaves)
                or not all(parent_roles & domain or leaf_terms & vocabulary for domain, vocabulary in groups)
                or sum(bool(roles & domain) for domain, _ in groups) < 2):
            return None
    elif not any(tokens) or (None in leaves and len(columns) < 2):
        return None
    return HeaderObservation(2, tuple(columns), open_role, frozenset(), tuple(review_columns), opaque)


def possible_tier_parent(row):
    """Avoid inspecting lower tiers or normalizing data cells without a source heading signal."""
    if not 3 <= len(row) <= MAX_SOURCE_COLUMNS:
        return False
    for cell in row:
        if type(cell) is not str or not 0 < len(cell) <= 64:
            continue
        first = cell[0]
        if first.isspace():
            first = cell.lstrip()[:1]
        # ASCII and basic CJK initials need no compatibility normalization.
        # Other short forms, such as fullwidth letters, use the same validator
        # as the actual observation, without normalizing dates or amounts.
        if not first or first.isdecimal() or (first not in _PARENT_INITIALS
                                             and (first.isascii() or "\u3400" <= first <= "\u9fff")):
            continue
        if cell in _PARENTS or _normalize(cell) in _PARENTS:
            return True
    return False


def observe(row, following, source_cell, *, version=VERSION):
    """Detect source headings using original, same-column coordinates."""
    tokens = _text_row(row, source_cell)
    # Two explicit tiers have date, account and denomination vocabulary at
    # fixed leaf coordinates. Empty or opaque parents can delimit the region
    # but cannot prove the roles of their leaves.
    if row is not None and following is not None and len(following) == len(row):
        # Use the same-column pairs to identify the lower tier. Other cells
        # may have numbered labels and do not supply proposal evidence.
        leaves = _text_row(following, source_cell, layout_leaf=True) if tokens is not None else None
        if _leaf_shape(leaves) and (result := _two_tier(tokens, leaves)) is not None:
            return result
        if supports_opaque_tiers(version) and possible_tier_parent(row):
            parents = tokens if tokens is not None else _text_row(row, source_cell, opaque=True)
            leaves = leaves if leaves is not None else _text_row(following, source_cell, opaque=True)
            if (result := _two_tier(parents, leaves, opaque=True)) is not None:
                return result
    if tokens is None:
        return None
    if _leaf_shape(tokens):
        # Standalone generic leaves are still a boundary when every role is
        # unproved. In particular a blank preceding row supplies no parent.
        return HeaderObservation(1, (), True, frozenset({"leaf_only"}))
    columns, review_columns, open_role, structural = [], [], False, 0
    anchors = set()
    for index, token in enumerate(tokens, 1):
        parts, claim = _single(token)
        if claim:
            columns.append((index, parts, *claim))
        if len(parts) == 2:
            parent, leaf = parts
            parent_role = _PARENTS.get(parent)
            if leaf in _DATE_LEAVES or parent_role in _TIME_ROLES:
                anchors.add("time")
            if leaf in _UNITS or parent_role in _MONEY_ROLES:
                anchors.add("money")
            if leaf in _ACCOUNT_LEAVES or parent_role in _ACCOUNT_ROLES:
                anchors.add(("account", index))
            if leaf in _ACCOUNT_LEAVES and (role := _REVIEW_ACCOUNT_ROLES.get(parent)):
                review_columns.append((index, parent, leaf, role))
        if len(parts) == 2 and (parts[0] in _PARENTS or parts[1] in _DATE_LEAVES | _ACCOUNT_LEAVES | _UNITS):
            structural += 1
            open_role |= claim is None
    # A heading may combine exact parser aliases and unfamiliar compounds.
    # Require independent shape anchors before a proposal or a boundary.
    if len(columns) >= 2 or structural >= 3:
        return HeaderObservation(1, tuple(columns), open_role, frozenset(anchors), tuple(review_columns))
    return None


def _leaf_shape(tokens):
    return (tokens and any(token in _DATE_LEAVES for token in tokens) and any(token in _UNITS for token in tokens)
            and (sum(token in _ACCOUNT_LEAVES for token in tokens) >= 2
                 or any(token in _ACCOUNT_LEAVES for token in tokens) and any(token in _DIRECTIONS for token in tokens)))


def _prompt(observation, *, phase=None, shapes=None):
    # Only scoped public vocabulary and enum-shaped evidence leave the parser.
    options = list(reversed(_REVIEW_OPTIONS)) if phase == "recheck" else _REVIEW_OPTIONS
    cells = [{"column": index, "header": [parent, leaf], "candidate_roles": options,
              **({"source_shapes": shapes[index]} if shapes is not None else {})}
             for index, parent, leaf, _ in observation.review_columns]
    payload = {"header_rows": list(range(1, observation.depth + 1)), "columns": cells}
    guidance = ""
    if phase is not None:
        payload["phase"] = phase
    if phase == "recheck":
        unresolved = {index for index, *_ in observation.review_columns}
        neighbors = [{"column": index, "role": role}
                     for index, _, role, _ in observation.claims
                     if index not in unresolved and (index - 1 in unresolved or index + 1 in unresolved)]
        if neighbors:
            payload["verified_neighbors"] = neighbors
            guidance = "verified_neighbors 是原件表头已核验的相邻列，只用于辅助复核。"
    elif phase == "shape":
        guidance = "source_shapes 仅描述当前列的有限值形态，不能单凭形态分配角色。"
    return ("依同列标题词提出供人工核对的账号角色。payer_account 是付款方账号，payee_account 是收款方账号。"
            "仅从该列的 candidate_roles 选择。返回相对 header_rows 和需要核对的 column、role，"
            "不确定的列可以省略。" + guidance + "\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _admitted_suggestions(values, observation, coordinate):
    if type(values) is not list:
        return []
    allowed = {index: role for index, _, _, role in observation.review_columns}
    found = {}
    for value in values:
        if type(value) is not dict:
            continue
        index = value.get(coordinate)
        if type(index) is not int or index not in allowed:
            continue
        role = value.get("role")
        if index in found or set(value) != {coordinate, "role"} or type(role) is not str or role != allowed[index]:
            found[index] = None
        else:
            found[index] = role
    return [{"column_index": index, "role": role} for index, role in sorted(found.items()) if role is not None]


def _proposal_suggestions(proposal, observation):
    if type(proposal) is not dict or set(proposal) != {"header_rows", "columns"}:
        return None
    tiers, columns = proposal["header_rows"], proposal["columns"]
    if (type(tiers) is not list or any(type(row) is not int for row in tiers)
            or tiers != list(range(1, observation.depth + 1)) or type(columns) is not list
            or len(columns) > MAX_COLUMNS):
        return None
    return _admitted_suggestions(columns, observation, "column")


def _scoped_source_shapes(values, columns):
    if type(values) is not dict:
        return {}
    return {index: tuple(sorted(set(labels))) for index, labels in values.items()
            if type(index) is int and index in columns and type(labels) is tuple
            and 0 < len(labels) <= MAX_SHAPES_PER_COLUMN
            and all(type(label) is str and label in SOURCE_SHAPE_LABELS for label in labels)}


class HeaderRegionPolicy:
    """Bounded decisions for one original; replay independently rechecks proof."""

    def __init__(self, cancelled, *, version=VERSION, replay=None):
        self.cancelled = cancelled
        self.version = version
        self.decisions = []
        self.attempted_inference = False
        self._replay = replay
        self._requests = 0
        self._deadline = None

    def _pair_deadline(self):
        now = time.monotonic()
        if self._deadline is None:
            self._deadline = now + OPERATION_SECONDS
        if self._requests + 2 > MAX_MODEL_CALLS or self.cancelled.is_set() or now >= self._deadline:
            return None
        return min(self._deadline, now + header_model.REQUEST_SECONDS)

    def _request_pair(self, observation, *, phase=None, shapes=None):
        deadline = self._pair_deadline()
        if deadline is None:
            return None
        self._requests += 2
        if header_model.configured():
            self.attempted_inference = True
        reply = header_model.local_model.suggest(_prompt(observation, phase=phase, shapes=shapes), _RESULT_SCHEMA,
                                                self.cancelled, deadline)
        if (not isinstance(reply, (tuple, list)) or len(reply) != 2 or self.cancelled.is_set()
                or time.monotonic() >= deadline):
            return None
        first, second = (_proposal_suggestions(proposal, observation) for proposal in reply)
        if first is None or second is None:
            return None
        return [item for item in first if item in second]

    def _request_suggestions(self, observation, source_shapes):
        if self.version != VERSION_V3:
            return self._request_pair(observation) or []
        admitted = {}
        unresolved = observation
        for phase in ("headings", "recheck", "shape"):
            shapes = None
            if phase == "shape":
                if source_shapes is None or self._pair_deadline() is None:
                    break
                columns = tuple(item[0] for item in unresolved.review_columns)
                shapes = _scoped_source_shapes(source_shapes(columns), columns)
                if not shapes:
                    break
                unresolved = replace(unresolved, review_columns=tuple(
                    item for item in unresolved.review_columns if item[0] in shapes))
            pair = self._request_pair(unresolved, phase=phase, shapes=shapes)
            if pair is None:
                break
            admitted.update((item["column_index"], item) for item in pair)
            pending = tuple(item for item in unresolved.review_columns if item[0] not in admitted)
            if not pending:
                break
            unresolved = replace(unresolved, review_columns=pending)
        return [] if self.cancelled.is_set() else [admitted[index] for index in sorted(admitted)]

    def decide(self, observation, *, valid, allow_suggestions, path, sheet, member_sha256, profile, first_row, raw_headers,
               source_shapes=None):
        last_row = first_row + observation.depth - 1
        decision = {"member_path": path, "sheet": sheet, "member_sha256": member_sha256, "profile": profile,
                    "header_row": last_row, "header_rows": list(range(first_row, last_row + 1)),
                    "header_sha256": hashlib.sha256(json.dumps(raw_headers, ensure_ascii=False,
                        separators=(",", ":"), default=str if supports_opaque_tiers(self.version) else None).encode()).hexdigest(),
                    "status": "review", "reason": "ambiguous_source_header", "columns": []}
        if len(self.decisions) >= MAX_DECISIONS:
            return {**decision, "reason": "header_region_limit"}
        replay = self._replay[len(self.decisions):len(self.decisions) + 1] if type(self._replay) is list else []
        expected = replay[0] if replay and type(replay[0]) is dict else {}
        if valid:
            decision.update(status="verified", reason="source_header_verified", columns=observation.proof(first_row))
        elif allow_suggestions and 0 < len(observation.review_columns) <= MAX_COLUMNS:
            suggestions = (self._request_suggestions(observation, source_shapes) if self._replay is None else
                           _admitted_suggestions(expected.get("suggestions"), observation, "column_index"))
            if suggestions:
                decision["suggestions"] = suggestions
        if self._replay is not None and json.dumps(decision, sort_keys=True) != json.dumps(expected, sort_keys=True):
            decision.update(status="review", reason="invalid_header_replay", columns=[])
            decision.pop("suggestions", None)
        self.decisions.append(decision)
        return decision
