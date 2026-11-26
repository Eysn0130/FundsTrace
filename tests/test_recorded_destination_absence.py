"""Exercise source-scoped destinations through the ordinary card."""
import csv
from io import BytesIO

import pytest
from openpyxl import Workbook

from test_api_v1 import poll


@pytest.mark.parametrize(("later", "proved", "with_timezone"), [
    ("2040-06-05 08:00:00,000101,000204,5.30,收,CNY", True, False),
    ("2040-06-05 08:00:00,000101,,5.30,付,CNY", False, False),
    ("2040-06-04 08:00:30,000101,,5.30,付,CNY", False, False),
    ("2040-06-04 08:00,000101,,5.30,付,CNY", False, False),
    ("2040-06-04 08:00:00,000101,,5.30,付,CNY", True, False),
    ("2040-06-05,000101,000204,5.30,收,CNY", False, False),
    ("2040-06-04 07:00:00,000101,,5.30,付,CNY,-08:00", False, True),
])
@pytest.mark.parametrize("carrier", ["csv", "xlsx"])
def test_original_destination_absence_is_bound_to_the_complete_source(
        client, make_case, monkeypatch, later, proved, with_timezone, carrier):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base = f"/api/v1/cases/{case}"
    rows = [
        "交易时间,本方账号,对方账号,交易金额,收付标志,币种",
        "2040-06-02 08:00:00,000101,000202,16.30,付,CNY",
        "2040-06-04 08:00:30,000101,000203,23.25,收,CNY",
    ]
    if with_timezone:
        rows = [rows[0] + ",交易时区", rows[1] + ",", rows[2] + ","]
    rows.append(later)
    if carrier == "xlsx":
        book = Workbook()
        sheet = book.active
        for cells in csv.reader(rows):
            sheet.append(cells)
        for number in range(2, sheet.max_row + 1):
            sheet.cell(number, 9, "")
        stream = BytesIO()
        book.save(stream)
        book.close()
        raw = stream.getvalue()
        member = "worksheets/0001"
    else:
        raw = ("\n".join(rows) + "\n").encode()
        member = "table.csv"
    ready = client.post(base + "/imports/preview", files={"file": (f"example.{carrier}", raw)}).raise_for_status().json()
    job = base + "/imports/" + ready["import_id"]
    submission = {"mode": ready["mode"], "mapping": ready["mapping"]}
    if ready.get("layout_review"):
        submission["ready_revision"] = ready["layout_review"]["revision"]
    client.post(job + "/commit", json=submission).raise_for_status()
    batch = poll(client, case, ready["import_id"])["result"]["batch_id"]
    [anchor] = client.get(job + "/records", params={
        "member_path": member, "source_row": 3,
    }).raise_for_status().json()["items"]
    request = {"case_id": case, "x": 1, "side": "payee", "review": True}
    card = client.get("/api/context/" + anchor["flow_id"], params=request).raise_for_status().json()
    actual = card["review"].get("recorded_destination_absence")
    if proved:
        assert actual == {"member_path": member, "first_row": 2, "last_row": 4, "matching_rows": 3,
                          "anchor_row": 3, "clock_column": 1}
        assert card["review"]["principal"]["target"]["status"] == "unknown"
    else:
        assert actual is None
    source = base + "/sources/" + ready["import_id"]
    assert client.delete(source + "/operation", params={"expected_batch_id": batch}).status_code == 200
    old = client.get(f"/api/context/{anchor['flow_id']}/record/{anchor['flow_id']}",
                     params={"case_id": case}, headers={"X-FundsTrace-Review": card["review"]["token"]})
    assert old.status_code == 409


@pytest.mark.parametrize(("day", "account", "currency", "proved"), [
    ("2040-06-05 08:00:00", "000101", "CNY", False),
    ("2040-06-04 08:00:30", "000101", "CNY", False),
    ("2040-06-04 08:00:00", "000101", "CNY", True),
    ("2040-06-05 08:00:00", "000102", "CNY", True),
    ("2040-06-05 08:00:00", "000101", "USD", True),
])
def test_workbook_absence_checks_unresolved_rows_in_the_matching_time_window(
        client, make_case, monkeypatch, day, account, currency, proved):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base = f"/api/v1/cases/{case}"
    book = Workbook()
    sheet = book.active
    sheet.append(["交易时间", "本方账号", "对方账号", "交易金额", "收付标志", "币种", "支出金额"])
    sheet.append(["2040-06-02 08:00:00", "000101", "000202", "16.30", "付", "CNY", "16.30"])
    sheet.append(["2040-06-04 08:00:30", "000101", "000203", "23.25", "收", "CNY", ""])
    sheet.append([day, account, "000204", "5.30", "收", currency, "5.30"])
    raw = BytesIO()
    book.save(raw)
    book.close()
    ready = client.post(base + "/imports/preview", files={
        "file": ("example.xlsx", raw.getvalue()),
    }).raise_for_status().json()
    job = base + "/imports/" + ready["import_id"]
    client.post(job + "/commit", json={"mode": ready["mode"], "mapping": ready["mapping"],
                                      "ready_revision": ready["layout_review"]["revision"]}).raise_for_status()
    assert poll(client, case, ready["import_id"])["result"]["ok"] == 2
    records = client.get(job + "/records", params={
        "member_path": "worksheets/0001",
    }).raise_for_status().json()["items"]
    by_position = {record["source_row"]: record for record in records}
    assert by_position[4]["disposition"] == "unresolved"
    assert by_position[4]["reason_code"] == "conflicting_direction"
    card = client.get("/api/context/" + by_position[3]["flow_id"], params={
        "case_id": case, "x": 1, "side": "payee", "review": True,
    }).raise_for_status().json()
    assert bool(card["review"].get("recorded_destination_absence")) is proved


@pytest.mark.parametrize(("extra", "account", "subaccounts", "reported"), [
    (None, "000101", None, True),
    (("2040-06-05 08:00:00", "000101", "000206", "另一原报对端", "9.25", "付", "CNY"), "000101", None, False),
    (("2040-06-04 08:00:30", "000101", "000206", "另一原报对端", "9.25", "付", "CNY"), "000101", None, False),
    (("2040-06-04", "000101", "000206", "另一原报对端", "9.25", "付", "CNY"), "000101", None, False),
    (("2040-06-04 08:00:30", "000101", "000206", "另一原报对端", "9.25", "付", "USD"), "000101", None, True),
    (None, "****0101", None, False),
    (None, "000000", None, False),
    (None, "未提供", None, False),
    (None, "000101", ("01", "01", "01"), True),
    (None, "000101", ("01", "02", "02"), False),
])
def test_same_account_reported_debit_is_separate_from_funds_attribution(
        client, make_case, monkeypatch, extra, account, subaccounts, reported):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base = f"/api/v1/cases/{case}"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["交易时间", "本方账号", "对方账号", "对方户名", "交易金额", "收付标志", "币种"])
    sheet.append(["2040-06-04 08:00:30", account, "000203", "来款人", "23.25", "收", "CNY"])
    sheet.append(["2040-06-04 08:00:30", account, "000204", "另一来款人", "3.10", "收", "CNY"])
    sheet.append(["2040-06-05 08:00:00", account, account, "原报对端", "16.30", "付", "CNY"])
    if subaccounts:
        sheet.cell(1, 8, "子账户序号")
        for number, subaccount in enumerate(subaccounts, start=2):
            sheet.cell(number, 8, subaccount)
    if extra:
        sheet.append(extra)
    # An Excel writer can materialize empty cells beyond the header.
    for number in range(2, sheet.max_row + 1):
        sheet.cell(number, 9, "")
    raw = BytesIO()
    workbook.save(raw)
    ready = client.post(base + "/imports/preview",
                        files={"file": ("example.xlsx", raw.getvalue())}).raise_for_status().json()
    job = base + "/imports/" + ready["import_id"]
    submission = {"mode": ready["mode"], "mapping": ready["mapping"]}
    if ready.get("layout_review"):
        submission["ready_revision"] = ready["layout_review"]["revision"]
    client.post(job + "/commit", json=submission).raise_for_status()
    batch = poll(client, case, ready["import_id"])["result"]["batch_id"]
    member = "worksheets/0001"
    [anchor] = client.get(job + "/records", params={
        "member_path": member, "source_row": 2,
    }).raise_for_status().json()["items"]
    anchor_id = anchor["flow_id"]
    card = client.get("/api/context/" + anchor_id, params={
        "case_id": case, "x": 1, "side": "payee", "review": True,
    }).raise_for_status().json()
    review = card["review"]
    actual = review.get("reported_destination")
    if not reported:
        assert actual is None
        return
    assert actual is not None
    assert actual["member_path"] == member
    assert actual["anchor"]["source_row"] == 2
    assert actual["debit"]["source_row"] == 4
    assert actual["debit"]["counterparty_account"] == {"column": 3, "value": "000101"}
    assert actual["debit"]["counterparty_name"] == {"column": 4, "value": "原报对端"}
    assert [row["source_row"] for row in actual["other_credits"]] == [3]
    assert actual["conditional_upper"] == {"amount": "16.30", "currency": "CNY",
                                           "amount_column": 5, "header_declares_unit": False}
    assert actual["actual_share"] is None
    assert review["principal"]["target"]["status"] == "unknown"
    assert card["suggestions"]["targets"] == []
    headers = {"X-FundsTrace-Review": review["token"]}
    detail_path = f"/api/context/{anchor_id}/record/{actual['debit']['id']}"
    detail = client.get(detail_path, params={"case_id": case}, headers=headers).raise_for_status().json()
    [original] = detail["originals"]
    assert original["source_row"] == 4
    assert original["cells"][2]["value"] == "000101"
    assert client.delete(base + f"/sources/{ready['import_id']}/operation",
                         params={"expected_batch_id": batch}).status_code == 200
    assert client.get(detail_path, params={"case_id": case}, headers=headers).status_code == 409
