"""Identical card/account text links only through scoped typed declarations."""
import json

import pytest

from test_folder_evidence import accepted, account, archive, entities, table, transaction

BANK = "Synthetic bank"
CARD = "SYN-EQUAL-CARD-ACCOUNT"
REQUEST = "SYN-REQUEST"


def members(*, account_path=None, account_rows=None, root="root", request=REQUEST, bank=BANK, include_accounts=True):
    prefix = f"{root}/{request}" if root else request
    result = [(f"{prefix}/{bank}交易明细信息.csv", table("transaction", [transaction(**{
        "交易账号": "", "交易卡号": CARD})])),
        ("manifest.csv", table("task", [{"请求单号": request, "银行名称": bank}]))]
    if include_accounts:
        result.append((account_path or f"{prefix}/{bank}账户信息.csv", table("account", account_rows or [account(number=CARD, card=CARD, bank=bank)])))
    return result


def observed_own(client, cid):
    response = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"})
    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert len(rows) == 1
    row = rows[0]
    own = next(item for item in entities(client, cid)["entities"] if item["entity_id"] == row["self_account"])
    return row, own


def test_native_request_typed_equal_declaration_resolves_without_filling_original_account(client, database, make_case):
    cid = make_case("Synthetic native scoped equal fields")
    accepted(client, cid, members(), zipped=True, scope_bank=None)
    row, own = observed_own(client, cid)
    assert (own["kind"], own["account_number"], own["bank"]) == ("account", CARD, BANK)
    assert row["payer_account"] == row["self_account"] == own["entity_id"]
    assert row["self_source_account"] == row["payer_source_account"] == ""
    assert row["self_card"] == row["payer_card"] == CARD
    sources = client.get(f"/api/v1/cases/{cid}/flows/{row['id']}/sources").json()["items"]
    original = [json.loads(item["record_details"]["resolution_evidence"]) for item in sources]
    assert all(item["endpoints"]["self"]["account"] == "" for item in original)
    # The account and card remain distinct types; no identity comes from text equality.
    from app.evidence_resolution import _entity_id
    assert own["entity_id"] == _entity_id(BANK, "account", CARD)
    assert own["entity_id"] != _entity_id(BANK, "card", CARD)


@pytest.mark.parametrize("shape", ["no_account", "ordinary_csv", "other_request", "other_request_prefix", "other_family", "other_bank", "bank_conflict", "conflicting_account", "parent_only"])
def test_equal_text_without_the_complete_common_scope_does_not_supply_account(client, make_case, shape):
    cid = make_case("Synthetic unsupported equal fields " + shape)
    if shape == "no_account":
        supplied = members(include_accounts=False)
    elif shape == "ordinary_csv":
        supplied = members(account_path="accounts.csv")
    elif shape == "other_request":
        supplied = members(account_path=f"root/SYN-OTHER/{BANK}账户信息.csv")
        supplied[1] = ("manifest.csv", table("task", [{"请求单号": REQUEST, "银行名称": BANK}, {"请求单号": "SYN-OTHER", "银行名称": BANK}]))
    elif shape == "other_request_prefix":
        supplied = members(account_path=f"another-root/{REQUEST}/{BANK}账户信息.csv")
    elif shape == "other_family":
        supplied = members(include_accounts=False)
        inner = [(f"root/{REQUEST}/{BANK}账户信息.csv", table("account", [account(number=CARD, card=CARD)])),
                 ("manifest.csv", table("task", [{"请求单号": REQUEST, "银行名称": BANK}]))]
        supplied.append(("separate-family.zip", archive(inner)))
    elif shape == "other_bank":
        other_bank = "Synthetic other bank"
        supplied = members(account_path=f"root/{REQUEST}/{other_bank}账户信息.csv", account_rows=[account(number=CARD, card=CARD, bank=other_bank)])
        supplied[1] = ("manifest.csv", table("task", [{"请求单号": REQUEST, "银行名称": BANK}, {"请求单号": REQUEST, "银行名称": other_bank}]))
    elif shape == "bank_conflict":
        supplied = members(account_rows=[account(number=CARD, card=CARD, bank="Synthetic other bank")])
    elif shape == "conflicting_account":
        supplied = members(account_rows=[account(number=CARD, card=CARD), account(number="SYN-DIFFERENT-ACCOUNT", card=CARD)])
    else:
        supplied = members(include_accounts=False)
        supplied.append((f"root/{REQUEST}/{BANK}关联子账户信息.csv", table("subaccount", [{"银行名称": BANK, "开户账号": CARD, "子账户账号": "SYN-CHILD"}])))
    accepted(client, cid, supplied, scope_bank=None)
    _row, own = observed_own(client, cid)
    assert own["kind"] == "card"


@pytest.mark.parametrize("strong_first", [False, True])
def test_scoped_support_replay_withdrawal_is_per_observation_and_reversible(client, database, make_case, strong_first):
    cid = make_case("Synthetic scoped equal support withdrawal")
    strong, weak = members(), members(include_accounts=False)
    if strong_first:
        _, first = accepted(client, cid, strong, scope_bank=None)
        accepted(client, cid, weak, scope_bank=None)
    else:
        accepted(client, cid, weak, scope_bank=None)
        _, first = accepted(client, cid, strong, scope_bank=None)
    before_raw = database.q_raw("select * from flows where case_id=? order by id", [cid])
    row, own = observed_own(client, cid)
    assert own["kind"] == "account"
    _, second = accepted(client, cid, list(reversed(strong)), zipped=True, scope_bank=None)
    assert observed_own(client, cid)[1]["kind"] == "account"
    assert client.delete(f"/api/batches/{second['batch_id']}", params={"case_id": cid}).status_code == 200
    assert observed_own(client, cid)[1]["kind"] == "account"
    assert client.delete(f"/api/batches/{first['batch_id']}", params={"case_id": cid}).status_code == 200
    after_row, after = observed_own(client, cid)
    assert after["kind"] == "card"
    assert after_row["self_account"] != row["self_account"]
    # Storage ownership can move to the remaining batch; source fields cannot.
    without_owner = lambda rows: [{key: value for key, value in row.items() if key != "batch_id"} for row in rows]
    assert without_owner(database.q_raw("select * from flows where case_id=? order by id", [cid])) == without_owner(before_raw)


def test_scoped_equal_case_package_and_older_derived_version_keep_immutable_audit(client, database, make_case):
    cid = make_case("Synthetic scoped equal portable projection")
    accepted(client, cid, members(), scope_bank=None)
    row, own = observed_own(client, cid)
    assert own["kind"] == "account"
    immutable = database.q_raw("select revision,summary_json,decisions_json from resolution_revisions where case_id=? order by revision", [cid])
    with database._lock:
        database.x("update resolution_projection_versions set card_version='endpoint-cards-v1' where case_id=?", [cid])
        database._con.close()
        database._con = None
        database.con()
    assert observed_own(client, cid)[1]["entity_id"] == own["entity_id"]
    assert database.q_raw("select revision,summary_json,decisions_json from resolution_revisions where case_id=? order by revision", [cid]) == immutable
    package = client.get(f"/api/cases/{cid}/export")
    assert package.status_code == 200
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    assert observed_own(client, restored.json()["case_id"])[1]["entity_id"] == own["entity_id"]


@pytest.mark.parametrize("weak_kind", ["unknown_bank", "other_bank", "other_card", "explicit_other_account"])
def test_same_observation_does_not_override_conflicting_or_unbound_weak_endpoint(client, database, make_case, weak_kind):
    """Same flow IDs alone never authorize the narrow absence-of-support merge."""
    import copy
    from app import evidence_resolution
    cid = make_case("Synthetic same observation guard " + weak_kind)
    accepted(client, cid, members(), scope_bank=None)
    evidence = list(evidence_resolution._load(cid))
    source, position, flow_id, note = next(item for item in evidence if item[3]["role"] == "transaction")
    weak = copy.deepcopy(note)
    weak["_family"], weak["_member_path"] = "SYN-UNRELATED-FAMILY", "ordinary.csv"
    for role in ("payer", "self"):
        weak["endpoints"][role]["bank"] = "" if weak_kind == "unknown_bank" else "Synthetic other bank" if weak_kind == "other_bank" else BANK
        if weak_kind == "other_card":
            weak["endpoints"][role]["card"] = "SYN-ANOTHER-CARD"
        if weak_kind == "explicit_other_account":
            weak["endpoints"][role]["account"] = "SYN-ANOTHER-ACCOUNT"
            weak["endpoints"][role]["identifier_type"] = "account"
    combined = [*evidence, ("SYN-INDEPENDENT-SOURCE", position, flow_id, weak)]
    for inputs in (combined, list(reversed(combined))):
        _entities, endpoints, _conflicts, _unresolved = evidence_resolution._compute(copy.deepcopy(inputs))
        assert {item["state"] for item in endpoints if item["role"] in {"payer", "self"}} == {"conflict"}


def test_scoped_typed_parent_account_is_resolved_without_choosing_a_child(client, database, make_case):
    cid = make_case("Synthetic explicit typed parent account")
    supplied = members()
    supplied.append((f"root/{REQUEST}/{BANK}关联子账户信息.csv", table("subaccount", [
        {"银行名称": BANK, "开户账号": CARD, "子账户账号": "SYN-CHILD-CNY", "币种": "CNY"},
        {"银行名称": BANK, "开户账号": CARD, "子账户账号": "SYN-CHILD-USD", "币种": "USD"}])))
    accepted(client, cid, supplied, scope_bank=None)
    row, own = observed_own(client, cid)
    assert (own["kind"], own["account_number"]) == ("account", CARD)
    children = [item for item in entities(client, cid)["entities"] if item["kind"] == "subaccount"]
    assert {item["account_number"] for item in children} == {"SYN-CHILD-CNY", "SYN-CHILD-USD"}
    assert {item["parent_entity_id"] for item in children} == {own["entity_id"]}
    assert all(item["entity_id"] != row["self_account"] for item in children)
    assert row["self_source_account"] == row["payer_source_account"] == ""
    assert row["self_card"] == row["payer_card"] == CARD
    assert row["currency"] == "CNY" and row["balance"] == "100.00"


def test_generic_card_to_declared_parent_stays_conservative(client, make_case):
    cid = make_case("Synthetic generic card parent ambiguity")
    supplied = members(account_rows=[account(number="SYN-PARENT", card=CARD)])
    supplied.append((f"root/{REQUEST}/{BANK}关联子账户信息.csv", table("subaccount", [
        {"银行名称": BANK, "开户账号": "SYN-PARENT", "子账户账号": "SYN-CHILD", "币种": "CNY"}])))
    accepted(client, cid, supplied, scope_bank=None)
    _row, own = observed_own(client, cid)
    assert own["kind"] == "card"


def three_source_observation_evidence(database, cid, *, explicit_card=True):
    import copy
    from app import evidence_resolution
    evidence = list(evidence_resolution._load(cid))
    auxiliary = [item for item in evidence if item[3]["role"] != "transaction"]
    strong = next(item for item in evidence if item[3]["role"] == "transaction")
    _source, position, flow_id, note = strong
    weak_note = copy.deepcopy(note)
    weak_note["_family"], weak_note["_member_path"] = "SYN-WEAK-FAMILY", "ordinary.csv"
    for role in ("payer", "self"):
        weak_note["endpoints"][role]["bank"] = BANK
    weak = ("SYN-WEAK", position, flow_id, weak_note)
    explicit_note = copy.deepcopy(weak_note)
    explicit_note["_family"] = "SYN-EXPLICIT-FAMILY"
    for role in ("payer", "self"):
        explicit_note["endpoints"][role].update(account=CARD, identifier_type="account", card=CARD if explicit_card else "")
    explicit = ("SYN-EXPLICIT", position, flow_id, explicit_note)
    return auxiliary, (weak, explicit, strong)


@pytest.mark.parametrize("explicit_card", [True, False])
def test_three_source_scoped_equal_resolution_is_invariant_under_all_source_orders(client, database, make_case, explicit_card):
    import copy
    from itertools import permutations
    from app import evidence_resolution
    cid = make_case("Synthetic three source resolution order")
    accepted(client, cid, members(), scope_bank=None)
    auxiliary, observations = three_source_observation_evidence(database, cid, explicit_card=explicit_card)
    expected_entity = evidence_resolution._entity_id(BANK, "account", CARD)
    outputs = []
    for order in permutations(observations):
        actual = evidence_resolution._compute(copy.deepcopy([*auxiliary, *order]))
        for item in actual[1]:
            if item["role"] in {"payer", "self"}:
                assert (item["entity_id"], item["state"], item["card_state"]) == (expected_entity, "resolved", "confirmed")
                assert item["reason"] == evidence_resolution._SCOPED_EQUAL_REASON
        outputs.append(actual)
        with evidence_resolution._ResolutionStaging() as staging:
            staged = evidence_resolution._compute(copy.deepcopy([*auxiliary, *order]), staging=staging)
            assert (staged[0], list(staged[1]), staged[2], staged[3]) == actual
    assert all(item == outputs[0] for item in outputs)
    # Duplicate scoped declarations retain the same typed identity and do not
    # manufacture a new entity or a stronger temporal statement.
    duplicate = copy.deepcopy(observations[-1])
    duplicate = ("SYN-DUPLICATE-SCOPED", *duplicate[1:])
    assert evidence_resolution._compute(copy.deepcopy([*auxiliary, *observations, duplicate])) == outputs[0]


@pytest.mark.parametrize("conflict", ["unknown_bank", "other_bank", "other_card", "other_account", "source_scope_conflict"])
def test_three_source_ordering_never_erases_a_real_endpoint_conflict(client, database, make_case, conflict):
    import copy
    from itertools import permutations
    from app import evidence_resolution
    cid = make_case("Synthetic three source real conflict " + conflict)
    accepted(client, cid, members(), scope_bank=None)
    auxiliary, observations = three_source_observation_evidence(database, cid)
    source, position, flow_id, bad = copy.deepcopy(observations[0])
    bad["_family"] = "SYN-CONTRARY-FAMILY"
    if conflict == "source_scope_conflict":
        bad["_source_scope_conflict"] = True
    for role in ("payer", "self"):
        endpoint = bad["endpoints"][role]
        if conflict == "unknown_bank": endpoint["bank"] = ""
        if conflict == "other_bank": endpoint["bank"] = "Synthetic another bank"
        if conflict == "other_card": endpoint["card"] = "SYN-CONTRARY-CARD"
        if conflict == "other_account": endpoint.update(account="SYN-CONTRARY-ACCOUNT", identifier_type="account")
    contrary = ("SYN-CONTRARY", position, flow_id, bad)
    for order in permutations([*observations, contrary]):
        _entities, endpoints, _conflicts, _unresolved = evidence_resolution._compute(copy.deepcopy([*auxiliary, *order]))
        assert {item["state"] for item in endpoints if item["role"] in {"payer", "self"}} == {"conflict"}
