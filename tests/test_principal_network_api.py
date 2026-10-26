"""Public source import -> confirmed events -> global principal -> export."""
from test_event_import import csv_rows, transaction
from test_folder_evidence import accepted


def own(own_account,payer,payee,ref,minute,balance,amount="100.00"):
    return transaction(own_account,**{"付款账号":payer,"收款账号":payee,"跨账簿事件参考号":ref,
        "交易时间":f"2026-01-01 10:{minute:02d}:00","本笔交易后账面余额":balance,"交易金额":amount})


def imported_network(client,cid,*,references=True):
    rows=[own("SYN-B","SYN-A","SYN-B","SYN-E1",0,"100.00"),
          own("SYN-B","SYN-B","SYN-C","SYN-E2",1,"0.00"),
          own("SYN-C","SYN-B","SYN-C","SYN-E2",1,"100.00"),
          own("SYN-C","SYN-C","SYN-D","SYN-E3",2,"0.00")]
    if not references:
        for row in rows:
            for key in ("参考号机构","参考号渠道","参考号业务域","跨账簿事件参考号"):
                row.pop(key)
    accepted(client,cid,[("synthetic-network.csv",csv_rows(rows))],scope_bank=None)
    response=client.get(f"/api/v1/cases/{cid}/flows")
    assert response.status_code == 200,response.text
    records=response.json()["items"]
    source=next(row for row in records if row["raw_payer_account"]=="SYN-A")
    target=next(row for row in records if row["raw_payee_account"]=="SYN-D")
    accounts={row["self_account"] for row in records}
    return {"case_id":str(cid),"currency":"CNY","start":"2026-01-01T00:00:00","end":"2026-01-01T23:59:59",
            "source_flow_id":source["id"],"target_flow_ids":[target["id"]],
            "accounts":[{"account":account,"conditions":{"coverage":"complete_declared","no_overdraft":True,"funds_available":True,
                         "opening_balance":"0.00","balance_kind":"post_book"}} for account in sorted(accounts)],
            "boundary":"terminal_no_return_declared"}


def test_same_process_public_import_multihop_and_exact_export(client,database,make_case):
    cid=make_case("Synthetic continuous network")
    request=imported_network(client,cid)
    result=client.post("/api/analysis/principal/network",json=request)
    assert result.status_code==200,result.text
    value=result.json()
    assert value["status"]=="ok",value
    assert value["event_count"]==3 and value["ledger_count"]==4
    target=value["targets"][0]
    assert (target["amount"],target["min"],target["max"],target["fifo"]) == ("100.00",)*4
    assert value["source_amount"] == value["fifo_witness"]["external_total"] == "100.00"
    assert value["fifo_witness"]["retained_total"] == "0.00"
    stats=client.get(f"/api/v1/cases/{cid}/stats").json()
    assert stats["event_summary"]["confirmed_events"]==1
    assert stats["amount_totals"]==[{"currency":"CNY","amount":"400.00"}]
    assert stats["analysis_revision"]==value["revision"]
    exported=client.post("/api/analysis/principal/network/export",json={**request,"expected_revision":value["revision"]})
    assert exported.status_code==200 and value["revision"] in exported.text
    assert "100.00" in exported.text and "完整分配见证" in exported.text


def test_internal_pair_without_scoped_evidence_is_unknown(client,make_case):
    cid=make_case()
    request=imported_network(client,cid,references=False)
    value=client.post("/api/analysis/principal/network",json=request).json()
    assert value["status"]=="unknown" and value["targets"]==[]
    assert value["reasons"]==["internal_transfer_requires_confirmed_opposite_ledgers"]


def test_boundary_and_account_openings_are_not_assumed(client,make_case):
    cid=make_case()
    request=imported_network(client,cid)
    request["boundary"]="unknown"
    value=client.post("/api/analysis/principal/network",json=request).json()
    assert value["reasons"]==["external_boundary_returns_unknown"]
    request["boundary"]="terminal_no_return_declared"
    request["accounts"][0]["conditions"]["opening_balance"]=None
    value=client.post("/api/analysis/principal/network",json=request).json()
    assert value["status"]=="unknown" and value["targets"]==[]
