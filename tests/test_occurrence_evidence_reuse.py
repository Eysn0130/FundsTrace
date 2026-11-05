"""Small synthetic bindings retain source identity while reusing private evidence."""
import hashlib
import json
from contextlib import contextmanager
from decimal import Decimal

import pytest

from app import evidence_resolution as evidence, import_staging as staging


def transaction(bank="Synthetic Bank", **changes):
    endpoint = {"account": "SYN-A", "card": "SYN-C", "bank": bank,
                "name": "Synthetic Holder", "identifier_type": "account"}
    data = {"role": "transaction", "scope_eligible": "yes", "own_side": "payer",
            "endpoints": {role: dict(endpoint) for role in ("payer", "payee", "self")}}
    data.update(changes)
    return json.dumps(data, ensure_ascii=False)


def auxiliary(role="manifest", bank="Synthetic Bank"):
    data = {"role": role, "scope_eligible": "yes", "bank": bank}
    if role == "manifest":
        data["request"] = "SYN-REQUEST"
    else:
        data.update(account="SYN-A", card="SYN-C", name="Synthetic Holder", currency="CNY")
    return json.dumps(data)


def plan_for(entries):
    """Entries are (original evidence, member path, financial kind or auxiliary)."""
    plan = {"members": [], "records": [], "rows": [], "cancelled_rows": [],
            "_member_families": {}}
    for number, (raw, path, kind) in enumerate(entries, 1):
        if path not in plan["_member_families"]:
            plan["members"].append({"member_path": path, "sha256": hashlib.sha256(path.encode()).hexdigest(),
                                    "role": "auxiliary" if kind == "auxiliary" else "table"})
            plan["_member_families"][path] = path.split("/")[0]
        plan["records"].append({"record_no": number, "member_path": path, "source_row": number * 3 + 1,
                                "disposition": "auxiliary" if kind == "auxiliary" else "accepted",
                                "details": {evidence.DETAIL_FIELD: raw, "note": f"Synthetic source {number}"}})
        if kind != "auxiliary":
            plan[kind].append({"_source_row": number, "amount": Decimal("90071992547409.91"),
                               "balance": None, "synthetic_id": f"SYN-{number}"})
    return plan


@contextmanager
def bound_plan(entries, staged):
    plan = plan_for(entries)
    if staged:
        plan = staging.stage_plan(plan)
    try:
        yield plan
    finally:
        if staged:
            plan["_plan_store"].close()


def observe(monkeypatch):
    validate, project = evidence.validate_evidence, evidence._projection_evidence
    calls = {"validate": [], "project": 0}

    def counted(value):
        calls["validate"].append(value)
        return validate(value)

    def projected(value):
        calls["project"] += 1
        return project(value)

    monkeypatch.setattr(evidence, "validate_evidence", counted)
    monkeypatch.setattr(evidence, "_projection_evidence", projected)
    return calls


def assert_financial(plan, scopes):
    records = {record["record_no"]: record for record in plan["records"]}
    rows = list(plan["rows"]) + list(plan["cancelled_rows"])
    assert len(rows) == len(scopes)
    for row, scope in zip(rows, scopes):
        record = records[row["_source_row"]]
        member_hash = hashlib.sha256(record["member_path"].encode()).hexdigest()
        material = json.dumps(["typed-account-evidence-v1", member_hash, record["source_row"]],
                              separators=(",", ":"))
        assert row["_occurrence_key"] == hashlib.sha256(material.encode()).hexdigest()
        assert row["_occurrence_scope"] == record["details"]["occurrence_scope"] == scope
        assert row["amount"] == Decimal("90071992547409.91")
        assert row["balance"] is None
        assert row["synthetic_id"] == f"SYN-{row['_source_row']}"


@pytest.mark.parametrize("staged", [False, True])
def test_equal_text_reuses_processing_but_not_source_binding(database, monkeypatch, staged):
    raw = transaction()
    entries = [(raw, "first.zip/rows.csv", "rows"),
               (raw.encode().decode(), "second.zip/other.csv", "rows"),
               (raw, "first.zip/rows.csv", "cancelled_rows")]
    calls = observe(monkeypatch)
    with bound_plan(entries, staged) as plan:
        assert evidence.bind_occurrences(plan) is plan
        assert_financial(plan, ["Synthetic Bank"] * 3)
        records = list(plan["records"])
        assert records[0]["details"]["evidence_family"] != records[1]["details"]["evidence_family"]
        assert records[0]["details"]["family_member_sha256"] != records[1]["details"]["family_member_sha256"]
        assert [r["details"][evidence.DETAIL_FIELD] for r in records] == [raw] * 3
        # Cost assertion follows independent binding/amount/original-text checks.
        assert len(calls["validate"]) == calls["project"] == 1
        evidence.bind_occurrences(plan)
        assert_financial(plan, ["Synthetic Bank"] * 3)
        assert len(calls["validate"]) == calls["project"] == 2


@pytest.mark.parametrize("staged", [False, True])
def test_misses_revalidate_raw_text_bank_role_and_missing_markers(database, monkeypatch, staged):
    a = transaction()
    b = transaction("Other Synthetic Bank")
    marker = transaction("NULL")
    role = auxiliary("account")
    changed = json.loads(a)
    changed["endpoints"]["self"].update(identifier_type="card", name="Other Synthetic Holder")
    c = json.dumps(changed)
    # Same projection with different raw JSON must miss; A after B must miss.
    values = [a, b, a, a + " ", c, role, marker, marker]
    calls = observe(monkeypatch)
    with bound_plan([(v, "one.zip/rows.csv", "rows") for v in values], staged) as plan:
        evidence.bind_occurrences(plan)
        assert_financial(plan, ["Synthetic Bank", "Other Synthetic Bank", "Synthetic Bank",
                                "Synthetic Bank", "Synthetic Bank", "", "", ""])
        assert [r["details"][evidence.DETAIL_FIELD] for r in plan["records"]] == values
        assert len(calls["validate"]) == calls["project"] == 7


@pytest.mark.parametrize("staged", [False, True])
def test_two_auxiliary_passes_and_transaction_scope_remain_source_bound(database, monkeypatch, staged):
    manifest, raw = auxiliary(), transaction("")
    entries = [(manifest, "one.zip/manifest.csv", "auxiliary"),
               (manifest, "two.zip/manifest.csv", "auxiliary"),
               (raw, "one.zip/SYN-REQUEST/Synthetic Bank交易明细信息.csv", "rows"),
               (raw, "two.zip/unmatched.csv", "rows")]
    calls = observe(monkeypatch)
    with bound_plan(entries, staged) as plan:
        evidence.bind_occurrences(plan)
        assert_financial(plan, ["Synthetic Bank", ""])
        assert len(calls["validate"]) == calls["project"] == 2


@pytest.mark.parametrize("staged", [False, True])
def test_auxiliary_conflict_denies_only_its_family(database, staged):
    raw = transaction("")
    entries = [(auxiliary(), f"{family}/manifest.csv", "auxiliary") for family in ("one.zip", "two.zip")]
    entries += [(auxiliary("account", "Conflicting Synthetic Bank"),
                 "one.zip/SYN-REQUEST/Synthetic Bank账户信息.csv", "auxiliary")]
    entries += [(raw, f"{family}/SYN-REQUEST/Synthetic Bank交易明细信息.csv", "rows")
                for family in ("one.zip", "two.zip")]
    with bound_plan(entries, staged) as plan:
        evidence.bind_occurrences(plan)
        assert_financial(plan, ["", "Synthetic Bank"])


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("value", [None, "", False, 0, [], {}])
def test_falsey_financial_evidence_still_skips_validation(database, monkeypatch, staged, value):
    calls = observe(monkeypatch)
    with bound_plan([(value, "one.zip/rows.csv", "rows")], staged) as plan:
        evidence.bind_occurrences(plan)
        assert_financial(plan, [""])
        assert calls == {"validate": [], "project": 0}


@pytest.mark.parametrize("aux,value", [
    pytest.param(aux, value, id=f"{'auxiliary' if aux else 'financial'}-{name}")
    for name, value in [("none", None), ("empty", ""), ("false", False), ("zero", 0),
                        ("list", []), ("dict", {}), ("non-string", True), ("json", "{"),
                        ("unicode", "\ud800"), ("length", "x" * 8193),
                        ("role", '{"role":"unknown"}'), ("own-side", transaction(own_side="wrong")),
                        ("field", transaction("Synthetic\x00Bank"))]
    for aux in (False, True) if aux or value
])
def test_invalid_evidence_preserves_first_exception(monkeypatch, value, aux):
    with pytest.raises(Exception) as original:
        evidence.validate_evidence(value)
    raw = auxiliary() if aux else transaction()
    kind = "auxiliary" if aux else "rows"
    calls = observe(monkeypatch)
    plan = plan_for([(raw, "one.zip/rows.csv", kind), (value, "one.zip/rows.csv", kind),
                     (raw, "one.zip/rows.csv", kind)])
    with pytest.raises(type(original.value)) as actual:
        evidence.bind_occurrences(plan)
    assert str(actual.value) == str(original.value)
    assert calls["validate"] == [raw, value]
    assert calls["project"] == 1


def test_string_subclass_never_hits_or_populates_slot(monkeypatch):
    encodes = []

    class SourceText(str):
        def encode(self, *args, **kwargs):
            encodes.append(True)
            return super().encode(*args, **kwargs)

        def __eq__(self, other):
            raise AssertionError("source subclass equality must not be consulted")

    raw = transaction()
    values = [raw, SourceText(raw), raw, SourceText(raw), SourceText(raw), raw]
    calls = observe(monkeypatch)
    plan = plan_for([(v, "one.zip/rows.csv", "rows") for v in values])
    evidence.bind_occurrences(plan)
    assert_financial(plan, ["Synthetic Bank"] * len(values))
    assert len(encodes) == 3
    assert len(calls["validate"]) == calls["project"] == len(values)


def test_string_subclass_encode_error_is_not_hidden():
    class SourceText(str):
        def encode(self, *args, **kwargs):
            raise ValueError("Synthetic source encoding failure")

    raw = transaction()
    plan = plan_for([(raw, "one.zip/rows.csv", "rows"), (SourceText(raw), "one.zip/rows.csv", "rows")])
    with pytest.raises(ValueError, match="^Synthetic source encoding failure$"):
        evidence.bind_occurrences(plan)


def test_falsey_string_subclass_still_skips_financial_validation():
    class SourceText(str):
        def __bool__(self):
            return False

        def encode(self, *args, **kwargs):
            raise AssertionError("falsey financial evidence must not be validated")

    plan = plan_for([(SourceText(transaction()), "one.zip/rows.csv", "rows")])
    evidence.bind_occurrences(plan)
    assert_financial(plan, [""])


def test_public_validation_is_fresh_and_projection_does_not_escape():
    raw = transaction("NULL")
    first, second = evidence.validate_evidence(raw), evidence.validate_evidence(raw)
    assert first == second and first is not second
    first["endpoints"]["self"]["bank"] = "Synthetic mutation"
    assert second["endpoints"]["self"]["bank"] == "NULL"
    plan = plan_for([(raw, "one.zip/rows.csv", "rows")] * 2)
    evidence.bind_occurrences(plan)
    assert_financial(plan, ["", ""])
    assert all(r["details"][evidence.DETAIL_FIELD] == raw for r in plan["records"])
    assert evidence.validate_evidence(raw) == second
    assert all(not isinstance(v, dict) for r in plan["records"] for v in r["details"].values())
