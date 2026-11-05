"""Public source-share behavior; synthetic own-account ledgers only."""
from decimal import Decimal
from datetime import datetime

import pytest

from app import analysis_snapshot, importer, principal, principal_ledger


def own_rows(case_id):
    return principal_ledger.read(case_id, "SYN-B", "CNY", datetime(2026, 1, 1), datetime(2026, 1, 2))["rows"]


def ledger(database, case_id, rows):
    ids = []
    for index, (direction, amount, balance) in enumerate(rows):
        incoming = direction == "in"
        ids.append(str(database.q("""insert into flows(case_id,trade_time,payer_account,payee_account,
            self_account,amount,balance,currency) values (?,?,?,?,?,?,?,'CNY') returning id""",
            [case_id, f"2026-01-01 10:{index:02d}:00", "SYN-O" if incoming else "SYN-B",
             "SYN-B" if incoming else "SYN-C", "SYN-B", Decimal(amount), Decimal(balance) if balance is not None else None])[0]["id"]))
    return ids


def body(case_id, ids, **overrides):
    return {"case_id": str(case_id), "account": "SYN-B", "currency": "CNY",
            "start": "2026-01-01T00:00:00", "end": "2026-01-01T23:59:59",
            "source_flow_id": ids[0], "target_flow_ids": [ids[-1]],
            "conditions": {"coverage": "complete_declared", "opening_balance": "0.00", "no_overdraft": True, "funds_available": True,
                           "balance_kind": "post_book"}, **overrides}


def run(client, request):
    response = client.post("/api/analysis/principal", json=request)
    assert response.status_code == 200, response.text
    return response.json()


def test_untracked_competitors_remain_complete_but_client_conditions_do_not_prove_coverage(client, database, make_case):
    case_id = make_case()
    ids = ledger(database, case_id, [("in","100.00","140.00"),("out","50.00","90.00"),("out","90.00","0.00")])
    request = body(case_id, ids)
    request["conditions"]["opening_balance"] = "40.00"
    result = run(client, request)
    assert result["status"] == "unknown" and result["targets"] == []
    assert result["ledger_count"] == 3
    assert "ledger_coverage_unknown" in result["reasons"]
    # The internal conditional model remains independently testable; this is
    # not source evidence and is never injected into the public assessment.
    entries, reason = principal_ledger.ordered(own_rows(case_id), 4000, {}, "post_book")
    assert reason is None
    modeled = principal.allocate_ledger(4000, entries, {ids[0]}, [ids[-1]])
    target = modeled["targets"][0]
    assert (target["amount"], target["min"], target["max"], target["fifo"]) == (9000, 5000, 9000, 9000)
    assert len(target["min_witness"]["entries"]) == 3
    assert target["min_witness"]["opening_balance"] == 4000
    assert modeled["fifo_witness"]["closing_source_balance"] == 0
    assert all(isinstance(item["id"], str) for item in modeled["fifo_witness"]["entries"])
    exported = client.post("/api/analysis/principal/export", json={**request,"expected_revision":result["revision"]})
    assert exported.status_code == 200
    assert result["revision"] in exported.text and "ledger_coverage_unknown" in exported.text


@pytest.mark.parametrize("change,reason", [
    ({"coverage":"unknown"},"ledger_coverage_unknown"),
    ({"opening_balance":None},"opening_balance_unknown"),
    ({"no_overdraft":False},"credit_availability_unknown"),
    ({"funds_available":False},"funds_availability_unknown"),
])
def test_missing_conditions_do_not_become_zero_balance_or_certainty(client,database,make_case,change,reason):
    case_id = make_case()
    ids = ledger(database,case_id,[("in","100.00","100.00"),("out","100.00","0.00")])
    request = body(case_id,ids)
    request["conditions"].update(change)
    result = run(client,request)
    assert result["status"] == "unknown" and reason in result["reasons"]
    assert result["targets"] == []


def test_matching_balances_do_not_certify_complete_ledger(client,database,make_case):
    case_id = make_case()
    ids = ledger(database,case_id,[("in","100.00","100.00"),("out","100.00","0.00")])
    assert run(client,body(case_id,ids,conditions={})) ["status"] == "unknown"


@pytest.mark.parametrize("currency", ["UNKNOWN", "未知", " "])
@pytest.mark.parametrize("network", [False, True])
def test_unknown_currency_placeholder_is_not_one_fungible_currency(client,database,make_case,currency,network):
    case_id = make_case()
    ids = ledger(database,case_id,[("in","100.00","100.00"),("out","100.00","0.00")])
    database.x("update flows set currency=? where case_id=?", [currency,case_id])
    request = body(case_id,ids,currency=currency)
    if network:
        request["accounts"] = [{"account":request.pop("account"),"conditions":request.pop("conditions")}]
        request["boundary"] = "terminal_no_return_declared"
    response = client.post("/api/analysis/principal" + ("/network" if network else ""),json=request)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "unknown" and result["targets"] == []
    assert "currency_evidence_unknown" in result["reasons"]


@pytest.mark.parametrize("inside_interval", [True, False])
def test_unknown_currency_competitor_cannot_be_filtered_out_of_known_ledger(client,database,make_case,inside_interval):
    case_id = make_case()
    ids = ledger(database,case_id,[("in","100.00",None),("out","10.00",None),("out","90.00",None)])
    database.x("update flows set currency='UNKNOWN' where id=?", [ids[1]])
    if not inside_interval:
        database.x("update flows set trade_time='2025-01-01 10:00:00' where id=?", [ids[1]])
    request = body(case_id,ids)
    request["conditions"]["balance_kind"] = "unknown"
    result = run(client,request)
    if inside_interval:
        assert result["status"] == "unknown" and result["targets"] == []
        assert "currency_evidence_unknown" in result["reasons"]
    else:
        assert result["status"] == "unknown" and result["targets"] == []
        assert "currency_evidence_unknown" not in result["reasons"]
        assert result["ledger_count"] == 2


@pytest.mark.parametrize("amount", [None, Decimal("-1.00")])
def test_legacy_missing_or_signed_amount_does_not_become_a_source_share(client,database,make_case,amount):
    case_id = make_case()
    ids = ledger(database,case_id,[("in","100.00",None),("out","100.00",None)])
    database.x("update flows set amount=? where case_id=? and id=?",[amount,case_id,ids[1]])
    result = run(client,body(case_id,ids))
    assert result["status"] == "unknown" and result["targets"] == []
    assert "amount_evidence_unknown" in result["reasons"]


def test_untyped_balance_is_unknown_and_explicit_book_model_detects_inconsistency(client,database,make_case):
    case_id = make_case()
    ids = ledger(database,case_id,[("in","100.00","100.00"),("out","100.00","50.00")])
    result = run(client,body(case_id,ids))
    assert result["status"] == "unknown" and result["targets"] == []
    assert result["conditions"]["balance_kind"] == "unknown"
    entries, reason = principal_ledger.ordered(own_rows(case_id), 0, {}, "post_book")
    assert reason is None
    assert principal.allocate_ledger(0, entries, {ids[0]}, [ids[-1]])["status"] == "inconsistency"


def test_same_timestamp_is_inferred_only_when_unique_not_by_id(client,database,make_case):
    case_id = make_case()
    ids = ledger(database,case_id,[("out","100.00",None),("in","100.00",None)])
    database.x("update flows set trade_time='2026-01-01 10:00:00' where case_id=?",[case_id])
    result = run(client,body(case_id,list(reversed(ids))))
    assert result["status"] == "unknown" and result["targets"] == []
    entries, reason = principal_ledger.ordered(own_rows(case_id), 0, {}, "unknown")
    assert reason is None
    assert principal.allocate_ledger(0, entries, {ids[-1]}, [ids[0]])["targets"][0]["min"] == 10000
    request = body(case_id,list(reversed(ids)))
    request["conditions"]["opening_balance"] = "100.00"
    assert run(client,request)["status"] == "unknown"
    assert principal_ledger.ordered(own_rows(case_id), 10000, {}, "unknown")[1] == "posting_order_unknown"


def test_other_currency_and_other_cases_never_contribute(client,database,make_case):
    case_id, other = make_case(), make_case("Other synthetic")
    ids = ledger(database,case_id,[("in","100.00","100.00"),("out","100.00","0.00")])
    other_ids = ledger(database,other,[("in","999.00",None)])
    database.x("insert into flows(case_id,trade_time,self_account,payer_account,payee_account,amount,currency) "
               "values (?,'2026-01-01 10:00:30','SYN-B','SYN-B','SYN-X',900,'USD')",[case_id])
    result = run(client,body(case_id,ids))
    assert result["status"] == "unknown" and result["ledger_count"] == 2
    result = run(client,body(case_id,ids,target_flow_ids=other_ids))
    assert result["status"] == "unknown" and not result["targets"]


def test_public_import_invalidates_previous_analysis_and_export(client,database,make_case,flow_row):
    case_id = make_case()
    ids = ledger(database,case_id,[("in","100.00","100.00"),("out","100.00","0.00")])
    request = body(case_id,ids)
    result = run(client,request)
    importer.commit_rows("synthetic.csv","A",[flow_row(trade_no="NEW",amount=Decimal("0.01"))],case_id=case_id)
    for path in ("/api/analysis/principal","/api/analysis/principal/export"):
        response = client.post(path,json={**request,"expected_revision":result["revision"]})
        assert response.status_code == 409


def test_generation_change_rolls_back_with_transaction(database,make_case):
    case_id = make_case()
    revision = analysis_snapshot.revision(case_id)
    with pytest.raises(RuntimeError):
        with database.transaction():
            analysis_snapshot.invalidate(case_id)
            raise RuntimeError("synthetic failure")
    assert analysis_snapshot.revision(case_id) == revision


@pytest.mark.parametrize("field,value", [("case_id",1),("source_flow_id",1),("conditions",{"opening_balance":0}),
                                            ("conditions",{"opening_balance":"0.001"})])
def test_exact_string_input_boundary(client,database,make_case,field,value):
    case_id=make_case()
    ids=ledger(database,case_id,[("in","1.00","1.00"),("out","1.00","0.00")])
    request=body(case_id,ids)
    request[field]=value
    assert client.post("/api/analysis/principal",json=request).status_code == 422
