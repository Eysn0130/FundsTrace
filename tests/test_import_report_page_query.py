"""Report pagination shares selection while retaining full source support."""
import io
import zipfile

import pytest

from app import analysis_snapshot
from app.services import imports
from test_import_report import complete, payload, row


@pytest.fixture
def report_queries(database, monkeypatch):
    query = database.q_raw
    calls = []

    def observed(sql, params=None):
        result = query(sql, params)
        if sql.startswith("with _report_member_links as materialized"):
            calls.append(len(result))
        return result

    monkeypatch.setattr(database, "q_raw", observed)
    return calls


def page(client, base, endpoint, params, calls):
    calls.clear()
    response = client.get(base + "/report/" + endpoint, params=params)
    assert response.status_code == 200, response.text
    result = response.json()
    assert calls == [max(1, len(result["items"]))]
    return result


def archive(members):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as target:
        for member, rows in members:
            target.writestr(member, payload(rows))
    return stream.getvalue()


def test_account_option_count_and_pages_share_selection_and_keep_empty_page_total(client, make_case, report_queries):
    cid = make_case("Synthetic shared account pages")
    values = [row("SYN-" + own, "支出", "0.01", own=own, name="Synthetic MATCH")
              for own in ("000SYN-C", "000SYN-A", "000SYN-B")]
    values.append(row("SYN-USD", "支出", "0.02", own="000SYN-USD", currency="USD", name="Synthetic MATCH"))
    base, _ = complete(client, cid, archive([
        ("selected.csv", values),
        ("other.csv", [row("SYN-OUTSIDE", "支出", "0.03", own="000SYN-0", name="Synthetic MATCH")]),
    ]), "synthetic.zip")
    report = client.get(base + "/report", params={"member_path": "selected.csv"}).json()
    params = {"member_path": "selected.csv", "expected_revision": report["analysis_revision"],
              "currency": "CNY", "search": " match ", "page_size": 1}
    accounts = []
    for number, expected in enumerate(("000SYN-A", "000SYN-B", "000SYN-C"), 1):
        data = page(client, base, "accounts", {**params, "page": number}, report_queries)
        assert data["total"] == 3 and data["page"] == number and data["page_size"] == 1
        assert data["analysis_revision"] == report["analysis_revision"] and data["case_id"] == str(cid)
        item, = data["items"]
        assert item["account_number"] == expected and item["account_numbers"] == [expected]
        assert item["names"] == ["Synthetic MATCH"] and item["cards"] == []
        accounts.append(item)
    repeated = page(client, base, "accounts", {**params, "page": 2}, report_queries)
    assert repeated["items"] == [accounts[1]]
    empty = page(client, base, "accounts", {**params, "page": 99}, report_queries)
    assert empty["total"] == 3 and empty["items"] == [] and empty["page"] == 99
    selected = page(client, base, "accounts", {**params, "account": accounts[1]["account"]}, report_queries)
    assert selected["total"] == 1 and selected["items"] == [accounts[1]]
    missing = page(client, base, "accounts", {**params, "search": "SYN-NOT-PRESENT"}, report_queries)
    assert missing["total"] == 0 and missing["items"] == []


def test_record_pages_keep_duplicate_sources_exact_previews_and_all_filters(client, make_case, report_queries, monkeypatch):
    cid = make_case("Synthetic shared record pages")
    first = row("SYN-DUP", "支出", "9999999999999999.99", stamp="2026-02-01 00:00:00.123456", name="Synthetic MATCH")
    values = [first, list(first),
              row("SYN-LAST", "支出", "0.01", stamp="2026-02-28 23:59:59.999999", name="Synthetic MATCH"),
              row("SYN-END", "支出", "0.02", stamp="2026-03-01 00:00:00", name="Synthetic MATCH"),
              row("SYN-USD", "支出", "0.03", stamp="2026-02-01 00:00:00", currency="USD", name="Synthetic MATCH"),
              row("SYN-OTHER-OWN", "支出", "0.04", stamp="2026-02-01 00:00:00", own="000SYN-C", name="Synthetic MATCH"),
              row("SYN-UTC", "支出", "0.05", stamp="2026-02-01T00:00:00.654321+00:00", name="Synthetic MATCH")]
    raw = archive([
        ("selected.csv", values),
        ("other.csv", [row("SYN-OUTSIDE", "支出", "0.06", stamp="2026-02-01 00:00:00", name="Synthetic MATCH")]),
    ])
    complete(client, cid, raw, "synthetic-first.zip")
    base, _ = complete(client, cid, raw, "synthetic-copy.zip")
    report = client.get(base + "/report", params={"member_path": "selected.csv"}).json()
    own = next(item["account"] for item in report["accounts"]["items"] if item["account_number"] == "000SYN-A")
    relation = next(item for item in report["relations"]["items"] if item["account"] == own and item["counterparty_number"] == "000SYN-B")
    params = {"member_path": "selected.csv", "expected_revision": report["analysis_revision"],
              "currency": "CNY", "account": own, "counterparty": relation["counterparty"], "direction": "out",
              "timeline_scope": "selected", "timeline_accounts": [own], "timeline_search": " match ",
              "time_start": "2026-02-01 00:00:00", "time_end": "2026-03-01 00:00:00",
              "time_basis": "unqualified", "page_size": 2}
    details = imports._record_items
    detail_pages = []

    def bounded_details(*args, **options):
        detail_pages.append(options["record_nos"])
        assert len(options["record_nos"]) <= options["limit"] == 2
        return details(*args, **options)

    monkeypatch.setattr(imports, "_record_items", bounded_details)
    first_page = page(client, base, "records", params, report_queries)
    assert first_page["total"] == 3 and [item["source_row"] for item in first_page["items"]] == [2, 3]
    assert [item["disposition"] for item in first_page["items"]] == ["duplicate", "duplicate"]
    # Equal fields on different physical source rows remain distinct observations.
    assert len({item["flow_id"] for item in first_page["items"]}) == 2
    for item in first_page["items"]:
        assert isinstance(item["flow_id"], str) and item["member_path"] == "selected.csv"
        assert item["preview"]["amount"] == "9999999999999999.99"
        assert item["preview"]["trade_time"] == "2026-02-01 00:00:00.123456"
        assert item["preview"]["self_account"] == "000SYN-A"
    last_page = page(client, base, "records", {**params, "page": 2}, report_queries)
    assert last_page["total"] == 3 and [item["source_row"] for item in last_page["items"]] == [4]
    assert last_page["items"][0]["preview"]["amount"] == "0.01"
    empty = page(client, base, "records", {**params, "page": 99}, report_queries)
    assert empty["total"] == 3 and empty["items"] == [] and detail_pages[-1] == []
    utc = page(client, base, "records", {**params, "time_start": "2026-02-01T00:00:00Z",
        "time_end": "2026-03-01T00:00:00Z", "time_basis": "utc"}, report_queries)
    assert utc["total"] == 1 and utc["items"][0]["source_row"] == 8
    assert utc["items"][0]["preview"]["trade_time"] == "2026-02-01T00:00:00.654321+00:00"
    excluded = page(client, base, "records", {**params, "timeline_scope": "except"}, report_queries)
    assert excluded["total"] == 0 and excluded["items"] == []
    no_accounts = page(client, base, "records", {**params, "timeline_accounts": []}, report_queries)
    assert no_accounts["total"] == 0 and no_accounts["items"] == []


def test_record_pages_keep_full_confirmed_support_and_late_competing_evidence(client, make_case, report_queries):
    from test_event_import import csv_rows, transaction
    from test_folder_evidence import accepted

    cid = make_case("Synthetic shared event pages")
    values = [transaction(own, **{"收付标志": "支出" if own == "SYN-A" else "收入"}) for own in ("SYN-A", "SYN-B")]
    view, _ = accepted(client, cid, [("events.csv", csv_rows(values))], scope_bank=None)
    base = f'/api/v1/cases/{cid}/imports/{view["import_id"]}'
    before = client.get(base + "/report", params={"member_path": "events.csv"}).json()
    assert before["perspective"] == "own_ledger"
    own = next(item["account"] for item in before["accounts"]["items"] if item["account_number"] == "SYN-A")
    params = {"member_path": "events.csv", "expected_revision": before["analysis_revision"],
              "currency": "CNY", "account": own, "direction": "out", "page_size": 1}
    for number, expected_row in ((1, 2), (2, 3)):
        data = page(client, base, "records", {**params, "page": number}, report_queries)
        assert data["total"] == 2 and data["items"][0]["source_row"] == expected_row
        assert data["items"][0]["preview"]["amount"] == "100.00"
    empty = page(client, base, "records", {**params, "page": 3}, report_queries)
    assert empty["total"] == 2 and empty["items"] == []
    accepted(client, cid, [("conflict.csv", csv_rows([transaction("SYN-A", **{"交易金额": "101.00", "收付标志": "支出"})]))], scope_bank=None)
    assert client.get(base + "/report/records", params=params).status_code == 409
    after = client.get(base + "/report", params={"member_path": "events.csv"}).json()
    changed = page(client, base, "records", {**params, "expected_revision": after["analysis_revision"]}, report_queries)
    assert changed["total"] == 1 and changed["items"][0]["source_row"] == 2
    assert changed["items"][0]["details"]["current_event_state"] == "conflict"
    foreign = make_case("Synthetic foreign report pages")
    assert client.get(base.replace(f"/cases/{cid}/", f"/cases/{foreign}/") + "/report/records", params=params).status_code == 404


def test_empty_case_member_has_zero_total_without_a_page_item(client, database, make_case, report_queries):
    cid, source = make_case("Synthetic empty report pages"), "e" * 32
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,mapping_json,sheet,parser_version,row_kind)
        values (?,?,'synthetic.csv',?,0,'B','{}','','funds-trace-table-v4','table_record')""", [source, cid, "a" * 64])
    database.x("insert into import_members values (?,?,?,'table',?,0,'utf-8-sig',0)", [cid, source, "table.csv", "a" * 64])
    with database._lock:
        revision = analysis_snapshot.revision(cid)
    base = f"/api/v1/cases/{cid}/imports/{source}"
    for endpoint in ("accounts", "records"):
        params = {"member_path": "table.csv", "expected_revision": revision, "currency": "CNY", "page": 99}
        data = page(client, base, endpoint, params, report_queries)
        assert data["total"] == 0 and data["items"] == [] and data["analysis_revision"] == revision


def test_record_page_sentinel_preserves_zero_negative_and_maximum_record_keys(client, database, make_case, report_queries):
    cid, source = make_case("Synthetic signed report keys"), "f" * 32
    numbers = [-7, 0, 9223372036854775807]
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,mapping_json,sheet,parser_version,row_kind)
        values (?,?,'synthetic.csv',?,0,'A','{}','','funds-trace-table-v4','table_record')""", [source, cid, "a" * 64])
    database.x("insert into import_members values (?,?,?,'table',?,0,'utf-8-sig',3)", [cid, source, "table.csv", "a" * 64])
    for source_row, number in enumerate(numbers, 2):
        flow_id = source_row - 1 if number <= 0 else number
        database.x("""insert into flows(id,case_id,trade_time,payer_account,payee_account,amount,currency,balance)
            values (?,?,timestamp '2026-02-01 00:00:00.123456','000SYN-A','000SYN-B',0.01,'CNY',null)""", [flow_id, cid])
        database.x("insert into import_records values (?,?,?,'table.csv',?,'SYN-key','imported','transaction_valid','{}')",
                   [cid, source, number, source_row])
        database.x("insert into flow_sources values (?,?,?,?,'imported')", [cid, source, number, flow_id])
    with database._lock:
        revision = analysis_snapshot.revision(cid)
    base = f"/api/v1/cases/{cid}/imports/{source}"
    params = {"member_path": "table.csv", "expected_revision": revision, "currency": "CNY", "page_size": 1}
    for current, number in enumerate(numbers, 1):
        data = page(client, base, "records", {**params, "page": current}, report_queries)
        assert data["total"] == 3
        item, = data["items"]
        assert item["record_no"] == number and item["flow_id"] == str(current if number <= 0 else number)
        assert item["preview"]["amount"] == "0.01" and item["preview"]["balance"] is None
        assert item["preview"]["trade_time"] == "2026-02-01 00:00:00.123456"
    empty = page(client, base, "records", {**params, "page": 4}, report_queries)
    assert empty["total"] == 3 and empty["items"] == []
