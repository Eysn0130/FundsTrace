"""Source money roles use the original import lifecycle without writing amounts."""
import csv
import io

from app import table_import
from app.services import import_source_money, import_verbatim_money
from test_api_v1 import poll


def _notes(client, job):
    response = client.get(job + "/intake").raise_for_status().json()
    return {note["money_role"]["role"]: note for note in response.get("source_field_notes", ()) if "money_role" in note}


def test_original_money_roles_follow_the_normal_ready_commit_cold_and_withdraw_path(
        client, database, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base = f"/api/v1/cases/{case}"
    header = ["交易账号", "对方账号", "交易时间", "借贷标志", "币种", "交易金额", "交易金额折人民币",
              "交易金额折美元", "交易流水号"]
    rows = [
        ["000042", "000052", "2041-01-03 12:00:01", "11", "09", "125.00", "125.00", "17.30", "ROLE-1"],
        ["000042", "000053", "2041-01-03 12:00:02", "12", "09", "0", "0", "0", "ROLE-2"],
        ["000042", "000054", "2041-01-03 12:00:03", "11", "09", "45.10", "", "6.40", "ROLE-3"],
    ]
    stream = io.StringIO(newline="")
    csv.writer(stream, delimiter="\t").writerows([header, *rows])
    raw = stream.getvalue().encode()
    ready = client.post(base + "/imports/preview", files={"file": ("sample.csv", raw)}).raise_for_status().json()
    assert ready["stats"]["ok"] == 0
    job = base + "/imports/" + ready["import_id"]
    notes = _notes(client, job)
    fields = {role: note["money_role"] for role, note in notes.items()}
    assert {role: item.get("column") for role, item in fields.items()} == {
        "amount": 6, "currency": 5, "balance": None, "converted_cny": 7, "converted_usd": 8,
    }
    assert fields["amount"]["unit_status"] == fields["converted_usd"]["unit_status"] == "not_declared"
    assert fields["currency"]["unit_status"] == "not_applicable"
    assert fields["balance"]["state"] == "absent" and notes["balance"]["columns"] == []
    assert fields["converted_cny"]["value_rows"] == [2, 3] and fields["converted_cny"]["blank_rows"] == [4]
    body = {"mode": ready["mode"], "mapping": ready["mapping"], "ready_revision": ready["layout_review"]["revision"]}
    wrong = {**body, "mapping": {**body["mapping"], "amount": header[6]}}
    rejected = client.post(job + "/preview", json=wrong)
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "invalid_mapping"
    assert client.post(job + "/commit", json=wrong).status_code == 409
    refreshed = client.post(job + "/preview", json=body).raise_for_status().json()
    revision = refreshed["layout_review"]["revision"]
    assert revision != body["ready_revision"] and _notes(client, job) == notes
    assert client.post(job + "/commit", json=body).status_code == 409
    assert client.post(job + "/commit", json={**body, "ready_revision": revision}).raise_for_status().json()["started"]
    done = poll(client, case, ready["import_id"])["result"]
    assert done["ok"] == 0
    with database._lock:
        database._con.close()
        database._con = None
    assert _notes(client, job) == notes
    originals = client.get(job + "/review", params={"member_path": "table.csv", "view": "all"}).raise_for_status().json()
    assert [[cell["value"] for cell in line["original"]] for line in originals["items"]] == rows
    assert all(line["flow_id"] is None for line in originals["items"])
    source = base + "/sources/" + ready["import_id"]
    assert client.delete(source + "/operation", params={"expected_batch_id": done["batch_id"]}).status_code == 200
    assert not _notes(client, job)
    assert client.get(source + "/file").content == raw
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


def test_unregistered_transaction_amounts_have_distinct_source_roles_without_writing_flows(
        client, database, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base = f"/api/v1/cases/{case}"
    header = ["交易账号", "处理日期", "交易金额", "交易后金额", "备注"]
    rows = [
        ["000081", "2042-02-10", "0.00", "0.00", "同值按各自原列核对"],
        ["000081", "2042-02-11", "(0.00)", "12.50", ""],
    ]

    def preview(matrix):
        stream = io.StringIO(newline="")
        csv.writer(stream, delimiter="\t").writerows(matrix)
        raw = stream.getvalue().encode()
        response = client.post(base + "/imports/preview", files={"file": ("sample.csv", raw)})
        return response.raise_for_status().json(), raw

    def notes(job):
        intake = client.get(job + "/intake").raise_for_status().json()
        return [note for note in intake.get("source_field_notes", ()) if note["columns"] == [3, 4]]

    ready, raw = preview([header, *rows])
    assert ready["layout_review"]["members"][0]["header_rows"] == []
    assert ready["table_summary"]["accepted"] == 0 and ready["mapping"] == {}
    job = base + "/imports/" + ready["import_id"]
    [note] = notes(job)
    assert note["header_row"] == 1 and note["rows"] == [2, 3]
    assert "交易发生额按第 3 列原格核对" in note["text"]
    assert "第 4 列的交易后金额仅保留原值和位置" in note["text"]
    assert "余额口径待核" in note["text"]
    assert "为零" not in note["text"]

    body = {"mode": ready["mode"], "mapping": {}, "ready_revision": ready["layout_review"]["revision"]}
    wrong = {**body, "mapping": {"balance": header[3]}}
    rejected = client.post(job + "/preview", json=wrong)
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "invalid_mapping"
    assert client.post(job + "/commit", json=body).raise_for_status().json()["started"]
    done = poll(client, case, ready["import_id"])["result"]
    assert done["ok"] == 0
    with database._lock:
        database._con.close()
        database._con = None
    assert notes(job) == [note]
    originals = client.get(job + "/review", params={"member_path": "table.csv", "view": "all"}).raise_for_status().json()
    assert [[cell["value"] for cell in line["original"]] for line in originals["items"]] == [header, *rows]
    assert all(line["flow_id"] is None for line in originals["items"])
    source = base + "/sources/" + ready["import_id"]
    assert client.delete(source + "/operation", params={"expected_batch_id": done["batch_id"]}).status_code == 200
    assert not notes(job)
    assert client.get(source + "/file").content == raw
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0

    other_header = [*header[:2], "账户余额", "交易后金额", "备注"]
    for matrix in ([header, rows[0], other_header, rows[1]],
                   [header, rows[0], ["续表", "", "", "", ""], rows[1]],
                   [header, rows[0], ["对方账号", "对方户名", "", "", "摘要说明"], rows[1]],
                   [header[:3] + ["备注", "业务说明"], rows[0][:3] + ["交易后金额", ""], rows[1]]):
        other, _ = preview(matrix)
        assert not notes(base + "/imports/" + other["import_id"])


def test_present_balance_remains_located_with_blank_and_missing_cells():
    profile = table_import.CSV_TAIL_COMPLETE_ROWS_PROFILE
    header = [{"value": text} for text in ("交易日期", "交易金额（分）", "币种", "账户余额")]
    rows = {row: [{"value": text} for text in values] for row, values in {
        2: ("2041-01-01", "0", "07", ""),
        3: ("2041-01-02", "110", "07"),
    }.items()}
    fields = {field["role"]: field for field in import_source_money._fields(header, rows, profile)}
    assert fields["amount"]["declared_unit"] == "CENT" and fields["amount"]["value_rows"] == [2, 3]
    assert fields["balance"] == {"role": "balance", "state": "located", "column": 4,
                                 "unit_status": "not_declared", "value_rows": [], "blank_rows": [2], "missing_rows": [3]}
    assert "balance" not in import_verbatim_money.proof(header, rows, profile, (1, 2, 3, 4), "units")
    assert "balance" not in import_verbatim_money.proof(header[:-1], rows, profile, (1, 2, 3), "units", registered=False)


def test_unlabelled_populated_cell_or_unknown_numeric_column_keeps_absence_unproved():
    profile = table_import.CSV_TAIL_COMPLETE_ROWS_PROFILE
    rows = {2: [{"value": text} for text in ("2041-01-01", "125", "07", "40")]}
    for tail in ("", "余额口径未明", "字段X17", "BAL_AMT"):
        header = [{"value": text} for text in ("交易日期", "交易金额", "币种", tail)]
        fields = {field["role"]: field for field in import_source_money._fields(header, rows, profile)}
        assert fields["balance"] == {"role": "balance", "state": "unproved", "unit_status": "unproved"}
        assert "balance" not in import_verbatim_money.proof(header, rows, profile, (1, 2, 3, 4), "units")


def test_first_physical_header_can_support_present_roles_without_lifting_the_financial_gate():
    profile = table_import.VOUCHER_REFERENCE_PROFILE
    header = [{"value": text} for text in ("交易日期", "借贷标志", "发生额", "余额")]
    rows = {2: [{"value": text} for text in ("2041-01-01", "借", "20.30", "")]}
    fields = {field["role"]: field for field in import_source_money._fields(header, rows, profile, registered=False)}
    assert fields["amount"]["column"] == 3 and fields["amount"]["unit_status"] == "not_declared"
    assert fields["balance"]["column"] == 4 and fields["balance"]["blank_rows"] == [2]
    assert fields["currency"]["state"] == "unproved"
    assert not import_source_money._fields(header, {**rows, 3: header}, profile, registered=False)
    labeled = [*header, {"value": "货币码"}]
    with_currency = {2: [*rows[2], {"value": "USD"}]}
    fields = {field["role"]: field for field in import_source_money._fields(labeled, with_currency, profile, registered=False)}
    assert fields["currency"]["column"] == 5 and fields["currency"]["value_rows"] == [2]


def test_scoped_positive_fields_preserve_present_but_blank_neighboring_amounts():
    profile = table_import.VOUCHER_REFERENCE_PROFILE
    labels = ("交易日期", "交易货币", "交易金额", "换算RMB金额", "转入交易货币金额", "转出货币金额",
              "付款币种", "支取参考金额")
    header = [{"value": text} for text in labels]
    rows = {2: [{"value": text} for text in ("2041-01-01", "05", "20.30", "20.30", "", "", "", "")]}
    fields = {field["role"]: field for field in import_source_money._fields(header, rows, profile)}
    assert fields["amount"]["column"] == 3 and fields["currency"]["column"] == 2
    assert fields["converted_cny"]["column"] == 4
    for role, col in (("transfer_in_amount", 5), ("transfer_out_amount", 6), ("other_currency", 7), ("other_amount", 8)):
        assert (fields[role]["column"], fields[role]["blank_rows"]) == (col, [2])
    rows[2][7]["value"] = "19.15"
    fields_after = {field["role"]: field for field in import_source_money._fields(header, rows, profile)}
    assert fields_after["amount"]["state"] == "unproved"
    later = [{"value": value} for value in ("续表", "", "", "", "交易流水号", "凭证号", "", "")]
    assert not import_source_money._fields(header, {**rows, 3: later}, profile)
