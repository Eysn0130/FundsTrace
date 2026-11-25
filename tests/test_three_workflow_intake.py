"""Complete synthetic intake through the same v1 preview and source receipts."""
import csv
import hashlib
import io
import json
import os
import signal
import sys
from threading import Event
from types import SimpleNamespace

import pytest

from app import pdf_import
from app.services import import_ready_evidence


@pytest.mark.parametrize("label", ["Currency Code", "币种代码"])
def test_borrow_lend_notes_do_not_deny_a_physically_labelled_currency_field(label):
    notes = []
    names = [import_ready_evidence.table_import._norm(name) for name in ("借方发生额", "贷方发生额", label)]
    import_ready_evidence._declared_intake_notes(names, [(2, ["3.10", "0.00", "CNY"])],
        lambda columns, text, rows: notes.append(text), True)
    assert len(notes) == 1
    assert "未列明币种字段" not in notes[0]


@pytest.mark.parametrize("failed", [False, True])
def test_source_card_distinguishes_original_type_and_status_without_changing_admission(client, make_case, monkeypatch, failed):
    from test_automatic_table_import import preview

    case = make_case("Synthetic registered transaction descriptions")
    enable(client, monkeypatch)
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows([
        ["交易账号", "对方账号", "交易时间", "交易金额", "收付标志", "币种", "交易类型",
         "支付交易类型", "原交易状态码", "交易是否成功", "交易方式", "账户余额"],
        ["10001230", "10004560", "2034-05-03 09:15:10", "30.24", "支", "CNY", "原申报类别",
         "来源分类说明", "成功" if failed else "", "失败" if failed else "成功", "", "70.10"],
    ])
    original = stream.getvalue().encode()
    view = preview(client, case, original)
    assert view["table_summary"]["accepted"] == (0 if failed else 1)
    if failed:
        assert view["table_summary"]["reason_counts"] == {"failed_transaction": 1}
    assert any(proof["field"] == "channel" and proof["column"] == "交易类型"
               for proof in view["table_summary"]["mapping_evidence"])
    source = view["import_id"]
    notes = material(client, case, source)["source_field_notes"]
    assert any(note["columns"] == [7] and note["rows"] == [2] and "归集到现有渠道字段" in note["text"] for note in notes)
    assert any(note["columns"] == [8] and note["rows"] == [2] and "记载原表交易类型" in note["text"] for note in notes)
    if failed:
        assert any(note["columns"] == [9] and "「原交易状态码」" in note["text"] and "格填写成功" in note["text"] for note in notes)
        assert any(note["columns"] == [10] and "原表报告失败" in note["text"] for note in notes)
        assert all("原表报告成功" not in note["text"] for note in notes)
    else:
        assert any(note["columns"] == [9] and note["rows"] == [2] and "状态原格为空" in note["text"] for note in notes)
        assert any(note["columns"] == [10] and "原表报告成功，最终结算需另核" in note["text"] for note in notes)
    assert complete(client, case, view)["result"]["ok"] == (0 if failed else 1)
    assert material(client, case, source)["source_field_notes"] == notes


from test_folder_evidence import account, archive, complete, table, transaction
from test_import_layout_review import setup_review


def test_source_card_compares_main_payer_payee_and_counter_account_per_original_row(
        client, database, make_case, monkeypatch):
    from test_automatic_table_import import preview

    enable(client, monkeypatch)
    case = make_case("Synthetic source party roles")
    output = io.StringIO(newline="")
    csv.writer(output, delimiter="\t").writerows([
        ["对方帐号", "交易时间", "真实收款人账号", "真实付款人帐号", "主账户账号", "交易金额"],
        ["09010", "2039-01-20 12:00:00", "08102", "09010", "08102", "14.50"],
        ["09010", "2039-01-20 12:01:00", "09010", "08102", "08102", "11.25"],
        ["00082", "2039-01-20 12:02:00", "09010", "08102", "08102", "1"],
        ["08102", "2039-01-20 12:03:00", "08102", "08102", "08102", "4"],
        ["09010", "2039-01-20 12:04:00", "09010", "8102", "08102", "7"],
        ["", "2039-01-20 12:05:00", "09010", "08102", "08102", "8"],
        ["09010", "2039-01-20 12:06:00", "09010", "00000", "00000", "9"],
    ])
    original = output.getvalue().encode()
    view = preview(client, case, original)
    assert view["table_summary"]["accepted"] == 0
    source = view["import_id"]
    card = material(client, case, source)
    notes = [note for note in card["source_field_notes"] if note["columns"] == [1, 3, 4, 5]]
    assert sorted(note["rows"] for note in notes) == [[], [2], [3], [4, 5, 6, 7, 8]]
    assert any(note["rows"] == [2] and "主账户是收款方" in note["text"] for note in notes)
    assert any(note["rows"] == [3] and "主账户是付款方" in note["text"] for note in notes)
    assert any(not note["rows"] and "第 5 列为主账户账号" in note["text"] and "各自核证" in note["text"] for note in notes)
    assert all("08102" not in note["text"] and "09010" not in note["text"] for note in notes)
    assert not any("主账户是" in note["text"] for note in notes if 4 in note["rows"])
    receipt = complete(client, case, view)["result"]
    assert receipt["ok"] == 0
    with database._lock:
        database._con.close()
        database._con = None
    assert material(client, case, source)["source_field_notes"] == card["source_field_notes"]
    base = f"/api/v1/cases/{case}"
    path = base + f"/sources/{source}"
    assert client.get(path + "/file").content == original
    assert client.delete(path + "/operation", params={"expected_batch_id": receipt["batch_id"]}).status_code == 200
    assert material(client, case, source).get("source_field_notes") in (None, [])
    assert client.get(base + "/flows").json()["total"] == 0
    assert client.get(path + "/file").content == original


def test_unmapped_payment_reports_labelled_endpoints_and_amounts_without_assuming_own_side(
        client, database, make_case, monkeypatch):
    from test_automatic_table_import import preview

    enable(client, monkeypatch)
    case = make_case("Synthetic unmapped payment roles")
    output = io.StringIO(newline="")
    csv.writer(output, delimiter="\t").writerows([
        ["工作日期", "收款人帐号", "手续费", "借贷标志", "付款方账号", "金额", "往来标志", "附录"],
        ["2039-01-20", "09010", "", "2", "08102", "1450", "9", ""],
        ["2039-01-21", "08102", "12", "2", "09010", "1125", "8", ""],
    ])
    original = output.getvalue().encode()
    view = preview(client, case, original)
    assert view["table_summary"]["accepted"] == 0
    source = view["import_id"]
    notes = material(client, case, source)["source_field_notes"]
    assert any(note["columns"] == [2, 5] and note["rows"] == [2, 3] and "本账归属待核" in note["text"] for note in notes)
    assert any(note["columns"] == [3, 6] and "手续费" in note["text"] and "原表未列明币种字段" in note["text"] for note in notes)
    assert any(note["columns"] == [4, 7] and "代码释义及本账收付待核" in note["text"] for note in notes)
    assert all("09010" not in note["text"] and "08102" not in note["text"] for note in notes)
    receipt = complete(client, case, view)["result"]
    assert receipt["ok"] == 0
    with database._lock:
        database._con.close()
        database._con = None
    assert material(client, case, source)["source_field_notes"] == notes
    route = f"/api/v1/cases/{case}/sources/{source}"
    assert client.get(route + "/file").content == original
    assert client.delete(route + "/operation", params={"expected_batch_id": receipt["batch_id"]}).status_code == 200
    assert not material(client, case, source).get("source_field_notes")


@pytest.mark.parametrize("boundary", ["duplicate_endpoint", "repeated_header", "currency_column"])
def test_unmapped_payment_source_notes_respect_header_scope_and_currency(boundary):
    headings = ["付款账号", "收款账号", "金额", "往来标志", "借贷标志", "备注"]
    rows = [(2, ["001", "002", "35", "1", "2", ""])]
    if boundary == "duplicate_endpoint":
        headings[-1] = "付款方帐号"
    elif boundary == "repeated_header":
        rows.append((3, headings[::-1]))
    else:
        headings[-1] = "币种代码"
    notes = []
    import_ready_evidence._declared_intake_notes(headings, rows, lambda cols, text, positions: notes.append(text), False)
    if boundary == "currency_column":
        assert any("金额按原格回查" in note for note in notes)
        assert all("未列明币种" not in note for note in notes)
    else:
        assert not notes


@pytest.mark.parametrize("boundary", ["duplicate_role", "later_header"])
def test_party_account_source_notes_require_unique_roles_in_the_current_table(boundary):
    headings = ["主账户账号", "真实付款人账号", "真实收款人账号", "对方账号", "摘要"]
    rows = [(2, ["001", "001", "002", "002", "普通原值"])]
    if boundary == "duplicate_role":
        headings[-1] = "主账户帐号"
    else:
        rows.extend([(3, headings[::-1]), (4, rows[0][1][::-1])])
    notes = []
    import_ready_evidence._party_account_notes(headings, rows, lambda *note: notes.append(note))
    assert not notes


@pytest.mark.parametrize("opposite,stated,expected", [("", "12.34", True), ("0.20", "12.34", False),
                                                     ("", "13.49", False), ("-0.10", "12.34", False),
                                                     ("--", "12.34", False)])
def test_split_amounts_explain_the_original_rows_without_decoding_the_direction_code(
        client, make_case, monkeypatch, opposite, stated, expected):
    from test_automatic_table_import import preview

    case = make_case("Synthetic directional code and split amounts")
    enable(client, monkeypatch)
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows([
        ["交易账号", "交易时间", "借贷标志", "借方金额", "贷方金额", "余额", "旁证交易金额"],
        ["10102111", "2034-06-01 00:10:00", "9", opposite, "12.34", "50.23", stated],
        ["10102111", "2034-06-01 00:11:00", "8", "2.09", "0.00", "48.14", "2.09"],
    ])
    raw = stream.getvalue().encode()
    view = preview(client, case, raw)
    assert view["table_summary"]["accepted"] == 0
    source = view["import_id"]
    initial = material(client, case, source)
    claims = initial.get("source_field_notes", [])
    assert any(note["rows"] == [2] and "本账入账" in note["text"] and "原值 12.34" in note["text"]
               and "原格为空" in note["text"] for note in claims) is expected
    if expected:
        assert any(note["rows"] == [3] and "本账出账" in note["text"] and "原格记为零" in note["text"] for note in claims)
        assert any(note["columns"] == [3] and note["rows"] == [2, 3] and "来源码表" in note["text"] for note in claims)
        assert all("收付仍待核" not in note["text"] for note in claims)
    done = complete(client, case, view)
    assert done["result"]["ok"] == 0
    assert material(client, case, source).get("source_field_notes", []) == claims
    response = client.delete(f"/api/v1/cases/{case}/sources/{source}/operation",
                             params={"expected_batch_id": done["result"]["batch_id"]})
    assert response.status_code == 200
    assert not material(client, case, source).get("source_field_notes")


def trial_workbook(*, variant=False):
    from test_automatic_table_import import account_inquiry_rows
    from test_table_multisheet_append import encoded

    inquiry = account_inquiry_rows(2)
    if variant:
        inquiry[1] = (*inquiry[1][:-1], "Synthetic different original")
    ledger = [("账户号", "对方账号", "收入", "支出", "余额", "交易日期", "交易时间", "币种"),
              ("SYN-BOOK", "SYN-FROM", "10.01", "", "30.01", "20260703", "20260703 16:18:00", "CNY"),
              ("SYN-BOOK", "SYN-TO", "", "4.02", "25.99", "20260703", "20260703 16:19:00", "CNY"),
              ("SYN-BOOK", "SYN-TO", "0", "0", "25.99", "20260703", "20260703 16:20:00", "CNY")]
    return encoded([("Synthetic inquiry", inquiry), ("Synthetic posted ledger", ledger)], ".xlsx")


def trial_directory(client, case, files):
    response = client.post(f"/api/v1/cases/{case}/imports/directory/preview",
                           files=[("files", file) for file in files])
    assert response.status_code == 200, response.text
    return response.json()


def test_trial_directory_workbook_keeps_original_positions_and_deduplicates_only_identical_leaves(client, make_case, monkeypatch):
    case = make_case("Synthetic trial directory copies")
    enable(client, monkeypatch)
    original, changed = trial_workbook(), trial_workbook(variant=True)
    leaves = [("a/first.xlsx", original), ("b/copy.xlsx", original), ("c/other.xlsx", changed)]
    view = trial_directory(client, case, leaves)
    summary = view["table_summary"]
    assert summary["profile"] == "funds-trace-table-archive-v69"
    assert (summary["accepted"], summary["non_monetary"], summary["auxiliary"], summary["unresolved"]) == (6, 3, 6, 0)
    result = complete(client, case, view)
    assert result["stage"] == "done", result
    assert (result["result"]["ok"], result["result"]["rows_dup"]) == (4, 2)
    items = material(client, case, view["import_id"])["items"]
    ledger = {item["member_path"]: item for item in items if item["role"] == "transaction"}
    records = {}
    for name, raw in leaves:
        item = ledger[f"{name}/worksheets/0002"]
        assert client.get(item["original_url"]).content == raw
        rows = client.get(item["records_url"]).json()["items"]
        assert [row["source_row"] for row in rows] == [2, 3, 4]
        records[name] = {row["flow_id"] for row in rows if row["disposition"] in {"imported", "duplicate"}}
    assert records["a/first.xlsx"] == records["b/copy.xlsx"]
    assert records["a/first.xlsx"].isdisjoint(records["c/other.xlsx"])
    assert all(item["status"] == "registered" and item["auxiliary"] == 2 for item in items if item["role"] == "account")
    repeated = complete(client, case, trial_directory(client, case, [(f"again/{name}", raw) for name, raw in leaves]))
    assert repeated["stage"] == "done", repeated
    assert (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 6)


@pytest.mark.parametrize("directory_first", [False, True])
def test_trial_workbook_directory_and_direct_upload_reuse_the_same_observations(client, make_case, monkeypatch, directory_first):
    from test_automatic_table_import import preview

    case = make_case("Synthetic direct and directory occurrence")
    enable(client, monkeypatch)
    raw = trial_workbook()
    linked_ids = []
    for directory in (directory_first, not directory_first):
        view = (trial_directory(client, case, [("workbook/ledger.xlsx", raw)]) if directory
                else preview(client, case, raw, "direct.xlsx"))
        assert view["table_summary"]["profile"] == ("funds-trace-table-archive-v69" if directory else "funds-trace-table-v97")
        result = complete(client, case, view)
        expected = (2, 0) if not linked_ids else (0, 2)
        assert result["stage"] == "done" and (result["result"]["ok"], result["result"]["rows_dup"]) == expected, result
        [ledger] = [item for item in material(client, case, view["import_id"])["items"] if item["role"] == "transaction"]
        assert client.get(ledger["original_url"]).content == raw
        rows = client.get(ledger["records_url"]).json()["items"]
        linked_ids.append({row["source_row"]: row["flow_id"] for row in rows if row["flow_id"] is not None})
    assert linked_ids[0] == linked_ids[1] and set(linked_ids[0]) == {2, 3}
    if directory_first:
        shifted = complete(client, case, preview(client, case, trial_workbook(variant=True), "other.xlsx"))
        assert shifted["stage"] == "done" and (shifted["result"]["ok"], shifted["result"]["rows_dup"]) == (2, 0)


@pytest.mark.parametrize("directories", [(True,), (False, True), (True, False)],
                         ids=["directory", "direct-first", "directory-first"])
@pytest.mark.parametrize("with_cards", [False, True], ids=["split-ledger", "different-source-cards"])
def test_trial_workbook_equal_rows_conform_to_direct_fact_identity(client, make_case, monkeypatch, directories, with_cards):
    from test_automatic_table_import import preview
    from test_open_table_voucher_reference import COLUMNS, source_row
    from test_table_multisheet_append import encoded

    if with_cards:
        cards = ["000SYN-CARD-1", "000SYN-CARD-2"]
        values = [source_row(**{"交易卡号": card}) for card in cards]
        rows = [COLUMNS, *[[row.get(field, "") for field in COLUMNS] for row in values]]
    else:
        row = ("SYN-BOOK", "SYN-FROM", "10.01", "", "30.01", "20260703", "20260703 16:18:00", "CNY")
        rows = [("账户号", "对方账号", "收入", "支出", "余额", "交易日期", "交易时间", "币种"), row, row]
    case = make_case("Synthetic equal physical bank rows")
    enable(client, monkeypatch)
    raw = encoded([("Synthetic ledger", rows)], ".xlsx")
    observed = set()
    for directory in directories:
        view = (trial_directory(client, case, [("original/ledger.xlsx", raw)]) if directory
                else preview(client, case, raw, "direct.xlsx"))
        profile = "funds-trace-table-archive-v69" if directory else ("funds-trace-table-v95" if with_cards else "funds-trace-table-v96")
        assert view["table_summary"]["profile"] == profile
        expected = (0, 2) if observed else (1, 1)
        result = complete(client, case, view)
        assert result["stage"] == "done" and (result["result"]["ok"], result["result"]["rows_dup"]) == expected, result
        [leaf] = material(client, case, view["import_id"])["items"]
        records = client.get(leaf["records_url"]).json()["items"]
        assert [row["source_row"] for row in records] == [2, 3]
        observed.update(row["flow_id"] for row in records)
        assert len(observed) == 1 and None not in observed
        if with_cards:
            assert [json.loads(row["details"]["source_ledger_evidence"])["own_card"] for row in records] == cards


def test_trial_directory_reopens_coded_csv_with_its_original_profile_and_positions(client, database, make_case, monkeypatch):
    from test_automatic_table_import import coded_bank_csv

    case = make_case("Synthetic directory copied bank CSV")
    enable(client, monkeypatch)
    raw = coded_bank_csv([("D:借", "-4.25", "95.88", "人民币", "10", "", "SYN-PEER")])
    paths = ["sample.xlsx/worksheets/first.csv", "z/copy.csv"]
    view = trial_directory(client, case, [(path, raw) for path in paths])
    assert view["table_summary"]["profile"] == "funds-trace-table-archive-v69"
    base = f"/api/v1/cases/{case}/imports/{view['import_id']}"
    refreshed = client.post(base + "/preview", json={"mode": view["mode"], "mapping": view["mapping"]})
    assert refreshed.status_code == 200, refreshed.text
    done = complete(client, case, refreshed.json())
    assert done["stage"] == "done" and (done["result"]["ok"], done["result"]["rows_dup"]) == (1, 1), done
    rows = client.get(base + "/records").json()["items"]
    assert [(row["member_path"], row["source_row"], row["details"]["trial_member_parser_version"]) for row in rows] == [
        (path, 2, "funds-trace-table-v98") for path in paths]
    assert all(client.get(item["original_url"]).content == raw for item in material(client, case, view["import_id"])["items"])
    package = client.get(f"/api/cases/{case}/export")
    assert package.status_code == 200, package.text
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    restored_case = restored.json()["case_id"]
    [source] = database.q_raw("select source_id from import_sources where case_id=?", [restored_case])
    assert [item["member_path"] for item in material(client, restored_case, source["source_id"])["items"]] == paths


@pytest.mark.parametrize("conflict", ["different_facts", "multiple_flows"])
def test_trial_workbook_history_must_have_one_exact_physical_link(client, database, make_case, monkeypatch, conflict):
    from app import import_staging
    from test_automatic_table_import import preview

    case = make_case("Synthetic conflicting workbook history")
    enable(client, monkeypatch)
    raw = trial_workbook()
    view = preview(client, case, raw, "direct.xlsx")
    base = f"/api/v1/cases/{case}/imports/{view['import_id']}"
    if conflict == "different_facts":
        response = client.post(base + "/preview", json={"mode": view["mode"], "mapping": {**view["mapping"], "channel": "余额"}})
        assert response.status_code == 200, response.text
        view = response.json()
    assert complete(client, case, view)["result"]["ok"] == 2
    staged_competitors = []
    if conflict == "multiple_flows":
        repeated = preview(client, case, raw, "prior-copy.xlsx")
        assert complete(client, case, repeated)["result"]["rows_dup"] == 2
        [link] = database.q_raw("""select flow_id,source_row from flow_sources where case_id=? and source_id=?
            order by source_row limit 1""", [case, repeated["import_id"]])
        [allocated] = database.q_raw("select nextval('flow_seq') id")
        database.x("insert into flows select * replace (?::bigint as id) from flows where case_id=? and id=?",
                   [allocated["id"], case, link["flow_id"]])
        database.x("update flow_sources set flow_id=? where case_id=? and source_id=? and source_row=?",
                   [allocated["id"], case, repeated["import_id"], link["source_row"]])
        write = import_staging.StagedMap.__setitem__
        expected_ids = {link["flow_id"], allocated["id"]}

        def observe(index, key, value):
            if isinstance(value, set) and all(isinstance(entry, tuple) and len(entry) == 8 for entry in value):
                if {entry[-1] for entry in value} == expected_ids:
                    staged_competitors.append(key)
            write(index, key, value)

        monkeypatch.setattr(import_staging, "PLAN_CHUNK", 1)
        monkeypatch.setattr(import_staging.StagedMap, "__setitem__", observe)
    before = database.q_raw("select count(*) n from flows where case_id=?", [case])
    response = client.post(f"/api/v1/cases/{case}/imports/directory/preview",
                           files=[("files", ("workbook/ledger.xlsx", raw))])
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == ("trial_occurrence_conflict" if conflict == "different_facts" else "trial_occurrence_ambiguous")
    assert database.q_raw("select count(*) n from flows where case_id=?", [case]) == before
    if conflict == "multiple_flows":
        assert staged_competitors


@pytest.mark.parametrize("entry", ["together", "separately", "after-preview"])
def test_trial_directory_rejects_shared_history_from_different_originals(client, database, make_case, monkeypatch, entry):
    from test_automatic_table_import import preview

    case = make_case("Synthetic distinct originals with shared direct history")
    enable(client, monkeypatch)
    first, second = trial_workbook(), trial_workbook(variant=True)
    assert first != second
    first_view = preview(client, case, first, "first.xlsx")
    first_done = complete(client, case, first_view)
    assert first_done["stage"] == "done" and first_done["result"]["ok"] == 2
    if entry == "after-preview":
        ready = trial_directory(client, case, [("first.xlsx", first)])
    second_view = preview(client, case, second, "second.xlsx")
    second_done = complete(client, case, second_view)
    assert second_done["stage"] == "done" and (second_done["result"]["ok"], second_done["result"]["rows_dup"]) == (0, 2)
    previous = database.q_raw("select source_id,source_row,flow_id from flow_sources where case_id=? order by source_id,source_row", [case])
    assert len(previous) == 4 and len({link["flow_id"] for link in previous}) == 2

    base = f"/api/v1/cases/{case}/imports"
    if entry == "after-preview":
        failed = complete(client, case, ready)
        assert failed["stage"] == "error" and failed["error_code"] == "trial_occurrence_conflict", failed
    else:
        leaves = [("first.xlsx", first), ("second.xlsx", second)]
        selections = [leaves] if entry == "together" else [[leaf] for leaf in leaves]
        for selection in selections:
            response = client.post(base + "/directory/preview", files=[("files", file) for file in selection])
            assert response.status_code == 409, response.text
            assert response.json()["error"]["code"] == "trial_occurrence_conflict"

    assert database.q_raw("select source_id,source_row,flow_id from flow_sources where case_id=? order by source_id,source_row", [case]) == previous
    assert database.q_raw("select count(*) n from flows where case_id=?", [case]) == [{"n": 2}]
    for view, done in ((first_view, first_done), (second_view, second_done)):
        assert client.get(f"{base}/{view['import_id']}").json()["result"] == done["result"]


def pdf_bytes():
    content = b"BT /F1 14 Tf 40 250 Td (Synthetic original page) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    body = bytearray(b"%PDF-1.4\n")
    offsets = []
    for num, obj in enumerate(objects, 1):
        offsets.append(len(body))
        body.extend(f"{num} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(body)
    body.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        body.extend(f"{offset:010d} 00000 n \n".encode())
    body.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    return bytes(body)


def enable(client, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)


def material(client, case, source):
    result = client.get(f"/api/v1/cases/{case}/imports/{source}/intake")
    assert result.status_code == 200, result.text
    assert result.headers["Cache-Control"] == "no-store"
    return result.json()


def test_directory_retains_unreadable_member_and_writes_only_verified_observations(client, database, make_case, monkeypatch):
    case, other = make_case("Synthetic intake"), make_case("Synthetic separate intake")
    broken = b"not an XLSX workbook"
    original = pdf_bytes()
    sources = [
        ("nested/damaged.xlsx", broken),
        ("looks-like-ledger.pdf", original),
        ("looks-like-totals.csv", table("account", [account(**{"通信地址": "SYN address only"})])),
        ("address-material.csv", table("address", [{"地址": "SYN test address"}])),
        ("actual.csv", table("transaction", [transaction(**{"交易金额": "10.25"})])),
    ]
    route = f"/api/v1/cases/{case}/imports/directory/preview"
    files = [("files", item) for item in sources]
    assert client.post(route, files=files).status_code == 400
    enable(client, monkeypatch)
    res = client.post(route, files=files)
    assert res.status_code == 200, res.text
    preview = res.json()
    source = preview["import_id"]
    ready = material(client, case, source)
    by_path = {row["member_path"]: row for row in ready["items"]}
    assert ready["stage"] == "ready"
    assert by_path["looks-like-totals.csv"]["role"] == "account"
    assert by_path["address-material.csv"]["role"] == "address"
    assert by_path["looks-like-ledger.pdf"]["reason"] == "unsupported_attachment"
    damaged = by_path["nested/damaged.xlsx"]
    assert (damaged["status"], damaged["reason"], damaged["next_action"]) == (
        "unreadable", "intake_member_unreadable", "replace_or_retry")
    assert client.get(damaged["original_url"]).content == broken
    assert client.get(damaged["original_url"].replace(f"/cases/{case}/", f"/cases/{other}/", 1)).status_code == 404
    assert client.get(f"/api/v1/cases/{case}/sources/{source}/member-file",
                      params={"member_path": "../nested/damaged.xlsx"}).status_code == 404
    result = complete(client, case, preview)
    assert result["stage"] == "done", result
    assert result["result"]["ok"] == 1
    done = material(client, case, source)
    assert done["stage"] == "done"
    by_path = {row["member_path"]: row for row in done["items"]}
    assert by_path["actual.csv"]["status"] == "imported"
    assert by_path["looks-like-totals.csv"]["status"] == "registered"
    assert client.get(by_path["looks-like-ledger.pdf"]["original_url"]).content == original
    damaged_row = client.get(by_path["nested/damaged.xlsx"]["records_url"]).json()["items"][0]
    assert damaged_row["reason_code"] == "intake_member_unreadable" and damaged_row["flow_id"] is None
    [flow] = client.get(f"/api/v1/cases/{case}/flows?granularity=observations").json()["items"]
    assert flow["currency"] == "CNY" and flow["amount"] == "10.25"
    exported = client.get(f"/api/cases/{case}/export")
    restored = client.post("/api/cases/import", files={"file": ("intake.fundcase", exported.content)})
    assert restored.status_code == 200, restored.text
    copies = database.q_raw("select source_id from import_sources where case_id=?", [restored.json()["case_id"]])
    restored_receipt = material(client, restored.json()["case_id"], copies[0]["source_id"])
    assert not restored_receipt["source_available"]
    assert next(row for row in restored_receipt["items"] if row["member_path"] == "nested/damaged.xlsx")["reason"] == "intake_member_unreadable"


def test_archive_path_corruption_still_rejects_the_entire_upload(client, database, make_case, monkeypatch):
    case = make_case()
    enable(client, monkeypatch)
    files = [("actual.csv", table("transaction", [transaction()])), ("../unsafe.xlsx", b"not a workbook")]
    result = client.post(f"/api/v1/cases/{case}/imports/preview", files={"file": ("unsafe.zip", archive(files))})
    assert result.status_code == 400 and database.q_raw("select * from import_sessions where case_id=?", [case]) == []


def test_direct_registered_account_table_uses_provenance_and_never_creates_flows(client, make_case, monkeypatch):
    case = make_case()
    enable(client, monkeypatch)
    raw = table("account", [account(**{"通信地址": "SYN address"})])
    response = client.post(f"/api/v1/cases/{case}/imports/preview", files={"file": ("name-does-not-classify.csv", raw)})
    assert response.status_code == 200, response.text
    preview = response.json()
    source_id = preview["import_id"]
    [item] = material(client, case, source_id)["items"]
    assert item["role"] == "account" and item["auxiliary"] == 1
    done = complete(client, case, preview)
    assert done["stage"] == "done", done
    assert done["result"]["ok"] == 0
    [item] = material(client, case, source_id)["items"]
    assert item["status"] == "registered" and client.get(item["original_url"]).content == raw
    [row] = client.get(item["records_url"]).json()["items"]
    assert row["details"]["source_role"] == "account" and row["flow_id"] is None


@pytest.mark.parametrize("reason", [pdf_import.CANDIDATE, pdf_import.CELL_QUALITY_UNAVAILABLE])
def test_pdf_review_preserves_pages_candidates_and_package_without_publishing_money(client, database, make_case, monkeypatch, reason):
    case = make_case()
    raw = pdf_bytes()
    url = f"/api/v1/cases/{case}/imports/preview"
    assert client.post(url, files={"file": ("statement.pdf", raw)}).status_code == 400
    enable(client, monkeypatch)

    def extract(source, size, digest, cancelled):
        assert size == len(raw) and digest == hashlib.sha256(raw).hexdigest() and source.read() == raw
        return {"reason": reason, "rows": [[1, 1, 1, ["Synthetic original page", "123.45"]]], "page_count": 1}

    monkeypatch.setattr(pdf_import, "extract", extract)
    response = client.post(url, files={"file": ("statement.pdf", raw)})
    assert response.status_code == 200, response.text
    preview = response.json()
    source = preview["import_id"]
    [item] = material(client, case, source)["items"]
    assert (item["role"], item["page"], item["reason"]) == ("pdf", 1, reason)
    assert client.get(item["original_url"]).content == raw
    assert preview["table_summary"]["profile"] == "pdf-review-v1"
    assert preview["table_summary"]["accepted"] == 0
    unauthorized = client.post(f"/api/v1/cases/{case}/imports/{source}/commit",
                               json={"mode": "A", "mapping": {"amount": "123.45"}})
    assert unauthorized.status_code == 422
    done = complete(client, case, preview)
    assert done["stage"] == "done" and done["result"]["ok"] == 0
    [item] = material(client, case, source)["items"]
    [record] = client.get(item["records_url"]).json()["items"]
    assert record["details"]["pdf_page"] == "1" and record["flow_id"] is None
    original = client.get(f"/api/v1/cases/{case}/sources/{source}/file")
    assert original.content == raw
    package = client.get(f"/api/cases/{case}/export").content
    restored = client.post("/api/cases/import", files={"file": ("pdf-review.fundcase", package)})
    assert restored.status_code == 200, restored.text
    restored_case = restored.json()["case_id"]
    [saved] = database.q_raw("select source_id from import_sources where case_id=?", [restored_case])
    recovered = material(client, restored_case, saved["source_id"])
    [page] = recovered["items"]
    assert (page["page"], page["reason"], page["original_url"]) == (1, reason, None)
    assert client.get(page["records_url"]).json()["items"][0]["details"] == record["details"]
    removed = client.delete(f"/api/v1/cases/{case}/sources/{source}/operation",
                            params={"expected_batch_id": done["result"]["batch_id"]})
    assert removed.status_code == 200, removed.text
    assert material(client, case, source)["stage"] == "withdrawn"


@pytest.mark.parametrize(("configured", "budget"), [(None, 90), ("90", 90), ("600", 600), ("900", 900)])
def test_pdf_review_budget_comes_from_local_configuration(client, make_case, monkeypatch, tmp_path, configured, budget):
    enable(client, monkeypatch)
    executable = tmp_path / "synthetic-mineru"
    executable.touch()
    monkeypatch.setenv("FUND_MINERU_EXECUTABLE", str(executable))
    monkeypatch.setenv("FUND_MINERU_MODEL_DIR", str(tmp_path))
    if configured is None:
        monkeypatch.delenv("FUND_MINERU_REVIEW_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("FUND_MINERU_REVIEW_TIMEOUT_SECONDS", configured)
    monkeypatch.setattr(pdf_import, "time", SimpleNamespace(monotonic=lambda: 100.0))
    raw = pdf_bytes()

    def run(cli, model_dir, directory, document, output, cancelled, deadline):
        assert (cli, model_dir) == (executable, tmp_path)
        assert document.read_bytes() == raw
        assert deadline == 100 + budget
        return {"reason": "pdf_no_tables", "rows": [], "page_count": 1}

    monkeypatch.setattr(pdf_import, "_run", run)
    response = client.post(f"/api/v1/cases/{make_case()}/imports/preview", files={"file": ("scan.pdf", raw)},
                           data={"timeout_seconds": "900"})
    assert response.status_code == 200, response.text
    assert response.json()["table_summary"]["reason_counts"] == {"pdf_no_tables": 1}


@pytest.mark.skipif(os.name != "posix", reason="Checks the owned POSIX process group")
@pytest.mark.parametrize("cancel_upload", [True, False])
def test_extended_pdf_review_stops_and_reaps_its_owned_process_group(monkeypatch, tmp_path, cancel_upload):
    executable = tmp_path / "synthetic-mineru"
    executable.touch()
    monkeypatch.setenv("FUND_MINERU_EXECUTABLE", str(executable))
    monkeypatch.setenv("FUND_MINERU_MODEL_DIR", str(tmp_path))
    monkeypatch.setenv("FUND_MINERU_REVIEW_TIMEOUT_SECONDS", "600")
    clock = iter((100.0, 100.0, 300.0 if cancel_upload else 700.0))
    monkeypatch.setattr(pdf_import, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    cancelled = Event()
    raw = pdf_bytes()
    children, signals = [], []
    launch, killpg = pdf_import.subprocess.Popen, os.killpg

    def start(_command, **options):
        assert options["start_new_session"] is True
        process = launch([sys.executable, "-c", "from time import sleep; sleep(30)"], **options)
        children.append(process)
        if cancel_upload:
            cancelled.set()
        return process

    def kill_owned_group(group, signum):
        signals.append((group, signum))
        assert group == children[0].pid
        return killpg(group, signum)

    monkeypatch.setattr(pdf_import.subprocess, "Popen", start)
    monkeypatch.setattr(os, "killpg", kill_owned_group)
    result = pdf_import.extract(io.BytesIO(raw), len(raw), hashlib.sha256(raw).hexdigest(), cancelled)
    assert result == (None if cancel_upload else {"reason": "pdf_ocr_timeout", "rows": []})
    [process] = children
    assert signals == [(process.pid, signal.SIGKILL)]
    assert process.returncode == -signal.SIGKILL


def test_column_evidence_counts_all_rows_and_samples_the_first_tail_disagreement(client, make_case):
    case = make_case()
    records = [f"2038-01-01 09:{idx:02d}:00,00012,{('00016' if idx == 9 else '00012')},SYN-B,1.00,出,CNY"
               for idx in range(10)]
    raw = ("不明时间,本端账号,另一个本方账号,对方账号,交易金额,收付标志,交易币种\n"
           + "\n".join(records) + "\n").encode()
    preview, _, body = setup_review(client, case, raw)
    profile = import_ready_evidence.column_evidence(str(case), preview["import_id"], SimpleNamespace(
        **body, member_path="table.csv", header_row=1, column_start=1, compare_columns=[2, 3]))
    assert profile["coverage"] == {
        "first_row": 2, "last_row": 11, "scanned_rows": 10, "complete": True, "reason": None}
    primary = profile["columns"][1]
    assert primary["heading"] == "本端账号" and primary["kinds"] == {"text": 10}
    assert primary["leading_zero"] == primary["nonempty"] == 10
    assert primary["length_counts"] == {"5": 10}
    assert primary["comparisons"] == [{"column": 3, "different": 1, "first_difference": 11,
                                       "both_present": 10, "equal_present": 9, "missing_rows": 0}]
    assert {sample["source_row"] for sample in primary["samples"]} == {2, 6, 11}


def test_known_table_preamble_and_transaction_have_separate_physical_roles(client, make_case, monkeypatch):
    case = make_case()
    enable(client, monkeypatch)
    raw = ("字段口径：本方账号是逐笔交易本方账号。\n"
           "交易时间,本方账号,对方账号,交易金额,收付标志,交易币种\n"
           "2038-01-01 09:01:00,SYN-A,SYN-B,18.20,出,CNY\n"
           "2038-01-01 09:02:00,SYN-A,SYN-C,22.30,入,CNY\n").encode()
    route = f"/api/v1/cases/{case}/imports/preview"
    response = client.post(route, files={"file": ("one-table.csv", raw)})
    assert response.status_code == 200, response.text
    preview = response.json()
    items = material(client, case, preview["import_id"])["items"]
    note, table_scope = items
    assert (note["role"], note["source_row"], note["accepted"]) == ("description", 1, 0)
    assert (table_scope["role"], table_scope["header_row"], table_scope["last_row"], table_scope["accepted"]) == (
        "transaction", 2, 4, 2)
    result = complete(client, case, preview)
    assert result["stage"] == "done"
    notes = client.get(material(client, case, preview["import_id"])["items"][0]["records_url"]).json()["items"]
    assert any(item["source_row"] == 1 and item["disposition"] == "auxiliary" and not item["flow_id"] for item in notes)
    assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 2


def test_column_profile_reports_full_region_limit_without_sampling_as_proof(client, make_case):
    case = make_case()
    data = ("不明时间,本端账号,对方账号,交易金额,收付标志,交易币种\n"
            + "2038-01-01 09:00:00,SYN-A,SYN-B,1.00,出,CNY\n" * 256).encode()
    preview, _, body = setup_review(client, case, data)
    evidence = import_ready_evidence.column_evidence(str(case), preview["import_id"], SimpleNamespace(
        **body, member_path="table.csv", header_row=1, column_start=1, compare_columns=[2, 3]))
    assert evidence["coverage"] == {"first_row": 2, "last_row": 257, "scanned_rows": 0,
                                    "complete": False, "reason": "layout_trial_limit"}
    assert evidence["columns"] == []


def test_column_profile_reports_first_difference_in_second_region_by_physical_row(client, make_case):
    case = make_case()
    head = "不明时间,本端账号,另一个本方账号,对方账号,交易金额,收付标志,交易币种"
    first = ["2038-01-01 09:00:00,00012,00012,SYN-B,1.00,出,CNY"] * 16
    second = ["2038-02-01 09:00:00,00012,00016,SYN-B,1.00,出,CNY",
              *(["2038-02-01 09:01:00,00012,00012,SYN-B,1.00,出,CNY"] * 12),
              "2038-02-01 09:02:00,00012,00018,SYN-B,1.00,出,CNY"]
    raw = ("\n".join([head, *first, head, *second]) + "\n").encode()
    preview, _, body = setup_review(client, case, raw)
    result = import_ready_evidence.column_evidence(str(case), preview["import_id"], SimpleNamespace(
        **body, member_path="table.csv", header_row=18, column_start=2, compare_columns=[3]))
    assert result["coverage"] == {"first_row": 19, "last_row": 32, "scanned_rows": 14,
                                  "complete": True, "reason": None}
    assert result["columns"][0]["comparisons"] == [{"column": 3, "different": 2, "first_difference": 19,
                                                    "both_present": 14, "equal_present": 12, "missing_rows": 0}]
    assert [sample["source_row"] for sample in result["columns"][0]["samples"]] == [19, 25, 32]


def test_directory_with_only_a_pdf_keeps_original_and_has_zero_financial_rows(client, make_case, monkeypatch):
    case = make_case()
    enable(client, monkeypatch)
    original = pdf_bytes()
    base = f"/api/v1/cases/{case}/imports"
    response = client.post(base + "/directory/preview", files=[("files", ("only.pdf", original))])
    assert response.status_code == 200, response.text
    view = response.json()
    [item] = material(client, case, view["import_id"])["items"]
    assert (item["role"], item["reason"], item["accepted"]) == ("attachment", "unsupported_attachment", 0)
    assert client.get(item["original_url"]).content == original
    report = complete(client, case, view)
    assert report["stage"] == "done" and report["result"]["ok"] == 0
    assert client.get(item["original_url"]).content == original


def test_declared_small_auxiliary_sources_are_classified_by_physical_content(client, database, make_case, monkeypatch):
    case = make_case()
    enable(client, monkeypatch)
    originals = {
        "one.csv": ("名称,证件类型,证件号码,开户账号,开户机构,适用期间\n"
                    "SYN ACTOR,PASSPORT,SYN-DOC,00031,SYN BANK,2038\n").encode(),
        "two.csv": "登记主体,证件类型,证件号码,登记编号\nSYN ACTOR,PASSPORT,SYN-DOC,SYN-REG\n".encode(),
        "three.csv": "地址用途,地址文本,关联主体编号,有效起日,有效止日\n通信地址,SYN PLACE,SYN-REG,2038-01-01,\n".encode(),
        "four.txt": b"MATERIAL NOTES\nScope: one.csv only.\nOriginal roles apply only in that file.\n",
    }
    base = f"/api/v1/cases/{case}/imports/directory/preview"
    response = client.post(base, files=[("files", pair) for pair in originals.items()])
    assert response.status_code == 200, response.text
    view = response.json()
    got = {item["member_path"]: item["role"] for item in material(client, case, view["import_id"])["items"]}
    assert got == {"one.csv": "account", "two.csv": "registry", "three.csv": "address", "four.txt": "description"}
    completed = complete(client, case, view)
    assert completed["stage"] == "done" and completed["result"]["ok"] == 0
    package = client.get(f"/api/cases/{case}/export").content
    returned = client.post("/api/cases/import", files={"file": ("auxiliary.fundcase", package)})
    assert returned.status_code == 200, returned.text
    restored = returned.json()["case_id"]
    [src] = database.q_raw("select source_id from import_sources where case_id=?", [restored])
    assert {item["member_path"]: item["role"] for item in material(client, restored, src["source_id"])["items"]} == got
    direct = client.post(f"/api/v1/cases/{case}/imports/preview", files={"file": ("same.csv", originals["two.csv"])})
    assert direct.status_code == 200, direct.text
    [item] = material(client, case, direct.json()["import_id"])["items"]
    assert item["role"] == "registry"
    assert complete(client, case, direct.json())["result"]["ok"] == 0
