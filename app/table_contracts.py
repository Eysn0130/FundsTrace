"""Closed source semantics for the v8 table parser, without file or DB access.

Compile one immutable contract from the entire header region. Every candidate
remains a validator; values cannot choose a different amount model on each row.
Only balances explicitly attached to this very entry authorize direction
recovery. Ordinary running balances provide no continuity evidence.
"""
from dataclasses import dataclass
from decimal import localcontext
import hashlib
import json
import re


VERSION = "funds-trace-source-contract-v1"
ALIASES = {
    "balance_before": ("交易前余额", "交易前账面余额", "本笔交易前余额", "本笔交易前账面余额"),
    "balance": ("交易后账面余额", "本笔交易后余额", "本笔交易后账面余额"),
    "balance_before_unit": ("交易前余额单位", "交易前账面余额单位", "balance before unit"),
}
# Unqualified balances may be available/limit-inclusive rather than booked.
# They are retained as candidates, but cannot confer recovery authority.
BEFORE_HEADERS = ("交易前账面余额", "本笔交易前账面余额")
AFTER_HEADERS = ("交易后账面余额", "本笔交易后账面余额")
MONEY_FIELDS = ("balance_before",)
VALIDATION_ISSUES = frozenset(("balance_amount_conflict", "balance_direction_conflict"))
DETAILS_FIELDS = frozenset(("source_contract", "contract_sha256", "amount_model",
                           "candidate_scope", "direction_basis", "balance_basis"))
MODELS = frozenset(("account_signed", "legacy_magnitude", "split_magnitude"))
DIRECTION_BASES = frozenset(("explicit_direction", "explicit_signed_amount", "split_amount",
                             "same_entry_book_balances", "explicit_parties", "undetermined"))
BALANCE_BASES = frozenset(("no_same_entry_pair", "incomplete_same_entry_pair",
                           "same_entry_unvalidated", "same_entry_exact", "same_entry_conflict",
                           "not_account_ledger_layout"))


@dataclass(frozen=True, slots=True)
class Contract:
    digest: str
    amount_model: str
    after_indices: tuple[int, ...]
    paired: bool

    def details(self):
        return {"source_contract": VERSION, "contract_sha256": self.digest,
                "amount_model": self.amount_model, "candidate_scope": "all_region_candidates"}


def compile_contract(candidates):
    """The caller classifies headers using exact aliases, never cell statistics."""
    model = ("account_signed" if any(item.get("signed") for item in candidates.get("amount", ()))
             else "split_magnitude" if not candidates.get("amount") and any(
                 candidates.get(field) for field in ("income_amt", "expense_amt")) else "legacy_magnitude")
    after = tuple(item["index"] for item in candidates.get("balance", ()) if item.get("same_entry_after"))
    # Include every semantic candidate, including its unit and identity role.
    # No source cell values or unchecked user declaration confer authority.
    layout = {field: [{key: str(item.get(key, "")) for key in
                      ("index", "factor", "unit", "signed", "reference_role", "same_entry_after", "same_entry_before",
                       *(("time_format",) if "time_format" in item else ()),
                       *(("reference_profile",) if "reference_profile" in item else ()),
                       *(("reference_blank_indices",) if "reference_blank_indices" in item else ()))}
                     for item in items] for field, items in sorted(candidates.items())}
    digest = hashlib.sha256(json.dumps([VERSION, model, layout], sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    return Contract(digest, model, after, bool(after and any(
        item.get("same_entry_before") for item in candidates.get("balance_before", ()))))


def resolve_direction(contract, *, mode, amount, direction, signed_direction,
                      split_direction, party_direction, before, after):
    """Validate exact same-entry balances; never derive or repair an amount."""
    basis = ("explicit_direction" if direction else "explicit_signed_amount" if signed_direction
             else "split_amount" if split_direction else "explicit_parties" if party_direction or mode == "A" else "undetermined")
    notes, issues = {**contract.details(), "direction_basis": basis}, []
    direction = direction or signed_direction or split_direction or party_direction
    if mode != "B":
        notes["balance_basis"] = "not_account_ledger_layout"
        return direction, issues, notes
    if not contract.paired:
        notes["balance_basis"] = "no_same_entry_pair"
        return direction, issues, notes
    if before is None or after is None:
        notes["balance_basis"] = "incomplete_same_entry_pair"
        return direction, issues, notes
    if amount is None:
        notes["balance_basis"] = "same_entry_unvalidated"
        return direction, issues, notes
    with localcontext() as context:
        context.prec = max(context.prec, 50)
        delta = after - before
    effect_direction = "in" if delta > 0 else "out" if delta < 0 else None
    if delta.copy_abs() != amount.copy_abs():
        issues.append("balance_amount_conflict")
    if direction and effect_direction and direction != effect_direction:
        issues.append("balance_direction_conflict")
    notes["balance_basis"] = "same_entry_conflict" if issues else "same_entry_exact"
    if not issues and not direction and effect_direction:
        direction = effect_direction
        notes["direction_basis"] = "same_entry_book_balances"
    return direction, issues, notes


def validate_details(details, disposition, reason=None):
    """Package metadata validation, not authentication of a bank's statements."""
    present = DETAILS_FIELDS.intersection(details)
    if not present:
        return (disposition in {"auxiliary", "non_transaction", "unresolved"} and reason in {
            "preamble", "blank_record", "repeated_header", "unknown_table", "row_width", "region_mapping_conflict"})
    if (not {"source_contract", "contract_sha256", "amount_model", "candidate_scope"} <= present
            or details.get("source_contract") != VERSION
            or not re.fullmatch(r"[0-9a-f]{64}", details.get("contract_sha256", ""))
            or details.get("amount_model") not in MODELS
            or details.get("candidate_scope") != "all_region_candidates"
            or "direction_basis" in details and details["direction_basis"] not in DIRECTION_BASES
            or "balance_basis" in details and details["balance_basis"] not in BALANCE_BASES):
        return False
    if disposition in {"accepted", "imported", "duplicate", "removed", "non_monetary"} and present != DETAILS_FIELDS:
        return False
    if details.get("direction_basis") == "same_entry_book_balances" and details.get("balance_basis") != "same_entry_exact":
        return False
    if details.get("balance_basis") == "same_entry_conflict" and disposition != "unresolved":
        return False
    return True
