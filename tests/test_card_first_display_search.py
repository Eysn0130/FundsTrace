"""Card-first display and search for ordinary table sources.

The posting's own card is a scoped display alias for its account: the canvas and
flow rows show it first, search matches it, and details keep both the account and
every observed card. Identity, aggregation and deduplication stay account-bound.
"""
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
