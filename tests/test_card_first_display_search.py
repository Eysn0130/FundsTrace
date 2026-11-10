"""Card-first display and search for ordinary table sources.

The posting's own card is a scoped display alias for its account: the canvas and
flow rows show it first, search matches it, and details keep both the account and
every observed card. Identity, aggregation and deduplication stay account-bound.
"""
import io

import pytest
from openpyxl import load_workbook

from test_automatic_table_import import complete, preview


HEADER = ("交易卡号,交易账号,交易时间,交易金额,交易余额,收付标志,交易对手账卡号,对手户名,"
          "摘要说明,交易流水号,交易币种,交易是否成功\n")


def rows():
    return (HEADER + (
        "C1,A,2026-01-01 10:00:00,10,90,出,B,对手,转账,T1,CNY,01\n"
        "C2,A,2026-02-01 10:00:00,20,70,出,B,对手,转账,T2,CNY,01\n"
        ",A,2026-03-01 10:00:00,30,40,出,B,对手,转账,T3,CNY,01\n"
        "A,A,2026-04-01 10:00:00,40,0,出,B,对手,转账,T4,CNY,01\n"
        "C3,D,2026-01-05 10:00:00,50,50,出,B,对手,转账,T5,CNY,01\n"
    )).encode()


def imported(client, cid):
    view = preview(client, cid, rows())
    assert view["stats"]["accepted"] == 5
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 5, job
    return view


def exported_rows(client, cid):
    response = client.get("/api/export/tracked.xlsx", params={"case_id": cid})
    assert response.status_code == 200, response.text
    book = load_workbook(io.BytesIO(response.content), data_only=True)
    values = list(book["追踪流水"].values)
    book.close()
    return [dict(zip(values[0], row)) for row in values[1:]]


def test_flow_rows_display_the_posting_card_and_fall_back_to_the_account(client, database, make_case):
    cid = make_case("Synthetic card display")
    imported(client, cid)
    page = client.get(f"/api/v1/cases/{cid}/flows?account=A&page_size=20").json()
    assert page["total"] == 4
    by_trade = {row["trade_no"]: row for row in page["items"]}
    assert by_trade["T1"]["payer_display_account"] == "C1"
    assert by_trade["T2"]["payer_display_account"] == "C2"
    assert by_trade["T3"]["payer_display_account"] == "A"
    assert by_trade["T4"]["payer_display_account"] == "A"
    assert all(row["self_display_account"] == row["payer_display_account"] for row in page["items"])


def test_search_matches_card_or_account_and_card_equals_account_is_not_a_card(client, database, make_case):
    cid = make_case("Synthetic card search")
    imported(client, cid)
    card = client.get(f"/api/v1/cases/{cid}/flows?account=C1&page_size=20").json()
    assert card["total"] == 1 and card["items"][0]["trade_no"] == "T1"
    account = client.get(f"/api/v1/cases/{cid}/flows?account=A&page_size=20").json()
    assert account["total"] == 4
    other = client.get(f"/api/v1/cases/{cid}/flows?account=C3&page_size=20").json()
    assert other["total"] == 1 and other["items"][0]["trade_no"] == "T5"


def test_graph_node_prefers_the_latest_card_and_details_list_every_account_and_card(client, database, make_case):
    cid = make_case("Synthetic card canvas")
    imported(client, cid)
    ids = [row["id"] for row in client.get(f"/api/v1/cases/{cid}/flows?page_size=20").json()["items"]]
    tracked = client.post("/api/track", json={"case_id": cid, "canvas_id": 0, "flow_ids": ids})
    assert tracked.status_code == 200, tracked.text
    nodes = {node["account"]: node for node in client.get(f"/api/graph?case_id={cid}").json()["nodes"]}
    node = nodes["A"]
    assert node["display_account"] == "C2"
    assert sorted(node["cards"]) == ["C1", "C2"]
    assert node["account_number"] == "A"
    assert node["display_card_tied"] is False


def test_registry_and_suggestions_locate_the_account_by_card(client, database, make_case):
    cid = make_case("Synthetic card registry")
    imported(client, cid)
    registry = client.get(f"/api/account-registry?case_id={cid}&q=C1").json()
    assert [item["account"] for item in registry["items"]] == ["A"]
    assert registry["items"][0]["display_account"] == "C2"
    assert sorted(registry["items"][0]["cards"]) == ["C1", "C2"]
    suggestion = client.get(f"/api/account-suggestions?case_id={cid}&q=C1").json()
    assert [item["account"] for item in suggestion["items"]] == ["A"]
    assert suggestion["items"][0]["display_account"] == "C2"
    profile = client.get(f"/api/account-registry/profile?case_id={cid}&account=A").json()
    assert profile["display_account"] == "C2" and profile["account_number"] == "A"
    assert sorted(profile["cards"]) == ["C1", "C2"]


def test_a_card_shared_by_two_accounts_keeps_accounts_visible(client, database, make_case):
    cid = make_case("Synthetic shared card")
    content = (HEADER + (
        "X1,P,2026-01-01 10:00:00,10,90,出,B,对手,转账,T1,CNY,01\n"
        "X1,Q,2026-02-01 10:00:00,20,70,出,B,对手,转账,T2,CNY,01\n"
    )).encode()
    view = preview(client, cid, content)
    job = complete(client, cid, view)
    assert job["result"]["ok"] == 2, job
    ids = [row["id"] for row in client.get(f"/api/v1/cases/{cid}/flows?page_size=20").json()["items"]]
    assert client.post("/api/track", json={"case_id": cid, "canvas_id": 0, "flow_ids": ids}).status_code == 200
    nodes = {node["account"]: node for node in client.get(f"/api/graph?case_id={cid}").json()["nodes"]}
    assert nodes["P"]["display_account"] == "X1" and nodes["Q"]["display_account"] == "X1"
    assert nodes["P"]["shared_accounts"] == ["Q"]
    assert nodes["Q"]["shared_accounts"] == ["P"]
    profile = client.get(f"/api/account-registry/profile?case_id={cid}&account=P").json()
    assert profile["display_account"] == "X1" and profile["account_number"] == "P"


@pytest.mark.parametrize("reverse", [False, True])
def test_mirrored_source_cards_stay_with_their_own_accounts(client, database, make_case, reverse):
    cid = make_case("Synthetic mirrored source cards")
    lines = ["C-A,A,2026-01-01 10:00:00,10,90,出,B,,same,T1,CNY,01\n",
             "C-B,B,2026-01-01 10:00:00,10,90,进,A,,same,T1,CNY,01\n"]
    view = preview(client, cid, (HEADER + "".join(reversed(lines) if reverse else lines)).encode())
    job = complete(client, cid, view)
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (1, 1)
    page = client.get(f"/api/v1/cases/{cid}/flows").json()
    row, = page["items"]
    assert row["payer_display_account"] == "C-A"
    assert row["payee_display_account"] == "C-B"
    assert (row["payer_account"], row["payee_account"], row["amount"]) == ("A", "B", "10.00")
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid])[0]["n"] == 1
    for card, account in (("C-A", "A"), ("C-B", "B")):
        candidates = client.get(f"/api/account-suggestions?case_id={cid}&q={card}").json()["items"]
        assert [item["account"] for item in candidates] == [account]
        assert client.get(f"/api/v1/cases/{cid}/flows?account={card}").json()["total"] == 1
        assert client.get(f"/api/flows?case_id={cid}&account={card}").json()["total"] == 1
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"]]}).status_code == 200
    graph = client.get(f"/api/graph?case_id={cid}").json()
    nodes = {node["account"]: node for node in graph["nodes"]}
    assert nodes["A"]["display_account"] == "C-A" and nodes["A"]["cards"] == ["C-A"]
    assert nodes["B"]["display_account"] == "C-B" and nodes["B"]["cards"] == ["C-B"]
    assert graph["edges"][0]["total"] == "10.00"
    exported, = exported_rows(client, cid)
    assert exported["当笔本方卡号"] == ("C-B" if reverse else "C-A")


def test_disagreeing_cards_for_one_posting_are_searchable_without_an_arbitrary_label(client, database, make_case):
    cid = make_case("Synthetic conflicting posting cards")
    content = (HEADER + "C1,A,2026-01-01 10:00:00,10,90,出,B,,same,T1,CNY,01\n"
                       "C2,A,2026-01-01 10:00:00,10,90,出,B,,same,T1,CNY,01\n").encode()
    job = complete(client, cid, preview(client, cid, content))
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (1, 1)
    row, = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert row["payer_display_account"] == row["self_display_account"] == "A"
    for card in ("C1", "C2"):
        assert client.get(f"/api/v1/cases/{cid}/flows?account={card}").json()["total"] == 1
        assert client.get(f"/api/flows?case_id={cid}&entity={card}").json()["total"] == 1
        assert [item["account"] for item in client.get(f"/api/account-suggestions?case_id={cid}&q={card}").json()["items"]] == ["A"]
        assert [item["account"] for item in client.get(f"/api/account-registry?case_id={cid}&q={card}").json()["items"]] == ["A"]
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"]]}).status_code == 200
    node = next(node for node in client.get(f"/api/graph?case_id={cid}").json()["nodes"] if node["account"] == "A")
    assert node["display_account"] == "A" and node["cards"] == ["C1", "C2"]
    assert node["display_card_order"] == "conflict" and node["display_card_tied"] is True
    exported, = exported_rows(client, cid)
    assert exported["当笔本方卡号"] is None and exported["交易金额"] == "10.00"


def test_typed_card_conflict_is_not_reenabled_by_the_source_alias(client, database, make_case):
    from test_folder_evidence import accepted, account, table, transaction
    cid = make_case("Synthetic typed source card conflict")
    accepted(client, cid, [("tx.csv", table("transaction", [transaction()])),
                           ("account.csv", table("account", [account()]))])
    _, conflict = accepted(client, cid, [("conflict.csv", table("account", [account(number="SYN-OTHER")]))])
    row, = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert row["payer_card_state"] == "conflict"
    assert row["payer_display_account"] == row["payer_source_account"] == "SYN-A"
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"]]}).status_code == 200
    own = next(node for node in client.get(f"/api/graph?case_id={cid}").json()["nodes"] if node["account"] == row["payer_account"])
    assert own["display_account"] == "SYN-A"
    exported, = exported_rows(client, cid)
    assert exported["当笔本方卡号"] is None
    assert client.delete(f"/api/batches/{conflict['batch_id']}", params={"case_id": cid}).status_code == 200
    row, = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert row["payer_card_state"] == "confirmed" and row["payer_display_account"] == "SYN-C1"


def test_old_card_projection_rebuild_package_reopen_and_withdraw(client, database, make_case):
    cid = make_case("Synthetic source card derived upgrade")
    first = complete(client, cid, preview(client, cid, (HEADER + "C1,A,2026-01-01 10:00:00,10,90,出,B,,same,T1,CNY,01\n").encode()))
    second = complete(client, cid, preview(client, cid, (HEADER + "C2,A,2026-02-01 10:00:00,20,70,出,B,,same,T2,CNY,01\n").encode()))
    assert first["stage"] == second["stage"] == "done"
    raw = database.q_raw("select * from flows where case_id=? order by id", [cid])
    with database._lock:
        database.x("drop table flow_source_cards")
        database.x("create table flow_source_cards(case_id bigint,flow_id bigint,own_card varchar,primary key(case_id,flow_id))")
        database.x("insert into flow_source_cards values (?,?,?)", [cid, raw[0]["id"], "SYN-STALE"])
        database._con.close()
        database._con = None
        database.con()
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == raw
    assert client.get(f"/api/account-registry?case_id={cid}&q=SYN-STALE").json()["total"] == 0
    own, = client.get(f"/api/account-registry?case_id={cid}&q=C1").json()["items"]
    assert own["cards"] == ["C1", "C2"] and own["display_account"] == "C2"
    packed = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("synthetic-cards.fundcase", packed.content)})
    assert restored.status_code == 200, restored.text
    restored_cid = restored.json()["case_id"]
    restored_own, = client.get(f"/api/account-registry?case_id={restored_cid}&q=C1").json()["items"]
    assert restored_own["cards"] == own["cards"] and restored_own["display_account"] == "C2"
    assert client.delete(f"/api/batches/{second['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    remaining, = client.get(f"/api/account-registry?case_id={cid}&q=C1").json()["items"]
    assert remaining["cards"] == ["C1"] and remaining["display_account"] == "C1"
    assert client.get(f"/api/account-suggestions?case_id={cid}&q=C2").json()["items"] == []


def test_same_amount_postings_and_other_cases_keep_their_original_membership(client, database, make_case):
    cid, other = make_case("Synthetic equal amounts"), make_case("Synthetic separate card case")
    content = (HEADER + "C1,A,2026-01-01 10:00:00,10,90,出,B,,same,T1,CNY,01\n"
                       "C2,A,2026-01-01 10:00:00,10,90,出,B,,same,T2,CNY,01\n").encode()
    job = complete(client, cid, preview(client, cid, content))
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (2, 0)
    complete(client, other, preview(client, other, (HEADER + "C1,Q,2026-01-01 10:00:00,10,90,出,B,,same,T1,CNY,01\n").encode()))
    page = client.get(f"/api/v1/cases/{cid}/flows?account=C1").json()
    assert page["total"] == 1 and page["items"][0]["trade_no"] == "T1"
    assert [item["account"] for item in client.get(f"/api/account-suggestions?case_id={cid}&q=C1").json()["items"]] == ["A"]
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert len(rows) == 2 and {row["payer_display_account"] for row in rows} == {"C1", "C2"}
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"] for row in rows]}).status_code == 200
    graph = client.get(f"/api/graph?case_id={cid}").json()
    own = next(node for node in graph["nodes"] if node["account"] == "A")
    assert own["cards"] == ["C1", "C2"] and own["shared_accounts"] == []
    assert graph["edges"][0]["total"] == "20.00"
