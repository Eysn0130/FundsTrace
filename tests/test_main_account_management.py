"""Synthetic main-account management journeys and classification regressions."""
import io
import time

import pytest

from app import main
from test_cases import insert_flow


def batch(database, case_id, mode="B", filename="synthetic.csv"):
    return database.q("insert into batches(case_id,filename,mode) values (?,?,?) returning id",
                      [case_id, filename, mode])[0]["id"]


def marks(client, case_id):
    response = client.get("/api/main-accounts", params={"case_id": case_id})
    assert response.status_code == 200, response.text
    return {row["account"]: row for row in response.json()}


def search(client, case_id, **params):
    response = client.get("/api/main-accounts/candidates", params={"case_id": case_id, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_candidates_search_all_case_flows_not_only_latest_flow_page(client, database, make_case, flow_row):
    cid, other = make_case("Search scope"), make_case("Other search scope")
    insert_flow(database, cid, flow_row, payer_account="000-OLDER", payer_name="Synthetic old owner",
                payee_account="000-SHARED", payee_name="Same synthetic owner")
    database.con().executemany("""insert into flows(case_id, payer_account, payer_name, payee_account, payee_name, amount)
                               values (?,?,'Synthetic recent','000-SHARED','Same synthetic owner',0.01)""",
                              [(cid, f"RECENT-{i:04}") for i in range(550)])
    insert_flow(database, other, flow_row, payer_account="000-OTHER", payer_name="Synthetic old owner")
    result = search(client, cid, q="old owner")
    assert result == {"items": [{"account": "000-OLDER", "name": "Synthetic old owner", "is_main": False, "source": None}],
                      "total": 1, "page": 1, "page_size": 30}
    all_accounts = search(client, cid, page_size=100)
    assert all_accounts["total"] == 552
    assert not any(row["account"] == "000-OTHER" for row in all_accounts["items"])
    assert search(client, cid, q="000-SHARED")["total"] == 1


@pytest.mark.parametrize("literal", ["%", "_", "\\", "' OR 1=1 --"])
def test_candidate_search_treats_query_as_literal_text(client, database, make_case, flow_row, literal):
    cid = make_case()
    account = f"000-SYN{literal}END"
    insert_flow(database, cid, flow_row, payer_account=account, payer_name="Literal synthetic owner")
    insert_flow(database, cid, flow_row, payer_account="000-PLAIN", payer_name="Unrelated synthetic owner")
    result = search(client, cid, q=literal)
    assert [row["account"] for row in result["items"]] == [account]
    assert result["total"] == 1


def test_candidates_distinct_account_pagination_names_sources_and_leading_zeros(client, database, make_case, flow_row):
    cid = make_case()
    for account in ["0003", "0001", "0002", "0001"]:
        insert_flow(database, cid, flow_row, payer_account=account, payer_name="同名合成主体",
                    payee_account=" ", payee_name="")
    assert client.post("/api/main-accounts", json={"case_id": cid, "account": "0002", "name": "手动合成名称"}).status_code == 200
    database.x("insert into main_accounts(case_id,account,name,source) values (?,'0003','自动合成名称','auto')", [cid])
    first = search(client, cid, page_size=2)
    assert first["total"] == 3
    assert first["items"] == [{"account": "0001", "name": "同名合成主体", "is_main": False, "source": None},
                              {"account": "0002", "name": "手动合成名称", "is_main": True, "source": "manual"}]
    second = search(client, cid, page_size=2, page=2)
    assert second["items"] == [{"account": "0003", "name": "自动合成名称", "is_main": True, "source": "auto"}]
    assert search(client, cid, q="手动合成")["items"][0]["account"] == "0002"
    assert search(client, cid, page_size=2, page=3)["items"] == []
    assert search(client, cid, page_size=2, page=3)["total"] == 3


def test_candidates_match_every_flow_name_alias_for_same_account(client, database, make_case, flow_row):
    cid, other = make_case("Account aliases"), make_case("Other account aliases")
    insert_flow(database, cid, flow_row, payer_account="000-ALIASED", payer_name="Alpha synthetic alias",
                payee_account="000-COUNTER", payee_name="Synthetic counterparty")
    insert_flow(database, cid, flow_row, payer_account="000-COUNTER", payer_name="Synthetic counterparty",
                payee_account="000-ALIASED", payee_name="Zeta synthetic alias")
    insert_flow(database, other, flow_row, payer_account="000-OTHER", payer_name="Alpha synthetic alias")
    for keyword in ("Alpha synthetic alias", "Zeta synthetic alias"):
        result = search(client, cid, q=keyword)
        assert result["total"] == 1
        assert result["items"] == [{"account": "000-ALIASED", "name": "Zeta synthetic alias", "is_main": False, "source": None}]


def test_exact_candidate_lookup_is_not_hidden_by_first_page_of_substring_matches(client, database, make_case, flow_row):
    cid, other = make_case("Exact candidate"), make_case("Other exact candidate")
    for i in range(35):
        insert_flow(database, cid, flow_row, payer_account=f"000-{i:02}-TARGET", payer_name="Earlier synthetic match")
    insert_flow(database, cid, flow_row, payer_account="TARGET", payer_name="Exact synthetic owner")
    insert_flow(database, other, flow_row, payer_account="TARGET", payer_name="Other case synthetic owner")
    assert client.post("/api/main-accounts", json={"case_id": cid, "account": "TARGET"}).status_code == 200
    substring = search(client, cid, q="TARGET")
    assert substring["total"] == 36
    assert len(substring["items"]) == 30
    assert not any(row["account"] == "TARGET" for row in substring["items"])
    exact = search(client, cid, q=" TARGET ", exact=True)
    assert exact == {"items": [{"account": "TARGET", "name": "Exact synthetic owner", "is_main": True, "source": "manual"}],
                     "total": 1, "page": 1, "page_size": 30}
    assert search(client, cid, q="target", exact=True)["total"] == 0
    assert search(client, other, q="TARGET", exact=True)["items"] == [
        {"account": "TARGET", "name": "Other case synthetic owner", "is_main": False, "source": None}]


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 0}, {"page_size": 101}])
def test_candidate_pagination_rejects_invalid_bounds(client, make_case, params):
    assert client.get("/api/main-accounts/candidates", params={"case_id": make_case(), **params}).status_code == 422


def test_main_account_reads_reject_missing_case(client):
    assert client.get("/api/main-accounts/candidates", params={"case_id": 99999}).status_code == 404
    assert client.get("/api/main-accounts", params={"case_id": 99999}).status_code == 404


def test_manual_add_requires_existing_case_account_and_uses_known_name(client, database, make_case, flow_row):
    cid, other = make_case("Manual owner"), make_case("Other manual owner")
    insert_flow(database, cid, flow_row, payer_account="000-OWN", payer_name="Known synthetic owner")
    insert_flow(database, other, flow_row, payer_account="000-OTHER")
    for account, status in [("", 400), ("000-UNKNOWN", 404), ("000-OTHER", 404)]:
        response = client.post("/api/main-accounts", json={"case_id": cid, "account": account})
        assert response.status_code == status, response.text
    for _ in range(2):
        response = client.post("/api/main-accounts", json={"case_id": cid, "account": " 000-OWN "})
        assert response.json() == {"ok": True, "account": "000-OWN", "name": "Known synthetic owner", "source": "manual"}
    assert len(marks(client, cid)) == 1


def test_convert_auto_to_manual_preserves_name_and_cancellation_is_exact_account(client, database, make_case, flow_row):
    cid, other = make_case("Exact cancellation"), make_case("Other exact cancellation")
    shared_name = "Same synthetic owner"
    for account in ("000-A", "000-B"):
        insert_flow(database, cid, flow_row, payer_account=account, payer_name=shared_name)
        database.x("insert into main_accounts(case_id,account,name,source) values (?,?,?,'auto')", [cid, account, shared_name])
    database.x("insert into main_accounts(case_id,account,name,source) values (?,'000-A',?,'manual')", [other, shared_name])
    assert client.post("/api/main-accounts", json={"case_id": cid, "account": "000-A"}).json()["name"] == shared_name
    assert marks(client, cid)["000-A"]["source"] == "manual"
    response = client.request("DELETE", "/api/main-accounts", json={"case_id": cid, "account": "000-A", "name": shared_name})
    assert response.json() == {"ok": True}
    assert set(marks(client, cid)) == {"000-B"}
    assert set(marks(client, other)) == {"000-A"}
    assert database.q("select count(*) n from flows where case_id=?", [cid])[0]["n"] == 2
    assert client.request("DELETE", "/api/main-accounts", json={"case_id": cid, "account": ""}).status_code == 400


def test_case_owned_declaration_remains_convertible_when_ledger_is_absent(client, database, make_case):
    cid, other = make_case("Declared owner"), make_case("Other declaration")
    database.x("insert into main_accounts(case_id,account,name,source) values (?,'000-DECLARED','Synthetic declared owner','auto')", [cid])
    database.x("insert into main_accounts(case_id,account,name,source) values (?,'000-OTHER','Other synthetic owner','auto')", [other])
    response = client.post("/api/main-accounts", json={"case_id": cid, "account": "000-DECLARED"})
    assert response.json() == {"ok": True, "account": "000-DECLARED", "name": "Synthetic declared owner", "source": "manual"}
    assert client.post("/api/main-accounts", json={"case_id": cid, "account": "000-OTHER"}).status_code == 404


def test_auto_recognition_reports_all_unique_declared_accounts_and_preserves_manual(client, database, make_case, flow_row):
    cid, other = make_case("Recognition"), make_case("Other recognition")
    bid = batch(database, cid)
    for account in ("000-A", "000-B", "000-C"):
        insert_flow(database, cid, flow_row, batch_id=bid, payer_account=account,
                    payer_name=f"Synthetic {account}", self_account=account, self_name=f"Synthetic {account}")
    repeat = batch(database, cid, filename="synthetic-repeat.csv")
    insert_flow(database, cid, flow_row, batch_id=repeat, payer_account="000-A", self_account="000-A")
    unrelated = batch(database, cid, mode="A")
    insert_flow(database, cid, flow_row, batch_id=unrelated, payer_account="000-A-MODE", self_account="000-A-MODE")
    # Even malformed cross-case references do not become evidence in this case.
    insert_flow(database, other, flow_row, batch_id=bid, payer_account="000-OTHER", self_account="000-OTHER")
    assert client.post("/api/main-accounts", json={"case_id": cid, "account": "000-A", "name": "Retained human name"}).status_code == 200
    before = marks(client, cid)["000-A"]
    database.x("insert into main_accounts(case_id,account,name,source) values (?,'000-B','Existing automatic name','auto')", [cid])
    response = client.post("/api/main-accounts/auto", json={"case_id": cid})
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert {row["account"] for row in receipt["detected"]} == {"000-A", "000-B", "000-C"}
    assert receipt["batches_scanned"] == 2
    assert (receipt["added_count"], receipt["manual_preserved"], receipt["existing_auto_count"]) == (1, 1, 1)
    after = marks(client, cid)
    assert after["000-A"] == before
    repeated = client.post("/api/main-accounts/auto", json={"case_id": cid}).json()
    assert (repeated["added_count"], repeated["manual_preserved"], repeated["existing_auto_count"]) == (0, 1, 2)
    cleared = client.post("/api/main-accounts/clear-auto", json={"case_id": cid}).json()
    assert cleared == {"cleared": 2}
    assert marks(client, cid) == {"000-A": before}
    assert client.post("/api/main-accounts/clear-auto", json={"case_id": cid}).json() == {"cleared": 0}


def test_automatic_fallback_requires_unambiguous_top_account(client, database, make_case, flow_row):
    cid = make_case()
    tied = batch(database, cid)
    insert_flow(database, cid, flow_row, batch_id=tied, self_account="", self_name="")
    assert client.post("/api/main-accounts/auto", json={"case_id": cid}).json()["detected"] == []
    clear = batch(database, cid)
    for recipient in ("000-COUNTER-A", "000-COUNTER-B"):
        insert_flow(database, cid, flow_row, batch_id=clear, payer_account="000-OWNER", payee_account=recipient,
                    self_account="", self_name="")
    assert [row["account"] for row in client.post("/api/main-accounts/auto", json={"case_id": cid}).json()["detected"]] == ["000-OWNER"]


def test_fallback_does_not_count_self_transfer_twice_toward_ninety_percent(client, database, make_case, flow_row):
    cid = make_case()
    bid = batch(database, cid)
    for i in range(10):
        insert_flow(database, cid, flow_row, batch_id=bid, self_account="", self_name="",
                    payer_account="000-SELF" if i < 5 else f"000-PAYER-{i}",
                    payee_account="000-SELF" if i < 5 else f"000-PAYEE-{i}")
    assert client.post("/api/main-accounts/auto", json={"case_id": cid}).json()["detected"] == []
    assert marks(client, cid) == {}


def test_auto_recognition_is_atomic_if_later_account_write_fails(client, database, make_case, flow_row, monkeypatch):
    cid = make_case()
    bid = batch(database, cid)
    for account in ("000-A", "000-B"):
        insert_flow(database, cid, flow_row, batch_id=bid, self_account=account)
    original = main.mark_automatic
    calls = []
    def fail_later(*args):
        result = original(*args)
        calls.append(result["account"])
        if len(calls) == 2:
            raise RuntimeError("Synthetic write failure")
        return result
    monkeypatch.setattr(main, "mark_automatic", fail_later)
    with pytest.raises(RuntimeError, match="Synthetic write failure"):
        client.post("/api/main-accounts/auto", json={"case_id": cid})
    assert len(calls) == 2
    assert marks(client, cid) == {}


def test_import_does_not_replace_manual_source_name_or_marking_time(client, database, make_case, flow_row):
    cid = make_case()
    insert_flow(database, cid, flow_row, payer_account="000-SELF", payer_name="Synthetic prior owner")
    assert client.post("/api/main-accounts", json={"case_id": cid, "account": "000-SELF", "name": "Synthetic retained name"}).status_code == 200
    before = marks(client, cid)["000-SELF"]
    content = ("交易时间,本方户名,本方账号,对方账号,收支标志,金额\n"
               "2026-02-01 10:00:00,Automatic imported name,000-SELF,000-COUNTER,支出,0.01\n").encode()
    preview = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("synthetic.csv", io.BytesIO(content))})
    assert preview.status_code == 200, preview.text
    preview = preview.json()
    base = f"/api/v1/cases/{cid}/imports/{preview['import_id']}"
    assert client.post(base + "/commit", json={"mode": preview["mode"], "mapping": preview["mapping"]}).status_code == 200
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = client.get(base).json()
        if job["stage"] in ("done", "error"):
            break
        time.sleep(.01)
    assert job["stage"] == "done", job
    assert job["result"]["main_detected"] == [{"account": "000-SELF", "name": "Synthetic retained name"}]
    assert marks(client, cid)["000-SELF"] == before
    assert client.post("/api/main-accounts/clear-auto", json={"case_id": cid}).json() == {"cleared": 0}
