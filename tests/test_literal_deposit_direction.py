"""Ordinary preview, confirmation and journal for a closed CSV direction word."""
import csv
import io
import json
import time

import pytest

from test_automatic_table_import import preview


def original(*, variant=None, extra_note=None):
    titles = ["交易账号", "对方账号", "交易时间", "交易方向", "交易金额", "交易摘要"]
    own, peer = "000001002345", "000001009876"
    rows = [[own, peer, "2039-04-05 10:01:01", "存款", "17.23", "普通款项"],
            [own, peer, "2039-04-05 10:02:02", "存款", "22.11", "普通款项"],
            [own, peer, "2039-04-05 10:03:03", "取款", "5.03", "普通款项"]]
    if variant == "business_type":
        titles[3] = "交易类型"
    elif variant == "no_contrast":
        rows[-1][3] = "存款"
    elif variant == "different_owner":
        rows[1][0] = peer
    elif variant == "withdrawal_fee":
        rows[-1][5] = "手续费"
    elif variant == "reversal":
        rows[0][5] = "交易冲正"
    elif variant == "opposite_declaration":
        rows[0][5] = "本表存款表示本方支出"
    elif variant == "signed_title":
        titles[4] = "带符号交易金额"
    elif variant == "sign_declaration":
        rows[0][5] = "本表交易金额正数表示本方支出，绝对值为元；负数表示本方收入。"
    elif variant == "competing_direction":
        titles.append("收付方向")
        for row in rows:
            row.append("出")
    elif variant == "second_header":
        rows.insert(2, titles.copy())
    elif variant == "currency_conflict":
        titles.append("币种")
        for index, row in enumerate(rows):
            row.append("unknown currency" if index == 0 else "USD")
    if extra_note is not None:
        heading, note = extra_note
        if heading in titles:
            rows[0][titles.index(heading)] = note
        else:
            titles.append(heading)
            for index, row in enumerate(rows):
                row.append(note if index == 0 else "")
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows([titles, *rows])
    return stream.getvalue().encode(), rows


def enable_trial(client, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)


def job_url(case, view):
    return f"/api/v1/cases/{case}/imports/{view['import_id']}"


def confirm_body(view):
    return {"mode": view["mode"], "mapping": view["mapping"], "ready_revision": view["layout_review"]["revision"]}


def completed(client, address, body):
    client.post(address + "/commit", json=body).raise_for_status()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = client.get(address).raise_for_status().json()
        if result["stage"] in ("done", "error"):
            assert result["stage"] == "done", result.get("error_code")
            return result["result"]
        time.sleep(.01)
    pytest.fail("ordinary confirmation did not complete")


def test_limited_deposit_rule_preserves_default_and_survives_confirmation_and_reopening(client, database, make_case, monkeypatch):
    from app import import_preview_cache

    raw, cells = original()
    old_case = make_case("Synthetic legacy input")
    before = preview(client, old_case, raw)
    assert before["table_summary"]["profile"] == "funds-trace-table-v69"
    assert before["table_summary"]["accepted"] == 1
    enable_trial(client, monkeypatch)
    old_address = job_url(old_case, before)
    old_repeat = client.post(old_address + "/preview", json=confirm_body(before)).raise_for_status().json()
    assert old_repeat["table_summary"]["profile"] == "funds-trace-table-v69"
    assert old_repeat["table_summary"]["accepted"] == 1

    case = make_case("Synthetic ordinary direction")
    view = preview(client, case, raw)
    assert view["table_summary"]["profile"] == "funds-trace-table-v104"
    assert view["table_summary"]["accepted"] == 3 and view["automatic"] is False
    assert view["table_summary"]["reason_counts"] == {"transaction_valid": 3}
    address = job_url(case, view)
    base = f"/api/v1/cases/{case}"
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0
    assert client.post(address + "/commit", json={"mode": view["mode"], "mapping": view["mapping"]}).status_code == 409
    changed = {**confirm_body(view), "mapping": {**view["mapping"], "direction": "交易摘要"}}
    assert client.post(address + "/preview", json=changed).status_code == 422
    repeat = client.post(address + "/preview", json=confirm_body(view)).raise_for_status().json()
    assert repeat["layout_review"]["revision"] != view["layout_review"]["revision"]
    assert client.post(address + "/commit", json=confirm_body(view)).status_code == 409
    import_preview_cache.discard(view["import_id"])
    receipt = completed(client, address, confirm_body(repeat))
    assert receipt["ok"] == 3
    with database._lock:
        database._con.close()
        database._con = None
    record_reply = client.get(address + "/records", params={"member_path": "table.csv"}).raise_for_status().json()
    notes = client.get(address + "/intake").raise_for_status().json()["source_field_notes"]
    assert any(note["columns"] == [4, 5] and note["rows"] == [2, 3] and "来源方向" in note["text"] for note in notes)
    assert any("原表未列示币种" in note["text"] and "默认" in note["text"] for note in notes)
    for item, row in zip(record_reply["items"], cells, strict=True):
        observed = json.loads(item["details"]["source_ledger_evidence"])
        assert observed["source_direction"] == row[3] and observed["source_currency"] == ""
        assert observed["direction"] == ("in" if row[3] == "存款" else "out")
        assert observed["row"]["currency"] == "CNY"
        assert observed["row"]["self_account"] == observed["row"]["payee_account" if row[3] == "存款" else "payer_account"]
    original_url = f"{base}/sources/{view['import_id']}"
    assert client.get(original_url + "/file").content == raw

    second = preview(client, case, raw)
    reused = completed(client, job_url(case, second), confirm_body(second))
    assert reused["ok"] == 0 and reused["rows_dup"] == 3
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 3
    assert client.delete(f"{base}/sources/{second['import_id']}/operation",
                         params={"expected_batch_id": reused["batch_id"]}).status_code == 200
    assert client.delete(original_url + "/operation", params={"expected_batch_id": receipt["batch_id"]}).status_code == 200
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


@pytest.mark.parametrize("variant", ["business_type", "no_contrast", "different_owner", "withdrawal_fee", "reversal",
                                    "opposite_declaration", "signed_title", "sign_declaration", "competing_direction", "second_header"])
def test_closed_deposit_literal_declines_out_of_scope_csvs(client, make_case, monkeypatch, variant):
    enable_trial(client, monkeypatch)
    view = preview(client, make_case(), original(variant=variant)[0])
    assert view["table_summary"]["profile"] != "funds-trace-table-v104"


@pytest.mark.parametrize(("heading", "note", "withdrawal_conflicts"), [
    ("方向说明", "本表存款=本方支出、取款=本方收入", True),
    ("借贷码表", "以报告方提供的另附码表为准", False),
    ("附加材料", "本表存款：本方支出；取款：本方收入", True),
    ("交易摘要", "本表方向约定：“存款”表示本方支出，“取款”表示本方收入。", True),
    ("方向说明", "本表存款表示本方支出", False),
    ("方向说明", "另一表的取款=本方收入", False),
], ids=["explicit-opposite-note", "unverified-codebook", "opposite-note-in-unmapped-column", "quoted-opposite-summary",
        "only-deposit-rebutted", "other-table-declaration"])
def test_source_direction_note_withholds_only_conflicting_postings(
        client, database, make_case, monkeypatch, heading, note, withdrawal_conflicts):
    from app import import_preview_cache

    enable_trial(client, monkeypatch)
    case = make_case()
    raw, _ = original(extra_note=(heading, note))
    view = preview(client, case, raw)
    address = job_url(case, view)
    if withdrawal_conflicts:
        assert view["table_summary"]["profile"] == "funds-trace-table-v105"
        assert view["table_summary"]["accepted"] == 0
        bad_mapping = {**view["mapping"], "direction": "交易摘要"}
        assert client.post(address + "/preview", json={"mode": view["mode"], "mapping": bad_mapping}).status_code == 422
        repeat = client.post(address + "/preview", json=confirm_body(view)).raise_for_status().json()
        assert repeat["table_summary"] == view["table_summary"]
    import_preview_cache.discard(view["import_id"])
    receipt = completed(client, address, {"mode": view["mode"], "mapping": view["mapping"]})
    with database._lock:
        database._con.close()
        database._con = None
    source = f"/api/v1/cases/{case}/sources/{view['import_id']}"
    records = client.get(address + "/records").raise_for_status().json()["items"]
    assert client.get(source + "/file").content == raw
    if withdrawal_conflicts:
        assert (receipt["ok"], records[-1]["source_row"], records[-1]["flow_id"]) == (0, 4, None)
        assert view["table_summary"]["reason_counts"] == {"invalid_direction": 2, "conflicting_direction": 1}
        detail = records[-1]["details"]
        assert (records[-1]["reason_code"], records[-1]["disposition"]) == ("conflicting_direction", "unresolved")
        assert "source_ledger_evidence" not in detail and "flow_sha256" not in detail
        note_column = 6 if heading == "交易摘要" else 7
        assert f"第 2 行第 {note_column} 列" in detail["reason"] and "第 4 列" in detail["reason"]
        review = client.get(address + "/review", params={"member_path": "table.csv", "view": "all"}).raise_for_status().json()
        assert review["items"][0]["original"][note_column - 1]["value"] == note
        assert review["items"][-1]["original"][3]["value"] == "取款"
        assert client.get(f"/api/v1/cases/{case}/flows").raise_for_status().json()["total"] == 0
        assert client.delete(source + "/operation", params={"expected_batch_id": receipt["batch_id"]}).status_code == 200
        assert client.get(address + "/intake").raise_for_status().json()["stage"] == "withdrawn"
        assert client.get(source + "/file").content == raw
        return
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"
    assert view["table_summary"]["reason_counts"] == {"invalid_direction": 2, "transaction_valid": 1}
    assert receipt["ok"] == 1
    assert [(item["source_row"], item["reason_code"], bool(item["flow_id"])) for item in records] == [
        (2, "invalid_direction", False), (3, "invalid_direction", False), (4, "transaction_valid", True)]
    ledger = json.loads(records[-1]["details"]["source_ledger_evidence"])
    assert (ledger["source_direction"], ledger["direction"]) == ("取款", "out")


@pytest.mark.parametrize("extra_note", [("方向说明", ""), ("来源标识", "手续费"), ("交易摘要", "记录当日取款业务")],
                         ids=["blank-direction-note", "fee-word-in-unbound-column", "ordinary-withdrawal-summary"])
def test_blank_or_unbound_extra_cell_does_not_block_an_existing_literal_proof(client, make_case, monkeypatch, extra_note):
    enable_trial(client, monkeypatch)
    case = make_case()
    view = preview(client, case, original(extra_note=extra_note)[0])
    assert view["table_summary"]["profile"] == "funds-trace-table-v104"
    assert view["table_summary"]["accepted"] == 3
    assert completed(client, job_url(case, view), confirm_body(view))["ok"] == 3


def test_conflict_on_another_table_does_not_hold_the_first_table_or_an_unrelated_row(
        client, database, make_case, monkeypatch):
    from test_cases import exported, import_package

    enable_trial(client, monkeypatch)
    case = make_case()
    raw, _ = original()
    headings, *rows = csv.reader(io.StringIO(raw.decode()))
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows([
        headings, *rows, headings,
        ["000001009876", "", "2039-04-05 11:01:01", "存款", "19.7", "本表取款表示本方收入"],
        ["000001009876", "", "2039-04-05 11:02:02", "取款", "6.2", ""],
        ["000001009876", "", "2039-04-05 11:03:03", "入", "7.5", ""],
    ])
    view = preview(client, case, stream.getvalue().encode())
    assert view["table_summary"]["profile"] == "funds-trace-table-v105"
    address = job_url(case, view)
    assert completed(client, address, confirm_body(view))["ok"] == 2
    records = client.get(address + "/records").raise_for_status().json()["items"]
    by_row = {item["source_row"]: item for item in records}
    assert by_row[5]["reason_code"] == "repeated_header"
    assert by_row[4]["flow_id"] and by_row[8]["flow_id"]
    assert (by_row[7]["reason_code"], by_row[7]["flow_id"]) == ("conflicting_direction", None)
    assert "第 6 行第 6 列" in by_row[7]["details"]["reason"]
    for row, direction in [(4, "out"), (8, "in")]:
        assert json.loads(by_row[row]["details"]["source_ledger_evidence"])["direction"] == direction

    restored = import_package(client, exported(client, case)).raise_for_status().json()["case_id"]
    [source] = database.q_raw("select source_id,parser_version from import_sources where case_id=?", [restored])
    assert source["parser_version"] == "funds-trace-table-v105"
    [held] = database.q_raw("select disposition,reason_code from import_records where source_id=? and source_row=7",
                            [source["source_id"]])
    assert held == {"disposition": "unresolved", "reason_code": "conflicting_direction"}
    assert client.get(f"/api/v1/cases/{restored}/flows").raise_for_status().json()["total"] == 2


@pytest.mark.parametrize("with_unaffected_row", [False, True], ids=["only-held-row", "separate-withdrawal"])
def test_explicit_row_scope_keeps_its_own_original_withdrawal_without_changing_other_rows(
        client, database, make_case, monkeypatch, with_unaffected_row):
    from test_cases import exported, import_package

    enable_trial(client, monkeypatch)
    case = make_case()
    headings, *rows = csv.reader(io.StringIO(original()[0].decode()))
    held = [*rows[-1][:5], "本行取款表示本方收入"]
    verified = [*rows[-1]]
    verified[2] = "2039-04-05 10:04:04"
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows([headings, held, *([verified] if with_unaffected_row else [])])
    view = preview(client, case, stream.getvalue().encode())
    assert view["table_summary"]["profile"] == "funds-trace-table-v105"
    address = job_url(case, view)
    assert completed(client, address, confirm_body(view))["ok"] == int(with_unaffected_row)
    actual = client.get(address + "/records").raise_for_status().json()["items"]
    assert (actual[0]["reason_code"], actual[0]["source_row"], actual[0]["flow_id"]) == ("conflicting_direction", 2, None)
    if with_unaffected_row:
        assert json.loads(actual[1]["details"]["source_ledger_evidence"])["direction"] == "out"
    else:
        restored = import_package(client, exported(client, case)).raise_for_status().json()["case_id"]
        [record] = database.q_raw("select disposition,reason_code from import_records where case_id=?", [restored])
        assert record == {"disposition": "unresolved", "reason_code": "conflicting_direction"}


def test_other_financial_checks_still_leave_a_deposit_pending(client, make_case, monkeypatch):
    enable_trial(client, monkeypatch)
    case = make_case()
    view = preview(client, case, original(variant="currency_conflict")[0])
    assert view["table_summary"]["profile"] == "funds-trace-table-v104"
    assert view["table_summary"]["reason_counts"] == {"invalid_currency": 1, "transaction_valid": 2}
    address = job_url(case, view)
    assert completed(client, address, confirm_body(view))["ok"] == 2
    [pending] = client.get(address + "/records", params={"source_row": 2}).raise_for_status().json()["items"]
    assert (pending["disposition"], pending["reason_code"], pending["flow_id"]) == ("unresolved", "invalid_currency", None)


@pytest.mark.parametrize(("table", "tamper"), [
    ("import_sources", "update rows set mapping_json=cast(json_merge_patch(mapping_json, '{\"direction\":\"收付方向\"}') as varchar)"),
    ("import_records", """update rows set details_json=cast(json_merge_patch(details_json,
        json_object('source_ledger_evidence', cast(json_merge_patch(json_extract_string(details_json, '$.source_ledger_evidence'),
        '{"direction_literal":{"column_index":5}}') as varchar))) as varchar) where source_row=2"""),
    ("import_sources", "update rows set parser_version='funds-trace-table-v69'"),
], ids=["wrong_mapping", "wrong_physical_column", "legacy_profile"])
def test_deposit_direction_bundle_binds_profile_source_role_and_physical_column(
        client, database, make_case, monkeypatch, tmp_path, table, tamper):
    from test_cases import archive_of, exported, import_package, members, rewrite_parquet

    enable_trial(client, monkeypatch)
    case = make_case()
    view = preview(client, case, original()[0])
    assert completed(client, job_url(case, view), confirm_body(view))["ok"] == 3
    bundle = exported(client, case)
    unopened = import_package(client, bundle)
    assert unopened.status_code == 200, unopened.text
    restored = unopened.json()["case_id"]
    records = database.q_raw("select parser_version from import_sources where case_id=?", [restored])
    assert records == [{"parser_version": "funds-trace-table-v104"}]

    def money_facts(case_id):
        rows = client.get(f"/api/v1/cases/{case_id}/flows").raise_for_status().json()["items"]
        return sorted(tuple(row[field] for field in ("trade_time", "payer_account", "payee_account", "amount", "currency"))
                      for row in rows)

    restored_money = money_facts(restored)
    assert len(restored_money) == 3 and restored_money == money_facts(case)
    entries = members(bundle)
    rewrite_parquet(entries, table, tamper, tmp_path)
    counts = "select (select count(*) from cases) cases, (select count(*) from flows) flows, (select count(*) from import_records) records"
    before = database.q_raw(counts)
    response = import_package(client, archive_of(entries))
    assert response.status_code == 400
    assert database.q_raw(counts) == before


def test_directory_csv_does_not_select_standalone_deposit_profile(client, make_case, monkeypatch):
    from test_folder_evidence import preview as folder_preview

    enable_trial(client, monkeypatch)
    view = folder_preview(client, make_case(), [("synthetic.csv", original()[0])], scope_bank=None)
    assert view["table_summary"]["profile"].startswith("funds-trace-table-archive-")
    assert view["table_summary"]["accepted"] <= 1
