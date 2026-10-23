"""Public account candidates use bounded, literal, case-bound synthetic queries."""

import pytest

from test_account_registry import identity, listing
from test_cases import insert_flow


def suggestions(client, case_id, q, **params):
    response = client.get("/api/account-suggestions", params={"case_id": case_id, "q": q, **params})
    assert response.status_code == 200, response.text
    return response.json()


def flow(database, case_id, flow_row, account, name="合成张三丰"):
    return insert_flow(database, case_id, flow_row, payer_account=account, payer_name=name,
                       payee_account="", payee_name="")


def test_homonyms_accounts_and_identity_are_bound_to_the_case(client, database, make_case, flow_row):
    cid, other = make_case("Suggestions A"), make_case("Suggestions B")
    for account in ("0001", "0002"):
        flow(database, cid, flow_row, account)
    flow(database, other, flow_row, "0001", "Other-case holder")
    flow(database, other, flow_row, "OTHER-ONLY")
    identity(database, cid, "0001", "SYN-ID-A")
    identity(database, other, "0001", "SYN-ID-B")
    result = suggestions(client, cid, "张")
    assert result == {"case_id": str(cid), "source": "flows", "query": "张", "items": [
        {"account": "0001", "name": "合成张三丰", "id_no": "SYN-ID-A", "matched_field": "name", "matched_text": "合成张三丰"},
        {"account": "0002", "name": "合成张三丰", "id_no": None, "matched_field": "name", "matched_text": "合成张三丰"},
    ], "has_more": False, "next_offset": None}
    assert suggestions(client, cid, "SYN-ID-B")["items"] == []
    assert suggestions(client, other, "SYN-ID-B")["items"][0]["name"] == "Other-case holder"
    assert suggestions(client, cid, "SYN-ID-A")["items"][0]["matched_field"] == "id"


def test_all_original_flow_aliases_and_manual_name_return_the_actual_match(client, database, make_case, flow_row):
    cid = make_case()
    for name in ("张三丰", "曾用别名", "张三丰"):
        flow(database, cid, flow_row, "000-ALIAS", name)
    database.x("insert into main_accounts(case_id,account,name,source) values (?,?,'手工保留姓名','manual')", [cid, "000-ALIAS"])
    for query, matched in (("张", "张三丰"), ("曾用", "曾用别名"), ("手工", "手工保留姓名")):
        items = suggestions(client, cid, query)["items"]
        assert len(items) == 1
        assert items[0] == {"account": "000-ALIAS", "name": "手工保留姓名", "id_no": None,
                            "matched_field": "name", "matched_text": matched}


def test_rank_is_exact_then_prefix_then_contains_with_stable_field_and_account_ties(client, database, make_case, flow_row):
    cid = make_case()
    for account, name in (("880", "Plain"), ("ID-EXACT", "Plain"), ("NAME-EXACT", "880"),
                          ("88002", "Plain"), ("88001", "Plain"), ("ID-PREFIX", "Plain"),
                          ("NAME-PREFIX", "880 Name"), ("1880", "Plain"), ("NAME-CONTAINS", "Name 880")):
        flow(database, cid, flow_row, account, name)
    identity(database, cid, "ID-EXACT", "880")
    identity(database, cid, "ID-PREFIX", "880999")
    expected = ["880", "ID-EXACT", "NAME-EXACT", "88001", "88002", "ID-PREFIX", "NAME-PREFIX", "1880", "NAME-CONTAINS"]
    for _ in range(2):
        assert [item["account"] for item in suggestions(client, cid, "880")["items"]] == expected
    assert suggestions(client, cid, "880")["items"][1]["matched_text"] == "880"


def test_query_normalization_preserves_original_long_and_leading_zero_accounts(client, database, make_case, flow_row):
    cid = make_case()
    account = "000123456789012345678901234567890"
    flow(database, cid, flow_row, account, "MiXeD Synthetic")
    identity(database, cid, account, "SYN-ID-000000000000000000001X")
    fullwidth = "　" + "".join(chr(ord(char) + 0xFEE0) for char in account) + "　"
    value = suggestions(client, cid, fullwidth)
    assert value["query"] == account
    assert value["items"][0] == {"account": account, "name": "MiXeD Synthetic", "id_no": "SYN-ID-000000000000000000001X",
                                 "matched_field": "account", "matched_text": account}
    assert suggestions(client, cid, "  ｍＩｘＥｄ  ")["query"] == "mIxEd"
    assert suggestions(client, cid, "  ｍＩｘＥｄ  ")["items"][0]["matched_text"] == "MiXeD Synthetic"
    assert suggestions(client, cid, "000123456789012345678901234567891")["items"] == []


@pytest.mark.parametrize("query", ["%", "_", "\\", "' OR 1=1 --"])
def test_query_characters_are_literal_not_sql_or_like_patterns(client, database, make_case, flow_row, query):
    cid = make_case()
    account = f"000-{query}-SYN"
    flow(database, cid, flow_row, account, "Plain")
    flow(database, cid, flow_row, "000-PLAIN-SYN", "Plain")
    assert [row["account"] for row in suggestions(client, cid, query)["items"]] == [account]


def test_source_is_a_union_of_flow_and_identity_accounts_without_fabricated_rows(client, database, make_case, flow_row):
    cid = make_case()
    flow(database, cid, flow_row, "SYN-FLOW", "Synthetic flow")
    flow(database, cid, flow_row, "SYN-BOTH", "Synthetic both")
    flow(database, cid, flow_row, "", "SYN-MISSING-ACCOUNT")
    flow(database, cid, flow_row, "  ", "SYN-SPACE-ACCOUNT")
    identity(database, cid, "SYN-BOTH", "ID-BOTH")
    identity(database, cid, "SYN-ID-ONLY", "ID-ONLY")
    database.x("insert into main_accounts(case_id,account,name,source) values (?,'SYN-MARK-ONLY','Synthetic mark','manual')", [cid])
    assert {row["account"] for row in suggestions(client, cid, "SYN", source="flows")["items"]} == {"SYN-FLOW", "SYN-BOTH"}
    assert {row["account"] for row in suggestions(client, cid, "SYN", source="identity")["items"]} == {"SYN-ID-ONLY", "SYN-BOTH"}
    all_items = suggestions(client, cid, "SYN", source="all")["items"]
    assert {row["account"] for row in all_items} == {"SYN-FLOW", "SYN-ID-ONLY", "SYN-BOTH"}
    only = next(row for row in all_items if row["account"] == "SYN-ID-ONLY")
    assert only["name"] == "" and only["id_no"] == "ID-ONLY"
    assert suggestions(client, cid, "ID-ONLY", source="flows")["items"] == []
    assert suggestions(client, cid, "ID-ONLY", source="identity")["items"][0]["account"] == "SYN-ID-ONLY"


def test_limit_plus_one_pages_are_repeatable_and_do_not_duplicate_or_skip_accounts(client, database, make_case, flow_row):
    cid = make_case()
    for number in range(23):
        account = f"000{number:03d}"
        for alias in ("张三丰", "张别名"):
            flow(database, cid, flow_row, account, alias)
    expected = [row["account"] for row in suggestions(client, cid, "张", page_size=50)["items"]]
    received, offset = [], 0
    while True:
        value = suggestions(client, cid, "张", page_size=7, offset=offset)
        assert value == suggestions(client, cid, "张", page_size=7, offset=offset)
        assert len(value["items"]) <= 7
        received.extend(row["account"] for row in value["items"])
        if not value["has_more"]:
            assert value["next_offset"] is None
            break
        assert value["next_offset"] == offset + 7
        offset = value["next_offset"]
    assert received == expected and len(received) == len(set(received)) == 23
    assert suggestions(client, cid, "张", page_size=7, offset=23)["items"] == []
    last_full = suggestions(client, cid, "张", page_size=23)
    assert len(last_full["items"]) == 23 and last_full["has_more"] is False


@pytest.mark.parametrize("query", ["", "  ", "　"])
def test_empty_query_requires_the_case_but_never_scans_or_counts_candidates(client, database, make_case, monkeypatch, query):
    cid = make_case()
    original = database.q_raw
    calls = []

    def observed(sql, params=None):
        calls.append(sql)
        return original(sql, params)

    monkeypatch.setattr(database, "q_raw", observed)
    assert suggestions(client, cid, query) == {"case_id": str(cid), "source": "flows", "query": "",
                                              "items": [], "has_more": False, "next_offset": None}
    assert len(calls) == 1 and "from cases" in calls[0].lower()
    response = client.get("/api/account-suggestions", params={"case_id": 999999, "q": query})
    assert response.status_code == 404 and response.json()["detail"] == "案件不存在，请重新选择案件"


@pytest.mark.parametrize("params", [{"source": "people"}, {"page_size": 0}, {"page_size": 51},
                                    {"offset": -1}, {"offset": 2147483648}, {"case_id": 0},
                                    {"case_id": 2147483648}, {"q": "a" * 201}])
def test_invalid_suggestion_input_is_rejected_by_public_http(client, make_case, params):
    response = client.get("/api/account-suggestions", params={"case_id": make_case(), "q": "x", **params})
    assert response.status_code == 422


def test_suggestions_require_explicit_case_binding(client):
    assert client.get("/api/account-suggestions", params={"q": "张"}).status_code == 422


@pytest.mark.parametrize("account,neighbor", [("0001", "10001"), ("AC%T", "AC-ANY-T"),
                                             ("AC_T", "ACXT"), ("MiXeD", "mixed"),
                                             ("000123456789012345678901234567890", "123456789012345678901234567890")])
def test_registry_exact_mode_targets_one_original_account_within_source_and_case(client, database, make_case, flow_row, account, neighbor):
    cid, other = make_case("Exact registry A"), make_case("Exact registry B")
    for owner in (cid, other):
        for key in (account, neighbor):
            flow(database, owner, flow_row, key, "Same synthetic holder")
            identity(database, owner, key, "SYN-ID")
    for source in ("flows", "identity"):
        exact = listing(client, cid, q=f" {account} ", source=source, account_exact=True)
        assert exact["total"] == 1 and exact["items"][0]["account"] == account
        assert exact["source"] == source and exact["case_id"] == str(cid)
    fuzzy = listing(client, cid, q=account)
    assert fuzzy == listing(client, cid, q=account, account_exact=False)
    assert listing(client, cid, q="Same synthetic holder", account_exact=True)["items"] == []
    assert listing(client, cid, q="SYN-ID", account_exact=True)["items"] == []


def test_registry_default_contains_search_is_preserved_for_exact_account_prefix_conflicts(client, database, make_case, flow_row):
    cid = make_case()
    for key in ("0001", "10001"):
        flow(database, cid, flow_row, key)
    assert [row["account"] for row in listing(client, cid, q="0001")["items"]] == ["0001", "10001"]
    assert [row["account"] for row in listing(client, cid, q="0001", account_exact=True)["items"]] == ["0001"]


def test_registry_normalizes_free_queries_without_changing_exact_account_strings(client, database, make_case, flow_row):
    cid, other = make_case("Normalized registry"), make_case("Other normalized registry")
    for account, name in (("0001", "MiXeD synthetic holder"), ("10001", "Other holder"), ("０００１", "Wide key holder")):
        flow(database, cid, flow_row, account, name)
        identity(database, cid, account, "SYN-ID-A" if account == "0001" else "SYN-ID-OTHER")
    flow(database, other, flow_row, "0001", "Other case holder")
    identity(database, other, "0001", "SYN-ID-B")
    for source in ("flows", "identity"):
        assert [row["account"] for row in listing(client, cid, source=source, q="　ＳＹＮ－ＩＤ－Ａ　")["items"]] == ["0001"]
        assert listing(client, cid, source=source, q="ＳＹＮ－ＩＤ－Ｂ")["items"] == []
        assert [row["account"] for row in listing(client, cid, source=source, q="ｍｉｘｅｄ")["items"]] == ["0001"]
        assert [row["account"] for row in listing(client, cid, source=source, q="０００１")["items"]] == ["0001", "10001"]
        assert [row["account"] for row in listing(client, cid, source=source, q="0001", account_exact=True)["items"]] == ["0001"]
        assert [row["account"] for row in listing(client, cid, source=source, q="０００１", account_exact=True)["items"]] == ["０００１"]


def test_exact_counter_uses_both_account_endpoints_not_names_or_containing_accounts(client, database, make_case, flow_row):
    cid, other = make_case("Exact counter A"), make_case("Exact counter B")
    sent = insert_flow(database, cid, flow_row, payer_account="0001", payee_account="TAIL",
                       payer_name="Sender", payee_name="Receiver", trade_time="2026-01-01 10:00:00")
    received = insert_flow(database, cid, flow_row, payer_account="TAIL", payee_account="0001",
                           payer_name="Sender", payee_name="Receiver", trade_time="2026-01-01 11:00:00")
    insert_flow(database, cid, flow_row, payer_account="10001", payee_account="TAIL",
                payer_name="Sender", payee_name="Receiver")
    insert_flow(database, cid, flow_row, payer_account="NAME-ONLY", payee_account="TAIL",
                payer_name="0001", payee_name="Receiver")
    foreign = insert_flow(database, other, flow_row, payer_account="0001", payee_account="TAIL")
    base = {"case_id": cid, "counter": "0001"}
    fuzzy = client.get("/api/flows", params=base).json()
    assert fuzzy["total"] == 4
    assert client.get("/api/flows", params={**base, "counter_exact": False}).json() == fuzzy
    exact = client.get("/api/flows", params={**base, "counter": " 0001 ", "counter_exact": True}).json()
    assert exact["total"] == 2 and [row["id"] for row in exact["rows"]] == [received, sent]
    assert [row["id"] for row in client.get("/api/flows", params={**base, "counter_exact": True, "case_id": other}).json()["rows"]] == [foreign]
    combined = client.get("/api/flows", params={**base, "counter_exact": True, "account": "TAIL", "account_exact": True,
                                               "name": "Receiver", "tfrom": "2026-01-01", "tto": "2026-01-01"}).json()
    assert [row["id"] for row in combined["rows"]] == [received, sent]


def test_free_counter_matches_normalized_identity_only_for_case_bound_account_endpoints(client, database, make_case, flow_row):
    cid, other = make_case("Counter identity"), make_case("Other counter identity")
    sent = insert_flow(database, cid, flow_row, payer_account="0001", payee_account="TAIL", payer_name="Sender", payee_name="Receiver")
    received = insert_flow(database, cid, flow_row, payer_account="TAIL", payee_account="0001", payer_name="Sender", payee_name="Receiver")
    flow(database, cid, flow_row, "10001", "Same sender")
    flow(database, other, flow_row, "0001", "Other case")
    identity(database, cid, "0001", "SYN-ID-A")
    identity(database, cid, "ID-ONLY", "SYN-ID-ONLY")
    identity(database, other, "0001", "SYN-ID-B")
    identity(database, other, "10001", "SYN-ID-A")
    for query in ("SYN-ID-A", "　ＳＹＮ－ＩＤ－Ａ　"):
        result = client.get("/api/flows", params={"case_id": cid, "counter": query}).json()
        assert result["total"] == 2 and {row["id"] for row in result["rows"]} == {sent, received}
    for query in ("SYN-ID-B", "SYN-ID-ONLY"):
        assert client.get("/api/flows", params={"case_id": cid, "counter": query}).json()["total"] == 0
    # Even text matching a known identity is not an account key in exact mode.
    assert client.get("/api/flows", params={"case_id": cid, "counter": "SYN-ID-A", "counter_exact": True}).json()["total"] == 0
    exact = client.get("/api/flows", params={"case_id": cid, "counter": "0001", "counter_exact": True}).json()
    assert exact["total"] == 2 and {row["id"] for row in exact["rows"]} == {sent, received}
    broad = client.get("/api/flows", params={"case_id": cid, "counter": "0001"}).json()
    assert broad["total"] == 3


@pytest.mark.parametrize("literal", ["ID%TOKEN", "ID_TOKEN", "ID\\TOKEN"])
def test_counter_identity_match_is_literal_while_existing_name_account_like_behavior_remains(client, database, make_case, flow_row, literal):
    cid = make_case()
    target = flow(database, cid, flow_row, "0001", "Plain holder")
    flow(database, cid, flow_row, "0002", "Plain holder")
    identity(database, cid, "0001", literal)
    identity(database, cid, "0002", "ID-ANY-TOKEN")
    result = client.get("/api/flows", params={"case_id": cid, "counter": literal}).json()
    assert result["total"] == 1 and [row["id"] for row in result["rows"]] == [target]


def test_free_entity_query_finds_all_homonyms_identity_and_account_rows_without_candidate_limits(client, database, make_case, flow_row):
    cid, other = make_case("Free entity A"), make_case("Free entity B")
    expected = []
    # More than both the default suggestion page and its maximum page size.
    for number in range(63):
        account = f"000{number:03d}"
        expected.append(flow(database, cid, flow_row, account, "张三丰"))
    received = insert_flow(database, cid, flow_row, payer_account="TAIL", payee_account="000000",
                           payer_name="Sender", payee_name="张三丰")
    expected.append(received)
    foreign = flow(database, other, flow_row, "000000", "张三丰")
    foreign_only = flow(database, other, flow_row, "000099", "张三丰")
    identity(database, cid, "000000", "SYN-IDENTITY-A")
    identity(database, other, "000000", "SYN-IDENTITY-B")
    response = client.get("/api/flows", params={"case_id": cid, "entity": "张", "size": 500})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total"] == 64 and set(row["id"] for row in result["rows"]) == set(expected)
    assert foreign not in {row["id"] for row in result["rows"]}
    assert foreign_only not in {row["id"] for row in result["rows"]}
    first_page = client.get("/api/flows", params={"case_id": cid, "entity": "张", "size": 50}).json()
    second_page = client.get("/api/flows", params={"case_id": cid, "entity": "张", "size": 50, "page": 2}).json()
    assert first_page["total"] == second_page["total"] == 64
    assert len(first_page["rows"]) == 50 and len(second_page["rows"]) == 14
    assert set(row["id"] for row in first_page["rows"]).isdisjoint(row["id"] for row in second_page["rows"])
    by_id = client.get("/api/flows", params={"case_id": cid, "entity": "IDENTITY-A"}).json()
    assert by_id["total"] == 2 and {row["id"] for row in by_id["rows"]} == {expected[0], received}
    assert client.get("/api/flows", params={"case_id": cid, "entity": "IDENTITY-B"}).json()["total"] == 0
    fullwidth_account = "００００００"
    by_account = client.get("/api/flows", params={"case_id": cid, "entity": f" {fullwidth_account} "}).json()
    assert by_account["total"] == 2 and {row["id"] for row in by_account["rows"]} == {expected[0], received}
    # Existing account semantics still only inspect account columns.
    assert client.get("/api/flows", params={"case_id": cid, "account": "张"}).json()["total"] == 0
    legacy = client.get("/api/flows", params={"case_id": cid, "account": "0000"}).json()
    assert client.get("/api/flows", params={"case_id": cid, "account": "0000", "entity": ""}).json() == legacy
    combined = client.get("/api/flows", params={"case_id": cid, "entity": "张", "account": "000000", "account_exact": True,
                                               "name": "Sender", "counter": "TAIL", "counter_exact": True}).json()
    assert combined["total"] == 1 and [row["id"] for row in combined["rows"]] == [received]


@pytest.mark.parametrize("query", ["%", "_", "\\", "' OR 1=1 --"])
def test_free_entity_query_treats_special_characters_as_literal_text(client, database, make_case, flow_row, query):
    cid = make_case()
    target = flow(database, cid, flow_row, f"000-{query}-SYN", "Holder")
    flow(database, cid, flow_row, "000-PLAIN-SYN", "Holder")
    result = client.get("/api/flows", params={"case_id": cid, "entity": query}).json()
    assert result["total"] == 1 and [row["id"] for row in result["rows"]] == [target]


def test_free_entity_contains_account_search_differs_from_selected_exact_account(client, database, make_case, flow_row):
    cid = make_case()
    exact = flow(database, cid, flow_row, "0001", "Plain")
    neighbor = flow(database, cid, flow_row, "10001", "Plain")
    broad = client.get("/api/flows", params={"case_id": cid, "entity": "0001"}).json()
    assert broad["total"] == 2 and {row["id"] for row in broad["rows"]} == {exact, neighbor}
    selected = client.get("/api/flows", params={"case_id": cid, "account": "0001", "account_exact": True}).json()
    assert selected["total"] == 1 and [row["id"] for row in selected["rows"]] == [exact]
