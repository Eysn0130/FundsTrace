"""Independent direct-edge expectations and a public reversible evidence loop."""
import copy
import hashlib
import io
import zipfile

import pytest

from app import evidence_resolution as e
from test_folder_evidence import accepted, account, entities, scoped, table, transaction


def statement(role, **fields):
    if role == "account":
        data = dict(role=role, scope_eligible="no", account="", card="", bank="Bank A", name="", currency="")
    else:
        data = dict(role=role, scope_eligible="no", parent="", account="", bank="Bank A", currency="")
    data.update(fields)
    return data


def observation(number, card="C", bank="Bank A", kind="account"):
    empty = dict(account="", card="", bank="", name="", identifier_type="account")
    return dict(role="transaction", scope_eligible="no", own_side="payer", endpoints={
        "payer": dict(account=number, card=card, bank=bank, name="", identifier_type=kind),
        "payee": dict(empty), "self": dict(empty)})


def evidence(declarations, observations, scope="source-a"):
    result = [(scope, i + 1, None, e.validate_evidence(e._json(data))) for i, data in enumerate(declarations)]
    result.extend((scope, 10000 + i, i + 1, e.validate_evidence(e._json(data))) for i, data in enumerate(observations))
    return result


# Hand-written outcomes: these do not compute an expected graph from the candidate.
RELATIONS = [
    ([("Bank A", "P", "S1"), ("Bank A", "P", "S2"), ("Bank A", "P", "S1")],
     [("Bank A", "P", "C")], ["P", "S1", "S2", "X"], ["resolved", "resolved", "resolved", "conflict"]),
    ([("Bank A", "P", "S"), ("Bank A", "Q", "S")],
     [("Bank A", "Q", "C")], ["S", "P"], ["resolved", "conflict"]),
    ([("Bank A", "Q", "S")], [("Bank A", "P", "C"), ("Bank A", "Q", "C")],
     ["P", "Q", "S", "X"], ["resolved", "resolved", "resolved", "conflict"]),
    ([("Bank B", "P", "S")], [("Bank A", "P", "C")], ["S"], ["conflict"]),
    ([("Bank A", "P", "S"), ("Bank A", "S", "G")], [("Bank A", "P", "C")],
     ["P", "S", "G"], ["resolved", "resolved", "conflict"]),
    ([("Bank A", "P", "S"), ("Bank A", "S", "P"), ("Bank A", "P", "P")],
     [("Bank A", "P", "C")], ["P", "S", "G"], ["resolved", "resolved", "conflict"]),
    ([], [("Bank A", "P", "C")], ["P", "S"], ["resolved", "conflict"]),
    ([("Bank A", "P", "S")], [], ["P", "S", "X"], ["resolved", "resolved", "resolved"]),
]


@pytest.mark.parametrize("relations,accounts,numbers,expected", RELATIONS)
@pytest.mark.parametrize("collect", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_direct_relations_keep_all_edges_without_transitive_or_bank_leakage(relations, accounts, numbers, expected, collect, reverse):
    declarations = [statement("subaccount", bank=b, parent=p, account=c) for b, p, c in relations]
    declarations += [statement("account", bank=b, account=a, card=c) for b, a, c in accounts]
    source = evidence(declarations, [observation(n) for n in numbers])
    if reverse:
        source.reverse()
    original = copy.deepcopy(source)
    registry, endpoints, conflicts, unresolved = e._compute(source, collect_endpoints=collect)
    assert source == original  # No lookup mutates the declarations/targets.
    assert conflicts == expected.count("conflict")
    assert unresolved == len(numbers) + expected.count("conflict")  # Missing payee per observation.
    if collect:
        own = {row["flow_id"]: row for row in endpoints if row["role"] == "payer"}
        assert [own[i + 1]["state"] for i in range(len(numbers))] == expected
        assert [own[i + 1]["endpoint_account"] for i in range(len(numbers))] == numbers
        assert all(row["endpoint_card"] == "C" for row in own.values())
    else:
        assert endpoints == 2 * len(numbers)
    if relations == RELATIONS[1][0]:
        child = next(row for row in registry if row["account_number"] == "S")
        assert child["status"] == "conflict" and child["parent_entity_id"] is None


@pytest.mark.parametrize("collect", [False, True])
def test_unscoped_relations_equal_identifiers_and_mixed_conflict_remain_separate(collect):
    first = evidence([statement("account", bank="", account="P", card="C"),
                      statement("subaccount", bank="", parent="P", account="S")],
                     [observation("S", bank="")])
    second = evidence([statement("account", bank="", account="P", card="C")],
                      [observation("S", bank="")], scope="source-b")
    second = [(scope, pos, 2 if flow else None, data) for scope, pos, flow, data in second]
    registry, endpoints, conflicts, _ = e._compute(first + second, collect_endpoints=collect)
    assert conflicts == 1
    assert len([row for row in registry if row["account_number"] == "S"]) == 2
    if collect:
        own = {row["flow_id"]: row["state"] for row in endpoints if row["role"] == "payer"}
        assert own == {1: "unscoped", 2: "conflict"}
    equal = evidence([statement("account", account="P", card="P"),
                      statement("subaccount", parent="P", account="S")],
                     [observation("P", "P"), observation("S", "P")])
    assert e._compute(equal, collect_endpoints=collect)[2] == 0
    mixed = evidence([statement("account", account="P", card="C"),
                      statement("account", account="C", card="D"),
                      statement("subaccount", parent="P", account="C")],
                     [observation("C", kind="account_or_card")])
    assert e._compute(mixed, collect_endpoints=collect)[2] == 1


def test_public_parent_relations_late_conflict_withdraw_restore_last_support_and_reopen(client, database, make_case):
    cid, other = make_case("Synthetic relation lifecycle"), make_case("Synthetic relation isolation")
    members = [("tx.csv", table("transaction", [transaction(**{"交易账号": "SYN-S"}),
        transaction(**{"交易账号": "", "交易卡号": "SYN-D", "交易流水号": "SYN-T2"})])),
        ("accounts.csv", table("account", [account(number="SYN-P"), account(number="SYN-A", card="SYN-D")])),
        ("children.csv", table("subaccount", [{"银行名称": "Synthetic bank", "开户账号": "SYN-P", "子账户账号": "SYN-S"}]))]
    view, receipt = accepted(client, cid, members)
    assert view["resolution"]["conflicts"] == 0
    accepted(client, other, members)

    def snapshot(case):
        page = client.get(f"/api/v1/cases/{case}/flows").json()
        resolution = entities(client, case)
        stats = client.get(f"/api/v1/cases/{case}/stats").json()
        assert page["total"] == 2
        assert page["analysis_revision"] == stats["analysis_revision"]
        assert stats["funds"]["totals"] == [{"currency": "CNY", "amount": None, "lower": "0.00", "upper": "20.50"}]
        assert {r["raw_payer_account"] for r in page["items"]} == {"SYN-S", "SYN-D"}
        assert {r["amount"] for r in page["items"]} == {"10.25"}
        assert all(r["analysis_revision"] == page["analysis_revision"] for r in page["items"])
        return page, resolution

    before, before_entities = snapshot(cid)
    other_before = snapshot(other)
    report = client.get(f"/api/v1/cases/{cid}/sources/{receipt['source_id']}/report").json()
    original = client.get(f"/api/v1/cases/{cid}/sources/{receipt['source_id']}/file")
    assert original.status_code == 200
    with zipfile.ZipFile(io.BytesIO(original.content)) as source:
        assert {name: source.read(name) for name in source.namelist()} == dict(scoped(members, "Synthetic bank"))
    assert report["sha256"] == hashlib.sha256(original.content).hexdigest()
    supports = {r["id"]: client.get(f"/api/v1/cases/{cid}/flows/{r['id']}/sources").json() for r in before["items"]}
    raw = database.q_raw("select * from flows where case_id=? order by id", [cid])
    _, conflict = accepted(client, cid, [
        ("conflict-accounts.csv", table("account", [account(number="SYN-A2", card="SYN-D")])),
        ("conflict-children.csv", table("subaccount", [{"银行名称": "Synthetic bank", "开户账号": "SYN-Q", "子账户账号": "SYN-S"}]))])
    changed, changed_entities = snapshot(cid)
    assert changed["analysis_revision"] != before["analysis_revision"]
    initial = {r["trade_no"]: r for r in before["items"]}
    after = {r["trade_no"]: r for r in changed["items"]}
    assert after["SYN-T2"]["payer_account"] != initial["SYN-T2"]["payer_account"]
    child = next(r for r in changed_entities["entities"] if r["account_number"] == "SYN-S")
    assert child["status"] == "conflict" and child["parent_entity_id"] is None
    assert snapshot(other) == other_before
    assert client.delete(f"/api/batches/{conflict['batch_id']}", params={"case_id": cid}).status_code == 200
    restored, restored_entities = snapshot(cid)
    assert restored_entities["entities"] == before_entities["entities"]
    assert restored["analysis_revision"] != changed["analysis_revision"]
    assert [{k: v for k, v in r.items() if k != "analysis_revision"} for r in restored["items"]] == [
        {k: v for k, v in r.items() if k != "analysis_revision"} for r in before["items"]]
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == raw
    assert client.get(f"/api/v1/cases/{cid}/sources/{receipt['source_id']}/report").json() == report
    assert client.get(f"/api/v1/cases/{cid}/sources/{receipt['source_id']}/file").content == original.content
    for flow, support in supports.items():
        assert client.get(f"/api/v1/cases/{cid}/flows/{flow}/sources").json() == support
    package = client.get(f"/api/cases/{cid}/export")
    assert package.status_code == 200
    reopened = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert reopened.status_code == 200, reopened.text
    target = reopened.json()["case_id"]
    copied, copied_entities = snapshot(target)
    assert copied_entities["entities"] == before_entities["entities"]
    fields = ("trade_no", "amount", "currency", "raw_payer_account", "payer_account", "payee_account")
    assert sorted(tuple(r[k] for k in fields) for r in copied["items"]) == sorted(tuple(r[k] for k in fields) for r in restored["items"])
    copied_support = client.get(f"/api/v1/cases/{target}/flows/{copied['items'][0]['id']}/sources").json()["items"]
    assert len(copied_support) == 1
    copied_source = copied_support[0]["source_id"]
    copied_report = client.get(f"/api/v1/cases/{target}/sources/{copied_source}/report").json()
    assert copied_report["sha256"] == report["sha256"]
    journal_fields = ("member_path", "source_row", "disposition", "reason_code", "details")
    assert [tuple(r[k] for k in journal_fields) for r in copied_report["items"]] == [
        tuple(r[k] for k in journal_fields) for r in report["items"]]
    assert client.get(f"/api/v1/cases/{target}/sources/{copied_source}/file").status_code == 404
    assert client.delete(f"/api/batches/{receipt['batch_id']}", params={"case_id": cid}).status_code == 200
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0
    assert entities(client, cid)["entities"] == []
    assert snapshot(other) == other_before
    assert snapshot(target) == (copied, copied_entities)
