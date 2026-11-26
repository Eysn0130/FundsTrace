"""Physical review for readable originals whose roles or layout are still undecided."""
import hashlib
import io
import json

from openpyxl import Workbook
import pytest

from app import table_source_roles
from app.services import imports
from test_api_v1 import poll
from test_automatic_table_import import preview
from test_cases import archive_of, exported, import_package, members, rewrite_parquet
from test_feedback_import_api import complete


UNKNOWN = ("发生时点,已调单账户,对方账号,交易金额,收付标志,交易币种,证件号\n"
           "2026-01-01 10:00:00,SYN-A,SYN-B,100.25,出,CNY,SYN-A\n"
           "2026-01-01 10:05:00,SYN-A,SYN-C,22.50,入,CNY,SYN-A\n").encode()
FIELDS = {"trade_time": 1, "self_account": 2, "counter_account": 3, "amount": 4, "direction": 5, "currency": 6}


def setup_review(client, case, raw=UNKNOWN, name="synthetic.csv"):
    view = preview(client, case, raw, name)
    url = f"/api/v1/cases/{case}/imports/{view['import_id']}/layout-review"
    return view, url, {"revision": view["layout_review"]["revision"]}


def region(member="table.csv", header=1, last=3, fields=FIELDS):
    return {"member_path": member, "header_row": header, "last_row": last, "mode": "B", "mapping": fields}


@pytest.mark.parametrize(("account_heading", "second_heading", "clock", "positions"), [
    ("对方账号", "备注2", "交易时间", [20]),
    ("对方账号", "对手账号", "交易时间", [20, 21]),
    ("对方账卡号", "对方账号(查询条件)", "交易时间", []),
    ("对方账号", "备注2", "说明日期", []),
])
def test_trial_locates_declared_counter_fields_without_admitting_financial_rows(
        client, make_case, monkeypatch, account_heading, second_heading, clock, positions):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    heading = [clock, "收付标志", "交易币种", "对方卡号",
               *[f"其他字段{i}" for i in range(5, 20)], account_heading, second_heading]
    cells = ["2040-01-01 08:32:00", "出", "CNY", "000123"] + [""] * 15 + ["000918", ""]
    raw = ("\n".join(",".join(row) for row in (heading, cells)) + "\n").encode()
    view, url, revision = setup_review(client, case, raw)
    assert view["layout_model_trial"] and not view["layout_applied"]
    request = {**revision, "member_path": "table.csv", "header_row": 1,
               "field": "counter_account", "prepare_layout": True}
    for first in (1, 17):
        outcome = client.post(url + "/model-trial", json={**request, "column_start": first}).raise_for_status().json()
        assert outcome["state"] == "pending"
        assert outcome["first_calls"] == outcome["recheck_calls"] == 0
        assert outcome["prepared_preview"] is None
        assert outcome["packet"]["facts"]["sha256"] == hashlib.sha256(raw).hexdigest()
        if positions:
            assert outcome["header_role"] == {
                "scope": "source_header_only", "field": "counter_account", "member_path": "table.csv",
                "header_row": 1, "width": 21,
                "matches": [{"column": index, "heading": heading[index - 1]} for index in positions],
            }
        else:
            assert "header_role" not in outcome
    if positions:
        card = client.post(url + "/model-trial", json={**request, "field": "counter_card"}).raise_for_status().json()
        assert card["header_role"]["matches"] == [{"column": 4, "heading": "对方卡号"}]
    assert client.post(url + "/model-trial", json={
        **request, "revision": "0" * len(revision["revision"]),
    }).status_code == 409
    assert client.get(f"/api/v1/cases/{case}/flows").raise_for_status().json()["total"] == 0


@pytest.mark.parametrize(("value", "absence"), [("NULL", True), ("0", False)])
def test_intake_reports_compound_source_columns_without_treating_zero_as_blank(client, make_case, monkeypatch, value, absence):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    raw = ("交易时间,交易金额,对方账号及户名\n"
           f"2040-01-01 08:00:00,10.00,{value}\n"
           f"2040-01-01 08:01:00,10.00,{value}\n").encode()
    view = preview(client, case, raw, "compound.csv")
    path = f"/api/v1/cases/{case}/imports/{view['import_id']}/intake"
    payload = client.get(path).raise_for_status().json()
    [finding] = payload["source_field_notes"]
    assert finding["header_row"] == 1 and finding["columns"] == [3]
    assert finding["rows"] == [2, 3]
    assert ("无法提取" in finding["text"]) == absence
    if not absence:
        assert "2行非空" in finding["text"] and "2行原值为零" in finding["text"]
    assert client.get(payload["items"][0]["original_url"]).content == raw
    assert client.get(f"/api/v1/cases/{case}/flows").raise_for_status().json()["total"] == 0


@pytest.mark.parametrize("header_only", [False, True])
def test_opening_and_balance_notes_follow_a_retained_unknown_source(
        client, database, make_case, monkeypatch, header_only):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case()
    header = "账户名称,账号,币种,余额,可用余额,开户日期,销户日期,上次交易日\n"
    data = "" if header_only else "Synthetic owner,000000123,CNY,0,0,2040-08-03,,2041-08-03\n"
    raw = (header + data).encode()
    view = preview(client, cid, raw)
    assert view["layout_review"]["members"][0]["header_rows"] == []
    base = f"/api/v1/cases/{cid}"
    source = base + f"/imports/{view['import_id']}/intake"
    first = client.get(source).raise_for_status().json()
    facts = first["source_field_notes"]
    assert [note["columns"] for note in facts] == [[6, 7, 8], [4, 5]]
    assert all(note["header_row"] == 1 and note["rows"] == ([] if header_only else [2]) for note in facts)
    assert "开户、销户和账户历史交易日期" in facts[0]["text"] and "000000123" not in facts[0]["text"]
    assert ("原值为零" in facts[1]["text"]) != header_only
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 0
    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    assert client.get(source).raise_for_status().json()["source_field_notes"] == facts
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0
    assert client.delete(base + f"/sources/{view['import_id']}/operation",
                         params={"expected_batch_id": job["result"]["batch_id"]}).status_code == 200
    assert "source_field_notes" not in client.get(source).raise_for_status().json()


@pytest.mark.parametrize("memo", ["交易后余额", "对手交易后可用余额"])
@pytest.mark.parametrize("swapped", [False, True])
def test_distinct_posting_balance_roles_follow_an_unrecognized_original_without_financial_admission(
        client, database, make_case, monkeypatch, memo, swapped):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Separate source balance changes")
    headings, values = ("交易后余额", "交易后可用余额"), ("350.00", "330.00")
    if swapped:
        headings, values = headings[::-1], values[::-1]
    raw = (f"材料说明,{','.join(headings)}\n"
           f"{memo},{','.join(values)}\n"
           "Synthetic note,420.00,420.00\n").encode()
    view = preview(client, cid, raw)
    assert view["layout_review"]["members"][0]["header_rows"] == []
    assert view["stats"]["accepted"] == 0
    base = f"/api/v1/cases/{cid}"
    source = view["import_id"]
    card = base + f"/imports/{source}"
    ready = client.get(card + "/intake").raise_for_status().json()
    [note] = ready["source_field_notes"]
    assert {name: note[name] for name in ("member_path", "header_row", "columns", "rows")} == {
        "member_path": "table.csv", "header_row": 1, "columns": [2, 3], "rows": [2, 3],
    }
    assert all(f"第 {number} 列「{heading}」" in note["text"] for number, heading in enumerate(headings, 2))
    assert "其中1行" in note["text"] and "不可互换或互补" in note["text"]
    assert not any(value in note["text"] for value in ("350.00", "330.00", "420.00"))
    other = make_case("Different source context")
    assert client.get(f"/api/v1/cases/{other}/imports/{source}/intake").status_code == 404
    assert client.get(card + "/intake").raise_for_status().json()["source_field_notes"] == [note]

    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 0
    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    assert client.get(card + "/intake").raise_for_status().json()["source_field_notes"] == [note]
    rows = client.get(card + "/review", params={"member_path": "table.csv"}).raise_for_status().json()["items"]
    assert {r["source_row"]: [c["value"] for c in r["original"] if c["column"] in (2, 3)]
            for r in rows if r["source_row"] in (2, 3)} == {2: list(values), 3: ["420.00", "420.00"]}
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0
    assert client.delete(base + f"/sources/{source}/operation",
                         params={"expected_batch_id": job["result"]["batch_id"]}).status_code == 200
    assert "source_field_notes" not in client.get(card + "/intake").raise_for_status().json()


@pytest.mark.parametrize("raw", [
    ("材料说明,交易后余额,本方交易后余额,交易后可用余额\n"
     "Synthetic note,350.00,360.00,330.00\n").encode(),
    ("材料说明,交易后余额,对手交易后可用余额\n"
     "Synthetic note,350.00,330.00\n").encode(),
    ("材料说明,交易后余额,交易后可用余额\n"
     "Synthetic note,350.00,330.00\n"
     "另一表区,交易后可用余额,交易后余额\n"
     "Synthetic other,450.00,420.00\n").encode(),
])
def test_posting_balance_notes_withhold_ambiguous_roles_and_repeated_unknown_headers(client, make_case, monkeypatch, raw):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Synthetic ambiguous balance scope")
    view = preview(client, cid, raw)
    base = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    assert "source_field_notes" not in client.get(base + "/intake").raise_for_status().json()
    assert view["stats"]["accepted"] == 0


@pytest.mark.parametrize("peer", ["对手", "对方"])
def test_later_peer_balance_header_does_not_extend_unregistered_source_balance_scope(
        client, make_case, monkeypatch, peer):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Synthetic second source balance area")
    raw = ("材料说明,交易后余额,交易后可用余额\n"
           "First balance area,142.07,93.21\n"
           f"材料说明,{peer}交易后余额,{peer}交易后可用余额\n"
           "Second balance area,17.45,17.45\n").encode()
    view = preview(client, cid, raw)
    assert view["layout_review"]["members"][0]["header_rows"] == []
    assert view["stats"]["accepted"] == 0
    base = f"/api/v1/cases/{cid}"
    card = base + f"/imports/{view['import_id']}"
    ready = client.get(card + "/intake").raise_for_status().json()
    assert "source_field_notes" not in ready

    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 0
    assert "source_field_notes" not in client.get(card + "/intake").raise_for_status().json()
    rows = client.get(card + "/review", params={"member_path": "table.csv"}).raise_for_status().json()["items"]
    assert {r["source_row"]: [c["value"] for c in r["original"] if c["column"] in (2, 3)]
            for r in rows if r["source_row"] in (2, 3, 4)} == {
        2: ["142.07", "93.21"],
        3: [f"{peer}交易后余额", f"{peer}交易后可用余额"],
        4: ["17.45", "17.45"],
    }
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


def test_transaction_order_and_teller_number_stay_on_their_original_columns_without_admitting_rows(
        client, database, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Synthetic distinct number purposes")
    raw = ("交易时间,本方账号,交易金额,币种,交易序号,柜员流水号,备注\n"
           "2040-04-11 08:01:00,00090001,19.34,CNY,000068,000817,交易序号\n"
           "2040-04-11 08:11:00,00090001,39.54,CNY,000057,000057,凭证号\n").encode()
    view = preview(client, cid, raw)
    assert view["layout_review"]["members"][0]["header_rows"] == [1]
    assert view["mapping"]["trade_no"] == "交易序号" and "柜员流水号" not in view["mapping"].values()
    assert view["table_summary"]["reason_counts"] == {"missing_direction": 2}
    base = f"/api/v1/cases/{cid}"
    card = base + f"/imports/{view['import_id']}"
    [note] = client.get(card + "/intake").raise_for_status().json()["source_field_notes"]
    assert (note["member_path"], note["header_row"], note["columns"], note["rows"]) == (
        "table.csv", 1, [5, 6], [2, 3])
    assert "第 5 列「交易序号」" in note["text"] and "第 6 列「柜员流水号」" in note["text"]
    assert "不可互换" in note["text"] and "同笔" in note["text"]
    assert "000817" not in note["text"]
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 0
    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    assert client.get(card + "/intake").raise_for_status().json()["source_field_notes"] == [note]
    page = client.get(card + "/review", params={"member_path": "table.csv"}).raise_for_status().json()
    assert {r["source_row"]: [c["value"] for c in r["original"] if c["column"] in (5, 6)]
            for r in page["items"]} == {2: ["000068", "000817"], 3: ["000057", "000057"]}
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


@pytest.mark.parametrize("raw", [
    ("材料说明,交易序号,柜员流水号\n"
     "Synthetic note,100,97\n"),
    ("交易时间,本方账号,交易金额,币种,交易序号,柜员流水号,柜员流水号\n"
     "2040-04-11 08:01:00,00090001,19.34,CNY,000068,000817,000931\n"),
    ("交易时间,本方账号,交易金额,币种,交易序号,柜员流水号\n"
     "2040-04-11 08:01:00,00090001,19.34,CNY,000068,000817\n"
     "第二组编号,,,,柜员流水号,交易序号\n"
     "2040-04-11 08:11:00,00090001,39.54,CNY,000057,000063\n"),
])
def test_number_role_notes_require_unique_columns_and_one_registered_scope(client, make_case, monkeypatch, raw):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case()
    view = preview(client, cid, raw.encode())
    card = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    body = client.get(card + "/intake").raise_for_status().json()
    assert "source_field_notes" not in body and view["stats"]["accepted"] == 0


@pytest.mark.parametrize("swapped", [False, True])
def test_counterparty_account_and_card_roles_keep_independent_original_cells(
        client, database, make_case, monkeypatch, swapped):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Synthetic separate counterparty identifiers")
    last_headers = ["对手账号", "对手卡号"]
    first = ["00000196", "00000196"]
    second = ["00000488", ""]
    if swapped:
        last_headers.reverse()
        first.reverse()
        second.reverse()
    book = Workbook()
    book.active.append(["交易时间", "本方账号", "交易金额", "收付标志", "交易币种", "备注", *last_headers])
    memo = "本方卡号" if swapped else "对手卡号"
    book.active.append(["2040-04-11 08:01:00", "00090001", "19.34", "出", "CNY", memo, *first])
    book.active.append(["2040-04-11 08:11:00", "00090001", "39.54", "出", "CNY", "", *second])
    content = io.BytesIO()
    book.save(content)
    raw = content.getvalue()
    view = preview(client, cid, raw, "peer-roles.xlsx")
    assert view["layout_review"]["members"][0]["header_rows"] == [1]
    assert view["stats"]["accepted"] == 2
    base = f"/api/v1/cases/{cid}"
    source = view["import_id"]
    card = base + f"/imports/{source}"
    [note] = client.get(card + "/intake").raise_for_status().json()["source_field_notes"]
    assert (note["member_path"], note["header_row"], note["columns"], note["rows"]) == (
        "worksheets/0001", 1, [7, 8], [2, 3])
    assert all(f"第 {col} 列「{name}」" in note["text"] for col, name in enumerate(last_headers, 7))
    assert "1行两个原始字符串相同" in note["text"]
    assert "不可互换" in note["text"] and "不足以证明卡账绑定" in note["text"]
    assert "00000196" not in note["text"] and "00000488" not in note["text"]

    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 2
    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    assert client.get(card + "/intake").raise_for_status().json()["source_field_notes"] == [note]
    page = client.get(card + "/review", params={"member_path": "worksheets/0001"}).raise_for_status().json()
    cells = {row["source_row"]: [c["value"] for c in row["original"] if c["column"] in (7, 8)]
             for row in page["items"]}
    assert cells == {2: first, 3: second}
    observations = client.get(base + "/flows", params={"granularity": "observations"}).raise_for_status().json()
    assert observations["total"] == 2 and observations["funds"]["confirmed_event_count"] == 0
    assert client.get(base + f"/sources/{source}/file").content == raw
    assert client.delete(base + f"/sources/{source}/operation",
                         params={"expected_batch_id": job["result"]["batch_id"]}).status_code == 200
    assert "source_field_notes" not in client.get(card + "/intake").raise_for_status().json()


@pytest.mark.parametrize(("extra_headers", "extra_values", "repeated"), [
    (["对手账号", "本方卡号"], ["00000196", "00000012"], False),
    (["对手账号", "对手卡号", "交易对方卡号"], ["00000196", "00000196", "00000733"], False),
    (["对手账号", "对手卡号", "对方账户号"], ["00000196", "00000196", "00000344"], False),
    (["对手账号", "对手卡号"], ["00000196", "00000196"], True),
])
def test_counterparty_role_notes_require_unique_roles_in_one_registered_scope(
        client, make_case, monkeypatch, extra_headers, extra_values, repeated):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Synthetic competing counterparty roles")
    book = Workbook()
    book.active.append(["交易时间", "本方账号", "交易金额", "收付标志", "交易币种", "备注", *extra_headers])
    book.active.append(["2040-04-11 08:01:00", "00090001", "19.34", "出", "CNY", "", *extra_values])
    if repeated:
        book.active.append(["另一个表区", "", "", "", "", "", "对方卡号", "对方账户号"])
        book.active.append(["2040-04-11 08:11:00", "00090002", "39.54", "出", "CNY", "", "00000883", "00000488"])
    content = io.BytesIO()
    book.save(content)
    view = preview(client, cid, content.getvalue(), "ambiguous-peer-roles.xlsx")
    route = f"/api/v1/cases/{cid}/imports/{view['import_id']}/intake"
    notes = client.get(route).raise_for_status().json().get("source_field_notes", [])
    assert not any("卡账绑定" in note["text"] for note in notes)


@pytest.mark.parametrize(("first_role_headers", "later_role_headers"), [
    (("交易序号", "柜员流水号"), ("续表", "", "", "", "交易流水号", "凭证号", "")),
    (("交易序号", "柜员流水号"), ("续表", "", "", "交易流水号", "", "", "凭证编号")),
    (("交易序号", "柜员流水号"), ("续表", "", "", "", "交易序号", "凭证号", "")),
    (("对手账号", "对手卡号"), ("续表", "", "", "", "本方账号", "本方卡号", "")),
    (("对手账号", "对手卡号"), ("续表", "", "", "交易账号", "", "", "交易卡号")),
    (("对手账号", "对手卡号"), ("续表", "", "", "", "对手账号", "本方卡号", "")),
])
def test_later_different_roles_do_not_extend_the_first_source_note(
        client, make_case, monkeypatch, first_role_headers, later_role_headers):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Synthetic second source role header")
    rows = [
        ("交易时间", "本方账号", "交易金额", "币种", *first_role_headers, "备注"),
        ("2040-04-11 08:01:00", "00090001", "19.34", "CNY", "00000196", "00000196", "Synthetic first"),
        later_role_headers,
        ("2040-04-12 08:11:00", "00090722", "39.54", "CNY", "00000375", "00000375", "Synthetic second"),
    ]
    raw = ("\n".join(",".join(row) for row in rows) + "\n").encode()
    view = preview(client, cid, raw)
    assert view["layout_review"]["members"][0]["header_rows"] == [1]
    assert view["stats"]["accepted"] == 0
    base = f"/api/v1/cases/{cid}"
    card = base + f"/imports/{view['import_id']}"
    ready = client.get(card + "/intake").raise_for_status().json()
    assert "source_field_notes" not in ready

    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 0
    assert "source_field_notes" not in client.get(card + "/intake").raise_for_status().json()
    review = client.get(card + "/review", params={"member_path": "table.csv"}).raise_for_status().json()
    assert {row["source_row"]: tuple(c["value"] for c in row["original"]) for row in review["items"]} == {
        index: row for index, row in enumerate(rows[1:], 2)}
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


def test_wide_original_card_and_account_labels_keep_separate_coordinates(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case()
    physical = {4: "交易卡号", 5: "交易账号", 9: "交易时间", 10: "交易金额", 12: "借贷标志",
                14: "对手账/卡号", 15: "对手卡号", 16: "对手账号", 30: "币种", 43: "交易流水号", 44: "凭证号"}
    cells = {4: "000000678", 5: "000000123", 9: "2040-08-03 08:10:11", 10: "8.34",
             12: "待释义", 14: "000000456", 16: "000000456", 30: "CNY"}
    book = Workbook()
    book.active.append([physical.get(i, f"原字段{i}") for i in range(1, 46)])
    book.active.append([cells.get(i, "") for i in range(1, 45)])
    output = io.BytesIO()
    book.save(output)
    raw = output.getvalue()
    view = preview(client, cid, raw, "wide.xlsx")
    base = f"/api/v1/cases/{cid}"
    route = base + f"/imports/{view['import_id']}/intake"
    facts = client.get(route).raise_for_status().json()["source_field_notes"]
    by_columns = {tuple(n["columns"]): n for n in facts}
    assert set(by_columns) == {(4, 5), (14, 15, 16), (43, 44)}
    assert all(note["member_path"] == "worksheets/0001" and note["rows"] == [2] for note in facts)
    peer = by_columns[14, 15, 16]["text"]
    assert "第 15 列「对手卡号」0行非空、1行空白" in peer
    assert "1行合列与账号列字面相同" in peer and "000000456" not in peer
    original = imports._path(view["import_id"], ".xlsx")
    original.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    assert "source_field_notes" not in client.get(route).raise_for_status().json()


def test_mixed_peer_columns_read_original_text_shapes_without_assigning_parties(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case()
    raw = ("交易时间,本方账号,对方账号姓名,对方账号卡号,交易金额,借贷标志,币种,摘要,交易备注\n"
           "2040-08-03 08:01:00,SYN-BOOK,合成人甲,AB 000123,8.34,待释义,CNY,合成摘要,合成备注\n").encode()
    view = preview(client, cid, raw)
    base = f"/api/v1/cases/{cid}"
    route = base + f"/imports/{view['import_id']}/intake"
    notes = client.get(route).raise_for_status().json()["source_field_notes"]
    found = {tuple(n["columns"]): n for n in notes}
    assert found[3, 4]["rows"] == [2]
    assert "1行有汉字且无数字" in found[3, 4]["text"]
    assert "1行含字母和数字并见空白分段" in found[3, 4]["text"]
    assert "1行两栏原文均有值且不同" in found[8, 9]["text"]
    assert "000123" not in str(notes) and "合成人甲" not in str(notes)
    assert view["stats"]["accepted"] == 0


def test_trial_opening_clock_is_refused_in_dry_run_and_confirmed_physical_mapping(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case()
    raw = ("开户日期,本方账号,对方账号,交易金额,收付标志,交易币种,备注\n"
           "2040-05-01,000123,000234,10.01,出,CNY,Synthetic note\n"
           "2040-05-02,000123,000235,10.02,入,CNY,Synthetic note\n").encode()
    view, path, revision = setup_review(client, cid, raw)
    assert view["stats"]["accepted"] == 0
    selected = {**revision, "regions": [region()]}
    trial = client.post(path + "/dry-run", json=selected)
    assert (trial.status_code, trial.json().get("error", {}).get("code")) == (422, "invalid_layout_mapping")
    apply = client.post(path + "/apply", json={**selected, "confirmed": True})
    assert (apply.status_code, apply.json().get("error", {}).get("code")) == (400, "invalid_table_file")
    assert client.get(f"/api/v1/cases/{cid}/flows").raise_for_status().json()["total"] == 0


def test_transaction_date_and_transaction_clock_remain_a_valid_physical_mapping(client, make_case, monkeypatch):
    from test_import_trial_rules_first import commit_body

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case()
    raw = ("交易日期,本方账号,对方账号,交易金额,收付标志,交易币种,交易时分秒,开户日期\n"
           "2040-05-04,000123,000234,10.01,出,CNY,08:10:11,2040-05-01\n"
           "2040-05-05,000123,000235,10.02,入,CNY,09:10:12,2040-05-02\n").encode()
    before, path, revision = setup_review(client, cid, raw)
    assert before["stats"]["accepted"] == 2
    selected = {**revision, "regions": [region(fields={**FIELDS, "time_part": 7})]}
    dry = client.post(path + "/dry-run", json=selected).raise_for_status().json()
    assert [row["disposition"] for row in dry["regions"][0]["rows"]] == ["accepted", "accepted"]
    prepared = client.post(path + "/apply", json={**selected, "confirmed": True}).raise_for_status().json()
    result = client.post(path.removesuffix("/layout-review") + "/commit", json=commit_body(prepared))
    assert result.status_code == 200
    completed = poll(client, cid, prepared["import_id"])
    assert completed["stage"] == "done" and completed["result"]["ok"] == 2
    flows = client.get(f"/api/v1/cases/{cid}/flows").raise_for_status().json()["items"]
    assert {row["trade_time"] for row in flows} == {"2040-05-04 08:10:11", "2040-05-05 09:10:12"}


@pytest.mark.parametrize("heading", ["可用余额", "对手余额"])
def test_trial_physical_layout_cannot_adopt_a_non_book_balance(client, make_case, monkeypatch, heading):
    from test_import_trial_rules_first import commit_body

    raw = (f"交易时间,本方账号,对方账号,交易金额,收付标志,交易币种,{heading}\n"
           "2040-08-03 10:01:00,SYN-BOOK,SYN-PEER,8.34,出,CNY,93.25\n"
           "2040-08-03 10:02:00,SYN-BOOK,SYN-PEER,12.75,入,CNY,106.00\n").encode()
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    initial, url, revision = setup_review(client, case, raw)
    denied = client.post(url + "/apply", json={
        **revision, "confirmed": True, "regions": [region(fields={**FIELDS, "balance": 7})],
    })
    assert denied.status_code == 400 and denied.json()["error"]["code"] == "invalid_table_file"

    correct = client.post(url + "/apply", json={**revision, "confirmed": True, "regions": [region()]})
    assert correct.status_code == 200, correct.text
    view = correct.json()
    committed = client.post(url.removesuffix("/layout-review") + "/commit", json=commit_body(view))
    assert committed.status_code == 200, committed.text
    finished = poll(client, case, initial["import_id"])
    assert finished["stage"] == "done" and finished["result"]["ok"] == 2
    flows = client.get(f"/api/v1/cases/{case}/flows?granularity=observations").json()
    assert flows["total"] == 2 and all(row["balance"] is None for row in flows["items"])
    assert client.get(f"/api/v1/cases/{case}/sources/{initial['import_id']}/file").content == raw


@pytest.mark.parametrize("scenario", ["proved", "without_note", "dual_nonzero", "dual_with_unknown_currency"])
def test_trial_inline_account_proof_runs_existing_split_amount_validation(
        client, database, make_case, monkeypatch, scenario):
    from test_import_trial_rules_first import commit_body

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic proved split-ledger layout")
    note = "字段口径：甲账户是逐笔交易本方账号；乙账户是归集备用本方账号。\n" if scenario != "without_note" else ""
    conflict = ""
    if scenario in {"dual_nonzero", "dual_with_unknown_currency"}:
        currency = "CNY" if scenario == "dual_nonzero" else "未知币种"
        conflict = f"2040-05-15 09:18:16,000SYN-A,000SYN-X,000SYN-E,3.70,1.00,{currency}\n"
    raw = (note + "交易时间,甲账户,乙账户,对方账号,借方发生额,贷方发生额,交易币种\n"
           "2040-05-15 09:14:23,000SYN-A,000SYN-A,000SYN-C,0,24.75,CNY\n"
           "2040-05-15 09:16:17,000SYN-A,000SYN-X,000SYN-D,11.20,0,CNY\n" + conflict).encode()
    view, url, revision = setup_review(client, case, raw)
    base = f"/api/v1/cases/{case}"
    if scenario in {"without_note", "dual_with_unknown_currency"}:
        assert view["layout_model_trial"] and not view["layout_applied"] and view["table_summary"]["accepted"] == 0
        assert client.get(base + "/flows").json()["total"] == 0
        if scenario == "dual_with_unknown_currency":
            response = client.post(url + "/dry-run", json={**revision, "regions": [
                region(header=2, last=5, fields={"self_account": 2}),
            ]})
            assert response.status_code == 200, response.text
            last = response.json()["regions"][0]["rows"][-1]
            assert last["disposition"] == "unresolved" and "retained_split_conflict" not in last
        return

    assert view["automatic"] and view["layout_applied"] and not view["layout_model_trial"]
    [decision] = view["layout_rule_evidence"]["regions"]
    assert decision["column"] == 2 and decision["note"] == {"member_path": "table.csv", "source_row": 1}
    job_url = url.removesuffix("/layout-review")
    if scenario == "dual_nonzero":
        assert view["table_summary"]["reason_counts"] == {
            "preamble": 1, "transaction_valid": 2, "conflicting_income_expense": 1,
        }
        with database._lock:
            database._con.close()
            database._con = None
        reopened = client.post(job_url + "/preview", json={
            "mode": view["mode"], "mapping": view["mapping"], "ready_revision": revision["revision"],
        })
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["table_summary"]["reason_counts"] == view["table_summary"]["reason_counts"]
        assert not reopened.json()["layout_model_trial"]
        view = reopened.json()
    confirmed = client.post(job_url + "/commit", json=commit_body(view))
    assert confirmed.status_code == 200, confirmed.text
    finished = poll(client, case, view["import_id"])
    assert finished["stage"] == "done" and finished["result"]["ok"] == 2
    assert finished["result"]["layout_decision"]["origin"] == "rule"
    with database._lock:
        database._con.close()
        database._con = None
    flows = client.get(base + "/flows").json()["items"]
    assert {(row["payer_account"], row["payee_account"], row["amount"]) for row in flows} == {
        ("000SYN-C", "000SYN-A", "24.75"), ("000SYN-A", "000SYN-D", "11.20")}
    assert client.get(base + f"/sources/{view['import_id']}/file").content == raw
    if scenario == "dual_nonzero":
        [pending] = client.get(job_url + "/records", params={"source_row": 5}).json()["items"]
        assert (pending["disposition"], pending["reason_code"], pending["flow_id"]) == (
            "unresolved", "conflicting_income_expense", None)
        assert json.loads(pending["details"]["confirmed_physical_layout"])["policy"] == "physical-table-layout-v2"
        assert pending["details"]["validation_issues"] == "conflicting_income_expense,missing_amount,missing_direction"
        restored = import_package(client, exported(client, case))
        assert restored.status_code == 200, restored.text
        restored_case = restored.json()["case_id"]
        [restored_source] = database.q_raw("select source_id from import_sources where case_id=?", [restored_case])
        [saved] = client.get(f"/api/v1/cases/{restored_case}/imports/{restored_source['source_id']}/records",
                             params={"source_row": 5}).json()["items"]
        assert saved["details"] == pending["details"] and saved["flow_id"] is None


@pytest.mark.parametrize("scenario", ["data_row_address", "same_header_conflict", "unknown_address", "wrong_only"])
def test_trial_scoped_workbook_retains_misaddressed_note_without_promoting_counterevidence(
        client, database, make_case, monkeypatch, tmp_path, scenario):
    from test_import_trial_rules_first import commit_body

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic independent field descriptions")
    book = Workbook()
    flows = book.active
    flows.title = "流水"
    flows.append(["交易时间", "证件号", "甲账户", "乙账户", "对方账号", "交易金额", "收付标志", "交易币种"])
    for index, direction in enumerate(("进", "出", "进"), 1):
        flows.append([f"2040-04-22 12:34:5{index}", "SYN-ID", "000SYN-QUERY", "000SYN-OWN", f"SYN-PEER-{index}",
                      "1.75", direction, "CNY"])
    notes = book.create_sheet("字段说明")
    notes.append(["字段", "字段说明"])
    if scenario != "wrong_only":
        notes.append(["流水!C1 甲账户", "归集备用本方账号"])
        notes.append(["流水!D1 乙账户", "逐笔交易本方账号"])
    edits = {"data_row_address": ("流水!D2 乙账户", "逐笔交易本方账号"),
             "same_header_conflict": ("流水!D1 乙账户", "归集备用本方账号"),
             "unknown_address": ("另一本账!D2 乙账户", "逐笔交易本方账号"),
             "wrong_only": ("流水!D2 乙账户", "逐笔交易本方账号")}
    notes.append(list(edits[scenario]))
    archive = io.BytesIO()
    book.save(archive)
    raw = archive.getvalue()
    view, url, revision = setup_review(client, case, raw, "synthetic.xlsx")
    base = f"/api/v1/cases/{case}"
    if scenario != "data_row_address":
        assert view["table_summary"]["accepted"] == 0 and not view["layout_applied"]
        expected_reason = {"same_header_conflict": "source_role_conflict", "wrong_only": "field_note_wrong_header_row"}.get(scenario)
        if expected_reason:
            reply = client.post(url + "/model-trial", json={
                **revision, "member_path": "worksheets/0001", "header_row": 1, "field": "self_account",
                "support_member_path": "worksheets/0002", "prepare_layout": True,
            }).raise_for_status().json()
            assert reply["state"] == "pending" and reply["reason"] == expected_reason
            assert reply["first_calls"] == reply["recheck_calls"] == 0
        assert client.get(base + "/flows").json()["total"] == 0
        return
    assert view["automatic"] and view["layout_applied"] and not view["layout_model_trial"]
    assert view["table_summary"]["reason_counts"] == {
        "transaction_valid": 3, "auxiliary_table": 2, "field_note_wrong_header_row": 1,
    }
    [basis] = view["layout_rule_evidence"]["regions"]
    assert basis["column"] == 4 and basis["note"] == {"member_path": "worksheets/0002", "source_row": 3}
    assert client.post(url + "/apply", json={**revision, "confirmed": True, "policy": "physical-table-layout-v2",
                      "regions": [region("worksheets/0001", last=4, fields={"self_account": 4})]}).status_code == 422
    job_url = url.removesuffix("/layout-review")
    saved = client.post(job_url + "/commit", json=commit_body(view))
    assert saved.status_code == 200, saved.text
    finished = poll(client, case, view["import_id"])
    assert finished["stage"] == "done" and finished["result"]["ok"] == 3
    with database._lock:
        database._con.close()
        database._con = None
    assert client.get(base + "/flows").json()["total"] == 3
    params = {"member_path": "worksheets/0002", "source_row": 4}
    [pending] = client.get(job_url + "/records", params=params).json()["items"]
    assert (pending["source_row"], pending["reason_code"], pending["disposition"], pending["flow_id"]) == (
        4, "field_note_wrong_header_row", "unresolved", None)
    assert json.loads(pending["details"]["confirmed_field_note_scope"]) == {
        "policy": "physical-table-layout-v2", "member_path": "worksheets/0001",
        "header_row": 1, "target_row": 2, "column": 4,
    }
    original = client.get(job_url + "/review", params={"member_path": "worksheets/0002", "view": "review"}).json()
    assert original["items"][0]["original"][0]["value"] == "流水!D2 乙账户"
    source_url = base + f"/sources/{view['import_id']}"
    assert client.get(source_url + "/file").content == raw
    packed = exported(client, case)
    restored = import_package(client, packed)
    assert restored.status_code == 200, restored.text
    restored_case = restored.json()["case_id"]
    [restored_source] = database.q_raw("select source_id from import_sources where case_id=?", [restored_case])
    [restored_record] = client.get(f"/api/v1/cases/{restored_case}/imports/{restored_source['source_id']}/records",
                                    params=params).json()["items"]
    assert restored_record["details"] == pending["details"] and restored_record["flow_id"] is None

    replaced = pending["details"]["confirmed_field_note_scope"].replace('"target_row":2', '"target_row":20')
    details = json.dumps({**pending["details"], "confirmed_field_note_scope": replaced}, ensure_ascii=False)
    tampered = members(packed)
    rewrite_parquet(tampered, "import_records",
                    f"update rows set details_json='{details}' where reason_code='field_note_wrong_header_row'", tmp_path)
    assert import_package(client, archive_of(tampered)).status_code == 400
    removed = client.delete(source_url + "/operation", params={"expected_batch_id": finished["result"]["batch_id"]})
    assert removed.status_code == 200, removed.text
    assert client.get(base + "/flows").json()["total"] == 0
    assert client.get(job_url + "/records", params=params).json()["items"][0]["reason_code"] == "field_note_wrong_header_row"


@pytest.mark.parametrize("scenario", ["proved", "wrong_scope", "missing_definition", "contradictory_definition",
                                     "repeated_definition", "unknown_definition", "unrelated_wrong_scope", "missing_competitor",
                                     "contradictory_binding", "conflicting_preamble"])
def test_trial_requires_scoped_use_code_and_definition_for_each_competing_account(
        client, database, make_case, monkeypatch, scenario):
    from test_import_trial_rules_first import commit_body

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    book = Workbook()
    ledger = book.active
    ledger.title = "流水"
    header = 1
    if scenario == "conflicting_preamble":
        ledger.append(["字段口径：甲账号是逐笔交易本方账号；乙账号是归集备用本方账号。"])
        header = 2
    last = header + 3
    headings = ["交易时间", "对方账号", "甲账号", "乙账号", "收付标志", "交易金额", "交易币种"]
    ledger.append(headings)
    ledger.append(["2040-05-15 09:00:00", "SYN-PEER-1", "000SYN-A", "000SYN-A", "出", "10.25", "CNY"])
    ledger.append(["2040-05-15 09:01:00", "SYN-PEER-2", "000SYN-A", "000SYN-B", "入", "11.50", "CNY"])
    ledger.append(["2040-05-15 09:02:00", "SYN-PEER-3", "000SYN-A", "000SYN-C", "出", "1.05", "CNY"])
    notes = book.create_sheet("字段说明")
    area = f"流水!A{header}:G{last}"
    note_rows = [table_source_roles.SCOPED_FIELD_NOTE_HEADER]
    if scenario != "missing_competitor":
        note_rows.extend([(area, "甲账号", "甲账号使用用途码 A"),
                          (area, "用途码 A", "用途码 A 表示归集备用账号，不记录本表逐笔交易本方账号")])
    note_rows.append((area, "乙账号", "乙账号使用用途码 B"))
    if scenario != "missing_definition":
        scope = "流水!A1:G1" if scenario == "wrong_scope" else area
        definition = "用途码 B 有其他说明" if scenario == "unknown_definition" else "用途码 B 表示本表逐笔交易的本方账号"
        note_rows.append((scope, "用途码 B", definition))
    if scenario == "contradictory_definition":
        note_rows.append((area, "用途码 B", "用途码 B 表示归档备查账号，不记录本表逐笔交易本方账号"))
    if scenario == "repeated_definition":
        note_rows.append(note_rows[-1])
    if scenario == "unrelated_wrong_scope":
        note_rows.append(("流水!A1:G1", "用途码 C", "用途码 C 表示本表逐笔交易的本方账号"))
    if scenario == "contradictory_binding":
        note_rows.append((area, "乙账号", "乙账号使用用途码 A"))
    for row in note_rows:
        notes.append(row)
    archive = io.BytesIO()
    book.save(archive)
    raw = archive.getvalue()

    view, url, revision = setup_review(client, case, raw, "scoped-use-codes.xlsx")
    base = f"/api/v1/cases/{case}"
    job_url = url.removesuffix("/layout-review")
    ledger_member, note_member = "worksheets/0001", "worksheets/0002"
    source = dict(enumerate(note_rows, 1))
    scope = dict(sheet="流水", header_row=header, last_row=last, headings=headings, columns=(3, 4))
    proof = table_source_roles.verify_account_use_codes(source, **scope, citation=(4, 4, 5))
    if scenario != "proved":
        assert proof is None
        expected = {
            "wrong_scope": ("use_code_wrong_scope", [5]),
            "missing_definition": ("use_code_missing_definition", [4]),
            "contradictory_definition": ("use_code_conflicting_definitions", [5, 6]),
        }.get(scenario)
        issue = None
        if expected:
            reason, lines = expected
            issue = {"reason": reason, "code": "B", "member_path": ledger_member, "header_row": 1, "last_row": 4,
                     "notes": [{"member_path": note_member, "source_row": line} for line in lines]}
            assert view["layout_review_issue"] == issue
        else:
            assert "layout_review_issue" not in view
        assert view["layout_model_trial"] and not view["layout_applied"] and view["table_summary"]["accepted"] == 0
        pending = client.post(url + "/model-trial", json={
            **revision, "member_path": ledger_member, "header_row": header, "field": "self_account",
            "support_member_path": note_member, "prepare_layout": True,
        }).raise_for_status().json()
        assert pending["state"] == "pending" and pending["prepared_preview"] is None
        assert pending["reason"] == (issue["reason"] if issue else "insufficient_evidence")
        assert pending["first_calls"] == pending["recheck_calls"] == 0
        assert [row["locator"]["source_row"] for row in pending["packet"]["records"]
                if row["locator"]["member_path"] == note_member] == list(source)
        blocked = client.post(url + "/apply", json={**revision, "confirmed": True, "regions": [
            region(ledger_member, header=header, last=last, fields={"self_account": 4}),
        ]})
        assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "trial_source_role_conflict"
        if issue:
            again = client.post(job_url + "/preview", json={
                "mode": view["mode"], "mapping": view["mapping"], "ready_revision": revision["revision"],
            }).raise_for_status().json()
            assert again["layout_review_issue"] == issue
        assert client.get(base + "/flows").json()["total"] == 0
        return

    assert proof is not None
    assert "layout_review_issue" not in view
    for wrong in ((3, 4, 5), (4, 3, 5), (4, 4, 3)):
        assert table_source_roles.verify_account_use_codes(source, **scope, citation=wrong) is None
    evidence = view["layout_rule_evidence"]
    assert evidence == {"source_sha256": hashlib.sha256(raw).hexdigest(), "regions": [{
        "member_path": ledger_member, "header_row": 1, "last_row": 4, "column": 4, "basis": "source_role_statement",
        "note": {"member_path": note_member, "source_row": 4},
        "definition": {"member_path": note_member, "source_row": 5},
    }]}
    assert view["automatic"] and view["layout_applied"] and not view["layout_model_trial"]
    rejected = client.post(url + "/apply", json={**revision, "confirmed": True, "regions": [
        region(ledger_member, last=4, fields={"self_account": 3}),
    ]})
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "trial_source_role_conflict"
    same_scope = client.post(url + "/apply", json={**revision, "confirmed": True, "regions": [
        region(ledger_member, last=4, fields={"self_account": 4}),
    ]}).raise_for_status().json()
    assert same_scope["layout_applied"]
    revision = same_scope["layout_review"]["revision"]
    with database._lock:
        database._con.close()
        database._con = None
    view = client.post(job_url + "/preview", json={
        "mode": view["mode"], "mapping": view["mapping"], "ready_revision": revision,
    }).raise_for_status().json()
    assert not view["layout_model_trial"] and view["table_summary"]["accepted"] == 3
    ready = commit_body(view)
    stale = client.post(job_url + "/commit", json={**ready, "ready_revision": revision})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "ready_evidence_stale"
    saved = imports._path(view["import_id"], ".xlsx")
    saved.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    try:
        changed = client.post(job_url + "/commit", json=ready)
        assert changed.status_code == 409 and changed.json()["error"]["code"] == "source_changed"
        assert client.get(base + "/flows").json()["total"] == 0
    finally:
        saved.write_bytes(raw)
    assert client.post(job_url + "/commit", json=ready).status_code == 200
    finished = poll(client, case, view["import_id"])
    assert finished["stage"] == "done" and finished["result"]["layout_decision"] == {
        "origin": "rule", "packet_sha256": None, **evidence,
    }
    with database._lock:
        database._con.close()
        database._con = None
    flows = client.get(base + "/flows?granularity=observations").json()["items"]
    assert {(row["payer_account"], row["payee_account"], row["amount"]) for row in flows} == {
        ("000SYN-A", "SYN-PEER-1", "10.25"), ("SYN-PEER-2", "000SYN-B", "11.50"),
        ("000SYN-C", "SYN-PEER-3", "1.05"),
    }
    assert client.get(job_url).json()["result"]["layout_decision"] == finished["result"]["layout_decision"]
    source_url = base + f"/sources/{view['import_id']}"
    assert client.get(source_url + "/file").content == raw
    auxiliary = client.get(job_url + "/records", params={"member_path": note_member}).json()["items"]
    assert {(row["source_row"], row["disposition"], row["flow_id"]) for row in auxiliary} == {
        (number, "auxiliary", None) for number in range(2, 6)
    }
    restored = import_package(client, exported(client, case)).raise_for_status().json()["case_id"]
    [restored_source] = database.q_raw("select source_id from import_sources where case_id=?", [restored])
    restored_url = f"/api/v1/cases/{restored}/imports/{restored_source['source_id']}"
    restored_records = client.get(restored_url + "/records", params={"member_path": note_member}).json()["items"]
    assert [row["details"] for row in restored_records] == [row["details"] for row in auxiliary]
    removed = client.delete(source_url + "/operation", params={"expected_batch_id": finished["result"]["batch_id"]})
    assert removed.status_code == 200
    assert client.get(base + "/flows").json()["total"] == 0
    assert client.get(job_url).json()["result"]["layout_decision"] == finished["result"]["layout_decision"]
    monkeypatch.setattr(client.app.state, "desktop_model_trial", None)
    default = preview(client, make_case("Production default"), raw, "scoped-use-codes.xlsx")
    assert default["table_summary"]["accepted"] == 0 and not default["layout_applied"]


def test_unknown_headers_get_original_coordinates_and_only_a_read_only_trial(client, database, make_case):
    case, other = make_case("导入试算"), make_case("另一案件")
    view, url, revision = setup_review(client, case)
    assert view["table_summary"]["accepted"] == 0 and view["ready_evidence"] is None
    [member] = view["layout_review"]["members"]
    assert member == {"member_path": "table.csv", "sheet": "", "source_rows": 3, "header_rows": []}
    before = database.q_raw("select * from import_sessions where import_id=?", [view["import_id"]])

    response = client.post(url, json={**revision, "member_path": "table.csv"})
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store", response.text
    layout = response.json()
    assert layout["sha256"] == hashlib.sha256(UNKNOWN).hexdigest()
    assert [c["value"] for c in layout["rows"][0]["cells"][:3]] == ["发生时点", "已调单账户", "对方账号"]
    assert [r["source_row"] for r in layout["rows"]] == [1, 2, 3]
    assert client.post(url, json={**revision, "member_path": "table.csv", "row_start": 4}).status_code == 422
    assert client.post(url.replace(f"/cases/{case}/", f"/cases/{other}/"),
                       json={**revision, "member_path": "table.csv"}).status_code == 404

    endpoint = url + "/dry-run"
    res = client.post(endpoint, json={**revision, "regions": [region()]})
    assert res.status_code == 200 and res.headers["Cache-Control"] == "no-store", res.text
    out = res.json()
    assert out["review_status"] == "review_only" and out["uncovered_present_rows"] == 0
    [first, second] = out["regions"][0]["rows"]
    assert first["source_row"] == 2 and first["disposition"] == "accepted"
    assert (first["preview"]["payer_account"], first["preview"]["payee_account"], first["preview"]["amount"]) == (
        "SYN-A", "SYN-B", "100.25")
    assert (second["preview"]["payer_account"], second["preview"]["payee_account"], second["preview"]["amount"]) == (
        "SYN-C", "SYN-A", "22.50")
    assert client.post(endpoint, json={**revision, "regions": [region(last=2)]}).json()["uncovered_present_rows"] == 1
    for regions in ([region(), region()], [region(last=4)], [region(fields={**FIELDS, "self_account": 7})],
                    [region(fields={**FIELDS, "self_account": 30})], [region("outside.csv")]):
        assert client.post(endpoint, json={**revision, "regions": regions}).status_code == 422
    prove = client.post(url.replace("layout-review", "ready-evidence") + "/verify", json={
        **revision, "field": "self_account", "claims": [{"member_path": "table.csv", "header_row": 1, "column": 2}],
    })
    assert prove.status_code == 409
    assert database.q_raw("select * from import_sessions where import_id=?", [view["import_id"]]) == before
    assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 0


def test_trial_maps_each_sheet_and_repeated_header_independently(client, make_case):
    case = make_case()
    book = Workbook()
    first = book.active
    second = book.create_sheet("第二账簿")
    normal = ["发生时点", "已调单账户", "对方账号", "交易金额", "收付标志", "交易币种"]
    reordered = [normal[0], normal[2], normal[1], *normal[3:]]
    first.append(normal)
    first.append(["2026-01-01 10:00:00", "SYN-X", "SYN-Y", "56.25", "出", "CNY"])
    first.append(reordered)
    first.append(["2026-01-01 10:03:00", "SYN-Z", "SYN-X", "8.66", "出", "CNY"])
    second.append(reordered)
    second.append(["2026-01-01 10:08:00", "SYN-W", "SYN-Y", "15.00", "出", "CNY"])
    out = io.BytesIO()
    book.save(out)
    view, url, revision = setup_review(client, case, out.getvalue(), "synthetic.xlsx")
    assert len(view["layout_review"]["members"]) == 2
    swapped = {**FIELDS, "self_account": 3, "counter_account": 2}
    response = client.post(url + "/dry-run", json={**revision, "regions": [
        region("worksheets/0001", 1, 2), region("worksheets/0001", 3, 4, swapped), region("worksheets/0002", 1, 2, swapped),
    ]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["uncovered_present_rows"] == 0
    flows = [entry["rows"][0]["preview"] for entry in result["regions"]]
    assert [(flow["source_member"], flow["source_row"], flow["payer_account"], flow["payee_account"]) for flow in flows] == [
        ("worksheets/0001", 2, "SYN-X", "SYN-Y"), ("worksheets/0001", 4, "SYN-X", "SYN-Z"),
        ("worksheets/0002", 2, "SYN-Y", "SYN-W"),
    ]


def test_layout_rejects_stale_and_changed_originals(client, make_case):
    case = make_case()
    view, url, revision = setup_review(client, case)
    body = {**revision, "member_path": "table.csv"}
    changed = UNKNOWN.replace(b"100.25", b"100.26")
    imports._path(view["import_id"], ".csv").write_bytes(changed)
    assert client.post(url, json=body).json()["error"]["code"] == "source_changed"
    assert client.post(url + "/dry-run", json={**revision, "regions": [region()]}).json()["error"]["code"] == "source_changed"
    assert client.post(url, json={**body, "revision": "0" * 32}).json()["error"]["code"] == "ready_evidence_stale"


def test_unknown_csv_header_after_single_cell_source_note(client, make_case):
    case = make_case()
    raw = "来源说明：发生时点为交易发生时间\n".encode() + UNKNOWN
    _, url, revision = setup_review(client, case, raw)
    page = client.post(url, json={**revision, "member_path": "table.csv"}).json()
    assert len(page["rows"][0]["cells"]) == 1
    assert page["rows"][1]["cells"][1]["value"] == "已调单账户"
    res = client.post(url + "/dry-run", json={**revision, "regions": [region(header=2, last=4)]})
    assert res.status_code == 200, res.text
    assert res.json()["uncovered_present_rows"] == 1
    assert [r["disposition"] for r in res.json()["regions"][0]["rows"]] == ["accepted", "accepted"]


def test_whole_csv_structure_takes_precedence_over_separators_in_descriptions(client, make_case):
    case = make_case()
    raw = b"\n".join(row + end for row, end in zip(
        UNKNOWN.splitlines(), (",备注".encode(), b",POS|ONE", b",POS|TWO"), strict=True)) + b"\n"
    _, url, revision = setup_review(client, case, raw)
    resp = client.post(url, json={**revision, "member_path": "table.csv"})
    assert resp.status_code == 200 and resp.json()["rows"][1]["cells"][-1]["value"] == "POS|ONE"
    trial = client.post(url + "/dry-run", json={**revision, "regions": [region()]})
    assert trial.status_code == 200, trial.text
    assert all(row["disposition"] == "accepted" for row in trial.json()["regions"][0]["rows"])


def test_layout_does_not_guess_between_two_partial_csv_structures(client, make_case):
    case = make_case()
    raw = "来源说明\n".encode() + b"\n".join(row + end for row, end in zip(
        UNKNOWN.splitlines(), (",备注|说明".encode(), b",POS|ONE", b",POS|TWO"), strict=True)) + b"\n"
    _, url, revision = setup_review(client, case, raw)
    resp = client.post(url, json={**revision, "member_path": "table.csv"})
    trial = client.post(url + "/dry-run", json={**revision, "regions": [region(header=2, last=4)]})
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "ready_evidence_unreadable"
    assert trial.status_code == 422 and trial.json()["error"]["code"] == "invalid_layout_mapping"


def test_large_auxiliary_sheet_counts_before_trial_reopens_workbook(client, make_case, monkeypatch):
    from app import table_import, table_source_roles

    case = make_case()
    book = Workbook()
    for row in UNKNOWN.decode().splitlines():
        book.active.append(row.split(","))
    auxiliary = book.create_sheet("账户信息")
    auxiliary.append(table_source_roles._ACCOUNT)
    for number in range(256):
        auxiliary.append([f"SYN-{number}"] + [""] * 14)
    stream = io.BytesIO()
    book.save(stream)
    view, url, revision = setup_review(client, case, stream.getvalue(), "synthetic.xlsx")
    assert [m["member_path"] for m in view["layout_review"]["members"]] == ["worksheets/0001"]
    assert view["table_summary"]["documents"][1]["source_rows"] > 256

    def reread(*args, **kwargs):
        raise AssertionError("trial should stop before materializing the workbook")

    monkeypatch.setattr(table_import, "_excel", reread)
    response = client.post(url + "/dry-run", json={**revision, "regions": [region("worksheets/0001")]})
    assert response.status_code == 422 and response.json()["error"]["code"] == "layout_trial_limit"


def test_trial_does_not_apply_verified_amount_unit_to_another_physical_region(client, make_case):
    case = make_case()
    raw = ("交易时间,本方账号,对方账号,交易金额,收付标志,备注,币种\n"
           "2026-01-01 10:11:00,SYN-A,SYN-B,20000,出,本表交易金额正数表示本方支出，绝对值为分；负数表示本方收入。,CNY\n"
           "发生时点,账户载体,对手编号,货值,收付描述,留白,计量币\n"
           "2026-01-02 10:00:00,SYN-Y,SYN-X,500,出,,CNY\n").encode()
    _, url, revision = setup_review(client, case, raw)
    map_unit = {**FIELDS, "currency": 7}
    response = client.post(url + "/dry-run", json={**revision, "regions": [
        region(last=2, fields=map_unit), region(header=3, last=4, fields=map_unit)]})
    assert response.status_code == 200, response.text
    first, second = (r["rows"][0] for r in response.json()["regions"])
    assert first["preview"]["amount"] == "200.00"
    assert second["preview"] is None and second["disposition"] == "unresolved"
    assert second["reason"] == "conflicting_direction"


def test_explicit_csv_layout_full_preview_and_commit_replays_same_original(client, database, make_case, tmp_path):
    case = make_case()
    raw = ("字段口径：发生时点表示交易时间，已调单账户表示本方账号\n".encode() + UNKNOWN)
    before, url, revision = setup_review(client, case, raw)
    assert before["table_summary"]["accepted"] == 0
    endpoint = url + "/apply"
    chosen = {**revision, "regions": [region(header=2, last=4)], "confirmed": True}
    assert client.post(endpoint, json={**chosen, "confirmed": False}).status_code == 422
    assert client.post(endpoint, json={**chosen, "regions": [region(header=2, last=3)]}).status_code == 400
    assert client.post(endpoint, json={**chosen, "regions": [region(header=2, last=4,
                        fields={**FIELDS, "self_account": 7})]}).status_code == 400
    response = client.post(endpoint, json=chosen)
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["layout_applied"] and view["table_summary"]["accepted"] == 2
    assert view["table_summary"]["reason_counts"] == {"preamble": 1, "transaction_valid": 2}
    assert [row["source_row"] for row in view["preview"]] == [3, 4]
    assert next(item for item in view["table_summary"]["mapping_evidence"]
                if item["field"] == "self_account")["basis"] == "manual_override"
    assert client.post(endpoint, json=chosen).status_code == 409
    base = url.removesuffix("/layout-review")
    commit = {"mode": view["mode"], "mapping": view["mapping"]}
    assert client.post(base + "/commit", json=commit).status_code == 409
    assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 0
    bound = {**commit, "ready_revision": view["layout_review"]["revision"]}
    refresh = client.post(base + "/preview", json=bound)
    assert refresh.status_code == 200 and refresh.json()["preview"] == view["preview"]
    assert client.post(base + "/commit", json=bound).status_code == 409
    current = refresh.json()
    response = client.post(base + "/commit", json={**bound, "ready_revision": current["layout_review"]["revision"]})
    assert response.status_code == 200 and response.json()["started"], response.text
    finished = complete(client, case, current)
    assert finished["stage"] == "done", finished
    assert finished["result"]["ok"] == 2 and finished["result"]["table_summary"]["accepted"] == 2
    response = client.get(f"/api/v1/cases/{case}/sources/{current['import_id']}/file")
    assert response.content == raw
    actual = database.q_raw("select payer_account,payee_account,amount from flows where case_id=? order by trade_time", [case])
    assert [(row["payer_account"], row["payee_account"], str(row["amount"])) for row in actual] == [
        ("SYN-A", "SYN-B", "100.25"), ("SYN-C", "SYN-A", "22.50")]
    original = exported(client, case)
    for change, where in (('{"mapping":{"self_account":7}}', "source_row=4"), ('{"last_row":9}', "source_row>2")):
        rewritten = members(original)
        rewrite_parquet(rewritten, "import_records", f"""update rows set details_json=json_merge_patch(
            details_json, json_object('confirmed_physical_layout', cast(json_merge_patch(
                json_extract_string(details_json,'$.confirmed_physical_layout'), '{change}') as varchar)))
            where {where}""", tmp_path)
        rejected = import_package(client, archive_of(rewritten))
        assert rejected.status_code == 400 and "物理映射" in rejected.json()["detail"]


def test_xlsx_apply_keeps_physical_regions_and_persists_mapping_after_package_restore(client, database, make_case):
    import json

    case = make_case()
    book = Workbook()
    first = book.active
    first.title = "交易甲"
    second = book.create_sheet("交易乙")
    notes = book.create_sheet("字段说明")
    normal = ["发生时点", "已调单账户", "对方账号", "交易金额", "收付标志", "交易币种"]
    reordered = [normal[0], normal[2], normal[1], *normal[3:]]
    first.append(normal)
    first.append(["2026-01-01 10:00:00", "SYN-X", "SYN-Y", "56.25", "出", "CNY"])
    first.append(reordered)
    first.append(["2026-01-01 10:03:00", "SYN-Z", "SYN-X", "8.66", "出", "CNY"])
    second.append(reordered)
    second.append(["2026-01-01 10:08:00", "SYN-W", "SYN-Y", "15.00", "出", "CNY"])
    notes.append(["字段", "字段说明"])
    notes.append(["交易甲!B1 已调单账户", "本表的合成账号。"])
    stream = io.BytesIO()
    book.save(stream)
    _, url, revision = setup_review(client, case, stream.getvalue(), "synthetic.xlsx")
    swapped = {**FIELDS, "self_account": 3, "counter_account": 2}
    sections = [region("worksheets/0001", 1, 2), region("worksheets/0001", 3, 4, swapped),
                region("worksheets/0002", 1, 2, swapped)]
    assert client.post(url + "/apply", json={**revision, "regions": sections[:2], "confirmed": True}).status_code == 400
    applied = client.post(url + "/apply", json={**revision, "regions": sections, "confirmed": True})
    assert applied.status_code == 200, applied.text
    view = applied.json()
    assert [m["role"] for m in view["table_summary"]["members"]] == ["table", "table", "auxiliary"]
    assert view["table_summary"]["accepted"] == 3
    start = client.post(url.removesuffix("/layout-review") + "/commit", json={
        "ready_revision": view["layout_review"]["revision"], "mode": view["mode"], "mapping": view["mapping"]})
    assert start.status_code == 200, start.text
    done = complete(client, case, view)
    assert done["stage"] == "done" and done["result"]["ok"] == 3, done

    package = exported(client, case)
    restored = import_package(client, package)
    assert restored.status_code == 200, restored.text
    loaded = restored.json()["case_id"]
    assert database.q_raw("select count(*) n from flows where case_id=?", [loaded]) == [{"n": 3}]
    details = database.q_raw("select details_json from import_records where case_id=? order by record_no", [loaded])
    layouts = [json.loads(r["details_json"]).get("confirmed_physical_layout") for r in details]
    basis = [json.loads(item) for item in layouts if item]
    assert [item["mapping"]["self_account"] for item in basis] == [2, 3, 3]
    assert [item["header_row"] for item in basis] == [1, 3, 1]


def test_confirmed_layout_checks_case_history_again_before_writing(client, database, make_case):
    case = make_case()
    raw = ("发生时点,载体甲,载体乙,对方账号,交易金额,收付标志,交易币种\n"
           "2026-01-01 10:00:00,SYN-A,SYN-Z,SYN-B,100.25,出,CNY\n").encode()
    jobs = []
    for owner in (2, 3):
        _, url, revision = setup_review(client, case, raw)
        fields = {**FIELDS, "self_account": owner, "counter_account": 4, "amount": 5, "direction": 6, "currency": 7}
        response = client.post(url + "/apply", json={**revision, "regions": [region(last=2, fields=fields)], "confirmed": True})
        assert response.status_code == 200, response.text
        jobs.append((url.removesuffix("/layout-review"), response.json()))
    for base, view in reversed(jobs):
        begun = client.post(base + "/commit", json={"mode": "B", "mapping": view["mapping"],
                            "ready_revision": view["layout_review"]["revision"]})
        assert begun.status_code == 200, begun.text
        finished = poll(client, case, view["import_id"])
        if view is jobs[0][1]:
            assert finished["stage"] == "error" and finished["error_code"] == "layout_preview_changed"
        else:
            assert finished["stage"] == "done" and finished["result"]["ok"] == 1, finished
    assert database.q_raw("select count(*) n from flows where case_id=?", [case]) == [{"n": 1}]


def test_explicit_confirmation_cannot_override_query_condition_annotation(client, make_case):
    raw = UNKNOWN.replace("已调单账户".encode(), "卡号（查询条件）".encode())
    _, url, revision = setup_review(client, make_case(), raw)
    resp = client.post(url + "/apply", json={**revision, "regions": [region()], "confirmed": True})
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "invalid_table_file"


@pytest.mark.parametrize("cell", ["#REF!", True])
def test_layout_holds_non_identity_cell_types_in_xlsx(client, make_case, cell):
    case = make_case()
    book = Workbook()
    for row in UNKNOWN.decode().splitlines():
        book.active.append(row.split(","))
    book.active["B2"] = cell
    archive = io.BytesIO()
    book.save(archive)
    _, url, revision = setup_review(client, case, archive.getvalue(), "synthetic.xlsx")
    payload = {**revision, "regions": [region("worksheets/0001")]}
    trial = client.post(url + "/dry-run", json=payload)
    assert trial.status_code == 200 and trial.json()["regions"][0]["rows"][0]["reason"] == "invalid_cell_type"
    assert client.post(url + "/apply", json={**payload, "confirmed": True}).status_code == 400


@pytest.mark.parametrize("header", ["交易时间", "发生时点"])
def test_confirmed_layout_cannot_omit_a_transaction_as_another_header(client, make_case, header):
    rows = ["2026-01-01 10:00:00,SYN-A,SYN-B,100.25,出,CNY",
            "2026-01-01 10:01:00,SYN-A,SYN-B,101.25,出,CNY",
            "2026-01-01 10:02:00,SYN-A,SYN-B,102.25,出,CNY"]
    raw = ("\n".join([f"{header},本方账号,对方账号,交易金额,收付标志,交易币种", *rows]) + "\n").encode()
    case = make_case()
    _, url, revision = setup_review(client, case, raw)
    bad = client.post(url + "/apply", json={**revision, "confirmed": True,
                       "regions": [region(header=1, last=2), region(header=3, last=4)]})
    assert bad.status_code == 400
    assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 0


def test_intake_reports_split_occurrences_and_two_balance_roles_from_original_cells(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    raw = ("本方账号,对方账号,交易时间,交易金额,收付标志,币种,借方发生额,贷方发生额,交易后余额,对手交易后余额\n"
           "SYN-OWN,SYN-PEER,2026-01-01 08:00:00,10.55,出,CNY,10.55,,100.12,0.00\n"
           "SYN-OWN,SYN-PEER,2026-01-01 08:10:00,10.55,进,CNY,0.00,10.55,110.67,95.00\n").encode()
    _, url, _ = setup_review(client, case, raw)
    result = client.get(url.removesuffix("/layout-review") + "/intake").raise_for_status().json()
    notes = {tuple(item["columns"]): item for item in result["source_field_notes"]}
    assert notes[(7, 8)]["rows"] == notes[(9, 10)]["rows"] == [2, 3]
    assert "1行原值为零" in notes[(7, 8)]["text"] and "1行空白" in notes[(7, 8)]["text"]
    assert "对手交易后余额" in notes[(9, 10)]["text"] and "1行原值为零" in notes[(9, 10)]["text"]
    assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 0


def test_completed_unknown_tabular_custody_retains_positions_and_absent_trailing_cells(client, database, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    matrix = [
        ("本人账号", "未判类字段", "存取", "发生额", "另记余额", "币种", "备注"),
        ("0000000123", "Synthetic row one", "存", "64.25", "100.00", "", ""),
        ("0000000123", "Synthetic row two", "取", "48.50", "98.00", "", ""),
        ("0000000123", "Synthetic row three", "取", "35.25", "85.00"),
    ]
    raw = ("\n".join("\t".join(row) for row in matrix) + "\n").encode()
    view, layout, revision = setup_review(client, case, raw)
    assert view["layout_review"]["members"][0]["header_rows"] == []
    assert view["table_summary"]["accepted"] == 0 and view["table_summary"]["reason_counts"] == {"unknown_table": 4}

    def values(rows, field):
        return [tuple(cell["value"] for cell in row[field]) for row in rows]

    before = client.post(layout, json={**revision, "member_path": "table.csv"}).raise_for_status().json()
    assert values(before["rows"], "cells") == matrix
    completed = complete(client, case, view)
    assert completed["stage"] == "done" and completed["result"]["ok"] == 0
    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    base = f"/api/v1/cases/{case}"
    after = client.get(base + f"/imports/{view['import_id']}/review", params={
        "member_path": "table.csv", "view": "all"}).raise_for_status().json()
    assert values(after["items"], "original") == matrix
    assert [row["source_row"] for row in after["items"]] == [1, 2, 3, 4]
    assert [len(row["original"]) for row in after["items"]] == [7, 7, 7, 5]
    assert all(row["disposition"] == "unresolved" and row["flow_id"] is None for row in after["items"])
    assert client.get(base + f"/sources/{view['import_id']}/file").content == raw
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0
