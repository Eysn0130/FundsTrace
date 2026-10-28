"""Public tracked selections keep both funds windows usable with typed endpoints."""
from decimal import Decimal

from app import event_resolution, funds_projection
from funds_assertions import unresolved_funds
from test_event_import import csv_rows, transaction
from test_folder_evidence import accepted


def test_funds_window_with_joined_between_filter(database, make_case):
    cid = make_case("Synthetic joined range filter")
    database.x("""insert into flows(case_id,payer_account,payee_account,amount,currency)
        select ?, 'SYN-A', 'SYN-B', i + 1, 'CNY' from range(65) r(i)""", [cid])
    database.x("""insert into resolved_endpoints(case_id,flow_id,role,entity_id,state,reason)
        select ?, id, 'payer', 'SYN-M', 'resolved', 'synthetic' from flows where id % 2 = 0""", [cid])
    source = """select f.* from flows f left join resolved_endpoints e
        on e.case_id=f.case_id and e.flow_id=f.id and e.role='payer'
        where f.case_id=? and coalesce(e.entity_id,f.payer_account) between 'SYN-A' and 'SYN-Z'"""
    assert database.q_raw(funds_projection.cte(source) + "select count(*) n from selected_facts", [cid]) == [{"n": 65}]
    facts = database.q_raw(funds_projection.cte(source) + "select * from selected_facts order by id", [cid])
    assert [row["id"] for row in facts] == list(range(1, 66))
    assert [row["amount"] for row in facts] == [Decimal(number) for number in range(1, 66)]
    assert all(row["balance"] is None and row["fact_status"] == "unresolved" for row in facts)
    assert funds_projection.summary(source, [cid]) == unresolved_funds(65, ("CNY", "2145.00"))


def test_tracked_typed_subset_without_events_keeps_graph_funds(client, database, make_case):
    cid = make_case("Synthetic tracked funds windows")
    source = []
    for index in range(65):
        payer = index % 8 if index < 64 else 8
        payee = (index + 1) % 8 if index < 64 else 0
        row = transaction(f"SYN-WINDOW-{payer}", **{
            "交易流水号": f"SYN-WINDOW-{index:03}",
            "付款账号": f"SYN-WINDOW-{payer}",
            "收款账号": f"SYN-WINDOW-{payee}",
            "交易金额": f"{index + 1}.01",
            "付款人开户行": "",
            "收款人开户行": "",
            "本方开户银行": "",
            "本笔交易后账面余额": "",
        })
        source.append({key: value for key, value in row.items()
                       if key not in event_resolution.HEADERS.values()})
    _, result = accepted(client, cid, [("synthetic-window.csv", csv_rows(source))], scope_bank=None)
    assert result["ok"] == 65
    assert database.q_raw("select count(*) n from resolved_events where case_id=?", [cid]) == [{"n": 0}]
    assert database.q_raw("select count(*) n from resolution_entities where case_id=?", [cid]) == [{"n": 9}]
    rows = database.q_raw("select id,trade_no from flows where case_id=? order by trade_no", [cid])
    selected = [row["id"] for row in rows[51:59]]
    tracked = client.post("/api/track", json={"case_id": cid, "flow_ids": selected})
    assert tracked.status_code == 200, tracked.text
    with database._lock:
        database.con().execute("checkpoint")
        database._con.close()
        database._con = None
        database.con()
    response = client.get("/api/graph", params={"case_id": cid})
    assert response.status_code == 200, response.text
    graph = response.json()
    assert sorted(graph["tracked_ids"]) == sorted(selected)
    assert len(graph["nodes"]) == 8 and len(graph["edges"]) == 8
    assert graph["funds"] == unresolved_funds(8, ("CNY", "444.08"))
    assert sorted(flow_id for edge in graph["edges"] for flow_id in edge["ids"]) == sorted(selected)
    assert all(edge["cnt"] is None and edge["total"] is None for edge in graph["edges"])
    assert sorted(edge["funds"]["totals"][0]["upper"] for edge in graph["edges"]) == [f"{number}.01" for number in range(52, 60)]
